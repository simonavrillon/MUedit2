"""Tests for the segmentation of a force target (:mod:`muedit.signal.segmentation`)."""

from __future__ import annotations

import itertools
from collections.abc import Sequence

import numpy as np
import pytest
from scipy.signal import butter, sosfiltfilt

from muedit.signal.segmentation import TargetSegment, segment_target

FSAMP = 2048.0
# Corners blur over the smoothing width (0.1 s each side).
TOL_S = 0.15


def _piecewise(points: Sequence[tuple[float, float]]) -> np.ndarray:
    """A target through ``(seconds, level)`` points, linear between them."""
    t, v = zip(*points, strict=True)
    return np.interp(np.arange(int(t[-1] * FSAMP)) / FSAMP, t, v)


def _sine(seconds: float, freq: float, offset: float = 20.0, amp: float = 5.0) -> np.ndarray:
    t = np.arange(int(seconds * FSAMP)) / FSAMP
    return offset + amp * np.sin(2 * np.pi * freq * t)


def _sine_protocol(freq: float) -> np.ndarray:
    """Ramp to 20, hold, oscillate ±5 for 16 s, hold, ramp down."""
    return np.concatenate(
        [
            _piecewise([(0, 0), (2, 0), (6, 20), (10, 20)]),
            _sine(16, freq),
            _piecewise([(0, 20), (4, 20), (8, 0), (10, 0)]),
        ]
    )


def _kinds(segments: list[TargetSegment]) -> list[str]:
    return [s.kind + (f"-{s.direction}" if s.direction else "") for s in segments]


def _assert_bounds(segments: list[TargetSegment], bounds_s: list[float]) -> None:
    edges = [segments[0].start] + [s.end for s in segments]
    np.testing.assert_allclose(np.asarray(edges) / FSAMP, bounds_s, atol=TOL_S)


def test_trapezoid_is_ramp_plateau_ramp() -> None:
    segments = segment_target(
        _piecewise([(0, 0), (2, 0), (7, 20), (17, 20), (22, 0), (25, 0)]), FSAMP
    )
    assert _kinds(segments) == ["ramp-up", "plateau", "ramp-down"]
    _assert_bounds(segments, [2, 7, 17, 22])
    assert segments[1].level_start == pytest.approx(20, abs=0.5)


def test_rest_between_trapezoids_leaves_a_gap() -> None:
    x = _piecewise(
        [(0, 0), (2, 0), (7, 20), (17, 20), (22, 0), (26, 0), (31, 20), (41, 20), (46, 0), (48, 0)]
    )
    segments = segment_target(x, FSAMP)
    assert _kinds(segments) == ["ramp-up", "plateau", "ramp-down"] * 2
    assert segments[2].end < segments[3].start


def test_staircase_is_alternating_ramps_and_plateaus() -> None:
    x = _piecewise(
        [(0, 0), (2, 0), (4, 10), (9, 10), (11, 20), (16, 20), (18, 30), (23, 30), (28, 0), (30, 0)]
    )
    assert _kinds(segment_target(x, FSAMP)) == [
        "ramp-up",
        "plateau",
        "ramp-up",
        "plateau",
        "ramp-up",
        "plateau",
        "ramp-down",
    ]


def test_consecutive_segments_share_their_boundary() -> None:
    x = np.concatenate(
        [_sine_protocol(0.5), _piecewise([(0, 0), (3, 0), (6, 10), (9, 10), (12, 0)])]
    )
    segments = segment_target(x, FSAMP)
    touching = [(a, b) for a, b in itertools.pairwise(segments) if b.start - a.end < FSAMP]
    assert touching
    assert all(a.end == b.start for a, b in touching)


@pytest.mark.parametrize("freq", [0.25, 1.5])
def test_sinusoid_block_between_plateaus(freq: float) -> None:
    segments = segment_target(_sine_protocol(freq), FSAMP)
    assert _kinds(segments) == ["ramp-up", "plateau", "sinusoid", "plateau", "ramp-down"]
    sine = segments[2]
    _assert_bounds(segments[2:3], [10, 26])
    assert sine.frequency_hz == pytest.approx(freq, rel=0.05)
    assert sine.amplitude == pytest.approx(5, rel=0.1)
    assert sine.offset == pytest.approx(20, abs=0.5)
    assert sine.n_cycles == pytest.approx(16 * freq, abs=1)
    assert len(sine.cycles) == pytest.approx(16 * freq, abs=1)
    assert all(sine.start <= c < sine.end for c in sine.cycles)


def test_noise_does_not_break_plateaus_or_the_sinusoid() -> None:
    x = _sine_protocol(0.5) + np.random.default_rng(0).normal(0, 0.3, _sine_protocol(0.5).size)
    assert _kinds(segment_target(x, FSAMP)) == [
        "ramp-up",
        "plateau",
        "sinusoid",
        "plateau",
        "ramp-down",
    ]


def test_pure_sinusoid_without_ramp_in_is_one_sinusoid() -> None:
    """The troughs sit at the target's minimum; they must not read as rest, nor end the block."""
    freq = 0.1
    segments = segment_target(_sine(10 / freq, freq), FSAMP)
    assert _kinds(segments) == ["sinusoid"]
    sine = segments[0]
    assert sine.frequency_hz == pytest.approx(freq, rel=0.05)
    assert sine.amplitude == pytest.approx(5, rel=0.1)
    assert sine.offset == pytest.approx(20, abs=0.5)
    assert sine.n_cycles == pytest.approx(10, abs=1)
    assert len(sine.cycles) == pytest.approx(10, abs=1)


def test_fast_sinusoid_is_a_sinusoid() -> None:
    """The frequency estimate must not be quantized to whole samples of period."""
    sine = next(s for s in segment_target(_sine_protocol(5.0), FSAMP) if s.kind == "sinusoid")
    assert sine.frequency_hz == pytest.approx(5.0, rel=0.05)


def test_random_tracking_target_is_one_irregular_block() -> None:
    raw = np.random.default_rng(1).normal(size=int(30 * FSAMP))
    band = sosfiltfilt(butter(2, [0.1, 1.0], btype="band", fs=FSAMP, output="sos"), raw)
    x = np.concatenate(
        [
            _piecewise([(0, 0), (2, 0), (6, 20), (8, 20)]),
            20 + 5 * band / band.std(),
            _piecewise([(0, 20), (2, 20), (6, 0), (8, 0)]),
        ]
    )
    kinds = _kinds(segment_target(x, FSAMP))
    assert kinds.count("irregular") == 1
    assert "sinusoid" not in kinds
    assert kinds[0] == "ramp-up" and kinds[-1] == "ramp-down"


def test_single_triangle_stays_two_ramps() -> None:
    x = _piecewise([(0, 0), (2, 0), (7, 20), (12, 0), (14, 0)])
    assert _kinds(segment_target(x, FSAMP)) == ["ramp-up", "ramp-down"]


@pytest.mark.parametrize(
    "target",
    [np.full(int(10 * FSAMP), 5.0), np.zeros(0), np.array([0.0, np.nan, 1.0])],
    ids=["flat", "empty", "nan"],
)
def test_degenerate_targets_give_no_segments(target: np.ndarray) -> None:
    assert segment_target(target, FSAMP) == []
