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
    _reply_number_tokens,
)

APPROVAL_STATE_NONE = "none"

# Line-anchored on purpose: "the draft claim above" mid-sentence is prose,
# while a line that OPENS with approval language is presenting a verdict.
_APPROVAL_LINE_RE = re.compile(
    r"(?im)^(?P<prefix>[ \t]*(?:[-*>]+[ \t]*)?(?:\*\*)?)"
    r"(?=(?:draft[ \t]+claim|approved[ \t]+by|reviewer[ \t]+approved)\b)"
)
_MARKER = "NOT APPROVED - "


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

    marked = 0
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        match = _APPROVAL_LINE_RE.match(line)
        if match and not line.lstrip().startswith(_MARKER):
            if any(
                any(
                    math.isclose(token, value, rel_tol=0.01, abs_tol=1e-12)
                    for value in claimable
                )
                for token in _reply_number_tokens(line)
            ):
                prefix = match.group("prefix")
                line = prefix + _MARKER + line[len(prefix):]
                marked += 1
        out.append(line)
    return "".join(out), marked
