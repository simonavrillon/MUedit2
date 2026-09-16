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

from muedit.api.binary import pack_json_f32_payload
from muedit.api.cache import (
    _get_decomp_preview_binary,
    _get_upload_signal,
    _get_upload_source_path,
    _store_decomp_preview_binary,
)
from muedit.api.common import (
    build_params,
    make_json_safe,
    parse_discard_channels,
    parse_json_object,
    parse_rois,
    summarize_result,
)
from muedit.decomp.pipeline import run_decomposition


def _as_f32_matrix(value: Any) -> np.ndarray:
    """Coerce a preview pulse payload into a 2-D float32 matrix."""
    matrix = np.asarray(value if value is not None else [], dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2:
        matrix = np.zeros((0, 0), dtype=np.float32)
    return matrix


def _encode_decompose_preview_f32(preview: dict[str, Any]) -> bytes:
    """Encode streamed preview arrays as float32 binary payload (MDPV v1)."""
    rest = dict(preview)
    pulse_full = _as_f32_matrix(rest.pop("pulse_trains_full", None))
    pulse_all = _as_f32_matrix(rest.pop("pulse_trains_all", None))
    preview_copy = make_json_safe(rest)

    preview_copy["pulse_trains_full_shape"] = [int(pulse_full.shape[0]), int(pulse_full.shape[1])]
    preview_copy["pulse_trains_all_shape"] = [int(pulse_all.shape[0]), int(pulse_all.shape[1])]
    preview_copy["pulse_dtype"] = "float32"
    return pack_json_f32_payload(b"MDPV", preview_copy, pulse_full, pulse_all)


def fetch_decompose_preview_binary(token: str) -> Response:
    """Resolve a preview token from cache and return binary preview content."""
    payload = _get_decomp_preview_binary(token)
    if payload is None:
        raise HTTPException(status_code=404, detail="Preview binary token not found or expired")
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"x-muedit-format": "decompose-preview-f32-v1"},
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
    preloaded_signal: dict[str, Any] | None = None,
    binary_preview: bool = False,
    artifact_regions: list[tuple[int, int]] | None = None,
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
        try:
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
            )
            preview_raw = result.get("preview", {})
            if binary_preview:
                bin_payload = _encode_decompose_preview_f32(preview_raw)
                preview_token = _store_decomp_preview_binary(bin_payload)
                preview_payload = make_json_safe(
                    {
                        k: v
                        for k, v in preview_raw.items()
                        if k not in ("pulse_trains_full", "pulse_trains_all")
                    }
                )
                preview_payload["preview_binary_token"] = preview_token
            else:
                preview_payload = make_json_safe(preview_raw)
            q.put(
                {
                    "stage": "done",
                    "summary": make_json_safe(
                        summarize_result(result, save_path, persist_output)
                    ),
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
) -> tuple[str, dict[str, Any]]:
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
