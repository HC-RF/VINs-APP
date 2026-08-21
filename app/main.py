"""FastAPI application factory.

Serves the JSON API under ``/api/v1`` and the single-page frontend from
``app/static``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import router as v1_router
from app.config import STATIC_DIR, Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.rate_limit import RateLimitMiddleware, SlidingWindowLimiter

log = logging.getLogger(__name__)

API_DESCRIPTION = """
Decode Vehicle Identification Numbers into verified vehicle profiles.

**Every field carries its provenance**: which source supplied it, how much that
source is trusted, when it was retrieved, and whether it was read directly out
of the VIN or enriched from an external database. When two sources disagree,
both values are returned and the record is flagged - no silent tie-breaking.

Free sources are used first. Commercial providers are called only when a
required field is still missing, or when `verify` is requested. Results are
cached by VIN so the same vehicle is never paid for twice.
"""


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        from app.db.base import init_db
        from app.providers.registry import close_registry, get_registry

        init_db(settings)
        registry = get_registry()
        available = [p.name for p in registry.available()]
        log.info("Providers available: %s", ", ".join(available) or "none")
        if not any(p.kind.value == "COMMERCIAL" for p in registry.available()):
            log.info("No commercial provider configured - running at zero API cost.")
        yield
        await close_registry()

    app = FastAPI(
        title=settings.app_name,
        description=API_DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.add_middleware(
        RateLimitMiddleware,
        limiter=SlidingWindowLimiter(
            settings.rate_limit_requests, settings.rate_limit_window_seconds
        ),
        enabled=settings.rate_limit_enabled,
        exempt_paths=("/api/v1/health",),
    )

    register_error_handlers(app)
    app.include_router(v1_router)

    # --- Frontend -----------------------------------------------------------
    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/favicon.svg", include_in_schema=False)
        async def favicon() -> FileResponse:
            return FileResponse(STATIC_DIR / "favicon.svg")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=_settings.host,
        port=_settings.port,
        reload=not _settings.is_production,
    )
