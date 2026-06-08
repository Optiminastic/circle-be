"""Google Calendar sync endpoints (single shared HR account, one-way push).

The OAuth connect flow stores the shared account's refresh token in the
`google_oauth` table (row id "shared"). Event pushes look that token up, write
to Google, and persist the app-event -> google-event id mapping in
`calendar_links` so later updates/deletes target the same event.

Pushes are best-effort: any failure returns {pushed: false, reason} with HTTP
200 so the frontend scheduling flow is never blocked (same contract as
notifications.py). The work runs synchronously in the request (not a
BackgroundTask) because it needs the request-scoped DB session.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.dependencies import get_google_calendar_service, get_repository
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.repositories.base import DocumentRepository
from app.services.google_calendar import GoogleCalendarService

logger = get_logger("curcle.calendar")

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

_OAUTH_TABLE = "google_oauth"
_OAUTH_ROW_ID = "shared"
_LINKS_TABLE = "calendar_links"
_OAUTH_STATE = "curcle-shared"


def _connection(repo: DocumentRepository) -> dict[str, Any] | None:
    return repo.get(_OAUTH_TABLE, _OAUTH_ROW_ID)


@router.get("/status")
def status(
    repo: DocumentRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    conn = _connection(repo)
    return {
        "configured": settings.has_google,
        "connected": bool(conn and conn.get("refreshToken")),
        "connectedEmail": (conn or {}).get("connectedEmail"),
        "calendarId": settings.google_calendar_id,
    }


@router.get("/oauth/url")
def oauth_url(
    service: GoogleCalendarService = Depends(get_google_calendar_service),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not settings.has_google:
        return {"url": None, "reason": "not_configured"}
    return {"url": service.build_auth_url(state=_OAUTH_STATE)}


@router.get("/oauth/callback")
def oauth_callback(
    code: str | None = None,
    error: str | None = None,
    service: GoogleCalendarService = Depends(get_google_calendar_service),
    repo: DocumentRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    target = f"{settings.frontend_url.rstrip('/')}/settings"
    if error or not code:
        return RedirectResponse(url=f"{target}?calendar=error")
    try:
        result = service.exchange_code(code)
        if not result.get("refresh_token"):
            # Google only returns a refresh token on first consent; prompt=consent
            # in build_auth_url forces it, but guard anyway.
            return RedirectResponse(url=f"{target}?calendar=error")
        repo.upsert(
            _OAUTH_TABLE,
            _OAUTH_ROW_ID,
            {
                "id": _OAUTH_ROW_ID,
                "refreshToken": result["refresh_token"],
                "connectedEmail": result.get("email"),
            },
        )
        return RedirectResponse(url=f"{target}?calendar=connected")
    except Exception:  # noqa: BLE001
        logger.exception("Google OAuth callback failed")
        return RedirectResponse(url=f"{target}?calendar=error")


class PushEventIn(BaseModel):
    appEventId: str
    type: str  # 'HR Call' | 'IQ Test' | 'Assessment' | 'Interview'
    title: str
    dateTimeIso: str
    durationMin: int = 45
    notes: str | None = None
    attendeeEmail: str | None = None


@router.post("/events")
def push_event(
    payload: PushEventIn,
    service: GoogleCalendarService = Depends(get_google_calendar_service),
    repo: DocumentRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not settings.has_google:
        return {"pushed": False, "reason": "not_configured"}
    conn = _connection(repo)
    if not (conn and conn.get("refreshToken")):
        return {"pushed": False, "reason": "not_connected"}

    existing = repo.get(_LINKS_TABLE, payload.appEventId)
    google_event_id = (existing or {}).get("googleEventId")
    try:
        result = service.upsert_event(
            conn["refreshToken"],
            calendar_id=settings.google_calendar_id,
            event_type=payload.type,
            summary=payload.title,
            description=payload.notes or "",
            start_iso=payload.dateTimeIso,
            duration_min=payload.durationMin,
            attendee_email=payload.attendeeEmail,
            google_event_id=google_event_id,
            request_id=payload.appEventId,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to push event %s to Google Calendar", payload.appEventId)
        return {"pushed": False, "reason": "error"}

    repo.upsert(
        _LINKS_TABLE,
        payload.appEventId,
        {
            "id": payload.appEventId,
            "googleEventId": result["id"],
            "calendarId": settings.google_calendar_id,
        },
    )
    return {"pushed": True, "meetLink": result.get("hangoutLink")}


@router.delete("/events/{app_event_id}")
def delete_event(
    app_event_id: str,
    service: GoogleCalendarService = Depends(get_google_calendar_service),
    repo: DocumentRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    link = repo.get(_LINKS_TABLE, app_event_id)
    if not link or not link.get("googleEventId"):
        return {"deleted": False, "reason": "no_link"}
    conn = _connection(repo)
    if not (conn and conn.get("refreshToken")):
        return {"deleted": False, "reason": "not_connected"}
    try:
        service.delete_event(
            conn["refreshToken"],
            link.get("calendarId", settings.google_calendar_id),
            link["googleEventId"],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to delete event %s from Google Calendar", app_event_id)
        return {"deleted": False, "reason": "error"}
    repo.delete(_LINKS_TABLE, app_event_id)
    return {"deleted": True}
