"""MUedit — FastICA-based Motor Unit decomposition of High-Density EMG signals."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from muedit.decomp.pipeline import run_decomposition
    from muedit.decomp.types import DecompositionParameters
    from muedit.io.factory import load_signal, register_loader

__all__ = [
    "DecompositionParameters",
    "load_signal",
    "register_loader",
    "run_decomposition",
]

# Imported on first use, so a submodule such as ``muedit.desktop`` starts without SciPy.
_LAZY = {
    "DecompositionParameters": "muedit.decomp.types",
    "load_signal": "muedit.io.factory",
    "register_loader": "muedit.io.factory",
    "run_decomposition": "muedit.decomp.pipeline",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'muedit' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
