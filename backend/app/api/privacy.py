"""Explicit analytics consent and restore-safe account deletion APIs."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, hash_password, verify_password
from app.config import settings
from app.models.claim_audit_records import (
    AccountDeletionTombstone,
    ClaimAudit,
    PrivacyPreference,
)
from app.models.database import get_db
from app.models.research_records import ResearchJob
from app.models.schemas import PipelineRun, ScheduledRun, User, UserEvent
from app.services.account_deletion import (
    deletion_receipt_hash,
    deletion_user_fingerprint,
    purge_user_runtime_state,
    write_external_deletion_tombstone,
)
from app.services.event_collector import event_collector
from app.rate_limit import limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["privacy"])


class PrivacyPreferenceUpdate(BaseModel):
    analytics_enabled: bool


class DeleteAccountRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=255)
    password: str | None = Field(default=None, max_length=128)
    google_credential: str | None = Field(default=None, max_length=16_384)


def _serialize_preference(
    preference: PrivacyPreference | None,
) -> dict[str, object]:
    return {
        "analytics_enabled": bool(
            preference is not None and preference.analytics_enabled
        ),
        "consented_at": (
            preference.consented_at.isoformat()
            if preference is not None and preference.consented_at
            else None
        ),
        "retention_days": settings.product_analytics_retention_days,
        "research_records_retained_until_user_deletion": True,
    }


@router.get("/privacy/preferences")
async def get_privacy_preferences(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    preference = (
        await db.execute(
            select(PrivacyPreference).where(PrivacyPreference.user_id == user.id)
        )
    ).scalar_one_or_none()
    return _serialize_preference(preference)


@router.put("/privacy/preferences")
async def put_privacy_preferences(
    request: PrivacyPreferenceUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user_id = user.id
    preference = (
        await db.execute(
            select(PrivacyPreference)
            .where(PrivacyPreference.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if preference is None:
        preference = PrivacyPreference(
            user_id=user_id,
            analytics_enabled=request.analytics_enabled,
            consented_at=now if request.analytics_enabled else None,
        )
        db.add(preference)
        try:
            await db.flush()
        except IntegrityError:
            # Two first-time requests can both observe an absent row. The
            # unique user_id is authoritative; after the winner commits,
            # retry the update under a row lock instead of returning 500.
            await db.rollback()
            preference = (
                await db.execute(
                    select(PrivacyPreference)
                    .where(PrivacyPreference.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one()
            preference.analytics_enabled = request.analytics_enabled
            preference.consented_at = now if request.analytics_enabled else None
    else:
        preference.analytics_enabled = request.analytics_enabled
        preference.consented_at = now if request.analytics_enabled else None
    if not request.analytics_enabled:
        await db.execute(delete(UserEvent).where(UserEvent.user_id == user_id))
    await db.commit()
    await db.refresh(preference)
    await event_collector.invalidate_consent(user_id)
    return _serialize_preference(preference)


async def _reauthenticate_for_deletion(
    request: DeleteAccountRequest,
    user: User,
) -> None:
    if request.password and verify_password(request.password, user.password_hash):
        return
    if request.google_credential and user.google_id:
        from app.api.auth import _verify_google_token

        payload = await _verify_google_token(request.google_credential)
        if str(payload.get("sub") or "") == user.google_id:
            return
    raise HTTPException(
        status_code=403,
        detail="Account deletion confirmation or reauthentication failed",
    )


def _dispatch_account_erasure(user_id: str, tombstone_id: str) -> bool:
    try:
        from app.tasks.privacy_tasks import erase_account_task

        erase_account_task.delay(user_id, tombstone_id)
        return True
    except Exception:
        logger.exception(
            "Account erasure dispatch failed; periodic reconciliation will retry"
        )
        return False


@router.delete("/auth/account", status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("3/hour")
async def delete_account(
    request: Request,
    payload: DeleteAccountRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Disable login now, then erase connected records asynchronously."""

    if payload.confirmation.strip() != user.username:
        raise HTTPException(
            status_code=403,
            detail="Account deletion confirmation or reauthentication failed",
        )
    await _reauthenticate_for_deletion(payload, user)

    locked_user = (
        await db.execute(
            select(User)
            .where(User.id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked_user is None or str(locked_user.account_status or "").upper() != "ACTIVE":
        raise HTTPException(status_code=409, detail="Account deletion is already in progress")
    user = locked_user

    requested_at = datetime.now(timezone.utc)
    receipt = f"delete_{secrets.token_urlsafe(24)}"
    receipt_hash = deletion_receipt_hash(receipt)
    fingerprint = deletion_user_fingerprint(user.id)
    try:
        write_external_deletion_tombstone(
            user_id=user.id,
            receipt_hash=receipt_hash,
            requested_at=requested_at,
        )
    except Exception as exc:
        logger.exception("Could not persist restore-safe deletion tombstone")
        raise HTTPException(
            status_code=503,
            detail="Account deletion could not be safely scheduled; try again",
        ) from exc

    tombstone = AccountDeletionTombstone(
        user_fingerprint=fingerprint,
        status="PENDING",
        receipt_hash=receipt_hash,
        requested_at=requested_at,
        backup_expires_at=requested_at + timedelta(days=30),
    )
    db.add(tombstone)
    user.account_status = "DELETION_PENDING"
    user.deletion_requested_at = requested_at
    user.api_keys = None
    user.anthropic_api_key = None
    user.stripe_customer_id = None
    user.google_id = None
    # Destroy the reusable password credential before returning. The account
    # can no longer authenticate even in a service missing the status check.
    user.password_hash = hash_password(secrets.token_urlsafe(48))
    await db.execute(delete(UserEvent).where(UserEvent.user_id == user.id))
    await db.execute(
        update(ResearchJob)
        .where(
            ResearchJob.user_id == user.id,
            ResearchJob.status.in_(["queued", "running"]),
        )
        .values(
            status="cancelled",
            error_class="account_deletion_requested",
            error="Account deletion requested",
            completed_at=requested_at,
            updated_at=requested_at,
        )
    )
    await db.execute(
        update(ClaimAudit)
        .where(
            ClaimAudit.user_id == user.id,
            ClaimAudit.lifecycle_status.in_(["QUEUED", "RUNNING"]),
        )
        .values(
            lifecycle_status="CANCELLED",
            scientific_verdict=None,
            error_class="account_deletion_requested",
            error="Account deletion requested",
            worker_lease_id=None,
            lease_expires_at=None,
            completed_at=requested_at,
            updated_at=requested_at,
        )
    )
    await db.execute(
        update(PipelineRun)
        .where(
            PipelineRun.user_id == user.id,
            PipelineRun.status.in_(["pending", "running"]),
        )
        .values(
            status="cancelled",
            results={
                "success": False,
                "error_class": "account_deletion_requested",
                "error": "Account deletion requested",
            },
            completed_at=requested_at,
        )
    )
    await db.execute(
        update(ScheduledRun)
        .where(ScheduledRun.user_id == user.id, ScheduledRun.enabled.is_(True))
        .values(enabled=False, next_run_at=None)
    )
    await db.flush()
    # Commit the durable deletion request before touching shared runtime KV.
    # If Redis is unavailable, the external tombstone already suppresses late
    # worker writes and the reconciler can retry from this PENDING record.
    # Keeping the purge inside this transaction would instead roll back the
    # only database work item while leaving the external tombstone in place.
    await db.commit()
    await db.refresh(tombstone)
    try:
        await purge_user_runtime_state(
            db,
            user_id=user.id,
            strict_pipeline_cache=True,
        )
    except Exception:
        logger.exception(
            "Initial runtime purge failed; durable account erasure will retry it"
        )
    await event_collector.invalidate_consent(user.id)

    scheduled = _dispatch_account_erasure(str(user.id), str(tombstone.id))
    return {
        "status": "DELETION_PENDING",
        "receipt": receipt,
        "cleanup_scheduled": scheduled,
        "backup_expiry": tombstone.backup_expires_at.isoformat(),
    }
