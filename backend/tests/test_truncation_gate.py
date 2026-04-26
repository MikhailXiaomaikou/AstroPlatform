"""PART AC C2 — text-shape detection of mid-sentence reply truncation.

Locks the regression: the M3 audit caught a chat round where the
model's stop_reason was "stop" / "end_turn" but the prose obviously
ended mid-sentence ("Let me search for additional [CII] datasets:")
because the provider doesn't always return max_tokens / length even
when the reply was effectively cut. C-X1's gate only triggered on
those two stop reasons; PART AC C2 adds a text-shape fallback signal.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "reply, expected, label",
    [
        # Truncated — trailing punctuation
        ("Let me search for additional [CII] datasets:", True, "colon end (M3 reproducer)"),
        ("Here are the values,", True, "comma end"),
        ("Calling the next tool —", True, "em-dash end"),
        ("Calling the next tool –", True, "en-dash end"),
        ("See section (", True, "unclosed paren"),
        ("The list is [", True, "unclosed bracket"),
        ("The shape is {", True, "unclosed brace"),
        # Truncated — connective trailing word
        ("I will fit either OLS or", True, "trailing 'or'"),
        ("Working with the cached data and", True, "trailing 'and'"),
        ("Compared to the", True, "trailing 'the'"),
        ("Either fit on a", True, "trailing 'a'"),
        ("Looking from", True, "trailing 'from'"),
        # Clean endings
        ("The slope is 1.05 plus or minus 0.05.", False, "sentence-final period"),
        ("Analysis complete!", False, "exclamation"),
        ("Was that the right answer?", False, "question mark"),
        ("...", False, "ellipsis"),
        ("'cached'", False, "trailing apostrophe close"),
        ('"final"', False, "trailing quote close"),
        ("see ref [1]", False, "closing bracket"),
        ("Done.", False, "minimal complete sentence"),
        # Edge cases
        ("", False, "empty reply"),
        ("   \n  ", False, "whitespace only"),
        ("<tools_returned_nothing failed_tools='x'/>", False, "abstention tag"),
        ("Here is code:\n\n```python\nrows = get_cached_results('latest_adql')",
         False, "unclosed code fence (let raw text through)"),
        ("M-class", False, "trailing ascii hyphen kept legitimate"),
        ("section A.B.C", False, "abbreviation periods OK"),
    ],
)
def test_reply_looks_truncated(reply: str, expected: bool, label: str) -> None:
    from app.api.chat import _reply_looks_truncated

    assert _reply_looks_truncated(reply) is expected, (
        f"{label}: expected {expected}, got {_reply_looks_truncated(reply)}"
    )
