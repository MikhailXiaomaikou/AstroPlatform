"""Asteroid taxonomic classification helpers.

References (cite when using in published work):
- **DeMeo+ 2009** Icarus 202, 160 (bibcode 2009Icar..202..160D) —
  Bus-DeMeo 25-class system, visible + NIR (0.45-2.45 μm), PCA-based.
- **Bus & Binzel 2002** Icarus 158, 106 — Bus visible-only taxonomy.
- **Carvano+ 2010** A&A 510, A43 (bibcode 2010A&A...510A..43C) —
  SDSS MOC 4-color (g-r, r-i, i-z, g-z) → Bus-class mapping.
- **Sergeyev & Carry 2021** A&A 652, A59 — SDSS-based 3D color taxonomy refinement.

M0 Commit 3 (2026-05-18). 简化版:
- Carvano+ 2010 SDSS 4-color → 9 major classes (C, X, S, V, D, Q, L, A, O)
- Bus-DeMeo 11 main types via spectrum slope + 1-μm feature depth (no PCA)
"""

from __future__ import annotations

import math
from typing import Sequence

# ── Bus-DeMeo 11 main types (DeMeo+ 2009 Tables 1-3 statistics) ────────
#
# Slope 是 visible 0.45-0.74 μm 谱斜率 (per 0.1 μm);
# Band-I depth 是 ~1 μm 吸收带相对 continuum 的 depth.
# 用 keystone (slope, band1_depth) 中心 + 半径 nearest-class 划分,
# 数值取自 DeMeo+ 2009 Table 2/3 + Bus & Binzel 2002 Table 4 typical 值.
BUS_DEMEO_TYPE_CENTERS: dict[str, dict[str, float]] = {
    # type: (slope per 0.1μm, band1_depth fraction, NIR slope per μm)
    "C":  {"vis_slope": 0.04, "band1_depth": 0.00, "nir_slope": -0.02, "p_V_typical": 0.06},
    "B":  {"vis_slope": -0.05, "band1_depth": 0.00, "nir_slope": -0.05, "p_V_typical": 0.07},
    "X":  {"vis_slope": 0.15, "band1_depth": 0.00, "nir_slope": 0.05, "p_V_typical": 0.15},
    "D":  {"vis_slope": 0.40, "band1_depth": 0.00, "nir_slope": 0.20, "p_V_typical": 0.05},
    "T":  {"vis_slope": 0.30, "band1_depth": 0.00, "nir_slope": 0.10, "p_V_typical": 0.08},
    "S":  {"vis_slope": 0.20, "band1_depth": 0.15, "nir_slope": 0.05, "p_V_typical": 0.20},
    "Q":  {"vis_slope": 0.15, "band1_depth": 0.25, "nir_slope": -0.05, "p_V_typical": 0.23},
    "V":  {"vis_slope": 0.25, "band1_depth": 0.40, "nir_slope": -0.10, "p_V_typical": 0.35},
    "A":  {"vis_slope": 0.55, "band1_depth": 0.35, "nir_slope": -0.10, "p_V_typical": 0.20},
    "R":  {"vis_slope": 0.35, "band1_depth": 0.45, "nir_slope": -0.10, "p_V_typical": 0.34},
    "K":  {"vis_slope": 0.15, "band1_depth": 0.10, "nir_slope": -0.05, "p_V_typical": 0.18},
    "L":  {"vis_slope": 0.30, "band1_depth": 0.10, "nir_slope": -0.10, "p_V_typical": 0.18},
}


def classify_bus_demeo_from_features(
    visible_slope: float, band1_depth: float, nir_slope: float = 0.0,
) -> dict[str, object]:
    """Nearest-class chi-sq 划分(欧氏距离, 3D feature space)。

    参数:
        visible_slope — 0.45-0.74 μm 谱斜率 (per 0.1 μm); 典型 [-0.1, 0.6]
        band1_depth — 1-μm 吸收带相对 continuum 的 fractional depth;
                       典型 [0, 0.5], 0 = featureless
        nir_slope — 0.85-2.45 μm 谱斜率 (per μm), default 0

    返回 dict 含 best_class, chi_sq, all_chi_sq, typical_albedo
    简化版 — 不区分 Bus-DeMeo 25 子类 (Sa/Sk/Sl/Sq/Sr/Sv 等), 只识别 11 main types。
    """
    chi_sqs: dict[str, float] = {}
    for type_name, center in BUS_DEMEO_TYPE_CENTERS.items():
        d_vis = (visible_slope - center["vis_slope"]) ** 2 / 0.05  # σ ~0.22
        d_b1 = (band1_depth - center["band1_depth"]) ** 2 / 0.01    # σ ~0.10
        d_nir = (nir_slope - center["nir_slope"]) ** 2 / 0.02       # σ ~0.14
        chi_sqs[type_name] = d_vis + d_b1 + d_nir
    best_class = min(chi_sqs, key=chi_sqs.get)
    return {
        "best_class": best_class,
        "chi_sq": chi_sqs[best_class],
        "all_chi_sq": chi_sqs,
        "typical_albedo_pV": BUS_DEMEO_TYPE_CENTERS[best_class]["p_V_typical"],
        "classification_system": "Bus-DeMeo main-type (simplified, 11 classes)",
        "reference": "DeMeo+ 2009 Icarus 202, 160 (2009Icar..202..160D)",
    }


# ── Carvano+ 2010 SDSS 4-color → Bus class ─────────────────────────────
#
# Carvano+ 2010 (A&A 510, A43) Table 1 给出 SDSS 4-color (u-g, g-r, r-i, i-z)
# 各 Bus class (Tholen-equiv) 的 mean 和 std。 我用 9 个 main superclasses 的
# nearest-center matching (Mahalanobis 简化).

CARVANO_2010_COLOR_CENTERS: dict[str, dict[str, float]] = {
    # 4-color in mag: (u-g, g-r, r-i, i-z); p_V_typical 来自 NEOWISE Mainzer+ 2011
    "C":  {"u-g": 1.62, "g-r": 0.46, "r-i": 0.16, "i-z": -0.04, "p_V_typical": 0.06},
    "X":  {"u-g": 1.75, "g-r": 0.55, "r-i": 0.20, "i-z": 0.05, "p_V_typical": 0.15},
    "D":  {"u-g": 1.90, "g-r": 0.75, "r-i": 0.32, "i-z": 0.18, "p_V_typical": 0.05},
    "S":  {"u-g": 1.80, "g-r": 0.65, "r-i": 0.27, "i-z": -0.10, "p_V_typical": 0.20},
    "V":  {"u-g": 1.85, "g-r": 0.65, "r-i": -0.05, "i-z": -0.30, "p_V_typical": 0.35},
    "A":  {"u-g": 2.05, "g-r": 0.85, "r-i": 0.30, "i-z": -0.18, "p_V_typical": 0.20},
    "Q":  {"u-g": 1.80, "g-r": 0.62, "r-i": 0.12, "i-z": -0.18, "p_V_typical": 0.23},
    "L":  {"u-g": 1.90, "g-r": 0.70, "r-i": 0.30, "i-z": -0.08, "p_V_typical": 0.18},
    "O":  {"u-g": 1.55, "g-r": 0.42, "r-i": 0.10, "i-z": -0.25, "p_V_typical": 0.30},  # rare
}


def classify_carvano_sdss_colors(
    u_g: float, g_r: float, r_i: float, i_z: float,
) -> dict[str, object]:
    """Carvano+ 2010 SDSS 4-color nearest-center 分类。

    返回 dict 含 best_class, distance, all_distances, typical_albedo
    """
    obs = (u_g, g_r, r_i, i_z)
    dists: dict[str, float] = {}
    for type_name, center in CARVANO_2010_COLOR_CENTERS.items():
        c = (center["u-g"], center["g-r"], center["r-i"], center["i-z"])
        d_sq = sum((o - ci) ** 2 for o, ci in zip(obs, c))
        dists[type_name] = math.sqrt(d_sq)
    best = min(dists, key=dists.get)
    return {
        "best_class": best,
        "distance": dists[best],
        "all_distances": dists,
        "typical_albedo_pV": CARVANO_2010_COLOR_CENTERS[best]["p_V_typical"],
        "classification_system": "Carvano+ 2010 SDSS 4-color nearest-center",
        "reference": "Carvano+ 2010 A&A 510, A43 (2010A&A...510A..43C)",
    }


def spectrum_to_features(
    wavelengths_um: Sequence[float], reflectance: Sequence[float],
) -> dict[str, float]:
    """从可视/近红外光谱提取 Bus-DeMeo nearest-class 用的 3 个 keystone features.

    Returns:
        visible_slope — linear regression slope per 0.1 μm in [0.45, 0.74] μm
        band1_depth — 1-μm absorption depth fraction (relative to continuum
                      估自 [0.74, 0.85] 与 [1.0, 1.3] μm 端点)
        nir_slope — linear regression slope per μm in [1.3, 2.45] μm
    简化版,不做 PCA(留 M2)。
    """
    if len(wavelengths_um) != len(reflectance):
        raise ValueError("wavelengths_um and reflectance 长度必须一致")
    if len(wavelengths_um) < 3:
        raise ValueError("光谱点数 < 3, 无法回归")
    wl = list(wavelengths_um)
    rf = list(reflectance)
    visible = [(w, r) for w, r in zip(wl, rf) if 0.45 <= w <= 0.74]
    nir = [(w, r) for w, r in zip(wl, rf) if 1.3 <= w <= 2.45]
    if not visible:
        raise ValueError("visible 区段 [0.45, 0.74] μm 无数据点")
    visible_slope = _linear_slope(visible) * 0.1
    nir_slope = _linear_slope(nir) if nir else 0.0
    # Band-I depth: continuum interpolated at 0.7 + 1.5 μm, 测 1.0 μm 处
    cont_left = _value_at(wl, rf, 0.7)
    cont_right = _value_at(wl, rf, 1.5)
    bot = _value_at(wl, rf, 1.0)
    if cont_left is not None and cont_right is not None and bot is not None:
        # interpolate continuum at 1.0 μm
        cont_at_1um = cont_left + (cont_right - cont_left) * (1.0 - 0.7) / (1.5 - 0.7)
        if cont_at_1um > 0:
            band1_depth = max(0.0, (cont_at_1um - bot) / cont_at_1um)
        else:
            band1_depth = 0.0
    else:
        band1_depth = 0.0
    return {
        "visible_slope": visible_slope,
        "band1_depth": band1_depth,
        "nir_slope": nir_slope,
    }


def _linear_slope(points: list[tuple[float, float]]) -> float:
    n = len(points)
    if n < 2:
        return 0.0
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] ** 2 for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0
    return (n * sxy - sx * sy) / denom


def _value_at(
    wl: list[float], rf: list[float], target_um: float, tol: float = 0.1,
) -> float | None:
    """返回 wl 距 target_um 最近(且在 tol 内)的 reflectance 值,否则 None."""
    if not wl:
        return None
    diffs = [(abs(w - target_um), r) for w, r in zip(wl, rf)]
    diffs.sort(key=lambda x: x[0])
    if diffs[0][0] > tol:
        return None
    return diffs[0][1]
