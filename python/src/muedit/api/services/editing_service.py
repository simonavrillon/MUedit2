"""Edit stage services: the server-side edit session, and the saves of edits and runs."""

from __future__ import annotations

import csv
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException
from fastapi.responses import Response

from muedit.api import config
from muedit.api.binary import FRAME_FORMAT, FRAME_MEDIA_TYPE, pack_frame
from muedit.api.cache import (
    _get_edit_session,
    _get_run_result_entry,
    _live_edit_logs,
    _release_edit_sessions,
    _resize_edit_session,
    _store_edit_session,
)
from muedit.api.common import (
    bids_root_for,
    make_json_safe,
    parse_entity_label,
    require_existing_path,
)
from muedit.api.memory import DEFAULT_SESSION
from muedit.api.schemas import (
    BidsSaveFields,
    EditOpPayload,
    EditRecoverPayload,
    EditSavePayload,
    EditSessionSavePayload,
)
from muedit.api.services.bids_helpers import (
    _infer_bids_root_from_decomp_path,
    _parse_all_bids_entities,
    _parse_subject_session_from_entity_label,
    _read_bids_channels_sidecar,
    _read_bids_grid,
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
    load_decomposition,
    normalize_distimes,
    pack_csr,
    save_decomposition_npz,
    save_editlog,
)
from muedit.decomp.postprocess import dedup_survivors
from muedit.decomp.preprocess import build_manual_artifact_mask
from muedit.decomp.types import DecompositionParameters
from muedit.editing.edit_log import EditLog, find_recoverable
from muedit.editing.session import Change, EditError, EditSession, spike_array, timestamp
from muedit.io.bids import (
    export_bids_emg,
    export_bids_mu_derivatives,
    write_bids_dataset_description,
)
from muedit.io.npz import RowSource
from muedit.io.store import SessionStore, copy_into
from muedit.models import (
    BoolArray,
    EditSignalContext,
    FloatArray,
    IntArray,
    LoadedDecomposition,
    resident_nbytes,
)

logger = logging.getLogger(__name__)


def _frame_response(meta: dict[str, Any], arrays: dict[str, tuple[Any, str]]) -> Response:
    return Response(
        content=pack_frame(meta, arrays),
        media_type=FRAME_MEDIA_TYPE,
        headers={"x-muedit-format": FRAME_FORMAT},
    )


def _dedup(
    distimes: list[IntArray],
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
    kept, _ = dedup_survivors(
        [np.asarray(d, dtype=int) for d in distimes],
        mu_grid_index,
        max(mu_grid_index, default=0) + 1,
        params,
        fsamp,
        total_samples,
    )
    return sorted(kept)


# ── opening a decomposition ──────────────────────────────────────────────────


@dataclass
class _FileExtras:
    """What the files around a decomposition add: BIDS sidecars and the saved edit log."""

    project: str | None = None
    bids_root: Path | None = None
    sidecar_meta: dict[str, Any] = field(default_factory=dict)
    mu_uids: list[Any] | None = None
    edit_history: list[Any] | None = None
    artifact_times: list[Any] | None = None


def _file_extras(filepath: str, file_label: str, decomp: LoadedDecomposition) -> _FileExtras:
    """Read the BIDS sidecars and ``.json`` edit log next to a decomposition; updates ``decomp``."""
    extras = _FileExtras()
    bids_root = _infer_bids_root_from_decomp_path(filepath)
    if bids_root is not None:
        extras.bids_root = bids_root
        extras.project = config.project_of(bids_root)
        try:
            entity_label = parse_entity_label(file_label)
            subject, bids_session = _parse_subject_session_from_entity_label(entity_label)
            emg_dir = bids_root / f"sub-{subject}"
            if bids_session:
                emg_dir = emg_dir / f"ses-{bids_session}"
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
            extras.sidecar_meta = read_bids_sidecar_meta(bids_root, entity_label)
        except (ValueError, OSError, csv.Error, KeyError):
            pass  # best-effort; I/O and parse errors are non-fatal

    editlog_path = Path(filepath).with_suffix(".json")
    if editlog_path.exists():
        try:
            with editlog_path.open("r", encoding="utf-8") as fh:
                editlog = json.load(fh)
            if isinstance(editlog.get("mu_uids"), list):
                extras.mu_uids = editlog["mu_uids"]
            if isinstance(editlog.get("history"), list):
                extras.edit_history = editlog["history"]
            if isinstance(editlog.get("artifact_times"), list):
                extras.artifact_times = editlog["artifact_times"]
        except (OSError, ValueError, KeyError):
            pass  # best-effort; missing or corrupt editlog is non-fatal
    return extras


def _dataset_root(edit: EditSession, project: str | None) -> Path:
    """The dataset the file was opened from while its project is unchanged, else the project's."""
    if edit.bids_root is not None and (project or "").strip() == edit.meta.get("project"):
        return edit.bids_root
    return bids_root_for(project)


def _session_pulse(decomp: LoadedDecomposition, store: SessionStore) -> FloatArray | None:
    """The file's pulse trains as float32 outside the heap, or None when it has none."""
    pulse = decomp.pulse_trains_full
    n_mu, total = len(decomp.distime_all), int(decomp.total_samples)
    if pulse.ndim != 2 or n_mu == 0 or pulse.shape != (n_mu, total):
        return None
    if pulse.dtype != np.float32 or resident_nbytes(pulse):
        pulse = copy_into(store, "pulse", pulse)
    return pulse


def _new_session(
    filepath: str,
    decomp: LoadedDecomposition,
    signal: EditSignalContext | None,
    store: SessionStore,
) -> EditSession:
    file_label = Path(filepath).name
    extras = _file_extras(filepath, file_label, decomp)
    n_mu = len(decomp.distime_all)
    fsamp = float(decomp.fsamp or 0.0)
    total = int(decomp.total_samples)
    parameters = dict(decomp.parameters)
    entity_label = parse_entity_label(file_label)

    def bids_grid(
        project: str | None, grid: int, into: SessionStore
    ) -> tuple[FloatArray, float, IntArray] | None:
        try:
            return _read_bids_grid(_dataset_root(edit, project), entity_label, grid, into)
        except (ValueError, FileNotFoundError):
            return None

    def duplicates(spikes: list[IntArray], grids: list[int]) -> list[int]:
        if fsamp <= 0:
            raise EditError("fsamp is required for deduplication")
        return _dedup(spikes, grids, parameters, fsamp, total)

    uids = extras.mu_uids
    edit = EditSession(
        store=store,
        fsamp=fsamp,
        total_samples=total,
        spikes=decomp.distime_all,
        pulse=_session_pulse(decomp, store),
        mu_grid_index=decomp.mu_grid_index,
        mu_uids=uids if uids and len(uids) == n_mu else _generate_mu_uids(decomp.mu_grid_index),
        artifacts=extras.artifact_times,
        history=extras.edit_history,
        signal=signal,
        bids_grid=bids_grid,
        duplicates=duplicates,
    )
    decomp.pulse_trains_full = np.zeros((0, 0), dtype=np.float32)
    edit.bids_root = extras.bids_root
    edit.meta = {
        "file_label": file_label,
        "source_path": str(Path(filepath).resolve()),
        "fsamp": decomp.fsamp,
        "total_samples": total,
        "grid_names": list(decomp.grid_names),
        "rois": [(int(s), int(e)) for s, e in decomp.rois],
        "parameters": parameters,
        "muscle": list(decomp.muscle),
        "sil": [float(x) for x in decomp.sil],
        **({"project": extras.project} if extras.project is not None else {}),
        **extras.sidecar_meta,
    }
    return edit


def open_edit_session(filepath: str, session: str = DEFAULT_SESSION) -> Response:
    """Open a decomposition for editing; the frame holds its fields and discharge times.

    The previous edit session of the tab closes first. ``recoverable_edits`` counts the
    unsaved edits an earlier session left for this file (``/edit/session/recover``).
    """
    if require_existing_path(filepath).suffix.lower() not in {".npz", ".mat"}:
        raise HTTPException(
            status_code=400,
            detail={
                "field": "path",
                "reason": "Unsupported decomposition format. Expected .mat or .npz",
            },
        )
    _release_edit_sessions(session)
    store = SessionStore.create("edit")
    try:
        decomp, signal = load_decomposition(filepath, store, binary_trains=False)
        edit = _new_session(filepath, decomp, signal, store)
    except BaseException:
        store.close()
        raise
    token = _store_edit_session(edit, session)
    with edit.lock:
        edit.recovery = find_recoverable(filepath, _live_edit_logs())
        edit.log = EditLog.create(filepath, token)
        return _state_response(edit, token)


def _require_session(token: str, session: str) -> EditSession:
    edit = _get_edit_session(token, session)
    if edit is None:
        raise HTTPException(
            status_code=400,
            detail={"field": "token", "reason": "Edit session expired; open the file again"},
        )
    return edit


def _per_mu(edit: EditSession) -> dict[str, Any]:
    """The small per-MU arrays every response carries in full."""
    return {
        "n_mu": edit.n_mu,
        "mu_uids": list(edit.mu_uids),
        "mu_grid_index": list(edit.mu_grid_index),
        "flagged": list(edit.flagged),
        "versions": list(edit.versions),
        "has_pulse": [edit.has_pulse(i) for i in range(edit.n_mu)],
        "dirty": edit.dirty,
        "can_undo": edit.can_undo,
    }


def _spike_arrays(spikes: list[IntArray], artifacts: list[IntArray]) -> dict[str, tuple[Any, str]]:
    values, offsets = pack_csr(spikes, np.int32)
    art_values, art_offsets = pack_csr(artifacts, np.int32)
    return {
        "spikes": (values, "i4"),
        "spike_offsets": (offsets, "i8"),
        "artifacts": (art_values, "i4"),
        "artifact_offsets": (art_offsets, "i8"),
    }


def _state_response(edit: EditSession, token: str, **extra: Any) -> Response:
    recovery = edit.recovery
    meta = {
        **edit.meta,
        "token": token,
        **_per_mu(edit),
        "edit_history": edit.history,
        "recoverable_edits": recovery.edits if recovery is not None else 0,
        **extra,
    }
    return _frame_response(meta, _spike_arrays(edit.spikes, edit.artifacts))


def _change_response(edit: EditSession, change: Change) -> Response:
    meta = {
        **_per_mu(edit),
        "changed": change.changed,
        "history_start": change.history_start,
        "history": edit.history[change.history_start :],
        **({"kept_indices": change.kept} if change.kept is not None else {}),
        **change.info,
    }
    arrays = _spike_arrays(
        [edit.spikes[i] for i in change.changed], [edit.artifacts[i] for i in change.changed]
    )
    return _frame_response(meta, arrays)


def edit_session_state(token: str, session: str = DEFAULT_SESSION) -> Response:
    """The whole state of an open session, for a page that was reloaded; the tab takes it over."""
    edit = _require_session(token, session)
    with edit.lock:
        return _state_response(edit, token)


def apply_edit(op: str, payload: EditOpPayload, session: str = DEFAULT_SESSION) -> Response:
    """Run one edit; the frame holds what changed."""
    edit = _require_session(payload.token, session)
    args = payload.model_dump(exclude_unset=True, exclude={"token"})
    with edit.lock:
        try:
            change = edit.apply(op, args)
        except EditError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response = _change_response(edit, change)
    _resize_edit_session(payload.token)
    return response


def recover_edits(payload: EditRecoverPayload, session: str = DEFAULT_SESSION) -> Response:
    """Replay the unsaved edits an earlier session left for this file, or drop them."""
    edit = _require_session(payload.token, session)
    with edit.lock:
        recovery, edit.recovery = edit.recovery, None
        applied = 0
        if recovery is not None:
            try:
                if payload.apply:
                    applied = edit.replay(recovery.records)
            finally:
                # Replayed edits are in this session's own log now; a failed replay is not retried.
                recovery.discard()
        response = _state_response(edit, payload.token, recovered_edits=applied)
    _resize_edit_session(payload.token)
    return response


# ── saving ───────────────────────────────────────────────────────────────────


@dataclass
class _SaveRequest:
    """Everything a save writes, whoever holds the edits."""

    distimes: list[IntArray]
    flagged: list[bool]
    mu_grid_index: list[int]
    mu_uids: list[str]
    edit_history: list[dict[str, Any]]
    artifact_times: list[list[int]]
    fsamp: float
    total_samples: int
    grid_names: list[str]
    parameters: dict[str, Any]
    form: BidsSaveFields
    pulse: Callable[[list[int]], FloatArray | RowSource | None]  # pulse trains of the kept MUs
    artifact_mask: BoolArray | None = None
    signal: EditSignalContext | None = None  # raw EMG for the BIDS export
    bids_root: Path | None = None  # where the files go; else the form's project
    before_write: Callable[[Path], None] | None = None


def _save_removal_entry(entry_type: str, removed_uids: list[str]) -> dict[str, Any]:
    """Build the editlog entry for MUs dropped while saving."""
    return {
        "type": entry_type,
        "on_save": True,
        "removed_count": len(removed_uids),
        "removed_mu_uids": removed_uids,
        "timestamp": timestamp(),
    }


def _export_bids_emg(
    ctx: EditSignalContext | None,
    bids_root: Path,
    entity_label: str,
    fsamp: float | None,
    grid_names: list[str],
    muscle_names: list[str],
    form: BidsSaveFields,
) -> dict[str, str] | None:
    """Best-effort BIDS EMG export of the raw EMG a decomposition file embeds."""
    if ctx is None or ctx.data.size == 0 or ctx.prefiltered:
        return None  # no raw EMG: a mask-only context, or a v1 .npz holding filtered EMG

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
            manufacturer=form.manufacturer or meta.get("manufacturer"),
            manufacturers_model_name=form.manufacturers_model_name or meta.get("device_name"),
            powerline_freq=float(form.powerline_freq or 0) or meta.get("powerline_freq") or 50.0,
            placement_scheme=form.placement_scheme or "ChannelSpecific",
            placement_scheme_description=form.placement_scheme_description or None,
            task_description=form.task_description or None,
            software_versions=form.software_versions or None,
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


def _save(req: _SaveRequest) -> tuple[dict[str, Any], list[int], list[dict[str, Any]]]:
    """Write the edited decomposition and its BIDS files.

    Returns the response, the indices of the MUs the file keeps, and the log entries
    for the MUs dropped on save.
    """
    form = req.form
    distimes = list(req.distimes)
    fsamp = req.fsamp
    mu_grid_index = list(req.mu_grid_index)
    expected_grid_count = (max(mu_grid_index) + 1) if mu_grid_index else 1
    grid_names = _pad_grid_names(req.grid_names, expected_grid_count, [])
    parameters = dict(req.parameters)
    muscle_names = _normalize_muscle_names(form.muscle or form.muscle_names)
    if muscle_names and not parameters.get("target_muscle"):
        parameters["target_muscle"] = muscle_names if len(muscle_names) > 1 else muscle_names[0]

    mu_uids = list(req.mu_uids)
    artifact_times = [list(row) for row in req.artifact_times]
    artifact_times.extend([] for _ in range(len(distimes) - len(artifact_times)))
    entries: list[dict[str, Any]] = []

    # Schema declares these as bool | None; default to True when unspecified.
    remove_flagged = True if form.remove_flagged is None else form.remove_flagged
    remove_duplicates = True if form.remove_duplicates is None else form.remove_duplicates

    # Indices into the request's MUs that survive the save, in saved order.
    kept_mus = list(range(len(distimes)))

    def keep(indices: list[int]) -> None:
        nonlocal distimes, mu_grid_index, mu_uids, artifact_times, kept_mus
        distimes = [distimes[i] for i in indices]
        mu_grid_index = [mu_grid_index[i] for i in indices]
        mu_uids = [mu_uids[i] for i in indices]
        artifact_times = [artifact_times[i] for i in indices]
        kept_mus = [kept_mus[i] for i in indices]

    if remove_flagged and distimes:
        flagged = _normalize_flagged(req.flagged, len(distimes))
        removed_uids = [mu_uids[i] for i in range(len(distimes)) if flagged[i]]
        keep([i for i in range(len(distimes)) if not flagged[i]])
        if removed_uids:
            entries.append(_save_removal_entry("remove_flagged", removed_uids))

    if remove_duplicates and len(distimes) > 1 and fsamp and fsamp > 0:
        kept_idx = _dedup(distimes, mu_grid_index, parameters, fsamp, req.total_samples)
        kept_set = set(kept_idx)
        removed_uids = [uid for i, uid in enumerate(mu_uids) if i not in kept_set]
        keep(kept_idx)
        if removed_uids:
            entries.append(_save_removal_entry("remove_duplicates", removed_uids))
    edit_history = [*req.edit_history, *entries]

    bids_root = req.bids_root if req.bids_root is not None else bids_root_for(form.project)
    file_label = form.file_label or ""
    entity_label = form.entity_label or parse_entity_label(file_label)
    subject, bids_session = _parse_subject_session_from_entity_label(entity_label)
    decomp_dir = bids_root / "derivatives" / "muedit" / f"sub-{subject}"
    if bids_session:
        decomp_dir = decomp_dir / f"ses-{bids_session}"
    decomp_dir = decomp_dir / "decomp"
    decomp_dir.mkdir(parents=True, exist_ok=True)
    out_path = decomp_dir / f"{entity_label}_edited.npz"

    if req.before_write is not None:
        req.before_write(out_path)
    save_decomposition_npz(
        out_path,
        pulse_trains=req.pulse(kept_mus),
        distimes=distimes,
        fsamp=fsamp,
        grid_names=grid_names,
        mu_grid_index=mu_grid_index,
        muscles=muscle_names,
        parameters=parameters,
        total_samples=req.total_samples,
        artifact_mask=req.artifact_mask,
    )
    save_editlog(out_path.with_suffix(".json"), mu_uids, edit_history, artifact_times or None)

    participant_meta = form.participant_meta or {}
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
                distimes=[np.asarray(d).tolist() for d in distimes],
                fsamp=fsamp,
                bids_root=bids_root,
                entities=entity_label,
                mu_uids=mu_uids,
            )
            deriv_paths = {k: str(v) for k, v in deriv_result.items()}
        except Exception:  # noqa: BLE001, S110
            pass  # derivatives export is best-effort; never block the primary save

    bids_paths = _export_bids_emg(
        req.signal, bids_root, entity_label, fsamp, grid_names, muscle_names, form
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
    return result, kept_mus, entries


def save_edit_session(
    payload: EditSessionSavePayload, session: str = DEFAULT_SESSION
) -> dict[str, Any]:
    """Save an edit session; the saved file becomes the session's baseline."""
    edit = _require_session(payload.token, session)
    with edit.lock:
        if edit.fsamp <= 0:
            raise HTTPException(status_code=400, detail="fsamp is required to save edits")
        meta = edit.meta
        mask = edit.signal.artifact_mask if edit.signal is not None else None
        req = _SaveRequest(
            distimes=edit.spikes,
            flagged=edit.flagged,
            mu_grid_index=edit.mu_grid_index,
            mu_uids=edit.mu_uids,
            edit_history=edit.history,
            artifact_times=[a.tolist() for a in edit.artifacts],
            fsamp=edit.fsamp,
            total_samples=edit.total_samples,
            grid_names=list(meta.get("grid_names") or []),
            parameters=dict(meta.get("parameters") or {}),
            form=payload.model_copy(
                update={"file_label": payload.file_label or meta.get("file_label")}
            ),
            pulse=edit.pulse_rows,
            artifact_mask=mask if mask is not None and mask.size == edit.total_samples else None,
            signal=edit.signal,
            bids_root=_dataset_root(edit, payload.project),
            # Windows cannot replace a file that is memory-mapped.
            before_write=(lambda path: edit.detach(str(path))) if sys.platform == "win32" else None,
        )
        result, kept, entries = _save(req)
        edit.saved(kept, entries)
        if edit.log is not None:
            edit.log.close(keep=False)
        edit.log = EditLog.create(result["path"], payload.token)
        result.update(_per_mu(edit))
    _resize_edit_session(payload.token)
    return make_json_safe(result)


def save_edits(payload: EditSavePayload, pulse_trains: np.ndarray | None = None) -> dict[str, Any]:
    """Save a run: discharge times from the request, else from the stored run.

    Pulse trains come from the request frame, else the stored run.
    """
    run = _get_run_result_entry(payload.run_result_token)
    raw = payload.distimes or payload.discharge_times
    if raw:
        distimes = [spike_array(d) for d in normalize_distimes(raw)]
    elif run is not None:
        distimes = list(run.spikes)
    else:
        # Without it the save would write a file with no motor units and report success.
        raise HTTPException(
            status_code=400,
            detail={
                "field": "run_result_token",
                "reason": "The run's results are no longer on the server; run the decomposition again",
            },
        )
    total_samples = payload.total_samples
    if total_samples <= 0:
        raise HTTPException(status_code=400, detail="total_samples is required to save edits")
    fsamp = payload.fsamp
    if fsamp is None:
        raise HTTPException(status_code=400, detail="fsamp is required to save edits")

    if pulse_trains is None and run is not None:
        pulse_trains = run.pulse_trains
    if pulse_trains is not None and (
        pulse_trains.size == 0 or pulse_trains.shape != (len(distimes), total_samples)
    ):
        pulse_trains = None  # the loader draws them from the discharge times
    matrix = pulse_trains

    def pulse(kept: list[int]) -> FloatArray | RowSource | None:
        if matrix is None or len(kept) == matrix.shape[0]:
            return matrix
        # Read as the file is written, not indexed into a heap copy of the kept rows.
        return RowSource((len(kept), matrix.shape[1]), lambda a, b: matrix[kept[a:b]])

    mu_grid_index = _normalize_mu_grid_index(payload.mu_grid_index, len(distimes))
    uids = payload.mu_uids
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

    req = _SaveRequest(
        distimes=distimes,
        flagged=_normalize_flagged(payload.flagged, len(distimes)),
        mu_grid_index=mu_grid_index,
        mu_uids=list(uids)
        if isinstance(uids, list) and len(uids) == len(distimes)
        else _generate_mu_uids(mu_grid_index),
        edit_history=list(payload.edit_history or []),
        artifact_times=[list(row) for row in payload.artifact_times or []],
        fsamp=fsamp,
        total_samples=total_samples,
        grid_names=list(payload.grid_names or []),
        parameters=dict(payload.parameters or {}),
        form=payload,
        pulse=pulse,
        artifact_mask=build_manual_artifact_mask(regions, total_samples),
    )
    result, _, _ = _save(req)
    return make_json_safe(result)
