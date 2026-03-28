"""Registry-backed signal loader dispatch for EMG file formats.

Supported formats
-----------------
``.mat``
    MATLAB workspace files (v5 and v7.3) exported from the OT Biolab+ or
    custom recording scripts.
``.otb+``
    OT Biolab+ archive format (tar/zip containing XML metadata and ``.sig``
    binary files).
``.otb4``
    OT Biolab4 proprietary binary format with embedded XML metadata.

Usage
-----
>>> from muedit.io.factory import LoaderFactory, register_loader
>>> signal = LoaderFactory.load_signal("recording.otb4")
>>> print(signal.fsamp, signal.data.shape)
>>>
>>> # Add support for a new extension:
>>> # register_loader(".myfmt", load_myfmt)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from muedit.io.loaders import load_mat, load_otb4, load_otb_plus
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
}


def register_loader(ext: str, loader: LoaderFn, *, overwrite: bool = False) -> None:
    """Register a loader function for a file extension.

    Args:
        ext: File extension such as ``.mat`` or ``otb4``.
        loader: Callable receiving ``filepath`` and returning ``dict`` or ``SignalImport``.
        overwrite: When ``False`` (default), raises if extension is already registered.
    """
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
    """Return loader function for a file path based on extension."""
    ext = Path(filepath).suffix.lower()
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
    """Compatibility facade over the registry-based loader dispatch.

    Examples:
        >>> loader = LoaderFactory.create_loader("data/rec.otb4")
        >>> signal = loader.load("data/rec.otb4")

        Or more concisely:

        >>> signal = LoaderFactory.load_signal("data/rec.otb4")
    """

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


__all__ = [
    "LoaderFactory",
    "LoaderFn",
    "get_loader",
    "register_loader",
    "supported_extensions",
]
