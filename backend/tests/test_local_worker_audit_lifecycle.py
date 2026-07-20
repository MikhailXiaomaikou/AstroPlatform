"""Cancellation and deletion regressions for registered HTTPS-worker Audits."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models.claim_audit_records import ClaimAudit
from app.models.research_records import ResearchJob
from app.models.worker_records import (
    ScienceExecutionAttempt,
    WorkerArtifactIssuance,
    WorkerNode,
)
from app.services.worker_protocol import (
    WorkerProtocolError,
    acknowledge_cancel,
    complete_attempt,
    fail_attempt,
)


async def test_registered_only_mode_withholds_stale_legacy_task_and_rejects_retry(
    app_client,
    db_session,
    test_user,
    monkeypatch,
):
    from app.models import database
    from app.tasks.claim_audit_tasks import _process

    user, token = test_user
    monkeypatch.setattr(settings, "claim_audit_enabled", True)
    monkeypatch.setattr(settings, "claim_audit_execution_mode", "https_worker")
    session_factory = async_sessionmaker(
        bind=db_session.bind,
        expire_on_commit=False,
    )
    monkeypatch.setattr(database, "async_session", session_factory)

    queued = ClaimAudit(
        id=uuid.uuid4(),
        user_id=user.id,
        request_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        lifecycle_status="QUEUED",
        mode="audit_only",
        claim_text="Legacy generic claim",
        source_kind="doi",
        source_value="10.0000/legacy",
    )
    retryable = ClaimAudit(
        id=uuid.uuid4(),
        user_id=user.id,
        request_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        lifecycle_status="FAILED_RETRYABLE",
        mode="audit_only",
        claim_text="Legacy retry claim",
        source_kind="doi",
        source_value="10.0000/legacy-retry",
    )
    db_session.add_all([queued, retryable])
    await db_session.commit()

    assert await _process(queued.id) == "legacy_withheld"
    await db_session.refresh(queued)
    assert queued.lifecycle_status == "COMPLETED"
    assert queued.scientific_verdict == "WITHHELD"
    assert queued.machine_support_eligible is False
    assert queued.error_class == "legacy_claim_audit_disabled"

    response = await app_client.post(
        f"/api/research/claim-audits/{retryable.id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409, response.text
    await db_session.refresh(retryable)
    assert retryable.lifecycle_status == "FAILED_RETRYABLE"
    assert retryable.scientific_verdict is None


async def test_cancel_and_delete_cover_uppercase_local_worker_state(
    app_client,
    db_session,
    test_user,
    monkeypatch,
):
    user, token = test_user
    monkeypatch.setattr(settings, "claim_audit_enabled", True)
    headers = {"Authorization": f"Bearer {token}"}
    audit_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    job_id = f"union3-primary-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    audit = ClaimAudit(
        id=audit_id,
        user_id=user.id,
        request_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        lifecycle_status="RUNNING",
        mode="execute_registered",
        claim_text="Registered Union3 reproduction",
        source_kind="arxiv",
        source_value="2311.12098v4",
        child_job_ids=[job_id],
        independent_verification_job_id=job_id,
    )
    job = ResearchJob(
        job_id=job_id,
        user_id=user.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="a" * 64,
        args={"workflow_key": "union3_flat_lcdm_sn_only_v1"},
        args_replayable=True,
        status="RUNNING",
        background_backend="https_worker",
        current_attempt_id=attempt_id,
        created_at=now,
        started_at=now,
    )
    node = WorkerNode(
        id=uuid.uuid4(),
        user_id=user.id,
        name="cancel test node",
        public_key="test-only",
        public_key_fingerprint="sha256:" + "b" * 64,
        protocol_version="1",
        status="ACTIVE",
        capabilities={},
        release_manifest={},
    )
    attempt = ScienceExecutionAttempt(
        id=attempt_id,
        job_id=job_id,
        audit_id=audit_id,
        user_id=user.id,
        worker_node_id=node.id,
        attempt_number=1,
        status="RUNNING",
        lease_id="c" * 64,
        lease_expires_at=now + timedelta(minutes=2),
        input_hash=job.inputs_hash,
        task_envelope={},
        artifact_manifest=[],
    )
    db_session.add_all([audit, job, node, attempt])
    issuance = WorkerArtifactIssuance(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        attempt_id=attempt_id,
        user_id=user.id,
        worker_node_id=node.id,
        artifact_name="pending.json",
        artifact_ref=f"science-attempts/{user.id}/{attempt_id}/uploads/pending.json",
        authoritative_ref=(
            f"science-attempts/{user.id}/{attempt_id}/verified/pending.json"
        ),
        sha256="d" * 64,
        size_bytes=1,
        content_type="application/json",
        expires_at=now + timedelta(minutes=15),
    )
    db_session.add(issuance)
    await db_session.commit()

    response = await app_client.post(
        f"/api/research/claim-audits/{audit_id}/cancel",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    await db_session.refresh(job)
    assert job.status == "CANCELLED"

    with pytest.raises(WorkerProtocolError, match="science_job_cancelled"):
        await fail_attempt(
            db_session,
            node=node,
            attempt_id=attempt.id,
            lease_id=attempt.lease_id,
            error_class="worker_network_interrupted",
            retryable=True,
        )
    await db_session.refresh(audit)
    await db_session.refresh(job)
    assert audit.lifecycle_status == "CANCELLED"
    assert job.status == "CANCELLED"

    with pytest.raises(WorkerProtocolError, match="science_job_cancelled"):
        await complete_attempt(
            db_session,
            node=node,
            attempt_id=attempt.id,
            lease_id=attempt.lease_id,
            result={"workflow_id": "union3_flat_lcdm_sn_only_v1"},
            claimed_result_hash=None,
            diagnostics={},
            artifact_manifest=[],
        )

    deleted = await app_client.delete(
        f"/api/research/claim-audits/{audit_id}",
        headers=headers,
    )
    assert deleted.status_code == 409

    await acknowledge_cancel(
        db_session,
        node=node,
        attempt_id=attempt.id,
        lease_id=attempt.lease_id,
    )
    deleted = await app_client.delete(
        f"/api/research/claim-audits/{audit_id}",
        headers=headers,
    )
    assert deleted.status_code == 409
    issuance.expires_at = now - timedelta(hours=2)
    await db_session.commit()
    deleted = await app_client.delete(
        f"/api/research/claim-audits/{audit_id}",
        headers=headers,
    )
    assert deleted.status_code == 204, deleted.text
    assert await db_session.get(ScienceExecutionAttempt, attempt_id) is None
    assert await db_session.get(ResearchJob, job_id) is None
    assert await db_session.get(ClaimAudit, audit_id) is None
