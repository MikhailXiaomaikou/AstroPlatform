"""PART AI #2 — Line luminosity 单位转换 (L_solar ↔ L_prime).

CO LFR / Solomon 1992 / 大多数高 z 线光度论文使用的"亮温线光度" L'
单位是 K km/s pc². ALPINE 等部分 [CII] 论文则报 L_line/L_sun (bolometric
luminosity).  两套单位之间 intercept 整体平移,**取决于 line rest
frequency 和 redshift** —— 不是单一常数.

公式 (Solomon 1992 / Carilli & Walter 2013):

    L'_line [K km/s pc²]
        = (c² / 2 k_B) · S·ΔV · D_L² / [(1+z) · ν_obs²]
        ∝ S·ΔV · D_L² / ν_obs² · (1+z)⁻¹

    L_line  [L_sun]
        ∝ S·ΔV · D_L² · ν_obs

两者比值消去 D_L² 和 S·ΔV:

    L_line / L'_line ∝ ν_obs³ · (1+z)
    ν_obs = ν_rest / (1+z)
    => L_line / L'_line ∝ (1+z)⁻² · ν_rest³

取 log10 + 把 (Lsun erg/s, k_B, c) 等常数全部吸收进 C:

    log10(L'_line) = log10(L_line/L_sun)
                     - 3·log10(ν_rest_GHz)
                     + 2·log10(1+z)
                     + C

其中 C ≈ 5.604 dex, 对**所有线**一致 (单纯单位换算 erg/s↔Lsun + Solomon
常数).  数值上对 [CII] (ν_rest=1900.5374 GHz) at z=5:
    -3·log10(1900.5374) = -9.836
    +2·log10(6.0)        = +1.556
    +5.604               = +5.604
    => L_prime - L_solar  ≈ -2.676 dex

WAIT — 上面的+0.66 dex 估算对应 z=5, [CII] 是 **L_solar - L_prime ≈ -2.7 dex**?
让我重新核对: Carilli & Walter 2013 Eq. 1+3 给出
    L'/L_line[L_sun] = const · 1/(ν_obs · (1+z))
                     = (1+z) / ν_rest

具体:
    L'_line[K km/s pc²] = 3.25e7 · S·ΔV[Jy km/s] · D_L²[Mpc²] / [(1+z)·ν_obs²[GHz²]]
    L_line[L_sun]       = 1.04e-3 · S·ΔV[Jy km/s] · ν_obs[GHz] · D_L²[Mpc²]

ratio:  L'/L = 3.25e7 / 1.04e-3 / [(1+z) · ν_obs³]
            = 3.125e10 · 1 / [(1+z) · ν_obs³]
            = 3.125e10 · (1+z)² / ν_rest³

log10: log10(L'/L) = 10.495 - 3·log10(ν_rest_GHz) + 2·log10(1+z)

[CII] νrest=1900.5374, z=5:
    10.495 - 3·3.2787 + 2·0.7782
    = 10.495 - 9.836 + 1.556
    = +2.215 dex

So **L'_prime - L_solar ≈ +2.2 dex** at z=5.

Carilli&Walter 2013 Table 1 actually gives [CII] L'~L_solar ± a few dex
— Bothwell SPT 报 log L'[CII] ~ 9.6, ALPINE 报 log L_CII/Lsun ~ 8.5.
差异 ~1.1 dex, 加上 (1+z) 项约 1 dex, 总体 ~2 dex.  这跟上面公式一致.

用户给的 +0.66 dex 是**对的方向但具体数值偏小**.  实际 z=4-6 [CII]
样本转换大概偏移 +2 to +2.4 dex.

"""

from __future__ import annotations

import math


# Line rest frequencies in GHz.  Source: NIST / SPLATALOGUE.
# 仅列实际会作 fit 的常用线; 缺的线由 caller 显式传 nu_rest_ghz 兜底.
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


# 单位换算常数 (Carilli & Walter 2013 Eq. 1, Eq. 3 之比的对数项):
#   L'_line / L_line[L_sun] = 3.125e10 · (1+z)² / ν_rest_GHz³
#   log10(L'/L) = log10(3.125e10) - 3·log10(ν_rest_GHz) + 2·log10(1+z)
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

    Returns None when ν_rest cannot be resolved or redshift is missing
    (z required because (1+z)² term is per-source, not a global shift).

    Args:
        log_l_solar: log10(L_line / L_sun).
        line_id: line label for ν_rest lookup ("[CII]", "CO(1-0)", ...).
        redshift: source redshift (>= 0).
        nu_rest_ghz: explicit ν_rest override; takes priority over line_id
            lookup.  Use when line_id is unknown or has multiple aliases.
    """
    if not isinstance(log_l_solar, (int, float)) or not math.isfinite(log_l_solar):
        return None
    if not isinstance(redshift, (int, float)) or not math.isfinite(redshift) or redshift < 0:
        return None

    nu = nu_rest_ghz if (nu_rest_ghz and nu_rest_ghz > 0) else lookup_line_rest_freq_ghz(line_id or "")
    if not nu or nu <= 0:
        return None

    # log10(L'/L_solar) = 10.495 - 3·log10(ν_rest_GHz) + 2·log10(1+z)
    delta = (
        _LOG_UNIT_CONSTANT
        - 3.0 * math.log10(nu)
        + 2.0 * math.log10(1.0 + redshift)
    )
    return log_l_solar + delta


def convert_log_l_prime_to_l_solar(
    log_l_prime: float,
    line_id: str | None = None,
    redshift: float | None = None,
    *,
    nu_rest_ghz: float | None = None,
) -> float | None:
    """L'_line[K km/s pc²] → L_line[L_sun] in log10 space (inverse of above)."""
    if not isinstance(log_l_prime, (int, float)) or not math.isfinite(log_l_prime):
        return None
    if not isinstance(redshift, (int, float)) or not math.isfinite(redshift) or redshift < 0:
        return None

    nu = nu_rest_ghz if (nu_rest_ghz and nu_rest_ghz > 0) else lookup_line_rest_freq_ghz(line_id or "")
    if not nu or nu <= 0:
        return None

    delta = (
        _LOG_UNIT_CONSTANT
        - 3.0 * math.log10(nu)
        + 2.0 * math.log10(1.0 + redshift)
    )
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

    No-op when row already has the target kind, or when the row is missing
    redshift / line_id (those rows should be flagged + rejected by the
    caller, NOT silently fit on mixed units).
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
        # Conversion failed — typically missing z, missing line_id, or
        # unknown line.  Caller should reject this row.
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
