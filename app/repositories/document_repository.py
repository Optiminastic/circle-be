"""PostgreSQL JSONB implementation of DocumentRepository.

Each resource is a table of (id, data JSONB). Storing the nested HR documents as
JSONB keeps the flexible shape of the frontend models while remaining queryable
and indexable in Postgres. All DB errors are normalized to RepositoryError so
callers never see raw driver exceptions (error tolerance).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import RepositoryError
from app.core.logging import get_logger
from app.repositories.base import Document

logger = get_logger("curcle.repository")


def _as_dict(value: Any) -> Document:
    # psycopg returns JSONB as dict, but be defensive across drivers.
    if isinstance(value, str):
        return json.loads(value)
    return value


class SqlAlchemyDocumentRepository:
    """DocumentRepository backed by SQLAlchemy + PostgreSQL JSONB."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, table: str) -> list[Document]:
        try:
            rows = self._session.execute(
                text(f'SELECT data FROM "{table}" ORDER BY created_at ASC')
            ).fetchall()
            return [_as_dict(row[0]) for row in rows]
        except SQLAlchemyError as exc:
            raise RepositoryError(f"Failed to list '{table}'") from exc

    def get(self, table: str, item_id: str) -> Document | None:
        try:
            row = self._session.execute(
                text(f'SELECT data FROM "{table}" WHERE id = :id'), {"id": item_id}
            ).fetchone()
            return _as_dict(row[0]) if row else None
        except SQLAlchemyError as exc:
            raise RepositoryError(f"Failed to read '{table}/{item_id}'") from exc

    def upsert(self, table: str, item_id: str, data: Document) -> Document:
        try:
            self._session.execute(
                text(
                    f'INSERT INTO "{table}" (id, data) VALUES (:id, CAST(:data AS JSONB)) '
                    "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()"
                ),
                {"id": item_id, "data": json.dumps(data)},
            )
            self._session.commit()
            return data
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise RepositoryError(f"Failed to save '{table}/{item_id}'") from exc

    def delete(self, table: str, item_id: str) -> bool:
        try:
            result = self._session.execute(
                text(f'DELETE FROM "{table}" WHERE id = :id'), {"id": item_id}
            )
            self._session.commit()
            return result.rowcount > 0
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise RepositoryError(f"Failed to delete '{table}/{item_id}'") from exc
