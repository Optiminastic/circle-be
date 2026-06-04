"""Document storage endpoints (resumes & important files).

Metadata lives in the `documents` Postgres table (via the repository); the blob
lives in Backblaze B2. Uploads are proxied; downloads are short-lived presigned
URLs so the bucket stays private.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.api.dependencies import get_repository, get_storage
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationError
from app.repositories.base import DocumentRepository
from app.storage.base import FileStorage

router = APIRouter(prefix="/api/documents", tags=["documents"])

TABLE = "documents"

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_name(name: str) -> str:
    return _SAFE.sub("_", name).strip("_")[:120] or "file"


def _get_or_404(repo: DocumentRepository, doc_id: str) -> dict[str, Any]:
    doc = repo.get(TABLE, doc_id)
    if doc is None:
        raise NotFoundError(f"document '{doc_id}' not found")
    return doc


@router.get("")
def list_documents(
    entityType: str | None = None,
    entityId: str | None = None,
    repo: DocumentRepository = Depends(get_repository),
) -> list[dict[str, Any]]:
    match: dict[str, Any] = {}
    if entityType:
        match["entityType"] = entityType
    if entityId:
        match["entityId"] = entityId
    # Indexed JSONB containment (GIN) instead of loading the whole table.
    items = repo.find(TABLE, match) if match else repo.list(TABLE)
    return sorted(items, key=lambda d: d.get("uploadedAt", ""), reverse=True)


@router.post("", status_code=201)
async def upload_document(
    entityType: str = Form(...),
    entityId: str = Form(...),
    category: str = Form("document"),
    file: UploadFile = File(...),
    repo: DocumentRepository = Depends(get_repository),
    storage: FileStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    data = await file.read()
    if not data:
        raise ValidationError("Empty file.")
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise ValidationError(f"File exceeds the {settings.max_upload_mb} MB limit.")

    doc_id = uuid.uuid4().hex[:12]
    filename = file.filename or "file"
    key = f"documents/{entityType}/{entityId}/{doc_id}_{_safe_name(filename)}"
    storage.put(key, data, file.content_type or "application/octet-stream")

    meta = {
        "id": doc_id,
        "entityType": entityType,
        "entityId": entityId,
        "category": category,
        "fileName": filename,
        "contentType": file.content_type,
        "size": len(data),
        "storageKey": key,
        "uploadedAt": datetime.now(timezone.utc).isoformat(),
    }
    repo.upsert(TABLE, doc_id, meta)
    return meta


@router.get("/{doc_id}/url")
def get_download_url(
    doc_id: str,
    repo: DocumentRepository = Depends(get_repository),
    storage: FileStorage = Depends(get_storage),
) -> dict[str, Any]:
    doc = _get_or_404(repo, doc_id)
    return {"url": storage.presigned_url(doc["storageKey"]), "fileName": doc.get("fileName")}


@router.delete("/{doc_id}", status_code=204, response_class=Response)
def delete_document(
    doc_id: str,
    repo: DocumentRepository = Depends(get_repository),
    storage: FileStorage = Depends(get_storage),
) -> Response:
    doc = _get_or_404(repo, doc_id)
    storage.delete(doc["storageKey"])
    repo.delete(TABLE, doc_id)
    return Response(status_code=204)
