"""Application entrypoint / composition root.

Builds the FastAPI app, owns the database lifecycle, registers middleware,
exception handlers and routers. This is the only module that knows how all the
layers fit together (composition root pattern).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.routes import calendar, documents, meta, notifications, resources
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.database import Database
from app.domain.registry import all_tables
from app.storage.s3_storage import S3FileStorage

logger = get_logger("curcle.main")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(settings)
        database.connect()
        if settings.auto_create_tables:
            database.ensure_tables([*all_tables(), "documents"])
        app.state.database = database

        if settings.has_storage:
            app.state.storage = S3FileStorage(settings)
            logger.info("Object storage configured (bucket=%s).", settings.b2_bucket)
        else:
            app.state.storage = None
            logger.warning("B2 storage not configured — document endpoints will return 502.")

        logger.info("%s v%s started.", settings.app_name, settings.app_version)
        try:
            yield
        finally:
            database.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Backend for the Optiminastic × Circle HR Operating System — PostgreSQL source of truth.",
        lifespan=lifespan,
    )

    # Compress large list payloads — JSONB resource lists shrink ~5-10x.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(meta.router)
    # Documents/notifications routers must precede the generic resources router
    # so their literal paths win over "/api/{resource}".
    app.include_router(documents.router)
    app.include_router(notifications.router)
    app.include_router(calendar.router)
    app.include_router(resources.router)
    return app


app = create_app()
