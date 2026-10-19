"""Shared signal-processing primitives for the offline and adaptive decomposition paths."""

from __future__ import annotations

import numpy as np
from numpy.typing import DTypeLike
from scipy.cluster.vq import ClusterError, kmeans2
from scipy.signal import find_peaks

from muedit.models import BoolArray, FloatArray, IntArray

KMEANS_ITER: int = 10
DECOMP_MIN_ISI_SEC: float = 0.02
POSTPROC_MIN_ISI_SEC: float = 0.005


def extend_mask(mask: BoolArray, ex_factor: int) -> BoolArray:
    """Mask of the extended samples: sample ``t`` is masked if any of ``t - ex_factor + 1 .. t`` is."""
    out = np.array(mask, dtype=bool)
    for m in range(1, ex_factor):
        out[m:] |= mask[:-m]
    return out


def extend_signal_mask(mask: BoolArray, ex_factor: int) -> BoolArray:
    """:func:`extend_mask` over ``extend_signal``'s ``n + ex_factor - 1`` columns, padded tail included."""
    return extend_mask(np.pad(mask, (0, ex_factor - 1)), ex_factor)


def extension_factor(nbextchan: int, n_channels: int) -> int:
    """Delays per channel that bring ``n_channels`` closest to ``nbextchan`` extended channels."""
    return max(1, int(round(nbextchan / max(n_channels, 1))))


def extend_signal(
    data: FloatArray,
    ex_factor: int,
    samples_first: bool = False,
    dtype: DTypeLike | None = None,
) -> FloatArray:
    """Delay-embedding channel extension; channels-first output is ``dtype``, else at least float64."""
    if samples_first:
        if ex_factor <= 1:
            return data.copy()
        n_samples, n_channels = data.shape
        output = np.zeros((n_samples, n_channels * ex_factor), dtype=data.dtype)
        for i in range(ex_factor):
            output[i:, n_channels * i : n_channels * (i + 1)] = data[: n_samples - i]
        return output

    out_dtype = np.result_type(data.dtype, np.float64) if dtype is None else np.dtype(dtype)
    if ex_factor <= 1:
        return data.astype(out_dtype, copy=True)

    rows, cols = data.shape
    extended_rows = rows * ex_factor
    extended_cols = cols + ex_factor - 1
    esample = np.zeros((extended_rows, extended_cols), dtype=out_dtype)
    for m in range(ex_factor):
        esample[m * rows : (m + 1) * rows, m : cols + m] = data
    return esample


def zeroed_matmul(a: FloatArray, b: FloatArray) -> FloatArray:
    """``a @ b`` (``b`` 2-D) written into a zeroed output."""
    # Accelerate's float32 cblas_sgemv (NumPy's ``v @ m`` and one-row ``(1, k) @ (k, n)``) reads
    # the output's old contents at some column counts even with beta = 0, so a NaN left in a
    # fresh buffer comes out as NaN; a zeroed output makes that term exact.
    out = np.zeros((*a.shape[:-1], b.shape[1]), dtype=np.result_type(a, b))
    return np.matmul(a, b, out=out)


def signed_square(x: FloatArray) -> FloatArray:
    """Signed-squared nonlinearity ``x * |x|`` used to build pulse trains."""
    return x * np.abs(x)


def find_refractory_peaks(
    data: FloatArray,
    fsamp: float,
    min_isi_sec: float = DECOMP_MIN_ISI_SEC,
    **kwargs: object,
) -> IntArray:
    """Peak picking with a refractory-distance constraint."""
    distance = int(np.round(fsamp * min_isi_sec))
    peaks, _ = find_peaks(data, distance=distance, **kwargs)
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
    missing: str = "raise",
) -> tuple[IntArray, FloatArray, IntArray]:
    """Split peak amplitudes into low/high clusters and return the high ones."""
    try:
        centroids, labels = kmeans2(
            values[peaks], 2, iter=KMEANS_ITER, minit="++", missing=missing, seed=0
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
