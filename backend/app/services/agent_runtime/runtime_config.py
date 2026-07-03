"""Workflow budget configuration and meta-request detection.

Moved verbatim from app/api/chat.py (2026-07-03 god-file split).
"""

from typing import Any


_DEFAULT_WORKFLOW_BUDGET = {
    "mode": "default",
    "agent_loop_seconds": 360.0,
    "endpoint_timeout_seconds": 420.0,
    "summary_reserve_seconds": 60.0,
    "soft_reminder_seconds": 75.0,
    "max_iterations": 12,
    "tool_deadline_scale": 1.0,
}
_LONG_WORKFLOW_BUDGET = {
    "mode": "long",
    # P1.4 (2026-05-22): long mode meaningfully wider now that long-running
    # tools off-load to the async-tool runtime (P1.2) and SSE drops can
    # resume from workflow_checkpoint (P1.3). 30 min wall-clock is enough
    # to walk a 6-stage cosmology research flow with poll-and-continue
    # iterations, without occupying real loop seconds for the slow tool.
    "agent_loop_seconds": 1800.0,
    "endpoint_timeout_seconds": 1920.0,
    "summary_reserve_seconds": 120.0,
    "soft_reminder_seconds": 240.0,
    "max_iterations": 30,
    "tool_deadline_scale": 2.0,
}


def _workflow_budget_config(mode: str | None) -> dict[str, Any]:
    if str(mode or "").strip().lower() in {"long", "extended", "elastic"}:
        return dict(_LONG_WORKFLOW_BUDGET)
    return dict(_DEFAULT_WORKFLOW_BUDGET)


def _is_tool_inventory_request(message: str) -> bool:
    """Detect prompts whose goal is to inspect the actual callable tool schema."""
    msg = (message or "").lower()
    zh_markers = ("工具清单", "工具列表", "有哪些工具", "可用工具", "工具 schema", "工具名")
    en_markers = (
        "tool list",
        "available tools",
        "which tools",
        "what tools",
        "tool schema",
        "function schema",
        "registered tools",
    )
    return any(marker in msg for marker in zh_markers + en_markers)
