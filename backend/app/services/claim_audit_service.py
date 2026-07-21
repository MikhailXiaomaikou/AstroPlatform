"""Fail-closed orchestration and private evidence packs for Claim Audit."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.claim_audit_records import (
    ArtifactCleanupQueue,
    ClaimAudit,
    EvidencePack,
)
from app.models.research_records import ResearchJob
from app.models.schemas import User
from app.services.server_evidence import (
    build_research_job_attestation,
    build_scientific_attestation,
    scientific_content_hash,
    verify_scientific_attestation,
    verify_research_job_attestation,
)
from app.services.workflow_registry_v2 import (
    DESI_DR2_MATRIX_WORKFLOW_ID,
    WorkflowRegistryError,
    assert_workflow_executable,
    bind_formal_registry_record,
    get_formal_workflow_spec,
)


CLAIM_AUDIT_SCHEMA_VERSION = 1
EVIDENCE_PACK_SCHEMA_VERSION = 1
MAX_EVIDENCE_PACK_BYTES = 32 * 1024 * 1024
MAX_EVIDENCE_PACK_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_EVIDENCE_PACK_COMPRESSION_RATIO = 200
LIFECYCLE_STATUSES = frozenset(
    {
        "QUEUED",
        "RUNNING",
        "COMPLETED",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "CANCELLED",
    }
)
SCIENTIFIC_VERDICTS = frozenset({"SUPPORTED", "WITHHELD", "CAPABILITY_GAP"})
AUDIT_MODES = frozenset({"audit_only", "execute_registered"})
SOURCE_KINDS = frozenset({"doi", "arxiv", "bibcode", "url"})
_SAFE_SOURCE_HOSTS = frozenset(
    {
        "arxiv.org",
        "data.desi.lbl.gov",
        "desi.lbl.gov",
        "doi.org",
        "journals.aps.org",
        "ui.adsabs.harvard.edu",
        "www.desi.lbl.gov",
    }
)
_ARXIV_RE = re.compile(r"^(?:arxiv:)?\d{4}\.\d{4,5}(?:v\d+)?$", re.IGNORECASE)
_BIBCODE_RE = re.compile(r"^[12]\d{3}[A-Za-z0-9.&]{14,20}$")
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_CLAIM_BOUNDARY_RE = re.compile(r"(?:\r?\n)+|(?<=[。！？!?;；])\s*")
_STRICT_NUMERIC_CLAIM_RE = re.compile(
    r"^\s*(?:(?:the\s+)?(?:current|registered|this)\s+"
    r"(?:run|analysis)\s+(?:finds|gives|reports)\s+)?"
    r"(?:"
    r"(?:H0|H₀)\s*(?:=|is|≈|~)\s*"
    r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?\s*"
    r"(?:km\s*/\s*s\s*/\s*Mpc|km\s+s\^-1\s+Mpc\^-1|km\s+s⁻¹\s+Mpc⁻¹)"
    r"|"
    r"(?:Omega[_ ]?m|Ω[_ ]?m|omegam|sigma8|S8|w0|wa)\s*"
    r"(?:=|is|≈|~)\s*"
    r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?"
    r")"
    r"\s*[.]?\s*$",
    re.IGNORECASE,
)
logger = logging.getLogger(__name__)


class ClaimAuditInputError(ValueError):
    """The user request cannot enter the audit state machine."""


class RegisteredScientificIntegrityError(RuntimeError):
    """A pinned registered artifact exists but fails its scientific contract."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git_commit_identity() -> str:
    return str(
        os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("GIT_COMMIT")
        or "development"
    ).strip()


def _software_release_identity() -> str:
    return str(
        os.getenv("STANDARD_ASTRO_RELEASE")
        or os.getenv("APP_VERSION")
        or "unreleased"
    ).strip()


def _duration_bucket(started_at: datetime | None, ended_at: datetime | None) -> str:
    if started_at is None or ended_at is None:
        return "unknown"
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=timezone.utc)
    seconds = max(0.0, (ended_at - started_at).total_seconds())
    if seconds < 60:
        return "under_1m"
    if seconds < 10 * 60:
        return "1m_to_10m"
    return "over_10m"


async def _track_terminal_audit(audit: ClaimAudit) -> None:
    """Emit consent-gated coarse metadata; never block the audit lifecycle."""

    from app.services.event_collector import event_collector

    event_type = (
        "claim_audit.completed"
        if audit.lifecycle_status == "COMPLETED"
        else "claim_audit.failed"
    )
    tool_count = len(audit.evidence_record_ids or [])
    try:
        await event_collector.track(
            event_type,
            {
                "outcome_bucket": str(
                    audit.scientific_verdict or audit.lifecycle_status
                ).lower(),
                "duration_bucket": _duration_bucket(
                    audit.started_at,
                    audit.completed_at or datetime.now(timezone.utc),
                ),
                "tool_count_bucket": (
                    "0" if tool_count == 0 else "1-3" if tool_count <= 3 else "4+"
                ),
                "source_kind": audit.source_kind,
                "execution_mode": audit.mode,
                "error_class": audit.error_class,
                "retryable": audit.lifecycle_status == "FAILED_RETRYABLE",
            },
            user_id=audit.user_id,
        )
    except Exception:
        logger.exception("Could not buffer consent-gated Claim Audit metadata")


def registry_snapshot() -> str:
    from app.services.cosmology_likelihoods import list_cosmology_datasets

    registry = list_cosmology_datasets()
    return str(registry.get("registry_version") or "unknown")


def validate_source(kind: str, value: str) -> tuple[str, str]:
    normalized_kind = str(kind or "").strip().lower()
    normalized_value = str(value or "").strip()
    if normalized_kind not in SOURCE_KINDS:
        raise ClaimAuditInputError(
            f"source.kind must be one of {sorted(SOURCE_KINDS)}"
        )
    if not normalized_value or len(normalized_value) > 2048:
        raise ClaimAuditInputError("source.value must contain 1 to 2048 characters")
    if normalized_kind == "url":
        parsed = urlparse(normalized_value)
        host = str(parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in _SAFE_SOURCE_HOSTS:
            raise ClaimAuditInputError(
                "source URL must use HTTPS on an approved scholarly host"
            )
    elif normalized_kind == "arxiv":
        normalized_value = re.sub(
            r"^arxiv:", "", normalized_value, flags=re.IGNORECASE
        )
        if not _ARXIV_RE.fullmatch(normalized_value):
            raise ClaimAuditInputError("source.value is not a recognized arXiv identifier")
    elif normalized_kind == "bibcode" and not _BIBCODE_RE.fullmatch(normalized_value):
        raise ClaimAuditInputError("source.value is not a recognized ADS bibcode")
    elif normalized_kind == "doi":
        normalized_value = re.sub(
            r"^(?:doi:\s*|https?://doi\.org/)",
            "",
            normalized_value,
            flags=re.IGNORECASE,
        )
        if not _DOI_RE.fullmatch(normalized_value):
            raise ClaimAuditInputError("source.value is not a recognized DOI")
    return normalized_kind, normalized_value


def canonical_request_hash(
    *,
    claim_text: str,
    source_kind: str,
    source_value: str,
    evidence_input_refs: list[str],
    dataset_hints: list[str],
    mode: str,
) -> str:
    payload = {
        "schema_version": CLAIM_AUDIT_SCHEMA_VERSION,
        "claim_text": " ".join(str(claim_text).split()),
        "source": {
            "kind": source_kind,
            "value": source_value,
        },
        "evidence_input_refs": sorted(set(evidence_input_refs)),
        "dataset_hints": sorted(set(dataset_hints)),
        "mode": mode,
        "software_release": _software_release_identity(),
        "git_commit": _git_commit_identity(),
        "registry_snapshot": registry_snapshot(),
    }
    return _sha256(_canonical_json(payload))


def normalize_claims(claim_text: str) -> list[str]:
    """Split a user statement into bounded, stable claim units."""

    normalized = " ".join(str(claim_text or "").split())
    if not normalized:
        raise ClaimAuditInputError("claim_text cannot be empty")
    claims = [
        " ".join(part.split()).strip(" ;；")
        for part in _CLAIM_BOUNDARY_RE.split(normalized)
        if " ".join(part.split()).strip(" ;；")
    ]
    if len(claims) > 32:
        raise ClaimAuditInputError(
            "claim_text contains more than 32 claim units; split it into smaller audits"
        )
    return claims or [normalized]


def _claim_text_fully_covered(claim_text: str) -> bool:
    """P1 supports only one fully parsed numerical assertion per claim unit.

    The general fact extractor is deliberately broad, but an unparsed suffix
    must never inherit support from a verified number.  Expanding this grammar
    requires a new regression corpus and explicit evidence-path mapping.
    """

    return bool(_STRICT_NUMERIC_CLAIM_RE.fullmatch(str(claim_text or "")))


def load_structured_research_job_result(
    persisted_result: Any,
) -> dict[str, Any] | None:
    """Hydrate one signed job result and require a structured evidence envelope.

    Research jobs may persist arbitrary JSON values, but Claim Audit can only
    interpret mappings with explicit scientific-state fields.  A scalar or
    list must therefore remain unusable even when its completion HMAC is valid.
    """

    result = persisted_result
    if isinstance(persisted_result, dict) and persisted_result.get("_artifact_ref"):
        from app.services.durable_research_records import hydrate_result

        result = hydrate_result(persisted_result)
    return result if isinstance(result, dict) else None


async def _trusted_tool_results(
    db: AsyncSession,
    *,
    owner_id: Any,
    job_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    if not job_ids:
        return [], []
    rows = (
        await db.execute(
            select(ResearchJob).where(
                ResearchJob.user_id == owner_id,
                ResearchJob.job_id.in_(job_ids),
            )
        )
    ).scalars().all()
    by_id = {row.job_id: row for row in rows}
    missing = [job_id for job_id in job_ids if job_id not in by_id]
    tool_results: list[dict[str, Any]] = []
    for job_id in job_ids:
        row = by_id.get(job_id)
        if row is None or row.status != "completed":
            if row is not None:
                missing.append(job_id)
            continue
        persisted_result = row.result
        if not verify_research_job_attestation(
            row.attestation,
            job_id=row.job_id,
            owner_id=row.user_id,
            session_id=row.session_id,
            tool_name=row.tool_name,
            inputs_hash=row.inputs_hash,
            args=row.args or {},
            args_replayable=row.args_replayable,
            result=persisted_result,
            background_backend=row.background_backend,
            completed_at=row.completed_at,
        ):
            missing.append(job_id)
            continue
        result = load_structured_research_job_result(persisted_result)
        if result is None:
            missing.append(job_id)
            continue
        tool_results.append(
            {
                "id": row.job_id,
                "tool": row.tool_name,
                "input": row.args if row.args_replayable else {},
                "arguments_redacted": not row.args_replayable,
                "result": result,
            }
        )
    return tool_results, missing


def _dataset_gaps(dataset_hints: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from app.services.cosmology_likelihoods import list_cosmology_datasets

    if not dataset_hints:
        return [], []
    result = list_cosmology_datasets(dataset_keys=dataset_hints)
    gaps = [
        {
            "gap_code": "dataset_not_registered",
            "dataset_key": key,
            "next_action": "Register and provenance-audit this dataset before execution.",
        }
        for key in result.get("unknown_dataset_keys") or []
    ]
    return list(result.get("datasets") or []), gaps


_REGISTERED_AVAILABILITY_REASONS = frozenset(
    {
        "official_chain_cache_not_configured",
        "official_joint_chain_not_registered_for_model",
    }
)
_REGISTERED_AVAILABILITY_PREFIXES = (
    "official_artifact_missing:",
)


def _classify_registered_matrix_result(
    result: dict[str, Any],
    *,
    workflow_key: str,
) -> list[dict[str, Any]]:
    """Map a signed DESI matrix result to gaps or an integrity failure.

    Only explicitly enumerated absence conditions are capability gaps. Every
    unknown WITHHELD reason is treated as an artifact/contract failure so a
    newly introduced checksum, schema, checkpoint, or weight error cannot be
    silently downgraded to "data unavailable".
    """

    if result.get("success") is not True:
        error_class = str(result.get("error_class") or "").lower()
        message = str(result.get("error") or "Registered workflow failed")
        if "timeout" in error_class or "oserror" in error_class:
            raise OSError(message)
        raise RegisteredScientificIntegrityError(message)
    if result.get("publication_ready") is not False or result.get(
        "__do_not_claim__"
    ) is not True:
        raise RegisteredScientificIntegrityError(
            "Registered DESI DR2 aggregate violated its non-claimable contract"
        )
    if result.get("bao_dataset_key") not in {None, "desi_dr2_bao"}:
        raise RegisteredScientificIntegrityError(
            "Registered workflow returned an unexpected BAO release"
        )
    if result.get("include_desi_dr1_reference") not in {None, False}:
        raise RegisteredScientificIntegrityError(
            "Registered workflow mixed DESI DR1 into the DR2 likelihood"
        )
    expected_supernova_sets = {"pantheon_plus", "union3", "des_sn5yr"}
    if result.get("model") != "w0wa_cdm" or set(
        result.get("supernova_sets") or []
    ) != expected_supernova_sets:
        raise RegisteredScientificIntegrityError(
            "Registered workflow returned a different model or supernova matrix"
        )

    cells = [cell for cell in result.get("matrix", []) if isinstance(cell, dict)]
    declared_size = int(result.get("matrix_size") or 0)
    if declared_size != 3 or len(cells) != declared_size:
        raise RegisteredScientificIntegrityError(
            "Registered DESI DR2 matrix shape did not match its declaration"
        )
    cell_ids = [str(cell.get("cell_id") or "") for cell in cells]
    if any(not cell_id for cell_id in cell_ids) or len(cell_ids) != len(set(cell_ids)):
        raise RegisteredScientificIntegrityError(
            "Registered DESI DR2 matrix contains missing or duplicate cell ids"
        )
    if {
        str(cell.get("supernova_selection") or "") for cell in cells
    } != expected_supernova_sets or any(
        cell.get("model") != "w0wa_cdm"
        or cell.get("bao_dataset_key") != "desi_dr2_bao"
        for cell in cells
    ):
        raise RegisteredScientificIntegrityError(
            "Registered DESI DR2 cell identities do not match the fixed matrix"
        )

    actual_ready = sum(cell.get("status") == "COMPLETED" for cell in cells)
    declared_ready = int(result.get("official_ready_cells") or 0)
    if actual_ready != declared_ready:
        raise RegisteredScientificIntegrityError(
            "Registered DESI DR2 ready-cell count is inconsistent"
        )
    unavailable_reasons: list[str] = []
    integrity_reasons: list[str] = []
    for cell in cells:
        status = str(cell.get("status") or "")
        if status == "COMPLETED":
            if not cell.get("analysis_id") or not cell.get("support_artifacts"):
                integrity_reasons.append(
                    f"completed_cell_missing_provenance:{cell.get('cell_id')}"
                )
            continue
        reasons = [
            str(reason).strip()
            for reason in (cell.get("withheld_reasons") or [])
            if str(reason).strip()
        ]
        if not reasons:
            integrity_reasons.append(
                f"withheld_cell_missing_reason:{cell.get('cell_id')}"
            )
            continue
        for reason in reasons:
            if reason in _REGISTERED_AVAILABILITY_REASONS or any(
                reason.startswith(prefix)
                for prefix in _REGISTERED_AVAILABILITY_PREFIXES
            ):
                unavailable_reasons.append(reason)
            else:
                integrity_reasons.append(reason)
    if integrity_reasons:
        raise RegisteredScientificIntegrityError(
            "Registered DESI DR2 artifact integrity failed: "
            + ", ".join(sorted(set(integrity_reasons)))
        )
    if actual_ready < declared_size:
        return [
            {
                "gap_code": "registered_data_unavailable",
                "workflow_key": workflow_key,
                "ready_cells": actual_ready,
                "expected_cells": declared_size,
                "withheld_reasons": sorted(set(unavailable_reasons)),
                "next_action": (
                    "Install and checksum-verify the complete official DESI DR2 "
                    "chain mirror, then retry the registered workflow."
                ),
            }
        ]
    return []


async def _execute_registered_workflow(
    db: AsyncSession,
    *,
    audit: ClaimAudit,
    existing_gaps: list[dict[str, Any]],
    worker_lease_id: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Execute one versioned, server-owned workflow selected by registry keys.

    P1 intentionally exposes only the byte-pinned DESI DR2 evidence matrix.
    No client-provided tool name or arguments reach the dispatcher.
    """

    owner_id = audit.user_id
    audit_id = audit.id
    if audit.mode != "execute_registered" or existing_gaps:
        return [], []
    hints = set(audit.dataset_hints or [])
    if "desi_dr1_bao" in hints and "desi_dr2_bao" in hints:
        return [], [{
            "gap_code": "conflicting_bao_releases",
            "dataset_keys": sorted(hints),
            "next_action": "Choose exactly one BAO release; DR1 and DR2 never share a likelihood.",
        }]
    if hints != {"desi_dr2_bao"}:
        return [], [{
            "gap_code": "registered_workflow_not_available",
            "dataset_keys": sorted(hints),
            "supported_selection": ["desi_dr2_bao"],
            "next_action": (
                "Select only desi_dr2_bao for the registered P1 dark-energy matrix, "
                "or attach a completed signed Research Job in audit-only mode."
            ),
        }]

    workflow_key = DESI_DR2_MATRIX_WORKFLOW_ID
    try:
        registry_binding = assert_workflow_executable(workflow_key)
    except WorkflowRegistryError as exc:
        if exc.code not in {
            "workflow_suspended",
            "workflow_superseded",
            "workflow_revoked",
        }:
            raise
        return [], [
            {
                "gap_code": exc.code,
                "workflow_id": workflow_key,
                "next_action": (
                    "Wait for an independently verified signed registry release; "
                    "this workflow cannot execute formally yet."
                ),
            }
        ]
    workflow_spec = get_formal_workflow_spec(workflow_key)
    bind_formal_registry_record(audit, registry_binding)
    tool_name = workflow_spec.primary_entrypoint_id
    tool_args = dict(workflow_spec.science_contract["tool_arguments"])
    prior_job_ids = list(audit.child_job_ids or [])
    if prior_job_ids:
        trusted, _missing = await _trusted_tool_results(
            db,
            owner_id=owner_id,
            job_ids=prior_job_ids,
        )
        for item in reversed(trusted):
            if item.get("tool") != tool_name or not isinstance(item.get("result"), dict):
                continue
            classified_gaps = _classify_registered_matrix_result(
                item["result"],
                workflow_key=workflow_key,
            )
            return [str(item["id"])], classified_gaps

    job_id = (
        f"claim-audit-{audit_id.hex}-{workflow_key}-"
        f"a{max(1, int(audit.attempt_count or 1))}"
    )
    if not await _lock_active_audit_owner(db, owner_id):
        await db.rollback()
        return [], []
    existing = await db.get(ResearchJob, job_id)

    now = datetime.now(timezone.utc)
    if existing is None:
        existing = ResearchJob(
            job_id=job_id,
            user_id=owner_id,
            session_id=audit.session_id,
            tool_name=tool_name,
            workflow_key=workflow_key,
            inputs_hash=_sha256(_canonical_json(tool_args)),
            args=tool_args,
            args_replayable=True,
            description=f"Registered Claim Audit workflow {workflow_key}",
            status="running",
            progress=0.05,
            progress_message="Validating the official DESI DR2 chain registry",
            result=None,
            attestation=None,
            background_backend="claim_audit",
            capability_requirements={
                "workflow_version": registry_binding["workflow_version"],
                "registry_epoch": registry_binding["registry_epoch"],
                "registry_entry_hash": registry_binding["registry_entry_hash"],
                "entrypoint_id": workflow_spec.primary_entrypoint_id,
            },
            created_at=now,
            started_at=now,
            completed_at=None,
        )
        db.add(existing)
        bind_formal_registry_record(existing, registry_binding)
    else:
        expected_capability_requirements = {
            "workflow_version": registry_binding["workflow_version"],
            "registry_epoch": registry_binding["registry_epoch"],
            "registry_entry_hash": registry_binding["registry_entry_hash"],
            "entrypoint_id": workflow_spec.primary_entrypoint_id,
        }
        if (
            existing.workflow_key not in {None, workflow_key}
            or existing.tool_name != tool_name
            or existing.args != tool_args
            or existing.capability_requirements
            not in ({}, expected_capability_requirements)
        ):
            raise RegisteredScientificIntegrityError(
                "Registered workflow job binding changed"
            )
        existing.workflow_key = workflow_key
        existing.capability_requirements = expected_capability_requirements
        bind_formal_registry_record(existing, registry_binding)
        existing.status = "running"
        existing.progress = 0.05
        existing.progress_message = "Retrying the registered DESI DR2 workflow"
        existing.result = None
        existing.attestation = None
        existing.error = None
        existing.error_class = None
        existing.started_at = now
        existing.completed_at = None
    audit.child_job_ids = list(dict.fromkeys([
        *(audit.child_job_ids or []),
        job_id,
    ]))
    await db.commit()
    await db.refresh(audit)
    if audit.lifecycle_status == "CANCELLED":
        existing.status = "cancelled"
        existing.progress_message = "Parent Claim Audit was cancelled"
        existing.completed_at = datetime.now(timezone.utc)
        await db.commit()
        return [], []

    try:
        from app.services.ai_tools_cosmology import dispatch_cosmology

        result = await asyncio.wait_for(
            dispatch_cosmology(
                tool_name,
                tool_args,
                user_id=str(owner_id),
                chat_session_id=(str(audit.session_id) if audit.session_id else None),
            ),
            timeout=float(settings.claim_audit_registered_timeout_seconds),
        )
        if not isinstance(result, dict):
            raise RuntimeError("Registered workflow returned no structured result")
        classified_gaps = _classify_registered_matrix_result(
            result,
            workflow_key=workflow_key,
        )
    except Exception as exc:
        await db.rollback()
        if not await _lock_active_audit_owner(db, owner_id):
            await db.rollback()
            return [], []
        child = await db.get(ResearchJob, job_id)
        if child is not None:
            child.status = "failed"
            child.error = str(exc)[:2000]
            child.error_class = exc.__class__.__name__
            child.progress_message = "Registered workflow failed"
            child.completed_at = datetime.now(timezone.utc)
            await db.commit()
        raise

    from app.services.durable_research_records import prepare_job_result

    await db.rollback()
    if not await _lock_active_audit_owner(db, owner_id):
        await db.rollback()
        return [], []
    persisted_result = await asyncio.to_thread(
        prepare_job_result,
        job_id=job_id,
        owner_id=owner_id,
        result=result,
    )
    completed_at = datetime.now(timezone.utc)
    parent = await _lock_audit_for_terminal_write(db, audit_id)
    child = (
        await db.execute(
            select(ResearchJob)
            .where(ResearchJob.job_id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if child is None:
        raise RuntimeError("Registered child Research Job disappeared")
    if (
        parent is None
        or parent.lifecycle_status != "RUNNING"
        or parent.worker_lease_id != worker_lease_id
    ):
        child.status = (
            "cancelled"
            if parent is not None and parent.lifecycle_status == "CANCELLED"
            else "failed"
        )
        child.progress_message = "Parent Claim Audit stopped before child finalization"
        # Keep only a large-object reference so account/audit deletion can
        # discover and remove it; an inline cancelled result is discarded.
        child.result = (
            persisted_result
            if isinstance(persisted_result, dict)
            and persisted_result.get("_artifact_ref")
            else None
        )
        child.attestation = None
        child.completed_at = completed_at
        await db.commit()
        return [], []
    child.status = "completed"
    child.progress = 1.0
    child.progress_message = "Registered workflow completed"
    child.result = persisted_result
    child.error = None
    child.error_class = None
    child.completed_at = completed_at
    child.attestation = build_research_job_attestation(
        job_id=child.job_id,
        owner_id=child.user_id,
        session_id=child.session_id,
        tool_name=child.tool_name,
        inputs_hash=child.inputs_hash,
        args=child.args or {},
        args_replayable=child.args_replayable,
        result=child.result,
        background_backend=child.background_backend,
        completed_at=child.completed_at,
    )
    await db.commit()
    return [job_id], classified_gaps


def _build_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, files[path])
    return buffer.getvalue()


def _bibtex_escape(value: str) -> str:
    escaped = str(value).replace("\\", "/")
    for token in ("%", "#", "&", "_", "{", "}"):
        escaped = escaped.replace(token, f"\\{token}")
    return escaped


def _source_citation_bibtex(audit: ClaimAudit) -> str:
    """Keep the audited source resolvable inside the private Evidence Pack."""

    key = f"claim_source_{_sha256(audit.source_value.encode('utf-8'))[:12]}"
    value = _bibtex_escape(audit.source_value)
    if audit.source_kind == "doi":
        fields = [f"  doi = {{{value}}}", f"  url = {{https://doi.org/{value}}}"]
    elif audit.source_kind == "arxiv":
        fields = [
            f"  eprint = {{{value}}}",
            "  archivePrefix = {arXiv}",
            f"  url = {{https://arxiv.org/abs/{value}}}",
        ]
    elif audit.source_kind == "bibcode":
        fields = [
            f"  adsbibcode = {{{value}}}",
            f"  url = {{https://ui.adsabs.harvard.edu/abs/{value}/abstract}}",
        ]
    else:
        fields = [f"  url = {{{value}}}"]
    fields.append(
        "  note = {User-supplied identifier; syntax validated only, not resolved by this audit}"
    )
    return "@misc{" + key + ",\n" + ",\n".join(fields) + "\n}\n"


def _merge_dataset_releases(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge registry and tool-derived dataset metadata deterministically."""

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        for raw in group:
            if not isinstance(raw, dict):
                continue
            key = str(
                raw.get("key")
                or raw.get("dataset_key")
                or raw.get("display_name")
                or ""
            ).strip()
            version = str(raw.get("version") or raw.get("release") or "").strip()
            if not key:
                continue
            identity = (key, version)
            current = merged.setdefault(identity, {"key": key, "version": version})
            for field, value in sorted(raw.items()):
                if value not in (None, "", [], {}) and current.get(field) in (
                    None,
                    "",
                    [],
                    {},
                ):
                    current[field] = value
    return [merged[identity] for identity in sorted(merged)]


def _registered_workflow_snapshot(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("tool") != "run_dark_energy_evidence_matrix":
        return None
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    registry = (
        (result.get("provenance") or {}).get("cosmology_analysis_registry")
        if isinstance(result.get("provenance"), dict)
        else {}
    )
    cells: list[dict[str, Any]] = []
    for raw_cell in sorted(
        (cell for cell in result.get("matrix", []) if isinstance(cell, dict)),
        key=lambda cell: str(cell.get("cell_id") or ""),
    ):
        contours: list[dict[str, Any]] = []
        raw_contours = raw_cell.get("two_dimensional_contours")
        if isinstance(raw_contours, dict):
            for pair, contour in sorted(raw_contours.items()):
                if not isinstance(contour, dict):
                    continue
                contours.append(
                    {
                        "pair": pair,
                        "representation": contour.get("representation"),
                        "grid_hash": f"sha256:{_sha256(_canonical_json(contour))}",
                    }
                )
        artifacts = sorted(
            (
                dict(artifact)
                for artifact in raw_cell.get("support_artifacts", [])
                if isinstance(artifact, dict)
            ),
            key=lambda artifact: (
                str(artifact.get("role") or ""),
                str(artifact.get("relative_path") or ""),
            ),
        )
        cells.append(
            {
                key: raw_cell.get(key)
                for key in (
                    "cell_id",
                    "analysis_id",
                    "model",
                    "bao_dataset_key",
                    "supernova_selection",
                    "official_sn_component",
                    "data_components",
                    "execution_source",
                    "evidence_tier",
                    "status",
                    "publication_ready",
                    "claim_scope",
                    "overlap_groups",
                    "source_url",
                    "paper_arxiv",
                    "paper_doi",
                    "license",
                    "analysis_contract",
                    "parameter_intervals",
                    "diagnostics",
                    "withheld_reasons",
                )
            }
        )
        cells[-1]["support_artifacts"] = artifacts
        cells[-1]["contour_summaries"] = contours
    snapshot = {
        "job_id": item.get("id"),
        "tool": item.get("tool"),
        "arguments": item.get("input") or {},
        "input_hash": f"sha256:{_sha256(_canonical_json(item.get('input') or {}))}",
        "result_hash": f"sha256:{_sha256(_canonical_json(result))}",
        "analysis_status": result.get("analysis_status"),
        "publication_ready": result.get("publication_ready"),
        "do_not_claim": result.get("__do_not_claim__"),
        "registry": registry or {},
        "literature": result.get("literature_context") or {},
        "tension_lab": result.get("tension_lab") or {},
        "random_seed": None,
        "seed_status": "upstream_not_published",
        "cells": cells,
    }
    return snapshot


def _pack_files(
    audit: ClaimAudit,
    *,
    report: dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    registered_workflows = [
        snapshot
        for item in tool_results
        if (snapshot := _registered_workflow_snapshot(item)) is not None
    ]
    provenance = {
        "audit_id": str(audit.id),
        "source": {
            "kind": audit.source_kind,
            "identifier": audit.source_value,
            "resolution_status": "syntax_validated_only",
            "verified": False,
        },
        "evidence_record_ids": list(audit.evidence_record_ids or []),
        "child_job_ids": list(audit.child_job_ids or []),
        "evidence_graph": audit.evidence_graph or {},
        "fact_check_report": audit.fact_check_report or {},
        "reproducibility_manifest": report.get("reproducibility_manifest") or [],
        "registered_workflows": registered_workflows,
    }
    generated_bibtex = str(report.get("bibtex") or "").strip()
    source_bibtex = _source_citation_bibtex(audit)
    verdict = str(audit.scientific_verdict or "WITHHELD")
    report_banner = [
        f"# Claim Audit — {verdict}",
        "",
        f"Audit ID: `{audit.id}`",
        "",
        "**NOT PEER REVIEWED.** This pack records a server-owned evidence check.",
        "The supplied source identifier was syntax-validated only and was not resolved.",
    ]
    if verdict != "SUPPORTED":
        report_banner.extend(
            [
                "",
                "**NOT CITABLE — NUMERICAL CLAIMS ARE WITHHELD.**",
            ]
        )
    report_markdown = "\n".join(report_banner) + "\n\n" + str(
        report.get("markdown") or ""
    ).lstrip()
    files = {
        "report.md": report_markdown.encode("utf-8"),
        "citations.bib": (
            source_bibtex + ("\n" + generated_bibtex + "\n" if generated_bibtex else "")
        ).encode("utf-8"),
        "provenance.json": _canonical_json(provenance),
    }
    file_hashes = {path: f"sha256:{_sha256(content)}" for path, content in files.items()}
    tool_records = []
    for item in tool_results:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        diagnostics = {}
        if isinstance(result, dict):
            for key in (
                "chain_diagnostics",
                "convergence_diagnostics",
                "diagnostics",
            ):
                if isinstance(result.get(key), dict):
                    diagnostics[key] = result[key]
        reproducibility = (
            result.get("reproducibility")
            if isinstance(result.get("reproducibility"), dict)
            else {}
        )
        scientific_outputs = {
            key: result.get(key)
            for key in (
                "parameters",
                "posterior_summary",
                "derived_parameters",
                "parameter_intervals",
                "datasets_used",
                "datasets_not_run",
            )
            if result.get(key) not in (None, {}, [])
        }
        tool_records.append(
            {
                "job_id": item.get("id"),
                "tool": item.get("tool"),
                "analysis_status": result.get("analysis_status")
                or result.get("__tool_status__"),
                "publication_ready": result.get("publication_ready") is True,
                "do_not_claim": result.get("__do_not_claim__") is True,
                "claim_scope": result.get("claim_scope"),
                "arguments": item.get("input") or {},
                "arguments_redacted": bool(item.get("arguments_redacted")),
                "input_hash": f"sha256:{_sha256(_canonical_json(item.get('input') or {}))}",
                "result_hash": f"sha256:{_sha256(_canonical_json(result))}",
                "config_hash": (
                    result.get("config_hash") or result.get("runner_hash")
                    if isinstance(result, dict)
                    else None
                ),
                "diagnostics": diagnostics,
                "run_id": reproducibility.get("run_id"),
                "query_hash": reproducibility.get("query_hash"),
                "random_seed": reproducibility.get("random_seed"),
                "tool_version": reproducibility.get("tool_version"),
                "scientific_outputs": scientific_outputs,
            }
        )
    content_root = scientific_content_hash(file_hashes)
    claim_graphs = (audit.evidence_graph or {}).get("claim_graphs") or {}
    claim_verdicts = {
        str(item.get("claim_id")): str(item.get("verdict"))
        for item in (audit.normalized_claims or [])
        if isinstance(item, dict) and item.get("claim_id")
    }
    claim_evidence_paths = {
        claim_id: {
            "verdict": claim_verdicts.get(claim_id, "WITHHELD"),
            "supported_claims": (
                list(graph.get("supported_claims") or [])
                if claim_verdicts.get(claim_id) == "SUPPORTED"
                else []
            ),
            "unsupported_claims": list(graph.get("unsupported_claims") or []),
        }
        for claim_id, graph in sorted(claim_graphs.items())
        if isinstance(graph, dict)
    }
    manifest_payload = {
        "pack_schema_version": EVIDENCE_PACK_SCHEMA_VERSION,
        "audit_id": str(audit.id),
        "owner_id": str(audit.user_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "software_release": _software_release_identity(),
        "git_commit": _git_commit_identity(),
        "request_hash": audit.request_hash,
        "source": {
            "kind": audit.source_kind,
            "identifier": audit.source_value,
            "identifier_hash": f"sha256:{_sha256(audit.source_value.encode('utf-8'))}",
            "resolution_status": "syntax_validated_only",
            "verified": False,
        },
        "normalized_claims": list(audit.normalized_claims or []),
        "scientific_verdict": audit.scientific_verdict,
        "evidence_paths": (
            (audit.evidence_graph or {}).get("supported_claims", [])
            if audit.scientific_verdict == "SUPPORTED"
            else []
        ),
        "claim_evidence_paths": claim_evidence_paths,
        "dataset_releases": report.get("datasets") or [],
        "tool_records": tool_records,
        "registered_workflows": registered_workflows,
        "capability_gaps": list(audit.capability_gaps or []),
        "limitations": [
            "SUPPORTED means supported by this server-owned evidence chain; it is not peer review.",
            "WITHHELD and CAPABILITY_GAP packs are non-citable process records.",
        ],
        "files": file_hashes,
        # Hash every payload file.  Hashing the final ZIP inside the manifest
        # would be self-referential, so this explicit content hash is the
        # portable Evidence Pack identity.
        "pack_content_hash": content_root,
        "content_root": content_root,
    }
    manifest = build_scientific_attestation(
        attestation_type="claim_audit_evidence_pack",
        payload=manifest_payload,
    )
    files["manifest.json"] = _canonical_json(manifest)
    return files, manifest


async def stage_evidence_pack(
    db: AsyncSession,
    *,
    audit: ClaimAudit,
    report: dict[str, Any],
    tool_results: list[dict[str, Any]],
    worker_lease_id: str,
) -> tuple[EvidencePack, bytes, str | None]:
    """Persist an UPLOADING row before writing the recoverable object."""

    existing = (
        await db.execute(select(EvidencePack).where(EvidencePack.audit_id == audit.id))
    ).scalar_one_or_none()
    files, manifest = _pack_files(
        audit,
        report=report,
        tool_results=tool_results,
    )
    artifact = _build_zip(files)
    artifact_ref = (
        f"evidence-packs/{audit.user_id}/{audit.id}/{worker_lease_id}.zip"
    )
    row = existing
    previous_artifact_ref = row.artifact_ref if row is not None else None
    if previous_artifact_ref and previous_artifact_ref != artifact_ref:
        from app.services.account_deletion import deletion_user_fingerprint
        from app.storage import normalize_storage_key

        previous_artifact_ref = normalize_storage_key(previous_artifact_ref)
        cleanup = await db.scalar(
            select(ArtifactCleanupQueue)
            .where(ArtifactCleanupQueue.artifact_ref == previous_artifact_ref)
            .with_for_update()
        )
        cleanup_deadline = datetime.now(timezone.utc) + timedelta(
            seconds=int(settings.artifact_cleanup_grace_seconds)
        )
        if cleanup is None:
            db.add(
                ArtifactCleanupQueue(
                    user_fingerprint=deletion_user_fingerprint(audit.user_id),
                    artifact_ref=previous_artifact_ref,
                    reason_class="superseded_evidence_pack",
                    not_before=cleanup_deadline,
                )
            )
        else:
            cleanup.user_fingerprint = deletion_user_fingerprint(audit.user_id)
            cleanup.reason_class = "superseded_evidence_pack"
            cleanup.not_before = cleanup_deadline
            cleanup.last_error_class = None
    if row is None:
        row = EvidencePack(
            audit_id=audit.id,
            user_id=audit.user_id,
            status="UPLOADING",
            schema_version=EVIDENCE_PACK_SCHEMA_VERSION,
            artifact_ref=artifact_ref,
            manifest=manifest,
            manifest_hash=str(manifest["manifest_hash"]),
            signature=str(manifest["signature"]),
            key_id=str(manifest["key_id"]),
            upload_lease_id=worker_lease_id,
            upload_started_at=datetime.now(timezone.utc),
            finalized_at=None,
        )
        db.add(row)
    else:
        row.status = "UPLOADING"
        row.schema_version = EVIDENCE_PACK_SCHEMA_VERSION
        row.artifact_ref = artifact_ref
        row.manifest = manifest
        row.manifest_hash = str(manifest["manifest_hash"])
        row.signature = str(manifest["signature"])
        row.key_id = str(manifest["key_id"])
        row.upload_lease_id = worker_lease_id
        row.upload_started_at = datetime.now(timezone.utc)
        row.finalized_at = None
    await db.flush()
    return row, artifact, previous_artifact_ref


async def _delete_pack_artifact(
    artifact_ref: str,
    *,
    cleanup_queue_expected: bool = False,
) -> bool:
    from app.storage import delete_fits_all_versions

    try:
        await asyncio.to_thread(delete_fits_all_versions, artifact_ref)
        if cleanup_queue_expected:
            from app.services.artifact_cleanup import clear_artifact_cleanup_sync

            await asyncio.to_thread(clear_artifact_cleanup_sync, artifact_ref)
        return True
    except FileNotFoundError:
        return True
    except Exception:
        logger.exception("Could not remove staged Evidence Pack %s", artifact_ref)
        return False


async def _discard_staged_pack(
    db: AsyncSession,
    *,
    audit_id: Any,
    artifact_ref: str,
    worker_lease_id: str,
) -> None:
    """Best-effort cleanup; leave a recoverable UPLOADING row on delete failure."""

    await db.rollback()
    cleaned = await _delete_pack_artifact(artifact_ref)
    row = (
        await db.execute(
            select(EvidencePack)
            .where(
                EvidencePack.audit_id == audit_id,
                EvidencePack.status == "UPLOADING",
                EvidencePack.upload_lease_id == worker_lease_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is not None and cleaned:
        await db.delete(row)
    await db.commit()


def _lease_deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(
        seconds=int(settings.claim_audit_worker_lease_seconds)
    )


async def _lock_active_audit_owner(db: AsyncSession, owner_id: Any) -> bool:
    """Serialize Claim Audit persistence with account deletion."""

    owner = (
        await db.execute(
            select(User)
            .where(User.id == owner_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    return owner is not None and str(owner.account_status or "").upper() == "ACTIVE"


async def _claim_audit_lease(
    db: AsyncSession,
    audit: ClaimAudit,
    *,
    worker_lease_id: str,
) -> bool:
    """Claim QUEUED work or atomically take over an expired RUNNING lease."""

    audit_id = audit.id
    owner_id = audit.user_id
    await db.rollback()
    if not await _lock_active_audit_owner(db, owner_id):
        await db.rollback()
        await db.refresh(audit)
        return False
    now = datetime.now(timezone.utc)
    claimed = await db.execute(
        update(ClaimAudit)
        .where(
            ClaimAudit.id == audit_id,
            or_(
                ClaimAudit.lifecycle_status == "QUEUED",
                (
                    (ClaimAudit.lifecycle_status == "RUNNING")
                    & or_(
                        ClaimAudit.lease_expires_at.is_(None),
                        ClaimAudit.lease_expires_at < now,
                    )
                ),
            ),
        )
        .values(
            lifecycle_status="RUNNING",
            started_at=func.coalesce(ClaimAudit.started_at, now),
            error=None,
            error_class=None,
            worker_lease_id=worker_lease_id,
            lease_expires_at=_lease_deadline(),
            attempt_count=ClaimAudit.attempt_count + 1,
        )
    )
    # Release the state-transition lock before scientific work.  This keeps
    # cancellation responsive and prevents a worker from overwriting it.
    await db.commit()
    await db.refresh(audit)
    return bool(claimed.rowcount == 1)


async def renew_claim_audit_lease(
    db: AsyncSession,
    *,
    audit_id: Any,
    worker_lease_id: str,
) -> bool:
    """Renew only the live worker's lease; a stale process cannot reclaim it."""

    renewed = await db.execute(
        update(ClaimAudit)
        .where(
            ClaimAudit.id == audit_id,
            ClaimAudit.lifecycle_status == "RUNNING",
            ClaimAudit.worker_lease_id == worker_lease_id,
            ClaimAudit.user_id.in_(
                select(User.id).where(User.account_status == "ACTIVE")
            ),
        )
        .values(lease_expires_at=_lease_deadline())
    )
    await db.commit()
    return bool(renewed.rowcount == 1)


async def _lock_audit_for_terminal_write(
    db: AsyncSession,
    audit_id: Any,
) -> ClaimAudit | None:
    """Reload and lock the row immediately before a terminal transition."""

    return (
        await db.execute(
            select(ClaimAudit)
            .where(ClaimAudit.id == audit_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _record_failure(
    db: AsyncSession,
    *,
    audit_id: Any,
    lifecycle_status: str,
    exc: Exception,
    worker_lease_id: str,
) -> ClaimAudit | None:
    """Record a failure without reviving a cancelled or deleted audit."""

    await db.rollback()
    owner_id = await db.scalar(
        select(ClaimAudit.user_id).where(ClaimAudit.id == audit_id)
    )
    if owner_id is None:
        await db.rollback()
        return None
    if not await _lock_active_audit_owner(db, owner_id):
        await db.rollback()
        return await db.get(ClaimAudit, audit_id)
    audit = await _lock_audit_for_terminal_write(db, audit_id)
    if audit is None:
        await db.rollback()
        return None
    if (
        audit.lifecycle_status == "CANCELLED"
        or audit.worker_lease_id != worker_lease_id
    ):
        await db.commit()
        return audit
    audit.lifecycle_status = lifecycle_status
    audit.scientific_verdict = None
    audit.error = str(exc)[:2000]
    audit.error_class = exc.__class__.__name__
    audit.worker_lease_id = None
    audit.lease_expires_at = None
    await db.commit()
    await db.refresh(audit)
    return audit


async def process_claim_audit(
    db: AsyncSession,
    audit: ClaimAudit,
    *,
    worker_lease_id: str | None = None,
) -> ClaimAudit:
    audit_id = audit.id
    owner_id = audit.user_id
    lease_id = worker_lease_id or uuid.uuid4().hex
    if audit.lifecycle_status == "CANCELLED":
        return audit
    if not await _claim_audit_lease(
        db,
        audit,
        worker_lease_id=lease_id,
    ):
        # Another live lease or a terminal lifecycle state won the atomic
        # claim. Expired RUNNING leases are eligible for safe takeover.
        return audit
    try:
        datasets, gaps = _dataset_gaps(list(audit.dataset_hints or []))
        generated_refs, execution_gaps = await _execute_registered_workflow(
            db,
            audit=audit,
            existing_gaps=gaps,
            worker_lease_id=lease_id,
        )
        gaps.extend(execution_gaps)
        requested_refs = list(dict.fromkeys([
            *(audit.evidence_input_refs or []),
            *generated_refs,
        ]))
        tool_results, missing_refs = await _trusted_tool_results(
            db,
            owner_id=audit.user_id,
            job_ids=requested_refs,
        )
        if missing_refs:
            gaps.append(
                {
                    "gap_code": "evidence_ref_unavailable",
                    "count": len(missing_refs),
                    "next_action": "Choose completed research jobs owned by this account.",
                }
            )
        from app.services.research_program import (
            build_evidence_graph,
            export_research_report,
            verify_research_facts,
        )

        claim_texts = normalize_claims(audit.claim_text)
        graph_result = build_evidence_graph(
            tool_results=tool_results,
            final_reply=audit.claim_text,
        )
        graph = graph_result.get("evidence_graph") or {}
        fact_result = verify_research_facts(
            tool_results=tool_results,
            final_reply=audit.claim_text,
            evidence_graph=graph,
        )
        fact_report = fact_result.get("fact_check_report") or {}
        normalized_claims: list[dict[str, Any]] = []
        claim_graphs: dict[str, Any] = {}
        claim_reports: dict[str, Any] = {}
        for index, claim_text in enumerate(claim_texts, start=1):
            claim_id = f"claim-{index}"
            claim_graph_result = build_evidence_graph(
                tool_results=tool_results,
                final_reply=claim_text,
            )
            claim_graph = claim_graph_result.get("evidence_graph") or {}
            claim_fact_result = verify_research_facts(
                tool_results=tool_results,
                final_reply=claim_text,
                evidence_graph=claim_graph,
            )
            claim_fact_report = claim_fact_result.get("fact_check_report") or {}
            checked_claims = list(claim_fact_report.get("claims") or [])
            substantive_claims = [
                item for item in checked_claims if item.get("kind") != "scope"
            ]
            full_text_covered = _claim_text_fully_covered(claim_text)
            claim_supported = full_text_covered and bool(substantive_claims) and all(
                item.get("status") == "verified"
                and bool(item.get("evidence_ids"))
                for item in substantive_claims
            )
            claim_supported = claim_supported and bool(
                claim_graph_result.get("has_support_path")
            ) and claim_graph_result.get("publication_ready") is True
            if gaps:
                claim_verdict = "CAPABILITY_GAP"
            elif claim_supported:
                claim_verdict = "SUPPORTED"
            else:
                claim_verdict = "WITHHELD"
            support_indices = {
                int(match.group(1))
                for supported_claim in claim_graph.get("supported_claims") or []
                for path_item in supported_claim.get("evidence_path") or []
                if (match := re.match(r"tool_run:(\d+):", str(path_item)))
            }
            supporting_ids = (
                [
                    str(tool_results[item_index].get("id"))
                    for item_index in sorted(support_indices)
                    if item_index < len(tool_results)
                ]
                if claim_verdict == "SUPPORTED"
                else []
            )
            normalized_claims.append(
                {
                    "claim_id": claim_id,
                    "text": claim_text,
                    "verdict": claim_verdict,
                    "parse_coverage": (
                        "complete" if full_text_covered else "unparsed_residual"
                    ),
                    "supporting_evidence_ids": supporting_ids,
                }
            )
            claim_graphs[claim_id] = claim_graph
            claim_reports[claim_id] = claim_fact_report

        if gaps:
            verdict = "CAPABILITY_GAP"
        elif normalized_claims and all(
            item["verdict"] == "SUPPORTED" for item in normalized_claims
        ):
            verdict = "SUPPORTED"
        else:
            verdict = "WITHHELD"

        report = export_research_report(
            research_plan={
                "research_question": audit.claim_text,
                "blocking_gaps": [gap.get("next_action") for gap in gaps],
            },
            evidence_graph=graph,
            tool_results=tool_results,
            title="Standard Astro Claim Audit",
        )
        report["datasets"] = _merge_dataset_releases(
            list(report.get("datasets") or []),
            datasets,
        )

        # Scientific work runs without a row lock.  Re-lock only for the short
        # immutable-pack transaction and reload the status so a cancellation
        # received during the run wins deterministically.
        if not await _lock_active_audit_owner(db, owner_id):
            await db.rollback()
            return audit
        audit = await _lock_audit_for_terminal_write(db, audit_id)
        if audit is None:
            await db.rollback()
            raise ClaimAuditInputError("Claim Audit disappeared while running")
        if audit.lifecycle_status == "CANCELLED":
            await db.commit()
            return audit
        if (
            audit.lifecycle_status != "RUNNING"
            or audit.worker_lease_id != lease_id
        ):
            await db.commit()
            return audit

        audit.scientific_verdict = verdict
        audit.capability_gaps = gaps
        audit.evidence_record_ids = [str(item.get("id")) for item in tool_results]
        audit.child_job_ids = list(
            dict.fromkeys([*(audit.child_job_ids or []), *generated_refs])
        )
        audit.evidence_graph = {**graph, "claim_graphs": claim_graphs}
        audit.fact_check_report = {
            **fact_report,
            "claim_reports": claim_reports,
        }
        audit.normalized_claims = normalized_claims
        pack, artifact, previous_artifact_ref = await stage_evidence_pack(
            db,
            audit=audit,
            report=report,
            tool_results=tool_results,
            worker_lease_id=lease_id,
        )
        artifact_ref = pack.artifact_ref
        # Make the UPLOADING row durable before object storage. A worker crash
        # can therefore be reconciled instead of leaving an undiscoverable ZIP.
        await db.commit()

        from app.storage import upload_fits

        if not await _lock_active_audit_owner(db, owner_id):
            await _discard_staged_pack(
                db,
                audit_id=audit_id,
                artifact_ref=artifact_ref,
                worker_lease_id=lease_id,
            )
            return audit
        try:
            await asyncio.to_thread(upload_fits, artifact_ref, artifact)
        except Exception:
            await _discard_staged_pack(
                db,
                audit_id=audit_id,
                artifact_ref=artifact_ref,
                worker_lease_id=lease_id,
            )
            raise

        locked_audit = await _lock_audit_for_terminal_write(db, audit_id)
        pack = (
            await db.execute(
                select(EvidencePack)
                .where(
                EvidencePack.audit_id == audit_id,
                EvidencePack.status == "UPLOADING",
                EvidencePack.upload_lease_id == lease_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_audit is None or pack is None:
            await _discard_staged_pack(
                db,
                audit_id=audit_id,
                artifact_ref=artifact_ref,
                worker_lease_id=lease_id,
            )
            raise ClaimAuditInputError("Claim Audit finalization state disappeared")
        audit = locked_audit
        if audit.lifecycle_status == "CANCELLED":
            await _discard_staged_pack(
                db,
                audit_id=audit_id,
                artifact_ref=artifact_ref,
                worker_lease_id=lease_id,
            )
            await db.refresh(audit)
            return audit
        if (
            audit.lifecycle_status != "RUNNING"
            or audit.worker_lease_id != lease_id
        ):
            # A newer lease owns the row. Its staged object may already be in
            # flight, so this stale worker must not delete shared state.
            await db.commit()
            return audit

        finalized_at = datetime.now(timezone.utc)
        pack.status = "FINALIZED"
        pack.finalized_at = finalized_at
        audit.lifecycle_status = "COMPLETED"
        audit.completed_at = finalized_at
        audit.worker_lease_id = None
        audit.lease_expires_at = None
        try:
            await db.commit()
        except Exception:
            # COMMIT may have succeeded while its acknowledgement was lost.
            # Do not delete an artifact that may now be referenced by a
            # FINALIZED pack; stale-UPLOADING reconciliation handles true
            # rollback after re-reading durable state.
            await db.rollback()
            raise
        if previous_artifact_ref and previous_artifact_ref != artifact_ref:
            await _delete_pack_artifact(
                previous_artifact_ref,
                cleanup_queue_expected=True,
            )
        await db.refresh(audit)
        await _track_terminal_audit(audit)
        return audit
    except (FileNotFoundError, OSError, TimeoutError) as exc:
        failed = await _record_failure(
            db,
            audit_id=audit_id,
            lifecycle_status="FAILED_RETRYABLE",
            exc=exc,
            worker_lease_id=lease_id,
        )
        if failed is not None and failed.lifecycle_status in {
            "FAILED_RETRYABLE",
            "FAILED_FINAL",
        }:
            await _track_terminal_audit(failed)
        return failed or audit
    except Exception as exc:
        failed = await _record_failure(
            db,
            audit_id=audit_id,
            lifecycle_status="FAILED_FINAL",
            exc=exc,
            worker_lease_id=lease_id,
        )
        if failed is not None and failed.lifecycle_status in {
            "FAILED_RETRYABLE",
            "FAILED_FINAL",
        }:
            await _track_terminal_audit(failed)
        return failed or audit


def verify_pack_bytes(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_EVIDENCE_PACK_BYTES:
        return {"valid": False, "reason": "pack_too_large"}
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            required = {"manifest.json", "report.md", "citations.bib", "provenance.json"}
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            total_uncompressed = sum(entry.file_size for entry in entries)
            unsafe_ratio = any(
                entry.file_size
                > max(1, entry.compress_size) * MAX_EVIDENCE_PACK_COMPRESSION_RATIO
                for entry in entries
            )
            if (
                len(entries) != len(required)
                or len(set(names)) != len(names)
                or set(names) != required
                or any(name.startswith("/") or ".." in name.split("/") for name in names)
                or any(entry.is_dir() or entry.flag_bits & 0x1 for entry in entries)
                or any(
                    entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    for entry in entries
                )
                or total_uncompressed > MAX_EVIDENCE_PACK_UNCOMPRESSED_BYTES
                or unsafe_ratio
            ):
                return {"valid": False, "reason": "invalid_pack_layout"}
            extracted = {name: archive.read(name) for name in names}
            if _build_zip(extracted) != payload:
                return {"valid": False, "reason": "noncanonical_pack_bytes"}
            manifest = json.loads(extracted["manifest.json"])
            if not verify_scientific_attestation(
                manifest,
                expected_type="claim_audit_evidence_pack",
            ):
                return {"valid": False, "reason": "invalid_manifest_signature"}
            file_hashes = manifest.get("files")
            if not isinstance(file_hashes, dict):
                return {"valid": False, "reason": "missing_file_hashes"}
            if set(file_hashes) != required - {"manifest.json"}:
                return {"valid": False, "reason": "invalid_file_manifest"}
            for name, expected in file_hashes.items():
                observed = f"sha256:{_sha256(extracted[name])}"
                if observed != expected:
                    return {"valid": False, "reason": f"hash_mismatch:{name}"}
            content_root = scientific_content_hash(file_hashes)
            if content_root != manifest.get("content_root"):
                return {"valid": False, "reason": "content_root_mismatch"}
            if content_root != manifest.get("pack_content_hash"):
                return {"valid": False, "reason": "pack_content_hash_mismatch"}
            return {
                "valid": True,
                "audit_id": manifest.get("audit_id"),
                "scientific_verdict": manifest.get("scientific_verdict"),
                "key_id": manifest.get("key_id"),
                "manifest_hash": manifest.get("manifest_hash"),
            }
    except (
        zipfile.BadZipFile,
        KeyError,
        json.JSONDecodeError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        return {"valid": False, "reason": "unreadable_pack"}
