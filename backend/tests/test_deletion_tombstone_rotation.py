"""Deletion suppression remains discoverable across signing-key rotation."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.services import account_deletion


def test_v2_tombstone_survives_current_key_rotation(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_require_integrity", True)
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "objects"))
    monkeypatch.setattr(settings, "deletion_tombstone_key", "a" * 40)
    monkeypatch.setattr(settings, "deletion_tombstone_key_id", "delete-v1")
    monkeypatch.setattr(settings, "deletion_tombstone_verification_keys", "{}")
    user_id = uuid.uuid4()
    account_deletion.write_external_deletion_tombstone(
        user_id=user_id,
        receipt_hash="b" * 64,
        requested_at=datetime.now(timezone.utc),
    )

    monkeypatch.setattr(settings, "deletion_tombstone_key", "c" * 40)
    monkeypatch.setattr(settings, "deletion_tombstone_key_id", "delete-v2")
    monkeypatch.setattr(
        settings,
        "deletion_tombstone_verification_keys",
        json.dumps({"delete-v1": "a" * 40}),
    )
    account_deletion._EXTERNAL_TOMBSTONE_CACHE.clear()
    assert account_deletion.external_deletion_tombstone_exists(user_id) is True

    # A missing retired verification key is an operator incident, never
    # permission to resurrect the user: the stable v2 object still fails shut.
    monkeypatch.setattr(settings, "deletion_tombstone_verification_keys", "{}")
    account_deletion._EXTERNAL_TOMBSTONE_CACHE.clear()
    assert account_deletion.external_deletion_tombstone_exists(user_id) is True
