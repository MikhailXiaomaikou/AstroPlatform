"""Signed HTTPS protocol and durable leases for local science workers.

Worker output is deliberately not a scientific verdict.  This module only
authenticates a node, leases a registered task, and records its untrusted
result for later deterministic validation and independent verification.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import SessionTransaction

from app.cache import get_redis
from app.models.claim_audit_records import ClaimAudit
from app.models.research_records import ResearchJob
from app.models.schemas import User
from app.models.worker_records import (
    ScienceExecutionAttempt,
    WorkerEnrollmentToken,
    WorkerNode,
)
from app.services.worker_contract import (
    WORKER_PROTOCOL_VERSION,
    canonical_json as _canonical_json,
    canonical_result_hash,
    canonical_worker_request,
)

WORKER_CLOCK_SKEW_SECONDS = 5 * 60
WORKER_NONCE_TTL_SECONDS = 10 * 60
WORKER_ENROLLMENT_TTL_SECONDS = 10 * 60
WORKER_LEASE_SECONDS = 120
WORKER_REDISPATCH_GRACE_SECONDS = 10 * 60
WORKER_MAX_ATTEMPTS = 3
WORKER_MAX_NODES_PER_USER = 3
WORKER_RESULT_MAX_BYTES = 2 * 1024 * 1024

REGISTERED_LOCAL_WORKFLOWS = frozenset({"union3_flat_lcdm_sn_only_v1"})

__all__ = [
    "WORKER_PROTOCOL_VERSION",
    "canonical_result_hash",
    "canonical_worker_request",
]


class WorkerProtocolError(ValueError):
    """A safe, machine-readable worker protocol rejection."""

    def __init__(self, code: str, *, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class PreparedAttemptCompletion:
    """Lease/result validation held by one open database transaction."""

    attempt: ScienceExecutionAttempt
    job: ResearchJob | None
    audit: ClaimAudit | None
    result_hash: str
    node_id: uuid.UUID
    user_id: uuid.UUID
    attempt_id: uuid.UUID
    lease_id: str
    lease_expires_at: datetime
    validated_at: datetime
    transaction: SessionTransaction


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _require_active_worker_owner(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    check_restore_tombstone: bool,
    lock: bool = False,
) -> User:
    if lock:
        owner = await db.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
        owner = await db.get(User, user_id)
    if owner is None or str(owner.account_status or "").upper() != "ACTIVE":
        raise WorkerProtocolError("worker_owner_inactive", status_code=401)
    if check_restore_tombstone:
        from app.services.account_deletion import external_deletion_tombstone_exists

        try:
            tombstoned = await asyncio.to_thread(
                external_deletion_tombstone_exists,
                user_id,
            )
        except Exception as exc:
            raise WorkerProtocolError(
                "worker_owner_status_unavailable", status_code=503
            ) from exc
        if tombstoned:
            raise WorkerProtocolError("worker_owner_inactive", status_code=401)
    return owner


def enrollment_token_hash(raw_token: str) -> str:
    """Hash a high-entropy one-time enrollment token for storage."""

    return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()


def decode_ed25519_public_key(value: str) -> Ed25519PublicKey:
    """Parse one raw, unpadded/base64 Ed25519 public key."""

    try:
        raw = base64.b64decode(value.strip(), validate=True)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, TypeError) as exc:
        raise WorkerProtocolError("invalid_worker_public_key", status_code=422) from exc


def public_key_fingerprint(value: str) -> str:
    key = decode_ed25519_public_key(value)
    from cryptography.hazmat.primitives import serialization

    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


async def create_enrollment_token(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> tuple[WorkerEnrollmentToken, str]:
    """Persist only a digest and return the raw enrollment code once."""

    issued_at = now or _utcnow()
    await _require_active_worker_owner(
        db,
        user_id=user_id,
        check_restore_tombstone=False,
        lock=True,
    )
    active_nodes = await db.scalar(
        select(func.count(WorkerNode.id)).where(
            WorkerNode.user_id == user_id,
            WorkerNode.status != "REVOKED",
        )
    )
    if int(active_nodes or 0) >= WORKER_MAX_NODES_PER_USER:
        raise WorkerProtocolError("worker_node_limit_reached", status_code=409)

    raw_token = "ASTRO-WORKER-" + secrets.token_urlsafe(32)
    row = WorkerEnrollmentToken(
        user_id=user_id,
        token_hash=enrollment_token_hash(raw_token),
        token_prefix=raw_token[:16],
        expires_at=issued_at + timedelta(seconds=WORKER_ENROLLMENT_TTL_SECONDS),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, raw_token


async def enroll_worker_node(
    db: AsyncSession,
    *,
    enrollment_code: str,
    name: str,
    public_key: str,
    protocol_version: str,
    capabilities: Mapping[str, Any] | None = None,
    release_manifest: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> WorkerNode:
    """Atomically consume an enrollment code and register one node."""

    enrolled_at = now or _utcnow()
    if protocol_version != WORKER_PROTOCOL_VERSION:
        raise WorkerProtocolError("unsupported_worker_protocol", status_code=422)
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 160:
        raise WorkerProtocolError("invalid_worker_name", status_code=422)

    fingerprint = public_key_fingerprint(public_key)
    token_snapshot = (
        await db.execute(
            select(WorkerEnrollmentToken).where(
                WorkerEnrollmentToken.token_hash
                == enrollment_token_hash(enrollment_code),
            )
        )
    ).scalar_one_or_none()
    if token_snapshot is None:
        raise WorkerProtocolError("invalid_enrollment_code", status_code=401)
    # Enrollment, revocation, and deletion all serialize capacity changes on
    # the owner row. Lock it before the token so different valid codes cannot
    # concurrently observe the same node count and exceed the per-user cap.
    await _require_active_worker_owner(
        db,
        user_id=token_snapshot.user_id,
        check_restore_tombstone=True,
        lock=True,
    )
    token = (
        await db.execute(
            select(WorkerEnrollmentToken)
            .where(
                WorkerEnrollmentToken.token_hash
                == enrollment_token_hash(enrollment_code),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if token is None:
        raise WorkerProtocolError("invalid_enrollment_code", status_code=401)
    if token.used_at is not None:
        raise WorkerProtocolError("enrollment_code_used", status_code=409)
    if _as_utc(token.expires_at) <= enrolled_at:
        raise WorkerProtocolError("enrollment_code_expired", status_code=410)

    active_nodes = await db.scalar(
        select(func.count(WorkerNode.id)).where(
            WorkerNode.user_id == token.user_id,
            WorkerNode.status != "REVOKED",
        )
    )
    if int(active_nodes or 0) >= WORKER_MAX_NODES_PER_USER:
        raise WorkerProtocolError("worker_node_limit_reached", status_code=409)

    existing_key = await db.scalar(
        select(WorkerNode.id).where(WorkerNode.public_key_fingerprint == fingerprint)
    )
    if existing_key is not None:
        raise WorkerProtocolError("worker_key_already_enrolled", status_code=409)

    token.used_at = enrolled_at
    node = WorkerNode(
        user_id=token.user_id,
        name=clean_name,
        public_key=public_key.strip(),
        public_key_fingerprint=fingerprint,
        protocol_version=protocol_version,
        status="ACTIVE",
        capabilities=dict(capabilities or {}),
        release_manifest=dict(release_manifest or {}),
        last_seen_at=enrolled_at,
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return node


async def verify_worker_request(
    db: AsyncSession,
    *,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
    redis_client: Any | None = None,
    now: datetime | None = None,
) -> WorkerNode:
    """Authenticate a signed request and atomically consume its nonce."""

    worker_id_text = str(headers.get("x-standard-astro-worker-id", "")).strip()
    timestamp = str(headers.get("x-standard-astro-worker-timestamp", "")).strip()
    nonce = str(headers.get("x-standard-astro-worker-nonce", "")).strip()
    protocol_version = str(headers.get("x-standard-astro-worker-protocol", "")).strip()
    signature_text = str(headers.get("x-standard-astro-worker-signature", "")).strip()
    if not all((worker_id_text, timestamp, nonce, protocol_version, signature_text)):
        raise WorkerProtocolError("missing_worker_signature", status_code=401)
    if protocol_version != WORKER_PROTOCOL_VERSION:
        raise WorkerProtocolError("unsupported_worker_protocol", status_code=426)
    if not 16 <= len(nonce) <= 128 or any(ch.isspace() for ch in nonce):
        raise WorkerProtocolError("invalid_worker_nonce", status_code=401)

    try:
        worker_id = uuid.UUID(worker_id_text)
        request_epoch = int(timestamp)
        signature = base64.b64decode(signature_text, validate=True)
    except (ValueError, TypeError) as exc:
        raise WorkerProtocolError("invalid_worker_signature", status_code=401) from exc

    checked_at = now or _utcnow()
    if abs(int(checked_at.timestamp()) - request_epoch) > WORKER_CLOCK_SKEW_SECONDS:
        raise WorkerProtocolError("worker_clock_skew", status_code=401)

    node = await db.get(WorkerNode, worker_id)
    if node is None or node.status == "REVOKED":
        raise WorkerProtocolError("worker_revoked_or_unknown", status_code=401)
    if node.protocol_version != protocol_version:
        raise WorkerProtocolError("worker_protocol_mismatch", status_code=401)
    await _require_active_worker_owner(
        db,
        user_id=node.user_id,
        check_restore_tombstone=True,
    )

    body_sha256 = hashlib.sha256(body).hexdigest()
    signed = canonical_worker_request(
        method=method,
        path=path,
        timestamp=timestamp,
        nonce=nonce,
        body_sha256=body_sha256,
        worker_id=worker_id_text,
        protocol_version=protocol_version,
    )
    try:
        decode_ed25519_public_key(node.public_key).verify(signature, signed)
    except InvalidSignature as exc:
        raise WorkerProtocolError("invalid_worker_signature", status_code=401) from exc

    redis_conn = redis_client if redis_client is not None else await get_redis()
    if redis_conn is None:
        raise WorkerProtocolError("worker_nonce_store_unavailable", status_code=503)
    nonce_key = f"astro:worker-nonce:{node.id}:{nonce}"
    try:
        stored = await redis_conn.set(
            nonce_key,
            "1",
            ex=WORKER_NONCE_TTL_SECONDS,
            nx=True,
        )
    except Exception as exc:
        raise WorkerProtocolError(
            "worker_nonce_store_unavailable", status_code=503
        ) from exc
    if not stored:
        raise WorkerProtocolError("worker_nonce_replayed", status_code=409)

    node.last_seen_at = checked_at
    await db.commit()
    return node


def _decode_private_key(value: str) -> Ed25519PrivateKey:
    try:
        raw = base64.b64decode(value.strip(), validate=True)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (ValueError, TypeError) as exc:
        raise WorkerProtocolError(
            "worker_task_signing_key_invalid", status_code=503
        ) from exc


def sign_task_envelope(
    envelope: Mapping[str, Any],
    *,
    private_key: str,
    key_id: str,
) -> dict[str, Any]:
    """Return a control-plane-signed task envelope."""

    if not private_key.strip() or not key_id.strip():
        raise WorkerProtocolError(
            "worker_task_signing_key_unavailable", status_code=503
        )
    unsigned = dict(envelope)
    unsigned.pop("server_signature", None)
    signature = _decode_private_key(private_key).sign(_canonical_json(unsigned))
    return {
        **unsigned,
        "server_signature": {
            "algorithm": "ed25519",
            "key_id": key_id.strip(),
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


async def lease_next_task(
    db: AsyncSession,
    *,
    node: WorkerNode,
    private_key: str,
    key_id: str,
    release_commit: str,
    image_digest: str,
    now: datetime | None = None,
) -> ScienceExecutionAttempt | None:
    """Atomically lease the oldest compatible queued job owned by this node's user."""

    leased_at = now or _utcnow()
    if node.status != "ACTIVE":
        raise WorkerProtocolError("worker_not_accepting_tasks", status_code=409)
    job = (
        await db.execute(
            select(ResearchJob)
            .where(
                ResearchJob.user_id == node.user_id,
                ResearchJob.status == "QUEUED",
                ResearchJob.background_backend == "https_worker",
                ResearchJob.tool_name.in_(REGISTERED_LOCAL_WORKFLOWS),
            )
            .order_by(ResearchJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        return None

    args = dict(job.args or {})
    workflow_key = str(args.get("workflow_key") or job.tool_name)
    if workflow_key not in REGISTERED_LOCAL_WORKFLOWS:
        raise WorkerProtocolError("workflow_not_registered", status_code=422)
    audit_id_value = args.get("audit_id")
    try:
        audit_id = uuid.UUID(str(audit_id_value)) if audit_id_value else None
    except ValueError as exc:
        raise WorkerProtocolError("invalid_audit_binding", status_code=409) from exc
    audit = None
    if audit_id is not None:
        audit = (
            await db.execute(
                select(ClaimAudit).where(ClaimAudit.id == audit_id).with_for_update()
            )
        ).scalar_one_or_none()
        if audit is None or audit.user_id != node.user_id:
            raise WorkerProtocolError("invalid_audit_binding", status_code=409)
        if audit.lifecycle_status not in {"QUEUED", "RUNNING"}:
            raise WorkerProtocolError("audit_not_leaseable", status_code=409)

    attempt_count = await db.scalar(
        select(func.count(ScienceExecutionAttempt.id)).where(
            ScienceExecutionAttempt.job_id == job.job_id
        )
    )
    attempt_number = int(attempt_count or 0) + 1
    max_attempt_number = WORKER_MAX_ATTEMPTS * (
        1 + max(0, int(audit.retry_count or 0)) if audit is not None else 1
    )
    if attempt_number > max_attempt_number:
        job.status = "FAILED"
        job.current_attempt_id = None
        job.error_class = "worker_attempts_exhausted"
        job.error = "The local science task exhausted its signed attempts"
        job.completed_at = leased_at
        if audit is not None:
            audit.lifecycle_status = "FAILED_RETRYABLE"
            audit.scientific_verdict = None
            audit.machine_support_eligible = False
            audit.reproduction_ready = False
            audit.publication_ready = False
            audit.progress_stage = "worker_attempts_exhausted"
            audit.error_class = "worker_attempts_exhausted"
            audit.error = job.error
            audit.completed_at = leased_at
        await db.commit()
        return None

    attempt_id = uuid.uuid4()
    lease_id = secrets.token_hex(32)
    lease_expires_at = leased_at + timedelta(seconds=WORKER_LEASE_SECONDS)
    deadline = leased_at + timedelta(
        seconds=int(args.get("deadline_seconds") or 30 * 60)
    )
    unsigned_envelope = {
        "protocol_version": WORKER_PROTOCOL_VERSION,
        "job_id": job.job_id,
        "audit_id": str(audit_id) if audit_id else None,
        "attempt_id": str(attempt_id),
        "lease_id": lease_id,
        "workflow_key": workflow_key,
        "normalized_inputs": args.get("normalized_inputs") or {},
        "input_sha256": job.inputs_hash,
        "dataset_pins": args.get("dataset_pins") or [],
        "image_digest": image_digest,
        "git_commit": release_commit,
        "resource_limits": args.get("resource_limits")
        or {"cpu": 2, "memory_mb": 6144, "concurrency": 1},
        "deadline": deadline.isoformat(),
        "lease_expires_at": lease_expires_at.isoformat(),
    }
    envelope = sign_task_envelope(
        unsigned_envelope,
        private_key=private_key,
        key_id=key_id,
    )
    attempt = ScienceExecutionAttempt(
        id=attempt_id,
        job_id=job.job_id,
        audit_id=audit_id,
        user_id=node.user_id,
        worker_node_id=node.id,
        attempt_number=attempt_number,
        status="LEASED",
        lease_id=lease_id,
        lease_expires_at=lease_expires_at,
        input_hash=job.inputs_hash,
        task_envelope=envelope,
        last_heartbeat_at=leased_at,
    )
    db.add(attempt)
    job.status = "RUNNING"
    # Bind the durable job to the one lease whose result may be verified.
    # A late result from an expired or superseded attempt must never become
    # scientific evidence, even when it still carries a valid node signature.
    job.current_attempt_id = attempt_id
    job.started_at = job.started_at or leased_at
    job.progress = 0.0
    job.progress_message = "leased_to_local_worker"
    node.last_seen_at = leased_at
    if audit is not None:
        audit.lifecycle_status = "RUNNING"
        audit.started_at = audit.started_at or leased_at
        audit.completed_at = None
        audit.attempt_count = attempt_number
        audit.progress = 0.05
        audit.progress_stage = "local_worker_running"
        audit.error = None
        audit.error_class = None
    await db.commit()
    await db.refresh(attempt)
    return attempt


async def get_worker_attempt(
    db: AsyncSession,
    *,
    node: WorkerNode,
    attempt_id: uuid.UUID,
    lock: bool = True,
) -> ScienceExecutionAttempt:
    query = select(ScienceExecutionAttempt).where(
        ScienceExecutionAttempt.id == attempt_id,
        ScienceExecutionAttempt.worker_node_id == node.id,
        ScienceExecutionAttempt.user_id == node.user_id,
    )
    if lock:
        query = query.with_for_update()
    row = (await db.execute(query)).scalar_one_or_none()
    if row is None:
        raise WorkerProtocolError("science_attempt_not_found", status_code=404)
    return row


def require_live_lease(
    attempt: ScienceExecutionAttempt,
    *,
    lease_id: str,
    now: datetime | None = None,
) -> None:
    if not secrets.compare_digest(str(attempt.lease_id), str(lease_id)):
        raise WorkerProtocolError("stale_worker_lease", status_code=409)
    if attempt.status not in {"LEASED", "RUNNING"}:
        raise WorkerProtocolError("science_attempt_not_active", status_code=409)
    if _as_utc(attempt.lease_expires_at) <= (now or _utcnow()):
        raise WorkerProtocolError("worker_lease_expired", status_code=409)


def _validated_worker_result_hash(
    result: Mapping[str, Any],
    claimed_result_hash: str | None,
) -> str:
    canonical_bytes = _canonical_json(dict(result))
    if len(canonical_bytes) > WORKER_RESULT_MAX_BYTES:
        raise WorkerProtocolError("worker_result_too_large", status_code=413)
    result_hash = hashlib.sha256(canonical_bytes).hexdigest()
    if claimed_result_hash:
        normalized_claim = claimed_result_hash.removeprefix("sha256:").lower()
        if not secrets.compare_digest(normalized_claim, result_hash):
            raise WorkerProtocolError("worker_result_hash_mismatch", status_code=422)
    return result_hash


async def lock_active_attempt_state(
    db: AsyncSession,
    *,
    node: WorkerNode,
    attempt_id: uuid.UUID,
    lease_id: str,
    now: datetime | None = None,
) -> tuple[ScienceExecutionAttempt, ResearchJob, ClaimAudit | None]:
    """Lock Audit, job, and attempt and require one live owner-bound lease."""

    observed = await get_worker_attempt(
        db,
        node=node,
        attempt_id=attempt_id,
        lock=False,
    )
    audit = None
    if observed.audit_id is not None:
        audit = await db.scalar(
            select(ClaimAudit)
            .where(ClaimAudit.id == observed.audit_id)
            .with_for_update()
        )
    job = await db.scalar(
        select(ResearchJob)
        .where(ResearchJob.job_id == observed.job_id)
        .with_for_update()
    )
    attempt = await get_worker_attempt(db, node=node, attempt_id=attempt_id)
    require_live_lease(attempt, lease_id=lease_id, now=now)
    if job is None:
        raise WorkerProtocolError("science_job_not_found", status_code=409)
    if job.status == "CANCELLED":
        raise WorkerProtocolError("science_job_cancelled", status_code=409)
    if job.status != "RUNNING" or job.current_attempt_id != attempt.id:
        raise WorkerProtocolError("stale_worker_lease", status_code=409)
    if attempt.audit_id is not None:
        if audit is None or audit.user_id != node.user_id:
            raise WorkerProtocolError("invalid_audit_binding", status_code=409)
        if audit.lifecycle_status == "CANCELLED":
            raise WorkerProtocolError("claim_audit_cancelled", status_code=409)
        if audit.lifecycle_status != "RUNNING":
            raise WorkerProtocolError("audit_not_completable", status_code=409)
    return attempt, job, audit


async def prepare_attempt_completion(
    db: AsyncSession,
    *,
    node: WorkerNode,
    attempt_id: uuid.UUID,
    lease_id: str,
    result: Mapping[str, Any],
    claimed_result_hash: str | None,
    now: datetime | None = None,
) -> PreparedAttemptCompletion:
    """Lock and validate every state row before artifact promotion.

    The API holds these locks while server-side object promotion runs. This
    prevents cancellation/deletion from winning between validation and copy.
    A completed same-hash replay is returned without requiring an active job.
    """

    checked_at = now or _utcnow()
    result_hash = _validated_worker_result_hash(result, claimed_result_hash)
    observed = await get_worker_attempt(
        db,
        node=node,
        attempt_id=attempt_id,
        lock=False,
    )
    audit = None
    if observed.audit_id is not None:
        audit = await db.scalar(
            select(ClaimAudit)
            .where(ClaimAudit.id == observed.audit_id)
            .with_for_update()
        )
    job = await db.scalar(
        select(ResearchJob)
        .where(ResearchJob.job_id == observed.job_id)
        .with_for_update()
    )
    attempt = await get_worker_attempt(db, node=node, attempt_id=attempt_id)
    if attempt.status == "SUCCEEDED":
        if attempt.result_hash and secrets.compare_digest(
            attempt.result_hash, result_hash
        ):
            transaction = db.sync_session.get_transaction()
            if transaction is None:  # pragma: no cover - SQLAlchemy invariant
                raise WorkerProtocolError(
                    "completion_transaction_unavailable", status_code=409
                )
            return PreparedAttemptCompletion(
                attempt=attempt,
                job=job,
                audit=audit,
                result_hash=result_hash,
                node_id=node.id,
                user_id=node.user_id,
                attempt_id=attempt_id,
                lease_id=lease_id,
                lease_expires_at=_as_utc(attempt.lease_expires_at),
                validated_at=checked_at,
                transaction=transaction,
            )
        raise WorkerProtocolError("conflicting_worker_result", status_code=409)
    require_live_lease(attempt, lease_id=lease_id, now=checked_at)
    if job is None:
        raise WorkerProtocolError("science_job_not_found", status_code=409)
    if job.status == "CANCELLED":
        raise WorkerProtocolError("science_job_cancelled", status_code=409)
    if job.status != "RUNNING" or job.current_attempt_id != attempt.id:
        raise WorkerProtocolError("stale_worker_lease", status_code=409)
    if attempt.audit_id is not None:
        if audit is None or audit.user_id != node.user_id:
            raise WorkerProtocolError("invalid_audit_binding", status_code=409)
        if audit.lifecycle_status == "CANCELLED":
            raise WorkerProtocolError("claim_audit_cancelled", status_code=409)
        if audit.lifecycle_status != "RUNNING":
            raise WorkerProtocolError("audit_not_completable", status_code=409)
    transaction = db.sync_session.get_transaction()
    if transaction is None:  # pragma: no cover - SQLAlchemy invariant
        raise WorkerProtocolError("completion_transaction_unavailable", status_code=409)
    return PreparedAttemptCompletion(
        attempt=attempt,
        job=job,
        audit=audit,
        result_hash=result_hash,
        node_id=node.id,
        user_id=node.user_id,
        attempt_id=attempt_id,
        lease_id=lease_id,
        lease_expires_at=_as_utc(attempt.lease_expires_at),
        validated_at=checked_at,
        transaction=transaction,
    )


def _validate_prepared_attempt_completion(
    db: AsyncSession,
    *,
    prepared: PreparedAttemptCompletion,
    node: WorkerNode,
    attempt_id: uuid.UUID,
    lease_id: str,
    result: Mapping[str, Any],
    claimed_result_hash: str | None,
) -> tuple[ScienceExecutionAttempt, ResearchJob | None, ClaimAudit | None, str]:
    """Revalidate a prepared completion without advancing the lease clock.

    Artifact verification runs while the Audit, job, and attempt rows remain
    locked.  It may legitimately outlive the lease deadline because a blocked
    heartbeat cannot renew those rows.  The token is therefore accepted only
    in the exact transaction that performed the live-lease validation, while
    every owner, lease, row binding, and result-hash invariant is checked
    again.
    """

    transaction = db.sync_session.get_transaction()
    if transaction is None or transaction is not prepared.transaction:
        raise WorkerProtocolError("completion_transaction_lost", status_code=409)
    if (
        node.id != prepared.node_id
        or node.user_id != prepared.user_id
        or attempt_id != prepared.attempt_id
    ):
        raise WorkerProtocolError("completion_owner_binding_mismatch", status_code=409)

    result_hash = _validated_worker_result_hash(result, claimed_result_hash)
    if not secrets.compare_digest(result_hash, prepared.result_hash):
        raise WorkerProtocolError("completion_result_changed", status_code=409)

    attempt = prepared.attempt
    job = prepared.job
    audit = prepared.audit
    if (
        attempt.id != attempt_id
        or attempt.worker_node_id != node.id
        or attempt.user_id != node.user_id
    ):
        raise WorkerProtocolError("completion_owner_binding_mismatch", status_code=409)
    if attempt.status == "SUCCEEDED":
        if attempt.result_hash and secrets.compare_digest(
            attempt.result_hash, result_hash
        ):
            return attempt, job, audit, result_hash
        raise WorkerProtocolError("conflicting_worker_result", status_code=409)
    if not secrets.compare_digest(
        str(prepared.lease_id), str(lease_id)
    ) or not secrets.compare_digest(str(attempt.lease_id), str(lease_id)):
        raise WorkerProtocolError("stale_worker_lease", status_code=409)
    if attempt.status not in {"LEASED", "RUNNING"}:
        raise WorkerProtocolError("science_attempt_not_active", status_code=409)
    if prepared.lease_expires_at <= prepared.validated_at:
        raise WorkerProtocolError("worker_lease_expired", status_code=409)
    if _as_utc(attempt.lease_expires_at) != prepared.lease_expires_at:
        raise WorkerProtocolError("stale_worker_lease", status_code=409)
    if job is None or job.job_id != attempt.job_id:
        raise WorkerProtocolError("science_job_not_found", status_code=409)
    if job.status == "CANCELLED":
        raise WorkerProtocolError("science_job_cancelled", status_code=409)
    if job.status != "RUNNING" or job.current_attempt_id != attempt.id:
        raise WorkerProtocolError("stale_worker_lease", status_code=409)
    if attempt.audit_id is not None:
        if (
            audit is None
            or audit.id != attempt.audit_id
            or audit.user_id != node.user_id
        ):
            raise WorkerProtocolError("invalid_audit_binding", status_code=409)
        if audit.lifecycle_status == "CANCELLED":
            raise WorkerProtocolError("claim_audit_cancelled", status_code=409)
        if audit.lifecycle_status != "RUNNING":
            raise WorkerProtocolError("audit_not_completable", status_code=409)
    return attempt, job, audit, result_hash


async def heartbeat_attempt(
    db: AsyncSession,
    *,
    node: WorkerNode,
    attempt_id: uuid.UUID,
    lease_id: str,
    progress: float | None,
    checkpoint: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    checked_at = now or _utcnow()
    attempt = await get_worker_attempt(db, node=node, attempt_id=attempt_id)
    require_live_lease(attempt, lease_id=lease_id, now=checked_at)
    job = await db.get(ResearchJob, attempt.job_id)
    if job is None:
        raise WorkerProtocolError("science_job_not_found", status_code=409)
    if job.current_attempt_id != attempt.id:
        raise WorkerProtocolError("stale_worker_lease", status_code=409)

    if job.status == "CANCELLED":
        action = "cancel"
    elif node.status == "DRAINING":
        action = "drain"
    else:
        action = "continue"
        try:
            deadline = datetime.fromisoformat(
                str(attempt.task_envelope["deadline"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkerProtocolError(
                "worker_task_deadline_invalid", status_code=409
            ) from exc
        deadline = _as_utc(deadline)
        if deadline <= checked_at:
            raise WorkerProtocolError("worker_task_deadline_expired", status_code=409)
        attempt.status = "RUNNING"
        attempt.lease_expires_at = min(
            checked_at + timedelta(seconds=WORKER_LEASE_SECONDS),
            deadline,
        )
        attempt.last_heartbeat_at = checked_at
        node.last_seen_at = checked_at
        if progress is not None:
            job.progress = max(0.0, min(1.0, float(progress)))
        if checkpoint:
            current = dict(attempt.diagnostics or {})
            current["checkpoint"] = dict(checkpoint)
            attempt.diagnostics = current
    await db.commit()
    return {
        "action": action,
        "lease_expires_at": attempt.lease_expires_at.isoformat(),
    }


async def complete_attempt(
    db: AsyncSession,
    *,
    node: WorkerNode,
    attempt_id: uuid.UUID,
    lease_id: str,
    result: Mapping[str, Any],
    claimed_result_hash: str | None,
    diagnostics: Mapping[str, Any] | None,
    artifact_manifest: list[dict[str, Any]] | None,
    now: datetime | None = None,
    prepared: PreparedAttemptCompletion | None = None,
) -> tuple[ScienceExecutionAttempt, bool]:
    """Record an untrusted result; return ``(attempt, idempotent_replay)``."""

    completed_at = now or _utcnow()
    if prepared is None:
        prepared = await prepare_attempt_completion(
            db,
            node=node,
            attempt_id=attempt_id,
            lease_id=lease_id,
            result=result,
            claimed_result_hash=claimed_result_hash,
            now=completed_at,
        )
    attempt, job, audit, result_hash = _validate_prepared_attempt_completion(
        db,
        prepared=prepared,
        node=node,
        attempt_id=attempt_id,
        lease_id=lease_id,
        result=result,
        claimed_result_hash=claimed_result_hash,
    )
    if attempt.status == "SUCCEEDED":
        return attempt, True
    assert job is not None
    attempt.status = "SUCCEEDED"
    attempt.result_hash = result_hash
    attempt.result = dict(result)
    attempt.diagnostics = dict(diagnostics or {})
    attempt.artifact_manifest = list(artifact_manifest or [])
    attempt.completed_at = completed_at
    attempt.last_heartbeat_at = completed_at
    job.status = "COMPLETED"
    # Keep the job/attempt relationship explicit through the untrusted-result
    # handoff; the verifier and finalizer reject any other attempt id.
    job.current_attempt_id = attempt.id
    job.progress = 1.0
    job.progress_message = "local_worker_result_recorded_pending_verification"
    job.result = {
        "worker_attempt_id": str(attempt.id),
        "worker_result_hash": f"sha256:{result_hash}",
        "worker_result": dict(result),
        "scientific_verdict": None,
        "publication_ready": False,
    }
    job.completed_at = completed_at
    node.last_seen_at = completed_at
    if audit is not None:
        audit.progress = 0.7
        audit.progress_stage = "pending_independent_verification"
    await db.commit()
    await db.refresh(attempt)
    return attempt, False


async def fail_attempt(
    db: AsyncSession,
    *,
    node: WorkerNode,
    attempt_id: uuid.UUID,
    lease_id: str,
    error_class: str,
    retryable: bool,
    now: datetime | None = None,
) -> ScienceExecutionAttempt:
    failed_at = now or _utcnow()
    attempt = await get_worker_attempt(db, node=node, attempt_id=attempt_id)
    require_live_lease(attempt, lease_id=lease_id, now=failed_at)
    job = await db.get(ResearchJob, attempt.job_id)
    if job is None:
        raise WorkerProtocolError("science_job_not_found", status_code=409)
    if job.status == "CANCELLED":
        raise WorkerProtocolError("science_job_cancelled", status_code=409)
    if job.status != "RUNNING":
        raise WorkerProtocolError("science_job_not_running", status_code=409)
    if job.current_attempt_id != attempt.id:
        raise WorkerProtocolError("stale_worker_lease", status_code=409)
    audit = None
    if attempt.audit_id is not None:
        audit = await db.get(ClaimAudit, attempt.audit_id)
        if audit is None or audit.user_id != node.user_id:
            raise WorkerProtocolError("invalid_audit_binding", status_code=409)
        if audit.lifecycle_status == "CANCELLED":
            raise WorkerProtocolError("claim_audit_cancelled", status_code=409)
        if audit.lifecycle_status != "RUNNING":
            raise WorkerProtocolError("audit_not_failable", status_code=409)
    attempt.status = "FAILED"
    attempt.error_class = error_class.strip()[:255] or "worker_failed"
    attempt.error = "The local science worker reported a controlled failure"
    attempt.completed_at = failed_at
    max_attempt_number = WORKER_MAX_ATTEMPTS * (
        1 + max(0, int(audit.retry_count or 0)) if audit is not None else 1
    )
    if retryable and attempt.attempt_number < max_attempt_number:
        job.status = "QUEUED"
        job.current_attempt_id = None
        job.progress_message = "waiting_for_worker_retry"
        job.error_class = attempt.error_class
        job.error = None
        job.completed_at = None
        if audit is not None:
            audit.lifecycle_status = "RUNNING"
            audit.progress_stage = "waiting_for_worker_retry"
            audit.error_class = attempt.error_class
            audit.error = None
    else:
        job.status = "FAILED"
        job.current_attempt_id = None
        job.error_class = attempt.error_class
        job.error = attempt.error
        job.completed_at = failed_at
        if audit is not None:
            audit.machine_support_eligible = False
            audit.reproduction_ready = False
            audit.publication_ready = False
            audit.review_status = "NOT_SUBMITTED"
            audit.completed_at = failed_at
            audit.error_class = attempt.error_class
            audit.error = attempt.error
            if retryable:
                audit.lifecycle_status = "FAILED_RETRYABLE"
                audit.scientific_verdict = None
                audit.progress_stage = "worker_attempts_exhausted"
            else:
                audit.lifecycle_status = "COMPLETED"
                audit.scientific_verdict = "WITHHELD"
                audit.progress = 1.0
                audit.progress_stage = "primary_calculation_withheld"
    node.last_seen_at = failed_at
    await db.commit()
    await db.refresh(attempt)
    return attempt


async def acknowledge_cancel(
    db: AsyncSession,
    *,
    node: WorkerNode,
    attempt_id: uuid.UUID,
    lease_id: str,
    now: datetime | None = None,
) -> ScienceExecutionAttempt:
    cancelled_at = now or _utcnow()
    observed = await get_worker_attempt(
        db,
        node=node,
        attempt_id=attempt_id,
        lock=False,
    )
    audit = None
    if observed.audit_id is not None:
        audit = await db.scalar(
            select(ClaimAudit)
            .where(ClaimAudit.id == observed.audit_id)
            .with_for_update()
        )
    job = await db.scalar(
        select(ResearchJob)
        .where(ResearchJob.job_id == observed.job_id)
        .with_for_update()
    )
    attempt = await get_worker_attempt(db, node=node, attempt_id=attempt_id)
    if not secrets.compare_digest(str(attempt.lease_id), str(lease_id)):
        raise WorkerProtocolError("stale_worker_lease", status_code=409)
    if attempt.status in {"SUCCEEDED", "FAILED", "EXPIRED"}:
        raise WorkerProtocolError("science_attempt_not_active", status_code=409)
    # A node may acknowledge a cancellation, but it must never originate one.
    # The authenticated user-facing cancel path persists these parent states
    # first. Requiring every applicable parent to already be CANCELLED makes an
    # unsolicited or replayed cancel ACK incapable of cancelling research.
    if job is None or job.status != "CANCELLED":
        raise WorkerProtocolError("worker_cancel_not_requested", status_code=409)
    if audit is not None and audit.lifecycle_status != "CANCELLED":
        raise WorkerProtocolError("worker_cancel_not_requested", status_code=409)
    attempt.status = "CANCELLED"
    attempt.completed_at = cancelled_at
    await db.commit()
    await db.refresh(attempt)
    return attempt


async def reconcile_expired_attempts(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """Requeue abandoned HTTPS-worker jobs from the PostgreSQL ledger.

    A lease becomes ineligible for result submission immediately at
    ``lease_expires_at``.  Reassignment waits for the documented ten-minute
    grace period so a briefly disconnected node can reconnect without two
    workers running the same job.  Redis is deliberately not consulted: a
    broker restart must not lose or duplicate the authoritative job state.
    """

    checked_at = now or _utcnow()
    cutoff = checked_at - timedelta(seconds=WORKER_REDISPATCH_GRACE_SECONDS)
    attempts = list(
        (
            await db.execute(
                select(ScienceExecutionAttempt)
                .where(
                    ScienceExecutionAttempt.status.in_({"LEASED", "RUNNING"}),
                    ScienceExecutionAttempt.lease_expires_at <= cutoff,
                )
                .order_by(ScienceExecutionAttempt.lease_expires_at.asc())
                .with_for_update(skip_locked=True)
                .limit(max(1, min(int(limit), 1000)))
            )
        )
        .scalars()
        .all()
    )
    reconciled = 0
    for attempt in attempts:
        job = await db.get(ResearchJob, attempt.job_id)
        audit = (
            await db.get(ClaimAudit, attempt.audit_id)
            if attempt.audit_id is not None
            else None
        )
        attempt.status = "EXPIRED"
        attempt.error_class = "worker_lease_expired"
        attempt.error = "The local science worker lease expired before completion"
        attempt.completed_at = checked_at
        if (
            job is not None
            and job.current_attempt_id == attempt.id
            and job.status == "RUNNING"
        ):
            job.current_attempt_id = None
            job.progress = 0.0
            max_attempt_number = WORKER_MAX_ATTEMPTS * (
                1 + max(0, int(audit.retry_count or 0)) if audit is not None else 1
            )
            if attempt.attempt_number < max_attempt_number:
                job.status = "QUEUED"
                job.progress_message = "waiting_for_worker_retry"
                job.error_class = "worker_lease_expired"
                job.error = None
                job.completed_at = None
                if audit is not None and audit.lifecycle_status == "RUNNING":
                    audit.progress_stage = "waiting_for_worker_retry"
                    audit.error_class = "worker_lease_expired"
                    audit.error = None
            else:
                job.status = "FAILED"
                job.progress_message = "worker_attempts_exhausted"
                job.error_class = "worker_attempts_exhausted"
                job.error = "The local science task exhausted its three signed attempts"
                job.completed_at = checked_at
                if audit is not None and audit.lifecycle_status == "RUNNING":
                    audit.lifecycle_status = "FAILED_RETRYABLE"
                    audit.scientific_verdict = None
                    audit.machine_support_eligible = False
                    audit.reproduction_ready = False
                    audit.publication_ready = False
                    audit.review_status = "NOT_SUBMITTED"
                    audit.progress_stage = "worker_attempts_exhausted"
                    audit.error_class = "worker_attempts_exhausted"
                    audit.error = job.error
                    audit.completed_at = checked_at
        reconciled += 1
    if reconciled:
        await db.commit()
    return reconciled
