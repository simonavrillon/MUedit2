"""Edit stage services: the server-side edit session, and the saves of edits and runs."""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fastapi.responses import Response

from muedit.api import config
from muedit.api.binary import FRAME_FORMAT, FRAME_MEDIA_TYPE, pack_frame
from muedit.api.cache import (
    RunExtras,
    _get_edit_session,
    _held_run_result,
    _live_edit_logs,
    _resize_edit_session,
    _store_edit_session,
)
from muedit.api.common import (
    bids_root_for,
    dataset_root_of,
    make_json_safe,
    parse_entity_label,
    require_existing_path,
)
from muedit.api.memory import DEFAULT_SESSION
from muedit.api.schemas import (
    BidsSaveFields,
    EditOpPayload,
    EditPrepareGridPayload,
    EditRecoverPayload,
    EditSavePayload,
    EditSessionSavePayload,
)
from muedit.api.services.edit_helpers import (
    _coerce_bool_param,
    _coerce_dup_tol,
    _expected_grid_count,
    _normalize_flagged,
    _normalize_mu_grid_index,
    _pad_grid_names,
)
from muedit.decomp.decomposition_file import (
    load_decomposition,
    normalize_spike_times,
    pack_csr,
    save_decomposition_npz,
    save_editlog,
)
from muedit.decomp.postprocess import dedup_survivors
from muedit.decomp.preprocess import build_manual_artifact_mask
from muedit.decomp.types import DecompositionParameters
from muedit.editing.edit_log import EditLog, find_recoverable
from muedit.editing.session import (
    SESSION_EXPIRED,
    Change,
    EditError,
    EditSession,
    initial_mu_uids,
    timestamp,
)
from muedit.errors import MueditError
from muedit.io._mat import parse_name_list
from muedit.io.bids import (
    BidsEntities,
    BidsFieldError,
    bids_emg_fields,
    export_bids_emg,
    export_bids_events,
    export_bids_mu_derivatives,
    form_over_loader_meta,
    read_bids_emg_grid,
    read_channel_groups,
    read_channels_summary,
    read_sidecar_meta,
    resolve_bids_channels_tsv,
    write_participant,
)
from muedit.io.npz import RowSource
from muedit.models import (
    BoolArray,
    EditSignalContext,
    FloatArray,
    IntArray,
    LoadedDecomposition,
    resident_nbytes,
    spike_array,
)
from muedit.storage import SessionStore, copy_into

logger = logging.getLogger(__name__)


def _frame_response(meta: dict[str, Any], arrays: dict[str, tuple[Any, str]]) -> Response:
    return Response(
        content=pack_frame(meta, arrays),
        media_type=FRAME_MEDIA_TYPE,
        headers={"x-muedit-format": FRAME_FORMAT},
    )


def _dedup(
    spike_times: list[IntArray],
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
        spike_times,
        mu_grid_index,
        max(mu_grid_index, default=0) + 1,
        params,
        fsamp,
        total_samples,
    )
    return kept


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
    bids_root = dataset_root_of(filepath)
    if bids_root is not None:
        extras.bids_root = bids_root
        extras.project = config.project_of(bids_root)
        try:
            entity_label = parse_entity_label(file_label)
            extras.sidecar_meta = read_sidecar_meta(bids_root, entity_label)
            emg_dir = BidsEntities.parse(entity_label).emg_dir(bids_root)
            channels_tsv = resolve_bids_channels_tsv(emg_dir, entity_label)
            grid_names, muscle_names, fsamp = read_channels_summary(channels_tsv)
            if grid_names:
                decomp.grid_names = _pad_grid_names(
                    grid_names, _expected_grid_count(decomp), decomp.grid_names
                )
            if muscle_names:
                decomp.muscle_names = muscle_names
            if fsamp and fsamp > 0:
                decomp.fsamp = fsamp
        except (ValueError, OSError, csv.Error, KeyError) as exc:
            logger.warning("Cannot read the BIDS sidecars of %s: %s", filepath, exc)

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
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("Cannot read the edit log %s: %s", editlog_path, exc)
    return extras


def _dataset_root(edit: EditSession, project: str | None) -> Path:
    """The dataset the file was opened from while its project is unchanged, else the project's."""
    if edit.bids_root is not None and (project or "").strip() == edit.meta.get("project"):
        return edit.bids_root
    return bids_root_for(project)


def _session_pulse_trains(decomp: LoadedDecomposition, store: SessionStore) -> FloatArray | None:
    """The file's pulse trains as float32 outside the heap, or None when it has none."""
    pulse_trains = decomp.pulse_trains_full
    n_mu, total = len(decomp.spike_times), int(decomp.total_samples)
    if pulse_trains.ndim != 2 or n_mu == 0 or pulse_trains.shape != (n_mu, total):
        return None
    if pulse_trains.dtype != np.float32 or resident_nbytes(pulse_trains):
        pulse_trains = copy_into(store, "pulse", pulse_trains)
    return pulse_trains


def _new_session(
    filepath: str,
    decomp: LoadedDecomposition,
    signal: EditSignalContext | None,
    store: SessionStore,
) -> EditSession:
    file_label = Path(filepath).name
    extras = _file_extras(filepath, file_label, decomp)
    n_mu = len(decomp.spike_times)
    fsamp = float(decomp.fsamp or 0.0)
    total = int(decomp.total_samples)
    parameters = dict(decomp.parameters)
    entity_label = parse_entity_label(file_label)

    def bids_grid(
        project: str | None, grid: int, into: SessionStore
    ) -> tuple[FloatArray, float, IntArray] | None:
        try:
            return read_bids_emg_grid(_dataset_root(edit, project), entity_label, grid, into)
        except (ValueError, FileNotFoundError):
            return None  # not in a BIDS dataset: the session refits on its embedded EMG

    def duplicates(spikes: list[IntArray], grids: list[int]) -> list[int]:
        if fsamp <= 0:
            raise EditError("fsamp is required for deduplication")
        return _dedup(spikes, grids, parameters, fsamp, total)

    uids = extras.mu_uids
    edit = EditSession(
        store=store,
        fsamp=fsamp,
        total_samples=total,
        spike_times=decomp.spike_times,
        pulse_trains=_session_pulse_trains(decomp, store),
        mu_grid_index=decomp.mu_grid_index,
        mu_uids=uids if uids and len(uids) == n_mu else initial_mu_uids(decomp.mu_grid_index),
        artifacts=extras.artifact_times,
        history=extras.edit_history,
        signal=signal,
        bids_grid=bids_grid,
        duplicates=duplicates,
    )
    decomp.pulse_trains_full = np.zeros((0, 0), dtype=np.float32)
    edit.bids_root = extras.bids_root
    if len(decomp.sil) == n_mu:
        edit.sil_by_uid = dict(zip(edit.mu_uids, map(float, decomp.sil), strict=True))
    edit.meta = {
        "file_label": file_label,
        "source_path": str(Path(filepath).resolve()),
        "fsamp": decomp.fsamp,
        "total_samples": total,
        "grid_names": list(decomp.grid_names),
        "rois": [(int(s), int(e)) for s, e in decomp.rois],
        "parameters": parameters,
        "muscle_names": list(decomp.muscle_names),
        "sil": [float(x) for x in decomp.sil],
        **({"project": extras.project} if extras.project is not None else {}),
        **extras.sidecar_meta,
    }
    return edit


def open_edit_session(filepath: str, session: str = DEFAULT_SESSION) -> Response:
    """Open a decomposition for editing; the frame holds its fields and discharge times.

    The tab's previous edit session closes once this one is built: a file that fails to open
    leaves it open. ``recoverable_edits`` counts the unsaved edits an earlier session left for
    this file (``/edit/session/recover``).
    """
    require_existing_path(filepath)
    store = SessionStore.create("edit")
    try:
        decomp, signal = load_decomposition(filepath, store, binary_trains=False)
        edit = _new_session(filepath, decomp, signal, store)
    except BaseException:
        store.close()
        raise
    token = _store_edit_session(edit, session)
    # Before edit.lock: the budget lock is never taken under it (a dropped session closes under it).
    live_logs = _live_edit_logs()
    with edit.lock:
        _check_open(edit)
        # Created first: a log another tab opens meanwhile is in its live set or has edits.
        edit.log = EditLog.create(filepath, token)
        if edit.log is not None:
            live_logs.add(edit.log.path)
        edit.recovery = find_recoverable(filepath, live_logs)
        return _state_response(edit, token)


def _expired() -> MueditError:
    return MueditError(SESSION_EXPIRED, "token")


def _require_session(token: str, session: str) -> EditSession:
    edit = _get_edit_session(token, session)
    if edit is None:
        raise _expired()
    return edit


def _check_open(edit: EditSession) -> None:
    """Under ``edit.lock``: the 400 ``_require_session`` gives, for a session closed since."""
    if edit.closed:
        raise _expired()


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
        "crash_recovery": edit.log is not None,  # unsaved edits survive a crash of the app
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


#: Log entries a state or save response carries; the sidecar keeps the whole log.
HISTORY_TAIL = 100


def _history_tail(edit: EditSession) -> dict[str, Any]:
    start = edit.history_tail_start(HISTORY_TAIL)
    return {"edit_history": edit.history[start:], "edit_history_start": start}


def _state_response(edit: EditSession, token: str, **extra: Any) -> Response:
    recovery = edit.recovery
    meta = {
        **edit.meta,
        "token": token,
        **_per_mu(edit),
        **_history_tail(edit),
        "recoverable_edits": recovery.edits if recovery is not None else 0,
        **extra,
    }
    return _frame_response(meta, _spike_arrays(edit.spike_times, edit.artifacts))


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
        [edit.spike_times[i] for i in change.changed],
        [edit.artifacts[i] for i in change.changed],
    )
    return _frame_response(meta, arrays)


def edit_session_state(token: str, session: str = DEFAULT_SESSION) -> Response:
    """The whole state of an open session, for a page that was reloaded; the tab takes it over."""
    edit = _require_session(token, session)
    with edit.lock:
        _check_open(edit)
        return _state_response(edit, token)


def apply_edit(op: str, payload: EditOpPayload, session: str = DEFAULT_SESSION) -> Response:
    """Run one edit; the frame holds what changed."""
    edit = _require_session(payload.token, session)
    args = payload.model_dump(exclude_unset=True, exclude={"token"})
    with edit.lock:
        change = edit.apply(op, args)
        response = _change_response(edit, change)
    _resize_edit_session(payload.token)
    return response


def recover_edits(payload: EditRecoverPayload, session: str = DEFAULT_SESSION) -> Response:
    """Replay the unsaved edits an earlier session left for this file, or drop them."""
    edit = _require_session(payload.token, session)
    with edit.lock:
        _check_open(edit)
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


def prepare_edit_grid(payload: EditPrepareGridPayload, session: str = DEFAULT_SESSION) -> None:
    """Filter a grid's EMG now, so its first filter update does not wait for it; edits go on."""
    edit = _require_session(payload.token, session)
    edit.prepare_grid(payload.project, payload.grid)


# ── saving ───────────────────────────────────────────────────────────────────


@dataclass
class _SaveRequest:
    """Everything a save writes, whoever holds the edits."""

    spike_times: list[IntArray]
    flagged: list[bool]
    mu_grid_index: list[int]
    mu_uids: list[str]
    edit_history: list[dict[str, Any]]
    artifact_times: list[IntArray]
    fsamp: float
    total_samples: int
    grid_names: list[str]
    parameters: dict[str, Any]
    form: BidsSaveFields
    pulse_trains: Callable[
        [list[int]], FloatArray | RowSource | None
    ]  # pulse trains of the kept MUs
    muscle_names: list[str] = field(default_factory=list)  # the file's, for a form that names none
    sil: list[float | None] | None = None  # per MU; None where unknown
    rois: list[tuple[int, int]] | None = None
    target_segments: dict[str, Any] | None = None  # a run's; else the opened file's
    artifact_mask: BoolArray | None = None
    signal: EditSignalContext | None = None  # raw EMG for the BIDS export
    bids_root: Path | None = None  # where the files go; else the form's project
    subject: str | None = None  # the subject a save back into the file's own dataset keeps
    before_write: Callable[[Path], None] | None = None

    @property
    def segments(self) -> dict[str, Any] | None:
        """The segmented target the saved file carries."""
        if self.target_segments is not None or self.signal is None:
            return self.target_segments
        return self.signal.target_segments


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
    req: _SaveRequest,
    bids_root: Path,
    entity_label: str,
    grid_names: list[str],
    muscle_names: list[str],
    meta: dict[str, Any],
    warnings: list[str],
) -> tuple[dict[str, str] | None, str | None]:
    """Best-effort BIDS EMG export of the raw EMG a decomposition file embeds: its paths, or why not."""
    ctx = req.signal
    if ctx is None or ctx.data.size == 0 or ctx.prefiltered:
        # No raw EMG: a mask-only context, or a v1 .npz holding filtered EMG.
        return None, None

    entities = BidsEntities.parse(entity_label)
    fsamp = float(req.fsamp or ctx.fsamp)
    try:
        paths = export_bids_emg(
            data=ctx.data,
            fsamp=fsamp,
            grid_names=ctx.grid_names or grid_names,
            coordinates=ctx.coordinates,
            discard_channels=ctx.discard_channels,
            bids_root=bids_root,
            ied=ctx.ied,
            subject=entities.subject,
            task=entities.task or "task",
            run=entities.run,
            session=entities.session,
            acquisition=entities.acquisition,
            recording=entities.recording,
            aux_data=ctx.aux_data,
            aux_names=ctx.aux_names or None,
            **bids_emg_fields(meta, muscle_names, req.form.task_description),
        )
    except BidsFieldError as exc:
        return None, str(exc)  # the save itself goes on
    except Exception:  # noqa: BLE001
        logger.warning("BIDS EMG export failed", exc_info=True)
        return None, "The BIDS EMG export failed; see the server log."
    exported = {k: str(v) for k, v in paths.items()}
    try:
        events = export_bids_events(
            paths["edf"], fsamp, aux_data=ctx.aux_data, target_segments=req.segments
        )
    except Exception:  # noqa: BLE001
        logger.warning("BIDS events export failed", exc_info=True)
        warnings.append("The BIDS events.tsv was not written; see the server log.")
    else:
        if events is not None:
            exported["events_tsv"] = str(events)
    return exported, None


def _kept_mus(
    req: _SaveRequest, parameters: dict[str, Any]
) -> tuple[list[int], list[dict[str, Any]]]:
    """The request's MUs the file keeps, in order, and the log entries for those it drops."""
    kept = list(range(len(req.spike_times)))
    entries: list[dict[str, Any]] = []
    # Unset (None) form fields leave both steps on.
    if req.form.remove_flagged is not False and kept:
        flagged = _normalize_flagged(req.flagged, len(kept))
        dropped = [i for i in kept if flagged[i]]
        kept = [i for i in kept if not flagged[i]]
        if dropped:
            entries.append(_save_removal_entry("remove_flagged", [req.mu_uids[i] for i in dropped]))
    if req.form.remove_duplicates is not False and len(kept) > 1 and req.fsamp > 0:
        survivors = _dedup(
            [req.spike_times[i] for i in kept],
            [req.mu_grid_index[i] for i in kept],
            parameters,
            req.fsamp,
            req.total_samples,
        )
        survived = set(survivors)
        dropped = [i for j, i in enumerate(kept) if j not in survived]
        kept = [kept[j] for j in survivors]
        if dropped:
            entries.append(
                _save_removal_entry("remove_duplicates", [req.mu_uids[i] for i in dropped])
            )
    return kept, entries


def _file_subject(file_label: str | None) -> str | None:
    """The subject a decomposition file's BIDS name gives, if it has one."""
    try:
        return BidsEntities.parse(parse_entity_label(file_label or "")).subject
    except ValueError:
        return None


def _save_target(req: _SaveRequest) -> tuple[Path, str, Path]:
    """The BIDS dataset, the recording's entity label, and the ``_edited.npz`` path in its derivatives."""
    bids_root = req.bids_root if req.bids_root is not None else bids_root_for(req.form.project)
    try:
        entity_label = req.form.entity_label or parse_entity_label(req.form.file_label or "")
        entities = BidsEntities.parse(entity_label)
    except ValueError as exc:
        raise MueditError(str(exc), "entity_label") from exc
    if req.subject is not None and entities.subject != req.subject:
        raise MueditError(
            f"The file is sub-{req.subject}'s: saving it as sub-{entities.subject} "
            f"would write it into another subject's folder of {bids_root}. Save it under "
            "its own subject, or into another project",
            "entity_label",
        )
    decomp_dir = entities.derivative_dir(bids_root, "decomp")
    return bids_root, entity_label, decomp_dir / f"{entity_label}_edited.npz"


def _raw_grid_groups(bids_root: Path, entity_label: str) -> list[str] | None:
    """The ``group`` of each grid in the recording's raw channels.tsv, when it has one."""
    try:
        emg_dir = BidsEntities.parse(entity_label).emg_dir(bids_root)
        return read_channel_groups(resolve_bids_channels_tsv(emg_dir, entity_label)) or None
    except (OSError, ValueError, csv.Error):
        return None


def _export_mu_derivatives(
    req: _SaveRequest, kept: list[int], bids_root: Path, entity_label: str, warnings: list[str]
) -> dict[str, str] | None:
    """Best-effort discharge-time derivatives of the kept MUs: their paths, or None."""
    if not kept or req.fsamp <= 0:
        return None
    loader_labels = (req.signal.loader_meta if req.signal else {}).get("grid_labels")
    try:
        paths = export_bids_mu_derivatives(
            spike_times=[req.spike_times[i] for i in kept],
            fsamp=req.fsamp,
            bids_root=bids_root,
            entity_label=entity_label,
            mu_uids=[req.mu_uids[i] for i in kept],
            mu_grid_index=[req.mu_grid_index[i] for i in kept],
            grid_labels=_raw_grid_groups(bids_root, entity_label) or loader_labels,
        )
    except Exception:  # noqa: BLE001
        logger.warning("BIDS derivatives export failed", exc_info=True)
        warnings.append("The motor unit derivatives were not written; see the server log.")
        return None
    return {k: str(v) for k, v in paths.items()}


def _write_decomposition(
    req: _SaveRequest, out_path: Path, kept: list[int], editlog: dict[str, Any], **npz: Any
) -> None:
    """Write the ``.npz`` and its edit-log sidecar, replacing both or neither."""
    log_path = out_path.with_suffix(".json")
    npz_tmp = out_path.with_name(f".{out_path.stem}.saving.npz")
    log_tmp = log_path.with_name(f".{log_path.stem}.saving.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if req.before_write is not None:
        req.before_write(out_path)
    try:
        save_decomposition_npz(
            npz_tmp,
            pulse_trains=req.pulse_trains(kept),
            spike_times=[req.spike_times[i] for i in kept],
            fsamp=req.fsamp,
            mu_grid_index=[req.mu_grid_index[i] for i in kept],
            total_samples=req.total_samples,
            artifact_mask=req.artifact_mask,
            rois=req.rois,
            target_segments=req.segments,
            **npz,
        )
        save_editlog(log_tmp, **editlog)
        # Both are complete before either replaces the previous save.
        os.replace(npz_tmp, out_path)
        os.replace(log_tmp, log_path)
    finally:
        npz_tmp.unlink(missing_ok=True)
        log_tmp.unlink(missing_ok=True)


def _save(req: _SaveRequest) -> tuple[dict[str, Any], list[int], list[dict[str, Any]]]:
    """Write the edited decomposition and its BIDS files.

    Returns the response, the indices of the MUs the file keeps, and the log entries
    for the MUs dropped on save.
    """
    form = req.form
    grid_names = _pad_grid_names(req.grid_names, max(req.mu_grid_index, default=0) + 1, [])
    parameters = dict(req.parameters)
    muscle_names = parse_name_list(form.muscle_names) or list(req.muscle_names)
    if muscle_names and not parameters.get("target_muscle"):
        parameters["target_muscle"] = muscle_names if len(muscle_names) > 1 else muscle_names[0]

    kept, entries = _kept_mus(req, parameters)
    mu_uids = [req.mu_uids[i] for i in kept]
    edit_history = [*req.edit_history, *entries]
    artifacts = [
        req.artifact_times[i] if i < len(req.artifact_times) else spike_array([]) for i in kept
    ]
    sil = [req.sil[i] for i in kept] if req.sil is not None else []
    loader_meta = form_over_loader_meta(
        req.signal.loader_meta if req.signal is not None else {}, form.model_dump()
    )

    bids_root, entity_label, out_path = _save_target(req)
    _write_decomposition(
        req,
        out_path,
        kept,
        {"mu_uids": mu_uids, "edit_history": edit_history, "artifact_times": artifacts},
        grid_names=grid_names,
        muscle_names=muscle_names,
        parameters=parameters,
        sil=sil if sil and all(s is not None for s in sil) else None,
        loader_meta=loader_meta or None,
    )

    warnings: list[str] = []
    participant = form.participant_meta.model_dump() if form.participant_meta else None
    try:
        write_participant(bids_root, BidsEntities.parse(entity_label).subject, participant)
    except (OSError, ValueError, csv.Error):
        logger.warning("Failed to write participants.tsv", exc_info=True)
        warnings.append("participants.tsv was not updated; see the server log.")
    # Before the derivatives: their unit groups are read from the raw channels.tsv.
    bids_paths, bids_error = _export_bids_emg(
        req, bids_root, entity_label, grid_names, muscle_names, loader_meta, warnings
    )
    deriv_paths = _export_mu_derivatives(req, kept, bids_root, entity_label, warnings)
    result: dict[str, Any] = {
        "saved": True,
        "path": str(out_path),
        "kept_indices": kept,
        "mu_uids": mu_uids,
        "edit_history": edit_history,
    }
    if bids_paths:
        result["bids_emg_paths"] = bids_paths
    if bids_error:
        result["bids_emg_error"] = bids_error
    if deriv_paths:
        result["bids_deriv_paths"] = deriv_paths
    if warnings:
        result["bids_warnings"] = warnings
    return result, kept, entries


def save_edit_session(
    payload: EditSessionSavePayload, session: str = DEFAULT_SESSION
) -> dict[str, Any]:
    """Save an edit session; the saved file becomes the session's baseline."""
    edit = _require_session(payload.token, session)
    with edit.lock:
        _check_open(edit)
        if edit.fsamp <= 0:
            raise MueditError("fsamp is required to save edits")
        meta = edit.meta
        signal = edit.signal
        mask = signal.artifact_mask if signal is not None else None
        bids_root = _dataset_root(edit, payload.project)
        req = _SaveRequest(
            spike_times=edit.spike_times,
            flagged=edit.flagged,
            mu_grid_index=edit.mu_grid_index,
            mu_uids=edit.mu_uids,
            edit_history=edit.history,
            artifact_times=edit.artifacts,
            fsamp=edit.fsamp,
            total_samples=edit.total_samples,
            grid_names=list(meta.get("grid_names") or []),
            parameters=dict(meta.get("parameters") or {}),
            form=payload.model_copy(
                update={"file_label": payload.file_label or meta.get("file_label")}
            ),
            pulse_trains=edit.pulse_rows,
            muscle_names=list(meta.get("muscle_names") or meta.get("muscle") or []),
            sil=[edit.sil_by_uid.get(uid) for uid in edit.mu_uids],
            rois=list(meta.get("rois") or []) or None,
            artifact_mask=mask if mask is not None and mask.size == edit.total_samples else None,
            signal=signal,
            bids_root=bids_root,
            subject=_file_subject(meta.get("file_label")) if bids_root == edit.bids_root else None,
            # Windows cannot replace a file that is memory-mapped.
            before_write=(lambda path: edit.detach(str(path))) if sys.platform == "win32" else None,
        )
        result, kept, entries = _save(req)
        edit.saved(kept, entries)
        # The saved file is the session's file from now on.
        meta.update(file_label=Path(result["path"]).name, source_path=result["path"])
        result.update(_history_tail(edit))
        if edit.log is not None:
            edit.log.close(keep=False)
        # Keyed to the saved file: reopening it is what offers these edits back after a crash.
        edit.log = EditLog.create(result["path"], payload.token)
        result.update(_per_mu(edit))
    _resize_edit_session(payload.token)
    return make_json_safe(result)


def save_edits(payload: EditSavePayload) -> dict[str, Any]:
    """Save a run: discharge times from the request, else from the stored run, and its pulse trains."""
    with _held_run_result(payload.run_result_token) as run:
        if payload.spike_times:
            spike_times = normalize_spike_times(payload.spike_times)
        elif run is not None:
            spike_times = list(run.spike_times)
        else:
            # Without it the save would write a file with no motor units and report success.
            raise MueditError(
                "The run's results are no longer on the server; run the decomposition again",
                "run_result_token",
            )
        matrix = run.pulse_trains if run is not None else None
        if matrix is not None and (
            matrix.size == 0 or matrix.shape != (len(spike_times), payload.total_samples)
        ):
            matrix = None  # the loader draws them from the discharge times

        def pulse_trains(kept: list[int]) -> FloatArray | RowSource | None:
            if matrix is None or len(kept) == matrix.shape[0]:
                return matrix
            # Read as the file is written, not indexed into a heap copy of the kept rows.
            return RowSource((len(kept), matrix.shape[1]), lambda a, b: matrix[kept[a:b]])

        mu_grid_index = _normalize_mu_grid_index(payload.mu_grid_index, len(spike_times))
        regions = [(r.start, r.end) for r in payload.artifact_regions or []]
        extras = run.extras if run is not None else RunExtras()
        sil = extras.sil if len(extras.sil) == len(spike_times) else None

        req = _SaveRequest(
            spike_times=spike_times,
            flagged=[False] * len(spike_times),
            mu_grid_index=mu_grid_index,
            mu_uids=initial_mu_uids(mu_grid_index),
            edit_history=[],
            artifact_times=[],
            fsamp=payload.fsamp,
            total_samples=payload.total_samples,
            grid_names=list(payload.grid_names or []),
            parameters=dict(payload.parameters or {}),
            form=payload,
            pulse_trains=pulse_trains,
            sil=list(sil) if sil is not None else None,
            rois=extras.rois,
            target_segments=extras.target_segments,
            artifact_mask=build_manual_artifact_mask(regions, payload.total_samples),
        )
        result, _, _ = _save(req)
        return make_json_safe(result)
