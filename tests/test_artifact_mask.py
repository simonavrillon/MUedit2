"""Tests for the automatic per-grid artifact-mask detector."""

from __future__ import annotations

import numpy as np
import pytest

from muedit.signal.artifact_mask import (
    ArtifactMaskConfig,
    _detect_artifact_mask,
    detect_artifact_masks,
    mask_to_intervals,
)
from muedit.signal.filters import bandpass_signals
from tests._platform import MAPPED_FILES_STAY

# ── Fixtures ─────────────────────────────────────────────────────────────────

FSAMP = 2000.0
N_CHANNELS = 64
N_SAMPLES = 20_000


def _clean_emg(
    n_channels: int = N_CHANNELS,
    n_samples: int = N_SAMPLES,
    noise_std: float = 0.01,
) -> np.ndarray:
    """Synthetic clean HD-EMG: bandpass-filtered Gaussian noise."""
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
    """Inject a physiological burst on a few channels."""
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


# ── _detect_artifact_mask ────────────────────────────────────────────────────


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
    """A short contraction in a mostly-rest recording is not an artifact."""
    data = _clean_emg()
    start, end = 8000, 12000
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
    """Transients on fewer than ``min_channels`` channels are not artifacts."""
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


# ── Blocks of samples vs the whole-grid detector ────────────────────────────


def _reference_mask(data: np.ndarray, fsamp: float, cfg: ArtifactMaskConfig) -> np.ndarray:
    """The detector as it was before stage 12, without run extension: whole-grid arrays and medians."""
    from scipy.ndimage import binary_closing, binary_dilation, maximum_filter1d, median_filter

    def baselines(x: np.ndarray, cols: np.ndarray | None) -> list[tuple[np.ndarray, np.ndarray]]:
        med = np.median(x, axis=-1, keepdims=True)
        sigma = 1.4826 * np.median(np.abs(x - med), axis=-1, keepdims=True)
        out = [(med, np.maximum(sigma, 0.20 * med))]
        step = max(1, int(round(fsamp * 25 / 1000.0)))
        xd = x[..., ::step]
        size = (1,) * (xd.ndim - 1) + (max(3, int(round(2.0 * fsamp / step)) | 1),)
        med_d = median_filter(xd, size=size, mode="nearest")
        mad_d = median_filter(np.abs(xd - med_d), size=size, mode="nearest")
        idx = np.arange(x.shape[-1]) if cols is None else cols
        idx = np.minimum(idx // step, xd.shape[-1] - 1)
        med = med_d[..., idx]
        out.append((med, np.maximum(1.4826 * mad_d[..., idx], 0.20 * med)))
        return out

    def exceeds(
        x: np.ndarray, z_thr: float, amp_ratio: float | None, cols: np.ndarray | None = None
    ) -> np.ndarray:
        xs = x if cols is None else x[..., cols]
        out = np.ones(xs.shape, dtype=bool)
        for med, sigma in baselines(x, cols):
            hit = (xs - med) / (sigma + 1e-12) > z_thr
            if amp_ratio is not None:
                hit |= xs > amp_ratio * (med + 1e-12)
            out &= hit
        return out

    n_samples = data.shape[1]
    ch_win = maximum_filter1d(
        np.abs(data.astype(np.float32)),
        size=max(1, int(round(fsamp * cfg.win_ms / 1000.0))),
        axis=1,
        mode="nearest",
    )
    n_channels = ch_win.shape[0]
    k = min(max(cfg.min_channels, 1), n_channels)
    win_stat = np.partition(ch_win, n_channels - k, axis=0)[n_channels - k]
    candidate = exceeds(win_stat, cfg.z_thr, cfg.amp_ratio)
    if not candidate.any():
        return np.zeros(n_samples, dtype=bool)
    cols = np.flatnonzero(candidate)
    confirmed = np.zeros(n_samples, dtype=bool)
    confirmed[cols[exceeds(ch_win, cfg.ch_z_thr, None, cols).sum(axis=0) >= k]] = True
    if not confirmed.any():
        return confirmed
    bridge = max(1, int(round(fsamp * cfg.min_gap_ms / 1000.0)))
    pad = max(1, int(round(fsamp * cfg.pad_ms / 1000.0)))
    confirmed = binary_closing(confirmed, structure=np.ones(bridge, dtype=bool))
    return np.asarray(binary_dilation(confirmed, structure=np.ones(pad, dtype=bool)), dtype=bool)


def _artifacted_grid() -> np.ndarray:
    data = _clean_emg(n_samples=40_000).astype(np.float64)
    for center in (5_000, 17_000, 30_011):
        data = _inject_artifact(data, center, channels=slice(0, 40))
    return _inject_burst(data, 22_000)


@pytest.mark.parametrize("block_bytes", [None, 64 * 4 * 997, 64 * 4 * 3000])
def test_blocks_of_samples_give_the_whole_grid_mask(
    block_bytes: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from muedit.io import store

    if block_bytes is not None:
        monkeypatch.setattr(store, "BLOCK_BYTES", block_bytes)
    data = _artifacted_grid()
    cfg = ArtifactMaskConfig(max_extend_s=0.0)
    want = _reference_mask(data, FSAMP, cfg)
    assert want.any()
    np.testing.assert_array_equal(_detect_artifact_mask(data, FSAMP, cfg), want)
    session = store.SessionStore.create("artifact")
    np.testing.assert_array_equal(_detect_artifact_mask(data, FSAMP, cfg, store=session), want)
    # The working array is deleted.
    assert MAPPED_FILES_STAY or not any(session.path.glob("artifact-windows*"))


def test_kept_rows_give_the_mask_of_the_stacked_rows() -> None:
    data = _artifacted_grid()
    rows = np.array([0, 2, 5, 7, 9, *range(12, N_CHANNELS)])
    cfg = ArtifactMaskConfig(max_extend_s=0.0)
    want = _reference_mask(data[rows], FSAMP, cfg)
    np.testing.assert_array_equal(_detect_artifact_mask(data, FSAMP, cfg, rows=rows), want)
    per_grid, _ = detect_artifact_masks(data, FSAMP, [rows.size], cfg, grid_rows=[rows])
    np.testing.assert_array_equal(per_grid[0], want)


# ── Run extension ───────────────────────────────────────────────────────────


def _long_burst_grid() -> np.ndarray:
    """A 3 s broadband burst on every channel, longer than the 2 s local baseline."""
    data = _clean_emg(n_samples=40_000)
    burst = np.random.default_rng(5).normal(0, 0.2, (N_CHANNELS, 6_000)).astype(np.float32)
    data[:, 15_000:21_000] += bandpass_signals(burst, FSAMP, emg_type=1).astype(np.float32)
    return data


def test_long_artifact_is_masked_whole() -> None:
    data = _long_burst_grid()
    mask = _detect_artifact_mask(data, FSAMP)
    assert mask[15_000:21_000].mean() > 0.95
    assert not mask[:14_000].any() and not mask[22_000:].any()
    # Without the extension only its edges stand out against the local baseline.
    edges = _detect_artifact_mask(data, FSAMP, ArtifactMaskConfig(max_extend_s=0.0))
    assert edges[15_000:21_000].mean() < 0.1


def test_extension_is_capped() -> None:
    mask = _detect_artifact_mask(_long_burst_grid(), FSAMP, ArtifactMaskConfig(max_extend_s=0.5))
    runs = mask_to_intervals(mask)
    assert runs.size and (runs[:, 1] - runs[:, 0]).max() < 2 * 0.5 * FSAMP + 0.2 * FSAMP


def test_extension_stops_at_emg_well_below_the_artifact() -> None:
    """A contraction starting under a brief artifact is not masked with it."""
    data = _clean_emg(n_samples=40_000)
    data[:, 15_000:21_000] *= 3
    data = _inject_artifact(data, 15_000, amplitude=0.3)
    mask = _detect_artifact_mask(data, FSAMP)
    assert mask[15_000] and not mask[15_400:].any()
