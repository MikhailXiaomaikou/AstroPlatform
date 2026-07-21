"""Protected Candidate source-materialization lifecycle and trust boundaries."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

import app.services.foundry_materialization as materialization_module
import app.api.foundry_materialization as materialization_api
from app.api.foundry_materialization import _reservation_status
from app.config import settings
from app.models.foundry_records import (
    FoundryCandidate,
    FoundryCandidateEvent,
    FoundryDemoRun,
    FoundryReview,
)
from app.services.foundry_catalog import (
    FoundryCatalogError,
    _append_event,
    _validate_formal_source_provenance,
    append_candidate_version,
    record_demo_report,
    review_candidate_version,
    sha256_json,
    start_validation_run,
)
from app.services.foundry_materialization import (
    record_finalization_dispatch,
    record_materialization_dispatch,
    record_materialization_final_receipt,
    record_materialization_pr_attestation,
    request_materialization_finalization,
    request_source_materialization,
)
from app.services.foundry_materialization_dispatch import (
    FoundryMaterializationDispatchConfig,
    FoundryMaterializationDispatchError,
    dispatch_materialization_pr,
)
from scripts.prepare_foundry_materialization import (
    _verify_protected_main_ancestry,
    _verify_pull_request_merge_identity,
)
from scripts.sign_foundry_materialization_receipt import _sign_ed25519


REPOSITORY = "standard-astro/platform"
PR_WORKFLOW = "foundry-materialize-candidate.yml"
FINAL_WORKFLOW = "foundry-finalize-materialization.yml"
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"\x19" * 32)
PUBLIC_KEY = base64.b64encode(
    PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
).decode()
KEYRING = {"materialization-test-1": PUBLIC_KEY}
DOMAIN = b"standard-astro/foundry-materialization/v1\0"
EMPTY = hashlib.sha256(b"").hexdigest()


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_finalizer_checks_same_repo_main_and_real_git_ancestry(tmp_path: Path):
    repository = tmp_path / "repository"
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(repository)],
        check=True,
        capture_output=True,
    )
    _git(repository, "config", "user.name", "Foundry Test")
    _git(repository, "config", "user.email", "foundry@example.test")
    tracked = repository / "tracked.txt"
    tracked.write_text("root\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "root")
    root_commit = _git(repository, "rev-parse", "HEAD")

    _git(repository, "checkout", "-b", "fork")
    tracked.write_text("fork\n", encoding="utf-8")
    _git(repository, "commit", "-am", "fork")
    fork_commit = _git(repository, "rev-parse", "HEAD")

    _git(repository, "checkout", "main")
    tracked.write_text("main\n", encoding="utf-8")
    _git(repository, "commit", "-am", "main")
    main_commit = _git(repository, "rev-parse", "HEAD")
    _git(repository, "update-ref", "refs/remotes/origin/main", main_commit)

    binding = {
        "pull_request_number": 42,
        "pull_request_head_commit": "f" * 40,
    }
    pull_request = {
        "number": 42,
        "state": "MERGED",
        "baseRefName": "main",
        "headRefOid": "f" * 40,
        "headRepository": {"nameWithOwner": REPOSITORY},
        "mergeCommit": {"oid": root_commit},
    }
    identity = _verify_pull_request_merge_identity(
        pull_request,
        binding=binding,
        expected_repository=REPOSITORY,
        expected_merge_commit=root_commit,
    )
    assert identity["pull_request_base_ref"] == "main"
    assert identity["pull_request_head_repository"] == REPOSITORY
    assert (
        _verify_protected_main_ancestry(
            repository,
            merge_commit=root_commit,
            protected_main_commit=main_commit,
        )
        == main_commit
    )

    with pytest.raises(ValueError, match="materialization_merge_identity_invalid"):
        _verify_pull_request_merge_identity(
            {**pull_request, "baseRefName": "attacker-base"},
            binding=binding,
            expected_repository=REPOSITORY,
            expected_merge_commit=root_commit,
        )
    with pytest.raises(ValueError, match="materialization_merge_identity_invalid"):
        _verify_pull_request_merge_identity(
            {
                **pull_request,
                "headRepository": {"nameWithOwner": "attacker/fork"},
            },
            binding=binding,
            expected_repository=REPOSITORY,
            expected_merge_commit=root_commit,
        )
    with pytest.raises(
        ValueError, match="materialization_merge_not_on_protected_main"
    ):
        _verify_protected_main_ancestry(
            repository,
            merge_commit=fork_commit,
            protected_main_commit=main_commit,
        )


def _signed(payload: dict) -> dict:
    payload = json.loads(json.dumps(payload))
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    envelope = {
        "schema_version": "standard_astro_materialization_attestation_bundle_v1",
        "payload": payload,
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "signature": {
            "algorithm": "ed25519",
            "key_id": "materialization-test-1",
            "value": base64.b64encode(PRIVATE_KEY.sign(DOMAIN + canonical)).decode(),
        },
    }
    envelope["receipt_sha256"] = sha256_json(envelope)
    return envelope


async def _approved_generated_version(
    db_session, reviewer_id: uuid.UUID, *, gap_nonce: str | None = None
):
    descriptor = {
        "gap_code": "registered_workflow_missing",
        "dataset_key": "materialization-test",
        "research_domain": "cosmology",
    }
    if gap_nonce is not None:
        descriptor["test_nonce"] = gap_nonce
    candidate = FoundryCandidate(
        gap_fingerprint=sha256_json(descriptor),
        gap_code=descriptor["gap_code"],
        gap_descriptor=descriptor,
        status="BUILDING",
        risk_level="R1",
        generation_route="SCIENCE_CODE",
    )
    db_session.add(candidate)
    await db_session.flush()
    nonce_suffix = (
        f"_{hashlib.sha256(gap_nonce.encode()).hexdigest()[:8]}"
        if gap_nonce is not None
        else ""
    )
    candidate_key = f"generated_materialization_test{nonce_suffix}_v1"
    workflow_id = f"generated_materialization_workflow{nonce_suffix}_v1"
    base_tree = "4" * 64
    patched_tree = "5" * 64
    patch_hash = "6" * 64
    bundle = {
        "schema_version": 1,
        "candidate_id": candidate_key,
        "candidate_version": 1,
        "proposed_workflow_id": workflow_id,
        "entrypoint_id": "candidate_generated_python_demo_v1",
        "risk_level": "R1",
        "workflow_spec": {
            "workflow_id": workflow_id,
            "workflow_version": "1.0.0-candidate.1",
            "claim_scope": "non_formal_generated_demo",
            "output_policy": {"publication_ready": False},
        },
        "source_pins": [
            {
                "key": "materialization_fixture",
                "url": "https://example.test/materialization-fixture",
                "sha256": "1" * 64,
            }
        ],
        "fixture_hashes": [],
        "dependency_lock_sha256": "7" * 64,
        "runner_definition_sha256": "8" * 64,
        "generation": {
            "kind": "ai_draft_provider_contract_v1",
            "model": "provider/model",
            "prompt_or_claim_stored": False,
            "generated_code_executed_by_draft_job": False,
            "source_hash_algorithm": "standard_astro_tracked_source_manifest_v1",
            "source_base_commit": "a" * 40,
            "source_base_tree_sha256": base_tree,
            "source_tree_sha256": patched_tree,
            "source_materialization_required": True,
        },
        "limitations": ["Non-formal generated Candidate."],
        "output_policy": {
            "evidence_class": "NON_FORMAL_DEMO",
            "publication_ready": False,
            "claim_eligible": False,
            "evidence_pack_allowed": False,
        },
    }
    version = await append_candidate_version(
        db_session,
        candidate=candidate,
        draft={
            "candidate_bundle": bundle,
            "validation_runner_image_digest": "sha256:" + "9" * 64,
            "code_tree_hash": patched_tree,
            "patch_hash": patch_hash,
            "sbom_hash": "b" * 64,
        },
        actor_kind="AI_DRAFT_JOB",
        actor_user_id=None,
    )
    draft_run_id = uuid.uuid4()
    await _append_event(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="AI_DRAFT_RESULT_ACCEPTED",
        actor_kind="AI_DRAFT_JOB",
        actor_user_id=None,
        payload={
            "draft_run_id": str(draft_run_id),
            "artifact_manifest": [
                {"kind": "CANDIDATE_BUNDLE", "sha256": "c" * 64},
                {"kind": "PATCH", "sha256": patch_hash},
                {"kind": "SBOM", "sha256": "b" * 64},
            ],
            "artifact_receipt": {
                "repository": REPOSITORY,
                "workflow_run_id": "123",
                "artifact_id": "456",
                "artifact_name": f"foundry-draft-{draft_run_id}",
                "artifact_sha256": "d" * 64,
            },
            "source_receipt": {
                "base_commit": "a" * 40,
                "base_source_tree_sha256": base_tree,
                "post_patch_source_tree_sha256": patched_tree,
            },
        },
    )
    await db_session.commit()
    validation = await start_validation_run(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        actor_kind="HUMAN_REVIEWER",
        actor_user_id=reviewer_id,
    )
    now = datetime.now(timezone.utc)
    environment = {"python": "3.12", "entrypoint_id": bundle["entrypoint_id"]}
    report = {
        "schema_version": 1,
        "candidate_id": version.candidate_key,
        "candidate_version": version.version_number,
        "demo_run_id": str(uuid.uuid4()),
        "status": "PASSED",
        "evidence_class": "NON_FORMAL_DEMO",
        "publication_ready": False,
        "claim_eligible": False,
        "evidence_pack_allowed": False,
        "candidate_bundle_sha256": sha256_json(version.candidate_bundle),
        "candidate_version_sha256": version.version_hash,
        "workflow_spec_sha256": version.workflow_spec_hash,
        "dependency_lock_sha256": version.dependency_lock_hash,
        "runner_definition_sha256": bundle["runner_definition_sha256"],
        "runner_image_digest": version.validation_runner_image_digest,
        "environment": environment,
        "environment_sha256": sha256_json(environment),
        "generation": bundle["generation"],
        "source_pins": bundle["source_pins"],
        "fixture_hashes": [],
        "started_at": now.isoformat(),
        "completed_at": (now + timedelta(seconds=1)).isoformat(),
        "duration_ms": 1000,
        "stdout_sha256": EMPTY,
        "stderr_sha256": EMPTY,
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "artifact_manifest": [
            {"path": "stdout.log", "kind": "STDOUT", "sha256": EMPTY, "bytes": 0},
            {"path": "stderr.log", "kind": "STDERR", "sha256": EMPTY, "bytes": 0},
        ],
        "result": {"status": "demo_only"},
        "limitations": ["Non-formal."],
        "validation_summary": {"passed": True},
        "failure_class": None,
        "resource_usage": {},
    }
    report["demo_report_sha256"] = sha256_json(report)
    await record_demo_report(
        db_session, validation_run_id=validation.id, demo_report=report
    )
    await review_candidate_version(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        reviewer_user_id=reviewer_id,
        review_scope="ENGINEERING",
        decision="APPROVED",
        comment="Reviewed non-formal generated patch.",
    )
    await db_session.refresh(candidate)
    return candidate, version


async def test_materialization_requires_exact_reviewed_version_and_signed_callbacks(
    db_session, test_user
):
    reviewer, _token = test_user
    candidate, origin = await _approved_generated_version(db_session, reviewer.id)
    binding, request, reservation, attestation = await request_source_materialization(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        candidate_version_hash=origin.version_hash,
        actor_user_id=reviewer.id,
    )
    assert reservation is not None and reservation.should_dispatch is True
    assert reservation.attempt_number == 1 and attestation is None
    assert binding["artifact_id"] == "456"
    assert binding["candidate_module_path"].endswith(f"/{origin.candidate_key}.py")
    await record_materialization_dispatch(
        db_session,
        request_event=request,
        reservation=reservation,
        dispatched=True,
    )
    opened = datetime.now(timezone.utc) + timedelta(seconds=1)
    pr_payload = {
        "schema_version": "standard_astro_materialization_pr_v1",
        "attestation_id": str(request.id),
        "materialization_request_id": str(request.id),
        "candidate_id": str(candidate.id),
        "origin_candidate_version_id": str(origin.id),
        "origin_candidate_version_hash": origin.version_hash,
        "draft_run_id": binding["draft_run_id"],
        "artifact_repository": REPOSITORY,
        "artifact_workflow_run_id": "123",
        "artifact_id": "456",
        "artifact_name": binding["artifact_name"],
        "artifact_sha256": "d" * 64,
        "base_commit": "a" * 40,
        "base_source_tree_sha256": "4" * 64,
        "post_patch_source_tree_sha256": "5" * 64,
        "patch_sha256": "6" * 64,
        "candidate_module_path": binding["candidate_module_path"],
        "candidate_module_sha256": "e" * 64,
        "branch_name": binding["branch_name"],
        "pull_request_number": 42,
        "pull_request_state": "OPEN",
        "pull_request_url": f"https://github.com/{REPOSITORY}/pull/42",
        "pull_request_head_commit": "f" * 40,
        "pull_request_head_tree_sha256": "5" * 64,
        "github_repository": REPOSITORY,
        "github_workflow_ref": f"{REPOSITORY}/.github/workflows/{PR_WORKFLOW}@refs/heads/main",
        "github_workflow_sha": "1" * 40,
        "github_run_id": "999",
        "github_run_attempt": 1,
        "candidate_code_executed": False,
        "auto_merge_performed": False,
        "opened_at": opened.isoformat(),
    }
    tampered = _signed(pr_payload)
    tampered["payload"]["pull_request_number"] = 43
    with pytest.raises(FoundryCatalogError, match="hash does not match"):
        await record_materialization_pr_attestation(
            db_session,
            attestation_bundle=tampered,
            expected_repository=REPOSITORY,
            expected_workflow=PR_WORKFLOW,
            trusted_public_keys=KEYRING,
        )
    pr_attestation = await record_materialization_pr_attestation(
        db_session,
        attestation_bundle=_signed(pr_payload),
        expected_repository=REPOSITORY,
        expected_workflow=PR_WORKFLOW,
        trusted_public_keys=KEYRING,
    )
    assert pr_attestation.pull_request_number == 42

    final_binding, final_request, final_reservation, receipt = (
        await request_materialization_finalization(
            db_session,
            candidate_id=candidate.id,
            attestation_id=pr_attestation.id,
            actor_user_id=reviewer.id,
        )
    )
    assert final_reservation is not None and final_reservation.should_dispatch is True
    assert final_reservation.attempt_number == 1 and receipt is None
    await record_finalization_dispatch(
        db_session,
        request_event=final_request,
        reservation=final_reservation,
        dispatched=True,
    )
    finalized = opened + timedelta(seconds=2)
    new_image = "sha256:" + "2" * 64
    final_payload = {
        "schema_version": "standard_astro_materialization_final_v1",
        "receipt_id": str(pr_attestation.id),
        "materialization_attestation_id": str(pr_attestation.id),
        "candidate_id": str(candidate.id),
        "origin_candidate_version_id": str(origin.id),
        "origin_candidate_version_hash": origin.version_hash,
        "pull_request_number": 42,
        "pull_request_head_commit": "f" * 40,
        "pull_request_base_ref": "main",
        "pull_request_head_repository": REPOSITORY,
        "merge_commit": "3" * 40,
        "origin_main_commit": "a" * 40,
        "merge_commit_is_ancestor_of_origin_main": True,
        "merge_source_tree_sha256": "4" * 64,
        "candidate_module_path": final_binding["candidate_module_path"],
        "candidate_module_sha256": "e" * 64,
        "patch_sha256": "6" * 64,
        "dependency_lock_sha256": "7" * 64,
        "runner_definition_sha256": "8" * 64,
        "validation_sbom_sha256": "9" * 64,
        "validation_runner_image_digest": new_image,
        "github_repository": REPOSITORY,
        "github_workflow_ref": f"{REPOSITORY}/.github/workflows/{FINAL_WORKFLOW}@refs/heads/main",
        "github_workflow_sha": "a" * 40,
        "github_run_id": "1000",
        "github_run_attempt": 1,
        "source_was_merged": True,
        "candidate_code_executed": False,
        "validation_image_built_without_execution": True,
        "finalized_at": finalized.isoformat(),
    }
    for field, invalid_value in (
        ("pull_request_base_ref", "attacker-base"),
        ("pull_request_head_repository", "attacker/fork"),
        ("origin_main_commit", "b" * 40),
        ("merge_commit_is_ancestor_of_origin_main", False),
    ):
        invalid_payload = {**final_payload, field: invalid_value}
        with pytest.raises(
            FoundryCatalogError, match="Merged source/image identity is invalid"
        ):
            await record_materialization_final_receipt(
                db_session,
                attestation_bundle=_signed(invalid_payload),
                expected_repository=REPOSITORY,
                expected_workflow=FINAL_WORKFLOW,
                trusted_public_keys=KEYRING,
            )
    materialization, new_version = await record_materialization_final_receipt(
        db_session,
        attestation_bundle=_signed(final_payload),
        expected_repository=REPOSITORY,
        expected_workflow=FINAL_WORKFLOW,
        trusted_public_keys=KEYRING,
    )
    assert new_version.version_number == 2
    assert new_version.created_by_kind == "PROTECTED_MATERIALIZATION"
    assert new_version.validation_runner_image_digest == new_image
    assert new_version.validation_runner_image_digest != origin.validation_runner_image_digest
    assert candidate.status == "BUILDING"
    assert await db_session.scalar(
        select(FoundryDemoRun.id).where(
            FoundryDemoRun.candidate_version_id == new_version.id
        )
    ) is None
    assert await db_session.scalar(
        select(FoundryReview.id).where(
            FoundryReview.candidate_version_id == new_version.id
        )
    ) is None
    assert materialization.validation_sbom_hash == "9" * 64
    assert materialization.pull_request_base_ref == "main"
    assert materialization.pull_request_head_repository == REPOSITORY
    assert materialization.origin_main_commit == "a" * 40
    assert materialization.merge_commit_is_ancestor_of_origin_main is True
    validated_receipt = await _validate_formal_source_provenance(
        db_session,
        version=new_version,
        source_commit=materialization.merge_commit,
        source_tree_hash=materialization.merge_source_tree_hash,
        dependency_lock_hash=materialization.dependency_lock_hash,
    )
    assert validated_receipt is not None
    assert validated_receipt.id == materialization.id
    same_receipt, same_version = await record_materialization_final_receipt(
        db_session,
        attestation_bundle=_signed(final_payload),
        expected_repository=REPOSITORY,
        expected_workflow=FINAL_WORKFLOW,
        trusted_public_keys=KEYRING,
    )
    assert same_receipt.id == materialization.id
    assert same_version.id == new_version.id


def test_materialization_api_only_reports_confirmed_workflow_as_dispatched():
    assert _reservation_status("DISPATCH_RESERVED") == "DISPATCH_RESERVED"
    assert (
        _reservation_status("DISPATCH_OUTCOME_UNKNOWN")
        == "DISPATCH_OUTCOME_UNKNOWN"
    )
    assert _reservation_status("WORKFLOW_DISPATCHED") == "DISPATCHED"


async def test_materialization_api_reports_committed_reservation_before_dispatch(
    db_session, test_user, monkeypatch
):
    reviewer, _token = test_user
    candidate, origin = await _approved_generated_version(db_session, reviewer.id)
    _binding, _request, reservation, _attestation = (
        await request_source_materialization(
            db_session,
            candidate_id=candidate.id,
            candidate_version_id=origin.id,
            candidate_version_hash=origin.version_hash,
            actor_user_id=reviewer.id,
        )
    )
    assert reservation is not None and reservation.state == "DISPATCH_RESERVED"
    monkeypatch.setattr(settings, "foundry_source_materialization_enabled", True)
    calls = 0

    async def unexpected_dispatch(*_args, **_kwargs):
        nonlocal calls
        calls += 1

    monkeypatch.setattr(
        materialization_api, "dispatch_materialization_pr", unexpected_dispatch
    )
    response = await materialization_api.materialize_candidate_source(
        candidate.id,
        materialization_api.ExactCandidateVersion(
            candidate_version_id=origin.id,
            candidate_version_hash=origin.version_hash,
        ),
        db_session,
        reviewer,
    )
    assert response["status"] == "DISPATCH_RESERVED"
    assert response["dispatch_state"] == "DISPATCH_RESERVED"
    assert response["retry_after"] == reservation.retry_after.isoformat()
    assert calls == 0


async def test_materialization_api_keeps_unknown_network_result_pending(
    db_session, test_user, monkeypatch
):
    reviewer, _token = test_user
    candidate, origin = await _approved_generated_version(db_session, reviewer.id)
    monkeypatch.setattr(settings, "foundry_source_materialization_enabled", True)
    monkeypatch.setattr(
        settings, "foundry_materialization_dispatch_backend", "github_actions"
    )
    calls = 0

    async def unknown_dispatch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise FoundryMaterializationDispatchError(
            "materialization_dispatch_unavailable",
            retryable=True,
            outcome_unknown=True,
        )

    monkeypatch.setattr(
        materialization_api, "dispatch_materialization_pr", unknown_dispatch
    )
    payload = materialization_api.ExactCandidateVersion(
        candidate_version_id=origin.id,
        candidate_version_hash=origin.version_hash,
    )
    first = await materialization_api.materialize_candidate_source(
        candidate.id, payload, db_session, reviewer
    )
    assert first["status"] == "DISPATCH_OUTCOME_UNKNOWN"
    assert first["dispatch_state"] == "DISPATCH_OUTCOME_UNKNOWN"
    assert first["outcome_unknown"] is True
    assert calls == 1

    replay = await materialization_api.materialize_candidate_source(
        candidate.id, payload, db_session, reviewer
    )
    assert replay["status"] == "DISPATCH_OUTCOME_UNKNOWN"
    assert replay["idempotent_replay"] is True
    assert replay["retry_after"] == first["retry_after"]
    assert calls == 1


async def test_materialization_dispatch_contains_only_server_binding():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(204)

    config = FoundryMaterializationDispatchConfig(
        repository=REPOSITORY,
        token="t" * 32,
    )
    binding = {
        "schema_version": "standard_astro_materialization_request_v1",
        "artifact_id": "123",
        "candidate_id": str(uuid.uuid4()),
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await dispatch_materialization_pr(
            config, request_id=uuid.uuid4(), binding=binding, client=client
        )
    assert seen["ref"] == "main"
    assert set(seen["inputs"]) == {"request_id", "binding"}
    assert "code" not in seen["inputs"]
    assert "commit" not in seen["inputs"]


async def test_materialization_dispatch_timeout_marks_result_unknown():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("dispatch response timed out", request=request)

    config = FoundryMaterializationDispatchConfig(
        repository=REPOSITORY,
        token="t" * 32,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FoundryMaterializationDispatchError) as caught:
            await dispatch_materialization_pr(
                config,
                request_id=uuid.uuid4(),
                binding={"schema_version": "test"},
                client=client,
            )
    assert caught.value.code == "materialization_dispatch_unavailable"
    assert caught.value.retryable is True
    assert caught.value.outcome_unknown is True


@pytest.mark.parametrize(
    ("lane", "request_event_type"),
    (
        ("materialization", "SOURCE_MATERIALIZATION_REQUESTED"),
        ("finalization", "SOURCE_MATERIALIZATION_FINALIZATION_REQUESTED"),
    ),
)
async def test_unconfirmed_reservation_uses_short_dispatch_lease_for_both_lanes(
    db_session, test_user, monkeypatch, lane, request_event_type
):
    reviewer, _token = test_user
    candidate, origin = await _approved_generated_version(db_session, reviewer.id)
    dispatch_request_id = uuid.uuid4()
    request = await _append_event(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        event_type=request_event_type,
        actor_kind="HUMAN_REVIEWER",
        actor_user_id=reviewer.id,
        payload={"test_dispatch_request_id": str(dispatch_request_id)},
    )
    first = await materialization_module._reserve_dispatch_attempt(
        db_session,
        request_event=request,
        dispatch_request_id=dispatch_request_id,
        lane=lane,
    )
    await db_session.commit()
    assert first.should_dispatch is True
    assert first.state == "DISPATCH_RESERVED"

    active = await materialization_module._reserve_dispatch_attempt(
        db_session,
        request_event=request,
        dispatch_request_id=dispatch_request_id,
        lane=lane,
    )
    assert active.should_dispatch is False
    assert active.state == "DISPATCH_RESERVED"
    assert active.reservation_event_id == first.reservation_event_id
    assert active.retry_after == first.retry_after
    reservation_event = await db_session.get(
        FoundryCandidateEvent, first.reservation_event_id
    )
    assert reservation_event is not None
    assert (
        active.retry_after - materialization_module._as_utc(
            reservation_event.created_at
        )
    ) == timedelta(minutes=2)

    monkeypatch.setattr(
        materialization_module,
        "_utc_now",
        lambda: active.retry_after + timedelta(seconds=1),
    )
    second = await materialization_module._reserve_dispatch_attempt(
        db_session,
        request_event=request,
        dispatch_request_id=dispatch_request_id,
        lane=lane,
    )
    assert second.should_dispatch is True
    assert second.state == "DISPATCH_RESERVED"
    assert second.attempt_number == 2
    retry_event = await db_session.get(
        FoundryCandidateEvent, second.reservation_event_id
    )
    assert retry_event is not None
    assert retry_event.event_payload["retry_reason"] == "dispatch_lease_expired"


async def test_unknown_dispatch_outcome_waits_for_workflow_callback_timeout(
    db_session, test_user, monkeypatch
):
    reviewer, _token = test_user
    candidate, origin = await _approved_generated_version(db_session, reviewer.id)
    _binding, request, first, _attestation = await request_source_materialization(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        candidate_version_hash=origin.version_hash,
        actor_user_id=reviewer.id,
    )
    assert first is not None and first.should_dispatch is True
    outcome = await record_materialization_dispatch(
        db_session,
        request_event=request,
        reservation=first,
        dispatched=False,
        failure_class="materialization_dispatch_unavailable",
        retryable=True,
        outcome_unknown=True,
    )
    same_outcome = await record_materialization_dispatch(
        db_session,
        request_event=request,
        reservation=first,
        dispatched=False,
        failure_class="materialization_dispatch_unavailable",
        retryable=True,
        outcome_unknown=True,
    )
    assert same_outcome.id == outcome.id

    _binding, _request, waiting, _attestation = await request_source_materialization(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        candidate_version_hash=origin.version_hash,
        actor_user_id=reviewer.id,
    )
    assert waiting is not None and waiting.should_dispatch is False
    assert waiting.state == "DISPATCH_OUTCOME_UNKNOWN"
    assert waiting.attempt_number == 1
    assert waiting.retry_after == (
        materialization_module._as_utc(outcome.created_at) + timedelta(minutes=60)
    )

    monkeypatch.setattr(
        materialization_module,
        "_utc_now",
        lambda: waiting.retry_after + timedelta(seconds=1),
    )
    _binding, _request, second, _attestation = await request_source_materialization(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        candidate_version_hash=origin.version_hash,
        actor_user_id=reviewer.id,
    )
    assert second is not None and second.should_dispatch is True
    assert second.attempt_number == 2
    event = await db_session.get(FoundryCandidateEvent, second.reservation_event_id)
    assert event is not None
    assert event.event_payload["retry_reason"] == "dispatch_outcome_unknown_timeout"


@pytest.mark.parametrize(
    (
        "lane",
        "request_event_type",
        "legacy_success_type",
        "legacy_failure_type",
        "legacy_binding_field",
    ),
    (
        (
            "materialization",
            "SOURCE_MATERIALIZATION_REQUESTED",
            "SOURCE_MATERIALIZATION_DISPATCHED",
            "SOURCE_MATERIALIZATION_DISPATCH_FAILED",
            "materialization_request_id",
        ),
        (
            "finalization",
            "SOURCE_MATERIALIZATION_FINALIZATION_REQUESTED",
            "SOURCE_MATERIALIZATION_FINALIZATION_DISPATCHED",
            "SOURCE_MATERIALIZATION_FINALIZATION_DISPATCH_FAILED",
            "materialization_attestation_id",
        ),
    ),
)
async def test_legacy_compatibility_requires_exact_old_dispatched_event(
    db_session,
    test_user,
    lane,
    request_event_type,
    legacy_success_type,
    legacy_failure_type,
    legacy_binding_field,
):
    reviewer, _token = test_user

    ignored_candidate, ignored_origin = await _approved_generated_version(
        db_session, reviewer.id
    )
    ignored_request = await _append_event(
        db_session,
        candidate_id=ignored_candidate.id,
        candidate_version_id=ignored_origin.id,
        event_type=request_event_type,
        actor_kind="HUMAN_REVIEWER",
        actor_user_id=reviewer.id,
        payload={},
    )
    ignored_dispatch_id = (
        ignored_request.id if lane == "materialization" else uuid.uuid4()
    )
    legacy_failed = await _append_event(
        db_session,
        candidate_id=ignored_candidate.id,
        candidate_version_id=ignored_origin.id,
        event_type=legacy_failure_type,
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload={
            legacy_binding_field: str(ignored_dispatch_id),
            "failure_class": "legacy_dispatch_failed",
            "retryable": True,
        },
    )
    wrong_legacy_dispatched = await _append_event(
        db_session,
        candidate_id=ignored_candidate.id,
        candidate_version_id=ignored_origin.id,
        event_type=legacy_success_type,
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload={legacy_binding_field: str(uuid.uuid4())},
    )
    assert not materialization_module._successful_dispatch_binds(
        legacy_failed,
        request_event_id=ignored_request.id,
        dispatch_request_id=ignored_dispatch_id,
        lane=lane,
    )
    assert not materialization_module._successful_dispatch_binds(
        wrong_legacy_dispatched,
        request_event_id=ignored_request.id,
        dispatch_request_id=ignored_dispatch_id,
        lane=lane,
    )
    ignored = await materialization_module._reserve_dispatch_attempt(
        db_session,
        request_event=ignored_request,
        dispatch_request_id=ignored_dispatch_id,
        lane=lane,
    )
    await db_session.commit()
    assert ignored.should_dispatch is True
    assert ignored.state == "DISPATCH_RESERVED"
    assert ignored.attempt_number == 1

    legacy_candidate, legacy_origin = await _approved_generated_version(
        db_session, reviewer.id, gap_nonce=f"legacy-{lane}"
    )
    legacy_request = await _append_event(
        db_session,
        candidate_id=legacy_candidate.id,
        candidate_version_id=legacy_origin.id,
        event_type=request_event_type,
        actor_kind="HUMAN_REVIEWER",
        actor_user_id=reviewer.id,
        payload={},
    )
    legacy_dispatch_id = (
        legacy_request.id if lane == "materialization" else uuid.uuid4()
    )
    legacy_dispatched = await _append_event(
        db_session,
        candidate_id=legacy_candidate.id,
        candidate_version_id=legacy_origin.id,
        event_type=legacy_success_type,
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload={legacy_binding_field: str(legacy_dispatch_id)},
    )
    await db_session.commit()
    assert materialization_module._successful_dispatch_binds(
        legacy_dispatched,
        request_event_id=legacy_request.id,
        dispatch_request_id=legacy_dispatch_id,
        lane=lane,
    )
    active = await materialization_module._reserve_dispatch_attempt(
        db_session,
        request_event=legacy_request,
        dispatch_request_id=legacy_dispatch_id,
        lane=lane,
    )
    assert active.should_dispatch is False
    assert active.state == "WORKFLOW_DISPATCHED"
    assert active.reservation_event_id == legacy_dispatched.id
    assert active.attempt_number == 1
    reserved_type = materialization_module._DISPATCH_LANES[lane]["reserved"]
    reserved_count = len(
        list(
            (
                await db_session.execute(
                    select(FoundryCandidateEvent).where(
                        FoundryCandidateEvent.candidate_id == legacy_candidate.id,
                        FoundryCandidateEvent.candidate_version_id
                        == legacy_origin.id,
                        FoundryCandidateEvent.event_type == reserved_type,
                    )
                )
            ).scalars().all()
        )
    )
    assert reserved_count == 0


async def test_materialization_dispatch_retry_is_bounded_idempotent_and_audited(
    db_session, test_user, monkeypatch
):
    reviewer, _token = test_user
    candidate, origin = await _approved_generated_version(db_session, reviewer.id)
    _binding, request, first, attestation = await request_source_materialization(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        candidate_version_hash=origin.version_hash,
        actor_user_id=reviewer.id,
    )
    assert first is not None and first.should_dispatch is True
    assert first.attempt_number == 1 and attestation is None
    first_outcome = await record_materialization_dispatch(
        db_session,
        request_event=request,
        reservation=first,
        dispatched=True,
    )
    same_outcome = await record_materialization_dispatch(
        db_session,
        request_event=request,
        reservation=first,
        dispatched=True,
    )
    assert same_outcome.id == first_outcome.id

    _binding, same_request, active, _attestation = (
        await request_source_materialization(
            db_session,
            candidate_id=candidate.id,
            candidate_version_id=origin.id,
            candidate_version_hash=origin.version_hash,
            actor_user_id=reviewer.id,
        )
    )
    assert same_request.id == request.id
    assert active is not None and active.should_dispatch is False
    assert active.reservation_event_id == first.reservation_event_id

    monkeypatch.setattr(
        materialization_module,
        "_utc_now",
        lambda: active.retry_after + timedelta(seconds=1),
    )
    _binding, _request, second, _attestation = await request_source_materialization(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        candidate_version_hash=origin.version_hash,
        actor_user_id=reviewer.id,
    )
    assert second is not None and second.should_dispatch is True
    assert second.attempt_number == 2
    second_outcome = await record_materialization_dispatch(
        db_session,
        request_event=request,
        reservation=second,
        dispatched=True,
    )

    monkeypatch.setattr(
        materialization_module,
        "_utc_now",
        lambda: materialization_module.materialization_workflow_retry_after(
            second_outcome
        )
        + timedelta(seconds=1),
    )
    _binding, _request, third, _attestation = await request_source_materialization(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        candidate_version_hash=origin.version_hash,
        actor_user_id=reviewer.id,
    )
    assert third is not None and third.should_dispatch is True
    assert third.attempt_number == 3
    third_outcome = await record_materialization_dispatch(
        db_session,
        request_event=request,
        reservation=third,
        dispatched=True,
    )

    monkeypatch.setattr(
        materialization_module,
        "_utc_now",
        lambda: materialization_module.materialization_workflow_retry_after(
            third_outcome
        )
        + timedelta(seconds=1),
    )
    for _ in range(2):
        with pytest.raises(
            FoundryCatalogError, match="retry budget is exhausted"
        ):
            await request_source_materialization(
                db_session,
                candidate_id=candidate.id,
                candidate_version_id=origin.id,
                candidate_version_hash=origin.version_hash,
                actor_user_id=reviewer.id,
            )
    ledger = list(
        (
            await db_session.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate.id,
                    FoundryCandidateEvent.candidate_version_id == origin.id,
                    FoundryCandidateEvent.event_type.in_(
                        (
                            "SOURCE_MATERIALIZATION_DISPATCH_RESERVED",
                            "SOURCE_MATERIALIZATION_DISPATCHED",
                            "SOURCE_MATERIALIZATION_DISPATCH_EXHAUSTED",
                        )
                    ),
                )
            )
        ).scalars().all()
    )
    reservations = [
        row
        for row in ledger
        if row.event_type == "SOURCE_MATERIALIZATION_DISPATCH_RESERVED"
    ]
    assert [row.event_payload["attempt_number"] for row in reservations] == [
        1,
        2,
        3,
    ]
    assert len(
        [
            row
            for row in ledger
            if row.event_type == "SOURCE_MATERIALIZATION_DISPATCH_EXHAUSTED"
        ]
    ) == 1


async def test_retryable_dispatch_failure_reserves_next_attempt_immediately(
    db_session, test_user
):
    reviewer, _token = test_user
    candidate, origin = await _approved_generated_version(db_session, reviewer.id)
    _binding, request, first, _attestation = await request_source_materialization(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        candidate_version_hash=origin.version_hash,
        actor_user_id=reviewer.id,
    )
    assert first is not None
    await record_materialization_dispatch(
        db_session,
        request_event=request,
        reservation=first,
        dispatched=False,
        failure_class="materialization_dispatch_unavailable",
        retryable=True,
    )
    _binding, _request, second, _attestation = await request_source_materialization(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        candidate_version_hash=origin.version_hash,
        actor_user_id=reviewer.id,
    )
    assert second is not None and second.should_dispatch is True
    assert second.attempt_number == 2
    event = await db_session.get(FoundryCandidateEvent, second.reservation_event_id)
    assert event is not None
    assert event.event_payload["retry_reason"] == "retryable_dispatch_failure"
    _binding, _request, active, _attestation = await request_source_materialization(
        db_session,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        candidate_version_hash=origin.version_hash,
        actor_user_id=reviewer.id,
    )
    assert active is not None and active.should_dispatch is False
    assert active.state == "DISPATCH_RESERVED"
    assert active.attempt_number == 2
    assert active.reservation_event_id == second.reservation_event_id


def test_materialization_signer_uses_clean_runner_openssl_backend():
    seed = bytes(range(32))
    message = b"standard-astro-materialization-signing-smoke"
    signature = _sign_ed25519(seed, message)
    assert len(signature) == 64
    Ed25519PrivateKey.from_private_bytes(seed).public_key().verify(
        signature, message
    )
    root = Path(__file__).resolve().parents[2]
    signer = (
        root / "backend/scripts/sign_foundry_materialization_receipt.py"
    ).read_text(encoding="utf-8")
    assert "cryptography.hazmat" not in signer
    assert '"openssl",' in signer


def test_materialization_workflows_never_auto_merge_or_expose_private_key_to_render():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    pr = (root / ".github/workflows/foundry-materialize-candidate.yml").read_text()
    final = (root / ".github/workflows/foundry-finalize-materialization.yml").read_text()
    pr_prepare, pr_protected = pr.split("  sign-receipt:", 1)
    final_prepare, final_protected = final.split("  sign-receipt:", 1)
    pr_sign, pr_callback = pr_protected.split("  callback-only:", 1)
    final_sign, final_callback = final_protected.split("  callback-only:", 1)
    runbook = (
        root / "docs/runbooks/FOUNDRY_SOURCE_MATERIALIZATION.zh-CN.md"
    ).read_text(encoding="utf-8")
    assert "gh pr merge" not in pr
    assert "pull_request_target" not in pr + final
    assert "git add -A" not in pr
    assert 'git add -- "$CANDIDATE_MODULE_PATH"' in pr
    assert pr.count("FOUNDRY_MATERIALIZATION_ATTESTATION_PRIVATE_KEY") == 1
    assert final.count("FOUNDRY_MATERIALIZATION_ATTESTATION_PRIVATE_KEY") == 1
    assert "FOUNDRY_MATERIALIZATION_ATTESTATION_PRIVATE_KEY" not in pr_prepare
    assert "FOUNDRY_MATERIALIZATION_ATTESTATION_PRIVATE_KEY" not in final_prepare
    assert "FOUNDRY_MATERIALIZATION_ATTESTATION_PRIVATE_KEY" not in pr_callback
    assert "FOUNDRY_MATERIALIZATION_ATTESTATION_PRIVATE_KEY" not in final_callback
    assert pr.count("FOUNDRY_MATERIALIZATION_RESULT_SECRET") == 1
    assert final.count("FOUNDRY_MATERIALIZATION_RESULT_SECRET") == 1
    assert "FOUNDRY_MATERIALIZATION_RESULT_SECRET" not in pr_prepare
    assert "FOUNDRY_MATERIALIZATION_RESULT_SECRET" not in final_prepare
    assert "FOUNDRY_MATERIALIZATION_RESULT_SECRET" not in pr_sign
    assert "FOUNDRY_MATERIALIZATION_RESULT_SECRET" not in final_sign
    assert "FOUNDRY_MATERIALIZATION_RESULT_SECRET" in pr_callback
    assert "FOUNDRY_MATERIALIZATION_RESULT_SECRET" in final_callback
    assert "environment: foundry-materialization-pr" in pr_prepare
    assert "environment: foundry-materialization-build" in final_prepare
    assert "environment: foundry-materialization-attestation" in pr_sign
    assert "environment: foundry-materialization-attestation" in final_sign
    assert "command -v openssl" in pr_sign
    assert "command -v openssl" in final_sign
    assert "openssl version" in pr_sign
    assert "openssl version" in final_sign
    assert "environment: foundry-materialization-callback" in pr_callback
    assert "environment: foundry-materialization-callback" in final_callback
    for section in (
        pr_prepare,
        pr_sign,
        pr_callback,
        final_prepare,
        final_sign,
        final_callback,
    ):
        assert 'test "$GITHUB_REF" = "refs/heads/main"' in section
        assert section.index('test "$GITHUB_REF" = "refs/heads/main"') < (
            section.index("uses:")
        )
    assert "contents: write" not in pr_protected
    assert "packages: write" not in final_protected
    assert "FOUNDRY_MATERIALIZATION_RESULT_SECRET" in runbook
    assert "foundry-materialization-pr" in runbook
    assert "foundry-materialization-build" in runbook
    assert "foundry-materialization-attestation" in runbook
    assert "foundry-materialization-callback" in runbook
    assert "Selected branches and tags" in runbook
    assert "且只允许 `main`" in runbook
    assert "/actions/artifacts/${ARTIFACT_ID}" in pr
    assert "/actions/artifacts/${ARTIFACT_ID}" in final
    assert "FOUNDRY_MATERIALIZATION_ATTESTATION_PRIVATE_KEY" not in (
        root / "render.yaml"
    ).read_text()
    assert "FOUNDRY_MATERIALIZATION_ATTESTATION_PRIVATE_KEY" not in (
        root / "docker-compose.yml"
    ).read_text()
    assert "docker run" not in final
    assert "baseRefName" in final
    assert "headRepository" in final
    assert "refs/remotes/origin/main" in final
    assert "merge-base --is-ancestor" in final
    assert '--expected-repository "$EXPECTED_REPOSITORY"' in final
    assert '--protected-main-repo control-source' in final
    assert '--protected-main-commit "$GITHUB_SHA"' in final
    assert "validation_image_built_without_execution" in (
        root / "backend/scripts/prepare_foundry_materialization.py"
    ).read_text()
