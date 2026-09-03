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
    _assigned_parameter,
    _claimable_current_values,
    _is_interval_idiom,
    _parameter_assignment_before,
    _reply_number_spans,
    _Token,
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
    # A task-list marker is a marker too: "- [x] APPROVED by reviewer: ..."
    # left "[x]" in front of the lookahead and shipped unmarked (Codex review
    # 2026-09-03).  It consumes a fixed three-or-four character token, so it
    # adds no new way to split a whitespace run.
    # A table-cell delimiter is a marker too: "| APPROVED by reviewer: ... |"
    # shipped unmarked (Codex review 2026-09-03, PRRT_kwDORoeoE86evFte).
    r"(?:[-*>#|][ \t]*|[0-9]{1,3}[.)][ \t]*|\[[ \txX]?\][ \t]*){0,8}"
    # A bold span can wrap the verdict WORD alone: "**APPROVED** by
    # reviewer: ..." consumed the opening ** and then the closing ** stopped
    # the lookahead from matching (Codex review 2026-09-03).  The emphasis
    # markers inside the phrase are skipped when the phrase is tested.
    r"(?:\*\*|__|\*)?)"
    # The platform's OWN vocabulary counts too.  The review lane stores the
    # verdict as review_status == "APPROVED" / decision == "APPROVED"
    # (services/union3_research_loop.py), so "Review status: APPROVED" and
    # "Decision: APPROVED" are the natural renderings and were shipping
    # unmarked (Codex review 2026-09-03).  A bare "APPROVED" counts only when
    # a separator follows it, so ordinary prose beginning with the word is
    # left alone.
    r"(?=(?:draft[ \t]*(?:\*\*|__|\*)?[ \t]+claim\b"
    r"|approved(?:\*\*|__|\*)?[ \t]+by\b"
    r"|reviewer(?:\*\*|__|\*)?[ \t]+approved\b"
    # Paired emphasis may wrap the LABEL as well as the verdict word:
    # "**Review status:** APPROVED" closes the bold after the colon and
    # "**Review status**: APPROVED" before it, and both shipped unmarked
    # because the colon had to follow "status" directly (Codex review
    # 2026-09-03, PRRT_kwDORoeoE86ethcM).
    r"|(?:review[ \t_-]*)?status(?:\*\*|__|\*)?[ \t]*:(?:\*\*|__|\*)?[ \t]*"
    r"(?:\*\*|__|\*)?approved\b"
    r"|decision(?:\*\*|__|\*)?[ \t]*:(?:\*\*|__|\*)?[ \t]*"
    r"(?:\*\*|__|\*)?approved\b"
    r"|approved(?:\*\*|__|\*)?[ \t]*[:\u2014-]))"
)
_MARKER = "NOT APPROVED - "


_FENCE_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")
# Four spaces, or a tab within the first three columns, open a Markdown
# indented code block.
_INDENTED_CODE_RE = re.compile(r"^(?: {4}|[ ]{0,3}\t)")


def _fenced_lines(lines: list[str]) -> list[bool]:
    """Which lines sit inside a fenced code block, fences included.

    A reply that TELLS the user not to write an approval line quotes one
    inside a fence, and rewriting that example marked an otherwise clean
    response as limited (Codex review 2026-09-03).

    A fence closes only on a run of the SAME character at least as long as
    the one that opened it (CommonMark).  Toggling on any fence line let the
    inner backtick fence of a ``~~~~markdown`` example close the outer tilde
    fence, and the quoted verdict was stamped (Codex review 2026-09-03,
    PRRT_kwDORoeoE86etNOq).
    """
    opener = ""
    flags: list[bool] = []
    for line in lines:
        match = _FENCE_RE.match(line)
        if match is None:
            flags.append(bool(opener))
            continue
        fence = match.group("fence")
        if not opener:
            opener = fence
        elif fence[0] == opener[0] and len(fence) >= len(opener):
            opener = ""
        flags.append(True)
    return flags


def _indented_code_lines(lines: list[str], fenced: list[bool]) -> list[bool]:
    """Which lines are Markdown indented code: four spaces or a tab.

    The prefix consumed arbitrary leading whitespace, so an example quoted
    the indented way was rewritten like prose (Codex review 2026-09-03,
    PRRT_kwDORoeoE86ethcV).  An indented code block cannot interrupt a
    paragraph, so an indented line directly under a prose line is a lazy
    continuation of that paragraph and is still read: it renders as prose,
    and a stamp in it is a stamp.
    """
    flags: list[bool] = []
    block_may_open = True  # start of text, or after a blank or fence line
    for index, line in enumerate(lines):
        if fenced[index] or not line.strip():
            flags.append(False)
            block_may_open = True
        elif block_may_open and _INDENTED_CODE_RE.match(line):
            flags.append(True)
        else:
            flags.append(False)
            block_may_open = False
    return flags


# The H0 anchor that compare_luminosity_distances reports is stated as "the
# anchor is 67.36"; honesty's subject grammar has no word for it, so the
# label is bound here with the same symbol-or-copula rule.
_ANCHOR_ASSIGNMENT_BEFORE_RE = re.compile(
    r"\banchor\b[^\n;]{0,28}?(?:[=:~≈]|\b(?:is|was|of|at|equals?)\b)\s*$",
    re.IGNORECASE,
)


def _bound_to_a_parameter(line: str, token: _Token) -> bool:
    """True when the token is the VALUE of a named parameter or statistic.

    ``H0 = 67.36``, ``H0 is 67.36`` and ``h = 0.6736`` bind; ``67 galaxies``
    does not.  The rules are honesty's own per-token ones: the named
    parameter assignment, the little-h token, and the unlabelled statistic
    subject ("the median is 67.36").
    """
    if token.little_h or _assigned_parameter(line, token) is not None:
        return True
    before = line[max(0, token.start - 48):token.start]
    return _parameter_assignment_before(before) or bool(
        _ANCHOR_ASSIGNMENT_BEFORE_RE.search(before)
    )


def _states_a_claimable_value(line: str, claimable: set[float]) -> bool:
    """True when this line binds a claimable result's value to a parameter.

    Every token is read, little-h included: "h = 0.6736" is the standard
    equivalent of H0 = 67.36 and the converted token is the one that matches.
    A coverage level is NOT such a number: "Draft claim: the 68% credible
    interval remains to be calculated" was stamped NOT APPROVED because 68
    fell within 1% of a claimable 67.36 (Codex review 2026-09-03).
    Nor is a number that is merely NEAR the result: "Draft claim: 67
    galaxies pass the cut" was stamped because a galaxy count fell within 1%
    of a claimable 67.36.  Only a value assigned to a parameter -- "H0 =
    67.36", "H0 is 67.36", "h = 0.6736" -- states the claim (Codex review
    2026-09-03, PRRT_kwDORoeoE86ethcQ).
    """
    return any(
        any(
            math.isclose(token.value, value, rel_tol=0.01, abs_tol=1e-12)
            for value in claimable
        )
        for token in _reply_number_spans(line)
        if _bound_to_a_parameter(line, token)
        and not (token.is_percent and _is_interval_idiom(line, token))
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
    fenced = _fenced_lines(lines)
    indented = _indented_code_lines(lines, fenced)
    marked = 0
    out: list[str] = []
    for index, line in enumerate(lines):
        match = (
            None if fenced[index] or indented[index]
            else _APPROVAL_LINE_RE.match(line)
        )
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
