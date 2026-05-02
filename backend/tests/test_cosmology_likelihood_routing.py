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
    from app.api.chat import _cosmology_models_from_prompt

    prompt = (
        "我在做 galaxy weak-lensing surveys 的 S8 consistency 检验。请基于 "
        "KiDS-1000、DES Y3 3x2pt、HSC Y1 与 Planck compressed prior 的可用资料，"
        "只用本轮工具结果比较 S8 posterior。"
    )

    assert _cosmology_models_from_prompt(prompt) == ["lcdm"]


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
