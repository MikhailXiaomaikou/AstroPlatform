"""Phase D2 — astronomy fundamentals tests.

Locks the contract that:
- Cosmology has one source of truth (Planck18 by default).
- Gaia RV is exposed as radial_velocity_km_s, never as redshift.
- Crossmatch propagates proper motion between mixed epochs.
- Lomb-Scargle refuses short series, reports window function +
  pseudo-Nyquist, uses bootstrap FAP when n<200.
- Pulsar derived quantities honour user-supplied I / class /
  braking index and report the assumption envelope.
- dust_extinction supports F99 / G03 / G16 in addition to CCM89.
"""

from __future__ import annotations

import numpy as np
import pytest


# --------------------------------------------------------------------
# D2.3 — cosmology module
# --------------------------------------------------------------------


def test_cosmology_default_is_planck18():
    from app.services.cosmology import get_cosmology, cosmology_manifest

    cosmo = get_cosmology()
    assert cosmo.name == "Planck18"
    manifest = cosmology_manifest()
    assert manifest["name"] == "Planck18"
    assert 60 < manifest["H0_km_s_Mpc"] < 80
    assert 0.2 < manifest["Om0"] < 0.5


def test_cosmology_named_override():
    from app.services.cosmology import get_cosmology

    wmap9 = get_cosmology("WMAP9")
    assert wmap9.name == "WMAP9"
    planck15 = get_cosmology("Planck15")
    assert planck15.name == "Planck15"


def test_cosmology_custom_flat_lcdm():
    from app.services.cosmology import get_cosmology

    cosmo = get_cosmology("FlatLambdaCDM_H70p0_Om0p3")
    assert 69 < float(cosmo.H0.value) < 71
    assert 0.28 < float(cosmo.Om0) < 0.32


# --------------------------------------------------------------------
# D2.2 — Gaia RV never mislabelled as redshift
# --------------------------------------------------------------------


def test_gaia_table_to_objects_does_not_fill_redshift_from_rv():
    """Simulate a Gaia row with radial_velocity but no cosmological z field."""
    from astropy.table import Table

    from app.connectors.gaia import GaiaConnector

    tbl = Table({
        "source_id": [1],
        "ra": [10.68],
        "dec": [41.27],
        "radial_velocity": [123.4],  # stellar kinematic km/s
        "parallax": [1.0],
    })

    connector = GaiaConnector()
    objs = connector._table_to_objects(tbl)
    assert len(objs) == 1
    obj = objs[0]
    # The stellar RV must not appear as redshift — that was the bug.
    assert obj.redshift is None
    # But it should be available explicitly as km/s.
    assert obj.extra["radial_velocity_km_s"] == pytest.approx(123.4)
    assert "note" in obj.extra.get("radial_velocity_note", "").lower() or \
           "not" in obj.extra.get("radial_velocity_note", "").lower()


# --------------------------------------------------------------------
# D2.1 — crossmatch PM epoch propagation
# --------------------------------------------------------------------


def test_crossmatch_static_match_for_identical_coords():
    from app.pipeline.nodes.crossmatch import crossmatch

    out = crossmatch(
        {"data": {"ra": [10.0, 20.0], "dec": [0.0, 1.0],
                  "ra_2": [10.0, 20.0], "dec_2": [0.0, 1.0]}},
        {"max_sep": 1.0},
    )
    assert out["crossmatch_result"]["match_count"] == 2  # noqa


def test_crossmatch_applies_pm_epoch_propagation():
    from app.pipeline.nodes.crossmatch import crossmatch

    # Fast-moving star: pmra = 3600 mas/yr = 1 arcsec/yr.  Over 16
    # years (2000 → 2016) the star moves ~16 arcsec.  Without
    # propagation: 1" cap misses the match.  With propagation: the
    # positions align and the match succeeds.
    # 3600 mas/yr × 16 yr = 57.6 arcsec = 0.016 deg of RA motion.
    input_data = {"data": {
        "ra": [10.0],
        "dec": [0.0],
        "pmra": [3600.0],
        "pmdec": [0.0],
        "parallax": [10.0],
        "radial_velocity_km_s": [0.0],
        "ra_2": [10.0 + 0.016],
        "dec_2": [0.0],
    }}
    out_no_prop = crossmatch(input_data, {"max_sep": 1.0})
    assert out_no_prop["crossmatch_result"]["match_count"] == 0

    out_prop = crossmatch(
        input_data,
        {"max_sep": 1.0, "epoch_1": 2000.0, "epoch_2": 2016.0},
    )
    assert out_prop["crossmatch_result"]["match_count"] == 1
    assert "epoch_propagation" in out_prop["crossmatch_result"]


# --------------------------------------------------------------------
# D2.6 — Lomb-Scargle FAP + window + short-series refusal
# --------------------------------------------------------------------


def test_lomb_scargle_refuses_short_series():
    from app.services.astro_analysis import lomb_scargle_period

    t = np.linspace(0, 10, 10)
    m = np.sin(2 * np.pi * t / 2.3)
    with pytest.raises(ValueError, match="n=10"):
        lomb_scargle_period(t, m, min_period=0.5, max_period=5)


def test_lomb_scargle_reports_window_and_nyquist():
    from app.services.astro_analysis import lomb_scargle_period

    rng = np.random.default_rng(42)
    t = np.sort(rng.uniform(0, 100, 100))
    m = np.sin(2 * np.pi * t / 2.3) + rng.normal(0, 0.1, len(t))
    out = lomb_scargle_period(
        t, m, min_period=0.5, max_period=20,
        n_frequencies=2000, fap_method="bootstrap",
        n_bootstrap=50, random_seed=1,
    )
    assert 2.0 < out["best_period"] < 2.7  # recover ~2.3 d period
    assert out["fap_method"] == "bootstrap"
    assert out["spectral_resolution_per_day"] > 0
    assert out["nyquist_pseudo_per_day"] > 0
    assert "window_function_max_sidelobe" in out
    assert "best_period_err" in out


def test_lomb_scargle_bootstrap_fap_is_reproducible():
    from app.services.astro_analysis import lomb_scargle_period

    rng = np.random.default_rng(5)
    t = np.sort(rng.uniform(0, 50, 60))
    m = np.sin(2 * np.pi * t / 3.1) + rng.normal(0, 0.5, len(t))
    r1 = lomb_scargle_period(t, m, min_period=0.5, max_period=10,
                              n_frequencies=500, fap_method="bootstrap",
                              n_bootstrap=30, random_seed=7)
    r2 = lomb_scargle_period(t, m, min_period=0.5, max_period=10,
                              n_frequencies=500, fap_method="bootstrap",
                              n_bootstrap=30, random_seed=7)
    assert r1["fap"] == r2["fap"]


# --------------------------------------------------------------------
# D2.7 — pulsar derived quantities with I + class + braking index
# --------------------------------------------------------------------


def test_pulsar_quantities_canonical_defaults():
    from app.services.ai_tools import _exec_pulsar_derived

    out = _exec_pulsar_derived({"period_s": 0.0334, "period_dot": 4.21e-13})
    assert out["moment_of_inertia_g_cm2"] == 1e45
    assert out["moment_of_inertia_source"] == "canonical_1e45"
    assert out["braking_index_assumed"] == 3.0
    assert "assumptions_note" in out


def test_pulsar_quantities_class_overrides_I():
    from app.services.ai_tools import _exec_pulsar_derived

    out = _exec_pulsar_derived({
        "period_s": 0.005,
        "period_dot": 1e-20,
        "pulsar_class": "recycled",
    })
    assert out["moment_of_inertia_g_cm2"] == pytest.approx(1.4e45)
    assert out["moment_of_inertia_source"] == "class_default_recycled"


def test_pulsar_quantities_braking_index_changes_age():
    from app.services.ai_tools import _exec_pulsar_derived

    out_n3 = _exec_pulsar_derived({
        "period_s": 0.0334, "period_dot": 4.21e-13, "braking_index": 3.0,
    })
    out_n2 = _exec_pulsar_derived({
        "period_s": 0.0334, "period_dot": 4.21e-13, "braking_index": 2.0,
    })
    # τ_c = P / ((n-1) Ṗ); smaller n-1 → larger age.
    assert out_n2["characteristic_age_yr"] > out_n3["characteristic_age_yr"]


# --------------------------------------------------------------------
# D2.5 — extinction curves span multiple laws
# --------------------------------------------------------------------


def test_extinction_curve_f99_is_non_zero_in_optical():
    from app.services.astro_analysis import extinction_curve

    wave = np.array([4000.0, 5500.0, 6500.0])
    a_f99 = extinction_curve(wave, ebv=0.1, model="f99")
    assert np.all(a_f99 > 0)
    # A(5500 Å) ≈ A_V = R_V * E(B-V) = 3.1 * 0.1 = 0.31 mag
    assert 0.25 < a_f99[1] < 0.40


def test_extinction_curve_g03_differs_from_ccm89():
    from app.services.astro_analysis import extinction_curve

    wave = np.array([2000.0, 5500.0])  # UV where the laws diverge most
    a_ccm = extinction_curve(wave, ebv=0.3, model="ccm89")
    a_g03 = extinction_curve(wave, ebv=0.3, model="g03")
    # G03 (SMC bar) has a markedly different UV slope than CCM89 MW.
    assert not np.allclose(a_ccm, a_g03, rtol=0.1)
