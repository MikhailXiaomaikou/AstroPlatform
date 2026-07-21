"""Focused tests for the deterministic Union3 control-plane finalizer."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models.schemas import User
from app.models.claim_audit_records import EvidencePack
from app.models.worker_records import WorkerArtifactIssuance, WorkerNode
from app.services import union3_research_loop as research_loop
from app.services.account_deletion import anonymize_reviewer_identity
from app.services.research_workspace_service import (
    create_claim_audit_review,
    create_workspace,
    ingest_union3_source,
)
from app.services.union3_reader import (
    UNION3_ARXIV_ID,
    UNION3_SOURCE_PROFILE_KEY,
)
from app.services.union3_reproduction import run_union3_primary_reproduction
from app.services.union3_research_loop import (
    Union3ResearchLoopError,
    create_union3_reproduction_audit,
    finalize_union3_audit,
    retry_union3_reproduction_audit,
    verify_union3_attempt,
)
from app.services.worker_protocol import (
    complete_attempt,
    lease_next_task,
    sign_task_envelope,
)
from app.services.worker_contract import WORKER_PROTOCOL_VERSION
from app.services.workflow_registry_v2 import list_worker_execution_bindings
from tests.union3_source_test_support import registered_union3_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "union3_2311_12098v4_table9.txt"


def _raw_private_key(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")


def _raw_public_key(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


def _user(username: str) -> User:
    return User(
        id=uuid.uuid4(),
        username=username,
        email=f"{username}@example.test",
        password_hash="not-used",
        subscription_tier="solo",
    )


async def _completed_primary_attempt(db_session, monkeypatch):
    task_key = Ed25519PrivateKey.generate()
    evidence_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(settings, "worker_task_signing_key_id", "worker-task-v1")
    monkeypatch.setattr(
        settings, "worker_task_signing_public_key", _raw_public_key(task_key)
    )
    monkeypatch.setattr(settings, "worker_task_verification_keys", "{}")
    monkeypatch.setattr(
        settings, "evidence_signing_key", "hmac-evidence-key-32-bytes-minimum"
    )
    monkeypatch.setattr(settings, "evidence_signing_key_id", "hmac-v1")
    monkeypatch.setattr(
        settings, "evidence_v2_signing_private_key", _raw_private_key(evidence_key)
    )
    monkeypatch.setattr(settings, "evidence_v2_signing_key_id", "pack-v2-test")
    monkeypatch.setattr(
        settings, "evidence_v2_signing_public_key", _raw_public_key(evidence_key)
    )
    monkeypatch.setattr(settings, "evidence_v2_verification_keys", "{}")
    monkeypatch.setattr(settings, "claim_audit_enabled", True)
    monkeypatch.setattr(settings, "research_workspace_enabled", True)
    monkeypatch.setattr(settings, "arxiv_reader_enabled", True)
    monkeypatch.setattr(settings, "union3_reproduction_enabled", True)
    monkeypatch.setattr(settings, "evidence_pack_v2_enabled", True)
    monkeypatch.setattr(settings, "local_science_worker_enabled", True)

    owner = _user("union3-owner")
    reviewer = _user("union3-reviewer")
    db_session.add_all([owner, reviewer])
    await db_session.commit()
    workspace = await create_workspace(
        db_session, user_id=owner.id, title="Union3 deterministic loop"
    )
    source, extraction = await ingest_union3_source(
        db_session,
        user_id=owner.id,
        workspace_id=workspace.id,
        source_profile_key=UNION3_SOURCE_PROFILE_KEY,
        identifier=UNION3_ARXIV_ID,
        trusted_snapshot=registered_union3_snapshot(
            FIXTURE.read_text(encoding="utf-8")
        ),
    )
    candidate = extraction.extraction_payload["candidates"][0]
    audit, primary_job = await create_union3_reproduction_audit(
        db_session,
        user_id=owner.id,
        workspace_id=workspace.id,
        source_document_id=source.id,
        candidate_id=candidate["candidate_id"],
        workflow_key="union3_flat_lcdm_sn_only_v1",
    )
    from app.api.research_workspaces import _serialize_review_queue_item

    review_packet = _serialize_review_queue_item(audit, source, extraction)
    assert audit.atomic_claim != candidate
    assert audit.normalized_claims == [candidate]
    assert review_packet["review_binding"] == {
        "source_document_id": str(source.id),
        "source_extraction_id": str(extraction.id),
        "candidate_id": candidate["candidate_id"],
        "claim_hash": candidate["claim_hash"],
        "source_hash": source.source_document_hash,
        "anchor_ids": candidate["source_anchor_ids"],
    }
    assert review_packet["review_evidence"]["anchors"]
    worker_key = Ed25519PrivateKey.generate()
    node = WorkerNode(
        id=uuid.uuid4(),
        user_id=owner.id,
        name="Union3 test worker",
        public_key=_raw_public_key(worker_key),
        public_key_fingerprint="sha256:" + uuid.uuid4().hex * 2,
        protocol_version=WORKER_PROTOCOL_VERSION,
        status="ACTIVE",
        capabilities={
            "workflows": list_worker_execution_bindings(
                worker_image_digest="sha256:" + "2" * 64
            ),
            "concurrency": 1,
        },
        release_manifest={"image_digest": "sha256:" + "2" * 64},
    )
    db_session.add(node)
    await db_session.commit()
    attempt = await lease_next_task(
        db_session,
        node=node,
        private_key=_raw_private_key(task_key),
        key_id="worker-task-v1",
        release_commit="1" * 40,
        image_digest="sha256:" + "2" * 64,
    )
    assert attempt is not None
    primary_result = run_union3_primary_reproduction()
    environment = {
        "schema_version": "standard_astro_worker_environment_v1",
        "protocol_version": attempt.task_envelope["protocol_version"],
        "workflow_key": attempt.task_envelope["workflow_key"],
        "git_commit": attempt.task_envelope["git_commit"],
        "image_digest": attempt.task_envelope["worker_image_digest"],
        "python_version": "3.14.0-test",
        "platform_system": "test",
        "platform_machine": "test-machine",
        "mcmc": "not_applicable",
    }
    artifact_payloads = {
        "primary_analysis.json": (
            research_loop.canonical_json(primary_result) + b"\n",
            "application/json",
        ),
        "chi2_profile.svg": (
            research_loop._union3_profile_svg(primary_result).encode("utf-8"),
            "image/svg+xml",
        ),
        "environment.json": (
            research_loop.canonical_json(environment) + b"\n",
            "application/json",
        ),
    }
    artifact_objects: dict[str, bytes] = {}
    artifact_rows: list[WorkerArtifactIssuance] = []
    verified_at = datetime.now(timezone.utc)
    for name, (payload, content_type) in artifact_payloads.items():
        issuance_id = uuid.uuid4()
        prefix = f"science-attempts/{owner.id}/{attempt.id}"
        row = WorkerArtifactIssuance(
            id=issuance_id,
            batch_id=uuid.uuid4(),
            attempt_id=attempt.id,
            user_id=owner.id,
            worker_node_id=node.id,
            artifact_name=name,
            artifact_ref=f"{prefix}/uploads/{issuance_id}-{name}",
            authoritative_ref=f"{prefix}/verified/{issuance_id}-{name}",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            content_type=content_type,
            authoritative_version_id=f"test-version-{name}",
            verification_method="streamed_sha256",
            verified_at=verified_at,
            expires_at=verified_at + timedelta(minutes=15),
        )
        artifact_rows.append(row)
        artifact_objects[row.authoritative_ref] = payload
    db_session.add_all(artifact_rows)
    await db_session.flush()
    artifact_manifest: list[dict] = []
    for row in sorted(artifact_rows, key=lambda item: str(item.id)):
        artifact_manifest.extend(
            [
                research_loop._artifact_manifest_record(
                    row, status="STAGING_PENDING_CLEANUP"
                ),
                research_loop._artifact_manifest_record(
                    row, status="VERIFIED", authoritative=True
                ),
            ]
        )
    monkeypatch.setattr(
        research_loop, "download_fits", lambda key: artifact_objects[key]
    )
    await complete_attempt(
        db_session,
        node=node,
        attempt_id=attempt.id,
        lease_id=attempt.lease_id,
        result=primary_result,
        claimed_result_hash=None,
        diagnostics={"mcmc": "not_applicable"},
        artifact_manifest=artifact_manifest,
    )
    return {
        "owner": owner,
        "reviewer": reviewer,
        "workspace": workspace,
        "source": source,
        "extraction": extraction,
        "candidate": candidate,
        "audit": audit,
        "primary_job": primary_job,
        "attempt": attempt,
        "task_key": task_key,
        "artifact_objects": artifact_objects,
        "artifact_rows": artifact_rows,
        "environment": environment,
    }


async def _approve(context, db_session):
    candidate = context["candidate"]
    return await create_claim_audit_review(
        db_session,
        reviewer_user_id=context["reviewer"].id,
        reviewer_username=context["reviewer"].username,
        reviewer_usernames={context["reviewer"].username},
        workspace_id=context["workspace"].id,
        audit_id=context["audit"].id,
        source_document_id=context["source"].id,
        source_extraction_id=context["extraction"].id,
        candidate_id=candidate["candidate_id"],
        claim_hash=candidate["claim_hash"],
        source_hash=context["source"].source_document_hash,
        anchor_ids=candidate["source_anchor_ids"],
        decision="APPROVED",
        comment="Exact source, claim, and independent calculation confirmed.",
    )


@pytest.mark.asyncio
async def test_legacy_claim_audit_reconciler_does_not_fail_local_worker_run(
    db_session,
    monkeypatch,
):
    from app.models import database
    from app.tasks.claim_audit_tasks import _reconcile_stale

    context = await _completed_primary_attempt(db_session, monkeypatch)
    audit = context["audit"]
    await db_session.refresh(audit)
    assert audit.lifecycle_status == "RUNNING"
    assert audit.lease_expires_at is None

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", session_factory)
    await _reconcile_stale()

    await db_session.refresh(audit)
    assert audit.lifecycle_status == "RUNNING"
    assert audit.progress_stage == "pending_independent_verification"


@pytest.mark.asyncio
async def test_supported_requires_review_and_pack_finalization_is_idempotent(
    db_session, monkeypatch
):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    verified = await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)
    assert verified.scientific_verdict == "WITHHELD"
    assert verified.machine_support_eligible is True

    with pytest.raises(Union3ResearchLoopError) as no_review:
        await finalize_union3_audit(db_session, audit_id=verified.id)
    assert no_review.value.code == "human_review_missing"
    await db_session.refresh(verified)
    assert verified.scientific_verdict == "WITHHELD"
    assert verified.reproduction_ready is False

    await _approve(context, db_session)
    objects = context["artifact_objects"]
    uploads: list[str] = []

    def upload(key: str, payload: bytes) -> str:
        uploads.append(key)
        objects[key] = bytes(payload)
        return key

    monkeypatch.setattr(research_loop, "upload_fits", upload)
    monkeypatch.setattr(research_loop, "download_fits", lambda key: objects[key])
    monkeypatch.setattr(research_loop, "delete_fits_all_versions", objects.pop)

    finalized, pack = await finalize_union3_audit(db_session, audit_id=verified.id)
    assert finalized.scientific_verdict == "SUPPORTED"
    assert finalized.reproduction_ready is True
    assert finalized.publication_ready is False
    assert finalized.evidence_graph["evidence_pack_id"] == str(pack.id)
    assert pack.status == "FINALIZED"
    assert pack.manifest["scientific_verdict"] == "SUPPORTED"
    with zipfile.ZipFile(BytesIO(objects[pack.artifact_ref])) as archive:
        assert "chi2_profile.svg" in archive.namelist()
        report = archive.read("report.md").decode("utf-8")
        svg = archive.read("chi2_profile.svg").decode("utf-8")
        provenance = json.loads(archive.read("provenance.json"))
        diagnostics = json.loads(archive.read("diagnostics.json"))
    primary_statistics = context["attempt"].result["statistics"]
    assert primary_statistics["omega_m_best"] in report
    assert primary_statistics["omega_m_lower"] in report
    assert primary_statistics["omega_m_upper"] in report
    assert "Union3 normalized profile chi-square curve" in svg
    assert provenance["worker_environment"] == context["environment"]
    assert {row["name"] for row in provenance["primary_artifacts"]} == {
        "primary_analysis.json",
        "chi2_profile.svg",
        "environment.json",
    }
    assert provenance["primary_artifact_binding_hash"].startswith("sha256:")
    assert (
        diagnostics["primary_artifact_binding_hash"]
        == provenance["primary_artifact_binding_hash"]
        == pack.manifest["input_hashes"]["primary_artifacts"]
    )
    assert (
        pack.manifest["evidence_path"]["primary_artifacts"]
        == provenance["primary_artifacts"]
    )

    replayed_audit, replayed_pack = await finalize_union3_audit(
        db_session, audit_id=verified.id
    )
    assert replayed_audit.id == finalized.id
    assert replayed_pack.id == pack.id
    assert replayed_pack.pack_hash == pack.pack_hash
    assert uploads == [pack.artifact_ref]


@pytest.mark.asyncio
async def test_verification_requires_all_authoritative_worker_artifacts(
    db_session, monkeypatch
):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    environment_row = next(
        row
        for row in context["artifact_rows"]
        if row.artifact_name == "environment.json"
    )
    context["artifact_objects"].pop(environment_row.authoritative_ref)

    with pytest.raises(Union3ResearchLoopError) as rejected:
        await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)
    assert rejected.value.code == "worker_artifact_unavailable"
    await db_session.refresh(context["audit"])
    assert context["audit"].scientific_verdict is None
    assert context["audit"].machine_support_eligible is False


@pytest.mark.asyncio
@pytest.mark.parametrize("release_commit", ["development", "abc123"])
async def test_unpinned_worker_commit_cannot_cross_evidence_binding_gate(
    db_session,
    monkeypatch,
    release_commit: str,
):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    unsigned = dict(context["attempt"].task_envelope)
    unsigned.pop("server_signature")
    unsigned["git_commit"] = release_commit
    context["attempt"].task_envelope = sign_task_envelope(
        unsigned,
        private_key=_raw_private_key(context["task_key"]),
        key_id="worker-task-v1",
    )
    await db_session.commit()

    with pytest.raises(Union3ResearchLoopError) as rejected:
        await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)

    assert rejected.value.code == "worker_task_binding_mismatch"
    await db_session.refresh(context["audit"])
    assert context["audit"].scientific_verdict is None
    assert context["audit"].machine_support_eligible is False


@pytest.mark.asyncio
async def test_worker_environment_must_match_signed_task_envelope(
    db_session, monkeypatch
):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    environment_row = next(
        row
        for row in context["artifact_rows"]
        if row.artifact_name == "environment.json"
    )
    forged_environment = dict(context["environment"])
    forged_environment["image_digest"] = "sha256:" + "9" * 64
    payload = research_loop.canonical_json(forged_environment) + b"\n"
    context["artifact_objects"][environment_row.authoritative_ref] = payload
    environment_row.sha256 = hashlib.sha256(payload).hexdigest()
    environment_row.size_bytes = len(payload)
    manifest: list[dict] = []
    for row in sorted(context["artifact_rows"], key=lambda item: str(item.id)):
        manifest.extend(
            [
                research_loop._artifact_manifest_record(
                    row, status="STAGING_PENDING_CLEANUP"
                ),
                research_loop._artifact_manifest_record(
                    row, status="VERIFIED", authoritative=True
                ),
            ]
        )
    context["attempt"].artifact_manifest = manifest
    await db_session.commit()

    with pytest.raises(Union3ResearchLoopError) as rejected:
        await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)
    assert rejected.value.code == "worker_environment_binding_mismatch"
    await db_session.refresh(context["audit"])
    assert context["audit"].scientific_verdict is None


@pytest.mark.asyncio
async def test_finalizer_rechecks_authoritative_artifact_bytes(db_session, monkeypatch):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)
    await _approve(context, db_session)
    primary_row = next(
        row
        for row in context["artifact_rows"]
        if row.artifact_name == "primary_analysis.json"
    )
    context["artifact_objects"][primary_row.authoritative_ref] += b"tampered"
    uploads: list[str] = []
    monkeypatch.setattr(
        research_loop,
        "upload_fits",
        lambda key, _payload: uploads.append(key) or key,
    )

    with pytest.raises(Union3ResearchLoopError) as rejected:
        await finalize_union3_audit(db_session, audit_id=context["audit"].id)
    assert rejected.value.code == "worker_artifact_hash_mismatch"
    assert uploads == []
    await db_session.refresh(context["audit"])
    assert context["audit"].scientific_verdict == "WITHHELD"
    assert context["audit"].reproduction_ready is False


@pytest.mark.asyncio
async def test_verification_rejects_mutated_primary_job_args(db_session, monkeypatch):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    job = context["primary_job"]
    forged_args = dict(job.args)
    forged_inputs = dict(forged_args["normalized_inputs"])
    forged_inputs["claim_hash"] = "0" * 64
    forged_args["normalized_inputs"] = forged_inputs
    job.args = forged_args
    await db_session.commit()

    with pytest.raises(Union3ResearchLoopError) as rejected:
        await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)
    assert rejected.value.code == "primary_job_immutable_binding_mismatch"
    await db_session.refresh(context["audit"])
    assert context["audit"].scientific_verdict is None
    assert context["audit"].reproduction_ready is False


@pytest.mark.asyncio
async def test_cancelled_audit_cannot_be_revived_by_late_verification(
    db_session, monkeypatch
):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    audit = context["audit"]
    audit.lifecycle_status = "CANCELLED"
    audit.completed_at = research_loop._utcnow()
    await db_session.commit()

    with pytest.raises(Union3ResearchLoopError) as rejected:
        await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)
    assert rejected.value.code == "claim_audit_cancelled"
    await db_session.refresh(audit)
    assert audit.lifecycle_status == "CANCELLED"
    assert audit.scientific_verdict is None


@pytest.mark.asyncio
async def test_transient_independent_verifier_failure_sets_no_scientific_verdict(
    db_session, monkeypatch
):
    context = await _completed_primary_attempt(db_session, monkeypatch)

    def unavailable(_result):
        raise OSError("temporary verifier outage")

    monkeypatch.setattr(research_loop, "verify_union3_primary_result", unavailable)
    with pytest.raises(Union3ResearchLoopError) as rejected:
        await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)
    assert rejected.value.code == "independent_verifier_unavailable"
    assert rejected.value.status_code == 503
    await db_session.refresh(context["audit"])
    assert context["audit"].lifecycle_status == "RUNNING"
    assert context["audit"].scientific_verdict is None


@pytest.mark.asyncio
async def test_registered_retry_requeues_same_immutable_job_with_new_attempt_budget(
    db_session, monkeypatch
):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    audit = context["audit"]
    job = context["primary_job"]
    audit.lifecycle_status = "FAILED_RETRYABLE"
    audit.scientific_verdict = None
    audit.error_class = "worker_attempts_exhausted"
    audit.error = "temporary worker failures"
    job.status = "FAILED"
    job.current_attempt_id = None
    job.result = None
    job.error_class = "worker_attempts_exhausted"
    await db_session.commit()

    retried = await retry_union3_reproduction_audit(
        db_session,
        audit_id=audit.id,
        user_id=context["owner"].id,
    )
    assert retried.lifecycle_status == "QUEUED"
    assert retried.retry_count == 1
    await db_session.refresh(job)
    assert job.status == "QUEUED"
    assert job.args == context["primary_job"].args

    node = await db_session.get(WorkerNode, context["attempt"].worker_node_id)
    next_attempt = await lease_next_task(
        db_session,
        node=node,
        private_key=_raw_private_key(Ed25519PrivateKey.generate()),
        key_id="worker-task-v1",
        release_commit="3" * 40,
        image_digest="sha256:" + "2" * 64,
    )
    assert next_attempt is not None
    assert next_attempt.attempt_number == 2
    await db_session.refresh(retried)
    assert retried.lifecycle_status == "RUNNING"


@pytest.mark.asyncio
async def test_idempotent_finalization_rejects_tampered_pack_ledger(
    db_session, monkeypatch
):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)
    await _approve(context, db_session)
    objects = context["artifact_objects"]
    monkeypatch.setattr(
        research_loop,
        "upload_fits",
        lambda key, payload: objects.setdefault(key, bytes(payload)) and key,
    )
    monkeypatch.setattr(research_loop, "download_fits", lambda key: objects[key])
    monkeypatch.setattr(research_loop, "delete_fits_all_versions", objects.pop)
    finalized, pack = await finalize_union3_audit(
        db_session, audit_id=context["audit"].id
    )
    assert finalized.scientific_verdict == "SUPPORTED"

    forged_manifest = dict(pack.manifest)
    forged_manifest["publication_ready"] = True
    pack.manifest = forged_manifest
    await db_session.commit()
    with pytest.raises(Union3ResearchLoopError) as rejected:
        await finalize_union3_audit(db_session, audit_id=context["audit"].id)
    assert rejected.value.code == "evidence_pack_binding_invalid"


@pytest.mark.asyncio
async def test_reviewer_deletion_preserves_pseudonymous_review_and_pack(
    db_session, monkeypatch
):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)
    review = await _approve(context, db_session)
    reviewer_username = context["reviewer"].username
    reviewer_id = context["reviewer"].id

    assert (
        await anonymize_reviewer_identity(
            db_session,
            reviewer_user_id=reviewer_id,
        )
        == 1
    )
    await db_session.delete(context["reviewer"])
    await db_session.commit()
    await db_session.refresh(review)
    assert review.reviewer_user_id is None
    assert review.reviewer_username.startswith("reviewer:")

    objects = context["artifact_objects"]
    monkeypatch.setattr(
        research_loop,
        "upload_fits",
        lambda key, payload: objects.setdefault(key, bytes(payload)) and key,
    )
    monkeypatch.setattr(research_loop, "download_fits", lambda key: objects[key])
    monkeypatch.setattr(research_loop, "delete_fits_all_versions", objects.pop)
    finalized, pack = await finalize_union3_audit(
        db_session,
        audit_id=context["audit"].id,
    )
    assert finalized.scientific_verdict == "SUPPORTED"

    with zipfile.ZipFile(BytesIO(objects[pack.artifact_ref])) as archive:
        reviews = json.loads(archive.read("reviews.json"))
    serialized = json.dumps(reviews, sort_keys=True)
    assert reviewer_username not in serialized
    assert str(reviewer_id) not in serialized
    assert reviews[0]["reviewer_pseudonym"].startswith("reviewer:")
    assert "reviewer_user_id" not in reviews[0]
    assert "reviewer_username" not in reviews[0]


@pytest.mark.asyncio
async def test_final_commit_ack_loss_never_deletes_durable_supported_pack(
    db_session,
    monkeypatch,
):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)
    await _approve(context, db_session)
    objects = context["artifact_objects"]
    monkeypatch.setattr(
        research_loop,
        "upload_fits",
        lambda key, payload: objects.setdefault(key, bytes(payload)) and key,
    )
    monkeypatch.setattr(research_loop, "download_fits", lambda key: objects[key])

    original_commit = db_session.commit
    commit_count = 0

    async def commit_with_lost_final_ack():
        nonlocal commit_count
        commit_count += 1
        await original_commit()
        if commit_count == 2:
            raise OSError("database commit acknowledgement lost")

    monkeypatch.setattr(db_session, "commit", commit_with_lost_final_ack)
    with pytest.raises(OSError, match="acknowledgement lost"):
        await finalize_union3_audit(db_session, audit_id=context["audit"].id)
    monkeypatch.setattr(db_session, "commit", original_commit)

    durable_audit = await db_session.get(
        type(context["audit"]),
        context["audit"].id,
    )
    durable_pack = await db_session.scalar(
        select(EvidencePack).where(EvidencePack.audit_id == context["audit"].id)
    )
    assert durable_audit.scientific_verdict == "SUPPORTED"
    assert durable_pack.status == "FINALIZED"
    assert durable_pack.artifact_ref in objects

    replayed_audit, replayed_pack = await finalize_union3_audit(
        db_session,
        audit_id=context["audit"].id,
    )
    assert replayed_audit.scientific_verdict == "SUPPORTED"
    assert replayed_pack.id == durable_pack.id


@pytest.mark.asyncio
async def test_operator_kill_switch_and_owner_deletion_block_supported_transition(
    db_session,
    monkeypatch,
):
    context = await _completed_primary_attempt(db_session, monkeypatch)
    await verify_union3_attempt(db_session, attempt_id=context["attempt"].id)
    await _approve(context, db_session)
    uploads: list[str] = []
    monkeypatch.setattr(
        research_loop,
        "upload_fits",
        lambda key, _payload: uploads.append(key) or key,
    )

    monkeypatch.setattr(settings, "evidence_pack_v2_enabled", False)
    with pytest.raises(Union3ResearchLoopError) as disabled:
        await finalize_union3_audit(db_session, audit_id=context["audit"].id)
    assert disabled.value.code == "union3_feature_disabled"
    assert uploads == []
    await db_session.refresh(context["audit"])
    assert context["audit"].scientific_verdict == "WITHHELD"

    monkeypatch.setattr(settings, "evidence_pack_v2_enabled", True)
    context["owner"].account_status = "DELETION_PENDING"
    await db_session.commit()
    with pytest.raises(Union3ResearchLoopError) as inactive:
        await finalize_union3_audit(db_session, audit_id=context["audit"].id)
    assert inactive.value.code == "claim_audit_owner_inactive"
    assert uploads == []
