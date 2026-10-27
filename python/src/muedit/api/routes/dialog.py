"""Native OS dialog endpoints."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from muedit.api.common import success_payload
from muedit.api.services.dialog_service import choose_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dialog")


@router.get("/open-file")
def open_file_dialog() -> dict[str, Any]:
    """Return the selected file path and basename from a native open dialog."""
    try:
        path = choose_file()
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="File dialog timed out") from None
    except (OSError, RuntimeError) as exc:
        logger.exception("Native file dialog failed")
        raise HTTPException(
            status_code=500, detail="Could not open the file dialog; see the server log"
        ) from exc
    if not path:
        return success_payload({"path": None, "name": None})
    return success_payload({"path": path, "name": Path(path).name})
