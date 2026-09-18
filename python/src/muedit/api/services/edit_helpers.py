"""Editing-workflow data normalization helpers."""

from __future__ import annotations

from typing import Any

import numpy as np

from muedit.models import LoadedDecomposition


def _expected_grid_count(decomp: LoadedDecomposition) -> int:
    """Number of grids implied by the names, muscles and MU-to-grid indices."""
    from_index = max(decomp.mu_grid_index) + 1 if decomp.mu_grid_index else 0
    return max(1, len(decomp.grid_names), len(decomp.muscle), from_index)


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


def _coerce_bool_param(raw: Any) -> bool:
    """Coerce a boolean parameter such as ``duplicatesbgrids`` (MATLAB stores 0/1), unwrapping nested lists."""
    while isinstance(raw, (list, tuple, np.ndarray)) and np.ndim(raw) > 0:
        raw = raw[0] if len(raw) > 0 else False
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)
