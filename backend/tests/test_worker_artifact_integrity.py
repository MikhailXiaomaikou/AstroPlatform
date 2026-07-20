"""Regression tests for Worker upload receipts and byte-level verification."""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import worker_control
from app.api.worker_control import (
    ArtifactRequest,
    ArtifactUrlsRequest,
    _verify_completed_artifacts,
    artifact_urls,
)
from app.config import settings
from app.models.research_records import ResearchJob
from app.models.worker_records import (
    ScienceExecutionAttempt,
    WorkerArtifactIssuance,
    WorkerNode,
)
from app import storage
from app.storage import (
    StorageIntegrityError,
    create_presigned_upload_url,
    promote_verified_storage_object,
    verify_storage_object,
)


class _StreamingBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False

    def iter_chunks(self, *, chunk_size: int):
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset : offset + chunk_size]

    def close(self) -> None:
        self.closed = True


class _FakeS3:
    def __init__(
        self,
        *,
        payload: bytes = b"",
        native_sha256: str | None = None,
        metadata_sha256: str | None = None,
    ) -> None:
        self.payload = payload
        self.native_sha256 = native_sha256
        self.metadata_sha256 = metadata_sha256
        self.presign_params = None
        self.copy_params = None
        self.get_calls = 0

    def generate_presigned_url(self, operation, *, Params, ExpiresIn, HttpMethod):
        self.presign_params = {
            "operation": operation,
            "params": Params,
            "expires_in": ExpiresIn,
            "method": HttpMethod,
        }
        return "https://objects.example.test/signed"

    def head_object(self, **_kwargs):
        response = {
            "ContentLength": len(self.payload),
            "Metadata": {"sha256": self.metadata_sha256 or ""},
            "VersionId": "version-1",
        }
        if self.native_sha256 is not None:
            response["ChecksumSHA256"] = self.native_sha256
        return response

    def get_object(self, **_kwargs):
        self.get_calls += 1
        return {"Body": _StreamingBody(self.payload), "VersionId": "version-1"}

    def copy_object(self, **kwargs):
        self.copy_params = kwargs
        return {"VersionId": "authoritative-version-1"}


def _use_fake_s3(monkeypatch, client: _FakeS3) -> None:
    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setattr(settings, "s3_bucket", "research")
    monkeypatch.setattr(storage, "_s3_client", lambda: client)


def test_presigned_upload_binds_native_sha256_and_content_length(monkeypatch):
    payload = b"registered science artifact"
    digest = hashlib.sha256(payload).hexdigest()
    client = _FakeS3(payload=payload)
    _use_fake_s3(monkeypatch, client)

    receipt = create_presigned_upload_url(
        "science-attempts/user/attempt/uploads/result.json",
        sha256=digest,
        size_bytes=len(payload),
        content_type="application/json",
    )

    expected_checksum = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
    assert client.presign_params is not None
    assert client.presign_params["params"]["ChecksumSHA256"] == expected_checksum
    assert client.presign_params["params"]["ContentLength"] == len(payload)
    assert receipt["headers"]["x-amz-checksum-sha256"] == expected_checksum
    assert receipt["headers"]["Content-Length"] == str(len(payload))


def test_native_storage_checksum_avoids_downloading_object(monkeypatch):
    payload = b"native checksum payload"
    digest = hashlib.sha256(payload).hexdigest()
    client = _FakeS3(
        payload=payload,
        native_sha256=base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
    )
    _use_fake_s3(monkeypatch, client)

    receipt = verify_storage_object(
        "science-attempts/user/attempt/uploads/result.json",
        expected_sha256=digest,
        expected_size_bytes=len(payload),
    )

    assert receipt["verification_method"] == "s3_checksum_sha256"
    assert receipt["sha256"] == digest
    assert client.get_calls == 0


def test_forged_user_metadata_cannot_replace_actual_byte_hash(monkeypatch):
    expected_payload = b"expected artifact bytes"
    forged_payload = b"different artifact byte"
    assert len(expected_payload) == len(forged_payload)
    expected_digest = hashlib.sha256(expected_payload).hexdigest()
    client = _FakeS3(
        payload=forged_payload,
        metadata_sha256=expected_digest,
    )
    _use_fake_s3(monkeypatch, client)

    with pytest.raises(StorageIntegrityError, match="byte-level SHA-256 mismatch"):
        verify_storage_object(
            "science-attempts/user/attempt/uploads/result.json",
            expected_sha256=expected_digest,
            expected_size_bytes=len(expected_payload),
        )
    assert client.get_calls == 1


def test_verified_staging_object_is_promoted_to_server_only_key(monkeypatch):
    payload = b"immutable authoritative artifact"
    digest = hashlib.sha256(payload).hexdigest()
    client = _FakeS3(payload=payload)
    _use_fake_s3(monkeypatch, client)

    source = verify_storage_object(
        "science-attempts/user/attempt/uploads/result.json",
        expected_sha256=digest,
        expected_size_bytes=len(payload),
    )
    promoted = promote_verified_storage_object(
        "science-attempts/user/attempt/uploads/result.json",
        "science-attempts/user/attempt/verified/result.json",
        expected_sha256=digest,
        expected_size_bytes=len(payload),
        content_type="application/json",
        source_version_id=source["version_id"],
        source_etag=source["etag"],
    )

    assert client.copy_params is not None
    assert client.copy_params["CopySource"]["VersionId"] == "version-1"
    assert client.copy_params["Key"].endswith("/verified/result.json")
    assert promoted["key"].endswith("/verified/result.json")


async def _active_attempt(db_session, user_id: uuid.UUID):
    now = datetime.now(timezone.utc)
    attempt_id = uuid.uuid4()
    job = ResearchJob(
        job_id=f"artifact-job-{uuid.uuid4().hex}",
        user_id=user_id,
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
        user_id=user_id,
        name="artifact test node",
        public_key="test-only",
        public_key_fingerprint="sha256:" + uuid.uuid4().hex * 2,
        protocol_version="1",
        status="ACTIVE",
        capabilities={},
        release_manifest={},
    )
    attempt = ScienceExecutionAttempt(
        id=attempt_id,
        job_id=job.job_id,
        user_id=user_id,
        worker_node_id=node.id,
        attempt_number=1,
        status="RUNNING",
        lease_id="b" * 64,
        lease_expires_at=now + timedelta(minutes=2),
        input_hash=job.inputs_hash,
        task_envelope={},
        artifact_manifest=[],
    )
    db_session.add_all([job, node, attempt])
    await db_session.commit()
    return node, attempt


@pytest.mark.asyncio
async def test_reissuing_urls_keeps_every_key_in_append_only_ledger(
    db_session,
    test_user,
    monkeypatch,
):
    user, _token = test_user
    node, attempt = await _active_attempt(db_session, user.id)
    monkeypatch.setattr(
        worker_control,
        "create_presigned_upload_url",
        lambda path, **_kwargs: {
            "method": "PUT",
            "url": f"https://objects.example.test/{path}",
            "artifact_ref": path,
            "expires_in": 900,
            "headers": {},
        },
    )
    request = ArtifactUrlsRequest(
        lease_id=attempt.lease_id,
        artifacts=[
            ArtifactRequest(
                name="profile.json",
                sha256=hashlib.sha256(b"profile").hexdigest(),
                size_bytes=len(b"profile"),
                content_type="application/json",
            )
        ],
    )

    first = await artifact_urls(attempt.id, request, node=node, db=db_session)
    second = await artifact_urls(attempt.id, request, node=node, db=db_session)

    rows = list(
        (
            await db_session.execute(
                select(WorkerArtifactIssuance)
                .where(WorkerArtifactIssuance.attempt_id == attempt.id)
                .order_by(WorkerArtifactIssuance.id.asc())
            )
        )
        .scalars()
        .all()
    )
    await db_session.refresh(attempt)
    assert len(rows) == 2
    assert rows[0].artifact_ref != rows[1].artifact_ref
    assert first["batch_id"] != second["batch_id"]
    assert {item["artifact_ref"] for item in attempt.artifact_manifest} == {
        row.artifact_ref for row in rows
    }

    chosen_ref = second["uploads"][0]["artifact_ref"]
    monkeypatch.setattr(
        worker_control,
        "verify_storage_object",
        lambda path, **_kwargs: {
            "key": path,
            "verification_method": "streamed_sha256",
            "version_id": "version-2",
            "etag": '"source-etag"',
        },
    )
    monkeypatch.setattr(
        worker_control,
        "promote_verified_storage_object",
        lambda source, destination, **_kwargs: {
            "key": destination,
            "source_key": source,
            "verification_method": "s3_checksum_sha256",
            "version_id": "authoritative-version-2",
        },
    )
    final_manifest = await _verify_completed_artifacts(
        db_session,
        attempt,
        [{"artifact_ref": chosen_ref}],
    )
    assert {item["status"] for item in final_manifest} == {
        "STAGING_PENDING_CLEANUP",
        "VERIFIED",
        "SUPERSEDED_PENDING_CLEANUP",
    }
    verified = next(
        item for item in final_manifest if item["status"] == "VERIFIED"
    )
    assert verified["version_id"] == "authoritative-version-2"
    assert verified["artifact_ref"] != chosen_ref
    assert verified["staging_artifact_ref"] == chosen_ref
    chosen_row = next(row for row in rows if row.artifact_ref == chosen_ref)
    assert verified["artifact_ref"] == chosen_row.authoritative_ref
    assert chosen_row.authoritative_version_id == "authoritative-version-2"
    assert chosen_row.verified_at is not None


@pytest.mark.asyncio
async def test_completion_rejects_unissued_artifact_reference(
    db_session,
    test_user,
):
    user, _token = test_user
    _node, attempt = await _active_attempt(db_session, user.id)

    with pytest.raises(HTTPException) as error:
        await _verify_completed_artifacts(
            db_session,
            attempt,
            [{"artifact_ref": "science-attempts/foreign/result.json"}],
        )
    assert error.value.status_code == 422
    assert error.value.detail == "artifact_manifest_mismatch"


@pytest.mark.asyncio
async def test_cancelled_job_cannot_mint_new_artifact_upload_urls(
    db_session,
    test_user,
):
    user, _token = test_user
    node, attempt = await _active_attempt(db_session, user.id)
    job = await db_session.get(ResearchJob, attempt.job_id)
    job.status = "CANCELLED"
    await db_session.commit()
    request = ArtifactUrlsRequest(
        lease_id=attempt.lease_id,
        artifacts=[
            ArtifactRequest(
                name="late.json",
                sha256=hashlib.sha256(b"late").hexdigest(),
                size_bytes=4,
                content_type="application/json",
            )
        ],
    )
    with pytest.raises(HTTPException) as rejected:
        await artifact_urls(attempt.id, request, node=node, db=db_session)
    assert rejected.value.status_code == 409
    assert rejected.value.detail == "science_job_cancelled"
    rows = list(
        (
            await db_session.execute(
                select(WorkerArtifactIssuance).where(
                    WorkerArtifactIssuance.attempt_id == attempt.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []
