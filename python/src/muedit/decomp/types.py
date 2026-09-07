"""Typed dataclasses for decomposition pipeline inputs/outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

DEFAULT_NBEXTCHAN: int = 1000
DEFAULT_PEEL_OFF_WIN_SEC: float = 0.025
POSTPROCESS_MODES: dict[str, dict[str, bool]] = {
    "windowed": {"use_adaptive": False, "full_trace": False},
    "full-trace": {"use_adaptive": False, "full_trace": True},
    "adaptive": {"use_adaptive": True, "full_trace": False},
}

DEFAULT_POSTPROCESS_MODE: str = "windowed"


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
    contrast_func: str = "skew"
    sil_thr: float = 0.88
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


@dataclass
class LoadStepOutput:
    """Raw signal loaded from disk, before any preprocessing."""

    full_path: str
    filename: str
    signal: dict[str, Any]
    data: np.ndarray
    fsamp: float


@dataclass
class PreprocessStepOutput:
    """Filtered signal and grid metadata ready for ICA decomposition."""

    signal: dict[str, Any]
    data: np.ndarray
    fsamp: float
    grid_names: list[str]
    coordinates: list[np.ndarray]
    ied: list[float]
    discard_channels: list[np.ndarray]
    muscles: list[str]
    loader_meta: dict[str, Any]
    roi_list: list[tuple[int, int]]
    ngrid: int
    coordinates_plateau: list[int]


@dataclass
class DecomposeStepOutput:
    """ICA filters and per-window SIL scores produced by the decompose step."""

    mu_filters: dict[int, np.ndarray]
    whiten_mat: dict[int, np.ndarray]
    coordinates_plateau: list[int]
    sil_by_window: dict[int, list[float]]
    mu_grid_index: list[int]
    win_means: dict[int, np.ndarray]


@dataclass
class PostprocessStepOutput:
    """Deduplicated pulse trains and discharge times after post-processing."""

    pulse_t: np.ndarray
    distime: list[np.ndarray]
    mu_grid_index: list[int]
    sil_by_window: dict[int, list[float]]
    sil: list[float]
    adaptive_losses: dict[int, Any]
