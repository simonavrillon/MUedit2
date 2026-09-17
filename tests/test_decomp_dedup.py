"""Validation tests for motor-unit deduplication: within-grid and cross-grid.

Deduplication runs in two stages, both built on the lag-aware overlap test in
:func:`muedit.decomp.algorithm.rem_duplicates`:

1. **Within each grid** -- :func:`muedit.decomp.postprocess._remove_duplicates_by_grid`
   calls ``rem_duplicates`` once per grid so duplicate motor units extracted from
   the *same* grid are collapsed to a single representative (the one with the
   lowest ISI coefficient of variation).
2. **Between grids** -- when ``DecompositionParameters.duplicatesbgrids`` is set,
   the survivors of every grid are stacked and ``rem_duplicates`` is run again so
   duplicates that were extracted from *different* grids are collapsed too.

The ground-truth spike trains embedded in the simulated HD-EMG datasets
(``data/simulation_*_surface.mat_decomp.mat_edited.mat``, 150 motor units at
10 240 Hz) give realistic, regular discharge patterns.  Each test picks a set
of mutually-distinct ground-truth MUs as "originals" and injects controlled
duplicates -- exact copies, lag-shifted copies, jittered copies, and spike
subsets -- to exercise both dedup stages.

The simulated ``.mat`` files are git-ignored, so the fixtures skip cleanly when
the data is absent (handled by the ``simulation_loaded`` conftest fixture).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from muedit.decomp.algorithm import (
    DEDUP_JITTER,
    DEDUP_MAXLAG_RATIO,
    rem_duplicates,
)
from muedit.decomp.postprocess import _remove_duplicates_by_grid
from muedit.decomp.types import DecompositionParameters
from muedit.signal.decomp_primitives import isi_cov

# Default overlap score above which a MU pair is flagged a duplicate.
_DEDUP_TOL = 0.3

# Central 10 s contraction window of the simulated recording (samples).
_SIM_ROI = (10240, 112639)

# Minimum/maximum spike count a ground-truth MU must have inside the ROI to be
# considered as a candidate "original" -- enough spikes for a stable CoV, few
# enough that the per-pair lag search stays cheap.
_MIN_SPIKES, _MAX_SPIKES = 30, 120

# Excitation level used for the dedup datasets (mid-level: 126 active GT MUs).
_SIM_PCT = 40


# ---------------------------------------------------------------------------
# Realistic pulse-train + duplicate construction helpers
# ---------------------------------------------------------------------------


def _gt_in_roi(sim: dict[str, Any], mu: int) -> np.ndarray:
    """Return ground-truth spike sample indices for MU ``mu``, shifted to ROI-local time."""
    roi_start, roi_end = _SIM_ROI
    s = np.asarray(sim["gt_spike_times"][mu], dtype=int)
    s = s[(s >= roi_start) & (s < roi_end)]
    return s - roi_start


def _make_pulse_train(spike_times: np.ndarray, n_samples: int, fsamp: float) -> np.ndarray:
    """Build a realistic non-negative pulse train by stamping a Gaussian MUAP at each spike.

    The dedup function only reads ``pulse_t`` for its shape and to echo the kept
    rows back, so the waveform content is cosmetic -- but using a MUAP-shaped
    kernel keeps the test data realistic rather than a sparse 0/1 raster.
    """
    pt = np.zeros(n_samples, dtype=float)
    half = int(round(0.0025 * fsamp))  # ~2.5 ms half-width
    t = np.arange(-half, half + 1)
    kernel = np.exp(-(t**2) / (2.0 * (half / 3.0) ** 2))
    for s in np.asarray(spike_times, dtype=int):
        if s - half >= 0 and s + half < n_samples:
            pt[s - half : s + half + 1] += kernel
    return pt


def _lagged(spikes: np.ndarray, lag: int, n_samples: int) -> np.ndarray:
    """Return ``spikes + lag`` clipped to ``[0, n_samples)``."""
    shifted = spikes + lag
    return shifted[(shifted >= 0) & (shifted < n_samples)]


def _jittered(
    spikes: np.ndarray, rng: np.random.Generator, max_j: int, n_samples: int
) -> np.ndarray:
    """Return ``spikes`` perturbed per-spike by up to ``+/- max_j`` samples."""
    perturbed = spikes + rng.integers(-max_j, max_j + 1, size=spikes.size)
    perturbed = np.sort(perturbed)
    return perturbed[(perturbed >= 0) & (perturbed < n_samples)]


def _subset(spikes: np.ndarray, rng: np.random.Generator, drop_frac: float) -> np.ndarray:
    """Return ``spikes`` with a ``drop_frac`` fraction of spikes removed."""
    keep = rng.random(spikes.size) > drop_frac
    return spikes[keep]


def _jitter_match_count(a: np.ndarray, b: np.ndarray, jitter: int) -> int:
    """Count spikes in ``b`` that fall within ``+/- jitter`` samples of an ``a`` spike.

    Mirrors the jitter-expanded intersection ``rem_duplicates`` uses: a shifted
    spike counts as a match if it lands within the per-spike jitter window of any
    reference spike, not only on an exact sample.  Both trains must be sorted.
    """
    a = np.asarray(a, dtype=int)
    b = np.asarray(b, dtype=int)
    if a.size == 0 or b.size == 0:
        return 0
    pos = np.searchsorted(a, b)
    cnt = 0
    for j in range(b.size):
        for k in (pos[j] - 1, pos[j], pos[j] + 1):
            if 0 <= k < a.size and abs(int(a[k]) - int(b[j])) <= jitter:
                cnt += 1
                break
    return cnt


def _best_lag_overlap(
    a: np.ndarray, b: np.ndarray, max_lag: int, n_samples: int, jitter: int
) -> float:
    """Best-lag, jitter-tolerant overlap fraction between two spike trains.

    Mirrors the agreement metric at the heart of ``rem_duplicates``: for each
    candidate lag in ``[-2*max_lag, 2*max_lag]`` shift ``b`` and count how many of
    its spikes fall within ``+/- jitter`` of an ``a`` spike, then divide by
    ``max(|a|, |b|)``.  A pair flagged as duplicates by ``rem_duplicates`` (exact,
    lagged, jittered, or subset copies) scores ~1.0 here; distinct MUs score
    low.  The jitter window is essential -- the kept representative of a group
    can be a per-spike jittered copy, which no single global lag realigns exactly.
    """
    a = np.asarray(a, dtype=int)
    b = np.asarray(b, dtype=int)
    if a.size == 0 or b.size == 0:
        return 0.0
    best = 0
    for lag in range(-2 * max_lag, 2 * max_lag + 1):
        shifted = b + lag
        shifted = shifted[(shifted >= 0) & (shifted < n_samples)]
        ov = _jitter_match_count(a, shifted, jitter)
        if ov > best:
            best = ov
    return best / max(len(a), len(b))


def _assert_one_survivor_per_original(
    survivors: list[np.ndarray],
    originals: list[np.ndarray],
    max_lag: int,
    n_samples: int,
    jitter: int,
    thresh: float = 0.8,
) -> None:
    """Assert the survivor set is a bijection onto the originals.

    Each survivor must match exactly one original with best-lag overlap >=
    ``thresh``, and every original must be matched exactly once -- i.e. each
    duplicate group was collapsed to a single member near its original, and no
    distinct original was over-merged.  The kept representative of a group is
    the lowest-CoV member, which can be a jittered copy rather than the
    original, so identity is verified via overlap rather than array equality.
    """
    assert len(survivors) == len(originals), (
        f"expected {len(originals)} survivors (one per original), got {len(survivors)}"
    )
    matched_orig: list[int] = []
    for surv in survivors:
        scores = [_best_lag_overlap(surv, o, max_lag, n_samples, jitter) for o in originals]
        best = int(np.argmax(scores))
        assert scores[best] >= thresh, (
            f"survivor matched original {best} with overlap {scores[best]:.3f} < {thresh}"
        )
        matched_orig.append(best)
    assert sorted(matched_orig) == list(range(len(originals))), (
        f"survivors do not map one-to-one onto originals: {sorted(matched_orig)}"
    )


# ---------------------------------------------------------------------------
# Session-scoped dedup datasets built from simulated ground truth
# ---------------------------------------------------------------------------


def _select_distinct_originals(
    sim: dict[str, Any],
    n_needed: int,
    n_samples: int,
    fsamp: float,
    maxlag: int,
    tol: float,
) -> list[int]:
    """Greedily pick mutually-distinct ground-truth MU indices.

    A candidate is only accepted if adding it to the current set leaves the
    count returned by ``rem_duplicates`` unchanged (i.e. it is not itself a
    duplicate of any already-selected original under ``tol``).  This guarantees
    the originals survive dedup on their own, so any collapse in the injected
    dataset is attributable to the injected duplicates, not to the originals
    colliding with each other.
    """
    candidates = [
        m
        for m in range(sim["n_total_mus"])
        if _MIN_SPIKES <= _gt_in_roi(sim, m).size <= _MAX_SPIKES
    ]
    selected: list[int] = []
    for m in candidates:
        trial = [*selected, m]
        dist = [_gt_in_roi(sim, i) for i in trial]
        pt = np.zeros((len(trial), n_samples))
        _, _, kept = rem_duplicates(pt, dist, dist, maxlag, DEDUP_JITTER, tol, fsamp)
        if len(kept) == len(trial):
            selected.append(m)
        if len(selected) >= n_needed:
            break
    assert len(selected) == n_needed, (
        f"could not find {n_needed} mutually-distinct GT MUs (found {len(selected)})"
    )
    return selected


@pytest.fixture(scope="session")
def dedup_ctx(simulation_loaded: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Shared context: fsamp, signal length, max-lag, and distinct original MUs.

    Built once from the 40 %-excitation simulation.  The originals are selected
    greedily so they are mutually non-duplicate; downstream fixtures inject
    copies of them to form the duplicate sets.
    """
    sim = simulation_loaded[_SIM_PCT]
    fsamp = float(sim["fsamp"])
    n_samples = _SIM_ROI[1] - _SIM_ROI[0]
    maxlag = round(fsamp / DEDUP_MAXLAG_RATIO)
    originals = _select_distinct_originals(sim, 5, n_samples, fsamp, maxlag, _DEDUP_TOL)
    return {
        "sim": sim,
        "fsamp": fsamp,
        "n_samples": n_samples,
        "maxlag": maxlag,
        "jitter": int(round(DEDUP_JITTER * fsamp)),
        "originals": originals,  # GT MU indices
    }


@pytest.fixture(scope="session")
def within_grid_dataset(dedup_ctx: dict[str, Any]) -> dict[str, Any]:
    """Within-grid duplicate set: originals + 4 injected copies each, one grid.

    For each of the 5 originals we add an exact copy, a lag-shifted copy, a
    jittered copy, and a 10 %-subset copy -- 25 MUs total, all assigned to
    grid 0.  The 5 originals are mutually distinct, so dedup should collapse
    each 5-member group to a single survivor, leaving exactly 5 MUs.
    """
    sim = dedup_ctx["sim"]
    fsamp = dedup_ctx["fsamp"]
    n_samples = dedup_ctx["n_samples"]
    jit = int(round(DEDUP_JITTER * fsamp))
    rng = np.random.default_rng(0)

    dist: list[np.ndarray] = []
    mu_grid_index: list[int] = []
    for m in dedup_ctx["originals"]:
        o = _gt_in_roi(sim, m)
        dist.append(o.copy())
        dist.append(o.copy())  # exact duplicate
        dist.append(_lagged(o, 50, n_samples))  # lag-shifted duplicate
        dist.append(_jittered(o, rng, jit, n_samples))  # jittered duplicate
        dist.append(_subset(o, rng, 0.1))  # spike-subset duplicate
        mu_grid_index.extend([0] * 5)

    pulse_t = np.array([_make_pulse_train(d, n_samples, fsamp) for d in dist])
    original_trains = [_gt_in_roi(sim, m) for m in dedup_ctx["originals"]]
    return {
        "pulse_t": pulse_t,
        "distime": dist,
        "mu_grid_index": mu_grid_index,
        "original_trains": original_trains,
        "n_originals": len(dedup_ctx["originals"]),
    }


@pytest.fixture(scope="session")
def between_grid_dataset(dedup_ctx: dict[str, Any]) -> dict[str, Any]:
    """Cross-grid duplicate set: originals in grid 0, lagged copies in grid 1.

    4 originals live in grid 0; a lag-shifted copy of each lives in grid 1
    (a duplicate only detectable across grids); plus one *genuinely distinct*
    ground-truth MU in grid 1 that must survive both dedup modes.  9 MUs total.

    With ``duplicatesbgrids=False`` the per-grid pass sees no intra-grid
    duplicates (grid 0 holds only originals, grid 1 holds only copies), so all
    9 survive.  With ``duplicatesbgrids=True`` the cross-grid pass collapses the
    4 lagged copies, leaving 5 survivors (4 originals + the distinct grid-1 MU).
    """
    sim = dedup_ctx["sim"]
    fsamp = dedup_ctx["fsamp"]
    n_samples = dedup_ctx["n_samples"]
    # Take 4 of the 5 originals for grid 0; the 5th serves as the distinct grid-1 MU.
    grid0_mus = dedup_ctx["originals"][:4]
    distinct_mu = dedup_ctx["originals"][4]

    dist: list[np.ndarray] = []
    mu_grid_index: list[int] = []
    for m in grid0_mus:
        o = _gt_in_roi(sim, m)
        dist.append(o.copy())
        mu_grid_index.append(0)
    for m in grid0_mus:
        o = _gt_in_roi(sim, m)
        dist.append(_lagged(o, 80, n_samples))  # cross-grid lagged duplicate
        mu_grid_index.append(1)
    dist.append(_gt_in_roi(sim, distinct_mu))  # distinct MU, not a duplicate
    mu_grid_index.append(1)

    pulse_t = np.array([_make_pulse_train(d, n_samples, fsamp) for d in dist])
    original_trains = [_gt_in_roi(sim, m) for m in grid0_mus] + [_gt_in_roi(sim, distinct_mu)]
    return {
        "pulse_t": pulse_t,
        "distime": dist,
        "mu_grid_index": mu_grid_index,
        "original_trains": original_trains,  # 4 grid-0 originals + 1 distinct grid-1 MU
        "n_originals": 4,
        "n_distinct": 5,  # survivors expected with cross-grid dedup on
        "n_no_cross": 9,  # survivors expected with cross-grid dedup off
    }


# ---------------------------------------------------------------------------
# 1. Core rem_duplicates on small synthetic spike trains
# ---------------------------------------------------------------------------


class TestRemDuplicatesCore:
    """Precise behavioural checks of ``rem_duplicates`` on crafted spike trains.

    Uses small synthetic trains (no simulation data needed) so each duplicate
    variant is unambiguous.  ``fsamp = 10240`` matches the simulation so the
    jitter/max-lag constants resolve to the same sample windows (jit = 3,
    maxlag = 256) used in the real-data tests.
    """

    FSAMP = 10240.0
    N_SAMPLES = 6000
    MAXLAG = round(FSAMP / DEDUP_MAXLAG_RATIO)  # 256

    def _run(self, dist: list[np.ndarray]) -> tuple[int, list[int]]:
        pt = np.zeros((len(dist), self.N_SAMPLES))
        _, _, kept = rem_duplicates(
            pt, dist, dist, self.MAXLAG, DEDUP_JITTER, _DEDUP_TOL, self.FSAMP
        )
        return len(kept), [int(k) for k in kept]

    def _regular(self, period: int, start: int, count: int) -> np.ndarray:
        return np.arange(start, start + period * count, period, dtype=int)

    def test_exact_duplicate_collapsed(self) -> None:
        a = self._regular(200, 100, 20)
        kept, idx = self._run([a, a.copy()])
        assert kept == 1, f"exact duplicate not collapsed: {kept} survivors"
        assert idx == [0]

    def test_lagged_duplicate_collapsed(self) -> None:
        a = self._regular(200, 100, 20)
        kept, _idx = self._run([a, _lagged(a, 30, self.N_SAMPLES)])
        assert kept == 1, f"lagged duplicate not collapsed: {kept} survivors"

    def test_jittered_duplicate_collapsed(self) -> None:
        a = self._regular(200, 100, 20)
        rng = np.random.default_rng(7)
        jit = int(round(DEDUP_JITTER * self.FSAMP))
        kept, _idx = self._run([a, _jittered(a, rng, jit, self.N_SAMPLES)])
        assert kept == 1, f"jittered duplicate not collapsed: {kept} survivors"

    def test_subset_duplicate_collapsed(self) -> None:
        a = self._regular(200, 100, 20)
        rng = np.random.default_rng(3)
        kept, _idx = self._run([a, _subset(a, rng, 0.1)])
        assert kept == 1, f"subset duplicate not collapsed: {kept} survivors"

    def test_distinct_unit_preserved(self) -> None:
        """A low-firing, irregular MU is neither a duplicate of the regular MU nor vice-versa."""
        a = self._regular(200, 100, 20)  # 20 spikes, period 200
        b = np.array([250, 900, 1700, 2600, 3400, 4300, 5200], dtype=int)  # 7 irregular spikes
        kept, idx = self._run([a, b])
        assert kept == 2, f"distinct unit wrongly collapsed: {kept} survivors"
        assert sorted(idx) == [0, 1]

    def test_empty_spike_train_excluded(self) -> None:
        """An MU with no spikes is skipped and never appears in the output."""
        a = self._regular(200, 100, 20)
        kept, idx = self._run([a, np.array([], dtype=int), a.copy()])
        # The empty MU (index 1) is dropped; the a/a-copy pair collapses to one.
        assert kept == 1, f"expected 1 survivor, got {kept}"
        assert 1 not in idx, f"empty-spike MU kept: {idx}"

    def test_kept_representative_is_lowest_cov(self) -> None:
        """Among a duplicate group the lowest-CoV member is the one retained."""
        a = self._regular(200, 100, 20)  # regular, low CoV
        rng = np.random.default_rng(11)
        jit = int(round(DEDUP_JITTER * self.FSAMP))
        jittery = _jittered(a, rng, jit, self.N_SAMPLES)  # jitter raises CoV
        assert isi_cov(jittery, self.FSAMP) > isi_cov(a, self.FSAMP)
        _, idx = self._run([a, jittery])
        assert idx == [0], f"lowest-CoV representative not kept: {idx}"


# ---------------------------------------------------------------------------
# 2. Within-grid deduplication via _remove_duplicates_by_grid
# ---------------------------------------------------------------------------


class TestWithinGridDedup:
    """Per-grid duplicate collapse on realistic pulse trains from simulation."""

    def test_collapses_injected_duplicates(
        self, dedup_ctx: dict[str, Any], within_grid_dataset: dict[str, Any]
    ) -> None:
        """The 5 duplicate groups (5 members each) collapse to exactly 5 survivors."""
        params = DecompositionParameters(duplicatesthresh=_DEDUP_TOL, duplicatesbgrids=False)
        pulse_t, distime, gidx, _ = _remove_duplicates_by_grid(
            within_grid_dataset["pulse_t"],
            within_grid_dataset["distime"],
            within_grid_dataset["mu_grid_index"],
            ngrid=1,
            params=params,
            fsamp=dedup_ctx["fsamp"],
        )
        n_surv = len(distime)
        assert n_surv == within_grid_dataset["n_originals"], (
            f"within-grid dedup left {n_surv} MUs, expected {within_grid_dataset['n_originals']}"
        )
        assert pulse_t.shape[0] == n_surv
        assert len(gidx) == n_surv and all(g == 0 for g in gidx), (
            "survivors should all remain in grid 0"
        )

        _assert_one_survivor_per_original(
            [np.asarray(d) for d in distime],
            within_grid_dataset["original_trains"],
            dedup_ctx["maxlag"],
            dedup_ctx["n_samples"],
            dedup_ctx["jitter"],
        )


# ---------------------------------------------------------------------------
# 3. Between-grid deduplication via _remove_duplicates_by_grid
# ---------------------------------------------------------------------------


class TestBetweenGridDedup:
    """Cross-grid duplicate collapse (duplicatesbgrids on/off) on simulation data."""

    def test_cross_grid_off_keeps_all(
        self, dedup_ctx: dict[str, Any], between_grid_dataset: dict[str, Any]
    ) -> None:
        """With ``duplicatesbgrids=False`` cross-grid duplicates are *not* removed.

        The per-grid pass sees no intra-grid duplicates (grid 0 holds only
        originals, grid 1 holds only lagged copies + one distinct MU), so every
        input MU survives.
        """
        params = DecompositionParameters(duplicatesthresh=_DEDUP_TOL, duplicatesbgrids=False)
        _, distime, gidx, _ = _remove_duplicates_by_grid(
            between_grid_dataset["pulse_t"],
            between_grid_dataset["distime"],
            between_grid_dataset["mu_grid_index"],
            ngrid=2,
            params=params,
            fsamp=dedup_ctx["fsamp"],
        )
        assert len(distime) == between_grid_dataset["n_no_cross"], (
            f"cross-grid-off should keep {between_grid_dataset['n_no_cross']} MUs, "
            f"got {len(distime)}"
        )
        assert gidx.count(0) == 4 and gidx.count(1) == 5, (
            f"grid index counts wrong with cross-grid off: {gidx}"
        )

    def test_cross_grid_on_collapses_duplicates(
        self, dedup_ctx: dict[str, Any], between_grid_dataset: dict[str, Any]
    ) -> None:
        """With ``duplicatesbgrids=True`` the 4 cross-grid lagged copies collapse.

        The 4 grid-0 originals each merge with their grid-1 lagged copy, leaving
        5 survivors: 4 originals (in grid 0) + the one genuinely-distinct grid-1
        MU that has no counterpart in grid 0.
        """
        params = DecompositionParameters(duplicatesthresh=_DEDUP_TOL, duplicatesbgrids=True)
        pulse_t, distime, gidx, _ = _remove_duplicates_by_grid(
            between_grid_dataset["pulse_t"],
            between_grid_dataset["distime"],
            between_grid_dataset["mu_grid_index"],
            ngrid=2,
            params=params,
            fsamp=dedup_ctx["fsamp"],
        )
        n_surv = len(distime)
        assert n_surv == between_grid_dataset["n_distinct"], (
            f"cross-grid-on should leave {between_grid_dataset['n_distinct']} MUs, got {n_surv}"
        )
        # The 4 collapsed originals stay attributed to grid 0; the distinct MU
        # stays in grid 1.
        assert gidx.count(0) == 4, f"expected 4 grid-0 survivors, got {gidx}"
        assert gidx.count(1) == 1, f"expected 1 grid-1 survivor (the distinct MU), got {gidx}"
        assert pulse_t.shape[0] == n_surv

        # The survivors map one-to-one onto the 5 ground-truth originals
        # (4 grid-0 originals + the distinct grid-1 MU).
        _assert_one_survivor_per_original(
            [np.asarray(d) for d in distime],
            between_grid_dataset["original_trains"],
            dedup_ctx["maxlag"],
            dedup_ctx["n_samples"],
            dedup_ctx["jitter"],
        )
