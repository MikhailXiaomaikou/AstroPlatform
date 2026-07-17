"""Cross-restart persistence and owner isolation for scientific evidence."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.database import Base


def _create_active_user(durable, owner: str) -> None:
    from app.models.schemas import User

    owner_id = uuid.UUID(owner)
    with Session(durable._engine()) as db:
        db.add(User(
            id=owner_id,
            username=f"durable-{owner_id.hex}",
            email=f"durable-{owner_id.hex}@example.test",
            password_hash="test-only",
            account_status="ACTIVE",
        ))
        db.commit()


@pytest.fixture
def durable_database(monkeypatch, tmp_path):
    from app.config import settings
    from app.services import durable_research_records as durable

    url = f"sqlite+aiosqlite:///{tmp_path / 'durable.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    durable.reset_engine()
    engine = create_engine(url.replace("+aiosqlite", ""))
    Base.metadata.create_all(engine)
    engine.dispose()
    yield durable
    durable.reset_engine()


def test_provenance_survives_hot_cache_clear_and_is_owner_scoped(durable_database):
    from app.services import provenance

    owner = str(uuid.uuid4())
    other = str(uuid.uuid4())
    provenance._provenance_records.clear()
    record_id = provenance.record_activity(
        entity_type="chat_tool_result",
        entity_id="run-42",
        activity="fit_cosmology_mcmc",
        params={"random_seed": 7},
        parent_ids=["dataset-1"],
        user_id=owner,
        artifact_sha256="a" * 64,
    )
    assert record_id

    provenance._provenance_records.clear()  # simulate process restart
    lineage = provenance.get_lineage("run-42", owner_id=owner)
    assert [node["id"] for node in lineage["nodes"]] == ["run-42"]
    assert lineage["edges"] == [{"from": "dataset-1", "to": "run-42"}]
    assert provenance.get_lineage("run-42", owner_id=other)["nodes"] == []


def test_async_job_survives_kv_restart_and_rejects_other_owner(durable_database):
    from app.services import _kv_store, async_tool_runtime as runtime

    owner = str(uuid.uuid4())
    other = str(uuid.uuid4())
    _create_active_user(durable_database, owner)
    _kv_store.use_memory_backend_for_testing()
    runtime.set_dispatcher(lambda *_args, **_kwargs: None)
    runtime.reset_persister()
    try:
        banner = runtime.submit_async_job(
            "fit_cosmology_mcmc",
            {"n_walkers": 32, "n_steps": 800},
            user_id=owner,
            session_id=None,
        )
        runtime.write_result(banner["job_id"], {"success": True, "value": 1.0})

        _kv_store.use_memory_backend_for_testing()  # empty replacement backend
        restored = runtime.get_async_job(banner["job_id"], owner_id=owner)
        assert restored is not None
        assert restored["status"] == "completed"
        assert restored["result"] == {"success": True, "value": 1.0}
        assert runtime.get_async_job(banner["job_id"], owner_id=other) is None
    finally:
        runtime.reset_dispatcher()
        runtime.set_persister(lambda _job: None)


def test_large_job_arguments_are_hashed_not_replayed(durable_database, monkeypatch):
    from app.services import durable_research_records as durable

    monkeypatch.setattr(durable, "MAX_REPLAY_ARGS_BYTES", 64)
    owner = str(uuid.uuid4())
    _create_active_user(durable, owner)
    durable.save_job({
        "job_id": "large-1",
        "user_id": owner,
        "tool_name": "transit_search_bls",
        "inputs_hash": "abc",
        "args": {"flux": list(range(100))},
        "status": "failed",
        "created_at": 1_700_000_000,
    })

    restored = durable.load_job("large-1", owner_id=owner)
    assert restored is not None
    assert restored["args_replayable"] is False
    assert restored["args"]["_omitted"].startswith("arguments exceed")
    assert len(restored["args"]["sha256"]) == 64


def test_large_job_result_is_integrity_checked_object_artifact(
    durable_database, monkeypatch, tmp_path
):
    from app import storage
    from app.services import durable_research_records as durable

    monkeypatch.setattr(durable, "MAX_INLINE_RESULT_BYTES", 64)
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "local_storage_dir", str(tmp_path / "objects"))
    owner = str(uuid.uuid4())
    _create_active_user(durable, owner)
    expected = {"chain": list(range(100))}
    durable.save_job({
        "job_id": "large-result-1",
        "user_id": owner,
        "tool_name": "fit_cosmology_mcmc",
        "inputs_hash": "abc",
        "args": {},
        "status": "completed",
        "result": expected,
        "created_at": 1_700_000_000,
        "completed_at": 1_700_000_001,
    })

    raw = durable.load_job("large-result-1", owner_id=owner, hydrate=False)
    assert raw is not None
    assert raw["result"]["_artifact_ref"].endswith("/result.json.gz")
    assert len(raw["result"]["sha256"]) == 64
    restored = durable.load_job("large-result-1", owner_id=owner)
    assert restored is not None
    assert restored["result"] == expected
    from app.models.claim_audit_records import ArtifactCleanupQueue

    with Session(durable._engine()) as db:
        assert db.scalar(select(ArtifactCleanupQueue.id)) is None


def test_failed_large_result_commit_keeps_durable_cleanup_discovery(
    durable_database,
    monkeypatch,
    tmp_path,
):
    from app import storage
    from app.models.claim_audit_records import ArtifactCleanupQueue

    durable = durable_database
    owner = str(uuid.uuid4())
    _create_active_user(durable, owner)
    object_root = tmp_path / "objects"
    monkeypatch.setattr(durable, "MAX_INLINE_RESULT_BYTES", 32)
    monkeypatch.setattr(durable, "JOB_PERSIST_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "local_storage_dir", str(object_root))
    monkeypatch.setattr(
        durable,
        "_save_job_once",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("database commit failed")),
    )
    monkeypatch.setattr(
        storage,
        "delete_fits_all_versions",
        lambda _path: (_ for _ in ()).throw(OSError("storage delete failed")),
    )

    with pytest.raises(durable.ResearchJobPersistenceError):
        durable.save_job({
            "job_id": "uncommitted-large-result",
            "user_id": owner,
            "tool_name": "fit_cosmology_mcmc",
            "inputs_hash": "abc",
            "args": {},
            "status": "completed",
            "result": {"chain": list(range(100))},
            "created_at": 1_700_000_000,
            "completed_at": 1_700_000_001,
        })

    with Session(durable._engine()) as db:
        queued = db.scalar(select(ArtifactCleanupQueue))
        assert queued is not None
        assert queued.user_fingerprint
        assert queued.artifact_ref.endswith("/result.json.gz")
        deadline = queued.not_before
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        assert deadline > datetime.now(timezone.utc) + timedelta(hours=23)
    assert any(path.is_file() for path in object_root.rglob("result.json.gz"))


async def test_cleanup_queue_reconciles_committed_reference_without_deleting_bytes(
    durable_database,
    monkeypatch,
    tmp_path,
):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import storage
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.services.artifact_cleanup import purge_artifact_cleanup_queue

    durable = durable_database
    owner = str(uuid.uuid4())
    _create_active_user(durable, owner)
    monkeypatch.setattr(durable, "MAX_INLINE_RESULT_BYTES", 32)
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "local_storage_dir", str(tmp_path / "objects"))
    durable.save_job({
        "job_id": "commit-ack-uncertain",
        "user_id": owner,
        "tool_name": "fit_cosmology_mcmc",
        "inputs_hash": "abc",
        "args": {},
        "status": "completed",
        "result": {"chain": list(range(100))},
        "created_at": 1_700_000_000,
        "completed_at": 1_700_000_001,
    })
    row = durable.load_job("commit-ack-uncertain", owner_id=owner, hydrate=False)
    artifact_ref = row["result"]["_artifact_ref"]
    with Session(durable._engine()) as db:
        db.add(ArtifactCleanupQueue(
            user_fingerprint="f" * 64,
            artifact_ref=artifact_ref,
            reason_class="commit_ack_uncertain",
            not_before=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        db.commit()

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            result = await purge_artifact_cleanup_queue(db=db)
    finally:
        await engine.dispose()

    assert result == {
        "objects_deleted": 0,
        "objects_failed": 0,
        "references_reconciled": 1,
    }
    assert storage.download_fits(artifact_ref)
    with Session(durable._engine()) as db:
        assert db.scalar(select(ArtifactCleanupQueue.id)) is None


def test_tombstoned_owner_is_rejected_before_large_result_upload(
    durable_database, monkeypatch
):
    from app import storage
    from app.services import account_deletion

    durable = durable_database
    monkeypatch.setattr(durable, "MAX_INLINE_RESULT_BYTES", 32)
    monkeypatch.setattr(
        account_deletion,
        "external_deletion_tombstone_exists",
        lambda _owner_id: True,
    )
    uploads: list[str] = []
    monkeypatch.setattr(
        storage,
        "upload_fits",
        lambda key, _payload: uploads.append(str(key)),
    )

    with pytest.raises(
        durable.ResearchJobPersistenceError,
        match="after account deletion",
    ):
        durable.save_job({
            "job_id": "late-large-result",
            "user_id": str(uuid.uuid4()),
            "tool_name": "fit_cosmology_mcmc",
            "inputs_hash": "abc",
            "args": {},
            "status": "completed",
            "result": {"chain": list(range(100))},
            "created_at": 1_700_000_000,
            "completed_at": 1_700_000_001,
        })

    assert uploads == []


def test_inactive_owner_cannot_recreate_job_or_leave_large_artifact(
    durable_database, monkeypatch, tmp_path
):
    from app import storage
    from app.models.schemas import User
    from app.services import account_deletion

    durable = durable_database
    owner = str(uuid.uuid4())
    _create_active_user(durable, owner)
    with Session(durable._engine()) as db:
        user = db.get(User, uuid.UUID(owner))
        user.account_status = "DELETION_PENDING"
        db.commit()

    object_root = tmp_path / "objects"
    monkeypatch.setattr(durable, "MAX_INLINE_RESULT_BYTES", 32)
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "local_storage_dir", str(object_root))
    # Model the cross-process false-negative cache window: the database lock
    # and ACTIVE check must still close the late-write race.
    monkeypatch.setattr(
        account_deletion,
        "external_deletion_tombstone_exists",
        lambda _owner_id: False,
    )

    with pytest.raises(
        durable.ResearchJobOwnerInactive,
        match="inactive account",
    ):
        durable.save_job({
            "job_id": "late-after-deletion",
            "user_id": owner,
            "tool_name": "fit_cosmology_mcmc",
            "inputs_hash": "abc",
            "args": {},
            "status": "completed",
            "result": {"chain": list(range(100))},
            "created_at": 1_700_000_000,
            "completed_at": 1_700_000_001,
        })

    assert durable.load_job("late-after-deletion", owner_id=owner) is None
    assert not any(path.is_file() for path in object_root.rglob("*"))


def test_signed_job_stub_rejects_body_and_sidecar_replacement(
    durable_database, monkeypatch, tmp_path
):
    from app import storage
    from app.services import durable_research_records as durable

    monkeypatch.setattr(durable, "MAX_INLINE_RESULT_BYTES", 32)
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    object_root = tmp_path / "objects"
    monkeypatch.setattr(storage.settings, "local_storage_dir", str(object_root))
    monkeypatch.setattr(storage.settings, "storage_require_integrity", True)
    owner = str(uuid.uuid4())
    _create_active_user(durable, owner)
    durable.save_job({
        "job_id": "tampered-large-result",
        "user_id": owner,
        "tool_name": "fit_cosmology_mcmc",
        "inputs_hash": "abc",
        "args": {},
        "status": "completed",
        "result": {"chain": list(range(100))},
        "created_at": 1_700_000_000,
        "completed_at": 1_700_000_001,
    })
    raw = durable.load_job(
        "tampered-large-result", owner_id=owner, hydrate=False
    )
    artifact_key = raw["result"]["_artifact_ref"]
    artifact_path = object_root / artifact_key
    changed = b"replacement scientific bytes"
    artifact_path.write_bytes(changed)
    storage._sidecar_path(artifact_path).write_text(  # noqa: SLF001
        storage._digest(changed) + "\n",  # noqa: SLF001
        encoding="ascii",
    )

    with pytest.raises(
        storage.StorageIntegrityError,
        match="signed completion record",
    ):
        durable.hydrate_result(raw["result"])


def test_job_persistence_retries_before_success(durable_database, monkeypatch):
    durable = durable_database
    attempts: list[str] = []

    def flaky(job, **_kwargs):
        attempts.append(str(job["job_id"]))
        if len(attempts) < 3:
            raise OSError("temporary database outage")

    monkeypatch.setattr(durable, "_save_job_once", flaky)
    monkeypatch.setattr(durable, "JOB_PERSIST_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(durable, "JOB_PERSIST_RETRY_BASE_SECONDS", 0)

    durable.save_job({
        "job_id": "retry-write-1",
        "tool_name": "fit_cosmology_mcmc",
        "inputs_hash": "abc",
        "args": {},
        "status": "queued",
        "created_at": 1_700_000_000,
    })

    assert attempts == ["retry-write-1"] * 3


def test_job_persistence_raises_after_bounded_retries(
    durable_database, monkeypatch
):
    durable = durable_database
    attempts = 0

    def always_fail(_job, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise OSError("database remains unavailable")

    monkeypatch.setattr(durable, "_save_job_once", always_fail)
    monkeypatch.setattr(durable, "JOB_PERSIST_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(durable, "JOB_PERSIST_RETRY_BASE_SECONDS", 0)

    with pytest.raises(durable.ResearchJobPersistenceError):
        durable.save_job({
            "job_id": "retry-write-2",
            "tool_name": "fit_cosmology_mcmc",
            "inputs_hash": "abc",
            "args": {},
            "status": "queued",
            "created_at": 1_700_000_000,
        })

    assert attempts == 2


def test_stale_job_reconciliation_is_durable_and_updates_hot_state(
    durable_database,
):
    from app.models.research_records import ResearchJob
    from app.services import _kv_store, async_tool_runtime as runtime

    durable = durable_database
    now = datetime.now(timezone.utc)
    durable.save_job({
        "job_id": "orphaned-running-1",
        "tool_name": "fit_cosmology_mcmc",
        "inputs_hash": "abc",
        "args": {},
        "status": "running",
        "created_at": now - timedelta(hours=3),
        "started_at": now - timedelta(hours=3),
    })
    with Session(durable._engine()) as db:
        row = db.get(ResearchJob, "orphaned-running-1")
        row.updated_at = now - timedelta(hours=2)
        db.commit()

    _kv_store.use_memory_backend_for_testing()
    runtime._JOBS_STORE.set(
        "orphaned-running-1",
        {"job_id": "orphaned-running-1", "status": "running", "ttl": 3600},
        ttl=3600,
    )

    assert durable.reconcile_stale_jobs(
        stale_after_seconds=3600,
        now=now,
    ) == 1
    restored = durable.load_job("orphaned-running-1")
    assert restored["status"] == "failed"
    assert restored["error_class"] == "stale_job_reconciled"
    hot = runtime._JOBS_STORE.get("orphaned-running-1")
    assert hot["status"] == "failed"
    assert hot["durability_status"] == "durable"
