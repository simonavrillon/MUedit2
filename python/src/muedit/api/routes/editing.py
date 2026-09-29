"""Edit-stage endpoints: the server-side edit session, and saves."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import ValidationError

from muedit.api.binary import FRAME_MEDIA_TYPE, unpack_frame
from muedit.api.common import request_session
from muedit.api.contracts import success_payload
from muedit.api.schemas import (
    EditOpPayload,
    EditRecoverPayload,
    EditSavePayload,
    EditSessionSavePayload,
    PathPayload,
)
from muedit.api.services.editing_service import (
    apply_edit,
    edit_session_state,
    open_edit_session,
    recover_edits,
    save_edit_session,
    save_edits,
)
from muedit.editing.session import EditSession

router = APIRouter(prefix="/api/v1", dependencies=[Depends(request_session)])

_OP_PATTERN = "^(" + "|".join(EditSession.OPS) + ")$"


@router.post("/edit/session/open")
def open_session_endpoint(
    payload: PathPayload, session: str = Depends(request_session)
) -> Response:
    """Open a decomposition for editing (MUB1: its fields, then CSR discharge times and artifacts)."""
    if not payload.path:
        raise HTTPException(status_code=400, detail="path is required")
    return open_edit_session(payload.path, session)


@router.get("/edit/session")
def session_state_endpoint(
    token: str = Query(...), session: str = Depends(request_session)
) -> Response:
    """The whole state of an open edit session, for a page reloaded while it was open."""
    return edit_session_state(token, session)


@router.post("/edit/session/recover")
def recover_endpoint(
    payload: EditRecoverPayload, session: str = Depends(request_session)
) -> Response:
    """Replay (``apply``) or drop the unsaved edits a previous session left for the file."""
    return recover_edits(payload, session)


@router.post("/edit/session/save")
def save_session_endpoint(
    payload: EditSessionSavePayload, session: str = Depends(request_session)
) -> dict[str, Any]:
    """Save the session's edits; the request carries only the session form's fields."""
    return success_payload(save_edit_session(payload, session))


@router.post("/edit/ops/{op}")
def edit_op_endpoint(
    payload: EditOpPayload,
    op: str = Path(..., pattern=_OP_PATTERN),
    session: str = Depends(request_session),
) -> Response:
    """Apply one edit to a session (MUB1: what changed, and the changed MUs' discharge times)."""
    return apply_edit(op, payload, session)


@router.post(
    "/edit/save",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {"schema": EditSavePayload.model_json_schema()},
                FRAME_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}},
            },
            "required": True,
        }
    },
)
async def save_edits_endpoint(request: Request) -> dict[str, Any]:
    """Save a run; the body is JSON, or a MUB1 frame with float32 ``pulse_trains``."""
    body = await request.body()
    pulse_trains = None
    try:
        if request.headers.get("content-type", "").startswith(FRAME_MEDIA_TYPE):
            try:
                meta, arrays = unpack_frame(body)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid frame: {exc}") from exc
            payload = EditSavePayload.model_validate(meta)
            pulse_trains = arrays.get("pulse_trains")
        else:
            payload = EditSavePayload.model_validate_json(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc
    return success_payload(save_edits(payload, pulse_trains))
