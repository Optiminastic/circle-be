"""Meta endpoints: root info and health (incl. DB connectivity)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies import get_database
from app.core.config import Settings, get_settings
from app.db.database import Database
from app.domain.registry import RESOURCES

router = APIRouter(tags=["meta"])


@router.get("/")
def root(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "resources": list(RESOURCES.keys()),
        "docs": "/docs",
    }


@router.get("/api/health")
def health(database: Database = Depends(get_database)) -> dict[str, Any]:
    db_ok = database.healthcheck()
    return {"status": "ok" if db_ok else "degraded", "database": "up" if db_ok else "down"}
