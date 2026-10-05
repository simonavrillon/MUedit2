"""Tests for the peel-off and filter-application primitives of ``muedit.decomp.algorithm``."""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from muedit.decomp.algorithm import (
    batch_process_filters,
    column_energy,
    subtract_mu_waveforms,
    vec_mat,
)
from muedit.models import FloatArray
from muedit.signal.decomp_primitives import signed_square

FSAMP = 2048.0
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
    ltime = 10_500
    pulse, _ = batch_process_filters(filters, windows, starts, ltime, FSAMP)

    row = 0
    for nwin in (0, 1):
        start = starts[2 * nwin]
        for j in range(filters[nwin].shape[1]):
            expected = np.zeros(ltime)
            expected[start : start + windows[nwin].shape[1]] = vec_mat(
                filters[nwin][:, j], windows[nwin]
            )[: ltime - start]
            # Pulse trains are stored as float32 whatever the window's dtype.
            peak = float(np.max(expected**2))
            np.testing.assert_allclose(
                pulse[row], signed_square(expected), rtol=1e-5, atol=1e-5 * peak
            )
            row += 1
