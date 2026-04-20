"""Pre-built astronomical analysis modules and publication-quality plot templates.

These functions are available inside the AI's run_python sandbox.
They produce journal-standard (ApJ/MNRAS) figures and compute common
astronomical diagnostics without requiring the user to write boilerplate.
"""

import inspect
import logging

import numpy as np

logger = logging.getLogger(__name__)

# ── Publication Figure Defaults ──

PUB_STYLE = {
    "font.family": "serif",
    "font.serif": ["Noto Serif CJK SC", "Noto Serif", "DejaVu Serif", "Times New Roman"],
    "font.size": 12,
    "axes.labelsize": 14,
    "axes.titlesize": 14,
    "xtick.labelsize": 11,
    "ytick.labelsize": 11,
    "legend.fontsize": 10,
    "figure.figsize": (8, 6),
    "figure.dpi": 150,
    "axes.linewidth": 1.0,
    "xtick.major.width": 0.8,
    "ytick.major.width": 0.8,
    "xtick.minor.visible": True,
    "ytick.minor.visible": True,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "xtick.top": True,
    "ytick.right": True,
    "savefig.bbox": "tight",
    "savefig.dpi": 300,
}


def pub_style():
    """Apply publication-quality matplotlib style (ApJ/MNRAS standard)."""
    import matplotlib.pyplot as plt
    plt.rcParams.update(PUB_STYLE)


def pub_figure(nrows=1, ncols=1, figsize=None, **kwargs):
    """Create a publication-quality figure with proper styling."""
    import matplotlib.pyplot as plt
    pub_style()
    if figsize is None:
        # ApJ column widths: single=3.5in, double=7in
        w = 3.5 * ncols if ncols <= 2 else 7
        h = 3.5 * nrows
        figsize = (w, h)
    fig, axes = plt.subplots(nrows, ncols, figsize=figsize, **kwargs)
    return fig, axes


# ── Isochrone Helpers ──


def get_isochrone(log_age, metallicity=0.0, photometric_system="gaia"):
    """Get a PARSEC isochrone for a given age and metallicity.

    Tries local API cache first, falls back to CMD 3.7 web API.

    Args:
        log_age: log10(age/yr), e.g. 8.0 for 100 Myr, 10.0 for 10 Gyr
        metallicity: [M/H], default 0.0 (solar)
        photometric_system: 'gaia' or 'sdss'

    Returns:
        pandas DataFrame with columns including mass, logTe, logg,
        and photometric magnitudes (Gmag, G_BPmag, G_RPmag for Gaia;
        umag, gmag, rmag, imag, zmag for SDSS).
    """
    import httpx
    import pandas as pd

    # Try local API first
    try:
        params = {
            "log_age": log_age,
            "metallicity": metallicity,
        }
        resp = httpx.get(
            "http://127.0.0.1:8000/api/isochrones/get",
            params=params,
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                df = pd.DataFrame(data)
                logger.info(
                    "Loaded isochrone from local API: log_age=%.2f, [M/H]=%.2f (%d rows)",
                    log_age, metallicity, len(df),
                )
                return df
    except Exception:
        pass

    # Fall back to CMD 3.7 web API
    logger.info(
        "Falling back to CMD API for isochrone: log_age=%.2f, [M/H]=%.2f",
        log_age, metallicity,
    )
    from app.services.parsec_fetcher import fetch_isochrone

    return fetch_isochrone(
        log_age=log_age,
        metallicity=metallicity,
        photometric_system=photometric_system,
    )


# ── Isochrone Fitting ──

def fit_isochrone(
    bp_rp,
    abs_mag,
    mag_err=None,
    color_err=None,
    method="grid",
    photometric_system="gaia",
    age_range=(6.5, 10.3),
    met_range=(-2.0, 0.5),
    dm_range=(0.0, 20.0),
    av_range=(0.0, 2.0),
    n_grid_age=20,
    n_grid_met=5,
    n_walkers=32,
    n_steps=500,
    n_burn=200,
    seed=42,
    compute_errors=False,
):
    """Fit PARSEC isochrones to observed CMD data.

    Finds the best-fit (age, [M/H], distance_modulus, A_V) by minimising the
    chi-squared sum of nearest-point distances on the colour-magnitude diagram.

    Args:
        bp_rp: observed colour array (e.g. G_BP - G_RP)
        abs_mag: observed magnitude array (apparent or absolute)
        mag_err: magnitude uncertainties (optional, default 0.1)
        color_err: colour uncertainties (optional, default 0.05)
        method: 'grid' for grid+Nelder-Mead, 'mcmc' for emcee MCMC
        photometric_system: 'gaia' or 'sdss'
        age_range: (min, max) log10(age/yr) for search
        met_range: (min, max) [M/H]
        dm_range: (min, max) distance modulus
        av_range: (min, max) A_V extinction
        n_grid_age: grid points in age dimension
        n_grid_met: grid points in metallicity dimension
        n_walkers: MCMC walkers (method='mcmc')
        n_steps: MCMC steps (method='mcmc')
        n_burn: MCMC burn-in (method='mcmc')
        seed: random seed
        compute_errors: if True, run Monte Carlo error propagation (100
            iterations) after the best fit to estimate parameter uncertainties

    Returns:
        dict with keys:
            best_fit: {log_age, metallicity, distance_modulus, A_V}
            chi2: best-fit chi-squared
            chi2_reduced: chi2 / dof
            method: fitting method used
            errors_source: 'provided' or 'estimated' (how uncertainties were set)
            (if mcmc) uncertainties: {param: [p16, p50, p84]}
            (if mcmc) corner_fig: matplotlib Figure
            (if mcmc) chain: raw MCMC chain (n_walkers * n_steps, 4)
            (if compute_errors) uncertainties: {param: [p16, p84], method, n_successful}
    """
    from scipy.optimize import minimize

    bp_rp = np.asarray(bp_rp, dtype=float)
    abs_mag = np.asarray(abs_mag, dtype=float)

    # Synchronously filter NaN/Inf from both arrays
    valid = np.isfinite(bp_rp) & np.isfinite(abs_mag)
    bp_rp = bp_rp[valid]
    abs_mag = abs_mag[valid]
    if mag_err is not None:
        mag_err = np.asarray(mag_err, dtype=float)[valid]
    if color_err is not None:
        color_err = np.asarray(color_err, dtype=float)[valid]

    # Default uncertainties — adaptive estimates if not provided
    if mag_err is None:
        mag_err = 0.02 + 0.005 * np.abs(abs_mag - np.nanmedian(abs_mag))
        errors_source = "estimated"
    else:
        errors_source = "provided"
    if color_err is None:
        color_err = 0.02 + 0.01 * np.abs(bp_rp - np.nanmedian(bp_rp))

    n_data = len(bp_rp)
    if n_data < 5:
        raise ValueError(f"Need at least 5 data points after NaN filtering, got {n_data}")

    # Gaia DR3 extinction coefficients (Wang & Chen 2019, ApJ 877, 116)
    # Solar metallicity, A/F/G stars, R_V = 3.1
    # Note: previous value 0.836 was incorrectly attributed to C&VB 2018;
    # 0.836 is actually Jordi+ 2010 (pre-DR2). Modern consensus is ~0.789.
    a_g_over_av = 0.789
    e_bprp_over_av = 0.415

    def _get_model_cmd(log_age, met):
        """Fetch isochrone and extract colour-magnitude columns."""
        try:
            iso = get_isochrone(log_age, met, photometric_system)
            if iso is None or len(iso) == 0:
                return None, None
            # Gaia columns
            if "G_BPmag" in iso.columns and "G_RPmag" in iso.columns and "Gmag" in iso.columns:
                model_color = iso["G_BPmag"].values - iso["G_RPmag"].values
                model_mag = iso["Gmag"].values
            elif "G_BPmag" in [c for c in iso.columns] or "gmag" in iso.columns:
                # SDSS fallback
                model_color = iso.iloc[:, -3].values - iso.iloc[:, -1].values
                model_mag = iso.iloc[:, -2].values
            else:
                return None, None
            mask = np.isfinite(model_color) & np.isfinite(model_mag)
            return model_color[mask], model_mag[mask]
        except Exception:
            return None, None

    # Cache fetched isochrones to avoid re-downloading
    _iso_cache = {}

    def _chi2(params):
        """Compute chi-squared for a given parameter set."""
        log_age, met, dm, av = params

        # Bounds check
        if not (age_range[0] <= log_age <= age_range[1]):
            return 1e12
        if not (met_range[0] <= met <= met_range[1]):
            return 1e12
        if not (dm_range[0] <= dm <= dm_range[1]):
            return 1e12
        if not (av_range[0] <= av <= av_range[1]):
            return 1e12

        # Round for caching
        cache_key = (round(log_age, 2), round(met, 2))
        if cache_key not in _iso_cache:
            _iso_cache[cache_key] = _get_model_cmd(cache_key[0], cache_key[1])
        model_color, model_mag = _iso_cache[cache_key]

        if model_color is None or len(model_color) < 3:
            return 1e12

        # Apply extinction and distance modulus to model
        shifted_mag = model_mag + dm + a_g_over_av * av
        shifted_color = model_color + e_bprp_over_av * av

        # For each observed point, find nearest model point on the CMD
        chi2_sum = 0.0
        for i in range(n_data):
            d_color = (bp_rp[i] - shifted_color) / color_err[i]
            d_mag = (abs_mag[i] - shifted_mag) / mag_err[i]
            dist2 = d_color**2 + d_mag**2
            chi2_sum += np.min(dist2)

        return chi2_sum

    # --- 4-D Grid search (age × metallicity × dm × av) ---
    logger.info("Starting isochrone fit: method=%s, n_data=%d", method, n_data)

    age_grid = np.linspace(age_range[0], age_range[1], n_grid_age)
    met_grid = np.linspace(met_range[0], met_range[1], n_grid_met)
    # Coarse 3-point sweep over dm and av (min, center, max of range)
    dm_grid = np.linspace(dm_range[0], dm_range[1], 3)
    av_grid = np.linspace(av_range[0], av_range[1], 3)

    # Initialize best_params from range midpoints, not hardcoded values
    best_chi2 = 1e12
    best_params = [
        float((age_range[0] + age_range[1]) / 2),
        float((met_range[0] + met_range[1]) / 2),
        float((dm_range[0] + dm_range[1]) / 2),
        float((av_range[0] + av_range[1]) / 2),
    ]

    for log_age in age_grid:
        for met in met_grid:
            for dm in dm_grid:
                for av in av_grid:
                    c2 = _chi2([log_age, met, dm, av])
                    if c2 < best_chi2:
                        best_chi2 = c2
                        best_params = [float(log_age), float(met), float(dm), float(av)]

    logger.info("Grid search best: log_age=%.2f, [M/H]=%.2f, dm=%.2f, A_V=%.2f, chi2=%.1f",
                best_params[0], best_params[1], best_params[2], best_params[3], best_chi2)

    # If best_chi2 is still the sentinel value, no model isochrone could be fetched
    # (e.g. PARSEC API unavailable). Raise so the caller can fall back.
    if best_chi2 >= 1e11:
        raise RuntimeError("Grid search failed — no valid isochrone model fetched (PARSEC API may be unavailable)")

    # --- Nelder-Mead refinement ---
    result = minimize(_chi2, best_params, method="Nelder-Mead",
                      options={"maxiter": 2000, "xatol": 0.01, "fatol": 1.0})
    best_params = list(result.x)
    best_chi2 = float(result.fun)
    dof = max(n_data - 4, 1)

    fit_result = {
        "best_fit": {
            "log_age": round(float(best_params[0]), 3),
            "metallicity": round(float(best_params[1]), 3),
            "distance_modulus": round(float(best_params[2]), 3),
            "A_V": round(float(best_params[3]), 3),
            "age_myr": round(10 ** float(best_params[0]) / 1e6, 1),
            "distance_pc": round(10 ** (float(best_params[2]) / 5 + 1), 1),
        },
        "chi2": round(best_chi2, 2),
        "chi2_reduced": round(best_chi2 / dof, 4),
        "n_data": n_data,
        "dof": dof,
        "method": method,
        "errors_source": errors_source,
    }

    # --- Monte Carlo error propagation ---
    if compute_errors:
        n_mc = 100
        mc_params = []
        rng_mc = np.random.default_rng(seed)
        for _ in range(n_mc):
            # Perturb observed data within errors
            perturbed_mag = abs_mag + rng_mc.normal(0, mag_err)
            perturbed_color = bp_rp + rng_mc.normal(0, color_err)

            # Build a chi2 function for the perturbed data
            def _mc_chi2(params, _pmag=perturbed_mag, _pcol=perturbed_color):
                log_age, met, dm, av = params
                if not (age_range[0] <= log_age <= age_range[1]):
                    return 1e12
                if not (met_range[0] <= met <= met_range[1]):
                    return 1e12
                if not (dm_range[0] <= dm <= dm_range[1]):
                    return 1e12
                if not (av_range[0] <= av <= av_range[1]):
                    return 1e12
                cache_key = (round(log_age, 2), round(met, 2))
                if cache_key not in _iso_cache:
                    _iso_cache[cache_key] = _get_model_cmd(cache_key[0], cache_key[1])
                model_color, model_mag = _iso_cache[cache_key]
                if model_color is None or len(model_color) < 3:
                    return 1e12
                shifted_mag = model_mag + dm + a_g_over_av * av
                shifted_color = model_color + e_bprp_over_av * av
                chi2_sum = 0.0
                for i in range(len(_pmag)):
                    d_color = (_pcol[i] - shifted_color) / color_err[i]
                    d_mag = (_pmag[i] - shifted_mag) / mag_err[i]
                    dist2 = d_color**2 + d_mag**2
                    chi2_sum += np.min(dist2)
                return chi2_sum

            try:
                mc_result = minimize(
                    _mc_chi2, best_params, method="Nelder-Mead",
                    options={"maxiter": 2000, "xatol": 0.01, "fatol": 1.0},
                )
                mc_params.append(list(mc_result.x))
            except Exception:
                continue

        if len(mc_params) >= 10:
            mc_arr = np.array(mc_params)
            percentiles = np.percentile(mc_arr, [16, 50, 84], axis=0)
            fit_result["uncertainties"] = {
                "log_age": [round(float(percentiles[0, 0]), 3), round(float(percentiles[2, 0]), 3)],
                "metallicity": [round(float(percentiles[0, 1]), 3), round(float(percentiles[2, 1]), 3)],
                "distance_modulus": [round(float(percentiles[0, 2]), 3), round(float(percentiles[2, 2]), 3)],
                "A_V": [round(float(percentiles[0, 3]), 3), round(float(percentiles[2, 3]), 3)],
                "method": "monte_carlo_100",
                "n_successful": len(mc_params),
            }
        else:
            logger.warning(
                "Monte Carlo error estimation failed: only %d/%d iterations succeeded",
                len(mc_params), n_mc,
            )

    if method == "mcmc":
        try:
            import emcee
            import corner as corner_pkg
        except ImportError:
            fit_result["mcmc_error"] = "emcee or corner not installed"
            return fit_result

        rng = np.random.default_rng(seed)

        def _log_prob(params):
            log_age, met, dm, av = params
            # Uniform priors
            if not (age_range[0] <= log_age <= age_range[1]):
                return -np.inf
            if not (met_range[0] <= met <= met_range[1]):
                return -np.inf
            if not (dm_range[0] <= dm <= dm_range[1]):
                return -np.inf
            if not (av_range[0] <= av <= av_range[1]):
                return -np.inf
            c2 = _chi2(params)
            if c2 >= 1e11:
                return -np.inf
            return -0.5 * c2

        ndim = 4
        # Initialize walkers around best-fit
        p0 = np.array(best_params) + rng.normal(0, [0.1, 0.05, 0.2, 0.02], (n_walkers, ndim))
        # Clip to bounds
        p0[:, 0] = np.clip(p0[:, 0], age_range[0], age_range[1])
        p0[:, 1] = np.clip(p0[:, 1], met_range[0], met_range[1])
        p0[:, 2] = np.clip(p0[:, 2], dm_range[0], dm_range[1])
        p0[:, 3] = np.clip(p0[:, 3], av_range[0], av_range[1])

        sampler = emcee.EnsembleSampler(n_walkers, ndim, _log_prob)
        logger.info("Running MCMC: %d walkers, %d steps, %d burn-in",
                     n_walkers, n_steps, n_burn)
        sampler.run_mcmc(p0, n_steps, progress=False)

        chain = sampler.get_chain(discard=n_burn, flat=True)
        labels = ["log(age)", "[M/H]", "DM", "A_V"]

        percentiles = {}
        for i, label in enumerate(labels):
            p16, p50, p84 = np.percentile(chain[:, i], [16, 50, 84])
            percentiles[label] = {
                "p16": round(float(p16), 4),
                "p50": round(float(p50), 4),
                "p84": round(float(p84), 4),
                "value": round(float(p50), 4),
                "err_minus": round(float(p50 - p16), 4),
                "err_plus": round(float(p84 - p50), 4),
            }

        fit_result["uncertainties"] = percentiles
        fit_result["best_fit"]["log_age"] = percentiles["log(age)"]["p50"]
        fit_result["best_fit"]["metallicity"] = percentiles["[M/H]"]["p50"]
        fit_result["best_fit"]["distance_modulus"] = percentiles["DM"]["p50"]
        fit_result["best_fit"]["A_V"] = percentiles["A_V"]["p50"]
        fit_result["best_fit"]["age_myr"] = round(
            10 ** percentiles["log(age)"]["p50"] / 1e6, 1
        )
        fit_result["best_fit"]["distance_pc"] = round(
            10 ** (percentiles["DM"]["p50"] / 5 + 1), 1
        )
        fit_result["acceptance_fraction"] = round(
            float(np.mean(sampler.acceptance_fraction)), 4
        )

        # Corner plot
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig = corner_pkg.corner(
            chain,
            labels=labels,
            quantiles=[0.16, 0.5, 0.84],
            show_titles=True,
            title_kwargs={"fontsize": 10},
        )
        fig.suptitle("Isochrone Fit - MCMC Posterior", fontsize=12, y=1.02)
        fit_result["corner_fig"] = fig
        plt.close(fig)

    return fit_result


# ── Model Comparison & Residual Analysis ──

def compare_models(data, models):
    """Compare multiple fitted models using information criteria.

    Args:
        data: dict with 'x' and 'y' arrays (observed data), and optionally 'y_err'.
              Alternatively, an integer n_data (number of data points) if chi2 values
              are pre-computed in each model entry.
        models: list of dicts, each with:
            - 'name': model label (str)
            - 'chi2': chi-squared value (float)
            - 'n_params': number of free parameters (int)
            Optionally:
            - 'func': callable model function (ignored if chi2 provided)
            - 'params': best-fit parameters (ignored if chi2 provided)

    Returns:
        dict with:
            ranked: list of dicts sorted by BIC, each containing
                name, chi2, chi2_reduced, AIC, BIC, delta_BIC, verdict
            best_model: name of the best model
            summary: human-readable comparison text
    """
    if isinstance(data, (int, float)):
        n_data = int(data)
    elif isinstance(data, dict):
        n_data = len(data.get("y", data.get("x", [])))
    else:
        n_data = len(data)

    if n_data < 1:
        raise ValueError("Need at least 1 data point")

    results = []
    for m in models:
        name = m.get("name", "unnamed")
        chi2 = float(m["chi2"])
        k = int(m.get("n_params", m.get("k", 1)))
        dof = max(n_data - k, 1)

        # AIC = chi2 + 2k  (for Gaussian likelihood, AIC = -2*lnL + 2k ≈ chi2 + 2k)
        aic = chi2 + 2 * k
        # BIC = chi2 + k * ln(n)
        bic = chi2 + k * np.log(n_data)

        results.append({
            "name": name,
            "chi2": round(chi2, 4),
            "chi2_reduced": round(chi2 / dof, 4),
            "n_params": k,
            "AIC": round(aic, 4),
            "BIC": round(bic, 4),
        })

    # Sort by BIC
    results.sort(key=lambda r: r["BIC"])
    best_bic = results[0]["BIC"]

    for r in results:
        delta = r["BIC"] - best_bic
        r["delta_BIC"] = round(delta, 4)
        if delta < 2:
            r["verdict"] = "inconclusive"
        elif delta < 6:
            r["verdict"] = "positive"
        elif delta < 10:
            r["verdict"] = "strong"
        else:
            r["verdict"] = "decisive"

    # Best model gets "best" verdict
    results[0]["verdict"] = "best"

    # Summary text
    best = results[0]
    lines = [f"Best model: {best['name']} (BIC={best['BIC']:.1f}, chi2_red={best['chi2_reduced']:.3f})"]
    for r in results[1:]:
        lines.append(
            f"  vs {r['name']}: delta_BIC={r['delta_BIC']:.1f} → {r['verdict']} "
            f"evidence against this model"
        )

    return {
        "ranked": results,
        "best_model": best["name"],
        "summary": "\n".join(lines),
    }


def analyze_residuals(data, model_prediction, errors=None):
    """Analyse fit residuals for systematic patterns.

    Computes autocorrelation (Durbin-Watson), normality (Shapiro-Wilk),
    outlier count, and generates a Q-Q plot.

    Args:
        data: observed values (array)
        model_prediction: model values at same points (array)
        errors: measurement uncertainties (optional; used to normalise residuals)

    Returns:
        dict with:
            residuals: array of residuals
            durbin_watson: DW statistic (2.0 = no autocorrelation)
            durbin_watson_rating: 'pass' / 'warn' / 'fail'
            shapiro_stat, shapiro_p: Shapiro-Wilk test results
            normality_rating: 'pass' / 'warn' / 'fail'
            n_outliers_3sigma: count of >3-sigma outliers
            outlier_fraction: fraction of outliers
            outlier_rating: 'pass' / 'warn' / 'fail'
            overall_rating: worst of the three ratings
            qq_fig: matplotlib Figure with Q-Q plot
    """
    from scipy import stats

    data = np.asarray(data, dtype=float)
    model_prediction = np.asarray(model_prediction, dtype=float)

    residuals = data - model_prediction
    if errors is not None:
        errors = np.asarray(errors, dtype=float)
        norm_residuals = residuals / np.where(errors > 0, errors, 1.0)
    else:
        std = np.std(residuals)
        norm_residuals = residuals / std if std > 0 else residuals

    n = len(residuals)
    if n < 5:
        raise ValueError(f"Need at least 5 data points for residual analysis, got {n}")

    # --- Durbin-Watson statistic ---
    diffs = np.diff(norm_residuals)
    dw = float(np.sum(diffs**2) / np.sum(norm_residuals**2)) if np.sum(norm_residuals**2) > 0 else 2.0
    # DW ~ 2.0 means no autocorrelation; <1.5 or >2.5 is suspicious
    if 1.5 <= dw <= 2.5:
        dw_rating = "pass"
    elif 1.0 <= dw < 1.5 or 2.5 < dw <= 3.0:
        dw_rating = "warn"
    else:
        dw_rating = "fail"

    # --- Shapiro-Wilk normality test ---
    # Shapiro-Wilk limited to 5000 samples
    test_residuals = norm_residuals[:5000] if n > 5000 else norm_residuals
    sw_stat, sw_p = stats.shapiro(test_residuals)
    if sw_p > 0.05:
        norm_rating = "pass"
    elif sw_p > 0.01:
        norm_rating = "warn"
    else:
        norm_rating = "fail"

    # --- 3-sigma outliers ---
    n_outliers = int(np.sum(np.abs(norm_residuals) > 3.0))
    outlier_frac = n_outliers / n
    # For Gaussian, expect ~0.27% beyond 3-sigma
    if outlier_frac < 0.01:
        outlier_rating = "pass"
    elif outlier_frac < 0.03:
        outlier_rating = "warn"
    else:
        outlier_rating = "fail"

    # --- Overall rating ---
    ratings = [dw_rating, norm_rating, outlier_rating]
    if "fail" in ratings:
        overall = "fail"
    elif "warn" in ratings:
        overall = "warn"
    else:
        overall = "pass"

    # --- Q-Q plot ---
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(12, 4))

    # Panel 1: Residual histogram
    axes[0].hist(norm_residuals, bins=min(30, max(10, n // 10)), density=True,
                 color="#4a90d9", alpha=0.7, edgecolor="white")
    x_gauss = np.linspace(-4, 4, 100)
    axes[0].plot(x_gauss, stats.norm.pdf(x_gauss), "r-", lw=1.5, label="Gaussian")
    axes[0].set_xlabel("Normalised Residual")
    axes[0].set_ylabel("Density")
    axes[0].set_title("Residual Distribution")
    axes[0].legend(fontsize=8)

    # Panel 2: Q-Q plot
    stats.probplot(norm_residuals, dist="norm", plot=axes[1])
    axes[1].set_title("Q-Q Plot")
    axes[1].get_lines()[0].set_color("#4a90d9")
    axes[1].get_lines()[0].set_markersize(3)

    # Panel 3: Residuals vs index (autocorrelation visual)
    axes[2].scatter(range(n), norm_residuals, s=8, alpha=0.6, color="#4a90d9")
    axes[2].axhline(0, color="gray", ls="--", lw=0.8)
    axes[2].axhline(3, color="red", ls=":", lw=0.8, alpha=0.5)
    axes[2].axhline(-3, color="red", ls=":", lw=0.8, alpha=0.5)
    axes[2].set_xlabel("Index")
    axes[2].set_ylabel("Normalised Residual")
    axes[2].set_title(f"Residuals (DW={dw:.2f})")

    fig.tight_layout()

    return {
        "residuals": residuals.tolist(),
        "durbin_watson": round(dw, 4),
        "durbin_watson_rating": dw_rating,
        "shapiro_stat": round(float(sw_stat), 4),
        "shapiro_p": round(float(sw_p), 6),
        "normality_rating": norm_rating,
        "n_outliers_3sigma": n_outliers,
        "outlier_fraction": round(outlier_frac, 4),
        "outlier_rating": outlier_rating,
        "overall_rating": overall,
        "n_data": n,
        "qq_fig": fig,
    }


# ── Common Astronomy Plots ──

def plot_hr_diagram(bp_rp, gmag, parallax=None, distance_pc=None,
                    labels=None,
                    title="HR Diagram", color_by=None,
                    isochrones=None, isochrone_ages=None,
                    ax=None, scatter_kwargs=None):
    """Publication-quality HR diagram (color-magnitude or color-absolute magnitude).

    Args:
        bp_rp: BP-RP color array
        gmag: G magnitude array
        parallax: parallax in mas (if given, converts to absolute magnitude)
        distance_pc: distance in parsecs — alternative to parallax. Converted
            internally to parallax_mas = 1000 / distance_pc before use.
            Accepts scalar or array.
        labels: object labels for legend
        color_by: array to color points by (e.g., metallicity)
        isochrones: list of DataFrames from get_isochrone() to overlay
        isochrone_ages: list of log_age floats to auto-fetch and overlay
        ax: optional matplotlib Axes to draw into
        scatter_kwargs: optional keyword overrides passed to scatter()
    """
    import matplotlib.pyplot as plt
    scatter_kwargs = dict(scatter_kwargs or {})
    if ax is None:
        fig, ax = pub_figure()
    else:
        fig = ax.figure
        pub_style()

    # Fix D8 + R8: robust element-wise conversion that preserves row count.
    # Each top-level element of `arr` becomes exactly one output float.
    # Non-scalar entries (nested lists, arrays, objects) become NaN — that
    # way a dataframe column with some ragged cells still produces a vector
    # of the same length as its siblings and the plot can be drawn with NaN
    # holes rather than raising an inhomogeneous-shape ValueError.
    def _clean(arr):
        if arr is None:
            return None
        # Fast path: already a clean numeric ndarray or plain scalar
        try:
            a = np.asarray(arr, dtype=float)
            if a.ndim == 0:
                return np.array([float(a)])
            if a.ndim > 1:
                a = a.flatten()
            return a
        except (ValueError, TypeError):
            pass

        # Slow path: iterate top level, one output per input element
        out: list[float] = []
        try:
            iterator = iter(arr)
        except TypeError:
            try:
                return np.array([float(arr)], dtype=float)
            except (TypeError, ValueError):
                return np.array([float("nan")], dtype=float)

        for x in iterator:
            if x is None:
                out.append(float("nan"))
                continue
            if isinstance(x, (int, float, np.integer, np.floating)):
                out.append(float(x))
                continue
            if isinstance(x, np.ndarray):
                if x.ndim == 0:
                    out.append(float(x))
                    continue
                # Non-scalar array in a cell — cannot collapse to one value
                out.append(float("nan"))
                continue
            if isinstance(x, str):
                try:
                    out.append(float(x))
                except ValueError:
                    out.append(float("nan"))
                continue
            # Generic iterable / object — best effort single float, else NaN
            try:
                out.append(float(x))
            except (TypeError, ValueError):
                out.append(float("nan"))

        return np.array(out, dtype=float) if out else np.array([], dtype=float)

    bp_rp = _clean(bp_rp)
    gmag = _clean(gmag)

    y = gmag.copy()
    ylabel = "G [mag]"

    # Fix D6: allow distance_pc as alternative to parallax
    if distance_pc is not None and parallax is None:
        d = _clean(distance_pc)
        # Handle scalar distance (broadcast to match gmag length)
        if d.size == 1:
            d = np.full_like(gmag, float(d[0]))
        parallax = 1000.0 / np.where(d > 0, d, np.nan)

    if parallax is not None:
        plx = _clean(parallax) if not isinstance(parallax, np.ndarray) else parallax
        valid = plx > 0
        y = y.copy()
        y[valid] = y[valid] + 5 * np.log10(plx[valid]) - 10  # absolute mag
        y[~valid] = np.nan
        ylabel = r"$M_G$ [mag]"

    if color_by is not None:
        sc = ax.scatter(
            bp_rp, y, c=color_by, s=3, alpha=0.6, cmap="viridis", rasterized=True, **scatter_kwargs
        )
        plt.colorbar(sc, ax=ax, label="Color parameter")
    else:
        ax.scatter(bp_rp, y, s=3, alpha=0.5, color="#0A84FF", rasterized=True, **scatter_kwargs)

    # ── Isochrone overlay ──
    # If isochrone_ages provided, auto-fetch them
    if isochrone_ages is not None and isochrones is None:
        isochrones = []
        for age in isochrone_ages:
            try:
                iso_df = get_isochrone(age)
                isochrones.append(iso_df)
            except Exception as exc:
                logger.warning("Could not fetch isochrone for log_age=%.2f: %s", age, exc)

    if isochrones:
        from matplotlib.colors import Normalize

        # Determine log_age for each isochrone (from DataFrame or index)
        iso_ages = []
        for iso_df in isochrones:
            age_val = None
            for col in ("logAge", "logageyr", "log_age", "logage"):
                if col in iso_df.columns:
                    age_val = float(iso_df[col].iloc[0])
                    break
            iso_ages.append(age_val)

        # Build colormap for age range
        valid_ages = [a for a in iso_ages if a is not None]
        if valid_ages:
            age_min, age_max = min(valid_ages), max(valid_ages)
        else:
            age_min, age_max = 0.0, 1.0
        if age_min == age_max:
            age_max = age_min + 1.0
        norm = Normalize(vmin=age_min, vmax=age_max)
        cmap = plt.get_cmap("plasma")

        for iso_df, age_val in zip(isochrones, iso_ages):
            # Determine color and magnitude columns
            bp_col, rp_col, g_col = None, None, None
            for c in iso_df.columns:
                cl = c.lower()
                if cl in ("g_bpmag", "g_bp", "gbpmag"):
                    bp_col = c
                elif cl in ("g_rpmag", "g_rp", "grpmag"):
                    rp_col = c
                elif cl in ("gmag", "g_mag"):
                    g_col = c

            if bp_col is None or rp_col is None or g_col is None:
                logger.warning(
                    "Isochrone DataFrame missing expected Gaia columns "
                    "(found: %s). Skipping overlay.", list(iso_df.columns)
                )
                continue

            iso_color = iso_df[bp_col] - iso_df[rp_col]
            iso_mag = iso_df[g_col]

            color_val = age_val if age_val is not None else 0.5
            line_color = cmap(norm(color_val))
            label = f"log(age)={age_val:.1f}" if age_val is not None else "isochrone"

            ax.plot(
                iso_color, iso_mag,
                color=line_color, linewidth=1.2, alpha=0.85,
                label=label, zorder=5,
            )

        ax.legend(loc="upper left", fontsize=8, frameon=False, ncol=1)

    ax.set_xlabel(r"$G_{BP} - G_{RP}$ [mag]")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.invert_yaxis()
    fig.tight_layout()
    return fig, ax


def plot_bpt(log_nii_ha, log_oiii_hb, labels=None, title="BPT Diagram"):
    """BPT (Baldwin-Phillips-Terlevich) diagram with classification lines.

    Args:
        log_nii_ha: log10([NII]/H-alpha) array
        log_oiii_hb: log10([OIII]/H-beta) array
    """
    import matplotlib.pyplot as plt
    fig, ax = pub_figure()

    ax.scatter(log_nii_ha, log_oiii_hb, s=8, alpha=0.6, color="#0A84FF", rasterized=True)

    # Kewley+01 maximum starburst line
    x_kew = np.linspace(-1.5, 0.3, 100)
    y_kew = 0.61 / (x_kew - 0.47) + 1.19
    ax.plot(x_kew, y_kew, "r-", linewidth=1.5, label="Kewley+01")

    # Kauffmann+03 pure star-forming line
    x_kau = np.linspace(-1.5, 0.0, 100)
    y_kau = 0.61 / (x_kau - 0.05) + 1.3
    ax.plot(x_kau, y_kau, "b--", linewidth=1.5, label="Kauffmann+03")

    ax.set_xlabel(r"log([N II] $\lambda$6583 / H$\alpha$)")
    ax.set_ylabel(r"log([O III] $\lambda$5007 / H$\beta$)")
    ax.set_title(title)
    ax.set_xlim(-1.5, 0.8)
    ax.set_ylim(-1.2, 1.5)

    # Region labels
    ax.text(-1.0, -0.5, "Star-Forming", fontsize=9, color="blue", alpha=0.7)
    ax.text(0.2, 0.8, "AGN", fontsize=9, color="red", alpha=0.7)
    ax.text(-0.3, 0.0, "Composite", fontsize=9, color="gray", alpha=0.7)

    ax.legend(loc="upper left", frameon=False)
    plt.tight_layout()
    return fig, ax


def plot_sed(wavelength, flux, flux_err=None, model_wave=None, model_flux=None,
             title="Spectral Energy Distribution", log_scale=True):
    """Publication-quality SED plot.

    Args:
        wavelength: observed wavelengths (Angstrom)
        flux: flux values
        flux_err: flux uncertainties (optional)
        model_wave: model wavelengths for overplot (optional)
        model_flux: model flux values (optional)
    """
    import matplotlib.pyplot as plt
    fig, ax = pub_figure()

    if flux_err is not None:
        ax.errorbar(wavelength, flux, yerr=flux_err, fmt="o", ms=4,
                    color="#0A84FF", ecolor="#666", capsize=2, label="Data")
    else:
        ax.plot(wavelength, flux, "o-", ms=3, color="#0A84FF", label="Data")

    if model_wave is not None and model_flux is not None:
        ax.plot(model_wave, model_flux, "r-", linewidth=1.5, label="Model", alpha=0.8)

    ax.set_xlabel(r"Wavelength [$\AA$]")
    ax.set_ylabel(r"Flux [arbitrary units]")
    ax.set_title(title)
    if log_scale:
        ax.set_xscale("log")
        ax.set_yscale("log")
    ax.legend(frameon=False)
    plt.tight_layout()
    return fig, ax


def plot_lightcurve(time, mag, mag_err=None, title="Light Curve", band=""):
    """Publication-quality light curve."""
    import matplotlib.pyplot as plt
    fig, ax = pub_figure()

    if mag_err is not None:
        ax.errorbar(time, mag, yerr=mag_err, fmt="o", ms=3,
                    color="#0A84FF", ecolor="#999", capsize=2)
    else:
        ax.plot(time, mag, "o-", ms=3, color="#0A84FF")

    ax.set_xlabel("Time [MJD]")
    ax.set_ylabel(f"Magnitude{f' ({band})' if band else ''}")
    ax.set_title(title)
    ax.invert_yaxis()
    plt.tight_layout()
    return fig, ax


def plot_sky_distribution(ra, dec, title="Sky Distribution", projection="aitoff"):
    """Publication-quality sky distribution plot in Aitoff or Mollweide projection."""
    import matplotlib.pyplot as plt
    pub_style()
    fig = plt.figure(figsize=(10, 5))
    ax = fig.add_subplot(111, projection=projection)

    ra_rad = np.deg2rad(np.where(np.array(ra) > 180, np.array(ra) - 360, ra))
    dec_rad = np.deg2rad(dec)

    ax.scatter(ra_rad, dec_rad, s=3, alpha=0.5, color="#0A84FF", rasterized=True)
    ax.grid(True, alpha=0.3)
    ax.set_title(title, pad=20)
    plt.tight_layout()
    return fig, ax


# ── Analysis Modules ──

def bpt_classify(log_nii_ha, log_oiii_hb):
    """Classify objects using BPT diagram (Kewley+01, Kauffmann+03).

    Returns:
        Array of classifications: "SF", "Composite", "AGN", or "Unknown"
    """
    x = np.asarray(log_nii_ha, dtype=float)
    y = np.asarray(log_oiii_hb, dtype=float)
    result = np.full(len(x), "Unknown", dtype="U10")

    # Kauffmann+03 line: below this = pure star-forming
    kau_y = np.where(x < 0.05, 0.61 / (x - 0.05) + 1.3, np.inf)
    # Kewley+01 line: above this = AGN
    kew_y = np.where(x < 0.47, 0.61 / (x - 0.47) + 1.19, np.inf)

    sf = y < kau_y
    agn = y > kew_y
    composite = ~sf & ~agn

    result[sf] = "SF"
    result[agn] = "AGN"
    result[composite] = "Composite"

    return result


def compute_luminosity_distance(z, H0=70.0, Om0=0.3):
    """Compute luminosity distance in Mpc.

    Args:
        z: Redshift scalar or array.
        H0: Hubble constant in km/s/Mpc.
        Om0: Matter density parameter in a flat Lambda-CDM cosmology.

    Returns:
        Scalar or array of luminosity distances in Mpc.
    """
    try:
        from astropy.cosmology import FlatLambdaCDM
        import astropy.units as u
        cosmo = FlatLambdaCDM(H0=H0, Om0=Om0)
        dl = cosmo.luminosity_distance(z)
        return dl.to(u.Mpc).value
    except ImportError:
        # Fallback: simple approximation for low z
        c = 299792.458  # km/s
        return c * z / H0 * (1 + z / 2)


def compute_absolute_magnitude(
    mag=None,
    apparent_mag=None,
    redshift=None,
    distance_mpc=None,
    distance_pc=None,
    parallax_mas=None,
):
    """Compute absolute magnitude from apparent magnitude and one distance indicator.

    Args:
        mag: Apparent magnitude scalar or array.
        apparent_mag: Alias of ``mag``.
        redshift: Redshift scalar/array. Converted internally to luminosity distance.
        distance_mpc: Distance in megaparsecs.
        distance_pc: Distance in parsecs.
        parallax_mas: Parallax in milliarcseconds.

    Notes:
        Provide exactly one of ``redshift``, ``distance_mpc``, ``distance_pc``,
        or ``parallax_mas``.
    """
    if mag is None and apparent_mag is None:
        raise ValueError("Provide apparent magnitude via mag or apparent_mag")
    if mag is not None and apparent_mag is not None:
        raise ValueError("Use either mag or apparent_mag, not both")

    magnitude = mag if mag is not None else apparent_mag
    m = np.asarray(magnitude, dtype=float)

    provided = sum(
        value is not None
        for value in (redshift, distance_mpc, distance_pc, parallax_mas)
    )
    if provided != 1:
        raise ValueError(
            "Provide exactly one of redshift, distance_mpc, distance_pc, or parallax_mas"
        )

    if parallax_mas is not None:
        plx = np.asarray(parallax_mas, dtype=float)
        valid = plx > 0
        M = np.full_like(m, np.nan)
        M[valid] = m[valid] + 5 * np.log10(plx[valid]) - 10
        return M
    elif distance_pc is not None:
        d_pc = np.asarray(distance_pc, dtype=float)
        valid = d_pc > 0
        M = np.full_like(m, np.nan)
        M[valid] = m[valid] - 5 * np.log10(d_pc[valid]) + 5
        return M
    elif distance_mpc is not None:
        d = np.asarray(distance_mpc, dtype=float)
        valid = d > 0
        M = np.full_like(m, np.nan)
        M[valid] = m[valid] - 5 * np.log10(d[valid] * 1e6) + 5
        return M
    elif redshift is not None:
        d = np.asarray(compute_luminosity_distance(redshift), dtype=float)
        valid = d > 0
        M = np.full_like(m, np.nan)
        M[valid] = m[valid] - 5 * np.log10(d[valid] * 1e6) + 5
        return M
    else:
        raise ValueError("Provide redshift, distance_mpc, or parallax_mas")


def k_correction(z, band="r", galaxy_type="elliptical"):
    """Approximate K-correction for common bands.

    Simple polynomial approximation — for precise work use kcorrect package.
    """
    z = np.asarray(z, dtype=float)
    # Approximate K-corrections from Chilingarian+10
    coeffs = {
        ("r", "elliptical"): [0.0, 1.0, 0.5],
        ("r", "spiral"): [0.0, 0.5, 0.3],
        ("g", "elliptical"): [0.0, 1.5, 0.8],
        ("g", "spiral"): [0.0, 0.8, 0.4],
        ("i", "elliptical"): [0.0, 0.6, 0.3],
        ("i", "spiral"): [0.0, 0.3, 0.2],
    }
    c = coeffs.get((band, galaxy_type), [0.0, 1.0, 0.5])
    return c[0] + c[1] * z + c[2] * z**2


def spectral_stacking(wavelengths_list, fluxes_list, method="median"):
    """Stack multiple spectra onto a common wavelength grid.

    Args:
        wavelengths_list: list of wavelength arrays
        fluxes_list: list of flux arrays (same length as wavelengths)
        method: "median" or "mean"

    Returns:
        (common_wavelength, stacked_flux)
    """
    from scipy.interpolate import interp1d

    # Common grid: union of all wavelength ranges
    all_min = min(w[0] for w in wavelengths_list)
    all_max = max(w[-1] for w in wavelengths_list)
    n_pix = max(len(w) for w in wavelengths_list)
    common_wave = np.linspace(all_min, all_max, n_pix)

    interpolated = []
    for wave, flux in zip(wavelengths_list, fluxes_list):
        f = interp1d(wave, flux, bounds_error=False, fill_value=np.nan)
        interpolated.append(f(common_wave))

    stack = np.array(interpolated)
    if method == "median":
        result = np.nanmedian(stack, axis=0)
    else:
        result = np.nanmean(stack, axis=0)

    return common_wave, result


def multi_gaussian_fit(wavelength, flux, n_components=2, initial_centers=None):
    """Fit multiple Gaussian components to a spectrum.

    Args:
        wavelength: wavelength array
        flux: flux array
        n_components: number of Gaussian components
        initial_centers: list of initial center wavelengths (optional)

    Returns:
        dict with fit parameters and model
    """
    from scipy.optimize import curve_fit

    wave = np.asarray(wavelength, dtype=float)
    f = np.asarray(flux, dtype=float)

    if len(wave) < n_components * 3 + 1:
        return {"success": False, "error": f"Need at least {n_components * 3 + 1} data points for {n_components} components, got {len(wave)}"}

    def multi_gauss(x, *params):
        result = np.zeros_like(x)
        for i in range(0, len(params), 3):
            amp, center, sigma = params[i], params[i + 1], params[i + 2]
            result += amp * np.exp(-0.5 * ((x - center) / sigma) ** 2)
        return result

    # Build initial guesses
    p0 = []
    if initial_centers:
        for c in initial_centers:
            idx = np.argmin(np.abs(wave - c))
            p0.extend([f[idx], c, 5.0])
    else:
        # Auto-detect peaks
        from scipy.signal import find_peaks
        peaks, props = find_peaks(f, height=np.median(f), distance=max(1, len(f) // (n_components + 1)))
        peaks = peaks[:n_components]
        for p in peaks:
            p0.extend([f[p], wave[p], 5.0])
        while len(p0) < n_components * 3:
            p0.extend([np.max(f), np.mean(wave), 10.0])

    try:
        popt, pcov = curve_fit(multi_gauss, wave, f, p0=p0, maxfev=10000)
        perr = np.sqrt(np.diag(pcov))
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    components = []
    for i in range(n_components):
        components.append({
            "amplitude": {"value": popt[i * 3], "error": perr[i * 3]},
            "center": {"value": popt[i * 3 + 1], "error": perr[i * 3 + 1]},
            "sigma": {"value": abs(popt[i * 3 + 2]), "error": perr[i * 3 + 2]},
            "fwhm": abs(popt[i * 3 + 2]) * 2.3548,
        })

    model_flux = multi_gauss(wave, *popt)
    residuals = f - model_flux
    chi2 = np.sum(residuals**2) / max(len(f) - len(popt), 1)

    return {
        "success": True,
        "components": components,
        "model_flux": model_flux.tolist(),
        "residuals": residuals.tolist(),
        "reduced_chi2": float(chi2),
    }


def continuum_normalize(wavelength, flux, order=5, sigma_clip=3.0, n_iter=3):
    """Normalize a spectrum by fitting and dividing by the continuum.

    Uses iterative sigma-clipped polynomial fitting.

    Returns:
        (normalized_flux, continuum_model)
    """
    wave = np.asarray(wavelength, dtype=float)
    f = np.asarray(flux, dtype=float)
    mask = np.ones(len(f), dtype=bool)

    for _ in range(n_iter):
        if np.sum(mask) <= order:
            break  # Not enough points for polynomial fit
        coeffs = np.polyfit(wave[mask], f[mask], deg=order)
        continuum = np.polyval(coeffs, wave)
        residuals = f - continuum
        std = np.std(residuals[mask])
        if std == 0:
            break  # Perfectly flat — no further clipping needed
        mask = np.abs(residuals) < sigma_clip * std

    if np.sum(mask) <= order:
        # Fallback: return unmodified spectrum
        return np.ones_like(f), np.full_like(f, np.median(f))

    continuum = np.polyval(np.polyfit(wave[mask], f[mask], deg=order), wave)
    # Guard against zero continuum
    continuum = np.where(np.abs(continuum) < 1e-30, 1e-30, continuum)
    normalized = f / continuum

    return normalized, continuum


def batch_equivalent_width(wavelength, flux, line_centers, window=10.0, cont_window=50.0):
    """Measure equivalent widths for multiple spectral lines at once.

    Args:
        wavelength: wavelength array
        flux: flux array
        line_centers: list of line center wavelengths
        window: half-width of line integration window (Angstrom)
        cont_window: half-width of continuum estimation window

    Returns:
        list of dicts with EW measurements
    """
    from scipy.integrate import trapezoid

    wave = np.asarray(wavelength, dtype=float)
    f = np.asarray(flux, dtype=float)
    results = []

    for center in line_centers:
        line_mask = (wave >= center - window) & (wave <= center + window)
        cont_mask = (
            (wave >= center - cont_window) & (wave <= center + cont_window)
            & ~line_mask
        )

        if np.sum(line_mask) < 3 or np.sum(cont_mask) < 3:
            results.append({"center": center, "ew": None, "error": "insufficient data"})
            continue

        cont_level = np.median(f[cont_mask])
        if abs(cont_level) < 1e-30:
            results.append({"center": center, "ew": None, "error": "zero continuum"})
            continue

        integrand = 1.0 - f[line_mask] / cont_level
        ew = float(trapezoid(integrand, wave[line_mask]))

        # Error from continuum RMS
        cont_rms = float(np.std(f[cont_mask] - cont_level))
        n_pix = np.sum(line_mask)
        dlambda = float(np.median(np.diff(wave[line_mask]))) if np.sum(line_mask) > 1 else 1.0
        ew_err = float(np.sqrt(n_pix) * dlambda * cont_rms / cont_level)

        results.append({
            "center": float(center),
            "ew": ew,
            "ew_error": ew_err,
            "continuum_level": float(cont_level),
            "type": "absorption" if ew > 0 else "emission",
        })

    return results


# ── Cosmological Calculator Expansion ──

def cosmological_calculator(z, H0=67.4, Om0=0.315):
    """Compute multiple cosmological quantities at redshift z.

    Uses flat ΛCDM with Planck18 defaults.

    Args:
        z: Redshift (scalar or array).
        H0: Hubble constant in km/s/Mpc.
        Om0: Matter density parameter.

    Returns:
        dict with: luminosity_distance_mpc, angular_diameter_distance_mpc,
        comoving_distance_mpc, lookback_time_gyr, age_at_redshift_gyr,
        distance_modulus, comoving_volume_gpc3, scale_factor.
    """
    from astropy.cosmology import FlatLambdaCDM
    import astropy.units as u

    cosmo = FlatLambdaCDM(H0=H0, Om0=Om0)
    z_arr = np.asarray(z, dtype=float)

    dl = cosmo.luminosity_distance(z_arr).to(u.Mpc).value
    da = cosmo.angular_diameter_distance(z_arr).to(u.Mpc).value
    dc = cosmo.comoving_distance(z_arr).to(u.Mpc).value
    lookback = cosmo.lookback_time(z_arr).to(u.Gyr).value
    age_at_z = cosmo.age(z_arr).to(u.Gyr).value
    dm = cosmo.distmod(z_arr).value
    vol = cosmo.comoving_volume(z_arr).to(u.Gpc**3).value
    scale = 1.0 / (1.0 + z_arr)

    return {
        "luminosity_distance_mpc": float(dl) if dl.ndim == 0 else dl.tolist(),
        "angular_diameter_distance_mpc": float(da) if da.ndim == 0 else da.tolist(),
        "comoving_distance_mpc": float(dc) if dc.ndim == 0 else dc.tolist(),
        "lookback_time_gyr": float(lookback) if lookback.ndim == 0 else lookback.tolist(),
        "age_at_redshift_gyr": float(age_at_z) if age_at_z.ndim == 0 else age_at_z.tolist(),
        "distance_modulus": float(dm) if dm.ndim == 0 else dm.tolist(),
        "comoving_volume_gpc3": float(vol) if vol.ndim == 0 else vol.tolist(),
        "scale_factor": float(scale) if scale.ndim == 0 else scale.tolist(),
    }


def redshift_at_age(age_gyr, H0=67.4, Om0=0.315):
    """Find redshift corresponding to a given cosmic age in Gyr.

    Uses scipy.optimize.brentq to invert age(z).

    Args:
        age_gyr: Cosmic age in Gyr (scalar).
        H0: Hubble constant in km/s/Mpc.
        Om0: Matter density parameter.

    Returns:
        Redshift corresponding to the given cosmic age.
    """
    from astropy.cosmology import FlatLambdaCDM
    import astropy.units as u
    from scipy.optimize import brentq

    cosmo = FlatLambdaCDM(H0=H0, Om0=Om0)
    age_now = cosmo.age(0).to(u.Gyr).value

    if age_gyr <= 0 or age_gyr >= age_now:
        raise ValueError(
            f"age_gyr must be between 0 and {age_now:.3f} Gyr (age of the universe), "
            f"got {age_gyr}"
        )

    def _age_diff(z):
        return cosmo.age(z).to(u.Gyr).value - age_gyr

    z_result = brentq(_age_diff, 0.0, 1000.0, xtol=1e-9)
    return float(z_result)


# ── Dust / Extinction Correction ──

def _extinction_curve_manual(wavelength_angstrom, ebv, Rv=3.1):
    """Fallback CCM89 implementation (used when dust_extinction is unavailable)."""
    wave = np.atleast_1d(np.asarray(wavelength_angstrom, dtype=float))
    x = 1.0e4 / wave  # x in um^-1
    a = np.zeros_like(x)
    b = np.zeros_like(x)
    ir = (x >= 0.3) & (x < 1.1)
    if np.any(ir):
        xi = x[ir]
        a[ir] = 0.574 * xi**1.61
        b[ir] = -0.527 * xi**1.61
    opt = (x >= 1.1) & (x < 3.3)
    if np.any(opt):
        y = x[opt] - 1.82
        a[opt] = (1.0 + 0.17699 * y - 0.50447 * y**2 - 0.02427 * y**3
                  + 0.72085 * y**4 + 0.01979 * y**5 - 0.77530 * y**6
                  + 0.32999 * y**7)
        b[opt] = (1.41338 * y + 2.28305 * y**2 + 1.07233 * y**3
                  - 5.38434 * y**4 - 0.62251 * y**5 + 5.30260 * y**6
                  - 2.09002 * y**7)
    uv = (x >= 3.3) & (x <= 8.0)
    if np.any(uv):
        xi = x[uv]
        fa = np.zeros_like(xi)
        fb = np.zeros_like(xi)
        uv2 = xi >= 5.9
        if np.any(uv2):
            xm = xi[uv2] - 5.9
            fa[uv2] = -0.04473 * xm**2 - 0.009779 * xm**3
            fb[uv2] = 0.2130 * xm**2 + 0.1207 * xm**3
        a[uv] = (1.752 - 0.316 * xi - 0.104
                 / ((xi - 4.67)**2 + 0.341) + fa)
        b[uv] = (-3.090 + 1.825 * xi + 1.206
                 / ((xi - 4.62)**2 + 0.263) + fb)
    A_lambda = (a + b / Rv) * Rv * ebv
    return A_lambda


def extinction_curve(wavelength_angstrom, ebv, Rv=3.1, model='ccm89'):
    """Compute extinction A_lambda using a published parameter-averages law.

    D2.5: supported models span the full UV→NIR range; users should pick
    the law matching their sightline:

    - ``ccm89`` (Cardelli+ 1989 ApJ 345, 245): Milky Way diffuse ISM, R_V=3.1
    - ``f99``   (Fitzpatrick 1999 PASP 111, 63): improved UV over CCM89
    - ``g03``   (Gordon+ 2003 ApJ 594, 279): SMC-bar / LMC, low-metallicity galaxies
    - ``g16``   (Gordon+ 2016 ApJ 826, 104): Milky Way, R_V dependence covers 2.0-6.0

    Args:
        wavelength_angstrom: Wavelength(s) in Angstroms (scalar or array).
        ebv: E(B-V) color excess.
        Rv: Ratio of total-to-selective extinction (default 3.1).
        model: one of {ccm89, f99, g03, g16}.

    Returns:
        A_lambda array (extinction in magnitudes at each wavelength).
    """
    try:
        import astropy.units as u

        wave = np.atleast_1d(np.asarray(wavelength_angstrom, dtype=float))
        model_l = (model or "ccm89").lower()
        if model_l == "f99":
            from dust_extinction.parameter_averages import F99
            ext_model = F99(Rv=Rv)
        elif model_l in ("g03", "g03_smcbar"):
            from dust_extinction.averages import G03_SMCBar
            ext_model = G03_SMCBar()
        elif model_l == "g16":
            from dust_extinction.parameter_averages import G16
            ext_model = G16(RvA=Rv)
        else:
            from dust_extinction.parameter_averages import CCM89
            ext_model = CCM89(Rv=Rv)
        # dust_extinction returns A(lambda)/A(V); multiply by A(V) = Rv * E(B-V)
        Av = Rv * ebv
        A_lambda = ext_model(wave * u.AA) * Av
        return np.asarray(A_lambda, dtype=float)
    except ImportError:
        logger.debug("dust_extinction not installed, using manual CCM89 fallback")
        if model != "ccm89":
            logger.warning(
                "%s model requires dust_extinction package; falling back to CCM89",
                model,
            )
        return _extinction_curve_manual(wavelength_angstrom, ebv, Rv=Rv)
    except Exception as exc:
        logger.warning("extinction_curve model=%s failed (%s); falling back to CCM89", model, exc)
        return _extinction_curve_manual(wavelength_angstrom, ebv, Rv=Rv)


def dust_ebv_at_position(ra_deg, dec_deg, source="sfd"):
    """Look up E(B-V) at a sky coordinate (Phase D2.5).

    Uses the optional ``dustmaps`` package; returns ``None`` if the map
    data is not present on disk (first-time users see a clear log line
    about how to download it).  Supported sources: ``sfd`` (Schlegel-
    Finkbeiner 1998, the Planck-revised version), ``planck``
    (Planck Collab XI 2014 thermal dust), ``bayestar`` (Green 2019 3D,
    requires distance — not exposed here yet).
    """
    try:
        import astropy.units as u
        from astropy.coordinates import SkyCoord
        coord = SkyCoord(float(ra_deg) * u.degree, float(dec_deg) * u.degree, frame="icrs")
        src = (source or "sfd").lower()
        if src == "planck":
            from dustmaps.planck import PlanckQuery
            return float(PlanckQuery()(coord))
        # Default: SFD (E(B-V))
        from dustmaps.sfd import SFDQuery
        return float(SFDQuery()(coord))
    except FileNotFoundError as exc:
        logger.warning(
            "Dust map data not found (%s).  First-time install: "
            "python -c 'from dustmaps.%s import fetch; fetch()'",
            exc, source,
        )
        return None
    except ImportError:
        logger.warning("dustmaps package not installed; run: pip install dustmaps")
        return None
    except Exception as exc:
        logger.warning("dust_ebv_at_position failed: %s", exc)
        return None


def deredden(wavelength_angstrom, flux, ebv, Rv=3.1, model='ccm89'):
    """Correct flux for dust extinction using CCM89 or F99.

    Args:
        wavelength_angstrom: Wavelength(s) in Angstroms.
        flux: Observed flux (scalar or array, same shape as wavelength).
        ebv: E(B-V) color excess.
        Rv: Ratio of total-to-selective extinction (default 3.1).
        model: 'ccm89' (Cardelli+1989) or 'f99' (Fitzpatrick 1999, better UV).

    Returns:
        Dereddened flux array: flux_corrected = flux * 10^(0.4 * A_lambda).
    """
    flux = np.asarray(flux, dtype=float)
    A_lam = extinction_curve(wavelength_angstrom, ebv, Rv=Rv, model=model)
    return flux * 10.0**(0.4 * A_lam)


def estimate_ebv(observed_color, intrinsic_color, Rv=3.1):
    """Compute E(B-V) from observed and intrinsic colors.

    E(B-V) = observed_color - intrinsic_color.

    Args:
        observed_color: Observed color index (e.g., B-V observed).
        intrinsic_color: Intrinsic (unreddened) color index.
        Rv: Ratio of total-to-selective extinction (for reference, not used
            in the simple subtraction but returned for convenience).

    Returns:
        E(B-V) value (scalar or array).
    """
    obs = np.asarray(observed_color, dtype=float)
    intr = np.asarray(intrinsic_color, dtype=float)
    return obs - intr


def lookup_ebv_irsa(ra: float, dec: float) -> dict:
    """Look up E(B-V) from IRSA Galactic Dust Reddening and Extinction service."""
    import httpx

    url = "https://irsa.ipac.caltech.edu/cgi-bin/DUST/nph-dust"
    params = {"locstr": f"{ra:.6f} {dec:.6f} equ j2000"}

    try:
        resp = httpx.get(url, params=params, timeout=15.0)
        resp.raise_for_status()
        text = resp.text

        # Parse E(B-V) from the XML/text response
        import re
        # Look for SFD value
        match = re.search(r'<meanValueSandF[^>]*>([\d.]+)</meanValueSandF>', text)
        if match:
            ebv_sfd = float(match.group(1))
        else:
            # Fallback: look for any E(B-V) value
            match = re.search(r'E\(B-V\)\s*=?\s*([\d.]+)', text)
            ebv_sfd = float(match.group(1)) if match else None

        if ebv_sfd is not None:
            return {
                "ebv_sfd": ebv_sfd,
                "ra": ra,
                "dec": dec,
                "source": "IRSA/SFD",
                "A_V": round(ebv_sfd * 3.1, 4),
            }
        return {"error": "Could not parse E(B-V) from IRSA response", "raw": text[:500]}
    except Exception as e:
        return {"error": f"IRSA dust query failed: {e}"}


# ── Error Propagation / Monte Carlo ──

def monte_carlo_propagate(func, params, errors, n_samples=1000, seed=42):
    """Monte Carlo error propagation.

    Draws Gaussian samples around each parameter and evaluates the function
    to build a distribution of outputs.

    Args:
        func: Callable that takes *params and returns a scalar or array.
        params: List of central values.
        errors: List of 1-sigma uncertainties (same length as params).
        n_samples: Number of Monte Carlo draws.
        seed: Random seed for reproducibility.

    Returns:
        dict with: median, mean, std, ci_lower (2.5%), ci_upper (97.5%), samples.
    """
    rng = np.random.default_rng(seed)
    params = np.asarray(params, dtype=float)
    errors = np.asarray(errors, dtype=float)

    draws = rng.normal(
        loc=params, scale=errors, size=(n_samples, len(params))
    )

    samples = np.array([func(*row) for row in draws])

    return {
        "median": float(np.median(samples)),
        "mean": float(np.mean(samples)),
        "std": float(np.std(samples)),
        "ci_lower": float(np.percentile(samples, 2.5)),
        "ci_upper": float(np.percentile(samples, 97.5)),
        "samples": samples.tolist(),
    }


def bootstrap_statistic(data, statistic_func=None, n_bootstrap=1000, ci=0.95, seed=42):
    """Bootstrap resampling to estimate uncertainty of a statistic.

    Args:
        data: 1-D array of observations.
        statistic_func: Callable applied to each resample (default: np.mean).
        n_bootstrap: Number of bootstrap iterations.
        ci: Confidence interval level (default 0.95).
        seed: Random seed for reproducibility.

    Returns:
        dict with: statistic (original), ci_lower, ci_upper, std, samples.
    """
    if statistic_func is None:
        statistic_func = np.mean

    rng = np.random.default_rng(seed)
    data = np.asarray(data, dtype=float)
    n = len(data)

    original = float(statistic_func(data))

    boot_samples = np.array([
        float(statistic_func(data[rng.integers(0, n, size=n)]))
        for _ in range(n_bootstrap)
    ])

    alpha = (1.0 - ci) / 2.0
    ci_lower = float(np.percentile(boot_samples, 100 * alpha))
    ci_upper = float(np.percentile(boot_samples, 100 * (1 - alpha)))

    return {
        "statistic": original,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "std": float(np.std(boot_samples)),
        "samples": boot_samples.tolist(),
    }


def error_weighted_mean(values, errors):
    """Inverse-variance weighted mean with propagated uncertainty.

    Args:
        values: Array of measurements.
        errors: Array of 1-sigma uncertainties (must be > 0).

    Returns:
        dict with: weighted_mean, uncertainty.
    """
    v = np.asarray(values, dtype=float)
    e = np.asarray(errors, dtype=float)

    if np.any(e <= 0):
        raise ValueError("All errors must be positive (> 0)")

    weights = 1.0 / e**2
    w_sum = np.sum(weights)

    wmean = float(np.sum(weights * v) / w_sum)
    wunc = float(np.sqrt(1.0 / w_sum))

    return {
        "weighted_mean": wmean,
        "uncertainty": wunc,
    }


# ── Time-Series / Variability Analysis ──

def lomb_scargle_period(time, mag, mag_err=None, min_period=0.1, max_period=100,
                        n_frequencies=10000, fap_method="auto", n_bootstrap=200,
                        random_seed=None):
    """Detect periodic signal using Lomb-Scargle periodogram.

    Args:
        time, mag, mag_err: observation series.
        min_period / max_period: search bounds (days).
        n_frequencies: number of frequency grid points.
        fap_method: ``"auto"`` (bootstrap when n<200, else Baluev 2008
            analytic), ``"bootstrap"``, or ``"baluev"``.
        n_bootstrap: number of shuffle iterations when bootstrapping.
        random_seed: deterministic reproducibility for bootstrap.

    Returns:
        dict with best_period, best_period_err, best_power, fap,
        fap_level, fap_method, reliable, n_datapoints, frequencies,
        powers, top_periods, window_function, spectral_resolution,
        nyquist_pseudo.

    D2.6: refuses n<20 (FAP is not calibratable for such sparse data);
    computes spectral resolution 1/T, a pseudo-Nyquist frequency, and a
    bootstrap FAP when the analytic one is unreliable.  References:
    Baluev 2008 MNRAS 385, 1279; VanderPlas & Ivezic 2015 ApJ 812, 18.
    """
    from astropy.timeseries import LombScargle

    t = np.asarray(time, dtype=float)
    m = np.asarray(mag, dtype=float)

    if len(t) == 0 or len(m) == 0:
        raise ValueError("lomb_scargle_period: empty input arrays")
    if len(t) != len(m):
        raise ValueError("lomb_scargle_period: time and mag must have same length")
    if len(t) < 20:
        raise ValueError(
            f"lomb_scargle_period: n={len(t)} < 20; FAP cannot be calibrated. "
            "Use a longer time series or bootstrap a null-hypothesis surrogate."
        )

    err = np.asarray(mag_err, dtype=float) if mag_err is not None else None

    min_freq = 1.0 / max_period
    max_freq = 1.0 / min_period
    frequency = np.linspace(min_freq, max_freq, n_frequencies)

    ls = LombScargle(t, m, dy=err)
    power = ls.power(frequency)

    sorted_idx = np.argsort(power)[::-1]
    top_indices = sorted_idx[:5]
    top_periods = (1.0 / frequency[top_indices]).tolist()

    best_idx = sorted_idx[0]
    best_freq = frequency[best_idx]
    best_period = float(1.0 / best_freq)
    best_power = float(power[best_idx])

    # Spectral resolution + pseudo-Nyquist
    t_span = float(np.max(t) - np.min(t))
    spectral_resolution = 1.0 / t_span if t_span > 0 else np.nan
    sorted_t = np.sort(t)
    dt_median = float(np.median(np.diff(sorted_t))) if len(sorted_t) > 1 else np.nan
    nyquist_pseudo = 0.5 / dt_median if dt_median and np.isfinite(dt_median) and dt_median > 0 else np.nan

    # Window function: LS of a constant series on the same times.
    try:
        window_power = LombScargle(t, np.ones_like(m)).power(frequency)
        window_max_off = float(np.max(window_power[1:]))  # skip zero freq
    except Exception:
        window_max_off = float("nan")

    # Parabolic refinement around the peak for best_period_err.
    best_period_err = None
    if 0 < best_idx < len(power) - 1:
        y0, y1, y2 = power[best_idx - 1], power[best_idx], power[best_idx + 1]
        denom = (y0 - 2 * y1 + y2)
        if denom != 0:
            shift = 0.5 * (y0 - y2) / denom
            refined_freq = best_freq + shift * (frequency[1] - frequency[0])
            if refined_freq > 0:
                refined_period = 1.0 / refined_freq
                best_period_err = float(abs(refined_period - best_period))

    # FAP computation.
    method = fap_method
    if method == "auto":
        method = "bootstrap" if len(t) < 200 else "baluev"

    if method == "bootstrap":
        rng = np.random.default_rng(random_seed)
        null_max_power = np.zeros(n_bootstrap, dtype=float)
        for i in range(n_bootstrap):
            shuffled = rng.permutation(m)
            null_power = LombScargle(t, shuffled, dy=err).power(frequency)
            null_max_power[i] = float(np.max(null_power))
        fap_at_best = float(np.mean(null_max_power >= best_power))
    else:
        fap_at_best = float(ls.false_alarm_probability(best_power))

    if fap_at_best < 0.01:
        fap_label = "significant"
    elif fap_at_best < 0.05:
        fap_label = "marginal"
    else:
        fap_label = "unreliable"

    return {
        "best_period": best_period,
        "best_period_err": best_period_err,
        "best_power": best_power,
        "fap": fap_at_best,
        "fap_level": fap_label,
        "fap_method": method,
        "reliable": bool(fap_at_best < 0.01 and len(t) >= 20),
        "n_datapoints": len(t),
        "spectral_resolution_per_day": spectral_resolution,
        "nyquist_pseudo_per_day": nyquist_pseudo,
        "window_function_max_sidelobe": window_max_off,
        "frequencies": frequency.tolist(),
        "powers": power.tolist(),
        "top_periods": top_periods,
        "reference": "Baluev 2008 MNRAS 385, 1279; VanderPlas & Ivezic 2015",
    }


def phase_fold(time, period, epoch=None):
    """Phase-fold time array.

    Args:
        time: observation times array
        period: folding period
        epoch: reference epoch (defaults to min(time))

    Returns:
        phases array in [0, 1)
    """
    t = np.asarray(time, dtype=float)
    if epoch is None:
        epoch = float(np.min(t))
    phases = ((t - epoch) / period) % 1.0
    return phases


def plot_periodogram(time, mag, mag_err=None, min_period=0.1, max_period=100,
                     n_frequencies=10000, title="Lomb-Scargle Periodogram"):
    """Publication-quality Lomb-Scargle periodogram plot.

    Marks the best period and false alarm probability levels.

    Returns:
        matplotlib Figure
    """
    import matplotlib.pyplot as plt

    result = lomb_scargle_period(time, mag, mag_err=mag_err,
                                 min_period=min_period, max_period=max_period,
                                 n_frequencies=n_frequencies)

    periods = 1.0 / np.array(result["frequencies"])
    powers = np.array(result["powers"])

    fig, ax = pub_figure()

    ax.plot(periods, powers, color="#0A84FF", linewidth=0.8)

    # Mark best period
    ax.axvline(result["best_period"], color="#FF453A", linestyle="--",
               linewidth=1.2, alpha=0.8,
               label=f"Best P = {result['best_period']:.4f} d")

    # FAP levels
    from astropy.timeseries import LombScargle
    t = np.asarray(time, dtype=float)
    m = np.asarray(mag, dtype=float)
    err = np.asarray(mag_err, dtype=float) if mag_err is not None else None
    ls = LombScargle(t, m, dy=err)
    for fap_level, ls_label, color in [(0.05, "5% FAP", "#FF453A"),
                                       (0.01, "1% FAP", "#FF9F0A"),
                                       (0.001, "0.1% FAP", "#30D158")]:
        try:
            power_level = ls.false_alarm_level(fap_level)
            ax.axhline(float(power_level), color=color, linestyle=":",
                       linewidth=1.0, alpha=0.7, label=ls_label)
        except Exception:
            pass

    ax.set_xlabel("Period [days]")
    ax.set_ylabel("Lomb-Scargle Power")
    ax.set_title(title)
    ax.set_xscale("log")
    ax.legend(frameon=False, loc="upper right")
    plt.tight_layout()
    return fig


def plot_phase_folded(time, mag, period, mag_err=None, epoch=None,
                      title="Phase-Folded Light Curve"):
    """Publication-quality phase-folded light curve.

    Args:
        time: observation times
        mag: magnitudes
        period: folding period
        mag_err: magnitude uncertainties (optional)
        epoch: reference epoch (defaults to min(time))
        title: plot title

    Returns:
        matplotlib Figure
    """
    import matplotlib.pyplot as plt

    phases = phase_fold(time, period, epoch=epoch)

    fig, ax = pub_figure()

    if mag_err is not None:
        ax.errorbar(phases, mag, yerr=mag_err, fmt="o", ms=3,
                    color="#0A84FF", ecolor="#999", capsize=2, alpha=0.7)
    else:
        ax.scatter(phases, mag, s=9, color="#0A84FF", alpha=0.7)

    ax.set_xlabel("Phase")
    ax.set_ylabel("Magnitude")
    ax.set_title(f"{title} (P = {period:.4f} d)")
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    plt.tight_layout()
    return fig


def variability_indices(time, mag, mag_err=None):
    """Compute variability statistics for a light curve.

    Args:
        time: observation times array
        mag: magnitude array
        mag_err: magnitude uncertainties (optional)

    Returns:
        dict with stetson_J, stetson_K, eta, chi2_reduced, iqr, mad,
        amplitude, beyond_1sigma_fraction, skewness, kurtosis
    """
    from scipy.stats import skew, kurtosis as sp_kurtosis

    t = np.asarray(time, dtype=float)
    m = np.asarray(mag, dtype=float)

    if len(t) == 0 or len(m) == 0:
        raise ValueError("variability_indices: empty input arrays")
    if len(t) < 3:
        raise ValueError("variability_indices: need at least 3 data points")

    mean_m = np.mean(m)
    std_m = np.std(m, ddof=1) if len(m) > 1 else 1.0

    if mag_err is not None:
        err = np.asarray(mag_err, dtype=float)
        err = np.where(err <= 0, np.median(err[err > 0]) if np.any(err > 0) else 1.0, err)
    else:
        err = np.full_like(m, std_m if std_m > 0 else 1.0)

    # Weighted residuals
    d = (m - mean_m) / err
    n = len(m)

    # Stetson J index (consecutive pairs)
    stetson_j = 0.0
    n_pairs = n - 1
    if n_pairs > 0:
        for i in range(n_pairs):
            product = d[i] * d[i + 1]
            stetson_j += np.sign(product) * np.sqrt(np.abs(product))
        stetson_j /= n_pairs

    # Stetson K index (kurtosis-like)
    sum_abs_d = np.sum(np.abs(d))
    sum_d2 = np.sum(d ** 2)
    stetson_k = (sum_abs_d / np.sqrt(n * sum_d2)) if sum_d2 > 0 else 0.0

    # Von Neumann eta (successive differences)
    diffs = np.diff(m)
    eta = float(np.sum(diffs ** 2) / ((n - 1) * np.var(m, ddof=1))) if np.var(m, ddof=1) > 0 else 0.0

    # Reduced chi-squared
    chi2 = np.sum(((m - mean_m) / err) ** 2)
    chi2_reduced = float(chi2 / (n - 1)) if n > 1 else 0.0

    # IQR
    q75, q25 = np.percentile(m, [75, 25])
    iqr = float(q75 - q25)

    # MAD
    mad = float(np.median(np.abs(m - np.median(m))))

    # Amplitude (5th to 95th percentile range)
    p95, p5 = np.percentile(m, [95, 5])
    amplitude = float(p95 - p5)

    # Fraction beyond 1 sigma
    beyond = float(np.sum(np.abs(m - mean_m) > std_m) / n) if n > 0 else 0.0

    return {
        "stetson_J": float(stetson_j),
        "stetson_K": float(stetson_k),
        "eta": eta,
        "chi2_reduced": chi2_reduced,
        "iqr": iqr,
        "mad": mad,
        "amplitude": amplitude,
        "beyond_1sigma_fraction": beyond,
        "skewness": float(skew(m)),
        "kurtosis": float(sp_kurtosis(m)),
    }


def classify_variable(indices, period=None):
    """Simple rule-based variable star classifier from variability indices.

    Args:
        indices: dict from variability_indices()
        period: best period in days (optional, from lomb_scargle_period)

    Returns:
        dict with classification, confidence, reasoning
    """
    j = indices.get("stetson_J", 0)
    chi2 = indices.get("chi2_reduced", 0)
    amplitude = indices.get("amplitude", 0)
    skewness = indices.get("skewness", 0)
    beyond = indices.get("beyond_1sigma_fraction", 0)

    reasons = []

    # Non-variable check
    if chi2 < 2.0 and j < 0.5 and amplitude < 0.05:
        return {
            "classification": "non_variable",
            "confidence": 0.8,
            "reasoning": "Low chi2_reduced, low Stetson J, small amplitude",
        }

    if period is not None:
        if 0.2 < period < 1.0:
            reasons.append(f"Period {period:.3f}d in RR Lyrae range")
            if amplitude > 0.3 and skewness > 0:
                return {
                    "classification": "RR_Lyrae",
                    "confidence": 0.7,
                    "reasoning": "; ".join(reasons + ["Positive skewness, significant amplitude"]),
                }
        if 1.0 < period < 100.0 and amplitude > 0.3:
            reasons.append(f"Period {period:.3f}d with amplitude {amplitude:.2f}")
            if skewness < -0.2:
                return {
                    "classification": "eclipsing_binary",
                    "confidence": 0.6,
                    "reasoning": "; ".join(reasons + ["Negative skewness suggests eclipses"]),
                }
            if 1.0 < period < 80.0 and amplitude > 0.5:
                return {
                    "classification": "cepheid",
                    "confidence": 0.5,
                    "reasoning": "; ".join(reasons + ["Large amplitude, intermediate period"]),
                }
        if period > 80.0:
            return {
                "classification": "long_period_variable",
                "confidence": 0.5,
                "reasoning": f"Long period ({period:.1f}d) suggests LPV/Mira",
            }

    if j > 1.0 and beyond > 0.4:
        return {
            "classification": "irregular",
            "confidence": 0.4,
            "reasoning": f"High Stetson J ({j:.2f}), large beyond-1sigma fraction ({beyond:.2f}), no clear period",
        }

    return {
        "classification": "irregular",
        "confidence": 0.3,
        "reasoning": "Variable but no confident classification; manual inspection recommended",
    }


def voigt_fit(wavelength, flux, initial_centers=None, n_components=1):
    """Fit Voigt profiles to spectral lines.

    Uses scipy.special.voigt_profile. Each component has: amplitude, center,
    sigma (Gaussian width), gamma (Lorentzian width).

    Args:
        wavelength: wavelength array
        flux: flux array
        initial_centers: list of initial center wavelengths (optional)
        n_components: number of Voigt components to fit

    Returns:
        dict: components (list of {center, sigma, gamma, amplitude, fwhm}),
        fitted_flux, residuals, chi2_reduced.
    """
    from scipy.optimize import curve_fit
    from scipy.special import voigt_profile

    wave = np.asarray(wavelength, dtype=float)
    f = np.asarray(flux, dtype=float)

    # 4 params per component + 1 continuum offset
    n_params = 4 * n_components + 1

    if len(wave) < n_params + 1:
        return {"success": False, "error": f"Need at least {n_params + 1} data points, got {len(wave)}"}

    # Estimate continuum from edges of the spectrum
    n_edge = max(5, len(wave) // 10)
    continuum_est = float(np.median(np.concatenate([f[:n_edge], f[-n_edge:]])))

    def multi_voigt(x, *params):
        # Last param is continuum offset
        result = np.full_like(x, params[-1])
        for i in range(0, len(params) - 1, 4):
            amp, center, sigma, gamma = params[i], params[i + 1], abs(params[i + 2]), abs(params[i + 3])
            result += amp * voigt_profile(x - center, sigma, gamma)
        return result

    # Build initial guesses: subtract continuum for amplitude estimates
    f_sub = f - continuum_est
    p0 = []
    if initial_centers:
        for c in initial_centers[:n_components]:
            idx = np.argmin(np.abs(wave - c))
            p0.extend([f_sub[idx], c, 2.0, 1.0])
    else:
        from scipy.signal import find_peaks
        peaks, _ = find_peaks(f_sub, height=np.std(f_sub), distance=max(1, len(f_sub) // (n_components + 1)))
        peaks = peaks[:n_components]
        for p in peaks:
            p0.extend([f_sub[p], wave[p], 2.0, 1.0])

    # Pad if not enough peaks found
    while len(p0) < 4 * n_components:
        p0.extend([np.max(f_sub), np.mean(wave), 2.0, 1.0])
    # Append continuum initial guess
    p0.append(continuum_est)

    try:
        popt, pcov = curve_fit(multi_voigt, wave, f, p0=p0, maxfev=10000)
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    components = []
    for i in range(n_components):
        sigma = abs(popt[i * 4 + 2])
        gamma = abs(popt[i * 4 + 3])
        # FWHM of Voigt: approximation by Thompson et al.
        fg = 2.3548 * sigma
        fl = 2.0 * gamma
        fwhm = 0.5346 * fl + np.sqrt(0.2166 * fl**2 + fg**2)
        components.append({
            "amplitude": float(popt[i * 4]),
            "center": float(popt[i * 4 + 1]),
            "sigma": float(sigma),
            "gamma": float(gamma),
            "fwhm": float(fwhm),
        })

    model_flux = multi_voigt(wave, *popt)
    residuals = f - model_flux
    dof = max(len(f) - n_params, 1)
    chi2 = float(np.sum(residuals**2) / dof)

    return {
        "success": True,
        "components": components,
        "fitted_flux": model_flux.tolist(),
        "residuals": residuals.tolist(),
        "chi2_reduced": chi2,
    }


def velocity_dispersion(wavelength, flux, rest_wavelength, z=0):
    """Measure velocity dispersion from spectral line broadening.

    Fits a Gaussian to the line nearest rest_wavelength*(1+z), then converts
    sigma_lambda to sigma_v = c * sigma_lambda / lambda_0.

    Args:
        wavelength: wavelength array
        flux: flux array
        rest_wavelength: rest-frame wavelength of the line (Angstrom)
        z: redshift (default 0)

    Returns:
        dict: sigma_v_km_s, sigma_lambda, line_center, line_flux.
    """
    from scipy.optimize import curve_fit

    wave = np.asarray(wavelength, dtype=float)
    f = np.asarray(flux, dtype=float)

    obs_center = rest_wavelength * (1.0 + z)
    c_km_s = 299792.458  # speed of light in km/s

    # Select a window around the expected line center
    half_window = max(20.0, 0.01 * obs_center)
    mask = (wave >= obs_center - half_window) & (wave <= obs_center + half_window)
    if np.sum(mask) < 4:
        return {"success": False, "error": "Too few points near the line center"}

    w_sub = wave[mask]
    f_sub = f[mask]

    def gaussian(x, amp, mu, sigma, offset):
        return amp * np.exp(-0.5 * ((x - mu) / sigma) ** 2) + offset

    p0 = [np.max(f_sub) - np.median(f_sub), obs_center, 2.0, np.median(f_sub)]

    try:
        popt, pcov = curve_fit(gaussian, w_sub, f_sub, p0=p0, maxfev=10000)
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    amp, mu, sigma, offset = popt
    sigma = abs(sigma)
    sigma_v = c_km_s * sigma / mu

    return {
        "success": True,
        "sigma_v_km_s": float(sigma_v),
        "sigma_lambda": float(sigma),
        "line_center": float(mu),
        "line_flux": float(amp),
    }


def radial_velocity(wavelength, flux, template_wave, template_flux):
    """Measure radial velocity via cross-correlation.

    Interpolates both spectrum and template onto a common log-wavelength grid,
    cross-correlates, and finds the peak shift.

    Args:
        wavelength: observed wavelength array
        flux: observed flux array
        template_wave: template wavelength array
        template_flux: template flux array

    Returns:
        dict: rv_km_s, cc_peak, velocity_array, cc_array.
    """
    from scipy.signal import correlate

    wave = np.asarray(wavelength, dtype=float)
    f = np.asarray(flux, dtype=float)
    tw = np.asarray(template_wave, dtype=float)
    tf = np.asarray(template_flux, dtype=float)

    # Common wavelength range
    w_min = max(wave.min(), tw.min())
    w_max = min(wave.max(), tw.max())
    if w_max <= w_min:
        return {"success": False, "error": "No overlapping wavelength range"}

    # Build common log-wavelength grid
    n_pix = min(len(wave), len(tw), 2048)
    log_wave = np.linspace(np.log(w_min), np.log(w_max), n_pix)
    common_wave = np.exp(log_wave)
    dlog = log_wave[1] - log_wave[0]

    # Interpolate both onto common grid
    obs_interp = np.interp(common_wave, wave, f)
    tpl_interp = np.interp(common_wave, tw, tf)

    # Subtract mean and normalize
    obs_interp -= np.mean(obs_interp)
    tpl_interp -= np.mean(tpl_interp)
    obs_norm = np.sqrt(np.sum(obs_interp**2))
    tpl_norm = np.sqrt(np.sum(tpl_interp**2))
    if obs_norm == 0 or tpl_norm == 0:
        return {"success": False, "error": "Flat spectrum — cannot cross-correlate"}
    obs_interp /= obs_norm
    tpl_interp /= tpl_norm

    # Cross-correlate
    cc = correlate(obs_interp, tpl_interp, mode="full")
    lags = np.arange(len(cc)) - (len(tpl_interp) - 1)

    # Convert lags to velocity
    c_km_s = 299792.458
    velocity = lags * dlog * c_km_s

    # Find peak
    peak_idx = np.argmax(cc)
    rv = float(velocity[peak_idx])
    cc_peak = float(cc[peak_idx])

    return {
        "success": True,
        "rv_km_s": rv,
        "cc_peak": cc_peak,
        "velocity_array": velocity.tolist(),
        "cc_array": cc.tolist(),
    }


def spectral_template_match(wavelength, flux, flux_err=None):
    """Match spectrum against stellar spectral type templates.

    Uses parametric polynomial approximations for O/B/A/F/G/K/M main sequence
    types. Each template is a polynomial continuum shape normalized at 5500 A.

    Args:
        wavelength: wavelength array (Angstrom)
        flux: flux array
        flux_err: flux uncertainty array (optional, used for weighting)

    Returns:
        dict: best_type, chi2_values (per type), all_types sorted by chi2.
    """
    wave = np.asarray(wavelength, dtype=float)
    f = np.asarray(flux, dtype=float)

    # Normalize observed spectrum at 5500 A
    ref_idx = np.argmin(np.abs(wave - 5500.0))
    ref_flux = f[ref_idx] if f[ref_idx] != 0 else 1.0
    f_norm = f / ref_flux

    if flux_err is not None:
        err = np.asarray(flux_err, dtype=float) / abs(ref_flux)
        err = np.where(err > 0, err, 1.0)
    else:
        err = np.ones_like(f_norm)

    # Normalized wavelength for polynomial evaluation
    x = (wave - 5500.0) / 1000.0  # in units of 1000 A from 5500

    # Parametric templates: polynomial coefficients for continuum shape
    # Approximations of main-sequence spectral energy distributions.
    # Coefficients [c0, c1, c2, c3] for polynomial c0 + c1*x + c2*x^2 + c3*x^3
    # where x = (wavelength - 5500) / 1000.
    # x = (lambda - 5500)/1000: negative = blue, positive = red
    # Hot stars are brighter in blue (negative x → higher flux) → negative c1
    # Cool stars are brighter in red (positive x → higher flux) → positive c1
    templates = {
        "O": [1.0, -0.30, 0.02, -0.001],   # hot, blue-bright
        "B": [1.0, -0.20, 0.015, -0.0008],  # hot, moderately blue
        "A": [1.0, -0.08, 0.01, -0.0005],   # mild blue slope
        "F": [1.0, -0.02, 0.005, -0.0002],  # nearly flat
        "G": [1.0, 0.05, -0.005, 0.001],    # slight red rise
        "K": [1.0, 0.12, -0.015, 0.002],    # red-bright
        "M": [1.0, 0.25, -0.04, 0.004],     # strongly red-bright
    }

    chi2_values = {}
    for stype, coeffs in templates.items():
        template = np.polyval(coeffs[::-1], x)  # polyval uses highest-degree first
        residuals = (f_norm - template) / err
        chi2_values[stype] = float(np.sum(residuals**2) / max(len(f) - len(coeffs), 1))

    sorted_types = sorted(chi2_values.keys(), key=lambda t: chi2_values[t])
    best = sorted_types[0]

    return {
        "success": True,
        "best_type": best,
        "chi2_values": chi2_values,
        "all_types": sorted_types,
    }


# ── Observation Planning ──

# Observatory presets (using astropy site registry names)
_OBSERVATORY_PRESETS = {
    "paranal": "Cerro Paranal",          # VLT
    "mauna_kea": "Mauna Kea",            # Keck / Gemini-N
    "la_palma": "Roque de los Muchachos",  # GTC / WHT
    "cerro_pachon": "Cerro Pachon",      # Gemini-S / Rubin
    "arecibo": "Arecibo Observatory",    # Arecibo
    "alma": "ALMA",                      # ALMA
    "subaru": "Subaru",                  # Subaru (Mauna Kea)
}

# Telescope to observatory mapping
_TELESCOPE_OBSERVATORY = {
    "vlt": "paranal",
    "keck": "mauna_kea",
    "gemini": "mauna_kea",
    "gemini-n": "mauna_kea",
    "gemini-s": "cerro_pachon",
    "hst": "paranal",       # HST is space-based; use Paranal as placeholder
    "jwst": "paranal",      # JWST is space-based; use Paranal as placeholder
    "subaru": "mauna_kea",
    "alma": "alma",
    "gtc": "la_palma",
    "rubin": "cerro_pachon",
}


def _get_observer_location(observatory):
    """Resolve an observatory name or (lat, lon, alt) tuple to an EarthLocation.

    Args:
        observatory: string name (e.g. 'paranal') or (lat_deg, lon_deg, alt_m) tuple.

    Returns:
        astropy.coordinates.EarthLocation
    """
    from astropy.coordinates import EarthLocation
    import astropy.units as u

    if isinstance(observatory, (list, tuple)) and len(observatory) >= 2:
        lat, lon = observatory[0], observatory[1]
        alt = observatory[2] if len(observatory) > 2 else 0.0
        return EarthLocation.from_geodetic(lon=lon * u.deg, lat=lat * u.deg,
                                           height=alt * u.m)

    name = str(observatory).lower().replace("-", "_").replace(" ", "_")

    # Try our preset mapping first (uses astropy site names)
    site_name = _OBSERVATORY_PRESETS.get(name)
    if site_name is not None:
        try:
            return EarthLocation.of_site(site_name)
        except Exception:
            pass

    # Fall back to astropy's own site registry
    try:
        return EarthLocation.of_site(observatory)
    except Exception:
        raise ValueError(
            f"Unknown observatory '{observatory}'. Known presets: "
            f"{list(_OBSERVATORY_PRESETS.keys())}. "
            "Or pass (lat_deg, lon_deg, alt_m) tuple."
        )


def target_visibility(ra, dec, observatory="paranal", date=None, utc_offset=0):
    """Compute target visibility from an observatory.

    Args:
        ra: Right ascension in degrees (ICRS).
        dec: Declination in degrees (ICRS).
        observatory: 'paranal' (VLT), 'mauna_kea' (Keck/Gemini), 'la_palma' (GTC),
                     'cerro_pachon' (Gemini-S), 'arecibo', 'alma', or custom
                     (lat_deg, lon_deg, alt_m) tuple.
        date: 'YYYY-MM-DD' string, defaults to today.
        utc_offset: Hours offset from UTC for display (default 0 = UTC).

    Returns:
        dict: rise_time_utc, set_time_utc, transit_time_utc, transit_altitude_deg,
              max_airmass, hours_observable (alt>30deg), is_circumpolar, never_rises.
    """
    from astropy.coordinates import SkyCoord, AltAz
    from astropy.time import Time
    import astropy.units as u
    from datetime import datetime

    location = _get_observer_location(observatory)
    target = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")

    if date is None:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
    else:
        date_str = str(date)

    # Start at noon UTC on the given date, span 24 hours
    noon = Time(f"{date_str}T12:00:00", format="isot", scale="utc")

    # Sample every 10 minutes through 24 hours starting at noon
    n_steps = 144  # 24 hours * 6
    times = noon + np.linspace(0, 24, n_steps) * u.hour

    altaz_frame = AltAz(obstime=times, location=location)
    target_altaz = target.transform_to(altaz_frame)
    altitudes = target_altaz.alt.deg

    # Find transit (maximum altitude)
    transit_idx = np.argmax(altitudes)
    transit_alt = float(altitudes[transit_idx])
    transit_time = times[transit_idx]

    # Airmass at transit (sec(z) approximation, only valid for alt > 0)
    if transit_alt > 0:
        max_airmass = float(1.0 / np.cos(np.radians(90.0 - transit_alt)))
    else:
        max_airmass = None

    # Check circumpolar / never rises
    is_circumpolar = bool(np.all(altitudes > 0))
    never_rises = bool(np.all(altitudes <= 0))

    # Hours observable (altitude > 30 deg)
    above_30 = altitudes > 30.0
    hours_observable = float(np.sum(above_30) * (24.0 / n_steps))

    # Rise and set times (crossing 0 deg altitude)
    rise_time_utc = None
    set_time_utc = None
    if not is_circumpolar and not never_rises:
        for i in range(1, len(altitudes)):
            if altitudes[i - 1] <= 0 < altitudes[i] and rise_time_utc is None:
                rise_time_utc = times[i].iso
            if altitudes[i - 1] > 0 >= altitudes[i] and set_time_utc is None and rise_time_utc is not None:
                set_time_utc = times[i].iso

    return {
        "rise_time_utc": rise_time_utc,
        "set_time_utc": set_time_utc,
        "transit_time_utc": transit_time.iso,
        "transit_altitude_deg": round(transit_alt, 2),
        "max_airmass": round(max_airmass, 3) if max_airmass else None,
        "hours_observable": round(hours_observable, 2),
        "is_circumpolar": is_circumpolar,
        "never_rises": never_rises,
        "observatory": str(observatory),
        "date": date_str,
    }


def airmass_plot(ra, dec, observatory="paranal", date=None):
    """Generate publication-quality airmass vs. time plot.

    Shows airmass curve over the night with twilight shading.

    Args:
        ra: Right ascension in degrees (ICRS).
        dec: Declination in degrees (ICRS).
        observatory: Observatory name or (lat, lon, alt) tuple.
        date: 'YYYY-MM-DD' string, defaults to today.

    Returns:
        matplotlib Figure.
    """
    import matplotlib.pyplot as plt
    from astropy.coordinates import SkyCoord, AltAz, get_sun
    from astropy.time import Time
    import astropy.units as u
    from datetime import datetime

    location = _get_observer_location(observatory)
    target = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")

    if date is None:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
    else:
        date_str = str(date)

    # Span from noon to noon (covers the full night)
    noon = Time(f"{date_str}T12:00:00", format="isot", scale="utc")
    n_steps = 200
    times = noon + np.linspace(0, 24, n_steps) * u.hour

    altaz_frame = AltAz(obstime=times, location=location)
    target_altaz = target.transform_to(altaz_frame)
    altitudes = target_altaz.alt.deg

    # Compute airmass (Pickering 2002 formula, valid closer to horizon)
    airmass = np.full_like(altitudes, np.nan)
    above_horizon = altitudes > 1.0
    alt_rad = np.radians(altitudes[above_horizon])
    airmass[above_horizon] = 1.0 / np.sin(alt_rad + 0.0014 * np.degrees(alt_rad))

    # Sun altitude for twilight shading
    sun_altaz = get_sun(times).transform_to(altaz_frame)
    sun_alt = sun_altaz.alt.deg

    # Time axis as hours since noon UTC
    hours = np.linspace(0, 24, n_steps)

    fig, ax = pub_figure()

    # Twilight shading
    for i in range(len(hours) - 1):
        if sun_alt[i] < -18:
            ax.axvspan(hours[i], hours[i + 1], alpha=0.15, color="#1a1a2e", zorder=0)
        elif sun_alt[i] < -12:
            ax.axvspan(hours[i], hours[i + 1], alpha=0.10, color="#16213e", zorder=0)
        elif sun_alt[i] < -6:
            ax.axvspan(hours[i], hours[i + 1], alpha=0.08, color="#0f3460", zorder=0)
        elif sun_alt[i] < 0:
            ax.axvspan(hours[i], hours[i + 1], alpha=0.05, color="#e94560", zorder=0)

    ax.plot(hours, airmass, color="#0A84FF", linewidth=1.8, label="Target")

    # Mark airmass = 2 guideline
    ax.axhline(2.0, color="#FF9F0A", linestyle="--", linewidth=0.8, alpha=0.7,
               label="Airmass = 2")
    ax.axhline(1.5, color="#30D158", linestyle=":", linewidth=0.8, alpha=0.5,
               label="Airmass = 1.5")

    ax.set_xlabel(f"Hours since 12:00 UTC on {date_str}")
    ax.set_ylabel("Airmass")
    ax.set_title(f"Airmass Plot — RA={ra:.3f}, Dec={dec:.3f} from {observatory}")
    ax.set_ylim(3.5, 1.0)  # Inverted: lower airmass = better
    ax.set_xlim(0, 24)
    ax.legend(frameon=False, loc="upper right")
    plt.tight_layout()
    return fig


# Telescope parameters for exposure time estimation
_TELESCOPE_PARAMS = {
    "vlt": {"diameter_m": 8.2, "qe": 0.85, "read_noise": 4.0, "dark_current": 0.002,
            "name": "VLT (8.2m)"},
    "keck": {"diameter_m": 10.0, "qe": 0.80, "read_noise": 5.0, "dark_current": 0.003,
             "name": "Keck (10m)"},
    "gemini": {"diameter_m": 8.1, "qe": 0.82, "read_noise": 4.5, "dark_current": 0.002,
               "name": "Gemini (8.1m)"},
    "hst": {"diameter_m": 2.4, "qe": 0.70, "read_noise": 3.0, "dark_current": 0.005,
            "name": "HST (2.4m)"},
    "jwst": {"diameter_m": 6.5, "qe": 0.90, "read_noise": 6.0, "dark_current": 0.01,
             "name": "JWST (6.5m)"},
    "subaru": {"diameter_m": 8.2, "qe": 0.82, "read_noise": 4.0, "dark_current": 0.002,
               "name": "Subaru (8.2m)"},
}

# Zero-point fluxes in photons/s/cm^2/A for common bands (Vega system, approximate)
_BAND_ZEROPOINTS = {
    "U": {"lambda_eff": 3600, "dlambda": 660, "flux0": 1810},
    "B": {"lambda_eff": 4400, "dlambda": 940, "flux0": 4260},
    "V": {"lambda_eff": 5500, "dlambda": 880, "flux0": 3640},
    "R": {"lambda_eff": 6400, "dlambda": 1380, "flux0": 3080},
    "I": {"lambda_eff": 7900, "dlambda": 1490, "flux0": 2550},
    "J": {"lambda_eff": 12500, "dlambda": 1620, "flux0": 1600},
    "H": {"lambda_eff": 16500, "dlambda": 2510, "flux0": 1080},
    "K": {"lambda_eff": 21600, "dlambda": 2620, "flux0": 670},
    "g": {"lambda_eff": 4770, "dlambda": 1500, "flux0": 3730},
    "r": {"lambda_eff": 6231, "dlambda": 1370, "flux0": 3170},
    "i": {"lambda_eff": 7625, "dlambda": 1530, "flux0": 2510},
    "z": {"lambda_eff": 9134, "dlambda": 950, "flux0": 2230},
}


def exposure_time_estimate(target_mag, snr_target=10, telescope="vlt",
                           filter_band="V", seeing=1.0):
    """Rough exposure time calculator using the standard CCD equation.

    This is a simplified ETC for quick estimates. For precise values,
    use the telescope's official ETC.

    Args:
        target_mag: Target apparent magnitude in the chosen band.
        snr_target: Desired signal-to-noise ratio (default 10).
        telescope: 'vlt' (8.2m), 'keck' (10m), 'gemini' (8.1m), 'hst' (2.4m),
                   'jwst' (6.5m), 'subaru' (8.2m).
        filter_band: Photometric band ('U','B','V','R','I','J','H','K','g','r','i','z').
        seeing: Seeing FWHM in arcseconds (default 1.0).

    Returns:
        dict: exposure_time_seconds, electrons_target, electrons_sky,
              snr_achieved, notes.
    """
    tel = _TELESCOPE_PARAMS.get(telescope.lower())
    if tel is None:
        raise ValueError(
            f"Unknown telescope '{telescope}'. "
            f"Available: {list(_TELESCOPE_PARAMS.keys())}"
        )

    band = _BAND_ZEROPOINTS.get(filter_band)
    if band is None:
        raise ValueError(
            f"Unknown filter band '{filter_band}'. "
            f"Available: {list(_BAND_ZEROPOINTS.keys())}"
        )

    # Telescope collecting area in cm^2
    diameter_cm = tel["diameter_m"] * 100.0
    area_cm2 = np.pi * (diameter_cm / 2.0) ** 2

    # Target photon rate (photons/s on detector)
    flux0_phot = band["flux0"]  # approx photons/s/cm^2/A at mag=0
    dlambda = band["dlambda"]
    target_flux = flux0_phot * 10.0 ** (-0.4 * target_mag)
    rate_target = target_flux * dlambda * area_cm2 * tel["qe"]  # photons/s

    # Sky background (mag/arcsec^2, varies by band)
    sky_mag_per_arcsec2 = {"U": 22.0, "B": 22.7, "V": 21.8, "R": 20.9, "I": 19.9,
                           "J": 16.0, "H": 14.4, "K": 12.6,
                           "g": 22.0, "r": 21.0, "i": 20.0, "z": 19.2}
    sky_mag = sky_mag_per_arcsec2.get(filter_band, 21.5)

    # Aperture area in arcsec^2 (circular aperture = 1.5 * FWHM diameter)
    aperture_radius = 1.5 * seeing / 2.0  # arcsec
    aperture_area = np.pi * aperture_radius ** 2  # arcsec^2

    sky_flux = flux0_phot * 10.0 ** (-0.4 * sky_mag) * dlambda * area_cm2 * tel["qe"]
    rate_sky = sky_flux * aperture_area  # photons/s in aperture

    # Number of pixels in aperture
    pixel_scale = 0.2  # arcsec/pixel (typical for large telescopes)
    n_pix = aperture_area / (pixel_scale ** 2)

    read_noise = tel["read_noise"]
    dark_current = tel["dark_current"]

    # CCD equation: SNR = S*t / sqrt(S*t + B*t + D*t*npix + R^2*npix)
    # Quadratic in t: S^2*t^2 - snr^2*(S+B+D*npix)*t - snr^2*R^2*npix = 0
    S = rate_target
    B = rate_sky
    D = dark_current * n_pix
    R2N = read_noise ** 2 * n_pix
    snr2 = snr_target ** 2

    a_coeff = S ** 2
    b_coeff = -snr2 * (S + B + D)
    c_coeff = -snr2 * R2N

    if a_coeff == 0:
        return {
            "exposure_time_seconds": None,
            "error": "Target too faint — zero signal rate",
            "notes": "Target produces negligible signal on this telescope/band combination."
        }

    discriminant = b_coeff ** 2 - 4 * a_coeff * c_coeff
    t_exp = (-b_coeff + np.sqrt(discriminant)) / (2 * a_coeff)
    t_exp = max(float(t_exp), 0.1)

    # Compute achieved SNR at this exposure
    signal = S * t_exp
    noise_sq = S * t_exp + B * t_exp + dark_current * n_pix * t_exp + R2N
    snr_achieved = signal / np.sqrt(noise_sq) if noise_sq > 0 else 0.0

    notes = []
    if t_exp > 3600:
        notes.append("Long exposure — consider splitting into sub-exposures.")
    if t_exp > 36000:
        notes.append("Very long integration — may require multiple nights.")
    if seeing > 1.5:
        notes.append("Poor seeing conditions — consider adaptive optics.")
    notes.append(f"Simplified ETC; use {tel['name']} official ETC for proposals.")

    return {
        "exposure_time_seconds": round(t_exp, 1),
        "electrons_target": round(float(signal), 0),
        "electrons_sky": round(float(B * t_exp), 0),
        "snr_achieved": round(float(snr_achieved), 2),
        "telescope": tel["name"],
        "filter_band": filter_band,
        "seeing_arcsec": seeing,
        "aperture_arcsec2": round(float(aperture_area), 2),
        "n_pixels": round(float(n_pix), 0),
        "notes": " ".join(notes),
    }


# ── Light Curve Analysis (lightkurve) ──

def search_lightcurve(target, mission='kepler'):
    """Search for Kepler/TESS/K2 light curves via lightkurve.

    H0.3: `r.exptime` is an astropy Quantity; using `if r.exptime` triggers
    "Quantity truthiness is ambiguous, especially for logarithmic units and
    temperatures." — the bug the Paper 2 reviewer hit.  Use explicit
    `is not None` check instead.  Also try Simbad name resolution as a
    fallback when direct search returns empty for well-known aliases.
    """
    import lightkurve as lk

    def _exptime(r):
        try:
            et = getattr(r, "exptime", None)
            if et is None:
                return None
            val = getattr(et, "value", et)
            return float(val)
        except Exception:
            return None

    def _do_search(q):
        try:
            return lk.search_lightcurve(q, mission=mission)
        except Exception as e:
            return e

    # H0.3 target resolution fallback: well-known exoplanet host aliases
    # like HD 189733, HD 209458 sometimes don't hit MAST directly.  Try
    # the original target, then Simbad-resolved coordinates.
    result = _do_search(target)
    if isinstance(result, Exception) or (hasattr(result, "__len__") and len(result) == 0):
        try:
            from astropy.coordinates import SkyCoord
            coord = SkyCoord.from_name(target)
            result2 = _do_search(coord)
            if not isinstance(result2, Exception) and hasattr(result2, "__len__") and len(result2) > 0:
                result = result2
        except Exception:
            pass  # fall through with the original result

    if isinstance(result, Exception):
        return {
            "found": 0,
            "error": f"lightkurve search_lightcurve failed: {type(result).__name__}: {result}",
            "mission": mission,
            "target": target,
        }

    if len(result) == 0:
        return {"found": 0, "message": f"No {mission} light curves found for {target}"}
    return {
        "found": len(result),
        "results": [{"mission": str(r.mission), "target": str(r.target_name),
                      "exptime": _exptime(r)}
                     for r in result[:20]]
    }


def download_and_clean_lightcurve(target, mission='kepler', flatten=True):
    """Download, stitch, and clean a light curve."""
    import lightkurve as lk
    search = lk.search_lightcurve(target, mission=mission)
    if len(search) == 0:
        raise ValueError(f"No {mission} light curves found for {target}")
    lc_collection = search.download_all()
    lc = lc_collection.stitch()
    if flatten:
        lc = lc.remove_outliers().flatten()
    return {
        'time': lc.time.value.tolist(),
        'flux': lc.flux.value.tolist(),
        'flux_err': lc.flux_err.value.tolist() if lc.flux_err is not None else None,
        'meta': {'target': target, 'mission': mission, 'segments': len(lc_collection)}
    }


def transit_search(target, mission='kepler'):
    """Search for periodic transits using BLS periodogram."""
    import lightkurve as lk
    search = lk.search_lightcurve(target, mission=mission)
    if len(search) == 0:
        raise ValueError(f"No {mission} light curves found for {target}")
    lc = search.download_all().stitch().flatten()
    pg = lc.to_periodogram(method='bls')
    return {
        'period_days': float(pg.period_at_max_power.value),
        'transit_time': float(pg.transit_time_at_max_power.value),
        'depth': float(pg.depth_at_max_power.value),
        'max_power': float(pg.max_power.value),
    }


# ── Source Extraction (SEP) ──

def extract_sources(image_data, threshold_sigma=3.0, min_area=5):
    """Extract sources from a 2D image using SEP (SExtractor in Python)."""
    import sep
    data = np.asarray(image_data, dtype=np.float64)
    # Ensure C-contiguous
    if not data.flags['C_CONTIGUOUS']:
        data = np.ascontiguousarray(data)
    bkg = sep.Background(data)
    data_sub = data - bkg
    objects = sep.extract(data_sub, threshold_sigma, err=bkg.globalrms, minarea=min_area)
    if len(objects) == 0:
        return {'n_sources': 0, 'sources': []}
    # Kron aperture photometry
    kronrad, krflag = sep.kron_radius(data_sub, objects['x'], objects['y'],
                                       objects['a'], objects['b'], objects['theta'], 6.0)
    flux, fluxerr, flag = sep.sum_ellipse(data_sub, objects['x'], objects['y'],
                                           objects['a'], objects['b'], objects['theta'],
                                           2.5 * kronrad, err=bkg.globalrms)
    sources = []
    for i in range(len(objects)):
        sources.append({
            'x': float(objects['x'][i]),
            'y': float(objects['y'][i]),
            'a': float(objects['a'][i]),
            'b': float(objects['b'][i]),
            'theta': float(objects['theta'][i]),
            'flux': float(flux[i]),
            'flux_err': float(fluxerr[i]),
            'mag': float(-2.5 * np.log10(max(flux[i], 1e-30))),
        })
    return {'n_sources': len(sources), 'sources': sources}


# ── Professional spectral analysis (specutils) ──


def pro_load_spectrum(fits_path: str) -> dict:
    """Load spectrum from FITS file with specutils."""
    from app.services.spectral_analysis_pro import load_spectrum
    return load_spectrum(fits_path)


def pro_identify_lines(wavelength, flux, flux_err=None, threshold_snr=3.0, redshift=0.0):
    """Identify spectral lines against NIST catalog."""
    from app.services.spectral_analysis_pro import identify_lines
    return identify_lines(wavelength, flux, flux_err, threshold_snr, redshift=redshift)


def pro_fit_lines(wavelength, flux, line_centers=None, model="gaussian"):
    """Fit spectral lines with Gaussian/Lorentzian/Voigt profiles via specutils."""
    from app.services.spectral_analysis_pro import fit_lines
    return fit_lines(wavelength, flux, line_centers=line_centers, model=model)


def pro_measure_ew(wavelength, flux, line_center, window=20.0):
    """Measure equivalent width via specutils."""
    from app.services.spectral_analysis_pro import measure_equivalent_width
    return measure_equivalent_width(wavelength, flux, line_center, window)


def pro_heliocentric(wavelength, flux, ra, dec, obstime, observatory="greenwich"):
    """Apply heliocentric velocity correction."""
    from app.services.spectral_analysis_pro import heliocentric_correction
    return heliocentric_correction(wavelength, flux, ra, dec, obstime, observatory)


# ── Bayesian inference (dynesty/arviz) ──


def pro_nested_sampling(log_likelihood, prior_transform, ndim, nlive=500, method="dynesty"):
    """Run nested sampling for Bayesian evidence computation."""
    from app.services.bayesian_inference import nested_sampling
    return nested_sampling(log_likelihood, prior_transform, ndim, nlive=nlive, method=method)


def pro_bayes_factor(logZ1, logZ2):
    """Compute Bayes factor between two models."""
    from app.services.bayesian_inference import compute_bayes_factor
    return compute_bayes_factor(logZ1, logZ2)


def pro_chain_diagnostics(samples, parameter_names=None):
    """Compute MCMC chain diagnostics (R-hat, ESS, MCSE) via ArviZ."""
    from app.services.bayesian_inference import chain_diagnostics
    return chain_diagnostics(samples, parameter_names)


def pro_model_comparison(models):
    """Compare models using BIC/AIC and/or Bayesian evidence."""
    from app.services.bayesian_inference import model_comparison_table
    return model_comparison_table(models)


# ── Time-domain astronomy (celerite2/batman) ──


def pro_gp_detrend(time, flux, flux_err=None, kernel="matern32"):
    """GP detrend a light curve via celerite2."""
    from app.services.time_domain_pro import gp_detrend
    return gp_detrend(time, flux, flux_err, kernel)


def pro_fit_transit(time, flux, flux_err=None, period=1.0, t0=0.0, rp_rs=0.1):
    """Fit a transit model via batman."""
    from app.services.time_domain_pro import fit_transit
    return fit_transit(time, flux, flux_err, period, t0, rp_rs)


def pro_detect_flares(time, flux, flux_err=None, nsigma=3.0):
    """Detect stellar flares in a light curve."""
    from app.services.time_domain_pro import detect_flares
    return detect_flares(time, flux, flux_err, nsigma)


def pro_bls_search(time, flux, period_range=(0.5, 20.0)):
    """BLS transit search."""
    from app.services.time_domain_pro import transit_search_bls
    return transit_search_bls(time, flux, period_range)


# ── Advanced image processing (reproject/photutils) ──


def pro_reproject(fits_path, target_ra=None, target_dec=None, pixscale=1.0):
    """Reproject a FITS image."""
    from app.services.image_processing_pro import reproject_image
    return reproject_image(fits_path, target_ra=target_ra, target_dec=target_dec, pixscale=pixscale)


def pro_deblend(fits_path, threshold_sigma=2.0):
    """Detect and deblend sources in a FITS image."""
    from app.services.image_processing_pro import deblend_sources
    return deblend_sources(fits_path, threshold_sigma)


def pro_cutout(fits_path, ra, dec, size_arcsec=60.0):
    """Extract a postage stamp cutout from a FITS image."""
    from app.services.image_processing_pro import cutout_service
    return cutout_service(fits_path, ra, dec, size_arcsec)


def wd_cooling_age(M_G, Teff=None, mass_Msun=0.6, atmosphere="H"):
    """White dwarf cooling age from absolute G magnitude and/or Teff.

    References:
    - Bédard, Bergeron, Brassard & Fontaine 2020 ApJ 901, 93 — Montreal
      cooling tables (definitive). Use these for publication work; download
      from http://www.astro.umontreal.ca/~bergeron/CoolingModels/
    - Fontaine, Brassard & Bergeron 2001 PASP 113, 409 — earlier calibration
    - Tremblay+ 2019 MNRAS 482, 5222 — Gaia-DR2 WD luminosity function

    This function provides a log-log spline interpolation over a 10-point
    table extracted from the Bédard+ 2020 DA models (0.6 Msun). It is a
    quick-look approximation — for high-precision work, interpolate the
    full Bédard+ 2020 grids directly.

    Parameters:
        M_G: absolute G magnitude (scalar or array). Brighter → younger.
        Teff: effective temperature in K (optional). If provided, uses Teff
              instead of M_G.
        mass_Msun: WD mass in solar masses (default 0.6, typical for DA).
        atmosphere: "H" for DA (hydrogen) or "He" for DB (helium). DB WDs
                    cool ~15% faster at given Teff.

    Returns:
        cooling_age_gyr: ndarray of cooling ages in Gyr (no hard cap).
    """
    import numpy as _np

    M_G = _np.asarray(M_G, dtype=float)

    if Teff is None:
        # M_G → Teff relation for DA 0.6 Msun WDs from Gaia DR2 catalog
        # (Gentile Fusillo+ 2019; Tremblay+ 2019). Calibrated pairs:
        #   M_G ≈ 10.5 → Teff ≈ 30000 K
        #   M_G ≈ 11.5 → Teff ≈ 20000 K
        #   M_G ≈ 12.5 → Teff ≈ 13000 K
        #   M_G ≈ 13.5 → Teff ≈ 9000 K
        #   M_G ≈ 14.5 → Teff ≈ 6500 K
        #   M_G ≈ 15.5 → Teff ≈ 4500 K
        # Linear fit in log10(Teff) vs M_G: slope ≈ -0.17 per mag
        _mg_tbl = [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0, 15.5, 16.0]
        _teff_tbl = [40000, 30000, 24000, 20000, 16500, 13000, 11000, 9000, 7500, 6500, 5500, 4500, 3800]
        Teff = _np.interp(M_G, _mg_tbl, _teff_tbl[::1])  # linear; M_G increasing
    else:
        Teff = _np.asarray(Teff, dtype=float)

    # Bédard+ 2020 DA 0.6 Msun cooling curve (Teff in K, age in Gyr)
    # Values spot-checked against http://www.astro.umontreal.ca/~bergeron/CoolingModels/
    _teff_grid = _np.array([
        40000, 30000, 20000, 15000, 12000, 10000, 8000, 7000, 6000, 5000, 4000, 3500, 3000
    ], dtype=float)
    _age_grid = _np.array([
        0.00025, 0.001, 0.04, 0.12, 0.28, 0.50, 1.2, 1.8, 2.7, 4.0, 7.0, 9.5, 12.0
    ], dtype=float)

    # Interpolate in log-log space (age is smooth function of Teff on log axes)
    log_Teff_grid = _np.log10(_teff_grid[::-1])
    log_age_grid = _np.log10(_age_grid[::-1])

    Teff_clip = _np.clip(Teff, _teff_grid.min(), _teff_grid.max())
    log_Teff_in = _np.log10(Teff_clip)
    log_age = _np.interp(log_Teff_in, log_Teff_grid, log_age_grid)
    base_age_gyr = 10.0 ** log_age

    # Mass scaling: t_cool ∝ M (more massive → longer cooling at same Teff
    # because of larger heat reservoir; Mestel approximation)
    base_age_gyr = base_age_gyr * (mass_Msun / 0.6)

    # DB (He) atmospheres cool ~15% faster at given Teff
    if atmosphere.upper() in ("HE", "DB"):
        base_age_gyr *= 0.85

    # Physical floor for very hot pre-WDs
    base_age_gyr = _np.where(base_age_gyr < 1e-4, 1e-4, base_age_gyr)

    return base_age_gyr


def bss_select(bp_rp, abs_mag, turnoff_bp_rp, turnoff_M_G,
               dmag_min=0.3, dmag_max=3.0, dcolor_max=0.5):
    """Identify blue straggler (BSS) candidates in a cluster CMD.

    Reference: Rain+ 2021 A&A 650, A67 (Gaia DR3 open cluster BSS criteria).
    Also Ahumada & Lapasset 2007 (BSS catalog) and Simunovic & Puzia 2016
    (globular cluster BSS).

    BSS are defined as stars brighter AND bluer than the main-sequence
    turnoff but within a physically reasonable envelope:
    - Brighter than turnoff by at least dmag_min (default 0.3 mag)
    - But not brighter than turnoff by more than dmag_max (default 3 mag,
      excluding bright RGB scatterers)
    - Bluer than turnoff color
    - But not unreasonably blue (within dcolor_max = 0.5 mag bluer;
      excludes HB, blue pulsators)

    Parameters:
        bp_rp: array of BP-RP colors for cluster members
        abs_mag: array of absolute G magnitudes
        turnoff_bp_rp: BP-RP at the MSTO (from fit_isochrone or blue-edge fit)
        turnoff_M_G: M_G at the MSTO
        dmag_min, dmag_max: brightness range relative to turnoff
        dcolor_max: max blueward offset from turnoff color

    Returns:
        ndarray bool mask of BSS candidates (True where BSS)
    """
    import numpy as _np
    bp_rp = _np.asarray(bp_rp, dtype=float)
    abs_mag = _np.asarray(abs_mag, dtype=float)
    mask = (
        (abs_mag < turnoff_M_G - dmag_min) &
        (abs_mag > turnoff_M_G - dmag_max) &
        (bp_rp < turnoff_bp_rp) &
        (bp_rp > turnoff_bp_rp - dcolor_max)
    )
    return mask


def available_functions():
    """Return signatures and one-line docs for sandbox preloaded helpers."""
    exported = [
        pub_figure,
        pub_style,
        get_isochrone,
        fit_isochrone,
        wd_cooling_age,
        bss_select,
        compare_models,
        analyze_residuals,
        plot_hr_diagram,
        plot_bpt,
        plot_sed,
        plot_lightcurve,
        plot_sky_distribution,
        bpt_classify,
        compute_absolute_magnitude,
        compute_luminosity_distance,
        k_correction,
        spectral_stacking,
        multi_gaussian_fit,
        continuum_normalize,
        batch_equivalent_width,
        cosmological_calculator,
        redshift_at_age,
        extinction_curve,
        deredden,
        estimate_ebv,
        lookup_ebv_irsa,
        monte_carlo_propagate,
        bootstrap_statistic,
        error_weighted_mean,
        lomb_scargle_period,
        phase_fold,
        plot_periodogram,
        plot_phase_folded,
        variability_indices,
        classify_variable,
        voigt_fit,
        velocity_dispersion,
        radial_velocity,
        spectral_template_match,
        target_visibility,
        airmass_plot,
        exposure_time_estimate,
        search_lightcurve,
        download_and_clean_lightcurve,
        transit_search,
        extract_sources,
        pro_load_spectrum,
        pro_identify_lines,
        pro_fit_lines,
        pro_measure_ew,
        pro_heliocentric,
        pro_nested_sampling,
        pro_bayes_factor,
        pro_chain_diagnostics,
        pro_model_comparison,
        pro_gp_detrend,
        pro_fit_transit,
        pro_detect_flares,
        pro_bls_search,
        pro_reproject,
        pro_deblend,
        pro_cutout,
    ]

    # Lazy-import photo-z so its numpy-heavy globals aren't loaded at
    # module level — only when the function catalogue is requested.
    from app.services.photo_z import estimate_photo_z_template, estimate_photo_z_ml, estimate_photo_z
    exported.append(estimate_photo_z_template)
    exported.append(estimate_photo_z_ml)
    exported.append(estimate_photo_z)

    from app.analysis.image_reduction import (
        bias_subtract,
        dark_subtract,
        flat_correct,
        cosmic_ray_reject,
        full_reduction,
        solve_astrometry,
        extract_and_photometer,
    )
    exported.extend([
        bias_subtract,
        dark_subtract,
        flat_correct,
        cosmic_ray_reject,
        full_reduction,
        solve_astrometry,
        extract_and_photometer,
    ])

    info = {}
    for func in exported:
        doc = inspect.getdoc(func) or ""
        info[func.__name__] = {
            "signature": f"{func.__name__}{inspect.signature(func)}",
            "summary": doc.splitlines()[0] if doc else "",
        }
    return info
