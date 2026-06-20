"""Trusted-caller gate for the public careers endpoints.

The public careers operations (OTP request/verify, applied-check, apply) must be
reachable ONLY through the careers server (a Next.js server action) — never by a
browser fetch or a copied cURL hitting the API directly. The careers server
attaches a shared secret as the `X-Internal-Token` header; this dependency
verifies it with a constant-time compare.

When no token is configured the gate is OPEN (and says so in the logs) so an
un-migrated environment keeps working — set INTERNAL_API_TOKEN on both the API
and the careers server to enforce it. This follows the CLAUDE.md rule that all
enforcement lives server-side and no security logic sits in the client bundle.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.errors import AuthError
from app.core.logging import get_logger

logger = get_logger("curcle.security")


def require_internal_caller(
    x_internal_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Reject any caller that doesn't present the shared internal token."""
    expected = settings.internal_api_token.strip()
    if not expected:
        logger.warning(
            "INTERNAL_API_TOKEN is not set — the public endpoint gate is OPEN. "
            "Set it (and the careers server's matching value) to block direct calls."
        )
        return
    provided = (x_internal_token or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise AuthError("This endpoint is not directly accessible.")
