"""Core decomposition step orchestration shared by CLI and API flows."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np

from muedit.decomp.algorithm import (
    FIXED_POINT_MAXITER,
    compute_silhouette,
    extend_signal,
    fixed_point_alg,
    get_spikes,
    minimize_isi_covariance,
    pca_extended_signal,
    subtract_mu_waveforms,
    whiten_extended_signal,
)
from muedit.decomp.types import DecomposeStepOutput, DecompositionParameters, PreprocessStepOutput
from muedit.signal.decomp_primitives import DECOMP_MIN_ISI_SEC, isi_cov
from muedit.signal.filters import demean

logger = logging.getLogger(__name__)


def decompose_step(
    prep: PreprocessStepOutput,
    params: DecompositionParameters,
    rng: np.random.Generator,
    progress_cb: Callable[[str, dict[str, Any]], None] | None,
) -> DecomposeStepOutput:
    """Run decomposition iterations over every grid and ROI window."""
    nwindows = len(prep.roi_list)
    total_windows = prep.ngrid * nwindows

    coordinates_plateau = list(prep.coordinates_plateau)
    mu_filters: dict[int, np.ndarray] = {}
    whiten_mat: dict[int, np.ndarray] = {}
    win_means: dict[int, np.ndarray] = {}

    ch_idx = 0
    sil_by_window: dict[int, list[float]] = {}
    mu_grid_index: list[int] = []

    for i in range(prep.ngrid):
        mask = np.array(prep.discard_channels[i]).astype(int)
        n_channels_grid = mask.size
        keep_idx = np.where(mask == 0)[0]
        logger.info(
            "Grid %d (%s): %d/%d channels kept",
            i + 1,
            prep.grid_names[i],
            keep_idx.size,
            n_channels_grid,
        )

        for nwin in range(nwindows):
            win_global = i * nwindows + nwin
            span = 80 / total_windows
            logger.info(
                "Processing Grid %d (%s), Window %d",
                i + 1,
                prep.grid_names[i],
                nwin + 1,
            )

            start = coordinates_plateau[win_global * 2]
            end = coordinates_plateau[win_global * 2 + 1]
            grid_block = prep.data[ch_idx : ch_idx + n_channels_grid, start:end]
            win_data_arr = grid_block[keep_idx, :]

            ex_factor = int(round(params.nbextchan / win_data_arr.shape[0]))
            win_means[win_global] = np.mean(win_data_arr, axis=1)
            e_sig = extend_signal(demean(win_data_arr), ex_factor)

            edge_samples = int(round(prep.fsamp * params.edges_sec))
            trim_edges = edge_samples > 0 and win_data_arr.shape[1] > 2 * edge_samples
            if trim_edges:
                e_sig = e_sig[:, edge_samples:-edge_samples]
                coordinates_plateau[win_global * 2] += edge_samples
                coordinates_plateau[win_global * 2 + 1] -= edge_samples

            eigenvectors, eigenvalues_diag = pca_extended_signal(e_sig)
            w_sig_win, whiten_mat_win = whiten_extended_signal(
                e_sig, eigenvectors, eigenvalues_diag
            )
            whiten_mat[win_global] = whiten_mat_win

            basis = np.zeros((w_sig_win.shape[0], params.niter))
            filter_matrix = np.zeros((w_sig_win.shape[0], params.niter))
            sil_scores = np.zeros(params.niter)
            cov_scores = np.zeros(params.niter)
            fitted = np.zeros(params.niter, dtype=bool)
            x = w_sig_win

            use_activity_init = not params.initialization
            if use_activity_init:
                refractory = max(1, int(round(prep.fsamp * DECOMP_MIN_ISI_SEC)))
                consumed = np.zeros(x.shape[1], dtype=bool)

            for j in range(params.niter):
                w = rng.standard_normal(x.shape[0])

                if use_activity_init:
                    act_ind = np.sum(x * x, axis=0)
                    act_ind[consumed] = -1.0
                    col_idx = int(np.argmax(act_ind))
                    if act_ind[col_idx] > 0:
                        w = x[:, col_idx]
                        lo = max(0, col_idx - refractory)
                        hi = min(len(consumed), col_idx + refractory + 1)
                        consumed[lo:hi] = True

                w = w - basis[:, :j] @ (basis[:, :j].T @ w)
                w_norm = np.linalg.norm(w)
                if w_norm < 1e-12:
                    logger.info(
                        "Grid %d, Window %d: basis exhausted after %d/%d "
                        "iterations, stopping (deflated seed norm %.2e)",
                        i + 1, nwin + 1, j, params.niter, w_norm,
                    )
                    break
                w = w / w_norm
                w = fixed_point_alg(w, x, basis[:, :j], FIXED_POINT_MAXITER, params.contrast_func)
                _, spikes = get_spikes(w, x, prep.fsamp)

                if len(spikes) > 10:
                    cov_val = isi_cov(spikes, prep.fsamp)
                    w_ini = np.sum(x[:, spikes], axis=1)
                    w_final, spikes_final, cov_final = minimize_isi_covariance(
                        w_ini, x, cov_val, prep.fsamp
                    )
                    w_final_norm = np.sqrt(np.sum(w_final**2))
                    if w_final_norm > 0:
                        w_final = w_final / w_final_norm
                    filter_matrix[:, j] = w_final
                    w_basis = w_final - basis[:, :j] @ (basis[:, :j].T @ w_final)
                    w_basis_norm = np.linalg.norm(w_basis)
                    if w_basis_norm > 1e-12:
                        w_basis = w_basis / w_basis_norm
                    else:
                        w_basis = w
                    basis[:, j] = w_basis
                    cov_scores[j] = cov_final
                    fitted[j] = True
                    _, _, sil_val = compute_silhouette(x, w_final, prep.fsamp)
                    sil_scores[j] = sil_val
                    if params.peel_off_enabled and sil_val >= params.sil_thr:
                        x = subtract_mu_waveforms(
                            x, spikes_final, prep.fsamp, params.peel_off_win
                        )
                else:
                    basis[:, j] = w

                if progress_cb and (j % 5 == 4 or j == params.niter - 1):
                    pct_iter = 10 + win_global * span + ((j + 1) / params.niter) * span
                    progress_cb(
                        "progress",
                        {
                            "message": (
                                f"Grid {i + 1}/{prep.ngrid} • "
                                f"Window {nwin + 1}/{nwindows} • "
                                f"Iter {j + 1}/{params.niter}"
                            ),
                            "pct": min(90, int(pct_iter)),
                        },
                    )

            good_indices = (sil_scores >= params.sil_thr) & fitted
            if params.covfilter:
                good_indices = good_indices & (cov_scores <= params.cov_thr)
            mu_filters[win_global] = filter_matrix[:, good_indices]
            sil_by_window[win_global] = sil_scores[good_indices].tolist()
            mu_grid_index.extend([i] * int(np.sum(good_indices)))

            if progress_cb:
                pct = min(90, int(10 + (win_global + 1) * span))
                progress_cb(
                    "progress",
                    {
                        "message": (
                            f"Grid {i + 1}/{prep.ngrid} • "
                            f"Window {nwin + 1}/{nwindows} completed"
                        ),
                        "pct": pct,
                        "sil": sil_by_window[win_global],
                    },
                )
        ch_idx += n_channels_grid

    return DecomposeStepOutput(
        mu_filters=mu_filters,
        whiten_mat=whiten_mat,
        coordinates_plateau=coordinates_plateau,
        sil_by_window=sil_by_window,
        mu_grid_index=mu_grid_index,
        win_means=win_means,
    )
