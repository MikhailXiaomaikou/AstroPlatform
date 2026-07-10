#!/usr/bin/env python3
"""Wait until the configured database matches this image's Alembic head.

Render deploys web services and background workers independently.  The backend
owns the one production migration command, while worker/beat processes use this
read-only gate so new task code cannot start against the previous schema.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path


# Executing ``python scripts/wait_for_schema_head.py`` puts only scripts/ on
# sys.path. Add the backend root so the same command works in Docker/Render and
# from a developer checkout without relying on an ambient PYTHONPATH.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


Probe = Callable[[], Awaitable[tuple[bool, str]]]


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


async def wait_for_schema_head(
    *,
    timeout_seconds: float,
    interval_seconds: float,
    probe: Probe | None = None,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, str]:
    """Poll the read-only schema probe until it passes or the deadline expires."""
    if timeout_seconds <= 0 or interval_seconds <= 0:
        raise ValueError("schema wait timeout and interval must be positive")
    if probe is None:
        from app.api.health import _probe_schema_head

        probe = _probe_schema_head

    deadline = clock() + timeout_seconds
    last_status = "not_checked"
    while True:
        ok, last_status = await probe()
        if ok:
            return True, last_status
        remaining = deadline - clock()
        if remaining <= 0:
            return False, last_status
        await sleep(min(interval_seconds, remaining))


async def _main() -> int:
    timeout = _positive_float("SCHEMA_WAIT_TIMEOUT_SECONDS", 900.0)
    interval = _positive_float("SCHEMA_WAIT_INTERVAL_SECONDS", 5.0)
    ok, status = await wait_for_schema_head(
        timeout_seconds=timeout,
        interval_seconds=interval,
    )
    if ok:
        print(f"schema gate passed: {status}", flush=True)
        return 0
    print(
        f"schema gate timed out after {timeout:g}s: {status}",
        file=sys.stderr,
        flush=True,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
