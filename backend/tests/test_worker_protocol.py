"""Security and lease regressions for the local HTTPS science worker."""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.models.claim_audit_records import ClaimAudit
from app.models.research_records import ResearchJob
from app.models.schemas import User
from app.models.worker_records import (
    ScienceExecutionAttempt,
    WorkerEnrollmentToken,
    WorkerNode,
)
from app.services import worker_protocol as worker_protocol_service
from app.services.worker_protocol import (
    WORKER_PROTOCOL_VERSION,
    WorkerProtocolError,
    acknowledge_cancel,
    canonical_result_hash,
    canonical_worker_request,
    complete_attempt,
    create_enrollment_token,
    enroll_worker_node,
    fail_attempt,
    heartbeat_attempt,
    lease_next_task,
    reconcile_expired_attempts,
    verify_worker_request,
)
from app.services.workflow_registry_v2 import (
    get_worker_execution_binding,
    list_worker_execution_bindings,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: set[str] = set()

    async def set(self, key: str, _value: str, *, ex: int, nx: bool):
        assert ex == 600
        assert nx is True
        if key in self.values:
            return False
        self.values.add(key)
        return True


def _worker_keypair() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, base64.b64encode(public).decode("ascii")


def _private_seed(private: Ed25519PrivateKey) -> str:
    raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(raw).decode("ascii")


def _signed_headers(
    private: Ed25519PrivateKey,
    node: WorkerNode,
    *,
    method: str,
    path: str,
    body: bytes,
    nonce: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    timestamp_text = str(timestamp if timestamp is not None else int(time.time()))
    message = canonical_worker_request(
        method=method,
        path=path,
        timestamp=timestamp_text,
        nonce=nonce,
        body_sha256=hashlib.sha256(body).hexdigest(),
        worker_id=str(node.id),
        protocol_version=node.protocol_version,
    )
    return {
        "x-standard-astro-worker-id": str(node.id),
        "x-standard-astro-worker-timestamp": timestamp_text,
        "x-standard-astro-worker-nonce": nonce,
        "x-standard-astro-worker-protocol": node.protocol_version,
        "x-standard-astro-worker-signature": base64.b64encode(
            private.sign(message)
        ).decode("ascii"),
    }


async def _user(db_session, *, username: str = "worker-owner") -> User:
    row = User(
        id=uuid.uuid4(),
        username=username,
        email=f"{username}@localhost",
        password_hash="not-used",
        subscription_tier="solo",
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest.mark.asyncio
async def test_enrollment_is_single_use_and_stores_only_digest(db_session):
    user = await _user(db_session)
    _private, public_key = _worker_keypair()
    token, raw_code = await create_enrollment_token(db_session, user_id=user.id)

    assert raw_code.startswith("ASTRO-WORKER-")
    assert raw_code not in token.token_hash
    stored = await db_session.get(WorkerEnrollmentToken, token.id)
    assert stored is not None
    assert stored.token_hash == hashlib.sha256(raw_code.encode()).hexdigest()

    node = await enroll_worker_node(
        db_session,
        enrollment_code=raw_code,
        name="Mac mini",
        public_key=public_key,
        protocol_version="1",
    )
    assert node.user_id == user.id
    assert node.public_key_fingerprint.startswith("sha256:")

    with pytest.raises(WorkerProtocolError, match="enrollment_code_used"):
        await enroll_worker_node(
            db_session,
            enrollment_code=raw_code,
            name="Replay",
            public_key=_worker_keypair()[1],
            protocol_version="1",
        )


@pytest.mark.asyncio
async def test_enrollment_capacity_checks_lock_owner_row(db_session, monkeypatch):
    user = await _user(db_session, username="locked-enrollment-owner")
    observed_locks: list[bool] = []
    original = worker_protocol_service._require_active_worker_owner

    async def observe_owner_lock(
        db,
        *,
        user_id,
        check_restore_tombstone,
        lock=False,
    ):
        observed_locks.append(lock)
        return await original(
            db,
            user_id=user_id,
            check_restore_tombstone=check_restore_tombstone,
            lock=lock,
        )

    monkeypatch.setattr(
        worker_protocol_service,
        "_require_active_worker_owner",
        observe_owner_lock,
    )
    _token, raw_code = await create_enrollment_token(db_session, user_id=user.id)
    await enroll_worker_node(
        db_session,
        enrollment_code=raw_code,
        name="Capacity-locked node",
        public_key=_worker_keypair()[1],
        protocol_version="1",
    )
    assert observed_locks == [True, True]


@pytest.mark.asyncio
async def test_enrollment_cannot_outlive_owner_deactivation(db_session):
    user = await _user(db_session, username="deactivated-enrollment-owner")
    _token, raw_code = await create_enrollment_token(db_session, user_id=user.id)
    user.account_status = "DELETION_PENDING"
    await db_session.commit()

    with pytest.raises(WorkerProtocolError, match="worker_owner_inactive"):
        await enroll_worker_node(
            db_session,
            enrollment_code=raw_code,
            name="Late enrollment",
            public_key=_worker_keypair()[1],
            protocol_version="1",
        )


@pytest.mark.asyncio
async def test_signed_request_rejects_replay_and_revocation(db_session):
    user = await _user(db_session)
    private, public_key = _worker_keypair()
    _token, code = await create_enrollment_token(db_session, user_id=user.id)
    node = await enroll_worker_node(
        db_session,
        enrollment_code=code,
        name="Signed node",
        public_key=public_key,
        protocol_version="1",
    )
    body = b'{"lease_id":"example"}'
    redis = _FakeRedis()
    headers = _signed_headers(
        private,
        node,
        method="PUT",
        path="/api/compute/v1/attempts/1/heartbeat",
        body=body,
        nonce="nonce-000000000001",
    )
    authenticated = await verify_worker_request(
        db_session,
        method="PUT",
        path="/api/compute/v1/attempts/1/heartbeat",
        headers=headers,
        body=body,
        redis_client=redis,
    )
    assert authenticated.id == node.id

    with pytest.raises(WorkerProtocolError, match="worker_nonce_replayed"):
        await verify_worker_request(
            db_session,
            method="PUT",
            path="/api/compute/v1/attempts/1/heartbeat",
            headers=headers,
            body=body,
            redis_client=redis,
        )

    user.account_status = "DELETION_PENDING"
    await db_session.commit()
    inactive_headers = _signed_headers(
        private,
        node,
        method="POST",
        path="/api/compute/v1/tasks/claim",
        body=b"",
        nonce="nonce-000000000003",
    )
    with pytest.raises(WorkerProtocolError, match="worker_owner_inactive"):
        await verify_worker_request(
            db_session,
            method="POST",
            path="/api/compute/v1/tasks/claim",
            headers=inactive_headers,
            body=b"",
            redis_client=redis,
        )

    user.account_status = "ACTIVE"
    await db_session.commit()

    node.status = "REVOKED"
    node.revoked_at = datetime.now(timezone.utc)
    await db_session.commit()
    revoked_headers = _signed_headers(
        private,
        node,
        method="POST",
        path="/api/compute/v1/tasks/claim",
        body=b"",
        nonce="nonce-000000000002",
    )
    with pytest.raises(WorkerProtocolError, match="worker_revoked_or_unknown"):
        await verify_worker_request(
            db_session,
            method="POST",
            path="/api/compute/v1/tasks/claim",
            headers=revoked_headers,
            body=b"",
            redis_client=redis,
        )


@pytest.mark.asyncio
async def test_task_envelope_is_owner_bound_signed_and_contains_no_credentials(
    db_session,
):
    owner = await _user(db_session)
    other = await _user(db_session, username="other-owner")
    _owner_private, owner_public = _worker_keypair()
    _token, code = await create_enrollment_token(db_session, user_id=owner.id)
    node = await enroll_worker_node(
        db_session,
        enrollment_code=code,
        name="Owner node",
        public_key=owner_public,
        protocol_version="1",
    )
    now = datetime.now(timezone.utc)
    other_job = ResearchJob(
        job_id="other-job",
        user_id=other.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="b" * 64,
        args={"workflow_key": "union3_flat_lcdm_sn_only_v1"},
        status="QUEUED",
        background_backend="https_worker",
        created_at=now - timedelta(seconds=10),
    )
    owner_job = ResearchJob(
        job_id="owner-job",
        user_id=owner.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="a" * 64,
        args={
            "workflow_key": "union3_flat_lcdm_sn_only_v1",
            "normalized_inputs": {"parameter": "omegam"},
            "dataset_pins": [{"key": "union3", "sha256": "a" * 64}],
        },
        status="QUEUED",
        background_backend="https_worker",
        created_at=now,
    )
    db_session.add_all([other_job, owner_job])
    await db_session.commit()

    control_private = Ed25519PrivateKey.generate()
    attempt = await lease_next_task(
        db_session,
        node=node,
        private_key=_private_seed(control_private),
        key_id="control-2026-01",
        release_commit="1" * 40,
        image_digest="sha256:" + "2" * 64,
    )
    assert attempt is not None
    assert attempt.job_id == "owner-job"
    envelope = attempt.task_envelope
    assert envelope["workflow_key"] == "union3_flat_lcdm_sn_only_v1"
    assert envelope["input_sha256"] == "a" * 64
    assert "password" not in str(envelope).lower()
    assert "redis" not in str(envelope).lower()
    assert "database_url" not in str(envelope).lower()

    signature = base64.b64decode(envelope["server_signature"]["value"])
    unsigned = dict(envelope)
    unsigned.pop("server_signature")
    import json

    control_private.public_key().verify(
        signature,
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8"),
    )


@pytest.mark.asyncio
async def test_v2_task_envelope_is_bound_to_compiled_registry(db_session):
    owner = await _user(db_session, username="v2-worker-owner")
    _private, public = _worker_keypair()
    _token, code = await create_enrollment_token(db_session, user_id=owner.id)
    image_digest = "sha256:" + "2" * 64
    node = await enroll_worker_node(
        db_session,
        enrollment_code=code,
        name="V2 node",
        public_key=public,
        protocol_version=WORKER_PROTOCOL_VERSION,
        capabilities={
            "workflows": list_worker_execution_bindings(
                worker_image_digest=image_digest
            ),
            "concurrency": 1,
        },
        release_manifest={
            "git_commit": "1" * 40,
            "image_digest": image_digest,
        },
    )
    binding = get_worker_execution_binding("union3_flat_lcdm_sn_only_v1")
    job = ResearchJob(
        job_id="v2-owner-job",
        user_id=owner.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        workflow_key="union3_flat_lcdm_sn_only_v1",
        inputs_hash="a" * 64,
        args={
            "workflow_key": "union3_flat_lcdm_sn_only_v1",
            "worker_protocol_version": WORKER_PROTOCOL_VERSION,
            "dataset_pins": [],
        },
        status="QUEUED",
        background_backend="https_worker",
        capability_requirements={"protocol_version": WORKER_PROTOCOL_VERSION},
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()

    attempt = await lease_next_task(
        db_session,
        node=node,
        private_key=_private_seed(Ed25519PrivateKey.generate()),
        key_id="control-v2",
        release_commit="1" * 40,
        image_digest=image_digest,
    )
    assert attempt is not None
    envelope = attempt.task_envelope
    assert envelope["protocol_version"] == WORKER_PROTOCOL_VERSION
    assert envelope["workflow_version"] == binding["workflow_version"]
    assert envelope["registry_epoch"] == binding["registry_epoch"]
    assert envelope["registry_entry_hash"] == binding["registry_entry_hash"]
    assert envelope["entrypoint_id"] == binding["entrypoint_id"]
    assert envelope["worker_image_digest"] == image_digest
    assert "image_digest" not in envelope


@pytest.mark.asyncio
async def test_v1_node_gets_stable_upgrade_required_for_v2_job(db_session):
    owner = await _user(db_session, username="legacy-worker-owner")
    _private, public = _worker_keypair()
    _token, code = await create_enrollment_token(db_session, user_id=owner.id)
    node = await enroll_worker_node(
        db_session,
        enrollment_code=code,
        name="Legacy node",
        public_key=public,
        protocol_version="1",
    )
    db_session.add(
        ResearchJob(
            job_id="requires-v2",
            user_id=owner.id,
            tool_name="union3_flat_lcdm_sn_only_v1",
            inputs_hash="a" * 64,
            args={
                "workflow_key": "union3_flat_lcdm_sn_only_v1",
                "worker_protocol_version": WORKER_PROTOCOL_VERSION,
            },
            status="QUEUED",
            background_backend="https_worker",
            capability_requirements={"protocol_version": WORKER_PROTOCOL_VERSION},
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    with pytest.raises(WorkerProtocolError) as error:
        await lease_next_task(
            db_session,
            node=node,
            private_key=_private_seed(Ed25519PrivateKey.generate()),
            key_id="control-v2",
            release_commit="1" * 40,
            image_digest="sha256:" + "2" * 64,
        )
    assert error.value.code == "worker_upgrade_required"
    assert error.value.status_code == 426


@pytest.mark.asyncio
async def test_complete_is_idempotent_but_conflicting_replay_is_security_error(
    db_session,
):
    owner = await _user(db_session)
    _private, public = _worker_keypair()
    _token, code = await create_enrollment_token(db_session, user_id=owner.id)
    node = await enroll_worker_node(
        db_session,
        enrollment_code=code,
        name="Result node",
        public_key=public,
        protocol_version="1",
    )
    job = ResearchJob(
        job_id="result-job",
        user_id=owner.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="c" * 64,
        args={"workflow_key": "union3_flat_lcdm_sn_only_v1"},
        status="QUEUED",
        background_backend="https_worker",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    attempt = await lease_next_task(
        db_session,
        node=node,
        private_key=_private_seed(Ed25519PrivateKey.generate()),
        key_id="control",
        release_commit="3" * 40,
        image_digest="sha256:" + "4" * 64,
    )
    assert attempt is not None

    result = {"workflow_key": "union3_flat_lcdm_sn_only_v1", "best_fit": "0.356"}
    completed, replayed = await complete_attempt(
        db_session,
        node=node,
        attempt_id=attempt.id,
        lease_id=attempt.lease_id,
        result=result,
        claimed_result_hash=canonical_result_hash(result),
        diagnostics={"r_hat": "not_applicable"},
        artifact_manifest=[],
    )
    assert replayed is False
    assert completed.status == "SUCCEEDED"
    assert completed.result_hash == canonical_result_hash(result)
    assert job.result["publication_ready"] is False
    assert job.result["scientific_verdict"] is None

    _same, replayed = await complete_attempt(
        db_session,
        node=node,
        attempt_id=attempt.id,
        lease_id=attempt.lease_id,
        result=result,
        claimed_result_hash=None,
        diagnostics={},
        artifact_manifest=[],
    )
    assert replayed is True

    with pytest.raises(WorkerProtocolError, match="conflicting_worker_result"):
        await complete_attempt(
            db_session,
            node=node,
            attempt_id=attempt.id,
            lease_id=attempt.lease_id,
            result={**result, "best_fit": "0.400"},
            claimed_result_hash=None,
            diagnostics={},
            artifact_manifest=[],
        )


@pytest.mark.asyncio
async def test_expired_lease_requeues_from_postgres_and_rejects_late_result(db_session):
    owner = await _user(db_session)
    _private, public = _worker_keypair()
    _token, code = await create_enrollment_token(db_session, user_id=owner.id)
    node = await enroll_worker_node(
        db_session,
        enrollment_code=code,
        name="Disconnected node",
        public_key=public,
        protocol_version="1",
    )
    job = ResearchJob(
        job_id="disconnected-job",
        user_id=owner.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="d" * 64,
        args={"workflow_key": "union3_flat_lcdm_sn_only_v1"},
        status="QUEUED",
        background_backend="https_worker",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    leased_at = datetime.now(timezone.utc) - timedelta(minutes=13)
    attempt = await lease_next_task(
        db_session,
        node=node,
        private_key=_private_seed(Ed25519PrivateKey.generate()),
        key_id="control",
        release_commit="5" * 40,
        image_digest="sha256:" + "6" * 64,
        now=leased_at,
    )
    assert attempt is not None

    # The lease expired 11 minutes ago: past both the live lease and the
    # documented ten-minute reassignment grace.
    reconciled = await reconcile_expired_attempts(
        db_session,
        now=datetime.now(timezone.utc),
    )
    assert reconciled == 1
    await db_session.refresh(job)
    expired = await db_session.get(ScienceExecutionAttempt, attempt.id)
    assert expired is not None
    assert expired.status == "EXPIRED"
    assert job.status == "QUEUED"
    assert job.current_attempt_id is None

    with pytest.raises(WorkerProtocolError, match="science_attempt_not_active"):
        await complete_attempt(
            db_session,
            node=node,
            attempt_id=attempt.id,
            lease_id=attempt.lease_id,
            result={"workflow_key": "union3_flat_lcdm_sn_only_v1"},
            claimed_result_hash=None,
            diagnostics={},
            artifact_manifest=[],
        )


@pytest.mark.asyncio
async def test_redispatch_grace_does_not_create_overlapping_attempt(db_session):
    owner = await _user(db_session)
    _private, public = _worker_keypair()
    _token, code = await create_enrollment_token(db_session, user_id=owner.id)
    node = await enroll_worker_node(
        db_session,
        enrollment_code=code,
        name="Briefly offline node",
        public_key=public,
        protocol_version="1",
    )
    job = ResearchJob(
        job_id="grace-job",
        user_id=owner.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="e" * 64,
        args={"workflow_key": "union3_flat_lcdm_sn_only_v1"},
        status="QUEUED",
        background_backend="https_worker",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    attempt = await lease_next_task(
        db_session,
        node=node,
        private_key=_private_seed(Ed25519PrivateKey.generate()),
        key_id="control",
        release_commit="7" * 40,
        image_digest="sha256:" + "8" * 64,
        now=datetime.now(timezone.utc) - timedelta(minutes=4),
    )
    assert attempt is not None

    assert await reconcile_expired_attempts(db_session) == 0
    await db_session.refresh(job)
    assert job.status == "RUNNING"
    assert job.current_attempt_id == attempt.id


@pytest.mark.asyncio
async def test_unsolicited_cancel_ack_cannot_cancel_active_research(db_session):
    owner = await _user(db_session, username="unsolicited-cancel-owner")
    _private, public = _worker_keypair()
    _token, code = await create_enrollment_token(db_session, user_id=owner.id)
    node = await enroll_worker_node(
        db_session,
        enrollment_code=code,
        name="Untrusted cancel node",
        public_key=public,
        protocol_version="1",
    )
    job = ResearchJob(
        job_id="unsolicited-cancel-job",
        user_id=owner.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="1" * 64,
        args={"workflow_key": "union3_flat_lcdm_sn_only_v1"},
        status="QUEUED",
        background_backend="https_worker",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    attempt = await lease_next_task(
        db_session,
        node=node,
        private_key=_private_seed(Ed25519PrivateKey.generate()),
        key_id="control",
        release_commit="2" * 40,
        image_digest="sha256:" + "3" * 64,
    )
    assert attempt is not None

    with pytest.raises(WorkerProtocolError, match="worker_cancel_not_requested"):
        await acknowledge_cancel(
            db_session,
            node=node,
            attempt_id=attempt.id,
            lease_id=attempt.lease_id,
        )
    await db_session.refresh(job)
    await db_session.refresh(attempt)
    assert job.status == "RUNNING"
    assert attempt.status == "LEASED"

    # The authenticated owner path records cancellation before the Worker ACK.
    job.status = "CANCELLED"
    job.completed_at = datetime.now(timezone.utc)
    await db_session.commit()
    acknowledged = await acknowledge_cancel(
        db_session,
        node=node,
        attempt_id=attempt.id,
        lease_id=attempt.lease_id,
    )
    assert acknowledged.status == "CANCELLED"


@pytest.mark.asyncio
async def test_draining_node_requeues_attempt_without_cancelling_audit(db_session):
    owner = await _user(db_session, username="draining-node-owner")
    _private, public = _worker_keypair()
    _token, code = await create_enrollment_token(db_session, user_id=owner.id)
    node = await enroll_worker_node(
        db_session,
        enrollment_code=code,
        name="Draining node",
        public_key=public,
        protocol_version="1",
    )
    audit = ClaimAudit(
        id=uuid.uuid4(),
        user_id=owner.id,
        request_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        lifecycle_status="QUEUED",
        mode="execute_registered",
        claim_text="Registered Union3 reproduction",
        source_kind="arxiv",
        source_value="2311.12098v4",
    )
    job = ResearchJob(
        job_id=f"draining-job-{audit.id.hex}",
        user_id=owner.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="4" * 64,
        args={
            "workflow_key": "union3_flat_lcdm_sn_only_v1",
            "audit_id": str(audit.id),
        },
        status="QUEUED",
        background_backend="https_worker",
        created_at=datetime.now(timezone.utc),
    )
    audit.child_job_ids = [job.job_id]
    db_session.add_all([audit, job])
    await db_session.commit()
    attempt = await lease_next_task(
        db_session,
        node=node,
        private_key=_private_seed(Ed25519PrivateKey.generate()),
        key_id="control",
        release_commit="5" * 40,
        image_digest="sha256:" + "6" * 64,
    )
    assert attempt is not None
    node.status = "DRAINING"
    await db_session.commit()

    heartbeat = await heartbeat_attempt(
        db_session,
        node=node,
        attempt_id=attempt.id,
        lease_id=attempt.lease_id,
        progress=None,
        checkpoint=None,
    )
    assert heartbeat["action"] == "drain"
    released = await fail_attempt(
        db_session,
        node=node,
        attempt_id=attempt.id,
        lease_id=attempt.lease_id,
        error_class="worker_draining",
        retryable=True,
    )
    await db_session.refresh(job)
    await db_session.refresh(audit)
    assert released.status == "FAILED"
    assert job.status == "QUEUED"
    assert job.current_attempt_id is None
    assert job.progress_message == "waiting_for_worker_retry"
    assert audit.lifecycle_status == "RUNNING"
    assert audit.progress_stage == "waiting_for_worker_retry"


@pytest.mark.asyncio
async def test_registered_job_propagates_running_and_retryable_failure_to_audit(
    db_session,
):
    owner = await _user(db_session, username="audit-state-owner")
    _private, public = _worker_keypair()
    _token, code = await create_enrollment_token(db_session, user_id=owner.id)
    node = await enroll_worker_node(
        db_session,
        enrollment_code=code,
        name="Audit state node",
        public_key=public,
        protocol_version="1",
    )
    audit = ClaimAudit(
        id=uuid.uuid4(),
        user_id=owner.id,
        request_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        lifecycle_status="QUEUED",
        mode="execute_registered",
        claim_text="Registered Union3 reproduction",
        source_kind="arxiv",
        source_value="2311.12098v4",
    )
    job = ResearchJob(
        job_id=f"union3-primary-{audit.id.hex}",
        user_id=owner.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="f" * 64,
        args={
            "workflow_key": "union3_flat_lcdm_sn_only_v1",
            "audit_id": str(audit.id),
        },
        status="QUEUED",
        background_backend="https_worker",
        created_at=datetime.now(timezone.utc),
    )
    audit.child_job_ids = [job.job_id]
    db_session.add_all([audit, job])
    await db_session.commit()

    for attempt_number in range(1, 4):
        attempt = await lease_next_task(
            db_session,
            node=node,
            private_key=_private_seed(Ed25519PrivateKey.generate()),
            key_id="control",
            release_commit="9" * 40,
            image_digest="sha256:" + "a" * 64,
        )
        assert attempt is not None
        assert attempt.attempt_number == attempt_number
        await db_session.refresh(audit)
        assert audit.lifecycle_status == "RUNNING"
        await fail_attempt(
            db_session,
            node=node,
            attempt_id=attempt.id,
            lease_id=attempt.lease_id,
            error_class="worker_network_timeout",
            retryable=True,
        )

    await db_session.refresh(audit)
    await db_session.refresh(job)
    assert job.status == "FAILED"
    assert job.current_attempt_id is None
    assert audit.lifecycle_status == "FAILED_RETRYABLE"
    assert audit.scientific_verdict is None
    assert audit.reproduction_ready is False
