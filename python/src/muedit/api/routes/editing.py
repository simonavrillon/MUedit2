"""Edit-stage endpoints: the server-side edit session, and saves."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response

from muedit.api.common import request_session
from muedit.api.contracts import success_payload
from muedit.api.schemas import (
    EditOpPayload,
    EditPrepareGridPayload,
    EditRecoverPayload,
    EditSavePayload,
    EditSessionSavePayload,
    PathPayload,
)
from muedit.api.services.editing_service import (
    apply_edit,
    edit_session_state,
    open_edit_session,
    prepare_edit_grid,
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


@router.post("/edit/session/prepare-grid")
def prepare_grid_endpoint(
    payload: EditPrepareGridPayload, session: str = Depends(request_session)
) -> dict[str, Any]:
    """Filter a grid's EMG ahead of its first filter update; answers once it is done."""
    prepare_edit_grid(payload, session)
    return success_payload({"grid": payload.grid})


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


@router.post("/edit/save")
def save_edits_endpoint(payload: EditSavePayload) -> dict[str, Any]:
    """Save a run: its discharge times and pulse trains stay on the server under its token."""
    return success_payload(save_edits(payload))
