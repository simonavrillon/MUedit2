"""Utilities for building frontend preview payloads from decomposition output."""

from __future__ import annotations

from typing import Any

import numpy as np

from muedit.models import BoolArray, FloatArray, IntArray


def abs_means(data: FloatArray, keep: BoolArray | None = None) -> tuple[FloatArray, FloatArray]:
    """Mean of ``|data|`` over the kept rows per sample, and over samples per row, one row at a time.

    Sums are taken in the data's own float type.
    """
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
    """Build the preview payload of a finished run.

    With ``include_full_preview``, ``pulse_trains_full`` is ``pulse_t`` itself: the server
    keeps it for ``/series/pulse`` and the run save, and never sends it. Series the frontend
    draws for the recording come from the upload's ``/series/*`` endpoints, not from here.
    """
    channel_means: list[list[float]] = []
    first = 0
    for cell in discard_channels:
        n_channels = np.asarray(cell).size
        channel_means.append(abs_means(data[first : first + n_channels])[1].tolist())
        first += n_channels

    return {
        "fsamp": fsamp,
        "distime_all": [np.asarray(d, dtype=np.int32) for d in distime],
        "total_samples": data.shape[1],
        "grid_names": grid_names,
        "rois": roi_list,
        "pulse_trains_full": pulse_t if include_full_preview and pulse_t.size > 0 else None,
        "mu_grid_index": mu_grid_index,
        "metadata": loader_meta,
        "muscle": muscles,
        "channel_means": channel_means,
        "coordinates": [c.tolist() if hasattr(c, "tolist") else c for c in coordinates],
    }
