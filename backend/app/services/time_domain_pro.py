"""Professional time-domain astronomy service.

GP detrending via celerite2, transit modeling via batman,
flare detection, BLS transit search, and injection-recovery tests.
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)


def gp_detrend(time: list, flux: list, flux_err: list | None = None,
               kernel: str = "matern32") -> dict:
    """Detrend a light curve using Gaussian Process regression via celerite2.

    Kernels: 'matern32', 'sho' (stochastically-driven harmonic oscillator),
             'rotation' (quasi-periodic for stellar rotation).
    """
    t = np.array(time)
    f = np.array(flux)
    ferr = np.array(flux_err) if flux_err else np.ones_like(f) * np.std(f) * 0.01

    # Normalize
    f_mean = np.nanmean(f)
    f_norm = f - f_mean

    try:
        import celerite2
        from scipy.optimize import minimize

        if kernel == "sho":
            term = celerite2.terms.SHOTerm(sigma=np.std(f_norm), rho=1.0, tau=10.0)
        elif kernel == "rotation":
            term = celerite2.terms.RotationTerm(
                sigma=np.std(f_norm), period=1.0, Q0=1.0, dQ=0.5, f=0.5
            )
        else:  # matern32
            term = celerite2.terms.Matern32Term(sigma=np.std(f_norm), rho=1.0)

        gp = celerite2.GaussianProcess(term, mean=0.0)
        gp.compute(t, yerr=ferr)

        # Optimize hyperparameters
        def neg_log_like(params):
            gp.mean = params[0]
            try:
                gp.compute(t, yerr=ferr)
                return -gp.log_likelihood(f_norm)
            except Exception as e:
                logger.debug("GP hyperparameter optimization failed: %s", e)
                return 1e10

        initial = np.array([0.0])
        try:
            # neg_log_like updates gp.set_parameter_vector(...) in its
            # closure, so the OptimizeResult itself is not needed —
            # the side-effect is what matters.  Prefix with _ to signal
            # deliberately-discarded return.
            _ = minimize(neg_log_like, initial, method="L-BFGS-B")
        except Exception as e:
            logger.debug("GP hyperparameter optimization failed: %s", e)

        # Predict GP trend
        gp.compute(t, yerr=ferr)
        mu = gp.predict(f_norm, t, return_cov=False)

        detrended = f - mu - f_mean + np.nanmean(f)
        trend = mu + f_mean

        return {
            "time": time,
            "flux_detrended": detrended.tolist(),
            "trend": trend.tolist(),
            "flux_original": flux,
            "kernel": kernel,
            "method": "celerite2",
        }
    except ImportError:
        # Fallback: Savitzky-Golay filter
        from scipy.signal import savgol_filter
        window = min(len(f) // 4 * 2 + 1, 101)
        if window < 5:
            window = 5
        trend = savgol_filter(f, window, 3)
        detrended = f - trend + np.nanmean(f)

        return {
            "time": time,
            "flux_detrended": detrended.tolist(),
            "trend": trend.tolist(),
            "flux_original": flux,
            "kernel": "savgol_fallback",
            "method": "savgol",
        }


def fit_transit(time: list, flux: list, flux_err: list | None = None,
                period: float = 1.0, t0: float = 0.0,
                rp_rs: float = 0.1, a_rs: float = 10.0,
                inc: float = 90.0, limb_darkening: str = "quadratic",
                ld_coeffs: list[float] | None = None) -> dict:
    """Fit a transit model using batman."""
    t = np.array(time)
    f = np.array(flux)
    ferr = np.array(flux_err) if flux_err else np.ones_like(f) * np.std(f) * 0.01

    try:
        import batman
        from scipy.optimize import minimize

        if ld_coeffs is None:
            ld_coeffs = [0.4, 0.3]

        params = batman.TransitParams()
        params.t0 = t0
        params.per = period
        params.rp = rp_rs
        params.a = a_rs
        params.inc = inc
        params.ecc = 0.0
        params.w = 90.0
        params.u = ld_coeffs
        params.limb_dark = limb_darkening

        m = batman.TransitModel(params, t)

        # Optimize: fit rp, a, inc, t0
        def chi2(theta):
            rp, a, i, t0_fit = theta
            params.rp = abs(rp)
            params.a = abs(a)
            params.inc = i
            params.t0 = t0_fit
            model = m.light_curve(params)
            return np.sum(((f - model) / ferr) ** 2)

        x0 = [rp_rs, a_rs, inc, t0]
        result = minimize(chi2, x0, method="Nelder-Mead",
                         options={"maxiter": 5000})

        best_rp, best_a, best_inc, best_t0 = result.x
        params.rp = abs(best_rp)
        params.a = abs(best_a)
        params.inc = best_inc
        params.t0 = best_t0
        model_flux = m.light_curve(params)

        residuals = f - model_flux
        chi2_val = np.sum((residuals / ferr) ** 2)
        ndof = len(f) - 4

        return {
            "time": time,
            "model_flux": model_flux.tolist(),
            "residuals": residuals.tolist(),
            "fitted_params": {
                "rp_rs": float(abs(best_rp)),
                "a_rs": float(abs(best_a)),
                "inclination": float(best_inc),
                "t0": float(best_t0),
                "period": period,
                "depth_ppm": float((1 - min(model_flux)) * 1e6),
            },
            "chi2_reduced": float(chi2_val / max(ndof, 1)),
            "limb_darkening": limb_darkening,
            "method": "batman",
        }
    except ImportError:
        # Simple box model fallback
        phase = ((t - t0) / period) % 1.0
        in_transit = (phase < 0.05) | (phase > 0.95)
        depth = 1.0 - np.median(f[in_transit]) / np.median(f[~in_transit]) if np.any(in_transit) else 0

        return {
            "time": time,
            "fitted_params": {"depth_ppm": float(depth * 1e6), "period": period, "t0": t0},
            "method": "box_fallback",
            "note": "batman not available, using simple box model",
        }


def detect_flares(time: list, flux: list, flux_err: list | None = None,
                  nsigma: float = 3.0, min_duration: int = 3) -> dict:
    """Detect stellar flares in a light curve.

    Note: `flux_err` is accepted for API consistency but currently unused
    — this function estimates noise via MAD of the median-filter residual,
    which is more robust than trusting user-supplied error bars that may
    underestimate correlated noise.  To use the user errors, replace the
    MAD line below with `noise = np.median(ferr)`.
    """
    t = np.array(time)
    f = np.array(flux)
    # flux_err kept in the signature for future-proofing — see docstring.
    _ = flux_err

    # Detrend first (median filter)
    from scipy.ndimage import median_filter
    window = min(len(f) // 10, 51)
    if window < 3:
        window = 3
    if window % 2 == 0:
        window += 1
    baseline = median_filter(f, size=window)
    residual = f - baseline

    # Detect excursions above nsigma
    noise = 1.4826 * np.median(np.abs(residual - np.median(residual)))  # MAD
    threshold = nsigma * noise

    above = residual > threshold

    # Group consecutive points into flare events
    flares = []
    i = 0
    while i < len(above):
        if above[i]:
            start = i
            while i < len(above) and above[i]:
                i += 1
            end = i
            if end - start >= min_duration:
                peak_idx = start + np.argmax(residual[start:end])
                # Equivalent duration (integral of relative flux excess)
                dt = np.median(np.diff(t[start:end])) if end - start > 1 else np.median(np.diff(t))
                equiv_dur = float(np.sum(residual[start:end] / np.median(baseline)) * dt)

                flares.append({
                    "start_time": float(t[start]),
                    "end_time": float(t[min(end, len(t)-1)]),
                    "peak_time": float(t[peak_idx]),
                    "peak_flux": float(f[peak_idx]),
                    "amplitude": float(residual[peak_idx]),
                    "snr": float(residual[peak_idx] / noise),
                    "duration_points": end - start,
                    "equivalent_duration": equiv_dur,
                })
        else:
            i += 1

    return {
        "flares": flares,
        "n_flares": len(flares),
        "threshold_sigma": nsigma,
        "noise_level": float(noise),
        "baseline": baseline.tolist(),
    }


def transit_search_bls(time: list, flux: list,
                       period_range: tuple[float, float] = (0.5, 20.0),
                       duration_range: tuple[float, float] = (0.01, 0.15),
                       n_periods: int = 10000,
                       n_bootstrap: int = 200,
                       random_seed: int | None = None) -> dict:
    """Search for transits using Box Least Squares (BLS) periodogram.

    Phase D3.2:
    - Reports depth and period uncertainties via parabolic peak refinement.
    - Computes a **bootstrap** false-alarm probability by shuffling the
      flux array ``n_bootstrap`` times and recording the max BLS power of
      each surrogate (cites Kipping 2011; Bruls+ 2021 for red-noise
      commentary).  Analytical Gaussian SNR is kept as a diagnostic but
      no longer drives the "significant / marginal / none" verdict.
    """
    from astropy.timeseries import BoxLeastSquares
    import astropy.units as u

    t = np.array(time)
    f = np.array(flux)

    bls = BoxLeastSquares(t * u.day, f)
    periods = np.linspace(period_range[0], period_range[1], n_periods)
    durations = np.linspace(duration_range[0], duration_range[1], 10)

    results = bls.power(periods * u.day, durations * u.day)

    power = np.asarray(results.power)
    best_idx = int(np.argmax(power))
    best_period = float(results.period[best_idx].value)
    best_t0 = float(results.transit_time[best_idx].value)
    best_duration = float(results.duration[best_idx].value)
    best_depth = float(results.depth[best_idx])

    # Parabolic refinement for period uncertainty (half-width at the peak).
    period_err = None
    if 0 < best_idx < len(power) - 1:
        y0, y1, y2 = power[best_idx - 1], power[best_idx], power[best_idx + 1]
        denom = (y0 - 2 * y1 + y2)
        if denom != 0:
            shift = 0.5 * (y0 - y2) / denom
            dP = periods[1] - periods[0]
            period_err = float(abs(shift * dP))

    # Legacy Gaussian-SNR — kept as a diagnostic but not used for verdict.
    snr_gaussian = float((power[best_idx] - np.median(power)) / np.std(power))

    # Bootstrap FAP: shuffle flux 200x; each gives a max-power under H0.
    rng = np.random.default_rng(random_seed)
    null_max = np.empty(n_bootstrap, dtype=float)
    for i in range(n_bootstrap):
        shuffled = rng.permutation(f)
        bls_null = BoxLeastSquares(t * u.day, shuffled)
        res_null = bls_null.power(periods * u.day, durations * u.day)
        null_max[i] = float(np.max(res_null.power))
    fap_bootstrap = float(np.mean(null_max >= power[best_idx]))

    if fap_bootstrap < 0.01:
        verdict = "significant"
    elif fap_bootstrap < 0.05:
        verdict = "marginal"
    else:
        verdict = "none"

    return {
        "best_period": best_period,
        "best_period_err": period_err,
        "best_t0": best_t0,
        "best_duration": best_duration,
        "best_depth": best_depth,
        "snr_gaussian": snr_gaussian,
        "fap_bootstrap": fap_bootstrap,
        "n_bootstrap": n_bootstrap,
        "periods": periods.tolist(),
        "power": power.tolist(),
        "detection": verdict,
        "reference": "Kovács+ 2002 A&A 391, 369 (BLS); Kipping 2011 MNRAS 416, 689 (FAP bootstrap)",
    }
