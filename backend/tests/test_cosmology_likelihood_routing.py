from __future__ import annotations


def test_cosmology_likelihood_workflow_detects_bao_sn_cmb_model_prompt() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_models_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "请基于可用的 BAO、SN Ia 和 CMB compressed/prior 数据，"
        "比较 flat ΛCDM、wCDM、w0waCDM 的适用性。"
    )

    assert _is_cosmology_likelihood_workflow(prompt)
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "desi_dr1_bao",
        "pantheon_plus",
        "planck2018_compressed",
    ]
    assert _cosmology_models_from_prompt(prompt) == ["lcdm", "wcdm", "w0wa_cdm"]

    calls = _cosmology_likelihood_build_calls_from_prompt(prompt)
    assert [call["name"] for call in calls] == ["build_cosmology_likelihood"] * 3
    assert [call["input"]["model"] for call in calls] == ["lcdm", "wcdm", "w0wa_cdm"]
    assert all(
        call["input"]["dataset_keys"]
        == ["desi_dr1_bao", "pantheon_plus", "planck2018_compressed"]
        for call in calls
    )


def test_research_program_workflow_detects_research_intent() -> None:
    from app.api.chat import _is_research_program_workflow

    assert _is_research_program_workflow(
        "I want to research DESI BAO + SN + CMB robustness for dark energy."
    )
    assert not _is_research_program_workflow(
        "帮我找几篇关于 BAO 重建算法的综述论文，先不用做模型约束。"
    )


def test_research_matrix_dataset_keys_satisfy_anchor_gate() -> None:
    """BAO+CMB research cells should count as selecting the CMB anchor.

    The chat-side anchor guard used to inspect only top-level datasets_used.
    Research Matrix stores selected datasets on each cell, so a valid
    BAO+CMB compressed result could be misread as an unsupported Planck/CMB
    anchor and the final prose would be withheld.
    """
    from app.api.chat import (
        _cosmology_dataset_keys_present,
        _unsupported_cosmology_anchor_numeric_comparison,
    )
    from app.services.research_program import plan_research_program, run_research_matrix

    plan = plan_research_program(
        question="Research DESI BAO + Pantheon+ + Planck CMB LCDM consistency."
    )["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)
    tool_results = [{"tool": "run_research_matrix", "result": matrix}]

    assert "planck2018_compressed" in _cosmology_dataset_keys_present(tool_results)
    assert not _unsupported_cosmology_anchor_numeric_comparison(
        "BAO + CMB gives H0 = 67.3 in the compressed preliminary matrix.",
        tool_results,
    )


def test_cosmology_likelihood_workflow_ignores_plain_literature_requests() -> None:
    from app.api.chat import _is_cosmology_likelihood_workflow

    assert not _is_cosmology_likelihood_workflow(
        "帮我找几篇关于 BAO 重建算法的综述论文，先不用做模型约束。"
    )


def test_multi_sn_comparison_routes_to_robustness_matrix() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_supernova_sets_from_prompt,
    )

    prompt = (
        "用 Pantheon+、DES-5YR、Union3 与 DESI DR1 BAO 做模型无关 GP "
        "重构，并比较 SN compilation 的 consistency。"
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "desi_dr1_bao",
        "pantheon_plus",
        "des_sn5yr",
        "union3",
    ]
    assert _cosmology_supernova_sets_from_prompt(prompt) == [
        "pantheon_plus",
        "des_sn5yr",
        "union3",
    ]

    calls = _cosmology_likelihood_build_calls_from_prompt(prompt)
    assert [call["name"] for call in calls] == ["build_cosmology_robustness_matrix"]
    assert [call["input"]["model"] for call in calls] == ["lcdm"]
    assert calls[0]["input"]["supernova_sets"] == ["pantheon_plus", "des_sn5yr", "union3"]
    assert calls[0]["input"]["include_h0_prior"] is False


def test_shoes_h0_prior_prompt_routes_to_registry_key() -> None:
    from app.api.chat import _cosmology_dataset_keys_from_prompt

    prompt = "请用 DESI DR1 BAO + SH0ES H0 prior 检查 flat ΛCDM 的 H0 consistency。"

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "desi_dr1_bao",
        "shoes_h0_riess22",
    ]


def test_trgb_h0_prompt_routes_to_trgb_not_shoes() -> None:
    from app.api.chat import _cosmology_dataset_keys_from_prompt

    prompt = (
        "I am testing a TRGB distance-ladder H0 workflow as an alternative "
        "to Cepheid-calibrated SH0ES. Use any registered TRGB/H0 prior "
        "products if available."
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == ["trgb_h0_freedman19"]


def test_h0_prior_comparison_routes_registered_anchors() -> None:
    from app.api.chat import _cosmology_dataset_keys_from_prompt

    prompt = (
        "I am comparing late-universe H0 priors from SH0ES, TRGB, lensing, "
        "and megamasers against CMB compressed constraints. Use only registered "
        "H0-prior products; if some anchors are missing, list the registry gaps."
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "planck2018_compressed",
        "trgb_h0_freedman19",
        "h0licow_h0",
        "megamaser_h0_pesce20",
        "shoes_h0_riess22",
    ]


def test_time_delay_and_megamaser_h0_prompts_route_to_specific_priors() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_likelihood_run_calls_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    lens_prompt = (
        "I am studying strong-lens time-delay cosmography as an H0 constraint. "
        "Use registered H0-prior or likelihood products if present."
    )
    megamaser_prompt = (
        "I am checking geometric megamaser H0 constraints. Use registered "
        "megamaser/H0 prior products if present."
    )

    assert _is_cosmology_likelihood_workflow(lens_prompt) is True
    assert _is_cosmology_likelihood_workflow(megamaser_prompt) is True
    assert _cosmology_dataset_keys_from_prompt(lens_prompt) == ["h0licow_h0"]
    assert _cosmology_dataset_keys_from_prompt(megamaser_prompt) == [
        "megamaser_h0_pesce20",
    ]
    assert _cosmology_likelihood_build_calls_from_prompt(lens_prompt)[0]["input"]["dataset_keys"] == [
        "h0licow_h0",
    ]
    assert _cosmology_likelihood_run_calls_from_prompt(megamaser_prompt)[0]["input"]["dataset_keys"] == [
        "megamaser_h0_pesce20",
    ]


def test_pre_desi_bao_product_workflow_routes_to_sdss_not_desi() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_likelihood_run_calls_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "I am testing a pre-DESI BAO + CMB consistency workflow using "
        "SDSS/BOSS/eBOSS/6dF-style BAO. Do not route to DESI unless "
        "explicitly needed. Explain which BAO products are config-only "
        "and whether any posterior can be run this turn."
    )

    assert _is_cosmology_likelihood_workflow(prompt) is True
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "sdss_6df_bao",
        "planck2018_compressed",
    ]
    assert _cosmology_likelihood_build_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "sdss_6df_bao",
        "planck2018_compressed",
    ]
    assert _cosmology_likelihood_run_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "sdss_6df_bao",
        "planck2018_compressed",
    ]


def test_act_era_cross_check_prompt_is_a_cosmology_workflow() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "I want an ACT-era cross-check before DESI DR1 existed. Use "
        "SDSS/BOSS/eBOSS/6dF BAO rather than DESI, plus ACT/Planck products, "
        "and explain executable vs config-only pieces."
    )

    assert _is_cosmology_likelihood_workflow(prompt) is True
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "sdss_6df_bao",
        "planck2018_compressed",
        "act_dr6_lensing",
    ]


def test_spt_workflow_lists_spt_planck_act_but_suppresses_surrogate_posterior() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_likelihood_run_calls_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "I am checking an SPT-3G damping-tail CMB workflow. Use registered "
        "SPT/Planck/ACT products if present; if the SPT likelihood is "
        "external-only, do not produce posterior numbers."
    )

    assert _is_cosmology_likelihood_workflow(prompt) is True
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "planck2018_compressed",
        "act_dr6_lensing",
        "spt3g_cmb",
    ]
    assert _cosmology_likelihood_build_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "act_dr6_lensing",
        "spt3g_cmb",
    ]
    assert _cosmology_likelihood_run_calls_from_prompt(prompt) == []


def test_generic_executable_observational_cosmology_prompt_routes_to_registry_chains() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_likelihood_run_calls_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "I am comparing observational-cosmology probes but want every number "
        "tied to a current-turn tool result. Use registry and executable chains "
        "only; do not rely on remembered paper conclusions, even if they are famous."
    )

    assert _is_cosmology_likelihood_workflow(prompt) is True
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "planck2018_compressed",
        "act_dr6_lensing",
        "kids1000_wl",
    ]
    assert _cosmology_likelihood_build_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "act_dr6_lensing",
        "kids1000_wl",
    ]
    assert _cosmology_likelihood_run_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "act_dr6_lensing",
        "kids1000_wl",
    ]


def test_chinese_multiprobe_prompt_routes_generic_weak_lensing() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_likelihood_run_calls_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "我在做观测宇宙学多探针一致性检查。请只基于已注册且本轮可执行的"
        "数据产品评估 Planck/ACT、BAO、SN 和 weak-lensing 的可用性；"
        "不能执行的 likelihood 只报告配置状态，不要给 posterior 数值。"
    )

    assert _is_cosmology_likelihood_workflow(prompt) is True
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "desi_dr1_bao",
        "pantheon_plus",
        "planck2018_compressed",
        "act_dr6_lensing",
        "kids1000_wl",
        "des_y3_3x2pt",
        "hsc_y1_cosmic_shear",
    ]
    assert _cosmology_likelihood_build_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "desi_dr1_bao",
        "pantheon_plus",
        "planck2018_compressed",
        "act_dr6_lensing",
        "kids1000_wl",
        "des_y3_3x2pt",
        "hsc_y1_cosmic_shear",
    ]
    assert _cosmology_likelihood_run_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "desi_dr1_bao",
        "pantheon_plus",
        "planck2018_compressed",
        "act_dr6_lensing",
        "kids1000_wl",
        "des_y3_3x2pt",
        "hsc_y1_cosmic_shear",
    ]


def test_des_sn_workflow_does_not_turn_negated_bao_into_matrix() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "I am testing a DES-SN 5YR supernova cosmology workflow. Use DES-SN "
        "and optionally Pantheon+/Union3 only if requested by registry routing; "
        "do not add BAO or CMB unless needed for the stated analysis."
    )

    assert _is_cosmology_likelihood_workflow(prompt) is True
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "pantheon_plus",
        "des_sn5yr",
        "union3",
    ]
    calls = _cosmology_likelihood_build_calls_from_prompt(prompt)
    assert [call["name"] for call in calls] == ["build_cosmology_likelihood"]
    assert calls[0]["input"]["dataset_keys"] == [
        "pantheon_plus",
        "des_sn5yr",
        "union3",
    ]


def test_sn_only_negation_does_not_exclude_preceding_sn_datasets() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_forbidden_probe_families,
        _cosmology_likelihood_run_calls_from_prompt,
    )

    prompt = (
        "I am doing a supernova-only compilation check with Pantheon+, DES-SN, "
        "and Union3. Do not include BAO, CMB, weak lensing, or H0 priors. "
        "If no executable SN likelihood is available, say so without posterior numbers."
    )

    assert _cosmology_forbidden_probe_families(prompt) == {"bao", "cmb", "wl", "h0"}
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "pantheon_plus",
        "des_sn5yr",
        "union3",
    ]
    calls = _cosmology_likelihood_run_calls_from_prompt(prompt)
    assert calls[0]["input"]["dataset_keys"] == [
        "pantheon_plus",
        "des_sn5yr",
        "union3",
    ]


def test_with_and_without_cmb_is_not_parsed_as_cmb_exclusion() -> None:
    from app.api.chat import _cosmology_dataset_keys_from_prompt

    prompt = (
        "I am checking robustness across DESI BAO plus multiple SN "
        "compilations. Build the available robustness matrix for Pantheon+, "
        "DES-SN, and Union3, with and without CMB if executable."
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "desi_dr1_bao",
        "pantheon_plus",
        "des_sn5yr",
        "union3",
        "planck2018_compressed",
    ]


def test_generic_cmb_lensing_routes_to_act_dr6_lensing() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_models_from_prompt,
    )

    prompt = (
        "I am testing whether CMB lensing plus CMB compressed products can say "
        "anything about neutrino mass. Use registered likelihood products only "
        "and block any m_nu number unless the chain is publication-ready for that model."
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "planck2018_compressed",
        "act_dr6_lensing",
    ]
    assert _cosmology_models_from_prompt(prompt) == ["lcdm_mnu"]
    calls = _cosmology_likelihood_build_calls_from_prompt(prompt)
    assert calls[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "act_dr6_lensing",
    ]


def test_hz_cosmic_chronometer_prompt_routes_to_registry() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "I am checking an H(z) cosmic-chronometer expansion-history workflow. "
        "Use registered cosmic-chronometer data if executable; otherwise explain "
        "what external table or covariance is missing."
    )

    assert _is_cosmology_likelihood_workflow(prompt) is True
    assert _cosmology_dataset_keys_from_prompt(prompt) == ["cosmic_chronometers"]


def test_bao_only_desi_or_pre_desi_routes_both_without_cmb_h0() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_forbidden_probe_families,
    )

    prompt = (
        "I am doing a BAO-only distance-ratio check without CMB calibration or H0 prior. "
        "Use DESI or pre-DESI BAO products as appropriate, and do not infer absolute H0 "
        "without rd calibration."
    )

    assert _cosmology_forbidden_probe_families(prompt) == {"cmb", "h0"}
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "desi_dr1_bao",
        "sdss_6df_bao",
    ]


def test_curvature_and_neutrino_extensions_route_to_supported_models() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_models_from_prompt,
    )

    prompt = (
        "请基于 ACT DR6 CMB lensing、Planck primary CMB、BAO 数据，"
        "评估 ΛCDM consistency，并说明 neutrino-mass/curvature 扩展如何检验。"
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "sdss_6df_bao",
        "planck2018_compressed",
        "act_dr6_lensing",
    ]
    assert _cosmology_models_from_prompt(prompt) == ["lcdm", "ok_lcdm", "lcdm_mnu"]

    calls = _cosmology_likelihood_build_calls_from_prompt(prompt)
    assert [call["input"]["model"] for call in calls] == ["lcdm", "ok_lcdm", "lcdm_mnu"]


def test_act_dr6_weak_lensing_prompt_routes_to_act_era_bao_and_wl_surveys() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_likelihood_run_calls_from_prompt,
    )

    prompt = (
        "我在做 CMB lensing 与低红移距离数据的观测宇宙学交叉检验。"
        "请基于 ACT DR6 CMB lensing、Planck CMB lensing/primary CMB、"
        "BAO 数据和 weak-lensing survey 的可用资料，规划并尝试评估 "
        "ΛCDM 下的 S8/H0/Ωm consistency、与 galaxy weak lensing 的 S8 差异，"
        "以及 neutrino-mass/curvature 扩展该如何检验。"
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "sdss_6df_bao",
        "planck2018_compressed",
        "act_dr6_lensing",
        "des_y3_3x2pt",
        "kids1000_wl",
        "hsc_y1_cosmic_shear",
    ]
    calls = _cosmology_likelihood_build_calls_from_prompt(prompt)
    assert [call["input"]["model"] for call in calls] == ["lcdm", "ok_lcdm", "lcdm_mnu"]
    assert all(
        call["input"]["dataset_keys"]
        == [
            "sdss_6df_bao",
            "planck2018_compressed",
            "act_dr6_lensing",
            "des_y3_3x2pt",
            "kids1000_wl",
            "hsc_y1_cosmic_shear",
        ]
        for call in calls
    )

    run_calls = _cosmology_likelihood_run_calls_from_prompt(prompt)
    assert [call["name"] for call in run_calls] == ["run_cosmology_likelihood_chain"]
    assert run_calls[0]["input"]["model"] == "lcdm"
    assert run_calls[0]["input"]["dataset_keys"] == [
        "sdss_6df_bao",
        "planck2018_compressed",
        "act_dr6_lensing",
        "des_y3_3x2pt",
        "kids1000_wl",
        "hsc_y1_cosmic_shear",
    ]


def test_cmb_only_prompt_respects_explicit_dataset_exclusions() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_likelihood_run_calls_from_prompt,
    )

    prompt = (
        "我在做 CMB-only consistency 检验。请基于 Planck compressed prior "
        "与 ACT DR6 CMB lensing 的可用资料，在 flat ΛCDM 下只比较 "
        "H0、Ωm、σ8、S8；不要加入 BAO、SN 或 weak lensing。"
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "planck2018_compressed",
        "act_dr6_lensing",
    ]
    assert _cosmology_likelihood_build_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "act_dr6_lensing",
    ]
    assert _cosmology_likelihood_run_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "act_dr6_lensing",
    ]


def test_english_cmb_only_prompt_respects_do_not_include_exclusions() -> None:
    from app.api.chat import _cosmology_dataset_keys_from_prompt

    prompt = (
        "I am doing a CMB-only consistency check. Using only the available "
        "Planck compressed prior and ACT DR6 CMB lensing information, compare "
        "H0, Omega_m, sigma8, and S8 under flat LCDM; do not include BAO, "
        "SN, or weak lensing."
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "planck2018_compressed",
        "act_dr6_lensing",
    ]


def test_supernova_only_prompt_does_not_route_to_bao_robustness_matrix() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_likelihood_run_calls_from_prompt,
        _should_build_cosmology_robustness_matrix,
    )

    prompt = (
        "我在做 Type Ia supernova compilation 的稳健性检查。请基于 "
        "Pantheon+、DES-SN 5YR、Union3 的可用资料，在 flat ΛCDM 下判断 "
        "本轮是否能得到 posterior；如果只能生成外部 likelihood 配置，"
        "请不要给 H0/Ωm/S8 数值。"
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "pantheon_plus",
        "des_sn5yr",
        "union3",
    ]
    assert _should_build_cosmology_robustness_matrix(prompt) is False
    assert [call["name"] for call in _cosmology_likelihood_build_calls_from_prompt(prompt)] == [
        "build_cosmology_likelihood"
    ]
    assert [call["name"] for call in _cosmology_likelihood_run_calls_from_prompt(prompt)] == [
        "run_cosmology_likelihood_chain"
    ]


def test_s8_consistency_defaults_to_lcdm_only_without_dark_energy_request() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_likelihood_run_calls_from_prompt,
        _cosmology_models_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "我在做 galaxy weak-lensing surveys 的 S8 consistency 检验。请基于 "
        "KiDS-1000、DES Y3 3x2pt、HSC Y1 与 Planck compressed prior 的可用资料，"
        "只用本轮工具结果比较 S8 posterior。"
    )

    assert _is_cosmology_likelihood_workflow(prompt)
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "planck2018_compressed",
        "kids1000_wl",
        "des_y3_3x2pt",
        "hsc_y1_cosmic_shear",
    ]
    assert _cosmology_models_from_prompt(prompt) == ["lcdm"]
    assert _cosmology_likelihood_build_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "kids1000_wl",
        "des_y3_3x2pt",
        "hsc_y1_cosmic_shear",
    ]
    assert _cosmology_likelihood_run_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "kids1000_wl",
        "des_y3_3x2pt",
        "hsc_y1_cosmic_shear",
    ]


def test_english_kids_s8_tension_prompt_routes_deterministically() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_likelihood_run_calls_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "I am checking a galaxy weak-lensing S8 analysis inspired by KiDS-1000. "
        "Use registered weak-lensing and CMB compressed datasets to determine "
        "whether this turn can support an S8 tension claim; do not quote "
        "non-tool paper values."
    )

    assert _is_cosmology_likelihood_workflow(prompt)
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "planck2018_compressed",
        "kids1000_wl",
    ]
    assert _cosmology_likelihood_build_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "kids1000_wl",
    ]
    assert _cosmology_likelihood_run_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "kids1000_wl",
    ]


def test_expanded_weak_lensing_family_names_share_routing_vocabulary() -> None:
    from app.api.chat import _cosmology_dataset_keys_from_prompt

    prompt = (
        "Compare Kilo-Degree Survey Legacy DR5 cosmic shear with Dark Energy "
        "Survey Y6 weak lensing."
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "kids1000_wl",
        "des_y3_3x2pt",
    ]
    assert _cosmology_dataset_keys_from_prompt("Run DES Y6 weak lensing.") == [
        "des_y3_3x2pt"
    ]
    assert _cosmology_dataset_keys_from_prompt("Run DES cosmic shear.") == [
        "des_y3_3x2pt"
    ]


def test_english_hsc_cosmic_shear_constraint_prompt_routes_deterministically() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _cosmology_likelihood_run_calls_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "I am testing an HSC Y1 cosmic-shear comparison against CMB constraints. "
        "Use registered HSC, Planck, and any compatible executable compressed "
        "products; avoid broad unrelated datasets unless the prompt requires them."
    )

    assert _is_cosmology_likelihood_workflow(prompt)
    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "planck2018_compressed",
        "hsc_y1_cosmic_shear",
    ]
    assert _cosmology_likelihood_build_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "hsc_y1_cosmic_shear",
    ]
    assert _cosmology_likelihood_run_calls_from_prompt(prompt)[0]["input"]["dataset_keys"] == [
        "planck2018_compressed",
        "hsc_y1_cosmic_shear",
    ]


def test_pre_desi_bao_prompt_uses_sdss_not_desi() -> None:
    from app.api.chat import _cosmology_dataset_keys_from_prompt

    prompt = (
        "我在做 pre-DESI BAO 与 CMB 的一致性测试。请基于 "
        "SDSS/BOSS/eBOSS/6dF BAO compilation 和 Planck compressed prior，"
        "在 flat ΛCDM 下判断是否能得到 posterior。"
    )

    assert _cosmology_dataset_keys_from_prompt(prompt) == [
        "sdss_6df_bao",
        "planck2018_compressed",
    ]


def test_desi_bin_outlier_prompt_routes_to_cosmology_registry_not_python() -> None:
    from app.api.chat import (
        _cosmology_dataset_keys_from_prompt,
        _cosmology_likelihood_build_calls_from_prompt,
        _is_cosmology_likelihood_workflow,
    )

    prompt = (
        "我在做 DESI DR1 BAO 分红移 bin 的稳健性检查。请基于 DESI DR1 BAO "
        "可用资料判断是否能评估某个 redshift bin 的 pull/outlier；"
        "如果没有 bin-level residual 工具，请不要给异常显著性。"
    )

    assert _is_cosmology_likelihood_workflow(prompt)
    assert _cosmology_dataset_keys_from_prompt(prompt) == ["desi_dr1_bao"]
    assert _cosmology_likelihood_build_calls_from_prompt(prompt)[0]["name"] == "build_cosmology_likelihood"


def test_explicit_desi_dr2_prompt_routes_to_dr2_not_dr1() -> None:
    """Regression: a prompt explicitly requesting desi_dr2_bao was silently
    rerouted to desi_dr1_bao (verified live 2026-07-09; captured provenance
    showed datasets_used=["desi_dr1_bao"])."""
    from app.api.chat import _cosmology_dataset_keys_from_prompt

    # The exact failure channel: the registry key spelled out in the prompt.
    assert _cosmology_dataset_keys_from_prompt(
        "Build and run the executable likelihood with datasets desi_dr2_bao "
        "under flat LCDM and report the posterior."
    ) == ["desi_dr2_bao"]

    # Natural phrasing.
    assert _cosmology_dataset_keys_from_prompt(
        "Fit flat LCDM to the DESI DR2 BAO distance measurements with a "
        "Planck compressed prior; executable run please."
    ) == ["desi_dr2_bao", "planck2018_compressed"]


def test_bare_desi_prompt_still_routes_to_dr1() -> None:
    from app.api.chat import _cosmology_dataset_keys_from_prompt

    assert _cosmology_dataset_keys_from_prompt(
        "Run the executable DESI BAO likelihood under flat LCDM."
    ) == ["desi_dr1_bao"]


def test_desi_dr1_vs_dr2_prompt_never_selects_both() -> None:
    """desi_dr1_bao and desi_dr2_bao are mutually do_not_combine_with in the
    registry, so routing must pick exactly one; DR2 supersedes DR1 when both
    releases are named."""
    from app.api.chat import _cosmology_dataset_keys_from_prompt

    keys = _cosmology_dataset_keys_from_prompt(
        "Compare DESI DR1 vs DESI DR2 BAO constraints under flat LCDM, "
        "executable run."
    )
    assert "desi_dr2_bao" in keys
    assert "desi_dr1_bao" not in keys


def test_desi_dr2_respects_bao_family_exclusion() -> None:
    """desi_dr2_bao must be classified as the BAO probe family so an explicit
    'no BAO' prompt filters it like desi_dr1_bao."""
    from app.api.chat import _cosmology_dataset_keys_from_prompt

    assert _cosmology_dataset_keys_from_prompt(
        "Fit flat LCDM with the Planck compressed prior only; do not include "
        "BAO (desi_dr2_bao) in this run."
    ) == ["planck2018_compressed"]


def test_empty_cosmology_prose_fallback_summarizes_config_only_turn() -> None:
    from app.api.chat import _cosmology_tool_grounded_summary

    summary = _cosmology_tool_grounded_summary([
        {
            "tool": "list_cosmology_datasets",
            "result": {
                "datasets": [
                    {
                        "key": "desi_dr1_bao",
                        "display_name": "DESI DR1 BAO",
                        "data_products": [{"role": "measurement_vector"}],
                    }
                ],
            },
        },
        {
            "tool": "build_cosmology_likelihood",
            "result": {
                "model": "lcdm",
                "config_hash": "abcdef1234567890",
                "publication_ready": False,
            },
        },
        {
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "publication_ready": False,
                "__do_not_claim__": True,
                "warnings": ["No selected dataset has a registered compressed Gaussian likelihood."],
                "datasets_not_run": [{"key": "desi_dr1_bao", "display_name": "DESI DR1 BAO"}],
            },
        },
    ])

    assert summary is not None
    assert "DESI DR1 BAO" in summary
    assert "1 machine-readable product" in summary
    assert "config_hash=abcdef123456" in summary
    assert "not publication-ready" in summary
    assert "cannot support H0/Omega_m/S8/tension" in summary
    assert "language model did not return" not in summary


def test_cosmology_summary_discloses_requested_release_substitution() -> None:
    """An explicit unregistered release must never inherit the selected
    registry product's identity merely because both share a survey family."""
    from app.api.chat import _cosmology_tool_grounded_summary

    tool_results = [{
        "tool": "list_cosmology_datasets",
        "result": {"datasets": [{
            "key": "kids1000_wl",
            "display_name": "KiDS-1000 cosmic shear",
            "version": "KiDS-1000 cosmic-shear likelihood / 2-point statistics",
        }]},
    }]
    summary = _cosmology_tool_grounded_summary(
        tool_results,
        "Run KiDS-Legacy DR5 cosmic shear and state which release was run.",
    )

    assert summary is not None
    assert "KiDS-1000 cosmic shear" in summary
    assert "DR5" in summary and "Legacy" in summary
    assert "not registered" in summary
    assert "selected registered product(s) instead" in summary
    assert "not to the requested release" in summary


def test_cosmology_summary_release_disclosure_is_generic_and_not_spurious() -> None:
    from app.api.chat import _cosmology_tool_grounded_summary

    desi_results = [{
        "tool": "list_cosmology_datasets",
        "result": {"datasets": [{
            "key": "desi_dr1_bao",
            "display_name": "DESI DR1 BAO",
            "version": "DESI 2024 Data Release 1",
        }]},
    }]
    mismatch = _cosmology_tool_grounded_summary(
        desi_results, "Run the DESI DR3 BAO registered likelihood."
    )
    assert mismatch is not None
    assert "Dataset substitution disclosure" in mismatch
    assert "DR3" in mismatch and "DESI DR1 BAO" in mismatch

    exact = _cosmology_tool_grounded_summary(
        [{
            "tool": "list_cosmology_datasets",
            "result": {"datasets": [{
                "key": "kids1000_wl",
                "display_name": "KiDS-1000 cosmic shear",
                "version": "KiDS-1000 cosmic-shear likelihood",
            }]},
        }],
        "Run KiDS-1000 cosmic shear.",
    )
    assert exact is not None
    assert "Dataset substitution disclosure" not in exact

    nearby_scientific_numbers = _cosmology_tool_grounded_summary(
        [{
            "tool": "list_cosmology_datasets",
            "result": {"datasets": [{
                "key": "kids1000_wl",
                "display_name": "KiDS-1000 cosmic shear",
                "version": "KiDS-1000 cosmic-shear likelihood",
            }]},
        }],
        "Run KiDS cosmic shear with 1500 samples following the 2024 methods paper.",
    )
    assert nearby_scientific_numbers is not None
    assert "Dataset substitution disclosure" not in nearby_scientific_numbers

    pantheon_plus = _cosmology_tool_grounded_summary(
        [{
            "tool": "list_cosmology_datasets",
            "result": {"datasets": [{
                "key": "pantheon_plus",
                "display_name": "Pantheon+ supernovae",
                "version": "Pantheon Plus release",
            }]},
        }],
        "Run Pantheon+ with the registered supernova likelihood.",
    )
    assert pantheon_plus is not None
    assert "Dataset substitution disclosure" not in pantheon_plus

    pantheon_plus_sh0es_mismatch = _cosmology_tool_grounded_summary(
        [{
            "tool": "list_cosmology_datasets",
            "result": {"datasets": [{
                "key": "pantheon18",
                "display_name": "Pantheon 2018 supernovae",
                "version": "2018 release",
            }]},
        }],
        "Use Pantheon+SH0ES for the supernova branch.",
    )
    assert pantheon_plus_sh0es_mismatch is not None
    assert "Dataset substitution disclosure" in pantheon_plus_sh0es_mismatch
    assert "Plus" in pantheon_plus_sh0es_mismatch

    with_release_marker = _cosmology_tool_grounded_summary(
        desi_results, "Run DESI BAO with DR3 registered likelihood."
    )
    assert with_release_marker is not None
    assert "Dataset substitution disclosure" in with_release_marker
    assert "DR3" in with_release_marker

    registered_but_not_run = _cosmology_tool_grounded_summary(
        [{
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "datasets_used": [{
                    "key": "desi_dr1_bao",
                    "display_name": "DESI DR1 BAO",
                    "version": "Data Release 1",
                }],
                "datasets_not_run": [{
                    "key": "desi_dr3_bao",
                    "display_name": "DESI DR3 BAO",
                    "version": "Data Release 3",
                }],
            },
        }],
        "Run DESI DR3 BAO.",
    )
    assert registered_but_not_run is not None
    assert "Dataset substitution disclosure" in registered_but_not_run
    assert "listed in `datasets_not_run` but was not executed" in registered_but_not_run
    assert "not registered" not in registered_but_not_run

    expanded_kids = _cosmology_tool_grounded_summary(
        [{
            "tool": "list_cosmology_datasets",
            "result": {"datasets": [{
                "key": "kids1000_wl",
                "display_name": "KiDS-1000 cosmic shear",
                "version": "KiDS-1000 cosmic-shear likelihood",
            }]},
        }],
        "Run the Kilo-Degree Survey Legacy DR5 likelihood.",
    )
    assert expanded_kids is not None
    assert "Dataset substitution disclosure" in expanded_kids
    assert "Legacy" in expanded_kids and "DR5" in expanded_kids

    expanded_des = _cosmology_tool_grounded_summary(
        [{
            "tool": "list_cosmology_datasets",
            "result": {"datasets": [{
                "key": "des_y3_3x2pt",
                "display_name": "DES Y3 3x2pt",
                "version": "DES Year 3",
            }]},
        }],
        "Run the Dark Energy Survey Y6 likelihood.",
    )
    assert expanded_des is not None
    assert "Dataset substitution disclosure" in expanded_des
    assert "Y6" in expanded_des

    infix_union = _cosmology_tool_grounded_summary(
        [{
            "tool": "list_cosmology_datasets",
            "result": {"datasets": [
                {
                    "key": "desi_dr1_bao",
                    "display_name": "DESI DR1 BAO",
                    "version": "Data Release 1",
                },
                {
                    "key": "planck2018_compressed",
                    "display_name": "Planck 2018 compressed CMB",
                    "version": "Planck 2018",
                },
            ]},
        }],
        "Run DESI+Planck.",
    )
    assert infix_union is not None
    assert "Dataset substitution disclosure" not in infix_union

    word_union = _cosmology_tool_grounded_summary(
        desi_results,
        "Run DESI plus Planck, using the legacy pipeline only for file conversion.",
    )
    assert word_union is not None
    assert "Dataset substitution disclosure" not in word_union

    used_is_authoritative = _cosmology_tool_grounded_summary(
        [
            {
                "tool": "list_cosmology_datasets",
                "result": {"datasets": [
                    {
                        "key": "desi_dr1_bao",
                        "display_name": "DESI DR1 BAO",
                        "version": "Data Release 1",
                    },
                    {
                        "key": "desi_dr2_bao",
                        "display_name": "DESI DR2 BAO",
                        "version": "Data Release 2",
                    },
                ]},
            },
            {
                "tool": "run_cosmology_likelihood_chain",
                "result": {"datasets_used": [{
                    "key": "desi_dr1_bao",
                    "display_name": "DESI DR1 BAO",
                    "version": "Data Release 1",
                }]},
            },
        ],
        "Run DESI DR2 BAO.",
    )
    assert used_is_authoritative is not None
    assert "Dataset substitution disclosure" in used_is_authoritative
    assert "registry selection but was not executed" in used_is_authoritative
    assert "DESI DR1 BAO" in used_is_authoritative

    same_family_multi_select = _cosmology_tool_grounded_summary(
        [{
            "tool": "list_cosmology_datasets",
            "result": {"datasets": [
                {
                    "key": "des_y3_3x2pt",
                    "display_name": "DES Y3 3x2pt weak lensing + clustering",
                    "version": "DES Year 3",
                },
                {
                    "key": "des_sn5yr",
                    "display_name": "DES-SN 5YR",
                    "version": "DES five-year supernova release",
                },
            ]},
        }],
        "Compare the selected DES Y3 product with the supernova branch.",
    )
    assert same_family_multi_select is not None
    assert "Dataset substitution disclosure" not in same_family_multi_select


def test_cosmology_summary_appends_out_of_coverage_caveat_for_beyond_z_request() -> None:
    """C2 anti-fabrication: a 'report X at z=N' prompt where N exceeds every
    included dataset's z_coverage must deterministically append an extrapolation
    caveat — independent of the model's wording — referencing only the sourced
    coverage bound (never echoing the requested z as a measured value)."""
    from app.api.chat import _cosmology_tool_grounded_summary

    tool_results = [
        {
            "tool": "list_cosmology_datasets",
            "result": {"datasets": [
                {"key": "pantheon_plus", "display_name": "Pantheon+",
                 "z_coverage": [0.001, 2.26], "data_products": [{"role": "mu_vector"}]},
            ]},
        },
        {
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "publication_ready": True,
                "datasets_used": [{"key": "pantheon_plus", "display_name": "Pantheon+", "z_coverage": [0.001, 2.26]}],
                "parameters": {"omegam": {"median": 0.334, "hdi_94": [0.30, 0.367]}},
            },
        },
    ]
    summary = _cosmology_tool_grounded_summary(tool_results, "Use Pantheon+ to report Omega_m at z = 12.")
    assert summary is not None
    # Hits the C2 hard-check vocabulary deterministically.
    assert "extrapolat" in summary
    assert "(1+z)" in summary
    assert "model-dependent" in summary
    assert "not a measurement" in summary
    assert "z ≤ 2.26" in summary
    # Must NOT echo the requested z as if it were a measured/claimable value.
    assert "z = 12" not in summary and "z=12" not in summary

    # A request that stays within coverage gets NO out-of-coverage caveat.
    in_range = _cosmology_tool_grounded_summary(tool_results, "Report Omega_m from Pantheon+.")
    assert in_range is not None and "Out-of-coverage" not in in_range


def test_empty_cosmology_prose_fallback_can_report_publication_ready_compressed_chain() -> None:
    from app.api.chat import _cosmology_tool_grounded_summary

    summary = _cosmology_tool_grounded_summary([
        {
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "publication_ready": True,
                "datasets_used": [{"key": "planck2018_compressed", "display_name": "Planck 2018 compressed distance priors"}],
                "datasets_not_run": [{"key": "desi_dr1_bao", "display_name": "DESI DR1 BAO"}],
                "parameters": {
                    "H0": {"median": 67.36, "hdi_94": [66.3, 68.4]},
                    "S8": {"median": 0.804, "hdi_94": [0.787, 0.821]},
                },
            },
        }
    ])

    assert summary is not None
    assert "publication-ready" in summary
    assert "compressed-likelihood preliminary" in summary
    assert "H0=67.36" in summary
    assert "S8=0.804" in summary
    assert "DESI DR1 BAO" in summary


def test_cosmology_grounded_summary_keeps_single_h0_prior_comparison_clean() -> None:
    from app.api.chat import (
        _cosmology_tool_grounded_summary,
        _unsupported_cosmology_anchor_numeric_comparison,
    )
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [
        {
            "tool": "list_cosmology_datasets",
            "result": {
                "datasets": [
                    {
                        "key": "trgb_h0_freedman19",
                        "display_name": "TRGB H0 prior (Freedman+ 2019)",
                        "data_products": [],
                    }
                ],
            },
        },
        {
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "publication_ready": True,
                "datasets_used": [
                    {
                        "key": "trgb_h0_freedman19",
                        "display_name": "TRGB H0 prior (Freedman+ 2019)",
                    }
                ],
                "datasets_not_run": [],
                "parameters": {
                    "H0": {"median": 69.7853, "hdi_94": [66.3505, 73.3545]},
                },
                "provenance": {
                    "cosmology_likelihood": {
                        "publication_ready": True,
                        "citations": [
                            {
                                "label": "Freedman et al. TRGB H0 (CCHP)",
                                "year": 2019,
                                "arxiv": "1907.05922",
                                "doi": "10.3847/1538-4357/ab2f73",
                            }
                        ],
                    }
                },
            },
        },
    ]

    summary = _cosmology_tool_grounded_summary(tool_results)

    assert summary is not None
    assert "H0=69.79" in summary
    assert "SH0ES" not in summary
    assert "Planck" not in summary
    assert provenance_citation_violations(summary, tool_results) == []
    assert _unsupported_cosmology_anchor_numeric_comparison(summary, tool_results) is False

    unsafe_comparison = (
        "Planck CMB (2018): H0 = 67.36 km/s/Mpc. "
        "SH0ES Cepheid ladder: H0 = 73.04 km/s/Mpc. "
        "TRGB: H0 = 69.83 km/s/Mpc."
    )
    assert _unsupported_cosmology_anchor_numeric_comparison(unsafe_comparison, tool_results) is True


def test_timeout_fallback_recovers_research_tool_results_from_stream_audit() -> None:
    from app.api.chat import (
        _tool_grounded_timeout_summary,
        _tool_results_from_stream_audit,
    )

    audit_trail = [
        {"type": "status", "message": "running"},
        {
            "type": "tool_result",
            "tool": "plan_research_program",
            "tool_call_id": "plan1",
            "result": {
                "research_plan": {
                    "required_probes": ["BAO", "CMB"],
                    "model_families": ["lcdm"],
                    "blocking_gaps": ["Pantheon+ requires external Cobaya/CosmoSIS."],
                }
            },
        },
        {
            "type": "tool_result",
            "tool": "run_research_matrix",
            "tool_call_id": "matrix1",
            "result": {
                "ready_cells": 1,
                "matrix_size": 2,
                "matrix": [
                    {
                        "label": "BAO + CMB",
                        "publication_ready": True,
                        "result": {
                            "parameters": {
                                "H0": {"median": 67.31},
                                "omegam": {"median": 0.3116},
                            },
                            "chain_diagnostics": {"proposal_ess": 471.3, "rhat": 1.0},
                        },
                    },
                    {"label": "BAO + SN", "publication_ready": False},
                ],
            },
        },
        {
            "type": "tool_result",
            "tool": "build_evidence_graph",
            "tool_call_id": "graph1",
            "result": {
                "evidence_graph": {
                    "claimable_parameters": ["H0", "omegam"],
                }
            },
        },
    ]

    recovered = _tool_results_from_stream_audit(audit_trail)
    assert [item["tool"] for item in recovered] == [
        "plan_research_program",
        "run_research_matrix",
        "build_evidence_graph",
    ]

    summary = _tool_grounded_timeout_summary(recovered, 420)
    assert "time budget" in summary
    assert "tool-grounded partial summary" in summary
    assert "BAO + CMB" in summary
    assert "H0 median 67.31" in summary
    assert "`Omega_m` median 0.3116" in summary
    assert "Pantheon+ requires external Cobaya/CosmoSIS" in summary


def test_research_summary_separates_executed_not_ready_from_config_only() -> None:
    from app.api.chat import _research_tool_grounded_summary

    tool_results = [
        {
            "tool": "plan_research_program",
            "result": {
                "research_plan": {
                    "required_probes": ["BAO", "CMB", "WL"],
                    "model_families": ["lcdm"],
                }
            },
        },
        {
            "tool": "run_research_matrix",
            "result": {
                "ready_cells": 1,
                "matrix_size": 3,
                "matrix": [
                    {
                        "label": "BAO + CMB",
                        "publication_ready": True,
                        "execution_level": "compressed_preliminary",
                        "result": {
                            "parameters": {
                                "H0": {"median": 67.28},
                                "omegam": {"median": 0.3117},
                                "S8": {"median": 0.8317},
                            },
                            "chain_diagnostics": {"proposal_ess": 507.5, "rhat": 1.0},
                        },
                    },
                    {
                        "label": "BAO + WL",
                        "publication_ready": False,
                        "execution_level": "executed_not_ready",
                        "result": {
                            "parameters": {"S8": {"median": 0.7796}},
                            "chain_diagnostics": {
                                "proposal_ess": 105.0,
                                "rhat": 1.0,
                                "thresholds": {"ess_min": 400},
                            },
                        },
                    },
                    {
                        "label": "BAO + SN",
                        "publication_ready": False,
                        "execution_level": "config_only",
                        "warnings": ["Pantheon+ requires external Cobaya/CosmoSIS."],
                    },
                    {
                        "label": "BAO only",
                        "publication_ready": False,
                        "execution_level": "executed_not_ready",
                        "result": {
                            "chain_diagnostics": {
                                "proposal_ess": 1310.0,
                                "thresholds": {"ess_min": 400},
                            },
                            "publication_gate": {
                                "reasons": [
                                    "compressed_or_approximate_likelihood",
                                    "fewer_than_four_independent_chains",
                                ]
                            },
                        },
                    },
                ],
            },
        },
    ]

    summary = _research_tool_grounded_summary(tool_results)

    assert summary is not None
    assert "BAO + CMB · H0 median 67.28 · `Omega_m` median 0.3117 · S8 median 0.8317" in summary
    assert "Executed but not claimable" in summary
    assert "BAO + WL · ESS 105 below threshold 400" in summary
    assert "BAO only · ESS 1310 meets threshold 400" in summary
    assert "BAO only · ESS 1310 below threshold 400" not in summary
    assert "`compressed_or_approximate_likelihood`" in summary
    assert "`fewer_than_four_independent_chains`" in summary
    assert "Config-only or not-runnable branches" in summary
    assert "BAO + SN · Pantheon+ requires external Cobaya/CosmoSIS." in summary
    assert "BAO + WL" not in summary.split("Config-only or not-runnable branches:", 1)[-1]


def test_research_summary_formats_config_only_dataset_dicts_for_users() -> None:
    from app.api.chat import _research_tool_grounded_summary

    tool_results = [
        {
            "tool": "plan_research_program",
            "result": {
                "research_plan": {
                    "required_probes": ["CMB_POLARIZATION_ROTATION"],
                    "model_families": ["isotropic_beta"],
                    "blocking_gaps": [
                        "Planck PR4/NPIPE EB/TB polarization-rotation products requires an EB/TB likelihood.",
                    ],
                }
            },
        },
        {
            "tool": "run_research_matrix",
            "result": {
                "ready_cells": 0,
                "matrix_size": 1,
                "matrix": [
                    {
                        "label": "CMB rotation - isotropic beta",
                        "publication_ready": False,
                        "execution_level": "config_only",
                        "result": {
                            "datasets_not_run": [
                                {
                                    "key": "planck_pr4_ebtb_rotation",
                                    "display_name": "Planck PR4/NPIPE EB/TB polarization-rotation products",
                                    "execution_mode": "config_only",
                                },
                                {
                                    "key": "act_dr6_ebtb_rotation",
                                    "display_name": "ACT DR6 EB/TB polarization-rotation products",
                                    "execution_mode": "config_only",
                                },
                            ]
                        },
                    }
                ],
            },
        },
    ]

    summary = _research_tool_grounded_summary(tool_results)

    assert summary is not None
    assert "No publication-ready compressed-likelihood baseline completed this turn." in summary
    assert "Planck PR4/NPIPE EB/TB polarization-rotation products (planck_pr4_ebtb_rotation)" in summary
    assert "ACT DR6 EB/TB polarization-rotation products (act_dr6_ebtb_rotation)" in summary
    assert "{'key':" not in summary
