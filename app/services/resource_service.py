"""Resource service — business rules for the generic CRUD lifecycle.

Depends on the DocumentRepository abstraction (DIP). Owns id generation and
not-found semantics so routers stay thin (SRP).
"""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError
from app.domain.registry import ResourceDef
from app.repositories.base import Document, DocumentRepository


class ResourceService:
    def __init__(self, repository: DocumentRepository) -> None:
        self._repo = repository

    def list(self, resource: ResourceDef) -> list[Document]:
        return self._repo.list(resource.table)

    def get(self, resource: ResourceDef, item_id: str) -> Document:
        item = self._repo.get(resource.table, item_id)
        if item is None:
            raise NotFoundError(f"{resource.slug} '{item_id}' not found")
        return item

    def create(self, resource: ResourceDef, payload: Document) -> Document:
        item_id = str(payload.get(resource.id_field) or uuid.uuid4().hex[:8])
        payload[resource.id_field] = item_id
        return self._repo.upsert(resource.table, item_id, payload)

    def replace(self, resource: ResourceDef, item_id: str, payload: Document) -> Document:
        payload[resource.id_field] = item_id
        return self._repo.upsert(resource.table, item_id, payload)

    def patch(self, resource: ResourceDef, item_id: str, changes: Document) -> Document:
        existing = self.get(resource, item_id)
        existing.update(changes)
        return self._repo.upsert(resource.table, item_id, existing)

    def delete(self, resource: ResourceDef, item_id: str) -> None:
        if not self._repo.delete(resource.table, item_id):
            raise NotFoundError(f"{resource.slug} '{item_id}' not found")
