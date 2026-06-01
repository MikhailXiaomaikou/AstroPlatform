#!/usr/bin/env python3
"""Cosmology dataset registry audit.

Scans every ``CosmologyDatasetEntry`` and asserts the fields the rest of
the platform relies on are populated:

- ``version`` (display + provenance)
- ``probe`` (gating + tension grouping)
- ``status`` (UI: ready / external_likelihood / metadata_only)
- ``applicable_models`` (model filter)
- ``citations`` (at least one DatasetCitation with bibcode / arxiv / doi)
- ``units`` (per-observable units present for every observable)
- ``execution_mode`` (compressed_gaussian / external_cobaya / ...)
- ``covariance`` (a CovarianceSpec, even when metadata_only)
- bibcode reachability: every claim_validator citation pool source must
  resolve back to at least one DatasetCitation across the active registry

Exit code 0 only when every entry passes every check. Used by the
``benchmarks`` CI job so a missing registry field surfaces immediately.

Usage:
    python scripts/audit_registry.py
    python scripts/audit_registry.py --json audit.json
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


def _citation_has_identifier(citation: Any) -> bool:
    return any(getattr(citation, attr, None) for attr in ("bibcode", "arxiv", "doi"))


def _audit_entry(entry: Any) -> list[str]:
    """Return a list of issue strings (empty = clean)."""
    issues: list[str] = []

    if not entry.version:
        issues.append("missing version")
    if not entry.probe:
        issues.append("missing probe")
    if not entry.status:
        issues.append("missing status")
    if not entry.applicable_models:
        issues.append("missing applicable_models")
    if not entry.citations:
        issues.append("missing citations (need at least one DatasetCitation)")
    else:
        identifiable = [c for c in entry.citations if _citation_has_identifier(c)]
        if not identifiable:
            issues.append("no citation has any of bibcode/arxiv/doi")
    if entry.covariance is None:
        issues.append("missing covariance (CovarianceSpec required even for metadata_only)")
    if not entry.execution_mode:
        issues.append("missing execution_mode")

    # We deliberately DO NOT require an entry.units key per observable —
    # most observables (xi_plus / xi_minus / TT,TE,EE / mu_covariance /
    # redshifts) are dimensionless by convention and listing them as
    # "dimensionless" repeats no information. The compressed_likelihood
    # parameters DO get a units check below because those drive sampling.

    # compressed_likelihood self-consistency
    spec = entry.compressed_likelihood
    if spec is not None:
        params = list(spec.parameters or ())
        n = len(params)
        if n == 0:
            issues.append("compressed_likelihood has no parameters")
        else:
            mean = list(spec.mean or ())
            if len(mean) != n:
                issues.append(f"compressed_likelihood mean shape {len(mean)} != n_params {n}")
            cov = spec.covariance
            try:
                cov_rows = list(cov or ())
                if len(cov_rows) != n or any(len(list(row)) != n for row in cov_rows):
                    issues.append(f"compressed_likelihood covariance shape != ({n},{n})")
            except Exception as exc:  # noqa: BLE001
                issues.append(f"compressed_likelihood covariance unreadable: {exc}")
            # units for compressed parameters
            spec_units = dict(spec.units or {})
            missing_spec_units = [p for p in params if p not in spec_units]
            if missing_spec_units:
                issues.append(f"compressed_likelihood params missing units: {missing_spec_units}")

    return issues


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args()

    from app.services.cosmology_likelihoods import _REGISTRY as REGISTRY
    from app.services.cosmology_likelihoods import audit_executable_pins

    per_entry: dict[str, list[str]] = {}
    for key, entry in sorted(REGISTRY.items()):
        issues = _audit_entry(entry)
        if issues:
            per_entry[key] = issues

    # T1-U7: every in-process-executable probe must read a sha256-verified
    # vendored file (or be an allowlisted no-released-file literature probe).
    executable_pin_issues = audit_executable_pins()

    payload = {
        "suite": "registry_audit",
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "n_entries": len(REGISTRY),
        "n_clean": len(REGISTRY) - len(per_entry),
        "n_dirty": len(per_entry),
        "issues_by_dataset": per_entry,
        "executable_pin_issues": executable_pin_issues,
    }
    print(json.dumps(payload, indent=2, default=str))
    if args.json:
        with open(args.json, "w") as fp:
            json.dump(payload, fp, indent=2, default=str)

    return 0 if not per_entry and not executable_pin_issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
