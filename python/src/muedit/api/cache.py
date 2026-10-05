"""The API caches, all counted against one ``MemoryBudget`` and scoped to sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from muedit.api.memory import (
    DEFAULT_SESSION,
    BudgetedLRU,
    MemoryBudget,
    default_budget_bytes,
)
from muedit.editing.session import EditSession
from muedit.io.store import SessionStore
from muedit.models import (
    FloatArray,
    IntArray,
    SignalImport,
    resident_nbytes,
)
from muedit.signal.pyramid import MinMaxPyramid


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
    overview: FloatArray  # (n_grids, n_samples): smoothed mean |bandpassed EMG| per grid
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
class RunResult:
    """A finished run's pulse trains and discharge times, for its explorer and its save."""

    pulse_trains: FloatArray  # float32, (n_mu, n_samples)
    store: SessionStore | None = None
    spikes: list[IntArray] = field(default_factory=list)  # sorted int32 per MU

    @property
    def nbytes(self) -> int:
        return resident_nbytes(self.pulse_trains) + sum(int(s.nbytes) for s in self.spikes)


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


def _get_upload_source_path(token: str | None) -> str | None:
    """Return the on-disk path the upload for ``token`` was loaded from, if known."""
    entry = _UPLOADS.get(token)
    return entry.source_path if entry else None


def _get_upload_signal(token: str | None) -> SignalImport | None:
    """Resolve upload token to a read-only view of the stored signal."""
    entry = _UPLOADS.get(token)
    return entry.signal.readonly_view() if entry else None


@dataclass
class HeldUpload:
    """An upload taken for one request or run: its store stays until ``release``."""

    signal: SignalImport  # a read-only view
    source_path: str | None
    store: SessionStore | None
    views: SignalViews | None

    def release(self) -> None:
        """End the hold; a drop of the upload meanwhile deletes its store now."""
        if self.store is not None:
            self.store.release()


def _hold_upload(token: str | None) -> HeldUpload | None:
    """The upload for ``token``, its store held until ``release``.

    Dropping the upload meanwhile (a new file, an eviction) closes the store only after.
    """
    with BUDGET.locked():
        entry = _UPLOADS.get(token)
        if entry is None:
            return None
        if entry.store is not None:
            entry.store.hold()
        return HeldUpload(entry.signal.readonly_view(), entry.source_path, entry.store, entry.views)


def _get_signal_views(token: str | None) -> SignalViews | None:
    """The pyramids and overview of the upload for ``token`` (sealed, so read-only)."""
    entry = _UPLOADS.get(token)
    return entry.views if entry is not None else None


def _store_run_result(
    pulse_trains: FloatArray,
    session: str = DEFAULT_SESSION,
    store: SessionStore | None = None,
    spikes: list[IntArray] | None = None,
) -> str:
    """Keep a finished run's pulse trains and discharge times; the run save reads them back.

    With ``store`` (the run's T1 folder holding them), the entry deletes it when dropped.
    """
    return _RUN_RESULTS.pin(RunResult(pulse_trains, store, list(spikes or [])), session)


def _get_run_result_entry(token: str | None) -> RunResult | None:
    """The stored run for ``token``, its pulse trains read-only."""
    stored = _RUN_RESULTS.get(token)
    if stored is None:
        return None
    pulse = stored.pulse_trains.view()
    pulse.flags.writeable = False
    return RunResult(pulse, None, stored.spikes)


def _get_run_result(token: str | None) -> FloatArray | None:
    """Read-only view of a stored run's pulse trains."""
    stored = _get_run_result_entry(token)
    return stored.pulse_trains if stored is not None else None


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
    return {edit.log.path for edit in entries if edit.log is not None}
