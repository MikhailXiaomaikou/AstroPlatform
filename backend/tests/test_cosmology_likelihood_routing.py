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
