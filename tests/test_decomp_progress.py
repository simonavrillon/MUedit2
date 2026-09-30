"""The decomposition's live progress events: one outcome per iteration, matching what is kept."""

from __future__ import annotations

from typing import Any

import numpy as np

from muedit.decomp.core import decompose_step
from muedit.decomp.preprocess import load_step, preprocess_step
from muedit.decomp.types import DecompositionParameters
from muedit.models import SignalImport
from tests._synthetic_emg import FSAMP, motor_unit_emg

_ROI = (5_000, 35_000)
_PARAMS = DecompositionParameters(niter=12)


def _decompose_with_events() -> tuple[Any, list[dict[str, Any]]]:
    emg = motor_unit_emg(seed=5, n_samples=40_000, fsamp=FSAMP, activity=_ROI)
    signal = SignalImport(
        data=emg.astype(np.float32), fsamp=FSAMP, gridname=["GR08MM1305"], muscle=["ta"]
    )
    events: list[dict[str, Any]] = []
    loaded = load_step("synthetic.mat", None, signal, lambda _s, p: events.append(p))
    prep = preprocess_step(
        loaded=loaded,
        duration=None,
        manual_roi=False,
        roi=_ROI,
        rois=None,
        params=_PARAMS,
        discard_overrides=None,
        bids_root=None,
        bids_entities=None,
        bids_metadata=None,
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
    assert outcomes.count("k") == decomposed.mu_filters[0].shape[1]
