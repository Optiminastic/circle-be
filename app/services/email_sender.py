"""Candidate notification emails (Gmail SMTP).

Pure composition + transport — no FastAPI imports. Designed to run inside a
BackgroundTask: failures are logged, never raised (a schedule must never break
because an email could not be delivered).
"""

from __future__ import annotations

import html
import smtplib
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger("curcle.email")

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
        return "Your HR call with Curcle is scheduled"
    return f"Your {schedule_type} at Curcle — details inside"


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
    lines += ["", "— The Curcle HR Team"]
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
          <div style="margin:20px 0;padding:16px;background:#fdf6ec;border:1px solid #e1d6bc;border-radius:10px;">
            <p style="margin:0 0 6px;font-size:14px;font-weight:bold;color:#212842;">
              📍 This round is conducted in person at our office.
            </p>
            <p style="margin:0 0 12px;font-size:13px;color:#444;">{safe_address}</p>
            <a href="{html.escape(office_maps_url, quote=True)}"
               style="display:inline-block;background:#212842;color:#ffffff;text-decoration:none;
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
  <body style="margin:0;padding:0;background:#f0e7d5;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f0e7d5;padding:28px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                 style="max-width:560px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid #e1d6bc;">
            <tr>
              <td style="background:#212842;padding:16px 24px;">
                <span style="color:#ffffff;font-size:15px;font-weight:bold;letter-spacing:0.4px;">Curcle HRMS</span>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 26px 30px;">
                <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
                <p style="margin:0 0 18px;font-size:13.5px;color:#444;line-height:1.55;">{intro}</p>
                <div style="padding:14px 16px;background:#f0e7d5;border-radius:10px;">
                  <p style="margin:0;font-size:13px;color:#212842;">
                    <strong>When:</strong> {html.escape(when_ist)}
                  </p>
                </div>
                {location_block}
                {notes_block}
                <p style="margin:26px 0 0;font-size:12px;color:#999;">— The Curcle HR Team</p>
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
    "display:inline-block;background:#212842;color:#ffffff;text-decoration:none;"
    "font-size:13px;font-weight:bold;padding:10px 20px;border-radius:8px;"
)

_RULES_HTML = """
  <ul style="margin:10px 0 0;padding-left:18px;font-size:12.5px;color:#555;line-height:1.7;">
    <li>The test runs in <strong>full screen, in a single tab</strong>.</li>
    <li>Switching tabs or leaving the window is flagged — <strong>3 violations auto-submit your test</strong>.</li>
    <li>The timer keeps running even if you refresh the page.</li>
    <li>Make sure you have a stable internet connection before starting.</li>
  </ul>
"""


def test_email_subject(template: str, position: str | None = None) -> str:
    subjects = {
        "iq_invite": "Your Curcle IQ Test — secure test link inside",
        "iq_passed": "Great news — you've cleared the IQ round at Curcle",
        "iq_failed": "Update on your application at Curcle",
        "assessment_invite": "Your Curcle assessment — secure test link inside",
        "assessment_passed": "You've cleared the assessment — interview is next",
        "assessment_failed": "Update on your application at Curcle",
    }
    if template == "iq_passed" and position:
        return f"You've cleared the IQ round — your {position} assessment is ready"
    return subjects.get(template, "Update from the Curcle HR Team")


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
) -> str:
    """Inner HTML for each pipeline template (greeting/footer added by caller)."""
    name = html.escape(candidate_name)
    pos = html.escape(position or "the role you applied for")
    url = html.escape(test_url or "#", quote=True)
    dur = duration_min or 60

    if template in ("iq_invite", "assessment_invite"):
        when_block = (
            f"""<div style="padding:14px 16px;background:#f0e7d5;border-radius:10px;margin:0 0 16px;">
                  <p style="margin:0;font-size:13px;color:#212842;"><strong>Scheduled for:</strong> {html.escape(when_ist)}</p>
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
          <p style="margin:18px 0 0;font-size:12.5px;font-weight:bold;color:#212842;">Before you begin:</p>
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
            Your next step is the <strong>{pos} assessment</strong> — a role-specific online test
            with a <strong>{dur}-minute</strong> time limit. The same rules apply.
          </p>
          <a href="{url}" style="{_BTN_STYLE}">Start Assessment</a>
          {_RULES_HTML}
        """

    if template == "iq_failed":
        return f"""
          <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
          <p style="margin:0 0 12px;font-size:13.5px;color:#444;line-height:1.55;">
            Thank you for taking the time to complete our IQ test for <strong>{pos}</strong>.
          </p>
          <p style="margin:0 0 12px;font-size:13.5px;color:#444;line-height:1.55;">
            Unfortunately your score of <strong>{html.escape(score or '')}</strong> did not meet the
            qualifying bar for this round, and we won't be moving forward at this time.
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
          <div style="margin:0;padding:16px;background:#fdf6ec;border:1px solid #e1d6bc;border-radius:10px;">
            <p style="margin:0 0 6px;font-size:14px;font-weight:bold;color:#212842;">
              📍 The interview is conducted in person at our office.
            </p>
            <p style="margin:0 0 12px;font-size:13px;color:#444;">{html.escape(settings.office_address)}</p>
            <a href="{html.escape(settings.office_maps_url, quote=True)}" style="{_BTN_STYLE}">View on Google Maps</a>
          </div>
        """

    # assessment_failed (default fallback)
    return f"""
      <p style="margin:0 0 14px;font-size:15px;color:#1a1a1a;">Hi {name},</p>
      <p style="margin:0 0 12px;font-size:13.5px;color:#444;line-height:1.55;">
        Thank you for completing the <strong>{pos}</strong> assessment.
      </p>
      <p style="margin:0 0 12px;font-size:13.5px;color:#444;line-height:1.55;">
        Unfortunately your score of <strong>{html.escape(score or '')}</strong> did not meet the
        qualifying bar, and we won't be moving forward at this time.
      </p>
      <p style="margin:0;font-size:13.5px;color:#444;line-height:1.55;">
        We genuinely appreciate the effort you put in and encourage you to apply again in the future.
      </p>
    """


def _wrap_branded(inner: str) -> str:
    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#f0e7d5;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f0e7d5;padding:28px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                 style="max-width:560px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid #e1d6bc;">
            <tr>
              <td style="background:#212842;padding:16px 24px;">
                <span style="color:#ffffff;font-size:15px;font-weight:bold;letter-spacing:0.4px;">Curcle HRMS</span>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 26px 30px;">
                {inner}
                <p style="margin:26px 0 0;font-size:12px;color:#999;">— The Curcle HR Team</p>
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
) -> None:
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
        )
        text_fallback = (
            f"Hi {candidate_name},\n\n"
            f"{test_email_subject(template, position)}.\n"
            + (f"Test link: {test_url}\n" if test_url else "")
            + (f"Score: {score}\n" if score else "")
            + "\n— The Curcle HR Team"
        )

        msg = EmailMessage()
        msg["Subject"] = test_email_subject(template, position)
        msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
        msg["To"] = to
        msg.set_content(text_fallback)
        msg.add_alternative(_wrap_branded(inner), subtype="html")

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        logger.info("Test email '%s' sent to %s.", template, to)
    except Exception:  # noqa: BLE001 - background task must never propagate
        logger.exception("Failed to send test email '%s' to %s.", template, to)


def send_schedule_email(
    settings: Settings,
    to: str,
    candidate_name: str,
    schedule_type: str,
    date_time_iso: str,
    notes: str | None = None,
) -> None:
    """Compose and deliver the schedule notification. Logs failures, never raises."""
    try:
        when_ist = _format_ist(date_time_iso)
        msg = EmailMessage()
        msg["Subject"] = subject_for(schedule_type)
        msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
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

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        logger.info("Schedule email (%s) sent to %s.", schedule_type, to)
    except Exception:  # noqa: BLE001 - background task must never propagate
        logger.exception("Failed to send schedule email (%s) to %s.", schedule_type, to)
