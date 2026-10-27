"""Shared request helpers and serialization for API routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from fastapi import Header

from muedit.api import config
from muedit.api.cache import BUDGET
from muedit.api.memory import DEFAULT_SESSION, parse_session_id
from muedit.errors import MueditError, NotFoundError
from muedit.io.bids import dataset_root


def success_payload(data: Any) -> dict[str, Any]:
    """Wrap JSON success responses in the canonical v1 envelope."""
    return {"data": data, "meta": {"api_version": "v1"}}


def request_session(x_muedit_session: str | None = Header(None)) -> str:
    """The calling tab's session id; a request that sends one makes it the active session."""
    if x_muedit_session is None:
        return DEFAULT_SESSION
    session = parse_session_id(x_muedit_session, "X-MUedit-Session")
    BUDGET.touch(session)
    return session


def bids_root_for(project: str | None) -> Path:
    """``resolve_bids_root``, with a project name that is not a folder name as a 400."""
    try:
        return config.resolve_bids_root(project)
    except ValueError as exc:
        raise MueditError(str(exc), "project") from exc


def dataset_root_of(file_path: str) -> Path | None:
    """The BIDS dataset a file sits in, else the default project folder above it, else ``None``."""
    if not file_path:
        return None
    path = Path(file_path).expanduser().resolve()
    root = dataset_root(path)
    if root is None:
        root = next((p for p in path.parents if p.name.lower() == config.DEFAULT_PROJECT), None)
    return root


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


def require_existing_path(path: str, field: str = "path") -> Path:
    """Return ``path`` as a Path, raising ``NotFoundError`` when nothing exists there."""
    resolved = Path(path)
    if not resolved.exists():
        raise NotFoundError(f"File not found: {path}", field)
    return resolved


def missing_upload() -> MueditError:
    """The error for an upload token the server no longer holds."""
    return MueditError(
        "Missing or expired upload; request /api/v1/preview-by-path first", "upload_token"
    )


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
