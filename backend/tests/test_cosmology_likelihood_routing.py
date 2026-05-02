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

