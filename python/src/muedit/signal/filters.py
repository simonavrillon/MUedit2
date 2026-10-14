"""Signal filtering utilities for HD-EMG preprocessing."""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from itertools import islice
from typing import TypeVar

import numpy as np
from scipy.fft import irfft, rfft
from scipy.signal import butter, filtfilt

from muedit.models import FloatArray

NOTCH_WINDOW_HZ: float = 50.0
#: Threads that filter rows at once (filtfilt and scipy's FFT release the GIL).
FILTER_THREADS: int = min(4, os.cpu_count() or 1)
#: Rows filtered together where a whole-grid copy would be too large.
FILTER_BLOCK_ROWS: int = 8
#: Working memory the filter threads may hold together.
FILTER_WORK_BYTES: int = 64 * 1024 * 1024
#: Bytes per sample one row's filtering holds: its float64 copies and complex spectra.
_ROW_WORK_BYTES_PER_SAMPLE = 64
#: Below this many samples in all, a thread costs more than the filtering it takes over.
_MIN_THREADED_SAMPLES = 1 << 20

T = TypeVar("T")


def demean(signal: FloatArray) -> FloatArray:
    """Remove per-channel DC offset from a 2D signal array; the mean is taken in float64."""
    mean = np.mean(signal, axis=1, keepdims=True, dtype=np.float64)
    if signal.dtype == np.float32:
        return (signal - mean).astype(np.float32)
    return signal - mean


def filter_workers(n_rows: int, n_samples: int) -> int:
    """Threads for filtering ``n_rows`` rows of ``n_samples``, within ``FILTER_WORK_BYTES``."""
    if n_rows < 2 or n_rows * n_samples < _MIN_THREADED_SAMPLES:
        return 1
    fit = FILTER_WORK_BYTES // max(1, _ROW_WORK_BYTES_PER_SAMPLE * n_samples)
    return max(1, min(FILTER_THREADS, n_rows, fit))


def map_rows(fn: Callable[[int], T], rows: range, n_samples: int) -> Iterator[T]:
    """``fn(row)`` for each of ``rows`` on ``filter_workers`` threads, in order; ``fn`` owns its row."""
    workers = filter_workers(len(rows), n_samples)
    if workers == 1:
        yield from map(fn, rows)
        return
    pool = ThreadPoolExecutor(workers, thread_name_prefix="muedit-filter")
    todo = iter(rows)
    # One result per thread at most waits to be consumed.
    pending: deque[Future[T]] = deque(pool.submit(fn, row) for row in islice(todo, workers))
    try:
        while pending:
            result = pending.popleft().result()
            for row in islice(todo, 1):
                pending.append(pool.submit(fn, row))
            yield result
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def for_each_row(fn: Callable[[int], object], rows: range, n_samples: int) -> None:
    """``map_rows`` for ``fn`` run for what it writes."""
    deque(map_rows(fn, rows, n_samples), maxlen=0)


def _bandpass_coefficients(fsamp: float, emg_type: int) -> tuple[FloatArray, FloatArray]:
    """Butterworth (b, a) for the surface (1) or intramuscular (2) bandpass."""
    if emg_type == 1:
        if fsamp <= 1000:
            raise ValueError(
                f"Surface bandpass (20-500 Hz) requires fsamp > 1000 Hz; got {fsamp} Hz."
            )
        return butter(2, [20, 500], btype="bandpass", fs=fsamp)
    if fsamp <= 8800:
        raise ValueError(
            f"Intramuscular bandpass (100-4400 Hz) requires fsamp > 8800 Hz; got {fsamp} Hz."
        )
    return butter(3, [100, 4400], btype="bandpass", fs=fsamp)


def bandpass_signals(signal: FloatArray, fsamp: float, emg_type: int = 1) -> FloatArray:
    """Zero-phase Butterworth bandpass: emg_type=1 → 20–500 Hz (surface), 2 → 100–4400 Hz (intramuscular)."""
    b, a = _bandpass_coefficients(fsamp, emg_type)
    return filtfilt(b, a, signal, axis=-1)


def bandpass_inplace(signal: FloatArray, fsamp: float, emg_type: int = 1) -> None:
    """``bandpass_signals`` written back into ``signal``, row by row on the filter threads."""
    # filtfilt filters each row on its own, so per-row results are bit-identical.
    b, a = _bandpass_coefficients(fsamp, emg_type)

    def filter_row(row: int) -> None:
        signal[row] = filtfilt(b, a, signal[row])

    for_each_row(filter_row, range(signal.shape[0]), signal.shape[1])


def _notch_params(fsamp: float, n_samples: int) -> tuple[int, int]:
    """Harmonic half-width ``frad`` and median window, both in FFT bins."""
    frad = int(round(4 / (fsamp / n_samples)))
    window = max(1, int(round(NOTCH_WINDOW_HZ * n_samples / fsamp)))
    return frad, window


def _remove_line_interference(x: FloatArray, frad: int, window: int) -> FloatArray:
    """Remove interference from a single-channel signal, computed in float64."""
    x = np.asarray(x, dtype=np.float64)  # a float32 FFT would run in single precision
    n = x.size
    spectrum = rfft(x)
    magnitude = np.abs(spectrum)
    half = frad // 2

    # The thresholds come from segments of the two-sided magnitude. A segment starting more
    # than ``half`` bins past Nyquist only flags bins that are dropped, so it is not scanned.
    n_segments = min(len(range(0, n - window, window)), (n // 2 + half - 1) // window + 1)
    two_sided = np.concatenate([magnitude, magnitude[1 : n - n // 2][::-1]])
    segments = two_sided[1 : n_segments * window + 1].reshape(n_segments, window)
    threshold = np.median(segments, axis=1) + 5 * np.std(segments, axis=1)
    rows, cols = np.nonzero(segments > threshold[:, None])
    peaks = rows * window + cols + 1
    tstamp = (peaks[:, None] + np.arange(-half, half + 1)).ravel()
    tstamp = tstamp[(tstamp > 0) & (tstamp <= n // 2)]

    correction = np.zeros_like(spectrum)
    correction[tstamp] = spectrum[tstamp]
    return x - irfft(correction, n)


def notch_signals(signal: FloatArray, fsamp: float) -> FloatArray:
    """FFT-based notch that suppresses mains harmonics without knowing the line frequency."""
    filtered = np.array(signal)
    notch_inplace(filtered, fsamp)
    return filtered


def notch_inplace(signal: FloatArray, fsamp: float) -> None:
    """``notch_signals`` written back into ``signal``, channel by channel on the filter threads."""
    if signal.size == 0:
        return
    frad, window = _notch_params(fsamp, signal.shape[1])

    def filter_row(row: int) -> None:
        signal[row] = _remove_line_interference(signal[row], frad, window)

    for_each_row(filter_row, range(signal.shape[0]), signal.shape[1])


def emg_filter_inplace(signal: FloatArray, fsamp: float, emg_type: int = 1) -> None:
    """The decomposition's filtering of one grid's rows (notch, then bandpass), written back."""
    notch_inplace(signal, fsamp)
    bandpass_inplace(signal, fsamp, emg_type)
