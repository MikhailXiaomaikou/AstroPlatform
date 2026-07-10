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
from langdetect import DetectorFactory, LangDetectException, detect_langs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.schemas import ChatSession
from app.services.claim_validator import (
    literature_prior_violations,
    methodology_consistency_violations,
    provenance_citation_violations,
    reply_contains_cjk,
    scientific_conclusion_scope_violations,
    unclassified_literature_violations,
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


PAPER_VALIDATION_SCHEMA_VERSION = 5
PAPER_VALIDATION_ARTIFACT_TYPE = "paper_draft"
EVIDENCE_SNAPSHOT_SCHEMA_VERSION = 2
UNVERIFIED_DRAFT_WATERMARK = "UNVERIFIED DRAFT — NOT FOR PUBLICATION"
PUBLICATION_LANGUAGE_ATTESTATION_KEY = "_publication_language_attestation"
PUBLICATION_LANGUAGE_ATTESTATION_SCHEMA_VERSION = 1
PUBLICATION_LANGUAGE_ATTESTATION_ARTIFACT_TYPE = "publication_language_attestation"
PUBLICATION_LANGUAGE_DETECTOR = "langdetect-1.0.9"
PUBLICATION_LANGUAGE_MIN_WORDS = 4
PUBLICATION_LANGUAGE_MIN_PROBABILITY = 0.95

# langdetect otherwise seeds its character n-gram sampler from process entropy.
# Publication decisions must be bit-for-bit deterministic across workers.
DetectorFactory.seed = 0

_UNSUPPORTED_PUBLICATION_SCRIPT_RE = re.compile(
    "["
    "\\u0400-\\u052f"  # Cyrillic
    "\\u0590-\\u05ff"  # Hebrew
    "\\u0600-\\u06ff"  # Arabic
    "\\u0900-\\u097f"  # Devanagari
    "\\u0e00-\\u0e7f"  # Thai
    "]"
)
_NON_ENGLISH_PUBLICATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Spanish",
        re.compile(
            r"\b(?:los\s+datos|la\s+energ[ií]a\s+oscura|"
            r"constante\s+cosmol[oó]gica|nuestros?\s+resultados?|"
            r"favorecen|evoluciona)\b",
            re.I,
        ),
    ),
    (
        "French",
        re.compile(
            r"\b(?:les\s+donn[eé]es|[eé]nergie\s+sombre|"
            r"constante\s+cosmologique|nos\s+r[eé]sultats|"
            r"favorisent|[eé]volue)\b",
            re.I,
        ),
    ),
    (
        "German",
        re.compile(
            r"\b(?:die\s+daten|dunkle\s+energie|kosmologische\s+konstante|"
            r"unsere\s+ergebnisse|bevorzug(?:en|t)|entwickelt\s+sich)\b",
            re.I,
        ),
    ),
)

_PAPER_REQUIRES_EXPLICIT_PUBLICATION_READY = {
    "generate_proposal",
    "run_pipeline",
    "run_python",
}


def _paper_without_language_attestation(paper_json: Mapping[str, Any]) -> dict[str, Any]:
    clean = copy.deepcopy(dict(paper_json))
    clean.pop(PUBLICATION_LANGUAGE_ATTESTATION_KEY, None)
    return clean


def _paper_claim_text(paper_json: Mapping[str, Any]) -> str:
    """Return claim-bearing string leaves without hidden attestation metadata."""

    values: list[str] = []

    def _walk(value: Any, key: str = "") -> None:
        if key == PUBLICATION_LANGUAGE_ATTESTATION_KEY:
            return
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                _walk(child, str(child_key))
        elif isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for child in value:
                _walk(child, key)
        elif isinstance(value, str):
            values.append(value)

    _walk(paper_json)
    return "\n".join(values)


def _unsupported_publication_language(
    text: str, *, require_confident_english: bool = True
) -> str | None:
    """Reject any claim-bearing segment not confidently detected as English."""

    if reply_contains_cjk(text, threshold=1):
        return "CJK/Japanese/Korean or full-width claim text"
    if _UNSUPPORTED_PUBLICATION_SCRIPT_RE.search(text):
        return "a non-Latin script outside the English validator scope"
    for language, pattern in _NON_ENGLISH_PUBLICATION_PATTERNS:
        if pattern.search(text):
            return f"{language} claim text"
    for segment in re.split(r"[\n;]+|(?<=[.!?])\s+", text):
        words = _natural_language_words(segment)
        if len(words) < PUBLICATION_LANGUAGE_MIN_WORDS:
            continue
        detector_text = " ".join(words)
        try:
            ranked = detect_langs(detector_text)
        except LangDetectException:
            return "claim text whose language could not be determined"
        if not ranked:
            return "claim text whose language could not be determined"
        top = ranked[0]
        if top.lang != "en" or (
            require_confident_english
            and float(top.prob) < PUBLICATION_LANGUAGE_MIN_PROBABILITY
        ):
            return (
                f"claim text detected as {top.lang} with probability "
                f"{float(top.prob):.3f} (English >= "
                f"{PUBLICATION_LANGUAGE_MIN_PROBABILITY:.2f} is required)"
            )
    return None


def _has_positive_english_segment(text: str) -> bool:
    for segment in re.split(r"[\n;]+|(?<=[.!?])\s+", text):
        words = _natural_language_words(segment)
        if len(words) < PUBLICATION_LANGUAGE_MIN_WORDS:
            continue
        try:
            ranked = detect_langs(" ".join(words))
        except LangDetectException:
            continue
        if (
            ranked
            and ranked[0].lang == "en"
            and float(ranked[0].prob) >= PUBLICATION_LANGUAGE_MIN_PROBABILITY
        ):
            return True
    return False


def _natural_language_words(segment: str) -> list[str]:
    """Exclude formula/unit fragments before applying the language threshold."""

    without_math = re.sub(r"\$.*?\$|\\\([^)]*\\\)|\\\[[^]]*\\\]", " ", segment)
    without_math = re.sub(r"\b[A-Za-z_]+\d+[A-Za-z_]*\b", " ", without_math)
    tokens = re.findall(
        r"(?<![\\\w])[^\W\d_]+(?:[-'][^\W\d_]+)*(?![\w])",
        without_math,
        re.UNICODE,
    )
    scientific_units = {
        "aa",
        "arcsec",
        "day",
        "days",
        "deg",
        "erg",
        "gyr",
        "jy",
        "kelvin",
        "km",
        "kpc",
        "mas",
        "mpc",
        "myr",
        "pc",
        "sec",
        "sigma",
        "snr",
    }
    return [
        token
        for token in tokens
        if token.lower() not in scientific_units
        and not (token.isupper() and len(token) <= 4)
    ]


def _language_claim_hash(paper_json: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _paper_without_language_attestation(paper_json),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _language_attestation_signature(payload: Mapping[str, Any], *, key: str) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    digest = hmac.new(
        key.encode("utf-8"),
        b"standard-astro/publication-language/v1\0" + encoded,
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def build_publication_language_attestation(
    paper_json: Mapping[str, Any],
    *,
    source: str = "human_review",
    reviewer_id: str | None = None,
) -> dict[str, Any] | None:
    """Attest exact human-reviewed English-scope paper content.

    Automatic server generation is accepted only when every natural-language
    segment of four or more words is deterministically detected as English at
    probability >=0.95. Short formula/parameter fragments are neutral. A fully
    neutral draft requires explicit human review.
    """

    if source not in {"server_generated", "human_review"}:
        raise ValueError("Unsupported publication language attestation source")
    if source == "human_review" and not str(reviewer_id or "").strip():
        raise ValueError("Human review requires a reviewer id")
    claim_text = _paper_claim_text(paper_json)
    if _unsupported_publication_language(
        claim_text, require_confident_english=source == "server_generated"
    ):
        return None
    positive_english = _has_positive_english_segment(claim_text)
    if source == "server_generated" and not positive_english:
        return None
    payload: dict[str, Any] = {
        "schema_version": PUBLICATION_LANGUAGE_ATTESTATION_SCHEMA_VERSION,
        "artifact_type": PUBLICATION_LANGUAGE_ATTESTATION_ARTIFACT_TYPE,
        "language": "en",
        "source": source,
        "reviewer_id": str(reviewer_id or "standard-astro-paper-generator"),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "detector": PUBLICATION_LANGUAGE_DETECTOR,
        "detector_seed": 0,
        "minimum_words_per_segment": PUBLICATION_LANGUAGE_MIN_WORDS,
        "minimum_english_probability": PUBLICATION_LANGUAGE_MIN_PROBABILITY,
        "positive_english_segment": positive_english,
        "claim_text_sha256": _language_claim_hash(paper_json),
        "key_id": settings.evidence_signing_key_id,
    }
    payload["signature"] = _language_attestation_signature(
        payload, key=settings.evidence_signing_key
    )
    return payload


def verify_publication_language_attestation(paper_json: Mapping[str, Any]) -> bool:
    attestation = paper_json.get(PUBLICATION_LANGUAGE_ATTESTATION_KEY)
    if not isinstance(attestation, dict):
        return False
    try:
        attested_probability = float(
            attestation.get("minimum_english_probability") or 0.0
        )
    except (TypeError, ValueError):
        return False
    if (
        attestation.get("schema_version")
        != PUBLICATION_LANGUAGE_ATTESTATION_SCHEMA_VERSION
        or attestation.get("artifact_type")
        != PUBLICATION_LANGUAGE_ATTESTATION_ARTIFACT_TYPE
        or attestation.get("language") != "en"
        or attestation.get("source") not in {"server_generated", "human_review"}
        or not str(attestation.get("reviewer_id") or "").strip()
        or attestation.get("detector") != PUBLICATION_LANGUAGE_DETECTOR
        or attestation.get("detector_seed") != 0
        or attestation.get("minimum_words_per_segment")
        != PUBLICATION_LANGUAGE_MIN_WORDS
        or attested_probability != PUBLICATION_LANGUAGE_MIN_PROBABILITY
        or attestation.get("claim_text_sha256") != _language_claim_hash(paper_json)
        or _unsupported_publication_language(
            _paper_claim_text(paper_json),
            require_confident_english=(
                attestation.get("source") == "server_generated"
            ),
        )
        is not None
        or (
            attestation.get("source") == "server_generated"
            and not _has_positive_english_segment(_paper_claim_text(paper_json))
        )
    ):
        return False
    supplied = attestation.get("signature")
    key_id = attestation.get("key_id")
    if not isinstance(supplied, str) or not isinstance(key_id, str):
        return False
    unsigned = {key: value for key, value in attestation.items() if key != "signature"}
    verification_keys: list[str] = []
    if key_id == settings.evidence_signing_key_id:
        verification_keys.append(settings.evidence_signing_key)
    retired = settings.evidence_verification_keyring.get(key_id)
    if retired:
        verification_keys.append(retired)
    return any(
        hmac.compare_digest(
            supplied, _language_attestation_signature(unsigned, key=key)
        )
        for key in dict.fromkeys(verification_keys)
    )


def _publication_numeric_evidence(
    tool_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only signed results eligible to support manuscript numbers.

    Chat may discuss an explicitly labelled exploratory result, but the paper
    boundary is stricter.  Any tool that declares itself preliminary, partial,
    exploratory, or non-publication is removed from the numeric evidence
    universe.  Free-form Python and rough proposal estimates require an
    explicit positive publication attestation rather than inheriting trust from
    successful execution alone.
    """
    eligible: list[dict[str, Any]] = []
    for record in tool_results:
        if not isinstance(record, dict):
            continue
        tool_name = str(record.get("tool") or "")
        result = record.get("result")
        if not isinstance(result, dict):
            continue
        statuses = {
            str(result.get(key) or "").strip().upper()
            for key in ("analysis_status", "__tool_status__", "status")
            if result.get(key) is not None
        }
        scope = str(result.get("claim_scope") or "").strip().lower()
        if (
            result.get("__do_not_claim__") is True
            or result.get("publication_ready") is False
            or bool(statuses & {"PARTIAL", "EXPLORATORY", "BLOCKED", "FAILED"})
            or any(token in scope for token in ("preliminary", "exploratory"))
            or (
                tool_name in _PAPER_REQUIRES_EXPLICIT_PUBLICATION_READY
                and result.get("publication_ready") is not True
            )
        ):
            continue
        eligible.append(record)
    return eligible


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
    publication_numeric_results = _publication_numeric_evidence(
        trusted_tool_results
    )
    draft_text = ""
    if paper_json is not None:
        # paper_json is the source of truth for API-authored drafts. Avoid also
        # appending its rendered LaTeX, which would duplicate every p-value and
        # bias the multiple-testing heuristic below.
        draft_text = json.dumps(
            _paper_without_language_attestation(paper_json),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
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
    validation_text = draft_text or "\n".join(artifacts.assistant_text)
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

    # The claim catalogue is English-only. CJK detection alone is not English
    # detection, so each natural-language segment is deterministically detected
    # and the result is HMAC-bound to exact paper JSON. Editing invalidates the
    # hash and fails closed until server generation or human review re-attests.
    language_text = (
        _paper_claim_text(paper_json)
        if isinstance(paper_json, Mapping)
        else validation_text
    )
    language_attestation = (
        paper_json.get(PUBLICATION_LANGUAGE_ATTESTATION_KEY)
        if isinstance(paper_json, Mapping)
        else None
    )
    human_reviewed_language = (
        isinstance(language_attestation, Mapping)
        and language_attestation.get("source") == "human_review"
    )
    language_problem = _unsupported_publication_language(
        language_text,
        require_confident_english=not human_reviewed_language,
    )
    language_attested = (
        verify_publication_language_attestation(paper_json)
        if isinstance(paper_json, Mapping)
        else latex_source is None
    )
    if language_problem is not None:
        checks.append(
            _build_check(
                "claim_language",
                "FAIL",
                "The draft is outside the supported English claim scope: "
                + language_problem
                + ".",
                "Translate the scientific claims to English and obtain a new content-bound server or human-review attestation.",
            )
        )
    elif not language_attested:
        checks.append(
            _build_check(
                "claim_language",
                "FAIL",
                "The editable/free-form draft has no valid server-signed English-scope attestation for its exact content.",
                "Regenerate the draft on the server or obtain a human language review attestation after the final edit.",
            )
        )
    else:
        checks.append(
            _build_check(
                "claim_language",
                "PASS",
                "Claim-bearing text is content-bound to the supported English-language publication policy.",
                "Any edit requires a fresh content-bound language attestation.",
            )
        )

    # Reuse the same numeric claim validator as chat. Any validator failure,
    # empty numeric universe for a quantitative claim, or unsupported number
    # fails closed at the publication boundary.
    try:
        numeric_validation = validate_claims(
            validation_text,
            publication_numeric_results,
            require_typed_scientific_match=True,
        )
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
                "are supported by publication-eligible signed tool results."
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
            validation_text,
            trusted_tool_results,
            strict=True,
        )
        narrative_violations = unsupported_literature_narrative_violations(
            validation_text,
            trusted_tool_results,
        )
        unclassified_violations = unclassified_literature_violations(
            validation_text,
            trusted_tool_results,
        )
        prior_violations = literature_prior_violations(
            validation_text,
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
        all_provenance_violations = [
            *citation_violations,
            *narrative_violations,
            *unclassified_violations,
        ]
        all_provenance_details = [
            *(f"{item.kind}: {item.match_text}" for item in all_provenance_violations),
            *(f"unsupported_literature_prior: {item.raw}" for item in prior_violations),
        ]
        if all_provenance_details:
            details = ", ".join(all_provenance_details[:8])
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

    try:
        method_violations = methodology_consistency_violations(
            validation_text, trusted_tool_results
        )
        conclusion_scope_violations = scientific_conclusion_scope_violations(
            validation_text, trusted_tool_results
        )
    except Exception as exc:
        checks.append(
            _build_check(
                "methodology_and_conclusion_scope",
                "FAIL",
                f"Scientific scope validation could not complete: {exc.__class__.__name__}.",
                "Restore the scope validator and rerun before publishing.",
            )
        )
    else:
        scope_violations = [*method_violations, *conclusion_scope_violations]
        if scope_violations:
            details = ", ".join(
                f"{item.kind}: {item.match_text}" for item in scope_violations[:8]
            )
            checks.append(
                _build_check(
                    "methodology_and_conclusion_scope",
                    "FAIL",
                    "Unsupported methodology or conclusion scope: " + details,
                    "Remove the claim or produce a signed, publication-ready result with calibrated model-comparison evidence.",
                )
            )
        else:
            checks.append(
                _build_check(
                    "methodology_and_conclusion_scope",
                    "PASS",
                    "Method and high-level scientific conclusions match the signed evidence scope.",
                    "Keep conclusion language within the validated claim scope.",
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
    has_signed_dataset_provenance = any(
        isinstance(record.get("result"), dict)
        and bool(
            record["result"].get("datasets_used")
            or record["result"].get("provenance")
            or record["result"].get("source_url")
        )
        for record in trusted_tool_results
        if isinstance(record, dict)
    )
    if (
        not artifacts.search_calls
        and not artifacts.adql_calls
        and not has_signed_dataset_provenance
    ):
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
