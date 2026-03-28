"""Core decomposition step orchestration shared by CLI and API flows."""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np

from muedit.decomp.algorithm import (
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
from muedit.utils import demean

logger = logging.getLogger(__name__)


def decompose_step(
    prep: PreprocessStepOutput,
    params: DecompositionParameters,
    rng: np.random.Generator,
    progress_cb: Callable[[str, dict[str, object]], None] | None,
) -> DecomposeStepOutput:
    """Run decomposition iterations over every grid and ROI window."""
    params.nwindows = len(prep.roi_list)
    total_windows = max(1, prep.ngrid * params.nwindows)

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

        for nwin in range(params.nwindows):
            win_global = i * params.nwindows + nwin
            logger.info(
                "Processing Grid %d (%s), Window %d",
                i + 1,
                prep.grid_names[i],
                nwin + 1,
            )

            start = prep.signal_process["coordinates_plateau"][win_global * 2]
            end = prep.signal_process["coordinates_plateau"][win_global * 2 + 1]
            grid_block = prep.data[ch_idx : ch_idx + n_channels_grid, start:end]
            win_data = grid_block[keep_idx, :]

            ex_factor = int(round(params.nbextchan / max(1, win_data.shape[0])))
            prep.signal_process["ex_factor"] = ex_factor
            e_sig = demean(extend_signal(win_data, ex_factor))

            edge_samples = int(round(prep.fsamp * params.edges_sec))
            if e_sig.shape[1] > 2 * edge_samples:
                e_sig = e_sig[:, edge_samples:-edge_samples]
                prep.signal_process["coordinates_plateau"][win_global * 2] += edge_samples
                prep.signal_process["coordinates_plateau"][win_global * 2 + 1] -= edge_samples

            eigenvectors, eigenvalues_diag = pca_extended_signal(e_sig)
            w_sig, whiten_mat, _ = whiten_extended_signal(
                e_sig, eigenvectors, eigenvalues_diag
            )
            prep.signal_process["w_sig"][win_global] = w_sig
            prep.signal_process["win_data"][win_global] = (
                win_data[:, edge_samples:-edge_samples]
                if win_data.shape[1] > 2 * edge_samples
                else win_data
            )
            prep.signal_process["whiten_mat"][win_global] = whiten_mat

            basis = np.zeros((w_sig.shape[0], params.niter))
            mu_filters = np.zeros((w_sig.shape[0], params.niter))
            sil_scores = np.zeros(params.niter)
            cov_scores = np.zeros(params.niter)
            x = w_sig.copy()

            for j in range(params.niter):
                if j == 0 and params.initialization == 0:
                    act_ind = np.sum(x, axis=0) ** 2
                    w = x[:, int(np.argmax(act_ind))]
                else:
                    w = rng.standard_normal(x.shape[0])

                w = w - basis @ (basis.T @ w)
                w = w / np.linalg.norm(w)
                w = fixed_point_alg(w, x, basis, 500, params.contrast_func)
                _, spikes = get_spikes(w, x, prep.fsamp)

                if len(spikes) > 10:
                    isi = np.diff(spikes) / prep.fsamp
                    cov_val = np.std(isi) / np.mean(isi)
                    cov_scores[j] = cov_val
                    w_ini = np.sum(x[:, spikes], axis=1)
                    w_final, spikes_final, cov_final = minimize_isi_covariance(
                        w_ini, x, cov_val, prep.fsamp
                    )
                    # Keep separator vector scaling consistent with adapt_decomp.
                    w_final_norm = np.sqrt(np.sum(w_final**2))
                    if w_final_norm > 0:
                        w_final = w_final / w_final_norm
                    mu_filters[:, j] = w_final
                    basis[:, j] = w
                    cov_scores[j] = cov_final
                    _, _, sil_val = compute_silhouette(x, w_final, prep.fsamp)
                    sil_scores[j] = sil_val
                    if params.peel_off_enabled == 1 and sil_val > params.sil_thr:
                        x = subtract_mu_waveforms(
                            x, spikes_final, prep.fsamp, params.peel_off_win
                        )
                else:
                    basis[:, j] = w

                if progress_cb and (j % 5 == 4 or j == params.niter - 1):
                    span = 80 / total_windows
                    pct_iter = 10 + win_global * span + ((j + 1) / params.niter) * span
                    progress_cb(
                        "progress",
                        {
                            "message": (
                                f"Grid {i + 1}/{prep.ngrid} • "
                                f"Window {nwin + 1}/{params.nwindows} • "
                                f"Iter {j + 1}/{params.niter}"
                            ),
                            "pct": min(90, int(pct_iter)),
                        },
                    )

            good_indices = sil_scores >= params.sil_thr
            if params.covfilter == 1:
                good_indices = good_indices & (cov_scores <= params.cov_thr)
            prep.signal_process["mu_filters"][win_global] = mu_filters[:, good_indices]
            sil_by_window[win_global] = sil_scores[good_indices].tolist()
            mu_grid_index.extend([i] * int(np.sum(good_indices)))

            if progress_cb:
                win_idx = win_global + 1
                pct = min(90, int(win_idx / total_windows * 80) + 10)
                progress_cb(
                    "progress",
                    {
                        "message": (
                            f"Grid {i + 1}/{prep.ngrid} • "
                            f"Window {nwin + 1}/{params.nwindows} completed"
                        ),
                        "pct": pct,
                        "sil": sil_by_window[win_global],
                    },
                )
        ch_idx += n_channels_grid

    return DecomposeStepOutput(
        signal_process=prep.signal_process,
        sil_by_window=sil_by_window,
        mu_grid_index=mu_grid_index,
    )
