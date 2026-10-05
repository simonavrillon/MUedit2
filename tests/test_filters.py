"""Tests for the row-parallel filtering in ``muedit.signal.filters``."""

from __future__ import annotations

import threading

import numpy as np
import pytest

from muedit.signal import filters
from muedit.signal.filters import (
    _notch_params,
    _remove_line_interference,
    bandpass_inplace,
    filter_workers,
    map_rows,
    notch_inplace,
)

FSAMP = 2048.0


def _reference_line_interference(x: np.ndarray, frad: int, window: int) -> np.ndarray:
    """The notch as it was written before it was vectorized, kept to pin its output."""
    x = np.asarray(x, dtype=np.float64)
    fsignal = np.fft.fft(x)
    fcorrec = np.zeros_like(fsignal, dtype=complex)
    tstamp: list[int] = []
    for start in range(0, len(fsignal) - window, window):
        segment = fsignal[start + 1 : start + window + 1]
        median_freq = np.median(np.abs(segment))
        std_freq = np.std(np.abs(segment))
        tstamp2 = np.where(np.abs(segment) > median_freq + 5 * std_freq)[0] + start + 1
        for j in range(-int(np.floor(frad / 2)), int(np.floor(frad / 2)) + 1):
            if tstamp2.size:
                tstamp.extend(list(tstamp2 + j))
    tstamp_arr = np.array(tstamp, dtype=int)
    tstamp_arr = tstamp_arr[(tstamp_arr > 0) & (tstamp_arr <= len(fsignal) // 2 + 1)]
    if tstamp_arr.size:
        fcorrec[tstamp_arr] = fsignal[tstamp_arr]
    n = len(fsignal)
    correc = n - (n // 2) * 2
    upper = int(np.ceil(n / 2))
    for idx in range(1, upper + 1 - correc):
        fcorrec[-idx] = np.conj(fcorrec[idx])
    return np.real(x - np.fft.ifft(fcorrec))


def _emg(n_rows: int, n_samples: int, dtype: type = np.float64) -> np.ndarray:
    t = np.arange(n_samples) / FSAMP
    rng = np.random.default_rng(0)
    mains = 5 * np.sin(2 * np.pi * 50 * t) + 2 * np.sin(2 * np.pi * 150 * t)
    return (rng.standard_normal((n_rows, n_samples)) + mains).astype(dtype)


@pytest.mark.parametrize("n_samples", [20_480, 20_481, 61_440, 7, 1])
def test_notch_matches_the_reference(n_samples: int) -> None:
    x = _emg(1, n_samples)[0]
    frad, window = _notch_params(FSAMP, n_samples)
    np.testing.assert_array_equal(
        _remove_line_interference(x, frad, window), _reference_line_interference(x, frad, window)
    )


@pytest.mark.parametrize("dtype", [np.float64, np.float32])
@pytest.mark.parametrize("apply", [bandpass_inplace, notch_inplace])
def test_threads_give_the_serial_result(
    monkeypatch: pytest.MonkeyPatch, apply: object, dtype: type
) -> None:
    data = _emg(16, 2 * filters._MIN_THREADED_SAMPLES // 16, dtype)
    assert filter_workers(*data.shape) > 1
    threaded = data.copy()
    apply(threaded, FSAMP)  # type: ignore[operator]
    monkeypatch.setattr(filters, "FILTER_THREADS", 1)
    serial = data.copy()
    apply(serial, FSAMP)  # type: ignore[operator]
    np.testing.assert_array_equal(threaded, serial)


class TestFilterWorkers:
    def test_small_work_stays_on_the_caller(self) -> None:
        assert filter_workers(64, 1000) == 1
        assert filter_workers(1, 10**7) == 1

    def test_long_rows_get_fewer_threads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(filters, "FILTER_THREADS", 4)
        per_row = filters._ROW_WORK_BYTES_PER_SAMPLE
        assert filter_workers(64, filters.FILTER_WORK_BYTES // (2 * per_row)) == 2
        assert filter_workers(64, filters.FILTER_WORK_BYTES // per_row + 1) == 1

    def test_never_more_threads_than_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(filters, "FILTER_THREADS", 4)
        monkeypatch.setattr(filters, "FILTER_WORK_BYTES", 1 << 40)
        assert filter_workers(3, 10**6) == 3


class TestMapRows:
    @pytest.fixture(autouse=True)
    def _threaded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(filters, "FILTER_THREADS", 3)

    def test_results_come_in_row_order(self) -> None:
        rows = range(5, 40)
        assert list(map_rows(lambda r: r * r, rows, 10**6)) == [r * r for r in rows]

    def test_only_a_few_rows_run_ahead_of_the_consumer(self) -> None:
        started: list[int] = []
        lock = threading.Lock()

        def fn(row: int) -> int:
            with lock:
                started.append(row)
            return row

        for row in map_rows(fn, range(30), 10**6):
            with lock:
                assert len(started) <= row + 1 + 3

    def test_an_error_reaches_the_consumer(self) -> None:
        def fn(row: int) -> int:
            if row == 4:
                raise ValueError("bad row")
            return row

        with pytest.raises(ValueError, match="bad row"):
            list(map_rows(fn, range(10), 10**6))
