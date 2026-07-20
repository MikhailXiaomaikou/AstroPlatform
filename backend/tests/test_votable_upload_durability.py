"""Durability boundaries for VOTable-to-FITS user uploads."""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.database import Base


def _votable_bytes() -> bytes:
    from astropy.table import Table

    buffer = io.BytesIO()
    Table({"redshift": [0.1, 0.2], "flux": [1.5, 2.5]}).write(
        buffer,
        format="votable",
        overwrite=True,
    )
    return buffer.getvalue()


async def _upload_votable_direct(integration_api, *, db, user, filename: str):
    from fastapi import UploadFile

    payload = _votable_bytes()
    upload = UploadFile(
        file=io.BytesIO(payload),
        size=len(payload),
        filename=filename,
        headers={"content-type": "application/x-votable+xml"},
    )
    try:
        return await integration_api.upload_votable(
            file=upload,
            db=db,
            user=user,
        )
    finally:
        await upload.close()


def _create_active_user(durable, owner_id: uuid.UUID) -> None:
    from app.models.schemas import User

    with Session(durable._engine()) as db:
        db.add(
            User(
                id=owner_id,
                username=f"votable-{owner_id.hex}",
                email=f"votable-{owner_id.hex}@example.test",
                password_hash="test-only",
                account_status="ACTIVE",
            )
        )
        db.commit()


@pytest.fixture
def votable_database(monkeypatch, tmp_path):
    from app.config import settings
    from app.services import durable_research_records as durable

    url = f"sqlite+aiosqlite:///{tmp_path / 'votable.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    durable.reset_engine()
    engine = create_engine(url.replace("+aiosqlite", ""))
    Base.metadata.create_all(engine)
    engine.dispose()
    yield durable
    durable.reset_engine()


def _use_local_storage(monkeypatch, tmp_path) -> None:
    from app import storage

    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(
        storage.settings,
        "local_storage_dir",
        str(tmp_path / "objects"),
    )


@pytest.mark.asyncio
async def test_votable_upload_commit_ack_loss_reconciles_durable_reference(
    votable_database,
    monkeypatch,
    tmp_path,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app import storage
    from app.api import integration as integration_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User
    from app.services.artifact_cleanup import purge_artifact_cleanup_queue

    durable = votable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, owner_id)
    _use_local_storage(monkeypatch, tmp_path)

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
                await _upload_votable_direct(
                    integration_api,
                    db=db,
                    user=user,
                    filename="ack-lost.xml",
                )
            assert rejected.value.status_code == 500

        with Session(durable._engine()) as db:
            data_file = db.scalar(
                select(DataFile).where(
                    DataFile.user_id == owner_id,
                    DataFile.object_id == "ack-lost.xml",
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
            assert queued.reason_class == "uncommitted_votable_upload"
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
async def test_votable_upload_storage_failure_keeps_cleanup_discovery(
    votable_database,
    monkeypatch,
    tmp_path,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app import storage
    from app.api import integration as integration_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User
    from app.services.artifact_cleanup import purge_artifact_cleanup_queue

    durable = votable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, owner_id)
    _use_local_storage(monkeypatch, tmp_path)
    original_upload = integration_api.upload_fits

    def upload_then_lose_ack(path: str, payload: bytes) -> str:
        original_upload(path, payload)
        raise OSError("object storage acknowledgement lost")

    monkeypatch.setattr(integration_api, "upload_fits", upload_then_lose_ack)

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            with pytest.raises(HTTPException) as rejected:
                await _upload_votable_direct(
                    integration_api,
                    db=db,
                    user=user,
                    filename="storage-failure.xml",
                )
            assert rejected.value.status_code == 500

        with Session(durable._engine()) as db:
            assert db.scalar(
                select(DataFile.id).where(DataFile.user_id == owner_id)
            ) is None
            queued = db.scalar(select(ArtifactCleanupQueue))
            assert queued is not None
            assert queued.reason_class == "uncommitted_votable_upload"
            assert queued.artifact_ref.startswith(
                f"votable_imports/{str(owner_id)[:8]}/"
            )
            artifact_ref = queued.artifact_ref
            queued.not_before = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()

        assert storage.download_fits(artifact_ref)
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
async def test_votable_upload_cleanup_stage_failure_rolls_back_before_upload(
    votable_database,
    monkeypatch,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.api import integration as integration_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User
    from app.services import artifact_cleanup

    durable = votable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, owner_id)
    uploads: list[str] = []
    monkeypatch.setattr(
        artifact_cleanup,
        "stage_artifact_cleanup_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("cleanup ledger unavailable")
        ),
    )
    monkeypatch.setattr(
        integration_api,
        "upload_fits",
        lambda path, _payload: uploads.append(path),
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            with pytest.raises(HTTPException) as rejected:
                await _upload_votable_direct(
                    integration_api,
                    db=db,
                    user=user,
                    filename="stage-failure.xml",
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
async def test_votable_upload_refresh_failure_preserves_committed_object(
    votable_database,
    monkeypatch,
    tmp_path,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app import storage
    from app.api import integration as integration_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User

    durable = votable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, owner_id)
    _use_local_storage(monkeypatch, tmp_path)

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
                await _upload_votable_direct(
                    integration_api,
                    db=db,
                    user=user,
                    filename="refresh-failure.xml",
                )
            assert rejected.value.status_code == 500

        with Session(durable._engine()) as db:
            data_file = db.scalar(
                select(DataFile).where(
                    DataFile.user_id == owner_id,
                    DataFile.object_id == "refresh-failure.xml",
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
async def test_votable_upload_inactive_owner_writes_nothing(
    votable_database,
    monkeypatch,
):
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.api import integration as integration_api
    from app.config import settings
    from app.models.claim_audit_records import ArtifactCleanupQueue
    from app.models.schemas import DataFile, User

    durable = votable_database
    owner_id = uuid.uuid4()
    _create_active_user(durable, owner_id)
    with Session(durable._engine()) as db:
        owner = db.get(User, owner_id)
        owner.account_status = "DELETION_PENDING"
        db.commit()

    uploads: list[str] = []
    monkeypatch.setattr(
        integration_api,
        "upload_fits",
        lambda path, _payload: uploads.append(path),
    )

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            user = await db.get(User, owner_id)
            with pytest.raises(HTTPException) as rejected:
                await _upload_votable_direct(
                    integration_api,
                    db=db,
                    user=user,
                    filename="inactive-owner.xml",
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
