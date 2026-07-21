"""Service layer for non-formal workflow candidates and their durable ledger."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, select, text
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
        "REVOKED",
    }
)
REQUEST_STATUSES = frozenset({"SUBMITTED", "TRIAGED", "MERGED", "CLOSED"})
GENERATION_ROUTES = frozenset({"COMPOSITION", "DATA_ADAPTER", "SCIENCE_CODE"})
RISK_LEVELS = frozenset({"R0", "R1", "R2", "R3"})
DEMO_STATUSES = frozenset({"PASSED", "PARTIAL", "FAILED"})
REVIEW_DECISIONS = frozenset({"APPROVED", "REJECTED", "CHANGES_REQUESTED"})
REVIEW_SCOPES = frozenset({"ENGINEERING", "SCIENTIFIC"})
NON_FORMAL_EVIDENCE_CLASS = "NON_FORMAL_DEMO"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IMAGE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
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


def _contains_formal_claim_escape(value: Any) -> bool:
    """Reject nested candidate output that impersonates formal evidence."""

    if isinstance(value, list):
        return any(_contains_formal_claim_escape(item) for item in value)
    if not isinstance(value, dict):
        return False
    for raw_key, item in value.items():
        key = str(raw_key).strip().lower()
        if key in {"publication_ready", "claim_eligible"} and item is True:
            return True
        if key == "scientific_verdict" and str(item).upper() == "SUPPORTED":
            return True
        if key in {"evidence_pack", "evidence_pack_id", "formal_evidence_pack"} and item:
            return True
        if _contains_formal_claim_escape(item):
            return True
    return False


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
    if _contains_formal_claim_escape(value.get("workflow_spec")):
        raise FoundryCatalogError(
            "candidate_formal_claim_forbidden",
            "Candidate workflow specs cannot claim formal scientific support",
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
    version_hash = sha256_json(bundle)
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
        or sha256_json({"candidate_bundle_sha256": version_hash}),
        "patch_hash",
    )
    sbom_hash = _require_sha256(
        draft.get("sbom_hash") or sha256_json([]), "sbom_hash"
    )
    image_digest = _require_image_digest(
        draft.get("validation_runner_image_digest")
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
    if candidate.status in {"PROMOTED", "SUSPENDED", "REVOKED"}:
        raise FoundryCatalogError(
            "candidate_not_triageable",
            "A promoted, suspended, or revoked candidate cannot be re-triaged",
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


async def start_validation_run(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    candidate_version_id: uuid.UUID,
    candidate_version_hash: str,
    actor_kind: str,
    actor_user_id: uuid.UUID | None,
) -> FoundryValidationRun:
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
            "Validation must bind the exact current candidate version hash",
            status_code=409,
        )
    if candidate.status in {
        "APPROVED",
        "REJECTED",
        "PROMOTED",
        "SUSPENDED",
        "REVOKED",
    }:
        raise FoundryCatalogError(
            "candidate_version_validation_closed",
            "This exact candidate version has a terminal review decision; create a new version to validate",
            status_code=409,
        )
    if candidate.status in {"PROMOTED", "SUSPENDED", "REVOKED"}:
        raise FoundryCatalogError(
            "candidate_not_validatable", "This candidate cannot start a validation run", status_code=409
        )
    active = await db.scalar(
        select(FoundryValidationRun).where(
            FoundryValidationRun.candidate_version_id == version.id,
            FoundryValidationRun.status.in_({"QUEUED", "RUNNING"}),
        )
    )
    if active is not None:
        return active
    run = FoundryValidationRun(
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        status="QUEUED",
        requested_by_kind=actor_kind,
        requested_by_user_id=actor_user_id,
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
        },
    )
    await db.commit()
    await db.refresh(run)
    return run


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
        or _contains_formal_claim_escape(report.get("result"))
        or _contains_formal_claim_escape(report.get("validation_summary"))
    ):
        raise FoundryCatalogError(
            "candidate_formal_claim_forbidden",
            "DemoReport attempted to cross the non-formal evidence boundary",
        )
    status = str(report.get("status") or "").upper()
    if status not in DEMO_STATUSES:
        raise FoundryCatalogError("invalid_demo_status", "Unsupported Demo status")
    try:
        report_demo_id = uuid.UUID(str(report.get("demo_run_id")))
    except ValueError as exc:
        raise FoundryCatalogError(
            "invalid_demo_report", "demo_run_id must be a UUID"
        ) from exc
    existing = await db.get(FoundryDemoRun, report_demo_id)
    if existing is not None:
        if existing.demo_report_hash != declared_report_hash:
            raise FoundryCatalogError(
                "demo_run_id_conflict",
                "The Demo run id already exists with different content",
                status_code=409,
            )
        return existing

    run = await db.scalar(
        select(FoundryValidationRun)
        .where(FoundryValidationRun.id == validation_run_id)
        .with_for_update()
    )
    if run is None:
        raise FoundryCatalogError(
            "validation_run_not_found", "Validation run not found", status_code=404
        )
    if run.status not in {"QUEUED", "RUNNING"}:
        raise FoundryCatalogError(
            "validation_run_already_completed",
            "Validation run is already terminal",
            status_code=409,
        )
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == run.candidate_id)
        .with_for_update()
    )
    version = await db.get(FoundryCandidateVersion, run.candidate_version_id)
    if candidate is None or version is None:
        raise FoundryCatalogError(
            "candidate_version_not_found", "Candidate version not found", status_code=404
        )
    if (
        candidate.current_version_number != version.version_number
        or version.version_hash != run.candidate_version_hash
        or report.get("candidate_id") != version.candidate_key
        or report.get("candidate_version") != version.version_number
        or report.get("candidate_bundle_sha256") != version.version_hash
        or report.get("workflow_spec_sha256") != version.workflow_spec_hash
        or report.get("dependency_lock_sha256") != version.dependency_lock_hash
        or report.get("runner_definition_sha256")
        != version.candidate_bundle.get("runner_definition_sha256")
        or report.get("generation") != version.candidate_bundle.get("generation")
        or report.get("source_pins") != version.candidate_bundle.get("source_pins")
        or report.get("fixture_hashes") != version.candidate_bundle.get("fixture_hashes")
    ):
        raise FoundryCatalogError(
            "candidate_version_binding_mismatch",
            "DemoReport does not bind the exact current candidate bundle",
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
    run.validation_summary = dict(report.get("validation_summary") or {})
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
        candidate_bundle_hash=version.version_hash,
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
        artifact_manifest=[],
        duration_ms=duration_ms,
        demo_report_hash=declared_report_hash,
        started_at=started_at,
        completed_at=completed_at,
    )
    db.add(demo)
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


async def record_formal_build_attestation(
    db: AsyncSession,
    *,
    attestation_report: dict[str, Any],
    expected_oidc_subject: str,
) -> FoundryFormalBuildAttestation:
    """Accept a byte-bound formal build receipt from the protected CI callback."""

    if not isinstance(attestation_report, dict):
        raise FoundryCatalogError(
            "invalid_formal_build_attestation", "Build attestation must be an object"
        )
    report = json.loads(canonical_json(attestation_report))
    declared_hash = str(report.pop("receipt_sha256", ""))
    if _require_sha256(declared_hash, "receipt_sha256") != sha256_json(report):
        raise FoundryCatalogError(
            "formal_build_receipt_hash_mismatch",
            "Formal build attestation content hash does not match",
        )
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
        "tests_passed",
        "formal_worker_image_digest",
        "oidc_issuer",
        "oidc_subject",
        "sigstore_verified",
        "sigstore_bundle_sha256",
        "provenance_sha256",
        "verification_method",
        "build_metadata",
        "built_at",
    }
    if set(report) != required or report.get("schema_version") != 1:
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
    existing = await db.get(FoundryFormalBuildAttestation, attestation_id)
    if existing is not None:
        if existing.receipt_hash != declared_hash:
            raise FoundryCatalogError(
                "formal_build_attestation_id_conflict",
                "Build attestation id already exists with different content",
                status_code=409,
            )
        return existing
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
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
    passed_demo = await db.scalar(
        select(FoundryDemoRun.id).where(
            FoundryDemoRun.candidate_version_id == version.id,
            FoundryDemoRun.status == "PASSED",
            FoundryDemoRun.evidence_class == NON_FORMAL_EVIDENCE_CLASS,
            FoundryDemoRun.publication_ready.is_(False),
            FoundryDemoRun.claim_eligible.is_(False),
            FoundryDemoRun.evidence_pack_allowed.is_(False),
        )
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
    if passed_demo is None or not _review_requirements_satisfied(
        str(candidate.risk_level or ""), reviews
    ):
        raise FoundryCatalogError(
            "formal_build_approval_missing",
            "A PASSED Demo and required human approvals must precede formal build",
            status_code=409,
        )
    if report.get("tests_passed") is not True or report.get("sigstore_verified") is not True:
        raise FoundryCatalogError(
            "formal_build_verification_failed",
            "Protected CI must verify tests and Sigstore before callback",
        )
    if report.get("verification_method") != "protected_ci_callback_after_sigstore_verification":
        raise FoundryCatalogError(
            "formal_build_verification_method_invalid",
            "Formal builds require the protected Sigstore-verifying callback",
        )
    if report.get("oidc_issuer") != "https://token.actions.githubusercontent.com":
        raise FoundryCatalogError(
            "formal_build_oidc_issuer_invalid", "Untrusted formal-build OIDC issuer"
        )
    expected_subject = str(expected_oidc_subject or "").strip()
    if not expected_subject or report.get("oidc_subject") != expected_subject:
        raise FoundryCatalogError(
            "formal_build_oidc_subject_invalid", "Untrusted formal-build OIDC subject"
        )
    source_tree_hash = _require_sha256(report["source_tree_sha256"], "source_tree_sha256")
    dependency_lock_hash = _require_sha256(
        report["dependency_lock_sha256"], "dependency_lock_sha256"
    )
    if source_tree_hash != version.code_tree_hash or dependency_lock_hash != version.dependency_lock_hash:
        raise FoundryCatalogError(
            "formal_build_source_binding_mismatch",
            "Formal build source tree or dependency lock differs from the approved version",
            status_code=409,
        )
    git_commit = str(report.get("git_commit") or "").lower()
    if not _GIT_SHA_RE.fullmatch(git_commit):
        raise FoundryCatalogError(
            "invalid_formal_build_git_commit", "Formal build Git commit must be 40 hex characters"
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
    row = FoundryFormalBuildAttestation(
        id=attestation_id,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        candidate_version_hash=version.version_hash,
        source_tree_hash=source_tree_hash,
        git_commit=git_commit,
        dependency_lock_hash=dependency_lock_hash,
        formal_sbom_hash=_require_sha256(report["formal_sbom_sha256"], "formal_sbom_sha256"),
        test_report_hash=_require_sha256(report["test_report_sha256"], "test_report_sha256"),
        formal_worker_image_digest=_require_image_digest(report["formal_worker_image_digest"]),
        oidc_issuer=str(report["oidc_issuer"]),
        oidc_subject=str(report["oidc_subject"]),
        sigstore_bundle_hash=_require_sha256(
            report["sigstore_bundle_sha256"], "sigstore_bundle_sha256"
        ),
        provenance_hash=_require_sha256(report["provenance_sha256"], "provenance_sha256"),
        build_metadata=build_metadata,
        receipt_hash=declared_hash,
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
            "formal_worker_image_digest": row.formal_worker_image_digest,
            "git_commit": row.git_commit,
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


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

    status = status.upper()
    if status not in DEMO_STATUSES:
        raise FoundryCatalogError("invalid_demo_status", "Unsupported Demo status")
    if completed_at < started_at:
        raise FoundryCatalogError("invalid_demo_time", "Demo completion precedes its start")
    if _contains_formal_claim_escape(structured_result) or _contains_formal_claim_escape(
        validation_summary
    ):
        raise FoundryCatalogError(
            "candidate_formal_claim_forbidden",
            "A candidate Demo cannot contain a formal verdict or Evidence Pack",
        )
    run = await db.scalar(
        select(FoundryValidationRun)
        .where(FoundryValidationRun.id == validation_run_id)
        .with_for_update()
    )
    if run is None:
        raise FoundryCatalogError("validation_run_not_found", "Validation run not found", status_code=404)
    if run.status not in {"QUEUED", "RUNNING"}:
        existing = await db.scalar(
            select(FoundryDemoRun).where(FoundryDemoRun.validation_run_id == run.id)
        )
        if existing is not None:
            return existing
        raise FoundryCatalogError(
            "validation_run_already_completed", "Validation run is already terminal", status_code=409
        )
    version = await db.get(FoundryCandidateVersion, run.candidate_version_id)
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == run.candidate_id)
        .with_for_update()
    )
    if version is None or candidate is None or version.version_hash != run.candidate_version_hash:
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
    run.validation_summary = validation_summary
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
    if candidate.current_version_number == version.version_number:
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
        "REVOKED",
    }:
        raise FoundryCatalogError(
            "candidate_version_review_closed",
            "This exact candidate version has a terminal review decision; create a new version",
            status_code=409,
        )
    if version.created_by_user_id == reviewer_user_id:
        raise FoundryCatalogError(
            "candidate_self_review_forbidden",
            "A candidate author cannot approve the same candidate version",
            status_code=403,
        )
    passed_demo = await db.scalar(
        select(FoundryDemoRun.id).where(
            FoundryDemoRun.candidate_version_id == version.id,
            FoundryDemoRun.status == "PASSED",
            FoundryDemoRun.evidence_class == NON_FORMAL_EVIDENCE_CLASS,
            FoundryDemoRun.publication_ready.is_(False),
            FoundryDemoRun.claim_eligible.is_(False),
        )
    )
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
            "reviewer_id": str(review.reviewer_user_id or ""),
            "reviewer_type": "human",
            "review_role": review.review_scope.lower(),
            "decision": review.decision,
            "candidate_version_hash": review.candidate_version_hash,
        }
        for review in reviews
    ]


async def _pending_registry_release_request(
    db: AsyncSession,
    *,
    request_kind: str,
    entries: list[dict[str, Any]],
    status_changes: list[dict[str, Any]],
    context: dict[str, Any],
    actor_user_id: uuid.UUID,
) -> WorkflowRegistryRelease:
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        # Serialize the global release-request chain across independent
        # candidate transactions. The lock is released by the caller's commit.
        await db.execute(text("SELECT pg_advisory_xact_lock(734291105)"))
    try:
        from app.services.workflow_registry_v2 import registry_snapshot

        base = registry_snapshot()
        base_epoch = str(base["epoch"])
        base_hash = str(base["registry_hash"])
        if not base_epoch or not re.fullmatch(r"sha256:[0-9a-f]{64}", base_hash):
            raise ValueError("invalid base registry binding")
    except Exception as exc:
        raise _registry_error(exc) from exc
    pending_releases = list(
        (
            await db.execute(
                select(WorkflowRegistryRelease).where(
                    WorkflowRegistryRelease.status == "PENDING_SIGNATURE"
                )
            )
        )
        .scalars()
        .all()
    )
    matching_releases = [
        row
        for row in pending_releases
        if (row.manifest or {}).get("base_registry_epoch") == base_epoch
        and (row.manifest or {}).get("base_registry_hash") == base_hash
        and isinstance((row.manifest or {}).get("operation_sequence"), list)
    ]
    prior = (
        max(
            matching_releases,
            key=lambda row: (
                len((row.manifest or {}).get("operation_sequence") or []),
                str(row.id),
            ),
        )
        if matching_releases
        else None
    )
    prior_manifest = dict(prior.manifest or {}) if prior is not None else {}
    if (
        prior_manifest.get("base_registry_epoch") == base_epoch
        and prior_manifest.get("base_registry_hash") == base_hash
        and isinstance(prior_manifest.get("operation_sequence"), list)
    ):
        previous_operations = list(prior_manifest["operation_sequence"])
        previous_request_hash = prior.manifest_hash
    else:
        previous_operations = []
        previous_request_hash = None
    new_operations = [
        {"operation": "UPSERT_ENTRY", "entry": entry} for entry in entries
    ] + [
        {"operation": "SET_ENTRY_STATUS", "status_change": change}
        for change in status_changes
    ]
    operation_sequence = previous_operations + new_operations
    request_id = uuid.uuid4()
    epoch = f"pending.{request_id.hex}"
    payload = {
        "schema_version": "standard_astro_registry_release_request_v1",
        "request_id": str(request_id),
        "request_kind": request_kind,
        "request_epoch": epoch,
        "request_status": "PENDING_SIGNATURE",
        "requested_at": _utc_now().isoformat(),
        "requested_by_user_id": str(actor_user_id),
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

    existing = await db.scalar(
        select(WorkflowRegistryEntry).where(
            WorkflowRegistryEntry.candidate_version_id == candidate_version_id
        )
    )
    if existing is not None:
        if existing.formal_build_attestation_id != build_attestation_id:
            raise FoundryCatalogError(
                "candidate_registration_attestation_conflict",
                "This candidate version already has a different formal-build request",
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
                if any(
                    entry.get("candidate_version_hash")
                    == existing.candidate_version_hash
                    for entry in (row.manifest or {}).get("entries", [])
                    if isinstance(entry, dict)
                )
            ),
            None,
        )
        if release is None:
            raise FoundryCatalogError(
                "workflow_registry_release_missing",
                "Pending registry entry has no release-request receipt",
                status_code=409,
            )
        return existing, release
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
    image_digest = build_attestation.formal_worker_image_digest
    if version.created_by_user_id == registrar_user_id:
        raise FoundryCatalogError(
            "candidate_self_registration_forbidden",
            "A candidate author cannot register the same version",
            status_code=403,
        )
    passed_demo = await db.scalar(
        select(FoundryDemoRun).where(
            FoundryDemoRun.candidate_version_id == version.id,
            FoundryDemoRun.status == "PASSED",
            FoundryDemoRun.evidence_class == NON_FORMAL_EVIDENCE_CLASS,
            FoundryDemoRun.publication_ready.is_(False),
            FoundryDemoRun.claim_eligible.is_(False),
            FoundryDemoRun.evidence_pack_allowed.is_(False),
        )
    )
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
    except Exception as exc:
        raise _registry_error(exc) from exc

    release = await _pending_registry_release_request(
        db,
        request_kind="REGISTER_CANDIDATE",
        entries=[release_entry],
        status_changes=[],
        context={
            "candidate_db_id": str(candidate.id),
            "candidate_version_db_id": str(version.id),
            "formal_build_attestation_id": str(build_attestation.id),
            "formal_build_attestation_receipt_sha256": build_attestation.receipt_hash,
            "formal_build_git_commit": build_attestation.git_commit,
            "formal_build_sbom_sha256": build_attestation.formal_sbom_hash,
            "formal_build_test_report_sha256": build_attestation.test_report_hash,
            "formal_build_sigstore_bundle_sha256": build_attestation.sigstore_bundle_hash,
            "formal_build_provenance_sha256": build_attestation.provenance_hash,
        },
        actor_user_id=registrar_user_id,
    )
    workflow = dict(release_entry["workflow"])
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
