"""Editing endpoints for decomposition artifacts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from muedit.api.contracts import success_payload
from muedit.api.schemas import (
    EditDeduplicatePayload,
    EditFilterPayload,
    EditFlagPayload,
    EditOutliersPayload,
    EditRoiPayload,
    EditSavePayload,
    EditSessionDuplicatePayload,
    EditSessionRowPayload,
    PathPayload,
)
from muedit.api.services.editing_service import (
    add_artifact,
    add_spikes,
    delete_dr,
    delete_spikes,
    duplicate_session_row,
    flag_mu,
    get_session_row,
    load_decomposition_binary_from_path,
    load_decomposition_from_path,
    load_decomposition_lazy_from_path,
    remove_duplicates_service,
    remove_outliers,
    save_edits,
    update_filter,
)

router = APIRouter(prefix="/api/v1")


@router.post("/edit/load-by-path", response_model=None)
async def load_decomposition_by_path_endpoint(
    request: Request, payload: PathPayload
) -> dict[str, Any] | Response:
    """Load a decomposition from an absolute/local path for edit mode."""
    path = payload.path
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    if request.headers.get("x-muedit-lazy-rows") == "1":
        return success_payload(load_decomposition_lazy_from_path(path))
    wants_binary = request.headers.get("x-muedit-binary", "1") != "0"
    if wants_binary:
        return load_decomposition_binary_from_path(path)
    return success_payload(load_decomposition_from_path(path))


@router.post("/edit/save")
async def save_edits_endpoint(payload: EditSavePayload) -> dict[str, Any]:
    """Persist edited decomposition to BIDS source tree."""
    result = save_edits(payload)
    return success_payload(result)


@router.post("/edit/session/duplicate")
async def duplicate_session_row_endpoint(payload: EditSessionDuplicatePayload) -> dict[str, Any]:
    """Register a duplicated motor unit in the edit session so it can be saved from there."""
    return success_payload(duplicate_session_row(payload))


@router.post("/edit/session/row", response_model=None)
async def session_row_endpoint(payload: EditSessionRowPayload) -> Response:
    """One motor unit's row from the edit session, as raw float32 for display."""
    return get_session_row(payload)


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
