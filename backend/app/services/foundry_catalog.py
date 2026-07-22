"""Service layer for non-formal workflow candidates and their durable ledger."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim_audit_records import ClaimAudit
from app.models.foundry_records import (
    CapabilityRequest,
    FoundryCandidate,
    FoundryCandidateEvent,
    FoundryCandidateVersion,
    FoundryDemoRun,
    FoundryFormalBuildAttestation,
    FoundryReview,
    FoundryValidationRun,
    WorkflowRegistryEntry,
    WorkflowRegistryRelease,
)
from app.services.foundry_candidate_identity import candidate_version_sha256
from app.services.foundry_evidence_policy import (
    NON_FORMAL_EVIDENCE_CLASS,
    candidate_bundle_contains_formal_claim_escape,
    contains_formal_claim_escape,
    demo_report_contract_issue,
)


CANDIDATE_STATUSES = frozenset(
    {
        "DRAFT",
        "BUILDING",
        "VALIDATING",
        "DEMO_RECORDED",
        "REVIEW_PENDING",
        "APPROVED",
        "REJECTED",
        "PROMOTED",
        "SUSPENDED",
        "SUPERSEDED",
        "REVOKED",
    }
)
REQUEST_STATUSES = frozenset({"SUBMITTED", "TRIAGED", "MERGED", "CLOSED"})
GENERATION_ROUTES = frozenset({"COMPOSITION", "DATA_ADAPTER", "SCIENCE_CODE"})
RISK_LEVELS = frozenset({"R0", "R1", "R2", "R3"})
DEMO_STATUSES = frozenset({"PASSED", "PARTIAL", "FAILED"})
REVIEW_DECISIONS = frozenset({"APPROVED", "REJECTED", "CHANGES_REQUESTED"})
REVIEW_SCOPES = frozenset({"ENGINEERING", "SCIENTIFIC"})
AI_DRAFT_MAX_ATTEMPTS = 3
AI_DRAFT_DISPATCH_LEASE = timedelta(minutes=5)
AI_DRAFT_WORKFLOW_LEASE = timedelta(hours=1)
VALIDATION_MAX_ATTEMPTS = 3
VALIDATION_DISPATCH_LEASE = timedelta(minutes=5)
VALIDATION_WORKFLOW_LEASE = timedelta(hours=1)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IMAGE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_FORMAL_BUILD_SIGNING_DOMAIN = b"standard-astro/formal-build-attestation/v2\0"
_FORMAL_BUILD_BUNDLE_SCHEMA = "standard_astro_formal_build_attestation_bundle_v2"
_FORMAL_BUILD_PAYLOAD_SCHEMA = "standard_astro_formal_build_attestation_v2"
FORMAL_BUILD_MAX_ATTEMPTS = 3
FORMAL_BUILD_DISPATCH_LEASE = timedelta(minutes=5)
FORMAL_BUILD_ATTEMPT_TIMEOUT = timedelta(hours=6)
_FORMAL_RELEASE_AUDIT_SCHEMA = "standard_astro_formal_release_audit_v1"
_FORMAL_RELEASE_RECEIPT_KEYS = frozenset(
    {
        "dependency_lock",
        "secret_scan",
        "static_audit",
        "linux_amd64_dependency_integrity",
        "linux_amd64_license_policy",
        "linux_amd64_environment",
        "linux_arm64_dependency_integrity",
        "linux_arm64_license_policy",
        "linux_arm64_environment",
    }
)
_FINGERPRINT_KEYS = (
    "gap_code",
    "dataset_key",
    "dataset_keys",
    "workflow_key",
    "supported_selection",
    "source_profile_key",
    "claim_schema",
    "claim_type",
    "model",
    "parameter",
    "statistic",
    "evidence_kind",
)


class FoundryCatalogError(ValueError):
    """Classified API-safe foundry error."""

    def __init__(self, error_class: str, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.error_class = error_class
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class FormalBuildDispatchPlan:
    """One durable, bounded formal-build dispatch decision."""

    binding: dict[str, str]
    request_event: FoundryCandidateEvent
    attempt_event: FoundryCandidateEvent | None
    attempt_id: uuid.UUID | None
    attempt_number: int
    already_active: bool
    dispatch_status: str
    retry_after: datetime | None
    attestation: FoundryFormalBuildAttestation | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def capability_gap_id(
    audit_id: uuid.UUID | str,
    index: int,
    gap: dict[str, Any],
) -> str:
    """Return a stable audit-local identifier without trusting the browser."""

    digest = sha256_json(
        {
            "audit_id": str(audit_id),
            "gap_index": int(index),
            "gap": gap,
        }
    )
    return f"gap_{digest}"


def serialize_capability_gaps(
    audit_id: uuid.UUID | str,
    gaps: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, raw_gap in enumerate(gaps):
        if not isinstance(raw_gap, dict):
            continue
        gap = dict(raw_gap)
        gap["gap_id"] = capability_gap_id(audit_id, index, raw_gap)
        output.append(gap)
    return output


def _resolve_audit_gap(audit: ClaimAudit, gap_id: str) -> dict[str, Any]:
    for index, gap in enumerate(audit.capability_gaps or []):
        if not isinstance(gap, dict):
            continue
        if capability_gap_id(audit.id, index, gap) == gap_id:
            return dict(gap)
    raise FoundryCatalogError(
        "capability_gap_not_found",
        "The capability gap does not belong to this Claim Audit",
        status_code=404,
    )


def gap_descriptor(gap: dict[str, Any]) -> dict[str, Any]:
    descriptor = {
        key: gap[key]
        for key in _FINGERPRINT_KEYS
        if key in gap and gap[key] not in (None, "", [], {})
    }
    gap_code = str(descriptor.get("gap_code") or "").strip()
    if not gap_code:
        raise FoundryCatalogError(
            "unstructured_capability_gap",
            "The selected gap has no stable gap_code and cannot enter the Foundry",
        )
    descriptor["gap_code"] = gap_code
    descriptor["research_domain"] = "cosmology"
    return descriptor


def gap_fingerprint(gap: dict[str, Any]) -> str:
    return sha256_json(gap_descriptor(gap))


def _require_sha256(value: str, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise FoundryCatalogError("invalid_content_hash", f"{field} must be a SHA-256 hex digest")
    return normalized


def _require_image_digest(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _IMAGE_DIGEST_RE.fullmatch(normalized):
        raise FoundryCatalogError(
            "invalid_runner_image_digest",
            "runner_image_digest must be a sha256:<64 hex> digest",
        )
    return normalized


def _validate_formal_release_audit(
    value: Any,
    *,
    source_tree_hash: str,
    dependency_lock_hash: str,
    formal_sbom_hash: str,
) -> tuple[str, dict[str, Any]]:
    required = {
        "schema_version",
        "status",
        "policy_id",
        "policy_sha256",
        "source_tree_sha256",
        "dependency_lock_sha256",
        "formal_sbom_sha256",
        "architectures",
        "receipts",
        "gates",
        "advisory_database_checked",
        "vulnerability_status",
        "legal_review_complete",
        "aggregate_receipt_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise FoundryCatalogError(
            "formal_release_audit_invalid",
            "Formal release supply-chain receipt is malformed",
        )
    receipts = value.get("receipts")
    if (
        value.get("schema_version") != _FORMAL_RELEASE_AUDIT_SCHEMA
        or value.get("status") != "PASSED"
        or value.get("architectures") != ["linux/amd64", "linux/arm64"]
        or value.get("gates")
        != {
            "dependency_integrity": True,
            "license_inventory_policy": True,
            "tracked_source_secret_scan": True,
        }
        or value.get("advisory_database_checked") is not False
        or value.get("vulnerability_status") != "NOT_EVALUATED"
        or value.get("legal_review_complete") is not False
        or value.get("source_tree_sha256") != source_tree_hash
        or value.get("dependency_lock_sha256") != dependency_lock_hash
        or value.get("formal_sbom_sha256") != formal_sbom_hash
        or not isinstance(receipts, dict)
        or set(receipts) != _FORMAL_RELEASE_RECEIPT_KEYS
        or not str(value.get("policy_id") or "")
    ):
        raise FoundryCatalogError(
            "formal_release_audit_invalid",
            "Formal release supply-chain gates did not produce the registered receipt",
        )
    _require_sha256(str(value.get("policy_sha256") or ""), "release_audit.policy_sha256")
    for name, digest in receipts.items():
        _require_sha256(str(digest or ""), f"release_audit.receipts.{name}")
    aggregate_hash = _require_sha256(
        str(value.get("aggregate_receipt_sha256") or ""),
        "release_audit.aggregate_receipt_sha256",
    )
    return aggregate_hash, json.loads(canonical_json(value))


def _normalize_candidate_bundle(value: Any) -> dict[str, Any]:
    """Validate the data-only bundle consumed by the isolated Demo runner."""

    if not isinstance(value, dict):
        raise FoundryCatalogError(
            "invalid_candidate_bundle", "candidate_bundle must be an object"
        )
    try:
        from app.services.foundry_demo_runner import validate_candidate_bundle
    except ImportError:
        validate_candidate_bundle = None
    if validate_candidate_bundle is not None:
        try:
            return validate_candidate_bundle(value)
        except ValueError as exc:
            raise FoundryCatalogError(
                "invalid_candidate_bundle", str(exc)
            ) from exc
    required = {
        "schema_version",
        "candidate_id",
        "candidate_version",
        "proposed_workflow_id",
        "entrypoint_id",
        "risk_level",
        "workflow_spec",
        "source_pins",
        "fixture_hashes",
        "dependency_lock_sha256",
        "runner_definition_sha256",
        "generation",
        "limitations",
        "output_policy",
    }
    if set(value) != required or value.get("schema_version") != 1:
        raise FoundryCatalogError(
            "invalid_candidate_bundle", "candidate_bundle shape is not registered"
        )
    if not isinstance(value.get("candidate_version"), int) or isinstance(
        value.get("candidate_version"), bool
    ):
        raise FoundryCatalogError(
            "invalid_candidate_bundle", "candidate_version must be an integer"
        )
    if value.get("risk_level") not in RISK_LEVELS:
        raise FoundryCatalogError(
            "invalid_candidate_bundle", "candidate bundle risk level is unsupported"
        )
    if not isinstance(value.get("workflow_spec"), dict):
        raise FoundryCatalogError(
            "invalid_candidate_bundle", "candidate workflow_spec must be an object"
        )
    expected_policy = {
        "evidence_class": NON_FORMAL_EVIDENCE_CLASS,
        "publication_ready": False,
        "claim_eligible": False,
        "evidence_pack_allowed": False,
    }
    if value.get("output_policy") != expected_policy:
        raise FoundryCatalogError(
            "candidate_formal_claim_forbidden",
            "Candidate output policy must remain non-formal",
        )
    _require_sha256(value.get("dependency_lock_sha256"), "dependency_lock_sha256")
    _require_sha256(value.get("runner_definition_sha256"), "runner_definition_sha256")
    if candidate_bundle_contains_formal_claim_escape(value):
        raise FoundryCatalogError(
            "candidate_formal_claim_forbidden",
            "Candidate bundles cannot claim formal scientific support",
        )
    return json.loads(canonical_json(value))


async def _append_event(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    event_type: str,
    actor_kind: str,
    actor_user_id: uuid.UUID | None,
    payload: dict[str, Any],
    candidate_version_id: uuid.UUID | None = None,
) -> FoundryCandidateEvent:
    # PostgreSQL READ COMMITTED takes a fresh snapshot after this lock is
    # acquired.  Serializing every append on the durable candidate row means a
    # callback that waited here observes the event committed by the callback
    # ahead of it, rather than creating a second child of the same parent.
    candidate_row = await db.scalar(
        select(FoundryCandidate.id)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
    if candidate_row is None:
        raise FoundryCatalogError(
            "candidate_not_found",
            "Candidate event chain owner does not exist",
            status_code=404,
        )
    previous = await db.scalar(
        select(FoundryCandidateEvent)
        .where(FoundryCandidateEvent.candidate_id == candidate_id)
        .order_by(FoundryCandidateEvent.created_at.desc(), FoundryCandidateEvent.id.desc())
        .limit(1)
    )
    occurred_at = _utc_now()
    envelope = {
        "candidate_id": str(candidate_id),
        "candidate_version_id": str(candidate_version_id) if candidate_version_id else None,
        "event_type": event_type,
        "actor_kind": actor_kind,
        "actor_user_id": str(actor_user_id) if actor_user_id else None,
        "payload": payload,
        "previous_event_hash": previous.event_hash if previous else None,
        "occurred_at": occurred_at.isoformat(),
    }
    row = FoundryCandidateEvent(
        candidate_id=candidate_id,
        candidate_version_id=candidate_version_id,
        event_type=event_type,
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        event_payload=payload,
        previous_event_hash=previous.event_hash if previous else None,
        event_hash=sha256_json(envelope),
        created_at=occurred_at,
    )
    db.add(row)
    await db.flush()
    return row


async def _get_or_create_candidate(
    db: AsyncSession,
    *,
    descriptor: dict[str, Any],
    fingerprint: str,
) -> tuple[FoundryCandidate, bool]:
    existing = await db.scalar(
        select(FoundryCandidate).where(FoundryCandidate.gap_fingerprint == fingerprint)
    )
    if existing is not None:
        return existing, False
    candidate = FoundryCandidate(
        gap_fingerprint=fingerprint,
        gap_code=str(descriptor["gap_code"]),
        gap_descriptor=descriptor,
        status="DRAFT",
    )
    try:
        async with db.begin_nested():
            db.add(candidate)
            await db.flush()
    except IntegrityError:
        candidate = await db.scalar(
            select(FoundryCandidate).where(FoundryCandidate.gap_fingerprint == fingerprint)
        )
        if candidate is None:
            raise
        return candidate, False
    await _append_event(
        db,
        candidate_id=candidate.id,
        event_type="CANDIDATE_CREATED",
        actor_kind="SYSTEM",
        actor_user_id=None,
        payload={"gap_fingerprint": fingerprint, "status": "DRAFT"},
    )
    return candidate, True


async def create_capability_request(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audit: ClaimAudit,
    gap_id: str,
) -> CapabilityRequest:
    if audit.user_id != user_id:
        raise FoundryCatalogError(
            "claim_audit_not_found", "Claim Audit not found", status_code=404
        )
    gap = _resolve_audit_gap(audit, gap_id)
    descriptor = gap_descriptor(gap)
    fingerprint = sha256_json(descriptor)
    existing = await db.scalar(
        select(CapabilityRequest).where(
            CapabilityRequest.user_id == user_id,
            CapabilityRequest.audit_id == audit.id,
            CapabilityRequest.gap_id == gap_id,
        )
    )
    if existing is not None:
        return existing

    candidate, _created = await _get_or_create_candidate(
        db, descriptor=descriptor, fingerprint=fingerprint
    )
    request = CapabilityRequest(
        user_id=user_id,
        audit_id=audit.id,
        candidate_id=candidate.id,
        gap_id=gap_id,
        gap_fingerprint=fingerprint,
        gap_snapshot=gap,
        status="SUBMITTED",
    )
    try:
        async with db.begin_nested():
            db.add(request)
            await db.flush()
    except IntegrityError:
        request = await db.scalar(
            select(CapabilityRequest).where(
                CapabilityRequest.user_id == user_id,
                CapabilityRequest.audit_id == audit.id,
                CapabilityRequest.gap_id == gap_id,
            )
        )
        if request is None:
            raise
        return request
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate.id)
        .with_for_update()
    )
    if candidate is None:
        raise FoundryCatalogError(
            "candidate_not_found", "Foundry candidate not found", status_code=404
        )
    await _append_event(
        db,
        candidate_id=candidate.id,
        event_type="CAPABILITY_REQUEST_LINKED",
        actor_kind="USER",
        actor_user_id=user_id,
        payload={"request_id": str(request.id), "gap_id": gap_id},
    )
    await db.commit()
    await db.refresh(request)
    return request


def serialize_capability_request(
    row: CapabilityRequest,
    *,
    candidate: FoundryCandidate | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(row.id),
        "status": row.status,
        "audit_id": str(row.audit_id),
        "gap_id": row.gap_id,
        "gap_fingerprint": row.gap_fingerprint,
        "candidate_id": str(row.candidate_id) if row.candidate_id else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if candidate is not None:
        payload["candidate"] = {
            "id": str(candidate.id),
            "status": candidate.status,
            "risk_level": candidate.risk_level,
            "generation_route": candidate.generation_route,
        }
    return payload


def serialize_candidate_version(row: FoundryCandidateVersion) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "candidate_key": row.candidate_key,
        "version_number": row.version_number,
        "version_hash": row.version_hash,
        "workflow_id": row.workflow_id,
        "workflow_version": row.workflow_version,
        "workflow_spec_hash": row.workflow_spec_hash,
        "validation_runner_image_digest": row.validation_runner_image_digest,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def serialize_demo_run(
    row: FoundryDemoRun,
    *,
    version_number: int | None = None,
) -> dict[str, Any]:
    artifact_receipts = []
    for raw_artifact in row.artifact_manifest or []:
        if not isinstance(raw_artifact, dict):
            continue
        if set(raw_artifact) == {
            "path",
            "kind",
            "sha256",
            "bytes",
        }:
            receipt = {
                "name": raw_artifact["path"],
                "sha256": raw_artifact["sha256"],
                "size_bytes": raw_artifact["bytes"],
                "media_type": "text/plain; charset=utf-8",
            }
        else:
            receipt = {
                key: raw_artifact[key]
                for key in ("name", "sha256", "size_bytes", "media_type")
                if key in raw_artifact
            }
        if receipt:
            artifact_receipts.append(receipt)
    return {
        "candidate_id": str(row.candidate_id),
        "candidate_key": row.candidate_key,
        "candidate_version": version_number,
        "candidate_version_id": str(row.candidate_version_id),
        "candidate_version_hash": row.candidate_version_hash,
        "demo_run_id": str(row.id),
        "status": row.status,
        "evidence_class": NON_FORMAL_EVIDENCE_CLASS,
        "publication_ready": False,
        "claim_eligible": False,
        "evidence_pack_allowed": False,
        "limitations": list(row.limitations or []),
        "validation_summary": dict(row.validation_summary or {}),
        "result": dict(row.structured_result or {}),
        "failure_class": row.failure_class,
        "resource_usage": dict(row.resource_usage or {}),
        "environment": dict(row.environment or {}),
        "generation": dict(row.generation or {}),
        "source_pins": list(row.source_pins or []),
        "fixture_hashes": list(row.fixture_hashes or []),
        "data_hashes": dict(row.data_hashes or {}),
        "artifact_receipts": artifact_receipts,
        "receipt": {
            "demo_report_sha256": row.demo_report_hash,
            "candidate_bundle_sha256": row.candidate_bundle_hash,
            "workflow_spec_sha256": row.workflow_spec_hash,
            "code_tree_sha256": row.code_tree_hash,
            "dependency_lock_sha256": row.dependency_lock_hash,
            "runner_definition_sha256": row.runner_definition_hash,
            "sbom_sha256": row.sbom_hash,
            "environment_sha256": row.environment_sha256,
            "stdout_sha256": row.stdout_sha256,
            "stderr_sha256": row.stderr_sha256,
            "stdout_bytes": row.stdout_bytes,
            "stderr_bytes": row.stderr_bytes,
        },
        "validation_runner_image_digest": row.runner_image_digest,
        "started_at": row.started_at.isoformat(),
        "completed_at": row.completed_at.isoformat(),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def current_candidate_version(
    db: AsyncSession,
    candidate: FoundryCandidate,
) -> FoundryCandidateVersion | None:
    if candidate.current_version_number is None:
        return None
    return await db.scalar(
        select(FoundryCandidateVersion).where(
            FoundryCandidateVersion.candidate_id == candidate.id,
            FoundryCandidateVersion.version_number == candidate.current_version_number,
        )
    )


async def serialize_candidate(
    db: AsyncSession,
    candidate: FoundryCandidate,
    *,
    demo_limit: int = 10,
) -> dict[str, Any]:
    version = await current_candidate_version(db, candidate)
    all_versions = list(
        (
            await db.execute(
                select(FoundryCandidateVersion)
                .where(FoundryCandidateVersion.candidate_id == candidate.id)
                .order_by(FoundryCandidateVersion.version_number.desc())
            )
        )
        .scalars()
        .all()
    )
    demos = list(
        (
            await db.execute(
                select(FoundryDemoRun)
                .where(FoundryDemoRun.candidate_id == candidate.id)
                .order_by(FoundryDemoRun.created_at.desc())
                .limit(demo_limit)
            )
        )
        .scalars()
        .all()
    )
    version_numbers: dict[uuid.UUID, int] = {}
    if demos:
        versions = list(
            (
                await db.execute(
                    select(FoundryCandidateVersion).where(
                        FoundryCandidateVersion.id.in_(
                            [demo.candidate_version_id for demo in demos]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        version_numbers = {item.id: item.version_number for item in versions}
    return {
        "id": str(candidate.id),
        "status": candidate.status,
        "gap_fingerprint": candidate.gap_fingerprint,
        "gap_code": candidate.gap_code,
        "gap_descriptor": dict(candidate.gap_descriptor or {}),
        "risk_level": candidate.risk_level,
        "generation_route": candidate.generation_route,
        "current_version": serialize_candidate_version(version) if version else None,
        "versions": [serialize_candidate_version(item) for item in all_versions],
        "demo_runs": [
            serialize_demo_run(
                demo,
                version_number=version_numbers.get(demo.candidate_version_id),
            )
            for demo in demos
        ],
        "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
        "updated_at": candidate.updated_at.isoformat() if candidate.updated_at else None,
    }


async def append_candidate_version(
    db: AsyncSession,
    *,
    candidate: FoundryCandidate,
    draft: dict[str, Any],
    actor_kind: str,
    actor_user_id: uuid.UUID | None,
) -> FoundryCandidateVersion:
    """Append one server-hashed candidate version; never replace a prior row."""

    locked = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate.id)
        .with_for_update()
    )
    if locked is None:
        raise FoundryCatalogError("candidate_not_found", "Foundry candidate not found", status_code=404)
    max_version = int(
        await db.scalar(
            select(func.max(FoundryCandidateVersion.version_number)).where(
                FoundryCandidateVersion.candidate_id == candidate.id
            )
        )
        or 0
    )
    bundle = _normalize_candidate_bundle(draft.get("candidate_bundle"))
    if bundle["candidate_version"] != max_version + 1:
        raise FoundryCatalogError(
            "candidate_version_sequence_mismatch",
            "candidate_bundle.candidate_version must be the next immutable version",
            status_code=409,
        )
    if candidate.risk_level and bundle["risk_level"] != candidate.risk_level:
        raise FoundryCatalogError(
            "candidate_risk_binding_mismatch",
            "Candidate bundle risk level differs from triage",
            status_code=409,
        )
    workflow_id = str(bundle["proposed_workflow_id"])
    workflow_spec = dict(bundle["workflow_spec"])
    declared_workflow_id = str(workflow_spec.get("workflow_id") or workflow_id).strip()
    if declared_workflow_id != workflow_id:
        raise FoundryCatalogError(
            "candidate_workflow_binding_mismatch",
            "candidate workflow_id differs from proposed_workflow_id",
            status_code=409,
        )
    workflow_version = str(
        workflow_spec.get("version") or workflow_spec.get("workflow_version") or ""
    ).strip()
    if not workflow_version or len(workflow_version) > 64:
        raise FoundryCatalogError(
            "invalid_workflow_version",
            "candidate workflow_spec.workflow_version is required",
        )
    workflow_spec_hash = sha256_json(workflow_spec)
    bundle_hash = sha256_json(bundle)
    generation = dict(bundle.get("generation") or {})
    ai_model = str(generation.get("model") or "unspecified")
    source_pins = list(bundle.get("source_pins") or [])
    fixture_pins = list(bundle.get("fixture_hashes") or [])
    data_hashes = {
        str(item.get("key")): str(item.get("sha256"))
        for item in source_pins
        if isinstance(item, dict)
        and item.get("key")
        and _SHA256_RE.fullmatch(str(item.get("sha256") or ""))
    }
    code_tree_hash = _require_sha256(
        draft.get("code_tree_hash") or bundle["runner_definition_sha256"],
        "code_tree_hash",
    )
    patch_hash = _require_sha256(
        draft.get("patch_hash")
        or sha256_json({"candidate_bundle_sha256": bundle_hash}),
        "patch_hash",
    )
    sbom_hash = _require_sha256(
        draft.get("sbom_hash") or sha256_json([]), "sbom_hash"
    )
    image_digest = _require_image_digest(
        draft.get("validation_runner_image_digest")
    )
    version_hash = candidate_version_sha256(
        candidate_bundle_sha256=bundle_hash,
        workflow_spec_sha256=workflow_spec_hash,
        code_tree_sha256=code_tree_hash,
        patch_sha256=patch_hash,
        dependency_lock_sha256=str(bundle["dependency_lock_sha256"]),
        sbom_sha256=sbom_hash,
        fixture_hashes=fixture_pins,
        data_hashes=data_hashes,
        validation_runner_image_digest=image_digest,
    )
    row = FoundryCandidateVersion(
        candidate_id=candidate.id,
        candidate_key=str(bundle["candidate_id"]),
        version_number=max_version + 1,
        version_hash=version_hash,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        candidate_bundle=bundle,
        workflow_spec=workflow_spec,
        workflow_spec_hash=workflow_spec_hash,
        code_tree_hash=code_tree_hash,
        patch_hash=patch_hash,
        dependency_lock_hash=str(bundle["dependency_lock_sha256"]),
        sbom_hash=sbom_hash,
        fixture_hashes=fixture_pins,
        data_hashes=data_hashes,
        ai_model=ai_model,
        ai_generation_config=generation,
        validation_runner_image_digest=image_digest,
        created_by_kind=actor_kind,
        created_by_user_id=actor_user_id,
    )
    db.add(row)
    await db.flush()
    locked.current_version_number = row.version_number
    locked.status = "BUILDING"
    await _append_event(
        db,
        candidate_id=locked.id,
        candidate_version_id=row.id,
        event_type="CANDIDATE_VERSION_CREATED",
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        payload={
            "version_number": row.version_number,
            "version_hash": row.version_hash,
            "candidate_bundle_hash": bundle_hash,
            "workflow_id": row.workflow_id,
            "workflow_version": row.workflow_version,
        },
    )
    return row


async def triage_capability_request(
    db: AsyncSession,
    *,
    request_id: uuid.UUID,
    generation_route: str,
    risk_level: str,
    actor_kind: str,
    actor_user_id: uuid.UUID | None,
    draft: dict[str, Any] | None = None,
) -> tuple[CapabilityRequest, FoundryCandidate, FoundryCandidateVersion | None]:
    generation_route = generation_route.upper()
    risk_level = risk_level.upper()
    if generation_route not in GENERATION_ROUTES:
        raise FoundryCatalogError("invalid_generation_route", "Unsupported generation route")
    if risk_level not in RISK_LEVELS:
        raise FoundryCatalogError("invalid_risk_level", "Unsupported Foundry risk level")
    request = await db.scalar(
        select(CapabilityRequest)
        .where(CapabilityRequest.id == request_id)
        .with_for_update()
    )
    if request is None or request.candidate_id is None:
        raise FoundryCatalogError("capability_request_not_found", "Capability request not found", status_code=404)
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == request.candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise FoundryCatalogError("candidate_not_found", "Foundry candidate not found", status_code=404)
    if candidate.status in {
        "APPROVED",
        "REJECTED",
        "PROMOTED",
        "SUSPENDED",
        "SUPERSEDED",
        "REVOKED",
    }:
        raise FoundryCatalogError(
            "candidate_not_triageable",
            "A reviewed or formal candidate cannot be re-triaged",
            status_code=409,
        )
    latest_draft_event = await db.scalar(
        select(FoundryCandidateEvent)
        .where(
            FoundryCandidateEvent.candidate_id == candidate.id,
            FoundryCandidateEvent.event_type.in_(_DRAFT_EVENT_TYPES),
        )
        .order_by(
            FoundryCandidateEvent.created_at.desc(),
            FoundryCandidateEvent.id.desc(),
        )
        .limit(1)
    )
    if (
        latest_draft_event is not None
        and latest_draft_event.event_type == "AI_DRAFT_RESULT_ACCEPTED"
    ):
        raise FoundryCatalogError(
            "candidate_draft_already_accepted",
            "An accepted immutable Draft cannot be replaced by re-triage",
            status_code=409,
        )
    if (
        latest_draft_event is not None
        and latest_draft_event.event_type
        in {
            "AI_DRAFT_QUEUED",
            "AI_DRAFT_DISPATCHED",
            "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN",
        }
        and (
            draft is not None
            or candidate.generation_route != generation_route
            or candidate.risk_level != risk_level
        )
    ):
        raise FoundryCatalogError(
            "candidate_draft_binding_active",
            "An active AI Draft keeps its route, risk, and version binding",
            status_code=409,
        )
    request.status = "TRIAGED"
    candidate.generation_route = generation_route
    candidate.risk_level = risk_level
    candidate.status = "BUILDING"
    version = None
    if draft is not None:
        version = await append_candidate_version(
            db,
            candidate=candidate,
            draft=draft,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
        )
    await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id if version else None,
        event_type="REQUEST_TRIAGED",
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        payload={
            "request_id": str(request.id),
            "generation_route": generation_route,
            "risk_level": risk_level,
        },
    )
    await db.commit()
    await db.refresh(request)
    await db.refresh(candidate)
    return request, candidate, version


_DRAFT_EVENT_TYPES = frozenset(
    {
        "AI_DRAFT_QUEUED",
        "AI_DRAFT_DISPATCHED",
        "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN",
        "AI_DRAFT_DISPATCH_FAILED",
        "AI_DRAFT_LEASE_EXPIRED",
        "AI_DRAFT_RESULT_ACCEPTED",
        "AI_DRAFT_RESULT_FAILED",
    }
)
_DRAFT_FAILURE_RE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
_SAFE_ARTIFACT_PATH_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SAFE_PROVIDER_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,255}$")
_DRAFT_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
)
_DRAFT_PATCH_PATH_RE = re.compile(
    r"^backend/app/services/foundry_generated/[a-z][a-z0-9_]{2,96}\.py$"
)


def _draft_run_id(event: FoundryCandidateEvent) -> str:
    if event.event_type == "AI_DRAFT_QUEUED":
        return str(event.id)
    return str((event.event_payload or {}).get("draft_run_id") or "")


def serialize_ai_draft_run_event(event: FoundryCandidateEvent) -> dict[str, Any]:
    """Return the stable persisted state for one exact Draft ledger event."""

    payload = dict(event.event_payload or {})
    state = {
        "AI_DRAFT_QUEUED": "QUEUED",
        "AI_DRAFT_DISPATCHED": "DISPATCHED",
        "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN": "OUTCOME_UNKNOWN",
        "AI_DRAFT_DISPATCH_FAILED": "DISPATCH_FAILED",
        "AI_DRAFT_LEASE_EXPIRED": "TIMED_OUT",
        "AI_DRAFT_RESULT_ACCEPTED": "COMPLETED",
        "AI_DRAFT_RESULT_FAILED": "RESULT_FAILED",
    }.get(event.event_type, "UNKNOWN")
    retry_after = None
    if event.event_type == "AI_DRAFT_QUEUED":
        retry_after = payload.get("lease_expires_at")
    elif event.event_type in {
        "AI_DRAFT_DISPATCHED",
        "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN",
    }:
        active_since = event.created_at
        if active_since.tzinfo is None:
            active_since = active_since.replace(tzinfo=timezone.utc)
        retry_after = (active_since + AI_DRAFT_WORKFLOW_LEASE).isoformat()
    return {
        "status": state,
        "retryable": payload.get("retryable") is True,
        "failure_class": payload.get("failure_class"),
        "delivery_uncertain": payload.get("delivery_uncertain") is True,
        "event_type": event.event_type,
        "retry_after": retry_after,
    }


async def get_ai_draft_run_state(
    db: AsyncSession, *, draft_run_id: uuid.UUID
) -> dict[str, Any]:
    """Read the latest immutable state bound to one Draft reservation."""

    queued = await db.get(FoundryCandidateEvent, draft_run_id)
    if queued is None or queued.event_type != "AI_DRAFT_QUEUED":
        raise FoundryCatalogError(
            "draft_run_not_found", "AI Draft run not found", status_code=404
        )
    lifecycle = list(
        (
            await db.execute(
                select(FoundryCandidateEvent)
                .where(
                    FoundryCandidateEvent.candidate_id == queued.candidate_id,
                    FoundryCandidateEvent.event_type.in_(_DRAFT_EVENT_TYPES),
                )
                .order_by(
                    FoundryCandidateEvent.created_at.asc(),
                    FoundryCandidateEvent.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    bound = [event for event in lifecycle if _draft_run_id(event) == str(draft_run_id)]
    return serialize_ai_draft_run_event(bound[-1] if bound else queued)


async def queue_ai_draft(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    actor_kind: str,
    actor_user_id: uuid.UUID | None,
    now: datetime | None = None,
) -> tuple[FoundryCandidateEvent, bool]:
    """Append a durable, privacy-minimized Draft request event.

    The event id is the ``draft_run_id``.  A separate mutable job table is not
    needed: every dispatch and result is another hash-chained event referring
    to this immutable id.
    """

    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise FoundryCatalogError(
            "candidate_not_found", "Foundry candidate not found", status_code=404
        )
    if candidate.status in {
        "APPROVED",
        "REJECTED",
        "PROMOTED",
        "SUSPENDED",
        "SUPERSEDED",
        "REVOKED",
    }:
        raise FoundryCatalogError(
            "candidate_not_draftable",
            "A reviewed or formal candidate cannot be drafted in place",
            status_code=409,
        )
    if (
        candidate.generation_route not in GENERATION_ROUTES
        or candidate.risk_level not in RISK_LEVELS
    ):
        raise FoundryCatalogError(
            "candidate_triage_incomplete",
            "Generation route and risk level must be fixed before AI drafting",
            status_code=409,
        )

    # Rebuild the provider-visible descriptor from the server-owned Candidate
    # row.  This strips request prose such as ``next_action`` and retains only
    # the small, explicit _FINGERPRINT_KEYS allowlist plus research_domain.
    descriptor = gap_descriptor(dict(candidate.gap_descriptor or {}))
    if (
        descriptor.get("gap_code") != candidate.gap_code
        or sha256_json(descriptor) != candidate.gap_fingerprint
    ):
        raise FoundryCatalogError(
            "candidate_gap_binding_invalid",
            "Candidate gap descriptor no longer matches its immutable fingerprint",
            status_code=409,
        )

    recent = list(
        (
            await db.execute(
                select(FoundryCandidateEvent)
                .where(
                    FoundryCandidateEvent.candidate_id == candidate.id,
                    FoundryCandidateEvent.event_type.in_(_DRAFT_EVENT_TYPES),
                )
                .order_by(
                    FoundryCandidateEvent.created_at.desc(),
                    FoundryCandidateEvent.id.desc(),
                )
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    attempt_count = int(
        await db.scalar(
            select(func.count())
            .select_from(FoundryCandidateEvent)
            .where(
                FoundryCandidateEvent.candidate_id == candidate.id,
                FoundryCandidateEvent.event_type == "AI_DRAFT_QUEUED",
            )
        )
        or 0
    )
    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    if recent:
        latest = recent[0]
        if latest.event_type in {
            "AI_DRAFT_QUEUED",
            "AI_DRAFT_DISPATCHED",
            "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN",
        }:
            run_id = _draft_run_id(latest)
            queued = next(
                (
                    event
                    for event in recent
                    if event.event_type == "AI_DRAFT_QUEUED"
                    and str(event.id) == run_id
                ),
                None,
            )
            if queued is None:
                raise FoundryCatalogError(
                    "draft_run_binding_missing",
                    "The active AI Draft has no immutable queued reservation",
                    status_code=409,
                )
            active_since = latest.created_at
            if active_since.tzinfo is None:
                active_since = active_since.replace(tzinfo=timezone.utc)
            lease = (
                AI_DRAFT_DISPATCH_LEASE
                if latest.event_type == "AI_DRAFT_QUEUED"
                else AI_DRAFT_WORKFLOW_LEASE
            )
            retry_after = active_since + lease
            if current_time < retry_after:
                return queued, False
            attempt_number = int(
                (queued.event_payload or {}).get("attempt_number")
                or attempt_count
                or 1
            )
            retryable = attempt_number < AI_DRAFT_MAX_ATTEMPTS
            await _append_event(
                db,
                candidate_id=candidate.id,
                event_type="AI_DRAFT_LEASE_EXPIRED",
                actor_kind="CONTROL_PLANE",
                actor_user_id=None,
                payload={
                    "draft_run_id": str(queued.id),
                    "attempt_number": attempt_number,
                    "max_attempts": AI_DRAFT_MAX_ATTEMPTS,
                    "expired_state": latest.event_type,
                    "timeout_seconds": int(lease.total_seconds()),
                    "retryable": retryable,
                },
            )
            if not retryable:
                await db.commit()
                raise FoundryCatalogError(
                    "draft_attempts_exhausted",
                    "AI Draft reached the bounded attempt limit",
                    status_code=409,
                )
        elif latest.event_type == "AI_DRAFT_RESULT_ACCEPTED":
            raise FoundryCatalogError(
                "candidate_draft_already_accepted",
                "The current candidate already has an accepted immutable draft version",
                status_code=409,
            )
        elif (
            latest.event_type == "AI_DRAFT_DISPATCH_FAILED"
            and (latest.event_payload or {}).get("retryable") is not True
        ):
            if attempt_count >= AI_DRAFT_MAX_ATTEMPTS:
                raise FoundryCatalogError(
                    "draft_attempts_exhausted",
                    "AI Draft reached the bounded attempt limit",
                    status_code=409,
                )
            raise FoundryCatalogError(
                "draft_dispatch_not_retryable",
                "The latest Draft dispatch failed permanently and cannot be retried in place",
                status_code=409,
            )
    if attempt_count >= AI_DRAFT_MAX_ATTEMPTS:
        await db.commit()
        raise FoundryCatalogError(
            "draft_attempts_exhausted",
            "AI Draft reached the bounded attempt limit",
            status_code=409,
        )

    attempt_number = attempt_count + 1
    event = await _append_event(
        db,
        candidate_id=candidate.id,
        event_type="AI_DRAFT_QUEUED",
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        payload={
            # Deliberately omit claim, source, prompt, user, and workspace data.
            "candidate_id": str(candidate.id),
            "gap_fingerprint": candidate.gap_fingerprint,
            "gap_code": candidate.gap_code,
            "gap_descriptor": descriptor,
            "generation_route": candidate.generation_route,
            "risk_level": candidate.risk_level,
            "attempt_number": attempt_number,
            "max_attempts": AI_DRAFT_MAX_ATTEMPTS,
            "dispatch_lease_seconds": int(
                AI_DRAFT_DISPATCH_LEASE.total_seconds()
            ),
            "workflow_lease_seconds": int(
                AI_DRAFT_WORKFLOW_LEASE.total_seconds()
            ),
            "lease_expires_at": (
                current_time + AI_DRAFT_DISPATCH_LEASE
            ).isoformat(),
        },
    )
    candidate.status = "BUILDING"
    await db.commit()
    await db.refresh(event)
    return event, True


async def record_ai_draft_dispatch(
    db: AsyncSession,
    *,
    draft_run_id: uuid.UUID,
    dispatched: bool,
    failure_class: str | None = None,
    retryable: bool = False,
    delivery_uncertain: bool = False,
) -> FoundryCandidateEvent:
    """Append the sanitized GitHub dispatch outcome for one queued Draft."""

    if dispatched and delivery_uncertain:
        raise FoundryCatalogError(
            "draft_dispatch_outcome_invalid",
            "A successful Draft dispatch cannot also have an uncertain outcome",
            status_code=409,
        )
    queued = await db.get(FoundryCandidateEvent, draft_run_id)
    if queued is None or queued.event_type != "AI_DRAFT_QUEUED":
        raise FoundryCatalogError(
            "draft_run_not_found", "AI Draft run not found", status_code=404
        )
    raw_attempt_number = (queued.event_payload or {}).get("attempt_number")
    attempt_number = int(
        raw_attempt_number
        or await db.scalar(
            select(func.count())
            .select_from(FoundryCandidateEvent)
            .where(
                FoundryCandidateEvent.candidate_id == queued.candidate_id,
                FoundryCandidateEvent.event_type == "AI_DRAFT_QUEUED",
            )
        )
        or 1
    )
    effective_retryable = (
        bool(retryable) and attempt_number < AI_DRAFT_MAX_ATTEMPTS
    )
    # Serialize the dispatch receipt with the callback path.  A GitHub run can
    # finish while the original HTTP request is recovering from an uncertain
    # response; without this shared lock, a late receipt could overwrite the
    # callback's VALIDATING state with BUILDING.
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == queued.candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise FoundryCatalogError(
            "candidate_not_found", "Foundry candidate not found", status_code=404
        )
    lifecycle = list(
        (
            await db.execute(
                select(FoundryCandidateEvent)
                .where(
                    FoundryCandidateEvent.candidate_id == queued.candidate_id,
                    FoundryCandidateEvent.event_type.in_(
                        {
                            "AI_DRAFT_DISPATCHED",
                            "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN",
                            "AI_DRAFT_DISPATCH_FAILED",
                            "AI_DRAFT_LEASE_EXPIRED",
                            "AI_DRAFT_RESULT_ACCEPTED",
                            "AI_DRAFT_RESULT_FAILED",
                        }
                    ),
                )
                .order_by(FoundryCandidateEvent.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    completed = next(
        (
            event
            for event in lifecycle
            if _draft_run_id(event) == str(draft_run_id)
            and event.event_type
            in {
                "AI_DRAFT_LEASE_EXPIRED",
                "AI_DRAFT_RESULT_ACCEPTED",
                "AI_DRAFT_RESULT_FAILED",
            }
        ),
        None,
    )
    if completed is not None:
        # The authenticated callback is stronger delivery evidence than the
        # late control-plane receipt.  Keep the terminal event and aggregate
        # state untouched.
        await db.commit()
        return completed
    existing = next(
        (event for event in lifecycle if _draft_run_id(event) == str(draft_run_id)),
        None,
    )
    expected_type = (
        "AI_DRAFT_DISPATCHED"
        if dispatched
        else (
            "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN"
            if delivery_uncertain
            else "AI_DRAFT_DISPATCH_FAILED"
        )
    )
    sanitized_failure = None
    if not dispatched:
        sanitized_failure = str(failure_class or "draft_dispatch_failed")
        if not _DRAFT_FAILURE_RE.fullmatch(sanitized_failure):
            sanitized_failure = "draft_dispatch_failed"
    if existing is not None:
        if (
            existing.event_type != expected_type
            or (existing.event_payload or {}).get("failure_class")
            != sanitized_failure
            or bool((existing.event_payload or {}).get("delivery_uncertain"))
            != bool(delivery_uncertain)
            or bool((existing.event_payload or {}).get("retryable"))
            != (effective_retryable if not dispatched else False)
        ):
            raise FoundryCatalogError(
                "draft_dispatch_outcome_conflict",
                "The Draft dispatch outcome is already recorded differently",
                status_code=409,
            )
        await db.commit()
        return existing

    event = await _append_event(
        db,
        candidate_id=queued.candidate_id,
        event_type=expected_type,
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload={
            "draft_run_id": str(draft_run_id),
            "failure_class": sanitized_failure,
            "retryable": effective_retryable if not dispatched else False,
            "delivery_uncertain": bool(delivery_uncertain),
            "attempt_number": attempt_number,
            "max_attempts": AI_DRAFT_MAX_ATTEMPTS,
        },
    )
    candidate.status = "BUILDING"
    await db.commit()
    await db.refresh(event)
    return event


def _validate_ai_draft_report(
    report_value: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if not isinstance(report_value, dict):
        raise FoundryCatalogError(
            "invalid_draft_result", "AI Draft result must be an object"
        )
    report = json.loads(canonical_json(report_value))
    expected_keys = {
        "schema_version",
        "draft_run_id",
        "candidate_id",
        "gap_fingerprint",
        "gap_code",
        "gap_descriptor",
        "generation_route",
        "risk_level",
        "status",
        "candidate_version",
        "provider_receipt",
        "artifact_manifest",
        "artifact_receipt",
        "source_receipt",
        "failure_class",
        "draft_result_sha256",
    }
    if set(report) != expected_keys or report.get("schema_version") != 1:
        raise FoundryCatalogError(
            "invalid_draft_result", "AI Draft result shape is not registered"
        )
    declared_hash = _require_sha256(
        report.pop("draft_result_sha256"), "draft_result_sha256"
    )
    if sha256_json(report) != declared_hash:
        raise FoundryCatalogError(
            "draft_result_hash_mismatch", "AI Draft result hash does not match"
        )
    report["draft_result_sha256"] = declared_hash

    provider = report.get("provider_receipt")
    provider_keys = {
        "contract_version",
        "provider",
        "model",
        "request_id_sha256",
        "prompt_or_user_data_stored",
        "generated_code_executed",
        "tests_executed",
    }
    if (
        not isinstance(provider, dict)
        or set(provider) != provider_keys
        or provider.get("contract_version") != 1
        or provider.get("prompt_or_user_data_stored") is not False
        or provider.get("generated_code_executed") is not False
        or provider.get("tests_executed") is not False
        or not _SAFE_PROVIDER_RE.fullmatch(str(provider.get("provider") or ""))
        or len(str(provider.get("provider") or "")) > 128
        or not _SAFE_PROVIDER_RE.fullmatch(str(provider.get("model") or ""))
    ):
        raise FoundryCatalogError(
            "invalid_draft_provider_receipt",
            "AI Draft provider receipt violates the non-execution contract",
        )
    request_hash = provider.get("request_id_sha256")
    if request_hash is not None:
        _require_sha256(request_hash, "request_id_sha256")

    artifacts = report.get("artifact_manifest")
    if not isinstance(artifacts, list) or len(artifacts) > 16:
        raise FoundryCatalogError(
            "invalid_draft_artifact_manifest",
            "AI Draft artifact manifest must be a bounded list",
        )
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"path", "kind", "sha256", "bytes"}
            or not _SAFE_ARTIFACT_PATH_RE.fullmatch(str(artifact.get("path") or ""))
            or artifact.get("kind")
            not in {"CANDIDATE_BUNDLE", "PATCH", "SBOM", "PROVIDER_RECEIPT"}
            or not isinstance(artifact.get("bytes"), int)
            or isinstance(artifact.get("bytes"), bool)
            or not 0 <= artifact["bytes"] <= 2 * 1024 * 1024
        ):
            raise FoundryCatalogError(
                "invalid_draft_artifact_manifest",
                "AI Draft artifact entry is invalid or exceeds its size bound",
            )
        _require_sha256(artifact.get("sha256"), "artifact.sha256")
    return report, declared_hash


async def record_ai_draft_result(
    db: AsyncSession,
    *,
    draft_run_id: uuid.UUID,
    draft_result: dict[str, Any],
    expected_repository: str | None = None,
    expected_base_commit: str | None = None,
    authenticated_callback_proof: bool = False,
) -> tuple[FoundryCandidateVersion | None, FoundryCandidateEvent]:
    """Append one host-ingested Draft result and, on success, one AI version."""

    report, report_hash = _validate_ai_draft_report(draft_result)
    queued = await db.get(FoundryCandidateEvent, draft_run_id)
    if queued is None or queued.event_type != "AI_DRAFT_QUEUED":
        raise FoundryCatalogError(
            "draft_run_not_found", "AI Draft run not found", status_code=404
        )
    binding = dict(queued.event_payload or {})
    expected_binding = {
        "draft_run_id": str(draft_run_id),
        "candidate_id": str(queued.candidate_id),
        "gap_fingerprint": str(binding.get("gap_fingerprint") or ""),
        "gap_code": str(binding.get("gap_code") or ""),
        "gap_descriptor": binding.get("gap_descriptor"),
        "generation_route": str(binding.get("generation_route") or ""),
        "risk_level": str(binding.get("risk_level") or ""),
    }
    actual_binding = {
        **{
            key: str(report.get(key) or "")
            for key in expected_binding
            if key != "gap_descriptor"
        },
        "gap_descriptor": report.get("gap_descriptor"),
    }
    if actual_binding != expected_binding:
        raise FoundryCatalogError(
            "draft_result_binding_mismatch",
            "AI Draft result does not match its immutable dispatch binding",
            status_code=409,
        )
    descriptor = gap_descriptor(dict(report.get("gap_descriptor") or {}))
    if (
        descriptor != report.get("gap_descriptor")
        or sha256_json(descriptor) != report.get("gap_fingerprint")
        or descriptor.get("gap_code") != report.get("gap_code")
    ):
        raise FoundryCatalogError(
            "draft_gap_descriptor_invalid",
            "AI Draft descriptor is not the canonical server-approved gap binding",
            status_code=409,
        )

    # Serialize callback/replay handling on the candidate before checking the
    # result ledger.  Without this lock, two simultaneous callbacks could both
    # observe "not completed" and append two versions.
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == queued.candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise FoundryCatalogError(
            "candidate_not_found", "Foundry candidate not found", status_code=404
        )

    lifecycle_events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent)
                .where(
                    FoundryCandidateEvent.candidate_id == queued.candidate_id,
                    FoundryCandidateEvent.event_type.in_(
                        {
                            "AI_DRAFT_DISPATCHED",
                            "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN",
                            "AI_DRAFT_DISPATCH_FAILED",
                            "AI_DRAFT_LEASE_EXPIRED",
                            "AI_DRAFT_RESULT_ACCEPTED",
                            "AI_DRAFT_RESULT_FAILED",
                        }
                    ),
                )
                .order_by(FoundryCandidateEvent.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    bound_events = [
        event
        for event in lifecycle_events
        if _draft_run_id(event) == str(draft_run_id)
    ]
    completed = next(
        (
            event
            for event in bound_events
            if event.event_type
            in {"AI_DRAFT_RESULT_ACCEPTED", "AI_DRAFT_RESULT_FAILED"}
        ),
        None,
    )
    if completed is not None:
        if (completed.event_payload or {}).get("draft_result_sha256") != report_hash:
            raise FoundryCatalogError(
                "draft_result_conflict",
                "The AI Draft run already has a different immutable result",
                status_code=409,
            )
        version_id = (completed.event_payload or {}).get("candidate_version_id")
        version = (
            await db.get(FoundryCandidateVersion, uuid.UUID(str(version_id)))
            if version_id
            else None
        )
        return version, completed
    if any(event.event_type == "AI_DRAFT_DISPATCH_FAILED" for event in bound_events):
        raise FoundryCatalogError(
            "draft_result_after_failed_dispatch",
            "A failed Draft dispatch must be retried with a new run id",
            status_code=409,
        )
    if any(event.event_type == "AI_DRAFT_LEASE_EXPIRED" for event in bound_events):
        raise FoundryCatalogError(
            "draft_result_after_lease_expired",
            "The AI Draft lease expired and a late result cannot create a version",
            status_code=409,
        )
    # The internal route may supply an independently authenticated callback as
    # delivery proof.  This covers a control-plane crash after GitHub accepted
    # workflow_dispatch but before the outcome event was appended.  All other
    # service callers remain fail closed by default.
    if not any(
        event.event_type
        in {"AI_DRAFT_DISPATCHED", "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN"}
        for event in bound_events
    ) and not authenticated_callback_proof:
        raise FoundryCatalogError(
            "draft_result_not_dispatched",
            "AI Draft result cannot be ingested before a dispatch attempt",
            status_code=409,
        )

    status = str(report.get("status") or "").upper()
    if status not in {"SUCCEEDED", "FAILED"}:
        raise FoundryCatalogError(
            "invalid_draft_result", "AI Draft status must be SUCCEEDED or FAILED"
        )
    failure_class = report.get("failure_class")
    if failure_class is not None and not _DRAFT_FAILURE_RE.fullmatch(
        str(failure_class)
    ):
        raise FoundryCatalogError(
            "invalid_draft_failure_class", "AI Draft failure class is invalid"
        )

    if status == "FAILED":
        if (
            report.get("candidate_version") is not None
            or report.get("artifact_receipt") is not None
            or report.get("source_receipt") is not None
            or failure_class is None
        ):
            raise FoundryCatalogError(
                "invalid_draft_result",
                "A failed AI Draft must contain only a classified failure",
            )
        event = await _append_event(
            db,
            candidate_id=candidate.id,
            event_type="AI_DRAFT_RESULT_FAILED",
            actor_kind="AI_DRAFT_JOB",
            actor_user_id=None,
            payload={
                "draft_run_id": str(draft_run_id),
                "draft_result_sha256": report_hash,
                "failure_class": str(failure_class),
                "retryable": True,
            },
        )
        candidate.status = "BUILDING"
        await db.commit()
        await db.refresh(event)
        return None, event

    if failure_class is not None or not isinstance(report.get("candidate_version"), dict):
        raise FoundryCatalogError(
            "invalid_draft_result",
            "A successful AI Draft requires one candidate version and no failure",
        )
    artifact_receipt = report.get("artifact_receipt")
    source_receipt = report.get("source_receipt")
    if (
        not isinstance(artifact_receipt, dict)
        or set(artifact_receipt)
        != {
            "repository",
            "workflow_run_id",
            "artifact_id",
            "artifact_name",
            "artifact_sha256",
        }
        or not _DRAFT_REPOSITORY_RE.fullmatch(
            str(artifact_receipt.get("repository") or "")
        )
        or not str(artifact_receipt.get("workflow_run_id") or "").isdigit()
        or not str(artifact_receipt.get("artifact_id") or "").isdigit()
        or artifact_receipt.get("artifact_name")
        != f"foundry-draft-{draft_run_id}"
    ):
        raise FoundryCatalogError(
            "draft_artifact_receipt_invalid",
            "Successful AI Drafts require an exact immutable artifact receipt",
        )
    artifact_sha256 = _require_sha256(
        artifact_receipt.get("artifact_sha256"), "artifact_sha256"
    )
    if expected_repository and artifact_receipt["repository"] != expected_repository:
        raise FoundryCatalogError(
            "draft_artifact_repository_mismatch",
            "AI Draft artifact came from an unexpected repository",
            status_code=409,
        )
    source_keys = {
        "hash_algorithm",
        "base_commit",
        "base_source_tree_sha256",
        "post_patch_source_tree_sha256",
        "patch_sha256",
        "patch_applied",
        "changed_paths",
        "dependency_lock_sha256",
        "runner_definition_sha256",
    }
    if (
        not isinstance(source_receipt, dict)
        or set(source_receipt) != source_keys
        or source_receipt.get("hash_algorithm")
        != "standard_astro_tracked_source_manifest_v1"
        or not _GIT_SHA_RE.fullmatch(str(source_receipt.get("base_commit") or ""))
        or not isinstance(source_receipt.get("patch_applied"), bool)
        or not isinstance(source_receipt.get("changed_paths"), list)
        or len(source_receipt["changed_paths"]) > 64
        or any(
            not _DRAFT_PATCH_PATH_RE.fullmatch(str(path))
            for path in source_receipt["changed_paths"]
        )
    ):
        raise FoundryCatalogError(
            "draft_source_receipt_invalid",
            "AI Draft source receipt violates the canonical patch policy",
        )
    for field in (
        "base_source_tree_sha256",
        "post_patch_source_tree_sha256",
        "patch_sha256",
        "dependency_lock_sha256",
        "runner_definition_sha256",
    ):
        _require_sha256(source_receipt.get(field), field)
    if expected_base_commit and source_receipt["base_commit"] != expected_base_commit:
        raise FoundryCatalogError(
            "draft_source_base_commit_mismatch",
            "AI Draft source was not based on the pinned dispatch commit",
            status_code=409,
        )
    patch_artifact = next(
        (
            artifact
            for artifact in report["artifact_manifest"]
            if artifact.get("kind") == "PATCH"
        ),
        None,
    )
    if (
        patch_artifact is None
        or patch_artifact.get("sha256") != source_receipt["patch_sha256"]
        or bool(patch_artifact.get("bytes"))
        != source_receipt["patch_applied"]
        or bool(source_receipt["changed_paths"])
        != source_receipt["patch_applied"]
    ):
        raise FoundryCatalogError(
            "draft_patch_receipt_mismatch",
            "Patch bytes, changed paths, and source receipt do not agree",
        )
    draft = json.loads(canonical_json(report["candidate_version"]))
    bundle = draft.get("candidate_bundle")
    if not isinstance(bundle, dict):
        raise FoundryCatalogError(
            "invalid_candidate_bundle", "AI Draft did not return a candidate bundle"
        )
    if source_receipt["patch_applied"]:
        expected_generated_path = (
            "backend/app/services/foundry_generated/"
            f"{str(bundle.get('candidate_id') or '')}.py"
        )
        if (
            bundle.get("entrypoint_id")
            != "candidate_generated_python_demo_v1"
            or source_receipt["changed_paths"] != [expected_generated_path]
        ):
            raise FoundryCatalogError(
                "draft_generated_module_binding_mismatch",
                "A generated-code patch must bind exactly one candidate module and the fixed Validation entrypoint",
            )
    raw_version = bundle.get("candidate_version")
    if raw_version is not None and raw_version != 0:
        raise FoundryCatalogError(
            "draft_candidate_version_not_server_assigned",
            "AI must leave candidate_version for the control plane to assign",
            status_code=409,
        )
    max_version = int(
        await db.scalar(
            select(func.max(FoundryCandidateVersion.version_number)).where(
                FoundryCandidateVersion.candidate_id == candidate.id
            )
        )
        or 0
    )
    bundle["candidate_version"] = max_version + 1
    generation = bundle.get("generation")
    if (
        not isinstance(generation, dict)
        or generation.get("prompt_or_claim_stored") is not False
        or generation.get("generated_code_executed_by_draft_job") is not False
        or any(
            key in generation
            for key in {
                "prompt",
                "claim",
                "claim_text",
                "source",
                "source_text",
                "user_id",
                "workspace_id",
            }
        )
        or generation.get("source_hash_algorithm")
        != source_receipt["hash_algorithm"]
        or generation.get("source_base_commit") != source_receipt["base_commit"]
        or generation.get("source_base_tree_sha256")
        != source_receipt["base_source_tree_sha256"]
        or generation.get("source_tree_sha256")
        != source_receipt["post_patch_source_tree_sha256"]
        or generation.get("source_materialization_required")
        is not source_receipt["patch_applied"]
    ):
        raise FoundryCatalogError(
            "draft_generation_metadata_unsafe",
            "AI Draft generation metadata must not retain user research content",
        )
    draft["candidate_bundle"] = bundle
    if (
        draft.get("code_tree_hash")
        != source_receipt["post_patch_source_tree_sha256"]
        or draft.get("patch_hash") != source_receipt["patch_sha256"]
        or bundle.get("dependency_lock_sha256")
        != source_receipt["dependency_lock_sha256"]
        or bundle.get("runner_definition_sha256")
        != source_receipt["runner_definition_sha256"]
    ):
        raise FoundryCatalogError(
            "draft_source_version_binding_mismatch",
            "Candidate version hashes do not match the trusted source receipt",
            status_code=409,
        )
    version = await append_candidate_version(
        db,
        candidate=candidate,
        draft=draft,
        actor_kind="AI_DRAFT_JOB",
        actor_user_id=None,
    )
    provider = dict(report["provider_receipt"])
    event = await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="AI_DRAFT_RESULT_ACCEPTED",
        actor_kind="AI_DRAFT_JOB",
        actor_user_id=None,
        payload={
            "draft_run_id": str(draft_run_id),
            "draft_result_sha256": report_hash,
            "candidate_version_id": str(version.id),
            "candidate_version_hash": version.version_hash,
            "provider": provider["provider"],
            "model": provider["model"],
            "artifact_manifest": list(report["artifact_manifest"]),
            "artifact_receipt": {
                **dict(artifact_receipt),
                "artifact_sha256": artifact_sha256,
            },
            "source_receipt": dict(source_receipt),
        },
    )
    await db.commit()
    await db.refresh(version)
    await db.refresh(event)
    return version, event


async def reconcile_expired_ai_draft_runs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """Close stale Draft reservations so a bounded retry can be requested."""

    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    dispatch_cutoff = current_time - AI_DRAFT_DISPATCH_LEASE
    workflow_cutoff = current_time - AI_DRAFT_WORKFLOW_LEASE
    latest_draft = (
        select(
            FoundryCandidateEvent.candidate_id.label("candidate_id"),
            func.max(FoundryCandidateEvent.created_at).label("latest_created_at"),
        )
        .where(FoundryCandidateEvent.event_type.in_(_DRAFT_EVENT_TYPES))
        .group_by(FoundryCandidateEvent.candidate_id)
        .subquery()
    )
    candidate_ids = list(
        (
            await db.execute(
                select(FoundryCandidateEvent.candidate_id)
                .join(
                    latest_draft,
                    and_(
                        latest_draft.c.candidate_id
                        == FoundryCandidateEvent.candidate_id,
                        latest_draft.c.latest_created_at
                        == FoundryCandidateEvent.created_at,
                    ),
                )
                .where(
                    or_(
                        and_(
                            FoundryCandidateEvent.event_type
                            == "AI_DRAFT_QUEUED",
                            FoundryCandidateEvent.created_at <= dispatch_cutoff,
                        ),
                        and_(
                            FoundryCandidateEvent.event_type.in_(
                                {
                                    "AI_DRAFT_DISPATCHED",
                                    "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN",
                                }
                            ),
                            FoundryCandidateEvent.created_at <= workflow_cutoff,
                        ),
                    )
                )
                .distinct()
                .order_by(FoundryCandidateEvent.candidate_id)
                .limit(max(1, min(int(limit), 1000)))
            )
        )
        .scalars()
        .all()
    )
    reconciled = 0
    for candidate_id in candidate_ids:
        candidate = await db.scalar(
            select(FoundryCandidate)
            .where(FoundryCandidate.id == candidate_id)
            .with_for_update()
        )
        if candidate is None:
            await db.rollback()
            continue
        lifecycle = list(
            (
                await db.execute(
                    select(FoundryCandidateEvent)
                    .where(
                        FoundryCandidateEvent.candidate_id == candidate.id,
                        FoundryCandidateEvent.event_type.in_(_DRAFT_EVENT_TYPES),
                    )
                    .order_by(
                        FoundryCandidateEvent.created_at.desc(),
                        FoundryCandidateEvent.id.desc(),
                    )
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        if not lifecycle or lifecycle[0].event_type not in {
            "AI_DRAFT_QUEUED",
            "AI_DRAFT_DISPATCHED",
            "AI_DRAFT_DISPATCH_OUTCOME_UNKNOWN",
        }:
            await db.rollback()
            continue
        active = lifecycle[0]
        run_id = _draft_run_id(active)
        queued = next(
            (
                event
                for event in lifecycle
                if event.event_type == "AI_DRAFT_QUEUED"
                and str(event.id) == run_id
            ),
            None,
        )
        if queued is None:
            await db.rollback()
            continue
        active_since = active.created_at
        if active_since.tzinfo is None:
            active_since = active_since.replace(tzinfo=timezone.utc)
        lease = (
            AI_DRAFT_DISPATCH_LEASE
            if active.event_type == "AI_DRAFT_QUEUED"
            else AI_DRAFT_WORKFLOW_LEASE
        )
        if current_time < active_since + lease:
            await db.rollback()
            continue
        attempt_number = int(
            (queued.event_payload or {}).get("attempt_number") or 1
        )
        await _append_event(
            db,
            candidate_id=candidate.id,
            event_type="AI_DRAFT_LEASE_EXPIRED",
            actor_kind="CONTROL_PLANE",
            actor_user_id=None,
            payload={
                "draft_run_id": str(queued.id),
                "attempt_number": attempt_number,
                "max_attempts": AI_DRAFT_MAX_ATTEMPTS,
                "expired_state": active.event_type,
                "timeout_seconds": int(lease.total_seconds()),
                "retryable": attempt_number < AI_DRAFT_MAX_ATTEMPTS,
            },
        )
        await db.commit()
        reconciled += 1
    return reconciled


async def ai_draft_validation_binding(
    db: AsyncSession,
    *,
    version: FoundryCandidateVersion,
) -> dict[str, Any] | None:
    """Return the immutable Draft artifact binding for one AI-created version."""

    if version.created_by_kind != "AI_DRAFT_JOB":
        return None
    events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent)
                .where(
                    FoundryCandidateEvent.candidate_version_id == version.id,
                    FoundryCandidateEvent.event_type == "AI_DRAFT_RESULT_ACCEPTED",
                )
                .order_by(FoundryCandidateEvent.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if len(events) != 1:
        raise FoundryCatalogError(
            "ai_draft_artifact_binding_missing",
            "AI-created versions require exactly one immutable Draft artifact binding",
            status_code=409,
        )
    payload = dict(events[0].event_payload or {})
    artifact = payload.get("artifact_receipt")
    source = payload.get("source_receipt")
    artifact_manifest = payload.get("artifact_manifest")
    if (
        not isinstance(artifact, dict)
        or not isinstance(source, dict)
        or not isinstance(artifact_manifest, list)
    ):
        raise FoundryCatalogError(
            "ai_draft_artifact_binding_missing",
            "AI Draft artifact or source receipt is missing",
            status_code=409,
        )
    artifact_hashes = {
        str(item.get("kind")): str(item.get("sha256"))
        for item in artifact_manifest
        if isinstance(item, dict)
    }
    if not all(
        _SHA256_RE.fullmatch(artifact_hashes.get(kind, ""))
        for kind in ("CANDIDATE_BUNDLE", "PATCH", "SBOM")
    ):
        raise FoundryCatalogError(
            "ai_draft_artifact_binding_missing",
            "AI Draft artifact file hashes are incomplete",
            status_code=409,
        )
    return {
        "candidate_id": str(version.candidate_id),
        "candidate_version_id": str(version.id),
        "candidate_version_number": str(version.version_number),
        "candidate_version_hash": version.version_hash,
        "candidate_bundle_hash": sha256_json(version.candidate_bundle),
        "candidate_artifact_hash": artifact_hashes["CANDIDATE_BUNDLE"],
        "draft_run_id": str(payload.get("draft_run_id") or ""),
        "artifact_id": str(artifact.get("artifact_id") or ""),
        "artifact_workflow_run_id": str(artifact.get("workflow_run_id") or ""),
        "artifact_name": str(artifact.get("artifact_name") or ""),
        "artifact_sha256": str(artifact.get("artifact_sha256") or ""),
        "artifact_repository": str(artifact.get("repository") or ""),
        "base_commit": str(source.get("base_commit") or ""),
        "base_source_tree_sha256": str(
            source.get("base_source_tree_sha256") or ""
        ),
        "post_patch_source_tree_sha256": str(
            source.get("post_patch_source_tree_sha256") or ""
        ),
        "patch_sha256": version.patch_hash,
        "sbom_sha256": version.sbom_hash,
        "validation_runner_image_digest": version.validation_runner_image_digest,
    }


def candidate_validation_binding(
    version: FoundryCandidateVersion,
) -> dict[str, str]:
    """Bind every durable Demo run to one exact immutable DB version."""

    return {
        "candidate_id": str(version.candidate_id),
        "candidate_version_id": str(version.id),
        "candidate_key": version.candidate_key,
        "candidate_version_number": str(version.version_number),
        "candidate_version_hash": version.version_hash,
        "candidate_bundle_hash": sha256_json(version.candidate_bundle),
        "validation_runner_image_digest": version.validation_runner_image_digest,
    }


async def merge_capability_request(
    db: AsyncSession,
    *,
    request_id: uuid.UUID,
    target_candidate_id: uuid.UUID,
    actor_kind: str,
    actor_user_id: uuid.UUID | None,
) -> tuple[CapabilityRequest, FoundryCandidate]:
    request = await db.scalar(
        select(CapabilityRequest)
        .where(CapabilityRequest.id == request_id)
        .with_for_update()
    )
    target = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == target_candidate_id)
        .with_for_update()
    )
    if request is None or target is None:
        raise FoundryCatalogError("foundry_resource_not_found", "Foundry resource not found", status_code=404)
    if request.gap_fingerprint != target.gap_fingerprint:
        raise FoundryCatalogError(
            "candidate_fingerprint_mismatch",
            "Only requests with the same structured gap fingerprint can be merged",
            status_code=409,
        )
    previous_candidate_id = request.candidate_id
    request.candidate_id = target.id
    request.status = "MERGED"
    await _append_event(
        db,
        candidate_id=target.id,
        event_type="CAPABILITY_REQUEST_MERGED",
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        payload={
            "request_id": str(request.id),
            "from_candidate_id": str(previous_candidate_id) if previous_candidate_id else None,
        },
    )
    await db.commit()
    await db.refresh(request)
    return request, target


async def ensure_validation_run(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    candidate_version_id: uuid.UUID,
    candidate_version_hash: str,
    actor_kind: str,
    actor_user_id: uuid.UUID | None,
    reuse_terminal: bool = False,
    now: datetime | None = None,
) -> tuple[FoundryValidationRun, bool]:
    """Return one validation run and whether this call created it.

    ``reuse_terminal`` is reserved for idempotent control-plane triggers such
    as an AI Draft result callback.  It prevents a replay from turning a
    previously recorded dispatch failure into a new implicit retry.  Human
    validation requests leave it disabled so an explicit retry after
    ``DISPATCH_FAILED`` still creates a fresh immutable run.
    """

    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
    version = await db.get(FoundryCandidateVersion, candidate_version_id)
    if candidate is None or version is None or version.candidate_id != candidate_id:
        raise FoundryCatalogError("candidate_version_not_found", "Candidate version not found", status_code=404)
    if version.version_hash != candidate_version_hash:
        raise FoundryCatalogError(
            "candidate_version_binding_mismatch",
            "Validation must bind the exact candidate version hash",
            status_code=409,
        )
    if candidate_bundle_contains_formal_claim_escape(version.candidate_bundle):
        raise FoundryCatalogError(
            "candidate_formal_claim_forbidden",
            "Persisted candidate bundle fails the current non-formal evidence policy",
            status_code=409,
        )
    if reuse_terminal:
        existing = await db.scalar(
            select(FoundryValidationRun)
            .where(
                FoundryValidationRun.candidate_version_id == version.id,
                FoundryValidationRun.candidate_version_hash == version.version_hash,
            )
            .order_by(FoundryValidationRun.created_at.asc())
        )
        if existing is not None:
            return existing, False
    if (
        candidate.current_version_number != version.version_number
    ):
        raise FoundryCatalogError(
            "candidate_version_binding_mismatch",
            "Validation must bind the exact current candidate version hash",
            status_code=409,
        )
    if candidate.status in {
        "APPROVED",
        "REJECTED",
        "PROMOTED",
        "SUSPENDED",
        "SUPERSEDED",
        "REVOKED",
    }:
        raise FoundryCatalogError(
            "candidate_version_validation_closed",
            "This exact candidate version has a terminal review decision; create a new version to validate",
            status_code=409,
        )
    if candidate.status in {"PROMOTED", "SUSPENDED", "SUPERSEDED", "REVOKED"}:
        raise FoundryCatalogError(
            "candidate_not_validatable", "This candidate cannot start a validation run", status_code=409
        )
    runs = list(
        (
            await db.execute(
                select(FoundryValidationRun)
                .where(
                    FoundryValidationRun.candidate_version_id == version.id,
                    FoundryValidationRun.candidate_version_hash
                    == version.version_hash,
                )
                .order_by(FoundryValidationRun.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    active = next(
        (
            row
            for row in reversed(runs)
            if row.status
            in {"QUEUED", "OUTCOME_UNKNOWN", "DISPATCHED", "RUNNING"}
        ),
        None,
    )
    if active is not None:
        active_since = active.started_at or active.created_at
        if active_since.tzinfo is None:
            active_since = active_since.replace(tzinfo=timezone.utc)
        lease = (
            VALIDATION_DISPATCH_LEASE
            if active.status == "QUEUED"
            else VALIDATION_WORKFLOW_LEASE
        )
        retry_after = active_since + lease
        if current_time < retry_after:
            summary = dict(active.validation_summary or {})
            summary.update(
                {
                    "phase": (
                        "DISPATCH_PENDING"
                        if active.status == "QUEUED"
                        else (
                            "DISPATCH_UNCERTAIN"
                            if active.status == "OUTCOME_UNKNOWN"
                            else active.status
                        )
                    ),
                    "retry_after": retry_after.isoformat(),
                }
            )
            active.validation_summary = summary
            await db.commit()
            await db.refresh(active)
            return active, False

        active_status = active.status
        timeout_event_type = {
            "QUEUED": "VALIDATION_DISPATCH_RESERVATION_TIMED_OUT",
            "OUTCOME_UNKNOWN": "VALIDATION_DISPATCH_UNCERTAIN_TIMED_OUT",
            "DISPATCHED": "VALIDATION_WORKFLOW_TIMED_OUT",
            "RUNNING": "VALIDATION_WORKFLOW_TIMED_OUT",
        }[active_status]
        timeout_phase = (
            "DISPATCH_UNCERTAIN"
            if active_status == "OUTCOME_UNKNOWN"
            else (
                "DISPATCH_PENDING" if active_status == "QUEUED" else active_status
            )
        )
        attempt_number = int(
            (active.validation_summary or {}).get("attempt_number")
            or runs.index(active) + 1
        )
        active.failure_class = {
            "QUEUED": "validation_dispatch_reservation_timeout",
            "OUTCOME_UNKNOWN": "validation_dispatch_outcome_unknown_timeout",
            "DISPATCHED": "validation_workflow_timeout",
            "RUNNING": "validation_workflow_timeout",
        }[active_status]
        active.status = "TIMED_OUT"
        active.completed_at = current_time
        active.validation_summary = {
            **dict(active.validation_summary or {}),
            "phase": "TIMED_OUT",
            "timeout_phase": timeout_phase,
            "timeout_seconds": int(lease.total_seconds()),
            "retryable": attempt_number < VALIDATION_MAX_ATTEMPTS,
        }
        if (
            candidate.current_version_number == version.version_number
            and candidate.status == "VALIDATING"
        ):
            candidate.status = "BUILDING"
        await _append_event(
            db,
            candidate_id=candidate.id,
            candidate_version_id=version.id,
            event_type=timeout_event_type,
            actor_kind="CONTROL_PLANE",
            actor_user_id=None,
            payload={
                "validation_run_id": str(active.id),
                "candidate_version_hash": version.version_hash,
                "attempt_number": attempt_number,
                "max_attempts": VALIDATION_MAX_ATTEMPTS,
                "timeout_phase": timeout_phase,
                "timeout_seconds": int(lease.total_seconds()),
                "retryable": attempt_number < VALIDATION_MAX_ATTEMPTS,
            },
        )

    if len(runs) >= VALIDATION_MAX_ATTEMPTS:
        await db.commit()
        raise FoundryCatalogError(
            "validation_attempts_exhausted",
            "Candidate validation reached the bounded attempt limit",
            status_code=409,
        )
    if runs:
        latest = runs[-1]
        if latest.status == "DISPATCH_FAILED":
            summary = dict(latest.validation_summary or {})
            retryable = bool(summary.get("retryable"))
            if "retryable" not in summary:
                # Before the attempt-state upgrade, retryability lived only
                # in the append-only event.  Preserve that exact legacy
                # contract without treating an unbound/missing event as a
                # retry grant.
                legacy_events = list(
                    (
                        await db.execute(
                            select(FoundryCandidateEvent).where(
                                FoundryCandidateEvent.candidate_id
                                == candidate.id,
                                FoundryCandidateEvent.candidate_version_id
                                == version.id,
                                FoundryCandidateEvent.event_type
                                == "VALIDATION_DISPATCH_FAILED",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                retryable = any(
                    (event.event_payload or {}).get("validation_run_id")
                    == str(latest.id)
                    and (event.event_payload or {}).get("retryable") is True
                    for event in legacy_events
                )
            if not retryable:
                await db.commit()
                raise FoundryCatalogError(
                    "validation_dispatch_not_retryable",
                    "Candidate validation dispatch failed permanently",
                    status_code=409,
                )
    attempt_number = len(runs) + 1
    retry_after = current_time + VALIDATION_DISPATCH_LEASE
    run = FoundryValidationRun(
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        status="QUEUED",
        requested_by_kind=actor_kind,
        requested_by_user_id=actor_user_id,
        validation_summary={
            "attempt_number": attempt_number,
            "max_attempts": VALIDATION_MAX_ATTEMPTS,
            "phase": "DISPATCH_PENDING",
            "retry_after": retry_after.isoformat(),
            "dispatch_lease_seconds": int(
                VALIDATION_DISPATCH_LEASE.total_seconds()
            ),
            "workflow_lease_seconds": int(
                VALIDATION_WORKFLOW_LEASE.total_seconds()
            ),
        },
    )
    db.add(run)
    candidate.status = "VALIDATING"
    await db.flush()
    await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="VALIDATION_QUEUED",
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        payload={
            "validation_run_id": str(run.id),
            "candidate_version_hash": version.version_hash,
            "attempt_number": attempt_number,
            "max_attempts": VALIDATION_MAX_ATTEMPTS,
            "dispatch_lease_seconds": int(
                VALIDATION_DISPATCH_LEASE.total_seconds()
            ),
            "workflow_lease_seconds": int(
                VALIDATION_WORKFLOW_LEASE.total_seconds()
            ),
        },
    )
    await db.commit()
    await db.refresh(run)
    return run, True


async def start_validation_run(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    candidate_version_id: uuid.UUID,
    candidate_version_hash: str,
    actor_kind: str,
    actor_user_id: uuid.UUID | None,
) -> FoundryValidationRun:
    """Backward-compatible explicit validation-run creator."""

    run, _created = await ensure_validation_run(
        db,
        candidate_id=candidate_id,
        candidate_version_id=candidate_version_id,
        candidate_version_hash=candidate_version_hash,
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
    )
    return run


async def record_validation_dispatch(
    db: AsyncSession,
    *,
    validation_run_id: uuid.UUID,
    dispatched: bool,
    failure_class: str | None = None,
    retryable: bool = False,
    delivery_uncertain: bool = False,
    now: datetime | None = None,
) -> FoundryValidationRun:
    """Record one idempotent CI delivery transition for an exact attempt.

    ``delivery_uncertain`` is also written *before* crossing the external
    network boundary.  A process crash or lost response then keeps the exact
    attempt under the long workflow lease instead of dispatching a duplicate.
    """

    initial = await db.get(FoundryValidationRun, validation_run_id)
    if initial is None:
        raise FoundryCatalogError(
            "validation_run_not_found", "Validation run not found", status_code=404
        )
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == initial.candidate_id)
        .with_for_update()
    )
    run = await db.scalar(
        select(FoundryValidationRun)
        .where(FoundryValidationRun.id == validation_run_id)
        .with_for_update()
    )
    if run is None:
        raise FoundryCatalogError(
            "validation_run_not_found", "Validation run not found", status_code=404
        )
    desired_status = (
        "DISPATCHED"
        if dispatched
        else ("OUTCOME_UNKNOWN" if delivery_uncertain else "DISPATCH_FAILED")
    )
    if run.status == desired_status:
        if desired_status != "OUTCOME_UNKNOWN" or (
            run.failure_class == str(failure_class or "dispatch_outcome_unknown")[:255]
        ):
            return run
    if run.status not in {"QUEUED", "OUTCOME_UNKNOWN"}:
        if run.status in DEMO_STATUSES or run.status in {
            "TIMED_OUT",
            "WORKFLOW_FAILED",
            "DISPATCH_FAILED",
        }:
            return run
        raise FoundryCatalogError(
            "validation_dispatch_state_conflict",
            "Validation dispatch outcome conflicts with the durable run state",
            status_code=409,
        )
    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    attempt_number = int(
        (run.validation_summary or {}).get("attempt_number") or 1
    )
    effective_retryable = bool(retryable) and attempt_number < VALIDATION_MAX_ATTEMPTS
    run.status = desired_status
    run.failure_class = (
        None
        if dispatched
        else str(failure_class or "dispatch_failed")[:255]
    )
    started_at = run.started_at or current_time
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    run.started_at = started_at
    lease = (
        VALIDATION_WORKFLOW_LEASE
        if desired_status in {"DISPATCHED", "OUTCOME_UNKNOWN"}
        else None
    )
    run.completed_at = current_time if desired_status == "DISPATCH_FAILED" else None
    run.validation_summary = {
        **dict(run.validation_summary or {}),
        "phase": (
            "DISPATCH_UNCERTAIN"
            if desired_status == "OUTCOME_UNKNOWN"
            else desired_status
        ),
        "retryable": effective_retryable,
        "delivery_uncertain": desired_status == "OUTCOME_UNKNOWN",
        "retry_after": (
            (started_at + lease).isoformat() if lease is not None else None
        ),
    }
    version = await db.get(FoundryCandidateVersion, run.candidate_version_id)
    if (
        candidate is not None
        and version is not None
        and desired_status == "DISPATCH_FAILED"
        and candidate.current_version_number
        == version.version_number
        and candidate.status == "VALIDATING"
    ):
        candidate.status = "BUILDING"
    event_type = {
        "DISPATCHED": "VALIDATION_DISPATCHED",
        "OUTCOME_UNKNOWN": "VALIDATION_DISPATCH_UNCERTAIN",
        "DISPATCH_FAILED": "VALIDATION_DISPATCH_FAILED",
    }[desired_status]
    await _append_event(
        db,
        candidate_id=run.candidate_id,
        candidate_version_id=run.candidate_version_id,
        event_type=event_type,
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload={
            "validation_run_id": str(run.id),
            "status": run.status,
            "failure_class": run.failure_class,
            "attempt_number": attempt_number,
            "max_attempts": VALIDATION_MAX_ATTEMPTS,
            "delivery_uncertain": desired_status == "OUTCOME_UNKNOWN",
            "retryable": effective_retryable,
            "retry_after": (run.validation_summary or {}).get("retry_after"),
        },
    )
    await db.commit()
    await db.refresh(run)
    return run


async def record_validation_workflow_failure(
    db: AsyncSession,
    *,
    validation_run_id: uuid.UUID,
    report: dict[str, Any],
    expected_repository: str,
    expected_workflow_ref: str,
    now: datetime | None = None,
) -> tuple[FoundryValidationRun, bool]:
    """Close one exact protected validation attempt without creating evidence."""

    try:
        report_run_id = uuid.UUID(str(report["validation_run_id"]))
        candidate_id = uuid.UUID(str(report["candidate_id"]))
        version_id = uuid.UUID(str(report["candidate_version_id"]))
    except (KeyError, ValueError) as exc:
        raise FoundryCatalogError(
            "validation_failure_binding_invalid",
            "Validation failure identifiers are invalid",
            status_code=409,
        ) from exc
    initial = await db.get(FoundryValidationRun, validation_run_id)
    if initial is None:
        raise FoundryCatalogError(
            "validation_run_not_found", "Validation run not found", status_code=404
        )
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == initial.candidate_id)
        .with_for_update()
    )
    run = await db.scalar(
        select(FoundryValidationRun)
        .where(FoundryValidationRun.id == validation_run_id)
        .with_for_update()
    )
    version = await db.get(FoundryCandidateVersion, version_id)
    if (
        candidate is None
        or run is None
        or report_run_id != validation_run_id
        or run.candidate_id != candidate_id
        or run.candidate_version_id != version_id
        or version is None
        or version.candidate_id != candidate_id
        or run.candidate_version_hash != version.version_hash
        or report.get("candidate_version_hash") != version.version_hash
        or report.get("candidate_version_number") != version.version_number
        or report.get("status") != "FAILED"
        or report.get("failure_class") != "validation_workflow_failed"
        or report.get("failed_stage") != "isolated_demo"
        or report.get("workflow_conclusion")
        not in {"failure", "cancelled", "skipped"}
        or report.get("github_repository") != expected_repository
        or report.get("github_workflow_ref") != expected_workflow_ref
        or not _GIT_SHA_RE.fullmatch(str(report.get("github_workflow_sha") or ""))
        or not re.fullmatch(
            r"[1-9][0-9]{0,19}", str(report.get("github_run_id") or "")
        )
        or str(report.get("github_run_attempt") or "") != "1"
    ):
        raise FoundryCatalogError(
            "validation_failure_binding_invalid",
            "Validation failure does not match its protected attempt binding",
            status_code=409,
        )
    report_hash = sha256_json(report)
    existing_failure_hash = (run.validation_summary or {}).get(
        "workflow_failure_report_sha256"
    )
    if run.status == "WORKFLOW_FAILED":
        if existing_failure_hash != report_hash:
            raise FoundryCatalogError(
                "validation_failure_result_conflict",
                "Validation attempt already has a different failure result",
                status_code=409,
            )
        return run, False
    existing_demo = await db.scalar(
        select(FoundryDemoRun).where(
            FoundryDemoRun.validation_run_id == validation_run_id
        )
    )
    if existing_demo is not None:
        # The Demo callback may have succeeded before a later artifact-upload
        # step failed.  Never overwrite the already durable scientific Demo.
        return run, False
    if run.status not in {"OUTCOME_UNKNOWN", "DISPATCHED", "RUNNING"}:
        raise FoundryCatalogError(
            "validation_attempt_closed",
            "Validation attempt is not active or is already terminal",
            status_code=409,
        )
    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    attempt_number = int(
        (run.validation_summary or {}).get("attempt_number") or 1
    )
    retryable = attempt_number < VALIDATION_MAX_ATTEMPTS
    run.status = "WORKFLOW_FAILED"
    run.failure_class = "validation_workflow_failed"
    run.completed_at = current_time
    run.validation_summary = {
        **dict(run.validation_summary or {}),
        "phase": "WORKFLOW_FAILED",
        "retryable": retryable,
        "workflow_failure_report_sha256": report_hash,
        "failed_stage": report["failed_stage"],
        "workflow_conclusion": report["workflow_conclusion"],
        "github_repository": report["github_repository"],
        "github_workflow_ref": report["github_workflow_ref"],
        "github_workflow_sha": report["github_workflow_sha"],
        "github_run_id": str(report["github_run_id"]),
        "github_run_attempt": str(report["github_run_attempt"]),
        "retry_after": None,
    }
    if (
        candidate.current_version_number == version.version_number
        and candidate.status == "VALIDATING"
    ):
        candidate.status = "BUILDING"
    await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="VALIDATION_WORKFLOW_FAILED",
        actor_kind="PROTECTED_VALIDATION_CALLBACK",
        actor_user_id=None,
        payload={
            "validation_run_id": str(run.id),
            "candidate_version_hash": version.version_hash,
            "attempt_number": attempt_number,
            "max_attempts": VALIDATION_MAX_ATTEMPTS,
            "failure_class": run.failure_class,
            "failed_stage": report["failed_stage"],
            "workflow_conclusion": report["workflow_conclusion"],
            "github_repository": report["github_repository"],
            "github_workflow_ref": report["github_workflow_ref"],
            "github_workflow_sha": report["github_workflow_sha"],
            "github_run_id": str(report["github_run_id"]),
            "github_run_attempt": str(report["github_run_attempt"]),
            "failure_report_sha256": report_hash,
            "retryable": retryable,
        },
    )
    await db.commit()
    await db.refresh(run)
    return run, True


async def reconcile_expired_validation_runs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """Close expired validation attempts even when no user retries them."""

    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    dispatch_cutoff = current_time - VALIDATION_DISPATCH_LEASE
    workflow_cutoff = current_time - VALIDATION_WORKFLOW_LEASE
    run_ids = list(
        (
            await db.execute(
                select(FoundryValidationRun.id)
                .where(
                    or_(
                        and_(
                            FoundryValidationRun.status == "QUEUED",
                            FoundryValidationRun.created_at <= dispatch_cutoff,
                        ),
                        and_(
                            FoundryValidationRun.status.in_(
                                {"OUTCOME_UNKNOWN", "DISPATCHED", "RUNNING"}
                            ),
                            func.coalesce(
                                FoundryValidationRun.started_at,
                                FoundryValidationRun.created_at,
                            )
                            <= workflow_cutoff,
                        ),
                    )
                )
                .order_by(
                    func.coalesce(
                        FoundryValidationRun.started_at,
                        FoundryValidationRun.created_at,
                    ).asc()
                )
                .limit(max(1, min(int(limit), 1000)))
            )
        )
        .scalars()
        .all()
    )
    reconciled = 0
    for run_id in run_ids:
        initial = await db.get(FoundryValidationRun, run_id)
        if initial is None:
            continue
        candidate = await db.scalar(
            select(FoundryCandidate)
            .where(FoundryCandidate.id == initial.candidate_id)
            .with_for_update()
        )
        run = await db.scalar(
            select(FoundryValidationRun)
            .where(FoundryValidationRun.id == run_id)
            .with_for_update()
        )
        if candidate is None or run is None or run.status not in {
            "QUEUED",
            "OUTCOME_UNKNOWN",
            "DISPATCHED",
            "RUNNING",
        }:
            await db.rollback()
            continue
        active_status = run.status
        active_since = run.started_at or run.created_at
        if active_since.tzinfo is None:
            active_since = active_since.replace(tzinfo=timezone.utc)
        lease = (
            VALIDATION_DISPATCH_LEASE
            if active_status == "QUEUED"
            else VALIDATION_WORKFLOW_LEASE
        )
        if current_time < active_since + lease:
            await db.rollback()
            continue
        version = await db.get(FoundryCandidateVersion, run.candidate_version_id)
        if version is None or version.version_hash != run.candidate_version_hash:
            await db.rollback()
            continue
        attempt_number = int(
            (run.validation_summary or {}).get("attempt_number") or 1
        )
        timeout_phase = {
            "QUEUED": "DISPATCH_PENDING",
            "OUTCOME_UNKNOWN": "DISPATCH_UNCERTAIN",
            "DISPATCHED": "DISPATCHED",
            "RUNNING": "RUNNING",
        }[active_status]
        run.status = "TIMED_OUT"
        run.failure_class = {
            "QUEUED": "validation_dispatch_reservation_timeout",
            "OUTCOME_UNKNOWN": "validation_dispatch_outcome_unknown_timeout",
            "DISPATCHED": "validation_workflow_timeout",
            "RUNNING": "validation_workflow_timeout",
        }[active_status]
        run.completed_at = current_time
        run.validation_summary = {
            **dict(run.validation_summary or {}),
            "phase": "TIMED_OUT",
            "timeout_phase": timeout_phase,
            "timeout_seconds": int(lease.total_seconds()),
            "retryable": attempt_number < VALIDATION_MAX_ATTEMPTS,
            "retry_after": None,
        }
        if (
            candidate.current_version_number == version.version_number
            and candidate.status == "VALIDATING"
        ):
            candidate.status = "BUILDING"
        await _append_event(
            db,
            candidate_id=candidate.id,
            candidate_version_id=version.id,
            event_type=(
                "VALIDATION_DISPATCH_RESERVATION_TIMED_OUT"
                if active_status == "QUEUED"
                else (
                    "VALIDATION_DISPATCH_UNCERTAIN_TIMED_OUT"
                    if active_status == "OUTCOME_UNKNOWN"
                    else "VALIDATION_WORKFLOW_TIMED_OUT"
                )
            ),
            actor_kind="CONTROL_PLANE_RECONCILER",
            actor_user_id=None,
            payload={
                "validation_run_id": str(run.id),
                "candidate_version_hash": version.version_hash,
                "attempt_number": attempt_number,
                "max_attempts": VALIDATION_MAX_ATTEMPTS,
                "timeout_phase": timeout_phase,
                "timeout_seconds": int(lease.total_seconds()),
                "retryable": attempt_number < VALIDATION_MAX_ATTEMPTS,
            },
        )
        await db.commit()
        reconciled += 1
    return reconciled


def _parse_report_time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise FoundryCatalogError(
            "invalid_demo_report", f"{field} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise FoundryCatalogError(
            "invalid_demo_report", f"{field} must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


async def record_demo_report(
    db: AsyncSession,
    *,
    validation_run_id: uuid.UUID,
    demo_report: dict[str, Any],
) -> FoundryDemoRun:
    """Persist one exact, runner-produced DemoReport as non-formal evidence.

    This is an internal authenticated-runner service, not a browser endpoint.
    It recomputes every binding instead of trusting the report's claim flags.
    """

    if not isinstance(demo_report, dict):
        raise FoundryCatalogError("invalid_demo_report", "DemoReport must be an object")
    contract_issue = demo_report_contract_issue(demo_report)
    if contract_issue is not None:
        raise FoundryCatalogError(
            "invalid_demo_report",
            f"DemoReport violates schema version 1: {contract_issue}",
        )
    report = json.loads(canonical_json(demo_report))
    declared_report_hash = str(report.pop("demo_report_sha256", ""))
    if _require_sha256(declared_report_hash, "demo_report_sha256") != sha256_json(report):
        raise FoundryCatalogError(
            "demo_report_hash_mismatch", "DemoReport content hash does not match"
        )
    if report.get("schema_version") != 1:
        raise FoundryCatalogError("invalid_demo_report", "Unsupported DemoReport schema")
    if (
        report.get("evidence_class") != NON_FORMAL_EVIDENCE_CLASS
        or report.get("publication_ready") is not False
        or report.get("claim_eligible") is not False
        or report.get("evidence_pack_allowed") is not False
        or contains_formal_claim_escape(report, scan_text_leaves=True)
    ):
        raise FoundryCatalogError(
            "candidate_formal_claim_forbidden",
            "DemoReport attempted to cross the non-formal evidence boundary",
        )
    status = str(report["status"])
    try:
        report_demo_id = uuid.UUID(str(report.get("demo_run_id")))
    except ValueError as exc:
        raise FoundryCatalogError(
            "invalid_demo_report", "demo_run_id must be a UUID"
        ) from exc
    initial = await db.get(FoundryValidationRun, validation_run_id)
    if initial is None:
        raise FoundryCatalogError(
            "validation_run_not_found", "Validation run not found", status_code=404
        )
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == initial.candidate_id)
        .with_for_update()
    )
    run = await db.scalar(
        select(FoundryValidationRun)
        .where(FoundryValidationRun.id == validation_run_id)
        .with_for_update()
    )
    if run is None or candidate is None:
        raise FoundryCatalogError(
            "validation_run_not_found", "Validation run not found", status_code=404
        )
    existing = await db.get(FoundryDemoRun, report_demo_id)
    if existing is not None:
        if (
            existing.demo_report_hash != declared_report_hash
            or existing.validation_run_id != validation_run_id
        ):
            raise FoundryCatalogError(
                "demo_run_id_conflict",
                "The Demo run id already exists with different content or binding",
                status_code=409,
            )
        return existing
    existing_for_attempt = await db.scalar(
        select(FoundryDemoRun).where(
            FoundryDemoRun.validation_run_id == validation_run_id
        )
    )
    if existing_for_attempt is not None:
        raise FoundryCatalogError(
            "validation_result_conflict",
            "Validation attempt already has a different Demo result",
            status_code=409,
        )
    if run.status not in {"OUTCOME_UNKNOWN", "DISPATCHED", "RUNNING"}:
        raise FoundryCatalogError(
            "validation_attempt_closed",
            "Validation attempt is not active or is already terminal",
            status_code=409,
        )
    version = await db.get(FoundryCandidateVersion, run.candidate_version_id)
    if version is None:
        raise FoundryCatalogError(
            "candidate_version_not_found", "Candidate version not found", status_code=404
        )
    if (
        version.version_hash != run.candidate_version_hash
        or report.get("candidate_id") != version.candidate_key
        or report.get("candidate_version") != version.version_number
        or report.get("candidate_bundle_sha256")
        != sha256_json(version.candidate_bundle)
        or report.get("candidate_version_sha256") != version.version_hash
        or report.get("workflow_spec_sha256") != version.workflow_spec_hash
        or report.get("dependency_lock_sha256") != version.dependency_lock_hash
        or report.get("runner_definition_sha256")
        != version.candidate_bundle.get("runner_definition_sha256")
        or report.get("generation") != version.candidate_bundle.get("generation")
        or report.get("source_pins") != version.candidate_bundle.get("source_pins")
        or report.get("fixture_hashes") != version.candidate_bundle.get("fixture_hashes")
        or report.get("limitations") != version.candidate_bundle.get("limitations")
    ):
        raise FoundryCatalogError(
            "candidate_version_binding_mismatch",
            "DemoReport does not bind the exact immutable candidate bundle",
            status_code=409,
        )
    image_digest = _require_image_digest(report.get("runner_image_digest"))
    if image_digest != version.validation_runner_image_digest:
        raise FoundryCatalogError(
            "runner_image_binding_mismatch",
            "DemoReport runner image does not match the candidate version",
            status_code=409,
        )
    environment = report.get("environment")
    if (
        not isinstance(environment, dict)
        or _require_sha256(
            report.get("environment_sha256"), "environment_sha256"
        )
        != sha256_json(environment)
    ):
        raise FoundryCatalogError(
            "demo_environment_hash_mismatch",
            "DemoReport environment hash does not match",
        )
    stdout_sha256 = _require_sha256(report.get("stdout_sha256"), "stdout_sha256")
    stderr_sha256 = _require_sha256(report.get("stderr_sha256"), "stderr_sha256")
    stdout_bytes = report.get("stdout_bytes")
    stderr_bytes = report.get("stderr_bytes")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (stdout_bytes, stderr_bytes)
    ):
        raise FoundryCatalogError(
            "invalid_demo_stream_receipt",
            "DemoReport stream byte counts must be non-negative integers",
        )
    artifact_manifest = report.get("artifact_manifest")
    expected_artifacts = [
        {
            "path": "stdout.log",
            "kind": "STDOUT",
            "sha256": stdout_sha256,
            "bytes": stdout_bytes,
        },
        {
            "path": "stderr.log",
            "kind": "STDERR",
            "sha256": stderr_sha256,
            "bytes": stderr_bytes,
        },
    ]
    if artifact_manifest != expected_artifacts:
        raise FoundryCatalogError(
            "invalid_demo_artifact_manifest",
            "DemoReport log artifacts must match the exact stream receipts",
        )
    resource_usage = report.get("resource_usage")
    if not isinstance(resource_usage, dict):
        raise FoundryCatalogError(
            "invalid_demo_resource_usage",
            "DemoReport resource_usage must be an object",
        )
    started_at = _parse_report_time(report.get("started_at"), "started_at")
    completed_at = _parse_report_time(report.get("completed_at"), "completed_at")
    duration_ms = report.get("duration_ms")
    measured_duration = max(0, int((completed_at - started_at).total_seconds() * 1000))
    if (
        completed_at < started_at
        or not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or duration_ms != measured_duration
    ):
        raise FoundryCatalogError(
            "demo_time_binding_mismatch", "DemoReport duration does not match its timestamps"
        )
    run.status = status
    run.runner_image_digest = image_digest
    attempt_number = int(
        (run.validation_summary or {}).get("attempt_number") or 1
    )
    run.validation_summary = {
        **dict(run.validation_summary or {}),
        "phase": status,
        "retryable": False,
        "retry_after": None,
        "demo_report_sha256": declared_report_hash,
        "demo_validation": dict(report.get("validation_summary") or {}),
    }
    run.failure_class = str(report.get("failure_class") or "") or None
    run.started_at = started_at
    run.completed_at = completed_at
    demo = FoundryDemoRun(
        id=report_demo_id,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_key=version.candidate_key,
        validation_run_id=run.id,
        status=status,
        evidence_class=NON_FORMAL_EVIDENCE_CLASS,
        publication_ready=False,
        claim_eligible=False,
        evidence_pack_allowed=False,
        candidate_version_hash=version.version_hash,
        candidate_bundle_hash=str(report["candidate_bundle_sha256"]),
        workflow_spec_hash=version.workflow_spec_hash,
        code_tree_hash=version.code_tree_hash,
        dependency_lock_hash=version.dependency_lock_hash,
        runner_definition_hash=str(report["runner_definition_sha256"]),
        sbom_hash=version.sbom_hash,
        fixture_hashes=version.fixture_hashes,
        data_hashes=version.data_hashes,
        ai_model=version.ai_model,
        ai_generation_config=version.ai_generation_config,
        generation=dict(report.get("generation") or {}),
        source_pins=list(report.get("source_pins") or []),
        runner_image_digest=image_digest,
        environment=environment,
        environment_sha256=str(report["environment_sha256"]),
        stdout_sha256=stdout_sha256,
        stderr_sha256=stderr_sha256,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        structured_result=dict(report.get("result") or {}),
        limitations=[str(item) for item in report.get("limitations") or []],
        validation_summary=dict(report.get("validation_summary") or {}),
        failure_class=str(report.get("failure_class") or "") or None,
        resource_usage=resource_usage,
        artifact_manifest=artifact_manifest,
        duration_ms=duration_ms,
        demo_report_hash=declared_report_hash,
        started_at=started_at,
        completed_at=completed_at,
    )
    db.add(demo)
    # A newer immutable version may be appended while this exact-version run is
    # still executing.  Keep the older Demo and its event, but never let that
    # late callback overwrite the aggregate state owned by the newer version.
    if (
        candidate.current_version_number == version.version_number
        and candidate.status == "VALIDATING"
    ):
        candidate.status = "DEMO_RECORDED"
    await db.flush()
    await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="DEMO_RECORDED",
        actor_kind="VALIDATION_RUNNER",
        actor_user_id=None,
        payload={
            "demo_run_id": str(demo.id),
            "validation_run_id": str(run.id),
            "attempt_number": attempt_number,
            "max_attempts": VALIDATION_MAX_ATTEMPTS,
            "demo_report_sha256": declared_report_hash,
            "status": status,
            "evidence_class": NON_FORMAL_EVIDENCE_CLASS,
            "publication_ready": False,
            "claim_eligible": False,
            "evidence_pack_allowed": False,
        },
    )
    await db.commit()
    await db.refresh(demo)
    return demo


def _demo_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _has_strict_demo_report_v1_receipt(
    demo: FoundryDemoRun,
    version: FoundryCandidateVersion,
) -> bool:
    """Verify that a stored row reconstructs the exact accepted v1 receipt."""

    expected_artifacts = [
        {
            "path": "stdout.log",
            "kind": "STDOUT",
            "sha256": demo.stdout_sha256,
            "bytes": demo.stdout_bytes,
        },
        {
            "path": "stderr.log",
            "kind": "STDERR",
            "sha256": demo.stderr_sha256,
            "bytes": demo.stderr_bytes,
        },
    ]
    started = demo.started_at
    completed = demo.completed_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    measured_duration = max(
        0,
        int((completed - started).total_seconds() * 1000),
    )
    body = {
        "schema_version": 1,
        "candidate_id": demo.candidate_key,
        "candidate_version": version.version_number,
        "demo_run_id": str(demo.id),
        "status": demo.status,
        "evidence_class": demo.evidence_class,
        "publication_ready": demo.publication_ready,
        "claim_eligible": demo.claim_eligible,
        "evidence_pack_allowed": demo.evidence_pack_allowed,
        "candidate_bundle_sha256": demo.candidate_bundle_hash,
        "candidate_version_sha256": demo.candidate_version_hash,
        "workflow_spec_sha256": demo.workflow_spec_hash,
        "dependency_lock_sha256": demo.dependency_lock_hash,
        "runner_definition_sha256": demo.runner_definition_hash,
        "runner_image_digest": demo.runner_image_digest,
        "environment": dict(demo.environment or {}),
        "environment_sha256": demo.environment_sha256,
        "generation": dict(demo.generation or {}),
        "source_pins": list(demo.source_pins or []),
        "fixture_hashes": list(demo.fixture_hashes or []),
        "started_at": _demo_time(demo.started_at),
        "completed_at": _demo_time(demo.completed_at),
        "duration_ms": demo.duration_ms,
        "stdout_sha256": demo.stdout_sha256,
        "stderr_sha256": demo.stderr_sha256,
        "stdout_bytes": demo.stdout_bytes,
        "stderr_bytes": demo.stderr_bytes,
        "artifact_manifest": list(demo.artifact_manifest or []),
        "resource_usage": dict(demo.resource_usage or {}),
        "failure_class": demo.failure_class,
        "validation_summary": dict(demo.validation_summary or {}),
        "limitations": list(demo.limitations or []),
        "result": dict(demo.structured_result or {}),
    }
    complete = {**body, "demo_report_sha256": demo.demo_report_hash}
    expected_bundle_hash = sha256_json(version.candidate_bundle)
    return bool(
        demo.candidate_version_id == version.id
        and demo.candidate_id == version.candidate_id
        and demo.candidate_key == version.candidate_key
        and demo.candidate_version_hash == version.version_hash
        and demo.candidate_bundle_hash == expected_bundle_hash
        and demo.workflow_spec_hash == version.workflow_spec_hash
        and demo.code_tree_hash == version.code_tree_hash
        and demo.dependency_lock_hash == version.dependency_lock_hash
        and demo.runner_definition_hash
        == version.candidate_bundle.get("runner_definition_sha256")
        and demo.sbom_hash == version.sbom_hash
        and demo.runner_image_digest == version.validation_runner_image_digest
        and demo.fixture_hashes == version.fixture_hashes
        and demo.fixture_hashes
        == version.candidate_bundle.get("fixture_hashes")
        and demo.generation == version.candidate_bundle.get("generation")
        and demo.source_pins == version.candidate_bundle.get("source_pins")
        and demo.limitations == version.candidate_bundle.get("limitations")
        and demo.artifact_manifest == expected_artifacts
        and demo.duration_ms == measured_duration
        and demo.environment_sha256 == sha256_json(body["environment"])
        and demo_report_contract_issue(complete) is None
        and not contains_formal_claim_escape(
            complete,
            scan_text_leaves=True,
        )
        and hmac.compare_digest(demo.demo_report_hash, sha256_json(body))
    )


async def _has_demo_validation_lineage(
    db: AsyncSession,
    demo: FoundryDemoRun,
    version: FoundryCandidateVersion,
) -> bool:
    """Bind a self-consistent report row to its accepted run and ledger event."""

    if demo.validation_run_id is None:
        return False
    run = await db.get(FoundryValidationRun, demo.validation_run_id)
    if run is None or run.started_at is None or run.completed_at is None:
        return False
    run_summary = dict(run.validation_summary or {})
    if not (
        run.candidate_id == version.candidate_id
        and run.candidate_version_id == version.id
        and run.candidate_version_hash == version.version_hash
        and run.status == demo.status
        and run.runner_image_digest == demo.runner_image_digest
        and run.failure_class == demo.failure_class
        and _demo_time(run.started_at) == _demo_time(demo.started_at)
        and _demo_time(run.completed_at) == _demo_time(demo.completed_at)
        and run_summary.get("phase") == demo.status
        and run_summary.get("retryable") is False
        and run_summary.get("retry_after") is None
        and run_summary.get("demo_report_sha256") == demo.demo_report_hash
        and run_summary.get("demo_validation") == demo.validation_summary
    ):
        return False
    events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == version.candidate_id,
                    FoundryCandidateEvent.candidate_version_id == version.id,
                    FoundryCandidateEvent.event_type == "DEMO_RECORDED",
                )
            )
        )
        .scalars()
        .all()
    )
    return any(
        event.actor_kind == "VALIDATION_RUNNER"
        and (event.event_payload or {}).get("demo_run_id") == str(demo.id)
        and (event.event_payload or {}).get("validation_run_id")
        == str(run.id)
        and (event.event_payload or {}).get("demo_report_sha256")
        == demo.demo_report_hash
        and (event.event_payload or {}).get("status") == demo.status
        and (event.event_payload or {}).get("evidence_class")
        == NON_FORMAL_EVIDENCE_CLASS
        and (event.event_payload or {}).get("publication_ready") is False
        and (event.event_payload or {}).get("claim_eligible") is False
        and (event.event_payload or {}).get("evidence_pack_allowed") is False
        for event in events
    )


async def _strict_passed_demo_v1(
    db: AsyncSession,
    version: FoundryCandidateVersion,
) -> FoundryDemoRun | None:
    if candidate_bundle_contains_formal_claim_escape(version.candidate_bundle):
        return None
    rows = list(
        (
            await db.execute(
                select(FoundryDemoRun)
                .where(
                    FoundryDemoRun.candidate_version_id == version.id,
                    FoundryDemoRun.status == "PASSED",
                    FoundryDemoRun.evidence_class == NON_FORMAL_EVIDENCE_CLASS,
                    FoundryDemoRun.publication_ready.is_(False),
                    FoundryDemoRun.claim_eligible.is_(False),
                    FoundryDemoRun.evidence_pack_allowed.is_(False),
                )
                .order_by(FoundryDemoRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        if _has_strict_demo_report_v1_receipt(
            row,
            version,
        ) and await _has_demo_validation_lineage(db, row, version):
            return row
    return None


async def record_formal_build_attestation(
    db: AsyncSession,
    *,
    attestation_report: dict[str, Any],
    expected_oidc_subject: str,
    expected_github_repository: str,
    expected_github_workflow: str,
    expected_github_ref: str,
    trusted_attestation_public_keys: Mapping[str, str],
) -> FoundryFormalBuildAttestation:
    """Verify and append one signed protected-CI formal-build receipt."""

    if not isinstance(attestation_report, dict):
        raise FoundryCatalogError(
            "invalid_formal_build_attestation", "Build attestation must be an object"
        )
    envelope = json.loads(canonical_json(attestation_report))
    declared_artifact_hash = str(
        envelope.pop("attestation_artifact_sha256", "")
    )
    if _require_sha256(
        declared_artifact_hash, "attestation_artifact_sha256"
    ) != sha256_json(envelope):
        raise FoundryCatalogError(
            "formal_build_attestation_artifact_hash_mismatch",
            "Formal build attestation artifact hash does not match",
        )
    if set(envelope) != {
        "schema_version",
        "payload",
        "payload_sha256",
        "signature",
    } or envelope.get("schema_version") != _FORMAL_BUILD_BUNDLE_SCHEMA:
        raise FoundryCatalogError(
            "invalid_formal_build_attestation",
            "Formal build attestation bundle shape is not registered",
        )
    report = envelope.get("payload")
    signature = envelope.get("signature")
    if not isinstance(report, dict) or not isinstance(signature, dict):
        raise FoundryCatalogError(
            "invalid_formal_build_attestation",
            "Formal build attestation payload and signature must be objects",
        )
    canonical_payload = canonical_json(report)
    declared_payload_hash = _require_sha256(
        envelope.get("payload_sha256"), "payload_sha256"
    )
    actual_payload_hash = hashlib.sha256(canonical_payload).hexdigest()
    if not hmac.compare_digest(declared_payload_hash, actual_payload_hash):
        raise FoundryCatalogError(
            "formal_build_payload_hash_mismatch",
            "Formal build signed payload hash does not match",
        )
    if set(signature) != {"algorithm", "key_id", "value"}:
        raise FoundryCatalogError(
            "formal_build_attestation_signature_invalid",
            "Formal build attestation signature shape is invalid",
        )
    key_id = str(signature.get("key_id") or "")
    public_key = str(trusted_attestation_public_keys.get(key_id) or "")
    if signature.get("algorithm") != "ed25519" or not public_key:
        raise FoundryCatalogError(
            "formal_build_attestation_key_untrusted",
            "Formal build attestation signing key is not trusted",
        )
    try:
        public_key_raw = base64.b64decode(public_key, validate=True)
        signature_raw = base64.b64decode(
            str(signature.get("value") or ""), validate=True
        )
        if len(public_key_raw) != 32 or len(signature_raw) != 64:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(public_key_raw).verify(
            signature_raw,
            _FORMAL_BUILD_SIGNING_DOMAIN + canonical_payload,
        )
    except (binascii.Error, InvalidSignature, TypeError, ValueError) as exc:
        raise FoundryCatalogError(
            "formal_build_attestation_signature_invalid",
            "Formal build attestation signature is invalid",
        ) from exc
    required = {
        "schema_version",
        "attestation_id",
        "candidate_id",
        "candidate_version_id",
        "candidate_version_hash",
        "source_tree_sha256",
        "git_commit",
        "dependency_lock_sha256",
        "formal_sbom_sha256",
        "test_report_sha256",
        "release_audit",
        "tests_passed",
        "subject",
        "build_identity",
        "sigstore",
        "provenance_sha256",
        "verification_method",
        "build_metadata",
        "built_at",
    }
    if (
        set(report) != required
        or report.get("schema_version") != _FORMAL_BUILD_PAYLOAD_SCHEMA
    ):
        raise FoundryCatalogError(
            "invalid_formal_build_attestation",
            "Formal build attestation shape is not registered",
        )
    try:
        attestation_id = uuid.UUID(str(report["attestation_id"]))
        candidate_id = uuid.UUID(str(report["candidate_id"]))
        candidate_version_id = uuid.UUID(str(report["candidate_version_id"]))
    except ValueError as exc:
        raise FoundryCatalogError(
            "invalid_formal_build_attestation",
            "Formal build identifiers must be UUIDs",
        ) from exc
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
    # The candidate lock serializes concurrent callbacks. Re-read the
    # attestation afterwards so an exact callback replay stays idempotent
    # instead of racing into the primary-key constraint.
    existing = await db.get(
        FoundryFormalBuildAttestation,
        attestation_id,
        populate_existing=True,
    )
    if existing is not None:
        if (
            existing.receipt_hash != declared_payload_hash
            or existing.attestation_artifact_hash != declared_artifact_hash
        ):
            raise FoundryCatalogError(
                "formal_build_attestation_id_conflict",
                "Build attestation id already exists with different content",
                status_code=409,
            )
        return existing
    version = await db.get(FoundryCandidateVersion, candidate_version_id)
    candidate_version_hash = _require_sha256(
        report["candidate_version_hash"], "candidate_version_hash"
    )
    if (
        candidate is None
        or version is None
        or version.candidate_id != candidate_id
        or candidate.status != "APPROVED"
        or candidate.current_version_number != version.version_number
        or version.version_hash != candidate_version_hash
    ):
        raise FoundryCatalogError(
            "formal_build_candidate_binding_mismatch",
            "Formal builds require the exact approved current candidate version",
            status_code=409,
        )
    passed_demo = await _strict_passed_demo_v1(db, version)
    reviews = list(
        (
            await db.execute(
                select(FoundryReview).where(
                    FoundryReview.candidate_version_id == version.id
                )
            )
        )
        .scalars()
        .all()
    )
    if passed_demo is None or not _review_requirements_satisfied(
        str(candidate.risk_level or ""), reviews
    ):
        raise FoundryCatalogError(
            "formal_build_approval_missing",
            "A PASSED Demo and required human approvals must precede formal build",
            status_code=409,
        )
    if report.get("tests_passed") is not True:
        raise FoundryCatalogError(
            "formal_build_verification_failed",
            "Protected CI formal tests must pass before callback",
        )
    if (
        report.get("verification_method")
        != "github_oidc_cosign_plus_ed25519_callback_v2"
    ):
        raise FoundryCatalogError(
            "formal_build_verification_method_invalid",
            "Formal builds require a signed protected-CI attestation bundle",
        )
    subject = report.get("subject")
    build_identity = report.get("build_identity")
    sigstore = report.get("sigstore")
    if (
        not isinstance(subject, dict)
        or set(subject) != {"image", "digest"}
        or not isinstance(build_identity, dict)
        or set(build_identity)
        != {
            "github_repository",
            "github_workflow_ref",
            "github_workflow_sha",
            "github_run_id",
            "github_run_attempt",
        }
        or not isinstance(sigstore, dict)
        or set(sigstore)
        != {
            "oidc_issuer",
            "certificate_identity",
            "bundle_sha256",
            "verification_record_sha256",
        }
    ):
        raise FoundryCatalogError(
            "invalid_formal_build_attestation",
            "Formal build identity, subject, or Sigstore receipt is malformed",
        )
    if sigstore.get("oidc_issuer") != "https://token.actions.githubusercontent.com":
        raise FoundryCatalogError(
            "formal_build_oidc_issuer_invalid", "Untrusted formal-build OIDC issuer"
        )
    expected_subject = str(expected_oidc_subject or "").strip()
    if not expected_subject or sigstore.get("certificate_identity") != expected_subject:
        raise FoundryCatalogError(
            "formal_build_oidc_subject_invalid", "Untrusted formal-build OIDC subject"
        )
    expected_repository = str(expected_github_repository or "").strip()
    expected_workflow = str(expected_github_workflow or "").strip()
    expected_ref = str(expected_github_ref or "").strip()
    expected_workflow_ref = (
        f"{expected_repository}/.github/workflows/{expected_workflow}"
        f"@refs/heads/{expected_ref}"
    )
    expected_image = f"ghcr.io/{expected_repository.lower()}/science-worker"
    if (
        not expected_repository
        or not expected_workflow
        or expected_ref != "main"
        or build_identity.get("github_repository") != expected_repository
        or build_identity.get("github_workflow_ref") != expected_workflow_ref
        or not _GIT_SHA_RE.fullmatch(
            str(build_identity.get("github_workflow_sha") or "")
        )
        or not str(build_identity.get("github_run_id") or "").isdigit()
        or build_identity.get("github_run_attempt") != 1
        or subject.get("image") != expected_image
    ):
        raise FoundryCatalogError(
            "formal_build_identity_mismatch",
            "Formal build does not come from the expected repository and workflow",
        )
    formal_worker_image_digest = _require_image_digest(subject.get("digest"))
    source_tree_hash = _require_sha256(report["source_tree_sha256"], "source_tree_sha256")
    dependency_lock_hash = _require_sha256(
        report["dependency_lock_sha256"], "dependency_lock_sha256"
    )
    formal_sbom_hash = _require_sha256(
        report["formal_sbom_sha256"], "formal_sbom_sha256"
    )
    if source_tree_hash != version.code_tree_hash or dependency_lock_hash != version.dependency_lock_hash:
        raise FoundryCatalogError(
            "formal_build_source_binding_mismatch",
            "Formal build source tree or dependency lock differs from the approved version",
            status_code=409,
        )
    formal_release_audit_hash, formal_release_audit_receipts = (
        _validate_formal_release_audit(
            report.get("release_audit"),
            source_tree_hash=source_tree_hash,
            dependency_lock_hash=dependency_lock_hash,
            formal_sbom_hash=formal_sbom_hash,
        )
    )
    git_commit = str(report.get("git_commit") or "").lower()
    if not _GIT_SHA_RE.fullmatch(git_commit):
        raise FoundryCatalogError(
            "invalid_formal_build_git_commit", "Formal build Git commit must be 40 hex characters"
        )
    materialization_receipt = await _validate_formal_source_provenance(
        db,
        version=version,
        source_commit=git_commit,
        source_tree_hash=source_tree_hash,
        dependency_lock_hash=dependency_lock_hash,
    )
    built_at = _parse_report_time(report["built_at"], "built_at")
    approval_times = [
        review.created_at.replace(tzinfo=timezone.utc)
        if review.created_at.tzinfo is None
        else review.created_at.astimezone(timezone.utc)
        for review in reviews
        if review.decision == "APPROVED"
    ]
    if not approval_times or built_at < max(approval_times):
        raise FoundryCatalogError(
            "formal_build_precedes_approval",
            "Formal Worker image must be built after exact-version approval",
            status_code=409,
        )
    build_metadata = report.get("build_metadata")
    if not isinstance(build_metadata, dict):
        raise FoundryCatalogError(
            "invalid_formal_build_metadata", "build_metadata must be an object"
        )
    attempt_id: uuid.UUID | None = None
    attempt_id_value = build_metadata.get("formal_build_attempt_id")
    if attempt_id_value is not None:
        try:
            attempt_id = uuid.UUID(str(attempt_id_value))
        except ValueError as exc:
            raise FoundryCatalogError(
                "formal_build_attempt_binding_invalid",
                "Signed formal build attempt identifier is invalid",
                status_code=409,
            ) from exc
    expected_metadata = {
        "candidate_id": str(candidate.id),
        "candidate_version_id": str(version.id),
        "candidate_version_hash": version.version_hash,
        "source_commit": git_commit,
        "source_tree_sha256": source_tree_hash,
        "formal_worker_image_digest": formal_worker_image_digest,
        "tests_passed": True,
        "platforms": ["linux/amd64", "linux/arm64"],
        "image": expected_image,
        "repository": expected_repository,
        "workflow_ref": expected_workflow_ref,
        "workflow_sha": str(build_identity["github_workflow_sha"]),
        "run_id": str(build_identity["github_run_id"]),
        "run_attempt": str(build_identity["github_run_attempt"]),
    }
    if attempt_id is not None:
        expected_metadata["formal_build_attempt_id"] = str(attempt_id)
    if any(build_metadata.get(name) != value for name, value in expected_metadata.items()):
        raise FoundryCatalogError(
            "formal_build_metadata_binding_mismatch",
            "Formal build metadata does not match its signed identity and subject",
        )
    attempt_events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate.id,
                    FoundryCandidateEvent.candidate_version_id == version.id,
                    FoundryCandidateEvent.event_type.in_(
                        {
                            "FORMAL_BUILD_ATTEMPT_RESERVED",
                            "FORMAL_BUILD_DISPATCHED",
                            "FORMAL_BUILD_DISPATCH_UNCERTAIN",
                            "FORMAL_BUILD_DISPATCH_FAILED",
                            "FORMAL_BUILD_ATTEMPT_FAILED",
                            "FORMAL_BUILD_DISPATCH_RESERVATION_TIMED_OUT",
                            "FORMAL_BUILD_DISPATCH_UNCERTAIN_TIMED_OUT",
                            "FORMAL_BUILD_ATTEMPT_TIMED_OUT",
                        }
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    all_reservations = [
        event
        for event in attempt_events
        if event.event_type == "FORMAL_BUILD_ATTEMPT_RESERVED"
    ]
    source_binding = _formal_build_source_binding(version)
    reservations = [
        event
        for event in all_reservations
        if all(
            (event.event_payload or {}).get(key) == value
            for key, value in source_binding.items()
        )
    ]
    # Receipts produced before the bounded-attempt ledger remain valid when
    # no reservation exists. Once the ledger exists, every new signed receipt
    # must identify one exact live, successfully dispatched attempt.
    if all_reservations or attempt_id is not None:
        if attempt_id is None:
            raise FoundryCatalogError(
                "formal_build_attempt_binding_missing",
                "Signed formal build receipt is missing its durable attempt binding",
                status_code=409,
            )
        reservation = next(
            (
                event
                for event in reservations
                if (event.event_payload or {}).get("formal_build_attempt_id")
                == str(attempt_id)
            ),
            None,
        )
        if reservation is None:
            raise FoundryCatalogError(
                "formal_build_attempt_binding_invalid",
                "Signed formal build receipt does not match a reserved attempt",
                status_code=409,
            )
        exact_attempt_events = [
            event
            for event in attempt_events
            if (event.event_payload or {}).get("formal_build_attempt_id")
            == str(attempt_id)
        ]
        if not any(
            event.event_type
            in {"FORMAL_BUILD_DISPATCHED", "FORMAL_BUILD_DISPATCH_UNCERTAIN"}
            for event in exact_attempt_events
        ):
            raise FoundryCatalogError(
                "formal_build_attempt_not_dispatched",
                "Signed formal build receipt has no successful dispatch record",
                status_code=409,
            )
        if any(
            event.event_type
            in {
                "FORMAL_BUILD_DISPATCH_FAILED",
                "FORMAL_BUILD_ATTEMPT_FAILED",
                "FORMAL_BUILD_DISPATCH_RESERVATION_TIMED_OUT",
                "FORMAL_BUILD_DISPATCH_UNCERTAIN_TIMED_OUT",
                "FORMAL_BUILD_ATTEMPT_TIMED_OUT",
            }
            for event in exact_attempt_events
        ):
            raise FoundryCatalogError(
                "formal_build_attempt_closed",
                "Signed formal build receipt belongs to a closed attempt",
                status_code=409,
            )
    else:
        legacy_dispatch = next(
            (
                event
                for event in attempt_events
                if event.event_type == "FORMAL_BUILD_DISPATCHED"
                and not (event.event_payload or {}).get(
                    "formal_build_attempt_id"
                )
                and all(
                    (event.event_payload or {}).get(key) == value
                    for key, value in source_binding.items()
                )
                and (
                    event.created_at.replace(tzinfo=timezone.utc)
                    if event.created_at.tzinfo is None
                    else event.created_at.astimezone(timezone.utc)
                )
                <= built_at
            ),
            None,
        )
        if legacy_dispatch is None:
            raise FoundryCatalogError(
                "formal_build_legacy_dispatch_missing",
                "Legacy formal build receipt has no exact dispatch ledger entry",
                status_code=409,
            )
    row = FoundryFormalBuildAttestation(
        id=attestation_id,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        source_tree_hash=source_tree_hash,
        git_commit=git_commit,
        dependency_lock_hash=dependency_lock_hash,
        formal_sbom_hash=formal_sbom_hash,
        test_report_hash=_require_sha256(report["test_report_sha256"], "test_report_sha256"),
        formal_release_audit_hash=formal_release_audit_hash,
        formal_release_audit_receipts=formal_release_audit_receipts,
        formal_worker_image_digest=formal_worker_image_digest,
        github_repository=expected_repository,
        github_workflow_ref=expected_workflow_ref,
        github_workflow_sha=str(build_identity["github_workflow_sha"]),
        oidc_issuer=str(sigstore["oidc_issuer"]),
        oidc_subject=str(sigstore["certificate_identity"]),
        attestation_signing_key_id=key_id,
        sigstore_bundle_hash=_require_sha256(
            sigstore["bundle_sha256"], "sigstore.bundle_sha256"
        ),
        sigstore_verification_record_hash=_require_sha256(
            sigstore["verification_record_sha256"],
            "sigstore.verification_record_sha256",
        ),
        provenance_hash=_require_sha256(report["provenance_sha256"], "provenance_sha256"),
        build_metadata=build_metadata,
        receipt_hash=declared_payload_hash,
        attestation_artifact_hash=declared_artifact_hash,
        built_at=built_at,
    )
    db.add(row)
    await db.flush()
    await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="FORMAL_BUILD_ATTESTED",
        actor_kind="PROTECTED_BUILD_CALLBACK",
        actor_user_id=None,
        payload={
            "build_attestation_id": str(row.id),
            "receipt_sha256": row.receipt_hash,
            "attestation_artifact_sha256": row.attestation_artifact_hash,
            "attestation_signing_key_id": row.attestation_signing_key_id,
            "formal_worker_image_digest": row.formal_worker_image_digest,
            "git_commit": row.git_commit,
            **(
                {"formal_build_attempt_id": str(attempt_id)}
                if attempt_id is not None
                else {}
            ),
            "formal_release_audit_sha256": row.formal_release_audit_hash,
            **(
                {
                    "source_materialization_receipt_id": str(
                        materialization_receipt.id
                    ),
                    "source_materialization_receipt_sha256": (
                        materialization_receipt.receipt_hash
                    ),
                }
                if materialization_receipt is not None
                else {}
            ),
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


def _formal_build_source_binding(
    version: FoundryCandidateVersion,
) -> dict[str, str]:
    """Rebuild the protected-build inputs from a host-verified Draft receipt.

    The browser is intentionally unable to supply a commit, source hash, or
    candidate hash.  v1 can dispatch an unchanged, pinned Draft base commit
    (the common COMPOSITION route).  A non-empty AI patch remains a Candidate
    until a reviewed materialization process records a real Git commit for it.
    """

    generation = dict(version.ai_generation_config or {})
    source_commit = str(generation.get("source_base_commit") or "").lower()
    base_tree = str(generation.get("source_base_tree_sha256") or "").lower()
    source_tree = str(generation.get("source_tree_sha256") or "").lower()
    if (
        version.created_by_kind not in {"AI_DRAFT_JOB", "PROTECTED_MATERIALIZATION"}
        or generation.get("source_hash_algorithm")
        != "standard_astro_tracked_source_manifest_v1"
        or not _GIT_SHA_RE.fullmatch(source_commit)
        or not _SHA256_RE.fullmatch(base_tree)
        or not _SHA256_RE.fullmatch(source_tree)
        or source_tree != version.code_tree_hash
    ):
        raise FoundryCatalogError(
            "formal_source_binding_untrusted",
            "Formal build inputs require a host-verified Draft or materialization receipt",
            status_code=409,
        )
    if version.created_by_kind == "PROTECTED_MATERIALIZATION":
        origin_id = str(
            generation.get("source_materialized_from_candidate_version_id") or ""
        )
        receipt_id = str(generation.get("source_materialization_receipt_id") or "")
        module_path = str(generation.get("candidate_module_path") or "")
        if (
            generation.get("source_materialization_required") is not False
            or generation.get("source_materialized") is not True
            or base_tree != source_tree
            or source_tree != version.code_tree_hash
            or version.patch_hash == _EMPTY_SHA256
            or generation.get("source_patch_sha256") != version.patch_hash
            or not re.fullmatch(
                r"backend/app/services/foundry_generated/[a-z][a-z0-9_]{2,96}\.py",
                module_path,
            )
            or not _SHA256_RE.fullmatch(
                str(generation.get("candidate_module_sha256") or "")
            )
        ):
            raise FoundryCatalogError(
                "formal_materialization_binding_invalid",
                "Protected materialized source does not match its signed receipt binding",
                status_code=409,
            )
        try:
            uuid.UUID(origin_id)
            uuid.UUID(receipt_id)
        except ValueError as exc:
            raise FoundryCatalogError(
                "formal_materialization_binding_invalid",
                "Protected materialization origin identifiers are invalid",
                status_code=409,
            ) from exc
        return {
            "candidate_id": str(version.candidate_id),
            "candidate_version_id": str(version.id),
            "candidate_version_hash": version.version_hash,
            "source_commit": source_commit,
            "source_tree_sha256": source_tree,
        }
    if generation.get("source_materialization_required") is True:
        raise FoundryCatalogError(
            "formal_source_commit_not_materialized",
            "The approved Draft patch has no reviewed Git commit for formal build",
            status_code=409,
        )
    if (
        generation.get("source_materialization_required") is not False
        or base_tree != source_tree
        or version.patch_hash != _EMPTY_SHA256
    ):
        raise FoundryCatalogError(
            "formal_source_binding_untrusted",
            "An unchanged formal source binding must match the pinned Draft base",
            status_code=409,
        )
    return {
        "candidate_id": str(version.candidate_id),
        "candidate_version_id": str(version.id),
        "candidate_version_hash": version.version_hash,
        "source_commit": source_commit,
        "source_tree_sha256": source_tree,
    }


async def _validate_formal_source_provenance(
    db: AsyncSession,
    *,
    version: FoundryCandidateVersion,
    source_commit: str,
    source_tree_hash: str,
    dependency_lock_hash: str,
) -> Any | None:
    """Require the exact protected materialization receipt for generated code.

    A protected workflow signature proves who built a commit, not how an AI
    Draft reached that commit.  Generated patches therefore become formal only
    through the fresh ``PROTECTED_MATERIALIZATION`` Candidate version created
    by the append-only materialization receipt.  The callback and registration
    paths both call this gate so a manual workflow dispatch cannot manufacture
    the missing lineage from only a commit and tree hash.
    """

    if version.created_by_kind not in {
        "AI_DRAFT_JOB",
        "PROTECTED_MATERIALIZATION",
    }:
        # Legacy/non-generated fixtures do not claim an AI Draft source receipt.
        return None

    generation = dict(version.ai_generation_config or {})
    if (
        version.created_by_kind == "AI_DRAFT_JOB"
        and generation.get("source_materialization_required") is True
    ):
        raise FoundryCatalogError(
            "formal_materialization_receipt_required",
            "Generated source requires the exact Candidate version created by a protected materialization receipt",
            status_code=409,
        )

    binding = _formal_build_source_binding(version)
    if (
        binding["source_commit"] != source_commit
        or binding["source_tree_sha256"] != source_tree_hash
        or version.dependency_lock_hash != dependency_lock_hash
    ):
        raise FoundryCatalogError(
            "formal_source_materialization_binding_mismatch",
            "Formal build source does not match its server-verified source lineage",
            status_code=409,
        )
    if version.created_by_kind != "PROTECTED_MATERIALIZATION":
        return None

    from app.models.foundry_materialization_records import (
        FoundryMaterializationReceipt,
    )

    try:
        receipt_id = uuid.UUID(
            str(generation.get("source_materialization_receipt_id") or "")
        )
        origin_version_id = uuid.UUID(
            str(
                generation.get(
                    "source_materialized_from_candidate_version_id"
                )
                or ""
            )
        )
    except ValueError as exc:
        raise FoundryCatalogError(
            "formal_materialization_receipt_required",
            "Formal build requires the exact protected materialization receipt",
            status_code=409,
        ) from exc

    materialization = await db.get(FoundryMaterializationReceipt, receipt_id)
    origin_version = await db.get(FoundryCandidateVersion, origin_version_id)
    if (
        materialization is None
        or origin_version is None
        or origin_version.candidate_id != version.candidate_id
        or origin_version.version_hash
        != generation.get("source_materialized_from_candidate_version_hash")
        or origin_version.version_number >= version.version_number
        or materialization.candidate_id != version.candidate_id
        or materialization.materialized_candidate_version_id != version.id
        or materialization.origin_candidate_version_id != origin_version_id
        or materialization.origin_candidate_version_hash
        != generation.get("source_materialized_from_candidate_version_hash")
        or materialization.merge_commit != source_commit
        or materialization.merge_source_tree_hash != source_tree_hash
        or materialization.patch_hash != version.patch_hash
        or materialization.dependency_lock_hash != dependency_lock_hash
        or materialization.runner_definition_hash
        != version.candidate_bundle.get("runner_definition_sha256")
        or materialization.candidate_module_path
        != generation.get("candidate_module_path")
        or materialization.candidate_module_hash
        != generation.get("candidate_module_sha256")
        or materialization.validation_sbom_hash != version.sbom_hash
        or materialization.validation_runner_image_digest
        != version.validation_runner_image_digest
        or materialization.pull_request_base_ref != "main"
        or materialization.merge_commit_is_ancestor_of_origin_main is not True
    ):
        raise FoundryCatalogError(
            "formal_materialization_receipt_mismatch",
            "Formal source does not match the exact protected materialization receipt",
            status_code=409,
        )
    return materialization


async def request_formal_build_dispatch(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    candidate_version_id: uuid.UUID,
    candidate_version_hash: str,
    actor_user_id: uuid.UUID,
    now: datetime | None = None,
) -> FormalBuildDispatchPlan:
    """Reserve one bounded attempt or recover the currently active attempt."""

    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
    version = await db.get(FoundryCandidateVersion, candidate_version_id)
    expected_hash = _require_sha256(
        candidate_version_hash, "candidate_version_hash"
    )
    if (
        candidate is None
        or version is None
        or version.candidate_id != candidate_id
        or candidate.status != "APPROVED"
        or candidate.current_version_number != version.version_number
        or version.version_hash != expected_hash
    ):
        raise FoundryCatalogError(
            "formal_build_candidate_binding_mismatch",
            "Formal build requires the exact approved current candidate version",
            status_code=409,
        )
    passed_demo = await _strict_passed_demo_v1(db, version)
    reviews = list(
        (
            await db.execute(
                select(FoundryReview).where(
                    FoundryReview.candidate_version_id == version.id
                )
            )
        )
        .scalars()
        .all()
    )
    if passed_demo is None or not _review_requirements_satisfied(
        str(candidate.risk_level or ""), reviews
    ):
        raise FoundryCatalogError(
            "formal_build_approval_missing",
            "A PASSED Demo and required human approvals must precede formal build",
            status_code=409,
        )
    binding = _formal_build_source_binding(version)
    attestation = await db.scalar(
        select(FoundryFormalBuildAttestation)
        .where(
            FoundryFormalBuildAttestation.candidate_version_id == version.id,
            FoundryFormalBuildAttestation.candidate_version_hash
            == version.version_hash,
        )
        .order_by(FoundryFormalBuildAttestation.created_at.desc())
    )
    events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent)
                .where(
                    FoundryCandidateEvent.candidate_id == candidate.id,
                    FoundryCandidateEvent.candidate_version_id == version.id,
                    FoundryCandidateEvent.event_type.in_(
                        {
                            "FORMAL_BUILD_REQUESTED",
                            "FORMAL_BUILD_ATTEMPT_RESERVED",
                            "FORMAL_BUILD_DISPATCHED",
                            "FORMAL_BUILD_DISPATCH_UNCERTAIN",
                            "FORMAL_BUILD_DISPATCH_FAILED",
                            "FORMAL_BUILD_ATTEMPT_FAILED",
                            "FORMAL_BUILD_DISPATCH_RESERVATION_TIMED_OUT",
                            "FORMAL_BUILD_DISPATCH_UNCERTAIN_TIMED_OUT",
                            "FORMAL_BUILD_ATTEMPT_TIMED_OUT",
                        }
                    ),
                )
                .order_by(
                    FoundryCandidateEvent.created_at.asc(),
                    FoundryCandidateEvent.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    matching = [
        event
        for event in events
        if all(
            (event.event_payload or {}).get(key) == value
            for key, value in binding.items()
        )
    ]
    requested = next(
        (event for event in matching if event.event_type == "FORMAL_BUILD_REQUESTED"),
        None,
    )
    if requested is None:
        requested = await _append_event(
            db,
            candidate_id=candidate.id,
            candidate_version_id=version.id,
            event_type="FORMAL_BUILD_REQUESTED",
            actor_kind="HUMAN_REVIEWER",
            actor_user_id=actor_user_id,
            payload={**binding, "source_binding_origin": "SERVER_DRAFT_RECEIPT"},
        )

    reservations = [
        event
        for event in matching
        if event.event_type == "FORMAL_BUILD_ATTEMPT_RESERVED"
        and str((event.event_payload or {}).get("formal_build_attempt_id") or "")
        and isinstance((event.event_payload or {}).get("attempt_number"), int)
    ]
    attempt_event: FoundryCandidateEvent | None = None
    attempt_id: uuid.UUID | None = None
    attempt_number = 0
    terminal = False
    terminal_event: FoundryCandidateEvent | None = None
    active_anchor: FoundryCandidateEvent | None = None
    active_status = "DISPATCH_PENDING"
    active_lease = FORMAL_BUILD_DISPATCH_LEASE
    timeout_event_type = "FORMAL_BUILD_DISPATCH_RESERVATION_TIMED_OUT"
    if reservations:
        attempt_event = max(
            reservations,
            key=lambda event: int((event.event_payload or {})["attempt_number"]),
        )
        try:
            attempt_id = uuid.UUID(
                str((attempt_event.event_payload or {})["formal_build_attempt_id"])
            )
        except ValueError as exc:
            raise FoundryCatalogError(
                "formal_build_attempt_ledger_invalid",
                "Formal build attempt ledger contains an invalid identifier",
                status_code=409,
            ) from exc
        attempt_number = int((attempt_event.event_payload or {})["attempt_number"])
        attempt_outcomes = [
            event
            for event in matching
            if (event.event_payload or {}).get("formal_build_attempt_id")
            == str(attempt_id)
        ]
        terminal_event = next(
            (
                event
                for event in reversed(attempt_outcomes)
                if event.event_type
                in {
                    "FORMAL_BUILD_DISPATCH_FAILED",
                    "FORMAL_BUILD_ATTEMPT_FAILED",
                    "FORMAL_BUILD_DISPATCH_RESERVATION_TIMED_OUT",
                    "FORMAL_BUILD_DISPATCH_UNCERTAIN_TIMED_OUT",
                    "FORMAL_BUILD_ATTEMPT_TIMED_OUT",
                }
            ),
            None,
        )
        terminal = terminal_event is not None
        delivery_event = next(
            (
                event
                for event in reversed(attempt_outcomes)
                if event.event_type
                in {"FORMAL_BUILD_DISPATCHED", "FORMAL_BUILD_DISPATCH_UNCERTAIN"}
            ),
            None,
        )
        active_anchor = delivery_event or attempt_event
        if delivery_event is not None:
            if delivery_event.event_type == "FORMAL_BUILD_DISPATCHED":
                active_status = "DISPATCHED"
                active_lease = FORMAL_BUILD_ATTEMPT_TIMEOUT
                timeout_event_type = "FORMAL_BUILD_ATTEMPT_TIMED_OUT"
            else:
                active_status = "DISPATCH_UNCERTAIN"
                active_lease = FORMAL_BUILD_DISPATCH_LEASE
                timeout_event_type = "FORMAL_BUILD_DISPATCH_UNCERTAIN_TIMED_OUT"
    else:
        # A pre-attempt-ledger dispatch remains active until the same timeout;
        # this avoids launching a duplicate immediately after an upgrade.
        legacy = next(
            (
                event
                for event in reversed(matching)
                if event.event_type
                in {"FORMAL_BUILD_DISPATCHED", "FORMAL_BUILD_DISPATCH_FAILED"}
                and not (event.event_payload or {}).get("formal_build_attempt_id")
            ),
            None,
        )
        if legacy is not None:
            attempt_event = legacy
            attempt_id = legacy.id
            attempt_number = 1
            terminal = legacy.event_type == "FORMAL_BUILD_DISPATCH_FAILED"
            terminal_event = legacy if terminal else None
            active_anchor = legacy
            active_status = (
                "DISPATCHED"
                if legacy.event_type == "FORMAL_BUILD_DISPATCHED"
                else "DISPATCH_FAILED"
            )
            active_lease = FORMAL_BUILD_ATTEMPT_TIMEOUT
            timeout_event_type = "FORMAL_BUILD_ATTEMPT_TIMED_OUT"

    if attestation is not None:
        await db.commit()
        return FormalBuildDispatchPlan(
            binding=binding,
            request_event=requested,
            attempt_event=attempt_event,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            already_active=False,
            dispatch_status="VERIFIED_BUILD_RECEIPT",
            retry_after=None,
            attestation=attestation,
        )

    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    if attempt_event is not None and not terminal and active_anchor is not None:
        active_since = active_anchor.created_at
        if active_since.tzinfo is None:
            active_since = active_since.replace(tzinfo=timezone.utc)
        retry_after = active_since + active_lease
        if current_time < retry_after:
            await db.commit()
            return FormalBuildDispatchPlan(
                binding=binding,
                request_event=requested,
                attempt_event=attempt_event,
                attempt_id=attempt_id,
                attempt_number=attempt_number,
                already_active=True,
                dispatch_status=active_status,
                retry_after=retry_after,
                attestation=None,
            )
        terminal_event = await _append_event(
            db,
            candidate_id=candidate.id,
            candidate_version_id=version.id,
            event_type=timeout_event_type,
            actor_kind="CONTROL_PLANE",
            actor_user_id=None,
            payload={
                **binding,
                "formal_build_attempt_id": str(attempt_id),
                "attempt_number": attempt_number,
                "timeout_phase": active_status,
                "timeout_seconds": int(active_lease.total_seconds()),
                "retryable": attempt_number < FORMAL_BUILD_MAX_ATTEMPTS,
            },
        )
        terminal = True

    if attempt_number >= FORMAL_BUILD_MAX_ATTEMPTS:
        await db.commit()
        raise FoundryCatalogError(
            "formal_build_attempts_exhausted",
            "Formal build reached the bounded attempt limit",
            status_code=409,
        )
    if (
        terminal_event is not None
        and terminal_event.event_type == "FORMAL_BUILD_DISPATCH_FAILED"
        and "retryable" in (terminal_event.event_payload or {})
        and not bool((terminal_event.event_payload or {}).get("retryable"))
    ):
        await db.commit()
        raise FoundryCatalogError(
            "formal_build_dispatch_not_retryable",
            "Formal build dispatch failed permanently",
            status_code=409,
        )

    attempt_id = uuid.uuid4()
    attempt_number += 1
    attempt_event = await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="FORMAL_BUILD_ATTEMPT_RESERVED",
        actor_kind="HUMAN_REVIEWER",
        actor_user_id=actor_user_id,
        payload={
            **binding,
            "formal_build_attempt_id": str(attempt_id),
            "attempt_number": attempt_number,
            "max_attempts": FORMAL_BUILD_MAX_ATTEMPTS,
            "dispatch_lease_seconds": int(
                FORMAL_BUILD_DISPATCH_LEASE.total_seconds()
            ),
            "workflow_lease_seconds": int(
                FORMAL_BUILD_ATTEMPT_TIMEOUT.total_seconds()
            ),
        },
    )
    await db.commit()
    await db.refresh(requested)
    await db.refresh(attempt_event)
    return FormalBuildDispatchPlan(
        binding=binding,
        request_event=requested,
        attempt_event=attempt_event,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        already_active=False,
        dispatch_status="DISPATCH_PENDING",
        retry_after=(
            attempt_event.created_at.replace(tzinfo=timezone.utc)
            if attempt_event.created_at.tzinfo is None
            else attempt_event.created_at.astimezone(timezone.utc)
        )
        + FORMAL_BUILD_DISPATCH_LEASE,
        attestation=None,
    )


async def record_formal_build_dispatch(
    db: AsyncSession,
    *,
    binding: dict[str, str],
    attempt_id: uuid.UUID,
    dispatched: bool,
    failure_class: str | None = None,
    retryable: bool = False,
    delivery_uncertain: bool = False,
) -> tuple[FoundryCandidateEvent, bool]:
    """Append a secret-free, idempotent protected-build dispatch outcome."""

    try:
        candidate_id = uuid.UUID(binding["candidate_id"])
        version_id = uuid.UUID(binding["candidate_version_id"])
    except (KeyError, ValueError) as exc:
        raise FoundryCatalogError(
            "formal_build_dispatch_binding_invalid",
            "Formal build dispatch identifiers are invalid",
            status_code=409,
        ) from exc
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
    version = await db.get(FoundryCandidateVersion, version_id)
    if (
        candidate is None
        or version is None
        or version.candidate_id != candidate_id
        or _formal_build_source_binding(version) != binding
    ):
        raise FoundryCatalogError(
            "formal_build_dispatch_binding_invalid",
            "Formal build dispatch no longer matches its immutable source binding",
            status_code=409,
        )
    events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate_id,
                    FoundryCandidateEvent.candidate_version_id == version_id,
                    FoundryCandidateEvent.event_type.in_(
                        {
                            "FORMAL_BUILD_ATTEMPT_RESERVED",
                            "FORMAL_BUILD_DISPATCHED",
                            "FORMAL_BUILD_DISPATCH_UNCERTAIN",
                            "FORMAL_BUILD_DISPATCH_FAILED",
                            "FORMAL_BUILD_ATTEMPT_FAILED",
                            "FORMAL_BUILD_DISPATCH_RESERVATION_TIMED_OUT",
                            "FORMAL_BUILD_DISPATCH_UNCERTAIN_TIMED_OUT",
                            "FORMAL_BUILD_ATTEMPT_TIMED_OUT",
                        }
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    reservation = next(
        (
            event
            for event in events
            if event.event_type == "FORMAL_BUILD_ATTEMPT_RESERVED"
            and (event.event_payload or {}).get("formal_build_attempt_id")
            == str(attempt_id)
            and all(
                (event.event_payload or {}).get(key) == value
                for key, value in binding.items()
            )
        ),
        None,
    )
    if reservation is None:
        raise FoundryCatalogError(
            "formal_build_attempt_missing",
            "Formal build dispatch has no durable attempt reservation",
            status_code=409,
        )
    attempt_events = [
        event
        for event in events
        if (event.event_payload or {}).get("formal_build_attempt_id")
        == str(attempt_id)
        and all(
            (event.event_payload or {}).get(key) == value
            for key, value in binding.items()
        )
    ]
    if dispatched and delivery_uncertain:
        raise FoundryCatalogError(
            "formal_build_dispatch_result_invalid",
            "A successful dispatch cannot have uncertain delivery",
            status_code=409,
        )
    outcome_type = (
        "FORMAL_BUILD_DISPATCHED"
        if dispatched
        else (
            "FORMAL_BUILD_DISPATCH_UNCERTAIN"
            if delivery_uncertain
            else "FORMAL_BUILD_DISPATCH_FAILED"
        )
    )
    existing = next(
        (
            event for event in attempt_events if event.event_type == outcome_type
        ),
        None,
    )
    sanitized_failure = None
    if not dispatched:
        sanitized_failure = str(failure_class or "formal_build_dispatch_failed")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,127}", sanitized_failure):
            sanitized_failure = "formal_build_dispatch_failed"
    if existing is not None:
        if not dispatched and (existing.event_payload or {}).get(
            "failure_class"
        ) != sanitized_failure:
            raise FoundryCatalogError(
                "formal_build_dispatch_result_conflict",
                "Formal build attempt already has a different dispatch result",
                status_code=409,
            )
        return existing, False
    if any(
        event.event_type
        in {
            "FORMAL_BUILD_ATTEMPT_FAILED",
            "FORMAL_BUILD_DISPATCH_RESERVATION_TIMED_OUT",
            "FORMAL_BUILD_DISPATCH_UNCERTAIN_TIMED_OUT",
            "FORMAL_BUILD_ATTEMPT_TIMED_OUT",
        }
        for event in attempt_events
    ):
        raise FoundryCatalogError(
            "formal_build_attempt_closed",
            "Formal build dispatch attempt is already terminal",
            status_code=409,
        )
    prior_dispatched = next(
        (
            event
            for event in attempt_events
            if event.event_type == "FORMAL_BUILD_DISPATCHED"
        ),
        None,
    )
    prior_failure = next(
        (
            event
            for event in attempt_events
            if event.event_type == "FORMAL_BUILD_DISPATCH_FAILED"
        ),
        None,
    )
    if prior_failure is not None or (
        prior_dispatched is not None and outcome_type != "FORMAL_BUILD_DISPATCHED"
    ):
        raise FoundryCatalogError(
            "formal_build_dispatch_result_conflict",
            "Formal build attempt already has a conflicting dispatch result",
            status_code=409,
        )
    event = await _append_event(
        db,
        candidate_id=candidate_id,
        candidate_version_id=version_id,
        event_type=outcome_type,
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload={
            **binding,
            "formal_build_attempt_id": str(attempt_id),
            "attempt_number": int((reservation.event_payload or {})["attempt_number"]),
            "failure_class": sanitized_failure,
            "delivery_uncertain": bool(delivery_uncertain),
            "dispatch_lease_seconds": (
                int(FORMAL_BUILD_DISPATCH_LEASE.total_seconds())
                if delivery_uncertain
                else None
            ),
            "workflow_lease_seconds": (
                int(FORMAL_BUILD_ATTEMPT_TIMEOUT.total_seconds())
                if dispatched
                else None
            ),
            "failure_retryable": bool(retryable) if not dispatched else False,
            "retryable": (
                bool(retryable)
                and int((reservation.event_payload or {})["attempt_number"])
                < FORMAL_BUILD_MAX_ATTEMPTS
                if not dispatched
                else False
            ),
        },
    )
    await db.commit()
    await db.refresh(event)
    return event, True


async def record_formal_build_attempt_failure(
    db: AsyncSession,
    *,
    report: dict[str, Any],
    expected_repository: str,
    expected_workflow_ref: str,
) -> tuple[FoundryCandidateEvent, bool]:
    """Append one idempotent protected-workflow failure for an exact attempt."""

    try:
        attempt_id = uuid.UUID(str(report["formal_build_attempt_id"]))
        candidate_id = uuid.UUID(str(report["candidate_id"]))
        version_id = uuid.UUID(str(report["candidate_version_id"]))
    except (KeyError, ValueError) as exc:
        raise FoundryCatalogError(
            "formal_build_failure_binding_invalid",
            "Formal build failure identifiers are invalid",
            status_code=409,
        ) from exc
    binding = {
        "candidate_id": str(candidate_id),
        "candidate_version_id": str(version_id),
        "candidate_version_hash": str(report.get("candidate_version_hash") or ""),
        "source_commit": str(report.get("source_commit") or ""),
        "source_tree_sha256": str(report.get("source_tree_sha256") or ""),
    }
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
    version = await db.get(FoundryCandidateVersion, version_id)
    if (
        candidate is None
        or version is None
        or version.candidate_id != candidate_id
        or _formal_build_source_binding(version) != binding
        or report.get("status") != "FAILED"
        or report.get("failure_class") != "formal_build_workflow_failed"
        or report.get("failed_stage")
        not in {"definition_gate", "validation", "image_build", "signing"}
        or report.get("workflow_conclusion")
        not in {"failure", "cancelled", "skipped"}
        or report.get("github_repository") != expected_repository
        or report.get("github_workflow_ref") != expected_workflow_ref
        or not _GIT_SHA_RE.fullmatch(str(report.get("github_workflow_sha") or ""))
        or not re.fullmatch(
            r"[1-9][0-9]{0,19}", str(report.get("github_run_id") or "")
        )
        or str(report.get("github_run_attempt") or "") != "1"
    ):
        raise FoundryCatalogError(
            "formal_build_failure_binding_invalid",
            "Formal build failure does not match its protected attempt binding",
            status_code=409,
        )
    events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate_id,
                    FoundryCandidateEvent.candidate_version_id == version_id,
                    FoundryCandidateEvent.event_type.in_(
                        {
                            "FORMAL_BUILD_ATTEMPT_RESERVED",
                            "FORMAL_BUILD_DISPATCHED",
                            "FORMAL_BUILD_DISPATCH_UNCERTAIN",
                            "FORMAL_BUILD_ATTEMPT_FAILED",
                            "FORMAL_BUILD_DISPATCH_FAILED",
                            "FORMAL_BUILD_DISPATCH_RESERVATION_TIMED_OUT",
                            "FORMAL_BUILD_DISPATCH_UNCERTAIN_TIMED_OUT",
                            "FORMAL_BUILD_ATTEMPT_TIMED_OUT",
                        }
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    reservation = next(
        (
            event
            for event in events
            if event.event_type == "FORMAL_BUILD_ATTEMPT_RESERVED"
            and (event.event_payload or {}).get("formal_build_attempt_id")
            == str(attempt_id)
            and all(
                (event.event_payload or {}).get(key) == value
                for key, value in binding.items()
            )
        ),
        None,
    )
    if reservation is None:
        raise FoundryCatalogError(
            "formal_build_attempt_missing",
            "Formal build failure has no durable attempt reservation",
            status_code=409,
        )
    report_hash = sha256_json(report)
    existing = next(
        (
            event
            for event in events
            if event.event_type == "FORMAL_BUILD_ATTEMPT_FAILED"
            and (event.event_payload or {}).get("formal_build_attempt_id")
            == str(attempt_id)
        ),
        None,
    )
    if existing is not None:
        if (existing.event_payload or {}).get("failure_report_sha256") != report_hash:
            raise FoundryCatalogError(
                "formal_build_failure_result_conflict",
                "Formal build attempt already has a different failure result",
                status_code=409,
            )
        return existing, False
    if await db.scalar(
        select(FoundryFormalBuildAttestation.id).where(
            FoundryFormalBuildAttestation.candidate_version_id == version_id,
            FoundryFormalBuildAttestation.candidate_version_hash
            == version.version_hash,
        )
    ):
        raise FoundryCatalogError(
            "formal_build_attempt_already_succeeded",
            "A verified formal build receipt already exists",
            status_code=409,
        )
    if any(
        event.event_type
        in {
            "FORMAL_BUILD_DISPATCH_FAILED",
            "FORMAL_BUILD_DISPATCH_RESERVATION_TIMED_OUT",
            "FORMAL_BUILD_DISPATCH_UNCERTAIN_TIMED_OUT",
            "FORMAL_BUILD_ATTEMPT_TIMED_OUT",
        }
        and (event.event_payload or {}).get("formal_build_attempt_id")
        == str(attempt_id)
        for event in events
    ):
        raise FoundryCatalogError(
            "formal_build_attempt_closed",
            "Formal build attempt is already terminal",
            status_code=409,
        )
    if not any(
        event.event_type
        in {"FORMAL_BUILD_DISPATCHED", "FORMAL_BUILD_DISPATCH_UNCERTAIN"}
        and (event.event_payload or {}).get("formal_build_attempt_id")
        == str(attempt_id)
        for event in events
    ):
        raise FoundryCatalogError(
            "formal_build_attempt_not_dispatched",
            "Formal build failure has no successful dispatch record",
            status_code=409,
        )
    event = await _append_event(
        db,
        candidate_id=candidate_id,
        candidate_version_id=version_id,
        event_type="FORMAL_BUILD_ATTEMPT_FAILED",
        actor_kind="PROTECTED_BUILD_CALLBACK",
        actor_user_id=None,
        payload={
            **binding,
            "formal_build_attempt_id": str(attempt_id),
            "attempt_number": int((reservation.event_payload or {})["attempt_number"]),
            "failure_class": "formal_build_workflow_failed",
            "failed_stage": report["failed_stage"],
            "workflow_conclusion": report["workflow_conclusion"],
            "github_repository": report["github_repository"],
            "github_workflow_ref": report["github_workflow_ref"],
            "github_workflow_sha": report["github_workflow_sha"],
            "github_run_id": str(report["github_run_id"]),
            "github_run_attempt": str(report["github_run_attempt"]),
            "failure_report_sha256": report_hash,
            "retryable": (
                int((reservation.event_payload or {})["attempt_number"])
                < FORMAL_BUILD_MAX_ATTEMPTS
            ),
        },
    )
    await db.commit()
    await db.refresh(event)
    return event, True


async def record_validation_result(
    db: AsyncSession,
    *,
    validation_run_id: uuid.UUID,
    status: str,
    runner_image_digest: str,
    stdout_sha256: str,
    stderr_sha256: str,
    structured_result: dict[str, Any],
    limitations: list[str],
    validation_summary: dict[str, Any],
    failure_class: str | None,
    resource_usage: dict[str, Any],
    artifact_manifest: list[dict[str, Any]],
    started_at: datetime,
    completed_at: datetime,
) -> FoundryDemoRun:
    """Complete a queued validation and append its non-formal Demo receipt.

    This function is deliberately not exposed as a browser API. The isolated
    validation runner integration calls it after authenticating the runner's
    own result channel.
    """

    raise FoundryCatalogError(
        "legacy_validation_result_disabled",
        "Legacy validation receipts cannot satisfy the immutable DemoReport v1 contract",
        status_code=410,
    )

    status = status.upper()
    if status not in DEMO_STATUSES:
        raise FoundryCatalogError("invalid_demo_status", "Unsupported Demo status")
    if completed_at < started_at:
        raise FoundryCatalogError("invalid_demo_time", "Demo completion precedes its start")
    if contains_formal_claim_escape(
        {
            "artifact_manifest": artifact_manifest,
            "failure_class": failure_class,
            "limitations": limitations,
            "resource_usage": resource_usage,
            "result": structured_result,
            "validation_summary": validation_summary,
        },
        scan_text_leaves=True,
    ):
        raise FoundryCatalogError(
            "candidate_formal_claim_forbidden",
            "A candidate Demo cannot contain a formal verdict or Evidence Pack",
        )
    initial = await db.get(FoundryValidationRun, validation_run_id)
    if initial is None:
        raise FoundryCatalogError(
            "validation_run_not_found", "Validation run not found", status_code=404
        )
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == initial.candidate_id)
        .with_for_update()
    )
    run = await db.scalar(
        select(FoundryValidationRun)
        .where(FoundryValidationRun.id == validation_run_id)
        .with_for_update()
    )
    if run is None or candidate is None:
        raise FoundryCatalogError("validation_run_not_found", "Validation run not found", status_code=404)
    if run.status not in {"OUTCOME_UNKNOWN", "DISPATCHED", "RUNNING"}:
        existing = await db.scalar(
            select(FoundryDemoRun).where(FoundryDemoRun.validation_run_id == run.id)
        )
        if existing is not None:
            if not (
                existing.status == status
                and existing.runner_image_digest == runner_image_digest
                and existing.stdout_sha256 == stdout_sha256
                and existing.stderr_sha256 == stderr_sha256
                and existing.structured_result == structured_result
                and existing.limitations == limitations
                and existing.validation_summary == validation_summary
                and existing.failure_class == failure_class
                and existing.resource_usage == resource_usage
                and existing.artifact_manifest == artifact_manifest
                and existing.started_at == started_at
                and existing.completed_at == completed_at
            ):
                raise FoundryCatalogError(
                    "validation_result_conflict",
                    "Validation attempt already has a different Demo result",
                    status_code=409,
                )
            return existing
        raise FoundryCatalogError(
            "validation_attempt_closed", "Validation attempt is not active or is already terminal", status_code=409
        )
    version = await db.get(FoundryCandidateVersion, run.candidate_version_id)
    if version is None or version.version_hash != run.candidate_version_hash:
        raise FoundryCatalogError(
            "candidate_version_binding_mismatch",
            "Validation result no longer matches its immutable candidate version",
            status_code=409,
        )
    image_digest = _require_image_digest(runner_image_digest)
    if image_digest != version.validation_runner_image_digest:
        raise FoundryCatalogError(
            "runner_image_binding_mismatch",
            "Demo runner image does not match the candidate version",
            status_code=409,
        )
    run.status = status
    run.runner_image_digest = image_digest
    run.validation_summary = {
        **dict(run.validation_summary or {}),
        "phase": status,
        "retryable": False,
        "retry_after": None,
        "demo_validation": validation_summary,
    }
    run.failure_class = failure_class
    run.started_at = started_at
    run.completed_at = completed_at
    demo_id = uuid.uuid4()
    duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))
    demo_report_hash = sha256_json(
        {
            "demo_run_id": str(demo_id),
            "candidate_version_hash": version.version_hash,
            "status": status,
            "runner_image_digest": image_digest,
            "structured_result": structured_result,
            "validation_summary": validation_summary,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
        }
    )
    demo = FoundryDemoRun(
        id=demo_id,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_key=version.candidate_key,
        validation_run_id=run.id,
        status=status,
        evidence_class=NON_FORMAL_EVIDENCE_CLASS,
        publication_ready=False,
        claim_eligible=False,
        evidence_pack_allowed=False,
        candidate_version_hash=version.version_hash,
        candidate_bundle_hash=version.version_hash,
        workflow_spec_hash=version.workflow_spec_hash,
        code_tree_hash=version.code_tree_hash,
        dependency_lock_hash=version.dependency_lock_hash,
        runner_definition_hash=str(
            version.candidate_bundle.get("runner_definition_sha256")
        ),
        sbom_hash=version.sbom_hash,
        fixture_hashes=version.fixture_hashes,
        data_hashes=version.data_hashes,
        ai_model=version.ai_model,
        ai_generation_config=version.ai_generation_config,
        generation=dict(version.candidate_bundle.get("generation") or {}),
        source_pins=list(version.candidate_bundle.get("source_pins") or []),
        runner_image_digest=image_digest,
        environment={},
        environment_sha256=sha256_json({}),
        stdout_sha256=_require_sha256(stdout_sha256, "stdout_sha256"),
        stderr_sha256=_require_sha256(stderr_sha256, "stderr_sha256"),
        stdout_bytes=0,
        stderr_bytes=0,
        structured_result=structured_result,
        limitations=[str(item) for item in limitations],
        validation_summary=validation_summary,
        failure_class=failure_class,
        resource_usage=resource_usage,
        artifact_manifest=artifact_manifest,
        duration_ms=duration_ms,
        demo_report_hash=demo_report_hash,
        started_at=started_at,
        completed_at=completed_at,
    )
    db.add(demo)
    await db.flush()
    if (
        candidate.current_version_number == version.version_number
        and candidate.status == "VALIDATING"
    ):
        candidate.status = "DEMO_RECORDED"
    await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="DEMO_RECORDED",
        actor_kind="VALIDATION_RUNNER",
        actor_user_id=None,
        payload={
            "demo_run_id": str(demo.id),
            "validation_run_id": str(run.id),
            "status": status,
            "evidence_class": NON_FORMAL_EVIDENCE_CLASS,
            "publication_ready": False,
            "claim_eligible": False,
        },
    )
    await db.commit()
    await db.refresh(demo)
    return demo


def _reviewer_pseudonym(candidate_id: uuid.UUID, reviewer_user_id: uuid.UUID) -> str:
    digest = hashlib.sha256(
        f"foundry-reviewer:{candidate_id}:{reviewer_user_id}".encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _review_requirements_satisfied(
    risk_level: str,
    reviews: Iterable[FoundryReview],
) -> bool:
    latest: dict[tuple[uuid.UUID | None, str], FoundryReview] = {}
    for review in sorted(reviews, key=lambda row: (row.created_at, str(row.id))):
        latest[(review.reviewer_user_id, review.review_scope)] = review
    approved = [row for row in latest.values() if row.decision == "APPROVED"]
    if risk_level in {"R0", "R1"}:
        return bool(approved)
    if risk_level == "R2":
        return any(row.review_scope == "SCIENTIFIC" for row in approved)
    if risk_level == "R3":
        engineering = {
            row.reviewer_user_id
            for row in approved
            if row.review_scope == "ENGINEERING" and row.reviewer_user_id is not None
        }
        scientific = {
            row.reviewer_user_id
            for row in approved
            if row.review_scope == "SCIENTIFIC" and row.reviewer_user_id is not None
        }
        return any(engineer != scientist for engineer in engineering for scientist in scientific)
    return False


async def review_candidate_version(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    candidate_version_id: uuid.UUID,
    candidate_version_hash: str,
    reviewer_user_id: uuid.UUID,
    review_scope: str,
    decision: str,
    comment: str,
) -> FoundryReview:
    review_scope = review_scope.upper()
    decision = decision.upper()
    if review_scope not in REVIEW_SCOPES:
        raise FoundryCatalogError("invalid_review_scope", "Unsupported Foundry review scope")
    if decision not in REVIEW_DECISIONS:
        raise FoundryCatalogError("invalid_review_decision", "Unsupported Foundry review decision")
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
    version = await db.get(FoundryCandidateVersion, candidate_version_id)
    if candidate is None or version is None or version.candidate_id != candidate_id:
        raise FoundryCatalogError("candidate_version_not_found", "Candidate version not found", status_code=404)
    if (
        candidate.current_version_number != version.version_number
        or version.version_hash != candidate_version_hash
    ):
        raise FoundryCatalogError(
            "candidate_version_binding_mismatch",
            "Review must bind the exact current candidate version hash",
            status_code=409,
        )
    if candidate.status in {
        "APPROVED",
        "REJECTED",
        "PROMOTED",
        "SUSPENDED",
        "SUPERSEDED",
        "REVOKED",
    }:
        raise FoundryCatalogError(
            "candidate_version_review_closed",
            "This exact candidate version has a terminal review decision; create a new version",
            status_code=409,
        )
    active_validation = await db.scalar(
        select(FoundryValidationRun.id).where(
            FoundryValidationRun.candidate_version_id == version.id,
            FoundryValidationRun.candidate_version_hash == version.version_hash,
            FoundryValidationRun.status.in_(
                {"QUEUED", "OUTCOME_UNKNOWN", "DISPATCHED", "RUNNING"}
            ),
        )
    )
    if active_validation is not None:
        raise FoundryCatalogError(
            "candidate_validation_active",
            "Human review must wait for the active validation attempt to finish",
            status_code=409,
        )
    if version.created_by_user_id == reviewer_user_id:
        raise FoundryCatalogError(
            "candidate_self_review_forbidden",
            "A candidate author cannot approve the same candidate version",
            status_code=403,
        )
    passed_demo = await _strict_passed_demo_v1(db, version)
    if passed_demo is None:
        raise FoundryCatalogError(
            "passed_demo_required",
            "An exact-version PASSED Demo is required before human review",
            status_code=409,
        )
    review = FoundryReview(
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        reviewer_user_id=reviewer_user_id,
        reviewer_pseudonym=_reviewer_pseudonym(candidate.id, reviewer_user_id),
        review_scope=review_scope,
        decision=decision,
        comment=comment,
    )
    db.add(review)
    await db.flush()
    reviews = list(
        (
            await db.execute(
                select(FoundryReview).where(
                    FoundryReview.candidate_version_id == version.id
                )
            )
        )
        .scalars()
        .all()
    )
    if decision in {"REJECTED", "CHANGES_REQUESTED"}:
        candidate.status = "REJECTED"
    elif _review_requirements_satisfied(str(candidate.risk_level or ""), reviews):
        candidate.status = "APPROVED"
    else:
        candidate.status = "REVIEW_PENDING"
    await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="HUMAN_REVIEW_RECORDED",
        actor_kind="HUMAN_REVIEWER",
        actor_user_id=reviewer_user_id,
        payload={
            "review_id": str(review.id),
            "review_scope": review_scope,
            "decision": decision,
            "candidate_status": candidate.status,
        },
    )
    await db.commit()
    await db.refresh(review)
    return review


def _registry_error(exc: Exception) -> FoundryCatalogError:
    return FoundryCatalogError(
        str(getattr(exc, "code", None) or "workflow_registry_unavailable"),
        "Formal workflow registry validation failed",
        status_code=int(getattr(exc, "status_code", 503) or 503),
    )


def _review_records_for_registry(reviews: Iterable[FoundryReview]) -> list[dict[str, Any]]:
    return [
        {
            "review_id": str(review.id),
            # Public release requests keep a stable candidate-local pseudonym,
            # never the account UUID stored in the private review ledger.
            "reviewer_id": review.reviewer_pseudonym,
            "reviewer_type": "human",
            "review_role": review.review_scope.lower(),
            "decision": review.decision,
            "candidate_version_hash": review.candidate_version_hash,
        }
        for review in reviews
    ]


_REGISTRY_RELEASE_REQUEST_FIELDS = frozenset(
    {
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
)
_SHA256_TAGGED_RE = re.compile(r"sha256:[0-9a-f]{64}")


def _validate_registry_release_chain(
    rows: Iterable[WorkflowRegistryRelease],
    *,
    base_epoch: str,
    base_hash: str,
) -> list[WorkflowRegistryRelease]:
    """Validate and order the single append-only cumulative request chain."""

    by_hash: dict[str, WorkflowRegistryRelease] = {}
    manifests: dict[str, dict[str, Any]] = {}
    for row in rows:
        manifest = dict(row.manifest or {})
        if (
            set(manifest) != _REGISTRY_RELEASE_REQUEST_FIELDS
            or manifest.get("schema_version")
            != "standard_astro_registry_release_request_v1"
            or manifest.get("request_id") != str(row.id)
            or manifest.get("request_epoch") != row.epoch
            or manifest.get("request_status") != "PENDING_SIGNATURE"
            or row.status != "PENDING_SIGNATURE"
            or row.signature is not None
            or row.key_id is not None
            or row.public_key_fingerprint is not None
            or manifest.get("runtime_registry_modified") is not False
            or manifest.get("signature_required") is not True
            or manifest.get("base_registry_epoch") != base_epoch
            or manifest.get("base_registry_hash") != base_hash
            or not _SHA256_TAGGED_RE.fullmatch(
                str(manifest.get("requested_by_actor_hash") or "")
            )
            or not isinstance(manifest.get("new_operations"), list)
            or not manifest["new_operations"]
            or not isinstance(manifest.get("operation_sequence"), list)
            or not manifest["operation_sequence"]
            or manifest.get("operation_sequence_hash")
            != "sha256:" + sha256_json(manifest["operation_sequence"])
            or row.manifest_hash != "sha256:" + sha256_json(manifest)
        ):
            raise FoundryCatalogError(
                "registry_release_chain_invalid",
                "The immutable Registry release-request chain is invalid",
                status_code=409,
            )
        if row.manifest_hash in by_hash:
            raise FoundryCatalogError(
                "registry_release_chain_duplicate",
                "The Registry release-request chain contains a duplicate hash",
                status_code=409,
            )
        predecessor = manifest.get("previous_request_hash")
        if predecessor is not None and not _SHA256_TAGGED_RE.fullmatch(
            str(predecessor)
        ):
            raise FoundryCatalogError(
                "registry_release_chain_invalid",
                "The Registry release-request predecessor is invalid",
                status_code=409,
            )
        by_hash[row.manifest_hash] = row
        manifests[row.manifest_hash] = manifest

    if not by_hash:
        return []
    roots = [
        request_hash
        for request_hash, manifest in manifests.items()
        if manifest.get("previous_request_hash") is None
    ]
    if len(roots) != 1:
        raise FoundryCatalogError(
            "registry_release_chain_forked",
            "The Registry release-request chain must have exactly one root",
            status_code=409,
        )
    children: dict[str, list[str]] = {}
    for request_hash, manifest in manifests.items():
        predecessor = manifest.get("previous_request_hash")
        if predecessor is None:
            continue
        if predecessor not in by_hash:
            raise FoundryCatalogError(
                "registry_release_chain_predecessor_missing",
                "A Registry release-request predecessor is missing",
                status_code=409,
            )
        children.setdefault(str(predecessor), []).append(request_hash)
    if any(len(items) != 1 for items in children.values()):
        raise FoundryCatalogError(
            "registry_release_chain_forked",
            "The Registry release-request chain has more than one child",
            status_code=409,
        )

    ordered: list[WorkflowRegistryRelease] = []
    request_hash = roots[0]
    previous_operations: list[dict[str, Any]] = []
    while True:
        row = by_hash[request_hash]
        manifest = manifests[request_hash]
        new_operations = list(manifest["new_operations"])
        operation_sequence = list(manifest["operation_sequence"])
        if sha256_json(operation_sequence) != sha256_json(
            previous_operations + new_operations
        ):
            raise FoundryCatalogError(
                "registry_release_chain_prefix_mismatch",
                "A Registry request is not the exact cumulative successor",
                status_code=409,
            )
        ordered.append(row)
        previous_operations = operation_sequence
        next_hashes = children.get(request_hash, [])
        if not next_hashes:
            break
        request_hash = next_hashes[0]
    if len(ordered) != len(by_hash):
        raise FoundryCatalogError(
            "registry_release_chain_forked",
            "The Registry release-request chain contains an unreachable branch",
            status_code=409,
        )
    return ordered


async def registry_release_request_chain(
    db: AsyncSession,
    *,
    lock: bool = False,
) -> list[WorkflowRegistryRelease]:
    """Return the verified fixed-base request chain from root through head."""

    try:
        from app.services.workflow_registry_v2 import builtin_registry_identity

        base = builtin_registry_identity()
        base_epoch = str(base["registry_epoch"])
        base_hash = str(base["registry_hash"])
        if not base_epoch or not _SHA256_TAGGED_RE.fullmatch(base_hash):
            raise ValueError("invalid built-in registry binding")
    except Exception as exc:
        raise _registry_error(exc) from exc
    statement = select(WorkflowRegistryRelease).order_by(
        WorkflowRegistryRelease.created_at.asc(),
        WorkflowRegistryRelease.id.asc(),
    )
    if lock:
        statement = statement.with_for_update()
    rows = list((await db.execute(statement)).scalars().all())
    return _validate_registry_release_chain(
        rows,
        base_epoch=base_epoch,
        base_hash=base_hash,
    )


async def _locked_registry_release_request_chain(
    db: AsyncSession,
) -> list[WorkflowRegistryRelease]:
    """Serialize creation of cumulative Registry release requests."""

    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(734291105)"))
    return await registry_release_request_chain(db, lock=True)


def _requested_registry_states(
    chain: list[WorkflowRegistryRelease],
) -> dict[tuple[str, str], str]:
    """Return the effective state at the immutable request-chain head."""

    if not chain:
        return {}
    states: dict[tuple[str, str], str] = {}
    operations = (chain[-1].manifest or {}).get("operation_sequence")
    if not isinstance(operations, list):
        raise FoundryCatalogError(
            "registry_release_chain_invalid",
            "The Registry release-request operation sequence is invalid",
            status_code=409,
        )
    for operation in operations:
        if not isinstance(operation, dict):
            raise FoundryCatalogError(
                "registry_release_chain_invalid",
                "The Registry release-request operation is invalid",
                status_code=409,
            )
        if operation.get("operation") == "UPSERT_ENTRY":
            entry = operation.get("entry")
            workflow = entry.get("workflow") if isinstance(entry, dict) else None
            if not isinstance(workflow, dict):
                raise FoundryCatalogError(
                    "registry_release_chain_invalid",
                    "The Registry release-request entry is invalid",
                    status_code=409,
                )
            identity = (
                str(workflow.get("workflow_id") or ""),
                str(workflow.get("version") or ""),
            )
            state = str(workflow.get("state") or "")
        elif operation.get("operation") == "SET_ENTRY_STATUS":
            change = operation.get("status_change")
            if not isinstance(change, dict):
                raise FoundryCatalogError(
                    "registry_release_chain_invalid",
                    "The Registry release-request status change is invalid",
                    status_code=409,
                )
            identity = (
                str(change.get("workflow_id") or ""),
                str(change.get("workflow_version") or ""),
            )
            state = str(change.get("requested_status") or "")
        else:
            raise FoundryCatalogError(
                "registry_release_chain_invalid",
                "The Registry release-request operation kind is invalid",
                status_code=409,
            )
        if not all(identity) or state not in {
            "REGISTERED",
            "SUSPENDED",
            "SUPERSEDED",
            "REVOKED",
        }:
            raise FoundryCatalogError(
                "registry_release_chain_invalid",
                "The Registry release-request state binding is invalid",
                status_code=409,
            )
        states[identity] = state
    return states


async def record_registry_release_dispatch(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    candidate_version_id: uuid.UUID,
    release_request_id: uuid.UUID,
    release_request_hash: str,
    dispatched: bool,
    failure_class: str | None = None,
    retryable: bool = False,
) -> tuple[FoundryCandidateEvent, bool]:
    """Append the post-commit protected release dispatch result."""

    release = await db.get(WorkflowRegistryRelease, release_request_id)
    candidate = await db.get(FoundryCandidate, candidate_id)
    if (
        release is None
        or candidate is None
        or release.manifest_hash != release_request_hash
    ):
        raise FoundryCatalogError(
            "registry_release_dispatch_binding_missing",
            "The Registry dispatch no longer matches its immutable request",
            status_code=409,
        )
    prior = list(
        (
            await db.execute(
                select(FoundryCandidateEvent)
                .where(
                    FoundryCandidateEvent.candidate_id == candidate_id,
                    FoundryCandidateEvent.event_type
                    == "REGISTRY_RELEASE_DISPATCHED",
                )
                .order_by(FoundryCandidateEvent.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    existing = next(
        (
            event
            for event in prior
            if (event.event_payload or {}).get("release_request_id")
            == str(release_request_id)
            and (event.event_payload or {}).get("release_request_hash")
            == release_request_hash
        ),
        None,
    )
    if dispatched and existing is not None:
        return existing, False
    sanitized_failure = None
    if not dispatched:
        sanitized_failure = str(failure_class or "registry_dispatch_failed")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,127}", sanitized_failure):
            sanitized_failure = "registry_dispatch_failed"
    event = await _append_event(
        db,
        candidate_id=candidate_id,
        candidate_version_id=candidate_version_id,
        event_type=(
            "REGISTRY_RELEASE_DISPATCHED"
            if dispatched
            else "REGISTRY_RELEASE_DISPATCH_FAILED"
        ),
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload={
            "release_request_id": str(release_request_id),
            "release_request_hash": release_request_hash,
            "failure_class": sanitized_failure,
            "retryable": bool(retryable) if not dispatched else False,
        },
    )
    await db.commit()
    await db.refresh(event)
    return event, True


async def registry_release_dispatch_succeeded(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    release_request_id: uuid.UUID,
    release_request_hash: str,
) -> bool:
    """Return whether this exact immutable request already reached GitHub."""

    events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate_id,
                    FoundryCandidateEvent.event_type
                    == "REGISTRY_RELEASE_DISPATCHED",
                )
            )
        )
        .scalars()
        .all()
    )
    return any(
        (event.event_payload or {}).get("release_request_id")
        == str(release_request_id)
        and (event.event_payload or {}).get("release_request_hash")
        == release_request_hash
        for event in events
    )


async def _pending_registry_release_request(
    db: AsyncSession,
    *,
    request_kind: str,
    entries: list[dict[str, Any]],
    status_changes: list[dict[str, Any]],
    context: dict[str, Any],
    actor_user_id: uuid.UUID,
) -> WorkflowRegistryRelease:
    chain = await _locked_registry_release_request_chain(db)
    try:
        from app.services.workflow_registry_v2 import builtin_registry_identity

        base = builtin_registry_identity()
        base_epoch = str(base["registry_epoch"])
        base_hash = str(base["registry_hash"])
    except Exception as exc:
        raise _registry_error(exc) from exc
    prior = chain[-1] if chain else None
    prior_manifest = dict(prior.manifest or {}) if prior is not None else {}
    if prior is not None:
        previous_operations = list(prior_manifest["operation_sequence"])
        previous_request_hash = prior.manifest_hash
    else:
        previous_operations = []
        previous_request_hash = None
    new_operations = [
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
    operation_sequence = previous_operations + new_operations
    request_id = uuid.uuid4()
    epoch = f"pending.{request_id.hex}"
    actor_hash = "sha256:" + sha256_json(
        {
            "actor_user_id": str(actor_user_id),
            "release_request_id": str(request_id),
            "domain": "foundry_registry_release_request_v1",
        }
    )
    payload = {
        "schema_version": "standard_astro_registry_release_request_v1",
        "request_id": str(request_id),
        "request_kind": request_kind,
        "request_epoch": epoch,
        "request_status": "PENDING_SIGNATURE",
        "requested_at": _utc_now().isoformat(),
        "requested_by_actor_hash": actor_hash,
        "base_registry_epoch": base_epoch,
        "base_registry_hash": base_hash,
        "previous_request_hash": previous_request_hash,
        "new_operations": new_operations,
        "operation_sequence": operation_sequence,
        "operation_sequence_hash": "sha256:" + sha256_json(operation_sequence),
        "entries": entries,
        "status_changes": status_changes,
        "context": context,
        "runtime_registry_modified": False,
        "signature_required": True,
    }
    return WorkflowRegistryRelease(
        id=request_id,
        epoch=epoch,
        status="PENDING_SIGNATURE",
        manifest=payload,
        manifest_hash="sha256:" + sha256_json(payload),
        signature=None,
        key_id=None,
        public_key_fingerprint=None,
        created_by_user_id=actor_user_id,
    )


async def register_candidate_version(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    candidate_version_id: uuid.UUID,
    candidate_version_hash: str,
    build_attestation_id: uuid.UUID,
    registrar_user_id: uuid.UUID,
) -> tuple[WorkflowRegistryEntry, WorkflowRegistryRelease]:
    """Create an unsigned release request without modifying the runtime registry."""

    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
    version = await db.get(FoundryCandidateVersion, candidate_version_id)
    build_attestation = await db.get(
        FoundryFormalBuildAttestation, build_attestation_id
    )
    if candidate is None or version is None or version.candidate_id != candidate_id:
        raise FoundryCatalogError(
            "candidate_version_not_found", "Candidate version not found", status_code=404
        )
    if (
        candidate.status != "APPROVED"
        or candidate.current_version_number != version.version_number
        or version.version_hash != candidate_version_hash
    ):
        raise FoundryCatalogError(
            "candidate_registration_binding_mismatch",
            "Registration must bind the exact approved current version",
            status_code=409,
        )
    if (
        build_attestation is None
        or build_attestation.candidate_id != candidate.id
        or build_attestation.candidate_version_id != version.id
        or build_attestation.candidate_version_hash != version.version_hash
    ):
        raise FoundryCatalogError(
            "formal_build_attestation_binding_mismatch",
            "Registration requires a protected formal build for the exact approved version",
            status_code=409,
        )
    materialization_receipt = await _validate_formal_source_provenance(
        db,
        version=version,
        source_commit=build_attestation.git_commit,
        source_tree_hash=build_attestation.source_tree_hash,
        dependency_lock_hash=build_attestation.dependency_lock_hash,
    )
    try:
        stored_release_audit_hash, stored_release_audit_receipts = (
            _validate_formal_release_audit(
                build_attestation.formal_release_audit_receipts,
                source_tree_hash=build_attestation.source_tree_hash,
                dependency_lock_hash=build_attestation.dependency_lock_hash,
                formal_sbom_hash=build_attestation.formal_sbom_hash,
            )
        )
    except FoundryCatalogError as exc:
        raise FoundryCatalogError(
            "formal_release_audit_required",
            "Registration requires passed dependency, license, and secret-scan receipts",
            status_code=409,
        ) from exc
    if stored_release_audit_hash != build_attestation.formal_release_audit_hash:
        raise FoundryCatalogError(
            "formal_release_audit_binding_mismatch",
            "Formal release supply-chain receipt no longer matches its build record",
            status_code=409,
        )
    image_digest = build_attestation.formal_worker_image_digest
    if version.created_by_user_id == registrar_user_id:
        raise FoundryCatalogError(
            "candidate_self_registration_forbidden",
            "A candidate author cannot register the same version",
            status_code=403,
        )
    passed_demo = await _strict_passed_demo_v1(db, version)
    if passed_demo is None:
        raise FoundryCatalogError(
            "passed_demo_required",
            "An exact-version PASSED Demo is required before registration",
            status_code=409,
        )
    reviews = list(
        (
            await db.execute(
                select(FoundryReview).where(
                    FoundryReview.candidate_version_id == version.id
                )
            )
        )
        .scalars()
        .all()
    )
    if not _review_requirements_satisfied(str(candidate.risk_level or ""), reviews):
        raise FoundryCatalogError(
            "candidate_human_approval_missing",
            "The exact candidate version does not have the required human reviews",
            status_code=409,
        )
    try:
        from app.services.evidence_pack_v2 import jcs_canonicalize
        from app.services.workflow_registry_v2 import (
            assert_registry_entry_static_compatible,
            build_registry_entry_from_approved_candidate,
        )

        formal_spec_hash = "sha256:" + hashlib.sha256(
            jcs_canonicalize(version.workflow_spec)
        ).hexdigest()
        release_entry = build_registry_entry_from_approved_candidate(
            {
                "candidate_id": str(candidate.id),
                "candidate_version": version.version_number,
                "candidate_version_hash": version.version_hash,
                "approved_candidate_version_hash": version.version_hash,
                "status": "APPROVED",
                "workflow_spec": version.workflow_spec,
                "workflow_spec_hash": formal_spec_hash,
                "worker_image_digest": image_digest,
                "tool_specs": list(version.workflow_spec.get("tool_specs") or []),
                "reviews": _review_records_for_registry(reviews),
            }
        )
        # A signed release is configuration, not a plugin installer.  Reject
        # Demo-only or newly generated entrypoints before a PENDING release is
        # even created; startup repeats this exact static ToolSpec comparison.
        assert_registry_entry_static_compatible(release_entry)
    except Exception as exc:
        raise _registry_error(exc) from exc

    workflow = dict(release_entry["workflow"])
    existing = await db.scalar(
        select(WorkflowRegistryEntry).where(
            WorkflowRegistryEntry.candidate_version_id == version.id
        )
    )
    if existing is not None:
        if existing.formal_build_attestation_id != build_attestation.id:
            raise FoundryCatalogError(
                "candidate_registration_attestation_conflict",
                "This candidate version already has a different formal-build request",
                status_code=409,
            )
        if not (
            existing.candidate_id == candidate.id
            and existing.candidate_version_hash == version.version_hash
            and existing.workflow_id == str(workflow["workflow_id"])
            and existing.workflow_version == str(workflow["version"])
            and existing.registry_entry_hash
            == str(release_entry["registry_entry_hash"])
            and existing.workflow_spec == version.workflow_spec
            and existing.release_entry == release_entry
            and existing.risk_level == str(candidate.risk_level)
            and existing.worker_image_digest == image_digest
        ):
            raise FoundryCatalogError(
                "candidate_registration_receipt_conflict",
                "Pending registration no longer matches the exact validated release entry",
                status_code=409,
            )
        releases = list(
            (
                await db.execute(
                    select(WorkflowRegistryRelease).order_by(
                        WorkflowRegistryRelease.created_at.desc()
                    )
                )
            )
            .scalars()
            .all()
        )
        release = next(
            (
                row
                for row in releases
                if isinstance(row.manifest, dict)
                if row.manifest_hash
                == "sha256:" + sha256_json(row.manifest)
                and isinstance(row.manifest.get("context"), dict)
                and row.manifest["context"].get(
                    "formal_build_attestation_id"
                )
                == str(build_attestation.id)
                and isinstance(row.manifest.get("entries"), list)
                and any(
                    item == release_entry
                    for item in row.manifest["entries"]
                    if isinstance(item, dict)
                )
            ),
            None,
        )
        if release is None:
            raise FoundryCatalogError(
                "workflow_registry_release_missing",
                "Pending registry entry has no exact release-request receipt",
                status_code=409,
            )
        return existing, release
    release_chain = await _locked_registry_release_request_chain(db)
    requested_states = _requested_registry_states(release_chain)
    prior_entries = list(
        (
            await db.execute(
                select(WorkflowRegistryEntry)
                .where(
                    WorkflowRegistryEntry.workflow_id
                    == str(workflow["workflow_id"]),
                    WorkflowRegistryEntry.workflow_version
                    != str(workflow["version"]),
                )
                .order_by(
                    WorkflowRegistryEntry.registered_at.asc(),
                    WorkflowRegistryEntry.id.asc(),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    superseded_entries = [
        old_entry
        for old_entry in prior_entries
        if requested_states.get(
            (old_entry.workflow_id, old_entry.workflow_version),
            old_entry.status,
        )
        in {"REGISTERED", "SUSPENDED"}
    ]
    supersede_reason = (
        f"superseded_by={workflow['workflow_id']}@{workflow['version']}"
    )
    supersede_changes = [
        {
            "registry_entry_id": str(old_entry.id),
            "registry_entry_hash": old_entry.registry_entry_hash,
            "workflow_id": old_entry.workflow_id,
            "workflow_version": old_entry.workflow_version,
            "requested_status": "SUPERSEDED",
            "reason": supersede_reason,
            "superseded_by_workflow_id": str(workflow["workflow_id"]),
            "superseded_by_workflow_version": str(workflow["version"]),
        }
        for old_entry in superseded_entries
    ]
    release = await _pending_registry_release_request(
        db,
        request_kind=(
            "REGISTER_CANDIDATE_AND_SUPERSEDE"
            if supersede_changes
            else "REGISTER_CANDIDATE"
        ),
        entries=[release_entry],
        status_changes=supersede_changes,
        context={
            "candidate_db_id": str(candidate.id),
            "candidate_version_db_id": str(version.id),
            "formal_build_attestation_id": str(build_attestation.id),
            "formal_build_attestation_receipt_sha256": build_attestation.receipt_hash,
            "formal_build_attestation_artifact_sha256": (
                build_attestation.attestation_artifact_hash
            ),
            "formal_build_git_commit": build_attestation.git_commit,
            "formal_build_sbom_sha256": build_attestation.formal_sbom_hash,
            "formal_build_test_report_sha256": build_attestation.test_report_hash,
            "formal_build_release_audit_sha256": stored_release_audit_hash,
            "formal_build_release_audit_receipts": stored_release_audit_receipts,
            "formal_build_sigstore_bundle_sha256": build_attestation.sigstore_bundle_hash,
            "formal_build_provenance_sha256": build_attestation.provenance_hash,
            **(
                {
                    "source_materialization_receipt_id": str(
                        materialization_receipt.id
                    ),
                    "source_materialization_receipt_sha256": (
                        materialization_receipt.receipt_hash
                    ),
                }
                if materialization_receipt is not None
                else {}
            ),
        },
        actor_user_id=registrar_user_id,
    )
    entry = WorkflowRegistryEntry(
        workflow_id=str(workflow["workflow_id"]),
        workflow_version=str(workflow["version"]),
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        formal_build_attestation_id=build_attestation.id,
        registry_entry_hash=str(release_entry["registry_entry_hash"]),
        workflow_spec=version.workflow_spec,
        release_entry=release_entry,
        risk_level=str(candidate.risk_level),
        worker_image_digest=image_digest,
        status="PENDING_RELEASE",
        registered_by_user_id=registrar_user_id,
        registered_at=_utc_now(),
    )
    db.add_all([entry, release])
    await db.flush()
    for old_entry in superseded_entries:
        await _append_event(
            db,
            candidate_id=old_entry.candidate_id,
            candidate_version_id=old_entry.candidate_version_id,
            event_type="REGISTRY_STATUS_CHANGE_REQUESTED",
            actor_kind="HUMAN_REGISTRAR",
            actor_user_id=registrar_user_id,
            payload={
                "reason": supersede_reason,
                "registry_entry_hash": old_entry.registry_entry_hash,
                "requested_status": "SUPERSEDED",
                "superseded_by_workflow_id": str(workflow["workflow_id"]),
                "superseded_by_workflow_version": str(workflow["version"]),
                "release_request_id": str(release.id),
                "release_request_hash": release.manifest_hash,
                "runtime_registry_modified": False,
            },
        )
    await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="REGISTRY_RELEASE_REQUESTED",
        actor_kind="HUMAN_REGISTRAR",
        actor_user_id=registrar_user_id,
        payload={
            "registry_entry_id": str(entry.id),
            "registry_entry_hash": entry.registry_entry_hash,
            "release_request_id": str(release.id),
            "release_request_hash": release.manifest_hash,
            "formal_build_attestation_id": str(build_attestation.id),
            "candidate_status": candidate.status,
            "runtime_registry_modified": False,
        },
    )
    await db.commit()
    await db.refresh(entry)
    await db.refresh(release)
    return entry, release


async def change_registered_candidate_status(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    target_status: str,
    reason: str,
    actor_user_id: uuid.UUID,
) -> tuple[FoundryCandidate, WorkflowRegistryEntry, WorkflowRegistryRelease]:
    target_status = target_status.upper()
    if target_status not in {"SUSPENDED", "REVOKED"}:
        raise FoundryCatalogError("invalid_registry_status", "Unsupported registry status")
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
    entry = await db.scalar(
        select(WorkflowRegistryEntry)
        .where(WorkflowRegistryEntry.candidate_id == candidate_id)
        .with_for_update()
    )
    if candidate is None or entry is None:
        raise FoundryCatalogError(
            "registered_candidate_not_found", "Registered candidate not found", status_code=404
        )
    allowed = candidate.status == "PROMOTED" or (
        candidate.status == "SUSPENDED" and target_status == "REVOKED"
    )
    if not allowed:
        raise FoundryCatalogError(
            "registry_status_transition_invalid",
            "The requested registry status transition is not allowed",
            status_code=409,
        )
    if entry.status not in {"REGISTERED", "SUSPENDED"}:
        raise FoundryCatalogError(
            "registry_entry_not_active",
            "Only an imported formal registry entry can request a status change",
            status_code=409,
        )
    release_chain = await _locked_registry_release_request_chain(db)
    requested_states = _requested_registry_states(release_chain)
    effective_state = requested_states.get(
        (entry.workflow_id, entry.workflow_version),
        entry.status,
    )
    allowed_requested_transitions = {
        "REGISTERED": {"SUSPENDED", "REVOKED"},
        "SUSPENDED": {"REVOKED"},
        "SUPERSEDED": set(),
        "REVOKED": set(),
    }
    if target_status not in allowed_requested_transitions.get(
        effective_state,
        set(),
    ):
        raise FoundryCatalogError(
            "registry_status_transition_pending",
            "The request-chain head already makes this status transition invalid",
            status_code=409,
        )
    release = await _pending_registry_release_request(
        db,
        request_kind=f"{target_status}_WORKFLOW",
        entries=[],
        status_changes=[
            {
                "registry_entry_id": str(entry.id),
                "registry_entry_hash": entry.registry_entry_hash,
                "workflow_id": entry.workflow_id,
                "workflow_version": entry.workflow_version,
                "requested_status": target_status,
                "reason": reason,
            }
        ],
        context={"candidate_db_id": str(candidate.id)},
        actor_user_id=actor_user_id,
    )
    db.add(release)
    await db.flush()
    await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=entry.candidate_version_id,
        event_type="REGISTRY_STATUS_CHANGE_REQUESTED",
        actor_kind="HUMAN_REGISTRAR",
        actor_user_id=actor_user_id,
        payload={
            "reason": reason,
            "registry_entry_hash": entry.registry_entry_hash,
            "requested_status": target_status,
            "release_request_id": str(release.id),
            "release_request_hash": release.manifest_hash,
            "runtime_registry_modified": False,
        },
    )
    await db.commit()
    await db.refresh(candidate)
    await db.refresh(entry)
    await db.refresh(release)
    return candidate, entry, release
