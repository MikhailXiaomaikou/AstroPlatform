"""Boot-only delivery and append-only activation receipts for Registry v2.

The signed import callback is intentionally not an activation mechanism.  An
activation is accepted only after public release material has been baked into
an exact Git commit, a fresh process has booted that material, and the hosted
control roles report that same commit.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.foundry_activation_records import (
    WorkflowRegistryActivationReceipt,
)
from app.models.foundry_records import (
    FoundryCandidate,
    WorkflowRegistryEntry,
    WorkflowRegistryRelease,
    WorkflowRegistryReleaseImport,
)
from app.services.evidence_pack_v2 import jcs_canonicalize
from app.services.foundry_registry_import import (
    RegistryReleaseImportError,
    _require_current_candidate_policy,
)
from app.services.workflow_registry_v2 import (
    WorkflowRegistryError,
    active_registry_identity,
    verify_signed_registry_snapshot,
)


ACTIVATION_MANIFEST_SCHEMA = "standard_astro_registry_activation_manifest_v1"
ACTIVATION_EXPORT_SCHEMA = "standard_astro_registry_activation_export_v1"
ACTIVATION_MODE = "process_boot_only"
ACTIVATION_PROVIDER = "render_api_exact_commit"
SIGNED_SNAPSHOT_BASENAME = "active-signed-registry.json"
TRUSTED_KEYRING_BASENAME = "trusted-registry-keyring.json"
ACTIVATION_MANIFEST_BASENAME = "activation-manifest.json"
PACKAGED_ACTIVATION_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "registry_releases"
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_DEPLOY_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_ROLE_SET = frozenset({"backend", "control_worker", "beat"})
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "release_request_id",
        "release_request_sha256",
        "registry_release_import_id",
        "registry_release_import_receipt_sha256",
        "registry_epoch",
        "registry_snapshot_sha256",
        "signing_key_id",
        "signed_snapshot_path",
        "signed_snapshot_file_sha256",
        "trusted_keyring_path",
        "trusted_keyring_file_sha256",
        "prepared_from_git_commit",
        "runtime_activation_mode",
    }
)
_EXPORT_FIELDS = frozenset(
    {
        "schema_version",
        "release_request_id",
        "release_request_sha256",
        "registry_release_import_id",
        "registry_release_import_receipt_sha256",
        "registry_epoch",
        "registry_snapshot_sha256",
        "signing_key_id",
        "signed_snapshot",
        "trusted_keyring",
        "runtime_registry_modified",
        "activation_required",
    }
)


class RegistryActivationError(ValueError):
    """Stable fail-closed activation error for APIs and startup."""

    def __init__(self, code: str, *, status_code: int = 422):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


async def _require_current_activation_policy(
    db: AsyncSession,
    entries: Any,
) -> None:
    try:
        await _require_current_candidate_policy(db, entries)
    except RegistryReleaseImportError as exc:
        code = (
            "registry_activation_candidate_policy_invalid"
            if exc.code == "registry_release_candidate_policy_invalid"
            else "registry_activation_candidate_policy_unverifiable"
        )
        raise RegistryActivationError(code, status_code=409) from exc


def _signed_import_entries(imported: WorkflowRegistryReleaseImport) -> Any:
    snapshot = imported.signed_snapshot
    payload = snapshot.get("payload") if isinstance(snapshot, Mapping) else None
    return payload.get("entries") if isinstance(payload, Mapping) else None


@dataclass(frozen=True)
class PackagedActivation:
    manifest: dict[str, Any]
    manifest_hash: str
    manifest_path: Path
    signed_snapshot: dict[str, Any]
    signed_snapshot_path: Path
    trusted_keyring: dict[str, str]
    trusted_keyring_path: Path
    verified_payload: dict[str, Any]


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_hash(value: Any) -> str:
    try:
        return _sha256_bytes(jcs_canonicalize(value))
    except (TypeError, ValueError) as exc:
        raise RegistryActivationError("registry_activation_json_invalid") from exc


def _json_loads(raw: bytes, *, error_code: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RegistryActivationError(error_code, status_code=503) from exc


def _read_bounded_regular_file(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RegistryActivationError(
            "registry_activation_file_missing", status_code=503
        ) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > maximum_bytes
    ):
        raise RegistryActivationError(
            "registry_activation_file_invalid", status_code=503
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RegistryActivationError(
            "registry_activation_file_unreadable", status_code=503
        ) from exc


def _exact_uuid(value: Any, *, error_code: str) -> uuid.UUID:
    try:
        parsed = uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RegistryActivationError(error_code, status_code=503) from exc
    if str(parsed) != str(value):
        raise RegistryActivationError(error_code, status_code=503)
    return parsed


def read_packaged_activation(
    manifest_path: str | Path,
) -> PackagedActivation:
    """Read and independently verify one repository-baked public bundle."""

    path = Path(manifest_path)
    manifest_raw = _read_bounded_regular_file(path, maximum_bytes=128 * 1024)
    manifest = _json_loads(
        manifest_raw, error_code="registry_activation_manifest_invalid"
    )
    if (
        not isinstance(manifest, dict)
        or set(manifest) != _MANIFEST_FIELDS
        or manifest.get("schema_version") != ACTIVATION_MANIFEST_SCHEMA
        or manifest.get("runtime_activation_mode") != ACTIVATION_MODE
        or manifest.get("signed_snapshot_path") != SIGNED_SNAPSHOT_BASENAME
        or manifest.get("trusted_keyring_path") != TRUSTED_KEYRING_BASENAME
        or not _SHA256.fullmatch(
            str(manifest.get("release_request_sha256") or "")
        )
        or not _SHA256.fullmatch(
            str(manifest.get("registry_release_import_receipt_sha256") or "")
        )
        or not _SHA256.fullmatch(
            str(manifest.get("registry_snapshot_sha256") or "")
        )
        or not _SHA256.fullmatch(
            str(manifest.get("signed_snapshot_file_sha256") or "")
        )
        or not _SHA256.fullmatch(
            str(manifest.get("trusted_keyring_file_sha256") or "")
        )
        or not _GIT_SHA.fullmatch(
            str(manifest.get("prepared_from_git_commit") or "")
        )
        or not str(manifest.get("registry_epoch") or "").strip()
        or len(str(manifest.get("registry_epoch") or "")) > 128
        or not str(manifest.get("signing_key_id") or "").strip()
        or any(ch.isspace() for ch in str(manifest.get("signing_key_id") or ""))
    ):
        raise RegistryActivationError(
            "registry_activation_manifest_invalid", status_code=503
        )
    _exact_uuid(
        manifest.get("release_request_id"),
        error_code="registry_activation_manifest_invalid",
    )
    _exact_uuid(
        manifest.get("registry_release_import_id"),
        error_code="registry_activation_manifest_invalid",
    )

    signed_path = path.parent / SIGNED_SNAPSHOT_BASENAME
    keyring_path = path.parent / TRUSTED_KEYRING_BASENAME
    signed_raw = _read_bounded_regular_file(signed_path, maximum_bytes=10 * 1024 * 1024)
    keyring_raw = _read_bounded_regular_file(keyring_path, maximum_bytes=128 * 1024)
    if not hmac.compare_digest(
        _sha256_bytes(signed_raw), manifest["signed_snapshot_file_sha256"]
    ) or not hmac.compare_digest(
        _sha256_bytes(keyring_raw), manifest["trusted_keyring_file_sha256"]
    ):
        raise RegistryActivationError(
            "registry_activation_file_hash_mismatch", status_code=503
        )

    signed_snapshot = _json_loads(
        signed_raw, error_code="registry_activation_snapshot_invalid"
    )
    trusted_keyring = _json_loads(
        keyring_raw, error_code="registry_activation_keyring_invalid"
    )
    if not isinstance(signed_snapshot, dict) or not isinstance(trusted_keyring, dict):
        raise RegistryActivationError(
            "registry_activation_material_invalid", status_code=503
        )
    if not trusted_keyring or not all(
        isinstance(key, str)
        and key
        and not any(ch.isspace() for ch in key)
        and isinstance(value, str)
        and value
        for key, value in trusted_keyring.items()
    ):
        raise RegistryActivationError(
            "registry_activation_keyring_invalid", status_code=503
        )
    key_id = str(manifest["signing_key_id"])
    if key_id not in trusted_keyring:
        raise RegistryActivationError(
            "registry_activation_signing_key_untrusted", status_code=503
        )
    try:
        payload = verify_signed_registry_snapshot(signed_snapshot, trusted_keyring)
    except WorkflowRegistryError as exc:
        raise RegistryActivationError(exc.code, status_code=503) from exc
    if (
        signed_snapshot.get("payload_sha256")
        != manifest["registry_snapshot_sha256"]
        or payload.get("registry_epoch") != manifest["registry_epoch"]
        or (signed_snapshot.get("signature") or {}).get("key_id") != key_id
    ):
        raise RegistryActivationError(
            "registry_activation_snapshot_binding_mismatch", status_code=503
        )
    return PackagedActivation(
        manifest=dict(manifest),
        manifest_hash=_sha256_bytes(manifest_raw),
        manifest_path=path,
        signed_snapshot=dict(signed_snapshot),
        signed_snapshot_path=signed_path,
        trusted_keyring={str(key): str(value) for key, value in trusted_keyring.items()},
        trusted_keyring_path=keyring_path,
        verified_payload=dict(payload),
    )


def assert_configured_registry_activation_bundle(
    environ: Mapping[str, str] | None = None,
) -> PackagedActivation | None:
    """Fail process startup when configured release material is not exact.

    The Registry module has already activated the configured signed file by the
    time the application lifespan begins.  This second gate proves that file is
    the one committed alongside its public keyring and activation manifest.
    """

    environment = environ or os.environ
    release_path = str(environment.get("WORKFLOW_REGISTRY_RELEASE_PATH") or "").strip()
    keyring_path = str(
        environment.get("WORKFLOW_REGISTRY_TRUSTED_KEYRING_PATH") or ""
    ).strip()
    manifest_path = str(
        environment.get("WORKFLOW_REGISTRY_ACTIVATION_MANIFEST_PATH") or ""
    ).strip()
    keyring_json = str(
        environment.get("WORKFLOW_REGISTRY_TRUSTED_KEYRING_JSON") or ""
    ).strip()
    packaged_manifest = PACKAGED_ACTIVATION_DIRECTORY / ACTIVATION_MANIFEST_BASENAME
    configured = any((release_path, keyring_path, manifest_path, keyring_json))
    if not configured and packaged_manifest.is_file():
        release_path = str(PACKAGED_ACTIVATION_DIRECTORY / SIGNED_SNAPSHOT_BASENAME)
        keyring_path = str(PACKAGED_ACTIVATION_DIRECTORY / TRUSTED_KEYRING_BASENAME)
        manifest_path = str(packaged_manifest)
        configured = True
    if not configured:
        return None
    if not release_path or not keyring_path or not manifest_path or keyring_json:
        raise RegistryActivationError(
            "registry_activation_configuration_incomplete", status_code=503
        )
    packaged = read_packaged_activation(manifest_path)
    try:
        configured_release = Path(release_path).resolve(strict=True)
        configured_keyring = Path(keyring_path).resolve(strict=True)
        packaged_release = packaged.signed_snapshot_path.resolve(strict=True)
        packaged_keyring = packaged.trusted_keyring_path.resolve(strict=True)
    except OSError as exc:
        raise RegistryActivationError(
            "registry_activation_configuration_invalid", status_code=503
        ) from exc
    if configured_release != packaged_release or configured_keyring != packaged_keyring:
        raise RegistryActivationError(
            "registry_activation_configuration_mismatch", status_code=503
        )
    runtime = active_registry_identity()
    if (
        runtime.get("release_kind") != "signed_registry_release"
        or runtime.get("status") != "ACTIVE"
        or runtime.get("signature_algorithm") != "ed25519"
        or runtime.get("signature_key_id") != packaged.manifest["signing_key_id"]
        or runtime.get("registry_epoch") != packaged.manifest["registry_epoch"]
        or runtime.get("signed_payload_sha256")
        != packaged.manifest["registry_snapshot_sha256"]
    ):
        raise RegistryActivationError(
            "registry_activation_runtime_identity_mismatch", status_code=503
        )
    return packaged


async def export_verified_activation_material(
    db: AsyncSession,
    *,
    release_request_id: uuid.UUID,
    release_request_hash: str,
    trusted_public_keys: Mapping[str, str],
) -> dict[str, Any]:
    """Export only an already-imported, independently reverified release."""

    if not _SHA256.fullmatch(release_request_hash):
        raise RegistryActivationError("registry_activation_request_hash_invalid")
    imported = await db.scalar(
        select(WorkflowRegistryReleaseImport).where(
            WorkflowRegistryReleaseImport.release_request_id == release_request_id
        )
    )
    request = await db.get(WorkflowRegistryRelease, release_request_id)
    if imported is None or request is None:
        raise RegistryActivationError(
            "registry_activation_import_missing", status_code=404
        )
    if (
        imported.status != "SIGNED_READY_FOR_DEPLOYMENT"
        or imported.runtime_registry_modified is not False
        or request.manifest_hash != release_request_hash
        or imported.release_request_hash != release_request_hash
    ):
        raise RegistryActivationError(
            "registry_activation_import_binding_mismatch", status_code=409
        )
    key_id = str(imported.signing_key_id or "")
    public_key = trusted_public_keys.get(key_id)
    if not public_key:
        raise RegistryActivationError(
            "registry_activation_signing_key_untrusted", status_code=503
        )
    try:
        payload = verify_signed_registry_snapshot(
            imported.signed_snapshot, {key_id: public_key}
        )
    except WorkflowRegistryError as exc:
        raise RegistryActivationError(exc.code, status_code=503) from exc
    if (
        payload.get("registry_epoch") != imported.registry_epoch
        or imported.signed_snapshot.get("payload_sha256")
        != imported.registry_snapshot_hash
    ):
        raise RegistryActivationError(
            "registry_activation_import_binding_mismatch", status_code=409
        )
    await _require_current_activation_policy(db, payload.get("entries"))
    result = {
        "schema_version": ACTIVATION_EXPORT_SCHEMA,
        "release_request_id": str(release_request_id),
        "release_request_sha256": release_request_hash,
        "registry_release_import_id": str(imported.id),
        "registry_release_import_receipt_sha256": imported.receipt_hash,
        "registry_epoch": imported.registry_epoch,
        "registry_snapshot_sha256": imported.registry_snapshot_hash,
        "signing_key_id": key_id,
        "signed_snapshot": dict(imported.signed_snapshot),
        "trusted_keyring": {key_id: public_key},
        "runtime_registry_modified": False,
        "activation_required": "protected_commit_and_fresh_process",
    }
    if set(result) != _EXPORT_FIELDS:
        raise RegistryActivationError("registry_activation_export_invalid")
    return result


async def _release_chain_state(
    db: AsyncSession,
    *,
    candidate_import: WorkflowRegistryReleaseImport,
) -> dict[str, Any]:
    """Return the imported head and highest persisted activation in one chain."""

    await _require_current_activation_policy(
        db,
        _signed_import_entries(candidate_import),
    )

    imports = list(
        (await db.execute(select(WorkflowRegistryReleaseImport))).scalars().all()
    )
    if not imports:
        raise RegistryActivationError(
            "registry_activation_import_missing", status_code=404
        )
    requests: dict[uuid.UUID, WorkflowRegistryRelease] = {}
    operation_sequences: dict[uuid.UUID, list[Any]] = {}
    for imported in imports:
        request = await db.get(WorkflowRegistryRelease, imported.release_request_id)
        operations = (
            (request.manifest or {}).get("operation_sequence")
            if request is not None
            else None
        )
        if request is None or not isinstance(operations, list) or not operations:
            raise RegistryActivationError(
                "registry_activation_release_chain_invalid", status_code=503
            )
        requests[request.id] = request
        operation_sequences[request.id] = list(operations)

    candidate_operations = operation_sequences.get(candidate_import.release_request_id)
    if candidate_operations is None:
        raise RegistryActivationError(
            "registry_activation_release_chain_invalid", status_code=503
        )
    maximum_import_length = max(len(value) for value in operation_sequences.values())
    imported_heads = [
        imported
        for imported in imports
        if len(operation_sequences[imported.release_request_id])
        == maximum_import_length
    ]
    if len(imported_heads) != 1:
        raise RegistryActivationError(
            "registry_activation_import_chain_fork", status_code=503
        )
    imported_head = imported_heads[0]

    activations = list(
        (
            await db.execute(select(WorkflowRegistryActivationReceipt))
        ).scalars().all()
    )
    active_receipt: WorkflowRegistryActivationReceipt | None = None
    active_operations: list[Any] | None = None
    if activations:
        activation_contexts: list[
            tuple[int, WorkflowRegistryActivationReceipt, list[Any]]
        ] = []
        for activation in activations:
            operations = operation_sequences.get(activation.release_request_id)
            if operations is None:
                raise RegistryActivationError(
                    "registry_activation_receipt_chain_invalid", status_code=503
                )
            activation_contexts.append((len(operations), activation, operations))
        maximum_active_length = max(item[0] for item in activation_contexts)
        active_heads = [
            item for item in activation_contexts if item[0] == maximum_active_length
        ]
        if len(active_heads) != 1:
            raise RegistryActivationError(
                "registry_activation_receipt_chain_fork", status_code=503
            )
        _length, active_receipt, active_operations = active_heads[0]

    return {
        "candidate_operations": candidate_operations,
        "imported_head": imported_head,
        "active_receipt": active_receipt,
        "active_operations": active_operations,
    }


def _runtime_matches_release(imported: WorkflowRegistryReleaseImport) -> bool:
    runtime = active_registry_identity()
    return bool(
        runtime.get("release_kind") == "signed_registry_release"
        and runtime.get("status") == "ACTIVE"
        and runtime.get("registry_epoch") == imported.registry_epoch
        and runtime.get("signed_payload_sha256")
        == imported.registry_snapshot_hash
        and runtime.get("signature_key_id") == imported.signing_key_id
    )


async def preflight_registry_activation(
    db: AsyncSession,
    *,
    release_request_id: uuid.UUID,
    release_request_hash: str,
    registry_snapshot_hash: str,
    target_git_commit: str,
) -> dict[str, Any]:
    """Reject stale or replayed releases before any Render deploy is created."""

    target = str(target_git_commit or "").strip().lower()
    if (
        not _GIT_SHA.fullmatch(target)
        or not _SHA256.fullmatch(release_request_hash)
        or not _SHA256.fullmatch(registry_snapshot_hash)
    ):
        raise RegistryActivationError("registry_activation_preflight_invalid")
    imported = await db.scalar(
        select(WorkflowRegistryReleaseImport).where(
            WorkflowRegistryReleaseImport.release_request_id == release_request_id
        )
    )
    if imported is None:
        raise RegistryActivationError(
            "registry_activation_import_missing", status_code=404
        )
    if (
        imported.release_request_hash != release_request_hash
        or imported.registry_snapshot_hash != registry_snapshot_hash
        or imported.status != "SIGNED_READY_FOR_DEPLOYMENT"
        or imported.runtime_registry_modified is not False
    ):
        raise RegistryActivationError(
            "registry_activation_import_binding_mismatch", status_code=409
        )
    chain = await _release_chain_state(db, candidate_import=imported)
    if chain["imported_head"].id != imported.id:
        raise RegistryActivationError(
            "registry_activation_import_not_chain_head", status_code=409
        )
    active = chain["active_receipt"]
    active_operations = chain["active_operations"]
    candidate_operations = chain["candidate_operations"]
    if active is not None:
        if active.release_import_id == imported.id:
            raise RegistryActivationError(
                "registry_activation_release_already_active", status_code=409
            )
        if (
            not isinstance(active_operations, list)
            or len(candidate_operations) <= len(active_operations)
            or candidate_operations[: len(active_operations)] != active_operations
        ):
            raise RegistryActivationError(
                "registry_activation_release_not_strict_successor", status_code=409
            )
        active_import = await db.get(
            WorkflowRegistryReleaseImport, active.release_import_id
        )
        if active_import is None:
            raise RegistryActivationError(
                "registry_activation_receipt_chain_invalid", status_code=503
            )
        # A retry may observe the candidate already booted but not yet
        # confirmed. Both that state and the last persisted ACTIVE state are
        # safe; any third runtime identity indicates rollback or drift.
        if not (
            _runtime_matches_release(active_import)
            or _runtime_matches_release(imported)
        ):
            raise RegistryActivationError(
                "registry_activation_runtime_not_persisted_head", status_code=503
            )
    else:
        runtime = active_registry_identity()
        if runtime.get("release_kind") != "builtin_code_registry" and not (
            _runtime_matches_release(imported)
        ):
            raise RegistryActivationError(
                "registry_activation_runtime_not_persisted_head", status_code=503
            )
    return {
        "status": "DEPLOY_APPROVED",
        "release_request_id": str(release_request_id),
        "release_request_sha256": release_request_hash,
        "registry_release_import_id": str(imported.id),
        "registry_epoch": imported.registry_epoch,
        "registry_snapshot_sha256": imported.registry_snapshot_hash,
        "target_git_commit": target,
        "strict_successor": active is not None,
        "deployment_required": True,
        "runtime_hot_switched": False,
    }


async def assert_persisted_activation_not_rollback(
    db: AsyncSession,
    *,
    packaged: PackagedActivation,
) -> dict[str, Any]:
    """Startup gate against an image older than the persisted ACTIVE receipt."""

    imported = await db.get(
        WorkflowRegistryReleaseImport,
        _exact_uuid(
            packaged.manifest["registry_release_import_id"],
            error_code="registry_activation_manifest_invalid",
        ),
    )
    if imported is None:
        raise RegistryActivationError(
            "registry_activation_import_missing", status_code=503
        )
    chain = await _release_chain_state(db, candidate_import=imported)
    active = chain["active_receipt"]
    active_operations = chain["active_operations"]
    candidate_operations = chain["candidate_operations"]
    if active is not None and active.release_import_id == imported.id:
        return {
            "status": "PERSISTED_ACTIVE_RESTART",
            "release_import_id": str(imported.id),
            "registry_epoch": imported.registry_epoch,
        }
    if active is not None and (
        not isinstance(active_operations, list)
        or len(candidate_operations) <= len(active_operations)
        or candidate_operations[: len(active_operations)] != active_operations
    ):
        raise RegistryActivationError(
            "registry_activation_startup_rollback_detected", status_code=503
        )
    # A never-activated candidate may boot only if it remains the newest
    # verified import. This rejects a stale activation PR after a newer import.
    if chain["imported_head"].id != imported.id:
        raise RegistryActivationError(
            "registry_activation_startup_import_stale", status_code=503
        )
    return {
        "status": "UNCONFIRMED_HEAD_BOOT",
        "release_import_id": str(imported.id),
        "registry_epoch": imported.registry_epoch,
    }


def _runtime_commit() -> str:
    commit = str(
        os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("GIT_COMMIT")
        or os.getenv("TOOL_VERSION")
        or ""
    ).strip().lower()
    if not _GIT_SHA.fullmatch(commit):
        raise RegistryActivationError(
            "registry_activation_runtime_commit_invalid", status_code=503
        )
    return commit


async def _verify_projected_catalog(
    db: AsyncSession,
    *,
    packaged: PackagedActivation,
    imported: WorkflowRegistryReleaseImport,
) -> dict[str, Any]:
    entries = packaged.verified_payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RegistryActivationError(
            "registry_activation_entries_invalid", status_code=503
        )
    await _require_current_activation_policy(db, entries)
    counts: dict[str, int] = {}
    for signed_entry in entries:
        workflow = signed_entry.get("workflow") if isinstance(signed_entry, dict) else None
        if not isinstance(workflow, dict):
            raise RegistryActivationError(
                "registry_activation_entries_invalid", status_code=503
            )
        workflow_id = str(workflow.get("workflow_id") or "")
        workflow_version = str(workflow.get("version") or "")
        state = str(workflow.get("state") or "")
        candidate_id = str(signed_entry.get("candidate_id") or "")
        row = await db.scalar(
            select(WorkflowRegistryEntry).where(
                WorkflowRegistryEntry.workflow_id == workflow_id,
                WorkflowRegistryEntry.workflow_version == workflow_version,
            )
        )
        if row is None or str(row.candidate_id) != candidate_id or row.status != state:
            raise RegistryActivationError(
                "registry_activation_projection_incomplete", status_code=503
            )
        candidate = await db.get(FoundryCandidate, row.candidate_id)
        expected_candidate_state = {
            "REGISTERED": "PROMOTED",
            "SUSPENDED": "SUSPENDED",
            "SUPERSEDED": "SUPERSEDED",
            "REVOKED": "REVOKED",
        }.get(state)
        if candidate is None or expected_candidate_state is None:
            raise RegistryActivationError(
                "registry_activation_projection_incomplete", status_code=503
            )
        if candidate.status != expected_candidate_state:
            raise RegistryActivationError(
                "registry_activation_projection_incomplete", status_code=503
            )
        if (
            row.registry_entry_hash != signed_entry.get("registry_entry_hash")
            or row.candidate_version_hash
            != signed_entry.get("candidate_version_hash")
        ):
            raise RegistryActivationError(
                "registry_activation_projection_binding_mismatch", status_code=503
            )
        counts[expected_candidate_state] = counts.get(expected_candidate_state, 0) + 1
    return {
        "release_import_id": str(imported.id),
        "entry_count": len(entries),
        "candidate_status_counts": dict(sorted(counts.items())),
        "projection_verified": True,
    }


async def probe_activation_control_roles(target_commit: str) -> dict[str, Any]:
    """Require the hosted verifier and scheduler to run the target release."""

    from app.api.health import (
        _probe_celery_beat,
        _probe_celery_workers,
        _probe_verification_queue,
    )

    worker_count, workers_match = await _probe_celery_workers(target_commit)
    verification_queue = await _probe_verification_queue()
    beat_ok, beat_status = await _probe_celery_beat(target_commit)
    if worker_count < 1 or not workers_match or not verification_queue or not beat_ok:
        raise RegistryActivationError(
            "registry_activation_control_roles_not_ready", status_code=503
        )
    return {
        "backend_commit": target_commit,
        "control_worker_count": worker_count,
        "control_workers_match": True,
        "verification_queue": "ready",
        "beat": beat_status,
    }


async def registry_activation_readiness(
    db: AsyncSession,
    *,
    release_request_id: uuid.UUID,
    release_request_hash: str,
    target_git_commit: str,
) -> dict[str, Any]:
    """Prove import, package, process, projection and hosted role coherence."""

    target = str(target_git_commit or "").strip().lower()
    if not _GIT_SHA.fullmatch(target) or not _SHA256.fullmatch(release_request_hash):
        raise RegistryActivationError("registry_activation_request_invalid")
    packaged = assert_configured_registry_activation_bundle()
    if packaged is None:
        raise RegistryActivationError(
            "registry_activation_bundle_not_configured", status_code=503
        )
    manifest = packaged.manifest
    if (
        manifest["release_request_id"] != str(release_request_id)
        or manifest["release_request_sha256"] != release_request_hash
        or _runtime_commit() != target
    ):
        raise RegistryActivationError(
            "registry_activation_runtime_binding_mismatch", status_code=409
        )
    imported = await db.get(
        WorkflowRegistryReleaseImport,
        _exact_uuid(
            manifest["registry_release_import_id"],
            error_code="registry_activation_manifest_invalid",
        ),
    )
    if imported is None or (
        imported.release_request_id != release_request_id
        or imported.release_request_hash != release_request_hash
        or imported.receipt_hash
        != manifest["registry_release_import_receipt_sha256"]
        or imported.registry_epoch != manifest["registry_epoch"]
        or imported.registry_snapshot_hash != manifest["registry_snapshot_sha256"]
        or imported.runtime_registry_modified is not False
        or _canonical_hash(imported.signed_snapshot)
        != _canonical_hash(packaged.signed_snapshot)
    ):
        raise RegistryActivationError(
            "registry_activation_import_binding_mismatch", status_code=409
        )
    projection = await _verify_projected_catalog(
        db, packaged=packaged, imported=imported
    )
    roles = await probe_activation_control_roles(target)
    return {
        "status": "ACTIVATION_READY",
        "runtime_hot_switched": False,
        "release_request_id": str(release_request_id),
        "release_request_sha256": release_request_hash,
        "registry_release_import_id": str(imported.id),
        "registry_epoch": imported.registry_epoch,
        "registry_snapshot_sha256": imported.registry_snapshot_hash,
        "activation_manifest_sha256": packaged.manifest_hash,
        "target_git_commit": target,
        "projection": projection,
        "control_roles": roles,
    }


def _normalize_deployments(
    deployments: Sequence[Mapping[str, Any]], *, target_git_commit: str
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in deployments:
        role = str(raw.get("role") or "").strip()
        service_hash = str(raw.get("service_id_sha256") or "").strip()
        deploy_id = str(raw.get("deploy_id") or "").strip()
        commit = str(raw.get("git_commit") or "").strip().lower()
        if (
            role not in _ROLE_SET
            or role in seen
            or not _SHA256.fullmatch(service_hash)
            or not _DEPLOY_ID.fullmatch(deploy_id)
            or commit != target_git_commit
        ):
            raise RegistryActivationError(
                "registry_activation_deployment_receipt_invalid"
            )
        seen.add(role)
        normalized.append(
            {
                "role": role,
                "service_id_sha256": service_hash,
                "deploy_id": deploy_id,
                "git_commit": commit,
            }
        )
    if seen != _ROLE_SET:
        raise RegistryActivationError(
            "registry_activation_deployment_set_incomplete"
        )
    return sorted(normalized, key=lambda item: item["role"])


async def confirm_registry_activation(
    db: AsyncSession,
    *,
    release_request_id: uuid.UUID,
    release_request_hash: str,
    target_git_commit: str,
    activation_manifest_hash: str,
    deployments: Sequence[Mapping[str, Any]],
) -> tuple[WorkflowRegistryActivationReceipt, bool]:
    """Append one immutable activation receipt after recomputing every gate."""

    target = str(target_git_commit or "").strip().lower()
    if not _GIT_SHA.fullmatch(target) or not _SHA256.fullmatch(
        activation_manifest_hash
    ):
        raise RegistryActivationError("registry_activation_confirmation_invalid")
    normalized_deployments = _normalize_deployments(
        deployments, target_git_commit=target
    )
    readiness = await registry_activation_readiness(
        db,
        release_request_id=release_request_id,
        release_request_hash=release_request_hash,
        target_git_commit=target,
    )
    if not hmac.compare_digest(
        readiness["activation_manifest_sha256"], activation_manifest_hash
    ):
        raise RegistryActivationError(
            "registry_activation_manifest_hash_mismatch", status_code=409
        )
    import_id = uuid.UUID(readiness["registry_release_import_id"])
    existing = await db.scalar(
        select(WorkflowRegistryActivationReceipt).where(
            WorkflowRegistryActivationReceipt.release_import_id == import_id
        )
    )
    deployment_set_hash = _canonical_hash(normalized_deployments)
    projection = dict(readiness["projection"])
    receipt_body = {
        "schema_version": "standard_astro_registry_activation_receipt_v1",
        "release_import_id": str(import_id),
        "release_request_id": str(release_request_id),
        "release_request_sha256": release_request_hash,
        "registry_epoch": readiness["registry_epoch"],
        "registry_snapshot_sha256": readiness["registry_snapshot_sha256"],
        "activation_manifest_sha256": activation_manifest_hash,
        "target_git_commit": target,
        "deployment_provider": ACTIVATION_PROVIDER,
        "deployment_set_sha256": deployment_set_hash,
        "projection": projection,
        "status": "ACTIVE",
        "runtime_hot_switched": False,
    }
    receipt_hash = _canonical_hash(receipt_body)
    if existing is not None:
        exact = (
            existing.release_request_id == release_request_id
            and existing.release_request_hash == release_request_hash
            and existing.registry_epoch == readiness["registry_epoch"]
            and existing.registry_snapshot_hash
            == readiness["registry_snapshot_sha256"]
            and existing.activation_manifest_hash == activation_manifest_hash
            and existing.target_git_commit == target
            and existing.deployment_receipts == normalized_deployments
            and existing.deployment_set_hash == deployment_set_hash
            and existing.projection_summary == projection
            and existing.receipt_hash == receipt_hash
            and existing.status == "ACTIVE"
            and existing.runtime_hot_switched is False
            and existing.projection_verified is True
        )
        if not exact:
            raise RegistryActivationError(
                "registry_activation_confirmation_conflict", status_code=409
            )
        return existing, False

    packaged = assert_configured_registry_activation_bundle()
    if packaged is None:
        raise RegistryActivationError(
            "registry_activation_bundle_not_configured", status_code=503
        )
    row = WorkflowRegistryActivationReceipt(
        release_import_id=import_id,
        release_request_id=release_request_id,
        release_request_hash=release_request_hash,
        registry_epoch=readiness["registry_epoch"],
        registry_snapshot_hash=readiness["registry_snapshot_sha256"],
        activation_manifest_hash=activation_manifest_hash,
        signed_snapshot_file_hash=packaged.manifest[
            "signed_snapshot_file_sha256"
        ],
        trusted_keyring_file_hash=packaged.manifest[
            "trusted_keyring_file_sha256"
        ],
        target_git_commit=target,
        deployment_provider=ACTIVATION_PROVIDER,
        deployment_receipts=normalized_deployments,
        deployment_set_hash=deployment_set_hash,
        projection_summary=projection,
        status="ACTIVE",
        runtime_hot_switched=False,
        projection_verified=True,
        receipt_hash=receipt_hash,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise RegistryActivationError(
            "registry_activation_confirmation_conflict", status_code=409
        ) from exc
    await db.refresh(row)
    return row, True


def serialize_activation_receipt(
    row: WorkflowRegistryActivationReceipt,
) -> dict[str, Any]:
    return {
        "activation_receipt_id": str(row.id),
        "release_request_id": str(row.release_request_id),
        "release_request_sha256": row.release_request_hash,
        "registry_epoch": row.registry_epoch,
        "registry_snapshot_sha256": row.registry_snapshot_hash,
        "activation_manifest_sha256": row.activation_manifest_hash,
        "target_git_commit": row.target_git_commit,
        "deployment_provider": row.deployment_provider,
        "deployment_receipts": list(row.deployment_receipts or []),
        "deployment_set_sha256": row.deployment_set_hash,
        "projection": dict(row.projection_summary or {}),
        "status": row.status,
        "runtime_hot_switched": False,
        "receipt_sha256": row.receipt_hash,
        "activated_at": row.activated_at.isoformat(),
    }


__all__ = [
    "ACTIVATION_EXPORT_SCHEMA",
    "ACTIVATION_MANIFEST_SCHEMA",
    "ACTIVATION_MANIFEST_BASENAME",
    "PACKAGED_ACTIVATION_DIRECTORY",
    "RegistryActivationError",
    "assert_persisted_activation_not_rollback",
    "assert_configured_registry_activation_bundle",
    "confirm_registry_activation",
    "export_verified_activation_material",
    "probe_activation_control_roles",
    "preflight_registry_activation",
    "read_packaged_activation",
    "registry_activation_readiness",
    "serialize_activation_receipt",
]
