"""BIDS EMG export helpers for writing dataset-compliant sidecar files."""

import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np


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


def _build_entities(
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


def _default_software_filters() -> dict[str, Any]:
    """Return default BIDS SoftwareFilters metadata when unspecified."""
    return {"SoftwareFilters": "n/a"}


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

    entities = _build_entities(subject, task, run, session, acquisition, recording)
    base_dir = bids_root / f"sub-{subject}"
    if session:
        base_dir = base_dir / f"ses-{session}"
    emg_dir = base_dir / "emg"
    emg_dir.mkdir(parents=True, exist_ok=True)

    suffix = f"{entities}_emg"
    edf_path = emg_dir / f"{suffix}{edf_ext}"
    json_path = emg_dir / f"{suffix}.json"
    channels_tsv = emg_dir / f"{suffix}_channels.tsv"
    electrodes_tsv = emg_dir / f"{suffix}_electrodes.tsv"
    coordsys_json = emg_dir / f"{suffix}_coordsystem.json"

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
                ch_name = (
                    aux_names[aux_idx]
                    if aux_names and aux_idx < len(aux_names)
                    else f"Aux{aux_idx+1}"
                )
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
                    (f"{ied_mm}" if ied_mm else "n/a"),
                    grid_name,
                    (
                        f"{low_cutoff[global_idx]}"
                        if isinstance(low_cutoff, list) and global_idx < len(low_cutoff)
                        else (str(low_cutoff) if low_cutoff is not None else "n/a")
                    ),
                    (
                        f"{high_cutoff[global_idx]}"
                        if isinstance(high_cutoff, list)
                        and global_idx < len(high_cutoff)
                        else (str(high_cutoff) if high_cutoff is not None else "n/a")
                    ),
                    status,
                    (
                        f"{gain[global_idx]}"
                        if isinstance(gain, list) and global_idx < len(gain)
                        else (str(gain) if gain is not None else "n/a")
                    ),
                ]
            )
        ch_idx += n_grid_ch

    if aux_names and aux_data is not None:
        for i, name in enumerate(aux_names):
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
                    (
                        f"{aux_low_cutoff[i]}"
                        if aux_low_cutoff and i < len(aux_low_cutoff)
                        else "n/a"
                    ),
                    (
                        f"{aux_high_cutoff[i]}"
                        if aux_high_cutoff and i < len(aux_high_cutoff)
                        else "n/a"
                    ),
                    "good",
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
            "interelectrode_distance",
            "grid_name",
            "low_cutoff",
            "high_cutoff",
            "status",
            "gain",
        ],
        rows,
    )

    electrode_rows = []
    electrode_counter = 1

    for g_idx, coords in enumerate(coordinates):
        ied_mm = 1.0
        if ied and g_idx < len(ied):
            ied_mm = float(ied[g_idx])

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
                    "GridSystem",
                    "Grid",
                    f"Grid{g_idx+1}",
                ]
            )
            electrode_counter += 1

    _write_tsv(
        electrodes_tsv,
        ["name", "x", "y", "z", "coordinate_system", "type", "group"],
        electrode_rows,
    )

    coord_units = "mm" if ied else "a.u."

    _write_json(
        coordsys_json,
        {
            "EMGCoordinateSystem": "Other",
            "EMGCoordinateUnits": coord_units,
            "EMGCoordinateSystemDescription": "2D Grid coordinates where X/Y are derived from row/column indices multiplied by inter-electrode distance.",
        },
    )

    emg_json = {
        "TaskName": task,
        "EMGReference": reference_description,
        "SamplingFrequency": fsamp,
        "PowerLineFrequency": powerline_freq,
        "RecordingType": "continuous",
        "EMGPlacementScheme": placement_scheme,
        "HardwareFilters": {
            "Description": str(hardware_filters) if hardware_filters else "n/a"
        },
    }
    emg_json.update(_default_software_filters())

    if emg_json_extra:
        emg_json.update(emg_json_extra)

    if placement_scheme == "Other" and placement_scheme_description:
        emg_json["EMGPlacementSchemeDescription"] = placement_scheme_description

    emg_json["EMGChannelCount"] = n_channels
    emg_json["BIDSVersion"] = "1.10.0"
    emg_json["RecordingDuration"] = float(n_samples / fsamp)

    _write_json(json_path, emg_json)

    return {
        "edf": edf_path,
        "emg_json": json_path,
        "channels_tsv": channels_tsv,
        "electrodes_tsv": electrodes_tsv,
        "coordsystem_json": coordsys_json,
    }
