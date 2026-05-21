"""Exoplanet M0 (2026-05-20) — ai_tools dispatch + reset_provenance + claim_validator tests.

Covers schema registration, dispatch routing, failure paths, and zero-fabrication contract.
"""

from __future__ import annotations

import asyncio


# ── schema & registration ──────────────────────────────────────────


def test_schemas_count_is_eight():
    from app.services.ai_tools_exoplanet import (
        EXOPLANET_TOOL_SCHEMAS, EXOPLANET_TOOL_NAMES,
    )
    assert len(EXOPLANET_TOOL_SCHEMAS) == 8
    assert len(EXOPLANET_TOOL_NAMES) == 8


def test_schemas_have_required_fields():
    from app.services.ai_tools_exoplanet import EXOPLANET_TOOL_SCHEMAS
    for s in EXOPLANET_TOOL_SCHEMAS:
        assert "name" in s and s["name"]
        assert "description" in s and s["description"]
        assert "input_schema" in s and s["input_schema"].get("type") == "object"


def test_tools_extended_into_ai_tools_TOOLS_list():
    from app.services.ai_tools import TOOLS
    names = {t["name"] for t in TOOLS if isinstance(t, dict) and "name" in t}
    for ex_name in (
        "query_exoplanet_archive", "query_confirmed_planets",
        "fetch_tess_lightcurve", "fit_transit",
        "compute_equilibrium_temperature", "compute_transit_depth",
        "compute_planet_density", "query_tess_target_list",
    ):
        assert ex_name in names, f"exoplanet tool {ex_name} not registered in TOOLS"


def test_result_provenance_classifies_all_exoplanet_tools():
    from app.services.result_provenance import _DATA_TOOLS, _COMPUTE_TOOLS
    data_tools = {
        "query_exoplanet_archive", "query_confirmed_planets",
        "fetch_tess_lightcurve", "query_tess_target_list",
    }
    compute_tools = {
        "fit_transit", "compute_equilibrium_temperature",
        "compute_transit_depth", "compute_planet_density",
    }
    assert data_tools.issubset(_DATA_TOOLS)
    assert compute_tools.issubset(_COMPUTE_TOOLS)


def test_chat_deadline_table_has_exoplanet_entries():
    import inspect

    import app.api.chat as chat_mod
    src = inspect.getsource(chat_mod)
    for tool in ("fetch_tess_lightcurve", "query_exoplanet_archive"):
        assert f'"{tool}":' in src, f"missing deadline for {tool}"


# ── compute tools (pure, no HTTP) ──────────────────────────────────


def test_exec_compute_t_eq_happy_path():
    from app.services.ai_tools_exoplanet import _exec_compute_equilibrium_temperature

    r = asyncio.run(_exec_compute_equilibrium_temperature({
        "T_star_K": 5778, "R_star_solar": 1.0, "a_au": 1.0,
        "albedo": 0.3, "data_source": "archive_cache",
    }))
    assert r["success"] is True
    assert r["data_origin"] == "cached_real"
    assert 250 <= r["T_eq_K"] <= 260
    assert "bibcode" in r or any(c.get("bibcode") for c in r.get("citations", []))


def test_exec_compute_t_eq_user_supplied_downgrades():
    from app.services.ai_tools_exoplanet import _exec_compute_equilibrium_temperature

    r = asyncio.run(_exec_compute_equilibrium_temperature({
        "T_star_K": 5778, "R_star_solar": 1.0, "a_au": 1.0,
        "data_source": "user_supplied",
    }))
    assert r["data_origin"] == "user_uploaded"


def test_exec_compute_t_eq_invalid_arg_fails_with_contract():
    from app.services.ai_tools_exoplanet import _exec_compute_equilibrium_temperature

    r = asyncio.run(_exec_compute_equilibrium_temperature({
        "T_star_K": -1.0, "R_star_solar": 1.0, "a_au": 1.0,
    }))
    assert r["success"] is False
    assert r["__tool_status__"] == "FAILED"
    assert r["__do_not_claim__"] is True
    assert r["error_class"] == "invalid_argument"


def test_exec_transit_depth_recovers_84ppm_for_earth():
    from app.services.ai_tools_exoplanet import _exec_compute_transit_depth

    r = asyncio.run(_exec_compute_transit_depth({
        "R_p_earth": 1.0, "R_star_solar": 1.0,
    }))
    assert r["success"] is True
    assert 80 <= r["depth_ppm"] <= 90


def test_exec_planet_density_earth():
    from app.services.ai_tools_exoplanet import _exec_compute_planet_density

    r = asyncio.run(_exec_compute_planet_density({
        "M_earth": 1.0, "R_earth": 1.0,
    }))
    assert r["success"] is True
    assert 5.4 <= r["density_g_cm3"] <= 5.6


def test_exec_fit_transit_missing_arguments():
    from app.services.ai_tools_exoplanet import _exec_fit_transit

    r = asyncio.run(_exec_fit_transit({}))
    assert r["success"] is False
    assert r["error_class"] == "missing_argument"


# ── dispatch ────────────────────────────────────────────────────────


def test_dispatch_unknown_tool_returns_failed():
    from app.services.ai_tools_exoplanet import dispatch_exoplanet

    r = asyncio.run(dispatch_exoplanet("not_a_real_tool", {}))
    assert r["success"] is False
    assert r["error_class"] == "unknown_tool"


def test_dispatch_routes_compute_planet_density_correctly():
    from app.services.ai_tools_exoplanet import dispatch_exoplanet

    r = asyncio.run(dispatch_exoplanet("compute_planet_density", {
        "M_earth": 1.0, "R_earth": 1.0,
    }))
    assert r["success"] is True
    assert "density_g_cm3" in r


# ── claim_validator: exoplanet bibcodes + numeric patterns ──────────


def test_exoplanet_canonical_bibcodes_present():
    from app.services.claim_validator import EXOPLANET_CANONICAL_BIBCODES

    assert "2013PASP..125..989A" in EXOPLANET_CANONICAL_BIBCODES  # NEA
    assert "2002ApJ...580L.171M" in EXOPLANET_CANONICAL_BIBCODES  # Mandel-Agol
    assert "2015JATIS...1a4003R" in EXOPLANET_CANONICAL_BIBCODES  # TESS


def test_exoplanet_numeric_patterns_in_PATTERNS():
    from app.services.claim_validator import _PATTERNS

    names = {p[0] for p in _PATTERNS}
    assert "planet_radius_re" in names
    assert "planet_mass_me" in names
    assert "orbital_period_days" in names
    assert "equilibrium_temperature_K" in names
    assert "transit_depth_ppm" in names


def test_exoplanet_manifest_block_chained_into_citation_text():
    """blocked_citation_reply_text source must chain _exoplanet_manifest_block_note."""
    import inspect

    from app.services import claim_validator as cv_mod

    src = inspect.getsource(cv_mod)
    assert "_exoplanet_manifest_block_note" in src
    assert "exoplanet_note" in src
