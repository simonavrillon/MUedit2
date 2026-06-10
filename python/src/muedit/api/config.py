"""Central configuration for the MUedit API."""

from __future__ import annotations

import os
from pathlib import Path

# Repo root is 4 levels up from this file: api/ → muedit/ → src/ → python/ → MUedit2/
_REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_ROOT = Path(os.environ.get("MUEDIT_DATA_ROOT") or _REPO_ROOT / "data")


def resolve_bids_root(project: str | None) -> Path:
    """Return the absolute BIDS root for a given project name.

    Falls back to ``DATA_ROOT/muedit_out`` when project is empty.
    """
    name = str(project or "").strip()
    return DATA_ROOT / (name if name else "muedit_out")
