"""Cache and signal-window utilities for the FastAPI layer."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import TypeVar

import numpy as np

from muedit.models import EditSignalContext, FloatArray, IntArray, SignalImport

logger = logging.getLogger(__name__)

UPLOAD_CACHE_TTL_SEC = 20 * 60
UPLOAD_CACHE_MAX_ITEMS = 3
EDIT_SIGNAL_CONTEXT_TTL_SEC = 12 * 60 * 60
EDIT_SIGNAL_CONTEXT_MAX_ITEMS = 1

DECOMP_PREVIEW_BINARY_TTL_SEC = 10 * 60
DECOMP_PREVIEW_BINARY_MAX_ITEMS = 8

UPLOAD_CACHE_MAX_BYTES = 1024 * 1024 * 1024
DECOMP_PREVIEW_BINARY_MAX_BYTES = 512 * 1024 * 1024

_CACHE_LOCK = threading.Lock()


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
class _Entry:
    """Base cache entry: every entry expires and reports its resident size."""

    expires_at: float

    @property
    def nbytes(self) -> int:
        return 0


@dataclass
class _UploadEntry(_Entry):
    signal: SignalImport
    source_path: str | None
    qc: QCSignal | None = None

    @property
    def nbytes(self) -> int:
        return self.signal.nbytes + (self.qc.nbytes if self.qc is not None else 0)


@dataclass
class _PreviewBinaryEntry(_Entry):
    payload: bytes

    @property
    def nbytes(self) -> int:
        return len(self.payload)


@dataclass
class _EditContextEntry(_Entry):
    context: EditSignalContext

    @property
    def nbytes(self) -> int:
        return self.context.nbytes


_E = TypeVar("_E", bound=_Entry)

_UPLOAD_SESSION_CACHE: dict[str, _UploadEntry] = {}
_DECOMP_PREVIEW_BINARY_CACHE: dict[str, _PreviewBinaryEntry] = {}
_EDIT_SIGNAL_CONTEXT_CACHE: dict[str, _EditContextEntry] = {}
_EDIT_SIGNAL_LABEL_INDEX: dict[str, str] = {}


def _purge_expired_caches_locked() -> None:
    """Purge expired entries from all API caches (caller must hold lock)."""
    now = time.time()
    for token, entry in list(_UPLOAD_SESSION_CACHE.items()):
        if entry.expires_at <= now:
            _UPLOAD_SESSION_CACHE.pop(token, None)
    for token, preview in list(_DECOMP_PREVIEW_BINARY_CACHE.items()):
        if preview.expires_at <= now:
            _DECOMP_PREVIEW_BINARY_CACHE.pop(token, None)
    expired_edit = {
        token for token, entry in _EDIT_SIGNAL_CONTEXT_CACHE.items() if entry.expires_at <= now
    }
    for token in expired_edit:
        _EDIT_SIGNAL_CONTEXT_CACHE.pop(token, None)
    if expired_edit:
        for label, mapped in list(_EDIT_SIGNAL_LABEL_INDEX.items()):
            if mapped in expired_edit:
                _EDIT_SIGNAL_LABEL_INDEX.pop(label, None)


def _evict_to_budget_locked(
    cache: dict[str, _E],
    max_items: int,
    max_bytes: int | None = None,
    protect: str | None = None,
) -> None:
    """Trim cache to its item and byte budget, evicting oldest-expiring first."""
    total = sum(entry.nbytes for entry in cache.values())
    while len(cache) > max_items or (max_bytes is not None and total > max_bytes):
        candidates = [key for key in cache if key != protect]
        if not candidates:
            return  # only the protected entry is left; keep it whatever its size
        oldest_key = min(candidates, key=lambda key: cache[key].expires_at)
        evicted = cache.pop(oldest_key)
        total -= evicted.nbytes
        logger.debug(
            "Evicted cache entry %s (%.1f MB); %d entries / %.1f MB retained",
            oldest_key,
            evicted.nbytes / 1e6,
            len(cache),
            total / 1e6,
        )


def _store_upload_signal(signal: SignalImport, source_path: str | None = None) -> str:
    """Store a copy of ``signal`` (and the file it came from) and return a token."""
    token = uuid.uuid4().hex
    entry = _UploadEntry(
        expires_at=time.time() + UPLOAD_CACHE_TTL_SEC,
        signal=signal.clone(),
        source_path=source_path,
    )
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        _UPLOAD_SESSION_CACHE[token] = entry
        _evict_to_budget_locked(
            _UPLOAD_SESSION_CACHE,
            UPLOAD_CACHE_MAX_ITEMS,
            UPLOAD_CACHE_MAX_BYTES,
            protect=token,
        )
    return token


def _get_upload_source_path(token: str | None) -> str | None:
    """Return the on-disk path the upload for ``token`` was loaded from, if known."""
    if not token:
        return None
    with _CACHE_LOCK:
        entry = _UPLOAD_SESSION_CACHE.get(token)
        return entry.source_path if entry else None


def _get_upload_signal(token: str | None) -> SignalImport | None:
    """Resolve upload token to a copy of the stored signal, refreshing TTL on hit."""
    if not token:
        return None
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        entry = _UPLOAD_SESSION_CACHE.get(token)
        if not entry:
            return None
        entry.expires_at = time.time() + UPLOAD_CACHE_TTL_SEC
        stored = entry.signal
    return stored.clone()


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
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        entry = _UPLOAD_SESSION_CACHE.get(token)
        if entry is None:
            logger.debug("Dropping QC data: upload token %s is no longer cached", token)
            return
        entry.qc = qc
        entry.expires_at = time.time() + UPLOAD_CACHE_TTL_SEC
        _evict_to_budget_locked(
            _UPLOAD_SESSION_CACHE,
            UPLOAD_CACHE_MAX_ITEMS,
            UPLOAD_CACHE_MAX_BYTES,
            protect=token,
        )


def _get_qc_signal(token: str | None) -> QCSignal | None:
    """Resolve QC arrays by upload token and refresh session TTL on hit.

    ``data`` is a read-only view of the cached array; everything else is a copy.
    """
    if not token:
        return None
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        entry = _UPLOAD_SESSION_CACHE.get(token)
        qc = entry.qc if entry is not None else None
        if entry is None or qc is None:
            return None
        entry.expires_at = time.time() + UPLOAD_CACHE_TTL_SEC
    data_view = qc.data.view()
    data_view.flags.writeable = False
    return QCSignal(
        data=data_view,
        fsamp=qc.fsamp,
        grid_names=list(qc.grid_names),
        channel_offsets=list(qc.channel_offsets),
        discard_channels=[m.copy() for m in qc.discard_channels],
    )


def _store_decomp_preview_binary(payload: bytes) -> str:
    """Store binary decompose-preview payload and return short-lived token."""
    token = uuid.uuid4().hex
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        _DECOMP_PREVIEW_BINARY_CACHE[token] = _PreviewBinaryEntry(
            expires_at=time.time() + DECOMP_PREVIEW_BINARY_TTL_SEC,
            payload=payload,
        )
        _evict_to_budget_locked(
            _DECOMP_PREVIEW_BINARY_CACHE,
            DECOMP_PREVIEW_BINARY_MAX_ITEMS,
            DECOMP_PREVIEW_BINARY_MAX_BYTES,
            protect=token,
        )
    return token


def _get_decomp_preview_binary(token: str | None) -> bytes | None:
    """Resolve decompose-preview binary payload by token, refreshing TTL on hit."""
    if not token:
        return None
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        entry = _DECOMP_PREVIEW_BINARY_CACHE.get(token)
        if not entry:
            return None
        entry.expires_at = time.time() + DECOMP_PREVIEW_BINARY_TTL_SEC
        return entry.payload


def _store_edit_signal_context(context: EditSignalContext, file_label: str | None = None) -> str:
    """Store a compact copy of a decomposition's raw-signal context and return a token."""
    token = uuid.uuid4().hex
    entry = _EditContextEntry(
        expires_at=time.time() + EDIT_SIGNAL_CONTEXT_TTL_SEC,
        context=context.compact_copy(),
    )
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        _EDIT_SIGNAL_CONTEXT_CACHE[token] = entry
        label = str(file_label or "").strip()
        if label:
            _EDIT_SIGNAL_LABEL_INDEX[label] = token
        _evict_to_budget_locked(
            _EDIT_SIGNAL_CONTEXT_CACHE,
            EDIT_SIGNAL_CONTEXT_MAX_ITEMS,
            protect=token,
        )
    return token


def _get_edit_signal_context(token: str | None) -> EditSignalContext | None:
    """Resolve edit signal context token to a copy, refreshing TTL on hit."""
    if not token:
        return None
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        entry = _EDIT_SIGNAL_CONTEXT_CACHE.get(token)
        if not entry:
            return None
        entry.expires_at = time.time() + EDIT_SIGNAL_CONTEXT_TTL_SEC
        stored = entry.context
    return stored.compact_copy()


def _get_edit_signal_context_by_label(file_label: str | None) -> EditSignalContext | None:
    """Resolve edit signal context by loaded decomposition file label."""
    label = str(file_label or "").strip()
    if not label:
        return None
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        token = _EDIT_SIGNAL_LABEL_INDEX.get(label)
    result = _get_edit_signal_context(token)
    if result is None and token is not None:
        with _CACHE_LOCK:
            if _EDIT_SIGNAL_LABEL_INDEX.get(label) == token:
                _EDIT_SIGNAL_LABEL_INDEX.pop(label, None)
    return result
