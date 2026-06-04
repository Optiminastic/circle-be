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

    app_name: str = "Curcle HRMS API"
    app_version: str = "2.0.0"
    log_level: str = "INFO"

    # PostgreSQL connection string. Required to serve data.
    database_url: str = ""
    auto_create_tables: bool = True

    # DB pool tuning (scalability).
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
