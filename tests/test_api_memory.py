"""Tests for the shared cache budget (``muedit.api.memory``)."""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from muedit.api import memory
from muedit.api.memory import MIB, BudgetedLRU, MemoryBudget


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


@pytest.mark.parametrize(
    ("header", "session"),
    [
        (None, memory.DEFAULT_SESSION),
        ("", memory.DEFAULT_SESSION),
        (" 3f2a-bc ", "3f2a-bc"),
        ("a" * 64, "a" * 64),
        ("a" * 65, memory.DEFAULT_SESSION),
        ("../etc", memory.DEFAULT_SESSION),
    ],
)
def test_session_id_or_default(header: str | None, session: str) -> None:
    assert memory.session_id_or_default(header) == session


class TestBudgetedLRU:
    def test_names_are_unique_per_budget(self, budget: MemoryBudget) -> None:
        BudgetedLRU[Blob]("a", budget)
        with pytest.raises(ValueError):
            BudgetedLRU[Blob]("a", budget)

    def test_offer_refuses_entries_over_a_quarter_of_the_budget(self, budget: MemoryBudget) -> None:
        lru = BudgetedLRU[Blob]("derived", budget)
        assert lru.offer(Blob(251)) is None
        assert lru.offer(Blob(250)) is not None
        assert lru.pin(Blob(900)) is not None

    def test_least_recently_used_goes_first_across_caches(self, budget: MemoryBudget) -> None:
        first = BudgetedLRU[Blob]("first", budget)
        second = BudgetedLRU[Blob]("second", budget)
        old = first.offer(Blob(200))
        used = second.offer(Blob(200))
        newer = first.offer(Blob(200))
        assert second.get(used) is not None
        second.offer(Blob(250))
        first.offer(Blob(250))
        assert set(first.slots) & {old, newer} == {newer}
        first.offer(Blob(200))
        assert newer not in first.slots
        assert used in second.slots
        assert budget.used_bytes == 900

    def test_unpinned_entries_go_before_other_sessions_pins(self, budget: MemoryBudget) -> None:
        session_data = BudgetedLRU[Blob]("session", budget)
        derived = BudgetedLRU[Blob]("derived", budget)
        pinned = session_data.pin(Blob(400), "tab-a")
        extra = derived.offer(Blob(200), "tab-b")
        session_data.pin(Blob(500), "tab-b")
        assert extra not in derived.slots
        assert pinned in session_data.slots
        session_data.pin(Blob(400), "tab-c")
        assert pinned not in session_data.slots

    def test_active_sessions_pins_are_never_evicted(self, budget: MemoryBudget) -> None:
        lru = BudgetedLRU[Blob]("session", budget)
        tokens = [lru.pin(Blob(600), "tab-a"), lru.offer(Blob(200), "tab-a")]
        lru.pin(Blob(600), "tab-a")
        assert lru.get(tokens[0]) is not None
        assert lru.get(tokens[1]) is None
        assert budget.used_bytes == 1200

    def test_per_session_limit_replaces_the_oldest(self, budget: MemoryBudget) -> None:
        lru = BudgetedLRU[Blob]("session", budget, per_session=2)
        a1, a2 = lru.pin(Blob(1), "a"), lru.pin(Blob(1), "a")
        b1 = lru.pin(Blob(1), "b")
        a3 = lru.pin(Blob(1), "a")
        assert set(lru.slots) == {a2, b1, a3}
        assert lru.get(a1) is None

    def test_resize_evicts_others_to_fit(self, budget: MemoryBudget) -> None:
        lru = BudgetedLRU[Blob]("session", budget)
        other = lru.offer(Blob(200), "tab-b")
        blob = Blob(100)
        token = lru.pin(blob, "tab-a")
        blob.nbytes = 850
        lru.resize(token)
        assert lru.get(other) is None
        assert lru.slots[token].nbytes == 850
        assert budget.used_bytes == 850

    def test_time_to_live(self, budget: MemoryBudget) -> None:
        clock = budget.clock
        assert isinstance(clock, Clock)
        lru = BudgetedLRU[Blob]("short", budget, ttl_sec=10)
        token = lru.pin(Blob(1))
        clock.now = 9.9
        assert lru.get(token) is not None
        clock.now = 10
        assert lru.get(token) is None

    def test_pop_and_discard(self, budget: MemoryBudget) -> None:
        lru = BudgetedLRU[Blob]("once", budget)
        blob = Blob(5)
        token = lru.pin(blob)
        assert lru.pop(token) is blob
        assert lru.pop(token) is None
        token = lru.pin(blob)
        lru.discard(token)
        lru.discard(None)
        assert budget.used_bytes == 0


def test_sweeper_thread_runs_until_stopped() -> None:
    budget = MemoryBudget(1000)
    lru = BudgetedLRU[Blob]("short", budget, ttl_sec=0)
    lru.pin(Blob(1))
    budget.start_sweeper(interval_sec=0.01)
    budget.start_sweeper(interval_sec=0.01)
    deadline = time.monotonic() + 5
    while lru.slots and time.monotonic() < deadline:
        time.sleep(0.01)
    budget.stop_sweeper()
    assert not lru.slots


class TestOnDrop:
    def test_runs_when_an_entry_leaves_but_not_on_pop(self, budget: MemoryBudget) -> None:
        dropped: list[Blob] = []
        cache: BudgetedLRU[Blob] = BudgetedLRU("c", budget, on_drop=dropped.append)
        evicted, popped, released = Blob(600), Blob(10), Blob(20)
        cache.pin(evicted, "a")
        cache.pin(Blob(600), "b")  # the active session's pin evicts the other one
        token = cache.pin(popped, "b")
        assert cache.pop(token) is popped
        cache.pin(released, "c")
        cache.release_session("c")
        assert dropped == [evicted, released]

    def test_a_failing_release_does_not_break_the_cache(self, budget: MemoryBudget) -> None:
        def boom(_: Blob) -> None:
            raise OSError("still mapped")

        cache: BudgetedLRU[Blob] = BudgetedLRU("c", budget, on_drop=boom)
        token = cache.pin(Blob(10), "a")
        cache.discard(token)
        assert token not in cache.slots
