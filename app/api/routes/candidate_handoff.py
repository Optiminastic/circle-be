"""Onboarding candidate handoff — Curcle-hosted pull feed.

When a hired candidate arrives for their first office day, HR clicks "Mark
arrived" on the onboarding screen. That records the candidate in the
`candidate_handoffs` table. An EXTERNAL onboarding system then fetches a single
public, token-gated feed URL (``GET /api/candidate-feed/<token>``) to pull every
arrived candidate — a curated set of fields (name, id, email, phone, previous
company, current title, role hired) plus only the documents HR APPROVED during
verification, each as a short-lived presigned S3 URL.

The feed token is the secret embedded in the URL (settings.candidate_feed_token);
HR shares the full URL with the receiving team. The feed is per-IP rate limited
in main.py. Document links are short-lived, so the receiver should download on
fetch.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies import get_repository, get_storage
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.repositories.base import DocumentRepository
from app.storage.base import FileStorage

# HR-facing actions (authenticated app, like the rest of /api/*).
router = APIRouter(prefix="/api/candidate-handoffs", tags=["candidate-handoffs"])
# Public, token-gated pull feed for the external onboarding system.
feed_router = APIRouter(prefix="/api/candidate-feed", tags=["candidate-feed"])

logger = get_logger("curcle.candidate_handoff")

TABLE = "candidate_handoffs"
CANDIDATES_TABLE = "candidates"
ONBOARDING_TABLE = "onboarding"
EMPLOYEES_TABLE = "employees"
DOCS_TABLE = "documents"
DOC_REQUESTS_TABLE = "doc_requests"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_candidate(
    repo: DocumentRepository, candidate_id: str
) -> tuple[dict[str, Any] | None, str]:
    """The richest profile available for this id. Prefer the full candidate
    record; fall back to onboarding, employee, then a doc-request (which still
    carries name/email/role) since a hired candidate may have been converted
    (candidate row removed) by the time HR pushes them. Returns (record, source)."""
    cand = repo.get(CANDIDATES_TABLE, candidate_id)
    if cand is not None:
        return cand, "candidate"
    onb = repo.get(ONBOARDING_TABLE, candidate_id)
    if onb is not None:
        return onb, "onboarding"
    emp = repo.get(EMPLOYEES_TABLE, candidate_id)
    if emp is not None:
        return emp, "employee"
    dr = repo.find(DOC_REQUESTS_TABLE, {"candidateId": candidate_id})
    if dr:
        return dr[0], "doc-request"
    return None, ""


def _curated_candidate(rec: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    """The agreed handoff fields only — name, id, email, phone, previous company,
    current title, role hired, and department. Pulled from a candidate record,
    falling back to onboarding/employee/doc-request keys where the shapes differ."""
    g = rec.get
    return {
        "candidateId": candidate_id,
        "name": g("fullName") or g("candidateName") or "",
        "email": g("email") or g("candidateEmail") or "",
        "phone": g("phone") or "",
        "previousCompany": g("currentCompany") or "",
        "currentTitle": g("currentDesignation") or "",
        "roleHired": g("appliedRole") or g("role") or "",
        "department": g("department") or "",
    }


def _verified_documents(
    repo: DocumentRepository, storage: FileStorage | None, candidate_id: str
) -> list[dict[str, Any]]:
    """Only the documents the candidate uploaded that HR APPROVED during document
    verification (DocRequest submissions with status 'Verified'), each with a
    short-lived presigned S3 download URL. Deduped per doc type (latest wins)."""
    requests = repo.find(DOC_REQUESTS_TABLE, {"candidateId": candidate_id})
    best: dict[str, dict[str, Any]] = {}
    for req in requests:
        for sub in req.get("submissions") or []:
            if sub.get("status") != "Verified":
                continue
            key = sub.get("docType") or sub.get("documentId") or ""
            prev = best.get(key)
            stamp = sub.get("reviewedAt") or sub.get("uploadedAt") or ""
            prev_stamp = (prev or {}).get("reviewedAt") or (prev or {}).get("uploadedAt") or ""
            if prev is None or stamp >= prev_stamp:
                best[key] = sub

    out: list[dict[str, Any]] = []
    for sub in best.values():
        doc = repo.get(DOCS_TABLE, sub.get("documentId", "")) or {}
        download_url: str | None = None
        if storage is not None and doc.get("storageKey"):
            try:
                download_url = storage.presigned_url(
                    doc["storageKey"],
                    disposition=f'inline; filename="{doc.get("fileName") or "file"}"',
                    content_type=doc.get("contentType") or None,
                )
            except Exception:  # noqa: BLE001 - a single bad object must not 500 the feed
                logger.warning("Could not presign document %s for feed.", sub.get("documentId"))
        out.append(
            {
                "docType": sub.get("docType"),
                "fileName": sub.get("fileName") or doc.get("fileName"),
                "contentType": doc.get("contentType"),
                "size": sub.get("size") or doc.get("size"),
                "verifiedAt": sub.get("reviewedAt"),
                "downloadUrl": download_url,
            }
        )
    return out


# ── HR action: mark a candidate arrived (adds them to the feed) ───────────────


@router.post("/{candidate_id}/send")
def mark_arrived(
    candidate_id: str,
    repo: DocumentRepository = Depends(get_repository),
) -> dict[str, Any]:
    """Record that the candidate has arrived in office, so they appear in the
    public candidate feed the external onboarding system fetches."""
    candidate, _ = _resolve_candidate(repo, candidate_id)
    if candidate is None:
        raise NotFoundError("candidate not found")

    now = _now()
    existing = repo.get(TABLE, candidate_id) or {}
    name = (
        candidate.get("fullName")
        or candidate.get("candidateName")
        or existing.get("candidateName", "")
    )
    record = {
        **existing,
        "candidateId": candidate_id,
        "candidateName": name,
        # First mark = the in-office arrival; never overwritten on re-mark.
        "arrivedAt": existing.get("arrivedAt") or now,
        "updatedAt": now,
    }
    repo.upsert(TABLE, candidate_id, record)
    logger.info("Candidate %s marked arrived (added to feed).", candidate_id)
    return {"ok": True, "arrivedAt": record["arrivedAt"], "updatedAt": now}


# ── Public, token-gated pull feed ─────────────────────────────────────────────


@feed_router.get("/{token}")
def feed(
    token: str,
    repo: DocumentRepository = Depends(get_repository),
    storage: FileStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Every candidate HR has marked arrived, curated + verified documents. The
    external onboarding system fetches this with the shared token in the URL."""
    configured = settings.candidate_feed_token.strip()
    # 404 (not 401) when disabled or the token is wrong — never reveal existence.
    if not configured or not secrets.compare_digest(token, configured):
        raise NotFoundError("Not found")

    records = repo.list(TABLE)
    candidates: list[dict[str, Any]] = []
    for rec in sorted(records, key=lambda r: r.get("arrivedAt") or "", reverse=True):
        cid = rec.get("candidateId")
        if not cid:
            continue
        resolved, source = _resolve_candidate(repo, cid)
        if resolved is None:
            continue
        candidates.append(
            {
                **_curated_candidate(resolved, cid),
                "candidateSource": source,
                "arrivedAt": rec.get("arrivedAt"),
                "documents": _verified_documents(repo, storage, cid),
            }
        )
    logger.info("Candidate feed served (%d candidates).", len(candidates))
    return {"count": len(candidates), "generatedAt": _now(), "candidates": candidates}
