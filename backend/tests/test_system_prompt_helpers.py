"""R1.2 回归测试: SYSTEM_PROMPT 必须包含常用 astro.* helper 签名清单.

防止后续 prompt 重构时把 PART R 加的那段签名清单误删掉, 导致 AI 又回到
"猜 kwarg" 模式.
"""


def test_system_prompt_lists_download_and_clean_lightcurve_signature():
    from app.api.chat import SYSTEM_PROMPT

    # download_and_clean_lightcurve 签名必须带 sector/author kwarg
    assert "download_and_clean_lightcurve" in SYSTEM_PROMPT
    assert "sector=None" in SYSTEM_PROMPT
    assert "author=None" in SYSTEM_PROMPT


def test_system_prompt_points_to_available_functions_for_full_list():
    from app.api.chat import SYSTEM_PROMPT

    # 必须告诉 AI 可以 call astro.available_functions() 查全部 helper
    assert "available_functions" in SYSTEM_PROMPT


def test_system_prompt_lists_at_least_twelve_astro_helpers():
    """第二档: 至少 12 个 astro.* helper 出现在 prompt 里."""
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
    """防 AI 再猜 sector= / quarter= 这类 kwarg 而不检查签名."""
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
    """R20: HD 189733b / Mandel-Agol 场景优先走平台 transit fit helper."""
    from app.api.chat import SYSTEM_PROMPT

    assert "astro.pro_fit_transit" in SYSTEM_PROMPT
    assert "Mandel-Agol" in SYSTEM_PROMPT
    assert "hand-roll" in SYSTEM_PROMPT


def test_system_prompt_preserves_requested_separate_cells():
    """R20: 用户明确要求 separate cells 时不能合并成一个 run_python."""
    from app.api.chat import SYSTEM_PROMPT

    assert "separate cells" in SYSTEM_PROMPT
    assert "Do not concatenate" in SYSTEM_PROMPT


def test_system_prompt_gcvs_fallback_has_correct_columns():
    """W4 (PART W): GCVS 段必须给出真实 CDS 列名 (非 Vmax / Vmin / Name /
    Type). B3 回归里 AI 猜这些列名导致 4 iterations 浪费."""
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
    """X2 (PART X): SYSTEM_PROMPT 教 AI 识别 DBSCAN/HDBSCAN silent-failure
    信号 (n_clusters=0 / all-outlier / matching-count). B6 Pleiades 回归里
    AI 把 outliers 当成员导致错误 CMD 分析. 规则存在即可, 不测具体行为."""
    from app.api.chat import SYSTEM_PROMPT

    assert "Clustering algorithm failure" in SYSTEM_PROMPT
    assert "n_clusters" in SYSTEM_PROMPT
    # 三条关键检查
    assert "90%+" in SYSTEM_PROMPT or "90% are outliers" in SYSTEM_PROMPT
    # B6 反例被显式写入 prompt
    assert "DBSCAN found 0 clusters" in SYSTEM_PROMPT or "0 clusters" in SYSTEM_PROMPT


def test_system_prompt_mandates_english_only_reply():
    """X (PART X 方案 D): SYSTEM_PROMPT 明确要求 final reply 必须英文,
    删除了旧的 'Always respond in the same language' 冲突规则."""
    from app.api.chat import SYSTEM_PROMPT

    # 必须含新 English-only 段
    assert "English-only reply rule" in SYSTEM_PROMPT
    assert "MUST be in standard English" in SYSTEM_PROMPT
    # 旧冲突规则必须删
    assert "Always respond in the same language" not in SYSTEM_PROMPT
    # 允许范围说明
    assert "Greek" in SYSTEM_PROMPT or "α" in SYSTEM_PROMPT
    assert "Å" in SYSTEM_PROMPT


def test_vizier_common_mistakes_covers_gcvs_traps():
    """W4: VIZIER_COMMON_MISTAKES suggests correct column when AI guesses
    wrong GCVS column name."""
    from app.services.catalog_registry import suggest_for_missing_column

    # Name → GCVS / VarName
    hint_name = suggest_for_missing_column("Name")
    assert hint_name is not None
    assert "GCVS" in hint_name

    # Vmax → magMax
    hint_vmax = suggest_for_missing_column("Vmax")
    assert hint_vmax is not None
    assert "magMax" in hint_vmax

    # Vmin → min1 / min2
    hint_vmin = suggest_for_missing_column("Vmin")
    assert hint_vmin is not None
    assert "min1" in hint_vmin

    # Type → VarType
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
