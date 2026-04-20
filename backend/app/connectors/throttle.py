"""Per-connector upstream rate limiting.

A stdlib-only token bucket + semaphore combo. Each connector class gets
one shared UpstreamThrottle instance that enforces both:
- max concurrent requests (asyncio.Semaphore)
- max steady-state request rate (tokens replenished at `rate` per second)

Defaults come from the public rate-limit guidance of each archive:

    Gaia TAP       5 req/s,  2 concurrent  (ESA Gaia Archive SLA)
    SDSS CasJobs   2 req/s,  1 concurrent
    VizieR        10 req/s,  4 concurrent
    SIMBAD TAP    10 req/s,  4 concurrent
    MAST           5 req/s,  2 concurrent
    other          3 req/s,  1 concurrent  (conservative fallback)

Callers use `async with throttle: ...` to wait for both capacity and
a rate token. The throttle never blocks longer than `timeout` seconds
(default 30) — on overflow it raises ThrottleTimeout so the caller can
fail fast rather than queueing indefinitely.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class ThrottleTimeout(RuntimeError):
    """Raised when the upstream rate limit couldn't be acquired in time."""


@dataclass
class ThrottleConfig:
    rate_per_sec: float = 3.0
    max_concurrent: int = 1
    wait_timeout: float = 30.0


# Default per-connector policy; callers can override via set_policy()
_POLICIES: dict[str, ThrottleConfig] = {
    "gaia": ThrottleConfig(rate_per_sec=5.0, max_concurrent=2),
    "sdss": ThrottleConfig(rate_per_sec=2.0, max_concurrent=1),
    "sdss_spec": ThrottleConfig(rate_per_sec=2.0, max_concurrent=1),  # Bug 11 path C
    "vizier": ThrottleConfig(rate_per_sec=10.0, max_concurrent=4),
    "simbad": ThrottleConfig(rate_per_sec=10.0, max_concurrent=4),
    "mast": ThrottleConfig(rate_per_sec=5.0, max_concurrent=2),
    "ned": ThrottleConfig(rate_per_sec=5.0, max_concurrent=2),
    "allwise": ThrottleConfig(rate_per_sec=3.0, max_concurrent=1),
    "twomass": ThrottleConfig(rate_per_sec=5.0, max_concurrent=2),
    "lamost": ThrottleConfig(rate_per_sec=3.0, max_concurrent=1),
    "desi": ThrottleConfig(rate_per_sec=3.0, max_concurrent=1),
    "panstarrs": ThrottleConfig(rate_per_sec=3.0, max_concurrent=1),
}


class UpstreamThrottle:
    """Async token-bucket + concurrency gate for one upstream source."""

    def __init__(self, config: ThrottleConfig):
        self._cfg = config
        self._sem = asyncio.Semaphore(config.max_concurrent)
        self._tokens = float(config.max_concurrent)
        self._last_refill = time.monotonic()
        self._token_lock = asyncio.Lock()

    async def _acquire_token(self) -> None:
        deadline = time.monotonic() + self._cfg.wait_timeout
        while True:
            async with self._token_lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    float(self._cfg.max_concurrent),
                    self._tokens + elapsed * self._cfg.rate_per_sec,
                )
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                needed = 1.0 - self._tokens
                wait = needed / self._cfg.rate_per_sec
            if time.monotonic() + wait > deadline:
                raise ThrottleTimeout(
                    f"upstream throttle timeout after "
                    f"{self._cfg.wait_timeout:.1f}s "
                    f"(rate={self._cfg.rate_per_sec}/s, "
                    f"concurrent={self._cfg.max_concurrent})"
                )
            await asyncio.sleep(wait)

    async def __aenter__(self):
        await self._sem.acquire()
        try:
            await self._acquire_token()
        except BaseException:
            self._sem.release()
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._sem.release()
        return False


_cache: dict[str, UpstreamThrottle] = {}


def get_throttle(connector: str) -> UpstreamThrottle:
    """Return the shared UpstreamThrottle for a connector name."""
    key = connector.lower()
    if key not in _cache:
        cfg = _POLICIES.get(key, ThrottleConfig())
        _cache[key] = UpstreamThrottle(cfg)
    return _cache[key]


def set_policy(connector: str, config: ThrottleConfig) -> None:
    """Override the default policy for a connector (test/runtime tuning)."""
    _POLICIES[connector.lower()] = config
    _cache.pop(connector.lower(), None)


def reset() -> None:
    """Drop all cached throttle instances (test helper)."""
    _cache.clear()
