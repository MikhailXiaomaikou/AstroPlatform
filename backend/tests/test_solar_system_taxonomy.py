"""Unit tests for solar_system_taxonomy service (M0 Commit 3).

Physics validation: Bus-DeMeo nearest-class chi-sq / Carvano+ 2010 SDSS color classifier.
"""

from __future__ import annotations

import pytest


# ── Bus-DeMeo from keystone features ───────────────────────────────


def test_busdemeo_C_class_for_flat_dark_spectrum():
    """visible_slope ≈ 0.04, band1_depth = 0, nir_slope = -0.02 → C class."""
    from app.services.solar_system_taxonomy import classify_bus_demeo_from_features

    r = classify_bus_demeo_from_features(
        visible_slope=0.04, band1_depth=0.00, nir_slope=-0.02,
    )
    assert r["best_class"] == "C"
    assert r["typical_albedo_pV"] == pytest.approx(0.06, abs=0.01)


def test_busdemeo_V_class_for_strong_1um_band():
    """Vesta-like: strong 1-μm band, moderate slope → V."""
    from app.services.solar_system_taxonomy import classify_bus_demeo_from_features

    r = classify_bus_demeo_from_features(
        visible_slope=0.25, band1_depth=0.40, nir_slope=-0.10,
    )
    assert r["best_class"] == "V"


def test_busdemeo_S_class_typical_main_belt():
    """Typical main-belt S-type average: slope ~ 0.2, band1 ~ 0.15."""
    from app.services.solar_system_taxonomy import classify_bus_demeo_from_features

    r = classify_bus_demeo_from_features(
        visible_slope=0.20, band1_depth=0.15, nir_slope=0.05,
    )
    assert r["best_class"] == "S"


def test_busdemeo_D_class_steep_red_spectrum():
    """Jupiter Trojan-like D: very steep red visible slope."""
    from app.services.solar_system_taxonomy import classify_bus_demeo_from_features

    r = classify_bus_demeo_from_features(
        visible_slope=0.40, band1_depth=0.00, nir_slope=0.20,
    )
    assert r["best_class"] == "D"


def test_busdemeo_returns_all_chi_sq():
    """Result should contain chi_sq values for all 12 main classes (C, B, X, D, T, S, Q, V, A, R, K, L)."""
    from app.services.solar_system_taxonomy import (
        BUS_DEMEO_TYPE_CENTERS, classify_bus_demeo_from_features,
    )

    r = classify_bus_demeo_from_features(0.20, 0.15, 0.05)
    assert "all_chi_sq" in r
    assert len(r["all_chi_sq"]) == len(BUS_DEMEO_TYPE_CENTERS) == 12
    assert r["chi_sq"] == min(r["all_chi_sq"].values())


# ── Carvano+ 2010 SDSS classifier ──────────────────────────────────


def test_carvano_vesta_colors_yield_V():
    """V-type colors (strong 1 micron absorption: r-i ~-0.40) → V class.

    After P3 calibration, V class center: (u-g=1.85, g-r=0.60, r-i=-0.40, i-z=-0.20).
    """
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(
        u_g=1.85, g_r=0.60, r_i=-0.40, i_z=-0.20,
    )
    assert r["best_class"] == "V"


def test_carvano_c_type_low_albedo():
    """C-type colors: u-g~1.45, g-r~0.42, r-i~0.10, i-z~0.01 → C, p_V≈0.06."""
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(1.45, 0.42, 0.10, 0.01)
    assert r["best_class"] == "C"
    assert r["typical_albedo_pV"] == pytest.approx(0.06, abs=0.02)


def test_carvano_d_type_steep_red():
    """D-type colors: large g-r, large r-i, large i-z."""
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(2.00, 0.75, 0.35, 0.20)
    assert r["best_class"] == "D"


def test_carvano_returns_all_distances():
    """Diagnostic fields (backward-compat: distance/all_distances still present = sqrt(chi2))."""
    from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

    r = classify_carvano_sdss_colors(1.85, 0.55, 0.18, -0.15)  # S center
    assert "all_distances" in r
    assert len(r["all_distances"]) >= 9
    assert r["distance"] == min(r["all_distances"].values())


# ── Spectrum → feature extraction ──────────────────────────────────


def test_spectrum_to_features_flat_spectrum_zero_slope():
    """Completely flat spectrum → slope=0, depth=0."""
    from app.services.solar_system_taxonomy import spectrum_to_features

    wl = [0.45, 0.55, 0.65, 0.75, 0.85, 1.0, 1.1, 1.3, 1.5, 1.8, 2.0, 2.45]
    rf = [1.0] * len(wl)
    feat = spectrum_to_features(wl, rf)
    assert feat["visible_slope"] == pytest.approx(0.0, abs=1e-6)
    assert feat["band1_depth"] == pytest.approx(0.0, abs=1e-6)
    assert feat["nir_slope"] == pytest.approx(0.0, abs=1e-6)


def test_spectrum_to_features_red_sloped_no_band():
    """Red linear spectrum → positive slope, band1_depth ≈ 0."""
    from app.services.solar_system_taxonomy import spectrum_to_features

    wl = [0.45, 0.55, 0.65, 0.75]
    rf = [1.0, 1.05, 1.10, 1.15]  # slope = 0.5 per μm = 0.05 per 0.1 μm
    feat = spectrum_to_features(wl, rf)
    assert feat["visible_slope"] == pytest.approx(0.05, abs=0.005)


def test_spectrum_to_features_with_1um_absorption_band():
    """V-type strong 1-micron band: continuum ~1, value at 1.0 micron ~0.6 → depth~0.4."""
    from app.services.solar_system_taxonomy import spectrum_to_features

    wl = [0.50, 0.70, 0.85, 1.00, 1.15, 1.30, 1.50]
    rf = [1.00, 1.00, 0.95, 0.60, 0.85, 0.95, 1.00]
    feat = spectrum_to_features(wl, rf)
    # continuum at 1.0 μm ~ 0.975 (interp between 0.7→1.00 and 1.5→1.00),
    # bottom = 0.60, depth ~ (0.975 - 0.6) / 0.975 ≈ 0.39
    assert feat["band1_depth"] == pytest.approx(0.39, abs=0.10)


def test_spectrum_to_features_invalid_input_raises():
    from app.services.solar_system_taxonomy import spectrum_to_features
    with pytest.raises(ValueError):
        spectrum_to_features([0.5, 0.6], [1.0])  # mismatched lengths
    with pytest.raises(ValueError):
        spectrum_to_features([0.5], [1.0])  # only 1 point
    with pytest.raises(ValueError):
        spectrum_to_features([1.0, 1.5, 2.0], [1.0, 1.0, 1.0])  # no visible-range segment
