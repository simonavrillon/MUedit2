"""BIDS-specific helpers for decomposition loading and channel sidecar parsing."""

from __future__ import annotations

import csv
import json
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


def _parse_all_bids_entities(entity_label: str) -> dict[str, str | None]:
    """Extract all BIDS key-value pairs from an entity label string."""
    keys = ("sub", "ses", "task", "acq", "run", "recording")
    result: dict[str, str | None] = {k: None for k in keys}
    for part in str(entity_label).split("_"):
        for k in keys:
            prefix = f"{k}-"
            if part.startswith(prefix) and len(part) > len(prefix):
                result[k] = part[len(prefix):]
    return result


def _parse_subject_session_from_entity_label(entity_label: str) -> tuple[str, str | None]:
    """Extract the subject (default ``"01"``) and optional session from an entity label."""
    subject = "01"
    session: str | None = None
    for part in str(entity_label).split("_"):
        if part.startswith("sub-") and len(part) > 4:
            subject = part[4:]
        elif part.startswith("ses-") and len(part) > 4:
            session = part[4:]
    return subject, session


def _infer_bids_root_from_decomp_path(filepath: str) -> Path | None:
    """Infer the BIDS dataset root from a decomposition file path.

    Handles both the current ``derivatives/muedit/sub-X/...`` layout and the
    legacy ``sub-X/...`` layout, falling back to a ``muedit_out`` marker.
    Returns ``None`` when no root can be determined.
    """
    raw = str(filepath or "")
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    parts = list(path.parts)

    # New layout: .../bids_root/derivatives/muedit/sub-X/... → everything before derivatives/
    for i, part in enumerate(parts):
        if (
            part.lower() == "derivatives"
            and i + 1 < len(parts)
            and parts[i + 1].lower() == "muedit"
            and i > 0
        ):
            return Path(*parts[:i])

    # Legacy layout: .../bids_root/sub-X/... → everything before sub-X/
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
    """Return a trimmed sidecar value, collapsing ``"n/a"`` and blanks to ``""``."""
    text = str(value or "").strip()
    if not text or text.lower() == "n/a":
        return ""
    return text


def _grid_sort_key(group: str) -> tuple[int, str]:
    """Sort key ordering ``GridN`` group labels numerically, others lexically last."""
    text = str(group or "").strip()
    lower = text.lower()
    if lower.startswith("grid"):
        suffix = text[4:].strip()
        if suffix.isdigit():
            return (int(suffix), text)
    return (10**9, text)


def _read_bids_channels_sidecar(channels_path: Path) -> tuple[list[str], list[str], float | None]:
    """Parse a channels.tsv into ordered grid names, target muscles, and sampling rate.

    Considers only EMG-type rows, dedupes by grid ``group`` (first row wins),
    orders groups via :func:`_grid_sort_key`, and trims trailing empty muscle
    entries. Returns ``([], [], fsamp_or_None)`` when no EMG rows are present.
    """
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


def read_bids_sidecar_meta(bids_root: Path, entity_label: str) -> dict[str, Any]:
    """Read participant and hardware metadata from BIDS sidecars for an entity.

    Returns a flat mapping ready to merge into a loaded-decomposition or preview
    payload: a nested ``participant_meta`` (age/sex/handedness) read from
    ``participants.tsv`` plus the hardware/recording fields round-tripped from the
    ``_emg.json`` sidecar. Missing files simply contribute no keys. I/O and parse
    errors are intentionally left to propagate so callers can decide whether the
    enrichment is best-effort.
    """
    subject, session = _parse_subject_session_from_entity_label(entity_label)
    meta: dict[str, Any] = {}

    parts_tsv = bids_root / "participants.tsv"
    if parts_tsv.exists():
        participant_id = f"sub-{subject}"
        with parts_tsv.open("r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                if row.get("participant_id") == participant_id:
                    meta["participant_meta"] = {
                        "age": row.get("age", ""),
                        "sex": row.get("sex", ""),
                        "handedness": row.get("handedness", ""),
                    }
                    break

    emg_dir = bids_root / f"sub-{subject}"
    if session:
        emg_dir = emg_dir / f"ses-{session}"
    emg_dir = emg_dir / "emg"
    emg_json_path = emg_dir / f"{entity_label}_emg.json"
    if emg_json_path.exists():
        with emg_json_path.open("r", encoding="utf-8") as fh:
            emg_json = json.load(fh)
        meta["manufacturer"] = emg_json.get("Manufacturer") or ""
        meta["manufacturers_model_name"] = emg_json.get("ManufacturersModelName") or ""
        meta["powerline_freq"] = emg_json.get("PowerLineFrequency")
        meta["placement_scheme"] = emg_json.get("EMGPlacementScheme")
        meta["placement_scheme_description"] = emg_json.get("EMGPlacementSchemeDescription")
        meta["software_versions"] = emg_json.get("SoftwareVersions")

    return meta
