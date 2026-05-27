"""Post-processing, deduplication, preview building, and export hooks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from muedit.decomp.adaptive_batch import adaptive_batch_process
from muedit.decomp.algorithm import batch_process_filters, rem_duplicates
from muedit.decomp.preview import build_preview_payload
from muedit.decomp.types import (
    DecomposeStepOutput,
    DecompositionParameters,
    LoadStepOutput,
    PostprocessStepOutput,
    PreprocessStepOutput,
)
from muedit.io.bids import build_entities
from muedit.models import DecompositionExport, DecompositionSignalExport

logger = logging.getLogger(__name__)


def _remove_duplicates_by_grid(
    pulse_t: np.ndarray,
    distime: list[np.ndarray],
    mu_grid_index: list[int],
    ngrid: int,
    params: DecompositionParameters,
    fsamp: float,
) -> tuple[np.ndarray, list[np.ndarray], list[int]]:
    """Remove duplicate motor units within each grid and optionally across grids.

    Returns the filtered (pulse_t, distime, mu_grid_index) after deduplication.
    """
    if len(distime) == 0:
        return np.array([]), [], []

    filtered_pulses = []
    filtered_distime: list[np.ndarray] = []
    filtered_grid_index: list[int] = []
    logger.info("Removing duplicates...")

    for g_idx in range(ngrid):
        mu_indices = [idx for idx, g in enumerate(mu_grid_index) if g == g_idx]
        if not mu_indices:
            continue
        pulses_subset = pulse_t[mu_indices, :]
        dist_subset = [distime[idx] for idx in mu_indices]
        pulses_subset, dist_subset, _ = rem_duplicates(
            pulses_subset,
            dist_subset,
            dist_subset,
            round(fsamp / 40),
            0.00025,
            params.duplicatesthresh,
            fsamp,
        )
        if pulses_subset.size == 0:
            continue
        filtered_pulses.append(pulses_subset)
        filtered_distime.extend(dist_subset)
        filtered_grid_index.extend([g_idx] * pulses_subset.shape[0])

    if params.duplicatesbgrids and filtered_pulses:
        combined_pulses = np.vstack(filtered_pulses)
        combined_distime = filtered_distime
        combined_pulses, combined_distime, kept_idx = rem_duplicates(
            combined_pulses,
            combined_distime,
            combined_distime,
            round(fsamp / 40),
            0.00025,
            params.duplicatesthresh,
            fsamp,
        )
        return combined_pulses, combined_distime, [filtered_grid_index[i] for i in kept_idx]

    pulse_t_out = np.vstack(filtered_pulses) if filtered_pulses else np.array([])
    return pulse_t_out, filtered_distime, filtered_grid_index


def _save_npz_with_app_schema(
    out_path: str | Path,
    pulse_trains: np.ndarray,
    distimes: list[np.ndarray],
    fsamp: float,
    grid_names: list[str],
    mu_grid_index: list[int],
    muscles: list[str],
    parameters: dict[str, Any],
    total_samples: int,
    extras: dict[str, Any] | None = None,
) -> None:
    """Save NPZ using the same core key schema as web-app edit saves."""
    payload: dict[str, Any] = {
        "pulse_trains": pulse_trains,
        "discharge_times": np.array(distimes, dtype=object),
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
    bids_root: str | None,
    bids_entities: dict[str, Any] | None,
    progress_cb: Callable[[str, dict[str, Any]], None] | None,
) -> PostprocessStepOutput:
    """Batch filters, remove duplicates, and optionally save BIDS decomposition output."""
    logger.info("Batch processing...")
    if progress_cb:
        progress_cb("progress", {"message": "Batch processing filters", "pct": 92})

    nwindows = len(prep.roi_list)
    adaptive_losses: dict[str, Any] = {}
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
            batch_ms=params.adapt_batch_ms,
            adapt_wh=params.adapt_wh,
            adapt_sv=params.adapt_sv,
        )
    else:
        pulse_t, distime = batch_process_filters(
            decomposed.mu_filters,
            decomposed.w_sig,
            decomposed.coordinates_plateau,
            prep.data.shape[1],
            prep.fsamp,
            nwindows,
        )

    pulse_t, distime, mu_grid_index = _remove_duplicates_by_grid(
        pulse_t,
        distime,
        decomposed.mu_grid_index,
        prep.ngrid,
        params,
        prep.fsamp,
    )

    if bids_root and pulse_t.size > 0:
        subj = (bids_entities or {}).get("subject", "01")
        sess = (bids_entities or {}).get("session")
        entity_label = build_entities(
            subject=subj,
            task=(bids_entities or {}).get("task", "task"),
            run=(bids_entities or {}).get("run"),
            session=sess,
            acquisition=(bids_entities or {}).get("acquisition"),
            recording=(bids_entities or {}).get("recording"),
        )
        base_dir = Path(bids_root) / f"sub-{subj}"
        if sess:
            base_dir = base_dir / f"ses-{sess}"
        decomp_dir = base_dir / "decomp"
        decomp_dir.mkdir(parents=True, exist_ok=True)
        out_path = decomp_dir / f"{entity_label}_decomp.npz"
        _save_npz_with_app_schema(
            out_path,
            pulse_trains=pulse_t,
            distimes=distime,
            fsamp=prep.fsamp,
            grid_names=prep.grid_names,
            mu_grid_index=mu_grid_index,
            muscles=prep.muscles,
            parameters=asdict(params),
            total_samples=prep.data.shape[1],
            extras={"adaptive_losses": np.array([adaptive_losses], dtype=object)},
        )
        logger.info("Saved combined decomposition to %s", out_path)

    if progress_cb:
        progress_cb("progress", {"message": "Finalizing output", "pct": 97})

    return PostprocessStepOutput(
        pulse_t=pulse_t,
        distime=distime,
        mu_grid_index=mu_grid_index,
        sil_by_window=decomposed.sil_by_window,
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
