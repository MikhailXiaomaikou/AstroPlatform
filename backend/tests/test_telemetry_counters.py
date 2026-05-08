"""Action 3 (telemetry, 2026-05-08) — verify execute_tool and the
pipeline engine emit invocation counters.

Counters are the data that feeds the cosmology-focus surgery decisions
(which tools to drop from the allowlist after 7 days of production
traffic), so they must be wired correctly from day 1.

Reuses app.observability.metrics._registry; reset between tests.
"""

from __future__ import annotations

import asyncio

import pytest

from app.observability.metrics import _registry


@pytest.fixture(autouse=True)
def _clean_registry():
    _registry.reset()
    yield
    _registry.reset()


def test_execute_tool_increments_tool_invoked_total(monkeypatch):
    """One call to execute_tool() must increment tool_invoked_total
    with the right label."""
    from app.services import ai_tools

    async def _fake_inner(tool_name, *args, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", _fake_inner)
    # normalize_tool_result is heavy and irrelevant; replace with passthrough.
    from app.services import result_provenance
    monkeypatch.setattr(
        result_provenance, "normalize_tool_result",
        lambda name, result, tool_input=None: result,
    )

    asyncio.run(ai_tools.execute_tool("search_objects", {"query": "M31"}))

    snap = _registry.snapshot()
    assert "tool_invoked_total" in snap["counters"], snap["counters"]
    counters = snap["counters"]["tool_invoked_total"]
    # labels stored as sorted tuple of (key, value) pairs
    expected_key = (("tool", "search_objects"),)
    assert expected_key in counters, counters
    assert counters[expected_key] == 1.0


def test_execute_tool_separate_tools_get_separate_counters(monkeypatch):
    from app.services import ai_tools
    from app.services import result_provenance

    async def _fake_inner(tool_name, *args, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", _fake_inner)
    monkeypatch.setattr(
        result_provenance, "normalize_tool_result",
        lambda name, result, tool_input=None: result,
    )

    asyncio.run(ai_tools.execute_tool("search_objects", {}))
    asyncio.run(ai_tools.execute_tool("run_adql", {}))
    asyncio.run(ai_tools.execute_tool("search_objects", {}))

    counters = _registry.snapshot()["counters"]["tool_invoked_total"]
    assert counters[(("tool", "search_objects"),)] == 2.0
    assert counters[(("tool", "run_adql"),)] == 1.0


def test_execute_tool_counter_failure_does_not_break_call(monkeypatch):
    """Telemetry must never break the actual tool call.  If
    record_counter raises, execute_tool must still return."""
    from app.services import ai_tools
    from app.services import result_provenance

    async def _fake_inner(tool_name, *args, **kwargs):
        return {"value": 42}

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", _fake_inner)
    monkeypatch.setattr(
        result_provenance, "normalize_tool_result",
        lambda name, result, tool_input=None: result,
    )

    def _broken_record(*args, **kwargs):
        raise RuntimeError("metrics backend offline")

    import app.observability.metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "record_counter", _broken_record)

    # Must not raise.
    out = asyncio.run(ai_tools.execute_tool("any_tool", {}))
    assert out == {"value": 42}
