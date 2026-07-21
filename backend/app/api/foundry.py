"""Owner-scoped Candidate Catalog and protected workflow-governance APIs."""

from __future__ import annotations

import hmac
import inspect
import os
import uuid
from datetime import timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin_any
from app.auth import decode_token, get_current_user, require_active_account, require_not_tombstoned
from app.config import settings
from app.models.claim_audit_records import ClaimAudit
from app.models.database import get_db
from app.models.foundry_records import (
    CapabilityRequest,
    FoundryCandidate,
    FoundryCandidateEvent,
    FoundryCandidateVersion,
    FoundryDemoRun,
    FoundryFormalBuildAttestation,
    FoundryReview,
    FoundryValidationRun,
    WorkflowRegistryEntry,
    WorkflowRegistryRelease,
    WorkflowRegistryReleaseImport,
)
from app.models.schemas import User
from app.services.foundry_catalog import (
    FoundryCatalogError,
    create_capability_request,
    merge_capability_request,
    queue_ai_draft,
    record_ai_draft_dispatch,
    record_ai_draft_result,
    record_formal_build_attempt_failure,
    record_formal_build_dispatch,
    review_candidate_version,
    request_formal_build_dispatch,
    serialize_candidate,
    serialize_capability_request,
    serialize_demo_run,
    triage_capability_request,
    record_demo_report,
    record_validation_workflow_failure,
    record_formal_build_attestation,
)
from app.services.foundry_validation_dispatch import (
    queue_and_dispatch_candidate_validation,
)
from app.services.foundry_draft_dispatch import (
    FoundryDraftDispatchError,
    dispatch_candidate_draft,
)
from app.services.foundry_ci_dispatch import (
    FoundryCIDispatchConfig,
    FoundryCIDispatchError,
    dispatch_formal_worker_build,
)


research_router = APIRouter(prefix="/api/research", tags=["workflow-foundry"])
admin_router = APIRouter(prefix="/api/admin/foundry", tags=["admin-workflow-foundry"])
internal_router = APIRouter(prefix="/api/internal/foundry", tags=["internal-workflow-foundry"])


class CapabilityRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_id: str = Field(pattern=r"^gap_[0-9a-f]{64}$")


class CandidateVersionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_bundle: dict[str, Any]
    validation_runner_image_digest: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    code_tree_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    patch_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sbom_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class AIDraftCandidateVersion(BaseModel):
    """Content returned by AI, with all executable validation still pending."""

    model_config = ConfigDict(extra="forbid")

    candidate_bundle: dict[str, Any]
    validation_runner_image_digest: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    code_tree_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sbom_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AIDraftProviderReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[1]
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=255)
    request_id_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    prompt_or_user_data_stored: Literal[False]
    generated_code_executed: Literal[False]
    tests_executed: Literal[False]


class AIDraftArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    kind: Literal[
        "CANDIDATE_BUNDLE", "PATCH", "SBOM", "PROVIDER_RECEIPT"
    ]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0, le=2 * 1024 * 1024)


class AIDraftArtifactReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(
        pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
    )
    workflow_run_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    artifact_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    artifact_name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AIDraftSourceReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hash_algorithm: Literal["standard_astro_tracked_source_manifest_v1"]
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    base_source_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    post_patch_source_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_applied: bool
    changed_paths: list[str] = Field(max_length=64)
    dependency_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runner_definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AIDraftResultReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    draft_run_id: uuid.UUID
    candidate_id: uuid.UUID
    gap_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    gap_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    gap_descriptor: dict[str, Any]
    generation_route: Literal["COMPOSITION", "DATA_ADAPTER", "SCIENCE_CODE"]
    risk_level: Literal["R0", "R1", "R2", "R3"]
    status: Literal["SUCCEEDED", "FAILED"]
    candidate_version: AIDraftCandidateVersion | None
    provider_receipt: AIDraftProviderReceipt
    artifact_manifest: list[AIDraftArtifact] = Field(max_length=16)
    artifact_receipt: AIDraftArtifactReceipt | None
    source_receipt: AIDraftSourceReceipt | None
    failure_class: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{1,127}$"
    )
    draft_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TriageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_route: Literal["COMPOSITION", "DATA_ADAPTER", "SCIENCE_CODE"]
    risk_level: Literal["R0", "R1", "R2", "R3"]
    candidate_version: CandidateVersionDraft | None = None


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_candidate_id: uuid.UUID


class CandidateVersionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_version_id: uuid.UUID
    candidate_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ValidationWorkflowFailureReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    validation_run_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_version_id: uuid.UUID
    candidate_version_number: int = Field(ge=1)
    candidate_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["FAILED"]
    failure_class: Literal["validation_workflow_failed"]
    failed_stage: Literal["isolated_demo"]
    workflow_conclusion: Literal["failure", "cancelled", "skipped"]
    github_repository: str = Field(
        pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
    )
    github_workflow_ref: str = Field(min_length=1, max_length=512)
    github_workflow_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    github_run_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    github_run_attempt: Literal["1"]


class FoundryReviewCreate(CandidateVersionBinding):
    review_scope: Literal["ENGINEERING", "SCIENTIFIC"]
    decision: Literal["APPROVED", "REJECTED", "CHANGES_REQUESTED"]
    comment: str = Field(default="", max_length=4000)


class FoundryRegisterRequest(CandidateVersionBinding):
    build_attestation_id: uuid.UUID


class FormalBuildSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = Field(pattern=r"^ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+/science-worker$")
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class FormalBuildAttemptFailureReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    formal_build_attempt_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_version_id: uuid.UUID
    candidate_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["FAILED"]
    failure_class: Literal["formal_build_workflow_failed"]
    failed_stage: Literal[
        "definition_gate", "validation", "image_build", "signing"
    ]
    workflow_conclusion: Literal["failure", "cancelled", "skipped"]
    github_repository: str = Field(
        pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
    )
    github_workflow_ref: str = Field(min_length=1, max_length=512)
    github_workflow_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    github_run_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    github_run_attempt: Literal["1"]


class FormalBuildIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    github_repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    github_workflow_ref: str = Field(min_length=1, max_length=2048)
    github_workflow_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    github_run_id: str = Field(pattern=r"^[1-9][0-9]*$")
    github_run_attempt: Literal[1]


class FormalBuildSigstoreReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oidc_issuer: Literal["https://token.actions.githubusercontent.com"]
    certificate_identity: str = Field(min_length=1, max_length=2048)
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FormalReleaseAuditReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["standard_astro_formal_release_audit_v1"]
    status: Literal["PASSED"]
    policy_id: str = Field(min_length=1, max_length=255)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dependency_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formal_sbom_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    architectures: list[str]
    receipts: dict[str, str]
    gates: dict[str, bool]
    advisory_database_checked: Literal[False]
    vulnerability_status: Literal["NOT_EVALUATED"]
    legal_review_complete: Literal[False]
    aggregate_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FormalBuildAttestationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["standard_astro_formal_build_attestation_v2"]
    attestation_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_version_id: uuid.UUID
    candidate_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    dependency_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formal_sbom_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_audit: FormalReleaseAuditReceipt
    tests_passed: Literal[True]
    subject: FormalBuildSubject
    build_identity: FormalBuildIdentity
    sigstore: FormalBuildSigstoreReceipt
    provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_method: Literal["github_oidc_cosign_plus_ed25519_callback_v2"]
    build_metadata: dict[str, Any]
    built_at: str


class FormalBuildSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["ed25519"]
    key_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    value: str = Field(min_length=1, max_length=256)


class FormalBuildAttestationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["standard_astro_formal_build_attestation_bundle_v2"]
    payload: FormalBuildAttestationPayload
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: FormalBuildSignature
    attestation_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RegistryReleaseImportReceipt(BaseModel):
    """Exact protected-signer callback; extra metadata is rejected."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["standard_astro_registry_release_import_v1"]
    release_request_id: uuid.UUID
    release_request_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_registry_epoch: str = Field(min_length=1, max_length=128)
    base_registry_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    registry_epoch: str = Field(min_length=1, max_length=128)
    registry_snapshot_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    signing_key_id: str = Field(min_length=1, max_length=255, pattern=r"^\S+$")
    signing_public_key_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    complete_entry_count: int = Field(ge=1, le=10000)
    generated_at: str = Field(min_length=1, max_length=64)
    import_mode: Literal["protected_offline_registry_release"]
    signed_snapshot: dict[str, Any]
    receipt_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class FoundryStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=4000)


def _require_flag(enabled: bool, label: str) -> None:
    if not enabled:
        raise HTTPException(status_code=404, detail=f"{label} is not enabled")


def _raise_foundry_error(exc: FoundryCatalogError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error_class": exc.error_class, "message": str(exc)},
    ) from exc


def _validation_api_status(row: FoundryValidationRun) -> str:
    return {
        "QUEUED": "DISPATCH_PENDING",
        "OUTCOME_UNKNOWN": "DISPATCH_UNCERTAIN",
    }.get(row.status, row.status)


def _validation_retryable(row: FoundryValidationRun) -> bool:
    return bool((row.validation_summary or {}).get("retryable"))


async def _admin_actor(request: Request, db: AsyncSession) -> tuple[str, uuid.UUID | None]:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return "AI_SERVICE", None
    try:
        user_id = decode_token(auth_header[7:])
        user = await db.get(User, user_id)
        if user is None:
            return "AI_SERVICE", None
        user = await require_not_tombstoned(require_active_account(user), db)
        return "HUMAN_ADMIN", user.id
    except HTTPException:
        return "AI_SERVICE", None


def _human_reviewer_usernames() -> frozenset[str]:
    values = [
        os.getenv("FOUNDRY_HUMAN_REVIEWER_USERNAMES", ""),
        os.getenv("SCIENTIFIC_REVIEWER_USERNAMES", ""),
    ]
    return frozenset(
        username.strip()
        for value in values
        for username in value.split(",")
        if username.strip()
    )


async def require_foundry_human_reviewer(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Require an explicitly allowlisted human JWT; admin/service secrets fail."""

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="A human reviewer JWT is required")
    try:
        user_id = decode_token(auth_header[7:])
        user = await db.get(User, user_id)
        user = await require_not_tombstoned(require_active_account(user), db)
    except HTTPException as exc:
        raise HTTPException(status_code=403, detail="A human reviewer JWT is required") from exc
    if user.username not in _human_reviewer_usernames():
        raise HTTPException(
            status_code=403,
            detail="This account is not an allowlisted human Foundry reviewer",
        )
    return user


def _require_validation_runner(request: Request) -> None:
    authorization = request.headers.get("Authorization", "")
    expected = settings.foundry_validation_result_secret
    provided = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not expected or not provided or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Validation runner access required")


def _require_draft_result_callback(request: Request) -> None:
    """Authenticate the host ingestion step, never the AI provider process."""

    authorization = request.headers.get("Authorization", "")
    expected = settings.foundry_draft_result_secret
    provided = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not expected or not provided or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="AI Draft callback access required")


def _require_formal_build_callback(request: Request) -> None:
    authorization = request.headers.get("Authorization", "")
    expected = settings.foundry_formal_build_result_secret
    provided = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not expected or not provided or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Formal build callback access required")


def _require_formal_build_failure_callback(request: Request) -> None:
    """Authenticate the failure-only job, which cannot submit attestations."""

    authorization = request.headers.get("Authorization", "")
    expected = settings.foundry_formal_build_failure_result_secret
    provided = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not expected or not provided or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=403,
            detail="Formal build failure callback access required",
        )


def _require_registry_import_callback(request: Request) -> None:
    authorization = request.headers.get("Authorization", "")
    expected = settings.foundry_registry_import_result_secret
    provided = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not expected or not provided or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=403,
            detail="Registry release import callback access required",
        )


def _require_registry_export_callback(request: Request) -> None:
    authorization = request.headers.get("Authorization", "")
    expected = settings.foundry_registry_export_secret
    provided = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not expected or not provided or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=403,
            detail="Registry release export access required",
        )


async def _dispatch_pending_registry_release(
    *,
    db: AsyncSession,
    candidate_id: uuid.UUID,
    candidate_version_id: uuid.UUID,
    release: WorkflowRegistryRelease,
) -> dict[str, Any]:
    """Dispatch after DB commit; a failed dispatch remains visibly pending."""

    if settings.foundry_registry_dispatch_backend == "disabled":
        return {"status": "DISABLED", "retryable": True}
    from app.services.foundry_catalog import (
        record_registry_release_dispatch,
        registry_release_dispatch_succeeded,
    )
    from app.services.foundry_registry_dispatch import (
        FoundryRegistryDispatchError,
        dispatch_registry_release,
    )

    if await registry_release_dispatch_succeeded(
        db,
        candidate_id=candidate_id,
        release_request_id=release.id,
        release_request_hash=release.manifest_hash,
    ):
        return {"status": "DISPATCHED", "idempotent_replay": True}
    try:
        await dispatch_registry_release(
            release_request_id=release.id,
            release_request_hash=release.manifest_hash,
        )
    except FoundryRegistryDispatchError as exc:
        try:
            await record_registry_release_dispatch(
                db,
                candidate_id=candidate_id,
                candidate_version_id=candidate_version_id,
                release_request_id=release.id,
                release_request_hash=release.manifest_hash,
                dispatched=False,
                failure_class=exc.failure_class,
                retryable=exc.retryable,
            )
        except FoundryCatalogError as ledger_exc:
            _raise_foundry_error(ledger_exc)
        raise HTTPException(
            status_code=503 if exc.retryable else 409,
            detail={
                "error_class": exc.failure_class,
                "message": "Registry release dispatch failed; request remains pending",
                "release_request_id": str(release.id),
                "release_request_hash": release.manifest_hash,
                "retryable": exc.retryable,
            },
        ) from exc
    try:
        _event, created = await record_registry_release_dispatch(
            db,
            candidate_id=candidate_id,
            candidate_version_id=candidate_version_id,
            release_request_id=release.id,
            release_request_hash=release.manifest_hash,
            dispatched=True,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    return {"status": "DISPATCHED", "idempotent_replay": not created}


def _compatibility(workflow: dict[str, Any]) -> dict[str, list[str]]:
    raw = workflow.get("compatibility")
    if isinstance(raw, dict):
        return {
            key: [str(value) for value in raw.get(key) or []]
            for key in (
                "source_profile_keys",
                "candidate_types",
                "model_scopes",
                "data_scopes",
            )
        }
    if workflow.get("workflow_id") == "union3_flat_lcdm_sn_only_v1":
        return {
            "source_profile_keys": ["union3_arxiv_v1"],
            "candidate_types": ["parameter_interval_report"],
            "model_scopes": ["flat_lcdm"],
            "data_scopes": ["union3_sn_only"],
        }
    return {
        "source_profile_keys": [str(value) for value in workflow.get("source_profiles") or []],
        "candidate_types": [str(value) for value in workflow.get("claim_types") or []],
        "model_scopes": [str(value) for value in workflow.get("model_scopes") or []],
        "data_scopes": [str(value) for value in workflow.get("dataset_keys") or []],
    }


def _normalize_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_id": str(workflow.get("workflow_id") or ""),
        "workflow_version": str(workflow.get("workflow_version") or ""),
        "display_name": workflow.get("display_name"),
        "summary": workflow.get("summary"),
        "status": str(workflow.get("status") or workflow.get("state") or "REGISTERED"),
        "risk_level": workflow.get("risk_level"),
        "claim_scope": workflow.get("claim_scope"),
        "model": workflow.get("model"),
        "dataset_key": workflow.get("dataset_key"),
        "dataset_keys": list(workflow.get("dataset_keys") or []),
        "execution_backend": workflow.get("execution_backend"),
        "registry_epoch": workflow.get("registry_epoch"),
        "registry_entry_hash": workflow.get("registry_entry_hash"),
        "compatibility": _compatibility(workflow),
    }


async def _formal_workflows() -> list[dict[str, Any]]:
    try:
        from app.services.workflow_registry_v2 import list_formal_workflows
    except (ImportError, AttributeError):
        list_formal_workflows = None
    if list_formal_workflows is not None:
        rows = list_formal_workflows()
        if inspect.isawaitable(rows):
            rows = await rows
        if not isinstance(rows, (list, tuple)):
            raise HTTPException(status_code=503, detail="Formal workflow registry is unavailable")
        return [_normalize_workflow(dict(item)) for item in rows]

    from app.services.registered_workflows import (
        get_registered_workflow,
        list_registered_workflows,
    )

    return [
        _normalize_workflow(get_registered_workflow(workflow_id))
        for workflow_id in list_registered_workflows()
    ]


async def _owned_candidate(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    candidate_id: uuid.UUID,
) -> FoundryCandidate:
    candidate = await db.scalar(
        select(FoundryCandidate)
        .join(CapabilityRequest, CapabilityRequest.candidate_id == FoundryCandidate.id)
        .where(
            FoundryCandidate.id == candidate_id,
            CapabilityRequest.user_id == user_id,
        )
        .limit(1)
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Foundry candidate not found")
    return candidate


@research_router.get("/workflows")
async def list_research_workflows(
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_flag(settings.workflow_registry_v2_enabled, "Workflow Registry v2")
    items = await _formal_workflows()
    return {"items": items, "total": len(items)}


@research_router.post(
    "/claim-audits/{audit_id}/capability-requests",
    status_code=status.HTTP_201_CREATED,
)
async def post_capability_request(
    audit_id: uuid.UUID,
    payload: CapabilityRequestCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_flag(settings.foundry_gap_tracking_enabled, "Foundry gap tracking")
    audit = await db.scalar(
        select(ClaimAudit).where(ClaimAudit.id == audit_id, ClaimAudit.user_id == user.id)
    )
    if audit is None:
        raise HTTPException(status_code=404, detail="Claim Audit not found")
    try:
        row = await create_capability_request(
            db, user_id=user.id, audit=audit, gap_id=payload.gap_id
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    return serialize_capability_request(row)


@research_router.get("/capability-requests")
async def list_capability_requests(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_flag(settings.foundry_gap_tracking_enabled, "Foundry gap tracking")
    total = int(
        await db.scalar(
            select(func.count()).select_from(CapabilityRequest).where(
                CapabilityRequest.user_id == user.id
            )
        )
        or 0
    )
    rows = list(
        (
            await db.execute(
                select(CapabilityRequest)
                .where(CapabilityRequest.user_id == user.id)
                .order_by(CapabilityRequest.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [serialize_capability_request(row) for row in rows], "total": total}


@research_router.get("/capability-requests/{request_id}")
async def get_capability_request(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_flag(settings.foundry_gap_tracking_enabled, "Foundry gap tracking")
    row = await db.scalar(
        select(CapabilityRequest).where(
            CapabilityRequest.id == request_id,
            CapabilityRequest.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Capability request not found")
    candidate = await db.get(FoundryCandidate, row.candidate_id) if row.candidate_id else None
    return serialize_capability_request(row, candidate=candidate)


@research_router.get("/foundry-candidates/{candidate_id}")
async def get_foundry_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_flag(settings.foundry_candidate_catalog_enabled, "Foundry Candidate Catalog")
    candidate = await _owned_candidate(db, user_id=user.id, candidate_id=candidate_id)
    return await serialize_candidate(db, candidate)


@research_router.get("/foundry-candidates/{candidate_id}/demo-runs")
async def get_foundry_demo_runs(
    candidate_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _require_flag(settings.foundry_candidate_catalog_enabled, "Foundry Candidate Catalog")
    await _owned_candidate(db, user_id=user.id, candidate_id=candidate_id)
    total = int(
        await db.scalar(
            select(func.count()).select_from(FoundryDemoRun).where(
                FoundryDemoRun.candidate_id == candidate_id
            )
        )
        or 0
    )
    demos = list(
        (
            await db.execute(
                select(FoundryDemoRun)
                .where(FoundryDemoRun.candidate_id == candidate_id)
                .order_by(FoundryDemoRun.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    version_ids = [row.candidate_version_id for row in demos]
    versions = (
        list(
            (
                await db.execute(
                    select(FoundryCandidateVersion).where(
                        FoundryCandidateVersion.id.in_(version_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if version_ids
        else []
    )
    number_by_id = {row.id: row.version_number for row in versions}
    return {
        "items": [
            serialize_demo_run(row, version_number=number_by_id.get(row.candidate_version_id))
            for row in demos
        ],
        "total": total,
    }


@admin_router.get("/requests")
async def admin_list_foundry_requests(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    _require_flag(settings.foundry_candidate_catalog_enabled, "Foundry Candidate Catalog")
    total = int(await db.scalar(select(func.count()).select_from(CapabilityRequest)) or 0)
    rows = list(
        (
            await db.execute(
                select(CapabilityRequest)
                .order_by(CapabilityRequest.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [serialize_capability_request(row) for row in rows], "total": total}


@admin_router.get("/registry")
async def admin_get_foundry_registry(
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    """Return a reviewer-safe view of runtime and control-plane Registry state."""

    _require_flag(settings.foundry_candidate_catalog_enabled, "Foundry Candidate Catalog")
    try:
        from app.services.workflow_registry_v2 import (
            active_registry_identity,
            list_formal_workflows,
        )

        runtime_identity = active_registry_identity()
        runtime_entries = [
            _normalize_workflow(dict(item))
            for item in list_formal_workflows(include_inactive=True)
        ]
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Formal workflow registry is unavailable",
        ) from exc

    control_entries = list(
        (
            await db.execute(
                select(WorkflowRegistryEntry)
                .order_by(
                    WorkflowRegistryEntry.registered_at.desc(),
                    WorkflowRegistryEntry.id.desc(),
                )
                .limit(1000)
            )
        )
        .scalars()
        .all()
    )
    releases = list(
        (
            await db.execute(
                select(WorkflowRegistryRelease)
                .order_by(
                    WorkflowRegistryRelease.created_at.desc(),
                    WorkflowRegistryRelease.id.desc(),
                )
                .limit(1000)
            )
        )
        .scalars()
        .all()
    )
    request_ids = [row.id for row in releases]
    imports = (
        list(
            (
                await db.execute(
                    select(WorkflowRegistryReleaseImport).where(
                        WorkflowRegistryReleaseImport.release_request_id.in_(
                            request_ids
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        if request_ids
        else []
    )
    import_by_request = {row.release_request_id: row for row in imports}

    return {
        "runtime": {
            "registry_epoch": runtime_identity["registry_epoch"],
            "registry_hash": runtime_identity["registry_hash"],
            "release_kind": runtime_identity["release_kind"],
            "signing_key_id": runtime_identity["signature_key_id"],
            "entries": runtime_entries,
        },
        "pending_entries": [
            {
                "id": str(row.id),
                "workflow_id": row.workflow_id,
                "workflow_version": row.workflow_version,
                "status": row.status,
                "risk_level": row.risk_level,
                "candidate_id": str(row.candidate_id),
                "candidate_version_id": str(row.candidate_version_id),
                "candidate_version_hash": row.candidate_version_hash,
                "registry_entry_hash": row.registry_entry_hash,
                "worker_image_digest": row.worker_image_digest,
                "registered_at": (
                    row.registered_at.isoformat() if row.registered_at else None
                ),
                "suspended_at": (
                    row.suspended_at.isoformat() if row.suspended_at else None
                ),
                "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
                "status_reason": row.status_reason,
            }
            for row in control_entries
        ],
        "releases": [
            _safe_registry_release_summary(
                row,
                signed_import=import_by_request.get(row.id),
            )
            for row in releases
        ],
    }


def _safe_registry_release_summary(
    row: WorkflowRegistryRelease,
    *,
    signed_import: WorkflowRegistryReleaseImport | None,
) -> dict[str, Any]:
    """Serialize hashes/status only; never release content or signatures."""

    imported = (
        {
            "registry_epoch": signed_import.registry_epoch,
            "registry_snapshot_hash": signed_import.registry_snapshot_hash,
            "signing_key_id": signed_import.signing_key_id,
            "signing_public_key_fingerprint": (
                signed_import.signing_public_key_fingerprint
            ),
            "status": signed_import.status,
            "runtime_registry_modified": False,
            "imported_at": (
                signed_import.imported_at.isoformat()
                if signed_import.imported_at
                else None
            ),
        }
        if signed_import is not None
        else None
    )
    return {
        "id": str(row.id),
        "epoch": row.epoch,
        "status": signed_import.status if signed_import is not None else row.status,
        "manifest_hash": row.manifest_hash,
        "key_id": row.key_id,
        "public_key_fingerprint": row.public_key_fingerprint,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "signed_import": imported,
    }


@admin_router.post("/requests/{request_id}/triage")
async def admin_triage_foundry_request(
    request_id: uuid.UUID,
    payload: TriageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    _require_flag(settings.foundry_candidate_catalog_enabled, "Foundry Candidate Catalog")
    actor_kind, actor_user_id = await _admin_actor(request, db)
    try:
        row, candidate, version = await triage_capability_request(
            db,
            request_id=request_id,
            generation_route=payload.generation_route,
            risk_level=payload.risk_level,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
            draft=(payload.candidate_version.model_dump(exclude_none=True) if payload.candidate_version else None),
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    response = serialize_capability_request(row, candidate=candidate)
    response["candidate_version"] = (
        {
            "id": str(version.id),
            "version_number": version.version_number,
            "version_hash": version.version_hash,
        }
        if version
        else None
    )
    response["draft_run"] = None
    if version is None and settings.foundry_ai_drafting_enabled:
        try:
            draft_event, created = await queue_ai_draft(
                db,
                candidate_id=candidate.id,
                actor_kind=actor_kind,
                actor_user_id=actor_user_id,
            )
        except FoundryCatalogError as exc:
            _raise_foundry_error(exc)
        draft_status = "ALREADY_ACTIVE"
        failure_class = None
        retryable = False
        if created:
            try:
                await dispatch_candidate_draft(
                    draft_run_id=draft_event.id,
                    candidate_id=candidate.id,
                    gap_fingerprint=candidate.gap_fingerprint,
                    gap_code=candidate.gap_code,
                    gap_descriptor=dict(candidate.gap_descriptor or {}),
                    generation_route=str(candidate.generation_route),
                    risk_level=str(candidate.risk_level),
                )
                dispatched = True
                draft_status = "DISPATCHED"
            except FoundryDraftDispatchError as exc:
                dispatched = False
                failure_class = exc.failure_class
                retryable = exc.retryable
                draft_status = "DISPATCH_FAILED"
            except Exception:
                dispatched = False
                failure_class = "draft_dispatch_internal_error"
                retryable = True
                draft_status = "DISPATCH_FAILED"
            try:
                await record_ai_draft_dispatch(
                    db,
                    draft_run_id=draft_event.id,
                    dispatched=dispatched,
                    failure_class=failure_class,
                    retryable=retryable,
                )
            except FoundryCatalogError as exc:
                _raise_foundry_error(exc)
        response["draft_run"] = {
            "draft_run_id": str(draft_event.id),
            "status": draft_status,
            "retryable": retryable,
            "failure_class": failure_class,
        }
    return response


@admin_router.post("/requests/{request_id}/merge")
async def admin_merge_foundry_request(
    request_id: uuid.UUID,
    payload: MergeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    _require_flag(settings.foundry_candidate_catalog_enabled, "Foundry Candidate Catalog")
    actor_kind, actor_user_id = await _admin_actor(request, db)
    try:
        row, candidate = await merge_capability_request(
            db,
            request_id=request_id,
            target_candidate_id=payload.target_candidate_id,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    return serialize_capability_request(row, candidate=candidate)


@admin_router.get("/candidates/{candidate_id}")
async def admin_get_foundry_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    _require_flag(settings.foundry_candidate_catalog_enabled, "Foundry Candidate Catalog")
    candidate = await db.get(FoundryCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Foundry candidate not found")
    payload = await serialize_candidate(db, candidate, demo_limit=100)
    candidate_versions = list(
        (
            await db.execute(
                select(FoundryCandidateVersion)
                .where(FoundryCandidateVersion.candidate_id == candidate.id)
                .order_by(FoundryCandidateVersion.version_number.desc())
            )
        )
        .scalars()
        .all()
    )
    payload["versions"] = [
        {
            "id": str(row.id),
            "candidate_key": row.candidate_key,
            "version_number": row.version_number,
            "version_hash": row.version_hash,
            "workflow_id": row.workflow_id,
            "workflow_version": row.workflow_version,
            "candidate_bundle": dict(row.candidate_bundle or {}),
            "workflow_spec": dict(row.workflow_spec or {}),
            "workflow_spec_hash": row.workflow_spec_hash,
            "code_tree_sha256": row.code_tree_hash,
            "patch_sha256": row.patch_hash,
            "dependency_lock_sha256": row.dependency_lock_hash,
            "sbom_sha256": row.sbom_hash,
            "validation_runner_image_digest": row.validation_runner_image_digest,
            "ai_model": row.ai_model,
            "ai_generation_config": dict(row.ai_generation_config or {}),
            "created_by_kind": row.created_by_kind,
            "created_at": row.created_at.isoformat(),
        }
        for row in candidate_versions
    ]
    reviews = list(
        (
            await db.execute(
                select(FoundryReview)
                .where(FoundryReview.candidate_id == candidate.id)
                .order_by(FoundryReview.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    validations = list(
        (
            await db.execute(
                select(FoundryValidationRun)
                .where(FoundryValidationRun.candidate_id == candidate.id)
                .order_by(FoundryValidationRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    build_attestations = list(
        (
            await db.execute(
                select(FoundryFormalBuildAttestation)
                .where(FoundryFormalBuildAttestation.candidate_id == candidate.id)
                .order_by(FoundryFormalBuildAttestation.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent)
                .where(FoundryCandidateEvent.candidate_id == candidate.id)
                .order_by(FoundryCandidateEvent.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    payload["reviews"] = [
        {
            "id": str(row.id),
            "candidate_version_id": str(row.candidate_version_id),
            "candidate_version_hash": row.candidate_version_hash,
            "reviewer_pseudonym": row.reviewer_pseudonym,
            "review_scope": row.review_scope,
            "decision": row.decision,
            "comment": row.comment,
            "created_at": row.created_at.isoformat(),
        }
        for row in reviews
    ]
    payload["validation_runs"] = [
        {
            "validation_run_id": str(row.id),
            "candidate_version_id": str(row.candidate_version_id),
            "candidate_version_hash": row.candidate_version_hash,
            "status": _validation_api_status(row),
            "validation_summary": dict(row.validation_summary or {}),
            "failure_class": row.failure_class,
            "retryable": _validation_retryable(row),
            "retry_after": (row.validation_summary or {}).get("retry_after"),
            "created_at": row.created_at.isoformat(),
        }
        for row in validations
    ]
    payload["formal_build_attestations"] = [
        {
            "build_attestation_id": str(row.id),
            "candidate_version_id": str(row.candidate_version_id),
            "candidate_version_hash": row.candidate_version_hash,
            "formal_worker_image_digest": row.formal_worker_image_digest,
            "source_tree_sha256": row.source_tree_hash,
            "dependency_lock_sha256": row.dependency_lock_hash,
            "formal_sbom_sha256": row.formal_sbom_hash,
            "test_report_sha256": row.test_report_hash,
            "formal_release_audit_sha256": row.formal_release_audit_hash,
            "formal_release_audit_receipts": dict(
                row.formal_release_audit_receipts or {}
            ),
            "git_commit": row.git_commit,
            "github_repository": row.github_repository,
            "github_workflow_ref": row.github_workflow_ref,
            "github_workflow_sha": row.github_workflow_sha,
            "oidc_issuer": row.oidc_issuer,
            "oidc_subject": row.oidc_subject,
            "attestation_signing_key_id": row.attestation_signing_key_id,
            "sigstore_bundle_sha256": row.sigstore_bundle_hash,
            "sigstore_verification_record_sha256": (
                row.sigstore_verification_record_hash
            ),
            "provenance_sha256": row.provenance_hash,
            "receipt_sha256": row.receipt_hash,
            "attestation_artifact_sha256": row.attestation_artifact_hash,
            "built_at": row.built_at.isoformat(),
            "created_at": row.created_at.isoformat(),
            "status": "VERIFIED_BUILD_RECEIPT",
        }
        for row in build_attestations
    ]
    payload["events"] = [
        {
            "id": str(row.id),
            "event_type": row.event_type,
            "actor_kind": row.actor_kind,
            "event_hash": row.event_hash,
            "previous_event_hash": row.previous_event_hash,
            "payload": dict(row.event_payload or {}),
            "created_at": row.created_at.isoformat(),
        }
        for row in events
    ]
    return payload


@admin_router.post("/candidates/{candidate_id}/validate", status_code=status.HTTP_202_ACCEPTED)
async def admin_validate_foundry_candidate(
    candidate_id: uuid.UUID,
    payload: CandidateVersionBinding,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    _require_flag(settings.foundry_auto_demo_enabled, "Foundry automatic Demo")
    actor_kind, actor_user_id = await _admin_actor(request, db)
    try:
        row = await queue_and_dispatch_candidate_validation(
            db,
            candidate_id=candidate_id,
            candidate_version_id=payload.candidate_version_id,
            candidate_version_hash=payload.candidate_version_hash,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    return {
        "validation_run_id": str(row.id),
        "status": _validation_api_status(row),
        "candidate_id": str(row.candidate_id),
        "candidate_version_id": str(row.candidate_version_id),
        "candidate_version_hash": row.candidate_version_hash,
        "retryable": _validation_retryable(row),
        "retry_after": (row.validation_summary or {}).get("retry_after"),
        "failure_class": row.failure_class,
        "created_at": row.created_at.isoformat(),
    }


@internal_router.post("/validation-runs/{validation_run_id}/demo-report")
async def ingest_foundry_demo_report(
    validation_run_id: uuid.UUID,
    demo_report: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Persist a runner result through a secret isolated from AI and Registry keys."""

    _require_flag(settings.foundry_auto_demo_enabled, "Foundry automatic Demo")
    _require_validation_runner(request)
    try:
        demo = await record_demo_report(
            db,
            validation_run_id=validation_run_id,
            demo_report=demo_report,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    version = await db.get(FoundryCandidateVersion, demo.candidate_version_id)
    return serialize_demo_run(
        demo,
        version_number=version.version_number if version else None,
    )


@internal_router.post(
    "/validation-runs/{validation_run_id}/failure",
    status_code=status.HTTP_200_OK,
)
async def ingest_foundry_validation_failure(
    validation_run_id: uuid.UUID,
    payload: ValidationWorkflowFailureReport,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Close a failed protected workflow without creating formal evidence."""

    _require_flag(settings.foundry_auto_demo_enabled, "Foundry automatic Demo")
    _require_validation_runner(request)
    if payload.validation_run_id != validation_run_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error_class": "validation_failure_binding_invalid",
                "message": "Path and body validation_run_id differ",
            },
        )
    expected_workflow_ref = (
        f"{settings.foundry_validation_github_repository}/.github/workflows/"
        f"{settings.foundry_validation_github_workflow}@refs/heads/"
        f"{settings.foundry_validation_github_ref}"
    )
    try:
        run, created = await record_validation_workflow_failure(
            db,
            validation_run_id=validation_run_id,
            report=payload.model_dump(mode="json"),
            expected_repository=settings.foundry_validation_github_repository,
            expected_workflow_ref=expected_workflow_ref,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    return {
        "validation_run_id": str(run.id),
        "candidate_id": str(run.candidate_id),
        "candidate_version_id": str(run.candidate_version_id),
        "status": (
            "DEMO_ALREADY_RECORDED"
            if run.status in {"PASSED", "PARTIAL", "FAILED"}
            else "FAILED_RECORDED"
        ),
        "failure_class": run.failure_class,
        "retryable": _validation_retryable(run),
        "idempotent_replay": not created,
    }


@internal_router.post("/draft-runs/{draft_run_id}/result")
async def ingest_foundry_ai_draft_result(
    draft_run_id: uuid.UUID,
    payload: AIDraftResultReport,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Append an AI candidate version after the key-bearing AI job has exited."""

    _require_flag(settings.foundry_ai_drafting_enabled, "Foundry AI drafting")
    _require_draft_result_callback(request)
    if payload.draft_run_id != draft_run_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error_class": "draft_result_binding_mismatch",
                "message": "Path and body draft_run_id differ",
            },
        )
    try:
        version, event = await record_ai_draft_result(
            db,
            draft_run_id=draft_run_id,
            draft_result=payload.model_dump(mode="json"),
            expected_repository=(
                settings.foundry_draft_github_repository or None
            ),
            # workflow_dispatch accepts a branch/tag ref.  The protected-main
            # workflow records the exact checkout SHA inside the signed/hash-
            # bound source receipt, so the literal branch name is not a source
            # commit and must not be compared with it here.
            expected_base_commit=None,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    auto_demo: dict[str, Any] | None = None
    if (
        settings.foundry_auto_demo_enabled
        and version is not None
        and event.event_type == "AI_DRAFT_RESULT_ACCEPTED"
    ):
        try:
            validation = await queue_and_dispatch_candidate_validation(
                db,
                candidate_id=event.candidate_id,
                candidate_version_id=version.id,
                candidate_version_hash=version.version_hash,
                actor_kind="CONTROL_PLANE",
                actor_user_id=None,
                idempotent_replay=True,
            )
        except FoundryCatalogError as exc:
            _raise_foundry_error(exc)
        auto_demo = {
            "validation_run_id": str(validation.id),
            "status": _validation_api_status(validation),
            "retryable": _validation_retryable(validation),
            "retry_after": (validation.validation_summary or {}).get(
                "retry_after"
            ),
            "failure_class": validation.failure_class,
        }
    return {
        "draft_run_id": str(draft_run_id),
        "candidate_id": str(event.candidate_id),
        "status": (
            "VERSION_APPENDED"
            if event.event_type == "AI_DRAFT_RESULT_ACCEPTED"
            else "FAILED_RECORDED"
        ),
        "candidate_version_id": str(version.id) if version else None,
        "candidate_version_hash": version.version_hash if version else None,
        "actor_kind": event.actor_kind,
        "event_hash": event.event_hash,
        "auto_demo": auto_demo,
    }


@internal_router.post("/formal-build-attestations", status_code=status.HTTP_201_CREATED)
async def ingest_formal_build_attestation(
    payload: FormalBuildAttestationReport,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Accept a trusted CI receipt; browsers and AI drafting cannot call this."""

    _require_flag(settings.foundry_registration_enabled, "Foundry registration")
    _require_formal_build_callback(request)
    try:
        row = await record_formal_build_attestation(
            db,
            attestation_report=payload.model_dump(mode="json"),
            expected_oidc_subject=settings.foundry_formal_build_oidc_subject,
            expected_github_repository=settings.foundry_formal_build_github_repository,
            expected_github_workflow=settings.foundry_formal_build_github_workflow,
            expected_github_ref=settings.foundry_formal_build_github_ref,
            trusted_attestation_public_keys=(
                settings.foundry_formal_build_attestation_verification_keyring
            ),
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    return {
        "build_attestation_id": str(row.id),
        "candidate_id": str(row.candidate_id),
        "candidate_version_id": str(row.candidate_version_id),
        "candidate_version_hash": row.candidate_version_hash,
        "formal_worker_image_digest": row.formal_worker_image_digest,
        "receipt_sha256": row.receipt_hash,
        "attestation_artifact_sha256": row.attestation_artifact_hash,
        "status": "VERIFIED_BUILD_RECEIPT",
    }


@internal_router.post(
    "/formal-build-attempts/{attempt_id}/failure",
    status_code=status.HTTP_200_OK,
)
async def ingest_formal_build_attempt_failure(
    attempt_id: uuid.UUID,
    payload: FormalBuildAttemptFailureReport,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Close one protected build attempt without granting any formal evidence."""

    _require_flag(settings.foundry_registration_enabled, "Foundry registration")
    _require_formal_build_failure_callback(request)
    if payload.formal_build_attempt_id != attempt_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error_class": "formal_build_failure_binding_invalid",
                "message": "Path and body formal_build_attempt_id differ",
            },
        )
    expected_workflow_ref = (
        f"{settings.foundry_formal_build_github_repository}/.github/workflows/"
        f"{settings.foundry_formal_build_github_workflow}@refs/heads/"
        f"{settings.foundry_formal_build_github_ref}"
    )
    try:
        event, created = await record_formal_build_attempt_failure(
            db,
            report=payload.model_dump(mode="json"),
            expected_repository=settings.foundry_formal_build_github_repository,
            expected_workflow_ref=expected_workflow_ref,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    return {
        "formal_build_attempt_id": str(attempt_id),
        "candidate_id": str(event.candidate_id),
        "candidate_version_id": str(event.candidate_version_id),
        "status": "FAILED_RECORDED",
        "failure_class": (event.event_payload or {}).get("failure_class"),
        "retryable": bool((event.event_payload or {}).get("retryable")),
        "idempotent_replay": not created,
        "event_hash": event.event_hash,
    }


@internal_router.post("/registry-releases/import")
async def ingest_signed_registry_release(
    payload: RegistryReleaseImportReceipt,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Record a signed release as deployable without hot-switching runtime."""

    _require_flag(settings.foundry_registration_enabled, "Foundry registration")
    _require_registry_import_callback(request)
    from app.services.foundry_registry_import import (
        RegistryReleaseImportError,
        record_signed_registry_release_import,
        serialize_registry_release_import,
    )

    try:
        row, created = await record_signed_registry_release_import(
            db,
            receipt=payload.model_dump(mode="json"),
            trusted_public_keys=settings.workflow_registry_verification_keyring,
        )
    except RegistryReleaseImportError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_class": exc.code, "message": str(exc)},
        ) from exc
    return {
        **serialize_registry_release_import(row),
        "idempotent_replay": not created,
    }


@internal_router.get("/registry-releases/{release_request_id}/export")
async def export_registry_release_request(
    release_request_id: uuid.UUID,
    request: Request,
    release_request_hash: str = Query(pattern=r"^sha256:[0-9a-f]{64}$"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return one exact hash-bound chain head to the protected signer."""

    _require_flag(settings.foundry_registration_enabled, "Foundry registration")
    _require_registry_export_callback(request)
    from app.services.foundry_registry_import import (
        RegistryReleaseImportError,
        export_pending_registry_release_request,
    )

    try:
        return await export_pending_registry_release_request(
            db,
            release_request_id=release_request_id,
            release_request_hash=release_request_hash,
        )
    except RegistryReleaseImportError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_class": exc.code, "message": str(exc)},
        ) from exc


@admin_router.post("/candidates/{candidate_id}/reviews")
async def admin_review_foundry_candidate(
    candidate_id: uuid.UUID,
    payload: FoundryReviewCreate,
    db: AsyncSession = Depends(get_db),
    reviewer: User = Depends(require_foundry_human_reviewer),
) -> dict[str, Any]:
    _require_flag(settings.foundry_candidate_catalog_enabled, "Foundry Candidate Catalog")
    try:
        review = await review_candidate_version(
            db,
            candidate_id=candidate_id,
            candidate_version_id=payload.candidate_version_id,
            candidate_version_hash=payload.candidate_version_hash,
            reviewer_user_id=reviewer.id,
            review_scope=payload.review_scope,
            decision=payload.decision,
            comment=payload.comment,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    candidate = await db.get(FoundryCandidate, candidate_id)
    return {
        "review_id": str(review.id),
        "candidate_id": str(candidate_id),
        "candidate_version_id": str(review.candidate_version_id),
        "candidate_version_hash": review.candidate_version_hash,
        "review_scope": review.review_scope,
        "decision": review.decision,
        "candidate_status": candidate.status if candidate else None,
        "created_at": review.created_at.isoformat(),
    }


@admin_router.post(
    "/candidates/{candidate_id}/formal-build",
    status_code=status.HTTP_202_ACCEPTED,
)
async def admin_dispatch_foundry_formal_build(
    candidate_id: uuid.UUID,
    payload: CandidateVersionBinding,
    db: AsyncSession = Depends(get_db),
    reviewer: User = Depends(require_foundry_human_reviewer),
) -> dict[str, Any]:
    """Dispatch a protected build using only server-reconstructed source pins."""

    _require_flag(settings.foundry_registration_enabled, "Foundry registration")
    try:
        plan = await request_formal_build_dispatch(
            db,
            candidate_id=candidate_id,
            candidate_version_id=payload.candidate_version_id,
            candidate_version_hash=payload.candidate_version_hash,
            actor_user_id=reviewer.id,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    binding = plan.binding
    if plan.attestation is not None:
        return {
            "candidate_id": str(candidate_id),
            "candidate_version_id": binding["candidate_version_id"],
            "candidate_version_hash": binding["candidate_version_hash"],
            "formal_build_request_id": str(plan.request_event.id),
            "formal_build_attempt_id": (
                str(plan.attempt_id) if plan.attempt_id else None
            ),
            "attempt_number": plan.attempt_number,
            "status": "VERIFIED_BUILD_RECEIPT",
            "build_attestation_id": str(plan.attestation.id),
            "idempotent_replay": True,
        }
    if plan.already_active:
        return {
            "candidate_id": str(candidate_id),
            "candidate_version_id": binding["candidate_version_id"],
            "candidate_version_hash": binding["candidate_version_hash"],
            "formal_build_request_id": str(plan.request_event.id),
            "formal_build_attempt_id": str(plan.attempt_id),
            "attempt_number": plan.attempt_number,
            "status": plan.dispatch_status,
            "retry_after": (
                plan.retry_after.isoformat() if plan.retry_after else None
            ),
            "build_attestation_id": None,
            "idempotent_replay": True,
        }
    if plan.attempt_id is None:
        raise HTTPException(
            status_code=409,
            detail={"error_class": "formal_build_attempt_missing"},
        )

    failure: FoundryCIDispatchError | None = None
    if settings.foundry_formal_build_dispatch_backend != "github_actions":
        failure = FoundryCIDispatchError(
            "formal_build_dispatch_disabled", retryable=True
        )
    else:
        config = FoundryCIDispatchConfig(
            repository=settings.foundry_formal_build_github_repository,
            ref=settings.foundry_formal_build_github_ref,
            token=settings.foundry_formal_build_github_token,
            formal_build_workflow=settings.foundry_formal_build_github_workflow,
        )
        try:
            await dispatch_formal_worker_build(
                config,
                candidate_id=binding["candidate_id"],
                candidate_version_id=binding["candidate_version_id"],
                formal_build_attempt_id=plan.attempt_id,
                candidate_version_hash=binding["candidate_version_hash"],
                source_commit=binding["source_commit"],
                source_tree_sha256=binding["source_tree_sha256"],
            )
        except FoundryCIDispatchError as exc:
            failure = exc
    if failure is not None:
        try:
            dispatch_event, _created = await record_formal_build_dispatch(
                db,
                binding=binding,
                attempt_id=plan.attempt_id,
                dispatched=False,
                failure_class=failure.code,
                retryable=failure.retryable,
                delivery_uncertain=failure.delivery_uncertain,
            )
        except FoundryCatalogError as exc:
            _raise_foundry_error(exc)
        retry_allowed = bool((dispatch_event.event_payload or {}).get("retryable"))
        if failure.delivery_uncertain:
            created_at = dispatch_event.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            return {
                "candidate_id": str(candidate_id),
                "candidate_version_id": binding["candidate_version_id"],
                "candidate_version_hash": binding["candidate_version_hash"],
                "formal_build_request_id": str(plan.request_event.id),
                "formal_build_attempt_id": str(plan.attempt_id),
                "attempt_number": plan.attempt_number,
                "status": "DISPATCH_UNCERTAIN",
                "failure_class": failure.code,
                "retry_after": (
                    created_at
                    + timedelta(
                        seconds=int(
                            (dispatch_event.event_payload or {}).get(
                                "dispatch_lease_seconds"
                            )
                            or 300
                        )
                    )
                ).isoformat(),
                "build_attestation_id": None,
                "idempotent_replay": not _created,
            }
        raise HTTPException(
            status_code=503 if retry_allowed else 409,
            detail={
                "error_class": failure.code,
                "message": "Formal build dispatch failed; the candidate remains approved",
                "formal_build_request_id": str(plan.request_event.id),
                "formal_build_attempt_id": str(plan.attempt_id),
                "attempt_number": plan.attempt_number,
                "retryable": retry_allowed,
            },
        )
    try:
        dispatch_event, created = await record_formal_build_dispatch(
            db,
            binding=binding,
            attempt_id=plan.attempt_id,
            dispatched=True,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    dispatched_at = dispatch_event.created_at
    if dispatched_at.tzinfo is None:
        dispatched_at = dispatched_at.replace(tzinfo=timezone.utc)
    return {
        "candidate_id": str(candidate_id),
        "candidate_version_id": binding["candidate_version_id"],
        "candidate_version_hash": binding["candidate_version_hash"],
        "formal_build_request_id": str(plan.request_event.id),
        "formal_build_attempt_id": str(plan.attempt_id),
        "attempt_number": plan.attempt_number,
        "status": "DISPATCHED",
        "retry_after": (
            dispatched_at
            + timedelta(
                seconds=int(
                    (dispatch_event.event_payload or {}).get(
                        "workflow_lease_seconds"
                    )
                    or 21600
                )
            )
        ).isoformat(),
        "build_attestation_id": None,
        "idempotent_replay": not created,
    }


@admin_router.post("/candidates/{candidate_id}/register")
async def admin_register_foundry_candidate(
    candidate_id: uuid.UUID,
    payload: FoundryRegisterRequest,
    db: AsyncSession = Depends(get_db),
    registrar: User = Depends(require_foundry_human_reviewer),
) -> dict[str, Any]:
    _require_flag(settings.foundry_registration_enabled, "Foundry registration")
    from app.services.foundry_catalog import register_candidate_version

    try:
        entry, release = await register_candidate_version(
            db,
            candidate_id=candidate_id,
            candidate_version_id=payload.candidate_version_id,
            candidate_version_hash=payload.candidate_version_hash,
            build_attestation_id=payload.build_attestation_id,
            registrar_user_id=registrar.id,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    release_dispatch = await _dispatch_pending_registry_release(
        db=db,
        candidate_id=candidate_id,
        candidate_version_id=entry.candidate_version_id,
        release=release,
    )
    return {
        "candidate_id": str(candidate_id),
        "status": "PENDING_RELEASE",
        "candidate_status": "APPROVED",
        "registry_entry_id": str(entry.id),
        "registry_entry_status": entry.status,
        "registry_entry_hash": entry.registry_entry_hash,
        "build_attestation_id": str(entry.formal_build_attestation_id),
        "formal_worker_image_digest": entry.worker_image_digest,
        "release_request_id": str(release.id),
        "release_request_hash": release.manifest_hash,
        "release_request_status": release.status,
        "release_dispatch": release_dispatch,
        "runtime_registry_modified": False,
    }


async def _change_formal_status(
    *,
    candidate_id: uuid.UUID,
    target_status: Literal["SUSPENDED", "REVOKED"],
    reason: str,
    actor: User,
    db: AsyncSession,
) -> dict[str, Any]:
    from app.services.foundry_catalog import change_registered_candidate_status

    try:
        candidate, entry, release = await change_registered_candidate_status(
            db,
            candidate_id=candidate_id,
            target_status=target_status,
            reason=reason,
            actor_user_id=actor.id,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    release_dispatch = await _dispatch_pending_registry_release(
        db=db,
        candidate_id=candidate.id,
        candidate_version_id=entry.candidate_version_id,
        release=release,
    )
    return {
        "candidate_id": str(candidate.id),
        "status": f"{target_status}_PENDING_RELEASE",
        "candidate_status": candidate.status,
        "registry_entry_id": str(entry.id),
        "registry_entry_status": entry.status,
        "release_request_id": str(release.id),
        "release_request_hash": release.manifest_hash,
        "release_request_status": release.status,
        "release_dispatch": release_dispatch,
        "runtime_registry_modified": False,
    }


@admin_router.post("/candidates/{candidate_id}/suspend")
async def admin_suspend_foundry_candidate(
    candidate_id: uuid.UUID,
    payload: FoundryStatusRequest,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_foundry_human_reviewer),
) -> dict[str, Any]:
    _require_flag(settings.foundry_registration_enabled, "Foundry registration")
    return await _change_formal_status(
        candidate_id=candidate_id,
        target_status="SUSPENDED",
        reason=payload.reason,
        actor=actor,
        db=db,
    )


@admin_router.post("/candidates/{candidate_id}/revoke")
async def admin_revoke_foundry_candidate(
    candidate_id: uuid.UUID,
    payload: FoundryStatusRequest,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_foundry_human_reviewer),
) -> dict[str, Any]:
    _require_flag(settings.foundry_registration_enabled, "Foundry registration")
    return await _change_formal_status(
        candidate_id=candidate_id,
        target_status="REVOKED",
        reason=payload.reason,
        actor=actor,
        db=db,
    )
