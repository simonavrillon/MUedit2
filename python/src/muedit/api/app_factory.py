"""FastAPI app construction for MUedit."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from muedit.api.cache import BUDGET
from muedit.api.errors import register_exception_handlers
from muedit.io.store import purge_stale_sessions


@asynccontextmanager
async def _sweep_caches(_app: FastAPI) -> AsyncIterator[None]:
    """Sweep expired entries and idle sessions while the app runs; own no session stores after."""
    purge_stale_sessions()
    BUDGET.start_sweeper()
    try:
        yield
    finally:
        BUDGET.stop_sweeper()
        BUDGET.clear()


def create_app(
    title: str = "MUedit API",
    version: str = "2.1.0",
    allowed_origins: Sequence[str] = (),
    allowed_hosts: Sequence[str] | None = None,
) -> FastAPI:
    """Create the FastAPI app with origin/host restrictions and canonical exception handlers."""
    app = FastAPI(title=title, version=version, lifespan=_sweep_caches)
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    if allowed_hosts is not None:
        # DNS rebinding makes a hostile page same-origin, so CORS cannot stop it; the Host header can.
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    register_exception_handlers(app)
    return app
