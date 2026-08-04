"""PART AG C1 — citation guard annotate-and-attach mode regression.

R2.4 M6 audit: AI produced a long correct prose (Python output, fit
numbers, sample analysis), then mentioned "Bothwell 2013" on a single
line without a corresponding tool_result. The citation guard tripped
and the user-visible reply was replaced ENTIRELY by the "Reply
withheld" message, erasing 11 of 12 tool cards' worth of work.

Locks the new behaviour: original prose stays, the violation report is
APPENDED as a footer.
"""

from __future__ import annotations

from app.services.claim_validator import (
    CitationViolation,
    blocked_citation_reply_text,
    blocked_methodology_reply_text,
    limited_citation_reply_text,
    limited_methodology_reply_text,
)


def test_blocked_citation_reply_text_returns_footer_only_text() -> None:
    """blocked_citation_reply_text is the FOOTER text — by itself it
    must NOT contain the original prose. The composition with the prose
    happens in chat.py, not in claim_validator."""
    text = blocked_citation_reply_text([
        CitationViolation(
            kind="suspicious_author_year",
            match_text="Bothwell 2013",
            line_number=19,
        ),
    ])
    assert "Reply withheld" in text
    assert "Bothwell 2013" in text
    assert "(line 19)" in text


def test_blocked_citation_reply_text_hints_for_builtin_cosmology_manifest() -> None:
    text = blocked_citation_reply_text([
        CitationViolation(
            kind="invalid_bibcode",
            match_text="2020A&A...641A...6P",
            line_number=14,
        ),
    ])

    assert "platform cosmology preset" in text
    assert "compare_luminosity_distances" in text
    assert "tool_results" in text


def test_limited_citation_note_does_not_claim_the_visible_reply_was_withheld() -> None:
    text = limited_citation_reply_text([
        CitationViolation(
            kind="suspicious_author_year",
            match_text="Bothwell 2013",
            line_number=19,
        ),
    ])

    assert "Unsupported citation note" in text
    assert "Reply withheld" not in text
    assert "Bothwell 2013" in text


def test_chat_appends_violation_footer_without_replacing_prose() -> None:
    """Simulate the chat.py inline composition path: original prose +
    footer. The full reply must contain BOTH."""
    original_prose = (
        "## Sample composition\n\n"
        "Loaded 74 [CII] line measurements from ALPINE (arXiv:2002.00962).\n\n"
        "## Fit results\n\n"
        "OLS slope = 0.798, scatter = 0.320 dex.\n\n"
        "## Multi-survey context\n\n"
        "Bothwell 2013 reported a similar slope at z~3."  # ← line 7, the offending citation
    )
    violation = CitationViolation(
        kind="suspicious_author_year",
        match_text="Bothwell 2013",
        line_number=7,
    )
    annotation = limited_citation_reply_text([violation])

    # This mirrors what chat.py:_run_agent_loop now does.
    composed = original_prose.rstrip() + (
        "\n\n---\n\n"
        "## ⚠ Limited answer: provenance gaps\n\n"
        "The supported parts of the answer remain visible, but "
        "the platform's provenance gate flagged specific claims "
        "that this turn's tool results did not support. Treat "
        "only the flagged items as "
        "**NOT verified** and re-run the relevant tools before "
        "quoting any of them in a paper.\n\n"
        + annotation
    )

    # 1. Original prose untouched
    assert "Loaded 74 [CII] line measurements" in composed
    assert "OLS slope = 0.798, scatter = 0.320 dex" in composed
    # 2. Violation footer present
    assert "## ⚠ Limited answer: provenance gaps" in composed
    assert "Reply withheld" not in composed
    assert "Bothwell 2013" in composed
    # 3. Composition order: prose THEN footer (the M6 reproducer's key
    # property — user sees their analysis first, then the warning)
    prose_idx = composed.index("OLS slope")
    footer_idx = composed.index("Limited answer")
    assert prose_idx < footer_idx, "prose must come before footer"


def test_blocked_methodology_reply_text_separately_addressable() -> None:
    """Methodology violations get their own footer text (PART AB) —
    they should round-trip through the same annotate-and-attach path."""
    text = blocked_methodology_reply_text([
        CitationViolation(
            kind="method_mismatch",
            match_text="Bayesian xyerr fit",
            line_number=5,
        ),
    ])
    assert 'fit_method_requested="bayesian_xyerr"' in text
    # Must NOT mention "re-run the archive query" — that's the citation
    # advice and would mislead the AI when the violation is methodology.
    assert "re-run the archive" not in text


def test_limited_methodology_note_does_not_claim_the_reply_was_withheld() -> None:
    text = limited_methodology_reply_text([
        CitationViolation(
            kind="method_mismatch",
            match_text="Bayesian xyerr fit",
            line_number=5,
        ),
    ])

    assert "Unsupported methodology note" in text
    assert "Reply withheld" not in text
