"""Tests for data source connectors and the retry decorator."""

import asyncio

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

        # Default retryable set is transient I/O (ConnectionError/TimeoutError/OSError)
        # as of H12.  Use ConnectionError to exercise the retry path; ValueError
        # now correctly bypasses retries since it indicates a user error.
        @with_retry(max_retries=2, base_delay=0.01)
        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("permanent transient failure")

        with pytest.raises(ConnectionError, match="permanent transient failure"):
            await always_fail()

        # 1 initial + 2 retries = 3 total
        assert call_count == 3

    async def test_non_transient_exception_is_not_retried(self):
        """H12 regression: user errors (ValueError etc.) must NOT be retried."""
        call_count = 0

        @with_retry(max_retries=3, base_delay=0.01)
        async def user_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("bad input")

        with pytest.raises(ValueError, match="bad input"):
            await user_error()

        # No retries — this would previously have been 4.
        assert call_count == 1

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


class TestVizierConnector:
    """Unit tests for Vizier-specific behavior."""

    async def test_search_disables_astroquery_cache(self, monkeypatch):
        from astropy.table import Table
        from app.connectors.vizier import VizierConnector

        calls: dict[str, object] = {}

        class FakeVizier:
            def query_region(self, coord, radius=None, cache=None):
                calls["cache"] = cache
                calls["radius"] = radius
                return [Table({"_RAJ2000": [10.0], "_DEJ2000": [20.0], "Name": ["M31"]})]

        connector = VizierConnector()
        monkeypatch.setattr(connector, "_make_vizier", lambda catalogs=None, row_limit=50: FakeVizier())

        results = await connector.search("M31", ra=10.0, dec=20.0, radius=0.1)

        assert calls["cache"] is False
        assert len(results) == 1
        assert results[0].source == "vizier"

    async def test_fetch_disables_astroquery_cache(self, monkeypatch):
        from astropy.table import Table
        from app.connectors.vizier import VizierConnector

        calls: dict[str, object] = {}

        class FakeVizier:
            def get_catalogs(self, object_id, cache=None):
                calls["cache"] = cache
                calls["object_id"] = object_id
                return [Table({"value": [1.0]})]

        connector = VizierConnector()
        monkeypatch.setattr("astroquery.vizier.Vizier", lambda row_limit=500: FakeVizier())

        result = await connector.fetch("II/246/out")

        assert calls["cache"] is False
        assert calls["object_id"] == "II/246/out"
        assert result.source == "vizier"


class TestSIMBADConnector:
    async def test_common_aliases_use_canonical_name(self, monkeypatch):
        from astropy.table import Table
        from app.connectors.simbad import SIMBADConnector

        calls: dict[str, str] = {}

        class FakeSimbad:
            def query_object(self, name):
                calls["name"] = name
                return Table({"MAIN_ID": ["M 45"], "RA": [56.75], "DEC": [24.12], "OTYPE": ["Cl*"]})

        connector = SIMBADConnector()
        monkeypatch.setattr(connector, "_make_simbad", lambda: FakeSimbad())

        results = await connector.search("Pleiades")

        assert calls["name"] == "M45"
        assert len(results) == 1
        assert results[0].name == "M 45"


class TestSDSSConnector:
    async def test_search_falls_back_when_sql_path_fails(self, monkeypatch):
        from astropy.table import Table
        from app.connectors.sdss import SDSSConnector

        connector = SDSSConnector()

        async def fail_sql(_sql: str):
            raise TimeoutError("sql timed out")

        async def fallback(ra: float, dec: float, radius: float):
            assert radius <= 0.05
            return Table({"objid": ["123"], "ra": [ra], "dec": [dec], "r": [17.2], "type": [6]})

        monkeypatch.setattr(connector, "_query_skyserver", fail_sql)
        monkeypatch.setattr(connector, "_query_region_fallback", fallback)

        results = await connector.search("M31", ra=10.6847, dec=41.2687, radius=0.2)

        assert len(results) == 1
        assert results[0].source == "sdss"
        assert results[0].object_id == "123"


class TestChandraConnector:
    async def test_search_uses_csc21_cone_search(self, monkeypatch):
        from app.connectors.chandra import ChandraConnector, CSC2_CONE_URL

        calls: dict[str, object] = {}
        votable = b"""<?xml version="1.0"?>
<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">
  <RESOURCE>
    <TABLE>
      <FIELD name="name" datatype="char" arraysize="*"/>
      <FIELD name="ra" datatype="double"/>
      <FIELD name="dec" datatype="double"/>
      <DATA>
        <TABLEDATA>
          <TR><TD>2CXO J0534.5+2201</TD><TD>83.6331</TD><TD>22.0145</TD></TR>
        </TABLEDATA>
      </DATA>
    </TABLE>
  </RESOURCE>
</VOTABLE>"""

        class FakeResponse:
            def __init__(self, content: bytes):
                self.content = content

            def raise_for_status(self):
                return None

        class FakeClient:
            def __init__(self, timeout=None):
                calls["timeout"] = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, params=None):
                calls["url"] = url
                calls["params"] = params
                return FakeResponse(votable)

        monkeypatch.setattr("app.connectors.chandra.httpx.AsyncClient", FakeClient)

        connector = ChandraConnector()
        results = await connector.search("Crab Nebula", ra=83.6331, dec=22.0145, radius=0.1)

        assert calls["url"] == CSC2_CONE_URL
        assert calls["params"] == {"RA": "83.6331", "DEC": "22.0145", "SR": "0.1", "VERB": "1"}
        assert calls["timeout"] == 30.0
        assert len(results) == 1
        assert results[0].source == "chandra"
