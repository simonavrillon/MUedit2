"""Configuration dataclass for adaptive decomposition routines."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Config:
    fsamp: int = 2048
    ex_factor: int = 10

    batch_ms: int = 100
    adapt_wh: bool = True
    adapt_sv: bool = True
    adapt_sd: bool = True

    wh_learning_rate: float = 7e-3
    sv_learning_rate: float = 3e-3

    sv_epochs: int = 1
    sv_tol: float = 1e-4
    contrast_func: Literal["logcosh", "cube"] = "logcosh"

    cov_alpha: float = 0.1

    compute_loss: bool = False

    spike_height_mult: int = 3
    spike_prev_weight: int = 5
    spike_dist_ms: int = 5
    spike_dist: int = field(init=False)
    batch_size: int = field(init=False)

    def __post_init__(self) -> None:
        self.spike_dist = int(self.spike_dist_ms * self.fsamp / 1000)
        self.batch_size = int(self.batch_ms * self.fsamp / 1000)
