#!/usr/bin/env python3
"""Citation-pool reachability audit.

For each active cosmology dataset entry, checks that every declared bibcode,
arXiv identifier, and DOI is REACHABLE through the entry's actual tool-result
shape — i.e., calling the machine-readable tool would return a result whose
``provenance`` block carries that identifier.

This catches the failure mode where a manifest cites a paper that the
claim_validator's tool-result harvester can't actually surface, so the
LLM can never legitimately cite the paper (the citation would be
hard-blocked even when the underlying data exists).

Exit code 0 only when every registered citation can be reached via
get_cosmology_dataset(key).provenance harvest. Intended as a
pre-researcher-alpha audit, not a per-PR gate.

Usage:
    python scripts/audit_citation_pool.py
    python scripts/audit_citation_pool.py --json out.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
from typing import Any

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


_IDENTIFIER_FIELDS = ("bibcode", "arxiv", "doi")


def _normalize_identifier(kind: str, value: str) -> str:
    text = value.strip()
    if kind == "doi":
        text = text.removeprefix("https://doi.org/").removeprefix("doi:").lower()
    elif kind == "arxiv":
        text = text.removeprefix("https://arxiv.org/abs/").removeprefix("arXiv:")
    return text


def _identifiers_of(entry: Any) -> dict[str, set[str]]:
    out = {kind: set() for kind in _IDENTIFIER_FIELDS}
    for c in (entry.citations or ()):
        for kind in _IDENTIFIER_FIELDS:
            value = getattr(c, kind, None)
            if isinstance(value, str) and value.strip():
                out[kind].add(_normalize_identifier(kind, value))
    return out


def _identifiers_reachable_via_tool_result(entry: Any) -> dict[str, set[str]]:
    """Return identifiers actually exposed by the entry's tool-result shape.

    Do not inject ``entry.citations`` here: that was the object being audited
    and made reachability a tautology even when ``to_dict()`` exposed nothing.
    """
    found = {kind: set() for kind in _IDENTIFIER_FIELDS}

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in found and isinstance(v, str) and v.strip():
                    found[k].add(_normalize_identifier(k, v))
                walk(v)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)

    walk(entry.to_dict())
    return found


def _bibcodes_of(entry: Any) -> set[str]:
    """Backward-compatible helper used by older audit tests."""
    return _identifiers_of(entry)["bibcode"]


def _bibcodes_reachable_via_tool_result(entry: Any) -> set[str]:
    return _identifiers_reachable_via_tool_result(entry)["bibcode"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args()

    from app.services.cosmology_likelihoods import _REGISTRY as REGISTRY

    unreachable: dict[str, dict[str, list[str]]] = {}
    declared_counts = {kind: 0 for kind in _IDENTIFIER_FIELDS}
    for key, entry in sorted(REGISTRY.items()):
        declared = _identifiers_of(entry)
        reachable = _identifiers_reachable_via_tool_result(entry)
        missing_by_kind: dict[str, list[str]] = {}
        for kind in _IDENTIFIER_FIELDS:
            declared_counts[kind] += len(declared[kind])
            missing = sorted(declared[kind] - reachable[kind])
            if missing:
                missing_by_kind[kind] = missing
        if missing_by_kind:
            unreachable[key] = missing_by_kind

    payload = {
        "suite": "citation_pool_audit",
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "n_entries": len(REGISTRY),
        "n_identifiers_declared": declared_counts,
        "n_bibcodes_declared": declared_counts["bibcode"],
        "n_entries_with_unreachable_identifiers": len(unreachable),
        "n_entries_with_unreachable_bibcodes": sum(
            1 for missing in unreachable.values() if missing.get("bibcode")
        ),
        "unreachable_by_entry_and_kind": unreachable,
        "unreachable_by_entry": unreachable,
    }
    print(json.dumps(payload, indent=2, default=str))
    if args.json:
        with open(args.json, "w") as fp:
            json.dump(payload, fp, indent=2, default=str)

    return 0 if not unreachable else 1


if __name__ == "__main__":
    raise SystemExit(main())
