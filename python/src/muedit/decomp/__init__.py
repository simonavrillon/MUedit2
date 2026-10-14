"""Decomposition pipeline package."""

from __future__ import annotations

from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.types import DecompositionParameters, DecompositionResult

__all__ = [
    "DecompositionParameters",
    "DecompositionResult",
    "run_decomposition",
]
