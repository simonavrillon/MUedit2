"""Tests for the post-hoc motor-unit editing operations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from starlette.testclient import TestClient

from muedit.editing.operations import (
    FilterUpdateResult,
    add_artifact_in_roi,
    add_spikes_in_roi,
    delete_artifacts_in_roi,
    delete_high_discharge_rate_spikes_in_roi,
    delete_spikes_in_roi,
    remove_discharge_rate_outliers,
    update_motor_unit_filter_window,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

FSAMP = 2000.0
N_SAMPLES = 10_000
N_CHANNELS = 32
MUAP_LEN = 20
VIEW_START, VIEW_END = 1000, 9000
EDGE = int(round(0.1 * FSAMP))


def _spike_train(rate: float, seed: int, jitter: float = 20.0) -> np.ndarray:
    """Quasi-regular discharge times (samples) at ``rate`` Hz."""
    rng = np.random.default_rng(seed)
    times: list[int] = []
    t = 300
    while t < N_SAMPLES - 300:
        times.append(t)
        t += int(FSAMP / rate + rng.normal(0, jitter))
    return np.asarray(times, dtype=int)


def _muap(seed: int) -> np.ndarray:
    """Multichannel MUAP template, shape ``(N_CHANNELS, MUAP_LEN)``."""
    rng = np.random.default_rng(seed)
    tt = np.arange(MUAP_LEN)
    win = np.hanning(MUAP_LEN)
    sin = (np.sin(2 * np.pi * tt / MUAP_LEN) * win)[np.newaxis, :]
    cos = (np.cos(2 * np.pi * tt / MUAP_LEN) * win)[np.newaxis, :]
    return rng.normal(0, 1, (N_CHANNELS, 1)) * sin + 0.3 * rng.normal(0, 1, (N_CHANNELS, 1)) * cos


@pytest.fixture(scope="module")
def synthetic_mu() -> dict[str, np.ndarray]:
    """EMG with one target MU, one interfering MU and white noise."""
    rng = np.random.default_rng(0)
    target = _spike_train(12.0, seed=1)
    other = _spike_train(9.0, seed=2)
    emg = rng.normal(0, 0.01, (N_CHANNELS, N_SAMPLES))
    w_target, w_other = _muap(10), 0.5 * _muap(11)
    for s in target:
        emg[:, s : s + MUAP_LEN] += w_target
    for s in other:
        emg[:, s : s + MUAP_LEN] += w_other
    return {"emg": emg, "target": target, "other": other}


def _in_view(spikes: np.ndarray | list[int]) -> np.ndarray:
    arr = np.asarray(spikes, dtype=int)
    return arr[(arr >= VIEW_START + EDGE) & (arr < VIEW_END - EDGE)]


def _match_count(truth: np.ndarray, found: np.ndarray, tol: int = 2) -> int:
    if found.size == 0:
        return 0
    return int(sum(np.min(np.abs(found - s)) <= tol for s in truth))


def _pulse_with_peaks(peaks: dict[int, float], n: int = N_SAMPLES) -> np.ndarray:
    """Flat pulse train with isolated peaks of the given heights."""
    pulse = np.zeros(n)
    for t, h in peaks.items():
        pulse[t] = h
    return pulse


# ── add_spikes_in_roi / add_artifact_in_roi ──────────────────────────────────


@pytest.mark.parametrize("op", [add_spikes_in_roi, add_artifact_in_roi])
class TestAddInRoi:
    """Both add operations share the same peak-picking semantics."""

    def test_adds_peaks_above_threshold_inside_roi(self, op: Callable[..., list[int]]) -> None:
        pulse = _pulse_with_peaks({1000: 1.0, 1500: 0.2, 3000: 1.0})
        out = op(pulse, [500], FSAMP, 900, 2000, 0.5)
        assert out == [500, 1000]

    def test_roi_bounds_are_inclusive(self, op: Callable[..., list[int]]) -> None:
        pulse = _pulse_with_peaks({1000: 1.0, 2000: 1.0})
        assert op(pulse, [], FSAMP, 1000, 2000, 0.5) == [1000, 2000]

    def test_no_peak_returns_sorted_unique_input(self, op: Callable[..., list[int]]) -> None:
        pulse = np.zeros(N_SAMPLES)
        assert op(pulse, [30, 10, 30], FSAMP, 0, 100, 0.5) == [10, 30]

    def test_existing_spike_not_duplicated(self, op: Callable[..., list[int]]) -> None:
        pulse = _pulse_with_peaks({1000: 1.0})
        assert op(pulse, [1000], FSAMP, 0, 2000, 0.5) == [1000]

    def test_refractory_period_keeps_one_of_close_peaks(self, op: Callable[..., list[int]]) -> None:
        pulse = _pulse_with_peaks({1000: 1.0, 1005: 0.8})
        assert op(pulse, [], FSAMP, 0, 2000, 0.5) == [1000]


# ── delete_spikes_in_roi / delete_artifacts_in_roi ───────────────────────────


@pytest.mark.parametrize("op", [delete_spikes_in_roi, delete_artifacts_in_roi])
class TestDeleteInRoi:
    """Both delete operations share the same box semantics."""

    def test_deletes_only_spikes_inside_box(self, op: Callable[..., list[int]]) -> None:
        pulse = _pulse_with_peaks({100: 0.5, 200: 0.5, 300: 5.0, 900: 0.5})
        out = op(pulse, [100, 200, 300, 900], 150, 400, 0.2, 0.8)
        assert out == [100, 300, 900]

    def test_y_bounds_order_is_irrelevant(self, op: Callable[..., list[int]]) -> None:
        pulse = _pulse_with_peaks({200: 0.5})
        assert op(pulse, [200], 0, 400, 0.8, 0.2) == []

    def test_x_bounds_are_inclusive(self, op: Callable[..., list[int]]) -> None:
        pulse = _pulse_with_peaks({150: 0.5, 400: 0.5})
        assert op(pulse, [150, 400], 150, 400, 0.2, 0.8) == []


# ── delete_high_discharge_rate_spikes_in_roi ─────────────────────────────────


class TestDeleteHighDischargeRate:
    def test_removes_weaker_spike_of_fast_pair(self) -> None:
        spikes = [1000, 2000, 2040, 3000]
        pulse = _pulse_with_peaks({1000: 1.0, 2000: 1.0, 2040: 0.3, 3000: 1.0})
        out = delete_high_discharge_rate_spikes_in_roi(pulse, spikes, FSAMP, 1500, 2500, 20.0)
        assert out == [1000, 2000, 3000]

    def test_keeps_stronger_spike_when_left_is_weaker(self) -> None:
        spikes = [2000, 2040]
        pulse = _pulse_with_peaks({2000: 0.3, 2040: 1.0})
        out = delete_high_discharge_rate_spikes_in_roi(pulse, spikes, FSAMP, 0, 5000, 20.0)
        assert out == [2040]

    def test_pair_midpoint_outside_roi_untouched(self) -> None:
        spikes = [2000, 2040]
        pulse = _pulse_with_peaks({2000: 1.0, 2040: 0.3})
        out = delete_high_discharge_rate_spikes_in_roi(pulse, spikes, FSAMP, 3000, 5000, 20.0)
        assert out == spikes

    def test_rate_below_threshold_untouched(self) -> None:
        spikes = [2000, 2040]
        pulse = _pulse_with_peaks({2000: 1.0, 2040: 0.3})
        out = delete_high_discharge_rate_spikes_in_roi(pulse, spikes, FSAMP, 0, 5000, 60.0)
        assert out == spikes

    def test_fewer_than_two_spikes(self) -> None:
        pulse = np.zeros(10)
        assert delete_high_discharge_rate_spikes_in_roi(pulse, [5], FSAMP, 0, 10, 1.0) == [5]
        assert delete_high_discharge_rate_spikes_in_roi(pulse, [], FSAMP, 0, 10, 1.0) == []


# ── remove_discharge_rate_outliers ───────────────────────────────────────────


class TestRemoveDischargeRateOutliers:
    def test_removes_weaker_spike_of_outlier_pair(self) -> None:
        spikes = list(range(1000, 9001, 200))
        spikes.append(4020)
        heights = dict.fromkeys(spikes, 1.0)
        heights[4020] = 0.2
        pulse = _pulse_with_peaks(heights)
        out = remove_discharge_rate_outliers(pulse, spikes, FSAMP)
        assert 4020 not in out
        assert out == list(range(1000, 9001, 200))

    def test_regular_train_untouched(self) -> None:
        spikes = list(range(1000, 9001, 200))
        pulse = _pulse_with_peaks(dict.fromkeys(spikes, 1.0))
        assert remove_discharge_rate_outliers(pulse, spikes, FSAMP) == spikes

    def test_z_factor_controls_sensitivity(self) -> None:
        spikes = [*range(1000, 9001, 200), 4100]
        pulse = _pulse_with_peaks(dict.fromkeys(spikes, 1.0))
        strict = remove_discharge_rate_outliers(pulse, spikes, FSAMP, z_factor=0.5)
        lenient = remove_discharge_rate_outliers(pulse, spikes, FSAMP, z_factor=100.0)
        assert len(strict) < len(spikes)
        assert lenient == sorted(spikes)

    def test_fewer_than_three_spikes(self) -> None:
        pulse = np.zeros(10)
        assert remove_discharge_rate_outliers(pulse, [5, 1], FSAMP) == [1, 5]


# ── update_motor_unit_filter_window ──────────────────────────────────────────


def _update(
    mu: dict[str, np.ndarray],
    emg: np.ndarray | None = None,
    emg_mask: np.ndarray | None = None,
    spike_times: list[int] | None = None,
    start: int = VIEW_START,
    end: int = VIEW_END,
    **kwargs: Any,
) -> FilterUpdateResult:
    """Run the filter update on ``mu`` with overridable defaults."""
    return update_motor_unit_filter_window(
        mu["emg"] if emg is None else emg,
        np.zeros(N_CHANNELS, dtype=int) if emg_mask is None else emg_mask,
        mu["target"].tolist() if spike_times is None else spike_times,
        FSAMP,
        start,
        end,
        **kwargs,
    )


class TestUpdateFilterWindow:
    def test_recovers_target_unit(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        pt, updated = _update(synthetic_mu)
        assert pt is not None
        assert pt.shape == (VIEW_END - VIEW_START,)

        truth = _in_view(synthetic_mu["target"])
        found = _in_view(updated)
        matched = _match_count(truth, found)
        assert matched == found.size
        assert matched >= 0.75 * truth.size
        assert updated == sorted(set(updated))
        assert all(type(x) is int for x in updated)

    def test_spikes_outside_window_preserved(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        _, updated = _update(synthetic_mu)
        target = synthetic_mu["target"]
        outside = target[(target < VIEW_START + EDGE) | (target >= VIEW_END - EDGE)]
        assert set(outside.tolist()) <= set(updated)

    def test_pulse_train_edges_zeroed(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        pt, _ = _update(synthetic_mu)
        assert pt is not None
        assert not pt[:EDGE].any()
        assert not pt[-EDGE:].any()

    def test_lock_spikes_retains_original_spikes(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        _, free = _update(synthetic_mu)
        _, locked = _update(synthetic_mu, lock_spikes=True)
        truth = _in_view(synthetic_mu["target"])
        assert _match_count(truth, _in_view(locked)) >= _match_count(truth, _in_view(free))
        assert _match_count(truth, _in_view(locked)) >= 0.9 * truth.size

    def test_emg_offset_matches_full_signal(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        """A pre-sliced EMG with ``emg_offset`` gives the same result as the full EMG."""
        pt_full, up_full = _update(synthetic_mu)
        sliced = synthetic_mu["emg"][:, VIEW_START:VIEW_END]
        pt_sl, up_sl = _update(synthetic_mu, emg=sliced, emg_offset=VIEW_START)
        assert pt_sl is not None and pt_full is not None
        np.testing.assert_allclose(pt_sl, pt_full)
        assert up_sl == up_full

    def test_masked_channels_ignored(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        """Channels with ``emg_mask == 1`` do not influence the result."""
        emg = synthetic_mu["emg"].copy()
        emg[3] = np.random.default_rng(5).normal(0, 100.0, N_SAMPLES)
        mask = np.zeros(N_CHANNELS, dtype=int)
        mask[3] = 1
        pt_masked, up_masked = _update(synthetic_mu, emg=emg, emg_mask=mask)
        pt_ref, up_ref = _update(
            synthetic_mu, emg=np.delete(synthetic_mu["emg"], 3, axis=0), emg_mask=np.array([])
        )
        assert pt_masked is not None and pt_ref is not None
        np.testing.assert_allclose(pt_masked, pt_ref)
        assert up_masked == up_ref

    @pytest.mark.parametrize(
        "start,end",
        [(5000, 5000), (6000, 5000), (5000, 5000 + 2 * EDGE)],
        ids=["empty", "reversed", "shorter-than-edges"],
    )
    def test_degenerate_window_is_noop(
        self,
        synthetic_mu: dict[str, np.ndarray],
        start: int,
        end: int,
    ) -> None:
        spikes = synthetic_mu["target"].tolist()
        pt, updated = _update(synthetic_mu, start=start, end=end)
        assert pt is None
        assert updated == spikes

    def test_no_spikes_in_window_is_noop(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        spikes = [100, 9900]
        pt, updated = _update(synthetic_mu, spike_times=spikes)
        assert pt is None
        assert updated == spikes

    def test_peeloff_and_artifact_times_run(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        """Peel-off of the other MU and manual artifact times still recover the target."""
        artifact_times = [4321]
        pt, updated = _update(
            synthetic_mu,
            use_peeloff=True,
            peeloff_spike_times=[synthetic_mu["other"].tolist()],
            artifact_times=artifact_times,
        )
        assert pt is not None
        truth = _in_view(synthetic_mu["target"])
        assert _match_count(truth, _in_view(updated)) >= 0.75 * truth.size


class TestUpdateFilterArtifactMask:
    """The artifact mask gates PCA, the pulse train and the detected spikes."""

    MASK_START, MASK_END = 4000, 5000

    def _mask(self) -> np.ndarray:
        mask = np.zeros(N_SAMPLES, dtype=bool)
        mask[self.MASK_START : self.MASK_END] = True
        return mask

    def test_nothing_detected_inside_mask(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        pt, updated = _update(synthetic_mu, artifact_mask=self._mask())
        assert pt is not None
        local = slice(self.MASK_START - VIEW_START, self.MASK_END - VIEW_START)
        assert not pt[local].any()
        assert pt.max() > 0
        arr = np.asarray(updated)
        assert not ((arr >= self.MASK_START) & (arr < self.MASK_END)).any()

    def test_spikes_outside_mask_still_recovered(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        _, updated = _update(synthetic_mu, artifact_mask=self._mask())
        truth = _in_view(synthetic_mu["target"])
        truth = truth[(truth < self.MASK_START) | (truth >= self.MASK_END)]
        found = _in_view(updated)
        matched = _match_count(truth, found)
        assert matched == found.size
        assert matched >= 0.75 * truth.size


# ── /api/v1/edit/* routes ────────────────────────────────────────────────────


@pytest.fixture()
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """FastAPI TestClient with DATA_ROOT redirected to a tmp dir."""
    from fastapi import FastAPI

    import muedit.api.config as config
    import muedit.api.services.editing_service as editing_service
    from muedit.api.routes import include_routers

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(editing_service, "DATA_ROOT", tmp_path)
    app = FastAPI()
    include_routers(app)
    with TestClient(app) as client:
        yield client


def _post(client: TestClient, route: str, body: dict) -> dict:
    resp = client.post(f"/api/v1/edit/{route}", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestEditRoutes:
    def test_add_spikes(self, api_client: TestClient) -> None:
        pulse = _pulse_with_peaks({1000: 1.0, 3000: 1.0}, n=4000).tolist()
        data = _post(
            api_client,
            "add-spikes",
            {
                "distimes": [[10], [500]],
                "mu_index": 1,
                "pulse_train": pulse,
                "fsamp": FSAMP,
                "x_start": 900,
                "x_end": 1100,
                "y_min": 0.5,
            },
        )
        assert data == {"distimes": [500, 1000]}

    def test_add_artifact(self, api_client: TestClient) -> None:
        pulse = _pulse_with_peaks({1000: 1.0}, n=4000).tolist()
        data = _post(
            api_client,
            "add-artifact",
            {
                "distimes": [[]],
                "pulse_train": pulse,
                "fsamp": FSAMP,
                "x_start": 900,
                "x_end": 1100,
                "y_min": 0.5,
                "artifact_times": [50],
            },
        )
        assert data == {"artifact_times": [50, 1000]}

    def test_delete_spikes_also_deletes_artifacts(self, api_client: TestClient) -> None:
        pulse = _pulse_with_peaks({100: 0.5, 200: 0.5, 300: 0.5}, n=1000).tolist()
        data = _post(
            api_client,
            "delete-spikes",
            {
                "distimes": [[100, 200]],
                "pulse_train": pulse,
                "x_start": 150,
                "x_end": 400,
                "y_min": 0.2,
                "y_max": 0.8,
                "artifact_times": [300, 900],
            },
        )
        assert data == {"distimes": [100], "artifact_times": [900]}

    def test_delete_dr(self, api_client: TestClient) -> None:
        pulse = _pulse_with_peaks({1000: 1.0, 2000: 1.0, 2040: 0.3}, n=4000).tolist()
        data = _post(
            api_client,
            "delete-dr",
            {
                "distimes": [[1000, 2000, 2040]],
                "pulse_train": pulse,
                "fsamp": FSAMP,
                "x_start": 1500,
                "x_end": 2500,
                "y_min": 20.0,
            },
        )
        assert data == {"distimes": [1000, 2000]}

    def test_remove_outliers_reports_count(self, api_client: TestClient) -> None:
        spikes = [*range(1000, 9001, 200), 4020]
        heights = dict.fromkeys(spikes, 1.0)
        heights[4020] = 0.2
        data = _post(
            api_client,
            "remove-outliers",
            {
                "distimes": [spikes],
                "pulse_train": _pulse_with_peaks(heights).tolist(),
                "fsamp": FSAMP,
            },
        )
        assert data["removed_count"] == 1
        assert 4020 not in data["distimes"]

    def test_remove_duplicates(self, api_client: TestClient) -> None:
        a = _spike_train(10.0, seed=3).tolist()
        b = _spike_train(8.0, seed=4).tolist()
        data = _post(
            api_client,
            "remove-duplicates",
            {
                "distimes": [a, [t + 1 for t in a], b],
                "fsamp": FSAMP,
                "total_samples": N_SAMPLES,
            },
        )
        assert data["removed_count"] == 1
        assert len(data["kept_indices"]) == 2
        assert 2 in data["kept_indices"]

    def test_remove_duplicates_keeps_original_order(self, api_client: TestClient) -> None:
        a = _spike_train(10.0, seed=3).tolist()
        b = _spike_train(8.0, seed=4).tolist()
        # An extra mid-ISI spike raises MU 0's CoV, so its duplicate MU 2 is kept instead.
        noisy_a = sorted([*a, (a[5] + a[6]) // 2])
        data = _post(
            api_client,
            "remove-duplicates",
            {"distimes": [noisy_a, b, a], "fsamp": FSAMP, "total_samples": N_SAMPLES},
        )
        assert data["kept_indices"] == [1, 2]
        assert data["distimes"] == [b, a]

    @pytest.mark.parametrize(
        "bgrids,kept",
        [(None, [0, 1]), (False, [0, 1, 2]), (0, [0, 1, 2]), (True, [0, 1]), ([[1.0]], [0, 1])],
    )
    def test_remove_duplicates_across_grids_unless_disabled(
        self, api_client: TestClient, bgrids: Any, kept: list[int]
    ) -> None:
        a = _spike_train(10.0, seed=3).tolist()
        b = _spike_train(8.0, seed=4).tolist()
        parameters = {} if bgrids is None else {"duplicatesbgrids": bgrids}
        data = _post(
            api_client,
            "remove-duplicates",
            {
                "distimes": [a, b, a],
                "mu_grid_index": [0, 0, 1],
                "parameters": parameters,
                "fsamp": FSAMP,
                "total_samples": N_SAMPLES,
            },
        )
        assert data["kept_indices"] == kept

    @pytest.mark.parametrize("bgrids", [False, True])
    def test_remove_duplicates_matches_decomposition(
        self, api_client: TestClient, bgrids: bool
    ) -> None:
        from muedit.decomp.decomposition_file import build_pulse_trains_from_distimes
        from muedit.decomp.postprocess import remove_duplicates_by_grid
        from muedit.decomp.types import DecompositionParameters

        a = _spike_train(10.0, seed=3).tolist()
        b = _spike_train(8.0, seed=4).tolist()
        c = _spike_train(12.0, seed=5).tolist()
        noisy_a = sorted([*a, (a[5] + a[6]) // 2])
        distimes = [a, b, noisy_a, c, a, b]
        grids = [0, 0, 1, 1, 1, 2]
        _, _, _, pipeline_kept = remove_duplicates_by_grid(
            build_pulse_trains_from_distimes(distimes, N_SAMPLES),
            [np.asarray(d) for d in distimes],
            grids,
            3,
            DecompositionParameters(duplicatesbgrids=bgrids),
            FSAMP,
        )
        data = _post(
            api_client,
            "remove-duplicates",
            {
                "distimes": distimes,
                "mu_grid_index": grids,
                "parameters": {"duplicatesbgrids": bgrids},
                "fsamp": FSAMP,
                "total_samples": N_SAMPLES,
            },
        )
        assert data["kept_indices"] == sorted(pipeline_kept)

    def test_save_logs_mus_removed_on_save(self, api_client: TestClient, tmp_path: Path) -> None:
        import json

        a = _spike_train(10.0, seed=3).tolist()
        b = _spike_train(8.0, seed=4).tolist()
        c = _spike_train(12.0, seed=5).tolist()
        history = [{"type": "duplicate_mu", "mu_uid": "g0_mu3", "source_mu_uid": "g0_mu0"}]
        data = _post(
            api_client,
            "save",
            {
                "distimes": [a, b, c, a],
                "flagged": [False, True, False, False],
                "mu_uids": ["g0_mu0", "g0_mu1", "g0_mu2", "g0_mu3"],
                "mu_grid_index": [0, 0, 0, 0],
                "edit_history": history,
                "total_samples": N_SAMPLES,
                "fsamp": FSAMP,
                "entity_label": "sub-01_task-x",
                "file_label": "sub-01_task-x_edited.npz",
            },
        )
        assert data["kept_indices"] == [0, 2]
        assert data["mu_uids"] == ["g0_mu0", "g0_mu2"]
        removals = [
            {k: e[k] for k in ("type", "on_save", "removed_mu_uids")}
            for e in data["edit_history"][1:]
        ]
        assert removals == [
            {"type": "remove_flagged", "on_save": True, "removed_mu_uids": ["g0_mu1"]},
            {"type": "remove_duplicates", "on_save": True, "removed_mu_uids": ["g0_mu3"]},
        ]
        editlog = json.loads(Path(data["path"]).with_suffix(".json").read_text())
        assert editlog["mu_uids"] == data["mu_uids"]
        assert editlog["history"] == data["edit_history"]

    def test_save_without_dedup_keeps_duplicates(self, api_client: TestClient) -> None:
        a = _spike_train(10.0, seed=3).tolist()
        data = _post(
            api_client,
            "save",
            {
                "distimes": [a, a],
                "mu_uids": ["g0_mu0", "g0_mu1"],
                "remove_duplicates": False,
                "total_samples": N_SAMPLES,
                "fsamp": FSAMP,
                "entity_label": "sub-01_task-x",
            },
        )
        assert data["kept_indices"] == [0, 1]
        assert data["edit_history"] == []

    @pytest.mark.parametrize("flag,expected", [(None, True), (True, True), (False, False)])
    def test_flag_mu(self, api_client: TestClient, flag: bool | None, expected: bool) -> None:
        body = {"distimes": [[1]], "mu_index": 0}
        if flag is not None:
            body["flag"] = flag
        assert _post(api_client, "flag-mu", body) == {"flagged": expected}

    @pytest.mark.parametrize(
        "route,body",
        [
            ("add-spikes", {"distimes": [[1]], "fsamp": FSAMP}),
            ("add-spikes", {"distimes": [[1]], "pulse_train": [0.0]}),
            (
                "add-spikes",
                {"distimes": [[1]], "pulse_train": [0.0], "fsamp": FSAMP, "mu_index": 3},
            ),
            ("add-artifact", {"distimes": [[1]], "pulse_train": [0.0]}),
            ("delete-spikes", {"distimes": [[1]], "pulse_train": [0.0], "mu_index": -1}),
            ("delete-dr", {"distimes": [[1]], "pulse_train": [0.0]}),
            ("remove-outliers", {"distimes": [[1]], "fsamp": FSAMP}),
            ("remove-duplicates", {"distimes": [[1], [2]]}),
            ("flag-mu", {"distimes": [[1]], "mu_index": 1}),
        ],
    )
    def test_invalid_requests_rejected(
        self,
        api_client: TestClient,
        route: str,
        body: dict[str, Any],
    ) -> None:
        resp = api_client.post(f"/api/v1/edit/{route}", json=body)
        assert resp.status_code == 400


class TestUpdateFilterRoute:
    """``/edit/update-filter`` with the EMG served from the edit-signal cache."""

    @pytest.fixture()
    def ctx_token(self, synthetic_mu: dict[str, np.ndarray]) -> Callable[..., str]:
        """Cache a 64-channel GR08MM1305 context with the MU on the first 32 channels."""
        from muedit.api.cache import _store_edit_signal_context
        from muedit.models import EditSignalContext

        rng = np.random.default_rng(7)
        data = np.vstack(
            [
                synthetic_mu["emg"],
                rng.normal(0, 0.01, (64 - N_CHANNELS, N_SAMPLES)),
            ]
        )

        def store(artifact_mask: np.ndarray | None = None) -> str:
            return _store_edit_signal_context(
                EditSignalContext(
                    data=data,
                    fsamp=FSAMP,
                    grid_names=["GR08MM1305"],
                    emgmask=[np.zeros(64, dtype=int)],
                    artifact_mask=artifact_mask,
                )
            )

        return store

    def _body(
        self, token: str, synthetic_mu: dict[str, np.ndarray], **extra: Any
    ) -> dict[str, Any]:
        body = {
            "project": "proj",
            "edit_signal_token": token,
            "file_label": "sub-01_task-synthetic_decomp.npz",
            "distimes": [synthetic_mu["target"].tolist()],
            "pulse_train": [0.0] * N_SAMPLES,
            "view_start": VIEW_START,
            "view_end": VIEW_END,
        }
        body.update(extra)
        return body

    def test_update_from_cached_context(
        self,
        api_client: TestClient,
        ctx_token: Callable[..., str],
        synthetic_mu: dict[str, np.ndarray],
    ) -> None:
        data = _post(api_client, "update-filter", self._body(ctx_token(), synthetic_mu))
        assert data["fsamp"] == FSAMP
        truth = _in_view(synthetic_mu["target"])
        assert _match_count(truth, _in_view(data["distimes"])) >= 0.5 * truth.size
        pulse = np.asarray(data["pulse_train"])
        assert pulse.shape == (N_SAMPLES,)
        assert pulse[VIEW_START + EDGE : VIEW_END - EDGE].any()
        assert not pulse[: VIEW_START + EDGE].any()

    def test_cached_artifact_mask_is_applied(
        self,
        api_client: TestClient,
        ctx_token: Callable[..., str],
        synthetic_mu: dict[str, np.ndarray],
    ) -> None:
        mask = np.zeros(N_SAMPLES, dtype=bool)
        mask[4000:5000] = True
        data = _post(api_client, "update-filter", self._body(ctx_token(mask), synthetic_mu))
        spikes = np.asarray(data["distimes"])
        assert not ((spikes >= 4000) & (spikes < 5000)).any()
        assert not np.asarray(data["pulse_train"])[4000:5000].any()

    def test_missing_context_rejected(
        self,
        api_client: TestClient,
        synthetic_mu: dict[str, np.ndarray],
    ) -> None:
        resp = api_client.post("/api/v1/edit/update-filter", json=self._body("nope", synthetic_mu))
        assert resp.status_code == 400

    @pytest.mark.parametrize(
        "extra",
        [
            {"distimes": []},
            {"mu_index": 5},
            {"view_end": VIEW_START},
            {"grid_index": 3},
            {"view_end": N_SAMPLES + 1},
        ],
        ids=["no-distimes", "bad-mu", "empty-view", "bad-grid", "view-overrun"],
    )
    def test_invalid_requests_rejected(
        self,
        api_client: TestClient,
        ctx_token: Callable[..., str],
        synthetic_mu: dict[str, np.ndarray],
        extra: dict[str, Any],
    ) -> None:
        resp = api_client.post(
            "/api/v1/edit/update-filter",
            json=self._body(ctx_token(), synthetic_mu, **extra),
        )
        assert resp.status_code == 400
