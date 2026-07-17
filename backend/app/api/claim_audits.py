"""Owner-scoped Claim Audit and private Evidence Pack APIs."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Literal
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.models.claim_audit_records import ClaimAudit, EvidencePack
from app.models.database import get_db
from app.models.research_records import ResearchJob
from app.models.schemas import ChatSession, User
from app.services.claim_audit_service import (
    AUDIT_MODES,
    ClaimAuditInputError,
    canonical_request_hash,
    load_structured_research_job_result,
    normalize_claims,
    process_claim_audit,
    validate_source,
    verify_pack_bytes,
)
from app.services.server_evidence import verify_research_job_attestation
from app.services.event_collector import event_collector
from app.rate_limit import limiter


router = APIRouter(prefix="/api/research", tags=["claim-audits"])
logger = logging.getLogger(__name__)
_ACTIVE_AUDIT_STATUSES = frozenset({"QUEUED", "RUNNING"})


async def _track_started_audit(audit: ClaimAudit) -> None:
    try:
        await event_collector.track(
            "claim_audit.started",
            {
                "source_kind": audit.source_kind,
                "execution_mode": audit.mode,
            },
            user_id=audit.user_id,
        )
    except Exception:
        logger.exception("Could not buffer consent-gated Claim Audit start metadata")


class ClaimSource(BaseModel):
    kind: Literal["doi", "arxiv", "bibcode", "url"]
    value: str = Field(min_length=1, max_length=2048)


class ClaimAuditCreateRequest(BaseModel):
    claim_text: str = Field(min_length=1, max_length=20_000)
    source: ClaimSource
    evidence_input_refs: list[str] = Field(default_factory=list, max_length=32)
    dataset_hints: list[str] = Field(default_factory=list, max_length=32)
    mode: Literal["audit_only", "execute_registered"] = "audit_only"
    session_id: str | None = None


def _require_enabled() -> None:
    if not settings.claim_audit_enabled:
        raise HTTPException(status_code=404, detail="Claim Audit is not enabled")


def _serialize(audit: ClaimAudit, pack: EvidencePack | None = None) -> dict:
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
        "evidence_pack": (
            {
                "pack_id": str(pack.id),
                "status": pack.status,
                "schema_version": pack.schema_version,
                "manifest_hash": pack.manifest_hash,
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
    }


async def _owned_audit(
    db: AsyncSession,
    user: User,
    audit_id: str,
) -> ClaimAudit:
    try:
        parsed_id = uuid.UUID(audit_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Claim Audit not found") from exc
    audit = (
        await db.execute(
            select(ClaimAudit).where(
                ClaimAudit.id == parsed_id,
                ClaimAudit.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if audit is None:
        raise HTTPException(status_code=404, detail="Claim Audit not found")
    return audit


async def _pack_for_audit(db: AsyncSession, audit_id: uuid.UUID) -> EvidencePack | None:
    return (
        await db.execute(select(EvidencePack).where(EvidencePack.audit_id == audit_id))
    ).scalar_one_or_none()


def _dispatch_claim_audit(audit_id: uuid.UUID) -> None:
    from app.tasks.claim_audit_tasks import process_claim_audit_task

    process_claim_audit_task.delay(str(audit_id))


async def _start_claim_audit(db: AsyncSession, audit: ClaimAudit) -> ClaimAudit:
    if settings.claim_audit_execution_mode == "inline":
        return await process_claim_audit(db, audit)
    try:
        _dispatch_claim_audit(audit.id)
    except Exception:
        logger.exception("Claim Audit dispatch failed")
        audit.lifecycle_status = "FAILED_RETRYABLE"
        audit.error_class = "dispatch_failed"
        audit.error = "Claim Audit worker dispatch failed"
        await db.commit()
        await db.refresh(audit)
    return audit


async def _lock_owner_and_enforce_active_limit(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    exclude_audit_id: uuid.UUID | None = None,
) -> None:
    """Serialize starts for one owner and enforce a durable worker cap."""

    locked_user = await db.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if locked_user is None or str(locked_user.account_status).upper() != "ACTIVE":
        raise HTTPException(status_code=403, detail="Account is not active")
    filters = [
        ClaimAudit.user_id == user_id,
        ClaimAudit.lifecycle_status.in_(_ACTIVE_AUDIT_STATUSES),
    ]
    if exclude_audit_id is not None:
        filters.append(ClaimAudit.id != exclude_audit_id)
    active = int(
        await db.scalar(select(func.count()).select_from(ClaimAudit).where(*filters))
        or 0
    )
    if active >= settings.claim_audit_max_active_per_user:
        raise HTTPException(
            status_code=429,
            detail=(
                "Claim Audit active-work limit reached; wait for an existing "
                "audit to finish or cancel it"
            ),
        )


@router.post("/claim-audits", status_code=status.HTTP_201_CREATED)
@limiter.limit("3/minute")
async def create_claim_audit(
    request: Request,
    payload: ClaimAuditCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_enabled()
    try:
        source_kind, source_value = validate_source(
            payload.source.kind,
            payload.source.value,
        )
        normalize_claims(payload.claim_text)
    except ClaimAuditInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.mode not in AUDIT_MODES:
        raise HTTPException(status_code=422, detail="Unsupported audit mode")
    evidence_refs = list(dict.fromkeys(str(item).strip() for item in payload.evidence_input_refs if str(item).strip()))
    dataset_hints = list(dict.fromkeys(str(item).strip() for item in payload.dataset_hints if str(item).strip()))
    if evidence_refs:
        evidence_rows = list(
            (
                await db.execute(
                    select(ResearchJob).where(
                        ResearchJob.user_id == user.id,
                        ResearchJob.job_id.in_(evidence_refs),
                    )
                )
            ).scalars().all()
        )
        by_job_id = {row.job_id: row for row in evidence_rows}
        if any(job_id not in by_job_id for job_id in evidence_refs):
            raise HTTPException(
                status_code=422,
                detail="Every evidence_input_ref must be owned by this account",
            )
        pending = [
            row.job_id
            for row in evidence_rows
            if row.status in {"queued", "running"}
        ]
        if pending:
            raise HTTPException(
                status_code=409,
                detail="Referenced research jobs are still running; retry after completion",
            )
        invalid: list[str] = []
        for row in evidence_rows:
            valid_attestation = (
                row.status == "completed"
                and verify_research_job_attestation(
                    row.attestation,
                    job_id=row.job_id,
                    owner_id=row.user_id,
                    session_id=row.session_id,
                    tool_name=row.tool_name,
                    inputs_hash=row.inputs_hash,
                    args=row.args or {},
                    args_replayable=row.args_replayable,
                    result=row.result,
                    background_backend=row.background_backend,
                    completed_at=row.completed_at,
                )
            )
            if not valid_attestation:
                invalid.append(row.job_id)
                continue
            try:
                structured_result = load_structured_research_job_result(row.result)
            except Exception:
                structured_result = None
            if structured_result is None:
                invalid.append(row.job_id)
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Every evidence_input_ref must be a completed server-signed "
                    "job with a structured result"
                ),
            )
    session_id: uuid.UUID | None = None
    if payload.session_id:
        try:
            session_id = uuid.UUID(payload.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid session id") from exc
        owned_session = (
            await db.execute(
                select(ChatSession.id).where(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if owned_session is None:
            raise HTTPException(status_code=404, detail="Session not found")
    request_hash = canonical_request_hash(
        claim_text=payload.claim_text,
        source_kind=source_kind,
        source_value=source_value,
        evidence_input_refs=evidence_refs,
        dataset_hints=dataset_hints,
        mode=payload.mode,
    )
    existing = (
        await db.execute(
            select(ClaimAudit).where(
                ClaimAudit.user_id == user.id,
                ClaimAudit.request_hash == request_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _serialize(existing, await _pack_for_audit(db, existing.id))
    await _lock_owner_and_enforce_active_limit(db, user_id=user.id)
    # The owner lock serializes concurrent creates. Recheck idempotency inside
    # that critical section before consuming another worker slot.
    existing = (
        await db.execute(
            select(ClaimAudit).where(
                ClaimAudit.user_id == user.id,
                ClaimAudit.request_hash == request_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _serialize(existing, await _pack_for_audit(db, existing.id))
    audit = ClaimAudit(
        user_id=user.id,
        session_id=session_id,
        request_hash=request_hash,
        lifecycle_status="QUEUED",
        mode=payload.mode,
        claim_text=payload.claim_text.strip(),
        source_kind=source_kind,
        source_value=source_value,
        evidence_input_refs=evidence_refs,
        dataset_hints=dataset_hints,
    )
    db.add(audit)
    try:
        await db.commit()
        await db.refresh(audit)
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(ClaimAudit).where(
                    ClaimAudit.user_id == user.id,
                    ClaimAudit.request_hash == request_hash,
                )
            )
        ).scalar_one()
        return _serialize(existing, await _pack_for_audit(db, existing.id))
    await _track_started_audit(audit)
    audit = await _start_claim_audit(db, audit)
    return _serialize(audit, await _pack_for_audit(db, audit.id))


@router.get("/claim-audits")
async def list_claim_audits(
    lifecycle_status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_enabled()
    filters = [ClaimAudit.user_id == user.id]
    if lifecycle_status:
        filters.append(ClaimAudit.lifecycle_status == lifecycle_status)
    total = await db.scalar(select(func.count()).select_from(ClaimAudit).where(*filters))
    rows = (
        await db.execute(
            select(ClaimAudit)
            .where(*filters)
            .order_by(ClaimAudit.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    packs = (
        await db.execute(
            select(EvidencePack).where(EvidencePack.audit_id.in_([row.id for row in rows]))
        )
    ).scalars().all() if rows else []
    packs_by_audit = {pack.audit_id: pack for pack in packs}
    return {
        "items": [_serialize(row, packs_by_audit.get(row.id)) for row in rows],
        "total": int(total or 0),
    }


@router.get("/claim-audits/{audit_id}")
async def get_claim_audit(
    audit_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_enabled()
    audit = await _owned_audit(db, user, audit_id)
    return _serialize(audit, await _pack_for_audit(db, audit.id))


@router.post("/claim-audits/{audit_id}/cancel")
async def cancel_claim_audit(
    audit_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_enabled()
    audit = await _owned_audit(db, user, audit_id)
    cancelled = await db.execute(
        update(ClaimAudit)
        .where(
            ClaimAudit.id == audit.id,
            ClaimAudit.user_id == user.id,
            ClaimAudit.lifecycle_status.in_({"QUEUED", "RUNNING"}),
        )
        .values(
            lifecycle_status="CANCELLED",
            worker_lease_id=None,
            lease_expires_at=None,
        )
    )
    if cancelled.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Claim Audit is no longer cancellable")
    if audit.child_job_ids:
        await db.execute(
            update(ResearchJob)
            .where(
                ResearchJob.user_id == user.id,
                ResearchJob.job_id.in_(audit.child_job_ids),
                ResearchJob.status.in_({"queued", "running"}),
            )
            .values(
                status="cancelled",
                progress_message="Parent Claim Audit was cancelled",
                error=None,
                error_class=None,
                completed_at=datetime.now(timezone.utc),
                attestation=None,
            )
        )
    await db.commit()
    await db.refresh(audit)
    return _serialize(audit)


@router.post("/claim-audits/{audit_id}/retry")
@limiter.limit("2/minute")
async def retry_claim_audit(
    request: Request,
    audit_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_enabled()
    audit = await _owned_audit(db, user, audit_id)
    if audit.lifecycle_status != "FAILED_RETRYABLE":
        raise HTTPException(
            status_code=409,
            detail="Only retryable failed audits can be retried",
        )
    await _lock_owner_and_enforce_active_limit(
        db,
        user_id=user.id,
        exclude_audit_id=audit.id,
    )
    await db.refresh(audit)
    if audit.lifecycle_status != "FAILED_RETRYABLE":
        raise HTTPException(
            status_code=409,
            detail="Only retryable failed audits can be retried",
        )
    audit.retry_count += 1
    audit.lifecycle_status = "QUEUED"
    audit.error = None
    audit.error_class = None
    audit.worker_lease_id = None
    audit.lease_expires_at = None
    await db.commit()
    await _track_started_audit(audit)
    audit = await _start_claim_audit(db, audit)
    return _serialize(audit, await _pack_for_audit(db, audit.id))


@router.delete("/claim-audits/{audit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_claim_audit(
    audit_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_enabled()
    audit = await _owned_audit(db, user, audit_id)
    if audit.lifecycle_status in {"QUEUED", "RUNNING"}:
        raise HTTPException(
            status_code=409,
            detail="Cancel the running Claim Audit before deleting it",
        )
    pack = await _pack_for_audit(db, audit.id)
    if pack is not None and pack.status != "FINALIZED":
        raise HTTPException(
            status_code=409,
            detail="Claim Audit cleanup is still in progress",
        )
    child_rows = []
    if audit.child_job_ids:
        child_rows = list((await db.execute(
            select(ResearchJob).where(
                ResearchJob.user_id == user.id,
                ResearchJob.job_id.in_(audit.child_job_ids),
            )
        )).scalars().all())
        if any(row.status in {"queued", "running"} for row in child_rows):
            raise HTTPException(
                status_code=409,
                detail="Registered child workflow cleanup is still in progress",
            )
    if pack is not None:
        from app.storage import delete_fits_all_versions

        try:
            await asyncio.to_thread(delete_fits_all_versions, pack.artifact_ref)
        except Exception as exc:
            await db.rollback()
            logger.exception("Could not permanently delete Evidence Pack artifact")
            raise HTTPException(
                status_code=503,
                detail="Evidence Pack storage cleanup failed; retry deletion",
            ) from exc
        await db.delete(pack)
    for child in child_rows:
        if isinstance(child.result, dict) and child.result.get("_artifact_ref"):
            from app.storage import delete_fits_all_versions

            try:
                await asyncio.to_thread(
                    delete_fits_all_versions,
                    str(child.result["_artifact_ref"]),
                )
            except Exception as exc:
                await db.rollback()
                logger.exception("Could not permanently delete child research artifact")
                raise HTTPException(
                    status_code=503,
                    detail="Research artifact cleanup failed; retry deletion",
                ) from exc
        await db.delete(child)
    await db.delete(audit)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _owned_pack(
    db: AsyncSession,
    user: User,
    pack_id: str,
) -> EvidencePack:
    try:
        parsed_id = uuid.UUID(pack_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Evidence Pack not found") from exc
    pack = (
        await db.execute(
            select(EvidencePack).where(
                EvidencePack.id == parsed_id,
                EvidencePack.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if pack is None:
        raise HTTPException(status_code=404, detail="Evidence Pack not found")
    return pack


@router.get("/evidence-packs/{pack_id}/download")
async def download_evidence_pack(
    pack_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_enabled()
    pack = await _owned_pack(db, user, pack_id)
    if pack.status != "FINALIZED":
        raise HTTPException(status_code=409, detail="Evidence Pack is not finalized")
    from app.storage import download_fits

    try:
        payload = download_fits(pack.artifact_ref)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="Evidence Pack artifact is missing") from exc
    verification = verify_pack_bytes(payload)
    if not verification.get("valid"):
        raise HTTPException(status_code=409, detail="Evidence Pack integrity check failed")
    if (
        str(verification.get("audit_id") or "") != str(pack.audit_id)
        or str(verification.get("manifest_hash") or "") != pack.manifest_hash
        or str(verification.get("key_id") or "") != pack.key_id
    ):
        raise HTTPException(status_code=409, detail="Evidence Pack binding check failed")
    try:
        audit = await db.get(ClaimAudit, pack.audit_id)
        await event_collector.track(
            "evidence_pack.exported",
            {
                "outcome_bucket": str(
                    audit.scientific_verdict if audit is not None else "unknown"
                ).lower(),
                "execution_mode": audit.mode if audit is not None else "unknown",
            },
            user_id=user.id,
        )
    except Exception:
        logger.exception("Could not buffer consent-gated Evidence Pack export metadata")
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="claim-audit-{pack.audit_id}.zip"',
            "X-Evidence-Manifest-Hash": pack.manifest_hash,
        },
    )


@router.post("/evidence-packs/verify")
async def verify_evidence_pack(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_enabled()
    max_upload_bytes = 32 * 1024 * 1024
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_upload_bytes:
                raise HTTPException(status_code=413, detail="Evidence Pack is too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from None

    pack_id: str | None = None
    supplied_payload: bytes | None = None
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("application/json"):
        try:
            body = await request.json()
            pack_id = str(body.get("pack_id") or "").strip()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid verification request") from exc
    elif content_type.startswith("multipart/form-data"):
        form = await request.form()
        pack_id = str(form.get("pack_id") or "").strip() or None
        uploaded = form.get("file")
        if uploaded is None or not hasattr(uploaded, "read"):
            raise HTTPException(status_code=422, detail="Evidence Pack file is required")
        supplied_payload = await uploaded.read(max_upload_bytes + 1)
        if len(supplied_payload) > max_upload_bytes:
            raise HTTPException(status_code=413, detail="Evidence Pack is too large")
    else:
        raise HTTPException(
            status_code=415,
            detail="Use application/json or multipart/form-data",
        )

    pack = await _owned_pack(db, user, pack_id) if pack_id else None
    from app.storage import download_fits

    if supplied_payload is None:
        if pack is None:
            raise HTTPException(status_code=422, detail="pack_id or file is required")
        try:
            supplied_payload = download_fits(pack.artifact_ref)
        except FileNotFoundError:
            return {"valid": False, "reason": "artifact_missing"}
    result = verify_pack_bytes(supplied_payload)
    if not result.get("valid"):
        return result
    try:
        audit_id = uuid.UUID(str(result.get("audit_id") or ""))
    except ValueError:
        return {"valid": False, "reason": "invalid_audit_binding"}
    owned_audit = await db.scalar(
        select(ClaimAudit.id).where(
            ClaimAudit.id == audit_id,
            ClaimAudit.user_id == user.id,
        )
    )
    if owned_audit is None:
        return {"valid": False, "reason": "audit_binding_mismatch"}
    if pack is not None and audit_id != pack.audit_id:
        return {"valid": False, "reason": "audit_binding_mismatch"}
    return result
