"""The edit session: parity with the edit operations, the filter refit, undo, replay and saving."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from muedit.decomp.decomposition_file import load_decomposition, save_decomposition_npz
from muedit.decomp.preprocess import preprocess_step
from muedit.decomp.types import DecompositionParameters, LoadStepOutput
from muedit.editing import session as session_module
from muedit.editing.edit_log import EditLog, find_recoverable
from muedit.editing.operations import (
    delete_spikes_in_roi,
    peaks_in_roi,
    remove_discharge_rate_outliers,
    update_motor_unit_filter_window,
)
from muedit.editing.session import EditError, EditSession, initial_mu_uids, next_mu_uid
from muedit.models import EditSignalContext, SignalImport, spike_array
from muedit.storage import SessionStore
from tests._editing_data import (
    EDGE,
    FSAMP,
    N_CHANNELS,
    N_SAMPLES,
    VIEW_END,
    VIEW_START,
    noisy_pulse_trains,
    spike_train,
    synthetic_mu,
)
from tests._platform import deleted


def _session(
    spikes: list[Any],
    pulse_trains: np.ndarray | None = None,
    grids: list[int] | None = None,
    **kw: Any,
) -> EditSession:
    return EditSession(
        store=SessionStore.create("test-edit"),
        fsamp=FSAMP,
        total_samples=N_SAMPLES,
        spike_times=spikes,
        pulse_trains=pulse_trains,
        mu_grid_index=grids or [0] * len(spikes),
        mu_uids=[f"g0_mu{i}" for i in range(len(spikes))],
        **kw,
    )


@pytest.fixture(scope="module", name="synthetic_mu")
def synthetic_mu_fixture() -> dict[str, np.ndarray]:
    return synthetic_mu()


@pytest.fixture()
def pulse_trains() -> np.ndarray:
    return noisy_pulse_trains(3)


@pytest.fixture()
def edit(pulse_trains: np.ndarray) -> Iterator[EditSession]:
    session = _session([spike_train(10.0, seed=s).tolist() for s in (1, 2, 3)], pulse_trains.copy())
    yield session
    session.close()


# ── parity with the edit functions on the whole train ────────────────────────


class TestParity:
    @pytest.mark.parametrize("roi", [(900, 1100), (0, 400), (N_SAMPLES - 300, N_SAMPLES + 50)])
    def test_add_spikes(
        self, edit: EditSession, pulse_trains: np.ndarray, roi: tuple[int, int]
    ) -> None:
        before = edit.spike_times[1].tolist()
        peaks = peaks_in_roi(pulse_trains[1].astype(float), FSAMP, *roi, 0.6)
        expected = spike_array(np.concatenate([before, peaks]))
        edit.apply("add-spikes", {"mu": 1, "x_start": roi[0], "x_end": roi[1], "y_min": 0.6})
        assert edit.spike_times[1].tolist() == expected.tolist()
        assert edit.spike_times[1].dtype == np.int32

    def test_add_artifact(self, edit: EditSession, pulse_trains: np.ndarray) -> None:
        expected = peaks_in_roi(pulse_trains[0].astype(float), FSAMP, 2000, 4000, 0.6)
        edit.apply("add-artifact", {"mu": 0, "x_start": 2000, "x_end": 4000, "y_min": 0.6})
        assert edit.artifact_times[0].tolist() == expected.tolist()

    def test_delete_spikes(self, edit: EditSession, pulse_trains: np.ndarray) -> None:
        before = edit.spike_times[2].tolist()
        expected = delete_spikes_in_roi(
            pulse_trains[2].astype(float), before, 1000, 6000, 0.0, 0.15
        )
        edit.apply(
            "delete-spikes", {"mu": 2, "x_start": 1000, "x_end": 6000, "y_min": 0.0, "y_max": 0.15}
        )
        assert edit.spike_times[2].tolist() == expected.tolist()
        assert edit.history[-1]["type"] == "delete_spikes"

    def test_remove_outliers(self, edit: EditSession, pulse_trains: np.ndarray) -> None:
        edit.spike_times[0] = np.asarray(
            sorted({*edit.spike_times[0].tolist(), 4020}), dtype=np.int32
        )
        before = edit.spike_times[0].tolist()
        expected = remove_discharge_rate_outliers(pulse_trains[0].astype(float), before, FSAMP)
        change = edit.apply("remove-outliers", {"mu": 0})
        assert edit.spike_times[0].tolist() == expected.tolist()
        assert change.info["removed_count"] == len(before) - len(expected)

    def test_drawn_trains_without_pulse(self) -> None:
        edit = _session([[100, 200, 300]])
        try:
            assert not edit.has_pulse(0)
            np.testing.assert_array_equal(np.flatnonzero(edit.values(0, 0, 1000)), [100, 200, 300])
            edit.apply("delete-spikes", {"mu": 0, "x_start": 150, "x_end": 250, "y_max": 1.0})
            assert edit.spike_times[0].tolist() == [100, 300]
        finally:
            edit.close()


# ── update filter ────────────────────────────────────────────────────────────


def _emg_signal(
    synthetic: dict[str, np.ndarray], mask: np.ndarray | None = None
) -> EditSignalContext:
    """A 64-channel GR08MM1305 context with the MU on the first 32 channels."""
    rng = np.random.default_rng(7)
    data = np.vstack([synthetic["emg"], rng.normal(0, 0.01, (64 - N_CHANNELS, N_SAMPLES))])
    return EditSignalContext(
        data=data.astype(np.float32),
        fsamp=FSAMP,
        grid_names=["GR08MM1305"],
        discard_channels=[np.zeros(64, dtype=int)],
        artifact_mask=mask,
    )


def _decomposition_filtered(ctx: EditSignalContext) -> np.ndarray:
    """``ctx``'s EMG as ``preprocess_step`` filters it before a decomposition (default float32)."""
    signal = SignalImport(
        data=ctx.data,
        fsamp=ctx.fsamp,
        grid_names=list(ctx.grid_names),
        aux_data=np.zeros((0, ctx.data.shape[1])),
    )
    prep = preprocess_step(
        loaded=LoadStepOutput("x", signal),
        duration=None,
        manual_roi=False,
        rois=None,
        params=DecompositionParameters(),
        discard_overrides=None,
    )
    return np.asarray(prep.data)


class TestUpdateFilter:
    def test_refits_on_the_emg_as_the_decomposition_filtered_it(
        self, synthetic_mu: dict[str, np.ndarray]
    ) -> None:
        """Notch and the grid's bandpass over the whole recording, built once in the store."""
        signal = _emg_signal(synthetic_mu)
        edit = _session([synthetic_mu["target"].tolist()], signal=signal)
        try:
            emg, fsamp, _ = edit._grid_emg(None, 0)
            np.testing.assert_array_equal(emg, _decomposition_filtered(signal))
            assert fsamp == FSAMP
            assert Path(getattr(emg, "filename", "")).parent == edit.store.path.resolve()
            assert edit._grid_emg(None, 0)[0] is emg  # the next refit reuses it
        finally:
            edit.close()

    def test_matches_the_window_update_and_writes_the_train(
        self, synthetic_mu: dict[str, np.ndarray]
    ) -> None:
        signal = _emg_signal(synthetic_mu)
        target = synthetic_mu["target"].tolist()
        edit = _session([target], np.zeros((1, N_SAMPLES), np.float32), signal=signal)
        try:
            pulse_train, expected = update_motor_unit_filter_window(
                _decomposition_filtered(signal),
                np.zeros(64, int),
                target,
                FSAMP,
                VIEW_START,
                VIEW_END,
            )
            assert pulse_train is not None
            edit.apply("update-filter", {"mu": 0, "view_start": VIEW_START, "view_end": VIEW_END})
            assert edit.spike_times[0].tolist() == expected.tolist()
            row = edit.values(0, 0, N_SAMPLES)
            np.testing.assert_array_equal(
                row[VIEW_START + EDGE : VIEW_END - EDGE],
                pulse_train[EDGE : len(pulse_train) - EDGE].astype(np.float32),
            )
            assert not row[: VIEW_START + EDGE].any()
            # The file's train is untouched: the MU got its own copy.
            assert edit.owned[0] and edit.rows[0] != ("base", 0)
            assert not edit.arrays["base"].any()
        finally:
            edit.close()

    def test_a_prepared_grid_is_filtered_while_edits_hold_the_lock(
        self, synthetic_mu: dict[str, np.ndarray]
    ) -> None:
        edit = _session(
            [synthetic_mu["target"].tolist()],
            np.zeros((1, N_SAMPLES), np.float32),
            signal=_emg_signal(synthetic_mu),
        )
        held, done = threading.Event(), threading.Event()

        def edit_in_progress() -> None:
            with edit.lock:
                held.set()
                done.wait(10)

        other = threading.Thread(target=edit_in_progress)
        other.start()
        try:
            held.wait(10)
            edit.prepare_grid(None, 0)
            edit.prepare_grid(None, 3)  # no MU on that grid: nothing to filter
            done.set()
            other.join()
            assert list(edit._filtered) == [(None, 0)]
            emg = edit._filtered[(None, 0)][0]
            edit.apply("update-filter", {"mu": 0, "view_start": VIEW_START, "view_end": VIEW_END})
            assert edit._grid_emg(None, 0)[0] is emg
        finally:
            done.set()
            other.join()
            edit.close()
        with pytest.raises(EditError, match="closed"):
            edit.prepare_grid(None, 0)

    def test_artifact_mask_is_applied(self, synthetic_mu: dict[str, np.ndarray]) -> None:
        mask = np.zeros(N_SAMPLES, dtype=bool)
        mask[4000:5000] = True
        edit = _session(
            [synthetic_mu["target"].tolist()],
            np.zeros((1, N_SAMPLES), np.float32),
            signal=_emg_signal(synthetic_mu, mask),
        )
        try:
            edit.apply("update-filter", {"mu": 0, "view_start": VIEW_START, "view_end": VIEW_END})
            spikes = edit.spike_times[0]
            assert not ((spikes >= 4000) & (spikes < 5000)).any()
            assert not edit.values(0, 4000, 5000).any()
        finally:
            edit.close()

    @pytest.mark.parametrize(
        "args",
        [
            {"mu": 5, "view_start": VIEW_START, "view_end": VIEW_END},
            {"mu": 0, "view_start": VIEW_START, "view_end": VIEW_START},
            {"mu": 0, "view_start": VIEW_START, "view_end": N_SAMPLES + 1},
        ],
        ids=["bad-mu", "empty-view", "view-overrun"],
    )
    def test_invalid_requests_rejected(
        self, synthetic_mu: dict[str, np.ndarray], args: dict[str, Any]
    ) -> None:
        edit = _session([synthetic_mu["target"].tolist()], signal=_emg_signal(synthetic_mu))
        try:
            with pytest.raises(EditError):
                edit.apply("update-filter", args)
        finally:
            edit.close()

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            ({}, (500, 0.03, "float64")),
            ({"peel_off_win": 0.05}, (500, 0.05, "float64")),
        ],
        ids=["run-settings", "request-overrides"],
    )
    def test_refit_follows_the_run_settings(
        self,
        synthetic_mu: dict[str, np.ndarray],
        monkeypatch: pytest.MonkeyPatch,
        args: dict[str, Any],
        expected: tuple[int, float, str],
    ) -> None:
        seen: dict[str, Any] = {}

        def spy(*a: Any, **kw: Any) -> tuple[None, np.ndarray]:
            seen.update(kw)
            return None, np.asarray(a[2], dtype=np.int64)

        monkeypatch.setattr(session_module, "update_motor_unit_filter_window", spy)
        edit = _session([synthetic_mu["target"].tolist()], signal=_emg_signal(synthetic_mu))
        edit.meta["parameters"] = {
            "nbextchan": 500,
            "peel_off_win": 0.03,
            "compute_dtype": "float64",
        }
        try:
            edit.apply(
                "update-filter", {"mu": 0, "view_start": VIEW_START, "view_end": VIEW_END, **args}
            )
            assert (seen["nbextchan"], seen["peel_off_win"], seen["compute_dtype"]) == expected
        finally:
            edit.close()

    def test_no_emg_is_an_error(self) -> None:
        edit = _session([[100]])
        try:
            with pytest.raises(EditError, match="No BIDS EMG"):
                edit.apply("update-filter", {"mu": 0, "view_start": 0, "view_end": 5000})
        finally:
            edit.close()


# ── undo, copy-on-write, structure ───────────────────────────────────────────


def _state(edit: EditSession) -> tuple[Any, ...]:
    return (
        [s.tolist() for s in edit.spike_times],
        [a.tolist() for a in edit.artifact_times],
        list(edit.flagged),
        list(edit.mu_uids),
        [edit.values(i, 0, N_SAMPLES).tobytes() for i in range(edit.n_mu)],
        len(edit.history),
    )


def _write_patch(edit: EditSession, mu: int, start: int, values: np.ndarray) -> None:
    """What a refit does to the train: an undo step, then an in-place write."""
    step = edit._begin("update_filter", mu)
    row, copied = edit._own_row(mu)
    if not copied:
        step.patch = (start, edit._patch_copy(row[start : start + len(values)]))
    row[start : start + len(values)] = values
    edit._touch(mu)


class TestUndo:
    def test_every_op_undoes_to_the_state_before(self, edit: EditSession) -> None:
        ops: list[tuple[str, dict[str, Any]]] = [
            ("add-spikes", {"mu": 0, "x_start": 0, "x_end": N_SAMPLES, "y_min": 0.6}),
            ("add-artifact", {"mu": 1, "x_start": 0, "x_end": 5000, "y_min": 0.6}),
            ("delete-spikes", {"mu": 1, "x_start": 0, "x_end": 5000, "y_max": 2.0}),
            ("remove-outliers", {"mu": 0}),
            ("flag", {"mu": 2, "flag": True}),
            ("duplicate", {"mu": 1}),
            ("reset", {"mu": 0}),
        ]
        states = []
        for op, args in ops:
            states.append(_state(edit))
            edit.apply(op, args)
        for expected in reversed(states):
            edit.apply("undo", {})
            assert _state(edit) == expected
        assert not edit.can_undo
        with pytest.raises(EditError, match="Nothing to undo"):
            edit.apply("undo", {})

    def test_refits_undo_in_place_and_after_a_copy(self, edit: EditSession) -> None:
        original = edit.values(0, 0, N_SAMPLES).copy()
        _write_patch(edit, 0, 100, np.full(50, 7.0, np.float32))  # copies the file's row
        once = edit.values(0, 0, N_SAMPLES).copy()
        _write_patch(edit, 0, 120, np.full(50, 9.0, np.float32))  # in place, with a patch
        edit.apply("undo", {})
        np.testing.assert_array_equal(edit.values(0, 0, N_SAMPLES), once)
        edit.apply("undo", {})
        np.testing.assert_array_equal(edit.values(0, 0, N_SAMPLES), original)

    def test_a_duplicate_keeps_its_train_when_the_source_is_refit(self, edit: EditSession) -> None:
        _write_patch(edit, 0, 100, np.full(50, 7.0, np.float32))
        edit.apply("duplicate", {"mu": 0})
        copy = edit.values(3, 0, N_SAMPLES).copy()
        _write_patch(edit, 0, 100, np.full(50, 3.0, np.float32))
        np.testing.assert_array_equal(edit.values(3, 0, N_SAMPLES), copy)
        assert edit.values(0, 100, 150).tolist() == [3.0] * 50
        # Undoing the source's refit must not write into the duplicate either.
        edit.apply("undo", {})
        np.testing.assert_array_equal(edit.values(0, 0, N_SAMPLES), copy)
        np.testing.assert_array_equal(edit.values(3, 0, N_SAMPLES), copy)

    def test_undoing_a_first_refit_deletes_its_copy(self, edit: EditSession) -> None:
        _write_patch(edit, 0, 100, np.full(50, 7.0, np.float32))
        row = edit.rows[0]
        assert row is not None
        copy = edit.arrays[row[0]]
        edit.apply("undo", {})
        assert set(edit.arrays) == {"base"}
        assert isinstance(copy, np.memmap)
        assert copy.filename is not None
        assert deleted(Path(copy.filename))

    def test_a_copy_a_duplicate_still_shows_is_kept(self, edit: EditSession) -> None:
        _write_patch(edit, 0, 100, np.full(50, 7.0, np.float32))
        edit.apply("duplicate", {"mu": 0})
        row = edit.rows[3]
        assert row is not None
        key = row[0]
        edit.apply("reset", {"mu": 0})
        edit.apply("undo", {})
        assert key in edit.arrays
        assert edit.values(3, 100, 150).tolist() == [7.0] * 50

    def test_a_failed_edit_changes_nothing(
        self, edit: EditSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        edit.apply("flag", {"mu": 1})
        before, depth = _state(edit), len(edit.undo_stack)

        def fail(*args: Any, **kwargs: Any) -> None:  # after the spikes were written
            raise RuntimeError("boom")

        monkeypatch.setattr(edit, "_entry", fail)
        with pytest.raises(RuntimeError):
            edit.apply("add-spikes", {"mu": 0, "x_start": 0, "x_end": N_SAMPLES - 1})
        monkeypatch.undo()
        assert _state(edit) == before
        assert len(edit.undo_stack) == depth
        assert edit.unsaved == 1

    def test_a_failed_duplicate_keeps_every_mu(
        self, edit: EditSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = _state(edit)

        def fail(grid: int) -> str:
            raise RuntimeError("boom")

        monkeypatch.setattr(edit, "_new_uid", fail)
        with pytest.raises(RuntimeError):
            edit.apply("duplicate", {"mu": 0})
        assert _state(edit) == before
        assert not edit.can_undo

    def test_a_failed_undo_keeps_its_step(
        self, edit: EditSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_patch(edit, 0, 100, np.full(50, 7.0, np.float32))
        _write_patch(edit, 0, 100, np.full(50, 3.0, np.float32))

        def fail(mu: int) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(edit, "_own_row", fail)
        with pytest.raises(OSError):
            edit.apply("undo", {})
        monkeypatch.undo()
        assert len(edit.undo_stack) == 2
        edit.apply("undo", {})
        assert edit.values(0, 100, 150).tolist() == [7.0] * 50

    def test_a_train_only_a_forgotten_step_used_is_deleted(
        self, edit: EditSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(session_module, "MAX_UNDO", 1)
        _write_patch(edit, 0, 100, np.full(50, 7.0, np.float32))
        row = edit.rows[0]
        assert row is not None
        edit.apply("reset", {"mu": 0})
        assert row[0] in edit.arrays
        edit.apply("flag", {"mu": 1})
        assert set(edit.arrays) == {"base"}

    def test_a_closed_session_takes_no_edit(self) -> None:
        edit = _session([[100]], noisy_pulse_trains(1))
        edit.close()
        with pytest.raises(EditError) as err:
            edit.apply("flag", {"mu": 0})
        assert err.value.field == "token"
        assert edit.signal is None

    def test_the_pulse_train_is_read_only(self, edit: EditSession) -> None:
        assert not edit.values(0, 0, 10).flags.writeable

    def test_reset_restores_the_file_train_and_spikes(
        self, edit: EditSession, pulse_trains: np.ndarray
    ) -> None:
        spikes = edit.spike_times[0].copy()
        _write_patch(edit, 0, 100, np.full(50, 7.0, np.float32))
        edit.apply("delete-spikes", {"mu": 0, "x_start": 0, "x_end": N_SAMPLES, "y_max": 5.0})
        was_dirty = edit.dirty
        assert was_dirty
        edit.apply("reset", {"mu": 0})
        np.testing.assert_array_equal(edit.values(0, 0, N_SAMPLES), pulse_trains[0])
        np.testing.assert_array_equal(edit.spike_times[0], spikes)
        assert edit.dirty  # the reset is an unsaved edit too
        assert edit.history[-1]["type"] == "reset_mu"

    @pytest.mark.parametrize(
        ("op", "args"),
        [
            ("flag", {"mu": 0, "flag": True}),
            ("add-artifact", {"mu": 0, "x_start": 0, "x_end": N_SAMPLES, "y_min": 5.0}),
            ("duplicate", {"mu": 0}),
        ],
    )
    def test_an_edit_that_keeps_the_spike_times_is_unsaved_too(
        self, edit: EditSession, op: str, args: dict[str, Any]
    ) -> None:
        states = [edit.dirty]
        edit.apply(op, args)
        states.append(edit.dirty)
        edit.apply("undo", {})
        states.append(edit.dirty)
        assert states == [False, True, False]

    def test_duplicate_uids_skip_every_uid_the_log_named(self, edit: EditSession) -> None:
        edit.history.append({"type": "remove_duplicates", "removed_mu_uids": ["g0_mu7"]})
        edit.apply("duplicate", {"mu": 0})
        assert edit.mu_uids[-1] == "g0_mu8"
        assert edit.history[-1] == {**edit.history[-1], "source_mu_uid": "g0_mu0"}

    def test_remove_duplicates_keeps_order_and_clears_undo(self) -> None:
        a = spike_train(10.0, seed=3).tolist()
        b = spike_train(8.0, seed=4).tolist()
        noisy_a = sorted([*a, (a[5] + a[6]) // 2])
        edit = _session(
            [noisy_a, b, a],
            noisy_pulse_trains(3),
            duplicates=lambda spikes, grids: [1, 2],
        )
        try:
            edit.apply("flag", {"mu": 1})
            change = edit.apply("remove-duplicates", {})
            assert change.kept == [1, 2]
            assert edit.mu_uids == ["g0_mu1", "g0_mu2"]
            assert edit.flagged == [True, False]
            assert not edit.can_undo
            assert edit.history[-1]["removed_mu_uids"] == ["g0_mu0"]
        finally:
            edit.close()

    def test_unknown_op_and_bad_args_are_edit_errors(self, edit: EditSession) -> None:
        with pytest.raises(EditError, match="Unknown"):
            edit.apply("explode", {})
        with pytest.raises(EditError, match="Invalid arguments"):
            edit.apply("flag", {"mu": 0, "colour": "red"})
        with pytest.raises(EditError, match="out of range"):
            edit.apply("flag", {"mu": 3})


# ── replay and the edit log ──────────────────────────────────────────────────


class TestReplay:
    def test_replaying_the_log_rebuilds_the_session(
        self, cache_dir: Path, tmp_path: Path, pulse_trains: np.ndarray
    ) -> None:
        source = tmp_path / "rec_decomp.npz"
        source.write_bytes(b"decomposition")
        spikes = [spike_train(10.0, seed=s).tolist() for s in (1, 2, 3)]
        first = _session(spikes, pulse_trains.copy())
        first.log = EditLog.create(source, "tab-one")
        first.apply("add-spikes", {"mu": 0, "x_start": 0, "x_end": N_SAMPLES, "y_min": 0.6})
        first.apply("duplicate", {"mu": 2})
        first.apply("flag", {"mu": 1})
        first.apply("delete-spikes", {"mu": 3, "x_start": 0, "x_end": 4000, "y_max": 2.0})
        first.apply("undo", {})
        expected = _state(first)
        history = [{k: v for k, v in e.items() if k != "timestamp"} for e in first.history]
        first.close()  # a crash: the log stays

        found = find_recoverable(source, set())
        assert found is not None and found.edits == 3
        second = _session(spikes, pulse_trains.copy())
        try:
            assert second.replay(found.records) == 5
            assert _state(second) == expected
            assert [{k: v for k, v in e.items() if k != "timestamp"} for e in second.history] == (
                history
            )
        finally:
            second.close()

    def test_replay_stops_at_an_unexpected_failure(
        self, edit: EditSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(**_: Any) -> Any:
            raise RuntimeError("numpy broke")

        monkeypatch.setattr(edit, "remove_outliers", broken)
        records = [
            {"op": "flag", "args": {"mu": 0}},
            {"op": "flag", "args": {"mu": 9}},  # refused: skipped
            {"op": "remove-outliers", "args": {"mu": 0}},  # fails: stops here
            {"op": "duplicate", "args": {"mu": 0}},
        ]
        assert edit.replay(records) == 1
        assert edit.n_mu == 3

    def test_closing_deletes_a_log_whose_edits_were_undone(
        self, cache_dir: Path, tmp_path: Path
    ) -> None:
        source = tmp_path / "rec_decomp.npz"
        source.write_bytes(b"decomposition")
        edit = _session([[100]])
        edit.log = EditLog.create(source, "tab")
        edit.apply("flag", {"mu": 0})
        edit.apply("undo", {})
        edit.close()
        assert not list((cache_dir / "edit-logs").iterdir())

    def test_a_log_that_fails_keeps_what_it_holds(self, cache_dir: Path, tmp_path: Path) -> None:
        source = tmp_path / "rec_decomp.npz"
        source.write_bytes(b"decomposition")
        edit = _session([[100], [200]])
        log = EditLog.create(source, "tab")
        assert log is not None
        edit.log = log
        edit.apply("flag", {"mu": 0})
        log._fh.close()
        edit.apply("flag", {"mu": 1})
        assert edit.unsaved == 2
        edit.close()
        found = find_recoverable(source, set())
        assert found is not None and found.edits == 1


# ── uids and saving ──────────────────────────────────────────────────────────


def test_mu_uids_are_numbered_per_grid_and_never_reused() -> None:
    assert initial_mu_uids([0, 0, 1, 0]) == ["g0_mu0", "g0_mu1", "g1_mu0", "g0_mu2"]
    assert next_mu_uid(0, ["g0_mu4", "g1_mu9", "g0_extra"]) == "g0_mu5"
    assert next_mu_uid(2, []) == "g2_mu0"


def test_detach_copies_what_is_mapped_from_the_file(tmp_path: Path) -> None:
    """Windows cannot replace a mapped file: before a save over it, both maps move to the store."""
    n_samples = 3001
    path = tmp_path / "d.npz"
    save_decomposition_npz(
        path,
        pulse_trains=np.random.default_rng(1).random((2, n_samples)),
        spike_times=[[10, 20], [30]],
        fsamp=2048.0,
        grid_names=["GR08MM1305"],
        mu_grid_index=[0, 0],
        muscle_names=[],
        parameters={},
        total_samples=n_samples,
        emg_data=np.random.default_rng(0).standard_normal((64, n_samples)),
    )
    store = SessionStore.create("edit")
    decomp, ctx = load_decomposition(str(path), store, binary_trains=False)
    edit = EditSession(
        store=store,
        fsamp=2048.0,
        total_samples=n_samples,
        spike_times=decomp.spike_times,
        pulse_trains=decomp.pulse_trains,
        mu_grid_index=decomp.mu_grid_index,
        mu_uids=["g0_mu0", "g0_mu1"],
        signal=ctx,
    )
    assert edit.signal is not None
    mapped = (edit.arrays["base"], edit.signal.data)
    assert all(Path(getattr(arr, "filename", "")) == path for arr in mapped)

    edit.detach(str(tmp_path / "other.npz"))  # another file: nothing moves
    assert edit.arrays["base"] is mapped[0] and edit.signal.data is mapped[1]

    edit.detach(str(path))
    for arr, before in zip((edit.arrays["base"], edit.signal.data), mapped, strict=True):
        assert Path(getattr(arr, "filename", "")).parent.resolve() == store.path.resolve()
        np.testing.assert_array_equal(arr, before)
