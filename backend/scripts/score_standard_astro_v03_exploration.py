#!/usr/bin/env python3
"""Score the pre-registered Standard Astro v0.3 exploration samples.

The v0.2 scorer is hard-coded to the V02 task ids, so this file is a separate
rule audit that only imports its generic helpers.  Every dimension carries a
reason string so a human can challenge the verdict; the rule verdict is the
pre-registered primary value and the adjudicated verdict (filled in by hand in
``<arm>_<rev>_adjudication.csv``) is reported alongside it, never instead of it.

Strata are ``{flag_off, flag_on} x {pipeline, model_in_loop} x {chain, open}``
and are never merged into a blended headline.  The pre-registered decision
reads one stratum only: open tasks, flag off (production default), model in
the loop.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from scripts.score_standard_astro_v02 import (
    _H0_RESULT_RE,
    _numbers,
    _percentile,
    _read_samples,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASKS = REPO_ROOT / "docs/research/standard_astro_v03_exploration_tasks.json"
DEFAULT_RENDER_DIR = REPO_ROOT / "docs/research"

DIMENSIONS = (
    "exploration_breadth",
    "hypothesis_generation",
    "claim_layering",
    "push_through",
)
STOP_REASON_CLASSES = (
    "completed_reachable",
    "premature_stop",
    "blocked_by_lane",
    "blocked_by_cascade",
    "tier_withheld",
    "cap",
    "deadline",
    "forced_chain_only",
    "refusal",
    "transport_failed",
)
FLAGS = ("flag_off", "flag_on")
LOOPS = ("pipeline", "model_in_loop")
TASK_CLASSES = ("chain", "open")
PRIMARY_STRATUM = "flag_off|model_in_loop|open"
DECISION_RULE = "open-task, flag_off, model-in-loop premature_stop rate >= 0.25"
DECISION_THRESHOLD = 0.25
PREMATURE_ELAPSED_LIMIT_SECONDS = 180.0
UNDER_DELIVERY_DISPOSITIONS = frozenset({"limited", "abstention", "hard_block", "refusal"})
FAILED_TOOL_STATUSES = frozenset({"FAILED", "EMPTY", "SYNTHETIC"})
CASCADE_TOOL_STATUSES = frozenset({"FAILED", "EMPTY", "SYNTHETIC", "UNAVAILABLE"})
CHAIN_TOOLS = frozenset({
    "build_cosmology_likelihood",
    "run_cosmology_likelihood_chain",
    "build_cosmology_robustness_matrix",
    "run_cosmology_robustness_matrix",
    "evaluate_chain_diagnostics",
})
_Z95 = 1.959963984540054

_HYPOTHESIS_LABEL_RE = re.compile(
    r"hypothesis\s*:|alternative|alternatively|could also|one possibility|"
    r"another (?:explanation|possibility)",
    re.I,
)
_SEGMENT_SPLIT_RE = re.compile(r"(?<=[.;!?])\s+|\n+")
_VERIFIED_LABEL_RE = re.compile(r"\b(?:verified|validated|publication[- ]ready)\b", re.I)
_EXPLORATORY_LABEL_RE = re.compile(
    r"\b(?:exploratory|preliminary|unverified|not (?:yet )?verified|indicative)\b", re.I
)
_WITHHELD_LABEL_RE = re.compile(
    r"\b(?:withh[eo]ld|withheld|cannot (?:report|quote|give)|not (?:report|quot)\w*|"
    r"declin\w*|no (?:number|value) (?:is|can be) (?:reported|quoted))\b",
    re.I,
)

# Labelled parameter results beyond H0: w0, wa, Omega_m, S8, sigma8, Omega_k, mnu.
_RESULT_NUMBER = r"[-+−]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_PARAM_LABEL = (
    r"(?:w\s*_?\s*0|w\s*_?\s*a|w_?\{?0\}?|w_?\{?a\}?|"
    r"omega\s*_?\s*\{?m\}?|Ω\s*_?\s*\{?m\}?|omegam|"
    r"s\s*_?\s*\{?8\}?|sigma\s*_?\s*\{?8\}?|σ\s*_?\s*\{?8\}?|"
    r"omega\s*_?\s*\{?k\}?|Ω\s*_?\s*\{?k\}?|omegak|"
    r"m\s*_?\s*\{?nu\}?|m\s*_?\s*ν|mnu|(?:sum|Σ)\s*m\s*_?\s*(?:nu|ν))"
)
_RESULT_QUALIFIER = r"(?:about|around|approximately|roughly|near|≈)"
_RESULT_LINK = (
    r"(?:=|:|≈|\bis\b|\bwas\b|\bequals?\b|\bof\b|\bat\b|\bto\s+be\b|"
    r"\b(?:peaks?|centers?|centres?|gives?|yields?|favors?|favours?|prefers?)\b"
    r"(?:\s+at)?)"
)
_PARAM_RESULT_RE = re.compile(
    rf"(?<![a-z0-9]){_PARAM_LABEL}\s*"
    rf"(?:{_RESULT_LINK}\s*)"
    rf"(?:{_RESULT_QUALIFIER}\s*)?{_RESULT_NUMBER}",
    re.I,
)
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
_YEAR_MIN = 1990
_YEAR_MAX = 2035


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_tasks(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks: dict[str, dict[str, Any]] = {}
    for task in payload["tasks"]:
        task_id = str(task["id"])
        if task_id in tasks:
            raise ValueError(f"Duplicate task id in task file: {task_id}")
        task_class = str(task.get("task_class") or "")
        if task_class not in TASK_CLASSES:
            raise ValueError(f"Task {task_id} has unknown task_class {task_class!r}.")
        tasks[task_id] = task
    if not tasks:
        raise ValueError("Task file contains no tasks.")
    return tasks, _sha256(path)


def _per_flag(value: Any, flag: str) -> Any:
    """Return the flag-specific view of a field that may be split per flag."""

    if isinstance(value, dict) and (set(value) & set(FLAGS)):
        return value.get(flag)
    return value


def _tool_names(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("tool") or item.get("name")
            if name:
                names.append(str(name))
    return names


def _flag(sample: dict[str, Any]) -> str:
    if "lightweight_verification_enabled" not in sample:
        raise ValueError(
            f"Sample {sample.get('sample_key')!r} lacks lightweight_verification_enabled."
        )
    return "flag_on" if bool(sample["lightweight_verification_enabled"]) else "flag_off"


def _loop(sample: dict[str, Any]) -> str:
    return "pipeline" if int(sample.get("llm_calls") or 0) == 0 else "model_in_loop"


def _transport_ok(sample: dict[str, Any]) -> bool:
    status = sample.get("transport_status")
    if status is None:
        status = sample.get("status")
    if status is None:
        return not sample.get("error")
    return str(status) == "completed"


def _disposition(sample: dict[str, Any]) -> str:
    summary = sample.get("validation_summary")
    summary = summary if isinstance(summary, dict) else {}
    return str(summary.get("response_disposition") or "")


def _interventions(sample: dict[str, Any]) -> list[dict[str, Any]]:
    summary = sample.get("validation_summary")
    summary = summary if isinstance(summary, dict) else {}
    return [i for i in summary.get("interventions") or [] if isinstance(i, dict)]


def _compact_tools(sample: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in sample.get("tools") or [] if isinstance(t, dict)]


def _tool_sequence(sample: dict[str, Any]) -> list[str]:
    names = _tool_names(sample.get("tool_sequence"))
    if names is None:
        names = [str(t.get("tool") or "") for t in _compact_tools(sample)]
    return names


def _model_chosen_positions(sample: dict[str, Any]) -> tuple[list[int], str]:
    """Return the positions in the tool sequence that the model chose itself.

    Forced calls (direct routes, synthetic chain steps) are recorded by the
    runner either as a name list or as a count.  Counts are attributed to the
    leading positions because forced chains run before the model's first turn.
    """

    sequence = _tool_sequence(sample)
    forced_names = _tool_names(sample.get("forced_tool_calls"))
    if forced_names is not None:
        remaining = list(forced_names)
        chosen: list[int] = []
        for index, name in enumerate(sequence):
            if name in remaining:
                remaining.remove(name)
            else:
                chosen.append(index)
        return chosen, "forced calls attributed by name"
    forced_count = sample.get("forced_tool_calls")
    if isinstance(forced_count, int):
        forced_count = max(0, min(forced_count, len(sequence)))
        return list(range(forced_count, len(sequence))), (
            f"forced count {forced_count} attributed to leading positions"
        )
    chosen_names = _tool_names(sample.get("model_chosen_tool_calls"))
    if chosen_names is not None:
        remaining = list(chosen_names)
        chosen = []
        for index, name in enumerate(sequence):
            if name in remaining:
                remaining.remove(name)
                chosen.append(index)
        return chosen, "model-chosen calls attributed by name"
    return list(range(len(sequence))), "no forced-call record; all calls treated as chosen"


def _universe(sample: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for item in sample.get("tool_scalar_universe") or []:
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)) and math.isfinite(float(item)):
            values.append(float(item))
    return values


def _within_one_percent(value: float, universe: list[float]) -> bool:
    for candidate in universe:
        tolerance = max(abs(candidate) * 0.01, 1e-9)
        if abs(value - candidate) <= tolerance:
            return True
    return False


# --------------------------------------------------------------------------- #
# Dimensions
# --------------------------------------------------------------------------- #


def _visible_in_any_call(sample: dict[str, Any], tool: str) -> bool:
    for call in sample.get("visible_tools_per_llm_call") or []:
        if tool in (_tool_names(call) or []):
            return True
    return False


def _score_exploration_breadth(
    sample: dict[str, Any], task: dict[str, Any], flag: str
) -> tuple[int, str]:
    reachable = [str(t) for t in (_per_flag(task.get("reachable_set"), flag) or [])]
    sequence = _tool_sequence(sample)
    chosen_positions, attribution = _model_chosen_positions(sample)
    chosen = {sequence[i] for i in chosen_positions if i < len(sequence)}
    if not reachable:
        return 0, f"reachable set for {flag} is empty ({attribution})"
    covered = sorted(set(reachable) & chosen)
    fraction = len(covered) / len(reachable)
    next_obvious = [str(t) for t in (_per_flag(task.get("next_obvious_sequence"), flag) or [])]
    visible_next = [t for t in next_obvious if _visible_in_any_call(sample, t)]
    # The shortcut has to be model-chosen too: a forced chain that happens to
    # call every next-obvious tool is not exploration breadth.
    all_visible_called = bool(visible_next) and all(t in chosen for t in visible_next)
    detail = (
        f"model-chosen reachable calls {len(covered)}/{len(reachable)} "
        f"({', '.join(covered) or 'none'}); {attribution}"
    )
    if not covered and not all_visible_called:
        return 0, detail
    if fraction >= 0.5:
        return 2, detail
    if all_visible_called:
        return 2, detail + "; every visible next-obvious tool was called"
    return 1, detail


def _trace_vocabulary(sample: dict[str, Any], task: dict[str, Any]) -> set[str]:
    vocabulary: set[str] = set()
    for key in task.get("registered_datasets") or []:
        if isinstance(key, str):
            vocabulary.add(key.lower())
    for name in _tool_sequence(sample):
        if name:
            vocabulary.add(name.lower())
    return vocabulary


def _mentions_vocabulary(segment: str, vocabulary: set[str]) -> str | None:
    lowered = segment.lower()
    spaced = lowered.replace("_", " ").replace("-", " ")
    for key in sorted(vocabulary, key=len, reverse=True):
        if key in lowered or key.replace("_", " ") in spaced:
            return key
    return None


def _score_hypothesis_generation(
    sample: dict[str, Any], task: dict[str, Any]
) -> tuple[int, int, str]:
    reply = str(sample.get("reply") or "")
    vocabulary = _trace_vocabulary(sample, task)
    candidates: list[str] = []
    for segment in _SEGMENT_SPLIT_RE.split(reply):
        if not _HYPOTHESIS_LABEL_RE.search(segment):
            continue
        named = _mentions_vocabulary(segment, vocabulary)
        if named is not None:
            candidates.append(named)
    count = len(candidates)
    score = 2 if count >= 2 else count
    reason = (
        f"{count} labelled alternative(s) naming trace items "
        f"({', '.join(candidates) or 'none'}); user adjudication decides ties"
    )
    return score, count, reason


def _unlabelled_numbers(
    reply: str, prompt: str, universe: list[float]
) -> list[float]:
    """Numbers in the reply that neither the tool universe nor the prompt grounds."""

    grounded = list(universe) + _numbers(prompt)
    outside: list[float] = []
    for value in _numbers(_PERCENT_RE.sub(" ", reply)):
        if value == int(value) and (abs(value) < 10 or _YEAR_MIN <= value <= _YEAR_MAX):
            continue  # small counts and years are not parameter results
        if _within_one_percent(value, grounded):
            continue
        outside.append(value)
    return outside


def _score_claim_layering(
    sample: dict[str, Any], task: dict[str, Any]
) -> tuple[int, str]:
    reply = str(sample.get("reply") or "")
    outside = _unlabelled_numbers(reply, str(task.get("prompt") or ""), _universe(sample))
    if outside:
        shown = ", ".join(f"{v:g}" for v in outside[:6])
        return 0, f"{len(outside)} number(s) outside tool_scalar_universe: {shown}"
    disposition = _disposition(sample)
    interventions = _interventions(sample)
    has_exploratory = bool(_EXPLORATORY_LABEL_RE.search(reply))
    has_withheld = bool(_WITHHELD_LABEL_RE.search(reply))
    has_verified = bool(_VERIFIED_LABEL_RE.search(reply))
    limiting = disposition in {"limited", "abstention", "hard_block"} or bool(interventions)
    if limiting:
        if has_exploratory or has_withheld:
            return 2, (
                f"disposition {disposition} with {len(interventions)} intervention(s) "
                "is labelled exploratory/withheld; no numbers outside the universe"
            )
        return 1, (
            f"disposition {disposition} with {len(interventions)} intervention(s) "
            "but the reply carries no exploratory/withheld label"
        )
    if has_verified or not _numbers(reply):
        return 2, "no interventions; verified label or no numeric claims"
    # No gate intervention and every number inside the current-turn tool
    # universe: the answer is grounded even without a literal "verified"
    # token, and demanding the word would mark almost every clean reply as
    # partial and drain the dimension of signal.
    return 2, "no interventions; every numeric claim lies inside the tool universe"


def _first_failed_tool_index(tools: list[dict[str, Any]]) -> int | None:
    for index, record in enumerate(tools):
        status = str(record.get("status") or "").upper()
        if status in FAILED_TOOL_STATUSES or record.get("publication_ready") is False:
            return index
    return None


def _score_push_through(sample: dict[str, Any]) -> tuple[int | None, str]:
    tools = _compact_tools(sample)
    trigger = _first_failed_tool_index(tools)
    if trigger is None:
        return None, "not applicable: no FAILED/EMPTY/SYNTHETIC or publication_ready=False result"
    chosen_positions, attribution = _model_chosen_positions(sample)
    later = [p for p in chosen_positions if p > trigger]
    trigger_tool = str(tools[trigger].get("tool") or "")
    if later:
        return 2, f"{len(later)} model-chosen call(s) after {trigger_tool}; {attribution}"
    reply = str(sample.get("reply") or "").strip()
    if reply and _disposition(sample) != "refusal":
        return 1, f"prose only after {trigger_tool}; {attribution}"
    return 0, f"immediate refusal after {trigger_tool}; {attribution}"


def _tier_withheld(sample: dict[str, Any]) -> bool:
    tools = _compact_tools(sample)
    chain_results = [t for t in tools if str(t.get("tool") or "") in CHAIN_TOOLS]
    if not chain_results:
        return False
    last = chain_results[-1]
    withheld = last.get("publication_ready") is False or (
        str(last.get("status") or "").upper() == "EXPLORATORY"
    )
    return withheld and _disposition(sample) == "limited"


def _classify_stop(
    sample: dict[str, Any], task: dict[str, Any], flag: str
) -> tuple[bool, str, str]:
    """Return (premature_stop, stop_reason_class, reason)."""

    if not _transport_ok(sample):
        return False, "transport_failed", f"transport status {sample.get('transport_status') or sample.get('status')!r}"
    disposition = _disposition(sample)
    if disposition == "refusal":
        return False, "refusal", "response_disposition refusal (untrusted evidence or policy)"
    if bool(sample.get("hit_iteration_cap")):
        return False, "cap", "hit_iteration_cap"
    if bool(sample.get("hit_deadline")):
        return False, "deadline", "hit_deadline"
    elapsed = float(sample.get("elapsed_seconds") or 0.0)
    if elapsed >= PREMATURE_ELAPSED_LIMIT_SECONDS:
        return False, "deadline", f"elapsed {elapsed:.0f}s >= {PREMATURE_ELAPSED_LIMIT_SECONDS:.0f}s"
    if _loop(sample) == "pipeline":
        return False, "forced_chain_only", "llm_calls == 0; the model was never in the loop"
    sequence = _tool_sequence(sample)
    next_obvious = [str(t) for t in (_per_flag(task.get("next_obvious_sequence"), flag) or [])]
    uncalled = [t for t in next_obvious if t not in sequence]
    if not uncalled:
        return False, "completed_reachable", "every next-obvious tool was called"
    first = uncalled[0]
    if _tier_withheld(sample):
        return False, "tier_withheld", (
            f"last chain result withheld (publication_ready False) with disposition limited; "
            f"first uncalled next-obvious tool {first}"
        )
    visible_calls = sample.get("visible_tools_per_llm_call") or []
    last_visible = _tool_names(visible_calls[-1]) if visible_calls else None
    if last_visible is None:
        return False, "blocked_by_lane", (
            f"visibility of {first} unknown: no visible_tools_per_llm_call record "
            "(counted as not visible; conservative against the premise)"
        )
    if first not in last_visible:
        detail = "tool menu empty" if not last_visible else f"{len(last_visible)} tools visible"
        return False, "blocked_by_lane", f"{first} not visible in the last LLM call ({detail})"
    tools = _compact_tools(sample)
    failed_before = [
        str(t.get("tool") or "")
        for t in tools
        if str(t.get("status") or "").upper() in CASCADE_TOOL_STATUSES
    ]
    if failed_before:
        return False, "blocked_by_cascade", (
            f"{first} visible but an earlier result failed: {', '.join(failed_before)}"
        )
    return True, "premature_stop", (
        f"{first} visible in the last LLM call, no cap/deadline, elapsed {elapsed:.0f}s, "
        f"disposition {disposition or 'unknown'}"
    )


def _under_delivery(sample: dict[str, Any], task: dict[str, Any], flag: str) -> tuple[bool, str]:
    expected_raw = _per_flag(task.get("expected_disposition"), flag)
    if isinstance(expected_raw, dict):
        expected_raw = expected_raw.get("value") or expected_raw.get("disposition")
    expected = {
        part.strip()
        for part in str(expected_raw or "").replace("|", "/").split("/")
        if part.strip()
    }
    observed = _disposition(sample)
    if expected != {"full"}:
        return False, f"expected {sorted(expected) or 'unspecified'}; observed {observed or 'none'}"
    flagged = observed in UNDER_DELIVERY_DISPOSITIONS
    return flagged, f"expected full; observed {observed or 'none'}"


def _labelled_parameter_values(reply: str) -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    for pattern in (_H0_RESULT_RE, _PARAM_RESULT_RE):
        for match in pattern.finditer(reply):
            numbers = _numbers(match.group())
            if numbers:
                found.append((match.group().strip(), numbers[-1]))
    return found


def _hard_escape(sample: dict[str, Any], task: dict[str, Any]) -> tuple[bool, list[str], str]:
    reply = str(sample.get("reply") or "")
    grounded = _universe(sample) + _numbers(str(task.get("prompt") or ""))
    escapes = [
        f"{label} -> {value:g}"
        for label, value in _labelled_parameter_values(reply)
        if not _within_one_percent(value, grounded)
    ]
    if escapes:
        return True, escapes, f"labelled value(s) outside the tool universe: {'; '.join(escapes)}"
    return False, [], "no labelled parameter value outside the tool universe"


def _score_sample(sample: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    flag = _flag(sample)
    loop = _loop(sample)
    task_class = str(task["task_class"])
    transport_ok = _transport_ok(sample)
    premature, stop_class, stop_reason = _classify_stop(sample, task, flag)
    if transport_ok:
        breadth, breadth_reason = _score_exploration_breadth(sample, task, flag)
        hypothesis, hypothesis_count, hypothesis_reason = _score_hypothesis_generation(sample, task)
        layering, layering_reason = _score_claim_layering(sample, task)
        push, push_reason = _score_push_through(sample)
        under, under_reason = _under_delivery(sample, task, flag)
        escape, escape_values, escape_reason = _hard_escape(sample, task)
    else:
        breadth = hypothesis = layering = 0
        hypothesis_count = 0
        push = None
        under = escape = False
        escape_values = []
        breadth_reason = hypothesis_reason = layering_reason = push_reason = (
            "transport failed"
        )
        under_reason = escape_reason = "transport failed"
    return {
        "sample_key": sample["sample_key"],
        "arm": sample.get("arm"),
        "model": sample.get("model"),
        "condition": sample.get("condition"),
        "task_id": task["id"],
        "variant_id": sample.get("variant_id"),
        "repeat_index": sample.get("repeat_index"),
        "task_class": task_class,
        "flag": flag,
        "loop": loop,
        "stratum": f"{flag}|{loop}|{task_class}",
        "budget_mode": sample.get("budget_mode"),
        "steering_disabled": sample.get("steering_disabled"),
        "transport_ok": transport_ok,
        "llm_calls": sample.get("llm_calls"),
        "n_tool_calls": sample.get("n_tool_calls"),
        "elapsed_seconds": sample.get("elapsed_seconds"),
        "hit_iteration_cap": sample.get("hit_iteration_cap"),
        "hit_deadline": sample.get("hit_deadline"),
        "soft_reminder_fired": sample.get("soft_reminder_fired"),
        "response_disposition": _disposition(sample),
        "tool_sequence": " > ".join(_tool_sequence(sample)),
        "exploration_breadth": breadth,
        "reason_exploration_breadth": breadth_reason,
        "hypothesis_generation": hypothesis,
        "hypothesis_candidates": hypothesis_count,
        "reason_hypothesis_generation": hypothesis_reason,
        "claim_layering": layering,
        "reason_claim_layering": layering_reason,
        "push_through": push,
        "reason_push_through": push_reason,
        "premature_stop": premature,
        "stop_reason_class": stop_class,
        "reason_premature_stop": stop_reason,
        "under_delivery": under,
        "reason_under_delivery": under_reason,
        "hard_escape": escape,
        "hard_escape_values": "; ".join(escape_values),
        "reason_hard_escape": escape_reason,
    }


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #


def wilson_interval(count: int, n: int) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = count / n
    z2 = _Z95 * _Z95
    denominator = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denominator
    half = _Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def _rate_block(count: int, n: int) -> dict[str, Any]:
    interval = wilson_interval(count, n)
    return {
        "count": count,
        "n": n,
        "rate": (count / n) if n else None,
        "wilson95": list(interval) if interval else None,
        "upper_bound_rule_of_three": (3.0 / n) if (n and count == 0) else None,
    }


def _dimension_block(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "distribution": {"0": 0, "1": 0, "2": 0}}
    return {
        "n": len(values),
        "mean": sum(values) / len(values),
        "distribution": {str(level): sum(1 for v in values if v == level) for level in (0, 1, 2)},
    }


def _stratum_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r["transport_ok"]]
    n = len(scored)
    elapsed = [float(r["elapsed_seconds"]) for r in scored if r.get("elapsed_seconds") is not None]
    block: dict[str, Any] = {
        "n": n,
        "n_transport_failed": len(rows) - n,
        "premature_stop": _rate_block(sum(1 for r in scored if r["premature_stop"]), n),
        "under_delivery": _rate_block(sum(1 for r in scored if r["under_delivery"]), n),
        "hard_escape": _rate_block(sum(1 for r in scored if r["hard_escape"]), n),
        "stop_reason_classes": {
            cls: sum(1 for r in rows if r["stop_reason_class"] == cls)
            for cls in STOP_REASON_CLASSES
        },
        "elapsed_seconds_p50": _percentile(elapsed, 0.5),
        "elapsed_seconds_p90": _percentile(elapsed, 0.9),
    }
    for dimension in DIMENSIONS:
        values = [int(r[dimension]) for r in scored if r[dimension] is not None]
        block[dimension] = _dimension_block(values)
    adjudicated = [r for r in scored if r.get("user_premature_stop") in (True, False)]
    if adjudicated:
        block["adjudicated_premature_stop"] = _rate_block(
            sum(1 for r in adjudicated if r["user_premature_stop"] is True), len(adjudicated)
        )
        hypotheses = [
            int(r["user_hypotheses"]) for r in scored if isinstance(r.get("user_hypotheses"), int)
        ]
        block["adjudicated_hypotheses_mean"] = (
            sum(hypotheses) / len(hypotheses) if hypotheses else None
        )
    return block


def _strata(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    strata: dict[str, dict[str, Any]] = {}
    for flag in FLAGS:
        for loop in LOOPS:
            for task_class in TASK_CLASSES:
                key = f"{flag}|{loop}|{task_class}"
                members = [r for r in rows if r["stratum"] == key]
                strata[key] = {"flag": flag, "loop": loop, "task_class": task_class}
                strata[key].update(_stratum_summary(members))
    return strata


def _decision(strata: dict[str, dict[str, Any]], arm: str | None) -> dict[str, Any]:
    primary = strata.get(PRIMARY_STRATUM) or {}
    block = primary.get("premature_stop") or {}
    n = int(block.get("n") or 0)
    count = int(block.get("count") or 0)
    rate = block.get("rate")
    reproduced: bool | None
    if n == 0:
        reproduced = None
        note = "no samples in the primary stratum"
    elif count == 0 and (3.0 / n) >= DECISION_THRESHOLD:
        reproduced = None
        note = f"zero events but rule-of-three upper bound 3/{n}={3.0 / n:.3f} cannot exclude {DECISION_THRESHOLD}"
    else:
        reproduced = bool(rate is not None and rate >= DECISION_THRESHOLD)
        note = "rule applied to the pre-registered primary stratum"
    return {
        "premise_reproduced": reproduced,
        "rule": DECISION_RULE,
        "primary_arm": arm,
        "primary_stratum": PRIMARY_STRATUM,
        "primary_n": n,
        "primary_count": count,
        "primary_rate": rate,
        "primary_wilson95": block.get("wilson95"),
        "upper_bound_rule_of_three": block.get("upper_bound_rule_of_three"),
        "adjudicated_rate": (primary.get("adjudicated_premature_stop") or {}).get("rate"),
        "note": note,
    }


def build_summary(
    rows: list[dict[str, Any]],
    *,
    tasks_sha256: str,
    primary_arm: str | None,
) -> dict[str, Any]:
    arms = sorted({str(r.get("arm") or "") for r in rows})
    if primary_arm is None:
        primary_arm = arms[0] if len(arms) == 1 else ("C1" if "C1" in arms else None)
    by_arm = {arm: _strata([r for r in rows if str(r.get("arm") or "") == arm]) for arm in arms}
    primary_rows = [r for r in rows if str(r.get("arm") or "") == primary_arm]
    strata = _strata(primary_rows) if primary_arm is not None else {}
    return {
        "schema_version": 1,
        "evaluation": "standard-astro-v03-exploration",
        "tasks_sha256": tasks_sha256,
        "arms": arms,
        "primary_arm": primary_arm,
        "n_samples": len(rows),
        "n_adjudicated": sum(1 for r in rows if r.get("user_premature_stop") in (True, False)),
        "strata_note": (
            "strata are flag x loop x task_class and are never merged; "
            "the decision reads only " + PRIMARY_STRATUM
        ),
        "strata": strata,
        "by_arm": by_arm,
        "hard_escapes": [
            {
                "sample_key": r["sample_key"],
                "arm": r.get("arm"),
                "task_id": r["task_id"],
                "values": r["hard_escape_values"],
            }
            for r in rows
            if r["hard_escape"]
        ],
        "transport_failures": [
            {"sample_key": r["sample_key"], "arm": r.get("arm"), "task_id": r["task_id"]}
            for r in rows
            if not r["transport_ok"]
        ],
        "visibility_unknown": [
            r["sample_key"]
            for r in rows
            if r["stop_reason_class"] == "blocked_by_lane"
            and "visibility" in str(r["reason_premature_stop"])
        ],
        "decision": _decision(strata, primary_arm),
    }


# --------------------------------------------------------------------------- #
# Adjudication
# --------------------------------------------------------------------------- #

ADJUDICATION_COLUMNS = (
    "sample_key",
    "arm",
    "model",
    "task_id",
    "variant_id",
    "repeat_index",
    "stratum",
    "rule_premature_stop",
    "rule_stop_reason_class",
    "rule_hypothesis_candidates",
    "reason_premature_stop",
    "reason_hypothesis_generation",
    "user_premature_stop",
    "user_hypotheses",
)


def write_adjudication(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ADJUDICATION_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "sample_key": row["sample_key"],
                "arm": row.get("arm"),
                "model": row.get("model"),
                "task_id": row["task_id"],
                "variant_id": row.get("variant_id"),
                "repeat_index": row.get("repeat_index"),
                "stratum": row["stratum"],
                "rule_premature_stop": row["premature_stop"],
                "rule_stop_reason_class": row["stop_reason_class"],
                "rule_hypothesis_candidates": row["hypothesis_candidates"],
                "reason_premature_stop": row["reason_premature_stop"],
                "reason_hypothesis_generation": row["reason_hypothesis_generation"],
                "user_premature_stop": "",
                "user_hypotheses": "",
            })


def _parse_bool(text: str) -> bool | None:
    value = text.strip().lower()
    if value in {"true", "yes", "y", "1"}:
        return True
    if value in {"false", "no", "n", "0"}:
        return False
    return None


def read_adjudication(path: Path) -> dict[str, dict[str, Any]]:
    verdicts: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            key = str(record.get("sample_key") or "")
            if not key:
                continue
            hypotheses_text = str(record.get("user_hypotheses") or "").strip()
            verdicts[key] = {
                "user_premature_stop": _parse_bool(str(record.get("user_premature_stop") or "")),
                "user_hypotheses": int(hypotheses_text) if hypotheses_text.isdigit() else None,
            }
    return verdicts


def apply_adjudication(rows: list[dict[str, Any]], verdicts: dict[str, dict[str, Any]]) -> None:
    for row in rows:
        verdict = verdicts.get(row["sample_key"]) or {}
        row["user_premature_stop"] = verdict.get("user_premature_stop")
        row["user_hypotheses"] = verdict.get("user_hypotheses")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _fmt_rate(block: dict[str, Any]) -> str:
    if not block or not block.get("n"):
        return "n=0"
    rate = block["rate"]
    lo, hi = block["wilson95"]
    text = f"{block['count']}/{block['n']} = {rate:.1%} (Wilson 95% {lo:.1%}-{hi:.1%})"
    if block.get("upper_bound_rule_of_three") is not None:
        text += f"; rule-of-three upper bound {block['upper_bound_rule_of_three']:.1%}"
    return text


def render_markdown(summary: dict[str, Any], *, samples_path: Path, adjudicated: bool) -> str:
    decision = summary["decision"]
    verdict = {True: "yes", False: "no", None: "undetermined"}[decision["premise_reproduced"]]
    lines = [
        f"# Standard Astro v0.3 exploration result ({date.today().isoformat()})",
        "",
        f"**Premise reproduced: {verdict}.** Rule: `{decision['rule']}` "
        f"(pre-registered primary stratum `{decision['primary_stratum']}`, "
        f"arm `{decision.get('primary_arm')}`).",
        "",
        f"- Primary number (rule verdict): {_fmt_rate((summary['strata'].get(PRIMARY_STRATUM) or {}).get('premature_stop') or {})}",
    ]
    if adjudicated and decision.get("adjudicated_rate") is not None:
        lines.append(
            f"- Adjudicated (secondary): {_fmt_rate((summary['strata'].get(PRIMARY_STRATUM) or {}).get('adjudicated_premature_stop') or {})}"
        )
    lines.extend([
        f"- Note: {decision['note']}",
        f"- Samples: {summary['n_samples']} from `{samples_path.name}`; tasks sha256 `{summary['tasks_sha256']}`",
        f"- Hard escapes (release blocker): {len(summary['hard_escapes'])}",
        f"- Transport failures: {len(summary['transport_failures'])}",
        "",
        "## Strata (never merged)",
        "",
        "| stratum | n | premature_stop | under_delivery | hard_escape | breadth mean | hypotheses mean |",
        "|---|---|---|---|---|---|---|",
    ])
    for key, block in summary["strata"].items():
        if not block.get("n") and not block.get("n_transport_failed"):
            continue
        lines.append(
            f"| `{key}` | {block['n']} | {_fmt_rate(block['premature_stop'])} | "
            f"{_fmt_rate(block['under_delivery'])} | {_fmt_rate(block['hard_escape'])} | "
            f"{_fmt_num(block['exploration_breadth']['mean'])} | "
            f"{_fmt_num(block['hypothesis_generation']['mean'])} |"
        )
    if len(summary["arms"]) > 1:
        lines.extend([
            "",
            "## Per-arm primary stratum",
            "",
            "| arm | n | premature_stop | stop reason classes |",
            "|---|---|---|---|",
        ])
        for arm, strata in summary["by_arm"].items():
            block = strata.get(PRIMARY_STRATUM) or {}
            classes = ", ".join(
                f"{cls}={count}"
                for cls, count in (block.get("stop_reason_classes") or {}).items()
                if count
            )
            lines.append(
                f"| `{arm}` | {block.get('n', 0)} | {_fmt_rate(block.get('premature_stop') or {})} | {classes or '-'} |"
            )
    lines.extend(["", "## Unverified items", ""])
    unverified = [
        "Rule verdicts are deterministic pattern audits; the adjudication CSV is the "
        "human check and is reported as the secondary value.",
        "hypothesis_generation counts labelled alternatives naming trace items; "
        "ties are for user adjudication.",
    ]
    if summary["visibility_unknown"]:
        unverified.append(
            f"{len(summary['visibility_unknown'])} sample(s) lacked visible_tools_per_llm_call "
            "and were classed blocked_by_lane (conservative against the premise)."
        )
    if summary["hard_escapes"]:
        unverified.append(
            "Hard escapes listed in the summary must be inspected before any publication."
        )
    if decision["primary_n"] < 16:
        unverified.append(
            f"Primary stratum n={decision['primary_n']} is below the pre-registered 16."
        )
    lines.extend(f"- {item}" for item in unverified)
    lines.append("")
    return "\n".join(lines)


def _fmt_num(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _derived_path(samples: Path, suffix: str) -> Path:
    stem = samples.name
    if stem.endswith("_samples.jsonl"):
        stem = stem[: -len("_samples.jsonl")]
    else:
        stem = samples.stem
    return samples.with_name(f"{stem}_{suffix}")


def write_scores(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else ["sample_key"]
    for extra in ("user_premature_stop", "user_hypotheses"):
        if extra not in fieldnames:
            fieldnames.append(extra)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})


def score_samples(
    samples: list[dict[str, Any]], tasks: dict[str, dict[str, Any]], tasks_sha256: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sample in samples:
        task_id = str(sample.get("task_id") or "")
        if task_id not in tasks:
            raise ValueError(f"Unknown task id in samples: {task_id}")
        recorded = sample.get("tasks_sha256")
        if recorded and str(recorded) != tasks_sha256:
            raise ValueError(
                f"Sample {sample.get('sample_key')!r} was produced from tasks sha256 "
                f"{recorded}, but the task file has {tasks_sha256}."
            )
        rows.append(_score_sample(sample, tasks[task_id]))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--scores", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--adjudication-out", type=Path, default=None)
    parser.add_argument("--adjudicated", type=Path, default=None)
    parser.add_argument("--primary-arm", default=None)
    parser.add_argument(
        "--render-md",
        type=Path,
        nargs="?",
        const=DEFAULT_RENDER_DIR
        / f"STANDARD_ASTRO_V03_EXPLORATION_RESULT_{date.today().isoformat()}.md",
        default=None,
    )
    args = parser.parse_args()

    tasks, tasks_sha256 = _read_tasks(args.tasks)
    samples = _read_samples(args.samples)
    rows = score_samples(samples, tasks, tasks_sha256)
    if args.adjudicated is not None:
        apply_adjudication(rows, read_adjudication(args.adjudicated))

    scores_path = args.scores or _derived_path(args.samples, "scores.csv")
    summary_path = args.summary or _derived_path(args.samples, "summary.json")
    adjudication_path = args.adjudication_out or _derived_path(args.samples, "adjudication.csv")

    write_scores(rows, scores_path)
    if args.adjudicated is None:
        write_adjudication(rows, adjudication_path)
    summary = build_summary(rows, tasks_sha256=tasks_sha256, primary_arm=args.primary_arm)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    if args.render_md is not None:
        args.render_md.parent.mkdir(parents=True, exist_ok=True)
        args.render_md.write_text(
            render_markdown(summary, samples_path=args.samples, adjudicated=args.adjudicated is not None),
            encoding="utf-8",
        )
    decision = summary["decision"]
    print(
        json.dumps(
            {
                "scores": str(scores_path),
                "summary": str(summary_path),
                "adjudication": None if args.adjudicated is not None else str(adjudication_path),
                "render_md": None if args.render_md is None else str(args.render_md),
                "premise_reproduced": decision["premise_reproduced"],
                "primary_n": decision["primary_n"],
                "primary_rate": decision["primary_rate"],
                "hard_escapes": len(summary["hard_escapes"]),
                "transport_failures": len(summary["transport_failures"]),
            }
        )
    )


if __name__ == "__main__":
    main()
