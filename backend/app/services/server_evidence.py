"""Tamper-evident, owner-bound scientific evidence from chat tool runs.

``ChatSession.messages`` is a client-authored display transcript.  It must not
become a publication authority merely because the browser sends an ``actions``
array that looks like a tool result.  This module stores the server's actual
tool executions in the existing, server-owned ``ChatSession.audit_log`` column
and authenticates every record with an application-secret HMAC.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.models.database import async_session as AsyncSessionLocal
from app.models.schemas import ChatSession
from app.services.agent_runtime.sse import _slim_tool_result_for_sse

logger = logging.getLogger(__name__)

LEGACY_SERVER_EVIDENCE_SCHEMA_VERSION = 1
SERVER_EVIDENCE_SCHEMA_VERSION = 2
SERVER_EVIDENCE_SOURCE = "server_tool_execution"
_SERVER_EVIDENCE_MAX_RECORDS = 100
_SERVER_EVIDENCE_RESULT_MAX_BYTES = 250_000


def _json_safe(value: Any) -> Any:
    """Return deterministic JSON-native data suitable for JSON/JSONB."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return None
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


def _record_signature(payload: Mapping[str, Any], *, key: str) -> str:
    schema_version = int(
        payload.get("schema_version", LEGACY_SERVER_EVIDENCE_SCHEMA_VERSION)
    )
    digest = hmac.new(
        key.encode("utf-8"),
        f"standard-astro/server-evidence/v{schema_version}\0".encode("ascii")
        + _canonical_bytes(payload),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def _verification_keys(record: Mapping[str, Any]) -> list[str]:
    """Resolve trusted verification keys without accepting unknown key ids."""

    schema_version = record.get("schema_version")
    keyring = settings.evidence_verification_keyring
    if schema_version == LEGACY_SERVER_EVIDENCE_SCHEMA_VERSION:
        # Schema-v1 records predate key ids and were signed with JWT_SECRET.
        # Trying the retired-key ring as well lets an operator preserve the old
        # JWT secret under a descriptive id before rotating JWT_SECRET.
        candidates = [settings.jwt_secret, *keyring.values()]
        return list(dict.fromkeys(key for key in candidates if key))
    if schema_version != SERVER_EVIDENCE_SCHEMA_VERSION:
        return []
    key_id = record.get("key_id")
    if not isinstance(key_id, str) or not key_id:
        return []
    if key_id == settings.evidence_signing_key_id:
        return [settings.evidence_signing_key]
    retired = keyring.get(key_id)
    return [retired] if retired else []


def build_server_evidence_record(
    *,
    session_id: str | uuid.UUID,
    owner_id: str | uuid.UUID,
    run_id: str,
    assistant_reply: str,
    tool_results: list[dict[str, Any]],
    validation_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a signed record from the server's in-memory execution result."""

    bounded_results: list[dict[str, Any]] = []
    for raw in tool_results or []:
        if not isinstance(raw, dict):
            continue
        result = _slim_tool_result_for_sse(
            raw.get("result"), max_bytes=_SERVER_EVIDENCE_RESULT_MAX_BYTES
        )
        bounded_results.append(
            _json_safe(
                {
                    "id": raw.get("id"),
                    "tool": raw.get("tool") or raw.get("name"),
                    "input": raw.get("input"),
                    "result": result,
                }
            )
        )

    payload: dict[str, Any] = {
        "schema_version": SERVER_EVIDENCE_SCHEMA_VERSION,
        "source": SERVER_EVIDENCE_SOURCE,
        "key_id": settings.evidence_signing_key_id,
        "record_id": str(uuid.uuid4()),
        "session_id": str(session_id),
        "owner_id": str(owner_id),
        "run_id": str(run_id),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "assistant_reply": str(assistant_reply or ""),
        "tool_results": bounded_results,
        "validation_summary": _json_safe(validation_summary or {}),
    }
    payload["signature"] = _record_signature(
        payload, key=settings.evidence_signing_key
    )
    return payload


def verify_server_evidence_record(
    record: Any,
    *,
    session_id: str | uuid.UUID,
    owner_id: str | uuid.UUID,
) -> bool:
    """Verify origin, ownership, session binding, shape, and HMAC."""

    if not isinstance(record, dict):
        return False
    if record.get("schema_version") not in {
        LEGACY_SERVER_EVIDENCE_SCHEMA_VERSION,
        SERVER_EVIDENCE_SCHEMA_VERSION,
    }:
        return False
    if record.get("source") != SERVER_EVIDENCE_SOURCE:
        return False
    if str(record.get("session_id") or "") != str(session_id):
        return False
    if str(record.get("owner_id") or "") != str(owner_id):
        return False
    if not isinstance(record.get("tool_results"), list):
        return False
    supplied = record.get("signature")
    if not isinstance(supplied, str):
        return False
    payload = {key: value for key, value in record.items() if key != "signature"}
    return any(
        hmac.compare_digest(supplied, _record_signature(payload, key=key))
        for key in _verification_keys(record)
    )


def verified_server_evidence_records(
    audit_log: Any,
    *,
    session_id: str | uuid.UUID,
    owner_id: str | uuid.UUID,
) -> list[dict[str, Any]]:
    """Return only HMAC-valid records belonging to the requested owner/session."""

    if not isinstance(audit_log, list):
        return []
    return [
        record
        for record in audit_log
        if verify_server_evidence_record(
            record, session_id=session_id, owner_id=owner_id
        )
    ]


async def append_server_evidence(
    *,
    session_id: str | uuid.UUID | None,
    owner_id: str | uuid.UUID | None,
    run_id: str,
    assistant_reply: str,
    tool_results: list[dict[str, Any]],
    validation_summary: dict[str, Any] | None = None,
) -> bool:
    """Append one execution record after an owner-scoped session lookup.

    Chat remains usable if durable evidence persistence is unavailable, but
    paper validation will then fail closed because no valid signed record is
    present.
    """

    try:
        sid = uuid.UUID(str(session_id))
        uid = uuid.UUID(str(owner_id))
    except (ValueError, TypeError, AttributeError):
        return False

    try:
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(ChatSession).where(
                        ChatSession.id == sid,
                        ChatSession.user_id == uid,
                    ).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            record = build_server_evidence_record(
                session_id=sid,
                owner_id=uid,
                run_id=run_id,
                assistant_reply=assistant_reply,
                tool_results=tool_results,
                validation_summary=validation_summary,
            )
            existing = list(row.audit_log) if isinstance(row.audit_log, list) else []
            # Legacy/client-authored entries carry no valid HMAC and are not
            # publication evidence.  Retain them for diagnostics, while
            # bounding the total JSON column size by record count.
            row.audit_log = (existing + [record])[-_SERVER_EVIDENCE_MAX_RECORDS:]
            await db.commit()
            return True
    except Exception as exc:
        logger.warning(
            "server evidence persistence failed closed for session %s: %s",
            sid,
            exc,
        )
        return False
