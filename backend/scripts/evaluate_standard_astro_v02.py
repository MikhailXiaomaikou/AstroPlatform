#!/usr/bin/env python3
"""Run the pre-registered Standard Astro v0.2 evaluation matrix.

The JSONL output is resumable and intentionally compact: it contains the
user-visible reply, routing/validation metadata, and bounded tool receipts. It
never serializes system prompts, provider credentials, raw message context, or
untrimmed source documents.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import time
from typing import Any
import uuid

from app.ai.inference_router import LocalBackend
from app.ai.model_profiles import resolve_model_profile
from app.api import chat
from app.config import settings
from app.services.ai_tools import TOOLS


MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "claude-fable-5",
    "kimi-k3",
)
CONDITIONS = ("direct", "standard_astro")
DEFAULT_REPEATS = 3
REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_PATH = REPO_ROOT / "docs/research/standard_astro_v02_preregistered_tasks.json"
DEFAULT_OUTPUT = REPO_ROOT / ".local/standard-astro-v02/evaluation_samples.jsonl"

DIRECT_SYSTEM = (
    "You are completing a closed-book observational-cosmology evaluation with "
    "no external tools. Do not claim to have fetched a source, queried current "
    "data, or run a likelihood. You may calculate only from values explicitly "
    "provided in the user prompt. Separate source attribution, deterministic "
    "calculation, assumptions, and unavailable execution. Refuse requests to "
    "forge or conceal provenance."
)


_LLM_CALL_COUNT = 0


def _install_llm_call_counter() -> None:
    """Count agent-loop model calls via the loop's late-binding shim.

    The agent loop resolves ``chat._llm_messages_create`` at call time (the
    monkeypatch channel its own docstring documents), so wrapping it here
    counts every model completion the loop makes without touching product
    code. The deadline-summary fallback calls ``inference_router.route``
    directly and is not counted, so ``llm_calls`` is a lower bound on
    degraded samples.
    """
    original = chat._llm_messages_create

    async def _counting(**kwargs):
        global _LLM_CALL_COUNT
        _LLM_CALL_COUNT += 1
        return await original(**kwargs)

    chat._llm_messages_create = _counting


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _validate_model_backends(parser: argparse.ArgumentParser, models: list[str]) -> None:
    if any(
        not model.startswith(("claude-", "kimi-")) for model in models
    ) and not _env_enabled(
        "OPENAI_CLI_ENABLED"
    ):
        parser.error(
            "OpenAI/Codex models require OPENAI_CLI_ENABLED=1; refusing to record "
            "a matrix of immediate backend failures."
        )
    if any(model.startswith("claude-") for model in models) and not _env_enabled(
        "CLAUDE_CLI_ENABLED"
    ):
        parser.error(
            "Claude models require CLAUDE_CLI_ENABLED=1; refusing to record a "
            "matrix of immediate backend failures."
        )
    if any(model.startswith("kimi-") for model in models) and not _env_enabled(
        "KIMI_CLI_ENABLED"
    ):
        parser.error(
            "Kimi models require KIMI_CLI_ENABLED=1; refusing to record a "
            "matrix of immediate backend failures."
        )


def _profile(model: str):
    if model.startswith("claude-"):
        profile_id = "local:claude-cli"
        resolved_model_id = model
    elif model.startswith("kimi-"):
        profile_id = "local:kimi-cli"
        resolved_model_id = "kimi-code/k3"
    else:
        profile_id = "local:openai-cli"
        resolved_model_id = model
    base = resolve_model_profile("local", profile_id)
    return replace(
        base,
        model_id=model,
        resolved_model_id=resolved_model_id,
        display_name=model,
    )


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 8:
        raise ValueError("The v0.2 preregistration must contain exactly eight tasks.")
    ids = [str(task.get("id") or "") for task in tasks]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("Pre-registered task ids must be unique and non-empty.")
    return tasks


def _sample_key(
    *, model: str, condition: str, task_id: str, repeat_index: int
) -> str:
    return f"{model}|{condition}|{task_id}|{repeat_index}"


def _completed_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            sample = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_number}.") from exc
        key = str(sample.get("sample_key") or "")
        if not key or key in keys:
            raise ValueError(f"Missing or duplicate sample key at line {line_number}.")
        keys.add(key)
    return keys


_SIGNED_SCALAR_RECEIPT_FIELDS = (
    "success",
    "schema_version",
    "task_kind",
    "operation",
    "result",
    "inputs",
    "formula",
    "uncertainty_model",
    "calculation_status",
    "source_status",
    "claim_scopes",
    "source_evidence",
    "assumptions",
    "boundary_statement",
    "response_disposition",
    "earliest_limiting_stage",
    "missing_dependencies",
    "safe_fallback",
    "publication_ready",
    "supports_measurement_claims",
    "supports_derived_numeric_claims",
    "__tool_status__",
    "__do_not_claim_source_measurement__",
    "__do_not_claim__",
    "error",
    "error_class",
    "receipt_sha256",
)


def _compact_scalar_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    # The receipt digest was created before generic dispatcher provenance was
    # attached. Preserve every field in that signed contract, including full
    # source evidence, and exclude only later unsigned dispatcher metadata.
    # Crucially, absent fields stay absent: inserting ``None`` would also change
    # the canonical JSON and invalidate the original digest.
    return {
        key: payload[key]
        for key in _SIGNED_SCALAR_RECEIPT_FIELDS
        if key in payload
    }


def _compact_tools(result: dict[str, Any]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in result.get("tool_results") or []:
        if not isinstance(item, dict):
            continue
        payload = item.get("result")
        payload = payload if isinstance(payload, dict) else {}
        tool_name = str(item.get("tool") or "")
        record: dict[str, Any] = {
            "tool": tool_name,
            "status": payload.get("__tool_status__"),
            "success": payload.get("success"),
            "analysis_status": payload.get("analysis_status"),
            "calculation_status": payload.get("calculation_status"),
            "source_status": payload.get("source_status"),
            "response_disposition": payload.get("response_disposition"),
            "publication_ready": payload.get("publication_ready"),
            "error_class": payload.get("error_class"),
        }
        if tool_name == "verify_scalar_derivation":
            record["receipt"] = _compact_scalar_receipt(payload)
        compact.append(record)
    return compact


async def _run_direct(model: str, prompt: str) -> dict[str, Any]:
    response = await LocalBackend().complete(
        [{"role": "user", "content": prompt}],
        system=DIRECT_SYSTEM,
        tools=[],
        max_tokens=1800,
        temperature=0.0,
        request_timeout=180,
        model_profile=_profile(model),
    )
    return {
        "reply": response.get("content"),
        "model_name": response.get("model_name"),
        "tools": [],
        "validation_summary": None,
        "hit_deadline": False,
        "hit_iteration_cap": False,
    }


async def _run_standard(model: str, prompt: str, sample_key: str) -> dict[str, Any]:
    result = await chat._run_agent_loop(
        system=chat.SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        tools=chat._filter_tools_by_research_focus(list(TOOLS)),
        provider_api_keys={},
        agent_name="orchestrator",
        python_session_id=f"v02-{sample_key[:32]}-{uuid.uuid4().hex}",
        preferred_backend="local",
        model_profile=_profile(model),
        workflow_budget={
            "mode": "default",
            "max_iterations": 5,
            "agent_loop_seconds": 240,
            "summary_reserve_seconds": 30,
        },
    )
    return {
        "reply": result.get("reply"),
        "tools": _compact_tools(result),
        "validation_summary": result.get("validation_summary"),
        "hit_deadline": bool(result.get("hit_deadline", False)),
        "hit_iteration_cap": bool(result.get("hit_iteration_cap", False)),
    }


async def _run_sample(
    *,
    model: str,
    condition: str,
    task: dict[str, Any],
    repeat_index: int,
    evaluation_id: str,
) -> dict[str, Any]:
    task_id = str(task["id"])
    key = _sample_key(
        model=model,
        condition=condition,
        task_id=task_id,
        repeat_index=repeat_index,
    )
    started = time.monotonic()
    calls_before = _LLM_CALL_COUNT
    record: dict[str, Any] = {
        "schema_version": 1,
        "evaluation_id": evaluation_id,
        "sample_key": key,
        "model": model,
        "condition": condition,
        "task_id": task_id,
        "repeat_index": repeat_index,
    }
    try:
        if condition == "direct":
            output = await _run_direct(model, str(task["prompt"]))
        else:
            output = await _run_standard(model, str(task["prompt"]), key)
        record.update(output)
        record["status"] = "completed"
    except Exception as exc:  # preserve the rest of the registered matrix
        record.update(
            {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }
        )
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    # Direct runs exactly one completion outside the counted shim; the
    # standard condition runs through the agent loop where the shim counts
    # every model call (0 means the deterministic route answered alone).
    if condition == "direct":
        record["llm_calls"] = 1 if record["status"] == "completed" else 0
    else:
        record["llm_calls"] = _LLM_CALL_COUNT - calls_before
    return record


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")
        handle.flush()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-path", type=Path, default=TASKS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    parser.add_argument(
        "--conditions", nargs="+", choices=CONDITIONS, default=list(CONDITIONS)
    )
    parser.add_argument("--task-ids", nargs="+")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--evaluation-id",
        default="standard-astro-v02-lightweight-verification",
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.no_resume and args.output.exists():
        parser.error("--no-resume requires a new output path; refusing to append duplicates")
    _validate_model_backends(parser, args.models)

    tasks = _load_tasks(args.tasks_path)
    if args.task_ids:
        requested = set(args.task_ids)
        known = {str(task["id"]) for task in tasks}
        unknown = requested - known
        if unknown:
            parser.error(f"Unknown task ids: {sorted(unknown)}")
        tasks = [task for task in tasks if task["id"] in requested]
    completed = set() if args.no_resume else _completed_keys(args.output)
    settings.lightweight_verification_enabled = True
    _install_llm_call_counter()

    total = len(args.models) * len(args.conditions) * len(tasks) * args.repeats
    pending = 0
    for model in args.models:
        for condition in args.conditions:
            for task in tasks:
                for repeat_index in range(1, args.repeats + 1):
                    key = _sample_key(
                        model=model,
                        condition=condition,
                        task_id=str(task["id"]),
                        repeat_index=repeat_index,
                    )
                    if key in completed:
                        continue
                    pending += 1
                    sample = await _run_sample(
                        model=model,
                        condition=condition,
                        task=task,
                        repeat_index=repeat_index,
                        evaluation_id=args.evaluation_id,
                    )
                    _append_jsonl(args.output, sample)
                    print(
                        json.dumps(
                            {
                                "progress": f"{len(completed) + pending}/{total}",
                                "sample_key": key,
                                "status": sample["status"],
                                "duration_seconds": sample["duration_seconds"],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )


if __name__ == "__main__":
    asyncio.run(main())
