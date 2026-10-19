"""BIDS EMG export utilities and re-exports of read helpers."""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import math
import os
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

logger = logging.getLogger(__name__)

from muedit import installed_version
from muedit.errors import MueditError
from muedit.io import aux_channels
from muedit.io._bids_events import export_bids_events
from muedit.io._bids_layout import BidsEntities, dataset_root
from muedit.io._bids_reader import (
    _ensure_pyedflib,
    read_bids_emg_grid,
    read_channel_groups,
    read_channels_summary,
    read_sidecar_meta,
    read_tsv,
    resolve_bids_channels_tsv,
    resolve_bids_emg_path,
)
from muedit.signal.grid import fallback_grid_name, get_grid_electrode_metadata
from muedit.storage import BLOCK_BYTES, sample_blocks

__all__ = [
    "BidsEntities",
    "BidsFieldError",
    "bids_emg_fields",
    "check_run_export",
    "dataset_root",
    "export_bids_emg",
    "export_bids_events",
    "export_bids_mu_derivatives",
    "form_entities",
    "form_over_loader_meta",
    "parse_age",
    "read_bids_emg_grid",
    "read_channel_groups",
    "read_channels_summary",
    "read_sidecar_meta",
    "resolve_bids_channels_tsv",
    "resolve_bids_emg_path",
    "run_decomp_path",
    "write_bids_dataset_description",
    "write_derivative_description",
    "write_participant",
]


_EDF_LABEL_LEN = 16
# Every EMG channel is monopolar against one reference electrode, named here.
_REFERENCE = "R1"
_CODE_URL = "https://github.com/simonavrillon/MUedit2"


class BidsFieldError(MueditError):
    """A value given for the export that BIDS cannot take; the message says what to change."""

    field = "bids_entities"


def resolve_powerline_freq(*candidates: Any) -> float | None:
    """The first positive frequency among ``candidates``, in priority order; None when unknown."""
    for value in candidates:
        try:
            freq = float(value)
        except (TypeError, ValueError):
            continue  # None, "", "n/a"
        if freq > 0:
            return freq
    return None


#: Session-form fields that override what the loader recorded, by their ``loader_meta`` key.
_FORM_LOADER_KEYS: dict[str, str] = {
    "manufacturer": "manufacturer",
    "manufacturers_model_name": "device_name",
    "powerline_freq": "powerline_freq",
    "placement_scheme": "placement_scheme",
    "placement_scheme_description": "placement_scheme_description",
    "reference_location": "reference_location",
    "ground_location": "ground_location",
    "units": "units",
    "software_versions": "software_versions",
}


def form_over_loader_meta(
    loader_meta: Mapping[str, Any], form: Mapping[str, Any]
) -> dict[str, Any]:
    """``loader_meta`` with the session form's filled-in fields over it."""
    merged = dict(loader_meta)
    for form_key, meta_key in _FORM_LOADER_KEYS.items():
        value = form.get(form_key)
        if value not in (None, ""):
            merged[meta_key] = value
    return merged


def bids_emg_fields(
    meta: Mapping[str, Any], muscle_names: str | Sequence[str], task_description: str | None = None
) -> dict[str, Any]:
    """The metadata arguments of ``export_bids_emg``, from ``form_over_loader_meta``'s result."""
    muscle_names = (
        [muscle_names] if isinstance(muscle_names, str) else [str(m) for m in muscle_names]
    )
    return {
        "emg_json_extra": {"Muscles": muscle_names} if any(muscle_names) else None,
        "target_muscle": (
            muscle_names if len(muscle_names) > 1 else (muscle_names[0] if muscle_names else None)
        ),
        "manufacturer": meta.get("manufacturer"),
        "manufacturers_model_name": meta.get("device_name"),
        "powerline_freq": resolve_powerline_freq(meta.get("powerline_freq")),
        "placement_scheme": meta.get("placement_scheme") or "Other",
        "placement_scheme_description": meta.get("placement_scheme_description"),
        "reference_location": meta.get("reference_location"),
        "ground_location": meta.get("ground_location"),
        "task_description": task_description or None,
        "software_versions": meta.get("software_versions"),
        "units": meta.get("units") or "mV",
        "hardware_filters": meta.get("hardware_filters") or "n/a",
        "gain": meta.get("gains"),
        "low_cutoff": meta.get("emg_hpf"),
        "high_cutoff": meta.get("emg_lpf"),
        "aux_gain": meta.get("aux_gains"),
        "aux_low_cutoff": meta.get("aux_hpf"),
        "aux_high_cutoff": meta.get("aux_lpf"),
        "aux_units": meta.get("aux_units"),
        "aux_kinds": meta.get("aux_kinds"),
        "grid_labels": meta.get("grid_labels"),
        "recording_type": meta.get("recording_type") or "continuous",
        "software_filters": meta.get("software_filters"),
        "start_time": meta.get("acquisition_date"),
        "impedance": meta.get("electrode_impedances"),
    }


def form_entities(fields: Mapping[str, Any]) -> BidsEntities:
    """The entities a run's ``bids_entities`` name, with MUedit's subject and task defaults."""
    return BidsEntities(
        subject=fields.get("subject") or "01",
        session=fields.get("session"),
        task=fields.get("task") or "task",
        acquisition=fields.get("acquisition"),
        run=fields.get("run"),
        recording=fields.get("recording"),
    )


def check_run_export(
    bids_root: Path,
    fields: Mapping[str, Any],
    loader_meta: Mapping[str, Any],
    bids_metadata: Mapping[str, Any] | None = None,
) -> None:
    """Raise the ``BidsFieldError`` a run's BIDS export of these form fields would raise."""
    participant = fields.get("participant_meta")
    if isinstance(participant, Mapping):
        parse_age(participant.get("age"))
    entities = form_entities(fields)
    if (entities.emg_dir(bids_root) / f"{entities.label}_emg.json").exists():
        return  # an existing sidecar is never rewritten
    meta = form_over_loader_meta(loader_meta, fields)
    extra = bids_metadata or {}
    check_placement(
        extra.get("EMGPlacementScheme", meta.get("placement_scheme") or "Other"),
        extra.get("EMGPlacementSchemeDescription", meta.get("placement_scheme_description")),
    )


def check_placement(scheme: str | None, description: str | None) -> None:
    """Raise ``BidsFieldError`` for a placement scheme BIDS cannot take without a description."""
    if (scheme or "Other") == "Other" and not str(description or "").strip():
        raise BidsFieldError(
            "EMGPlacementScheme 'Other' requires a placement description "
            "(EMGPlacementSchemeDescription): describe how the electrodes were placed."
        )


def parse_age(value: Any) -> int | None:
    """A participant's age in years, ``None`` when not given; ``BidsFieldError`` when not one."""
    text = "" if value is None else str(value).strip()
    if text.lower() in ("", "n/a", "none"):
        return None
    try:
        age = int(text)
    except ValueError:
        raise BidsFieldError(f"age must be a whole number of years, not {text!r}") from None
    if not 0 <= age <= 150:
        raise BidsFieldError(f"age must be between 0 and 150 years, not {age}")
    return age


def run_decomp_path(source: str | Path, loader_meta: Mapping[str, Any]) -> tuple[Path, bool]:
    """A run's ``.npz``: in its BIDS dataset's MUedit derivatives (True), else beside ``source``."""
    emg_path, label = loader_meta.get("bids_emg_path"), loader_meta.get("bids_entity_label")
    root = dataset_root(Path(emg_path).resolve()) if emg_path and label else None
    if root is not None:
        try:
            folder = BidsEntities.parse(str(label)).derivative_dir(root, "decomp")
        except ValueError:
            pass  # a file in a sub- folder whose name is no BIDS label
        else:
            return folder / f"{label}_decomp.npz", True
    path = Path(source)
    return path.with_name(path.stem + "_decomp.npz"), False


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON sidecar file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=False)


def _write_tsv(path: Path, header: list[str], rows: Iterable[Iterable[Any]]) -> None:
    """Write a tab-delimited file with header and row values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("\t".join(header) + "\n")
        for row in rows:
            f.write("\t".join("" if v is None else str(v) for v in row) + "\n")


def _fmt_hz(val: Any) -> str:
    """Format a filter frequency: round to 4 decimal places, strip trailing zeros."""
    try:
        num = float(val)
    except (TypeError, ValueError):
        return str(val) if val not in (None, "") else "n/a"
    s = f"{round(num, 4):.4f}".rstrip("0").rstrip(".")
    return s or "0"


def _shared_cutoff(val: Any, n_channels: int) -> float | str | None:
    """The cutoff every EMG channel shares, ``None`` when none is known, ``"n/a"`` when they differ."""
    if isinstance(val, (int, float)):
        return float(val)
    rows = val[:n_channels] if isinstance(val, list) else []
    numbers = [float(v) for v in rows if isinstance(v, (int, float))]
    if not numbers:
        return None
    return numbers[0] if len(numbers) == n_channels and len(set(numbers)) == 1 else "n/a"


def _group_labels(grid_labels: list[str] | None, n_grids: int) -> list[str]:
    """The ``group`` of each grid: the source's labels when there is one per grid, else ``Grid<n>``."""
    if grid_labels and len(grid_labels) == n_grids and all(grid_labels):
        return [str(label) for label in grid_labels]
    return [fallback_grid_name(i) for i in range(n_grids)]


def _aux_unit_list(aux_units: str | list[str | None] | None, n_aux: int) -> list[str]:
    """One unit per aux row: a shared unit, or a per-row list padded with ``"n/a"``."""
    if isinstance(aux_units, list):
        units = [(str(u).strip() or "n/a") if u is not None else "n/a" for u in aux_units[:n_aux]]
        return units + ["n/a"] * (n_aux - len(units))
    return [aux_units or "n/a"] * n_aux


def _impedance(impedance: list[float | None] | None, index: int) -> str:
    """The impedance of electrode ``index`` in kOhm, ``"n/a"`` when not measured."""
    value = impedance[index] if impedance and index < len(impedance) else None
    return "n/a" if value is None else _fmt_hz(value)


def _truncate_physical(val: float) -> float:
    """``val`` rounded to fit the 8 characters of an EDF physical min/max field."""
    for decimals in range(6, -1, -1):
        txt = f"{val:.{decimals}f}"
        if len(txt) <= 8:
            return float(txt)
    return float(f"{val:.6g}")


def _physical_range(lo: float, hi: float) -> tuple[float, float]:
    """EDF physical min/max for a channel spanning ``[lo, hi]``, never equal."""
    if math.isclose(lo, hi):
        lo, hi = lo - 1.0, hi + 1.0
    lo_t, hi_t = _truncate_physical(lo), _truncate_physical(hi)
    if math.isclose(lo_t, hi_t):
        lo_t, hi_t = lo_t - 1.0, hi_t + 1.0
    return lo_t, hi_t


def _row_reader(data: np.ndarray, aux_data: np.ndarray | None) -> Callable[[int, int], np.ndarray]:
    """Read EMG rows then aux rows over ``[start, stop)`` as float64; aux past its end reads as 0."""
    n_emg, n_samples = data.shape
    n_aux = 0 if aux_data is None else aux_data.shape[0]
    aux_len = 0 if aux_data is None else min(aux_data.shape[1], n_samples)

    def read(start: int, stop: int) -> np.ndarray:
        out = np.zeros((n_emg + n_aux, stop - start))
        out[:n_emg] = data[:, start:stop]
        if aux_data is not None and start < aux_len:
            end = min(stop, aux_len)
            out[n_emg:, : end - start] = aux_data[:, start:end]
        return out

    return read


def _channel_extrema(
    read: Callable[[int, int], np.ndarray], n_rows: int, n_samples: int
) -> tuple[np.ndarray, np.ndarray]:
    """Per-row minimum and maximum, read block by block."""
    mins = np.full(n_rows, np.inf)
    maxs = np.full(n_rows, -np.inf)
    for start, stop in sample_blocks(n_samples, n_rows):
        block = read(start, stop)
        np.minimum(mins, block.min(axis=1), out=mins)
        np.maximum(maxs, block.max(axis=1), out=maxs)
        del block  # before the next block is read, not when the name rebinds
    return mins, maxs


def _write_records(
    writer: Any, read: Callable[[int, int], np.ndarray], n_rows: int, n_samples: int
) -> None:
    """Write the samples one data record at a time, as ``EdfWriter.writeSamples`` lays them out."""
    spr = writer.get_smp_per_record(0)
    n_full = n_samples // spr
    per_block = max(1, BLOCK_BYTES // (n_rows * spr * 8))
    for first in range(0, n_full, per_block):
        last = min(first + per_block, n_full)
        block = read(first * spr, last * spr)
        for r in range(last - first):
            # Channel after channel within a record, spr samples each.
            if writer.blockWritePhysicalSamples(block[:, r * spr : (r + 1) * spr].ravel()) < 0:
                raise OSError("pyedflib could not write a data record")
        del block
    tail = n_samples - n_full * spr
    if tail:
        # The last record is zero-padded and written channel by channel.
        block = read(n_full * spr, n_samples)
        record = np.zeros(spr)
        for row in block:
            record[:tail] = row
            if writer.writePhysicalSamples(record) < 0:
                raise OSError("pyedflib could not write the last data record")


def export_bids_emg(
    data: np.ndarray,
    fsamp: float,
    grid_names: list[str],
    coordinates: list[np.ndarray],
    discard_channels: list[np.ndarray],
    bids_root: Path,
    ied: list[float] | None = None,
    subject: str = "01",
    task: str = "task",
    run: str | None = None,
    session: str | None = None,
    acquisition: str | None = None,
    recording: str | None = None,
    emg_json_extra: dict[str, Any] | None = None,
    powerline_freq: float | None = None,
    placement_scheme: str = "Other",
    placement_scheme_description: str | None = None,
    reference_location: str | None = None,
    ground_location: str | None = None,
    units: str = "mV",
    target_muscle: str | list[str] | None = None,
    file_format: str = "edf",
    start_time: str | dt.datetime | None = None,
    hardware_filters: str | list[str] | None = "n/a",
    gain: float | list[float] | None = None,
    low_cutoff: float | list[float] | None = None,
    high_cutoff: float | list[float] | None = None,
    aux_data: np.ndarray | None = None,
    aux_names: list[str] | None = None,
    aux_gain: list[float] | None = None,
    aux_low_cutoff: list[float] | None = None,
    aux_high_cutoff: list[float] | None = None,
    aux_units: str | list[str | None] | None = None,
    aux_kinds: list[str] | None = None,
    grid_labels: list[str] | None = None,
    manufacturer: str | None = None,
    manufacturers_model_name: str | None = None,
    task_description: str | None = None,
    software_versions: str | None = None,
    recording_type: str = "continuous",
    software_filters: str | dict[str, Any] | None = None,
    skip_existing: bool = False,
    auto_discard_channels: list[np.ndarray] | None = None,
    impedance: list[float | None] | None = None,
) -> dict[str, Path]:
    """Export EMG signals plus BIDS sidecars and return produced paths."""
    pyedflib = _ensure_pyedflib()

    def _write_json_if_missing(path: Path, payload: dict[str, Any]) -> None:
        if skip_existing and path.exists():
            logger.info("BIDS export: %s already exists, skipping", path.name)
            return
        _write_json(path, payload)

    def _write_tsv_if_missing(path: Path, header: list[str], rows: Iterable[Iterable[Any]]) -> None:
        if skip_existing and path.exists():
            logger.info("BIDS export: %s already exists, skipping", path.name)
            return
        _write_tsv(path, header, rows)

    fmt = file_format.lower()
    if fmt not in ("edf", "bdf"):
        raise BidsFieldError("file_format must be 'edf' or 'bdf'")

    entities = BidsEntities(subject, session, task, acquisition, run, recording)
    entity_label = entities.label
    # electrodes.tsv and coordsystem.json describe every task of the session.
    session_prefix = BidsEntities(subject, session).label
    emg_dir = entities.emg_dir(bids_root)
    json_path = emg_dir / f"{entity_label}_emg.json"

    # Checked only when the sidecar will be written: an existing one is never rewritten.
    extra = emg_json_extra or {}
    if not json_path.exists():
        check_placement(
            extra.get("EMGPlacementScheme", placement_scheme),
            extra.get("EMGPlacementSchemeDescription", placement_scheme_description),
        )
    emg_dir.mkdir(parents=True, exist_ok=True)

    suffix = f"{entity_label}_emg"
    # One EMG file per recording: an existing one keeps its format.
    existing = next((e for e in (fmt, "edf", "bdf") if (emg_dir / f"{suffix}.{e}").exists()), fmt)
    if existing != fmt:
        logger.info("BIDS export: %s.%s exists, keeping its format", suffix, existing)
        fmt = existing
    use_bdf = fmt == "bdf"
    edf_type = pyedflib.FILETYPE_BDFPLUS if use_bdf else pyedflib.FILETYPE_EDFPLUS
    digital_min = -8388608 if use_bdf else -32768
    digital_max = 8388607 if use_bdf else 32767
    edf_path = emg_dir / f"{suffix}.{fmt}"
    channels_tsv = emg_dir / f"{entity_label}_channels.tsv"

    n_channels, n_samples = data.shape
    if n_channels == 0 or n_samples == 0:
        raise ValueError(f"export_bids_emg requires non-empty data (got shape {data.shape})")
    if aux_data is not None and aux_data.ndim == 1:
        aux_data = aux_data.reshape(1, -1)
    if aux_data is not None and aux_data.size == 0:
        aux_data = None
    # EMG then aux rows, read in blocks: aux is cut to the EMG length, or zero-padded to it.
    read_rows = _row_reader(data, aux_data)
    n_aux = 0 if aux_data is None else aux_data.shape[0]
    n_total_channels = n_channels + n_aux

    def _unique_labels(channel_names: list[str] | None, count: int) -> list[str]:
        # EDF labels are 16 ASCII chars; channels.tsv names must match them.
        labels: list[str] = []
        used: set[str] = set()
        for i in range(count):
            raw = (
                channel_names[i]
                if channel_names and i < len(channel_names) and channel_names[i]
                else f"Aux{i + 1}"
            )
            ascii_raw = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
            base = ascii_raw.strip() or f"Aux{i + 1}"
            label = base[:_EDF_LABEL_LEN].rstrip()
            n = 1
            while label in used:
                n += 1
                tag = f"_{n}"
                label = base[: _EDF_LABEL_LEN - len(tag)].rstrip() + tag
            used.add(label)
            labels.append(label)
        return labels

    aux_labels = _unique_labels(aux_names, n_aux)
    given_names = list(aux_names or [])[:n_aux]
    given_names += [""] * (n_aux - len(given_names))
    aux_descriptions = [name or "Auxiliary Channel" for name in given_names]
    aux_unit_list = _aux_unit_list(aux_units, n_aux)
    aux_kind_list = (
        list(aux_kinds)
        if aux_kinds is not None and len(aux_kinds) == n_aux
        else list(
            aux_channels.aux_kinds(
                [n or label for n, label in zip(given_names, aux_labels, strict=True)]
            )
        )
    )
    # A trigger line holds 0/1 states, whatever unit the loader gave its stream.
    aux_unit_list = [
        "n/a" if kind == "digital" else unit
        for kind, unit in zip(aux_kind_list, aux_unit_list, strict=True)
    ]

    if skip_existing and edf_path.exists():
        logger.info("BIDS export: %s already exists, skipping signal re-encode", edf_path.name)
    else:
        # Encoded beside the destination and renamed over it, so a failed write leaves no
        # partial signal file (pyedflib needs the .edf/.bdf extension).
        tmp_path = edf_path.with_name(f".{edf_path.stem}.partial{edf_path.suffix}")
        writer = None
        try:
            writer = pyedflib.EdfWriter(
                str(tmp_path), n_channels=n_total_channels, file_type=edf_type
            )

            if start_time:
                if isinstance(start_time, str):
                    start_time = dt.datetime.fromisoformat(start_time)
                # EDF records the start time without a zone.
                writer.setStartdatetime(start_time.replace(tzinfo=None))

            mins, maxs = _channel_extrema(read_rows, n_total_channels, n_samples)
            signal_headers = []
            for idx in range(n_total_channels):
                if idx >= n_channels:
                    ch_name = aux_labels[idx - n_channels]
                    # EDF leaves an unknown physical dimension blank; channels.tsv says n/a.
                    aux_unit = aux_unit_list[idx - n_channels]
                    ch_units = "" if aux_unit == "n/a" else aux_unit
                else:
                    ch_name = f"Ch{idx + 1:02d}"
                    ch_units = units

                phys_min_t, phys_max_t = _physical_range(float(mins[idx]), float(maxs[idx]))
                header = {
                    "label": ch_name,
                    "dimension": ch_units,
                    "sample_frequency": fsamp,
                    "physical_min": phys_min_t,
                    "physical_max": phys_max_t,
                    "digital_min": digital_min,
                    "digital_max": digital_max,
                    "transducer": "",
                    "prefilter": "n/a",
                }
                signal_headers.append(header)

            writer.setSignalHeaders(signal_headers)
            _write_records(writer, read_rows, n_total_channels, n_samples)
            writer.close()
            writer = None
            os.replace(tmp_path, edf_path)
        finally:
            if writer is not None:
                writer.close()
            tmp_path.unlink(missing_ok=True)

    group_labels = _group_labels(grid_labels, len(grid_names))
    rows = []
    ied_values: list[float | None] = (
        [None] * len(grid_names) if ied is None else [float(x) for x in ied]
    )

    _ch_placement = "measured" if (placement_scheme or "").lower() == "measured" else "other"

    ch_idx = 0
    for g_idx, grid_name in enumerate(grid_names):
        mask = discard_channels[g_idx]
        n_grid_ch = mask.size
        auto = auto_discard_channels[g_idx] if auto_discard_channels is not None else None

        ied_mm = None
        if ied_values and g_idx < len(ied_values):
            ied_mm = ied_values[g_idx]

        for local_idx in range(n_grid_ch):
            global_idx = ch_idx + local_idx
            ch_name = f"Ch{global_idx + 1:02d}"
            electrode_name = f"E{global_idx + 1}"

            if mask[local_idx] == 1:
                status, status_desc = "bad", "manually excluded"
            elif auto is not None and auto[local_idx] == 1:
                status, status_desc = "bad", "auto-detected"
            else:
                status, status_desc = "good", "n/a"

            rows.append(
                [
                    ch_name,
                    "EMG",
                    units,
                    "ElectroMyoGraphy",
                    fsamp,
                    electrode_name,
                    _REFERENCE,
                    group_labels[g_idx],
                    (
                        target_muscle[g_idx]
                        if isinstance(target_muscle, list) and g_idx < len(target_muscle)
                        else (target_muscle if isinstance(target_muscle, str) else "n/a")
                    ),
                    _ch_placement,
                    "n/a",
                    (f"{ied_mm}" if ied_mm else "n/a"),
                    grid_name,
                    (
                        _fmt_hz(low_cutoff[global_idx])
                        if isinstance(low_cutoff, list) and global_idx < len(low_cutoff)
                        else (
                            _fmt_hz(low_cutoff) if isinstance(low_cutoff, (int, float)) else "n/a"
                        )
                    ),
                    (
                        _fmt_hz(high_cutoff[global_idx])
                        if isinstance(high_cutoff, list) and global_idx < len(high_cutoff)
                        else (
                            _fmt_hz(high_cutoff) if isinstance(high_cutoff, (int, float)) else "n/a"
                        )
                    ),
                    "n/a",
                    status,
                    status_desc,
                    (
                        f"{gain[global_idx]}"
                        if isinstance(gain, list) and global_idx < len(gain)
                        else (str(gain) if gain is not None else "n/a")
                    ),
                ]
            )
        ch_idx += n_grid_ch

    n_trig = 0
    if n_aux > 0:
        for i in range(n_aux):
            name = aux_labels[i]
            upper_name = name.upper()
            is_trigger = (
                aux_kind_list[i] == "digital" or "TRIG" in upper_name or "SYNC" in upper_name
            )
            ctype = "TRIG" if is_trigger else "MISC"
            n_trig += ctype == "TRIG"

            rows.append(
                [
                    name,
                    ctype,
                    aux_unit_list[i],
                    aux_descriptions[i],
                    fsamp,
                    "n/a",
                    "n/a",
                    "Aux",
                    "n/a",
                    "n/a",
                    "n/a",
                    "n/a",
                    "n/a",
                    (
                        _fmt_hz(aux_low_cutoff[i])
                        if aux_low_cutoff and i < len(aux_low_cutoff)
                        else "n/a"
                    ),
                    (
                        _fmt_hz(aux_high_cutoff[i])
                        if aux_high_cutoff and i < len(aux_high_cutoff)
                        else "n/a"
                    ),
                    "n/a",
                    "good",
                    "n/a",
                    (f"{aux_gain[i]}" if aux_gain and i < len(aux_gain) else "n/a"),
                ]
            )

    _write_tsv_if_missing(
        channels_tsv,
        [
            "name",
            "type",
            "units",
            "description",
            "sampling_frequency",
            "signal_electrode",
            "reference",
            "group",
            "target_muscle",
            "placement_scheme",
            "placement_description",
            "interelectrode_distance",
            "grid_name",
            "low_cutoff",
            "high_cutoff",
            "notch",
            "status",
            "status_description",
            "gain",
        ],
        rows,
    )

    _write_json_if_missing(
        channels_tsv.with_suffix(".json"),
        {
            "group": {
                "Description": "Identifier for the electrode grid or array this channel belongs to.",
            },
            "reference": {
                "Description": "Reference electrode of the channel; n/a for auxiliary channels.",
                "Levels": {
                    _REFERENCE: "Monopolar reference electrode"
                    + (f": {reference_location.strip()}" if reference_location else "")
                },
            },
            "target_muscle": {
                "Description": "Anatomical target of the electrode grid.",
            },
            "placement_description": {
                "Description": "Free-text description of the electrode placement procedure.",
            },
            "interelectrode_distance": {
                "Description": "Distance between adjacent electrode contacts.",
                "Units": "mm",
            },
            "grid_name": {
                "Description": "Hardware model name or identifier of the electrode grid.",
            },
            "gain": {
                "Description": "Amplifier gain applied to the channel signal.",
            },
            "status_description": {
                "Description": "Reason for channel status classification.",
            },
        },
    )

    space_labels = [
        "".join(c for c in gn if c.isalnum()) or fallback_grid_name(i)
        for i, gn in enumerate(grid_names)
    ]
    electrodes_tsv = emg_dir / f"{session_prefix}_electrodes.tsv"

    el_meta_per_grid = [get_grid_electrode_metadata(gn) for gn in grid_names]

    electrode_rows = []
    electrode_counter = 1

    for g_idx, coords in enumerate(coordinates):
        ied_mm = 1.0
        if ied and g_idx < len(ied):
            ied_mm = float(ied[g_idx])

        el_meta = el_meta_per_grid[g_idx]

        for row in coords:
            x_val = row[0] * ied_mm
            y_val = row[1] * ied_mm
            z_val = "n/a"
            if len(row) > 2:
                z_val = row[2] * ied_mm

            electrode_rows.append(
                [
                    f"E{electrode_counter}",
                    f"{x_val:.4f}",
                    f"{y_val:.4f}",
                    z_val,
                    space_labels[g_idx],
                    el_meta["ElectrodeType"],
                    el_meta["ElectrodeMaterial"],
                    _impedance(impedance, electrode_counter - 1),
                    group_labels[g_idx],
                ]
            )
            electrode_counter += 1

    _write_tsv_if_missing(
        electrodes_tsv,
        ["name", "x", "y", "z", "coordinate_system", "type", "material", "impedance", "group"],
        electrode_rows,
    )

    coordsys_paths: list[Path] = []
    seen_spaces: set[str] = set()
    for g_idx, grid_name in enumerate(grid_names):
        space_label = space_labels[g_idx]
        if space_label in seen_spaces:
            continue
        seen_spaces.add(space_label)
        coordsys_path = emg_dir / f"{session_prefix}_space-{space_label}_coordsystem.json"
        grid_ied = ied_values[g_idx] if ied_values and g_idx < len(ied_values) else None
        coord_units = "mm" if grid_ied else "n/a"
        _write_json_if_missing(
            coordsys_path,
            {
                "EMGCoordinateSystem": "Other",
                "EMGCoordinateUnits": coord_units,
                "EMGCoordinateSystemDescription": (
                    f"2D grid coordinates for {grid_name}: X/Y derived from row/column indices"
                    + (
                        f" multiplied by inter-electrode distance ({grid_ied} mm)."
                        if grid_ied
                        else "."
                    )
                ),
            },
        )
        coordsys_paths.append(coordsys_path)

    def _build_hardware_filters(hpf: float | None, lpf: float | None, raw: Any) -> Any:
        if hpf is not None and lpf is not None:
            return {
                "HighPassFilter": {"CutOffHz": round(float(hpf), 4)},
                "LowPassFilter": {"CutOffHz": round(float(lpf), 4)},
            }
        if hpf is not None:
            return {"HighPassFilter": {"CutOffHz": round(float(hpf), 4)}}
        if lpf is not None:
            return {"LowPassFilter": {"CutOffHz": round(float(lpf), 4)}}
        if isinstance(raw, dict):
            return raw
        return "n/a"

    hpf = _shared_cutoff(low_cutoff, n_channels)
    lpf = _shared_cutoff(high_cutoff, n_channels)
    # Differing cutoffs stay per channel in channels.tsv; one sidecar value would misstate them.
    sidecar_filters = (
        "n/a"
        if isinstance(hpf, str) or isinstance(lpf, str)
        else _build_hardware_filters(hpf, lpf, hardware_filters)
    )

    emg_json: dict[str, Any] = {
        "TaskName": task,
        "EMGReference": (reference_location or "").strip() or _REFERENCE,
        "SamplingFrequency": fsamp,
        "PowerLineFrequency": "n/a" if powerline_freq is None else powerline_freq,
        "RecordingType": recording_type,
        "EMGPlacementScheme": placement_scheme,
        "HardwareFilters": sidecar_filters,
        "SoftwareFilters": software_filters if software_filters is not None else "n/a",
    }

    if manufacturer:
        emg_json["Manufacturer"] = manufacturer
    if manufacturers_model_name:
        emg_json["ManufacturersModelName"] = manufacturers_model_name
    if software_versions:
        emg_json["SoftwareVersions"] = software_versions
    if task_description:
        emg_json["TaskDescription"] = task_description

    if gain is not None:
        if isinstance(gain, (int, float)):
            emg_json["Gain"] = float(gain)
        elif gain:
            unique_gains = set(gain)
            if len(unique_gains) == 1:
                emg_json["Gain"] = float(next(iter(unique_gains)))

    if grid_names:

        def _scalar_or_omit(key: str, exclude: str | None = None) -> str | None:
            vals = list(dict.fromkeys(m[key] for m in el_meta_per_grid))
            if exclude:
                vals = [v for v in vals if v != exclude]
            if len(vals) != 1:
                return None
            return vals[0]

        electrode_manufacturer = _scalar_or_omit("ElectrodeManufacturer", exclude="n/a")
        if electrode_manufacturer is not None:
            emg_json["ElectrodeManufacturer"] = electrode_manufacturer

        model = _scalar_or_omit("ElectrodeManufacturersModelName", exclude="n/a")
        if model is not None:
            emg_json["ElectrodeManufacturersModelName"] = model

        electrode_type = _scalar_or_omit("ElectrodeType")
        if electrode_type is not None:
            emg_json["ElectrodeType"] = electrode_type

        material = _scalar_or_omit("ElectrodeMaterial", exclude="n/a")
        if material is not None:
            emg_json["ElectrodeMaterial"] = material

    non_none_ieds = [v for v in ied_values if v is not None]
    if non_none_ieds and len(set(non_none_ieds)) == 1:
        emg_json["InterelectrodeDistance"] = non_none_ieds[0]

    if emg_json_extra:
        emg_json.update(emg_json_extra)

    if placement_scheme_description:
        emg_json["EMGPlacementSchemeDescription"] = placement_scheme_description
    if ground_location:
        emg_json["EMGGround"] = ground_location.strip()

    emg_json["EMGChannelCount"] = n_channels
    if n_trig:
        emg_json["TriggerChannelCount"] = n_trig
    emg_json["RecordingDuration"] = float(n_samples / fsamp)

    _write_json_if_missing(json_path, emg_json)

    result: dict[str, Path] = {
        "edf": edf_path,
        "emg_json": json_path,
        "channels_tsv": channels_tsv,
        "electrodes_tsv": electrodes_tsv,
    }
    for i, p in enumerate(coordsys_paths):
        result[f"coordsystem_json_{i}"] = p
    return result


def write_bids_dataset_description(
    bids_root: Path,
    *,
    subject: str = "1",
    age: int | None = None,
    sex: str | None = None,
    handedness: str | None = None,
) -> None:
    """Write or update dataset-level BIDS files (idempotent)."""
    bids_root.mkdir(parents=True, exist_ok=True)

    desc_path = bids_root / "dataset_description.json"
    if not desc_path.exists():
        _write_json(
            desc_path,
            {
                "Name": bids_root.name,
                "BIDSVersion": "1.11.1",
                "DatasetType": "raw",
                "License": "n/a",
                "Authors": [],
            },
        )

    bidsignore_path = bids_root / ".bidsignore"
    if not bidsignore_path.exists():
        bidsignore_path.write_text("decomp/\n", encoding="utf-8")

    parts_json_path = bids_root / "participants.json"
    if not parts_json_path.exists():
        _write_json(
            parts_json_path,
            {
                "participant_id": {"Description": "Unique participant identifier."},
                "age": {"Description": "Age of the participant.", "Units": "years"},
                "sex": {
                    "Description": "Biological sex of the participant.",
                    "Levels": {"M": "male", "F": "female", "O": "other"},
                },
                "handedness": {
                    "Description": "Handedness of the participant.",
                    "Levels": {
                        "right": "right-handed",
                        "left": "left-handed",
                        "ambidextrous": "ambidextrous",
                    },
                },
            },
        )

    parts_tsv_path = bids_root / "participants.tsv"
    participant_id = f"sub-{subject}"
    # Only the fields given replace a listed participant's values.
    given = {
        key: value
        for key, value in (
            ("age", str(age) if age is not None else None),
            ("sex", sex if sex not in ("", "n/a") else None),
            ("handedness", handedness if handedness not in ("", "n/a") else None),
        )
        if value is not None
    }
    header = ["participant_id", "age", "sex", "handedness"]

    existing: list[dict[str, str]] = []
    if parts_tsv_path.exists():
        with parts_tsv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            existing = [dict(row) for row in reader]

    row = next((r for r in existing if r.get("participant_id") == participant_id), None)
    if row is None:
        existing.append({"participant_id": participant_id, **given})
    else:
        row.update(given)

    def _sort_key(r: dict[str, str]) -> tuple[int, int, str]:
        label = (r.get("participant_id") or "").split("-")[-1]
        try:
            return (0, int(label), "")
        except ValueError:
            return (1, 0, label)

    existing.sort(key=_sort_key)
    _write_tsv(parts_tsv_path, header, [[r.get(h, "n/a") for h in header] for r in existing])


def write_participant(
    bids_root: Path, subject: str, participant_meta: Mapping[str, Any] | None
) -> None:
    """``write_bids_dataset_description`` from the session form's participant fields."""
    meta = participant_meta or {}
    write_bids_dataset_description(
        bids_root,
        subject=subject,
        age=parse_age(meta.get("age")),
        sex=meta.get("sex") or None,
        handedness=meta.get("handedness") or None,
    )


def write_derivative_description(deriv_root: Path, pipeline_name: str = "muedit") -> None:
    """Write or update the derivative dataset's description, keeping keys someone else added."""
    path = deriv_root / "dataset_description.json"
    existing: dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            existing = json.load(f)
    generated: dict[str, Any] = {"Name": "MUedit", "CodeURL": _CODE_URL}
    version = installed_version()
    if version:
        generated["Version"] = version
    others = [
        entry
        for entry in existing.get("GeneratedBy", [])
        if isinstance(entry, dict) and entry.get("Name") not in ("MUedit", pipeline_name)
    ]
    _write_json(
        path,
        {
            "Name": pipeline_name,
            **existing,
            "BIDSVersion": "1.11.1",
            "DatasetType": "derivative",
            "GeneratedBy": [generated, *others],
            # The raw dataset two levels up, named "raw" for BIDS URIs such as bids:raw:sub-01/...
            "DatasetLinks": {**existing.get("DatasetLinks", {}), "raw": "../../"},
            "SourceDatasets": [{"URL": "bids:raw:"}],
        },
    )


def _write_description_row(deriv_root: Path, desc: str, text: str) -> None:
    """Record what ``desc-<desc>`` means in the derivative root's ``descriptions.tsv``."""
    path = deriv_root / "descriptions.tsv"
    described = {r["desc_id"]: r["description"] for r in read_tsv(path)[1]} if path.exists() else {}
    described[f"desc-{desc}"] = text
    _write_tsv(path, ["desc_id", "description"], sorted(described.items()))


def export_bids_mu_derivatives(
    spike_times: Sequence[ArrayLike],
    fsamp: float,
    bids_root: Path,
    entity_label: str,
    pipeline_name: str = "muedit",
    desc: str = "decomposition",
    mu_uids: list[str] | None = None,
    mu_grid_index: list[int] | None = None,
    grid_labels: list[str] | None = None,
) -> dict[str, Path]:
    """Export motor unit discharge times as a BIDS derivative ``events.tsv``, one row per discharge."""
    deriv_emg_dir = BidsEntities.parse(entity_label).derivative_dir(bids_root, "emg", pipeline_name)
    deriv_root = bids_root / "derivatives" / pipeline_name
    write_derivative_description(deriv_root, pipeline_name)
    _write_description_row(
        deriv_root,
        desc,
        "Motor unit discharge times saved by MUedit after editing: one row per discharge, "
        "unit_id names the motor unit and group the grid it was decomposed from.",
    )

    deriv_emg_dir.mkdir(parents=True, exist_ok=True)

    events_tsv = deriv_emg_dir / f"{entity_label}_desc-{desc}_events.tsv"

    n_grids = max(max(mu_grid_index or [], default=-1) + 1, len(grid_labels or []))
    labels = _group_labels(grid_labels, n_grids)
    spikes: list[tuple[int, int, str, str]] = []
    for idx, spike_samples in enumerate(spike_times):
        uid = mu_uids[idx] if mu_uids and idx < len(mu_uids) else str(idx)
        grid = mu_grid_index[idx] if mu_grid_index and idx < len(mu_grid_index) else None
        group = labels[grid] if grid is not None and 0 <= grid < len(labels) else "n/a"
        spikes.extend((int(t), idx, uid, group) for t in np.asarray(spike_samples).reshape(-1))
    spikes.sort()  # by sample, then by unit, as BIDS wants events sorted by onset
    rows = [
        [f"{sample / fsamp:.6f}", "0", str(sample), uid, "motor-unit-spike", group]
        for sample, _, uid, group in spikes
    ]
    _write_tsv(events_tsv, ["onset", "duration", "sample", "unit_id", "event_type", "group"], rows)

    events_json = events_tsv.with_suffix(".json")
    _write_json(
        events_json,
        {
            "onset": {
                "Description": "Time of the discharge from the first sample of the recording.",
                "Units": "s",
            },
            "duration": {"Description": "A discharge is an impulse.", "Units": "s"},
            "sample": {
                "Description": "Sample index of the discharge (zero-based) at the recording's "
                "sampling frequency.",
                "Units": "samples",
            },
            "unit_id": {
                "Description": "Identifier of the motor unit, matching the mu_uid in the MUedit "
                "edit log."
            },
            "event_type": {
                "Description": "Event category.",
                "Levels": {"motor-unit-spike": "Motor unit discharge"},
            },
            "group": {
                "Description": "The grid the motor unit was decomposed from: the group of its "
                "channels in the raw *_channels.tsv."
            },
        },
    )

    return {"events_tsv": events_tsv, "events_json": events_json}
