"""Registry-backed signal loader dispatch for EMG file formats."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

from muedit.io.loaders import (
    load_bids_signal,
    load_intan,
    load_mat,
    load_otb4,
    load_otb_plus,
)
from muedit.io.store import ArrayStore, store_signal
from muedit.models import SignalImport

LoaderFn = Callable[..., SignalImport | dict[str, Any]]


def _normalize_extension(ext: str) -> str:
    """Normalize extension values to lower-case dotted form."""
    text = str(ext or "").strip().lower()
    if not text:
        raise ValueError("Loader extension cannot be empty")
    if not text.startswith("."):
        text = "." + text
    return text


def _as_signal_import(loaded: SignalImport | dict[str, Any]) -> SignalImport:
    """Normalize loader output to ``SignalImport``."""
    if isinstance(loaded, SignalImport):
        return loaded
    if isinstance(loaded, dict):
        return SignalImport.from_mapping(loaded)
    raise TypeError(
        f"Loader returned unsupported type {type(loaded).__name__}; expected dict or SignalImport"
    )


_LOADERS: dict[str, LoaderFn] = {
    ".mat": load_mat,
    ".otb+": load_otb_plus,
    ".otb4": load_otb4,
    ".bdf": load_bids_signal,
    ".edf": load_bids_signal,
    ".rhd": load_intan,
}


def register_loader(ext: str, loader: LoaderFn, *, overwrite: bool = False) -> None:
    """Register a loader function for a file extension."""
    key = _normalize_extension(ext)
    if key in _LOADERS and not overwrite:
        raise ValueError(
            f"Loader already registered for '{key}'. Use overwrite=True to replace it."
        )
    _LOADERS[key] = loader


def supported_extensions() -> tuple[str, ...]:
    """Return sorted tuple of currently registered loader extensions."""
    return tuple(sorted(_LOADERS.keys()))


def get_loader(filepath: str | Path) -> LoaderFn:
    """Return loader function for filepath extension; accepts BIDS EMG or Intan directories."""
    path = Path(filepath)
    if path.is_dir():
        candidates = sorted(path.glob("*_emg.bdf")) + sorted(path.glob("*_emg.edf"))
        if candidates:
            return load_bids_signal
        if sorted(path.glob("*.rhd")):
            return load_intan
        raise ValueError(
            f"Directory does not contain a recognized BIDS EMG or Intan RHD file: {path}"
        )
    ext = path.suffix.lower()
    loader = _LOADERS.get(ext)
    if loader is None:
        supported = ", ".join(supported_extensions())
        raise ValueError(
            f"Unsupported file format: {ext or '<no extension>'}. Supported formats: {supported}"
        )
    return loader


def load_signal(filepath: str, store: ArrayStore | None = None) -> SignalImport:
    """Load a raw signal file with the loader registered for its extension.

    With a ``store``, the EMG and auxiliary arrays are written into it: built-in
    loaders write there block by block, other registered loaders are copied in.
    """
    loader_fn = get_loader(filepath)
    if store is None:
        return _as_signal_import(loader_fn(str(filepath)))
    if "store" in inspect.signature(loader_fn).parameters:
        return _as_signal_import(loader_fn(str(filepath), store=store))
    return store_signal(_as_signal_import(loader_fn(str(filepath))), store)


__all__ = [
    "LoaderFn",
    "get_loader",
    "load_signal",
    "register_loader",
    "supported_extensions",
]
