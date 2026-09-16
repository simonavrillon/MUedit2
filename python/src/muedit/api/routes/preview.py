"""Preview and QC-window endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response

from muedit.api.contracts import success_payload
from muedit.api.schemas import PathPayload, QcAutoPayload, QcWindowPayload
from muedit.api.services.preview_service import (
    build_preview_from_path,
    get_qc_window,
    run_auto_qc_on_token,
)

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health() -> dict[str, Any]:
    """Health probe used by local tooling and deployment checks."""
    return success_payload({"status": "ok"})


@router.post("/preview-by-path")
def preview_by_path(payload: PathPayload) -> dict[str, Any]:
    """Build preview data from an existing file path on disk."""
    return success_payload(build_preview_from_path(payload.path))


@router.post("/qc/window", response_model=None)
def qc_window(payload: QcWindowPayload) -> Response:
    """Return QC channel-window data as packed float32 binary."""
    return get_qc_window(payload)


@router.post("/qc/auto")
def qc_auto(payload: QcAutoPayload) -> dict[str, Any]:
    """Run automatic QC on the cached signal and return bad channels + artifacts."""
    return success_payload(run_auto_qc_on_token(payload))
