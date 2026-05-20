"""Unit tests for solar_system_phot service (M0 Commit 3).

物理验证: HG phase function (Bowell+ 1989) / Afρ (A'Hearn+ 1984) / light-time.
"""

from __future__ import annotations

import math

import pytest


# ── H-G phase function ─────────────────────────────────────────────


def test_hg_phase_function_unity_at_opposition():
    """α=0° → Φ=1 (full phase, no diminution)."""
    from app.services.solar_system_phot import hg_phase_function

    assert hg_phase_function(0.0, G=0.15) == pytest.approx(1.0, abs=1e-6)


def test_hg_phase_function_decreases_with_phase_angle():
    """Φ 单调递减 from 0° → 180°."""
    from app.services.solar_system_phot import hg_phase_function

    angles = [0, 10, 30, 60, 90, 120, 150]
    values = [hg_phase_function(a, G=0.15) for a in angles]
    for i in range(len(values) - 1):
        assert values[i] > values[i + 1], (
            f"Φ should decrease: Φ({angles[i]})={values[i]} ≤ Φ({angles[i+1]})={values[i+1]}"
        )


def test_hg_reduced_magnitude_at_opposition_equals_H():
    """α=0° 时 H(α=0) = H."""
    from app.services.solar_system_phot import hg_reduced_magnitude

    assert hg_reduced_magnitude(H=14.6, G=0.15, alpha_deg=0.0) == pytest.approx(14.6, abs=1e-5)


def test_hg_apparent_magnitude_at_1au_opposition_equals_H():
    """r=Δ=1, α=0° → V = H + 5 log10(1×1) = H."""
    from app.services.solar_system_phot import hg_apparent_magnitude

    assert hg_apparent_magnitude(H=14.6, G=0.15, alpha_deg=0.0, r_au=1.0, delta_au=1.0) == pytest.approx(14.6, abs=1e-5)


def test_hg_apparent_magnitude_distance_dimming():
    """r=2, Δ=2, α=0 → V = H + 5 log10(4) ≈ H + 3.01."""
    from app.services.solar_system_phot import hg_apparent_magnitude

    V = hg_apparent_magnitude(H=10.0, G=0.15, alpha_deg=0.0, r_au=2.0, delta_au=2.0)
    expected = 10.0 + 5.0 * math.log10(4.0)
    assert V == pytest.approx(expected, abs=1e-5)


def test_hg_phase_function_invalid_angle_raises():
    from app.services.solar_system_phot import hg_phase_function
    with pytest.raises(ValueError):
        hg_phase_function(-5.0)
    with pytest.raises(ValueError):
        hg_phase_function(190.0)


def test_hg_apparent_magnitude_invalid_distance_raises():
    from app.services.solar_system_phot import hg_apparent_magnitude
    with pytest.raises(ValueError):
        hg_apparent_magnitude(14.6, 0.15, 30, r_au=0, delta_au=1.0)
    with pytest.raises(ValueError):
        hg_apparent_magnitude(14.6, 0.15, 30, r_au=1.0, delta_au=-1)


def test_hg_curve_vectorised_consistency():
    """curve helper 等价于循环."""
    from app.services.solar_system_phot import (
        hg_apparent_magnitude, hg_apparent_magnitude_curve,
    )

    alphas = [0.0, 30.0, 60.0]
    rs = [1.0, 1.5, 2.0]
    deltas = [1.0, 1.2, 1.5]
    curve = hg_apparent_magnitude_curve(14.6, 0.15, alphas, rs, deltas)
    expected = [
        hg_apparent_magnitude(14.6, 0.15, a, r, d)
        for a, r, d in zip(alphas, rs, deltas)
    ]
    assert curve == pytest.approx(expected, abs=1e-6)


# ── Light-time correction ──────────────────────────────────────────


def test_light_time_one_au_is_about_500s():
    """Δ=1 au → light time ≈ 499 s ≈ 8.32 min."""
    from app.services.solar_system_phot import light_time_minutes, light_time_seconds

    assert light_time_minutes(1.0) == pytest.approx(8.317, abs=0.005)
    assert light_time_seconds(1.0) == pytest.approx(499.0, abs=1.0)


def test_light_time_scales_linearly():
    from app.services.solar_system_phot import light_time_minutes
    assert light_time_minutes(2.0) == pytest.approx(2 * light_time_minutes(1.0), abs=1e-6)


def test_light_time_negative_raises():
    from app.services.solar_system_phot import light_time_minutes
    with pytest.raises(ValueError):
        light_time_minutes(-0.1)


# ── Afρ (A'Hearn+ 1984) ────────────────────────────────────────────


def test_afrho_67P_like_comet_in_typical_range():
    """67P-like comet at perihelion: order-of-magnitude check ∈ [10, 10000] cm.

    r=1.24 au, Δ=2.7 au, aperture=5", m_V_comet=12 (在合理 active 范围).
    """
    from app.services.solar_system_phot import afrho_cm

    af = afrho_cm(m_comet=12.0, r_au=1.24, delta_au=2.7, aperture_arcsec=5.0)
    assert 10 <= af <= 10000, f"Afρ out of typical order: {af} cm"


def test_afrho_brighter_comet_higher_value():
    """Comet 越亮 → Afρ 越大 (单调)."""
    from app.services.solar_system_phot import afrho_cm

    af_bright = afrho_cm(m_comet=10.0, r_au=1.0, delta_au=1.0, aperture_arcsec=5.0)
    af_faint = afrho_cm(m_comet=15.0, r_au=1.0, delta_au=1.0, aperture_arcsec=5.0)
    assert af_bright > af_faint


def test_afrho_larger_aperture_smaller_value():
    """ρ 单调反比关系."""
    from app.services.solar_system_phot import afrho_cm

    af_small_ap = afrho_cm(12.0, 1.5, 1.0, aperture_arcsec=2.0)
    af_large_ap = afrho_cm(12.0, 1.5, 1.0, aperture_arcsec=10.0)
    # Afρ ∝ 1/ρ → 大孔径 → 小 Afρ
    assert af_small_ap > af_large_ap
    assert af_small_ap / af_large_ap == pytest.approx(5.0, rel=0.01)  # 比例 = 10/2 = 5


def test_afrho_invalid_input_raises():
    from app.services.solar_system_phot import afrho_cm
    with pytest.raises(ValueError):
        afrho_cm(12.0, r_au=-1, delta_au=1.0, aperture_arcsec=5.0)
    with pytest.raises(ValueError):
        afrho_cm(12.0, r_au=1, delta_au=0, aperture_arcsec=5.0)
    with pytest.raises(ValueError):
        afrho_cm(12.0, r_au=1, delta_au=1, aperture_arcsec=0)


def test_afrho_phase_corrected_at_zero_equals_input():
    """α=0 时 phase factor=1, corrected 等于输入."""
    from app.services.solar_system_phot import afrho_phase_corrected

    val = afrho_phase_corrected(500.0, alpha_deg=0.0)
    assert val == pytest.approx(500.0, rel=0.02)  # phase_HalleyMarcus(0)=1 ± 2%


def test_afrho_phase_corrected_increases_with_phase():
    """非零 α 时 phase factor < 1 → corrected > input."""
    from app.services.solar_system_phot import afrho_phase_corrected

    val_0 = afrho_phase_corrected(500.0, alpha_deg=0.0)
    val_60 = afrho_phase_corrected(500.0, alpha_deg=60.0)
    assert val_60 > val_0
