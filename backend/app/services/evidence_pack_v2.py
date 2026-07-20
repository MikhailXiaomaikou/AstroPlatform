"""Public-key Evidence Pack v2 construction and offline verification.

The pack proves origin and integrity, not scientific truth. Scientific support
is only recorded after the independent verifier and append-only human review
have both passed their separate gates.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

EVIDENCE_PACK_V2_SCHEMA_VERSION = 2
EVIDENCE_PACK_V2_SIGNATURE_ALGORITHM = "ed25519"
EVIDENCE_PACK_V2_REQUIRED_FILES = frozenset(
    {
        "report.md",
        "citations.bib",
        "provenance.json",
        "source_snapshot.json",
        "anchors.json",
        "claims.json",
        "primary_analysis.json",
        "independent_analysis.json",
        "diagnostics.json",
        "reviews.json",
        "limitations.json",
    }
)
EVIDENCE_PACK_V2_OPTIONAL_FILES = frozenset({"chi2_profile.svg"})
EVIDENCE_PACK_V2_ARCHIVE_FILES = (
    EVIDENCE_PACK_V2_REQUIRED_FILES | EVIDENCE_PACK_V2_OPTIONAL_FILES | {
    "manifest.json",
    "manifest.sig",
    }
)
EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
EVIDENCE_PACK_V2_MAX_EXPANDED_BYTES = 50 * 1024 * 1024
_MAX_SAFE_JSON_INTEGER = 2**53 - 1


class EvidencePackV2Error(ValueError):
    """A deterministic Evidence Pack v2 construction or verification error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EvidencePackV2Verification:
    valid: bool
    code: str
    manifest: dict[str, Any] | None = None
    key_id: str | None = None
    key_status: str | None = None


def _utf16_sort_key(value: str) -> bytes:
    try:
        return value.encode("utf-16be")
    except UnicodeEncodeError as exc:
        raise EvidencePackV2Error("jcs_invalid_unicode") from exc


def _jcs_string(value: str) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return encoded.encode("utf-8")
    except (UnicodeEncodeError, ValueError) as exc:
        raise EvidencePackV2Error("jcs_invalid_string") from exc


def jcs_canonicalize(value: Any) -> bytes:
    """Canonicalize the exact-number JSON subset used by Evidence Pack v2.

    RFC 8785 relies on ECMAScript's IEEE-754 number serialization. Scientific
    decimal values in this project are strings, so manifests intentionally
    reject floats and unsafe integers instead of silently rounding them.
    """

    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, str):
        return _jcs_string(value)
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > _MAX_SAFE_JSON_INTEGER:
            raise EvidencePackV2Error("jcs_unsafe_integer")
        return str(value).encode("ascii")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidencePackV2Error("jcs_non_finite_number")
        raise EvidencePackV2Error("jcs_float_forbidden_use_decimal_string")
    if isinstance(value, (list, tuple)):
        return b"[" + b",".join(jcs_canonicalize(item) for item in value) + b"]"
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise EvidencePackV2Error("jcs_object_key_not_string")
        pairs = []
        for key in sorted(value, key=_utf16_sort_key):
            pairs.append(_jcs_string(key) + b":" + jcs_canonicalize(value[key]))
        return b"{" + b",".join(pairs) + b"}"
    raise EvidencePackV2Error("jcs_unsupported_type")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_trust_timestamp(value: Any, *, code: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise EvidencePackV2Error(code)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidencePackV2Error(code) from exc
    if parsed.tzinfo is None:
        raise EvidencePackV2Error(code)
    return parsed.astimezone(timezone.utc)


def _decode_private_key(value: str) -> Ed25519PrivateKey:
    try:
        raw = base64.b64decode(str(value).strip(), validate=True)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (TypeError, ValueError) as exc:
        raise EvidencePackV2Error("evidence_v2_private_key_invalid") from exc


def _decode_public_key(value: str) -> Ed25519PublicKey:
    try:
        raw = base64.b64decode(str(value).strip(), validate=True)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PublicKey.from_public_bytes(raw)
    except (TypeError, ValueError) as exc:
        raise EvidencePackV2Error("evidence_v2_public_key_invalid") from exc


def public_key_fingerprint(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "sha256:" + _sha256(raw)


def encode_public_key(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def _normalize_pack_file(name: str, value: Any) -> bytes:
    if name.endswith(".json"):
        if isinstance(value, (bytes, bytearray)):
            try:
                parsed = json.loads(bytes(value))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise EvidencePackV2Error(f"invalid_json_file:{name}") from exc
            return jcs_canonicalize(parsed)
        return jcs_canonicalize(value)
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    raise EvidencePackV2Error(f"invalid_text_file:{name}")


def _zip_write(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    archive.writestr(info, payload)


def build_evidence_pack_v2(
    *,
    manifest_fields: Mapping[str, Any],
    files: Mapping[str, Any],
    signing_private_key: str,
    key_id: str,
) -> tuple[bytes, dict[str, Any], str]:
    """Build one deterministic, detached-Ed25519-signed Evidence Pack."""

    file_names = set(files)
    if (
        not EVIDENCE_PACK_V2_REQUIRED_FILES.issubset(file_names)
        or not file_names.issubset(
            EVIDENCE_PACK_V2_REQUIRED_FILES | EVIDENCE_PACK_V2_OPTIONAL_FILES
        )
    ):
        raise EvidencePackV2Error("evidence_v2_required_files_mismatch")
    clean_key_id = str(key_id or "").strip()
    if not clean_key_id or any(ch.isspace() for ch in clean_key_id):
        raise EvidencePackV2Error("evidence_v2_key_id_invalid")
    private_key = _decode_private_key(signing_private_key)
    fingerprint = public_key_fingerprint(private_key.public_key())

    encoded_files = {
        name: _normalize_pack_file(name, files[name])
        for name in sorted(file_names)
    }
    manifest = dict(manifest_fields)
    for reserved in (
        "schema_version",
        "signature_algorithm",
        "key_id",
        "public_key_fingerprint",
        "files",
        "pack_hash",
        "signature",
    ):
        manifest.pop(reserved, None)
    manifest.update(
        {
            "schema_version": EVIDENCE_PACK_V2_SCHEMA_VERSION,
            "signature_algorithm": EVIDENCE_PACK_V2_SIGNATURE_ALGORITHM,
            "key_id": clean_key_id,
            "public_key_fingerprint": fingerprint,
            "files": [
                {
                    "path": name,
                    "sha256": _sha256(payload),
                    "size_bytes": len(payload),
                }
                for name, payload in encoded_files.items()
            ],
        }
    )
    manifest_bytes = jcs_canonicalize(manifest)
    signature = private_key.sign(manifest_bytes)
    signature_bytes = base64.b64encode(signature)

    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as archive:
        _zip_write(archive, "manifest.json", manifest_bytes)
        _zip_write(archive, "manifest.sig", signature_bytes)
        for name, payload in encoded_files.items():
            _zip_write(archive, name, payload)
    pack = output.getvalue()
    if len(pack) > EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES:
        raise EvidencePackV2Error("evidence_v2_archive_too_large")
    return pack, manifest, "sha256:" + _sha256(pack)


def parse_evidence_keyring(value: Any) -> dict[str, dict[str, Any]]:
    """Parse the public keyring without accepting secret/private fields."""

    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError as exc:
            raise EvidencePackV2Error("evidence_v2_keyring_invalid") from exc
    if isinstance(value, Mapping) and "keys" in value:
        value = value["keys"]
    if isinstance(value, Mapping):
        records = [dict(record, key_id=key_id) for key_id, record in value.items()]
    elif isinstance(value, list):
        records = value
    else:
        raise EvidencePackV2Error("evidence_v2_keyring_invalid")

    keyring: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise EvidencePackV2Error("evidence_v2_keyring_invalid")
        if any("private" in str(field).lower() or "secret" in str(field).lower() for field in record):
            raise EvidencePackV2Error("evidence_v2_keyring_contains_secret")
        key_id = str(record.get("key_id") or "").strip()
        if not key_id or key_id in keyring:
            raise EvidencePackV2Error("evidence_v2_keyring_duplicate_key")
        algorithm = str(record.get("algorithm") or "").lower()
        if algorithm != EVIDENCE_PACK_V2_SIGNATURE_ALGORITHM:
            raise EvidencePackV2Error("evidence_v2_keyring_algorithm_invalid")
        public_key_text = str(record.get("public_key") or "")
        public_key = _decode_public_key(public_key_text)
        observed_fingerprint = public_key_fingerprint(public_key)
        declared_fingerprint = str(record.get("fingerprint") or "")
        if declared_fingerprint and declared_fingerprint != observed_fingerprint:
            raise EvidencePackV2Error("evidence_v2_keyring_fingerprint_mismatch")
        status = str(record.get("status") or "active").lower()
        if status not in {"active", "retired", "revoked"}:
            raise EvidencePackV2Error("evidence_v2_keyring_status_invalid")
        not_before = record.get("not_before")
        not_after = record.get("not_after")
        parsed_not_before = (
            _parse_trust_timestamp(
                not_before,
                code="evidence_v2_keyring_validity_invalid",
            )
            if not_before is not None
            else None
        )
        parsed_not_after = (
            _parse_trust_timestamp(
                not_after,
                code="evidence_v2_keyring_validity_invalid",
            )
            if not_after is not None
            else None
        )
        if (
            parsed_not_before is not None
            and parsed_not_after is not None
            and parsed_not_after < parsed_not_before
        ):
            raise EvidencePackV2Error("evidence_v2_keyring_validity_invalid")
        if status == "retired" and (
            parsed_not_before is None or parsed_not_after is None
        ):
            raise EvidencePackV2Error("evidence_v2_retired_key_window_required")
        keyring[key_id] = {
            "key_id": key_id,
            "algorithm": algorithm,
            "public_key": public_key_text,
            "fingerprint": observed_fingerprint,
            "not_before": (
                parsed_not_before.isoformat() if parsed_not_before is not None else None
            ),
            "not_after": (
                parsed_not_after.isoformat() if parsed_not_after is not None else None
            ),
            "status": status,
        }
    return keyring


def evidence_keyring_document(value: Any) -> dict[str, Any]:
    keyring = parse_evidence_keyring(value)
    return {
        "schema_version": 1,
        "purpose": "standard-astro-evidence-pack-v2",
        "keys": [keyring[key_id] for key_id in sorted(keyring)],
        "warning": (
            "A valid signature proves origin and integrity; it does not prove "
            "that the scientific conclusion is correct."
        ),
    }


def _safe_archive_entries(pack: bytes) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    if len(pack) > EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES:
        raise EvidencePackV2Error("evidence_v2_archive_too_large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(pack), mode="r")
    except zipfile.BadZipFile as exc:
        raise EvidencePackV2Error("evidence_v2_bad_zip") from exc
    entries: dict[str, zipfile.ZipInfo] = {}
    expanded_size = 0
    for info in archive.infolist():
        name = info.filename
        if (
            not name
            or name.startswith(("/", "\\"))
            or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or name not in EVIDENCE_PACK_V2_ARCHIVE_FILES
        ):
            archive.close()
            raise EvidencePackV2Error("evidence_v2_unsafe_archive_path")
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            archive.close()
            raise EvidencePackV2Error("evidence_v2_archive_symlink")
        if name in entries:
            archive.close()
            raise EvidencePackV2Error("evidence_v2_duplicate_archive_entry")
        entries[name] = info
        expanded_size += int(info.file_size)
        if expanded_size > EVIDENCE_PACK_V2_MAX_EXPANDED_BYTES:
            archive.close()
            raise EvidencePackV2Error("evidence_v2_expanded_archive_too_large")
    archive_files = set(entries)
    if (
        not (EVIDENCE_PACK_V2_REQUIRED_FILES | {"manifest.json", "manifest.sig"})
        .issubset(archive_files)
        or not archive_files.issubset(EVIDENCE_PACK_V2_ARCHIVE_FILES)
    ):
        archive.close()
        raise EvidencePackV2Error("evidence_v2_archive_files_mismatch")
    return archive, entries


def verify_evidence_pack_v2(
    pack: bytes,
    *,
    trusted_keyring: Any,
) -> EvidencePackV2Verification:
    """Verify hashes, canonical manifest, detached signature, and key trust."""

    try:
        keyring = parse_evidence_keyring(trusted_keyring)
        archive, entries = _safe_archive_entries(bytes(pack))
        try:
            manifest_bytes = archive.read(entries["manifest.json"])
            signature_bytes = archive.read(entries["manifest.sig"])
            try:
                manifest = json.loads(manifest_bytes)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise EvidencePackV2Error("evidence_v2_manifest_invalid") from exc
            if not isinstance(manifest, Mapping):
                raise EvidencePackV2Error("evidence_v2_manifest_invalid")
            manifest = dict(manifest)
            if manifest_bytes != jcs_canonicalize(manifest):
                raise EvidencePackV2Error("evidence_v2_manifest_not_canonical")
            if manifest.get("schema_version") != EVIDENCE_PACK_V2_SCHEMA_VERSION:
                raise EvidencePackV2Error("evidence_v2_schema_mismatch")
            if (
                manifest.get("signature_algorithm")
                != EVIDENCE_PACK_V2_SIGNATURE_ALGORITHM
            ):
                raise EvidencePackV2Error("evidence_v2_algorithm_mismatch")

            file_records = manifest.get("files")
            if not isinstance(file_records, list):
                raise EvidencePackV2Error("evidence_v2_manifest_files_invalid")
            expected_files: dict[str, dict[str, Any]] = {}
            for record in file_records:
                if not isinstance(record, Mapping):
                    raise EvidencePackV2Error("evidence_v2_manifest_files_invalid")
                path = str(record.get("path") or "")
                if path in expected_files:
                    raise EvidencePackV2Error("evidence_v2_manifest_file_duplicate")
                expected_files[path] = dict(record)
            expected_file_names = set(expected_files)
            if (
                not EVIDENCE_PACK_V2_REQUIRED_FILES.issubset(expected_file_names)
                or not expected_file_names.issubset(
                    EVIDENCE_PACK_V2_REQUIRED_FILES
                    | EVIDENCE_PACK_V2_OPTIONAL_FILES
                )
                or set(entries)
                != expected_file_names | {"manifest.json", "manifest.sig"}
            ):
                raise EvidencePackV2Error("evidence_v2_manifest_files_mismatch")
            for path, record in expected_files.items():
                payload = archive.read(entries[path])
                declared_size = record.get("size_bytes")
                if type(declared_size) is not int or declared_size < 0:
                    raise EvidencePackV2Error("evidence_v2_file_size_invalid")
                if declared_size != len(payload):
                    raise EvidencePackV2Error("evidence_v2_file_size_mismatch")
                if str(record.get("sha256") or "") != _sha256(payload):
                    raise EvidencePackV2Error("evidence_v2_file_hash_mismatch")

            key_id = str(manifest.get("key_id") or "")
            key_record = keyring.get(key_id)
            if key_record is None:
                raise EvidencePackV2Error("evidence_v2_untrusted_key")
            if manifest.get("public_key_fingerprint") != key_record["fingerprint"]:
                raise EvidencePackV2Error("evidence_v2_pack_fingerprint_mismatch")
            try:
                signature = base64.b64decode(signature_bytes, validate=True)
            except (TypeError, ValueError) as exc:
                raise EvidencePackV2Error("evidence_v2_signature_encoding_invalid") from exc
            try:
                _decode_public_key(key_record["public_key"]).verify(
                    signature,
                    manifest_bytes,
                )
            except InvalidSignature as exc:
                raise EvidencePackV2Error("evidence_v2_signature_invalid") from exc
            if key_record["status"] == "revoked":
                return EvidencePackV2Verification(
                    valid=False,
                    code="evidence_v2_key_revoked",
                    manifest=manifest,
                    key_id=key_id,
                    key_status="revoked",
                )
            not_before = key_record.get("not_before")
            not_after = key_record.get("not_after")
            if not_before is not None or not_after is not None:
                signed_at = _parse_trust_timestamp(
                    manifest.get("finalized_at"),
                    code="evidence_v2_signing_time_invalid",
                )
                if (
                    not_before is not None
                    and signed_at
                    < _parse_trust_timestamp(
                        not_before,
                        code="evidence_v2_keyring_validity_invalid",
                    )
                ):
                    raise EvidencePackV2Error("evidence_v2_key_not_yet_valid")
                if (
                    not_after is not None
                    and signed_at
                    > _parse_trust_timestamp(
                        not_after,
                        code="evidence_v2_keyring_validity_invalid",
                    )
                ):
                    raise EvidencePackV2Error("evidence_v2_key_expired_for_pack")
            return EvidencePackV2Verification(
                valid=True,
                code="ok",
                manifest=manifest,
                key_id=key_id,
                key_status=key_record["status"],
            )
        finally:
            archive.close()
    except EvidencePackV2Error as exc:
        return EvidencePackV2Verification(valid=False, code=exc.code)
    except Exception:
        # This verifier is exposed to unauthenticated hostile ZIP/JSON input.
        # All malformed shapes, conversion failures and decompression errors
        # must become a deterministic invalid result rather than an HTTP 500.
        return EvidencePackV2Verification(
            valid=False,
            code="evidence_v2_malformed_pack",
        )
