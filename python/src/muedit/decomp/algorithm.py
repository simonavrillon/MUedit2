"""Core decomposition math primitives (whitening, fixed-point ICA, spike ops)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
from scipy.linalg import eigh, inv

from muedit.decomp.types import ContrastFunc
from muedit.io.store import ArrayStore, RamStore
from muedit.models import BoolArray, FloatArray, IntArray
from muedit.signal.decomp_primitives import (
    DECOMP_MIN_ISI_SEC,
    POSTPROC_MIN_ISI_SEC,
    extend_signal,
    find_refractory_peaks,
    isi_cov,
    signed_square,
    split_by_amplitude,
    zeroed_matmul,
)
from muedit.signal.streaming import SampleSource, StreamedExtender, extend_mask

logger = logging.getLogger(__name__)

_FIXED_POINT_TOL = 1e-4
FIXED_POINT_MAXITER = 500
_MIN_ISI_SEC = DECOMP_MIN_ISI_SEC
_KMEANS_ITER = 10
DEDUP_MAXLAG_RATIO: int = 40
DEDUP_JITTER: float = 0.00025
_CHUNK_BYTES = 32 * 1024 * 1024
_FULL_TRACE_BATCH_BYTES = 64 * 1024 * 1024

__all__ = [
    "DEDUP_JITTER",
    "DEDUP_MAXLAG_RATIO",
    "FIXED_POINT_MAXITER",
    "batch_process_filters",
    "column_energy",
    "compute_silhouette",
    "covariance",
    "extend_signal",
    "fixed_point_alg",
    "get_spikes",
    "minimize_isi_covariance",
    "pca_extended_signal",
    "rem_duplicates",
    "subtract_mu_waveforms",
    "whiten_extended_signal",
    "whiten_inplace",
    "window_trim",
]


def vec_mat(v: FloatArray, m: FloatArray) -> FloatArray:
    """``v @ m`` in ``m``'s dtype, written into a zeroed output (see ``zeroed_matmul``)."""
    return zeroed_matmul(v.astype(m.dtype), m)


def window_trim(n_samples: int, fsamp: float, edges_sec: float, ex_factor: int) -> int:
    """Columns cut from each end of an extended window: ``edges_sec``, and never the zero-padded ones."""
    # extend_signal zero-pads ex_factor - 1 columns at each end; they must never reach PCA
    # or FastICA, even when edges_sec is 0 or the window is too short for it.
    incomplete = max(0, ex_factor - 1)
    for trim in (max(int(round(fsamp * edges_sec)), incomplete), incomplete):
        if n_samples > 2 * trim:
            return trim
    return 0


def _chunk_cols(n_rows: int) -> int:
    """Columns per chunk so one float64 chunk stays near ``_CHUNK_BYTES``."""
    return max(1, _CHUNK_BYTES // (8 * max(1, n_rows)))


def _column_chunks(
    n_cols: int, n_rows: int, columns: IntArray | None = None
) -> list[slice] | list[IntArray]:
    """Split all columns, or the given column indices, into memory-bounded chunks."""
    step = _chunk_cols(n_rows)
    if columns is None:
        return [slice(c, min(c + step, n_cols)) for c in range(0, n_cols, step)]
    return [columns[c : c + step] for c in range(0, columns.size, step)]


def covariance(signal: FloatArray, columns: IntArray | None = None) -> FloatArray:
    """Biased row covariance of ``signal`` (or of its ``columns``), built in float64 column chunks."""
    n_rows, n_cols = signal.shape
    chunks = _column_chunks(n_cols, n_rows, columns)
    n = n_cols if columns is None else columns.size
    if columns is None:
        mean = signal.mean(axis=1, dtype=np.float64)
    else:
        mean = np.zeros(n_rows)
        for cols in chunks:
            mean += signal[:, cols].sum(axis=1, dtype=np.float64)
        mean /= n
    cov = np.zeros((n_rows, n_rows))
    for cols in chunks:
        block = signal[:, cols] - mean[:, None]  # float64 even when ``signal`` is float32
        cov += block @ block.T
    cov /= n
    return cov


def pca_extended_signal(
    signal: FloatArray, columns: IntArray | None = None
) -> tuple[FloatArray, FloatArray]:
    """Estimate PCA basis/eigenvalues for extended signal whitening."""
    cov_matrix = covariance(signal, columns)
    eigenvalues, eigenvectors = eigh(cov_matrix)

    idx = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[idx]
    eigenvectors = eigenvectors[:, idx]

    n_eigs = len(eigenvalues)
    rank_tolerance = np.mean(eigenvalues[n_eigs // 2 :])

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
    signal: FloatArray,
    eigenvectors: FloatArray,
    eigenvalues_diag: FloatArray,
    inplace: bool = False,
) -> tuple[FloatArray, FloatArray]:
    """Whiten extended signal and return the whitening matrix."""
    inv_sqrt_d = inv(np.sqrt(eigenvalues_diag))

    whitening_matrix = eigenvectors @ inv_sqrt_d @ eigenvectors.T
    if not inplace:
        return whitening_matrix @ signal, whitening_matrix
    return whiten_inplace(signal, whitening_matrix), whitening_matrix


def whiten_inplace(signal: FloatArray, whitening_matrix: FloatArray) -> FloatArray:
    """Overwrite ``signal`` with ``whitening_matrix @ signal``, one column chunk at a time.

    The product is float64 (a float32 chunk is promoted), then stored in ``signal``'s dtype.
    """
    # Each output column depends only on the same input column, so chunks can
    # be written back without a second full-size array.
    for cols in _column_chunks(signal.shape[1], signal.shape[0]):
        signal[:, cols] = whitening_matrix @ signal[:, cols]
    return signal


def column_energy(x: FloatArray) -> FloatArray:
    """``np.sum(x * x, axis=0)`` summed in float64, without a full-size temporary."""
    out = np.empty(x.shape[1])
    for cols in _column_chunks(x.shape[1], x.shape[0]):
        block = x[:, cols]
        out[cols] = np.sum(block * block, axis=0, dtype=np.float64)
    return out


def fixed_point_alg(
    w: FloatArray,
    x: FloatArray,
    basis: FloatArray,
    maxiter: int,
    contrast_func: ContrastFunc,
) -> FloatArray:
    """Run one-unit FastICA fixed-point iterations with orthogonalization.

    Projections run in ``x``'s dtype; ``w`` and its updates stay float64.
    """
    k = 0
    delta = 1.0
    basis_bt = basis @ basis.T
    n_samples = x.shape[1]

    while delta > _FIXED_POINT_TOL and k < maxiter:
        w_last = w.copy()
        # vec_mat casts w, never x: a float64 w would promote the whole window to float64.
        wtx = vec_mat(w_last, x)

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

        a = np.mean(gp, dtype=np.float64)
        w = (x @ g.T).astype(np.float64) / n_samples - a * w_last
        w = w - basis_bt @ w
        w_norm = np.linalg.norm(w)
        if w_norm == 0:
            logger.warning(
                "FastICA: separating vector collapsed to zero norm after "
                "%d iterations; stopping early.",
                k,
            )
            break
        w = w / w_norm

        k += 1
        delta = float(abs(abs(np.dot(w.flatten(), w_last.flatten())) - 1))

    return w


def _pulse_train(w: FloatArray, x: FloatArray) -> FloatArray:
    """Project source and apply signed-squared nonlinearity, in ``x``'s dtype."""
    return signed_square(vec_mat(w, x)).flatten()


def _detect_peaks(icasig: FloatArray, fsamp: float) -> IntArray:
    """Detect candidate spikes with refractory-distance peak picking."""
    return find_refractory_peaks(icasig, fsamp, min_isi_sec=_MIN_ISI_SEC)


def get_spikes(
    w: FloatArray,
    x: FloatArray,
    fsamp: float,
) -> tuple[FloatArray, IntArray]:
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
    w: FloatArray,
    x: FloatArray,
    cov: float,
    fsamp: float,
) -> tuple[FloatArray, IntArray, float]:
    """Refine separating vector by minimizing ISI coefficient of variation."""
    cov_last = cov + 0.1
    spikes = np.array([], dtype=int)

    best_w = w.copy()
    best_spikes = spikes
    best_cov = np.inf

    while cov < cov_last:
        cov_last = cov
        w_detect = w.copy()

        _, spikes = get_spikes(w, x, fsamp)

        if len(spikes) < 2:
            break

        cov = isi_cov(spikes, fsamp)

        if cov < best_cov:
            best_cov = cov
            best_spikes = spikes
            best_w = w_detect

        w = np.sum(x[:, spikes], axis=1, dtype=np.float64)

    if len(best_spikes) < 2:
        _, spikes = get_spikes(best_w, x, fsamp)
        return best_w, spikes, isi_cov(spikes, fsamp)

    return best_w, best_spikes, best_cov


def compute_silhouette(
    x: FloatArray,
    w: FloatArray,
    fsamp: float,
) -> tuple[FloatArray, IntArray, float]:
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
    x: FloatArray,
    spikes: IntArray,
    fsamp: float,
    win: float,
) -> list[tuple[int, int]]:
    """Subtract the averaged MU waveform from ``x`` at every spike, in place.

    Returns the ``[start, stop)`` column runs that changed, merged and ascending.
    """
    window_l = int(np.round(win * fsamp))
    n_cols = x.shape[1]

    spikes = np.asarray(spikes, dtype=int)
    valid_spikes = spikes[(spikes >= window_l) & (spikes < n_cols - window_l)]
    if valid_spikes.size == 0:
        return []

    # Summed window by window: no (rows, spikes, window) copy of x.
    waveforms = np.zeros((x.shape[0], 2 * window_l + 1))
    for s in valid_spikes:
        waveforms += x[:, s - window_l : s + window_l + 1]
    waveforms /= valid_spikes.size

    for s in valid_spikes:
        x[:, s - window_l : s + window_l + 1] -= waveforms
    return _merged_windows(np.sort(valid_spikes), window_l)


def _merged_windows(centers: IntArray, half: int) -> list[tuple[int, int]]:
    """The union of ``[c - half, c + half + 1)`` over the sorted ``centers``, as disjoint runs."""
    starts, stops = centers - half, centers + half + 1
    first = np.flatnonzero(np.r_[True, starts[1:] > np.maximum.accumulate(stops)[:-1]])
    return [
        (int(s), int(e))
        for s, e in zip(starts[first], np.maximum.reduceat(stops, first), strict=True)
    ]


def _dewhitened_filters(
    filters: FloatArray,
    whiten_mat: FloatArray,
    win_mean: FloatArray | None,
    ex_factor: int,
) -> tuple[FloatArray, FloatArray | None]:
    """Filters applicable to the raw extension, and the cumulative window-mean correction."""
    w_dewhite = whiten_mat.T @ filters  # (n_ext, n_mu)
    if win_mean is None:
        return w_dewhite, None
    n_ch = win_mean.size
    # Delay k of sample t sees win_mean only when t >= k, so the correction of the
    # first ex_factor - 1 samples (recording start) is a partial sum.
    per_delay = np.einsum("kcm,c->km", w_dewhite.reshape(ex_factor, n_ch, -1), win_mean)
    return w_dewhite, np.cumsum(per_delay, axis=0)


def _stream_full_trace(
    source: StreamedExtender,
    w_dewhite: FloatArray,
    corr_cum: FloatArray | None,
    out: FloatArray,
    rows: IntArray,
) -> None:
    """Write the signed-squared projection of every filter over the whole trace into ``out[rows]``.

    The projection runs in the extender's dtype; a grid with one MU is a one-row product,
    hence ``zeroed_matmul``.
    """
    itemsize = np.dtype(source.dtype).itemsize
    step = max(source.ex_factor, _FULL_TRACE_BATCH_BYTES // (itemsize * source.n_extended))
    last_delay = source.ex_factor - 1
    w_t = w_dewhite.T.astype(source.dtype)
    for start in range(0, source.n_samples, step):
        stop = min(start + step, source.n_samples)
        pt = zeroed_matmul(w_t, source.read(start, stop))
        if corr_cum is not None:
            pt -= corr_cum[np.minimum(np.arange(start, stop), last_delay)].T
        out[rows, start:stop] = signed_square(pt)


def _detect_row(pt: FloatArray, fsamp: float, artifact_mask: BoolArray | None) -> IntArray:
    """Mask and detect one pulse train (global peak picking + k-means split)."""
    if artifact_mask is not None:
        pt[artifact_mask] = 0.0
    spikes = find_refractory_peaks(pt, fsamp, min_isi_sec=POSTPROC_MIN_ISI_SEC)
    if len(spikes) > 1:
        spikes, _, _ = split_by_amplitude(pt, spikes, kmeans_iter=_KMEANS_ITER)
    if artifact_mask is not None and len(spikes) > 0:
        spikes = spikes[~artifact_mask[spikes]]
    return spikes


def batch_process_filters(
    mu_filters_by_window: dict[int, FloatArray],
    whitened_windows: dict[int, FloatArray] | Callable[[int], FloatArray],
    coordinates: list[int],
    ltime: int,
    fsamp: float,
    whiten_mat_by_window: dict[int, FloatArray] | None = None,
    grid_data: Mapping[int, SampleSource] | None = None,
    window_to_grid: dict[int, int] | None = None,
    win_means_by_window: dict[int, FloatArray] | None = None,
    artifact_mask: BoolArray | None = None,
    pulse_dtype: type[np.floating[Any]] = np.float32,
    store: ArrayStore | None = None,
    work_dtype: type[np.floating[Any]] | np.dtype[np.floating[Any]] = np.float64,
) -> tuple[FloatArray, list[IntArray]]:
    """Apply MU filters over their windows, or streamed over each grid's ``grid_data`` (full trace).

    The pulse trains are written into ``store`` (heap by default) as they are produced. The
    full-trace projection runs in ``work_dtype``; a window's, in the window's dtype.
    """
    sorted_wins = sorted(mu_filters_by_window.keys())
    first_row: dict[int, int] = {}
    total_mus = 0
    for nwin in sorted_wins:
        first_row[nwin] = total_mus
        if mu_filters_by_window[nwin].size > 0:
            total_mus += mu_filters_by_window[nwin].shape[1]

    if total_mus == 0:
        return np.array([]), []

    by_grid: dict[int, list[int]] = {}
    for nwin in sorted_wins:
        if mu_filters_by_window[nwin].size > 0:
            g = window_to_grid[nwin] if window_to_grid is not None else 0
            by_grid.setdefault(g, []).append(nwin)
    ex_by_grid = {
        g: mu_filters_by_window[wins[0]].shape[0] // grid_data[g].shape[0]
        if grid_data is not None
        else 1
        for g, wins in by_grid.items()
    }
    mask_by_grid = {
        g: None if artifact_mask is None else extend_mask(artifact_mask, ex)
        for g, ex in ex_by_grid.items()
    }

    store = store if store is not None else RamStore()
    pulse_t = store.allocate("pulse_all", (total_mus, ltime), pulse_dtype, zero=True)
    spikes_by_row: dict[int, IntArray] = {}

    if grid_data is not None and whiten_mat_by_window is not None:
        for g, wins in by_grid.items():
            source = StreamedExtender(grid_data[g], ex_by_grid[g], dtype=work_dtype)
            w_parts: list[FloatArray] = []
            corr_parts: list[FloatArray] = []
            rows: list[IntArray] = []
            for nwin in wins:
                filters = mu_filters_by_window[nwin]
                w_dewhite, corr_cum = _dewhitened_filters(
                    filters,
                    whiten_mat_by_window[nwin],
                    win_means_by_window[nwin] if win_means_by_window is not None else None,
                    source.ex_factor,
                )
                w_parts.append(w_dewhite)
                if corr_cum is not None:
                    corr_parts.append(corr_cum)
                rows.append(first_row[nwin] + np.arange(filters.shape[1]))
            unit_rows = np.concatenate(rows)
            _stream_full_trace(
                source,
                np.hstack(w_parts),
                np.hstack(corr_parts) if corr_parts else None,
                pulse_t,
                unit_rows,
            )
            for mu_nb in unit_rows:
                pt = pulse_t[mu_nb].astype(np.float64)
                spikes_by_row[int(mu_nb)] = _detect_row(pt, fsamp, mask_by_grid[g])
                pulse_t[mu_nb] = pt
        return pulse_t, [spikes_by_row[i] for i in range(total_mus)]

    for g, wins in by_grid.items():
        for nwin in wins:
            filters = mu_filters_by_window[nwin]
            start = coordinates[nwin * 2]
            w_win = whitened_windows(nwin) if callable(whitened_windows) else whitened_windows[nwin]
            segment_len = w_win.shape[1]
            # Every filter of the window in one product: the window is read once, not per MU.
            projections = zeroed_matmul(filters.T.astype(w_win.dtype), w_win)
            for j, projection in enumerate(projections):
                pt = np.zeros(ltime)
                pt[start : start + segment_len] = projection[: ltime - start]
                pt = signed_square(pt)
                spikes_by_row[first_row[nwin] + j] = _detect_row(pt, fsamp, mask_by_grid[g])
                pulse_t[first_row[nwin] + j] = pt
            # Released before the next window is reconstructed, not when the name rebinds.
            del w_win

    return pulse_t, [spikes_by_row[i] for i in range(total_mus)]


def _jittered_times(spikes: IntArray, jitter_samples: int, n_samples: int) -> IntArray:
    """Sorted unique samples within ``jitter_samples`` of a spike, inside ``[0, n_samples)``."""
    d = np.asarray(spikes, dtype=np.int64)
    d = d[d < n_samples]
    expanded = np.unique((d[:, None] + np.arange(-jitter_samples, jitter_samples + 1)).ravel())
    return expanded[(expanded >= 0) & (expanded < n_samples)].astype(np.int32)


def _lag_overlap(ref: IntArray, target: IntArray, max_lag: int) -> IntArray:
    """Number of ``target`` samples that land on a ``ref`` sample, per lag in ``[-max_lag, max_lag]``."""
    # Both are sorted and unique, so the count at a lag is the number of (ref, target)
    # pairs that differ by it: only pairs within max_lag of each other are formed.
    lo = np.searchsorted(ref, target - max_lag, side="left")
    counts = np.searchsorted(ref, target + max_lag, side="right") - lo
    pair_target = np.repeat(np.arange(target.size), counts)
    pair_ref = np.repeat(lo - np.cumsum(counts) + counts, counts) + np.arange(counts.sum())
    return np.bincount(ref[pair_ref] - target[pair_target] + max_lag, minlength=2 * max_lag + 1)


def rem_duplicates(
    distime: list[IntArray],
    distime_ref: list[IntArray] | None,
    maxlag: int,
    jitter: float,
    tol: float,
    fsamp: float,
    n_samples: int,
) -> list[int]:
    """Indices of the motor units kept after removing duplicates by lag-aware spike-train overlap."""

    if distime_ref is None:
        distime_ref = distime

    jitter_samples = int(round(jitter * fsamp))
    n_mus = len(distime)
    jittered_distimes = [_jittered_times(d, jitter_samples, n_samples) for d in distime_ref]

    kept_indices: list[int] = []
    active_mus = np.ones(n_mus, dtype=bool)

    lag_gate = 0.2

    for i in range(n_mus):
        if not active_mus[i]:
            continue
        ref_expanded = jittered_distimes[i]
        if len(ref_expanded) == 0:
            logger.debug("rem_duplicates: skipping MU %d (empty spike train)", i)
            continue
        duplicates = [i]

        for j in range(i + 1, n_mus):
            if not active_mus[j]:
                continue
            target_expanded = jittered_distimes[j]
            if len(target_expanded) == 0:
                continue
            norm = np.sqrt(max(len(ref_expanded), 1) * max(len(target_expanded), 1))
            overlap = _lag_overlap(ref_expanded, target_expanded, 2 * maxlag)
            max_overlap = int(overlap.max())
            if max_overlap > 0:
                best_lag = int(np.argmax(overlap)) - 2 * maxlag
                best_corr = max_overlap / norm if norm > 0 else 0.0
            else:
                best_lag = 0
                best_corr = 0.0
            aligned_target = target_expanded + best_lag if best_corr > lag_gate else target_expanded
            common = np.intersect1d(ref_expanded, aligned_target, assume_unique=True)
            if len(common) > 0:
                common = np.sort(common)
                # Count runs of consecutive samples as one shared discharge.
                n_common = 1 + int(np.count_nonzero(np.diff(common) != 1))
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
        kept_indices.append(duplicates[best_idx_local])

        for idx_dup in duplicates:
            active_mus[idx_dup] = False

    return kept_indices
