"""Signal filtering utilities for HD-EMG preprocessing.

Two filters are provided:

* :func:`bandpass_signals` — 2nd-order Butterworth bandpass filter applied
  with zero-phase ``filtfilt``.  Two frequency bands are supported depending on
  the electrode type (surface vs. intramuscular).

* :func:`notch_signals` — FFT-based notch filter that identifies and removes
  narrow-band power-line interference without requiring knowledge of the exact
  line frequency.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, filtfilt


def bandpass_signals(signal: np.ndarray, fsamp: float, emg_type: int = 1) -> np.ndarray:
    """Apply a zero-phase Butterworth bandpass filter to multi-channel EMG.

    Args:
        signal: Array of shape ``(n_channels, n_samples)``.
        fsamp: Sampling frequency in Hz.
        emg_type: Electrode type selector.
            ``1`` (default) → surface EMG, 20–500 Hz, 2nd-order.
            ``2`` → intramuscular / fine-wire EMG, 100–4400 Hz, 3rd-order.

    Returns:
        Filtered signal array, same shape as *signal*.
    """
    if emg_type == 1:
        b, a = butter(2, [20, 500], btype="bandpass", fs=fsamp)
    else:
        b, a = butter(3, [100, 4400], btype="bandpass", fs=fsamp)

    return filtfilt(b, a, signal, axis=-1)


def notch_signals(signal: np.ndarray, fsamp: float) -> np.ndarray:
    """Remove narrow-band power-line interference using an FFT-based notch.

    The algorithm scans the frequency spectrum in 1-second windows and flags
    frequency bins whose amplitude exceeds ``median + 5 * std`` within that
    window.  Flagged bins (± half a 4 Hz bandwidth) are zeroed in the complex
    spectrum before transforming back to the time domain.  This approach
    automatically suppresses the fundamental and all harmonics of mains hum
    without needing to specify the line frequency explicitly.

    Args:
        signal: Array of shape ``(n_channels, n_samples)``.  An empty array
            is returned unchanged.
        fsamp: Sampling frequency in Hz; used to compute the per-window width
            and the frequency resolution of flagged bins.

    Returns:
        Filtered signal array, same shape as *signal*.
    """
    if signal.size == 0:
        return signal

    n_channels, n_samples = signal.shape
    frad = int(round(4 / (fsamp / n_samples)))
    window = int(fsamp)

    def _remove_line_interference(x: np.ndarray) -> np.ndarray:
        """Remove interference from a single-channel signal."""
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

        # Enforce conjugate symmetry so the IFFT produces a real signal.
        n = len(fsignal)
        correc = n - (n // 2) * 2
        upper = int(np.ceil(n / 2))
        for idx in range(1, upper + 1 - correc):
            fcorrec[-idx] = np.conj(fcorrec[idx])

        return np.real(x - np.fft.ifft(fcorrec))

    filtered = np.zeros_like(signal)
    for ch in range(n_channels):
        filtered[ch, :] = _remove_line_interference(signal[ch, :])

    return filtered
