"""Router registration for MUedit API."""

from fastapi import FastAPI

from muedit.api.routes.decompose import router as decompose_router
from muedit.api.routes.dialog import router as dialog_router
from muedit.api.routes.editing import router as editing_router
from muedit.api.routes.preview import router as preview_router


def include_routers(app: FastAPI) -> None:
    """Attach all v1 routers to the app in a single place."""

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(preview_router)
    app.include_router(decompose_router)
    app.include_router(editing_router)
    app.include_router(dialog_router)
