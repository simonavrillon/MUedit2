"""Tests for :mod:`muedit.decomp.adaptive_batch`: the adaptive route over the decomposed windows."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from muedit.decomp import adaptive_batch
from muedit.decomp.adaptation import AdaptiveDecomp
from muedit.decomp.types import ComputeDtype, DecompositionParameters
from tests._synthetic_emg import calibration_case


@pytest.mark.parametrize("compute_dtype", ["float32", "float64"])
def test_adaptive_route_computes_in_the_work_dtype(
    short_emg: np.ndarray, compute_dtype: ComputeDtype, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``compute_dtype`` reaches the adaptive state and the extended signal it reads."""
    whiten_mat, mu_filters, win_mean = calibration_case(short_emg)
    seen: list[tuple[np.dtype[Any], ...]] = []

    class Spy(AdaptiveDecomp):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            seen.append((self.whiten_mat.dtype, self.mu_filters.dtype, self.source.dtype))

    monkeypatch.setattr(adaptive_batch, "AdaptiveDecomp", Spy)
    adaptive_batch.adaptive_batch_process(
        {0: mu_filters},
        {0: whiten_mat},
        {0: short_emg},
        [200, 2600],
        short_emg.shape[1],
        2000,
        1,
        {0: win_mean},
        DecompositionParameters(adapt_batch_ms=50, compute_dtype=compute_dtype),
    )
    assert len(seen) == 2  # forward and backward passes
    assert all(dtypes == (np.dtype(compute_dtype),) * 3 for dtypes in seen)
