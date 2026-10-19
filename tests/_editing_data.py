"""Synthetic motor units and pulse trains shared by the editing test modules."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from muedit.decomp.decomposition_file import save_decomposition_npz
from muedit.signal.filters import bandpass_signals
from muedit.signal.grid import format_hdemg_signal

FSAMP = 2000.0
N_SAMPLES = 10_000
N_CHANNELS = 32
MUAP_LEN = 20
VIEW_START, VIEW_END = 1000, 9000
EDGE = int(round(0.1 * FSAMP))


def spike_train(rate: float, seed: int, jitter: float = 20.0) -> np.ndarray:
    """Quasi-regular discharge times (samples) at ``rate`` Hz."""
    rng = np.random.default_rng(seed)
    times: list[int] = []
    t = 300
    while t < N_SAMPLES - 300:
        times.append(t)
        t += int(FSAMP / rate + rng.normal(0, jitter))
    return np.asarray(times, dtype=int)


def _muap(seed: int) -> np.ndarray:
    """Multichannel MUAP template, shape ``(N_CHANNELS, MUAP_LEN)``."""
    rng = np.random.default_rng(seed)
    tt = np.arange(MUAP_LEN)
    win = np.hanning(MUAP_LEN)
    sin = (np.sin(2 * np.pi * tt / MUAP_LEN) * win)[np.newaxis, :]
    cos = (np.cos(2 * np.pi * tt / MUAP_LEN) * win)[np.newaxis, :]
    return rng.normal(0, 1, (N_CHANNELS, 1)) * sin + 0.3 * rng.normal(0, 1, (N_CHANNELS, 1)) * cos


def synthetic_mu() -> dict[str, np.ndarray]:
    """EMG with one target MU, one interfering MU and white noise, raw and band-passed."""
    rng = np.random.default_rng(0)
    target = spike_train(12.0, seed=1)
    other = spike_train(9.0, seed=2)
    emg = rng.normal(0, 0.01, (N_CHANNELS, N_SAMPLES))
    w_target, w_other = _muap(10), 0.5 * _muap(11)
    for s in target:
        emg[:, s : s + MUAP_LEN] += w_target
    for s in other:
        emg[:, s : s + MUAP_LEN] += w_other
    return {"emg": emg, "filtered": bandpass_signals(emg, FSAMP), "target": target, "other": other}


def noisy_pulse_trains(n_mu: int, seed: int = 0) -> np.ndarray:
    """Pulse trains with random peaks, float32 like a saved file."""
    rng = np.random.default_rng(seed)
    pulse_trains = rng.random((n_mu, N_SAMPLES)).astype(np.float32) * 0.2
    for row in pulse_trains:
        row[rng.choice(N_SAMPLES, 60, replace=False)] += rng.random(60).astype(np.float32) + 0.5
    return pulse_trains


def pulse_with_peaks(peaks: dict[int, float], n: int = N_SAMPLES) -> np.ndarray:
    """Flat pulse train with isolated peaks of the given heights."""
    pulse_train = np.zeros(n)
    for t, h in peaks.items():
        pulse_train[t] = h
    return pulse_train


def in_view(spikes: np.ndarray | list[int]) -> np.ndarray:
    """The spikes a refit of ``[VIEW_START, VIEW_END)`` can redetect: inside its edges."""
    arr = np.asarray(spikes, dtype=int)
    return arr[(arr >= VIEW_START + EDGE) & (arr < VIEW_END - EDGE)]


def match_count(truth: np.ndarray, found: np.ndarray, tol: int = 2) -> int:
    """How many of ``truth`` have a spike of ``found`` within ``tol`` samples."""
    if found.size == 0:
        return 0
    return int(sum(np.min(np.abs(found - s)) <= tol for s in truth))


def write_decomposition(path: Path, *, emg: bool = False) -> Path:
    """A three-MU decomposition of one GR08MM1305 grid, with its raw EMG embedded when ``emg``."""
    coordinates, _, discard_channels, _ = format_hdemg_signal(["GR08MM1305"])
    raw = np.random.default_rng(0).normal(0, 1, (len(discard_channels[0]), N_SAMPLES))
    save_decomposition_npz(
        path,
        pulse_trains=noisy_pulse_trains(3),
        spike_times=[spike_train(10.0, seed=s) for s in (1, 2, 3)],
        fsamp=FSAMP,
        grid_names=["GR08MM1305"],
        mu_grid_index=[0, 0, 0],
        muscle_names=["ta"],
        parameters={"duplicatesthresh": 0.3},
        total_samples=N_SAMPLES,
        emg_data=raw if emg else None,
        discard_channels=discard_channels if emg else None,
        coordinates=coordinates if emg else None,
    )
    return path
