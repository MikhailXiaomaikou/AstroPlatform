"""P3 (2026-05-20): Carvano+ 2010 SDSS classifier 校准回归测试.

盲测发现 V class r-i=-0.05 错值导致 Vesta 被分到 O 类. 校准后用
paper-accurate centers + per-color std + χ² scoring. 这些测试覆盖
4 个 well-known prototype 加 noisy variant, 防止以后误改 centers
导致 known asteroid 分错.
"""

from __future__ import annotations


# ── well-known prototype 真实 SDSS 颜色(non-trivial test) ──────────


def test_vesta_real_sdss_colors_yield_V():
    """Vesta 真实 SDSS 颜色 (u-g=1.43, g-r=0.50, r-i=-0.39, i-z=-0.16) → V class.

    Vesta 是 V-class 原型 (Burbine+ 2001), 强 1μm 吸收特征.
    u-g 偏蓝端 (1.43) 是 Vesta 这颗个体相比 V class 平均的 outlier,
    需要 V 类 u-g_std 足够宽容才能正确分类.
    """
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(1.43, 0.50, -0.39, -0.16)
    assert r["best_class"] == "V", (
        f"Vesta real should classify as V, got {r['best_class']} "
        f"(χ²={r['chi2']:.2f}, all_chi2={r['all_chi2']})"
    )


def test_bennu_real_sdss_colors_yield_C_complex():
    """(101955) Bennu 真实颜色 (~C/B-type) → C complex (C or X)."""
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(1.40, 0.43, 0.12, -0.02)
    assert r["best_class"] in {"C", "X"}, (
        f"Bennu should classify as C-complex, got {r['best_class']}"
    )


def test_itokawa_real_sdss_colors_yield_S_or_Q():
    """(25143) Itokawa 真实颜色 (S/Q hybrid) → S 或 Q."""
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(1.92, 0.55, 0.09, -0.10)
    assert r["best_class"] in {"S", "Q"}, (
        f"Itokawa should classify as S or Q, got {r['best_class']}"
    )


def test_d_type_trojan_classifies_correctly():
    """Jupiter Trojan typical D-type colors (very red): u-g≈2.0, g-r≈0.75, r-i≈0.35, i-z≈0.20."""
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(2.00, 0.75, 0.35, 0.20)
    assert r["best_class"] == "D"


# ── χ² scoring 行为(取代旧的 Euclidean distance) ───────────────────


def test_chi2_score_present_and_consistent_with_distance():
    """classify 应返 chi2 和 all_chi2,backward-compat 字段 distance = sqrt(chi2)。"""
    import math

    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(1.45, 0.42, 0.10, 0.01)  # C center
    assert "chi2" in r
    assert "all_chi2" in r
    assert math.isclose(r["chi2"], r["distance"] ** 2, abs_tol=1e-9)
    for cls, chi2 in r["all_chi2"].items():
        assert math.isclose(chi2, r["all_distances"][cls] ** 2, abs_tol=1e-9)


def test_chi2_classifier_robust_to_one_outlier_color():
    """noisy 颜色中,如果其他 3 颜色匹配某 class 强,χ² 比 Euclidean 更鲁棒。

    给一个 C-type 天体但 u-g 噪声偏蓝 (1.30 vs C mean 1.45), 其他 3 颜色
    都接近 C 中心. χ² 应仍 best=C (因为 u-g_std=0.10 容许 noise).
    """
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(1.30, 0.42, 0.10, 0.01)
    assert r["best_class"] == "C"


def test_v_class_distinct_from_o_for_strong_1um_absorption():
    """1μm 吸收强(r-i 强负): 必须分到 V 类不是 O 类。

    P3 修复前的 bug: V 类 r-i=-0.05 让 V/O 混淆. 现在 V r-i=-0.40, O r-i=-0.15.
    """
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    # 强 1μm 吸收 (r-i=-0.45) 应该是 V
    r = classify_carvano_sdss_colors(1.85, 0.60, -0.45, -0.20)
    assert r["best_class"] == "V"

    # 弱 1μm 吸收 (r-i=-0.15) 应该是 O (或 Q)
    r2 = classify_carvano_sdss_colors(1.75, 0.50, -0.15, -0.30)
    assert r2["best_class"] in {"O", "Q"}
