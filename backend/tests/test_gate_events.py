"""Structured validation-gate events (false-positive triage layer, 2026-06-11).

Locks three things:
1. build_gate_event schema + truncation + claim/violation serialization.
2. The JSONL sink: append/read-back round-trip, empty-path disable, and
   unwritable-path silence (logging must never break a reply).
3. End-to-end through _run_agent_loop with a fake LLM: a zero-data turn with
   a fabricated number must emit exactly one gate_event with gate=="zero_data",
   action=="blocked", a non-empty draft preview, and the event must flow
   through BOTH the on_event stream and the JSONL sink.
"""
from __future__ import annotations

import asyncio
import json

from app.api import chat as chat_mod
from app.observability.gate_events import (
    GATE_EVENT_TYPE,
    append_gate_event_jsonl,
    build_gate_event,
    claims_to_dicts,
    violations_to_dicts,
)


class _FakeClaim:
    def __init__(self, label: str, value: float, raw: str) -> None:
        self.label = label
        self.value = value
        self.raw = raw


class _FakeViolation:
    def __init__(self, kind: str, match_text: str, line_number: int) -> None:
        self.kind = kind
        self.match_text = match_text
        self.line_number = line_number


# ---------- build / serialize ----------


def test_build_gate_event_schema_and_truncation():
    evt = build_gate_event(
        gate="zero_data",
        action="blocked",
        reason="no_rewrite",
        agent="orchestrator",
        details={"long": "x" * 1000},
        tools_run=["b_tool", "a_tool"],
        universe_size=42,
        regenerations=2,
        draft="d" * 2000,
        final="f" * 2000,
        chat_session_id="cs1",
        python_session_id="ps1",
    )
    assert evt["type"] == GATE_EVENT_TYPE
    assert evt["schema_version"] == 1
    assert evt["gate"] == "zero_data" and evt["action"] == "blocked"
    assert evt["tools_run"] == ["a_tool", "b_tool"]  # sorted
    assert len(evt["draft_preview"]) <= 801  # 800 + ellipsis
    assert len(evt["final_preview"]) <= 801
    assert len(evt["details"]["long"]) <= 301
    assert evt["universe_size"] == 42 and evt["regenerations"] == 2
    json.dumps(evt)  # must be JSON-serializable as-is


def test_claim_and_violation_serializers():
    claims = claims_to_dicts([_FakeClaim("H0", 73.0, "H0 = 73.0 km/s/Mpc")])
    assert claims == [{"label": "H0", "value": 73.0, "raw": "H0 = 73.0 km/s/Mpc"}]
    violations = violations_to_dicts([_FakeViolation("bibcode", "2020A&A...641A...6P", 3)])
    assert violations[0]["kind"] == "bibcode"
    assert claims_to_dicts(None) == [] and violations_to_dicts(None) == []


# ---------- JSONL sink ----------


def test_jsonl_append_and_read_back(monkeypatch, tmp_path):
    from app.config import settings

    target = tmp_path / "sub" / "gate_events.jsonl"  # parent must be auto-created
    monkeypatch.setattr(settings, "gate_events_jsonl_path", str(target))
    for i in range(2):
        append_gate_event_jsonl(build_gate_event(gate=f"g{i}", action="blocked"))
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["gate"] == "g0"
    assert json.loads(lines[1])["gate"] == "g1"


def test_jsonl_empty_path_disables(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "gate_events_jsonl_path", "")
    append_gate_event_jsonl(build_gate_event(gate="g", action="blocked"))
    assert list(tmp_path.iterdir()) == []  # nothing written anywhere visible


def test_jsonl_unwritable_path_never_raises(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "gate_events_jsonl_path", "/dev/null/impossible/x.jsonl")
    append_gate_event_jsonl(build_gate_event(gate="g", action="blocked"))  # must not raise


# ---------- end-to-end through the agent loop ----------


def test_zero_data_block_emits_one_gate_event(monkeypatch, tmp_path):
    """Fake LLM fabricates a number on a zero-tool turn → the zero-data gate
    blocks → exactly one gate_event(zero_data, blocked) reaches both sinks."""
    from app.config import settings

    target = tmp_path / "gate_events.jsonl"
    monkeypatch.setattr(settings, "gate_events_jsonl_path", str(target))

    fabricated = "The Pleiades cluster contains exactly 776 stars at 7.353 mas parallax."

    async def fake_llm(*, tools, **kwargs):
        return {"content": fabricated, "stop_reason": "end_turn", "tool_calls": []}

    collected: list[dict] = []

    async def collector(evt: dict) -> None:
        collected.append(dict(evt))

    orig_llm = chat_mod._llm_messages_create
    chat_mod._llm_messages_create = fake_llm
    try:
        result = asyncio.run(
            chat_mod._run_agent_loop(
                system="test system prompt",
                messages=[{"role": "user", "content": "how many stars are in the Pleiades?"}],
                tools=[],
                provider_api_keys={},
                agent_name="orchestrator",
                python_session_id="gate-event-test",
                on_event=collector,
            )
        )
    finally:
        chat_mod._llm_messages_create = orig_llm

    # The reply itself must be the blocked banner (zero-data hard block).
    assert "776" not in result["reply"] or "withheld" in result["reply"].lower() or "unverified" in result["reply"].lower()

    sse_events = [e for e in collected if e.get("type") == GATE_EVENT_TYPE]
    assert len(sse_events) == 1, sse_events
    evt = sse_events[0]
    assert evt["gate"] == "zero_data"
    assert evt["action"] == "blocked"
    assert evt["draft_preview"]  # pre-block draft captured
    assert "776" in evt["draft_preview"]
    assert evt["agent"] == "orchestrator"

    jsonl_events = [json.loads(line) for line in target.read_text().splitlines()]
    assert len(jsonl_events) == 1
    assert jsonl_events[0]["gate"] == "zero_data"
