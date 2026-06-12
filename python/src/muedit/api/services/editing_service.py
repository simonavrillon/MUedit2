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
    make_json_safe,
    parse_entity_label,
    safe_unlink,
    save_upload_to_temp,
)
from muedit.api.config import DATA_ROOT, resolve_bids_root
from muedit.api.schemas import (
    EditDeduplicatePayload,
    EditFilterPayload,
    EditFlagPayload,
    EditOutliersPayload,
    EditRoiPayload,
    EditSavePayload,
)
from muedit.api.services.bids_helpers import (
    _infer_bids_root_from_decomp_path,
    _load_bids_grid,
    _parse_all_bids_entities,
    _parse_subject_session_from_entity_label,
    _read_bids_channels_sidecar,
    read_bids_sidecar_meta,
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
    delete_artifacts_in_roi,
    delete_high_discharge_rate_spikes_in_roi,
    delete_spikes_in_roi,
    remove_discharge_rate_outliers,
    update_motor_unit_filter_window,
)
from muedit.io.bids import (
    export_bids_emg,
    export_bids_mu_derivatives,
    write_bids_dataset_description,
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
        try:
            rel = bids_root.relative_to(DATA_ROOT)
            loaded["project"] = rel.parts[0] if rel.parts else ""
        except ValueError:
            loaded["project"] = ""
        try:
            entity_label = parse_entity_label(file_label)
            subject, session = _parse_subject_session_from_entity_label(entity_label)
            emg_dir = bids_root / f"sub-{subject}"
            if session:
                emg_dir = emg_dir / f"ses-{session}"
            emg_dir = emg_dir / "emg"
            channels_path = emg_dir / f"{entity_label}_channels.tsv"
            if not channels_path.exists():
                channels_path = emg_dir / f"{entity_label}_emg_channels.tsv"  # backward compat
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

            # Enrich with participant + hardware metadata from BIDS sidecars.
            loaded.update(read_bids_sidecar_meta(bids_root, entity_label))

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


def _export_bids_from_mat_context(
    bids_root: Path,
    entity_label: str,
    edit_signal_token: str | None,
    file_label: str | None,
    fsamp: float | None,
    grid_names: list[str],
    muscle_names: list[str],
    parameters: dict[str, Any],
    powerline_freq: float | None = None,
    manufacturer: str | None = None,
    manufacturers_model_name: str | None = None,
    placement_scheme: str | None = None,
    placement_scheme_description: str | None = None,
    task_description: str | None = None,
    software_versions: str | None = None,
) -> dict[str, str] | None:
    """Best-effort BIDS EMG export using the raw signal cached from a .mat load."""
    ctx = _get_edit_signal_context(edit_signal_token) or _get_edit_signal_context_by_label(file_label)
    if ctx is None:
        return None  # non-MAT source or context expired

    entities = _parse_all_bids_entities(entity_label)
    try:
        aux_data = ctx.get("aux_data")
        aux_names = ctx.get("aux_names") or None
        paths = export_bids_emg(
            data=ctx["data"],
            fsamp=float(fsamp or ctx["fsamp"]),
            grid_names=ctx["grid_names"] or grid_names,
            coordinates=ctx.get("coordinates") or [],
            discard_channels=ctx["emgmask"],
            bids_root=bids_root,
            ied=ctx.get("ied"),
            subject=entities["sub"] or "01",
            task=entities["task"] or "task",
            run=entities["run"],
            session=entities["ses"],
            acquisition=entities["acq"],
            recording=entities["recording"],
            target_muscle=muscle_names if len(muscle_names) > 1 else (muscle_names[0] if muscle_names else None),
            aux_data=aux_data if isinstance(aux_data, np.ndarray) and aux_data.size > 0 else None,
            aux_names=aux_names if aux_names else None,
            # User-editable fields take priority; fall back to loader ctx, then hardcoded default
            manufacturer=manufacturer or ctx.get("manufacturer"),
            manufacturers_model_name=manufacturers_model_name or ctx.get("device_name"),
            powerline_freq=powerline_freq or ctx.get("powerline_freq") or 50.0,
            placement_scheme=placement_scheme,
            placement_scheme_description=placement_scheme_description,
            task_description=task_description,
            software_versions=software_versions,
            # Loader-only fields — taken directly from ctx
            units=ctx.get("units") or "uV",
            hardware_filters=ctx.get("hardware_filters"),
            gain=ctx.get("gains"),
            low_cutoff=ctx.get("emg_hpf"),
            high_cutoff=ctx.get("emg_lpf"),
            recording_type=ctx.get("recording_type") or "continuous",
            software_filters=ctx.get("software_filters"),
        )
        return {k: str(v) for k, v in paths.items()}
    except Exception:  # noqa: BLE001
        return None  # never block the primary save on BIDS export error


def save_edits(payload: EditSavePayload) -> dict[str, Any]:
    distimes = normalize_distimes(payload.distimes or payload.discharge_times or [])
    pulse_trains_raw = payload.pulse_trains
    total_samples = payload.total_samples
    if total_samples <= 0:
        raise HTTPException(
            status_code=400, detail="total_samples is required to save edits"
        )

    fsamp = payload.fsamp
    mu_grid_index = _normalize_mu_grid_index(payload.mu_grid_index, len(distimes))
    expected_grid_count = (max(mu_grid_index) + 1) if mu_grid_index else 1
    grid_names = _pad_grid_names(payload.grid_names or [], expected_grid_count, [])
    parameters = payload.parameters or {}
    muscle_names = _normalize_muscle_names(payload.muscle_names or payload.muscle)

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

    mu_uids_raw = payload.mu_uids
    mu_uids: list[str] = (
        list(mu_uids_raw)
        if isinstance(mu_uids_raw, (list, tuple)) and len(mu_uids_raw) == len(distimes)
        else _generate_mu_uids(mu_grid_index)
    )
    edit_history: list[dict[str, Any]] = list(payload.edit_history or [])
    artifact_times_raw = payload.artifact_times or []
    artifact_times_all: list[list[int]] = [
        [int(x) for x in row if isinstance(x, (int, float))]
        for row in artifact_times_raw
        if isinstance(row, (list, tuple))
    ]

    # Schema declares these as bool | None; default to True when unspecified.
    remove_flagged = True if payload.remove_flagged is None else payload.remove_flagged
    remove_duplicates = True if payload.remove_duplicates is None else payload.remove_duplicates

    if remove_flagged and distimes:
        flagged = _normalize_flagged(payload.flagged, len(distimes))
        keep_idx = [
            i
            for i, spikes in enumerate(distimes)
            if not flagged[i]
        ]
        distimes = [distimes[i] for i in keep_idx]
        mu_grid_index = [mu_grid_index[i] for i in keep_idx]
        mu_uids = [mu_uids[i] for i in keep_idx]
        pulse_trains = pulse_trains[keep_idx, :] if pulse_trains.size else pulse_trains
        artifact_times_all = [artifact_times_all[i] for i in keep_idx if i < len(artifact_times_all)]

    if remove_duplicates and len(distimes) > 1 and fsamp and fsamp > 0:
        dup_tol_raw = parameters.get("duplicatesthresh", 0.3)
        while isinstance(dup_tol_raw, (list, tuple, np.ndarray)):
            dup_tol_raw = dup_tol_raw[0] if len(dup_tol_raw) > 0 else 0.3
        dup_tol = float(dup_tol_raw)
        dedup_pulses, dedup_distimes, kept_idx = _dedup(pulse_trains, distimes, dup_tol, fsamp)
        pulse_trains = dedup_pulses if dedup_pulses.size else np.zeros((0, total_samples))
        distimes = [
            sorted({int(v) for v in np.asarray(d, dtype=int).tolist() if int(v) >= 0})
            for d in dedup_distimes
        ]
        mu_grid_index = [mu_grid_index[i] for i in kept_idx]
        mu_uids = [mu_uids[i] for i in kept_idx]
        artifact_times_all = [artifact_times_all[i] for i in kept_idx if i < len(artifact_times_all)]

    bids_root = resolve_bids_root(payload.project)
    file_label = payload.file_label or ""
    entity_label = payload.entity_label or parse_entity_label(file_label)
    subject, session = _parse_subject_session_from_entity_label(entity_label)
    decomp_dir = bids_root / "derivatives" / "muedit" / f"sub-{subject}"
    if session:
        decomp_dir = decomp_dir / f"ses-{session}"
    decomp_dir = decomp_dir / "decomp"
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

    participant_meta = payload.participant_meta or {}
    try:
        write_bids_dataset_description(
            bids_root,
            subject=subject,
            age=int(participant_meta["age"]) if participant_meta.get("age") not in (None, "", "n/a") else None,
            sex=participant_meta.get("sex") or None,
            handedness=participant_meta.get("handedness") or None,
        )
    except Exception:  # noqa: BLE001
        pass  # dataset-level files are best-effort; never block the primary save

    deriv_paths: dict[str, str] | None = None
    if distimes and fsamp and fsamp > 0:
        try:
            deriv_result = export_bids_mu_derivatives(
                distimes=distimes,
                fsamp=fsamp,
                bids_root=bids_root,
                entities=entity_label,
                mu_uids=mu_uids,
            )
            deriv_paths = {k: str(v) for k, v in deriv_result.items()}
        except Exception:  # noqa: BLE001
            pass  # derivatives export is best-effort; never block the primary save

    bids_paths = _export_bids_from_mat_context(
        bids_root=bids_root,  # already a Path
        entity_label=entity_label,
        edit_signal_token=payload.edit_signal_token,
        file_label=file_label,
        fsamp=fsamp,
        grid_names=grid_names,
        muscle_names=muscle_names,
        parameters=parameters,
        powerline_freq=float(payload.powerline_freq) if payload.powerline_freq else None,
        manufacturer=payload.manufacturer or None,
        manufacturers_model_name=payload.manufacturers_model_name or None,
        placement_scheme=payload.placement_scheme or None,
        placement_scheme_description=payload.placement_scheme_description or None,
        task_description=payload.task_description or None,
        software_versions=payload.software_versions or None,
    )
    result: dict[str, Any] = {"saved": True, "path": str(out_path)}
    if bids_paths:
        result["bids_emg_paths"] = bids_paths
    if deriv_paths:
        result["bids_deriv_paths"] = deriv_paths
    return make_json_safe(result)


def update_filter(payload: EditFilterPayload) -> dict[str, Any]:
    bids_root = str(resolve_bids_root(payload.project))
    edit_signal_token = payload.edit_signal_token
    file_label = payload.file_label or ""
    entity_label = payload.entity_label or parse_entity_label(file_label)
    grid_index = payload.grid_index
    distimes = normalize_distimes(payload.distimes or [])
    if not distimes:
        raise HTTPException(
            status_code=400, detail="distimes are required for filter update"
        )

    mu_index = payload.mu_index
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")
    mu_grid_index = _normalize_mu_grid_index(payload.mu_grid_index, len(distimes))
    peeloff_win = payload.peel_off_win
    if peeloff_win <= 0:
        peeloff_win = 0.025
    use_peeloff = payload.use_peeloff
    flagged_raw = payload.flagged or []
    flagged = [bool(f) for f in flagged_raw] if flagged_raw else []

    view_start = payload.view_start
    view_end = payload.view_end
    if view_end <= view_start:
        raise HTTPException(status_code=400, detail="view_start/view_end are required")
    nbextchan = payload.nbextchan

    emg: np.ndarray | None = None
    fsamp: float | None = None
    emg_mask: np.ndarray | None = None
    emg_is_presliced = False  # True only when BIDS loaded a view-length slice

    if bids_root:
        try:
            emg, fsamp, emg_mask = _load_bids_grid(
                Path(bids_root), str(entity_label), grid_index, view_start, view_end
            )
            emg_is_presliced = True
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

    artifact_times_raw = payload.artifact_times or []
    artifact_times = [int(x) for x in artifact_times_raw if isinstance(x, (int, float))]

    bids_emg_offset = view_start if emg_is_presliced else 0
    lock_spikes = payload.lock_spikes
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
        lock_spikes=lock_spikes,
    )

    pulse_train = payload.pulse_train
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


def add_spikes(payload: EditRoiPayload) -> dict[str, Any]:
    """Add spikes in ROI for selected motor unit."""
    pulse_train = payload.pulse_train
    if pulse_train is None:
        raise HTTPException(status_code=400, detail="pulse_train is required")
    distimes = normalize_distimes(payload.distimes or [])
    fsamp = payload.fsamp or 0.0
    if fsamp <= 0:
        raise HTTPException(status_code=400, detail="fsamp is required")
    x_start = payload.x_start
    x_end = payload.x_end
    y_min = payload.y_min or 0.0
    mu_index = payload.mu_index
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")

    pulse = np.array(pulse_train, dtype=float)
    updated = add_spikes_in_roi(pulse, distimes[mu_index], fsamp, x_start, x_end, y_min)
    return make_json_safe({"distimes": updated})


def add_artifact(payload: EditRoiPayload) -> dict[str, Any]:
    """Mark a peak in the ROI as an artifact for the selected motor unit."""
    pulse_train = payload.pulse_train
    if pulse_train is None:
        raise HTTPException(status_code=400, detail="pulse_train is required")
    fsamp = payload.fsamp or 0.0
    if fsamp <= 0:
        raise HTTPException(status_code=400, detail="fsamp is required")
    x_start = payload.x_start
    x_end = payload.x_end
    y_min = payload.y_min or 0.0

    artifact_times_raw = payload.artifact_times or []
    artifact_times = [int(x) for x in artifact_times_raw if isinstance(x, (int, float))]

    pulse = np.array(pulse_train, dtype=float)
    updated = add_artifact_in_roi(pulse, artifact_times, fsamp, x_start, x_end, y_min)
    return make_json_safe({"artifact_times": updated})


def delete_spikes(payload: EditRoiPayload) -> dict[str, Any]:
    """Delete spikes and artifacts in ROI for selected motor unit."""
    pulse_train = payload.pulse_train
    if pulse_train is None:
        raise HTTPException(status_code=400, detail="pulse_train is required")
    distimes = normalize_distimes(payload.distimes or [])
    x_start = payload.x_start
    x_end = payload.x_end
    y_min = payload.y_min or 0.0
    y_max = payload.y_max or 0.0
    mu_index = payload.mu_index
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")

    pulse = np.array(pulse_train, dtype=float)
    updated_distimes = delete_spikes_in_roi(
        pulse, distimes[mu_index], x_start, x_end, y_min, y_max
    )

    # Also delete artifacts in the same ROI
    updated_artifact_times = None
    artifact_times_raw = payload.artifact_times
    if artifact_times_raw:
        artifact_times = [int(x) for x in artifact_times_raw if isinstance(x, (int, float))]
        if artifact_times:
            updated_artifact_times = delete_artifacts_in_roi(
                pulse, artifact_times, x_start, x_end, y_min, y_max
            )

    result = {"distimes": updated_distimes}
    if updated_artifact_times is not None:
        result["artifact_times"] = updated_artifact_times
    return make_json_safe(result)


def delete_dr(payload: EditRoiPayload) -> dict[str, Any]:
    """Delete spikes with high discharge rates inside ROI for selected MU."""
    pulse_train = payload.pulse_train
    if pulse_train is None:
        raise HTTPException(status_code=400, detail="pulse_train is required")
    distimes = normalize_distimes(payload.distimes or [])
    fsamp = payload.fsamp or 0.0
    if fsamp <= 0:
        raise HTTPException(status_code=400, detail="fsamp is required")
    x_start = payload.x_start
    x_end = payload.x_end
    y_min = payload.y_min or 0.0
    mu_index = payload.mu_index
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")

    pulse = np.array(pulse_train, dtype=float)
    updated = delete_high_discharge_rate_spikes_in_roi(
        pulse, distimes[mu_index], fsamp, x_start, x_end, y_min
    )
    return make_json_safe({"distimes": updated})


def remove_outliers(payload: EditOutliersPayload) -> dict[str, Any]:
    """Remove discharge-rate outlier spikes and return removal count."""
    pulse_train = payload.pulse_train
    if pulse_train is None:
        raise HTTPException(status_code=400, detail="pulse_train is required")
    distimes = normalize_distimes(payload.distimes or [])
    fsamp = payload.fsamp or 0.0
    if fsamp <= 0:
        raise HTTPException(status_code=400, detail="fsamp is required")
    mu_index = payload.mu_index
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")

    pulse = np.array(pulse_train, dtype=float)
    source = distimes[mu_index]
    updated = remove_discharge_rate_outliers(pulse, source, fsamp)
    removed = max(0, len(source) - len(updated))
    return make_json_safe({"distimes": updated, "removed_count": removed})


def remove_duplicates_service(payload: EditDeduplicatePayload) -> dict[str, Any]:
    """Remove duplicate motor units using lag-aware spike-train overlap."""
    distimes = normalize_distimes(payload.distimes or [])
    if not distimes:
        return make_json_safe({"kept_indices": [], "distimes": []})

    fsamp = payload.fsamp
    if not fsamp or fsamp <= 0:
        raise HTTPException(status_code=400, detail="fsamp is required for deduplication")

    total_samples = payload.total_samples
    if total_samples <= 0:
        total_samples = max((max(d) for d in distimes if d), default=0) + 1
    parameters = payload.parameters or {}

    pulse_trains = build_pulse_trains_from_distimes(distimes, total_samples)

    if len(distimes) <= 1:
        return make_json_safe({
            "kept_indices": list(range(len(distimes))),
            "distimes": distimes,
        })

    dup_tol = float(parameters.get("duplicatesthresh", 0.3))
    _, dedup_distimes, kept_idx = _dedup(pulse_trains, distimes, dup_tol, fsamp)
    dedup_distimes_clean = [
        sorted({int(v) for v in np.asarray(d, dtype=int).tolist() if int(v) >= 0})
        for d in dedup_distimes
    ]
    return make_json_safe({
        "kept_indices": kept_idx,
        "distimes": dedup_distimes_clean,
        "removed_count": len(distimes) - len(kept_idx),
    })


def flag_mu(payload: EditFlagPayload) -> dict[str, Any]:
    """Validate MU index and return flag status without mutating spike times."""
    distimes = normalize_distimes(payload.distimes or [])
    mu_index = payload.mu_index
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")
    return make_json_safe({"flagged": True})
