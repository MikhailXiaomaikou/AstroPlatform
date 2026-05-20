"""Solar-system thermal modelling (NEATM + STM + Harris diameter).

References (cite when using in published work):
- **Harris 1998** Icarus 131, 291 (bibcode 1998Icar..131..291H) —
  Near-Earth Asteroid Thermal Model (NEATM) 原始论文 + diameter/albedo formula.
- **Lebofsky+ 1986** Icarus 68, 239 (bibcode 1986Icar...68..239L) —
  Standard Thermal Model (STM).
- **Mainzer+ 2011** ApJ 743, 156 (bibcode 2011ApJ...743..156M) —
  NEOWISE 多波段 NEATM 拟合 + beaming parameter η 校准.
- **Pravec & Harris 2007** Icarus 190, 250 — η 默认值统计 (NEAs ⟨η⟩ ≈ 1.4).

M0 Commit 3 (2026-05-18). 单波段简化 fit;多波段 + 自洽 η 留 M2.
"""

from __future__ import annotations

import math

# 物理常数
STEFAN_BOLTZMANN = 5.670374419e-8       # W m⁻² K⁻⁴
SOLAR_CONSTANT_1AU = 1361.0             # W m⁻² (Kopp & Lean 2011)
PLANCK_H = 6.62607015e-34               # J s
SPEED_OF_LIGHT = 2.99792458e8           # m s⁻¹
BOLTZMANN = 1.380649e-23                # J K⁻¹

DEFAULT_EMISSIVITY = 0.9                # ε, NEATM 标准取值
DEFAULT_BEAMING_NEA = 1.4               # η for NEAs (Pravec & Harris 2007)
DEFAULT_BEAMING_MBA = 0.756             # η for MBAs (Mainzer+ 2011)

# Harris diameter constant (km, V band): D = K / sqrt(p_V) × 10^(-H/5)
HARRIS_DIAMETER_CONSTANT_KM = 1329.0    # Harris 1998 eq. 1


def harris_diameter_km(H: float, p_V: float) -> float:
    """Harris 1998 公式: D (km) = 1329 / √p_V × 10^(-H/5).

    用于已知 H_V (绝对星等) 和几何反照率 p_V 时,估直径。
    若已知 D 和 H,可反推 p_V。
    """
    if p_V <= 0 or p_V > 1.0:
        raise ValueError(f"p_V out of (0, 1]: {p_V}")
    return HARRIS_DIAMETER_CONSTANT_KM / math.sqrt(p_V) * 10 ** (-H / 5.0)


def harris_albedo(H: float, D_km: float) -> float:
    """Harris 1998 逆解: p_V = (1329 / D / 10^(H/5))²."""
    if D_km <= 0:
        raise ValueError(f"D_km must be positive: {D_km}")
    ratio = HARRIS_DIAMETER_CONSTANT_KM / D_km * 10 ** (-H / 5.0)
    return ratio * ratio


def phase_integral_bowell(G: float = 0.15) -> float:
    """Bowell+ 1989 phase integral: q = 0.290 + 0.684 × G。

    用于 NEATM 中 (1 - A) Bond albedo 与 p_V 的转换: A = p_V × q.
    """
    return 0.290 + 0.684 * G


def neatm_subsolar_temperature(
    p_V: float, G: float, r_au: float,
    eta: float = DEFAULT_BEAMING_NEA, epsilon: float = DEFAULT_EMISSIVITY,
) -> float:
    """NEATM subsolar temperature T_ss (K).

    T_ss = ((1 - A_Bond) × S_⊙ / (η × ε × σ × r_au²))^(1/4)
    A_Bond = p_V × q(G), q = 0.290 + 0.684 G  (Bowell+ 1989)

    参数:
        p_V — 几何反照率 (V band)
        G — phase slope (Bowell+ 1989)
        r_au — 日心距 (au)
        eta — beaming parameter (默认 1.4 for NEA, Pravec & Harris 2007)
        epsilon — 红外发射率 (默认 0.9)
    """
    if p_V <= 0 or p_V > 1.0:
        raise ValueError(f"p_V out of (0, 1]: {p_V}")
    if r_au <= 0:
        raise ValueError(f"r_au must be positive: {r_au}")
    if eta <= 0:
        raise ValueError(f"eta must be positive: {eta}")
    q = phase_integral_bowell(G)
    A_bond = p_V * q
    if A_bond >= 1.0:
        raise ValueError(f"Bond albedo ≥ 1 (p_V={p_V}, G={G}); 非物理 NEATM")
    flux_helio = SOLAR_CONSTANT_1AU / (r_au ** 2)
    return ((1 - A_bond) * flux_helio / (eta * epsilon * STEFAN_BOLTZMANN)) ** 0.25


def planck_spectral_radiance(T: float, lambda_um: float) -> float:
    """B_λ(T) — Planck 谱辐射 (W m⁻² m⁻¹ sr⁻¹)."""
    if T <= 0 or lambda_um <= 0:
        raise ValueError(f"T, lambda_um must be positive: {T}, {lambda_um}")
    lam_m = lambda_um * 1e-6
    x = PLANCK_H * SPEED_OF_LIGHT / (lam_m * BOLTZMANN * T)
    # 避免 expm1 溢出
    if x > 500:
        denom = float("inf")
    else:
        denom = math.expm1(x)
    if denom <= 0 or not math.isfinite(denom):
        return 0.0
    numerator = 2.0 * PLANCK_H * (SPEED_OF_LIGHT ** 2) / (lam_m ** 5)
    return numerator / denom


def neatm_thermal_flux_density(
    D_km: float, p_V: float, G: float, r_au: float, delta_au: float,
    lambda_um: float, eta: float = DEFAULT_BEAMING_NEA,
    epsilon: float = DEFAULT_EMISSIVITY,
) -> float:
    """NEATM 预测的热辐射 flux density at 波长 λ (Jy).

    简化版: 在 phase angle α=0° (opposition) + 半球积分近似下,有
        F_λ = (π R² / Δ²) × ε × B_λ(T_eff)
    其中 T_eff ≈ T_ss × 0.5^0.25 ≈ T_ss × 0.84 (半球平均).
    对于 publication-grade 多波段 fit,使用 sbpy 完整实现或 Mainzer+ 2011 NEATM
    代码,M0 这里只做单波段粗估。

    返回 Jy (= 1e-26 W m⁻² Hz⁻¹).
    """
    if D_km <= 0 or delta_au <= 0 or lambda_um <= 0:
        raise ValueError(
            f"D_km, delta_au, lambda_um must be positive: "
            f"{D_km}, {delta_au}, {lambda_um}"
        )
    T_ss = neatm_subsolar_temperature(p_V, G, r_au, eta=eta, epsilon=epsilon)
    # 半球平均温度近似 (Lebofsky+ 1986 STM 的常用 fudge factor)
    T_eff = T_ss * 0.84
    # Radius (m)
    R_m = D_km * 0.5 * 1000.0
    # Geocentric distance (m)
    delta_m = delta_au * 1.495978707e11
    # Spectral radiance → spectral flux density
    # F_λ (W/m²/m) = ε × (π R² / Δ²) × B_λ(T_eff)
    # 然后 Jy = (lambda² / c) × F_λ × 1e26 / 1m (Hz unit conversion)
    B_lam = planck_spectral_radiance(T_eff, lambda_um)  # W/m²/m/sr
    F_lambda = epsilon * math.pi * (R_m ** 2) / (delta_m ** 2) * B_lam  # W/m²/m
    # convert F_λ → F_ν: F_ν = λ² / c × F_λ → multiply by 1e26 to get Jy
    lam_m = lambda_um * 1e-6
    F_nu = (lam_m ** 2) / SPEED_OF_LIGHT * F_lambda
    return F_nu * 1e26


def fit_diameter_albedo_neatm(
    H: float, observed_flux_jy: float, lambda_um: float,
    r_au: float, delta_au: float, G: float = 0.15,
    eta: float = DEFAULT_BEAMING_NEA, epsilon: float = DEFAULT_EMISSIVITY,
) -> dict[str, float]:
    """单波段 NEATM 拟合: 给 H, F_λ → 估 D 和 p_V (耦合,Harris 1998 关系约束).

    NEATM single-band fit:
    - Use Harris 1998 to express D = D(p_V, H): D = 1329/√p_V × 10^(-H/5)
    - Compute predicted F_λ for trial p_V, then iterate to match observed_flux_jy
    - Return best (D_km, p_V) plus T_ss + diagnostics

    简化版,精确多波段拟合走 sbpy.thermal 完整管线或 Mainzer+ 2011 工具链。
    """
    from scipy.optimize import brentq

    def residual(log_pv):
        p_V = 10 ** log_pv
        if p_V <= 0 or p_V > 1.0:
            return 1e6
        D_km = harris_diameter_km(H, p_V)
        try:
            pred = neatm_thermal_flux_density(
                D_km, p_V, G, r_au, delta_au, lambda_um, eta=eta, epsilon=epsilon,
            )
        except (ValueError, OverflowError):
            return 1e6
        return math.log10(pred) - math.log10(observed_flux_jy)

    # p_V 物理范围 [0.01, 0.6],log10 -> [-2, -0.22]
    try:
        log_pv = brentq(residual, -2.0, -0.22, xtol=1e-4)
    except ValueError:
        # 边界外 — fallback: 用 p_V=0.05 (typical asteroid)
        log_pv = math.log10(0.05)
    p_V = 10 ** log_pv
    D_km = harris_diameter_km(H, p_V)
    T_ss = neatm_subsolar_temperature(p_V, G, r_au, eta=eta, epsilon=epsilon)
    predicted_flux = neatm_thermal_flux_density(
        D_km, p_V, G, r_au, delta_au, lambda_um, eta=eta, epsilon=epsilon,
    )
    return {
        "diameter_km": D_km,
        "albedo_pV": p_V,
        "subsolar_temperature_K": T_ss,
        "beaming_parameter_eta": eta,
        "predicted_flux_jy": predicted_flux,
        "observed_flux_jy": observed_flux_jy,
        "residual_dex": math.log10(predicted_flux) - math.log10(observed_flux_jy),
    }
