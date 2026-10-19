"""Signal loading/preprocessing and ROI preparation for decomposition."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np

from muedit.decomp.types import DecompositionParameters, LoadStepOutput, PreprocessStepOutput
from muedit.errors import MueditError
from muedit.io.bids import (
    bids_emg_fields,
    export_bids_emg,
    export_bids_events,
    form_entities,
    form_over_loader_meta,
    resolve_bids_emg_path,
    write_participant,
)
from muedit.io.factory import load_signal
from muedit.models import BoolArray, FloatArray, IntArray, SignalImport
from muedit.signal.filters import FILTER_BLOCK_ROWS, bandpass_inplace, notch_inplace
from muedit.signal.grid import format_hdemg_signal, grid_rows
from muedit.signal.qc_pipeline import run_auto_qc
from muedit.signal.segmentation import SegmentationConfig, segment_target
from muedit.storage import ArrayStore, RamStore

logger = logging.getLogger(__name__)


def select_roi_interactively(data: FloatArray, fsamp: float) -> tuple[int, int]:
    """Display a plot and let the user click ROI start/end times."""
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("--manual-roi needs matplotlib: install muedit[plot]") from exc

    logger.info("Select the start and end of the analysis window on the plot.")

    ds_factor = 10 if data.shape[1] <= 1_000_000 else 100
    time = np.arange(0, data.shape[1], ds_factor) / fsamp
    signal_vis = np.mean(np.abs(data[:, ::ds_factor]), axis=0)

    # Match the webapp visual language for CLI/manual ROI selection.
    bg = "#1f1f1f"
    panel = "#2a2a2a"
    text = "#ffffff"
    muted = "#b7b7b7"
    accent = "#ffd43b"
    border = "#3a3a3a"

    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(panel)
    ax.plot(time, signal_vis, color=accent, linewidth=1.2)
    ax.set_title("Click START and END points of the ROI", color=text, pad=10)
    ax.set_xlabel("Time (s)", color=text)
    ax.set_ylabel("Mean Absolute Amplitude", color=text)
    ax.grid(True, color=border, alpha=0.7, linewidth=0.8)
    ax.tick_params(colors=muted)
    for spine in ax.spines.values():
        spine.set_color(border)

    fig.tight_layout()

    pts = fig.ginput(2, timeout=0)
    plt.close()

    if len(pts) < 2:
        logger.warning("Selection cancelled or incomplete. Using full signal.")
        return 0, data.shape[1]

    t_start = min(pts[0][0], pts[1][0])
    t_end = max(pts[0][0], pts[1][0])

    idx_start = int(t_start * fsamp)
    idx_end = int(t_end * fsamp)
    idx_start = max(0, idx_start)
    idx_end = min(data.shape[1], idx_end)

    logger.info(
        "Selected ROI: %.2fs to %.2fs (%d to %d)",
        t_start,
        t_end,
        idx_start,
        idx_end,
    )
    return idx_start, idx_end


def _resolve_rois(
    data: FloatArray,
    fsamp: float,
    duration: float | None,
    manual_roi: bool,
    roi: tuple[int, int] | None,
    rois: list[tuple[int, int]] | None,
    nwindows: int = 1,
) -> list[tuple[int, int]]:
    """Resolve the analysis ROI from the various supported input methods."""
    total_len = data.shape[1]
    if total_len == 0:
        raise MueditError("Input signal contains zero samples; cannot define ROI.")
    explicit_rois = bool(rois)
    n_rois = len(rois) if rois is not None else 0
    if explicit_rois and nwindows > 1 and n_rois != nwindows:
        logger.warning(
            "nwindows=%d ignored because %d explicit ROI(s) were provided; the "
            "ROI(s) are used as-is (one window each).",
            nwindows,
            n_rois,
        )
    requested = rois or ([roi] if roi else [])
    if requested:
        resolved = []
        for r in requested:
            s = max(0, min(int(r[0]), total_len - 1))
            e = max(s + 1, min(int(r[1]), total_len))
            resolved.append((s, e))
    elif manual_roi:
        s, e = select_roi_interactively(data, fsamp)
        resolved = [(s, e)]
    elif duration is not None:
        total_samples = max(1, min(total_len, int(duration * fsamp)))
        resolved = [(0, total_samples)]
    else:
        resolved = [(0, total_len)]

    if nwindows > 1 and not explicit_rois and len(resolved) == 1:
        s, e = resolved[0]
        span = e - s
        if span >= nwindows:
            step = span // nwindows
            resolved = [(s + i * step, s + (i + 1) * step) for i in range(nwindows)]
            # Extend the last sub-window to the original end so no samples are lost.
            resolved[-1] = (resolved[-1][0], e)
        else:
            logger.warning(
                "nwindows=%d requested but the ROI span (%d samples) is smaller "
                "than nwindows; using a single window.",
                nwindows,
                span,
            )
    return resolved


def _build_coordinates_plateau(ngrid: int, rois: list[tuple[int, int]]) -> list[int]:
    """Flatten the ROI bounds of every grid into ``[start, end, start, end, ...]``."""
    coordinates_plateau: list[int] = []
    for _ in range(ngrid):
        for s, e in rois:
            coordinates_plateau.extend([s, e])
    return coordinates_plateau


def _filter_grids(
    data: FloatArray, fsamp: float, discard_channels: list[IntArray], emg_types: list[int]
) -> None:
    """Notch- then bandpass-filter each grid's channels in place, for the grid's EMG type."""
    for i, rows in enumerate(grid_rows([len(mask) for mask in discard_channels])):
        logger.info("Filtering Grid %d (Type %d)...", i + 1, emg_types[i])
        notch_inplace(data[rows], fsamp)
        bandpass_inplace(data[rows], fsamp, emg_types[i])


def load_step(
    filepath: str,
    file_label: str | None,
    preloaded_signal: SignalImport | None,
    progress_cb: Callable[[str, dict[str, Any]], None] | None,
) -> LoadStepOutput:
    """Load input signal data from disk, or share a preloaded signal read-only."""
    filename = file_label or Path(filepath).name
    logger.info("Processing %s...", filename)
    if progress_cb:
        progress_cb("start", {"message": "Loading signal", "file": filename, "phase": "load"})

    signal = (
        preloaded_signal.readonly_view() if preloaded_signal is not None else load_signal(filepath)
    )
    logger.info("Loaded data: %s, Fs=%s", signal.data.shape, signal.fsamp)
    return LoadStepOutput(full_path=filepath, signal=signal)


def build_manual_artifact_mask(
    artifact_regions: list[tuple[int, int]] | None,
    n_samples: int,
) -> BoolArray | None:
    """Rasterize user-drawn ``(start, end)`` sample ranges into a boolean mask."""
    if not artifact_regions or n_samples <= 0:
        return None
    mask = np.zeros(n_samples, dtype=bool)
    for start_raw, end_raw in artifact_regions:
        start, end = int(start_raw), int(end_raw)
        if end < start:
            start, end = end, start
        start = max(0, min(start, n_samples))
        end = max(0, min(end, n_samples))
        if end > start:
            mask[start:end] = True
    return mask if mask.any() else None


def _target_segments(signal: SignalImport, target_index: int | None) -> dict[str, Any] | None:
    """The segments of the auxiliary channel the user segmented, or ``None``."""
    n_aux = signal.aux_data.shape[0] if signal.aux_data.ndim == 2 else 0
    if target_index is None or not 0 <= target_index < n_aux:
        return None
    # Deterministic: the default settings give the segments the QC page showed.
    segments = segment_target(np.asarray(signal.aux_data[target_index]), signal.fsamp)
    if not segments:
        return None
    units = signal.metadata.get("aux_units")
    if isinstance(units, list):
        units = units[target_index] if target_index < len(units) else None
    names = signal.aux_names
    return {
        "target_index": target_index,
        "target_name": names[target_index] if target_index < len(names) else "",
        "target_units": units,
        "config": asdict(SegmentationConfig()),
        "segments": [segment.to_dict() for segment in segments],
    }


def preprocess_step(
    loaded: LoadStepOutput,
    duration: float | None,
    manual_roi: bool,
    roi: tuple[int, int] | None,
    rois: list[tuple[int, int]] | None,
    params: DecompositionParameters,
    discard_overrides: list[list[int]] | None,
    artifact_regions: list[tuple[int, int]] | None = None,
    store: ArrayStore | None = None,
    target_index: int | None = None,
) -> PreprocessStepOutput:
    """Apply channel formatting, filtering, ROI selection and, if asked, automatic QC.

    The filtered copy (``params.compute_dtype``) is allocated in ``store`` (the run's T1
    folder), so every later step reads windows and batches from the memory-mapped file.
    The filters themselves compute in float64.
    """
    store = store if store is not None else RamStore()
    fsamp = loaded.signal.fsamp
    raw = loaded.signal.data
    data = store.allocate("filtered", raw.shape, params.work_dtype)
    for lo in range(0, data.shape[0], FILTER_BLOCK_ROWS):
        data[lo : lo + FILTER_BLOCK_ROWS] = raw[lo : lo + FILTER_BLOCK_ROWS]
    grid_names = loaded.signal.grid_names
    if not grid_names:
        raise MueditError(
            "Signal has no grid name. Set signal.grid_names to a name present in "
            "_GRID_CATALOG (signal/grid.py) before decomposing."
        )
    coordinates, ied, discard_channels, emg_types = format_hdemg_signal(
        grid_names,
        discard_overrides=discard_overrides,
    )
    total_declared_channels = sum(c.shape[0] for c in coordinates)
    if total_declared_channels > data.shape[0]:
        raise MueditError(
            f"Grid catalogue declares {total_declared_channels} channels "
            f"({', '.join(grid_names)}) but the signal has only {data.shape[0]} "
            f"channels. Check that the grid name matches the recording."
        )
    declared_discard = list(discard_channels)
    _filter_grids(data, fsamp, discard_channels, emg_types)

    resolved = _resolve_rois(data, fsamp, duration, manual_roi, roi, rois, params.nwindows)

    artifact_mask: BoolArray | None = None
    if params.auto_mask_artifacts:
        grid_channel_counts = [c.shape[0] for c in coordinates]
        qc_result = run_auto_qc(
            data,
            fsamp,
            grid_channel_counts,
            grid_coordinates=coordinates,
            store=store,
        )
        artifact_mask = qc_result.artifact_mask

        for i, auto_bad in enumerate(qc_result.bad_channel_masks):
            if auto_bad.any():
                discard_channels[i] = np.maximum(
                    discard_channels[i].astype(int),
                    auto_bad.astype(int),
                )
                logger.info(
                    "Grid %d: auto-detected %d bad channel(s), %d total discarded.",
                    i + 1,
                    int(auto_bad.sum()),
                    int(discard_channels[i].sum()),
                )

    for i, mask in enumerate(discard_channels):
        if mask.all():
            raise MueditError(
                f"Grid {i + 1} ({grid_names[i]}) has every channel discarded; keep at least one."
            )

    manual_mask = build_manual_artifact_mask(artifact_regions, data.shape[1])
    if manual_mask is not None:
        artifact_mask = manual_mask if artifact_mask is None else (artifact_mask | manual_mask)
        logger.info(
            "Manual artifact regions: %d region(s), %d / %d samples (%.2f%%)",
            len(artifact_regions or []),
            int(manual_mask.sum()),
            data.shape[1],
            100.0 * manual_mask.sum() / max(data.shape[1], 1),
        )

    return PreprocessStepOutput(
        signal=loaded.signal.without_data(),
        data=store.seal(data),
        fsamp=fsamp,
        grid_names=grid_names,
        coordinates=coordinates,
        ied=ied,
        discard_channels=discard_channels,
        muscle_names=loaded.signal.muscle_names,
        loader_meta=loaded.signal.metadata,
        rois=resolved,
        coordinates_plateau=_build_coordinates_plateau(len(grid_names), resolved),
        artifact_mask=artifact_mask,
        target_segments=_target_segments(loaded.signal, target_index),
        declared_discard_channels=declared_discard,
    )


def bids_export_step(
    loaded: LoadStepOutput,
    prep: PreprocessStepOutput,
    bids_root: str,
    bids_entities: dict[str, Any] | None,
    bids_metadata: dict[str, Any] | None,
) -> PreprocessStepOutput:
    """Export the raw EMG, its sidecars and events to ``bids_root``; the run then saves there too."""
    entities = bids_entities or {}
    names = form_entities(entities)
    loader_meta = prep.loader_meta
    fields = bids_emg_fields(
        form_over_loader_meta(loader_meta, entities),
        entities.get("target_muscle") or prep.muscle_names,
        entities.get("task_description"),
    )
    fields["emg_json_extra"] = {**(fields["emg_json_extra"] or {}), **(bids_metadata or {})}
    skip_existing = bool(loader_meta.get("bids_emg_path"))
    if skip_existing:
        logger.info(
            "Source is already BIDS (%s); exporting only missing files.",
            loader_meta.get("bids_emg_path"),
        )

    signal = loaded.signal
    out = export_bids_emg(
        signal.data,  # unfiltered
        signal.fsamp,
        prep.grid_names,
        prep.coordinates,
        prep.declared_discard_channels,
        Path(bids_root),
        ied=prep.ied,
        subject=names.subject,
        task=names.task or "task",
        run=names.run,
        session=names.session,
        acquisition=names.acquisition,
        recording=names.recording,
        file_format=entities.get("file_format", "edf"),
        aux_data=signal.aux_data,
        aux_names=signal.aux_names,
        skip_existing=skip_existing,
        auto_discard_channels=prep.discard_channels,
        **fields,
    )
    # The events are refreshed even when the source is already BIDS: they are merged, not skipped.
    export_bids_events(
        out["edf"], signal.fsamp, aux_data=signal.aux_data, target_segments=prep.target_segments
    )
    write_participant(Path(bids_root), names.subject, entities.get("participant_meta"))

    emg_path = resolve_bids_emg_path(Path(bids_root), names.label)
    # export_step saves the .npz in this file's derivatives.
    return replace(
        prep,
        loader_meta={
            **loader_meta,
            "bids_entity_label": names.label,
            "bids_emg_path": str(emg_path),
        },
    )
