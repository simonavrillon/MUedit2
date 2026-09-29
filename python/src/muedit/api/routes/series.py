"""Viewport series endpoints: what the QC, run and edit stages draw, at the resolution drawn."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from muedit.api.common import request_session
from muedit.api.services.series_service import MAX_BINS, pulse_frame, series_frame, spikes_frame

router = APIRouter(prefix="/api/v1", dependencies=[Depends(request_session)])

_BINS = Query(1024, ge=1, le=MAX_BINS, description="Bins of the envelope (about the pixel width)")
_START = Query(0, ge=0)
_END = Query(0, ge=0, description="Exclusive end sample; 0 is the end of the recording")


@router.get("/series/emg")
def series_emg(
    upload_token: str,
    grid: int = Query(0, ge=0),
    start: int = _START,
    end: int = _END,
    bins: int = _BINS,
) -> Response:
    """Bandpassed EMG of every channel of one grid, as a min/max envelope (MUB1)."""
    return series_frame("emg", upload_token, start, end, bins, grid)


@router.get("/series/overview")
def series_overview(
    upload_token: str, start: int = _START, end: int = _END, bins: int = _BINS
) -> Response:
    """Smoothed mean ``|EMG|`` of each grid, as a min/max envelope (MUB1)."""
    return series_frame("overview", upload_token, start, end, bins)


@router.get("/series/aux")
def series_aux(
    upload_token: str, start: int = _START, end: int = _END, bins: int = _BINS
) -> Response:
    """Auxiliary channels, as a min/max envelope (MUB1)."""
    return series_frame("aux", upload_token, start, end, bins)


@router.get("/series/pulse")
def series_pulse(
    token: str,
    mu: int = Query(..., ge=0),
    start: int = _START,
    end: int = _END,
    bins: int = _BINS,
) -> Response:
    """One MU's pulse train, as a min/max envelope (MUB1), with its discharges in view.

    ``token`` names an edit session or a finished run.
    """
    return pulse_frame(token, mu, start, end, bins)


@router.get("/spikes")
def spikes(token: str, mu: str = Query("all", description="An MU index, or ``all``")) -> Response:
    """Discharge times as CSR (MUB1: ``spikes`` int32, ``spike_offsets`` int64)."""
    return spikes_frame(token, mu)
