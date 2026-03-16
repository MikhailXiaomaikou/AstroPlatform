"""Tests for data source connectors and the retry decorator."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.connectors.retry import with_retry


# ── Retry decorator tests ──


class TestWithRetry:
    """Test the with_retry decorator logic."""

    async def test_succeeds_first_try(self):
        call_count = 0

        @with_retry(max_retries=3, base_delay=0.01)
        async def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await succeed()
        assert result == "ok"
        assert call_count == 1

    async def test_retries_then_succeeds(self):
        call_count = 0

        @with_retry(max_retries=3, base_delay=0.01)
        async def fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError(f"fail #{call_count}")
            return "recovered"

        result = await fail_twice()
        assert result == "recovered"
        assert call_count == 3

    async def test_exhausts_retries_and_raises(self):
        call_count = 0

        @with_retry(max_retries=2, base_delay=0.01)
        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("permanent failure")

        with pytest.raises(ValueError, match="permanent failure"):
            await always_fail()

        # 1 initial + 2 retries = 3 total
        assert call_count == 3

    async def test_only_retries_specified_exceptions(self):
        call_count = 0

        @with_retry(max_retries=3, base_delay=0.01, retryable_exceptions=(ConnectionError,))
        async def wrong_error():
            nonlocal call_count
            call_count += 1
            raise TypeError("not retryable")

        with pytest.raises(TypeError, match="not retryable"):
            await wrong_error()

        # Should fail immediately since TypeError is not retryable
        assert call_count == 1

    async def test_respects_max_delay(self):
        """Verify the delay is capped at max_delay."""
        call_count = 0

        @with_retry(max_retries=2, base_delay=100.0, max_delay=0.01, backoff_factor=10.0)
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("flaky")
            return "ok"

        # Should finish quickly because max_delay is tiny
        result = await flaky()
        assert result == "ok"


# ── Integration tests (hit real APIs, skipped by default) ──


@pytest.mark.integration
class TestSDSSConnectorIntegration:
    """Integration tests for SDSSConnector — requires internet access."""

    async def test_search_m31(self):
        from app.connectors.sdss import SDSSConnector

        connector = SDSSConnector()
        # M31 coordinates: RA=10.6847, Dec=41.2687
        results = await connector.search("M31", ra=10.6847, dec=41.2687, radius=0.05)
        assert isinstance(results, list)
        for obj in results:
            assert obj.source == "sdss"
            assert obj.ra is not None
            assert obj.dec is not None


@pytest.mark.integration
class TestGaiaConnectorIntegration:
    """Integration tests for GaiaConnector — requires internet access."""

    async def test_search_m31(self):
        from app.connectors.gaia import GaiaConnector

        connector = GaiaConnector()
        results = await connector.search("M31", ra=10.6847, dec=41.2687, radius=0.01)
        assert isinstance(results, list)


@pytest.mark.integration
class TestSIMBADConnectorIntegration:
    """Integration tests for SIMBADConnector — requires internet access."""

    async def test_search_m31(self):
        from app.connectors.simbad import SIMBADConnector

        connector = SIMBADConnector()
        results = await connector.search("M31")
        assert isinstance(results, list)
        assert len(results) > 0
        assert results[0].source == "simbad"


# ── Advanced retry decorator tests ──


class TestWithRetryAdvanced:
    """Additional tests for with_retry edge cases."""

    async def test_zero_retries(self):
        """With max_retries=0, failure should raise immediately without retrying."""
        call_count = 0

        @with_retry(max_retries=0, base_delay=0.01)
        async def fail_once():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("immediate failure")

        with pytest.raises(RuntimeError, match="immediate failure"):
            await fail_once()

        assert call_count == 1

    async def test_preserves_return_value(self):
        """Verify the decorated function's return value is passed through."""
        @with_retry(max_retries=1, base_delay=0.01)
        async def returns_complex():
            return {"data": [1, 2, 3], "nested": {"key": "value"}}

        result = await returns_complex()
        assert result == {"data": [1, 2, 3], "nested": {"key": "value"}}

    async def test_preserves_function_name(self):
        """Verify functools.wraps preserves __name__."""
        @with_retry(max_retries=2, base_delay=0.01)
        async def my_special_function():
            return True

        assert my_special_function.__name__ == "my_special_function"

    async def test_concurrent_retries(self):
        """Run two retrying functions concurrently with asyncio.gather; both should work independently."""
        count_a = 0
        count_b = 0

        @with_retry(max_retries=2, base_delay=0.01)
        async def func_a():
            nonlocal count_a
            count_a += 1
            if count_a < 2:
                raise ConnectionError("a fails first")
            return "a_ok"

        @with_retry(max_retries=2, base_delay=0.01)
        async def func_b():
            nonlocal count_b
            count_b += 1
            if count_b < 3:
                raise ConnectionError("b fails twice")
            return "b_ok"

        results = await asyncio.gather(func_a(), func_b())
        assert results[0] == "a_ok"
        assert results[1] == "b_ok"
        assert count_a == 2
        assert count_b == 3
