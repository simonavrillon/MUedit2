"""Session lifetime endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Query, Response

from muedit.api.cache import close_session
from muedit.api.memory import parse_session_id
from muedit.api.services.decompose_service import cancel_decomposition

router = APIRouter(prefix="/api/v1")


@router.post("/session/close", status_code=204)
def close_session_endpoint(session: str = Query(...)) -> Response:
    """Drop what a closing tab held and stop its run; the frontend sends it as a ``keepalive`` fetch."""
    session = parse_session_id(session, "session")
    cancel_decomposition(session)
    close_session(session)
    return Response(status_code=204)
