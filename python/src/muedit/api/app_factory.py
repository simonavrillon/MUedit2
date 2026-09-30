"""FastAPI app construction for MUedit."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from muedit.api.cache import BUDGET
from muedit.api.errors import error_payload, register_exception_handlers
from muedit.api.services.decompose_service import stop_decompositions
from muedit.editing.edit_log import purge_old_logs
from muedit.io.store import purge_stale_sessions

TOKEN_HEADER = "X-MUedit-Token"  # noqa: S105 (a header name)
#: The desktop launcher polls it before the page, which holds the token, has loaded.
OPEN_PATHS = frozenset({"/api/v1/health"})


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


class TokenMiddleware:
    """Refuse API requests that do not carry the app's token in ``X-MUedit-Token``."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and path.startswith("/api/") and path not in OPEN_PATHS:
            sent = dict(scope["headers"]).get(TOKEN_HEADER.lower().encode("ascii"), b"")
            if not secrets.compare_digest(sent, self._token):
                response = JSONResponse(
                    status_code=401,
                    content=error_payload("unauthorized", "Missing or wrong app token"),
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


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
    token: str | None = None,
) -> FastAPI:
    """Create the FastAPI app with host and desktop-token checks and canonical error handlers."""
    app = FastAPI(title=title, version=version, lifespan=_sweep_caches)
    if token is not None:
        app.add_middleware(TokenMiddleware, token=token)
    if allowed_hosts is not None:
        # DNS rebinding makes a hostile page same-origin, so CORS cannot stop it; the Host header can.
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    register_exception_handlers(app)
    return app


def mount_frontend(app: FastAPI, folder: Path) -> None:
    """Serve the frontend at ``/``; call after the API routers, which it would otherwise shadow."""
    app.mount("/", _RevalidatedFiles(directory=folder, html=True), name="frontend")
