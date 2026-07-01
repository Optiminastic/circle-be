"""Password hashing (PBKDF2-HMAC-SHA256, stdlib — no extra dependency).

Stored format:  pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

Never store or log plaintext. `verify_password` is constant-time. `looks_hashed`
lets the auth flow lazily upgrade any legacy plaintext rows on first login.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240_000
_SALT_BYTES = 16


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def hash_password(plaintext: str) -> str:
    """Hash a plaintext password into the storable `pbkdf2_sha256$...` string."""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${_b64e(salt)}${_b64e(digest)}"


def looks_hashed(stored: str) -> bool:
    """True if the stored value is one of our hashes (vs a legacy plaintext row)."""
    return isinstance(stored, str) and stored.startswith(f"{_ALGO}$")


def verify_password(plaintext: str, stored: str) -> bool:
    """Constant-time verify a plaintext against a stored hash. False on any error."""
    if not looks_hashed(stored):
        return False
    try:
        _algo, iters, salt_b64, hash_b64 = stored.split("$", 3)
        expected = _b64d(hash_b64)
        computed = hashlib.pbkdf2_hmac(
            "sha256", plaintext.encode("utf-8"), _b64d(salt_b64), int(iters)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(computed, expected)
