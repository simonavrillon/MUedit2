"""Project listing endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from muedit.api import config
from muedit.api.common import success_payload

router = APIRouter(prefix="/api/v1")


@router.get("/projects")
def projects() -> dict[str, Any]:
    """The projects in the output folder, for the Project field's dropdown."""
    return success_payload({"projects": config.list_projects()})
