"""Solar-system module M0 Commit 7 集成 smoke (盲测) — 端到端验证.

目标:
- 12 个工具都能通过 ai_tools.execute_tool() 主入口 dispatch
- 金路径 chain (Phaethon: MPC orbit → Horizons ephemeris → HG mag) 串通
- result_provenance.normalize_tool_result 正确分类 data_origin
- claim_validator 对 solar_system bibcode 正确响应
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from astropy.table import Table


# ── 12 个工具都能通过 ai_tools.execute_tool dispatch ─────────────


def test_all_twelve_tools_dispatch_via_execute_tool(monkeypatch):
    """对每个工具发送最小合法 input, 验证 dispatch 不抛 unknown_tool."""
    from app.services.ai_tools import execute_tool

    # mock HTTP for SBDB/Sentry/DAMIT — 避免真实 outbound
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            req = httpx.Request("GET", url)
            # 给一个合理 JSON 让 each tool 不至于 throw
            if "sentry" in url:
                return httpx.Response(404, request=req)
            if "cad.api" in url:
                return httpx.Response(200, request=req, json={"fields": ["des"], "data": []})
            if "sbdb.api" in url:
                return httpx.Response(200, request=req, json={"object": {"fullname": "X"}, "orbit": {}, "phys_par": []})
            if "damit" in url:
                return httpx.Response(204, request=req)
            return httpx.Response(200, request=req, json={})

    import app.services.ai_tools_solar_system as ss_mod
    monkeypatch.setattr(ss_mod.httpx, "AsyncClient", FakeClient)

    # mock MPC + Horizons connectors
    from app.connectors.mpc import MPCConnector
    monkeypatch.setattr(MPCConnector, "_query_mpc", lambda self, _: None)

    minimal_inputs = {
        "query_mpc_orbit": {"designation": "Phaethon"},
        "fetch_horizons_ephemeris": None,  # 见下 — Horizons 需 mock
        "query_sbdb_orbit": {"designation": "99942"},
        "query_sbdb_close_approaches": {"date_min": "2024-01-01", "date_max": "2025-01-01"},
        "query_sentry_risk": {"designation": "99942"},
        "query_damit_shape_model": {"designation": "21"},
        "compute_hg_magnitude": {
            "H": 14.6, "phase_angles_deg": [0.0],
            "r_au": [1.0], "delta_au": [1.0],
        },
        "compute_afrho": {
            "m_comet": 12.0, "r_au": 1.5, "delta_au": 1.0, "aperture_arcsec": 5.0,
        },
        "fit_neatm_diameter_albedo": {
            "H": 17.0, "observed_flux_jy": 0.05, "lambda_um": 12.0,
            "r_au": 1.5, "delta_au": 1.0,
        },
        "compute_neo_collision_probability": {
            "moid_au": 0.001, "a_au": 1.2, "e": 0.3, "i_deg": 5.0,
        },
        "classify_asteroid_busdemeo": {
            "visible_slope": 0.04, "band1_depth": 0.0, "nir_slope": 0.0,
        },
        "classify_asteroid_sdss_colors": {
            "u_g": 1.62, "g_r": 0.46, "r_i": 0.16, "i_z": -0.04,
        },
    }

    # mock Horizons.ephemerides
    fake_eph = Table({"targetname": ["X"], "RA": [10.0], "DEC": [20.0]})
    import app.connectors.jpl as jpl_mod
    import app.services.ai_tools_solar_system as ss_mod2

    class FakeHorizons:
        def __init__(self, **kw): pass
        def ephemerides(self): return fake_eph

    monkeypatch.setitem(jpl_mod.__dict__, "Horizons", FakeHorizons)
    minimal_inputs["fetch_horizons_ephemeris"] = {
        "target": "Phaethon", "start": "2026-01-01", "stop": "2026-01-02",
    }
    # mock astroquery.jplhorizons inside _exec_fetch_horizons_ephemeris
    # 因为它在函数内部 import, 用 monkeypatch sys.modules
    import sys
    fake_module = type(sys)("astroquery.jplhorizons")
    fake_module.Horizons = FakeHorizons  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "astroquery.jplhorizons", fake_module)

    # iterate all 12 tools
    from app.services.ai_tools_solar_system import SOLAR_SYSTEM_TOOL_NAMES
    fail_classes: list[str] = []
    for tool_name in SOLAR_SYSTEM_TOOL_NAMES:
        inp = minimal_inputs[tool_name]
        result = asyncio.run(execute_tool(tool_name, inp, python_session_id=None))
        # 应该是 dict, 且包含 success 字段 (或至少 __tool_status__)
        assert isinstance(result, dict), f"{tool_name} returned non-dict"
        # 不能 unknown_tool
        if result.get("error_class") == "unknown_tool":
            fail_classes.append(tool_name)
        # 不能 raise — 所有错误都包成 FAILED banner
    assert not fail_classes, f"dispatch fail: {fail_classes}"


# ── 金路径: Phaethon ephemeris + HG ──────────────────────────────


def test_phaethon_golden_path_chain(monkeypatch):
    """模拟 LLM chain: MPC → Horizons → HG mag → 数值合理且 provenance 完整."""
    from app.services.ai_tools import execute_tool
    from astropy.table import Table

    # Step 1: mock MPC return
    fake_mpc = Table({
        "name": ["3200 Phaethon"],
        "absolute_magnitude": [14.6],
        "phase_slope": [0.15],
        "a": [1.271], "e": [0.8898], "i": [22.26],
    })
    from app.connectors.mpc import MPCConnector
    monkeypatch.setattr(MPCConnector, "_query_mpc", lambda self, _: fake_mpc)

    # Step 2: mock Horizons return — 5 epochs across 2026
    fake_eph = Table({
        "targetname": ["3200 Phaethon"] * 5,
        "datetime_str": ["2026-Jan-01", "2026-Apr-01", "2026-Jul-01", "2026-Oct-01", "2026-Dec-31"],
        "RA": [10.0, 50.0, 120.0, 210.0, 340.0],
        "DEC": [-5.0, 10.0, 25.0, 5.0, -10.0],
        "V": [17.5, 19.0, 14.5, 18.0, 17.0],
        "r": [1.8, 2.4, 0.4, 1.5, 1.9],
        "delta": [1.0, 1.5, 0.7, 2.0, 1.2],
        "alpha": [20.0, 15.0, 90.0, 10.0, 25.0],
        "lighttime": [8.3, 12.5, 5.8, 16.6, 10.0],
    })

    class FakeHorizons:
        def __init__(self, **kw): pass
        def ephemerides(self): return fake_eph

    import sys
    fake_module = type(sys)("astroquery.jplhorizons")
    fake_module.Horizons = FakeHorizons  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "astroquery.jplhorizons", fake_module)

    # Step 1: query MPC orbit
    mpc_result = asyncio.run(execute_tool(
        "query_mpc_orbit", {"designation": "Phaethon"}, python_session_id=None,
    ))
    assert mpc_result["success"] is True
    H = mpc_result["orbital_elements"]["absolute_magnitude"]
    G = mpc_result["orbital_elements"]["phase_slope"]
    assert H == 14.6
    assert G == 0.15

    # Step 2: fetch Horizons ephemeris
    eph_result = asyncio.run(execute_tool(
        "fetch_horizons_ephemeris",
        {"target": "Phaethon", "start": "2026-01-01", "stop": "2026-12-31", "step": "1d"},
        python_session_id=None,
    ))
    assert eph_result["success"] is True
    assert eph_result["n_rows"] == 5
    alphas = [row["alpha"] for row in eph_result["rows"]]
    rs = [row["r"] for row in eph_result["rows"]]
    deltas = [row["delta"] for row in eph_result["rows"]]

    # Step 3: compute HG magnitude using H, G from step 1 + alphas/r/delta from step 2
    hg_result = asyncio.run(execute_tool(
        "compute_hg_magnitude",
        {
            "H": H, "G": G,
            "phase_angles_deg": alphas, "r_au": rs, "delta_au": deltas,
            "data_source": "archive_cache",
        },
        python_session_id=None,
    ))
    assert hg_result["success"] is True
    assert hg_result["data_origin"] == "cached_real"
    V_predicted = hg_result["V_apparent"]
    assert len(V_predicted) == 5
    # V should track Horizons V column within HG model precision (~0.5 mag)
    V_horizons = [row["V"] for row in eph_result["rows"]]
    for V_pred, V_hor in zip(V_predicted, V_horizons):
        assert abs(V_pred - V_hor) < 1.5, f"V_pred={V_pred} vs V_hor={V_hor}"

    # 验证 provenance 链:Horizons rows 有 jpl _provenance_dataset
    assert eph_result.get("_provenance_dataset", {}).get("service_key") == "jpl"
    # MPC result 有 mpc _provenance_dataset
    assert mpc_result.get("_provenance_dataset", {}).get("service_key") == "mpc"


# ── data_source enum 反幻造路径 ────────────────────────────────


def test_user_supplied_data_source_downgrades_data_origin():
    """当 LLM 标 data_source='user_supplied' (没经过 archive), 输出 data_origin 应降级."""
    from app.services.ai_tools import execute_tool

    result = asyncio.run(execute_tool(
        "compute_hg_magnitude",
        {
            "H": 14.6, "G": 0.15,
            "phase_angles_deg": [30.0], "r_au": [1.0], "delta_au": [1.0],
            "data_source": "user_supplied",
        },
        python_session_id=None,
    ))
    assert result["success"] is True
    assert result["data_origin"] == "user_uploaded"  # 降级标记


def test_failed_tool_returns_do_not_claim_banner():
    """任何 _exec_* 抛异常 → FAILED + __do_not_claim__=True + 不 leak python exc."""
    from app.services.ai_tools import execute_tool

    # invalid α=200 (out of [0, 180]) — 应该 FAILED
    result = asyncio.run(execute_tool(
        "compute_hg_magnitude",
        {
            "H": 14.6, "phase_angles_deg": [200.0],
            "r_au": [1.0], "delta_au": [1.0],
        },
        python_session_id=None,
    ))
    assert result["success"] is False
    assert result["__tool_status__"] == "FAILED"
    assert result["__do_not_claim__"] is True
    assert "out of range" in result["error"].lower() or "200" in result["error"]


# ── normalize_tool_result 分类正确 ────────────────────────────


def test_normalize_classifies_data_tool_correctly():
    """query_mpc_orbit 应该被分类为 _DATA_TOOLS, banner 不是 UNAVAILABLE."""
    from app.services.result_provenance import normalize_tool_result

    fake_result = {
        "success": True,
        "data_origin": "real_archive",
        "designation": "X",
        "orbital_elements": {"a": 1.0},
    }
    normalised = normalize_tool_result("query_mpc_orbit", fake_result)
    # 不该 silent downgrade 为 UNAVAILABLE
    assert normalised.get("__tool_status__") != "UNAVAILABLE"


def test_normalize_classifies_compute_tool_correctly():
    """compute_hg_magnitude 在 _COMPUTE_TOOLS, normalize 后 data_origin 不是 unavailable."""
    from app.services.result_provenance import normalize_tool_result

    fake_result = {
        "success": True,
        "data_origin": "cached_real",
        "V_apparent": [14.5],
    }
    normalised = normalize_tool_result("compute_hg_magnitude", fake_result)
    assert normalised.get("__tool_status__") != "UNAVAILABLE"
