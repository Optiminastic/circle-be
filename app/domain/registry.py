"""Resource registry — the single declaration of the HR resources the API serves.

Adding a new resource is a one-line change here (Open/Closed): the generic
repository, service and router pick it up automatically.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import NotFoundError


@dataclass(frozen=True)
class ResourceDef:
    slug: str       # URL segment, e.g. "iq-tests"
    table: str      # physical table name, e.g. "iq_tests"
    id_field: str   # field within the document used as the primary key


_DEFS: tuple[ResourceDef, ...] = (
    ResourceDef("auth-users", "auth_users", "id"),
    ResourceDef("schedules", "schedules", "id"),
    ResourceDef("jobs", "jobs", "id"),
    ResourceDef("candidates", "candidates", "id"),
    ResourceDef("test-invites", "test_invites", "id"),
    ResourceDef("doc-requests", "doc_requests", "id"),
    ResourceDef("interviews", "interviews", "id"),
    ResourceDef("iq-tests", "iq_tests", "id"),
    ResourceDef("assignments", "assignments", "id"),
    ResourceDef("bgvs", "bgvs", "candidateId"),
    ResourceDef("onboarding", "onboarding", "candidateId"),
    ResourceDef("employees", "employees", "id"),
    ResourceDef("assets", "assets", "id"),
    ResourceDef("email-templates", "email_templates", "id"),
    ResourceDef("sent-emails", "sent_emails", "id"),
    ResourceDef("offboarding", "offboarding", "employeeId"),
    # Google Calendar: single shared-account OAuth row + per-event id mapping.
    ResourceDef("google-oauth", "google_oauth", "id"),
    ResourceDef("calendar-links", "calendar_links", "id"),
)

RESOURCES: dict[str, ResourceDef] = {d.slug: d for d in _DEFS}


def get_resource(slug: str) -> ResourceDef:
    resource = RESOURCES.get(slug)
    if resource is None:
        raise NotFoundError(f"Unknown resource '{slug}'. Valid: {', '.join(RESOURCES)}")
    return resource


def all_tables() -> list[str]:
    return [d.table for d in _DEFS]
