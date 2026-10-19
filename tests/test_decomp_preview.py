"""Tests for :mod:`muedit.decomp.preview`: the preview payload of a finished run."""

from __future__ import annotations

import numpy as np
import pytest

from muedit.decomp.preview import abs_means


@pytest.mark.parametrize("dtype", [np.float64, np.float32])
def test_row_by_row_abs_means_are_bit_identical(dtype: type[np.floating]) -> None:
    rng = np.random.default_rng(2)
    x = rng.standard_normal((37, 50_003)).astype(dtype)
    keep = rng.random(37) > 0.3
    per_sample, per_row = abs_means(x)
    np.testing.assert_array_equal(per_sample, np.mean(np.abs(x), axis=0))
    np.testing.assert_array_equal(per_row, np.mean(np.abs(x), axis=1))
    np.testing.assert_array_equal(abs_means(x, keep)[0], np.mean(np.abs(x[keep]), axis=0))
