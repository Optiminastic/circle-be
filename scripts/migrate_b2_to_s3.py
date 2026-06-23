"""One-time migration of all objects from Backblaze B2 to AWS S3.

Copies every object from the B2 source bucket to the AWS S3 destination bucket,
applying the app's configured key prefix (AWS_S3_PREFIX, e.g. "Circle") so the
migrated files match where the app now reads/writes them.

Source (B2) credentials are read from ENV VARS so no secrets live in this file:
    B2_KEY_ID, B2_APPLICATION_KEY, B2_ENDPOINT, B2_BUCKET
Destination (AWS S3) is taken from the app settings (AWS_* in .env).

Run from the circle-be root with the venv active. PowerShell:

    $env:B2_KEY_ID="<old key id>"; $env:B2_APPLICATION_KEY="<old app key>"
    $env:B2_ENDPOINT="https://s3.us-east-005.backblazeb2.com"; $env:B2_BUCKET="curcle-documents"
    .\.venv\Scripts\python.exe scripts\migrate_b2_to_s3.py            # or add --dry-run

Idempotent: an object already in the destination with the same size is skipped,
so you can re-run safely (e.g. after fixing IAM permissions partway through).
"""

from __future__ import annotations

import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"Missing required env var {name} (set the source B2 credentials).")
        raise SystemExit(2)
    return val


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    # --- Source: Backblaze B2 (S3-compatible) ---
    src = boto3.client(
        "s3",
        endpoint_url=_require_env("B2_ENDPOINT"),
        aws_access_key_id=_require_env("B2_KEY_ID"),
        aws_secret_access_key=_require_env("B2_APPLICATION_KEY"),
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )
    src_bucket = _require_env("B2_BUCKET")

    # --- Destination: AWS S3 (from the app's .env) ---
    s = get_settings()
    if not s.has_storage:
        print("AWS S3 is not configured in .env (AWS_* vars). Aborting.")
        raise SystemExit(1)
    prefix = s.aws_s3_prefix.strip().strip("/")
    dst = boto3.client(
        "s3",
        endpoint_url=s.aws_s3_endpoint or None,
        aws_access_key_id=s.aws_access_key_id,
        aws_secret_access_key=s.aws_secret_access_key,
        region_name=s.aws_region,
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )
    dst_bucket = s.aws_bucket_name

    def dst_key(key: str) -> str:
        return f"{prefix}/{key}" if prefix else key

    def already_there(key: str, size: int) -> bool:
        try:
            head = dst.head_object(Bucket=dst_bucket, Key=key)
            return int(head["ContentLength"]) == size
        except ClientError:
            return False

    print(f"Source : B2 bucket {src_bucket}")
    print(f"Dest   : S3 bucket {dst_bucket} (prefix={prefix or '<none>'})")
    print(f"Mode   : {'DRY RUN' if dry_run else 'COPY'}\n")

    copied = skipped = failed = 0
    paginator = src.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=src_bucket):
        for obj in page.get("Contents", []):
            key, size = obj["Key"], int(obj["Size"])
            dk = dst_key(key)
            if already_there(dk, size):
                skipped += 1
                continue
            if dry_run:
                print(f"[dry] {key} -> {dk} ({size} bytes)")
                copied += 1
                continue
            try:
                body = src.get_object(Bucket=src_bucket, Key=key)
                data = body["Body"].read()
                ctype = body.get("ContentType") or "application/octet-stream"
                dst.put_object(Bucket=dst_bucket, Key=dk, Body=data, ContentType=ctype)
                copied += 1
                if copied % 25 == 0:
                    print(f"...copied {copied}")
            except Exception as exc:  # noqa: BLE001 - report and continue
                failed += 1
                print(f"FAILED {key}: {exc}")

    print(f"\nDone. copied={copied} skipped={skipped} failed={failed}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
