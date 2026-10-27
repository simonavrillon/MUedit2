"""BIDS EMG signal loader and targeted single-grid read helpers."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from muedit.errors import SignalLoadError
from muedit.io._bids_layout import BidsEntities, dataset_root, entity_pairs
from muedit.io.aux_channels import aux_kinds
from muedit.models import SignalImport
from muedit.signal.grid import is_known_grid
from muedit.storage import ArrayStore, RamStore

_GRID_LABEL = re.compile(r"^grid\s*(\d+)$", re.IGNORECASE)
# Target-phase labels written by MUedit and MUniverse, and by the MUnitQuest tutorials.
_RAMP_EVENTS = {"linear_isometric_ramp", "linear_ramp"}
_PLATEAU_EVENTS = {"steady_isometric", "steady_hold", "steady"}
_DYNAMIC_EVENTS = {"sinusoid", "irregular"}
# EMGReference values that name a montage, not where the reference sits ([T1] writes "monopolar").
_REFERENCE_KEYWORDS = {
    "",
    "n/a",
    "channelspecific",
    "bipolar",
    "monopolar",
    "unipolar",
    "ref",
    "reference",
    "average",
    "common average",
    "car",
}


def _ensure_pyedflib() -> Any:
    """Import and return ``pyedflib`` with a clear install hint on failure."""
    try:
        import pyedflib
    except ImportError as exc:
        raise ImportError(
            "pyedflib is required for BIDS EMG (EDF/BDF). Install it with `pip install pyedflib`."
        ) from exc
    return pyedflib


def _open_edf(path: Path) -> Any:
    """A ``pyedflib.EdfReader`` on ``path``; a file pyedflib cannot read is a ``SignalLoadError``."""
    pyedflib = _ensure_pyedflib()
    try:
        return pyedflib.EdfReader(str(path))
    except OSError as exc:
        raise SignalLoadError(f"Cannot read {path.name} as EDF/BDF: {exc}") from exc


def _parse_tsv_number(value: float | str | None) -> float | str:
    """Convert a channels.tsv cell to float, preserving ``"n/a"`` placeholders."""
    if value is None or value == "" or str(value).strip().lower() == "n/a":
        return "n/a"
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _parse_unit(value: str | None) -> str | None:
    """A channels.tsv ``units`` cell, or ``None`` when blank or ``"n/a"``."""
    text = (value or "").strip()
    return None if not text or text.lower() == "n/a" else text


def _collapse_units(units: list[str]) -> str | list[str] | None:
    """Aux units as one shared unit, a per-row list when they differ, or ``None`` when unknown."""
    if all(u == "n/a" for u in units):
        return None
    if len(set(units)) == 1:
        return units[0]
    return units


def _declared_sample_count(sidecar: dict[str, Any], fsamp: float) -> int | None:
    """Sample count implied by the sidecar's ``RecordingDuration``, when usable."""
    # EDF/BDF stores whole data records, so the writer zero-pads the last one:
    # RecordingDuration is the only record of where the signal really ends.
    duration = sidecar.get("RecordingDuration")
    if duration is None or fsamp <= 0:
        return None
    try:
        n_samples = int(round(float(duration) * fsamp))
    except (TypeError, ValueError):
        return None
    return n_samples if n_samples > 0 else None


def _inherited(emg_path: Path, entity_label: str, suffix: str) -> list[Path]:
    """Files named ``…_<suffix>`` whose entities all match the recording's, least specific first."""
    root = dataset_root(emg_path) or emg_path.parent
    wanted = entity_pairs(entity_label)
    folders = [root]
    if "sub" in wanted:
        folders.append(root / f"sub-{wanted['sub']}")
        if "ses" in wanted:
            folders.append(folders[-1] / f"ses-{wanted['ses']}")
    folders.append(emg_path.parent)
    found: list[tuple[int, int, Path]] = []
    for level, folder in enumerate(dict.fromkeys(folders)):
        for path in folder.glob(f"*{suffix}"):
            if path.name != suffix and not path.name.endswith(f"_{suffix}"):
                continue
            entities = entity_pairs(path.name[: -len(suffix)].rstrip("_"))
            if all(wanted.get(k) == v for k, v in entities.items()):
                found.append((level, len(entities), path))
    return [path for *_, path in sorted(found)]


def _read_sidecar(emg_path: Path, entity_label: str) -> dict[str, Any]:
    """The recording's ``*_emg.json`` merged over the sidecars it inherits, or ``{}``."""
    merged: dict[str, Any] = {}
    for path in _inherited(emg_path, entity_label, "emg.json"):
        with path.open("r", encoding="utf-8") as f:
            try:
                merged.update(json.load(f))
            except json.JSONDecodeError as exc:
                raise SignalLoadError(f"{path.name} is not valid JSON: {exc}") from exc
    return merged


def resolve_bids_emg_path(bids_root: Path, entity_label: str) -> Path:
    """Resolve the EDF/BDF path for a BIDS EMG recording."""
    emg_dir = BidsEntities.parse(entity_label).emg_dir(bids_root)
    for ext in (".bdf", ".edf"):
        candidate = emg_dir / f"{entity_label}_emg{ext}"
        if candidate.exists():
            return candidate
    candidates = list(emg_dir.glob(f"{entity_label}_emg.*"))
    if candidates:
        return candidates[0]
    raise SignalLoadError(f"Cannot find EMG file for {entity_label} in {emg_dir}")


def resolve_bids_channels_tsv(emg_dir: Path, entity_label: str) -> Path:
    """Resolve the channels TSV associated with a BIDS EMG recording."""
    for candidate in [
        emg_dir / f"{entity_label}_channels.tsv",
        emg_dir / f"{entity_label}_emg_channels.tsv",  # backward compat
    ]:
        if candidate.exists():
            return candidate
    raise SignalLoadError(f"Cannot find channels.tsv for {entity_label} in {emg_dir}")


def _blank(value: str | None) -> bool:
    """Whether a TSV cell is empty or ``n/a``."""
    return (value or "").strip().lower() in ("", "n/a")


def read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """The columns and rows of a BIDS TSV file."""
    with path.open("r", encoding="utf-8") as f:
        # BIDS TSV has no quoting: a cell starting with '"' is plain text.
        reader = csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        return list(reader.fieldnames or []), list(reader)


def _grid_number(label: str) -> int | None:
    """``n`` for a ``Grid<n>`` label, else ``None``."""
    match = _GRID_LABEL.match(label.strip())
    return int(match[1]) if match else None


def grid_group_order(labels: Iterable[str]) -> list[str]:
    """Grid labels in grid order: by number when all are ``Grid<n>``, else as first listed."""
    unique = list(dict.fromkeys(labels))
    if all(_grid_number(label) is not None for label in unique):
        return sorted(unique, key=lambda label: _grid_number(label) or 0)
    return unique


def is_grid_row(row: dict[str, str]) -> bool:
    """Whether a channels.tsv row is an EMG channel of a grid (an EMG row without group is aux)."""
    return (row.get("type") or "").strip().upper() == "EMG" and not _blank(row.get("group"))


@dataclass
class _GridLayout:
    """One grid: its group label, a name the grid catalogue resolves, its rows in channel order."""

    label: str
    name: str
    rows: list[int]


def _grid_layouts(rows: list[dict[str, str]], sidecar: dict[str, Any]) -> list[_GridLayout]:
    """The grids of a channels.tsv, each named after a grid catalogue model."""
    groups: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        if is_grid_row(row):
            groups.setdefault(row["group"].strip(), []).append(idx)
    model = sidecar.get("ElectrodeManufacturersModelName")
    layouts: list[_GridLayout] = []
    for label in grid_group_order(groups):
        idxs = groups[label]
        named = [
            str(n).strip()
            for n in (rows[idxs[0]].get("grid_name"), model)
            if isinstance(n, str) and not _blank(n)
        ]
        known = next((n for n in named if is_known_grid(n)), None)
        if known is None:
            given = f" (unknown model {', '.join(repr(n) for n in named)})" if named else ""
            raise SignalLoadError(
                f"Cannot determine the grid model of group '{label}'{given}: add the grid to "
                "_GRID_CATALOG in signal/grid.py, then name it in the 'grid_name' column of "
                "channels.tsv or in 'ElectrodeManufacturersModelName' of the EMG JSON sidecar."
            )
        layouts.append(_GridLayout(label, known, idxs))
    return layouts


@dataclass
class _BidsRecording:
    """A recording's channels.tsv rows, merged sidecar and grids."""

    emg_path: Path
    rows: list[dict[str, str]]
    sidecar: dict[str, Any]
    layouts: list[_GridLayout]

    def bad_mask(self, layout: _GridLayout) -> np.ndarray:
        """1 for each channel of ``layout`` marked bad, in channel order."""
        statuses = ((self.rows[r].get("status") or "").strip().lower() for r in layout.rows)
        return np.array([1 if s == "bad" else 0 for s in statuses], dtype=int)


def _read_recording(emg_path: Path, entity_label: str, channels_tsv: Path) -> _BidsRecording:
    """Channels, sidecar and grid layouts of one BIDS EMG recording."""
    _, rows = read_tsv(channels_tsv)
    sidecar = _read_sidecar(emg_path, entity_label)
    layouts = _grid_layouts(rows, sidecar)
    if not layouts:
        raise SignalLoadError(f"No EMG grid channels found in {channels_tsv}")
    return _BidsRecording(emg_path, rows, sidecar, layouts)


def _signal_index(edf_reader: Any, rows: list[dict[str, str]]) -> list[int]:
    """EDF signal of each channels.tsv row: matched by label, by position when labels do not match."""
    labels = {str(label).strip(): i for i, label in enumerate(edf_reader.getSignalLabels())}
    names = [(row.get("name") or "").strip() for row in rows]
    if len(set(names)) == len(names) and all(name in labels for name in names):
        return [labels[name] for name in names]
    return list(range(len(rows)))


def _grid(
    bids_root: Path, entity_label: str, grid_index: int
) -> tuple[_BidsRecording, _GridLayout]:
    """The recording of ``entity_label`` and its grid ``grid_index``."""
    emg_path = resolve_bids_emg_path(bids_root, entity_label)
    channels_tsv = resolve_bids_channels_tsv(emg_path.parent, entity_label)
    recording = _read_recording(emg_path, entity_label, channels_tsv)
    if not 0 <= grid_index < len(recording.layouts):
        raise SignalLoadError(f"No EMG grid {grid_index + 1} in {channels_tsv}")
    return recording, recording.layouts[grid_index]


def read_bids_emg_grid(
    bids_root: Path, entity_label: str, grid_index: int, store: ArrayStore
) -> tuple[np.ndarray, float, np.ndarray]:
    """One BIDS grid over the whole recording as writable float32 rows of ``store``, a channel at a time."""
    recording, layout = _grid(bids_root, entity_label, grid_index)

    reader = _open_edf(recording.emg_path)
    try:
        index = _signal_index(reader, recording.rows)
        channels = [index[r] for r in layout.rows]
        fsamp = float(reader.getSampleFrequency(channels[0]))
        n = min(int(reader.getNSamples()[ch]) for ch in channels)
        declared = _declared_sample_count(recording.sidecar, fsamp)
        n = n if declared is None else min(n, declared)
        data = store.allocate("bids-grid", (len(channels), n), np.float32)
        for row, ch in enumerate(channels):
            data[row] = reader.readSignal(ch, start=0, n=n)
    finally:
        reader.close()
    return data, fsamp, recording.bad_mask(layout)


def _cell_number(row: dict[str, str], key: str) -> float | None:
    """A numeric cell, or ``None`` when blank, ``n/a`` or not a number."""
    value = _parse_tsv_number(row.get(key))
    return value if isinstance(value, float) else None


def _ramp_direction(phase: dict[str, Any], row: dict[str, str]) -> str | None:
    """``up``/``down`` from the ``direction`` column, the levels, or the sign of ``mvc_rate``."""
    direction = (row.get("direction") or "").strip().lower()
    if direction in ("up", "down"):
        return direction
    start, end = phase["level_start"], phase["level_end"]
    if start is not None and end is not None and start != end:
        return "up" if end > start else "down"
    rate = _cell_number(row, "mvc_rate")
    if rate:
        return "up" if rate > 0 else "down"
    return None


def segments_from_events(path: Path, fsamp: float, n_samples: int) -> list[dict[str, Any]]:
    """The target phases an ``events.tsv`` lists, as segments in samples; other events are left out."""
    phases: list[dict[str, Any]] = []
    cycles: list[int] = []
    for row in read_tsv(path)[1]:
        label = (row.get("event_type") or row.get("trial_type") or "").strip()
        onset = _cell_number(row, "onset")
        if onset is None:
            continue
        sample = _cell_number(row, "sample")
        start = int(sample) if sample is not None else int(round(onset * fsamp))
        if label == "cycle":
            cycles.append(start)
            continue
        if label in _RAMP_EVENTS:
            kind = "ramp"
        elif label in _PLATEAU_EVENTS:
            kind = "plateau"
        elif label in _DYNAMIC_EVENTS:
            kind = label
        else:
            continue
        duration = _cell_number(row, "duration") or 0.0
        end = min(n_samples, start + int(round(duration * fsamp)))
        start = max(0, start)
        if end <= start:
            continue
        level_start = _cell_number(row, "level_start")
        if level_start is None:
            level_start = _cell_number(row, "mvc_level")
        level_end = _cell_number(row, "level_end")
        rate = _cell_number(row, "mvc_rate")
        if level_end is None and level_start is not None:
            if kind == "plateau":
                level_end = level_start
            elif kind == "ramp" and rate is not None:
                level_end = level_start + rate * duration
        phase: dict[str, Any] = {
            "start": start,
            "end": end,
            "kind": kind,
            "direction": None,
            "level_start": level_start,
            "level_end": level_end,
            "frequency_hz": _cell_number(row, "frequency_hz"),
            "amplitude": _cell_number(row, "amplitude"),
            "n_cycles": _cell_number(row, "n_cycles"),
            "cycles": [],
        }
        if kind == "ramp":
            phase["direction"] = _ramp_direction(phase, row)
        phases.append(phase)
    phases.sort(key=lambda p: p["start"])
    for i, phase in enumerate(phases):
        if phase["kind"] == "ramp" and phase["direction"] is None:
            # Without levels or a rate, a ramp right after a plateau comes down from it.
            before = phases[i - 1] if i else None
            after_plateau = before is not None and before["kind"] == "plateau"
            phase["direction"] = "down" if after_plateau else "up"
        if phase["kind"] == "sinusoid":
            phase["cycles"] = [c for c in cycles if phase["start"] <= c < phase["end"]]
        if phase["n_cycles"] is not None:
            phase["n_cycles"] = int(phase["n_cycles"])
    return phases


def _aux_name(row: dict[str, str], idx: int) -> str:
    """An aux row's name: the full ``description`` when ``name`` is its 16-char EDF label."""
    name = "" if _blank(row.get("name")) else (row.get("name") or "").strip()
    desc = (row.get("description") or "").strip()
    if desc.lower() in ("", "n/a", "auxiliary channel"):
        desc = ""
    ascii_desc = unicodedata.normalize("NFKD", desc).encode("ascii", "ignore").decode()
    if name and len(ascii_desc) > len(name) and ascii_desc.startswith(name):
        return desc
    return name or desc or f"Ch{idx:02d}"


def reference_location(value: Any) -> str | None:
    """``EMGReference`` when it describes a place, not one of the keywords or a bare electrode name."""
    text = str(value or "").strip()
    if text.lower() in _REFERENCE_KEYWORDS or re.fullmatch(r"[A-Za-z]+\d+", text):
        return None
    return text


def _muscle(row: dict[str, str]) -> str:
    """The ``target_muscle`` of a row, ``""`` when blank or ``n/a``."""
    return "" if _blank(row.get("target_muscle")) else (row.get("target_muscle") or "").strip()


def load_bids_signal(file_path: str, store: ArrayStore | None = None) -> SignalImport:
    """Load a BIDS EMG recording (BDF/EDF + sidecars) as a ``SignalImport``, written into ``store``."""
    store = store if store is not None else RamStore()
    emg_path = Path(file_path)

    if emg_path.is_dir():
        candidates = sorted(emg_path.glob("*_emg.bdf")) + sorted(emg_path.glob("*_emg.edf"))
        if not candidates:
            raise SignalLoadError(f"No BIDS EMG file found in directory: {emg_path}")
        if len(candidates) > 1:
            raise SignalLoadError(
                f"Multiple BIDS EMG files found in {emg_path}; provide a specific file path."
            )
        emg_path = candidates[0]

    stem = emg_path.stem
    entity_label = stem.removesuffix("_emg")

    channels_tsv = resolve_bids_channels_tsv(emg_path.parent, entity_label)
    recording = _read_recording(emg_path, entity_label, channels_tsv)
    rows, sidecar, layouts = recording.rows, recording.sidecar, recording.layouts
    fsamp = float(sidecar.get("SamplingFrequency") or 0.0)

    grid_rows = [r for layout in layouts for r in layout.rows]
    in_grid = set(grid_rows)
    aux_rows = [r for r in range(len(rows)) if r not in in_grid]

    def column(name: str, picked: list[int]) -> list[float | str]:
        return [_parse_tsv_number(rows[r].get(name) or "n/a") for r in picked]

    edf_reader = _open_edf(emg_path)
    try:
        index = _signal_index(edf_reader, rows)
        acquisition_date = edf_reader.getStartdatetime().isoformat()
        if fsamp == 0.0:
            fsamp = float(edf_reader.getSampleFrequency(index[grid_rows[0]]))
        n_samples = int(edf_reader.getNSamples()[index[min(grid_rows + aux_rows)]])

        # Drop the zero padding filling the last data record.
        declared = _declared_sample_count(sidecar, fsamp)
        if declared is not None and 0 < declared < n_samples:
            n_samples = declared

        # One channel at a time, straight into its row: pyedflib reads per channel anyway.
        data = store.allocate("emg", (len(grid_rows), n_samples), np.float32)
        for i, r in enumerate(grid_rows):
            data[i] = edf_reader.readSignal(index[r], n=n_samples)
        aux_data = store.allocate("aux", (len(aux_rows), n_samples), np.float32)
        for i, r in enumerate(aux_rows):
            aux_data[i] = edf_reader.readSignal(index[r], n=n_samples)
    finally:
        edf_reader.close()
    data = store.seal(data)
    aux_data = store.seal(aux_data)

    event_files = _inherited(emg_path, entity_label, "events.tsv")
    segments = segments_from_events(event_files[-1], fsamp, n_samples) if event_files else []

    aux_units = [_parse_unit(rows[r].get("units")) or "n/a" for r in aux_rows]
    aux_names = [_aux_name(rows[r], r) for r in aux_rows]
    aux_digital = [(rows[r].get("type") or "").strip().upper() == "TRIG" for r in aux_rows]

    # Read back what export_bids_emg wrote, so export -> re-import -> re-export keeps it.
    metadata: dict[str, Any] = {
        "acquisition_date": acquisition_date,
        "manufacturer": sidecar.get("Manufacturer"),
        "device_name": sidecar.get("ManufacturersModelName"),
        "software_versions": sidecar.get("SoftwareVersions"),
        "hardware_filters": sidecar.get("HardwareFilters") or ["n/a"],
        "powerline_freq": sidecar.get("PowerLineFrequency"),
        # Without these a re-export falls back to mV and relabels recordings in other units.
        "units": _parse_unit(rows[grid_rows[0]].get("units")),
        "gains": column("gain", grid_rows),
        "emg_hpf": column("low_cutoff", grid_rows),
        "emg_lpf": column("high_cutoff", grid_rows),
        "aux_units": _collapse_units(aux_units),
        "aux_gains": column("gain", aux_rows),
        "aux_hpf": column("low_cutoff", aux_rows),
        "aux_lpf": column("high_cutoff", aux_rows),
        "aux_kinds": aux_kinds(aux_names, aux_digital),
        "recording_type": sidecar.get("RecordingType"),
        "software_filters": sidecar.get("SoftwareFilters"),
        "placement_scheme": sidecar.get("EMGPlacementScheme"),
        "placement_scheme_description": sidecar.get("EMGPlacementSchemeDescription"),
        "ground_location": sidecar.get("EMGGround"),
        "reference_location": reference_location(sidecar.get("EMGReference")),
        "bad_channels_per_grid": [recording.bad_mask(layout).tolist() for layout in layouts],
        "grid_labels": [layout.label for layout in layouts],
        # Target phases the recording's events.tsv lists, drawn on the QC page as segments.
        "segments": segments,
        "bids_entity_label": entity_label,
        "bids_emg_path": str(emg_path),
    }

    return SignalImport.build(
        data=data,
        fsamp=fsamp,
        grid_names=[layout.name for layout in layouts],
        muscle_names=[_muscle(rows[min(layout.rows)]) for layout in layouts],
        aux_data=aux_data,
        aux_names=aux_names,
        metadata=metadata,
    )


def _cell(row: dict[str, str], key: str) -> str:
    """A trimmed TSV cell, ``""`` when empty or ``n/a``."""
    value = row.get(key)
    return "" if _blank(value) else str(value).strip()


def read_channels_summary(channels_tsv: Path) -> tuple[list[str], list[str], float | None]:
    """The grid models, target muscles and sampling rate a channels.tsv gives, grid by grid."""
    by_group: dict[str, tuple[str, str]] = {}
    fsamp: float | None = None
    for row in read_tsv(channels_tsv)[1]:
        if (row.get("type") or "").strip().upper() != "EMG":
            continue
        if fsamp is None:
            fsamp = _cell_number(row, "sampling_frequency")
        group = _cell(row, "group")
        if group and group not in by_group:
            by_group[group] = (_cell(row, "grid_name"), _cell(row, "target_muscle"))
    groups = grid_group_order(by_group)
    grid_names = [by_group[group][0] for group in groups]
    if not all(grid_names):
        grid_names = []  # a group label alone is no model the grid catalogue can lay out
    muscle_names = [by_group[group][1] for group in groups]
    while muscle_names and not muscle_names[-1]:
        muscle_names.pop()
    return grid_names, muscle_names, fsamp


def read_channel_groups(channels_tsv: Path) -> list[str]:
    """The ``group`` of each grid's EMG channels in a channels.tsv, grid by grid."""
    return grid_group_order(
        _cell(row, "group") for row in read_tsv(channels_tsv)[1] if is_grid_row(row)
    )


def read_sidecar_meta(bids_root: Path, entity_label: str) -> dict[str, Any]:
    """The participant and hardware fields the session form shows, from the recording's sidecars."""
    entities = BidsEntities.parse(entity_label)
    meta: dict[str, Any] = {}
    participants = bids_root / "participants.tsv"
    if participants.exists():
        participant_id = f"sub-{entities.subject}"
        row = next(
            (r for r in read_tsv(participants)[1] if r.get("participant_id") == participant_id),
            None,
        )
        if row is not None:
            meta["participant_meta"] = {
                key: row.get(key, "") for key in ("age", "sex", "handedness")
            }
    emg_json_path = entities.emg_dir(bids_root) / f"{entity_label}_emg.json"
    if emg_json_path.exists():
        with emg_json_path.open("r", encoding="utf-8") as fh:
            emg_json = json.load(fh)
        meta["manufacturer"] = emg_json.get("Manufacturer") or ""
        meta["manufacturers_model_name"] = emg_json.get("ManufacturersModelName") or ""
        meta["powerline_freq"] = emg_json.get("PowerLineFrequency")
        meta["placement_scheme"] = emg_json.get("EMGPlacementScheme")
        meta["placement_scheme_description"] = emg_json.get("EMGPlacementSchemeDescription")
        meta["software_versions"] = emg_json.get("SoftwareVersions")
        meta["ground_location"] = emg_json.get("EMGGround")
        meta["reference_location"] = reference_location(emg_json.get("EMGReference"))
    return meta
