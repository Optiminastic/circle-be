"""Candidate notification endpoints.

The email is sent synchronously and the endpoint returns {sent: bool, reason?},
so the frontend can show the real "sent / failed" outcome. Sends never raise
(failures are logged and reported as sent:false), so a mail problem never breaks
the scheduling flow — it just surfaces as a failed-to-send result in the UI.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.services.email_sender import send_custom_email, send_schedule_email, send_test_email

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class ScheduleEmailIn(BaseModel):
    to: str
    candidateName: str
    type: str  # 'HR Call' | 'IQ Test' | 'Assessment' | 'Interview'
    dateTimeIso: str
    notes: str | None = None


@router.post("/schedule-email")
def schedule_email(
    payload: ScheduleEmailIn,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not payload.to.strip():
        return {"sent": False, "reason": "no_recipient"}
    if not settings.has_smtp:
        return {"sent": False, "reason": "not_configured"}

    sent = send_schedule_email(
        settings=settings,
        to=payload.to.strip(),
        candidate_name=payload.candidateName,
        schedule_type=payload.type,
        date_time_iso=payload.dateTimeIso,
        notes=payload.notes,
    )
    return {"sent": sent} if sent else {"sent": False, "reason": "send_failed"}


class TestEmailIn(BaseModel):
    to: str
    candidateName: str
    # 'iq_invite' | 'iq_passed' | 'iq_failed' | 'assessment_passed' | 'assessment_failed'
    template: str
    testUrl: str | None = None
    position: str | None = None
    score: str | None = None
    durationMin: int | None = None
    dateTimeIso: str | None = None
    salary: str | None = None


@router.post("/test-email")
def test_email(
    payload: TestEmailIn,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not payload.to.strip():
        return {"sent": False, "reason": "no_recipient"}
    if not settings.has_smtp:
        return {"sent": False, "reason": "not_configured"}

    sent = send_test_email(
        settings=settings,
        to=payload.to.strip(),
        candidate_name=payload.candidateName,
        template=payload.template,
        test_url=payload.testUrl,
        position=payload.position,
        score=payload.score,
        duration_min=payload.durationMin,
        date_time_iso=payload.dateTimeIso,
        salary=payload.salary,
    )
    return {"sent": sent} if sent else {"sent": False, "reason": "send_failed"}


class EmailLinkIn(BaseModel):
    label: str
    url: str


class CustomEmailIn(BaseModel):
    to: str
    subject: str
    body: str
    # Optional calendar invite (.ics, METHOD:REQUEST) attached when eventStartIso is set.
    eventStartIso: str | None = None
    eventDurationMin: int = 45
    eventSummary: str | None = None
    eventLocation: str | None = None
    eventDescription: str | None = None
    organizerEmail: str | None = None
    organizerName: str | None = None
    attendees: list[str] | None = None
    eventUid: str | None = None
    # Rendered as labelled buttons (e.g. resume / interview-questions links).
    links: list[EmailLinkIn] | None = None


@router.post("/custom-email")
def custom_email(
    payload: CustomEmailIn,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Send an HR-composed email (e.g. an interview invitation the HR edited),
    optionally with a Google Calendar invite attached.

    Returns {sent, reason?} reflecting the real delivery result; never raises.
    """
    if not payload.to.strip():
        return {"sent": False, "reason": "no_recipient"}
    if not payload.subject.strip() or not payload.body.strip():
        return {"sent": False, "reason": "empty"}
    if not settings.has_smtp:
        return {"sent": False, "reason": "not_configured"}

    sent = send_custom_email(
        settings=settings,
        to=payload.to.strip(),
        subject=payload.subject,
        body=payload.body,
        event_start_iso=payload.eventStartIso,
        event_duration_min=payload.eventDurationMin,
        event_summary=payload.eventSummary,
        event_location=payload.eventLocation,
        event_description=payload.eventDescription,
        organizer_email=payload.organizerEmail,
        organizer_name=payload.organizerName,
        attendees=payload.attendees,
        event_uid=payload.eventUid,
        links=[l.model_dump() for l in payload.links] if payload.links else None,
    )
    return {"sent": sent} if sent else {"sent": False, "reason": "send_failed"}
