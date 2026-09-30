"""Typed API request models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PathPayload(BaseModel):
    """Path-based request body used by path-loading endpoints."""

    path: str


class QcAutoPayload(BaseModel):
    """Typed request body for the on-demand automatic QC pass."""

    upload_token: str


class BidsSaveFields(BaseModel):
    """Where and how a save writes its BIDS files: the session form's fields."""

    muscle: list[str] | str | None = None
    muscle_names: list[str] | str | None = None  # deprecated alias for ``muscle``
    project: str | None = None
    file_label: str | None = None
    entity_label: str | None = None
    participant_meta: dict[str, Any] | None = None
    powerline_freq: float | None = None
    manufacturer: str | None = None
    manufacturers_model_name: str | None = None
    placement_scheme: str | None = None
    placement_scheme_description: str | None = None
    task_description: str | None = None
    software_versions: str | None = None
    remove_flagged: bool | None = None
    remove_duplicates: bool | None = None


class EditSavePayload(BidsSaveFields):
    """The run save: pulse trains stay on the server under ``run_result_token``.

    The run also supplies the discharge times when none are sent.
    """

    distimes: list[list[int]] | None = None
    run_result_token: str | None = None
    total_samples: int
    fsamp: float | None = None
    grid_names: list[str] | None = None
    mu_grid_index: list[int] | None = None
    parameters: dict[str, Any] | None = None
    artifact_regions: list[Any] | None = None


class EditSessionSavePayload(BidsSaveFields):
    """Save of an edit session: everything but the form fields is on the server."""

    token: str


class EditRecoverPayload(BaseModel):
    """Replay (or drop) the unsaved edits a previous session left for the open file."""

    token: str
    apply: bool = True


class EditPrepareGridPayload(BaseModel):
    """Filter one grid's EMG before its first filter update, with that update's ``project``."""

    token: str
    grid: int
    project: str | None = None


class EditOpPayload(BaseModel):
    """One edit of an open session; each operation reads the fields it needs."""

    token: str
    mu: int | None = None
    x_start: int | None = None
    x_end: int | None = None
    y_min: float | None = None
    y_max: float | None = None
    view_start: int | None = None
    view_end: int | None = None
    use_peeloff: bool | None = None
    lock_spikes: bool | None = None
    flag: bool | None = None
    project: str | None = None
    nbextchan: int | None = None
    peel_off_win: float | None = None
