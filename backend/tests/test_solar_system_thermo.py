"""Unit tests for solar_system_thermo service (M0 Commit 3).

物理验证: Harris 1998 diameter / NEATM (Mainzer+ 2011) subsolar T / 单波段 fit.
"""

from __future__ import annotations

import math

import pytest


# ── Harris 1998 diameter / albedo ──────────────────────────────────


def test_harris_diameter_ceres_matches_known():
    """Ceres: H=3.34, p_V=0.090 → D ≈ 940 km (Drummond+ 2014 actual: 939.4 km)."""
    from app.services.solar_system_thermo import harris_diameter_km

    D = harris_diameter_km(H=3.34, p_V=0.090)
    assert D == pytest.approx(940.0, abs=20.0), f"Ceres D = {D} km (期望 ~940)"


def test_harris_diameter_vesta_within_formula_precision():
    """Vesta: H=3.20, p_V=0.42 → Harris formula 给 ~470 km;实际 D (Dawn) ≈ 525 km.

    Harris 1998 formula 本身就有 ±10-15% 系统精度(Vesta 非球形 + 投影几何效应),
    所以容忍放宽到 ±80 km 覆盖 formula precision 范围。 实际 D 525 km 来自
    Russell+ 2013 Dawn 直接测量,不是 Harris formula 反推。
    """
    from app.services.solar_system_thermo import harris_diameter_km

    D = harris_diameter_km(H=3.20, p_V=0.42)
    # 公式精确值 ≈ 469 km,期望落在 [400, 600] km 区间
    assert 400 <= D <= 600, f"Vesta Harris D = {D} km (公式期望 ~470, 真实 525)"


def test_harris_diameter_eros_matches_NEAR_within_formula_precision():
    """433 Eros H=11.16 + p_V=0.25 → Harris formula D ≈ 16-17 km, NEAR 测量 ~16.8 km."""
    from app.services.solar_system_thermo import harris_diameter_km

    D = harris_diameter_km(H=11.16, p_V=0.25)
    assert D == pytest.approx(15.5, abs=3.0), f"Eros Harris D = {D} km"


def test_harris_albedo_round_trip():
    """harris_albedo(H, D) 是 harris_diameter_km 的逆解."""
    from app.services.solar_system_thermo import harris_albedo, harris_diameter_km

    H, pV = 14.6, 0.10
    D = harris_diameter_km(H, pV)
    pV_back = harris_albedo(H, D)
    assert pV_back == pytest.approx(pV, rel=1e-4)


def test_harris_invalid_albedo_raises():
    from app.services.solar_system_thermo import harris_diameter_km
    with pytest.raises(ValueError):
        harris_diameter_km(14.6, p_V=0.0)
    with pytest.raises(ValueError):
        harris_diameter_km(14.6, p_V=1.5)


# ── Phase integral / NEATM subsolar temperature ────────────────────


def test_phase_integral_bowell_at_typical_G():
    from app.services.solar_system_thermo import phase_integral_bowell
    # G=0.15 → q = 0.290 + 0.684 × 0.15 = 0.3926
    assert phase_integral_bowell(0.15) == pytest.approx(0.3926, abs=1e-3)
    assert phase_integral_bowell(0.0) == pytest.approx(0.290, abs=1e-3)


def test_neatm_subsolar_temp_neo_at_1au_typical_range():
    """1 au, p_V=0.20, G=0.15, η=1.4: T_ss 应该在 ~360-410 K (NEA 典型)."""
    from app.services.solar_system_thermo import neatm_subsolar_temperature

    T = neatm_subsolar_temperature(p_V=0.20, G=0.15, r_au=1.0, eta=1.4)
    assert 340 <= T <= 430, f"NEATM T_ss = {T} K (期望 ~360-410)"


def test_neatm_subsolar_temp_scales_with_r():
    """T_ss ∝ r^(-1/2)."""
    from app.services.solar_system_thermo import neatm_subsolar_temperature

    T_1au = neatm_subsolar_temperature(0.2, 0.15, 1.0, eta=1.4)
    T_4au = neatm_subsolar_temperature(0.2, 0.15, 4.0, eta=1.4)
    # √(r) factor of 2 → T 减半
    assert T_1au / T_4au == pytest.approx(2.0, rel=0.01)


def test_neatm_subsolar_temp_higher_albedo_cooler():
    """更高反照率 → 吸收少 → T_ss 低."""
    from app.services.solar_system_thermo import neatm_subsolar_temperature

    T_low_alb = neatm_subsolar_temperature(0.05, 0.15, 1.5, eta=1.4)
    T_high_alb = neatm_subsolar_temperature(0.50, 0.15, 1.5, eta=1.4)
    assert T_high_alb < T_low_alb


def test_neatm_invalid_params_raise():
    from app.services.solar_system_thermo import neatm_subsolar_temperature
    with pytest.raises(ValueError):
        neatm_subsolar_temperature(p_V=0.0, G=0.15, r_au=1.0)
    with pytest.raises(ValueError):
        neatm_subsolar_temperature(0.2, 0.15, r_au=0)
    with pytest.raises(ValueError):
        neatm_subsolar_temperature(0.2, 0.15, 1.0, eta=0)


# ── Planck spectral radiance ───────────────────────────────────────


def test_planck_at_300K_10um_reasonable():
    """B_λ(300K, 10μm) ≈ 1e7 W/m²/m/sr — IR thermal radiation."""
    from app.services.solar_system_thermo import planck_spectral_radiance

    B = planck_spectral_radiance(T=300.0, lambda_um=10.0)
    # Wien's law max at λ=9.66 μm for 300 K, so 10 μm 接近 peak
    assert 5e6 <= B <= 1e8, f"B_λ at 300 K, 10 μm = {B}"


def test_planck_higher_T_higher_radiance():
    from app.services.solar_system_thermo import planck_spectral_radiance
    B1 = planck_spectral_radiance(200.0, 10.0)
    B2 = planck_spectral_radiance(400.0, 10.0)
    assert B2 > B1


def test_planck_invalid_input_raises():
    from app.services.solar_system_thermo import planck_spectral_radiance
    with pytest.raises(ValueError):
        planck_spectral_radiance(0, 10.0)
    with pytest.raises(ValueError):
        planck_spectral_radiance(300, 0)


# ── NEATM single-band fit ──────────────────────────────────────────


def test_neatm_fit_recovers_input_for_synthetic():
    """正向预测 + 反向 fit:对同一 (H, D, p_V) 设置 should recover p_V."""
    from app.services.solar_system_thermo import (
        fit_diameter_albedo_neatm, harris_diameter_km,
        neatm_thermal_flux_density,
    )

    H_true, p_V_true, G = 17.0, 0.10, 0.15
    r_au, delta_au, lam_um = 1.5, 1.0, 12.0  # WISE W3 band
    D_true = harris_diameter_km(H_true, p_V_true)
    F_true = neatm_thermal_flux_density(
        D_true, p_V_true, G, r_au, delta_au, lam_um, eta=1.4,
    )

    result = fit_diameter_albedo_neatm(
        H=H_true, observed_flux_jy=F_true, lambda_um=lam_um,
        r_au=r_au, delta_au=delta_au, G=G, eta=1.4,
    )
    # 应该 recover p_V_true 和 D_true
    assert result["albedo_pV"] == pytest.approx(p_V_true, rel=0.05)
    assert result["diameter_km"] == pytest.approx(D_true, rel=0.05)
    assert abs(result["residual_dex"]) < 0.05  # < 0.05 dex 残差


def test_neatm_fit_returns_diagnostics_dict():
    from app.services.solar_system_thermo import fit_diameter_albedo_neatm

    result = fit_diameter_albedo_neatm(
        H=18.0, observed_flux_jy=0.05, lambda_um=12.0,
        r_au=1.2, delta_au=0.8,
    )
    expected_keys = {
        "diameter_km", "albedo_pV", "subsolar_temperature_K",
        "beaming_parameter_eta", "predicted_flux_jy", "observed_flux_jy",
        "residual_dex",
    }
    assert expected_keys.issubset(set(result.keys()))
    assert result["diameter_km"] > 0
    assert 0 < result["albedo_pV"] < 1
