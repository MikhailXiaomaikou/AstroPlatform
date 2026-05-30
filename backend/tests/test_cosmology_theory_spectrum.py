"""Tests for the CAMB theory CMB power-spectrum tool (M1-A).

Validation/wiring tests run everywhere; the real-CAMB compute + the critical
concurrency-serialization guard are guarded with importorskip('camb') because
camb is a heavy optional dep absent from the local dev .venv (CI installs
requirements.txt, which pins it).
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.cosmology_theory_spectrum import (
    LMAX_DEFAULT,
    LMAX_MAX,
    TheorySpectrumError,
    _coerce_params,
    compute_theory_cmb_spectrum,
)


# ── input validation / clamping (no camb needed) ─────────────────────────────

def test_coerce_params_fills_planck_defaults_when_omitted():
    params, lmax = _coerce_params({})
    assert params["H0"] == pytest.approx(67.36)
    assert params["ombh2"] == pytest.approx(0.02237)
    assert lmax == LMAX_DEFAULT


def test_coerce_params_rejects_out_of_range():
    with pytest.raises(TheorySpectrumError, match="H0"):
        _coerce_params({"H0": 200.0})
    with pytest.raises(TheorySpectrumError, match="tau"):
        _coerce_params({"tau": 0.9})


def test_coerce_params_rejects_nonnumeric_and_nonfinite():
    with pytest.raises(TheorySpectrumError):
        _coerce_params({"H0": "abc"})
    with pytest.raises(TheorySpectrumError):
        _coerce_params({"ns": float("nan")})


def test_coerce_params_clamps_high_lmax_instead_of_rejecting():
    _params, lmax = _coerce_params({"lmax": 5000})
    assert lmax == LMAX_MAX  # clamped, not an error
    with pytest.raises(TheorySpectrumError):
        _coerce_params({"lmax": 1})  # below the floor IS an error


# ── tool wiring (no camb needed) ─────────────────────────────────────────────

def test_tool_is_registered_everywhere():
    from app.services.ai_tools_cosmology import COSMOLOGY_TOOL_NAMES, COSMOLOGY_TOOL_SCHEMAS
    from app.services.result_provenance import _COMPUTE_TOOLS

    assert "compute_theory_cmb_spectrum" in COSMOLOGY_TOOL_NAMES
    assert "compute_theory_cmb_spectrum" in _COMPUTE_TOOLS
    assert any(s["name"] == "compute_theory_cmb_spectrum" for s in COSMOLOGY_TOOL_SCHEMAS)


def test_handler_bad_input_returns_clean_failed_envelope():
    from app.services.ai_tools_cosmology import _exec_compute_theory_cmb_spectrum

    out = asyncio.run(_exec_compute_theory_cmb_spectrum({"H0": 999.0}))
    assert out["success"] is False
    assert out["__tool_status__"] == "FAILED"
    assert out["error_class"] == "invalid_theory_spectrum_input"
    assert out["__do_not_claim__"] is True


def test_dispatch_routes_compute_theory_cmb_spectrum():
    from app.services.ai_tools_cosmology import dispatch_cosmology

    out = asyncio.run(dispatch_cosmology("compute_theory_cmb_spectrum", {"H0": 999.0}))
    assert out is not None and out["success"] is False  # routed (not None) + validated


# ── real CAMB compute (needs camb) ───────────────────────────────────────────

def test_compute_spectrum_reproduces_planck_first_acoustic_peak():
    pytest.importorskip("camb")
    out = compute_theory_cmb_spectrum({"lmax": 2500})
    assert out["success"] is True
    assert out["__tool_status__"] == "COMPLETED"
    peak_ell = out["derived"]["first_acoustic_peak_ell_tt"]
    peak_height = out["derived"]["first_acoustic_peak_height_tt_muK2"]
    # Planck base-LCDM first TT acoustic peak sits at l ~ 220, ~5700-6000 muK^2.
    assert 200 <= peak_ell <= 240, peak_ell
    assert 5400.0 <= peak_height <= 6100.0, peak_height
    # Spectrum payload is present and self-consistent.
    spec = out["spectrum"]
    assert len(spec["ell"]) == len(spec["dl_tt_muK2"]) == out["n_spectrum_points"]
    assert "CAMB" in out["provenance"]["theory_code"]
    assert out["input_parameters"]["H0"] == pytest.approx(67.36)


def test_handler_concurrent_calls_are_serialized_not_crashing():
    """REGRESSION GUARD for the load-bearing _CAMB_LOCK. CAMB's Fortran kernel is
    non-reentrant: two get_results() running concurrently in one process segfault
    it. The agent loop dispatches a turn's tool calls via asyncio.gather, so
    without the lock this exact pattern crashes the worker. With the lock the two
    calls serialize and both succeed. (If the lock were removed, this test would
    segfault the test process, not merely fail.)"""
    pytest.importorskip("camb")
    from app.services.ai_tools_cosmology import _exec_compute_theory_cmb_spectrum

    async def _two():
        return await asyncio.gather(
            _exec_compute_theory_cmb_spectrum({"lmax": 2000}),
            _exec_compute_theory_cmb_spectrum({"lmax": 2000, "H0": 70.0}),
        )

    a, b = asyncio.run(_two())
    assert a["success"] is True and b["success"] is True
    assert a["input_parameters"]["H0"] == pytest.approx(67.36)
    assert b["input_parameters"]["H0"] == pytest.approx(70.0)
