"""Tests for the min/max pyramids and their viewport envelopes (:mod:`muedit.signal.pyramid`)."""

from __future__ import annotations

import numpy as np
import pytest

from muedit.signal.pyramid import MinMaxPyramid, view
from muedit.storage import RamStore

N_SAMPLES = 40_011  # not a multiple of any level's bin


def _series(n_rows: int = 5) -> np.ndarray:
    return np.random.default_rng(0).normal(size=(n_rows, N_SAMPLES)).astype(np.float32)


def _pyramid(x: np.ndarray) -> MinMaxPyramid:
    pyramid = MinMaxPyramid.allocate(RamStore(), "p", x.shape[0], x.shape[1])
    pyramid.write(0, x[:2])
    pyramid.write(2, x[2:])
    return pyramid


class TestPyramid:
    def test_levels_are_the_min_and_max_of_their_bins(self) -> None:
        x = _series()
        pyramid = _pyramid(x)
        assert pyramid.factors == [16, 64, 256]  # a level keeps at least 64 bins
        for factor, level in zip(pyramid.factors, pyramid.levels, strict=True):
            for b in (0, 7, level.shape[2] - 1):  # the last bin is short
                chunk = x[:, b * factor : (b + 1) * factor]
                np.testing.assert_array_equal(level[0, :, b], chunk.min(axis=1))
                np.testing.assert_array_equal(level[1, :, b], chunk.max(axis=1))

    @pytest.mark.parametrize(
        ("start", "end", "bins"),
        [(0, N_SAMPLES, 1000), (123, 39_000, 97)],
    )
    def test_each_bin_holds_its_samples_and_at_most_one_level_bin_more(
        self, start: int, end: int, bins: int
    ) -> None:
        x = _series()
        got = view(_pyramid(x), lambda r, a, b: x[r, a:b], slice(1, 4), start, end, bins)
        assert got.mins is not None and got.maxs is not None
        assert got.mins.shape == (3, bins)
        edges = start + (np.arange(bins + 1) * (end - start)) // bins
        f = got.factor
        for b in range(bins):
            inside = x[1:4, edges[b] : edges[b + 1]]
            around = x[1:4, max(0, edges[b] - f) : edges[b + 1] + f]
            assert (got.mins[:, b] <= inside.min(axis=1)).all()
            assert (got.maxs[:, b] >= inside.max(axis=1)).all()
            assert (got.mins[:, b] >= around.min(axis=1)).all()
            assert (got.maxs[:, b] <= around.max(axis=1)).all()
            if f == 1:  # from the samples themselves: exact
                np.testing.assert_array_equal(got.maxs[:, b], inside.max(axis=1))

    def test_a_short_window_is_its_samples(self) -> None:
        x = _series()
        got = view(_pyramid(x), lambda r, a, b: x[r, a:b], slice(0, 5), 10, 60, 64)
        assert got.samples is not None and got.factor == 1
        np.testing.assert_array_equal(got.samples, x[:, 10:60])
