"""P3 (2026-05-20): Carvano+ 2010 SDSS classifier calibration regression tests.

Blind test revealed that the wrong V class r-i=-0.05 value caused Vesta to be
classified as O. After calibration, paper-accurate centers + per-color std + chi2
scoring are used. These tests cover 4 well-known prototypes plus a noisy variant,
guarding against future mis-edits to the centers that would misclassify known asteroids.
"""

from __future__ import annotations


# ── well-known prototype real SDSS colors (non-trivial test) ──────────


def test_vesta_real_sdss_colors_yield_V():
    """Vesta real SDSS colors (u-g=1.43, g-r=0.50, r-i=-0.39, i-z=-0.16) → V class.

    Vesta is the V-class prototype (Burbine+ 2001) with a strong 1-micron absorption feature.
    u-g toward the blue end (1.43) is an outlier for this individual relative to the V class
    mean; the V class u-g_std must be tolerant enough to classify it correctly.
    """
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(1.43, 0.50, -0.39, -0.16)
    assert r["best_class"] == "V", (
        f"Vesta real should classify as V, got {r['best_class']} "
        f"(χ²={r['chi2']:.2f}, all_chi2={r['all_chi2']})"
    )


def test_bennu_real_sdss_colors_yield_C_complex():
    """(101955) Bennu real colors (~C/B-type) → C complex (C or X)."""
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(1.40, 0.43, 0.12, -0.02)
    assert r["best_class"] in {"C", "X"}, (
        f"Bennu should classify as C-complex, got {r['best_class']}"
    )


def test_itokawa_real_sdss_colors_yield_S_or_Q():
    """(25143) Itokawa real colors (S/Q hybrid) → S or Q."""
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


# ── chi2 scoring behavior (replaces old Euclidean distance) ───────────────────


def test_chi2_score_present_and_consistent_with_distance():
    """classify should return chi2 and all_chi2, with backward-compatible field distance = sqrt(chi2)."""
    import math

    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(1.45, 0.42, 0.10, 0.01)  # C center
    assert "chi2" in r
    assert "all_chi2" in r
    assert math.isclose(r["chi2"], r["distance"] ** 2, abs_tol=1e-9)
    for cls, chi2 in r["all_chi2"].items():
        assert math.isclose(chi2, r["all_distances"][cls] ** 2, abs_tol=1e-9)


def test_chi2_classifier_robust_to_one_outlier_color():
    """With noisy colors, chi2 is more robust than Euclidean when the other 3 colors
    strongly match a class.

    Given a C-type object with noisy u-g shifted blue (1.30 vs C mean 1.45), while the
    other 3 colors are close to the C center, chi2 should still give best=C (because
    u-g_std=0.10 tolerates the noise).
    """
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(1.30, 0.42, 0.10, 0.01)
    assert r["best_class"] == "C"


def test_v_class_distinct_from_o_for_strong_1um_absorption():
    """Strong 1-micron absorption (strongly negative r-i): must classify as V, not O.

    Bug before P3 fix: V class r-i=-0.05 caused V/O confusion. Now V r-i=-0.40, O r-i=-0.15.
    """
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    # strong 1-micron absorption (r-i=-0.45) should be V
    r = classify_carvano_sdss_colors(1.85, 0.60, -0.45, -0.20)
    assert r["best_class"] == "V"

    # weak 1-micron absorption (r-i=-0.15) should be O (or Q)
    r2 = classify_carvano_sdss_colors(1.75, 0.50, -0.15, -0.30)
    assert r2["best_class"] in {"O", "Q"}
