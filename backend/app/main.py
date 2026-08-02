"""FastAPI application factory for Lextract.

Run with::

    uvicorn app.main:app --reload

Interactive docs are at http://localhost:8000/docs.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import engine, dispose_engine
from app.dependencies import DBSession
from app.routers import bills, evaluation, extraction, zoho
from app.services.llm_clients import configured_models
from app.schemas import ErrorResponse, HealthResponse

logger = logging.getLogger(__name__)

DESCRIPTION = """\
**Lextract** extracts structured information from handwritten receipts using
multiple vision language models, and benchmarks their performance against
human-verified ground truth.

**Typical flow**

1. `POST /api/bills/upload` -- upload a photo of a receipt
2. `POST /api/extract/{bill_id}` -- run the configured vision models over it
3. `POST /api/ground-truth/{bill_id}` -- record what the receipt actually says
4. `POST /api/evaluate/{bill_id}` -- score each model field by field
5. `GET  /api/evaluation/report` -- accuracy, latency and cost leaderboard
6. `POST /api/zoho/expenses` -- push the winning extraction into Zoho Books
"""

def _safe_dsn(dsn: str) -> str:
    """Render a DSN for logs with the password removed.

    Connection strings end up in log aggregators; the host is the diagnostic
    value, the password is a liability.
    """
    return re.sub(r"://([^:@/]+):[^@]*@", r"://\1:***@", dsn)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s",
    )
    # httpx logs every outbound request at INFO, which drowns our own output.
    logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Verify the database on boot and close the pool on shutdown.

    Failing to reach Postgres is logged as a loud warning rather than an
    exception: a developer who has not run ``createdb`` yet should still get a
    server they can open ``/docs`` on, with an error that says exactly what to
    fix.
    """
    settings = get_settings()
    _configure_logging(settings.log_level)

    logger.info("Providers configured: %s", ", ".join(settings.configured_providers) or "NONE")
    for slug in settings.configured_providers:
        logger.info("  %-10s -> %s", slug, configured_models(settings)[slug])
    logger.info("Zoho Books configured: %s", settings.zoho_configured)
    logger.info("Upload directory: %s", settings.upload_dir)

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        logger.info("Database connection OK.")
    except Exception as exc:  # noqa: BLE001
        # Tailor the advice: "run createdb" is useless inside a container, and
        # a localhost DSN in a deployed environment nearly always means the
        # platform's database variable never reached the process.
        # Name the host being dialled. Whether the variable is "set" is far less
        # useful than where it actually points — a value of "localhost" and an
        # unset variable produce the same failure but have different fixes.
        target = _safe_dsn(settings.database_url)
        env_set = bool(os.getenv("DATABASE_URL"))

        if "localhost" in settings.database_url or "127.0.0.1" in settings.database_url:
            hint = (
                "That is a LOCALHOST address — there is no database inside this "
                "container. "
                + (
                    "DATABASE_URL is set, but to a local address; replace it with "
                    "the database service's INTERNAL host."
                    if env_set
                    else "DATABASE_URL is unset, so the local-development default "
                    "was used."
                )
                + " On Railway: add a PostgreSQL service, then set "
                "DATABASE_URL=${{Postgres.DATABASE_URL}} (the service name must "
                "match exactly), or paste the postgres.railway.internal value."
            )
        elif env_set:
            hint = (
                "The host resolved but refused the connection. Check the database "
                "service is running, is in the SAME project/environment, and that "
                "you used its internal host rather than the public proxy."
            )
        else:
            hint = "Run: createdb lextract && alembic upgrade head"

        logger.warning("Could not reach the database at %s (%s). %s", target, exc, hint)

    yield
    await dispose_engine()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Build and configure the ASGI application."""
    settings = get_settings()

    app = FastAPI(
        title="Lextract API",
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Vite serves on 5173; the extra origins cover CRA and 127.0.0.1 variants.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(bills.router)
    app.include_router(extraction.router)
    app.include_router(evaluation.router)
    app.include_router(zoho.router)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Return field-level validation errors the frontend can render."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ErrorResponse(
                detail="Request validation failed.",
                error_type="validation_error",
                context={"errors": exc.errors()},
            ).model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """Last-resort handler: log the traceback, return a safe message.

        The exception text is deliberately not echoed to the client -- an
        unhandled error can carry a connection string or an API key in its
        message.
        """
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                detail="Internal server error. Check the backend logs for the traceback.",
                error_type=type(exc).__name__,
            ).model_dump(mode="json"),
        )

    @app.get("/api/health", response_model=HealthResponse, tags=["system"])
    async def health(session: DBSession) -> HealthResponse:
        """Liveness probe plus a summary of what is actually configured."""
        try:
            await session.execute(text("SELECT 1"))
            database = "connected"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Health check database probe failed: %s", exc)
            database = "unavailable"

        return HealthResponse(
            status="ok" if database == "connected" else "degraded",
            database=database,
            configured_providers=settings.configured_providers,
            # Only report models for providers that actually have a key, so
            # the UI never offers a provider it cannot call.
            models={
                slug: model
                for slug, model in configured_models(settings).items()
                if slug in settings.configured_providers
            },
            zoho_configured=settings.zoho_configured,
        )

    @app.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        """Friendly landing payload pointing at the docs."""
        return {
            "name": "Lextract API",
            "version": "1.0.0",
            "docs": "/docs",
            "health": "/api/health",
        }

    return app


app = create_app()
