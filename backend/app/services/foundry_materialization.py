"""Protected source-materialization lane for AI-generated Candidate patches."""

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
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.foundry_materialization_records import (
    FoundryMaterializationAttestation,
    FoundryMaterializationReceipt,
)
from app.models.foundry_records import (
    FoundryCandidate,
    FoundryCandidateEvent,
    FoundryCandidateVersion,
    FoundryDemoRun,
    FoundryReview,
)
from app.services.foundry_catalog import (
    NON_FORMAL_EVIDENCE_CLASS,
    FoundryCatalogError,
    _append_event,
    _review_requirements_satisfied,
    ai_draft_validation_binding,
    append_candidate_version,
    canonical_json,
    sha256_json,
)


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_WORKFLOW = re.compile(r"^[A-Za-z0-9_.-]{1,128}\.ya?ml$")
_MODULE_PATH = re.compile(
    r"^backend/app/services/foundry_generated/[a-z][a-z0-9_]{2,96}\.py$"
)
_MATERIALIZATION_DOMAIN = b"standard-astro/foundry-materialization/v1\0"
_PR_PAYLOAD_SCHEMA = "standard_astro_materialization_pr_v1"
_FINAL_PAYLOAD_SCHEMA = "standard_astro_materialization_final_v1"
_BUNDLE_SCHEMA = "standard_astro_materialization_attestation_bundle_v1"
_DISPATCH_LEASE_TIMEOUT = timedelta(minutes=2)
_WORKFLOW_CALLBACK_TIMEOUT = timedelta(minutes=60)
_MAX_DISPATCH_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class MaterializationDispatchReservation:
    """One durable, bounded right to call a protected workflow once."""

    request_event_id: uuid.UUID
    dispatch_request_id: uuid.UUID
    reservation_event_id: uuid.UUID
    attempt_number: int
    should_dispatch: bool
    state: str
    retry_after: datetime


_DISPATCH_LANES = {
    "materialization": {
        "reserved": "SOURCE_MATERIALIZATION_DISPATCH_RESERVED",
        "succeeded": "SOURCE_MATERIALIZATION_DISPATCHED",
        "failed": "SOURCE_MATERIALIZATION_DISPATCH_FAILED",
        "exhausted": "SOURCE_MATERIALIZATION_DISPATCH_EXHAUSTED",
        "error_prefix": "materialization_dispatch",
    },
    "finalization": {
        "reserved": "SOURCE_MATERIALIZATION_FINALIZATION_DISPATCH_RESERVED",
        "succeeded": "SOURCE_MATERIALIZATION_FINALIZATION_DISPATCHED",
        "failed": "SOURCE_MATERIALIZATION_FINALIZATION_DISPATCH_FAILED",
        "exhausted": "SOURCE_MATERIALIZATION_FINALIZATION_DISPATCH_EXHAUSTED",
        "error_prefix": "materialization_finalization_dispatch",
    },
}


def _legacy_dispatch_success_binds(
    event: FoundryCandidateEvent,
    *,
    request_event_id: uuid.UUID,
    dispatch_request_id: uuid.UUID,
    lane: str,
) -> bool:
    config = _DISPATCH_LANES[lane]
    payload = dict(event.event_payload or {})
    if event.event_type != config["succeeded"] or "reservation_event_id" in payload:
        return False
    if lane == "materialization":
        return payload.get("materialization_request_id") == str(request_event_id)
    return payload.get("materialization_attestation_id") == str(
        dispatch_request_id
    )


def _successful_dispatch_binds(
    event: FoundryCandidateEvent,
    *,
    request_event_id: uuid.UUID,
    dispatch_request_id: uuid.UUID,
    lane: str,
) -> bool:
    config = _DISPATCH_LANES[lane]
    payload = dict(event.event_payload or {})
    # A protected workflow may have accepted workflow_dispatch even when
    # GitHub (or an intermediary) returned a 5xx to the control plane.  The
    # later signed workflow receipt is authoritative proof that this exact
    # reservation did run, so an exact outcome-unknown event is an admissible
    # dispatch binding.  Known failures remain ineligible.
    if event.event_type not in {config["succeeded"], config["failed"]}:
        return False
    if (
        event.event_type == config["failed"]
        and payload.get("outcome_unknown") is not True
    ):
        return False
    if "reservation_event_id" not in payload:
        if event.event_type != config["succeeded"]:
            return False
        return _legacy_dispatch_success_binds(
            event,
            request_event_id=request_event_id,
            dispatch_request_id=dispatch_request_id,
            lane=lane,
        )
    return (
        payload.get("request_event_id") == str(request_event_id)
        and payload.get("dispatch_request_id") == str(dispatch_request_id)
        and isinstance(payload.get("reservation_event_id"), str)
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def materialization_workflow_retry_after(event: FoundryCandidateEvent) -> datetime:
    """Return the workflow callback deadline after a known/unknown dispatch."""

    return _as_utc(event.created_at) + _WORKFLOW_CALLBACK_TIMEOUT


async def _reserve_dispatch_attempt(
    db: AsyncSession,
    *,
    request_event: FoundryCandidateEvent,
    dispatch_request_id: uuid.UUID,
    lane: str,
) -> MaterializationDispatchReservation:
    """Reserve at most one active workflow dispatch for an immutable request.

    A GitHub ``204`` only proves that Actions accepted a dispatch.  If no
    signed callback arrives, the same human endpoint may reserve another
    attempt after a fixed timeout.  Reservations and outcomes are append-only,
    so retries are bounded, idempotent under concurrent callers, and auditable.
    """

    config = _DISPATCH_LANES.get(lane)
    if config is None:
        raise ValueError("materialization_dispatch_lane_invalid")
    candidate = await db.scalar(
        select(FoundryCandidate.id)
        .where(FoundryCandidate.id == request_event.candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise FoundryCatalogError(
            "candidate_not_found", "Candidate not found", status_code=404
        )
    event_types = tuple(
        str(config[key])
        for key in ("reserved", "succeeded", "failed", "exhausted")
    )
    events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent)
                .where(
                    FoundryCandidateEvent.candidate_id
                    == request_event.candidate_id,
                    FoundryCandidateEvent.candidate_version_id
                    == request_event.candidate_version_id,
                    FoundryCandidateEvent.event_type.in_(event_types),
                )
                .order_by(
                    FoundryCandidateEvent.created_at.asc(),
                    FoundryCandidateEvent.id.asc(),
                )
            )
        ).scalars().all()
    )
    request_id = str(request_event.id)
    reservations = [
        row
        for row in events
        if row.event_type == config["reserved"]
        and str((row.event_payload or {}).get("request_event_id") or "")
        == request_id
    ]
    outcomes = [
        row
        for row in events
        if row.event_type in {config["succeeded"], config["failed"]}
        and str((row.event_payload or {}).get("request_event_id") or "")
        == request_id
        and str((row.event_payload or {}).get("dispatch_request_id") or "")
        == str(dispatch_request_id)
    ]
    exhausted = next(
        (
            row
            for row in events
            if row.event_type == config["exhausted"]
            and str((row.event_payload or {}).get("request_event_id") or "")
            == request_id
        ),
        None,
    )
    now = _utc_now()
    reason = "initial_dispatch"
    previous_reservation_id: str | None = None
    attempt_number = 1
    if reservations:
        reservations_by_attempt: dict[int, FoundryCandidateEvent] = {}
        for row in reservations:
            raw_attempt = (row.event_payload or {}).get("attempt_number")
            if (
                type(raw_attempt) is not int
                or not 1 <= raw_attempt <= _MAX_DISPATCH_ATTEMPTS
                or raw_attempt in reservations_by_attempt
            ):
                raise FoundryCatalogError(
                    f"{config['error_prefix']}_ledger_invalid",
                    "Dispatch reservation ledger is invalid",
                    status_code=409,
                )
            reservations_by_attempt[raw_attempt] = row
        latest_attempt = max(reservations_by_attempt)
        latest = reservations_by_attempt[latest_attempt]
        previous_reservation_id = str(latest.id)
        matching_outcome = next(
            (
                row
                for row in reversed(outcomes)
                if str(
                    (row.event_payload or {}).get("reservation_event_id") or ""
                )
                == previous_reservation_id
            ),
            None,
        )
        if (
            matching_outcome is not None
            and matching_outcome.event_type == config["failed"]
        ):
            outcome_payload = dict(matching_outcome.event_payload or {})
            if bool(outcome_payload.get("outcome_unknown")):
                retry_after = (
                    _as_utc(matching_outcome.created_at)
                    + _WORKFLOW_CALLBACK_TIMEOUT
                )
                if now < retry_after:
                    return MaterializationDispatchReservation(
                        request_event_id=request_event.id,
                        dispatch_request_id=dispatch_request_id,
                        reservation_event_id=latest.id,
                        attempt_number=latest_attempt,
                        should_dispatch=False,
                        state="DISPATCH_OUTCOME_UNKNOWN",
                        retry_after=retry_after,
                    )
                reason = "dispatch_outcome_unknown_timeout"
            elif not bool(outcome_payload.get("retryable")):
                raise FoundryCatalogError(
                    f"{config['error_prefix']}_not_retryable",
                    "The protected workflow dispatch failed permanently",
                    status_code=409,
                )
            else:
                reason = "retryable_dispatch_failure"
        elif matching_outcome is not None:
            retry_after = (
                _as_utc(matching_outcome.created_at) + _WORKFLOW_CALLBACK_TIMEOUT
            )
            if now < retry_after:
                return MaterializationDispatchReservation(
                    request_event_id=request_event.id,
                    dispatch_request_id=dispatch_request_id,
                    reservation_event_id=latest.id,
                    attempt_number=latest_attempt,
                    should_dispatch=False,
                    state="WORKFLOW_DISPATCHED",
                    retry_after=retry_after,
                )
            reason = "workflow_callback_timeout"
        else:
            retry_after = _as_utc(latest.created_at) + _DISPATCH_LEASE_TIMEOUT
            if now < retry_after:
                return MaterializationDispatchReservation(
                    request_event_id=request_event.id,
                    dispatch_request_id=dispatch_request_id,
                    reservation_event_id=latest.id,
                    attempt_number=latest_attempt,
                    should_dispatch=False,
                    state="DISPATCH_RESERVED",
                    retry_after=retry_after,
                )
            reason = "dispatch_lease_expired"
        attempt_number = latest_attempt + 1
    else:
        # Preserve bounded recovery only for a real pre-attempt-ledger
        # DISPATCHED record. A legacy failure or unrelated event must never be
        # promoted into an active workflow lease.
        legacy = next(
            (
                row
                for row in reversed(events)
                if _legacy_dispatch_success_binds(
                    row,
                    request_event_id=request_event.id,
                    dispatch_request_id=dispatch_request_id,
                    lane=lane,
                )
            ),
            None,
        )
        if legacy is not None:
            retry_after = _as_utc(legacy.created_at) + _WORKFLOW_CALLBACK_TIMEOUT
            if now < retry_after:
                return MaterializationDispatchReservation(
                    request_event_id=request_event.id,
                    dispatch_request_id=dispatch_request_id,
                    reservation_event_id=legacy.id,
                    attempt_number=1,
                    should_dispatch=False,
                    state="WORKFLOW_DISPATCHED",
                    retry_after=retry_after,
                )
            attempt_number = 2
            previous_reservation_id = str(legacy.id)
            reason = "legacy_workflow_callback_timeout"
    if exhausted is not None or attempt_number > _MAX_DISPATCH_ATTEMPTS:
        if exhausted is None:
            await _append_event(
                db,
                candidate_id=request_event.candidate_id,
                candidate_version_id=request_event.candidate_version_id,
                event_type=str(config["exhausted"]),
                actor_kind="CONTROL_PLANE",
                actor_user_id=None,
                payload={
                    "request_event_id": request_id,
                    "dispatch_request_id": str(dispatch_request_id),
                    "attempts_exhausted": _MAX_DISPATCH_ATTEMPTS,
                    "last_reservation_event_id": previous_reservation_id,
                },
            )
            await db.commit()
        raise FoundryCatalogError(
            f"{config['error_prefix']}_retry_exhausted",
            "Protected workflow retry budget is exhausted",
            status_code=409,
        )
    reservation = await _append_event(
        db,
        candidate_id=request_event.candidate_id,
        candidate_version_id=request_event.candidate_version_id,
        event_type=str(config["reserved"]),
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload={
            "request_event_id": request_id,
            "dispatch_request_id": str(dispatch_request_id),
            "attempt_number": attempt_number,
            "max_attempts": _MAX_DISPATCH_ATTEMPTS,
            "dispatch_lease_seconds": int(_DISPATCH_LEASE_TIMEOUT.total_seconds()),
            "workflow_timeout_seconds": int(
                _WORKFLOW_CALLBACK_TIMEOUT.total_seconds()
            ),
            "retry_reason": reason,
            "previous_reservation_event_id": previous_reservation_id,
        },
    )
    retry_after = _as_utc(reservation.created_at) + _DISPATCH_LEASE_TIMEOUT
    return MaterializationDispatchReservation(
        request_event_id=request_event.id,
        dispatch_request_id=dispatch_request_id,
        reservation_event_id=reservation.id,
        attempt_number=attempt_number,
        should_dispatch=True,
        state="DISPATCH_RESERVED",
        retry_after=retry_after,
    )


def _hash(value: Any, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _HEX64.fullmatch(normalized):
        raise FoundryCatalogError(
            "materialization_hash_invalid", f"{field} must be a SHA-256 digest"
        )
    return normalized


def _parse_time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise FoundryCatalogError(
            "materialization_time_invalid", f"{field} must be an ISO-8601 time"
        ) from exc
    if parsed.tzinfo is None:
        raise FoundryCatalogError(
            "materialization_time_invalid", f"{field} must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


def _verify_signed_bundle(
    bundle: dict[str, Any],
    *,
    expected_payload_schema: str,
    trusted_public_keys: Mapping[str, str],
) -> tuple[dict[str, Any], str, str, str]:
    if not isinstance(bundle, dict):
        raise FoundryCatalogError(
            "materialization_attestation_invalid", "Attestation must be an object"
        )
    envelope = json.loads(canonical_json(bundle))
    receipt_hash = str(envelope.pop("receipt_sha256", ""))
    if _hash(receipt_hash, "receipt_sha256") != sha256_json(envelope):
        raise FoundryCatalogError(
            "materialization_receipt_hash_mismatch",
            "Materialization receipt hash does not match its envelope",
        )
    if set(envelope) != {"schema_version", "payload", "payload_sha256", "signature"}:
        raise FoundryCatalogError(
            "materialization_attestation_invalid", "Attestation shape is not registered"
        )
    if envelope.get("schema_version") != _BUNDLE_SCHEMA:
        raise FoundryCatalogError(
            "materialization_attestation_invalid", "Attestation bundle version is unsupported"
        )
    payload = envelope.get("payload")
    signature = envelope.get("signature")
    if not isinstance(payload, dict) or payload.get("schema_version") != expected_payload_schema:
        raise FoundryCatalogError(
            "materialization_attestation_invalid", "Signed payload version is unsupported"
        )
    if not isinstance(signature, dict) or set(signature) != {"algorithm", "key_id", "value"}:
        raise FoundryCatalogError(
            "materialization_signature_invalid", "Materialization signature shape is invalid"
        )
    canonical_payload = canonical_json(payload)
    payload_hash = _hash(envelope.get("payload_sha256"), "payload_sha256")
    if not hmac.compare_digest(payload_hash, hashlib.sha256(canonical_payload).hexdigest()):
        raise FoundryCatalogError(
            "materialization_payload_hash_mismatch", "Signed payload hash does not match"
        )
    key_id = str(signature.get("key_id") or "")
    encoded_key = str(trusted_public_keys.get(key_id) or "")
    if signature.get("algorithm") != "ed25519" or not encoded_key:
        raise FoundryCatalogError(
            "materialization_signing_key_untrusted", "Materialization signing key is not trusted"
        )
    try:
        public_key = base64.b64decode(encoded_key, validate=True)
        signature_bytes = base64.b64decode(str(signature.get("value") or ""), validate=True)
        if len(public_key) != 32 or len(signature_bytes) != 64:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature_bytes, _MATERIALIZATION_DOMAIN + canonical_payload
        )
    except (binascii.Error, InvalidSignature, TypeError, ValueError) as exc:
        raise FoundryCatalogError(
            "materialization_signature_invalid", "Materialization signature is invalid"
        ) from exc
    return payload, payload_hash, receipt_hash, key_id


async def _approved_origin(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    candidate_version_id: uuid.UUID,
    candidate_version_hash: str,
) -> tuple[FoundryCandidate, FoundryCandidateVersion]:
    candidate = await db.scalar(
        select(FoundryCandidate)
        .where(FoundryCandidate.id == candidate_id)
        .with_for_update()
    )
    version = await db.get(FoundryCandidateVersion, candidate_version_id)
    if (
        candidate is None
        or version is None
        or version.candidate_id != candidate_id
        or candidate.current_version_number != version.version_number
        or candidate.status != "APPROVED"
        or version.version_hash != _hash(candidate_version_hash, "candidate_version_hash")
    ):
        raise FoundryCatalogError(
            "materialization_candidate_binding_mismatch",
            "Materialization requires the exact approved current Candidate version",
            status_code=409,
        )
    generation = dict(version.ai_generation_config or {})
    if (
        version.created_by_kind != "AI_DRAFT_JOB"
        or generation.get("source_hash_algorithm")
        != "standard_astro_tracked_source_manifest_v1"
        or generation.get("source_materialization_required") is not True
        or not _GIT_SHA.fullmatch(str(generation.get("source_base_commit") or ""))
        or not _HEX64.fullmatch(str(generation.get("source_tree_sha256") or ""))
        or not _HEX64.fullmatch(str(version.patch_hash or ""))
    ):
        raise FoundryCatalogError(
            "materialization_not_required",
            "Only an AI Draft with an immutable non-empty allowlisted patch can enter this lane",
            status_code=409,
        )
    demo = await db.scalar(
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
                select(FoundryReview).where(FoundryReview.candidate_version_id == version.id)
            )
        ).scalars().all()
    )
    if demo is None or not _review_requirements_satisfied(str(candidate.risk_level or ""), reviews):
        raise FoundryCatalogError(
            "materialization_review_missing",
            "An exact-version PASSED Demo and required workflow reviews must precede materialization",
            status_code=409,
        )
    return candidate, version


async def request_source_materialization(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    candidate_version_id: uuid.UUID,
    candidate_version_hash: str,
    actor_user_id: uuid.UUID,
) -> tuple[
    dict[str, Any],
    FoundryCandidateEvent,
    MaterializationDispatchReservation | None,
    FoundryMaterializationAttestation | None,
]:
    """Create/recover a server-reconstructed request for the protected PR workflow."""

    candidate, version = await _approved_origin(
        db,
        candidate_id=candidate_id,
        candidate_version_id=candidate_version_id,
        candidate_version_hash=candidate_version_hash,
    )
    draft = await ai_draft_validation_binding(db, version=version)
    if draft is None:
        raise FoundryCatalogError(
            "materialization_draft_receipt_missing", "AI Draft artifact binding is missing", status_code=409
        )
    expected_path = f"backend/app/services/foundry_generated/{version.candidate_key}.py"
    generation = dict(version.ai_generation_config or {})
    if (
        version.patch_hash == hashlib.sha256(b"").hexdigest()
        or generation.get("source_tree_sha256") != draft["post_patch_source_tree_sha256"]
    ):
        raise FoundryCatalogError(
            "materialization_source_binding_invalid", "Draft patch/source binding is inconsistent", status_code=409
        )
    existing_attestation = await db.scalar(
        select(FoundryMaterializationAttestation).where(
            FoundryMaterializationAttestation.origin_candidate_version_id == version.id
        )
    )
    events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent)
                .where(
                    FoundryCandidateEvent.candidate_id == candidate.id,
                    FoundryCandidateEvent.candidate_version_id == version.id,
                    FoundryCandidateEvent.event_type == "SOURCE_MATERIALIZATION_REQUESTED",
                )
                .order_by(FoundryCandidateEvent.created_at.asc(), FoundryCandidateEvent.id.asc())
            )
        ).scalars().all()
    )
    if events:
        request = events[0]
        binding = {
            **dict(request.event_payload or {}),
            "materialization_request_id": str(request.id),
        }
        if existing_attestation is not None:
            await db.commit()
            return binding, request, None, existing_attestation
        reservation = await _reserve_dispatch_attempt(
            db,
            request_event=request,
            dispatch_request_id=request.id,
            lane="materialization",
        )
        await db.commit()
        return binding, request, reservation, None

    branch = f"foundry/materialize-{candidate.id.hex[:12]}-v{version.version_number}-{version.version_hash[:12]}"
    binding: dict[str, Any] = {
        "schema_version": "standard_astro_materialization_request_v1",
        "candidate_id": str(candidate.id),
        "origin_candidate_version_id": str(version.id),
        "origin_candidate_version_hash": version.version_hash,
        "candidate_key": version.candidate_key,
        "candidate_module_path": expected_path,
        "draft_run_id": draft["draft_run_id"],
        "artifact_repository": draft["artifact_repository"],
        "artifact_workflow_run_id": draft["artifact_workflow_run_id"],
        "artifact_id": draft["artifact_id"],
        "artifact_name": draft["artifact_name"],
        "artifact_sha256": draft["artifact_sha256"],
        "base_commit": draft["base_commit"],
        "base_source_tree_sha256": draft["base_source_tree_sha256"],
        "post_patch_source_tree_sha256": draft["post_patch_source_tree_sha256"],
        "patch_sha256": draft["patch_sha256"],
        "candidate_bundle_sha256": draft["candidate_bundle_hash"],
        "branch_name": branch,
        "auto_merge_allowed": False,
        "candidate_code_execution_allowed": False,
    }
    request = await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="SOURCE_MATERIALIZATION_REQUESTED",
        actor_kind="HUMAN_REVIEWER",
        actor_user_id=actor_user_id,
        payload=binding,
    )
    reservation = await _reserve_dispatch_attempt(
        db,
        request_event=request,
        dispatch_request_id=request.id,
        lane="materialization",
    )
    await db.commit()
    await db.refresh(request)
    return (
        {**binding, "materialization_request_id": str(request.id)},
        request,
        reservation,
        None,
    )


async def _record_dispatch_outcome(
    db: AsyncSession,
    *,
    request_event: FoundryCandidateEvent,
    reservation: MaterializationDispatchReservation,
    lane: str,
    dispatched: bool,
    failure_class: str | None,
    retryable: bool,
    outcome_unknown: bool,
) -> FoundryCandidateEvent:
    config = _DISPATCH_LANES.get(lane)
    if config is None:
        raise ValueError("materialization_dispatch_lane_invalid")
    candidate = await db.scalar(
        select(FoundryCandidate.id)
        .where(FoundryCandidate.id == request_event.candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise FoundryCatalogError(
            "candidate_not_found", "Candidate not found", status_code=404
        )
    if (
        reservation.request_event_id != request_event.id
        or not reservation.should_dispatch
        or not 1 <= reservation.attempt_number <= _MAX_DISPATCH_ATTEMPTS
    ):
        raise FoundryCatalogError(
            f"{config['error_prefix']}_reservation_invalid",
            "Dispatch outcome is not bound to an active reservation",
            status_code=409,
        )
    reservation_event = await db.get(
        FoundryCandidateEvent, reservation.reservation_event_id
    )
    reservation_payload = (
        dict(reservation_event.event_payload or {})
        if reservation_event is not None
        else {}
    )
    if (
        reservation_event is None
        or reservation_event.event_type != config["reserved"]
        or reservation_event.candidate_id != request_event.candidate_id
        or reservation_event.candidate_version_id
        != request_event.candidate_version_id
        or reservation_payload.get("request_event_id") != str(request_event.id)
        or reservation_payload.get("dispatch_request_id")
        != str(reservation.dispatch_request_id)
        or reservation_payload.get("attempt_number") != reservation.attempt_number
    ):
        raise FoundryCatalogError(
            f"{config['error_prefix']}_reservation_invalid",
            "Dispatch outcome is not bound to the durable reservation",
            status_code=409,
        )
    outcome_types = {str(config["succeeded"]), str(config["failed"])}
    outcomes = list(
        (
            await db.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id
                    == request_event.candidate_id,
                    FoundryCandidateEvent.candidate_version_id
                    == request_event.candidate_version_id,
                    FoundryCandidateEvent.event_type.in_(tuple(outcome_types)),
                )
            )
        ).scalars().all()
    )
    existing = next(
        (
            row
            for row in outcomes
            if str((row.event_payload or {}).get("reservation_event_id") or "")
            == str(reservation.reservation_event_id)
        ),
        None,
    )
    event_type = str(config["succeeded"] if dispatched else config["failed"])
    unknown = bool(outcome_unknown) if not dispatched else False
    failure = None
    if not dispatched:
        failure = str(failure_class or f"{config['error_prefix']}_failed")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,127}", failure):
            failure = f"{config['error_prefix']}_failed"
    if existing is not None:
        existing_payload = dict(existing.event_payload or {})
        if (
            existing.event_type != event_type
            or existing_payload.get("failure_class") != failure
            or bool(existing_payload.get("retryable"))
            != (bool(retryable) if not dispatched else False)
            or bool(existing_payload.get("outcome_unknown")) != unknown
        ):
            raise FoundryCatalogError(
                f"{config['error_prefix']}_outcome_conflict",
                "Dispatch reservation already has a different outcome",
                status_code=409,
            )
        return existing
    event = await _append_event(
        db,
        candidate_id=request_event.candidate_id,
        candidate_version_id=request_event.candidate_version_id,
        event_type=event_type,
        actor_kind="CONTROL_PLANE",
        actor_user_id=None,
        payload={
            "request_event_id": str(request_event.id),
            "dispatch_request_id": str(reservation.dispatch_request_id),
            "reservation_event_id": str(reservation.reservation_event_id),
            "attempt_number": reservation.attempt_number,
            "failure_class": failure,
            "retryable": bool(retryable) if not dispatched else False,
            "outcome_unknown": unknown,
        },
    )
    await db.commit()
    await db.refresh(event)
    return event


async def record_materialization_dispatch(
    db: AsyncSession,
    *,
    request_event: FoundryCandidateEvent,
    reservation: MaterializationDispatchReservation,
    dispatched: bool,
    failure_class: str | None = None,
    retryable: bool = False,
    outcome_unknown: bool = False,
) -> FoundryCandidateEvent:
    return await _record_dispatch_outcome(
        db,
        request_event=request_event,
        reservation=reservation,
        lane="materialization",
        dispatched=dispatched,
        failure_class=failure_class,
        retryable=retryable,
        outcome_unknown=outcome_unknown,
    )


def _expected_workflow_ref(repository: str, workflow: str) -> str:
    return f"{repository}/.github/workflows/{workflow}@refs/heads/main"


async def record_materialization_pr_attestation(
    db: AsyncSession,
    *,
    attestation_bundle: dict[str, Any],
    expected_repository: str,
    expected_workflow: str,
    trusted_public_keys: Mapping[str, str],
) -> FoundryMaterializationAttestation:
    payload, payload_hash, receipt_hash, key_id = _verify_signed_bundle(
        attestation_bundle,
        expected_payload_schema=_PR_PAYLOAD_SCHEMA,
        trusted_public_keys=trusted_public_keys,
    )
    required = {
        "schema_version", "attestation_id", "materialization_request_id", "candidate_id",
        "origin_candidate_version_id", "origin_candidate_version_hash", "draft_run_id",
        "artifact_repository", "artifact_workflow_run_id", "artifact_id", "artifact_name",
        "artifact_sha256", "base_commit", "base_source_tree_sha256",
        "post_patch_source_tree_sha256", "patch_sha256", "candidate_module_path",
        "candidate_module_sha256", "branch_name", "pull_request_number", "pull_request_state", "pull_request_url",
        "pull_request_head_commit", "pull_request_head_tree_sha256", "github_repository",
        "github_workflow_ref", "github_workflow_sha", "github_run_id", "github_run_attempt",
        "candidate_code_executed", "auto_merge_performed", "opened_at",
    }
    if set(payload) != required:
        raise FoundryCatalogError(
            "materialization_attestation_invalid", "PR attestation fields are not registered"
        )
    try:
        attestation_id = uuid.UUID(str(payload["attestation_id"]))
        request_id = uuid.UUID(str(payload["materialization_request_id"]))
        candidate_id = uuid.UUID(str(payload["candidate_id"]))
        version_id = uuid.UUID(str(payload["origin_candidate_version_id"]))
        draft_run_id = uuid.UUID(str(payload["draft_run_id"]))
    except ValueError as exc:
        raise FoundryCatalogError(
            "materialization_attestation_invalid", "PR attestation identifiers are invalid"
        ) from exc
    existing = await db.get(FoundryMaterializationAttestation, attestation_id)
    if existing is not None:
        if existing.payload_hash != payload_hash or existing.receipt_hash != receipt_hash:
            raise FoundryCatalogError(
                "materialization_attestation_conflict", "Attestation id already has different content", status_code=409
            )
        return existing
    request = await db.get(FoundryCandidateEvent, request_id)
    if (
        request is None
        or request.event_type != "SOURCE_MATERIALIZATION_REQUESTED"
        or request.candidate_id != candidate_id
        or request.candidate_version_id != version_id
    ):
        raise FoundryCatalogError(
            "materialization_request_not_found", "Exact materialization request was not found", status_code=404
        )
    dispatch_events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate_id,
                    FoundryCandidateEvent.candidate_version_id == version_id,
                    FoundryCandidateEvent.event_type.in_(
                        {
                            "SOURCE_MATERIALIZATION_DISPATCHED",
                            "SOURCE_MATERIALIZATION_DISPATCH_FAILED",
                        }
                    ),
                )
            )
        ).scalars().all()
    )
    if not any(
        _successful_dispatch_binds(
            event,
            request_event_id=request_id,
            dispatch_request_id=request_id,
            lane="materialization",
        )
        for event in dispatch_events
    ):
        raise FoundryCatalogError(
            "materialization_not_dispatched",
            "A PR attestation cannot precede the protected-workflow dispatch",
            status_code=409,
        )
    binding = dict(request.event_payload or {})
    compare = {
        "candidate_id": str(candidate_id),
        "origin_candidate_version_id": str(version_id),
        "origin_candidate_version_hash": str(payload["origin_candidate_version_hash"]),
        "draft_run_id": str(draft_run_id),
        "artifact_repository": str(payload["artifact_repository"]),
        "artifact_workflow_run_id": str(payload["artifact_workflow_run_id"]),
        "artifact_id": str(payload["artifact_id"]),
        "artifact_name": str(payload["artifact_name"]),
        "artifact_sha256": str(payload["artifact_sha256"]),
        "base_commit": str(payload["base_commit"]),
        "base_source_tree_sha256": str(payload["base_source_tree_sha256"]),
        "post_patch_source_tree_sha256": str(payload["post_patch_source_tree_sha256"]),
        "patch_sha256": str(payload["patch_sha256"]),
        "candidate_module_path": str(payload["candidate_module_path"]),
        "branch_name": str(payload["branch_name"]),
    }
    if (
        str(payload["materialization_request_id"]) != str(request_id)
        or any(binding.get(key) != value for key, value in compare.items())
    ):
        raise FoundryCatalogError(
            "materialization_attestation_binding_mismatch", "PR receipt differs from the server request", status_code=409
        )
    repository = str(expected_repository or "")
    workflow = str(expected_workflow or "")
    pr_number = payload.get("pull_request_number")
    if (
        not _REPOSITORY.fullmatch(repository)
        or not _WORKFLOW.fullmatch(workflow)
        or payload.get("github_repository") != repository
        or payload.get("github_workflow_ref") != _expected_workflow_ref(repository, workflow)
        or not _GIT_SHA.fullmatch(str(payload.get("github_workflow_sha") or ""))
        or not str(payload.get("github_run_id") or "").isdigit()
        or type(payload.get("github_run_attempt")) is not int
        or int(payload["github_run_attempt"]) < 1
        or type(pr_number) is not int
        or not 1 <= pr_number <= 2_147_483_647
        or payload.get("pull_request_state") not in {"OPEN", "MERGED"}
        or payload.get("pull_request_url") != f"https://github.com/{repository}/pull/{pr_number}"
        or not _GIT_SHA.fullmatch(str(payload.get("pull_request_head_commit") or ""))
        or payload.get("pull_request_head_tree_sha256")
        != binding["post_patch_source_tree_sha256"]
        or not _MODULE_PATH.fullmatch(str(payload.get("candidate_module_path") or ""))
        or not _HEX64.fullmatch(str(payload.get("candidate_module_sha256") or ""))
        or payload.get("candidate_code_executed") is not False
        or payload.get("auto_merge_performed") is not False
    ):
        raise FoundryCatalogError(
            "materialization_pr_identity_invalid", "PR receipt identity or safety assertions are invalid"
        )
    row = FoundryMaterializationAttestation(
        id=attestation_id,
        candidate_id=candidate_id,
        origin_candidate_version_id=version_id,
        origin_candidate_version_hash=_hash(payload["origin_candidate_version_hash"], "origin hash"),
        materialization_request_event_id=request_id,
        draft_run_id=draft_run_id,
        artifact_repository=str(payload["artifact_repository"]),
        artifact_workflow_run_id=str(payload["artifact_workflow_run_id"]),
        artifact_id=str(payload["artifact_id"]),
        artifact_name=str(payload["artifact_name"]),
        artifact_hash=_hash(payload["artifact_sha256"], "artifact hash"),
        base_commit=str(payload["base_commit"]),
        base_source_tree_hash=_hash(payload["base_source_tree_sha256"], "base tree"),
        draft_source_tree_hash=_hash(payload["post_patch_source_tree_sha256"], "draft tree"),
        patch_hash=_hash(payload["patch_sha256"], "patch hash"),
        candidate_module_path=str(payload["candidate_module_path"]),
        candidate_module_hash=_hash(payload["candidate_module_sha256"], "module hash"),
        branch_name=str(payload["branch_name"]),
        pull_request_number=pr_number,
        pull_request_state=str(payload["pull_request_state"]),
        pull_request_url=str(payload["pull_request_url"]),
        pull_request_head_commit=str(payload["pull_request_head_commit"]),
        pull_request_head_tree_hash=_hash(payload["pull_request_head_tree_sha256"], "head tree"),
        github_repository=repository,
        github_workflow_ref=str(payload["github_workflow_ref"]),
        github_workflow_sha=str(payload["github_workflow_sha"]),
        github_run_id=str(payload["github_run_id"]),
        github_run_attempt=int(payload["github_run_attempt"]),
        signing_key_id=key_id,
        payload_hash=payload_hash,
        receipt_hash=receipt_hash,
        opened_at=_parse_time(payload["opened_at"], "opened_at"),
    )
    request_time = request.created_at
    if request_time.tzinfo is None:
        request_time = request_time.replace(tzinfo=timezone.utc)
    if row.opened_at < request_time.astimezone(timezone.utc):
        raise FoundryCatalogError(
            "materialization_attestation_precedes_request",
            "PR attestation time precedes its human request",
            status_code=409,
        )
    db.add(row)
    await db.flush()
    await _append_event(
        db,
        candidate_id=candidate_id,
        candidate_version_id=version_id,
        event_type="SOURCE_MATERIALIZATION_PR_ATTESTED",
        actor_kind="PROTECTED_MATERIALIZATION_CALLBACK",
        actor_user_id=None,
        payload={
            "materialization_attestation_id": str(row.id),
            "materialization_request_id": str(request_id),
            "pull_request_number": row.pull_request_number,
            "pull_request_head_commit": row.pull_request_head_commit,
            "receipt_sha256": row.receipt_hash,
            "signing_key_id": row.signing_key_id,
            "auto_merge_performed": False,
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


async def request_materialization_finalization(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    attestation_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> tuple[
    dict[str, Any],
    FoundryCandidateEvent,
    MaterializationDispatchReservation | None,
    FoundryMaterializationReceipt | None,
]:
    attestation = await db.get(FoundryMaterializationAttestation, attestation_id)
    if attestation is None or attestation.candidate_id != candidate_id:
        raise FoundryCatalogError(
            "materialization_attestation_not_found", "Materialization PR receipt not found", status_code=404
        )
    candidate = await db.scalar(
        select(FoundryCandidate).where(FoundryCandidate.id == candidate_id).with_for_update()
    )
    if candidate is None or candidate.current_version_number is None:
        raise FoundryCatalogError("candidate_not_found", "Candidate not found", status_code=404)
    origin = await db.get(FoundryCandidateVersion, attestation.origin_candidate_version_id)
    if (
        origin is None
        or candidate.status != "APPROVED"
        or candidate.current_version_number != origin.version_number
        or origin.version_hash != attestation.origin_candidate_version_hash
    ):
        raise FoundryCatalogError(
            "materialization_origin_no_longer_current",
            "The reviewed origin version changed before merge finalization",
            status_code=409,
        )
    receipt = await db.scalar(
        select(FoundryMaterializationReceipt).where(
            FoundryMaterializationReceipt.materialization_attestation_id == attestation.id
        )
    )
    events = list(
        (
            await db.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate.id,
                    FoundryCandidateEvent.candidate_version_id == origin.id,
                    FoundryCandidateEvent.event_type == "SOURCE_MATERIALIZATION_FINALIZATION_REQUESTED",
                )
            )
        ).scalars().all()
    )
    if events:
        event = events[0]
        if receipt is not None:
            await db.commit()
            return dict(event.event_payload or {}), event, None, receipt
        reservation = await _reserve_dispatch_attempt(
            db,
            request_event=event,
            dispatch_request_id=attestation.id,
            lane="finalization",
        )
        await db.commit()
        return dict(event.event_payload or {}), event, reservation, None
    binding = {
        "schema_version": "standard_astro_materialization_finalization_request_v1",
        "candidate_id": str(candidate.id),
        "origin_candidate_version_id": str(origin.id),
        "origin_candidate_version_hash": origin.version_hash,
        "materialization_attestation_id": str(attestation.id),
        "materialization_attestation_receipt_sha256": attestation.receipt_hash,
        "materialization_request_id": str(attestation.materialization_request_event_id),
        "draft_run_id": str(attestation.draft_run_id),
        "artifact_repository": attestation.artifact_repository,
        "artifact_workflow_run_id": attestation.artifact_workflow_run_id,
        "artifact_id": attestation.artifact_id,
        "artifact_name": attestation.artifact_name,
        "artifact_sha256": attestation.artifact_hash,
        "base_commit": attestation.base_commit,
        "base_source_tree_sha256": attestation.base_source_tree_hash,
        "post_patch_source_tree_sha256": attestation.draft_source_tree_hash,
        "patch_sha256": attestation.patch_hash,
        "candidate_module_path": attestation.candidate_module_path,
        "candidate_module_sha256": attestation.candidate_module_hash,
        "branch_name": attestation.branch_name,
        "pull_request_number": attestation.pull_request_number,
        "pull_request_head_commit": attestation.pull_request_head_commit,
        "pull_request_head_tree_sha256": attestation.pull_request_head_tree_hash,
        "candidate_code_execution_allowed": False,
    }
    event = await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=origin.id,
        event_type="SOURCE_MATERIALIZATION_FINALIZATION_REQUESTED",
        actor_kind="HUMAN_REVIEWER",
        actor_user_id=actor_user_id,
        payload=binding,
    )
    reservation = await _reserve_dispatch_attempt(
        db,
        request_event=event,
        dispatch_request_id=attestation.id,
        lane="finalization",
    )
    await db.commit()
    await db.refresh(event)
    return binding, event, reservation, None


async def record_finalization_dispatch(
    db: AsyncSession,
    *,
    request_event: FoundryCandidateEvent,
    reservation: MaterializationDispatchReservation,
    dispatched: bool,
    failure_class: str | None = None,
    retryable: bool = False,
    outcome_unknown: bool = False,
) -> FoundryCandidateEvent:
    return await _record_dispatch_outcome(
        db,
        request_event=request_event,
        reservation=reservation,
        lane="finalization",
        dispatched=dispatched,
        failure_class=failure_class,
        retryable=retryable,
        outcome_unknown=outcome_unknown,
    )


async def record_materialization_final_receipt(
    db: AsyncSession,
    *,
    attestation_bundle: dict[str, Any],
    expected_repository: str,
    expected_workflow: str,
    trusted_public_keys: Mapping[str, str],
) -> tuple[FoundryMaterializationReceipt, FoundryCandidateVersion]:
    payload, payload_hash, receipt_hash, key_id = _verify_signed_bundle(
        attestation_bundle,
        expected_payload_schema=_FINAL_PAYLOAD_SCHEMA,
        trusted_public_keys=trusted_public_keys,
    )
    required = {
        "schema_version", "receipt_id", "materialization_attestation_id", "candidate_id",
        "origin_candidate_version_id", "origin_candidate_version_hash", "pull_request_number",
        "pull_request_head_commit", "pull_request_base_ref",
        "pull_request_head_repository", "merge_commit", "origin_main_commit",
        "merge_commit_is_ancestor_of_origin_main", "merge_source_tree_sha256",
        "candidate_module_path", "candidate_module_sha256", "patch_sha256",
        "dependency_lock_sha256", "runner_definition_sha256", "validation_runner_image_digest",
        "validation_sbom_sha256",
        "github_repository", "github_workflow_ref", "github_workflow_sha", "github_run_id",
        "github_run_attempt", "source_was_merged", "candidate_code_executed",
        "validation_image_built_without_execution", "finalized_at",
    }
    if set(payload) != required:
        raise FoundryCatalogError(
            "materialization_final_receipt_invalid", "Final receipt fields are not registered"
        )
    try:
        receipt_id = uuid.UUID(str(payload["receipt_id"]))
        attestation_id = uuid.UUID(str(payload["materialization_attestation_id"]))
        candidate_id = uuid.UUID(str(payload["candidate_id"]))
        origin_id = uuid.UUID(str(payload["origin_candidate_version_id"]))
    except ValueError as exc:
        raise FoundryCatalogError(
            "materialization_final_receipt_invalid", "Final receipt identifiers are invalid"
        ) from exc
    existing = await db.get(FoundryMaterializationReceipt, receipt_id)
    if existing is not None:
        if existing.payload_hash != payload_hash or existing.receipt_hash != receipt_hash:
            raise FoundryCatalogError(
                "materialization_final_receipt_conflict", "Receipt id already has different content", status_code=409
            )
        version = await db.get(FoundryCandidateVersion, existing.materialized_candidate_version_id)
        if version is None:
            raise FoundryCatalogError("materialization_version_missing", "Materialized version is missing", status_code=409)
        return existing, version
    attestation = await db.get(FoundryMaterializationAttestation, attestation_id)
    candidate = await db.scalar(
        select(FoundryCandidate).where(FoundryCandidate.id == candidate_id).with_for_update()
    )
    origin = await db.get(FoundryCandidateVersion, origin_id)
    if (
        attestation is None
        or candidate is None
        or origin is None
        or attestation.candidate_id != candidate_id
        or attestation.origin_candidate_version_id != origin_id
        or candidate.current_version_number != origin.version_number
        or candidate.status != "APPROVED"
        or origin.version_hash != str(payload["origin_candidate_version_hash"])
    ):
        raise FoundryCatalogError(
            "materialization_final_binding_mismatch", "Final receipt is not bound to the current reviewed origin", status_code=409
        )
    finalization_request = await db.scalar(
        select(FoundryCandidateEvent).where(
            FoundryCandidateEvent.candidate_id == candidate_id,
            FoundryCandidateEvent.candidate_version_id == origin_id,
            FoundryCandidateEvent.event_type
            == "SOURCE_MATERIALIZATION_FINALIZATION_REQUESTED",
        )
    )
    finalization_dispatches = list(
        (
            await db.execute(
                select(FoundryCandidateEvent).where(
                    FoundryCandidateEvent.candidate_id == candidate_id,
                    FoundryCandidateEvent.candidate_version_id == origin_id,
                    FoundryCandidateEvent.event_type.in_(
                        {
                            "SOURCE_MATERIALIZATION_FINALIZATION_DISPATCHED",
                            "SOURCE_MATERIALIZATION_FINALIZATION_DISPATCH_FAILED",
                        }
                    ),
                )
            )
        ).scalars().all()
    )
    if (
        finalization_request is None
        or not any(
            _successful_dispatch_binds(
                event,
                request_event_id=finalization_request.id,
                dispatch_request_id=attestation_id,
                lane="finalization",
            )
            for event in finalization_dispatches
        )
        or (finalization_request.event_payload or {}).get(
            "materialization_attestation_id"
        )
        != str(attestation_id)
    ):
        raise FoundryCatalogError(
            "materialization_finalization_not_dispatched",
            "A final receipt requires an exact human request and protected dispatch",
            status_code=409,
        )
    repository = str(expected_repository or "")
    workflow = str(expected_workflow or "")
    merge_commit = str(payload.get("merge_commit") or "").lower()
    origin_main_commit = str(payload.get("origin_main_commit") or "").lower()
    merge_tree = str(payload.get("merge_source_tree_sha256") or "").lower()
    image_digest = str(payload.get("validation_runner_image_digest") or "").lower()
    if (
        payload.get("github_repository") != repository
        or payload.get("github_workflow_ref") != _expected_workflow_ref(repository, workflow)
        or not _GIT_SHA.fullmatch(str(payload.get("github_workflow_sha") or ""))
        or not str(payload.get("github_run_id") or "").isdigit()
        or type(payload.get("github_run_attempt")) is not int
        or int(payload["github_run_attempt"]) < 1
        or payload.get("pull_request_number") != attestation.pull_request_number
        or payload.get("pull_request_head_commit") != attestation.pull_request_head_commit
        or payload.get("pull_request_base_ref") != "main"
        or payload.get("pull_request_head_repository") != repository
        or attestation.github_repository != repository
        or payload.get("candidate_module_path") != attestation.candidate_module_path
        or payload.get("candidate_module_sha256") != attestation.candidate_module_hash
        or payload.get("patch_sha256") != attestation.patch_hash
        or not _GIT_SHA.fullmatch(merge_commit)
        or merge_commit == attestation.base_commit
        or not _GIT_SHA.fullmatch(origin_main_commit)
        or origin_main_commit != payload.get("github_workflow_sha")
        or payload.get("merge_commit_is_ancestor_of_origin_main") is not True
        or not _HEX64.fullmatch(merge_tree)
        or not _IMAGE_DIGEST.fullmatch(image_digest)
        or payload.get("source_was_merged") is not True
        or payload.get("candidate_code_executed") is not False
        or payload.get("validation_image_built_without_execution") is not True
    ):
        raise FoundryCatalogError(
            "materialization_final_identity_invalid", "Merged source/image identity is invalid"
        )
    dependency_hash = _hash(payload["dependency_lock_sha256"], "dependency lock")
    runner_hash = _hash(payload["runner_definition_sha256"], "runner definition")
    validation_sbom_hash = _hash(payload["validation_sbom_sha256"], "validation SBOM")
    finalized_at = _parse_time(payload["finalized_at"], "finalized_at")
    request_time = finalization_request.created_at
    opened_at = attestation.opened_at
    if request_time.tzinfo is None:
        request_time = request_time.replace(tzinfo=timezone.utc)
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    if finalized_at < max(
        request_time.astimezone(timezone.utc), opened_at.astimezone(timezone.utc)
    ):
        raise FoundryCatalogError(
            "materialization_final_receipt_precedes_request",
            "Final receipt time precedes its PR or human finalization request",
            status_code=409,
        )
    bundle = json.loads(canonical_json(origin.candidate_bundle))
    generation = dict(bundle.get("generation") or {})
    generation.update(
        {
            "source_hash_algorithm": "standard_astro_tracked_source_manifest_v1",
            "source_base_commit": merge_commit,
            "source_base_tree_sha256": merge_tree,
            "source_tree_sha256": merge_tree,
            "source_materialization_required": False,
            "source_materialized": True,
            "source_materialization_receipt_id": str(receipt_id),
            "source_materialized_from_candidate_version_id": str(origin.id),
            "source_materialized_from_candidate_version_hash": origin.version_hash,
            "source_patch_sha256": attestation.patch_hash,
            "candidate_module_path": attestation.candidate_module_path,
            "candidate_module_sha256": attestation.candidate_module_hash,
        }
    )
    bundle["generation"] = generation
    bundle["dependency_lock_sha256"] = dependency_hash
    bundle["runner_definition_sha256"] = runner_hash
    bundle["candidate_version"] = origin.version_number + 1
    version = await append_candidate_version(
        db,
        candidate=candidate,
        draft={
            "candidate_bundle": bundle,
            "validation_runner_image_digest": image_digest,
            "code_tree_hash": merge_tree,
            "patch_hash": attestation.patch_hash,
            "sbom_hash": validation_sbom_hash,
        },
        actor_kind="PROTECTED_MATERIALIZATION",
        actor_user_id=None,
    )
    row = FoundryMaterializationReceipt(
        id=receipt_id,
        candidate_id=candidate.id,
        materialization_attestation_id=attestation.id,
        origin_candidate_version_id=origin.id,
        origin_candidate_version_hash=origin.version_hash,
        materialized_candidate_version_id=version.id,
        pull_request_number=attestation.pull_request_number,
        pull_request_head_commit=attestation.pull_request_head_commit,
        pull_request_base_ref="main",
        pull_request_head_repository=repository,
        merge_commit=merge_commit,
        origin_main_commit=origin_main_commit,
        merge_commit_is_ancestor_of_origin_main=True,
        merge_source_tree_hash=merge_tree,
        candidate_module_path=attestation.candidate_module_path,
        candidate_module_hash=attestation.candidate_module_hash,
        patch_hash=attestation.patch_hash,
        dependency_lock_hash=dependency_hash,
        runner_definition_hash=runner_hash,
        validation_sbom_hash=validation_sbom_hash,
        validation_runner_image_digest=image_digest,
        github_repository=repository,
        github_workflow_ref=str(payload["github_workflow_ref"]),
        github_workflow_sha=str(payload["github_workflow_sha"]),
        github_run_id=str(payload["github_run_id"]),
        github_run_attempt=int(payload["github_run_attempt"]),
        signing_key_id=key_id,
        payload_hash=payload_hash,
        receipt_hash=receipt_hash,
        finalized_at=finalized_at,
    )
    db.add(row)
    await db.flush()
    await _append_event(
        db,
        candidate_id=candidate.id,
        candidate_version_id=version.id,
        event_type="SOURCE_MATERIALIZATION_FINALIZED",
        actor_kind="PROTECTED_MATERIALIZATION_CALLBACK",
        actor_user_id=None,
        payload={
            "materialization_receipt_id": str(row.id),
            "origin_candidate_version_id": str(origin.id),
            "origin_candidate_version_hash": origin.version_hash,
            "materialized_candidate_version_id": str(version.id),
            "materialized_candidate_version_hash": version.version_hash,
            "merge_commit": merge_commit,
            "pull_request_base_ref": "main",
            "pull_request_head_repository": repository,
            "origin_main_commit": origin_main_commit,
            "merge_commit_is_ancestor_of_origin_main": True,
            "merge_source_tree_sha256": merge_tree,
            "validation_runner_image_digest": image_digest,
            "receipt_sha256": receipt_hash,
            "reviews_transferred": False,
            "demo_transferred": False,
            "candidate_status": "BUILDING",
        },
    )
    await db.commit()
    await db.refresh(row)
    await db.refresh(version)
    return row, version


__all__ = [
    "MaterializationDispatchReservation",
    "materialization_workflow_retry_after",
    "record_finalization_dispatch",
    "record_materialization_dispatch",
    "record_materialization_final_receipt",
    "record_materialization_pr_attestation",
    "request_materialization_finalization",
    "request_source_materialization",
]
