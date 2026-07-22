"""Protected import gate for signed Workflow Registry release artifacts.

This module records deployment-ready signatures.  It deliberately does not
call ``activate_verified_registry_release`` and never updates a live registry.
Activation is possible only when a new process boots from a fixed release file.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import re
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.foundry_records import (
    FoundryCandidate,
    FoundryCandidateVersion,
    FoundryFormalBuildAttestation,
    WorkflowRegistryEntry,
    WorkflowRegistryRelease,
    WorkflowRegistryReleaseImport,
)
from app.services.evidence_pack_v2 import jcs_canonicalize
from app.services.foundry_catalog import (
    FoundryCatalogError,
    _append_event,
    _utc_now,
    canonical_json,
    registry_release_request_chain,
    sha256_json,
)
from app.services.foundry_evidence_policy import (
    candidate_bundle_contains_formal_claim_escape,
)
from app.services.workflow_registry_v2 import (
    WorkflowRegistryError,
    build_registry_status_entry,
    active_registry_identity,
    builtin_registry_identity,
    list_formal_workflows,
    verify_signed_registry_snapshot,
)


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUEST_SCHEMA = "standard_astro_registry_release_request_v1"
_IMPORT_SCHEMA = "standard_astro_registry_release_import_v1"
_IMPORT_MODE = "protected_offline_registry_release"
_IMPORT_STATUS = "SIGNED_READY_FOR_DEPLOYMENT"
_REQUEST_FIELDS = {
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


class RegistryReleaseImportError(ValueError):
    """Stable fail-closed error surfaced by the internal callback."""

    def __init__(self, code: str, *, status_code: int = 422):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _fail_from_registry(exc: WorkflowRegistryError) -> RegistryReleaseImportError:
    return RegistryReleaseImportError(exc.code, status_code=exc.status_code)


def _identity(entry: Mapping[str, Any]) -> tuple[str, str]:
    workflow = entry.get("workflow")
    if not isinstance(workflow, Mapping):
        raise RegistryReleaseImportError("registry_entry_invalid")
    identity = (
        str(workflow.get("workflow_id") or ""),
        str(workflow.get("version") or ""),
    )
    if not all(identity):
        raise RegistryReleaseImportError("registry_entry_identity_invalid")
    return identity


def _json_equal(left: Any, right: Any) -> bool:
    """Compare persisted JSON semantically (tuples become arrays on reload)."""

    try:
        return hmac.compare_digest(jcs_canonicalize(left), jcs_canonicalize(right))
    except (TypeError, ValueError):
        # Fall back through the same strict JSON encoder used by the catalog;
        # this handles in-session tuple values before a JSON column reload.
        try:
            return hmac.compare_digest(canonical_json(left), canonical_json(right))
        except (TypeError, ValueError):
            return False


def _validate_pending_request(
    request: WorkflowRegistryRelease,
    *,
    expected_request_id: uuid.UUID,
    expected_request_hash: str,
) -> dict[str, Any]:
    manifest = copy.deepcopy(dict(request.manifest or {}))
    if (
        request.id != expected_request_id
        or request.status != "PENDING_SIGNATURE"
        or request.signature is not None
        or request.key_id is not None
        or request.public_key_fingerprint is not None
        or set(manifest) != _REQUEST_FIELDS
        or manifest.get("schema_version") != _REQUEST_SCHEMA
        or manifest.get("request_id") != str(request.id)
        or manifest.get("request_status") != "PENDING_SIGNATURE"
        or not _SHA256.fullmatch(
            str(manifest.get("requested_by_actor_hash") or "")
        )
        or manifest.get("runtime_registry_modified") is not False
        or manifest.get("signature_required") is not True
    ):
        raise RegistryReleaseImportError(
            "registry_release_request_not_pending", status_code=409
        )
    calculated_request_hash = "sha256:" + sha256_json(manifest)
    if (
        not _SHA256.fullmatch(expected_request_hash)
        or not hmac.compare_digest(request.manifest_hash, expected_request_hash)
        or not hmac.compare_digest(calculated_request_hash, expected_request_hash)
    ):
        raise RegistryReleaseImportError(
            "registry_release_request_hash_mismatch", status_code=409
        )
    operations = manifest.get("operation_sequence")
    new_operations = manifest.get("new_operations")
    entries = manifest.get("entries")
    status_changes = manifest.get("status_changes")
    context = manifest.get("context")
    if not all(
        isinstance(value, list)
        for value in (operations, new_operations, entries, status_changes)
    ) or not isinstance(context, dict):
        raise RegistryReleaseImportError("registry_release_operations_invalid")
    if not operations:
        raise RegistryReleaseImportError("registry_release_operations_empty")
    if not new_operations or operations[-len(new_operations) :] != new_operations:
        raise RegistryReleaseImportError("registry_release_operation_chain_invalid")
    if (manifest.get("previous_request_hash") is None) != (
        len(operations) == len(new_operations)
    ):
        raise RegistryReleaseImportError("registry_release_operation_chain_invalid")
    expected_new_operations = [
        {"operation": "UPSERT_ENTRY", "entry": entry, "context": context}
        for entry in entries
    ] + [
        {
            "operation": "SET_ENTRY_STATUS",
            "status_change": change,
            "context": context,
        }
        for change in status_changes
    ]
    if not _json_equal(new_operations, expected_new_operations):
        raise RegistryReleaseImportError("registry_release_new_operations_mismatch")
    operation_hash = "sha256:" + sha256_json(operations)
    if not hmac.compare_digest(
        str(manifest.get("operation_sequence_hash") or ""), operation_hash
    ):
        raise RegistryReleaseImportError(
            "registry_release_operation_sequence_hash_mismatch"
        )
    if manifest.get("previous_request_hash") is not None and not _SHA256.fullmatch(
        str(manifest["previous_request_hash"])
    ):
        raise RegistryReleaseImportError("registry_release_predecessor_invalid")
    return manifest


async def _validate_request_chain_position(
    db: AsyncSession,
    request: WorkflowRegistryRelease,
    *,
    require_head: bool,
) -> None:
    try:
        chain = await registry_release_request_chain(db, lock=True)
    except FoundryCatalogError as exc:
        raise RegistryReleaseImportError(
            exc.error_class,
            status_code=exc.status_code,
        ) from exc
    indexes = {row.id: index for index, row in enumerate(chain)}
    index = indexes.get(request.id)
    if index is None:
        raise RegistryReleaseImportError(
            "registry_release_request_chain_missing", status_code=409
        )
    if require_head and index != len(chain) - 1:
        raise RegistryReleaseImportError(
            "registry_release_request_not_chain_head", status_code=409
        )


async def _catalog_entry_by_identity(
    db: AsyncSession,
    identity: tuple[str, str],
) -> WorkflowRegistryEntry:
    row = await db.scalar(
        select(WorkflowRegistryEntry).where(
            WorkflowRegistryEntry.workflow_id == identity[0],
            WorkflowRegistryEntry.workflow_version == identity[1],
        )
    )
    if row is None:
        raise RegistryReleaseImportError(
            "registry_release_catalog_entry_missing", status_code=409
        )
    return row


async def _verify_catalog_binding(
    db: AsyncSession,
    entry: Mapping[str, Any],
    context: Mapping[str, Any],
) -> WorkflowRegistryEntry:
    identity = _identity(entry)
    row = await _catalog_entry_by_identity(db, identity)
    version = await db.get(FoundryCandidateVersion, row.candidate_version_id)
    candidate = await db.get(FoundryCandidate, row.candidate_id)
    attestation = await db.get(
        FoundryFormalBuildAttestation, row.formal_build_attestation_id
    )
    required_context = {
        "candidate_db_id",
        "candidate_version_db_id",
        "formal_build_attestation_id",
        "formal_build_attestation_receipt_sha256",
        "formal_build_attestation_artifact_sha256",
        "formal_build_git_commit",
        "formal_build_sbom_sha256",
        "formal_build_test_report_sha256",
        "formal_build_release_audit_sha256",
        "formal_build_release_audit_receipts",
        "formal_build_sigstore_bundle_sha256",
        "formal_build_provenance_sha256",
    }
    if (
        version is None
        or candidate is None
        or attestation is None
        or version.candidate_id != candidate.id
        or row.candidate_version_hash != version.version_hash
        or str(entry.get("candidate_id") or "") != str(candidate.id)
        or entry.get("candidate_version") != version.version_number
        or entry.get("candidate_version_hash") != version.version_hash
        or entry.get("workflow_spec_hash")
        != row.release_entry.get("workflow_spec_hash")
        or entry.get("approval_attestation_hash")
        != row.release_entry.get("approval_attestation_hash")
        or entry.get("build_attestation_hash")
        != row.release_entry.get("build_attestation_hash")
        or entry.get("worker_image_digest") != row.worker_image_digest
        or attestation.candidate_id != candidate.id
        or attestation.candidate_version_id != version.id
        or attestation.candidate_version_hash != version.version_hash
        or attestation.formal_worker_image_digest != row.worker_image_digest
        or set(context) != required_context
        or context.get("candidate_db_id") != str(candidate.id)
        or context.get("candidate_version_db_id") != str(version.id)
        or context.get("formal_build_attestation_id") != str(attestation.id)
        or context.get("formal_build_attestation_receipt_sha256")
        != attestation.receipt_hash
        or context.get("formal_build_attestation_artifact_sha256")
        != attestation.attestation_artifact_hash
        or context.get("formal_build_git_commit") != attestation.git_commit
        or context.get("formal_build_sbom_sha256")
        != attestation.formal_sbom_hash
        or context.get("formal_build_test_report_sha256")
        != attestation.test_report_hash
        or context.get("formal_build_release_audit_sha256")
        != attestation.formal_release_audit_hash
        or context.get("formal_build_release_audit_receipts")
        != attestation.formal_release_audit_receipts
        or context.get("formal_build_sigstore_bundle_sha256")
        != attestation.sigstore_bundle_hash
        or context.get("formal_build_provenance_sha256")
        != attestation.provenance_hash
    ):
        raise RegistryReleaseImportError(
            "registry_release_catalog_binding_mismatch", status_code=409
        )
    return row


def _require_operation_shape(
    operation: Any,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if not isinstance(operation, dict) or not isinstance(
        operation.get("context"), dict
    ):
        raise RegistryReleaseImportError("registry_release_operation_invalid")
    kind = operation.get("operation")
    if kind == "UPSERT_ENTRY" and set(operation) == {
        "operation",
        "entry",
        "context",
    } and isinstance(operation.get("entry"), dict):
        return kind, operation["entry"], operation["context"]
    if kind == "SET_ENTRY_STATUS" and set(operation) == {
        "operation",
        "status_change",
        "context",
    } and isinstance(operation.get("status_change"), dict):
        return kind, operation["status_change"], operation["context"]
    raise RegistryReleaseImportError("registry_release_operation_invalid")


async def _base_overlay(
    _db: AsyncSession,
    manifest: Mapping[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    base = builtin_registry_identity()
    if (
        base["registry_epoch"] != manifest.get("base_registry_epoch")
        or base["registry_hash"] != manifest.get("base_registry_hash")
    ):
        raise RegistryReleaseImportError(
            "registry_release_builtin_base_mismatch", status_code=409
        )
    # Every request contains the full operation history and every signer starts
    # from an empty candidate overlay over the immutable built-in registry.
    return {}


async def _replay_request(
    db: AsyncSession,
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    entries = await _base_overlay(db, manifest)
    for raw_operation in manifest["operation_sequence"]:
        kind, value, context = _require_operation_shape(raw_operation)
        if kind == "UPSERT_ENTRY":
            await _verify_catalog_binding(db, value, context)
            identity = _identity(value)
            prior = entries.get(identity)
            if prior is not None and not _json_equal(prior, value):
                raise RegistryReleaseImportError(
                    "registry_release_entry_identity_conflict", status_code=409
                )
            entries[identity] = copy.deepcopy(value)
            continue

        required = {
            "registry_entry_id",
            "registry_entry_hash",
            "workflow_id",
            "workflow_version",
            "requested_status",
            "reason",
        }
        target = str(value.get("requested_status") or "")
        supersede_fields = {
            "superseded_by_workflow_id",
            "superseded_by_workflow_version",
        }
        expected_fields = required | supersede_fields if target == "SUPERSEDED" else required
        if set(value) != expected_fields:
            raise RegistryReleaseImportError("registry_release_status_change_invalid")
        identity = (str(value["workflow_id"]), str(value["workflow_version"]))
        current = entries.get(identity)
        if current is None:
            raise RegistryReleaseImportError(
                "registry_release_status_entry_missing", status_code=409
            )
        row = await _catalog_entry_by_identity(db, identity)
        if (
            str(row.id) != str(value["registry_entry_id"])
            or not hmac.compare_digest(
                str(row.registry_entry_hash or ""),
                str(value["registry_entry_hash"]),
            )
        ):
            raise RegistryReleaseImportError(
                "registry_release_status_entry_mismatch", status_code=409
            )
        reason = str(value["reason"] or "").strip()
        if target not in {"REGISTERED", "SUSPENDED", "SUPERSEDED", "REVOKED"}:
            raise RegistryReleaseImportError("registry_release_status_invalid")
        if target != "REGISTERED" and not reason:
            raise RegistryReleaseImportError("registry_release_status_reason_missing")
        if target == "SUPERSEDED":
            replacement = (
                str(value["superseded_by_workflow_id"]),
                str(value["superseded_by_workflow_version"]),
            )
            replacement_entry = entries.get(replacement)
            if (
                replacement[0] != identity[0]
                or replacement == identity
                or replacement_entry is None
                or replacement_entry.get("workflow", {}).get("state")
                != "REGISTERED"
            ):
                raise RegistryReleaseImportError(
                    "registry_release_supersede_replacement_invalid",
                    status_code=409,
                )
        try:
            entries[identity] = build_registry_status_entry(
                current,
                target,  # type: ignore[arg-type]
                reason=reason or None,
            )
        except WorkflowRegistryError as exc:
            raise _fail_from_registry(exc) from exc
    return [entries[key] for key in sorted(entries)]


async def _require_current_candidate_policy(
    db: AsyncSession,
    entries: Any,
) -> None:
    """Revalidate every executable entry against today's Candidate policy.

    Historical UPSERT operations remain in the append-only release chain even
    after a corrective SUSPEND or REVOKE.  Gate the final executable state so
    an invalid legacy candidate cannot become REGISTERED while still allowing
    operators to publish a release that removes it from execution.
    """

    if not isinstance(entries, list):
        raise RegistryReleaseImportError(
            "registry_release_candidate_policy_unverifiable",
            status_code=409,
        )
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise RegistryReleaseImportError(
                "registry_release_candidate_policy_unverifiable",
                status_code=409,
            )
        workflow = entry.get("workflow")
        if not isinstance(workflow, Mapping):
            raise RegistryReleaseImportError(
                "registry_release_candidate_policy_unverifiable",
                status_code=409,
            )
        if str(workflow.get("state") or "") != "REGISTERED":
            continue
        row = await _catalog_entry_by_identity(db, _identity(entry))
        version = await db.get(FoundryCandidateVersion, row.candidate_version_id)
        if (
            version is None
            or version.candidate_id != row.candidate_id
            or version.version_hash != row.candidate_version_hash
        ):
            raise RegistryReleaseImportError(
                "registry_release_candidate_policy_unverifiable",
                status_code=409,
            )
        try:
            policy_invalid = candidate_bundle_contains_formal_claim_escape(
                version.candidate_bundle
            )
        except Exception as exc:
            raise RegistryReleaseImportError(
                "registry_release_candidate_policy_unverifiable",
                status_code=409,
            ) from exc
        if policy_invalid:
            raise RegistryReleaseImportError(
                "registry_release_candidate_policy_invalid",
                status_code=409,
            )


def _public_key_fingerprint(public_key: str) -> str:
    try:
        raw = base64.b64decode(public_key, validate=True)
    except (TypeError, ValueError) as exc:
        raise RegistryReleaseImportError("registry_public_key_invalid") from exc
    if len(raw) != 32:
        raise RegistryReleaseImportError("registry_public_key_invalid")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


async def export_pending_registry_release_request(
    db: AsyncSession,
    *,
    release_request_id: uuid.UUID,
    release_request_hash: str,
) -> dict[str, Any]:
    """Export only the current chain head for the protected offline signer."""

    if not _SHA256.fullmatch(str(release_request_hash or "")):
        raise RegistryReleaseImportError("registry_release_request_hash_invalid")
    chain = []
    try:
        chain = await registry_release_request_chain(db, lock=True)
    except FoundryCatalogError as exc:
        raise RegistryReleaseImportError(
            exc.error_class,
            status_code=exc.status_code,
        ) from exc
    if not chain:
        raise RegistryReleaseImportError(
            "registry_release_request_not_found", status_code=404
        )
    request = next((row for row in chain if row.id == release_request_id), None)
    if request is None:
        raise RegistryReleaseImportError(
            "registry_release_request_not_found", status_code=404
        )
    manifest = _validate_pending_request(
        request,
        expected_request_id=release_request_id,
        expected_request_hash=release_request_hash,
    )
    if chain[-1].id != request.id:
        raise RegistryReleaseImportError(
            "registry_release_request_not_chain_head", status_code=409
        )
    expected_entries = await _replay_request(db, manifest)
    await _require_current_candidate_policy(db, expected_entries)
    return {
        "schema_version": "standard_astro_registry_release_export_v1",
        "release_request_id": str(request.id),
        "release_request_sha256": request.manifest_hash,
        "pending_release_request": copy.deepcopy(manifest),
    }


async def record_signed_registry_release_import(
    db: AsyncSession,
    *,
    receipt: dict[str, Any],
    trusted_public_keys: Mapping[str, str],
) -> tuple[WorkflowRegistryReleaseImport, bool]:
    """Verify and append one signed release receipt without runtime mutation.

    Returns ``(row, created)``.  An exact retry is idempotent; reusing a
    request id, epoch, snapshot, or receipt for different content is a 409.
    """

    body = copy.deepcopy(receipt)
    declared_receipt_hash = str(body.pop("receipt_sha256", ""))
    expected_receipt_hash = "sha256:" + hashlib.sha256(
        jcs_canonicalize(body)
    ).hexdigest()
    if not hmac.compare_digest(declared_receipt_hash, expected_receipt_hash):
        raise RegistryReleaseImportError("registry_release_receipt_hash_mismatch")
    try:
        request_id = uuid.UUID(str(body["release_request_id"]))
    except (KeyError, ValueError) as exc:
        raise RegistryReleaseImportError("registry_release_request_id_invalid") from exc
    request_hash = str(body.get("release_request_sha256") or "")
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        # Serialize the head check against creation of the next request.
        await db.execute(text("SELECT pg_advisory_xact_lock(734291105)"))
    request = await db.scalar(
        select(WorkflowRegistryRelease)
        .where(WorkflowRegistryRelease.id == request_id)
        .with_for_update()
    )
    if request is None:
        raise RegistryReleaseImportError(
            "registry_release_request_not_found", status_code=404
        )
    manifest = _validate_pending_request(
        request,
        expected_request_id=request_id,
        expected_request_hash=request_hash,
    )
    existing = await db.scalar(
        select(WorkflowRegistryReleaseImport).where(
            WorkflowRegistryReleaseImport.release_request_id == request_id
        )
    )
    await _validate_request_chain_position(
        db,
        request,
        require_head=existing is None,
    )
    signed_snapshot = body.get("signed_snapshot")
    if not isinstance(signed_snapshot, dict):
        raise RegistryReleaseImportError("registry_snapshot_signature_missing")
    try:
        signed_payload = verify_signed_registry_snapshot(
            signed_snapshot,
            trusted_public_keys,
        )
    except WorkflowRegistryError as exc:
        raise _fail_from_registry(exc) from exc

    if (
        body.get("schema_version") != _IMPORT_SCHEMA
        or body.get("import_mode") != _IMPORT_MODE
        or body.get("base_registry_epoch") != manifest["base_registry_epoch"]
        or body.get("base_registry_hash") != manifest["base_registry_hash"]
        or signed_payload.get("base_registry_epoch")
        != manifest["base_registry_epoch"]
        or signed_payload.get("base_registry_hash") != manifest["base_registry_hash"]
        or body.get("registry_epoch") != signed_payload.get("registry_epoch")
        or body.get("registry_snapshot_sha256")
        != signed_snapshot.get("payload_sha256")
        or signed_payload.get("operation_sequence")
        != len(manifest["operation_sequence"])
    ):
        raise RegistryReleaseImportError(
            "registry_release_request_snapshot_mismatch", status_code=409
        )
    expected_entries = await _replay_request(db, manifest)
    await _require_current_candidate_policy(db, expected_entries)
    if not _json_equal(signed_payload.get("entries"), expected_entries):
        raise RegistryReleaseImportError(
            "registry_release_signed_entries_mismatch", status_code=409
        )
    if body.get("complete_entry_count") != len(expected_entries):
        raise RegistryReleaseImportError("registry_release_entry_count_mismatch")
    signature = signed_snapshot.get("signature")
    if not isinstance(signature, dict):
        raise RegistryReleaseImportError("registry_snapshot_signature_missing")
    key_id = str(signature.get("key_id") or "")
    if body.get("signing_key_id") != key_id or key_id not in trusted_public_keys:
        raise RegistryReleaseImportError("registry_snapshot_key_untrusted")
    public_key_fingerprint = _public_key_fingerprint(trusted_public_keys[key_id])
    if body.get("signing_public_key_sha256") != public_key_fingerprint:
        raise RegistryReleaseImportError("registry_release_public_key_hash_mismatch")

    snapshot_hash = str(signed_snapshot.get("payload_sha256") or "")
    if existing is not None:
        exact_replay = (
            existing.release_request_hash == request_hash
            and existing.registry_snapshot_hash == snapshot_hash
            and existing.receipt_hash == declared_receipt_hash
            and hmac.compare_digest(
                jcs_canonicalize(existing.signed_snapshot),
                jcs_canonicalize(signed_snapshot),
            )
        )
        if not exact_replay:
            raise RegistryReleaseImportError(
                "registry_release_import_conflict", status_code=409
            )
        return existing, False
    reused = await db.scalar(
        select(WorkflowRegistryReleaseImport.id).where(
            (
                WorkflowRegistryReleaseImport.registry_epoch
                == str(signed_payload["registry_epoch"])
            )
            | (WorkflowRegistryReleaseImport.registry_snapshot_hash == snapshot_hash)
            | (WorkflowRegistryReleaseImport.receipt_hash == declared_receipt_hash)
        )
    )
    if reused is not None:
        raise RegistryReleaseImportError(
            "registry_release_import_replay_conflict", status_code=409
        )

    row = WorkflowRegistryReleaseImport(
        release_request_id=request_id,
        release_request_hash=request_hash,
        base_registry_epoch=str(manifest["base_registry_epoch"]),
        base_registry_hash=str(manifest["base_registry_hash"]),
        registry_epoch=str(signed_payload["registry_epoch"]),
        registry_snapshot_hash=snapshot_hash,
        signing_key_id=key_id,
        signing_public_key_fingerprint=public_key_fingerprint,
        signed_snapshot=signed_snapshot,
        receipt_hash=declared_receipt_hash,
        status=_IMPORT_STATUS,
        runtime_registry_modified=False,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise RegistryReleaseImportError(
            "registry_release_import_conflict", status_code=409
        ) from exc
    await db.refresh(row)
    return row, True


def _latest_status_contexts(
    manifest: Mapping[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    contexts: dict[tuple[str, str], dict[str, Any]] = {}
    for operation in manifest.get("operation_sequence") or []:
        if (
            isinstance(operation, dict)
            and operation.get("operation") == "SET_ENTRY_STATUS"
            and isinstance(operation.get("status_change"), dict)
        ):
            change = dict(operation["status_change"])
            identity = (
                str(change.get("workflow_id") or ""),
                str(change.get("workflow_version") or ""),
            )
            if all(identity):
                contexts[identity] = change
    return contexts


async def reconcile_active_registry_projection(
    db: AsyncSession,
    *,
    trusted_public_keys: Mapping[str, str],
) -> dict[str, Any]:
    """Project a boot-verified signed release into mutable control-plane rows.

    This is deliberately separate from the import callback.  Before a fixed
    signed file is active, it performs no mutation.  After restart, it verifies
    that the active payload has an exact append-only import receipt, rejects an
    older-than-imported release, and then projects only status fields/events.
    """

    runtime = active_registry_identity()
    if runtime.get("release_kind") == "builtin_code_registry":
        return {
            "status": "BUILTIN_REGISTRY_ACTIVE",
            "registry_epoch": runtime["registry_epoch"],
            "updated_entries": 0,
        }
    if (
        runtime.get("release_kind") != "signed_registry_release"
        or runtime.get("signature_algorithm") != "ed25519"
        or not runtime.get("signature_key_id")
        or not _SHA256.fullmatch(str(runtime.get("signed_payload_sha256") or ""))
    ):
        raise RegistryReleaseImportError(
            "registry_runtime_identity_invalid", status_code=503
        )
    receipt = await db.scalar(
        select(WorkflowRegistryReleaseImport).where(
            WorkflowRegistryReleaseImport.registry_epoch
            == str(runtime["registry_epoch"]),
            WorkflowRegistryReleaseImport.registry_snapshot_hash
            == str(runtime["signed_payload_sha256"]),
        )
    )
    if receipt is None:
        raise RegistryReleaseImportError(
            "registry_runtime_import_receipt_missing", status_code=503
        )
    if (
        receipt.status != _IMPORT_STATUS
        or receipt.runtime_registry_modified is not False
        or receipt.signing_key_id != runtime["signature_key_id"]
    ):
        raise RegistryReleaseImportError(
            "registry_runtime_import_receipt_invalid", status_code=503
        )
    try:
        payload = verify_signed_registry_snapshot(
            receipt.signed_snapshot,
            trusted_public_keys,
        )
    except WorkflowRegistryError as exc:
        raise _fail_from_registry(exc) from exc
    base = builtin_registry_identity()
    if (
        payload.get("registry_epoch") != runtime["registry_epoch"]
        or receipt.registry_snapshot_hash
        != receipt.signed_snapshot.get("payload_sha256")
        or payload.get("base_registry_epoch") != base["registry_epoch"]
        or payload.get("base_registry_hash") != base["registry_hash"]
    ):
        raise RegistryReleaseImportError(
            "registry_runtime_release_binding_mismatch", status_code=503
        )
    request = await db.get(WorkflowRegistryRelease, receipt.release_request_id)
    if request is None:
        raise RegistryReleaseImportError(
            "registry_runtime_release_request_missing", status_code=503
        )
    manifest = _validate_pending_request(
        request,
        expected_request_id=request.id,
        expected_request_hash=receipt.release_request_hash,
    )
    await _validate_request_chain_position(db, request, require_head=False)

    # A newer verified import is not automatically ACTIVE and must not make a
    # restart of the currently persisted activation impossible. The separate
    # activation startup gate compares this bundle against append-only ACTIVE
    # receipts and rejects an actual rollback; preflight separately requires a
    # never-activated candidate to remain the imported chain head.

    runtime_entries = {
        (str(item["workflow_id"]), str(item["workflow_version"])): item
        for item in list_formal_workflows(include_inactive=True)
    }
    status_contexts = _latest_status_contexts(manifest)
    signed_entries = payload.get("entries")
    if not isinstance(signed_entries, list):
        raise RegistryReleaseImportError(
            "registry_runtime_signed_entries_invalid", status_code=503
        )
    await _require_current_candidate_policy(db, signed_entries)
    updated = 0
    reconciled_at = _utc_now()
    for signed_entry in signed_entries:
        if not isinstance(signed_entry, dict):
            raise RegistryReleaseImportError(
                "registry_runtime_signed_entries_invalid", status_code=503
            )
        identity = _identity(signed_entry)
        row = await _catalog_entry_by_identity(db, identity)
        candidate = await db.get(FoundryCandidate, row.candidate_id)
        runtime_entry = runtime_entries.get(identity)
        workflow = signed_entry.get("workflow")
        if candidate is None or not isinstance(workflow, dict) or runtime_entry is None:
            raise RegistryReleaseImportError(
                "registry_runtime_catalog_binding_missing", status_code=503
            )
        state = str(workflow.get("state") or "")
        if state not in {"REGISTERED", "SUSPENDED", "SUPERSEDED", "REVOKED"}:
            raise RegistryReleaseImportError(
                "registry_runtime_status_invalid", status_code=503
            )
        try:
            expected_signed_entry = build_registry_status_entry(
                row.release_entry,
                state,  # type: ignore[arg-type]
                reason=(
                    str(workflow.get("revocation_reason") or "") or None
                ),
            )
        except WorkflowRegistryError as exc:
            raise _fail_from_registry(exc) from exc
        if (
            not _json_equal(signed_entry, expected_signed_entry)
            or str(signed_entry.get("candidate_id") or "") != str(candidate.id)
            or signed_entry.get("candidate_version_hash")
            != row.candidate_version_hash
            or runtime_entry.get("state") != state
            or runtime_entry.get("registry_entry_hash")
            != signed_entry.get("registry_entry_hash")
            or runtime_entry.get("registry_epoch") != runtime["registry_epoch"]
        ):
            raise RegistryReleaseImportError(
                "registry_runtime_catalog_binding_mismatch", status_code=503
            )

        status_context = status_contexts.get(identity, {})
        reason = (
            str(status_context.get("reason") or "").strip()
            if state != "REGISTERED"
            else ""
        )
        candidate_status = {
            "REGISTERED": "PROMOTED",
            "SUSPENDED": "SUSPENDED",
            "SUPERSEDED": "SUPERSEDED",
            "REVOKED": "REVOKED",
        }[state]
        desired_reason = reason or None
        needs_update = (
            row.status != state
            or candidate.status != candidate_status
            or row.status_reason != desired_reason
        )
        if not needs_update:
            continue
        row.status = state
        row.status_reason = desired_reason
        candidate.status = candidate_status
        if state in {"SUSPENDED", "SUPERSEDED"} and row.suspended_at is None:
            row.suspended_at = reconciled_at
        if state == "REVOKED" and row.revoked_at is None:
            row.revoked_at = reconciled_at
        await _append_event(
            db,
            candidate_id=candidate.id,
            candidate_version_id=row.candidate_version_id,
            event_type="REGISTRY_RUNTIME_RECONCILED",
            actor_kind="STARTUP_RECONCILER",
            actor_user_id=None,
            payload={
                "release_import_id": str(receipt.id),
                "release_request_id": str(request.id),
                "registry_epoch": runtime["registry_epoch"],
                "registry_snapshot_hash": receipt.registry_snapshot_hash,
                "registry_entry_hash": signed_entry["registry_entry_hash"],
                "projected_status": state,
                "candidate_status": candidate_status,
                "runtime_registry_observed": True,
            },
        )
        updated += 1
    await db.commit()
    return {
        "status": "SIGNED_REGISTRY_RECONCILED",
        "registry_epoch": runtime["registry_epoch"],
        "registry_snapshot_hash": receipt.registry_snapshot_hash,
        "updated_entries": updated,
    }


def serialize_registry_release_import(
    row: WorkflowRegistryReleaseImport,
) -> dict[str, Any]:
    return {
        "registry_release_import_id": str(row.id),
        "release_request_id": str(row.release_request_id),
        "release_request_hash": row.release_request_hash,
        "registry_epoch": row.registry_epoch,
        "registry_snapshot_hash": row.registry_snapshot_hash,
        "signing_key_id": row.signing_key_id,
        "status": row.status,
        "runtime_registry_modified": False,
        "activation_required": "pin_release_file_and_restart",
        "imported_at": row.imported_at.isoformat(),
    }


__all__ = [
    "RegistryReleaseImportError",
    "export_pending_registry_release_request",
    "reconcile_active_registry_projection",
    "record_signed_registry_release_import",
    "serialize_registry_release_import",
]
