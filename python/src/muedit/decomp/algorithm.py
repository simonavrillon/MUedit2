"""Core decomposition math primitives (whitening, fixed-point ICA, spike ops)."""

import numpy as np
from scipy.cluster.vq import kmeans2
from scipy.linalg import eigh, inv
from scipy.signal import convolve, find_peaks

_FIXED_POINT_TOL = 1e-4



def extend_signal(signal, exfactor):
    """Create delayed channel extension used by convolutive source separation."""
    rows, cols = signal.shape
    extended_rows = rows * exfactor
    extended_cols = cols + exfactor - 1
    esample = np.zeros((extended_rows, extended_cols))

    for m in range(exfactor):
        esample[m * rows:(m + 1) * rows, m:cols + m] = signal

    return esample


def pca_extended_signal(signal):
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
        lower_limit_value = (
            eigenvalues[max_last_eig - 1] + eigenvalues[max_last_eig]
        ) / 2
    else:
        lower_limit_value = rank_tolerance

    mask = eigenvalues > lower_limit_value
    eigenvectors_selected = eigenvectors[:, mask]
    eigenvalues_diag = np.diag(eigenvalues[mask])

    return eigenvectors_selected, eigenvalues_diag


def whiten_extended_signal(signal, eigenvectors, eigenvalues_diag):
    """Whiten extended signal and return whitening/dewhitening matrices."""
    sqrt_d = np.sqrt(eigenvalues_diag)
    inv_sqrt_d = inv(sqrt_d)

    whitening_matrix = eigenvectors @ inv_sqrt_d @ eigenvectors.T
    dewhitening_matrix = eigenvectors @ sqrt_d @ eigenvectors.T
    whiten_signals = whitening_matrix @ signal

    return whiten_signals, whitening_matrix, dewhitening_matrix


def fixed_point_alg(w, x, basis, maxiter, contrast_func):
    """Run one-unit FastICA fixed-point iterations with orthogonalization."""
    k = 0
    delta = np.ones(maxiter)
    basis_bt = basis @ basis.T
    n_samples = x.shape[1]

    while delta[k] > _FIXED_POINT_TOL and k < maxiter - 1:
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
        delta[k] = abs(np.dot(w.flatten(), w_last.flatten()) - 1)

    return w


def _pulse_train(w, x):
    """Project source and apply signed-squared nonlinearity."""
    wtx = w.T @ x
    return (wtx * np.abs(wtx)).flatten()


def _detect_peaks(icasig, fsamp):
    """Detect candidate spikes with refractory-distance peak picking."""
    distance = int(np.round(fsamp * 0.02))
    spikes, _ = find_peaks(icasig, distance=distance)

    return icasig, spikes


def get_spikes(w, x, fsamp):
    """Estimate spike times from one source using k-means amplitude split."""
    icasig = _pulse_train(w, x)
    icasig, spikes = _detect_peaks(icasig, fsamp)

    if len(spikes) <= 1:
        return icasig, np.asarray(spikes, dtype=int)

    centroids, labels = kmeans2(icasig[spikes], 2, iter=10, minit="++", missing="raise", seed=0)
    idx2 = int(np.argmax(centroids))
    spikes2 = spikes[labels == idx2]

    vals = icasig[spikes2]
    threshold = np.mean(vals) + 3 * np.std(vals)
    spikes2 = spikes2[vals <= threshold]

    return icasig, spikes2


def minimize_isi_covariance(w, x, cov, fsamp):
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

        isi = np.diff(spikes) / fsamp
        cov = np.std(isi) / np.mean(isi)

        w = np.sum(x[:, spikes], axis=1)

    if len(spikes_last) < 2:
        _, spikes_last = get_spikes(w, x, fsamp)

    return w_last, spikes_last, cov_last


def compute_silhouette(x, w, fsamp):
    """Compute silhouette-like separability score for detected spikes."""
    icasig = _pulse_train(w, x)
    icasig, spikes = _detect_peaks(icasig, fsamp)

    if len(spikes) <= 1:
        return icasig, np.array(spikes, dtype=int), 0.0


    centroids, labels = kmeans2(icasig[spikes], 2, iter=10, minit="++", missing="raise", seed=0)

    idx2 = int(np.argmax(centroids))
    other_idx = 1 - idx2
    spikes2 = spikes[labels == idx2]

    spike_cluster_vals = icasig[spikes][labels == idx2]


    within = float(np.sum((spike_cluster_vals - centroids[idx2]) ** 2))


    between = float(np.sum((spike_cluster_vals - centroids[other_idx]) ** 2))

    denom = max(within, between)
    sil = (between - within) / denom if denom > 0 else 0.0

    return icasig, spikes2, sil


def extract_muap_segments(mu_pulses, length_radius, y):
    """Extract waveform snippets around pulse indices."""
    pulses = np.asarray(mu_pulses, dtype=int).reshape(-1)
    window_size = 2 * length_radius + 1
    if pulses.size == 0:
        return np.zeros((0, window_size))

    valid_mask = (pulses >= length_radius) & (pulses < len(y) - length_radius)
    valid_pulses = pulses[valid_mask]
    if valid_pulses.size == 0:
        return np.zeros((0, window_size))

    offsets = np.arange(-length_radius, length_radius + 1, dtype=int)
    idx = valid_pulses[:, None] + offsets[None, :]
    return np.asarray(y, dtype=float)[idx]


def subtract_mu_waveforms(x, spikes, fsamp, win):
    """Subtract averaged MU waveform estimate from multichannel signal."""
    window_l = int(np.round(win * fsamp))
    firings = np.zeros(x.shape[1])
    firings[spikes] = 1

    emg_temp = np.zeros_like(x)

    for ch in range(x.shape[0]):
        temp = extract_muap_segments(spikes, window_l, x[ch, :])
        if len(temp) > 0:
            waveform = np.mean(temp, axis=0)
            emg_temp[ch, :] = convolve(firings, waveform, mode="same")

    return x - emg_temp


def batch_process_filters(
    mu_filters_by_window,
    whitened_windows,
    coordinates,
    ltime,
    fsamp,
    nwindows_per_grid,
):
    """Apply MU filters across windows and reconstruct pulse trains/spike times."""
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
        grid_idx = nwin // max(1, nwindows_per_grid)

        for j in range(n_filters):
            current_filter = filters[:, j]

            for nwin2 in whitened_windows:
                if nwin2 // max(1, nwindows_per_grid) != grid_idx:
                    continue
                start = coordinates[nwin2 * 2]
                segment_len = whitened_windows[nwin2].shape[1]
                pt_segment = np.dot(current_filter, whitened_windows[nwin2])
                if start + segment_len <= ltime:
                    pulse_t[mu_nb, start : start + segment_len] = pt_segment
                else:
                    valid_len = ltime - start
                    pulse_t[mu_nb, start:ltime] = pt_segment[:valid_len]

            pulse_t[mu_nb, :] = pulse_t[mu_nb, :] * np.abs(pulse_t[mu_nb, :])
            distance = int(np.round(fsamp * 0.02))
            spikes, _ = find_peaks(pulse_t[mu_nb, :], distance=distance)

            if len(spikes) > 1:
                centroids, labels = kmeans2(pulse_t[mu_nb, spikes], 2, iter=10, minit="++", missing="raise", seed=0)
                idx = np.argmax(centroids)
                distime.append(spikes[labels == idx])
            else:
                distime.append(spikes)

            mu_nb += 1

    return pulse_t, distime


def rem_duplicates(pulse_t, distime, distime_ref, maxlag, jitter, tol, fsamp):
    """Remove duplicated motor units based on lag-aware spike-train overlap."""

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
            norm = np.sqrt(max(len(distime[i]), 1) * max(len(distime[j]), 1))
            lag_range = range(-2 * maxlag, 2 * maxlag + 1)
            for lag in lag_range:
                shifted = {t + lag for t in tgt_set if 0 <= t + lag < l_sig}
                overlap = len(ref_set & shifted)
                corr_val = overlap / norm if norm > 0 else 0
                if corr_val > best_corr:
                    best_corr = corr_val
                    best_lag = lag
            aligned_target = (
                target_expanded + best_lag if best_corr > 0.2 else target_expanded
            )
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

            score = (
                n_common / max(len_ref, len_target)
                if max(len_ref, len_target) > 0
                else 0
            )
            if score >= tol:
                duplicates.append(j)

        if len(duplicates) > 0:
            covs = []
            for idx_dup in duplicates:
                spikes = distime[idx_dup]
                if len(spikes) > 1:
                    isi = np.diff(spikes)
                    cov = np.std(isi) / np.mean(isi) if np.mean(isi) > 0 else 100
                else:
                    cov = 100
                covs.append(cov)

            best_idx_local = int(np.argmin(covs))
            best_idx = duplicates[best_idx_local]

            distimenew.append(distime[best_idx])
            pulsenew.append(pulse_t[best_idx, :])
            kept_indices.append(best_idx)

            for idx_dup in duplicates:
                active_mus[idx_dup] = False

    return np.array(pulsenew), distimenew, kept_indices
