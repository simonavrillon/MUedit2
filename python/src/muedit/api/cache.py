"""The API caches, all counted against one ``MemoryBudget`` and scoped to sessions."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from muedit.api.memory import (
    DEFAULT_SESSION,
    BudgetedLRU,
    MemoryBudget,
    default_budget_bytes,
)
from muedit.editing.session import EditSession
from muedit.models import (
    FloatArray,
    IntArray,
    SignalImport,
    resident_nbytes,
)
from muedit.signal.pyramid import MinMaxPyramid
from muedit.storage import SessionStore


@dataclass
class SignalViews:
    """What the QC stage draws, kept with an upload: min/max pyramids and the grid overview.

    Every array is float32 and memory-mapped from the upload's store.
    """

    fsamp: float
    grid_names: list[str]
    grid_rows: list[tuple[int, int]]  # [first, stop) EMG rows of each grid
    emg_types: list[int]  # per grid, the bandpass the EMG is filtered with
    emg: MinMaxPyramid  # bandpassed EMG of the grid rows
    overview: FloatArray  # (ngrid, n_samples): smoothed mean |bandpassed EMG| per grid
    overview_levels: MinMaxPyramid
    aux: MinMaxPyramid

    @property
    def nbytes(self) -> int:
        return (
            self.emg.nbytes
            + resident_nbytes(self.overview)
            + self.overview_levels.nbytes
            + self.aux.nbytes
        )


@dataclass
class _UploadEntry:
    signal: SignalImport
    source_path: str | None
    views: SignalViews | None = None
    store: SessionStore | None = None  # T1 folder holding the memory-mapped arrays

    @property
    def nbytes(self) -> int:
        return self.signal.nbytes + (self.views.nbytes if self.views is not None else 0)


@dataclass
class RunExtras:
    """What a run's save writes besides the trains: per-MU SIL, windows and target segments."""

    sil: list[float] = field(default_factory=list)
    rois: list[tuple[int, int]] | None = None
    target_segments: dict[str, Any] | None = None


@dataclass
class RunResult:
    """A finished run's pulse trains and discharge times, for its explorer and its save."""

    pulse_trains: FloatArray  # float32, (n_mu, n_samples)
    store: SessionStore | None = None
    spike_times: list[IntArray] = field(default_factory=list)  # sorted int32 per MU
    extras: RunExtras = field(default_factory=RunExtras)

    @property
    def nbytes(self) -> int:
        return resident_nbytes(self.pulse_trains) + sum(int(s.nbytes) for s in self.spike_times)


@dataclass
class _EditSessionEntry:
    session: EditSession

    @property
    def nbytes(self) -> int:
        return self.session.nbytes


def _close_store(entry: _UploadEntry | RunResult) -> None:
    """Delete the T1 folder of an entry leaving its cache."""
    if entry.store is not None:
        entry.store.close()


def _close_edit_session(entry: _EditSessionEntry) -> None:
    entry.session.close()


BUDGET = MemoryBudget(default_budget_bytes())
_UPLOADS: BudgetedLRU[_UploadEntry] = BudgetedLRU(
    "uploads", BUDGET, per_session=1, on_drop=_close_store
)
_RUN_RESULTS: BudgetedLRU[RunResult] = BudgetedLRU(
    "run_results", BUDGET, per_session=1, on_drop=_close_store
)
_EDIT_SESSIONS: BudgetedLRU[_EditSessionEntry] = BudgetedLRU(
    "edit_sessions", BUDGET, per_session=1, on_drop=_close_edit_session
)


def close_session(session: str) -> None:
    """Drop everything ``session`` holds: its tab was closed."""
    BUDGET.close_session(session)


def _release_upload(session: str = DEFAULT_SESSION) -> None:
    """Drop the upload ``session`` holds, before it loads the next file."""
    _UPLOADS.release_session(session)


def _store_upload_signal(
    signal: SignalImport,
    source_path: str | None = None,
    session: str = DEFAULT_SESSION,
    store: SessionStore | None = None,
    views: SignalViews | None = None,
) -> str:
    """Keep ``signal`` (and the file it came from), with the QC stage's ``views``; return a token.

    With ``store``, the entry takes over the store ``signal`` and ``views`` were built in and
    deletes it when dropped; otherwise it keeps a copy of ``signal``. The upload is kept
    whole: a store is only ever deleted once nothing is still being written into it.
    """
    kept = signal if store is not None else signal.clone()
    entry = _UploadEntry(signal=kept, source_path=source_path, views=views, store=store)
    return _UPLOADS.pin(entry, session)


@dataclass
class HeldUpload:
    """An upload taken for one request or run: its store stays until ``release``."""

    signal: SignalImport  # a read-only view
    source_path: str | None
    store: SessionStore | None
    views: SignalViews | None
    token: str

    def release(self) -> None:
        """End the hold; a drop of the upload meanwhile deletes its store now."""
        _UPLOADS.unhold(self.token)
        if self.store is not None:
            self.store.release()


def _hold_upload(token: str | None) -> HeldUpload | None:
    """The upload for ``token``, kept from eviction and its store held until ``release``.

    Dropping the upload meanwhile (a new file, a closed tab) closes the store only after.
    """
    with BUDGET.locked():
        entry = _UPLOADS.hold(token)
        if entry is None or token is None:
            return None
        if entry.store is not None:
            entry.store.hold()
        signal = entry.signal.readonly_view()
        return HeldUpload(signal, entry.source_path, entry.store, entry.views, token)


def _store_run_result(
    pulse_trains: FloatArray,
    session: str = DEFAULT_SESSION,
    store: SessionStore | None = None,
    spike_times: list[IntArray] | None = None,
    extras: RunExtras | None = None,
) -> str:
    """Keep a finished run's pulse trains and discharge times; the run save reads them back.

    With ``store`` (the run's T1 folder holding them), the entry deletes it when dropped.
    """
    result = RunResult(pulse_trains, store, list(spike_times or []), extras or RunExtras())
    return _RUN_RESULTS.pin(result, session)


@contextlib.contextmanager
def _held_run_result(token: str | None) -> Iterator[RunResult | None]:
    """The stored run for ``token``, pulse trains read-only, its store kept until the block ends."""
    with BUDGET.locked():
        stored = _RUN_RESULTS.hold(token)
        if stored is not None and stored.store is not None:
            stored.store.hold()
    if stored is None or token is None:
        yield None
        return
    pulse_trains = stored.pulse_trains.view()
    pulse_trains.flags.writeable = False
    try:
        yield RunResult(pulse_trains, None, stored.spike_times, stored.extras)
    finally:
        _RUN_RESULTS.unhold(token)
        if stored.store is not None:
            stored.store.release()


def _store_edit_session(edit: EditSession, session: str = DEFAULT_SESSION) -> str:
    """Keep an edit session for ``session``, in place of its last, and return its token.

    Dropping a session closes it.
    """
    return _EDIT_SESSIONS.pin(_EditSessionEntry(edit), session)


def _get_edit_session(token: str | None, session: str | None = None) -> EditSession | None:
    """The edit session for ``token``; with ``session``, it moves to that tab (a reloaded page)."""
    entry = _EDIT_SESSIONS.get(token) if session is None else _EDIT_SESSIONS.move(token, session)
    return entry.session if entry is not None else None


def _resize_edit_session(token: str) -> None:
    """Recount an edit session's bytes after an edit."""
    _EDIT_SESSIONS.resize(token)


def _live_edit_logs() -> set[Path]:
    """The edit logs open sessions are writing."""
    with BUDGET.locked():
        entries = [slot.value.session for slot in _EDIT_SESSIONS.slots.values()]
    logs = [edit.log for edit in entries]  # read once: a close sets it to None
    return {log.path for log in logs if log is not None}
