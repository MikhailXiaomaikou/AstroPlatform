"""M1 Phase 3 — prompt_loader.build_system_prompt regression tests.

Locks the contract that SYSTEM_PROMPT is correctly assembled from
backend/app/prompts/ three-layer structure, with the right sections
included/excluded per ASTRO_RESEARCH_FOCUS.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_prompt_loader_cache():
    """Clear @lru_cache between tests so each test sees fresh assembly."""
    from app.services.prompt_loader import build_allowed_tools, build_system_prompt

    build_system_prompt.cache_clear()
    build_allowed_tools.cache_clear()
    yield
    build_system_prompt.cache_clear()
    build_allowed_tools.cache_clear()


# ── focus=cosmology ────────────────────────────────────────────────


def test_cosmology_prompt_contains_cross_cutting_rules():
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("cosmology")
    assert "USER-PROMPT INJECTION DEFENSE" in sp
    assert "ZERO-FABRICATION CONTRACT" in sp
    assert "STRUCTURED ABSTENTION" in sp
    assert "ANTI-INSTRUCTION-REFLECTION" in sp


def test_cosmology_prompt_contains_core_infrastructure():
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("cosmology")
    assert "ADQL aggregate-function semantics" in sp
    assert "Gaia DR3 data completeness" in sp
    assert "Python Code Execution" in sp


def test_cosmology_prompt_contains_cosmology_workflow():
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("cosmology")
    assert "COSMOLOGY PRESETS" in sp
    assert "Distance estimation hierarchy" in sp
    assert "Variable star workflow" in sp  # RR Lyrae / Cepheid distance ladder
    assert "Research Mode" in sp


def test_cosmology_prompt_contains_overlap_tool_section():
    """query_high_velocity_stars is an overlap tool — its prompt section
    must live in cosmology/prompt.md (not dormant_stellar) so cosmology
    focus shows the LLM how to use it."""
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("cosmology")
    assert "Milky Way escape velocity" in sp or "query_high_velocity_stars" in sp


def test_cosmology_prompt_excludes_dormant_sections():
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("cosmology")
    # Dormant module headers must not appear
    assert "X-ray spectral analysis workflow" not in sp
    assert "Pulsar analysis" not in sp
    assert "Solar system objects" not in sp
    assert "AGN SED decomposition" not in sp


def test_cosmology_prompt_appends_focus_appendix():
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("cosmology")
    assert "RESEARCH FOCUS: OBSERVATIONAL COSMOLOGY" in sp
    assert "ASTRO_RESEARCH_FOCUS=all" in sp


def test_archive_manifest_placeholder_is_resolved():
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("cosmology")
    assert "__ARCHIVE_MANIFEST__" not in sp, "placeholder must be resolved at load time"


# ── focus=all ──────────────────────────────────────────────────────


def test_all_focus_loads_every_module():
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("all")
    # Should contain cosmology AND every dormant module's section keywords
    assert "COSMOLOGY PRESETS" in sp
    assert "X-ray spectral analysis workflow" in sp
    assert "Pulsar analysis" in sp
    assert "Solar system objects" in sp


def test_all_focus_does_not_append_cosmology_appendix():
    """The RESEARCH FOCUS appendix is misleading under focus=all (LLM
    sees all tools, no module gating), so it must NOT be appended."""
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("all")
    assert "=== RESEARCH FOCUS: OBSERVATIONAL COSMOLOGY ===" not in sp


# ── unknown focus → fallback ───────────────────────────────────────


def test_unknown_focus_falls_back_to_all():
    from app.services.prompt_loader import build_system_prompt

    sp_xyz = build_system_prompt("xyz")
    sp_all = build_system_prompt("all")
    # Unknown focus should behave like "all"
    assert sp_xyz == sp_all


# ── allowed tools ──────────────────────────────────────────────────


def test_cosmology_allowed_tools_includes_core_and_cosmology():
    from app.services.prompt_loader import build_allowed_tools

    tools = build_allowed_tools("cosmology")
    # Core
    assert "run_adql" in tools
    assert "run_python" in tools
    assert "search_literature" in tools
    # Cosmology-specific
    assert "fit_cosmology_mcmc" in tools
    assert "run_cosmology_robustness_matrix" in tools
    assert "plan_research_program" in tools  # research_program suite
    # Overlap
    assert "query_high_velocity_stars" in tools
    assert "search_lightcurve" in tools


def test_cosmology_allowed_tools_excludes_dormant():
    from app.services.prompt_loader import build_allowed_tools

    tools = build_allowed_tools("cosmology")
    # Dormant module tools must not appear
    assert "x_ray_spectral_fit" not in tools  # _dormant_xray_spectroscopy
    assert "pulsar_derived_quantities" not in tools  # _dormant_pulsar_timing
    assert "mine_paper_tools" not in tools  # _dormant_paper_tool_mining
    assert "fit_isochrone" not in tools  # _dormant_stellar
    assert "fit_transit_model" not in tools  # _dormant_exoplanet


def test_all_focus_includes_every_tool():
    """ASTRO_RESEARCH_FOCUS=all must expose every TOOLS entry."""
    from app.services.ai_tools import TOOLS
    from app.services.prompt_loader import build_allowed_tools

    all_tools = {t["name"] for t in TOOLS}
    allowed = build_allowed_tools("all")
    missing = all_tools - allowed
    assert not missing, f"focus=all dropped tools: {sorted(missing)}"


# ── focus=solar_system (M0 Commit 1, 2026-05-18) ───────────────────


def test_solar_system_prompt_loaded_when_focus_is_solar_system():
    """focus=solar_system loads content from modules/solar_system/prompt.md."""
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("solar_system")
    # Known strings from Solar-system module prompt.md (seeded from _dormant_solar_system):
    assert "Solar system objects" in sp
    # Cross-module infrastructure (core/) must still be present
    assert "ADQL aggregate-function semantics" in sp
    assert "Python Code Execution" in sp


def test_solar_system_focus_excludes_cosmology_workflow():
    """focus=solar_system should not load cosmology module sections."""
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("solar_system")
    assert "COSMOLOGY PRESETS" not in sp
    assert "Distance estimation hierarchy" not in sp
    assert "Variable star workflow" not in sp


def test_solar_system_allowed_tools_at_commit1_is_core_only():
    """M0 Commit 1: manifest.yaml tools is [], only core tools remain under solar_system focus."""
    from app.services.prompt_loader import build_allowed_tools

    tools = build_allowed_tools("solar_system")
    # Core tools (from core/infrastructure.yaml) must be present
    assert "run_python" in tools
    assert "search_literature" in tools
    assert "run_adql" in tools
    # Cosmology-specific tools must not be present
    assert "fit_cosmology_mcmc" not in tools
    assert "run_cosmology_robustness_matrix" not in tools
    assert "build_cosmology_likelihood" not in tools


def test_all_focus_still_loads_solar_system_after_mv():
    """After mv _dormant_solar_system → solar_system, focus=all fallback can still
    iterate to the solar_system module (active dirs also count)."""
    from app.services.prompt_loader import build_system_prompt

    sp = build_system_prompt("all")
    assert "Solar system objects" in sp
    assert "COSMOLOGY PRESETS" in sp  # cosmology also present
