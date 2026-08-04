from __future__ import annotations

import json
from pathlib import Path

from app.services.agent_runtime.prompt_routing import classify_task_kind


TASKS_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/research/standard_astro_v02_preregistered_tasks.json"
)


def _tasks() -> list[dict]:
    payload = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    return payload["tasks"]


def test_preregistered_matrix_is_frozen_and_unique() -> None:
    tasks = _tasks()
    assert len(tasks) == 8
    assert len({task["id"] for task in tasks}) == 8
    assert all(task["ground_truth"] for task in tasks)


def test_preregistered_task_kinds_match_the_unified_router() -> None:
    for task in _tasks():
        decision = classify_task_kind(task["prompt"])
        assert decision["task_kind"] == task["expected_task_kind"], task["id"]
        if task["expected_task_kind"] == "full_research":
            assert decision["heavy_route_allowed"] is True
        else:
            assert decision["heavy_route_allowed"] is False


def test_all_four_scalar_tasks_have_a_complete_direct_tool_call() -> None:
    for task in _tasks()[:4]:
        decision = classify_task_kind(task["prompt"])
        assert decision["missing_inputs"] == [], task["id"]
        assert decision["direct_tool_call"]["name"] == "verify_scalar_derivation"

    ns_input = classify_task_kind(_tasks()[3]["prompt"])["direct_tool_call"]["input"]
    assert ns_input["uncertainty_model"]["kind"] == "independent"
