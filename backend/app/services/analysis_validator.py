"""Rule-based scientific rigor checks for session-derived paper drafts."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import ChatSession
from app.services.claim_validator import (
    provenance_citation_violations,
    unsupported_literature_narrative_violations,
    validate_claims,
)
from app.services.event_collector import track_event
from app.services.paper_generator import _extract_actions
from app.services.server_evidence import (
    SERVER_EVIDENCE_SOURCE,
    verified_server_evidence_records,
    verify_server_evidence_record,
)


PAPER_VALIDATION_SCHEMA_VERSION = 4
PAPER_VALIDATION_ARTIFACT_TYPE = "paper_draft"
EVIDENCE_SNAPSHOT_SCHEMA_VERSION = 2
UNVERIFIED_DRAFT_WATERMARK = "UNVERIFIED DRAFT — NOT FOR PUBLICATION"


def _normalize_evidence_value(value: Any) -> Any:
    """Return a deterministic, JSON-safe copy of session evidence.

    Session messages normally already contain JSON-native values.  The explicit
    normalization makes the fingerprint stable across mapping key order and
    also handles the occasional numpy scalar, tuple, or non-finite float
    without relying on Python's non-standard ``NaN`` JSON representation.
    """

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        label = "nan" if math.isnan(value) else ("infinity" if value > 0 else "-infinity")
        return {"__normalized_type__": "float", "value": label}
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_evidence_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_evidence_value(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        return {
            "__normalized_type__": "bytes",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        }
    # JSON columns should not reach this branch, but deterministic tagging is
    # safer than a lossy or process-specific repr if an imported session does.
    return {
        "__normalized_type__": f"{type(value).__module__}.{type(value).__qualname__}",
        "value": str(value),
    }


def build_evidence_snapshot(
    *,
    session_id: str,
    owner_id: str,
    records: list[dict] | None,
) -> dict:
    """Freeze verified server tool records used for paper validation."""

    normalized_records = _normalize_evidence_value(records or [])
    if not isinstance(normalized_records, list):
        normalized_records = []
    return {
        "schema_version": EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        "source": SERVER_EVIDENCE_SOURCE,
        "session_id": str(session_id),
        "owner_id": str(owner_id),
        "records": normalized_records,
    }


def evidence_snapshot_fingerprint(snapshot: dict) -> str:
    """Hash a normalized evidence snapshot using canonical JSON bytes."""

    canonical = json.dumps(
        _normalize_evidence_value(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _validation_has_current_evidence(
    validation: dict | None,
    *,
    session_id: str,
    owner_id: str,
) -> bool:
    if not isinstance(validation, dict):
        return False
    snapshot = validation.get("evidence_snapshot")
    fingerprint = validation.get("evidence_fingerprint")
    if not isinstance(snapshot, dict) or not isinstance(fingerprint, str):
        return False
    if snapshot.get("schema_version") != EVIDENCE_SNAPSHOT_SCHEMA_VERSION:
        return False
    if snapshot.get("source") != SERVER_EVIDENCE_SOURCE:
        return False
    if str(snapshot.get("session_id") or "") != str(session_id):
        return False
    if str(snapshot.get("owner_id") or "") != str(owner_id):
        return False
    records = snapshot.get("records")
    if not isinstance(records, list) or not records:
        return False
    if not all(
        verify_server_evidence_record(
            record,
            session_id=session_id,
            owner_id=owner_id,
        )
        for record in records
    ):
        return False
    expected = evidence_snapshot_fingerprint(snapshot)
    return hmac.compare_digest(fingerprint, expected)


def analysis_validation_is_pass(validation: dict | None) -> bool:
    """Require an internally consistent PASS, not just a top-level label."""

    if not isinstance(validation, dict):
        return False
    checks = validation.get("checks")
    return (
        str(validation.get("overall_status") or "").upper() == "PASS"
        and isinstance(checks, list)
        and bool(checks)
        and all(
            isinstance(check, dict) and check.get("status") == "PASS"
            for check in checks
        )
    )


def paper_content_hash(
    *,
    paper_json: dict,
    latex_source: str,
    bibtex: str,
    journal_format: str,
) -> str:
    """Return a deterministic digest for every user-visible paper artifact.

    The validation record is stored in the existing JSON column, so this keeps
    old database rows readable while making new publication decisions bind to
    the exact JSON, LaTeX, bibliography, and renderer format being served.
    """

    payload = {
        "artifact_type": PAPER_VALIDATION_ARTIFACT_TYPE,
        "paper_json": paper_json or {},
        "latex_source": latex_source or "",
        "bibtex": bibtex or "",
        "journal_format": (journal_format or "aastex").strip().lower(),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def bind_paper_validation(
    validation: dict,
    *,
    session_id: str,
    owner_id: str,
    paper_json: dict,
    latex_source: str,
    bibtex: str,
    journal_format: str,
) -> dict:
    """Bind validator output to immutable paper and evidence snapshots."""

    bound = copy.deepcopy(validation) if isinstance(validation, dict) else {}
    overall_status = str(bound.get("overall_status") or "FAIL").upper()
    bound["overall_status"] = overall_status
    content_hash = paper_content_hash(
        paper_json=paper_json,
        latex_source=latex_source,
        bibtex=bibtex,
        journal_format=journal_format,
    )
    evidence_valid = _validation_has_current_evidence(
        bound,
        session_id=session_id,
        owner_id=owner_id,
    )
    evidence_fingerprint = str(bound.get("evidence_fingerprint") or "")
    binding_payload = {
        "schema_version": PAPER_VALIDATION_SCHEMA_VERSION,
        "artifact_type": PAPER_VALIDATION_ARTIFACT_TYPE,
        "session_id": str(session_id),
        "owner_id": str(owner_id),
        "content_hash": content_hash,
        "evidence_fingerprint": evidence_fingerprint,
    }
    binding_hash = evidence_snapshot_fingerprint(binding_payload)
    publishable = analysis_validation_is_pass(bound) and evidence_valid
    bound.update(
        {
            "schema_version": PAPER_VALIDATION_SCHEMA_VERSION,
            "artifact_type": PAPER_VALIDATION_ARTIFACT_TYPE,
            "session_id": str(session_id),
            "owner_id": str(owner_id),
            "content_hash": content_hash,
            "binding_hash": binding_hash,
            "evidence_binding_valid": evidence_valid,
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "publishable": publishable,
            "publication_status": (
                "publication_ready" if publishable else "unverified_private_draft"
            ),
            "watermark": None if publishable else UNVERIFIED_DRAFT_WATERMARK,
        }
    )
    return bound


def paper_validation_is_current(
    validation: dict | None,
    *,
    session_id: str,
    owner_id: str,
    paper_json: dict,
    latex_source: str,
    bibtex: str,
    journal_format: str,
) -> bool:
    """Return whether a stored validation belongs to the current contents.

    Legacy records deliberately return ``False``: they remain editable and can
    be upgraded by publishing, but cannot retain public access without a fresh
    validation under this binding scheme.
    """

    if not isinstance(validation, dict):
        return False
    if validation.get("schema_version") != PAPER_VALIDATION_SCHEMA_VERSION:
        return False
    if validation.get("artifact_type") != PAPER_VALIDATION_ARTIFACT_TYPE:
        return False
    if str(validation.get("session_id") or "") != str(session_id):
        return False
    if str(validation.get("owner_id") or "") != str(owner_id):
        return False
    if validation.get("evidence_binding_valid") is not True:
        return False
    if not _validation_has_current_evidence(
        validation,
        session_id=session_id,
        owner_id=owner_id,
    ):
        return False
    stored_hash = validation.get("content_hash")
    if not isinstance(stored_hash, str):
        return False
    expected_hash = paper_content_hash(
        paper_json=paper_json,
        latex_source=latex_source,
        bibtex=bibtex,
        journal_format=journal_format,
    )
    if not hmac.compare_digest(stored_hash, expected_hash):
        return False

    stored_binding_hash = validation.get("binding_hash")
    evidence_fingerprint = validation.get("evidence_fingerprint")
    if not isinstance(stored_binding_hash, str) or not isinstance(
        evidence_fingerprint, str
    ):
        return False
    expected_binding_hash = evidence_snapshot_fingerprint(
        {
            "schema_version": PAPER_VALIDATION_SCHEMA_VERSION,
            "artifact_type": PAPER_VALIDATION_ARTIFACT_TYPE,
            "session_id": str(session_id),
            "owner_id": str(owner_id),
            "content_hash": expected_hash,
            "evidence_fingerprint": evidence_fingerprint,
        }
    )
    return hmac.compare_digest(stored_binding_hash, expected_binding_hash)


def paper_validation_is_publishable(
    validation: dict | None,
    *,
    session_id: str,
    owner_id: str,
    paper_json: dict,
    latex_source: str,
    bibtex: str,
    journal_format: str,
) -> bool:
    """Fail closed unless a current, explicitly PASS validation is present."""

    if not paper_validation_is_current(
        validation,
        session_id=session_id,
        owner_id=owner_id,
        paper_json=paper_json,
        latex_source=latex_source,
        bibtex=bibtex,
        journal_format=journal_format,
    ):
        return False
    assert isinstance(validation, dict)
    return (
        analysis_validation_is_pass(validation)
        and validation.get("publishable") is True
        and validation.get("publication_status") == "publication_ready"
        and not validation.get("watermark")
    )


def _build_check(name: str, status: str, details: str, recommendation: str) -> dict:
    return {
        "name": name,
        "status": status,
        "details": details,
        "recommendation": recommendation,
    }


@track_event("analysis.function_called")
async def validate_analysis(
    session_id: str,
    db: AsyncSession,
    *,
    owner_id: str,
    paper_json: dict | None = None,
    latex_source: str | None = None,
    bibtex: str | None = None,
) -> dict:
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == owner_id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    evidence_records = verified_server_evidence_records(
        session.audit_log,
        session_id=session.id,
        owner_id=session.user_id,
    )
    evidence_snapshot = build_evidence_snapshot(
        session_id=str(session.id),
        owner_id=str(session.user_id),
        records=evidence_records,
    )

    # Reconstruct the small SessionArtifacts adapter exclusively from signed
    # server records. Client-authored session.messages/actions never enter the
    # publication decision.
    trusted_messages: list[dict[str, Any]] = []
    trusted_tool_results: list[dict[str, Any]] = []
    search_tools = {"search", "search_objects", "search_literature"}
    query_tools = {
        "adql",
        "run_adql",
        "run_sdss_sql",
        "query_gaia_cluster",
        "query_high_velocity_stars",
    }
    for record in evidence_records:
        actions: list[dict[str, Any]] = []
        for tool_record in record.get("tool_results") or []:
            if not isinstance(tool_record, dict):
                continue
            tool_name = str(tool_record.get("tool") or "")
            if not tool_name:
                continue
            tool_input = (
                tool_record.get("input")
                if isinstance(tool_record.get("input"), dict)
                else {}
            )
            result_value = tool_record.get("result")
            trusted_tool_results.append(tool_record)
            action_name = (
                "search"
                if tool_name in search_tools
                else ("run_adql" if tool_name in query_tools else tool_name)
            )
            actions.append(
                {
                    "action": action_name,
                    "server_tool_name": tool_name,
                    "tool_input": tool_input,
                    **tool_input,
                    "tool_result": result_value,
                }
            )
        trusted_messages.append(
            {
                "role": "assistant",
                "content": str(record.get("assistant_reply") or ""),
                "actions": actions,
            }
        )
    artifacts = _extract_actions(trusted_messages)
    draft_text = ""
    if paper_json is not None:
        # paper_json is the source of truth for API-authored drafts. Avoid also
        # appending its rendered LaTeX, which would duplicate every p-value and
        # bias the multiple-testing heuristic below.
        draft_text = json.dumps(
            paper_json, ensure_ascii=False, sort_keys=True, default=str
        )
    elif latex_source:
        # Legacy/imported content can still be assessed before it is re-rendered.
        draft_text = latex_source
    # Once a draft exists it becomes the claim-bearing source of truth. The
    # generated draft often repeats the last assistant response verbatim, so
    # counting both would double p-values and create false multiple-testing
    # warnings. Bibliography titles are likewise not scientific claims; the
    # BibTeX bytes are integrity-bound by ``paper_content_hash`` instead.
    claim_text = [draft_text] if draft_text else artifacts.assistant_text
    combined_text = "\n".join(
        artifacts.user_prompts
        + claim_text
        + [str(action.get("query", "")) for action in artifacts.adql_calls]
        + [str(action.get("code", "")) for action in artifacts.python_calls]
    ).lower()

    checks: list[dict] = []

    # Server evidence integrity. A browser-created/imported transcript has no
    # signed execution record and therefore cannot become publication-ready.
    if not evidence_records or not trusted_tool_results:
        checks.append(
            _build_check(
                "server_evidence_integrity",
                "FAIL",
                "No owner-bound, server-signed tool execution evidence was found.",
                "Rerun the analysis tools from this saved session before publishing.",
            )
        )
    else:
        checks.append(
            _build_check(
                "server_evidence_integrity",
                "PASS",
                f"Verified {len(evidence_records)} signed server run record(s).",
                "Retain the signed evidence binding with the final artifact.",
            )
        )

    # Reuse the same numeric claim validator as chat. Any validator failure,
    # empty numeric universe for a quantitative claim, or unsupported number
    # fails closed at the publication boundary.
    try:
        numeric_validation = validate_claims(draft_text, trusted_tool_results)
    except Exception as exc:
        checks.append(
            _build_check(
                "numeric_claim_evidence",
                "FAIL",
                f"Numeric claim validation could not complete: {exc.__class__.__name__}.",
                "Rerun validation after restoring the claim validator; do not publish meanwhile.",
            )
        )
    else:
        if numeric_validation.ok:
            details = (
                f"All {len(numeric_validation.claims)} detected numeric claim(s) "
                "are supported by signed tool results."
                if numeric_validation.claims
                else "No unsupported numeric scientific claims were detected."
            )
            checks.append(
                _build_check(
                    "numeric_claim_evidence",
                    "PASS",
                    details,
                    "Keep reported values traceable to the signed tool results.",
                )
            )
        else:
            unsupported = [str(claim.raw) for claim in numeric_validation.uncited[:8]]
            checks.append(
                _build_check(
                    "numeric_claim_evidence",
                    "FAIL",
                    "Unsupported numeric claim(s): " + ", ".join(unsupported),
                    "Remove the unsupported values or rerun a tool that produces them.",
                )
            )

    try:
        citation_violations = provenance_citation_violations(
            draft_text,
            trusted_tool_results,
            strict=True,
        )
        narrative_violations = unsupported_literature_narrative_violations(
            draft_text,
            trusted_tool_results,
        )
    except Exception as exc:
        checks.append(
            _build_check(
                "citation_and_narrative_provenance",
                "FAIL",
                f"Citation provenance validation could not complete: {exc.__class__.__name__}.",
                "Restore the provenance validator and rerun before publishing.",
            )
        )
    else:
        all_provenance_violations = [*citation_violations, *narrative_violations]
        if all_provenance_violations:
            details = ", ".join(
                f"{item.kind}: {item.match_text}"
                for item in all_provenance_violations[:8]
            )
            checks.append(
                _build_check(
                    "citation_and_narrative_provenance",
                    "FAIL",
                    "Unsupported citation or literature narrative: " + details,
                    "Remove the assertion or support it with a signed literature/tool result.",
                )
            )
        else:
            checks.append(
                _build_check(
                    "citation_and_narrative_provenance",
                    "PASS",
                    "Citations and literature assertions are grounded in signed tool results.",
                    "Keep citations bound to the same server evidence snapshot.",
                )
            )
    # Unit consistency
    unit_status = "PASS"
    unit_details = "No obvious unit mismatches detected in the recorded session."
    unit_reco = "Document all unit conversions explicitly in the final manuscript."
    if ("arcsec" in combined_text and "degree" in combined_text and "convert" not in combined_text):
        unit_status = "WARN"
        unit_details = "Angular quantities mention both arcsec and degrees without an explicit conversion step."
        unit_reco = "State the conversion between angular units before combining measurements."
    elif ("jy" in combined_text and "erg/s/cm" in combined_text and "convert" not in combined_text):
        unit_status = "WARN"
        unit_details = "Flux-like quantities appear in different systems without a documented conversion."
        unit_reco = "Convert all fluxes to a common system before drawing comparisons."
    checks.append(_build_check("unit_consistency", unit_status, unit_details, unit_reco))

    # Statistical method audit
    stat_status = "PASS"
    stat_details = "No immediately suspicious statistical pattern was detected."
    stat_reco = "Report assumptions, effect sizes, and confidence intervals together."
    p_value_mentions = len(re.findall(r"p\s*[<=>]\s*0\.\d+", combined_text))
    if "pearson" in combined_text and not any(token in combined_text for token in ("shapiro", "kolmogorov", "spearman")):
        stat_status = "WARN"
        stat_details = "Pearson correlation appears without evidence of a normality check."
        stat_reco = "Add a Shapiro-Wilk or KS test, or justify Pearson over Spearman."
    elif p_value_mentions > 3 and not any(token in combined_text for token in ("bonferroni", "benjamini", "fdr", "bh correction")):
        stat_status = "WARN"
        stat_details = "Multiple p-values were reported without a multiple-testing correction."
        stat_reco = "Apply a Bonferroni or Benjamini-Hochberg correction."
    checks.append(_build_check("statistical_method_audit", stat_status, stat_details, stat_reco))

    # Conclusion-data consistency
    conclusion_status = "PASS"
    conclusion_details = "No unsupported conclusion pattern was detected."
    conclusion_reco = "Ensure every major claim is paired with the supporting statistic or measurement."
    if "significant" in combined_text and "p < 0.05" not in combined_text and "p-value" not in combined_text:
        conclusion_status = "WARN"
        conclusion_details = "The session uses significance language without explicitly reporting the supporting p-value."
        conclusion_reco = "Add the test statistic, p-value, and uncertainty when claiming significance."
    if re.search(r"\bs/?n\s*[<:=]\s*([0-2](\.\d+)?)", combined_text):
        conclusion_status = "FAIL"
        conclusion_details = "A detection claim appears alongside an S/N below 3."
        conclusion_reco = "Recast the statement as a tentative signal or gather deeper data."
    checks.append(_build_check("conclusion_data_consistency", conclusion_status, conclusion_details, conclusion_reco))

    # Completeness
    completeness_status = "PASS"
    completeness_details = "The session includes basic analysis context."
    completeness_reco = "Add systematic uncertainties, extinction handling, and literature comparison where relevant."
    if any(token in combined_text for token in ("bp_rp", "phot_g_mean_mag", "optical", "magnitude")) and "extinction" not in combined_text:
        completeness_status = "WARN"
        completeness_details = "Optical photometry appears without an explicit extinction correction discussion."
        completeness_reco = "Consider applying or discussing extinction corrections (e.g. CCM89 or F99)."
    elif not artifacts.bibcodes:
        completeness_status = "WARN"
        completeness_details = "No literature references were recorded in the session."
        completeness_reco = "Run a literature search and compare the findings to prior work."
    checks.append(_build_check("completeness", completeness_status, completeness_details, completeness_reco))

    # Provenance
    provenance_status = "PASS"
    provenance_details = "Queries and data sources were captured in the session history."
    provenance_reco = "Retain archive names, data release identifiers, and query strings in the appendix."
    if not artifacts.search_calls and not artifacts.adql_calls:
        provenance_status = "FAIL"
        provenance_details = "No recorded search or ADQL actions were found for this session."
        provenance_reco = "Run the analysis from a saved session that includes the underlying data acquisition steps."
    elif any("gaia_source" in str(action.get("query", "")).lower() for action in artifacts.adql_calls) and "gaiadr3" not in combined_text:
        provenance_status = "WARN"
        provenance_details = "Gaia queries were present, but the data release was not consistently obvious."
        provenance_reco = "Explicitly state the Gaia release (e.g. DR3) in the final draft."
    checks.append(_build_check("data_provenance", provenance_status, provenance_details, provenance_reco))

    fail_count = sum(1 for check in checks if check["status"] == "FAIL")
    warn_count = sum(1 for check in checks if check["status"] == "WARN")
    score = max(0.0, min(1.0, 1.0 - 0.25 * fail_count - 0.08 * warn_count))
    overall_status = "FAIL" if fail_count else ("WARN" if warn_count else "PASS")

    return {
        "overall_status": overall_status,
        "score": round(score, 2),
        "checks": checks,
        "evidence_snapshot": evidence_snapshot,
        "evidence_fingerprint": evidence_snapshot_fingerprint(evidence_snapshot),
    }
