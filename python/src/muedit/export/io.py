"""Readers for previously exported BIDS EMG datasets."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from muedit.export.bids import _ensure_pyedflib


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
            signals.append(reader.readSignal(ch_idx))
        data = np.vstack(signals)
    finally:
        reader.close()
    return data, fsamp, selection.bad_mask
