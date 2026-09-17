"""Editing-workflow data normalization helpers."""

from __future__ import annotations

import contextlib
from typing import Any

import numpy as np


def _expected_grid_count(loaded: dict[str, Any]) -> int:
    count = 0
    grid_names = loaded.get("grid_names")
    if isinstance(grid_names, list):
        count = max(count, len(grid_names))
    muscles = loaded.get("muscle")
    if isinstance(muscles, list):
        count = max(count, len(muscles))
    mu_grid_index = loaded.get("mu_grid_index")
    if isinstance(mu_grid_index, list) and mu_grid_index:
        # Non-numeric indices: fall back to the other counts.
        with contextlib.suppress(TypeError, ValueError):
            count = max(count, int(max(mu_grid_index)) + 1)
    return max(1, count)


def _pad_grid_names(names: list[str], expected_count: int, fallback: list[str]) -> list[str]:
    out = [str(x).strip() for x in (names or []) if str(x).strip()]
    if not out:
        out = [str(x).strip() for x in (fallback or []) if str(x).strip()]
    target_count = max(int(expected_count or 0), len(out))
    while len(out) < target_count:
        out.append(f"Grid {len(out) + 1}")
    return out


def _normalize_muscle_names(raw: list[str] | str | None) -> list[str]:
    """Normalize a muscle-name payload value into a clean list of non-empty strings."""
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _normalize_flagged(raw: Any, nmu: int) -> list[bool]:
    if not isinstance(raw, (list, tuple)):
        return [False] * nmu
    out = [bool(v) for v in raw[:nmu]]
    if len(out) < nmu:
        out.extend([False] * (nmu - len(out)))
    return out


def _generate_mu_uids(mu_grid_index: list[int]) -> list[str]:
    counts: dict[int, int] = {}
    uids: list[str] = []
    for grid_idx in mu_grid_index:
        count = counts.get(grid_idx, 0)
        uids.append(f"g{grid_idx}_mu{count}")
        counts[grid_idx] = count + 1
    return uids


def _normalize_mu_grid_index(raw: Any, nmu: int) -> list[int]:
    if not isinstance(raw, (list, tuple)):
        return [0] * nmu
    vals: list[int] = []
    for x in raw[:nmu]:
        try:
            vals.append(int(x))
        except (TypeError, ValueError):
            vals.append(0)
    if len(vals) < nmu:
        vals.extend([0] * (nmu - len(vals)))
    return vals


def _coerce_dup_tol(raw: Any, default: float = 0.3) -> float:
    """Coerce a ``duplicatesthresh`` parameter to float, unwrapping nested lists."""
    while isinstance(raw, (list, tuple, np.ndarray)) and np.ndim(raw) > 0:
        raw = raw[0] if len(raw) > 0 else default
    return float(raw)
