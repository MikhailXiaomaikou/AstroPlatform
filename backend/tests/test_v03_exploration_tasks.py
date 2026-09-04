"""Pre-registration integrity for the v0.3 exploration-depth experiment.

The experiment measures whether the model stops with a visible next-obvious
tool uncalled.  That measurement is only meaningful if the deterministic
router did not make the choice for it, so the four open tasks must classify
as ``general`` with no workflow and no direct route, and the frozen prompts
must match the committed sha256.  If a future routing change starts forcing
an open task, the thing to re-freeze is a NEW task file — never this one.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.services.agent_runtime.prompt_routing import (
    _cosmology_direct_route_from_prompt,
    _is_cosmology_likelihood_workflow,
    _is_research_program_workflow,
    classify_task_kind,
)

_DOCS = Path(__file__).resolve().parents[2] / "docs" / "research"
TASKS_PATH = _DOCS / "standard_astro_v03_exploration_tasks.json"
COMMITMENT_PATH = _DOCS / "standard_astro_v03_exploration_tasks_commitment.json"


def _payload() -> dict:
    return json.loads(TASKS_PATH.read_text(encoding="utf-8"))


def _tasks() -> list[dict]:
    return _payload()["tasks"]


def test_v03_tasks_sha256_matches_commitment() -> None:
    commitment = json.loads(COMMITMENT_PATH.read_text(encoding="utf-8"))
    raw = TASKS_PATH.read_bytes()
    assert commitment["artifact_role"] == "preregistration_commitment"
    assert commitment["tasks_file"] == TASKS_PATH.name
    assert commitment["sha256"] == hashlib.sha256(raw).hexdigest()
    assert commitment["size_bytes"] == len(raw)
    assert commitment["status"] == "FROZEN_NOT_YET_RUN"


def test_v03_matrix_is_frozen_and_unique() -> None:
    tasks = _tasks()
    assert len(tasks) == 8
    assert len({task["id"] for task in tasks}) == 8
    classes = [task["task_class"] for task in tasks]
    assert classes.count("open") == 4 and classes.count("chain") == 4
    for task in tasks:
        assert task["next_obvious_sequence"], task["id"]
        assert task["reachable_set"]["flag_off"], task["id"]
        assert task["expected_disposition"], task["id"]


def test_v03_open_tasks_are_not_router_forced() -> None:
    """The primary endpoint is measured on these four; a deterministic route
    would decide the tool sequence before the model acts."""
    for task in _tasks():
        if task["task_class"] != "open":
            continue
        prompt = task["prompt"]
        assert classify_task_kind(prompt)["task_kind"] == "general", task["id"]
        assert _is_cosmology_likelihood_workflow(prompt) is False, task["id"]
        assert _is_research_program_workflow(prompt) is False, task["id"]
        assert _cosmology_direct_route_from_prompt(prompt) is None, task["id"]


def test_v03_chain_tasks_route_as_recorded() -> None:
    for task in _tasks():
        if task["task_class"] != "chain":
            continue
        prompt = task["prompt"]
        recorded = task["expected_routing"]["flag_off"]
        assert classify_task_kind(prompt)["task_kind"] == recorded["task_kind"], task["id"]
        assert _is_cosmology_likelihood_workflow(prompt) == recorded[
            "cosmology_likelihood_workflow"
        ], task["id"]
        assert _is_research_program_workflow(prompt) == recorded[
            "research_program_workflow"
        ], task["id"]
        assert (
            _cosmology_direct_route_from_prompt(prompt) is not None
        ) == bool(recorded["direct_route"]), task["id"]


def test_v03_prompts_avoid_the_forbidden_router_tokens() -> None:
    """Each category records whether it applies to every task or only to the
    open ones (chain prompts deliberately carry heavy-intent words).  This is
    also the drift detector: if a future router change adds a trigger, the
    frozen prompts must be re-frozen in a NEW file rather than edited."""
    categories = _payload()["forbidden_prompt_tokens"]["categories"]
    for task in _tasks():
        lowered = task["prompt"].lower()
        for name, spec in categories.items():
            applies_to = spec.get("applies_to", "all")
            if applies_to == "open" and task["task_class"] != "open":
                continue
            for token in spec.get("tokens", []):
                assert str(token).lower() not in lowered, (task["id"], name, token)


def test_v03_analysis_plan_never_blends_strata() -> None:
    plan = _payload()["analysis_plan"]
    text = json.dumps(plan, ensure_ascii=False).lower()
    assert "llm_calls" in text
    assert "lightweight_verification_enabled" in text
    assert "premature_stop" in text
    assert "rule of three" in text or "rule_of_three" in text
    assert "0.25" in text or "25%" in text


def _steps_for_flag(task: dict, flag: str) -> list[str]:
    """The frozen steps that apply to one flag state.

    A step prefixed ``flag_off:``/``flag_on:`` belongs to that state only;
    every other step applies to both.
    """
    sequence = task["next_obvious_sequence"]
    if isinstance(sequence, dict):
        sequence = sequence.get(flag) or []
    steps = []
    for step in sequence:
        prefix = step.split(":", 1)[0].strip() if ":" in step else ""
        if prefix in {"flag_off", "flag_on"} and prefix != flag:
            continue
        steps.append(step)
    return steps


def test_v03_next_obvious_tools_projects_every_tool_named_in_a_step() -> None:
    """The scorer declares a task complete once every projected name appears in
    the trace, so a tool named in a step but missing from the projection makes a
    stop before it score completed_reachable.  The four open tasks dropped
    ``run_cosmology_likelihood_chain`` from their flag-off projection even though
    the step reads ``build_cosmology_likelihood(...) ->
    run_cosmology_likelihood_chain`` (review 2026-09-03)."""
    for task in _tasks():
        known_tools = set(task["reachable_set"]["flag_off"]) | set(task["reachable_set"]["flag_on"])
        for flag in ("flag_off", "flag_on"):
            named = {
                tool
                for step in _steps_for_flag(task, flag)
                for tool in known_tools
                if tool in step
            }
            projected = set(task["next_obvious_tools"][flag])
            assert named <= projected, (task["id"], flag, sorted(named - projected))
            # And nothing is projected that no step names.
            assert projected <= named, (task["id"], flag, sorted(projected - named))


def test_v03_registered_repeats_match_the_frozen_design() -> None:
    """conditions.C1 and analysis_plan.power_note fix chain x2 and open x4; the
    runner reads the mapping instead of applying one global --repeats, which
    would put the open tasks at 12 samples per flag - the underpowered zone
    where a zero-event result cannot exclude the 25% threshold."""
    payload = _payload()
    registered = payload["registered_repeats"]
    assert registered == {"chain": 2, "open": 4}
    assert "chain tasks x2 repeats, open tasks x4 repeats" in payload["conditions"]["C1"]
    assert "--repeats 4" in payload["analysis_plan"]["power_note"]
    classes = {task["task_class"] for task in _tasks()}
    assert classes <= set(registered), classes - set(registered)
    open_samples = registered["open"] * sum(1 for t in _tasks() if t["task_class"] == "open")
    assert open_samples == 16  # per flag state; 3/16 = 18.75% excludes 25%
