"""Shared request parsing and serialization helpers for API routes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException, UploadFile

from muedit.api.cache import UPLOAD_CHUNK_SIZE
from muedit.decomp.types import DecompositionParameters


def parse_json(raw: str | None, field_name: str) -> Any:
    """Parse JSON form field and raise HTTP 400 with field context on failure."""
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "field": field_name,
                "reason": "Invalid JSON",
                "message": str(exc),
            },
        ) from exc


def parse_discard_channels(raw: str | None) -> list[list[int]] | None:
    """Parse discard channel overrides from JSON list[list[int]]."""
    parsed = parse_json(raw, "discard_channels")
    if parsed is None:
        return None
    if not isinstance(parsed, list):
        raise HTTPException(
            status_code=400,
            detail={"field": "discard_channels", "reason": "Expected list of lists"},
        )
    result: list[list[int]] = []
    for grid_idx, grid in enumerate(parsed):
        if not isinstance(grid, list):
            raise HTTPException(
                status_code=400,
                detail={
                    "field": "discard_channels",
                    "reason": f"Grid {grid_idx} must be a list",
                },
            )
        try:
            result.append([int(x) for x in grid])
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "field": "discard_channels",
                    "reason": f"Grid {grid_idx} contains non-integer values",
                },
            ) from exc
    return result


def parse_rois(raw: str | None) -> list[tuple[int, int]] | None:
    """Parse ROI payload into list of (start, end) sample tuples."""
    parsed = parse_json(raw, "rois")
    if parsed is None:
        return None
    if not isinstance(parsed, list):
        raise HTTPException(
            status_code=400,
            detail={"field": "rois", "reason": "Expected list of [start, end] pairs"},
        )

    result: list[tuple[int, int]] = []
    for idx, item in enumerate(parsed):
        if isinstance(item, (list, tuple)) and len(item) == 2:
            start_raw, end_raw = item
        elif isinstance(item, dict) and "start" in item and "end" in item:
            start_raw, end_raw = item["start"], item["end"]
        else:
            raise HTTPException(
                status_code=400,
                detail={
                    "field": "rois",
                    "reason": f"ROI {idx} must be [start, end] or object with start/end",
                },
            )
        try:
            result.append((int(start_raw), int(end_raw)))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail={"field": "rois", "reason": f"ROI {idx} has non-integer bounds"},
            ) from exc
    return result


def parse_json_object(raw: str | None, field_name: str) -> dict | None:
    """Parse and validate a JSON object form field."""
    parsed = parse_json(raw, field_name)
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=400,
            detail={"field": field_name, "reason": "Expected JSON object"},
        )
    return parsed


def safe_unlink(path: str) -> None:
    """Best-effort file removal without propagating OS errors."""
    try:
        os.unlink(path)
    except OSError:
        pass


def _coerce_param_value(current: Any, value: Any) -> Any:
    """Coerce a JSON override value to match the type of the existing param.

    ``bool`` is handled explicitly because ``bool("false")`` is ``True`` in Python;
    string values are interpreted case-insensitively.
    """
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)
    target_type = type(current)
    return target_type(value)


def build_params(raw: str | None) -> DecompositionParameters:
    """Build decomposition parameters from optional JSON override payload."""
    base = DecompositionParameters()
    if not raw:
        return base

    data = parse_json(raw, "params")
    if data is None:
        return base
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400,
            detail={"field": "params", "reason": "Expected JSON object"},
        )
    for key, value in data.items():
        if hasattr(base, key) and value is not None:
            current = getattr(base, key)
            try:
                setattr(base, key, _coerce_param_value(current, value))
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "field": key,
                        "reason": f"Cannot coerce {value!r} to {type(current).__name__}",
                    },
                ) from exc

    if base.adapt_batch_ms <= 0:
        raise HTTPException(
            status_code=400,
            detail={
                "field": "adapt_batch_ms",
                "reason": "adapt_batch_ms must be > 0",
            },
        )
    if base.use_adaptive and base.adapt_batch_ms > base.edges_sec * 1000:
        raise HTTPException(
            status_code=400,
            detail={
                "field": "adapt_batch_ms",
                "reason": (
                    f"adapt_batch_ms must be <= {base.edges_sec * 1000:.0f} "
                    f"(edges_sec * 1000) when use_adaptive is enabled"
                ),
            },
        )
    return base


def make_json_safe(value: Any) -> Any:
    """Recursively convert numpy scalars/arrays into JSON-serializable values."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()

    if isinstance(value, dict):
        return {k: make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(v) for v in value]
    return value


async def save_upload_to_temp(file: UploadFile) -> str:
    """Persist uploaded file to a temporary path and return that path."""
    extension = os.path.splitext(file.filename or "")[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
        while True:
            chunk = await file.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            tmp.write(chunk)
    return tmp.name



def parse_entity_label(file_label: str) -> str:
    """Derive BIDS entity label stem from decomposition filename."""
    if not file_label:
        raise ValueError("file_label is required to locate BIDS EMG")
    stem = Path(file_label).stem
    if "_grid-" in stem:
        stem = stem.split("_grid-")[0]
    for suffix in ("_decomp", "_edited"):
        while stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem


def serialize_preview(result: dict[str, Any]) -> dict[str, Any]:
    """Extract and normalize preview payload from decomposition result object."""
    preview = result.get("preview", {})
    distime = preview.get("distime", [])
    return {
        "mean_abs": preview.get("mean_abs", []),
        "pulse_trains": preview.get("pulse_trains", []),
        "pulse_trains_full": preview.get("pulse_trains_full", []),
        "distime": [list(map(int, d)) for d in distime],
        "distime_all": [list(map(int, d)) for d in preview.get("distime_all", distime)],
        "pulse_trains_all": preview.get("pulse_trains_all", []),
        "mu_grid_index": preview.get("mu_grid_index", []),
        "fsamp": preview.get("fsamp"),
        "grid_mean_abs": preview.get("grid_mean_abs", []),
        "grid_names": preview.get("grid_names", []),
        "total_samples": preview.get("total_samples"),
        "channel_means": preview.get("channel_means", []),
        "coordinates": preview.get("coordinates", []),
        "metadata": preview.get("metadata", {}),
        "muscle": preview.get("muscle", []),
        "auxiliary": preview.get("auxiliary"),
        "auxiliary_names": preview.get("auxiliaryname", []),
    }


def summarize_result(
    result: dict[str, Any], save_path: str, persisted: bool
) -> dict[str, Any]:
    """Build compact decomposition summary for frontend progress/result panels."""
    signal = result.get("signal", {})
    pulse_t = signal.get("PulseT")
    pulse_len = (
        int(pulse_t.shape[1]) if hasattr(pulse_t, "shape") and pulse_t.size > 0 else 0
    )
    mu_count = len(signal.get("Dischargetimes", []))

    return {
        "fsamp": signal.get("fsamp"),
        "grid_names": result.get("grid_names", []),
        "mu_count": mu_count,
        "pulse_length": pulse_len,
        "sil": result.get("sil", []),
        "discard_channels": result.get("discard_channels"),
        "save_path": save_path if persisted else None,
        "parameters": result.get("parameters"),
    }
