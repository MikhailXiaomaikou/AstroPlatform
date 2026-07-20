"""Cross-restart persistence and owner isolation for scientific evidence."""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.database import Base


def _fits_table_bytes() -> bytes:
    from astropy.table import Table

    buffer = io.BytesIO()
    Table({"redshift": [0.1, 0.2], "flux": [1.5, 2.5]}).write(
        buffer, format="fits", overwrite=True
    )
    return buffer.getvalue()


async def _upload_fits_direct(data_api, *, db, user, filename: str):
    from fastapi import UploadFile

    payload = _fits_table_bytes()
    upload = UploadFile(
        file=io.BytesIO(payload),
        size=len(payload),
        filename=filename,
    )
    try:
        return await data_api.upload_fits_file.__wrapped__(
            request=None,
            file=upload,
            object_id="",
            db=db,
            user=user,
        )
    finally:
        await upload.close()


async def _upload_general_direct(
    data_api,
    *,
    db,
    user,
    filename: str,
    payload: bytes = b"z,mu\n0.1,38.2\n",
    content_type: str = "text/csv",
):
    from fastapi import UploadFile
    from starlette.datastructures import Headers

    upload = UploadFile(
        file=io.BytesIO(payload),
        size=len(payload),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )
    try:
        return await data_api.upload_general_file.__wrapped__(
            request=None,
            file=upload,
            db=db,
            user=user,
        )
    finally:
        await upload.close()


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


@pytest.mark.asyncio
async def test_fits_upload_commit_ack_loss_reconciles_without_deleting_bytes(
    durable_database,
    monkeypatch,
    tmp_path,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import storage
    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User
    from app.services.artifact_cleanup import purge_artifact_cleanup_queue

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(
        storage.settings, "local_storage_dir", str(tmp_path / "objects")
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            original_commit = db.commit

            async def commit_then_lose_ack():
                await original_commit()
                raise OSError("database commit acknowledgement lost")

            monkeypatch.setattr(db, "commit", commit_then_lose_ack)
            with pytest.raises(HTTPException) as rejected:
                await _upload_fits_direct(
                    data_api,
                    db=db,
                    user=user,
                    filename="commit-ack-lost.fits",
                )
            assert rejected.value.status_code == 500

        with Session(durable._engine()) as db:
            data_file = db.scalar(
                select(DataFile).where(
                    DataFile.user_id == owner_id,
                    DataFile.object_id == "commit-ack-lost.fits",
                )
            )
            assert data_file is not None
            artifact_ref = data_file.fits_path
            queued = db.scalar(
                select(ArtifactCleanupQueue).where(
                    ArtifactCleanupQueue.artifact_ref == artifact_ref
                )
            )
            assert queued is not None
            queued.not_before = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()

        expected = storage.download_fits(artifact_ref)
        async with factory() as db:
            result = await purge_artifact_cleanup_queue(db=db)
        assert result == {
            "objects_deleted": 0,
            "objects_failed": 0,
            "references_reconciled": 1,
        }
        assert storage.download_fits(artifact_ref) == expected
        with Session(durable._engine()) as db:
            assert db.scalar(select(ArtifactCleanupQueue.id)) is None
            assert db.scalar(
                select(DataFile.id).where(DataFile.fits_path == artifact_ref)
            ) is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fits_upload_precommit_failure_keeps_durable_cleanup_discovery(
    durable_database,
    monkeypatch,
    tmp_path,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import storage
    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(
        storage.settings, "local_storage_dir", str(tmp_path / "objects")
    )
    monkeypatch.setattr(
        data_api,
        "delete_fits_all_versions",
        lambda _path: pytest.fail("ambiguous upload must not be deleted immediately"),
    )
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)

            async def fail_commit():
                raise OSError("definite database commit failure")

            monkeypatch.setattr(db, "commit", fail_commit)
            with pytest.raises(HTTPException) as rejected:
                await _upload_fits_direct(
                    data_api,
                    db=db,
                    user=user,
                    filename="precommit-failure.fits",
                )
            assert rejected.value.status_code == 500

        with Session(durable._engine()) as db:
            assert db.scalar(
                select(DataFile.id).where(
                    DataFile.user_id == owner_id,
                    DataFile.object_id == "precommit-failure.fits",
                )
            ) is None
            queued = db.scalar(select(ArtifactCleanupQueue))
            assert queued is not None
            assert queued.reason_class == "uncommitted_data_file_upload"
            deadline = queued.not_before
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            assert deadline > datetime.now(timezone.utc) + timedelta(hours=23)
            artifact_ref = queued.artifact_ref
        assert storage.download_fits(artifact_ref)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fits_upload_inactive_owner_creates_no_storage_or_cleanup_rows(
    durable_database,
    monkeypatch,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))
    with Session(durable._engine()) as db:
        owner = db.get(User, owner_id)
        owner.account_status = "DELETION_PENDING"
        db.commit()

    uploads: list[str] = []
    monkeypatch.setattr(
        data_api,
        "upload_fits",
        lambda path, _payload: uploads.append(path),
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            with pytest.raises(HTTPException) as rejected:
                await _upload_fits_direct(
                    data_api,
                    db=db,
                    user=user,
                    filename="inactive-owner.fits",
                )
            assert rejected.value.status_code == 409

        assert uploads == []
        with Session(durable._engine()) as db:
            assert db.scalar(
                select(DataFile.id).where(DataFile.user_id == owner_id)
            ) is None
            assert db.scalar(select(ArtifactCleanupQueue.id)) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fits_upload_storage_failure_keeps_durable_cleanup_discovery(
    durable_database,
    monkeypatch,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))

    def fail_upload(_path: str, _payload: bytes) -> str:
        raise OSError("object storage upload failed")

    monkeypatch.setattr(data_api, "upload_fits", fail_upload)
    monkeypatch.setattr(
        data_api,
        "delete_fits_all_versions",
        lambda _path: pytest.fail("failed upload must remain cleanup-discoverable"),
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            with pytest.raises(HTTPException) as rejected:
                await _upload_fits_direct(
                    data_api,
                    db=db,
                    user=user,
                    filename="storage-failure.fits",
                )
            assert rejected.value.status_code == 500

        with Session(durable._engine()) as db:
            assert db.scalar(
                select(DataFile.id).where(DataFile.user_id == owner_id)
            ) is None
            queued = db.scalar(select(ArtifactCleanupQueue))
            assert queued is not None
            assert queued.reason_class == "uncommitted_data_file_upload"
            assert queued.artifact_ref.endswith("_storage-failure.fits")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fits_upload_refresh_failure_preserves_committed_object(
    durable_database,
    monkeypatch,
    tmp_path,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import storage
    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(
        storage.settings, "local_storage_dir", str(tmp_path / "objects")
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            original_refresh = db.refresh

            async def fail_data_file_refresh(instance, *args, **kwargs):
                if isinstance(instance, DataFile):
                    raise OSError("database refresh failed")
                return await original_refresh(instance, *args, **kwargs)

            monkeypatch.setattr(db, "refresh", fail_data_file_refresh)
            with pytest.raises(HTTPException) as rejected:
                await _upload_fits_direct(
                    data_api,
                    db=db,
                    user=user,
                    filename="refresh-failure.fits",
                )
            assert rejected.value.status_code == 500

        with Session(durable._engine()) as db:
            data_file = db.scalar(
                select(DataFile).where(
                    DataFile.user_id == owner_id,
                    DataFile.object_id == "refresh-failure.fits",
                )
            )
            assert data_file is not None
            artifact_ref = data_file.fits_path
            assert db.scalar(
                select(ArtifactCleanupQueue.id).where(
                    ArtifactCleanupQueue.artifact_ref == artifact_ref
                )
            ) is None
        assert storage.download_fits(artifact_ref)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_general_upload_commits_owner_ledger_and_storage_receipt(
    durable_database,
    monkeypatch,
    tmp_path,
):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import storage
    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))
    object_root = tmp_path / "objects"
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "local_storage_dir", str(object_root))
    payload = b"z,mu\n0.1,38.2\n"

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            response = await _upload_general_direct(
                data_api,
                db=db,
                user=user,
                filename='union3"draft\'.csv',
                payload=payload,
                content_type="text/csv",
            )

        assert response["filename"] == "union3_draft_.csv"
        assert response["path"].startswith(f"uploads/{str(owner_id)[:8]}/")
        assert response["path"].endswith("_union3_draft_.csv")
        assert response["size_bytes"] == len(payload)
        with Session(durable._engine()) as db:
            data_file = db.get(DataFile, uuid.UUID(response["id"]))
            assert data_file is not None
            assert data_file.user_id == owner_id
            assert data_file.source == "upload"
            assert data_file.object_id == "union3_draft_.csv"
            assert data_file.fits_path == response["path"]
            assert data_file.metadata_ == {
                "original_filename": "union3_draft_.csv",
                "size_bytes": len(payload),
                "content_type": "text/csv",
                "sha256": storage.get_storage_metadata(response["path"])["sha256"],
                "storage_backend": "local",
                "storage_version_id": None,
            }
            assert len(data_file.metadata_["sha256"]) == 64
            assert db.scalar(
                select(ArtifactCleanupQueue.id).where(
                    ArtifactCleanupQueue.artifact_ref == response["path"]
                )
            ) is None
        assert storage.download_fits(response["path"]) == payload
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_general_upload_cleanup_stage_failure_rolls_back_before_upload(
    durable_database,
    monkeypatch,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User
    from app.services import artifact_cleanup

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))
    uploads: list[str] = []
    monkeypatch.setattr(
        artifact_cleanup,
        "stage_artifact_cleanup_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("cleanup ledger unavailable")
        ),
    )
    monkeypatch.setattr(
        data_api,
        "upload_fits",
        lambda path, _payload: uploads.append(path),
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            with pytest.raises(HTTPException) as rejected:
                await _upload_general_direct(
                    data_api,
                    db=db,
                    user=user,
                    filename="stage-failure.csv",
                )
            assert rejected.value.status_code == 503
            assert not db.in_transaction()

        assert uploads == []
        with Session(durable._engine()) as db:
            assert db.scalar(
                select(DataFile.id).where(DataFile.user_id == owner_id)
            ) is None
            assert db.scalar(select(ArtifactCleanupQueue.id)) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_general_upload_commit_ack_loss_reconciles_without_deleting_bytes(
    durable_database,
    monkeypatch,
    tmp_path,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import storage
    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User
    from app.services.artifact_cleanup import purge_artifact_cleanup_queue

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(
        storage.settings, "local_storage_dir", str(tmp_path / "objects")
    )
    payload = b"parameter,value\nomegam,0.356\n"

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            original_commit = db.commit

            async def commit_then_lose_ack():
                await original_commit()
                raise OSError("database commit acknowledgement lost")

            monkeypatch.setattr(db, "commit", commit_then_lose_ack)
            with pytest.raises(HTTPException) as rejected:
                await _upload_general_direct(
                    data_api,
                    db=db,
                    user=user,
                    filename="commit-ack-lost.csv",
                    payload=payload,
                )
            assert rejected.value.status_code == 500

        with Session(durable._engine()) as db:
            data_file = db.scalar(
                select(DataFile).where(
                    DataFile.user_id == owner_id,
                    DataFile.object_id == "commit-ack-lost.csv",
                )
            )
            assert data_file is not None
            artifact_ref = data_file.fits_path
            queued = db.scalar(
                select(ArtifactCleanupQueue).where(
                    ArtifactCleanupQueue.artifact_ref == artifact_ref
                )
            )
            assert queued is not None
            queued.not_before = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()

        assert storage.download_fits(artifact_ref) == payload
        async with factory() as db:
            result = await purge_artifact_cleanup_queue(db=db)
        assert result == {
            "objects_deleted": 0,
            "objects_failed": 0,
            "references_reconciled": 1,
        }
        assert storage.download_fits(artifact_ref) == payload
        with Session(durable._engine()) as db:
            assert db.scalar(select(ArtifactCleanupQueue.id)) is None
            assert db.scalar(
                select(DataFile.id).where(DataFile.fits_path == artifact_ref)
            ) is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["storage", "metadata", "commit"])
async def test_general_upload_failures_remain_durably_cleanup_discoverable(
    durable_database,
    monkeypatch,
    tmp_path,
    failure_point,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import storage
    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User
    from app.services.artifact_cleanup import purge_artifact_cleanup_queue

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(
        storage.settings, "local_storage_dir", str(tmp_path / "objects")
    )
    payload = b"z,mu\n0.1,38.2\n"
    if failure_point == "storage":
        original_upload = data_api.upload_fits

        def upload_then_lose_ack(path, contents):
            original_upload(path, contents)
            raise OSError("object storage acknowledgement lost")

        monkeypatch.setattr(data_api, "upload_fits", upload_then_lose_ack)
    elif failure_point == "metadata":
        monkeypatch.setattr(
            storage,
            "get_storage_metadata",
            lambda _path: (_ for _ in ()).throw(
                OSError("object metadata lookup failed")
            ),
        )
    monkeypatch.setattr(
        data_api,
        "delete_fits_all_versions",
        lambda _path: pytest.fail("ambiguous upload must not be deleted immediately"),
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            if failure_point == "commit":

                async def fail_commit():
                    raise OSError("database commit failed")

                monkeypatch.setattr(db, "commit", fail_commit)
            with pytest.raises(HTTPException) as rejected:
                await _upload_general_direct(
                    data_api,
                    db=db,
                    user=user,
                    filename=f"{failure_point}-failure.csv",
                    payload=payload,
                )
            assert rejected.value.status_code == 500

        with Session(durable._engine()) as db:
            assert db.scalar(
                select(DataFile.id).where(DataFile.user_id == owner_id)
            ) is None
            queued = db.scalar(select(ArtifactCleanupQueue))
            assert queued is not None
            assert queued.reason_class == "uncommitted_data_file_upload"
            assert queued.artifact_ref.endswith(f"_{failure_point}-failure.csv")
            deadline = queued.not_before
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            assert deadline > datetime.now(timezone.utc) + timedelta(hours=23)
            artifact_ref = queued.artifact_ref
            queued.not_before = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()

        # Every failure point occurs after object bytes may already be durable.
        # The request must leave those bytes alone until the cleanup worker can
        # prove that no trusted database record references them.
        assert storage.download_fits(artifact_ref) == payload
        async with factory() as db:
            result = await purge_artifact_cleanup_queue(db=db)
        assert result == {
            "objects_deleted": 1,
            "objects_failed": 0,
            "references_reconciled": 0,
        }
        with pytest.raises(FileNotFoundError):
            storage.download_fits(artifact_ref)
        with Session(durable._engine()) as db:
            assert db.scalar(select(ArtifactCleanupQueue.id)) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_general_upload_inactive_owner_creates_no_durable_write(
    durable_database,
    monkeypatch,
    tmp_path,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import storage
    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))
    with Session(durable._engine()) as db:
        owner = db.get(User, owner_id)
        owner.account_status = "DELETION_PENDING"
        db.commit()
    object_root = tmp_path / "objects"
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "local_storage_dir", str(object_root))
    uploads: list[str] = []
    monkeypatch.setattr(
        data_api,
        "upload_fits",
        lambda path, _payload: uploads.append(path),
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            with pytest.raises(HTTPException) as rejected:
                await _upload_general_direct(
                    data_api,
                    db=db,
                    user=user,
                    filename="late-after-deletion.csv",
                )
            assert rejected.value.status_code == 409

        assert uploads == []
        assert not object_root.exists()
        with Session(durable._engine()) as db:
            assert db.scalar(
                select(DataFile.id).where(DataFile.user_id == owner_id)
            ) is None
            assert db.scalar(select(ArtifactCleanupQueue.id)) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_general_upload_refresh_failure_preserves_committed_object(
    durable_database,
    monkeypatch,
    tmp_path,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import storage
    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(
        storage.settings, "local_storage_dir", str(tmp_path / "objects")
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)

            async def fail_refresh(_instance, *_args, **_kwargs):
                raise OSError("database refresh failed")

            monkeypatch.setattr(db, "refresh", fail_refresh)
            with pytest.raises(HTTPException) as rejected:
                await _upload_general_direct(
                    data_api,
                    db=db,
                    user=user,
                    filename="refresh-failure.csv",
                )
            assert rejected.value.status_code == 500

        with Session(durable._engine()) as db:
            data_file = db.scalar(
                select(DataFile).where(
                    DataFile.user_id == owner_id,
                    DataFile.object_id == "refresh-failure.csv",
                )
            )
            assert data_file is not None
            artifact_ref = data_file.fits_path
            assert db.scalar(
                select(ArtifactCleanupQueue.id).where(
                    ArtifactCleanupQueue.artifact_ref == artifact_ref
                )
            ) is None
        assert storage.download_fits(artifact_ref)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_archive_fetch_commit_ack_loss_reconciles_cleanup_queue(
    durable_database,
    monkeypatch,
    tmp_path,
):
    from types import SimpleNamespace

    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import storage
    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User
    from app.services.artifact_cleanup import purge_artifact_cleanup_queue

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(
        storage.settings, "local_storage_dir", str(tmp_path / "objects")
    )
    payload = b"real archive product bytes"

    class ArchiveConnector:
        async def fetch(self, _object_id):
            return SimpleNamespace(filename="archive-product.fits", data=payload)

    monkeypatch.setattr(data_api, "get_connector", lambda _source: ArchiveConnector())

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            original_commit = db.commit

            async def commit_then_lose_ack():
                await original_commit()
                raise OSError("database commit acknowledgement lost")

            monkeypatch.setattr(db, "commit", commit_then_lose_ack)
            with pytest.raises(HTTPException) as rejected:
                await data_api.fetch_object(
                    "test_archive",
                    "object-42",
                    db=db,
                    user=user,
                )
            assert rejected.value.status_code == 500

        with Session(durable._engine()) as db:
            data_file = db.scalar(
                select(DataFile).where(
                    DataFile.user_id == owner_id,
                    DataFile.source == "test_archive",
                    DataFile.object_id == "object-42",
                )
            )
            assert data_file is not None
            assert data_file.metadata_["original_filename"] == "archive-product.fits"
            assert data_file.metadata_["size_bytes"] == len(payload)
            assert len(data_file.metadata_["sha256"]) == 64
            artifact_ref = data_file.fits_path
            queued = db.scalar(
                select(ArtifactCleanupQueue).where(
                    ArtifactCleanupQueue.artifact_ref == artifact_ref
                )
            )
            assert queued is not None
            queued.not_before = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()

        async with factory() as db:
            result = await purge_artifact_cleanup_queue(db=db)
        assert result == {
            "objects_deleted": 0,
            "objects_failed": 0,
            "references_reconciled": 1,
        }
        assert storage.download_fits(artifact_ref) == payload
        with Session(durable._engine()) as db:
            assert db.scalar(select(ArtifactCleanupQueue.id)) is None
            assert db.scalar(
                select(DataFile.id).where(DataFile.fits_path == artifact_ref)
            ) is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_archive_fetch_cleanup_stage_failure_skips_object_upload(
    durable_database,
    monkeypatch,
):
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.api import data as data_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User
    from app.services import artifact_cleanup

    durable = durable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, str(owner_id))

    class ArchiveConnector:
        async def fetch(self, _object_id):
            return SimpleNamespace(
                filename="archive-product.fits",
                data=b"fetched but not stored",
            )

    monkeypatch.setattr(data_api, "get_connector", lambda _source: ArchiveConnector())
    monkeypatch.setattr(
        artifact_cleanup,
        "stage_artifact_cleanup_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("cleanup ledger unavailable")
        ),
    )
    uploads: list[str] = []
    monkeypatch.setattr(
        data_api,
        "upload_fits",
        lambda path, _payload: uploads.append(path),
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            response = await data_api.fetch_object(
                "test_archive",
                "object-42",
                db=db,
                user=user,
            )
            assert response.fits_path == ""
            assert response.file_id is None
            assert not db.in_transaction()

        assert uploads == []
        with Session(durable._engine()) as db:
            assert db.scalar(
                select(DataFile.id).where(DataFile.user_id == owner_id)
            ) is None
            assert db.scalar(select(ArtifactCleanupQueue.id)) is None
    finally:
        await engine.dispose()


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
