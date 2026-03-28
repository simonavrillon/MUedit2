"""Shared exception payload and handlers for MUedit API."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def error_payload(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    """Build canonical API error envelope."""
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if detail is not None:
        payload["error"]["detail"] = detail
    return payload


async def http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Translate FastAPI HTTPException into project error envelope."""
    if not isinstance(exc, HTTPException):
        return await unhandled_exception_handler(_, exc)
    detail = exc.detail
    message = detail if isinstance(detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(
            code=f"http_{exc.status_code}",
            message=message,
            detail=detail if not isinstance(detail, str) else None,
        ),
    )


async def validation_exception_handler(
    _: Request, exc: Exception
) -> JSONResponse:
    """Translate request model validation failures into project envelope."""
    if not isinstance(exc, RequestValidationError):
        return await unhandled_exception_handler(_, exc)
    return JSONResponse(
        status_code=422,
        content=error_payload(
            code="validation_error",
            message="Request validation failed",
            detail=exc.errors(),
        ),
    )


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Catch-all error handler to avoid leaking internal tracebacks to clients."""
    return JSONResponse(
        status_code=500,
        content=error_payload(
            code="internal_error",
            message="Internal server error",
            detail={"type": exc.__class__.__name__},
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all API exception handlers on the FastAPI app instance."""
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
