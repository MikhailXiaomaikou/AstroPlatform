#!/usr/bin/env python3
"""Merge provider-sharded v0.2 JSONL files into one audited matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "claude-fable-5",
    "kimi-k3",
)
CONDITIONS = ("direct", "standard_astro")
EXPECTED_TASK_PREFIXES = tuple(f"V02_{index:02d}" for index in range(1, 9))


def _read(inputs: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in inputs:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            record = json.loads(line)
            key = str(record.get("sample_key") or "")
            if not key:
                raise ValueError(f"Missing sample key in {path}:{line_number}")
            if key in seen:
                raise ValueError(f"Duplicate sample key across shards: {key}")
            seen.add(key)
            records.append(record)
    return records


def _sort_key(record: dict[str, Any]) -> tuple[int, int, int, int]:
    model = str(record.get("model"))
    condition = str(record.get("condition"))
    prefix = str(record.get("task_id"))[:6]
    if model not in MODELS or condition not in CONDITIONS:
        raise ValueError(f"Unknown matrix coordinate: {record.get('sample_key')}")
    if prefix not in EXPECTED_TASK_PREFIXES:
        raise ValueError(f"Unknown task coordinate: {record.get('sample_key')}")
    return (
        MODELS.index(model),
        CONDITIONS.index(condition),
        EXPECTED_TASK_PREFIXES.index(prefix),
        int(record.get("repeat_index")),
    )


def _validate_complete(records: list[dict[str, Any]], repeats: int) -> None:
    expected = {
        (model, condition, task, repeat)
        for model in MODELS
        for condition in CONDITIONS
        for task in EXPECTED_TASK_PREFIXES
        for repeat in range(1, repeats + 1)
    }
    actual = {
        (
            str(record.get("model")),
            str(record.get("condition")),
            str(record.get("task_id"))[:6],
            int(record.get("repeat_index")),
        )
        for record in records
    }
    if actual != expected:
        missing = sorted(expected - actual)[:5]
        extra = sorted(actual - expected)[:5]
        raise ValueError(
            f"Incomplete merged matrix: expected {len(expected)}, got {len(actual)}; "
            f"missing={missing}; extra={extra}"
        )
    failed = [
        str(record.get("sample_key"))
        for record in records
        if record.get("status") != "completed"
    ]
    if failed:
        raise ValueError(f"Transport failures remain in formal matrix: {failed[:5]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    records = _read(args.inputs)
    _validate_complete(records, args.repeats)
    records.sort(key=_sort_key)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
    temporary.replace(args.output)
    print(f"Merged {len(records)} completed samples into {args.output}")


if __name__ == "__main__":
    main()
