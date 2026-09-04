"""Guard the Daily blind-suite workflow's model-profile wiring.

The 2026-08-11 outage was a scheduled run silently exercising a model
contract nobody had pinned. This file pins the pieces a later edit could
drop without any deterministic test noticing: the manual
``deepseek_profile`` input, its fallback, and the ``BLIND_DEEPSEEK_PROFILE``
hand-off into the runner (review thread PRRT_kwDORoeoE86fOjE5).
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DAILY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "daily.yml"
RUNNER = REPO_ROOT / "backend" / "scripts" / "blind_test_cosmology_m0" / "runner.py"
DEFAULT_PROFILE = "deepseek:v4-pro"


def _workflow() -> dict:
    return yaml.safe_load(DAILY_WORKFLOW.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the bare ``on:`` key as the boolean True.
    return workflow.get("on") or workflow.get(True) or {}


def test_daily_dispatch_exposes_the_deepseek_profile_input_with_the_cron_default() -> None:
    inputs = _triggers(_workflow())["workflow_dispatch"]["inputs"]
    assert "deepseek_profile" in inputs, "manual dispatch lost the deepseek_profile input"
    assert inputs["deepseek_profile"]["default"] == DEFAULT_PROFILE


def test_daily_run_step_hands_the_profile_to_the_runner_with_the_same_fallback() -> None:
    workflow = _workflow()
    mappings = [
        str(step["env"]["BLIND_DEEPSEEK_PROFILE"])
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step.get("env"), dict) and "BLIND_DEEPSEEK_PROFILE" in step["env"]
    ]
    assert mappings, "no step exports BLIND_DEEPSEEK_PROFILE to the runner"
    for mapping in mappings:
        assert "inputs.deepseek_profile" in mapping, mapping
        assert DEFAULT_PROFILE in mapping, f"fallback must stay {DEFAULT_PROFILE}: {mapping}"


def test_runner_reads_the_profile_from_the_same_variable_with_the_same_default() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert 'os.environ.get("BLIND_DEEPSEEK_PROFILE", "deepseek:v4-pro")' in source
