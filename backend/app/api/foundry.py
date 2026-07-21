"""Owner-scoped Candidate Catalog and protected workflow-governance APIs."""

from __future__ import annotations

import hmac
import inspect
import os
import uuid
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
)
from app.models.schemas import User
from app.services.foundry_catalog import (
    FoundryCatalogError,
    create_capability_request,
    merge_capability_request,
    review_candidate_version,
    serialize_candidate,
    serialize_capability_request,
    serialize_demo_run,
    start_validation_run,
    triage_capability_request,
    record_demo_report,
    record_formal_build_attestation,
    record_validation_dispatch,
)
from app.services.foundry_validation_dispatch import (
    FoundryValidationDispatchError,
    dispatch_candidate_validation,
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


class FoundryReviewCreate(CandidateVersionBinding):
    review_scope: Literal["ENGINEERING", "SCIENTIFIC"]
    decision: Literal["APPROVED", "REJECTED", "CHANGES_REQUESTED"]
    comment: str = Field(default="", max_length=4000)


class FoundryRegisterRequest(CandidateVersionBinding):
    build_attestation_id: uuid.UUID


class FormalBuildAttestationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    attestation_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_version_id: uuid.UUID
    candidate_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    dependency_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formal_sbom_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tests_passed: Literal[True]
    formal_worker_image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    oidc_issuer: Literal["https://token.actions.githubusercontent.com"]
    oidc_subject: str = Field(min_length=1, max_length=2048)
    sigstore_verified: Literal[True]
    sigstore_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_method: Literal["protected_ci_callback_after_sigstore_verification"]
    build_metadata: dict[str, Any]
    built_at: str
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


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


def _require_formal_build_callback(request: Request) -> None:
    authorization = request.headers.get("Authorization", "")
    expected = settings.foundry_formal_build_result_secret
    provided = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not expected or not provided or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Formal build callback access required")


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
            "status": row.status,
            "validation_summary": dict(row.validation_summary or {}),
            "failure_class": row.failure_class,
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
            "git_commit": row.git_commit,
            "oidc_issuer": row.oidc_issuer,
            "oidc_subject": row.oidc_subject,
            "sigstore_bundle_sha256": row.sigstore_bundle_hash,
            "provenance_sha256": row.provenance_hash,
            "receipt_sha256": row.receipt_hash,
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
        row = await start_validation_run(
            db,
            candidate_id=candidate_id,
            candidate_version_id=payload.candidate_version_id,
            candidate_version_hash=payload.candidate_version_hash,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    version = await db.get(FoundryCandidateVersion, row.candidate_version_id)
    failure_class = None
    should_dispatch = row.status == "QUEUED"
    if not should_dispatch:
        return {
            "validation_run_id": str(row.id),
            "status": row.status,
            "candidate_id": str(row.candidate_id),
            "candidate_version_id": str(row.candidate_version_id),
            "candidate_version_hash": row.candidate_version_hash,
            "retryable": False,
            "failure_class": row.failure_class,
            "created_at": row.created_at.isoformat(),
        }
    if version is None:
        failure_class = "validation_dispatch_internal_error"
    else:
        try:
            await dispatch_candidate_validation(
                validation_run_id=row.id,
                candidate_key=version.candidate_key,
            )
        except FoundryValidationDispatchError as exc:
            failure_class = exc.failure_class
        except Exception:
            failure_class = "validation_dispatch_internal_error"
    try:
        row = await record_validation_dispatch(
            db,
            validation_run_id=row.id,
            dispatched=failure_class is None,
            failure_class=failure_class,
        )
    except FoundryCatalogError as exc:
        _raise_foundry_error(exc)
    return {
        "validation_run_id": str(row.id),
        "status": row.status,
        "candidate_id": str(row.candidate_id),
        "candidate_version_id": str(row.candidate_version_id),
        "candidate_version_hash": row.candidate_version_hash,
        "retryable": row.status == "DISPATCH_FAILED",
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
        "status": "VERIFIED_BUILD_RECEIPT",
    }


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
    return {
        "candidate_id": str(candidate.id),
        "status": f"{target_status}_PENDING_RELEASE",
        "candidate_status": candidate.status,
        "registry_entry_id": str(entry.id),
        "registry_entry_status": entry.status,
        "release_request_id": str(release.id),
        "release_request_hash": release.manifest_hash,
        "release_request_status": release.status,
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
