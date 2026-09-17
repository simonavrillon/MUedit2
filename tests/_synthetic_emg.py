"""Synthetic HD-EMG shared by the QC test modules."""

from __future__ import annotations

import numpy as np

from muedit.signal.filters import bandpass_signals

FSAMP = 2000.0
N_CHANNELS = 64
N_SAMPLES = 20_000  # 10 s @ 2000 Hz


def grid_coords() -> np.ndarray:
    """(row, col) positions of a 13x5 grid, first 64 sites."""
    rows, cols = np.divmod(np.arange(N_CHANNELS), 5)
    return np.column_stack([rows, cols]).astype(float)


def bandpass(data: np.ndarray) -> np.ndarray:
    return bandpass_signals(np.asarray(data, dtype=np.float32), FSAMP, emg_type=1).astype(
        np.float32
    )


def spatial_sources(rng: np.random.Generator, n_samples: int, amplitude: float) -> np.ndarray:
    """Twenty band-limited sources seen through wide Gaussian spatial kernels."""
    coords = grid_coords()
    sources = bandpass(rng.normal(0, 1, size=(20, n_samples)))
    out = np.zeros((N_CHANNELS, n_samples), dtype=np.float32)
    for src in sources:
        center = np.array([rng.uniform(0, 12), rng.uniform(0, 4)])
        kernel = np.exp(-((coords - center) ** 2).sum(axis=1) / 18.0)
        out += amplitude * kernel[:, None] * src[None, :]
    return out


def correlated_emg(seed: int = 42, amplitude: float = 0.01) -> np.ndarray:
    """One clean 64-channel grid in which every electrode tracks its neighbours."""
    rng = np.random.default_rng(seed)
    data = spatial_sources(rng, N_SAMPLES, amplitude)
    data += amplitude * 0.5 * bandpass(rng.normal(0, 1, size=(1, N_SAMPLES)))
    data += rng.normal(0, amplitude * 0.2, size=data.shape).astype(np.float32)
    return bandpass(data)


def add_contraction(data: np.ndarray, start: int, end: int, seed: int = 99) -> np.ndarray:
    """Superimpose extra spatially distributed motor-unit activity on ``[start, end)``."""
    out = data.copy()
    out[:, start:end] += spatial_sources(np.random.default_rng(seed), end - start, 0.05)
    return out
