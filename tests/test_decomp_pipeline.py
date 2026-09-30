"""End-to-end decomposition pipeline tests on real HD-EMG data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.types import DecompositionParameters
from muedit.models import SignalImport
from muedit.signal.decomp_primitives import POSTPROC_MIN_ISI_SEC
from tests._metrics_helpers import central_roi, discharge_times

# Keep the per-iteration budget modest so the real-data run finishes in well
# under a minute while still exercising the full FastICA + CoV-ISI + peel-off
# loop on all six grids.
_NITER = 50
# Width of the analysis window in seconds (central portion of the recording).
_ROI_WIDTH_SEC = 10.0
# A recording must yield at least this many motor units for the pipeline to be
# considered functional on the 10 s window.
_MIN_MU_COUNT = 5


# ── Session-scoped pipeline results ──────────────────────────────────────────


@pytest.fixture(scope="session")
def novecento_roi(novecento_emg: SignalImport) -> tuple[int, int]:
    """Central 10 s ROI of the Novecento recording (where the contraction is)."""
    return central_roi(novecento_emg.data, float(novecento_emg.fsamp), _ROI_WIDTH_SEC)


@pytest.fixture(scope="session")
def decomp_results(
    novecento_otb4_file: Path, novecento_emg: SignalImport, novecento_roi: tuple[int, int]
) -> dict[str, Any]:
    """Run the full pipeline with peel-off on real Novecento EMG."""
    result, _save_path = run_decomposition(
        str(novecento_otb4_file),
        roi=novecento_roi,
        params=DecompositionParameters(niter=_NITER, peel_off_enabled=True),
        save_npz=False,
        preloaded_signal=novecento_emg,
    )
    return result


# ── Pipeline structure & output validity ─────────────────────────────────────


class TestDecompositionStructure:
    """Validate the shape and internal consistency of the export payload."""

    def test_counts_are_consistent(self, decomp_results: dict[str, Any]) -> None:
        result = decomp_results
        assert {"signal", "sil", "mu_grid_index", "parameters"} <= set(result)
        assert {"data", "fsamp", "PulseT", "Dischargetimes"} <= set(result["signal"])
        dt = discharge_times(result)
        pulse_t = np.asarray(result["signal"]["PulseT"])
        sil = result["sil"]
        grid_idx = result["mu_grid_index"]

        n_mu = len(dt)
        assert n_mu > 0, "pipeline produced zero motor units"
        assert pulse_t.shape[0] == n_mu
        assert len(sil) == n_mu
        assert len(grid_idx) == n_mu

    def test_pulse_trains_match_signal_length(self, decomp_results: dict[str, Any]) -> None:
        result = decomp_results
        pulse_t = np.asarray(result["signal"]["PulseT"])
        data = np.asarray(result["signal"]["data"])
        assert pulse_t.ndim == 2
        # Pulse trains span the full signal; the ``roi`` argument only selects
        # the ROI window that gets *filled* with non-zero activity.
        assert pulse_t.shape[1] == data.shape[1]
        assert np.isfinite(pulse_t).all()


# ── FastICA + min-CoV-ISI quality of detected motor units ────────────────────


class TestMotorUnitQuality:
    """Assert the separators the pipeline accepted are genuinely motor units."""

    def test_finds_multiple_motor_units(self, decomp_results: dict[str, Any]) -> None:
        dt = discharge_times(decomp_results)
        assert len(dt) >= _MIN_MU_COUNT, f"expected >= {_MIN_MU_COUNT} motor units, got {len(dt)}"

    def test_sil_above_acceptance_threshold(self, decomp_results: dict[str, Any]) -> None:
        result = decomp_results
        sil = result["sil"]
        sil_thr = result["parameters"]["sil_thr"]
        assert sil, "no SIL scores returned"
        assert min(sil) >= sil_thr, f"min SIL {min(sil):.3f} below acceptance threshold {sil_thr}"

    def test_discharge_times_respect_refractory(
        self, decomp_results: dict[str, Any], novecento_roi: tuple[int, int]
    ) -> None:
        """Every MU spike train is sorted, in-bounds, and refractory-clean."""
        result = decomp_results
        fsamp = float(result["signal"]["fsamp"])
        n_samples = np.asarray(result["signal"]["data"]).shape[1]
        refractory = int(np.round(fsamp * POSTPROC_MIN_ISI_SEC))

        for i, d in enumerate(discharge_times(result)):
            if d.size == 0:
                continue
            assert np.all(np.diff(d) >= 0), f"MU {i}: discharge times not sorted"
            assert d.min() >= 0 and d.max() < n_samples, (
                f"MU {i}: discharge times out of [0, {n_samples})"
            )
            if d.size >= 2:
                min_isi = int(np.diff(d).min())
                assert min_isi >= refractory, (
                    f"MU {i}: min ISI {min_isi} < refractory {refractory} samples"
                )
