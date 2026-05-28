#!/usr/bin/env python3
"""Exoplanet science-regression benchmark suite.

Pinned checks against ``app/services/exoplanet_{physical,transit}``.
Each baseline exercises one Seager & Mallén-Ornelas (2003) / Kepler-law
example and asserts the recovered value sits in a fixed tolerance —
science-regression smoke that does NOT exercise the LLM.

Usage:
    python scripts/benchmarks/run_exoplanet_benchmarks.py
    python scripts/benchmarks/run_exoplanet_benchmarks.py --json out.json
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


def bench_hd209458b_equilibrium_temperature() -> dict[str, Any]:
    """HD 209458 b T_eq with T*=6065 K, R*=1.203 R⊙, a=0.04747 AU, A=0.3.
    Published value sits at ~1450 K with full redistribution; with the
    default redistribution_factor=1.0 the formula returns ~1347 K. Allow
    [1300, 1400] K."""
    from app.services.exoplanet_physical import compute_equilibrium_temperature
    out = compute_equilibrium_temperature(
        T_star_K=6065.0, R_star_solar=1.203, a_au=0.04747, albedo=0.3,
    )
    t_eq = float(out["T_eq_K"])
    return {
        "pass": 1300.0 < t_eq < 1400.0,
        "T_eq_K": round(t_eq, 2),
        "target": "HD 209458 b T_eq in [1300, 1400] K",
    }


def bench_earth_equilibrium_temperature() -> dict[str, Any]:
    """Sun-like at 1 AU, A=0.3 → T_eq ≈ 255 K (the canonical Earth value)."""
    from app.services.exoplanet_physical import compute_equilibrium_temperature
    out = compute_equilibrium_temperature(
        T_star_K=5778.0, R_star_solar=1.0, a_au=1.0, albedo=0.3,
    )
    t_eq = float(out["T_eq_K"])
    return {
        "pass": 245.0 < t_eq < 265.0,
        "T_eq_K": round(t_eq, 2),
        "target": "Earth-equivalent T_eq in [245, 265] K",
    }


def bench_earth_density() -> dict[str, Any]:
    """Mp=1 M⊕, Rp=1 R⊕ → ρ = 5.51 ± 0.05 g/cm³ (Earth bulk density)."""
    from app.services.exoplanet_physical import compute_planet_density
    out = compute_planet_density(M_earth=1.0, R_earth=1.0)
    rho = float(out["density_g_cm3"])
    return {
        "pass": 5.4 < rho < 5.6,
        "density_g_cm3": round(rho, 4),
        "target": "Earth bulk density 5.51 ± 0.1 g/cm³",
    }


def bench_earth_sun_transit_depth() -> dict[str, Any]:
    """Rp=1 R⊕, R*=1 R⊙ → transit depth (Rp/R*)² ≈ 84 ppm (Seager 2003)."""
    from app.services.exoplanet_physical import compute_transit_depth
    out = compute_transit_depth(R_p_earth=1.0, R_star_solar=1.0)
    depth_ppm = float(out["depth_ppm"])
    return {
        "pass": 83.0 < depth_ppm < 85.0,
        "depth_ppm": round(depth_ppm, 3),
        "target": "Earth-Sun transit depth in [83, 85] ppm",
    }


def bench_kepler_a_from_period_earth() -> dict[str, Any]:
    """Kepler III inverse: P=365.25 d around 1 M⊙ → a = 1 AU within 1e-3."""
    from app.services.exoplanet_physical import kepler_a_from_period
    a_au = float(kepler_a_from_period(P_days=365.25, M_star_solar=1.0))
    return {
        "pass": abs(a_au - 1.0) < 1e-3,
        "a_au": round(a_au, 6),
        "target": "P=365.25 d, M*=1 M⊙ → a in [0.999, 1.001] AU",
    }


def bench_trapezoidal_transit_model_geometry() -> dict[str, Any]:
    """The trapezoidal transit model must obey its physical extremes:
    mid-transit flux = 1 - depth, flux outside the duration window
    = 1.0, and ingress/egress symmetric. Signature is
    (t, t0, period, duration, depth, ingress)."""
    import numpy as np
    from app.services.exoplanet_transit import _trapezoidal_transit_model

    t0 = 0.0
    period = 10.0          # long so a single in-phase sample stays in-transit
    duration = 0.20
    depth = 0.012
    ingress = 0.05
    half_dur = 0.5 * duration

    # mid-transit, edge of flat bottom (just inside in_full), well out of transit
    times = np.array([t0, t0 + (half_dur - ingress) * 0.5, t0 + 2.0])
    flux = _trapezoidal_transit_model(times, t0, period, duration, depth, ingress)
    mid_depth_ok = abs((1.0 - depth) - flux[0]) < 1e-9
    in_flat_ok = abs((1.0 - depth) - flux[1]) < 1e-9
    far_baseline_ok = abs(1.0 - flux[2]) < 1e-9

    # Ingress/egress symmetry: equal offsets from the wing midpoint
    wing_offset = ingress * 0.5
    sym_times = np.array([
        -(half_dur - ingress) - wing_offset,
        +(half_dur - ingress) + wing_offset,
    ])
    sym_flux = _trapezoidal_transit_model(sym_times, t0, period, duration, depth, ingress)
    sym_ok = abs(sym_flux[0] - sym_flux[1]) < 1e-9

    return {
        "pass": bool(mid_depth_ok and in_flat_ok and far_baseline_ok and sym_ok),
        "mid_transit_flux": float(flux[0]),
        "in_flat_flux": float(flux[1]),
        "far_baseline_flux": float(flux[2]),
        "ingress_egress_symmetric": bool(sym_ok),
        "target": "trapezoidal: mid=1-depth, baseline=1.0, ingress/egress symmetric",
    }


BENCHMARKS: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    ("hd209458b_equilibrium_temperature", bench_hd209458b_equilibrium_temperature),
    ("earth_equilibrium_temperature", bench_earth_equilibrium_temperature),
    ("earth_density", bench_earth_density),
    ("earth_sun_transit_depth", bench_earth_sun_transit_depth),
    ("kepler_a_from_period_earth", bench_kepler_a_from_period_earth),
    ("trapezoidal_transit_model_geometry", bench_trapezoidal_transit_model_geometry),
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
        "suite": "exoplanet_benchmarks",
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
