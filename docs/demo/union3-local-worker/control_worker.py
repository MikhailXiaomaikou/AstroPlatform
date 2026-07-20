#!/usr/bin/env python
"""Run the production Union3 reconciler as a local control-worker stand-in.

This helper does not manufacture state or bypass a gate. It calls the same
registered source processor and PostgreSQL-authoritative research reconciler
that Celery/Beat wake in the hosted control plane. The demo runner uses it
because Render and a general-purpose Celery broker are not part of a
self-contained laptop recording.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


async def _run(*, once: bool, interval: float) -> int:
    from app.tasks.union3_research_tasks import _reconcile
    from app.tasks.union3_source_tasks import _process, _queued_source_ids

    while True:
        processed_sources = 0
        for source_id in await _queued_source_ids():
            result = await _process(source_id)
            if result in {"COMPLETED", "already_completed"}:
                processed_sources += 1
        result = await _reconcile()
        if processed_sources or result["verified"] or result["finalized"]:
            print(
                json.dumps(
                    {
                        "at": datetime.now(timezone.utc).isoformat(),
                        "processed_sources": processed_sources,
                        **result,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        if once:
            return 0
        await asyncio.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Poll the real Union3 verification/finalization reconciler"
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=0.75)
    args = parser.parse_args()
    return asyncio.run(_run(once=args.once, interval=max(0.1, args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
