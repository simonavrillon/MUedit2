"""Preview and QC-window endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from muedit.api.common import request_session
from muedit.api.contracts import success_payload
from muedit.api.schemas import PathPayload, QcAutoPayload
from muedit.api.services.preview_service import (
    build_preview_from_path,
    run_auto_qc_on_token,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(request_session)])


@router.get("/health")
def health() -> dict[str, Any]:
    """Health probe used by local tooling and deployment checks."""
    return success_payload({"status": "ok"})


@router.post("/preview-by-path")
def preview_by_path(
    payload: PathPayload, session: str = Depends(request_session)
) -> dict[str, Any]:
    """Build preview data from an existing file path on disk."""
    return success_payload(build_preview_from_path(payload.path, session))


@router.post("/qc/auto")
def qc_auto(payload: QcAutoPayload) -> dict[str, Any]:
    """Run automatic QC on the cached signal and return bad channels + artifacts."""
    return success_payload(run_auto_qc_on_token(payload))
