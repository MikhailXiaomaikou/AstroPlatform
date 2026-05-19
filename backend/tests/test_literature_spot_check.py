"""Stage 4 MVP: literature spot-check 单元测试.

覆盖:
  - SN distance modulus 正向/反向/unknown 三个 case
  - CMB compressed parameter 正向/反向/unknown 三个 case
  - is_spot_check_enabled() opt-in flag 行为
  - PLANCK_2018_BASELINE 跟 cosmology_likelihoods.py 一致 (drift detection)
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from app.services.literature_spot_check import (
    PLANCK_2018_BASELINE,
    check_cmb_compressed_value,
    check_sn_distance_modulus,
    is_spot_check_enabled,
)


# ── SN distance modulus ─────────────────────────────────────────────


def test_sn_distance_modulus_pass_at_known_value():
    """SN 2007af 在 Pantheon+ 有 4 row, weighted mu=32.05 ± 0.17.
    claim 接近这个值 → pass."""
    result = check_sn_distance_modulus("2007af", 32.05, margin_sigma=3.0)
    assert result.status == "passed"
    assert result.passed is True
    assert result.our_value is not None
    assert abs(result.our_value - 32.0537) < 0.01
    assert result.paper_value == 32.05
    assert result.sigma_distance is not None
    assert result.sigma_distance < 3.0


def test_sn_distance_modulus_fail_when_far_off():
    """claim mu=40.0 跟 2007af 真实 mu=32.05 差 ~47σ → fail."""
    result = check_sn_distance_modulus("2007af", 40.0, margin_sigma=3.0)
    assert result.status == "failed"
    assert result.passed is False
    assert result.sigma_distance is not None
    assert result.sigma_distance > 3.0


def test_sn_distance_modulus_unavailable_for_unknown_sn():
    """不在 Pantheon+ 1543 unique SN 里的 → status=unavailable, 不当 fail."""
    result = check_sn_distance_modulus("unknown_sn_xyz", 32.0)
    assert result.status == "unavailable"
    assert result.passed is False
    assert "not found" in result.reason.lower()


def test_sn_distance_modulus_aggregates_multiple_survey_rows():
    """SN 2011fe 在 2 个 survey 里, weighted mu ≈ 29.03 (not just one row)."""
    result = check_sn_distance_modulus("2011fe", 29.03, margin_sigma=3.0)
    assert result.status == "passed"
    # source 里应该提到聚合了多 row
    assert "2 survey rows" in result.source


# ── CMB compressed parameter ────────────────────────────────────────


def test_cmb_H0_pass_at_planck_baseline():
    """H0=67.36 是 Planck 2018 baseline → pass."""
    result = check_cmb_compressed_value("H0", 67.36, margin_sigma=3.0)
    assert result.status == "passed"
    assert result.our_value == 67.36
    assert result.sigma_distance is not None
    assert result.sigma_distance < 0.01


def test_cmb_H0_fail_at_shoes_value():
    """SH0ES H0=73.04 跟 Planck baseline 67.36±0.54 差 ~10σ → fail.
    这正是 H0 张力的核心: 平台抓到 'AI 把 SH0ES H0 当作 Planck-baseline H0' 错用."""
    result = check_cmb_compressed_value("H0", 73.04, margin_sigma=3.0)
    assert result.status == "failed"
    assert result.passed is False
    assert result.sigma_distance is not None
    assert result.sigma_distance > 3.0


def test_cmb_param_aliases_normalized():
    """h_0 / Hubble 都应该归一化到 H0."""
    for alias in ["h0", "H_0", "Hubble", "hubble_constant"]:
        result = check_cmb_compressed_value(alias, 67.36)
        assert result.status == "passed", f"alias {alias!r} not recognized"


def test_cmb_omegam_pass_at_planck_baseline():
    result = check_cmb_compressed_value("omegam", 0.3153)
    assert result.status == "passed"


def test_cmb_unavailable_for_unsupported_param():
    """sigma_v / r_drag / etc. 都不在 MVP 支持范围内 → unavailable, 不 fail."""
    result = check_cmb_compressed_value("sigma_v", 1000.0)
    assert result.status == "unavailable"
    assert "not supported" in result.reason.lower()


# ── opt-in flag ─────────────────────────────────────────────────────


def test_is_spot_check_enabled_false_by_default():
    """默认不带 env flag → False, 保证不影响整体."""
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LITERATURE_SPOT_CHECK_ENABLED", None)
        assert is_spot_check_enabled() is False


def test_is_spot_check_enabled_when_set_true():
    with mock.patch.dict(os.environ, {"LITERATURE_SPOT_CHECK_ENABLED": "true"}):
        assert is_spot_check_enabled() is True


def test_is_spot_check_enabled_case_insensitive():
    with mock.patch.dict(os.environ, {"LITERATURE_SPOT_CHECK_ENABLED": "TRUE"}):
        assert is_spot_check_enabled() is True
    with mock.patch.dict(os.environ, {"LITERATURE_SPOT_CHECK_ENABLED": "True"}):
        assert is_spot_check_enabled() is True


def test_is_spot_check_enabled_anything_else_is_false():
    """非 'true' 值都视为关. 防止 '1' / 'yes' / 'enable' 这种意外开启."""
    for val in ["1", "yes", "on", "enabled", "false", ""]:
        with mock.patch.dict(os.environ, {"LITERATURE_SPOT_CHECK_ENABLED": val}):
            assert is_spot_check_enabled() is False, f"val={val!r} should not enable"


# ── Drift detection: literature_spot_check.py hardcoded baseline ────
# ── 必须跟 cosmology_likelihoods.py 一致 ────────────────────────────


def test_planck_baseline_consistent_with_cosmology_likelihoods():
    """literature_spot_check.PLANCK_2018_BASELINE 是 cosmology_likelihoods.py
    里 planck2018_compressed 数据集的 compressed_likelihood 的镜像.
    任何 drift 立即被这测试抓住, 避免两边数值不一致."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    spec = get_cosmology_dataset("planck2018_compressed").compressed_likelihood
    assert spec is not None
    params = list(spec.parameters)
    means = list(spec.mean)
    # 协方差对角线 → sigma
    sigmas = [spec.covariance[i][i] ** 0.5 for i in range(len(params))]

    for param, mean, sigma in zip(params, means, sigmas):
        assert param in PLANCK_2018_BASELINE, (
            f"PLANCK_2018_BASELINE missing param {param!r} that "
            f"cosmology_likelihoods declares"
        )
        baseline_mean, baseline_sigma = PLANCK_2018_BASELINE[param]
        assert abs(baseline_mean - mean) < 1e-6, (
            f"DRIFT: param {param!r} baseline mean {baseline_mean} != "
            f"cosmology_likelihoods mean {mean}"
        )
        assert abs(baseline_sigma - sigma) < 1e-6, (
            f"DRIFT: param {param!r} baseline sigma {baseline_sigma} != "
            f"cosmology_likelihoods sigma {sigma}"
        )
