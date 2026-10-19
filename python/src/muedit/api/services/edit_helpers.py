"""Editing-workflow data normalization helpers."""

from __future__ import annotations

from typing import Any

import numpy as np

from muedit.errors import MueditError
from muedit.models import LoadedDecomposition
from muedit.signal.grid import fallback_grid_name


def _expected_grid_count(decomp: LoadedDecomposition) -> int:
    """Number of grids implied by the names, muscles and MU-to-grid indices."""
    from_index = max(decomp.mu_grid_index) + 1 if decomp.mu_grid_index else 0
    return max(1, len(decomp.grid_names), len(decomp.muscle_names), from_index)


def _pad_grid_names(names: list[str], expected_count: int, fallback: list[str]) -> list[str]:
    out = [str(x).strip() for x in (names or []) if str(x).strip()]
    if not out:
        out = [str(x).strip() for x in (fallback or []) if str(x).strip()]
    target_count = max(int(expected_count or 0), len(out))
    while len(out) < target_count:
        out.append(fallback_grid_name(len(out)))
    return out


def _normalize_flagged(raw: Any, nmu: int) -> list[bool]:
    if not isinstance(raw, (list, tuple)):
        return [False] * nmu
    out = [bool(v) for v in raw[:nmu]]
    if len(out) < nmu:
        out.extend([False] * (nmu - len(out)))
    return out


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
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise MueditError(
            f"duplicatesthresh must be a number; got {raw!r}", "parameters.duplicatesthresh"
        ) from exc


def _coerce_bool_param(raw: Any) -> bool:
    """Coerce a boolean parameter such as ``duplicatesbgrids`` (MATLAB stores 0/1), unwrapping nested lists."""
    while isinstance(raw, (list, tuple, np.ndarray)) and np.ndim(raw) > 0:
        raw = raw[0] if len(raw) > 0 else False
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)
