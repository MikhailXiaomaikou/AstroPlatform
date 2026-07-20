"""Private research workspace, source-document, and review APIs."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.models.claim_audit_records import ClaimAudit, EvidencePack
from app.models.database import get_db
from app.models.schemas import User
from app.models.workspace_records import SourceDocument, SourceExtraction
from app.services.research_workspace_service import (
    WorkspaceConflictError,
    WorkspaceInputError,
    WorkspaceNotFoundError,
    WorkspacePermissionError,
    WorkspaceServiceError,
    create_claim_audit_review,
    create_workspace,
    configured_scientific_reviewer_usernames,
    get_owned_source_document,
    get_owned_workspace,
    list_claim_audit_reviews,
    list_source_documents,
    list_workspaces,
    queue_union3_source,
    serialize_claim_audit_review,
    serialize_extraction,
    serialize_source_document,
    serialize_workspace,
    update_workspace,
)
from app.services.union3_research_loop import (
    Union3ResearchLoopError,
    create_union3_reproduction_audit,
    create_union3_reproduction_revision,
)


router = APIRouter(prefix="/api/research", tags=["research-workspaces"])
logger = logging.getLogger(__name__)


def _require_workspace_enabled() -> None:
    if not settings.research_workspace_enabled:
        raise HTTPException(status_code=404, detail="Research Workspace is not enabled")


def _require_reader_enabled() -> None:
    _require_workspace_enabled()
    if not settings.arxiv_reader_enabled:
        raise HTTPException(
            status_code=404, detail="Registered arXiv Reader is not enabled"
        )


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=20_000)


class WorkspaceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=20_000)
    status: Literal["ACTIVE", "ARCHIVED"] | None = None


class SourceDocumentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_profile_key: str = Field(min_length=1, max_length=64)
    identifier: str = Field(min_length=1, max_length=2048)


class ClaimAuditReviewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_document_id: uuid.UUID
    source_extraction_id: uuid.UUID
    candidate_id: str = Field(min_length=1, max_length=71)
    claim_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    anchor_ids: list[str] = Field(min_length=1, max_length=32)
    decision: Literal["APPROVED", "REJECTED", "CHANGES_REQUESTED"]
    comment: str = Field(default="", max_length=4000)


class WorkspaceClaimAuditCreateRequest(BaseModel):
    """The client selects a registered candidate; the server rebuilds the claim."""

    model_config = ConfigDict(extra="forbid")

    source_document_id: uuid.UUID
    candidate_id: str = Field(min_length=1, max_length=71)
    workflow_key: Literal["union3_flat_lcdm_sn_only_v1"]


def _require_union3_loop_enabled() -> None:
    _require_reader_enabled()
    required = (
        settings.claim_audit_enabled,
        settings.union3_reproduction_enabled,
        settings.local_science_worker_enabled,
        settings.evidence_pack_v2_enabled,
    )
    if not all(required):
        raise HTTPException(
            status_code=404,
            detail="The registered Union3 reproduction loop is not enabled",
        )


def _serialize_union3_audit(
    audit: ClaimAudit,
    pack: EvidencePack | None = None,
) -> dict[str, Any]:
    atomic_claim = audit.atomic_claim if isinstance(audit.atomic_claim, dict) else {}
    return {
        "audit_id": str(audit.id),
        "request_hash": audit.request_hash,
        "lifecycle_status": audit.lifecycle_status,
        "scientific_verdict": audit.scientific_verdict,
        "mode": audit.mode,
        "claim_text": audit.claim_text,
        "source": {"kind": audit.source_kind, "value": audit.source_value},
        "evidence_input_refs": list(audit.evidence_input_refs or []),
        "dataset_hints": list(audit.dataset_hints or []),
        "normalized_claims": list(audit.normalized_claims or []),
        "capability_gaps": list(audit.capability_gaps or []),
        "evidence_record_ids": list(audit.evidence_record_ids or []),
        "child_job_ids": list(audit.child_job_ids or []),
        "evidence_graph": audit.evidence_graph,
        "fact_check_report": audit.fact_check_report,
        "error": audit.error,
        "error_class": audit.error_class,
        "retry_count": audit.retry_count,
        "supersedes_audit_id": (
            str(audit.supersedes_audit_id) if audit.supersedes_audit_id else None
        ),
        "workspace_id": str(audit.workspace_id) if audit.workspace_id else None,
        "source_document_id": (
            str(audit.source_document_id) if audit.source_document_id else None
        ),
        "source_extraction_id": (
            str(audit.source_extraction_id) if audit.source_extraction_id else None
        ),
        "candidate_id": atomic_claim.get("candidate_id"),
        "workflow_key": "union3_flat_lcdm_sn_only_v1",
        "review_status": audit.review_status,
        "machine_support_eligible": audit.machine_support_eligible,
        "reproduction_ready": audit.reproduction_ready,
        "publication_ready": audit.publication_ready,
        "progress": audit.progress,
        "progress_stage": audit.progress_stage,
        "evidence_pack": (
            {
                "pack_id": str(pack.id),
                "status": pack.status,
                "schema_version": pack.schema_version,
                "manifest_hash": pack.manifest_hash,
                "pack_hash": pack.pack_hash,
                "key_id": pack.key_id,
                "download_url": f"/api/research/evidence-packs/{pack.id}/download",
                "finalized_at": pack.finalized_at.isoformat(),
            }
            if pack is not None
            and pack.status == "FINALIZED"
            and pack.finalized_at is not None
            else None
        ),
        "created_at": audit.created_at.isoformat() if audit.created_at else None,
        "started_at": audit.started_at.isoformat() if audit.started_at else None,
        "completed_at": audit.completed_at.isoformat() if audit.completed_at else None,
        "can_cancel": audit.lifecycle_status in {"QUEUED", "RUNNING"},
        "can_retry": audit.lifecycle_status == "FAILED_RETRYABLE",
        "can_revise": audit.lifecycle_status
        in {"COMPLETED", "FAILED_FINAL", "CANCELLED"},
    }


def _serialize_review_queue_item(
    audit: ClaimAudit,
    document: SourceDocument | None,
    extraction: SourceExtraction | None,
) -> dict[str, Any]:
    """Expose a server-derived, exact review packet to authorized reviewers.

    Reviewers deliberately cannot browse another user's private Workspace.
    This packet therefore carries only the registered claim and short source
    anchors needed for independent review.  The POST endpoint reconstructs and
    revalidates every binding; the browser is never trusted to author hashes.
    """

    item = _serialize_union3_audit(audit)
    item["review_binding"] = None
    item["review_evidence"] = None
    if document is None or extraction is None:
        return item
    if (
        document.id != audit.source_document_id
        or extraction.id != audit.source_extraction_id
        or extraction.source_document_id != document.id
        or document.user_id != audit.user_id
        or document.workspace_id != audit.workspace_id
        or extraction.user_id != audit.user_id
        or extraction.input_source_document_hash != document.source_document_hash
    ):
        return item

    normalized_claims = (
        audit.normalized_claims if isinstance(audit.normalized_claims, list) else []
    )
    candidate = (
        normalized_claims[0]
        if len(normalized_claims) == 1 and isinstance(normalized_claims[0], dict)
        else {}
    )
    candidate_id = candidate.get("candidate_id")
    claim_hash = candidate.get("claim_hash")
    anchor_ids = candidate.get("source_anchor_ids")
    payload = (
        extraction.extraction_payload
        if isinstance(extraction.extraction_payload, dict)
        else {}
    )
    extracted_candidates = payload.get("candidates")
    anchors = payload.get("anchors")
    if (
        not isinstance(candidate_id, str)
        or not isinstance(claim_hash, str)
        or claim_hash != audit.claim_hash
        or not isinstance(anchor_ids, list)
        or not anchor_ids
        or not isinstance(extracted_candidates, list)
        or candidate not in extracted_candidates
        or not isinstance(anchors, list)
    ):
        return item
    anchor_by_id = {
        anchor.get("anchor_id"): anchor
        for anchor in anchors
        if isinstance(anchor, dict) and isinstance(anchor.get("anchor_id"), str)
    }
    if any(anchor_id not in anchor_by_id for anchor_id in anchor_ids):
        return item

    item["review_binding"] = {
        "source_document_id": str(document.id),
        "source_extraction_id": str(extraction.id),
        "candidate_id": candidate_id,
        "claim_hash": claim_hash,
        "source_hash": document.source_document_hash,
        "anchor_ids": list(anchor_ids),
    }
    item["review_evidence"] = {
        "canonical_identifier": document.canonical_identifier,
        "source_url": document.source_url,
        "source_document_hash": document.source_document_hash,
        "source_extraction_hash": extraction.extraction_payload_hash,
        "candidate": dict(candidate),
        "anchors": [anchor_by_id[anchor_id] for anchor_id in anchor_ids],
        "limitations": list(payload.get("limitations") or []),
    }
    return item


def _raise_http(exc: WorkspaceServiceError) -> None:
    detail: dict[str, object] = {
        "error_class": exc.error_class,
        "message": str(exc),
        "retryable": exc.retryable,
    }
    if exc.source_document_id is not None:
        detail["source_document_id"] = str(exc.source_document_id)
    if isinstance(exc, WorkspaceNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, WorkspacePermissionError):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, WorkspaceConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(exc, WorkspaceInputError):
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    elif exc.retryable:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def create_research_workspace(
    payload: WorkspaceCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    try:
        workspace = await create_workspace(
            db,
            user_id=user.id,
            title=payload.title,
            description=payload.description,
        )
    except WorkspaceServiceError as exc:
        _raise_http(exc)
    return serialize_workspace(workspace)


@router.get("/workspaces")
async def get_research_workspaces(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    workspaces = await list_workspaces(db, user_id=user.id, limit=limit, offset=offset)
    return {"items": [serialize_workspace(item) for item in workspaces]}


@router.get("/workspaces/{workspace_id}")
async def get_research_workspace(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    try:
        workspace = await get_owned_workspace(
            db, user_id=user.id, workspace_id=workspace_id
        )
    except WorkspaceServiceError as exc:
        _raise_http(exc)
    return serialize_workspace(workspace)


@router.patch("/workspaces/{workspace_id}")
async def patch_research_workspace(
    workspace_id: uuid.UUID,
    payload: WorkspaceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    try:
        workspace = await update_workspace(
            db,
            user_id=user.id,
            workspace_id=workspace_id,
            title=payload.title,
            description=payload.description,
            status=payload.status,
        )
    except WorkspaceServiceError as exc:
        _raise_http(exc)
    return serialize_workspace(workspace)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_research_workspace(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    _require_workspace_enabled()
    try:
        await update_workspace(
            db,
            user_id=user.id,
            workspace_id=workspace_id,
            status="ARCHIVED",
        )
    except WorkspaceServiceError as exc:
        _raise_http(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workspaces/{workspace_id}/sources",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_source_document(
    workspace_id: uuid.UUID,
    payload: SourceDocumentCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_reader_enabled()
    try:
        document, extraction = await queue_union3_source(
            db,
            user_id=user.id,
            workspace_id=workspace_id,
            source_profile_key=payload.source_profile_key,
            identifier=payload.identifier,
        )
    except WorkspaceServiceError as exc:
        _raise_http(exc)
    if document.lifecycle_status in {"QUEUED", "FAILED_RETRYABLE"}:
        try:
            from app.tasks.union3_source_tasks import enqueue_union3_source

            enqueue_union3_source(document.id)
        except Exception:
            # PostgreSQL already contains the durable request.  Beat will
            # rediscover it even if Redis/Celery is temporarily unavailable.
            logger.exception(
                "Could not wake the Union3 source control worker for %s",
                document.id,
            )
    return serialize_source_document(document, extraction)


@router.get("/workspaces/{workspace_id}/sources")
async def get_source_documents(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    try:
        documents = await list_source_documents(
            db, user_id=user.id, workspace_id=workspace_id
        )
    except WorkspaceServiceError as exc:
        _raise_http(exc)
    return {
        "items": [
            serialize_source_document(document, extraction)
            for document, extraction in documents
        ]
    }


@router.get("/workspaces/{workspace_id}/sources/{source_document_id}")
async def get_source_document(
    workspace_id: uuid.UUID,
    source_document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    try:
        document, extraction = await get_owned_source_document(
            db,
            user_id=user.id,
            workspace_id=workspace_id,
            source_document_id=source_document_id,
        )
    except WorkspaceServiceError as exc:
        _raise_http(exc)
    return serialize_source_document(document, extraction)


async def _owned_source_by_id(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    source_document_id: uuid.UUID,
):
    document = await db.scalar(
        select(SourceDocument).where(
            SourceDocument.id == source_document_id,
            SourceDocument.user_id == user_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Source document not found")
    extraction = await db.scalar(
        select(SourceExtraction).where(
            SourceExtraction.source_document_id == document.id,
            SourceExtraction.user_id == user_id,
        )
    )
    return document, extraction


@router.get("/sources/{source_document_id}")
async def get_source_document_by_id(
    source_document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    document, extraction = await _owned_source_by_id(
        db, user_id=user.id, source_document_id=source_document_id
    )
    return serialize_source_document(document, extraction)


@router.get("/sources/{source_document_id}/content")
async def get_source_document_content(
    source_document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    document, extraction = await _owned_source_by_id(
        db, user_id=user.id, source_document_id=source_document_id
    )
    payload = extraction.extraction_payload if extraction is not None else {}
    return {
        "source_document_id": str(document.id),
        "canonical_identifier": document.canonical_identifier,
        "source_document_hash": document.source_document_hash,
        "content_kind": "registered_anchors",
        "anchors": list(payload.get("anchors") or []),
        "limitations": list(payload.get("limitations") or []),
    }


@router.get("/sources/{source_document_id}/tables")
async def get_source_document_tables(
    source_document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    document, extraction = await _owned_source_by_id(
        db, user_id=user.id, source_document_id=source_document_id
    )
    payload = extraction.extraction_payload if extraction is not None else {}
    return {
        "source_document_id": str(document.id),
        "table_label": "Table 9",
        "section_label": "5.3",
        "pdf_page_label": "58",
        "statistical_semantics": "frequentist_profile_chi_square",
        "candidates": list(payload.get("candidates") or []),
        "extraction": serialize_extraction(extraction),
    }


@router.post(
    "/workspaces/{workspace_id}/sources/{source_document_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_source_document(
    workspace_id: uuid.UUID,
    source_document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_reader_enabled()
    try:
        document, _ = await get_owned_source_document(
            db,
            user_id=user.id,
            workspace_id=workspace_id,
            source_document_id=source_document_id,
        )
        new_document, extraction = await queue_union3_source(
            db,
            user_id=user.id,
            workspace_id=workspace_id,
            source_profile_key=document.source_profile_key,
            identifier=document.canonical_identifier,
            create_new_version=True,
        )
    except WorkspaceServiceError as exc:
        _raise_http(exc)
    try:
        from app.tasks.union3_source_tasks import enqueue_union3_source

        enqueue_union3_source(new_document.id)
    except Exception:
        logger.exception(
            "Could not wake the Union3 source control worker for retry %s",
            new_document.id,
        )
    return serialize_source_document(new_document, extraction)


@router.post(
    "/sources/{source_document_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_source_document_by_id(
    source_document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Retry an owned source through the public source-level contract."""

    _require_reader_enabled()
    document, _ = await _owned_source_by_id(
        db,
        user_id=user.id,
        source_document_id=source_document_id,
    )
    return await retry_source_document(
        workspace_id=document.workspace_id,
        source_document_id=source_document_id,
        db=db,
        user=user,
    )


@router.post(
    "/workspaces/{workspace_id}/claim-audits",
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_claim_audit(
    workspace_id: uuid.UUID,
    payload: WorkspaceClaimAuditCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_union3_loop_enabled()
    try:
        audit, _job = await create_union3_reproduction_audit(
            db,
            user_id=user.id,
            workspace_id=workspace_id,
            source_document_id=payload.source_document_id,
            candidate_id=payload.candidate_id,
            workflow_key=payload.workflow_key,
        )
    except Union3ResearchLoopError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_class": exc.code, "message": str(exc)},
        ) from exc
    pack = await db.scalar(
        select(EvidencePack).where(EvidencePack.audit_id == audit.id)
    )
    return _serialize_union3_audit(audit, pack)


@router.post(
    "/claim-audits/{audit_id}/revisions",
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_claim_audit_revision(
    audit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Append a new immutable run after a terminal registered Audit."""

    _require_union3_loop_enabled()
    try:
        audit, _job = await create_union3_reproduction_revision(
            db,
            user_id=user.id,
            supersedes_audit_id=audit_id,
        )
    except Union3ResearchLoopError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"error_class": exc.code, "message": str(exc)},
        ) from exc
    pack = await db.scalar(
        select(EvidencePack).where(EvidencePack.audit_id == audit.id)
    )
    return _serialize_union3_audit(audit, pack)


@router.get("/workspaces/{workspace_id}/claim-audits")
async def get_workspace_claim_audits(
    workspace_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    try:
        await get_owned_workspace(db, user_id=user.id, workspace_id=workspace_id)
    except WorkspaceServiceError as exc:
        _raise_http(exc)
    audits = list(
        (
            await db.execute(
                select(ClaimAudit)
                .where(
                    ClaimAudit.workspace_id == workspace_id,
                    ClaimAudit.user_id == user.id,
                )
                .order_by(ClaimAudit.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    pack_rows = (
        list(
            (
                await db.execute(
                    select(EvidencePack).where(
                        EvidencePack.audit_id.in_([audit.id for audit in audits])
                    )
                )
            )
            .scalars()
            .all()
        )
        if audits
        else []
    )
    packs = {row.audit_id: row for row in pack_rows}
    return {
        "items": [
            _serialize_union3_audit(audit, packs.get(audit.id)) for audit in audits
        ],
        "total": len(audits),
    }


@router.get("/review-queue")
async def get_scientific_review_queue(
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    if (
        str(user.username).strip().lower()
        not in configured_scientific_reviewer_usernames()
    ):
        raise HTTPException(status_code=403, detail="Scientific reviewer role required")
    audits = list(
        (
            await db.execute(
                select(ClaimAudit)
                .where(
                    ClaimAudit.lifecycle_status == "COMPLETED",
                    ClaimAudit.scientific_verdict == "WITHHELD",
                    ClaimAudit.machine_support_eligible.is_(True),
                    ClaimAudit.review_status == "PENDING",
                    ClaimAudit.user_id != user.id,
                )
                .order_by(ClaimAudit.completed_at.asc(), ClaimAudit.id.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    source_ids = [
        audit.source_document_id
        for audit in audits
        if audit.source_document_id is not None
    ]
    extraction_ids = [
        audit.source_extraction_id
        for audit in audits
        if audit.source_extraction_id is not None
    ]
    documents = (
        list(
            (
                await db.execute(
                    select(SourceDocument).where(SourceDocument.id.in_(source_ids))
                )
            )
            .scalars()
            .all()
        )
        if source_ids
        else []
    )
    extractions = (
        list(
            (
                await db.execute(
                    select(SourceExtraction).where(
                        SourceExtraction.id.in_(extraction_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if extraction_ids
        else []
    )
    documents_by_id = {row.id: row for row in documents}
    extractions_by_id = {row.id: row for row in extractions}
    return {
        "items": [
            _serialize_review_queue_item(
                audit,
                documents_by_id.get(audit.source_document_id),
                extractions_by_id.get(audit.source_extraction_id),
            )
            for audit in audits
        ]
    }


@router.post(
    "/claim-audits/{audit_id}/reviews",
    status_code=status.HTTP_201_CREATED,
)
async def append_claim_audit_review_by_id(
    audit_id: uuid.UUID,
    payload: ClaimAuditReviewCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    audit = await db.get(ClaimAudit, audit_id)
    if audit is None or audit.workspace_id is None:
        raise HTTPException(status_code=404, detail="Claim Audit not found")
    return await append_claim_audit_review(
        workspace_id=audit.workspace_id,
        audit_id=audit_id,
        payload=payload,
        db=db,
        user=user,
    )


@router.post(
    "/workspaces/{workspace_id}/claim-audits/{audit_id}/reviews",
    status_code=status.HTTP_201_CREATED,
)
async def append_claim_audit_review(
    workspace_id: uuid.UUID,
    audit_id: uuid.UUID,
    payload: ClaimAuditReviewCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    try:
        review = await create_claim_audit_review(
            db,
            reviewer_user_id=user.id,
            reviewer_username=user.username,
            workspace_id=workspace_id,
            audit_id=audit_id,
            source_document_id=payload.source_document_id,
            source_extraction_id=payload.source_extraction_id,
            candidate_id=payload.candidate_id,
            claim_hash=payload.claim_hash,
            source_hash=payload.source_hash,
            anchor_ids=payload.anchor_ids,
            decision=payload.decision,
            comment=payload.comment,
        )
    except WorkspaceServiceError as exc:
        _raise_http(exc)
    if review.supports_finalization:
        try:
            from app.tasks.union3_research_tasks import (
                enqueue_union3_finalization,
            )

            enqueue_union3_finalization(audit_id)
        except Exception:
            # The review is durable and the periodic reconciler will retry.
            logger.exception(
                "Could not wake Union3 finalizer for Audit %s; reconciliation pending",
                audit_id,
            )
    return serialize_claim_audit_review(review)


@router.get("/workspaces/{workspace_id}/claim-audits/{audit_id}/reviews")
async def get_claim_audit_reviews(
    workspace_id: uuid.UUID,
    audit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_workspace_enabled()
    try:
        reviews = await list_claim_audit_reviews(
            db,
            requester_user_id=user.id,
            requester_username=user.username,
            workspace_id=workspace_id,
            audit_id=audit_id,
        )
    except WorkspaceServiceError as exc:
        _raise_http(exc)
    return {"items": [serialize_claim_audit_review(item) for item in reviews]}


__all__ = ["router"]
