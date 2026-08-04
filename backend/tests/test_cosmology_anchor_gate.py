"""Cosmology-anchor numeric-comparison gate (2026-06-11).

Live-test regression: the LFR demo turn (extract ALPINE -> fit_line_lfr) was
hard-blocked with "Reply withheld" + an EMPTY violation list because the model
honestly declared the fit's assumed cosmology — "Planck18 (H0 = 67.36,
Om = 0.3153)" — and `_unsupported_cosmology_anchor_numeric_comparison` treated
any "Planck...H0...digit" mention without the planck2018_compressed dataset as
anchor laundering. The system prompt REQUIRES declaring the assumed cosmology,
and fit_line_lfr's result carries it in cosmology_manifest — two defenses were
fighting each other.

Locks:
1. Declaring a tool-declared (cosmology_manifest) value does NOT fire.
2. Anchor values NO tool declared still fire (Planck H0=70, SH0ES 73.04,
   H0LiCOW 73.3, Planck S8=0.832) — matched against the MANIFEST pool only,
   not the full tool universe (a ~10^3-value universe would launder any
   anchor near a coincidental FWHM/flux).
3. Token matching is word-bounded: the "H0" inside "SH0ES"/"H0LiCOW" must not
   be read as the parameter (it made the gate read the next "H0"'s digit as
   the value).
4. Qualitative tension prose with no number attached to the parameter does
   not fire.
"""
from __future__ import annotations

from app.api.chat import _unsupported_cosmology_anchor_numeric_comparison as fires
from app.services.claim_validator import value_supported_by_cosmology_manifest

# fit_line_lfr-shaped accumulator entry with the Planck18 preset manifest
# (values mirror app/services/cosmology.py PRESETS["planck18"]).
_FIT_TOOL_RESULTS = [
    {
        "tool": "fit_line_lfr",
        "input": {"cache_key": "latest_literature_tables"},
        "result": {
            "success": True,
            "fit_method": "bayesian_xyerr_linmix",
            "n_used": 74,
            # Dense unrelated numerics — the gate must NOT use these to
            # support an anchor (70.2 is ~within 1% of a fabricated H0=70).
            "fwhm_values": [526.0, 70.2, 73.5, 312.0, 13.0],
            "cosmology_manifest": {
                "name": "planck18",
                "H0_km_s_Mpc": 67.36,
                "H0_err": 0.54,
                "Om0": 0.3153,
                "sigma8": 0.8111,
                "ns": 0.9649,
                "w0": -1.0,
                "wa": 0.0,
                "bibcode": "2020A&A...641A...6P",
            },
        },
    },
]

_COMPARE_TOOL_RESULTS = [
    {
        "tool": "compare_luminosity_distances",
        "input": {
            "target_cosmology": "riess22_shoes",
            "comparison_mode": "h0_anchors",
        },
        "result": {
            "success": True,
            "__tool_status__": "PARTIAL",
            "data_origin": "cached_real",
            "current_cosmology": {
                "name": "planck18",
                "H0_km_s_Mpc": 67.36,
                "H0_err": 0.54,
                "bibcode": "2020A&A...641A...6P",
            },
            "target_cosmology": {
                "name": "riess22_shoes",
                "H0_km_s_Mpc": 73.04,
                "H0_err": 1.04,
                "bibcode": "2022ApJ...934L...7R",
            },
            "anchor_comparison": {
                "target_minus_baseline_pct": 8.432,
            },
        },
    }
]


def test_declared_manifest_cosmology_does_not_fire():
    reply = "Luminosities use Planck18 cosmology (H0 = 67.36, Om = 0.3153)."
    assert fires(reply, _FIT_TOOL_RESULTS) is False


def test_declared_manifest_sigma8_does_not_fire():
    assert fires("Planck18 sigma_8 = 0.8111 was assumed.", _FIT_TOOL_RESULTS) is False


def test_fabricated_planck_h0_still_fires():
    # 70.0 is within 1% of the unrelated FWHM value 70.2 — the manifest-only
    # pool is what keeps this blocked.
    assert fires("Planck measured H0 = 70.0 in 2018.", _FIT_TOOL_RESULTS) is True


def test_shoes_anchor_comparison_still_fires():
    # 73.04 is within 1% of the unrelated FWHM value 73.5; also guards the
    # word-boundary fix ("SH0ES" contains "H0").
    assert fires("Compare with SH0ES H0 = 73.04.", _FIT_TOOL_RESULTS) is True


def test_compare_tool_declares_both_curated_h0_anchors():
    reply = "Planck H0 = 67.36 and SH0ES H0 = 73.04, an 8.432% offset."
    assert fires(reply, _COMPARE_TOOL_RESULTS) is False
    assert value_supported_by_cosmology_manifest(67.36, _COMPARE_TOOL_RESULTS) is True
    assert value_supported_by_cosmology_manifest(73.04, _COMPARE_TOOL_RESULTS) is True


def test_compare_tool_does_not_support_unreturned_anchor():
    assert fires("H0LiCOW time-delay H0 = 74.9.", _COMPARE_TOOL_RESULTS) is True


def test_h0licow_anchor_comparison_still_fires():
    assert fires("H0LiCOW time-delay H0 = 73.3 agrees.", _FIT_TOOL_RESULTS) is True


def test_planck_s8_not_in_manifest_still_fires():
    assert fires("Planck CMB gives S8 = 0.832.", _FIT_TOOL_RESULTS) is True


def test_no_tools_anchor_still_fires():
    assert fires("Planck gives H0 = 67.4.", []) is True


def test_qualitative_tension_prose_does_not_fire():
    # The pattern's trailing \d is satisfied by the "0" inside "SH0ES" /
    # "H0" — there is no numeric value attached to the parameter.
    assert fires("Planck and SH0ES disagree about H0.", _FIT_TOOL_RESULTS) is False


def test_dataset_selected_turn_keeps_old_behavior():
    # When planck2018_compressed was actually selected this turn, anchor
    # mentions are allowed exactly as before.
    tool_results = [
        {
            "tool": "run_likelihood_chain",
            "result": {"datasets_used": ["planck2018_compressed"]},
        },
    ]
    assert fires("Planck compressed prior pins H0 = 67.36.", tool_results) is False


def test_value_supported_pool_is_manifest_only():
    assert value_supported_by_cosmology_manifest(67.36, _FIT_TOOL_RESULTS) is True
    assert value_supported_by_cosmology_manifest(67.4, _FIT_TOOL_RESULTS) is True  # ±1%
    assert value_supported_by_cosmology_manifest(70.0, _FIT_TOOL_RESULTS) is False
    assert value_supported_by_cosmology_manifest(73.04, _FIT_TOOL_RESULTS) is False
    # bibcode digits must not leak into the pool (2020A&A...641A...6P)
    assert value_supported_by_cosmology_manifest(2020.0, _FIT_TOOL_RESULTS) is False
    assert value_supported_by_cosmology_manifest(641.0, _FIT_TOOL_RESULTS) is False
