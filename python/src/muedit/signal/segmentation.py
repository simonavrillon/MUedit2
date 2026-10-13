"""Segmentation of a force target into ramps, plateaus, sinusoids and irregular blocks."""

from __future__ import annotations

import itertools
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks

from muedit.models import FloatArray, IntArray

SegmentKind = Literal["ramp", "plateau", "sinusoid", "irregular"]

_REST, _PLATEAU, _UP, _DOWN, _DYNAMIC = range(5)

# OTB4 feedback tracks name the target "<track> - Original Path" and the
# participant's own trace "<track> - Performed Path".
_TARGET_NAME = re.compile(r"target|reference|requested|original", re.IGNORECASE)
_PERFORMED_NAME = re.compile(r"performed|actual", re.IGNORECASE)


@dataclass(frozen=True)
class SegmentationConfig:
    """Tunable parameters for :func:`segment_target`; levels are fractions of the target's range."""

    rate_hz: float = 100.0
    smooth_s: float = 0.1
    rest_frac: float = 0.05
    plateau_slope: float = 0.02  # range fraction per second
    min_duration_s: float = 0.5
    min_plateau_s: float = 1.0
    turn_prominence: float = 0.02
    min_cycles: float = 2.0
    period_cv: float = 0.15
    amplitude_cv: float = 0.2
    sine_r2: float = 0.9


@dataclass
class TargetSegment:
    """One segment of the target, in EMG samples ``[start, end)``; levels in the target's units."""

    start: int
    end: int
    kind: SegmentKind
    direction: Literal["up", "down"] | None
    level_start: float
    level_end: float
    level_min: float
    level_max: float
    frequency_hz: float | None = None
    amplitude: float | None = None
    offset: float | None = None
    n_cycles: int | None = None
    cycles: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """The segment as a JSON-ready dict."""
        return asdict(self)


def find_target_channel(names: list[str]) -> int | None:
    """Index of the first auxiliary channel named as a target, or ``None``."""
    for idx, name in enumerate(names):
        if _TARGET_NAME.search(name) and not _PERFORMED_NAME.search(name):
            return idx
    return None


def _block_mean(x: FloatArray, step: int) -> FloatArray:
    """``x`` averaged over consecutive blocks of ``step`` samples, the last one partial."""
    n_full = x.size // step
    head = x[: n_full * step].reshape(n_full, step).mean(axis=1)
    if n_full * step == x.size:
        return head
    return np.append(head, x[n_full * step :].mean())


def _runs(labels: IntArray) -> list[list[int]]:
    """``[label, start, end]`` for each run of equal labels."""
    edges = np.flatnonzero(np.diff(labels)) + 1
    starts = np.concatenate([[0], edges])
    ends = np.concatenate([edges, [labels.size]])
    return [[int(labels[s]), int(s), int(e)] for s, e in zip(starts, ends, strict=True)]


def _turning_points(y: FloatArray, prominence: float) -> tuple[IntArray, IntArray]:
    """Alternating peaks and troughs of ``y``: their positions, and +1 (peak) / -1 (trough)."""
    peaks, _ = find_peaks(y, prominence=prominence)
    troughs, _ = find_peaks(-y, prominence=prominence)
    pos = np.concatenate([peaks, troughs])
    sign = np.concatenate([np.ones(peaks.size, int), -np.ones(troughs.size, int)])
    order = np.argsort(pos, kind="stable")
    pos, sign = pos[order], sign[order]
    keep_pos: list[int] = []
    keep_sign: list[int] = []
    for p, s in zip(pos.tolist(), sign.tolist(), strict=True):
        if keep_sign and keep_sign[-1] == s:
            # Two peaks in a row: keep the higher (two troughs: the lower).
            if s * y[p] > s * y[keep_pos[-1]]:
                keep_pos[-1] = p
            continue
        keep_pos.append(p)
        keep_sign.append(s)
    return np.asarray(keep_pos, int), np.asarray(keep_sign, int)


def _longest_run(mask: np.ndarray) -> int:
    """Length of the longest run of ``True`` in ``mask``."""
    if not mask.any():
        return 0
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    return int((edges[1::2] - edges[::2]).max())


def _dynamic_blocks(
    labels: IntArray, turns: IntArray, signs: IntArray, rate: float, cfg: SegmentationConfig
) -> list[tuple[int, int, IntArray, IntArray]]:
    """``(start, end, turns, signs)`` of each chain of reversals long enough to oscillate."""
    plateau_len = cfg.min_plateau_s * rate
    # A short rest does not break a chain: troughs at the target's minimum read as rest.
    rest_len = cfg.min_duration_s * rate
    # An edge stops at a flat stretch longer than the smoothing blurs a turning point over.
    edge_flat = int(round(2 * cfg.smooth_s * rate))
    oscillating = [
        _longest_run(labels[a:b] == _REST) < rest_len
        and _longest_run(labels[a:b] == _PLATEAU) < plateau_len
        for a, b in itertools.pairwise(turns)
    ]
    blocks: list[tuple[int, int, IntArray, IntArray]] = []
    i = 0
    while i < len(oscillating):
        if not oscillating[i]:
            i += 1
            continue
        j = i
        while j < len(oscillating) and oscillating[j]:
            j += 1
        chain = turns[i : j + 1]
        if (chain.size - 1) / 2 >= cfg.min_cycles:
            quarter = int(np.median(np.diff(chain))) // 2
            start = _extend(labels, int(chain[0]), -1, quarter, edge_flat)
            end = _extend(labels, int(chain[-1]), +1, quarter, edge_flat)
            blocks.append((start, end, chain, signs[i : j + 1]))
        i = j
    return blocks


def _extend(labels: IntArray, at: int, way: int, limit: int, max_flat: int) -> int:
    """How far a block edge at ``at`` reaches, up to ``limit`` samples, before a flat stretch."""
    pos, last_moving, flat = at, at, 0
    for _ in range(limit):
        nxt = pos + way
        if nxt < 0 or nxt >= labels.size:
            break
        flat = flat + 1 if labels[nxt] in (_REST, _PLATEAU) else 0
        if flat > max_flat:
            break
        pos = nxt
        if flat == 0:
            last_moving = pos
    # A flat stretch past the last moving sample is the next plateau or rest, not the block.
    edge = last_moving if flat else pos
    return edge if way < 0 else edge + 1


def _fold_short(runs: list[list[int]], min_len: float, fixed: set[int]) -> list[list[int]]:
    """Merge runs shorter than ``min_len`` into their longer neighbour; dynamic runs stay."""
    runs = [r[:] for r in runs]
    while True:
        short = [i for i, (lab, s, e) in enumerate(runs) if lab not in fixed and e - s < min_len]
        if not short or len(runs) == 1:
            return runs
        i = min(short, key=lambda k: runs[k][2] - runs[k][1])
        neighbours = [k for k in (i - 1, i + 1) if 0 <= k < len(runs)]
        into = max(neighbours, key=lambda k: runs[k][2] - runs[k][1])
        lo, hi = min(i, into), max(i, into)
        merged = [runs[into][0], runs[lo][1], runs[hi][2]]
        runs[lo : hi + 1] = [merged]
        runs = _merge_equal(runs, fixed)


def _merge_equal(runs: list[list[int]], fixed: set[int]) -> list[list[int]]:
    """Join neighbouring runs of one label, except dynamic blocks, which stay apart."""
    out: list[list[int]] = []
    for run in runs:
        if out and out[-1][0] == run[0] and run[0] not in fixed:
            out[-1][2] = run[2]
        else:
            out.append(run)
    return out


def _sine_fit(y: FloatArray, rate: float, freq: float) -> tuple[float, float, float]:
    """Least-squares ``offset + amplitude * sin(...)`` at ``freq``: offset, amplitude and R²."""
    t = np.arange(y.size) / rate
    basis = np.column_stack(
        [np.ones_like(t), np.sin(2 * np.pi * freq * t), np.cos(2 * np.pi * freq * t)]
    )
    coef, *_ = np.linalg.lstsq(basis, y, rcond=None)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    ss_res = float(((y - basis @ coef) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(coef[0]), float(np.hypot(coef[1], coef[2])), r2


def _cv(values: FloatArray) -> float:
    """Coefficient of variation; infinite for fewer than two values or a zero mean."""
    if values.size < 2 or values.mean() == 0:
        return float("inf")
    return float(values.std() / values.mean())


def segment_target(
    target: FloatArray, fsamp: float, config: SegmentationConfig | None = None
) -> list[TargetSegment]:
    """Split a target trace into ramps, plateaus, sinusoids and irregular blocks; rest is left out."""
    cfg = config or SegmentationConfig()
    x = np.asarray(target, dtype=np.float64).ravel()
    n_samples = x.size
    if n_samples == 0 or fsamp <= 0 or not np.isfinite(x).all():
        return []
    step = max(1, int(round(fsamp / cfg.rate_hz)))
    rate = fsamp / step
    xd = _block_mean(x, step)
    lo, hi = np.percentile(xd, [1, 99])
    span = float(hi - lo)
    if span <= 1e-9 * max(1.0, abs(float(hi))):
        return []
    width = max(1, int(round(cfg.smooth_s * rate)))
    smooth = uniform_filter1d(xd, width, mode="nearest")
    y = (smooth - lo) / span
    # Central difference across the smoothing width: a one-sample gradient turns noise into slope.
    padded = np.pad(y, width, mode="edge")
    slope = (padded[2 * width :] - padded[: -2 * width]) * rate / (2 * width)

    labels = np.where(slope > 0, _UP, _DOWN)
    flat = np.abs(slope) < cfg.plateau_slope
    labels[flat] = _PLATEAU
    labels[flat & (y < cfg.rest_frac)] = _REST

    turns, signs = _turning_points(y, cfg.turn_prominence)
    blocks = _dynamic_blocks(labels, turns, signs, rate, cfg)
    for start, end, _, _ in blocks:
        labels[start:end] = _DYNAMIC
    runs = _fold_short(_runs(labels), cfg.min_duration_s * rate, {_DYNAMIC})

    segments: list[TargetSegment] = []
    for lab, s, e in runs:
        if lab == _REST:
            continue
        piece = smooth[s:e]
        common: dict[str, Any] = {
            "start": min(n_samples, s * step),
            "end": min(n_samples, e * step),
            "level_start": float(piece[0]),
            "level_end": float(piece[-1]),
            "level_min": float(piece.min()),
            "level_max": float(piece.max()),
        }
        if lab == _PLATEAU:
            segments.append(TargetSegment(kind="plateau", direction=None, **common))
        elif lab in (_UP, _DOWN):
            direction: Literal["up", "down"] = "up" if lab == _UP else "down"
            segments.append(TargetSegment(kind="ramp", direction=direction, **common))
        else:
            # Chains are split by flat stretches no fold can absorb: one block per run.
            block = next(b for b in blocks if s <= b[0] < e)
            segments.append(_dynamic_segment(smooth, block, rate, cfg, common, step))
    return segments


def _dynamic_segment(
    smooth: FloatArray,
    block: tuple[int, int, IntArray, IntArray],
    rate: float,
    cfg: SegmentationConfig,
    common: dict[str, Any],
    step: int,
) -> TargetSegment:
    """A dynamic block as a sinusoid when it repeats like one, otherwise as irregular."""
    start, end, turns, signs = block
    common["cycles"] = [int(t) * step for t in turns[signs < 0]]
    # Fit over the block, not the run: a short ramp folded into the run would drag R² down.
    piece = smooth[start:end]
    turns = turns - start
    periods = np.concatenate([np.diff(turns[signs > 0]), np.diff(turns[signs < 0])]).astype(float)
    if periods.size:
        # The median period resists a stray turn but is quantized to whole samples;
        # the chain spans exactly turns.size - 1 half periods. Keep the better fit.
        freqs = [
            rate / float(np.median(periods)),
            rate * (turns.size - 1) / (2.0 * float(turns[-1] - turns[0])),
        ]
        freq, (offset, amplitude, r2) = max(
            ((f, _sine_fit(piece, rate, f)) for f in freqs), key=lambda fit: fit[1][2]
        )
        regular = (
            _cv(periods) < cfg.period_cv and _cv(np.abs(np.diff(piece[turns]))) < cfg.amplitude_cv
        )
        if regular and r2 >= cfg.sine_r2:
            return TargetSegment(
                kind="sinusoid",
                direction=None,
                frequency_hz=freq,
                amplitude=amplitude,
                offset=offset,
                n_cycles=int(round((turns.size - 1) / 2)),
                **common,
            )
    return TargetSegment(kind="irregular", direction=None, **common)
