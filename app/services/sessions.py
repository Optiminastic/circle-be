"""Signed, expiring session tokens for the HR dashboard.

A session is a Fernet-encrypted (authenticated + tamper-proof) JSON blob carrying
the user's email/role/name. It's delivered as an httpOnly cookie, so the browser
cannot read or forge it (unlike the old localStorage JSON). Expiry is enforced by
Fernet's built-in timestamp via `decrypt(ttl=...)`.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings

COOKIE_NAME = "circle_session"


def _fernet(settings: Settings) -> Fernet:
    # Dedicated session key: prefer SESSION_SECRET, else reuse an existing server
    # secret. Namespaced ("session:") so it never collides with crypto.py's key.
    secret = (
        settings.session_secret
        or settings.credentials_key
        or settings.smtp_password
        or settings.aws_secret_access_key
        or "circle-session-fallback-key"
    )
    digest = hashlib.sha256(("session:" + secret).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def issue_session(settings: Settings, *, email: str, role: str, name: str) -> str:
    payload = json.dumps({"email": email, "role": role, "name": name}).encode("utf-8")
    return _fernet(settings).encrypt(payload).decode("ascii")


def read_session(settings: Settings, token: str | None) -> dict[str, Any] | None:
    """Decode + validate a session token; None if missing, tampered, or expired."""
    if not token:
        return None
    ttl = max(1, settings.session_ttl_hours) * 3600
    try:
        raw = _fernet(settings).decrypt(token.encode("ascii"), ttl=ttl)
        data = json.loads(raw)
    except (InvalidToken, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not data.get("email") or data.get("role") not in ("admin", "hr"):
        return None
    return data
