"""Signal loading/preprocessing and ROI preparation for decomposition."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

from muedit.decomp.signal_io import clone_signal, load_signal
from muedit.decomp.types import DecompositionParameters, LoadStepOutput, PreprocessStepOutput
from muedit.export.bids import export_bids_emg
from muedit.signal.filters import bandpass_signals, notch_signals
from muedit.utils import format_hdemg_signal

logger = logging.getLogger(__name__)


def select_roi_interactively(data: np.ndarray, fsamp: float) -> tuple[int, int]:
    """Display a plot and let the user click ROI start/end times."""
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


def _apply_grid_notch_filters(
    data: np.ndarray,
    fsamp: float,
    grid_names: list[str],
    coordinates: list[np.ndarray],
) -> None:
    """Apply notch filtering in-place to each grid's channels."""
    ch_idx = 0
    for i in range(len(grid_names)):
        n_channels_grid = coordinates[i].shape[0]
        grid_data = data[ch_idx : ch_idx + n_channels_grid, :]
        logger.info("Applying notch filter to Grid %d...", i + 1)
        data[ch_idx : ch_idx + n_channels_grid, :] = notch_signals(grid_data, fsamp)
        ch_idx += n_channels_grid


def _apply_grid_bandpass_filters(
    data: np.ndarray,
    fsamp: float,
    grid_names: list[str],
    coordinates: list[np.ndarray],
    emg_type: list[int],
) -> None:
    """Apply bandpass filtering in-place to each grid's channels using the grid's EMG type."""
    ch_idx = 0
    for i in range(len(grid_names)):
        n_channels_grid = coordinates[i].shape[0]
        grid_data = data[ch_idx : ch_idx + n_channels_grid, :]
        current_type = emg_type[i] if i < len(emg_type) else 1
        logger.info(
            "Applying bandpass filter to Grid %d (Type %d)...",
            i + 1,
            current_type,
        )
        data[ch_idx : ch_idx + n_channels_grid, :] = bandpass_signals(
            grid_data, fsamp, emg_type=current_type
        )
        ch_idx += n_channels_grid


def _resolve_roi_list(
    data: np.ndarray,
    fsamp: float,
    duration: float | None,
    manual_roi: bool,
    roi: tuple[int, int] | None,
    rois: list[tuple[int, int]] | None,
) -> list[tuple[int, int]]:
    """Resolve the analysis ROI from the various supported input methods.

    Priority: explicit rois list > single roi > interactive > duration > full signal.
    All sample indices are clamped to valid bounds.
    """
    total_len = data.shape[1]
    if total_len == 0:
        raise ValueError("Input signal contains zero samples; cannot define ROI.")
    roi_list: list[tuple[int, int]] = []
    if rois:
        for r in rois:
            s = max(0, min(int(r[0]), total_len - 1))
            e = max(s + 1, min(int(r[1]), total_len))
            roi_list.append((s, e))
    elif roi:
        s = max(0, min(int(roi[0]), total_len - 1))
        e = max(s + 1, min(int(roi[1]), total_len))
        roi_list = [(s, e)]
    elif manual_roi:
        s, e = select_roi_interactively(data, fsamp)
        roi_list = [(s, e)]
    elif duration is not None:
        ltime = min(total_len, int(duration * fsamp))
        roi_list = [(0, ltime)]
    else:
        roi_list = [(0, total_len)]
    return roi_list


def _build_coordinates_plateau(ngrid: int, roi_list: list[tuple[int, int]]) -> list[int]:
    """Flatten per-grid ROI boundaries into the legacy plateau format."""
    coordinates_plateau: list[int] = []
    for _ in range(ngrid):
        for s, e in roi_list:
            coordinates_plateau.extend([s, e])
    return coordinates_plateau


def _export_raw_emg_bids(
    bids_root: str | None,
    bids_entities: dict[str, Any] | None,
    bids_metadata: dict[str, Any] | None,
    data: np.ndarray,
    fsamp: float,
    grid_names: list[str],
    coordinates: list[np.ndarray],
    discard_channels: list[np.ndarray],
    ied: list[float],
    loader_meta: dict[str, Any],
    derived_bids_metadata: dict[str, Any],
    default_target_muscle: str | None,
    signal: dict[str, Any],
) -> None:
    """Export preprocessed raw EMG and sidecars to a BIDS-compatible layout."""
    if not bids_root:
        return

    entities = bids_entities or {}
    target_muscle = entities.get("target_muscle") or default_target_muscle
    emg_meta: dict[str, Any] = {}
    emg_meta.update(derived_bids_metadata)
    if bids_metadata:
        emg_meta.update(bids_metadata)

    export_bids_emg(
        data,
        fsamp,
        grid_names,
        coordinates,
        discard_channels,
        Path(bids_root),
        ied=ied,
        subject=entities.get("subject", "01"),
        task=entities.get("task", "task"),
        run=entities.get("run"),
        session=entities.get("session"),
        acquisition=entities.get("acquisition"),
        recording=entities.get("recording"),
        emg_json_extra=emg_meta,
        powerline_freq=entities.get("powerline_freq", 50.0),
        placement_scheme=entities.get("placement_scheme", "ChannelSpecific"),
        placement_scheme_description=entities.get("placement_scheme_description"),
        reference_description=entities.get("reference", "ChannelSpecific"),
        units=entities.get("units", "uV"),
        target_muscle=target_muscle,
        file_format=entities.get("file_format", "bdf"),
        hardware_filters=loader_meta.get("hardware_filters", "n/a"),
        gain=loader_meta.get("gains"),
        low_cutoff=loader_meta.get("emg_hpf"),
        high_cutoff=loader_meta.get("emg_lpf"),
        aux_data=signal.get("auxiliary"),
        aux_names=signal.get("auxiliaryname"),
        aux_gain=loader_meta.get("aux_gains"),
        aux_low_cutoff=loader_meta.get("aux_hpf"),
        aux_high_cutoff=loader_meta.get("aux_lpf"),
    )


def load_step(
    filepath: str,
    file_label: str | None,
    preloaded_signal: dict[str, Any] | None,
    progress_cb: Callable[[str, dict[str, Any]], None] | None,
) -> LoadStepOutput:
    """Load input signal data from disk or a provided preloaded mapping."""
    filename = file_label or Path(filepath).name
    logger.info("Processing %s...", filename)
    if progress_cb:
        progress_cb("start", {"message": "Loading signal", "pct": 5, "file": filename})

    signal = (
        clone_signal(preloaded_signal)
        if preloaded_signal is not None
        else load_signal(filepath)
    )
    logger.info("Loaded data: %s, Fs=%s", signal["data"].shape, signal["fsamp"])
    return LoadStepOutput(
        full_path=filepath,
        filename=filename,
        signal=signal,
        data=signal["data"],
        fsamp=float(signal["fsamp"]),
    )


def preprocess_step(
    loaded: LoadStepOutput,
    duration: float | None,
    manual_roi: bool,
    roi: tuple[int, int] | None,
    rois: list[tuple[int, int]] | None,
    params: DecompositionParameters,
    discard_overrides: list[list[int]] | None,
    bids_root: str | None,
    bids_entities: dict[str, Any] | None,
    bids_metadata: dict[str, Any] | None,
) -> PreprocessStepOutput:
    """Apply channel formatting, filtering, ROI selection, and optional BIDS raw export."""
    data = np.array(loaded.data, copy=True)
    grid_names = loaded.signal.get("gridname", ["Default"])
    coordinates, ied, discard_channels, emg_type = format_hdemg_signal(
        data,
        grid_names,
        loaded.fsamp,
        discard_overrides=discard_overrides,
    )
    _apply_grid_notch_filters(data, loaded.fsamp, grid_names, coordinates)
    _apply_grid_bandpass_filters(data, loaded.fsamp, grid_names, coordinates, emg_type)

    muscles = loaded.signal.get("muscle") or []
    device_name = loaded.signal.get("device_name")
    loader_meta = loaded.signal.get("metadata", {})
    default_target_muscle = next(
        (m for m in muscles if isinstance(m, str) and m.strip()), None
    )
    derived_bids_metadata: dict[str, Any] = {}
    if device_name:
        derived_bids_metadata["RecordingDevice"] = device_name
    if muscles and any(isinstance(m, str) and m for m in muscles):
        derived_bids_metadata["Muscles"] = muscles

    _export_raw_emg_bids(
        bids_root=bids_root,
        bids_entities=bids_entities,
        bids_metadata=bids_metadata,
        data=data,
        fsamp=loaded.fsamp,
        grid_names=grid_names,
        coordinates=coordinates,
        discard_channels=discard_channels,
        ied=ied,
        loader_meta=loader_meta,
        derived_bids_metadata=derived_bids_metadata,
        default_target_muscle=default_target_muscle,
        signal=loaded.signal,
    )

    roi_list = _resolve_roi_list(data, loaded.fsamp, duration, manual_roi, roi, rois)
    ngrid = len(grid_names)
    signal_process: dict[str, Any] = {
        "mu_filters": {},
        "w_sig": {},
        "win_data": {},
        "whiten_mat": {},
        "coordinates_plateau": _build_coordinates_plateau(ngrid, roi_list),
        "ex_factor": 0,
    }
    return PreprocessStepOutput(
        signal=loaded.signal,
        data=data,
        fsamp=loaded.fsamp,
        grid_names=grid_names,
        coordinates=coordinates,
        ied=ied,
        discard_channels=discard_channels,
        muscles=muscles,
        loader_meta=loader_meta,
        roi_list=roi_list,
        ngrid=ngrid,
        signal_process=signal_process,
    )
