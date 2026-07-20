"""Public trust roots and server-side Evidence Pack v2 verification."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.services.evidence_pack_v2 import (
    EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES,
    EvidencePackV2Error,
    evidence_keyring_document,
    verify_evidence_pack_v2,
)

router = APIRouter(tags=["public-evidence"])


def _require_enabled() -> None:
    if not bool(getattr(settings, "evidence_pack_v2_enabled", False)):
        raise HTTPException(status_code=404, detail="Evidence Pack v2 is not enabled")


def _configured_keyring():
    configured = dict(settings.evidence_v2_verification_keyring)
    current_key_id = str(settings.evidence_v2_signing_key_id or "").strip()
    current_public_key = str(settings.evidence_v2_signing_public_key or "").strip()
    if current_key_id and current_public_key:
        configured.setdefault(
            current_key_id,
            {
                "key_id": current_key_id,
                "algorithm": "ed25519",
                "public_key": current_public_key,
                "status": "active",
            },
        )
    if not configured:
        raise HTTPException(status_code=503, detail="Evidence verification keyring unavailable")
    records = [dict(record) for _key_id, record in configured.items()]
    try:
        return evidence_keyring_document(records)
    except EvidencePackV2Error as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc


@router.get("/.well-known/standard-astro-evidence-keys.json")
async def evidence_keys():
    _require_enabled()
    return _configured_keyring()


@router.post("/api/public/evidence-packs/verify")
async def verify_public_evidence_pack(request: Request):
    _require_enabled()
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES:
                raise HTTPException(status_code=413, detail="Evidence Pack is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
    chunks: list[bytes] = []
    observed = 0
    async for chunk in request.stream():
        observed += len(chunk)
        if observed > EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES:
            raise HTTPException(status_code=413, detail="Evidence Pack is too large")
        chunks.append(bytes(chunk))
    payload = b"".join(chunks)
    if not payload:
        raise HTTPException(status_code=400, detail="Evidence Pack body is required")
    keyring = _configured_keyring()
    result = verify_evidence_pack_v2(payload, trusted_keyring=keyring)
    return {
        "valid": result.valid,
        "code": result.code,
        "key_id": result.key_id,
        "key_status": result.key_status,
        "manifest": result.manifest,
        "warning": (
            "A valid signature proves origin and integrity; it does not prove "
            "that the scientific conclusion is correct."
        ),
    }
