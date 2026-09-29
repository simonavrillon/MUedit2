"""Application services for decomposition execution."""

from __future__ import annotations

import json
import queue
import tempfile
import threading
import traceback
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException
from fastapi.responses import Response

from muedit.api.binary import FRAME_FORMAT, FRAME_MEDIA_TYPE, pack_frame
from muedit.api.cache import (
    _get_upload_signal,
    _get_upload_source_path,
    _pop_decomp_preview_binary,
    _store_decomp_preview_binary,
    _store_run_result,
)
from muedit.api.common import (
    build_params,
    make_json_safe,
    parse_discard_channels,
    parse_json_object,
    parse_rois,
    summarize_result,
)
from muedit.api.memory import DEFAULT_SESSION
from muedit.decomp.pipeline import run_decomposition
from muedit.io.store import SessionStore
from muedit.models import SignalImport

PREVIEW_PULSE_KEYS = ("pulse_trains_full", "pulse_trains_all")


def _as_matrix(value: Any) -> np.ndarray:
    """Coerce a preview pulse payload into a 2-D matrix without copying an ndarray."""
    matrix = np.asarray(value if value is not None else [])
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1) if matrix.size else np.zeros((0, 0))
    if matrix.ndim != 2:
        matrix = np.zeros((0, 0))
    return matrix


def _encode_decompose_preview(preview: dict[str, Any]) -> memoryview:
    """Encode the run preview as a MUB1 frame with float32 pulse matrices."""
    meta = {k: v for k, v in preview.items() if k not in PREVIEW_PULSE_KEYS}
    arrays = {key: (_as_matrix(preview.get(key)), "f4") for key in PREVIEW_PULSE_KEYS}
    return pack_frame(meta, arrays)


def fetch_decompose_preview_binary(token: str) -> Response:
    """Return the preview frame for ``token`` and drop it from the cache."""
    payload = _pop_decomp_preview_binary(token)
    if payload is None:
        raise HTTPException(status_code=404, detail="Preview binary token not found or expired")
    return Response(
        content=payload,
        media_type=FRAME_MEDIA_TYPE,
        headers={"x-muedit-format": FRAME_FORMAT},
    )


def decomposition_event_stream(
    run_path: str,
    params_raw: str | None,
    duration: float | None,
    persist_output: bool,
    roi: tuple[int, int] | None = None,
    rois: list[tuple[int, int]] | None = None,
    discard_channels: list[list[int]] | None = None,
    bids_root: str | None = None,
    bids_entities: dict | None = None,
    bids_metadata: dict | None = None,
    include_full_preview: bool = False,
    preloaded_signal: SignalImport | None = None,
    binary_preview: bool = False,
    artifact_regions: list[tuple[int, int]] | None = None,
    session: str = DEFAULT_SESSION,
) -> Iterator[str]:
    """Yield NDJSON progress events while decomposition executes in background thread."""
    q: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def progress(stage: str, payload: dict[str, Any]) -> None:
        """Normalize and queue progress callback payload from the pipeline."""
        if stage == "done":
            return
        event = {"stage": stage}
        event.update({k: make_json_safe(v) for k, v in payload.items()})
        q.put(event)

    def worker() -> None:
        """Execute decomposition and push terminal success/error events."""
        emitted = False
        store: SessionStore | None = None
        store_kept = False
        try:
            store = SessionStore.create("run")
            param_obj = build_params(params_raw)
            result, save_path = run_decomposition(
                run_path,
                duration=duration,
                manual_roi=False,
                params=param_obj,
                save_npz=persist_output or bids_root is not None,
                progress_cb=progress,
                roi=roi,
                rois=rois,
                discard_overrides=discard_channels,
                bids_root=bids_root,
                bids_entities=bids_entities,
                bids_metadata=bids_metadata,
                include_full_preview=include_full_preview,
                preloaded_signal=preloaded_signal,
                artifact_regions=artifact_regions,
                store=store,
            )
            preview_raw = result.get("preview", {})
            if binary_preview:
                frame = _encode_decompose_preview(preview_raw)
                preview_payload = make_json_safe(
                    {k: v for k, v in preview_raw.items() if k not in PREVIEW_PULSE_KEYS}
                )
                preview_payload["preview_binary_token"] = _store_decomp_preview_binary(
                    frame, session
                )
            else:
                preview_payload = make_json_safe(preview_raw)
            # The run save reads the pulse trains from the run's store, not from the frame.
            pulse_full = _as_matrix(preview_raw.get("pulse_trains_full")).astype(
                np.float32, copy=False
            )
            if pulse_full.size:
                preview_payload["run_result_token"] = _store_run_result(pulse_full, session, store)
                store_kept = True
            q.put(
                {
                    "stage": "done",
                    "summary": make_json_safe(summarize_result(result, save_path, persist_output)),
                    "preview": preview_payload,
                    "pct": 100,
                    "message": "Complete",
                }
            )
            emitted = True
        except Exception as exc:  # noqa: BLE001
            q.put(
                {
                    "stage": "error",
                    "pct": 100,
                    "message": "Decomposition failed",
                    "detail": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
            emitted = True
        finally:
            if store is not None and not store_kept:
                store.close()
            if not emitted:
                q.put(
                    {
                        "stage": "error",
                        "pct": 100,
                        "message": "Decomposition worker terminated unexpectedly",
                    }
                )
            q.put(None)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    while True:
        event = q.get()
        if event is None:
            break
        safe_event = make_json_safe(event)
        yield json.dumps(safe_event) + "\n"


def resolve_decompose_input(
    upload_token: str | None,
) -> tuple[str, SignalImport]:
    """Resolve an upload token into ``(run_path, preloaded_signal)``."""
    preloaded_signal = _get_upload_signal(upload_token)
    if preloaded_signal is None:
        raise HTTPException(
            status_code=400,
            detail={
                "field": "upload_token",
                "reason": "Token expired or missing; reload the file via /preview-by-path",
            },
        )
    run_path = _get_upload_source_path(upload_token) or str(
        Path(tempfile.gettempdir()) / "muedit_cached_input"
    )
    return run_path, preloaded_signal


def parse_stream_options(
    *,
    roi_start: int | None,
    roi_end: int | None,
    rois: str | None,
    discard_channels: str | None,
    bids_entities: str | None,
    bids_metadata: str | None,
    artifact_regions: str | None = None,
) -> tuple[
    tuple[int, int] | None,
    list[tuple[int, int]] | None,
    list[list[int]] | None,
    dict | None,
    dict | None,
    list[tuple[int, int]] | None,
]:
    """Parse optional stream route form inputs into typed decomposition options."""
    roi = None
    if roi_start is not None and roi_end is not None:
        roi = (int(roi_start), int(roi_end))

    return (
        roi,
        parse_rois(rois),
        parse_discard_channels(discard_channels),
        parse_json_object(bids_entities, "bids_entities"),
        parse_json_object(bids_metadata, "bids_metadata"),
        parse_rois(artifact_regions, "artifact_regions"),
    )
