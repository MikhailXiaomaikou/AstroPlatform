"""Fail-closed persistence and value scrubbing for opt-in product analytics."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.claim_audit_records import PrivacyPreference
from app.ai.inference_router import (
    INFERENCE_ERROR_CLASSES,
    InferenceRouter,
    classify_inference_error,
)
from app.models.schemas import InferenceLog, User, UserEvent
from app.services import event_collector as ec
from app.services.event_collector import (
    purge_expired_inference_logs,
    scrub_event_data,
    scrub_page,
)
from app.auth import hash_password


def _production_settings_kwargs() -> dict:
    return {
        "jwt_secret": "jwt-secret-independent-from-other-keys",
        "fernet_key": "fernet-secret-at-least-thirty-two-bytes",
        "deletion_tombstone_key": "deletion-secret-at-least-thirty-two-bytes",
        "deletion_tombstone_key_id": "deletion-v1",
        "evidence_signing_key": "evidence-secret-at-least-thirty-two-bytes",
        "evidence_signing_key_id": "evidence-v1",
        "claim_audit_execution_mode": "celery",
        "sandbox_backend": "disabled",
    }


def test_production_privacy_configuration_fails_closed(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "_ENV", "production")
    with pytest.raises(ValueError, match="retention cannot exceed 30 days"):
        config.Settings(
            **_production_settings_kwargs(),
            signup_mode="closed",
            product_analytics_retention_days=31,
        )
    with pytest.raises(ValueError, match="ADMIN_SECRET"):
        config.Settings(
            **_production_settings_kwargs(),
            signup_mode="invite_only",
            admin_secret="",
        )
    with pytest.raises(ValueError, match="PRIVACY_OPERATOR_NAME"):
        config.Settings(
            **_production_settings_kwargs(),
            signup_mode="invite_only",
            admin_secret="admin-secret",
            privacy_operator_name="",
            privacy_contact="",
            privacy_jurisdiction="",
        )
    with pytest.raises(ValueError, match="PRIVACY_OPERATOR_NAME"):
        config.Settings(
            **_production_settings_kwargs(),
            signup_mode="closed",
            privacy_operator_name="",
            privacy_contact="",
            privacy_jurisdiction="",
        )


def test_allowed_field_names_cannot_smuggle_research_values():
    assert scrub_event_data(
        "claim_audit.failed",
        {
            "tool_count_bucket": "H0=73.04",
            "outcome_bucket": "10.1234/private",
            "source_kind": "https://example.invalid/paper",
            "execution_mode": "Omega_m=0.3",
            "error_class": "https://example.invalid/private",
            "status_code": 67.4,
        },
    ) == {"error_class": "internal"}
    assert scrub_event_data(
        "claim_audit.completed",
        {
            "tool_count_bucket": "1-3",
            "outcome_bucket": "supported",
            "source_kind": "doi",
            "execution_mode": "audit_only",
            "retryable": False,
        },
    ) == {
        "tool_count_bucket": "1-3",
        "outcome_bucket": "supported",
        "source_kind": "doi",
        "execution_mode": "audit_only",
        "retryable": False,
    }
    assert scrub_page("/claim-audit") == "/claim-audit"
    assert scrub_page("/private-paper/title") is None
    assert scrub_page("/hubble/tension") is None


def test_inference_failures_use_only_finite_error_classes():
    assert classify_inference_error(TimeoutError("secret URL")) == "provider_timeout"
    request = httpx.Request("GET", "https://private.invalid/paper?doi=secret")
    response = httpx.Response(429, request=request)
    error = httpx.HTTPStatusError("raw private response", request=request, response=response)
    assert classify_inference_error(error) == "provider_rate_limited"
    assert classify_inference_error(RuntimeError("claim H0=73.04")) == "provider_error"
    assert "claim H0=73.04" not in INFERENCE_ERROR_CLASSES


async def test_inference_log_rejects_arbitrary_error_text(db_session, monkeypatch):
    session_factory = async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    import app.ai.inference_router as inference_module

    monkeypatch.setattr(inference_module, "async_session", session_factory)
    await InferenceRouter().log_inference(
        "orchestrator",
        "openai",
        {},
        success=False,
        latency_ms=1,
        error_class="https://private.invalid/?doi=10.1234/secret H0=73.04",
    )
    row = await db_session.scalar(select(InferenceLog))
    assert row is not None
    assert row.error == "provider_error"


async def test_inference_metrics_retention_is_bounded(db_session):
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            InferenceLog(
                agent_name="orchestrator",
                backend_name="openai",
                success=False,
                error="provider_error",
                timestamp=now - timedelta(days=31),
            ),
            InferenceLog(
                agent_name="orchestrator",
                backend_name="openai",
                success=True,
                timestamp=now - timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()
    assert await purge_expired_inference_logs(retention_days=30, db=db_session) == 1
    remaining = (await db_session.execute(select(InferenceLog))).scalars().all()
    assert len(remaining) == 1


async def test_flush_rechecks_database_consent_even_after_verified_buffering(
    db_session: AsyncSession,
    monkeypatch,
):
    session_factory = async_sessionmaker(
        db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(ec, "async_session", session_factory)
    collector = ec.EventCollector(flush_size=100)
    user = User(
        id=uuid.uuid4(),
        username="analytics-race-user",
        email="analytics-race-user@example.invalid",
        password_hash=hash_password("securepassword123"),
    )
    db_session.add(user)
    await db_session.commit()

    # Even a caller claiming it already verified consent cannot persist when
    # the authoritative row is absent.
    await collector.track(
        "session.started", {}, user_id=user.id, consent_verified=True
    )
    await collector.flush()
    assert await db_session.scalar(select(UserEvent.id)) is None

    preference = PrivacyPreference(user_id=user.id, analytics_enabled=True)
    db_session.add(preference)
    await db_session.commit()
    await collector.track(
        "session.started", {}, user_id=user.id, consent_verified=True
    )
    await collector.flush()
    assert await db_session.scalar(select(UserEvent.id)) is not None

    await db_session.execute(
        UserEvent.__table__.delete().where(UserEvent.user_id == user.id)
    )
    preference.analytics_enabled = False
    await db_session.commit()
    # Simulate a stale cache/other process that still buffers after opt-out.
    await collector.track(
        "session.started", {}, user_id=user.id, consent_verified=True
    )
    await collector.flush()
    assert await db_session.scalar(select(UserEvent.id)) is None
