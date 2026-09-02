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
