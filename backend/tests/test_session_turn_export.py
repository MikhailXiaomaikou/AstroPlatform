"""Current-turn transcript export — clean per-turn signals for blind-test eval."""

from app.api.sessions import _slice_turn, _summarize_turn


def _msgs() -> list[dict]:
    return [
        {"role": "user", "content": "first question about Gaia"},
        {"role": "assistant", "content": "first answer", "actions": []},
        {"role": "user", "content": "build a robustness matrix for DESI BAO"},
        {
            "role": "assistant",
            "content": "Here is the matrix.",
            "actions": [
                {"action": "run_research_matrix", "tool_result": {
                    "analysis_status": "RESEARCH_MATRIX_READY", "publication_ready": True}},
                {"action": "verify_research_facts", "tool_result": {
                    "analysis_status": "FACT_CHECK_READY",
                    "fact_check_report": {"status": "blocked", "verified_claim_count": 2, "unsupported_claim_count": 5}}},
                {"action": "export_research_report", "tool_result": {
                    "analysis_status": "RESEARCH_REPORT_READY", "paper_draft_markdown": "# Paper Draft..."}},
            ],
        },
    ]


def test_slice_turn_returns_latest_turn_only() -> None:
    idx, turn = _slice_turn(_msgs())
    assert idx == 1  # second (latest) user turn
    texts = [str(m.get("content")) for m in turn]
    assert "first question about Gaia" not in texts  # earlier turn excluded
    assert any("robustness matrix" in t for t in texts)


def test_slice_turn_index_selects_specific_turn_and_clamps() -> None:
    idx, turn = _slice_turn(_msgs(), index=0)
    assert idx == 0
    assert any("first question" in str(m.get("content")) for m in turn)
    idx2, _ = _slice_turn(_msgs(), index=99)  # out of range -> latest
    assert idx2 == 1


def test_summarize_turn_extracts_signals() -> None:
    idx, turn = _slice_turn(_msgs())
    summary = _summarize_turn(idx, turn)
    assert summary["turn_index"] == 1
    assert summary["turn_complete"] is True
    assert "robustness matrix" in summary["user_prompt"]
    assert summary["assistant_reply"] == "Here is the matrix."
    assert summary["fact_check"]["status"] == "blocked"
    assert summary["fact_check"]["unsupported_claim_count"] == 5
    tools = {tr["tool"] for tr in summary["tool_runs"]}
    assert {"run_research_matrix", "verify_research_facts", "export_research_report"} <= tools
    assert summary["research_report_present"] is True
    assert summary["paper_draft_present"] is True
    assert summary["internal_markers_present"] is False


def test_summarize_turn_flags_internal_marker_leak() -> None:
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "answer with <thinking>secret</thinking> leaked", "actions": []},
    ]
    idx, turn = _slice_turn(msgs)
    summary = _summarize_turn(idx, turn)
    assert summary["internal_markers_present"] is True


def test_summarize_turn_incomplete_when_no_assistant_reply() -> None:
    idx, turn = _slice_turn([{"role": "user", "content": "q with no answer yet"}])
    summary = _summarize_turn(idx, turn)
    assert summary["turn_complete"] is False
    assert summary["fact_check"] is None
    assert summary["assistant_reply"] == ""
