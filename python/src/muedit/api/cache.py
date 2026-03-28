"""Cache and signal-window utilities for the FastAPI layer."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from muedit.export.io import load_bids_emg_grid
from muedit.models import SignalImport

UPLOAD_CHUNK_SIZE = 1024 * 1024
UPLOAD_CACHE_TTL_SEC = 20 * 60
UPLOAD_CACHE_MAX_ITEMS = 3
PREVIEW_MOVING_AVG_MS = 25.0
EDIT_SIGNAL_CONTEXT_TTL_SEC = 12 * 60 * 60
EDIT_SIGNAL_CONTEXT_MAX_ITEMS = 8

_CACHE_LOCK = threading.Lock()
_UPLOAD_SIGNAL_CACHE: dict[str, dict[str, Any]] = {}
_QC_SIGNAL_CACHE: dict[str, dict[str, Any]] = {}
_DECOMP_PREVIEW_BINARY_CACHE: dict[str, dict[str, Any]] = {}
_EDIT_SIGNAL_CONTEXT_CACHE: dict[str, dict[str, Any]] = {}
_EDIT_SIGNAL_LABEL_INDEX: dict[str, str] = {}

DECOMP_PREVIEW_BINARY_TTL_SEC = 10 * 60
DECOMP_PREVIEW_BINARY_MAX_ITEMS = 8


def _clone_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """Clone signal mapping through typed model to avoid shared mutable arrays."""
    return SignalImport.from_mapping(signal).clone().to_dict()


def _purge_expired_caches_locked() -> None:
    """Purge expired entries from all API caches (caller must hold lock)."""
    now = time.time()
    for token, entry in list(_UPLOAD_SIGNAL_CACHE.items()):
        if entry["expires_at"] <= now:
            _UPLOAD_SIGNAL_CACHE.pop(token, None)
            _QC_SIGNAL_CACHE.pop(token, None)
    # A QC entry can outlive its upload entry when the upload cache is
    # capacity-evicted rather than TTL-expired (eviction only removes the
    # upload entry). This second pass cleans up those orphans by expiry.
    for token, entry in list(_QC_SIGNAL_CACHE.items()):
        if entry["expires_at"] <= now:
            _QC_SIGNAL_CACHE.pop(token, None)
    for token, entry in list(_DECOMP_PREVIEW_BINARY_CACHE.items()):
        if entry["expires_at"] <= now:
            _DECOMP_PREVIEW_BINARY_CACHE.pop(token, None)
    for token, entry in list(_EDIT_SIGNAL_CONTEXT_CACHE.items()):
        if entry["expires_at"] <= now:
            _EDIT_SIGNAL_CONTEXT_CACHE.pop(token, None)
            for label, mapped in list(_EDIT_SIGNAL_LABEL_INDEX.items()):
                if mapped == token:
                    _EDIT_SIGNAL_LABEL_INDEX.pop(label, None)


def _evict_oldest_locked(cache: dict[Any, dict[str, Any]], max_items: int) -> None:
    """Trim cache to max size by evicting the oldest-expiring entries."""
    while len(cache) > max_items:
        oldest_key = min(cache.items(), key=lambda item: item[1]["expires_at"])[0]
        cache.pop(oldest_key, None)


def _store_upload_signal(signal: dict[str, Any]) -> str:
    """Store uploaded signal snapshot and return short-lived token."""
    token = uuid.uuid4().hex
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        _UPLOAD_SIGNAL_CACHE[token] = {
            "signal": _clone_signal(signal),
            "expires_at": time.time() + UPLOAD_CACHE_TTL_SEC,
        }
        _evict_oldest_locked(_UPLOAD_SIGNAL_CACHE, UPLOAD_CACHE_MAX_ITEMS)
    return token


def _get_upload_signal(token: str | None) -> dict[str, Any] | None:
    """Resolve upload token to cloned signal snapshot, refreshing TTL on hit."""
    if not token:
        return None
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        entry = _UPLOAD_SIGNAL_CACHE.get(token)
        if not entry:
            return None
        entry["expires_at"] = time.time() + UPLOAD_CACHE_TTL_SEC
        if token in _QC_SIGNAL_CACHE:
            _QC_SIGNAL_CACHE[token]["expires_at"] = time.time() + UPLOAD_CACHE_TTL_SEC
        return _clone_signal(entry["signal"])


def _store_qc_signal(
    token: str,
    data: np.ndarray,
    fsamp: float,
    grid_names: list[str],
    discard_channels: list[np.ndarray],
) -> None:
    """Store preprocessed QC arrays keyed by upload token."""
    channel_offsets: list[int] = []
    offset = 0
    for mask in discard_channels:
        channel_offsets.append(offset)
        offset += int(np.asarray(mask).size)

    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        _QC_SIGNAL_CACHE[token] = {
            "data": np.asarray(data, dtype=np.float32),
            "fsamp": float(fsamp),
            "grid_names": list(grid_names),
            "channel_offsets": channel_offsets,
            "discard_channels": [np.asarray(m, dtype=int) for m in discard_channels],
            "expires_at": time.time() + UPLOAD_CACHE_TTL_SEC,
        }
        _evict_oldest_locked(_QC_SIGNAL_CACHE, UPLOAD_CACHE_MAX_ITEMS)


def _get_qc_signal(token: str | None) -> dict[str, Any] | None:
    """Resolve QC cache entry by upload token and refresh TTL on hit."""
    if not token:
        return None
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        entry = _QC_SIGNAL_CACHE.get(token)
        if not entry:
            return None
        entry["expires_at"] = time.time() + UPLOAD_CACHE_TTL_SEC
        return {
            "data": entry["data"],
            "fsamp": entry["fsamp"],
            "grid_names": entry["grid_names"],
            "channel_offsets": entry["channel_offsets"],
            "discard_channels": entry["discard_channels"],
        }


def _envelope_bins(series: np.ndarray, bins: int) -> tuple[list[float], list[float]]:
    """Downsample series into min/max envelope bins for fast QC plotting."""
    x = np.asarray(series, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return [], []
    bins = max(1, int(bins))
    if x.size <= bins:
        vals = x.astype(float).tolist()
        return vals, vals

    step = int(np.ceil(x.size / bins))
    mins: list[float] = []
    maxs: list[float] = []
    for start in range(0, x.size, step):
        seg = x[start : start + step]
        if seg.size == 0:
            continue
        mins.append(float(np.min(seg)))
        maxs.append(float(np.max(seg)))
    return mins, maxs


def _raw_series_at_fs(series: np.ndarray, source_fs: float, target_fs: float) -> list[float]:
    """Downsample raw series by decimation from source_fs to target_fs."""
    x = np.asarray(series, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return []
    if source_fs <= 0 or target_fs <= 0:
        return x.astype(float).tolist()
    step = max(1, int(np.round(source_fs / target_fs)))
    return x[::step].astype(float).tolist()


def _moving_average_ms(series: np.ndarray, fsamp: float, window_ms: float) -> np.ndarray:
    """Compute moving average with window size specified in milliseconds."""
    x = np.asarray(series, dtype=np.float32).reshape(-1)
    if x.size == 0 or fsamp <= 0 or window_ms <= 0:
        return x
    window_samples = max(1, int(round((window_ms / 1000.0) * fsamp)))
    if window_samples == 1:
        return x
    kernel = np.ones(window_samples, dtype=np.float32) / float(window_samples)
    return np.convolve(x, kernel, mode="same").astype(np.float32)


def _load_bids_grid(
    bids_root: Path, entity_label: str, grid_index: int
) -> tuple[np.ndarray, float, np.ndarray]:
    """Load BIDS EMG grid directly from disk (no caching — always reads fresh to avoid stale data)."""
    emg, fsamp, emg_mask = load_bids_emg_grid(bids_root, entity_label, grid_index)
    return emg.copy(), float(fsamp), np.asarray(emg_mask, dtype=int).copy()


def _store_decomp_preview_binary(payload: bytes) -> str:
    """Store binary decompose-preview payload and return short-lived token."""
    token = uuid.uuid4().hex
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        _DECOMP_PREVIEW_BINARY_CACHE[token] = {
            "payload": payload,
            "expires_at": time.time() + DECOMP_PREVIEW_BINARY_TTL_SEC,
        }
        _evict_oldest_locked(
            _DECOMP_PREVIEW_BINARY_CACHE, DECOMP_PREVIEW_BINARY_MAX_ITEMS
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
        return bytes(entry["payload"])


def _store_edit_signal_context(context: dict[str, Any], file_label: str | None = None) -> str:
    """Store decomposition raw-signal context and return short-lived token."""
    token = uuid.uuid4().hex
    data = np.asarray(context.get("data"), dtype=np.float32)
    emgmask_raw = context.get("emgmask") or []
    emgmask = [np.asarray(m, dtype=int).copy() for m in emgmask_raw]
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        _EDIT_SIGNAL_CONTEXT_CACHE[token] = {
            "data": data.copy(),
            "fsamp": float(context.get("fsamp") or 0.0),
            "grid_names": list(context.get("grid_names") or []),
            "emgmask": emgmask,
            "expires_at": time.time() + EDIT_SIGNAL_CONTEXT_TTL_SEC,
        }
        label = str(file_label or "").strip()
        if label:
            _EDIT_SIGNAL_LABEL_INDEX[label] = token
        _evict_oldest_locked(_EDIT_SIGNAL_CONTEXT_CACHE, EDIT_SIGNAL_CONTEXT_MAX_ITEMS)
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
        return {
            "data": np.asarray(entry["data"], dtype=np.float32).copy(),
            "fsamp": float(entry["fsamp"]),
            "grid_names": list(entry["grid_names"]),
            "emgmask": [np.asarray(m, dtype=int).copy() for m in entry["emgmask"]],
        }


def _get_edit_signal_context_by_label(file_label: str | None) -> dict[str, Any] | None:
    """Resolve edit signal context by loaded decomposition file label."""
    label = str(file_label or "").strip()
    if not label:
        return None
    with _CACHE_LOCK:
        _purge_expired_caches_locked()
        token = _EDIT_SIGNAL_LABEL_INDEX.get(label)
    # Lock is intentionally released before calling _get_edit_signal_context:
    # that function acquires _CACHE_LOCK itself, so holding it here would
    # deadlock. The token may be evicted between the two calls, which is safe
    # because _get_edit_signal_context returns None for a missing entry.
    return _get_edit_signal_context(token)
