#!/usr/bin/env python3
"""Solar-system science-regression benchmark suite.

Pinned, deterministic checks against the pure-function kernels in
``app/services/solar_system_{thermo,phot,dynamics,taxonomy}``. Each
benchmark exercises one canonical physical example and asserts the
recovered value lies within a fixed tolerance — a science-regression
smoke screen that does NOT exercise the LLM.

Usage:
    python scripts/benchmarks/run_solar_system_benchmarks.py
    python scripts/benchmarks/run_solar_system_benchmarks.py --json out.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
import traceback
from typing import Any, Callable

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def bench_ceres_neatm_diameter() -> dict[str, Any]:
    """Harris (1998) D = 1329 / sqrt(p_V) · 10^(-H/5) on Ceres (H=3.34, p_V=0.090)
    must recover Ceres's true diameter ~ 940 km within 5%."""
    from app.services.solar_system_thermo import harris_diameter_km
    d_km = float(harris_diameter_km(H=3.34, p_V=0.090))
    return {
        "pass": 900 < d_km < 1000,
        "diameter_km": round(d_km, 2),
        "target": "Ceres D in [900, 1000] km from Harris (true ≈ 940 km)",
    }


def bench_hg_phase_function_at_zero() -> dict[str, Any]:
    """HG phase function at α=0 must be exactly 1.0 (no phase darkening at
    opposition); a regression here means the phase basis functions broke."""
    from app.services.solar_system_phot import hg_phase_function
    phi = float(hg_phase_function(alpha_deg=0.0, G=0.15))
    return {
        "pass": abs(phi - 1.0) < 1e-6,
        "phi_at_zero": round(phi, 8),
        "target": "phi(alpha=0) == 1.0",
    }


def bench_light_time_one_au() -> dict[str, Any]:
    """Light-time at Earth-Sun (Δ=1 AU) is 499.0 s = 8.3167 min within rounding."""
    from app.services.solar_system_phot import light_time_minutes
    t_min = float(light_time_minutes(delta_au=1.0))
    return {
        "pass": abs(t_min - 8.3167) < 0.001,
        "light_time_min": round(t_min, 5),
        "target": "1 AU == 8.3167 ± 0.001 min",
    }


def bench_kepler_third_law_earth() -> dict[str, Any]:
    """Kepler III: a = 1 AU → P = 1 yr exactly (definitional)."""
    from app.services.solar_system_dynamics import orbital_period_years
    p_yr = float(orbital_period_years(a_au=1.0))
    return {
        "pass": abs(p_yr - 1.0) < 1e-6,
        "period_yr": round(p_yr, 6),
        "target": "a=1 AU → P=1 yr",
    }


def bench_circular_orbit_q_eq_Q() -> dict[str, Any]:
    """For e=0, perihelion q = aphelion Q = a (any drift signals
    a regression in (q, Q) = (a(1-e), a(1+e))."""
    from app.services.solar_system_dynamics import perihelion_aphelion_au
    q, Q = perihelion_aphelion_au(a_au=1.0, e=0.0)
    return {
        "pass": abs(float(q) - 1.0) < 1e-9 and abs(float(Q) - 1.0) < 1e-9,
        "q_au": float(q),
        "Q_au": float(Q),
        "target": "e=0 → q=Q=a=1.0",
    }


def bench_harris_round_trip() -> dict[str, Any]:
    """harris_diameter_km and harris_albedo must be self-consistent:
    p_V→D, then (H, D)→p_V should recover the input p_V."""
    from app.services.solar_system_thermo import harris_diameter_km, harris_albedo
    H = 5.0
    p_V_in = 0.20
    D = harris_diameter_km(H=H, p_V=p_V_in)
    p_V_back = harris_albedo(H=H, D_km=D)
    return {
        "pass": abs(float(p_V_back) - p_V_in) < 1e-6,
        "p_V_in": p_V_in,
        "diameter_km": round(float(D), 4),
        "p_V_recovered": round(float(p_V_back), 6),
        "target": "harris D⇄albedo round-trip exact",
    }


BENCHMARKS: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    ("ceres_neatm_diameter", bench_ceres_neatm_diameter),
    ("hg_phase_function_at_zero", bench_hg_phase_function_at_zero),
    ("light_time_one_au", bench_light_time_one_au),
    ("kepler_third_law_earth", bench_kepler_third_law_earth),
    ("circular_orbit_q_eq_Q", bench_circular_orbit_q_eq_Q),
    ("harris_round_trip", bench_harris_round_trip),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--name", type=str, default=None)
    args = ap.parse_args()

    results: dict[str, Any] = {}
    for name, fn in BENCHMARKS:
        if args.name and name != args.name:
            continue
        try:
            results[name] = fn()
        except Exception as exc:
            results[name] = {
                "pass": False,
                "error": str(exc),
                "error_class": exc.__class__.__name__,
                "traceback": traceback.format_exc(limit=4),
            }

    payload = {
        "suite": "solar_system_benchmarks",
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "n_benchmarks": len(results),
        "n_pass": sum(1 for r in results.values() if r.get("pass")),
        "n_fail": sum(1 for r in results.values() if not r.get("pass")),
        "results": results,
    }
    print(json.dumps(payload, indent=2, default=float))
    if args.json:
        with open(args.json, "w") as fp:
            json.dump(payload, fp, indent=2, default=float)

    return 0 if all(r.get("pass") for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
