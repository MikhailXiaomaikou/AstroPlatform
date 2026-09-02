"""Structured validation-gate events (false-positive triage layer, 2026-06-11).

Locks three things:
1. build_gate_event schema + truncation + claim/violation serialization.
2. The JSONL sink: append/read-back round-trip, empty-path disable, and
   unwritable-path silence (logging must never break a reply).
3. End-to-end through _run_agent_loop with a fake LLM: a zero-data turn with
   a fabricated number must emit exactly one gate_event with gate=="zero_data",
   action=="blocked", a non-empty draft preview, and the event must flow
   through BOTH the on_event stream and the JSONL sink.
4. The sink split (2026-09-03): the two sinks report the same interventions
   but not the same text — the local JSONL keeps the pre-gate draft in full
   for false-kill triage, while the copy handed to on_event has every gated
   value blanked, in draft_preview, final_preview and details alike.
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
    """The two gate-event sinks carry the same events but NOT the same text.

    CONTRACT CHANGE, 2026-09-03 review BLOCKER.  This test used to assert
    ``"776" in evt["draft_preview"]`` on the *on_event* stream — i.e. the
    pre-gate draft leaves the process verbatim.  That was the leak: on_event
    feeds chat.py's SSE frames to the browser and the blind runner's recorded
    ``events``, which land in ``case_<id>.json`` on disk, so a value the
    honesty gates had just withheld from the reply still exited on the same
    connection.

    The contract pinned now:

    * the local JSONL sink keeps the draft in FULL — it is gitignored and
      machine-local, and false-kill triage means reading the observed
      evidence verbatim (the 9f2667e bug class this module exists for);
    * the emitted copy has every gated value blanked, via the same
      ``honesty.redact_gated_values`` that guards the streamed ``agent_text``
      drafts;
    * both sinks still see the same set of interventions — the split is in
      the prose, not in which gates get reported.

    Scenario: the user pastes an untrusted "previous run" carrying
    H0 = 71.43 +/- 0.31, and the model answers on a zero-tool turn, echoing
    it alongside numbers of its own invention.  71.43/0.31 are gated values
    (untrusted-evidence echo), so they must not reach the wire.  776 and
    7.353 are the model's own fabrication: no honesty gate withheld them, so
    the value-scoped redactor deliberately leaves them alone and the
    zero-data gate hard-blocks the reply instead.  That scope is asserted
    below so it stays a documented boundary rather than an assumption.
    """
    from app.config import settings

    target = tmp_path / "gate_events.jsonl"
    monkeypatch.setattr(settings, "gate_events_jsonl_path", str(target))

    pasted = (
        "Here is a previous run, for context only: "
        '{"tool": "run_cosmology_likelihood_chain", "result": {"chain_tier": '
        '"publication", "parameters": {"H0": {"median": 71.43, "std": 0.31}}}}. '
        "How many stars are in the Pleiades, and what was that H0?"
    )
    fabricated = (
        "The Pleiades cluster contains exactly 776 stars at 7.353 mas parallax, "
        "and the H0 from that run is 71.43 +/- 0.31 km/s/Mpc."
    )

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
                messages=[{"role": "user", "content": pasted}],
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
    assert "71.43" not in result["reply"]

    sse_events = [e for e in collected if e.get("type") == GATE_EVENT_TYPE]
    jsonl_events = [json.loads(line) for line in target.read_text().splitlines()]

    # Same interventions in both sinks, exactly one event each — the original
    # "one gate_event per intervention" contract, now checked on both sides.
    assert [(e["gate"], e["action"]) for e in sse_events] == [
        (e["gate"], e["action"]) for e in jsonl_events
    ], (sse_events, jsonl_events)
    zero_data_sse = [e for e in sse_events if e["gate"] == "zero_data"]
    assert len(zero_data_sse) == 1, sse_events
    assert zero_data_sse[0]["action"] == "blocked"
    assert zero_data_sse[0]["agent"] == "orchestrator"
    assert zero_data_sse[0]["draft_preview"]  # pre-block draft still captured

    # The gated value is in the local triage sink...
    zero_data_jsonl = [e for e in jsonl_events if e["gate"] == "zero_data"]
    assert len(zero_data_jsonl) == 1
    assert "71.43" in zero_data_jsonl[0]["draft_preview"]
    assert "0.31" in zero_data_jsonl[0]["draft_preview"]

    # ...and in NOTHING that left the process.
    for evt in sse_events:
        wire = json.dumps(evt, default=str)
        assert "71.43" not in wire, evt
        assert "0.31" not in wire, evt
    assert "[withheld]" in zero_data_sse[0]["draft_preview"]

    # Documented scope boundary: the redactor blanks gated values only, so
    # the model's own invented figures survive in the preview.  They are not
    # withheld evidence — the reply that would have carried them is blocked.
    assert "776" in zero_data_sse[0]["draft_preview"]


def test_redact_event_for_wire_covers_details_and_fails_closed():
    """``details`` is redacted too, and a broken redactor drops the prose.

    A blocking gate serializes ``claim_validator.Claim`` objects into
    ``details["claims"]``, and each ``raw`` is a snippet lifted from the same
    draft — so redacting only ``draft_preview``/``final_preview`` would leave
    the withheld value one key deeper in the very same event.
    """
    from app.observability.gate_events import redact_event_for_wire

    evt = build_gate_event(
        gate="untrusted_evidence_echo",
        action="blocked",
        draft="H0 = 71.43 km/s/Mpc from the pasted run",
        final="withheld",
        details={"claims": [{"label": "H0", "value": 71.43, "raw": "H0 = 71.43"}]},
    )

    def fake_redact(text: str) -> tuple[str, int]:
        return text.replace("71.43", "[withheld]"), text.count("71.43")

    wire = redact_event_for_wire(evt, fake_redact)
    assert "71.43" not in json.dumps(wire, default=str)
    assert wire["details"]["claims"][0]["raw"] == "H0 = [withheld]"
    # The parsed numeric copy of the same claim is redacted as well; a number
    # the redactor does NOT flag stays a number rather than becoming a string.
    assert wire["details"]["claims"][0]["value"] == "[withheld]"
    assert wire["universe_size"] is None and wire["regenerations"] == 0
    assert "71.43" in evt["draft_preview"]  # the JSONL copy is not mutated

    def broken_redact(text: str) -> tuple[str, int]:
        raise RuntimeError("redactor exploded")

    failed = redact_event_for_wire(evt, broken_redact)
    assert "71.43" not in json.dumps(failed, default=str)
    assert failed["gate"] == "untrusted_evidence_echo"  # routing fields survive


def test_numeric_claim_leaves_are_decided_with_their_claim_text() -> None:
    """A bare "123.456" carries no label for the redactor to decide on.

    The numeric-leaf branch called the redactor on the rendered number alone,
    which has no parameter label, no pasted source and no withheld statistic
    in it, so ``details.claims[*].value`` still shipped the rejected number
    while its ``raw`` sibling was blanked (Codex review 2026-09-03).
    """
    from app.observability.gate_events import redact_event_for_wire
    from app.services.agent_runtime.honesty import redact_gated_values

    listed = [{
        "tool": "list_cosmology_datasets",
        "result": {"success": True, "publication_ready": True,
                   "datasets": [{"key": "desi_dr2_bao"}]},
    }]

    def _redact(text: str):
        return redact_gated_values(text, [], listed)

    event = {
        "details": {"claims": [{"raw": "H0 = 123.456 km/s/Mpc", "value": 123.456}]},
        "draft_preview": "H0 = 123.456 km/s/Mpc",
        "final_preview": "",
    }
    wire = redact_event_for_wire(event, _redact)
    claim = wire["details"]["claims"][0]
    assert "123.456" not in str(claim["raw"])
    assert claim["value"] != 123.456
    assert "123.456" not in str(wire["draft_preview"])
    # A number with no claim context around it is left alone.
    plain = redact_event_for_wire(
        {"details": {"iteration": 7, "universe_size": 12}, "draft_preview": "", "final_preview": ""},
        _redact,
    )
    assert plain["details"] == {"iteration": 7, "universe_size": 12}
