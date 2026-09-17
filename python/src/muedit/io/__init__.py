"""File I/O sub-package: loaders for .mat, .otb+, .otb4 formats."""

from __future__ import annotations

from muedit.io.factory import (
    LoaderFn,
    get_loader,
    load_signal,
    register_loader,
    supported_extensions,
)

__all__ = [
    "LoaderFn",
    "get_loader",
    "load_signal",
    "register_loader",
    "supported_extensions",
]
