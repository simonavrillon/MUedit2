"""Viewport series endpoints: what the QC stage draws, at the resolution it is drawn."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from muedit.api.common import request_session
from muedit.api.services.series_service import MAX_BINS, series_frame

router = APIRouter(prefix="/api/v1/series", dependencies=[Depends(request_session)])

_BINS = Query(1024, ge=1, le=MAX_BINS, description="Bins of the envelope (about the pixel width)")
_START = Query(0, ge=0)
_END = Query(0, ge=0, description="Exclusive end sample; 0 is the end of the recording")


@router.get("/emg")
def series_emg(
    upload_token: str,
    grid: int = Query(0, ge=0),
    start: int = _START,
    end: int = _END,
    bins: int = _BINS,
) -> Response:
    """Bandpassed EMG of every channel of one grid, as a min/max envelope (MUB1)."""
    return series_frame("emg", upload_token, start, end, bins, grid)


@router.get("/overview")
def series_overview(
    upload_token: str, start: int = _START, end: int = _END, bins: int = _BINS
) -> Response:
    """Smoothed mean ``|EMG|`` of each grid, as a min/max envelope (MUB1)."""
    return series_frame("overview", upload_token, start, end, bins)


@router.get("/aux")
def series_aux(
    upload_token: str, start: int = _START, end: int = _END, bins: int = _BINS
) -> Response:
    """Auxiliary channels, as a min/max envelope (MUB1)."""
    return series_frame("aux", upload_token, start, end, bins)
