"""Cascade delete for a candidate.

Deleting a candidate must also remove every record that hangs off them —
interviews, schedules, test invites/results, doc requests, BGV, onboarding, the
handoff-feed entry, and uploaded documents (metadata + S3 blob). Otherwise those
orphaned rows keep surfacing (e.g. a deleted candidate still showing under the
dashboard's "Upcoming Interviews", which is derived from the interviews table).

This dedicated DELETE is registered before the generic resources router so it
wins for `/api/candidates/{id}`; all other candidate methods still fall through
to the generic router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.api.dependencies import get_repository, get_storage, require_user
from app.core.logging import get_logger
from app.repositories.base import DocumentRepository
from app.storage.base import FileStorage

# Deleting a candidate is destructive + cascades — require a dashboard session.
router = APIRouter(prefix="/api/candidates", tags=["candidates"], dependencies=[Depends(require_user)])

logger = get_logger("curcle.candidate_delete")

# Tables whose rows carry a `candidateId` field — found by that field, then
# deleted by their own primary key (`id`).
_CHILD_BY_FIELD = (
    "schedules",
    "test_invites",
    "doc_requests",
    "interviews",
    "iq_tests",
    "assignments",
)
# Tables keyed directly by candidateId — deleted by the candidate id itself.
_CHILD_BY_KEY = ("bgvs", "onboarding", "candidate_handoffs")


@router.delete("/{candidate_id}", status_code=204, response_class=Response)
def delete_candidate(
    candidate_id: str,
    repo: DocumentRepository = Depends(get_repository),
    storage: FileStorage = Depends(get_storage),
) -> Response:
    removed = 0
    # Pipeline records that reference the candidate by a candidateId field.
    for table in _CHILD_BY_FIELD:
        for rec in repo.find(table, {"candidateId": candidate_id}):
            rid = rec.get("id")
            if rid and repo.delete(table, rid):
                removed += 1
    # Records keyed directly by candidateId (no-op when absent).
    for table in _CHILD_BY_KEY:
        if repo.delete(table, candidate_id):
            removed += 1
    # Uploaded documents — remove the S3 blob (best-effort) then the metadata row.
    for doc in repo.find("documents", {"entityType": "candidate", "entityId": candidate_id}):
        key = doc.get("storageKey")
        if key:
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001 - a missing blob must not block the delete
                logger.warning("Could not delete blob for document %s", doc.get("id"))
        if doc.get("id"):
            repo.delete("documents", doc["id"])
            removed += 1
    # Finally the candidate itself.
    repo.delete("candidates", candidate_id)
    logger.info("Cascade-deleted candidate %s (+%d related records).", candidate_id, removed)
    return Response(status_code=204)
