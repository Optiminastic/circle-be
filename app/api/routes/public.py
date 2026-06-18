"""Public, unauthenticated endpoints for the careers site.

This is the ONLY write surface the public is meant to use. Unlike the generic
CRUD router, it is deliberately narrow and hardened against abuse:

  * one atomic operation — submit a job application (resume + candidate) together;
  * a strict schema that REJECTS unknown fields (no mass-assignment), caps every
    string length, and validates email / phone / URL formats;
  * the server sets all trust-sensitive fields itself (id, status, source,
    applied date, role/department, fit rating) — the client cannot forge them;
  * the target job must exist and be Open;
  * the resume must be a real PDF (magic-byte checked) within the size limit;
  * text is sanitised (control chars + angle brackets stripped) to neutralise
    stored-XSS / HTML-injection into the HR dashboard and outgoing emails;
  * per-IP rate limited by the middleware in app.main.

Reads stay on the generic GET /api/jobs (served server-side by the careers app).
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError, field_validator

from app.api.dependencies import get_repository, get_storage
from app.core.config import Settings, get_settings
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.repositories.base import DocumentRepository
from app.services.screening import build_answers, compute_fit
from app.storage.base import FileStorage

router = APIRouter(prefix="/api/public", tags=["public"])

logger = get_logger("curcle.public")

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_LINKEDIN_RE = re.compile(r"^https?://([a-z0-9-]+\.)*linkedin\.com/.+", re.IGNORECASE)
_DRIVE_RE = re.compile(r"^https?://(drive|docs)\.google\.com/.+", re.IGNORECASE)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Max number of screening answers accepted (bounds payload + work).
_MAX_ANSWERS = 50
_MAX_ANSWER_LEN = 2000


def _clean(value: str) -> str:
    """Strip control chars and angle brackets, collapse, and trim.

    Angle brackets are removed so applicant text can never inject markup into the
    HR dashboard (defence in depth — React already escapes) or HTML emails.
    """
    return _CONTROL.sub("", value).replace("<", "").replace(">", "").strip()


class ApplicationIn(BaseModel):
    # Reject any field not declared here — blocks mass-assignment (e.g. trying to
    # smuggle status="Hired", a custom id, or a forged fitRating).
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    jobId: str = Field(min_length=1, max_length=64)
    fullName: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    phone: str = Field(min_length=10, max_length=10)
    currentDesignation: str = Field(min_length=1, max_length=120)
    currentCtc: str = Field(min_length=1, max_length=40)
    expectedCtc: str = Field(min_length=1, max_length=40)
    totalExperienceYears: float = Field(ge=0, le=60)
    noticePeriodDays: int = Field(ge=0, le=3650)
    linkedInUrl: str = Field(min_length=1, max_length=300)
    coverNote: str = Field(min_length=1, max_length=5000)
    # Optional fields.
    location: str = Field(default="", max_length=120)
    currentCompany: str = Field(default="", max_length=120)
    resumeUrl: str = Field(default="", max_length=500)
    responses: dict[str, str] = Field(default_factory=dict)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("invalid phone")
        return v

    @field_validator("linkedInUrl")
    @classmethod
    def _linkedin(cls, v: str) -> str:
        if not _LINKEDIN_RE.match(v):
            raise ValueError("invalid LinkedIn URL")
        return v

    @field_validator("resumeUrl")
    @classmethod
    def _drive(cls, v: str) -> str:
        if v and not _DRIVE_RE.match(v):
            raise ValueError("invalid Google Drive URL")
        return v

    @field_validator("responses")
    @classmethod
    def _responses(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > _MAX_ANSWERS:
            raise ValueError("too many answers")
        return {
            str(k)[:64]: str(val)[:_MAX_ANSWER_LEN]
            for k, val in list(v.items())[:_MAX_ANSWERS]
        }


@router.post("/apply", status_code=201)
async def apply(
    payload: str = Form(...),
    resume: UploadFile = File(...),
    repo: DocumentRepository = Depends(get_repository),
    storage: FileStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    # 1) Parse + validate the application (strict schema, length/format caps).
    try:
        raw = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        raise ValidationError("Invalid application data.")
    if not isinstance(raw, dict):
        raise ValidationError("Invalid application data.")
    try:
        app_in = ApplicationIn.model_validate(raw)
    except PydanticValidationError:
        # Don't echo internals back to the public — generic message only.
        raise ValidationError("Some details are missing or invalid. Please review the form.")

    # 2) The job must exist and be open to applications.
    job = repo.get("jobs", app_in.jobId)
    if job is None:
        raise ValidationError("This opening could not be found.")
    if job.get("status") != "Open":
        raise ValidationError("Applications for this opening are closed.")

    # 3) Validate the resume bytes — must be a real, non-empty PDF within limit.
    data = await resume.read()
    if not data:
        raise ValidationError("Please attach your resume.")
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise ValidationError(f"Your resume must be {settings.max_upload_mb} MB or smaller.")
    if not data.startswith(b"%PDF-"):
        raise ValidationError("Your resume must be a PDF file.")

    # 4) Server-owned identifiers + trust-sensitive fields (never from the client).
    candidate_id = f"CAN-{secrets.randbelow(900000) + 100000}"
    doc_id = uuid.uuid4().hex[:12]
    filename = (resume.filename or "resume.pdf")
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    key = f"documents/candidate/{candidate_id}/{doc_id}_{_SAFE_NAME.sub('_', filename)}"
    storage.put(key, data, "application/pdf")
    repo.upsert(
        "documents",
        doc_id,
        {
            "id": doc_id,
            "entityType": "candidate",
            "entityId": candidate_id,
            "category": "resume",
            "fileName": filename,
            "contentType": "application/pdf",
            "size": len(data),
            "storageKey": key,
            "uploadedAt": datetime.now(timezone.utc).isoformat(),
        },
    )

    # 5) Score screening answers server-side from the job's own questions.
    questions = job.get("screeningQuestions") or []
    answers = build_answers(questions, app_in.responses) if questions else []
    fit = compute_fit(answers) if answers else None

    # 6) Persist the candidate. Role/department come from the JOB, status/source/
    #    date are fixed by the server — the applicant can't influence the pipeline.
    candidate = {
        "id": candidate_id,
        "fullName": _clean(app_in.fullName),
        "email": app_in.email,
        "phone": f"+91 {app_in.phone}",
        "location": _clean(app_in.location),
        "currentCompany": _clean(app_in.currentCompany),
        "currentDesignation": _clean(app_in.currentDesignation),
        "totalExperienceYears": app_in.totalExperienceYears,
        "relevantExperienceYears": app_in.totalExperienceYears,
        "currentCtc": _clean(app_in.currentCtc),
        "expectedCtc": _clean(app_in.expectedCtc),
        "noticePeriodDays": app_in.noticePeriodDays,
        "resumeUrl": app_in.resumeUrl or filename,
        "linkedInUrl": app_in.linkedInUrl,
        "appliedRole": job.get("title", ""),
        "department": job.get("department", ""),
        "sourceOfApplication": "Job Posting",
        "referralDetails": f"Applied via public posting {app_in.jobId}",
        "hrRemarks": _clean(app_in.coverNote),
        "status": "New Application",
        "appliedDate": date.today().isoformat(),
        "jobId": app_in.jobId,
        "screeningAnswers": answers or None,
        "fitRating": fit,
    }
    repo.upsert("candidates", candidate_id, candidate)
    logger.info("Public application stored: candidate=%s job=%s", candidate_id, app_in.jobId)

    # Return only an acknowledgement — never echo stored data back to the public.
    return {"ok": True}
