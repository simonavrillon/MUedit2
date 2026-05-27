"""BIDS-specific helpers for decomposition loading and channel sidecar parsing."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import numpy as np

from muedit.io.bids import load_bids_emg_grid


def _load_bids_grid(
    bids_root: Path,
    entity_label: str,
    grid_index: int,
    view_start: int = 0,
    view_end: int | None = None,
) -> tuple[np.ndarray, float, np.ndarray]:
    """Load a BIDS EMG grid for a specific sample window."""
    read_n = (view_end - view_start) if view_end is not None and view_end > view_start else None
    emg, fsamp, emg_mask = load_bids_emg_grid(
        bids_root, entity_label, grid_index, read_start=view_start, read_n=read_n
    )
    return emg.copy(), float(fsamp), np.asarray(emg_mask, dtype=int).copy()


def _parse_subject_session_from_entity_label(entity_label: str) -> tuple[str, str | None]:
    subject = "01"
    session: str | None = None
    for part in str(entity_label).split("_"):
        if part.startswith("sub-") and len(part) > 4:
            subject = part[4:]
        elif part.startswith("ses-") and len(part) > 4:
            session = part[4:]
    return subject, session


def _infer_bids_root_from_decomp_path(filepath: str) -> Path | None:
    raw = str(filepath or "")
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    parts = list(path.parts)

    sub_idx = next(
        (i for i, part in enumerate(parts) if part.lower().startswith("sub-")),
        -1,
    )
    if sub_idx > 0:
        return Path(*parts[:sub_idx])

    marker = "muedit_out"
    marker_idx = next((i for i, part in enumerate(parts) if part.lower() == marker), -1)
    if marker_idx >= 0:
        return Path(*parts[: marker_idx + 1])
    return None


def _normalize_bids_meta_value(value: str | None) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "n/a":
        return ""
    return text


def _grid_sort_key(group: str) -> tuple[int, str]:
    text = str(group or "").strip()
    lower = text.lower()
    if lower.startswith("grid"):
        suffix = text[4:].strip()
        if suffix.isdigit():
            return (int(suffix), text)
    return (10**9, text)


def _read_bids_channels_sidecar(channels_path: Path) -> tuple[list[str], list[str], float | None]:
    by_group: dict[str, tuple[str, str]] = {}
    fsamp: float | None = None
    with channels_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if (row.get("type") or "").strip().upper() != "EMG":
                continue
            if fsamp is None:
                raw_fsamp = _normalize_bids_meta_value(row.get("sampling_frequency"))
                if raw_fsamp:
                    try:
                        fsamp = float(raw_fsamp)
                    except ValueError:
                        fsamp = None
            group = (row.get("group") or "").strip()
            if not group:
                continue
            if group in by_group:
                continue
            grid_name = _normalize_bids_meta_value(row.get("grid_name")) or group
            muscle = _normalize_bids_meta_value(row.get("target_muscle"))
            by_group[group] = (grid_name, muscle)
    if not by_group:
        return [], [], fsamp
    ordered_groups = sorted(by_group.keys(), key=_grid_sort_key)
    grid_names = [by_group[group][0] for group in ordered_groups]
    muscles = [by_group[group][1] for group in ordered_groups]
    while muscles and not muscles[-1]:
        muscles.pop()
    return grid_names, muscles, fsamp
