"""Binary payload encoders for API transport-layer wire formats."""

from __future__ import annotations

import json
import struct
from typing import Any

import numpy as np

from muedit.api.common import make_json_safe


def pack_json_f32_payload(
    magic: bytes, meta: dict[str, Any], *arrays: np.ndarray, dtype: str = "<f4"
) -> bytes:
    """Pack versioned binary payload: magic(4) + v1 + JSON-meta + arrays in ``dtype``."""
    meta_bytes = json.dumps(make_json_safe(meta), separators=(",", ":")).encode("utf-8")
    parts: list[bytes] = [magic, struct.pack("<I", 1), struct.pack("<I", len(meta_bytes))]
    for arr in arrays:
        parts += [struct.pack("<I", arr.shape[0]), struct.pack("<I", arr.shape[1])]
    parts.append(meta_bytes)
    for arr in arrays:
        parts.append(arr.astype(dtype, copy=False).tobytes(order="C"))
    return b"".join(parts)
