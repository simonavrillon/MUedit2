from __future__ import annotations

import csv
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException, UploadFile
from fastapi.responses import Response

from muedit.api.cache import (
    _load_bids_grid,
    _get_edit_signal_context,
    _get_edit_signal_context_by_label,
    _store_edit_signal_context,
)
from muedit.api.common import (
    as_float,
    as_int,
    make_json_safe,
    parse_entity_label,
    safe_unlink,
    save_upload_to_temp,
)
from muedit.decomp.signal_io import (
    build_pulse_trains_from_distimes,
    load_decomposition_file,
    load_decomposition_signal_context,
    normalize_distimes,
)
from muedit.decomp.algorithm import rem_duplicates
from muedit.editing import (
    add_spikes_in_roi,
    delete_high_discharge_rate_spikes_in_roi,
    delete_spikes_in_roi,
    remove_discharge_rate_outliers,
    update_motor_unit_filter_window,
)
from muedit.utils import format_hdemg_signal


def _infer_bids_root_from_decomp_path(filepath: str) -> Path | None:
    raw = str(filepath or "")
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    parts = list(path.parts)

    # Preferred: directory before first sub-<id> segment.
    sub_idx = next(
        (i for i, part in enumerate(parts) if part.lower().startswith("sub-")),
        -1,
    )
    if sub_idx > 0:
        return Path(*parts[:sub_idx])

    # Fallback: keep root up to muedit_out marker when present.
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


def _expected_grid_count(loaded: dict[str, Any]) -> int:
    count = 0
    grid_names = loaded.get("grid_names")
    if isinstance(grid_names, list):
        count = max(count, len(grid_names))
    muscles = loaded.get("muscle")
    if isinstance(muscles, list):
        count = max(count, len(muscles))
    mu_grid_index = loaded.get("mu_grid_index")
    if isinstance(mu_grid_index, list) and mu_grid_index:
        try:
            count = max(count, int(max(mu_grid_index)) + 1)
        except Exception:
            pass
    return max(1, count)


def _pad_grid_names(names: list[str], expected_count: int, fallback: list[str]) -> list[str]:
    out = [str(x).strip() for x in (names or []) if str(x).strip()]
    if not out:
        out = [str(x).strip() for x in (fallback or []) if str(x).strip()]
    target_count = max(int(expected_count or 0), len(out))
    while len(out) < target_count:
        fill = out[-1] if out else f"Grid {len(out) + 1}"
        out.append(fill)
    return out


def _normalize_muscle_names(payload: dict[str, Any]) -> list[str]:
    muscle_names_raw = payload.get("muscle_names") or payload.get("muscle") or []
    if isinstance(muscle_names_raw, str):
        return [muscle_names_raw.strip()] if muscle_names_raw.strip() else []
    if isinstance(muscle_names_raw, (list, tuple)):
        return [str(x).strip() for x in muscle_names_raw if str(x).strip()]
    return []


def _parse_subject_session_from_entity_label(entity_label: str) -> tuple[str, str | None]:
    subject = "01"
    session: str | None = None
    for part in str(entity_label).split("_"):
        if part.startswith("sub-") and len(part) > 4:
            subject = part[4:]
        elif part.startswith("ses-") and len(part) > 4:
            session = part[4:]
    return subject, session


def _save_npz(
    out_path: str | Path,
    pulse_trains: np.ndarray,
    distimes: list[list[int]],
    fsamp: Any,
    grid_names: list[str],
    mu_grid_index: list[int],
    muscle_names: list[str],
    parameters: dict[str, Any],
    total_samples: int,
) -> None:
    np.savez_compressed(
        out_path,
        pulse_trains=pulse_trains,
        discharge_times=np.array(distimes, dtype=object),
        fsamp=fsamp,
        grid_names=np.array(grid_names, dtype=object),
        mu_grid_index=np.array(mu_grid_index, dtype=int),
        muscle_names=np.array(muscle_names, dtype=object),
        muscle=np.array(muscle_names, dtype=object),
        parameters=np.array([parameters], dtype=object),
        total_samples=total_samples,
    )


def _save_editlog(
    editlog_path: Path,
    mu_uids: list[str],
    edit_history: list[dict[str, Any]],
) -> None:
    payload = {"mu_uids": mu_uids, "history": edit_history}
    with editlog_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


async def load_decomposition(file: UploadFile) -> dict[str, Any]:
    tmp_path = await save_upload_to_temp(file)
    try:
        loaded = load_decomposition_file(tmp_path)
        signal_ctx = load_decomposition_signal_context(tmp_path)
        if signal_ctx:
            loaded["edit_signal_token"] = _store_edit_signal_context(
                signal_ctx,
                file.filename,
            )
        loaded["file_label"] = file.filename
        return make_json_safe(loaded)
    finally:
        safe_unlink(tmp_path)


def _encode_edit_load_f32(loaded: dict[str, Any]) -> bytes | None:
    """Custom MELD v1 binary: JSON metadata header + float32 pulse matrix."""
    pulse_raw = loaded.get("pulse_trains_full")
    if pulse_raw is None:
        return None

    pulse = np.asarray(pulse_raw, dtype=np.float32)
    if pulse.ndim != 2:
        return None

    metadata = dict(loaded)
    metadata.pop("pulse_trains_full", None)
    metadata["pulse_shape"] = [int(pulse.shape[0]), int(pulse.shape[1])]
    metadata["pulse_dtype"] = "float32"
    metadata["pulse_binary"] = True
    meta_bytes = json.dumps(make_json_safe(metadata), separators=(",", ":")).encode("utf-8")

    parts: list[bytes] = []
    parts.append(b"MELD")
    parts.append(struct.pack("<I", 1))
    parts.append(struct.pack("<I", len(meta_bytes)))
    parts.append(struct.pack("<I", int(pulse.shape[0])))
    parts.append(struct.pack("<I", int(pulse.shape[1])))
    parts.append(meta_bytes)
    parts.append(pulse.astype("<f4", copy=False).tobytes(order="C"))
    return b"".join(parts)


async def load_decomposition_binary(file: UploadFile) -> Response | dict[str, Any]:
    loaded = await load_decomposition(file)
    blob = _encode_edit_load_f32(loaded)
    if blob is None:
        return loaded
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"x-muedit-format": "edit-load-f32-v1"},
    )


def load_decomposition_from_path(filepath: str) -> dict[str, Any]:
    loaded = load_decomposition_file(filepath)
    signal_ctx = load_decomposition_signal_context(filepath)
    if signal_ctx:
        loaded["edit_signal_token"] = _store_edit_signal_context(
            signal_ctx,
            Path(filepath).name,
        )
    file_label = Path(filepath).name
    loaded["file_label"] = file_label

    bids_root = _infer_bids_root_from_decomp_path(filepath)
    if bids_root is not None:
        loaded["bids_root"] = str(bids_root)
        try:
            entity_label = parse_entity_label(file_label)
            subject, session = _parse_subject_session_from_entity_label(entity_label)
            emg_dir = bids_root / f"sub-{subject}"
            if session:
                emg_dir = emg_dir / f"ses-{session}"
            emg_dir = emg_dir / "emg"
            channels_path = emg_dir / f"{entity_label}_emg_channels.tsv"
            if channels_path.exists():
                grid_names, muscles, fsamp = _read_bids_channels_sidecar(channels_path)
                expected_count = _expected_grid_count(loaded)
                if grid_names:
                    loaded["grid_names"] = _pad_grid_names(
                        grid_names, expected_count, loaded.get("grid_names") or []
                    )
                if muscles:
                    loaded["muscle"] = muscles
                if fsamp and fsamp > 0:
                    loaded["fsamp"] = fsamp
        except (ValueError, OSError, csv.Error, KeyError):
            pass  # best-effort; I/O and parse errors are non-fatal

    editlog_path = Path(filepath).with_suffix(".json")
    if editlog_path.exists():
        try:
            with editlog_path.open("r", encoding="utf-8") as fh:
                editlog = json.load(fh)
            if isinstance(editlog.get("mu_uids"), list):
                loaded["mu_uids"] = editlog["mu_uids"]
            if isinstance(editlog.get("history"), list):
                loaded["edit_history"] = editlog["history"]
        except (OSError, ValueError, KeyError):
            pass  # best-effort; missing or corrupt editlog is non-fatal

    return make_json_safe(loaded)


def load_decomposition_binary_from_path(filepath: str) -> Response | dict[str, Any]:
    loaded = load_decomposition_from_path(filepath)
    blob = _encode_edit_load_f32(loaded)
    if blob is None:
        return loaded
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"x-muedit-format": "edit-load-f32-v1"},
    )


def _normalize_flagged(raw: Any, nmu: int) -> list[bool]:
    if not isinstance(raw, (list, tuple)):
        return [False] * nmu
    out = [bool(v) for v in raw[:nmu]]
    if len(out) < nmu:
        out.extend([False] * (nmu - len(out)))
    return out


def _generate_mu_uids(mu_grid_index: list[int]) -> list[str]:
    counts: dict[int, int] = {}
    uids: list[str] = []
    for grid_idx in mu_grid_index:
        count = counts.get(grid_idx, 0)
        uids.append(f"g{grid_idx}_mu{count}")
        counts[grid_idx] = count + 1
    return uids


def _normalize_mu_grid_index(raw: Any, nmu: int) -> list[int]:
    if not isinstance(raw, (list, tuple)):
        return [0] * nmu
    vals = [int(x) for x in raw[:nmu]]
    if len(vals) < nmu:
        vals.extend([0] * (nmu - len(vals)))
    return vals


def save_edits(payload: dict[str, Any]):
    distimes_raw = payload.get("distimes") or payload.get("discharge_times") or []
    distimes = normalize_distimes(distimes_raw)
    pulse_trains_raw = payload.get("pulse_trains")
    total_samples = as_int(payload.get("total_samples"), "total_samples", default=0)
    if total_samples <= 0:
        raise HTTPException(
            status_code=400, detail="total_samples is required to save edits"
        )

    fsamp_raw = payload.get("fsamp")
    fsamp = float(fsamp_raw) if fsamp_raw is not None else None
    grid_names = payload.get("grid_names") or ["Grid 1"]
    mu_grid_index = _normalize_mu_grid_index(payload.get("mu_grid_index"), len(distimes))
    parameters = payload.get("parameters") or {}
    muscle_names = _normalize_muscle_names(payload)

    if isinstance(parameters, dict) and muscle_names and not parameters.get("target_muscle"):
        parameters["target_muscle"] = (
            muscle_names if len(muscle_names) > 1 else muscle_names[0]
        )

    pulse_trains = None
    if pulse_trains_raw is not None:
        try:
            pulse_trains = np.array(pulse_trains_raw, dtype=float)
        except (TypeError, ValueError):
            pulse_trains = None
    if (
        pulse_trains is None
        or pulse_trains.size == 0
        or pulse_trains.ndim != 2
        or pulse_trains.shape[0] != len(distimes)
        or pulse_trains.shape[1] != total_samples
    ):
        pulse_trains = build_pulse_trains_from_distimes(distimes, total_samples)

    mu_uids_raw = payload.get("mu_uids")
    mu_uids: list[str] = (
        list(mu_uids_raw)
        if isinstance(mu_uids_raw, (list, tuple)) and len(mu_uids_raw) == len(distimes)
        else _generate_mu_uids(mu_grid_index)
    )
    edit_history: list[dict[str, Any]] = list(payload.get("edit_history") or [])

    remove_flagged = bool(payload.get("remove_flagged", True))
    remove_duplicates = bool(payload.get("remove_duplicates", True))

    if remove_flagged and distimes:
        flagged = _normalize_flagged(payload.get("flagged"), len(distimes))
        keep_idx = [
            i
            for i, spikes in enumerate(distimes)
            if not flagged[i]
        ]
        distimes = [distimes[i] for i in keep_idx]
        mu_grid_index = [mu_grid_index[i] for i in keep_idx]
        mu_uids = [mu_uids[i] for i in keep_idx]
        pulse_trains = pulse_trains[keep_idx, :] if pulse_trains.size else pulse_trains

    if remove_duplicates and len(distimes) > 1 and fsamp and fsamp > 0:
        dup_tol = float(parameters.get("duplicatesthresh", 0.3))
        dedup_pulses, dedup_distimes, kept_idx = rem_duplicates(
            np.asarray(pulse_trains, dtype=float),
            [np.asarray(d, dtype=int) for d in distimes],
            [np.asarray(d, dtype=int) for d in distimes],
            round(fsamp / 40),
            0.00025,
            dup_tol,
            fsamp,
        )
        pulse_trains = dedup_pulses if dedup_pulses.size else np.zeros((0, total_samples))
        distimes = [
            sorted({int(v) for v in np.asarray(d, dtype=int).tolist() if int(v) >= 0})
            for d in dedup_distimes
        ]
        mu_grid_index = [mu_grid_index[i] for i in kept_idx]
        mu_uids = [mu_uids[i] for i in kept_idx]

    bids_root = payload.get("bids_root")
    if not bids_root:
        raise HTTPException(status_code=400, detail="bids_root is required for edit save")
    file_label = payload.get("file_label") or ""
    entity_label = payload.get("entity_label") or parse_entity_label(file_label)
    subject, session = _parse_subject_session_from_entity_label(entity_label)
    base_dir = Path(bids_root) / f"sub-{subject}"
    if session:
        base_dir = base_dir / f"ses-{session}"
    decomp_dir = base_dir / "decomp"
    decomp_dir.mkdir(parents=True, exist_ok=True)
    out_path = decomp_dir / f"{entity_label}_edited.npz"
    _save_npz(
        out_path,
        pulse_trains,
        distimes,
        fsamp,
        grid_names,
        mu_grid_index,
        muscle_names,
        parameters,
        total_samples,
    )
    _save_editlog(out_path.with_suffix(".json"), mu_uids, edit_history)
    return make_json_safe({"saved": True, "path": str(out_path)})


def update_filter(payload: dict[str, Any]) -> dict[str, Any]:
    bids_root = payload.get("bids_root")
    edit_signal_token = payload.get("edit_signal_token")
    file_label = payload.get("file_label") or ""
    entity_label = payload.get("entity_label")
    if not entity_label and bids_root:
        entity_label = parse_entity_label(file_label)
    grid_index = as_int(payload.get("grid_index"), "grid_index", default=0)
    distimes = normalize_distimes(payload.get("distimes") or [])
    if not distimes:
        raise HTTPException(
            status_code=400, detail="distimes are required for filter update"
        )

    mu_index = as_int(payload.get("mu_index"), "mu_index", default=0)
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")
    mu_grid_index = _normalize_mu_grid_index(payload.get("mu_grid_index"), len(distimes))
    peeloff_win = as_float(payload.get("peel_off_win"), "peel_off_win", default=0.025)
    if peeloff_win <= 0:
        peeloff_win = 0.025
    use_peeloff = bool(payload.get("use_peeloff", False))

    view_start = as_int(payload.get("view_start"), "view_start", default=0)
    view_end = as_int(payload.get("view_end"), "view_end", default=0)
    if view_end <= view_start:
        raise HTTPException(status_code=400, detail="view_start/view_end are required")
    nbextchan = as_int(payload.get("nbextchan"), "nbextchan", default=1000)

    emg: np.ndarray | None = None
    fsamp: float | None = None
    emg_mask: np.ndarray | None = None

    if bids_root:
        try:
            emg, fsamp, emg_mask = _load_bids_grid(
                Path(bids_root), str(entity_label), grid_index, view_start, view_end
            )
        except (ValueError, FileNotFoundError):
            emg, fsamp, emg_mask = None, None, None

    if emg is None or fsamp is None or emg_mask is None:
        ctx = _get_edit_signal_context(edit_signal_token)
        if ctx is None:
            ctx = _get_edit_signal_context_by_label(file_label)
        if ctx is None:
            raise HTTPException(
                status_code=400,
                detail="No BIDS EMG available. Reload decomposition MAT and retry filter update.",
            )
        data = np.asarray(ctx.get("data"), dtype=float)
        if data.ndim == 1:
            data = data.reshape(1, -1)
        if data.ndim != 2:
            raise HTTPException(status_code=400, detail="Invalid cached EMG context")
        if data.shape[0] > data.shape[1]:
            data = data.T
        fsamp_val = float(ctx.get("fsamp") or 0.0)
        if fsamp_val <= 0:
            raise HTTPException(status_code=400, detail="Missing fsamp in MAT signal context")
        grid_names = ctx.get("grid_names") or ["Grid 1"]
        coordinates, _, _, _ = format_hdemg_signal(
            data.copy(),
            grid_names,
            fsamp_val,
        )
        if grid_index < 0 or grid_index >= len(coordinates):
            raise HTTPException(status_code=400, detail="grid_index out of range")
        ch_offset = 0
        for g in range(grid_index):
            ch_offset += int(coordinates[g].shape[0])
        n_ch = int(coordinates[grid_index].shape[0])
        emg = data[ch_offset : ch_offset + n_ch, :]
        fsamp = fsamp_val

        raw_masks = ctx.get("emgmask") or []
        cell = raw_masks[grid_index] if grid_index < len(raw_masks) else np.array([], dtype=int)
        cell_arr = np.asarray(cell, dtype=int).flatten()
        if cell_arr.size == n_ch and np.all(np.isin(cell_arr, [0, 1])):
            emg_mask = cell_arr.copy()
        else:
            emg_mask = np.zeros(n_ch, dtype=int)
            if cell_arr.size > 0:
                max_val = int(np.max(cell_arr))
                min_val = int(np.min(cell_arr))
                if min_val >= 1 and max_val <= n_ch:
                    idx = cell_arr[cell_arr >= 1] - 1
                    emg_mask[idx.astype(int)] = 1
                elif min_val >= 0 and max_val < n_ch:
                    idx = cell_arr[cell_arr >= 0]
                    emg_mask[idx.astype(int)] = 1
                elif cell_arr.size == n_ch:
                    emg_mask = np.asarray(cell_arr != 0, dtype=int)

    bids_emg_offset = view_start if bids_root and emg is not None else 0
    pt, updated = update_motor_unit_filter_window(
        emg,
        emg_mask,
        distimes[mu_index],
        fsamp,
        view_start,
        view_end,
        nbextchan=nbextchan,
        peeloff_spike_times=[
            distimes[i]
            for i in range(len(distimes))
            if i != mu_index and mu_grid_index[i] == grid_index
        ],
        peeloff_win=peeloff_win,
        emg_offset=bids_emg_offset,
        use_peeloff=use_peeloff,
    )

    pulse_train = payload.get("pulse_train")
    updated_pulse = None
    if pulse_train is not None:
        try:
            pulse_arr = np.array(pulse_train, dtype=float)
        except (TypeError, ValueError):
            pulse_arr = None
        if pulse_arr is not None and pt is not None:
            edge = int(round(0.1 * fsamp))
            seg_start = view_start + edge
            seg_end = min(view_start + len(pt) - edge, pulse_arr.shape[0])
            if seg_end > seg_start and len(pt) > 2 * edge:
                pulse_arr[seg_start:seg_end] = pt[edge : edge + (seg_end - seg_start)]
            updated_pulse = pulse_arr

    return make_json_safe(
        {
            "fsamp": fsamp,
            "distimes": updated,
            "pulse_train": (
                updated_pulse.tolist()
                if isinstance(updated_pulse, np.ndarray)
                else pulse_train
            ),
        }
    )


def add_spikes(payload: dict[str, Any]) -> dict[str, Any]:
    """Add spikes in ROI for selected motor unit."""
    pulse_train = payload.get("pulse_train")
    if pulse_train is None:
        raise HTTPException(status_code=400, detail="pulse_train is required")
    distimes = normalize_distimes(payload.get("distimes") or [])
    fsamp = as_float(payload.get("fsamp"), "fsamp", default=0.0)
    if fsamp <= 0:
        raise HTTPException(status_code=400, detail="fsamp is required")
    x_start = as_int(payload.get("x_start"), "x_start", default=0)
    x_end = as_int(payload.get("x_end"), "x_end", default=0)
    y_min = as_float(payload.get("y_min"), "y_min", default=0.0)
    mu_index = as_int(payload.get("mu_index"), "mu_index", default=0)
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")

    pulse = np.array(pulse_train, dtype=float)
    updated = add_spikes_in_roi(pulse, distimes[mu_index], fsamp, x_start, x_end, y_min)
    return make_json_safe({"distimes": updated})


def delete_spikes(payload: dict[str, Any]) -> dict[str, Any]:
    """Delete spikes in ROI for selected motor unit."""
    pulse_train = payload.get("pulse_train")
    if pulse_train is None:
        raise HTTPException(status_code=400, detail="pulse_train is required")
    distimes = normalize_distimes(payload.get("distimes") or [])
    x_start = as_int(payload.get("x_start"), "x_start", default=0)
    x_end = as_int(payload.get("x_end"), "x_end", default=0)
    y_min = as_float(payload.get("y_min"), "y_min", default=0.0)
    y_max = as_float(payload.get("y_max"), "y_max", default=0.0)
    mu_index = as_int(payload.get("mu_index"), "mu_index", default=0)
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")

    pulse = np.array(pulse_train, dtype=float)
    updated = delete_spikes_in_roi(
        pulse, distimes[mu_index], x_start, x_end, y_min, y_max
    )
    return make_json_safe({"distimes": updated})


def delete_dr(payload: dict[str, Any]) -> dict[str, Any]:
    """Delete spikes with high discharge rates inside ROI for selected MU."""
    pulse_train = payload.get("pulse_train")
    if pulse_train is None:
        raise HTTPException(status_code=400, detail="pulse_train is required")
    distimes = normalize_distimes(payload.get("distimes") or [])
    fsamp = as_float(payload.get("fsamp"), "fsamp", default=0.0)
    if fsamp <= 0:
        raise HTTPException(status_code=400, detail="fsamp is required")
    x_start = as_int(payload.get("x_start"), "x_start", default=0)
    x_end = as_int(payload.get("x_end"), "x_end", default=0)
    y_min = as_float(payload.get("y_min"), "y_min", default=0.0)
    mu_index = as_int(payload.get("mu_index"), "mu_index", default=0)
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")

    pulse = np.array(pulse_train, dtype=float)
    updated = delete_high_discharge_rate_spikes_in_roi(
        pulse, distimes[mu_index], fsamp, x_start, x_end, y_min
    )
    return make_json_safe({"distimes": updated})


def remove_outliers(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove discharge-rate outlier spikes and return removal count."""
    pulse_train = payload.get("pulse_train")
    if pulse_train is None:
        raise HTTPException(status_code=400, detail="pulse_train is required")
    distimes = normalize_distimes(payload.get("distimes") or [])
    fsamp = as_float(payload.get("fsamp"), "fsamp", default=0.0)
    if fsamp <= 0:
        raise HTTPException(status_code=400, detail="fsamp is required")
    mu_index = as_int(payload.get("mu_index"), "mu_index", default=0)
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")

    pulse = np.array(pulse_train, dtype=float)
    source = distimes[mu_index]
    updated = remove_discharge_rate_outliers(pulse, source, fsamp)
    removed = max(0, len(source) - len(updated))
    return make_json_safe({"distimes": updated, "removed_count": removed})


def remove_duplicates_service(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove duplicate motor units using lag-aware spike-train overlap."""
    distimes_raw = payload.get("distimes") or []
    distimes = normalize_distimes(distimes_raw)
    if not distimes:
        return make_json_safe({"kept_indices": [], "distimes": [], "pulse_trains": []})

    fsamp_raw = payload.get("fsamp")
    fsamp = float(fsamp_raw) if fsamp_raw is not None else None
    if not fsamp or fsamp <= 0:
        raise HTTPException(status_code=400, detail="fsamp is required for deduplication")

    total_samples = as_int(payload.get("total_samples"), "total_samples", default=0)
    parameters = payload.get("parameters") or {}
    mu_grid_index = _normalize_mu_grid_index(payload.get("mu_grid_index"), len(distimes))

    pulse_trains_raw = payload.get("pulse_trains")
    pulse_trains: np.ndarray | None = None
    if pulse_trains_raw is not None:
        try:
            pulse_trains = np.array(pulse_trains_raw, dtype=float)
        except (TypeError, ValueError):
            pulse_trains = None
    if (
        pulse_trains is None
        or pulse_trains.ndim != 2
        or pulse_trains.shape[0] != len(distimes)
    ):
        if total_samples <= 0:
            total_samples = max((max(d) for d in distimes if d), default=0) + 1
        pulse_trains = build_pulse_trains_from_distimes(distimes, total_samples)

    if len(distimes) <= 1:
        return make_json_safe({
            "kept_indices": list(range(len(distimes))),
            "distimes": distimes,
            "pulse_trains": pulse_trains.tolist(),
        })

    dup_tol = float(parameters.get("duplicatesthresh", 0.3))
    dedup_pulses, dedup_distimes, kept_idx = rem_duplicates(
        np.asarray(pulse_trains, dtype=float),
        [np.asarray(d, dtype=int) for d in distimes],
        [np.asarray(d, dtype=int) for d in distimes],
        round(fsamp / 40),
        0.00025,
        dup_tol,
        fsamp,
    )
    dedup_distimes_clean = [
        sorted({int(v) for v in np.asarray(d, dtype=int).tolist() if int(v) >= 0})
        for d in dedup_distimes
    ]
    return make_json_safe({
        "kept_indices": kept_idx,
        "distimes": dedup_distimes_clean,
        "pulse_trains": dedup_pulses.tolist() if dedup_pulses.size else [],
        "removed_count": len(distimes) - len(kept_idx),
    })


def flag_mu(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate MU index and return flag status without mutating spike times."""
    distimes = normalize_distimes(payload.get("distimes") or [])
    mu_index = as_int(payload.get("mu_index"), "mu_index", default=0)
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")
    return make_json_safe({"flagged": True})
