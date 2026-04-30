"""Tests for F2.3: <tools_returned_nothing/> structured abstention parser.

The LLM is instructed by the system prompt to emit a single XML tag when
all tools return empty/failed.  The backend must parse it, classify the
reason, render a canonical markdown card, and emit the SSE event.
"""

from __future__ import annotations

from app.api.chat import (
    _parse_abstention_tag,
    _classify_abstention_reason,
    _render_abstention_card,
    _sanitize_tools_returned_nothing,
)


# ---------- Parser happy-path ----------


def test_parse_self_closing_tag_all_attrs():
    reply = (
        '<tools_returned_nothing failed_tools="run_python" '
        'empty_tools="run_adql" '
        'rationale="ADQL returned 0 rows and sandbox crashed." '
        'suggested_next_step="Widen the cone radius."/>'
    )
    attrs = _parse_abstention_tag(reply)
    assert attrs is not None
    assert attrs["failed_tools"] == "run_python"
    assert attrs["empty_tools"] == "run_adql"
    assert "ADQL returned 0 rows" in attrs["rationale"]
    assert "cone radius" in attrs["suggested_next_step"]


def test_parse_tolerates_whitespace_and_newlines():
    reply = (
        '   \n<tools_returned_nothing\n'
        '   failed_tools="a,b"\n'
        '   empty_tools=""\n'
        '   rationale="test rationale"\n'
        '   suggested_next_step="step"\n'
        '/>   \n  '
    )
    attrs = _parse_abstention_tag(reply)
    assert attrs is not None
    assert attrs["failed_tools"] == "a,b"
    assert attrs["empty_tools"] == ""


def test_parse_accepts_single_quotes():
    reply = (
        "<tools_returned_nothing failed_tools='x' empty_tools='y' "
        "rationale='ok' suggested_next_step='retry'/>"
    )
    attrs = _parse_abstention_tag(reply)
    assert attrs == {
        "failed_tools": "x",
        "empty_tools": "y",
        "rationale": "ok",
        "suggested_next_step": "retry",
    }


def test_parse_accepts_open_close_form():
    reply = (
        '<tools_returned_nothing failed_tools="t" empty_tools=""'
        ' rationale="r" suggested_next_step="s"></tools_returned_nothing>'
    )
    attrs = _parse_abstention_tag(reply)
    assert attrs is not None
    assert attrs["failed_tools"] == "t"


def test_parse_recovers_no_underscore_tag_and_attr_aliases():
    """B1: model emitted malformed abstention XML; UI should still render a card."""
    reply = (
        '<toolsreturnednothing failedtools="run_adql,run_python" '
        'emptytools="search_lightcurve" '
        'rationale="Archives returned no usable data" '
        'suggestednext_step="Try AAVSO photometry"/>'
    )
    attrs = _parse_abstention_tag(reply)
    assert attrs is not None
    assert attrs["failed_tools"] == "run_adql,run_python"
    assert attrs["empty_tools"] == "search_lightcurve"
    assert attrs["suggested_next_step"] == "Try AAVSO photometry"


# ---------- Parser negatives ----------


def test_parse_rejects_prose_before_tag():
    reply = 'Here is my answer: <tools_returned_nothing failed_tools="x"/>'
    assert _parse_abstention_tag(reply) is None


def test_parse_rejects_prose_after_tag():
    reply = (
        '<tools_returned_nothing failed_tools="x"/>\n'
        'And here is some extra prose the model added.'
    )
    assert _parse_abstention_tag(reply) is None


def test_parse_rejects_normal_reply():
    assert _parse_abstention_tag("The parallax is 7.3 mas.") is None


def test_parse_rejects_empty_reply():
    assert _parse_abstention_tag("") is None


def test_sanitize_embedded_malformed_abstention_hides_xml_and_tool_names():
    reply = (
        'I tried to continue. <toolsreturnednothing '
        'failedtools="extractliteraturetables,fitlinelfr" '
        'rationale="extractliteraturetables rate limit reached" '
        'suggestednext_step="Run fit_line_lfr later"/>'
    )
    safe = _sanitize_tools_returned_nothing(reply)
    assert "<toolsreturnednothing" not in safe
    assert "failedtools" not in safe
    assert "extractliteraturetables" not in safe
    assert "fit_line_lfr" not in safe
    assert "table extraction" in safe
    assert "line-relation fitting" in safe


# ---------- Reason classification ----------


def test_reason_empty_only():
    tool_results = [
        {"tool": "run_adql", "result": {"__tool_status__": "EMPTY"}},
    ]
    assert _classify_abstention_reason(tool_results) == "empty"


def test_reason_failed_only():
    tool_results = [
        {"tool": "run_python", "result": {"__tool_status__": "FAILED"}},
    ]
    assert _classify_abstention_reason(tool_results) == "failed"


def test_reason_mixed():
    tool_results = [
        {"tool": "run_adql", "result": {"__tool_status__": "EMPTY"}},
        {"tool": "run_python", "result": {"__tool_status__": "FAILED"}},
    ]
    assert _classify_abstention_reason(tool_results) == "mixed"


def test_reason_no_tools():
    assert _classify_abstention_reason([]) == "no_tools"


def test_reason_reads_analysis_status_fallback():
    """When banner not present, analysis_status should be consulted."""
    tool_results = [{"tool": "run_adql", "result": {"analysis_status": "empty"}}]
    assert _classify_abstention_reason(tool_results) == "empty"


# ---------- Rendered card ----------


def test_rendered_card_has_checkmark_header():
    attrs = {
        "failed_tools": "run_python",
        "empty_tools": "run_adql",
        "rationale": "No rows.",
        "suggested_next_step": "Widen radius.",
    }
    card = _render_abstention_card(attrs, "mixed")
    assert "✓" in card
    assert "run_python" in card
    assert "run_adql" in card
    assert "Widen radius" in card


def test_rendered_card_shows_empty_header_when_only_empty():
    attrs = {
        "failed_tools": "",
        "empty_tools": "run_adql",
        "rationale": "Query returned 0 rows.",
        "suggested_next_step": "Widen the cone radius.",
    }
    card = _render_abstention_card(attrs, "empty")
    assert "returned no data" in card
    assert "cone radius" in card


def test_rendered_card_without_rationale_includes_default_prose():
    attrs = {
        "failed_tools": "",
        "empty_tools": "",
        "rationale": "",
        "suggested_next_step": "",
    }
    card = _render_abstention_card(attrs, "no_tools")
    assert "No numerical claims" in card
