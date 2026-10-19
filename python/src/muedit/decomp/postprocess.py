"""Post-processing, deduplication, and export of a decomposition run."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import numpy as np

from muedit.decomp.adaptive_batch import adaptive_batch_process
from muedit.decomp.algorithm import (
    DEDUP_JITTER,
    DEDUP_MAXLAG_RATIO,
    batch_process_filters,
    unique_mu_indices,
    whiten_inplace,
)
from muedit.decomp.core import extended_window
from muedit.decomp.decomposition_file import save_decomposition_npz
from muedit.decomp.preview import build_preview_payload
from muedit.decomp.types import (
    DecomposeStepOutput,
    DecompositionParameters,
    DecompositionResult,
    LoadStepOutput,
    PostprocessStepOutput,
    PreprocessStepOutput,
)
from muedit.io.bids import run_decomp_path, write_derivative_description
from muedit.models import FloatArray, IntArray
from muedit.signal.grid import kept_rows
from muedit.signal.streaming import RowSelection
from muedit.storage import ArrayStore

logger = logging.getLogger(__name__)


def dedup_survivors(
    spike_times: list[IntArray],
    mu_grid_index: list[int],
    ngrid: int,
    params: DecompositionParameters,
    fsamp: float,
    total_samples: int,
) -> tuple[list[int], list[int]]:
    """Indices of the MUs kept by duplicate removal within and across grids, ascending, and their grids."""
    outside = [g for g in mu_grid_index if not 0 <= g < ngrid]
    if outside:
        # A grid no loop below visits would drop its MUs without a trace.
        raise ValueError(f"mu_grid_index {outside[0]} is outside the {ngrid} grid(s)")
    maxlag = round(fsamp / DEDUP_MAXLAG_RATIO)
    kept: list[int] = []
    kept_grids: list[int] = []

    for g_idx in range(ngrid):
        mu_indices = [idx for idx, g in enumerate(mu_grid_index) if g == g_idx]
        if not mu_indices:
            continue
        dist_subset = [spike_times[idx] for idx in mu_indices]
        kept_local = unique_mu_indices(
            dist_subset,
            maxlag,
            DEDUP_JITTER,
            params.duplicatesthresh,
            fsamp,
            total_samples,
        )
        kept.extend(mu_indices[l] for l in kept_local)
        kept_grids.extend([g_idx] * len(kept_local))

    if params.duplicatesbgrids and kept:
        dist_kept = [spike_times[idx] for idx in kept]
        kept_idx = unique_mu_indices(
            dist_kept,
            maxlag,
            DEDUP_JITTER,
            params.duplicatesthresh,
            fsamp,
            total_samples,
        )
        kept, kept_grids = [kept[i] for i in kept_idx], [kept_grids[i] for i in kept_idx]

    order = sorted(range(len(kept)), key=kept.__getitem__)
    return [kept[i] for i in order], [kept_grids[i] for i in order]


def _kept_mu_rows(
    pulse_trains: FloatArray, kept: list[int], store: ArrayStore | None
) -> FloatArray:
    """``pulse_trains[kept]``, copied row by row into ``store`` when one is given."""
    if store is None:
        return pulse_trains[kept]
    out = store.allocate("pulse_trains", (len(kept), pulse_trains.shape[1]), pulse_trains.dtype)
    for i, row in enumerate(kept):
        out[i] = pulse_trains[row]
    return store.seal(out)


def remove_duplicates_by_grid(
    pulse_trains: FloatArray,
    spike_times: list[IntArray],
    mu_grid_index: list[int],
    ngrid: int,
    params: DecompositionParameters,
    fsamp: float,
    store: ArrayStore | None = None,
) -> tuple[FloatArray, list[IntArray], list[int], list[int]]:
    """Remove duplicate motor units within each grid and optionally across grids.

    The kept pulse trains are indexed once, into ``store`` when one is given.
    """
    if len(spike_times) == 0:
        return np.array([]), [], [], []

    logger.info("Removing duplicates...")
    kept, kept_grids = dedup_survivors(
        spike_times, mu_grid_index, ngrid, params, fsamp, pulse_trains.shape[1]
    )
    pulse_trains_out = _kept_mu_rows(pulse_trains, kept, store) if kept else np.array([])
    return pulse_trains_out, [spike_times[idx] for idx in kept], kept_grids, kept


def postprocess_step(
    prep: PreprocessStepOutput,
    decomposed: DecomposeStepOutput,
    params: DecompositionParameters,
    progress_cb: Callable[[str, dict[str, Any]], None] | None,
    store: ArrayStore | None = None,
) -> PostprocessStepOutput:
    """Batch filters and remove duplicates.

    With ``store``, the pulse trains of every unit and then of the kept ones are
    written there instead of the heap.
    """
    logger.info("Batch processing...")
    if progress_cb:
        progress_cb("progress", {"message": "Batch processing filters", "phase": "postprocess"})

    nwindows = len(prep.rois)

    def get_w_sig(win_global: int) -> FloatArray:
        e_sig = extended_window(prep, params, win_global)[0]
        return whiten_inplace(e_sig, decomposed.whiten_mat_by_window[win_global])

    # Kept channels of each grid, read batch by batch by the streamed passes.
    grid_data = {
        i: RowSelection(prep.data, kept) for i, kept in enumerate(kept_rows(prep.discard_channels))
    }

    if params.use_adaptive:
        pulse_trains, spike_times, _ = adaptive_batch_process(
            decomposed.mu_filters_by_window,
            decomposed.whiten_mat_by_window,
            grid_data,
            decomposed.coordinates_plateau,
            prep.data.shape[1],
            prep.fsamp,
            nwindows,
            decomposed.win_mean_by_window,
            params,
            artifact_mask=prep.artifact_mask,
            store=store,
        )
    else:
        if params.full_trace:
            logger.info("Applying MU filters over the full trace (dewhitened).")
        pulse_trains, spike_times = batch_process_filters(
            decomposed.mu_filters_by_window,
            get_w_sig,
            decomposed.coordinates_plateau,
            prep.data.shape[1],
            prep.fsamp,
            grid_data,
            {win_global: win_global // nwindows for win_global in decomposed.mu_filters_by_window},
            whiten_mat_by_window=decomposed.whiten_mat_by_window if params.full_trace else None,
            win_mean_by_window=decomposed.win_mean_by_window if params.full_trace else None,
            artifact_mask=prep.artifact_mask,
            store=store,
            work_dtype=params.work_dtype,
        )

    pulse_trains_all = pulse_trains
    pulse_trains, spike_times, mu_grid_index, kept_global = remove_duplicates_by_grid(
        pulse_trains_all,
        spike_times,
        decomposed.mu_grid_index,
        len(prep.grid_names),
        params,
        prep.fsamp,
        store=store,
    )
    if store is not None:
        store.discard(pulse_trains_all)
    del pulse_trains_all

    # One (window, SIL) per pulse-train row, in the row order of the batch passes.
    sil_rows = [
        (win_global, sil)
        for win_global in sorted(decomposed.mu_filters_by_window)
        for sil in decomposed.sil_by_window[win_global]
    ]
    sil_flat: list[float] = []
    sil_by_window: dict[int, list[float]] = {}
    for mu_idx in kept_global:
        win_global, sil = sil_rows[mu_idx]
        sil_flat.append(sil)
        sil_by_window.setdefault(win_global, []).append(sil)

    if progress_cb:
        progress_cb("progress", {"message": "Finalizing output", "phase": "postprocess"})

    return PostprocessStepOutput(
        pulse_trains=pulse_trains,
        spike_times=spike_times,
        mu_grid_index=mu_grid_index,
        sil_by_window=sil_by_window,
        sil=sil_flat,
    )


def export_step(
    loaded: LoadStepOutput,
    prep: PreprocessStepOutput,
    post: PostprocessStepOutput,
    params: DecompositionParameters,
    include_full_preview: bool,
    save_npz: bool,
    emg_data: FloatArray | None,
    progress_cb: Callable[[str, dict[str, Any]], None] | None,
) -> DecompositionResult:
    """Build the run's result and preview, and optionally save the ``.npz``, with ``emg_data``."""
    if progress_cb:
        progress_cb("progress", {"message": "Writing output", "phase": "export"})
    preview = build_preview_payload(
        data=prep.data,
        fsamp=prep.fsamp,
        pulse_trains=post.pulse_trains,
        spike_times=post.spike_times,
        grid_names=prep.grid_names,
        rois=prep.rois,
        discard_channels=prep.discard_channels,
        coordinates=prep.coordinates,
        mu_grid_index=post.mu_grid_index,
        loader_meta=prep.loader_meta,
        muscle_names=prep.muscle_names,
        include_full_preview=include_full_preview,
    )
    save_path = None
    if save_npz:
        path, in_dataset = run_decomp_path(loaded.full_path, prep.loader_meta)
        path.parent.mkdir(parents=True, exist_ok=True)
        save_path = str(path)
        with_emg = emg_data is not None
        save_decomposition_npz(
            save_path,
            pulse_trains=post.pulse_trains,
            spike_times=post.spike_times,
            fsamp=prep.fsamp,
            grid_names=prep.grid_names,
            mu_grid_index=post.mu_grid_index,
            muscle_names=prep.muscle_names,
            parameters=asdict(params),
            total_samples=prep.data.shape[1],
            sil=post.sil,
            sil_by_window=post.sil_by_window,
            rois=prep.rois,
            artifact_mask=prep.artifact_mask,
            emg_data=emg_data,
            discard_channels=prep.discard_channels if with_emg else None,
            coordinates=prep.coordinates if with_emg else None,
            loader_meta=prep.loader_meta if with_emg else None,
            target_segments=prep.target_segments,
        )
        if in_dataset:
            # <root>/derivatives/muedit/sub-…/[ses-…/]decomp/<file>
            deriv_root = next(p for p in path.parents if p.parent.name == "derivatives")
            write_derivative_description(deriv_root)
        logger.info("Saved to %s", save_path)

    return DecompositionResult(
        fsamp=prep.fsamp,
        grid_names=prep.grid_names,
        pulse_trains=post.pulse_trains,
        spike_times=post.spike_times,
        mu_grid_index=post.mu_grid_index,
        sil=post.sil,
        discard_channels=prep.discard_channels,
        parameters=asdict(params),
        preview=preview,
        save_path=save_path,
        target_segments=prep.target_segments,
        rois=prep.rois,
    )
