"""In-process CAMB theory CMB power-spectrum tool (M1-A, 2026-05-31).

``compute_theory_cmb_spectrum`` runs CAMB to produce the lensed TT/TE/EE angular
power spectrum from a flat-ΛCDM parameter set.  camb is a heavy, Fortran-backed
*optional* dependency: it is imported lazily here (never at module load), so the
hot import path of the rest of the backend is unaffected.

Two hard safety properties live in the async handler that wraps this function
(``_exec_compute_theory_cmb_spectrum`` in ai_tools_cosmology):
  * every call is serialized behind a process-global ``asyncio.Lock`` — CAMB's
    Fortran kernel is NOT re-entrant, and two ``get_results()`` calls executing
    concurrently in one worker segfault the process (measured 8/8 at lmax=2500);
    the agent loop runs a turn's tool calls via ``asyncio.gather``, so the
    concurrency that would crash the single 2 GB web worker is real, not
    hypothetical;
  * the call runs off the event loop via ``asyncio.to_thread``.

This module's own job is the third guard: validate + clamp inputs *before* CAMB
is touched (a bad parameter set is the realistic crash vector), and cap ``lmax``
(peak RSS and the segfault blast radius both scale with it).
"""
from __future__ import annotations

import math
from typing import Any

LMAX_DEFAULT = 2500
LMAX_MAX = 2500  # capped: peak RSS (~110-250 MB) and crash surface scale with lmax

# Hard (low, high) bounds; reject before invoking CAMB.
_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "H0": (40.0, 100.0),
    "ombh2": (0.015, 0.035),
    "omch2": (0.05, 0.30),
    "ns": (0.85, 1.05),
    "As": (1.0e-9, 4.0e-9),
    "tau": (0.01, 0.12),
}
# Planck 2018 base-ΛCDM, used when a parameter is omitted.
_DEFAULTS: dict[str, float] = {
    "H0": 67.36, "ombh2": 0.02237, "omch2": 0.1200,
    "ns": 0.9649, "As": 2.1e-9, "tau": 0.0544,
}
CAMB_CITATION = "Lewis, Challinor & Lasenby 2000 (ApJ 538:473, arXiv:astro-ph/9911177)"


class TheorySpectrumError(ValueError):
    """Bad input — raised before CAMB is invoked so the handler returns a clean
    FAILED envelope instead of risking a Fortran crash on garbage parameters."""


def _coerce_params(raw: dict[str, Any]) -> tuple[dict[str, float], int]:
    params: dict[str, float] = {}
    for name, (low, high) in _PARAM_BOUNDS.items():
        val = raw.get(name)
        if val is None:
            params[name] = _DEFAULTS[name]
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            raise TheorySpectrumError(f"{name} must be a number, got {val!r}")
        if not math.isfinite(f):
            raise TheorySpectrumError(f"{name} must be finite")
        if not (low <= f <= high):
            raise TheorySpectrumError(f"{name}={f} is outside the allowed range [{low}, {high}]")
        params[name] = f

    lmax_raw = raw.get("lmax")
    if lmax_raw is None:
        lmax = LMAX_DEFAULT
    else:
        try:
            lmax = int(lmax_raw)
        except (TypeError, ValueError):
            raise TheorySpectrumError(f"lmax must be an integer, got {lmax_raw!r}")
        if lmax < 2:
            raise TheorySpectrumError("lmax must be >= 2")
        lmax = min(lmax, LMAX_MAX)  # clamp high lmax down to the cap, never reject
    return params, lmax


def compute_theory_cmb_spectrum(raw_input: dict[str, Any]) -> dict[str, Any]:
    """Synchronous CAMB compute (run inside ``asyncio.to_thread`` by the handler).

    Validates + clamps inputs, lazily imports camb, and returns a tool-result
    envelope with the lensed TT/TE/EE spectrum (downsampled so the model gets a
    compact, quotable summary), the first acoustic-peak features, and provenance.
    Raises ``TheorySpectrumError`` on bad input and lets ``ImportError`` propagate
    so the handler can report a structured UNAVAILABLE result.
    """
    params, lmax = _coerce_params(raw_input)

    import camb  # lazy heavy Fortran-backed optional dep; may raise ImportError
    import numpy as np

    pars = camb.set_params(
        H0=params["H0"], ombh2=params["ombh2"], omch2=params["omch2"],
        ns=params["ns"], As=params["As"], tau=params["tau"],
    )
    pars.set_for_lmax(lmax, lens_potential_accuracy=1)
    results = camb.get_results(pars)
    # 'total' is the lensed total D_l = l(l+1)C_l/2pi in muK^2, shape (lmax+1, 4)
    # with columns [TT, EE, BB, TE].
    total = results.get_cmb_power_spectra(pars, CMB_unit="muK")["total"]

    ell = np.arange(total.shape[0])
    dl_tt = total[:, 0]
    dl_ee = total[:, 1]
    dl_te = total[:, 3]

    # First TT acoustic peak (the canonical l ~ 220 feature), searched in a band.
    lo, hi = 100, min(350, lmax)
    peak_ell: int | None = None
    peak_height: float | None = None
    if hi > lo:
        seg = dl_tt[lo:hi + 1]
        peak_ell = lo + int(np.argmax(seg))
        peak_height = float(dl_tt[peak_ell])

    # Downsample for the model / JSON: the full array is ~lmax points; keep ~200.
    step = max(1, lmax // 200)
    idx = list(range(2, total.shape[0], step))
    spectrum = {
        "ell": [int(ell[i]) for i in idx],
        "dl_tt_muK2": [round(float(dl_tt[i]), 4) for i in idx],
        "dl_te_muK2": [round(float(dl_te[i]), 4) for i in idx],
        "dl_ee_muK2": [round(float(dl_ee[i]), 4) for i in idx],
    }

    return {
        "success": True,
        "__tool_status__": "COMPLETED",
        "analysis_status": "THEORY_SPECTRUM_COMPUTED",
        "computation": "camb_theory_cmb_powerspectrum",
        "model": "flat_lcdm",
        "input_parameters": params,
        "lmax": lmax,
        "lmax_requested": raw_input.get("lmax", LMAX_DEFAULT),
        "units": "D_l = l(l+1)C_l/2pi in microK^2 (lensed total)",
        "derived": {
            "first_acoustic_peak_ell_tt": peak_ell,
            "first_acoustic_peak_height_tt_muK2": (
                round(peak_height, 2) if peak_height is not None else None
            ),
        },
        "spectrum": spectrum,
        "n_spectrum_points": len(idx),
        "provenance": {
            "theory_code": f"CAMB {getattr(camb, '__version__', 'unknown')}",
            "citation": CAMB_CITATION,
            "note": (
                "Forward theory prediction from CAMB for the given parameters; "
                "not a data measurement, posterior, or fit to observations."
            ),
        },
        "__message_to_model__": (
            "These are CAMB theory C_l (D_l) predictions for the specified "
            "cosmological parameters — a forward-model output, NOT a measurement "
            "or a posterior. Describe them as 'CAMB-predicted' and cite CAMB."
        ),
    }
