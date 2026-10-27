"""Tests for :mod:`muedit.decomp.preprocess`: filtering, ROIs, channel QC and artifact masks."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from muedit.decomp.preprocess import build_manual_artifact_mask, preprocess_step
from muedit.decomp.types import DecompositionParameters, LoadStepOutput, PreprocessStepOutput
from muedit.models import SignalImport, resident_nbytes
from muedit.signal.filters import bandpass_signals
from muedit.signal.qc_pipeline import QCPipelineResult
from tests._synthetic_emg import FSAMP, N_CHANNELS, N_SAMPLES, STORE_PARAMS

GRID = "GR08MM1305"


def _raw_emg(ngrid: int = 1, seed: int = 0) -> np.ndarray:
    """Band-limited noise with a shared component (all channels well correlated)."""
    rng = np.random.default_rng(seed)
    n_channels = N_CHANNELS * ngrid
    common = rng.normal(0, 1, (1, N_SAMPLES))
    raw = 0.8 * common + 0.6 * rng.normal(0, 1, (n_channels, N_SAMPLES))
    return (0.05 * bandpass_signals(raw.astype(np.float32), FSAMP, emg_type=1)).astype(np.float64)


def _loaded(data: np.ndarray, grids: list[str]) -> LoadStepOutput:
    signal = SignalImport(
        data=data,
        fsamp=FSAMP,
        grid_names=grids,
        muscle_names=["ta"] * len(grids),
        aux_data=np.zeros((0, data.shape[1])),
    )
    return LoadStepOutput(full_path="synthetic.mat", signal=signal)


def _preprocess(data: np.ndarray, grids: list[str], **kwargs: Any) -> PreprocessStepOutput:
    params = kwargs.pop("params", DecompositionParameters())
    return preprocess_step(
        loaded=_loaded(data, grids),
        duration=None,
        manual_roi=False,
        rois=None,
        params=params,
        discard_overrides=kwargs.pop("discard_overrides", None),
        **kwargs,
    )


class TestBuildManualArtifactMask:
    def test_regions_are_rasterized(self) -> None:
        regions: list[Any] = [(2, 4), (6, 5), (-5, 1), (8, 50), (3.0, "4")]
        mask = build_manual_artifact_mask(regions, 10)
        assert mask is not None
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


class TestPreprocessArtifactMask:
    def test_manual_regions_become_mask(self) -> None:
        prep = _preprocess(_raw_emg(), [GRID], artifact_regions=[(1000, 1500), (9000, 9100)])
        assert prep.artifact_mask is not None
        assert prep.artifact_mask.shape == (N_SAMPLES,)
        assert int(prep.artifact_mask.sum()) == 600
        assert prep.artifact_mask[1000:1500].all()
        assert prep.artifact_mask[9000:9100].all()

    def test_auto_qc_merges_dead_channel_with_user_discards(self) -> None:
        data = _raw_emg()
        data[12] = 0.0
        override = [0] * N_CHANNELS
        override[40] = 1
        params = DecompositionParameters(auto_mask_artifacts=True)
        prep = _preprocess(data, [GRID], params=params, discard_overrides=[override])
        assert prep.artifact_mask is not None
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
            store: object = None,
        ) -> QCPipelineResult:
            assert counts == [N_CHANNELS, N_CHANNELS]
            assert grid_coordinates is not None
            assert len(grid_coordinates) == 2
            bad = [np.zeros(N_CHANNELS, bool), np.zeros(N_CHANNELS, bool)]
            bad[1][3] = True
            return QCPipelineResult(artifact_mask=auto.copy(), bad_channel_masks=bad)

        monkeypatch.setattr(preprocess, "run_auto_qc", fake_qc)
        params = DecompositionParameters(auto_mask_artifacts=True)
        prep = _preprocess(
            _raw_emg(ngrid=2),
            [GRID, GRID],
            params=params,
            artifact_regions=[(150, 300)],
        )
        assert prep.artifact_mask is not None
        assert np.flatnonzero(np.diff(prep.artifact_mask.astype(int))).tolist() == [99, 299]
        assert not prep.discard_channels[0].any()
        assert np.flatnonzero(prep.discard_channels[1]).tolist() == [3]

    def test_grid_left_without_channels_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Auto-QC marking a whole grid bad stops the run with a reason, not a division by zero."""
        import muedit.decomp.preprocess as preprocess

        def all_bad(
            data: np.ndarray, fsamp: float, counts: list[int], **_: Any
        ) -> QCPipelineResult:
            return QCPipelineResult(
                artifact_mask=np.zeros(N_SAMPLES, bool),
                bad_channel_masks=[np.ones(c, bool) for c in counts],
            )

        monkeypatch.setattr(preprocess, "run_auto_qc", all_bad)
        params = DecompositionParameters(auto_mask_artifacts=True)
        with pytest.raises(ValueError, match=r"Grid 1 .* every channel discarded"):
            _preprocess(_raw_emg(), [GRID], params=params)


def test_filtered_emg_in_the_store_matches_the_heap(store_prep: dict) -> None:
    heap, stored = store_prep["heap"], store_prep["stored"]
    np.testing.assert_array_equal(stored.data, heap.data)
    assert stored.data.dtype == STORE_PARAMS.work_dtype
    assert resident_nbytes(stored.data) == 0
    assert not stored.data.flags.writeable
