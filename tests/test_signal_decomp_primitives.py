"""Tests for the delay embedding of :mod:`muedit.signal.decomp_primitives`."""

from __future__ import annotations

import numpy as np
import pytest

from muedit.signal.decomp_primitives import (
    extend_mask,
    extend_signal,
    extend_signal_mask,
    extension_factor,
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
