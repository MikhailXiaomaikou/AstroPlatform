"""PART AI #2 — luminosity_units L_solar ↔ L_prime 转换正确性.

物理预期 (Carilli & Walter 2013 Eq. 1, Eq. 3):
    log10(L'/L_solar) = 10.495 - 3·log10(ν_rest_GHz) + 2·log10(1+z)

锁住三件事:
1. [CII] @ z=5: L_prime - L_solar ≈ +2.215 dex (用户 paper 关键场景)
2. 公式对 (1+z)² 缩放正确, 对 ν_rest⁻³ 缩放正确
3. 转换可逆 (L_solar → L_prime → L_solar 还原)
"""

from __future__ import annotations

import math

import pytest

from app.services.luminosity_units import (
    LINE_REST_FREQ_GHZ,
    convert_log_l_prime_to_l_solar,
    convert_log_l_solar_to_l_prime,
    convert_row_luminosity_inplace,
    lookup_line_rest_freq_ghz,
)


# ---------- ν_rest lookup ----------


def test_cii_rest_freq_is_1900_5369_ghz() -> None:
    """[CII] 158 μm 频率必须精确 1900.5369 GHz (NIST/SPLATALOGUE).
    错一位小数会让 fit intercept 偏 ~0.01 dex, 不能容忍."""
    assert math.isclose(lookup_line_rest_freq_ghz("[CII]"), 1900.5369, abs_tol=1e-4)
    assert lookup_line_rest_freq_ghz("[CII]158") == 1900.5369


def test_co_rest_freqs_present() -> None:
    """CO(1-0)..CO(7-6) 主旋转线必须在表内."""
    expected = {
        "CO(1-0)": 115.27120,
        "CO(2-1)": 230.53800,
        "CO(3-2)": 345.79599,
        "CO(7-6)": 806.65180,
    }
    for line, freq in expected.items():
        assert lookup_line_rest_freq_ghz(line) == pytest.approx(freq, abs=1e-3)


def test_unknown_line_returns_none() -> None:
    """未知 line label 返回 None — 防止静默 fallback 用错频率."""
    assert lookup_line_rest_freq_ghz("CII") is None  # 缺方括号
    assert lookup_line_rest_freq_ghz("nonexistent") is None
    assert lookup_line_rest_freq_ghz("") is None


# ---------- L_solar → L_prime 物理预期 ----------


def test_cii_at_z5_offset_is_about_plus_2_2_dex() -> None:
    """[CII] @ z=5 转换偏移应 ≈ +2.215 dex (推导于注释).

    用户 paper 报 ALPINE log L_CII/Lsun ~ 8.5; 转 L_prime 应 ~ 10.7.
    Bothwell SPT 报 log L'[CII] ~ 9.5-10 (low z, ~3-4 dex 较低 sample).
    """
    log_l_prime = convert_log_l_solar_to_l_prime(8.5, "[CII]", redshift=5.0)
    assert log_l_prime is not None
    # 预期 ≈ 8.5 + 2.215 = 10.715
    assert log_l_prime == pytest.approx(10.715, abs=0.01)


def test_cii_z_dependence_quadratic_in_one_plus_z() -> None:
    """偏移随 (1+z)² 缩放. z=1 → z=5, (1+z) 从 2 到 6, 2·log(6/2) = 2·log(3) ≈ +0.954 dex."""
    delta_z1 = convert_log_l_solar_to_l_prime(0.0, "[CII]", redshift=1.0)
    delta_z5 = convert_log_l_solar_to_l_prime(0.0, "[CII]", redshift=5.0)
    expected_diff = 2.0 * math.log10(6.0 / 2.0)  # 2 log(3) ≈ 0.954
    assert (delta_z5 - delta_z1) == pytest.approx(expected_diff, abs=1e-6)


def test_co10_at_z2_smaller_offset_than_cii_at_z2() -> None:
    """CO(1-0) ν_rest 比 [CII] 低 16x → -3 log ν 项贡献 +3·log(16.5) ≈ +3.65 dex 更大.
    所以同 z 下 CO(1-0) 的 L_prime 数值比 [CII] 大 (相对 L_solar)."""
    cii_z2 = convert_log_l_solar_to_l_prime(8.0, "[CII]", redshift=2.0)
    co_z2 = convert_log_l_solar_to_l_prime(8.0, "CO(1-0)", redshift=2.0)
    assert co_z2 > cii_z2  # CO(1-0) ν_rest 小, 偏移大


def test_explicit_nu_rest_override() -> None:
    """nu_rest_ghz 显式参数应优先于 line_id 查表."""
    # 用 [CII] line_id 但显式覆盖 ν_rest 为 100 GHz
    val_a = convert_log_l_solar_to_l_prime(8.0, "[CII]", redshift=2.0)
    val_b = convert_log_l_solar_to_l_prime(8.0, "[CII]", redshift=2.0, nu_rest_ghz=100.0)
    assert val_a != val_b


# ---------- 可逆性 ----------


@pytest.mark.parametrize("z", [0.5, 1.0, 2.5, 5.0, 7.0])
def test_round_trip_is_identity(z: float) -> None:
    """L_solar → L_prime → L_solar 必须无损还原."""
    original = 8.7
    forward = convert_log_l_solar_to_l_prime(original, "[CII]", redshift=z)
    back = convert_log_l_prime_to_l_solar(forward, "[CII]", redshift=z)
    assert back == pytest.approx(original, abs=1e-9)


# ---------- 错误路径 ----------


def test_missing_redshift_returns_none() -> None:
    """没 redshift 不能转换 (依赖 (1+z)² 项)."""
    assert convert_log_l_solar_to_l_prime(8.5, "[CII]", redshift=None) is None
    assert convert_log_l_solar_to_l_prime(8.5, "[CII]", redshift=float("nan")) is None
    assert convert_log_l_solar_to_l_prime(8.5, "[CII]", redshift=-0.5) is None


def test_missing_line_id_and_no_override_returns_none() -> None:
    """无 line_id 也无 nu_rest_ghz 显式参数 → None."""
    assert convert_log_l_solar_to_l_prime(8.5, line_id=None, redshift=2.0) is None
    assert convert_log_l_solar_to_l_prime(8.5, line_id="unknown", redshift=2.0) is None


def test_invalid_log_l_returns_none() -> None:
    assert convert_log_l_solar_to_l_prime(float("nan"), "[CII]", 5.0) is None
    assert convert_log_l_solar_to_l_prime(float("inf"), "[CII]", 5.0) is None


# ---------- row in-place 转换 ----------


def test_convert_row_l_solar_to_l_prime_records_audit_fields() -> None:
    """convert_row_luminosity_inplace 必须记录 transformed_from 旧值 +
    transformed_to 单位标记, 让 LLM/审稿人能看到哪行被转过."""
    row = {
        "source_name": "ALPINE-12345",
        "redshift": 5.0,
        "line_id": "[CII]",
        "log_luminosity": 8.5,
    }
    out = convert_row_luminosity_inplace(row, "L_prime")
    assert out["luminosity_kind"] == "L_prime"
    assert out["log_luminosity"] == pytest.approx(10.715, abs=0.01)
    assert out["log_luminosity_transformed_from"] == 8.5
    assert out["log_luminosity_transformed_to"] == "L_prime"
    # 原 row 不应被改 (保 immutable 风格)
    assert row["log_luminosity"] == 8.5
    assert "luminosity_kind" not in row


def test_convert_row_already_target_kind_is_noop() -> None:
    """已是 target_kind 的 row 不再转, 但补上 luminosity_kind 字段."""
    row = {"luminosity_kind": "L_prime", "log_luminosity": 10.7, "redshift": 5.0, "line_id": "[CII]"}
    out = convert_row_luminosity_inplace(row, "L_prime")
    assert out["log_luminosity"] == 10.7
    assert "log_luminosity_transformed_from" not in out


def test_convert_row_legacy_default_is_l_solar() -> None:
    """老 cache 行没 luminosity_kind → 视为 L_solar (现状), 转 L_prime 真转换."""
    row = {"log_luminosity": 8.5, "redshift": 5.0, "line_id": "[CII]"}
    # No luminosity_kind set
    out = convert_row_luminosity_inplace(row, "L_prime")
    assert out["luminosity_kind"] == "L_prime"
    assert out["log_luminosity"] == pytest.approx(10.715, abs=0.01)


def test_convert_row_missing_redshift_records_unit_error() -> None:
    """没 z → _unit_error 字段写明原因, 但 log_luminosity 不动让 caller reject."""
    row = {"log_luminosity": 8.5, "line_id": "[CII]"}  # no redshift
    out = convert_row_luminosity_inplace(row, "L_prime")
    assert "_unit_error" in out
    assert "redshift" in out["_unit_error"].lower()
    assert out["log_luminosity"] == 8.5  # untouched
    assert out.get("luminosity_kind") != "L_prime"


def test_convert_row_unknown_line_records_unit_error() -> None:
    row = {"log_luminosity": 8.5, "line_id": "MysteryLine", "redshift": 5.0}
    out = convert_row_luminosity_inplace(row, "L_prime")
    assert "_unit_error" in out
    assert "MysteryLine" in out["_unit_error"]


def test_line_rest_freq_dict_completeness() -> None:
    """LINE_REST_FREQ_GHZ 必须含 [CII] 主条目 + CO(1-0)..CO(7-6) 全套.
    防止哪天有人误删某条目."""
    required = ["[CII]", "[CII]158", "CO(1-0)", "CO(2-1)", "CO(3-2)",
                "CO(4-3)", "CO(5-4)", "CO(6-5)", "CO(7-6)"]
    for line in required:
        assert line in LINE_REST_FREQ_GHZ, f"missing required line {line!r}"
        assert LINE_REST_FREQ_GHZ[line] > 0
