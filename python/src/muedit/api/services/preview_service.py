"""Application services for preview and QC windows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException

from muedit.api import config
from muedit.api.cache import (
    _hold_upload,
    _release_upload,
    _store_signal_views,
    _store_upload_signal,
)
from muedit.api.common import (
    make_json_safe,
    parse_entity_label,
    require_existing_path,
)
from muedit.api.memory import DEFAULT_SESSION
from muedit.api.schemas import QcAutoPayload
from muedit.api.services.bids_helpers import (
    _infer_bids_root_from_decomp_path,
    read_bids_sidecar_meta,
)
from muedit.api.services.series_service import (
    bandpassed_rows,
    build_signal_views,
    grid_emg_type,
    grid_rows,
)
from muedit.io.factory import get_loader, load_signal
from muedit.io.store import ArrayStore, RamStore, SessionStore
from muedit.models import FloatArray, SignalImport
from muedit.signal.artifact_mask import mask_to_intervals
from muedit.signal.filters import FILTER_BLOCK_ROWS
from muedit.signal.grid import format_hdemg_signal
from muedit.signal.qc_pipeline import run_auto_qc


def _build_preview_core(filepath: str, session: str = DEFAULT_SESSION) -> dict[str, Any]:
    """Load a signal into a session store, build the series the QC stage draws, and the preview."""
    _release_upload(session)
    store = SessionStore.create("upload")
    try:
        signal = load_signal(filepath, store=store)
    except BaseException:
        store.close()
        raise
    upload_token = _store_upload_signal(signal, source_path=filepath, session=session, store=store)

    coordinates, _, _, emg_type = format_hdemg_signal(signal.gridname)
    views, channel_means = build_signal_views(
        signal, store, [c.shape[0] for c in coordinates], emg_type
    )
    _store_signal_views(upload_token, views)

    return make_json_safe(
        {
            "upload_token": upload_token,
            "grid_names": signal.gridname,
            "total_samples": int(signal.data.shape[1]),
            "fsamp": signal.fsamp,
            "channel_means": [means.tolist() for means in channel_means],
            "coordinates": [coords.tolist() for coords in coordinates],
            "metadata": signal.metadata,
            "muscle": signal.muscle,
            "auxiliary_names": signal.auxiliaryname,
        }
    )


def _decomp_artifact_error(field: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "field": field,
            "reason": "This MAT file is a decomposition artifact; load it in edit mode.",
        },
    )


def build_preview_from_path(filepath: str, session: str = DEFAULT_SESSION) -> dict[str, Any]:
    """Build preview payload from a file path already available on disk."""
    require_existing_path(filepath)
    try:
        get_loader(filepath)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"field": "path", "reason": str(exc)}) from exc
    try:
        result = _build_preview_core(filepath, session)
    except (OSError, ValueError) as exc:
        if "contains decomposition fields" in str(exc):
            raise _decomp_artifact_error("path") from exc
        raise

    # Best-effort: enrich with participant and hardware info from BIDS sidecars.
    try:
        bids_root = _infer_bids_root_from_decomp_path(filepath)
        if bids_root is not None:
            result["project"] = config.project_of(bids_root)
            entity_label = parse_entity_label(Path(filepath).name)
            result.update(read_bids_sidecar_meta(bids_root, entity_label))
    except Exception:  # noqa: BLE001, S110
        pass  # best-effort; never block the preview on sidecar errors

    return result


def _mask_to_regions(mask: np.ndarray | None) -> list[list[int]]:
    """Convert a boolean sample mask into contiguous ``[start, end)`` ranges."""
    return mask_to_intervals(mask).tolist()


def _bandpassed_grids(
    signal: SignalImport, grid_counts: list[int], emg_types: list[int], store: ArrayStore
) -> FloatArray:
    """The grid channels bandpassed into one float32 array of ``store``, a few rows at a time."""
    n_rows = sum(grid_counts)
    out = store.allocate("qc-auto", (n_rows, signal.data.shape[1]), np.float32)
    for grid, (first, stop) in enumerate(grid_rows(grid_counts, n_rows)):
        for lo in range(first, stop, FILTER_BLOCK_ROWS):
            hi = min(lo + FILTER_BLOCK_ROWS, stop)
            out[lo:hi] = bandpassed_rows(signal, lo, hi, grid_emg_type(emg_types, grid))
    return store.seal(out)


def run_auto_qc_on_token(payload: QcAutoPayload) -> dict[str, Any]:
    """Run the automatic QC pipeline over the upload's grid channels, bandpassed for the run."""
    held = _hold_upload(payload.upload_token)
    if held is None:
        raise HTTPException(
            status_code=400,
            detail={
                "field": "upload_token",
                "reason": "Missing or expired upload; request /api/v1/preview-by-path first",
            },
        )
    try:
        return _auto_qc(held.signal, held.store if held.store is not None else RamStore())
    finally:
        held.release()


def _auto_qc(signal: SignalImport, store: ArrayStore) -> dict[str, Any]:
    coordinates, _, _, emg_type = format_hdemg_signal(signal.gridname)
    grid_channel_counts = [int(c.shape[0]) for c in coordinates]
    total_declared = sum(grid_channel_counts)
    n_rows, n_samples = signal.data.shape
    if total_declared > n_rows:
        raise HTTPException(
            status_code=400,
            detail={
                "field": "upload_token",
                "reason": (
                    f"Grid catalogue declares {total_declared} channels but the cached "
                    f"signal has {n_rows}"
                ),
            },
        )

    data = _bandpassed_grids(signal, grid_channel_counts, emg_type, store)
    try:
        result = run_auto_qc(
            data,
            signal.fsamp,
            grid_channel_counts,
            grid_coordinates=coordinates,
            store=store,
        )
    finally:
        store.discard(data)
    return make_json_safe(
        {
            "bad_channels_per_grid": [
                np.asarray(m, dtype=int).tolist() for m in result.bad_channel_masks
            ],
            "artifact_regions": _mask_to_regions(result.artifact_mask),
            "artifact_samples": int(np.asarray(result.artifact_mask).sum()),
            "total_samples": int(n_samples),
            "fsamp": signal.fsamp,
            "grid_names": signal.gridname,
        }
    )
