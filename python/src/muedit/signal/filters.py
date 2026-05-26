"""Signal filtering utilities for HD-EMG preprocessing.

* :func:`demean` — subtract per-channel DC offset.
* :func:`bandpass_signals` — zero-phase Butterworth bandpass (surface or intramuscular bands).
* :func:`notch_signals` — FFT-based notch that suppresses mains harmonics automatically.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, filtfilt


def demean(signal: np.ndarray) -> np.ndarray:
    """Remove per-channel DC offset from a 2D signal array."""
    return signal - np.mean(signal, axis=1, keepdims=True)


def bandpass_signals(signal: np.ndarray, fsamp: float, emg_type: int = 1) -> np.ndarray:
    """Zero-phase Butterworth bandpass: emg_type=1 → 20–500 Hz (surface), 2 → 100–4400 Hz (intramuscular)."""
    if emg_type == 1:
        b, a = butter(2, [20, 500], btype="bandpass", fs=fsamp)
    else:
        b, a = butter(3, [100, 4400], btype="bandpass", fs=fsamp)

    return filtfilt(b, a, signal, axis=-1)


def notch_signals(signal: np.ndarray, fsamp: float) -> np.ndarray:
    """FFT-based notch: flags bins exceeding median+5·std in 1-second windows and zeros them, suppressing mains harmonics without knowing the line frequency."""
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
