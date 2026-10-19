"""One memory budget for every API cache, with entries scoped to browser sessions."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from muedit.errors import MueditError

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
BUDGET_RAM_FRACTION = 0.10
BUDGET_MIN_BYTES = 256 * MIB
BUDGET_MAX_BYTES = 1024 * MIB
BUDGET_ENV = "MUEDIT_CACHE_BUDGET_MB"
SESSION_IDLE_SEC = 20 * 60
SWEEP_INTERVAL_SEC = 60.0
DEFAULT_SESSION = "default"
_SESSION_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


def physical_memory_bytes() -> int | None:
    """Installed RAM, or None when the platform does not report it."""
    total: int | None = None
    if sys.platform == "win32":
        import ctypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            total = int(status.ullTotalPhys)
    else:
        with contextlib.suppress(ValueError, OSError):
            total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    return total


def default_budget_bytes() -> int:
    """``clamp(0.10 × RAM, 256 MB, 1 GB)``, or ``MUEDIT_CACHE_BUDGET_MB`` when it is set."""
    override = os.environ.get(BUDGET_ENV, "").strip()
    if override:
        return int(float(override) * MIB)
    ram = physical_memory_bytes()
    if ram is None:
        return BUDGET_MIN_BYTES
    return int(min(max(BUDGET_RAM_FRACTION * ram, BUDGET_MIN_BYTES), BUDGET_MAX_BYTES))


def parse_session_id(value: str, field: str) -> str:
    """The client's session id from ``field``; a malformed one is refused."""
    value = value.strip()
    if not _SESSION_ID.fullmatch(value):
        raise MueditError("Malformed session id: 1 to 64 letters, digits or '-'", field)
    return value


class _Sized(Protocol):
    @property
    def nbytes(self) -> int: ...


V = TypeVar("V", bound=_Sized)


@dataclass
class _Slot(Generic[V]):
    value: V
    nbytes: int
    session: str
    last_used: int
    holds: int = 0  # requests and runs reading the entry: budget pressure never evicts it


class MemoryBudget:
    """Byte limit shared by every registered cache; the least recently used entries go first."""

    def __init__(self, limit_bytes: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit_bytes = int(limit_bytes)
        self.clock = clock
        self.lock = threading.RLock()
        self.caches: dict[str, BudgetedLRU[Any]] = {}
        self.sessions: dict[str, float] = {}  # session id → time of its last request
        self.active_session: str | None = None
        self._tick = 0
        self._sweeper: threading.Thread | None = None
        self._stop_sweeper = threading.Event()
        self._depth = 0  # nested holds of ``lock`` by the thread that owns it
        self._dropped: list[tuple[BudgetedLRU[Any], str, Any]] = []

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """Hold ``lock``; entries dropped meanwhile are released once the outermost hold ends.

        A release can wait on a lock a request holds while that request asks the budget, so
        it never runs under ``lock``.
        """
        dropped: list[tuple[BudgetedLRU[Any], str, Any]] = []
        try:
            with self.lock:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                    if self._depth == 0:
                        dropped, self._dropped = self._dropped, []
        finally:
            for cache, token, value in dropped:
                cache.release(token, value)

    def register(self, cache: BudgetedLRU[Any]) -> None:
        """Count ``cache`` against this budget."""
        with self.locked():
            if cache.name in self.caches:
                raise ValueError(f"Cache {cache.name!r} is already registered")
            self.caches[cache.name] = cache

    def next_tick(self) -> int:
        """A counter that orders uses across all caches."""
        self._tick += 1
        return self._tick

    @property
    def used_bytes(self) -> int:
        """Bytes held by every registered cache."""
        with self.locked():
            return sum(cache.nbytes for cache in self.caches.values())

    def touch(self, session: str) -> None:
        """Record a request from ``session``, which becomes the active session."""
        with self.locked():
            self.sessions[session] = self.clock()
            self.active_session = session

    def make_room(self, nbytes: int, keep: str | None = None) -> None:
        """Evict other sessions' entries, least recently used first, until ``nbytes`` more fit."""
        with self.locked():
            used = self.used_bytes
            while used + nbytes > self.limit_bytes:
                victims = [
                    (slot.last_used, cache, token)
                    for cache in self.caches.values()
                    for token, slot in cache.slots.items()
                    if token != keep and slot.session != self.active_session and not slot.holds
                ]
                if not victims:
                    return  # what is left is the active session's or in use; it may exceed the budget
                _, cache, token = min(victims, key=lambda v: v[0])
                evicted = cache.drop(token)
                used -= evicted.nbytes
                logger.debug(
                    "Evicted %s entry %s (%.1f MB); %.1f MB of %.1f MB in use",
                    cache.name,
                    token,
                    evicted.nbytes / MIB,
                    used / MIB,
                    self.limit_bytes / MIB,
                )

    def close_session(self, session: str) -> None:
        """Drop every entry ``session`` holds, in all caches."""
        with self.locked():
            for cache in self.caches.values():
                cache.release_session(session)
            self.sessions.pop(session, None)
            if self.active_session == session:
                self.active_session = None

    def sweep(self) -> None:
        """Close idle sessions other than the active one."""
        with self.locked():
            now = self.clock()
            idle = [
                session
                for session, seen in self.sessions.items()
                if session != self.active_session and now - seen >= SESSION_IDLE_SEC
            ]
            for session in idle:
                logger.debug("Closing idle session %s", session)
                self.close_session(session)

    def start_sweeper(self, interval_sec: float = SWEEP_INTERVAL_SEC) -> None:
        """Run ``sweep`` every ``interval_sec`` on a daemon thread until ``stop_sweeper``."""
        with self.locked():
            if self._sweeper is not None and self._sweeper.is_alive():
                return
            self._stop_sweeper.clear()
            self._sweeper = threading.Thread(
                target=self._sweep_loop,
                args=(interval_sec,),
                name="muedit-cache-sweep",
                daemon=True,
            )
            self._sweeper.start()

    def stop_sweeper(self) -> None:
        """Stop the sweep thread and wait for it to finish."""
        self._stop_sweeper.set()
        sweeper, self._sweeper = self._sweeper, None
        if sweeper is not None:
            sweeper.join(timeout=5)

    def _sweep_loop(self, interval_sec: float) -> None:
        """Body of the sweep thread."""
        while not self._stop_sweeper.wait(interval_sec):
            try:
                self.sweep()
            except Exception:
                logger.exception("Cache sweep failed")

    def clear(self) -> None:
        """Drop every entry and forget every session."""
        with self.locked():
            for cache in self.caches.values():
                for token in list(cache.slots):
                    cache.drop(token)
            self.sessions.clear()
            self.active_session = None


class BudgetedLRU(Generic[V]):
    """Token-keyed cache whose entries count against a shared ``MemoryBudget``."""

    def __init__(
        self,
        name: str,
        budget: MemoryBudget,
        *,
        per_session: int | None = None,
        on_drop: Callable[[V], None] | None = None,
    ) -> None:
        self.name = name
        self.budget = budget
        self.per_session = per_session
        self.on_drop = on_drop  # releases what an entry holds outside the heap
        self.slots: dict[str, _Slot[V]] = {}
        budget.register(self)

    @property
    def nbytes(self) -> int:
        """Bytes held by this cache."""
        return sum(slot.nbytes for slot in self.slots.values())

    def drop(self, token: str) -> _Slot[V]:
        """Remove the entry for ``token`` and release it.

        The release runs once the budget's outermost hold of its lock ends.
        """
        with self.budget.locked():
            slot = self.slots.pop(token)
            self.budget._dropped.append((self, token, slot.value))
        return slot

    def release(self, token: str, value: V) -> None:
        """Release what a dropped entry holds outside the heap; a failure is only logged."""
        if self.on_drop is None:
            return
        try:
            self.on_drop(value)
        except Exception:
            logger.exception("Releasing %s entry %s failed", self.name, token)

    def pin(self, value: V, session: str = DEFAULT_SESSION) -> str:
        """Store session data that is never evicted while ``session`` is active, and return its token."""
        nbytes = int(value.nbytes)
        with self.budget.locked():
            self.budget.touch(session)
            if self.per_session is not None:
                own = [t for t, slot in self.slots.items() if slot.session == session]
                for token in own[: max(len(own) - self.per_session + 1, 0)]:
                    self.drop(token)
            self.budget.make_room(nbytes)
            token = uuid.uuid4().hex
            self.slots[token] = _Slot(
                value=value,
                nbytes=nbytes,
                session=session,
                last_used=self.budget.next_tick(),
            )
        return token

    def get(self, token: str | None) -> V | None:
        """The value for ``token``, marked as just used, or None."""
        with self.budget.locked():
            slot = self.slots.get(token) if token else None
            if slot is None:
                return None
            slot.last_used = self.budget.next_tick()
            if slot.session in self.budget.sessions:
                self.budget.sessions[slot.session] = self.budget.clock()
            return slot.value

    def hold(self, token: str | None) -> V | None:
        """``get``, and keep the entry from eviction until ``unhold``; a drop still removes it."""
        with self.budget.locked():
            value = self.get(token)
            if value is not None and token is not None:
                self.slots[token].holds += 1
            return value

    def unhold(self, token: str) -> None:
        """End one ``hold``; nothing when the entry was dropped meanwhile."""
        with self.budget.locked():
            slot = self.slots.get(token)
            if slot is not None and slot.holds > 0:
                slot.holds -= 1

    def move(self, token: str | None, session: str) -> V | None:
        """The value for ``token``, now held by ``session`` (whose other entries here make room)."""
        with self.budget.locked():
            slot = self.slots.get(token) if token else None
            if slot is None or token is None:
                return None
            if slot.session != session and self.per_session is not None:
                own = [t for t, s in self.slots.items() if s.session == session and t != token]
                for other in own[: max(len(own) - self.per_session + 1, 0)]:
                    self.drop(other)
            slot.session = session
            self.budget.touch(session)
            slot.last_used = self.budget.next_tick()
            return slot.value

    def resize(self, token: str) -> None:
        """Recount the entry for ``token`` after its value grew, evicting others to fit."""
        with self.budget.locked():
            slot = self.slots.get(token)
            if slot is None:
                return
            nbytes = int(slot.value.nbytes)
            self.budget.make_room(nbytes - slot.nbytes, keep=token)
            slot.nbytes = nbytes

    def release_session(self, session: str) -> None:
        """Drop the entries ``session`` holds in this cache."""
        with self.budget.locked():
            for token in [t for t, slot in self.slots.items() if slot.session == session]:
                self.drop(token)
