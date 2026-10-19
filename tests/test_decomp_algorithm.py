"""Tests for :mod:`muedit.decomp.algorithm`: whitening, FastICA, peel-off, filter application
and duplicate removal."""

from __future__ import annotations

import tracemalloc
from itertools import pairwise

import numpy as np
import pytest

from muedit.decomp import algorithm
from muedit.decomp.algorithm import (
    DEDUP_JITTER,
    DEDUP_MAXLAG_RATIO,
    _stream_full_trace,
    batch_process_filters,
    column_energy,
    covariance,
    fixed_point_alg,
    get_spikes,
    pca_extended_signal,
    subtract_mu_waveforms,
    unique_mu_indices,
    vec_mat,
    whiten_extended_signal,
)
from muedit.models import FloatArray
from muedit.signal.decomp_primitives import extend_signal, isi_cov, signed_square
from muedit.signal.filters import demean
from muedit.signal.streaming import StreamedExtender
from tests._synthetic_emg import FSAMP, motor_unit_emg

WIN = 0.025


def _reference_subtract(x: np.ndarray, spikes: np.ndarray, fsamp: float, win: float) -> None:
    """The peel-off as it was written with a gathered (rows, spikes, window) copy."""
    window_l = int(np.round(win * fsamp))
    valid = spikes[(spikes >= window_l) & (spikes < x.shape[1] - window_l)]
    if valid.size == 0:
        return
    idx = valid[:, None] + np.arange(-window_l, window_l + 1)[None, :]
    waveforms = x[:, idx].mean(axis=1, dtype=np.float64)
    for s in valid:
        x[:, s - window_l : s + window_l + 1] -= waveforms


def _spikes(rng: np.random.Generator, n_cols: int) -> np.ndarray:
    # Clustered and edge spikes: overlapping windows, and some outside the valid range.
    spikes = np.r_[rng.choice(n_cols, 120, replace=False), [0, 3, n_cols - 2], [500, 510, 530]]
    return np.sort(np.unique(spikes))


RTOL = 1e-10


@pytest.fixture
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force several column chunks on test-sized inputs."""
    monkeypatch.setattr(algorithm, "_CHUNK_BYTES", 64 * 1024)


@pytest.fixture(scope="module")
def extended() -> np.ndarray:
    """A demeaned 8-channel window extended by 8 (64 rows x ~6k columns)."""
    emg = motor_unit_emg(seed=3, n_samples=6000, activity=(0, 6000))[:8].astype(np.float64)
    return extend_signal(emg - emg.mean(axis=1, keepdims=True), 8)


@pytest.fixture(scope="module")
def window32() -> np.ndarray:
    """A float32 extended window (64 rows x ~20k columns)."""
    emg = motor_unit_emg(seed=5, n_samples=20_000, activity=(0, 20_000))[:8]
    return extend_signal(demean(emg.astype(np.float32)), 8, dtype=np.float32)


# Default overlap score above which a MU pair is flagged a duplicate.
_DEDUP_TOL = 0.3


def _lagged(spikes: np.ndarray, lag: int, n_samples: int) -> np.ndarray:
    """Return ``spikes + lag`` clipped to ``[0, n_samples)``."""
    shifted = spikes + lag
    return shifted[(shifted >= 0) & (shifted < n_samples)]


def _jittered(
    spikes: np.ndarray, rng: np.random.Generator, max_j: int, n_samples: int
) -> np.ndarray:
    """Return ``spikes`` perturbed per-spike by up to ``+/- max_j`` samples."""
    perturbed = spikes + rng.integers(-max_j, max_j + 1, size=spikes.size)
    perturbed = np.sort(perturbed)
    return perturbed[(perturbed >= 0) & (perturbed < n_samples)]


def _subset(spikes: np.ndarray, rng: np.random.Generator, drop_frac: float) -> np.ndarray:
    """Return ``spikes`` with a ``drop_frac`` fraction of spikes removed."""
    keep = rng.random(spikes.size) > drop_frac
    return spikes[keep]


@pytest.mark.usefixtures("small_chunks")
class TestChunkedFastIca:
    """Chunked covariance and cached energy equal their whole-array definitions."""

    def test_covariance_matches_np_cov(self, extended: np.ndarray) -> None:
        np.testing.assert_allclose(covariance(extended), np.cov(extended, bias=True), rtol=RTOL)

    def test_covariance_on_columns_matches_fancy_index(self, extended: np.ndarray) -> None:
        cols = np.flatnonzero(np.arange(extended.shape[1]) % 7 != 3)
        np.testing.assert_allclose(
            covariance(extended, cols), np.cov(extended[:, cols], bias=True), rtol=RTOL
        )

    def test_column_energy_is_exact(self, extended: np.ndarray) -> None:
        np.testing.assert_array_equal(column_energy(extended), np.sum(extended**2, axis=0))


@pytest.mark.parametrize(
    ("n_samples", "edges_sec", "ex_factor", "expected"),
    [
        (2000, 0.2, 16, 400),  # edges_sec, wider than the 15 zero-padded columns
        (2000, 0.0, 16, 15),  # no edges: the padded columns still go
        (2000, 0.002, 16, 15),  # edges narrower than the padding
        (500, 0.2, 16, 15),  # too short for edges_sec, long enough for the padding
        (20, 0.2, 16, 0),  # too short for either
        (2000, 0.0, 1, 0),  # nothing extended, nothing padded
    ],
)
def test_window_trim(n_samples: int, edges_sec: float, ex_factor: int, expected: int) -> None:
    assert algorithm.window_trim(n_samples, 2000.0, edges_sec, ex_factor) == expected


def test_float32_covariance_accumulates_in_float64(window32: np.ndarray) -> None:
    np.testing.assert_allclose(
        covariance(window32), covariance(window32.astype(np.float64)), rtol=1e-10, atol=1e-10
    )


def test_fixed_point_never_promotes_the_window(window32: np.ndarray) -> None:
    """A float64 ``w`` against a float32 window must not copy the window as float64."""
    w = np.random.default_rng(2).standard_normal(window32.shape[0])
    w /= np.linalg.norm(w)
    basis = np.zeros((window32.shape[0], 0))
    tracemalloc.start()
    out = fixed_point_alg(w, window32, basis, 50, "skew")
    get_spikes(out, window32, FSAMP)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert out.dtype == np.float64
    assert peak < window32.nbytes, (
        f"peak {peak / 1e6:.1f} MB for a {window32.nbytes / 1e6:.1f} MB window"
    )


def test_vec_mat_ignores_stale_output_memory() -> None:
    """A NaN left in freed memory never reaches ``v @ m``.

    Accelerate's float32 ``cblas_sgemv`` (transposed) computes ``0 * old`` for some outputs
    when beta = 0 (here the last 39 of 20519 columns); a fresh buffer that reuses freed
    NaN memory then yields NaN. ``vec_mat`` writes into a zeroed buffer instead.
    """
    rng = np.random.default_rng(0)
    m = rng.standard_normal((1024, 20519)).astype(np.float32)
    v = rng.standard_normal(1024)
    expected = v @ m.astype(np.float64)
    for _ in range(20):
        stale = np.full(m.shape[1], np.nan, dtype=np.float32)
        del stale  # the next buffer of this size reuses its memory
        out = vec_mat(v, m)
        assert np.isfinite(out).all()
        np.testing.assert_allclose(out, expected, rtol=1e-4, atol=1e-3)


def test_single_mu_full_trace_ignores_stale_output_memory() -> None:
    """The streamed full-trace pass of a grid with one MU over one batch of 12345 samples."""
    rng = np.random.default_rng(1)
    n_ch, ex, n = 64, 16, 12345
    source = StreamedExtender(
        rng.standard_normal((n_ch, n)).astype(np.float32), ex, dtype=np.float32
    )
    w = rng.standard_normal((n_ch * ex, 1))
    for _ in range(10):
        out = np.zeros((1, n), dtype=np.float32)  # before the stale buffer, or it takes its memory
        stale = np.full((1, n), np.nan, dtype=np.float32)
        del stale
        _stream_full_trace(source, w, None, out, np.array([0]))
        assert np.isfinite(out).all()


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_peel_off_matches_the_gathered_mean(dtype: type) -> None:
    rng = np.random.default_rng(1)
    x: FloatArray = rng.standard_normal((40, 20_000)).astype(dtype)
    spikes = _spikes(rng, x.shape[1])
    expected = x.copy()
    _reference_subtract(expected, spikes, FSAMP, WIN)
    subtract_mu_waveforms(x, spikes, FSAMP, WIN)
    np.testing.assert_array_equal(x, expected)


def test_peel_off_reports_exactly_the_columns_it_changed() -> None:
    rng = np.random.default_rng(2)
    x = rng.standard_normal((8, 20_000))
    before = x.copy()
    runs = subtract_mu_waveforms(x, _spikes(rng, x.shape[1]), FSAMP, WIN)
    reported = np.zeros(x.shape[1], dtype=bool)
    for lo, hi in runs:
        reported[lo:hi] = True
    assert np.array_equal(reported, (x != before).any(axis=0))
    assert all(hi < lo for (_, hi), (lo, _) in pairwise(runs))


def test_peel_off_without_valid_spikes_changes_nothing() -> None:
    x = np.ones((4, 100))
    assert subtract_mu_waveforms(x, np.array([0, 99]), FSAMP, WIN) == []
    assert np.all(x == 1)


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_energy_updated_on_changed_runs_equals_a_full_recount(dtype: type) -> None:
    rng = np.random.default_rng(3)
    x: FloatArray = rng.standard_normal((300, 30_000)).astype(dtype)
    energy = column_energy(x)
    for lo, hi in subtract_mu_waveforms(x, _spikes(rng, x.shape[1]), FSAMP, WIN):
        energy[lo:hi] = column_energy(x[:, lo:hi])
    np.testing.assert_array_equal(energy, column_energy(x))


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_windowed_filters_give_the_per_filter_projections(dtype: type) -> None:
    rng = np.random.default_rng(4)
    windows: dict[int, FloatArray] = {
        0: rng.standard_normal((60, 5_000)).astype(dtype),
        1: rng.standard_normal((60, 4_000)).astype(dtype),
    }
    filters: dict[int, FloatArray] = {
        0: rng.standard_normal((60, 3)),
        1: rng.standard_normal((60, 1)),
    }
    starts = [100, 5_100, 6_000, 10_000]
    total_samples = 10_500
    pulse_trains, _ = batch_process_filters(
        filters,
        windows.__getitem__,
        starts,
        total_samples,
        FSAMP,
        grid_data={0: np.empty((60, 0))},  # the windowed route reads only its channel count
        window_to_grid={0: 0, 1: 0},
    )

    row = 0
    for win_global in (0, 1):
        start = starts[2 * win_global]
        for j in range(filters[win_global].shape[1]):
            expected = np.zeros(total_samples)
            expected[start : start + windows[win_global].shape[1]] = vec_mat(
                filters[win_global][:, j], windows[win_global]
            )[: total_samples - start]
            # Pulse trains are stored as float32 whatever the window's dtype.
            peak = float(np.max(expected**2))
            np.testing.assert_allclose(
                pulse_trains[row], signed_square(expected), rtol=1e-5, atol=1e-5 * peak
            )
            row += 1


def test_whitening_decorrelates(extended: np.ndarray) -> None:
    """Convolutive sphering: the whitened covariance is a projector."""
    eigvecs, eigvals = pca_extended_signal(extended)
    whitened, _ = whiten_extended_signal(extended.copy(), eigvecs, eigvals)

    n_comp = eigvecs.shape[1]
    cov_whitened = np.cov(whitened, bias=True)
    np.testing.assert_allclose(cov_whitened, cov_whitened.T, atol=1e-10)
    np.testing.assert_allclose(
        cov_whitened @ cov_whitened,
        cov_whitened,
        atol=1e-4,
        err_msg="whitened covariance must be idempotent (a projector)",
    )
    # n_comp eigenvalues ~1 (sphered), the rest ~0.
    eigvals_w = np.linalg.eigvalsh(cov_whitened)[::-1]
    np.testing.assert_allclose(eigvals_w[:n_comp], 1.0, atol=1e-3)
    np.testing.assert_allclose(eigvals_w[n_comp:], 0.0, atol=1e-4)


class TestRemDuplicatesCore:
    """Precise behavioural checks of ``unique_mu_indices`` on crafted spike trains."""

    FSAMP = 10240.0
    N_SAMPLES = 6000
    MAXLAG = round(FSAMP / DEDUP_MAXLAG_RATIO)

    def _run(self, dist: list[np.ndarray]) -> tuple[int, list[int]]:
        kept = unique_mu_indices(
            dist, self.MAXLAG, DEDUP_JITTER, _DEDUP_TOL, self.FSAMP, self.N_SAMPLES
        )
        return len(kept), [int(k) for k in kept]

    def _regular(self, period: int, start: int, count: int) -> np.ndarray:
        return np.arange(start, start + period * count, period, dtype=int)

    def test_exact_duplicate_collapsed(self) -> None:
        a = self._regular(200, 100, 20)
        kept, idx = self._run([a, a.copy()])
        assert kept == 1, f"exact duplicate not collapsed: {kept} survivors"
        assert idx == [0]

    def test_lagged_duplicate_collapsed(self) -> None:
        a = self._regular(200, 100, 20)
        kept, _idx = self._run([a, _lagged(a, 30, self.N_SAMPLES)])
        assert kept == 1, f"lagged duplicate not collapsed: {kept} survivors"

    def test_jittered_duplicate_collapsed(self) -> None:
        a = self._regular(200, 100, 20)
        rng = np.random.default_rng(7)
        jit = int(round(DEDUP_JITTER * self.FSAMP))
        kept, _idx = self._run([a, _jittered(a, rng, jit, self.N_SAMPLES)])
        assert kept == 1, f"jittered duplicate not collapsed: {kept} survivors"

    def test_subset_duplicate_collapsed(self) -> None:
        a = self._regular(200, 100, 20)
        rng = np.random.default_rng(3)
        kept, _idx = self._run([a, _subset(a, rng, 0.1)])
        assert kept == 1, f"subset duplicate not collapsed: {kept} survivors"

    def test_distinct_unit_preserved(self) -> None:
        """A low-firing, irregular MU is neither a duplicate of the regular MU nor vice-versa."""
        a = self._regular(200, 100, 20)
        b = np.array([250, 900, 1700, 2600, 3400, 4300, 5200], dtype=int)
        kept, idx = self._run([a, b])
        assert kept == 2, f"distinct unit wrongly collapsed: {kept} survivors"
        assert sorted(idx) == [0, 1]

    def test_empty_spike_train_excluded(self) -> None:
        """An MU with no spikes is skipped and never appears in the output."""
        a = self._regular(200, 100, 20)
        kept, idx = self._run([a, np.array([], dtype=int), a.copy()])
        # The empty MU (index 1) is dropped; the a/a-copy pair collapses to one.
        assert kept == 1, f"expected 1 survivor, got {kept}"
        assert 1 not in idx, f"empty-spike MU kept: {idx}"

    def test_kept_representative_is_lowest_cov(self) -> None:
        """Among a duplicate group the lowest-CoV member is the one retained."""
        a = self._regular(200, 100, 20)
        rng = np.random.default_rng(11)
        jit = int(round(DEDUP_JITTER * self.FSAMP))
        jittery = _jittered(a, rng, jit, self.N_SAMPLES)
        assert isi_cov(jittery, self.FSAMP) > isi_cov(a, self.FSAMP)
        _, idx = self._run([a, jittery])
        assert idx == [0], f"lowest-CoV representative not kept: {idx}"
