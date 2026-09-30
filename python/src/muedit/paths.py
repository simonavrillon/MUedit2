"""Where MUedit keeps its per-user files, and where it finds the frontend it serves."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import platformdirs

CACHE_DIR_ENV = "MUEDIT_CACHE_DIR"
# Lowercase on Linux and the BSDs, as their other per-user folders are.
_APP = "MUedit" if sys.platform in ("darwin", "win32") else "muedit"
#: The repository checkout this package runs from, when it is not installed.
_CHECKOUT = Path(__file__).resolve().parents[3]


def cache_dir() -> Path:
    """``MUEDIT_CACHE_DIR``, else the platform's per-user cache folder for MUedit."""
    override = os.environ.get(CACHE_DIR_ENV, "").strip()
    if override:
        return Path(override)
    return Path(platformdirs.user_cache_dir(_APP, appauthor=False))


def log_dir() -> Path:
    """The platform's per-user log folder for MUedit."""
    return Path(platformdirs.user_log_dir(_APP, appauthor=False))


def documents_dir() -> Path:
    """``MUedit`` in the user's Documents folder, wherever the OS has moved it."""
    return Path(platformdirs.user_documents_dir()) / "MUedit"


def repo_root() -> Path | None:
    """The repository checkout this package runs from, or None when it is installed."""
    return _CHECKOUT if (_CHECKOUT / "pyproject.toml").is_file() else None


def frontend_dir() -> Path | None:
    """The checkout's frontend, or None when the package is installed without it."""
    root = repo_root()
    folder = root / "frontend" if root is not None else None
    return folder if folder is not None and (folder / "index.html").is_file() else None
