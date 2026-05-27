"""Registry-backed signal loader dispatch for EMG file formats."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from muedit.io.loaders import load_bids_signal, load_mat, load_otb4, load_otb_plus
from muedit.models import SignalImport

LoaderFn = Callable[[str], SignalImport | dict[str, Any]]


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
    """Return loader function for filepath extension; accepts a BIDS EMG directory."""
    path = Path(filepath)
    if path.is_dir():
        candidates = sorted(path.glob("*_emg.bdf")) + sorted(path.glob("*_emg.edf"))
        if candidates:
            return load_bids_signal
        raise ValueError(
            f"Directory does not contain a recognized BIDS EMG file: {path}"
        )
    ext = path.suffix.lower()
    loader = _LOADERS.get(ext)
    if loader is None:
        supported = ", ".join(supported_extensions())
        raise ValueError(
            f"Unsupported file format: {ext or '<no extension>'}. Supported formats: {supported}"
        )
    return loader


class _RegisteredSignalLoader:
    """Compatibility wrapper exposing a ``load`` method around a registered function."""

    def __init__(self, loader_fn: LoaderFn) -> None:
        self._loader_fn = loader_fn

    def load(self, filepath: str) -> SignalImport:
        """Load a signal and normalize to ``SignalImport``."""
        return _as_signal_import(self._loader_fn(filepath))


class LoaderFactory:
    """Compatibility facade over the registry-based loader dispatch."""

    @classmethod
    def register_loader(
        cls, ext: str, loader: LoaderFn, *, overwrite: bool = False
    ) -> None:
        """Register loader for extension via class facade."""
        register_loader(ext, loader, overwrite=overwrite)

    @classmethod
    def supported_extensions(cls) -> tuple[str, ...]:
        """Return sorted tuple of registered extensions."""
        return supported_extensions()

    @classmethod
    def create_loader(cls, filepath: str | Path) -> _RegisteredSignalLoader:
        """Return loader object with ``load`` method for filepath extension."""
        return _RegisteredSignalLoader(get_loader(filepath))

    @classmethod
    def load_signal(cls, filepath: str | Path) -> SignalImport:
        """Load a signal path using the registered extension loader."""
        loader_fn = get_loader(filepath)
        return _as_signal_import(loader_fn(str(filepath)))


def load_signal(filepath: str) -> dict[str, Any]:
    """Load a raw signal file and normalize it to the internal mapping shape."""
    return LoaderFactory.load_signal(filepath).to_dict()


def clone_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a signal mapping through validated model serialization."""
    return SignalImport.from_mapping(signal).clone().to_dict()


__all__ = [
    "LoaderFactory",
    "LoaderFn",
    "clone_signal",
    "get_loader",
    "load_signal",
    "register_loader",
    "supported_extensions",
]
