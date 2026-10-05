"""The server-side edit session: parity with the edit functions, undo, recovery, and its API."""

from __future__ import annotations

import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from httpx import Response
from starlette.testclient import TestClient

from muedit.api import cache
from muedit.api.binary import FRAME_MEDIA_TYPE, unpack_frame
from muedit.decomp.decomposition_file import save_decomposition_npz, unpack_csr
from muedit.decomp.preprocess import preprocess_step
from muedit.decomp.types import DecompositionParameters, LoadStepOutput
from muedit.editing.edit_log import EditLog, find_recoverable
from muedit.editing.operations import (
    add_artifact_in_roi,
    add_spikes_in_roi,
    delete_spikes_in_roi,
    remove_discharge_rate_outliers,
    update_motor_unit_filter_window,
)
from muedit.editing.session import EditError, EditSession
from muedit.io.store import SessionStore
from muedit.models import EditSignalContext, SignalImport
from tests.test_editing_operations import (
    EDGE,
    FSAMP,
    N_CHANNELS,
    N_SAMPLES,
    VIEW_END,
    VIEW_START,
    _in_view,
    _match_count,
    _pulse_with_peaks,
    _spike_train,
    make_synthetic_mu,
)

API = "/api/v1"
SESSION_HEADER = "X-MUedit-Session"


def _noisy_pulse(n_mu: int, seed: int = 0) -> np.ndarray:
    """Pulse trains with random peaks, float32 like a saved file."""
    rng = np.random.default_rng(seed)
    pulse = rng.random((n_mu, N_SAMPLES)).astype(np.float32) * 0.2
    for row in pulse:
        row[rng.choice(N_SAMPLES, 60, replace=False)] += rng.random(60).astype(np.float32) + 0.5
    return pulse


def _session(
    spikes: list[Any],
    pulse: np.ndarray | None = None,
    grids: list[int] | None = None,
    **kw: Any,
) -> EditSession:
    return EditSession(
        store=SessionStore.create("test-edit"),
        fsamp=FSAMP,
        total_samples=N_SAMPLES,
        spikes=spikes,
        pulse=pulse,
        mu_grid_index=grids or [0] * len(spikes),
        mu_uids=[f"g0_mu{i}" for i in range(len(spikes))],
        **kw,
    )


@pytest.fixture(scope="module")
def synthetic_mu() -> dict[str, np.ndarray]:
    return make_synthetic_mu()


@pytest.fixture()
def pulse() -> np.ndarray:
    return _noisy_pulse(3)


@pytest.fixture()
def edit(pulse: np.ndarray) -> Iterator[EditSession]:
    session = _session([_spike_train(10.0, seed=s).tolist() for s in (1, 2, 3)], pulse.copy())
    yield session
    session.close()


# ── parity with the edit functions on the whole train ────────────────────────


class TestParity:
    @pytest.mark.parametrize("roi", [(900, 1100), (0, 400), (N_SAMPLES - 300, N_SAMPLES + 50)])
    def test_add_spikes(self, edit: EditSession, pulse: np.ndarray, roi: tuple[int, int]) -> None:
        before = edit.spikes[1].tolist()
        expected = add_spikes_in_roi(pulse[1].astype(float), before, FSAMP, *roi, 0.6)
        edit.apply("add-spikes", {"mu": 1, "x_start": roi[0], "x_end": roi[1], "y_min": 0.6})
        assert edit.spikes[1].tolist() == expected
        assert edit.spikes[1].dtype == np.int32

    def test_add_artifact(self, edit: EditSession, pulse: np.ndarray) -> None:
        expected = add_artifact_in_roi(pulse[0].astype(float), [], FSAMP, 2000, 4000, 0.6)
        edit.apply("add-artifact", {"mu": 0, "x_start": 2000, "x_end": 4000, "y_min": 0.6})
        assert edit.artifacts[0].tolist() == expected

    def test_delete_spikes(self, edit: EditSession, pulse: np.ndarray) -> None:
        before = edit.spikes[2].tolist()
        expected = delete_spikes_in_roi(pulse[2].astype(float), before, 1000, 6000, 0.0, 0.15)
        edit.apply(
            "delete-spikes", {"mu": 2, "x_start": 1000, "x_end": 6000, "y_min": 0.0, "y_max": 0.15}
        )
        assert edit.spikes[2].tolist() == expected
        assert edit.history[-1]["type"] == "delete_spikes"

    def test_remove_outliers(self, edit: EditSession, pulse: np.ndarray) -> None:
        edit.spikes[0] = np.asarray(sorted({*edit.spikes[0].tolist(), 4020}), dtype=np.int32)
        before = edit.spikes[0].tolist()
        expected = remove_discharge_rate_outliers(pulse[0].astype(float), before, FSAMP)
        change = edit.apply("remove-outliers", {"mu": 0})
        assert edit.spikes[0].tolist() == expected
        assert change.info["removed_count"] == len(before) - len(expected)

    def test_drawn_trains_without_pulse(self) -> None:
        edit = _session([[100, 200, 300]])
        try:
            assert not edit.has_pulse(0)
            np.testing.assert_array_equal(np.flatnonzero(edit.values(0, 0, 1000)), [100, 200, 300])
            edit.apply("delete-spikes", {"mu": 0, "x_start": 150, "x_end": 250, "y_max": 1.0})
            assert edit.spikes[0].tolist() == [100, 300]
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
        emgmask=[np.zeros(64, dtype=int)],
        artifact_mask=mask,
    )


def _decomposition_filtered(ctx: EditSignalContext) -> np.ndarray:
    """``ctx``'s EMG as ``preprocess_step`` filters it before a decomposition (default float32)."""
    signal = SignalImport(
        data=ctx.data,
        fsamp=ctx.fsamp,
        gridname=list(ctx.grid_names),
        auxiliary=np.zeros((0, ctx.data.shape[1])),
    )
    prep = preprocess_step(
        loaded=LoadStepOutput("x", "x", signal, signal.data, signal.fsamp),
        duration=None,
        manual_roi=False,
        roi=None,
        rois=None,
        params=DecompositionParameters(),
        discard_overrides=None,
        bids_root=None,
        bids_entities=None,
        bids_metadata=None,
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
            pt, expected = update_motor_unit_filter_window(
                _decomposition_filtered(signal),
                np.zeros(64, int),
                target,
                FSAMP,
                VIEW_START,
                VIEW_END,
                bandpass=False,
            )
            assert pt is not None
            edit.apply("update-filter", {"mu": 0, "view_start": VIEW_START, "view_end": VIEW_END})
            assert edit.spikes[0].tolist() == expected
            row = edit.values(0, 0, N_SAMPLES)
            np.testing.assert_array_equal(
                row[VIEW_START + EDGE : VIEW_END - EDGE],
                pt[EDGE : len(pt) - EDGE].astype(np.float32),
            )
            assert not row[: VIEW_START + EDGE].any()
            truth = _in_view(synthetic_mu["target"])
            assert _match_count(truth, _in_view(edit.spikes[0])) >= 0.5 * truth.size
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
            spikes = edit.spikes[0]
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
        [s.tolist() for s in edit.spikes],
        [a.tolist() for a in edit.artifacts],
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
        assert not Path(copy.filename).exists()

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

    def test_reset_restores_the_file_train_and_spikes(
        self, edit: EditSession, pulse: np.ndarray
    ) -> None:
        spikes = edit.spikes[0].copy()
        _write_patch(edit, 0, 100, np.full(50, 7.0, np.float32))
        edit.apply("delete-spikes", {"mu": 0, "x_start": 0, "x_end": N_SAMPLES, "y_max": 5.0})
        was_dirty = edit.dirty
        assert was_dirty
        edit.apply("reset", {"mu": 0})
        np.testing.assert_array_equal(edit.values(0, 0, N_SAMPLES), pulse[0])
        np.testing.assert_array_equal(edit.spikes[0], spikes)
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
    def test_an_edit_that_keeps_the_discharge_times_is_unsaved_too(
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
        a = _spike_train(10.0, seed=3).tolist()
        b = _spike_train(8.0, seed=4).tolist()
        noisy_a = sorted([*a, (a[5] + a[6]) // 2])
        edit = _session(
            [noisy_a, b, a],
            _noisy_pulse(3),
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


# ── the edit log ─────────────────────────────────────────────────────────────


@pytest.fixture()
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MUEDIT_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path / "cache"


class TestEditLog:
    def test_replaying_the_log_rebuilds_the_session(
        self, cache_dir: Path, tmp_path: Path, pulse: np.ndarray
    ) -> None:
        source = tmp_path / "rec_decomp.npz"
        source.write_bytes(b"decomposition")
        spikes = [_spike_train(10.0, seed=s).tolist() for s in (1, 2, 3)]
        first = _session(spikes, pulse.copy())
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
        second = _session(spikes, pulse.copy())
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

    def test_logs_without_net_edits_or_of_another_file_version_are_dropped(
        self, cache_dir: Path, tmp_path: Path
    ) -> None:
        source = tmp_path / "rec_decomp.npz"
        source.write_bytes(b"v1")
        undone = _session([[100]])
        undone.log = EditLog.create(source, "a")
        undone.apply("flag", {"mu": 0})
        undone.apply("undo", {})
        undone.close()
        assert not list((cache_dir / "edit-logs").iterdir())

        stale = _session([[100]])
        stale.log = EditLog.create(source, "b")
        stale.apply("flag", {"mu": 0})
        stale.close()
        source.write_bytes(b"v2, saved elsewhere")
        assert find_recoverable(source, set()) is None
        assert not list((cache_dir / "edit-logs").iterdir())

    def test_a_live_sessions_log_is_not_recoverable(self, cache_dir: Path, tmp_path: Path) -> None:
        source = tmp_path / "rec_decomp.npz"
        source.write_bytes(b"decomposition")
        live = _session([[100]])
        live.log = EditLog.create(source, "live")
        try:
            live.apply("flag", {"mu": 0})
            assert live.log is not None
            assert find_recoverable(source, {live.log.path}) is None
            assert find_recoverable(source, set()) is not None
        finally:
            live.close()


# ── HTTP ─────────────────────────────────────────────────────────────────────


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cache_dir: Path) -> Iterator[TestClient]:
    import muedit.api.config as config
    from muedit.api.app_factory import create_app
    from muedit.api.routes import include_routers

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    app = create_app()
    include_routers(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def decomp_file(tmp_path: Path) -> Path:
    path = tmp_path / "sub-01_task-edit_decomp.npz"
    save_decomposition_npz(
        path,
        pulse_trains=_noisy_pulse(3),
        distimes=[_spike_train(10.0, seed=s) for s in (1, 2, 3)],
        fsamp=FSAMP,
        grid_names=["GR08MM1305"],
        mu_grid_index=[0, 0, 0],
        muscles=["ta"],
        parameters={"duplicatesthresh": 0.3},
        total_samples=N_SAMPLES,
    )
    return path


def _frame(resp: Response) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == FRAME_MEDIA_TYPE
    return unpack_frame(resp.content)


def _open(api: TestClient, path: Path, tab: str = "tab-edit") -> tuple[dict[str, Any], dict]:
    return _frame(
        api.post(
            f"{API}/edit/session/open", json={"path": str(path)}, headers={SESSION_HEADER: tab}
        )
    )


def _op(api: TestClient, token: str, op: str, tab: str = "tab-edit", **args: Any) -> Any:
    return _frame(
        api.post(
            f"{API}/edit/ops/{op}", json={"token": token, **args}, headers={SESSION_HEADER: tab}
        )
    )


class TestSessionApi:
    def test_open_sends_fields_and_csr_spikes_not_pulse_trains(
        self, api: TestClient, decomp_file: Path
    ) -> None:
        meta, arrays = _open(api, decomp_file)
        assert meta["n_mu"] == 3
        assert meta["file_label"] == decomp_file.name
        assert meta["has_pulse"] == [True, True, True]
        assert meta["dirty"] is False and meta["can_undo"] is False
        assert meta["recoverable_edits"] == 0
        assert set(arrays) == {"spikes", "spike_offsets", "artifacts", "artifact_offsets"}
        assert arrays["spikes"].dtype == np.int32
        rows = unpack_csr(arrays["spikes"], arrays["spike_offsets"])
        assert rows[1].tolist() == _spike_train(10.0, seed=2).tolist()
        edit = cache._get_edit_session(meta["token"])
        assert edit is not None
        assert isinstance(edit.arrays["base"], np.memmap)  # mapped from the file, not copied

    def test_ops_return_what_changed(self, api: TestClient, decomp_file: Path) -> None:
        meta, _ = _open(api, decomp_file)
        token = meta["token"]
        change, arrays = _op(
            api, token, "delete-spikes", mu=1, x_start=0, x_end=N_SAMPLES, y_min=0.0, y_max=5.0
        )
        assert change["changed"] == [1]
        assert change["dirty"] is True and change["can_undo"] is True
        assert change["history_start"] == 0
        assert change["history"][0]["type"] == "delete_spikes"
        assert arrays["spike_offsets"].tolist() == [0, 0]
        assert change["versions"][1] != meta["versions"][1]
        undone, arrays = _op(api, token, "undo")
        assert undone["history_start"] == 0 and undone["history"] == []
        assert unpack_csr(arrays["spikes"], arrays["spike_offsets"])[0].size > 0

    def test_duplicate_and_remove_duplicates(self, api: TestClient, decomp_file: Path) -> None:
        token = _open(api, decomp_file)[0]["token"]
        dup, _ = _op(api, token, "duplicate", mu=0)
        assert dup["n_mu"] == 4 and dup["changed"] == [3]
        removed, _ = _op(api, token, "remove-duplicates")
        assert removed["kept_indices"] == [0, 1, 2]
        assert removed["removed_count"] == 1
        assert removed["can_undo"] is False

    def test_bad_requests(self, api: TestClient, decomp_file: Path) -> None:
        token = _open(api, decomp_file)[0]["token"]
        resp = api.post(f"{API}/edit/ops/flag", json={"token": token, "mu": 9})
        assert resp.status_code == 400
        resp = api.post(f"{API}/edit/ops/explode", json={"token": token})
        assert resp.status_code == 422
        resp = api.post(f"{API}/edit/ops/flag", json={"token": "gone", "mu": 0})
        assert resp.status_code == 400
        resp = api.post(
            f"{API}/edit/ops/undo", json={"token": token}, headers={SESSION_HEADER: "tab-edit"}
        )
        assert resp.status_code == 400  # nothing to undo

    def test_prepare_grid(self, api: TestClient, decomp_file: Path) -> None:
        token = _open(api, decomp_file)[0]["token"]
        url = f"{API}/edit/session/prepare-grid"
        headers = {SESSION_HEADER: "tab-edit"}
        resp = api.post(url, json={"token": token, "grid": 9}, headers=headers)
        assert resp.status_code == 200 and resp.json()["data"] == {"grid": 9}
        resp = api.post(url, json={"token": "gone", "grid": 0}, headers=headers)
        assert resp.status_code == 400

    def test_pulse_viewport_is_exact(self, api: TestClient, decomp_file: Path) -> None:
        token = _open(api, decomp_file)[0]["token"]
        with np.load(decomp_file) as z:
            row = z["pulse_trains"][2]
        meta, arrays = _frame(
            api.get(f"{API}/series/pulse", params={"token": token, "mu": 2, "bins": 100})
        )
        assert meta["kind"] == "envelope" and meta["end"] == N_SAMPLES
        np.testing.assert_array_equal(arrays["max"][0], row.reshape(100, -1).max(axis=1))
        np.testing.assert_array_equal(arrays["min"][0], row.reshape(100, -1).min(axis=1))
        spikes = _spike_train(10.0, seed=3)
        assert arrays["spikes"].tolist() == spikes.tolist()
        np.testing.assert_array_equal(arrays["spike_values"], row[spikes])
        meta, arrays = _frame(
            api.get(
                f"{API}/series/pulse",
                params={"token": token, "mu": 2, "start": 500, "end": 900, "bins": 1000},
            )
        )
        assert meta["kind"] == "samples"
        np.testing.assert_array_equal(arrays["samples"][0], row[500:900])

    def test_a_reloaded_page_takes_the_session_over(
        self, api: TestClient, decomp_file: Path
    ) -> None:
        token = _open(api, decomp_file, tab="tab-before")[0]["token"]
        meta, _ = _frame(
            api.get(
                f"{API}/edit/session",
                params={"token": token},
                headers={SESSION_HEADER: "tab-after"},
            )
        )
        assert meta["token"] == token and meta["n_mu"] == 3
        slot = cache._EDIT_SESSIONS.slots[token]
        assert slot.session == "tab-after"

    def test_save_writes_the_edits_and_makes_them_the_baseline(
        self, api: TestClient, decomp_file: Path, tmp_path: Path
    ) -> None:
        meta, _ = _open(api, decomp_file)
        token = meta["token"]
        _op(api, token, "delete-spikes", mu=0, x_start=0, x_end=3000, y_max=5.0)
        _op(api, token, "flag", mu=1)
        _op(api, token, "duplicate", mu=2)
        edit = cache._get_edit_session(token)
        assert edit is not None
        kept_spikes = [edit.spikes[0].tolist(), edit.spikes[2].tolist()]
        resp = api.post(
            f"{API}/edit/session/save",
            json={"token": token, "project": "proj", "entity_label": "sub-01_task-edit"},
            headers={SESSION_HEADER: "tab-edit"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["kept_indices"] == [0, 2]
        assert [e["type"] for e in data["edit_history"][-2:]] == [
            "remove_flagged",
            "remove_duplicates",
        ]
        assert data["dirty"] is False and data["can_undo"] is False
        out = Path(data["path"])
        assert out.is_relative_to(tmp_path / "proj")
        with np.load(out) as z:
            np.testing.assert_array_equal(z["pulse_trains"], _noisy_pulse(3)[[0, 2]])
            rows = unpack_csr(z["spike_times"], z["spike_offsets"])
            assert [r.tolist() for r in rows] == kept_spikes
        assert edit.n_mu == 2 and not edit.dirty

        # Saving again overwrites the file the session may map.
        reopened = _open(api, out)[0]["token"]
        _op(api, reopened, "delete-spikes", mu=1, x_start=0, x_end=N_SAMPLES, y_max=5.0)
        again = api.post(
            f"{API}/edit/session/save",
            json={
                "token": reopened,
                "project": "proj",
                "entity_label": "sub-01_task-edit",
                "remove_duplicates": False,
            },
            headers={SESSION_HEADER: "tab-edit"},
        ).json()["data"]
        assert again["path"] == str(out)
        with np.load(out) as z:
            assert z["spike_offsets"].tolist()[-1] == z["spike_offsets"].tolist()[-2]
            saved_row = z["pulse_trains"][0].copy()
        # The file was replaced, not rewritten in place: the session still reads the
        # trains it had mapped, which are the ones it saved.
        _, arrays = _frame(
            api.get(
                f"{API}/series/pulse",
                params={"token": reopened, "mu": 0, "start": 0, "end": 1000, "bins": 1000},
            )
        )
        np.testing.assert_array_equal(arrays["samples"][0], saved_row[:1000])

    def test_a_dataset_outside_the_data_root_is_saved_back_into(
        self,
        api: TestClient,
        decomp_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import muedit.api.config as config

        monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "outputs")
        dataset = tmp_path / "elsewhere" / "study"
        path = dataset / "derivatives" / "muedit" / "sub-01" / "decomp" / decomp_file.name
        path.parent.mkdir(parents=True)
        shutil.copy(decomp_file, path)
        meta, _ = _open(api, path)
        assert meta["project"] == ""

        def save(project: str) -> Response:
            return api.post(
                f"{API}/edit/session/save",
                json={
                    "token": meta["token"],
                    "project": project,
                    "entity_label": "sub-01_task-edit",
                },
                headers={SESSION_HEADER: "tab-edit"},
            )

        assert Path(save("").json()["data"]["path"]).is_relative_to(dataset)
        assert Path(save("proj").json()["data"]["path"]).is_relative_to(
            tmp_path / "outputs" / "proj"
        )
        refused = save("../escape")
        assert refused.status_code == 400
        assert refused.json()["error"]["detail"]["field"] == "project"

    def test_unsaved_edits_are_offered_when_the_file_is_reopened(
        self, api: TestClient, decomp_file: Path
    ) -> None:
        token = _open(api, decomp_file)[0]["token"]
        _op(api, token, "delete-spikes", mu=0, x_start=0, x_end=N_SAMPLES, y_max=5.0)
        api.post(f"{API}/session/close", params={"session": "tab-edit"})

        meta, _ = _open(api, decomp_file)
        assert meta["recoverable_edits"] == 1
        meta, arrays = _frame(
            api.post(
                f"{API}/edit/session/recover",
                json={"token": meta["token"]},
                headers={SESSION_HEADER: "tab-edit"},
            )
        )
        assert meta["recovered_edits"] == 1 and meta["dirty"] is True
        assert arrays["spike_offsets"].tolist()[:2] == [0, 0]

        api.post(f"{API}/session/close", params={"session": "tab-edit"})
        meta, _ = _open(api, decomp_file)
        assert meta["recoverable_edits"] == 1  # the recovered edits are unsaved again
        declined, _ = _frame(
            api.post(
                f"{API}/edit/session/recover",
                json={"token": meta["token"], "apply": False},
                headers={SESSION_HEADER: "tab-edit"},
            )
        )
        assert declined["recovered_edits"] == 0 and declined["dirty"] is False
        api.post(f"{API}/session/close", params={"session": "tab-edit"})
        assert _open(api, decomp_file)[0]["recoverable_edits"] == 0

    def test_a_failed_recovery_still_drops_the_log(
        self, api: TestClient, decomp_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = _open(api, decomp_file)[0]["token"]
        _op(api, token, "flag", mu=0)
        api.post(f"{API}/session/close", params={"session": "tab-edit"})
        meta, _ = _open(api, decomp_file)
        assert meta["recoverable_edits"] == 1

        def broken(self: EditSession, records: Any) -> int:
            raise RuntimeError("replay broke")

        monkeypatch.setattr(EditSession, "replay", broken)
        resp = api.post(
            f"{API}/edit/session/recover",
            json={"token": meta["token"]},
            headers={SESSION_HEADER: "tab-edit"},
        )
        assert resp.status_code == 500
        monkeypatch.undo()
        api.post(f"{API}/session/close", params={"session": "tab-edit"})
        assert _open(api, decomp_file)[0]["recoverable_edits"] == 0

    def test_open_errors(self, api: TestClient, tmp_path: Path) -> None:
        assert api.post(f"{API}/edit/session/open", json={"path": ""}).status_code == 400
        missing = api.post(f"{API}/edit/session/open", json={"path": str(tmp_path / "no.npz")})
        assert missing.status_code == 404
        other = tmp_path / "recording.otb4"
        other.write_bytes(b"")
        assert api.post(f"{API}/edit/session/open", json={"path": str(other)}).status_code == 400

    def test_a_file_that_fails_to_open_leaves_the_open_one(
        self, api: TestClient, decomp_file: Path, tmp_path: Path
    ) -> None:
        token = _open(api, decomp_file)[0]["token"]
        broken = tmp_path / "broken_decomp.npz"
        broken.write_bytes(b"not a decomposition")
        resp = api.post(
            f"{API}/edit/session/open",
            json={"path": str(broken)},
            headers={SESSION_HEADER: "tab-edit"},
        )
        assert resp.status_code >= 400
        meta, _ = _op(api, token, "flag", mu=0, flag=True)
        assert meta["flagged"][0] is True
        replaced = _open(api, decomp_file)[0]["token"]
        assert replaced != token
        gone = api.post(
            f"{API}/edit/ops/undo", json={"token": token}, headers={SESSION_HEADER: "tab-edit"}
        )
        assert gone.status_code == 400

    def test_a_spikes_only_file_draws_its_trains(self, api: TestClient, tmp_path: Path) -> None:
        path = tmp_path / "spikes_only_decomp.npz"
        save_decomposition_npz(
            path,
            pulse_trains=None,
            distimes=[[100, 200]],
            fsamp=FSAMP,
            grid_names=["GR08MM1305"],
            mu_grid_index=[0],
            muscles=[],
            parameters={},
            total_samples=N_SAMPLES,
        )
        meta, _ = _open(api, path)
        assert meta["has_pulse"] == [False]
        _, arrays = _frame(
            api.get(
                f"{API}/series/pulse",
                params={"token": meta["token"], "mu": 0, "start": 0, "end": 300, "bins": 1000},
            )
        )
        assert np.flatnonzero(arrays["samples"][0]).tolist() == [100, 200]
        assert arrays["spike_values"].tolist() == [1.0, 1.0]


def test_delete_in_roi_matches_on_a_drawn_pulse() -> None:
    """The ROI helpers the session calls still work on a plain array."""
    pulse = _pulse_with_peaks({100: 0.5, 200: 0.5, 300: 0.5}, n=1000)
    assert delete_spikes_in_roi(pulse, [100, 200], 150, 400, 0.2, 0.8) == [100]
