from __future__ import annotations

import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


class R2UploadError(Exception):
    pass


# Cloudflare R2 accepts path-style for native S3 calls (put_object,
# get_object, delete_object, download_fileobj) BUT returns 401
# Unauthorized on presigned URLs generated in path-style. The clean
# fix is to use virtual-hosted addressing for ALL R2 operations —
# it works for both direct calls and presigned URLs, and it is the
# modern S3 default (path-style is being deprecated).
#
# Virtual-hosted URL shape :
#   https://<bucket>.<account-id>.r2.cloudflarestorage.com/<key>
# Path-style URL shape (broken for presign on R2) :
#   https://<account-id>.r2.cloudflarestorage.com/<bucket>/<key>
#
# Signature version : R2 only accepts SigV4 for presigned URLs. Without an
# explicit `signature_version="s3v4"`, boto3 falls back to a region-dependent
# default that can produce SigV2 URLs (`?AWSAccessKeyId=...&Signature=...`),
# which R2 rejects with 401 even when the addressing style is correct.
# Lesson 2026-04-27 — `scan_debug_viewer.py` got 401 on virtual-hosted URLs
# until this flag was set explicitly.
_R2_CLIENT_CONFIG = Config(
    signature_version="s3v4",
    s3={"addressing_style": "virtual"},
)


def get_s3_client():
    """Build a boto3 S3 client targeting Cloudflare R2.

    Single source of truth for the PA service — all R2 access (uploads,
    downloads, deletes, and presigned URLs) MUST go through this helper
    so the addressing-style stays consistent.
    """
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=_R2_CLIENT_CONFIG,
    )


def upload_receipt_image(
    content: bytes, key: str, s3_client=None, content_type: str = "application/octet-stream"
) -> None:
    """Upload file bytes to R2. Raises R2UploadError on failure."""
    client = s3_client or get_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    try:
        client.put_object(Bucket=bucket, Key=key, Body=content, ContentType=content_type)
    except (BotoCoreError, ClientError) as exc:
        raise R2UploadError(f"R2 upload failed: {exc}") from exc


def upload_label_image(
    content: bytes, key: str, s3_client=None, content_type: str = "application/octet-stream"
) -> None:
    """Upload a label image to R2. No lifecycle rule — retained for training data."""
    upload_receipt_image(content, key, s3_client=s3_client, content_type=content_type)


def delete_receipt_image(key: str, s3_client=None) -> None:
    """Delete a file from R2. Logs on failure, does not raise."""
    client = s3_client or get_s3_client()
    bucket = os.environ["R2_BUCKET_NAME"]
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("R2 delete failed for key %s: %s", key, exc)
