"""Tests for :mod:`muedit.decomp.core`: the decomposition loop and its windows."""

from __future__ import annotations

from typing import Any

import numpy as np

from muedit.decomp.core import decompose_step, extended_window
from muedit.decomp.preprocess import load_step, preprocess_step
from muedit.decomp.types import DecompositionParameters, PreprocessStepOutput
from muedit.models import SignalImport
from muedit.signal.decomp_primitives import extend_signal
from muedit.signal.filters import demean
from tests._synthetic_emg import ACTIVE_ROI, synthetic_recording

_PARAMS = DecompositionParameters(niter=12)


def _decompose_with_events() -> tuple[Any, list[dict[str, Any]]]:
    signal = synthetic_recording()
    events: list[dict[str, Any]] = []
    loaded = load_step("synthetic.mat", None, signal, lambda _s, p: events.append(p))
    prep = preprocess_step(
        loaded=loaded,
        duration=None,
        manual_roi=False,
        rois=[ACTIVE_ROI],
        params=_PARAMS,
        discard_overrides=None,
    )
    decomposed = decompose_step(
        prep=prep,
        params=_PARAMS,
        rng=np.random.default_rng(0),
        progress_cb=lambda _s, p: events.append(p),
    )
    return decomposed, events


def test_iteration_outcomes_cover_the_window_and_match_the_kept_filters() -> None:
    decomposed, events = _decompose_with_events()
    assert events[0]["phase"] == "load"
    live = [e for e in events if e.get("phase") == "decompose"]
    assert live[0]["iter"] == 0 and live[0]["outcomes"] == ""
    assert live[-1]["window_done"] is True
    for event in live:
        assert (event["grid"], event["window"]) == (0, 0)
        assert (event["ngrid"], event["nwindows"], event["niter"]) == (1, 1, _PARAMS.niter)
        assert set(event["outcomes"]) <= {"k", "r", "f"}

    outcomes = "".join(e["outcomes"] for e in live)
    assert len(outcomes) == live[-1]["iter"]
    assert outcomes.count("k") == decomposed.mu_filters_by_window[0].shape[1]


def test_window_without_edges_drops_the_zero_padded_columns() -> None:
    """With ``edges_sec`` 0, decompose and postprocess both cut the ``ex_factor - 1`` padded columns."""

    ex, (start, end) = 4, (500, 2500)
    data = np.random.default_rng(4).standard_normal((64, 3000))
    params = DecompositionParameters(
        nbextchan=64 * ex, edges_sec=0.0, niter=1, compute_dtype="float64"
    )
    prep = PreprocessStepOutput(
        signal=SignalImport(data=np.zeros((0, 0)), fsamp=2000.0, grid_names=["GR08MM1305"]),
        data=data,
        fsamp=2000.0,
        grid_names=["GR08MM1305"],
        coordinates=[np.zeros((64, 2))],
        ied=[8.0],
        discard_channels=[np.zeros(64, dtype=int)],
        declared_discard_channels=[np.zeros(64, dtype=int)],
        muscle_names=[],
        loader_meta={},
        rois=[(start, end)],
        coordinates_plateau=[start, end],
    )
    decomposed = decompose_step(prep, params, np.random.default_rng(0), None)
    assert decomposed.coordinates_plateau == [start + ex - 1, end - (ex - 1)]

    window = extended_window(prep, params, 0)[0]
    complete = extend_signal(demean(data[:, start:end]), ex)[:, ex - 1 : end - start]
    np.testing.assert_array_equal(window, complete)
