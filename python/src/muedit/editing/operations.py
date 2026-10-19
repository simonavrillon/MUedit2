"""Motor-unit editing utilities used by API routes and interactive tooling."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeAlias

import numpy as np
from numpy.typing import ArrayLike

from muedit.decomp.algorithm import (
    pca_extended_signal,
    subtract_mu_waveforms,
    vec_mat,
    whiten_extended_signal,
)
from muedit.decomp.types import (
    DEFAULT_COMPUTE_DTYPE,
    DEFAULT_NBEXTCHAN,
    DEFAULT_PEEL_OFF_WIN_SEC,
    ComputeDtype,
)
from muedit.models import BoolArray, FloatArray, IntArray, spike_array
from muedit.signal.decomp_primitives import (
    POSTPROC_MIN_ISI_SEC,
    extend_signal,
    extension_factor,
    find_refractory_peaks,
    signed_square,
    split_by_amplitude,
)
from muedit.signal.filters import bandpass_signals
from muedit.signal.streaming import extend_mask

SpikeTimes: TypeAlias = IntArray
"""Discharge times: sorted, unique, non-negative int32 sample indices (see ``spike_array``)."""
FilterUpdateResult: TypeAlias = tuple[FloatArray | None, SpikeTimes]

#: Samples around an original spike searched for its peak when ``lock_spikes`` is set.
_LOCK_RADIUS = 10


def _values_at(pulse: FloatArray, times: IntArray) -> FloatArray:
    """``pulse`` at ``times``, 0 where a time falls outside it."""
    out = np.zeros(times.shape, dtype=np.float64)
    ok = (times >= 0) & (times < len(pulse))
    out[ok] = pulse[times[ok]]
    return out


def _in_window(times: IntArray, lo: int, hi: int) -> IntArray:
    """``times`` inside ``[lo, hi)``, shifted to start at ``lo``."""
    return times[(times >= lo) & (times < hi)] - lo


def _whitened_window(
    window_emg: FloatArray,
    edge: int,
    nbextchan: int,
    artifact_mask: BoolArray | None,
    compute_dtype: ComputeDtype,
) -> FloatArray:
    """The window extended and whitened; PCA skips the padded ends, the edges and artifacts."""
    ex_factor = extension_factor(nbextchan, window_emg.shape[0])
    e_sig = extend_signal(window_emg, ex_factor, dtype=compute_dtype)
    win_len = window_emg.shape[1]
    pca_keep = np.zeros(e_sig.shape[1], dtype=bool)
    pca_keep[max(edge, ex_factor - 1) : win_len - edge] = True
    if artifact_mask is not None:
        clean = pca_keep.copy()  # the padded tail is never kept, so the mask stops at win_len
        clean[:win_len] &= ~extend_mask(artifact_mask, ex_factor)
        if np.count_nonzero(clean) > e_sig.shape[0]:
            pca_keep = clean
    pca_cols = np.flatnonzero(pca_keep) if pca_keep.any() else None
    eigenvectors, eigenvalues_diag = pca_extended_signal(e_sig, pca_cols)
    w_sig, _ = whiten_extended_signal(e_sig, eigenvectors, eigenvalues_diag, inplace=True)
    return w_sig


def _realign(spikes: IntArray, peaks: IntArray, edge: int, n: int) -> IntArray:
    """Each spike moved to the nearest of ``peaks`` within ``_LOCK_RADIUS`` and the edges."""
    lo = np.maximum(edge, spikes - _LOCK_RADIUS)
    hi = np.minimum(n - edge, spikes + _LOCK_RADIUS + 1)
    j = np.searchsorted(peaks, spikes)
    left = peaks[np.maximum(j - 1, 0)]
    right = peaks[np.minimum(j, peaks.size - 1)]
    left_ok = (j > 0) & (left >= lo) & (left < hi)
    right_ok = (j < peaks.size) & (right >= lo) & (right < hi)
    # On a tie the earlier peak wins.
    take_left = left_ok & (~right_ok | (spikes - left <= right - spikes))
    return np.where(take_left, left, np.where(right_ok, right, spikes))


def _window_pulse_train(
    w_sig: FloatArray, local: IntArray, win_len: int, edge: int, win_mask: BoolArray | None
) -> FloatArray:
    """The pulse train of the filter averaged over ``local``, zero at the edges and artifacts."""
    mu_filter = np.sum(w_sig[:, local], axis=1, dtype=np.float64)
    norm = float(np.linalg.norm(mu_filter))
    if norm > 0.0:
        mu_filter /= norm
    pt: FloatArray = vec_mat(mu_filter, w_sig).astype(np.float64)[:win_len]
    pt[:edge] = 0
    pt[-edge:] = 0
    pt = signed_square(pt)
    if win_mask is not None:
        pt[win_mask] = 0.0
    return pt


def _detect_in_window(
    pt: FloatArray,
    fsamp: float,
    local: IntArray,
    edge: int,
    win_mask: BoolArray | None,
    lock_spikes: bool,
) -> IntArray | None:
    """The window's discharges in ``pt``, or None when its peaks do not split in two."""
    peaks = find_refractory_peaks(pt, fsamp, min_isi_sec=POSTPROC_MIN_ISI_SEC)
    if peaks.size <= 2:
        return None
    found, centroids, labels = split_by_amplitude(pt, peaks, missing="warn")
    if len(np.unique(labels)) < 2:
        return None
    if win_mask is not None:
        found = found[~win_mask[found]]
    if lock_spikes:
        found = np.union1d(_realign(local, peaks, edge, pt.size), found)
    return found[pt[found] <= 3 * centroids[int(np.argmax(centroids))]]


def _recompute_spikes_in_window(
    emg: FloatArray,
    spike_times: ArrayLike,
    fsamp: float,
    start: int,
    end: int,
    nbextchan: int,
    peeloff_spike_times: Sequence[ArrayLike] | None = None,
    peeloff_win: float = DEFAULT_PEEL_OFF_WIN_SEC,
    emg_offset: int = 0,
    use_peeloff: bool = False,
    artifact_times: ArrayLike | None = None,
    lock_spikes: bool = False,
    artifact_mask: BoolArray | None = None,
    bandpass: bool = True,
    compute_dtype: ComputeDtype = DEFAULT_COMPUTE_DTYPE,
) -> FilterUpdateResult:
    """Recompute motor-unit pulse train and spikes within a visible time window.

    The extended and whitened window is ``compute_dtype``; the pulse train and detection are float64.
    """
    spikes = spike_array(spike_times)
    edge = int(round(0.1 * fsamp))
    win_len = end - start
    slice_start, slice_end = start - emg_offset, end - emg_offset
    if emg.size == 0 or win_len <= 2 * edge or slice_start < 0 or slice_end > emg.shape[1]:
        return None, spikes
    local = _in_window(spikes, start + edge, end - edge) + edge
    if local.size == 0:
        return None, spikes

    window_emg = emg[:, slice_start:slice_end]
    if bandpass:
        window_emg = bandpass_signals(window_emg, fsamp)
    win_mask = None
    if artifact_mask is not None:
        wm = artifact_mask[start:end]
        win_mask = wm if wm.size == win_len and wm.any() else None
    w_sig = _whitened_window(window_emg, edge, nbextchan, win_mask, compute_dtype)

    subtracted = list(peeloff_spike_times or []) if use_peeloff else []
    if artifact_times is not None:
        subtracted.append(artifact_times)
    for times in subtracted:
        inside = _in_window(spike_array(times), start + edge, end - edge) + edge
        if inside.size:
            subtract_mu_waveforms(w_sig, inside, fsamp, peeloff_win)

    pt = _window_pulse_train(w_sig, local, win_len, edge, win_mask)
    found = _detect_in_window(pt, fsamp, local, edge, win_mask, lock_spikes)
    if found is None:
        return None, spikes
    outside = spikes[(spikes < start + edge) | (spikes >= end - edge)]
    return pt, spike_array(np.concatenate([outside, found + start]))


def update_motor_unit_filter_window(
    emg: FloatArray,
    emg_mask: IntArray,
    spike_times: ArrayLike,
    fsamp: float,
    start: int,
    end: int,
    nbextchan: int = DEFAULT_NBEXTCHAN,
    peeloff_spike_times: Sequence[ArrayLike] | None = None,
    peeloff_win: float = DEFAULT_PEEL_OFF_WIN_SEC,
    emg_offset: int = 0,
    use_peeloff: bool = False,
    artifact_times: ArrayLike | None = None,
    lock_spikes: bool = False,
    artifact_mask: BoolArray | None = None,
    bandpass: bool = True,
    compute_dtype: ComputeDtype = DEFAULT_COMPUTE_DTYPE,
) -> FilterUpdateResult:
    """Update a motor-unit pulse train and spikes inside a time window; ``bandpass=False`` for filtered EMG."""
    slice_start, slice_end = start - emg_offset, end - emg_offset
    if 0 <= slice_start < slice_end <= emg.shape[1]:
        emg, emg_offset = emg[:, slice_start:slice_end], start
    emg_sel = np.asarray(emg[emg_mask == 0, :] if emg_mask.size else emg, dtype=float)
    return _recompute_spikes_in_window(
        emg_sel,
        spike_times,
        fsamp,
        start,
        end,
        nbextchan=nbextchan,
        peeloff_spike_times=peeloff_spike_times,
        peeloff_win=peeloff_win,
        emg_offset=emg_offset,
        use_peeloff=use_peeloff,
        artifact_times=artifact_times,
        lock_spikes=lock_spikes,
        artifact_mask=artifact_mask,
        bandpass=bandpass,
        compute_dtype=compute_dtype,
    )


def add_spikes_in_roi(
    pulse: FloatArray,
    spike_times: ArrayLike,
    fsamp: float,
    x_start: int,
    x_end: int,
    y_min: float,
) -> SpikeTimes:
    """Add the peaks above ``y_min`` inside ``[x_start, x_end]``."""
    lo, hi = max(0, x_start), max(0, x_end + 1)
    roi = np.zeros_like(pulse)
    roi[lo:hi] = pulse[lo:hi]
    peaks = find_refractory_peaks(roi, fsamp, min_isi_sec=POSTPROC_MIN_ISI_SEC, height=y_min)
    return spike_array(np.concatenate([spike_array(spike_times), peaks]))


def delete_spikes_in_roi(
    pulse: FloatArray,
    spike_times: ArrayLike,
    x_start: int,
    x_end: int,
    y_min: float,
    y_max: float,
) -> SpikeTimes:
    """Delete the spikes inside ``[x_start, x_end]`` whose amplitude is within ``[y_min, y_max]``."""
    times = spike_array(spike_times)
    values = _values_at(pulse, times)
    low, high = min(y_min, y_max), max(y_min, y_max) + 1
    in_box = (times >= x_start) & (times <= x_end) & (values >= low) & (values <= high)
    return times[~in_box]


def _drop_weaker(pulse: FloatArray, ordered: IntArray, pairs: IntArray) -> SpikeTimes:
    """``ordered`` without the lower-amplitude spike of each pair ``(pairs[k], pairs[k] + 1)``."""
    values = _values_at(pulse, ordered)
    drop = np.where(values[pairs] < values[pairs + 1], pairs, pairs + 1)
    keep = np.ones(ordered.size, dtype=bool)
    keep[drop] = False
    return ordered[keep]


def remove_discharge_rate_outliers(
    pulse: FloatArray,
    spike_times: ArrayLike,
    fsamp: float,
    z_factor: float = 3.0,
) -> SpikeTimes:
    """Delete one spike of each pair whose discharge rate is above mean + ``z_factor`` × std."""
    ordered = spike_array(spike_times)
    if ordered.size < 3:
        return ordered
    rate = fsamp / np.diff(ordered)
    threshold = rate.mean() + z_factor * rate.std()
    return _drop_weaker(pulse, ordered, np.flatnonzero(rate > threshold))
