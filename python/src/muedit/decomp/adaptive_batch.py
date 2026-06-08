
"""Adaptive post-processing helpers for online-style decomposition batches."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from scipy.signal import find_peaks


def _compute_calibration_stats(
    w_sig: np.ndarray,
    mu_filters: np.ndarray,
    fsamp: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Project the whitened calibration signal through MU filters and derive spike centroids."""
    from scipy.cluster.vq import kmeans2

    n_mu = mu_filters.shape[1]
    ipts_calib = (w_sig.T @ mu_filters).astype(np.float32)
    ipts_sq    = ipts_calib * np.abs(ipts_calib)

    base_centr   = np.zeros(n_mu, dtype=np.float32)
    spikes_centr = np.ones(n_mu, dtype=np.float32)

    dist = int(round(fsamp * 0.005))
    for j in range(n_mu):
        pt = ipts_sq[:, j]
        peaks, _ = find_peaks(pt, distance=dist)
        if len(peaks) > 1:
            centroids, labels = kmeans2(pt[peaks], 2, iter=10, minit="++", missing="raise", seed=0)
            hi = int(np.argmax(centroids))
            spikes_centr[j] = float(centroids[hi])
            base_centr[j]   = float(centroids[1 - hi])
        elif len(peaks) == 1:
            spikes_centr[j] = float(pt[peaks[0]])

    return base_centr, spikes_centr


def _run_one_pass(
    emg_seg: np.ndarray,
    emg_calib: np.ndarray,
    whiten_mat: np.ndarray,
    mu_filters: np.ndarray,
    base_centr: np.ndarray,
    spikes_centr: np.ndarray,
    fsamp: float,
    ex_factor: int,
    batch_ms: int,
    adapt_wh: bool,
    adapt_sv: bool,
    adapt_sd: bool = True,
    wh_learning_rate: float = 7e-3,
    sv_learning_rate: float = 3e-3,
    cov_alpha: float = 0.1,
    spike_prev_weight: int = 5,
    contrast_func: Literal["logcosh", "cube"] = "logcosh",
    compute_loss: bool = False,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Run adaptive decomposition on a single EMG segment and return ipts, spikes, and losses."""
    from muedit.adapt_decomp.adaptation import run_adaptive_decomposition
    from muedit.adapt_decomp.config import Config

    config = Config(
        fsamp=int(fsamp),
        ex_factor=ex_factor,
        batch_ms=batch_ms,
        adapt_wh=adapt_wh,
        adapt_sv=adapt_sv,
        adapt_sd=adapt_sd,
        wh_learning_rate=wh_learning_rate,
        sv_learning_rate=sv_learning_rate,
        cov_alpha=cov_alpha,
        spike_prev_weight=spike_prev_weight,
        contrast_func=contrast_func,
        compute_loss=compute_loss,
    )
    ipts, spikes, losses = run_adaptive_decomposition(
        emg=emg_seg.astype(np.float32),
        whitening=whiten_mat.astype(np.float32),
        sep_vectors=mu_filters.T.astype(np.float32),
        base_centr=base_centr.copy(),
        spikes_centr=spikes_centr.copy(),
        emg_calib=emg_calib.astype(np.float32),
        config=config,
    )
    return ipts.astype(np.float64), spikes, losses


def _run_adapt_decomp_bidirectional(
    grid_data_g: np.ndarray,
    win_data_g: np.ndarray,
    whiten_mat: np.ndarray,
    mu_filters: np.ndarray,
    w_sig: np.ndarray,
    fsamp: float,
    batch_ms: int,
    adapt_wh: bool,
    adapt_sv: bool,
    calib_start: int,
    adapt_sd: bool = True,
    wh_learning_rate: float = 7e-3,
    sv_learning_rate: float = 3e-3,
    cov_alpha: float = 0.1,
    spike_prev_weight: int = 5,
    contrast_func: Literal["logcosh", "cube"] = "logcosh",
    compute_loss: bool = False,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Run adaptive decomposition forward from calib_start and, if needed, backward over the pre-calibration segment."""
    from muedit.decomp.algorithm import extend_signal

    base_centr, spikes_centr = _compute_calibration_stats(
        w_sig, mu_filters, fsamp
    )

    ex_factor = w_sig.shape[0] // win_data_g.shape[0]
    bs = int(batch_ms * fsamp / 1000)

    shared = dict(
        whiten_mat=whiten_mat,
        mu_filters=mu_filters,
        base_centr=base_centr,
        spikes_centr=spikes_centr,
        fsamp=fsamp,
        batch_ms=batch_ms,
        adapt_wh=adapt_wh,
        adapt_sv=adapt_sv,
        adapt_sd=adapt_sd,
        wh_learning_rate=wh_learning_rate,
        sv_learning_rate=sv_learning_rate,
        cov_alpha=cov_alpha,
        spike_prev_weight=spike_prev_weight,
        contrast_func=contrast_func,
        compute_loss=compute_loss,
    )

    emg_calib_raw = win_data_g.T.astype(np.float32)
    n_calib = win_data_g.shape[1]
    emg_calib_ext = extend_signal(win_data_g, ex_factor).T[:n_calib].astype(np.float32)

    fwd_start = max(0, calib_start - bs)
    emg_fwd = np.ascontiguousarray(grid_data_g[:, fwd_start:].T.astype(np.float32))
    ipts_fwd_full, spikes_fwd_full, losses_fwd = _run_one_pass(
        emg_seg=emg_fwd,
        emg_calib=emg_calib_raw,
        ex_factor=ex_factor,
        **shared,
    )
    ipts_fwd   = ipts_fwd_full[calib_start - fwd_start:]
    spikes_fwd = spikes_fwd_full[calib_start - fwd_start:]

    if calib_start == 0:
        return ipts_fwd, spikes_fwd, losses_fwd

    # `extend_signal` adds (ex_factor - 1) trailing padded samples; keep only
    # the original pre-calibration duration to avoid forward/backward offset.
    e_pre = extend_signal(grid_data_g[:, :calib_start], ex_factor).T[:calib_start]

    split_pts = list(range(bs, calib_start, bs))
    blocks = np.split(e_pre, split_pts, axis=0)
    emg_bwd = np.ascontiguousarray(np.concatenate(blocks[::-1], axis=0).astype(np.float32))

    ipts_bwd_rev, spikes_bwd_rev, losses_bwd_rev = _run_one_pass(
        emg_seg=emg_bwd,
        emg_calib=emg_calib_ext,
        ex_factor=1,
        **shared,
    )

    out_ipts   = np.split(ipts_bwd_rev,   split_pts, axis=0)
    out_spikes = np.split(spikes_bwd_rev, split_pts, axis=0)
    ipts_bwd   = np.concatenate(out_ipts[::-1],   axis=0)
    spikes_bwd = np.concatenate(out_spikes[::-1], axis=0)

    losses: dict[str, Any] = {}
    if compute_loss and losses_fwd:
        n_bwd = losses_bwd_rev.get("wh_loss", np.array([])).shape[0]
        bwd_idx = list(range(n_bwd - 1, -1, -1))
        losses = {
            "wh_loss": np.concatenate([
                losses_bwd_rev["wh_loss"][bwd_idx],
                losses_fwd["wh_loss"],
            ]),
            "sv_loss": np.concatenate([
                losses_bwd_rev["sv_loss"][bwd_idx],
                losses_fwd["sv_loss"],
            ], axis=0),
            "total_loss": np.concatenate([
                losses_bwd_rev["total_loss"][bwd_idx],
                losses_fwd["total_loss"],
            ]),
        }

    return (
        np.concatenate([ipts_bwd, ipts_fwd], axis=0),
        np.concatenate([spikes_bwd, spikes_fwd], axis=0),
        losses,
    )


def adaptive_batch_process(
    mu_filters_by_window: dict[int, np.ndarray],
    w_sig_by_window: dict[int, np.ndarray],
    win_data: dict[int, np.ndarray],
    whiten_mats: dict[int, np.ndarray],
    grid_data: dict[int, np.ndarray],
    coordinates: list[int],
    ltime: int,
    fsamp: float,
    nwindows_per_grid: int,
    batch_ms: int = 100,
    adapt_wh: bool = True,
    adapt_sv: bool = True,
    adapt_sd: bool = True,
    wh_learning_rate: float = 7e-3,
    sv_learning_rate: float = 3e-3,
    cov_alpha: float = 0.1,
    spike_prev_weight: int = 5,
    contrast_func: Literal["logcosh", "cube"] = "logcosh",
    compute_loss: bool = False,
) -> tuple[np.ndarray, list[np.ndarray], dict[str, Any]]:
    """Apply adaptive post-processing across all decomposition windows and grids."""
    from muedit.signal.filters import demean

    total_mus = sum(f.shape[1] for f in mu_filters_by_window.values() if f.size > 0)
    if total_mus == 0:
        return np.array([]), [], {}

    pulse_t = np.zeros((total_mus, ltime), dtype=np.float64)
    distime: list[np.ndarray] = []
    mu_nb = 0
    all_losses: dict[int, dict[str, Any]] = {}

    grid_data_demeaned = {i: demean(g) for i, g in grid_data.items()}

    for nwin in sorted(mu_filters_by_window.keys()):
        filters = mu_filters_by_window[nwin]
        if filters.size == 0:
            continue

        grid_idx    = nwin // max(1, nwindows_per_grid)
        calib_start = coordinates[nwin * 2]

        ipts_out, spikes_out, win_losses = _run_adapt_decomp_bidirectional(
            grid_data_g=grid_data_demeaned[grid_idx],
            win_data_g=demean(win_data[nwin]),
            whiten_mat=whiten_mats[nwin],
            mu_filters=filters,
            w_sig=w_sig_by_window[nwin],
            fsamp=fsamp,
            batch_ms=batch_ms,
            adapt_wh=adapt_wh,
            adapt_sv=adapt_sv,
            calib_start=calib_start,
            adapt_sd=adapt_sd,
            wh_learning_rate=wh_learning_rate,
            sv_learning_rate=sv_learning_rate,
            cov_alpha=cov_alpha,
            spike_prev_weight=spike_prev_weight,
            contrast_func=contrast_func,
            compute_loss=compute_loss,
        )

        if compute_loss and win_losses:
            all_losses[nwin] = win_losses

        for j in range(filters.shape[1]):
            pt = ipts_out[:, j] * np.abs(ipts_out[:, j])
            pulse_t[mu_nb, :] = pt[:ltime]
            distime.append(np.where(spikes_out[:ltime, j] > 0)[0].astype(int))
            mu_nb += 1

    return pulse_t, distime, all_losses
