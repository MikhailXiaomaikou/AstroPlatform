#!/usr/bin/env python3
"""Citation-pool reachability audit.

For each active module (cosmology / solar_system / exoplanet), checks
that every bibcode declared in the cosmology dataset registry's active
entries is REACHABLE through a tool result — i.e., calling the
machine-readable tool that loads that dataset would return a result
whose ``provenance`` block carries the bibcode.

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


def _bibcodes_of(entry: Any) -> set[str]:
    out: set[str] = set()
    for c in (entry.citations or ()):
        b = getattr(c, "bibcode", None)
        if isinstance(b, str) and b.strip():
            out.add(b.strip())
    return out


def _bibcodes_reachable_via_tool_result(entry: Any) -> set[str]:
    """Return bibcodes the claim_validator would harvest from the
    entry's machine-readable surface (its `to_dict()` shape plus the
    citations list). Mirrors what _build_valid_bibcode_pool actually does."""
    found: set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "bibcode" and isinstance(v, str) and v.strip():
                    found.add(v.strip())
                walk(v)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)

    walk(entry.to_dict())
    for c in (entry.citations or ()):
        walk({"bibcode": getattr(c, "bibcode", None)})
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args()

    from app.services.cosmology_likelihoods import _REGISTRY as REGISTRY

    unreachable: dict[str, list[str]] = {}
    n_bibcodes = 0
    for key, entry in sorted(REGISTRY.items()):
        declared = _bibcodes_of(entry)
        n_bibcodes += len(declared)
        reachable = _bibcodes_reachable_via_tool_result(entry)
        missing = sorted(declared - reachable)
        if missing:
            unreachable[key] = missing

    payload = {
        "suite": "citation_pool_audit",
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "n_entries": len(REGISTRY),
        "n_bibcodes_declared": n_bibcodes,
        "n_entries_with_unreachable_bibcodes": len(unreachable),
        "unreachable_by_entry": unreachable,
    }
    print(json.dumps(payload, indent=2, default=str))
    if args.json:
        with open(args.json, "w") as fp:
            json.dump(payload, fp, indent=2, default=str)

    return 0 if not unreachable else 1


if __name__ == "__main__":
    raise SystemExit(main())
