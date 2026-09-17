"""Post-processing path validation: adaptive decomposition and full-trace filtering."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from muedit.adapt_decomp.adaptation import run_adaptive_decomposition
from muedit.adapt_decomp.config import Config
from muedit.decomp.core import decompose_step
from muedit.decomp.postprocess import postprocess_step
from muedit.decomp.preprocess import load_step, preprocess_step
from muedit.decomp.types import (
    POSTPROCESS_MODES,
    DecomposeStepOutput,
    DecompositionParameters,
    PostprocessStepOutput,
    PreprocessStepOutput,
)
from muedit.models import SignalImport
from muedit.signal.decomp_primitives import POSTPROC_MIN_ISI_SEC, enforce_refractory
from tests._metrics_helpers import central_roi
from tests._report import record

# Keep the per-iteration budget modest so the real-data decomposition finishes
# in well under a minute (matches ``test_decomp_pipeline``).
_NITER = 50
# Width of the analysis window in seconds (central portion of the recording).
_ROI_WIDTH_SEC = 10.0

# Novecento has a movement artifact after the ROI (samples ~51600-52000 and
# ~56900-57000).  Unmasked, the adaptive whitening overflows to inf/NaN there and
# every full-trace filter of the first grid fires only on the artifact.
_AUTO_MASK = True
# Unmasked that costs a whole grid, ~20% of the MUs; masked, only one or two weak
# units have no discharge in the ROI.
_MAX_ROI_SILENT_FRAC = 0.1

# Default dedup overlap threshold; disabling value sits above the maximum
# achievable overlap score (1.0) so no MU pair can ever be flagged a duplicate.
_DEDUP_ON_THRESH = 0.3
_DEDUP_OFF_THRESH = 2.0


# ── Session-scoped post-processing variants ──────────────────────────────────


@pytest.fixture(scope="session")
def novecento_roi_local(novecento_emg: SignalImport) -> tuple[int, int]:
    """Central 10 s ROI of the Novecento recording (where the contraction is)."""
    return central_roi(novecento_emg.data, float(novecento_emg.fsamp), _ROI_WIDTH_SEC)


@pytest.fixture(scope="session")
def postprocess_variants(
    novecento_otb4_file: Path,
    novecento_emg: SignalImport,
    novecento_roi_local: tuple[int, int],
) -> dict[str, Any]:
    """Decompose once, then re-run post-processing for each branch x dedup state."""
    base = DecompositionParameters(
        niter=_NITER, peel_off_enabled=True, auto_mask_artifacts=_AUTO_MASK
    )
    rng = np.random.default_rng(base.random_seed)

    loaded = load_step(str(novecento_otb4_file), None, novecento_emg, None)
    prep: PreprocessStepOutput = preprocess_step(
        loaded=loaded,
        duration=None,
        manual_roi=False,
        roi=novecento_roi_local,
        rois=None,
        params=base,
        discard_overrides=None,
        bids_root=None,
        bids_entities=None,
        bids_metadata=None,
    )
    decomposed: DecomposeStepOutput = decompose_step(
        prep=prep, params=base, rng=rng, progress_cb=None
    )
    pre_dedup_count = sum(f.shape[1] for f in decomposed.mu_filters.values() if f.size > 0)

    variants: dict[str, PostprocessStepOutput] = {}
    for label, mode in (
        ("adaptive", POSTPROCESS_MODES["adaptive"]),
        ("full_trace", POSTPROCESS_MODES["full-trace"]),
    ):
        for tag, thresh in (("_on", _DEDUP_ON_THRESH), ("_off", _DEDUP_OFF_THRESH)):
            params = DecompositionParameters(
                niter=_NITER,
                peel_off_enabled=True,
                auto_mask_artifacts=_AUTO_MASK,
                duplicatesthresh=thresh,
                use_adaptive=mode["use_adaptive"],
                full_trace=mode["full_trace"],
            )
            variants[label + tag] = postprocess_step(
                prep=prep, decomposed=decomposed, params=params, progress_cb=None
            )

    return {
        "roi": novecento_roi_local,
        "fsamp": float(prep.fsamp),
        "ngrid": prep.ngrid,
        "n_samples": prep.data.shape[1],
        "pre_dedup_count": pre_dedup_count,
        **variants,
    }


# ── Shared validation helpers ────────────────────────────────────────────────


def _nonzero_samples_outside_roi(post: PostprocessStepOutput, roi: tuple[int, int]) -> int:
    """Count samples with any pulse activity lying outside the ROI window."""
    nz = np.any(post.pulse_t != 0, axis=0)
    roi_start, roi_end = roi
    return int(np.count_nonzero(nz[:roi_start]) + np.count_nonzero(nz[roi_end:]))


def _assert_structure(
    post: PostprocessStepOutput,
    n_samples: int,
    fsamp: float,
    roi: tuple[int, int],
    min_isi_sec: float,
) -> None:
    """Assert the postprocess payload is internally consistent."""
    # Both branches pick spikes with POSTPROC_MIN_ISI_SEC: the separator is fitted
    # by now, so the stricter in-loop DECOMP_MIN_ISI_SEC no longer applies.
    n_mu = len(post.distime)
    assert n_mu > 0, "postprocess produced zero motor units"
    assert post.pulse_t.shape[0] == n_mu
    assert post.pulse_t.shape[1] == n_samples
    assert len(post.mu_grid_index) == n_mu
    assert len(post.sil) == n_mu
    assert np.isfinite(post.pulse_t).all(), "pulse trains contain non-finite values"
    roi_start, roi_end = roi
    silent = [
        i
        for i, raw in enumerate(post.distime)
        if not np.any((np.asarray(raw) >= roi_start) & (np.asarray(raw) < roi_end))
    ]
    assert len(silent) <= _MAX_ROI_SILENT_FRAC * n_mu, (
        f"{len(silent)}/{n_mu} MUs never discharge inside the ROI: {silent}"
    )

    refractory = int(np.round(fsamp * min_isi_sec))
    for i, raw in enumerate(post.distime):
        d = np.asarray(raw)
        if d.size == 0:
            continue
        assert np.all(np.diff(d) >= 0), f"MU {i}: discharge times not sorted"
        assert d.min() >= 0 and d.max() < n_samples, f"MU {i}: discharge times out of bounds"
        if d.size >= 2:
            assert int(np.diff(d).min()) >= refractory, (
                f"MU {i}: min ISI {int(np.diff(d).min())} < refractory {refractory}"
            )


def _record_dedup_effect(
    label: str,
    post_on: PostprocessStepOutput,
    post_off: PostprocessStepOutput,
    variants: dict[str, Any],
) -> None:
    """Measure how much the default dedup threshold removed, and check invariants."""
    n_on = len(post_on.distime)
    n_off = len(post_off.distime)
    pre = variants["pre_dedup_count"]

    record(
        "dedup",
        caption=(
            "Duplicate removal by postprocess branch "
            f"(threshold {_DEDUP_ON_THRESH} vs disabled {_DEDUP_OFF_THRESH})"
        ),
        branch=label,
        pre_dedup_filters=pre,
        dedup_disabled=n_off,
        dedup_enabled=n_on,
        removed=n_off - n_on,
        removed_pct=(100.0 * (n_off - n_on) / n_off) if n_off else 0.0,
    )

    assert n_off <= pre, (
        f"{label}: dedup-disabled retained {n_off} MUs but decompose only produced {pre}"
    )
    assert n_on <= n_off, f"{label}: dedup increased MU count: {n_on} > {n_off}"

    assert len(post_off.sil) == n_off
    assert len(post_on.sil) == n_on


# ── Tests ────────────────────────────────────────────────────────────────────


class TestEnforceRefractory:
    """The seam guard the adaptive branch applies to its per-batch spike picks."""

    def test_keeps_the_larger_peak_of_a_too_close_pair(self) -> None:
        values = np.zeros(100)
        values[[40, 44]] = [1.0, 3.0]
        kept = enforce_refractory(np.array([40, 44]), values, fsamp=2000.0, min_isi_sec=0.005)
        assert kept.tolist() == [44]

    def test_spikes_a_refractory_period_apart_are_kept(self) -> None:
        values = np.ones(100)
        # 10 samples = 5 ms at 2 kHz, exactly the refractory distance.
        kept = enforce_refractory(np.array([40, 50]), values, fsamp=2000.0, min_isi_sec=0.005)
        assert kept.tolist() == [40, 50]

    def test_a_kept_spike_gates_the_ones_after_it(self) -> None:
        """Survivors are compared against the last kept spike, not the last seen one."""
        values = np.zeros(100)
        values[[40, 43, 46]] = [3.0, 1.0, 2.0]
        kept = enforce_refractory(np.array([40, 43, 46]), values, fsamp=2000.0, min_isi_sec=0.005)
        assert kept.tolist() == [40]

    def test_short_trains_pass_through(self) -> None:
        values = np.ones(100)
        assert enforce_refractory(np.array([7]), values, fsamp=2000.0).tolist() == [7]
        assert enforce_refractory(np.array([], dtype=int), values, fsamp=2000.0).size == 0


class TestBatchSeamDetection:
    """A discharge peaking on a batch edge must still be detected."""

    # Identity whitening with adaptation off, so the peak picker alone decides.
    # On real data these edges silently cost ~1% of all discharges.

    @staticmethod
    def _run(peak_positions: list[int]) -> np.ndarray:
        fsamp, n_samples = 2000, 1000
        emg = np.zeros((n_samples, 1), dtype=np.float32)
        for pos in peak_positions:
            emg[pos, 0] = 1.0
            emg[pos - 1, 0] = 0.4
            emg[pos + 1, 0] = 0.4

        config = Config(
            fsamp=fsamp, ex_factor=1, batch_ms=100, adapt_wh=False, adapt_sv=False, adapt_sd=False
        )
        rng = np.random.default_rng(0)
        _ipts, spikes, _losses = run_adaptive_decomposition(
            emg=emg,
            whitening=np.eye(1, dtype=np.float32),
            sep_vectors=np.ones((1, 1), dtype=np.float32),
            base_centr=np.array([0.04], dtype=np.float32),
            spikes_centr=np.array([1.0], dtype=np.float32),
            emg_calib=rng.normal(0, 0.05, size=(400, 1)).astype(np.float32),
            config=config,
        )
        return np.where(spikes[:, 0] > 0)[0]

    def test_peak_inside_a_batch_is_detected(self) -> None:
        """Control: the same peak away from any edge is found."""
        assert self._run([300]).tolist() == [300]

    def test_peak_on_the_first_sample_of_a_batch_is_detected(self) -> None:
        # batch_size = 200 samples, so 200 and 400 open a batch.
        assert self._run([200, 400]).tolist() == [200, 400]

    def test_peak_on_the_last_sample_of_a_batch_is_detected(self) -> None:
        # 199 and 399 close a batch.
        assert self._run([199, 399]).tolist() == [199, 399]


class TestPostprocessBranches:
    """Validate the adaptive and full-trace post-processing branches + dedup."""

    def test_postprocess_adaptive_decomp(self, postprocess_variants: dict[str, Any]) -> None:
        """Adaptive batch post-processing spans the full trace and deduplicates MUs."""
        post_on = postprocess_variants["adaptive_on"]
        post_off = postprocess_variants["adaptive_off"]
        roi = postprocess_variants["roi"]

        assert _nonzero_samples_outside_roi(post_on, roi) > 0, (
            "adaptive postprocess left no activity outside the ROI (windowed branch ran instead)"
        )

        # Check the dedup-disabled run too: dedup would collapse a grid of
        # broken filters into one unit and hide it.
        for post in (post_on, post_off):
            _assert_structure(
                post,
                postprocess_variants["n_samples"],
                postprocess_variants["fsamp"],
                roi,
                POSTPROC_MIN_ISI_SEC,
            )
        _record_dedup_effect("adaptive", post_on, post_off, postprocess_variants)

    def test_postprocess_full_trace(self, postprocess_variants: dict[str, Any]) -> None:
        """Full-trace post-processing spans the full trace and deduplicates MUs."""
        post_on = postprocess_variants["full_trace_on"]
        post_off = postprocess_variants["full_trace_off"]
        roi = postprocess_variants["roi"]

        assert _nonzero_samples_outside_roi(post_on, roi) > 0, (
            "full-trace postprocess left no activity outside the ROI (windowed branch ran instead)"
        )

        # Check the dedup-disabled run too: dedup would collapse a grid of
        # broken filters into one unit and hide it.
        for post in (post_on, post_off):
            _assert_structure(
                post,
                postprocess_variants["n_samples"],
                postprocess_variants["fsamp"],
                roi,
                POSTPROC_MIN_ISI_SEC,
            )
        _record_dedup_effect("full_trace", post_on, post_off, postprocess_variants)
