"""Tests for :mod:`muedit.decomp.adaptation`: the batch-by-batch adaptive decomposition."""

from __future__ import annotations

import numpy as np
import pytest

from muedit.decomp.adaptation import AdaptiveDecomp, Calibration
from muedit.decomp.types import DecompositionParameters
from muedit.signal.streaming import StreamedExtender
from tests._synthetic_emg import EX, calibration_case, short_recording


@pytest.fixture(scope="module")
def raw() -> np.ndarray:
    return short_recording()


def _model(
    raw: np.ndarray, calibration: tuple[int, int] | Calibration, params: DecompositionParameters
) -> AdaptiveDecomp:
    """A pass over ``raw`` with the whitening and filters fitted on samples 200-2600."""
    whiten_mat, mu_filters, win_mean = calibration_case(raw)
    return AdaptiveDecomp(
        source=StreamedExtender(raw, EX, offset=win_mean, dtype=np.float32, samples_first=True),
        whiten_mat=whiten_mat,
        mu_filters=mu_filters,
        calibration=calibration,
        params=params,
        fsamp=2000,
    )


def test_backward_pass_reads_complete_lookback(raw: np.ndarray) -> None:
    """Frozen, a backward pass is a plain projection of complete samples, in any batch order."""
    params = DecompositionParameters(
        adapt_batch_ms=50, adapt_wh=False, adapt_sv=False, adapt_sd=False
    )
    model = _model(raw, (200, 2600), params)
    starts: list[int] = []
    pulse_trains = np.full((raw.shape[1], model.n_mu), np.nan, dtype=np.float32)

    def sink(start: int, batch: np.ndarray, _spikes: np.ndarray) -> None:
        starts.append(start)
        pulse_trains[start : start + len(batch)] = batch

    stop = 2345  # not a multiple of the batch size: the partial batch is processed last
    model.run(0, stop, reverse=True, sink=sink)

    assert starts == sorted(starts, reverse=True)
    assert starts[-1] == EX - 1, "the recording start is never processed"
    assert np.isnan(pulse_trains[: EX - 1]).all() and np.isnan(pulse_trains[stop:]).all()
    # One read of the whole span, against the batches each read with their own look-back.
    ext = model.source.read(EX - 1, stop)
    want = (model.mu_filters.T @ (model.whiten_mat @ ext.T)).T
    np.testing.assert_allclose(
        pulse_trains[EX - 1 : stop], want, rtol=1e-4, atol=1e-5 * np.abs(want).max()
    )


def test_backward_pass_starts_from_the_calibration_centroids(raw: np.ndarray) -> None:
    """The forward pass adapts its centroids; the backward pass starts from the fitted ones."""
    params = DecompositionParameters(adapt_batch_ms=50)
    fwd = _model(raw, (200, 2600), params)
    fitted = (fwd.base_centr.copy(), fwd.spikes_centr.copy())
    fwd.run(200, raw.shape[1])
    assert not (
        np.array_equal(fwd.base_centr, fitted[0]) and np.array_equal(fwd.spikes_centr, fitted[1])
    )
    bwd = _model(raw, fwd.calibration, params)
    np.testing.assert_array_equal(bwd.base_centr, fitted[0])
    np.testing.assert_array_equal(bwd.spikes_centr, fitted[1])


def test_discharges_on_batch_edges_are_detected() -> None:
    """Peaks on the last and first sample of a batch are found like one inside it."""
    # Identity whitening with adaptation off, so the peak picker alone decides.
    # On real data these edges silently cost ~1% of all discharges.
    peaks = [199, 300, 400]  # batches of 200 samples: 199 closes one, 400 opens one
    emg = np.zeros((1, 1000), dtype=np.float32)
    for pos in peaks:
        emg[0, pos - 1 : pos + 2] = (0.4, 1.0, 0.4)
    model = AdaptiveDecomp(
        source=StreamedExtender(emg, 1, dtype=np.float32, samples_first=True),
        whiten_mat=np.eye(1, dtype=np.float32),
        mu_filters=np.ones((1, 1), dtype=np.float32),
        calibration=(0, 100),  # silent: no peaks to fit centroids on
        params=DecompositionParameters(adapt_wh=False, adapt_sv=False, adapt_sd=False),
        fsamp=2000,
    )
    model._set_centroids(np.array([0.04]), np.array([1.0]))
    found: list[int] = []

    def sink(start: int, _pulse_trains: np.ndarray, spikes: np.ndarray) -> None:
        found.extend(start + np.flatnonzero(spikes[:, 0]))

    model.run(sink=sink)
    assert found == peaks
