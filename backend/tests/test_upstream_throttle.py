"""Tests for the upstream throttle (stdlib token bucket)."""

import asyncio
import time

import pytest

from app.connectors import throttle as t


@pytest.fixture(autouse=True)
def _reset():
    t.reset()
    yield
    t.reset()


class TestThrottle:
    @pytest.mark.asyncio
    async def test_defaults_registered_for_gaia(self):
        th = t.get_throttle("gaia")
        assert th._cfg.rate_per_sec == 5.0
        assert th._cfg.max_concurrent == 2

    @pytest.mark.asyncio
    async def test_unknown_connector_uses_fallback(self):
        th = t.get_throttle("obscure_source")
        assert th._cfg.rate_per_sec == 3.0
        assert th._cfg.max_concurrent == 1

    @pytest.mark.asyncio
    async def test_concurrent_limit_enforced(self):
        t.set_policy("x", t.ThrottleConfig(rate_per_sec=100.0, max_concurrent=2))
        th = t.get_throttle("x")
        active = 0
        peak = 0

        async def worker():
            nonlocal active, peak
            async with th:
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.05)
                active -= 1

        await asyncio.gather(*(worker() for _ in range(8)))
        assert peak <= 2

    @pytest.mark.asyncio
    async def test_rate_limit_paces_bursts(self):
        t.set_policy("y", t.ThrottleConfig(rate_per_sec=5.0, max_concurrent=10))
        th = t.get_throttle("y")

        async def ping():
            async with th:
                return time.monotonic()

        start = time.monotonic()
        # Drain initial capacity (10 tokens) + 5 more → should need ≥1s of refill
        results = await asyncio.gather(*(ping() for _ in range(15)))
        duration = max(results) - start
        # 15 requests at 5/s with 10 initial tokens → ~1s for the refills
        assert duration >= 0.9, f"throttle too fast: {duration:.2f}s"

    @pytest.mark.asyncio
    async def test_timeout_raised_on_sustained_overflow(self):
        t.set_policy(
            "z",
            t.ThrottleConfig(rate_per_sec=0.5, max_concurrent=1, wait_timeout=0.2),
        )
        th = t.get_throttle("z")

        async def ping():
            async with th:
                await asyncio.sleep(0.01)

        await ping()  # consume the initial token
        with pytest.raises(t.ThrottleTimeout):
            await ping()
