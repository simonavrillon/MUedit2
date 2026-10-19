"""Tests for :mod:`muedit.adapt_decomp.adaptation`: the batch-by-batch adaptive decomposition."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from muedit.adapt_decomp.adaptation import (
    AdaptiveDecomp,
    drop_partial_batch_loss,
    run_adaptive_decomposition,
)
from muedit.adapt_decomp.config import Config
from muedit.decomp.algorithm import pca_extended_signal, whiten_extended_signal
from muedit.signal.decomp_primitives import extend_signal
from muedit.signal.streaming import StreamedExtender
from tests._synthetic_emg import EX, calibration_case, short_recording


@pytest.fixture(scope="module")
def raw() -> np.ndarray:
    return short_recording()


def _adaptive_setup(
    raw: np.ndarray, **config_kw: object
) -> tuple[np.ndarray, dict[str, Any], Config]:
    """Samples-first float32 EMG, calibrated whitening and filters, and a small-batch config."""
    emg = (raw - raw.mean(axis=1, keepdims=True)).T.astype(np.float32)
    calib = emg[:1200]
    ext = extend_signal(calib.T.astype(np.float64), EX)
    vecs, vals = pca_extended_signal(ext)
    _, whitening = whiten_extended_signal(ext, vecs, vals)
    filters = np.linalg.qr(np.random.default_rng(2).standard_normal((ext.shape[0], 3)))[0]
    config = Config(fsamp=2000, ex_factor=EX, batch_ms=50, **config_kw)  # type: ignore[arg-type]
    state = {
        "whitening": whitening,
        "sep_vectors": filters.T,
        "base_centr": None,
        "spikes_centr": None,
        "emg_calib": calib,
    }
    return emg, state, config


def test_backward_pass_reads_complete_lookback(raw: np.ndarray) -> None:
    """Frozen, a backward pass is a plain projection of complete samples, in any batch order."""
    emg, state, config = _adaptive_setup(raw, adapt_wh=False, adapt_sv=False, adapt_sd=False)
    model = AdaptiveDecomp(emg=emg, config=config, **state)
    starts: list[int] = []
    pulse_trains = np.full((len(emg), model.n_motor_units), np.nan, dtype=np.float32)

    def sink(start: int, batch: np.ndarray, _spikes: np.ndarray) -> None:
        starts.append(start)
        pulse_trains[start : start + len(batch)] = batch

    stop = 2345  # not a multiple of the batch size: the partial batch is processed last
    model.run(0, stop, reverse=True, sink=sink)

    assert starts == sorted(starts, reverse=True)
    assert starts[-1] == EX - 1, "the recording start is never processed"
    assert np.isnan(pulse_trains[: EX - 1]).all() and np.isnan(pulse_trains[stop:]).all()
    ext = extend_signal(emg, EX, samples_first=True)
    want = (model.sep_vectors @ (model.whitening @ ext[EX - 1 : stop].T)).T
    np.testing.assert_allclose(
        pulse_trains[EX - 1 : stop], want, rtol=1e-4, atol=1e-5 * np.abs(want).max()
    )


def test_backward_pass_starts_from_the_calibration_centroids(raw: np.ndarray) -> None:
    """The forward pass adapts its centroids; the backward pass starts from the fitted ones."""
    whitening, filters, win_mean = calibration_case(raw)

    def source() -> StreamedExtender:
        return StreamedExtender(raw, EX, offset=win_mean, dtype=np.float32, samples_first=True)

    config = Config(fsamp=2000, ex_factor=EX, batch_ms=50)
    common: dict[str, Any] = {
        "whitening": whitening,
        "sep_vectors": filters.T,
        "base_centr": None,
        "spikes_centr": None,
    }
    fwd = AdaptiveDecomp(emg=source(), emg_calib=(200, 2600), config=config, **common)
    fitted = (fwd.base_centr.copy(), fwd.spikes_centr.copy())
    fwd.run(200, raw.shape[1])
    assert not (
        np.array_equal(fwd.base_centr, fitted[0]) and np.array_equal(fwd.spikes_centr, fitted[1])
    )
    bwd = AdaptiveDecomp(emg=source(), emg_calib=fwd.calibration, config=config, **common)
    np.testing.assert_array_equal(bwd.base_centr, fitted[0])
    np.testing.assert_array_equal(bwd.spikes_centr, fitted[1])


@pytest.mark.parametrize("compute_loss", [False, True])
def test_calibration_loss_baselines_exist_only_with_compute_loss(
    raw: np.ndarray, compute_loss: bool
) -> None:
    emg, state, config = _adaptive_setup(raw, compute_loss=compute_loss)
    calibration = AdaptiveDecomp(emg=emg, config=config, **state).calibration
    fitted = (calibration.contrast_calib_mean, calibration.contrast_calib_std)
    assert all((value is not None) == compute_loss for value in fitted)


@pytest.mark.parametrize(("n_samples", "kept"), [(250, 2), (300, 3)], ids=["partial", "whole"])
def test_partial_batch_has_no_loss(n_samples: int, kept: int) -> None:
    losses = {"wh_loss": np.arange(3.0), "sv_loss": np.ones((3, 2))}
    trimmed = drop_partial_batch_loss(losses, n_samples, batch_size=100)
    assert [len(v) for v in trimmed.values()] == [kept, kept]
    assert drop_partial_batch_loss({}, n_samples, batch_size=100) == {}


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
        _pulse_trains, spikes, _losses = run_adaptive_decomposition(
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


def test_single_mu_adaptive_separation_ignores_stale_output_memory() -> None:
    """``AdaptiveDecomp._separate`` with one MU over a batch of 520 samples (5.2 kHz, 100 ms)."""
    rng = np.random.default_rng(2)
    n_ext, n = 1024, 520
    model = AdaptiveDecomp.__new__(AdaptiveDecomp)
    model.sep_vectors = rng.standard_normal((1, n_ext)).astype(np.float32)
    whitened = rng.standard_normal((n_ext, n)).astype(np.float32)
    for _ in range(20):
        stale = np.full((1, n), np.nan, dtype=np.float32)
        del stale
        pulse_trains = model._separate(whitened)
        assert pulse_trains.shape == (n, 1) and np.isfinite(pulse_trains).all()
