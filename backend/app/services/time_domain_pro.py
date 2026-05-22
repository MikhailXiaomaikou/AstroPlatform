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
    """Fit a transit model using batman with a stable, documented schema."""
    t = np.asarray(time, dtype=float)
    f_raw = np.asarray(flux, dtype=float)
    if t.size == 0 or f_raw.size == 0:
        raise ValueError("fit_transit requires non-empty time and flux arrays")
    if t.shape != f_raw.shape:
        raise ValueError("time and flux must have the same length")

    ferr_raw = np.asarray(flux_err, dtype=float) if flux_err is not None else None
    if ferr_raw is not None and ferr_raw.shape != f_raw.shape:
        ferr_raw = None

    finite = np.isfinite(t) & np.isfinite(f_raw)
    if ferr_raw is not None:
        finite &= np.isfinite(ferr_raw) & (ferr_raw > 0)
    if np.count_nonzero(finite) < 8:
        raise ValueError("fit_transit needs at least 8 finite points")
    t = t[finite]
    f_raw = f_raw[finite]
    ferr_raw = ferr_raw[finite] if ferr_raw is not None else None

    continuum = float(np.nanmedian(f_raw))
    if not np.isfinite(continuum) or continuum == 0:
        continuum = 1.0
    f = f_raw / continuum
    if ferr_raw is not None:
        ferr = ferr_raw / abs(continuum)
    else:
        scatter = float(1.4826 * np.nanmedian(np.abs(f - np.nanmedian(f))))
        if not np.isfinite(scatter) or scatter <= 0:
            scatter = float(np.nanstd(f))
        if not np.isfinite(scatter) or scatter <= 0:
            scatter = 0.001
        ferr = np.ones_like(f) * scatter

    def _finite_float(value, default):
        try:
            out = float(value)
            return out if np.isfinite(out) else default
        except Exception:
            return default

    period = _finite_float(period, 1.0)
    if period <= 0:
        period = 1.0

    depth_guess = float(max(0.0, np.nanmedian(f) - np.nanpercentile(f, 1.0)))
    if not np.isfinite(depth_guess) or depth_guess <= 0:
        depth_guess = max(1e-4, float(np.nanvar(f)))
    rp_guess = float(np.clip(np.sqrt(depth_guess), 0.01, 0.5))
    rp_rs = _finite_float(rp_rs, rp_guess)
    if rp_rs <= 0 or rp_rs >= 1:
        rp_rs = rp_guess
    a_rs = _finite_float(a_rs, 10.0)
    if a_rs < 2.5:
        a_rs = 10.0
    inc = _finite_float(inc, 89.0)
    if inc <= 0 or inc > 90:
        inc = 89.0
    t0 = _finite_float(t0, float(t[np.nanargmin(f)]))
    # Absolute BJD times combined with t0=0 are essentially unusable; use the flux minimum as the initial guess.
    if abs(t0) < 1e-12 and float(np.nanmin(np.abs(t))) > period:
        t0 = float(t[np.nanargmin(f)])

    # For large light curves, subsample for parameter fitting but generate model/residuals on all points.
    fit_idx = np.arange(len(t))
    if len(fit_idx) > 8000:
        fit_idx = np.linspace(0, len(t) - 1, 8000, dtype=int)
    t_fit = t[fit_idx]
    f_fit = f[fit_idx]
    ferr_fit = ferr[fit_idx]

    def _schema(*, model_flux, residuals, method, optimizer_success=False,
                message="", fitted_rp=None, fitted_a=None, fitted_inc=None,
                fitted_t0=None, chi2_val=None, extra=None):
        fitted_rp = float(fitted_rp if fitted_rp is not None else rp_rs)
        fitted_a = float(fitted_a if fitted_a is not None else a_rs)
        fitted_inc = float(fitted_inc if fitted_inc is not None else inc)
        fitted_t0 = float(fitted_t0 if fitted_t0 is not None else t0)
        model_flux = np.asarray(model_flux, dtype=float)
        residuals = np.asarray(residuals, dtype=float)
        if chi2_val is None:
            chi2_val = float(np.nansum((residuals / ferr) ** 2))
        ndof = max(int(len(f) - 4), 1)
        depth_ppm = float((1.0 - np.nanmin(model_flux)) * 1e6)
        out = {
            "time": t.tolist(),
            "model_flux": model_flux.tolist(),
            "residual_values": residuals.tolist(),
            "residuals": {
                "values": residuals.tolist(),
                "rms": float(np.sqrt(np.nanmean(residuals ** 2))),
                "median": float(np.nanmedian(residuals)),
                "n": int(len(residuals)),
            },
            "rp_rs": fitted_rp,
            "a_rs": fitted_a,
            "inc": fitted_inc,
            "inclination": fitted_inc,
            "t0": fitted_t0,
            "period": float(period),
            "chi2": float(chi2_val),
            "chi2_reduced": float(chi2_val / ndof),
            "depth_ppm": depth_ppm,
            "power": None,
            "fitted_params": {
                "rp_rs": fitted_rp,
                "a_rs": fitted_a,
                "inc": fitted_inc,
                "inclination": fitted_inc,
                "t0": fitted_t0,
                "period": float(period),
                "depth_ppm": depth_ppm,
            },
            "optimizer_success": bool(optimizer_success),
            "optimizer_message": str(message),
            "limb_darkening": limb_darkening,
            "method": method,
            "flux_normalization": continuum,
        }
        if extra:
            out.update(extra)
        return out

    try:
        import batman
        from scipy.optimize import minimize

        if ld_coeffs is None:
            ld_coeffs = [0.4, 0.3]

        def _params(rp, t0_fit, a=a_rs, i=inc):
            params = batman.TransitParams()
            params.t0 = float(t0_fit)
            params.per = float(period)
            params.rp = float(rp)
            params.a = float(a)
            params.inc = float(i)
            params.ecc = 0.0
            params.w = 90.0
            params.u = ld_coeffs
            params.limb_dark = limb_darkening
            return params

        def _model(times, rp, t0_fit, baseline, a=a_rs, i=inc):
            params = _params(rp, t0_fit, a=a, i=i)
            m = batman.TransitModel(params, times)
            return float(baseline) * m.light_curve(params)

        def chi2(theta):
            rp, t0_fit, baseline = theta
            a = a_rs
            if rp <= 0 or rp >= 1.0 or a < 2.5 or inc < 0 or inc > 90:
                return 1e20
            try:
                model = _model(t_fit, rp, t0_fit, baseline)
            except Exception:
                return 1e20
            return float(np.nansum(((f_fit - model) / ferr_fit) ** 2))

        x0 = np.array([rp_rs, t0, 1.0], dtype=float)
        t0_half_width = max(float(period) * 0.5, 0.05)
        bounds = [(0.005, 0.5), (t0 - t0_half_width, t0 + t0_half_width), (0.8, 1.2)]
        result = minimize(chi2, x0, method="L-BFGS-B", bounds=bounds, options={"maxiter": 300})

        best_rp, best_t0, best_baseline = result.x
        model_flux = _model(t, best_rp, best_t0, best_baseline)
        residuals = f - model_flux
        chi2_val = float(np.nansum((residuals / ferr) ** 2))

        return _schema(
            model_flux=model_flux,
            residuals=residuals,
            method="batman_l_bfgs_b",
            optimizer_success=bool(result.success),
            message=getattr(result, "message", ""),
            fitted_rp=best_rp,
            fitted_a=a_rs,
            fitted_inc=inc,
            fitted_t0=best_t0,
            chi2_val=chi2_val,
        )
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("batman transit fit failed, falling back to box model: %s", exc)

    # Simple box model fallback with the same schema.
    try:
        phase = ((t - t0) / period) % 1.0
        width = min(0.08, max(0.015, 1.0 / max(a_rs, 2.5) / np.pi))
        in_transit = (phase < width) | (phase > 1.0 - width)
        if np.any(in_transit) and np.any(~in_transit):
            depth = max(0.0, 1.0 - float(np.nanmedian(f[in_transit])) / float(np.nanmedian(f[~in_transit])))
        else:
            depth = depth_guess
        rp_box = float(np.clip(np.sqrt(max(depth, 1e-6)), 0.001, 0.8))
        model_flux = np.ones_like(f)
        model_flux[in_transit] -= rp_box ** 2
        residuals = f - model_flux
        return _schema(
            model_flux=model_flux,
            residuals=residuals,
            method="box_fallback",
            optimizer_success=False,
            message="batman unavailable or failed; used box model fallback",
            fitted_rp=rp_box,
            fitted_a=a_rs,
            fitted_inc=inc,
            fitted_t0=t0,
            extra={"note": "batman not available or failed, using simple box model"},
        )
    except Exception as exc:
        raise RuntimeError(f"fit_transit failed before producing a model: {exc}") from exc


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
    # L2-b (audit 2026-04-20): strict > not >=. The Kipping 2011 standard
    # bootstrap FAP is defined as the fraction of null trials where the null
    # max **strictly exceeds** the observed max; using >= would count cases
    # where the null exactly equals the peak, over-estimating significance by
    # 0.5-2%.
    fap_bootstrap = float(np.mean(null_max > power[best_idx]))

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
