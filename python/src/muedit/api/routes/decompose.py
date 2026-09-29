"""Decomposition endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response, StreamingResponse

from muedit.api.common import request_session
from muedit.api.config import resolve_bids_root
from muedit.api.contracts import success_payload
from muedit.api.services.decompose_service import (
    cancel_decomposition,
    decomposition_event_stream,
    fetch_decompose_preview_binary,
    parse_stream_options,
    start_decomposition,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(request_session)])


@router.post("/decompose_stream")
async def decompose_stream(
    request: Request,
    params: str = Form(None),
    duration: float | None = Form(None),
    persist_output: bool = Form(False),
    roi_start: int | None = Form(None),
    roi_end: int | None = Form(None),
    rois: str | None = Form(None),
    discard_channels: str | None = Form(None),
    bids_export: bool | None = Form(None),
    project: str | None = Form(None),
    bids_entities: str | None = Form(None),
    bids_metadata: str | None = Form(None),
    full_preview: bool = Form(False),
    upload_token: str | None = Form(None),
    artifact_regions: str | None = Form(None),
    session: str = Depends(request_session),
) -> StreamingResponse:
    """Run decomposition and stream stage/progress events as NDJSON; 409 while another runs."""
    (
        roi,
        roi_list,
        discard_override,
        bids_entities_obj,
        bids_metadata_obj,
        artifact_region_list,
    ) = parse_stream_options(
        roi_start=roi_start,
        roi_end=roi_end,
        rois=rois,
        discard_channels=discard_channels,
        bids_entities=bids_entities,
        bids_metadata=bids_metadata,
        artifact_regions=artifact_regions,
    )
    run = start_decomposition(
        upload_token,
        {
            "params_raw": params,
            "duration": duration,
            "persist_output": persist_output,
            "roi": roi,
            "rois": roi_list,
            "discard_channels": discard_override,
            "bids_root": str(resolve_bids_root(project)) if bids_export else None,
            "bids_entities": bids_entities_obj,
            "bids_metadata": bids_metadata_obj,
            "include_full_preview": full_preview,
            "artifact_regions": artifact_region_list,
        },
        binary_preview=request.headers.get("x-muedit-binary", "1") != "0",
        session=session,
    )
    return StreamingResponse(
        decomposition_event_stream(run, request.is_disconnected),
        media_type="application/x-ndjson",
    )


@router.post("/decompose/cancel")
def decompose_cancel(session: str = Depends(request_session)) -> dict[str, Any]:
    """Stop the run this session started; ``cancelled`` is false when it has none."""
    return success_payload({"cancelled": cancel_decomposition(session)})


@router.get("/decompose_preview/{token}")
async def decompose_preview_binary(token: str) -> Response:
    """Fetch a cached binary preview blob referenced by stream token."""
    return fetch_decompose_preview_binary(token)
