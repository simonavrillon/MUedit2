"""FastAPI app construction for MUedit."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from muedit.api.errors import register_exception_handlers


def create_app(title: str = "MUedit API", version: str = "2.0") -> FastAPI:
    """Create FastAPI app with CORS and canonical exception handlers."""
    app = FastAPI(title=title, version=version)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    return app
