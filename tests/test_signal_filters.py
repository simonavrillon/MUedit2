"""Tests for the bandpass and notch filters (:mod:`muedit.signal.filters`)."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from muedit.models import SignalImport
from muedit.signal import filters
from muedit.signal.filters import (
    _notch_params,
    _remove_line_interference,
    bandpass_inplace,
    bandpass_signals,
    demean,
    notch_inplace,
)

FSAMP = 2048.0
# Band edges of the surface bandpass (emg_type=1).
_BP_LOW, _BP_HIGH = 20.0, 500.0


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
    """White noise plus 50 Hz mains and its third harmonic."""
    t = np.arange(n_samples) / FSAMP
    rng = np.random.default_rng(0)
    mains = 5 * np.sin(2 * np.pi * 50 * t) + 2 * np.sin(2 * np.pi * 150 * t)
    return (rng.standard_normal((n_rows, n_samples)) + mains).astype(dtype)


def _notch_rows(data: np.ndarray) -> np.ndarray:
    frad, window = _notch_params(FSAMP, data.shape[1])
    return np.stack([_remove_line_interference(row, frad, window) for row in data])


class TestBandpass:
    """The EMG band-pass, whole and in threaded blocks."""

    def test_bandpass_on_real_emg(self, novecento_emg: SignalImport) -> None:
        """Surface bandpass on real EMG: the DC offset goes and the out-of-band power drops."""
        grid0 = novecento_emg.data[:64, :].copy()
        fsamp = novecento_emg.fsamp
        filtered = bandpass_signals(grid0, fsamp, emg_type=1)

        assert filtered.shape == grid0.shape
        assert np.isfinite(filtered).all()
        assert np.max(np.abs(np.mean(grid0, axis=1))) > 1e-3
        assert np.max(np.abs(np.mean(filtered, axis=1))) < 1e-3

        freqs = np.fft.rfftfreq(grid0.shape[1], 1.0 / fsamp)
        in_band = (freqs >= _BP_LOW) & (freqs <= _BP_HIGH)

        def out_of_band_power(sig: np.ndarray) -> float:
            return float((np.abs(np.fft.rfft(sig, axis=1)) ** 2)[:, ~in_band].sum())

        assert out_of_band_power(filtered) < 0.5 * out_of_band_power(grid0)
        assert np.var(filtered) > 0.1 * np.var(grid0)

    @pytest.mark.parametrize(
        ("inplace", "whole"),
        [
            (bandpass_inplace, lambda data: bandpass_signals(data, FSAMP)),
            (notch_inplace, _notch_rows),
        ],
        ids=["bandpass", "notch"],
    )
    def test_threaded_inplace_filter_matches_the_whole_array_filter(
        self,
        inplace: Callable[[np.ndarray, float], None],
        whole: Callable[[np.ndarray], np.ndarray],
    ) -> None:
        data = _emg(16, 2 * filters._MIN_THREADED_SAMPLES // 16, np.float32)
        assert filters.filter_workers(*data.shape) > 1
        threaded = data.copy()
        inplace(threaded, FSAMP)
        np.testing.assert_array_equal(threaded, whole(data).astype(np.float32))


class TestNotch:
    """The mains notch: its harmonics go, the rest of the spectrum stays."""

    def test_notch_removes_mains_harmonics_and_keeps_the_rest(self) -> None:
        x = _emg(4, 20_480)
        filtered = x.copy()
        notch_inplace(filtered, FSAMP)

        freqs = np.fft.rfftfreq(x.shape[1], 1.0 / FSAMP)
        power_in = np.abs(np.fft.rfft(x, axis=1)) ** 2
        power_out = np.abs(np.fft.rfft(filtered, axis=1)) ** 2
        mains = np.isin(freqs, [50.0, 150.0])
        near_mains = (np.abs(freqs - 50) <= 2) | (np.abs(freqs - 150) <= 2)
        assert power_out[:, mains].sum() < 1e-6 * power_in[:, mains].sum()
        np.testing.assert_allclose(
            power_out[:, ~near_mains].sum(), power_in[:, ~near_mains].sum(), rtol=0.02
        )

    @pytest.mark.parametrize("n_samples", [20_480, 20_481], ids=["even", "odd"])
    def test_notch_matches_the_reference(self, n_samples: int) -> None:
        # The one-sided FFT rounds differently from the two-sided one; a bin flagged or missed
        # differently would move the output by far more than this.
        x = _emg(1, n_samples)
        np.testing.assert_allclose(
            _notch_rows(x),
            [_reference_line_interference(x[0], *_notch_params(FSAMP, n_samples))],
            rtol=0,
            atol=1e-9,
        )

    def test_float32_notch_filters_in_float64(self) -> None:
        t = np.arange(20_001) / 2048.0
        x = np.random.default_rng(1).standard_normal((3, t.size)) + 4 * np.sin(2 * np.pi * 50 * t)
        x32, x64 = x.astype(np.float32), x.astype(np.float32).astype(np.float64)
        notch_inplace(x32, 2048.0)
        notch_inplace(x64, 2048.0)
        np.testing.assert_array_equal(x32, x64.astype(np.float32))


class TestDemean:
    """Removing each channel's mean."""

    def test_demean_takes_the_mean_in_float64(self) -> None:
        x = (np.random.default_rng(0).standard_normal((4, 100_001)) + 1e4).astype(np.float32)
        out = demean(x)
        assert out.dtype == np.float32
        expected = (x - x.astype(np.float64).mean(axis=1, keepdims=True)).astype(np.float32)
        np.testing.assert_array_equal(out, expected)
