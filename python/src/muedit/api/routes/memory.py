"""Session lifetime and memory diagnostics endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response

from muedit.api.cache import BUDGET, close_session
from muedit.api.contracts import success_payload
from muedit.api.memory import (
    peak_rss_bytes,
    physical_memory_bytes,
    process_memory_bytes,
    session_id_or_default,
)
from muedit.api.services.decompose_service import cancel_decomposition
from muedit.io.store import store_usage

router = APIRouter(prefix="/api/v1")


@router.post("/session/close", status_code=204)
def close_session_endpoint(session: str = Query(...)) -> Response:
    """Drop what a closing tab held and stop its run; the frontend sends it with ``navigator.sendBeacon``."""
    if session_id_or_default(session) == session:
        cancel_decomposition(session)
        close_session(session)
    return Response(status_code=204)


@router.get("/debug/memory")
def debug_memory() -> dict[str, Any]:
    """Process memory, cache usage per cache and per session, and the session stores on disk."""
    return success_payload(
        {
            "process_bytes": process_memory_bytes(),
            "peak_rss_bytes": peak_rss_bytes(),
            "physical_memory_bytes": physical_memory_bytes(),
            "budget": BUDGET.usage(),
            "session_store": store_usage(),
        }
    )
