"""Tests for :mod:`muedit.decomp.postprocess`: the filter application routes and duplicate removal."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from muedit.decomp.postprocess import dedup_survivors, postprocess_step
from muedit.decomp.types import (
    POSTPROCESS_MODES,
    DecomposeStepOutput,
    DecompositionParameters,
    PostprocessMode,
)
from muedit.models import resident_nbytes
from tests._platform import MAPPED_FILES_STAY, store_files
from tests._synthetic_emg import ACTIVE_ROI, FSAMP, STORE_PARAMS

_DEDUP_N_SAMPLES = 4000


def _spikes(seed: int) -> list[int]:
    rng = np.random.default_rng(seed)
    return sorted(rng.choice(np.arange(100, _DEDUP_N_SAMPLES - 100), 40, replace=False).tolist())


@pytest.mark.parametrize("mode", list(POSTPROCESS_MODES))
def test_pulse_trains_in_the_store_match_the_heap(
    store_prep: dict, decomposed: DecomposeStepOutput, mode: PostprocessMode
) -> None:
    flags = POSTPROCESS_MODES[mode]
    params = replace(
        STORE_PARAMS, use_adaptive=flags["use_adaptive"], full_trace=flags["full_trace"]
    )
    store = store_prep["store"]
    heap = postprocess_step(
        prep=store_prep["heap"],
        decomposed=decomposed,
        params=params,
        progress_cb=None,
    )
    stored = postprocess_step(
        prep=store_prep["stored"],
        decomposed=decomposed,
        params=params,
        progress_cb=None,
        store=store,
    )
    assert len(heap.spike_times) > 0
    if mode != "windowed":
        # The full-signal routes run over the whole recording, not the decomposed window.
        active = np.any(heap.pulse_trains != 0, axis=0)
        assert active[: ACTIVE_ROI[0]].any() and active[ACTIVE_ROI[1] :].any(), mode
    np.testing.assert_array_equal(stored.pulse_trains, heap.pulse_trains)
    assert stored.pulse_trains.dtype == np.float32
    assert len(stored.spike_times) == len(heap.spike_times)
    for got, want in zip(stored.spike_times, heap.spike_times, strict=True):
        np.testing.assert_array_equal(got, want)
    assert stored.mu_grid_index == heap.mu_grid_index
    assert resident_nbytes(stored.pulse_trains) == 0
    assert MAPPED_FILES_STAY or "pulse_trains_all" not in store_files(store)


def test_dedup_keeps_survivors_in_input_order() -> None:
    a = np.array(_spikes(1))
    b = np.array(_spikes(2))
    # Index 1 duplicates index 0 with a steadier rhythm, so it is the one kept of the pair.
    steady = np.arange(200, _DEDUP_N_SAMPLES - 200, 100)
    kept, grids = dedup_survivors(
        [np.unique(np.r_[steady, steady[::3] + 7]), steady, a, b],
        [0, 0, 1, 1],
        2,
        DecompositionParameters(duplicatesbgrids=False),
        FSAMP,
        _DEDUP_N_SAMPLES,
    )
    assert kept == sorted(kept) and 1 in kept and 0 not in kept
    assert grids == [0, 1, 1]


@pytest.mark.parametrize(
    ("across_grids", "kept", "grids"),
    [(False, [0, 1, 2], [0, 1, 1]), (True, [0, 2], [0, 1])],
    ids=["within-grids-only", "across-grids"],
)
def test_a_copy_on_another_grid_goes_only_across_grids(
    across_grids: bool, kept: list[int], grids: list[int]
) -> None:
    steady = np.arange(200, _DEDUP_N_SAMPLES - 200, 100)
    copy = steady + 20  # within the lag the dedup aligns
    other = np.array(_spikes(2))
    got = dedup_survivors(
        [steady, copy, other],
        [0, 1, 1],
        2,
        DecompositionParameters(duplicatesbgrids=across_grids),
        FSAMP,
        _DEDUP_N_SAMPLES,
    )
    assert got == (kept, grids)


def test_dedup_survivors_refuses_a_grid_outside_the_range() -> None:
    with pytest.raises(ValueError, match="outside"):
        dedup_survivors(
            [np.array([1]), np.array([2])], [0, 1], 1, DecompositionParameters(), FSAMP, 10
        )
