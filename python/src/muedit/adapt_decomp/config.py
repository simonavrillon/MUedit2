"""Configuration dataclass for adaptive decomposition routines."""

from dataclasses import dataclass, field


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

    cov_alpha: float = 0.1

    compute_loss: bool = False

    spike_height_mult: int = 3
    spike_prev_weight: int = 5
    spike_dist_ms: int = 5
    batch_size: int = field(init=False)

    def __post_init__(self) -> None:
        self.batch_size = int(self.batch_ms * self.fsamp / 1000)
