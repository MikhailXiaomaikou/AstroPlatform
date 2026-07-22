"""Shared fail-closed evidence policy for non-formal Foundry output."""

from __future__ import annotations

import re
from typing import Any


NON_FORMAL_EVIDENCE_CLASS = "NON_FORMAL_DEMO"

_FORMAL_TEXT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bsupported\b",
        r"\bscientific[_ -]?verdict\b[\"']?\s*[:=]\s*[\"']?supported\b",
        r"\bstatus\b[\"']?\s*[:=]\s*[\"']?supported\b",
        (
            r"\b(?:publication[_ -]?ready|claim[_ -]?eligible|"
            r"evidence[_ -]?pack[_ -]?allowed)\b[\"']?\s*[:=]\s*"
            r"[\"']?(?:true|yes|1)\b"
        ),
        (
            r"\bevidence[_ -]?class\b[\"']?\s*[:=]\s*[\"']?"
            r"(?:formal|registered|publication[_ -]?ready)\b"
        ),
    )
)


def contains_formal_claim_escape(value: Any) -> bool:
    """Return whether nested candidate output impersonates formal evidence."""

    if isinstance(value, (list, tuple)):
        return any(contains_formal_claim_escape(item) for item in value)
    if not isinstance(value, dict):
        return False
    for raw_key, item in value.items():
        key = str(raw_key).strip().lower()
        if key in {
            "publication_ready",
            "claim_eligible",
            "evidence_pack_allowed",
        } and item is not False:
            return True
        if key in {"scientific_verdict", "status"} and (
            str(item).strip().upper() == "SUPPORTED"
        ):
            return True
        if key == "evidence_class" and item != NON_FORMAL_EVIDENCE_CLASS:
            return True
        if key in {
            "evidence_pack",
            "evidence_pack_id",
            "formal_evidence_pack",
        } and item:
            return True
        if contains_formal_claim_escape(item):
            return True
    return False


def contains_formal_claim_escape_text(value: bytes | str) -> bool:
    """Detect formal-evidence fields printed through untrusted text streams."""

    text = (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else str(value)
    )
    return any(pattern.search(text) is not None for pattern in _FORMAL_TEXT_PATTERNS)


__all__ = [
    "NON_FORMAL_EVIDENCE_CLASS",
    "contains_formal_claim_escape",
    "contains_formal_claim_escape_text",
]
