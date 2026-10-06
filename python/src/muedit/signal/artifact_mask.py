"""Automatic artifact-mask detection for HD-EMG signals."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import binary_closing, binary_dilation, maximum_filter1d, median_filter

from muedit.io.store import ArrayStore, RamStore, sample_blocks
from muedit.models import BoolArray, FloatArray, IntArray

logger = logging.getLogger(__name__)

#: The channels of one grid: a block of rows, or the indices of its kept channels.
Rows = slice | IntArray

_MAD_TO_STD: float = 1.4826
_SIGMA_FLOOR_FRAC: float = 0.20
_LOCAL_STEP_MS: int = 25
_RUNNING_TOP_MAX_K: int = 16


@dataclass
class ArtifactMaskConfig:
    """Tunable parameters for :func:`_detect_artifact_mask`."""

    win_ms: int = 20
    z_thr: float = 9.0
    amp_ratio: float = 8.0
    ch_z_thr: float = 3.0
    min_channels: int = 5
    local_baseline_s: float | None = 2.0
    pad_ms: int = 25
    min_gap_ms: int = 20
    extend_ratio: float = 3.0
    extend_hold_frac: float = 0.3
    max_extend_s: float = 5.0


def _median_and_mad(x: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Median and median absolute deviation along the last axis (kept), one row at a time."""
    if x.ndim == 1:
        med = np.median(x, axis=-1, keepdims=True)
        return med, np.median(np.abs(x - med), axis=-1, keepdims=True)
    rows = [np.asarray(row) for row in x]
    med = np.stack([np.median(row, keepdims=True) for row in rows])
    mad = np.stack(
        [np.median(np.abs(row - m), keepdims=True) for row, m in zip(rows, med, strict=True)]
    )
    return med, mad


def _baselines(
    x: FloatArray,
    fsamp: float,
    cfg: ArtifactMaskConfig,
    cols: IntArray | None,
) -> Iterator[tuple[FloatArray, FloatArray]]:
    """Yield ``(median, sigma)`` for each baseline, broadcastable to ``x[..., cols]``."""
    med, mad = _median_and_mad(x)
    sigma = _MAD_TO_STD * mad
    yield med, np.maximum(sigma, _SIGMA_FLOOR_FRAC * med)

    if cfg.local_baseline_s is None:
        return
    n_samples = x.shape[-1]
    step = max(1, int(round(fsamp * _LOCAL_STEP_MS / 1000.0)))
    xd = x[..., ::step]
    width = max(3, int(round(cfg.local_baseline_s * fsamp / step)) | 1)
    if cols is None:
        med_d, mad_d = _moving_median_and_mad(xd, width)
        idx = np.minimum(np.arange(n_samples) // step, xd.shape[-1] - 1)
        med, mad = med_d[..., idx], mad_d[..., idx]
    else:
        med, mad = _moving_median_and_mad_at(xd, width, np.minimum(cols // step, xd.shape[-1] - 1))
    yield med, np.maximum(_MAD_TO_STD * mad, _SIGMA_FLOOR_FRAC * med)


def _moving_median_and_mad(x: FloatArray, width: int) -> tuple[FloatArray, FloatArray]:
    """Moving median of ``x`` over ``width`` samples, and the moving median of its deviations."""
    size = (1,) * (x.ndim - 1) + (width,)
    med = median_filter(x, size=size, mode="nearest")
    return med, median_filter(np.abs(x - med), size=size, mode="nearest")


def _moving_median_and_mad_at(
    x: FloatArray, width: int, idx: IntArray
) -> tuple[FloatArray, FloatArray]:
    """:func:`_moving_median_and_mad` at the columns ``idx`` only, from spans around them.

    Each span reaches ``2 * (width // 2)`` columns past the ones it serves: the deviations'
    median needs the median that far out, so the values match the whole-row filter.
    """
    reach = 2 * (width // 2)
    cols, inverse = np.unique(idx, return_inverse=True)
    med = np.empty((*x.shape[:-1], cols.size), dtype=x.dtype)
    mad = np.empty_like(med)
    breaks = np.flatnonzero(np.diff(cols) > 2 * reach) + 1
    for group in np.split(np.arange(cols.size), breaks):
        lo = max(0, int(cols[group[0]]) - reach)
        hi = min(x.shape[-1], int(cols[group[-1]]) + reach + 1)
        span_med, span_mad = _moving_median_and_mad(x[..., lo:hi], width)
        med[..., group] = span_med[..., cols[group] - lo]
        mad[..., group] = span_mad[..., cols[group] - lo]
    return med[..., inverse], mad[..., inverse]


def _exceeds(
    x: FloatArray,
    fsamp: float,
    cfg: ArtifactMaskConfig,
    z_thr: float,
    amp_ratio: float | None,
    cols: IntArray | None = None,
) -> BoolArray:
    """Flag ``x[..., cols]`` values anomalous against every baseline of ``x``."""
    xs = x if cols is None else x[..., cols]
    out = np.ones(xs.shape, dtype=bool)
    for med, sigma in _baselines(x, fsamp, cfg, cols):
        hit = (xs - med) / (sigma + 1e-12) > z_thr
        if amp_ratio is not None:
            hit |= xs > amp_ratio * (med + 1e-12)
        out &= hit
    return out


def _window_maxima(
    data: FloatArray, rows: Rows, win: int, k: int, store: ArrayStore
) -> tuple[FloatArray, FloatArray]:
    """Per-channel moving maximum of ``|data[rows]|`` and its ``k``-th largest value per sample.

    Computed over blocks of samples, each read with ``win`` samples of overlap on both sides
    so the moving maximum inside the block is the one over the whole row.
    """
    n_samples = data.shape[1]
    n_channels = len(range(data.shape[0])[rows]) if isinstance(rows, slice) else len(rows)
    ch_win = store.allocate("artifact-windows", (n_channels, n_samples), np.float32)
    win_stat = np.empty(n_samples, dtype=np.float32)
    for start, stop in sample_blocks(n_samples, n_channels, np.dtype(np.float32).itemsize):
        lo, hi = max(0, start - win), min(n_samples, stop + win)
        ch_abs = np.abs(np.asarray(data[rows, lo:hi]).astype(np.float32))
        block = maximum_filter1d(ch_abs, size=win, axis=1, mode="nearest")[
            :, start - lo : stop - lo
        ]
        ch_win[:, start:stop] = block
        win_stat[start:stop] = _kth_largest(block, k)
    return ch_win, win_stat


def _kth_largest(block: FloatArray, k: int) -> FloatArray:
    """The ``k``-th largest value of each column of ``block``."""
    if k > _RUNNING_TOP_MAX_K:
        return np.partition(block, block.shape[0] - k, axis=0)[block.shape[0] - k]
    # A running top-k, one row at a time: far cheaper than partitioning down each column.
    top = np.full((k, block.shape[1]), -np.inf, dtype=block.dtype)
    carry = np.empty(block.shape[1], dtype=block.dtype)
    larger = np.empty_like(carry)
    for row in block:
        carry[:] = row
        for j in range(k):
            np.maximum(top[j], carry, out=larger)
            np.minimum(top[j], carry, out=carry)
            top[j] = larger
    return top[k - 1]


def _extend_runs(
    confirmed: BoolArray, win_stat: FloatArray, fsamp: float, cfg: ArtifactMaskConfig, bridge: int
) -> BoolArray:
    """Grow each run of ``confirmed`` over the samples of the artifact it starts or ends.

    A run grows, by ``max_extend_s`` at most each way, while ``win_stat`` stays anomalous
    against its global baseline, above ``extend_ratio`` times its level beside the run and
    above ``extend_hold_frac`` of its level inside the run. So an artifact longer than the
    local baseline is masked whole rather than at its edges, while EMG well below the
    artifact that starts or ends it is not. Dips shorter than ``bridge`` are crossed.
    """
    max_extend = int(round(cfg.max_extend_s * fsamp))
    if max_extend <= 0 or cfg.local_baseline_s is None:
        return confirmed
    n_samples = win_stat.size
    level = maximum_filter1d(win_stat, size=bridge, mode="nearest")
    med, mad = (float(v[0]) for v in _median_and_mad(win_stat))
    sigma = max(_MAD_TO_STD * mad, _SIGMA_FLOOR_FRAC * med)
    floor = min(med + cfg.z_thr * sigma, cfg.amp_ratio * med)
    reach = int(round(cfg.local_baseline_s * fsamp))

    def beside(lo: int, hi: int) -> float:
        quiet = win_stat[lo:hi][~confirmed[lo:hi]]
        return float(np.median(quiet)) if quiet.size else med

    out = confirmed.copy()
    for run_start, run_end in mask_to_intervals(confirmed).tolist():
        hold = max(floor, cfg.extend_hold_frac * float(np.median(level[run_start:run_end])))
        threshold = max(hold, cfg.extend_ratio * beside(max(0, run_start - reach), run_start))
        ahead = level[run_end : min(n_samples, run_end + max_extend)]
        below = np.flatnonzero(ahead < threshold)
        end = run_end + (int(below[0]) if below.size else ahead.size)

        threshold = max(hold, cfg.extend_ratio * beside(end, min(n_samples, end + reach)))
        behind = level[max(0, run_start - max_extend) : run_start][::-1]
        below = np.flatnonzero(behind < threshold)
        start = run_start - (int(below[0]) if below.size else behind.size)
        out[start:end] = True
    return out


def _detect_artifact_mask(
    data: FloatArray,
    fsamp: float,
    config: ArtifactMaskConfig | None = None,
    rows: Rows | None = None,
    store: ArrayStore | None = None,
) -> BoolArray:
    """Detect a boolean artifact mask for one grid's filtered signal, ``data[rows]``.

    The per-channel window maxima (one grid-size float32 array) go into ``store`` when one
    is given.
    """
    cfg = config or ArtifactMaskConfig()
    rows = slice(None) if rows is None else rows
    n_samples = data.shape[1] if data.ndim == 2 else 0
    n_channels = len(range(data.shape[0])[rows]) if isinstance(rows, slice) else len(rows)
    if n_channels == 0 or n_samples == 0:
        return np.zeros(max(n_samples, 0), dtype=bool)

    win = max(1, int(round(fsamp * cfg.win_ms / 1000.0)))
    k = min(max(cfg.min_channels, 1), n_channels)
    store = store if store is not None else RamStore()
    ch_win, win_stat = _window_maxima(data, rows, win, k, store)
    try:
        candidate = _exceeds(win_stat, fsamp, cfg, cfg.z_thr, cfg.amp_ratio)
        if not candidate.any():
            return np.zeros(n_samples, dtype=bool)
        cols = np.flatnonzero(candidate)
        n_excited = _exceeds(ch_win, fsamp, cfg, cfg.ch_z_thr, None, cols).sum(axis=0)
    finally:
        store.discard(ch_win)
    confirmed = np.zeros(n_samples, dtype=bool)
    confirmed[cols[n_excited >= k]] = True
    if not confirmed.any():
        return np.zeros(n_samples, dtype=bool)

    bridge = max(1, int(round(fsamp * cfg.min_gap_ms / 1000.0)))
    pad = max(1, int(round(fsamp * cfg.pad_ms / 1000.0)))
    confirmed = binary_closing(confirmed, structure=np.ones(bridge, dtype=bool))
    confirmed = _extend_runs(confirmed, win_stat, fsamp, cfg, bridge)
    mask = binary_dilation(confirmed, structure=np.ones(pad, dtype=bool))

    logger.debug(
        "Artifact mask: %d / %d samples (%.2f%%)",
        int(mask.sum()),
        n_samples,
        100.0 * mask.sum() / n_samples,
    )
    return mask.astype(bool)


def detect_artifact_masks(
    data: FloatArray,
    fsamp: float,
    grid_channel_counts: list[int],
    config: ArtifactMaskConfig | None = None,
    grid_rows: list[IntArray] | None = None,
    store: ArrayStore | None = None,
) -> tuple[list[BoolArray], BoolArray]:
    """Detect artifact masks per grid and return per-grid + global masks.

    Grid ``i`` is ``data[grid_rows[i]]`` when rows are given, else the next
    ``grid_channel_counts[i]`` rows.
    """
    n_samples = data.shape[1]
    per_grid_masks: list[BoolArray] = []
    global_mask = np.zeros(n_samples, dtype=bool)

    ch_idx = 0
    for grid_idx, n_ch in enumerate(grid_channel_counts):
        rows = slice(ch_idx, ch_idx + n_ch) if grid_rows is None else grid_rows[grid_idx]
        mask = _detect_artifact_mask(data, fsamp, config, rows, store)
        per_grid_masks.append(mask)
        global_mask |= mask
        ch_idx += n_ch

        if mask.any():
            logger.info(
                "Grid %d: %d / %d samples masked (%.2f%%)",
                grid_idx,
                int(mask.sum()),
                n_samples,
                100.0 * mask.sum() / n_samples,
            )

    total = int(global_mask.sum())
    if total:
        logger.info(
            "Global artifact mask: %d / %d samples (%.2f%%)",
            total,
            n_samples,
            100.0 * total / n_samples,
        )

    return per_grid_masks, global_mask


def mask_to_intervals(mask: BoolArray | None) -> IntArray:
    """The ``[start, end)`` runs of ``True`` in a sample mask, as sorted ``int64[k, 2]``."""
    if mask is None or not mask.any():
        return np.zeros((0, 2), dtype=np.int64)
    diff = np.diff(mask.astype(np.int8), prepend=0, append=0)
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    return np.stack([starts, ends], axis=1).astype(np.int64)


def intervals_to_mask(intervals: IntArray, n_samples: int) -> BoolArray:
    """A boolean sample mask that is ``True`` inside each ``[start, end)`` interval."""
    mask = np.zeros(n_samples, dtype=bool)
    for start, end in np.asarray(intervals, dtype=np.int64).reshape(-1, 2):
        mask[max(0, int(start)) : max(0, int(end))] = True
    return mask
