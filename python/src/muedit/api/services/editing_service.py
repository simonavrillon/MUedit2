from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException
from fastapi.responses import Response

from muedit.api.binary import pack_json_f32_payload
from muedit.api.cache import (
    _get_edit_signal_context,
    _get_edit_signal_context_by_label,
    _store_edit_signal_context,
)
from muedit.api.common import (
    make_json_safe,
    parse_entity_label,
    require_existing_path,
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
    _coerce_bool_param,
    _coerce_dup_tol,
    _expected_grid_count,
    _generate_mu_uids,
    _normalize_flagged,
    _normalize_mu_grid_index,
    _normalize_muscle_names,
    _pad_grid_names,
)
from muedit.decomp.decomposition_file import (
    build_pulse_trains_from_distimes,
    load_decomposition_file,
    load_decomposition_signal_context,
    normalize_distimes,
    save_decomposition_npz,
    save_editlog,
)
from muedit.decomp.postprocess import remove_duplicates_by_grid
from muedit.decomp.preprocess import build_manual_artifact_mask
from muedit.decomp.types import DEFAULT_PEEL_OFF_WIN_SEC, DecompositionParameters
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
from muedit.models import LoadedDecomposition
from muedit.signal.grid import format_hdemg_signal

logger = logging.getLogger(__name__)


@dataclass
class EditLoadResult:
    """What ``/edit/load-by-path`` returns: a decomposition plus its edit session."""

    decomposition: LoadedDecomposition
    file_label: str
    edit_signal_token: str | None = None  # set when the file embeds the raw EMG
    project: str | None = None  # BIDS project folder, when the file sits in one
    # Restored from the ``.json`` edit log saved next to the decomposition.
    mu_uids: list[Any] | None = None
    edit_history: list[Any] | None = None
    artifact_times: list[Any] | None = None
    # Participant and hardware fields from the BIDS sidecars.
    sidecar_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON payload: the decomposition fields plus the session fields that are set."""
        out = self.decomposition.to_dict()
        out["file_label"] = self.file_label
        session = {
            "edit_signal_token": self.edit_signal_token,
            "project": self.project,
            "mu_uids": self.mu_uids,
            "edit_history": self.edit_history,
            "artifact_times": self.artifact_times,
        }
        out.update({key: value for key, value in session.items() if value is not None})
        out.update(self.sidecar_meta)
        return out


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
    return pack_json_f32_payload(b"MELD", metadata, pulse)


def _wrap_edit_load_binary(loaded: dict[str, Any]) -> Response | dict[str, Any]:
    """Encode a loaded decomposition as f32 binary, falling back to JSON when not encodable."""
    blob = _encode_edit_load_f32(loaded)
    if blob is None:
        return loaded
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"x-muedit-format": "edit-load-f32-v1"},
    )


def load_decomposition_from_path(filepath: str) -> dict[str, Any]:
    if require_existing_path(filepath).suffix.lower() not in {".npz", ".mat"}:
        raise HTTPException(
            status_code=400,
            detail={
                "field": "path",
                "reason": "Unsupported decomposition format. Expected .mat or .npz",
            },
        )
    file_label = Path(filepath).name
    decomp = load_decomposition_file(filepath)
    result = EditLoadResult(decomposition=decomp, file_label=file_label)
    signal_ctx = load_decomposition_signal_context(filepath)
    if signal_ctx:
        result.edit_signal_token = _store_edit_signal_context(signal_ctx, file_label)

    bids_root = _infer_bids_root_from_decomp_path(filepath)
    if bids_root is not None:
        try:
            rel = bids_root.relative_to(DATA_ROOT)
            result.project = rel.parts[0] if rel.parts else ""
        except ValueError:
            result.project = ""
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
                expected_count = _expected_grid_count(decomp)
                if grid_names:
                    decomp.grid_names = _pad_grid_names(
                        grid_names, expected_count, decomp.grid_names
                    )
                if muscles:
                    decomp.muscle = muscles
                if fsamp and fsamp > 0:
                    decomp.fsamp = fsamp

            result.sidecar_meta = read_bids_sidecar_meta(bids_root, entity_label)

        except (ValueError, OSError, csv.Error, KeyError):
            pass  # best-effort; I/O and parse errors are non-fatal

    editlog_path = Path(filepath).with_suffix(".json")
    if editlog_path.exists():
        try:
            with editlog_path.open("r", encoding="utf-8") as fh:
                editlog = json.load(fh)
            if isinstance(editlog.get("mu_uids"), list):
                result.mu_uids = editlog["mu_uids"]
            if isinstance(editlog.get("history"), list):
                result.edit_history = editlog["history"]
            if isinstance(editlog.get("artifact_times"), list):
                result.artifact_times = editlog["artifact_times"]
        except (OSError, ValueError, KeyError):
            pass  # best-effort; missing or corrupt editlog is non-fatal

    return make_json_safe(result.to_dict())


def load_decomposition_binary_from_path(filepath: str) -> Response | dict[str, Any]:
    loaded = load_decomposition_from_path(filepath)
    return _wrap_edit_load_binary(loaded)


def _dedup(
    distimes: list[list[int]],
    mu_grid_index: list[int],
    parameters: dict[str, Any],
    fsamp: float,
    total_samples: int,
) -> list[int]:
    """Return the indices of the MUs the decomposition's duplicate removal keeps, ascending."""
    params = DecompositionParameters(
        duplicatesthresh=_coerce_dup_tol(parameters.get("duplicatesthresh", 0.3)),
        duplicatesbgrids=_coerce_bool_param(parameters.get("duplicatesbgrids", True)),
    )
    _, _, _, kept = remove_duplicates_by_grid(
        build_pulse_trains_from_distimes(distimes, total_samples),
        [np.asarray(d, dtype=int) for d in distimes],
        mu_grid_index,
        max(mu_grid_index, default=0) + 1,
        params,
        fsamp,
    )
    return sorted(kept)


def _clean_distimes(spikes: list[int]) -> list[int]:
    return sorted({int(v) for v in spikes if int(v) >= 0})


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
    ctx = _get_edit_signal_context(edit_signal_token) or _get_edit_signal_context_by_label(
        file_label
    )
    if ctx is None:
        return None  # non-MAT source or context expired
    if ctx.data.size == 0:
        return None  # mask-only context has no raw EMG to export

    entities = _parse_all_bids_entities(entity_label)
    try:
        meta = ctx.loader_meta
        paths = export_bids_emg(
            data=ctx.data,
            fsamp=float(fsamp or ctx.fsamp),
            grid_names=ctx.grid_names or grid_names,
            coordinates=ctx.coordinates,
            discard_channels=ctx.emgmask,
            bids_root=bids_root,
            ied=ctx.ied,
            subject=entities["sub"] or "01",
            task=entities["task"] or "task",
            run=entities["run"],
            session=entities["ses"],
            acquisition=entities["acq"],
            recording=entities["recording"],
            target_muscle=muscle_names
            if len(muscle_names) > 1
            else (muscle_names[0] if muscle_names else None),
            aux_data=ctx.aux_data,
            aux_names=ctx.aux_names or None,
            # User-editable fields take priority; fall back to loader ctx, then hardcoded default
            manufacturer=manufacturer or meta.get("manufacturer"),
            manufacturers_model_name=manufacturers_model_name or meta.get("device_name"),
            powerline_freq=powerline_freq or meta.get("powerline_freq") or 50.0,
            placement_scheme=placement_scheme or "ChannelSpecific",
            placement_scheme_description=placement_scheme_description,
            task_description=task_description,
            software_versions=software_versions,
            # Loader-only fields — taken directly from ctx
            units=meta.get("units") or "uV",
            hardware_filters=meta.get("hardware_filters"),
            gain=meta.get("gains"),
            low_cutoff=meta.get("emg_hpf"),
            high_cutoff=meta.get("emg_lpf"),
            aux_gain=meta.get("aux_gains"),
            aux_low_cutoff=meta.get("aux_hpf"),
            aux_high_cutoff=meta.get("aux_lpf"),
            aux_units=meta.get("aux_units"),
            recording_type=meta.get("recording_type") or "continuous",
            software_filters=meta.get("software_filters"),
        )
        return {k: str(v) for k, v in paths.items()}
    except Exception:  # noqa: BLE001
        return None  # never block the primary save on BIDS export error


def _save_removal_entry(entry_type: str, removed_uids: list[str]) -> dict[str, Any]:
    """Build the editlog entry for MUs dropped while saving."""
    return {
        "type": entry_type,
        "on_save": True,
        "removed_count": len(removed_uids),
        "removed_mu_uids": removed_uids,
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }


def save_edits(payload: EditSavePayload) -> dict[str, Any]:
    distimes = normalize_distimes(payload.distimes or payload.discharge_times or [])
    pulse_trains_raw = payload.pulse_trains
    total_samples = payload.total_samples
    if total_samples <= 0:
        raise HTTPException(status_code=400, detail="total_samples is required to save edits")

    fsamp = payload.fsamp
    if fsamp is None:
        raise HTTPException(status_code=400, detail="fsamp is required to save edits")
    mu_grid_index = _normalize_mu_grid_index(payload.mu_grid_index, len(distimes))
    expected_grid_count = (max(mu_grid_index) + 1) if mu_grid_index else 1
    grid_names = _pad_grid_names(payload.grid_names or [], expected_grid_count, [])
    parameters = payload.parameters or {}
    muscle_names = _normalize_muscle_names(payload.muscle or payload.muscle_names)

    if muscle_names and not parameters.get("target_muscle"):
        parameters["target_muscle"] = muscle_names if len(muscle_names) > 1 else muscle_names[0]

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
    # Pad to len(distimes) so keep_idx/kept_idx index safely.
    artifact_times_all: list[list[int]] = [list(row) for row in artifact_times_raw]
    if len(artifact_times_all) < len(distimes):
        artifact_times_all.extend([[] for _ in range(len(distimes) - len(artifact_times_all))])

    # Schema declares these as bool | None; default to True when unspecified.
    remove_flagged = True if payload.remove_flagged is None else payload.remove_flagged
    remove_duplicates = True if payload.remove_duplicates is None else payload.remove_duplicates

    # Indices into the payload's MUs that survive the save, in saved order.
    kept_mus = list(range(len(distimes)))

    if remove_flagged and distimes:
        flagged = _normalize_flagged(payload.flagged, len(distimes))
        keep_idx = [i for i, spikes in enumerate(distimes) if not flagged[i]]
        removed_uids = [mu_uids[i] for i in range(len(distimes)) if flagged[i]]
        distimes = [distimes[i] for i in keep_idx]
        mu_grid_index = [mu_grid_index[i] for i in keep_idx]
        mu_uids = [mu_uids[i] for i in keep_idx]
        pulse_trains = pulse_trains[keep_idx, :] if pulse_trains.size else pulse_trains
        artifact_times_all = [artifact_times_all[i] for i in keep_idx]
        kept_mus = [kept_mus[i] for i in keep_idx]
        if removed_uids:
            edit_history.append(_save_removal_entry("remove_flagged", removed_uids))

    if remove_duplicates and len(distimes) > 1 and fsamp and fsamp > 0:
        kept_idx = _dedup(distimes, mu_grid_index, parameters, fsamp, total_samples)
        kept_set = set(kept_idx)
        removed_uids = [uid for i, uid in enumerate(mu_uids) if i not in kept_set]
        pulse_trains = pulse_trains[kept_idx, :] if kept_idx else np.zeros((0, total_samples))
        distimes = [_clean_distimes(distimes[i]) for i in kept_idx]
        mu_grid_index = [mu_grid_index[i] for i in kept_idx]
        mu_uids = [mu_uids[i] for i in kept_idx]
        artifact_times_all = [artifact_times_all[i] for i in kept_idx]
        kept_mus = [kept_mus[i] for i in kept_idx]
        if removed_uids:
            edit_history.append(_save_removal_entry("remove_duplicates", removed_uids))

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

    regions: list[tuple[int, int]] = []
    for row in payload.artifact_regions or []:
        pair: tuple[Any, Any] | None = None
        if isinstance(row, (list, tuple)) and len(row) == 2:
            pair = (row[0], row[1])
        elif isinstance(row, dict) and "start" in row and "end" in row:
            pair = (row["start"], row["end"])
        if pair is None:
            continue
        try:
            regions.append((int(pair[0]), int(pair[1])))
        except (TypeError, ValueError):
            continue
    artifact_mask = build_manual_artifact_mask(regions, total_samples)
    if artifact_mask is None:
        ctx_for_mask = _get_edit_signal_context(
            payload.edit_signal_token
        ) or _get_edit_signal_context_by_label(file_label)
        if ctx_for_mask is not None:
            cached_mask = ctx_for_mask.artifact_mask
            if cached_mask is not None and cached_mask.size == total_samples:
                artifact_mask = cached_mask

    save_decomposition_npz(
        out_path,
        pulse_trains=pulse_trains,
        distimes=distimes,
        fsamp=fsamp,
        grid_names=grid_names,
        mu_grid_index=mu_grid_index,
        muscles=muscle_names,
        parameters=parameters,
        total_samples=total_samples,
        extras={"artifact_mask": artifact_mask} if artifact_mask is not None else None,
    )
    save_editlog(out_path.with_suffix(".json"), mu_uids, edit_history, artifact_times_all or None)

    participant_meta = payload.participant_meta or {}
    try:
        write_bids_dataset_description(
            bids_root,
            subject=subject,
            age=int(participant_meta["age"])
            if participant_meta.get("age") not in (None, "", "n/a")
            else None,
            sex=participant_meta.get("sex") or None,
            handedness=participant_meta.get("handedness") or None,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to write participants.tsv", exc_info=True)

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
        except Exception:  # noqa: BLE001, S110
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
    result: dict[str, Any] = {
        "saved": True,
        "path": str(out_path),
        "kept_indices": kept_mus,
        "mu_uids": mu_uids,
        "edit_history": edit_history,
    }
    if bids_paths:
        result["bids_emg_paths"] = bids_paths
    if deriv_paths:
        result["bids_deriv_paths"] = deriv_paths
    return make_json_safe(result)


def update_filter(payload: EditFilterPayload) -> dict[str, Any]:
    bids_root = resolve_bids_root(payload.project)
    edit_signal_token = payload.edit_signal_token
    file_label = payload.file_label or ""
    entity_label = payload.entity_label or parse_entity_label(file_label)
    grid_index = payload.grid_index
    distimes = normalize_distimes(payload.distimes or [])
    if not distimes:
        raise HTTPException(status_code=400, detail="distimes are required for filter update")

    mu_index = payload.mu_index
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")
    mu_grid_index = _normalize_mu_grid_index(payload.mu_grid_index, len(distimes))
    peeloff_win = payload.peel_off_win
    if peeloff_win <= 0:
        peeloff_win = DEFAULT_PEEL_OFF_WIN_SEC
    use_peeloff = payload.use_peeloff
    flagged = _normalize_flagged(payload.flagged, len(distimes))

    view_start = payload.view_start
    view_end = payload.view_end
    if view_end <= view_start:
        raise HTTPException(status_code=400, detail="view_start/view_end are required")
    nbextchan = payload.nbextchan

    emg: np.ndarray | None = None
    fsamp: float | None = None
    emg_mask: np.ndarray | None = None
    emg_is_presliced = False  # True only when BIDS loaded a view-length slice

    try:
        emg, fsamp, emg_mask = _load_bids_grid(
            bids_root, str(entity_label), grid_index, view_start, view_end
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
        data = np.asarray(ctx.data, dtype=float)
        if data.size == 0:
            raise HTTPException(
                status_code=400,
                detail="No BIDS EMG available. Reload decomposition MAT and retry filter update.",
            )
        if data.ndim == 1:
            data = data.reshape(1, -1)
        if data.ndim != 2:
            raise HTTPException(status_code=400, detail="Invalid cached EMG context")
        if data.shape[0] > data.shape[1]:
            data = data.T
        fsamp_val = ctx.fsamp
        if fsamp_val <= 0:
            raise HTTPException(status_code=400, detail="Missing fsamp in MAT signal context")
        grid_names = ctx.grid_names or ["Grid 1"]
        coordinates, _, _, _ = format_hdemg_signal(grid_names)
        if grid_index < 0 or grid_index >= len(coordinates):
            raise HTTPException(status_code=400, detail="grid_index out of range")
        ch_offset = 0
        for g in range(grid_index):
            ch_offset += int(coordinates[g].shape[0])
        n_ch = int(coordinates[grid_index].shape[0])
        emg = data[ch_offset : ch_offset + n_ch, :]
        fsamp = fsamp_val

        raw_masks = ctx.emgmask
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
    artifact_times = list(artifact_times_raw)

    artifact_mask: np.ndarray | None = None
    ctx_for_mask = _get_edit_signal_context(edit_signal_token) or _get_edit_signal_context_by_label(
        file_label
    )
    if ctx_for_mask is not None:
        artifact_mask = ctx_for_mask.artifact_mask

    bids_emg_offset = view_start if emg_is_presliced else 0
    if view_start - bids_emg_offset < 0 or view_end - bids_emg_offset > emg.shape[1]:
        raise HTTPException(
            status_code=400,
            detail="view window exceeds available EMG samples",
        )
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
            if i != mu_index and mu_grid_index[i] == grid_index and not flagged[i]
        ],
        peeloff_win=peeloff_win,
        emg_offset=bids_emg_offset,
        use_peeloff=use_peeloff,
        artifact_times=artifact_times or None,
        lock_spikes=lock_spikes,
        artifact_mask=artifact_mask,
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
                updated_pulse.tolist() if isinstance(updated_pulse, np.ndarray) else pulse_train
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
    y_min = payload.y_min if payload.y_min is not None else float("inf")
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
    y_min = payload.y_min if payload.y_min is not None else float("inf")

    artifact_times_raw = payload.artifact_times or []
    artifact_times = list(artifact_times_raw)

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
    updated_distimes = delete_spikes_in_roi(pulse, distimes[mu_index], x_start, x_end, y_min, y_max)

    # Also delete artifacts in the same ROI
    updated_artifact_times = None
    artifact_times_raw = payload.artifact_times
    if artifact_times_raw:
        artifact_times = list(artifact_times_raw)
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
    y_min = payload.y_min if payload.y_min is not None else float("inf")
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
    source = sorted({int(x) for x in distimes[mu_index]})
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

    if len(distimes) <= 1:
        return make_json_safe(
            {
                "kept_indices": list(range(len(distimes))),
                "distimes": distimes,
            }
        )

    mu_grid_index = _normalize_mu_grid_index(payload.mu_grid_index, len(distimes))
    kept_idx = _dedup(distimes, mu_grid_index, parameters, fsamp, total_samples)
    return make_json_safe(
        {
            "kept_indices": kept_idx,
            "distimes": [_clean_distimes(distimes[i]) for i in kept_idx],
            "removed_count": len(distimes) - len(kept_idx),
        }
    )


def flag_mu(payload: EditFlagPayload) -> dict[str, Any]:
    """Validate MU index and return the requested flag status without mutating spike times."""
    distimes = normalize_distimes(payload.distimes or [])
    mu_index = payload.mu_index
    if mu_index < 0 or mu_index >= len(distimes):
        raise HTTPException(status_code=400, detail="mu_index out of range")
    flagged = True if payload.flag is None else bool(payload.flag)
    return make_json_safe({"flagged": flagged})
