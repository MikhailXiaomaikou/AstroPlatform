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


def _published_h0() -> list[dict]:
    return [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "success": True,
            "publication_ready": True,
            "chain_tier": "publication",
            "parameters": {"H0": {"median": 67.36, "std": 0.42}},
        },
    }]


def test_a_standalone_verdict_line_stamps_the_neighbouring_result() -> None:
    """The verdict often stands on its own line, above or below the number.

    The check looked only at the verdict line itself, so "H0 = 67.36
    km/s/Mpc." followed by "APPROVED by human reviewer." shipped unmarked --
    the same stamp on the same number (Codex review 2026-09-03).
    """
    from app.services.agent_runtime.approval import mark_unapproved_claims

    tool_results = _published_h0()
    for text in (
        "The chain gives H0 = 67.36 km/s/Mpc.\nAPPROVED by human reviewer.",
        "APPROVED by human reviewer.\nThe chain gives H0 = 67.36 km/s/Mpc.",
        "H0 = 67.36 km/s/Mpc.\n\nAPPROVED by human reviewer.",
    ):
        marked, count = mark_unapproved_claims(text, tool_results)
        assert count == 1, text
        assert "NOT APPROVED - APPROVED by human reviewer." in marked, text
    # A verdict with no claim anywhere near it is prose, not a stamped result.
    untouched, count = mark_unapproved_claims(
        "APPROVED by human reviewer.\nNothing numeric here.", tool_results
    )
    assert count == 0
    assert "NOT APPROVED" not in untouched


def test_a_coverage_level_is_not_the_claimed_value() -> None:
    """68 sits within 1% of a claimable 67.36, but it is an interval level.

    The line "Draft claim: the 68% credible interval remains to be
    calculated" was stamped NOT APPROVED, which reads as a suppressed result
    where there is none (Codex review 2026-09-03).
    """
    from app.services.agent_runtime.approval import mark_unapproved_claims

    untouched, count = mark_unapproved_claims(
        "Draft claim: the 68% credible interval remains to be calculated.",
        _published_h0(),
    )
    assert count == 0
    assert "NOT APPROVED" not in untouched
    # The value itself is still stamped.
    marked, count = mark_unapproved_claims(
        "Draft claim: H0 = 67.36 km/s/Mpc.", _published_h0()
    )
    assert count == 1 and marked.startswith("NOT APPROVED - ")


def test_the_platforms_own_approval_vocabulary_is_recognised() -> None:
    """The review lane stores the verdict as review_status / decision APPROVED.

    ``services/union3_research_loop.py`` writes ``review_status ==
    "APPROVED"`` and ``decision == "APPROVED"``, so "Review status:
    APPROVED" and "Decision: APPROVED" are the natural renderings and were
    shipping unmarked while "APPROVED by reviewer" was caught (Codex review
    2026-09-03).
    """
    from app.services.agent_runtime.approval import mark_unapproved_claims

    tool_results = _published_h0()
    for text in (
        "Review status: APPROVED — H0 = 67.36 km/s/Mpc",
        "Decision: APPROVED. H0 = 67.36 km/s/Mpc",
        "APPROVED — H0 = 67.36 km/s/Mpc",
        "review_status: APPROVED\nH0 = 67.36 km/s/Mpc",
    ):
        marked, count = mark_unapproved_claims(text, tool_results)
        assert count == 1, text
        assert marked.lstrip().startswith("NOT APPROVED - "), text

    # A bare "Approved" in ordinary prose is not a verdict: the word has to
    # be followed by a separator, or by the reviewer wording.
    untouched, count = mark_unapproved_claims(
        "Approved datasets were listed; H0 = 67.36 came from the chain.", tool_results
    )
    assert count == 0 and "NOT APPROVED" not in untouched
    # And a verdict with no claim near it is still left alone.
    untouched, count = mark_unapproved_claims(
        "Review status: APPROVED — nothing numeric here.", tool_results
    )
    assert count == 0


def test_task_list_markers_and_fenced_examples() -> None:
    """Two more verdict-prefix shapes, in opposite directions.

    ``- [x] APPROVED by reviewer: H0 = 67.36`` left ``[x]`` in front of the
    lookahead and shipped unmarked; and a reply that TELLS the user not to
    write an approval line quotes one inside a fence, which was rewritten and
    marked the clean response limited (Codex review 2026-09-03).
    """
    from app.services.agent_runtime.approval import mark_unapproved_claims

    tool_results = _published_h0()
    for text in (
        "- [x] APPROVED by reviewer: H0 = 67.36",
        "- [ ] Draft claim: H0 = 67.36",
        "[x] APPROVED by reviewer: H0 = 67.36",
    ):
        marked, count = mark_unapproved_claims(text, tool_results)
        assert count == 1, text
        assert "NOT APPROVED - " in marked, text

    for fenced in (
        "Never write:\n```\nAPPROVED by reviewer: H0 = 67.36\n```\nUse the card.",
        "~~~\nAPPROVED by reviewer: H0 = 67.36\n~~~",
    ):
        untouched, count = mark_unapproved_claims(fenced, tool_results)
        assert count == 0, fenced
        assert "NOT APPROVED" not in untouched, fenced

    # A verdict AFTER the fence closes is still marked.
    marked, count = mark_unapproved_claims(
        "```\ncode\n```\nAPPROVED by reviewer: H0 = 67.36", tool_results
    )
    assert count == 1 and "NOT APPROVED - " in marked


def test_emphasis_around_the_verdict_word_is_recognised() -> None:
    """``**APPROVED** by reviewer: ...`` emphasises only the verdict.

    The prefix consumed the opening ``**`` and the closing one then stopped
    the lookahead from matching, so the fabricated approval shipped unmarked
    (Codex review 2026-09-03).  Emphasis markers inside the phrase are
    skipped when the phrase is tested.
    """
    from app.services.agent_runtime.approval import mark_unapproved_claims

    tool_results = _published_h0()
    for text in (
        "**APPROVED** by reviewer: H0 = 67.36",
        "**APPROVED by reviewer:** H0 = 67.36",
        "*APPROVED* by reviewer: H0 = 67.36",
        "**Draft claim:** H0 = 67.36",
        "Review status: **APPROVED** — H0 = 67.36",
        "**APPROVED** — H0 = 67.36",
    ):
        marked, count = mark_unapproved_claims(text, tool_results)
        assert count == 1, text
        assert "NOT APPROVED - " in marked, text
    # The two exclusions still hold.
    for clean in (
        "```\nAPPROVED by reviewer: H0 = 67.36\n```",
        "Approved datasets were listed; H0 = 67.36 came from the chain.",
    ):
        untouched, count = mark_unapproved_claims(clean, tool_results)
        assert count == 0, clean


# ---------- Codex review 2026-09-03, PR #69 second round ----------


def test_a_backtick_fence_inside_a_tilde_fence_does_not_close_it() -> None:
    """PRRT_kwDORoeoE86etNOq: a fence closes only on its own kind.

    ``_fenced_lines`` toggled on ANY fence line, so the inner backtick fence
    of a ``~~~~markdown`` example closed the outer tilde fence and the quoted
    verdict was stamped (count 1, want 0).  A fence closes only on a run of
    the same character at least as long as the opener (CommonMark).
    """
    for text in (
        "~~~~markdown\n```\nAPPROVED by reviewer: H0 = 67.36\n```\n~~~~",
        # A shorter run of the same character does not close it either.
        "````\n```\nAPPROVED by reviewer: H0 = 67.36\n```\n````",
    ):
        untouched, count = mark_unapproved_claims(text, _published_h0())
        assert count == 0, text
        assert untouched == text, text
    # A verdict after the outer fence really closes is still stamped.
    marked, count = mark_unapproved_claims(
        "~~~~\n```\n~~~~\nAPPROVED by reviewer: H0 = 67.36", _published_h0()
    )
    assert count == 1 and "NOT APPROVED - " in marked


def test_emphasis_around_the_status_label_is_recognised() -> None:
    """PRRT_kwDORoeoE86ethcM: ``**Review status:** APPROVED``.

    The status alternative demanded the colon straight after "status", so
    the closing ``**`` of a bold label blocked it and the line shipped
    unmarked (count 0, want 1).  Paired emphasis is accepted around the
    label as well as around the verdict word.
    """
    for text in (
        "**Review status:** APPROVED — H0 = 67.36",
        "**Review status**: APPROVED — H0 = 67.36",
        "**Decision:** APPROVED. H0 = 67.36 km/s/Mpc",
        "__Decision__: APPROVED — H0 = 67.36",
        "**Review status:** **APPROVED** — H0 = 67.36",
    ):
        marked, count = mark_unapproved_claims(text, _published_h0())
        assert count == 1, text
        assert "NOT APPROVED - " in marked, text


def test_a_number_not_bound_to_a_parameter_is_not_a_claim() -> None:
    """PRRT_kwDORoeoE86ethcQ: ``Draft claim: 67 galaxies pass the cut.``

    The 1% comparison bound ANY number on the line to the H0 result, so a
    galaxy count within 1% of 67.36 was stamped (count 1, want 0).  A line
    states the claim only when its number is assigned to a parameter --
    ``H0 = 67.36``, ``H0 is 67.36``, ``h = 0.6736`` -- and that value
    matches a claimable result.
    """
    for clean in (
        "Draft claim: 67 galaxies pass the cut.",
        "APPROVED by reviewer: 67 of the fields were inspected.",
        "The cut keeps 67 galaxies.\nAPPROVED by human reviewer.",
    ):
        untouched, count = mark_unapproved_claims(clean, _published_h0())
        assert count == 0, clean
        assert untouched == clean, clean
    for bound in (
        "APPROVED by reviewer: h = 0.6736",
        "APPROVED by reviewer: H0 = 67.36 km/s/Mpc",
        "APPROVED by reviewer: H0 is 67.36",
        "Draft claim: the median is 67.36",
        "The chain gives H0 = 67.36 km/s/Mpc.\nAPPROVED by human reviewer.",
    ):
        marked, count = mark_unapproved_claims(bound, _published_h0())
        assert count == 1, bound
        assert "NOT APPROVED - " in marked, bound


def test_an_indented_code_block_is_skipped_like_a_fence() -> None:
    """PRRT_kwDORoeoE86ethcV: four spaces or a tab open an indented code block.

    The prefix consumed arbitrary leading whitespace, so an example quoted
    the indented Markdown way was rewritten (count 1, want 0).  An indented
    block cannot interrupt a paragraph (CommonMark), so an indented line
    directly under a prose line is a lazy continuation that renders as that
    paragraph, and is still read.
    """
    for code in (
        "    APPROVED by reviewer: H0 = 67.36",
        "\tAPPROVED by reviewer: H0 = 67.36",
        "Never write:\n\n    APPROVED by reviewer: H0 = 67.36\n\nUse the card.",
        "Never write:\n\n    Draft claim: H0 = 67.36\n    APPROVED by reviewer.",
    ):
        untouched, count = mark_unapproved_claims(code, _published_h0())
        assert count == 0, code
        assert untouched == code, code
    # Up to three spaces is ordinary indentation: still a verdict line.
    marked, count = mark_unapproved_claims(
        "   APPROVED by reviewer: H0 = 67.36", _published_h0()
    )
    assert count == 1 and marked.startswith("   NOT APPROVED - ")
    # Directly under prose the indented line renders as that paragraph, so
    # the stamp on the number is still a stamp.
    marked, count = mark_unapproved_claims(
        "The chain gives H0 = 67.36 km/s/Mpc.\n    APPROVED by human reviewer.",
        _published_h0(),
    )
    assert count == 1 and "NOT APPROVED - APPROVED by human reviewer." in marked


def test_a_table_cell_delimiter_is_a_structural_prefix() -> None:
    """PRRT_kwDORoeoE86evFte: ``| APPROVED by reviewer: H0 = 67.36 |``.

    A verdict written as the first cell of a Markdown table row shipped
    unmarked (count 0, want 1) because ``|`` was not a prefix marker.
    """
    for text in (
        "| APPROVED by reviewer: H0 = 67.36 |",
        "|Draft claim: H0 = 67.36|",
        "| **Decision:** APPROVED | H0 = 67.36 |",
    ):
        marked, count = mark_unapproved_claims(text, _published_h0())
        assert count == 1, text
        # The marker lands inside the cell, after the delimiter.
        assert marked.startswith("|"), text
        assert marked.index(_MARKER) >= 1, text


def test_a_fence_line_with_an_info_string_does_not_close_an_open_fence() -> None:
    """Review thread e0fKl (2026-09-04): a closing fence carries no info string.

    ``_fenced_lines`` recognised a closer by its prefix alone, so inside an
    open ```` ```text ```` example the line ```` ```python ```` closed it and
    the quoted verdict below was stamped (count 1, want 0), which marked an
    otherwise clean reply limited.  CommonMark: a closing fence may be
    followed only by spaces or tabs to the end of the line, so a fence line
    with an info string while a fence is open is content.
    """
    for text in (
        "Never write:\n```text\n```python\nAPPROVED by reviewer: H0 = 67.36\n"
        "```\n```\nUse the card.",
        "~~~text\n~~~python\nAPPROVED by reviewer: H0 = 67.36\n~~~\n~~~",
    ):
        untouched, count = mark_unapproved_claims(text, _published_h0())
        assert count == 0, text
        assert untouched == text, text
    # Trailing spaces or tabs after the closer still close it, and a verdict
    # after the fence really closes is still stamped.
    marked, count = mark_unapproved_claims(
        "```text\n```python\nquoted\n```  \t\nAPPROVED by reviewer: H0 = 67.36",
        _published_h0(),
    )
    assert count == 1 and "NOT APPROVED - " in marked
