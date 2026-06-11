"""luminosity_units L_solar <-> L_prime conversion correctness.

Physical expectation (Solomon & Vanden Bout 2005 Eqs. 1 & 3 — the (1+z)
factors cancel, so the conversion is REDSHIFT-INDEPENDENT):
    log10(L'/L_solar) = 10.495 - 3·log10(ν_rest_GHz)

Locks three things:
1. [CII]: L_prime - L_solar ≈ +0.658 dex, the SAME at any z
2. The offset is redshift-INDEPENDENT and scales as ν_rest⁻³
3. Conversion is reversible (L_solar → L_prime → L_solar round-trips cleanly)
"""

from __future__ import annotations

import math

import pytest

from app.services.luminosity_units import (
    LINE_REST_FREQ_GHZ,
    convert_log_l_prime_to_l_solar,
    convert_log_l_solar_to_l_prime,
    convert_row_luminosity_inplace,
    lookup_line_rest_freq_ghz,
)


# ---------- ν_rest lookup ----------


def test_cii_rest_freq_is_1900_5369_ghz() -> None:
    """[CII] 158 μm frequency must be exactly 1900.5369 GHz (NIST/SPLATALOGUE).
    Off by one decimal place shifts fit intercept by ~0.01 dex — unacceptable."""
    assert math.isclose(lookup_line_rest_freq_ghz("[CII]"), 1900.5369, abs_tol=1e-4)
    assert lookup_line_rest_freq_ghz("[CII]158") == 1900.5369


def test_co_rest_freqs_present() -> None:
    """CO(1-0)..CO(7-6) primary rotational transitions must all be in the table."""
    expected = {
        "CO(1-0)": 115.27120,
        "CO(2-1)": 230.53800,
        "CO(3-2)": 345.79599,
        "CO(7-6)": 806.65180,
    }
    for line, freq in expected.items():
        assert lookup_line_rest_freq_ghz(line) == pytest.approx(freq, abs=1e-3)


def test_unknown_line_returns_none() -> None:
    """Unknown line label returns None — prevents silent fallback to wrong frequency."""
    assert lookup_line_rest_freq_ghz("CII") is None  # missing brackets
    assert lookup_line_rest_freq_ghz("nonexistent") is None
    assert lookup_line_rest_freq_ghz("") is None


# ---------- L_solar → L_prime 物理预期 ----------


def test_cii_offset_is_about_plus_0_66_dex() -> None:
    """[CII] conversion offset is ≈ +0.658 dex, redshift-independent.

    Solomon & Vanden Bout 2005 Eqs. 1 & 3: L'/L = 3.125e10 · ν_rest⁻³ with the
    (1+z) factors cancelled. For [CII] (ν_rest=1900.5369 GHz):
    10.495 - 3·log10(1900.5369) = +0.658 dex. An ALPINE log(L_CII/L_sun)=8.5
    source therefore has log L' ≈ 9.16.
    """
    log_l_prime = convert_log_l_solar_to_l_prime(8.5, "[CII]", redshift=5.0)
    assert log_l_prime is not None
    assert log_l_prime == pytest.approx(9.158, abs=0.01)


def test_cii_offset_is_redshift_independent() -> None:
    """The offset is the SAME at any z (the (1+z) factors cancel). The old code
    carried a spurious +2·log10(1+z) term; this locks that it is gone."""
    delta_z1 = convert_log_l_solar_to_l_prime(0.0, "[CII]", redshift=1.0)
    delta_z5 = convert_log_l_solar_to_l_prime(0.0, "[CII]", redshift=5.0)
    delta_z10 = convert_log_l_solar_to_l_prime(0.0, "[CII]", redshift=10.0)
    assert delta_z1 == pytest.approx(delta_z5, abs=1e-9)
    assert delta_z5 == pytest.approx(delta_z10, abs=1e-9)
    assert delta_z5 == pytest.approx(0.658, abs=0.01)


def test_co10_at_z2_smaller_offset_than_cii_at_z2() -> None:
    """CO(1-0) ν_rest is 16x lower than [CII] → -3 log ν term contributes +3·log(16.5) ≈ +3.65 dex more.
    So at the same z, CO(1-0) L_prime value is larger than [CII] (relative to L_solar)."""
    cii_z2 = convert_log_l_solar_to_l_prime(8.0, "[CII]", redshift=2.0)
    co_z2 = convert_log_l_solar_to_l_prime(8.0, "CO(1-0)", redshift=2.0)
    assert co_z2 > cii_z2  # CO(1-0) ν_rest smaller, offset larger


def test_explicit_nu_rest_override() -> None:
    """Explicit nu_rest_ghz parameter should take priority over line_id table lookup."""
    # use [CII] line_id but explicitly override ν_rest to 100 GHz
    val_a = convert_log_l_solar_to_l_prime(8.0, "[CII]", redshift=2.0)
    val_b = convert_log_l_solar_to_l_prime(8.0, "[CII]", redshift=2.0, nu_rest_ghz=100.0)
    assert val_a != val_b


# ---------- 可逆性 ----------


@pytest.mark.parametrize("z", [0.5, 1.0, 2.5, 5.0, 7.0])
def test_round_trip_is_identity(z: float) -> None:
    """L_solar → L_prime → L_solar must round-trip without loss."""
    original = 8.7
    forward = convert_log_l_solar_to_l_prime(original, "[CII]", redshift=z)
    back = convert_log_l_prime_to_l_solar(forward, "[CII]", redshift=z)
    assert back == pytest.approx(original, abs=1e-9)


# ---------- 错误路径 ----------


def test_missing_redshift_returns_none() -> None:
    """Without redshift, conversion is impossible (depends on (1+z)² term)."""
    assert convert_log_l_solar_to_l_prime(8.5, "[CII]", redshift=None) is None
    assert convert_log_l_solar_to_l_prime(8.5, "[CII]", redshift=float("nan")) is None
    assert convert_log_l_solar_to_l_prime(8.5, "[CII]", redshift=-0.5) is None


def test_missing_line_id_and_no_override_returns_none() -> None:
    """No line_id and no explicit nu_rest_ghz parameter → None."""
    assert convert_log_l_solar_to_l_prime(8.5, line_id=None, redshift=2.0) is None
    assert convert_log_l_solar_to_l_prime(8.5, line_id="unknown", redshift=2.0) is None


def test_invalid_log_l_returns_none() -> None:
    assert convert_log_l_solar_to_l_prime(float("nan"), "[CII]", 5.0) is None
    assert convert_log_l_solar_to_l_prime(float("inf"), "[CII]", 5.0) is None


# ---------- row in-place 转换 ----------


def test_convert_row_l_solar_to_l_prime_records_audit_fields() -> None:
    """convert_row_luminosity_inplace must record transformed_from (old value) +
    transformed_to unit label so LLM/reviewers can see which rows were converted."""
    row = {
        "source_name": "ALPINE-12345",
        "redshift": 5.0,
        "line_id": "[CII]",
        "log_luminosity": 8.5,
    }
    out = convert_row_luminosity_inplace(row, "L_prime")
    assert out["luminosity_kind"] == "L_prime"
    assert out["log_luminosity"] == pytest.approx(9.158, abs=0.01)
    assert out["log_luminosity_transformed_from"] == 8.5
    assert out["log_luminosity_transformed_to"] == "L_prime"
    # original row must not be modified (immutable style preserved)
    assert row["log_luminosity"] == 8.5
    assert "luminosity_kind" not in row


def test_convert_row_already_target_kind_is_noop() -> None:
    """Row already at target_kind is not converted again, but luminosity_kind field is added."""
    row = {"luminosity_kind": "L_prime", "log_luminosity": 10.7, "redshift": 5.0, "line_id": "[CII]"}
    out = convert_row_luminosity_inplace(row, "L_prime")
    assert out["log_luminosity"] == 10.7
    assert "log_luminosity_transformed_from" not in out


def test_convert_row_legacy_default_is_l_solar() -> None:
    """Old cache rows without luminosity_kind → treated as L_solar (current default), real conversion to L_prime."""
    row = {"log_luminosity": 8.5, "redshift": 5.0, "line_id": "[CII]"}
    # No luminosity_kind set
    out = convert_row_luminosity_inplace(row, "L_prime")
    assert out["luminosity_kind"] == "L_prime"
    assert out["log_luminosity"] == pytest.approx(9.158, abs=0.01)


def test_convert_row_missing_redshift_records_unit_error() -> None:
    """No z → _unit_error field explains the reason, but log_luminosity is untouched so caller can reject."""
    row = {"log_luminosity": 8.5, "line_id": "[CII]"}  # no redshift
    out = convert_row_luminosity_inplace(row, "L_prime")
    assert "_unit_error" in out
    assert "redshift" in out["_unit_error"].lower()
    assert out["log_luminosity"] == 8.5  # untouched
    assert out.get("luminosity_kind") != "L_prime"


def test_convert_row_unknown_line_records_unit_error() -> None:
    row = {"log_luminosity": 8.5, "line_id": "MysteryLine", "redshift": 5.0}
    out = convert_row_luminosity_inplace(row, "L_prime")
    assert "_unit_error" in out
    assert "MysteryLine" in out["_unit_error"]


def test_line_rest_freq_dict_completeness() -> None:
    """LINE_REST_FREQ_GHZ must contain the main [CII] entry + full CO(1-0)..CO(7-6) set.
    Guards against accidentally deleting an entry."""
    required = ["[CII]", "[CII]158", "CO(1-0)", "CO(2-1)", "CO(3-2)",
                "CO(4-3)", "CO(5-4)", "CO(6-5)", "CO(7-6)"]
    for line in required:
        assert line in LINE_REST_FREQ_GHZ, f"missing required line {line!r}"
        assert LINE_REST_FREQ_GHZ[line] > 0
