"""Deterministic control-plane loop for the first Union3 reproduction.

Only this service may promote the registered reproduction to ``SUPPORTED``.
The local worker result, Render-side independent verifier, and append-only
human review remain separate records and are bound again immediately before
the terminal write.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.claim_audit_records import ClaimAudit, EvidencePack
from app.models.research_records import ResearchJob
from app.models.schemas import User
from app.models.worker_records import ScienceExecutionAttempt, WorkerArtifactIssuance
from app.models.workspace_records import (
    ClaimAuditReview,
    ResearchWorkspace,
    SourceDocument,
    SourceExtraction,
)
from app.services.evidence_pack_v2 import (
    EVIDENCE_PACK_V2_SIGNATURE_ALGORITHM,
    build_evidence_pack_v2,
    jcs_canonicalize,
    verify_evidence_pack_v2,
)
from app.services.research_workspace_service import (
    reviewer_pseudonym,
    validate_registered_union3_source_receipt,
)
from app.services.registered_workflows import (
    UNION3_RADIATION_CONVENTION,
    UNION3_REPRODUCTION_WORKFLOW_ID,
    get_registered_dataset_pins,
    get_registered_workflow,
)
from app.services.server_evidence import (
    build_research_job_attestation,
    verify_research_job_attestation,
)
from app.services.union3_verification_service import verify_union3_primary_result
from app.services.union3_reader import (
    UNION3_ARXIV_ID,
    UNION3_EXTRACTION_SCHEMA_VERSION,
    UNION3_PDF_SHA256,
    UNION3_READER_VERSION,
    UNION3_SOURCE_PROFILE_KEY,
    UNION3_SOURCE_URL,
    UNION3_TEXT_PROJECTION_METHOD,
    Union3ReaderError,
    build_union3_source_document_hash,
)
from app.services.worker_contract import (
    WORKER_PROTOCOL_VERSION,
    canonical_json,
    canonical_result_hash,
)
from app.storage import (
    delete_fits_all_versions,
    download_fits,
    lock_active_storage_owner,
    upload_fits,
)


_HEX_64 = re.compile(r"[0-9a-f]{64}")
_FULL_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_EXPECTED_WORKER_ARTIFACT_CONTENT_TYPES = {
    "primary_analysis.json": "application/json",
    "chi2_profile.svg": "image/svg+xml",
    "environment.json": "application/json",
}
_WORKER_ENVIRONMENT_KEYS = {
    "schema_version",
    "protocol_version",
    "workflow_key",
    "git_commit",
    "image_digest",
    "python_version",
    "platform_system",
    "platform_machine",
    "mcmc",
}
_UNION3_CLAIM_TEXT = (
    "Union3 Table 9 reports Ωm = 0.356 +0.028/-0.026 for the SNe-only "
    "Flat ΛCDM fit, using a one-parameter Δχ2=1 profile interval."
)
_UNION3_LIMITATIONS = [
    "This reader maps one paper-reported Table 9 interval only.",
    "Human review cannot override a failed machine or independent-verification gate.",
]
_UNION3_ANCHOR_SPECS = (
    (
        "57",
        "interval_method",
        "Table 9 summarizes our cosmological constraints. We first solve for the best "
        "fit when varying each of the parameters specified in the rows. Then, we move "
        "each parameter in turn away from its best fit, find the best fit with that "
        "parameter fixed, and find the two points for that parameter (in the positive "
        "and negative direction) where χ2 increases by 1. This gives us the quoted "
        "plus and minus confidence intervals.",
        "The quoted one-parameter interval endpoints use Δχ2=1 after refitting the other parameters.",
    ),
    (
        "57",
        "frequentist_semantics",
        "We compute frequentist contours (Δχ2 compared to the best fit of 2.296, "
        "6.180, and 11.829 for 68.3%, 95.4%, and 99.7% confidence) by fixing the two "
        "parameters shown in the plane and fitting for the others.",
        "The paper explicitly describes these contours as frequentist.",
    ),
    (
        "58",
        "table_header",
        "Probes χ2 (DoF) h Ωm Ωk w or w0 wa DETF FoM",
        "The Ωm value is read from the registered Table 9 Ωm column.",
    ),
    (
        "58",
        "table_row",
        "Flat ΛCDM SNe 24.0 (20) ··· 0.356+0.028 -0.026 ··· ··· ··· ···",
        "The registered row is the SNe-only Flat ΛCDM fit.",
    ),
    (
        "58",
        "table_caption",
        "Table 9. Constraints on cosmological parameters. The SN χ2 values are based "
        "on spline-interpolated distances (with 22 nodes, so SN DoF = 22 - Nfit), so "
        "they are much smaller than the number of SNe (2087).",
        "The table uses the 22-node spline-interpolated SN distances.",
    ),
)
_CANDIDATE_KEYS = {
    "candidate_id",
    "candidate_hash",
    "claim_hash",
    "candidate_type",
    "claim_text",
    "parameter",
    "reported_value",
    "central",
    "plus",
    "minus",
    "lower",
    "upper",
    "confidence_level",
    "model_scope",
    "data_scope",
    "interval_kind",
    "statistical_semantics",
    "confidence_definition",
    "delta_chi_square",
    "fit_diagnostics",
    "claim_scope",
    "source_anchor_ids",
    "publication_ready",
    "review_required",
}
_EXTRACTION_KEYS = {
    "schema_version",
    "reader_version",
    "source",
    "anchors",
    "candidates",
    "coverage_status",
    "limitations",
    "publication_ready",
    "review_required",
    "extraction_hash",
}


class Union3ResearchLoopError(RuntimeError):
    """A classified failure that must leave the scientific verdict withheld."""

    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _require_union3_feature_gates() -> None:
    required = {
        "claim_audit": settings.claim_audit_enabled,
        "research_workspace": settings.research_workspace_enabled,
        "arxiv_reader": settings.arxiv_reader_enabled,
        "union3_reproduction": settings.union3_reproduction_enabled,
        "evidence_pack_v2": settings.evidence_pack_v2_enabled,
        "local_science_worker": settings.local_science_worker_enabled,
    }
    disabled = sorted(name for name, enabled in required.items() if not enabled)
    if disabled:
        raise Union3ResearchLoopError(
            "union3_feature_disabled",
            "The registered Union3 evidence lane is disabled by an operator",
            status_code=503,
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _request_hash(value: dict[str, Any]) -> str:
    return _sha256_bytes(canonical_json(value))


def _release_commit() -> str:
    value = str(
        os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("GIT_COMMIT")
        or os.getenv("TOOL_VERSION")
        or "development"
    ).strip()
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_digest(value: Any) -> str:
    try:
        return _sha256_bytes(canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise Union3ResearchLoopError(
            "canonical_binding_invalid",
            "A registered Union3 binding cannot be canonically encoded",
        ) from exc


def _semantic_anchor_text(value: str) -> str:
    dehyphenated = re.sub(
        r"(?<=[A-Za-z])-\s*\n\s*(?=[A-Za-z])",
        "",
        value,
    )
    return " ".join(dehyphenated.split())


def _registered_extraction_raw_artifacts(artifact_ref: str) -> list[dict[str, str]]:
    return [
        {
            "role": "authoritative_pdf",
            "artifact_ref": artifact_ref,
            "content_type": "application/pdf",
        }
    ]


def _expected_atomic_claim(candidate: dict[str, Any]) -> dict[str, str]:
    return {
        "claim_type": "PARAMETER_INTERVAL_REPRODUCTION",
        "candidate_id": str(candidate["candidate_id"]),
        "parameter": "omegam",
        "central": "0.356",
        "minus": "0.026",
        "plus": "0.028",
        "lower": "0.330",
        "upper": "0.384",
        "confidence_level": "0.683",
        "interval_kind": "frequentist_profile_chi_square",
        "model_scope": "flat_lcdm",
        "data_scope": "union3_sn_only",
    }


def _request_identity(
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    source: SourceDocument,
    extraction: SourceExtraction,
    candidate_id: str,
    supersedes_audit_id: uuid.UUID | None = None,
) -> dict[str, str]:
    identity = {
        "user_id": str(user_id),
        "workspace_id": str(workspace_id),
        "source_document_id": str(source.id),
        "source_document_hash": source.source_document_hash,
        "source_extraction_id": str(extraction.id),
        "source_extraction_hash": extraction.extraction_payload_hash,
        "candidate_id": candidate_id,
        "workflow_key": UNION3_REPRODUCTION_WORKFLOW_ID,
    }
    if supersedes_audit_id is not None:
        identity["supersedes_audit_id"] = str(supersedes_audit_id)
    return identity


def _primary_job_args(
    *,
    audit_id: uuid.UUID,
    source: SourceDocument,
    extraction: SourceExtraction,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "workflow_key": UNION3_REPRODUCTION_WORKFLOW_ID,
        "audit_id": str(audit_id),
        "normalized_inputs": {
            "candidate_id": str(candidate["candidate_id"]),
            "claim_hash": str(candidate["claim_hash"]),
            "source_document_id": str(source.id),
            "source_document_hash": source.source_document_hash,
            "source_extraction_id": str(extraction.id),
            "source_extraction_hash": extraction.extraction_payload_hash,
            "parameter": "omegam",
            "model_scope": "flat_lcdm",
            "data_scope": "union3_sn_only",
            "interval_kind": "frequentist_profile_chi_square",
        },
        "dataset_pins": get_registered_dataset_pins(UNION3_REPRODUCTION_WORKFLOW_ID),
        "resource_limits": {"cpu": 2, "memory_mb": 6144, "concurrency": 1},
        "deadline_seconds": 30 * 60,
    }


def _candidate_for_source(
    extraction: SourceExtraction, candidate_id: str
) -> dict[str, Any]:
    payload = extraction.extraction_payload
    if not isinstance(payload, dict) or set(payload) != _EXTRACTION_KEYS:
        raise Union3ResearchLoopError(
            "source_extraction_invalid", "The registered source extraction is invalid"
        )
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise Union3ResearchLoopError(
            "source_candidates_invalid", "The registered source candidates are invalid"
        )
    candidate = candidates[0]
    if not isinstance(candidate, dict) or set(candidate) != _CANDIDATE_KEYS:
        raise Union3ResearchLoopError(
            "source_candidate_schema_invalid",
            "The registered source candidate schema is invalid",
        )
    if candidate.get("candidate_id") != candidate_id:
        raise Union3ResearchLoopError(
            "source_candidate_not_found",
            "The registered source candidate was not found",
            status_code=404,
        )

    anchor_ids = candidate.get("source_anchor_ids")
    expected_core = {
        "candidate_type": "parameter_interval_report",
        "claim_text": _UNION3_CLAIM_TEXT,
        "parameter": "omegam",
        "reported_value": {
            "central": "0.356",
            "plus": "0.028",
            "minus": "0.026",
            "lower": "0.330",
            "upper": "0.384",
            "confidence_level": "0.683",
        },
        "central": "0.356",
        "plus": "0.028",
        "minus": "0.026",
        "lower": "0.330",
        "upper": "0.384",
        "confidence_level": "0.683",
        "model_scope": "flat_lcdm",
        "data_scope": "union3_sn_only",
        "interval_kind": "frequentist_profile_chi_square",
        "statistical_semantics": "frequentist_profile_chi_square",
        "confidence_definition": "delta_chi_square_1_one_parameter",
        "delta_chi_square": "1",
        "fit_diagnostics": {"chi_square": "24.0", "degrees_of_freedom": 20},
        "claim_scope": "paper_reported_frequentist_interval",
        "source_anchor_ids": anchor_ids,
        "publication_ready": False,
        "review_required": True,
    }
    candidate_core = {
        key: value
        for key, value in candidate.items()
        if key not in {"candidate_id", "candidate_hash", "claim_hash"}
    }
    expected_claim_hash = _sha256_bytes(
        " ".join(_UNION3_CLAIM_TEXT.split()).encode("utf-8")
    )
    expected_candidate_hash = _canonical_digest(expected_core)
    if (
        not isinstance(anchor_ids, list)
        or len(anchor_ids) != len(_UNION3_ANCHOR_SPECS)
        or len(set(anchor_ids)) != len(anchor_ids)
        or candidate_core != expected_core
        or candidate.get("claim_hash") != expected_claim_hash
        or candidate.get("candidate_hash") != expected_candidate_hash
        or candidate.get("candidate_id") != f"sha256:{expected_candidate_hash}"
    ):
        raise Union3ResearchLoopError(
            "source_candidate_binding_invalid",
            "The registered source candidate does not match the fixed Union3 claim",
        )
    return candidate


def _validate_source_extraction_binding(
    source: SourceDocument,
    extraction: SourceExtraction,
) -> dict[str, Any]:
    """Validate the complete fixed reader receipt and return its sole candidate."""

    try:
        validate_registered_union3_source_receipt(
            raw_artifacts=source.raw_artifacts,
            raw_artifact_hashes=source.raw_artifact_hashes,
            source_metadata=source.source_metadata,
        )
    except Union3ReaderError as exc:
        raise Union3ResearchLoopError(
            "source_document_binding_invalid",
            "The source document does not match the registered Union3 v4 source",
        ) from exc

    if (
        source.lifecycle_status != "COMPLETED"
        or source.coverage_status != "UNION3_TABLE9_INTERVAL_READY"
        or source.source_profile_key != UNION3_SOURCE_PROFILE_KEY
        or source.canonical_identifier != UNION3_ARXIV_ID
        or source.source_url != UNION3_SOURCE_URL
        or not isinstance(source.version, int)
        or source.version < 1
    ):
        raise Union3ResearchLoopError(
            "source_document_binding_invalid",
            "The source document does not match the registered Union3 v4 source",
        )
    expected_document_hash = build_union3_source_document_hash(
        pdf_sha256=UNION3_PDF_SHA256,
        version=source.version,
    )
    if source.source_document_hash != expected_document_hash:
        raise Union3ResearchLoopError(
            "source_document_hash_invalid",
            "The source document hash does not match its immutable identity",
        )
    if (
        extraction.source_document_id != source.id
        or extraction.user_id != source.user_id
        or extraction.schema_version != UNION3_EXTRACTION_SCHEMA_VERSION
        or extraction.reader_version != UNION3_READER_VERSION
        or extraction.input_source_document_hash != source.source_document_hash
        or extraction.extraction_artifacts != []
        or extraction.extraction_artifact_hashes != {}
        or not isinstance(extraction.extraction_payload, dict)
        or set(extraction.extraction_payload) != _EXTRACTION_KEYS
    ):
        raise Union3ResearchLoopError(
            "source_extraction_binding_mismatch",
            "The source extraction is not bound to the registered source",
        )

    payload = extraction.extraction_payload
    unsigned_payload = dict(payload)
    embedded_hash = unsigned_payload.pop("extraction_hash", None)
    observed_payload_hash = _canonical_digest(unsigned_payload)
    if (
        embedded_hash != observed_payload_hash
        or extraction.extraction_payload_hash != observed_payload_hash
    ):
        raise Union3ResearchLoopError(
            "source_extraction_hash_invalid",
            "The source extraction payload no longer matches its immutable hash",
        )
    source_payload = payload.get("source")
    text_projection = (
        source_payload.get("text_projection")
        if isinstance(source_payload, dict)
        else None
    )
    if (
        not isinstance(text_projection, dict)
        or set(text_projection) != {"method", "sha256"}
        or text_projection.get("method") != UNION3_TEXT_PROJECTION_METHOD
        or not _HEX_64.fullmatch(str(text_projection.get("sha256") or ""))
    ):
        raise Union3ResearchLoopError(
            "source_text_projection_invalid",
            "The source extraction is not bound to a deterministic PDF text projection",
        )
    projection_sha256 = str(text_projection["sha256"])
    expected_source_payload = {
        "source_profile_key": UNION3_SOURCE_PROFILE_KEY,
        "canonical_identifier": UNION3_ARXIV_ID,
        "source_url": UNION3_SOURCE_URL,
        "authority": "arxiv_pdf",
        "pdf_sha256": UNION3_PDF_SHA256,
        "source_document_hash": source.source_document_hash,
        "version": source.version,
        "raw_artifacts": _registered_extraction_raw_artifacts(
            str(source.raw_artifacts[2]["artifact_ref"])
        ),
        "raw_artifact_hashes": {"pdf": UNION3_PDF_SHA256},
        "text_projection": {
            "method": UNION3_TEXT_PROJECTION_METHOD,
            "sha256": projection_sha256,
        },
    }
    if (
        payload.get("schema_version") != UNION3_EXTRACTION_SCHEMA_VERSION
        or payload.get("reader_version") != UNION3_READER_VERSION
        or payload.get("source") != expected_source_payload
        or payload.get("coverage_status") != "UNION3_TABLE9_INTERVAL_READY"
        or payload.get("limitations") != _UNION3_LIMITATIONS
        or payload.get("publication_ready") is not False
        or payload.get("review_required") is not True
    ):
        raise Union3ResearchLoopError(
            "source_extraction_contract_invalid",
            "The source extraction does not match the fixed Union3 reader contract",
        )

    anchors = payload.get("anchors")
    if not isinstance(anchors, list) or len(anchors) != len(_UNION3_ANCHOR_SPECS):
        raise Union3ResearchLoopError(
            "source_anchor_binding_invalid", "The registered source anchors are invalid"
        )
    expected_anchor_ids: list[str] = []
    previous_end = -1
    for anchor, (page_label, role, semantic_text, interpretation) in zip(
        anchors, _UNION3_ANCHOR_SPECS, strict=True
    ):
        if not isinstance(anchor, dict):
            raise Union3ResearchLoopError(
                "source_anchor_binding_invalid",
                "A registered source anchor is not an object",
            )
        raw_text = anchor.get("raw_text")
        observed_locator = anchor.get("locator")
        if not isinstance(raw_text, str) or not isinstance(observed_locator, dict):
            raise Union3ResearchLoopError(
                "source_anchor_binding_invalid",
                "A registered source anchor has no exact text or locator",
            )
        char_start = observed_locator.get("char_start")
        char_end = observed_locator.get("char_end")
        if (
            type(char_start) is not int
            or type(char_end) is not int
            or char_start < 0
            or char_end <= char_start
            or char_start < previous_end
            or char_end - char_start != len(raw_text)
            or _semantic_anchor_text(raw_text) != semantic_text
        ):
            raise Union3ResearchLoopError(
                "source_anchor_binding_invalid",
                "A registered source anchor is not an exact ordered PDF-text slice",
            )
        locator = {
            "source_kind": "arxiv_pdf_text",
            "section_label": "5.3",
            "pdf_page_label": page_label,
            "table_label": "Table 9",
            "role": role,
            "text_projection": UNION3_TEXT_PROJECTION_METHOD,
            "projection_sha256": projection_sha256,
            "char_start": char_start,
            "char_end": char_end,
        }
        expected_anchor_id = "sha256:" + _sha256_bytes(
            (
                source.source_document_hash
                + canonical_json(locator).decode("utf-8")
                + raw_text
            ).encode("utf-8")
        )
        expected_anchor = {
            "anchor_id": expected_anchor_id,
            "source_document_hash": source.source_document_hash,
            "locator": locator,
            "raw_text": raw_text,
            "interpretation": interpretation,
        }
        if anchor != expected_anchor:
            raise Union3ResearchLoopError(
                "source_anchor_binding_invalid",
                "A registered source anchor no longer matches its exact locator",
            )
        expected_anchor_ids.append(expected_anchor_id)
        previous_end = char_end

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise Union3ResearchLoopError(
            "source_candidates_invalid", "The registered source candidates are invalid"
        )
    candidate_id = str(
        candidates[0].get("candidate_id") if isinstance(candidates[0], dict) else ""
    )
    candidate = _candidate_for_source(extraction, candidate_id)
    if candidate.get("source_anchor_ids") != expected_anchor_ids:
        raise Union3ResearchLoopError(
            "source_candidate_anchor_mismatch",
            "The registered claim is not bound to the exact source anchors",
        )
    return candidate


def _primary_inputs_hash(args: dict[str, Any]) -> str:
    return _request_hash(
        {
            "workflow_key": args.get("workflow_key"),
            "normalized_inputs": args.get("normalized_inputs"),
            "dataset_pins": args.get("dataset_pins"),
        }
    )


def _validate_audit_primary_binding(
    *,
    audit: ClaimAudit,
    primary_job: ResearchJob,
    source: SourceDocument,
    extraction: SourceExtraction,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    expected_request_hash = _request_hash(
        _request_identity(
            user_id=audit.user_id,
            workspace_id=audit.workspace_id,
            source=source,
            extraction=extraction,
            candidate_id=str(candidate["candidate_id"]),
            supersedes_audit_id=audit.supersedes_audit_id,
        )
    )
    expected_args = _primary_job_args(
        audit_id=audit.id,
        source=source,
        extraction=extraction,
        candidate=candidate,
    )
    expected_job_id = f"union3-primary-{audit.id.hex}"
    expected_atomic_claim = _expected_atomic_claim(candidate)
    if (
        audit.user_id != source.user_id
        or audit.workspace_id != source.workspace_id
        or audit.source_document_id != source.id
        or audit.source_extraction_id != extraction.id
        or audit.request_hash != expected_request_hash
        or audit.mode != "execute_registered"
        or audit.claim_text != candidate["claim_text"]
        or audit.claim_schema_version != "union3_parameter_interval_reproduction_v1"
        or audit.atomic_claim != expected_atomic_claim
        or audit.claim_hash != candidate["claim_hash"]
        or audit.risk_level != "R3"
        or audit.source_kind != "arxiv"
        or audit.source_value != UNION3_ARXIV_ID
        or audit.dataset_hints != ["union3"]
        or audit.normalized_claims != [candidate]
        or audit.publication_ready is not False
    ):
        raise Union3ResearchLoopError(
            "audit_immutable_binding_mismatch",
            "The Claim Audit no longer matches its immutable source and claim",
        )
    if (
        primary_job.job_id != expected_job_id
        or primary_job.user_id != audit.user_id
        or primary_job.session_id is not None
        or primary_job.workspace_id != audit.workspace_id
        or primary_job.tool_name != UNION3_REPRODUCTION_WORKFLOW_ID
        or primary_job.workflow_key != UNION3_REPRODUCTION_WORKFLOW_ID
        or primary_job.background_backend != "https_worker"
        or primary_job.args_replayable is not True
        or primary_job.args != expected_args
        or primary_job.inputs_hash != _primary_inputs_hash(expected_args)
        or primary_job.capability_requirements
        != {
            "cpu_cores_min": 2,
            "memory_mb_min": 6144,
            "gpu_required": False,
            "protocol_version": "1",
        }
    ):
        raise Union3ResearchLoopError(
            "primary_job_immutable_binding_mismatch",
            "The primary job arguments or input hash no longer match the Audit",
        )
    verifier_job_id = audit.independent_verification_job_id
    expected_children = [expected_job_id]
    if verifier_job_id:
        expected_children.append(verifier_job_id)
    if list(audit.child_job_ids or []) != expected_children:
        raise Union3ResearchLoopError(
            "audit_job_binding_mismatch",
            "The Audit child-job ledger does not match the registered workflow",
        )
    graph = audit.evidence_graph
    if not isinstance(graph, dict) or any(
        graph.get(key) != value
        for key, value in {
            "source_document_id": str(source.id),
            "source_extraction_id": str(extraction.id),
            "candidate_id": str(candidate["candidate_id"]),
            "primary_job_id": expected_job_id,
            "independent_verification_job_id": verifier_job_id,
        }.items()
    ):
        raise Union3ResearchLoopError(
            "audit_evidence_graph_binding_mismatch",
            "The Audit evidence graph no longer matches its immutable jobs",
        )
    return expected_args


def _task_verification_public_key(key_id: str) -> str:
    try:
        keyring = settings.worker_task_verification_keyring
    except ValueError as exc:
        raise Union3ResearchLoopError(
            "worker_task_keyring_invalid", "The Worker task keyring is invalid"
        ) from exc
    if key_id == str(settings.worker_task_signing_key_id or "").strip():
        current = str(settings.worker_task_signing_public_key or "").strip()
        if current:
            return current
    return str(keyring.get(key_id) or "").strip()


def _validate_attempt_envelope(
    *,
    attempt: ScienceExecutionAttempt,
    primary_job: ResearchJob,
    expected_args: dict[str, Any],
) -> str:
    envelope = attempt.task_envelope
    expected_keys = {
        "protocol_version",
        "job_id",
        "audit_id",
        "attempt_id",
        "lease_id",
        "workflow_key",
        "normalized_inputs",
        "input_sha256",
        "dataset_pins",
        "image_digest",
        "git_commit",
        "resource_limits",
        "deadline",
        "lease_expires_at",
        "server_signature",
    }
    if not isinstance(envelope, dict) or set(envelope) != expected_keys:
        raise Union3ResearchLoopError(
            "worker_task_envelope_invalid", "The signed Worker task envelope is invalid"
        )
    signature_record = envelope.get("server_signature")
    if (
        not isinstance(signature_record, dict)
        or set(signature_record) != {"algorithm", "key_id", "value"}
        or signature_record.get("algorithm") != "ed25519"
    ):
        raise Union3ResearchLoopError(
            "worker_task_signature_invalid", "The Worker task signature is invalid"
        )
    key_id = str(signature_record.get("key_id") or "")
    public_key_text = _task_verification_public_key(key_id)
    try:
        public_key_raw = base64.b64decode(public_key_text, validate=True)
        signature = base64.b64decode(
            str(signature_record.get("value") or ""), validate=True
        )
        if len(public_key_raw) != 32:
            raise ValueError
        unsigned = dict(envelope)
        unsigned.pop("server_signature")
        Ed25519PublicKey.from_public_bytes(public_key_raw).verify(
            signature, canonical_json(unsigned)
        )
    except (TypeError, ValueError, InvalidSignature) as exc:
        raise Union3ResearchLoopError(
            "worker_task_signature_invalid",
            "The Worker task envelope signature cannot be verified",
        ) from exc

    image_digest = str(envelope.get("image_digest") or "").lower()
    git_commit = str(envelope.get("git_commit") or "").lower()
    static_expected = {
        "protocol_version": WORKER_PROTOCOL_VERSION,
        "job_id": primary_job.job_id,
        "audit_id": str(attempt.audit_id),
        "attempt_id": str(attempt.id),
        "lease_id": attempt.lease_id,
        "workflow_key": UNION3_REPRODUCTION_WORKFLOW_ID,
        "normalized_inputs": expected_args["normalized_inputs"],
        "input_sha256": primary_job.inputs_hash,
        "dataset_pins": expected_args["dataset_pins"],
        "resource_limits": expected_args["resource_limits"],
    }
    if (
        attempt.input_hash != primary_job.inputs_hash
        or any(envelope.get(key) != value for key, value in static_expected.items())
        or not image_digest.startswith("sha256:")
        or _HEX_64.fullmatch(image_digest.removeprefix("sha256:")) is None
        or _FULL_GIT_SHA.fullmatch(git_commit) is None
    ):
        raise Union3ResearchLoopError(
            "worker_task_binding_mismatch",
            "The signed Worker task does not match the immutable primary job",
        )
    try:
        lease_expires_at = datetime.fromisoformat(
            str(envelope["lease_expires_at"]).replace("Z", "+00:00")
        )
        deadline = datetime.fromisoformat(
            str(envelope["deadline"]).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise Union3ResearchLoopError(
            "worker_task_time_binding_invalid", "The Worker task times are invalid"
        ) from exc
    if (
        _as_utc(lease_expires_at) > _as_utc(attempt.lease_expires_at)
        or _as_utc(deadline) < _as_utc(attempt.lease_expires_at)
        or _as_utc(deadline) <= _as_utc(lease_expires_at)
    ):
        raise Union3ResearchLoopError(
            "worker_task_time_binding_invalid",
            "The Worker task times do not match the durable lease",
        )
    return "sha256:" + _canonical_digest(envelope)


def _verifier_args(
    *,
    audit: ClaimAudit,
    source: SourceDocument,
    extraction: SourceExtraction,
    primary_job: ResearchJob,
    attempt: ScienceExecutionAttempt,
    task_envelope_hash: str,
    artifact_binding_hash: str,
) -> dict[str, str]:
    return {
        "workflow_key": UNION3_REPRODUCTION_WORKFLOW_ID,
        "audit_id": str(audit.id),
        "source_document_id": str(source.id),
        "source_document_hash": source.source_document_hash,
        "source_extraction_id": str(extraction.id),
        "source_extraction_hash": extraction.extraction_payload_hash,
        "claim_hash": str(audit.claim_hash),
        "primary_job_id": primary_job.job_id,
        "primary_job_inputs_hash": primary_job.inputs_hash,
        "attempt_id": str(attempt.id),
        "attempt_input_hash": attempt.input_hash,
        "attempt_result_hash": f"sha256:{attempt.result_hash}",
        "task_envelope_hash": task_envelope_hash,
        "artifact_binding_hash": artifact_binding_hash,
    }


async def create_union3_reproduction_audit(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    source_document_id: uuid.UUID,
    candidate_id: str,
    workflow_key: str,
    supersedes_audit_id: uuid.UUID | None = None,
) -> tuple[ClaimAudit, ResearchJob]:
    """Create an owner-bound Audit and one queued HTTPS-worker job."""

    if workflow_key != UNION3_REPRODUCTION_WORKFLOW_ID:
        raise Union3ResearchLoopError(
            "workflow_not_registered",
            "Only the registered Union3 workflow is available",
            status_code=422,
        )
    owner = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if owner is None or str(owner.account_status or "").upper() != "ACTIVE":
        raise Union3ResearchLoopError(
            "audit_owner_inactive", "The account is not active", status_code=403
        )
    workspace = await db.scalar(
        select(ResearchWorkspace)
        .where(
            ResearchWorkspace.id == workspace_id,
            ResearchWorkspace.user_id == user_id,
        )
        .with_for_update()
    )
    if workspace is None:
        raise Union3ResearchLoopError(
            "workspace_not_found", "Research Workspace not found", status_code=404
        )
    if workspace.status != "ACTIVE":
        raise Union3ResearchLoopError(
            "workspace_archived",
            "Archived Workspaces are read-only; restore it before creating a run",
            status_code=409,
        )
    if supersedes_audit_id is not None:
        parent = await db.scalar(
            select(ClaimAudit)
            .where(
                ClaimAudit.id == supersedes_audit_id,
                ClaimAudit.user_id == user_id,
            )
            .with_for_update()
        )
        if parent is None:
            raise Union3ResearchLoopError(
                "superseded_audit_not_found",
                "The earlier registered Audit was not found",
                status_code=404,
            )
        if parent.lifecycle_status not in {"COMPLETED", "FAILED_FINAL", "CANCELLED"}:
            raise Union3ResearchLoopError(
                "superseded_audit_not_terminal",
                "Create a revision only after the earlier Audit is terminal",
                status_code=409,
            )
        if (
            parent.mode != "execute_registered"
            or parent.claim_schema_version
            != "union3_parameter_interval_reproduction_v1"
            or parent.workspace_id != workspace_id
            or parent.source_document_id != source_document_id
            or not isinstance(parent.atomic_claim, dict)
            or parent.atomic_claim.get("candidate_id") != candidate_id
        ):
            raise Union3ResearchLoopError(
                "superseded_audit_binding_invalid",
                "The earlier Audit is not the same registered Union3 workflow",
                status_code=409,
            )
    source = await db.scalar(
        select(SourceDocument).where(
            SourceDocument.id == source_document_id,
            SourceDocument.workspace_id == workspace_id,
            SourceDocument.user_id == user_id,
        )
    )
    if source is None:
        raise Union3ResearchLoopError(
            "source_document_not_found", "Source document not found", status_code=404
        )
    extraction = await db.scalar(
        select(SourceExtraction).where(
            SourceExtraction.source_document_id == source.id,
            SourceExtraction.user_id == user_id,
            SourceExtraction.schema_version == UNION3_EXTRACTION_SCHEMA_VERSION,
            SourceExtraction.reader_version == UNION3_READER_VERSION,
        )
    )
    if extraction is None:
        raise Union3ResearchLoopError(
            "source_extraction_not_found",
            "Source extraction not found",
            status_code=404,
        )
    registered_candidate = _validate_source_extraction_binding(source, extraction)
    candidate = _candidate_for_source(extraction, candidate_id)
    if candidate != registered_candidate:
        raise Union3ResearchLoopError(
            "source_candidate_binding_invalid",
            "The requested candidate is not the registered Union3 candidate",
        )
    request_hash = _request_hash(
        _request_identity(
            user_id=user_id,
            workspace_id=workspace_id,
            source=source,
            extraction=extraction,
            candidate_id=candidate_id,
            supersedes_audit_id=supersedes_audit_id,
        )
    )
    existing = await db.scalar(
        select(ClaimAudit).where(
            ClaimAudit.user_id == user_id,
            ClaimAudit.request_hash == request_hash,
        )
    )
    if existing is not None:
        if not existing.child_job_ids:
            raise Union3ResearchLoopError(
                "audit_job_binding_missing", "The existing Audit has no primary job"
            )
        existing_job = await db.get(ResearchJob, existing.child_job_ids[0])
        if existing_job is None:
            raise Union3ResearchLoopError(
                "audit_job_binding_missing", "The existing primary job is missing"
            )
        _validate_audit_primary_binding(
            audit=existing,
            primary_job=existing_job,
            source=source,
            extraction=extraction,
            candidate=candidate,
        )
        return existing, existing_job

    active_count = int(
        await db.scalar(
            select(func.count())
            .select_from(ClaimAudit)
            .where(
                ClaimAudit.user_id == user_id,
                ClaimAudit.lifecycle_status.in_({"QUEUED", "RUNNING"}),
            )
        )
        or 0
    )
    if active_count >= settings.claim_audit_max_active_per_user:
        raise Union3ResearchLoopError(
            "claim_audit_active_limit_reached",
            "Claim Audit active-work limit reached",
            status_code=429,
        )

    audit_id = uuid.uuid4()
    audit = ClaimAudit(
        id=audit_id,
        user_id=user_id,
        workspace_id=workspace_id,
        source_document_id=source.id,
        source_extraction_id=extraction.id,
        supersedes_audit_id=supersedes_audit_id,
        request_hash=request_hash,
        lifecycle_status="QUEUED",
        scientific_verdict=None,
        mode="execute_registered",
        claim_text=str(candidate["claim_text"]),
        claim_schema_version="union3_parameter_interval_reproduction_v1",
        atomic_claim=_expected_atomic_claim(candidate),
        claim_hash=str(candidate["claim_hash"]),
        risk_level="R3",
        review_status="NOT_SUBMITTED",
        machine_support_eligible=False,
        reproduction_ready=False,
        publication_ready=False,
        progress=0.0,
        progress_stage="waiting_for_worker",
        source_kind="arxiv",
        source_value=source.canonical_identifier,
        evidence_input_refs=[],
        dataset_hints=["union3"],
        normalized_claims=[dict(candidate)],
        capability_gaps=[],
        evidence_record_ids=[],
        child_job_ids=[],
        evidence_graph={
            "source_document_id": str(source.id),
            "source_extraction_id": str(extraction.id),
            "candidate_id": candidate_id,
            "primary_job_id": None,
            "independent_verification_job_id": None,
            "supersedes_audit_id": (
                str(supersedes_audit_id) if supersedes_audit_id else None
            ),
        },
    )
    args = _primary_job_args(
        audit_id=audit_id,
        source=source,
        extraction=extraction,
        candidate=candidate,
    )
    job_id = f"union3-primary-{audit_id.hex}"
    job = ResearchJob(
        job_id=job_id,
        user_id=user_id,
        session_id=None,
        workspace_id=workspace_id,
        tool_name=UNION3_REPRODUCTION_WORKFLOW_ID,
        workflow_key=UNION3_REPRODUCTION_WORKFLOW_ID,
        inputs_hash=_primary_inputs_hash(args),
        args=args,
        args_replayable=True,
        description="Registered Union3 Table 9 flat-LambdaCDM reproduction",
        status="QUEUED",
        progress=0.0,
        progress_message="waiting_for_worker",
        result=None,
        attestation=None,
        error=None,
        error_class=None,
        background_backend="https_worker",
        capability_requirements={
            "cpu_cores_min": 2,
            "memory_mb_min": 6144,
            "gpu_required": False,
            "protocol_version": "1",
        },
        created_at=_utcnow(),
    )
    audit.child_job_ids = [job_id]
    audit.evidence_graph = {**audit.evidence_graph, "primary_job_id": job_id}
    db.add_all([audit, job])
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        existing = await db.scalar(
            select(ClaimAudit).where(
                ClaimAudit.user_id == user_id,
                ClaimAudit.request_hash == request_hash,
            )
        )
        if existing is None or not existing.child_job_ids:
            raise Union3ResearchLoopError(
                "audit_create_conflict", "Could not create the registered Audit"
            ) from exc
        existing_job = await db.get(ResearchJob, existing.child_job_ids[0])
        if existing_job is None:
            raise Union3ResearchLoopError(
                "audit_job_binding_missing", "The existing primary job is missing"
            ) from exc
        _validate_audit_primary_binding(
            audit=existing,
            primary_job=existing_job,
            source=source,
            extraction=extraction,
            candidate=candidate,
        )
        return existing, existing_job
    await db.refresh(audit)
    await db.refresh(job)
    return audit, job


async def create_union3_reproduction_revision(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    supersedes_audit_id: uuid.UUID,
) -> tuple[ClaimAudit, ResearchJob]:
    """Create one idempotent immutable successor to a terminal registered Audit."""

    parent = await db.scalar(
        select(ClaimAudit).where(
            ClaimAudit.id == supersedes_audit_id,
            ClaimAudit.user_id == user_id,
        )
    )
    atomic_claim = parent.atomic_claim if parent is not None else None
    if (
        parent is None
        or parent.workspace_id is None
        or parent.source_document_id is None
        or not isinstance(atomic_claim, dict)
        or not str(atomic_claim.get("candidate_id") or "")
    ):
        raise Union3ResearchLoopError(
            "superseded_audit_not_found",
            "The earlier registered Audit was not found",
            status_code=404,
        )
    return await create_union3_reproduction_audit(
        db,
        user_id=user_id,
        workspace_id=parent.workspace_id,
        source_document_id=parent.source_document_id,
        candidate_id=str(atomic_claim["candidate_id"]),
        workflow_key=UNION3_REPRODUCTION_WORKFLOW_ID,
        supersedes_audit_id=parent.id,
    )


async def retry_union3_reproduction_audit(
    db: AsyncSession,
    *,
    audit_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ClaimAudit:
    """Reset only the registered HTTPS job after an infrastructure failure."""

    audit = (
        await db.execute(
            select(ClaimAudit)
            .where(ClaimAudit.id == audit_id, ClaimAudit.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if audit is None:
        raise Union3ResearchLoopError(
            "claim_audit_not_found", "Claim Audit not found", status_code=404
        )
    if (
        audit.lifecycle_status != "FAILED_RETRYABLE"
        or audit.scientific_verdict is not None
        or audit.reproduction_ready is not False
        or audit.claim_schema_version != "union3_parameter_interval_reproduction_v1"
    ):
        raise Union3ResearchLoopError(
            "claim_audit_not_retryable",
            "Only a retryable registered Union3 infrastructure failure can be retried",
        )
    graph = audit.evidence_graph if isinstance(audit.evidence_graph, dict) else {}
    primary_job_id = str(graph.get("primary_job_id") or "")
    primary_job = (
        await db.execute(
            select(ResearchJob)
            .where(ResearchJob.job_id == primary_job_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    source = await db.get(SourceDocument, audit.source_document_id)
    extraction = await db.get(SourceExtraction, audit.source_extraction_id)
    if primary_job is None or source is None or extraction is None:
        raise Union3ResearchLoopError(
            "retry_binding_missing", "The registered retry binding is incomplete"
        )
    candidate = _validate_source_extraction_binding(source, extraction)
    _validate_audit_primary_binding(
        audit=audit,
        primary_job=primary_job,
        source=source,
        extraction=extraction,
        candidate=candidate,
    )
    active_attempt = await db.scalar(
        select(ScienceExecutionAttempt.id).where(
            ScienceExecutionAttempt.job_id == primary_job.job_id,
            ScienceExecutionAttempt.status.in_({"LEASED", "RUNNING"}),
        )
    )
    if primary_job.status != "FAILED" or active_attempt is not None:
        raise Union3ResearchLoopError(
            "claim_audit_retry_conflict",
            "The registered primary job is not safely retryable",
        )

    audit.retry_count = int(audit.retry_count or 0) + 1
    audit.lifecycle_status = "QUEUED"
    audit.review_status = "NOT_SUBMITTED"
    audit.machine_support_eligible = False
    audit.reproduction_ready = False
    audit.publication_ready = False
    audit.progress = 0.0
    audit.progress_stage = "waiting_for_worker"
    audit.error = None
    audit.error_class = None
    audit.completed_at = None
    primary_job.status = "QUEUED"
    primary_job.progress = 0.0
    primary_job.progress_message = "waiting_for_worker"
    primary_job.result = None
    primary_job.attestation = None
    primary_job.error = None
    primary_job.error_class = None
    primary_job.current_attempt_id = None
    primary_job.started_at = None
    primary_job.completed_at = None
    await db.commit()
    await db.refresh(audit)
    return audit


def _job_attestation_valid(job: ResearchJob) -> bool:
    return verify_research_job_attestation(
        job.attestation,
        job_id=job.job_id,
        owner_id=job.user_id,
        session_id=job.session_id,
        tool_name=job.tool_name,
        inputs_hash=job.inputs_hash,
        args=job.args or {},
        args_replayable=job.args_replayable,
        result=job.result,
        background_backend=job.background_backend,
        completed_at=job.completed_at,
    )


async def verify_union3_attempt(
    db: AsyncSession,
    *,
    attempt_id: uuid.UUID,
) -> ClaimAudit:
    """Persist an HMAC-bound Render verification receipt, never ``SUPPORTED``."""

    _require_union3_feature_gates()

    attempt = await db.scalar(
        select(ScienceExecutionAttempt)
        .where(ScienceExecutionAttempt.id == attempt_id)
        .with_for_update()
    )
    if attempt is None:
        raise Union3ResearchLoopError(
            "science_attempt_not_found", "Science attempt not found", status_code=404
        )
    if attempt.status != "SUCCEEDED" or not isinstance(attempt.result, dict):
        raise Union3ResearchLoopError(
            "science_attempt_not_verifiable", "The science attempt is not verifiable"
        )
    observed_result_hash = canonical_result_hash(attempt.result)
    if attempt.result_hash != observed_result_hash:
        raise Union3ResearchLoopError(
            "science_attempt_result_hash_mismatch",
            "The science attempt result no longer matches its receipt",
        )
    primary_job = (
        await db.execute(
            select(ResearchJob)
            .where(ResearchJob.job_id == attempt.job_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    audit = (
        await db.execute(
            select(ClaimAudit)
            .where(ClaimAudit.id == attempt.audit_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if primary_job is None or audit is None:
        raise Union3ResearchLoopError(
            "science_attempt_binding_missing", "The attempt binding is incomplete"
        )
    if audit.lifecycle_status == "CANCELLED":
        raise Union3ResearchLoopError(
            "claim_audit_cancelled",
            "A cancelled Claim Audit cannot be independently verified",
        )
    if audit.lifecycle_status not in {"RUNNING", "COMPLETED"}:
        raise Union3ResearchLoopError(
            "claim_audit_not_verifiable",
            "The Claim Audit is not in a verifiable lifecycle state",
        )
    if (
        primary_job.user_id != attempt.user_id
        or audit.user_id != attempt.user_id
        or attempt.audit_id != audit.id
        or primary_job.workspace_id != audit.workspace_id
        or primary_job.workflow_key != UNION3_REPRODUCTION_WORKFLOW_ID
        or primary_job.background_backend != "https_worker"
        or primary_job.current_attempt_id != attempt.id
        or primary_job.status != "COMPLETED"
        or primary_job.completed_at is None
        or attempt.completed_at is None
    ):
        raise Union3ResearchLoopError(
            "science_attempt_binding_mismatch", "The attempt ownership binding failed"
        )
    job_result = primary_job.result
    if (
        not isinstance(job_result, dict)
        or set(job_result)
        != {
            "worker_attempt_id",
            "worker_result_hash",
            "worker_result",
            "scientific_verdict",
            "publication_ready",
        }
        or job_result.get("worker_attempt_id") != str(attempt.id)
        or job_result.get("worker_result_hash") != f"sha256:{attempt.result_hash}"
        or job_result.get("worker_result") != attempt.result
        or job_result.get("scientific_verdict") is not None
        or job_result.get("publication_ready") is not False
    ):
        raise Union3ResearchLoopError(
            "primary_job_result_binding_mismatch",
            "The primary job does not contain the exact untrusted worker receipt",
        )

    source = await db.get(SourceDocument, audit.source_document_id)
    extraction = await db.get(SourceExtraction, audit.source_extraction_id)
    if source is None or extraction is None:
        raise Union3ResearchLoopError(
            "source_binding_missing", "The immutable source binding is missing"
        )
    candidate = _validate_source_extraction_binding(source, extraction)
    expected_args = _validate_audit_primary_binding(
        audit=audit,
        primary_job=primary_job,
        source=source,
        extraction=extraction,
        candidate=candidate,
    )
    task_envelope_hash = _validate_attempt_envelope(
        attempt=attempt,
        primary_job=primary_job,
        expected_args=expected_args,
    )
    artifact_evidence = await _load_verified_science_artifacts(db, attempt=attempt)
    try:
        verification = await asyncio.to_thread(
            verify_union3_primary_result, attempt.result
        )
    except Exception as exc:
        raise Union3ResearchLoopError(
            "independent_verifier_unavailable",
            "The independent verifier could not produce a safe receipt",
            status_code=503,
        ) from exc
    completed_at = _utcnow()
    verifier_job_id = f"union3-verifier-{attempt.id.hex}"
    verifier_args = _verifier_args(
        audit=audit,
        source=source,
        extraction=extraction,
        primary_job=primary_job,
        attempt=attempt,
        task_envelope_hash=task_envelope_hash,
        artifact_binding_hash=artifact_evidence["binding_hash"],
    )
    verifier_inputs_hash = _request_hash(verifier_args)
    verifier_job = (
        await db.execute(
            select(ResearchJob)
            .where(ResearchJob.job_id == verifier_job_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    eligible = _verification_is_supportable(verification)
    if verifier_job is None:
        verifier_job = ResearchJob(
            job_id=verifier_job_id,
            user_id=audit.user_id,
            session_id=None,
            workspace_id=audit.workspace_id,
            tool_name="union3_independent_verifier_v1",
            workflow_key=UNION3_REPRODUCTION_WORKFLOW_ID,
            inputs_hash=verifier_inputs_hash,
            args=verifier_args,
            args_replayable=True,
            description="Render-side independent Union3 verification",
            status="completed",
            progress=1.0,
            progress_message="independent_verification_completed",
            result=verification,
            attestation=None,
            error=None,
            error_class=None,
            background_backend="reference_verifier",
            capability_requirements={},
            created_at=completed_at,
            started_at=completed_at,
            completed_at=completed_at,
        )
        db.add(verifier_job)
    else:
        if (
            verifier_job.user_id != audit.user_id
            or verifier_job.session_id is not None
            or verifier_job.workspace_id != audit.workspace_id
            or verifier_job.tool_name != "union3_independent_verifier_v1"
            or verifier_job.workflow_key != UNION3_REPRODUCTION_WORKFLOW_ID
            or verifier_job.background_backend != "reference_verifier"
            or verifier_job.args_replayable is not True
            or verifier_job.args != verifier_args
            or verifier_job.inputs_hash != verifier_inputs_hash
            or verifier_job.status != "completed"
            or verifier_job.progress != 1.0
            or verifier_job.result != verification
            or verifier_job.completed_at is None
            or not _job_attestation_valid(verifier_job)
        ):
            raise Union3ResearchLoopError(
                "independent_receipt_conflict",
                "An inconsistent independent verification receipt already exists",
            )
        graph = audit.evidence_graph if isinstance(audit.evidence_graph, dict) else {}
        expected_audit_verdict = (
            "SUPPORTED" if audit.reproduction_ready is True else "WITHHELD"
        )
        if (
            audit.lifecycle_status != "COMPLETED"
            or audit.scientific_verdict != expected_audit_verdict
            or audit.machine_support_eligible is not eligible
            or audit.publication_ready is not False
            or audit.independent_verification_job_id != verifier_job_id
            or graph.get("primary_attempt_id") != str(attempt.id)
            or graph.get("primary_result_hash") != f"sha256:{attempt.result_hash}"
            or graph.get("primary_task_envelope_hash") != task_envelope_hash
            or graph.get("primary_artifact_binding_hash")
            != artifact_evidence["binding_hash"]
            or graph.get("primary_artifacts") != artifact_evidence["artifacts"]
            or graph.get("primary_environment") != artifact_evidence["environment"]
            or graph.get("independent_verification_job_id") != verifier_job_id
            or graph.get("independent_verification_inputs_hash") != verifier_inputs_hash
            or graph.get("machine_support_eligible") is not eligible
        ):
            raise Union3ResearchLoopError(
                "independent_receipt_audit_mismatch",
                "The immutable verification receipt no longer matches its Audit",
            )
        return audit

    verifier_job.attestation = build_research_job_attestation(
        job_id=verifier_job.job_id,
        owner_id=verifier_job.user_id,
        session_id=verifier_job.session_id,
        tool_name=verifier_job.tool_name,
        inputs_hash=verifier_job.inputs_hash,
        args=verifier_job.args or {},
        args_replayable=verifier_job.args_replayable,
        result=verifier_job.result,
        background_backend=verifier_job.background_backend,
        completed_at=verifier_job.completed_at,
    )

    audit.lifecycle_status = "COMPLETED"
    audit.scientific_verdict = "WITHHELD"
    audit.machine_support_eligible = eligible
    audit.reproduction_ready = False
    audit.publication_ready = False
    audit.review_status = "PENDING" if eligible else "NOT_SUBMITTED"
    audit.progress = 1.0
    audit.progress_stage = (
        "waiting_for_human_review" if eligible else "machine_verification_withheld"
    )
    audit.independent_verification_job_id = verifier_job_id
    audit.completed_at = completed_at
    audit.error = (
        None if eligible else str(verification.get("failure_reason") or "")[:2000]
    )
    audit.error_class = (
        None
        if eligible
        else str(verification.get("failure_code") or "verification_gate_failed")[:255]
    )
    audit.child_job_ids = list(
        dict.fromkeys([*(audit.child_job_ids or []), verifier_job_id])
    )
    graph = dict(audit.evidence_graph or {})
    graph.update(
        {
            "primary_attempt_id": str(attempt.id),
            "primary_result_hash": f"sha256:{attempt.result_hash}",
            "primary_task_envelope_hash": task_envelope_hash,
            "primary_artifact_binding_hash": artifact_evidence["binding_hash"],
            "primary_artifacts": artifact_evidence["artifacts"],
            "primary_environment": artifact_evidence["environment"],
            "independent_verification_job_id": verifier_job_id,
            "independent_verification_inputs_hash": verifier_inputs_hash,
            "machine_support_eligible": eligible,
        }
    )
    audit.evidence_graph = graph
    await db.commit()
    await db.refresh(audit)
    return audit


def _verification_is_supportable(receipt: Any) -> bool:
    if not isinstance(receipt, dict):
        return False
    if (
        receipt.get("workflow_id") != UNION3_REPRODUCTION_WORKFLOW_ID
        or receipt.get("status") != "COMPLETED"
        or receipt.get("scientific_status") != "WITHHELD"
        or receipt.get("review_status") != "PENDING"
        or receipt.get("verification_ready") is not True
        or receipt.get("machine_support_eligible") is not True
        or receipt.get("reproduction_ready") is not False
        or receipt.get("publication_ready") is not False
    ):
        return False
    gates = receipt.get("verification_gates")
    return (
        bool(gates)
        and isinstance(gates, dict)
        and all(
            isinstance(gate, dict) and gate.get("passed") is True
            for gate in gates.values()
        )
    )


async def _load_finalization_bindings(
    db: AsyncSession,
    *,
    audit: ClaimAudit,
) -> tuple[
    SourceDocument,
    SourceExtraction,
    dict[str, Any],
    ResearchJob,
    ScienceExecutionAttempt,
    ResearchJob,
    dict[str, Any],
    dict[str, Any],
    list[ClaimAuditReview],
]:
    """Reload and revalidate every record used by the terminal transition."""

    source = await db.get(SourceDocument, audit.source_document_id)
    extraction = await db.get(SourceExtraction, audit.source_extraction_id)
    if source is None or extraction is None:
        raise Union3ResearchLoopError(
            "source_binding_invalid", "The immutable source binding is invalid"
        )
    candidate = _validate_source_extraction_binding(source, extraction)
    primary_job_id = f"union3-primary-{audit.id.hex}"
    primary_job = await db.get(ResearchJob, primary_job_id)
    if primary_job is None:
        raise Union3ResearchLoopError(
            "primary_attempt_binding_invalid", "The primary job binding is missing"
        )
    expected_primary_args = _validate_audit_primary_binding(
        audit=audit,
        primary_job=primary_job,
        source=source,
        extraction=extraction,
        candidate=candidate,
    )
    verifier_job = await db.get(ResearchJob, audit.independent_verification_job_id)
    if verifier_job is None or not isinstance(verifier_job.args, dict):
        raise Union3ResearchLoopError(
            "independent_receipt_invalid",
            "The independent verification receipt is missing or invalid",
        )
    try:
        attempt_id = uuid.UUID(str(verifier_job.args["attempt_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise Union3ResearchLoopError(
            "primary_attempt_binding_invalid", "The primary attempt binding is invalid"
        ) from exc
    attempt = await db.get(ScienceExecutionAttempt, attempt_id)
    if (
        attempt is None
        or attempt.job_id != primary_job.job_id
        or attempt.audit_id != audit.id
        or attempt.user_id != audit.user_id
        or attempt.status != "SUCCEEDED"
        or attempt.completed_at is None
        or not isinstance(attempt.result, dict)
        or not isinstance(attempt.result_hash, str)
        or attempt.result_hash != canonical_result_hash(attempt.result)
        or primary_job.status != "COMPLETED"
        or primary_job.completed_at is None
        or primary_job.current_attempt_id != attempt.id
        or primary_job.user_id != audit.user_id
        or primary_job.workspace_id != audit.workspace_id
    ):
        raise Union3ResearchLoopError(
            "primary_attempt_binding_invalid",
            "The primary attempt no longer matches the registered Audit",
        )
    primary_receipt = primary_job.result
    if (
        not isinstance(primary_receipt, dict)
        or set(primary_receipt)
        != {
            "worker_attempt_id",
            "worker_result_hash",
            "worker_result",
            "scientific_verdict",
            "publication_ready",
        }
        or primary_receipt.get("worker_attempt_id") != str(attempt.id)
        or primary_receipt.get("worker_result_hash") != f"sha256:{attempt.result_hash}"
        or primary_receipt.get("worker_result") != attempt.result
        or primary_receipt.get("scientific_verdict") is not None
        or primary_receipt.get("publication_ready") is not False
    ):
        raise Union3ResearchLoopError(
            "primary_job_result_binding_mismatch",
            "The primary job no longer contains its exact untrusted Worker receipt",
        )
    task_envelope_hash = _validate_attempt_envelope(
        attempt=attempt,
        primary_job=primary_job,
        expected_args=expected_primary_args,
    )
    artifact_evidence = await _load_verified_science_artifacts(db, attempt=attempt)
    expected_verifier_args = _verifier_args(
        audit=audit,
        source=source,
        extraction=extraction,
        primary_job=primary_job,
        attempt=attempt,
        task_envelope_hash=task_envelope_hash,
        artifact_binding_hash=artifact_evidence["binding_hash"],
    )
    if (
        verifier_job.job_id != f"union3-verifier-{attempt.id.hex}"
        or verifier_job.user_id != audit.user_id
        or verifier_job.session_id is not None
        or verifier_job.workspace_id != audit.workspace_id
        or verifier_job.tool_name != "union3_independent_verifier_v1"
        or verifier_job.workflow_key != UNION3_REPRODUCTION_WORKFLOW_ID
        or verifier_job.background_backend != "reference_verifier"
        or verifier_job.args_replayable is not True
        or verifier_job.args != expected_verifier_args
        or verifier_job.inputs_hash != _request_hash(expected_verifier_args)
        or verifier_job.status != "completed"
        or verifier_job.progress != 1.0
        or verifier_job.completed_at is None
        or not _job_attestation_valid(verifier_job)
        or not _verification_is_supportable(verifier_job.result)
    ):
        raise Union3ResearchLoopError(
            "independent_receipt_invalid",
            "The independent verification receipt is missing or invalid",
        )
    graph = audit.evidence_graph if isinstance(audit.evidence_graph, dict) else {}
    if (
        graph.get("primary_attempt_id") != str(attempt.id)
        or graph.get("primary_result_hash") != f"sha256:{attempt.result_hash}"
        or graph.get("primary_task_envelope_hash") != task_envelope_hash
        or graph.get("primary_artifact_binding_hash")
        != artifact_evidence["binding_hash"]
        or graph.get("primary_artifacts") != artifact_evidence["artifacts"]
        or graph.get("primary_environment") != artifact_evidence["environment"]
        or graph.get("independent_verification_job_id") != verifier_job.job_id
        or graph.get("independent_verification_inputs_hash") != verifier_job.inputs_hash
        or graph.get("machine_support_eligible") is not True
    ):
        raise Union3ResearchLoopError(
            "audit_evidence_graph_binding_mismatch",
            "The Audit evidence graph does not match its verified jobs",
        )
    try:
        recomputed_verification = await asyncio.to_thread(
            verify_union3_primary_result, attempt.result
        )
    except Exception as exc:
        raise Union3ResearchLoopError(
            "independent_verifier_unavailable",
            "The independent verification cannot be reproduced at finalization",
            status_code=503,
        ) from exc
    if canonical_json(recomputed_verification) != canonical_json(
        verifier_job.result
    ) or not _verification_is_supportable(recomputed_verification):
        raise Union3ResearchLoopError(
            "independent_receipt_recompute_mismatch",
            "The independent verification cannot be reproduced at finalization",
        )

    reviews = list(
        (
            await db.execute(
                select(ClaimAuditReview)
                .where(ClaimAuditReview.audit_id == audit.id)
                .order_by(
                    ClaimAuditReview.created_at.desc(), ClaimAuditReview.id.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    if not reviews:
        raise Union3ResearchLoopError(
            "human_review_missing", "A configured independent review is required"
        )
    latest_review = reviews[0]
    expected_anchors = list(candidate["source_anchor_ids"])
    if (
        audit.review_status != "APPROVED"
        or latest_review.decision != "APPROVED"
        or latest_review.supports_finalization is not True
        or latest_review.review_scope != "scientific_claim_review"
        or (
            latest_review.reviewer_user_id is not None
            and latest_review.reviewer_user_id == audit.user_id
        )
        or not str(latest_review.reviewer_username or "").startswith("reviewer:")
        or latest_review.reviewer_username
        == reviewer_pseudonym(audit.id, audit.user_id)
        or latest_review.audit_owner_user_id != audit.user_id
        or latest_review.workspace_id != audit.workspace_id
        or latest_review.source_document_id != source.id
        or latest_review.source_extraction_id != extraction.id
        or latest_review.candidate_id != candidate["candidate_id"]
        or latest_review.claim_hash != audit.claim_hash
        or latest_review.source_hash != source.source_document_hash
        or list(latest_review.anchor_ids or []) != expected_anchors
    ):
        raise Union3ResearchLoopError(
            "human_review_binding_invalid",
            "The latest independent review does not approve the exact evidence path",
        )
    return (
        source,
        extraction,
        candidate,
        primary_job,
        attempt,
        verifier_job,
        recomputed_verification,
        artifact_evidence,
        reviews,
    )


def _union3_profile_svg(primary_analysis: dict[str, Any]) -> str:
    """Render a deterministic, dependency-free plot from the verified receipt."""

    try:
        rows = primary_analysis["normalized_profile"]
        statistics = primary_analysis["statistics"]
        samples = [
            (
                float(row["omega_m"]),
                max(0.0, float(row["normalized_chi_square"])),
            )
            for row in rows
        ]
        best = float(statistics["omega_m_best"])
        lower = float(statistics["omega_m_lower"])
        upper = float(statistics["omega_m_upper"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Union3ResearchLoopError(
            "profile_plot_receipt_invalid",
            "The verified primary receipt cannot produce a profile plot",
        ) from exc
    if len(samples) != 41 or samples != sorted(samples):
        raise Union3ResearchLoopError(
            "profile_plot_receipt_invalid",
            "The verified primary profile grid is invalid",
        )

    width, height = 800, 480
    left, right, top, bottom = 78, 28, 28, 66
    plot_width = width - left - right
    plot_height = height - top - bottom
    x_min, x_max = samples[0][0], samples[-1][0]
    y_max = 10.0

    def x_position(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * plot_width

    def y_position(value: float) -> float:
        return top + (1.0 - min(value, y_max) / y_max) * plot_height

    points = " ".join(
        f"{x_position(omega):.2f},{y_position(delta):.2f}" for omega, delta in samples
    )
    marker_lines = "\n".join(
        (
            f'<line x1="{x_position(lower):.2f}" y1="{top}" '
            f'x2="{x_position(lower):.2f}" y2="{top + plot_height}" '
            'stroke="#7c3aed" stroke-dasharray="5 5"/>',
            f'<line x1="{x_position(best):.2f}" y1="{top}" '
            f'x2="{x_position(best):.2f}" y2="{top + plot_height}" '
            'stroke="#dc2626" stroke-width="2"/>',
            f'<line x1="{x_position(upper):.2f}" y1="{top}" '
            f'x2="{x_position(upper):.2f}" y2="{top + plot_height}" '
            'stroke="#7c3aed" stroke-dasharray="5 5"/>',
        )
    )
    delta_one_y = y_position(1.0)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="480" '
        'viewBox="0 0 800 480" role="img" '
        'aria-label="Union3 normalized profile chi-square curve">\n'
        '<rect width="800" height="480" fill="white"/>\n'
        f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" '
        f'y2="{top + plot_height}" stroke="#111827"/>\n'
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" '
        'stroke="#111827"/>\n'
        f'<line x1="{left}" y1="{delta_one_y:.2f}" x2="{left + plot_width}" '
        f'y2="{delta_one_y:.2f}" stroke="#059669" stroke-dasharray="7 5"/>\n'
        f"{marker_lines}\n"
        f'<polyline points="{points}" fill="none" stroke="#2563eb" '
        'stroke-width="2.5" stroke-linejoin="round"/>\n'
        '<text x="400" y="458" text-anchor="middle" font-family="sans-serif" '
        'font-size="17">Ωm</text>\n'
        '<text x="22" y="220" text-anchor="middle" font-family="sans-serif" '
        'font-size="17" transform="rotate(-90 22 220)">Δχ²</text>\n'
        '<text x="790" y="54" text-anchor="end" font-family="sans-serif" '
        'font-size="13" fill="#059669">Δχ² = 1</text>\n'
        '<text x="790" y="76" text-anchor="end" font-family="sans-serif" '
        'font-size="13" fill="#374151">Union3 SN-only flat ΛCDM</text>\n'
        "</svg>\n"
    )


def _artifact_manifest_record(
    row: WorkerArtifactIssuance,
    *,
    status: str,
    authoritative: bool = False,
) -> dict[str, Any]:
    """Reconstruct the exact durable manifest record without trusting JSON."""

    record: dict[str, Any] = {
        "issuance_id": str(row.id),
        "batch_id": str(row.batch_id),
        "artifact_name": row.artifact_name,
        "artifact_ref": row.authoritative_ref if authoritative else row.artifact_ref,
        "staging_artifact_ref": row.artifact_ref,
        "authoritative_artifact_ref": row.authoritative_ref,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "content_type": row.content_type,
        "upload_expires_at": _as_utc(row.expires_at).isoformat(),
        "status": status,
    }
    if authoritative:
        record["verification_method"] = row.verification_method
        record["version_id"] = row.authoritative_version_id
    return record


def _validate_worker_environment(
    payload: bytes,
    *,
    attempt: ScienceExecutionAttempt,
) -> dict[str, str]:
    try:
        environment = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Union3ResearchLoopError(
            "worker_environment_invalid",
            "The verified Worker environment receipt is not valid JSON",
        ) from exc
    if (
        not isinstance(environment, dict)
        or set(environment) != _WORKER_ENVIRONMENT_KEYS
        or any(not isinstance(value, str) for value in environment.values())
        or payload != canonical_json(environment) + b"\n"
    ):
        raise Union3ResearchLoopError(
            "worker_environment_invalid",
            "The verified Worker environment receipt is not canonical",
        )
    envelope = attempt.task_envelope
    expected = {
        "schema_version": "standard_astro_worker_environment_v1",
        "protocol_version": WORKER_PROTOCOL_VERSION,
        "workflow_key": UNION3_REPRODUCTION_WORKFLOW_ID,
        "git_commit": str(envelope.get("git_commit") or ""),
        "image_digest": str(envelope.get("image_digest") or ""),
        "mcmc": "not_applicable",
    }
    if any(environment.get(key) != value for key, value in expected.items()):
        raise Union3ResearchLoopError(
            "worker_environment_binding_mismatch",
            "The Worker environment does not match the signed task envelope",
        )
    for key in ("python_version", "platform_system", "platform_machine"):
        value = environment[key].strip()
        if not value or len(value) > 160 or any(ord(char) < 32 for char in value):
            raise Union3ResearchLoopError(
                "worker_environment_invalid",
                "The Worker environment contains an invalid platform field",
            )
    return environment


async def _load_verified_science_artifacts(
    db: AsyncSession,
    *,
    attempt: ScienceExecutionAttempt,
) -> dict[str, Any]:
    """Load and byte-check the three server-promoted Union3 artifacts."""

    rows = list(
        (
            await db.execute(
                select(WorkerArtifactIssuance)
                .where(WorkerArtifactIssuance.attempt_id == attempt.id)
                .order_by(WorkerArtifactIssuance.id.asc())
            )
        )
        .scalars()
        .all()
    )
    expected_names = set(_EXPECTED_WORKER_ARTIFACT_CONTENT_TYPES)
    if not rows or {row.artifact_name for row in rows} != expected_names:
        raise Union3ResearchLoopError(
            "worker_artifact_set_invalid",
            "The completed Worker attempt does not contain the registered artifact set",
        )
    if any(
        row.user_id != attempt.user_id
        or row.worker_node_id != attempt.worker_node_id
        or row.attempt_id != attempt.id
        or row.content_type
        != _EXPECTED_WORKER_ARTIFACT_CONTENT_TYPES.get(row.artifact_name)
        or _HEX_64.fullmatch(str(row.sha256 or "")) is None
        or row.size_bytes <= 0
        for row in rows
    ):
        raise Union3ResearchLoopError(
            "worker_artifact_ledger_invalid",
            "The Worker artifact ledger does not match the registered contract",
        )

    verified_rows = [row for row in rows if row.verified_at is not None]
    if (
        len(verified_rows) != len(expected_names)
        or {row.artifact_name for row in verified_rows} != expected_names
        or any(
            row.authoritative_cleaned_at is not None
            or row.verification_method not in {"s3_checksum_sha256", "streamed_sha256"}
            for row in verified_rows
        )
    ):
        raise Union3ResearchLoopError(
            "worker_artifact_verification_missing",
            "Every registered Worker artifact must have one live authoritative copy",
        )

    expected_manifest: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: str(item.id)):
        is_verified = row.verified_at is not None
        expected_manifest.append(
            _artifact_manifest_record(
                row,
                status=(
                    "STAGING_PENDING_CLEANUP"
                    if is_verified
                    else "SUPERSEDED_PENDING_CLEANUP"
                ),
            )
        )
        if is_verified:
            expected_manifest.append(
                _artifact_manifest_record(row, status="VERIFIED", authoritative=True)
            )
    if list(attempt.artifact_manifest or []) != expected_manifest:
        raise Union3ResearchLoopError(
            "worker_artifact_manifest_mismatch",
            "The Worker artifact manifest no longer matches its append-only ledger",
        )

    prefix = f"science-attempts/{attempt.user_id}/{attempt.id}/verified/"
    payloads: dict[str, bytes] = {}
    artifact_receipts: list[dict[str, Any]] = []
    for row in sorted(verified_rows, key=lambda item: item.artifact_name):
        if not str(row.authoritative_ref).startswith(prefix):
            raise Union3ResearchLoopError(
                "worker_artifact_owner_binding_mismatch",
                "An authoritative Worker artifact is outside its owner-scoped prefix",
            )
        try:
            payload = await asyncio.to_thread(download_fits, row.authoritative_ref)
        except Exception as exc:
            raise Union3ResearchLoopError(
                "worker_artifact_unavailable",
                "An authoritative Worker artifact cannot be read safely",
                status_code=503,
            ) from exc
        if len(payload) != row.size_bytes or _sha256_bytes(payload) != row.sha256:
            raise Union3ResearchLoopError(
                "worker_artifact_hash_mismatch",
                "An authoritative Worker artifact does not match its durable hash",
            )
        payloads[row.artifact_name] = payload
        artifact_receipts.append(
            {
                "name": row.artifact_name,
                "authoritative_ref": row.authoritative_ref,
                "sha256": f"sha256:{row.sha256}",
                "size_bytes": row.size_bytes,
                "content_type": row.content_type,
                "verification_method": row.verification_method,
                "version_id": row.authoritative_version_id,
                "verified_at": _as_utc(row.verified_at).isoformat(),
            }
        )

    if payloads["primary_analysis.json"] != canonical_json(attempt.result) + b"\n":
        raise Union3ResearchLoopError(
            "worker_primary_artifact_mismatch",
            "The authoritative primary analysis is not the completed Worker result",
        )
    if payloads["chi2_profile.svg"] != _union3_profile_svg(attempt.result).encode(
        "utf-8"
    ):
        raise Union3ResearchLoopError(
            "worker_profile_artifact_mismatch",
            "The authoritative profile chart is not derived from the completed result",
        )
    environment = _validate_worker_environment(
        payloads["environment.json"], attempt=attempt
    )
    binding_payload = {
        "attempt_id": str(attempt.id),
        "result_hash": f"sha256:{attempt.result_hash}",
        "artifacts": artifact_receipts,
        "environment": environment,
    }
    return {
        **binding_payload,
        "binding_hash": "sha256:" + _canonical_digest(binding_payload),
        "payloads": payloads,
    }


def _pack_files(
    *,
    audit: ClaimAudit,
    source: SourceDocument,
    extraction: SourceExtraction,
    candidate: dict[str, Any],
    primary_job: ResearchJob,
    attempt: ScienceExecutionAttempt,
    verifier_job: ResearchJob,
    verification: dict[str, Any],
    artifact_evidence: dict[str, Any],
    reviews: list[ClaimAuditReview],
    finalized_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    workflow = get_registered_workflow(UNION3_REPRODUCTION_WORKFLOW_ID)
    chosen_review = reviews[0]
    anchors = list(extraction.extraction_payload.get("anchors") or [])
    payloads = artifact_evidence["payloads"]
    try:
        primary_analysis = json.loads(payloads["primary_analysis.json"])
        verified_profile_svg = payloads["chi2_profile.svg"].decode("utf-8")
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Union3ResearchLoopError(
            "worker_artifact_pack_binding_invalid",
            "Verified Worker artifacts cannot be embedded in the Evidence Pack",
        ) from exc
    if primary_analysis != verification["primary_analysis"]:
        raise Union3ResearchLoopError(
            "worker_artifact_pack_binding_invalid",
            "The verified primary artifact differs from the independent receipt",
        )
    primary_statistics = primary_analysis["statistics"]
    review_rows = [
        {
            "review_id": str(review.id),
            "reviewer_pseudonym": review.reviewer_username,
            "decision": review.decision,
            "supports_finalization": review.supports_finalization,
            "claim_hash": review.claim_hash,
            "source_hash": review.source_hash,
            "anchor_ids": list(review.anchor_ids or []),
            "comment_sha256": _sha256_bytes(review.comment.encode("utf-8")),
            "created_at": review.created_at.isoformat(),
        }
        for review in reviews
    ]
    report = (
        "# Standard Astro Union3 reproduction / Union3 复现报告\n\n"
        "SUPPORTED means that this registered workflow reproduced the published "
        "constraint within its preset tolerances after independent computation and "
        "human review. It is not a new discovery and is not publication-ready.\n\n"
        "SUPPORTED 表示该注册流程经过独立复算与人工审核，在预设误差内复现了论文约束；"
        "它不是新发现，也不能直接作为论文发表结果。\n\n"
        f"Result: Ωm = {primary_statistics['omega_m_best']}, interval "
        f"[{primary_statistics['omega_m_lower']}, "
        f"{primary_statistics['omega_m_upper']}], profile-χ² with Δχ²=1.\n"
    )
    files = {
        "report.md": report,
        "citations.bib": (
            "@article{Rubin2023Union3,\n"
            "  title={Union Through UNITY: Cosmology with 2,000 SNe Using a Unified "
            "Bayesian Framework},\n"
            "  author={Rubin, D. and others},\n"
            "  eprint={2311.12098v4},\n"
            "  archivePrefix={arXiv}\n"
            "}\n"
        ),
        "provenance.json": {
            "workflow": workflow,
            "software_release": str(
                os.getenv("STANDARD_ASTRO_RELEASE") or "unreleased"
            ),
            "git_commit": _release_commit(),
            "primary_job_id": primary_job.job_id,
            "primary_job_inputs_hash": primary_job.inputs_hash,
            "primary_job_args_hash": "sha256:"
            + _canonical_digest(primary_job.args or {}),
            "primary_attempt_id": str(attempt.id),
            "primary_result_hash": f"sha256:{attempt.result_hash}",
            "primary_artifact_binding_hash": artifact_evidence["binding_hash"],
            "primary_artifacts": artifact_evidence["artifacts"],
            "worker_environment": artifact_evidence["environment"],
            "primary_task_envelope_hash": str(
                (audit.evidence_graph or {}).get("primary_task_envelope_hash")
            ),
            "worker_image_digest": str(attempt.task_envelope["image_digest"]),
            "worker_git_commit": str(attempt.task_envelope["git_commit"]),
            "independent_verification_job_id": verifier_job.job_id,
            "independent_verification_inputs_hash": verifier_job.inputs_hash,
            "finalized_at": finalized_at.isoformat(),
            "random_seed": "not_applicable",
        },
        "source_snapshot.json": {
            "source_document_id": str(source.id),
            "canonical_identifier": source.canonical_identifier,
            "source_url": source.source_url,
            "source_document_hash": source.source_document_hash,
            "raw_artifacts": list(source.raw_artifacts or []),
            "raw_artifact_hashes": dict(source.raw_artifact_hashes or {}),
            "version": source.version,
        },
        "anchors.json": anchors,
        "claims.json": {
            "atomic_claim": dict(audit.atomic_claim or {}),
            "source_candidate": candidate,
            "claim_hash": audit.claim_hash,
            "scientific_verdict": "SUPPORTED",
            "claim_scope": "reproduction_of_published_constraint",
        },
        "primary_analysis.json": primary_analysis,
        "independent_analysis.json": verification["independent_analysis"],
        "chi2_profile.svg": verified_profile_svg,
        "diagnostics.json": {
            "verification_gates": verification["verification_gates"],
            "mcmc_diagnostics": "not_applicable",
            "machine_support_eligible": True,
            "human_review_id": str(chosen_review.id),
            "primary_artifact_binding_hash": artifact_evidence["binding_hash"],
            "worker_environment_artifact_sha256": next(
                item["sha256"]
                for item in artifact_evidence["artifacts"]
                if item["name"] == "environment.json"
            ),
        },
        "reviews.json": review_rows,
        "limitations.json": {
            "publication_ready": False,
            "reproduction_ready": True,
            "claim_scope": "reproduction_of_published_constraint",
            "radiation_convention": UNION3_RADIATION_CONVENTION,
            "cannot_claim": [
                "full_2087_supernova_reanalysis",
                "posterior_interval",
                "H0_measurement",
                "dark_energy_evolution_discovery",
                "LambdaCDM_proof_or_refutation",
                "peer_reviewed_result",
            ],
            "notes": list(verification.get("limitations") or []),
        },
    }
    manifest_fields = {
        "audit_id": str(audit.id),
        "owner": str(audit.user_id),
        "workspace_id": str(audit.workspace_id),
        "created_at": audit.created_at.isoformat(),
        "finalized_at": finalized_at.isoformat(),
        "software_release": str(os.getenv("STANDARD_ASTRO_RELEASE") or "unreleased"),
        "git_commit": _release_commit(),
        "workflow_key": UNION3_REPRODUCTION_WORKFLOW_ID,
        "input_hashes": {
            "source_document": source.source_document_hash,
            "source_extraction": extraction.extraction_payload_hash,
            "claim": str(audit.claim_hash),
            "primary_job_inputs": primary_job.inputs_hash,
            "primary_task_envelope": str(
                (audit.evidence_graph or {}).get("primary_task_envelope_hash")
            ),
            "primary_result": f"sha256:{attempt.result_hash}",
            "primary_artifacts": artifact_evidence["binding_hash"],
            "independent_verification_inputs": verifier_job.inputs_hash,
        },
        "normalized_claims": [dict(audit.atomic_claim or {})],
        "claim_verdicts": [
            {"claim_hash": str(audit.claim_hash), "verdict": "SUPPORTED"}
        ],
        "scientific_verdict": "SUPPORTED",
        "claim_scope": "reproduction_of_published_constraint",
        "reproduction_ready": True,
        "publication_ready": False,
        "evidence_path": {
            "source_document_id": str(source.id),
            "source_extraction_id": str(extraction.id),
            "anchor_ids": list(chosen_review.anchor_ids or []),
            "primary_job_id": primary_job.job_id,
            "primary_attempt_id": str(attempt.id),
            "primary_artifacts": artifact_evidence["artifacts"],
            "independent_verification_job_id": verifier_job.job_id,
            "review_id": str(chosen_review.id),
        },
        "dataset_versions": workflow["sources"],
        "tool_configuration": workflow["method"],
        "limitations": files["limitations.json"],
    }
    return manifest_fields, files


def _public_key_from_private(private_text: str) -> str:
    try:
        private_raw = base64.b64decode(private_text, validate=True)
        private_key = Ed25519PrivateKey.from_private_bytes(private_raw)
    except (TypeError, ValueError) as exc:
        raise Union3ResearchLoopError(
            "evidence_v2_signing_key_invalid", "Evidence Pack signing key is invalid"
        ) from exc
    return base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


def _evidence_v2_trusted_keyring() -> list[dict[str, Any]]:
    current_key_id = str(settings.evidence_v2_signing_key_id or "").strip()
    current_public_key = str(settings.evidence_v2_signing_public_key or "").strip()
    current_private_key = str(settings.evidence_v2_signing_private_key or "").strip()
    if current_private_key:
        derived_public_key = _public_key_from_private(current_private_key)
        if current_public_key and current_public_key != derived_public_key:
            raise Union3ResearchLoopError(
                "evidence_v2_key_binding_invalid",
                "The configured Evidence Pack public key does not match its private key",
            )
        current_public_key = derived_public_key
    try:
        retired = settings.evidence_v2_verification_keyring
    except ValueError as exc:
        raise Union3ResearchLoopError(
            "evidence_v2_keyring_invalid", "The Evidence Pack keyring is invalid"
        ) from exc
    records: list[dict[str, Any]] = []
    if current_key_id and current_public_key:
        records.append(
            {
                "key_id": current_key_id,
                "algorithm": EVIDENCE_PACK_V2_SIGNATURE_ALGORITHM,
                "public_key": current_public_key,
                "status": "active",
            }
        )
    for key_id, record in sorted(retired.items()):
        public_key = str(record.get("public_key") or "")
        if key_id == current_key_id:
            if current_public_key and public_key != current_public_key:
                raise Union3ResearchLoopError(
                    "evidence_v2_key_binding_invalid",
                    "The current Evidence Pack key id has conflicting public keys",
                )
            continue
        records.append(dict(record))
    return records


def _stable_finalized_at(audit: ClaimAudit, review: ClaimAuditReview) -> datetime:
    """Use the final immutable prerequisite time so retries produce one pack."""

    candidates = [review.created_at]
    if audit.completed_at is not None:
        candidates.append(audit.completed_at)
    return max(_as_utc(value) for value in candidates)


async def _validate_finalized_pack(
    *,
    audit: ClaimAudit,
    pack: EvidencePack,
    source: SourceDocument,
    extraction: SourceExtraction,
    candidate: dict[str, Any],
    primary_job: ResearchJob,
    attempt: ScienceExecutionAttempt,
    verifier_job: ResearchJob,
    artifact_evidence: dict[str, Any],
    reviews: list[ClaimAuditReview],
) -> None:
    latest_review = reviews[0]
    pack_hash = str(pack.pack_hash or "")
    expected_artifact_ref = (
        f"evidence-packs/v2/{audit.user_id}/{audit.id}/"
        f"{pack_hash.removeprefix('sha256:')}.zip"
    )
    expected_manifest_hash = (
        "sha256:" + _sha256_bytes(jcs_canonicalize(pack.manifest))
        if isinstance(pack.manifest, dict)
        else ""
    )
    expected_input_hashes = {
        "source_document": source.source_document_hash,
        "source_extraction": extraction.extraction_payload_hash,
        "claim": str(audit.claim_hash),
        "primary_job_inputs": primary_job.inputs_hash,
        "primary_task_envelope": str(
            (audit.evidence_graph or {}).get("primary_task_envelope_hash")
        ),
        "primary_result": f"sha256:{attempt.result_hash}",
        "primary_artifacts": artifact_evidence["binding_hash"],
        "independent_verification_inputs": verifier_job.inputs_hash,
    }
    expected_evidence_path = {
        "source_document_id": str(source.id),
        "source_extraction_id": str(extraction.id),
        "anchor_ids": list(latest_review.anchor_ids or []),
        "primary_job_id": primary_job.job_id,
        "primary_attempt_id": str(attempt.id),
        "primary_artifacts": artifact_evidence["artifacts"],
        "independent_verification_job_id": verifier_job.job_id,
        "review_id": str(latest_review.id),
    }
    manifest = pack.manifest if isinstance(pack.manifest, dict) else {}
    graph = audit.evidence_graph if isinstance(audit.evidence_graph, dict) else {}
    if (
        pack.audit_id != audit.id
        or pack.user_id != audit.user_id
        or pack.status != "FINALIZED"
        or pack.schema_version != 2
        or pack.signature_algorithm != EVIDENCE_PACK_V2_SIGNATURE_ALGORITHM
        or not isinstance(pack.id, uuid.UUID)
        or not pack_hash.startswith("sha256:")
        or _HEX_64.fullmatch(pack_hash.removeprefix("sha256:")) is None
        or pack.artifact_ref != expected_artifact_ref
        or pack.manifest_hash != expected_manifest_hash
        or not pack.signature
        or pack.key_id != manifest.get("key_id")
        or pack.public_key_fingerprint != manifest.get("public_key_fingerprint")
        or pack.finalized_at is None
        or manifest.get("audit_id") != str(audit.id)
        or manifest.get("owner") != str(audit.user_id)
        or manifest.get("workspace_id") != str(audit.workspace_id)
        or manifest.get("workflow_key") != UNION3_REPRODUCTION_WORKFLOW_ID
        or manifest.get("scientific_verdict") != "SUPPORTED"
        or manifest.get("claim_scope") != "reproduction_of_published_constraint"
        or manifest.get("reproduction_ready") is not True
        or manifest.get("publication_ready") is not False
        or manifest.get("input_hashes") != expected_input_hashes
        or manifest.get("normalized_claims") != [dict(audit.atomic_claim or {})]
        or manifest.get("claim_verdicts")
        != [{"claim_hash": str(audit.claim_hash), "verdict": "SUPPORTED"}]
        or manifest.get("evidence_path") != expected_evidence_path
        or manifest.get("finalized_at") != _as_utc(pack.finalized_at).isoformat()
        or graph.get("review_id") != str(latest_review.id)
        or graph.get("primary_artifact_binding_hash")
        != artifact_evidence["binding_hash"]
        or graph.get("primary_artifacts") != artifact_evidence["artifacts"]
        or graph.get("primary_environment") != artifact_evidence["environment"]
        or graph.get("evidence_pack_id") != str(pack.id)
        or graph.get("evidence_pack_hash") != pack_hash
    ):
        raise Union3ResearchLoopError(
            "evidence_pack_binding_invalid",
            "The finalized Evidence Pack no longer matches its immutable Audit",
        )
    fact_report = audit.fact_check_report
    if (
        not isinstance(fact_report, dict)
        or fact_report.get("finalizer") != "deterministic_union3_reproduction_v1"
        or fact_report.get("scientific_verdict") != "SUPPORTED"
        or fact_report.get("reproduction_ready") is not True
        or fact_report.get("publication_ready") is not False
        or fact_report.get("pack_hash") != pack_hash
        or fact_report.get("manifest_hash") != pack.manifest_hash
    ):
        raise Union3ResearchLoopError(
            "evidence_pack_finalizer_receipt_invalid",
            "The deterministic finalizer receipt does not match the Evidence Pack",
        )
    try:
        pack_bytes = await asyncio.to_thread(download_fits, pack.artifact_ref)
    except Exception as exc:
        raise Union3ResearchLoopError(
            "evidence_pack_artifact_unavailable",
            "The finalized Evidence Pack artifact cannot be read safely",
            status_code=503,
        ) from exc
    if "sha256:" + _sha256_bytes(pack_bytes) != pack_hash:
        raise Union3ResearchLoopError(
            "evidence_pack_artifact_hash_mismatch",
            "The finalized Evidence Pack artifact does not match its ledger hash",
        )
    verification = await asyncio.to_thread(
        verify_evidence_pack_v2,
        pack_bytes,
        trusted_keyring=_evidence_v2_trusted_keyring(),
    )
    if not verification.valid or verification.manifest != manifest:
        raise Union3ResearchLoopError(
            "evidence_pack_signature_invalid",
            "The finalized Evidence Pack no longer passes trusted verification",
        )
    try:
        with zipfile.ZipFile(BytesIO(pack_bytes), "r") as archive:
            observed_signature = archive.read("manifest.sig").decode("ascii")
    except (KeyError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise Union3ResearchLoopError(
            "evidence_pack_signature_invalid",
            "The finalized Evidence Pack signature is unreadable",
        ) from exc
    if observed_signature != pack.signature:
        raise Union3ResearchLoopError(
            "evidence_pack_signature_invalid",
            "The Evidence Pack signature does not match its durable ledger",
        )


async def finalize_union3_audit(
    db: AsyncSession,
    *,
    audit_id: uuid.UUID,
) -> tuple[ClaimAudit, EvidencePack]:
    """Run the only deterministic ``SUPPORTED`` transition and emit Pack v2."""

    _require_union3_feature_gates()

    audit_owner_id = await db.scalar(
        select(ClaimAudit.user_id).where(ClaimAudit.id == audit_id)
    )
    if audit_owner_id is None:
        raise Union3ResearchLoopError(
            "claim_audit_not_found", "Claim Audit not found", status_code=404
        )
    try:
        await lock_active_storage_owner(db, audit_owner_id)
    except PermissionError as exc:
        raise Union3ResearchLoopError(
            "claim_audit_owner_inactive",
            "The Claim Audit owner is no longer active",
        ) from exc
    audit = (
        await db.execute(
            select(ClaimAudit).where(ClaimAudit.id == audit_id).with_for_update()
        )
    ).scalar_one_or_none()
    if audit is None or audit.user_id != audit_owner_id:
        raise Union3ResearchLoopError(
            "claim_audit_not_found", "Claim Audit not found", status_code=404
        )
    existing_pack = (
        await db.execute(
            select(EvidencePack)
            .where(EvidencePack.audit_id == audit.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    supported_state = (
        audit.lifecycle_status == "COMPLETED"
        and audit.scientific_verdict == "SUPPORTED"
        and audit.review_status == "APPROVED"
        and audit.machine_support_eligible is True
        and audit.reproduction_ready is True
        and audit.publication_ready is False
        and bool(audit.independent_verification_job_id)
    )
    if audit.scientific_verdict == "SUPPORTED" and not supported_state:
        raise Union3ResearchLoopError(
            "supported_state_invalid",
            "The SUPPORTED Audit state is internally inconsistent",
        )
    if not supported_state and (
        audit.lifecycle_status != "COMPLETED"
        or audit.scientific_verdict != "WITHHELD"
        or audit.machine_support_eligible is not True
        or audit.reproduction_ready is not False
        or audit.publication_ready is not False
        or not audit.independent_verification_job_id
    ):
        raise Union3ResearchLoopError(
            "machine_gate_not_satisfied",
            "The independent machine gate has not made this Audit eligible",
        )
    if (
        existing_pack is not None
        and not supported_state
        and existing_pack.status != "UPLOADING"
    ):
        raise Union3ResearchLoopError(
            "evidence_pack_state_conflict",
            "An Evidence Pack row exists before the deterministic terminal state",
        )

    (
        source,
        extraction,
        candidate,
        primary_job,
        attempt,
        verifier_job,
        recomputed_verification,
        artifact_evidence,
        reviews,
    ) = await _load_finalization_bindings(db, audit=audit)
    if supported_state:
        if existing_pack is None:
            raise Union3ResearchLoopError(
                "evidence_pack_binding_invalid",
                "The SUPPORTED Audit has no finalized Evidence Pack",
            )
        await _validate_finalized_pack(
            audit=audit,
            pack=existing_pack,
            source=source,
            extraction=extraction,
            candidate=candidate,
            primary_job=primary_job,
            attempt=attempt,
            verifier_job=verifier_job,
            artifact_evidence=artifact_evidence,
            reviews=reviews,
        )
        return audit, existing_pack

    private_key = str(settings.evidence_v2_signing_private_key or "").strip()
    key_id = str(settings.evidence_v2_signing_key_id or "").strip()
    if not private_key or not key_id:
        raise Union3ResearchLoopError(
            "evidence_v2_signing_key_unavailable",
            "Evidence Pack v2 signing is unavailable",
            status_code=503,
        )
    finalized_at = _stable_finalized_at(audit, reviews[0])
    manifest_fields, files = _pack_files(
        audit=audit,
        source=source,
        extraction=extraction,
        candidate=candidate,
        primary_job=primary_job,
        attempt=attempt,
        verifier_job=verifier_job,
        verification=recomputed_verification,
        artifact_evidence=artifact_evidence,
        reviews=reviews,
        finalized_at=finalized_at,
    )
    pack_bytes, manifest, pack_hash = await asyncio.to_thread(
        build_evidence_pack_v2,
        manifest_fields=manifest_fields,
        files=files,
        signing_private_key=private_key,
        key_id=key_id,
    )
    verification = await asyncio.to_thread(
        verify_evidence_pack_v2,
        pack_bytes,
        trusted_keyring=_evidence_v2_trusted_keyring(),
    )
    if not verification.valid or verification.manifest != manifest:
        raise Union3ResearchLoopError(
            "evidence_v2_self_verification_failed",
            "Evidence Pack v2 did not pass its own public-key verification",
        )
    manifest_hash = "sha256:" + _sha256_bytes(jcs_canonicalize(manifest))
    artifact_ref = f"evidence-packs/v2/{audit.user_id}/{audit.id}/{pack_hash.removeprefix('sha256:')}.zip"
    with zipfile.ZipFile(BytesIO(pack_bytes), "r") as archive:
        signature = archive.read("manifest.sig").decode("ascii")

    if existing_pack is None:
        existing_pack = EvidencePack(
            id=uuid.uuid4(),
            audit_id=audit.id,
            user_id=audit.user_id,
            status="UPLOADING",
            schema_version=2,
            artifact_ref=artifact_ref,
            manifest=manifest,
            manifest_hash=manifest_hash,
            pack_hash=pack_hash,
            signature=signature,
            key_id=key_id,
            signature_algorithm=EVIDENCE_PACK_V2_SIGNATURE_ALGORITHM,
            public_key_fingerprint=str(manifest["public_key_fingerprint"]),
            upload_lease_id=uuid.uuid4().hex,
            upload_started_at=_utcnow(),
            finalized_at=None,
        )
        db.add(existing_pack)
    elif (
        existing_pack.status != "UPLOADING"
        or existing_pack.user_id != audit.user_id
        or existing_pack.schema_version != 2
        or existing_pack.artifact_ref != artifact_ref
        or existing_pack.manifest != manifest
        or existing_pack.manifest_hash != manifest_hash
        or existing_pack.pack_hash != pack_hash
        or existing_pack.signature != signature
        or existing_pack.key_id != key_id
        or existing_pack.signature_algorithm != EVIDENCE_PACK_V2_SIGNATURE_ALGORITHM
        or existing_pack.public_key_fingerprint
        != str(manifest["public_key_fingerprint"])
        or existing_pack.finalized_at is not None
    ):
        raise Union3ResearchLoopError(
            "evidence_pack_staging_conflict",
            "The durable Evidence Pack upload receipt does not match this finalization",
        )

    # The UPLOADING receipt must be durable before object storage. A crash or
    # lost commit acknowledgement can therefore be retried/reconciled without
    # leaving an undiscoverable authoritative ZIP.
    await db.commit()
    try:
        await lock_active_storage_owner(db, audit.user_id)
    except PermissionError as exc:
        raise Union3ResearchLoopError(
            "claim_audit_owner_inactive",
            "The Claim Audit owner became inactive before Pack upload",
        ) from exc
    try:
        uploaded_ref = await asyncio.to_thread(upload_fits, artifact_ref, pack_bytes)
    except Exception as exc:
        raise Union3ResearchLoopError(
            "evidence_v2_upload_failed",
            "Evidence Pack v2 could not be stored",
            status_code=503,
        ) from exc
    if uploaded_ref != artifact_ref:
        if str(uploaded_ref).startswith(
            f"evidence-packs/v2/{audit.user_id}/{audit.id}/"
        ):
            await asyncio.to_thread(delete_fits_all_versions, uploaded_ref)
        raise Union3ResearchLoopError(
            "evidence_v2_storage_binding_invalid",
            "Evidence Pack storage returned an unexpected object key",
        )

    # Reacquire the owner/Audit/Pack locks after the external upload and check
    # every scientific binding again. Account deletion and review changes win
    # safely while the object remains discoverable through the staged receipt.
    try:
        await lock_active_storage_owner(db, audit.user_id)
    except PermissionError as exc:
        raise Union3ResearchLoopError(
            "claim_audit_owner_inactive",
            "The Claim Audit owner became inactive before Pack finalization",
        ) from exc
    locked_audit = await db.scalar(
        select(ClaimAudit).where(ClaimAudit.id == audit.id).with_for_update()
    )
    locked_pack = await db.scalar(
        select(EvidencePack)
        .where(EvidencePack.id == existing_pack.id)
        .with_for_update()
    )
    if (
        locked_audit is None
        or locked_pack is None
        or locked_pack.status != "UPLOADING"
        or locked_pack.artifact_ref != artifact_ref
        or locked_audit.lifecycle_status != "COMPLETED"
        or locked_audit.scientific_verdict != "WITHHELD"
        or locked_audit.machine_support_eligible is not True
        or locked_audit.reproduction_ready is not False
        or locked_audit.publication_ready is not False
    ):
        raise Union3ResearchLoopError(
            "evidence_pack_terminal_race",
            "The Audit or staged Pack changed before the terminal transition",
        )
    (
        current_source,
        current_extraction,
        current_candidate,
        current_primary_job,
        current_attempt,
        current_verifier_job,
        current_verification,
        current_artifact_evidence,
        current_reviews,
    ) = await _load_finalization_bindings(db, audit=locked_audit)
    current_manifest_fields, current_files = _pack_files(
        audit=locked_audit,
        source=current_source,
        extraction=current_extraction,
        candidate=current_candidate,
        primary_job=current_primary_job,
        attempt=current_attempt,
        verifier_job=current_verifier_job,
        verification=current_verification,
        artifact_evidence=current_artifact_evidence,
        reviews=current_reviews,
        finalized_at=finalized_at,
    )
    current_pack_bytes, current_manifest, current_pack_hash = await asyncio.to_thread(
        build_evidence_pack_v2,
        manifest_fields=current_manifest_fields,
        files=current_files,
        signing_private_key=private_key,
        key_id=key_id,
    )
    if (
        current_pack_hash != pack_hash
        or current_manifest != manifest
        or current_pack_bytes != pack_bytes
    ):
        raise Union3ResearchLoopError(
            "evidence_pack_binding_changed",
            "A scientific binding changed while the Evidence Pack was uploading",
        )

    locked_pack.status = "FINALIZED"
    locked_pack.finalized_at = finalized_at
    locked_audit.scientific_verdict = "SUPPORTED"
    locked_audit.review_status = "APPROVED"
    locked_audit.machine_support_eligible = True
    locked_audit.reproduction_ready = True
    locked_audit.publication_ready = False
    locked_audit.progress = 1.0
    locked_audit.progress_stage = "evidence_pack_finalized"
    locked_audit.fact_check_report = {
        "finalizer": "deterministic_union3_reproduction_v1",
        "scientific_verdict": "SUPPORTED",
        "reproduction_ready": True,
        "publication_ready": False,
        "pack_hash": pack_hash,
        "manifest_hash": manifest_hash,
    }
    graph = dict(locked_audit.evidence_graph or {})
    graph.update(
        {
            "review_id": str(current_reviews[0].id),
            "evidence_pack_id": str(locked_pack.id),
            "evidence_pack_hash": pack_hash,
        }
    )
    locked_audit.evidence_graph = graph
    try:
        await db.commit()
    except Exception:
        # COMMIT may have succeeded while its acknowledgement was lost. Never
        # delete an object that could now be referenced by a FINALIZED Pack.
        # A retry re-reads the durable Audit/Pack pair and validates the bytes.
        await db.rollback()
        raise
    await db.refresh(locked_audit)
    await db.refresh(locked_pack)
    return locked_audit, locked_pack


__all__ = [
    "Union3ResearchLoopError",
    "create_union3_reproduction_audit",
    "create_union3_reproduction_revision",
    "finalize_union3_audit",
    "verify_union3_attempt",
]
