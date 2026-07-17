"""Key rotation and legacy compatibility for server-signed paper evidence."""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.services import server_evidence


def _record() -> dict:
    return server_evidence.build_server_evidence_record(
        session_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        run_id="rotation-test",
        assistant_reply="A signed result.",
        tool_results=[{"tool": "search_objects", "result": {"value": 1.0}}],
    )


def _verifies(record: dict) -> bool:
    return server_evidence.verify_server_evidence_record(
        record,
        session_id=record["session_id"],
        owner_id=record["owner_id"],
    )


def test_current_evidence_key_signs_schema_v2_record(monkeypatch):
    monkeypatch.setattr(settings, "evidence_signing_key", "current-secret")
    monkeypatch.setattr(settings, "evidence_signing_key_id", "current-v2")
    monkeypatch.setattr(settings, "evidence_verification_keys", "{}")

    record = _record()

    assert record["schema_version"] == server_evidence.SERVER_EVIDENCE_SCHEMA_VERSION
    assert record["key_id"] == "current-v2"
    assert _verifies(record)


def test_retired_keyring_verifies_old_record_and_rejects_unknown_id(monkeypatch):
    monkeypatch.setattr(settings, "evidence_signing_key", "old-secret")
    monkeypatch.setattr(settings, "evidence_signing_key_id", "evidence-2026-01")
    monkeypatch.setattr(settings, "evidence_verification_keys", "{}")
    old_record = _record()

    monkeypatch.setattr(settings, "evidence_signing_key", "new-secret")
    monkeypatch.setattr(settings, "evidence_signing_key_id", "evidence-2026-07")
    monkeypatch.setattr(
        settings,
        "evidence_verification_keys",
        '{"evidence-2026-01":"old-secret"}',
    )
    assert _verifies(old_record)

    unknown = dict(old_record)
    unknown["key_id"] = "unknown-key"
    assert not _verifies(unknown)


def test_legacy_jwt_signed_record_survives_jwt_rotation_via_keyring(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "legacy-jwt-secret")
    monkeypatch.setattr(settings, "evidence_verification_keys", "{}")
    record = _record()
    record["schema_version"] = server_evidence.LEGACY_SERVER_EVIDENCE_SCHEMA_VERSION
    record.pop("key_id")
    record.pop("signature")
    record["signature"] = server_evidence._record_signature(
        record, key="legacy-jwt-secret"
    )
    assert _verifies(record)

    monkeypatch.setattr(settings, "jwt_secret", "rotated-jwt-secret")
    monkeypatch.setattr(
        settings,
        "evidence_verification_keys",
        '{"legacy-jwt-2026-07":"legacy-jwt-secret"}',
    )
    assert _verifies(record)


def test_unknown_schema_and_keyless_v2_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "evidence_signing_key", "current-secret")
    monkeypatch.setattr(settings, "evidence_signing_key_id", "current-v2")
    monkeypatch.setattr(settings, "evidence_verification_keys", "{}")
    record = _record()

    unknown_schema = dict(record)
    unknown_schema["schema_version"] = 999
    assert not _verifies(unknown_schema)

    keyless = dict(record)
    keyless.pop("key_id")
    assert not _verifies(keyless)


def test_production_requires_independent_evidence_signing_key(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "_ENV", "production")
    with pytest.raises(ValueError, match="EVIDENCE_SIGNING_KEY must be set"):
        config.Settings(
            jwt_secret="jwt-secret",
            fernet_key="fernet-secret",
            deletion_tombstone_key="deletion-tombstone-key-at-least-32-bytes",
            deletion_tombstone_key_id="deletion-v1",
            signup_mode="closed",
            claim_audit_execution_mode="celery",
            evidence_signing_key="",
            evidence_signing_key_id="",
            sandbox_backend="disabled",
            privacy_operator_name="Test Operator",
            privacy_contact="privacy@example.invalid",
            privacy_jurisdiction="Test Jurisdiction",
        )

    with pytest.raises(ValueError, match="independent from JWT_SECRET"):
        config.Settings(
            jwt_secret="shared-secret",
            fernet_key="fernet-secret",
            deletion_tombstone_key="deletion-tombstone-key-at-least-32-bytes",
            deletion_tombstone_key_id="deletion-v1",
            signup_mode="closed",
            claim_audit_execution_mode="celery",
            evidence_signing_key="shared-secret",
            evidence_signing_key_id="evidence-v1",
            sandbox_backend="disabled",
            privacy_operator_name="Test Operator",
            privacy_contact="privacy@example.invalid",
            privacy_jurisdiction="Test Jurisdiction",
        )


def test_malformed_verification_keyring_fails_closed(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "_ENV", "production")
    with pytest.raises(ValueError, match="EVIDENCE_VERIFICATION_KEYS"):
        config.Settings(
            jwt_secret="jwt-secret",
            fernet_key="fernet-secret",
            deletion_tombstone_key="deletion-tombstone-key-at-least-32-bytes",
            deletion_tombstone_key_id="deletion-v1",
            signup_mode="closed",
            claim_audit_execution_mode="celery",
            evidence_signing_key="evidence-secret",
            evidence_signing_key_id="evidence-v1",
            evidence_verification_keys="not-json",
            sandbox_backend="disabled",
            privacy_operator_name="Test Operator",
            privacy_contact="privacy@example.invalid",
            privacy_jurisdiction="Test Jurisdiction",
        )


def test_production_rejects_weak_evidence_signing_key(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "_ENV", "production")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        config.Settings(
            jwt_secret="independent-jwt-secret",
            fernet_key="fernet-secret",
            deletion_tombstone_key="deletion-tombstone-key-at-least-32-bytes",
            deletion_tombstone_key_id="deletion-v1",
            signup_mode="closed",
            claim_audit_execution_mode="celery",
            evidence_signing_key="too-short",
            evidence_signing_key_id="evidence-v1",
            sandbox_backend="disabled",
            privacy_operator_name="Test Operator",
            privacy_contact="privacy@example.invalid",
            privacy_jurisdiction="Test Jurisdiction",
        )
