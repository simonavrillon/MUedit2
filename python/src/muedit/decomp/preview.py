"""Utilities for building frontend preview payloads from decomposition output."""

from __future__ import annotations

from typing import Any

import numpy as np


def downsample_vector(
    vector: np.ndarray, source_fs: float, target_fs: float = 1000.0
) -> list[float]:
    """Decimate a 1-D array from source_fs to target_fs by integer slicing."""
    if vector.size == 0:
        return []
    if source_fs <= 0 or target_fs <= 0:
        return vector.astype(float).tolist()

    step = max(1, int(np.round(source_fs / target_fs)))
    return vector[::step].astype(float).tolist()


def build_preview_payload(
    signal: dict[str, Any],
    data: np.ndarray,
    fsamp: float,
    pulse_t: np.ndarray,
    distime: list[np.ndarray],
    grid_names: list[str],
    roi_list: list[tuple[int, int]],
    discard_channels: list[np.ndarray],
    coordinates: list[np.ndarray],
    mu_grid_index: list[int],
    loader_meta: dict[str, Any],
    muscles: list[str],
    include_full_preview: bool,
) -> dict[str, Any]:
    """Build the preview payload dict sent to the frontend after decomposition."""
    preview_signal = np.mean(np.abs(data), axis=0)
    pulse_preview: list[list[float]] = []
    pulse_preview_all: list[list[float]] = []
    pulse_full_all: list[list[float]] = []
    distime_lists: list[list[int]] = []

    if pulse_t.size > 0:
        for i in range(pulse_t.shape[0]):
            ds = downsample_vector(pulse_t[i, :], fsamp)
            pulse_preview_all.append(ds)
            if i < 3:
                pulse_preview.append(ds)
            if include_full_preview:
                pulse_full_all.append(pulse_t[i, :].astype(float).tolist())
            distime_lists.append([int(x) for x in distime[i]])

    preview = {
        "mean_abs": downsample_vector(preview_signal, fsamp),
        "pulse_trains": pulse_preview,
        "fsamp": fsamp,
        "distime": list(distime_lists),
        "distime_all": list(distime_lists),
        "total_samples": data.shape[1],
        "grid_names": grid_names,
        "grid_mean_abs": [],
        "rois": roi_list,
        "pulse_trains_all": pulse_preview_all,
        "pulse_trains_full": pulse_full_all,
        "mu_grid_index": mu_grid_index,
        "metadata": loader_meta,
        "muscle": muscles,
        "auxiliary": (
            [
                downsample_vector(signal["auxiliary"][i, :], fsamp)
                for i in range(signal["auxiliary"].shape[0])
            ]
            if signal.get("auxiliary") is not None and signal["auxiliary"].size > 0
            else []
        ),
        "auxiliaryname": signal.get("auxiliaryname"),
    }

    ch_idx_tmp = 0
    grid_means: list[list[float]] = []
    channel_means: list[list[float]] = []
    for i in range(len(grid_names)):
        mask = np.array(discard_channels[i]).astype(int)
        n_channels_grid = mask.size
        keep_idx = np.where(mask == 0)[0]
        grid_block = data[ch_idx_tmp : ch_idx_tmp + n_channels_grid, :]
        grid_data = grid_block[keep_idx, :]
        grid_mean_abs = np.mean(np.abs(grid_data), axis=0)
        grid_means.append(downsample_vector(grid_mean_abs, fsamp))
        channel_means.append(np.mean(np.abs(grid_block), axis=1).tolist())
        ch_idx_tmp += n_channels_grid

    preview["grid_mean_abs"] = grid_means
    preview["channel_means"] = channel_means
    preview["coordinates"] = [c.tolist() if hasattr(c, "tolist") else c for c in coordinates]
    return preview
