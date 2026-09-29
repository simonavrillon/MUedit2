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
    """A save of discharge times sent by the client: the run save.

    Pulse trains travel as a MUB1 frame array, or stay on the server under
    ``run_result_token``, which also supplies the discharge times when none are sent.
    """

    distimes: list[list[int]] | None = None
    discharge_times: list[list[int]] | None = None
    flagged: list[bool] | None = None
    run_result_token: str | None = None
    total_samples: int
    fsamp: float | None = None
    grid_names: list[str] | None = None
    mu_grid_index: list[int] | None = None
    mu_uids: list[str] | None = None
    parameters: dict[str, Any] | None = None
    edit_history: list[dict[str, Any]] | None = None
    artifact_times: list[list[int]] | None = None
    artifact_regions: list[Any] | None = None


class EditSessionPayload(BaseModel):
    """Names an open edit session."""

    token: str


class EditSessionSavePayload(BidsSaveFields):
    """Save of an edit session: everything but the form fields is on the server."""

    token: str


class EditRecoverPayload(BaseModel):
    """Replay (or drop) the unsaved edits a previous session left for the open file."""

    token: str
    apply: bool = True


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
