"""API response contracts for MUedit."""

from __future__ import annotations

from typing import Any


def success_payload(data: Any, *, api_version: str = "v1") -> dict[str, Any]:
    """Wrap JSON success responses in the canonical v1 envelope."""
    return {
        "data": data,
        "meta": {
            "api_version": api_version,
        },
    }
