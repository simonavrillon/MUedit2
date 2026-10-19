"""Post-processing, deduplication, preview building, and export hooks."""

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
    window_trim,
)
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
from muedit.signal.decomp_primitives import extend_signal, extension_factor
from muedit.signal.filters import demean
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


def _reconstruct_window_signal(
    prep: PreprocessStepOutput,
    params: DecompositionParameters,
    win_global: int,
    whiten_mat: FloatArray,
) -> FloatArray:
    """Recompute the whitened window ``w_sig`` from ``prep.data`` + ``whiten_mat``."""
    nwindows = len(prep.roi_list)
    grid_idx = win_global // max(1, nwindows)

    ch_offset = 0
    for g in range(grid_idx):
        ch_offset += int(np.array(prep.discard_channels[g]).astype(int).size)

    mask = np.array(prep.discard_channels[grid_idx]).astype(int)
    n_ch_grid = mask.size
    keep_idx = np.where(mask == 0)[0]

    start = prep.coordinates_plateau[win_global * 2]
    end = prep.coordinates_plateau[win_global * 2 + 1]
    grid_block = prep.data[ch_offset : ch_offset + n_ch_grid, start:end]
    win_data_arr = grid_block[keep_idx, :]

    ex_factor = extension_factor(params.nbextchan, win_data_arr.shape[0])
    trim = window_trim(win_data_arr.shape[1], prep.fsamp, params.edges_sec, ex_factor)

    e_sig = extend_signal(demean(win_data_arr), ex_factor, dtype=params.work_dtype)
    if trim:
        e_sig = e_sig[:, trim:-trim]
    return whiten_inplace(e_sig, whiten_mat)


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

    nwindows = len(prep.roi_list)

    def get_w_sig(nwin: int) -> FloatArray:
        return _reconstruct_window_signal(prep, params, nwin, decomposed.whiten_mat[nwin])

    # Kept channels of each grid, read batch by batch by the streamed passes.
    grid_data: dict[int, RowSelection] = {}
    ch_idx_g = 0
    for i in range(prep.ngrid):
        mask = np.array(prep.discard_channels[i]).astype(int)
        grid_data[i] = RowSelection(prep.data, ch_idx_g + np.where(mask == 0)[0])
        ch_idx_g += mask.size

    if params.use_adaptive:
        pulse_trains, spike_times, _ = adaptive_batch_process(
            decomposed.mu_filters,
            decomposed.whiten_mat,
            grid_data,
            decomposed.coordinates_plateau,
            prep.data.shape[1],
            prep.fsamp,
            nwindows,
            decomposed.win_means,
            batch_ms=params.adapt_batch_ms,
            adapt_wh=params.adapt_wh,
            adapt_sv=params.adapt_sv,
            adapt_sd=params.adapt_sd,
            wh_learning_rate=params.adapt_wh_learning_rate,
            sv_learning_rate=params.adapt_sv_learning_rate,
            cov_alpha=params.adapt_cov_alpha,
            spike_prev_weight=params.adapt_spike_prev_weight,
            artifact_mask=prep.artifact_mask,
            store=store,
            work_dtype=params.work_dtype,
        )
    else:
        if params.full_trace:
            logger.info("Applying MU filters over the full trace (dewhitened).")
        pulse_trains, spike_times = batch_process_filters(
            decomposed.mu_filters,
            get_w_sig,
            decomposed.coordinates_plateau,
            prep.data.shape[1],
            prep.fsamp,
            whiten_mat_by_window=decomposed.whiten_mat if params.full_trace else None,
            grid_data=grid_data,
            window_to_grid={nwin: nwin // max(1, nwindows) for nwin in decomposed.mu_filters},
            win_means_by_window=decomposed.win_means if params.full_trace else None,
            artifact_mask=prep.artifact_mask,
            store=store,
            work_dtype=params.work_dtype,
        )

    pulse_trains_all = pulse_trains
    pulse_trains, spike_times, mu_grid_index, kept_global = remove_duplicates_by_grid(
        pulse_trains_all,
        spike_times,
        decomposed.mu_grid_index,
        prep.ngrid,
        params,
        prep.fsamp,
        store=store,
    )
    if store is not None:
        store.discard(pulse_trains_all)
    del pulse_trains_all

    mu_window_map: list[tuple[int, int]] = [
        (nwin, j)
        for nwin in sorted(decomposed.mu_filters.keys())
        for j in range(decomposed.mu_filters[nwin].shape[1])
    ]

    sil_flat: list[float] = []
    sil_by_window: dict[int, list[float]] = {}
    for g_idx in kept_global:
        if g_idx < len(mu_window_map):
            win, local = mu_window_map[g_idx]
            old_sil = decomposed.sil_by_window.get(win, [])
            if local < len(old_sil):
                sil_flat.append(old_sil[local])
                sil_by_window.setdefault(win, []).append(old_sil[local])

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
    raw_emg: FloatArray | None,
    progress_cb: Callable[[str, dict[str, Any]], None] | None,
) -> DecompositionResult:
    """Build the run's result and preview, and optionally save the ``.npz``, with ``raw_emg``."""
    if progress_cb:
        progress_cb("progress", {"message": "Writing output", "phase": "export"})
    preview = build_preview_payload(
        data=prep.data,
        fsamp=prep.fsamp,
        pulse_trains=post.pulse_trains,
        spike_times=post.spike_times,
        grid_names=prep.grid_names,
        roi_list=prep.roi_list,
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
        with_emg = raw_emg is not None
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
            rois=prep.roi_list,
            artifact_mask=prep.artifact_mask,
            emg_data=raw_emg,
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
        rois=prep.roi_list,
    )
