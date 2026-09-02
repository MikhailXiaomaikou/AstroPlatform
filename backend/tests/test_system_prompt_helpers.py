"""R1.2 regression test: SYSTEM_PROMPT must include the signature list for common astro.* helpers.

Prevents subsequent prompt refactors from accidentally deleting the PART R signature list,
which would cause the AI to revert to "guessing kwargs" mode.
"""


def test_system_prompt_lists_download_and_clean_lightcurve_signature():
    from app.api.chat import SYSTEM_PROMPT

    # download_and_clean_lightcurve signature must include sector/author kwargs
    assert "download_and_clean_lightcurve" in SYSTEM_PROMPT
    assert "sector=None" in SYSTEM_PROMPT
    assert "author=None" in SYSTEM_PROMPT


def test_system_prompt_points_to_available_functions_for_full_list():
    from app.api.chat import SYSTEM_PROMPT

    # must tell AI it can call astro.available_functions() to see all helpers
    assert "available_functions" in SYSTEM_PROMPT


def test_system_prompt_lists_at_least_twelve_astro_helpers():
    """Second tier: at least 12 astro.* helpers must appear in the prompt."""
    from app.api.chat import SYSTEM_PROMPT

    expected_helpers = [
        "astro.search_lightcurve",
        "astro.download_and_clean_lightcurve",
        "astro.transit_search",
        "astro.lomb_scargle_period",
        "astro.phase_fold",
        "astro.extinction_curve",
        "astro.deredden",
        "astro.estimate_ebv",
        "astro.get_isochrone",
        "astro.fit_isochrone",
        "astro.plot_hr_diagram",
        "astro.bpt_classify",
        "astro.classify_variable",
        "astro.compute_absolute_magnitude",
        "astro.compute_luminosity_distance",
        "astro.k_correction",
    ]
    missing = [h for h in expected_helpers if h not in SYSTEM_PROMPT]
    assert len(missing) == 0, f"missing helpers in SYSTEM_PROMPT: {missing}"


def test_system_prompt_warns_against_guessing_kwargs():
    """Prevent AI from guessing kwargs like sector= / quarter= instead of checking the signature."""
    from app.api.chat import SYSTEM_PROMPT

    assert "Never invent kwargs" in SYSTEM_PROMPT or "do not guess" in SYSTEM_PROMPT.lower()


def test_system_prompt_has_model_independent_cosmology_guardrails():
    from app.api.chat import SYSTEM_PROMPT

    assert "Gaussian Process" in SYSTEM_PROMPT
    assert "Om(z) = (E(z)^2 - 1) / ((1+z)^3 - 1)" in SYSTEM_PROMPT
    assert "do not replace the requested non-parametric workflow" in SYSTEM_PROMPT
    assert "LRG bin near `z_eff≈0.51`" in SYSTEM_PROMPT
    assert "bin-level" in SYSTEM_PROMPT
    assert "residuals, pulls" in SYSTEM_PROMPT


def test_system_prompt_routes_transit_fits_to_pro_helper():
    """R20: HD 189733b / Mandel-Agol scenarios must prefer the platform transit fit helper."""
    from app.api.chat import SYSTEM_PROMPT

    assert "astro.pro_fit_transit" in SYSTEM_PROMPT
    assert "Mandel-Agol" in SYSTEM_PROMPT
    assert "hand-roll" in SYSTEM_PROMPT


def test_system_prompt_preserves_requested_separate_cells():
    """R20: when the user explicitly requests separate cells, they must not be merged into a single run_python."""
    from app.api.chat import SYSTEM_PROMPT

    assert "separate cells" in SYSTEM_PROMPT
    assert "Do not concatenate" in SYSTEM_PROMPT


def test_system_prompt_gcvs_fallback_has_correct_columns():
    """W4 (PART W): the GCVS section must provide real CDS column names (not Vmax / Vmin / Name /
    Type). In the B3 regression, the AI guessing these names wasted 4 iterations."""
    from app.api.chat import SYSTEM_PROMPT

    assert "B/gcvs/gcvs_cat" in SYSTEM_PROMPT
    # Correct column names must be present
    assert "magMax" in SYSTEM_PROMPT
    assert "min1" in SYSTEM_PROMPT
    assert "VarType" in SYSTEM_PROMPT
    # Wrong-name warnings also in prompt (tell AI not to guess)
    assert "Vmax" in SYSTEM_PROMPT  # Now appears as a forbidden-guess warning
    assert "describe_tap_table" in SYSTEM_PROMPT


def test_gcvs_registry_has_real_column_names():
    """W4: catalog_registry entry uses CDS-correct column names."""
    from app.services.catalog_registry import get_catalog

    entry = get_catalog('"B/gcvs/gcvs_cat"')
    assert entry is not None
    col_names = {c.name for c in entry.columns}
    # Core columns required for a period lookup
    for required in ("GCVS", "RAJ2000", "DEJ2000", "VarType", "Period",
                     "magMax", "min1", "Epoch", "SpType"):
        assert required in col_names, f"GCVS registry missing column: {required}"


def test_system_prompt_has_clustering_failure_checks():
    """X2 (PART X): SYSTEM_PROMPT teaches the AI to recognise DBSCAN/HDBSCAN silent-failure
    signals (n_clusters=0 / all-outlier / matching-count). In the B6 Pleiades regression,
    the AI treated outliers as members and produced an incorrect CMD analysis.
    Rule presence is sufficient; specific behaviour is not tested here."""
    from app.api.chat import SYSTEM_PROMPT

    assert "Clustering algorithm failure" in SYSTEM_PROMPT
    assert "n_clusters" in SYSTEM_PROMPT
    # three key checks
    assert "90%+" in SYSTEM_PROMPT or "90% are outliers" in SYSTEM_PROMPT
    # the B6 counter-example is explicitly written into the prompt
    assert "DBSCAN found 0 clusters" in SYSTEM_PROMPT or "0 clusters" in SYSTEM_PROMPT


def test_system_prompt_mandates_english_only_reply():
    """X (PART X option D): SYSTEM_PROMPT explicitly requires final replies to be in English,
    and removes the old conflicting 'Always respond in the same language' rule."""
    from app.api.chat import SYSTEM_PROMPT

    # must contain the new English-only section
    assert "English-only reply rule" in SYSTEM_PROMPT
    assert "MUST be in standard English" in SYSTEM_PROMPT
    # old conflicting rule must be removed
    assert "Always respond in the same language" not in SYSTEM_PROMPT
    # allowed-scope explanation
    assert "Greek" in SYSTEM_PROMPT or "α" in SYSTEM_PROMPT
    assert "Å" in SYSTEM_PROMPT


def test_vizier_common_mistakes_covers_gcvs_traps():
    """W4: VIZIER_COMMON_MISTAKES suggests correct column when AI guesses
    wrong GCVS column name."""
    from app.services.catalog_registry import suggest_for_missing_column

    # Name → GCVS / VarName suggestion
    hint_name = suggest_for_missing_column("Name")
    assert hint_name is not None
    assert "GCVS" in hint_name

    # Vmax → magMax suggestion
    hint_vmax = suggest_for_missing_column("Vmax")
    assert hint_vmax is not None
    assert "magMax" in hint_vmax

    # Vmin → min1 / min2 suggestion
    hint_vmin = suggest_for_missing_column("Vmin")
    assert hint_vmin is not None
    assert "min1" in hint_vmin

    # Type → VarType suggestion
    hint_type = suggest_for_missing_column("Type")
    assert hint_type is not None
    assert "VarType" in hint_type


def test_system_prompt_has_cosmology_presets_section():
    """PART AA C3: SYSTEM_PROMPT must list the 4 PART AA presets + their
    bibcodes so the citation validator can match a chat reply that quotes
    "Planck18 H0=67.36" / "Riess22 SH0ES H0=73.04" against the universe.

    A regression on this section means the model loses the reference
    table and either uses an unsourced default or starts inventing
    cosmology bibcodes from training data.
    """
    from app.api.chat import SYSTEM_PROMPT

    # Section header
    assert "COSMOLOGY PRESETS" in SYSTEM_PROMPT

    # All 4 preset names exactly as the cosmology.py module exposes them
    for preset in ("planck18", "planck18_bao", "freedman21_trgb", "riess22_shoes"):
        assert preset in SYSTEM_PROMPT, f"missing preset {preset!r}"

    # Anchor bibcodes — these are the citation strings the validator
    # looks for in the tool_results universe.
    for bibcode in (
        "2020A&A...641A...6P",   # Planck Collab VI 2020
        "2009ApJ...707..916F",    # Fixsen 2009 Tcmb0
        "2021ApJ...919...16F",    # Freedman 2021 TRGB
        "2022ApJ...934L...7R",    # Riess 2022 SH0ES
    ):
        assert bibcode in SYSTEM_PROMPT, f"missing bibcode {bibcode}"

    # User-prompted-cosmology hook
    assert "compare_luminosity_distances" in SYSTEM_PROMPT
    assert "USER-PROMPTED COSMOLOGY HOOK" in SYSTEM_PROMPT


def test_system_prompt_has_tool_retry_budget_section():
    """C-X3: 5+ retry → escalate to abstention or different tool family."""
    from app.api.chat import SYSTEM_PROMPT

    assert "TOOL RETRY BUDGET" in SYSTEM_PROMPT
    assert "5+ times this turn" in SYSTEM_PROMPT
    assert "<tools_returned_nothing/>" in SYSTEM_PROMPT


def test_system_prompt_defaults_fit_line_lfr_to_bayesian_xyerr():
    """C-X3: Bayesian-by-default rule for the line-relation fit. OLS is
    only the fallback for very small samples."""
    from app.api.chat import SYSTEM_PROMPT

    # Rule 0 (the new one) of the methodology checklist
    assert 'fit_method_requested="bayesian_xyerr"' in SYSTEM_PROMPT
    assert "N >= 5" in SYSTEM_PROMPT
    assert "OLS is the fallback" in SYSTEM_PROMPT


def test_system_prompt_requires_lfr_orientation_before_coefficient_comparison():
    """R2 loop guard: LFR slopes are only comparable when dependent
    variable, predictor, and pivot/normalization match.
    """
    from app.api.chat import SYSTEM_PROMPT

    normalised = " ".join(SYSTEM_PROMPT.split())
    assert "Declare fit orientation and pivot" in SYSTEM_PROMPT
    assert "log_luminosity = alpha + beta * log10(FWHM_km_s / 100)" in SYSTEM_PROMPT
    assert "NOT directly comparable" in normalised


# ---------------------------------------------------------------------------
# 2026-09-02 (review H6a): the prompt must not invite exploratory posterior
# numbers into prose — the honesty gate withholds them regardless of wording.
# ---------------------------------------------------------------------------


def test_cosmology_prompt_no_longer_invites_exploratory_posterior_numbers() -> None:
    from app.api.chat import SYSTEM_PROMPT

    for stale in (
        "You MAY discuss the posterior median",
        "our refit recovers w0 = X ± Y",
        "preliminary fit suggests H0 around X",
        "Surface the literal `__exploratory_warning__`",
    ):
        assert stale not in SYSTEM_PROMPT, stale
    assert "stay in the tool card" in SYSTEM_PROMPT
    assert "NEVER write the number in any form" in SYSTEM_PROMPT


def test_exploratory_labelled_h0_is_forbidden_by_prompt_and_gate() -> None:
    """The prompt and the honesty gate now agree: an 'exploratory'-labelled
    H0 quote is forbidden in prose. test_claim_validator pins that the claim
    validator itself allows such a sentence; the honesty gate is the layer
    that withholds it."""
    from app.api.chat import SYSTEM_PROMPT
    from app.services.agent_runtime.honesty import nonpublication_posterior_values

    assert "regardless of wording" in SYSTEM_PROMPT
    escaped = nonpublication_posterior_values(
        "An exploratory chain at this prior gives H0 around 68 km/s/Mpc.",
        [{
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "publication_ready": False,
                "chain_tier": "exploratory",
                "parameters": {"H0": {"median": 67.69, "std": 0.52}},
            },
        }],
    )
    assert escaped == [68.0]


def test_cosmology_prompt_does_not_invent_a_generic_low_ess_diagnosis() -> None:
    """A chain can be demoted off-anchor, for compressed input, for prior
    dominance or for too few independent chains with ESS well above 400.
    Telling the model to say "ESS below threshold" would make it report a
    diagnosis the run never produced (review 2026-09-03)."""
    from app.api.chat import SYSTEM_PROMPT

    assert "not publication-ready (ESS below threshold)" not in SYSTEM_PROMPT
    assert "publication_gate.reasons" in SYSTEM_PROMPT
    assert "Do not invent a generic low-ESS" in SYSTEM_PROMPT


def test_cosmology_prompt_keeps_published_anchors_out_of_mixed_turns() -> None:
    """The honesty gate compares every reply number against the withheld
    posterior and does not exempt an independently published value, so a
    published anchor within 1% of an exploratory median replaces the whole
    reply."""
    from app.api.chat import SYSTEM_PROMPT

    assert "in a turn that ALSO produced a non-publication chain" in SYSTEM_PROMPT
    assert "does not exempt an independently\n  published value" in SYSTEM_PROMPT
