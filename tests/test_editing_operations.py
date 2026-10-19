"""The post-hoc motor-unit editing operations: ROI peaks and deletion, rate outliers, filter refit."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from muedit.editing import operations
from muedit.editing.operations import (
    delete_spikes_in_roi,
    peaks_in_roi,
    remove_discharge_rate_outliers,
    update_motor_unit_filter_window,
)
from tests._editing_data import (
    EDGE,
    FSAMP,
    N_CHANNELS,
    N_SAMPLES,
    VIEW_END,
    VIEW_START,
    in_view,
    match_count,
    pulse_with_peaks,
    synthetic_mu,
)


@pytest.fixture(scope="module", name="synthetic_mu")
def synthetic_mu_fixture() -> dict[str, np.ndarray]:
    return synthetic_mu()


# ── peaks_in_roi ─────────────────────────────────────────────────────────────


class TestPeaksInRoi:
    def test_peaks_above_threshold_inside_roi(self) -> None:
        pulse_train = pulse_with_peaks({1000: 1.0, 1500: 0.2, 3000: 1.0})
        assert peaks_in_roi(pulse_train, FSAMP, 900, 2000, 0.5).tolist() == [1000]

    def test_roi_bounds_are_inclusive(self) -> None:
        pulse_train = pulse_with_peaks({1000: 1.0, 2000: 1.0})
        assert peaks_in_roi(pulse_train, FSAMP, 1000, 2000, 0.5).tolist() == [1000, 2000]

    def test_no_peak(self) -> None:
        assert peaks_in_roi(np.zeros(N_SAMPLES), FSAMP, 0, 100, 0.5).tolist() == []

    def test_refractory_period_keeps_one_of_close_peaks(self) -> None:
        pulse_train = pulse_with_peaks({1000: 1.0, 1005: 0.8})
        assert peaks_in_roi(pulse_train, FSAMP, 0, 2000, 0.5).tolist() == [1000]


# ── delete_spikes_in_roi ─────────────────────────────────────────────────────


class TestDeleteInRoi:
    def test_deletes_only_spikes_inside_box(self) -> None:
        pulse_train = pulse_with_peaks({100: 0.5, 200: 0.5, 300: 5.0, 900: 0.5})
        out = delete_spikes_in_roi(pulse_train, [100, 200, 300, 900], 150, 400, 0.2, 0.8)
        assert out.tolist() == [100, 300, 900]

    def test_y_bounds_order_is_irrelevant(self) -> None:
        pulse_train = pulse_with_peaks({200: 0.5})
        assert delete_spikes_in_roi(pulse_train, [200], 0, 400, 0.8, 0.2).tolist() == []

    def test_x_bounds_are_inclusive(self) -> None:
        pulse_train = pulse_with_peaks({150: 0.5, 400: 0.5})
        assert delete_spikes_in_roi(pulse_train, [150, 400], 150, 400, 0.2, 0.8).tolist() == []


# ── remove_discharge_rate_outliers ───────────────────────────────────────────


class TestRemoveDischargeRateOutliers:
    def test_removes_weaker_spike_of_outlier_pair(self) -> None:
        spikes = list(range(1000, 9001, 200))
        spikes.append(4020)
        heights = dict.fromkeys(spikes, 1.0)
        heights[4020] = 0.2
        pulse_train = pulse_with_peaks(heights)
        out = remove_discharge_rate_outliers(pulse_train, spikes, FSAMP)
        assert 4020 not in out
        assert out.tolist() == list(range(1000, 9001, 200))

    def test_regular_train_untouched(self) -> None:
        spikes = list(range(1000, 9001, 200))
        pulse_train = pulse_with_peaks(dict.fromkeys(spikes, 1.0))
        assert remove_discharge_rate_outliers(pulse_train, spikes, FSAMP).tolist() == spikes

    def test_fewer_than_three_spikes(self) -> None:
        pulse_train = np.zeros(10)
        assert remove_discharge_rate_outliers(pulse_train, [5, 1], FSAMP).tolist() == [1, 5]


# ── update_motor_unit_filter_window ──────────────────────────────────────────


def _update(
    mu: dict[str, np.ndarray],
    emg: np.ndarray | None = None,
    discard_mask: np.ndarray | None = None,
    spike_times: list[int] | None = None,
    start: int = VIEW_START,
    end: int = VIEW_END,
    **kwargs: Any,
) -> tuple[np.ndarray | None, np.ndarray]:
    """Run the filter update on ``mu``'s band-passed EMG, with overridable defaults."""
    return update_motor_unit_filter_window(
        mu["filtered"] if emg is None else emg,
        np.zeros(N_CHANNELS, dtype=int) if discard_mask is None else discard_mask,
        mu["target"].tolist() if spike_times is None else spike_times,
        FSAMP,
        start,
        end,
        **kwargs,
    )


class TestUpdateFilterWindow:
    def test_recovers_target_unit(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        pulse_train, updated = _update(synthetic_mu)
        assert pulse_train is not None
        assert pulse_train.shape == (VIEW_END - VIEW_START,)

        truth = in_view(synthetic_mu["target"])
        found = in_view(updated)
        matched = match_count(truth, found)
        assert matched == found.size
        assert matched >= 0.75 * truth.size
        assert updated.tolist() == sorted(set(updated.tolist()))
        assert updated.dtype == np.int32

    def test_spikes_outside_window_preserved(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        _, updated = _update(synthetic_mu)
        target = synthetic_mu["target"]
        outside = target[(target < VIEW_START + EDGE) | (target >= VIEW_END - EDGE)]
        assert set(outside.tolist()) <= set(updated)

    @pytest.mark.parametrize("masked", [False, True])
    def test_pca_sees_only_the_columns_spikes_come_from(
        self, synthetic_mu: dict[str, np.ndarray], monkeypatch: pytest.MonkeyPatch, masked: bool
    ) -> None:
        """Not the zero-padded ends of the extension, nor the bandpass transients at the view edges."""
        seen: list[np.ndarray | None] = []
        real = operations.pca_extended_signal

        def spy(signal: np.ndarray, columns: np.ndarray | None = None) -> Any:
            seen.append(columns)
            return real(signal, columns)

        monkeypatch.setattr(operations, "pca_extended_signal", spy)
        mask = np.zeros(N_SAMPLES, dtype=bool)
        mask[VIEW_START + 3000 : VIEW_START + 3100] = masked
        pulse_train, _ = _update(synthetic_mu, artifact_mask=mask if masked else None)
        assert pulse_train is not None
        (cols,) = seen
        assert cols is not None
        win_len = VIEW_END - VIEW_START
        assert cols.min() == EDGE and cols.max() == win_len - EDGE - 1
        ex_factor = int(round(1000 / N_CHANNELS))
        masked_cols = np.arange(3000, 3100 + ex_factor - 1)  # a column sees ex_factor samples
        assert np.isin(masked_cols, cols).any() != masked
        assert cols.size == win_len - 2 * EDGE - (masked_cols.size if masked else 0)

    def test_pulse_train_edges_zeroed(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        pulse_train, _ = _update(synthetic_mu)
        assert pulse_train is not None
        assert not pulse_train[:EDGE].any()
        assert not pulse_train[-EDGE:].any()

    def test_lock_spikes_retains_original_spikes(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        _, free = _update(synthetic_mu)
        _, locked = _update(synthetic_mu, lock_spikes=True)
        truth = in_view(synthetic_mu["target"])
        assert match_count(truth, in_view(locked)) >= match_count(truth, in_view(free))
        assert match_count(truth, in_view(locked)) >= 0.9 * truth.size

    def test_masked_channels_ignored(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        """Channels with ``discard_mask == 1`` do not influence the result."""
        emg = synthetic_mu["emg"].copy()
        emg[3] = np.random.default_rng(5).normal(0, 100.0, N_SAMPLES)
        mask = np.zeros(N_CHANNELS, dtype=int)
        mask[3] = 1
        pt_masked, up_masked = _update(synthetic_mu, emg=emg, discard_mask=mask)
        pt_ref, up_ref = _update(
            synthetic_mu,
            emg=np.delete(synthetic_mu["emg"], 3, axis=0),
            discard_mask=np.zeros(N_CHANNELS - 1, dtype=int),
        )
        assert pt_masked is not None and pt_ref is not None
        np.testing.assert_allclose(pt_masked, pt_ref)
        np.testing.assert_array_equal(up_masked, up_ref)

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
        pulse_train, updated = _update(synthetic_mu, start=start, end=end)
        assert pulse_train is None
        assert updated.tolist() == spikes

    def test_no_spikes_in_window_is_noop(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        spikes = [100, 9900]
        pulse_train, updated = _update(synthetic_mu, spike_times=spikes)
        assert pulse_train is None
        assert updated.tolist() == spikes

    def test_peeloff_and_artifact_times_run(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        """Peel-off of the other MU and manual artifact times still recover the target."""
        artifact_times = [4321]
        pulse_train, updated = _update(
            synthetic_mu,
            peel_off_spike_times=[synthetic_mu["other"].tolist()],
            artifact_times=artifact_times,
        )
        assert pulse_train is not None
        truth = in_view(synthetic_mu["target"])
        assert match_count(truth, in_view(updated)) >= 0.75 * truth.size


class TestUpdateFilterArtifactMask:
    """The artifact mask gates PCA, the pulse train and the detected spikes."""

    MASK_START, MASK_END = 4000, 5000

    def _mask(self) -> np.ndarray:
        mask = np.zeros(N_SAMPLES, dtype=bool)
        mask[self.MASK_START : self.MASK_END] = True
        return mask

    def test_nothing_detected_inside_mask(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        pulse_train, updated = _update(synthetic_mu, artifact_mask=self._mask())
        assert pulse_train is not None
        local = slice(self.MASK_START - VIEW_START, self.MASK_END - VIEW_START)
        assert not pulse_train[local].any()
        assert pulse_train.max() > 0
        arr = np.asarray(updated)
        assert not ((arr >= self.MASK_START) & (arr < self.MASK_END)).any()

    def test_spikes_outside_mask_still_recovered(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        _, updated = _update(synthetic_mu, artifact_mask=self._mask())
        truth = in_view(synthetic_mu["target"])
        truth = truth[(truth < self.MASK_START) | (truth >= self.MASK_END)]
        found = in_view(updated)
        matched = match_count(truth, found)
        assert matched == found.size
        assert matched >= 0.75 * truth.size
