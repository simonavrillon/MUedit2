"""float32 compute: dtypes are kept, accumulations run in float64, stale output memory is ignored."""

from __future__ import annotations

import tracemalloc

import numpy as np
import pytest

from muedit.adapt_decomp.adaptation import AdaptiveDecomp
from muedit.decomp.algorithm import (
    _stream_full_trace,
    covariance,
    fixed_point_alg,
    get_spikes,
    vec_mat,
)
from muedit.signal.decomp_primitives import extend_signal, zeroed_matmul
from muedit.signal.filters import demean, notch_inplace
from muedit.signal.streaming import StreamedExtender
from tests._synthetic_emg import FSAMP, motor_unit_emg


@pytest.fixture(scope="module")
def window32() -> np.ndarray:
    """A float32 extended window (64 rows x ~20k columns)."""
    emg = motor_unit_emg(seed=5, n_samples=20_000, activity=(0, 20_000))[:8]
    return extend_signal(demean(emg.astype(np.float32)), 8, dtype=np.float32)


def test_extend_signal_keeps_the_requested_dtype() -> None:
    x = np.ones((3, 10), dtype=np.float32)
    assert extend_signal(x, 4, dtype=np.float32).dtype == np.float32
    assert extend_signal(x, 4).dtype == np.float64


@pytest.mark.parametrize("samples_first", [False, True])
def test_streamed_batches_keep_the_extender_dtype(samples_first: bool) -> None:
    source = np.ones((3, 50), dtype=np.float64)
    extender = StreamedExtender(source, 4, dtype=np.float32, samples_first=samples_first)
    assert extender.read(10, 30).dtype == np.float32


def test_demean_takes_the_mean_in_float64() -> None:
    x = (np.random.default_rng(0).standard_normal((4, 100_001)) + 1e4).astype(np.float32)
    out = demean(x)
    assert out.dtype == np.float32
    expected = (x - x.astype(np.float64).mean(axis=1, keepdims=True)).astype(np.float32)
    np.testing.assert_array_equal(out, expected)


def test_float32_notch_filters_in_float64() -> None:
    t = np.arange(20_001) / 2048.0
    x = np.random.default_rng(1).standard_normal((3, t.size)) + 4 * np.sin(2 * np.pi * 50 * t)
    x32, x64 = x.astype(np.float32), x.astype(np.float32).astype(np.float64)
    notch_inplace(x32, 2048.0)
    notch_inplace(x64, 2048.0)
    np.testing.assert_array_equal(x32, x64.astype(np.float32))


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


def test_one_row_products_ignore_stale_output_memory() -> None:
    """A grid or window with one MU projects through ``(1, k) @ (k, n)``, the same sgemv path."""
    rng = np.random.default_rng(0)
    m = rng.standard_normal((1024, 12345)).astype(np.float32)
    w = rng.standard_normal((1, 1024)).astype(np.float32)
    expected = w.astype(np.float64) @ m.astype(np.float64)
    for _ in range(20):
        stale = np.full((1, m.shape[1]), np.nan, dtype=np.float32)
        del stale
        out = zeroed_matmul(w, m)
        assert out.dtype == np.float32 and np.isfinite(out).all()
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


def test_single_mu_adaptive_separation_ignores_stale_output_memory() -> None:
    """``AdaptiveDecomp._separate`` with one MU over a batch of 520 samples (5.2 kHz, 100 ms)."""
    rng = np.random.default_rng(2)
    n_ext, n = 1024, 520
    model = AdaptiveDecomp.__new__(AdaptiveDecomp)
    model.sep_vectors = rng.standard_normal((1, n_ext)).astype(np.float32)
    whitened = rng.standard_normal((n_ext, n)).astype(np.float32)
    for _ in range(20):
        stale = np.full((1, n), np.nan, dtype=np.float32)
        del stale
        ipts = model._separate(whitened)
        assert ipts.shape == (n, 1) and np.isfinite(ipts).all()
