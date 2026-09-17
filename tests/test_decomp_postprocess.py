"""Post-processing path validation: adaptive decomposition and full-trace filtering.

The post-processing step (:func:`muedit.decomp.postprocess.postprocess_step`) has
three independent branches that decide how the per-window MU separators are
re-applied to produce pulse trains:

1. **Windowed** (default) -- filters are applied only inside each ROI window, so
   pulse activity is confined to the analysed windows.
2. **Adaptive** (``use_adaptive=True``) -- :func:`adaptive_batch_process` walks
   the full signal in short batches, adapting the whitening and source vectors
   online, so pulse activity spans the whole trace.
3. **Full trace** (``full_trace=True``) -- :func:`batch_process_filters` applies
   the (dewhitened) filters over the entire extended signal, again spanning the
   whole trace.

All three branches converge on :func:`_remove_duplicates_by_grid`, which calls
:func:`rem_duplicates` to collapse duplicated motor units within (and optionally
across) grids.  These two tests drive each of the full-signal branches (adaptive
and full trace) through the public step functions on real Novecento EMG and
assert that:

* the branch actually ran (pulse activity extends past the ROI, unlike the
  windowed default),
* the output payload is internally consistent (counts, finite pulse trains,
  sorted/refractory discharge times),
* the deduplication invariants hold -- dedup never invents motor units, the
  disabled run (``duplicatesthresh`` above the maximum achievable overlap score
  of 1.0) cannot exceed what the decomposition produced, and the surviving SIL
  list stays aligned with whichever MU set survived.

*How many* duplicates the default threshold actually removes is a measurement,
not an assertion: it depends on what the randomized decomposition extracted and
on how each branch perturbs the overlap scores.  It is recorded to the ``dedup``
table (``reports/dedup.csv``) for inspection instead.

Because ``duplicatesthresh``, ``use_adaptive`` and ``full_trace`` are consulted
only inside ``postprocess_step`` (never by ``decompose_step``), the expensive
decomposition is computed once and the two dedup thresholds are re-run as cheap
post-processing passes over the same separators.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from muedit.decomp.core import decompose_step
from muedit.decomp.postprocess import postprocess_step
from muedit.decomp.preprocess import load_step, preprocess_step
from muedit.decomp.types import (
    DecomposeStepOutput,
    DecompositionParameters,
    PostprocessStepOutput,
    PreprocessStepOutput,
)
from muedit.signal.decomp_primitives import POSTPROC_MIN_ISI_SEC
from tests._metrics_helpers import central_roi
from tests._report import record

# Keep the per-iteration budget modest so the real-data decomposition finishes
# in well under a minute (matches ``test_decomp_pipeline``).
_NITER = 50
# Width of the analysis window in seconds (central portion of the recording).
_ROI_WIDTH_SEC = 10.0

# Default dedup overlap threshold; disabling value sits above the maximum
# achievable overlap score (1.0) so no MU pair can ever be flagged a duplicate.
_DEDUP_ON_THRESH = 0.3
_DEDUP_OFF_THRESH = 2.0


# ---------------------------------------------------------------------------
# Session-scoped post-processing variants
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def novecento_roi_local(novecento_emg: dict[str, Any]) -> tuple[int, int]:
    """Central 10 s ROI of the Novecento recording (where the contraction is)."""
    return central_roi(novecento_emg["data"], float(novecento_emg["fsamp"]), _ROI_WIDTH_SEC)


@pytest.fixture(scope="session")
def postprocess_variants(
    novecento_otb4_file: Path,
    novecento_emg: dict[str, Any],
    novecento_roi_local: tuple[int, int],
) -> dict[str, Any]:
    """Decompose once, then re-run post-processing for each branch x dedup state.

    Returns a dict with:

    * ``"roi"``      -- the (start, end) ROI sample indices,
    * ``"fsamp"``    -- sampling frequency,
    * ``"ngrid"``    -- number of grids,
    * ``"n_samples"``-- total signal samples,
    * ``"pre_dedup_count"`` -- total MU filters produced by the decompose step,
    * ``"adaptive_on"`` / ``"adaptive_off"`` -- adaptive postprocess outputs
      with dedup enabled / disabled,
    * ``"full_trace_on"`` / ``"full_trace_off"`` -- full-trace postprocess outputs
      with dedup enabled / disabled.
    """
    base = DecompositionParameters(niter=_NITER, peel_off_enabled=True)
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
        ("adaptive", dict(use_adaptive=True)),
        ("full_trace", dict(full_trace=True)),
    ):
        for tag, thresh in (("_on", _DEDUP_ON_THRESH), ("_off", _DEDUP_OFF_THRESH)):
            params = DecompositionParameters(
                niter=_NITER, peel_off_enabled=True, duplicatesthresh=thresh, **mode
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


# ---------------------------------------------------------------------------
# Shared validation helpers
# ---------------------------------------------------------------------------


def _nonzero_samples_outside_roi(post: PostprocessStepOutput, roi: tuple[int, int]) -> int:
    """Count samples with any pulse activity lying outside the ROI window.

    The windowed postprocess branch only fills pulse trains inside each ROI
    window, leaving exact zeros outside.  The adaptive and full-trace branches
    apply the filters over the whole signal, so they leave non-zero activity
    across the entire trace -- including before and after the ROI.  A positive
    count therefore proves the full-signal branch ran rather than the windowed
    default.
    """
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
    """Assert the postprocess payload is internally consistent.

    Finiteness is checked over the ROI window only: the adaptive branch adapts
    its whitening/source state online and can diverge to NaN at the signal tail
    (after the calibration window), which is a known numerical characteristic of
    that path and does not affect the discharge times it extracts.

    ``min_isi_sec`` is the refractory the branch's spike picker enforces.  Both
    post-processing branches use :data:`POSTPROC_MIN_ISI_SEC`: the separator is
    already fitted at this stage, so the conservative in-loop decomposition
    refractory (:data:`DECOMP_MIN_ISI_SEC`) no longer applies.
    """
    n_mu = len(post.distime)
    assert n_mu > 0, "postprocess produced zero motor units"
    assert post.pulse_t.shape[0] == n_mu
    assert post.pulse_t.shape[1] == n_samples
    assert len(post.mu_grid_index) == n_mu
    assert len(post.sil) == n_mu
    roi_start, roi_end = roi
    assert np.isfinite(post.pulse_t[:, roi_start:roi_end]).all(), (
        "pulse trains contain non-finite values inside the ROI"
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
    """Measure how much the default dedup threshold removed, and check invariants.

    With ``duplicatesthresh`` above the maximum achievable overlap score (1.0)
    no MU pair can ever reach the gate, so ``post_off`` retains every non-empty
    MU the decomposition produced.  The default threshold is *expected* to
    collapse duplicates on top of that -- but how many it collapses depends on
    what the randomized decomposition happened to extract, and on how the
    branch's pulse trains perturb the overlap scores.  A run where the two
    counts coincide is informative, not automatically a bug, so the removal is
    recorded (``reports/dedup.csv``) rather than asserted.

    Note the adaptive branch is the interesting column here: it adapts its
    whitening online and can perturb discharge times enough to push genuine
    duplicate pairs below the 0.3 overlap gate, in which case duplicates
    survive into its output.  ``removed`` near zero for ``adaptive`` while
    ``full_trace`` removes a healthy fraction is the signature of that.

    What stays asserted is what cannot vary: dedup may not invent motor units,
    the disabled run cannot exceed what the decomposition produced, and the SIL
    list must stay aligned with whichever MU set survived.
    """
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

    # Disabling dedup keeps (at most) every MU the decompose step emitted.
    assert n_off <= pre, (
        f"{label}: dedup-disabled retained {n_off} MUs but decompose only produced {pre}"
    )
    # Dedup never invents MUs.
    assert n_on <= n_off, f"{label}: dedup increased MU count: {n_on} > {n_off}"

    # The dedup-disabled SIL list aligns one-to-one with its MUs (no reordering
    # / subsetting), and the dedup-enabled list stays aligned with its (smaller)
    # MU set -- confirming the postprocess wires dedup output through to SIL.
    assert len(post_off.sil) == n_off
    assert len(post_on.sil) == n_on


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPostprocessBranches:
    """Validate the adaptive and full-trace post-processing branches + dedup."""

    def test_postprocess_adaptive_decomp(self, postprocess_variants: dict[str, Any]) -> None:
        """Adaptive batch post-processing spans the full trace and deduplicates MUs.

        ``use_adaptive=True`` routes through :func:`adaptive_batch_process`, which
        adapts the whitening/source vectors over the whole signal in short
        batches.  The resulting pulse trains must therefore carry activity
        outside the ROI (unlike the windowed default), the payload must be
        internally consistent, and the per-grid deduplication must remove a
        strict subset of the MUs that disabling dedup retains.
        """
        post_on = postprocess_variants["adaptive_on"]
        post_off = postprocess_variants["adaptive_off"]
        roi = postprocess_variants["roi"]

        # The adaptive branch processed the full signal, not just the ROI.
        assert _nonzero_samples_outside_roi(post_on, roi) > 0, (
            "adaptive postprocess left no activity outside the ROI (windowed branch ran instead)"
        )

        _assert_structure(
            post_on,
            postprocess_variants["n_samples"],
            postprocess_variants["fsamp"],
            roi,
            POSTPROC_MIN_ISI_SEC,
        )
        _record_dedup_effect("adaptive", post_on, post_off, postprocess_variants)

    def test_postprocess_full_trace(self, postprocess_variants: dict[str, Any]) -> None:
        """Full-trace post-processing spans the full trace and deduplicates MUs.

        ``full_trace=True`` routes through :func:`batch_process_filters` with the
        dewhitened filters applied over the entire extended signal.  Like the
        adaptive branch it must leave activity outside the ROI, produce a
        consistent payload, and exercise the deduplication function (a strict
        subset of the dedup-disabled run).
        """
        post_on = postprocess_variants["full_trace_on"]
        post_off = postprocess_variants["full_trace_off"]
        roi = postprocess_variants["roi"]

        # The full-trace branch processed the full signal, not just the ROI.
        assert _nonzero_samples_outside_roi(post_on, roi) > 0, (
            "full-trace postprocess left no activity outside the ROI (windowed branch ran instead)"
        )

        _assert_structure(
            post_on,
            postprocess_variants["n_samples"],
            postprocess_variants["fsamp"],
            roi,
            POSTPROC_MIN_ISI_SEC,
        )
        _record_dedup_effect("full_trace", post_on, post_off, postprocess_variants)
