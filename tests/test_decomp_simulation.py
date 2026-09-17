"""Decomposition pipeline validation against simulated ground-truth spike trains."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.types import DecompositionParameters
from muedit.signal.decomp_primitives import POSTPROC_MIN_ISI_SEC
from tests._metrics_helpers import (
    Match,
    best_per_detected,
    build_signal,
    discharge_times,
    restrict_reference_to_roi,
    score_pairs,
)
from tests._report import describe, record

# 10 iterations per grid keeps each run fast (~15-20 s per excitation level).
_NITER = 10

# The contraction window in the 12 s simulated recording: 1 s ramp-in,
# 10 s plateau, 1 s ramp-out (defined by the ``target`` signal).
_SIM_ROI = (10240, 112639)

# Maximum lag (samples) searched by the cross-correlation.  The global shift
# between ground truth and the decomposed spike train is caused by the
# bandpass filter's group delay and the convolutive extension, typically
# 10-30 samples at 10 240 Hz.
_MAX_LAG = 100

# Jitter (samples) for the jitter-expanded intersection.  Matches the MATLAB
# ``checkduplicates.m`` default (``jitter = 0.00025 s`` -> ~3 samples at
# 10 240 Hz).  This is the tolerance for individual spike timing after the
# global shift is removed.
_JITTER = 3

# Valid units must have RoA, sensitivity, and precision all above this.
_VALID_THRESHOLD = 0.9


# ── Matching policy ──────────────────────────────────────────────────────────
#
# The agreement metrics themselves live in ``tests/_metrics_helpers`` and are
# shared with ``test_decomp_benchmark``.  What stays local to this module is the
# *assignment policy*: every detected MU is paired with its highest-RoA ground
# truth via :func:`best_per_detected`, with no RoA floor and no exclusivity.
#
# This is deliberately broader than the benchmark module, which applies a greedy
# one-to-one assignment with an RoA floor.  Here a detected MU that has no
# genuine counterpart still shows up, paired with whatever scored least badly,
# so those spurious units stay *visible* in the recorded agreement table rather
# than being filtered out of the reported metrics.


def _match_all(
    detected: list[np.ndarray],
    gt_spike_times: list[np.ndarray],
    roi: tuple[int, int],
) -> list[Match]:
    """Match each detected MU to its best ground-truth MU by RoA."""
    gt_in_roi = restrict_reference_to_roi(gt_spike_times, roi)
    return best_per_detected(score_pairs(detected, gt_in_roi, jitter=_JITTER, max_lag=_MAX_LAG))


# ── Session-scoped decomposition results ─────────────────────────────────────


@pytest.fixture(scope="session")
def sim_decomp_results(simulation_loaded: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Run the full decomposition pipeline on each simulated excitation level."""
    results: dict[int, dict[str, Any]] = {}
    for pct, sim in simulation_loaded.items():
        params = DecompositionParameters(niter=_NITER, peel_off_enabled=True)
        result, _ = run_decomposition(
            "simulated",
            roi=_SIM_ROI,
            params=params,
            save_npz=False,
            preloaded_signal=build_signal(sim),
        )
        results[pct] = result
    return results


@pytest.fixture(scope="session")
def sim_matches(
    sim_decomp_results: dict[int, dict[str, Any]],
    simulation_loaded: dict[int, dict[str, Any]],
) -> dict[int, list[Match]]:
    """Detected-to-ground-truth matches per excitation level, computed once."""
    return {
        pct: _match_all(
            discharge_times(sim_decomp_results[pct]),
            simulation_loaded[pct]["gt_spike_times"],
            _SIM_ROI,
        )
        for pct in sim_decomp_results
    }


# ── Decomposition output validity ────────────────────────────────────────────


@pytest.mark.parametrize("pct", [20, 40, 60])
def test_output_is_valid(sim_decomp_results: dict[int, dict[str, Any]], pct: int) -> None:
    """Enough MUs, all above the SIL gate, with sorted refractory-clean spike trains."""
    result = sim_decomp_results[pct]
    fsamp = float(result["signal"]["fsamp"])
    n_samples = np.asarray(result["signal"]["data"]).shape[1]
    refractory = int(np.round(fsamp * POSTPROC_MIN_ISI_SEC))
    dt = discharge_times(result)

    assert len(dt) >= 5, f"{pct}%: expected >= 5 detected MUs, got {len(dt)}"
    assert min(result["sil"]) >= result["parameters"]["sil_thr"]
    for i, d in enumerate(dt):
        if d.size == 0:
            continue
        assert np.all(np.diff(d) >= 0), f"{pct}% MU {i}: discharge times not sorted"
        assert d.min() >= 0 and d.max() < n_samples
        if d.size >= 2:
            assert int(np.diff(d).min()) >= refractory, f"{pct}% MU {i}: refractory violated"


# ── Ground-truth recovery ────────────────────────────────────────────────────


class TestGroundTruthRecovery:
    """Measure how well detected motor units match simulated ground truth."""

    @pytest.mark.parametrize("pct", [20, 40, 60])
    def test_measure_agreement_per_unit(
        self,
        sim_decomp_results: dict[int, dict[str, Any]],
        sim_matches: dict[int, list[Match]],
        pct: int,
    ) -> None:
        """Record per-MU agreement with ground truth; assert only that matching ran."""
        detected = discharge_times(sim_decomp_results[pct])
        matches = sim_matches[pct]

        for det_idx, gt_idx, prec, sens, roa, lag in matches:
            record(
                "gt_agreement_per_unit",
                caption=(
                    "Per-unit agreement with simulated ground truth "
                    f"(valid = RoA/precision/sensitivity all >= {_VALID_THRESHOLD})"
                ),
                excitation_pct=pct,
                detected_mu=det_idx,
                gt_mu=gt_idx,
                n_spikes=int(detected[det_idx].size),
                lag=lag,
                roa=roa,
                precision=prec,
                sensitivity=sens,
                valid=bool(
                    roa >= _VALID_THRESHOLD
                    and prec >= _VALID_THRESHOLD
                    and sens >= _VALID_THRESHOLD
                ),
            )

        # Deterministic floor: the matcher must have produced an answer for
        # every detected unit that carries enough spikes to be matchable.
        assert matches, f"{pct}%: no matches found"
        matchable = sum(1 for d in detected if d.size >= 2)
        assert len(matches) == matchable, (
            f"{pct}%: {len(matches)} matches for {matchable} matchable detected MUs"
        )

    @pytest.mark.parametrize("pct", [20, 40, 60])
    def test_each_detected_unit_matches_a_distinct_gt(
        self,
        sim_matches: dict[int, list[Match]],
        pct: int,
    ) -> None:
        """No two detected MUs match the same ground-truth MU (one-to-one)."""
        matches = sim_matches[pct]

        gt_indices = [m[1] for m in matches]
        assert len(gt_indices) == len(set(gt_indices)), (
            f"{pct}%: duplicate GT matches: {gt_indices}"
        )

    def test_measure_agreement_summary(
        self,
        sim_matches: dict[int, list[Match]],
    ) -> None:
        """Record per-excitation-level agreement summaries."""
        for pct in sorted(sim_matches):
            matches = sim_matches[pct]
            roas = [m[4] for m in matches]
            precs = [m[2] for m in matches]
            senss = [m[3] for m in matches]
            n_valid = sum(
                1
                for m in matches
                if m[4] >= _VALID_THRESHOLD
                and m[2] >= _VALID_THRESHOLD
                and m[3] >= _VALID_THRESHOLD
            )
            roa_stats = describe(roas)
            record(
                "gt_agreement_summary",
                caption="Agreement with simulated ground truth, by excitation level",
                excitation_pct=pct,
                detected=len(matches),
                valid=n_valid,
                valid_pct=(100.0 * n_valid / len(matches)) if matches else 0.0,
                roa_min=roa_stats["min"],
                roa_median=roa_stats["median"],
                roa_mean=roa_stats["mean"],
                prec_mean=describe(precs)["mean"],
                sens_mean=describe(senss)["mean"],
            )

        assert sim_matches, "no matches found across all excitation levels"
