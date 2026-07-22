"""Shared fail-closed evidence policy for non-formal Foundry output."""

from __future__ import annotations

from typing import Any


NON_FORMAL_EVIDENCE_CLASS = "NON_FORMAL_DEMO"


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
        if key == "scientific_verdict" and str(item).strip().upper() == "SUPPORTED":
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


__all__ = ["NON_FORMAL_EVIDENCE_CLASS", "contains_formal_claim_escape"]
