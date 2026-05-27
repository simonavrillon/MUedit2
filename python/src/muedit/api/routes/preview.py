"""Preview and QC-window endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response

from muedit.api.contracts import success_payload
from muedit.api.schemas import PathPayload, QcWindowPayload
from muedit.api.services.preview_service import (
    build_preview,
    build_preview_from_path,
    get_qc_window,
)

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health() -> dict[str, Any]:
    """Health probe used by local tooling and deployment checks."""
    return success_payload({"status": "ok"})


@router.post("/preview")
async def preview(file: UploadFile = File(...)) -> dict[str, Any]:
    """Build downsampled preview/QC metadata from an uploaded signal file."""
    return success_payload(await build_preview(file))


@router.post("/preview-by-path")
def preview_by_path(payload: PathPayload) -> dict[str, Any]:
    """Build preview data from an existing file path on disk."""
    return success_payload(build_preview_from_path(payload.path))


@router.post("/qc/window", response_model=None)
async def qc_window(payload: QcWindowPayload) -> dict[str, Any] | Response:
    """Return QC channel window data in JSON or raw-binary transport format."""
    result = get_qc_window(payload.model_dump(exclude_none=True))
    if isinstance(result, Response):
        return result
    return success_payload(result)
