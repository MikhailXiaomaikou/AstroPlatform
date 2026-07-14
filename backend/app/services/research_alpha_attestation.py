"""Dependency-light HMAC attestations for the offline ResearchAlpha pipeline.

The exact Cobaya environment intentionally contains only the frozen scientific
stack.  Importing the web application's evidence module would pull database and
settings-framework dependencies into that environment, so this module uses only
the Python standard library.  Exact-profile callers set ``require_explicit``;
the process-local fallback exists solely for non-publication development and CI.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
from collections.abc import Mapping, Sequence
from typing import Any


SCIENTIFIC_ATTESTATION_SCHEMA_VERSION = 1
SCIENTIFIC_ATTESTATION_SOURCE = "server_scientific_attestation"
_DEV_EPHEMERAL_KEY = secrets.token_hex(32)
_DEV_EPHEMERAL_KEY_ID = "dev-ephemeral"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_json_safe(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        return {
            "__type__": "bytes",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }
    return str(value)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def scientific_content_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _signature(payload: Mapping[str, Any], *, key: str) -> str:
    digest = hmac.new(
        key.encode("utf-8"),
        b"standard-astro/scientific-attestation/v1\0"
        + _canonical_bytes(payload),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def _explicit_signing_material() -> tuple[str, str] | None:
    key = os.environ.get("EVIDENCE_SIGNING_KEY")
    key_id = str(os.environ.get("EVIDENCE_SIGNING_KEY_ID") or "").strip()
    if not key and not key_id:
        return None
    if not key or not key_id:
        raise ValueError(
            "EVIDENCE_SIGNING_KEY and EVIDENCE_SIGNING_KEY_ID must be configured together"
        )
    return key_id, key


def signing_key_binding(*, require_explicit: bool) -> dict[str, Any]:
    material = _explicit_signing_material()
    if material is None:
        if require_explicit:
            raise ValueError(
                "exact ResearchAlpha signing requires explicit EVIDENCE_SIGNING_KEY "
                "and EVIDENCE_SIGNING_KEY_ID"
            )
        key_id, key = _DEV_EPHEMERAL_KEY_ID, _DEV_EPHEMERAL_KEY
    else:
        key_id, key = material
    if require_explicit and key_id == _DEV_EPHEMERAL_KEY_ID:
        raise ValueError("exact ResearchAlpha signing forbids an ephemeral key id")
    if require_explicit and len(key.encode("utf-8")) < 32:
        raise ValueError("exact ResearchAlpha signing key must contain at least 32 bytes")
    return {
        "available": True,
        "key_id": key_id,
        "sha256": "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest(),
    }


def build_scientific_attestation(
    *,
    attestation_type: str,
    payload: Mapping[str, Any],
    require_explicit: bool = False,
) -> dict[str, Any]:
    if not isinstance(attestation_type, str) or not attestation_type.strip():
        raise ValueError("attestation_type is required")
    binding = signing_key_binding(require_explicit=require_explicit)
    key_id = str(binding["key_id"])
    material = _explicit_signing_material()
    key = material[1] if material is not None else _DEV_EPHEMERAL_KEY
    body = dict(_json_safe(dict(payload)))
    for field in (
        "schema_version",
        "attestation_source",
        "attestation_type",
        "key_id",
        "manifest_hash",
        "signature",
        "signature_verified",
    ):
        body.pop(field, None)
    body.update(
        {
            "schema_version": SCIENTIFIC_ATTESTATION_SCHEMA_VERSION,
            "attestation_source": SCIENTIFIC_ATTESTATION_SOURCE,
            "attestation_type": attestation_type.strip(),
            "key_id": key_id,
        }
    )
    body["manifest_hash"] = scientific_content_hash(body)
    body["signature"] = _signature(body, key=key)
    return body


def _verification_keyring() -> dict[str, str]:
    raw = str(os.environ.get("EVIDENCE_VERIFICATION_KEYS") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key_id): str(key)
        for key_id, key in parsed.items()
        if str(key_id).strip() and str(key)
    }


def verification_key_for_id(key_id: Any) -> str | None:
    if not isinstance(key_id, str) or not key_id:
        return None
    material = _explicit_signing_material()
    if material is not None and material[0] == key_id:
        return material[1]
    retired = _verification_keyring().get(key_id)
    if retired:
        return retired
    if key_id == _DEV_EPHEMERAL_KEY_ID:
        return _DEV_EPHEMERAL_KEY
    return None


def verify_scientific_attestation(
    record: Any,
    *,
    expected_type: str,
) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("schema_version") != SCIENTIFIC_ATTESTATION_SCHEMA_VERSION:
        return False
    if record.get("attestation_source") != SCIENTIFIC_ATTESTATION_SOURCE:
        return False
    if record.get("attestation_type") != expected_type:
        return False
    manifest_hash = record.get("manifest_hash")
    signature = record.get("signature")
    if not isinstance(manifest_hash, str) or not isinstance(signature, str):
        return False
    unhashed = {
        key: value
        for key, value in record.items()
        if key not in {"manifest_hash", "signature", "signature_verified"}
    }
    if not hmac.compare_digest(manifest_hash, scientific_content_hash(unhashed)):
        return False
    signed = {
        key: value
        for key, value in record.items()
        if key not in {"signature", "signature_verified"}
    }
    key = verification_key_for_id(record.get("key_id"))
    return bool(
        key
        and hmac.compare_digest(signature, _signature(signed, key=key))
    )
