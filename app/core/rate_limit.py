"""In-memory, per-IP rate limiting for unauthenticated public writes.

The careers site lets anyone submit a job application (create a candidate +
upload a resume) with no login, which makes those two endpoints a spam target.
This module provides a small sliding-window limiter keyed by client IP, plus the
helpers the HTTP middleware uses to decide when it applies.

In-memory is deliberate: the backend runs as a single instance, so a process-
local limiter is enough and avoids a Redis dependency. If the API is ever scaled
horizontally, swap the store for a shared one (the public surface stays the same).
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque

from starlette.requests import Request

from app.core.config import Settings

# Origins that are never rate-limited: the HR app and any localhost / private-LAN
# dev origin. Everything else (the public careers site, or a script hitting the
# API directly with no/another origin) is subject to the per-IP limit.
_LOCAL_ORIGIN_RE = re.compile(
    r"^https?://(localhost|127\.0\.0\.1|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?$"
)


class SlidingWindowRateLimiter:
    """Allow at most N hits per key within each configured time window.

    Multiple windows can be enforced at once (e.g. a tight per-minute burst cap
    plus a looser per-hour cap). Thread-safe; FastAPI middleware runs on the
    event loop while sync endpoints run in a threadpool, so a lock is required.
    """

    def __init__(self, rules: list[tuple[int, float]]):
        # rules: (max_requests, window_seconds)
        self._rules = sorted(rules, key=lambda r: r[1])
        self._max_window = max((w for _, w in self._rules), default=0.0)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_gc = 0.0

    def allow(self, key: str) -> bool:
        """Record a hit for `key` and return whether it stays within all limits."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            cutoff = now - self._max_window
            while hits and hits[0] <= cutoff:
                hits.popleft()

            for max_requests, window in self._rules:
                start = now - window
                count = sum(1 for ts in hits if ts > start)
                if count >= max_requests:
                    return False

            hits.append(now)
            self._collect_garbage(now)
            return True

    def _collect_garbage(self, now: float) -> None:
        """Drop idle keys roughly once a minute so memory stays bounded."""
        if now - self._last_gc < 60:
            return
        self._last_gc = now
        cutoff = now - self._max_window
        for key in list(self._hits.keys()):
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]


def client_ip(request: Request) -> str:
    """Best-effort client IP. Behind Vercel/Render the real IP is the first hop
    of X-Forwarded-For; fall back to the socket peer for local/direct calls."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_exempt_origin(origin: str, settings: Settings) -> bool:
    """True for the HR app origin and local dev origins — never rate-limited."""
    if not origin:
        return False
    if origin.rstrip("/") == settings.hr_app_origin.rstrip("/"):
        return True
    return bool(_LOCAL_ORIGIN_RE.match(origin))
