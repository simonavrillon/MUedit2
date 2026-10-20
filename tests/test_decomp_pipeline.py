"""Tests for :mod:`muedit.decomp.pipeline`: a whole run, on the heap and in a session store."""

from __future__ import annotations

import numpy as np
import pytest

from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.types import DecompositionParameters, DecompositionResult
from muedit.models import resident_nbytes
from muedit.signal.decomp_primitives import POSTPROC_MIN_ISI_SEC
from muedit.storage import SessionStore
from tests._platform import MAPPED_FILES_STAY
from tests._synthetic_emg import ACTIVE_ROI, synthetic_recording

_STORE_PARAMS = DecompositionParameters(niter=8)


def _store_files(store: SessionStore) -> list[str]:
    return sorted(p.name.split("-")[0].removesuffix(".npy") for p in store.path.glob("*.npy"))


def _run(store: SessionStore | None) -> DecompositionResult:
    return run_decomposition(
        "synthetic.mat",
        params=_STORE_PARAMS,
        save_npz=False,
        rois=[ACTIVE_ROI],
        include_full_preview=True,
        preloaded_signal=synthetic_recording(),
        store=store,
    )


@pytest.fixture(scope="module")
def heap_run() -> DecompositionResult:
    return _run(None)


def test_a_run_gives_one_clean_entry_per_motor_unit(heap_run: DecompositionResult) -> None:
    """Pulse trains span the recording; discharges are sorted, in range and refractory-clean."""
    n_mu = len(heap_run.spike_times)
    total_samples = heap_run.preview["total_samples"]
    assert n_mu > 0
    assert heap_run.pulse_trains.shape == (n_mu, total_samples)
    assert np.isfinite(heap_run.pulse_trains).all()
    assert len(heap_run.sil) == len(heap_run.mu_grid_index) == n_mu
    assert min(heap_run.sil) >= _STORE_PARAMS.sil_thr
    refractory = int(np.round(heap_run.fsamp * POSTPROC_MIN_ISI_SEC))
    for spikes in heap_run.spike_times:
        assert np.all(np.diff(spikes) >= refractory)
        assert spikes.size == 0 or 0 <= spikes[0] <= spikes[-1] < total_samples


def test_a_run_in_a_store_matches_the_heap_run(heap_run: DecompositionResult) -> None:
    store = SessionStore.create("run")
    stored = _run(store)
    np.testing.assert_array_equal(stored.pulse_trains, heap_run.pulse_trains)
    for got, want in zip(stored.spike_times, heap_run.spike_times, strict=True):
        np.testing.assert_array_equal(got, want)
    assert resident_nbytes(stored.preview["pulse_trains_full"]) == 0
    # Only the kept pulse trains outlive the run: the filtered EMG and the
    # pre-dedup matrix were deleted.
    assert MAPPED_FILES_STAY or _store_files(store) == ["pulse_trains"]
