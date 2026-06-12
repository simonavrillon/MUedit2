"""BIDS EMG export utilities and re-exports of read helpers."""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from muedit.io._bids_reader import (
    BidsGridSelection,
    _ensure_pyedflib,
    load_bids_emg_grid,
    resolve_bids_channels_tsv,
    resolve_bids_emg_path,
    select_grid_channels,
)
from muedit.signal.grid import get_grid_electrode_metadata

__all__ = [
    "export_bids_emg",
    "write_bids_dataset_description",
    "export_bids_mu_derivatives",
    "build_entities",
    "BidsGridSelection",
    "load_bids_emg_grid",
    "resolve_bids_channels_tsv",
    "resolve_bids_emg_path",
    "select_grid_channels",
]


def build_entities(
    subject: str,
    task: str,
    run: str | None = None,
    session: str | None = None,
    acquisition: str | None = None,
    recording: str | None = None,
) -> str:
    """Build the BIDS entity prefix used for EMG file basenames."""
    parts: list[str] = [f"sub-{subject}"]
    if session:
        parts.append(f"ses-{session}")
    parts.append(f"task-{task}")
    if acquisition:
        parts.append(f"acq-{acquisition}")
    if run:
        parts.append(f"run-{run}")
    if recording:
        parts.append(f"recording-{recording}")
    return "_".join(parts)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON sidecar file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=False)


def _write_tsv(
    path: Path, header: list[str], rows: Iterable[Iterable[Any]]
) -> None:
    """Write a tab-delimited file with header and row values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("\t".join(header) + "\n")
        for row in rows:
            f.write("\t".join("" if v is None else str(v) for v in row) + "\n")


def _fmt_hz(val: Any) -> str:
    """Format a filter frequency: round to 4 decimal places, strip trailing zeros.

    Non-numeric values (e.g. the ``"n/a"`` placeholder some loaders emit when a
    cutoff is unknown) are passed through as their string form rather than
    raising, so recordings without real filter metadata still export.
    """
    try:
        num = float(val)
    except (TypeError, ValueError):
        return str(val) if val not in (None, "") else "n/a"
    s = f"{round(num, 4):.4f}".rstrip("0").rstrip(".")
    return s or "0"


def _first_numeric(val: Any) -> float | None:
    """Return the first numeric entry of a scalar/list, or None if there is none."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, list):
        return next((float(v) for v in val if isinstance(v, (int, float))), None)
    return None


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
    powerline_freq: float = 50.0,
    placement_scheme: str = "ChannelSpecific",
    placement_scheme_description: str | None = None,
    reference_description: str = "ChannelSpecific",
    units: str = "uV",
    target_muscle: str | list[str] | None = None,
    file_format: str = "bdf",
    start_time: Any | None = None,
    hardware_filters: str | list[str] | None = "n/a",
    gain: float | list[float] | None = None,
    low_cutoff: float | list[float] | None = None,
    high_cutoff: float | list[float] | None = None,
    aux_data: np.ndarray | None = None,
    aux_names: list[str] | None = None,
    aux_gain: list[float] | None = None,
    aux_low_cutoff: list[float] | None = None,
    aux_high_cutoff: list[float] | None = None,
    manufacturer: str | None = None,
    manufacturers_model_name: str | None = None,
    task_description: str | None = None,
    placement_scheme_per_channel: str | None = None,
    notch: float | list[float] | None = None,
    software_versions: str | None = None,
    recording_type: str = "continuous",
    software_filters: str | dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Export EMG signals plus BIDS sidecars and return produced paths."""
    pyedflib = _ensure_pyedflib()

    def _truncate_physical(val: float) -> float:
        for decimals in range(6, -1, -1):
            txt = f"{val:.{decimals}f}"
            if len(txt) <= 8:
                return float(txt)
        return float(f"{val:.6g}")

    fmt = file_format.lower()
    if fmt not in ("edf", "bdf"):
        raise ValueError("file_format must be 'edf' or 'bdf'")
    use_bdf = fmt == "bdf"
    edf_ext = ".bdf" if use_bdf else ".edf"
    edf_type = pyedflib.FILETYPE_BDFPLUS if use_bdf else pyedflib.FILETYPE_EDFPLUS
    digital_min = -8388608 if use_bdf else -32768
    digital_max = 8388607 if use_bdf else 32767

    entities = build_entities(subject, task, run, session, acquisition, recording)
    # Electrode/coordsystem files are session-scoped per BIDS-EMG: they may not
    # carry task/acq/run (electrodes.tsv also forbids `space`, coordsystem.json
    # forbids `run`). Using a sub[/ses] prefix avoids ENTITY_NOT_IN_RULE errors
    # and the EXCESSIVE_*_SPECIFICITY warnings.
    session_prefix = f"sub-{subject}" + (f"_ses-{session}" if session else "")
    base_dir = bids_root / f"sub-{subject}"
    if session:
        base_dir = base_dir / f"ses-{session}"
    emg_dir = base_dir / "emg"
    emg_dir.mkdir(parents=True, exist_ok=True)

    suffix = f"{entities}_emg"
    edf_path = emg_dir / f"{suffix}{edf_ext}"
    json_path = emg_dir / f"{suffix}.json"
    channels_tsv = emg_dir / f"{entities}_channels.tsv"

    n_channels = data.shape[0]
    final_data = data
    if aux_data is not None and aux_data.size > 0:
        if aux_data.ndim == 1:
            aux_data = aux_data.reshape(1, -1)
        len_emg = data.shape[1]
        len_aux = aux_data.shape[1]
        if len_aux > len_emg:
            aux_data = aux_data[:, :len_emg]
        elif len_aux < len_emg:
            pad = np.zeros((aux_data.shape[0], len_emg - len_aux))
            aux_data = np.hstack([aux_data, pad])

        final_data = np.vstack([data, aux_data])

    n_total_channels, n_samples = final_data.shape

    # Auxiliary channel labels must be unique (BIDS requires unique channels.tsv
    # `name` values). De-duplicate by suffixing repeats so the EDF labels and the
    # channels.tsv rows stay in sync and valid (e.g. Quaternions, Quaternions_2).
    def _unique_labels(names: list[str] | None, count: int) -> list[str]:
        labels: list[str] = []
        seen: dict[str, int] = {}
        for i in range(count):
            base = names[i] if names and i < len(names) and names[i] else f"Aux{i+1}"
            seen[base] = seen.get(base, 0) + 1
            labels.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
        return labels

    n_aux = final_data.shape[0] - data.shape[0]
    aux_labels = _unique_labels(aux_names, n_aux)

    writer = None
    try:
        writer = pyedflib.EdfWriter(
            str(edf_path), n_channels=n_total_channels, file_type=edf_type
        )

        if start_time:
            writer.setStartdatetime(start_time)

        signal_headers = []
        signal_data = []
        for idx in range(n_total_channels):
            is_aux = idx >= data.shape[0]

            if is_aux:
                aux_idx = idx - data.shape[0]
                ch_name = aux_labels[aux_idx]
                ch_units = "a.u."
            else:
                ch_name = f"Ch{idx+1:02d}"
                ch_units = units

            signal = final_data[idx, :].astype(np.float64)
            signal_data.append(signal)

            phys_min = float(np.min(signal))
            phys_max = float(np.max(signal))
            if math.isclose(phys_min, phys_max):
                phys_min -= 1.0
                phys_max += 1.0

            phys_min_t = _truncate_physical(phys_min)
            phys_max_t = _truncate_physical(phys_max)
            if math.isclose(phys_min_t, phys_max_t):
                phys_min_t -= 1.0
                phys_max_t += 1.0

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
        writer.writeSamples(signal_data)
    finally:
        if writer is not None:
            writer.close()

    rows = []
    ied_values: list[float | None]
    if ied is None:
        ied_values = [None] * len(grid_names)
    elif isinstance(ied, list):
        ied_values = [float(x) for x in ied]
    else:
        ied_values = [float(ied)] * len(grid_names)

    # BIDS channels.tsv placement_scheme only allows "measured" or "other"
    _ch_placement = "measured" if (placement_scheme or "").lower() == "measured" else "other"

    ch_idx = 0
    for g_idx, grid_name in enumerate(grid_names):
        mask = discard_channels[g_idx]
        n_grid_ch = mask.size

        ied_mm = None
        if ied_values and g_idx < len(ied_values):
            ied_mm = ied_values[g_idx]

        for local_idx in range(n_grid_ch):
            global_idx = ch_idx + local_idx
            ch_name = f"Ch{global_idx+1:02d}"
            electrode_name = f"E{global_idx+1}"

            status = "bad" if mask[local_idx] == 1 else "good"
            status_desc = "manually excluded" if status == "bad" else "n/a"

            notch_val = (
                f"{notch[global_idx]}" if isinstance(notch, list) and global_idx < len(notch)
                else (str(notch) if notch is not None else "n/a")
            )

            rows.append(
                [
                    ch_name,
                    "EMG",
                    units,
                    "ElectroMyoGraphy",
                    fsamp,
                    electrode_name,
                    reference_description,
                    f"Grid{g_idx+1}",
                    (
                        target_muscle[g_idx]
                        if isinstance(target_muscle, list)
                        and g_idx < len(target_muscle)
                        else (
                            target_muscle if isinstance(target_muscle, str) else "n/a"
                        )
                    ),
                    placement_scheme_per_channel or _ch_placement,
                    "n/a",  # placement_description — free text, not auto-populated
                    (f"{ied_mm}" if ied_mm else "n/a"),
                    grid_name,
                    (
                        _fmt_hz(low_cutoff[global_idx])
                        if isinstance(low_cutoff, list) and global_idx < len(low_cutoff)
                        else (_fmt_hz(low_cutoff) if isinstance(low_cutoff, (int, float)) else "n/a")
                    ),
                    (
                        _fmt_hz(high_cutoff[global_idx])
                        if isinstance(high_cutoff, list) and global_idx < len(high_cutoff)
                        else (_fmt_hz(high_cutoff) if isinstance(high_cutoff, (int, float)) else "n/a")
                    ),
                    notch_val,
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

    if n_aux > 0:
        for i in range(n_aux):
            name = aux_labels[i]
            upper_name = name.upper()
            if "TRIG" in upper_name or "SYNC" in upper_name:
                ctype = "TRIG"
            else:
                ctype = "MISC"

            rows.append(
                [
                    name,
                    ctype,
                    "a.u.",
                    "Auxiliary Channel",
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
                    (
                        f"{aux_gain[i]}" if aux_gain and i < len(aux_gain) else "n/a"
                    ),
                ]
            )

    _write_tsv(
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

    _write_json(
        channels_tsv.with_suffix(".json"),
        {
            "group": {
                "Description": "Identifier for the electrode grid or array this channel belongs to.",
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

    # Space labels identify each grid's coordinate system. They live on the
    # coordsystem.json filenames (`_space-<label>`) and in the electrodes.tsv
    # `coordinate_system` column — never on the electrodes.tsv filename, which
    # BIDS-EMG does not allow a `space` entity for.
    space_labels = [
        "".join(c for c in gn if c.isalnum()) or f"Grid{i + 1}"
        for i, gn in enumerate(grid_names)
    ]
    electrodes_tsv = emg_dir / f"{session_prefix}_electrodes.tsv"

    # Per-grid electrode metadata (type, material) from the grid catalogue
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
                    space_labels[g_idx],  # must match a coordsystem.json `space` entity
                    el_meta["ElectrodeType"],
                    el_meta["ElectrodeMaterial"],
                    "n/a",  # impedance — not measured
                    f"Grid{g_idx+1}",
                ]
            )
            electrode_counter += 1

    _write_tsv(
        electrodes_tsv,
        ["name", "x", "y", "z", "coordinate_system", "type", "material", "impedance", "group"],
        electrode_rows,
    )

    coordsys_paths: list[Path] = []
    seen_spaces: set[str] = set()
    for g_idx, grid_name in enumerate(grid_names):
        space_label = space_labels[g_idx]
        # One coordsystem.json per distinct space; skip duplicate grid labels.
        if space_label in seen_spaces:
            continue
        seen_spaces.add(space_label)
        coordsys_path = emg_dir / f"{session_prefix}_space-{space_label}_coordsystem.json"
        grid_ied = ied_values[g_idx] if ied_values and g_idx < len(ied_values) else None
        coord_units = "mm" if grid_ied else "a.u."
        _write_json(
            coordsys_path,
            {
                "EMGCoordinateSystem": "Other",
                "EMGCoordinateUnits": coord_units,
                "EMGCoordinateSystemDescription": (
                    f"2D grid coordinates for {grid_name}: X/Y derived from row/column indices"
                    + (f" multiplied by inter-electrode distance ({grid_ied} mm)." if grid_ied else ".")
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

    first_hpf = _first_numeric(low_cutoff)
    first_lpf = _first_numeric(high_cutoff)

    emg_json: dict[str, Any] = {
        "TaskName": task,
        "EMGReference": reference_description,
        "SamplingFrequency": fsamp,
        "PowerLineFrequency": powerline_freq,
        "RecordingType": recording_type,
        "EMGPlacementScheme": placement_scheme,
        "HardwareFilters": _build_hardware_filters(first_hpf, first_lpf, hardware_filters),
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

    # Uniform amplifier gain → scalar sidecar field; per-channel gains stay in channels.tsv
    if gain is not None:
        if isinstance(gain, (int, float)):
            emg_json["Gain"] = float(gain)
        elif isinstance(gain, list) and gain:
            unique_gains = set(gain)
            if len(unique_gains) == 1:
                emg_json["Gain"] = float(next(iter(unique_gains)))

    # Auto-derive electrode metadata — collapse to scalar when uniform, list when mixed
    if grid_names:
        def _scalar_or_omit(key: str, exclude: str | None = None) -> str | None:
            # BIDS-EMG defines these sidecar fields as strings. When grids differ
            # we omit the field and let the per-electrode `type`/`material`
            # columns in electrodes.tsv carry the variation, as the spec advises.
            vals = list(dict.fromkeys(m[key] for m in el_meta_per_grid))  # unique, order-preserving
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

    # Write InterelectrodeDistance only when all grids share the same IED
    non_none_ieds = [v for v in ied_values if v is not None]
    if non_none_ieds and len(set(non_none_ieds)) == 1:
        emg_json["InterelectrodeDistance"] = non_none_ieds[0]

    if emg_json_extra:
        emg_json.update(emg_json_extra)

    if placement_scheme == "Other" and placement_scheme_description:
        emg_json["EMGPlacementSchemeDescription"] = placement_scheme_description

    emg_json["EMGChannelCount"] = n_channels
    n_misc = len(aux_names) if aux_names else 0
    if n_misc:
        emg_json["MISCChannelCount"] = n_misc
    emg_json["RecordingDuration"] = float(n_samples / fsamp)

    _write_json(json_path, emg_json)

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
    """Write or update dataset-level BIDS files (idempotent).

    Creates ``dataset_description.json``, ``participants.tsv``,
    ``participants.json``, and ``.bidsignore`` if they do not exist, and
    appends / updates the row for *subject* in ``participants.tsv``.
    """
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
    new_row = {
        "participant_id": participant_id,
        "age": str(age) if age is not None else "n/a",
        "sex": sex or "n/a",
        "handedness": handedness or "n/a",
    }
    header = ["participant_id", "age", "sex", "handedness"]

    existing: list[dict[str, str]] = []
    if parts_tsv_path.exists():
        with parts_tsv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            existing = [dict(row) for row in reader]

    updated = False
    for row in existing:
        if row.get("participant_id") == participant_id:
            row.update(new_row)
            updated = True
            break
    if not updated:
        existing.append(new_row)

    def _sort_key(r: dict[str, str]) -> tuple[int, int, str]:
        # Numeric subject labels sort numerically; alphanumeric labels (e.g.
        # "control01") sort lexically after them, never crashing the sort.
        label = (r.get("participant_id") or "").split("-")[-1]
        try:
            return (0, int(label), "")
        except ValueError:
            return (1, 0, label)

    existing.sort(key=_sort_key)
    _write_tsv(parts_tsv_path, header, [[r.get(h, "n/a") for h in header] for r in existing])


def export_bids_mu_derivatives(
    distimes: list[list[int]],
    fsamp: float,
    bids_root: Path,
    entities: str,
    pipeline_name: str = "muedit",
    desc: str = "decomposition",
    mu_uids: list[str] | None = None,
) -> dict[str, Path]:
    """Export motor unit spike times as BIDS derivatives events.tsv.

    Writes one row per spike with columns: onset, duration, sample, unit_id,
    description.  Also creates the derivative dataset_description.json if it
    does not exist.  The primary NPZ artefact in
    ``derivatives/muedit/sub-XX/.../decomp/`` is kept unchanged; this file is
    the BIDS-facing supplementary output.
    """
    subject, session = None, None
    for part in entities.split("_"):
        if part.startswith("sub-"):
            subject = part[4:]
        elif part.startswith("ses-"):
            session = part[4:]
    if not subject:
        raise ValueError(f"Cannot parse subject from entities: {entities}")

    deriv_root = bids_root / "derivatives" / pipeline_name
    deriv_desc_path = deriv_root / "dataset_description.json"
    if not deriv_desc_path.exists():
        _write_json(
            deriv_desc_path,
            {
                "Name": pipeline_name,
                "BIDSVersion": "1.11.1",
                "DatasetType": "derivative",
                "GeneratedBy": [{"Name": pipeline_name}],
            },
        )

    deriv_emg_dir = deriv_root / f"sub-{subject}"
    if session:
        deriv_emg_dir = deriv_emg_dir / f"ses-{session}"
    deriv_emg_dir = deriv_emg_dir / "emg"
    deriv_emg_dir.mkdir(parents=True, exist_ok=True)

    events_tsv = deriv_emg_dir / f"{entities}_desc-{desc}_events.tsv"

    spike_rows = []
    for idx, spike_samples in enumerate(distimes):
        uid = mu_uids[idx] if mu_uids and idx < len(mu_uids) else str(idx)
        for sample in sorted(spike_samples):
            onset = sample / fsamp
            spike_rows.append([f"{onset:.6f}", "0.0", str(sample), uid, "motor-unit-spike"])

    _write_tsv(events_tsv, ["onset", "duration", "sample", "unit_id", "description"], spike_rows)

    events_json = events_tsv.with_suffix(".json")
    _write_json(
        events_json,
        {
            "onset": {"Description": "Time of spike onset relative to recording start.", "Units": "s"},
            "duration": {"Description": "Duration of the spike event.", "Units": "s"},
            "sample": {"Description": "Sample index of spike onset at the recording sampling frequency.", "Units": "samples"},
            "unit_id": {"Description": "Unique identifier of the motor unit, matching the mu_uid in the MUedit2 editlog."},
            "description": {"Description": "Event type label."},
        },
    )

    return {"events_tsv": events_tsv, "events_json": events_json}

