"""JPL Horizons connector M0 Commit 2 unit tests (mocked astroquery)."""

from __future__ import annotations

import asyncio

from astropy.table import Table


# ── happy path ─────────────────────────────────────────────────────


def test_jpl_search_returns_astroobject_with_physical_fields(monkeypatch):
    """search() converts ephemerides Table to AstroObject with extra physical fields."""
    from app.connectors.jpl import JPLHorizonsConnector

    fake_eph = Table({
        "targetname": ["3200 Phaethon (1983 TB)"],
        "RA": [123.456],
        "DEC": [45.678],
        "V": [14.6],
        "r": [1.27],
        "delta": [0.31],
        "alpha": [33.0],
        "lighttime": [2.57],
    })

    connector = JPLHorizonsConnector()
    monkeypatch.setattr(connector, "_fetch_now_ephemeris", lambda _: fake_eph)

    result = asyncio.run(connector.search("Phaethon"))

    assert len(result) == 1
    obj = result[0]
    assert obj.source == "jpl"
    assert obj.object_type == "solar_system_body"
    assert obj.name == "3200 Phaethon (1983 TB)"
    assert obj.ra == 123.456
    assert obj.dec == 45.678
    assert obj.magnitude == 14.6
    assert obj.extra["heliocentric_distance_au"] == 1.27
    assert obj.extra["geocentric_distance_au"] == 0.31
    assert obj.extra["phase_angle_deg"] == 33.0
    assert obj.extra["light_time_min"] == 2.57
    assert "Giorgini" in obj.extra["source_reference"]


def test_jpl_provenance_dataset_attached_with_correct_fields(monkeypatch):
    """provenance-v2 contract: _provenance_dataset must contain jpl key fields."""
    from app.connectors.jpl import JPLHorizonsConnector

    fake_eph = Table({
        "targetname": ["Phaethon"],
        "RA": [10.0],
        "DEC": [20.0],
    })
    connector = JPLHorizonsConnector()
    monkeypatch.setattr(connector, "_fetch_now_ephemeris", lambda _: fake_eph)
    obj = asyncio.run(connector.search("Phaethon"))[0]

    assert "_provenance_dataset" in obj.extra
    ds = obj.extra["_provenance_dataset"]
    assert ds["service_key"] == "jpl"
    assert ds["archive_version"] == "horizons-2026"
    assert ds["article"] == "1996DPS....28.2504G"
    assert ds.get("publisher")
    assert ds.get("acknowledgement_template")


# ── edge cases ─────────────────────────────────────────────────────


def test_jpl_empty_table_returns_empty_list(monkeypatch):
    from app.connectors.jpl import JPLHorizonsConnector

    connector = JPLHorizonsConnector()
    monkeypatch.setattr(connector, "_fetch_now_ephemeris", lambda _: None)
    assert asyncio.run(connector.search("Phaethon")) == []


def test_jpl_empty_query_short_circuits():
    from app.connectors.jpl import JPLHorizonsConnector
    connector = JPLHorizonsConnector()
    assert asyncio.run(connector.search("")) == []


def test_jpl_missing_columns_uses_safe_defaults(monkeypatch):
    """Missing V/r/delta columns should not raise errors; extra should omit those fields."""
    from app.connectors.jpl import JPLHorizonsConnector

    fake_eph = Table({
        "targetname": ["Test"],
        "RA": [0.0],
        "DEC": [0.0],
    })
    connector = JPLHorizonsConnector()
    monkeypatch.setattr(connector, "_fetch_now_ephemeris", lambda _: fake_eph)
    obj = asyncio.run(connector.search("Test"))[0]

    assert obj.ra == 0.0
    assert obj.dec == 0.0
    assert obj.magnitude is None
    assert "heliocentric_distance_au" not in obj.extra
    assert "phase_angle_deg" not in obj.extra


def test_jpl_fetch_raises_not_implemented():
    from app.connectors.jpl import JPLHorizonsConnector

    connector = JPLHorizonsConnector()
    try:
        asyncio.run(connector.fetch("dummy"))
    except NotImplementedError as exc:
        assert "FITS" in str(exc)
    else:
        raise AssertionError("Expected NotImplementedError")


def test_jpl_nan_inf_in_columns_falls_back_to_none(monkeypatch):
    """NaN/Inf data goes through _safe_float path and returns None without corrupting AstroObject."""
    from app.connectors.jpl import JPLHorizonsConnector
    import math

    fake_eph = Table({
        "targetname": ["Test"],
        "RA": [10.0],
        "DEC": [20.0],
        "V": [math.nan],
        "r": [math.inf],
        "delta": [1.0],
    })
    connector = JPLHorizonsConnector()
    monkeypatch.setattr(connector, "_fetch_now_ephemeris", lambda _: fake_eph)
    obj = asyncio.run(connector.search("Test"))[0]

    assert obj.magnitude is None  # V=NaN → None
    assert "heliocentric_distance_au" not in obj.extra  # r=Inf → None
    assert obj.extra["geocentric_distance_au"] == 1.0  # delta=1.0 preserved
