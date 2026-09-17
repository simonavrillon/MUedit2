"""Tests for the automatic QC pipeline (:mod:`muedit.signal.qc_pipeline`).

The pipeline runs bad channels -> artifacts -> bad channels, excluding the
artifact samples from the final pass so a transient artifact cannot make a
healthy channel look bad.
"""

from __future__ import annotations

import numpy as np
import pytest

from muedit.signal.qc_pipeline import QCPipelineResult, _select_kept_channels, run_auto_qc
from tests._synthetic_emg import (
    FSAMP,
    N_CHANNELS,
    N_SAMPLES,
    add_contraction,
    correlated_emg,
    grid_coords,
)


def _rest_artifact(data: np.ndarray, center: int, half_width: int, amplitude: float) -> np.ndarray:
    """A brief step on every channel (random sign), like a cable movement at rest."""
    rng = np.random.default_rng(77)
    out = data.copy()
    for ch in rng.choice(data.shape[0], size=data.shape[0], replace=False):
        out[ch, center - half_width : center + half_width] += amplitude * rng.choice([-1, 1])
    return out


def _qc(data: np.ndarray, n_grids: int = 1) -> QCPipelineResult:
    return run_auto_qc(
        data, FSAMP, [N_CHANNELS] * n_grids, grid_coordinates=[grid_coords()] * n_grids
    )


def test_clean_recording_has_no_flags() -> None:
    result = _qc(add_contraction(correlated_emg(), 8000, 12000))
    assert result.artifact_mask.shape == (N_SAMPLES,)
    assert len(result.bad_channel_masks) == 1
    assert not result.bad_channel_masks[0].any()


@pytest.mark.parametrize(
    "half_width,amplitude", [(50, 2.0), (400, 5.0)], ids=["short", "long-strong"]
)
def test_rest_artifact_is_masked_without_flagging_channels(
    half_width: int, amplitude: float
) -> None:
    data = add_contraction(correlated_emg(), 8000, 12000)
    data = _rest_artifact(data, 4000, half_width, amplitude)
    result = _qc(data)
    assert result.artifact_mask[4000 - half_width : 4000 + half_width].any()
    assert not result.bad_channel_masks[0].any(), "rest artifact caused a bad-channel flag"


def test_dead_channel_is_flagged_despite_artifact() -> None:
    data = add_contraction(correlated_emg(), 8000, 12000)
    data = _rest_artifact(data, 4000, 50, 2.0)
    data[30] = 1e-12
    assert _qc(data).bad_channel_masks[0][30]


def test_multi_grid_reports_on_the_right_grid() -> None:
    g1 = _rest_artifact(add_contraction(correlated_emg(seed=1), 4000, 6000, seed=10), 1000, 50, 2.0)
    g2 = add_contraction(correlated_emg(seed=2), 14000, 16000, seed=20)
    g2[10] = 1e-12
    result = _qc(np.vstack([g1, g2]), n_grids=2)
    assert result.artifact_mask[950:1050].any()
    assert [np.flatnonzero(m).tolist() for m in result.bad_channel_masks] == [[], [10]]


def test_kept_channels_are_selected_per_grid() -> None:
    # Row value encodes the global channel index so the selection is checkable.
    data = np.repeat(np.arange(10, dtype=float)[:, None], 4, axis=1)
    coords = [np.arange(8.0).reshape(4, 2), 100 + np.arange(12.0).reshape(6, 2)]
    bad = [np.ones(4, bool), np.array([1, 0, 0, 1, 0, 0], bool)]

    kept, counts, kept_coords = _select_kept_channels(data, [4, 6], bad, coords)

    # A fully bad grid keeps its first channel so it is never empty.
    assert counts == [1, 4]
    np.testing.assert_array_equal(kept[:, 0], [0, 5, 6, 8, 9])
    np.testing.assert_array_equal(kept_coords[0], coords[0][[0]])
    np.testing.assert_array_equal(kept_coords[1], coords[1][[1, 2, 4, 5]])
