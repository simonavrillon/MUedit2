"""Typed API request models."""

from __future__ import annotations

from dataclasses import fields
from typing import Annotated, Any

import numpy as np
from pydantic import (
    BaseModel,
    Field,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    StrictInt,
    field_validator,
    model_validator,
)

from muedit.decomp.types import ComputeDtype, DecompositionParameters
from muedit.io.bids import parse_age

#: A discharge time: a 0-based sample index the .npz stores as int32.
SampleIndex = Annotated[int, Field(ge=0, le=np.iinfo(np.int32).max)]


class PathPayload(BaseModel):
    """Path-based request body used by path-loading endpoints."""

    path: str


class QcAutoPayload(BaseModel):
    """Typed request body for the on-demand automatic QC pass."""

    upload_token: str


class SegmentPayload(BaseModel):
    """Request body for the target segmentation; without ``aux_index`` the target is found by name."""

    upload_token: str
    aux_index: int | None = None


class Span(BaseModel):
    """A ``[start, end)`` sample range; one past the recording's end is clamped to it."""

    start: NonNegativeInt
    end: int

    @model_validator(mode="after")
    def _not_empty(self) -> Span:
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be greater than start ({self.start})")
        return self


class DecomposePayload(BaseModel):
    """A decomposition run of the upload behind ``upload_token``."""

    upload_token: str
    params: DecompositionParameters = Field(default_factory=DecompositionParameters)
    persist_output: bool = False  # also save the .npz next to the source file
    full_preview: bool = False  # keep the full pulse trains for the run's explorer and save
    rois: list[Span] | None = None  # decomposition windows; None = the whole recording
    artifact_regions: list[Span] | None = None
    discard_channels: list[list[int]] | None = None  # per grid, per channel: 1 = discarded
    target_index: int | None = None  # the auxiliary channel the user segmented
    bids_export: bool = False
    project: str | None = None
    bids_entities: dict[str, Any] | None = None
    bids_metadata: dict[str, Any] | None = None

    @field_validator("params", mode="before")
    @classmethod
    def _known_params(cls, value: Any) -> Any:
        """Reject unknown parameter names, as the CLI does, rather than run without them."""
        if isinstance(value, dict):
            unknown = sorted(set(value) - {f.name for f in fields(DecompositionParameters)})
            if unknown:
                raise ValueError(f"unknown parameters: {', '.join(unknown)}")
        return value


class ParticipantMeta(BaseModel):
    """A participant's row in ``participants.tsv``; empty or ``n/a`` fields are not given."""

    age: int | None = None
    sex: str | None = None
    handedness: str | None = None

    @field_validator("age", mode="before")
    @classmethod
    def _age(cls, value: Any) -> int | None:
        return parse_age(value)


#: Mains frequencies are 50 or 60 Hz; the bound only catches a value typed in the wrong unit.
MAX_POWERLINE_HZ = 1000.0


class BidsSaveFields(BaseModel):
    """Where and how a save writes its BIDS files: the session form's fields."""

    muscle_names: list[str] | str | None = None
    project: str | None = None
    file_label: str | None = None
    entity_label: str | None = None
    participant_meta: ParticipantMeta | None = None
    powerline_freq: float | None = Field(default=None, gt=0, le=MAX_POWERLINE_HZ)
    units: str | None = None  # of the EMG samples, over what the loader recorded
    manufacturer: str | None = None
    manufacturers_model_name: str | None = None
    placement_scheme: str | None = None
    placement_scheme_description: str | None = None
    reference_location: str | None = None
    ground_location: str | None = None
    task_description: str | None = None
    software_versions: str | None = None
    remove_flagged: bool | None = None
    remove_duplicates: bool | None = None


class EditSavePayload(BidsSaveFields):
    """The run save: pulse trains stay on the server under ``run_result_token``.

    The run also supplies the discharge times when none are sent.
    """

    spike_times: list[list[SampleIndex]] | None = None
    run_result_token: str | None = None
    total_samples: PositiveInt
    fsamp: PositiveFloat
    grid_names: list[str] | None = None
    mu_grid_index: list[NonNegativeInt] | None = None
    parameters: dict[str, Any] | None = None
    artifact_regions: list[Span] | None = None


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
    """An edit of an open session; one subclass per operation, in ``EDIT_OPS``."""

    token: str


class MuOpPayload(EditOpPayload):
    """An operation on one MU: ``remove-outliers``, ``reset``, ``duplicate``."""

    mu: StrictInt  # lax mode would read true as MU 1


class FlagPayload(MuOpPayload):
    """``flag``: set the MU's flag, or clear it with ``flag=false``."""

    flag: bool = True


class RoiAddPayload(MuOpPayload):
    """``add-spikes`` / ``add-artifact``: the peaks above ``y_min`` in ``[x_start, x_end]``."""

    x_start: int
    x_end: int
    y_min: float | None = None


class RoiDeletePayload(RoiAddPayload):
    """``delete-spikes``: the discharges and artifacts in the box."""

    y_max: float | None = None


class FilterUpdatePayload(MuOpPayload):
    """``update-filter``: refit the MU filter on the EMG in view."""

    view_start: int
    view_end: int
    use_peeloff: bool = False
    lock_spikes: bool = False
    project: str | None = None
    # Left out, these are the run's own settings.
    nbextchan: int | None = None
    peel_off_win: float | None = Field(default=None, gt=0)
    compute_dtype: ComputeDtype | None = None


#: The body of each ``/edit/ops/{op}``; ``remove-duplicates`` and ``undo`` need only the token.
EDIT_OPS: dict[str, type[EditOpPayload]] = {
    "add-spikes": RoiAddPayload,
    "add-artifact": RoiAddPayload,
    "delete-spikes": RoiDeletePayload,
    "remove-outliers": MuOpPayload,
    "update-filter": FilterUpdatePayload,
    "flag": FlagPayload,
    "reset": MuOpPayload,
    "duplicate": MuOpPayload,
    "remove-duplicates": EditOpPayload,
    "undo": EditOpPayload,
}
