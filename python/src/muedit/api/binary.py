"""MUB1 binary frame: JSON metadata plus typed arrays, for every array transfer (memory plan §5)."""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from muedit.api.common import make_json_safe

FRAME_MAGIC = b"MUB1"
FRAME_MEDIA_TYPE = "application/x-muedit-frame"
FRAME_FORMAT = "mub1"  # x-muedit-format header value

# Offsets are 8-byte aligned so the client reads every dtype as a zero-copy typed-array view.
_ALIGN = 8
_PREFIX = struct.Struct("<4sI")  # magic, header_len
_WIRE_DTYPES = frozenset({"f4", "i4", "i8", "u1", "i2"})


def _aligned(n: int) -> int:
    return n + (-n % _ALIGN)


def pack_frame(meta: Mapping[str, Any], arrays: Mapping[str, tuple[ArrayLike, str]]) -> memoryview:
    """Pack ``meta`` and ``{name: (array, wire dtype)}`` into one preallocated MUB1 buffer."""
    sources: list[tuple[np.ndarray, np.dtype[Any], int]] = []
    specs: list[dict[str, Any]] = []
    data_len = 0
    for name, (value, code) in arrays.items():
        if code not in _WIRE_DTYPES:
            raise ValueError(f"unsupported wire dtype {code!r} for {name!r}")
        source = np.asarray(value)
        dtype = np.dtype("<" + code)
        specs.append({"name": name, "dtype": code, "shape": list(source.shape), "offset": data_len})
        sources.append((source, dtype, data_len))
        data_len = _aligned(data_len + source.size * dtype.itemsize)

    header = json.dumps(
        {"meta": make_json_safe(dict(meta)), "arrays": specs}, separators=(",", ":")
    ).encode("utf-8")
    data_start = _aligned(_PREFIX.size + len(header))
    buf = bytearray(data_start + data_len)
    _PREFIX.pack_into(buf, 0, FRAME_MAGIC, len(header))
    buf[_PREFIX.size : _PREFIX.size + len(header)] = header
    for source, dtype, offset in sources:
        target = np.frombuffer(buf, dtype=dtype, count=source.size, offset=data_start + offset)
        np.copyto(target.reshape(source.shape), source, casting="same_kind")
    return memoryview(buf)


def unpack_frame(
    body: bytes | bytearray | memoryview,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Return ``(meta, arrays)`` from a MUB1 frame; arrays are views into ``body``."""
    view = memoryview(body)
    if len(view) < _PREFIX.size:
        raise ValueError("frame shorter than its prefix")
    magic, header_len = _PREFIX.unpack_from(view, 0)
    if magic != FRAME_MAGIC:
        raise ValueError("not a MUB1 frame")
    header_end = _PREFIX.size + header_len
    if header_end > len(view):
        raise ValueError("frame header runs past the end of the body")
    try:
        header = json.loads(bytes(view[_PREFIX.size : header_end]))
        meta = header.get("meta", {})
        specs = header.get("arrays", [])
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        raise ValueError("frame header is not a JSON object") from exc
    if not isinstance(meta, dict) or not isinstance(specs, list):
        raise ValueError("frame header needs a meta object and an arrays list")

    data_start = _aligned(header_end)
    arrays: dict[str, np.ndarray] = {}
    for spec in specs:
        try:
            name, code, shape, offset = spec["name"], spec["dtype"], spec["shape"], spec["offset"]
            shape = tuple(int(n) for n in shape)
            offset = int(offset)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed array entry {spec!r}") from exc
        if code not in _WIRE_DTYPES:
            raise ValueError(f"unsupported wire dtype {code!r} for {name!r}")
        if offset < 0 or offset % _ALIGN or any(n < 0 for n in shape):
            raise ValueError(f"bad offset or shape for {name!r}")
        dtype = np.dtype("<" + code)
        count = int(np.prod(shape, dtype=np.int64))
        if data_start + offset + count * dtype.itemsize > len(view):
            raise ValueError(f"array {name!r} runs past the end of the body")
        arrays[str(name)] = np.frombuffer(
            view, dtype=dtype, count=count, offset=data_start + offset
        ).reshape(shape)
    return meta, arrays
