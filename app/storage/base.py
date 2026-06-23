"""Object-storage abstraction (DIP).

Routes depend on this Protocol, not on boto3/S3 — swapping providers (or using a
fake in tests) means another implementation, no route changes.
"""

from __future__ import annotations

from typing import Protocol


class FileStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> tuple[bytes, str]: ...

    def presigned_url(
        self,
        key: str,
        expires: int = 900,
        *,
        disposition: str | None = None,
        content_type: str | None = None,
    ) -> str: ...

    def delete(self, key: str) -> None: ...
