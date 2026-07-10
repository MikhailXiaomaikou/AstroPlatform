"""Stable automation-facing prompt triage for cosmology workflows.

External automation must use this public service (or its HTTP API) instead of
importing the agent runtime's private routing helpers.  Keeping the adapter in
the backend lets the internal routing implementation evolve without changing
the automation response contract.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from app.services.agent_runtime.prompt_routing import (
    _cosmology_direct_route_from_prompt,
    _cosmology_likelihood_run_calls_from_prompt,
    _is_cosmology_likelihood_workflow,
    _is_research_program_workflow,
)
from app.services.cosmology_likelihoods.verification import _executable_probe_keys


AutomationTriageVerdict = Literal["EXECUTE", "DETOUR", "NO_RUN", "MISS"]


class AutomationRunCall(TypedDict):
    """Stable, identifier-free summary of one deterministic run call."""

    tool: str | None
    dataset_keys: list[str]
    model: str | None
    supernova_sets: list[str] | None


class AutomationTriageResult(TypedDict):
    """Public result contract consumed by local automation."""

    is_cosmo_workflow: bool
    is_research_detour: bool
    direct_route: bool
    run_calls: list[AutomationRunCall]
    non_executable_keys: list[str]
    verdict: AutomationTriageVerdict


def triage_cosmology_prompt(text: str) -> AutomationTriageResult:
    """Predict deterministic cosmology routing without executing a tool or LLM.

    The output intentionally omits the runtime-generated call ``id`` so callers
    receive deterministic, durable data.  Verdict precedence and executable
    key handling mirror the original ``cosmo-second-order/triage.py`` adapter.
    """

    executable_keys = _executable_probe_keys()
    is_cosmo_workflow = _is_cosmology_likelihood_workflow(text)
    is_research_detour = _is_research_program_workflow(text)
    direct_route = bool(_cosmology_direct_route_from_prompt(text))
    run_calls: list[AutomationRunCall] = []
    non_executable_keys: list[str] = []

    for call in _cosmology_likelihood_run_calls_from_prompt(text):
        call_input = call.get("input") or {}
        dataset_keys = list(call_input.get("dataset_keys") or [])
        run_calls.append(
            {
                "tool": call.get("name"),
                "dataset_keys": dataset_keys,
                "model": call_input.get("model"),
                "supernova_sets": call_input.get("supernova_sets"),
            }
        )
        # Compressed class-B entries also execute in-process.  SPT-3G is the
        # one registry key the existing automation explicitly treats as a
        # non-executable likelihood rather than a compressed substitute.
        non_executable_keys.extend(
            key
            for key in dataset_keys
            if key not in executable_keys and key == "spt3g_cmb"
        )

    verdict: AutomationTriageVerdict
    if is_research_detour:
        verdict = "DETOUR"
    elif run_calls and not non_executable_keys:
        verdict = "EXECUTE"
    elif is_cosmo_workflow:
        verdict = "NO_RUN"
    else:
        verdict = "MISS"

    return {
        "is_cosmo_workflow": is_cosmo_workflow,
        "is_research_detour": is_research_detour,
        "direct_route": direct_route,
        "run_calls": run_calls,
        "non_executable_keys": non_executable_keys,
        "verdict": verdict,
    }
