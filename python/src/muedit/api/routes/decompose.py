"""Decomposition endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from muedit.api.common import request_session
from muedit.api.contracts import success_payload
from muedit.api.schemas import DecomposePayload
from muedit.api.services.decompose_service import (
    cancel_decomposition,
    decomposition_event_stream,
    start_decomposition,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(request_session)])


@router.post("/decompose_stream")
async def decompose_stream(
    payload: DecomposePayload,
    request: Request,
    session: str = Depends(request_session),
) -> StreamingResponse:
    """Run decomposition and stream stage/progress events as NDJSON; 409 while another runs."""
    run = start_decomposition(payload, session=session)
    return StreamingResponse(
        decomposition_event_stream(run, request.is_disconnected),
        media_type="application/x-ndjson",
    )


@router.post("/decompose/cancel")
def decompose_cancel(session: str = Depends(request_session)) -> dict[str, Any]:
    """Stop the run this session started; ``cancelled`` is false when it has none."""
    return success_payload({"cancelled": cancel_decomposition(session)})
