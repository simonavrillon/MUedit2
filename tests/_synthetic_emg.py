"""Synthetic HD-EMG shared by the QC test modules."""

from __future__ import annotations

import numpy as np

from muedit.decomp.algorithm import pca_extended_signal, whiten_extended_signal
from muedit.models import SignalImport
from muedit.signal.decomp_primitives import extend_signal
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


#: Where ``synthetic_recording`` contracts: the window the decomposition tests decompose.
ACTIVE_ROI = (5_000, 35_000)


def synthetic_recording() -> SignalImport:
    """One float32 64-channel grid of 40 000 samples, active over ``ACTIVE_ROI``."""
    emg = motor_unit_emg(seed=5, n_samples=40_000, activity=ACTIVE_ROI)
    return SignalImport(
        data=emg.astype(np.float32), fsamp=FSAMP, grid_names=["GR08MM1305"], muscle_names=["ta"]
    )


#: Delay embedding the ``short_recording`` tests extend by.
EX = 6


def short_recording() -> np.ndarray:
    """Ten channels x 3000 samples of synthetic MU activity (float64)."""
    return motor_unit_emg(seed=5, n_samples=3000, activity=(0, 3000))[:10].astype(np.float64)


def calibration_case(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Whitening, filters and window mean of a calibration window over samples 200-2600."""
    window = raw[:, 200:2600]
    ext = extend_signal(window - window.mean(axis=1, keepdims=True), EX)
    vecs, vals = pca_extended_signal(ext)
    _, whiten_mat = whiten_extended_signal(ext, vecs, vals)
    mu_filters = np.linalg.qr(np.random.default_rng(4).standard_normal((ext.shape[0], 4)))[0]
    return whiten_mat, mu_filters, window.mean(axis=1)
