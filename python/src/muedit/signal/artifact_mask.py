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
    size = (1,) * (xd.ndim - 1) + (max(3, int(round(cfg.local_baseline_s * fsamp / step)) | 1),)
    med_d = median_filter(xd, size=size, mode="nearest")
    mad_d = median_filter(np.abs(xd - med_d), size=size, mode="nearest")
    idx = np.arange(n_samples) if cols is None else cols
    idx = np.minimum(idx // step, xd.shape[-1] - 1)
    med = med_d[..., idx]
    yield med, np.maximum(_MAD_TO_STD * mad_d[..., idx], _SIGMA_FLOOR_FRAC * med)


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
        win_stat[start:stop] = np.partition(block, n_channels - k, axis=0)[n_channels - k]
    return ch_win, win_stat


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
