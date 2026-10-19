"""Raw BIDS events: trigger pulses and the segmented force target, merged into ``*_events.tsv``."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import numpy as np

from muedit import installed_version
from muedit.io._bids_reader import is_grid_row, read_tsv

logger = logging.getLogger(__name__)

# A digital line pulsing faster than this, on average, is a clock rather than task markers.
MAX_TRIGGER_RATE_HZ = 50.0

# Rows each source writes; a re-export replaces a source's rows only when it was given that source.
_TTL = "ttl"
_SEGMENTATION = "target_segmentation"

# Phase labels other tools write ([MU], MUnitQuest): a file that has them already describes the task.
_PHASES = {
    "muscle_on",
    "muscle_off",
    "rest",
    "linear_isometric_ramp",
    "steady_isometric",
    "linear_ramp",
    "steady_hold",
    "sinusoid",
    "irregular",
}

_LABEL = {"ramp": "linear_isometric_ramp", "plateau": "steady_isometric"}

_COLUMNS = [
    "onset",
    "duration",
    "sample",
    "event_type",
    "value",
    "channel",
    "direction",
    "level_start",
    "level_end",
    "mvc_level",
    "mvc_rate",
    "frequency_hz",
    "amplitude",
    "n_cycles",
    "source",
]

_EVENT_LEVELS = {
    "ttl": "Pulse on a digital trigger line: onset at the rising edge, duration while it is high.",
    "muscle_on": "Start of a contraction: the target leaves rest.",
    "muscle_off": "End of a contraction: the target returns to rest.",
    "linear_isometric_ramp": "The target rises or falls (direction) from level_start to level_end.",
    "steady_isometric": "The target holds a steady level.",
    "sinusoid": "The target oscillates regularly (frequency_hz, amplitude, n_cycles).",
    "irregular": "The target moves without a regular pattern.",
    "cycle": "Trough of one cycle of a sinusoidal target.",
}


def _by_muedit() -> str:
    version = installed_version()
    return f"MUedit {version}" if version else "MUedit"


def _num(value: float, places: int) -> str:
    """``value`` with at most ``places`` decimals, trailing zeros dropped."""
    text = f"{float(value):.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _is_mvc(units: Any) -> bool:
    """Whether a unit is percent MVC, however it is spelled (``%MVC``, ``%(MVC)``, ``% MVC``)."""
    return re.sub(r"[\s()]", "", str(units or "")).upper() == "%MVC"


def _trigger_channels(channels_tsv: Path) -> list[tuple[int, str]]:
    """``(aux row, name)`` of each TRIG channel; aux rows are the non-grid rows, in file order."""
    if not channels_tsv.exists():
        return []
    _, rows = read_tsv(channels_tsv)
    aux_rows = [row for row in rows if not is_grid_row(row)]
    return [
        (i, (row.get("name") or "").strip())
        for i, row in enumerate(aux_rows)
        if (row.get("type") or "").strip().upper() == "TRIG"
    ]


def _line_number(name: str, fallback: int) -> str:
    """The line a trigger channel carries: its trailing number (``TTL3``, ``DIGITAL-IN-03``)."""
    match = re.search(r"(\d+)\s*$", name)
    return str(int(match[1])) if match else str(fallback)


def _trigger_rows(
    aux_data: np.ndarray, channels: list[tuple[int, str]], fsamp: float
) -> tuple[list[dict[str, str]], list[str]]:
    """One row per pulse of each trigger channel, and the channels left out as clocks."""
    rows: list[dict[str, str]] = []
    clocks: list[str] = []
    if aux_data.ndim != 2:
        return rows, clocks
    n_samples = aux_data.shape[1]
    seconds = n_samples / fsamp if fsamp > 0 else 0.0
    for row, name in channels:
        if row >= aux_data.shape[0] or seconds <= 0:
            continue
        high = np.asarray(aux_data[row]) > 0.5
        edges = np.diff(high.astype(np.int8), prepend=np.int8(0), append=np.int8(0))
        starts, stops = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        if starts.size / seconds > MAX_TRIGGER_RATE_HZ:
            clocks.append(name)
            continue
        value = _line_number(name, row + 1)
        for start, stop in zip(starts.tolist(), stops.tolist(), strict=True):
            rows.append(
                {
                    "onset": _num(start / fsamp, 6),
                    "duration": _num((stop - start) / fsamp, 6),
                    "sample": str(start),
                    "event_type": "ttl",
                    "value": value,
                    "channel": name,
                    "source": _TTL,
                }
            )
    return rows, clocks


def _segment_rows(record: dict[str, Any], fsamp: float) -> list[dict[str, str]]:
    """Rows for the target's segments, ``muscle_on``/``muscle_off`` around each contraction."""
    mvc = _is_mvc(record.get("target_units"))
    segments = sorted(record.get("segments") or [], key=lambda s: int(s["start"]))
    rows: list[dict[str, str]] = []

    def instant(event: str, sample: int) -> dict[str, str]:
        return {
            "onset": _num(sample / fsamp, 6),
            "duration": "0",
            "sample": str(sample),
            "event_type": event,
            "source": _SEGMENTATION,
        }

    previous_end: int | None = None
    for seg in segments:
        start, end = int(seg["start"]), int(seg["end"])
        # Segments of one contraction share their boundaries; a gap is rest.
        if previous_end is None or start > previous_end:
            if previous_end is not None:
                rows.append(instant("muscle_off", previous_end))
            rows.append(instant("muscle_on", start))
        duration = (end - start) / fsamp
        kind = str(seg["kind"])
        level_start, level_end = float(seg["level_start"]), float(seg["level_end"])
        row = {
            "onset": _num(start / fsamp, 6),
            "duration": _num(duration, 6),
            "sample": str(start),
            "event_type": _LABEL.get(kind, kind),
            "level_start": _num(level_start, 4),
            "level_end": _num(level_end, 4),
            "source": _SEGMENTATION,
        }
        if kind == "ramp":
            row["direction"] = str(seg.get("direction") or "n/a")
        if mvc and kind in ("ramp", "plateau"):
            row["mvc_level"] = _num(level_start, 4)
            rate = (level_end - level_start) / duration if kind == "ramp" and duration else 0.0
            row["mvc_rate"] = _num(rate, 4)
        if kind == "sinusoid":
            for key in ("frequency_hz", "amplitude", "n_cycles"):
                if seg.get(key) is not None:
                    row[key] = _num(float(seg[key]), 4)
        rows.append(row)
        if kind == "sinusoid":
            rows.extend(instant("cycle", int(c)) for c in seg.get("cycles") or [])
        previous_end = end
    if previous_end is not None:
        rows.append(instant("muscle_off", previous_end))
    return rows


def _source(row: dict[str, str]) -> str:
    return (row.get("source") or "").strip().lower()


def _has_foreign_phases(rows: list[dict[str, str]]) -> bool:
    """Whether another tool already listed the task phases (rows with no MUedit ``source``)."""
    for row in rows:
        event = (row.get("event_type") or row.get("trial_type") or "").strip()
        if _source(row) in ("", "n/a") and event in _PHASES:
            return True
    return False


def _column_descriptions(
    columns: list[str], events: set[str], target: dict[str, Any] | None, clocks: list[str]
) -> dict[str, Any]:
    """``events.json`` entries for MUedit's columns."""
    units = (target or {}).get("target_units") or "n/a"
    sources: dict[str, str] = {}
    if _TTL in events or clocks:
        note = f"; left out as clocks: {', '.join(clocks)}" if clocks else ""
        sources[_TTL] = (
            f"Decoded by {_by_muedit()} from the TRIG channels of *_channels.tsv; a line "
            f"with more than {MAX_TRIGGER_RATE_HZ:g} rising edges per second is a clock and "
            f"left out{note}."
        )
    if target is not None:
        config = ", ".join(f"{k}={v}" for k, v in (target.get("config") or {}).items())
        sources[_SEGMENTATION] = (
            f"Computed by {_by_muedit()} segment_target() from the auxiliary channel "
            f"'{target.get('target_name', '')}' with SegmentationConfig({config})."
        )
    described: dict[str, Any] = {
        "onset": {
            "Description": "Time of the event from the first sample of the recording.",
            "Units": "s",
        },
        "duration": {"Description": "Length of the event; 0 for an instant.", "Units": "s"},
        "sample": {
            "Description": "Sample index of the onset (zero-based) at the recording's sampling frequency.",
            "Units": "samples",
        },
        "event_type": {
            "Description": "Event category.",
            "Levels": {k: v for k, v in _EVENT_LEVELS.items() if k in events},
        },
        "value": {"Description": "Number of the trigger line."},
        "channel": {"Description": "Trigger channel, as named in *_channels.tsv."},
        "direction": {
            "Description": "Direction of a ramp.",
            "Levels": {"up": "Rising target", "down": "Falling target"},
        },
        "level_start": {"Description": "Target level at the onset.", "Units": units},
        "level_end": {"Description": "Target level at the end of the event.", "Units": units},
        "mvc_level": {"Description": "Target level at the onset.", "Units": "% MVC"},
        "mvc_rate": {"Description": "Rate of change of the target.", "Units": "% MVC / s"},
        "frequency_hz": {"Description": "Frequency of a sinusoidal target.", "Units": "Hz"},
        "amplitude": {
            "Description": "Half the peak-to-peak swing of a sinusoidal target.",
            "Units": units,
        },
        "n_cycles": {"Description": "Number of cycles of a sinusoidal target."},
        "source": {"Description": "What produced the row.", "Levels": sources},
    }
    return {column: described[column] for column in columns if column in described}


def _merge_json(path: Path, ours: dict[str, Any], added: set[str]) -> dict[str, Any]:
    """The existing ``events.json`` with ``ours`` for the ``added`` columns; other columns only gain Levels."""
    existing: dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            existing = json.load(f)
    merged = dict(existing)
    for column, entry in ours.items():
        old = existing.get(column)
        if column in added:
            merged[column] = entry
        elif isinstance(old, dict) and isinstance(old.get("Levels"), dict) and "Levels" in entry:
            # A column someone else described keeps its words; its Levels list our new values too.
            merged[column] = {**old, "Levels": {**old["Levels"], **entry["Levels"]}}
    return merged


def export_bids_events(
    emg_path: Path,
    fsamp: float,
    *,
    aux_data: np.ndarray | None = None,
    target_segments: dict[str, Any] | None = None,
) -> Path | None:
    """Merge the recording's trigger pulses and target segments into its ``*_events.tsv``, or ``None``."""
    entity = emg_path.stem[: -len("_emg")] if emg_path.stem.endswith("_emg") else emg_path.stem
    events_tsv = emg_path.with_name(f"{entity}_events.tsv")
    events_json = events_tsv.with_suffix(".json")

    existing_columns: list[str] = []
    existing: list[dict[str, str]] = []
    if events_tsv.exists():
        existing_columns, existing = read_tsv(events_tsv)

    replaced: set[str] = set()
    new_rows: list[dict[str, str]] = []
    clocks: list[str] = []
    if aux_data is not None:
        replaced.add(_TTL)
        # aux_data rows follow the aux rows of channels.tsv.
        channels = _trigger_channels(emg_path.with_name(f"{entity}_channels.tsv"))
        rows, clocks = _trigger_rows(aux_data, channels, fsamp)
        new_rows.extend(rows)
    target = target_segments if target_segments and target_segments.get("segments") else None
    if target is not None:
        if _has_foreign_phases(existing):
            logger.info(
                "%s already lists the task phases; no segmentation rows added", events_tsv.name
            )
            target = None
        else:
            replaced.add(_SEGMENTATION)
            new_rows.extend(_segment_rows(target, fsamp))

    kept = [row for row in existing if _source(row) not in replaced]
    if not new_rows and len(kept) == len(existing):
        return events_tsv if events_tsv.exists() else None

    rows = kept + new_rows
    present = {key for row in rows for key, value in row.items() if value not in (None, "")}
    columns = [c for c in _COLUMNS if c in present or c in ("onset", "duration")]
    columns += [c for c in existing_columns if c not in columns]

    def onset(row: dict[str, str]) -> float:
        try:
            return float(row.get("onset", "inf"))
        except ValueError:
            return float("inf")

    rows.sort(key=onset)
    with events_tsv.open("w", encoding="utf-8") as f:
        f.write("\t".join(columns) + "\n")
        for row in rows:
            f.write("\t".join(row.get(c) or "n/a" for c in columns) + "\n")

    events = {row.get("event_type", "") for row in new_rows}
    ours = _column_descriptions([c for c in columns if c in _COLUMNS], events, target, clocks)
    added = set(columns) - set(existing_columns)
    merged = _merge_json(events_json, ours, added)  # read before the file is rewritten
    with events_json.open("w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    return events_tsv
