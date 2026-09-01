"""Core decomposition math primitives (whitening, fixed-point ICA, spike ops)."""

from __future__ import annotations

import logging

import numpy as np
from scipy.linalg import eigh, inv

from muedit.signal.decomp_primitives import (
    DECOMP_MIN_ISI_SEC,
    extend_signal,
    find_refractory_peaks,
    isi_cov,
    signed_square,
    split_by_amplitude,
)

logger = logging.getLogger(__name__)

_FIXED_POINT_TOL = 1e-4
_FIXED_POINT_MAXITER = 500
_MIN_ISI_SEC = DECOMP_MIN_ISI_SEC
_KMEANS_ITER = 10
DEDUP_MAXLAG_RATIO: int = 40
DEDUP_JITTER: float = 0.00025

__all__ = [
    "DEDUP_JITTER",
    "DEDUP_MAXLAG_RATIO",
    "batch_process_filters",
    "compute_silhouette",
    "extend_signal",
    "fixed_point_alg",
    "get_spikes",
    "minimize_isi_covariance",
    "pca_extended_signal",
    "rem_duplicates",
    "subtract_mu_waveforms",
    "whiten_extended_signal",
]


def pca_extended_signal(signal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Estimate PCA basis/eigenvalues for extended signal whitening."""
    cov_matrix = np.cov(signal, bias=True)
    eigenvalues, eigenvectors = eigh(cov_matrix)

    idx = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[idx]
    eigenvectors = eigenvectors[:, idx]

    n_eigs = len(eigenvalues)
    rank_tolerance = np.mean(eigenvalues[n_eigs // 2 :])
    if rank_tolerance < 0:
        rank_tolerance = 0

    max_last_eig = np.sum(eigenvalues > rank_tolerance)
    if 0 < max_last_eig < signal.shape[0]:
        lower_limit_value = (eigenvalues[max_last_eig - 1] + eigenvalues[max_last_eig]) / 2
    else:
        lower_limit_value = rank_tolerance

    mask = eigenvalues > max(lower_limit_value, 1e-10 * eigenvalues[0])
    if not mask.any():
        raise ValueError(
            "Degenerate window: covariance has no positive eigenvalues (flat channels?)"
        )
    eigenvectors_selected = eigenvectors[:, mask]
    eigenvalues_diag = np.diag(eigenvalues[mask])

    return eigenvectors_selected, eigenvalues_diag


def whiten_extended_signal(
    signal: np.ndarray,
    eigenvectors: np.ndarray,
    eigenvalues_diag: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Whiten extended signal and return whitening/dewhitening matrices."""
    sqrt_d = np.sqrt(eigenvalues_diag)
    inv_sqrt_d = inv(sqrt_d)

    whitening_matrix = eigenvectors @ inv_sqrt_d @ eigenvectors.T
    dewhitening_matrix = eigenvectors @ sqrt_d @ eigenvectors.T
    whiten_signals = whitening_matrix @ signal

    return whiten_signals, whitening_matrix, dewhitening_matrix


def fixed_point_alg(
    w: np.ndarray,
    x: np.ndarray,
    basis: np.ndarray,
    maxiter: int,
    contrast_func: str,
) -> np.ndarray:
    """Run one-unit FastICA fixed-point iterations with orthogonalization."""
    k = 0
    delta = 1.0
    basis_bt = basis @ basis.T
    n_samples = x.shape[1]

    while delta > _FIXED_POINT_TOL and k < maxiter - 1:
        w_last = w.copy()
        wtx = w_last.T @ x

        if contrast_func == "skew":
            gp = 2 * wtx
            g = wtx**2
        elif contrast_func == "kurtosis":
            gp = 3 * wtx**2
            g = wtx**3
        elif contrast_func == "logcosh":
            g = np.tanh(wtx)
            gp = 1.0 - g**2
        else:
            raise ValueError(f"Unknown contrast function: {contrast_func}")

        a = np.mean(gp)
        w = (x @ g.T) / n_samples - a * w_last
        w = w - basis_bt @ w
        w_norm = np.linalg.norm(w)
        if w_norm == 0:
            break
        w = w / w_norm

        k += 1
        delta = abs(abs(np.dot(w.flatten(), w_last.flatten())) - 1)

    return w


def _pulse_train(w: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Project source and apply signed-squared nonlinearity."""
    wtx = w.T @ x
    return signed_square(wtx).flatten()


def _detect_peaks(icasig: np.ndarray, fsamp: float) -> np.ndarray:
    """Detect candidate spikes with refractory-distance peak picking."""
    return find_refractory_peaks(icasig, fsamp, min_isi_sec=_MIN_ISI_SEC)


def get_spikes(
    w: np.ndarray,
    x: np.ndarray,
    fsamp: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate spike times from one source using k-means amplitude split."""
    icasig = _pulse_train(w, x)
    spikes = _detect_peaks(icasig, fsamp)

    if len(spikes) <= 1:
        return icasig, np.asarray(spikes, dtype=int)

    spikes2, _, _ = split_by_amplitude(icasig, spikes, kmeans_iter=_KMEANS_ITER)

    vals = icasig[spikes2]
    threshold = np.mean(vals) + 3 * np.std(vals)
    spikes2 = spikes2[vals <= threshold]

    return icasig, spikes2


def minimize_isi_covariance(
    w: np.ndarray,
    x: np.ndarray,
    cov: float,
    fsamp: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Refine separating vector by minimizing ISI coefficient of variation."""
    cov_last = cov + 0.1
    spikes = np.array([1])
    spikes_last = spikes
    w_last = w.copy()

    while cov < cov_last:
        cov_last = cov
        spikes_last = spikes
        w_last = w.copy()

        _, spikes = get_spikes(w, x, fsamp)

        if len(spikes) < 2:
            break

        cov = isi_cov(spikes, fsamp)

        w = np.sum(x[:, spikes], axis=1)

    if len(spikes_last) < 2:
        _, spikes_last = get_spikes(w_last, x, fsamp)

    # Recompute cov_last from the actual returned spike train so that the
    # reported CoV describes spikes_last, not the caller's input separator.
    # When the loop made one pass (the common case), cov_last is still the
    # caller's input CoV and spikes_last was recomputed above from w_last.
    if len(spikes_last) >= 2:
        cov_last = isi_cov(spikes_last, fsamp)
    else:
        # spikes_last still has < 2 entries — cov_last is stale.  Return
        # NaN so covfilter (if enabled) rejects the unit rather than
        # accepting it on a CoV from a different separator.
        cov_last = float("nan")

    return w_last, spikes_last, cov_last


def compute_silhouette(
    x: np.ndarray,
    w: np.ndarray,
    fsamp: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Compute silhouette-like separability score for detected spikes."""
    icasig = _pulse_train(w, x)
    spikes = _detect_peaks(icasig, fsamp)

    if len(spikes) <= 1:
        return icasig, np.array(spikes, dtype=int), 0.0

    spikes2, centroids, labels = split_by_amplitude(icasig, spikes, kmeans_iter=_KMEANS_ITER)

    idx2 = int(np.argmax(centroids))
    other_idx = 1 - idx2

    spike_cluster_vals = icasig[spikes][labels == idx2]
    within = float(np.sum((spike_cluster_vals - centroids[idx2]) ** 2))
    between = float(np.sum((spike_cluster_vals - centroids[other_idx]) ** 2))

    denom = max(within, between)
    sil = (between - within) / denom if denom > 0 else 0.0

    return icasig, spikes2, sil


def subtract_mu_waveforms(
    x: np.ndarray,
    spikes: np.ndarray,
    fsamp: float,
    win: float,
) -> np.ndarray:
    """Subtract averaged MU waveform estimate from multichannel signal."""
    window_l = int(np.round(win * fsamp))
    n_rows, n_cols = x.shape

    spikes = np.asarray(spikes, dtype=int)
    valid_spikes = spikes[(spikes >= window_l) & (spikes < n_cols - window_l)]
    if valid_spikes.size == 0:
        return x

    # Extract all segments at once: (n_rows, n_spikes, window_size)
    offsets = np.arange(-window_l, window_l + 1, dtype=int)
    idx = valid_spikes[:, None] + offsets[None, :]  # (n_spikes, window_size)
    waveforms = x[:, idx].mean(axis=1)  # (n_rows, window_size)

    # Scatter-add: stamp mean waveform at each spike position.
    # Loop is over n_spikes (~80), not n_rows (~1024) — each iteration is a
    # vectorised add across all rows, exploiting sparse firing patterns.
    emg_temp = np.zeros_like(x)
    for s in valid_spikes:
        emg_temp[:, s - window_l : s + window_l + 1] += waveforms

    return x - emg_temp


def batch_process_filters(
    mu_filters_by_window: dict[int, np.ndarray],
    whitened_windows: dict[int, np.ndarray],
    coordinates: list[int],
    ltime: int,
    fsamp: float,
    whiten_mat_by_window: dict[int, np.ndarray] | None = None,
    full_extended_by_window: dict[int, np.ndarray] | None = None,
    win_means_by_window: dict[int, np.ndarray] | None = None,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Apply MU filters across windows and reconstruct pulse trains/spike times.

    When ``full_extended_by_window`` and ``whiten_mat_by_window`` are provided,
    each filter is dewhitened (``w @ W``) and projected onto the full extended
    signal of its window, producing pulse trains over the entire trace instead
    of only the decomposed windows.

    ``full_extended_by_window`` holds the *raw* (non-demeaned) extended signal,
    shared per grid. When ``win_means_by_window`` is also provided, the
    per-window DC baseline is removed as a cheap additive correction so the
    dewhitened filter is applied to the same baseline it was estimated on,
    without building a per-window extended array.
    """
    total_mus = 0
    for nwin in mu_filters_by_window:
        if mu_filters_by_window[nwin].size > 0:
            total_mus += mu_filters_by_window[nwin].shape[1]

    if total_mus == 0:
        return np.array([]), []

    pulse_t = np.zeros((total_mus, ltime))
    distime = []

    mu_nb = 0
    sorted_wins = sorted(mu_filters_by_window.keys())

    for nwin in sorted_wins:
        filters = mu_filters_by_window[nwin]
        n_filters = filters.shape[1]

        for j in range(n_filters):
            current_filter = filters[:, j]

            if full_extended_by_window is not None and whiten_mat_by_window is not None:
                w_dewhite = current_filter @ whiten_mat_by_window[nwin]
                raw_ext = full_extended_by_window[nwin]
                pt_full = w_dewhite @ raw_ext
                # Remove the window's per-channel DC baseline as an additive
                # correction: w_dewhite @ extend(A - m) == w_dewhite @ extend(A)
                # - corr, avoiding a per-window extended array.
                if win_means_by_window is not None:
                    win_mean = win_means_by_window[nwin]
                    n_ch = win_mean.size
                    ex_factor = w_dewhite.size // n_ch
                    ext_cols = raw_ext.shape[1]
                    n_samples = ext_cols - ex_factor + 1
                    s = np.array(
                        [w_dewhite[k * n_ch : (k + 1) * n_ch] @ win_mean for k in range(ex_factor)]
                    )
                    corr = np.zeros(ext_cols)
                    for k in range(ex_factor):
                        corr[k : n_samples + k] += s[k]
                    pt_full = pt_full - corr
                pulse_t[mu_nb, :ltime] = pt_full[:ltime]
            else:
                # Apply the filter only to its own window's whitened signal.
                # Each window has its own whitening basis (estimated from that
                # window's PCA), so projecting a filter onto another window's
                # whitened signal would be invalid. Use ``full_trace`` to spread
                # a filter across the whole grid via dewhitening.
                start = coordinates[nwin * 2]
                segment_len = whitened_windows[nwin].shape[1]
                pt_segment = np.dot(current_filter, whitened_windows[nwin])
                if start + segment_len <= ltime:
                    pulse_t[mu_nb, start : start + segment_len] = pt_segment
                else:
                    valid_len = ltime - start
                    pulse_t[mu_nb, start:ltime] = pt_segment[:valid_len]

            pulse_t[mu_nb, :] = signed_square(pulse_t[mu_nb, :])
            spikes = find_refractory_peaks(pulse_t[mu_nb, :], fsamp, min_isi_sec=_MIN_ISI_SEC)

            if len(spikes) > 1:
                high_spikes, _, _ = split_by_amplitude(
                    pulse_t[mu_nb, :], spikes, kmeans_iter=_KMEANS_ITER
                )
                distime.append(high_spikes)
            else:
                distime.append(spikes)

            mu_nb += 1

    return pulse_t, distime


def rem_duplicates(
    pulse_t: np.ndarray,
    distime: list[np.ndarray],
    distime_ref: list[np.ndarray] | None,
    maxlag: int,
    jitter: float,
    tol: float,
    fsamp: float,
) -> tuple[np.ndarray, list[np.ndarray], list[int]]:
    """Remove duplicated motor units based on lag-aware spike-train overlap.

    MUs with an empty spike train (``len(distime[i]) == 0``) are skipped and
    excluded from the output — they are never added to ``pulsenew``,
    ``distimenew``, or ``kept_indices``.  Callers reading ``kept_indices``
    should be aware that the returned indices may be a strict subset of
    ``range(n_mus)`` when some MUs have no spikes.
    """

    if distime_ref is None:
        distime_ref = distime

    jit = int(round(jitter * fsamp))

    n_mus = pulse_t.shape[0]
    l_sig = pulse_t.shape[1]

    distimmp = []
    distimmp_sets = []
    kept_indices = []

    for i in range(n_mus):
        d_times = np.asarray(distime_ref[i], dtype=int)
        if len(d_times) > 0:
            d_times = d_times[d_times < l_sig]
            expanded_set = set(d_times.tolist())
            for j in range(1, jit + 1):
                expanded_set.update((d_times - j).tolist())
                expanded_set.update((d_times + j).tolist())

            expanded = np.array(list(expanded_set), dtype=int)
            expanded = expanded[(expanded >= 0) & (expanded < l_sig)]
            distimmp.append(expanded)
            distimmp_sets.append(set(expanded.tolist()))
        else:
            distimmp.append(np.array([]))
            distimmp_sets.append(set())

    pulsenew = []
    distimenew = []
    active_mus = np.ones(n_mus, dtype=bool)

    for i in range(n_mus):
        if not active_mus[i]:
            continue
        ref_expanded = distimmp[i]
        if len(ref_expanded) == 0:
            logger.debug("rem_duplicates: skipping MU %d (empty spike train)", i)
            continue
        duplicates = [i]

        for j in range(i + 1, n_mus):
            if not active_mus[j]:
                continue
            target_expanded = distimmp[j]
            if len(target_expanded) == 0:
                continue
            ref_set = distimmp_sets[i]
            tgt_set = distimmp_sets[j]
            best_corr = 0.0
            best_lag = 0
            # Normalise by the jitter-expanded set sizes so the overlap
            # coefficient stays in [0, 1] (the overlap counts expanded samples).
            norm = np.sqrt(max(len(ref_expanded), 1) * max(len(target_expanded), 1))
            # The lag gate is rescaled by (2*jit+1) to preserve the behaviour of
            # the original 0.2 threshold, which was tuned against raw spike
            # counts. The expanded norm is ~(2*jit+1) larger, so the same
            # overlap now yields a ~(2*jit+1) smaller coefficient; dividing the
            # gate by the same factor keeps the alignment decision unchanged.
            lag_gate = 0.2 / (2 * jit + 1)
            lag_range = range(-2 * maxlag, 2 * maxlag + 1)
            for lag in lag_range:
                shifted = {t + lag for t in tgt_set if 0 <= t + lag < l_sig}
                overlap = len(ref_set & shifted)
                corr_val = overlap / norm if norm > 0 else 0
                if corr_val > best_corr:
                    best_corr = corr_val
                    best_lag = lag
            aligned_target = target_expanded + best_lag if best_corr > lag_gate else target_expanded
            common = np.intersect1d(ref_expanded, aligned_target)
            if len(common) > 0:
                common = np.sort(common)
                filtered_common = [common[0]]
                for k in range(1, len(common)):
                    if common[k] != common[k - 1] + 1:
                        filtered_common.append(common[k])
                n_common = len(filtered_common)
            else:
                n_common = 0
            len_ref = len(distime[i])
            len_target = len(distime[j])

            score = n_common / max(len_ref, len_target) if max(len_ref, len_target) > 0 else 0
            if score >= tol:
                duplicates.append(j)

        covs = []
        for idx_dup in duplicates:
            spikes = distime[idx_dup]
            cov = isi_cov(spikes, 1.0, fallback=100.0)
            covs.append(cov)

        best_idx_local = int(np.argmin(covs))
        best_idx = duplicates[best_idx_local]

        distimenew.append(distime[best_idx])
        pulsenew.append(pulse_t[best_idx, :])
        kept_indices.append(best_idx)

        for idx_dup in duplicates:
            active_mus[idx_dup] = False

    return np.array(pulsenew), distimenew, kept_indices
