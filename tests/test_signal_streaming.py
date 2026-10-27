"""Tests for the extended signal read batch by batch (:mod:`muedit.signal.streaming`)."""

from __future__ import annotations

import numpy as np
import pytest

from muedit.signal.decomp_primitives import extend_signal
from muedit.signal.streaming import RowSelection, StreamedExtender
from tests._synthetic_emg import EX


class TestStreamedExtender:
    """Every read equals the matching slice of a whole-recording extension."""

    SPANS = ((0, 40), (EX - 1, 300), (2900, 3000))

    @pytest.mark.parametrize(("start", "stop"), SPANS)
    def test_channels_first_matches_full_extension(
        self, short_emg: np.ndarray, start: int, stop: int
    ) -> None:
        rows = np.array([0, 2, 3, 7, 9])
        offset = short_emg[rows].mean(axis=1)
        full = extend_signal(short_emg[rows] - offset[:, None], EX)
        source = StreamedExtender(RowSelection(short_emg, rows), EX, offset=offset)
        np.testing.assert_array_equal(source.read(start, stop), full[:, start:stop])

    @pytest.mark.parametrize(("start", "stop"), SPANS)
    def test_samples_first_float32_matches_full_extension(
        self, short_emg: np.ndarray, start: int, stop: int
    ) -> None:
        offset = short_emg.mean(axis=1)
        demeaned = short_emg.astype(np.float32) - offset.astype(np.float32)[:, None]
        full = extend_signal(demeaned.T, EX, samples_first=True)
        source = StreamedExtender(
            short_emg, EX, offset=offset, dtype=np.float32, samples_first=True
        )
        out = source.read(start, stop)
        assert out.dtype == np.float32
        np.testing.assert_array_equal(out, full[start:stop])

    def test_mask_is_extended_cut_with_the_batch_and_padded_past_its_end(
        self, short_emg: np.ndarray
    ) -> None:
        mask = np.zeros(2000, dtype=bool)
        mask[100:110] = True
        source = StreamedExtender(short_emg, EX, artifact_mask=mask)
        np.testing.assert_array_equal(source.mask(95, 105), [False] * 5 + [True] * 5)
        # 110 .. 110 + EX - 2 still hold masked samples in their look-back.
        np.testing.assert_array_equal(source.mask(108, 118), [True] * 7 + [False] * 3)
        np.testing.assert_array_equal(source.mask(1995, 2005), [False] * 10)
        assert StreamedExtender(short_emg, EX).mask(0, 10) is None


@pytest.mark.parametrize("samples_first", [False, True])
def test_streamed_batches_keep_the_extender_dtype(samples_first: bool) -> None:
    source = np.ones((3, 50), dtype=np.float64)
    extender = StreamedExtender(source, 4, dtype=np.float32, samples_first=samples_first)
    assert extender.read(10, 30).dtype == np.float32
