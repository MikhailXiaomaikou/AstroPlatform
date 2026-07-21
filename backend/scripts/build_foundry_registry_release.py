#!/usr/bin/env python3
"""Compile and Ed25519-sign a complete Foundry registry overlay.

This command is designed for a protected, offline GitHub Environment.  It
accepts a server-exported PENDING release request, replays its complete
cumulative operation sequence over the immutable built-in Registry base, and
emits both a runtime-compatible signed snapshot and the body for the protected
import callback. Private key bytes are never written to either output. A
signed overlay is never used as the next release's base.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,254}$")
_REQUEST_SCHEMA = "standard_astro_registry_release_request_v1"
_SNAPSHOT_SCHEMA = "standard_astro_signed_workflow_registry_v1"
_IMPORT_SCHEMA = "standard_astro_registry_release_import_v1"
_MAX_JSON_BYTES = 16 * 1024 * 1024
_PRIVATE_DER_PREFIX = bytes.fromhex("302e020100300506032b657004220420")
_PUBLIC_DER_PREFIX = bytes.fromhex("302a300506032b6570032100")


class RegistryReleaseError(ValueError):
    """The pending request, base release, or signing material is invalid."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _utf16_sort_key(value: str) -> bytes:
    try:
        return value.encode("utf-16be")
    except UnicodeEncodeError as exc:
        raise RegistryReleaseError("registry_jcs_invalid_unicode") from exc


def jcs_canonicalize(value: Any) -> bytes:
    """Canonicalize the exact JSON subset used by the server's RFC 8785 gate."""

    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > 2**53 - 1:
            raise RegistryReleaseError("registry_jcs_unsafe_integer")
        return str(value).encode("ascii")
    if isinstance(value, float):
        raise RegistryReleaseError("registry_jcs_float_forbidden")
    if isinstance(value, str):
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (UnicodeEncodeError, ValueError) as exc:
            raise RegistryReleaseError("registry_jcs_invalid_string") from exc
    if isinstance(value, list):
        return b"[" + b",".join(jcs_canonicalize(item) for item in value) + b"]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise RegistryReleaseError("registry_jcs_non_string_key")
        fields = []
        for key in sorted(value, key=_utf16_sort_key):
            fields.append(jcs_canonicalize(key) + b":" + jcs_canonicalize(value[key]))
        return b"{" + b",".join(fields) + b"}"
    raise RegistryReleaseError("registry_jcs_unsupported_type")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        stat = path.stat()
        if not path.is_file() or path.is_symlink() or stat.st_size > _MAX_JSON_BYTES:
            raise OSError
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryReleaseError(f"{label}_invalid") from exc
    if not isinstance(value, dict):
        raise RegistryReleaseError(f"{label}_invalid")
    return value


def _normalize_hash(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower().removeprefix("sha256:")
    if _HEX64.fullmatch(normalized) is None:
        raise RegistryReleaseError(f"{label}_invalid")
    return normalized


def _normalize_timestamp(value: Any, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryReleaseError(f"{label}_invalid") from exc
    if parsed.tzinfo is None:
        raise RegistryReleaseError(f"{label}_invalid")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode_key(value: str, *, private: bool) -> bytes:
    try:
        raw = base64.b64decode(str(value).strip(), validate=True)
    except (TypeError, ValueError) as exc:
        raise RegistryReleaseError("registry_signing_key_invalid") from exc
    if len(raw) != 32:
        raise RegistryReleaseError("registry_signing_key_invalid")
    return raw


def _openssl_ed25519(
    operation: str,
    *,
    data: bytes,
    private_seed: bytes | None = None,
    public_raw: bytes | None = None,
    signature: bytes | None = None,
) -> bytes | bool:
    key_bytes = (
        _PRIVATE_DER_PREFIX + private_seed
        if private_seed is not None
        else _PUBLIC_DER_PREFIX + bytes(public_raw or b"")
    )
    with tempfile.TemporaryDirectory(prefix="standard-astro-registry-key-") as root:
        key_path = Path(root) / "key.der"
        key_path.write_bytes(key_bytes)
        os.chmod(key_path, 0o600)
        if operation == "public":
            result = subprocess.run(
                [
                    "openssl",
                    "pkey",
                    "-inform",
                    "DER",
                    "-in",
                    str(key_path),
                    "-pubout",
                    "-outform",
                    "DER",
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if not result.stdout.startswith(_PUBLIC_DER_PREFIX):
                raise RegistryReleaseError("registry_public_key_derivation_failed")
            return result.stdout[-32:]
        if operation == "sign":
            result = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-sign",
                    "-inkey",
                    str(key_path),
                    "-keyform",
                    "DER",
                    "-rawin",
                ],
                input=data,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            return result.stdout
        signature_path = Path(root) / "signature.bin"
        signature_path.write_bytes(bytes(signature or b""))
        result = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(key_path),
                "-keyform",
                "DER",
                "-rawin",
                "-sigfile",
                str(signature_path),
            ],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.returncode == 0


def _derive_public(private_seed: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        return Ed25519PrivateKey.from_private_bytes(private_seed).public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    except ImportError:
        return bytes(
            _openssl_ed25519("public", data=b"", private_seed=private_seed)
        )


def _sign(private_seed: bytes, data: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        return Ed25519PrivateKey.from_private_bytes(private_seed).sign(data)
    except ImportError:
        return bytes(
            _openssl_ed25519("sign", data=data, private_seed=private_seed)
        )


def _verify(public_raw: bytes, signature: bytes, data: bytes) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        try:
            Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, data)
            return True
        except InvalidSignature:
            return False
    except ImportError:
        return bool(
            _openssl_ed25519(
                "verify",
                data=data,
                public_raw=public_raw,
                signature=signature,
            )
        )


def _validate_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    workflow = entry.get("workflow")
    tools = entry.get("tools")
    if not isinstance(workflow, Mapping) or not isinstance(tools, list) or not tools:
        raise RegistryReleaseError("registry_entry_invalid")
    workflow_id = str(workflow.get("workflow_id") or "")
    version = str(workflow.get("version") or "")
    if _IDENTIFIER.fullmatch(workflow_id) is None or not version:
        raise RegistryReleaseError("registry_entry_identity_invalid")
    if workflow.get("state") not in {
        "REGISTERED",
        "SUSPENDED",
        "SUPERSEDED",
        "REVOKED",
    }:
        raise RegistryReleaseError("registry_entry_state_invalid")
    candidate_id = str(entry.get("candidate_id") or "").strip()
    candidate_version = entry.get("candidate_version")
    candidate_hash = str(entry.get("candidate_version_hash") or "")
    image_digest = str(entry.get("worker_image_digest") or "").lower()
    approval_hash = str(entry.get("approval_attestation_hash") or "")
    build_hash = str(entry.get("build_attestation_hash") or "")
    if (
        not candidate_id
        or type(candidate_version) is not int
        or candidate_version < 1
        or _HEX64.fullmatch(candidate_hash.removeprefix("sha256:")) is None
        or _IMAGE_DIGEST.fullmatch(image_digest) is None
        or _HEX64.fullmatch(approval_hash.removeprefix("sha256:")) is None
        or _HEX64.fullmatch(build_hash.removeprefix("sha256:")) is None
    ):
        raise RegistryReleaseError("registry_entry_image_invalid")
    if (
        entry.get("installation_status") != "PENDING_RELEASE"
        or entry.get("runtime_registry_modified") is not False
    ):
        raise RegistryReleaseError("registry_entry_not_pending_release")
    release_binding = {
        "binding_kind": "signed_candidate_release",
        "candidate_id": candidate_id,
        "candidate_version": candidate_version,
        "candidate_version_hash": candidate_hash,
        "approved_worker_image_digest": image_digest,
        "approval_attestation_hash": approval_hash,
        "build_attestation_hash": build_hash,
    }
    ordered_tools = sorted(
        tools,
        key=lambda item: (
            str(item.get("tool_id") or "") if isinstance(item, Mapping) else "",
            str(item.get("version") or "") if isinstance(item, Mapping) else "",
        ),
    )
    if any(not isinstance(item, Mapping) for item in ordered_tools):
        raise RegistryReleaseError("registry_entry_invalid")
    expected = "sha256:" + sha256(
        canonical_json(
            {
                "workflow": dict(workflow),
                "tools": ordered_tools,
                "release_binding": release_binding,
            }
        )
    )
    if entry.get("registry_entry_hash") != expected:
        raise RegistryReleaseError("registry_entry_hash_mismatch")
    return copy.deepcopy(dict(entry))


def _validate_request(
    request: dict[str, Any], expected_request_hash: str
) -> tuple[list[dict[str, Any]], str]:
    required = {
        "schema_version",
        "request_id",
        "request_kind",
        "request_epoch",
        "request_status",
        "requested_at",
        "requested_by_actor_hash",
        "base_registry_epoch",
        "base_registry_hash",
        "previous_request_hash",
        "new_operations",
        "operation_sequence",
        "operation_sequence_hash",
        "entries",
        "status_changes",
        "context",
        "runtime_registry_modified",
        "signature_required",
    }
    if set(request) != required or request.get("schema_version") != _REQUEST_SCHEMA:
        raise RegistryReleaseError("release_request_schema_invalid")
    if (
        request.get("request_status") != "PENDING_SIGNATURE"
        or request.get("runtime_registry_modified") is not False
        or request.get("signature_required") is not True
    ):
        raise RegistryReleaseError("release_request_not_pending")
    try:
        uuid.UUID(str(request.get("request_id")))
    except ValueError as exc:
        raise RegistryReleaseError("release_request_identity_invalid") from exc
    if _SHA256.fullmatch(str(request.get("requested_by_actor_hash") or "")) is None:
        raise RegistryReleaseError("release_request_actor_hash_invalid")
    _normalize_timestamp(request.get("requested_at"), "release_requested_at")
    semantic_hash = sha256(canonical_json(request))
    if semantic_hash != _normalize_hash(expected_request_hash, "release_request_hash"):
        raise RegistryReleaseError("release_request_hash_mismatch")
    operations = request.get("operation_sequence")
    if not isinstance(operations, list) or not operations:
        raise RegistryReleaseError("release_operation_sequence_empty")
    operation_hash = "sha256:" + sha256(canonical_json(operations))
    if request.get("operation_sequence_hash") != operation_hash:
        raise RegistryReleaseError("release_operation_sequence_hash_mismatch")
    if not str(request.get("base_registry_epoch") or "") or _SHA256.fullmatch(
        str(request.get("base_registry_hash") or "")
    ) is None:
        raise RegistryReleaseError("release_base_binding_invalid")
    previous_request_hash = request.get("previous_request_hash")
    if previous_request_hash is not None and _SHA256.fullmatch(
        str(previous_request_hash)
    ) is None:
        raise RegistryReleaseError("release_previous_request_hash_invalid")
    new_operations = request.get("new_operations")
    if not isinstance(new_operations, list) or not new_operations:
        raise RegistryReleaseError("release_new_operations_empty")
    if len(new_operations) > len(operations) or operations[-len(new_operations) :] != new_operations:
        raise RegistryReleaseError("release_new_operations_not_suffix")
    if (previous_request_hash is None) != (len(new_operations) == len(operations)):
        raise RegistryReleaseError("release_operation_chain_invalid")
    context = request.get("context")
    if not isinstance(context, dict):
        raise RegistryReleaseError("release_context_invalid")
    for operation in operations:
        if not isinstance(operation, dict) or not isinstance(
            operation.get("context"), dict
        ):
            raise RegistryReleaseError("release_operation_context_invalid")
    if any(operation.get("context") != context for operation in new_operations):
        raise RegistryReleaseError("release_new_operation_context_mismatch")
    expected_entries = [
        operation["entry"]
        for operation in new_operations
        if operation.get("operation") == "UPSERT_ENTRY" and "entry" in operation
    ]
    expected_status_changes = [
        operation["status_change"]
        for operation in new_operations
        if operation.get("operation") == "SET_ENTRY_STATUS"
        and "status_change" in operation
    ]
    if request.get("entries") != expected_entries or request.get(
        "status_changes"
    ) != expected_status_changes:
        raise RegistryReleaseError("release_new_operation_projection_mismatch")
    return [copy.deepcopy(item) for item in operations], semantic_hash


def _apply_operations(
    base_entries: list[dict[str, Any]], operations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    origin_hashes: dict[tuple[str, str], str] = {}
    for raw in base_entries:
        entry = _validate_entry(raw)
        workflow = entry["workflow"]
        identity = (str(workflow["workflow_id"]), str(workflow["version"]))
        if identity in entries:
            raise RegistryReleaseError("base_registry_duplicate_entry")
        entries[identity] = entry
        origin_hashes[identity] = str(entry["registry_entry_hash"])

    for operation in operations:
        if not isinstance(operation, dict):
            raise RegistryReleaseError("release_operation_invalid")
        kind = operation.get("operation")
        if kind == "UPSERT_ENTRY" and set(operation) == {
            "operation",
            "entry",
            "context",
        }:
            entry = _validate_entry(operation["entry"])
            workflow = entry["workflow"]
            identity = (str(workflow["workflow_id"]), str(workflow["version"]))
            existing = entries.get(identity)
            if existing is not None and existing != entry:
                raise RegistryReleaseError("release_entry_identity_conflict")
            entries[identity] = entry
            origin_hashes.setdefault(identity, str(entry["registry_entry_hash"]))
            continue
        if kind == "SET_ENTRY_STATUS" and set(operation) == {
            "operation",
            "status_change",
            "context",
        }:
            change = operation["status_change"]
            if not isinstance(change, dict):
                raise RegistryReleaseError("release_status_change_invalid")
            identity = (
                str(change.get("workflow_id") or ""),
                str(change.get("workflow_version") or ""),
            )
            entry = entries.get(identity)
            common_fields = {
                "registry_entry_id",
                "registry_entry_hash",
                "workflow_id",
                "workflow_version",
                "requested_status",
                "reason",
            }
            target = str(change.get("requested_status") or "")
            expected_fields = (
                common_fields
                | {
                    "superseded_by_workflow_id",
                    "superseded_by_workflow_version",
                }
                if target == "SUPERSEDED"
                else common_fields
            )
            try:
                uuid.UUID(str(change.get("registry_entry_id") or ""))
            except ValueError as exc:
                raise RegistryReleaseError(
                    "release_status_entry_identity_invalid"
                ) from exc
            if (
                set(change) != expected_fields
                or entry is None
                or origin_hashes.get(identity) != change.get("registry_entry_hash")
            ):
                raise RegistryReleaseError("release_status_entry_mismatch")
            if target not in {"REGISTERED", "SUSPENDED", "SUPERSEDED", "REVOKED"}:
                raise RegistryReleaseError("release_status_invalid")
            reason = str(change.get("reason") or "").strip()
            if target != "REGISTERED" and not reason:
                raise RegistryReleaseError("release_status_reason_missing")
            if target == "SUPERSEDED":
                successor = (
                    str(change.get("superseded_by_workflow_id") or ""),
                    str(change.get("superseded_by_workflow_version") or ""),
                )
                if (
                    successor == identity
                    or successor not in entries
                    or _IDENTIFIER.fullmatch(successor[0]) is None
                    or not successor[1]
                ):
                    raise RegistryReleaseError(
                        "release_superseding_workflow_invalid"
                    )
            current = str(entry["workflow"].get("state") or "")
            allowed = {
                "REGISTERED": {
                    "REGISTERED",
                    "SUSPENDED",
                    "SUPERSEDED",
                    "REVOKED",
                },
                "SUSPENDED": {
                    "REGISTERED",
                    "SUSPENDED",
                    "SUPERSEDED",
                    "REVOKED",
                },
                "SUPERSEDED": {"SUPERSEDED", "REVOKED"},
                "REVOKED": {"REVOKED"},
            }
            if target not in allowed.get(current, set()):
                raise RegistryReleaseError("release_status_transition_invalid")
            updated = copy.deepcopy(entry)
            updated["workflow"]["state"] = target
            updated["workflow"]["revocation_reason"] = (
                reason if target == "REVOKED" else None
            )
            release_binding = {
                "binding_kind": "signed_candidate_release",
                "candidate_id": str(updated["candidate_id"]),
                "candidate_version": int(updated["candidate_version"]),
                "candidate_version_hash": str(updated["candidate_version_hash"]),
                "approved_worker_image_digest": str(updated["worker_image_digest"]),
                "approval_attestation_hash": str(updated["approval_attestation_hash"]),
                "build_attestation_hash": str(updated["build_attestation_hash"]),
            }
            updated["registry_entry_hash"] = "sha256:" + sha256(
                canonical_json(
                    {
                        "workflow": updated["workflow"],
                        "tools": sorted(
                            updated["tools"],
                            key=lambda item: (item["tool_id"], item["version"]),
                        ),
                        "release_binding": release_binding,
                    }
                )
            )
            entries[identity] = _validate_entry(updated)
            continue
        raise RegistryReleaseError("release_operation_invalid")
    if not entries:
        raise RegistryReleaseError("release_snapshot_empty")
    return [entries[key] for key in sorted(entries)]


def build_release(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    request = _read_json(Path(args.request), "release_request")
    operations, request_hash = _validate_request(request, args.request_sha256)
    # Every Foundry overlay is a complete replay over the immutable built-in
    # code registry.  Never chain one signed overlay onto another: process
    # startup deliberately loads exactly one signed release over built-ins.
    complete_entries = _apply_operations([], operations)
    epoch = str(args.registry_epoch or "").strip()
    if not epoch or len(epoch) > 128:
        raise RegistryReleaseError("registry_epoch_invalid")
    key_id = str(args.key_id or "").strip()
    if not key_id or any(character.isspace() for character in key_id):
        raise RegistryReleaseError("registry_signing_key_id_invalid")
    private_text = Path(args.private_key_file).read_text(encoding="utf-8").strip()
    private_seed = _decode_key(private_text, private=True)
    public_raw = _derive_public(private_seed)

    payload = {
        "schema_version": _SNAPSHOT_SCHEMA,
        "registry_epoch": epoch,
        "base_registry_epoch": request["base_registry_epoch"],
        "base_registry_hash": request["base_registry_hash"],
        "operation_sequence": len(operations),
        "entries": complete_entries,
    }
    canonical = jcs_canonicalize(payload)
    signature = _sign(private_seed, canonical)
    signed_snapshot = {
        "payload": payload,
        "payload_sha256": "sha256:" + sha256(canonical),
        "signature": {
            "algorithm": "ed25519",
            "key_id": key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }
    if not _verify(public_raw, signature, canonical):
        raise RegistryReleaseError("registry_signature_self_check_failed")

    # Stable across an exact GitHub retry so the import callback is idempotent.
    generated_at = _normalize_timestamp(request["requested_at"], "release_requested_at")
    import_receipt: dict[str, Any] = {
        "schema_version": _IMPORT_SCHEMA,
        "release_request_id": request["request_id"],
        "release_request_sha256": "sha256:" + request_hash,
        "base_registry_epoch": request["base_registry_epoch"],
        "base_registry_hash": request["base_registry_hash"],
        "registry_epoch": epoch,
        "registry_snapshot_sha256": signed_snapshot["payload_sha256"],
        "signing_key_id": key_id,
        "signing_public_key_sha256": "sha256:" + sha256(public_raw),
        "complete_entry_count": len(complete_entries),
        "generated_at": generated_at,
        "import_mode": "protected_offline_registry_release",
        "signed_snapshot": signed_snapshot,
    }
    import_receipt["receipt_sha256"] = "sha256:" + sha256(
        jcs_canonicalize(import_receipt)
    )
    return signed_snapshot, import_receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--private-key-file", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--registry-epoch", required=True)
    parser.add_argument("--snapshot-output", required=True)
    parser.add_argument("--import-output", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    snapshot, import_receipt = build_release(args)
    snapshot_output = Path(args.snapshot_output)
    import_output = Path(args.import_output)
    snapshot_output.parent.mkdir(parents=True, exist_ok=True)
    import_output.parent.mkdir(parents=True, exist_ok=True)
    snapshot_output.write_bytes(jcs_canonicalize(snapshot) + b"\n")
    import_output.write_bytes(jcs_canonicalize(import_receipt) + b"\n")
    os.chmod(snapshot_output, 0o600)
    os.chmod(import_output, 0o600)
    print(
        json.dumps(
            {
                "registry_epoch": import_receipt["registry_epoch"],
                "registry_snapshot_sha256": import_receipt[
                    "registry_snapshot_sha256"
                ],
                "complete_entry_count": import_receipt["complete_entry_count"],
                "release_request_id": import_receipt["release_request_id"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
