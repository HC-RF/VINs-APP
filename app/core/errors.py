"""Uniform API error shape.

Every failure the client can see is ``{"error": {"code", "message", ...}}``
with a stable, machine-readable ``code``.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, message: str, *, code: str = "ERROR", status_code: int = 400,
                 details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}

    def to_response(self) -> JSONResponse:
        body: dict = {"error": {"code": self.code, "message": self.message}}
        if self.details:
            body["error"]["details"] = self.details
        return JSONResponse(status_code=self.status_code, content=body)


class InvalidInput(ApiError):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message, code="INVALID_INPUT", status_code=400, details=details)


class NotFound(ApiError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="NOT_FOUND", status_code=404)


class TooManyItems(ApiError):
    def __init__(self, limit: int, given: int) -> None:
        super().__init__(
            f"Too many VINs in one request: {given} supplied, limit is {limit}. "
            f"Split the list into smaller batches.",
            code="TOO_MANY_ITEMS",
            status_code=413,
            details={"limit": limit, "given": given},
        )


_HTTP_CODES = {
    400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE", 429: "RATE_LIMITED",
    500: "INTERNAL_ERROR", 502: "UPSTREAM_ERROR", 503: "SERVICE_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return exc.to_response()

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(p) for p in err.get("loc", []) if p != "body"),
                "message": err.get("msg", "Invalid value."),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "The request body did not validate.",
                    "details": {"fields": fields},
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": _HTTP_CODES.get(exc.status_code, "HTTP_ERROR"),
                    "message": str(exc.detail),
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        # Internal details stay in the log, not in the response body.
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred. The incident has been logged.",
                }
            },
        )
