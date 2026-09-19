"""Quantitative measurement collection for the decomposition tests."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

TESTS_DIR = Path(__file__).resolve().parent

#: Ordered measurement tables: table name -> list of row dicts.
_TABLES: dict[str, list[dict[str, Any]]] = {}

#: Human-readable captions, printed above each table.
_CAPTIONS: dict[str, str] = {}


def record(table: str, *, caption: str | None = None, **fields: Any) -> None:
    """Append one measurement row to ``table``."""
    _TABLES.setdefault(table, []).append(dict(fields))
    if caption and table not in _CAPTIONS:
        _CAPTIONS[table] = caption


def tables() -> dict[str, list[dict[str, Any]]]:
    """Return the collected tables (name -> rows), in insertion order."""
    return _TABLES


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    """Union of row keys, ordered by first appearance."""
    cols: list[str] = []
    for row in rows:
        for key in row:
            if key not in cols:
                cols.append(key)
    return cols


def _fmt(value: Any) -> str:
    """Render a cell: floats to 3 decimals, everything else via ``str``."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}"
    if value is None:
        return ""
    return str(value)


def format_table(name: str, rows: list[dict[str, Any]]) -> str:
    """Render one table as fixed-width text with a caption and column headers."""
    if not rows:
        return ""
    cols = _columns(rows)
    cells = [[_fmt(row.get(c)) for c in cols] for row in rows]
    widths = [max(len(col), *(len(cell[i]) for cell in cells)) for i, col in enumerate(cols)]

    lines: list[str] = []
    caption = _CAPTIONS.get(name)
    lines.append(f"{name}" + (f" -- {caption}" if caption else ""))
    header = "  ".join(col.rjust(widths[i]) for i, col in enumerate(cols))
    lines.append(header)
    lines.append("-" * len(header))
    lines.extend("  ".join(cell[i].rjust(widths[i]) for i in range(len(cols))) for cell in cells)
    return "\n".join(lines)


def format_all() -> str:
    """Render every collected table, newest section last."""
    blocks = [format_table(name, rows) for name, rows in _TABLES.items() if rows]
    return "\n\n".join(b for b in blocks if b)


def report_dir() -> Path:
    """Directory the CSVs are written to."""
    return Path(os.environ.get("MUEDIT_TEST_REPORT_DIR", TESTS_DIR / "reports"))


def write_csvs() -> list[Path]:
    """Write one CSV per collected table; returns the paths written."""
    written: list[Path] = []
    if not _TABLES:
        return written
    out = report_dir()
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in _TABLES.items():
        if not rows:
            continue
        path = out / f"{name}.csv"
        cols = _columns(rows)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row.get(c, "") for c in cols})
        written.append(path)
    return written


# ── Small summary helpers used by the recording call sites ───────────────────


def describe(values: list[float]) -> dict[str, Any]:
    """Return count/min/median/mean/max for a list of numbers."""
    import numpy as np

    if not values:
        return {"n": 0, "min": None, "median": None, "mean": None, "max": None}
    arr = np.asarray(values, dtype=float)
    return {
        "n": int(arr.size),
        "min": float(arr.min()),
        "median": float(np.median(arr)),
        "mean": float(arr.mean()),
        "max": float(arr.max()),
    }
