"""Synthetic HD-EMG shared by the QC test modules."""

from __future__ import annotations

import numpy as np

from muedit.signal.filters import bandpass_signals

FSAMP = 2000.0
N_CHANNELS = 64
N_SAMPLES = 20_000


def grid_coords() -> np.ndarray:
    """(row, col) positions of a 13x5 grid, first 64 sites."""
    rows, cols = np.divmod(np.arange(N_CHANNELS), 5)
    return np.column_stack([rows, cols]).astype(float)


def bandpass(data: np.ndarray, fsamp: float = FSAMP) -> np.ndarray:
    return bandpass_signals(np.asarray(data, dtype=np.float32), fsamp, emg_type=1).astype(
        np.float32
    )


def spatial_sources(
    rng: np.random.Generator, n_samples: int, amplitude: float, fsamp: float = FSAMP
) -> np.ndarray:
    """Twenty band-limited sources seen through wide Gaussian spatial kernels."""
    coords = grid_coords()
    sources = bandpass(rng.normal(0, 1, size=(20, n_samples)), fsamp)
    out = np.zeros((N_CHANNELS, n_samples), dtype=np.float32)
    for src in sources:
        center = np.array([rng.uniform(0, 12), rng.uniform(0, 4)])
        kernel = np.exp(-((coords - center) ** 2).sum(axis=1) / 18.0)
        out += amplitude * kernel[:, None] * src[None, :]
    return out


def correlated_emg(
    seed: int = 42,
    amplitude: float = 0.01,
    n_samples: int = N_SAMPLES,
    fsamp: float = FSAMP,
) -> np.ndarray:
    """One clean 64-channel grid in which every electrode tracks its neighbours."""
    rng = np.random.default_rng(seed)
    data = spatial_sources(rng, n_samples, amplitude, fsamp)
    data += amplitude * 0.5 * bandpass(rng.normal(0, 1, size=(1, n_samples)), fsamp)
    data += rng.normal(0, amplitude * 0.2, size=data.shape).astype(np.float32)
    return bandpass(data, fsamp)


def add_contraction(data: np.ndarray, start: int, end: int, seed: int = 99) -> np.ndarray:
    """Superimpose extra spatially distributed motor-unit activity on ``[start, end)``."""
    out = data.copy()
    out[:, start:end] += spatial_sources(np.random.default_rng(seed), end - start, 0.05)
    return out


# Refractory of the decomposition (signal.decomp_primitives.DECOMP_MIN_ISI_SEC);
# synthetic trains never violate it.
_MIN_ISI_SEC = 0.02


def motor_unit_emg(
    seed: int = 7,
    n_samples: int = N_SAMPLES,
    fsamp: float = FSAMP,
    n_units: int = 24,
    rate_hz: float = 10.0,
    amplitude: float = 0.08,
    activity: tuple[int, int] | None = None,
) -> np.ndarray:
    """Background EMG plus separable biphasic MUAP trains firing inside ``activity``."""
    out = correlated_emg(seed, amplitude=0.01, n_samples=n_samples, fsamp=fsamp)
    if activity is None:  # central 80% of the recording
        activity = (n_samples // 10, n_samples - n_samples // 10)
    lo, hi = max(0, activity[0]), min(n_samples, activity[1])
    rng = np.random.default_rng(seed + 10_000)
    coords = grid_coords()
    t = np.arange(int(round(0.015 * fsamp))) / fsamp
    refractory = int(round(_MIN_ISI_SEC * fsamp))
    for _ in range(n_units):
        center = np.array([rng.uniform(0, 12), rng.uniform(0, 4)])
        kernel = np.exp(-((coords - center) ** 2).sum(axis=1) / 8.0)
        shape = rng.uniform(0.5, 1.5) * np.exp(
            -((t - rng.uniform(0.001, 0.003)) ** 2) / (2 * 0.0015**2)
        ) - rng.uniform(0.3, 1.0) * np.exp(
            -((t - rng.uniform(0.004, 0.007)) ** 2) / (2 * 0.0015**2)
        )
        times = np.sort(rng.integers(lo, hi, size=int(rng.poisson(rate_hz * (hi - lo) / fsamp))))
        times = times[np.concatenate(([True], np.diff(times) >= refractory))]
        for spike in times:
            seg = out[:, spike : spike + shape.size]
            if seg.shape[1] < shape.size:
                continue
            seg += (amplitude * kernel[:, None]) * shape[None, :]
    return out
