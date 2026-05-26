"""BIDS EMG signal loader and targeted single-grid read helpers."""

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from muedit.signal.grid import format_hdemg_signal


def _ensure_pyedflib():
    """Import and return ``pyedflib`` with a clear install hint on failure."""
    try:
        import pyedflib
    except ImportError as exc:
        raise ImportError(
            "pyedflib is required for BIDS EMG (EDF/BDF). "
            "Install it with `pip install pyedflib`."
        ) from exc
    return pyedflib


# ---------------------------------------------------------------------------
# Targeted single-grid read helpers (low-level, used by decomposition pipeline)
# ---------------------------------------------------------------------------

@dataclass
class BidsGridSelection:
    """Selection result for one grid extracted from ``*_channels.tsv``."""
    channel_indices: list[int]
    bad_mask: np.ndarray


def _parse_entity_label(entity_label: str) -> tuple[str, str | None]:
    """Extract ``subject`` and optional ``session`` from a BIDS entity label."""
    subject = None
    session = None
    for part in entity_label.split("_"):
        if part.startswith("sub-"):
            subject = part.replace("sub-", "", 1)
        elif part.startswith("ses-"):
            session = part.replace("ses-", "", 1)
    if not subject:
        raise ValueError("Unable to parse subject from entity label")
    return subject, session


def resolve_bids_emg_path(bids_root: Path, entity_label: str) -> Path:
    """Resolve the EDF/BDF path for a BIDS EMG recording."""
    subject, session = _parse_entity_label(entity_label)
    base_dir = bids_root / f"sub-{subject}"
    if session:
        base_dir = base_dir / f"ses-{session}"
    emg_dir = base_dir / "emg"
    for ext in (".bdf", ".edf"):
        candidate = emg_dir / f"{entity_label}_emg{ext}"
        if candidate.exists():
            return candidate
    candidates = list(emg_dir.glob(f"{entity_label}_emg.*"))
    if candidates:
        return candidates[0]
    raise FileNotFoundError(f"Cannot find EMG file for {entity_label} in {emg_dir}")


def resolve_bids_channels_tsv(emg_path: Path, entity_label: str) -> Path:
    """Resolve the channels TSV associated with a BIDS EMG recording."""
    channels_path = emg_path.with_name(f"{entity_label}_emg_channels.tsv")
    if channels_path.exists():
        return channels_path
    raise FileNotFoundError(
        f"Cannot find channels.tsv for {entity_label} in {emg_path.parent}"
    )


def select_grid_channels(channels_tsv: Path, grid_index: int) -> BidsGridSelection:
    """Select EMG channel indices and bad-mask values for one grid."""
    channel_indices: list[int] = []
    bad_mask: list[int] = []
    grid_label = f"Grid{grid_index + 1}"
    with channels_tsv.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for idx, row in enumerate(reader):
            if row.get("type") != "EMG":
                continue
            if row.get("group") != grid_label:
                continue
            channel_indices.append(idx)
            status = (row.get("status") or "").lower()
            bad_mask.append(1 if status == "bad" else 0)
    if not channel_indices:
        raise ValueError(f"No EMG channels found for {grid_label} in {channels_tsv}")
    return BidsGridSelection(
        channel_indices=channel_indices, bad_mask=np.array(bad_mask, dtype=int)
    )


def load_bids_emg_grid(
    bids_root: Path,
    entity_label: str,
    grid_index: int,
    read_start: int = 0,
    read_n: int | None = None,
) -> tuple[np.ndarray, float, np.ndarray]:
    """Load EMG samples, sampling rate, and bad-mask for a single BIDS grid."""
    emg_path = resolve_bids_emg_path(bids_root, entity_label)
    channels_tsv = resolve_bids_channels_tsv(emg_path, entity_label)
    selection = select_grid_channels(channels_tsv, grid_index)

    pyedflib = _ensure_pyedflib()
    reader = pyedflib.EdfReader(str(emg_path))
    try:
        fsamp = float(reader.getSampleFrequency(selection.channel_indices[0]))
        signals = []
        for ch_idx in selection.channel_indices:
            signals.append(reader.readSignal(ch_idx, start=read_start, n=read_n))
        data = np.vstack(signals)
    finally:
        reader.close()
    return data, fsamp, selection.bad_mask


# ---------------------------------------------------------------------------
# Full-recording loader (used by LoaderFactory for .bdf / .edf files)
# ---------------------------------------------------------------------------

def load_bids_signal(filepath: str) -> dict[str, Any]:
    """Load a BIDS EMG recording (BDF/EDF + sidecars) into MUedit signal dict format.

    Accepts either a path to the ``*_emg.bdf/.edf`` file or to the ``emg/``
    directory that contains it.  All grids and auxiliary channels defined in
    the accompanying ``*_emg_channels.tsv`` are loaded and stacked.
    """
    emg_path = Path(filepath)

    if emg_path.is_dir():
        candidates = sorted(emg_path.glob("*_emg.bdf")) + sorted(emg_path.glob("*_emg.edf"))
        if not candidates:
            raise FileNotFoundError(f"No BIDS EMG file found in directory: {emg_path}")
        if len(candidates) > 1:
            raise ValueError(
                f"Multiple BIDS EMG files found in {emg_path}; provide a specific file path."
            )
        emg_path = candidates[0]

    stem = emg_path.stem  # e.g. "sub-01_ses-01_task-trapezoid_run-01_emg"
    entity_label = stem[:-4] if stem.endswith("_emg") else stem

    channels_tsv = emg_path.parent / f"{entity_label}_emg_channels.tsv"
    if not channels_tsv.exists():
        raise FileNotFoundError(f"Cannot find channels TSV for {entity_label} in {emg_path.parent}")
    json_path = emg_path.parent / f"{entity_label}_emg.json"

    fsamp = 0.0
    device_name: str | None = None
    hardware_filters: list = ["n/a"]
    if json_path.exists():
        with json_path.open("r", encoding="utf-8") as f:
            emg_json = json.load(f)
        fsamp = float(emg_json.get("SamplingFrequency", 0.0))
        device_name = emg_json.get("RecordingDevice")
        hw = emg_json.get("HardwareFilters")
        if hw:
            hardware_filters = [str(hw)]

    grid_meta: dict[str, dict[str, Any]] = {}
    grid_order: list[str] = []
    aux_channel_indices: list[int] = []
    aux_names: list[str] = []

    with channels_tsv.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for idx, row in enumerate(reader):
            ch_type = (row.get("type") or "").upper()
            group = (row.get("group") or "").strip()

            if ch_type == "EMG" and group and group.lower() != "n/a":
                if group not in grid_meta:
                    grid_meta[group] = {
                        "grid_name": row.get("grid_name", group),
                        "target_muscle": row.get("target_muscle", ""),
                        "channel_indices": [],
                        "bad_mask": [],
                    }
                    grid_order.append(group)
                grid_meta[group]["channel_indices"].append(idx)
                status = (row.get("status") or "").lower()
                grid_meta[group]["bad_mask"].append(1 if status == "bad" else 0)
            else:
                aux_channel_indices.append(idx)
                aux_names.append(row.get("name") or f"Ch{idx:02d}")

    if not grid_order:
        raise ValueError(f"No EMG grid channels found in {channels_tsv}")

    grid_order.sort(key=lambda g: (len(g), g))

    pyedflib = _ensure_pyedflib()
    all_indices: set[int] = set()
    for g in grid_order:
        all_indices.update(grid_meta[g]["channel_indices"])
    all_indices.update(aux_channel_indices)

    all_data: dict[int, np.ndarray] = {}
    edf_reader = pyedflib.EdfReader(str(emg_path))
    try:
        if fsamp == 0.0:
            fsamp = float(edf_reader.getSampleFrequency(0))
        for ch_idx in sorted(all_indices):
            all_data[ch_idx] = edf_reader.readSignal(ch_idx)
    finally:
        edf_reader.close()

    n_samples = next(iter(all_data.values())).shape[0] if all_data else 0

    grid_type_names: list[str] = []
    grid_muscles: list[str] = []
    grid_bad_masks: list[list[int]] = []
    grid_segments: list[np.ndarray] = []

    for g in grid_order:
        meta = grid_meta[g]
        grid_segments.append(np.vstack([all_data[i] for i in meta["channel_indices"]]))
        grid_type_names.append(meta["grid_name"])
        muscle = meta["target_muscle"]
        grid_muscles.append("" if (not muscle or muscle == "n/a") else muscle)
        grid_bad_masks.append(meta["bad_mask"])

    data = np.vstack(grid_segments) if grid_segments else np.zeros((0, n_samples), dtype=float)

    auxiliary = (
        np.vstack([all_data[i] for i in aux_channel_indices])
        if aux_channel_indices
        else np.zeros((0, n_samples), dtype=float)
    )

    coordinates, ieds, discard_vecs, emg_types = format_hdemg_signal(grid_type_names)

    metadata: dict[str, Any] = {
        "device_name": device_name,
        "hardware_filters": hardware_filters,
        "coordinates": coordinates,
        "ieds": ieds,
        "discard_channels": discard_vecs,
        "emg_types": emg_types,
        "bad_channels_per_grid": grid_bad_masks,
        "bids_entity_label": entity_label,
        "bids_emg_path": str(emg_path),
    }

    return {
        "data": data,
        "fsamp": fsamp,
        "gridname": grid_type_names,
        "muscle": grid_muscles,
        "device_name": device_name,
        "auxiliary": auxiliary,
        "auxiliaryname": aux_names,
        "emgnotgrid": np.zeros((0, n_samples), dtype=float),
        "metadata": metadata,
    }
