"""Tests for the delay embedding of :mod:`muedit.signal.decomp_primitives`."""

from __future__ import annotations

import numpy as np
import pytest

from muedit.signal.decomp_primitives import (
    enforce_refractory,
    extend_mask,
    extend_signal,
    extend_signal_mask,
    extension_factor,
    zeroed_matmul,
)


def test_extend_signal_stacks_delayed_copies() -> None:
    data = np.random.default_rng(0).standard_normal((8, 500))
    extended = extend_signal(data, 4)
    assert extended.shape == (8 * 4, 500 + 4 - 1)
    assert extended.dtype == np.float64
    for m in range(4):
        delayed = extended[8 * m : 8 * (m + 1)]
        np.testing.assert_array_equal(delayed[:, m : m + 500], data)
        assert not delayed[:, :m].any()


@pytest.mark.parametrize("ex_factor", [1, 3])
def test_mask_follows_the_extended_columns(ex_factor: int) -> None:
    """A column is masked when any sample it delays is; both conventions agree on the first n."""
    mask = np.zeros(10, dtype=bool)
    mask[[0, 6]] = True
    touched = extend_signal(np.where(mask, 1.0, 0.0)[None, :], ex_factor).any(axis=0)
    padded = extend_signal_mask(mask, ex_factor)
    np.testing.assert_array_equal(padded, touched)
    np.testing.assert_array_equal(padded[: mask.size], extend_mask(mask, ex_factor))


@pytest.mark.parametrize(
    ("nbextchan", "n_channels", "expected"),
    [(1000, 64, 16), (10, 64, 1), (1000, 0, 1000)],
)
def test_extension_factor(nbextchan: int, n_channels: int, expected: int) -> None:
    assert extension_factor(nbextchan, n_channels) == expected


def test_extend_signal_keeps_the_requested_dtype() -> None:
    x = np.ones((3, 10), dtype=np.float32)
    assert extend_signal(x, 4, dtype=np.float32).dtype == np.float32
    assert extend_signal(x, 4).dtype == np.float64


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


class TestEnforceRefractory:
    """The seam guard the adaptive branch applies to its per-batch spike picks."""

    def test_keeps_the_larger_peak_of_a_too_close_pair(self) -> None:
        values = np.zeros(100)
        values[[40, 44]] = [1.0, 3.0]
        kept = enforce_refractory(np.array([40, 44]), values, fsamp=2000.0, min_isi_sec=0.005)
        assert kept.tolist() == [44]

    def test_spikes_a_refractory_period_apart_are_kept(self) -> None:
        values = np.ones(100)
        # 10 samples = 5 ms at 2 kHz, exactly the refractory distance.
        kept = enforce_refractory(np.array([40, 50]), values, fsamp=2000.0, min_isi_sec=0.005)
        assert kept.tolist() == [40, 50]

    def test_a_kept_spike_gates_the_ones_after_it(self) -> None:
        """Survivors are compared against the last kept spike, not the last seen one."""
        values = np.zeros(100)
        values[[40, 43, 46]] = [3.0, 1.0, 2.0]
        kept = enforce_refractory(np.array([40, 43, 46]), values, fsamp=2000.0, min_isi_sec=0.005)
        assert kept.tolist() == [40]

    def test_short_trains_pass_through(self) -> None:
        values = np.ones(100)
        assert enforce_refractory(np.array([7]), values, fsamp=2000.0).tolist() == [7]
        assert enforce_refractory(np.array([], dtype=int), values, fsamp=2000.0).size == 0
