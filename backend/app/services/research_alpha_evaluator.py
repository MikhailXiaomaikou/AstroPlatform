"""Hidden-answer evaluator for A-class research blind tests.

This module is deliberately separate from the Chat UI and Research Mode
execution path.  It is an offline grading helper: hidden paper answers are
allowed here, but they must never be sent back into the assistant prompt.

The evaluator distinguishes the current B-level target (correct route or
honest scope gap) from A-level agreement (correct data/method/model plus
tool-grounded result direction and numerical scale).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any


SEVERE_PROCESS_FLAGS = {
    "ui_process_error",
    "timeout",
    "raw_backend_error",
    "internal_marker_leak",
    "unsupported_numeric_risk",
    "possibly_unsupported_hidden_number",
    "silent_termination",
}

STRICT_A_REQUIRED_MATCH_CRITERIA = {
    "data_match",
    "method_match",
    "model_match",
    "direction_compatible",
    "numeric_compatible",
}

PENDING_MARKERS = {
    "",
    "pending",
    "pending_full_paper_read",
    "unknown",
    "tbd",
    "not_started",
    "not available",
    "n/a",
}


def evaluate_alpha_class(
    *,
    platform_record: Mapping[str, Any],
    hidden_record: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate one blind-test run against a hidden paper record.

    Parameters
    ----------
    platform_record:
        A local diagnostic record from a Chat UI run.  The function accepts the
        loose shape used by the current local runner: visible text fields,
        booleans such as ``matrixVisible`` / ``factCheckVisible``, flags, and
        optional structured result summaries.
    hidden_record:
        Either a wrapper containing ``paper_hidden_record`` or the inner hidden
        answer itself.  Strict A-level grading requires structured expectation
        fields; free-form abstracts or pending placeholders are intentionally
        insufficient.
    """

    hidden = _unwrap_hidden_record(hidden_record)
    text = _visible_text(platform_record)
    flags = _collect_flags(platform_record, text)
    hidden_status = _hidden_record_status(hidden)
    evidence_manifest = _alpha_evidence_manifest(platform_record)

    criteria = {
        "data_match": _terms_match(
            _manifest_terms_text(evidence_manifest, "datasets"),
            _expectation_terms(hidden, ("expected_datasets", "required_datasets", "datasets")),
        ),
        "method_match": _terms_match(
            _manifest_terms_text(evidence_manifest, "methods"),
            _expectation_terms(hidden, ("expected_methods", "required_methods", "methods")),
        ),
        "model_match": _terms_match(
            _manifest_terms_text(evidence_manifest, "models"),
            _expectation_terms(hidden, ("expected_models", "required_models", "model_family")),
        ),
        "execution_ready": _execution_ready(evidence_manifest),
        "diagnostics_ready": _diagnostics_ready(evidence_manifest),
        "direction_compatible": _direction_compatible(evidence_manifest, hidden),
        "numeric_compatible": _numeric_compatible(evidence_manifest, hidden),
        "evidence_complete": _evidence_complete(evidence_manifest),
    }
    why_not_a = _why_not_a(hidden_status, criteria, flags)

    if flags & SEVERE_PROCESS_FLAGS:
        grade = "E"
        comparison = "severe_failure_or_unsafe_output"
    elif not why_not_a:
        grade = "A"
        comparison = "paper_level_agreement_supported_by_current_turn_evidence"
    else:
        grade, comparison = _fallback_grade(platform_record, text, criteria)

    return {
        "grade": grade,
        "a_level_ready": grade == "A",
        "comparison_to_hidden_answer": comparison,
        "hidden_record_status": hidden_status,
        "criteria": criteria,
        "why_not_A": why_not_a,
        "flags": sorted(flags),
        "paper_arxiv_id": hidden.get("arxiv_id"),
        "paper_title": hidden.get("title"),
        "anomaly_type": hidden.get("anomaly_type"),
        "evidence_manifest_status": (
            "trusted" if _trusted_alpha_manifest(evidence_manifest) else "missing_or_untrusted"
        ),
    }


def summarize_alpha_evaluations(evaluations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate strict A-class evaluation records.

    The summary is intended for local blind-test reports.  It keeps the
    accounting blunt: A-level readiness is counted separately from B-or-better
    partial pass, and the most common ``why_not_A`` reasons are surfaced as the
    implementation queue.
    """

    items = [dict(item) for item in evaluations]
    grade_counts: dict[str, int] = {}
    hidden_status_counts: dict[str, int] = {}
    why_not_a_counts: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    for item in items:
        grade = str(item.get("grade") or "ungraded")
        grade_counts[grade] = grade_counts.get(grade, 0) + 1
        hidden_status = str(item.get("hidden_record_status") or "unknown")
        hidden_status_counts[hidden_status] = hidden_status_counts.get(hidden_status, 0) + 1
        for reason in _as_string_list(item.get("why_not_A")):
            why_not_a_counts[reason] = why_not_a_counts.get(reason, 0) + 1
        for flag in _as_string_list(item.get("flags")):
            flag_counts[flag] = flag_counts.get(flag, 0) + 1

    total = len(items)
    strict_a = grade_counts.get("A", 0)
    b_or_better = sum(grade_counts.get(grade, 0) for grade in ("A", "B"))
    return {
        "total": total,
        "strict_A_count": strict_a,
        "strict_A_rate": strict_a / total if total else 0.0,
        "B_or_better_count": b_or_better,
        "B_or_better_rate": b_or_better / total if total else 0.0,
        "grade_counts": grade_counts,
        "hidden_record_status_counts": hidden_status_counts,
        "top_why_not_A": _sorted_counts(why_not_a_counts),
        "flag_counts": flag_counts,
        "implementation_queue": _implementation_queue_from_reasons(why_not_a_counts, flag_counts),
    }


def _unwrap_hidden_record(hidden_record: Mapping[str, Any]) -> Mapping[str, Any]:
    inner = hidden_record.get("paper_hidden_record")
    return inner if isinstance(inner, Mapping) else hidden_record


def _visible_text(platform_record: Mapping[str, Any]) -> str:
    pieces = [
        platform_record.get("visible_text"),
        platform_record.get("text"),
        platform_record.get("final_answer"),
        platform_record.get("final_answer_excerpt"),
        platform_record.get("summary"),
    ]
    return "\n".join(str(piece) for piece in pieces if piece)


def _alpha_evidence_manifest(platform_record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the structured manifest used for strict-A grading.

    Visible prose, UI visibility flags, and loose ``publication_ready`` booleans
    are deliberately excluded: they can demonstrate a B-level route but cannot
    prove paper-level agreement.
    """

    for key in ("scientific_evidence_manifest", "alpha_evidence_manifest"):
        value = platform_record.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _trusted_alpha_manifest(manifest: Mapping[str, Any]) -> bool:
    from app.services.server_evidence import verify_scientific_attestation

    return bool(
        manifest.get("source") in {"server_attested", "human_attested"}
        and verify_scientific_attestation(
            dict(manifest),
            expected_type="research_alpha",
        )
    )


def _manifest_terms_text(manifest: Mapping[str, Any], key: str) -> str:
    value = manifest.get(key)
    values = value if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)) else [value]
    terms: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            terms.extend(str(v) for v in item.values() if v is not None)
        elif item is not None:
            terms.append(str(item))
    return " ".join(terms)


def _collect_flags(platform_record: Mapping[str, Any], text: str) -> set[str]:
    flags = set(_as_string_list(platform_record.get("flags")))
    bool_flag_map = {
        "error": "ui_process_error",
        "timedOut": "timeout",
        "rawBackendError": "raw_backend_error",
        "internalLeak": "internal_marker_leak",
        "unsupportedNumericRisk": "unsupported_numeric_risk",
        "silentLikely": "silent_termination",
    }
    for field, flag in bool_flag_map.items():
        if platform_record.get(field):
            flags.add(flag)

    if re.search(r"<tools|<internal|failedtools=|suggestednext_step=", text, re.I):
        flags.add("internal_marker_leak")
    if re.search(r"selected ai backend failed|all configured ai backends failed", text, re.I):
        flags.add("raw_backend_error")
    numeric_claims_verified = platform_record.get("numericClaimsVerified") is True
    validation_summary = platform_record.get("validation_summary")
    if isinstance(validation_summary, Mapping):
        numeric_claims_verified = numeric_claims_verified or (
            validation_summary.get("numeric_gate") == "passed"
            and validation_summary.get("blocked") is False
        )
    if _unsupported_numeric_risk(text) and not numeric_claims_verified:
        flags.add("unsupported_numeric_risk")
    return flags


def _hidden_record_status(hidden: Mapping[str, Any]) -> str:
    explicit = str(hidden.get("full_paper_read_status") or "").strip().lower()
    key_numbers = str(hidden.get("hidden_key_numbers") or "").strip().lower()
    conclusion = str(hidden.get("paper_conclusion") or "").strip().lower()
    structured = any(
        hidden.get(key)
        for key in (
            "expected_datasets",
            "required_datasets",
            "expected_methods",
            "required_methods",
            "expected_models",
            "required_models",
            "expected_numbers",
            "expected_direction_terms",
            "acceptable_result_terms",
        )
    )
    if explicit in {"complete", "read", "verified"} and structured:
        return "complete"
    if key_numbers in PENDING_MARKERS or conclusion in PENDING_MARKERS or explicit == "pending":
        return "pending_full_paper_read"
    if structured:
        return "complete"
    return "insufficient"


def _expectation_terms(hidden: Mapping[str, Any], keys: Iterable[str]) -> list[str]:
    terms: list[str] = []
    for key in keys:
        value = hidden.get(key)
        if isinstance(value, str):
            terms.append(value)
        elif isinstance(value, Mapping):
            terms.extend(str(v) for v in value.values() if v)
        elif isinstance(value, Iterable):
            terms.extend(str(item) for item in value if item)
    return [_normalize_term(term) for term in terms if _normalize_term(term)]


def _terms_match(text: str, terms: list[str]) -> str:
    if not terms:
        return "not_specified"
    lower = _normalize_text(text)
    matched = [term for term in terms if term in lower]
    if len(matched) == len(terms):
        return "match"
    if matched:
        return "partial"
    return "missing"


def _execution_ready(manifest: Mapping[str, Any]) -> bool:
    publication_gate = manifest.get("publication_gate")
    adequacy = (
        publication_gate.get("model_adequacy")
        if isinstance(publication_gate, Mapping)
        else None
    )
    return bool(
        _trusted_alpha_manifest(manifest)
        and isinstance(publication_gate, Mapping)
        and publication_gate.get("eligible") is True
        and isinstance(adequacy, Mapping)
        and adequacy.get("eligible") is True
        and adequacy.get("signature_verified") is True
        and _is_sha256_id(adequacy.get("manifest_hash"))
    )


def _diagnostics_ready(manifest: Mapping[str, Any]) -> bool:
    if not _trusted_alpha_manifest(manifest):
        return False
    diagnostics = manifest.get("diagnostics")
    return bool(
        isinstance(diagnostics, Mapping)
        and str(diagnostics.get("status") or "").lower() in {"passed", "pass", "ok"}
        and _is_sha256_id(diagnostics.get("evidence_id"))
        and diagnostics.get("evidence_hash") == diagnostics.get("evidence_id")
        and diagnostics.get("evidence_id") in (manifest.get("evidence_ids") or [])
        and isinstance(diagnostics.get("metrics"), Mapping)
        and bool(diagnostics.get("metrics"))
    )


def _evidence_complete(manifest: Mapping[str, Any]) -> bool:
    if not _trusted_alpha_manifest(manifest):
        return False
    evidence_ids = manifest.get("evidence_ids")
    support_paths = manifest.get("claim_support_paths")
    valid_ids = {
        item
        for item in evidence_ids or []
        if isinstance(item, str) and _is_sha256_id(item)
    }
    return bool(
        isinstance(evidence_ids, list)
        and evidence_ids
        and len(valid_ids) == len(evidence_ids)
        and isinstance(support_paths, list)
        and support_paths
        and all(
            isinstance(item, Mapping)
            and isinstance(item.get("claim"), str)
            and bool(item.get("claim").strip())
            and item.get("evidence_id") in valid_ids
            and isinstance(item.get("result_path"), str)
            and bool(item.get("result_path").strip())
            for item in support_paths
        )
    )


def _direction_compatible(manifest: Mapping[str, Any], hidden: Mapping[str, Any]) -> str:
    expected_terms = _expectation_terms(hidden, ("expected_direction_terms", "acceptable_result_terms"))
    if not expected_terms:
        return "not_specified"
    return _terms_match(_manifest_terms_text(manifest, "result_direction_terms"), expected_terms)


def _numeric_compatible(manifest: Mapping[str, Any], hidden: Mapping[str, Any]) -> str:
    expected_numbers = hidden.get("expected_numbers")
    if not expected_numbers:
        return "not_specified"
    if not isinstance(expected_numbers, list):
        return "missing"

    observed_numbers = manifest.get("numbers")
    observed_by_name: dict[str, float] = {}
    if isinstance(observed_numbers, Mapping):
        iterable = [
            {"name": name, **(dict(value) if isinstance(value, Mapping) else {"value": value})}
            for name, value in observed_numbers.items()
        ]
    elif isinstance(observed_numbers, list):
        iterable = observed_numbers
    else:
        iterable = []
    for item in iterable:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or item.get("parameter") or "").strip().lower()
        value = _float_or_none(_first_present(item, ("value", "median", "mean")))
        if name and value is not None:
            observed_by_name[name] = value

    results: list[bool] = []
    for spec in expected_numbers:
        if not isinstance(spec, Mapping):
            continue
        name = str(spec.get("name") or spec.get("parameter") or "").strip()
        value = _float_or_none(_first_present(spec, ("value", "median", "expected")))
        if not name or value is None:
            continue
        observed = observed_by_name.get(name.lower())
        if observed is None:
            results.append(False)
            continue
        tolerance_abs = _float_or_none(spec.get("tolerance_abs"))
        tolerance_rel = _float_or_none(spec.get("tolerance_rel"))
        sigma = _float_or_none(spec.get("sigma") or spec.get("uncertainty"))
        if tolerance_abs is None:
            tolerance_abs = 2.0 * sigma if sigma is not None else abs(value) * (tolerance_rel or 0.1)
        results.append(abs(observed - value) <= max(tolerance_abs, 1e-12))

    if not results:
        return "not_specified"
    if all(results):
        return "match"
    if any(results):
        return "partial"
    return "contradicted"


def _why_not_a(hidden_status: str, criteria: Mapping[str, Any], flags: set[str]) -> list[str]:
    reasons: list[str] = []
    if hidden_status != "complete":
        reasons.append(f"hidden record is {hidden_status}; strict A requires structured full-paper expectations")
    for flag in sorted(flags & SEVERE_PROCESS_FLAGS):
        reasons.append(f"severe process/safety flag: {flag}")
    for key, value in criteria.items():
        if key in STRICT_A_REQUIRED_MATCH_CRITERIA and value == "not_specified":
            reasons.append(f"{key}=not_specified")
        if value in {False, "missing", "contradicted"}:
            reasons.append(f"{key}={value}")
        elif key in {
            "data_match",
            "method_match",
            "model_match",
            "direction_compatible",
            "numeric_compatible",
        } and value == "partial":
            reasons.append(f"{key}=partial")
    return reasons


def _fallback_grade(
    platform_record: Mapping[str, Any],
    text: str,
    criteria: Mapping[str, Any],
) -> tuple[str, str]:
    # B/C grading is intentionally less strict than A: visible routing and an
    # honest scope gap are useful even when no signed scientific manifest was
    # produced.  The manifest-only criteria above remain mandatory for A.
    method_route = (
        bool(platform_record.get("researchPlanVisible"))
        or bool(platform_record.get("matrixVisible"))
        or bool(re.search(r"research plan|matrix|likelihood|dataset|scope gap", text, re.I))
    )
    evidence = bool(platform_record.get("factCheckVisible")) or criteria["evidence_complete"] is True
    honest_gap = bool(platform_record.get("honestGap")) or bool(
        re.search(r"missing|not executable|not runnable|config-only|scope gap|not supported", text, re.I)
    )
    if method_route and evidence and (platform_record.get("matrixVisible") or honest_gap):
        return "B", "partial_consistent_or_precise_scope_gap"
    if method_route and honest_gap:
        return "C", "honest_failure_against_hidden_paper"
    if method_route:
        return "D", "method_route_incomplete"
    return "E", "severe_failure_or_requires_manual_review"


def _unsupported_numeric_risk(text: str) -> bool:
    # Textual words such as "evidence", "verified", or "citation" are model
    # prose, not machine evidence.  Any claim-shaped number is a risk unless
    # _collect_flags receives an explicit successful numeric-gate record.
    return bool(re.search(
        r"(H0|H₀|S8|σ8|Omega_m|Ωm|w0|wa|beta|β|f_EDE|nσ|sigma|tension|Δχ²)\s*[=:≈]\s*[-+]?\d",
        text,
        re.I,
    ))


def _find_number_near_name(text: str, name: str) -> float | None:
    escaped = re.escape(name)
    patterns = [
        rf"{escaped}\s*(?:=|≈|:)\s*([-+]?\d+(?:\.\d+)?)",
        rf"([-+]?\d+(?:\.\d+)?)\s*(?:for|as)\s+{escaped}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _float_or_none(match.group(1))
    return None


def _as_string_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value if item]
    return [str(value)]


def _sorted_counts(counts: Mapping[str, int]) -> list[dict[str, Any]]:
    return [
        {"reason": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _implementation_queue_from_reasons(
    why_not_a_counts: Mapping[str, int],
    flag_counts: Mapping[str, int],
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    reason_map = {
        "data_match": "complete hidden-answer dataset fields and improve dataset routing",
        "method_match": "complete hidden-answer method fields and implement missing likelihood/estimator routes",
        "model_match": "complete hidden-answer model-family fields and add model-family routing",
        "direction_compatible": "record expected result direction and compare final claims against it",
        "numeric_compatible": "record expected numerical constraints/tolerances and compare platform outputs",
        "execution_ready": "add or harden executable runners so the case is more than config-only",
        "diagnostics_ready": "surface ESS/R-hat/acceptance or equivalent diagnostics for the executed runner",
        "evidence_complete": "wire claims through evidence graph and fact-check reports",
    }
    for needle, action in reason_map.items():
        count = sum(count for reason, count in why_not_a_counts.items() if reason.startswith(f"{needle}="))
        if count:
            queue.append({"priority": len(queue) + 1, "action": action, "count": count})
    for flag, count in sorted(flag_counts.items(), key=lambda item: (-item[1], item[0])):
        if flag in SEVERE_PROCESS_FLAGS:
            queue.append({
                "priority": len(queue) + 1,
                "action": f"fix severe blind-test failure mode: {flag}",
                "count": count,
            })
    return queue


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_sha256_id(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        return False
    return True


def _first_present(mapping: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def _normalize_term(term: str) -> str:
    return _normalize_text(str(term).strip())
