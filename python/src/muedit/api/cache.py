"""The API caches, all counted against one ``MemoryBudget`` and scoped to sessions."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from muedit.api.memory import (
    DEFAULT_SESSION,
    BudgetedLRU,
    MemoryBudget,
    default_budget_bytes,
)
from muedit.models import EditSignalContext, FloatArray, IntArray, SignalImport

logger = logging.getLogger(__name__)

DECOMP_PREVIEW_BINARY_TTL_SEC = 10 * 60


@dataclass
class QCSignal:
    """Filtered preview EMG kept with an upload for channel views and auto-QC."""

    data: FloatArray  # float32, (n_channels, n_samples)
    fsamp: float
    grid_names: list[str]
    channel_offsets: list[int]  # first row of each grid in ``data``
    discard_channels: list[IntArray]  # per grid, 1 = discarded channel

    @property
    def nbytes(self) -> int:
        return int(self.data.nbytes) + sum(int(m.nbytes) for m in self.discard_channels)


@dataclass
class _UploadEntry:
    signal: SignalImport
    source_path: str | None
    qc: QCSignal | None = None

    @property
    def nbytes(self) -> int:
        return self.signal.nbytes + (self.qc.nbytes if self.qc is not None else 0)


@dataclass
class _PreviewBlob:
    payload: bytes | memoryview

    @property
    def nbytes(self) -> int:
        return len(self.payload)


BUDGET = MemoryBudget(default_budget_bytes())
_UPLOADS: BudgetedLRU[_UploadEntry] = BudgetedLRU("uploads", BUDGET, per_session=1)
_DECOMP_PREVIEW_BLOBS: BudgetedLRU[_PreviewBlob] = BudgetedLRU(
    "decompose_previews", BUDGET, per_session=1, ttl_sec=DECOMP_PREVIEW_BINARY_TTL_SEC
)
_RUN_RESULTS: BudgetedLRU[FloatArray] = BudgetedLRU("run_results", BUDGET, per_session=1)
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
) -> str:
    """Store a copy of ``signal`` (and the file it came from) and return a token."""
    return _UPLOADS.pin(_UploadEntry(signal=signal.clone(), source_path=source_path), session)


def _get_upload_source_path(token: str | None) -> str | None:
    """Return the on-disk path the upload for ``token`` was loaded from, if known."""
    entry = _UPLOADS.get(token)
    return entry.source_path if entry else None


def _get_upload_signal(token: str | None) -> SignalImport | None:
    """Resolve upload token to a read-only view of the stored signal."""
    entry = _UPLOADS.get(token)
    return entry.signal.readonly_view() if entry else None


def _store_qc_signal(
    token: str,
    data: FloatArray,
    fsamp: float,
    grid_names: list[str],
    discard_channels: list[IntArray],
) -> None:
    """Attach preprocessed QC arrays to the upload session for ``token``."""
    channel_offsets: list[int] = []
    offset = 0
    for mask in discard_channels:
        channel_offsets.append(offset)
        offset += int(np.asarray(mask).size)

    qc = QCSignal(
        data=np.array(data, dtype=np.float32),
        fsamp=float(fsamp),
        grid_names=list(grid_names),
        channel_offsets=channel_offsets,
        discard_channels=[np.array(m, dtype=int) for m in discard_channels],
    )
    with BUDGET.lock:
        entry = _UPLOADS.get(token)
        if entry is None:
            logger.debug("Dropping QC data: upload token %s is no longer cached", token)
            return
        entry.qc = qc
        _UPLOADS.resize(token)


def _get_qc_signal(token: str | None) -> QCSignal | None:
    """Resolve QC arrays by upload token.

    ``data`` is a read-only view of the cached array; everything else is a copy.
    """
    entry = _UPLOADS.get(token)
    qc = entry.qc if entry is not None else None
    if qc is None:
        return None
    data_view = qc.data.view()
    data_view.flags.writeable = False
    return QCSignal(
        data=data_view,
        fsamp=qc.fsamp,
        grid_names=list(qc.grid_names),
        channel_offsets=list(qc.channel_offsets),
        discard_channels=[m.copy() for m in qc.discard_channels],
    )


def _store_decomp_preview_binary(
    payload: bytes | memoryview, session: str = DEFAULT_SESSION
) -> str:
    """Store binary decompose-preview payload and return short-lived token."""
    return _DECOMP_PREVIEW_BLOBS.pin(_PreviewBlob(payload), session)


def _pop_decomp_preview_binary(token: str | None) -> bytes | memoryview | None:
    """Remove and return the decompose-preview payload: the frontend fetches it once."""
    blob = _DECOMP_PREVIEW_BLOBS.pop(token)
    return blob.payload if blob is not None else None


def _store_run_result(pulse_trains: FloatArray, session: str = DEFAULT_SESSION) -> str:
    """Keep a finished run's pulse trains so the run save need not send them back."""
    return _RUN_RESULTS.pin(pulse_trains, session)


def _get_run_result(token: str | None) -> FloatArray | None:
    """Read-only view of a stored run's pulse trains."""
    stored = _RUN_RESULTS.get(token)
    if stored is None:
        return None
    pulse = stored.view()
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
