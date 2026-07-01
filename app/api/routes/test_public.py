"""Public (no-login) candidate test/assessment endpoints.

The candidate reaches these with the unguessable invite token in the URL. They
replace the old pattern where the public test page PATCHed the generic
`/api/test-invites/{id}` with arbitrary fields (letting a candidate overwrite a
finished result, flip fail→pass, or touch unrelated fields). Here the server:

  * enforces WRITE-ONCE — a finished attempt can't be resubmitted/overwritten,
  * accepts only the known result fields (a field allowlist),
  * stamps the completion time server-side,
  * creates the IQ result row itself (was an open `POST /api/iq-tests`), binding
    it to the invite's candidate so it can't be injected against someone else.

NOTE: the questions + answer key still live in the client bundle, so the *raw*
score is computed on the client for now. Making scoring fully tamper-proof means
moving the banks + scoring server-side (a separate change). These endpoints stop
the result being overwritten/replayed and block arbitrary-field tampering.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies import get_resource_service
from app.domain.registry import get_resource
from app.services.resource_service import ResourceService

router = APIRouter(prefix="/api/public/test", tags=["public-test"])

_INVITES = "test-invites"
_IQ = "iq-tests"
_TERMINAL = {"Completed", "Auto-Submitted", "Graded"}
# The only fields a candidate's submit may set on the invite.
_RESULT_FIELDS = (
    "status", "correct", "total", "score", "passed", "disqualified", "violations", "answers",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(service: ResourceService, token: str) -> dict[str, Any]:
    # NotFoundError -> 404, same as a bad/expired link.
    return service.get(get_resource(_INVITES), token)


@router.post("/{token}/start")
def start(token: str, service: ResourceService = Depends(get_resource_service)) -> dict[str, Any]:
    invite = _load(service, token)
    if invite.get("status") in _TERMINAL:
        raise HTTPException(status_code=409, detail="This test has already been submitted.")
    return service.patch(
        get_resource(_INVITES),
        token,
        {"status": "In Progress", "startedAt": invite.get("startedAt") or _now()},
    )


@router.post("/{token}/violation")
def violation(token: str, service: ResourceService = Depends(get_resource_service)) -> dict[str, Any]:
    invite = _load(service, token)
    if invite.get("status") in _TERMINAL:
        return {"violations": int(invite.get("violations") or 0)}
    count = int(invite.get("violations") or 0) + 1
    service.patch(get_resource(_INVITES), token, {"violations": count})
    return {"violations": count}


@router.post("/{token}/submit")
def submit(
    token: str,
    payload: dict[str, Any] = Body(...),
    service: ResourceService = Depends(get_resource_service),
) -> dict[str, Any]:
    invite = _load(service, token)
    if invite.get("status") in _TERMINAL:
        raise HTTPException(status_code=409, detail="This test has already been submitted.")

    changes = {key: payload[key] for key in _RESULT_FIELDS if key in payload}
    # Server owns the terminal status + timestamp.
    if changes.get("status") not in _TERMINAL:
        changes["status"] = "Completed"
    changes["completedAt"] = _now()
    service.patch(get_resource(_INVITES), token, changes)

    # Persist the IQ result row here (previously an open POST /api/iq-tests),
    # binding the candidate identity to the invite so it can't be spoofed.
    if invite.get("kind") == "iq" and isinstance(payload.get("iqRecord"), dict):
        record = {
            **payload["iqRecord"],
            "candidateId": invite.get("candidateId"),
            "candidateName": invite.get("candidateName"),
        }
        service.create(get_resource(_IQ), record)

    return {"ok": True, "status": changes["status"]}
