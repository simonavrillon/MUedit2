"""Project listing endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from muedit.api import config
from muedit.api.common import success_payload

router = APIRouter(prefix="/api/v1")


@router.get("/projects")
def projects() -> dict[str, Any]:
    """The projects in the output folder and its path, for the settings panel's project tree."""
    return success_payload({"projects": config.list_projects(), "data_root": str(config.DATA_ROOT)})
