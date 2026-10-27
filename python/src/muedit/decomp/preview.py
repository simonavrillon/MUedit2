"""Utilities for building frontend preview payloads from decomposition output."""

from __future__ import annotations

from typing import Any

import numpy as np

from muedit.models import BoolArray, FloatArray, IntArray
from muedit.signal.grid import grid_rows


def abs_means(data: FloatArray, keep: BoolArray | None = None) -> tuple[FloatArray, FloatArray]:
    """Mean ``|data|`` per sample over kept rows and per row over samples, in the data's dtype."""
    # Rows are accumulated in order, which is bit-identical to np.mean(np.abs(data),
    # axis=0 / 1) for the C-contiguous arrays used here, without a full-size abs copy.
    # Fortran-ordered input (e.g. MAT v7.3 or OTB loaders) can differ in the last ulp.
    n_rows, n_samples = data.shape
    dtype = data.dtype if np.issubdtype(data.dtype, np.floating) else np.dtype(np.float64)
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
    data: FloatArray,
    fsamp: float,
    pulse_trains: FloatArray,
    spike_times: list[IntArray],
    grid_names: list[str],
    rois: list[tuple[int, int]],
    discard_channels: list[IntArray],
    coordinates: list[FloatArray],
    mu_grid_index: list[int],
    loader_meta: dict[str, Any],
    muscle_names: list[str],
    include_full_preview: bool,
) -> dict[str, Any]:
    """Build a finished run's preview; ``pulse_trains_full`` stays on the server, never sent."""
    channel_means = [
        abs_means(data[rows])[1].tolist()
        for rows in grid_rows([len(mask) for mask in discard_channels])
    ]

    return {
        "fsamp": fsamp,
        "spike_times": [np.asarray(d, dtype=np.int32) for d in spike_times],
        "total_samples": data.shape[1],
        "grid_names": grid_names,
        "rois": rois,
        "pulse_trains_full": pulse_trains
        if include_full_preview and pulse_trains.size > 0
        else None,
        "mu_grid_index": mu_grid_index,
        "metadata": loader_meta,
        "muscle_names": muscle_names,
        "channel_means": channel_means,
        "coordinates": [c.tolist() if hasattr(c, "tolist") else c for c in coordinates],
    }
