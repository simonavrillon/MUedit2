"""Shared spike-train agreement metrics for the decomposition test modules."""

from __future__ import annotations

from typing import Any

import numpy as np

from muedit.models import SignalImport

#: ``(detected_index, reference_index, precision, sensitivity, roa, lag)``.
Match = tuple[int, int, float, float, float, int]


# ── Core metrics -- adapted from MUdict/lib/checkduplicates.m ────────────────


def find_lag(detected: np.ndarray, gt: np.ndarray, max_lag: int) -> int:
    """Estimate the global lag between two spike trains via cross-correlation."""
    if detected.size < 2 or gt.size < 2:
        return 0
    lags = np.arange(-max_lag, max_lag + 1)
    shifted = np.asarray(detected, dtype=np.int64)[None, :] - lags[:, None]
    overlaps = np.isin(shifted, np.asarray(gt, dtype=np.int64)).sum(axis=1)
    return int(lags[int(np.argmax(overlaps))])


def count_matches(detected: np.ndarray, gt: np.ndarray, tol: int) -> int:
    """Count detected spikes matching a reference spike within ``tol`` samples."""
    detected = np.asarray(detected, dtype=np.int64)
    gt = np.asarray(gt, dtype=np.int64)
    if detected.size == 0 or gt.size == 0:
        return 0
    idx = np.searchsorted(gt, detected)
    hit = np.zeros(detected.size, dtype=bool)
    for offset in (-1, 0, 1):
        k = idx + offset
        valid = (k >= 0) & (k < gt.size)
        near = gt[np.clip(k, 0, gt.size - 1)]
        hit |= valid & (np.abs(near - detected) <= tol)
    return int(hit.sum())


def compute_metrics(
    detected: np.ndarray,
    ref: np.ndarray,
    jitter: int,
    max_lag: int,
) -> tuple[int, float, float, float, int]:
    """Return ``(tp, precision, sensitivity, roa, lag)`` for one MU pair."""
    lag = find_lag(detected, ref, max_lag)
    d_shifted = detected - lag
    tp = count_matches(d_shifted, ref, jitter)
    fp = detected.size - tp
    fn = ref.size - tp
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    sens = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    roa = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else 0.0
    return tp, prec, sens, roa, lag


# ── Candidate scoring + assignment policies ──────────────────────────────────


def score_pairs(
    detected: list[np.ndarray],
    reference: list[np.ndarray],
    *,
    jitter: int,
    max_lag: int,
    ref_indices: list[int] | None = None,
) -> list[Match]:
    """Score every (detected, reference) pair, in detected-then-reference order."""
    if ref_indices is None:
        ref_indices = [i for i, d in enumerate(reference) if d.size > 0]

    candidates: list[Match] = []
    for det_idx, det_spikes in enumerate(detected):
        if det_spikes.size < 2:
            continue
        for ref_idx in ref_indices:
            _tp, prec, sens, roa, lag = compute_metrics(
                det_spikes, reference[ref_idx], jitter, max_lag
            )
            candidates.append((det_idx, ref_idx, prec, sens, roa, lag))
    return candidates


def best_per_detected(candidates: list[Match]) -> list[Match]:
    """Give every detected MU its highest-RoA reference, without exclusivity."""
    best: dict[int, Match] = {}
    for match in candidates:
        det_idx, roa = match[0], match[4]
        current = best.get(det_idx)
        if current is None or roa > current[4]:
            best[det_idx] = match
    return [best[k] for k in sorted(best)]


def greedy_one_to_one(candidates: list[Match], roa_threshold: float) -> list[Match]:
    """Greedily select one-to-one matches from all candidate pairs."""
    ordered = sorted(candidates, key=lambda m: m[4], reverse=True)
    claimed_det: set[int] = set()
    claimed_ref: set[int] = set()
    matches: list[Match] = []
    for det_idx, ref_idx, prec, sens, roa, lag in ordered:
        if det_idx in claimed_det or ref_idx in claimed_ref:
            continue
        claimed_det.add(det_idx)
        claimed_ref.add(ref_idx)
        if roa > roa_threshold:
            matches.append((det_idx, ref_idx, prec, sens, roa, lag))
    matches.sort(key=lambda m: m[0])
    return matches


# ── ROI / signal shaping shared by the decomposition test modules ────────────


def central_roi(data: np.ndarray, fsamp: float, width_sec: float) -> tuple[int, int]:
    """Return the (start, end) sample indices of the central ``width_sec`` window."""
    center = data.shape[1] // 2
    half = int(round(width_sec / 2.0 * fsamp))
    return (center - half, center + half)


def filter_to_roi(distime: list[np.ndarray], roi: tuple[int, int]) -> list[np.ndarray]:
    """Restrict each MU's discharge times to ``[roi_start, roi_end)``."""
    lo, hi = roi
    return [d[(d >= lo) & (d < hi)] for d in distime]


def restrict_reference_to_roi(
    gt_spike_times: list[np.ndarray], roi: tuple[int, int]
) -> list[np.ndarray]:
    """Clip ground-truth spike trains to the ROI, keeping absolute sample time."""
    lo, hi = roi
    return [s[(s >= lo) & (s < hi)] for s in gt_spike_times]


def active_reference_count(gt_spike_times: list[np.ndarray], roi: tuple[int, int]) -> int:
    """Count reference MUs carrying at least one spike inside the ROI."""
    return sum(1 for s in restrict_reference_to_roi(gt_spike_times, roi) if s.size > 0)


def discharge_times(result: dict[str, Any]) -> list[np.ndarray]:
    """Extract the per-MU discharge-time arrays from a ``run_decomposition`` result."""
    return [np.asarray(d) for d in result["signal"]["Dischargetimes"]]


def build_signal(sim: dict[str, Any]) -> SignalImport:
    """Wrap parsed simulation data in a ``SignalImport``, as ``load_signal`` returns."""
    return SignalImport(
        data=sim["data"],
        fsamp=float(sim["fsamp"]),
        gridname=["GR08MM1305"],
        muscle=["simulated"],
        auxiliary=np.zeros((0, sim["data"].shape[1])),
    )
