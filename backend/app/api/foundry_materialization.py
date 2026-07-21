"""Human-gated and protected-callback APIs for Candidate source materialization."""

from __future__ import annotations

import hmac
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.foundry import require_foundry_human_reviewer
from app.config import settings
from app.models.database import get_db
from app.models.foundry_materialization_records import (
    FoundryMaterializationAttestation,
    FoundryMaterializationReceipt,
)
from app.models.schemas import User
from app.services.foundry_catalog import FoundryCatalogError
from app.services.foundry_materialization import (
    materialization_workflow_retry_after,
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
    dispatch_materialization_finalization,
    dispatch_materialization_pr,
)


admin_router = APIRouter(prefix="/api/admin/foundry", tags=["admin-workflow-foundry"])
internal_router = APIRouter(prefix="/api/internal/foundry", tags=["internal-workflow-foundry"])


class ExactCandidateVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_version_id: uuid.UUID
    candidate_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class FinalizeMaterialization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materialization_attestation_id: uuid.UUID


class SignedMaterializationBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    payload: dict[str, Any]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: dict[str, Any]
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _enabled() -> None:
    if not settings.foundry_source_materialization_enabled:
        raise HTTPException(status_code=404, detail="Foundry source materialization is not enabled")


def _error(exc: FoundryCatalogError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error_class": exc.error_class, "message": str(exc)},
    ) from exc


def _callback(request: Request) -> None:
    authorization = request.headers.get("Authorization", "")
    provided = authorization[7:] if authorization.startswith("Bearer ") else ""
    expected = settings.foundry_materialization_result_secret
    if not provided or not expected or not hmac.compare_digest(
        provided.encode(), expected.encode()
    ):
        raise HTTPException(status_code=403, detail="Materialization callback access required")


def _dispatch_config() -> FoundryMaterializationDispatchConfig:
    return FoundryMaterializationDispatchConfig(
        repository=settings.foundry_materialization_github_repository,
        token=settings.foundry_materialization_github_token,
        materialize_workflow=settings.foundry_materialization_github_workflow,
        finalize_workflow=settings.foundry_materialization_finalize_github_workflow,
        ref=settings.foundry_materialization_github_ref,
    )


def _reservation_status(state: str) -> str:
    """Preserve DISPATCHED only when GitHub has actually accepted the request."""

    return "DISPATCHED" if state == "WORKFLOW_DISPATCHED" else state


@admin_router.post(
    "/candidates/{candidate_id}/materialize",
    status_code=status.HTTP_202_ACCEPTED,
)
async def materialize_candidate_source(
    candidate_id: uuid.UUID,
    payload: ExactCandidateVersion,
    db: AsyncSession = Depends(get_db),
    reviewer: User = Depends(require_foundry_human_reviewer),
) -> dict[str, Any]:
    """Request a draft PR; browser fields never include source or patch data."""

    _enabled()
    try:
        binding, event, reservation, attestation = await request_source_materialization(
            db,
            candidate_id=candidate_id,
            candidate_version_id=payload.candidate_version_id,
            candidate_version_hash=payload.candidate_version_hash,
            actor_user_id=reviewer.id,
        )
    except FoundryCatalogError as exc:
        _error(exc)
    if attestation is not None:
        return {
            "status": "DRAFT_PR_ATTESTED",
            "materialization_request_id": str(event.id),
            "materialization_attestation_id": str(attestation.id),
            "pull_request_number": attestation.pull_request_number,
            "pull_request_url": attestation.pull_request_url,
            "auto_merge_performed": False,
            "idempotent_replay": True,
        }
    if reservation is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error_class": "materialization_dispatch_reservation_missing",
                "message": "Protected materialization dispatch reservation is missing",
            },
        )
    if not reservation.should_dispatch:
        return {
            "status": _reservation_status(reservation.state),
            "dispatch_state": reservation.state,
            "materialization_request_id": str(event.id),
            "materialization_attestation_id": None,
            "dispatch_attempt": reservation.attempt_number,
            "retry_after": reservation.retry_after.isoformat(),
            "idempotent_replay": True,
        }
    if settings.foundry_materialization_dispatch_backend != "github_actions":
        failure = FoundryMaterializationDispatchError(
            "materialization_dispatch_disabled", retryable=True
        )
    else:
        failure = None
        try:
            await dispatch_materialization_pr(
                _dispatch_config(), request_id=event.id, binding=binding
            )
        except FoundryMaterializationDispatchError as exc:
            failure = exc
    if failure is not None:
        outcome_event = await record_materialization_dispatch(
            db,
            request_event=event,
            reservation=reservation,
            dispatched=False,
            failure_class=failure.code,
            retryable=failure.retryable,
            outcome_unknown=failure.outcome_unknown,
        )
        if failure.outcome_unknown:
            return {
                "status": "DISPATCH_OUTCOME_UNKNOWN",
                "dispatch_state": "DISPATCH_OUTCOME_UNKNOWN",
                "materialization_request_id": str(event.id),
                "materialization_attestation_id": None,
                "dispatch_attempt": reservation.attempt_number,
                "retry_after": materialization_workflow_retry_after(
                    outcome_event
                ).isoformat(),
                "outcome_unknown": True,
                "idempotent_replay": False,
            }
        raise HTTPException(
            status_code=503 if failure.retryable else 409,
            detail={
                "error_class": failure.code,
                "message": "Protected materialization dispatch failed; no source was changed",
                "retryable": failure.retryable,
            },
        )
    dispatch_event = await record_materialization_dispatch(
        db,
        request_event=event,
        reservation=reservation,
        dispatched=True,
    )
    return {
        "status": "DISPATCHED",
        "dispatch_state": "WORKFLOW_DISPATCHED",
        "materialization_request_id": str(event.id),
        "materialization_attestation_id": None,
        "dispatch_attempt": reservation.attempt_number,
        "retry_after": materialization_workflow_retry_after(
            dispatch_event
        ).isoformat(),
        "idempotent_replay": False,
    }


@admin_router.post(
    "/candidates/{candidate_id}/materialization-finalize",
    status_code=status.HTTP_202_ACCEPTED,
)
async def finalize_candidate_materialization(
    candidate_id: uuid.UUID,
    payload: FinalizeMaterialization,
    db: AsyncSession = Depends(get_db),
    reviewer: User = Depends(require_foundry_human_reviewer),
) -> dict[str, Any]:
    """After human merge, dispatch exact-PR verification and a fresh image build."""

    _enabled()
    try:
        binding, event, reservation, receipt = await request_materialization_finalization(
            db,
            candidate_id=candidate_id,
            attestation_id=payload.materialization_attestation_id,
            actor_user_id=reviewer.id,
        )
    except FoundryCatalogError as exc:
        _error(exc)
    if receipt is not None:
        return {
            "status": "FINALIZED_NEW_VERSION",
            "materialization_receipt_id": str(receipt.id),
            "candidate_version_id": str(receipt.materialized_candidate_version_id),
            "demo_and_reviews_transferred": False,
            "idempotent_replay": True,
        }
    if reservation is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error_class": "materialization_finalization_dispatch_reservation_missing",
                "message": "Merge finalization dispatch reservation is missing",
            },
        )
    if not reservation.should_dispatch:
        return {
            "status": _reservation_status(reservation.state),
            "dispatch_state": reservation.state,
            "materialization_attestation_id": str(payload.materialization_attestation_id),
            "dispatch_attempt": reservation.attempt_number,
            "retry_after": reservation.retry_after.isoformat(),
            "idempotent_replay": True,
        }
    if settings.foundry_materialization_dispatch_backend != "github_actions":
        failure = FoundryMaterializationDispatchError(
            "materialization_dispatch_disabled", retryable=True
        )
    else:
        failure = None
        try:
            # The protected workflow id is the exact signed PR attestation id,
            # not a browser-supplied commit or PR number.
            await dispatch_materialization_finalization(
                _dispatch_config(),
                request_id=payload.materialization_attestation_id,
                binding=binding,
            )
        except FoundryMaterializationDispatchError as exc:
            failure = exc
    if failure is not None:
        outcome_event = await record_finalization_dispatch(
            db,
            request_event=event,
            reservation=reservation,
            dispatched=False,
            failure_class=failure.code,
            retryable=failure.retryable,
            outcome_unknown=failure.outcome_unknown,
        )
        if failure.outcome_unknown:
            return {
                "status": "DISPATCH_OUTCOME_UNKNOWN",
                "dispatch_state": "DISPATCH_OUTCOME_UNKNOWN",
                "materialization_attestation_id": str(
                    payload.materialization_attestation_id
                ),
                "dispatch_attempt": reservation.attempt_number,
                "retry_after": materialization_workflow_retry_after(
                    outcome_event
                ).isoformat(),
                "outcome_unknown": True,
                "idempotent_replay": False,
            }
        raise HTTPException(
            status_code=503 if failure.retryable else 409,
            detail={
                "error_class": failure.code,
                "message": "Merge finalization dispatch failed; no new Candidate version was created",
                "retryable": failure.retryable,
            },
        )
    dispatch_event = await record_finalization_dispatch(
        db,
        request_event=event,
        reservation=reservation,
        dispatched=True,
    )
    return {
        "status": "DISPATCHED",
        "dispatch_state": "WORKFLOW_DISPATCHED",
        "materialization_attestation_id": str(payload.materialization_attestation_id),
        "dispatch_attempt": reservation.attempt_number,
        "retry_after": materialization_workflow_retry_after(
            dispatch_event
        ).isoformat(),
        "idempotent_replay": False,
    }


@internal_router.post(
    "/materialization/pr-attestations", status_code=status.HTTP_201_CREATED
)
async def ingest_materialization_pr_attestation(
    payload: SignedMaterializationBundle,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _enabled()
    _callback(request)
    try:
        row = await record_materialization_pr_attestation(
            db,
            attestation_bundle=payload.model_dump(mode="json"),
            expected_repository=settings.foundry_materialization_github_repository,
            expected_workflow=settings.foundry_materialization_github_workflow,
            trusted_public_keys=settings.foundry_materialization_attestation_verification_keyring,
        )
    except FoundryCatalogError as exc:
        _error(exc)
    return {
        "status": "DRAFT_PR_ATTESTED",
        "materialization_attestation_id": str(row.id),
        "candidate_id": str(row.candidate_id),
        "origin_candidate_version_id": str(row.origin_candidate_version_id),
        "pull_request_number": row.pull_request_number,
        "pull_request_state": row.pull_request_state,
        "pull_request_url": row.pull_request_url,
        "auto_merge_performed": False,
        "receipt_sha256": row.receipt_hash,
    }


@internal_router.post(
    "/materialization/final-receipts", status_code=status.HTTP_201_CREATED
)
async def ingest_materialization_final_receipt(
    payload: SignedMaterializationBundle,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _enabled()
    _callback(request)
    try:
        row, version = await record_materialization_final_receipt(
            db,
            attestation_bundle=payload.model_dump(mode="json"),
            expected_repository=settings.foundry_materialization_github_repository,
            expected_workflow=settings.foundry_materialization_finalize_github_workflow,
            trusted_public_keys=settings.foundry_materialization_attestation_verification_keyring,
        )
    except FoundryCatalogError as exc:
        _error(exc)
    return {
        "status": "FINALIZED_NEW_VERSION",
        "materialization_receipt_id": str(row.id),
        "candidate_id": str(row.candidate_id),
        "origin_candidate_version_id": str(row.origin_candidate_version_id),
        "candidate_version_id": str(version.id),
        "candidate_version_hash": version.version_hash,
        "candidate_status": "BUILDING",
        "demo_and_reviews_transferred": False,
        "validation_runner_image_digest": version.validation_runner_image_digest,
    }


@admin_router.get("/candidates/{candidate_id}/materializations")
async def list_candidate_materializations(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _reviewer: User = Depends(require_foundry_human_reviewer),
) -> dict[str, Any]:
    _enabled()
    attestations = list(
        (
            await db.execute(
                select(FoundryMaterializationAttestation)
                .where(FoundryMaterializationAttestation.candidate_id == candidate_id)
                .order_by(FoundryMaterializationAttestation.created_at.desc())
            )
        ).scalars().all()
    )
    receipts = list(
        (
            await db.execute(
                select(FoundryMaterializationReceipt)
                .where(FoundryMaterializationReceipt.candidate_id == candidate_id)
                .order_by(FoundryMaterializationReceipt.created_at.desc())
            )
        ).scalars().all()
    )
    return {
        "pull_requests": [
            {
                "materialization_attestation_id": str(row.id),
                "origin_candidate_version_id": str(row.origin_candidate_version_id),
                "origin_candidate_version_hash": row.origin_candidate_version_hash,
                "pull_request_number": row.pull_request_number,
                "pull_request_state": row.pull_request_state,
                "pull_request_url": row.pull_request_url,
                "pull_request_head_commit": row.pull_request_head_commit,
                "auto_merge_performed": False,
                "receipt_sha256": row.receipt_hash,
                "created_at": row.created_at.isoformat(),
            }
            for row in attestations
        ],
        "finalizations": [
            {
                "materialization_receipt_id": str(row.id),
                "origin_candidate_version_id": str(row.origin_candidate_version_id),
                "candidate_version_id": str(row.materialized_candidate_version_id),
                "pull_request_base_ref": row.pull_request_base_ref,
                "pull_request_head_repository": row.pull_request_head_repository,
                "merge_commit": row.merge_commit,
                "origin_main_commit": row.origin_main_commit,
                "merge_commit_is_ancestor_of_origin_main": (
                    row.merge_commit_is_ancestor_of_origin_main
                ),
                "merge_source_tree_sha256": row.merge_source_tree_hash,
                "validation_runner_image_digest": row.validation_runner_image_digest,
                "validation_sbom_sha256": row.validation_sbom_hash,
                "demo_and_reviews_transferred": False,
                "receipt_sha256": row.receipt_hash,
                "created_at": row.created_at.isoformat(),
            }
            for row in receipts
        ],
    }


__all__ = ["admin_router", "internal_router"]
