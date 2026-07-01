"""Meta endpoints: root info and health (incl. DB connectivity)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies import get_database
from app.db.database import Database

router = APIRouter(tags=["meta"])


def _status(database: Database) -> dict[str, Any]:
    """Minimal liveness/DB response — no app internals (resource list) exposed."""
    db_ok = database.healthcheck()
    return {"status": "ok" if db_ok else "degraded", "database": "up" if db_ok else "down"}


@router.get("/")
def root(database: Database = Depends(get_database)) -> dict[str, Any]:
    return _status(database)


@router.get("/api/health")
def health(database: Database = Depends(get_database)) -> dict[str, Any]:
    return _status(database)
