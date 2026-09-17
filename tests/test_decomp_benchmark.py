"""Decomposition benchmark: unit count, accuracy, and duration on real + simulated data.

For every dataset the expensive ICA decomposition is computed *once* and the
three post-processing branches are re-run over the same separators, each timed
independently:

1. **Windowed** (default) -- filters applied only inside the ROI window.
2. **Adaptive** (``use_adaptive=True``) -- :func:`adaptive_batch_process` walks
   the full signal, adapting whitening and source vectors online.
3. **Full trace** (``full_trace=True``) -- :func:`batch_process_filters` applies
   the dewhitened filters over the whole extended signal.

Two reference types are used:

1. **Real data** (``data/Novecento.otb4``) -- the *manually edited* discharge
   times stored in the BIDS derivatives.  The decomposition is re-run with the
   ROI and parameters of the stored ``_decomp.npz``.  The windowed branch is
   compared inside the ROI; the full-signal branches against the whole edited
   reference.  Matching is pooled across grids (the grids sit over the same
   muscle, so one physical MU can appear on several).
2. **Simulated data** (20/40/60 % excitation) -- the ground-truth spike trains
   in the simulation ``.mat`` files, restricted to the contraction ROI.

Every run upserts an entry -- dataset, the full ``DecompositionParameters`` and
the metrics -- into a JSON leaderboard at ``tests/benchmark_leaderboard.json``
(override with ``MUEDIT_BENCHMARK_LEADERBOARD``).  A row with the same dataset
and parameters is replaced, so the file is a local, git-ignored tuning history.
The same rows are also printed in the ``benchmark`` measurement table.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from muedit.decomp.core import decompose_step
from muedit.decomp.postprocess import postprocess_step
from muedit.decomp.preprocess import load_step, preprocess_step
from muedit.decomp.types import DecompositionParameters
from muedit.models import SignalImport
from tests._metrics_helpers import (
    Match,
    active_reference_count,
    build_signal,
    filter_to_roi,
    greedy_one_to_one,
    restrict_reference_to_roi,
    score_pairs,
)
from tests._report import record
from tests.conftest import DATA_DIR, require_sample

# Simulated runs only; the real-data run reuses the stored parameters.
_SIM_NITER = 10
_SIM_ROI = (10240, 112639)  # central 10 s of the 12 s simulated recording

# Matching tolerances (samples), after the MATLAB ``checkduplicates.m`` defaults.
_MAX_LAG = 100
_JITTER = 3
_REAL_MAX_LAG = 50
_REAL_JITTER = 3

# A match is kept only above this RoA; it is *valid* when RoA, precision and
# sensitivity all reach it.
_ROA_THRESHOLD = 0.9

LEADERBOARD_PATH = Path(
    os.environ.get(
        "MUEDIT_BENCHMARK_LEADERBOARD",
        Path(__file__).resolve().parent / "benchmark_leaderboard.json",
    )
)

_DERIV_DIR = DATA_DIR / "Test" / "derivatives" / "muedit" / "sub-1" / "ses-1" / "decomp"
_DECOMP_NPZ = _DERIV_DIR / "sub-1_ses-1_task-trapezoid_run-1_decomp.npz"
_EDITED_NPZ = _DERIV_DIR / "sub-1_ses-1_task-trapezoid_run-1_edited.npz"

_BRANCHES: tuple[tuple[str, dict[str, bool]], ...] = (
    ("windowed", {}),
    ("adaptive", {"use_adaptive": True}),
    ("full_trace", {"full_trace": True}),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarize(matches: list[Match], n_units: int, n_ref: int) -> dict[str, Any]:
    roas = [m[4] for m in matches]
    precs = [m[2] for m in matches]
    senss = [m[3] for m in matches]
    n_valid = sum(1 for m in matches if min(m[2], m[3], m[4]) >= _ROA_THRESHOLD)
    return {
        "n_units": n_units,
        "n_reference_units": n_ref,
        "n_matched": len(matches),
        "n_matched_valid": n_valid,
        "mean_roa": float(np.mean(roas)) if roas else 0.0,
        "mean_precision": float(np.mean(precs)) if precs else 0.0,
        "mean_sensitivity": float(np.mean(senss)) if senss else 0.0,
    }


def _log_result(dataset: str, params: DecompositionParameters, results: dict[str, Any]) -> None:
    """Upsert the run into the JSON leaderboard and the ``benchmark`` table."""
    entry = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": dataset,
        "parameters": asdict(params),
        "results": results,
    }
    data = json.loads(LEADERBOARD_PATH.read_text()) if LEADERBOARD_PATH.exists() else []
    for i, existing in enumerate(data):
        if existing.get("dataset") == dataset and existing.get("parameters") == entry["parameters"]:
            data[i] = entry
            break
    else:
        data.append(entry)
    LEADERBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEADERBOARD_PATH.write_text(json.dumps(data, indent=2))

    record(
        "benchmark",
        caption=f"Decomposition accuracy by dataset and postprocess branch (see {LEADERBOARD_PATH.name})",
        dataset=dataset,
        **results,
    )


def _prepare(
    path: str, signal: SignalImport, roi: tuple[int, int], params: DecompositionParameters
) -> Any:
    loaded = load_step(path, None, signal, None)
    return preprocess_step(
        loaded=loaded,
        duration=None,
        manual_roi=False,
        roi=roi,
        rois=None,
        params=params,
        discard_overrides=None,
        bids_root=None,
        bids_entities=None,
        bids_metadata=None,
    )


def _run_branches(
    prep: Any,
    base: DecompositionParameters,
    dataset: str,
    compare: Any,
) -> dict[str, dict[str, Any]]:
    """Decompose once, then time and score each postprocess branch.

    ``compare(label, distime)`` returns ``(matches, n_units, n_ref)``.
    Returns ``{label: summary}`` for every branch.
    """
    rng = np.random.default_rng(base.random_seed)
    t0 = time.perf_counter()
    decomposed = decompose_step(prep=prep, params=base, rng=rng, progress_cb=None)
    decompose_sec = round(time.perf_counter() - t0, 3)

    out: dict[str, dict[str, Any]] = {}
    for label, mode in _BRANCHES:
        params = replace(base, **mode)
        t0 = time.perf_counter()
        post = postprocess_step(prep=prep, decomposed=decomposed, params=params, progress_cb=None)
        post_sec = round(time.perf_counter() - t0, 3)

        distime = [np.asarray(d) for d in post.distime]
        matches, n_units, n_ref = compare(label, distime)
        results = _summarize(matches, n_units, n_ref)
        results.update(
            postprocess_mode=label, duration_sec=post_sec, decompose_duration_sec=decompose_sec
        )

        name = dataset if label == "windowed" else f"{dataset}_{label}"
        _log_result(name, params, results)
        out[label] = results
    return out


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def real_reference() -> dict[str, Any]:
    """Edited discharge times (reference) + the ROI/params from the stored decomp."""
    edited = np.load(str(require_sample(_EDITED_NPZ)), allow_pickle=True)
    decomp = np.load(str(require_sample(_DECOMP_NPZ)), allow_pickle=True)
    return {
        "discharge_times": [np.asarray(x) for x in edited["discharge_times"]],
        "roi": tuple(int(x) for x in decomp["rois"][0]),
        "params": decomp["parameters"].item(),
    }


@pytest.fixture(scope="session")
def real_benchmark(
    novecento_otb4_file: Path,
    novecento_emg: SignalImport,
    real_reference: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    p = real_reference["params"]
    roi = real_reference["roi"]
    base = DecompositionParameters(
        niter=p["niter"],
        peel_off_enabled=p["peel_off_enabled"],
        sil_thr=p["sil_thr"],
        duplicatesthresh=p["duplicatesthresh"],
        # The edited reference was deduplicated across grids; match that.
        duplicatesbgrids=True,
        random_seed=p["random_seed"],
    )
    prep = _prepare(str(novecento_otb4_file), novecento_emg, roi, base)
    ref_full = real_reference["discharge_times"]

    def compare(label: str, distime: list[np.ndarray]) -> Any:
        # The windowed branch only finds spikes inside the ROI.
        ref = filter_to_roi(ref_full, roi) if label == "windowed" else ref_full
        det = filter_to_roi(distime, roi) if label == "windowed" else distime
        active = [i for i, d in enumerate(ref) if d.size >= 2]
        candidates = score_pairs(
            det, ref, jitter=_REAL_JITTER, max_lag=_REAL_MAX_LAG, ref_indices=active
        )
        n_ref = sum(1 for d in ref if d.size > 0)
        return greedy_one_to_one(candidates, _ROA_THRESHOLD), len(det), n_ref

    return _run_branches(prep, base, "real_novecento", compare)


@pytest.fixture(scope="session")
def sim_benchmarks(
    simulation_loaded: dict[int, dict[str, Any]],
) -> dict[int, dict[str, dict[str, Any]]]:
    benchmarks: dict[int, dict[str, dict[str, Any]]] = {}
    for pct, sim in simulation_loaded.items():
        base = DecompositionParameters(niter=_SIM_NITER, peel_off_enabled=True)
        prep = _prepare("simulated", build_signal(sim), _SIM_ROI, base)
        gt = sim["gt_spike_times"]
        gt_in_roi = restrict_reference_to_roi(gt, _SIM_ROI)
        n_ref = active_reference_count(gt, _SIM_ROI)

        def compare(
            _label: str,
            distime: list[np.ndarray],
            gt_in_roi: list[np.ndarray] = gt_in_roi,
            n_ref: int = n_ref,
        ) -> Any:
            det = filter_to_roi(distime, _SIM_ROI)
            candidates = score_pairs(det, gt_in_roi, jitter=_JITTER, max_lag=_MAX_LAG)
            return greedy_one_to_one(candidates, _ROA_THRESHOLD), len(det), n_ref

        benchmarks[pct] = _run_branches(prep, base, f"simulated_{pct}", compare)
    return benchmarks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_real_units_match_edited_reference(real_benchmark: dict[str, dict[str, Any]]) -> None:
    """At least a quarter of the detected MUs match an edited MU with RoA > 0.9."""
    r = real_benchmark["windowed"]
    assert r["n_units"] >= 5, f"expected >= 5 MUs, got {r['n_units']}"
    assert r["n_matched"] >= max(1, r["n_units"] // 4), (
        f"only {r['n_matched']}/{r['n_units']} MUs matched the edited reference"
    )
    for label in ("adaptive", "full_trace"):
        assert real_benchmark[label]["n_matched"] > 0, f"{label}: no MU matched the reference"


@pytest.mark.parametrize("pct", [20, 40, 60])
def test_sim_matches_ground_truth(
    sim_benchmarks: dict[int, dict[str, dict[str, Any]]], pct: int
) -> None:
    """The windowed branch reaches a mean RoA of 0.9; the others match something."""
    r = sim_benchmarks[pct]["windowed"]
    assert r["n_matched"] > 0, f"{pct}%: no GT matches"
    assert r["mean_roa"] >= _ROA_THRESHOLD, f"{pct}%: mean RoA {r['mean_roa']:.3f}"
    # The full-signal branches adapt online and can land lower; the benchmark
    # records the gap rather than gating on it.
    for label in ("adaptive", "full_trace"):
        assert sim_benchmarks[pct][label]["n_matched"] > 0, f"{pct}% {label}: no GT matches"
