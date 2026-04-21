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
