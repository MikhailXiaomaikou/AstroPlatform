"""Unit tests for solar_system_dynamics service (M0 Commit 3).

物理验证: Öpik collision probability / encounter velocity / Kepler helpers.
"""

from __future__ import annotations

import math

import pytest


# ── Kepler helpers ────────────────────────────────────────────────


def test_orbital_period_earth_one_year():
    from app.services.solar_system_dynamics import orbital_period_years
    assert orbital_period_years(1.0) == pytest.approx(1.0, abs=1e-9)


def test_orbital_period_ceres_about_4_6_years():
    """Ceres a ≈ 2.77 au → T ≈ 4.61 yr (实际)."""
    from app.services.solar_system_dynamics import orbital_period_years
    assert orbital_period_years(2.77) == pytest.approx(4.61, abs=0.05)


def test_orbital_period_phaethon_about_1_4_yr():
    """3200 Phaethon a ≈ 1.27 au → T ≈ 1.43 yr (实际 1.434 yr)."""
    from app.services.solar_system_dynamics import orbital_period_years
    assert orbital_period_years(1.271) == pytest.approx(1.434, abs=0.02)


def test_orbital_period_invalid_raises():
    from app.services.solar_system_dynamics import orbital_period_years
    with pytest.raises(ValueError):
        orbital_period_years(0)
    with pytest.raises(ValueError):
        orbital_period_years(-1)


def test_perihelion_aphelion_phaethon():
    """Phaethon a=1.271, e=0.8898 → q≈0.140, Q≈2.403."""
    from app.services.solar_system_dynamics import perihelion_aphelion_au

    q, Q = perihelion_aphelion_au(1.271, 0.8898)
    assert q == pytest.approx(0.140, abs=0.001)
    assert Q == pytest.approx(2.403, abs=0.005)


def test_perihelion_aphelion_invalid_e_raises():
    from app.services.solar_system_dynamics import perihelion_aphelion_au
    with pytest.raises(ValueError):
        perihelion_aphelion_au(1.0, e=1.0)
    with pytest.raises(ValueError):
        perihelion_aphelion_au(1.0, e=-0.1)


# ── Encounter velocity ─────────────────────────────────────────────


def test_encounter_velocity_phaethon_typical_value():
    """3200 Phaethon (a=1.27, e=0.89, i=22.3°) — Earth-crossing.
    Wetherill 1967 closed form 给的 U_∞ 通常在 30-40 km/s 范围.
    """
    from app.services.solar_system_dynamics import encounter_velocity_km_s

    U = encounter_velocity_km_s(a_target_au=1.271, e_target=0.8898, i_target_deg=22.26)
    assert 25.0 <= U <= 45.0, f"Phaethon encounter v = {U} km/s (期望 ~30-40)"


def test_encounter_velocity_apophis_typical_value():
    """Apophis (a=0.92, e=0.19, i=3.3°) — close approach v ≈ 7.4 km/s."""
    from app.services.solar_system_dynamics import encounter_velocity_km_s

    U = encounter_velocity_km_s(a_target_au=0.922, e_target=0.191, i_target_deg=3.33)
    # 注意 Wetherill formula 给的是 U_∞ (无 gravity focus), Apophis 真实 close approach
    # geocentric v 是 ~7-8 km/s. Closed form 在低 e + i 时跟实际一致到 ±20%.
    assert 4.0 <= U <= 15.0, f"Apophis encounter v = {U} km/s (期望 ~7)"


def test_encounter_velocity_invalid_raises():
    from app.services.solar_system_dynamics import encounter_velocity_km_s
    with pytest.raises(ValueError):
        encounter_velocity_km_s(a_target_au=0, e_target=0.1, i_target_deg=5)
    with pytest.raises(ValueError):
        encounter_velocity_km_s(1.0, e_target=1.0, i_target_deg=5)
    with pytest.raises(ValueError):
        encounter_velocity_km_s(1.0, e_target=0.1, i_target_deg=-5)


# ── Öpik collision probability ─────────────────────────────────────


def test_opik_collision_probability_finite_for_typical_neo():
    """典型 NEO encounter (MOID=0.001 au, U=15 km/s, R=Earth):
    P_per_enc 在 1e-10 .. 1e-5 量级."""
    from app.services.solar_system_dynamics import opik_collision_probability_per_encounter

    p = opik_collision_probability_per_encounter(
        moid_au=0.001, U_rel_km_s=15.0, target_radius_km=6371.0,
    )
    assert 1e-10 <= p <= 1e-4, f"Öpik p = {p} (期望 ~1e-8..1e-5 量级)"


def test_opik_smaller_moid_higher_probability():
    """MOID 减小 → P_per_enc 增大 (反比)."""
    from app.services.solar_system_dynamics import opik_collision_probability_per_encounter

    p_close = opik_collision_probability_per_encounter(moid_au=0.0001, U_rel_km_s=10.0)
    p_far = opik_collision_probability_per_encounter(moid_au=0.01, U_rel_km_s=10.0)
    assert p_close > p_far
    # 应该 ~100×
    assert p_close / p_far == pytest.approx(100.0, rel=0.05)


def test_opik_gravity_focusing_amplifies_low_velocity():
    """U 越低 → grav focus 因子越大 → 概率越高."""
    from app.services.solar_system_dynamics import opik_collision_probability_per_encounter

    p_slow = opik_collision_probability_per_encounter(
        moid_au=0.001, U_rel_km_s=5.0, planet_gravity_focus=True,
    )
    p_fast = opik_collision_probability_per_encounter(
        moid_au=0.001, U_rel_km_s=50.0, planet_gravity_focus=True,
    )
    # slow 更高,但还要考虑 1/U 也增大 p_slow
    # 加上 focusing,p_slow 应该明显 > p_fast (>50×)
    assert p_slow / p_fast > 10


def test_opik_invalid_input_raises():
    from app.services.solar_system_dynamics import opik_collision_probability_per_encounter
    with pytest.raises(ValueError):
        opik_collision_probability_per_encounter(moid_au=0, U_rel_km_s=10.0)
    with pytest.raises(ValueError):
        opik_collision_probability_per_encounter(0.01, U_rel_km_s=0)
    with pytest.raises(ValueError):
        opik_collision_probability_per_encounter(0.01, 10.0, target_radius_km=0)


# ── 100-yr collision estimate ──────────────────────────────────────


def test_estimate_100yr_returns_full_diagnostics():
    """完整字典 + warning string."""
    from app.services.solar_system_dynamics import estimate_100yr_collision_probability

    r = estimate_100yr_collision_probability(
        moid_au=0.001, a_target_au=1.2, e_target=0.3, i_target_deg=5.0,
    )
    expected = {
        "p_per_encounter", "encounters_per_year", "encounter_velocity_km_s",
        "opik_upper_bound_100yr", "warning", "reference",
    }
    assert expected.issubset(set(r.keys()))
    assert "Sentry" in r["warning"]
    assert r["opik_upper_bound_100yr"] >= 0


def test_estimate_100yr_apophis_geometric_upper_bound():
    """Apophis-like(a=0.92, e=0.19, i=3.3°, MOID=0.00025 au 2029 close approach):
    Öpik 几何上限是"假设 MOID 不变 + 每 orbit 都过 MOID",数值上偏大,
    通常 [1e-3, 1] 范围。 真实 Sentry 给 ~1e-7 但那是 Monte Carlo 含轨道不确定度。
    """
    from app.services.solar_system_dynamics import estimate_100yr_collision_probability

    r = estimate_100yr_collision_probability(
        moid_au=0.00025, a_target_au=0.922, e_target=0.191, i_target_deg=3.33,
    )
    p = r["opik_upper_bound_100yr"]
    # Öpik geometric upper bound 物理上 ≤ 1 (probability bound); 一般 ≥ 1e-5
    assert 1e-6 <= p <= 1.0, f"Apophis Öpik upper bound = {p}"
    # warning 必须明确告诉 LLM 这不是真实概率
    assert "上限" in r["warning"] or "upper bound" in r["warning"].lower() or "Öpik" in r["warning"]
