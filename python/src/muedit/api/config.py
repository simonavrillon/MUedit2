"""Central configuration for the MUedit API."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from muedit.paths import documents_dir, repo_root
from muedit.settings import load_settings

DATA_ROOT_ENV = "MUEDIT_DATA_ROOT"
#: The settings key of the output folder the user picked in the desktop app.
DATA_ROOT_SETTING = "data_root"
DEFAULT_PROJECT = "muedit_out"


def default_data_root() -> Path:
    """``MUEDIT_DATA_ROOT``, else the folder picked in the app, else data/, else Documents."""
    override = os.environ.get(DATA_ROOT_ENV, "").strip()
    if override:
        return Path(override)
    saved = load_settings().get(DATA_ROOT_SETTING)
    if isinstance(saved, str) and saved:
        return Path(saved)
    root = repo_root()
    return root / "data" if root is not None else documents_dir()


#: Where outputs go; each project is a folder in it.
DATA_ROOT = default_data_root()


def set_data_root(path: str | Path) -> None:
    """Send later outputs under ``path``."""
    global DATA_ROOT  # noqa: PLW0603
    DATA_ROOT = Path(path)


def resolve_bids_root(project: str | None) -> Path:
    """The BIDS root of a project under DATA_ROOT; ValueError unless it is one folder name."""
    name = str(project or "").strip()
    if not name:
        return DATA_ROOT / DEFAULT_PROJECT
    # Checked with both path flavours, so a Windows separator is refused on every OS.
    parts = {PurePosixPath(name).parts, PureWindowsPath(name).parts}
    if parts != {(name,)} or name in {".", ".."}:
        raise ValueError(f"Project must be a folder name, not a path: {name!r}")
    return DATA_ROOT / name


def project_of(bids_root: Path) -> str:
    """The project folder ``bids_root`` lies in under ``DATA_ROOT``, or ``""`` outside it."""
    try:
        rel = bids_root.resolve().relative_to(DATA_ROOT.resolve())
    except ValueError:
        return ""
    return rel.parts[0] if rel.parts else ""
