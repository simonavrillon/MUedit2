"""Quantitative measurement collection for the decomposition tests.

Several properties of the decomposition are real *in expectation* but not on
every individual run: peel-off usually exposes additional motor units, dedup
usually collapses duplicates, detected units usually agree closely with ground
truth.  FastICA is a randomized fixed-point search, so the realized value of any
of these on one ROI with one seed can legitimately sit at zero (or below a
quality floor) without anything being broken.

Asserting a threshold on such a quantity produces a red build that says only
"below threshold" -- no magnitude, no distribution, no trend.  So the tests
record these as *measurements* instead: each run appends rows to the tables
below, ``conftest`` prints them at the end of the session and writes one CSV per
table for offline analysis.

What remains an assertion is the genuinely deterministic part: spike trains
sorted and in-bounds, refractory distances respected, counts internally
consistent, and dedup never *increasing* the unit count.

Usage::

    from tests._report import record

    record("peel_off", peel_off_units=n_on, no_peel_units=n_off, delta=n_on - n_off)

Set ``MUEDIT_TEST_REPORT_DIR`` to control where the CSVs land (default
``tests/reports/``); the directory is git-ignored.
"""

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
    """Append one measurement row to ``table``.

    Rows are plain scalars (numbers or strings).  Column order follows first
    appearance; rows missing a column render as an empty cell, so a table can
    grow columns over time without breaking earlier rows.
    """
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
    """Write one CSV per collected table; returns the paths written.

    Each file is overwritten per run, so a CSV always reflects the most recent
    session rather than accumulating across runs -- keep a copy, or point
    ``MUEDIT_TEST_REPORT_DIR`` somewhere per-run, to compare over time.
    """
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
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row.get(c, "") for c in cols})
        written.append(path)
    return written


# ---------------------------------------------------------------------------
# Small summary helpers used by the recording call sites
# ---------------------------------------------------------------------------


def describe(values: list[float]) -> dict[str, Any]:
    """Return count/min/median/mean/max for a list of numbers.

    Returned as plain floats so the row is CSV-friendly.  An empty input yields
    a row of ``None`` cells rather than raising, so a run that detected nothing
    still produces a readable line.
    """
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
