"""Signal downsampling and smoothing utilities for preview/QC rendering."""

from __future__ import annotations

import numpy as np

from muedit.models import FloatArray

PREVIEW_MOVING_AVG_MS: float = 25.0


def moving_average_ms(series: FloatArray, fsamp: float, window_ms: float) -> FloatArray:
    """Compute moving average with window size specified in milliseconds."""
    x = np.asarray(series, dtype=np.float32).reshape(-1)
    if x.size == 0 or fsamp <= 0 or window_ms <= 0:
        return x
    window_samples = max(1, int(round((window_ms / 1000.0) * fsamp)))
    if window_samples == 1:
        return x
    kernel = np.ones(window_samples, dtype=np.float32)
    sums = np.convolve(x, kernel, mode="same")
    counts = np.convolve(np.ones_like(x), kernel, mode="same")
    return (sums / counts).astype(np.float32)
