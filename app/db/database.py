"""Database access boundary.

Wraps the SQLAlchemy engine so the rest of the app depends on a small, stable
surface (connect, session, healthcheck, ensure_tables) instead of SQLAlchemy
internals. Engine creation is lazy and fault-tolerant: a missing/unreachable
database does not crash the process — endpoints degrade to 503 instead.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import RepositoryError
from app.core.logging import get_logger

logger = get_logger("curcle.db")


_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS "{table}" (
    id         TEXT PRIMARY KEY,
    data       JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# Indexes that keep reads cheap as tables grow:
#  - created_at: the default list ordering (avoids a full sort).
#  - GIN(jsonb_path_ops): indexed JSONB containment (`data @> '{...}'`) so
#    filtered lookups (e.g. documents by entityId) don't scan every row.
_INDEX_DDL = (
    'CREATE INDEX IF NOT EXISTS "ix_{table}_created_at" ON "{table}" (created_at)',
    'CREATE INDEX IF NOT EXISTS "ix_{table}_data_gin" ON "{table}" USING GIN (data jsonb_path_ops)',
)


class Database:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    def connect(self) -> None:
        if not self._settings.has_database:
            logger.warning("DATABASE_URL is not set — the API will start but data endpoints return 503.")
            return
        try:
            self._engine = create_engine(
                self._settings.sqlalchemy_url,
                pool_pre_ping=True,
                pool_size=self._settings.db_pool_size,
                max_overflow=self._settings.db_max_overflow,
                pool_timeout=self._settings.db_pool_timeout,
                future=True,
            )
            self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False, future=True)
            logger.info("Database engine initialized.")
        except SQLAlchemyError as exc:  # pragma: no cover - defensive
            logger.exception("Failed to initialize database engine: %s", exc)
            self._engine = None
            self._session_factory = None

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()

    @property
    def is_ready(self) -> bool:
        return self._session_factory is not None

    def session(self) -> Session:
        if self._session_factory is None:
            raise RepositoryError("Database is not configured. Set DATABASE_URL and restart.")
        return self._session_factory()

    def healthcheck(self) -> bool:
        if self._engine is None:
            return False
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError as exc:
            logger.error("Healthcheck failed: %s", exc)
            return False

    def ensure_tables(self, tables: list[str]) -> None:
        if self._engine is None:
            return
        try:
            with self._engine.begin() as conn:
                for table in tables:
                    conn.execute(text(_TABLE_DDL.format(table=table)))
                    for index_ddl in _INDEX_DDL:
                        conn.execute(text(index_ddl.format(table=table)))
            logger.info("Ensured %d resource tables (with indexes) exist.", len(tables))
        except SQLAlchemyError as exc:
            logger.exception("Failed to ensure tables: %s", exc)
