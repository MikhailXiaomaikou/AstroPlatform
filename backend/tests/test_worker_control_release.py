"""Release identity gates for local-science Worker claims."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from sqlalchemy import select

from app.api import worker_control
from app.api.worker_control import EnrollNodeRequest
from app.config import settings
from app.models.research_records import ResearchJob
from app.models.schemas import User
from app.models.worker_records import ScienceExecutionAttempt
from app.services.registered_workflows import (
    UNION3_REPRODUCTION_WORKFLOW_ID,
    get_registered_dataset_pins,
)
from app.services.worker_protocol import create_enrollment_token, enroll_worker_node
from app.worker_agent.client import (
    WorkerClientError,
    WorkerConfig,
    verify_task_envelope,
)


def _clear_release_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RENDER_GIT_COMMIT", "GIT_COMMIT", "TOOL_VERSION"):
        monkeypatch.delenv(name, raising=False)


def _private_text(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")


def _public_text(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


async def _enrolled_node_with_job(db_session, *, observed_commit: str):
    owner_id = uuid.uuid4()
    owner = User(
        id=owner_id,
        username=f"release-owner-{owner_id.hex}",
        email=f"release-{owner_id.hex}@example.test",
        password_hash="not-used",
        subscription_tier="solo",
    )
    db_session.add(owner)
    await db_session.commit()
    _token, code = await create_enrollment_token(db_session, user_id=owner.id)
    node = await enroll_worker_node(
        db_session,
        enrollment_code=code,
        name="Release contract node",
        public_key=_public_text(Ed25519PrivateKey.generate()),
        protocol_version="1",
        capabilities={
            "workflows": [UNION3_REPRODUCTION_WORKFLOW_ID],
            "concurrency": 1,
        },
        release_manifest={
            "git_commit": observed_commit,
            "image_digest": "unknown",
        },
    )
    job = ResearchJob(
        job_id=f"release-job-{uuid.uuid4().hex}",
        user_id=owner.id,
        tool_name=UNION3_REPRODUCTION_WORKFLOW_ID,
        inputs_hash="a" * 64,
        args={
            "workflow_key": UNION3_REPRODUCTION_WORKFLOW_ID,
            "dataset_pins": get_registered_dataset_pins(
                UNION3_REPRODUCTION_WORKFLOW_ID
            ),
        },
        status="QUEUED",
        background_backend="https_worker",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    return node, job


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("observed_commit", "local_commit", "expected_commit"),
    [
        pytest.param("", None, "development", id="empty"),
        pytest.param("unknown", "unknown", "development", id="unknown"),
        pytest.param("abc123", "abc123", "abc123", id="descriptive-dev-commit"),
        pytest.param("c" * 40, "c" * 40, "c" * 40, id="full-worker-sha"),
    ],
)
async def test_development_claim_creates_worker_verifiable_signed_lease(
    monkeypatch: pytest.MonkeyPatch,
    db_session,
    observed_commit: str,
    local_commit: str | None,
    expected_commit: str,
):
    _clear_release_environment(monkeypatch)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setattr(settings, "docker_image_digest", "")
    control_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        settings, "worker_task_signing_private_key", _private_text(control_key)
    )
    monkeypatch.setattr(settings, "worker_task_signing_key_id", "control-release-test")
    node, job = await _enrolled_node_with_job(
        db_session,
        observed_commit=observed_commit,
    )

    envelope = await worker_control.claim_task(
        wait_seconds=0,
        node=node,
        db=db_session,
    )

    assert envelope["git_commit"] == expected_commit
    attempt = await db_session.scalar(
        select(ScienceExecutionAttempt).where(
            ScienceExecutionAttempt.job_id == job.job_id
        )
    )
    assert attempt is not None
    assert attempt.status == "LEASED"
    assert attempt.task_envelope == envelope

    if local_commit is not None:
        monkeypatch.setenv("GIT_COMMIT", local_commit)
    config = WorkerConfig(
        control_plane_url="https://control.example.test",
        worker_id=str(node.id),
        worker_name=node.name,
        private_key=_private_text(Ed25519PrivateKey.generate()),
        protocol_version="1",
        task_signing_key_id="control-release-test",
        task_signing_public_key=_public_text(control_key),
    )
    verify_task_envelope(config, envelope)
    if expected_commit != "development":
        mismatched_commit = "d" * 40 if len(expected_commit) == 40 else "def456"
        monkeypatch.setenv("GIT_COMMIT", mismatched_commit)
        with pytest.raises(WorkerClientError, match="different worker release"):
            verify_task_envelope(config, envelope)


@pytest.mark.asyncio
async def test_configured_control_plane_sha_still_rejects_stale_worker(
    monkeypatch: pytest.MonkeyPatch,
):
    control_commit = "a" * 40
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("GIT_COMMIT", control_commit)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("TOOL_VERSION", raising=False)
    monkeypatch.setattr(settings, "docker_image_digest", "")

    async def unexpected_lease(*_args, **_kwargs):
        raise AssertionError("a stale Worker must not reach task leasing")

    monkeypatch.setattr(worker_control, "lease_next_task", unexpected_lease)
    node = SimpleNamespace(
        release_manifest={"git_commit": "b" * 40, "image_digest": "unknown"}
    )

    with pytest.raises(HTTPException) as exc_info:
        await worker_control.claim_task(wait_seconds=0, node=node, db=object())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "worker_release_manifest_stale"


@pytest.mark.parametrize("environment", ["production", " PROD "])
@pytest.mark.parametrize("configured_commit", [None, "development", "abc123"])
def test_production_release_commit_requires_full_sha(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    configured_commit: str | None,
):
    _clear_release_environment(monkeypatch)
    monkeypatch.setenv("ENV", environment)
    if configured_commit is not None:
        monkeypatch.setenv("GIT_COMMIT", configured_commit)

    with pytest.raises(HTTPException) as exc_info:
        worker_control._release_commit()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "release_commit_unavailable"


def test_production_release_commit_accepts_full_sha(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_release_environment(monkeypatch)
    monkeypatch.setenv("ENV", " PROD ")
    monkeypatch.setenv("GIT_COMMIT", "C" * 40)

    assert worker_control._release_commit() == "c" * 40


@pytest.mark.asyncio
async def test_prod_alias_enrollment_requires_full_worker_sha(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_release_environment(monkeypatch)
    monkeypatch.setenv("ENV", " prod ")
    monkeypatch.setattr(settings, "local_science_worker_enabled", True)
    monkeypatch.setattr(settings, "docker_image_digest", "")
    request = EnrollNodeRequest(
        enrollment_code="x" * 32,
        name="Invalid production node",
        public_key="x" * 40,
        protocol_version="1",
        capabilities={
            "workflows": [UNION3_REPRODUCTION_WORKFLOW_ID],
            "concurrency": 1,
        },
        release_manifest={"git_commit": "unknown", "image_digest": "unknown"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await worker_control.enroll_node(request, db=object())

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "worker_release_commit_invalid"


@pytest.mark.asyncio
async def test_prod_v2_enrollment_rejects_self_reported_unapproved_image(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_release_environment(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(settings, "local_science_worker_enabled", True)
    approved_digest = "sha256:" + "1" * 64
    observed_digest = "sha256:" + "2" * 64
    monkeypatch.setattr(settings, "docker_image_digest", approved_digest)
    request = EnrollNodeRequest(
        enrollment_code="x" * 32,
        name="Unapproved v2 node",
        public_key="x" * 40,
        protocol_version=worker_control.WORKER_PROTOCOL_VERSION,
        capabilities={
            "entrypoints": worker_control.list_static_worker_entrypoint_capabilities(
                worker_image_digest=observed_digest
            ),
            "concurrency": 1,
        },
        release_manifest={
            "git_commit": "a" * 40,
            "image_digest": observed_digest,
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        await worker_control.enroll_node(request, db=object())

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "worker_image_digest_mismatch"
