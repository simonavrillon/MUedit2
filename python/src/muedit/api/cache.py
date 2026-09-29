"""The API caches, all counted against one ``MemoryBudget`` and scoped to sessions."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from muedit.api.memory import (
    DEFAULT_SESSION,
    BudgetedLRU,
    MemoryBudget,
    default_budget_bytes,
)
from muedit.io.store import SessionStore
from muedit.models import (
    EditSignalContext,
    FloatArray,
    SignalImport,
    resident_nbytes,
)
from muedit.signal.pyramid import MinMaxPyramid

logger = logging.getLogger(__name__)

DECOMP_PREVIEW_BINARY_TTL_SEC = 10 * 60


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
class _RunResult:
    pulse_trains: FloatArray  # float32, (n_mu, n_samples)
    store: SessionStore | None = None

    @property
    def nbytes(self) -> int:
        return resident_nbytes(self.pulse_trains)


def _close_store(entry: _UploadEntry | _RunResult) -> None:
    """Delete the T1 folder of an entry leaving its cache."""
    if entry.store is not None:
        entry.store.close()


@dataclass
class _PreviewBlob:
    payload: bytes | memoryview

    @property
    def nbytes(self) -> int:
        return len(self.payload)


BUDGET = MemoryBudget(default_budget_bytes())
_UPLOADS: BudgetedLRU[_UploadEntry] = BudgetedLRU(
    "uploads", BUDGET, per_session=1, on_drop=_close_store
)
_DECOMP_PREVIEW_BLOBS: BudgetedLRU[_PreviewBlob] = BudgetedLRU(
    "decompose_previews", BUDGET, per_session=1, ttl_sec=DECOMP_PREVIEW_BINARY_TTL_SEC
)
_RUN_RESULTS: BudgetedLRU[_RunResult] = BudgetedLRU(
    "run_results", BUDGET, per_session=1, on_drop=_close_store
)
_EDIT_SIGNAL_CONTEXTS: BudgetedLRU[EditSignalContext] = BudgetedLRU(
    "edit_signal_contexts", BUDGET, per_session=1
)
_EDIT_SIGNAL_LABEL_INDEX: dict[str, str] = {}


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
) -> str:
    """Keep ``signal`` (and the file it came from) and return a token.

    With ``store``, the entry takes over the store ``signal`` was loaded into and
    deletes it when dropped; otherwise it keeps a copy of ``signal``.
    """
    kept = signal if store is not None else signal.clone()
    return _UPLOADS.pin(_UploadEntry(signal=kept, source_path=source_path, store=store), session)


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
    with BUDGET.lock:
        entry = _UPLOADS.get(token)
        if entry is None:
            return None
        if entry.store is not None:
            entry.store.hold()
        return HeldUpload(entry.signal.readonly_view(), entry.source_path, entry.store, entry.views)


def _store_signal_views(token: str, views: SignalViews) -> None:
    """Attach the QC stage's pyramids and overview to the upload for ``token``."""
    with BUDGET.lock:
        entry = _UPLOADS.get(token)
        if entry is None:
            logger.debug("Dropping signal views: upload token %s is no longer cached", token)
            return
        entry.views = views
        _UPLOADS.resize(token)


def _get_signal_views(token: str | None) -> SignalViews | None:
    """The pyramids and overview of the upload for ``token`` (sealed, so read-only)."""
    entry = _UPLOADS.get(token)
    return entry.views if entry is not None else None


def _store_decomp_preview_binary(
    payload: bytes | memoryview, session: str = DEFAULT_SESSION
) -> str:
    """Store binary decompose-preview payload and return short-lived token."""
    return _DECOMP_PREVIEW_BLOBS.pin(_PreviewBlob(payload), session)


def _pop_decomp_preview_binary(token: str | None) -> bytes | memoryview | None:
    """Remove and return the decompose-preview payload: the frontend fetches it once."""
    blob = _DECOMP_PREVIEW_BLOBS.pop(token)
    return blob.payload if blob is not None else None


def _store_run_result(
    pulse_trains: FloatArray,
    session: str = DEFAULT_SESSION,
    store: SessionStore | None = None,
) -> str:
    """Keep a finished run's pulse trains so the run save need not send them back.

    With ``store`` (the run's T1 folder holding them), the entry deletes it when dropped.
    """
    return _RUN_RESULTS.pin(_RunResult(pulse_trains, store), session)


def _get_run_result(token: str | None) -> FloatArray | None:
    """Read-only view of a stored run's pulse trains."""
    stored = _RUN_RESULTS.get(token)
    if stored is None:
        return None
    pulse = stored.pulse_trains.view()
    pulse.flags.writeable = False
    return pulse


def _drop_run_result(token: str | None) -> None:
    """Forget a run's pulse trains once they are saved."""
    _RUN_RESULTS.discard(token)


def _release_edit_signal_context(session: str = DEFAULT_SESSION) -> None:
    """Drop the edit context ``session`` holds, before it loads the next decomposition."""
    _EDIT_SIGNAL_CONTEXTS.release_session(session)


def _store_edit_signal_context(
    context: EditSignalContext,
    file_label: str | None = None,
    session: str = DEFAULT_SESSION,
) -> str:
    """Store a compact copy of a decomposition's raw-signal context and return a token."""
    token = _EDIT_SIGNAL_CONTEXTS.pin(context.compact_copy(), session)
    with BUDGET.lock:
        for label, mapped in list(_EDIT_SIGNAL_LABEL_INDEX.items()):
            if mapped not in _EDIT_SIGNAL_CONTEXTS.slots:
                del _EDIT_SIGNAL_LABEL_INDEX[label]
        label = str(file_label or "").strip()
        if label:
            _EDIT_SIGNAL_LABEL_INDEX[label] = token
    return token


def _get_edit_signal_context(token: str | None) -> EditSignalContext | None:
    """Resolve edit signal context token to a read-only view."""
    stored = _EDIT_SIGNAL_CONTEXTS.get(token)
    return stored.readonly_view() if stored is not None else None


def _get_edit_signal_context_by_label(file_label: str | None) -> EditSignalContext | None:
    """Resolve edit signal context by loaded decomposition file label."""
    label = str(file_label or "").strip()
    if not label:
        return None
    with BUDGET.lock:
        token = _EDIT_SIGNAL_LABEL_INDEX.get(label)
        result = _get_edit_signal_context(token)
        if result is None and token is not None:
            _EDIT_SIGNAL_LABEL_INDEX.pop(label, None)
    return result
