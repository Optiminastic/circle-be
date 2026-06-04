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

    def presigned_url(self, key: str, expires: int = 900) -> str:
        try:
            return self._client.generate_presigned_url(
                "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires
            )
        except (BotoCoreError, ClientError) as exc:
            logger.exception("presign failed for %s", key)
            raise StorageError("Failed to create a download link") from exc

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            logger.exception("delete_object failed for %s", key)
            raise StorageError("Failed to delete the file") from exc
