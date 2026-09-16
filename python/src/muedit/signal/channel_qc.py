"""Automatic bad-channel detection for HD-EMG electrode grids."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter1d

logger = logging.getLogger(__name__)


@dataclass
class ChannelQCConfig:
    """Tunable parameters for :func:`_detect_bad_channels`."""

    flat_rms_ratio: float = 0.10
    flat_abs_floor: float = 1e-8
    sat_extreme_frac: float = 0.005
    sat_tol_frac: float = 0.001
    quant_max_unique_frac: float = 0.80
    quant_min_samples: int = 1000
    neighbor_dist: float = 1.5
    noisy_corr_threshold: float = 0.10
    noisy_abs_floor: float = 1e-7
    snr_win_ms: int = 500
    snr_thr: float = 5.0
    low_snr_corr_thr: float = 0.50
    snr_min_windows: int = 4
    instability_win_ms: int = 50
    intermittent_amp_ratio: float = 30.0
    contact_loss_frac: float = 0.10
    contact_loss_min_run_ms: int = 1000
    contact_loss_active_frac: float = 0.30
    max_bad_fraction: float = 0.50


@dataclass
class ChannelQCMetrics:
    """Per-channel diagnostic metrics from :func:`_channel_qc_diagnostics`."""

    mask: np.ndarray
    rms: np.ndarray
    snr: np.ndarray
    sat_frac: np.ndarray
    n_unique: np.ndarray
    mean_neighbor_corr: np.ndarray
    max_win_ratio: np.ndarray
    max_loss_run: np.ndarray
    reasons: list[str]


def _detect_bad_channels(
    data: np.ndarray,
    fsamp: float,
    coordinates: np.ndarray | None = None,
    config: ChannelQCConfig | None = None,
) -> np.ndarray:
    """Detect bad channels in one grid's filtered signal."""
    return _channel_qc_diagnostics(data, fsamp, coordinates, config).mask


def _channel_qc_diagnostics(
    data: np.ndarray,
    fsamp: float,
    coordinates: np.ndarray | None = None,
    config: ChannelQCConfig | None = None,
) -> ChannelQCMetrics:
    """Compute per-channel QC metrics and a bad-channel mask for one grid."""
    cfg = config or ChannelQCConfig()
    n_ch = data.shape[0] if data.ndim == 2 else 0
    if data.size == 0 or n_ch == 0:
        return ChannelQCMetrics(
            mask=np.zeros(0, dtype=bool),
            rms=np.zeros(0),
            snr=np.zeros(0),
            sat_frac=np.zeros(0),
            n_unique=np.zeros(0, dtype=int),
            mean_neighbor_corr=np.ones(0),
            max_win_ratio=np.zeros(0),
            max_loss_run=np.zeros(0, dtype=int),
            reasons=[],
        )

    x = data.astype(np.float64)
    n_samples = x.shape[1]

    rms = np.sqrt(np.mean(x ** 2, axis=1))
    med_rms = float(np.median(rms))

    flat = np.zeros(n_ch, dtype=bool)
    if med_rms >= cfg.flat_abs_floor:
        flat = rms < cfg.flat_rms_ratio * med_rms

    ch_min = x.min(axis=1)
    ch_max = x.max(axis=1)
    ch_range = ch_max - ch_min
    nonzero_range = ch_range > 1e-15
    tol = np.where(nonzero_range, cfg.sat_tol_frac * ch_range, np.inf)
    n_at_max = (x >= (ch_max[:, None] - tol[:, None])).sum(axis=1)
    n_at_min = (x <= (ch_min[:, None] + tol[:, None])).sum(axis=1)
    sat_frac = (n_at_max + n_at_min) / max(n_samples, 1)
    saturated = nonzero_range & (sat_frac > cfg.sat_extreme_frac)

    n_unique = np.array([len(np.unique(x[c])) for c in range(n_ch)], dtype=int)
    can_check_quant = n_samples >= cfg.quant_min_samples
    quantized = np.zeros(n_ch, dtype=bool)
    if can_check_quant:
        quantized = (n_unique < cfg.quant_max_unique_frac * n_samples) & nonzero_range

    mean_corr = np.ones(n_ch)
    noisy = np.zeros(n_ch, dtype=bool)
    can_check_noisy = (
        coordinates is not None
        and n_ch > 1
        and med_rms >= cfg.noisy_abs_floor
    )
    if can_check_noisy:
        mean_corr = _mean_neighbor_correlation(x, coordinates, cfg.neighbor_dist)
        noisy = mean_corr < cfg.noisy_corr_threshold

    snr = np.full(n_ch, np.nan)
    low_snr = np.zeros(n_ch, dtype=bool)
    can_check_snr = (
        can_check_noisy
        and n_samples >= cfg.snr_min_windows * int(fsamp * cfg.snr_win_ms / 1000)
    )
    if can_check_snr:
        snr = _estimate_snr(x, fsamp, cfg.snr_win_ms)
        low_snr = (snr < cfg.snr_thr) & (mean_corr < cfg.low_snr_corr_thr)

    win_inst = max(1, int(round(fsamp * cfg.instability_win_ms / 1000.0)))
    ch_abs_inst = np.abs(x.astype(np.float32))
    ch_win = np.stack([
        uniform_filter1d(ch_abs_inst[c], size=win_inst, mode="nearest")
        for c in range(n_ch)
    ])
    ch_win_median = np.median(ch_win, axis=1)
    ch_win_max = ch_win.max(axis=1)
    max_win_ratio = ch_win_max / np.maximum(ch_win_median, 1e-15)

    has_signal = ch_win_median > 1e-15
    intermittent = has_signal & (max_win_ratio > cfg.intermittent_amp_ratio)

    contact_loss = np.zeros(n_ch, dtype=bool)
    max_loss_run = np.zeros(n_ch, dtype=int)
    grid_env = np.median(ch_win, axis=0)
    grid_env_med = float(np.median(grid_env))
    grid_active = grid_env > max(1e-6, 0.20 * grid_env_med)
    grid_peak = float(grid_env.max())

    if grid_active.any() and grid_peak > 1e-15:
        loss_min_samples = max(
            1, int(round(fsamp * cfg.contact_loss_min_run_ms / 1000.0)),
        )
        for c in range(n_ch):
            if ch_win_max[c] < cfg.contact_loss_active_frac * grid_peak:
                continue
            loss_mask = grid_active & (ch_win[c] < cfg.contact_loss_frac * grid_env)
            if not loss_mask.any():
                continue
            diff = np.diff(loss_mask.astype(np.int8), prepend=0, append=0)
            starts = np.where(diff == 1)[0]
            ends = np.where(diff == -1)[0]
            runs = ends - starts
            if runs.size:
                max_loss_run[c] = int(runs.max())
                contact_loss[c] = max_loss_run[c] >= loss_min_samples

    mask = flat | saturated | quantized | noisy | low_snr | intermittent | contact_loss

    reasons: list[str] = []
    for i in range(n_ch):
        r = []
        if flat[i]:
            r.append("flat")
        if saturated[i]:
            r.append("saturated")
        if quantized[i]:
            r.append("quantized")
        if noisy[i]:
            r.append("noisy")
        if low_snr[i]:
            r.append("low-SNR")
        if intermittent[i]:
            r.append("intermittent")
        if contact_loss[i]:
            r.append("contact-loss")
        reasons.append(", ".join(r))

    n_bad = int(mask.sum())
    if n_bad > 0:
        logger.info(
            "Channel QC: %d / %d channels flagged (flat=%d, saturated=%d, "
            "quantized=%d, noisy=%d, low-SNR=%d, intermittent=%d, contact-loss=%d)",
            n_bad, n_ch, int(flat.sum()), int(saturated.sum()),
            int(quantized.sum()), int(noisy.sum()), int(low_snr.sum()),
            int(intermittent.sum()), int(contact_loss.sum()),
        )
    if n_ch > 0 and n_bad / n_ch > cfg.max_bad_fraction:
        logger.warning(
            "Channel QC: %d / %d channels (%.0f%%) flagged — check thresholds "
            "or recording quality.",
            n_bad, n_ch, 100.0 * n_bad / n_ch,
        )

    return ChannelQCMetrics(
        mask=mask,
        rms=rms,
        snr=snr,
        sat_frac=sat_frac,
        n_unique=n_unique,
        mean_neighbor_corr=mean_corr,
        max_win_ratio=max_win_ratio,
        max_loss_run=max_loss_run,
        reasons=reasons,
    )


def _estimate_snr(
    data: np.ndarray,
    fsamp: float,
    win_ms: int,
) -> np.ndarray:
    """Estimate per-channel SNR (dB) via activity-sparse segmentation."""
    n_ch, n_samples = data.shape
    win_samples = int(fsamp * win_ms / 1000)
    n_windows = n_samples // win_samples
    snr = np.full(n_ch, np.nan)

    if n_windows < 4:
        return snr

    n_rest = max(1, int(0.3 * n_windows))
    n_act = max(1, int(0.3 * n_windows))

    for ch in range(n_ch):
        x = data[ch]
        win_powers = np.array([
            np.mean(x[w * win_samples:(w + 1) * win_samples] ** 2)
            for w in range(n_windows)
        ])
        sorted_pow = np.sort(win_powers)
        noise_power = float(np.median(sorted_pow[:n_rest]))
        signal_power = float(np.median(sorted_pow[-n_act:]))
        if noise_power > 1e-30:
            snr[ch] = 10.0 * np.log10(signal_power / noise_power)

    return snr


def _mean_neighbor_correlation(
    data: np.ndarray,
    coordinates: np.ndarray,
    neighbor_dist: float,
) -> np.ndarray:
    """Mean Pearson correlation of each channel with its spatial neighbours."""
    n_ch = data.shape[0]
    corr = _safe_correlation(data)
    mean_corr = np.ones(n_ch)

    for i in range(n_ch):
        diff = coordinates - coordinates[i]
        dist = np.sqrt((diff ** 2).sum(axis=1))
        neigh = np.where((dist > 0) & (dist <= neighbor_dist))[0]
        if neigh.size > 0:
            vals = corr[i, neigh]
            vals = vals[np.isfinite(vals)]
            if vals.size > 0:
                mean_corr[i] = float(np.mean(vals))

    return mean_corr


def _safe_correlation(data: np.ndarray) -> np.ndarray:
    """Pearson correlation matrix with NaN-safe handling."""
    std = data.std(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(data)
    corr = np.nan_to_num(corr, nan=0.0, posinf=0.0, neginf=0.0)
    flat_std = std < 1e-15
    if flat_std.any():
        corr[flat_std, :] = 0.0
        corr[:, flat_std] = 0.0
    return corr


def detect_bad_channels_per_grid(
    data: np.ndarray,
    fsamp: float,
    grid_channel_counts: list[int],
    grid_coordinates: list[np.ndarray] | None = None,
    config: ChannelQCConfig | None = None,
) -> list[np.ndarray]:
    """Detect bad channels per grid."""
    per_grid_masks: list[np.ndarray] = []
    ch_idx = 0
    for grid_idx, n_ch in enumerate(grid_channel_counts):
        grid_data = data[ch_idx : ch_idx + n_ch, :]
        coords = None
        if grid_coordinates is not None and grid_idx < len(grid_coordinates):
            coords = grid_coordinates[grid_idx]
        mask = _detect_bad_channels(grid_data, fsamp, coords, config)
        per_grid_masks.append(mask)
        ch_idx += n_ch

        if mask.any():
            logger.info(
                "Grid %d: %d / %d channels flagged",
                grid_idx, int(mask.sum()), n_ch,
            )

    return per_grid_masks
