"""FastAPI dependency providers — compose the object graph per request.

The concrete implementations are wired here only; routes and services depend on
abstractions. Sessions are opened per request and always closed (error tolerance).
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import StorageError
from app.db.database import Database
from app.repositories.base import DocumentRepository
from app.repositories.document_repository import SqlAlchemyDocumentRepository
from app.services.google_calendar import GoogleCalendarService
from app.services.resource_service import ResourceService
from app.storage.base import FileStorage


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_storage(request: Request) -> FileStorage:
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        raise StorageError("Document storage is not configured. Set the B2_* env vars and restart.")
    return storage


def get_session(database: Database = Depends(get_database)) -> Iterator[Session]:
    session = database.session()
    try:
        yield session
    finally:
        session.close()


def get_repository(session: Session = Depends(get_session)) -> DocumentRepository:
    return SqlAlchemyDocumentRepository(session)


def get_resource_service(repo: DocumentRepository = Depends(get_repository)) -> ResourceService:
    return ResourceService(repo)


def get_google_calendar_service(
    settings: Settings = Depends(get_settings),
) -> GoogleCalendarService:
    return GoogleCalendarService(settings)
