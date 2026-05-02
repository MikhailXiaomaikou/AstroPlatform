"""ASTRO_RESEARCH_FOCUS — L1 tool gating + L4 prompt appendix.

User decision (2026-04-30): platform supports many research domains
(time-domain, spectroscopy, isochrone, photometry, cosmology, ...).
Owner wants to maintain only observational cosmology long-term, but
NOT delete other modules. The agreed approach is "keep code, gate
visibility": when ASTRO_RESEARCH_FOCUS=cosmology, the LLM tool list
is filtered before delivery so non-cosmology tools are physically
invisible — the LLM cannot call them at all and naturally falls
through to Phase F's structured-abstention path for off-topic
questions.

Locks 4 contracts:

1. Default focus is "all" — _filter_tools_by_research_focus is a
   no-op. Existing deployments and tests do not regress.

2. focus="cosmology" — the allowlist contains the core cosmology
   tools (cosmology_mcmc, fit_line_lfr, demagnify_sample, photo-z,
   ADQL/Gaia, distance ladder Cepheid/SN Ia helpers). Non-cosmology
   tools (fit_isochrone for clusters, equivalent_width, pulsar
   physics, source extraction) are dropped.

3. focus="cosmology" — SYSTEM_PROMPT gains the focus appendix; the
   46 existing sections are preserved verbatim (other test files
   keyword-assert those sections, must not break).

4. The filter applies AFTER G3.4 dynamic disable; tools removed by
   either gate are correctly invisible. Ordering matters because
   focus is the outer gate (more permanent) and G3.4 is per-turn.
"""

from __future__ import annotations

import importlib

import pytest


def _reload_chat_with_focus(monkeypatch, focus_value: str):
    """Reimport app.api.chat with a fresh ASTRO_RESEARCH_FOCUS env.

    SYSTEM_PROMPT and the focus filter are evaluated at module load,
    so each parametrized focus value needs a clean import."""
    import app.api.chat as chat_mod

    monkeypatch.setenv("ASTRO_RESEARCH_FOCUS", focus_value)
    importlib.reload(chat_mod)
    return chat_mod


# ── Contract 1: default focus="all" is a no-op ─────────────────────


def test_default_focus_is_all_no_filter(monkeypatch) -> None:
    """No env set (or 'all') → filter must be identity. Existing
    deployments must not see any tool removed."""
    monkeypatch.delenv("ASTRO_RESEARCH_FOCUS", raising=False)
    chat = _reload_chat_with_focus(monkeypatch, "all")

    fake_tools = [
        {"name": "fit_isochrone"},
        {"name": "fit_cosmology_mcmc"},
        {"name": "equivalent_width"},
        {"name": "psf_photometry"},
    ]
    out = chat._filter_tools_by_research_focus(fake_tools)
    assert out == fake_tools  # identity, all 4 tools survive


def test_focus_unset_treated_as_all(monkeypatch) -> None:
    monkeypatch.delenv("ASTRO_RESEARCH_FOCUS", raising=False)
    chat = _reload_chat_with_focus(monkeypatch, "all")

    out = chat._filter_tools_by_research_focus([{"name": "anything"}])
    assert out == [{"name": "anything"}]


def test_focus_unknown_value_falls_back_to_no_filter(monkeypatch) -> None:
    """Typos / unknown focus values must NOT silently delete tools.
    Only the literal 'cosmology' triggers gating."""
    chat = _reload_chat_with_focus(monkeypatch, "stellar")  # not "cosmology"

    fake_tools = [{"name": "fit_isochrone"}, {"name": "fit_cosmology_mcmc"}]
    out = chat._filter_tools_by_research_focus(fake_tools)
    assert out == fake_tools


# ── Contract 2: focus="cosmology" — allowlist content ──────────────


def test_focus_cosmology_keeps_core_cosmology_tools(monkeypatch) -> None:
    chat = _reload_chat_with_focus(monkeypatch, "cosmology")

    must_survive = [
        {"name": "fit_cosmology_mcmc"},
        {"name": "run_cobaya_cosmology"},
        {"name": "get_cosmology_run_status"},
        {"name": "list_cosmology_datasets"},
        {"name": "build_cosmology_likelihood"},
        {"name": "build_cosmology_robustness_matrix"},
        {"name": "compare_luminosity_distances"},
        {"name": "fit_line_lfr"},
        {"name": "demagnify_sample"},
        {"name": "extract_literature_tables"},
        {"name": "estimate_photo_z_pro"},
        {"name": "run_adql"},
        {"name": "crossmatch_catalogs"},
        {"name": "search_lightcurve"},   # Cepheid / SN Ia distance ladder
        {"name": "lomb_scargle_period"},  # Cepheid period
        {"name": "transient_classifier"},  # SN Ia
        {"name": "run_python"},
        {"name": "search_literature"},
    ]
    out = chat._filter_tools_by_research_focus(must_survive)
    survived_names = {t["name"] for t in out}
    expected_names = {t["name"] for t in must_survive}
    missing = expected_names - survived_names
    assert not missing, f"Cosmology focus dropped core tools: {missing}"


def test_focus_cosmology_drops_non_cosmology_tools(monkeypatch) -> None:
    chat = _reload_chat_with_focus(monkeypatch, "cosmology")

    must_drop = [
        {"name": "fit_isochrone"},          # stellar isochrone, cluster ages
        {"name": "fit_transit"},            # exoplanet
        {"name": "transit_search_bls"},     # exoplanet search
        {"name": "equivalent_width"},       # stellar spectroscopy
        {"name": "spectral_analysis_pro"},  # stellar atmosphere
        {"name": "psf_photometry"},         # CCD reduction
        {"name": "source_extract"},         # CCD detection
        {"name": "pulsar_derived_quantities"},  # pulsar physics
    ]
    out = chat._filter_tools_by_research_focus(must_drop)
    assert out == [], (
        f"Cosmology focus must drop all 8 non-cosmology tools; "
        f"survived: {[t['name'] for t in out]}"
    )


def test_focus_cosmology_mixed_list_only_keeps_allowlisted(monkeypatch) -> None:
    chat = _reload_chat_with_focus(monkeypatch, "cosmology")

    mixed = [
        {"name": "fit_cosmology_mcmc"},   # keep
        {"name": "list_cosmology_datasets"},  # keep
        {"name": "build_cosmology_likelihood"},  # keep
        {"name": "fit_isochrone"},         # drop
        {"name": "fit_line_lfr"},          # keep
        {"name": "equivalent_width"},      # drop
        {"name": "run_python"},            # keep
    ]
    out = chat._filter_tools_by_research_focus(mixed)
    names = [t["name"] for t in out]
    assert names == [
        "fit_cosmology_mcmc",
        "list_cosmology_datasets",
        "build_cosmology_likelihood",
        "fit_line_lfr",
        "run_python",
    ]


def test_focus_cosmology_preserves_tool_dict_intact(monkeypatch) -> None:
    """Filter must not mutate or strip fields on surviving tools."""
    chat = _reload_chat_with_focus(monkeypatch, "cosmology")

    rich_tool = {
        "name": "fit_cosmology_mcmc",
        "description": "Bayesian H0 / Omega_m fit",
        "input_schema": {"type": "object", "properties": {"foo": {"type": "string"}}},
    }
    out = chat._filter_tools_by_research_focus([rich_tool])
    assert out == [rich_tool]  # equality including all keys


# ── Contract 3: SYSTEM_PROMPT appendix ─────────────────────────────


def test_focus_cosmology_appends_focus_section_to_prompt(monkeypatch) -> None:
    chat = _reload_chat_with_focus(monkeypatch, "cosmology")
    assert "RESEARCH FOCUS: OBSERVATIONAL COSMOLOGY" in chat.SYSTEM_PROMPT
    assert "ASTRO_RESEARCH_FOCUS=all" in chat.SYSTEM_PROMPT
    # Must reference structured abstention (so LLM knows where to fall through).
    assert "structured abstention" in chat.SYSTEM_PROMPT.lower() or "STRUCTURED ABSTENTION" in chat.SYSTEM_PROMPT


def test_focus_all_does_NOT_append_focus_section(monkeypatch) -> None:
    """Default focus must leave SYSTEM_PROMPT unchanged. Existing
    keyword-asserting tests in test_admin_literature etc. depend on
    the prompt staying its current shape."""
    chat = _reload_chat_with_focus(monkeypatch, "all")
    assert "RESEARCH FOCUS: OBSERVATIONAL COSMOLOGY" not in chat.SYSTEM_PROMPT


def test_focus_cosmology_preserves_all_existing_prompt_sections(monkeypatch) -> None:
    """Append must NOT delete or reorder anything in the original
    46 sections. Several other test files assert these keywords;
    this test catches regressions early before downstream tests fire.
    """
    chat = _reload_chat_with_focus(monkeypatch, "cosmology")

    must_preserve_keywords = [
        # Phase F / zero-fabrication architecture
        "ZERO-FABRICATION CONTRACT",
        "STRUCTURED ABSTENTION",
        # PART AF / multi-survey rules
        "Multi-survey sample composition",
        "Subsample fallback transparency",
        # PART AG / cite-after-extract
        "Cite-after-extract",
        # PART AF C3 / explicit cosmology call examples
        "EXACT CALL EXAMPLES",
        # PART AF C6 / demagnify no-op
        "0 lensed sources detected",
        # Round 12 / fit_orientation comparability
        "Declare fit orientation and pivot",
    ]
    for keyword in must_preserve_keywords:
        assert keyword in chat.SYSTEM_PROMPT, (
            f"Focus appendix accidentally removed/altered prompt keyword "
            f"{keyword!r}; downstream tests will red."
        )


# ── Contract 4: env value normalization ────────────────────────────


@pytest.mark.parametrize("raw_value,expected_filtered", [
    ("cosmology", True),
    ("COSMOLOGY", True),
    ("Cosmology", True),
    ("  cosmology  ", True),  # whitespace trimmed
    ("all", False),
    ("", False),
    ("xyz", False),
])
def test_focus_env_value_is_lowercased_and_stripped(
    monkeypatch, raw_value: str, expected_filtered: bool,
) -> None:
    chat = _reload_chat_with_focus(monkeypatch, raw_value)
    fake_tools = [{"name": "fit_isochrone"}, {"name": "fit_cosmology_mcmc"}]
    out = chat._filter_tools_by_research_focus(fake_tools)
    if expected_filtered:
        # focus active → fit_isochrone dropped
        assert out == [{"name": "fit_cosmology_mcmc"}]
    else:
        # focus inactive → both survive
        assert out == fake_tools


# ── Contract: cleanup ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _restore_chat_module(monkeypatch):
    """Restore the chat module to its production env state at end of
    every test, so subsequent test files (test_h_regression etc.) see
    a fresh module with default focus, not whatever the last
    parametrized run left behind."""
    yield
    monkeypatch.delenv("ASTRO_RESEARCH_FOCUS", raising=False)
    import app.api.chat as chat_mod

    importlib.reload(chat_mod)
