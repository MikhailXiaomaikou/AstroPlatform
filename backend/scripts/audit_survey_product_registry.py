#!/usr/bin/env python3
"""Audit Rubin, Euclid, and Roman schema fixtures without network access."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys


_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args()

    from app.services.survey_product_registry import (
        SURVEY_SCHEMA_FIXTURE_SHA256,
        audit_survey_product_registry,
        list_survey_product_specs,
    )

    issues = audit_survey_product_registry()
    maturities: list[str] = []
    if not issues:
        maturities = sorted(
            {item["maturity"] for item in list_survey_product_specs()["products"]}
        )
    payload = {
        "suite": "survey_product_registry_audit",
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "fixture_count": len(SURVEY_SCHEMA_FIXTURE_SHA256),
        "execution_available": False,
        "maturities": maturities,
        "issues_by_fixture": issues,
        "status": "PASS" if not issues else "FAIL",
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    print(rendered)
    if args.json:
        pathlib.Path(args.json).write_text(rendered + "\n", encoding="utf-8")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
