from __future__ import annotations

import asyncio
from typing import Any

from app.api import chat as chat_mod
from app.config import settings


DESI_PROMPT = (
    "Use arXiv:2503.14738 Table 4 LRG2 to recompute D_M/D_H. "
    "D_M/r_d=17.351 +/- 0.177 and D_H/r_d=19.455 +/- 0.330, "
    "rho=-0.404. Do not run a likelihood or fit."
)


def _run_loop(fake_llm, fake_exec, prompt: str, tools: list[dict[str, Any]]) -> dict:
    original_llm = chat_mod._llm_messages_create
    original_exec = chat_mod._execute_tool_calls
    original_flag = settings.lightweight_verification_enabled
    chat_mod._llm_messages_create = fake_llm
    chat_mod._execute_tool_calls = fake_exec
    settings.lightweight_verification_enabled = True
    try:
        return asyncio.run(
            chat_mod._run_agent_loop(
                system="test system",
                messages=[{"role": "user", "content": prompt}],
                tools=tools,
                provider_api_keys={},
                agent_name="orchestrator",
                python_session_id="lightweight-loop-test",
            )
        )
    finally:
        chat_mod._llm_messages_create = original_llm
        chat_mod._execute_tool_calls = original_exec
        settings.lightweight_verification_enabled = original_flag


def _full_receipt() -> dict[str, Any]:
    return {
        "success": True,
        "schema_version": 1,
        "task_kind": "deterministic_source_check",
        "operation": "ratio",
        "result": {
            "value": 0.891852994,
            "standard_uncertainty": 0.020562805,
            "unit": "dimensionless",
            "rounded_display": "0.89185299 +/- 0.020562805",
        },
        "inputs": [],
        "formula": "q0 / q1",
        "uncertainty_model": {
            "kind": "correlation_matrix",
            "matrix": [[1, -0.404], [-0.404, 1]],
        },
        "calculation_status": "verified_deterministic",
        "source_status": "verified_exact",
        "claim_scopes": {"derived_numeric": True, "source_measurement": True},
        "source_evidence": [
            {
                "id": "source-1",
                "kind": "arxiv",
                "identifier": "2503.14738",
                "locator": "Table 4, LRG2",
                "status": "verified_exact",
                "extraction_method": "ar5iv_html",
                "sha256": "a" * 64,
            }
        ],
        "assumptions": [],
        "boundary_statement": "This is a table consistency calculation, not a fit.",
        "receipt_sha256": "b" * 64,
        "response_disposition": "full",
        "earliest_limiting_stage": None,
        "missing_dependencies": [],
        "safe_fallback": None,
        "publication_ready": True,
        "__tool_status__": "COMPLETED",
    }


def test_high_confidence_scalar_route_overrides_model_planner_choice() -> None:
    llm_calls = 0
    executed: list[list[dict[str, Any]]] = []

    async def fake_llm(*, tools, **kwargs):
        nonlocal llm_calls
        llm_calls += 1
        if llm_calls == 1:
            # The model tries the exact detour v0.2 is meant to prevent.
            return {
                "content": "",
                "stop_reason": "tool_use",
                "tool_calls": [
                    {
                        "id": "wrong-plan",
                        "name": "plan_research_program",
                        "input": {"question": DESI_PROMPT},
                    }
                ],
            }
        return {
            "content": "The controlled table consistency check completed; see the receipt.",
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_exec(tool_calls, *_args, **_kwargs):
        executed.append(tool_calls)
        return [
            {
                "id": tool_calls[0]["id"],
                "name": "verify_scalar_derivation",
                "input": tool_calls[0]["input"],
                "result": _full_receipt(),
            }
        ]

    result = _run_loop(
        fake_llm,
        fake_exec,
        DESI_PROMPT,
        [
            {"name": "verify_scalar_derivation", "input_schema": {}},
            {"name": "plan_research_program", "input_schema": {}},
            {"name": "run_research_matrix", "input_schema": {}},
        ],
    )

    assert len(executed) == 1
    assert llm_calls == 0
    assert executed[0][0]["name"] == "verify_scalar_derivation"
    assert executed[0][0]["input"]["operation"] == "ratio"
    assert not any(call[0]["name"] == "plan_research_program" for call in executed)
    assert result["validation_summary"]["schema_version"] == 2
    assert result["validation_summary"]["task_kind"] == "deterministic_source_check"
    assert result["validation_summary"]["response_disposition"] == "full"


def test_incomplete_scalar_packet_asks_for_inputs_without_running_matrix() -> None:
    observed_tools: list[list[str]] = []

    async def fake_llm(*, tools, **kwargs):
        observed_tools.append([str(tool.get("name")) for tool in tools])
        return {
            "content": (
                "Please provide the two values with uncertainties and either a "
                "correlation matrix or an explicit independence assumption."
            ),
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_exec(*args, **kwargs):  # pragma: no cover - no tool should run
        raise AssertionError("incomplete light task must not execute a tool")

    result = _run_loop(
        fake_llm,
        fake_exec,
        "From arXiv:2503.14738 Table 4 compute D_M/D_H. Do not run likelihood.",
        [
            {"name": "verify_scalar_derivation", "input_schema": {}},
            {"name": "plan_research_program", "input_schema": {}},
            {"name": "run_research_matrix", "input_schema": {}},
        ],
    )

    assert observed_tools == [[]]
    summary = result["validation_summary"]
    assert summary["task_kind"] == "deterministic_source_check"
    assert summary["response_disposition"] == "abstention"
    assert set(summary["missing_dependencies"]) == {
        "quantities",
        "uncertainty_model",
    }


def test_exploration_route_physically_hides_heavy_tools() -> None:
    observed_tools: list[str] = []

    async def fake_llm(*, tools, **kwargs):
        observed_tools.extend(str(tool.get("name")) for tool in tools)
        return {
            "content": "A useful first step is to inspect possible systematics qualitatively.",
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_exec(*args, **kwargs):  # pragma: no cover - no tool should run
        raise AssertionError("no tool should run")

    result = _run_loop(
        fake_llm,
        fake_exec,
        "Explore which method could test a possible systematic in the BAO table.",
        [
            {"name": "search_literature", "input_schema": {}},
            {"name": "plan_research_program", "input_schema": {}},
            {"name": "run_research_matrix", "input_schema": {}},
            {"name": "run_cosmology_likelihood_chain", "input_schema": {}},
        ],
    )

    assert "search_literature" in observed_tools
    assert "plan_research_program" not in observed_tools
    assert "run_research_matrix" not in observed_tools
    assert "run_cosmology_likelihood_chain" not in observed_tools
    assert result["validation_summary"]["task_kind"] == "research_exploration"
