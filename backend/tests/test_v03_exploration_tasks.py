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
