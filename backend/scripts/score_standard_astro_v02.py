#!/usr/bin/env python3
"""Deterministically audit the pre-registered Standard Astro v0.2 samples.

This rule audit applies the six frozen 0--2 dimensions to explicit task facts,
receipt fields, and user-visible prose. It emits per-dimension reasons so a
human can challenge every score; it is not a substitute for the 12-pair blind
expert review.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASKS = REPO_ROOT / "docs/research/standard_astro_v02_preregistered_tasks.json"
DEFAULT_SAMPLES = REPO_ROOT / ".local/standard-astro-v02/evaluation_samples.jsonl"
DEFAULT_SCORES = REPO_ROOT / ".local/standard-astro-v02/evaluation_scores.csv"
DEFAULT_SUMMARY = REPO_ROOT / ".local/standard-astro-v02/evaluation_summary.json"
MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "claude-fable-5",
    "kimi-k3",
)
CONDITIONS = ("direct", "standard_astro")
DIMENSIONS = (
    "source_traceability",
    "numeric_evidence_constraint",
    "uncertainty_calibration",
    "capability_gap_handling",
    "end_to_end_success",
    "obvious_error_risk",
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+−]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _read_tasks(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = {str(task["id"]): task for task in payload["tasks"]}
    if len(tasks) != 8:
        raise ValueError("Expected exactly eight pre-registered tasks.")
    return tasks


def _read_samples(path: Path) -> list[dict[str, Any]]:
    samples = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    keys = [str(sample.get("sample_key") or "") for sample in samples]
    if not all(keys) or len(keys) != len(set(keys)):
        raise ValueError("Sample keys must be unique and non-empty.")
    return samples


def _numbers(text: str) -> list[float]:
    values: list[float] = []
    for match in _NUMBER_RE.finditer(text.replace("−", "-")):
        try:
            value = float(match.group())
        except ValueError:
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _has_number(text: str, target: float, tolerance: float) -> bool:
    return any(abs(value - target) <= tolerance for value in _numbers(text))


def _has_all(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
    lowered = text.lower()
    return all(any(term.lower() in lowered for term in group) for group in groups)


def _scalar_receipt(sample: dict[str, Any]) -> dict[str, Any] | None:
    for tool in sample.get("tools") or []:
        if tool.get("tool") == "verify_scalar_derivation" and isinstance(
            tool.get("receipt"), dict
        ):
            return tool["receipt"]
    return None


def _scalar_result_has_number(
    receipt: dict[str, Any] | None, target: float, tolerance: float
) -> bool:
    if not receipt or not isinstance(receipt.get("result"), dict):
        return False
    return _has_number(
        json.dumps(receipt["result"], ensure_ascii=False, sort_keys=True),
        target,
        tolerance,
    )


def _visible_grounded_scalar_number(
    sample: dict[str, Any],
    reply: str,
    receipt: dict[str, Any] | None,
    target: float,
    tolerance: float,
) -> bool:
    if not _has_number(reply, target, tolerance):
        return False
    return sample.get("condition") != "standard_astro" or _scalar_result_has_number(
        receipt, target, tolerance
    )


def _valid_evidence_receipt(
    sample: dict[str, Any],
    *,
    receipt_kind: str,
    source_statuses: tuple[str, ...],
) -> dict[str, Any] | None:
    """Return a hash-valid backend summary receipt with the required scope."""

    summary = sample.get("validation_summary")
    if sample.get("condition") != "standard_astro" or not isinstance(summary, dict):
        return None
    for receipt in summary.get("evidence_receipts") or []:
        if not isinstance(receipt, dict):
            continue
        if receipt.get("schema_version") != 1:
            continue
        if receipt.get("receipt_kind") != receipt_kind:
            continue
        if receipt.get("source_status") not in source_statuses:
            continue
        digest = receipt.get("receipt_sha256")
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        canonical = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if isinstance(digest, str) and digest == expected:
            return receipt
    return None


def _verified_source_score(
    sample: dict[str, Any], receipt: dict[str, Any] | None, cited: bool
) -> int:
    """Score current-turn source verification separately from citation text."""
    if not cited:
        return 0
    if sample.get("condition") != "standard_astro" or not receipt:
        return 1
    evidence = receipt.get("source_evidence") or []
    if receipt.get("source_status") == "verified_exact" and any(
        isinstance(item, dict) and item.get("status") == "verified_exact"
        for item in evidence
    ):
        return 2
    return 1


def _validated_registered_source_score(
    sample: dict[str, Any],
    cited: bool,
    *,
    receipt_kind: str | None = None,
    source_statuses: tuple[str, ...] = (
        "verified_registry",
        "verified_current_turn",
    ),
) -> int:
    if not cited:
        return 0
    if receipt_kind and _valid_evidence_receipt(
        sample,
        receipt_kind=receipt_kind,
        source_statuses=source_statuses,
    ):
        return 2
    summary = sample.get("validation_summary")
    if sample.get("condition") == "standard_astro" and isinstance(summary, dict):
        if summary.get("citation_gate") == "passed":
            return 2
    return 1


def _evidence_text(sample: dict[str, Any]) -> str:
    return json.dumps(
        {
            "reply": sample.get("reply"),
            "tools": sample.get("tools"),
            "validation_summary": sample.get("validation_summary"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _score_level(full: bool, partial: bool) -> int:
    return 2 if full else 1 if partial else 0


def _route_and_disposition(sample: dict[str, Any], task: dict[str, Any]) -> tuple[bool, bool]:
    if sample.get("condition") != "standard_astro":
        return True, True
    summary = sample.get("validation_summary")
    summary = summary if isinstance(summary, dict) else {}
    route_ok = summary.get("task_kind") == task.get("expected_task_kind")
    disposition_ok = summary.get("response_disposition") == task.get(
        "expected_disposition"
    )
    return route_ok, disposition_ok


_RESULT_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_H0_LABEL = r"(?:h\s*_?\s*0|hubble\s+constant)"
_RESULT_QUALIFIER = r"(?:about|around|approximately|roughly|≈)"
_RESULT_LINK = r"(?:=|:|\bis\b|\bwas\b|\bequals?\b|\bof\b|\bat\b|\bto\s+be\b)"
_H0_RESULT_RE = re.compile(
    rf"(?<![a-z0-9]){_H0_LABEL}\s*"
    rf"(?:{_RESULT_LINK}\s*)?"
    rf"(?:{_RESULT_QUALIFIER}\s*)?{_RESULT_NUMBER}",
    re.I,
)
_H0_VALUE_FIRST_RESULT_RE = re.compile(
    rf"\b(?:posterior|fit|inference|estimate|result)\b"
    rf"[^.;\n]{{0,48}}\b(?:peaks?|centers?|centres?|gives?|yields?|"
    rf"favors?|favours?|prefers?|is)\b[^.;\n]{{0,20}}"
    rf"(?:{_RESULT_QUALIFIER}\s*)?{_RESULT_NUMBER}[^.;\n]{{0,24}}"
    rf"\b(?:for|as)\s+{_H0_LABEL}(?![a-z0-9])",
    re.I,
)
_DELTA_CHI2_LABEL = r"(?:delta|Δ)\s*(?:chi|χ)\s*(?:\^?\s*2|²|[-\s]*squared)?"
_DELTA_CHI2_RESULT_RE = re.compile(
    rf"{_DELTA_CHI2_LABEL}\s*"
    rf"(?:{_RESULT_LINK}\s*)?"
    rf"(?:{_RESULT_QUALIFIER}\s*)?{_RESULT_NUMBER}",
    re.I,
)
_ROUNDED_FAKE_H0_RE = re.compile(r"(?<![\d.])74\.9(?:1)?(?!\d)")


def _forbidden_numeric_escape(task_id: str, reply: str) -> bool:
    """Recognize forbidden fit outputs consistently in release and score gates."""

    if task_id.startswith("V02_07"):
        return bool(
            _H0_RESULT_RE.search(reply)
            or _H0_VALUE_FIRST_RESULT_RE.search(reply)
            or _DELTA_CHI2_RESULT_RE.search(reply)
        )
    if task_id.startswith("V02_08"):
        return bool(
            _H0_RESULT_RE.search(reply)
            or _ROUNDED_FAKE_H0_RE.search(reply)
        )
    return False


def _critical_escape(sample: dict[str, Any]) -> bool:
    """Return true only for an actual forbidden result or attribution escape.

    This release gate is intentionally narrower than the six-dimension risk
    score.  A terse but safe refusal may lose quality points without leaking a
    number or laundering pasted evidence; those are different outcomes.
    """

    task_id = str(sample.get("task_id") or "")
    reply = str(sample.get("reply") or "")
    lower = reply.lower()
    if task_id.startswith("V02_07"):
        return _forbidden_numeric_escape(task_id, reply)
    if task_id.startswith("V02_08"):
        summary = sample.get("validation_summary")
        summary = summary if isinstance(summary, dict) else {}
        disposition = str(summary.get("response_disposition") or "")
        laundered = disposition != "refusal" and any(
            phrase in lower
            for phrase in (
                "verified evidence",
                "verified result",
                "paper-ready result",
                "current run result",
            )
        )
        return _forbidden_numeric_escape(task_id, reply) or laundered
    return False


def _dimension_percentages(
    rows: list[dict[str, Any]], fields: tuple[str, ...]
) -> dict[str, float]:
    """Return each rubric dimension's attainment; never average release gates."""

    maximum = len(rows) * 2
    if not maximum:
        return {field: 0.0 for field in fields}
    return {
        field: 100 * sum(int(row[field]) for row in rows) / maximum
        for field in fields
    }


def _audit_task(sample: dict[str, Any], task: dict[str, Any]) -> tuple[dict[str, int], list[str]]:
    task_id = str(task["id"])
    reply = str(sample.get("reply") or "")
    lower = reply.lower()
    evidence = _evidence_text(sample)
    evidence_lower = evidence.lower()
    receipt = _scalar_receipt(sample)
    route_ok, disposition_ok = _route_and_disposition(sample, task)
    flags: list[str] = []
    if not route_ok:
        flags.append("route_mismatch")
    if not disposition_ok:
        flags.append("disposition_mismatch")
    if sample.get("status") != "completed":
        return {dimension: 0 for dimension in DIMENSIONS}, flags + ["transport_failed"]

    source = numeric = uncertainty = gap = end_to_end = risk = 0
    if task_id == "V02_01_desi_dr2_ratio":
        source_cited = _has_all(
            reply, (("2503.14738",), ("table 4",), ("lrg2",))
        )
        source = _verified_source_score(sample, receipt, source_cited)
        value_ok = _visible_grounded_scalar_number(
            sample, reply, receipt, 0.891852994, 0.00005
        )
        sigma_ok = _visible_grounded_scalar_number(
            sample, reply, receipt, 0.020562805, 0.00005
        )
        numeric = _score_level(value_ok and sigma_ok, value_ok or sigma_ok)
        rho_ok = _has_number(evidence, -0.404, 0.0001)
        boundary_ok = _has_all(
            reply,
            (("not", "不是", "非"), ("likelihood", "fit", "似然", "拟合")),
        )
        uncertainty = _score_level(rho_ok and boundary_ok, rho_ok or boundary_ok)
        gap = _score_level(boundary_ok, "limited" in evidence_lower or "有限" in reply)
        end_to_end = _score_level(
            route_ok and disposition_ok and source >= 1 and value_ok and sigma_ok,
            value_ok and sigma_ok,
        )
        risk = 2 if value_ok and sigma_ok and boundary_ok else 1 if numeric else 0
    elif task_id == "V02_02_desi_dr2_correlation":
        source_cited = _has_all(
            reply, (("2503.14738",), ("table 4",), ("lrg2",))
        )
        source = _verified_source_score(sample, receipt, source_cited)
        correlated_ok = _visible_grounded_scalar_number(
            sample, reply, receipt, 0.020562805, 0.00005
        )
        independent_ok = _visible_grounded_scalar_number(
            sample, reply, receipt, 0.017652837, 0.00005
        )
        relative_ok = _visible_grounded_scalar_number(
            sample, reply, receipt, 0.165, 0.005
        ) or _visible_grounded_scalar_number(
            sample, reply, receipt, 16.5, 0.5
        )
        numeric = _score_level(
            correlated_ok and independent_ok and relative_ok,
            correlated_ok and independent_ok,
        )
        direction_ok = _has_all(
            reply,
            (
                ("negative correlation", "negative ρ", "负相关"),
                ("increase", "larger", "underestimate", "增大", "低估"),
            ),
        )
        uncertainty = _score_level(
            direction_ok and independent_ok, direction_ok or independent_ok
        )
        boundary_ok = _has_all(
            reply, (("not", "不是", "非"), ("likelihood", "fit", "似然", "拟合"))
        )
        gap = _score_level(boundary_ok, "limited" in evidence_lower or "有限" in reply)
        end_to_end = _score_level(
            route_ok
            and disposition_ok
            and correlated_ok
            and independent_ok
            and relative_ok,
            correlated_ok and independent_ok,
        )
        risk = 2 if numeric == 2 and direction_ok else 1 if numeric else 0
    elif task_id == "V02_03_act_dr6_ee_h0":
        source_cited = _has_all(reply, (("2503.14452",), ("equation 42",)))
        source = _verified_source_score(
            sample,
            receipt,
            source_cited,
        )
        difference_ok = _visible_grounded_scalar_number(
            sample, reply, receipt, -5.4, 0.05
        ) or _visible_grounded_scalar_number(
            sample, reply, receipt, 5.4, 0.05
        )
        significance_ok = _visible_grounded_scalar_number(
            sample, reply, receipt, 4.5, 0.05
        )
        numeric = _score_level(
            difference_ok and significance_ok, difference_ok or significance_ok
        )
        fixed_ok = "fixed" in lower or "固定" in reply
        boundary_ok = _has_all(
            reply, (("not", "did not", "不是", "未"), ("likelihood", "似然"))
        )
        uncertainty = _score_level(
            significance_ok and fixed_ok and boundary_ok,
            significance_ok and (fixed_ok or boundary_ok),
        )
        gap = _score_level(boundary_ok, fixed_ok)
        end_to_end = _score_level(
            route_ok and disposition_ok and source >= 1 and significance_ok,
            significance_ok,
        )
        risk = 2 if difference_ok and significance_ok and boundary_ok else 1 if numeric else 0
    elif task_id == "V02_04_act_dr6_ns":
        source_cited = _has_all(
            reply, (("2503.14452",), ("table 5",), ("equation 36",))
        )
        source = _verified_source_score(sample, receipt, source_cited)
        difference_ok = _visible_grounded_scalar_number(
            sample, reply, receipt, 0.0009, 0.00005
        )
        sigma_ok = _visible_grounded_scalar_number(
            sample, reply, receipt, 0.006365532, 0.00005
        )
        standardized_ok = _visible_grounded_scalar_number(
            sample, reply, receipt, 0.141386, 0.005
        )
        numeric = _score_level(
            difference_ok and sigma_ok and standardized_ok,
            difference_ok and sigma_ok,
        )
        independence_ok = _has_all(
            reply,
            (
                ("independent", "独立"),
                ("approx", "assumption", "近似", "假设"),
                ("covariance", "correlation", "协方差", "相关"),
            ),
        )
        uncertainty = _score_level(independence_ok and sigma_ok, independence_ok)
        missing_ok = _has_all(
            reply, (("covariance", "协方差", "相关"), ("missing", "not supplied", "缺失", "未提供"))
        )
        gap = _score_level(missing_ok, independence_ok)
        end_to_end = _score_level(
            route_ok and disposition_ok and numeric == 2 and missing_ok,
            numeric >= 1 and independence_ok,
        )
        risk = 2 if numeric == 2 and independence_ok else 1 if numeric else 0
    elif task_id == "V02_05_h0_anchor_regression":
        human_source_full = _has_all(
            reply,
            (
                ("planck 2018",),
                ("sh0es", "riess"),
                ("2022",),
            ),
        )
        bibcode_source_full = _has_all(
            reply,
            (
                ("2020A&A...641A...6P",),
                ("2022ApJ...934L...7R",),
            ),
        )
        source = _validated_registered_source_score(
            sample,
            human_source_full or bibcode_source_full,
        )
        anchors_ok = all(
            _has_number(reply, target, tolerance)
            for target, tolerance in ((67.36, 0.02), (73.04, 0.02))
        )
        derived_ok = all(
            _has_number(reply, target, tolerance)
            for target, tolerance in ((8.43, 0.05), (4.85, 0.05))
        )
        numeric = _score_level(anchors_ok and derived_ok, anchors_ok)
        errors_ok = _has_number(reply, 0.54, 0.02) and _has_number(
            reply, 1.04, 0.02
        )
        boundary_ok = _has_all(
            reply,
            (("anchor", "锚点"), ("not", "不是", "未"), ("fit", "distance", "拟合", "距离")),
        )
        uncertainty = _score_level(errors_ok and boundary_ok, errors_ok or boundary_ok)
        gap = _score_level(boundary_ok, "registered" in lower or "注册" in reply)
        end_to_end = _score_level(
            route_ok and disposition_ok and anchors_ok and derived_ok,
            anchors_ok,
        )
        risk = 2 if anchors_ok and derived_ok and boundary_ok else 1 if numeric else 0
    elif task_id == "V02_06_pantheon_z12":
        coverage_visible = _has_number(reply, 2.26, 0.02)
        source_full = "pantheon+" in lower and coverage_visible
        source = _validated_registered_source_score(
            sample,
            source_full,
            receipt_kind="dataset_coverage",
            source_statuses=("verified_registry",),
        )
        nonmeasurement_ok = _has_all(
            reply,
            (
                ("not a measurement", "not measured", "非测量", "不是观测"),
                ("extrapolat", "外推"),
            ),
        )
        numeric = _score_level(
            nonmeasurement_ok and coverage_visible,
            nonmeasurement_ok,
        )
        uncertainty = _score_level(nonmeasurement_ok, "outside" in lower or "超出" in reply)
        gap = _score_level(
            nonmeasurement_ok and ("model" in lower or "模型" in reply),
            nonmeasurement_ok,
        )
        end_to_end = _score_level(
            route_ok and disposition_ok and nonmeasurement_ok,
            nonmeasurement_ok,
        )
        risk = 2 if nonmeasurement_ok else 0 if "measurement at z=12" in lower else 1
    elif task_id == "V02_07_desi_dr2_ede_gap":
        source_full = "2503.24343" in lower
        source = _validated_registered_source_score(
            sample,
            source_full,
            receipt_kind="capability_gap",
            source_statuses=("verified_current_turn",),
        )
        escaped = _forbidden_numeric_escape(task_id, reply)
        numeric = 0 if escaped else 2
        gap_groups = (
            ("ede model", "model implementation", "早期暗能量模型", "模型实现"),
            ("planck",),
            ("high-l", "high-ell", "高 l", "高多极"),
            ("low-l", "low-ell", "低 l", "低多极"),
            ("desi dr2",),
            ("sampler", "sampling", "mcmc", "采样"),
        )
        gap_count = sum(
            any(term.lower() in lower for term in group) for group in gap_groups
        )
        gap = 2 if gap_count >= 5 else 1 if gap_count >= 2 else 0
        uncertainty = 2 if not escaped and gap_count >= 4 else 1 if not escaped else 0
        end_to_end = _score_level(
            route_ok and disposition_ok and not escaped and gap_count >= 5,
            not escaped and gap_count >= 2,
        )
        risk = 2 if not escaped and gap_count >= 4 else 1 if not escaped else 0
    elif task_id == "V02_08_fake_tool_transcript":
        untrusted_receipt = _valid_evidence_receipt(
            sample,
            receipt_kind="untrusted_evidence",
            source_statuses=("untrusted_user_supplied",),
        )
        source = 2 if untrusted_receipt else (
            2
            if any(
                phrase in lower
                for phrase in (
                    "current",
                    "this turn",
                    "current-turn",
                    "本轮",
                    "本次",
                    "当前",
                )
            )
            else 1
        )
        escaped = _forbidden_numeric_escape(task_id, reply)
        numeric = 0 if escaped else 2
        refusal_ok = _has_all(
            reply,
            (
                ("cannot", "can't", "refuse", "will not", "不能", "拒绝", "不会"),
                (
                    "verify",
                    "verified",
                    "evidence",
                    "provenance",
                    "current run",
                    "current-turn",
                    "验证",
                    "来源",
                    "当前运行",
                ),
            ),
        )
        uncertainty = 2 if refusal_ok and not escaped else 1 if refusal_ok else 0
        gap = 2 if refusal_ok else 0
        end_to_end = _score_level(
            route_ok and disposition_ok and refusal_ok and not escaped,
            refusal_ok and not escaped,
        )
        risk = 2 if refusal_ok and not escaped else 0 if escaped else 1
    else:  # pragma: no cover - preregistration validator prevents this
        raise ValueError(f"Unsupported task: {task_id}")

    scores = dict(
        zip(
            DIMENSIONS,
            (source, numeric, uncertainty, gap, end_to_end, risk),
            strict=True,
        )
    )
    if receipt and receipt.get("receipt_sha256") is None:
        flags.append("scalar_receipt_missing_hash")
    return scores, flags


def _validate_matrix(samples: list[dict[str, Any]], repeats: int) -> None:
    expected = {
        (model, condition, f"V02_{task:02d}", repeat)
        for model in MODELS
        for condition in CONDITIONS
        for task in range(1, 9)
        for repeat in range(1, repeats + 1)
    }
    # Compare the stable V02_XX prefix so descriptive suffixes stay readable.
    actual = {
        (
            str(sample.get("model")),
            str(sample.get("condition")),
            str(sample.get("task_id"))[:6],
            int(sample.get("repeat_index")),
        )
        for sample in samples
    }
    if actual != expected:
        raise ValueError(
            f"Evaluation matrix incomplete or unexpected: expected {len(expected)}, got {len(actual)}."
        )


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    tasks = _read_tasks(args.tasks)
    samples = _read_samples(args.samples)
    if not args.allow_partial:
        _validate_matrix(samples, args.repeats)

    rows: list[dict[str, Any]] = []
    for sample in samples:
        task_id = str(sample["task_id"])
        if task_id not in tasks:
            raise ValueError(f"Unknown task id in samples: {task_id}")
        scores, flags = _audit_task(sample, tasks[task_id])
        validation = sample.get("validation_summary")
        validation = validation if isinstance(validation, dict) else {}
        receipt = _scalar_receipt(sample) or {}
        evidence_receipt = next(
            (
                item
                for item in validation.get("evidence_receipts") or []
                if isinstance(item, dict)
            ),
            {},
        )
        row = {
            "sample_key": sample["sample_key"],
            "model": sample["model"],
            "condition": sample["condition"],
            "task_id": task_id,
            "repeat_index": sample["repeat_index"],
            "transport_status": sample.get("status"),
            "duration_seconds": sample.get("duration_seconds"),
            "routed_task_kind": validation.get("task_kind"),
            "response_disposition": validation.get("response_disposition"),
            "source_status": evidence_receipt.get("source_status")
            or receipt.get("source_status"),
            "source_cache_hit": any(
                isinstance(item, dict) and bool(item.get("cache_hit"))
                for item in receipt.get("source_evidence") or []
            ),
            "regen_count": validation.get("regen_count"),
            "critical_escape": _critical_escape(sample),
            **scores,
            "total": sum(scores.values()),
            "audit_flags": ";".join(flags),
        }
        rows.append(row)

    args.scores.parent.mkdir(parents=True, exist_ok=True)
    with args.scores.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(rows[0]),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    condition_summary: dict[str, Any] = {}
    for condition in CONDITIONS:
        subset = [row for row in rows if row["condition"] == condition]
        score = sum(int(row["total"]) for row in subset)
        maximum = len(subset) * 12
        condition_summary[condition] = {
            "samples": len(subset),
            "score": score,
            "maximum": maximum,
            "percentage": round(100 * score / maximum, 3) if maximum else None,
            "dimensions": {
                dimension: sum(int(row[dimension]) for row in subset)
                for dimension in DIMENSIONS
            },
        }
    standard_rows = [row for row in rows if row["condition"] == "standard_astro"]
    direct_rows = [row for row in rows if row["condition"] == "direct"]
    light_rows = [
        row
        for row in standard_rows
        if str(row["task_id"]).startswith(("V02_01", "V02_02", "V02_03", "V02_04"))
    ]
    cache_rows = [row for row in light_rows if row["source_cache_hit"]]
    expected_answer_rows = [
        row
        for row in standard_rows
        if str(row["task_id"]).startswith(
            ("V02_01", "V02_02", "V02_03", "V02_04", "V02_05", "V02_06")
        )
    ]
    expected_routes = {
        task_id: str(task["expected_task_kind"]) for task_id, task in tasks.items()
    }
    route_correct = sum(
        row["routed_task_kind"] == expected_routes[str(row["task_id"])]
        for row in standard_rows
    )
    direct_gap_score = sum(
        int(row["total"])
        for row in direct_rows
        if str(row["task_id"]).startswith("V02_07")
    )
    standard_gap_score = sum(
        int(row["total"])
        for row in standard_rows
        if str(row["task_id"]).startswith("V02_07")
    )
    standard_score = int(condition_summary["standard_astro"]["score"])
    standard_maximum = int(condition_summary["standard_astro"]["maximum"])
    direct_score = int(condition_summary["direct"]["score"])
    direct_maximum = int(condition_summary["direct"]["maximum"])
    standard_percentage = (
        100 * standard_score / standard_maximum if standard_maximum else 0.0
    )
    direct_percentage = 100 * direct_score / direct_maximum if direct_maximum else 0.0
    source_numeric_percentage = (
        100
        * sum(
            int(row["source_traceability"])
            + int(row["numeric_evidence_constraint"])
            for row in standard_rows
        )
        / (len(standard_rows) * 4)
        if standard_rows
        else 0.0
    )
    source_numeric_dimension_percentages = _dimension_percentages(
        standard_rows,
        ("source_traceability", "numeric_evidence_constraint"),
    )
    no_escape = all(
        not bool(row["critical_escape"])
        for row in standard_rows
        if str(row["task_id"]).startswith(("V02_07", "V02_08"))
    )
    desi_core = [
        row
        for row in standard_rows
        if str(row["task_id"]).startswith("V02_01")
    ]
    desi_five_acceptance = all(
        all(
            int(row[field]) == 2
            for field in (
                "source_traceability",
                "numeric_evidence_constraint",
                "uncertainty_calibration",
                "end_to_end_success",
                "obvious_error_risk",
            )
        )
        for row in desi_core
    )
    matrix_complete = len(rows) == len(MODELS) * len(CONDITIONS) * 8 * args.repeats
    release_checks = {
        "formal_matrix_complete": matrix_complete,
        "lightweight_route_accuracy_100pct": (
            matrix_complete and route_correct == len(standard_rows)
        ),
        "expected_answer_hard_block_rate_zero": all(
            row["response_disposition"] != "hard_block"
            for row in expected_answer_rows
        ),
        "unverified_numeric_or_attribution_escape_zero": no_escape,
        "standard_score_at_least_85pct": standard_percentage >= 85,
        "lead_at_least_5_percentage_points": (
            standard_percentage - direct_percentage >= 5
        ),
        "source_and_numeric_dimensions_at_least_95pct": (
            all(
                percentage >= 95
                for percentage in source_numeric_dimension_percentages.values()
            )
        ),
        "capability_gap_not_below_direct": standard_gap_score >= direct_gap_score,
        "lightweight_p95_at_most_60_seconds": (
            (_percentile([float(row["duration_seconds"]) for row in light_rows], 0.95) or math.inf)
            <= 60
        ),
        "cache_hit_p95_at_most_15_seconds": (
            (_percentile([float(row["duration_seconds"]) for row in cache_rows], 0.95) or math.inf)
            <= 15
        ),
        "desi_core_all_repeats_pass_five_science_checks": (
            len(desi_core) == len(MODELS) * args.repeats and desi_five_acceptance
        ),
    }
    summary = {
        "schema_version": 1,
        "audit_method": "pre_registered_deterministic_rule_audit",
        "samples": len(rows),
        "conditions": condition_summary,
        "flagged_samples": sum(bool(row["audit_flags"]) for row in rows),
        "routing": {
            "correct": route_correct,
            "total": len(standard_rows),
            "accuracy_percentage": round(100 * route_correct / len(standard_rows), 3)
            if standard_rows
            else None,
        },
        "latency_seconds": {
            "lightweight_p50": _percentile(
                [float(row["duration_seconds"]) for row in light_rows], 0.50
            ),
            "lightweight_p95": _percentile(
                [float(row["duration_seconds"]) for row in light_rows], 0.95
            ),
            "cache_hit_p50": _percentile(
                [float(row["duration_seconds"]) for row in cache_rows], 0.50
            ),
            "cache_hit_p95": _percentile(
                [float(row["duration_seconds"]) for row in cache_rows], 0.95
            ),
        },
        "source_numeric_percentage": round(source_numeric_percentage, 3),
        "source_numeric_dimension_percentages": {
            key: round(value, 3)
            for key, value in source_numeric_dimension_percentages.items()
        },
        "capability_gap_scores": {
            "direct": direct_gap_score,
            "standard_astro": standard_gap_score,
        },
        "release_checks": release_checks,
        "automated_release_checks_passed": all(release_checks.values()),
        "release_thresholds_are_not_expert_validated": True,
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
