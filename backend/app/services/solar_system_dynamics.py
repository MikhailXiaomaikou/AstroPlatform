"""Solar-system small-body dynamics — Öpik-style NEO collision probability +
keplerian helpers. **简化估计**, 精确分析必须 query JPL CNEOS Sentry-II API.

References (cite when using in published work):
- **Öpik 1951** Proc. R. Irish Acad. 54A, 165 — Earth-crossing collision rate.
- **Wetherill 1967** J. Geophys. Res. 72, 2429 — formal Öpik update.
- **Morbidelli+ 2002** Icarus 158, 329 (bibcode 2002Icar..158..329M) —
  NEO population + collision rate modernised treatment.
- **Bottke+ 1994** Icarus 107, 255 — Öpik formalism for orbit-crossing collision
  probability per encounter geometry.
- **Vereš+ 2017** Icarus 296, 139 — Yarkovsky drift detection from astrometry.

M0 Commit 3 (2026-05-18). Closed-form Öpik 估计 + MOID 计算助手。 不调 REBOUND
(留 M2). 精确 100-yr impact monitoring 走 cneos.jpl.nasa.gov/sentry 而非本模块。
"""

from __future__ import annotations

import math

# 物理常数
GAUSS_GRAV = 0.01720209895          # k, AU^(3/2) / day
AU_KM = 1.495978707e8               # km
EARTH_RADIUS_AU = 4.2635e-5         # 6371 km
EARTH_GRAV_PARAMETER_GMS_AU = 8.887692e-10  # G M_Earth in au³/day² (太阳引力单位下)
EARTH_ORBITAL_RADIUS_AU = 1.0       # 简化


def opik_collision_probability_per_encounter(
    moid_au: float, U_rel_km_s: float,
    target_radius_km: float = 6371.0, planet_gravity_focus: bool = True,
) -> float:
    """Öpik 单次轨道相遇 collision probability (per encounter).

    简化 Öpik 1951 + Wetherill 1967 closed-form:
        P_coll ≈ R_eff² / (2π × MOID × U_rel × T_orb)  [per orbital period]
    使用 gravitationally focused cross-section R_eff² = R² × (1 + (v_esc/U)²).

    参数:
        moid_au — Minimum Orbital Intersection Distance (au)
        U_rel_km_s — relative encounter velocity (km/s) at infinity
        target_radius_km — target physical radius (default Earth 6371 km)
        planet_gravity_focus — 是否应用 gravitational focusing

    返回 per-encounter collision probability (dimensionless),通常 1e-10..1e-6 范围.

    **注**: 这是 order-of-magnitude 估计. 真正的 NEA 风险评估必须 query
    JPL CNEOS Sentry-II,它做 Monte Carlo 完整 100-yr 推演含 Yarkovsky.
    """
    if moid_au <= 0:
        raise ValueError(f"moid_au must be positive: {moid_au}")
    if U_rel_km_s <= 0:
        raise ValueError(f"U_rel_km_s must be positive: {U_rel_km_s}")
    if target_radius_km <= 0:
        raise ValueError(f"target_radius_km must be positive: {target_radius_km}")
    # Convert target radius to au
    R_au = target_radius_km / AU_KM
    # Gravitational focusing factor
    if planet_gravity_focus and target_radius_km == 6371.0:
        # Earth: v_esc = 11.186 km/s
        v_esc_km_s = 11.186
        focus = 1.0 + (v_esc_km_s / U_rel_km_s) ** 2
    else:
        focus = 1.0
    R_eff_au = R_au * math.sqrt(focus)
    # Convert U_rel to au/day for consistency
    U_au_per_day = U_rel_km_s / AU_KM * 86400.0
    # Closed-form Öpik estimate (per encounter)
    return (R_eff_au ** 2) / (2.0 * math.pi * moid_au * U_au_per_day)


def encounter_velocity_au_per_day(
    a_target_au: float, e_target: float, i_target_deg: float,
    a_earth_au: float = EARTH_ORBITAL_RADIUS_AU,
) -> float:
    """Approximate relative encounter velocity (au/day) at Earth crossing.

    Wetherill 1967 + Öpik 1951 — 用 Tisserand parameter T_E 反推:
        U² = 3 - T_E      (units of v_E²)
        T_E = a_E/a + 2 cos(i) √(a(1-e²)/a_E)
    返回 |U_∞| in au/day。

    简化版:Earth 圆轨道 + target keplerian。 真实 close-approach 速度还要
    叠加 gravitational focusing(opik_collision_probability_per_encounter 中处理)。
    """
    if a_target_au <= 0:
        raise ValueError(f"a_target_au must be positive: {a_target_au}")
    if not (0 <= e_target < 1):
        raise ValueError(f"e_target must be ∈ [0, 1): {e_target}")
    if not (0 <= i_target_deg <= 180):
        raise ValueError(f"i_target_deg must be ∈ [0, 180]: {i_target_deg}")
    cos_i = math.cos(math.radians(i_target_deg))
    sqrt_term = math.sqrt(max(1 - e_target ** 2, 0.0))
    # Tisserand parameter w.r.t. Earth (a_earth=1 au)
    T_E = a_earth_au / a_target_au + 2.0 * cos_i * sqrt_term * math.sqrt(
        a_target_au / a_earth_au
    )
    inner = 3.0 - T_E
    U_norm = math.sqrt(max(inner, 0.0))  # 单位是 v_E
    v_earth_au_per_day = 2 * math.pi / 365.25  # ≈ 0.01721 au/day
    return U_norm * v_earth_au_per_day


def encounter_velocity_km_s(
    a_target_au: float, e_target: float, i_target_deg: float,
) -> float:
    """同上,返回 km/s。"""
    U_au_per_day = encounter_velocity_au_per_day(a_target_au, e_target, i_target_deg)
    return U_au_per_day * AU_KM / 86400.0


def estimate_100yr_collision_probability(
    moid_au: float, a_target_au: float, e_target: float,
    i_target_deg: float, target_radius_km: float = 6371.0,
) -> dict[str, float]:
    """**Öpik 几何上限**: 100 年累计 (encounter rate × 截面) — 不是真实 impact 概率。

    计算逻辑:
        opik_upper_bound_100yr ≈ P_per_encounter × n_encounters/yr × 100

    其中 P_per_encounter = R_eff² / (2π × MOID × U) 是 Öpik flux × 截面 比值。

    **关键警告**: 该函数输出的是"假设 MOID 在 100 年内不变 + asteroid 每 orbit
    都过该 MOID 点"的几何上限。 真实 NEA impact 概率(如 JPL CNEOS Sentry-II
    给的)通常比这个上限 **小 10⁴–10⁶ 倍**, 因为:
      - 每次 encounter 后 orbit 被 Earth 摄动,后续 MOID 变化
      - 观测有不确定度,asteroid 位置在 MOID 处的概率不是 1
      - Yarkovsky drift 改变长期 trajectory
      - keyhole 等共振几何

    任何 publication-grade NEO 风险评估必须用 Sentry-II 或 ESA NEODyS CLOMON2
    的 Monte Carlo 结果,**不要**直接 cite 本函数返回值作为 impact probability。
    """
    P = a_target_au ** 1.5
    n_encounters_per_year = 1.0 / P
    U_km_s = encounter_velocity_km_s(a_target_au, e_target, i_target_deg)
    if U_km_s <= 0:
        # 非 Earth-crossing 轨道(Tisserand T_E ≥ 3),没有 encounter
        return {
            "p_per_encounter": 0.0,
            "encounters_per_year": 0.0,
            "encounter_velocity_km_s": 0.0,
            "opik_upper_bound_100yr": 0.0,
            "warning": "Non-Earth-crossing orbit (Tisserand T_E ≥ 3); 不发生 close approach.",
            "reference": (
                "Öpik 1951 PRIA 54A, 165; Wetherill 1967 JGR 72, 2429."
            ),
        }
    p_per_encounter = opik_collision_probability_per_encounter(
        moid_au, U_km_s, target_radius_km=target_radius_km,
    )
    opik_100yr = p_per_encounter * n_encounters_per_year * 100.0
    return {
        "p_per_encounter": p_per_encounter,
        "encounters_per_year": n_encounters_per_year,
        "encounter_velocity_km_s": U_km_s,
        "opik_upper_bound_100yr": opik_100yr,
        "warning": (
            "Öpik 几何上限,不是实际 impact 概率。 真实 100-yr 概率从 Sentry-II "
            "通常 ×10⁻⁴..×10⁻⁶ 小(orbit uncertainty + Yarkovsky + keyhole). "
            "Publication-grade 必查 https://cneos.jpl.nasa.gov/sentry/."
        ),
        "reference": (
            "Öpik 1951 PRIA 54A, 165; Wetherill 1967 JGR 72, 2429; "
            "Morbidelli+ 2002 Icarus 158, 329 (2002Icar..158..329M)"
        ),
    }


# ── Kepler helpers ────────────────────────────────────────────────


def orbital_period_years(a_au: float) -> float:
    """Kepler 第三定律: T (yr) = a_au^(3/2)."""
    if a_au <= 0:
        raise ValueError(f"a_au must be positive: {a_au}")
    return a_au ** 1.5


def perihelion_aphelion_au(a_au: float, e: float) -> tuple[float, float]:
    """q = a(1-e), Q = a(1+e)."""
    if a_au <= 0:
        raise ValueError(f"a_au must be positive: {a_au}")
    if not (0 <= e < 1):
        raise ValueError(f"e must be ∈ [0, 1): {e}")
    return a_au * (1 - e), a_au * (1 + e)
