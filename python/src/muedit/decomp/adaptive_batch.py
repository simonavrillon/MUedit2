
"""Adaptive post-processing helpers for online-style decomposition batches."""

from __future__ import annotations

import importlib.util
from typing import Any

import numpy as np
from scipy.signal import find_peaks


def _compute_calibration_stats(
    w_sig: np.ndarray,
    mu_filters: np.ndarray,
    fsamp: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Project the whitened calibration signal through MU filters and derive spike statistics.

    Returns (ipts_calib, spikes_calib, base_centr, spikes_centr) used to
    initialise the AdaptDecomp model for subsequent batches.
    """
    from muedit.decomp.algorithm import simple_kmeans

    n_mu = mu_filters.shape[1]
    ipts_calib = (w_sig.T @ mu_filters).astype(np.float32)
    ipts_sq    = ipts_calib * np.abs(ipts_calib)

    spikes_calib = np.zeros((w_sig.shape[1], n_mu), dtype=np.int32)
    base_centr   = np.zeros(n_mu, dtype=np.float32)
    spikes_centr = np.ones(n_mu, dtype=np.float32)

    dist = int(round(fsamp * 0.02))
    for j in range(n_mu):
        pt = ipts_sq[:, j]
        peaks, _ = find_peaks(pt, distance=dist)
        if len(peaks) > 1:
            labels, centroids = simple_kmeans(pt[peaks], k=2)
            hi = int(np.argmax(centroids))
            spikes_calib[peaks[labels == hi], j] = 1
            spikes_centr[j] = float(centroids[hi])
            base_centr[j]   = float(centroids[1 - hi])
        elif len(peaks) == 1:
            spikes_calib[peaks[0], j] = 1
            spikes_centr[j] = float(pt[peaks[0]])

    return ipts_calib, spikes_calib, base_centr, spikes_centr





def _to_numpy_array(value: Any) -> np.ndarray:
    """Coerce a value (tensor, list, or None) to a NumPy array."""
    if value is None:
        return np.array([])
    if isinstance(value, np.ndarray):
        return value
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        return value.numpy()
    return np.asarray(value)


def _run_one_pass(
    emg_seg: np.ndarray,
    emg_calib: np.ndarray,
    whiten_mat: np.ndarray,
    mu_filters: np.ndarray,
    ipts_calib: np.ndarray,
    spikes_calib: np.ndarray,
    base_centr: np.ndarray,
    spikes_centr: np.ndarray,
    fsamp: float,
    ex_factor: int,
    batch_ms: int,
    adapt_wh: bool,
    adapt_sv: bool,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Run AdaptDecomp on a single EMG segment and return ipts, spikes, and optional metrics."""
    import torch

    from adapt_decomp.adaptation import AdaptDecomp
    from adapt_decomp.config import Config

    config = Config(
        fs=int(fsamp),
        ext_fact=ex_factor,
        batch_ms=batch_ms,
        adapt_wh=adapt_wh,
        adapt_sv=adapt_sv,
    )
    model = AdaptDecomp(
        emg=torch.from_numpy(emg_seg.astype(np.float32)),
        whitening=torch.from_numpy(whiten_mat.astype(np.float32)),
        sep_vectors=torch.from_numpy(mu_filters.T.astype(np.float32)),
        base_centr=torch.from_numpy(base_centr.copy()),
        spikes_centr=torch.from_numpy(spikes_centr.copy()),
        emg_calib=torch.from_numpy(emg_calib.astype(np.float32)),
        ipts_calib=torch.from_numpy(ipts_calib),
        spikes_calib=torch.from_numpy(spikes_calib),
        preprocess=False,
        config=config,
    )
    outputs = model.run()
    if isinstance(outputs, tuple) and len(outputs) == 2:
        spikes_out, ipts_out = outputs
        return (
            _to_numpy_array(ipts_out).astype(np.float64),
            _to_numpy_array(spikes_out),
            {},
        )

    outputs_map = outputs if isinstance(outputs, dict) else {}
    metrics = {
        "wh_loss": _to_numpy_array(outputs_map.get("wh_loss")),
        "sv_loss": _to_numpy_array(outputs_map.get("sv_loss")),
        "total_loss": _to_numpy_array(outputs_map.get("total_loss")),
        "wh_time_ms": _to_numpy_array(outputs_map.get("wh_time_ms")),
        "sv_time_ms": _to_numpy_array(outputs_map.get("sv_time_ms")),
        "sd_time_ms": _to_numpy_array(outputs_map.get("sd_time_ms")),
        "total_time_ms": _to_numpy_array(outputs_map.get("total_time_ms")),
    }
    return (
        _to_numpy_array(outputs_map.get("ipts")).astype(np.float64),
        _to_numpy_array(outputs_map.get("spikes")),
        metrics,
    )






def _merge_metrics(
    metrics_a: dict[str, np.ndarray],
    metrics_b: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Concatenate two metric dicts along axis 0, treating missing keys as empty."""
    merged: dict[str, np.ndarray] = {}
    for key in set(metrics_a.keys()) | set(metrics_b.keys()):
        arr_a = metrics_a.get(key, np.array([]))
        arr_b = metrics_b.get(key, np.array([]))
        if arr_a.size == 0:
            merged[key] = arr_b
        elif arr_b.size == 0:
            merged[key] = arr_a
        else:
            merged[key] = np.concatenate([arr_a, arr_b], axis=0)
    return merged


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
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Run adaptive decomposition forward from calib_start and, if needed, backward over the pre-calibration segment.

    Concatenates both passes to cover the full recording and returns
    (ipts, spikes, metrics).
    """
    from muedit.decomp.algorithm import extend_signal

    ipts_calib, spikes_calib, base_centr, spikes_centr = _compute_calibration_stats(
        w_sig, mu_filters, fsamp
    )

    ex_factor = w_sig.shape[0] // grid_data_g.shape[0]
    bs = int(batch_ms * fsamp / 1000)


    emg_calib_raw = win_data_g.T.astype(np.float32)
    emg_calib_ext = extend_signal(win_data_g, ex_factor).T.astype(np.float32)

    fwd_start = max(0, calib_start - bs)
    emg_fwd = np.ascontiguousarray(grid_data_g[:, fwd_start:].T.astype(np.float32))
    ipts_fwd_full, spikes_fwd_full, metrics_fwd = _run_one_pass(
        emg_seg=emg_fwd,
        emg_calib=emg_calib_raw,
        whiten_mat=whiten_mat,
        mu_filters=mu_filters,
        ipts_calib=ipts_calib,
        spikes_calib=spikes_calib,
        base_centr=base_centr,
        spikes_centr=spikes_centr,
        fsamp=fsamp,
        ex_factor=ex_factor,
        batch_ms=batch_ms,
        adapt_wh=adapt_wh,
        adapt_sv=adapt_sv,
    )
    ipts_fwd   = ipts_fwd_full[calib_start - fwd_start:]
    spikes_fwd = spikes_fwd_full[calib_start - fwd_start:]


    if calib_start == 0:
        return ipts_fwd, spikes_fwd, metrics_fwd


    # `extend_signal` adds (ex_factor - 1) trailing padded samples; keep only
    # the original pre-calibration duration to avoid forward/backward offset.
    e_pre = extend_signal(grid_data_g[:, :calib_start], ex_factor).T[:calib_start]


    split_pts = list(range(bs, calib_start, bs))
    blocks = np.split(e_pre, split_pts, axis=0)
    emg_bwd = np.ascontiguousarray(np.concatenate(blocks[::-1], axis=0).astype(np.float32))

    ipts_bwd_rev, spikes_bwd_rev, metrics_bwd = _run_one_pass(
        emg_seg=emg_bwd,
        emg_calib=emg_calib_ext,
        whiten_mat=whiten_mat,
        mu_filters=mu_filters,
        ipts_calib=ipts_calib,
        spikes_calib=spikes_calib,
        base_centr=base_centr,
        spikes_centr=spikes_centr,
        fsamp=fsamp,
        ex_factor=1,
        batch_ms=batch_ms,
        adapt_wh=adapt_wh,
        adapt_sv=adapt_sv,
    )


    out_ipts   = np.split(ipts_bwd_rev,   split_pts, axis=0)
    out_spikes = np.split(spikes_bwd_rev, split_pts, axis=0)
    ipts_bwd   = np.concatenate(out_ipts[::-1],   axis=0)
    spikes_bwd = np.concatenate(out_spikes[::-1], axis=0)

    return (
        np.concatenate([ipts_bwd,   ipts_fwd],   axis=0),
        np.concatenate([spikes_bwd, spikes_fwd], axis=0),
        _merge_metrics(metrics_bwd, metrics_fwd),
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
) -> tuple[np.ndarray, list[np.ndarray], dict[str, Any]]:
    """Apply adaptive post-processing across all decomposition windows and grids.

    Falls back to the standard batch_process_filters if torch or adapt_decomp
    is not installed. Returns (pulse_t, distime, adaptive_losses).
    """
    if (
        importlib.util.find_spec("torch") is None
        or importlib.util.find_spec("adapt_decomp.adaptation") is None
    ):
        from muedit.decomp.algorithm import batch_process_filters

        pulse_t, fallback_distime = batch_process_filters(
            mu_filters_by_window, w_sig_by_window, coordinates, ltime, fsamp, nwindows_per_grid
        )
        return pulse_t, fallback_distime, {}

    from muedit.utils import demean

    total_mus = sum(f.shape[1] for f in mu_filters_by_window.values() if f.size > 0)
    if total_mus == 0:
        return np.array([]), [], {}

    pulse_t = np.zeros((total_mus, ltime), dtype=np.float64)
    distime: list[np.ndarray] = []
    metrics_by_window: dict[int, dict[str, Any]] = {}
    mu_nb = 0

    grid_data_demeaned = {i: demean(g) for i, g in grid_data.items()}

    for nwin in sorted(mu_filters_by_window.keys()):
        filters = mu_filters_by_window[nwin]
        if filters.size == 0:
            continue

        grid_idx    = nwin // max(1, nwindows_per_grid)
        calib_start = coordinates[nwin * 2]

        ipts_out, spikes_out, metrics_out = _run_adapt_decomp_bidirectional(
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
        )
        metrics_by_window[nwin] = {
            key: value.tolist() if isinstance(value, np.ndarray) else value
            for key, value in metrics_out.items()
        }

        for j in range(filters.shape[1]):
            pt = ipts_out[:, j] * np.abs(ipts_out[:, j])
            pulse_t[mu_nb, :] = pt[:ltime]
            distime.append(np.where(spikes_out[:ltime, j] > 0)[0].astype(int))
            mu_nb += 1

    return pulse_t, distime, {"by_window": metrics_by_window}
