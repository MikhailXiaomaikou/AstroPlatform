"""Hidden-answer evaluator for A-class research blind tests.

This module is deliberately separate from the Chat UI and Research Mode
execution path.  It is an offline grading helper: hidden paper answers are
allowed here, but they must never be sent back into the assistant prompt.

The evaluator distinguishes the current B-level target (correct route or
honest scope gap), ``A_READY`` agreement (an exact-run manifest that matches the
hidden target), and strict A (the same evidence after a separately attested
external review).
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
    "run_identity_match",
    "target_match",
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
    expected_run_id = _expected_run_id(platform_record)

    criteria = {
        "run_identity_match": _run_identity_match(
            evidence_manifest, expected_run_id=expected_run_id
        ),
        "target_match": _target_compatible(evidence_manifest, hidden),
        "blinding_protocol_compatible": _blinding_protocol_compatible(
            evidence_manifest, hidden
        ),
        "data_match": _structured_terms_match(
            _manifest_terms(evidence_manifest, "datasets"),
            _expectation_terms(hidden, ("expected_datasets", "required_datasets", "datasets")),
        ),
        "method_match": _structured_terms_match(
            _manifest_terms(evidence_manifest, "methods"),
            _expectation_terms(hidden, ("expected_methods", "required_methods", "methods")),
        ),
        "model_match": _structured_terms_match(
            _manifest_terms(evidence_manifest, "models"),
            _expectation_terms(hidden, ("expected_models", "required_models", "model_family")),
        ),
        "execution_ready": _execution_ready(
            evidence_manifest, expected_run_id=expected_run_id
        ),
        "diagnostics_ready": _diagnostics_ready(
            evidence_manifest, expected_run_id=expected_run_id
        ),
        "direction_compatible": _direction_compatible(evidence_manifest, hidden),
        "numeric_compatible": _numeric_compatible(evidence_manifest, hidden),
        "evidence_complete": _evidence_complete(
            evidence_manifest, expected_run_id=expected_run_id
        ),
    }
    why_not_a_ready = _why_not_a(hidden_status, criteria, flags)
    externally_reviewed = _external_review_complete(
        evidence_manifest, expected_run_id=expected_run_id
    )

    if flags & SEVERE_PROCESS_FLAGS:
        grade = "E"
        comparison = "severe_failure_or_unsafe_output"
    elif not why_not_a_ready and externally_reviewed:
        grade = "A"
        comparison = "paper_level_agreement_with_external_review"
    elif not why_not_a_ready:
        grade = "A_READY"
        comparison = "paper_level_agreement_pending_external_review"
    else:
        grade, comparison = _fallback_grade(platform_record, text, criteria)

    why_not_a = list(why_not_a_ready)
    if grade == "A_READY":
        why_not_a.append("external_review=pending")

    return {
        "grade": grade,
        "a_level_ready": grade in {"A", "A_READY"},
        "a_ready": grade in {"A", "A_READY"},
        "strict_a": grade == "A",
        "externally_reviewed": externally_reviewed,
        "comparison_to_hidden_answer": comparison,
        "hidden_record_status": hidden_status,
        "criteria": criteria,
        "why_not_A": why_not_a,
        "flags": sorted(flags),
        "paper_arxiv_id": hidden.get("arxiv_id"),
        "paper_title": hidden.get("title"),
        "anomaly_type": hidden.get("anomaly_type"),
        "evidence_manifest_status": (
            "trusted"
            if _trusted_alpha_manifest(
                evidence_manifest, expected_run_id=expected_run_id
            )
            else "missing_or_untrusted"
        ),
    }


def summarize_alpha_evaluations(evaluations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate A-ready and externally reviewed strict-A records.

    The summary is intended for local blind-test reports.  It keeps the
    accounting blunt: A-ready and externally reviewed A are counted separately
    from B-or-better partial pass, and the most common ``why_not_A`` reasons are
    surfaced as the implementation queue.
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
    strict_a = sum(
        1
        for item in items
        if item.get("grade") == "A" and item.get("externally_reviewed") is True
    )
    a_ready = strict_a + grade_counts.get("A_READY", 0)
    b_or_better = sum(
        grade_counts.get(grade, 0) for grade in ("A", "A_READY", "B")
    )
    return {
        "total": total,
        "strict_A_count": strict_a,
        "strict_A_rate": strict_a / total if total else 0.0,
        "A_ready_count": a_ready,
        "A_ready_rate": a_ready / total if total else 0.0,
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


def _trusted_alpha_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_run_id: str | None = None,
) -> bool:
    from app.services.research_alpha_manifest import (
        validate_research_alpha_manifest,
    )

    return bool(
        validate_research_alpha_manifest(
            manifest,
            expected_run_id=expected_run_id,
        )["valid"]
    )


def _expected_run_id(platform_record: Mapping[str, Any]) -> str | None:
    for key in ("run_id", "execution_run_id", "scientific_run_id"):
        value = platform_record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _manifest_terms(manifest: Mapping[str, Any], key: str) -> list[str]:
    value = manifest.get(key)
    values = value if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)) else [value]
    terms: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            terms.extend(str(v) for v in item.values() if v is not None)
        elif item is not None:
            terms.append(str(item))
    return [_normalize_term(term) for term in terms if _normalize_term(term)]


def _run_identity_match(
    manifest: Mapping[str, Any], *, expected_run_id: str | None
) -> str:
    if expected_run_id is None:
        return "missing"
    identity = manifest.get("run_identity")
    observed = identity.get("run_id") if isinstance(identity, Mapping) else None
    if observed is None:
        return "missing"
    return "match" if observed == expected_run_id else "contradicted"


def _target_compatible(
    manifest: Mapping[str, Any], hidden: Mapping[str, Any]
) -> str:
    expected = hidden.get("target_hash") or hidden.get("paper_target_hash")
    if not expected:
        return "not_specified"
    target = manifest.get("target")
    observed = target.get("hash") if isinstance(target, Mapping) else None
    if observed is None:
        return "missing"
    return "match" if observed == expected else "contradicted"


def _blinding_protocol_compatible(
    manifest: Mapping[str, Any], hidden: Mapping[str, Any]
) -> bool:
    protocol = manifest.get("protocol_status")
    if not isinstance(protocol, Mapping):
        return False
    analyst_blinding = protocol.get("analyst_blinding")
    if analyst_blinding not in {"achieved", "not_achieved"}:
        return False
    if protocol.get("target_preregistration") != "frozen" or protocol.get(
        "computation_answer_key_separation"
    ) != "enforced":
        return False
    required = hidden.get("analyst_blinding_required")
    if required is None:
        thresholds = hidden.get("acceptance_thresholds")
        required = (
            thresholds.get("analyst_blinding_required")
            if isinstance(thresholds, Mapping)
            else False
        )
    return required is not True or analyst_blinding == "achieved"


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


def _structured_terms_match(observed: list[str], terms: list[str]) -> str:
    if not terms:
        return "not_specified"
    observed_set = set(observed)
    matched = [term for term in terms if term in observed_set]
    if len(matched) == len(terms):
        return "match"
    if matched:
        return "partial"
    return "missing"


def _execution_ready(
    manifest: Mapping[str, Any], *, expected_run_id: str | None = None
) -> bool:
    from app.services.w0wa_exact_contract import (
        EXACT_PROFILE_ID,
        exact_environment_validated_for_formal_execution,
    )

    publication_gate = manifest.get("publication_gate")
    adequacy = (
        publication_gate.get("model_adequacy")
        if isinstance(publication_gate, Mapping)
        else None
    )
    return bool(
        _trusted_alpha_manifest(manifest, expected_run_id=expected_run_id)
        and manifest.get("profile_id") == EXACT_PROFILE_ID
        and exact_environment_validated_for_formal_execution()
        and manifest.get("readiness_status")
        in {"A_READY_PENDING_EXTERNAL_REVIEW", "A"}
        and isinstance(publication_gate, Mapping)
        and publication_gate.get("eligible") is True
        and isinstance(adequacy, Mapping)
        and adequacy.get("eligible") is True
        and adequacy.get("signature_verified") is True
        and _is_sha256_id(adequacy.get("manifest_hash"))
    )


def _diagnostics_ready(
    manifest: Mapping[str, Any], *, expected_run_id: str | None = None
) -> bool:
    if not _trusted_alpha_manifest(manifest, expected_run_id=expected_run_id):
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


def _evidence_complete(
    manifest: Mapping[str, Any], *, expected_run_id: str | None = None
) -> bool:
    if not _trusted_alpha_manifest(manifest, expected_run_id=expected_run_id):
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
    return _structured_terms_match(
        _manifest_terms(manifest, "result_direction_terms"), expected_terms
    )


def _numeric_compatible(manifest: Mapping[str, Any], hidden: Mapping[str, Any]) -> str:
    """Compare named centers and complete 68% interval statistics.

    A hidden expectation that declares an interval is matched component by
    component.  Omitting a bound/uncertainty, swapping a statistic, or pairing
    a bound with a different uncertainty can therefore never be promoted by a
    correct center alone.
    """

    expected_numbers = hidden.get("expected_numbers")
    if not expected_numbers:
        return "not_specified"
    if not isinstance(expected_numbers, list):
        return "missing"

    observed_numbers = manifest.get("numbers")
    observed_by_name: dict[str, Mapping[str, Any]] = {}
    if isinstance(observed_numbers, Mapping):
        iterable = [
            {
                "name": name,
                **(dict(value) if isinstance(value, Mapping) else {"value": value}),
            }
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
        if name:
            observed_by_name[name] = item

    component_results: list[bool | None] = []
    parameter_seen = False
    for spec in expected_numbers:
        if not isinstance(spec, Mapping):
            component_results.append(False)
            continue
        name = str(spec.get("name") or spec.get("parameter") or "").strip()
        expected = _expected_number_components(spec)
        if not name or expected is None:
            component_results.append(False)
            continue
        observed_raw = observed_by_name.get(name.lower())
        if observed_raw is None:
            component_results.extend(False for _ in expected)
            continue
        parameter_seen = True
        observed = _observed_number_components(observed_raw)
        if not _observed_interval_aligned(observed):
            component_results.extend(False for _ in expected)
            continue
        for component, expected_value in expected.items():
            observed_value = observed.get(component)
            if observed_value is None:
                component_results.append(None)
                continue
            tolerance = _numeric_component_tolerance(
                component=component,
                expected=expected,
                spec=spec,
                observed_value=observed_value,
            )
            component_results.append(
                abs(observed_value - expected_value) <= max(tolerance, 1e-12)
            )
        if {"lower_68", "upper_68"}.issubset(expected):
            expected_width = expected["upper_68"] - expected["lower_68"]
            observed_lower = observed.get("lower_68")
            observed_upper = observed.get("upper_68")
            if observed_lower is None or observed_upper is None:
                component_results.append(None)
            else:
                width_tolerance_rel = _float_or_none(
                    spec.get("interval_width_tolerance_rel")
                )
                if width_tolerance_rel is None:
                    width_tolerance_rel = 0.15
                component_results.append(
                    abs((observed_upper - observed_lower) - expected_width)
                    <= max(abs(expected_width) * width_tolerance_rel, 1e-12)
                    + 1e-12
                )

    if not component_results:
        return "not_specified"
    if all(result is True for result in component_results):
        return "match"
    if any(result is True for result in component_results) or (
        parameter_seen and any(result is None for result in component_results)
    ):
        return "partial"
    return "contradicted"


def _expected_number_components(spec: Mapping[str, Any]) -> dict[str, float] | None:
    center = _float_or_none(
        _first_present(spec, ("center", "value", "median", "expected"))
    )
    if center is None:
        return None
    lower = _float_or_none(_first_present(spec, ("lower_68", "lower", "q16")))
    upper = _float_or_none(_first_present(spec, ("upper_68", "upper", "q84")))
    minus = _float_or_none(
        _first_present(spec, ("uncertainty_minus", "error_minus", "minus"))
    )
    plus = _float_or_none(
        _first_present(spec, ("uncertainty_plus", "error_plus", "plus"))
    )
    sigma = _float_or_none(_first_present(spec, ("sigma", "uncertainty")))
    if sigma is not None:
        minus = sigma if minus is None else minus
        plus = sigma if plus is None else plus
    interval_declared = any(
        value is not None for value in (lower, upper, minus, plus, sigma)
    )
    if not interval_declared:
        return {"center": center}
    if minus is None and lower is not None:
        minus = center - lower
    if plus is None and upper is not None:
        plus = upper - center
    if lower is None and minus is not None:
        lower = center - minus
    if upper is None and plus is not None:
        upper = center + plus
    if None in {lower, upper, minus, plus}:
        return {"center": center, "lower_68": math.nan}
    expected = {
        "center": center,
        "lower_68": float(lower),
        "upper_68": float(upper),
        "uncertainty_minus": float(minus),
        "uncertainty_plus": float(plus),
    }
    return expected if _observed_interval_aligned(expected) else {
        "center": center,
        "lower_68": math.nan,
    }


def _observed_number_components(item: Mapping[str, Any]) -> dict[str, float | None]:
    return {
        "center": _float_or_none(
            _first_present(item, ("center", "value", "median", "mean"))
        ),
        "lower_68": _float_or_none(
            _first_present(item, ("lower_68", "lower", "q16"))
        ),
        "upper_68": _float_or_none(
            _first_present(item, ("upper_68", "upper", "q84"))
        ),
        "uncertainty_minus": _float_or_none(
            _first_present(item, ("uncertainty_minus", "error_minus", "minus"))
        ),
        "uncertainty_plus": _float_or_none(
            _first_present(item, ("uncertainty_plus", "error_plus", "plus"))
        ),
    }


def _observed_interval_aligned(values: Mapping[str, float | None]) -> bool:
    center = values.get("center")
    lower = values.get("lower_68")
    upper = values.get("upper_68")
    minus = values.get("uncertainty_minus")
    plus = values.get("uncertainty_plus")
    interval_values = (lower, upper, minus, plus)
    if all(value is None for value in interval_values):
        return center is not None
    if center is None or any(value is None for value in interval_values):
        return False
    assert lower is not None and upper is not None and minus is not None and plus is not None
    if not all(math.isfinite(value) for value in (center, lower, upper, minus, plus)):
        return False
    if not lower < center < upper or minus <= 0 or plus <= 0:
        return False
    scale = max(abs(center), abs(lower), abs(upper), minus, plus, 1.0)
    tolerance = max(1e-12, scale * 1e-10)
    return math.isclose(
        center - lower, minus, rel_tol=1e-10, abs_tol=tolerance
    ) and math.isclose(
        upper - center, plus, rel_tol=1e-10, abs_tol=tolerance
    )


def _numeric_component_tolerance(
    *,
    component: str,
    expected: Mapping[str, float],
    spec: Mapping[str, Any],
    observed_value: float,
) -> float:
    component_abs = _float_or_none(spec.get(f"{component}_tolerance_abs"))
    if component_abs is not None:
        return abs(component_abs)
    generic_abs = _float_or_none(spec.get("tolerance_abs"))
    if component == "center" and generic_abs is not None:
        return abs(generic_abs)
    minus = expected.get("uncertainty_minus")
    plus = expected.get("uncertainty_plus")
    if component == "center":
        center = expected["center"]
        directional_sigma = plus if observed_value >= center else minus
        reference_sigma = directional_sigma or 0.0
    else:
        reference_sigma = max(
            value for value in (minus, plus, 0.0) if value is not None
        )
    if component == "center" and reference_sigma > 0:
        tolerance_sigma = _float_or_none(spec.get("center_tolerance_sigma"))
        return reference_sigma * (0.30 if tolerance_sigma is None else tolerance_sigma)
    if component in {"lower_68", "upper_68"}:
        # Endpoints are derived from two independently preregistered gates:
        # center may move by 0.30 paper sigma and the corresponding side width
        # may change by 15%.  Requiring an endpoint itself to stay within only
        # 0.15 sigma contradicts those accepted components (a valid aligned
        # endpoint can move by their sum, 0.45 sigma for a symmetric interval).
        side_sigma = minus if component == "lower_68" else plus
        if side_sigma is not None and side_sigma > 0:
            center_tolerance_sigma = _float_or_none(
                spec.get("center_tolerance_sigma")
            )
            if center_tolerance_sigma is None:
                center_tolerance_sigma = 0.30
            side_tolerance_rel = _float_or_none(
                spec.get(f"{component}_tolerance_rel")
            )
            if side_tolerance_rel is None:
                side_tolerance_rel = _float_or_none(
                    spec.get("interval_tolerance_rel")
                )
            if side_tolerance_rel is None:
                side_tolerance_rel = 0.15
            return side_sigma * (
                abs(center_tolerance_sigma) + abs(side_tolerance_rel)
            )
    relative = _float_or_none(spec.get(f"{component}_tolerance_rel"))
    if relative is None:
        relative = _float_or_none(spec.get("interval_tolerance_rel"))
    if relative is None:
        relative = 0.15
    expected_value = expected.get(component, 0.0)
    return abs(expected_value) * relative


def _external_review_complete(
    manifest: Mapping[str, Any], *, expected_run_id: str | None = None
) -> bool:
    if not _trusted_alpha_manifest(manifest, expected_run_id=expected_run_id):
        return False
    from app.services.research_alpha_manifest import (
        research_alpha_external_review_complete,
    )

    return research_alpha_external_review_complete(manifest)


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
        "run_identity_match": "bind the platform run_id to the exact signed manifest",
        "target_match": "bind the hidden paper target hash to the exact run manifest",
        "blinding_protocol_compatible": (
            "do not issue A-ready when analyst blinding is a required gate but was not achieved"
        ),
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
