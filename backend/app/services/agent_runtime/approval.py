"""Mark approval language that no stored review backs.

A human approval in this platform is an append-only ``ClaimAuditReview`` row
bound to the claim hash, the source hash and the anchor ids, written by a
reviewer who is not the owner (``models/workspace_records.py``,
``services/union3_research_loop.py``).  It lives behind three default-off
flags and is not reachable from the chat path at all.

So in chat the only honest approval state is "none".  Nothing in the tree
emits approval language today, which is exactly why this guard is cheap now:
a model that writes "APPROVED by human reviewer: H0 = 67.36" would otherwise
hand the reader a number wearing a governance stamp the platform never
issued, and every numeric gate would let it through because the number does
come from a claimable tool result.

This module only annotates.  It never changes a claim's tier, never sets
``publication_ready``, and never reads a review row — a reply cannot become
approved here, only marked as not approved.
"""

from __future__ import annotations

import math
import re

from app.services.agent_runtime.honesty import (
    _claimable_current_values,
    _is_interval_idiom,
    _reply_number_spans,
)

APPROVAL_STATE_NONE = "none"

# Line-anchored on purpose: "the draft claim above" mid-sentence is prose,
# while a line that OPENS with approval language is presenting a verdict.
#
# The accepted prefix covers the Markdown shapes a model actually writes for a
# verdict line: bullets/blockquotes ("- ", "> "), ATX headings ("### "),
# ordered-list markers ("1. ", "2) ") and a bold run ("**").  These NEST in
# real output ("> ### APPROVED by ...", "- > Draft claim: ..."), and a single
# optional group accepted only one of them, so nested verdict lines shipped
# unmarked (Codex review 2026-09-03).
#
# Linearity matters here (a CodeQL finding on this repository was exactly the
# "optional group between two whitespace runs" shape).  Each repetition
# consumes exactly ONE marker character (or one numbered marker) plus its
# trailing spaces, and every iteration must begin with a non-space marker
# character, so no whitespace run can be split between two iterations and a
# backtracker has nothing to explore.  The repetition is bounded as well.
# [ \t] is used instead of \s so a newline can never be consumed inside a
# line-anchored prefix.
_APPROVAL_LINE_RE = re.compile(
    r"(?im)^(?P<prefix>[ \t]*"
    r"(?:[-*>#][ \t]*|[0-9]{1,3}[.)][ \t]*){0,8}"
    r"(?:\*\*)?)"
    r"(?=(?:draft[ \t]+claim|approved[ \t]+by|reviewer[ \t]+approved)\b)"
)
_MARKER = "NOT APPROVED - "


def _states_a_claimable_value(line: str, claimable: set[float]) -> bool:
    """True when this line states a number a claimable result produced.

    Every token is read, little-h included: "h = 0.6736" is the standard
    equivalent of H0 = 67.36 and the converted token is the one that matches.
    A coverage level is NOT such a number: "Draft claim: the 68% credible
    interval remains to be calculated" was stamped NOT APPROVED because 68
    fell within 1% of a claimable 67.36 (Codex review 2026-09-03).
    """
    return any(
        any(
            math.isclose(token.value, value, rel_tol=0.01, abs_tol=1e-12)
            for value in claimable
        )
        for token in _reply_number_spans(line)
        if not (token.is_percent and _is_interval_idiom(line, token))
    )


def _neighbour_states_claim(
    states_claim: list[bool], lines: list[str], index: int
) -> bool:
    """True when the nearest non-blank line on either side states the claim."""
    for step in (-1, 1):
        cursor = index + step
        while 0 <= cursor < len(lines) and not lines[cursor].strip():
            cursor += step
        if 0 <= cursor < len(lines) and states_claim[cursor]:
            return True
    return False


def mark_unapproved_claims(
    reply: str,
    tool_results: object,
    *,
    approval_state: str = APPROVAL_STATE_NONE,
) -> tuple[str, int]:
    """Prefix approval-language lines that carry a tool-matched number.

    A line is marked when it opens with approval language AND states a number
    that matches a claimable current-turn result within 1%: that is the shape
    which turns a real measurement into an apparently sanctioned claim.  A
    line with no such number is left alone, because prose about approval is
    not a claim.
    """
    text = str(reply or "")
    if not text or approval_state != APPROVAL_STATE_NONE:
        return text, 0
    claimable = _claimable_current_values(tool_results)
    if not claimable:
        return text, 0

    lines = text.splitlines(keepends=True)
    states_claim = [_states_a_claimable_value(line, claimable) for line in lines]
    marked = 0
    out: list[str] = []
    for index, line in enumerate(lines):
        match = _APPROVAL_LINE_RE.match(line)
        if match and not line.lstrip().startswith(_MARKER):
            # The verdict may stand on its own line, with the result on the
            # line above or below it -- "H0 = 67.36 km/s/Mpc." then "APPROVED
            # by human reviewer." -- which is the same stamp on the same
            # number and was shipping unmarked (Codex review 2026-09-03).
            if states_claim[index] or _neighbour_states_claim(states_claim, lines, index):
                prefix = match.group("prefix")
                line = prefix + _MARKER + line[len(prefix):]
                marked += 1
        out.append(line)
    return "".join(out), marked
