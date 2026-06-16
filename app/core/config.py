"""Application configuration (12-factor, env-driven).

Single source of truth for settings; everything else depends on the `Settings`
abstraction rather than reading env vars directly (DIP).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Optiminastic × Circle HRMS API"
    app_version: str = "2.0.0"
    log_level: str = "INFO"

    # PostgreSQL connection string. Required to serve data.
    database_url: str = ""
    auto_create_tables: bool = True

    # DB pool tuning (scalability). pool_size is sized to the post-login
    # prefetch burst so overflow connections (which are torn down on check-in
    # and pay a fresh TLS handshake next time) are rarely needed.
    db_pool_size: int = 12
    db_max_overflow: int = 8
    db_pool_timeout: int = 30
    # Recycle pooled connections before the server/pooler idle-closes them
    # (Neon's pooler kills idle server connections after ~5 min).
    db_pool_recycle: int = 240

    # NoDecode: skip pydantic-settings' JSON decoding so the validator can accept
    # a plain comma-separated string from .env.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000", "http://localhost:3001"]

    # Backblaze B2 (S3-compatible) object storage.
    b2_key_id: str = ""
    b2_application_key: str = ""
    b2_endpoint: str = ""
    b2_region: str = ""
    b2_bucket: str = ""
    max_upload_mb: int = 15

    # SMTP for candidate notification emails. Works with Gmail (user = the full
    # address, also the From) or providers like SendGrid (user = "apikey",
    # password = the API key, and a distinct verified SMTP_FROM_EMAIL sender).
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""  # login username (Gmail address, or literally "apikey")
    smtp_password: str = ""  # Gmail app password, or the provider API key
    smtp_from_email: str = ""  # verified sender; falls back to smtp_user (Gmail)
    smtp_from_name: str = "Optiminastic HR Team"
    smtp_reply_to: str = ""  # optional Reply-To (e.g. hr@optiminastic.com)
    # Resend HTTP API key (used INSTEAD of SMTP when set — Render free tier blocks
    # outbound SMTP). Auto-derived from the Resend SMTP password if not set.
    resend_api_key: str = ""
    # SendGrid HTTP API (used INSTEAD of SMTP when set — required on hosts that
    # block outbound SMTP, e.g. Render free tier). Send over HTTPS, no SMTP ports.
    # Requires a verified sender/domain in SendGrid and SMTP_FROM_EMAIL set to it.
    sendgrid_api_key: str = ""

    # Office location for offline rounds (IQ Test / Assessment / Interview).
    office_address: str = "Optiminastic Office (set OFFICE_ADDRESS in .env)"
    office_maps_url: str = "https://maps.google.com/?q=Optiminastic"

    # Google Calendar (single shared HR account, one-way push). The OAuth client
    # id/secret come from a Google Cloud "Web application" credential; the shared
    # account's refresh token is obtained via the in-app connect flow and stored
    # in the DB (never in env). See app/services/google_calendar.py.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/calendar/oauth/callback"
    google_calendar_id: str = "primary"  # which calendar events are written to
    # Where the OAuth callback redirects the browser back to (the Settings page).
    frontend_url: str = "http://localhost:3001"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def sqlalchemy_url(self) -> str:
        """Normalize the URL to the psycopg (v3) driver SQLAlchemy expects."""
        url = self.database_url.strip()
        if url.startswith("postgresql+"):
            return url
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://"):]
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://"):]
        return url

    @property
    def has_database(self) -> bool:
        return bool(self.database_url.strip())

    @property
    def has_storage(self) -> bool:
        return bool(self.b2_key_id and self.b2_bucket and self.b2_endpoint)

    @property
    def sendgrid_key(self) -> str:
        """The SendGrid API key for the HTTPS transport.

        Uses SENDGRID_API_KEY if set, otherwise reuses the SMTP password when the
        SMTP config already points at SendGrid (host smtp.sendgrid.net, user
        'apikey'). This lets an existing SendGrid-over-SMTP setup switch to the
        API automatically on hosts that block SMTP — no new env var needed.
        """
        if self.sendgrid_api_key:
            return self.sendgrid_api_key
        if "sendgrid" in self.smtp_host.lower() and self.smtp_user.lower() == "apikey":
            return self.smtp_password
        return ""

    @property
    def resend_key(self) -> str:
        """The Resend API key for the HTTPS transport.

        Uses RESEND_API_KEY if set, otherwise reuses the SMTP password when the
        SMTP config points at Resend (host smtp.resend.com, user 'resend'). Lets
        an existing Resend-over-SMTP setup switch to the API automatically on
        hosts that block SMTP (e.g. Render) — no new env var needed.
        """
        if self.resend_api_key:
            return self.resend_api_key
        if "resend" in self.smtp_host.lower() and self.smtp_user.lower() == "resend":
            return self.smtp_password
        return ""

    @property
    def has_smtp(self) -> bool:
        # True when ANY email transport is configured (Resend/SendGrid HTTP or SMTP).
        return bool(self.resend_key or self.sendgrid_key or (self.smtp_user and self.smtp_password))

    @property
    def from_address(self) -> str:
        """The envelope From email. Defaults to smtp_user for Gmail, where the
        login address is also the sender; SendGrid needs a verified sender."""
        return self.smtp_from_email or self.smtp_user

    @property
    def has_google(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
