"""MPC connector M0 Commit 2 unit tests (mocked astroquery)."""

from __future__ import annotations

import asyncio

from astropy.table import Table


# ── happy path ─────────────────────────────────────────────────────


def test_mpc_search_returns_astroobject_with_orbit_elements(monkeypatch):
    """search() 把 MPC orbital-elements Table 转成 AstroObject + extra。"""
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
    # MPC 是轨道根数,没有 RA/Dec — 应该 0.0/0.0 占位
    assert obj.ra == 0.0
    assert obj.dec == 0.0
    # 轨道根数走 extra
    assert obj.extra["a"] == 1.271
    assert obj.extra["e"] == 0.8898
    assert obj.extra["i"] == 22.26
    assert obj.extra["absolute_magnitude"] == 14.6
    assert obj.extra["phase_slope"] == 0.15
    # magnitude = H (absolute magnitude)
    assert obj.magnitude == 14.6


def test_mpc_provenance_dataset_attached(monkeypatch):
    """provenance-v2: _provenance_dataset 必须含 mpc 关键字段."""
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
    """没有 name 列时回退到 designation,再回退到传入的 query."""
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
    """name + designation 都缺时,name 回退到传入的 query."""
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
    """absolute_magnitude 是字符串或缺失时,magnitude=None,避免 validation warning."""
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


# ── designation normalizer (P2 2026-05-20): 多变体 fallback ──────────


def test_normalize_extracts_number_and_name():
    """(3200) Phaethon 应展开成 ['3200', 'Phaethon', '(3200) Phaethon'] 3 变体。"""
    from app.connectors.mpc import _normalize_mpc_designations

    got = _normalize_mpc_designations("(3200) Phaethon")
    assert got == ["3200", "Phaethon", "(3200) Phaethon"]


def test_normalize_keeps_provisional_designation_intact():
    """1983 TB 是 provisional designation, 不能拆成 ['1983', 'TB']。"""
    from app.connectors.mpc import _normalize_mpc_designations

    got = _normalize_mpc_designations("1983 TB")
    assert got == ["1983 TB"]

    # 2024 YR4 同理
    got = _normalize_mpc_designations("2024 YR4")
    assert got == ["2024 YR4"]


def test_normalize_pure_name_or_number_unchanged():
    """单一 form 入参不展开。"""
    from app.connectors.mpc import _normalize_mpc_designations

    assert _normalize_mpc_designations("Apophis") == ["Apophis"]
    assert _normalize_mpc_designations("99942") == ["99942"]
    assert _normalize_mpc_designations("") == []


def test_query_mpc_tries_multiple_variants_when_first_fails(monkeypatch):
    """_query_mpc 应该逐个 try designation 变体, 第 1 个失败时试第 2 个。"""
    from app.connectors.mpc import MPCConnector

    call_log: list[tuple[str, str]] = []

    class FakeMPC:
        @staticmethod
        def query_object(target_type: str, designation: str):
            call_log.append((target_type, designation))
            # 让 "3200" 和 "Phaethon" 都失败 (返空), 仅原样 "(3200) Phaethon" 成功
            if designation == "(3200) Phaethon":
                return [{"name": "3200 Phaethon", "a": 1.271, "e": 0.8898, "i": 22.26}]
            return None

    import app.connectors.mpc as mpc_mod
    monkeypatch.setattr(mpc_mod, "MPC", FakeMPC, raising=False)
    # 让 from astroquery.mpc import MPC 拿到 FakeMPC
    import sys
    fake_mod = type(sys)("astroquery.mpc")
    fake_mod.MPC = FakeMPC  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "astroquery.mpc", fake_mod)

    connector = MPCConnector()
    table = connector._query_mpc("(3200) Phaethon")

    # 应该至少试了 3 个变体 × 2 个 target_type
    designations_tried = [d for _, d in call_log]
    assert "3200" in designations_tried
    assert "Phaethon" in designations_tried
    assert "(3200) Phaethon" in designations_tried
    # 最后成功返了 table
    assert table is not None
    assert len(table) == 1
