from uuid import uuid4

import boto3
from botocore.client import BaseClient
from fastapi import HTTPException

from app.core.config import settings

ALLOWED_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png", "text/csv"}


def create_s3_client() -> BaseClient:
    if not settings.aws_region:
        raise RuntimeError("AWS_REGION must be configured for S3 operations")
    # Uses the default AWS credential chain (ECS task role in the cloud).
    return boto3.client("s3", region_name=settings.aws_region)


def create_upload_form(
    tenant_id: str,
    content_type: str,
    *,
    object_key: str | None = None,
    exact_size: int | None = None,
    s3_client: BaseClient | None = None,
) -> dict:
    """Create a short-lived private upload form under an authorized tenant prefix.

    Callers must resolve tenant_id from an active server-side membership and authorize
    the target record before issuing this URL. A tenant prefix is never authorization.
    """
    if not settings.s3_bucket:
        raise HTTPException(status_code=503, detail="Document storage is not configured")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported file type")
    key = object_key or f"tenants/{tenant_id}/{uuid4()}"
    if not key.startswith(f"tenants/{tenant_id}/"):
        raise HTTPException(status_code=400, detail="Object key is outside the tenant prefix")
    size_condition = (
        ["content-length-range", exact_size, exact_size]
        if exact_size is not None
        else ["content-length-range", 1, settings.max_upload_bytes]
    )
    client = s3_client or create_s3_client()
    return client.generate_presigned_post(
        Bucket=settings.s3_bucket,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, size_condition],
        ExpiresIn=300,
    )


def create_download_url(tenant_id: str, authorized_object_key: str, *, s3_client: BaseClient | None = None) -> str:
    """Sign a private download after the caller has authorized the object record."""
    if not settings.s3_bucket:
        raise HTTPException(status_code=503, detail="Document storage is not configured")
    if not authorized_object_key.startswith(f"tenants/{tenant_id}/"):
        raise HTTPException(status_code=404, detail="File not found")
    client = s3_client or create_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": authorized_object_key},
        ExpiresIn=300,
    )


def verify_uploaded_object(
    object_key: str,
    content_type: str,
    file_size: int,
    *,
    s3_client: BaseClient | None = None,
) -> None:
    """Confirm the pending upload exists and matches the recorded metadata."""
    if not settings.s3_bucket:
        raise HTTPException(status_code=503, detail="Document storage is not configured")
    client = s3_client or create_s3_client()
    try:
        metadata = client.head_object(Bucket=settings.s3_bucket, Key=object_key)
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Uploaded object is not available for verification") from exc
    if metadata.get("ContentLength") != file_size or metadata.get("ContentType") != content_type:
        raise HTTPException(status_code=409, detail="Uploaded object does not match the registered file metadata")
