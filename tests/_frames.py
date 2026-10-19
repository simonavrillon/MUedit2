"""Reading MUB1 frames back, as the frontend does, to check what the API sends."""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from muedit.api.binary import _ALIGN, _PREFIX, _WIRE_DTYPES, FRAME_MAGIC, _aligned


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
