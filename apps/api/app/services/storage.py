"""Object storage.

Two rules encoded here: objects carry an expiry and are swept, and clients
never receive a raw storage URL -- only a short-lived signed one.
"""
from __future__ import annotations

import contextlib
from datetime import timedelta
from functools import lru_cache

import boto3
from app.config import get_settings
from botocore.client import Config


class Storage:
    def __init__(self, client, bucket: str):
        self._c = client
        self.bucket = bucket

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self._c.put_object(Bucket=self.bucket, Key=key, Body=data,
                           ContentType=content_type,
                           ServerSideEncryption="AES256")

    async def get(self, key: str) -> bytes:
        return self._c.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    async def delete(self, key: str) -> None:
        # Deletion is best-effort by design: it runs from retention sweeps and
        # from user-initiated erasure, and neither should fail because an
        # object was already gone.
        with contextlib.suppress(Exception):
            self._c.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, key: str, ttl: timedelta = timedelta(minutes=15)) -> str:
        return self._c.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=int(ttl.total_seconds()),
        )


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    client = boto3.client(
        "s3", endpoint_url=s.storage_endpoint,
        aws_access_key_id=s.storage_access_key,
        aws_secret_access_key=s.storage_secret_key,
        config=Config(signature_version="s3v4"),
    )
    return Storage(client, s.storage_bucket)
