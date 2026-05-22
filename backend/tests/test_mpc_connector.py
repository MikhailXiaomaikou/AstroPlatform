"""MPC connector M0 Commit 2 unit tests (mocked astroquery)."""

from __future__ import annotations

import asyncio

from astropy.table import Table


# ── happy path ─────────────────────────────────────────────────────


def test_mpc_search_returns_astroobject_with_orbit_elements(monkeypatch):
    """search() converts MPC orbital-elements Table to AstroObject with extra fields."""
    from app.connectors.mpc import MPCConnector

    fake = Table({
        "name": ["3200 Phaethon"],
        "designation": ["1983 TB"],
        "a": [1.271],
        "e": [0.8898],
        "i": [22.26],
        "absolute_magnitude": [14.6],
        "phase_slope": [0.15],
        "perihelion_distance": [0.140],
        "aphelion_distance": [2.403],
    })
    connector = MPCConnector()
    monkeypatch.setattr(connector, "_query_mpc", lambda _: fake)

    result = asyncio.run(connector.search("Phaethon"))

    assert len(result) == 1
    obj = result[0]
    assert obj.source == "mpc"
    assert obj.object_type == "solar_system_body"
    assert obj.name == "3200 Phaethon"
    # MPC provides orbital elements, no RA/Dec — should placeholder as 0.0/0.0
    assert obj.ra == 0.0
    assert obj.dec == 0.0
    # orbital elements go into extra
    assert obj.extra["a"] == 1.271
    assert obj.extra["e"] == 0.8898
    assert obj.extra["i"] == 22.26
    assert obj.extra["absolute_magnitude"] == 14.6
    assert obj.extra["phase_slope"] == 0.15
    # magnitude = H (absolute magnitude)
    assert obj.magnitude == 14.6


def test_mpc_provenance_dataset_attached(monkeypatch):
    """provenance-v2: _provenance_dataset must contain mpc key fields."""
    from app.connectors.mpc import MPCConnector

    fake = Table({
        "name": ["1 Ceres"],
        "absolute_magnitude": [3.34],
        "a": [2.77],
    })
    connector = MPCConnector()
    monkeypatch.setattr(connector, "_query_mpc", lambda _: fake)
    obj = asyncio.run(connector.search("Ceres"))[0]

    assert "_provenance_dataset" in obj.extra
    ds = obj.extra["_provenance_dataset"]
    assert ds["service_key"] == "mpc"
    assert ds["archive_version"] == "mpc-2026"
    assert ds.get("publisher")
    assert ds.get("acknowledgement_template")


# ── edge cases ─────────────────────────────────────────────────────


def test_mpc_empty_table_returns_empty_list(monkeypatch):
    from app.connectors.mpc import MPCConnector

    connector = MPCConnector()
    monkeypatch.setattr(connector, "_query_mpc", lambda _: None)
    assert asyncio.run(connector.search("Phaethon")) == []


def test_mpc_empty_query_short_circuits():
    from app.connectors.mpc import MPCConnector
    connector = MPCConnector()
    assert asyncio.run(connector.search("")) == []


def test_mpc_designation_fallback_when_name_missing(monkeypatch):
    """When name column is missing, fall back to designation, then fall back to input query."""
    from app.connectors.mpc import MPCConnector

    fake = Table({
        "designation": ["1983 TB"],
        "a": [1.27],
    })
    connector = MPCConnector()
    monkeypatch.setattr(connector, "_query_mpc", lambda _: fake)
    obj = asyncio.run(connector.search("Phaethon"))[0]

    assert obj.name == "1983 TB"


def test_mpc_query_fallback_to_input_when_table_no_name(monkeypatch):
    """When both name and designation are missing, name falls back to the input query."""
    from app.connectors.mpc import MPCConnector

    fake = Table({
        "a": [1.0],
        "e": [0.1],
    })
    connector = MPCConnector()
    monkeypatch.setattr(connector, "_query_mpc", lambda _: fake)
    obj = asyncio.run(connector.search("MyAsteroid"))[0]

    assert obj.name == "MyAsteroid"


def test_mpc_magnitude_is_none_when_absolute_magnitude_not_numeric(monkeypatch):
    """When absolute_magnitude is a string or missing, magnitude=None to avoid validation warning."""
    from app.connectors.mpc import MPCConnector

    fake = Table({"name": ["X"], "a": [1.0]})
    connector = MPCConnector()
    monkeypatch.setattr(connector, "_query_mpc", lambda _: fake)
    obj = asyncio.run(connector.search("X"))[0]

    assert obj.magnitude is None


def test_mpc_fetch_raises_not_implemented():
    from app.connectors.mpc import MPCConnector

    connector = MPCConnector()
    try:
        asyncio.run(connector.fetch("dummy"))
    except NotImplementedError as exc:
        assert "FITS" in str(exc)
    else:
        raise AssertionError("Expected NotImplementedError")


# ── designation normalizer (P2 2026-05-20): multi-variant fallback ──────────


def test_normalize_extracts_number_and_name():
    """(3200) Phaethon should expand to ['3200', 'Phaethon', '(3200) Phaethon'] — 3 variants."""
    from app.connectors.mpc import _normalize_mpc_designations

    got = _normalize_mpc_designations("(3200) Phaethon")
    assert got == ["3200", "Phaethon", "(3200) Phaethon"]


def test_normalize_keeps_provisional_designation_intact():
    """1983 TB is a provisional designation and must not be split into ['1983', 'TB']."""
    from app.connectors.mpc import _normalize_mpc_designations

    got = _normalize_mpc_designations("1983 TB")
    assert got == ["1983 TB"]

    # 2024 YR4 likewise
    got = _normalize_mpc_designations("2024 YR4")
    assert got == ["2024 YR4"]


def test_normalize_pure_name_or_number_unchanged():
    """Single-form input is not expanded."""
    from app.connectors.mpc import _normalize_mpc_designations

    assert _normalize_mpc_designations("Apophis") == ["Apophis"]
    assert _normalize_mpc_designations("99942") == ["99942"]
    assert _normalize_mpc_designations("") == []


def test_query_mpc_tries_multiple_variants_when_first_fails(monkeypatch):
    """_query_mpc should try each designation variant in order, trying the next when the first fails."""
    from app.connectors.mpc import MPCConnector

    call_log: list[tuple[str, str]] = []

    class FakeMPC:
        @staticmethod
        def query_object(target_type: str, designation: str):
            call_log.append((target_type, designation))
            # let "3200" and "Phaethon" fail (return empty), only "(3200) Phaethon" succeeds
            if designation == "(3200) Phaethon":
                return [{"name": "3200 Phaethon", "a": 1.271, "e": 0.8898, "i": 22.26}]
            return None

    import app.connectors.mpc as mpc_mod
    monkeypatch.setattr(mpc_mod, "MPC", FakeMPC, raising=False)
    # make `from astroquery.mpc import MPC` get FakeMPC
    import sys
    fake_mod = type(sys)("astroquery.mpc")
    fake_mod.MPC = FakeMPC  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "astroquery.mpc", fake_mod)

    connector = MPCConnector()
    table = connector._query_mpc("(3200) Phaethon")

    # should have tried at least 3 variants × 2 target_types
    designations_tried = [d for _, d in call_log]
    assert "3200" in designations_tried
    assert "Phaethon" in designations_tried
    assert "(3200) Phaethon" in designations_tried
    # ultimately succeeded and returned a table
    assert table is not None
    assert len(table) == 1
