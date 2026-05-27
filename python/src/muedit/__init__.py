"""MUedit — FastICA-based Motor Unit decomposition of High-Density EMG signals."""

from __future__ import annotations

from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.types import DecompositionParameters
from muedit.io.factory import LoaderFactory, load_signal, register_loader

__all__ = [
    "DecompositionParameters",
    "LoaderFactory",
    "load_signal",
    "register_loader",
    "run_decomposition",
]
