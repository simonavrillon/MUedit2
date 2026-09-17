"""Shared signal-processing primitives for the offline and adaptive decomposition paths."""

from __future__ import annotations

import numpy as np
from scipy.cluster.vq import ClusterError, kmeans2
from scipy.signal import find_peaks

from muedit.models import FloatArray, IntArray

KMEANS_ITER: int = 10
DECOMP_MIN_ISI_SEC: float = 0.02
POSTPROC_MIN_ISI_SEC: float = 0.005


def extend_signal(signal: FloatArray, exfactor: int, samples_first: bool = False) -> FloatArray:
    """Delay-embedding channel extension used by convolutive source separation."""
    if samples_first:
        if exfactor <= 1:
            return signal.copy()
        n_samples, n_channels = signal.shape
        output = np.zeros((n_samples, n_channels * exfactor), dtype=signal.dtype)
        for i in range(exfactor):
            output[i:, n_channels * i : n_channels * (i + 1)] = signal[: n_samples - i]
        return output

    out_dtype = np.result_type(signal.dtype, np.float64)
    if exfactor <= 1:
        return signal.astype(out_dtype, copy=True)

    rows, cols = signal.shape
    extended_rows = rows * exfactor
    extended_cols = cols + exfactor - 1
    esample = np.zeros((extended_rows, extended_cols), dtype=out_dtype)
    for m in range(exfactor):
        esample[m * rows : (m + 1) * rows, m : cols + m] = signal
    return esample


def signed_square(x: FloatArray) -> FloatArray:
    """Signed-squared nonlinearity ``x * |x|`` used to build pulse trains."""
    return x * np.abs(x)


def find_refractory_peaks(
    signal: FloatArray,
    fsamp: float,
    min_isi_sec: float = DECOMP_MIN_ISI_SEC,
    **kwargs: object,
) -> IntArray:
    """Peak picking with a refractory-distance constraint."""
    distance = int(np.round(fsamp * min_isi_sec))
    peaks, _ = find_peaks(signal, distance=distance, **kwargs)
    return peaks


def enforce_refractory(
    spikes: IntArray,
    values: FloatArray,
    fsamp: float,
    min_isi_sec: float = POSTPROC_MIN_ISI_SEC,
) -> IntArray:
    """Thin an already-sorted spike train to respect the refractory period."""
    distance = int(np.round(fsamp * min_isi_sec))
    if spikes.size < 2 or distance <= 0:
        return spikes

    kept: list[int] = [int(spikes[0])]
    for raw in spikes[1:]:
        spike = int(raw)
        if spike - kept[-1] < distance:
            if values[spike] > values[kept[-1]]:
                kept[-1] = spike
        else:
            kept.append(spike)
    return np.asarray(kept, dtype=int)


def split_by_amplitude(
    values: FloatArray,
    peaks: IntArray,
    kmeans_iter: int = KMEANS_ITER,
    missing: str = "raise",
    seed: int = 0,
) -> tuple[IntArray, FloatArray, IntArray]:
    """Split peak amplitudes into low/high clusters and return the high ones."""
    try:
        centroids, labels = kmeans2(
            values[peaks], 2, iter=kmeans_iter, minit="++", missing=missing, seed=seed
        )
    except ClusterError:
        peak_vals = values[peaks]
        centroid = float(np.mean(peak_vals)) if peak_vals.size > 0 else 0.0
        centroids = np.array([centroid, centroid])
        labels = np.zeros(len(peaks), dtype=int)
    hi = int(np.argmax(centroids))
    high_indices = peaks[labels == hi]
    return high_indices, centroids, labels


def isi_cov(spikes: IntArray, fsamp: float, fallback: float = np.nan) -> float:
    """Coefficient of variation of inter-spike intervals."""
    spikes = np.asarray(spikes)
    if spikes.size < 2:
        return fallback
    isi = np.diff(spikes) / fsamp
    mean_isi = np.mean(isi)
    if mean_isi <= 0:
        return fallback
    return float(np.std(isi) / mean_isi)
