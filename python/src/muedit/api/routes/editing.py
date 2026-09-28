"""Editing endpoints for decomposition artifacts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import ValidationError

from muedit.api.binary import FRAME_MEDIA_TYPE, unpack_frame
from muedit.api.common import request_session
from muedit.api.contracts import success_payload
from muedit.api.schemas import (
    EditDeduplicatePayload,
    EditFilterPayload,
    EditFlagPayload,
    EditOutliersPayload,
    EditRoiPayload,
    EditSavePayload,
    PathPayload,
)
from muedit.api.services.editing_service import (
    add_artifact,
    add_spikes,
    delete_dr,
    delete_spikes,
    flag_mu,
    load_decomposition_binary_from_path,
    load_decomposition_from_path,
    remove_duplicates_service,
    remove_outliers,
    save_edits,
    update_filter,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(request_session)])


@router.post("/edit/load-by-path", response_model=None)
async def load_decomposition_by_path_endpoint(
    request: Request, payload: PathPayload, session: str = Depends(request_session)
) -> dict[str, Any] | Response:
    """Load a decomposition from an absolute/local path for edit mode."""
    path = payload.path
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    wants_binary = request.headers.get("x-muedit-binary", "1") != "0"
    if wants_binary:
        return load_decomposition_binary_from_path(path, session)
    return success_payload(load_decomposition_from_path(path, session))


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
    """Persist edits; the body is JSON, or a MUB1 frame with float32 ``pulse_trains``."""
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


@router.post("/edit/update-filter")
async def update_filter_endpoint(payload: EditFilterPayload) -> dict[str, Any]:
    """Recompute MU filter segment from raw BIDS EMG for one motor unit."""
    return success_payload(update_filter(payload))


@router.post("/edit/add-spikes")
async def add_spikes_endpoint(payload: EditRoiPayload) -> dict[str, Any]:
    """Add spikes inside the selected ROI using pulse-train thresholds."""
    return success_payload(add_spikes(payload))


@router.post("/edit/add-artifact")
async def add_artifact_endpoint(payload: EditRoiPayload) -> dict[str, Any]:
    """Mark a peak in the ROI as an artifact; it will be excluded from filter updates."""
    return success_payload(add_artifact(payload))


@router.post("/edit/delete-spikes")
async def delete_spikes_endpoint(payload: EditRoiPayload) -> dict[str, Any]:
    """Delete spikes inside the selected ROI using pulse-train thresholds."""
    return success_payload(delete_spikes(payload))


@router.post("/edit/delete-dr")
async def delete_dr_endpoint(payload: EditRoiPayload) -> dict[str, Any]:
    """Delete high discharge-rate spikes inside the selected ROI."""
    return success_payload(delete_dr(payload))


@router.post("/edit/remove-outliers")
async def remove_outliers_endpoint(payload: EditOutliersPayload) -> dict[str, Any]:
    """Apply discharge-rate outlier removal for the selected motor unit."""
    return success_payload(remove_outliers(payload))


@router.post("/edit/remove-duplicates")
async def remove_duplicates_endpoint(payload: EditDeduplicatePayload) -> dict[str, Any]:
    """Remove duplicate motor units using lag-aware spike-train overlap."""
    return success_payload(remove_duplicates_service(payload))


@router.post("/edit/flag-mu")
async def flag_mu_endpoint(payload: EditFlagPayload) -> dict[str, Any]:
    """Flag or unflag a motor unit for deletion/review in edit workflow."""
    return success_payload(flag_mu(payload))
