"""Line luminosity unit conversion (L_solar <-> L_prime).

CO LFR / most high-z line luminosity papers use the brightness-temperature
line luminosity L' in units of K km/s pc². Some [CII] papers (e.g. ALPINE)
report L_line/L_sun (bolometric luminosity). The conversion between the two
is an intercept shift that depends ONLY on the line rest frequency — it is
**redshift-independent** (a common pitfall is to carry a spurious (1+z) term;
this module previously did, inflating z~5 [CII] offsets by ~1.5 dex).

Derivation — Solomon & Vanden Bout 2005 (ARA&A 43, 677), Eqs. 1 & 3
(ν in GHz, D_L in Mpc, S·dV in Jy km/s):

    L'_line [K km/s pc^2] = 3.25e7 * S*dV * nu_obs^-2 * D_L^2 * (1+z)^-3
    L_line  [L_sun]       = 1.04e-3 * S*dV * nu_obs    * D_L^2

(L uses nu_obs: the observed frequency converts the velocity-integrated flux
density to an energy flux; D_L carries the cosmological factors.)

Ratio — S*dV and D_L^2 cancel:

    L'/L = (3.25e7 / 1.04e-3) * nu_obs^-3 * (1+z)^-3
         = 3.125e10 * nu_obs^-3 * (1+z)^-3
    nu_obs = nu_rest / (1+z)   =>   nu_obs^-3 = nu_rest^-3 * (1+z)^3
    => L'/L = 3.125e10 * nu_rest^-3        # (1+z) cancels — redshift-independent

In log10:

    log10(L'/L) = log10(3.125e10) - 3*log10(nu_rest_GHz)
                = 10.495 - 3*log10(nu_rest_GHz)

[CII] nu_rest = 1900.5369 GHz:
    10.495 - 3*log10(1900.5369) = 10.495 - 9.837 = +0.658 dex   (at any z)

So for an ALPINE-style [CII] source with log(L_CII/L_sun)=8.5, the brightness-
temperature luminosity is log L' ~ 9.16 (offset +0.66 dex), independent of
redshift.
"""

from __future__ import annotations

import math


# Line rest frequencies in GHz.  Source: NIST / SPLATALOGUE.
# Lists only the commonly fitted lines; missing lines should be supplied by the caller via nu_rest_ghz.
LINE_REST_FREQ_GHZ: dict[str, float] = {
    "[CII]": 1900.5369,        # 158 μm fine-structure
    "[CII]158": 1900.5369,
    "[OIII]88": 3393.0062,     # 88 μm
    "[OIII]52": 5785.879,      # 52 μm
    "[NII]205": 1461.1318,     # 205 μm
    "[CI]370": 809.342,
    "[CI]610": 492.161,
    "CO(1-0)": 115.27120,
    "CO(2-1)": 230.53800,
    "CO(3-2)": 345.79599,
    "CO(4-3)": 461.04077,
    "CO(5-4)": 576.26793,
    "CO(6-5)": 691.47308,
    "CO(7-6)": 806.65180,
    "HCN(1-0)": 88.63185,
    "HCO+(1-0)": 89.18852,
    "Halpha": 456805.0,        # optical, GHz units
    "Hbeta":  616670.0,
}


# Unit conversion constant (ratio of Solomon & Vanden Bout 2005 Eqs. 1 & 3):
#   L'_line / L_line[L_sun] = 3.125e10 / nu_rest_GHz^3   (redshift-independent)
#   log10(L'/L) = log10(3.125e10) - 3*log10(nu_rest_GHz)
_LOG_UNIT_CONSTANT = math.log10(3.125e10)  # ≈ 10.495


def lookup_line_rest_freq_ghz(line_id: str) -> float | None:
    """Return ν_rest in GHz for known emission lines, else None.

    Match is case + bracket sensitive to avoid silent fallbacks
    (e.g. "[CII]" vs "CII" should not be confused — one of those is
    typically a typo).
    """
    if not isinstance(line_id, str):
        return None
    key = line_id.strip()
    return LINE_REST_FREQ_GHZ.get(key)


def convert_log_l_solar_to_l_prime(
    log_l_solar: float,
    line_id: str | None = None,
    redshift: float | None = None,
    *,
    nu_rest_ghz: float | None = None,
) -> float | None:
    """L_line[L_sun] → L'_line[K km/s pc²] in log10 space.

    The conversion is REDSHIFT-INDEPENDENT: L'/L = 3.125e10 · ν_rest⁻³ (the
    (1+z) factors cancel between L' ∝ ν_obs⁻²(1+z)⁻³ and L ∝ ν_obs — Solomon &
    Vanden Bout 2005, ARA&A 43, 677, Eqs. 1 & 3). ``redshift`` is optional; if
    supplied it is validated as a non-negative finite number, but it does not
    enter the conversion.

    Returns None when ν_rest cannot be resolved or a supplied redshift is invalid.

    Args:
        log_l_solar: log10(L_line / L_sun).
        line_id: line label for ν_rest lookup ("[CII]", "CO(1-0)", ...).
        redshift: optional source redshift (>= 0). Validated but not used in the
            (redshift-independent) conversion.
        nu_rest_ghz: explicit ν_rest override; takes priority over line_id
            lookup.  Use when line_id is unknown or has multiple aliases.
    """
    if not isinstance(log_l_solar, (int, float)) or not math.isfinite(log_l_solar):
        return None
    if redshift is not None and (
        not isinstance(redshift, (int, float))
        or not math.isfinite(redshift)
        or redshift < 0
    ):
        return None

    nu = nu_rest_ghz if (nu_rest_ghz and nu_rest_ghz > 0) else lookup_line_rest_freq_ghz(line_id or "")
    if not nu or nu <= 0:
        return None

    # log10(L'/L_solar) = 10.495 - 3·log10(ν_rest_GHz)  — redshift-independent
    # (the (1+z) factors cancel; Solomon & Vanden Bout 2005, Eqs. 1 & 3).
    delta = _LOG_UNIT_CONSTANT - 3.0 * math.log10(nu)
    return log_l_solar + delta


def convert_log_l_prime_to_l_solar(
    log_l_prime: float,
    line_id: str | None = None,
    redshift: float | None = None,
    *,
    nu_rest_ghz: float | None = None,
) -> float | None:
    """L'_line[K km/s pc²] → L_line[L_sun] in log10 space (inverse of above).

    Redshift-independent (see convert_log_l_solar_to_l_prime); ``redshift`` is
    optional and, when supplied, validated but not used in the conversion.
    """
    if not isinstance(log_l_prime, (int, float)) or not math.isfinite(log_l_prime):
        return None
    if redshift is not None and (
        not isinstance(redshift, (int, float))
        or not math.isfinite(redshift)
        or redshift < 0
    ):
        return None

    nu = nu_rest_ghz if (nu_rest_ghz and nu_rest_ghz > 0) else lookup_line_rest_freq_ghz(line_id or "")
    if not nu or nu <= 0:
        return None

    delta = _LOG_UNIT_CONSTANT - 3.0 * math.log10(nu)
    return log_l_prime - delta


def convert_row_luminosity_inplace(
    row: dict,
    target_kind: str,
    *,
    line_id_fallback: str | None = None,
) -> dict:
    """Convert row['log_luminosity'] to `target_kind` in place.

    target_kind ∈ {"L_solar", "L_prime"}.

    On success: sets row['luminosity_kind']=target_kind, row['log_luminosity']
    to the converted value, row['log_luminosity_transformed_from'] to the
    pre-conversion value (for audit).  Errors propagated to row['_unit_error'].

    No-op when row already has the target kind. A missing/unknown line identity
    is rejected; redshift is not required because the conversion is explicitly
    rest-frequency-only.
    """
    out = dict(row)
    current = out.get("luminosity_kind") or "L_solar"  # default: legacy rows are L_solar
    if current == target_kind:
        out["luminosity_kind"] = target_kind
        return out

    log_l = out.get("log_luminosity")
    z = out.get("redshift")
    line_id = out.get("line_id") or line_id_fallback

    if target_kind == "L_prime" and current == "L_solar":
        new_val = convert_log_l_solar_to_l_prime(log_l, line_id, z)
    elif target_kind == "L_solar" and current == "L_prime":
        new_val = convert_log_l_prime_to_l_solar(log_l, line_id, z)
    else:
        out["_unit_error"] = f"unsupported_conversion: {current} -> {target_kind}"
        return out

    if new_val is None:
        # Conversion failed — typically missing/unknown line identity or an
        # explicitly supplied invalid redshift. Caller should reject this row.
        out["_unit_error"] = (
            f"could_not_convert {current} -> {target_kind} "
            f"(line_id={line_id!r}, redshift={z!r})"
        )
        return out

    out["log_luminosity_transformed_from"] = log_l
    out["log_luminosity_transformed_to"] = target_kind
    out["log_luminosity"] = new_val
    out["luminosity_kind"] = target_kind
    return out
