"""Core decomposition step orchestration shared by CLI and API flows."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np

from muedit.decomp.algorithm import (
    FIXED_POINT_MAXITER,
    column_energy,
    compute_silhouette,
    fixed_point_alg,
    get_spikes,
    minimize_isi_covariance,
    pca_extended_signal,
    subtract_mu_waveforms,
    whiten_extended_signal,
    window_trim,
)
from muedit.decomp.types import DecomposeStepOutput, DecompositionParameters, PreprocessStepOutput
from muedit.models import FloatArray, IntArray
from muedit.signal.decomp_primitives import (
    DECOMP_MIN_ISI_SEC,
    extend_signal,
    extend_signal_mask,
    extension_factor,
    isi_cov,
)
from muedit.signal.filters import demean
from muedit.signal.grid import kept_rows

logger = logging.getLogger(__name__)


def extended_window(
    prep: PreprocessStepOutput, params: DecompositionParameters, win_global: int
) -> tuple[FloatArray, FloatArray, int, int]:
    """Window ``win_global`` demeaned, extended, trimmed; with means, ``ex_factor`` and trim."""
    kept = kept_rows(prep.discard_channels)[win_global // len(prep.rois)]
    start = prep.coordinates_plateau[win_global * 2]
    end = prep.coordinates_plateau[win_global * 2 + 1]
    win_data = prep.data[kept, start:end]
    ex_factor = extension_factor(params.nbextchan, win_data.shape[0])
    trim = window_trim(win_data.shape[1], prep.fsamp, params.edges_sec, ex_factor)
    e_sig = extend_signal(demean(win_data), ex_factor, dtype=params.work_dtype)
    if trim:
        e_sig = e_sig[:, trim:-trim]
    return e_sig, np.mean(win_data, axis=1, dtype=np.float64), ex_factor, trim


def decompose_step(
    prep: PreprocessStepOutput,
    params: DecompositionParameters,
    rng: np.random.Generator,
    progress_cb: Callable[[str, dict[str, Any]], None] | None,
) -> DecomposeStepOutput:
    """Run decomposition iterations over every grid and ROI window."""
    nwindows = len(prep.rois)
    ngrid = len(prep.grid_names)

    coordinates_plateau = list(prep.coordinates_plateau)
    mu_filters_by_window: dict[int, FloatArray] = {}
    whiten_mat_by_window: dict[int, FloatArray] = {}
    win_mean_by_window: dict[int, FloatArray] = {}

    sil_by_window: dict[int, list[float]] = {}
    mu_grid_index: list[int] = []

    for i, mask in enumerate(prep.discard_channels):
        logger.info(
            "Grid %d (%s): %d/%d channels kept",
            i + 1,
            prep.grid_names[i],
            np.count_nonzero(np.asarray(mask) == 0),
            len(mask),
        )

        for nwin in range(nwindows):
            win_global = i * nwindows + nwin
            logger.info(
                "Processing Grid %d (%s), Window %d",
                i + 1,
                prep.grid_names[i],
                nwin + 1,
            )

            # Where the live view places this window's iterations.
            where = {
                "phase": "decompose",
                "grid": i,
                "ngrid": ngrid,
                "window": nwin,
                "nwindows": nwindows,
                "niter": params.niter,
            }
            if progress_cb:
                progress_cb(
                    "progress",
                    {
                        "message": f"Grid {i + 1}/{ngrid} • Window {nwin + 1}/{nwindows}",
                        **where,
                        "iter": 0,
                        "outcomes": "",
                    },
                )

            # The untrimmed bounds, as extended_window reads them.
            start = prep.coordinates_plateau[win_global * 2]
            end = prep.coordinates_plateau[win_global * 2 + 1]
            e_sig, win_mean_by_window[win_global], ex_factor, trim = extended_window(
                prep, params, win_global
            )
            if trim:
                coordinates_plateau[win_global * 2] += trim
                coordinates_plateau[win_global * 2 + 1] -= trim

            clean_cols: IntArray | None = None
            win_clean = None
            if prep.artifact_mask is not None:
                win_mask_ext = extend_signal_mask(prep.artifact_mask[start:end], ex_factor)
                if trim:
                    win_mask_ext = win_mask_ext[trim:-trim]
                if win_mask_ext.any() and not win_mask_ext.all():
                    clean_cols = np.where(~win_mask_ext)[0]
                    win_clean = ~win_mask_ext

            eigenvectors, eigenvalues_diag = pca_extended_signal(e_sig, clean_cols)
            # Whitened in place: e_sig becomes the private working copy that
            # peel-off modifies, so the loop holds one extended-size array.
            x, whiten_mat = whiten_extended_signal(e_sig, eigenvectors, eigenvalues_diag)
            whiten_mat_by_window[win_global] = whiten_mat

            basis = np.zeros((x.shape[0], params.niter))
            filter_matrix = np.zeros((x.shape[0], params.niter))
            sil_scores = np.zeros(params.niter)
            cov_scores = np.zeros(params.niter)
            fitted = np.zeros(params.niter, dtype=bool)

            use_activity_init = not params.random_init
            refractory = max(1, int(round(prep.fsamp * DECOMP_MIN_ISI_SEC)))
            consumed = np.zeros(x.shape[1] if use_activity_init else 0, dtype=bool)
            energy = column_energy(x) if use_activity_init else None
            # Per iteration since the last event: k kept, r rejected, f too few spikes.
            outcomes: list[str] = []
            iters_done = 0

            for j in range(params.niter):
                w: FloatArray = rng.standard_normal(int(x.shape[0]))

                if energy is not None:
                    act_ind = energy.copy()
                    act_ind[consumed] = -1.0
                    if win_clean is not None:
                        act_ind[~win_clean] = -1.0
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
                        i + 1,
                        nwin + 1,
                        j,
                        params.niter,
                        w_norm,
                    )
                    break
                w = w / w_norm
                w = fixed_point_alg(w, x, basis[:, :j], FIXED_POINT_MAXITER, params.contrast_func)
                spikes = get_spikes(w, x, prep.fsamp)

                if win_clean is not None and len(spikes) > 0:
                    spikes = spikes[win_clean[spikes]]

                if len(spikes) > 10:
                    cov_val = isi_cov(spikes, prep.fsamp)
                    w_ini = np.sum(x[:, spikes], axis=1, dtype=np.float64)
                    w_final, spikes_final, cov_final = minimize_isi_covariance(
                        w_ini, x, cov_val, prep.fsamp
                    )
                    w_final_norm = np.sqrt(np.sum(w_final**2))
                    if w_final_norm > 0:
                        w_final = w_final / w_final_norm
                    filter_matrix[:, j] = w_final
                    w_basis: FloatArray = w_final - basis[:, :j] @ (basis[:, :j].T @ w_final)
                    w_basis_norm = np.linalg.norm(w_basis)
                    w_basis = w_basis / w_basis_norm if w_basis_norm > 1e-12 else w
                    basis[:, j] = w_basis
                    cov_scores[j] = cov_final
                    fitted[j] = True
                    sil_val = compute_silhouette(x, w_final, prep.fsamp)
                    sil_scores[j] = sil_val
                    if params.peel_off_enabled and sil_val >= params.sil_thr:
                        changed = subtract_mu_waveforms(
                            x, spikes_final, prep.fsamp, params.peel_off_win
                        )
                        if energy is not None:
                            for lo, hi in changed:
                                energy[lo:hi] = column_energy(x[:, lo:hi])
                    kept = sil_val >= params.sil_thr and (
                        not params.covfilter or cov_final <= params.cov_thr
                    )
                    outcomes.append("k" if kept else "r")
                else:
                    basis[:, j] = w
                    outcomes.append("f")
                iters_done = j + 1

                if progress_cb and (j % 5 == 4 or j == params.niter - 1):
                    progress_cb(
                        "progress",
                        {
                            "message": (
                                f"Grid {i + 1}/{ngrid} • "
                                f"Window {nwin + 1}/{nwindows} • "
                                f"Iter {j + 1}/{params.niter}"
                            ),
                            **where,
                            "iter": iters_done,
                            "outcomes": "".join(outcomes),
                        },
                    )
                    outcomes.clear()

            good_indices = (sil_scores >= params.sil_thr) & fitted
            if params.covfilter:
                good_indices = good_indices & (cov_scores <= params.cov_thr)
            mu_filters_by_window[win_global] = filter_matrix[:, good_indices]
            sil_by_window[win_global] = sil_scores[good_indices].tolist()
            mu_grid_index.extend([i] * int(np.sum(good_indices)))
            # Freed before the next window is extended, not when the names rebind.
            del e_sig, x

            if progress_cb:
                progress_cb(
                    "progress",
                    {
                        "message": (
                            f"Grid {i + 1}/{ngrid} • Window {nwin + 1}/{nwindows} completed"
                        ),
                        "sil": sil_by_window[win_global],
                        **where,
                        "iter": iters_done,
                        # Flushed here when the basis ran out before the next event.
                        "outcomes": "".join(outcomes),
                        "window_done": True,
                    },
                )

    return DecomposeStepOutput(
        mu_filters_by_window=mu_filters_by_window,
        whiten_mat_by_window=whiten_mat_by_window,
        coordinates_plateau=coordinates_plateau,
        sil_by_window=sil_by_window,
        mu_grid_index=mu_grid_index,
        win_mean_by_window=win_mean_by_window,
    )
