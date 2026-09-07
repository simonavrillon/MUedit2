"""Signal downsampling and smoothing utilities for preview/QC rendering."""

from __future__ import annotations

import numpy as np
from scipy.signal import decimate

PREVIEW_MOVING_AVG_MS: float = 25.0


def raw_series_at_fs(series: np.ndarray, source_fs: float, target_fs: float) -> list[float]:
    """Downsample a raw series from source_fs to target_fs with an FIR anti-alias filter."""
    x = np.asarray(series, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return []
    if source_fs <= 0 or target_fs <= 0:
        return x.astype(float).tolist()
    step = max(1, int(np.round(source_fs / target_fs)))
    if step <= 1:
        return x.astype(float).tolist()
    y = decimate(x, step, ftype="fir", zero_phase=True)
    return y.astype(float).tolist()


def moving_average_ms(series: np.ndarray, fsamp: float, window_ms: float) -> np.ndarray:
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
