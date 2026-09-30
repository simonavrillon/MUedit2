"""Central configuration for the MUedit API."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from muedit.paths import documents_dir, repo_root

DATA_ROOT_ENV = "MUEDIT_DATA_ROOT"
DEFAULT_PROJECT = "muedit_out"


def default_data_root() -> Path:
    """``MUEDIT_DATA_ROOT``, else the checkout's data/, else Documents."""
    override = os.environ.get(DATA_ROOT_ENV, "").strip()
    if override:
        return Path(override)
    root = repo_root()
    return root / "data" if root is not None else documents_dir()


#: Where outputs go; each project is a folder in it.
DATA_ROOT = default_data_root()


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
