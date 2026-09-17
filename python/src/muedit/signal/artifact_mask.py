"""Automatic artifact-mask detection for HD-EMG signals."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import binary_closing, binary_dilation, maximum_filter1d, median_filter

logger = logging.getLogger(__name__)

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


def _baselines(
    x: np.ndarray,
    fsamp: float,
    cfg: ArtifactMaskConfig,
    cols: np.ndarray | None,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield ``(median, sigma)`` for each baseline, broadcastable to ``x[..., cols]``."""
    med = np.median(x, axis=-1, keepdims=True)
    sigma = _MAD_TO_STD * np.median(np.abs(x - med), axis=-1, keepdims=True)
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
    x: np.ndarray,
    fsamp: float,
    cfg: ArtifactMaskConfig,
    z_thr: float,
    amp_ratio: float | None,
    cols: np.ndarray | None = None,
) -> np.ndarray:
    """Flag ``x[..., cols]`` values anomalous against every baseline of ``x``."""
    xs = x if cols is None else x[..., cols]
    out = np.ones(xs.shape, dtype=bool)
    for med, sigma in _baselines(x, fsamp, cfg, cols):
        hit = (xs - med) / (sigma + 1e-12) > z_thr
        if amp_ratio is not None:
            hit |= xs > amp_ratio * (med + 1e-12)
        out &= hit
    return out


def _detect_artifact_mask(
    data: np.ndarray,
    fsamp: float,
    config: ArtifactMaskConfig | None = None,
) -> np.ndarray:
    """Detect a boolean artifact mask for one grid's filtered signal."""
    cfg = config or ArtifactMaskConfig()
    n_samples = data.shape[1] if data.ndim == 2 else 0
    if data.size == 0 or n_samples == 0:
        return np.zeros(max(n_samples, 0), dtype=bool)

    ch_abs = np.abs(data.astype(np.float32))
    win = max(1, int(round(fsamp * cfg.win_ms / 1000.0)))

    ch_win = maximum_filter1d(ch_abs, size=win, axis=1, mode="nearest")
    n_channels = ch_win.shape[0]
    k = min(max(cfg.min_channels, 1), n_channels)

    win_stat = np.partition(ch_win, n_channels - k, axis=0)[n_channels - k]
    candidate = _exceeds(win_stat, fsamp, cfg, cfg.z_thr, cfg.amp_ratio)
    if not candidate.any():
        return np.zeros(n_samples, dtype=bool)

    cols = np.flatnonzero(candidate)
    n_excited = _exceeds(ch_win, fsamp, cfg, cfg.ch_z_thr, None, cols).sum(axis=0)
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
    data: np.ndarray,
    fsamp: float,
    grid_channel_counts: list[int],
    config: ArtifactMaskConfig | None = None,
) -> tuple[list[np.ndarray], np.ndarray]:
    """Detect artifact masks per grid and return per-grid + global masks."""
    n_samples = data.shape[1]
    per_grid_masks: list[np.ndarray] = []
    global_mask = np.zeros(n_samples, dtype=bool)

    ch_idx = 0
    for grid_idx, n_ch in enumerate(grid_channel_counts):
        grid_data = data[ch_idx : ch_idx + n_ch, :]
        mask = _detect_artifact_mask(grid_data, fsamp, config)
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
