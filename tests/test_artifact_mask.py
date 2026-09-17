"""Tests for the automatic per-grid artifact-mask detector.

Exercises the two-stage robust amplitude detector in
:mod:`muedit.signal.artifact_mask` with synthetic signals where the ground
truth is known: clean Gaussian noise, a positive transient, a negative
transient, a partial-grid artifact (subset of channels), and a physiological
burst that must *not* be flagged.
"""

from __future__ import annotations

import numpy as np
import pytest

from muedit.signal.artifact_mask import (
    ArtifactMaskConfig,
    _detect_artifact_mask,
    detect_artifact_masks,
)
from muedit.signal.filters import bandpass_signals

# ── Fixtures ─────────────────────────────────────────────────────────────────

FSAMP = 2000.0
N_CHANNELS = 64
N_SAMPLES = 20_000  # 10 s @ 2000 Hz


def _clean_emg(
    n_channels: int = N_CHANNELS,
    n_samples: int = N_SAMPLES,
    noise_std: float = 0.01,
) -> np.ndarray:
    """Synthetic clean HD-EMG: bandpass-filtered Gaussian noise.

    The detector runs on filtered data (notch + bandpass), so the test signal
    must be temporally correlated, not white noise.  White noise has a
    degenerate MAD that inflates z-scores and causes false positives.
    """
    rng = np.random.default_rng(42)
    raw = rng.normal(0, noise_std, size=(n_channels, n_samples)).astype(np.float32)
    return bandpass_signals(raw, FSAMP, emg_type=1).astype(np.float32)


def _inject_burst(
    data: np.ndarray,
    center: int,
    half_width: int = 200,
    amplitude: float = 0.05,
    n_active_channels: int = 4,
) -> np.ndarray:
    """Inject a physiological burst: moderate amplitude on a *few* channels.

    Real motor-unit discharges only strongly excite channels near the source;
    the quorum filter exploits this to distinguish bursts from artifacts.
    """
    data = data.copy()
    n_ch = data.shape[0]
    t = np.arange(data.shape[1])
    envelope = amplitude * np.exp(-((t - center) ** 2) / (2 * half_width**2))
    active = np.random.default_rng(99).choice(n_ch, size=n_active_channels, replace=False)
    data[active] += envelope[np.newaxis, :]
    return data


def _inject_artifact(
    data: np.ndarray,
    center: int,
    half_width: int = 50,
    amplitude: float = 0.3,
    channels: slice | None = None,
    polarity: float = 1.0,
) -> np.ndarray:
    """Inject a high-amplitude transient artifact on a subset of channels."""
    data = data.copy()
    if channels is None:
        channels = slice(None)
    t = np.arange(data.shape[1])
    envelope = polarity * amplitude * np.exp(-((t - center) ** 2) / (2 * half_width**2))
    data[channels] += envelope[np.newaxis, :]
    return data


# ── _detect_artifact_mask ─────────────────────────────────────────────────────


def test_clean_signal_has_empty_mask() -> None:
    mask = _detect_artifact_mask(_clean_emg(), FSAMP)
    assert mask.dtype == bool and mask.shape == (N_SAMPLES,)
    assert not mask.any()


@pytest.mark.parametrize(
    "channels,polarity",
    [(slice(None), 1.0), (slice(None), -1.0), (slice(0, 15), -1.0)],
    ids=["positive", "negative", "partial-grid"],
)
def test_transient_is_masked_around_its_center(channels: slice, polarity: float) -> None:
    center = N_SAMPLES // 2
    data = _inject_artifact(_clean_emg(), center, channels=channels, polarity=polarity)
    masked = np.flatnonzero(_detect_artifact_mask(data, FSAMP))
    assert masked.size and masked[0] <= center <= masked[-1]
    assert center - 200 < masked[0] and masked[-1] < center + 200
    assert np.all(np.diff(masked) == 1), "one contiguous region expected"


def test_two_transients_give_two_regions() -> None:
    c1, c2 = N_SAMPLES // 4, 3 * N_SAMPLES // 4
    data = _inject_artifact(_inject_artifact(_clean_emg(), c1), c2)
    mask = _detect_artifact_mask(data, FSAMP)
    assert mask[c1] and mask[c2]
    assert not mask[c1 + 500 : c2 - 500].all()


def test_single_channel_recording_with_min_channels_one() -> None:
    center = N_SAMPLES // 2
    data = _inject_artifact(_clean_emg(n_channels=1), center)
    mask = _detect_artifact_mask(data, FSAMP, ArtifactMaskConfig(min_channels=1))
    assert mask[center]


# ── Physiological activity must not be masked ────────────────────────────────


def test_burst_on_few_channels_not_flagged() -> None:
    data = _inject_burst(_clean_emg(), N_SAMPLES // 2, amplitude=0.05, half_width=300)
    assert not _detect_artifact_mask(data, FSAMP).any()


def test_short_contraction_in_mostly_rest_not_flagged() -> None:
    """A short contraction in a mostly-rest recording is not an artifact.

    The whole-recording baseline sits at the rest level, so the
    contraction stands out against it; the local baseline follows the
    contraction and keeps it from being flagged.
    """
    data = _clean_emg()
    start, end = 8000, 12000  # 2 s of a 10 s recording
    rng = np.random.default_rng(7)
    activity = bandpass_signals(
        rng.normal(0, 0.1, size=(N_CHANNELS, end - start)).astype(np.float32),
        FSAMP,
        emg_type=1,
    )
    ramp = np.ones(end - start, dtype=np.float32)
    taper = np.hanning(800).astype(np.float32)
    ramp[:400], ramp[-400:] = taper[:400], taper[400:]
    data[:, start:end] += activity * ramp
    mask = _detect_artifact_mask(data, FSAMP)
    assert not mask[start:end].any()


def test_few_defective_channels_not_flagged() -> None:
    """Transients on fewer than ``min_channels`` channels are not artifacts.

    Channels with intermittent contact produce large spikes of their own.
    When moderate activity on other channels happens to coincide with a
    spike, the quorum is met; the candidate must therefore itself be
    visible on at least ``min_channels`` channels, so a handful of
    defective electrodes cannot create detections.
    """
    data = _clean_emg()
    t = np.arange(N_SAMPLES)
    for center in range(1000, N_SAMPLES - 1000, 1400):
        data = _inject_artifact(
            data,
            center,
            half_width=15,
            amplitude=1.0,
            channels=slice(0, 3),
        )
        # Coincident moderate bursts on 6 healthy channels (not artifacts
        # on their own).
        envelope = 0.01 * np.exp(-((t - center) ** 2) / (2 * 60**2))
        noise = np.random.default_rng(center).normal(0, 1, size=(6, N_SAMPLES))
        data[10:16] += (envelope[np.newaxis, :] * noise).astype(np.float32)
    mask = _detect_artifact_mask(data, FSAMP)
    assert not mask.any()


# ── detect_artifact_masks (multi-grid) ───────────────────────────────────────


def test_multi_grid_masks_are_per_grid_and_ored() -> None:
    center = N_SAMPLES // 2
    data = _inject_artifact(
        _clean_emg(n_channels=2 * N_CHANNELS), center, channels=slice(0, N_CHANNELS), polarity=-1.0
    )
    per_grid, global_mask = detect_artifact_masks(data, FSAMP, [N_CHANNELS, N_CHANNELS])
    assert per_grid[0][center]
    assert not per_grid[1].any()
    np.testing.assert_array_equal(global_mask, per_grid[0] | per_grid[1])
