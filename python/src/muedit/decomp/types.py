"""Typed dataclasses for decomposition pipeline inputs/outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from muedit.models import BoolArray, FloatArray, IntArray, SignalImport

DEFAULT_NBEXTCHAN: int = 1000
DEFAULT_PEEL_OFF_WIN_SEC: float = 0.025

ContrastFunc: TypeAlias = Literal["skew", "kurtosis", "logcosh"]
"""FastICA contrast function applied in the fixed-point iteration."""

PostprocessMode: TypeAlias = Literal["windowed", "full-trace", "adaptive"]
"""Post-processing route; must match POSTPROCESS_MODES in the frontend."""

POSTPROCESS_MODES: dict[PostprocessMode, dict[str, bool]] = {
    "windowed": {"use_adaptive": False, "full_trace": False},
    "full-trace": {"use_adaptive": False, "full_trace": True},
    "adaptive": {"use_adaptive": True, "full_trace": False},
}

DEFAULT_POSTPROCESS_MODE: PostprocessMode = "windowed"


@dataclass
class DecompositionParameters:
    """Algorithm hyper-parameters for a single decomposition run."""

    niter: int = 150
    nwindows: int = 1
    initialization: bool = False
    random_seed: int = 0
    peel_off_enabled: bool = False
    covfilter: bool = False
    duplicatesbgrids: bool = False
    nbextchan: int = DEFAULT_NBEXTCHAN
    edges_sec: float = 0.2
    contrast_func: ContrastFunc = "skew"
    sil_thr: float = 0.9
    cov_thr: float = 0.5
    peel_off_win: float = DEFAULT_PEEL_OFF_WIN_SEC
    duplicatesthresh: float = 0.3
    use_adaptive: bool = False
    adapt_batch_ms: int = 100
    adapt_wh: bool = True
    adapt_sv: bool = True
    adapt_sd: bool = True
    adapt_wh_learning_rate: float = 7e-3
    adapt_sv_learning_rate: float = 3e-3
    adapt_cov_alpha: float = 0.1
    adapt_spike_prev_weight: int = 5
    full_trace: bool = False
    auto_mask_artifacts: bool = False


@dataclass
class LoadStepOutput:
    """Raw signal loaded from disk, before any preprocessing."""

    full_path: str
    filename: str
    signal: SignalImport
    data: FloatArray
    fsamp: float


@dataclass
class PreprocessStepOutput:
    """Filtered signal and grid metadata ready for ICA decomposition."""

    signal: SignalImport
    data: FloatArray
    fsamp: float
    grid_names: list[str]
    coordinates: list[FloatArray]
    ied: list[float]
    discard_channels: list[IntArray]
    muscles: list[str]
    loader_meta: dict[str, Any]
    roi_list: list[tuple[int, int]]
    ngrid: int
    coordinates_plateau: list[int]
    artifact_mask: BoolArray | None = None
    bad_channel_masks: list[BoolArray] | None = None


@dataclass
class DecomposeStepOutput:
    """ICA filters and per-window SIL scores produced by the decompose step."""

    mu_filters: dict[int, FloatArray]
    whiten_mat: dict[int, FloatArray]
    coordinates_plateau: list[int]
    sil_by_window: dict[int, list[float]]
    mu_grid_index: list[int]
    win_means: dict[int, FloatArray]


@dataclass
class PostprocessStepOutput:
    """Deduplicated pulse trains and discharge times after post-processing."""

    pulse_t: FloatArray
    distime: list[IntArray]
    mu_grid_index: list[int]
    sil_by_window: dict[int, list[float]]
    sil: list[float]
    adaptive_losses: dict[int, Any]
