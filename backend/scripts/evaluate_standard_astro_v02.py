#!/usr/bin/env python3
"""Run the pre-registered Standard Astro v0.2 evaluation matrix.

The JSONL output is resumable and intentionally compact: it contains the
user-visible reply, routing/validation metadata, and bounded tool receipts. It
never serializes system prompts, provider credentials, raw message context, or
untrimmed source documents.

v0.3 exploration harness (2026-09): the same runner accepts ``--budget``,
``--lightweight``, ``--steering``, ``--arm``, ``--system-appendix``,
``--lane-override`` and ``--record-pregate-drafts``, and records a per-sample
tool trace. With no new flag given the behaviour is identical to the v0.2
matrix (5/240/30 budget, lightweight verification forced on, same sample keys)
plus the additional trace fields on every record.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
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

# The v0.2 evaluation budget (unchanged since 2026-08-06). ``production`` and
# ``long`` hand the loop only a mode so ``_workflow_budget_config`` supplies
# the real 12/360 and 30/1800 numbers.
EVAL_WORKFLOW_BUDGET: dict[str, Any] = {
    "mode": "default",
    "max_iterations": 5,
    "agent_loop_seconds": 240,
    "summary_reserve_seconds": 30,
}
BUDGETS = ("eval", "production", "long")
LIGHTWEIGHT_CHOICES = ("on", "off", "both")
STEERING_CHOICES = ("on", "off")

# Arm presets (plan §3.2). Explicit CLI flags override preset values; the
# preset name is written into every record. Keys map to CLI destinations.
ARM_PRESETS: dict[str, dict[str, Any]] = {
    "C0": {"conditions": ["direct"]},
    "C1": {
        "conditions": ["standard_astro"],
        "budget": "production",
        "lightweight": "both",
    },
}
# Each ablation arm carries the flag state (and, for C2c, the task class) the
# pre-registration assigns it; inheriting C1's "both" would run cells the
# frozen design does not contain (review 2026-09-03). C2b is the lane
# ablation and only exists under flag ON; the rest are flag OFF.
ARM_PRESETS["C2a"] = {**ARM_PRESETS["C1"], "lightweight": "off", "system_appendix_required": True}
ARM_PRESETS["C2b"] = {**ARM_PRESETS["C1"], "lightweight": "on", "lane_override": True}
ARM_PRESETS["C2c"] = {
    **ARM_PRESETS["C1"],
    "lightweight": "off",
    "budget": "long",
    "task_class": "open",
}
ARM_PRESETS["C2d"] = {**ARM_PRESETS["C1"], "lightweight": "off", "steering": "off"}
ARM_PRESETS["C2_exploration"] = {
    **ARM_PRESETS["C1"], "lightweight": "off", "exploration_phase": True
}

# Status messages the loop emits immediately before a forced (non-model)
# tool call. A run of ``tool_call`` events that directly follows one of these
# status events is counted as forced; everything else is model-chosen (plan
# §3.2, loop.py 1580-1700).
FORCED_ROUTE_STATUS_PREFIXES = (
    "Direct-route trigger matched",
    "Planning the research program",
    "Executing the runnable cells",
    "Building the claim provenance graph",
    "Listing the curated",
    "Building guarded cosmology",
    "Running registered cosmology",
    "Running the deterministic cosmology comparison",
)
SOFT_REMINDER_MARKER = "near the workflow deadline"
SCALAR_UNIVERSE_LIMIT = 2000
SCALAR_UNIVERSE_DEPTH = 6


_LLM_CALL_COUNT = 0
# One entry per counted model call: the tool names visible to that call.
_VISIBLE_TOOLS_LOG: list[list[str]] = []


def _install_llm_call_counter() -> None:
    """Count agent-loop model calls via the loop's late-binding shim.

    The agent loop resolves ``chat._llm_messages_create`` at call time (the
    monkeypatch channel its own docstring documents), so wrapping it here
    counts every model completion the loop makes without touching product
    code. The deadline-summary fallback calls ``inference_router.route``
    directly and is not counted, so ``llm_calls`` is a lower bound on
    degraded samples.

    The shim also records the tool names offered to each call so a sample
    can answer "was the next obvious tool visible to the model?".
    """
    original = chat._llm_messages_create

    async def _counting(**kwargs):
        global _LLM_CALL_COUNT
        _LLM_CALL_COUNT += 1
        _VISIBLE_TOOLS_LOG.append(
            [
                str(t.get("name") or "")
                for t in (kwargs.get("tools") or [])
                if isinstance(t, dict)
            ]
        )
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
    # The eight-task rule is part of the v0.2 preregistration; v0.3 files
    # (evaluation_id "standard-astro-v03...") register their own task count.
    v03_file = str(payload.get("evaluation_id") or "").startswith("standard-astro-v03")
    if not isinstance(tasks, list) or (len(tasks) != 8 and not v03_file):
        raise ValueError("The v0.2 preregistration must contain exactly eight tasks.")
    ids = [str(task.get("id") or "") for task in tasks]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("Pre-registered task ids must be unique and non-empty.")
    return tasks


def _registered_repeats(path: Path, tasks: list[dict[str, Any]]) -> dict[str, int]:
    """Per-task repeat counts taken from the task file's registered design.

    The v0.3 file registers repeats per ``task_class`` (chain x2, open x4;
    conditions.C1 and analysis_plan.power_note). A single global ``--repeats``
    would run the open tasks at the underpowered count where a zero-event
    result cannot exclude the 25% threshold (review 2026-09-03). A file with
    no ``registered_repeats`` (every v0.2 file) yields an empty mapping and the
    caller keeps its own default.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    registered = payload.get("registered_repeats")
    if not isinstance(registered, dict) or not registered:
        return {}
    counts: dict[str, int] = {}
    for task_class, value in registered.items():
        count = int(value)
        if count < 1:
            raise ValueError(
                f"registered_repeats[{task_class!r}] must be positive, got {value!r}."
            )
        counts[str(task_class)] = count
    per_task: dict[str, int] = {}
    for task in tasks:
        task_class = str(task.get("task_class") or "")
        if task_class not in counts:
            raise ValueError(
                f"Task {task['id']} has task_class {task_class!r}, which "
                f"registered_repeats does not cover ({sorted(counts)})."
            )
        per_task[str(task["id"])] = counts[task_class]
    return per_task


def _expand_variants(tasks: list[dict[str, Any]]) -> list[tuple[str, str | None, str]]:
    """Expand tasks to ``(task_id, variant_id, prompt)`` triples.

    A task without ``variants`` yields one triple with ``variant_id=None``
    (the v0.2 shape). A task with ``variants`` (list of ``{variant_id,
    prompt}``) yields one triple per variant and its own ``prompt`` is not
    run.
    """
    expanded: list[tuple[str, str | None, str]] = []
    for task in tasks:
        task_id = str(task["id"])
        variants = task.get("variants")
        if not variants:
            expanded.append((task_id, None, str(task["prompt"])))
            continue
        if not isinstance(variants, list):
            raise ValueError(f"Task {task_id}: variants must be a list.")
        seen: set[str] = set()
        for variant in variants:
            variant_id = str((variant or {}).get("variant_id") or "")
            prompt = str((variant or {}).get("prompt") or "")
            if not variant_id or not prompt or variant_id in seen:
                raise ValueError(
                    f"Task {task_id}: variant ids must be unique and non-empty "
                    "with a non-empty prompt."
                )
            seen.add(variant_id)
            expanded.append((task_id, variant_id, prompt))
    return expanded


def _sample_key(
    *,
    model: str,
    condition: str,
    task_id: str,
    repeat_index: int,
    variant_id: str | None = None,
    lightweight_suffix: str | None = None,
    appendix_sha256: str | None = None,
) -> str:
    task_part = f"{task_id}__{variant_id}" if variant_id else task_id
    key = f"{model}|{condition}|{task_part}|{repeat_index}"
    if lightweight_suffix:
        key += f"|lv={lightweight_suffix}"
    if appendix_sha256:
        # Twelve hex characters are enough to separate two appendix texts in a
        # resume file and short enough to keep the key readable.
        key += f"|sa={appendix_sha256[:12]}"
    return key


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
        # Codex review P2 (PR #54): rows recorded status="failed" (bridge
        # exceptions) must not mark a sample complete — resume retries
        # them. A key may recur only while every earlier row for it
        # failed; a duplicate after a completed row still raises above.
        if str(sample.get("status") or "") == "failed":
            continue
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


# ---------------------------------------------------------------------------
# v0.3 run options, arms and evaluation-only runtime overrides
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunOptions:
    """Per-run switches shared by every sample (plan §3.2)."""

    budget: str = "eval"
    steering_off: bool = False
    arm: str | None = None
    system_appendix: str | None = None
    # Identity of the appendix text, not just its content: two different C2a
    # appendices produced indistinguishable samples under identical sample
    # keys, so a resume could mix or skip interventions without any artifact
    # recording which text ran (Codex review 2026-09-03).
    system_appendix_path: str | None = None
    system_appendix_sha256: str | None = None
    lane_override: bool = False
    exploration_phase: bool = False
    drafts_path: Path | None = None
    tasks_sha256: str = ""
    git_rev: str = "unknown"


def _workflow_budget_for(budget: str) -> dict[str, Any]:
    if budget == "eval":
        return dict(EVAL_WORKFLOW_BUDGET)
    if budget == "production":
        return {"mode": "default"}
    if budget == "long":
        return {"mode": "long"}
    raise ValueError(f"Unknown budget {budget!r}; expected one of {BUDGETS}.")


def _git_rev() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr, flush=True)


@contextmanager
def _settings_override(name: str, value: Any, *, missing_warning: str | None = None) -> Iterator[bool]:
    """Set ``settings.<name>`` for one sample and restore it afterwards.

    Yields whether the attribute exists in this build. Unknown attributes are
    left alone (pydantic refuses to set undeclared fields) so a flag whose
    product-side switch ships in a separate PR degrades to a warning.
    """
    if not hasattr(settings, name):
        if missing_warning:
            _warn(missing_warning)
        yield False
        return
    previous = getattr(settings, name)
    setattr(settings, name, value)
    try:
        yield True
    finally:
        setattr(settings, name, previous)


@contextmanager
def _lane_override_patch(enabled: bool) -> Iterator[None]:
    """Evaluation-only monkeypatch: open the heavy lane for every non-deterministic task kind.

    Wraps ``classify_task_kind`` on the loop module namespace (the loop looks
    the name up at call time) so ``heavy_route_allowed`` is True for every
    ``task_kind`` except ``deterministic_source_check``. Product code and
    ``prompt_routing`` itself are untouched; the original is restored after
    the sample.
    """
    if not enabled:
        yield
        return
    from app.services.agent_runtime import loop as loop_module

    original = loop_module.classify_task_kind

    def _open_lane(text: str):
        decision = original(text)
        if decision.get("task_kind") != "deterministic_source_check":
            decision = dict(decision)
            decision["heavy_route_allowed"] = True
        return decision

    loop_module.classify_task_kind = _open_lane
    try:
        yield
    finally:
        loop_module.classify_task_kind = original


def _routing_probe(prompt: str) -> dict[str, Any]:
    """Record what the pure routing functions say about the prompt.

    Uses ``prompt_routing`` directly, so the probe is unaffected by
    ``--lane-override`` and makes static routing explicit per sample.
    """
    from app.services.agent_runtime import prompt_routing

    decision = prompt_routing.classify_task_kind(prompt)
    direct_route = prompt_routing._cosmology_direct_route_from_prompt(prompt)
    return {
        "task_kind": decision.get("task_kind"),
        "heavy_route_allowed": bool(decision.get("heavy_route_allowed")),
        "confidence": decision.get("confidence"),
        "matched_signals": list(decision.get("matched_signals") or []),
        "cosmology_likelihood_workflow": bool(
            prompt_routing._is_cosmology_likelihood_workflow(prompt)
        ),
        "research_program_workflow": bool(
            prompt_routing._is_research_program_workflow(prompt)
        ),
        "cosmology_direct_route": (
            [str(call.get("name") or "") for call in direct_route]
            if direct_route
            else None
        ),
    }


def _tool_scalar_universe(result: dict[str, Any]) -> list[float]:
    """Finite numbers reachable in tool result payloads (depth <= 6).

    Skips citation/hash keys (``claim_validator._CITATION_KEYS_BLACKLIST``)
    for the same reason the validator does: bibcodes and digests would
    otherwise launder fabricated numbers into the universe.
    """
    from app.services.claim_validator import _CITATION_KEYS_BLACKLIST

    values: set[float] = set()

    def _walk(node: Any, depth: int) -> None:
        if depth > SCALAR_UNIVERSE_DEPTH or len(values) >= SCALAR_UNIVERSE_LIMIT:
            return
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            number = float(node)
            if math.isfinite(number):
                values.add(number)
            return
        if isinstance(node, dict):
            for key, child in node.items():
                if str(key) in _CITATION_KEYS_BLACKLIST:
                    continue
                _walk(child, depth + 1)
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                _walk(child, depth + 1)

    for item in result.get("tool_results") or []:
        if isinstance(item, dict):
            _walk(item.get("result"), 1)
    return sorted(values)[:SCALAR_UNIVERSE_LIMIT]


def _trace_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    budget_event = next((e for e in events if e.get("type") == "workflow_budget"), {})
    tool_sequence: list[str] = []
    forced = 0
    automatic = 0
    forced_run = False
    for event in events:
        kind = event.get("type")
        if kind == "status":
            message = str(event.get("message") or "")
            forced_run = message.startswith(FORCED_ROUTE_STATUS_PREFIXES)
            continue
        if kind != "tool_call":
            forced_run = False
            continue
        tool_sequence.append(str(event.get("tool") or ""))
        if event.get("automatic"):
            automatic += 1
        elif forced_run:
            forced += 1
    soft_reminder = any(
        e.get("type") == "status" and SOFT_REMINDER_MARKER in str(e.get("message") or "")
        for e in events
    )
    drafts = sum(
        1 for e in events if e.get("type") == "agent_text" and e.get("draft") is True
    )
    return {
        "budget_mode": budget_event.get("mode"),
        "max_iterations": budget_event.get("max_iterations"),
        "agent_loop_seconds": budget_event.get("agent_loop_seconds"),
        "n_tool_calls": len(tool_sequence),
        "tool_sequence": tool_sequence,
        "distinct_tools": sorted(set(tool_sequence)),
        "forced_tool_calls": forced,
        "automatic_tool_calls": automatic,
        "model_chosen_tool_calls": len(tool_sequence) - forced - automatic,
        "soft_reminder_fired": soft_reminder,
        "draft_agent_text_events": drafts,
    }


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


async def _run_standard(
    model: str,
    prompt: str,
    sample_key: str,
    *,
    lightweight: bool = True,
    options: RunOptions = RunOptions(),
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []

    async def collect(event: dict) -> None:
        events.append(dict(event))

    system = chat.SYSTEM_PROMPT
    if options.system_appendix:
        # Evaluation-only copy; app/prompts is untouched.
        system = chat.SYSTEM_PROMPT + "\n\n" + options.system_appendix

    visible_before = len(_VISIBLE_TOOLS_LOG)
    steering_present = exploration_present = False
    with ExitStack() as stack:
        stack.enter_context(
            _settings_override("lightweight_verification_enabled", lightweight)
        )
        if options.steering_off:
            steering_present = stack.enter_context(
                _settings_override("evaluation_steering_disabled", True)
            )
        if options.exploration_phase:
            exploration_present = stack.enter_context(
                _settings_override("exploration_phase_enabled", True)
            )
        stack.enter_context(_lane_override_patch(options.lane_override))
        started = time.monotonic()
        result = await chat._run_agent_loop(
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=chat._filter_tools_by_research_focus(list(TOOLS)),
            provider_api_keys={},
            agent_name="orchestrator",
            python_session_id=f"v02-{sample_key[:32]}-{uuid.uuid4().hex}",
            preferred_backend="local",
            model_profile=_profile(model),
            workflow_budget=_workflow_budget_for(options.budget),
            on_event=collect,
        )
        elapsed = time.monotonic() - started

    if options.drafts_path is not None:
        for event in events:
            if "draft" in event:
                _append_jsonl(
                    options.drafts_path,
                    {"sample_key": sample_key, "git_rev": options.git_rev, **event},
                )

    output: dict[str, Any] = {
        "reply": result.get("reply"),
        "tools": _compact_tools(result),
        "validation_summary": result.get("validation_summary"),
        "hit_deadline": bool(result.get("hit_deadline", False)),
        "hit_iteration_cap": bool(result.get("hit_iteration_cap", False)),
        "lightweight_verification_enabled": lightweight,
        "steering_disabled": steering_present,
        "exploration_phase_enabled": exploration_present,
        "elapsed_seconds": round(elapsed, 3),
        "visible_tools_per_llm_call": list(_VISIBLE_TOOLS_LOG[visible_before:]),
        "routing_probe": _routing_probe(prompt),
        "tool_scalar_universe": _tool_scalar_universe(result),
    }
    output.update(_trace_from_events(events))
    return output


async def _run_sample(
    *,
    model: str,
    condition: str,
    task_id: str,
    prompt: str,
    repeat_index: int,
    evaluation_id: str,
    variant_id: str | None = None,
    lightweight: bool = True,
    lightweight_suffix: str | None = None,
    options: RunOptions = RunOptions(),
) -> dict[str, Any]:
    key = _sample_key(
        model=model,
        condition=condition,
        task_id=task_id,
        repeat_index=repeat_index,
        variant_id=variant_id,
        lightweight_suffix=lightweight_suffix,
        appendix_sha256=options.system_appendix_sha256,
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
        "variant_id": variant_id,
        "arm": options.arm,
        "tasks_sha256": options.tasks_sha256,
        "system_appendix_path": options.system_appendix_path,
        "system_appendix_sha256": options.system_appendix_sha256,
        "git_rev": options.git_rev,
    }
    try:
        if condition == "direct":
            output = await _run_direct(model, prompt)
        else:
            output = await _run_standard(
                model, prompt, key, lightweight=lightweight, options=options
            )
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


@dataclass(frozen=True)
class SampleSpec:
    model: str
    condition: str
    task_id: str
    variant_id: str | None
    prompt: str
    repeat_index: int
    lightweight: bool
    lightweight_suffix: str | None
    appendix_sha256: str | None = None

    @property
    def key(self) -> str:
        return _sample_key(
            model=self.model,
            condition=self.condition,
            task_id=self.task_id,
            repeat_index=self.repeat_index,
            variant_id=self.variant_id,
            lightweight_suffix=self.lightweight_suffix,
            appendix_sha256=self.appendix_sha256,
        )


def _lightweight_states(choice: str) -> list[tuple[bool, str | None]]:
    """``(enabled, key suffix)`` pairs; the suffix is set only for ``both``."""
    if choice == "on":
        return [(True, None)]
    if choice == "off":
        return [(False, None)]
    if choice == "both":
        return [(True, "on"), (False, "off")]
    raise ValueError(f"Unknown lightweight choice {choice!r}.")


def _iter_matrix(
    *,
    models: list[str],
    conditions: list[str],
    expanded_tasks: list[tuple[str, str | None, str]],
    repeats: int,
    lightweight: str = "on",
    repeats_by_task: dict[str, int] | None = None,
    appendix_sha256: str | None = None,
) -> Iterator[SampleSpec]:
    states = _lightweight_states(lightweight)
    for model in models:
        for condition in conditions:
            for task_id, variant_id, prompt in expanded_tasks:
                # Each task gets its registered repeat count; ``repeats`` is
                # the fallback and the explicit ``--repeats`` override.
                task_repeats = (repeats_by_task or {}).get(task_id, repeats)
                for repeat_index in range(1, task_repeats + 1):
                    # The direct condition never enters the agent loop, so the
                    # lightweight switch cannot change it: one sample, no suffix.
                    for enabled, suffix in (states if condition != "direct" else [(True, None)]):
                        yield SampleSpec(
                            model=model,
                            condition=condition,
                            task_id=task_id,
                            variant_id=variant_id,
                            prompt=prompt,
                            repeat_index=repeat_index,
                            lightweight=enabled,
                            lightweight_suffix=suffix,
                            appendix_sha256=appendix_sha256,
                        )


def _resolve_arm(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Apply the ``--arm`` preset to unset flags, then fill the v0.2 defaults.

    Also the choke point that refuses an arm whose intervention this build
    cannot perform.
    """
    preset = ARM_PRESETS.get(args.arm or "", {})
    if preset.get("system_appendix_required") and args.system_appendix is None:
        parser.error(f"--arm {args.arm} requires --system-appendix PATH")
    if args.conditions is None:
        args.conditions = list(preset.get("conditions") or CONDITIONS)
    if args.budget is None:
        args.budget = str(preset.get("budget") or "eval")
    if args.lightweight is None:
        args.lightweight = str(preset.get("lightweight") or "on")
    if args.steering is None:
        args.steering = str(preset.get("steering") or "on")
    if preset.get("lane_override"):
        args.lane_override = True
    if preset.get("exploration_phase"):
        args.exploration_phase = True
    if args.exploration_phase and not hasattr(settings, "exploration_phase_enabled"):
        # Same failure mode as the steering ablation below, and the same
        # answer.  Warning and continuing collected ordinary flag-off samples
        # labelled C2_exploration, and the scorer does not exclude rows whose
        # `exploration_phase_enabled` is false, so an arm that intervened in
        # nothing could be reported as evidence about the exploration window
        # (Codex review 2026-09-03).
        parser.error(
            "--arm C2_exploration requires settings.exploration_phase_enabled, which "
            "this build does not have; the run would collect ordinary samples "
            "labelled as the exploration arm. Ship the product-side switch "
            "(PR-4a) first."
        )
    if args.steering == "off" and not hasattr(settings, "evaluation_steering_disabled"):
        # Fail closed. Without the switch the run collects ordinary flag-off
        # samples that are merely labelled C2d, and an ablation that intervened
        # in nothing would read as if it had (review 2026-09-03).
        parser.error(
            "steering off requires settings.evaluation_steering_disabled, which this "
            "build does not have; the run would collect ordinary samples labelled as "
            "the steering ablation. Ship the product-side switch (PR-3b) first."
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-path", type=Path, default=TASKS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    parser.add_argument("--conditions", nargs="+", choices=CONDITIONS, default=None)
    parser.add_argument("--task-ids", nargs="+")
    # Default None: each task takes the repeat count its file registers, and
    # falls back to DEFAULT_REPEATS when the file registers none (v0.2 files).
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--evaluation-id",
        default="standard-astro-v02-lightweight-verification",
    )
    # v0.3 exploration harness flags (plan §3.2). Defaults reproduce v0.2.
    parser.add_argument("--budget", choices=BUDGETS, default=None)
    parser.add_argument("--lightweight", choices=LIGHTWEIGHT_CHOICES, default=None)
    parser.add_argument("--steering", choices=STEERING_CHOICES, default=None)
    parser.add_argument("--arm", choices=sorted(ARM_PRESETS), default=None)
    parser.add_argument("--system-appendix", type=Path, default=None)
    parser.add_argument("--lane-override", action="store_true")
    parser.add_argument("--record-pregate-drafts", action="store_true")
    parser.set_defaults(exploration_phase=False)
    args = parser.parse_args()
    if args.repeats is not None and args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.no_resume and args.output.exists():
        parser.error("--no-resume requires a new output path; refusing to append duplicates")
    _validate_model_backends(parser, args.models)
    _resolve_arm(parser, args)

    tasks = _load_tasks(args.tasks_path)
    if args.task_ids:
        requested = set(args.task_ids)
        known = {str(task["id"]) for task in tasks}
        unknown = requested - known
        if unknown:
            parser.error(f"Unknown task ids: {sorted(unknown)}")
        tasks = [task for task in tasks if task["id"] in requested]
    elif ARM_PRESETS.get(args.arm or "", {}).get("task_class"):
        # C2c (long budget) is registered for the open tasks only; running the
        # statically routed chain tasks under it would spend hours on cells the
        # frozen design does not contain. An explicit --task-ids still wins.
        wanted = str(ARM_PRESETS[args.arm]["task_class"])
        selected = [t for t in tasks if str(t.get("task_class") or "") == wanted]
        if not selected:
            parser.error(
                f"Arm {args.arm} is registered for task_class {wanted!r}, "
                "but the task file has no such task."
            )
        tasks = selected
    # An explicit --repeats overrides the registered per-class counts.
    repeats_by_task = {} if args.repeats is not None else _registered_repeats(args.tasks_path, tasks)
    expanded_tasks = _expand_variants(tasks)
    completed = set() if args.no_resume else _completed_keys(args.output)
    _install_llm_call_counter()

    git_rev = _git_rev()
    # _resolve_arm already refused this run if the steering switch is missing.
    steering_off = args.steering == "off"
    _appendix_text = (
        args.system_appendix.read_text(encoding="utf-8")
        if args.system_appendix is not None
        else None
    )
    options = RunOptions(
        budget=args.budget,
        steering_off=steering_off,
        arm=args.arm,
        system_appendix=_appendix_text,
        system_appendix_path=(
            str(args.system_appendix) if args.system_appendix is not None else None
        ),
        system_appendix_sha256=(
            hashlib.sha256(_appendix_text.encode("utf-8")).hexdigest()
            if _appendix_text is not None
            else None
        ),
        lane_override=bool(args.lane_override),
        exploration_phase=bool(args.exploration_phase),
        # Offline pre-gate drafts: evaluation-only, never served, never
        # copied under docs/research (see run_exploration_matrix.sh).
        drafts_path=(
            args.output.parent / f"offline_drafts_{git_rev}.jsonl"
            if args.record_pregate_drafts
            else None
        ),
        tasks_sha256=_sha256_of(args.tasks_path),
        git_rev=git_rev,
    )

    specs = list(
        _iter_matrix(
            models=args.models,
            conditions=args.conditions,
            expanded_tasks=expanded_tasks,
            repeats=args.repeats if args.repeats is not None else DEFAULT_REPEATS,
            lightweight=args.lightweight,
            repeats_by_task=repeats_by_task,
            appendix_sha256=options.system_appendix_sha256,
        )
    )
    total = len(specs)
    pending = 0
    for spec in specs:
        key = spec.key
        if key in completed:
            continue
        pending += 1
        sample = await _run_sample(
            model=spec.model,
            condition=spec.condition,
            task_id=spec.task_id,
            prompt=spec.prompt,
            repeat_index=spec.repeat_index,
            evaluation_id=args.evaluation_id,
            variant_id=spec.variant_id,
            lightweight=spec.lightweight,
            lightweight_suffix=spec.lightweight_suffix,
            options=options,
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
