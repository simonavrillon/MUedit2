"""Typed API request models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PathPayload(BaseModel):
    """Path-based request body used by path-loading endpoints."""

    path: str


class QcWindowPayload(BaseModel):
    """Typed request body for QC channel-window retrieval."""

    upload_token: str
    grid_index: int = 0
    start: int = 0
    end: int = 0
    target_points: int = 96
    target_fs: float = 1000.0
    representation: str = "envelope"
    channel_index: int | None = None


class EditSavePayload(BaseModel):
    """Typed request body for persisting edited decomposition outputs."""

    distimes: list[list[int]] | None = None
    discharge_times: list[list[int]] | None = None
    flagged: list[bool] | None = None
    remove_flagged: bool | None = None
    remove_duplicates: bool | None = None
    pulse_trains: list[list[float]] | None = None
    total_samples: int
    fsamp: float | None = None
    grid_names: list[str] | None = None
    mu_grid_index: list[int] | None = None
    parameters: dict[str, Any] | None = None
    muscle_names: list[str] | str | None = None
    muscle: list[str] | str | None = None
    bids_root: str | None = None
    file_label: str | None = None
    entity_label: str | None = None


class EditFilterPayload(BaseModel):
    """Typed request body for update-filter endpoint."""

    bids_root: str | None = None
    edit_signal_token: str | None = None
    file_label: str | None = None
    entity_label: str | None = None
    grid_index: int = 0
    mu_index: int = 0
    distimes: list[list[int]]
    pulse_train: list[float] | None = None
    view_start: int = 0
    view_end: int = 0
    nbextchan: int = 1000


class EditRoiPayload(BaseModel):
    """Typed request body for ROI edit actions (add/delete spikes, delete-dr)."""

    distimes: list[list[int]]
    mu_index: int = 0
    pulse_train: list[float] | None = None
    fsamp: float | None = None
    x_start: int = 0
    x_end: int = 0
    y_min: float | None = None
    y_max: float | None = None
