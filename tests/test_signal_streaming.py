"""Tests for the extended signal read batch by batch (:mod:`muedit.signal.streaming`)."""

from __future__ import annotations

import numpy as np
import pytest

from muedit.signal.decomp_primitives import extend_signal
from muedit.signal.streaming import RowSelection, StreamedExtender
from tests._synthetic_emg import motor_unit_emg

EX = 6


@pytest.fixture(scope="module")
def raw() -> np.ndarray:
    """Ten channels x 3000 samples of synthetic MU activity (float64)."""
    return motor_unit_emg(seed=5, n_samples=3000, activity=(0, 3000))[:10].astype(np.float64)


class TestStreamedExtender:
    """Every read equals the matching slice of a whole-recording extension."""

    SPANS = ((0, 40), (EX - 1, 300), (2900, 3000))

    @pytest.mark.parametrize(("start", "stop"), SPANS)
    def test_channels_first_matches_full_extension(
        self, raw: np.ndarray, start: int, stop: int
    ) -> None:
        rows = np.array([0, 2, 3, 7, 9])
        offset = raw[rows].mean(axis=1)
        full = extend_signal(raw[rows] - offset[:, None], EX)
        source = StreamedExtender(RowSelection(raw, rows), EX, offset=offset)
        np.testing.assert_array_equal(source.read(start, stop), full[:, start:stop])

    @pytest.mark.parametrize(("start", "stop"), SPANS)
    def test_samples_first_float32_matches_full_extension(
        self, raw: np.ndarray, start: int, stop: int
    ) -> None:
        offset = raw.mean(axis=1)
        demeaned = raw.astype(np.float32) - offset.astype(np.float32)[:, None]
        full = extend_signal(demeaned.T, EX, samples_first=True)
        source = StreamedExtender(raw, EX, offset=offset, dtype=np.float32, samples_first=True)
        out = source.read(start, stop)
        assert out.dtype == np.float32
        np.testing.assert_array_equal(out, full[start:stop])

    def test_mask_is_extended_cut_with_the_batch_and_padded_past_its_end(
        self, raw: np.ndarray
    ) -> None:
        mask = np.zeros(2000, dtype=bool)
        mask[100:110] = True
        source = StreamedExtender(raw, EX, artifact_mask=mask)
        np.testing.assert_array_equal(source.mask(95, 105), [False] * 5 + [True] * 5)
        # 110 .. 110 + EX - 2 still hold masked samples in their look-back.
        np.testing.assert_array_equal(source.mask(108, 118), [True] * 7 + [False] * 3)
        np.testing.assert_array_equal(source.mask(1995, 2005), [False] * 10)
        assert StreamedExtender(raw, EX).mask(0, 10) is None
