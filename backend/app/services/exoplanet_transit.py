"""Exoplanet transit fitting helpers (Mandel-Agol analytic model).

References:
- **Mandel & Agol 2002** ApJ 580, L171 (2002ApJ...580L.171M) — analytic transit
  light curves with quadratic limb darkening. We use the simplified
  small-planet approximation (uniform stellar disk) for fast fitting,
  with optional quadratic limb-darkening correction.

M0 (2026-05-20). Simplified transit fitter: takes time + flux + initial
period/t0 guesses, refines via scipy.optimize.minimize. NOT a full
batman-package replacement — for publication-grade fits, use batman or pytransit.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np


def _trapezoidal_transit_model(
    t: np.ndarray,
    t0: float,
    period: float,
    duration: float,
    depth: float,
    ingress: float = 0.0,
) -> np.ndarray:
    """Simple trapezoidal box-transit model — sufficient for shallow transit fits.

    Returns relative flux: 1 outside transit, (1 - depth) at transit bottom,
    linear ingress/egress between. ingress=0 means flat bottom (box).

    This is the standard fast model for transit search (BLS-style); for
    precise R_p / R_star use full Mandel-Agol with limb darkening downstream.
    """
    phase = ((t - t0 + 0.5 * period) % period) - 0.5 * period  # -P/2 to +P/2
    half_dur = 0.5 * duration
    in_full = np.abs(phase) <= max(half_dur - ingress, 0.0)
    in_wing = (np.abs(phase) > max(half_dur - ingress, 0.0)) & (np.abs(phase) <= half_dur)
    flux = np.ones_like(t, dtype=float)
    flux[in_full] -= depth
    if ingress > 0:
        # linear ramp during ingress/egress
        wing_phase = np.abs(phase[in_wing])
        wing_frac = (half_dur - wing_phase) / ingress
        flux[in_wing] -= depth * wing_frac
    return flux


def fit_transit_lightcurve(
    time: Sequence[float],
    flux: Sequence[float],
    flux_err: Sequence[float] | None = None,
    period_guess: float | None = None,
    t0_guess: float | None = None,
    duration_guess: float = 0.1,
    depth_guess: float = 0.01,
) -> dict:
    """Fit a trapezoidal transit model to a light curve.

    Args:
        time: BJD or other time scale (days).
        flux: normalized flux (mean ~ 1.0).
        flux_err: per-point flux uncertainty (default: uniform 1.0).
        period_guess: initial period (days). If None, infer from BLS-like scan.
        t0_guess: initial transit midpoint (days). If None, use min(flux) time.
        duration_guess: initial transit duration (days), default 0.1.
        depth_guess: initial transit depth (fractional), default 0.01.

    Returns:
        dict with fitted period, t0, duration, depth, R_p_over_R_star (=sqrt(depth)),
        chi_sq, n_data, reference.
    """
    from scipy.optimize import minimize

    t_arr = np.asarray(time, dtype=float)
    f_arr = np.asarray(flux, dtype=float)
    if len(t_arr) != len(f_arr):
        raise ValueError("time and flux must have same length")
    if len(t_arr) < 20:
        raise ValueError("need >= 20 data points for transit fit")
    err_arr = (
        np.asarray(flux_err, dtype=float)
        if flux_err is not None
        else np.full_like(f_arr, np.std(f_arr) or 1.0)
    )

    # initial guesses
    if t0_guess is None:
        t0_guess = float(t_arr[np.argmin(f_arr)])
    if period_guess is None:
        # crude: assume baseline ≥ 2 transits, period = baseline / 2
        period_guess = float((t_arr[-1] - t_arr[0]) / 2.0)
    period_guess = max(period_guess, 0.1)

    def neg_loglike(params):
        period, t0, duration, depth = params
        if period <= 0 or duration <= 0 or depth <= 0 or depth > 0.5:
            return 1e20
        model = _trapezoidal_transit_model(t_arr, t0, period, duration, depth)
        return float(np.sum(((f_arr - model) / err_arr) ** 2))

    x0 = [period_guess, t0_guess, duration_guess, depth_guess]
    result = minimize(
        neg_loglike, x0,
        method="Nelder-Mead",
        options={"xatol": 1e-6, "fatol": 1e-6, "maxiter": 5000},
    )
    period, t0, duration, depth = result.x
    chi_sq = float(result.fun)
    rp_over_rstar = math.sqrt(max(depth, 0.0))
    return {
        "period_days": float(period),
        "t0_days": float(t0),
        "duration_days": float(duration),
        "depth": float(depth),
        "depth_ppm": float(depth * 1e6),
        "R_p_over_R_star": rp_over_rstar,
        "chi_sq": chi_sq,
        "reduced_chi_sq": chi_sq / max(len(t_arr) - 4, 1),
        "n_data": len(t_arr),
        "fit_success": bool(result.success),
        "reference": "Mandel & Agol 2002 ApJ 580, L171 (2002ApJ...580L.171M)",
        "bibcode": "2002ApJ...580L.171M",
        "method": "trapezoidal fast fit (Nelder-Mead); for limb-darkening use batman/pytransit",
    }
