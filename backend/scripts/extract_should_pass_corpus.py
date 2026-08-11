#!/usr/bin/env python3
"""Extract the should-pass regression corpus from a natural-matrix run.

A "should-pass" sample is a standard_astro sample whose task preregisters
disposition ``full`` (the question is answerable from supplied inputs) but
whose actual disposition came back abstention/hard_block/refusal — i.e. the
anti-fabrication stack suppressed a legitimate answer. These samples are the
specificity (false-kill) side of the gate error budget; the blind suite's B
group is the sensitivity side. Rerun both whenever a gate changes.

The corpus intentionally stores the full prompt, reply and routing metadata
so a future regression harness can replay and re-judge them without the
original run directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASKS = (
    REPO_ROOT / "docs/research/standard_astro_v02_natural_preregistered_tasks.json"
)
DEFAULT_SAMPLES = REPO_ROOT / ".local/standard-astro-v02-natural/natural_samples.jsonl"
DEFAULT_OUT = REPO_ROOT / "docs/research/standard_astro_v02_should_pass_corpus.json"

SUPPRESSED = ("abstention", "hard_block", "refusal")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    tasks = {
        task["id"]: task
        for task in json.loads(args.tasks.read_text(encoding="utf-8"))["tasks"]
    }
    expected_full = {
        task_id
        for task_id, task in tasks.items()
        if task.get("expected_disposition") == "full"
    }

    entries = []
    for line in args.samples.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sample = json.loads(line)
        if sample.get("condition") != "standard_astro":
            continue
        if sample.get("status") != "completed":
            continue
        task_id = str(sample.get("task_id"))
        if task_id not in expected_full:
            continue
        validation = sample.get("validation_summary") or {}
        disposition = str(validation.get("response_disposition") or "")
        if disposition not in SUPPRESSED:
            continue
        entries.append(
            {
                "sample_key": sample["sample_key"],
                "task_id": task_id,
                "model": sample["model"],
                "repeat_index": sample["repeat_index"],
                "prompt": tasks[task_id]["prompt"],
                "expected_disposition": "full",
                "observed_disposition": disposition,
                "routed_task_kind": validation.get("task_kind"),
                "llm_calls": sample.get("llm_calls"),
                "duration_seconds": sample.get("duration_seconds"),
                "reply": sample.get("reply"),
            }
        )

    corpus = {
        "schema_version": 1,
        "corpus_id": "standard-astro-v02-should-pass",
        "source_evaluation": "standard-astro-v02-natural-model-in-loop",
        "definition": (
            "standard_astro samples of tasks preregistered as disposition=full "
            "whose observed disposition was abstention/hard_block/refusal: the "
            "specificity (false-kill) side of the gate error budget."
        ),
        "usage": (
            "Replay alongside blind-suite group B whenever a claim gate, router "
            "or parser changes; a fix must reduce this list without adding "
            "escapes on the B side."
        ),
        "expected_full_task_ids": sorted(expected_full),
        "entries": sorted(entries, key=lambda item: item["sample_key"]),
    }
    args.out.write_text(
        json.dumps(corpus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(entries)} should-pass entries to {args.out}")


if __name__ == "__main__":
    main()
