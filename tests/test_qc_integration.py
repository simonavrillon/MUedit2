"""Tests for how the automatic QC pipeline is wired into the app.

Covers the integration points added alongside :mod:`muedit.signal.qc_pipeline`:

* ``build_manual_artifact_mask`` and the ``artifact_regions`` /
  ``auto_mask_artifacts`` handling in ``preprocess_step``;
* the ``POST /api/v1/qc/auto`` route and its ``_mask_to_regions`` encoder;
* ``artifact_mask`` round-trips through NPZ decomposition files
  (``_load_npz_decomp``, ``load_decomposition_signal_context``).

Everything is synthetic; no sample data is required.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from starlette.testclient import TestClient

from muedit.api.services.preview_service import _mask_to_regions
from muedit.decomp.decomposition_file import (
    _load_npz_decomp,
    load_decomposition_signal_context,
    save_decomposition_npz,
)
from muedit.decomp.preprocess import build_manual_artifact_mask, preprocess_step
from muedit.decomp.types import DecompositionParameters, LoadStepOutput, PreprocessStepOutput
from muedit.models import SignalImport
from muedit.signal.filters import bandpass_signals
from muedit.signal.qc_pipeline import QCPipelineResult

FSAMP = 2000.0
N_CHANNELS = 64
N_SAMPLES = 20_000  # 10 s @ 2000 Hz
GRID = "GR08MM1305"


def _raw_emg(n_grids: int = 1, seed: int = 0) -> np.ndarray:
    """Band-limited noise with a shared component (all channels well correlated)."""
    rng = np.random.default_rng(seed)
    n_ch = N_CHANNELS * n_grids
    common = rng.normal(0, 1, (1, N_SAMPLES))
    raw = 0.8 * common + 0.6 * rng.normal(0, 1, (n_ch, N_SAMPLES))
    return (0.05 * bandpass_signals(raw.astype(np.float32), FSAMP, emg_type=1)).astype(np.float64)


def _loaded(data: np.ndarray, grids: list[str]) -> LoadStepOutput:
    signal = SignalImport(
        data=data,
        fsamp=FSAMP,
        gridname=grids,
        muscle=["ta"] * len(grids),
        auxiliary=np.zeros((0, data.shape[1])),
    )
    return LoadStepOutput(
        full_path="synthetic.mat",
        filename="synthetic.mat",
        signal=signal,
        data=data,
        fsamp=FSAMP,
    )


def _preprocess(data: np.ndarray, grids: list[str], **kwargs: Any) -> PreprocessStepOutput:
    params = kwargs.pop("params", DecompositionParameters())
    return preprocess_step(
        loaded=_loaded(data, grids),
        duration=None,
        manual_roi=False,
        roi=None,
        rois=None,
        params=params,
        discard_overrides=kwargs.pop("discard_overrides", None),
        bids_root=None,
        bids_entities=None,
        bids_metadata=None,
        **kwargs,
    )


# ── build_manual_artifact_mask ──────────────────────────────────────────────


class TestBuildManualArtifactMask:
    def test_regions_are_rasterized(self) -> None:
        # Half-open, reversed, clipped, overlapping and non-int bounds.
        regions = [(2, 4), (6, 5), (-5, 1), (8, 50), (3.0, "4")]
        mask = build_manual_artifact_mask(regions, 10)
        assert mask.dtype == bool
        assert np.flatnonzero(mask).tolist() == [0, 2, 3, 5, 8, 9]

    @pytest.mark.parametrize(
        "regions,n",
        [(None, 10), ([], 10), ([(3, 3)], 10), ([(20, 30)], 10), ([(0, 5)], 0)],
        ids=["none", "empty", "zero-width", "past-end", "no-samples"],
    )
    def test_nothing_to_mask_returns_none(
        self,
        regions: list[tuple[int, int]] | None,
        n: int,
    ) -> None:
        assert build_manual_artifact_mask(regions, n) is None


# ── preprocess_step ──────────────────────────────────────────────────────────


class TestPreprocessArtifactMask:
    def test_manual_regions_become_mask(self) -> None:
        prep = _preprocess(_raw_emg(), [GRID], artifact_regions=[(1000, 1500), (9000, 9100)])
        assert prep.artifact_mask.shape == (N_SAMPLES,)
        assert int(prep.artifact_mask.sum()) == 600
        assert prep.artifact_mask[1000:1500].all()
        assert prep.artifact_mask[9000:9100].all()
        assert prep.bad_channel_masks is None

    def test_auto_qc_merges_dead_channel_with_user_discards(self) -> None:
        data = _raw_emg()
        data[12] = 0.0
        override = [0] * N_CHANNELS
        override[40] = 1
        params = DecompositionParameters(auto_mask_artifacts=True)
        prep = _preprocess(data, [GRID], params=params, discard_overrides=[override])
        assert prep.bad_channel_masks[0][12]
        assert {12, 40} <= set(np.flatnonzero(prep.discard_channels[0]).tolist())
        assert prep.artifact_mask.shape == (N_SAMPLES,)

    def test_auto_and_manual_masks_are_ored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import muedit.decomp.preprocess as preprocess

        auto = np.zeros(N_SAMPLES, dtype=bool)
        auto[100:200] = True

        def fake_qc(
            data: np.ndarray,
            fsamp: float,
            counts: list[int],
            grid_coordinates: list[np.ndarray] | None = None,
        ) -> QCPipelineResult:
            assert counts == [N_CHANNELS, N_CHANNELS]
            assert len(grid_coordinates) == 2
            bad = [np.zeros(N_CHANNELS, bool), np.zeros(N_CHANNELS, bool)]
            bad[1][3] = True
            return QCPipelineResult(artifact_mask=auto.copy(), bad_channel_masks=bad)

        monkeypatch.setattr(preprocess, "run_auto_qc", fake_qc)
        params = DecompositionParameters(auto_mask_artifacts=True)
        prep = _preprocess(
            _raw_emg(n_grids=2),
            [GRID, GRID],
            params=params,
            artifact_regions=[(150, 300)],
        )
        assert np.flatnonzero(np.diff(prep.artifact_mask.astype(int))).tolist() == [99, 299]
        assert not prep.discard_channels[0].any()
        assert np.flatnonzero(prep.discard_channels[1]).tolist() == [3]


# ── _mask_to_regions ─────────────────────────────────────────────────────────


def test_mask_to_regions_round_trips() -> None:
    mask = np.zeros(100, bool)
    mask[[0, 3, 4, 5, 40, 90, 91, 99]] = True
    regions = _mask_to_regions(mask)
    assert regions == [[0, 1], [3, 6], [40, 41], [90, 92], [99, 100]]
    np.testing.assert_array_equal(build_manual_artifact_mask(regions, 100), mask)
    assert _mask_to_regions(np.zeros(4, bool)) == []
    assert _mask_to_regions(None) == []


# ── POST /api/v1/qc/auto ─────────────────────────────────────────────────────


@pytest.fixture()
def api_client() -> Iterator[TestClient]:
    from fastapi import FastAPI

    from muedit.api.routes import include_routers

    app = FastAPI()
    include_routers(app)
    with TestClient(app) as client:
        yield client


def _qc_token(data: np.ndarray, grids: list[str]) -> str:
    """Cache ``data`` as the upload + QC preview signal, as /preview-by-path does."""
    from muedit.api.cache import _store_qc_signal, _store_upload_signal

    token = _store_upload_signal(_loaded(data, grids).signal)
    _store_qc_signal(
        token,
        data,
        FSAMP,
        grids,
        [np.zeros(N_CHANNELS, int) for _ in grids],
    )
    return token


class TestQcAutoRoute:
    def test_returns_bad_channels_and_artifact_regions(self, api_client: TestClient) -> None:
        data = _raw_emg(n_grids=2)
        data[N_CHANNELS + 7] = 0.0  # dead electrode on grid 2
        data[:, 10_000:10_100] += 50.0  # full-montage transient
        token = _qc_token(data, [GRID, GRID])

        resp = api_client.post("/api/v1/qc/auto", json={"upload_token": token})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["api_version"] == "v1"
        out = body["data"]

        assert out["fsamp"] == FSAMP
        assert out["grid_names"] == [GRID, GRID]
        assert out["total_samples"] == N_SAMPLES
        bad = out["bad_channels_per_grid"]
        assert [len(g) for g in bad] == [N_CHANNELS, N_CHANNELS]
        assert set(bad[0]) <= {0, 1} and set(bad[1]) <= {0, 1}
        assert bad[1][7] == 1

        regions = out["artifact_regions"]
        assert regions, "full-montage transient was not detected"
        assert any(s <= 10_050 < e for s, e in regions)
        assert out["artifact_samples"] == sum(e - s for s, e in regions)


# ── NPZ persistence ──────────────────────────────────────────────────────────


def _save_npz(path: Path, extras: dict | None) -> None:
    distimes = [[100, 300], [200]]
    pulse = np.zeros((2, 1000))
    save_decomposition_npz(
        path,
        pulse_trains=pulse,
        distimes=distimes,
        fsamp=FSAMP,
        grid_names=[GRID],
        mu_grid_index=[0, 0],
        muscles=["ta"],
        parameters={},
        total_samples=1000,
        extras=extras,
    )


class TestNpzArtifactMask:
    def test_decomp_load_returns_mask(self, tmp_path: Path) -> None:
        mask = np.zeros(1000, dtype=bool)
        mask[400:450] = True
        path = tmp_path / "d.npz"
        _save_npz(path, {"artifact_mask": mask})
        loaded = _load_npz_decomp(str(path))
        np.testing.assert_array_equal(loaded.artifact_mask, mask)
        assert loaded.artifact_mask.dtype == bool

    def test_signal_context_with_emg_and_mask(self, tmp_path: Path) -> None:
        from muedit.decomp.decomposition_file import pack_object_array

        emg = np.arange(3 * 1000, dtype=float).reshape(3, 1000)
        mask = np.zeros(1000, dtype=bool)
        mask[5:7] = True
        path = tmp_path / "d.npz"
        _save_npz(
            path,
            {
                "emg_data": emg.T,  # stored samples-first; loader must transpose
                "discard_channels": pack_object_array([np.array([0, 1, 0])]),
                "artifact_mask": mask,
            },
        )
        ctx = load_decomposition_signal_context(str(path))
        assert ctx is not None
        np.testing.assert_array_equal(ctx.data, emg)
        assert ctx.fsamp == FSAMP
        assert ctx.grid_names == [GRID]
        np.testing.assert_array_equal(ctx.artifact_mask, mask)
        assert len(ctx.emgmask) == 1
