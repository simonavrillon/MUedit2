"""Signal processing sub-package: bandpass and notch filtering."""

from __future__ import annotations

from muedit.signal.filters import bandpass_signals, demean, notch_signals
from muedit.signal.grid import format_hdemg_signal

__all__ = [
    "bandpass_signals",
    "demean",
    "format_hdemg_signal",
    "notch_signals",
]
