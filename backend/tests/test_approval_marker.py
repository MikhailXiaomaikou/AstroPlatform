"""Approval language in a chat reply must never ship unmarked.

Chat has no approval state: the ClaimAuditReview lane is behind three
default-off flags and is not reachable from the loop. A reply that opens a
line with "Draft claim" or "APPROVED by" and states a tool-matched number is
handing the reader a real measurement wearing a governance stamp the platform
never issued, and every numeric gate passes it because the number genuinely
came from a claimable tool result.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.agent_runtime.approval import (
    _MARKER,
    APPROVAL_STATE_NONE,
    mark_unapproved_claims,
)

_BACKEND = Path(__file__).resolve().parents[1]


def _claimable() -> list[dict]:
    return [{
        "tool": "compare_luminosity_distances",
        "result": {
            "success": True,
            "publication_ready": True,
            "comparison_mode": "h0_anchors",
            "anchors": {"planck18": {"H0": 67.36, "sigma": 0.54}},
        },
    }]


def test_approval_language_with_a_tool_matched_number_is_marked() -> None:
    for line in (
        "Draft claim: H0 = 67.36 +/- 0.54 km/s/Mpc.",
        "APPROVED by human reviewer: H0 = 67.36 km/s/Mpc.",
        "- Draft claim: H0 = 67.36 km/s/Mpc.",
        "**Draft claim:** H0 = 67.36 km/s/Mpc.",
        "Reviewer approved: the anchor is 67.36.",
    ):
        marked, count = mark_unapproved_claims(line, _claimable())
        assert count == 1, line
        assert "NOT APPROVED - " in marked, line
        # The number itself is untouched; only the framing changes.
        assert "67.36" in marked


def test_prose_about_approval_without_a_number_is_left_alone() -> None:
    for line in (
        "Draft claim: none eligible for this turn.",
        "The draft claim above still needs a reviewer.",
        "APPROVED by human reviewer: see the audit record for details.",
    ):
        marked, count = mark_unapproved_claims(line, _claimable())
        assert count == 0, line
        assert marked == line


def test_marker_is_line_anchored_and_idempotent() -> None:
    mid_sentence = "We will file the draft claim: H0 = 67.36 later."
    marked, count = mark_unapproved_claims(mid_sentence, _claimable())
    assert count == 0 and marked == mid_sentence

    once, first = mark_unapproved_claims(
        "Draft claim: H0 = 67.36 km/s/Mpc.", _claimable()
    )
    twice, second = mark_unapproved_claims(once, _claimable())
    assert first == 1 and second == 0
    assert twice.count("NOT APPROVED - ") == 1


def test_number_from_a_non_claimable_result_is_not_treated_as_approved() -> None:
    """The marker exists for the case where a real measurement is dressed as
    approved. A number the gates already withhold is another gate's problem."""
    withheld = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {"publication_ready": False, "parameters": {"H0": {"median": 67.36}}},
    }]
    marked, count = mark_unapproved_claims(
        "Draft claim: H0 = 67.36 km/s/Mpc.", withheld
    )
    assert count == 0 and "NOT APPROVED" not in marked


def test_no_code_path_sets_publication_ready_from_a_review_decision() -> None:
    """An approval must never promote a result's tier. Source assertion in the
    style of the existing regression guards."""
    pattern = re.compile(r"publication_ready\W*(?:=|:)\s*True")
    for name in (
        "app/services/research_workspace_service.py",
        "app/services/claim_audit_service.py",
        "app/api/claim_audits.py",
        "app/services/union3_research_loop.py",
        "app/services/agent_runtime/approval.py",
    ):
        path = _BACKEND / name
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not pattern.search(line), f"{name}:{number}: {line.strip()}"


def test_validation_summary_always_reports_an_approval_state() -> None:
    from app.services.agent_runtime.loop import _derive_validation_summary

    summary = _derive_validation_summary(
        claim_gate_ran=True,
        gate_skip_reason=None,
        fabrication_stats={},
        interventions=[],
        tool_results=[],
    )
    assert summary["approval_state"] == APPROVAL_STATE_NONE


def test_markdown_heading_and_ordered_list_prefixes_are_marked() -> None:
    """The shapes a model actually writes for a verdict line.

    Before this was pinned, "### APPROVED by human reviewer: H0 = 67.36" and
    "1. Draft claim: H0 = 67.36" both came back unchanged with count 0: the
    prefix expression only accepted bullets, blockquotes and a bold run, so a
    heading or an ordered-list item shipped the governance stamp intact.
    """
    for line in (
        "### APPROVED by human reviewer: H0 = 67.36 km/s/Mpc.",
        "# Draft claim: H0 = 67.36 km/s/Mpc.",
        "###### Reviewer approved: H0 = 67.36 km/s/Mpc.",
        "1. Draft claim: H0 = 67.36 km/s/Mpc.",
        "2) APPROVED by human reviewer: H0 = 67.36 km/s/Mpc.",
        "  12. Draft claim: H0 = 67.36 km/s/Mpc.",
    ):
        marked, count = mark_unapproved_claims(line, _claimable())
        assert count == 1, line
        assert _MARKER in marked, line
        # The marker goes after the Markdown structure, never before it, so
        # the heading or list item still renders as a heading or list item.
        assert not marked.startswith(_MARKER), line
        assert "67.36" in marked, line


def test_prefix_match_stays_linear_on_a_pathological_prefix() -> None:
    """A CodeQL finding on this repository was an ambiguous-prefix regex.

    Doubling a pathological run of prefix characters must roughly double the
    match cost, not square it. The wall-clock bound is generous on purpose;
    the assertion that matters is that a 20k-character prefix returns at all.
    """
    import time

    timings: list[float] = []
    for size in (5_000, 10_000, 20_000):
        line = " " * size + "*" * size + "-" * size + "x Draft claim: 67.36"
        start = time.perf_counter()
        marked, count = mark_unapproved_claims(line, _claimable())
        timings.append(time.perf_counter() - start)
        # "x" separates the run from the approval phrase, so nothing matches.
        assert count == 0 and marked == line
    assert timings[-1] < 1.0, timings
    # Superlinear blow-up would show up as a growth factor far above 2.
    assert timings[-1] < max(timings[0], 1e-4) * 20, timings


# ---------- the merged multi-specialist reply ----------


def _fake_specialist_result(reply: str, tool_results: list[dict]) -> dict:
    return {
        "reply": reply,
        "actions": [],
        "tool_results": tool_results,
        "hit_deadline": False,
        "hit_iteration_cap": False,
        "validation_summary": {
            "schema_version": 2,
            "approval_state": APPROVAL_STATE_NONE,
            "numeric_gate": "passed",
            "citation_gate": "passed",
            "regen_count": 0,
            "blocked": False,
            "limited": False,
            "response_disposition": "full",
            "task_kind": "general",
            "earliest_limiting_stage": None,
            "missing_dependencies": [],
            "safe_fallback": None,
            "interventions": [],
        },
    }


def _run_merge(monkeypatch, merged_text: str, tool_results: list[dict]) -> dict:
    """Drive the real multi-specialist merge path in app.api.chat."""
    import asyncio
    from types import SimpleNamespace

    import app.api.chat as chat_mod

    async def fake_loop(**_kwargs):
        return _fake_specialist_result(
            "Specialist reply with no approval language.", tool_results
        )

    async def fake_handoff(source, target, _reply):
        return SimpleNamespace(
            source_agent=source,
            target_agent=target,
            context_summary="Anchor comparison completed.",
            instruction="Review the anchor comparison.",
        )

    async def fake_merge(_agent_results):
        return merged_text

    monkeypatch.setattr(chat_mod, "_run_agent_loop", fake_loop)
    monkeypatch.setattr(
        chat_mod.orchestrator,
        "get_agent_runtime",
        lambda _name, _context: {"system_prompt": "specialist", "tool_names": []},
    )
    monkeypatch.setattr(chat_mod.orchestrator, "summarize_handoff", fake_handoff)
    monkeypatch.setattr(chat_mod.orchestrator, "merge_responses", fake_merge)

    return asyncio.run(chat_mod._run_orchestrated_chat(
        runtime={
            "agent_names": ["analyst", "reviewer"],
            "base_system": "test multi-agent system",
            "toolset": [],
        },
        messages=[{"role": "user", "content": "Summarise the H0 anchor."}],
        provider_api_keys={},
        python_session_id="approval-merge-test",
    ))


def test_merged_reply_approval_language_is_marked(monkeypatch) -> None:
    """merge_responses writes a NEW public reply.

    The loop's marker ran on each specialist reply, but the merged boundary
    only re-ran the numeric, citation and scientific-conclusion gates — all of
    which pass "APPROVED by reviewer: H0 = 67.36" because the number really
    did come from a claimable tool result. Before the fix this merged text
    shipped verbatim with limited=False and no approval_marker intervention.
    """
    result = _run_merge(
        monkeypatch,
        "APPROVED by reviewer: H0 = 67.36 km/s/Mpc.",
        _claimable(),
    )
    assert result["reply"].startswith(_MARKER)
    assert "67.36" in result["reply"]

    summary = result["validation_summary"]
    # Same limited flag as the single-specialist path.
    assert summary["limited"] is True
    assert summary["response_disposition"] == "limited"
    # Same gate event as the single-specialist path.
    marker_events = [
        item for item in summary["interventions"]
        if item.get("gate") == "approval_marker"
    ]
    assert len(marker_events) == 1
    assert marker_events[0]["action"] == "annotated_limited"
    assert marker_events[0]["reason"] == "no_bound_claim_audit_review"
    assert marker_events[0]["marked_lines"] == 1


def test_merged_reply_without_approval_language_is_untouched(monkeypatch) -> None:
    """The marker must not turn a clean merge into a limited one."""
    clean = "The registered CMB-only anchor is H0 = 67.36 km/s/Mpc."
    result = _run_merge(monkeypatch, clean, _claimable())
    assert result["reply"] == clean
    summary = result["validation_summary"]
    assert summary["limited"] is False
    assert summary["response_disposition"] == "full"
    assert not [
        item for item in summary["interventions"]
        if item.get("gate") == "approval_marker"
    ]


# ---------- every schema-v2 summary carries approval_state ----------


def _abstaining_specialist_result() -> dict:
    return {
        "reply": "No claimable tool-backed evidence was produced.",
        "actions": [],
        "tool_results": [],
        "hit_deadline": False,
        "hit_iteration_cap": False,
        "honest_abstention": True,
        "abstention_reason": "no_tools",
        "abstention_payload": {
            "failed_tools": "",
            "empty_tools": "",
            "rationale": "No tool produced claimable evidence.",
            "suggested_next_step": "Name a registered dataset and rerun.",
            "reason": "no_tools",
            "agent": "analyst",
        },
        "validation_summary": {
            "schema_version": 2,
            "approval_state": APPROVAL_STATE_NONE,
            "numeric_gate": "not_run",
            "citation_gate": "not_run",
            "regen_count": 0,
            "blocked": False,
            "limited": False,
            "response_disposition": "abstention",
            "task_kind": "general",
            "earliest_limiting_stage": "no_tools",
            "missing_dependencies": [],
            "safe_fallback": None,
            "interventions": [],
        },
    }


def test_every_schema_v2_summary_builder_carries_approval_state(
    monkeypatch,
) -> None:
    """Enumerate the builders, not just the one that had the field.

    Before the fix only ``_derive_validation_summary`` emitted
    ``approval_state``. The loop-deadline / honest-abstention returns and the
    merged multi-specialist summary all shipped schema-v2 payloads without it,
    so the badge fell back to inferring an absent approval from an absent
    field on exactly the paths where least review work was done.
    """
    import asyncio
    from types import SimpleNamespace

    import app.api.chat as chat_mod
    from app.services.agent_runtime.loop import (
        _derive_validation_summary,
        _not_run_validation_summary,
    )

    summaries: dict[str, dict] = {}

    summaries["_derive_validation_summary"] = _derive_validation_summary(
        claim_gate_ran=True,
        gate_skip_reason=None,
        fabrication_stats={},
        interventions=[],
        tool_results=[],
    )
    summaries["_not_run_validation_summary/loop_deadline"] = (
        _not_run_validation_summary("loop_deadline")
    )
    summaries["_not_run_validation_summary/honest_abstention"] = (
        _not_run_validation_summary("honest_abstention")
    )
    summaries["chat._run_orchestrated_chat/merged"] = _run_merge(
        monkeypatch,
        "The registered CMB-only anchor is H0 = 67.36 km/s/Mpc.",
        _claimable(),
    )["validation_summary"]

    async def fake_abstaining_loop(**_kwargs):
        return _abstaining_specialist_result()

    async def fake_handoff(source, target, _reply):
        return SimpleNamespace(
            source_agent=source,
            target_agent=target,
            context_summary="Nothing claimable.",
            instruction="Abstain.",
        )

    monkeypatch.setattr(chat_mod, "_run_agent_loop", fake_abstaining_loop)
    monkeypatch.setattr(
        chat_mod.orchestrator,
        "get_agent_runtime",
        lambda _name, _context: {"system_prompt": "specialist", "tool_names": []},
    )
    monkeypatch.setattr(chat_mod.orchestrator, "summarize_handoff", fake_handoff)
    abstained = asyncio.run(chat_mod._run_orchestrated_chat(
        runtime={
            "agent_names": ["analyst", "reviewer"],
            "base_system": "test multi-agent system",
            "toolset": [],
        },
        messages=[{"role": "user", "content": "Summarise the H0 anchor."}],
        provider_api_keys={},
        python_session_id="approval-abstention-test",
    ))
    assert abstained.get("honest_abstention") is True
    summaries["chat._run_orchestrated_chat/all_abstained"] = (
        abstained["validation_summary"]
    )

    for name, summary in summaries.items():
        assert summary["schema_version"] == 2, name
        assert summary["approval_state"] == APPROVAL_STATE_NONE, name


def test_no_schema_v2_validation_summary_literal_omits_approval_state() -> None:
    """Source guard so a NEW builder cannot quietly drop the field.

    Scoped to the two modules that emit validation summaries; the unrelated
    schema-v2 payloads elsewhere (session export, evidence pack manifest,
    deletion tombstone) are not validation summaries.
    """
    for name in (
        "app/services/agent_runtime/loop.py",
        "app/api/chat.py",
    ):
        lines = (_BACKEND / name).read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            if line.strip() != '"schema_version": 2,':
                continue
            window = "\n".join(lines[number:number + 10])
            assert '"approval_state"' in window, (
                f"{name}:{number + 1}: schema-v2 summary without approval_state"
            )


def test_little_h_approval_claim_is_marked() -> None:
    """``h = 0.6736`` is ``H0 = 67.36`` wearing the standard reduced units.

    The guard compared ``_reply_number_tokens``, which drops the converted
    little-h token, so an approval stamp on the reduced-unit form shipped
    unmarked (Codex review 2026-09-03).
    """
    from app.services.agent_runtime.approval import mark_unapproved_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "success": True,
            "publication_ready": True,
            "chain_tier": "publication",
            "parameters": {"H0": {"median": 67.36, "std": 0.42}},
        },
    }]
    marked, count = mark_unapproved_claims(
        "APPROVED by reviewer: h = 0.6736", tool_results
    )
    assert count == 1
    assert marked.startswith("NOT APPROVED - ")


def test_nested_markdown_markers_before_a_verdict_are_accepted() -> None:
    """Verdict lines nest their Markdown markers in real output.

    The prefix accepted a single structural marker, so ``> ### APPROVED by
    ...`` and ``- > Draft claim: ...`` did not match and shipped unmarked
    (Codex review 2026-09-03).
    """
    from app.services.agent_runtime.approval import mark_unapproved_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "success": True,
            "publication_ready": True,
            "chain_tier": "publication",
            "parameters": {"H0": {"median": 67.36, "std": 0.42}},
        },
    }]
    for line in (
        "> ### APPROVED by reviewer: H0 = 67.36",
        "- > Draft claim: H0 = 67.36",
        "1. > **Draft claim:** H0 = 67.36",
        "**Draft claim:** H0 = 67.36",
    ):
        marked, count = mark_unapproved_claims(line, tool_results)
        assert count == 1, line
        assert "NOT APPROVED - " in marked, line
    # Prose that merely mentions a draft claim is still left alone.
    untouched, count = mark_unapproved_claims(
        "The draft claim above is unrelated: H0 = 67.36", tool_results
    )
    assert count == 0
    assert "NOT APPROVED" not in untouched


def test_approval_prefix_match_stays_linear() -> None:
    """The repeated marker group must not backtrack polynomially.

    Each repetition consumes exactly one marker character plus its trailing
    spaces, so no whitespace run can be split across iterations.  Pinned the
    way the hypothesis-label regex is, after a CodeQL py/polynomial-redos
    finding on this repository.
    """
    import time

    from app.services.agent_runtime.approval import _APPROVAL_LINE_RE

    timings = []
    for size in (4000, 16000, 64000):
        probe = " " * size + "-" * size + " " * size + "x"
        started = time.perf_counter()
        _APPROVAL_LINE_RE.match(probe)
        timings.append(time.perf_counter() - started)
    # A 16x input growth must not cost anywhere near 16^2; allow generous
    # slack for a loaded CI runner.
    assert timings[-1] < 1.0
