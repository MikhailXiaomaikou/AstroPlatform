"""Regression tests for the localhost-only demo artifact capability."""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

import pytest


DEMO_DIR = Path(__file__).resolve().parents[2] / "docs/demo/union3-local-worker"
sys.path.insert(0, str(DEMO_DIR))
capability_module = importlib.import_module("artifact_capability")
store_module = importlib.import_module("artifact_store")


def test_demo_artifact_capability_binds_every_upload_field():
    capability = capability_module.ArtifactCapability(
        key="science-attempts/user/attempt/uploads/result.json",
        sha256="a" * 64,
        size_bytes=123,
        content_type="application/json",
        expires_at=int(time.time()) + 300,
    )
    secret = "demo-secret-with-at-least-thirty-two-characters"
    signature = capability_module.sign_capability(secret, capability)

    assert capability_module.verify_signature(secret, capability, signature) is True
    assert (
        capability_module.verify_signature(
            secret,
            capability_module.ArtifactCapability(
                **{**capability.__dict__, "size_bytes": capability.size_bytes + 1}
            ),
            signature,
        )
        is False
    )


def test_demo_artifact_key_token_round_trip_and_rejects_invalid_base64():
    key = "science-attempts/user/attempt/uploads/chi2_profile.svg"

    assert capability_module.decode_key(capability_module.encode_key(key)) == key
    with pytest.raises(ValueError, match="invalid_artifact_key_token"):
        capability_module.decode_key("***")


def test_demo_artifact_store_rejects_traversal(tmp_path: Path):
    destination = store_module._safe_destination(tmp_path, "safe/result.json")
    assert destination == tmp_path / "safe/result.json"

    for unsafe in ("../escape", "/absolute", "safe/../escape", "safe\\escape"):
        with pytest.raises(ValueError, match="invalid_artifact_key"):
            store_module._safe_destination(tmp_path, unsafe)
