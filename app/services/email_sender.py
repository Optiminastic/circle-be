"""Candidate notification emails (Gmail SMTP).

Pure composition + transport — no FastAPI imports. Designed to run inside a
BackgroundTask: failures are logged, never raised (a schedule must never break
because an email could not be delivered).
"""

from __future__ import annotations

import base64
import html
import json
import re
import smtplib
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.core.logging import get_logger


def _brand_header() -> str:
    """Email header: the Optiminastic logo (hosted), shown large."""
    return """              <td style="padding:22px 28px 16px;">
                <img src="https://res.cloudinary.com/dui7h1n3d/image/upload/q_auto/f_auto/v1781496355/optiminastic-logo_baso6m.png" alt="optiminastic logo" height="72" style="height:72px;width:auto;vertical-align:middle;border:0;" />
              </td>"""

logger = get_logger("curcle.email")

# How long to wait for an SMTP/HTTP connection before giving up.
_SMTP_TIMEOUT = 20
_SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"


def _addr_list(msg: EmailMessage, header: str) -> list[dict[str, str]]:
    raw = msg.get(header) or ""
    return [{"email": a.strip()} for a in raw.split(",") if a.strip()]


def _deliver_sendgrid(settings: Settings, msg: EmailMessage) -> None:
    """Send via SendGrid's HTTPS API (works where outbound SMTP is blocked, e.g.
    Render's free tier). Extracts the text/HTML parts + any attachments from the
    already-built EmailMessage. Raises on failure; callers log and swallow."""
    text_value, html_value = "", ""
    attachments: list[dict[str, str]] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_disposition() == "attachment":
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                {
                    "content": base64.b64encode(payload).decode("ascii"),
                    "filename": part.get_filename() or "attachment",
                    "type": part.get_content_type(),
                    "disposition": "attachment",
                }
            )
        elif part.get_content_type() == "text/plain" and not text_value:
            text_value = part.get_content()
        elif part.get_content_type() == "text/html" and not html_value:
            html_value = part.get_content()

    content = []
    if text_value:
        content.append({"type": "text/plain", "value": text_value})
    if html_value:
        content.append({"type": "text/html", "value": html_value})
    if not content:
        content.append({"type": "text/plain", "value": ""})

    # SendGrid requires every address to be unique across to/cc/bcc — dedupe the
    # To list, then drop any Cc that repeats a To (or another Cc).
    to_addrs: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in _addr_list(msg, "To"):
        key = a["email"].lower()
        if key and key not in seen:
            seen.add(key)
            to_addrs.append(a)
    cc_addrs: list[dict[str, str]] = []
    for a in _addr_list(msg, "Cc"):
        key = a["email"].lower()
        if key and key not in seen:
            seen.add(key)
            cc_addrs.append(a)

    personalization: dict[str, object] = {"to": to_addrs}
    if cc_addrs:
        personalization["cc"] = cc_addrs

    body: dict[str, object] = {
        "personalizations": [personalization],
        "from": {"email": settings.from_address, "name": settings.smtp_from_name},
        "subject": msg.get("Subject") or "",
        "content": content,
    }
    if attachments:
        body["attachments"] = attachments

    req = urllib.request.Request(
        _SENDGRID_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.sendgrid_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_SMTP_TIMEOUT):  # noqa: S310 (fixed host)
            return  # 202 Accepted
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"SendGrid {exc.code}: {detail}") from exc


def _deliver(settings: Settings, msg: EmailMessage, to_addrs: list[str] | None = None) -> None:
    """Send one message via the configured transport.

    Prefers the SendGrid HTTP API when SENDGRID_API_KEY is set (works on networks
    that block SMTP). Otherwise uses SMTP — implicit TLS (SMTP_SSL) on port 465,
    STARTTLS elsewhere. Raises on failure; callers log and swallow.
    """
    if settings.sendgrid_key:
        _deliver_sendgrid(settings, msg)
        return

    if settings.smtp_port == 465:
        smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=_SMTP_TIMEOUT)
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=_SMTP_TIMEOUT)
    with smtp:
        if settings.smtp_port != 465:
            smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        if to_addrs is not None:
            smtp.send_message(msg, to_addrs=to_addrs)
        else:
            smtp.send_message(msg)


# Rounds conducted in person at the office (everything except the HR call).
OFFLINE_TYPES = {"IQ Test", "Assessment", "Interview"}

_IST = ZoneInfo("Asia/Kolkata")


def _format_ist(date_time_iso: str) -> str:
    """ISO timestamp -> 'Tuesday, 10 June 2026 at 10:00 AM IST' (fallback: raw)."""
    try:
        dt = datetime.fromisoformat(date_time_iso.replace("Z", "+00:00"))
        local = dt.astimezone(_IST)
        # %-d / %#d are platform-specific; strip the leading zero manually.
        day = str(local.day)
        time = local.strftime("%I:%M %p").lstrip("0")
        return f"{local.strftime('%A')}, {day} {local.strftime('%B %Y')} at {time} IST"
    except (ValueError, TypeError):
        return date_time_iso


def subject_for(schedule_type: str) -> str:
    if schedule_type == "HR Call":
        return "Your HR call with Optiminastic is scheduled"
    return f"Your {schedule_type} at Optiminastic — details inside"


def _build_text(
    schedule_type: str,
    candidate_name: str,
    when_ist: str,
    office_address: str,
    office_maps_url: str,
    notes: str | None,
) -> str:
    lines = [f"Hi {candidate_name},", ""]
    if schedule_type == "HR Call":
        lines += [
            "Your HR call has been scheduled. Our team will call you at the time below.",
            "",
            f"When: {when_ist}",
        ]
    else:
        lines += [
            f"Your {schedule_type} has been scheduled.",
            "",
            f"When: {when_ist}",
            "",
            "Please note: this round is conducted IN PERSON at our office.",
            f"Address: {office_address}",
            f"Map: {office_maps_url}",
        ]
    if notes:
        lines += ["", f"Notes from our team: {notes}"]
    lines += ["", "— The Optiminastic HR Team"]
    return "\n".join(lines)


def _build_html(
    schedule_type: str,
    candidate_name: str,
    when_ist: str,
    office_address: str,
    office_maps_url: str,
    notes: str | None,
) -> str:
    name = html.escape(candidate_name)
    safe_notes = html.escape(notes) if notes else ""
    safe_address = html.escape(office_address)

    if schedule_type == "HR Call":
        intro = (
            "Your HR call has been scheduled. Our team will call you at the time below — "
            "please keep your phone handy."
        )
        location_block = ""
    else:
        intro = f"Your <strong>{html.escape(schedule_type)}</strong> has been scheduled."
        location_block = f"""
          <div style="margin:20px 0;padding:16px;background:#fdeef3;border:1px solid #f3c6d8;border-radius:10px;">
            <p style="margin:0 0 6px;font-size:14px;font-weight:bold;color:#111827;">
              📍 This round is conducted in person at our office.
            </p>
            <p style="margin:0 0 12px;font-size:13px;color:#444;">{safe_address}</p>
            <a href="{html.escape(office_maps_url, quote=True)}"
               style="display:inline-block;background:#d11453;color:#ffffff;text-decoration:none;
                      font-size:13px;font-weight:bold;padding:9px 18px;border-radius:8px;">
              View on Google Maps
            </a>
          </div>
        """

    notes_block = (
        f"""
          <p style="margin:16px 0 0;font-size:13px;color:#555;">
            <strong>Notes from our team:</strong> {safe_notes}
          </p>
        """
        if safe_notes
        else ""
    )

    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:28px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                 style="max-width:600px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e6e8ec;box-shadow:0 1px 3px rgba(17,24,39,0.04);">
            <tr>
{_brand_header()}
            </tr>
            <tr><td style="padding:0 28px;"><div style="height:1px;background:#eceef1;line-height:1px;font-size:0;">&nbsp;</div></td></tr>
            <tr>
              <td style="padding:22px 28px 28px;">
                <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
                <p style="margin:0 0 18px;font-size:13.5px;color:#444;line-height:1.55;">{intro}</p>
                <div style="padding:14px 16px;background:#f4f5f7;border-radius:10px;">
                  <p style="margin:0;font-size:13px;color:#111827;">
                    <strong>When:</strong> {html.escape(when_ist)}
                  </p>
                </div>
                {location_block}
                {notes_block}
                <p style="margin:26px 0 0;font-size:12px;color:#999;">— The Optiminastic HR Team</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


# ---------------------------------------------------------------------------
# Online test pipeline emails (IQ invite / results / assessment chain)
# ---------------------------------------------------------------------------

_BTN_STYLE = (
    "display:inline-block;background:#d11453;color:#ffffff;text-decoration:none;"
    "font-size:13px;font-weight:bold;padding:10px 20px;border-radius:8px;"
)

_RULES_HTML = """
  <ul style="margin:10px 0 0;padding-left:18px;font-size:12.5px;color:#555;line-height:1.7;">
    <li>The test runs in <strong>full screen, in a single tab</strong>.</li>
    <li>Switching tabs or leaving the window is flagged — <strong>3 violations disqualify your test (no score)</strong>.</li>
    <li>The timer keeps running even if you refresh the page.</li>
    <li>Make sure you have a stable internet connection before starting.</li>
  </ul>
"""


def test_email_subject(template: str, position: str | None = None) -> str:
    subjects = {
        "iq_invite": "Your Optiminastic IQ Test — secure test link inside",
        "iq_passed": "Great news — you've cleared the IQ round at Optiminastic",
        "iq_failed": "Update on your application at Optiminastic",
        "assessment_invite": "Your Optiminastic assessment — secure test link inside",
        "assessment_passed": "You've cleared the assessment — interview is next",
        "assessment_failed": "Update on your application at Optiminastic",
        "assignment_invite": "Your Optiminastic assignment — submit your work",
        "doc_request": "Action needed — upload your onboarding documents (link valid 24 hours)",
        "offer_shortlisted": "Great news — you've been shortlisted at Optiminastic ✨",
        "offer_selected": "Congratulations — you're selected at Optiminastic 🎉",
        "job_offer": "Your job offer from Optiminastic",
        "offer_letter": "Your offer letter from Optiminastic — please review & sign",
        "office_invite": "You're invited to our office — Optiminastic",
        "appointment_letter": "Your letter of appointment — Optiminastic",
    }
    if template == "job_offer" and position:
        return f"Your job offer — {position} at Optiminastic"
    if template == "offer_letter" and position:
        return f"Your offer letter for {position} at Optiminastic — please review & sign"
    if template == "appointment_letter" and position:
        return f"Letter of appointment — {position} at Optiminastic"
    if template == "offer_shortlisted" and position:
        return f"You're shortlisted for {position} at Optiminastic — confirm your availability"
    if template == "offer_selected" and position:
        return f"You're selected for {position} at Optiminastic — confirm your availability"
    if template == "assignment_invite" and position:
        return f"Your {position} assignment at Optiminastic — submit your work"
    if template == "iq_passed" and position:
        return f"You've cleared the IQ round — your {position} assignment is ready"
    return subjects.get(template, "Update from the Optiminastic HR Team")


def _test_email_body_html(
    settings: Settings,
    template: str,
    candidate_name: str,
    *,
    test_url: str | None,
    position: str | None,
    score: str | None,
    duration_min: int | None,
    when_ist: str | None,
    salary: str | None = None,
) -> str:
    """Inner HTML for each pipeline template (greeting/footer added by caller)."""
    name = html.escape(candidate_name)
    pos = html.escape(position or "the role you applied for")
    url = html.escape(test_url or "#", quote=True)
    dur = duration_min or 60

    if template == "job_offer":
        join_line = (
            f"Please confirm if you can join us from <strong>{when_ist}</strong>."
            if when_ist
            else "Please confirm your availability to join us."
        )
        salary_row = html.escape(salary) if salary else "As discussed during the process"
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Dear {name},</p>
          <p style="margin:0 0 16px;font-size:13.5px;color:#444;line-height:1.6;">
            We are pleased to offer you the position of <strong>{pos}</strong> at
            <strong>Optiminastic, Mumbai</strong>. {join_line}
          </p>
          <div style="margin:0 0 16px;padding:16px 18px;background:#f4f5f7;border-radius:10px;">
            <p style="margin:0 0 10px;font-size:12px;font-weight:bold;text-transform:uppercase;letter-spacing:0.4px;color:#9aa0a6;">The details are as follows</p>
            <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;font-size:13px;color:#111827;">
              <tr><td style="padding:3px 0;color:#6b7280;width:140px;">Job Title</td><td style="padding:3px 0;font-weight:bold;">{pos}</td></tr>
              <tr><td style="padding:3px 0;color:#6b7280;">Salary</td><td style="padding:3px 0;font-weight:bold;">{salary_row}</td></tr>
              <tr><td style="padding:3px 0;color:#6b7280;">Working Days</td><td style="padding:3px 0;">Monday to Friday</td></tr>
              <tr><td style="padding:3px 0;color:#6b7280;">Working Hours</td><td style="padding:3px 0;">10:00 AM to 7:00 PM <span style="color:#6b7280;">(flexible check-in between 9:00 AM and 11:00 AM)</span></td></tr>
              <tr><td style="padding:3px 0;color:#6b7280;">Mode</td><td style="padding:3px 0;">On site</td></tr>
            </table>
          </div>
          <p style="margin:0;font-size:13px;color:#444;line-height:1.55;">
            Your formal offer letter will follow shortly. We look forward to having you on our team.
          </p>
        """

    if template == "offer_letter":
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Dear {name},</p>
          <p style="margin:0 0 14px;font-size:13.5px;color:#444;line-height:1.55;">
            Please find your <strong>offer letter for the {pos} role</strong> at Optiminastic.
            It confirms the terms of your offer in full.
          </p>
          <div style="margin:0 0 16px;padding:14px 16px;background:#f4f5f7;border-radius:10px;">
            <p style="margin:0;font-size:13px;color:#111827;">
              <strong>Next step:</strong> review the letter, and if everything looks good,
              <strong>sign it and reply with the signed copy</strong>.
            </p>
          </div>
          <p style="margin:0;font-size:13px;color:#444;line-height:1.55;">
            If you have any questions about the offer, just reply here — we&apos;re happy to help.
          </p>
        """

    if template == "office_invite":
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
          <p style="margin:0 0 14px;font-size:13.5px;color:#444;line-height:1.55;">
            Thank you for accepting your offer — welcome to the team! We&apos;d love to have you
            visit our office to meet everyone and complete a few joining formalities.
          </p>
          <div style="margin:0 0 16px;padding:16px;background:#fdeef3;border:1px solid #f3c6d8;border-radius:10px;">
            <p style="margin:0 0 6px;font-size:14px;font-weight:bold;color:#111827;">📍 Our office</p>
            <p style="margin:0 0 12px;font-size:13px;color:#444;">{html.escape(settings.office_address)}</p>
            <a href="{html.escape(settings.office_maps_url, quote=True)}" style="{_BTN_STYLE}">View on Google Maps</a>
          </div>
          <p style="margin:0;font-size:13px;color:#444;line-height:1.55;">
            Our HR team will confirm the exact day and time with you shortly. We look forward to
            seeing you!
          </p>
        """

    if template == "appointment_letter":
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
          <p style="margin:0 0 14px;font-size:13.5px;color:#444;line-height:1.55;">
            We&apos;re delighted to share your <strong>letter of appointment</strong> for the
            <strong>{pos} role</strong> at Optiminastic. This confirms your appointment and
            the terms discussed during your onboarding.
          </p>
          <div style="margin:0 0 16px;padding:14px 16px;background:#f4f5f7;border-radius:10px;">
            <p style="margin:0;font-size:13px;color:#111827;">
              Please keep this letter for your records. Our HR team will reach out with your start-date
              logistics and first-day details.
            </p>
          </div>
          <p style="margin:0;font-size:13px;color:#444;line-height:1.55;">
            Welcome aboard — we can&apos;t wait to have you with us.
          </p>
        """

    if template == "doc_request":
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
          <p style="margin:0 0 14px;font-size:13.5px;color:#444;line-height:1.55;">
            Welcome aboard! To complete your onboarding for <strong>{pos}</strong>, please upload your
            joining documents through your secure personal link below.
          </p>
          <div style="margin:0 0 16px;padding:14px 16px;background:#fdeef3;border:1px solid #f3c6d8;border-radius:10px;">
            <p style="margin:0;font-size:13px;font-weight:bold;color:#b91c1c;">
              ⏳ This link is valid for 24 hours only.
            </p>
            <p style="margin:6px 0 0;font-size:12.5px;color:#555;">
              If it expires before you finish, just let us know and we&apos;ll send a fresh one.
            </p>
          </div>
          <p style="margin:0 0 8px;font-size:12.5px;font-weight:bold;color:#111827;">We&apos;ll need:</p>
          <ul style="margin:0 0 18px;padding-left:18px;font-size:12.5px;color:#555;line-height:1.7;">
            <li>Aadhaar card &amp; PAN card</li>
            <li>Address proof</li>
            <li>Education &amp; experience documents</li>
            <li>A passport-size photo</li>
            <li>Bank details (account number &amp; IFSC) + a cancelled cheque</li>
          </ul>
          <a href="{url}" style="{_BTN_STYLE}">Upload my documents</a>
          <p style="margin:18px 0 0;font-size:12px;color:#555;line-height:1.6;">
            Please use original, clearly readable files. Your information is stored securely and used
            only for verification.
          </p>
        """

    if template == "offer_shortlisted":
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
          <p style="margin:0 0 14px;font-size:13.5px;color:#444;line-height:1.55;">
            Great news — after your interview, you&apos;ve been
            <strong style="color:#0a7d4f;">shortlisted for the {pos} role</strong> at
            Optiminastic! ✨ The panel was impressed, and you&apos;re among the final
            candidates being considered.
          </p>
          <p style="margin:0 0 16px;font-size:13.5px;color:#444;line-height:1.55;">
            To help us move quickly on the final step, please <strong>confirm your availability to
            join</strong> — just reply to this email with your <strong>earliest joining date</strong>
            and any notice period you need to serve.
          </p>
          <div style="margin:0;padding:14px 16px;background:#f4f5f7;border-radius:10px;">
            <p style="margin:0;font-size:13px;color:#111827;">
              Reply with: your joining date, current notice period, and any questions you have for us.
            </p>
          </div>
          <p style="margin:16px 0 0;font-size:13px;color:#444;line-height:1.55;">
            We&apos;ll be in touch shortly with the outcome. Thank you for your patience.
          </p>
        """

    if template == "offer_selected":
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
          <p style="margin:0 0 14px;font-size:13.5px;color:#444;line-height:1.55;">
            We&apos;re delighted to let you know that you&apos;ve been
            <strong style="color:#0a7d4f;">selected for the {pos} role</strong> at
            Optiminastic! 🎉
          </p>
          <p style="margin:0 0 16px;font-size:13.5px;color:#444;line-height:1.55;">
            To move forward, please <strong>confirm your availability to join</strong> — just reply
            to this email with your <strong>earliest joining date</strong> and any notice period you
            need to serve.
          </p>
          <div style="margin:0;padding:14px 16px;background:#f4f5f7;border-radius:10px;">
            <p style="margin:0;font-size:13px;color:#111827;">
              Reply with: your joining date, current notice period, and any questions you have for us.
            </p>
          </div>
          <p style="margin:16px 0 0;font-size:13px;color:#444;line-height:1.55;">
            We&apos;re thrilled to have you on the team and look forward to hearing back from you.
          </p>
        """

    if template == "assignment_invite":
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
          <p style="margin:0 0 18px;font-size:13.5px;color:#444;line-height:1.55;">
            As the next step for <strong>{pos}</strong>, here&apos;s your
            <strong>take-home assignment</strong>. Open the link below to read the brief, complete
            the task, and upload your work before the deadline.
          </p>
          <a href="{url}" style="{_BTN_STYLE}">Open Assignment</a>
          <p style="margin:18px 0 0;font-size:12.5px;color:#555;line-height:1.6;">
            There&apos;s no timer — take your time and submit your best work. If you have any
            questions about the brief, just reply to this email.
          </p>
        """

    if template in ("iq_invite", "assessment_invite"):
        when_block = (
            f"""<div style="padding:14px 16px;background:#f4f5f7;border-radius:10px;margin:0 0 16px;">
                  <p style="margin:0;font-size:13px;color:#111827;"><strong>Scheduled for:</strong> {html.escape(when_ist)}</p>
                </div>"""
            if when_ist
            else ""
        )
        is_iq = template == "iq_invite"
        what = (
            "our online IQ test — multiple-choice logical reasoning questions"
            if is_iq
            else f"your role-specific <strong>{pos}</strong> assessment — multiple-choice questions"
        )
        btn = "Start IQ Test" if is_iq else "Start Assessment"
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
          <p style="margin:0 0 18px;font-size:13.5px;color:#444;line-height:1.55;">
            As the next step for <strong>{pos}</strong>, please complete {what}
            with a <strong>{dur}-minute</strong> time limit.
          </p>
          {when_block}
          <a href="{url}" style="{_BTN_STYLE}">{btn}</a>
          <p style="margin:18px 0 0;font-size:12.5px;font-weight:bold;color:#111827;">Before you begin:</p>
          {_RULES_HTML}
        """

    if template == "iq_passed":
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
          <p style="margin:0 0 12px;font-size:13.5px;color:#444;line-height:1.55;">
            Congratulations — you <strong style="color:#0a7d4f;">cleared the IQ round</strong>
            with a score of <strong>{html.escape(score or '')}</strong>! 🎉
          </p>
          <p style="margin:0 0 18px;font-size:13.5px;color:#444;line-height:1.55;">
            Your next step is a <strong>take-home {pos} assignment</strong>. Open the link below to
            read the brief, complete the task, and upload your work before the deadline.
          </p>
          <a href="{url}" style="{_BTN_STYLE}">Open Assignment</a>
          <p style="margin:18px 0 0;font-size:12.5px;color:#555;line-height:1.6;">
            Take your time and submit your best work — there&apos;s no timer. If you have any
            questions about the brief, just reply to this email.
          </p>
        """

    if template == "iq_failed":
        outcome = (
            f"Unfortunately your score of <strong>{html.escape(score)}</strong> did not meet the "
            "qualifying bar for this round, and we won't be moving forward at this time."
            if score
            else (
                "Unfortunately we are unable to move forward with your application at this time, "
                "as the test could not be accepted."
            )
        )
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
          <p style="margin:0 0 12px;font-size:13.5px;color:#444;line-height:1.55;">
            Thank you for taking the time to complete our IQ test for <strong>{pos}</strong>.
          </p>
          <p style="margin:0 0 12px;font-size:13.5px;color:#444;line-height:1.55;">
            {outcome}
          </p>
          <p style="margin:0;font-size:13.5px;color:#444;line-height:1.55;">
            We genuinely appreciate your interest and encourage you to apply again in the future.
          </p>
        """

    if template == "assessment_passed":
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
          <p style="margin:0 0 12px;font-size:13.5px;color:#444;line-height:1.55;">
            Excellent work — you <strong style="color:#0a7d4f;">cleared the {pos} assessment</strong>
            with a score of <strong>{html.escape(score or '')}</strong>! 🎉
          </p>
          <p style="margin:0 0 16px;font-size:13.5px;color:#444;line-height:1.55;">
            The final step is an <strong>interview with our panel</strong>. Our team will reach out
            shortly to schedule it.
          </p>
          <div style="margin:0;padding:16px;background:#fdeef3;border:1px solid #f3c6d8;border-radius:10px;">
            <p style="margin:0 0 6px;font-size:14px;font-weight:bold;color:#111827;">
              📍 The interview is conducted in person at our office.
            </p>
            <p style="margin:0 0 12px;font-size:13px;color:#444;">{html.escape(settings.office_address)}</p>
            <a href="{html.escape(settings.office_maps_url, quote=True)}" style="{_BTN_STYLE}">View on Google Maps</a>
          </div>
        """

    # assessment_failed (default fallback)
    outcome = (
        f"Unfortunately your score of <strong>{html.escape(score)}</strong> did not meet the "
        "qualifying bar, and we won't be moving forward at this time."
        if score
        else (
            "Unfortunately we are unable to move forward with your application at this time, "
            "as the assessment could not be accepted."
        )
    )
    return f"""
      <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
      <p style="margin:0 0 12px;font-size:13.5px;color:#444;line-height:1.55;">
        Thank you for completing the <strong>{pos}</strong> assessment.
      </p>
      <p style="margin:0 0 12px;font-size:13.5px;color:#444;line-height:1.55;">
        {outcome}
      </p>
      <p style="margin:0;font-size:13.5px;color:#444;line-height:1.55;">
        We genuinely appreciate the effort you put in and encourage you to apply again in the future.
      </p>
    """


def _wrap_branded(inner: str) -> str:
    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:28px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                 style="max-width:600px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e6e8ec;box-shadow:0 1px 3px rgba(17,24,39,0.04);">
            <tr>
{_brand_header()}
            </tr>
            <tr><td style="padding:0 28px;"><div style="height:1px;background:#eceef1;line-height:1px;font-size:0;">&nbsp;</div></td></tr>
            <tr>
              <td style="padding:22px 28px 28px;">
                {inner}
                <p style="margin:26px 0 0;font-size:12px;color:#999;">— The Optiminastic HR Team</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def send_test_email(
    settings: Settings,
    to: str,
    candidate_name: str,
    template: str,
    test_url: str | None = None,
    position: str | None = None,
    score: str | None = None,
    duration_min: int | None = None,
    date_time_iso: str | None = None,
    salary: str | None = None,
) -> bool:
    """Send one of the test-pipeline emails. Logs failures, never raises."""
    try:
        when_ist = _format_ist(date_time_iso) if date_time_iso else None
        inner = _test_email_body_html(
            settings,
            template,
            candidate_name,
            test_url=test_url,
            position=position,
            score=score,
            duration_min=duration_min,
            when_ist=when_ist,
            salary=salary,
        )
        text_fallback = (
            f"Hi {candidate_name},\n\n"
            f"{test_email_subject(template, position)}.\n"
            + (f"Test link: {test_url}\n" if test_url else "")
            + (f"Score: {score}\n" if score else "")
            + "\n— The Optiminastic HR Team"
        )

        msg = EmailMessage()
        msg["Subject"] = test_email_subject(template, position)
        msg["From"] = f"{settings.smtp_from_name} <{settings.from_address}>"
        msg["To"] = to
        msg.set_content(text_fallback)
        msg.add_alternative(_wrap_branded(inner), subtype="html")

        _deliver(settings, msg)
        logger.info("Test email '%s' sent to %s.", template, to)
        return True
    except Exception:  # noqa: BLE001 - never propagate; report failure to caller
        logger.exception("Failed to send test email '%s' to %s.", template, to)
        return False


def _wrap_custom(inner: str) -> str:
    """Branded shell WITHOUT the auto HR-team footer (custom bodies sign off themselves)."""
    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:28px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                 style="max-width:600px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e6e8ec;box-shadow:0 1px 3px rgba(17,24,39,0.04);">
            <tr>
{_brand_header()}
            </tr>
            <tr><td style="padding:0 28px;"><div style="height:1px;background:#eceef1;line-height:1px;font-size:0;">&nbsp;</div></td></tr>
            <tr>
              <td style="padding:22px 28px 28px;font-size:13.5px;color:#1a1a1a;line-height:1.6;">
                {inner}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def _ics_escape(value: str) -> str:
    """Escape a value for an iCalendar TEXT field (RFC 5545)."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _ics_dt(value_iso: str) -> str:
    """A naive/aware ISO timestamp -> UTC 'YYYYMMDDTHHMMSSZ'.

    Naive datetime-local strings (no tz) are treated as IST, matching how the
    calendar/email layer formats appointment times."""
    dt = datetime.fromisoformat(value_iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_IST)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _build_ics(
    *,
    uid: str,
    summary: str,
    description: str,
    location: str,
    start_iso: str,
    duration_min: int,
    organizer_email: str,
    organizer_name: str,
    attendees: list[str],
) -> str:
    """A METHOD:REQUEST VCALENDAR so recipients get a real Google Calendar invite."""
    start = _ics_dt(start_iso)
    end_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=_IST)
    end = (end_dt + timedelta(minutes=max(duration_min or 45, 1))).astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Optiminastic//Interview//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{_ics_escape(uid)}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start}",
        f"DTEND:{end}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        f"LOCATION:{_ics_escape(location)}",
        f"ORGANIZER;CN={_ics_escape(organizer_name)}:mailto:{organizer_email}",
    ]
    for email in attendees:
        if not email or not email.strip():
            continue
        lines.append(
            "ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:"
            f"mailto:{email.strip()}"
        )
    lines += ["STATUS:CONFIRMED", "SEQUENCE:0", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


# Inline link token: ``[[Label|https://url]]`` -> a clickable anchor in the
# email body. Lets HR-composed bodies show a labelled link (e.g. "View map")
# instead of pasting a raw URL.
_INLINE_LINK_RE = re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]")


def _render_body_html(body: str) -> str:
    """HTML-escape a plain-text body, turning ``[[Label|url]]`` tokens into
    anchors. Escaping is done per-segment so the token URL/label aren't
    double-escaped."""
    parts: list[str] = []
    last = 0
    for m in _INLINE_LINK_RE.finditer(body):
        parts.append(html.escape(body[last : m.start()]))
        label = html.escape(m.group(1).strip())
        url = html.escape(m.group(2).strip(), quote=True)
        parts.append(
            f'<a href="{url}" style="color:#9a1f33;font-weight:bold;'
            f'text-decoration:underline;">{label}</a>'
        )
        last = m.end()
    parts.append(html.escape(body[last:]))
    return "".join(parts).replace("\n", "<br/>")


def _strip_link_tokens(body: str) -> str:
    """Plain-text fallback form of ``[[Label|url]]`` -> ``Label: url``."""
    return _INLINE_LINK_RE.sub(lambda m: f"{m.group(1).strip()}: {m.group(2).strip()}", body)


def send_custom_email(
    settings: Settings,
    to: str,
    subject: str,
    body: str,
    cc: list[str] | None = None,
    *,
    event_start_iso: str | None = None,
    event_duration_min: int = 45,
    event_summary: str | None = None,
    event_location: str | None = None,
    event_description: str | None = None,
    organizer_email: str | None = None,
    organizer_name: str | None = None,
    attendees: list[str] | None = None,
    event_uid: str | None = None,
    links: list[dict[str, str]] | None = None,
) -> bool:
    """Send an HR-composed (and possibly edited) email, wrapped in the branded
    shell. When event details are supplied, a Google Calendar invite (.ics,
    METHOD:REQUEST) is attached so the event lands on every attendee's calendar.
    `links` are rendered as labelled buttons (HTML) / "Label: url" (plain text).
    Logs failures, never raises (runs inside a BackgroundTask)."""
    try:
        inner = _render_body_html(body)
        text_body = _strip_link_tokens(body)

        valid_links = [l for l in (links or []) if l.get("url") and l.get("label")]
        if valid_links:
            buttons = "".join(
                f'<a href="{html.escape(l["url"], quote=True)}" '
                'style="display:inline-block;margin:6px 8px 0 0;padding:10px 16px;'
                'background:#9a1f33;color:#ffffff;text-decoration:none;border-radius:8px;'
                f'font-size:13px;font-weight:bold;">{html.escape(l["label"])}</a>'
                for l in valid_links
            )
            inner += f'<div style="margin-top:18px;">{buttons}</div>'
            text_body += "\n\n" + "\n".join(f'{l["label"]}: {l["url"]}' for l in valid_links)

        recipients = [to] + [c for c in (cc or []) if c.strip()]

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"{settings.smtp_from_name} <{settings.from_address}>"
        msg["To"] = to
        if cc:
            msg["Cc"] = ", ".join(c.strip() for c in cc if c.strip())
        msg.set_content(text_body)
        msg.add_alternative(_wrap_custom(inner), subtype="html")

        if event_start_iso:
            ics = _build_ics(
                uid=event_uid or f"{event_start_iso}-{to}",
                summary=event_summary or subject,
                description=event_description or body,
                location=event_location or "",
                start_iso=event_start_iso,
                duration_min=event_duration_min,
                organizer_email=organizer_email or settings.from_address,
                organizer_name=organizer_name or settings.smtp_from_name,
                attendees=attendees or [to],
            )
            msg.add_attachment(
                ics.encode("utf-8"),
                maintype="text",
                subtype="calendar",
                filename="invite.ics",
                params={"method": "REQUEST", "name": "invite.ics"},
            )

        _deliver(settings, msg, to_addrs=recipients)
        logger.info("Custom email sent to %s.", to)
        return True
    except Exception:  # noqa: BLE001 - never propagate; report failure to caller
        logger.exception("Failed to send custom email to %s.", to)
        return False


def send_schedule_email(
    settings: Settings,
    to: str,
    candidate_name: str,
    schedule_type: str,
    date_time_iso: str,
    notes: str | None = None,
) -> bool:
    """Compose and deliver the schedule notification. Logs failures, never raises."""
    try:
        when_ist = _format_ist(date_time_iso)
        msg = EmailMessage()
        msg["Subject"] = subject_for(schedule_type)
        msg["From"] = f"{settings.smtp_from_name} <{settings.from_address}>"
        msg["To"] = to
        msg.set_content(
            _build_text(
                schedule_type, candidate_name, when_ist,
                settings.office_address, settings.office_maps_url, notes,
            )
        )
        msg.add_alternative(
            _build_html(
                schedule_type, candidate_name, when_ist,
                settings.office_address, settings.office_maps_url, notes,
            ),
            subtype="html",
        )

        _deliver(settings, msg)
        logger.info("Schedule email (%s) sent to %s.", schedule_type, to)
        return True
    except Exception:  # noqa: BLE001 - never propagate; report failure to caller
        logger.exception("Failed to send schedule email (%s) to %s.", schedule_type, to)
        return False
