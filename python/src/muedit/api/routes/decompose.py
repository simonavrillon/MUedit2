"""Decomposition endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse

from muedit.api.common import parse_discard_channels
from muedit.api.contracts import success_payload
from muedit.services.decompose_service import (
    cleanup_temp_file,
    decomposition_event_stream,
    fetch_decompose_preview_binary,
    parse_stream_options,
    resolve_decompose_input,
    run_decomposition_once,
)

router = APIRouter(prefix="/api/v1")


@router.post("/decompose")
async def decompose(
    file: UploadFile | None = File(None),
    params: str = Form(None),
    duration: float | None = Form(None),
    persist_output: bool = Form(False),
    discard_channels: str | None = Form(None),
    full_preview: bool = Form(False),
    upload_token: str | None = Form(None),
):
    """Run decomposition once and return a non-streamed summary + preview payload."""
    tmp_path, run_path, preloaded_signal, file_label = await resolve_decompose_input(
        file, upload_token
    )
    try:
        discard_override = parse_discard_channels(discard_channels)
        return success_payload(
            run_decomposition_once(
                input_path=run_path,
                duration=duration,
                params_raw=params,
                persist_output=persist_output,
                discard_override=discard_override,
                file_label=file_label,
                include_full_preview=full_preview,
                preloaded_signal=preloaded_signal,
            )
        )
    finally:
        cleanup_temp_file(tmp_path)


@router.post("/decompose_stream")
async def decompose_stream(
    request: Request,
    file: UploadFile | None = File(None),
    params: str = Form(None),
    duration: float | None = Form(None),
    persist_output: bool = Form(False),
    roi_start: int | None = Form(None),
    roi_end: int | None = Form(None),
    rois: str | None = Form(None),
    discard_channels: str | None = Form(None),
    bids_export: bool | None = Form(None),
    bids_root: str | None = Form(None),
    bids_entities: str | None = Form(None),
    bids_metadata: str | None = Form(None),
    full_preview: bool = Form(False),
    upload_token: str | None = Form(None),
):
    """Run decomposition and stream stage/progress events as NDJSON."""
    tmp_path, run_path, preloaded_signal, file_label = await resolve_decompose_input(
        file, upload_token
    )
    roi, roi_list, discard_override, bids_entities_obj, bids_metadata_obj = parse_stream_options(
        roi_start=roi_start,
        roi_end=roi_end,
        rois=rois,
        discard_channels=discard_channels,
        bids_entities=bids_entities,
        bids_metadata=bids_metadata,
    )

    wants_binary_preview = request.headers.get("x-muedit-binary", "0") == "1"
    generator = decomposition_event_stream(
        tmp_path=tmp_path,
        run_path=run_path,
        params_raw=params,
        duration=duration,
        persist_output=persist_output,
        roi=roi,
        rois=roi_list,
        discard_channels=discard_override,
        bids_root=bids_root if bids_export else None,
        bids_entities=bids_entities_obj,
        bids_metadata=bids_metadata_obj,
        file_label=file_label,
        include_full_preview=full_preview,
        preloaded_signal=preloaded_signal,
        cleanup=cleanup_temp_file,
        binary_preview=wants_binary_preview,
    )
    return StreamingResponse(generator, media_type="application/x-ndjson")


@router.get("/decompose_preview/{token}")
async def decompose_preview_binary(token: str):
    """Fetch a cached binary preview blob referenced by stream token."""
    return fetch_decompose_preview_binary(token)
