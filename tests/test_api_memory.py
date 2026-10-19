"""Tests for the shared cache budget (``muedit.api.memory``)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import pytest

from muedit.api import memory
from muedit.api.memory import MIB, BudgetedLRU, MemoryBudget
from muedit.errors import MueditError


@dataclass
class Blob:
    nbytes: int


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def budget() -> MemoryBudget:
    return MemoryBudget(1000, clock=Clock())


class TestDefaultBudget:
    @pytest.mark.parametrize(
        ("ram_gb", "budget_mb"),
        [(None, 256), (1, 256), (2, 256), (4, 409.6), (8, 819.2), (64, 1024)],
    )
    def test_ten_percent_of_ram_within_limits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ram_gb: int | None,
        budget_mb: float,
    ) -> None:
        monkeypatch.delenv(memory.BUDGET_ENV, raising=False)
        ram = None if ram_gb is None else ram_gb * 1024 * MIB
        monkeypatch.setattr(memory, "physical_memory_bytes", lambda: ram)
        assert memory.default_budget_bytes() == int(budget_mb * MIB)

    def test_environment_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(memory.BUDGET_ENV, "64")
        assert memory.default_budget_bytes() == 64 * MIB


def test_platform_memory_readers() -> None:
    ram = memory.physical_memory_bytes()
    held = memory.process_memory_bytes()
    assert ram is not None and ram > 0
    assert held is not None and 0 < held < ram
    peak = memory.peak_rss_bytes()
    assert peak is None or peak >= held // 2


@pytest.mark.parametrize(("raw", "session"), [(" 3f2a-bc ", "3f2a-bc"), ("a" * 64, "a" * 64)])
def test_a_well_formed_session_id_is_taken(raw: str, session: str) -> None:
    assert memory.parse_session_id(raw, "session") == session


@pytest.mark.parametrize("raw", ["", "a" * 65, "../etc"])
def test_a_malformed_session_id_is_refused(raw: str) -> None:
    with pytest.raises(MueditError) as refused:
        memory.parse_session_id(raw, "X-MUedit-Session")
    assert refused.value.field == "X-MUedit-Session"


class TestBudgetedLRU:
    def test_names_are_unique_per_budget(self, budget: MemoryBudget) -> None:
        BudgetedLRU[Blob]("a", budget)
        with pytest.raises(ValueError):
            BudgetedLRU[Blob]("a", budget)

    def test_least_recently_used_goes_first_across_caches(self, budget: MemoryBudget) -> None:
        first = BudgetedLRU[Blob]("first", budget)
        second = BudgetedLRU[Blob]("second", budget)
        old = first.pin(Blob(200), "a")
        used = second.pin(Blob(200), "b")
        newer = first.pin(Blob(200), "c")
        assert second.get(used) is not None
        second.pin(Blob(250), "d")
        first.pin(Blob(250), "e")
        assert set(first.slots) & {old, newer} == {newer}
        first.pin(Blob(200), "f")
        assert newer not in first.slots
        assert used in second.slots
        assert budget.used_bytes == 900

    def test_active_sessions_entries_are_never_evicted(self, budget: MemoryBudget) -> None:
        lru = BudgetedLRU[Blob]("session", budget)
        tokens = [lru.pin(Blob(600), "tab-a"), lru.pin(Blob(200), "tab-b")]
        lru.pin(Blob(600), "tab-a")
        assert lru.get(tokens[0]) is not None
        assert lru.get(tokens[1]) is None
        assert budget.used_bytes == 1200

    def test_held_entries_are_not_evicted_until_unheld(self, budget: MemoryBudget) -> None:
        lru = BudgetedLRU[Blob]("session", budget)
        held = lru.pin(Blob(600), "tab-a")
        assert lru.hold(held) is not None
        lru.pin(Blob(600), "tab-b")
        assert held in lru.slots
        lru.unhold(held)
        lru.pin(Blob(300), "tab-c")
        assert held not in lru.slots

    def test_a_held_entry_is_still_dropped_explicitly(self, budget: MemoryBudget) -> None:
        lru = BudgetedLRU[Blob]("session", budget)
        held = lru.pin(Blob(1), "tab-a")
        lru.hold(held)
        lru.release_session("tab-a")
        assert held not in lru.slots
        lru.unhold(held)

    def test_per_session_limit_replaces_the_oldest(self, budget: MemoryBudget) -> None:
        lru = BudgetedLRU[Blob]("session", budget, per_session=2)
        a1, a2 = lru.pin(Blob(1), "a"), lru.pin(Blob(1), "a")
        b1 = lru.pin(Blob(1), "b")
        a3 = lru.pin(Blob(1), "a")
        assert set(lru.slots) == {a2, b1, a3}
        assert lru.get(a1) is None

    def test_resize_evicts_others_to_fit(self, budget: MemoryBudget) -> None:
        lru = BudgetedLRU[Blob]("session", budget)
        other = lru.pin(Blob(200), "tab-b")
        blob = Blob(100)
        token = lru.pin(blob, "tab-a")
        blob.nbytes = 850
        lru.resize(token)
        assert lru.get(other) is None
        assert lru.slots[token].nbytes == 850
        assert budget.used_bytes == 850


def test_sweeper_thread_runs_until_stopped() -> None:
    clock = Clock()
    budget = MemoryBudget(1000, clock=clock)
    lru = BudgetedLRU[Blob]("idle", budget)
    lru.pin(Blob(1), "a")
    budget.touch("b")
    clock.now += memory.SESSION_IDLE_SEC
    budget.start_sweeper(interval_sec=0.01)
    budget.start_sweeper(interval_sec=0.01)
    deadline = time.monotonic() + 5
    while lru.slots and time.monotonic() < deadline:
        time.sleep(0.01)
    budget.stop_sweeper()
    assert not lru.slots


class TestOnDrop:
    def test_runs_when_an_entry_leaves(self, budget: MemoryBudget) -> None:
        dropped: list[Blob] = []
        cache: BudgetedLRU[Blob] = BudgetedLRU("c", budget, on_drop=dropped.append)
        evicted, released = Blob(600), Blob(20)
        cache.pin(evicted, "a")
        cache.pin(Blob(600), "b")  # the active session's pin evicts the other one
        cache.pin(released, "c")
        cache.release_session("c")
        assert dropped == [evicted, released]

    def test_a_failing_release_does_not_break_the_cache(self, budget: MemoryBudget) -> None:
        def boom(_: Blob) -> None:
            raise OSError("still mapped")

        cache: BudgetedLRU[Blob] = BudgetedLRU("c", budget, on_drop=boom)
        token = cache.pin(Blob(10), "a")
        cache.drop(token)
        assert token not in cache.slots

    @pytest.mark.parametrize(
        "leave",
        ["evict", "per_session", "release_session", "close_session", "sweep", "clear", "drop"],
    )
    def test_runs_after_the_budget_lock_is_released(self, budget: MemoryBudget, leave: str) -> None:
        # A release can wait on a lock a request holds while that request asks the budget.
        def lock_is_free() -> bool:
            free: list[bool] = []

            def probe() -> None:
                acquired = budget.lock.acquire(timeout=1)
                if acquired:
                    budget.lock.release()
                free.append(acquired)

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join()
            return free[0]

        seen: list[bool] = []
        cache: BudgetedLRU[Blob] = BudgetedLRU(
            "c", budget, per_session=1, on_drop=lambda _: seen.append(lock_is_free())
        )
        token = cache.pin(Blob(600), "a")
        if leave == "evict":
            cache.pin(Blob(600), "b")
        elif leave == "per_session":
            cache.pin(Blob(10), "a")
        elif leave == "release_session":
            cache.release_session("a")
        elif leave == "close_session":
            budget.close_session("a")
        elif leave == "sweep":
            budget.touch("b")
            budget.clock.now += memory.SESSION_IDLE_SEC  # type: ignore[attr-defined]
            budget.sweep()
        elif leave == "clear":
            budget.clear()
        else:
            cache.drop(token)
        assert seen == [True]

    def test_runs_when_the_hold_that_dropped_it_raises(self, budget: MemoryBudget) -> None:
        dropped: list[Blob] = []
        cache: BudgetedLRU[Blob] = BudgetedLRU("c", budget, on_drop=dropped.append)
        blob = Blob(10)
        cache.pin(blob, "a")
        with pytest.raises(RuntimeError), budget.locked():
            cache.release_session("a")
            assert dropped == []  # not while the outer hold lasts
            raise RuntimeError
        assert dropped == [blob]
