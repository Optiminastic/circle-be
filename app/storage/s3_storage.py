"""Backblaze B2 (S3-compatible) implementation of FileStorage.

Uploads are proxied through the backend; downloads are served as short-lived
presigned URLs so the bucket stays private. All boto errors are normalized to
StorageError (error tolerance).
"""

from __future__ import annotations

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.core.errors import StorageError
from app.core.logging import get_logger

logger = get_logger("curcle.storage")


class S3FileStorage:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.b2_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.b2_endpoint,
            aws_access_key_id=settings.b2_key_id,
            aws_secret_access_key=settings.b2_application_key,
            region_name=settings.b2_region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
        except (BotoCoreError, ClientError) as exc:
            logger.exception("put_object failed for %s", key)
            raise StorageError("Failed to store the file") from exc

    def get(self, key: str) -> tuple[bytes, str]:
        """Read an object's bytes and stored content type (for inline streaming)."""
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            data = resp["Body"].read()
            content_type = resp.get("ContentType") or "application/octet-stream"
            return data, content_type
        except (BotoCoreError, ClientError) as exc:
            logger.exception("get_object failed for %s", key)
            raise StorageError("Failed to read the file") from exc

    def presigned_url(
        self,
        key: str,
        expires: int = 900,
        *,
        disposition: str | None = None,
        content_type: str | None = None,
    ) -> str:
        # ResponseContentDisposition/Type override the response headers when the
        # URL is fetched — used to force inline preview instead of a download.
        params: dict[str, str] = {"Bucket": self._bucket, "Key": key}
        if disposition:
            params["ResponseContentDisposition"] = disposition
        if content_type:
            params["ResponseContentType"] = content_type
        try:
            return self._client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires)
        except (BotoCoreError, ClientError) as exc:
            logger.exception("presign failed for %s", key)
            raise StorageError("Failed to create a download link") from exc

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            logger.exception("delete_object failed for %s", key)
            raise StorageError("Failed to delete the file") from exc
