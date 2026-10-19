"""Registry-backed signal loader dispatch for EMG file formats."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from muedit.errors import SignalLoadError
from muedit.io._bids_reader import load_bids_signal
from muedit.io._intan import load_intan
from muedit.io._mat import load_mat
from muedit.io._openephys import load_openephys
from muedit.io._otb import load_otb4, load_otb_plus
from muedit.models import SignalImport
from muedit.storage import ArrayStore

#: ``loader(filepath, store=None)``: the signal, its arrays written into ``store`` when given.
LoaderFn = Callable[..., SignalImport]

_LOADERS: dict[str, LoaderFn] = {
    ".mat": load_mat,
    ".otb+": load_otb_plus,
    ".otb4": load_otb4,
    ".bdf": load_bids_signal,
    ".edf": load_bids_signal,
    ".rhd": load_intan,
    ".oebin": load_openephys,
}


def register_loader(ext: str, loader: LoaderFn, *, overwrite: bool = False) -> None:
    """Register a loader function for a file extension."""
    key = "." + ext.strip().lower().lstrip(".")
    if key == ".":
        raise ValueError("Loader extension cannot be empty")
    if key in _LOADERS and not overwrite:
        raise ValueError(
            f"Loader already registered for '{key}'. Use overwrite=True to replace it."
        )
    _LOADERS[key] = loader


def supported_extensions() -> tuple[str, ...]:
    """Return sorted tuple of currently registered loader extensions."""
    return tuple(sorted(_LOADERS))


def get_loader(filepath: str | Path) -> LoaderFn:
    """Return loader function for filepath extension; accepts BIDS EMG, Intan or Open Ephys directories."""
    path = Path(filepath)
    if path.is_dir():
        if any(path.glob("*_emg.bdf")) or any(path.glob("*_emg.edf")):
            return load_bids_signal
        if any(path.glob("*.rhd")):
            return load_intan
        if (path / "structure.oebin").is_file():
            return load_openephys
        raise SignalLoadError(
            "Directory does not contain a recognized BIDS EMG, Intan RHD or Open Ephys "
            f"structure.oebin file: {path}"
        )
    ext = path.suffix.lower()
    loader = _LOADERS.get(ext)
    if loader is None:
        supported = ", ".join(supported_extensions())
        raise SignalLoadError(
            f"Unsupported file format: {ext or '<no extension>'}. Supported formats: {supported}"
        )
    return loader


def load_signal(filepath: str | Path, store: ArrayStore | None = None) -> SignalImport:
    """Load a raw signal file with the loader registered for its extension, into ``store`` when given."""
    return get_loader(filepath)(str(filepath), store=store)
