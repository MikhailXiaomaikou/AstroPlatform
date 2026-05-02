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
    assert [call["name"] for call in calls] == [
        "build_cosmology_robustness_matrix",
        "build_cosmology_robustness_matrix",
        "build_cosmology_robustness_matrix",
    ]
    assert [call["input"]["model"] for call in calls] == ["lcdm", "wcdm", "w0wa_cdm"]
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
