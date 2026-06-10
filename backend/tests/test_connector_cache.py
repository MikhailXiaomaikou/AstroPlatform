"""Tests for the raw connector response cache."""

import asyncio

import pytest

from app.services import connector_cache as cc


@pytest.fixture(autouse=True)
def _reset_backend(tmp_path, monkeypatch):
    """Force SQLite backend in a tmp dir so tests never touch Redis."""
    monkeypatch.setattr(cc.settings, "connector_cache_backend", "sqlite")
    monkeypatch.setattr(cc.settings, "local_storage_dir", str(tmp_path / "fits"))
    cc.reset_backend()
    cc._inflight.clear()
    yield
    cc.reset_backend()
    cc._inflight.clear()


class TestCacheRoundTrip:
    @pytest.mark.asyncio
    async def test_miss_then_hit(self):
        calls = 0

        async def compute():
            nonlocal calls
            calls += 1
            return [{"name": "NGC 1647", "ra": 71.5, "dec": 19.1}]

        key = "test:miss_then_hit"
        r1 = await cc.get_or_compute(key, compute, ttl=60)
        r2 = await cc.get_or_compute(key, compute, ttl=60)

        assert calls == 1
        assert r1 == r2
        assert r1[0]["name"] == "NGC 1647"

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(self):
        calls = 0

        async def compute():
            nonlocal calls
            calls += 1
            return calls

        key = "test:force_refresh"
        v1 = await cc.get_or_compute(key, compute, ttl=60)
        v2 = await cc.get_or_compute(key, compute, ttl=60, force_refresh=True)

        assert v1 == 1
        assert v2 == 2
        assert calls == 2

    @pytest.mark.asyncio
    async def test_singleflight_dedupes_concurrent_calls(self):
        calls = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_compute():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "result"

        key = "test:singleflight"

        t1 = asyncio.create_task(cc.get_or_compute(key, slow_compute, ttl=60))
        await started.wait()
        t2 = asyncio.create_task(cc.get_or_compute(key, slow_compute, ttl=60))
        await asyncio.sleep(0.05)
        release.set()

        r1, r2 = await asyncio.gather(t1, t2)
        assert calls == 1
        assert r1 == r2 == "result"

    @pytest.mark.asyncio
    async def test_exception_in_compute_is_propagated(self):
        async def fail():
            raise RuntimeError("upstream down")

        key = "test:exception_retry"
        with pytest.raises(RuntimeError, match="upstream down"):
            await cc.get_or_compute(key, fail, ttl=60)

        # Second call retries (failure not cached)
        calls = 0

        async def succeed():
            nonlocal calls
            calls += 1
            return "ok"

        r = await cc.get_or_compute(key, succeed, ttl=60)
        assert r == "ok"
        assert calls == 1


class TestBackendSelection:
    def test_null_backend_disables_cache(self, monkeypatch):
        monkeypatch.setattr(cc.settings, "connector_cache_backend", "null")
        cc.reset_backend()
        backend = cc.get_backend()
        assert backend.name == "null"
        backend.set("k", "v", 60)
        assert backend.get("k") is None
