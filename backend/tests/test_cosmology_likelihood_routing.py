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


def test_dataset_identity_guard_replaces_model_release_equivalence_claim() -> None:
    from app.api import chat as chat_module
    from app.services.claim_validator import validate_claims

    for identity_question in (
        "Under LCDM, is Planck the same dataset as KiDS-1000?",
        "Under LCDM, are Planck and KiDS-1000 different datasets?",
        "Are Planck and KiDS-1000 equivalent products?",
        "Are these the same releases: Planck and KiDS-1000?",
        "Is Planck different from KiDS-1000 under LCDM?",
        "Is Planck the same as KiDS-1000 under LCDM?",
        "Under LCDM, is Planck, in this registry, the same dataset as KiDS-1000?",
    ):
        assert chat_module._is_cosmology_likelihood_workflow(
            identity_question
        ) is False
        assert chat_module._cosmology_dataset_keys_from_prompt(
            identity_question
        ) == []
        assert chat_module._cosmology_likelihood_build_calls_from_prompt(
            identity_question
        ) == []

    assert chat_module._cosmology_dataset_keys_from_prompt(
        "Run the executable cosmology likelihood chain with KiDS-1000, "
        "not DES Y3, under LCDM."
    ) == ["kids1000_wl"]
    assert chat_module._cosmology_dataset_keys_from_prompt(
        "Run the executable cosmology likelihood chain with KiDS-1000, "
        "not Planck, under LCDM."
    ) == ["kids1000_wl"]
    assert chat_module._cosmology_dataset_keys_from_prompt(
        "Run the executable cosmology likelihood chain with Planck, "
        "not Pantheon+, under LCDM."
    ) == ["planck2018_compressed"]
    assert chat_module._cosmology_dataset_keys_from_prompt(
        "Run the executable cosmology likelihood chain with Planck, "
        "not DES Y3, under LCDM."
    ) == ["planck2018_compressed"]
    assert chat_module._cosmology_dataset_keys_from_prompt(
        "Evaluate whether S8 differs from GR using Planck and KiDS-1000 "
        "under LCDM."
    ) == ["planck2018_compressed", "kids1000_wl"]
    assert chat_module._cosmology_dataset_keys_from_prompt(
        "Run KiDS-1000 without using Planck under LCDM."
    ) == ["kids1000_wl"]
    for exclusion_prompt in (
        "Run KiDS-1000, not using Planck under LCDM.",
        "Run KiDS-1000, never using Planck under LCDM.",
        "Run KiDS-1000; do not be using Planck under LCDM.",
        "Run KiDS-1000, do not use Planck under LCDM.",
        "Run KiDS-1000 and do not use Planck under LCDM.",
    ):
        assert chat_module._cosmology_dataset_keys_from_prompt(
            exclusion_prompt
        ) == ["kids1000_wl"]

    glossary_prompt = (
        "We are using a glossary to discuss whether Planck differs from "
        "KiDS-1000 under LCDM."
    )
    assert chat_module._cosmology_dataset_keys_from_prompt(glossary_prompt) == []
    assert chat_module._cosmology_likelihood_build_calls_from_prompt(
        glossary_prompt
    ) == []

    tool_results = [{
        "tool": "list_cosmology_datasets",
        "result": {"datasets": [{
            "key": "kids1000_wl",
            "display_name": "KiDS-1000 cosmic shear",
            "version": "KiDS-1000 cosmic-shear likelihood",
        }]},
    }]
    contradicted_draft = (
        "KiDS-Legacy DR5 and KiDS-1000 are two names for the same dataset."
    )

    guarded, enforced = chat_module._enforce_cosmology_dataset_identity(
        contradicted_draft,
        tool_results,
        "Run KiDS-Legacy DR5 cosmic shear.",
    )

    assert enforced is True
    assert "same dataset" not in guarded
    assert "Dataset substitution disclosure" in guarded
    assert "not registered" in guarded
    assert "KiDS-1000 cosmic shear" in guarded
    assert "not to the requested release" in guarded
    assert validate_claims(guarded, tool_results).ok is True

    unchanged, exact_enforced = chat_module._enforce_cosmology_dataset_identity(
        "The selected release is KiDS-1000.",
        tool_results,
        "Run KiDS-1000 cosmic shear.",
    )
    assert exact_enforced is False
    assert unchanged == "The selected release is KiDS-1000."

    for comparison_prompt in (
        "Do not run KiDS-Legacy DR5; run KiDS-1000 cosmic shear.",
        "Use KiDS-1000, not KiDS-Legacy DR5.",
        (
            "Run KiDS-1000, and explain why KiDS-Legacy DR5 is not "
            "the same release."
        ),
        "Run KiDS-1000 and explain why KiDS-Legacy DR5 differs.",
        "Compare KiDS-1000 with KiDS-Legacy DR5.",
        "Is KiDS-Legacy DR5 the same release as KiDS-1000?",
        "Use KiDS-1000 (not KiDS-Legacy DR5).",
    ):
        comparison_reply = "The selected release is KiDS-1000."
        unchanged, comparison_enforced = (
            chat_module._enforce_cosmology_dataset_identity(
                comparison_reply,
                tool_results,
                comparison_prompt,
            )
        )
        assert comparison_enforced is False, comparison_prompt
        assert unchanged == comparison_reply

    for mixed_intent_prompt in (
        "Run and explain KiDS-Legacy DR5 cosmic shear.",
        "Analyze and discuss KiDS-Legacy DR5 cosmic shear.",
        (
            "Explain KiDS-Legacy DR5 and KiDS-1000, then run both "
            "cosmic-shear releases."
        ),
        "Compare KiDS-Legacy DR5 vs KiDS-1000 by running both.",
    ):
        guarded, mixed_intent_enforced = (
            chat_module._enforce_cosmology_dataset_identity(
                contradicted_draft,
                tool_results,
                mixed_intent_prompt,
            )
        )
        assert mixed_intent_enforced is True, mixed_intent_prompt
        assert "same dataset" not in guarded
        assert "not registered" in guarded


def test_dataset_intent_scope_separates_identity_execution_and_exclusion() -> None:
    from app.api import chat as chat_module

    for parameter_prompt in (
        "Evaluate whether S8 differs between Planck and KiDS-1000 under LCDM.",
        "Test whether Omega_m differs between Planck and KiDS-1000 under LCDM.",
    ):
        assert chat_module._is_cosmology_likelihood_workflow(parameter_prompt)
        assert chat_module._cosmology_dataset_keys_from_prompt(
            parameter_prompt
        ) == ["planck2018_compressed", "kids1000_wl"]
        assert chat_module._cosmology_likelihood_build_calls_from_prompt(
            parameter_prompt
        )[0]["input"]["dataset_keys"] == [
            "planck2018_compressed",
            "kids1000_wl",
        ]

    for identity_prompt in (
        "Compare whether Planck and KiDS-1000 are different datasets under LCDM.",
        "Evaluate whether Planck and KiDS-1000 datasets differ under LCDM.",
    ):
        assert not chat_module._is_cosmology_likelihood_workflow(identity_prompt)
        assert chat_module._cosmology_dataset_keys_from_prompt(identity_prompt) == []
        assert chat_module._cosmology_likelihood_build_calls_from_prompt(
            identity_prompt
        ) == []

    for explanation_prompt in (
        "Use a glossary to explain Planck constraints under LCDM.",
        "Use documentation to discuss Planck constraints under LCDM.",
        "For documentation only, describe Planck constraints under LCDM.",
    ):
        assert chat_module._cosmology_dataset_keys_from_prompt(
            explanation_prompt
        ) == []
        assert chat_module._cosmology_likelihood_build_calls_from_prompt(
            explanation_prompt
        ) == []

    for metadata_prompt in (
        "What release is Planck 2018 under LCDM?",
        "Which versions of Planck and KiDS are registered under LCDM?",
        "List metadata for Planck and KiDS under LCDM.",
        "What is the difference between Planck and KiDS datasets under LCDM?",
        "Planck 在 LCDM 下是否可用？",
    ):
        assert not chat_module._is_cosmology_likelihood_workflow(metadata_prompt)
        assert chat_module._cosmology_dataset_keys_from_prompt(metadata_prompt) == []
        assert chat_module._cosmology_likelihood_build_calls_from_prompt(
            metadata_prompt
        ) == []

    for metadata_plus_execution in (
        "Use Planck if available under LCDM.",
        "Run Planck and list its metadata under LCDM.",
    ):
        assert chat_module._is_cosmology_likelihood_workflow(
            metadata_plus_execution
        )
        assert chat_module._cosmology_dataset_keys_from_prompt(
            metadata_plus_execution
        ) == ["planck2018_compressed"]

    for prompt, expected in (
        (
            "Run and briefly explain Planck under LCDM.",
            ["planck2018_compressed"],
        ),
        (
            "Analyze and carefully discuss KiDS-1000 under LCDM.",
            ["kids1000_wl"],
        ),
        (
            "KiDS-1000 should not be used; run Planck under LCDM.",
            ["planck2018_compressed"],
        ),
        (
            "Planck 2018 should not be used; run KiDS-1000 under LCDM.",
            ["kids1000_wl"],
        ),
        (
            "Run KiDS-1000 under LCDM; Planck should not be included.",
            ["kids1000_wl"],
        ),
        (
            "Run KiDS-1000 under LCDM; Planck should be excluded.",
            ["kids1000_wl"],
        ),
        (
            "Do not use Planck and run KiDS-1000 under LCDM.",
            ["kids1000_wl"],
        ),
        (
            "Do not use KiDS-1000 and run Planck under LCDM.",
            ["planck2018_compressed"],
        ),
        (
            "Run KiDS-1000 except Planck under LCDM.",
            ["kids1000_wl"],
        ),
        (
            "Run all registered weak-lensing datasets except DES Y3 under LCDM.",
            ["kids1000_wl", "hsc_y1_cosmic_shear"],
        ),
        (
            "Run KiDS-1000 without using Planck and include Pantheon+ under LCDM.",
            ["pantheon_plus", "kids1000_wl"],
        ),
    ):
        assert chat_module._cosmology_dataset_keys_from_prompt(prompt) == expected
        assert chat_module._cosmology_likelihood_build_calls_from_prompt(
            prompt
        )[0]["input"]["dataset_keys"] == expected


def test_dataset_identity_is_the_request_target_not_an_analysis_qualifier() -> None:
    from app.api import chat as chat_module

    for execution_prompt in (
        "Run Planck and KiDS-1000 as different datasets under LCDM.",
        "Use different datasets: Planck and KiDS-1000 under LCDM.",
        "Fit LCDM to two different datasets: Planck and KiDS-1000.",
        (
            "Compare S8 constraints from Planck and KiDS-1000; these are "
            "different datasets under LCDM."
        ),
        (
            "Are Planck and KiDS-1000 different datasets? Then run both "
            "under LCDM."
        ),
    ):
        assert chat_module._is_cosmology_likelihood_workflow(execution_prompt)
        assert chat_module._cosmology_dataset_keys_from_prompt(
            execution_prompt
        ) == ["planck2018_compressed", "kids1000_wl"]

    for identity_prompt in (
        "Under LCDM, are Planck and KiDS-1000 identical datasets?",
        "Under LCDM, are Planck and KiDS-1000 distinct releases?",
        "Do Planck and KiDS-1000 refer to the same data product under LCDM?",
        "Is Planck just another name for KiDS-1000 under LCDM?",
        "在 LCDM 下，Planck 和 KiDS-1000 是同一个数据集吗？",
    ):
        assert not chat_module._is_cosmology_likelihood_workflow(identity_prompt)
        assert chat_module._cosmology_dataset_keys_from_prompt(identity_prompt) == []
        assert chat_module._cosmology_likelihood_build_calls_from_prompt(
            identity_prompt
        ) == []

    for parameter_prompt in (
        "Do Planck and KiDS-1000 differ in S8 under LCDM?",
        "Do Planck and KiDS-1000 differ in their Omega_m constraints under LCDM?",
        "Are Planck and KiDS-1000 different in S8 under LCDM?",
    ):
        assert chat_module._is_cosmology_likelihood_workflow(parameter_prompt)
        assert chat_module._cosmology_dataset_keys_from_prompt(
            parameter_prompt
        ) == ["planck2018_compressed", "kids1000_wl"]


def test_named_exclusions_are_release_scoped_and_last_intent_wins() -> None:
    from app.api import chat as chat_module

    for prompt, expected in (
        (
            "Run DESI DR2 BAO except DESI DR1 under LCDM.",
            ["desi_dr2_bao"],
        ),
        (
            "Do not use DESI DR1; run DESI DR2 under LCDM.",
            ["desi_dr2_bao"],
        ),
        (
            "Run DESI DR1 but not DESI DR2 under LCDM.",
            ["desi_dr1_bao"],
        ),
        (
            "Run KiDS-1000, do not use DES Y3 under LCDM.",
            ["kids1000_wl"],
        ),
        (
            "Run Pantheon+ but do not use Union3 under LCDM.",
            ["pantheon_plus"],
        ),
        (
            "Do not use Planck initially; then run Planck under LCDM.",
            ["planck2018_compressed"],
        ),
        (
            "Without Planck initially, later use Planck under LCDM.",
            ["planck2018_compressed"],
        ),
        (
            "Exclude KiDS-1000 first, but then include KiDS-1000 under LCDM.",
            ["kids1000_wl"],
        ),
        (
            "Do not use Planck but instead run KiDS-1000 under LCDM.",
            ["kids1000_wl"],
        ),
        (
            "Do not use Planck, but please run KiDS-1000 under LCDM.",
            ["kids1000_wl"],
        ),
        (
            "Do not use Planck and subsequently run KiDS-1000 under LCDM.",
            ["kids1000_wl"],
        ),
        (
            "Do not use Planck with KiDS-1000 and run KiDS-1000 under LCDM.",
            ["kids1000_wl"],
        ),
        (
            "Exclude Planck from the KiDS-1000 fit; run KiDS-1000 under LCDM.",
            ["kids1000_wl"],
        ),
    ):
        assert chat_module._cosmology_dataset_keys_from_prompt(prompt) == expected


def test_dataset_combination_structure_and_multilingual_negation_are_preserved() -> None:
    from app.api import chat as chat_module

    for prompt, expected_groups in (
        (
            "Run Planck with and without KiDS-1000 under LCDM.",
            [
                ["planck2018_compressed"],
                ["planck2018_compressed", "kids1000_wl"],
            ],
        ),
        (
            "Run Planck and KiDS-1000 separately under LCDM.",
            [["planck2018_compressed"], ["kids1000_wl"]],
        ),
        (
            "Compare Planck and KiDS-1000 without combining them under LCDM.",
            [["planck2018_compressed"], ["kids1000_wl"]],
        ),
        (
            "Run KiDS-1000; Planck must not be combined under LCDM.",
            [["planck2018_compressed"], ["kids1000_wl"]],
        ),
    ):
        build_groups = [
            call["input"]["dataset_keys"]
            for call in chat_module._cosmology_likelihood_build_calls_from_prompt(
                prompt
            )
        ]
        run_groups = [
            call["input"]["dataset_keys"]
            for call in chat_module._cosmology_likelihood_run_calls_from_prompt(
                prompt
            )
        ]
        assert build_groups == expected_groups
        assert run_groups == expected_groups

    for prompt, expected in (
        (
            "Run KiDS-1000 without using Planck and add Pantheon+ under LCDM.",
            ["pantheon_plus", "kids1000_wl"],
        ),
        (
            "Run KiDS-1000 without using Planck and combine Pantheon+ under LCDM.",
            ["pantheon_plus", "kids1000_wl"],
        ),
        (
            "运行 KiDS-1000，不运行 Planck，在 LCDM 下。",
            ["kids1000_wl"],
        ),
        (
            "运行 KiDS-1000；Planck 不应被使用，在 LCDM 下。",
            ["kids1000_wl"],
        ),
    ):
        assert chat_module._cosmology_dataset_keys_from_prompt(prompt) == expected


def test_identity_context_and_method_advice_do_not_start_likelihoods() -> None:
    from app.api import chat as chat_module

    for identity_prompt in (
        "In this analysis, are Planck and KiDS-1000 the same dataset under LCDM?",
        "Are Planck and KiDS-1000 versions of the same dataset under LCDM?",
        "Are Planck and KiDS-1000 based on the same data product under LCDM?",
        "Are Planck and KiDS-1000 two versions of one dataset under LCDM?",
        (
            "Use a glossary to determine whether Planck and KiDS-1000 are "
            "the same dataset under LCDM."
        ),
        (
            "Use documentation to verify whether Planck and KiDS-1000 use "
            "the same data under LCDM."
        ),
    ):
        assert not chat_module._is_cosmology_likelihood_workflow(identity_prompt)
        assert chat_module._cosmology_dataset_keys_from_prompt(identity_prompt) == []
        assert chat_module._cosmology_likelihood_build_calls_from_prompt(
            identity_prompt
        ) == []

    for advice_prompt in (
        "Should Planck and KiDS-1000 be combined under LCDM?",
        "Is it okay to combine Planck and KiDS under LCDM?",
        "May I combine Planck and KiDS under LCDM?",
        "Should I use Planck and KiDS jointly under LCDM?",
        "Are Planck and KiDS safe to combine under LCDM?",
        "Would a joint Planck and KiDS fit double-count data under LCDM?",
        (
            "Would it be scientifically valid to combine Planck and "
            "KiDS-1000 under LCDM?"
        ),
        (
            "Discuss whether using Planck with KiDS-1000 under LCDM is "
            "appropriate."
        ),
    ):
        assert not chat_module._is_cosmology_likelihood_workflow(advice_prompt)
        assert chat_module._cosmology_dataset_keys_from_prompt(advice_prompt) == []

    explicit_execution = (
        "I decided we should combine Planck and KiDS; please run under LCDM."
    )
    assert chat_module._is_cosmology_likelihood_workflow(explicit_execution)
    assert chat_module._cosmology_dataset_keys_from_prompt(explicit_execution) == [
        "planck2018_compressed",
        "kids1000_wl",
    ]

    for parameter_prompt in (
        "Do Planck and KiDS-1000 agree on H0 under LCDM?",
        "Do Planck and KiDS-1000 disagree on S8 under LCDM?",
        "Are Planck and KiDS-1000 the same in S8 under LCDM?",
    ):
        assert chat_module._is_cosmology_likelihood_workflow(parameter_prompt)
        assert chat_module._cosmology_dataset_keys_from_prompt(
            parameter_prompt
        ) == ["planck2018_compressed", "kids1000_wl"]

    for chinese_parameter_prompt in (
        "Planck 和 KiDS 的 S8 是否不同？在 LCDM 下。",
        "Planck 和 KiDS 的 H0 是否不同？在 LCDM 下。",
        "Planck 和 KiDS 在 σ8 上是否相同？在 LCDM 下。",
    ):
        assert chat_module._is_cosmology_likelihood_workflow(
            chinese_parameter_prompt
        )
        assert chat_module._cosmology_dataset_keys_from_prompt(
            chinese_parameter_prompt
        ) == ["planck2018_compressed", "kids1000_wl"]


def test_neutral_mentions_and_family_overrides_preserve_last_scientific_intent() -> None:
    from app.api import chat as chat_module

    for prompt, expected in (
        (
            "Run Planck under LCDM, then explain Planck provenance.",
            ["planck2018_compressed"],
        ),
        (
            "Run Planck under LCDM; discuss Planck systematics.",
            ["planck2018_compressed"],
        ),
        (
            "Run Planck and KiDS-1000 under LCDM; explain how KiDS-1000 "
            "differs from DES Y3.",
            ["planck2018_compressed", "kids1000_wl"],
        ),
        (
            "Run Planck, then do not use any CMB data under LCDM.",
            [],
        ),
        (
            "Do not use any CMB data, then run Planck under LCDM.",
            ["planck2018_compressed"],
        ),
        (
            "Run KiDS-1000, then exclude all weak-lensing data under LCDM.",
            [],
        ),
        (
            "Exclude all weak-lensing data, then run KiDS-1000 under LCDM.",
            ["kids1000_wl"],
        ),
        (
            "Run weak-lensing datasets other than DES Y3 under LCDM.",
            ["kids1000_wl", "hsc_y1_cosmic_shear"],
        ),
    ):
        assert chat_module._cosmology_dataset_keys_from_prompt(prompt) == expected

    reinclude_prompt = (
        "Run all weak-lensing datasets except DES Y3; then run DES Y3 "
        "separately under LCDM."
    )
    assert [
        call["input"]["dataset_keys"]
        for call in chat_module._cosmology_likelihood_build_calls_from_prompt(
            reinclude_prompt
        )
    ] == [
        ["kids1000_wl", "hsc_y1_cosmic_shear"],
        ["des_y3_3x2pt"],
    ]


def test_release_alternatives_group_cues_and_multi_model_runs_remain_structured() -> None:
    from app.api import chat as chat_module

    for prompt, expected_keys in (
        (
            "Use DESI DR2 rather than DESI DR1 under LCDM.",
            ["desi_dr2_bao"],
        ),
        (
            "Use DESI DR1 rather than DESI DR2 under LCDM.",
            ["desi_dr1_bao"],
        ),
        (
            "Run DESI DR2 first, then use DESI DR1 instead under LCDM.",
            ["desi_dr1_bao"],
        ),
    ):
        assert chat_module._cosmology_dataset_keys_from_prompt(prompt) == expected_keys

    for prompt in (
        "Run DESI DR1 and DESI DR2 separately under LCDM.",
        "Compare DESI DR1 versus DESI DR2 without combining them under LCDM.",
        "Run DESI DR1 with and without DESI DR2 under LCDM.",
    ):
        assert [
            call["input"]["dataset_keys"]
            for call in chat_module._cosmology_likelihood_build_calls_from_prompt(
                prompt
            )
        ] == [["desi_dr1_bao"], ["desi_dr2_bao"]]

    for prompt, expected_groups in (
        (
            "Run Planck and KiDS-1000, never combine them, under LCDM.",
            [["planck2018_compressed"], ["kids1000_wl"]],
        ),
        (
            "Do not combine Planck and KiDS-1000; run them separately under LCDM.",
            [["planck2018_compressed"], ["kids1000_wl"]],
        ),
        (
            "Do not run Planck and KiDS-1000 separately; combine them under LCDM.",
            [["planck2018_compressed", "kids1000_wl"]],
        ),
        (
            "Run Planck and KiDS-1000 jointly under LCDM; report systematics separately.",
            [["planck2018_compressed", "kids1000_wl"]],
        ),
        (
            "运行 Planck 和 KiDS-1000，但不要组合它们，在 LCDM 下。",
            [["planck2018_compressed"], ["kids1000_wl"]],
        ),
        (
            "Run Planck with and without weak lensing under LCDM.",
            [
                ["planck2018_compressed"],
                [
                    "planck2018_compressed",
                    "kids1000_wl",
                    "des_y3_3x2pt",
                    "hsc_y1_cosmic_shear",
                ],
            ],
        ),
        (
            "Run Planck with and without an H0 prior under LCDM.",
            [
                ["planck2018_compressed"],
                ["planck2018_compressed", "shoes_h0_riess22"],
            ],
        ),
        (
            "Run Planck with and without KiDS-1000; then add Pantheon+ "
            "to both under LCDM.",
            [
                ["pantheon_plus", "planck2018_compressed"],
                ["pantheon_plus", "planck2018_compressed", "kids1000_wl"],
            ],
        ),
    ):
        assert [
            call["input"]["dataset_keys"]
            for call in chat_module._cosmology_likelihood_build_calls_from_prompt(
                prompt
            )
        ] == expected_groups

    multi_model_prompt = (
        "Run Planck with and without KiDS-1000 under LCDM and wCDM."
    )
    assert [
        (call["input"]["model"], call["input"]["dataset_keys"])
        for call in chat_module._cosmology_likelihood_run_calls_from_prompt(
            multi_model_prompt
        )
    ] == [
        ("lcdm", ["planck2018_compressed"]),
        ("lcdm", ["planck2018_compressed", "kids1000_wl"]),
        ("wcdm", ["planck2018_compressed"]),
        ("wcdm", ["planck2018_compressed", "kids1000_wl"]),
    ]


def test_overlapping_joint_groups_remain_independent_likelihoods() -> None:
    from app.api import chat as chat_module

    for prompt, expected_groups in (
        (
            "Run Planck with KiDS jointly; separately run Pantheon with "
            "DESI DR2 jointly under LCDM.",
            [
                ["planck2018_compressed", "kids1000_wl"],
                ["pantheon_plus", "desi_dr2_bao"],
            ],
        ),
        (
            "Run Planck+KiDS and Planck+Pantheon as two separate fits "
            "under LCDM.",
            [
                ["planck2018_compressed", "kids1000_wl"],
                ["planck2018_compressed", "pantheon_plus"],
            ],
        ),
        (
            "Run Planck and KiDS jointly; then run KiDS and Pantheon "
            "jointly under LCDM.",
            [
                ["planck2018_compressed", "kids1000_wl"],
                ["kids1000_wl", "pantheon_plus"],
            ],
        ),
        (
            "Run Planck with and without KiDS; separately run Pantheon "
            "under LCDM.",
            [
                ["planck2018_compressed"],
                ["planck2018_compressed", "kids1000_wl"],
                ["pantheon_plus"],
            ],
        ),
        (
            "Run DESI DR1 and DESI DR2 with and without Planck under LCDM.",
            [
                ["desi_dr1_bao"],
                ["desi_dr1_bao", "planck2018_compressed"],
                ["desi_dr2_bao"],
                ["desi_dr2_bao", "planck2018_compressed"],
            ],
        ),
    ):
        assert [
            call["input"]["dataset_keys"]
            for call in chat_module._cosmology_likelihood_build_calls_from_prompt(
                prompt
            )
        ] == expected_groups
        assert [
            call["input"]["dataset_keys"]
            for call in chat_module._cosmology_likelihood_run_calls_from_prompt(
                prompt
            )
        ] == expected_groups


def test_with_without_lists_and_every_fit_modifiers_preserve_both_arms() -> None:
    from app.api import chat as chat_module

    for prompt, expected_groups in (
        (
            "Run Planck with/without both KiDS and Pantheon+ under LCDM.",
            [
                ["planck2018_compressed"],
                [
                    "planck2018_compressed",
                    "kids1000_wl",
                    "pantheon_plus",
                ],
            ],
        ),
        (
            "Run Planck with and without KiDS, plus Pantheon in every fit "
            "under LCDM.",
            [
                ["pantheon_plus", "planck2018_compressed"],
                [
                    "pantheon_plus",
                    "planck2018_compressed",
                    "kids1000_wl",
                ],
            ],
        ),
    ):
        assert [
            call["input"]["dataset_keys"]
            for call in chat_module._cosmology_likelihood_build_calls_from_prompt(
                prompt
            )
        ] == expected_groups
        assert [
            call["input"]["dataset_keys"]
            for call in chat_module._cosmology_likelihood_run_calls_from_prompt(
                prompt
            )
        ] == expected_groups


def test_agent_loop_enforces_dataset_identity_after_nonempty_model_reply(
    monkeypatch,
) -> None:
    import asyncio

    from app.api import chat as chat_module

    harmful_reply = (
        "KiDS-Legacy DR5 and KiDS-1000 are two names for the same dataset."
    )
    llm_reply = {"content": harmful_reply}

    async def fake_llm(**_kwargs):
        # The loop deterministically overrides this text with registry/config/run
        # calls for the first three iterations.  On the synthesis iteration it
        # becomes the harmful non-empty draft that the post-condition must drop.
        return {
            "content": llm_reply["content"],
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_exec(tool_calls, *_args, **_kwargs):
        executed = []
        for call in tool_calls:
            if call["name"] == "list_cosmology_datasets":
                result = {"datasets": [{
                    "key": "kids1000_wl",
                    "display_name": "KiDS-1000 cosmic shear",
                    "version": "KiDS-1000 cosmic-shear likelihood",
                }]}
            elif call["name"] == "build_cosmology_likelihood":
                result = {
                    "model": "lcdm",
                    "config_hash": "guard-test-config",
                }
            elif call["name"] == "run_cosmology_likelihood_chain":
                result = {
                    "success": True,
                    "publication_ready": False,
                    "preliminary_ready": True,
                    "datasets_used": [{
                        "key": "kids1000_wl",
                        "display_name": "KiDS-1000 cosmic shear",
                        "version": "KiDS-1000 cosmic-shear likelihood",
                    }],
                    "warnings": ["Compressed preliminary runner only."],
                }
            else:  # pragma: no cover - the deterministic route is pinned above
                raise AssertionError(f"unexpected tool {call['name']}")
            executed.append({**call, "result": result})
        return executed

    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(dict(event))

    monkeypatch.setattr(chat_module, "_llm_messages_create", fake_llm)
    monkeypatch.setattr(chat_module, "_execute_tool_calls", fake_exec)
    result = asyncio.run(
        chat_module._run_agent_loop(
            system="test cosmology system",
            messages=[{
                "role": "user",
                "content": (
                    "Run the executable cosmology likelihood chain with datasets "
                    "KiDS-Legacy DR5 cosmic shear for model lcdm and state exactly "
                    "which registered release was run."
                ),
            }],
            tools=[
                {"name": "list_cosmology_datasets", "input_schema": {}},
                {"name": "build_cosmology_likelihood", "input_schema": {}},
                {"name": "run_cosmology_likelihood_chain", "input_schema": {}},
            ],
            provider_api_keys={},
            agent_name="orchestrator",
            python_session_id="dataset-identity-guard-test",
            on_event=collect,
        )
    )

    assert "same dataset" not in result["reply"]
    assert "Dataset substitution disclosure" in result["reply"]
    assert "not to the requested release" in result["reply"]
    assert any(
        event.get("type") == "gate_event"
        and event.get("gate") == "dataset_identity"
        and event.get("action") == "downgraded_summary"
        for event in events
    )
    assert any(
        item.get("gate") == "dataset_identity"
        for item in result["validation_summary"]["interventions"]
    )

    from app.services import claim_validator

    original_validate_claims = claim_validator.validate_claims

    def fail_identity_validation(reply, tool_results):
        if "Dataset substitution disclosure" in str(reply):
            raise RuntimeError("secondary validator unavailable")
        return original_validate_claims(reply, tool_results)

    monkeypatch.setattr(
        claim_validator,
        "validate_claims",
        fail_identity_validation,
    )
    events.clear()
    validation_failed = asyncio.run(
        chat_module._run_agent_loop(
            system="test cosmology system",
            messages=[{
                "role": "user",
                "content": (
                    "Run the executable cosmology likelihood chain with datasets "
                    "KiDS-Legacy DR5 cosmic shear for model lcdm and state exactly "
                    "which registered release was run."
                ),
            }],
            tools=[
                {"name": "list_cosmology_datasets", "input_schema": {}},
                {"name": "build_cosmology_likelihood", "input_schema": {}},
                {"name": "run_cosmology_likelihood_chain", "input_schema": {}},
            ],
            provider_api_keys={},
            agent_name="orchestrator",
            python_session_id="dataset-identity-validation-failure-test",
            on_event=collect,
        )
    )
    assert "secondary claim validation failed" in validation_failed["reply"]
    assert validation_failed["validation_summary"]["blocked"] is True
    assert validation_failed["validation_summary"]["numeric_gate"] == "blocked"
    assert any(
        item.get("gate") == "dataset_identity"
        and item.get("action") == "blocked"
        and item.get("reason") == "dataset_identity_validation_error"
        for item in validation_failed["validation_summary"]["interventions"]
    )



def test_dataset_identity_disclosure_preserves_honest_abstention(
    monkeypatch,
) -> None:
    import asyncio

    from app.api import chat as chat_module

    calls = {"count": 0}

    async def fake_llm(**_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "content": "",
                "stop_reason": "tool_use",
                "tool_calls": [{
                    "id": "dataset-registry-call",
                    "name": "list_cosmology_datasets",
                    "input": {},
                }],
            }
        return {
            "content": (
                '<tools_returned_nothing failed_tools="" empty_tools="" '
                'rationale="No claimable requested-release result." '
                'suggested_next_step="Use a registered release."/>'
            ),
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_exec(tool_calls, *_args, **_kwargs):
        return [{
            **call,
            "result": {"datasets": [{
                "key": "kids1000_wl",
                "display_name": "KiDS-1000 cosmic shear",
                "version": "KiDS-1000 cosmic-shear likelihood",
            }]},
        } for call in tool_calls]

    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(dict(event))

    monkeypatch.setattr(chat_module, "_llm_messages_create", fake_llm)
    monkeypatch.setattr(chat_module, "_execute_tool_calls", fake_exec)
    abstention_result = asyncio.run(
        chat_module._run_agent_loop(
            system="test cosmology system",
            messages=[{
                "role": "user",
                "content": (
                    "I requested KiDS-Legacy DR5. Check the registry and answer "
                    "only if that requested release was found."
                ),
            }],
            tools=[
                {"name": "list_cosmology_datasets", "input_schema": {}},
            ],
            provider_api_keys={},
            agent_name="orchestrator",
            python_session_id="dataset-identity-abstention-test",
            on_event=collect,
        )
    )

    assert abstention_result.get("honest_abstention") is True, (
        abstention_result,
        events,
    )
    assert abstention_result["abstention_reason"]
    assert "Dataset substitution disclosure" in abstention_result["reply"]
    assert "No claimable requested-release result" in abstention_result["reply"]
    assert abstention_result["validation_summary"]["numeric_gate"] == "not_run"
    assert abstention_result["validation_summary"]["citation_gate"] == "not_run"
    assert abstention_result["validation_summary"]["reason"] == (
        "honest_abstention_dataset_identity_disclosure"
    )
    assert any(
        event.get("type") == "honest_abstention"
        and event.get("payload", {}).get(
            "reply_overridden_by_dataset_identity"
        ) is True
        for event in events
    )
    assert any(
        "Dataset substitution disclosure"
        in str(event.get("payload", {}).get("rationale") or "")
        for event in events
        if event.get("type") == "honest_abstention"
    )
    assert not any(
        event.get("type") == "agent_text"
        and "tools_returned_nothing" in str(event.get("content") or "")
        for event in events
    )


def test_research_auto_fact_check_and_export_emit_tool_events(monkeypatch) -> None:
    import asyncio

    from app.api import chat as chat_module

    async def fake_llm(**_kwargs):
        return {
            "content": "The controlled research workflow is complete.",
            "stop_reason": "end_turn",
            "tool_calls": [],
        }

    async def fake_exec(tool_calls, *_args, **_kwargs):
        executed = []
        for call in tool_calls:
            if call["name"] == "plan_research_program":
                result = {"research_plan": {
                    "research_question": "Test a controlled BAO baseline.",
                    "required_probes": ["BAO"],
                    "model_families": ["lcdm"],
                    "blocking_gaps": [],
                }}
            elif call["name"] == "run_research_matrix":
                result = {"matrix_size": 0, "ready_cells": 0, "matrix": []}
            elif call["name"] == "build_evidence_graph":
                result = {
                    "success": True,
                    "evidence_graph": {
                        "claimable_parameters": [],
                        "supported_claims": [],
                        "unsupported_claims": [],
                    },
                    "claimable_parameters": [],
                }
            else:  # pragma: no cover - deterministic research route is pinned
                raise AssertionError(f"unexpected tool {call['name']}")
            executed.append({**call, "result": result})
        return executed

    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(dict(event))

    monkeypatch.setattr(chat_module, "_llm_messages_create", fake_llm)
    monkeypatch.setattr(chat_module, "_execute_tool_calls", fake_exec)
    result = asyncio.run(
        chat_module._run_agent_loop(
            system="test research system",
            messages=[{
                "role": "user",
                "content": (
                    "Plan + run a 3-probe analysis (BAO + Pantheon+ + Planck "
                    "compressed) under LCDM. Then build the evidence graph, run "
                    "fact-check, and export a draft report. Tell me which cells "
                    "are publication_ready vs executed_not_ready."
                ),
            }],
            tools=[
                {"name": "plan_research_program", "input_schema": {}},
                {"name": "run_research_matrix", "input_schema": {}},
                {"name": "build_evidence_graph", "input_schema": {}},
            ],
            provider_api_keys={},
            agent_name="orchestrator",
            python_session_id="auto-research-events-test",
            on_event=collect,
        )
    )

    automatic_calls = [
        event.get("tool")
        for event in events
        if event.get("type") == "tool_call" and event.get("automatic") is True
    ]
    automatic_results = [
        event.get("tool")
        for event in events
        if event.get("type") == "tool_result" and event.get("automatic") is True
    ]
    assert automatic_calls == [
        "verify_research_facts", "export_research_report"
    ], (events, result)
    assert automatic_results == [
        "verify_research_facts", "export_research_report"
    ], (events, result)
    assert {
        item.get("tool") for item in result["tool_results"]
    } >= {
        "plan_research_program",
        "run_research_matrix",
        "build_evidence_graph",
        "verify_research_facts",
        "export_research_report",
    }

    from app.services import research_program

    original_verify_research_facts = research_program.verify_research_facts

    def fail_fact_check(**_kwargs):
        raise RuntimeError("fact-check backend unavailable")

    monkeypatch.setattr(
        research_program,
        "verify_research_facts",
        fail_fact_check,
    )
    events.clear()
    failed_result = asyncio.run(
        chat_module._run_agent_loop(
            system="test research system",
            messages=[{
                "role": "user",
                "content": (
                    "Plan + run a 3-probe analysis (BAO + Pantheon+ + Planck "
                    "compressed) under LCDM. Then build the evidence graph, run "
                    "fact-check, and export a draft report. Tell me which cells "
                    "are publication_ready vs executed_not_ready."
                ),
            }],
            tools=[
                {"name": "plan_research_program", "input_schema": {}},
                {"name": "run_research_matrix", "input_schema": {}},
                {"name": "build_evidence_graph", "input_schema": {}},
            ],
            provider_api_keys={},
            agent_name="orchestrator",
            python_session_id="auto-research-events-failure-test",
            on_event=collect,
        )
    )

    failed_tools = {
        item.get("tool"): item.get("result")
        for item in failed_result["tool_results"]
    }
    assert failed_tools["verify_research_facts"]["status"] == "blocked"
    assert failed_tools["verify_research_facts"]["__do_not_claim__"] is True
    assert "export_research_report" not in failed_tools
    assert "Automatic fact verification failed" in failed_result["reply"]
    assert failed_result["validation_summary"]["blocked"] is True
    assert failed_result["validation_summary"]["numeric_gate"] == "blocked"
    assert failed_result["validation_summary"]["citation_gate"] == "blocked"
    assert any(
        item.get("gate") == "fact_verification"
        and item.get("action") == "blocked"
        and item.get("reason") == "automatic_fact_check_failed"
        for item in failed_result["validation_summary"]["interventions"]
    )
    assert any(
        event.get("type") == "tool_result"
        and event.get("tool") == "verify_research_facts"
        and event.get("automatic") is True
        and event.get("result", {}).get("__tool_status__") == "FAILED"
        for event in events
    )

    from app.services.agent_runtime import loop as loop_module

    def block_without_safe_summary(**_kwargs):
        return {
            "success": True,
            "status": "blocked",
            "publication_ready": False,
            "claims": [{
                "status": "unsupported",
                "claim": "Unsupported merged conclusion.",
            }],
        }

    monkeypatch.setattr(
        research_program,
        "verify_research_facts",
        block_without_safe_summary,
    )
    monkeypatch.setattr(
        loop_module,
        "_research_tool_grounded_summary",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        loop_module,
        "_cosmology_tool_grounded_summary",
        lambda *_args, **_kwargs: None,
    )
    events.clear()
    no_summary_result = asyncio.run(
        chat_module._run_agent_loop(
            system="test research system",
            messages=[{
                "role": "user",
                "content": (
                    "Plan + run a 3-probe analysis (BAO + Pantheon+ + Planck "
                    "compressed) under LCDM, fact-check it, and export a report."
                ),
            }],
            tools=[
                {"name": "plan_research_program", "input_schema": {}},
                {"name": "run_research_matrix", "input_schema": {}},
                {"name": "build_evidence_graph", "input_schema": {}},
            ],
            provider_api_keys={},
            agent_name="orchestrator",
            python_session_id="fact-check-blocked-no-summary-test",
            on_event=collect,
        )
    )
    assert no_summary_result["validation_summary"]["blocked"] is True
    assert no_summary_result["validation_summary"]["numeric_gate"] == "blocked"
    assert no_summary_result["validation_summary"]["citation_gate"] == "blocked"
    assert not any(
        item.get("tool") == "export_research_report"
        for item in no_summary_result["tool_results"]
    )
    assert not any(
        event.get("tool") == "export_research_report"
        for event in events
    )

    def fail_report_export(**_kwargs):
        raise RuntimeError("report store unavailable")

    monkeypatch.setattr(
        research_program,
        "verify_research_facts",
        original_verify_research_facts,
    )
    monkeypatch.setattr(
        research_program,
        "export_research_report",
        fail_report_export,
    )
    events.clear()
    export_failed_result = asyncio.run(
        chat_module._run_agent_loop(
            system="test research system",
            messages=[{
                "role": "user",
                "content": (
                    "Plan + run a 3-probe analysis (BAO + Pantheon+ + Planck "
                    "compressed) under LCDM. Then build the evidence graph, run "
                    "fact-check, and export a draft report. Tell me which cells "
                    "are publication_ready vs executed_not_ready."
                ),
            }],
            tools=[
                {"name": "plan_research_program", "input_schema": {}},
                {"name": "run_research_matrix", "input_schema": {}},
                {"name": "build_evidence_graph", "input_schema": {}},
            ],
            provider_api_keys={},
            agent_name="orchestrator",
            python_session_id="auto-research-report-failure-test",
            on_event=collect,
        )
    )
    export_failure = next(
        item
        for item in export_failed_result["tool_results"]
        if item.get("tool") == "export_research_report"
    )
    assert export_failure["result"]["__tool_status__"] == "FAILED"
    assert export_failure["result"]["analysis_status"] == (
        "REPORT_EXPORT_FAILED"
    )
    assert "report artifact was not" in export_failed_result["reply"]
    assert export_failed_result["validation_summary"]["blocked"] is True
    assert export_failed_result["validation_summary"]["numeric_gate"] == "blocked"
    assert export_failed_result["validation_summary"]["citation_gate"] == "blocked"
    assert any(
        item.get("gate") == "report_export"
        and item.get("action") == "blocked"
        and item.get("reason") == "automatic_report_export_failed"
        for item in export_failed_result["validation_summary"]["interventions"]
    )
    assert any(
        event.get("type") == "tool_result"
        and event.get("tool") == "export_research_report"
        and event.get("automatic") is True
        and event.get("result", {}).get("__tool_status__") == "FAILED"
        for event in events
    )


def test_multi_agent_fact_check_failure_blocks_merge_and_skips_report(
    monkeypatch,
) -> None:
    import asyncio
    from types import SimpleNamespace

    from app.api import chat as chat_module
    from app.services import research_program

    research_tools = [{
        "id": "research-plan",
        "tool": "plan_research_program",
        "input": {},
        "result": {"research_plan": {
            "research_question": "Test a controlled BAO baseline.",
            "required_probes": ["BAO"],
            "model_families": ["lcdm"],
            "blocking_gaps": [],
        }},
    }, {
        "id": "research-matrix",
        "tool": "run_research_matrix",
        "input": {},
        "result": {"matrix_size": 0, "ready_cells": 0, "matrix": []},
    }, {
        "id": "evidence-graph",
        "tool": "build_evidence_graph",
        "input": {},
        "result": {
            "success": True,
            "evidence_graph": {
                "claimable_parameters": [],
                "supported_claims": [],
                "unsupported_claims": [],
            },
            "claimable_parameters": [],
        },
    }]
    all_specialists_abstain = {"value": False}
    member_report_failure = {"value": False}
    member_report_success = {"value": False}

    async def fake_agent_loop(**kwargs):
        if (
            (
                kwargs.get("agent_name") == "analyst"
                or all_specialists_abstain["value"]
            )
            and kwargs.get("on_event") is not None
        ):
            await kwargs["on_event"]({
                "type": "honest_abstention",
                "payload": {
                    "reason": "no_tools",
                    "rationale": "Specialist could not make a claim.",
                },
            })
        is_analyst = kwargs.get("agent_name") == "analyst"
        specialist_tools = list(research_tools) if is_analyst else []
        if is_analyst and member_report_failure["value"]:
            specialist_tools.append({
                "id": "member-report-failure",
                "tool": "export_research_report",
                "input": {},
                "result": {
                    "success": False,
                    "__tool_status__": "FAILED",
                    "analysis_status": "REPORT_EXPORT_FAILED",
                },
            })
        if is_analyst and member_report_success["value"]:
            specialist_tools.append({
                "id": "member-report-success",
                "tool": "export_research_report",
                "input": {"report_scope": "specialist"},
                "result": {
                    "success": True,
                    "__tool_status__": "COMPLETED",
                    "analysis_status": "RESEARCH_REPORT_READY",
                },
            })
        return {
            "reply": "Specialist tool work complete.",
            "actions": [],
            "tool_results": specialist_tools,
            "hit_deadline": False,
            "hit_iteration_cap": False,
            "honest_abstention": all_specialists_abstain["value"],
            "abstention_reason": (
                "no_tools" if all_specialists_abstain["value"] else None
            ),
            "validation_summary": {
                "schema_version": 1,
                "numeric_gate": "passed",
                "citation_gate": "passed",
                "regen_count": 0,
                "blocked": False,
                "interventions": (
                    [{
                        "gate": "report_export",
                        "action": "blocked",
                        "reason": "automatic_report_export_failed",
                    }]
                    if is_analyst and member_report_failure["value"]
                    else []
                ),
            },
        }

    async def fake_handoff(source, target, _reply):
        return SimpleNamespace(
            source_agent=source,
            target_agent=target,
            context_summary="Tool work completed.",
            instruction="Independently review the tool results.",
        )

    async def fake_merge(_agent_results):
        return "The merged controlled research workflow is complete."

    def fail_fact_check(**_kwargs):
        raise RuntimeError("merged fact-check backend unavailable")

    report_calls = {"count": 0}

    def count_report_export(**_kwargs):
        report_calls["count"] += 1
        return {"success": True}

    monkeypatch.setattr(chat_module, "_run_agent_loop", fake_agent_loop)
    monkeypatch.setattr(
        chat_module.orchestrator,
        "get_agent_runtime",
        lambda _name, _context: {"system_prompt": "specialist", "tool_names": []},
    )
    monkeypatch.setattr(
        chat_module.orchestrator,
        "summarize_handoff",
        fake_handoff,
    )
    monkeypatch.setattr(
        chat_module.orchestrator,
        "merge_responses",
        fake_merge,
    )
    monkeypatch.setattr(
        research_program,
        "verify_research_facts",
        fail_fact_check,
    )
    monkeypatch.setattr(
        research_program,
        "export_research_report",
        count_report_export,
    )

    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(dict(event))

    async def run_multi(session_id: str):
        return await chat_module._run_orchestrated_chat(
            runtime={
                "agent_names": ["analyst", "reviewer"],
                "base_system": "test multi-agent system",
                "toolset": [],
            },
            messages=[{
                "role": "user",
                "content": (
                    "Research DESI BAO + Planck CMB consistency under LCDM "
                    "using a controlled analysis workflow."
                ),
            }],
            provider_api_keys={},
            python_session_id=session_id,
            on_event=collect,
        )

    result = asyncio.run(run_multi("merged-fact-check-failure-test"))

    assert "Automatic fact verification" in result["reply"]
    assert result["validation_summary"]["blocked"] is True
    assert result["validation_summary"]["numeric_gate"] == "blocked"
    assert result["validation_summary"]["citation_gate"] == "blocked"
    assert result["validation_summary"]["reason"] == (
        "automatic_fact_check_failed"
    )
    failed_fact = next(
        item
        for item in result["tool_results"]
        if item.get("tool") == "verify_research_facts"
    )
    assert failed_fact["result"]["__tool_status__"] == "FAILED"
    assert failed_fact["result"]["status"] == "blocked"
    assert not any(
        item.get("tool") == "export_research_report"
        for item in result["tool_results"]
    )
    assert report_calls["count"] == 0
    assert any(
        item.get("gate") == "fact_verification"
        and item.get("action") == "blocked"
        for item in result["validation_summary"]["interventions"]
    )
    assert any(
        event.get("type") == "tool_result"
        and event.get("tool") == "verify_research_facts"
        and event.get("result", {}).get("__tool_status__") == "FAILED"
        for event in events
    )
    assert not any(
        event.get("type") == "honest_abstention"
        for event in events
    )

    def block_merged_fact_check(**_kwargs):
        return {
            "success": True,
            "status": "blocked",
            "publication_ready": False,
            "claims": [{
                "status": "unsupported",
                "claim": "Unsupported merged claim.",
            }],
        }

    monkeypatch.setattr(
        research_program,
        "verify_research_facts",
        block_merged_fact_check,
    )
    monkeypatch.setattr(
        research_program,
        "export_research_report",
        count_report_export,
    )
    monkeypatch.setattr(
        chat_module,
        "_research_tool_grounded_summary",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        chat_module,
        "_cosmology_tool_grounded_summary",
        lambda *_args, **_kwargs: None,
    )
    report_calls["count"] = 0
    events.clear()
    no_safe_merge = asyncio.run(run_multi("merged-fact-block-no-summary-test"))
    assert no_safe_merge["validation_summary"]["blocked"] is True
    assert no_safe_merge["validation_summary"]["numeric_gate"] == "blocked"
    assert no_safe_merge["validation_summary"]["citation_gate"] == "blocked"
    assert report_calls["count"] == 0
    assert not any(
        item.get("tool") == "export_research_report"
        for item in no_safe_merge["tool_results"]
    )

    def pass_fact_check(**_kwargs):
        return {
            "success": True,
            "status": "passed",
            "publication_ready": False,
            "claims": [],
        }

    monkeypatch.setattr(
        research_program,
        "verify_research_facts",
        pass_fact_check,
    )
    member_report_failure["value"] = True
    monkeypatch.setattr(
        research_program,
        "export_research_report",
        count_report_export,
    )
    events.clear()
    recovered_report = asyncio.run(run_multi("member-report-recovery-test"))
    assert report_calls["count"] == 1
    report_results = [
        item.get("result")
        for item in recovered_report["tool_results"]
        if item.get("tool") == "export_research_report"
    ]
    assert any(
        isinstance(item, dict) and item.get("success") is True
        for item in report_results
    )
    assert recovered_report["validation_summary"]["blocked"] is False
    assert recovered_report["validation_summary"]["numeric_gate"] == (
        "regenerated"
    )
    assert recovered_report["validation_summary"]["citation_gate"] == (
        "regenerated"
    )

    member_report_failure["value"] = False
    member_report_success["value"] = True
    report_calls["count"] = 0
    events.clear()
    merged_after_member_report = asyncio.run(
        run_multi("successful-member-report-still-merges-test")
    )
    assert report_calls["count"] == 1
    assert any(
        item.get("tool") == "export_research_report"
        and item.get("input", {}).get("report_scope") == "merged"
        and item.get("result", {}).get("success") is True
        for item in merged_after_member_report["tool_results"]
    )

    all_specialists_abstain["value"] = True
    events.clear()
    all_abstained = asyncio.run(run_multi("all-specialists-abstain-test"))
    assert all_abstained["honest_abstention"] is True
    assert all_abstained["abstention_reason"] == "no_tools"
    assert all_abstained["validation_summary"]["numeric_gate"] == "not_run"
    assert all_abstained["validation_summary"]["citation_gate"] == "not_run"
    assert all_abstained["validation_summary"]["reason"] == (
        "all_specialists_honest_abstention"
    )
    assert sum(
        event.get("type") == "honest_abstention"
        for event in events
    ) == 1
    assert any(
        event.get("type") == "status"
        and event.get("specialist_abstention")
        for event in events
    )

    all_specialists_abstain["value"] = False
    member_report_failure["value"] = False
    member_report_success["value"] = False
    report_calls["count"] = 0

    def fail_merged_report(**_kwargs):
        report_calls["count"] += 1
        raise RuntimeError("merged report store unavailable")

    monkeypatch.setattr(
        research_program,
        "verify_research_facts",
        pass_fact_check,
    )
    monkeypatch.setattr(
        research_program,
        "export_research_report",
        fail_merged_report,
    )
    events.clear()
    report_failed = asyncio.run(run_multi("merged-report-failure-test"))
    assert report_calls["count"] == 1
    assert "report artifact was not" in report_failed["reply"]
    assert report_failed["validation_summary"]["blocked"] is True
    assert report_failed["validation_summary"]["numeric_gate"] == "blocked"
    assert report_failed["validation_summary"]["citation_gate"] == "blocked"
    assert report_failed["validation_summary"]["reason"] == (
        "automatic_report_export_failed"
    )
    failed_report = next(
        item
        for item in report_failed["tool_results"]
        if item.get("tool") == "export_research_report"
    )
    assert failed_report["result"]["__tool_status__"] == "FAILED"
    assert any(
        item.get("gate") == "report_export"
        and item.get("action") == "blocked"
        for item in report_failed["validation_summary"]["interventions"]
    )
    assert not any(
        event.get("type") == "honest_abstention"
        for event in events
    )


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
    assert "execution_level=executed_not_ready" in summary
    assert "BAO + WL · ESS 105 below threshold 400" in summary
    assert "BAO only · ESS 1310 meets threshold 400" in summary
    assert "BAO only · ESS 1310 below threshold 400" not in summary
    assert "`compressed_or_approximate_likelihood`" in summary
    assert "`fewer_than_four_independent_chains`" in summary
    assert "Config-only or not-runnable branches" in summary
    assert "BAO + SN · Pantheon+ requires external Cobaya/CosmoSIS." in summary
    assert "BAO + WL" not in summary.split("Config-only or not-runnable branches:", 1)[-1]


def test_tainted_research_matrix_summary_never_emits_cell_numbers() -> None:
    from app.api.chat import _research_tool_grounded_summary

    summary = _research_tool_grounded_summary([
        {
            "tool": "run_research_matrix",
            "result": {
                "publication_ready": False,
                "__do_not_claim__": True,
                "matrix": [
                    {
                        "label": "BAO + CMB",
                        "publication_ready": True,
                        "result": {
                            "parameters": {"H0": {"median": 67.28}},
                            "chain_diagnostics": {"ess_bulk": 901, "rhat": 1.001},
                        },
                    }
                ],
            },
        }
    ])

    assert summary is not None
    assert "BAO + CMB" in summary
    assert "67.28" not in summary
    assert "901" not in summary
    assert "1.001" not in summary


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
    assert "No publication-ready direct likelihood result completed this turn." in summary
    assert "Planck PR4/NPIPE EB/TB polarization-rotation products (planck_pr4_ebtb_rotation)" in summary
    assert "ACT DR6 EB/TB polarization-rotation products (act_dr6_ebtb_rotation)" in summary
    assert "{'key':" not in summary


def test_exclusion_survives_later_neutral_mention() -> None:
    # Regression (2026-07-23 review): last-mention-wins intent folding let a
    # verb-less follow-up mention ("Weak lensing would be double counting")
    # override an explicit exclusion, silently adding excluded datasets back
    # into the fit. A default-executable mention must never cancel an
    # explicit exclusion; only an explicit execution request can.
    from app.services.agent_runtime.prompt_routing import (
        _cosmology_forbidden_probe_families,
        _cosmology_likelihood_run_calls_from_prompt,
    )

    def dataset_keys(prompt: str) -> list[list[str]]:
        return [
            call["input"]["dataset_keys"]
            for call in _cosmology_likelihood_run_calls_from_prompt(prompt)
        ]

    assert dataset_keys(
        "Do not use weak lensing. Run Planck and Pantheon in LCDM. "
        "Weak lensing would be double counting."
    ) == [["pantheon_plus", "planck2018_compressed"]]
    assert "wl" in _cosmology_forbidden_probe_families(
        "Do not use weak lensing. Run Planck and Pantheon in LCDM. "
        "Weak lensing would be double counting."
    )
    assert dataset_keys(
        "Exclude KiDS. Run Planck and Pantheon in LCDM. "
        "KiDS is a lensing survey."
    ) == [["pantheon_plus", "planck2018_compressed"]]
    assert dataset_keys(
        "Do not use BAO. Run Planck and Pantheon in LCDM. "
        "Note that BAO traces the sound horizon."
    ) == [["pantheon_plus", "planck2018_compressed"]]

    # ``both`` directly followed by concrete dataset names is a determiner
    # scoped to those names, not an anaphor cancelling Planck's exclusion.
    assert dataset_keys(
        "Do not use Planck. Then run both DESI DR1 and DESI DR2 in LCDM."
    ) == [["desi_dr2_bao"]]

    # Specificity: an explicit later execution request still re-enables the
    # family, and true anaphors still override an exclusion.
    assert dataset_keys(
        "Do not use weak lensing yet. "
        "Now run KiDS-1000 weak lensing with Planck in LCDM."
    ) == [["planck2018_compressed", "kids1000_wl"]]
    assert dataset_keys(
        "Do not run DESI DR1 and Pantheon separately; "
        "then combine them in LCDM."
    ) == [["desi_dr1_bao", "pantheon_plus"]]
