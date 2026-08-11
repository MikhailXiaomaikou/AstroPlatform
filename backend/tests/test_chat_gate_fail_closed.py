"""Audit 2026-07-03 — chat.py gate orchestration must fail CLOSED.

Locks five behaviors of the reply-gate plumbing in app/api/chat.py:

1. The <tools_returned_nothing/> abstention card must NOT ship
   model-authored numeric claims under the '✓ Honest reply' banner.
   The card path skips the claim validator by design, so its
   model-authored attributes (rationale / suggested_next_step / ...)
   must be dropped when they contain numeric claims — Pleiades F1.1
   class smuggled through the honesty card.
2. The numeric-claims gate must fail CLOSED when the regeneration LLM
   call raises or returns empty: a draft that already FAILED
   validate_claims must never ship unmarked (previously both `break`
   paths skipped the block and emitted only a telemetry event).
3. The tool-inventory meta-skip ("what tools ...") must not disable the
   numeric gate on data-bearing turns — the skip is only honored when
   the turn produced no claimable tool data.
4. _slim_tool_result_for_sse must preserve the provenance block
   (datasets / field_bibcodes / reproducibility) when slimming >8 KB
   results; only bulky per-row payloads are trimmed/bounded.
5. hit_iteration_cap must cross the API boundary: final SSE text event
   and ChatResponse both carry the boolean.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.api import chat as chat_mod


def _ok_parallax_result(tool_call: dict) -> dict:
    """A successful data-bearing tool result: universe = {5.0, 1}."""
    return {
        "id": tool_call["id"],
        "name": tool_call["name"],
        "input": tool_call["input"],
        "result": {
            "success": True,
            "rows": [{"target": "HD 12345", "parallax": 5.0}],
            "row_count": 1,
            "columns": ["target", "parallax"],
            "analysis_status": "COMPLETED",
            "data_origin": "archive",
        },
    }


def _failed_adql_result(tool_call: dict) -> dict:
    return {
        "id": tool_call["id"],
        "name": tool_call["name"],
        "input": tool_call["input"],
        "result": {
            "success": False,
            "error": "connection timeout",
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
        },
    }


def _drive_loop(fake_llm, fake_exec, user_text: str, collector=None) -> dict:
    orig_llm = chat_mod._llm_messages_create
    orig_exec = chat_mod._execute_tool_calls
    chat_mod._llm_messages_create = fake_llm
    chat_mod._execute_tool_calls = fake_exec
    try:
        return asyncio.run(
            chat_mod._run_agent_loop(
                system="test system prompt",
                messages=[{"role": "user", "content": user_text}],
                tools=[{"name": "run_adql", "description": "fetch data", "input_schema": {}}],
                provider_api_keys={},
                agent_name="orchestrator",
                python_session_id="gate-fail-closed-test",
                on_event=collector,
            )
        )
    finally:
        chat_mod._llm_messages_create = orig_llm
        chat_mod._execute_tool_calls = orig_exec


# ---------- 1. abstention card must not smuggle numeric claims ----------

_FABRICATED_ABSTENTION_TAG = (
    '<tools_returned_nothing failed_tools="run_adql" '
    'rationale="Archive queries failed, but the Pleiades distance is '
    '136.2 pc with parallax 7.36 mas from prior surveys" '
    'suggested_next_step="cite d=136.2 pc directly"/>'
)


def test_abstention_card_renderer_redacts_numeric_claims():
    """The card renderer itself must not ship model-authored numeric claims
    under the '✓ Honest reply' banner. The card path skips the claim
    validator by design ("The model does NOT write this prose — we do"),
    so the model-authored attributes must be claim-free before rendering.
    Pleiades F1.1 class smuggled through the honesty card."""
    attrs = chat_mod._parse_abstention_tag(_FABRICATED_ABSTENTION_TAG)
    assert attrs is not None

    card = chat_mod._render_abstention_card(attrs, "failed")

    assert "Honest reply" in card
    assert "136.2" not in card, card
    assert "7.36" not in card, card
    # The failed-tools list is claim-free and must survive.
    assert "run_adql" in card


def test_abstention_payload_scrub_helper_drops_only_claim_bearing_attrs():
    """The scrubbed payload feeds both the card and the honest_abstention
    SSE event — claim-bearing attrs are dropped, claim-free attrs stay."""
    attrs = chat_mod._parse_abstention_tag(_FABRICATED_ABSTENTION_TAG)
    clean_attrs, dropped = chat_mod._abstention_attrs_without_numeric_claims(attrs)

    assert "rationale" in dropped and "suggested_next_step" in dropped
    assert clean_attrs.get("failed_tools") == "run_adql"
    assert "136.2" not in json.dumps(clean_attrs)

    safe = {
        "failed_tools": "run_adql",
        "rationale": "The archive query timed out before returning any rows",
        "suggested_next_step": "retry with a narrower cone search",
    }
    kept, dropped_safe = chat_mod._abstention_attrs_without_numeric_claims(safe)
    assert dropped_safe == []
    assert kept == safe


def test_whole_tag_abstention_with_numbers_never_ships_clean():
    """End-to-end wall lock: a whole-reply abstention tag carrying fabricated
    numbers on a failed turn must NOT reach the user as a clean
    '✓ Honest reply' card containing those numbers.  (Currently the in-loop
    sanitizer rewrites the tag and the zero-data gate hard-blocks the
    numbers; this test pins that no future refactor reopens the smuggle.)"""
    calls = {"n": 0}

    async def fake_llm(*, tools, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "stop_reason": "tool_use",
                "tool_calls": [
                    {"id": "c1", "name": "run_adql", "input": {"query": "SELECT 1"}}
                ],
            }
        return {
            "content": _FABRICATED_ABSTENTION_TAG,
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_exec(real_tool_calls, *args, **kwargs):
        return [_failed_adql_result(tc) for tc in real_tool_calls]

    events: list[dict] = []

    async def collector(evt: dict) -> None:
        events.append(dict(evt))

    result = _drive_loop(
        fake_llm, fake_exec,
        "what is the distance to the Pleiades?",
        collector=collector,
    )

    reply = result["reply"]
    # Either path is acceptable — hard block, or an abstention card with the
    # numbers redacted — but a clean 'Honest reply' banner over fabricated
    # numbers is not.
    if "Honest reply" in reply:
        assert "136.2 pc" not in reply, reply
        assert "7.36 mas" not in reply, reply
    else:
        assert "withheld" in reply.lower() or "unverified" in reply.lower(), reply
    # No honest_abstention SSE payload may carry the fabricated values.
    abstention_events = [e for e in events if e.get("type") == "honest_abstention"]
    payload_json = json.dumps(abstention_events)
    assert "136.2" not in payload_json, payload_json


# ---------- 2. numeric gate fails closed on regen-call failure ----------


@pytest.mark.parametrize("regen_behavior", ["raises", "empty"])
def test_numeric_gate_fails_closed_when_regen_call_fails(regen_behavior):
    """A draft that FAILED validate_claims must not ship unmarked when the
    regeneration call errors (429/timeout) or returns empty prose."""
    draft = "The archive query returned a parallax of 7.353 mas for the target."
    calls = {"n": 0}

    async def fake_llm(*, tools, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "stop_reason": "tool_use",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "run_adql",
                        "input": {"query": "SELECT parallax FROM stars"},
                    }
                ],
            }
        if calls["n"] == 2:
            return {"content": draft, "stop_reason": "end_turn", "tool_calls": []}
        # Regeneration call (tools=[]): simulate provider failure / empty prose.
        if regen_behavior == "raises":
            raise RuntimeError("simulated provider 429 during regeneration")
        return {"content": "", "stop_reason": "end_turn", "tool_calls": []}

    async def fake_exec(real_tool_calls, *args, **kwargs):
        return [_ok_parallax_result(tc) for tc in real_tool_calls]

    events: list[dict] = []

    async def collector(evt: dict) -> None:
        events.append(dict(evt))

    result = _drive_loop(
        fake_llm, fake_exec,
        "fetch the parallax of HD 12345 from the archive",
        collector=collector,
    )

    reply = result["reply"]
    # The fabricated sentence must never ship verbatim as normal prose.
    assert draft not in reply, reply
    # The user must see the hard block: banner + in-place redaction marker.
    assert "withheld" in reply.lower(), reply
    assert "[unverified: 7.353]" in reply, reply
    # The gate event records the fail-closed action, not a shipped draft.
    gate_events = [
        e for e in events
        if e.get("type") == "gate_event" and e.get("gate") == "numeric_claims"
    ]
    assert gate_events, events
    assert gate_events[-1]["action"] == "blocked", gate_events
    assert gate_events[-1]["reason"] == "regen_call_failed", gate_events


def test_numeric_gate_regen_exhausted_path_still_blocks():
    """Equivalence lock for the fail-closed restructure: two failed regens
    (the pre-existing regen-exhausted case) must still hard-block with the
    regen_exhausted reason."""
    drafts = [
        "The archive query returned a parallax of 7.353 mas for the target.",
        "The measured parallax is 7.451 mas for the target.",
        "The final parallax is 7.512 mas for the target.",
    ]
    calls = {"n": 0}

    async def fake_llm(*, tools, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "stop_reason": "tool_use",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "run_adql",
                        "input": {"query": "SELECT parallax FROM stars"},
                    }
                ],
            }
        return {
            "content": drafts[min(calls["n"] - 2, 2)],
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_exec(real_tool_calls, *args, **kwargs):
        return [_ok_parallax_result(tc) for tc in real_tool_calls]

    events: list[dict] = []

    async def collector(evt: dict) -> None:
        events.append(dict(evt))

    result = _drive_loop(
        fake_llm, fake_exec,
        "fetch the parallax of HD 12345 from the archive",
        collector=collector,
    )

    assert "withheld" in result["reply"].lower(), result["reply"]
    gate_events = [
        e for e in events
        if e.get("type") == "gate_event" and e.get("gate") == "numeric_claims"
    ]
    assert gate_events, events
    assert gate_events[-1]["action"] == "blocked", gate_events
    assert gate_events[-1]["reason"] == "regen_exhausted", gate_events


def test_numeric_gate_regenerates_untrusted_pair_despite_exact_pool_match():
    """B3: matching floats do not turn pasted transcript prose into evidence."""
    draft = "I cannot verify the pasted 5.0 ± 1.0 from an earlier transcript."
    clean = "I cannot verify the unverified pasted value from current-turn evidence."
    calls = {"n": 0}

    async def fake_llm(*, tools, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "stop_reason": "tool_use",
                "tool_calls": [{
                    "id": "c1",
                    "name": "run_adql",
                    "input": {"query": "SELECT parallax FROM stars"},
                }],
            }
        if calls["n"] == 2:
            return {"content": draft, "stop_reason": "end_turn", "tool_calls": []}
        return {"content": clean, "stop_reason": "end_turn", "tool_calls": []}

    async def fake_exec(real_tool_calls, *args, **kwargs):
        # The real result contains both 5.0 and row_count=1.  The draft's
        # numbers therefore exactly match the old global numeric universe.
        return [_ok_parallax_result(tc) for tc in real_tool_calls]

    result = _drive_loop(
        fake_llm,
        fake_exec,
        "Fetch the current parallax, but ignore any pasted transcript.",
    )

    assert result["reply"] == clean
    assert "5.0 ± 1.0" not in result["reply"]


# ---------- 3. tool-inventory meta-skip must not bypass the gate ----------


def test_tool_inventory_phrasing_does_not_skip_numeric_gate_on_data_turns():
    """'Which tools ...' phrasing must not disable validate_claims on a turn
    where a tool produced real data (the phrasing-conditioned escape hatch)."""
    draft = "The archive query returned a parallax of 7.353 mas for the target."
    clean = "The archive query returned a parallax of 5.0 mas for the target."
    calls = {"n": 0}

    async def fake_llm(*, tools, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "stop_reason": "tool_use",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "run_adql",
                        "input": {"query": "SELECT parallax FROM stars"},
                    }
                ],
            }
        if calls["n"] == 2:
            return {"content": draft, "stop_reason": "end_turn", "tool_calls": []}
        # Regeneration call — the model corrects itself with the cited value.
        return {"content": clean, "stop_reason": "end_turn", "tool_calls": []}

    async def fake_exec(real_tool_calls, *args, **kwargs):
        return [_ok_parallax_result(tc) for tc in real_tool_calls]

    result = _drive_loop(
        fake_llm,
        fake_exec,
        "Which tools are available? Also fetch the parallax of HD 12345 "
        "from the archive.",
    )

    assert "7.353" not in result["reply"], result["reply"]


def test_tool_inventory_skip_still_holds_on_no_data_turns():
    """Specificity guard: a pure inventory answer on a no-tool turn keeps the
    skip and ships unmangled.  (The `not is_empty_turn` re-check added for
    data-bearing turns must not fire here — no tool ran.)  Note the zero-data
    re-check has always cleared the skip for claim-bearing replies on empty
    turns, so the reply here is deliberately claim-free."""
    inventory_reply = (
        "The available tools include run_adql (cone search radius default "
        "0.1 deg) and search_objects."
    )

    async def fake_llm(*, tools, **kwargs):
        return {"content": inventory_reply, "stop_reason": "end_turn", "tool_calls": []}

    async def fake_exec(real_tool_calls, *args, **kwargs):  # pragma: no cover
        raise AssertionError("no tool should run on this turn")

    result = _drive_loop(fake_llm, fake_exec, "Which tools are available?")

    assert result["reply"] == inventory_reply


# ---------- 4. qualitative scientific conclusions share one final gate ----------


def test_single_agent_final_boundary_blocks_uncalibrated_headline_conclusion():
    draft = "The Hubble tension is resolved."

    async def fake_llm(*, tools, **kwargs):
        return {"content": draft, "stop_reason": "end_turn", "tool_calls": []}

    async def fake_exec(real_tool_calls, *args, **kwargs):  # pragma: no cover
        raise AssertionError("no tool should run on this turn")

    events: list[dict] = []

    async def collector(evt: dict) -> None:
        events.append(dict(evt))

    result = _drive_loop(
        fake_llm,
        fake_exec,
        "Give a qualitative cosmology conclusion.",
        collector=collector,
    )

    assert draft not in result["reply"]
    assert "Scientific conclusion withheld" in result["reply"]
    assert result["validation_summary"]["blocked"] is True
    assert any(
        event.get("gate") == "scientific_conclusion_scope"
        and event.get("action") == "blocked"
        for event in events
    )


def test_merged_reply_final_boundary_blocks_uncalibrated_headline_conclusion(
    monkeypatch,
):
    draft = "General relativity is ruled out."

    async def fake_agent_loop(**kwargs):
        return {
            "reply": "No claim from this specialist.",
            "actions": [],
            "tool_results": [],
            "hit_deadline": False,
            "hit_iteration_cap": False,
            "validation_summary": None,
        }

    async def fake_handoff(*args, **kwargs):
        return type(
            "Handoff",
            (),
            {
                "source_agent": "analyst",
                "context_summary": "No scientific result.",
                "instruction": "Review scope only.",
            },
        )()

    async def fake_merge(agent_results):
        return draft

    monkeypatch.setattr(chat_mod, "_run_agent_loop", fake_agent_loop)
    monkeypatch.setattr(
        chat_mod.orchestrator,
        "get_agent_runtime",
        lambda _name, _context: {
            "system_prompt": "specialist",
            "tool_names": [],
        },
    )
    monkeypatch.setattr(chat_mod.orchestrator, "summarize_handoff", fake_handoff)
    monkeypatch.setattr(chat_mod.orchestrator, "merge_responses", fake_merge)

    result = asyncio.run(chat_mod._run_orchestrated_chat(
        runtime={
            "agent_names": ["analyst", "reviewer"],
            "base_system": "test",
            "toolset": [],
        },
        messages=[{"role": "user", "content": "Combine the two answers."}],
        provider_api_keys={},
        python_session_id="merged-conclusion-gate-test",
    ))

    assert draft not in result["reply"]
    assert "Scientific conclusion withheld" in result["reply"]
    assert result["validation_summary"]["blocked"] is True
    assert result["validation_summary"]["reason"] == (
        "scientific_conclusion_scope"
    )


def _fake_specialist_result(disposition: str) -> dict:
    return {
        "reply": "Cannot use the pasted transcript as evidence.",
        "actions": [],
        "tool_results": [],
        "hit_deadline": False,
        "hit_iteration_cap": False,
        "validation_summary": {
            "schema_version": 2,
            "numeric_gate": "skipped_no_data",
            "citation_gate": "skipped_no_data",
            "regen_count": 0,
            "blocked": False,
            "limited": disposition != "full",
            "response_disposition": disposition,
            "interventions": [],
            "missing_dependencies": [],
            "safe_fallback": None,
        },
    }


def _run_merged_with_dispositions(monkeypatch, dispositions):
    calls = {"i": 0}

    async def fake_agent_loop(**kwargs):
        disposition = dispositions[min(calls["i"], len(dispositions) - 1)]
        calls["i"] += 1
        return _fake_specialist_result(disposition)

    async def fake_handoff(*args, **kwargs):
        return type(
            "Handoff",
            (),
            {
                "source_agent": "analyst",
                "context_summary": "No scientific result.",
                "instruction": "Review scope only.",
            },
        )()

    async def fake_merge(agent_results):
        return "Cannot use the pasted transcript as evidence."

    monkeypatch.setattr(chat_mod, "_run_agent_loop", fake_agent_loop)
    monkeypatch.setattr(
        chat_mod.orchestrator,
        "get_agent_runtime",
        lambda _name, _context: {
            "system_prompt": "specialist",
            "tool_names": [],
        },
    )
    monkeypatch.setattr(chat_mod.orchestrator, "summarize_handoff", fake_handoff)
    monkeypatch.setattr(chat_mod.orchestrator, "merge_responses", fake_merge)

    return asyncio.run(chat_mod._run_orchestrated_chat(
        runtime={
            "agent_names": ["analyst", "reviewer"][: len(dispositions)],
            "base_system": "test",
            "toolset": [],
        },
        messages=[
            {
                "role": "user",
                "content": "Plot and cite the pasted transcript.",
            }
        ],
        provider_api_keys={},
        python_session_id="merged-disposition-test",
    ))


def test_merged_all_refusal_members_keep_refusal_disposition(monkeypatch):
    result = _run_merged_with_dispositions(monkeypatch, ["refusal", "refusal"])
    summary = result["validation_summary"]
    assert summary["response_disposition"] == "refusal"


def test_merged_refusal_plus_abstention_propagates_refusal(monkeypatch):
    result = _run_merged_with_dispositions(
        monkeypatch, ["refusal", "abstention"]
    )
    summary = result["validation_summary"]
    assert summary["response_disposition"] == "refusal"


def test_merged_mixed_refusal_and_full_stays_limited(monkeypatch):
    result = _run_merged_with_dispositions(monkeypatch, ["refusal", "full"])
    summary = result["validation_summary"]
    assert summary["response_disposition"] == "limited"


# ---------- 5. SSE slimming preserves the provenance block ----------


def test_slim_tool_result_preserves_provenance_and_reproducibility():
    """A >8 KB tool result must keep provenance (datasets / field_bibcodes /
    reproducibility envelope) on the SSE wire; only per-row payloads shrink."""
    rows = [
        {"target": f"obj{i}", "plx_value": 5.0 + i, "plx_bibcode": "2018A&A...616A...1G"}
        for i in range(400)
    ]
    unique_extra = [f"2020ApJ...900..{i:03d}X" for i in range(80)]
    provenance = {
        "datasets": [
            {"dataset_id": "gaia_dr3", "archive": "GAIA", "bibcode": "2023A&A...674A...1G"}
        ],
        "field_bibcodes": {
            "columns": {"plx_bibcode": ["2018A&A...616A...1G"] * 400 + unique_extra},
            "mapping": {"plx_bibcode": "plx_value"},
            "source_column_pattern": "*_bibcode",
        },
        "coverage": {"field_level": {"available": True}},
        "reproducibility": {
            "run_id": "r1",
            "query_hash": "abc123",
            "archive_version": "gaia_dr3",
        },
    }
    result = {
        "success": True,
        "row_count": 400,
        "columns": ["target", "plx_value", "plx_bibcode"],
        "rows": rows,
        "provenance": provenance,
        "reproducibility": {
            "run_id": "r1",
            "query_hash": "abc123",
            "archive_version": "gaia_dr3",
        },
    }
    assert len(json.dumps(result)) > 8000  # sanity: slimming must engage

    slim = chat_mod._slim_tool_result_for_sse(result)

    assert slim.get("__preview__") is True
    assert "rows" not in slim and "rows__preview__" in slim
    prov = slim.get("provenance")
    assert isinstance(prov, dict), "provenance block dropped from SSE frame"
    assert prov.get("datasets") == provenance["datasets"]
    assert prov.get("reproducibility", {}).get("archive_version") == "gaia_dr3"
    cols = (prov.get("field_bibcodes") or {}).get("columns") or {}
    kept = cols.get("plx_bibcode") or []
    assert "2018A&A...616A...1G" in kept
    # Per-row duplicate bibcodes must be deduped/bounded on the wire.
    assert len(kept) <= 50, f"unbounded field_bibcodes list: {len(kept)} entries"
    # Top-level reproducibility envelope survives too.
    assert slim.get("reproducibility", {}).get("run_id") == "r1"
    # The slimmed frame itself stays small.
    assert len(json.dumps(slim, default=str)) < 8000
    # The source result must NOT be mutated (it is shared with actions /
    # session persistence).
    assert len(result["provenance"]["field_bibcodes"]["columns"]["plx_bibcode"]) == 480


# ---------- 6. hit_iteration_cap crosses the API boundary ----------


def test_chat_response_model_exposes_hit_iteration_cap():
    resp = chat_mod.ChatResponse(reply="x", actions=[])
    assert resp.hit_iteration_cap is False
    resp_capped = chat_mod.ChatResponse(reply="x", actions=[], hit_iteration_cap=True)
    assert resp_capped.hit_iteration_cap is True


async def test_stream_final_text_event_carries_hit_iteration_cap(app_client, monkeypatch):
    """M7 follow-through: the flag is computed 'so the UI can surface it' —
    it must actually reach the wire in the final SSE text event."""

    async def fake_build_runtime(req, user, db):
        return {"agent_names": ["orchestrator"], "toolset": [], "system": "test system"}

    async def fake_run_orchestrated_chat(**kwargs):
        return {
            "reply": "partial answer (budget exhausted)",
            "actions": [],
            "tool_results": [],
            "hit_iteration_cap": True,
            "hit_deadline": False,
        }

    monkeypatch.setattr("app.api.chat._build_runtime", fake_build_runtime)
    monkeypatch.setattr("app.api.chat._run_orchestrated_chat", fake_run_orchestrated_chat)

    resp = await app_client.post(
        "/api/chat/message/stream",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "context": {
                "api_provider": "local",
                "model_profile": "local:openai-cli",
                "python_session_id": "hit-cap-test",
                "current_session_id": None,
            },
        },
    )

    assert resp.status_code == 200
    frames = [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ") and line[len("data: "):].strip()
    ]
    text_frames = [f for f in frames if f.get("type") == "text"]
    assert text_frames, frames
    assert text_frames[-1].get("hit_iteration_cap") is True, text_frames
