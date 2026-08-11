#!/usr/bin/env python3
"""Score the natural-phrasing (model-in-loop) Standard Astro v0.2 rerun.

Reuses the frozen six-dimension audit from ``score_standard_astro_v02``
unchanged, and adds the stratification layer preregistered in
``standard_astro_v02_natural_preregistered_tasks.json``: every
standard_astro sample is labeled by recorded ``llm_calls`` as
``pipeline`` (0 — the deterministic route answered alone) or
``model_in_loop`` (>0), and scores are reported per stratum. The two
strata are never merged into a single headline number; the blended
condition totals are emitted for cross-run comparability only.

This run is a measurement, not a release gate, so the parent's release
check battery is intentionally not evaluated here. The only pass/fail
endpoint is the preregistered hard-escape count of zero.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from score_standard_astro_v02 import (
    CONDITIONS,
    DIMENSIONS,
    MODELS,
    _audit_task,
    _critical_escape,
    _percentile,
    _read_samples,
    _read_tasks,
    _scalar_receipt,
    _validate_matrix,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASKS = (
    REPO_ROOT / "docs/research/standard_astro_v02_natural_preregistered_tasks.json"
)
DEFAULT_SAMPLES = (
    REPO_ROOT / ".local/standard-astro-v02-natural/natural_samples.jsonl"
)
DEFAULT_SCORES = REPO_ROOT / ".local/standard-astro-v02-natural/natural_scores.csv"
DEFAULT_SUMMARY = REPO_ROOT / ".local/standard-astro-v02-natural/natural_summary.json"

STRATA = ("direct", "standard_pipeline", "standard_model_in_loop")


def _stratum(row: dict[str, Any]) -> str:
    if row["condition"] == "direct":
        return "direct"
    return "standard_pipeline" if int(row["llm_calls"] or 0) == 0 else "standard_model_in_loop"


def _block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    score = sum(int(row["total"]) for row in rows)
    maximum = len(rows) * 12
    return {
        "samples": len(rows),
        "score": score,
        "maximum": maximum,
        "percentage": round(100 * score / maximum, 3) if maximum else None,
        "dimensions": {
            dimension: sum(int(row[dimension]) for row in rows)
            for dimension in DIMENSIONS
        },
    }


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
            "llm_calls": sample.get("llm_calls"),
            "routed_task_kind": validation.get("task_kind"),
            "response_disposition": validation.get("response_disposition"),
            "source_status": evidence_receipt.get("source_status")
            or receipt.get("source_status"),
            "critical_escape": _critical_escape(sample),
            **scores,
            "total": sum(scores.values()),
            "audit_flags": ";".join(flags),
        }
        row["stratum"] = _stratum(row)
        rows.append(row)

    args.scores.parent.mkdir(parents=True, exist_ok=True)
    with args.scores.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    standard_rows = [row for row in rows if row["condition"] == "standard_astro"]
    strata_rows = {
        stratum: [row for row in rows if row["stratum"] == stratum]
        for stratum in STRATA
    }

    expected_routes = {
        task_id: str(task["expected_task_kind"]) for task_id, task in tasks.items()
    }
    expected_dispositions = {
        task_id: str(task.get("expected_disposition") or "")
        for task_id, task in tasks.items()
    }
    route_correct = sum(
        row["routed_task_kind"] == expected_routes[str(row["task_id"])]
        for row in standard_rows
    )
    disposition_correct = sum(
        row["response_disposition"] == expected_dispositions[str(row["task_id"])]
        for row in standard_rows
    )
    escapes = [row for row in rows if row["critical_escape"]]

    per_model: dict[str, Any] = {}
    for model in MODELS:
        per_model[model] = {
            "direct": _block(
                [row for row in rows if row["model"] == model and row["condition"] == "direct"]
            ),
            "standard_pipeline": _block(
                [
                    row
                    for row in strata_rows["standard_pipeline"]
                    if row["model"] == model
                ]
            ),
            "standard_model_in_loop": _block(
                [
                    row
                    for row in strata_rows["standard_model_in_loop"]
                    if row["model"] == model
                ]
            ),
        }

    per_task: dict[str, Any] = {}
    for task_id in sorted(tasks):
        task_standard = [row for row in standard_rows if row["task_id"] == task_id]
        per_task[task_id] = {
            "direct": _block(
                [
                    row
                    for row in rows
                    if row["task_id"] == task_id and row["condition"] == "direct"
                ]
            ),
            "standard": _block(task_standard),
            "standard_strata_counts": {
                "pipeline": sum(
                    row["stratum"] == "standard_pipeline" for row in task_standard
                ),
                "model_in_loop": sum(
                    row["stratum"] == "standard_model_in_loop" for row in task_standard
                ),
            },
            "standard_dispositions": {
                disposition: sum(
                    (row["response_disposition"] or "") == disposition
                    for row in task_standard
                )
                for disposition in sorted(
                    {str(row["response_disposition"] or "") for row in task_standard}
                )
            },
            "expected_disposition": expected_dispositions[task_id],
        }

    llm_calls_standard = [int(row["llm_calls"] or 0) for row in standard_rows]
    summary = {
        "schema_version": 1,
        "audit_method": "pre_registered_deterministic_rule_audit",
        "evaluation_id": "standard-astro-v02-natural-model-in-loop",
        "tasks_file": args.tasks.name,
        "samples": len(rows),
        "transport_failures": sum(
            row["transport_status"] != "completed" for row in rows
        ),
        "conditions": {
            condition: _block(
                [row for row in rows if row["condition"] == condition]
            )
            for condition in CONDITIONS
        },
        "blended_condition_totals_note": (
            "Condition totals blend the deterministic-pipeline and "
            "model-in-loop strata and exist only for comparability with the "
            "parent run; per-stratum blocks below are the primary reading."
        ),
        "strata": {stratum: _block(strata_rows[stratum]) for stratum in STRATA},
        "stratification": {
            "standard_samples": len(standard_rows),
            "pipeline": len(strata_rows["standard_pipeline"]),
            "model_in_loop": len(strata_rows["standard_model_in_loop"]),
            "llm_calls_p50": _percentile([float(v) for v in llm_calls_standard], 0.50),
            "llm_calls_max": max(llm_calls_standard) if llm_calls_standard else None,
        },
        "routing": {
            "correct": route_correct,
            "total": len(standard_rows),
            "accuracy_percentage": round(100 * route_correct / len(standard_rows), 3)
            if standard_rows
            else None,
        },
        "disposition_match": {
            "correct": disposition_correct,
            "total": len(standard_rows),
            "percentage": round(
                100 * disposition_correct / len(standard_rows), 3
            )
            if standard_rows
            else None,
        },
        "hard_escapes": {
            "count": len(escapes),
            "sample_keys": [row["sample_key"] for row in escapes],
        },
        "preregistered_endpoints": {
            "hard_escape_count_zero": not escapes,
            "note": (
                "Six-dimension scores per condition and stratum are the other "
                "primary endpoint and are reported above; phrase-level checks "
                "inside dimensions remain advisory diagnostics, not hard gates."
            ),
        },
        "per_model": per_model,
        "per_task": per_task,
        "latency_seconds": {
            f"{stratum}_p{int(percentile * 100)}": _percentile(
                [float(row["duration_seconds"]) for row in strata_rows[stratum]],
                percentile,
            )
            for stratum in STRATA
            for percentile in (0.50, 0.95)
        },
        "no_blending_rule": (
            "standard_pipeline scores describe the deterministic route's "
            "self-consistency and must never be presented as model behavior."
        ),
        "release_checks_note": (
            "The parent evaluation's release-check battery is defined for "
            "spec-language prompts and is deliberately not applied to this "
            "measurement run."
        ),
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
