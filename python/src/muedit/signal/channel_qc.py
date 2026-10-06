"""Automatic bad-channel detection for HD-EMG electrode grids."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter1d

from muedit.models import BoolArray, FloatArray, IntArray

logger = logging.getLogger(__name__)

# Samples per block where a whole-grid temporary would otherwise be made.
_BLOCK_SAMPLES = 65536


@dataclass
class ChannelQCConfig:
    """Tunable parameters for :func:`_detect_bad_channels`."""

    flat_rms_ratio: float = 0.20
    flat_abs_floor: float = 1e-8
    sat_extreme_frac: float = 0.005
    sat_tol_frac: float = 0.001
    neighbor_dist: float = 1.5
    noisy_corr_threshold: float = 0.30
    noisy_abs_floor: float = 1e-7
    snr_win_ms: int = 500
    snr_thr: float = 7.0
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

    mask: BoolArray
    rms: FloatArray
    snr: FloatArray
    sat_frac: FloatArray
    mean_neighbor_corr: FloatArray
    max_win_ratio: FloatArray
    max_loss_run: IntArray
    reasons: list[str]


def _detect_bad_channels(
    data: FloatArray,
    fsamp: float,
    coordinates: FloatArray | None = None,
    config: ChannelQCConfig | None = None,
) -> BoolArray:
    """Detect bad channels in one grid's filtered signal."""
    return _channel_qc_diagnostics(data, fsamp, coordinates, config).mask


def _channel_qc_diagnostics(
    data: FloatArray,
    fsamp: float,
    coordinates: FloatArray | None = None,
    config: ChannelQCConfig | None = None,
    *,
    structural_only: bool = False,
) -> ChannelQCMetrics:
    """Per-channel QC metrics and bad-channel mask for one grid; ``structural_only`` stops after the flat and saturated checks."""
    cfg = config or ChannelQCConfig()
    n_ch = data.shape[0] if data.ndim == 2 else 0
    if data.size == 0 or n_ch == 0:
        return ChannelQCMetrics(
            mask=np.zeros(0, dtype=bool),
            rms=np.zeros(0),
            snr=np.zeros(0),
            sat_frac=np.zeros(0),
            mean_neighbor_corr=np.ones(0),
            max_win_ratio=np.zeros(0),
            max_loss_run=np.zeros(0, dtype=int),
            reasons=[],
        )

    x = np.asarray(data, dtype=np.float32)
    n_samples = x.shape[1]

    rms = np.sqrt([np.mean(np.square(row), dtype=np.float64) for row in x])
    med_rms = float(np.median(rms))

    flat = np.zeros(n_ch, dtype=bool)
    if med_rms >= cfg.flat_abs_floor:
        flat = rms < cfg.flat_rms_ratio * med_rms

    ch_min = x.min(axis=1)
    ch_max = x.max(axis=1)
    ch_range = ch_max - ch_min
    nonzero_range = ch_range > 1e-15
    tol = np.where(nonzero_range, cfg.sat_tol_frac * ch_range, np.inf)
    n_at_extreme = np.array(
        [
            np.count_nonzero(row >= hi - t) + np.count_nonzero(row <= lo + t)
            for row, lo, hi, t in zip(x, ch_min, ch_max, tol, strict=True)
        ]
    )
    sat_frac = n_at_extreme / max(n_samples, 1)
    saturated = nonzero_range & (sat_frac > cfg.sat_extreme_frac)

    flags = {"flat": flat, "saturated": saturated}
    mean_corr: FloatArray = np.ones(n_ch)
    snr = np.full(n_ch, np.nan)
    max_win_ratio = np.zeros(n_ch)
    max_loss_run = np.zeros(n_ch, dtype=int)

    if not structural_only:
        noisy = np.zeros(n_ch, dtype=bool)
        can_check_noisy = coordinates is not None and n_ch > 1 and med_rms >= cfg.noisy_abs_floor
        if can_check_noisy and coordinates is not None:
            mean_corr = _mean_neighbor_correlation(x, coordinates, cfg.neighbor_dist)
            noisy = mean_corr < cfg.noisy_corr_threshold

        low_snr = np.zeros(n_ch, dtype=bool)
        can_check_snr = can_check_noisy and n_samples >= cfg.snr_min_windows * int(
            fsamp * cfg.snr_win_ms / 1000
        )
        if can_check_snr:
            snr = _estimate_snr(x, fsamp, cfg.snr_win_ms)
            low_snr = (snr < cfg.snr_thr) & (mean_corr < cfg.low_snr_corr_thr)

        win_inst = max(1, int(round(fsamp * cfg.instability_win_ms / 1000.0)))
        ch_win = np.empty_like(x)
        for c in range(n_ch):
            ch_win[c] = uniform_filter1d(np.abs(x[c]), size=win_inst, mode="nearest")
        ch_win_median = np.array([np.median(row) for row in ch_win])
        ch_win_max = ch_win.max(axis=1)
        max_win_ratio = ch_win_max / np.maximum(ch_win_median, 1e-15)

        has_signal = ch_win_median > 1e-15
        intermittent = has_signal & (max_win_ratio > cfg.intermittent_amp_ratio)

        contact_loss = np.zeros(n_ch, dtype=bool)
        grid_env = np.empty(n_samples, dtype=np.float32)
        for lo in range(0, n_samples, _BLOCK_SAMPLES):
            # Transposed first: a median along rows is faster than one down columns.
            grid_env[lo : lo + _BLOCK_SAMPLES] = np.median(
                np.ascontiguousarray(ch_win[:, lo : lo + _BLOCK_SAMPLES].T), axis=1
            )
        grid_env_med = float(np.median(grid_env))
        grid_active = grid_env > max(1e-6, 0.20 * grid_env_med)
        grid_peak = float(grid_env.max())

        if grid_active.any() and grid_peak > 1e-15:
            loss_min_samples = max(
                1,
                int(round(fsamp * cfg.contact_loss_min_run_ms / 1000.0)),
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

        flags |= {
            "noisy": noisy,
            "low-SNR": low_snr,
            "intermittent": intermittent,
            "contact-loss": contact_loss,
        }

    mask = np.logical_or.reduce(list(flags.values()))
    reasons = [
        ", ".join(name for name, flagged in flags.items() if flagged[i]) for i in range(n_ch)
    ]

    n_bad = int(mask.sum())
    if n_bad > 0:
        logger.info(
            "Channel QC: %d / %d channels flagged (%s)",
            n_bad,
            n_ch,
            ", ".join(f"{name}={int(flagged.sum())}" for name, flagged in flags.items()),
        )
    if n_ch > 0 and n_bad / n_ch > cfg.max_bad_fraction:
        logger.warning(
            "Channel QC: %d / %d channels (%.0f%%) flagged — check thresholds "
            "or recording quality.",
            n_bad,
            n_ch,
            100.0 * n_bad / n_ch,
        )

    return ChannelQCMetrics(
        mask=mask,
        rms=rms,
        snr=snr,
        sat_frac=sat_frac,
        mean_neighbor_corr=mean_corr,
        max_win_ratio=max_win_ratio,
        max_loss_run=max_loss_run,
        reasons=reasons,
    )


def _estimate_snr(
    data: FloatArray,
    fsamp: float,
    win_ms: int,
) -> FloatArray:
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
        windows = data[ch, : n_windows * win_samples].reshape(n_windows, win_samples)
        win_powers = np.mean(np.square(windows), axis=1, dtype=np.float64)
        sorted_pow = np.sort(win_powers)
        noise_power = float(np.median(sorted_pow[:n_rest]))
        signal_power = float(np.median(sorted_pow[-n_act:]))
        if noise_power > 1e-30:
            snr[ch] = 10.0 * np.log10(signal_power / noise_power)

    return snr


def _mean_neighbor_correlation(
    data: FloatArray,
    coordinates: FloatArray,
    neighbor_dist: float,
) -> FloatArray:
    """Mean Pearson correlation of each channel with its spatial neighbours."""
    n_ch = data.shape[0]
    corr = _safe_correlation(data)
    mean_corr = np.ones(n_ch)

    for i in range(n_ch):
        diff = coordinates - coordinates[i]
        dist = np.sqrt((diff**2).sum(axis=1))
        neigh = np.where((dist > 0) & (dist <= neighbor_dist))[0]
        if neigh.size > 0:
            vals = corr[i, neigh]
            vals = vals[np.isfinite(vals)]
            if vals.size > 0:
                mean_corr[i] = float(np.mean(vals))

    return mean_corr


def _safe_correlation(data: FloatArray) -> FloatArray:
    """Pearson correlation matrix with NaN-safe handling, accumulated in float64 a block at a time."""
    n_ch, n_samples = data.shape
    mean = data.mean(axis=1, dtype=np.float64)
    gram = np.zeros((n_ch, n_ch))
    for lo in range(0, n_samples, _BLOCK_SAMPLES):
        block = data[:, lo : lo + _BLOCK_SAMPLES].astype(np.float64) - mean[:, None]
        gram += block @ block.T
    scale = np.sqrt(np.diag(gram))
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.clip(gram / np.outer(scale, scale), -1.0, 1.0)
    corr = np.asarray(np.nan_to_num(corr, nan=0.0, posinf=0.0, neginf=0.0))
    flat_std = scale / np.sqrt(max(n_samples, 1)) < 1e-15
    if flat_std.any():
        corr[flat_std, :] = 0.0
        corr[:, flat_std] = 0.0
    return corr


def detect_bad_channels_per_grid(
    data: FloatArray,
    fsamp: float,
    grid_channel_counts: list[int],
    grid_coordinates: list[FloatArray] | None = None,
    config: ChannelQCConfig | None = None,
    *,
    keep: BoolArray | None = None,
    structural_only: bool = False,
) -> list[BoolArray]:
    """Detect bad channels per grid, from the samples ``keep`` marks (all of them when it marks none)."""
    if keep is not None and (keep.all() or not keep.any()):
        keep = None
    per_grid_masks: list[BoolArray] = []
    ch_idx = 0
    for grid_idx, n_ch in enumerate(grid_channel_counts):
        grid_data = data[ch_idx : ch_idx + n_ch, :]
        if keep is not None:
            # Row by row: indexing the columns of the block would return a Fortran-ordered copy.
            kept = np.empty((n_ch, int(keep.sum())), dtype=np.float32)
            for r in range(n_ch):
                kept[r] = grid_data[r][keep]
            grid_data = kept
        coords = None
        if grid_coordinates is not None and grid_idx < len(grid_coordinates):
            coords = grid_coordinates[grid_idx]
        mask = _channel_qc_diagnostics(
            grid_data, fsamp, coords, config, structural_only=structural_only
        ).mask
        per_grid_masks.append(mask)
        ch_idx += n_ch

        if mask.any():
            logger.info(
                "Grid %d: %d / %d channels flagged",
                grid_idx,
                int(mask.sum()),
                n_ch,
            )

    return per_grid_masks
