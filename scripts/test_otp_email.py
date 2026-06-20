"""Manual check: send a sample OTP email through the configured transport.

This bypasses the API route and the database — it calls send_otp_email directly,
so it isolates "does the OTP email itself send?" from the rest of the flow.

Run from the circle-be project root (with the venv active and .env present):

    python scripts/test_otp_email.py you@example.com

If it prints `sent: True` and the mail arrives, the email function works and any
"no OTP" problem is in the API path (endpoint not running the new code, or the
email_otps table missing). If `sent: False`, the backend log prints the exact
SendGrid/SMTP error.
"""

from __future__ import annotations

import sys

from app.core.config import get_settings
from app.services.email_sender import send_otp_email


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_otp_email.py <recipient-email>")
        raise SystemExit(2)

    to = sys.argv[1].strip()
    settings = get_settings()
    print(f"has_smtp = {settings.has_smtp}")
    print(f"from     = {settings.from_address}")
    print(f"resend   = {bool(settings.resend_key)} | sendgrid = {bool(settings.sendgrid_key)}")
    if not settings.has_smtp:
        print("No email transport configured in .env — nothing to send.")
        raise SystemExit(1)

    ok = send_otp_email(settings, to, "1234")
    print(f"sent: {ok}  -> check the inbox/spam for {to}")
    if not ok:
        print("Look just above for the logged 'Failed to send OTP email' traceback.")


if __name__ == "__main__":
    main()
