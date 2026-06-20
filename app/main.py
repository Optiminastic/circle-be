"""Application entrypoint / composition root.

Builds the FastAPI app, owns the database lifecycle, registers middleware,
exception handlers and routers. This is the only module that knows how all the
layers fit together (composition root pattern).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import calendar, doc_requests, documents, meta, notifications, public, resources
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import SlidingWindowRateLimiter, client_ip, is_exempt_origin
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
            database.ensure_tables([*all_tables(), "documents", "email_otps"])
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

    # --- Anti-spam: per-IP rate limit on the PUBLIC, unauthenticated writes ----
    # The careers form lets anyone create a candidate + upload a resume; throttle
    # those by client IP so the openings can't be spammed. Registered BEFORE CORS
    # below so the CORS middleware (added last = outermost) still attaches headers
    # to the 429, letting the browser read the message. HR + local origins exempt.
    public_write_limiter = SlidingWindowRateLimiter(
        [
            (settings.public_rate_limit_per_minute, 60.0),
            (settings.public_rate_limit_per_hour, 3600.0),
        ]
    )
    public_write_paths = {"/api/public/apply", "/api/candidates", "/api/documents"}
    # The OTP / email-check endpoints get their own (looser) per-IP budget — a
    # single applicant makes a few calls (check + request + verify [+ resend]).
    # This caps an IP from spamming codes to many different emails; the per-EMAIL
    # cap (public.py) caps bombing one address. Both hold for ANY caller — website,
    # server action, or a replayed cURL — so abuse can't bypass it via the client.
    otp_limiter = SlidingWindowRateLimiter(
        [
            (settings.otp_rate_limit_per_minute, 60.0),
            (settings.otp_rate_limit_per_hour, 3600.0),
        ]
    )
    otp_paths = {
        "/api/public/otp/request",
        "/api/public/otp/verify",
        "/api/public/check-applied",
    }

    @app.middleware("http")
    async def rate_limit_public_writes(request: Request, call_next):
        path = request.url.path
        if settings.rate_limit_enabled and request.method == "POST":
            limiter = (
                public_write_limiter
                if path in public_write_paths
                else otp_limiter
                if path in otp_paths
                else None
            )
            if (
                limiter is not None
                and not is_exempt_origin(request.headers.get("origin", ""), settings)
                and not limiter.allow(client_ip(request))
            ):
                logger.warning("Rate limit hit: ip=%s path=%s", client_ip(request), path)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please wait a minute and try again."},
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)

    # Compress large list payloads — JSONB resource lists shrink ~5-10x.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Also allow localhost and any private-LAN origin (any port), so the app
        # works when opened from a phone/other device on the same network. The
        # matched origin is echoed back (never "*"), so allow_credentials is safe.
        allow_origin_regex=(
            r"https?://(localhost|127\.0\.0\.1|"
            r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"192\.168\.\d{1,3}\.\d{1,3}|"
            r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(meta.router)
    # Documents/notifications routers must precede the generic resources router
    # so their literal paths win over "/api/{resource}".
    app.include_router(documents.router)
    app.include_router(doc_requests.router)
    app.include_router(notifications.router)
    app.include_router(calendar.router)
    # Hardened public router must precede the generic resources router so its
    # literal /api/public/* paths win over "/api/{resource}".
    app.include_router(public.router)
    app.include_router(resources.router)
    return app


app = create_app()


if __name__ == "__main__":
    # Production entrypoint (e.g. Render): bind 0.0.0.0 on the platform-provided
    # $PORT so the host can detect the open port. Locally falls back to 8000.
    import os

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104 - must bind all interfaces for the platform health check
        port=int(os.environ.get("PORT", "8000")),
    )
