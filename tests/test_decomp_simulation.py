"""Decomposition pipeline validation against simulated ground-truth spike trains.

Three simulated HD-EMG datasets (20 %, 40 %, 60 % max excitation) carry known
ground truth: ``signal.spikes`` is a (122 880 x 150) binary spike train for
150 simulated motor units, and ``signal.data`` is the 65-channel monopolar
EMG (64 GR08MM1305 electrodes + 1 extra, 10 240 Hz, 12 s).

The contraction occupies the central 10 s (samples 10 240-112 639).  Each
test decomposes that window through the public
:func:`muedit.decomp.pipeline.run_decomposition` entry point and matches the
detected motor units against the ground-truth spike trains using the
agreement metrics from the reference MATLAB pipeline (``checkduplicates.m``
in MUdict): cross-correlation to estimate the global lag, jitter-expanded
intersection for TP counting, and RoA = TP / (TP + FP + FN).
"""

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


# ---------------------------------------------------------------------------
# Matching policy
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Session-scoped decomposition results
# ---------------------------------------------------------------------------


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
    """Detected-to-ground-truth matches per excitation level, computed once.

    Scoring every detected MU against every active ground-truth MU costs a few
    seconds per level, and four tests below need the same answer.  Caching it
    here mirrors how ``sim_decomp_results`` caches the decomposition itself.
    """
    return {
        pct: _match_all(
            discharge_times(sim_decomp_results[pct]),
            simulation_loaded[pct]["gt_spike_times"],
            _SIM_ROI,
        )
        for pct in sim_decomp_results
    }


# ---------------------------------------------------------------------------
# Decomposition output validity
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Ground-truth recovery
# ---------------------------------------------------------------------------


class TestGroundTruthRecovery:
    """Measure how well detected motor units match simulated ground truth.

    Each detected MU is matched to the ground-truth MU with the highest rate
    of agreement (RoA), using cross-correlation to estimate and correct the
    global lag between the two spike trains (as in ``checkduplicates.m``).
    A match is labelled **valid** when RoA, sensitivity, and precision all
    exceed :data:`_VALID_THRESHOLD`.

    Recovery quality is *recorded*, not gated: it is a distribution that shifts
    with excitation level, random seed and BLAS build, so a threshold on its
    worst element (or on its mean) makes a poor pass/fail signal.  See the
    ``gt_agreement_per_unit`` and ``gt_agreement_summary`` tables.  The
    assertions that remain here cover the deterministic parts -- that matching
    ran for every matchable unit and that no two detected MUs collapse onto one
    ground-truth MU.
    """

    @pytest.mark.parametrize("pct", [20, 40, 60])
    def test_measure_agreement_per_unit(
        self,
        sim_decomp_results: dict[int, dict[str, Any]],
        sim_matches: dict[int, list[Match]],
        pct: int,
    ) -> None:
        """Record per-MU agreement with ground truth; assert only that matching ran.

        Agreement quality is a *distribution*, not a pass/fail: the pipeline
        emits a mix of clean units and occasional partial or spurious ones, and
        the mix shifts with excitation level, seed and NumPy/BLAS build.  A hard
        floor ("every detected MU must reach RoA 0.9") fails the whole run on a
        single marginal unit and reports nothing about the other twenty.

        So every matched pair is written to the ``gt_agreement_per_unit`` table
        (``reports/gt_agreement_per_unit.csv``) with its RoA, precision,
        sensitivity, spike count and estimated lag, plus a ``valid`` flag using
        :data:`_VALID_THRESHOLD`.  Sort by RoA to see the tail; compare the
        ``valid`` counts across excitation levels to judge whether a change
        helped or hurt.

        Note the matching policy here has no RoA floor and no exclusivity (see
        the module comment), so a detected MU with no genuine counterpart still
        appears, paired with whatever scored least badly -- those low-RoA rows
        are the spurious units, and they are exactly what the table is for.
        """
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
        """Record per-excitation-level agreement summaries.

        Complements the per-unit table with one row per excitation level:
        how many units were detected, how many cleared the validity threshold,
        and the min/median/mean/max of each metric.  Means are reported rather
        than asserted -- a mean is a poor gate anyway, since a handful of clean
        units can mask a growing tail of bad ones (which is precisely what the
        per-unit table exposes).
        """
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
