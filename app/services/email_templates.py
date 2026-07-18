"""HR-editable overrides for the built-in transactional email templates.

HR edits templates in Settings → Email templates. Each edited template is stored
as one row in `email_template_overrides`, keyed by its template id:

    {"id": "iq_test_invite", "subject": "...", "body": "...", "updatedAt": "..."}

`body` is **plain text** with two kinds of token:

* ``{{placeholder}}`` — substituted here from the send's context
  (``{{candidate_name}}``, ``{{role}}``, ``{{test_url}}``, …).
* ``[[Label|url]]`` — turned into a branded anchor button downstream by
  :func:`app.services.email_sender._render_body_html`, so a template can say
  ``[[Start your IQ test|{{test_url}}]]`` and the candidate gets a real button
  rather than a pasted URL.

Only templates HR has actually saved get a row. When no (usable) override exists
this module returns ``None`` and the caller keeps its existing built-in HTML —
so an un-edited template behaves exactly as it did before this feature.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

TABLE = "email_template_overrides"

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class _Repo(Protocol):
    def get(self, table: str, item_id: str) -> dict[str, Any] | None: ...


def render(text: str, variables: dict[str, Any]) -> str:
    """Substitute ``{{token}}`` placeholders. Unknown tokens resolve to ''."""

    def _sub(match: re.Match[str]) -> str:
        value = variables.get(match.group(1))
        return "" if value is None else str(value)

    return _TOKEN_RE.sub(_sub, text)


def load_override(repo: _Repo, key: str) -> dict[str, str] | None:
    """Return the HR-saved {subject, body} for `key`, or None to use the built-in.

    A row with a blank subject or body is treated as absent rather than sending
    an empty email.
    """
    try:
        doc = repo.get(TABLE, key)
    except Exception:  # never let a template lookup break a send
        return None
    if not doc:
        return None
    subject = str(doc.get("subject") or "").strip()
    body = str(doc.get("body") or "").strip()
    if not subject or not body:
        return None
    return {"subject": subject, "body": body}


def resolve(repo: _Repo, key: str, variables: dict[str, Any]) -> dict[str, str] | None:
    """Load an override and render its placeholders, or None if there isn't one."""
    override = load_override(repo, key)
    if override is None:
        return None
    return {
        "subject": render(override["subject"], variables),
        "body": render(override["body"], variables),
    }
