"""Post-processing, deduplication, preview building, and export hooks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from muedit.decomp.adaptive_batch import adaptive_batch_process
from muedit.decomp.algorithm import (
    DEDUP_JITTER,
    DEDUP_MAXLAG_RATIO,
    batch_process_filters,
    extend_signal,
    rem_duplicates,
)
from muedit.decomp.preview import build_preview_payload
from muedit.decomp.types import (
    DecomposeStepOutput,
    DecompositionParameters,
    LoadStepOutput,
    PostprocessStepOutput,
    PreprocessStepOutput,
)
from muedit.models import DecompositionExport, DecompositionSignalExport

logger = logging.getLogger(__name__)


def _remove_duplicates_by_grid(
    pulse_t: np.ndarray,
    distime: list[np.ndarray],
    mu_grid_index: list[int],
    ngrid: int,
    params: DecompositionParameters,
    fsamp: float,
) -> tuple[np.ndarray, list[np.ndarray], list[int], list[int]]:
    """Remove duplicate motor units within each grid and optionally across grids.

    Returns ``(pulse_t, distime, mu_grid_index, kept_global_indices)`` where
    ``kept_global_indices`` maps each surviving MU to its index in the input
    ``pulse_t`` array, so callers can subset per-window metadata (e.g. SIL
    scores) to match the deduplicated output.
    """
    if len(distime) == 0:
        return np.array([]), [], [], []

    filtered_pulses = []
    filtered_distime: list[np.ndarray] = []
    filtered_grid_index: list[int] = []
    global_indices: list[int] = []
    logger.info("Removing duplicates...")

    for g_idx in range(ngrid):
        mu_indices = [idx for idx, g in enumerate(mu_grid_index) if g == g_idx]
        if not mu_indices:
            continue
        pulses_subset = pulse_t[mu_indices, :]
        dist_subset = [distime[idx] for idx in mu_indices]
        pulses_subset, dist_subset, kept_local = rem_duplicates(
            pulses_subset,
            dist_subset,
            dist_subset,
            round(fsamp / DEDUP_MAXLAG_RATIO),
            DEDUP_JITTER,
            params.duplicatesthresh,
            fsamp,
        )
        if pulses_subset.size == 0:
            continue
        filtered_pulses.append(pulses_subset)
        filtered_distime.extend(dist_subset)
        filtered_grid_index.extend([g_idx] * pulses_subset.shape[0])
        global_indices.extend(mu_indices[l] for l in kept_local)

    if params.duplicatesbgrids and filtered_pulses:
        combined_pulses = np.vstack(filtered_pulses)
        combined_distime = filtered_distime
        combined_pulses, combined_distime, kept_idx = rem_duplicates(
            combined_pulses,
            combined_distime,
            combined_distime,
            round(fsamp / DEDUP_MAXLAG_RATIO),
            DEDUP_JITTER,
            params.duplicatesthresh,
            fsamp,
        )
        kept_global = [global_indices[i] for i in kept_idx]
        return (
            combined_pulses,
            combined_distime,
            [filtered_grid_index[i] for i in kept_idx],
            kept_global,
        )

    pulse_t_out = np.vstack(filtered_pulses) if filtered_pulses else np.array([])
    return pulse_t_out, filtered_distime, filtered_grid_index, global_indices


def _save_npz_with_app_schema(
    out_path: str | Path,
    pulse_trains: np.ndarray,
    distimes: list[np.ndarray] | list[list[int]],
    fsamp: float,
    grid_names: list[str],
    mu_grid_index: list[int],
    muscles: list[str],
    parameters: dict[str, Any],
    total_samples: int,
    extras: dict[str, Any] | None = None,
) -> None:
    """Save NPZ using the same core key schema as web-app edit saves."""
    distime_arrays = [np.asarray(d, dtype=int) for d in distimes]
    distime_obj = np.empty(len(distime_arrays), dtype=object)
    for i, d in enumerate(distime_arrays):
        distime_obj[i] = d
    payload: dict[str, Any] = {
        "pulse_trains": pulse_trains,
        "discharge_times": distime_obj,
        "fsamp": fsamp,
        "grid_names": np.array(grid_names, dtype=object),
        "mu_grid_index": np.array(mu_grid_index, dtype=int),
        "muscle_names": np.array(muscles, dtype=object),
        "muscle": np.array(muscles, dtype=object),
        "parameters": np.array([parameters], dtype=object),
        "total_samples": total_samples,
    }
    if extras:
        payload.update(extras)
    np.savez_compressed(out_path, **payload)


def postprocess_step(
    prep: PreprocessStepOutput,
    decomposed: DecomposeStepOutput,
    params: DecompositionParameters,
    progress_cb: Callable[[str, dict[str, Any]], None] | None,
) -> PostprocessStepOutput:
    """Batch filters and remove duplicates."""
    logger.info("Batch processing...")
    if progress_cb:
        progress_cb("progress", {"message": "Batch processing filters", "pct": 92})

    nwindows = len(prep.roi_list)
    adaptive_losses: dict[int, Any] = {}
    if params.use_adaptive:
        grid_data: dict[int, np.ndarray] = {}
        ch_idx_g = 0
        for i in range(prep.ngrid):
            mask = np.array(prep.discard_channels[i]).astype(int)
            n_ch_g = mask.size
            keep_idx = np.where(mask == 0)[0]
            raw = prep.data[ch_idx_g + keep_idx, :]
            grid_data[i] = raw
            ch_idx_g += n_ch_g

        pulse_t, distime, adaptive_losses = adaptive_batch_process(
            decomposed.mu_filters,
            decomposed.w_sig,
            decomposed.win_data,
            decomposed.whiten_mat,
            grid_data,
            decomposed.coordinates_plateau,
            prep.data.shape[1],
            prep.fsamp,
            nwindows,
            win_means_by_window=decomposed.win_means,
            batch_ms=params.adapt_batch_ms,
            adapt_wh=params.adapt_wh,
            adapt_sv=params.adapt_sv,
            adapt_sd=params.adapt_sd,
            wh_learning_rate=params.adapt_wh_learning_rate,
            sv_learning_rate=params.adapt_sv_learning_rate,
            cov_alpha=params.adapt_cov_alpha,
            spike_prev_weight=params.adapt_spike_prev_weight,
        )
    else:
        full_extended_by_window: dict[int, np.ndarray] | None = None
        if params.full_trace:
            full_extended_by_window = {}
            ch_idx_g = 0
            grid_full_ext: dict[int, np.ndarray] = {}
            for i in range(prep.ngrid):
                mask = np.array(prep.discard_channels[i]).astype(int)
                n_ch_g = mask.size
                keep_idx = np.where(mask == 0)[0]
                grid_raw = prep.data[ch_idx_g + keep_idx, :]
                ex_factor = int(round(params.nbextchan / max(1, grid_raw.shape[0])))
                # Raw (non-demeaned) extended signal, shared across the grid's
                # windows. The per-window DC baseline is removed as an additive
                # correction inside batch_process_filters via win_means.
                grid_full_ext[i] = extend_signal(grid_raw, ex_factor)
                ch_idx_g += n_ch_g
            for nwin in decomposed.mu_filters:
                grid_idx = nwin // max(1, nwindows)
                full_extended_by_window[nwin] = grid_full_ext[grid_idx]
            logger.info("Applying MU filters over the full trace (dewhitened).")

        pulse_t, distime = batch_process_filters(
            decomposed.mu_filters,
            decomposed.w_sig,
            decomposed.coordinates_plateau,
            prep.data.shape[1],
            prep.fsamp,
            whiten_mat_by_window=decomposed.whiten_mat if full_extended_by_window else None,
            full_extended_by_window=full_extended_by_window,
            win_means_by_window=decomposed.win_means if full_extended_by_window else None,
        )

    pulse_t, distime, mu_grid_index, kept_global = _remove_duplicates_by_grid(
        pulse_t,
        distime,
        decomposed.mu_grid_index,
        prep.ngrid,
        params,
        prep.fsamp,
    )

    # Subset SIL scores to match the deduplicated MUs.  Each global MU index
    # maps to a (window, local_index) pair via the sorted mu_filters order that
    # batch_process_filters / adaptive_batch_process used to build pulse_t.
    mu_window_map: list[tuple[int, int]] = []
    for nwin in sorted(decomposed.mu_filters.keys()):
        n_good = decomposed.mu_filters[nwin].shape[1]
        for j in range(n_good):
            mu_window_map.append((nwin, j))

    sil_by_window: dict[int, list[float]] = {}
    for g_idx in kept_global:
        if g_idx < len(mu_window_map):
            win, local = mu_window_map[g_idx]
            old_sil = decomposed.sil_by_window.get(win, [])
            if local < len(old_sil):
                sil_by_window.setdefault(win, []).append(old_sil[local])

    if progress_cb:
        progress_cb("progress", {"message": "Finalizing output", "pct": 97})

    return PostprocessStepOutput(
        pulse_t=pulse_t,
        distime=distime,
        mu_grid_index=mu_grid_index,
        sil_by_window=sil_by_window,
        adaptive_losses=adaptive_losses,
    )


def export_step(
    loaded: LoadStepOutput,
    prep: PreprocessStepOutput,
    post: PostprocessStepOutput,
    params: DecompositionParameters,
    include_full_preview: bool,
    save_npz: bool,
    save_emg_data: bool,
    progress_cb: Callable[[str, dict[str, Any]], None] | None,
) -> tuple[dict[str, Any], str]:
    """Build export payloads and optionally persist the default NPZ artifact."""
    bids_entity_label = prep.loader_meta.get("bids_entity_label")
    bids_emg_path = prep.loader_meta.get("bids_emg_path")
    if bids_entity_label and bids_emg_path:
        _emg_parts = list(Path(bids_emg_path).resolve().parts)
        _sub_idx = next((i for i, p in enumerate(_emg_parts) if p.lower().startswith("sub-")), -1)
        if _sub_idx > 0:
            _bids_rt = Path(*_emg_parts[:_sub_idx])
            _subj = _emg_parts[_sub_idx][4:]
            _sess = None
            for _tok in str(bids_entity_label).split("_"):
                if _tok.startswith("ses-") and len(_tok) > 4:
                    _sess = _tok[4:]
                    break
            decomp_dir = _bids_rt / "derivatives" / "muedit" / f"sub-{_subj}"
            if _sess:
                decomp_dir = decomp_dir / f"ses-{_sess}"
            decomp_dir = decomp_dir / "decomp"
        else:
            decomp_dir = Path(bids_emg_path).parent.parent / "decomp"
        decomp_dir.mkdir(parents=True, exist_ok=True)
        save_path = str(decomp_dir / f"{bids_entity_label}_decomp.npz")
    else:
        save_path = str(
            Path(loaded.full_path).with_name(Path(loaded.full_path).stem + "_decomp.npz")
        )

    preview = build_preview_payload(
        signal=prep.signal,
        data=prep.data,
        fsamp=prep.fsamp,
        pulse_t=post.pulse_t,
        distime=post.distime,
        grid_names=prep.grid_names,
        roi_list=prep.roi_list,
        discard_channels=prep.discard_channels,
        coordinates=prep.coordinates,
        mu_grid_index=post.mu_grid_index,
        loader_meta=prep.loader_meta,
        muscles=prep.muscles,
        include_full_preview=include_full_preview,
    )
    export_payload = DecompositionExport(
        signal=DecompositionSignalExport(
            data=prep.data,
            fsamp=prep.fsamp,
            pulse_t=post.pulse_t,
            discharge_times=post.distime,
        ),
        parameters=asdict(params),
        grid_names=prep.grid_names,
        sil=post.sil_by_window,
        discard_channels=prep.discard_channels,
        coordinates=prep.coordinates,
        mu_grid_index=post.mu_grid_index,
        preview=preview,
    )
    result = export_payload.to_dict()
    result["adaptive_losses"] = post.adaptive_losses

    if save_npz:
        extras: dict[str, Any] = {
            "adaptive_losses": np.array([post.adaptive_losses], dtype=object),
        }
        if save_emg_data:
            extras["emg_data"] = prep.data
            extras["discard_channels"] = np.array(prep.discard_channels, dtype=object)
            extras["coordinates"] = np.array(prep.coordinates, dtype=object)
        _save_npz_with_app_schema(
            save_path,
            pulse_trains=post.pulse_t,
            distimes=post.distime,
            fsamp=prep.fsamp,
            grid_names=prep.grid_names,
            mu_grid_index=post.mu_grid_index,
            muscles=prep.muscles,
            parameters=asdict(params),
            total_samples=prep.data.shape[1],
            extras=extras,
        )
        logger.info("Saved to %s", save_path)

    if progress_cb:
        progress_cb(
            "done",
            {
                "message": "Decomposition complete",
                "pct": 100,
                "summary": {
                    "fsamp": prep.fsamp,
                    "grid_names": prep.grid_names,
                    "mu_count": len(post.distime),
                    "parameters": asdict(params),
                },
                "preview": result["preview"],
            },
        )

    return result, save_path
