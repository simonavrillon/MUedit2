"""Shared signal-processing primitives for the offline and adaptive decomposition paths.

These helpers capture operations that were previously duplicated across
``muedit.decomp.algorithm`` (offline FastICA decomposition) and
``muedit.adapt_decomp.adaptation`` (online adaptive decomposition), with only
cosmetic differences in axis convention or guard clauses. Centralising them
keeps the two decomposition backends consistent and avoids drift.
"""

from __future__ import annotations

import numpy as np
from scipy.cluster.vq import ClusterError, kmeans2
from scipy.signal import find_peaks

#: Default k-means iteration count for the 2-cluster amplitude split.
KMEANS_ITER: int = 10

#: Minimum inter-spike interval (seconds) used during decomposition spike
#: detection — conservative, since the separator is still being refined.
DECOMP_MIN_ISI_SEC: float = 0.02

#: Minimum inter-spike interval (seconds) used during post-processing and
#: editing — tighter than :data:`DECOMP_MIN_ISI_SEC` because the separator is
#: already trusted and we want to recover closely spaced true spikes.
POSTPROC_MIN_ISI_SEC: float = 0.005


def extend_signal(signal: np.ndarray, exfactor: int, samples_first: bool = False) -> np.ndarray:
    """Delay-embedding channel extension used by convolutive source separation.

    The offline decomposition path keeps signals in ``(channels, samples)``
    layout (``samples_first=False``): the output has shape
    ``(channels * exfactor, samples + exfactor - 1)`` where the ``m``-th channel
    block is the input delayed by ``m`` samples (leading zeros).

    The adaptive path keeps signals in ``(samples, channels)`` layout
    (``samples_first=True``): the output has shape
    ``(samples, channels * exfactor)`` where the ``i``-th channel block is the
    input delayed by ``i`` samples (trailing zeros at the top).

    The two forms are transposes of each other. ``exfactor=1`` is a no-op in
    either orientation.

    Output dtype differs by orientation, preserving what each path has always
    done. The offline form promotes to at least float64 (its ``np.zeros(...)``
    allocation defaulted to float64), so an integer-typed loader feeding raw
    samples in cannot silently produce an integer extended signal. The adaptive
    form preserves the input dtype, keeping that path float32 end-to-end —
    ``emg_extended`` is its largest array and promoting it would double the
    memory of a long recording for float32-level noise.
    """
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


def signed_square(x: np.ndarray) -> np.ndarray:
    """Signed-squared nonlinearity ``x * |x|`` used to build pulse trains."""
    return x * np.abs(x)


def find_refractory_peaks(
    signal: np.ndarray,
    fsamp: float,
    min_isi_sec: float = DECOMP_MIN_ISI_SEC,
    **kwargs: object,
) -> np.ndarray:
    """Peak picking with a refractory-distance constraint.

    ``min_isi_sec`` is the minimum inter-spike interval in seconds; the
    distance in samples is ``round(fsamp * min_isi_sec)``. Extra keyword
    arguments (e.g. ``height``) are forwarded to :func:`scipy.signal.find_peaks`
    so callers that need amplitude-bounded detection can reuse this helper.
    """
    distance = int(np.round(fsamp * min_isi_sec))
    peaks, _ = find_peaks(signal, distance=distance, **kwargs)
    return peaks


def split_by_amplitude(
    values: np.ndarray,
    peaks: np.ndarray,
    kmeans_iter: int = KMEANS_ITER,
    missing: str = "raise",
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split peak amplitudes into low/high clusters and return the high ones.

    Runs 2-means (``minit="++"``) on ``values[peaks]`` and returns the peaks
    belonging to the cluster with the larger centroid, together with the
    centroids and labels so callers that need the spread (e.g. silhouette)
    or the low cluster can reuse the result.

    When all peak amplitudes are identical (degenerate clustering — e.g. a
    saturated segment or a source peeled to near-zero), k-means cannot split
    them into two non-empty clusters. In that case all peaks are assigned to
    the high cluster instead of raising, so a single bad window cannot abort
    a multi-hour run.
    """
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


def isi_cov(spikes: np.ndarray, fsamp: float, fallback: float = np.nan) -> float:
    """Coefficient of variation of inter-spike intervals.

    Returns ``fallback`` when there are fewer than two spikes or the mean ISI
    is non-positive. The ratio is scale-invariant, so ``fsamp`` only sets the
    units of the intervals and does not affect the result; pass ``1.0`` when
    working directly in samples.
    """
    spikes = np.asarray(spikes)
    if spikes.size < 2:
        return fallback
    isi = np.diff(spikes) / fsamp
    mean_isi = np.mean(isi)
    if mean_isi <= 0:
        return fallback
    return float(np.std(isi) / mean_isi)
