"""Adaptive post-processing helpers for online-style decomposition batches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import numpy as np

from muedit.adapt_decomp.adaptation import AdaptiveDecomp, BatchSink, Calibration
from muedit.adapt_decomp.config import Config
from muedit.io.store import ArrayStore, RamStore
from muedit.models import BoolArray, FloatArray, IntArray
from muedit.signal.decomp_primitives import (
    POSTPROC_MIN_ISI_SEC,
    enforce_refractory,
    find_refractory_peaks,
    signed_square,
    split_by_amplitude,
)
from muedit.signal.streaming import SampleSource, StreamedExtender, extend_mask

_DEFAULT_CONFIG = Config()
_STATS_CHUNK = 4096


def _compute_calibration_stats(
    grid: SampleSource,
    win_mean: FloatArray,
    whiten_mat: FloatArray,
    mu_filters: FloatArray,
    calib_start: int,
    calib_end: int,
    fsamp: float,
) -> tuple[FloatArray, FloatArray]:
    """Project the calibration window through the MU filters and derive spike centroids."""
    n_mu = mu_filters.shape[1]
    source = StreamedExtender(grid, whiten_mat.shape[0] // grid.shape[0], offset=win_mean)
    w_dewhite = whiten_mat.T @ mu_filters
    # Squared chunk by chunk, so the window's pulse trains exist once, not twice.
    ipts_sq = np.empty((n_mu, calib_end - calib_start))
    for lo in range(calib_start, calib_end, _STATS_CHUNK):
        hi = min(lo + _STATS_CHUNK, calib_end)
        ipts_sq[:, lo - calib_start : hi - calib_start] = signed_square(
            w_dewhite.T @ source.read(lo, hi)
        )

    base_centr = np.zeros(n_mu, dtype=np.float32)
    spikes_centr = np.ones(n_mu, dtype=np.float32)

    for j in range(n_mu):
        pt = ipts_sq[j]
        peaks = find_refractory_peaks(pt, fsamp, min_isi_sec=POSTPROC_MIN_ISI_SEC)
        if len(peaks) > 1:
            _, centroids, _ = split_by_amplitude(pt, peaks)
            hi = int(np.argmax(centroids))
            spikes_centr[j] = float(centroids[hi])
            base_centr[j] = float(centroids[1 - hi])
        elif len(peaks) == 1:
            spikes_centr[j] = float(pt[peaks[0]])

    return base_centr, spikes_centr


def _run_adapt_decomp_bidirectional(
    source: StreamedExtender,
    whiten_mat: FloatArray,
    mu_filters: FloatArray,
    centroids: tuple[FloatArray, FloatArray],
    calib_start: int,
    calib_end: int,
    config: Config,
    sink: BatchSink,
) -> dict[str, Any]:
    """Run adaptive decomposition forward from calib_start and, if needed, backward over the pre-calibration segment."""
    base_centr, spikes_centr = centroids
    cfg = replace(config, ex_factor=source.ex_factor)
    bs = cfg.batch_size

    def _clipped(lo: int, hi: int) -> BatchSink:
        def _emit(start: int, ipts: np.ndarray, spikes: np.ndarray) -> None:
            a, b = max(start, lo), min(start + len(ipts), hi)
            if a < b:
                sink(a, ipts[a - start : b - start], spikes[a - start : b - start])

        return _emit

    def _pass(calibration: tuple[int, int] | Calibration) -> AdaptiveDecomp:
        return AdaptiveDecomp(
            emg=source,
            whitening=whiten_mat,
            sep_vectors=mu_filters.T,
            base_centr=base_centr,
            spikes_centr=spikes_centr,
            emg_calib=calibration,
            config=cfg,
        )

    # The forward pass warms up on the batch before the calibration window;
    # that batch adapts the state but its output is dropped.
    fwd_start = max(0, calib_start - bs)
    fwd = _pass((calib_start, calib_end))
    losses_fwd = fwd.run(fwd_start, source.n_samples, sink=_clipped(calib_start, source.n_samples))
    if losses_fwd and (source.n_samples - fwd_start) % bs:
        # The trailing partial batch never has a loss; keep one entry per full batch.
        losses_fwd = {k: v[:-1] for k, v in losses_fwd.items()}
    n_pre_fwd = -(-(calib_start - fwd_start) // bs)
    if losses_fwd:
        losses_fwd = {k: v[n_pre_fwd:] for k, v in losses_fwd.items()}

    if calib_start == 0:
        return losses_fwd

    bwd = _pass(fwd.calibration)
    losses_bwd = bwd.run(0, calib_start, reverse=True, sink=_clipped(0, calib_start))
    if not losses_fwd:
        return {}
    # Processed from the calibration window backwards: flip to chronological order.
    return {
        k: np.concatenate([losses_bwd[k][::-1], losses_fwd[k]], axis=0)
        for k in ("wh_loss", "sv_loss", "total_loss")
    }


def adaptive_batch_process(
    mu_filters_by_window: dict[int, FloatArray],
    whiten_mats: dict[int, FloatArray],
    grid_data: Mapping[int, SampleSource],  # kept channels per grid, read batch by batch
    coordinates: list[int],
    ltime: int,
    fsamp: float,
    nwindows_per_grid: int,
    win_means_by_window: dict[int, FloatArray],
    batch_ms: int = _DEFAULT_CONFIG.batch_ms,
    adapt_wh: bool = _DEFAULT_CONFIG.adapt_wh,
    adapt_sv: bool = _DEFAULT_CONFIG.adapt_sv,
    adapt_sd: bool = _DEFAULT_CONFIG.adapt_sd,
    wh_learning_rate: float = _DEFAULT_CONFIG.wh_learning_rate,
    sv_learning_rate: float = _DEFAULT_CONFIG.sv_learning_rate,
    cov_alpha: float = _DEFAULT_CONFIG.cov_alpha,
    spike_prev_weight: int = _DEFAULT_CONFIG.spike_prev_weight,
    compute_loss: bool = _DEFAULT_CONFIG.compute_loss,
    artifact_mask: BoolArray | None = None,
    pulse_dtype: type[np.floating[Any]] = np.float32,
    store: ArrayStore | None = None,
) -> tuple[FloatArray, list[IntArray], dict[int, dict[str, Any]]]:
    """Apply adaptive post-processing across all decomposition windows and grids.

    The pulse trains are written into ``store`` (heap by default) batch by batch.
    """
    config = Config(
        fsamp=int(fsamp),
        batch_ms=batch_ms,
        adapt_wh=adapt_wh,
        adapt_sv=adapt_sv,
        adapt_sd=adapt_sd,
        wh_learning_rate=wh_learning_rate,
        sv_learning_rate=sv_learning_rate,
        cov_alpha=cov_alpha,
        spike_prev_weight=spike_prev_weight,
        compute_loss=compute_loss,
    )

    total_mus = sum(f.shape[1] for f in mu_filters_by_window.values() if f.size > 0)
    if total_mus == 0:
        return np.array([]), [], {}

    store = store if store is not None else RamStore()
    pulse_t = store.allocate("pulse_all", (total_mus, ltime), pulse_dtype, zero=True)
    spike_times: list[IntArray] = []
    spike_units: list[IntArray] = []
    mu_nb = 0
    all_losses: dict[int, dict[str, Any]] = {}

    for nwin in sorted(mu_filters_by_window.keys()):
        filters = mu_filters_by_window[nwin]
        if filters.size == 0:
            continue

        grid_idx = nwin // max(1, nwindows_per_grid)
        calib_start, calib_end = coordinates[nwin * 2], coordinates[nwin * 2 + 1]
        grid = grid_data[grid_idx]
        win_mean = win_means_by_window[nwin]
        source = StreamedExtender(
            grid,
            whiten_mats[nwin].shape[0] // grid.shape[0],
            offset=win_mean,
            dtype=np.float32,
            samples_first=True,
            artifact_mask=artifact_mask,
        )
        rows = slice(mu_nb, mu_nb + filters.shape[1])

        def _collect(start: int, ipts: np.ndarray, spikes: np.ndarray, rows: slice = rows) -> None:
            stop = min(start + len(ipts), ltime)
            pulse_t[rows, start:stop] = signed_square(ipts[: stop - start].astype(np.float64)).T
            t, unit = np.nonzero(spikes[: stop - start])
            spike_times.append(start + t)
            spike_units.append(rows.start + unit)

        centroids = _compute_calibration_stats(
            grid, win_mean, whiten_mats[nwin], filters, calib_start, calib_end, fsamp
        )
        win_losses = _run_adapt_decomp_bidirectional(
            source=source,
            whiten_mat=whiten_mats[nwin],
            mu_filters=filters,
            centroids=centroids,
            calib_start=calib_start,
            calib_end=calib_end,
            config=config,
            sink=_collect,
        )
        if compute_loss and win_losses:
            all_losses[nwin] = win_losses
        if artifact_mask is not None:
            pulse_t[rows][:, extend_mask(artifact_mask[:ltime], source.ex_factor)] = 0.0
        mu_nb += filters.shape[1]

    times = np.concatenate(spike_times) if spike_times else np.array([], dtype=int)
    units = np.concatenate(spike_units) if spike_units else np.array([], dtype=int)
    order = np.lexsort((times, units))
    by_unit = np.split(
        times[order].astype(int), np.searchsorted(units[order], np.arange(1, total_mus))
    )

    min_isi_sec = config.spike_dist_ms / 1000.0
    # Detection is per batch, so a pair straddling a batch boundary or the
    # backward/forward seam can still breach the refractory period.
    distime = [
        enforce_refractory(by_unit[j], pulse_t[j], fsamp, min_isi_sec=min_isi_sec)
        for j in range(total_mus)
    ]

    return pulse_t, distime, all_losses
