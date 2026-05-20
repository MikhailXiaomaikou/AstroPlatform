"""Unit tests for ai_tools_solar_system M0 Commit 4 — 12 ai_tools.

mock HTTP + connector 验证 happy path + failure path + provenance attachment。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest


# ── schema & registration ──────────────────────────────────────────


def test_schemas_count_is_twelve():
    from app.services.ai_tools_solar_system import (
        SOLAR_SYSTEM_TOOL_SCHEMAS, SOLAR_SYSTEM_TOOL_NAMES,
    )
    assert len(SOLAR_SYSTEM_TOOL_SCHEMAS) == 12
    assert len(SOLAR_SYSTEM_TOOL_NAMES) == 12


def test_schemas_have_required_fields():
    from app.services.ai_tools_solar_system import SOLAR_SYSTEM_TOOL_SCHEMAS
    for s in SOLAR_SYSTEM_TOOL_SCHEMAS:
        assert "name" in s and s["name"]
        assert "description" in s and s["description"]
        assert "input_schema" in s and s["input_schema"].get("type") == "object"


def test_tools_extended_into_ai_tools_TOOLS_list():
    """ai_tools.py 的 TOOLS list 必须含 12 个 solar_system 工具."""
    from app.services.ai_tools import TOOLS

    names = {t["name"] for t in TOOLS if isinstance(t, dict) and "name" in t}
    for ss_name in (
        "query_mpc_orbit", "fetch_horizons_ephemeris", "query_sbdb_orbit",
        "query_sbdb_close_approaches", "query_sentry_risk",
        "query_damit_shape_model",
        "compute_hg_magnitude", "compute_afrho", "fit_neatm_diameter_albedo",
        "compute_neo_collision_probability",
        "classify_asteroid_busdemeo", "classify_asteroid_sdss_colors",
    ):
        assert ss_name in names, f"solar_system tool {ss_name} 未注册到 TOOLS"


def test_result_provenance_classifies_all_solar_system_tools():
    """result_provenance._DATA_TOOLS / _COMPUTE_TOOLS 必须包含 12 个新工具,
    否则 normalize_tool_result 会 silent downgrade 它们的 data_origin."""
    from app.services.result_provenance import (
        _DATA_TOOLS, _COMPUTE_TOOLS, ALL_KNOWN_TOOLS,
    )

    data_tools = {
        "query_mpc_orbit", "fetch_horizons_ephemeris", "query_sbdb_orbit",
        "query_sbdb_close_approaches", "query_sentry_risk", "query_damit_shape_model",
    }
    compute_tools = {
        "compute_hg_magnitude", "compute_afrho", "fit_neatm_diameter_albedo",
        "compute_neo_collision_probability",
        "classify_asteroid_busdemeo", "classify_asteroid_sdss_colors",
    }
    assert data_tools.issubset(_DATA_TOOLS)
    assert compute_tools.issubset(_COMPUTE_TOOLS)
    assert (data_tools | compute_tools).issubset(ALL_KNOWN_TOOLS)


def test_tool_deadline_table_has_solar_system_entries():
    """chat.py _TOOL_DEADLINE_TABLE 包含 fetch_horizons_ephemeris + MPC/SBDB 系列."""
    # 通过 import chat 并触发 module 加载,然后看 source 中包含
    import app.api.chat as chat_mod
    import inspect
    source = inspect.getsource(chat_mod)
    for tool, deadline in (
        ("fetch_horizons_ephemeris", "90.0"),
        ("query_mpc_orbit", "60.0"),
        ("query_sentry_risk", "60.0"),
    ):
        assert f'"{tool}": {deadline}' in source, f"missing deadline for {tool}"


def test_horizons_input_normalizers_accept_common_apophis_forms():
    from app.services.ai_tools_solar_system import (
        _normalize_horizons_location,
        _normalize_horizons_target,
    )

    assert _normalize_horizons_target("(99942) Apophis") == "99942"
    assert _normalize_horizons_target("99942 Apophis") == "99942"
    assert _normalize_horizons_target("Apophis") == "Apophis"
    assert _normalize_horizons_location("geocenter") == "500"
    assert _normalize_horizons_location("geocentric") == "500"
    assert _normalize_horizons_location("heliocenter") == "500@10"


def test_fetch_horizons_rejects_century_daily_request_without_network():
    from app.services.ai_tools_solar_system import _exec_fetch_horizons_ephemeris

    result = asyncio.run(_exec_fetch_horizons_ephemeris({
        "target": "Phaethon",
        "start": "2026-01-01",
        "stop": "2028-01-01",
        "step": "1d",
        "location": "geocenter",
    }))
    assert result["success"] is False
    assert result["__tool_status__"] == "FAILED"
    assert result["error_class"] == "range_too_large"
    assert "Do not split a multi-year daily ephemeris" in result["__message_to_model__"]


# ── 公式/分类工具(纯算,无 HTTP) ──────────────────────────────────


def test_exec_compute_hg_magnitude_happy_path():
    from app.services.ai_tools_solar_system import _exec_compute_hg_magnitude

    result = asyncio.run(_exec_compute_hg_magnitude({
        "H": 14.6, "G": 0.15,
        "phase_angles_deg": [0, 30, 60],
        "r_au": [1.0, 1.0, 1.0],
        "delta_au": [1.0, 1.0, 1.0],
        "data_source": "archive_cache",
    }))
    assert result["success"] is True
    assert result["data_origin"] == "cached_real"
    assert len(result["V_apparent"]) == 3
    assert result["V_apparent"][0] == pytest.approx(14.6, abs=1e-3)
    assert result["bibcode"] == "1989aste.conf..524B"


def test_exec_compute_hg_magnitude_user_supplied_downgrades():
    from app.services.ai_tools_solar_system import _exec_compute_hg_magnitude

    result = asyncio.run(_exec_compute_hg_magnitude({
        "H": 14.6,
        "phase_angles_deg": [0],
        "r_au": [1.0], "delta_au": [1.0],
        "data_source": "user_supplied",
    }))
    assert result["data_origin"] == "user_uploaded"


def test_exec_compute_hg_magnitude_failure():
    from app.services.ai_tools_solar_system import _exec_compute_hg_magnitude

    result = asyncio.run(_exec_compute_hg_magnitude({
        "H": 14.6, "phase_angles_deg": [200],  # 越界
        "r_au": [1.0], "delta_au": [1.0],
    }))
    assert result["success"] is False
    assert result["__tool_status__"] == "FAILED"
    assert result["__do_not_claim__"] is True


def test_exec_compute_afrho_happy_path():
    from app.services.ai_tools_solar_system import _exec_compute_afrho

    result = asyncio.run(_exec_compute_afrho({
        "m_comet": 12.0, "r_au": 1.24, "delta_au": 2.7,
        "aperture_arcsec": 5.0, "alpha_deg": 30.0,
    }))
    assert result["success"] is True
    assert result["Afrho_cm"] > 10
    assert result["Afrho_0deg_cm"] > result["Afrho_cm"]  # phase 校正后变大


def test_exec_fit_neatm_returns_diameter_and_albedo():
    """forward + inverse fit consistency."""
    from app.services.ai_tools_solar_system import _exec_fit_neatm_diameter_albedo
    from app.services.solar_system_thermo import (
        harris_diameter_km, neatm_thermal_flux_density,
    )

    H_true, p_V_true = 17.0, 0.10
    D_true = harris_diameter_km(H_true, p_V_true)
    F_true = neatm_thermal_flux_density(
        D_true, p_V_true, 0.15, 1.5, 1.0, 12.0, eta=1.4,
    )

    result = asyncio.run(_exec_fit_neatm_diameter_albedo({
        "H": H_true, "observed_flux_jy": F_true,
        "lambda_um": 12.0, "r_au": 1.5, "delta_au": 1.0,
    }))
    assert result["success"] is True
    assert result["albedo_pV"] == pytest.approx(p_V_true, rel=0.05)
    assert result["diameter_km"] == pytest.approx(D_true, rel=0.05)


def test_exec_compute_neo_collision_probability_apophis_like():
    from app.services.ai_tools_solar_system import _exec_compute_neo_collision_probability

    result = asyncio.run(_exec_compute_neo_collision_probability({
        "moid_au": 0.00025, "a_au": 0.922, "e": 0.191, "i_deg": 3.33,
    }))
    assert result["success"] is True
    assert "opik_upper_bound_100yr" in result
    # warning 必须明确告诉这不是真实 Sentry 数字
    assert result["__message_to_model__"]


def test_exec_classify_busdemeo_from_features():
    from app.services.ai_tools_solar_system import _exec_classify_asteroid_busdemeo

    result = asyncio.run(_exec_classify_asteroid_busdemeo({
        "visible_slope": 0.25, "band1_depth": 0.40, "nir_slope": -0.10,
    }))
    assert result["success"] is True
    assert result["best_class"] == "V"


def test_exec_classify_busdemeo_from_spectrum():
    from app.services.ai_tools_solar_system import _exec_classify_asteroid_busdemeo

    wl = [0.45, 0.55, 0.65, 0.75, 0.85, 1.0, 1.15, 1.3, 1.5, 1.8, 2.0, 2.45]
    rf = [1.0] * len(wl)  # flat → C class
    result = asyncio.run(_exec_classify_asteroid_busdemeo({
        "wavelengths_um": wl, "reflectance": rf,
    }))
    assert result["success"] is True
    assert result["best_class"] in {"C", "B", "X"}  # flat featureless typically C


def test_exec_classify_carvano_sdss():
    """P3 (2026-05-20) 校准后 V class center r-i=-0.40; 旧测试用 r-i=-0.05
    现在会分到 O,需要用 paper-accurate V colors."""
    from app.services.ai_tools_solar_system import _exec_classify_asteroid_sdss_colors

    result = asyncio.run(_exec_classify_asteroid_sdss_colors({
        "u_g": 1.85, "g_r": 0.60, "r_i": -0.40, "i_z": -0.20,
    }))
    assert result["success"] is True
    assert result["best_class"] == "V"


# ── 数据查询工具(mock HTTP) ─────────────────────────────────────


def test_exec_query_mpc_orbit_via_mocked_connector(monkeypatch):
    from astropy.table import Table
    from app.services.ai_tools_solar_system import _exec_query_mpc_orbit
    from app.connectors.mpc import MPCConnector

    fake_table = Table({
        "name": ["3200 Phaethon"],
        "a": [1.271],
        "e": [0.8898],
        "i": [22.26],
        "absolute_magnitude": [14.6],
        "phase_slope": [0.15],
    })

    # monkeypatch the connector instance returned by get_connector
    def fake_query(self, _):
        return fake_table
    monkeypatch.setattr(MPCConnector, "_query_mpc", fake_query)

    result = asyncio.run(_exec_query_mpc_orbit({"designation": "Phaethon"}))
    assert result["success"] is True
    assert result["designation"] == "3200 Phaethon"
    assert result["orbital_elements"]["a"] == 1.271
    assert result["orbital_elements"]["e"] == 0.8898
    assert result["_provenance_dataset"]["service_key"] == "mpc"


def test_exec_query_mpc_orbit_missing_designation():
    from app.services.ai_tools_solar_system import _exec_query_mpc_orbit
    result = asyncio.run(_exec_query_mpc_orbit({}))
    assert result["success"] is False
    assert result["error_class"] == "missing_argument"


def test_exec_query_sbdb_orbit_via_mocked_http(monkeypatch):
    from app.services import ai_tools_solar_system as mod
    fake_response_payload = {
        "object": {
            "fullname": "99942 Apophis (2004 MN4)",
            "spkid": "2099942",
        },
        "orbit": {"elements": [{"name": "a", "value": "0.9224"}]},
        "phys_par": [{"name": "H", "value": "19.7"}],
    }

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            req = httpx.Request("GET", url)
            return httpx.Response(200, request=req, json=fake_response_payload)

    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    result = asyncio.run(mod._exec_query_sbdb_orbit({"designation": "Apophis"}))
    assert result["success"] is True
    assert "Apophis" in result["designation"]
    assert result["_provenance_dataset"]["service_key"] == "sbdb"


def test_exec_query_sentry_risk_404_returns_empty(monkeypatch):
    """Sentry 404 = NEO not on risk list — 应该返 EMPTY banner, 不是 FAILED."""
    from app.services import ai_tools_solar_system as mod

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            req = httpx.Request("GET", url)
            return httpx.Response(404, request=req)

    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    result = asyncio.run(mod._exec_query_sentry_risk({"designation": "Phaethon"}))
    assert result["success"] is True
    assert result["__tool_status__"] == "EMPTY"
    assert result["__do_not_claim__"] is True
    assert "not_on_sentry_list" in result["result"]


def test_exec_query_sbdb_close_approaches_happy_path(monkeypatch):
    from app.services import ai_tools_solar_system as mod
    fake_payload = {
        "fields": ["des", "cd", "dist", "v_rel"],
        "data": [
            ["99942", "2029-Apr-13 21:46", "0.0002", "7.42"],
            ["99942", "2036-Apr-13", "0.025", "5.85"],
        ],
    }

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            return httpx.Response(200, json=fake_payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    result = asyncio.run(mod._exec_query_sbdb_close_approaches({
        "designation": "99942", "date_min": "2020-01-01", "date_max": "2040-01-01",
    }))
    assert result["success"] is True
    assert result["n_approaches"] == 2
    assert result["approaches"][0]["des"] == "99942"


# ── dispatch ──────────────────────────────────────────────────────


def test_dispatch_routes_known_tools():
    from app.services.ai_tools_solar_system import dispatch_solar_system

    result = asyncio.run(dispatch_solar_system("compute_hg_magnitude", {
        "H": 14.6,
        "phase_angles_deg": [0.0],
        "r_au": [1.0], "delta_au": [1.0],
    }))
    assert result["success"] is True


def test_dispatch_unknown_tool_returns_failed():
    from app.services.ai_tools_solar_system import dispatch_solar_system

    result = asyncio.run(dispatch_solar_system("nonexistent_tool", {}))
    assert result["success"] is False
    assert result["error_class"] == "unknown_tool"


def test_dispatch_via_ai_tools_execute_tool():
    """端到端: ai_tools.execute_tool('compute_hg_magnitude', ...) 走 dispatch."""
    from app.services.ai_tools import execute_tool

    result = asyncio.run(execute_tool("compute_hg_magnitude", {
        "H": 14.6,
        "phase_angles_deg": [0.0, 30.0],
        "r_au": [1.0, 1.0], "delta_au": [1.0, 1.0],
    }, python_session_id=None))
    assert result["success"] is True
    assert "V_apparent" in result
