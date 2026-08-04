"""Per-reply validation_summary surfacing (2026-07-03 honesty-visibility work).

The gate stack already computed everything (fabrication_stats, gate_event
records) but discarded it at the API boundary — a gate-passed reply looked
identical to a gate-skipped one in the UI.  These tests lock the read-only
surfacing layer:

1. _derive_validation_summary maps existing gate state to the compact
   per-gate vocabulary (passed / regenerated / blocked / skipped_no_data /
   skipped) without inventing new validation logic.
2. _run_agent_loop attaches validation_summary on its return paths, with
   honest states: a fabricated-number block reports blocked; a clean
   data-grounded reply reports passed; a qualitative no-tool reply reports
   skipped_no_data (never "passed" — there was no tool data to validate
   against).
3. Both API endpoints thread the summary through (the hit_iteration_cap
   pattern): final SSE text frame and ChatResponse.
4. JOB 3 side: _execute_tool_calls registers each tool result's
   reproducibility envelope in the provenance ledger so
   /api/provenance/{run_id}/* can answer for run_ids users actually see.
"""
from __future__ import annotations

import asyncio
import json

from app.api import chat as chat_mod
from app.services.agent_runtime.loop import (
    _derive_validation_summary,
    _not_run_validation_summary,
)


# ---------- 1. state-mapping unit tests ----------


def test_derive_summary_passed_states():
    summary = _derive_validation_summary(
        claim_gate_ran=True,
        gate_skip_reason=None,
        fabrication_stats={"pass": 1, "blocked": False, "regenerations": 0},
        interventions=[],
        tool_results=[{"tool": "run_adql", "result": {
            "success": True, "rows": [{"parallax": 5.0}], "row_count": 1,
            "analysis_status": "COMPLETED",
        }}],
    )
    assert summary["numeric_gate"] == "passed"
    assert summary["citation_gate"] == "passed"
    assert summary["regen_count"] == 0
    assert summary["blocked"] is False
    assert summary["interventions"] == []
    json.dumps(summary)  # wire-serializable


def test_derive_summary_no_data_turn_never_reports_numeric_pass():
    """HONESTY RULE: with no claimable tool data there is nothing to
    validate numeric claims against — 'passed' would overstate."""
    summary = _derive_validation_summary(
        claim_gate_ran=True,
        gate_skip_reason=None,
        fabrication_stats={"pass": 0, "blocked": False, "regenerations": 0},
        interventions=[],
        tool_results=[],
    )
    assert summary["numeric_gate"] == "skipped_no_data"
    # The citation-provenance check genuinely runs on empty turns.
    assert summary["citation_gate"] == "passed"


def test_derive_summary_blocked_and_regenerated_families():
    blocked = _derive_validation_summary(
        claim_gate_ran=True,
        gate_skip_reason=None,
        fabrication_stats={"pass": 0, "blocked": True, "regenerations": 2},
        interventions=[
            {"gate": "numeric_claims", "action": "blocked", "reason": "regen_exhausted"},
            {"gate": "citation_methodology", "action": "blocked", "reason": ""},
        ],
        tool_results=[{"tool": "run_adql", "result": {"success": True, "rows": [{"x": 1}]}}],
    )
    assert blocked["numeric_gate"] == "blocked"
    assert blocked["citation_gate"] == "blocked"
    assert blocked["blocked"] is True
    assert blocked["response_disposition"] == "hard_block"
    assert blocked["regen_count"] == 2

    regenerated = _derive_validation_summary(
        claim_gate_ran=True,
        gate_skip_reason=None,
        fabrication_stats={"pass": 2, "blocked": False, "regenerations": 1},
        interventions=[
            {"gate": "numeric_claims", "action": "regenerated_clean", "reason": ""},
            {"gate": "cosmology_anchor", "action": "downgraded_summary", "reason": ""},
        ],
        tool_results=[{"tool": "run_adql", "result": {"success": True, "rows": [{"x": 1}]}}],
    )
    assert regenerated["numeric_gate"] == "regenerated"
    assert regenerated["citation_gate"] == "regenerated"
    assert regenerated["blocked"] is False


def test_derive_summary_limited_is_not_a_hard_block():
    limited = _derive_validation_summary(
        claim_gate_ran=True,
        gate_skip_reason=None,
        fabrication_stats={
            "pass": 1,
            "blocked": False,
            "limited": True,
            "regenerations": 0,
        },
        interventions=[{
            "gate": "citation_methodology",
            "action": "annotated_limited",
            "reason": "unsupported_inline_citation",
        }],
        tool_results=[{
            "tool": "run_adql",
            "result": {"success": True, "rows": [{"x": 1}]},
        }],
    )

    assert limited["numeric_gate"] == "passed"
    assert limited["citation_gate"] == "limited"
    assert limited["blocked"] is False
    assert limited["limited"] is True
    assert limited["response_disposition"] == "limited"


def test_hard_gate_overrides_full_scalar_receipt_disposition():
    summary = _derive_validation_summary(
        claim_gate_ran=True,
        gate_skip_reason=None,
        fabrication_stats={
            "pass": 0,
            "blocked": True,
            "limited": False,
            "regenerations": 2,
        },
        interventions=[
            {"gate": "numeric_claims", "action": "blocked", "reason": "regen_exhausted"}
        ],
        tool_results=[
            {
                "tool": "verify_scalar_derivation",
                "result": {
                    "response_disposition": "full",
                    "source_status": "verified_exact",
                },
            }
        ],
        routing_decision={"task_kind": "deterministic_source_check"},
    )

    assert summary["blocked"] is True
    assert summary["response_disposition"] == "hard_block"


def test_safe_refusal_is_visible_even_when_no_gate_had_to_block_it():
    summary = _derive_validation_summary(
        claim_gate_ran=True,
        gate_skip_reason=None,
        fabrication_stats={"pass": 0, "blocked": False, "regenerations": 0},
        interventions=[],
        tool_results=[],
        routing_decision={
            "task_kind": "general",
            "matched_signals": ["untrusted_evidence_request"],
        },
    )

    assert summary["blocked"] is False
    assert summary["response_disposition"] == "refusal"


def test_derive_summary_meta_skip_and_not_run_are_distinct_from_passed():
    skipped = _derive_validation_summary(
        claim_gate_ran=False,
        gate_skip_reason="tool_inventory_meta",
        fabrication_stats={"pass": 0, "blocked": False, "regenerations": 0},
        interventions=[],
        tool_results=[],
    )
    assert skipped["numeric_gate"] == "skipped"
    assert skipped["citation_gate"] == "skipped"
    assert skipped["reason"] == "tool_inventory_meta"

    not_run = _not_run_validation_summary("loop_deadline")
    assert not_run["numeric_gate"] == "not_run"
    assert not_run["citation_gate"] == "not_run"
    assert not_run["reason"] == "loop_deadline"
    assert "passed" not in (not_run["numeric_gate"], not_run["citation_gate"])


def test_full_research_nonpublication_summary_is_limited_and_actionable():
    prompt = (
        "Run the full DESI DR2 early-dark-energy posterior with Planck high-l "
        "and low-l likelihoods and a production sampler."
    )
    summary = _derive_validation_summary(
        claim_gate_ran=True,
        gate_skip_reason=None,
        fabrication_stats={
            "pass": 0,
            "blocked": False,
            "limited": True,
            "regenerations": 1,
        },
        interventions=[{
            "gate": "nonpublication_posterior",
            "action": "annotated_limited",
            "reason": "posterior_values_withheld",
        }],
        tool_results=[],
        routing_decision={"task_kind": "full_research"},
        user_prompt=prompt,
    )

    assert summary["task_kind"] == "full_research"
    assert summary["response_disposition"] == "limited"
    assert summary["limited"] is True
    assert summary["earliest_limiting_stage"] == "nonpublication_posterior"
    assert any("EDE" in item for item in summary["missing_dependencies"])
    assert any("Planck high-l" in item for item in summary["missing_dependencies"])
    assert any("sampler" in item for item in summary["missing_dependencies"])


def test_full_research_honest_abstention_preserves_route_as_limited_gap():
    summary = _not_run_validation_summary(
        "honest_abstention",
        {"task_kind": "full_research"},
        "Run an EDE posterior with DESI DR2, Planck, and a production sampler.",
    )

    assert summary["task_kind"] == "full_research"
    assert summary["response_disposition"] == "limited"
    assert summary["limited"] is True
    assert any("DESI DR2" in item for item in summary["missing_dependencies"])


def test_derive_summary_interventions_override_meta_skip():
    """The empty-model-reply path skips the main gate block but the fallback
    synthesis still validates what it ships — an intervention recorded there
    must win over the 'skipped' label."""
    summary = _derive_validation_summary(
        claim_gate_ran=False,
        gate_skip_reason="empty_model_reply",
        fabrication_stats={"pass": 0, "blocked": True, "regenerations": 0},
        interventions=[
            {"gate": "empty_reply_fallback", "action": "blocked", "reason": "fallback_synthesis"},
        ],
        tool_results=[],
    )
    assert summary["numeric_gate"] == "blocked"
    assert summary["blocked"] is True


# ---------- 2. agent-loop return paths ----------


def _drive_loop(fake_llm, fake_exec, user_text: str) -> dict:
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
                python_session_id="validation-summary-test",
            )
        )
    finally:
        chat_mod._llm_messages_create = orig_llm
        chat_mod._execute_tool_calls = orig_exec


def test_loop_zero_data_block_reports_blocked_summary():
    fabricated = "The Pleiades cluster contains exactly 776 stars at 7.353 mas parallax."

    async def fake_llm(*, tools, **kwargs):
        return {"content": fabricated, "stop_reason": "end_turn", "tool_calls": []}

    async def fake_exec(*args, **kwargs):  # pragma: no cover - no tools run
        raise AssertionError("no tool should run")

    result = _drive_loop(fake_llm, fake_exec, "how many stars are in the Pleiades?")

    summary = result["validation_summary"]
    assert summary["numeric_gate"] == "blocked"
    assert summary["blocked"] is True
    assert any(i["gate"] == "zero_data" for i in summary["interventions"])


def test_loop_clean_data_grounded_reply_reports_passed():
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
        return {
            "content": "The archive query returned a parallax of 5.0 mas for the target.",
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_exec(real_tool_calls, *args, **kwargs):
        return [{
            "id": tc["id"],
            "name": tc["name"],
            "input": tc["input"],
            "result": {
                "success": True,
                "rows": [{"target": "HD 12345", "parallax": 5.0}],
                "row_count": 1,
                "columns": ["target", "parallax"],
                "analysis_status": "COMPLETED",
                "data_origin": "archive",
            },
        } for tc in real_tool_calls]

    result = _drive_loop(
        fake_llm, fake_exec, "fetch the parallax of HD 12345 from the archive",
    )

    summary = result["validation_summary"]
    assert summary["numeric_gate"] == "passed", summary
    assert summary["citation_gate"] == "passed", summary
    assert summary["blocked"] is False
    assert summary["regen_count"] == 0


def test_loop_qualitative_no_tool_reply_reports_skipped_no_data():
    async def fake_llm(*, tools, **kwargs):
        return {
            "content": (
                "Dark energy remains an open problem; the platform can run a "
                "likelihood chain against registered datasets if you ask."
            ),
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_exec(*args, **kwargs):  # pragma: no cover - no tools run
        raise AssertionError("no tool should run")

    result = _drive_loop(fake_llm, fake_exec, "what is dark energy?")

    summary = result["validation_summary"]
    assert summary["numeric_gate"] == "skipped_no_data", summary
    assert summary["blocked"] is False


# ---------- 3. API boundary (both endpoints) ----------


def test_chat_response_model_accepts_optional_validation_summary():
    resp = chat_mod.ChatResponse(reply="x", actions=[])
    assert resp.validation_summary is None  # backward compatible default
    payload = {"schema_version": 1, "numeric_gate": "passed", "citation_gate": "passed",
               "regen_count": 0, "blocked": False, "interventions": []}
    resp2 = chat_mod.ChatResponse(reply="x", actions=[], validation_summary=payload)
    assert resp2.validation_summary == payload


async def test_nonstream_endpoint_returns_validation_summary(app_client, test_user, monkeypatch):
    summary = {
        "schema_version": 1,
        "numeric_gate": "passed",
        "citation_gate": "passed",
        "regen_count": 0,
        "blocked": False,
        "interventions": [],
    }

    async def fake_build_runtime(req, user, db):
        return {"agent_names": ["orchestrator"], "toolset": [], "system": "test system"}

    async def fake_run_orchestrated_chat(**kwargs):
        return {
            "reply": "validated answer",
            "actions": [],
            "tool_results": [],
            "hit_iteration_cap": False,
            "hit_deadline": False,
            "validation_summary": summary,
        }

    monkeypatch.setattr("app.api.chat._build_runtime", fake_build_runtime)
    monkeypatch.setattr("app.api.chat._run_orchestrated_chat", fake_run_orchestrated_chat)

    resp = await app_client.post(
        "/api/chat/message",
        headers={"Authorization": f"Bearer {test_user[1]}"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["validation_summary"] == summary


async def test_stream_final_text_event_carries_validation_summary(app_client, monkeypatch):
    summary = {
        "schema_version": 1,
        "numeric_gate": "blocked",
        "citation_gate": "passed",
        "regen_count": 2,
        "blocked": True,
        "interventions": [
            {"gate": "numeric_claims", "action": "blocked", "reason": "regen_exhausted"},
        ],
    }

    async def fake_build_runtime(req, user, db):
        return {"agent_names": ["orchestrator"], "toolset": [], "system": "test system"}

    async def fake_run_orchestrated_chat(**kwargs):
        return {
            "reply": "blocked banner text",
            "actions": [],
            "tool_results": [],
            "hit_iteration_cap": False,
            "hit_deadline": False,
            "validation_summary": summary,
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
                "python_session_id": "validation-summary-stream-test",
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
    assert text_frames[-1].get("validation_summary") == summary, text_frames


# ---------- 4. provenance ledger registration (JOB 3 surfacing) ----------


def test_execute_tool_calls_registers_reproducibility_envelope(monkeypatch):
    """A chat tool result's run_id must become answerable through the
    provenance service (previously only pipeline {run_id}:{node_id} entity
    ids were ever recorded, so /api/provenance/{run_id}/* always returned
    an empty graph for the envelopes users actually see)."""
    from app.services.agent_runtime import tool_execution as te
    from app.services import provenance as prov

    run_id = "test-run-validation-summary-0001"

    async def fake_execute_tool(tool_name, tool_input, *args, **kwargs):
        return {
            "success": True,
            "rows": [{"z": 0.5}],
            "reproducibility": {
                "run_id": run_id,
                "tool_version": "test",
                "query_hash": "abc123",
                "timestamp_utc": "2026-07-03T00:00:00+00:00",
                "archive_version": "DESI DR2",
            },
        }

    import app.services.ai_tools as ai_tools_mod
    monkeypatch.setattr(ai_tools_mod, "execute_tool", fake_execute_tool)

    executed = asyncio.run(
        te._execute_tool_calls(
            [{"id": "c1", "name": "run_adql", "input": {"query": "SELECT 1"}}],
            "", {}, "prov-test-session", chat_session_id="chat-prov-1",
        )
    )
    assert executed[0]["result"]["success"] is True

    lineage = prov.get_lineage(run_id)
    assert lineage["nodes"], "envelope run_id was not registered in the ledger"
    node = lineage["nodes"][0]
    assert node["id"] == run_id
    assert "run_adql" in node["label"]
    # The record carries the envelope's reproducibility params.
    records = [r for r in prov._provenance_records if r["entity_id"] == run_id]
    assert records and records[0]["params"]["query_hash"] == "abc123"
    assert records[0]["data_release"] == "DESI DR2"
    assert records[0]["params"]["chat_session_id"] == "chat-prov-1"


def test_record_tool_provenance_activity_ignores_envelope_free_results():
    from app.services.agent_runtime import tool_execution as te
    from app.services import provenance as prov

    before = len(prov._provenance_records)
    te._record_tool_provenance_activity("run_adql", {"success": False, "error": "boom"})
    te._record_tool_provenance_activity("run_adql", "not-a-dict")
    te._record_tool_provenance_activity("run_adql", {"reproducibility": {"run_id": ""}})
    assert len(prov._provenance_records) == before
