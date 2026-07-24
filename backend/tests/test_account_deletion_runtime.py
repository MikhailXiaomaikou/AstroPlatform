"""Runtime revocation and lease regressions for account deletion."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.models.claim_audit_records import (
    AccountDeletionTombstone,
    ArtifactCleanupQueue,
    ClaimAudit,
    EvidencePack,
)
from app.models.database import Base
from app.models.research_records import ResearchJob
from app.models.schemas import (
    ChatSession,
    DataFile,
    PipelineRun,
    RunResult,
    ScheduledRun,
    User,
)
from app.models.worker_records import (
    ScienceExecutionAttempt,
    WorkerArtifactIssuance,
    WorkerNode,
)
from app.services.account_deletion import (
    AccountArtifactUploadActive,
    claim_account_deletion_lease,
    deletion_user_fingerprint,
    erase_account_data,
    purge_user_runtime_state,
)


def _audit(user_id: uuid.UUID) -> ClaimAudit:
    return ClaimAudit(
        id=uuid.uuid4(),
        user_id=user_id,
        request_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        lifecycle_status="QUEUED",
        mode="audit_only",
        claim_text="H0 = 70 km/s/Mpc",
        source_kind="doi",
        source_value="10.0000/runtime-test",
        evidence_input_refs=[],
        dataset_hints=[],
        normalized_claims=[],
        capability_gaps=[],
        evidence_record_ids=[],
        child_job_ids=[],
    )


async def test_delete_request_cancels_every_runtime_and_disables_schedule(
    app_client,
    db_session,
    test_user,
    monkeypatch,
):
    from app.api import privacy

    user, token = test_user
    audit = _audit(user.id)
    job_id = f"union3-primary-{uuid.uuid4().hex}"
    attempt_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    node = WorkerNode(
        id=uuid.uuid4(),
        user_id=user.id,
        name="deletion-race-worker",
        public_key="test-only",
        public_key_fingerprint="sha256:" + "a" * 64,
        protocol_version="1",
        status="ACTIVE",
        capabilities={},
        release_manifest={},
    )
    job = ResearchJob(
        job_id=job_id,
        user_id=user.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="b" * 64,
        args={"workflow_key": "union3_flat_lcdm_sn_only_v1"},
        status="RUNNING",
        background_backend="https_worker",
        current_attempt_id=attempt_id,
        created_at=now,
        started_at=now,
    )
    attempt = ScienceExecutionAttempt(
        id=attempt_id,
        job_id=job_id,
        audit_id=audit.id,
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
    audit.child_job_ids = [job_id]
    pipeline = PipelineRun(user_id=user.id, dag={"nodes": [], "edges": []}, status="running")
    schedule = ScheduledRun(
        user_id=user.id,
        name="owned",
        dag={"nodes": [], "edges": []},
        input_data_id="uploads/input.fits",
        cron_expr="0 * * * *",
        enabled=True,
        next_run_at=datetime.now(timezone.utc),
    )
    db_session.add_all([audit, node, job, attempt, pipeline, schedule])
    await db_session.commit()

    monkeypatch.setattr(privacy, "write_external_deletion_tombstone", lambda **_: "ok")
    monkeypatch.setattr(privacy, "_dispatch_account_erasure", lambda *_: False)

    async def no_runtime_state(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(privacy, "purge_user_runtime_state", no_runtime_state)
    response = await app_client.request(
        "DELETE",
        "/api/auth/account",
        json={"confirmation": user.username, "password": "securepassword123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202, response.text

    await db_session.refresh(audit)
    await db_session.refresh(pipeline)
    await db_session.refresh(schedule)
    await db_session.refresh(node)
    await db_session.refresh(job)
    await db_session.refresh(attempt)
    assert audit.lifecycle_status == "CANCELLED"
    assert audit.worker_lease_id is None
    assert pipeline.status == "cancelled"
    assert schedule.enabled is False
    assert schedule.next_run_at is None
    assert node.status == "REVOKED"
    assert job.status == "CANCELLED"
    assert attempt.status == "CANCELLED"


async def test_delete_request_remains_durable_when_initial_runtime_purge_fails(
    app_client,
    db_session,
    test_user,
    monkeypatch,
):
    from app.api import privacy

    user, token = test_user
    monkeypatch.setattr(privacy, "write_external_deletion_tombstone", lambda **_: "ok")
    monkeypatch.setattr(privacy, "_dispatch_account_erasure", lambda *_: False)

    async def failed_runtime_purge(*_args, **_kwargs):
        raise RuntimeError("shared KV unavailable")

    monkeypatch.setattr(privacy, "purge_user_runtime_state", failed_runtime_purge)
    response = await app_client.request(
        "DELETE",
        "/api/auth/account",
        json={"confirmation": user.username, "password": "securepassword123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202, response.text

    await db_session.refresh(user)
    tombstone = await db_session.scalar(select(AccountDeletionTombstone))
    assert user.account_status == "DELETION_PENDING"
    assert tombstone is not None
    assert tombstone.status == "PENDING"


async def test_deletion_tombstone_lease_is_exclusive_and_expiry_allows_takeover(
    db_session,
    test_user,
):
    user, _token = test_user
    now = datetime.now(timezone.utc)
    tombstone = AccountDeletionTombstone(
        id=uuid.uuid4(),
        user_fingerprint=deletion_user_fingerprint(user.id),
        status="PENDING",
        receipt_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        requested_at=now,
        backup_expires_at=now + timedelta(days=30),
    )
    db_session.add(tombstone)
    await db_session.commit()

    first = await claim_account_deletion_lease(
        db_session,
        user_id=user.id,
        tombstone_id=tombstone.id,
    )
    assert first
    assert await claim_account_deletion_lease(
        db_session,
        user_id=user.id,
        tombstone_id=tombstone.id,
    ) is None

    await db_session.execute(
        update(AccountDeletionTombstone)
        .where(AccountDeletionTombstone.id == tombstone.id)
        .values(lease_expires_at=now - timedelta(seconds=1))
    )
    await db_session.commit()
    takeover = await claim_account_deletion_lease(
        db_session,
        user_id=user.id,
        tombstone_id=tombstone.id,
    )
    assert takeover and takeover != first


async def test_runtime_purge_removes_jobs_tools_checkpoints_python_and_cache(
    db_session,
    test_user,
    monkeypatch,
):
    from app.pipeline import engine as pipeline_engine
    from app.services import ai_tools, async_tool_runtime, code_executor, workflow_checkpoint
    from app.services.agent_runtime.tool_execution import _checkpoint_cache_refs
    from app.services.user_tools import _STORE as user_tool_store

    user, _token = test_user
    chat_id = uuid.uuid4()
    python_session_id = uuid.uuid4().hex
    db_session.add(
        ChatSession(id=chat_id, user_id=user.id, title="owned", messages=[])
    )
    await db_session.commit()

    banner = async_tool_runtime.submit_async_job(
        "runtime_test",
        {},
        user_id=str(user.id),
        session_id=str(chat_id),
    )
    user_tool_store.set(
        f"user:{user.id}:macro",
        {"tool_id": "macro"},
        ttl=3600,
    )
    workflow_checkpoint.record_step(
        str(chat_id),
        "search_objects",
        "input-hash",
        "completed",
        _checkpoint_cache_refs(
            "search_objects", {"results": [{"name": "owned"}]}, python_session_id
        ),
    )
    code_executor.get_session_vars(python_session_id)["secret"] = "owned"
    ai_tools.store_session_results("latest", python_session_id, [{"secret": "owned"}])
    ai_tools.store_session_results("derived_sample", python_session_id, {"secret": "owned"})
    suffix_collision_session = f"foreign:{python_session_id}"
    ai_tools.store_session_results(
        "latest", suffix_collision_session, [{"secret": "foreign"}]
    )
    cleared: list[str] = []
    monkeypatch.setattr(
        pipeline_engine,
        "delete_owner_pipeline_cache_sync",
        lambda owner_id, strict=True: cleared.append(str(owner_id)) or 2,
    )

    counts = await purge_user_runtime_state(
        db_session,
        user_id=user.id,
        strict_pipeline_cache=True,
    )
    assert async_tool_runtime.get_async_job(banner["job_id"], owner_id=str(user.id)) is None
    assert user_tool_store.get(f"user:{user.id}:macro") is None
    assert workflow_checkpoint.get_checkpoint(str(chat_id)) is None
    assert code_executor.is_session_deleted(python_session_id) is True
    assert code_executor.has_session_state(python_session_id) is False
    assert ai_tools.get_session_cached_results("latest", python_session_id) is None
    assert ai_tools.get_session_cached_results("derived_sample", python_session_id) is None
    assert ai_tools.get_session_cached_results(
        "latest", suffix_collision_session
    ) == [{"secret": "foreign"}]
    assert cleared == [str(user.id)]
    assert counts["pipeline_cache_keys_deleted"] == 2


async def test_runtime_purge_discovers_search_only_session_from_owner_index(
    db_session,
    test_user,
    monkeypatch,
):
    from app.pipeline import engine as pipeline_engine
    from app.services import ai_tools, code_executor

    user, _token = test_user
    runtime_session = ai_tools.build_trusted_python_session_id(
        user_id=str(user.id),
        chat_session_id=str(uuid.uuid4()),
        requested_session_id="search-only",
    )
    code_executor.register_user_session(str(user.id), runtime_session)
    ai_tools.store_session_results(
        "latest", runtime_session, [{"name": "search-only-secret"}]
    )
    monkeypatch.setattr(
        pipeline_engine,
        "delete_owner_pipeline_cache_sync",
        lambda _owner_id, strict=True: 0,
    )

    counts = await purge_user_runtime_state(
        db_session,
        user_id=user.id,
        selected={"chat_sessions": set()},
        strict_pipeline_cache=True,
    )

    assert counts["python_sessions_deleted"] == 1
    assert ai_tools.get_session_cached_results("latest", runtime_session) is None
    assert code_executor.list_user_sessions_strict(str(user.id)) == set()


@pytest.mark.parametrize(
    "failure_point",
    ["scan", "get", "set", "delete", "delete_verification"],
)
async def test_kv_failure_prevents_account_erasure_completion(
    failure_point,
    db_session,
    test_user,
    monkeypatch,
):
    from app.pipeline import engine as pipeline_engine
    from app.services import _kv_store, workflow_checkpoint
    from app.services.user_tools import _STORE as user_tool_store

    user, _token = test_user
    user_id = user.id
    if failure_point in {"get", "set"}:
        chat_id = uuid.uuid4()
        db_session.add(
            ChatSession(id=chat_id, user_id=user_id, title="owned", messages=[])
        )
        await db_session.commit()
        workflow_checkpoint.record_step(
            str(chat_id),
            "query_archive",
            "input-hash",
            "completed",
        )
    elif failure_point in {"delete", "delete_verification"}:
        user_tool_store.set(
            f"user:{user_id}:private_macro",
            {"tool_id": "private_macro"},
            ttl=3600,
        )

    now = datetime.now(timezone.utc)
    tombstone = AccountDeletionTombstone(
        id=uuid.uuid4(),
        user_fingerprint=deletion_user_fingerprint(user_id),
        status="PENDING",
        receipt_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        requested_at=now,
        backup_expires_at=now + timedelta(days=30),
    )
    db_session.add(tombstone)
    await db_session.commit()
    monkeypatch.setattr(
        pipeline_engine,
        "delete_owner_pipeline_cache_sync",
        lambda *_args, **_kwargs: 0,
    )

    backend = _kv_store._get_backend()

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"injected KV {failure_point} failure")

    if failure_point == "scan":
        monkeypatch.setattr(backend, "scan_keys_strict", fail)
    elif failure_point == "get":
        monkeypatch.setattr(backend, "get_strict", fail)
    elif failure_point == "set":
        monkeypatch.setattr(backend, "set_strict", fail)
    elif failure_point == "delete":
        monkeypatch.setattr(backend, "delete_strict", fail)
    else:
        # A backend that acknowledges DELETE without removing the key is also
        # a failure: JsonKvStore.delete_strict must catch it by reading back.
        monkeypatch.setattr(backend, "delete_strict", lambda _key: None)

    with pytest.raises(RuntimeError):
        await erase_account_data(
            user_id=user_id,
            tombstone_id=tombstone.id,
            db=db_session,
        )

    await db_session.refresh(tombstone)
    assert tombstone.status == "RETRYABLE"
    assert tombstone.completed_at is None
    assert await db_session.get(User, user_id) is not None


async def test_erasure_discovers_pipeline_run_result_object(
    db_session,
    test_user,
    monkeypatch,
    tmp_path,
):
    from app.config import settings
    from app.services import account_deletion
    from app.storage import download_fits, upload_fits

    user, _token = test_user
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "objects"))
    output_path = f"pipeline/{user.id}/result.fits"
    research_output_path = f"processed/{user.id}/chat-image.fits"
    worker_output_path = f"science-attempts/{user.id}/profile.svg"
    worker_staging_path = (
        f"science-attempts/{user.id}/artifact-attempt/uploads/profile.json"
    )
    worker_authoritative_path = (
        f"science-attempts/{user.id}/artifact-attempt/verified/profile.json"
    )
    restored_orphan_path = (
        f"science-attempts/{user.id}/restored-ledger-gap/orphan.bin"
    )
    pending_output_path = f"processed/{user.id}/late-image.fits"
    upload_fits(output_path, b"owned pipeline output")
    upload_fits(research_output_path, b"owned chat-tool output")
    upload_fits(worker_output_path, b"owned worker profile")
    upload_fits(worker_staging_path, b"staging worker artifact")
    upload_fits(worker_authoritative_path, b"verified worker artifact")
    upload_fits(restored_orphan_path, b"restored database orphan")
    upload_fits(pending_output_path, b"late worker output")
    run = PipelineRun(user_id=user.id, dag={"nodes": [], "edges": []}, status="cancelled")
    db_session.add(run)
    await db_session.flush()
    db_session.add(RunResult(run_id=run.id, node_id="node", output_path=output_path))
    now = datetime.now(timezone.utc)
    attempt_id = uuid.uuid4()
    research_job = ResearchJob(
        job_id=f"owned-{uuid.uuid4().hex}",
        user_id=user.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="a" * 64,
        args={},
        args_replayable=True,
        status="COMPLETED",
        result={"product": {"output_path": research_output_path}},
        background_backend="https_worker",
        current_attempt_id=attempt_id,
        created_at=now,
        completed_at=now,
    )
    node = WorkerNode(
        id=uuid.uuid4(),
        user_id=user.id,
        name="erasure test node",
        public_key="test-only",
        public_key_fingerprint="sha256:" + "b" * 64,
        protocol_version="1",
        status="ACTIVE",
        capabilities={},
        release_manifest={},
    )
    attempt = ScienceExecutionAttempt(
        id=attempt_id,
        job_id=research_job.job_id,
        user_id=user.id,
        worker_node_id=node.id,
        attempt_number=1,
        status="SUCCEEDED",
        lease_id="c" * 64,
        lease_expires_at=now,
        input_hash=research_job.inputs_hash,
        task_envelope={},
        artifact_manifest=[
            {
                "artifact_ref": worker_output_path,
                "sha256": "d" * 64,
                "size_bytes": 20,
                "content_type": "image/svg+xml",
                "status": "VERIFIED",
            }
        ],
        completed_at=now,
    )
    issuance = WorkerArtifactIssuance(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        attempt_id=attempt_id,
        user_id=user.id,
        worker_node_id=node.id,
        artifact_name="profile.json",
        artifact_ref=worker_staging_path,
        authoritative_ref=worker_authoritative_path,
        sha256="e" * 64,
        size_bytes=23,
        content_type="application/json",
        expires_at=now - timedelta(hours=2),
    )
    db_session.add_all([research_job, node, attempt, issuance])
    tombstone = AccountDeletionTombstone(
        id=uuid.uuid4(),
        user_fingerprint=deletion_user_fingerprint(user.id),
        status="PENDING",
        receipt_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        pending_user_id=user.id,
        pending_artifact_refs=[pending_output_path],
        requested_at=now,
        backup_expires_at=now + timedelta(days=30),
    )
    db_session.add(tombstone)
    await db_session.commit()

    async def no_runtime_state(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(account_deletion, "purge_user_runtime_state", no_runtime_state)
    result = await erase_account_data(
        user_id=user.id,
        tombstone_id=tombstone.id,
        db=db_session,
    )
    assert result["objects_deleted"] == 7
    for erased_path in (
        output_path,
        research_output_path,
        worker_output_path,
        worker_staging_path,
        worker_authoritative_path,
        restored_orphan_path,
        pending_output_path,
    ):
        try:
            download_fits(erased_path)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError(f"Owned output survived account erasure: {erased_path}")
    await db_session.refresh(tombstone)
    assert tombstone.status == "COMPLETED"
    assert tombstone.pending_user_id is None
    assert tombstone.pending_artifact_refs == []


async def test_erasure_waits_until_direct_upload_capability_is_quiescent(
    db_session,
    test_user,
):
    user, _token = test_user
    now = datetime.now(timezone.utc)
    attempt_id = uuid.uuid4()
    job = ResearchJob(
        job_id=f"active-upload-{uuid.uuid4().hex}",
        user_id=user.id,
        tool_name="union3_flat_lcdm_sn_only_v1",
        inputs_hash="f" * 64,
        args={},
        args_replayable=True,
        status="CANCELLED",
        background_backend="https_worker",
        current_attempt_id=attempt_id,
        created_at=now,
        completed_at=now,
    )
    node = WorkerNode(
        id=uuid.uuid4(),
        user_id=user.id,
        name="active upload node",
        public_key="test-only",
        public_key_fingerprint="sha256:" + "9" * 64,
        protocol_version="1",
        status="REVOKED",
        capabilities={},
        release_manifest={},
    )
    attempt = ScienceExecutionAttempt(
        id=attempt_id,
        job_id=job.job_id,
        user_id=user.id,
        worker_node_id=node.id,
        attempt_number=1,
        status="CANCELLED",
        lease_id="8" * 64,
        lease_expires_at=now,
        input_hash=job.inputs_hash,
        task_envelope={},
        artifact_manifest=[],
        completed_at=now,
    )
    issuance = WorkerArtifactIssuance(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        attempt_id=attempt.id,
        user_id=user.id,
        worker_node_id=node.id,
        artifact_name="late.bin",
        artifact_ref=(
            f"science-attempts/{user.id}/{attempt.id}/uploads/late.bin"
        ),
        authoritative_ref=(
            f"science-attempts/{user.id}/{attempt.id}/verified/late.bin"
        ),
        sha256="7" * 64,
        size_bytes=1,
        content_type="application/octet-stream",
        expires_at=now + timedelta(minutes=15),
    )
    tombstone = AccountDeletionTombstone(
        id=uuid.uuid4(),
        user_fingerprint=deletion_user_fingerprint(user.id),
        status="PENDING",
        receipt_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        pending_user_id=user.id,
        pending_artifact_refs=[],
        requested_at=now,
        backup_expires_at=now + timedelta(days=30),
    )
    db_session.add_all([job, node, attempt, issuance, tombstone])
    await db_session.commit()
    user_id = user.id
    issuance_id = issuance.id

    with pytest.raises(AccountArtifactUploadActive):
        await erase_account_data(
            user_id=user_id,
            tombstone_id=tombstone.id,
            db=db_session,
        )

    await db_session.refresh(tombstone)
    assert tombstone.status == "RETRYABLE"
    assert tombstone.last_error_class == "AccountArtifactUploadActive"
    assert await db_session.get(User, user_id) is not None
    assert await db_session.get(WorkerArtifactIssuance, issuance_id) is not None


def test_late_worker_cleanup_failure_is_queued_durably(tmp_path, monkeypatch):
    from app.services import account_deletion, durable_research_records
    from app.tasks import privacy_tasks

    database_path = tmp_path / "late-artifacts.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    owner_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    tombstone_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(
            AccountDeletionTombstone(
                id=tombstone_id,
                user_fingerprint=deletion_user_fingerprint(owner_id),
                status="COMPLETED",
                receipt_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                requested_at=now,
                completed_at=now,
                backup_expires_at=now + timedelta(days=30),
            )
        )
        session.commit()

    monkeypatch.setattr(durable_research_records, "_engine", lambda: engine)
    monkeypatch.setattr(
        account_deletion,
        "delete_fits_all_versions",
        lambda _path: (_ for _ in ()).throw(OSError("object store unavailable")),
    )
    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        privacy_tasks.erase_account_task,
        "delay",
        lambda user_id, tombstone_id: dispatched.append((user_id, tombstone_id)),
    )

    deleted = account_deletion.dispose_deleted_account_result(
        user_id=owner_id,
        result={"nested": {"output_path": "processed/late.fits"}},
    )

    assert deleted == 0
    with Session(engine) as session:
        tombstone = session.get(AccountDeletionTombstone, tombstone_id)
        assert tombstone.status == "RETRYABLE"
        assert tombstone.completed_at is None
        assert tombstone.pending_user_id == owner_id
        assert tombstone.pending_artifact_refs == ["processed/late.fits"]
    assert dispatched == [(str(owner_id), str(tombstone_id))]
    engine.dispose()


def test_server_tool_output_gets_owner_ledger_before_return(tmp_path, monkeypatch):
    from app.services import account_deletion, durable_research_records

    database_path = tmp_path / "tool-output-ledger.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    owner_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(
            User(
                id=owner_id,
                username="tool-owner",
                email="tool-owner@example.invalid",
                password_hash=hash_password("password123"),
                account_status="ACTIVE",
            )
        )
        session.commit()
    monkeypatch.setattr(durable_research_records, "_engine", lambda: engine)

    assert account_deletion.stage_result_artifacts_for_registration(
        user_id=owner_id,
        result={"nested": {"output_path": "processed/owned-image.fits"}},
    ) == 1

    assert account_deletion.register_result_artifacts(
        user_id=owner_id,
        result={"nested": {"output_path": "processed/owned-image.fits"}},
    ) == 1
    # Idempotent retries do not create duplicate ownership rows.
    assert account_deletion.register_result_artifacts(
        user_id=owner_id,
        result={"nested": {"output_path": "processed/owned-image.fits"}},
    ) == 0
    with Session(engine) as session:
        rows = session.scalars(
            select(DataFile).where(DataFile.user_id == owner_id)
        ).all()
        assert [row.fits_path for row in rows] == ["processed/owned-image.fits"]
        assert session.scalar(select(ArtifactCleanupQueue.id)) is None
        user = session.get(User, owner_id)
        user.account_status = "DELETION_PENDING"
        session.commit()

    with pytest.raises(account_deletion.AccountArtifactOwnerInactive):
        account_deletion.register_result_artifacts(
            user_id=owner_id,
            result={"output_path": "processed/too-late.fits"},
        )
    engine.dispose()


def test_tool_output_cannot_claim_another_owners_artifact(tmp_path, monkeypatch):
    from app.services import account_deletion, durable_research_records
    from app.storage import StorageOwnershipError

    database_path = tmp_path / "cross-owner-tool-output.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    artifact_ref = "processed/already-owned.fits"
    with Session(engine) as session:
        session.add_all([
            User(
                id=owner_a,
                username="tool-owner-a",
                email="tool-owner-a@example.invalid",
                password_hash=hash_password("password123"),
                account_status="ACTIVE",
            ),
            User(
                id=owner_b,
                username="tool-owner-b",
                email="tool-owner-b@example.invalid",
                password_hash=hash_password("password123"),
                account_status="ACTIVE",
            ),
            DataFile(
                user_id=owner_a,
                source="tool_output",
                object_id="already-owned.fits",
                fits_path=artifact_ref,
                metadata_={"owner_ledger": "server_tool_result_v1"},
            ),
        ])
        session.commit()
    monkeypatch.setattr(durable_research_records, "_engine", lambda: engine)

    account_deletion.stage_result_artifacts_for_registration(
        user_id=owner_b,
        result={"output_path": artifact_ref},
    )
    with pytest.raises(StorageOwnershipError):
        account_deletion.register_result_artifacts(
            user_id=owner_b,
            result={"output_path": artifact_ref},
        )

    with Session(engine) as session:
        queue = session.scalar(select(ArtifactCleanupQueue))
        assert queue is not None
        assert queue.artifact_ref == artifact_ref
        assert session.scalar(
            select(DataFile.id).where(
                DataFile.user_id == owner_b,
                DataFile.fits_path == artifact_ref,
            )
        ) is None
    engine.dispose()


async def test_claim_audit_worker_refuses_inactive_owner(db_session, test_user):
    from app.services.claim_audit_service import process_claim_audit

    user, _token = test_user
    user.account_status = "DELETION_PENDING"
    audit = _audit(user.id)
    db_session.add(audit)
    await db_session.commit()

    result = await process_claim_audit(db_session, audit)
    assert result.lifecycle_status == "QUEUED"
    assert await db_session.scalar(
        select(EvidencePack.id).where(EvidencePack.audit_id == audit.id)
    ) is None


def test_pipeline_cache_index_supports_complete_owner_purge(monkeypatch):
    from app.pipeline import engine as pipeline_engine

    class FakeRedis:
        def __init__(self):
            self.values: dict[str, str] = {}
            self.sets: dict[str, set[str]] = {}
            self.results: list[int] = []

        def pipeline(self, transaction=True):
            assert transaction is True
            self.results = []
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def setex(self, key, _ttl, value):
            self.values[key] = value
            self.results.append(1)

        def sadd(self, key, value):
            self.sets.setdefault(key, set()).add(value)
            self.results.append(1)

        def expire(self, _key, _ttl):
            self.results.append(1)

        def smembers(self, key):
            return set(self.sets.get(key, set()))

        def scan_iter(self, *, match, count):
            assert count == 500
            prefix = match.removesuffix("*")
            yield from (key for key in self.values if key.startswith(prefix))

        def delete(self, *keys):
            deleted = 0
            for key in keys:
                deleted += int(key in self.values or key in self.sets)
                self.values.pop(key, None)
                self.sets.pop(key, None)
            self.results.append(deleted)
            return deleted

        def execute(self):
            return list(self.results)

    owner_id = uuid.uuid4()
    fake = FakeRedis()
    monkeypatch.setattr(pipeline_engine, "_get_sync_redis", lambda: fake)
    key = pipeline_engine._build_node_cache_key(
        "Test",
        {},
        [],
        {},
        owner_scope=str(owner_id),
        root_input_capability="uploads/input.fits",
    )
    assert key is not None
    pipeline_engine._cache_set_sync(key, {"private": True}, ttl=60)
    assert key in fake.values
    assert any(key in members for members in fake.sets.values())

    # Simulate one entry written by a pre-index release.
    legacy_key = key.rsplit(":", 1)[0] + ":legacy"
    fake.values[legacy_key] = '{"private": true}'

    assert pipeline_engine.delete_owner_pipeline_cache_sync(owner_id) == 2
    assert fake.values == {}
    assert fake.sets == {}


def test_pipeline_worker_drops_late_output_after_owner_deactivation(
    tmp_path,
    monkeypatch,
):
    from app.pipeline import engine as pipeline_engine

    database_path = tmp_path / "pipeline-worker.sqlite"
    url = f"sqlite:///{database_path}"
    seed_engine = create_engine(url)
    Base.metadata.create_all(seed_engine)
    owner_id = uuid.uuid4()
    run_id = uuid.uuid4()
    with Session(seed_engine) as session:
        session.add(
            User(
                id=owner_id,
                username="pipeline-owner",
                email="pipeline-owner@example.invalid",
                password_hash=hash_password("password123"),
                account_status="ACTIVE",
            )
        )
        session.add(
            PipelineRun(
                id=run_id,
                user_id=owner_id,
                dag={},
                status="pending",
            )
        )
        session.commit()

    def revoke_during_node(_input, _params):
        separate_engine = create_engine(url)
        with Session(separate_engine) as session:
            session.execute(
                update(User)
                .where(User.id == owner_id)
                .values(account_status="DELETION_PENDING")
            )
            session.execute(
                update(PipelineRun)
                .where(PipelineRun.id == run_id)
                .values(status="cancelled")
            )
            session.commit()
        separate_engine.dispose()
        return {"success": True, "output_path": "pipeline/late.fits"}

    worker_engine = create_engine(url)
    monkeypatch.setattr(
        pipeline_engine,
        "_get_sync_session",
        lambda: (worker_engine, Session(worker_engine)),
    )
    monkeypatch.setattr(pipeline_engine.registry, "get", lambda *_: revoke_during_node)
    monkeypatch.setattr(pipeline_engine, "_publish_progress", lambda *_a, **_k: None)
    monkeypatch.setattr(
        pipeline_engine,
        "_cache_set_sync",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("inactive owner wrote pipeline cache")
        ),
    )
    deleted: list[str] = []
    monkeypatch.setattr(
        pipeline_engine,
        "_delete_unpersisted_pipeline_output",
        lambda *, owner_id, result: deleted.append(
            f"{owner_id}:{result['output_path']}"
        ),
    )
    result = pipeline_engine.execute_pipeline_task.run(
        str(run_id),
        {"nodes": [{"id": "node", "type": "Test", "data": {"params": {}}}], "edges": []},
        "uploads/input.fits",
    )
    assert result["status"] == "cancelled"
    assert deleted == [f"{owner_id}:pipeline/late.fits"]

    verify_engine = create_engine(url)
    with Session(verify_engine) as session:
        assert session.scalar(select(RunResult.id)) is None
        assert session.get(PipelineRun, run_id).status == "cancelled"
    verify_engine.dispose()


def test_scheduler_dispatches_only_active_owners(tmp_path, monkeypatch):
    from app import scheduler_worker
    from app.pipeline.engine import execute_pipeline_task

    database_path = tmp_path / "scheduler.sqlite"
    url = f"sqlite:///{database_path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    active_id = uuid.uuid4()
    inactive_id = uuid.uuid4()
    with Session(engine) as session:
        session.add_all(
            [
                User(
                    id=active_id,
                    username="active-schedule",
                    email="active-schedule@example.invalid",
                    password_hash="x",
                    account_status="ACTIVE",
                ),
                User(
                    id=inactive_id,
                    username="inactive-schedule",
                    email="inactive-schedule@example.invalid",
                    password_hash="x",
                    account_status="DELETION_PENDING",
                ),
            ]
        )
        for owner_id, key in (
            (active_id, "uploads/active.fits"),
            (inactive_id, "uploads/inactive.fits"),
        ):
            session.add(DataFile(user_id=owner_id, source="upload", object_id=key, fits_path=key))
            session.add(
                ScheduledRun(
                    user_id=owner_id,
                    name=key,
                    dag={"nodes": [], "edges": []},
                    input_data_id=key,
                    cron_expr="0 * * * *",
                    enabled=True,
                    next_run_at=now - timedelta(seconds=1),
                )
            )
        session.commit()

    dispatched: list[str] = []
    monkeypatch.setattr(scheduler_worker, "_create_sync_engine", lambda: engine)
    monkeypatch.setattr(
        execute_pipeline_task,
        "delay",
        lambda run_id, *_args: dispatched.append(run_id),
    )
    scheduler_worker.check_and_dispatch_due_schedules()
    assert len(dispatched) == 1

    verify_engine = create_engine(url)
    with Session(verify_engine) as session:
        runs = session.execute(select(PipelineRun)).scalars().all()
        assert len(runs) == 1
        assert runs[0].user_id == active_id
    verify_engine.dispose()


def test_runtime_active_check_fails_closed_for_malformed_or_unknown_ids():
    # Pinned behavior (2026-07-24, after the daily-CI triage): a user_id that
    # is not a UUID, or has no ACTIVE users row, is treated as
    # deletion-requested and tool execution is refused. This is intentional
    # fail-closed behavior — callers (and tests) must own a real ACTIVE
    # account; do not loosen this to make a harness pass.
    import uuid

    from app.services.account_deletion import account_runtime_is_active

    assert account_runtime_is_active(None) is True  # anonymous work is exempt
    assert account_runtime_is_active("") is True
    assert account_runtime_is_active("test-user") is False
    assert account_runtime_is_active(str(uuid.uuid4())) is False  # no users row
