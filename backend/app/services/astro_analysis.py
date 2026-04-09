"""Pre-built astronomical analysis modules and publication-quality plot templates.

These functions are available inside the AI's run_python sandbox.
They produce journal-standard (ApJ/MNRAS) figures and compute common
astronomical diagnostics without requiring the user to write boilerplate.
"""

import inspect

import numpy as np

# ── Publication Figure Defaults ──

PUB_STYLE = {
    "font.family": "serif",
    "font.serif": ["Computer Modern Roman", "DejaVu Serif", "Times New Roman"],
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


# ── Common Astronomy Plots ──

def plot_hr_diagram(bp_rp, gmag, parallax=None, labels=None,
                    title="HR Diagram", color_by=None):
    """Publication-quality HR diagram (color-magnitude or color-absolute magnitude).

    Args:
        bp_rp: BP-RP color array
        gmag: G magnitude array
        parallax: parallax in mas (if given, converts to absolute magnitude)
        labels: object labels for legend
        color_by: array to color points by (e.g., metallicity)
    """
    import matplotlib.pyplot as plt
    fig, ax = pub_figure()

    y = np.array(gmag)
    ylabel = "G [mag]"

    if parallax is not None:
        plx = np.array(parallax)
        valid = plx > 0
        y = y.copy()
        y[valid] = y[valid] + 5 * np.log10(plx[valid]) - 10  # absolute mag
        y[~valid] = np.nan
        ylabel = r"$M_G$ [mag]"

    if color_by is not None:
        sc = ax.scatter(bp_rp, y, c=color_by, s=3, alpha=0.6, cmap="viridis", rasterized=True)
        plt.colorbar(sc, ax=ax, label="Color parameter")
    else:
        ax.scatter(bp_rp, y, s=3, alpha=0.5, color="#0A84FF", rasterized=True)

    ax.set_xlabel(r"$G_{BP} - G_{RP}$ [mag]")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.invert_yaxis()
    plt.tight_layout()
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

def extinction_curve(wavelength_angstrom, ebv, Rv=3.1):
    """Compute extinction A_lambda using CCM89 (Cardelli, Clayton & Mathis 1989).

    Implements the CCM89 parameterization for UV/optical/NIR (1250 Å – 33333 Å).

    Args:
        wavelength_angstrom: Wavelength(s) in Angstroms (scalar or array).
        ebv: E(B-V) color excess.
        Rv: Ratio of total-to-selective extinction (default 3.1).

    Returns:
        A_lambda array (extinction in magnitudes at each wavelength).
    """
    wave = np.atleast_1d(np.asarray(wavelength_angstrom, dtype=float))
    # Convert to inverse microns
    x = 1.0e4 / wave  # x in μm⁻¹

    a = np.zeros_like(x)
    b = np.zeros_like(x)

    # Infrared: 0.3 ≤ x < 1.1 μm⁻¹
    ir = (x >= 0.3) & (x < 1.1)
    if np.any(ir):
        xi = x[ir]
        a[ir] = 0.574 * xi**1.61
        b[ir] = -0.527 * xi**1.61

    # Optical/NIR: 1.1 ≤ x < 3.3 μm⁻¹
    opt = (x >= 1.1) & (x < 3.3)
    if np.any(opt):
        y = x[opt] - 1.82
        a[opt] = (1.0 + 0.17699 * y - 0.50447 * y**2 - 0.02427 * y**3
                  + 0.72085 * y**4 + 0.01979 * y**5 - 0.77530 * y**6
                  + 0.32999 * y**7)
        b[opt] = (1.41338 * y + 2.28305 * y**2 + 1.07233 * y**3
                  - 5.38434 * y**4 - 0.62251 * y**5 + 5.30260 * y**6
                  - 2.09002 * y**7)

    # Ultraviolet: 3.3 ≤ x ≤ 8.0 μm⁻¹
    uv = (x >= 3.3) & (x <= 8.0)
    if np.any(uv):
        xi = x[uv]
        # UV bump
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


def deredden(wavelength_angstrom, flux, ebv, Rv=3.1):
    """Correct flux for dust extinction using CCM89.

    Args:
        wavelength_angstrom: Wavelength(s) in Angstroms.
        flux: Observed flux (scalar or array, same shape as wavelength).
        ebv: E(B-V) color excess.
        Rv: Ratio of total-to-selective extinction (default 3.1).

    Returns:
        Dereddened flux array: flux_corrected = flux * 10^(0.4 * A_lambda).
    """
    flux = np.asarray(flux, dtype=float)
    A_lam = extinction_curve(wavelength_angstrom, ebv, Rv=Rv)
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
                        n_frequencies=10000):
    """Detect periodic signal using Lomb-Scargle periodogram.

    Args:
        time: observation times array
        mag: magnitude array
        mag_err: magnitude uncertainties (optional)
        min_period: minimum period to search (days)
        max_period: maximum period to search (days)
        n_frequencies: number of frequency grid points

    Returns:
        dict with best_period, best_power, fap, frequencies, powers, top_periods
    """
    from astropy.timeseries import LombScargle

    t = np.asarray(time, dtype=float)
    m = np.asarray(mag, dtype=float)

    if len(t) == 0 or len(m) == 0:
        raise ValueError("lomb_scargle_period: empty input arrays")
    if len(t) != len(m):
        raise ValueError("lomb_scargle_period: time and mag must have same length")

    err = np.asarray(mag_err, dtype=float) if mag_err is not None else None

    min_freq = 1.0 / max_period
    max_freq = 1.0 / min_period
    frequency = np.linspace(min_freq, max_freq, n_frequencies)

    ls = LombScargle(t, m, dy=err)
    power = ls.power(frequency)

    # Top 5 periods by power
    sorted_idx = np.argsort(power)[::-1]
    top_indices = sorted_idx[:5]
    top_periods = (1.0 / frequency[top_indices]).tolist()

    best_idx = sorted_idx[0]
    best_freq = frequency[best_idx]
    best_period = float(1.0 / best_freq)
    best_power = float(power[best_idx])

    fap = float(ls.false_alarm_probability(best_power))

    return {
        "best_period": best_period,
        "best_power": best_power,
        "fap": fap,
        "frequencies": frequency.tolist(),
        "powers": power.tolist(),
        "top_periods": top_periods,
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
    for fap_level, ls_label, color in [(0.01, "1% FAP", "#FF9F0A"),
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


def available_functions():
    """Return signatures and one-line docs for sandbox preloaded helpers."""
    exported = [
        pub_figure,
        pub_style,
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
    ]
    info = {}
    for func in exported:
        doc = inspect.getdoc(func) or ""
        info[func.__name__] = {
            "signature": f"{func.__name__}{inspect.signature(func)}",
            "summary": doc.splitlines()[0] if doc else "",
        }
    return info
