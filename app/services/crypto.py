"""Symmetric encryption for sensitive fields stored at rest.

Used to encrypt exit-handover work-account passwords so the raw value is never
persisted in the database. The key is derived (SHA-256) from a configured secret
so no separate key file is required; set CREDENTIALS_KEY in the environment for a
dedicated key, otherwise it falls back to existing app secrets.

NEVER log the plaintext or the key.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings


def _fernet(settings: Settings) -> Fernet:
    # Derive a stable 32-byte Fernet key from a configured secret. Prefer a
    # dedicated CREDENTIALS_KEY; fall back to other server-only secrets so the
    # feature works out of the box without extra config.
    secret = (
        settings.credentials_key
        or settings.smtp_password
        or settings.aws_secret_access_key
        or "curcle-credentials-fallback-key"
    )
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(settings: Settings, plaintext: str) -> str:
    """Encrypt a plaintext value; returns an opaque token string."""
    return _fernet(settings).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(settings: Settings, token: str) -> str:
    """Decrypt a token produced by encrypt_secret; '' if it can't be read."""
    if not token:
        return ""
    try:
        return _fernet(settings).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
