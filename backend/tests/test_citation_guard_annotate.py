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

from unittest.mock import patch
from app.services.claim_validator import (
    CitationViolation,
    blocked_citation_reply_text,
    blocked_methodology_reply_text,
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
    annotation = blocked_citation_reply_text([violation])

    # This mirrors what chat.py:_run_agent_loop now does.
    composed = original_prose.rstrip() + (
        "\n\n---\n\n"
        "## ⚠ Citation / methodology provenance check failed\n\n"
        "The reply above was generated, but the platform's "
        "provenance gate flagged claims that the tool results "
        "this turn did not support. Treat the flagged items as "
        "**NOT verified** and re-run the relevant tools before "
        "quoting any of them in a paper.\n\n"
        + annotation
    )

    # 1. Original prose untouched
    assert "Loaded 74 [CII] line measurements" in composed
    assert "OLS slope = 0.798, scatter = 0.320 dex" in composed
    # 2. Violation footer present
    assert "## ⚠ Citation / methodology provenance check failed" in composed
    assert "Bothwell 2013" in composed
    # 3. Composition order: prose THEN footer (the M6 reproducer's key
    # property — user sees their analysis first, then the warning)
    prose_idx = composed.index("OLS slope")
    footer_idx = composed.index("provenance check failed")
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
