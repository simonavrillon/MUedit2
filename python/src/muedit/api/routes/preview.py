"""Preview and QC-window endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from muedit.api.contracts import success_payload
from muedit.api.schemas import PathPayload, QcWindowPayload
from muedit.services.preview_service import build_preview, build_preview_from_path, get_qc_window

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health() -> dict[str, Any]:
    """Health probe used by local tooling and deployment checks."""
    return success_payload({"status": "ok"})


@router.post("/preview")
async def preview(file: UploadFile = File(...)):
    """Build downsampled preview/QC metadata from an uploaded signal file."""
    try:
        return success_payload(await build_preview(file))
    except (OSError, ValueError) as exc:
        if "contains decomposition fields" in str(exc):
            raise HTTPException(
                status_code=400,
                detail={
                    "field": "file",
                    "reason": "This MAT file is a decomposition artifact; load it in edit mode.",
                },
            ) from exc
        raise


@router.post("/preview-by-path")
def preview_by_path(payload: PathPayload):
    """Build preview data from an existing file path on disk."""
    try:
        return success_payload(build_preview_from_path(payload.path))
    except (OSError, ValueError) as exc:
        if "contains decomposition fields" in str(exc):
            raise HTTPException(
                status_code=400,
                detail={
                    "field": "path",
                    "reason": "This MAT file is a decomposition artifact; load it in edit mode.",
                },
            ) from exc
        raise


@router.post("/qc/window")
async def qc_window(payload: QcWindowPayload):
    """Return QC channel window data in JSON or raw-binary transport format."""
    result = get_qc_window(payload.model_dump(exclude_none=True))
    if isinstance(result, dict):
        return success_payload(result)
    return result
