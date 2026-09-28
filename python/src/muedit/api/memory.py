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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
BUDGET_RAM_FRACTION = 0.10
BUDGET_MIN_BYTES = 256 * MIB
BUDGET_MAX_BYTES = 1024 * MIB
BUDGET_ENV = "MUEDIT_CACHE_BUDGET_MB"
ADMIT_FRACTION = 0.25
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


def process_memory_bytes() -> int | None:
    """Memory this process holds now, or None where it cannot be read."""
    held: int | None = None
    try:
        if sys.platform == "win32":
            held = _windows_working_set_bytes()
        elif sys.platform == "darwin":
            held = _darwin_footprint_bytes()
        else:
            with open("/proc/self/statm") as statm:
                held = int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None
    return held


def _darwin_footprint_bytes() -> int | None:
    """Physical footprint from the Mach ``task_info`` call, as Activity Monitor shows it."""
    # Not resident_size: macOS keeps freed pages counted there until it needs them back.
    import ctypes

    counters = [
        "virtual_size", "region_count_and_page_size", "resident_size", "resident_size_peak",
        "device", "device_peak", "internal", "internal_peak", "external", "external_peak",
        "reusable", "reusable_peak", "purgeable_volatile_pmap", "purgeable_volatile_resident",
        "purgeable_volatile_virtual", "compressed", "compressed_peak", "compressed_lifetime",
        "phys_footprint",
    ]  # fmt: skip

    class _TaskVmInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in counters]

    task_vm_info = 22
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    info = _TaskVmInfo()
    count = ctypes.c_uint(ctypes.sizeof(info) // 4)
    task = ctypes.c_uint.in_dll(libc, "mach_task_self_")
    if libc.task_info(task, task_vm_info, ctypes.byref(info), ctypes.byref(count)) != 0:
        return None
    return int(info.phys_footprint)


def _windows_working_set_bytes() -> int | None:
    """Working-set size from ``GetProcessMemoryInfo``."""
    import ctypes

    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = _Counters()
    counters.cb = ctypes.sizeof(counters)
    working_set: int | None = None
    # The only caller is Windows-only, but mypy checks this body on every OS and
    # ctypes.windll exists only in the Windows stubs.
    if sys.platform == "win32":
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            working_set = int(counters.WorkingSetSize)
    return working_set


def peak_rss_bytes() -> int | None:
    """Highest resident memory of this process so far, or None where it cannot be read."""
    peak: int | None = None
    if sys.platform != "win32":
        import resource

        # ru_maxrss is in bytes on macOS and in kilobytes elsewhere.
        unit = 1 if sys.platform == "darwin" else 1024
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit
    return peak


def session_id_or_default(value: str | None) -> str:
    """The client's session id if it is well formed, else ``DEFAULT_SESSION``."""
    value = (value or "").strip()
    return value if _SESSION_ID.fullmatch(value) else DEFAULT_SESSION


class _Sized(Protocol):
    @property
    def nbytes(self) -> int: ...


V = TypeVar("V", bound=_Sized)


@dataclass
class _Slot(Generic[V]):
    value: V
    nbytes: int
    session: str
    pinned: bool
    last_used: int
    expires_at: float | None


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

    def register(self, cache: BudgetedLRU[Any]) -> None:
        """Count ``cache`` against this budget."""
        with self.lock:
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
        with self.lock:
            return sum(cache.nbytes for cache in self.caches.values())

    def admits(self, nbytes: int) -> bool:
        """Whether an unpinned entry of ``nbytes`` is small enough to be cached at all."""
        return nbytes <= ADMIT_FRACTION * self.limit_bytes

    def touch(self, session: str) -> None:
        """Record a request from ``session``, which becomes the active session."""
        with self.lock:
            self.sessions[session] = self.clock()
            self.active_session = session

    def _evictable(self, slot: _Slot[Any]) -> bool:
        return not (slot.pinned and slot.session == self.active_session)

    def make_room(self, nbytes: int, keep: str | None = None) -> None:
        """Evict entries until ``nbytes`` more fit: unpinned ones first, then other sessions' pins."""
        with self.lock:
            used = self.used_bytes
            while used + nbytes > self.limit_bytes:
                victims = [
                    (slot.pinned, slot.last_used, cache, token)
                    for cache in self.caches.values()
                    for token, slot in cache.slots.items()
                    if token != keep and self._evictable(slot)
                ]
                if not victims:
                    return  # what is left belongs to the active session; it may exceed the budget
                _, _, cache, token = min(victims, key=lambda v: (v[0], v[1]))
                evicted = cache.slots.pop(token)
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
        with self.lock:
            for cache in self.caches.values():
                cache.release_session(session)
            self.sessions.pop(session, None)
            if self.active_session == session:
                self.active_session = None

    def sweep(self) -> None:
        """Drop expired entries and close idle sessions other than the active one."""
        with self.lock:
            now = self.clock()
            for cache in self.caches.values():
                cache.drop_expired(now)
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
        with self.lock:
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
        """Forget every entry and session."""
        with self.lock:
            for cache in self.caches.values():
                cache.slots.clear()
            self.sessions.clear()
            self.active_session = None

    def usage(self) -> dict[str, Any]:
        """Budget, per-cache and per-session totals, for the debug endpoint."""
        with self.lock:
            now = self.clock()
            slots = [slot for cache in self.caches.values() for slot in cache.slots.values()]
            return {
                "limit_bytes": self.limit_bytes,
                "used_bytes": sum(slot.nbytes for slot in slots),
                "pinned_bytes": sum(slot.nbytes for slot in slots if slot.pinned),
                "active_session": self.active_session,
                "caches": {
                    name: {"entries": len(cache.slots), "bytes": cache.nbytes}
                    for name, cache in self.caches.items()
                },
                "sessions": [
                    {
                        "id": session,
                        "active": session == self.active_session,
                        "idle_sec": round(now - seen, 1),
                        "entries": sum(1 for slot in slots if slot.session == session),
                        "bytes": sum(slot.nbytes for slot in slots if slot.session == session),
                    }
                    for session, seen in self.sessions.items()
                ],
            }


class BudgetedLRU(Generic[V]):
    """Token-keyed cache whose entries count against a shared ``MemoryBudget``."""

    def __init__(
        self,
        name: str,
        budget: MemoryBudget,
        *,
        per_session: int | None = None,
        ttl_sec: float | None = None,
    ) -> None:
        self.name = name
        self.budget = budget
        self.per_session = per_session
        self.ttl_sec = ttl_sec
        self.slots: dict[str, _Slot[V]] = {}
        budget.register(self)

    @property
    def nbytes(self) -> int:
        """Bytes held by this cache."""
        return sum(slot.nbytes for slot in self.slots.values())

    def pin(self, value: V, session: str = DEFAULT_SESSION) -> str:
        """Store session data that is never evicted while ``session`` is active, and return its token."""
        token = self._insert(value, session, pinned=True)
        assert token is not None
        return token

    def offer(self, value: V, session: str = DEFAULT_SESSION) -> str | None:
        """Store a derived entry that may be evicted any time; None when it is too large to cache."""
        return self._insert(value, session, pinned=False)

    def _insert(self, value: V, session: str, *, pinned: bool) -> str | None:
        nbytes = int(value.nbytes)
        with self.budget.lock:
            self.budget.touch(session)
            if self.per_session is not None:
                own = [t for t, slot in self.slots.items() if slot.session == session]
                for token in own[: max(len(own) - self.per_session + 1, 0)]:
                    del self.slots[token]
            if not pinned and not self.budget.admits(nbytes):
                return None
            self.budget.make_room(nbytes)
            token = uuid.uuid4().hex
            self.slots[token] = _Slot(
                value=value,
                nbytes=nbytes,
                session=session,
                pinned=pinned,
                last_used=self.budget.next_tick(),
                expires_at=None if self.ttl_sec is None else self.budget.clock() + self.ttl_sec,
            )
        return token

    def _live_slot(self, token: str | None) -> _Slot[V] | None:
        if not token:
            return None
        slot = self.slots.get(token)
        if slot is None:
            return None
        if slot.expires_at is not None and slot.expires_at <= self.budget.clock():
            del self.slots[token]
            return None
        return slot

    def get(self, token: str | None) -> V | None:
        """The value for ``token``, marked as just used, or None."""
        with self.budget.lock:
            slot = self._live_slot(token)
            if slot is None:
                return None
            slot.last_used = self.budget.next_tick()
            if slot.session in self.budget.sessions:
                self.budget.sessions[slot.session] = self.budget.clock()
            return slot.value

    def pop(self, token: str | None) -> V | None:
        """Remove and return the value for ``token``, or None."""
        with self.budget.lock:
            slot = self._live_slot(token)
            if slot is None or token is None:
                return None
            del self.slots[token]
            return slot.value

    def discard(self, token: str | None) -> None:
        """Remove the entry for ``token`` if there is one."""
        with self.budget.lock:
            if token:
                self.slots.pop(token, None)

    def resize(self, token: str) -> None:
        """Recount the entry for ``token`` after its value grew, evicting others to fit."""
        with self.budget.lock:
            slot = self.slots.get(token)
            if slot is None:
                return
            nbytes = int(slot.value.nbytes)
            self.budget.make_room(nbytes - slot.nbytes, keep=token)
            slot.nbytes = nbytes

    def release_session(self, session: str) -> None:
        """Drop the entries ``session`` holds in this cache."""
        with self.budget.lock:
            for token in [t for t, slot in self.slots.items() if slot.session == session]:
                del self.slots[token]

    def drop_expired(self, now: float) -> None:
        """Remove the entries whose time to live has passed."""
        with self.budget.lock:
            expired = [
                token
                for token, slot in self.slots.items()
                if slot.expires_at is not None and slot.expires_at <= now
            ]
            for token in expired:
                del self.slots[token]
