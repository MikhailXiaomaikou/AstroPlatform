"""HMAC capability shared by the demo API shim and localhost object fixture."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass


MAX_DEMO_ARTIFACT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ArtifactCapability:
    key: str
    sha256: str
    size_bytes: int
    content_type: str
    expires_at: int

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            [
                self.key,
                self.sha256,
                self.size_bytes,
                self.content_type,
                self.expires_at,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")


def encode_key(key: str) -> str:
    return base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")


def decode_key(token: str) -> str:
    if not token or len(token) > 4096:
        raise ValueError("invalid_artifact_key_token")
    padding = "=" * (-len(token) % 4)
    try:
        payload = base64.b64decode(
            token + padding,
            altchars=b"-_",
            validate=True,
        )
        return payload.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid_artifact_key_token") from exc


def sign_capability(secret: str, capability: ArtifactCapability) -> str:
    if not secret:
        raise ValueError("missing_demo_artifact_secret")
    return hmac.new(
        secret.encode("utf-8"),
        capability.canonical_bytes(),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    secret: str,
    capability: ArtifactCapability,
    signature: str,
) -> bool:
    if len(signature) != 64 or any(ch not in "0123456789abcdef" for ch in signature):
        return False
    return hmac.compare_digest(sign_capability(secret, capability), signature)
