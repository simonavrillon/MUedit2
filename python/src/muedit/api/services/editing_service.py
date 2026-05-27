from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException, UploadFile
from fastapi.responses import Response

from muedit.api.cache import (
    _get_edit_signal_context,
    _get_edit_signal_context_by_label,
    _store_edit_signal_context,
)
from muedit.api.common import (
    _pack_json_f32_payload,
    as_float,
    as_int,
    make_json_safe,
    parse_entity_label,
    safe_unlink,
    save_upload_to_temp,
)
from muedit.api.services.bids_helpers import (
    _infer_bids_root_from_decomp_path,
    _load_bids_grid,
    _parse_subject_session_from_entity_label,
    _read_bids_channels_sidecar,
)
from muedit.api.services.edit_helpers import (
    _expected_grid_count,
    _generate_mu_uids,
    _normalize_flagged,
    _normalize_mu_grid_index,
    _normalize_muscle_names,
    _pad_grid_names,
)
from muedit.decomp.algorithm import DEDUP_JITTER, DEDUP_MAXLAG_RATIO, rem_duplicates
from muedit.decomp.io import (
    build_pulse_trains_from_distimes,
    load_decomposition_file,
    load_decomposition_signal_context,
    normalize_distimes,
)
from muedit.decomp.postprocess import _save_npz_with_app_schema
from muedit.editing.operations import (
    add_artifact_in_roi,
    add_spikes_in_roi,
    delete_high_discharge_rate_spikes_in_roi,
    delete_spikes_in_roi,
    remove_discharge_rate_outliers,
    update_motor_unit_filter_window,
)
from muedit.signal.grid import format_hdemg_signal


def _save_editlog(
    editlog_path: Path,
    mu_uids: list[str],
    edit_history: list[dict[str, Any]],
    artifact_times: list[list[int]] | None = None,
) -> None:
    payload: dict[str, Any] = {"mu_uids": mu_uids, "history": edit_history}
    if artifact_times:
        payload["artifact_times"] = artifact_times
    with editlog_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _init_loaded_decomp(filepath: str, file_label: str) -> dict[str, Any]:
    loaded = load_decomposition_file(filepath)
    signal_ctx = load_decomposition_signal_context(filepath)
    if signal_ctx:
        loaded["edit_signal_token"] = _store_edit_signal_context(signal_ctx, file_label)
    loaded["file_label"] = file_label
    return loaded


async def load_decomposition(file: UploadFile) -> dict[str, Any]:
    tmp_path = await save_upload_to_temp(file)
    try:
        return make_json_safe(_init_loaded_decomp(tmp_path, file.filename))
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
    return _pack_json_f32_payload(b"MELD", metadata, pulse)


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
    file_label = Path(filepath).name
    loaded = _init_loaded_decomp(filepath, file_label)

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
            if isinstance(editlog.get("artifact_times"), list):
                loaded["artifact_times"] = editlog["artifact_times"]
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


def _dedup(
    pulse_trains: np.ndarray,
    distimes: list,
    dup_tol: float,
    fsamp: float,
) -> tuple[np.ndarray, list, list[int]]:
    return rem_duplicates(
        np.asarray(pulse_trains, dtype=float),
        [np.asarray(d, dtype=int) for d in distimes],
        [np.asarray(d, dtype=int) for d in distimes],
        round(fsamp / DEDUP_MAXLAG_RATIO),
        DEDUP_JITTER,
        dup_tol,
        fsamp,
    )



def save_edits(payload: dict[str, Any]) -> dict[str, Any]:
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
    mu_grid_index = _normalize_mu_grid_index(payload.get("mu_grid_index"), len(distimes))
    expected_grid_count = (max(mu_grid_index) + 1) if mu_grid_index else 1
    grid_names = _pad_grid_names(
        payload.get("grid_names") or [], expected_grid_count, []
    )
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
    artifact_times_raw = payload.get("artifact_times") or []
    artifact_times_all: list[list[int]] = [
        [int(x) for x in row if isinstance(x, (int, float))]
        for row in artifact_times_raw
        if isinstance(row, (list, tuple))
    ]

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
        dedup_pulses, dedup_distimes, kept_idx = _dedup(pulse_trains, distimes, dup_tol, fsamp)
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
    _save_npz_with_app_schema(
        out_path,
        pulse_trains=pulse_trains,
        distimes=distimes,
        fsamp=fsamp,
        grid_names=grid_names,
        mu_grid_index=mu_grid_index,
        muscles=muscle_names,
        parameters=parameters,
        total_samples=total_samples,
    )
    _save_editlog(out_path.with_suffix(".json"), mu_uids, edit_history, artifact_times_all or None)
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
    flagged_raw = payload.get("flagged") or []
    flagged = [bool(f) for f in flagged_raw] if flagged_raw else []

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
        coordinates, _, _, _ = format_hdemg_signal(grid_names)
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

    artifact_times_raw = payload.get("artifact_times") or []
    artifact_times = [int(x) for x in artifact_times_raw if isinstance(x, (int, float))]

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
            if i != mu_index
            and mu_grid_index[i] == grid_index
            and not (i < len(flagged) and flagged[i])
        ],
        peeloff_win=peeloff_win,
        emg_offset=bids_emg_offset,
        use_peeloff=use_peeloff,
        artifact_times=artifact_times or None,
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


def add_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    """Mark a peak in the ROI as an artifact for the selected motor unit."""
    pulse_train = payload.get("pulse_train")
    if pulse_train is None:
        raise HTTPException(status_code=400, detail="pulse_train is required")
    fsamp = as_float(payload.get("fsamp"), "fsamp", default=0.0)
    if fsamp <= 0:
        raise HTTPException(status_code=400, detail="fsamp is required")
    x_start = as_int(payload.get("x_start"), "x_start", default=0)
    x_end = as_int(payload.get("x_end"), "x_end", default=0)
    y_min = as_float(payload.get("y_min"), "y_min", default=0.0)

    artifact_times_raw = payload.get("artifact_times") or []
    artifact_times = [int(x) for x in artifact_times_raw if isinstance(x, (int, float))]

    pulse = np.array(pulse_train, dtype=float)
    updated = add_artifact_in_roi(pulse, artifact_times, fsamp, x_start, x_end, y_min)
    return make_json_safe({"artifact_times": updated})


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
    dedup_pulses, dedup_distimes, kept_idx = _dedup(pulse_trains, distimes, dup_tol, fsamp)
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
