#!/usr/bin/env python3
"""Compatibility entry point for the strict canonical evidence analyzer.

The former script used Cobaya's progress-file R-1 and could print intervals
without per-parameter rank-normalized R-hat/bulk-ESS certification. All output
now goes through ``canonical_full_likelihood_evidence.py analyze``.

Legacy usage remains valid::

    venv/bin/python scripts/cobaya/analyze_w0wa_run.py [chain_prefix]

Additional arguments are forwarded to the strict analyzer.
"""

from __future__ import annotations

import sys
from typing import Sequence

from canonical_full_likelihood_evidence import main as evidence_main


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    forwarded = ["analyze"]
    if args and not args[0].startswith("-"):
        forwarded.extend(["--chain-prefix", args.pop(0)])
    forwarded.extend(args)
    return evidence_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
