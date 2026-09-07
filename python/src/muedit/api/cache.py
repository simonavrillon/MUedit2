"""Cache and signal-window utilities for the FastAPI layer."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

import numpy as np

from muedit.decomp.io import LOADER_BIDS_META_KEYS
from muedit.models import SignalImport

logger = logging.getLogger(__name__)

UPLOAD_CHUNK_SIZE = 1024 * 1024
UPLOAD_CACHE_TTL_SEC = 20 * 60
UPLOAD_CACHE_MAX_ITEMS = 3
EDIT_SIGNAL_CONTEXT_TTL_SEC = 12 * 60 * 60
EDIT_SIGNAL_CONTEXT_MAX_ITEMS = 1

DECOMP_PREVIEW_BINARY_TTL_SEC = 10 * 60
DECOMP_PREVIEW_BINARY_MAX_ITEMS = 8

UPLOAD_CACHE_MAX_BYTES = 1024 * 1024 * 1024
DECOMP_PREVIEW_BINARY_MAX_BYTES = 512 * 1024 * 1024

_CACHE_LOCK = threading.Lock()
_UPLOAD_SESSION_CACHE: dict[str, dict[str, Any]] = {}
_DECOMP_PREVIEW_BINARY_CACHE: dict[str, dict[str, Any]] = {}
_EDIT_SIGNAL_CONTEXT_CACHE: dict[str, dict[str, Any]] = {}
_EDIT_SIGNAL_LABEL_INDEX: dict[str, str] = {}


def _clone_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """Clone signal mapping through typed model to avoid shared mutable arrays."""
    return SignalImport.from_mapping(signal).to_dict()


def _purge_expired_caches_locked() -> None:
    """Purge expired entries from all API caches (caller must hold lock)."""
    now = time.time()
    for token, entry in list(_UPLOAD_SESSION_CACHE.items()):
        if entry["expires_at"] <= now:
            _UPLOAD_SESSION_CACHE.pop(token, None)
    for token, entry in list(_DECOMP_PREVIEW_BINARY_CACHE.items()):
        if entry["expires_at"] <= now:
            _DECOMP_PREVIEW_BINARY_CACHE.pop(token, None)
    expired_edit = {
        token
        for token, entry in _EDIT_SIGNAL_CONTEXT_CACHE.items()
        if entry["expires_at"] <= now
    }
    for token in expired_edit:
        _EDIT_SIGNAL_CONTEXT_CACHE.pop(token, None)
    if expired_edit:
        for label, mapped in list(_EDIT_SIGNAL_LABEL_INDEX.items()):
            if mapped in expired_edit:
                _EDIT_SIGNAL_LABEL_INDEX.pop(label, None)


def _array_nbytes(value: Any) -> int:
    """Resident size of a NumPy array, or 0 for anything else."""
    return int(value.nbytes) if isinstance(value, np.ndarray) else 0


def _signal_nbytes(signal: dict[str, Any] | None) -> int:
    """Resident size of the array payload of a cached signal mapping."""
    if not signal:
        return 0
    return _array_nbytes(signal.get("data")) + _array_nbytes(signal.get("auxiliary"))


def _qc_nbytes(qc: dict[str, Any] | None) -> int:
    """Resident size of the array payload of a cached QC mapping."""
    if not qc:
        return 0
    masks = qc.get("discard_channels") or []
    return _array_nbytes(qc.get("data")) + sum(_array_nbytes(m) for m in masks)


def _evict_to_budget_locked(
    cache: dict[Any, dict[str, Any]],
    max_items: int,
    max_bytes: int | None = None,
    protect: Any = None,
) -> None:
    """Trim cache to its item and byte budget, evicting oldest-expiring first."""
    total = sum(int(entry.get("nbytes", 0)) for entry in cache.values())
    while len(cache) > max_items or (max_bytes is not None and total > max_bytes):
        candidates = [key for key in cache if key != protect]
        if not candidates:
            return  # only the protected entry is left; keep it whatever its size
        oldest_key = min(candidates, key=lambda key: cache[key]["expires_at"])
        evicted = cache.pop(oldest_key, None)
        if evicted is not None:
            total -= int(evicted.get("nbytes", 0))
            logger.debug(
                "Evicted cache entry %s (%.1f MB); %d entries / %.1f MB retained",
                oldest_key,
                int(evicted.get("nbytes", 0)) / 1e6,
                len(cache),
                total / 1e6,
            )


def _store_upload_signal(signal: dict[str, Any]) -> str:
    """Store uploaded signal snapshot and return short-lived token."""
    token = uuid.uuid4().hex
    cloned = _clone_signal(signal)
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        _UPLOAD_SESSION_CACHE[token] = {
            "signal": cloned,
            "qc": None,
            "nbytes": _signal_nbytes(cloned),
            "expires_at": time.time() + UPLOAD_CACHE_TTL_SEC,
        }
        _evict_to_budget_locked(
            _UPLOAD_SESSION_CACHE,
            UPLOAD_CACHE_MAX_ITEMS,
            UPLOAD_CACHE_MAX_BYTES,
            protect=token,
        )
    return token


def _get_upload_signal(token: str | None) -> dict[str, Any] | None:
    """Resolve upload token to cloned signal snapshot, refreshing TTL on hit."""
    if not token:
        return None
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        entry = _UPLOAD_SESSION_CACHE.get(token)
        if not entry:
            return None
        entry["expires_at"] = time.time() + UPLOAD_CACHE_TTL_SEC
        stored = entry["signal"]
    return _clone_signal(stored)


def _store_qc_signal(
    token: str,
    data: np.ndarray,
    fsamp: float,
    grid_names: list[str],
    discard_channels: list[np.ndarray],
) -> None:
    """Attach preprocessed QC arrays to the upload session for ``token``."""
    channel_offsets: list[int] = []
    offset = 0
    for mask in discard_channels:
        channel_offsets.append(offset)
        offset += int(np.asarray(mask).size)

    qc: dict[str, Any] = {
        "data": np.array(data, dtype=np.float32),
        "fsamp": float(fsamp),
        "grid_names": list(grid_names),
        "channel_offsets": channel_offsets,
        "discard_channels": [np.array(m, dtype=int) for m in discard_channels],
    }
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        entry = _UPLOAD_SESSION_CACHE.get(token)
        if entry is None:
            logger.debug("Dropping QC data: upload token %s is no longer cached", token)
            return
        entry["qc"] = qc
        entry["nbytes"] = _signal_nbytes(entry["signal"]) + _qc_nbytes(qc)
        entry["expires_at"] = time.time() + UPLOAD_CACHE_TTL_SEC
        _evict_to_budget_locked(
            _UPLOAD_SESSION_CACHE,
            UPLOAD_CACHE_MAX_ITEMS,
            UPLOAD_CACHE_MAX_BYTES,
            protect=token,
        )


def _get_qc_signal(token: str | None) -> dict[str, Any] | None:
    """Resolve QC arrays by upload token and refresh session TTL on hit."""
    if not token:
        return None
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        entry = _UPLOAD_SESSION_CACHE.get(token)
        qc = entry.get("qc") if entry is not None else None
        if entry is None or qc is None:
            return None
        entry["expires_at"] = time.time() + UPLOAD_CACHE_TTL_SEC
    data_view = qc["data"].view()
    data_view.flags.writeable = False
    return {
        "data": data_view,
        "fsamp": qc["fsamp"],
        "grid_names": list(qc["grid_names"]),
        "channel_offsets": list(qc["channel_offsets"]),
        "discard_channels": [np.asarray(m, dtype=int).copy() for m in qc["discard_channels"]],
    }


def _store_decomp_preview_binary(payload: bytes) -> str:
    """Store binary decompose-preview payload and return short-lived token."""
    token = uuid.uuid4().hex
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        _DECOMP_PREVIEW_BINARY_CACHE[token] = {
            "payload": payload,
            "nbytes": len(payload),
            "expires_at": time.time() + DECOMP_PREVIEW_BINARY_TTL_SEC,
        }
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
        entry["expires_at"] = time.time() + DECOMP_PREVIEW_BINARY_TTL_SEC
        payload = entry["payload"]
    return payload


def _store_edit_signal_context(context: dict[str, Any], file_label: str | None = None) -> str:
    """Store decomposition raw-signal context and return short-lived token."""
    token = uuid.uuid4().hex
    data = np.asarray(context.get("data"), dtype=np.float32)
    emgmask_raw = context.get("emgmask") or []
    emgmask = [np.asarray(m, dtype=int).copy() for m in emgmask_raw]
    coordinates_raw = context.get("coordinates") or []
    coordinates = [np.asarray(c, dtype=float).copy() for c in coordinates_raw]
    ied_raw = context.get("ied")
    ied = list(ied_raw) if ied_raw is not None else None
    aux_raw = context.get("aux_data")
    aux_data = np.asarray(aux_raw, dtype=np.float32).copy() if isinstance(aux_raw, np.ndarray) and aux_raw.size > 0 else None
    aux_names = list(context.get("aux_names") or [])
    cache_entry: dict[str, Any] = {
        "data": data.copy(),
        "fsamp": float(context.get("fsamp") or 0.0),
        "grid_names": list(context.get("grid_names") or []),
        "emgmask": emgmask,
        "coordinates": coordinates,
        "ied": ied,
        "aux_data": aux_data,
        "aux_names": aux_names,
        "nbytes": (
            _array_nbytes(data)
            + _array_nbytes(aux_data)
            + sum(_array_nbytes(m) for m in emgmask)
            + sum(_array_nbytes(c) for c in coordinates)
        ),
        "expires_at": time.time() + EDIT_SIGNAL_CONTEXT_TTL_SEC,
    }
    # Loader-provided BIDS metadata fields (single source: LOADER_BIDS_META_KEYS).
    for key in LOADER_BIDS_META_KEYS:
        cache_entry[key] = context.get(key)
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        _EDIT_SIGNAL_CONTEXT_CACHE[token] = cache_entry
        label = str(file_label or "").strip()
        if label:
            _EDIT_SIGNAL_LABEL_INDEX[label] = token
        _evict_to_budget_locked(
            _EDIT_SIGNAL_CONTEXT_CACHE,
            EDIT_SIGNAL_CONTEXT_MAX_ITEMS,
            protect=token,
        )
    return token


def _get_edit_signal_context(token: str | None) -> dict[str, Any] | None:
    """Resolve edit signal context token and refresh TTL on hit."""
    if not token:
        return None
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        entry = _EDIT_SIGNAL_CONTEXT_CACHE.get(token)
        if not entry:
            return None
        entry["expires_at"] = time.time() + EDIT_SIGNAL_CONTEXT_TTL_SEC
    aux = entry.get("aux_data")
    result: dict[str, Any] = {
        "data": np.asarray(entry["data"], dtype=np.float32).copy(),
        "fsamp": float(entry["fsamp"]),
        "grid_names": list(entry["grid_names"]),
        "emgmask": [np.asarray(m, dtype=int).copy() for m in entry["emgmask"]],
        "coordinates": [np.asarray(c, dtype=float).copy() for c in (entry.get("coordinates") or [])],
        "ied": list(entry["ied"]) if entry.get("ied") is not None else None,
        "aux_data": np.asarray(aux, dtype=np.float32).copy() if isinstance(aux, np.ndarray) else None,
        "aux_names": list(entry.get("aux_names") or []),
    }
    for key in LOADER_BIDS_META_KEYS:
        result[key] = entry.get(key)
    return result


def _get_edit_signal_context_by_label(file_label: str | None) -> dict[str, Any] | None:
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
