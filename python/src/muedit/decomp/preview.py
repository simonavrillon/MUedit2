"""Utilities for building frontend preview payloads from decomposition output."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import DTypeLike

from muedit.models import BoolArray, FloatArray, IntArray, SignalImport


def downsample_vector(
    vector: FloatArray, source_fs: float, target_fs: float = 1000.0
) -> list[float]:
    """Decimate a 1-D array from source_fs to target_fs by integer slicing."""
    if vector.size == 0:
        return []
    if source_fs <= 0 or target_fs <= 0:
        raise ValueError(
            f"Sample rates must be positive (source_fs={source_fs}, "
            f"target_fs={target_fs}); cannot downsample."
        )
    step = max(1, int(np.round(source_fs / target_fs)))
    return vector[::step].astype(float).tolist()


def abs_means(
    data: FloatArray, keep: BoolArray | None = None, dtype: DTypeLike | None = None
) -> tuple[FloatArray, FloatArray]:
    """Mean of ``|data|`` over the kept rows per sample, and over samples per row, one row at a time.

    Sums are taken in ``dtype``: by default the data's own float type.
    """
    # Rows are accumulated in order, which is bit-identical to np.mean(np.abs(data),
    # axis=0 / 1) for the C-contiguous arrays used here, without a full-size abs copy.
    # Fortran-ordered input (e.g. MAT v7.3 or OTB loaders) can differ in the last ulp.
    n_rows, n_samples = data.shape
    if dtype is not None:
        dtype = np.dtype(dtype)
    elif np.issubdtype(data.dtype, np.floating):
        dtype = data.dtype
    else:
        dtype = np.dtype(np.float64)
    row = np.empty(n_samples, dtype=dtype)
    total = np.zeros(n_samples, dtype=dtype)
    row_means = np.empty(n_rows, dtype=dtype)
    for r in range(n_rows):
        np.abs(data[r], out=row)
        row_means[r] = row.mean()
        if keep is None or keep[r]:
            total += row
    total /= n_rows if keep is None else int(np.count_nonzero(keep))
    return total, row_means


def build_preview_payload(
    signal: SignalImport,
    data: FloatArray,
    fsamp: float,
    pulse_t: FloatArray,
    distime: list[IntArray],
    grid_names: list[str],
    roi_list: list[tuple[int, int]],
    discard_channels: list[IntArray],
    coordinates: list[FloatArray],
    mu_grid_index: list[int],
    loader_meta: dict[str, Any],
    muscles: list[str],
    include_full_preview: bool,
) -> dict[str, Any]:
    """Build the preview payload dict sent to the frontend after decomposition."""
    preview_signal, _ = abs_means(data)
    pulse_preview: list[list[float]] = []
    pulse_preview_all: list[list[float]] = []
    pulse_full_all: FloatArray | list[list[float]] = []
    distime_lists: list[list[int]] = []

    if pulse_t.size > 0:
        if include_full_preview:
            pulse_full_all = pulse_t  # the API frame casts to float32 while packing
        for i in range(pulse_t.shape[0]):
            if not include_full_preview:
                ds = downsample_vector(pulse_t[i, :], fsamp)
                pulse_preview_all.append(ds)
                if i < 3:
                    pulse_preview.append(ds)
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
        "auxiliary": [downsample_vector(row, fsamp) for row in signal.auxiliary]
        if signal.auxiliary.size > 0
        else [],
        "auxiliaryname": signal.auxiliaryname,
    }

    ch_idx_tmp = 0
    grid_means: list[list[float]] = []
    channel_means: list[list[float]] = []
    for i in range(len(grid_names)):
        mask = np.array(discard_channels[i]).astype(int)
        n_channels_grid = mask.size
        grid_block = data[ch_idx_tmp : ch_idx_tmp + n_channels_grid, :]
        grid_mean_abs, grid_channel_means = abs_means(grid_block, keep=mask == 0)
        grid_means.append(downsample_vector(grid_mean_abs, fsamp))
        channel_means.append(grid_channel_means.tolist())
        ch_idx_tmp += n_channels_grid

    preview["grid_mean_abs"] = grid_means
    preview["channel_means"] = channel_means
    preview["coordinates"] = [c.tolist() if hasattr(c, "tolist") else c for c in coordinates]
    return preview
