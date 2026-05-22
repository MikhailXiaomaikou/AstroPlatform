"""Solar-system thermal modelling (NEATM + STM + Harris diameter).

References (cite when using in published work):
- **Harris 1998** Icarus 131, 291 (bibcode 1998Icar..131..291H) —
  NEATM original paper + diameter/albedo formula.
- **Lebofsky+ 1986** Icarus 68, 239 (bibcode 1986Icar...68..239L) —
  Standard Thermal Model (STM).
- **Mainzer+ 2011** ApJ 743, 156 (bibcode 2011ApJ...743..156M) —
  NEOWISE multi-band NEATM fitting + beaming parameter η calibration.
- **Pravec & Harris 2007** Icarus 190, 250 — statistics of default η values (NEAs ⟨η⟩ ≈ 1.4).

M0 Commit 3 (2026-05-18). Single-band simplified fit; multi-band + self-consistent η deferred to M2.
"""

from __future__ import annotations

import math

# Physical constants
STEFAN_BOLTZMANN = 5.670374419e-8       # W m⁻² K⁻⁴
SOLAR_CONSTANT_1AU = 1361.0             # W m⁻² (Kopp & Lean 2011)
PLANCK_H = 6.62607015e-34               # J s
SPEED_OF_LIGHT = 2.99792458e8           # m s⁻¹
BOLTZMANN = 1.380649e-23                # J K⁻¹

DEFAULT_EMISSIVITY = 0.9                # ε, NEATM standard value
DEFAULT_BEAMING_NEA = 1.4               # η for NEAs (Pravec & Harris 2007)
DEFAULT_BEAMING_MBA = 0.756             # η for MBAs (Mainzer+ 2011)

# Harris diameter constant (km, V band): D = K / sqrt(p_V) × 10^(-H/5)
HARRIS_DIAMETER_CONSTANT_KM = 1329.0    # Harris 1998 eq. 1


def harris_diameter_km(H: float, p_V: float) -> float:
    """Harris 1998 formula: D (km) = 1329 / sqrt(p_V) x 10^(-H/5).

    Used to estimate diameter when H_V (absolute magnitude) and geometric
    albedo p_V are known. Given D and H, p_V can be solved for with harris_albedo.
    """
    if p_V <= 0 or p_V > 1.0:
        raise ValueError(f"p_V out of (0, 1]: {p_V}")
    return HARRIS_DIAMETER_CONSTANT_KM / math.sqrt(p_V) * 10 ** (-H / 5.0)


def harris_albedo(H: float, D_km: float) -> float:
    """Harris 1998 inverse: p_V = (1329 / D / 10^(H/5))²."""
    if D_km <= 0:
        raise ValueError(f"D_km must be positive: {D_km}")
    ratio = HARRIS_DIAMETER_CONSTANT_KM / D_km * 10 ** (-H / 5.0)
    return ratio * ratio


def phase_integral_bowell(G: float = 0.15) -> float:
    """Bowell+ 1989 phase integral: q = 0.290 + 0.684 x G.

    Used in NEATM to convert between geometric albedo p_V and Bond albedo A: A = p_V x q.
    """
    return 0.290 + 0.684 * G


def neatm_subsolar_temperature(
    p_V: float, G: float, r_au: float,
    eta: float = DEFAULT_BEAMING_NEA, epsilon: float = DEFAULT_EMISSIVITY,
) -> float:
    """NEATM subsolar temperature T_ss (K).

    T_ss = ((1 - A_Bond) × S_⊙ / (η × ε × σ × r_au²))^(1/4)
    A_Bond = p_V × q(G), q = 0.290 + 0.684 G  (Bowell+ 1989)

    Args:
        p_V -- geometric albedo (V band)
        G -- phase slope (Bowell+ 1989)
        r_au -- heliocentric distance (au)
        eta -- beaming parameter (default 1.4 for NEAs, Pravec & Harris 2007)
        epsilon -- infrared emissivity (default 0.9)
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
        raise ValueError(f"Bond albedo >= 1 (p_V={p_V}, G={G}); non-physical NEATM parameters")
    flux_helio = SOLAR_CONSTANT_1AU / (r_au ** 2)
    return ((1 - A_bond) * flux_helio / (eta * epsilon * STEFAN_BOLTZMANN)) ** 0.25


def planck_spectral_radiance(T: float, lambda_um: float) -> float:
    """B_lambda(T) — Planck spectral radiance (W m⁻² m⁻¹ sr⁻¹)."""
    if T <= 0 or lambda_um <= 0:
        raise ValueError(f"T, lambda_um must be positive: {T}, {lambda_um}")
    lam_m = lambda_um * 1e-6
    x = PLANCK_H * SPEED_OF_LIGHT / (lam_m * BOLTZMANN * T)
    # Avoid expm1 overflow
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
    """NEATM predicted thermal flux density at wavelength lambda (Jy).

    Simplified: at phase angle alpha=0° (opposition) with hemisphere-average approximation:
        F_lambda = (pi R² / Delta²) x epsilon x B_lambda(T_eff)
    where T_eff ≈ T_ss x 0.5^0.25 ≈ T_ss x 0.84 (hemisphere average).
    For publication-grade multi-band fitting, use the full sbpy implementation
    or the Mainzer+ 2011 NEATM code. M0 provides only a single-band rough estimate.

    Returns Jy (= 1e-26 W m⁻² Hz⁻¹).
    """
    if D_km <= 0 or delta_au <= 0 or lambda_um <= 0:
        raise ValueError(
            f"D_km, delta_au, lambda_um must be positive: "
            f"{D_km}, {delta_au}, {lambda_um}"
        )
    T_ss = neatm_subsolar_temperature(p_V, G, r_au, eta=eta, epsilon=epsilon)
    # Hemisphere-average temperature approximation (Lebofsky+ 1986 STM fudge factor)
    T_eff = T_ss * 0.84
    # Radius (m)
    R_m = D_km * 0.5 * 1000.0
    # Geocentric distance (m)
    delta_m = delta_au * 1.495978707e11
    # Spectral radiance -> spectral flux density
    # F_lambda (W/m²/m) = epsilon x (pi R² / Delta²) x B_lambda(T_eff)
    # then Jy = (lambda² / c) x F_lambda x 1e26 (Hz unit conversion)
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
    """Single-band NEATM fit: given H and F_lambda, estimate D and p_V (coupled via Harris 1998).

    NEATM single-band fit:
    - Use Harris 1998 to express D = D(p_V, H): D = 1329/sqrt(p_V) x 10^(-H/5)
    - Compute predicted F_lambda for trial p_V, then iterate to match observed_flux_jy
    - Return best (D_km, p_V) plus T_ss + diagnostics

    Simplified; for precise multi-band fitting use the full sbpy.thermal pipeline
    or the Mainzer+ 2011 toolchain.
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

    # Physical p_V range [0.01, 0.6] -> log10 range [-2, -0.22]
    try:
        log_pv = brentq(residual, -2.0, -0.22, xtol=1e-4)
    except ValueError:
        # Outside bracket — fallback: use p_V=0.05 (typical asteroid)
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
