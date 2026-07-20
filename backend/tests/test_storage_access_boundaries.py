"""Owner binding and canonical storage-path security regression tests."""

from __future__ import annotations

import io
import uuid

import pytest
from sqlalchemy import select

from app.models.schemas import DataFile, User


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _use_local_storage(monkeypatch, tmp_path):
    from app import storage

    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "local_storage_dir", str(tmp_path))
    monkeypatch.setattr(storage.settings, "storage_require_integrity", False)
    storage.reset_storage_clients()
    return storage


def _fits_table_bytes() -> bytes:
    from astropy.table import Table

    buffer = io.BytesIO()
    Table({"redshift": [0.1, 0.2], "flux": [1.5, 2.5]}).write(
        buffer, format="fits", overwrite=True
    )
    return buffer.getvalue()


def _votable_bytes() -> bytes:
    from astropy.table import Table

    buffer = io.BytesIO()
    Table({"redshift": [0.1, 0.2], "flux": [1.5, 2.5]}).write(
        buffer, format="votable", overwrite=True
    )
    return buffer.getvalue()


def test_storage_normalizer_allows_double_dot_filename_but_rejects_segments():
    from app.storage import normalize_storage_key

    legal = "uploads/u1/spectrum.v1..final.fits"
    assert normalize_storage_key(legal) == legal
    assert normalize_storage_key("uploads//u1/data.fits") == "uploads/u1/data.fits"

    for hostile in (
        "../secret.fits",
        "uploads/../secret.fits",
        "uploads/./secret.fits",
        "/etc/passwd",
        "uploads\\..\\secret.fits",
        "uploads/u1/bad\x00name.fits",
    ):
        with pytest.raises(ValueError):
            normalize_storage_key(hostile)


@pytest.mark.asyncio
async def test_data_download_uses_canonical_key_and_hides_other_owner(
    app_client, db_session, test_user, monkeypatch, tmp_path
):
    storage = _use_local_storage(monkeypatch, tmp_path)
    user, token = test_user
    other = User(
        id=uuid.uuid4(),
        username=f"other-{uuid.uuid4().hex[:8]}",
        email=f"other-{uuid.uuid4().hex[:8]}@example.test",
        password_hash="not-used",
        subscription_tier="solo",
    )
    db_session.add(other)

    own_key = "uploads/u1/observations.v1..final.csv"
    other_key = "uploads/u2/private.csv"
    storage.upload_fits(own_key, b"z,mu\n0.1,38.2\n")
    storage.upload_fits(other_key, b"private")
    db_session.add_all(
        [
            DataFile(
                user_id=user.id,
                source="upload",
                object_id="observations.v1..final.csv",
                fits_path=own_key,
                metadata_={},
            ),
            DataFile(
                user_id=other.id,
                source="upload",
                object_id="private.csv",
                fits_path=other_key,
                metadata_={},
            ),
        ]
    )
    await db_session.commit()

    legal = await app_client.get(
        "/api/data/files/download",
        params={"path": "uploads//u1/observations.v1..final.csv"},
        headers=_auth(token),
    )
    assert legal.status_code == 200, legal.text
    assert legal.content == b"z,mu\n0.1,38.2\n"

    traversal = await app_client.get(
        "/api/data/files/download",
        params={"path": "uploads/u1/../u2/private.csv"},
        headers=_auth(token),
    )
    assert traversal.status_code == 400

    idor = await app_client.get(
        "/api/data/files/download",
        params={"path": other_key},
        headers=_auth(token),
    )
    assert idor.status_code == 404


@pytest.mark.asyncio
async def test_votable_convert_requires_owner_and_upload_creates_owner_record(
    app_client, db_session, test_user, monkeypatch, tmp_path
):
    from app.services import artifact_cleanup

    # The shared API fixture uses an in-memory dependency-overridden database,
    # while durable cleanup receipts intentionally use an independent engine.
    # Dedicated durability tests exercise the real receipt lifecycle.
    monkeypatch.setattr(
        artifact_cleanup,
        "stage_artifact_cleanup_sync",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        artifact_cleanup,
        "renew_artifact_cleanup_grace_sync",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        artifact_cleanup,
        "clear_artifact_cleanup_sync",
        lambda *_args, **_kwargs: None,
    )
    storage = _use_local_storage(monkeypatch, tmp_path)
    user, token = test_user
    other = User(
        id=uuid.uuid4(),
        username=f"vot-other-{uuid.uuid4().hex[:8]}",
        email=f"vot-other-{uuid.uuid4().hex[:8]}@example.test",
        password_hash="not-used",
        subscription_tier="solo",
    )
    db_session.add(other)

    own_key = "uploads/u1/my..table.fits"
    other_key = "uploads/u2/private-table.fits"
    payload = _fits_table_bytes()
    storage.upload_fits(own_key, payload)
    storage.upload_fits(other_key, payload)
    db_session.add_all(
        [
            DataFile(
                user_id=user.id,
                source="upload",
                object_id="my..table.fits",
                fits_path=own_key,
                metadata_={},
            ),
            DataFile(
                user_id=other.id,
                source="upload",
                object_id="private-table.fits",
                fits_path=other_key,
                metadata_={},
            ),
        ]
    )
    await db_session.commit()

    denied = await app_client.get(
        "/api/integration/votable/convert",
        params={"fits_path": other_key},
        headers=_auth(token),
    )
    assert denied.status_code == 404

    samp_idor = await app_client.post(
        "/api/integration/samp/send",
        json={"fits_path": other_key, "message_type": "table.load.fits"},
        headers=_auth(token),
    )
    assert samp_idor.status_code == 404
    samp_traversal = await app_client.post(
        "/api/integration/samp/send",
        json={"fits_path": "uploads/u1/../u2/private-table.fits"},
        headers=_auth(token),
    )
    assert samp_traversal.status_code == 400

    converted = await app_client.get(
        "/api/integration/votable/convert",
        params={"fits_path": own_key},
        headers=_auth(token),
    )
    assert converted.status_code == 200, converted.text
    assert b"VOTABLE" in converted.content

    uploaded = await app_client.post(
        "/api/integration/votable/upload",
        files={"file": ("measurements..v1.xml", _votable_bytes(), "application/x-votable+xml")},
        headers=_auth(token),
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    row = (
        await db_session.execute(
            select(DataFile).where(DataFile.fits_path == body["path"])
        )
    ).scalar_one()
    assert row.user_id == user.id
    assert row.source == "votable_upload"
    assert row.metadata_["sha256"]
    assert storage.download_fits(body["path"])


@pytest.mark.asyncio
async def test_requirements_are_owner_and_entity_snapshot_bound(
    app_client, test_user, monkeypatch
):
    from app.services import durable_research_records, provenance

    user, token = test_user
    other_id = str(uuid.uuid4())
    monkeypatch.setattr(durable_research_records, "load_provenance", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        provenance,
        "capture_environment",
        lambda: {
            "python_version": "9.9.9",
            "packages": {"live-process-only": "999"},
            "timestamp": "never",
        },
    )
    saved = list(provenance._provenance_records)
    provenance._provenance_records[:] = [
        {
            "id": "owned-record",
            "entity_id": "owned-run",
            "entity_type": "chat_tool_result",
            "activity": "run_adql",
            "params": {},
            "parent_ids": [],
            "agent": "test",
            "user_id": str(user.id),
            "environment": {
                "versions": {
                    "python": "3.11.12",
                    "platform": "test",
                    "numpy": "2.3.1",
                    "sklearn": "1.7.0",
                }
            },
            "timestamp": "2026-07-10T00:00:00+00:00",
        },
        {
            "id": "other-record",
            "entity_id": "other-run",
            "entity_type": "chat_tool_result",
            "activity": "run_adql",
            "params": {},
            "parent_ids": [],
            "agent": "test",
            "user_id": other_id,
            "environment": {"versions": {"python": "3.11", "private-pkg": "1.0"}},
            "timestamp": "2026-07-10T00:00:00+00:00",
        },
        {
            "id": "empty-record",
            "entity_id": "empty-run",
            "entity_type": "legacy",
            "activity": "legacy",
            "params": {},
            "parent_ids": [],
            "agent": "test",
            "user_id": str(user.id),
            "environment": {},
            "timestamp": "2026-07-10T00:00:00+00:00",
        },
    ]
    try:
        response = await app_client.get(
            "/api/provenance/owned-run/requirements.txt", headers=_auth(token)
        )
        assert response.status_code == 200, response.text
        assert "numpy==2.3.1" in response.text
        assert "scikit-learn==1.7.0" in response.text
        assert "# Python 3.11.12" in response.text
        assert "live-process-only" not in response.text

        idor = await app_client.get(
            "/api/provenance/other-run/requirements.txt", headers=_auth(token)
        )
        assert idor.status_code == 404
        assert "private-pkg" not in idor.text

        no_snapshot = await app_client.get(
            "/api/provenance/empty-run/requirements.txt", headers=_auth(token)
        )
        assert no_snapshot.status_code == 404
    finally:
        provenance._provenance_records[:] = saved


@pytest.mark.asyncio
async def test_tool_file_paths_fail_closed_before_executor_read(monkeypatch):
    from app import storage
    from app.services import ai_tools

    async def deny(_path, *, owner_id, db=None):
        raise storage.StorageOwnershipError("hidden")

    monkeypatch.setattr(storage, "resolve_owned_storage_key", deny)
    result = await ai_tools._execute_tool_inner(
        "read_fits_header",
        {"fits_path": "uploads/other/private.fits"},
        user_id=str(uuid.uuid4()),
    )
    assert result["success"] is False
    assert result["error_class"] == "storage_file_not_found"
    assert result["__do_not_claim__"] is True


@pytest.mark.asyncio
async def test_run_python_user_file_is_explicit_residual_fail_closed(monkeypatch):
    from app import storage
    from app.services import ai_tools

    async def allow(path, *, owner_id, db=None):
        return storage.normalize_storage_key(path)

    monkeypatch.setattr(storage, "resolve_owned_storage_key", allow)
    result = await ai_tools._execute_tool_inner(
        "run_python",
        {
            "code": "print(load_fits('uploads/u1/owned.fits'))",
            "data_source": "fits:uploads/u1/owned.fits",
        },
        user_id=str(uuid.uuid4()),
    )
    assert result["success"] is False
    assert result["error_class"] == "unbound_user_file_execution"
    assert result["__do_not_claim__"] is True
