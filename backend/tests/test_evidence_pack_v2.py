"""Public-key Evidence Pack v2 integrity and trust regressions."""

from __future__ import annotations

import base64
import io
import uuid
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import settings
from app.models.claim_audit_records import ClaimAudit
from app.services.evidence_pack_v2 import (
    EvidencePackV2Error,
    build_evidence_pack_v2,
    encode_public_key,
    jcs_canonicalize,
    public_key_fingerprint,
    verify_evidence_pack_v2,
)


def _private_text(key: Ed25519PrivateKey) -> str:
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(raw).decode("ascii")


def _files() -> dict:
    return {
        "report.md": "# Union3 reproduction\n\nNot a new discovery.\n",
        "citations.bib": "@article{union3, title={Union Through UNITY}}\n",
        "provenance.json": {"commit": "1" * 40},
        "source_snapshot.json": {"canonical_identifier": "2311.12098v4"},
        "anchors.json": [{"anchor_id": "sha256:" + "2" * 64, "table": "9"}],
        "claims.json": [
            {
                "parameter": "omegam",
                "central": "0.356",
                "interval_kind": "frequentist_profile_chi_square",
            }
        ],
        "primary_analysis.json": {"best_fit": "0.35592440"},
        "independent_analysis.json": {"best_fit": "0.35592442"},
        "diagnostics.json": {"r_hat": "not_applicable", "all_gates_passed": True},
        "reviews.json": [{"decision": "APPROVED", "reviewer": "reviewer"}],
        "limitations.json": {
            "publication_ready": False,
            "cannot_measure": ["H0"],
        },
    }


def _manifest_fields() -> dict:
    return {
        "audit_id": "audit-1",
        "owner": "owner-1",
        "scientific_verdict": "SUPPORTED",
        "claim_scope": "reproduction_of_published_constraint",
        "reproduction_ready": True,
        "publication_ready": False,
        "finalized_at": "2026-07-20T00:00:00+00:00",
    }


def _key_record(key: Ed25519PrivateKey, *, key_id: str, status: str = "active") -> dict:
    public = key.public_key()
    record = {
        "key_id": key_id,
        "algorithm": "ed25519",
        "public_key": encode_public_key(public),
        "fingerprint": public_key_fingerprint(public),
        "status": status,
    }
    if status == "retired":
        record.update(
            {
                "not_before": "2026-01-01T00:00:00+00:00",
                "not_after": "2026-12-31T23:59:59+00:00",
            }
        )
    return record


def _replace_zip_entry(pack: bytes, name: str, replacement: bytes) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(pack), "r")
    output = io.BytesIO()
    with (
        source,
        zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            target.writestr(
                info.filename,
                replacement if info.filename == name else source.read(info),
            )
    return output.getvalue()


def test_pack_is_deterministic_and_verifies_against_external_keyring():
    key = Ed25519PrivateKey.generate()
    kwargs = {
        "manifest_fields": _manifest_fields(),
        "files": _files(),
        "signing_private_key": _private_text(key),
        "key_id": "evidence-2026-01",
    }
    pack_one, manifest, pack_hash_one = build_evidence_pack_v2(**kwargs)
    pack_two, _, pack_hash_two = build_evidence_pack_v2(**kwargs)

    assert pack_one == pack_two
    assert pack_hash_one == pack_hash_two
    assert manifest["publication_ready"] is False
    assert "public_key" not in manifest
    verified = verify_evidence_pack_v2(
        pack_one,
        trusted_keyring={"keys": [_key_record(key, key_id="evidence-2026-01")]},
    )
    assert verified.valid is True
    assert verified.code == "ok"
    assert verified.manifest == manifest


def test_modifying_any_scientific_file_fails_hash_verification():
    key = Ed25519PrivateKey.generate()
    pack, _, _ = build_evidence_pack_v2(
        manifest_fields=_manifest_fields(),
        files=_files(),
        signing_private_key=_private_text(key),
        key_id="current",
    )
    tampered = _replace_zip_entry(pack, "primary_analysis.json", b'{"best_fit":"0.8"}')
    result = verify_evidence_pack_v2(
        tampered,
        trusted_keyring=[_key_record(key, key_id="current")],
    )
    assert result.valid is False
    assert result.code in {
        "evidence_v2_file_hash_mismatch",
        "evidence_v2_file_size_mismatch",
    }


def test_pack_does_not_trust_a_substituted_public_key():
    signer = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    pack, _, _ = build_evidence_pack_v2(
        manifest_fields=_manifest_fields(),
        files=_files(),
        signing_private_key=_private_text(signer),
        key_id="current",
    )
    result = verify_evidence_pack_v2(
        pack,
        trusted_keyring=[_key_record(attacker, key_id="current")],
    )
    assert result.valid is False
    assert result.code == "evidence_v2_pack_fingerprint_mismatch"


def test_retired_key_verifies_old_pack_but_revoked_key_is_not_trusted():
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    pack, _, _ = build_evidence_pack_v2(
        manifest_fields=_manifest_fields(),
        files=_files(),
        signing_private_key=_private_text(old_key),
        key_id="old",
    )
    retired = verify_evidence_pack_v2(
        pack,
        trusted_keyring=[
            _key_record(old_key, key_id="old", status="retired"),
            _key_record(new_key, key_id="current"),
        ],
    )
    assert retired.valid is True
    assert retired.key_status == "retired"

    revoked = verify_evidence_pack_v2(
        pack,
        trusted_keyring=[_key_record(old_key, key_id="old", status="revoked")],
    )
    assert revoked.valid is False
    assert revoked.code == "evidence_v2_key_revoked"
    assert revoked.manifest is not None


def test_jcs_subset_rejects_float_scientific_values():
    with pytest.raises(EvidencePackV2Error, match="jcs_float_forbidden"):
        jcs_canonicalize({"omegam": 0.356})


@pytest.mark.parametrize(
    "replacement",
    [
        b"[]",
        b'{"files":[{"path":"report.md","size_bytes":"not-an-int"}]}',
    ],
)
def test_verifier_is_total_for_hostile_manifest_shapes(replacement):
    key = Ed25519PrivateKey.generate()
    pack, _, _ = build_evidence_pack_v2(
        manifest_fields=_manifest_fields(),
        files=_files(),
        signing_private_key=_private_text(key),
        key_id="current",
    )
    hostile = _replace_zip_entry(pack, "manifest.json", replacement)
    result = verify_evidence_pack_v2(
        hostile,
        trusted_keyring=[_key_record(key, key_id="current")],
    )
    assert result.valid is False


def test_key_validity_window_is_enforced_against_manifest_finalization_time():
    key = Ed25519PrivateKey.generate()
    pack, _, _ = build_evidence_pack_v2(
        manifest_fields=_manifest_fields(),
        files=_files(),
        signing_private_key=_private_text(key),
        key_id="future",
    )
    record = _key_record(key, key_id="future")
    record.update(
        {
            "not_before": "2099-01-01T00:00:00+00:00",
            "not_after": "2100-01-01T00:00:00+00:00",
        }
    )
    result = verify_evidence_pack_v2(pack, trusted_keyring=[record])
    assert result.valid is False
    assert result.code == "evidence_v2_key_not_yet_valid"


def test_retired_key_requires_explicit_historical_signing_window():
    key = Ed25519PrivateKey.generate()
    pack, _, _ = build_evidence_pack_v2(
        manifest_fields=_manifest_fields(),
        files=_files(),
        signing_private_key=_private_text(key),
        key_id="old",
    )
    record = _key_record(key, key_id="old")
    record["status"] = "retired"
    result = verify_evidence_pack_v2(pack, trusted_keyring=[record])
    assert result.valid is False
    assert result.code == "evidence_v2_retired_key_window_required"


async def test_authenticated_file_verifier_detects_v2_without_pack_id(
    app_client,
    db_session,
    test_user,
    monkeypatch,
):
    owner, token = test_user
    audit_id = uuid.uuid4()
    audit = ClaimAudit(
        id=audit_id,
        user_id=owner.id,
        request_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        lifecycle_status="COMPLETED",
        scientific_verdict="SUPPORTED",
        mode="execute_registered",
        claim_text="Registered Union3 reproduction",
        source_kind="arxiv",
        source_value="2311.12098v4",
    )
    db_session.add(audit)
    await db_session.commit()

    key = Ed25519PrivateKey.generate()
    public_key = encode_public_key(key.public_key())
    monkeypatch.setattr(settings, "claim_audit_enabled", True)
    monkeypatch.setattr(settings, "evidence_v2_signing_key_id", "current")
    monkeypatch.setattr(settings, "evidence_v2_signing_public_key", public_key)
    pack, _, _ = build_evidence_pack_v2(
        manifest_fields={**_manifest_fields(), "audit_id": str(audit_id)},
        files=_files(),
        signing_private_key=_private_text(key),
        key_id="current",
    )
    response = await app_client.post(
        "/api/research/evidence-packs/verify",
        files={"file": ("pack.zip", pack, "application/zip")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["valid"] is True
    assert response.json()["schema_version"] == 2
    assert response.json()["audit_id"] == str(audit_id)
