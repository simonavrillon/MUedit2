"""Direct tests for the API caches (``muedit.api.cache``)."""

from __future__ import annotations

import tracemalloc
from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest

from muedit.api import cache
from muedit.api.memory import SESSION_IDLE_SEC
from muedit.api.services.series_service import build_signal_views
from muedit.editing.session import EditSession
from muedit.io.store import RamStore, SessionStore
from muedit.models import IntArray, SignalImport


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def clean_caches() -> Iterator[None]:
    cache.BUDGET.clear()
    yield
    cache.BUDGET.clear()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(cache.BUDGET, "clock", fake.time)
    return fake


def _signal(n_channels: int = 4, n_samples: int = 100, fill: float = 1.0) -> SignalImport:
    return SignalImport(
        data=np.full((n_channels, n_samples), fill, dtype=np.float64),
        fsamp=2000.0,
        gridname=["GR08MM1305"],
        muscle=["TA"],
    )


def _store(session: str = "tab-a", **kwargs: Any) -> str:
    return cache._store_upload_signal(_signal(**kwargs), session=session)


# ── upload session cache ─────────────────────────────────────────────────────


class TestUploadSignal:
    def test_store_copies_and_get_shares_read_only(self, clock: FakeClock) -> None:
        signal = _signal()
        token = cache._store_upload_signal(signal, source_path="/data/rec.otb+")
        signal.data[:] = 0
        first = cache._get_upload_signal(token)
        assert first is not None
        np.testing.assert_array_equal(first.data, np.ones((4, 100)))
        assert np.shares_memory(first.data, cache._UPLOADS.slots[token].value.signal.data)
        with pytest.raises(ValueError):
            first.data[0, 0] = 5
        with pytest.raises(ValueError):
            first.auxiliary[...] = 5
        assert cache._get_upload_source_path(token) == "/data/rec.otb+"

    def test_metadata_and_names_are_independent(self, clock: FakeClock) -> None:
        token = _store()
        first = cache._get_upload_signal(token)
        assert first is not None
        first.metadata["bids_entity_label"] = "sub-01"
        first.gridname.append("extra")
        again = cache._get_upload_signal(token)
        assert again is not None
        assert again.metadata == {}
        assert again.gridname == ["GR08MM1305"]

    @pytest.mark.parametrize("token", [None, "", "unknown"])
    def test_missing_token(self, clock: FakeClock, token: str | None) -> None:
        assert cache._get_upload_signal(token) is None
        assert cache._get_upload_source_path(token) is None

    def test_one_upload_per_session(self, clock: FakeClock) -> None:
        first = _store("tab-a")
        other = _store("tab-b")
        second = _store("tab-a")
        assert cache._get_upload_signal(first) is None
        assert cache._get_upload_signal(second) is not None
        assert cache._get_upload_signal(other) is not None

    def test_release_before_the_next_load(self, clock: FakeClock) -> None:
        token = _store("tab-a")
        other = _store("tab-b")
        cache._release_upload("tab-a")
        assert cache._get_upload_signal(token) is None
        assert cache._get_upload_signal(other) is not None

    def test_active_session_keeps_its_upload_however_long_it_idles(self, clock: FakeClock) -> None:
        token = _store()
        clock.advance(10 * SESSION_IDLE_SEC)
        cache.BUDGET.sweep()
        assert cache._get_upload_signal(token) is not None

    def test_budget_evicts_other_sessions_first(
        self, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        one_entry = _signal().nbytes
        monkeypatch.setattr(cache.BUDGET, "limit_bytes", int(one_entry * 1.5))
        first = _store("tab-a")
        second = _store("tab-b")
        assert cache._get_upload_signal(first) is None
        assert cache._get_upload_signal(second) is not None

    def test_active_session_may_exceed_the_budget(
        self, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache.BUDGET, "limit_bytes", 16)
        token = _store()
        assert cache._get_upload_signal(token) is not None
        assert cache.BUDGET.used_bytes > cache.BUDGET.limit_bytes


# ── QC series attached to an upload session ──────────────────────────────────


def _views(store: RamStore | SessionStore, n_samples: int = 2000) -> cache.SignalViews:
    rng = np.random.default_rng(0)
    signal = SignalImport(
        data=rng.normal(size=(6, n_samples)).astype(np.float32),
        fsamp=2000.0,
        gridname=["A", "B"],
        auxiliary=rng.normal(size=(1, n_samples)).astype(np.float32),
    )
    views, _ = build_signal_views(signal, store, [4, 2], [1, 1])
    return views


class TestSignalViews:
    def test_round_trip(self, clock: FakeClock) -> None:
        token = _store()
        views = _views(RamStore())
        cache._store_signal_views(token, views)
        assert cache._get_signal_views(token) is views
        assert views.grid_rows == [(0, 4), (4, 6)]
        assert views.overview.shape == (2, 2000)

    def test_arrays_are_read_only(self, clock: FakeClock) -> None:
        views = _views(SessionStore.create("views"))
        with pytest.raises(ValueError):
            views.overview[0, 0] = 5
        with pytest.raises(ValueError):
            views.emg.levels[0][0, 0, 0] = 5

    def test_without_views_returns_none(self, clock: FakeClock) -> None:
        assert cache._get_signal_views(_store()) is None

    def test_heap_views_count_against_the_budget(self, clock: FakeClock) -> None:
        token = _store()
        before = cache.BUDGET.used_bytes
        views = _views(RamStore())
        cache._store_signal_views(token, views)
        assert views.nbytes > 0
        assert cache.BUDGET.used_bytes == before + views.nbytes


# ── decompose preview binary cache ───────────────────────────────────────────


class TestDecompPreviewBinary:
    def test_first_fetch_removes_it(self, clock: FakeClock) -> None:
        token = cache._store_decomp_preview_binary(b"MUB1" + b"\0" * 12)
        assert cache._pop_decomp_preview_binary(token) == b"MUB1" + b"\0" * 12
        assert token not in cache._DECOMP_PREVIEW_BLOBS.slots
        assert cache._pop_decomp_preview_binary(token) is None

    def test_expiry(self, clock: FakeClock) -> None:
        token = cache._store_decomp_preview_binary(b"x")
        clock.advance(cache.DECOMP_PREVIEW_BINARY_TTL_SEC)
        assert cache._pop_decomp_preview_binary(token) is None

    def test_the_next_run_drops_an_unfetched_preview(self, clock: FakeClock) -> None:
        first = cache._store_decomp_preview_binary(b"a", "tab-a")
        other = cache._store_decomp_preview_binary(b"b", "tab-b")
        second = cache._store_decomp_preview_binary(b"c", "tab-a")
        assert set(cache._DECOMP_PREVIEW_BLOBS.slots) == {other, second}
        assert cache._pop_decomp_preview_binary(first) is None

    def test_sweep_drops_an_unfetched_preview(self, clock: FakeClock) -> None:
        token = cache._store_decomp_preview_binary(b"x")
        clock.advance(cache.DECOMP_PREVIEW_BINARY_TTL_SEC)
        cache.BUDGET.sweep()
        assert token not in cache._DECOMP_PREVIEW_BLOBS.slots


# ── run result kept for the run save ─────────────────────────────────────────


class TestRunResult:
    def test_read_only_view_with_its_spikes(self, clock: FakeClock) -> None:
        pulse = np.arange(6, dtype=np.float32).reshape(2, 3)
        spikes: list[IntArray] = [np.array([0, 2], np.int32), np.array([1], np.int32)]
        token = cache._store_run_result(pulse, spikes=spikes)
        got = cache._get_run_result(token)
        assert got is not None
        assert np.shares_memory(got, pulse)
        with pytest.raises(ValueError):
            got[0, 0] = 5
        entry = cache._get_run_result_entry(token)
        assert entry is not None and entry.spikes is not None
        assert [s.tolist() for s in entry.spikes] == [[0, 2], [1]]
        assert cache._RUN_RESULTS.slots[token].nbytes == pulse.nbytes + 12

    def test_keeps_only_the_latest_run_of_a_session(self, clock: FakeClock) -> None:
        first = cache._store_run_result(np.zeros((1, 3), dtype=np.float32), "tab-a")
        other = cache._store_run_result(np.zeros((1, 3), dtype=np.float32), "tab-b")
        second = cache._store_run_result(np.ones((1, 3), dtype=np.float32), "tab-a")
        assert cache._get_run_result(first) is None
        assert cache._get_run_result(second) is not None
        assert cache._get_run_result(other) is not None

    @pytest.mark.parametrize("token", [None, "", "unknown"])
    def test_missing_token(self, clock: FakeClock, token: str | None) -> None:
        assert cache._get_run_result(token) is None
        assert cache._get_run_result_entry(token) is None

    def test_does_not_expire_while_the_session_is_active(self, clock: FakeClock) -> None:
        token = cache._store_run_result(np.zeros((1, 3), dtype=np.float32))
        clock.advance(10 * SESSION_IDLE_SEC)
        cache.BUDGET.sweep()
        assert cache._get_run_result(token) is not None


# ── edit sessions ────────────────────────────────────────────────────────────


def _edit(fill: int = 1) -> EditSession:
    return EditSession(
        store=SessionStore.create("test-edit"),
        fsamp=2048.0,
        total_samples=50,
        spikes=[[fill, 10 + fill]],
        pulse=np.full((1, 50), fill, dtype=np.float32),
        mu_grid_index=[0],
        mu_uids=["g0_mu0"],
    )


class TestEditSessions:
    def test_keeps_one_session_per_tab_and_closes_the_one_it_drops(self, clock: FakeClock) -> None:
        first_edit = _edit()
        first = cache._store_edit_session(first_edit, "tab-a")
        other = cache._store_edit_session(_edit(3), "tab-b")
        second = cache._store_edit_session(_edit(2), "tab-a")
        assert set(cache._EDIT_SESSIONS.slots) == {other, second}
        assert cache._get_edit_session(first) is None
        assert not first_edit.store.path.exists()
        got = cache._get_edit_session(second)
        assert got is not None and got.spikes[0].tolist() == [2, 12]

    def test_release_before_the_next_open(self, clock: FakeClock) -> None:
        token = cache._store_edit_session(_edit(), "tab-a")
        cache._release_edit_sessions("tab-a")
        assert cache._get_edit_session(token) is None

    def test_a_reloaded_tab_takes_the_session_over(self, clock: FakeClock) -> None:
        token = cache._store_edit_session(_edit(), "tab-old")
        replaced = cache._store_edit_session(_edit(5), "tab-new")
        moved = cache._get_edit_session(token, "tab-new")
        assert moved is not None
        assert cache._EDIT_SESSIONS.slots[token].session == "tab-new"
        assert replaced not in cache._EDIT_SESSIONS.slots  # one session per tab
        cache.close_session("tab-old")
        assert cache._get_edit_session(token) is not None

    def test_counts_its_heap_arrays(self, clock: FakeClock) -> None:
        edit = _edit()
        token = cache._store_edit_session(edit, "tab-a")
        assert cache._EDIT_SESSIONS.slots[token].nbytes == edit.nbytes > 0


# ── sessions ─────────────────────────────────────────────────────────────────


class TestSessions:
    def _fill(self, session: str) -> list[str]:
        return [
            _store(session),
            cache._store_run_result(np.zeros((1, 3), dtype=np.float32), session),
            cache._store_edit_session(_edit(), session),
        ]

    def _alive(self, tokens: list[str]) -> list[bool]:
        upload, run, edit = tokens
        return [
            cache._get_upload_signal(upload) is not None,
            cache._get_run_result(run) is not None,
            cache._get_edit_session(edit) is not None,
        ]

    def test_close_drops_everything_the_session_holds(self, clock: FakeClock) -> None:
        closed = self._fill("tab-a")
        kept = self._fill("tab-b")
        cache.close_session("tab-a")
        assert self._alive(closed) == [False, False, False]
        assert self._alive(kept) == [True, True, True]
        assert "tab-a" not in cache.BUDGET.sessions

    def test_closing_frees_the_arrays(self, clock: FakeClock) -> None:
        tracemalloc.start()
        try:
            _store("tab-a", n_channels=64, n_samples=20_000)
            held = tracemalloc.get_traced_memory()[0]
            cache.close_session("tab-a")
            released = held - tracemalloc.get_traced_memory()[0]
        finally:
            tracemalloc.stop()
        assert released >= 64 * 20_000 * 8

    def test_sweep_closes_idle_sessions_but_not_the_active_one(self, clock: FakeClock) -> None:
        idle = self._fill("tab-a")
        clock.advance(SESSION_IDLE_SEC)
        active = self._fill("tab-b")
        clock.advance(1)
        cache.BUDGET.sweep()
        assert self._alive(idle) == [False, False, False]
        assert self._alive(active) == [True, True, True]

    def test_a_request_makes_its_session_active(self, clock: FakeClock) -> None:
        tokens = self._fill("tab-a")
        self._fill("tab-b")
        cache.BUDGET.touch("tab-a")
        clock.advance(SESSION_IDLE_SEC)
        cache.BUDGET.sweep()
        assert self._alive(tokens) == [True, True, True]
        assert "tab-b" not in cache.BUDGET.sessions

    def test_usage_reports_caches_and_sessions(self, clock: FakeClock) -> None:
        self._fill("tab-a")
        usage = cache.BUDGET.usage()
        assert usage["active_session"] == "tab-a"
        assert usage["used_bytes"] == cache.BUDGET.used_bytes > 0
        assert usage["caches"]["uploads"]["entries"] == 1
        assert usage["caches"]["decompose_previews"]["entries"] == 0
        [session] = usage["sessions"]
        assert session["id"] == "tab-a"
        assert session["active"] is True
        assert session["entries"] == 3
        assert session["bytes"] == usage["used_bytes"]


# ── T1 session stores owned by cache entries ─────────────────────────────────


def _stored_upload(session: str = "tab-a") -> tuple[str, SessionStore]:
    st = SessionStore.create("upload")
    data = st.allocate("emg", (4, 100), np.float32)
    data[:] = 1.0
    signal = SignalImport(data=st.seal(data), fsamp=2000.0, gridname=["GR08MM1305"])
    return cache._store_upload_signal(signal, session=session, store=st), st


def _stored_run(session: str = "tab-a") -> tuple[str, SessionStore]:
    st = SessionStore.create("run")
    pulse = st.allocate("pulse_trains", (2, 50), np.float32, zero=True)
    return cache._store_run_result(st.seal(pulse), session, st), st


class TestSessionStores:
    def test_upload_shares_its_memory_maps_and_costs_no_budget(self, clock: FakeClock) -> None:
        token, st = _stored_upload()
        got = cache._get_upload_signal(token)
        assert got is not None
        assert np.shares_memory(got.data, cache._UPLOADS.slots[token].value.signal.data)
        assert cache.BUDGET.used_bytes == 0
        assert st.path.exists()

    def test_memory_mapped_views_cost_no_budget(self, clock: FakeClock) -> None:
        token, st = _stored_upload()
        views = _views(st)
        cache._store_signal_views(token, views)
        assert views.nbytes == 0
        assert cache.BUDGET.used_bytes == 0

    @pytest.mark.parametrize("how", ["release", "close", "next_upload", "clear"])
    def test_dropping_an_upload_deletes_its_store(self, clock: FakeClock, how: str) -> None:
        token, st = _stored_upload("tab-a")
        if how == "release":
            cache._release_upload("tab-a")
        elif how == "close":
            cache.close_session("tab-a")
        elif how == "next_upload":
            _stored_upload("tab-a")
        else:
            cache.BUDGET.clear()
        assert cache._get_upload_signal(token) is None
        assert not st.path.exists()

    def test_idle_sessions_lose_their_stores(self, clock: FakeClock) -> None:
        _, idle = _stored_upload("tab-a")
        _stored_upload("tab-b")
        clock.advance(SESSION_IDLE_SEC)
        cache.BUDGET.sweep()
        assert not idle.path.exists()

    def test_run_result_keeps_its_store_until_its_session_closes(self, clock: FakeClock) -> None:
        token, st = _stored_run("tab-a")
        got = cache._get_run_result(token)
        assert got is not None and not got.flags.writeable
        assert cache.BUDGET.used_bytes == 0
        cache.close_session("tab-a")
        assert not st.path.exists()

    def test_the_next_run_deletes_the_previous_store(self, clock: FakeClock) -> None:
        _, first = _stored_run("tab-a")
        _, second = _stored_run("tab-a")
        assert not first.path.exists()
        assert second.path.exists()
