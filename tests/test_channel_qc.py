"""Tests for the automatic bad-channel detector (:mod:`muedit.signal.channel_qc`).

Each fault is injected into one channel of a clean synthetic grid, so the
expected flag and reason are known.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from muedit.signal.channel_qc import (
    _channel_qc_diagnostics,
    _detect_bad_channels,
    detect_bad_channels_per_grid,
)
from tests._synthetic_emg import (
    FSAMP,
    N_CHANNELS,
    N_SAMPLES,
    bandpass,
    correlated_emg,
    grid_coords,
)

CH = 30  # channel that receives the fault


def _flat(data: np.ndarray) -> None:
    data[CH] = 1e-12


def _dc_stuck(data: np.ndarray) -> None:
    """Stuck at a DC level; QC sees it after the app's bandpass, i.e. near zero."""
    data[CH] = bandpass(np.full((1, N_SAMPLES), 0.05))[0]


def _saturated(data: np.ndarray) -> None:
    clipped = np.random.default_rng(7).choice(N_SAMPLES, size=N_SAMPLES // 20, replace=False)
    data[CH, clipped] = data[CH].max() * 10


def _noisy(data: np.ndarray) -> None:
    data[CH] = bandpass(np.random.default_rng(99).normal(0, 0.02, (1, N_SAMPLES)))[0]


def _quantized(data: np.ndarray) -> None:
    """Stuck between two ADC levels with a handful of transitions."""
    stuck = np.full(N_SAMPLES, -0.1, dtype=np.float32)
    stuck[9000:9200] = 0.1
    stuck[15000:15100] = 0.1
    data[CH] = bandpass(stuck[None])[0]


def _low_snr(data: np.ndarray) -> None:
    """Weak and uncorrelated with its neighbours: poor contact."""
    data[CH] = bandpass(np.random.default_rng(77).normal(0, 0.003, (1, N_SAMPLES)))[0]


def _intermittent(data: np.ndarray) -> None:
    """Quiet baseline with brief contact make/break transients."""
    data[CH] *= 0.1
    for center in (5000, 10000, 15000):
        data[CH, center - 10 : center + 10] += 0.3


def _contact_loss(data: np.ndarray) -> None:
    data[CH, 6000:10000] = 0.0


@pytest.mark.parametrize(
    "inject,reason",
    [
        (_flat, "flat"),
        (_dc_stuck, "flat"),
        (_saturated, "saturated"),
        (_noisy, "noisy"),
        (_quantized, "quantized"),
        (_low_snr, "low-SNR"),
        (_intermittent, "intermittent"),
        (_contact_loss, "contact-loss"),
    ],
    ids=[
        "flat",
        "dc-stuck",
        "saturated",
        "noisy",
        "quantized",
        "low-snr",
        "intermittent",
        "contact-loss",
    ],
)
def test_fault_is_flagged_with_reason(inject: Callable[[np.ndarray], None], reason: str) -> None:
    data = correlated_emg()
    inject(data)
    diag = _channel_qc_diagnostics(data, FSAMP, grid_coords())
    assert np.flatnonzero(diag.mask).tolist() == [CH]
    assert reason in diag.reasons[CH]
    np.testing.assert_array_equal(_detect_bad_channels(data, FSAMP, grid_coords()), diag.mask)


def test_clean_grid_has_no_flags() -> None:
    mask = _detect_bad_channels(correlated_emg(), FSAMP, grid_coords())
    assert mask.dtype == bool and mask.shape == (N_CHANNELS,)
    assert not mask.any()


def test_quiet_but_correlated_channel_is_not_low_snr() -> None:
    """A channel over a quiet part of the muscle is weak but still tracks its neighbours."""
    data = correlated_emg()
    data[20] *= 0.05
    assert "low-SNR" not in _channel_qc_diagnostics(data, FSAMP, grid_coords()).reasons[20]


def test_noisy_check_needs_coordinates() -> None:
    data = correlated_emg()
    _noisy(data)
    assert not _detect_bad_channels(data, FSAMP, coordinates=None)[CH]


def test_all_flat_grid_is_not_flagged() -> None:
    """Flatness is relative to the grid median; a silent grid has no outlier."""
    data = np.zeros((N_CHANNELS, N_SAMPLES), dtype=np.float32)
    assert not _detect_bad_channels(data, FSAMP, grid_coords()).any()


def test_per_grid_masks_use_grid_local_indices() -> None:
    data = np.vstack([correlated_emg(seed=100), correlated_emg(seed=101)])
    data[5] = 1e-12  # grid 0, channel 5
    data[N_CHANNELS + 6] = 1e-12  # grid 1, channel 6
    masks = detect_bad_channels_per_grid(
        data, FSAMP, [N_CHANNELS, N_CHANNELS], [grid_coords(), grid_coords()]
    )
    assert [np.flatnonzero(m).tolist() for m in masks] == [[5], [6]]


def test_per_grid_without_coordinates_still_flags_flat() -> None:
    data = np.vstack([correlated_emg(seed=100), correlated_emg(seed=101)])
    data[0] = 1e-12
    masks = detect_bad_channels_per_grid(data, FSAMP, [N_CHANNELS, N_CHANNELS], None)
    assert [np.flatnonzero(m).tolist() for m in masks] == [[0], []]
