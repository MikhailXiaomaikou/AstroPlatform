"""Demo-only ASGI shim that issues localhost artifact-upload capabilities.

Production continues to use :mod:`app.main` directly and therefore cannot
activate this code path. The shim exists only so the laptop recorder can test
the direct-upload, server re-download, hash verification, and promotion flow
without pretending that local files are S3/R2.
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlencode

from app.api import worker_control
from app.config import settings
from app.storage import normalize_storage_key

from artifact_capability import (
    MAX_DEMO_ARTIFACT_BYTES,
    ArtifactCapability,
    encode_key,
    sign_capability,
)


def _create_local_demo_upload_url(
    path: str,
    *,
    sha256: str,
    size_bytes: int,
    content_type: str = "application/octet-stream",
    expires_seconds: int = 15 * 60,
) -> dict[str, object]:
    environment = os.environ.get("ENV", "").strip().lower()
    if environment != "dev" or str(settings.storage_backend).lower() != "local":
        raise RuntimeError("The localhost artifact fixture is development-only")
    base_url = os.environ.get("DEMO_ARTIFACT_UPLOAD_BASE_URL", "").strip().rstrip("/")
    secret = os.environ.get("DEMO_ARTIFACT_UPLOAD_SECRET", "")
    if not base_url.startswith("http://127.0.0.1:") or not secret:
        raise RuntimeError("The localhost artifact fixture is not configured")

    key = normalize_storage_key(path)
    digest = str(sha256 or "").strip().lower().removeprefix("sha256:")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("Artifact SHA-256 must contain 64 hexadecimal characters")
    declared_size = int(size_bytes)
    if declared_size <= 0 or declared_size > MAX_DEMO_ARTIFACT_BYTES:
        raise ValueError("Demo artifact size is outside the supported range")
    media_type = str(content_type or "application/octet-stream").strip()[:255]
    if not media_type or any(ord(ch) < 32 for ch in media_type):
        raise ValueError("Demo artifact content type is invalid")
    lifetime = max(60, min(int(expires_seconds), 60 * 60))
    capability = ArtifactCapability(
        key=key,
        sha256=digest,
        size_bytes=declared_size,
        content_type=media_type,
        expires_at=int(time.time()) + lifetime,
    )
    query = urlencode(
        {
            "sha256": capability.sha256,
            "size_bytes": str(capability.size_bytes),
            "content_type": capability.content_type,
            "expires_at": str(capability.expires_at),
            "signature": sign_capability(secret, capability),
        }
    )
    return {
        "method": "PUT",
        "url": f"{base_url}/upload/{encode_key(key)}?{query}",
        "artifact_ref": key,
        "expires_in": lifetime,
        "headers": {
            "Content-Type": media_type,
            "Content-Length": str(declared_size),
        },
    }


worker_control.create_presigned_upload_url = _create_local_demo_upload_url

from app.main import app  # noqa: E402  imported after the demo-only patch


__all__ = ["app"]
