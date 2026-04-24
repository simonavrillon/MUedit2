"""Editing endpoints for decomposition artifacts."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from muedit.api.contracts import success_payload
from muedit.api.schemas import EditFilterPayload, EditRoiPayload, EditSavePayload, PathPayload
from muedit.services.editing_service import (
    add_spikes,
    delete_dr,
    delete_spikes,
    flag_mu,
    load_decomposition,
    load_decomposition_binary,
    load_decomposition_binary_from_path,
    load_decomposition_from_path,
    remove_duplicates_service,
    remove_outliers,
    save_edits,
    update_filter,
)

router = APIRouter(prefix="/api/v1")


@router.post("/edit/load")
async def load_decomposition_endpoint(request: Request, file: UploadFile = File(...)):
    """Load a decomposition upload for interactive edit mode."""
    wants_binary = request.headers.get("x-muedit-binary", "0") == "1"
    if wants_binary:
        return await load_decomposition_binary(file)
    return success_payload(await load_decomposition(file))


@router.post("/edit/load-by-path")
def load_decomposition_by_path_endpoint(request: Request, payload: PathPayload):
    """Load a decomposition from an absolute/local path for edit mode."""
    path = payload.path
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    wants_binary = request.headers.get("x-muedit-binary", "0") == "1"
    if wants_binary:
        return load_decomposition_binary_from_path(path)
    return success_payload(load_decomposition_from_path(path))


@router.post("/edit/save")
async def save_edits_endpoint(payload: EditSavePayload):
    """Persist edited decomposition to BIDS source tree."""
    result = save_edits(payload.model_dump(exclude_none=True))
    return success_payload(result)


@router.post("/edit/update-filter")
async def update_filter_endpoint(payload: EditFilterPayload):
    """Recompute MU filter segment from raw BIDS EMG for one motor unit."""
    return success_payload(update_filter(payload.model_dump(exclude_none=True)))


@router.post("/edit/add-spikes")
async def add_spikes_endpoint(payload: EditRoiPayload):
    """Add spikes inside the selected ROI using pulse-train thresholds."""
    return success_payload(add_spikes(payload.model_dump(exclude_none=True)))


@router.post("/edit/delete-spikes")
async def delete_spikes_endpoint(payload: EditRoiPayload):
    """Delete spikes inside the selected ROI using pulse-train thresholds."""
    return success_payload(delete_spikes(payload.model_dump(exclude_none=True)))


@router.post("/edit/delete-dr")
async def delete_dr_endpoint(payload: EditRoiPayload):
    """Delete high discharge-rate spikes inside the selected ROI."""
    return success_payload(delete_dr(payload.model_dump(exclude_none=True)))


@router.post("/edit/remove-outliers")
async def remove_outliers_endpoint(payload: dict):
    """Apply discharge-rate outlier removal for the selected motor unit."""
    return success_payload(remove_outliers(payload))


@router.post("/edit/remove-duplicates")
async def remove_duplicates_endpoint(payload: dict):
    """Remove duplicate motor units using lag-aware spike-train overlap."""
    return success_payload(remove_duplicates_service(payload))


@router.post("/edit/flag-mu")
async def flag_mu_endpoint(payload: dict):
    """Flag or unflag a motor unit for deletion/review in edit workflow."""
    return success_payload(flag_mu(payload))
