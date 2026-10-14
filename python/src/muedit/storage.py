"""T1 session store: full-length arrays of an open file as memory-mapped ``.npy`` files."""

from __future__ import annotations

import contextlib
import json
import logging
import mmap
import os
import shutil
import sys
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import DTypeLike

from muedit.models import FloatArray, SignalImport
from muedit.paths import CACHE_DIR_ENV as CACHE_DIR_ENV
from muedit.paths import cache_dir

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
DISK_RESERVE_ENV = "MUEDIT_DISK_RESERVE_MB"
#: Free disk a store leaves untouched; an array that would cut into it is kept in RAM instead.
DISK_RESERVE_BYTES = 1024 * MIB
#: Size of the float64 working block a loader converts before writing it into the store.
BLOCK_BYTES = 32 * MIB
_OWNER_FILE = "owner.json"


def sessions_dir() -> Path:
    """Folder holding one sub-folder per open store."""
    return cache_dir() / "sessions"


def disk_reserve_bytes() -> int:
    """``DISK_RESERVE_BYTES``, or ``MUEDIT_DISK_RESERVE_MB`` when it is set."""
    override = os.environ.get(DISK_RESERVE_ENV, "").strip()
    return int(float(override) * MIB) if override else DISK_RESERVE_BYTES


def sample_blocks(n_samples: int, n_rows: int, itemsize: int = 8) -> Iterator[tuple[int, int]]:
    """``[start, stop)`` sample ranges whose ``n_rows`` rows fit in ``BLOCK_BYTES``."""
    step = max(1, BLOCK_BYTES // max(1, n_rows * itemsize))
    for start in range(0, n_samples, step):
        yield start, min(start + step, n_samples)


class ArrayStore(Protocol):
    """Where a loader or a decomposition run puts its full-length arrays."""

    def allocate(
        self, name: str, shape: tuple[int, ...], dtype: DTypeLike, *, zero: bool = False
    ) -> np.ndarray: ...

    def seal(self, arr: np.ndarray) -> np.ndarray: ...

    def discard(self, arr: np.ndarray) -> None: ...


class RamStore:
    """Arrays on the heap: what loaders and runs use when no session store is given."""

    def allocate(
        self, name: str, shape: tuple[int, ...], dtype: DTypeLike, *, zero: bool = False
    ) -> np.ndarray:
        """A new heap array; ``name`` is ignored."""
        return np.zeros(shape, dtype) if zero else np.empty(shape, dtype)

    def seal(self, arr: np.ndarray) -> np.ndarray:
        """``arr`` itself: heap arrays stay writable, as loaders always returned them."""
        return arr

    def discard(self, arr: np.ndarray) -> None:
        """Nothing to delete on the heap."""


class SessionStore:
    """A folder of ``.npy`` files under ``sessions_dir()``, deleted by ``close``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._warned_full = False
        self._lock = threading.Lock()
        self._holds = 0
        self._close_pending = False

    @classmethod
    def create(cls, label: str = "session") -> SessionStore:
        """A new, empty store folder owned by this process."""
        root = sessions_dir()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{label}-{uuid.uuid4().hex[:12]}"
        path.mkdir()
        (path / _OWNER_FILE).write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        return cls(path)

    def _new_file(self, name: str) -> Path:
        """``<name>.npy``, or a suffixed name if that file exists (a live map must not be truncated)."""
        path = self.path / f"{name}.npy"
        if path.exists():
            path = self.path / f"{name}-{uuid.uuid4().hex[:8]}.npy"
        return path

    def _fits_on_disk(self, nbytes: int) -> bool:
        try:
            free = shutil.disk_usage(self.path).free
        except OSError:
            return False
        if free - nbytes >= disk_reserve_bytes():
            return True
        if not self._warned_full:
            logger.warning(
                "Keeping arrays in RAM: %.0f MB free in %s, %.0f MB needed plus a %.0f MB reserve",
                free / MIB,
                self.path,
                nbytes / MIB,
                disk_reserve_bytes() / MIB,
            )
            self._warned_full = True
        return False

    def allocate(
        self, name: str, shape: tuple[int, ...], dtype: DTypeLike, *, zero: bool = False
    ) -> np.ndarray:
        """A writable array backed by a new ``<name>.npy`` (zero-filled), or on the heap if the disk is full."""
        dt = np.dtype(dtype)
        dims = tuple(int(s) for s in shape)
        nbytes = int(np.prod(dims, dtype=np.int64)) * dt.itemsize
        if nbytes == 0 or not self._fits_on_disk(nbytes):
            return RamStore().allocate(name, dims, dt, zero=zero)
        return np.lib.format.open_memmap(self._new_file(name), mode="w+", dtype=dt, shape=dims)

    def seal(self, arr: np.ndarray) -> np.ndarray:
        """Flush a stored array and reopen it read-only; a heap fallback becomes a read-only view."""
        if isinstance(arr, np.memmap) and arr.filename:
            arr.flush()
            return np.load(arr.filename, mmap_mode="r")
        view = arr.view()
        view.flags.writeable = False
        return view

    def discard(self, arr: np.ndarray) -> None:
        """Delete the file behind ``arr`` if it is one of this store's; live maps keep their pages.

        Windows cannot delete a mapped file: it goes when the store closes.
        """
        filename = getattr(arr, "filename", None)
        if not filename or Path(filename).resolve().parent != self.path.resolve():
            return
        with contextlib.suppress(OSError):
            Path(filename).unlink(missing_ok=True)

    def hold(self) -> None:
        """Keep the folder while another process reads it: ``close`` waits for ``release``."""
        with self._lock:
            self._holds += 1

    def release(self) -> None:
        """End one ``hold``, and do a ``close`` that was asked for meanwhile."""
        with self._lock:
            self._holds -= 1
            close = self._holds == 0 and self._close_pending
        if close:
            self.close()

    def close(self) -> None:
        """Delete the folder, or once the last ``hold`` is released.

        What Windows still maps is removed by the next ``purge_stale_sessions``.
        """
        with self._lock:
            if self._holds:
                self._close_pending = True
                return
            self._close_pending = False
        shutil.rmtree(self.path, ignore_errors=True)

    @property
    def disk_bytes(self) -> int:
        """Bytes the store's files take on disk."""
        return _folder_bytes(self.path)


def copy_into(store: ArrayStore, name: str, arr: FloatArray) -> FloatArray:
    """``arr`` as float32 in ``store``, copied block by block."""
    out = store.allocate(name, arr.shape, np.float32)
    for start, stop in sample_blocks(arr.shape[1], arr.shape[0], arr.dtype.itemsize):
        out[:, start:stop] = arr[:, start:stop]
    return store.seal(out)


def store_signal(signal: SignalImport, store: ArrayStore) -> SignalImport:
    """``signal`` with its EMG and auxiliary arrays copied into ``store`` as float32."""
    return replace(
        signal,
        data=copy_into(store, "emg", signal.data),
        auxiliary=copy_into(store, "aux", signal.auxiliary),
    )


def _open_mapped(
    filename: str, dtype: np.dtype[Any], shape: tuple[int, ...], offset: int
) -> np.ndarray:
    """Read-only map of the array stored ``offset`` bytes into ``filename``."""
    return np.memmap(filename, dtype=dtype, mode="r", offset=offset, shape=shape)


def _file_offset(arr: np.memmap) -> int | None:
    """Where ``arr``'s first element sits in its file, or None if it cannot be reopened there."""
    mapping = getattr(arr, "_mmap", None)
    if mapping is None or not arr.filename or arr.size == 0 or not arr.flags.c_contiguous:
        return None
    if not os.path.exists(arr.filename):
        return None
    try:
        mapped_at = np.frombuffer(mapping, dtype=np.uint8).ctypes.data
    except (ValueError, TypeError):  # the map is closed
        return None
    # np.memmap maps its file from the allocation boundary at or below its offset.
    mapped_from = arr.offset - arr.offset % mmap.ALLOCATIONGRANULARITY
    return int(mapped_from + arr.ctypes.data - mapped_at)


def _reduce_memmap(arr: np.memmap) -> tuple[Callable[..., np.ndarray], tuple[Any, ...]]:
    """Pickle a file-backed array as its file location; others (and views) by value."""
    offset = _file_offset(arr)
    if offset is None:
        return cast(tuple[Callable[..., np.ndarray], tuple[Any, ...]], np.asarray(arr).__reduce__())
    return _open_mapped, (arr.filename, arr.dtype, arr.shape, offset)


# The decomposition worker process gets its input and returns its pulse trains as files.
ForkingPickler.register(np.memmap, _reduce_memmap)


def _folder_bytes(path: Path) -> int:
    total = 0
    with contextlib.suppress(OSError):
        for entry in path.iterdir():
            with contextlib.suppress(OSError):
                total += entry.stat().st_size
    return total


def _pid_alive(pid: int) -> bool:
    """Whether a process with this id is running."""
    alive = False
    if sys.platform == "win32":
        import ctypes

        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            alive = bool(ok) and code.value == still_active
    else:
        try:
            os.kill(pid, 0)
            alive = True
        except PermissionError:
            alive = True
        except OSError:
            alive = False
    return alive


def _owner_pid(folder: Path) -> int | None:
    try:
        return int(json.loads((folder / _OWNER_FILE).read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def purge_stale_sessions() -> None:
    """Delete store folders whose owning process has exited (or that name no owner)."""
    root = sessions_dir()
    if not root.is_dir():
        return
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        pid = _owner_pid(folder)
        if pid is None or not _pid_alive(pid):
            logger.info("Removing stale session store %s", folder)
            shutil.rmtree(folder, ignore_errors=True)


def store_usage() -> dict[str, Any]:
    """Disk used by this process's stores and the free space left, for the debug endpoint."""
    root = sessions_dir()
    folders = [f for f in (root.iterdir() if root.is_dir() else []) if _owner_pid(f) == os.getpid()]
    stores = [{"name": f.name, "bytes": _folder_bytes(f)} for f in folders]
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free: int | None = None
    with contextlib.suppress(OSError):
        free = shutil.disk_usage(probe).free
    return {
        "path": str(root),
        "bytes": sum(s["bytes"] for s in stores),
        "free_bytes": free,
        "reserve_bytes": disk_reserve_bytes(),
        "stores": stores,
    }
