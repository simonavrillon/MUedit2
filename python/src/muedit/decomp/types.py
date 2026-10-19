"""Typed dataclasses for decomposition pipeline inputs/outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

import numpy as np

from muedit.errors import MueditError
from muedit.models import BoolArray, FloatArray, IntArray, SignalImport

DEFAULT_NBEXTCHAN: int = 1000
DEFAULT_PEEL_OFF_WIN_SEC: float = 0.025

ContrastFunc: TypeAlias = Literal["skew", "kurtosis", "logcosh"]
"""FastICA contrast function applied in the fixed-point iteration."""

ComputeDtype: TypeAlias = Literal["float32", "float64"]
"""Type of the large arrays (filtered EMG, extended and whitened windows); small matrices stay float64."""

DEFAULT_COMPUTE_DTYPE: ComputeDtype = "float32"
"""For runs that do not set ``compute_dtype``; the edit stage's filter update follows its run's."""

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
    random_init: bool = False  # seed each ICA filter randomly, not from the most active sample
    random_seed: int = 0
    peel_off_enabled: bool = False
    covfilter: bool = False
    duplicatesbgrids: bool = True
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
    compute_dtype: ComputeDtype = DEFAULT_COMPUTE_DTYPE

    def __post_init__(self) -> None:
        """Reject values the engine would run on silently: zero MUs, single-sample peel-off, divergence."""
        checks = (
            (self.niter >= 1, "niter must be >= 1"),
            (self.nwindows >= 1, "nwindows must be >= 1"),
            (self.peel_off_win > 0, "peel_off_win must be > 0"),
            (self.adapt_batch_ms > 0, "adapt_batch_ms must be > 0"),
            (self.adapt_wh_learning_rate > 0, "adapt_wh_learning_rate must be > 0"),
            (self.adapt_sv_learning_rate > 0, "adapt_sv_learning_rate must be > 0"),
            (0.0 < self.adapt_cov_alpha <= 1.0, "adapt_cov_alpha must be in (0, 1]"),
            (self.adapt_spike_prev_weight >= 0, "adapt_spike_prev_weight must be >= 0"),
        )
        for ok, message in checks:
            if not ok:
                raise MueditError(message)
        edge_ms = self.edges_sec * 1000
        if self.use_adaptive and self.adapt_batch_ms > edge_ms:
            # A batch longer than the calibration edge leaves a gap in the loss tracking.
            raise MueditError(
                f"adapt_batch_ms must be <= {edge_ms:.0f} (edges_sec * 1000) when use_adaptive"
            )

    @property
    def work_dtype(self) -> np.dtype[np.floating[Any]]:
        """``compute_dtype`` as a NumPy dtype."""
        return np.dtype(self.compute_dtype)


@dataclass
class LoadStepOutput:
    """Raw signal loaded from disk, before any preprocessing."""

    full_path: str
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
    #: ``discard_channels`` before automatic QC adds the bad channels it finds: what the grid
    #: catalogue and the request declare, and what the BIDS export marks in ``channels.tsv``.
    declared_discard_channels: list[IntArray]
    muscle_names: list[str]
    loader_meta: dict[str, Any]
    roi_list: list[tuple[int, int]]
    ngrid: int
    coordinates_plateau: list[int]
    artifact_mask: BoolArray | None = None
    target_segments: dict[str, Any] | None = None  # see preprocess._target_segments


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

    pulse_trains: FloatArray
    spike_times: list[IntArray]
    mu_grid_index: list[int]
    sil_by_window: dict[int, list[float]]
    sil: list[float]


@dataclass
class DecompositionResult:
    """What ``run_decomposition`` returns; every per-MU field has one entry per motor unit."""

    fsamp: float
    grid_names: list[str]
    pulse_trains: FloatArray  # (n_mu, n_samples)
    spike_times: list[IntArray]  # sorted sample indices per MU
    mu_grid_index: list[int]
    sil: list[float]
    discard_channels: list[IntArray]
    parameters: dict[str, Any]
    preview: dict[str, Any]  # build_preview_payload: what the app's run page draws
    save_path: str | None  # the .npz written, when the run saved one
    target_segments: dict[str, Any] | None = None  # the segmented target the run decomposed with
    rois: list[tuple[int, int]] | None = None  # the [start, end) windows it decomposed
