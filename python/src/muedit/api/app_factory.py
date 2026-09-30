"""FastAPI app construction for MUedit."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from muedit.api.cache import BUDGET
from muedit.api.errors import register_exception_handlers
from muedit.api.services.decompose_service import stop_decompositions
from muedit.editing.edit_log import purge_old_logs
from muedit.io.store import purge_stale_sessions


@asynccontextmanager
async def _sweep_caches(_app: FastAPI) -> AsyncIterator[None]:
    """Sweep expired entries and idle sessions while the app runs; own no run or session store after."""
    purge_stale_sessions()
    purge_old_logs()
    BUDGET.start_sweeper()
    try:
        yield
    finally:
        stop_decompositions()
        BUDGET.stop_sweeper()
        BUDGET.clear()


class _RevalidatedFiles(StaticFiles):
    """Static files the browser checks again on every load, so an update is never stale."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    title: str = "MUedit API",
    version: str = "2.1.0",
    allowed_hosts: Sequence[str] | None = None,
) -> FastAPI:
    """Create the FastAPI app with the host check and canonical error handlers."""
    app = FastAPI(title=title, version=version, lifespan=_sweep_caches)
    if allowed_hosts is not None:
        # DNS rebinding makes a hostile page same-origin, so CORS cannot stop it; the Host header can.
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    register_exception_handlers(app)
    return app


def mount_frontend(app: FastAPI, folder: Path) -> None:
    """Serve the frontend at ``/``; call after the API routers, which it would otherwise shadow."""
    app.mount("/", _RevalidatedFiles(directory=folder, html=True), name="frontend")
