"""``.npz`` archives: uncompressed, memory-mappable writing, and pickle-free reading."""

from __future__ import annotations

import io
import os
import pickle
import struct
import time
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Any

import numpy as np
from numpy.lib import format as npy_format
from numpy.typing import DTypeLike

#: Byte boundary each member's array data starts on, so a memory map of it is aligned.
DATA_ALIGN = 64
#: Largest block of an array converted and written at once.
WRITE_BLOCK_BYTES = 32 * 1024 * 1024
_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_ZIP64_EXTRA_BYTES = 20
# Extra-field id the Android ``zipalign`` tool uses for alignment padding.
_PAD_EXTRA_ID = 0xD935

# Every global a MUedit ``.npz`` pickle references: arrays, dtypes and numpy scalars.
_LEGACY_GLOBALS: dict[tuple[str, str], tuple[str, str]] = {
    ("numpy", "ndarray"): ("numpy", "ndarray"),
    ("numpy", "dtype"): ("numpy", "dtype"),
    ("numpy._core.multiarray", "_reconstruct"): ("numpy._core.multiarray", "_reconstruct"),
    ("numpy.core.multiarray", "_reconstruct"): ("numpy._core.multiarray", "_reconstruct"),
    ("numpy._core.multiarray", "scalar"): ("numpy._core.multiarray", "scalar"),
    ("numpy.core.multiarray", "scalar"): ("numpy._core.multiarray", "scalar"),
}


class LegacyPickleError(pickle.UnpicklingError):
    """A legacy ``.npz`` pickle asks for something other than arrays, lists, dicts and numbers."""


class _LegacyUnpickler(pickle.Unpickler):
    """Rebuilds ndarrays, dtypes, numpy scalars and plain Python values, and nothing else."""

    def find_class(self, module: str, name: str) -> Any:
        target = _LEGACY_GLOBALS.get((module, name))
        if target is None:
            raise LegacyPickleError(
                f"refusing to load {module}.{name}: MUedit files only hold arrays, "
                "lists, dicts, strings and numbers"
            )
        return super().find_class(*target)


@dataclass(frozen=True)
class ArrayInfo:
    """What a member's ``.npy`` header says."""

    shape: tuple[int, ...]
    fortran_order: bool
    dtype: np.dtype[Any]

    @property
    def stored_shape(self) -> tuple[int, ...]:
        """Shape of the bytes read as a C-order array (the array itself, or its transpose)."""
        return self.shape[::-1] if self.fortran_order else self.shape


def _read_info(fp: IO[bytes]) -> ArrayInfo:
    version = npy_format.read_magic(fp)
    if version == (1, 0):
        shape, fortran, dtype = npy_format.read_array_header_1_0(fp)
    elif version == (2, 0):
        shape, fortran, dtype = npy_format.read_array_header_2_0(fp)
    else:
        raise ValueError(f"unsupported .npy format version {version}")
    return ArrayInfo(tuple(int(s) for s in shape), bool(fortran), np.dtype(dtype))


def _read_exact(fp: IO[bytes], out: np.ndarray) -> None:
    view = memoryview(out.reshape(-1)).cast("B")
    filled = 0
    while filled < len(view):
        n = fp.readinto(view[filled:])  # type: ignore[attr-defined]
        if not n:
            raise ValueError("truncated array data")
        filled += n


def _read_body(fp: IO[bytes], info: ArrayInfo) -> np.ndarray:
    """The array whose header ``fp`` just passed."""
    if info.dtype.hasobject:
        arr = _LegacyUnpickler(fp, encoding="ASCII").load()
        if not isinstance(arr, np.ndarray):
            raise ValueError("object member does not hold an array")
        return arr
    stored = np.empty(info.stored_shape, dtype=info.dtype)
    _read_exact(fp, stored)
    return stored.T if info.fortran_order else stored


class NpzArchive:
    """Read-only access to a ``.npz`` file, opened once; object arrays never run pickled code."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._zip = zipfile.ZipFile(self.path)
        self._members = {
            name[: -len(".npy")]: name for name in self._zip.namelist() if name.endswith(".npy")
        }

    def __enter__(self) -> NpzArchive:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._zip.close()

    @property
    def files(self) -> list[str]:
        return list(self._members)

    def __contains__(self, key: object) -> bool:
        return key in self._members

    def info(self, key: str) -> ArrayInfo:
        with self._zip.open(self._members[key]) as fp:
            return _read_info(fp)

    def get(self, key: str) -> np.ndarray | None:
        """The array stored under ``key``, or None when the archive has no such member."""
        if key not in self._members:
            return None
        with self._zip.open(self._members[key]) as fp:
            return _read_body(fp, _read_info(fp))

    def row_blocks(self, key: str, max_bytes: int) -> Iterator[tuple[int, np.ndarray]]:
        """``(first_row, rows)`` blocks of a 1-D or 2-D member's ``stored_shape`` array, in order."""
        with self._zip.open(self._members[key]) as fp:
            info = _read_info(fp)
            if info.dtype.hasobject or len(info.shape) not in (1, 2):
                raise ValueError(f"{key}: not a 1-D or 2-D numeric array")
            n_rows, n_cols = info.stored_shape if len(info.shape) == 2 else (1, info.shape[0])
            step = max(1, max_bytes // max(1, n_cols * info.dtype.itemsize))
            for start in range(0, n_rows, step):
                block = np.empty((min(step, n_rows - start), n_cols), dtype=info.dtype)
                _read_exact(fp, block)
                yield start, block

    def memmap(self, key: str) -> np.ndarray | None:
        """A read-only memory map of an uncompressed member; None if it is compressed or pickled."""
        zinfo = self._zip.getinfo(self._members[key])
        if zinfo.compress_type != zipfile.ZIP_STORED:
            return None
        with open(self.path, "rb") as fp:
            fp.seek(zinfo.header_offset)
            fields = _LOCAL_HEADER.unpack(fp.read(_LOCAL_HEADER.size))
            name_len, extra_len = fields[-2], fields[-1]
            fp.seek(zinfo.header_offset + _LOCAL_HEADER.size + name_len + extra_len)
            info = _read_info(fp)
            data_offset = fp.tell()
        if info.dtype.hasobject:
            return None
        if 0 in info.shape:
            return np.empty(info.shape, dtype=info.dtype)
        return np.memmap(
            self.path,
            dtype=info.dtype,
            mode="r",
            offset=data_offset,
            shape=info.shape,
            order="F" if info.fortran_order else "C",
        )


def _npy_header(shape: tuple[int, ...], dtype: np.dtype[Any]) -> bytes:
    buf = io.BytesIO()
    header = {"descr": npy_format.dtype_to_descr(dtype), "fortran_order": False, "shape": shape}
    npy_format.write_array_header_1_0(buf, header)
    return buf.getvalue()


def _pad_extra(offset: int) -> bytes:
    """An extra field that moves data starting ``offset`` bytes into the file to ``DATA_ALIGN``."""
    pad = -offset % DATA_ALIGN
    if pad == 0:
        return b""
    if pad < 4:  # an extra field needs a 4-byte header
        pad += DATA_ALIGN
    return struct.pack("<HH", _PAD_EXTRA_ID, pad - 4) + bytes(pad - 4)


def _blocks(arr: np.ndarray, dtype: np.dtype[Any]) -> Iterator[np.ndarray]:
    """``arr`` as C-order ``dtype`` pieces of at most ``WRITE_BLOCK_BYTES``, in order."""
    if arr.ndim == 0 or arr.size == 0:
        yield np.ascontiguousarray(arr, dtype=dtype)
        return
    if arr.flags.c_contiguous:
        flat = arr.reshape(-1)
        step = max(1, WRITE_BLOCK_BYTES // max(dtype.itemsize, arr.itemsize))
        for start in range(0, flat.size, step):
            yield np.ascontiguousarray(flat[start : start + step], dtype=dtype)
        return
    row_bytes = arr[0].size * max(dtype.itemsize, arr.itemsize)
    step = max(1, WRITE_BLOCK_BYTES // max(1, row_bytes))
    for start in range(0, arr.shape[0], step):
        yield np.ascontiguousarray(arr[start : start + step], dtype=dtype)


class NpzWriter:
    """Writes an uncompressed ``.npz`` (``np.load`` reads it) with each array ``DATA_ALIGN``-aligned."""

    def __init__(self, path: str | Path) -> None:
        path = os.fspath(path)
        if not path.endswith(".npz"):
            path += ".npz"
        self.path = Path(path)
        # Written beside the destination and moved over it, so a failed save keeps the old file.
        self._tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex[:8]}.tmp")
        self._zip = zipfile.ZipFile(self._tmp, "w", zipfile.ZIP_STORED, allowZip64=True)
        self._names: set[str] = set()

    def __enter__(self) -> NpzWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._zip.close()
        if exc_type is None:
            os.replace(self._tmp, self.path)
        else:
            self._tmp.unlink(missing_ok=True)

    def add(self, name: str, arr: Any, dtype: DTypeLike | None = None) -> None:
        """Write ``arr`` (converted to ``dtype`` block by block) as ``<name>.npy``."""
        if name in self._names:
            raise ValueError(f"duplicate member {name!r}")
        arr = arr if isinstance(arr, np.ndarray) else np.asarray(arr)
        dt = np.dtype(dtype) if dtype is not None else arr.dtype
        if dt.hasobject:
            raise TypeError(f"{name}: object arrays are not written (they need pickle)")
        header = _npy_header(tuple(arr.shape), dt)
        zinfo = zipfile.ZipInfo(f"{name}.npy", date_time=time.localtime()[:6])
        zinfo.compress_type = zipfile.ZIP_STORED
        zinfo.file_size = len(header) + int(arr.size) * dt.itemsize
        fixed = _LOCAL_HEADER.size + len(zinfo.filename.encode()) + _ZIP64_EXTRA_BYTES
        zinfo.extra = _pad_extra(self._zip.start_dir + fixed)
        with self._zip.open(zinfo, "w", force_zip64=True) as out:
            out.write(header)
            for block in _blocks(arr, dt):
                if block.size:
                    out.write(memoryview(block.reshape(-1)).cast("B"))
                del block  # else it stays alive while the next block is converted
        self._names.add(name)
