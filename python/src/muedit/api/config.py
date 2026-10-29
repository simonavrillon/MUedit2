"""Central configuration for the MUedit API."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from muedit.paths import config_file, documents_dir, repo_root

DATA_ROOT_ENV = "MUEDIT_DATA_ROOT"
DEFAULT_PROJECT = "muedit_out"


def _read_config() -> dict[str, object]:
    """The config file's settings; empty when it is missing or unreadable."""
    try:
        settings = json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def data_root_override() -> Path | None:
    """``MUEDIT_DATA_ROOT``, which fixes the output root while it is set; None when unset."""
    override = os.environ.get(DATA_ROOT_ENV, "").strip()
    return Path(override) if override else None


def default_data_root() -> Path:
    """``MUEDIT_DATA_ROOT``, else the config's folder, else the checkout's data/, else Documents."""
    override = data_root_override()
    if override is not None:
        return override
    saved = _read_config().get("data_root")
    if isinstance(saved, str) and saved:
        return Path(saved)
    root = repo_root()
    return root / "data" if root is not None else documents_dir()


#: Where outputs go; each project is a folder in it.
DATA_ROOT = default_data_root()


def set_data_root(folder: Path) -> None:
    """Make ``folder`` the output root, remembered in the config file for the next launch."""
    global DATA_ROOT  # noqa: PLW0603  (every caller reads config.DATA_ROOT at call time)
    settings = _read_config()
    settings["data_root"] = str(folder)
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    DATA_ROOT = folder


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


def list_projects() -> list[str]:
    """The folders under DATA_ROOT that hold a BIDS dataset, sorted by name."""
    if not DATA_ROOT.is_dir():
        return []
    return sorted(
        folder.name
        for folder in DATA_ROOT.iterdir()
        if not folder.name.startswith(".") and (folder / "dataset_description.json").is_file()
    )


def project_of(bids_root: Path) -> str:
    """The project folder ``bids_root`` lies in under ``DATA_ROOT``, or ``""`` outside it."""
    try:
        rel = bids_root.resolve().relative_to(DATA_ROOT.resolve())
    except ValueError:
        return ""
    return rel.parts[0] if rel.parts else ""
