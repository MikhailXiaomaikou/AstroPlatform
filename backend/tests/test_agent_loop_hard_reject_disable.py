"""G3.4 + 2026-05-20: Circuit-breaker coverage for hard-reject error_class.

A blind-test case showed: after a data-fetch tool received a local
range_too_large rejection, the LLM retried 12 times without triggering the
circuit breaker, wasting 12 LLM calls. Root cause has two layers:

1. the data-fetch tool was not in `_DATA_FETCH_TOOLS`, so the entire
   hard_failure counting logic was skipped by the outer if.
2. The soft_failure check was missing the `err_class in _HARD_REJECT_ERROR_CLASSES`
   short-circuit; future error messages containing keywords like "too large"
   would be wrongly classified as soft.

These tests drive `_run_agent_loop` with a fake LLM that keeps calling a
data-fetch tool which always returns an `error_class='range_too_large'`
rejection.  They assert the *behavior* — the tool is counted as a hard
failure and physically removed from the `tools` list handed to the LLM after
`DISABLE_AFTER_FAILURES` attempts — rather than the literal source text, so a
refactor that inverts the classification logic while keeping the strings is
caught.
"""
from __future__ import annotations

import asyncio

from app.api import chat as chat_mod


def _range_too_large_result(tool_call: dict) -> dict:
    """The hard-reject payload a tool emits for a locally-rejected request.

    `error_class='range_too_large'` must short-circuit the soft_failure check
    so this counts toward the disable threshold even though the human-readable
    error string contains the keyword "too large".
    """
    return {
        "id": tool_call["id"],
        "name": tool_call["name"],
        "input": tool_call["input"],
        "result": {
            "success": False,
            "error": "requested range is too large; narrow the parameters",
            "error_class": "range_too_large",
            "analysis_status": "FAILED",
        },
    }


def _run_loop_with_repeated_hard_reject(tool_name: str = "run_adql") -> list[list[str]]:
    """Drive the agent loop; the LLM calls `tool_name` every turn.

    Returns the list of tool-name lists the LLM was offered on each iteration,
    so the test can assert when `tool_name` disappears from the offered tools.
    """
    offered_tool_names_per_call: list[list[str]] = []

    async def fake_llm(*, tools, **kwargs):
        offered = [t.get("name") for t in tools]
        offered_tool_names_per_call.append(offered)
        if tool_name in offered:
            # Tool still available — keep calling it (the buggy LLM loop).
            return {
                "content": "",
                "stop_reason": "tool_use",
                "tool_calls": [
                    {
                        "id": f"call_{len(offered_tool_names_per_call)}",
                        "name": tool_name,
                        "input": {"query": "SELECT * FROM huge_table"},
                    }
                ],
            }
        # Tool was disabled — model can no longer call it; finish with a plain,
        # claim-free reply so the post-loop validation passes cleanly.
        return {
            "content": "I could not retrieve the requested data after repeated rejections.",
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_execute_tool_calls(real_tool_calls, *args, **kwargs):
        return [_range_too_large_result(tc) for tc in real_tool_calls]

    orig_llm = chat_mod._llm_messages_create
    orig_exec = chat_mod._execute_tool_calls
    chat_mod._llm_messages_create = fake_llm
    chat_mod._execute_tool_calls = fake_execute_tool_calls
    try:
        asyncio.run(
            chat_mod._run_agent_loop(
                system="test system prompt",
                messages=[{"role": "user", "content": "fetch the data"}],
                tools=[{"name": tool_name, "description": "fetch data", "input_schema": {}}],
                provider_api_keys={},
                agent_name="orchestrator",
                python_session_id="test-session",
            )
        )
    finally:
        chat_mod._llm_messages_create = orig_llm
        chat_mod._execute_tool_calls = orig_exec
    return offered_tool_names_per_call


def test_range_too_large_counts_as_hard_failure_and_disables_tool():
    """A repeated `range_too_large` rejection must trip the circuit breaker.

    After `DISABLE_AFTER_FAILURES` hard failures the tool is removed from the
    `tools` list handed to the LLM, so the model literally cannot call it
    again — this is the C2 blind-test fix (12 wasted retries without a
    disable).  If the soft/hard classification were inverted, the tool would
    stay offered forever and this assertion fails.
    """
    offered_per_call = _run_loop_with_repeated_hard_reject("run_adql")

    # The tool is offered for the first DISABLE_AFTER_FAILURES iterations, then
    # disabled (removed from the offered tools) on the next LLM call.
    calls_with_tool = sum(1 for names in offered_per_call if "run_adql" in names)
    # Exactly DISABLE_AFTER_FAILURES (3) iterations should still offer the tool;
    # each of those returns a hard reject.  The next LLM call must no longer
    # offer it.
    assert calls_with_tool == 3, (
        f"run_adql should be offered exactly 3 times before being disabled, "
        f"got {calls_with_tool}: {offered_per_call}"
    )
    # And the loop must reach an iteration where the tool is gone — proving the
    # disable actually happened rather than the loop ending for another reason.
    assert any("run_adql" not in names for names in offered_per_call), (
        f"run_adql was never removed from the offered tools: {offered_per_call}"
    )
    # The final offered-tools list (the iteration that ended the loop) must not
    # contain the disabled tool.
    assert "run_adql" not in offered_per_call[-1], (
        f"run_adql still offered on the final iteration: {offered_per_call[-1]}"
    )


def test_disable_threshold_is_three():
    """Guard the DISABLE_AFTER_FAILURES value that the behavioral test relies on.

    If someone changes the threshold, the behavioral assertion above (== 3)
    would silently start failing for the wrong reason; pin it explicitly so the
    failure points at the constant.
    """
    import inspect

    src = inspect.getsource(chat_mod._run_agent_loop)
    assert "DISABLE_AFTER_FAILURES = 3" in src, (
        "behavioral test pins DISABLE_AFTER_FAILURES at 3; update both together"
    )
