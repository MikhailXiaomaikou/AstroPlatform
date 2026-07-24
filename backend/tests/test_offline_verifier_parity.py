"""The vendored offline verifier must never drift from the source module."""

from __future__ import annotations

import base64
import importlib.util
import sys
import zipfile
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services import evidence_pack_v2 as source_module

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VENDORED_PATH = _REPO_ROOT / "scripts" / "verify_evidence_pack.py"
_BEGIN = "# === BEGIN VENDORED evidence_pack_v2 ===\n"
_END = "# === END VENDORED evidence_pack_v2 ===\n"


def _vendored_module():
    spec = importlib.util.spec_from_file_location(
        "vendored_verify_evidence_pack", _VENDORED_PATH
    )
    module = importlib.util.module_from_spec(spec)
    # dataclass string-annotation resolution requires the module to be
    # registered in sys.modules during exec (its own name, not the app name).
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_vendored_section_is_byte_identical_to_source():
    # The strongest sync guarantee available: the marked section IS the
    # source file. Any edit to either side without re-vendoring fails here.
    vendored_text = _VENDORED_PATH.read_text()
    assert _BEGIN in vendored_text and _END in vendored_text
    section = vendored_text.split(_BEGIN, 1)[1].split(_END, 1)[0]
    source_text = (
        _REPO_ROOT / "backend" / "app" / "services" / "evidence_pack_v2.py"
    ).read_text()
    assert section == source_text


def _pack_and_keyrings():
    """Build one valid pack plus keyring variants with the SOURCE module.

    Reuses the canonical fixtures from tests/test_evidence_pack_v2.py so the
    parity cases stay aligned with the source module's own test corpus.
    """
    from tests.test_evidence_pack_v2 import (
        _files,
        _key_record,
        _manifest_fields,
        _private_text,
    )

    key = Ed25519PrivateKey.generate()
    pack, _manifest, _pack_hash = source_module.build_evidence_pack_v2(
        manifest_fields=_manifest_fields(),
        files=_files(),
        signing_private_key=_private_text(key),
        key_id="parity-key",
    )

    def keyring(status="active", **extra):
        record = _key_record(key, key_id="parity-key", status=status)
        record.update(extra)
        return {"keys": [record]}

    other_key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    return {
        "valid": (pack, keyring()),
        "tampered_member": (_tamper(pack), keyring()),
        "untrusted_key": (
            pack,
            {"keys": [_key_record(other_key, key_id="parity-key")]},
        ),
        "revoked_key": (pack, keyring(status="revoked")),
        "retired_outside_window": (
            pack,
            keyring(
                status="retired",
                not_before=(now - timedelta(days=730)).isoformat(),
                not_after=(now - timedelta(days=365)).isoformat(),
            ),
        ),
        "empty_keyring": (pack, {"keys": []}),
        "truncated_archive": (pack[: len(pack) // 2], keyring()),
        "oversize_archive": (
            pack + b"\0" * (source_module.EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES + 1),
            keyring(),
        ),
    }


def _tamper(pack: bytes) -> bytes:
    """Rewrite one member's bytes without re-signing."""
    src = zipfile.ZipFile(io.BytesIO(pack))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "report.md":
                data = data + b" tampered"
            dst.writestr(item, data)
    return out.getvalue()


def test_vendored_verifier_matches_source_verdicts():
    vendored = _vendored_module()
    cases = _pack_and_keyrings()
    for name, (pack_bytes, keyring) in cases.items():
        source_result = source_module.verify_evidence_pack_v2(
            pack_bytes, trusted_keyring=keyring
        )
        vendored_result = vendored.verify_evidence_pack_v2(
            pack_bytes, trusted_keyring=keyring
        )
        assert (vendored_result.valid, vendored_result.code) == (
            source_result.valid,
            source_result.code,
        ), name
    valid_pack, valid_keyring = cases["valid"]
    assert source_module.verify_evidence_pack_v2(
        valid_pack, trusted_keyring=valid_keyring
    ).valid is True
    assert source_module.verify_evidence_pack_v2(
        cases["tampered_member"][0], trusted_keyring=valid_keyring
    ).valid is False


def test_vendored_constants_match_source():
    vendored = _vendored_module()
    for constant in (
        "EVIDENCE_PACK_V2_SCHEMA_VERSION",
        "EVIDENCE_PACK_V2_REQUIRED_FILES",
        "EVIDENCE_PACK_V2_OPTIONAL_FILES",
        "EVIDENCE_PACK_V2_ARCHIVE_FILES",
        "EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES",
        "EVIDENCE_PACK_V2_MAX_EXPANDED_BYTES",
    ):
        assert getattr(vendored, constant) == getattr(source_module, constant), constant


def test_committed_keyring_parses_and_carries_no_secrets():
    keyring_path = _REPO_ROOT / "keys" / "evidence-keyring.json"
    text = keyring_path.read_text()
    parsed = source_module.parse_evidence_keyring(text)
    assert isinstance(parsed, dict)
    lowered = text.lower()
    assert "private" not in lowered.replace("private or secret material", "")
    assert "seed" not in lowered


def test_keygen_script_output_is_accepted_by_both_sides(tmp_path):
    import json
    import subprocess

    script = _REPO_ROOT / "backend" / "scripts" / "ops" / "generate_evidence_v2_keypair.py"
    before = sorted(p.name for p in tmp_path.iterdir())
    completed = subprocess.run(
        [sys.executable, str(script), "--key-id", "keygen-test"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=True,
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == before, "script must not write files"

    out = completed.stdout
    record = json.loads(out[out.index("{"):])
    keyring = source_module.parse_evidence_keyring(
        json.dumps({"schema_version": 1, "keys": [record]})
    )
    assert keyring, "generated public record must parse into a usable keyring"

    seed_line = next(
        line for line in out.splitlines()
        if line.startswith("EVIDENCE_V2_SIGNING_PRIVATE_KEY=")
    )
    seed_b64 = seed_line.split("=", 1)[1]
    # The printed private seed must satisfy the config-side decoder contract
    # (32-byte raw Ed25519 seed, base64).
    assert len(base64.b64decode(seed_b64)) == 32
