"""Direct tests for the API caches (``muedit.api.cache``)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from muedit.api import cache
from muedit.models import EditSignalContext, SignalImport


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def clean_caches() -> Iterator[None]:
    caches = (
        cache._UPLOAD_SESSION_CACHE,
        cache._DECOMP_PREVIEW_BINARY_CACHE,
        cache._RUN_RESULT_CACHE,
        cache._EDIT_SIGNAL_CONTEXT_CACHE,
        cache._EDIT_SIGNAL_LABEL_INDEX,
    )
    for c in caches:
        c.clear()
    yield
    for c in caches:
        c.clear()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(cache, "time", SimpleNamespace(time=fake.time))
    return fake


def _signal(n_channels: int = 4, n_samples: int = 100, fill: float = 1.0) -> SignalImport:
    return SignalImport(
        data=np.full((n_channels, n_samples), fill, dtype=np.float64),
        fsamp=2000.0,
        gridname=["GR08MM1305"],
        muscle=["TA"],
    )


def _store(clock: FakeClock, **kwargs: Any) -> str:
    """Store a signal, then tick the clock so entries get distinct expiry times."""
    token = cache._store_upload_signal(_signal(**kwargs))
    clock.advance(1)
    return token


# ── upload session cache ─────────────────────────────────────────────────────


class TestUploadSignal:
    def test_store_copies_and_get_shares_read_only(self, clock: FakeClock) -> None:
        signal = _signal()
        token = cache._store_upload_signal(signal, source_path="/data/rec.otb+")
        signal.data[:] = 0
        first = cache._get_upload_signal(token)
        assert first is not None
        np.testing.assert_array_equal(first.data, np.ones((4, 100)))
        assert np.shares_memory(first.data, cache._UPLOAD_SESSION_CACHE[token].signal.data)
        with pytest.raises(ValueError):
            first.data[0, 0] = 5
        with pytest.raises(ValueError):
            first.auxiliary[...] = 5
        assert cache._get_upload_source_path(token) == "/data/rec.otb+"

    def test_metadata_and_names_are_independent(self, clock: FakeClock) -> None:
        token = cache._store_upload_signal(_signal())
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

    def test_expires_after_ttl(self, clock: FakeClock) -> None:
        token = _store(clock)
        clock.advance(cache.UPLOAD_CACHE_TTL_SEC)
        assert cache._get_upload_signal(token) is None
        assert token not in cache._UPLOAD_SESSION_CACHE

    def test_access_refreshes_ttl(self, clock: FakeClock) -> None:
        token = _store(clock)
        for _ in range(3):
            clock.advance(cache.UPLOAD_CACHE_TTL_SEC - 10)
            assert cache._get_upload_signal(token) is not None

    def test_item_budget_evicts_oldest_expiring(self, clock: FakeClock) -> None:
        tokens = [_store(clock) for _ in range(cache.UPLOAD_CACHE_MAX_ITEMS + 1)]
        assert len(cache._UPLOAD_SESSION_CACHE) == cache.UPLOAD_CACHE_MAX_ITEMS
        assert tokens[0] not in cache._UPLOAD_SESSION_CACHE
        assert all(t in cache._UPLOAD_SESSION_CACHE for t in tokens[1:])

    def test_byte_budget_evicts_before_item_budget(
        self,
        clock: FakeClock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        one_entry = np.zeros((4, 100)).nbytes
        monkeypatch.setattr(cache, "UPLOAD_CACHE_MAX_BYTES", int(one_entry * 1.5))
        first = _store(clock)
        second = _store(clock)
        assert first not in cache._UPLOAD_SESSION_CACHE
        assert second in cache._UPLOAD_SESSION_CACHE


# ── QC arrays attached to an upload session ──────────────────────────────────


class TestQcSignal:
    def _attach(self, token: str, n_samples: int = 100) -> None:
        cache._store_qc_signal(
            token,
            data=np.ones((6, n_samples)),
            fsamp=2000.0,
            grid_names=["A", "B"],
            discard_channels=[np.zeros(4, dtype=int), np.zeros(2, dtype=int)],
        )

    def test_round_trip(self, clock: FakeClock) -> None:
        token = _store(clock)
        self._attach(token)
        qc = cache._get_qc_signal(token)
        assert qc is not None
        assert qc.data.dtype == np.float32
        assert qc.channel_offsets == [0, 4]
        assert qc.grid_names == ["A", "B"]

    def test_data_view_is_read_only(self, clock: FakeClock) -> None:
        token = _store(clock)
        self._attach(token)
        qc = cache._get_qc_signal(token)
        assert qc is not None
        with pytest.raises(ValueError):
            qc.data[0, 0] = 5

    def test_without_qc_returns_none(self, clock: FakeClock) -> None:
        token = _store(clock)
        assert cache._get_qc_signal(token) is None


# ── decompose preview binary cache ───────────────────────────────────────────


class TestDecompPreviewBinary:
    def test_first_fetch_removes_it(self, clock: FakeClock) -> None:
        token = cache._store_decomp_preview_binary(b"MUB1" + b"\0" * 12)
        assert cache._pop_decomp_preview_binary(token) == b"MUB1" + b"\0" * 12
        assert token not in cache._DECOMP_PREVIEW_BINARY_CACHE
        assert cache._pop_decomp_preview_binary(token) is None

    def test_expiry(self, clock: FakeClock) -> None:
        token = cache._store_decomp_preview_binary(b"x")
        clock.advance(cache.DECOMP_PREVIEW_BINARY_TTL_SEC)
        assert cache._pop_decomp_preview_binary(token) is None

    def test_byte_budget(self, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cache, "DECOMP_PREVIEW_BINARY_MAX_BYTES", 150)
        first = cache._store_decomp_preview_binary(b"x" * 100)
        clock.advance(1)
        second = cache._store_decomp_preview_binary(b"y" * 100)
        assert cache._pop_decomp_preview_binary(first) is None
        assert cache._pop_decomp_preview_binary(second) == b"y" * 100


# ── run result kept for the run save ─────────────────────────────────────────


class TestRunResult:
    def test_read_only_view_until_dropped(self, clock: FakeClock) -> None:
        pulse = np.arange(6, dtype=np.float32).reshape(2, 3)
        token = cache._store_run_result(pulse)
        got = cache._get_run_result(token)
        assert got is not None
        assert np.shares_memory(got, pulse)
        with pytest.raises(ValueError):
            got[0, 0] = 5
        cache._drop_run_result(token)
        assert cache._get_run_result(token) is None

    def test_keeps_only_the_latest_run(self, clock: FakeClock) -> None:
        first = cache._store_run_result(np.zeros((1, 3), dtype=np.float32))
        clock.advance(1)
        second = cache._store_run_result(np.ones((1, 3), dtype=np.float32))
        assert cache._get_run_result(first) is None
        assert cache._get_run_result(second) is not None

    @pytest.mark.parametrize("token", [None, "", "unknown"])
    def test_missing_token(self, clock: FakeClock, token: str | None) -> None:
        assert cache._get_run_result(token) is None
        cache._drop_run_result(token)

    def test_expires_after_ttl(self, clock: FakeClock) -> None:
        token = cache._store_run_result(np.zeros((1, 3), dtype=np.float32))
        clock.advance(cache.RUN_RESULT_TTL_SEC)
        assert cache._get_run_result(token) is None


# ── edit signal context cache + label index ──────────────────────────────────


def _context(fill: float = 1.0) -> EditSignalContext:
    return EditSignalContext(
        data=np.full((4, 50), fill, dtype=np.float64),
        fsamp=2048.0,
        grid_names=["G1"],
        emgmask=[np.zeros(4, dtype=int)],
        coordinates=[np.zeros((4, 2))],
        aux_data=np.ones((1, 50)),
        aux_names=["Force"],
        artifact_mask=np.zeros(50, dtype=bool),
        loader_meta={"manufacturer": "OTBioelettronica"},
    )


class TestEditSignalContext:
    def test_round_trip_by_token_and_label(self, clock: FakeClock) -> None:
        token = cache._store_edit_signal_context(_context(), file_label="rec_decomp.npz")
        by_token = cache._get_edit_signal_context(token)
        by_label = cache._get_edit_signal_context_by_label(" rec_decomp.npz ")
        for ctx in (by_token, by_label):
            assert ctx is not None
            assert ctx.data.dtype == np.float32
            assert ctx.fsamp == 2048.0
            assert ctx.aux_names == ["Force"]
            assert ctx.loader_meta == {"manufacturer": "OTBioelettronica"}

    def test_returned_arrays_are_read_only_views(self, clock: FakeClock) -> None:
        token = cache._store_edit_signal_context(_context())
        ctx = cache._get_edit_signal_context(token)
        assert ctx is not None
        stored = cache._EDIT_SIGNAL_CONTEXT_CACHE[token].context
        assert np.shares_memory(ctx.data, stored.data)
        arrays = [ctx.data, ctx.aux_data, ctx.artifact_mask, *ctx.emgmask, *ctx.coordinates]
        for arr in arrays:
            assert arr is not None
            with pytest.raises(ValueError):
                arr[...] = 0
        ctx.loader_meta["manufacturer"] = "changed"
        again = cache._get_edit_signal_context(token)
        assert again is not None
        assert again.loader_meta == {"manufacturer": "OTBioelettronica"}

    def test_keeps_one_context_and_prunes_stale_label(self, clock: FakeClock) -> None:
        first = cache._store_edit_signal_context(_context(), file_label="a.npz")
        clock.advance(1)
        second = cache._store_edit_signal_context(_context(2.0), file_label="b.npz")
        assert list(cache._EDIT_SIGNAL_CONTEXT_CACHE) == [second]
        assert cache._get_edit_signal_context(first) is None
        assert cache._EDIT_SIGNAL_LABEL_INDEX["a.npz"] == first
        assert cache._get_edit_signal_context_by_label("a.npz") is None
        assert "a.npz" not in cache._EDIT_SIGNAL_LABEL_INDEX
        ctx = cache._get_edit_signal_context_by_label("b.npz")
        assert ctx is not None
        assert ctx.data.max() == 2

    def test_expiry_purges_label_index(self, clock: FakeClock) -> None:
        cache._store_edit_signal_context(_context(), file_label="a.npz")
        clock.advance(cache.EDIT_SIGNAL_CONTEXT_TTL_SEC)
        assert cache._get_edit_signal_context_by_label("a.npz") is None
        assert cache._EDIT_SIGNAL_LABEL_INDEX == {}
