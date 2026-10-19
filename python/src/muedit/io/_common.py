"""Helpers shared by the signal file loaders (Intan, Open Ephys, ...)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from muedit.errors import SignalLoadError
from muedit.models import _as_name_list

#: Amplifier gain both supported acquisition systems record with (Intan RHD, Open Ephys board).
_AMPLIFIER_GAIN = 192.0

#: Name of the JSON sidecar a recording folder may hold to name its grids and muscles.
_GRID_SIDECAR = "muedit_grids.json"


def _read_grid_sidecar(sidecar: Path) -> tuple[list[str], list[str]]:
    """Return the grid names and muscle labels a ``muedit_grids.json`` sidecar lists, if any."""
    if not sidecar.exists():
        return [], []
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SignalLoadError(f"Cannot read grid sidecar {sidecar}: {exc}") from exc
    return (
        _as_name_list(payload.get("grid_names") or payload.get("gridname")),
        _as_name_list(payload.get("muscle_names") or payload.get("muscle")),
    )


def _resolve_muscles(
    muscle_names: str | Iterable[Any] | None,
    sidecar_muscle_names: list[str],
    n_groups: int,
    group_noun: str,
) -> list[str]:
    """Resolve per-group muscle labels: the argument wins over the sidecar, one label broadcasts."""
    resolved = _as_name_list(muscle_names) or sidecar_muscle_names
    if len(resolved) == 1 and n_groups > 1:
        resolved = resolved * n_groups
    if resolved and len(resolved) != n_groups:
        raise SignalLoadError(f"Got {len(resolved)} muscle label(s) for {n_groups} {group_noun}")
    return resolved
