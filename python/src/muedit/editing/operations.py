"""Motor-unit editing utilities used by API routes and interactive tooling."""

from __future__ import annotations

from typing import TypeAlias

import numpy as np
from scipy.cluster.vq import kmeans2
from scipy.signal import find_peaks

from muedit.decomp.algorithm import (
    extend_signal,
    pca_extended_signal,
    subtract_mu_waveforms,
    whiten_extended_signal,
)
from muedit.signal.filters import bandpass_signals

SpikeTimes: TypeAlias = list[int]
FilterUpdateResult: TypeAlias = tuple[np.ndarray | None, SpikeTimes]


def _remove_high_amplitude_outliers(
    pulse_train: np.ndarray, spike_indices: np.ndarray
) -> np.ndarray:
    """Drop candidate spikes with unusually large amplitudes."""
    if spike_indices.size == 0:
        return spike_indices
    threshold = np.mean(pulse_train[spike_indices]) + 3 * np.std(
        pulse_train[spike_indices]
    )
    return spike_indices[pulse_train[spike_indices] <= threshold]


def _recompute_spikes_in_window(
    emg: np.ndarray,
    spike_times: SpikeTimes,
    fsamp: float,
    start: int,
    end: int,
    nbextchan: int,
    peeloff_spike_times: list[SpikeTimes] | None = None,
    peeloff_win: float = 0.025,
    emg_offset: int = 0,
    use_peeloff: bool = False,
    artifact_times: SpikeTimes | None = None,
) -> FilterUpdateResult:
    """Recompute motor-unit pulse train and spikes within a visible time window."""
    if emg.size == 0 or start >= end:
        return None, spike_times

    edge = int(round(0.1 * fsamp))
    win_len = end - start
    if win_len <= 2 * edge:
        return None, spike_times

    window_emg = emg[:, start - emg_offset : end - emg_offset]
    window_emg = bandpass_signals(window_emg, fsamp)

    spikes_arr = np.asarray(spike_times, dtype=int)
    spikes1 = spikes_arr[(spikes_arr >= start + edge) & (spikes_arr < end - edge)]
    if spikes1.size == 0:
        return None, spike_times

    spikes2 = spikes1 - start
    ex_factor = int(round(nbextchan / max(window_emg.shape[0], 1)))
    ex_factor = max(1, ex_factor)
    e_sig = extend_signal(window_emg, ex_factor)
    eigenvectors, eigenvalues_diag = pca_extended_signal(e_sig)
    w_sig, _, _ = whiten_extended_signal(e_sig, eigenvectors, eigenvalues_diag)

    if use_peeloff and peeloff_spike_times:
        for other_spikes in peeloff_spike_times:
            local_spikes = np.asarray(other_spikes, dtype=int) - start
            local_spikes = local_spikes[
                (local_spikes >= edge) & (local_spikes < (win_len - edge))
            ]
            if local_spikes.size > 0:
                w_sig = subtract_mu_waveforms(w_sig, local_spikes, fsamp, peeloff_win)

    if artifact_times:
        local_artifacts = np.asarray(artifact_times, dtype=int) - start
        local_artifacts = local_artifacts[
            (local_artifacts >= edge) & (local_artifacts < (win_len - edge))
        ]
        if local_artifacts.size > 0:
            w_sig = subtract_mu_waveforms(w_sig, local_artifacts, fsamp, peeloff_win)

    mu_filters = np.sum(w_sig[:, spikes2], axis=1)
    norm = float(np.linalg.norm(mu_filters))
    if norm > 0.0:
        mu_filters = mu_filters / norm

    pt = mu_filters.T @ w_sig
    pt = pt[: window_emg.shape[1]]
    pt[:edge] = 0
    pt[-edge:] = 0
    pt = pt * np.abs(pt)

    peaks, _ = find_peaks(pt, distance=int(round(fsamp * 0.005)))
    if peaks.size == 0:
        return None, spike_times

    if peaks.size <= 2:
        return None, spike_times
    centroids, labels = kmeans2(pt[peaks], 2, iter=10, minit="++", missing="warn", seed=0)
    if len(np.unique(labels)) < 2:
        return None, spike_times
    idx2 = int(np.argmax(centroids))
    spikes_new = peaks[labels == idx2]
    spikes_new = _remove_high_amplitude_outliers(pt, spikes_new)

    spikes_new = spikes_new.astype(int)
    updated = [s for s in spike_times if s < start + edge or s > end - edge]
    updated.extend((spikes_new + start).tolist())
    updated = sorted({int(x) for x in updated if x >= 0})

    return pt, updated


def update_motor_unit_filter_window(
    emg: np.ndarray,
    emg_mask: np.ndarray,
    spike_times: SpikeTimes,
    fsamp: float,
    start: int,
    end: int,
    nbextchan: int = 1000,
    peeloff_spike_times: list[SpikeTimes] | None = None,
    peeloff_win: float = 0.025,
    emg_offset: int = 0,
    use_peeloff: bool = False,
    artifact_times: SpikeTimes | None = None,
) -> FilterUpdateResult:
    """Update a motor-unit pulse train and spikes inside a time window."""
    emg_sel = emg[emg_mask == 0, :] if emg_mask.size else emg
    pt, updated = _recompute_spikes_in_window(
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
    )
    return pt, updated


def add_spikes_in_roi(
    pulse: np.ndarray,
    spike_times: SpikeTimes,
    fsamp: float,
    x_start: int,
    x_end: int,
    y_min: float,
) -> SpikeTimes:
    """Add spikes inside a rectangular ROI using a peak-height threshold."""
    temp = pulse.copy()
    mask = (np.arange(len(temp)) >= x_start) & (np.arange(len(temp)) <= x_end)
    temp[~mask] = 0
    distance = int(round(fsamp * 0.005))
    peaks, _ = find_peaks(temp, height=y_min, distance=distance)
    if peaks.size == 0:
        return sorted({int(x) for x in spike_times})
    updated = list(spike_times) + peaks.astype(int).tolist()
    return sorted({int(x) for x in updated})


def add_artifact_in_roi(
    pulse: np.ndarray,
    artifact_times: SpikeTimes,
    fsamp: float,
    x_start: int,
    x_end: int,
    y_min: float,
) -> SpikeTimes:
    """Add an artifact peak inside a rectangular ROI."""
    temp = pulse.copy()
    mask = (np.arange(len(temp)) >= x_start) & (np.arange(len(temp)) <= x_end)
    temp[~mask] = 0
    distance = int(round(fsamp * 0.005))
    peaks, _ = find_peaks(temp, height=y_min, distance=distance)
    if peaks.size == 0:
        return sorted({int(x) for x in artifact_times})
    updated = list(artifact_times) + peaks.astype(int).tolist()
    return sorted({int(x) for x in updated})


def delete_spikes_in_roi(
    pulse: np.ndarray,
    spike_times: SpikeTimes,
    x_start: int,
    x_end: int,
    y_min: float,
    y_max: float,
) -> SpikeTimes:
    """Delete spikes inside ROI when amplitude is within the selected range."""
    updated = []
    ordered = sorted(spike_times)
    low = min(y_min, y_max)
    high = max(y_min, y_max)
    for t in ordered:
        if t < x_start or t > x_end:
            updated.append(int(t))
            continue
        val = pulse[t] if 0 <= t < len(pulse) else 0
        in_box = low <= val <= high
        if in_box:
            continue
        updated.append(int(t))
    return sorted({int(x) for x in updated})


def delete_high_discharge_rate_spikes_in_roi(
    pulse: np.ndarray,
    spike_times: SpikeTimes,
    fsamp: float,
    x_start: int,
    x_end: int,
    y_min: float,
) -> SpikeTimes:
    """Delete one spike from high-rate pairs within an ROI."""
    ordered = sorted(spike_times)
    if len(ordered) < 2:
        return sorted({int(x) for x in ordered})
    dist = np.array(ordered, dtype=int)
    isi = np.diff(dist)
    valid = isi > 0
    if not np.any(valid):
        return sorted({int(x) for x in ordered})
    isi = isi[valid]
    dr = fsamp / isi
    mids = dist[1:][valid] - (isi // 2)

    deletions = set()
    for i in range(len(dr)):
        mid = mids[i]
        if mid < x_start or mid > x_end:
            continue
        if dr[i] <= y_min:
            continue
        left_idx = i
        right_idx = i + 1
        left = ordered[left_idx]
        right = ordered[right_idx]
        left_val = pulse[left] if 0 <= left < len(pulse) else 0
        right_val = pulse[right] if 0 <= right < len(pulse) else 0
        deletions.add(left_idx if left_val < right_val else right_idx)

    updated = [t for j, t in enumerate(ordered) if j not in deletions]
    return sorted({int(x) for x in updated})


def remove_discharge_rate_outliers(
    pulse: np.ndarray,
    spike_times: SpikeTimes,
    fsamp: float,
    z_factor: float = 3.0,
) -> SpikeTimes:
    """Remove spikes belonging to discharge-rate outlier pairs."""
    ordered = sorted({int(x) for x in spike_times})
    if len(ordered) < 3:
        return ordered

    dr = []
    for i in range(len(ordered) - 1):
        isi = ordered[i + 1] - ordered[i]
        if isi > 0:
            dr.append(fsamp / isi)
    if not dr:
        return ordered

    mean = float(np.mean(dr))
    std = float(np.std(dr))
    threshold = mean + z_factor * std

    deletions = set()
    for i in range(len(ordered) - 1):
        isi = ordered[i + 1] - ordered[i]
        if isi <= 0:
            continue
        rate = fsamp / isi
        if rate <= threshold:
            continue
        left = ordered[i]
        right = ordered[i + 1]
        left_val = pulse[left] if 0 <= left < len(pulse) else 0
        right_val = pulse[right] if 0 <= right < len(pulse) else 0
        deletions.add(i if left_val < right_val else i + 1)

    return [spike for idx, spike in enumerate(ordered) if idx not in deletions]
