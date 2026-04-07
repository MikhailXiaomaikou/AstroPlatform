"""Pre-built astronomical analysis modules and publication-quality plot templates.

These functions are available inside the AI's run_python sandbox.
They produce journal-standard (ApJ/MNRAS) figures and compute common
astronomical diagnostics without requiring the user to write boilerplate.
"""

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
    """Compute luminosity distance in Mpc for given redshifts.

    Uses flat Lambda-CDM cosmology.
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


def compute_absolute_magnitude(apparent_mag, redshift=None, distance_mpc=None, parallax_mas=None):
    """Compute absolute magnitude from apparent magnitude + distance indicator.

    Provide ONE of: redshift, distance_mpc, or parallax_mas.
    """
    m = np.asarray(apparent_mag, dtype=float)

    if parallax_mas is not None:
        plx = np.asarray(parallax_mas, dtype=float)
        valid = plx > 0
        M = np.full_like(m, np.nan)
        M[valid] = m[valid] + 5 * np.log10(plx[valid]) - 10
        return M
    elif distance_mpc is not None:
        d = np.asarray(distance_mpc, dtype=float)
        return m - 5 * np.log10(d * 1e6) + 5
    elif redshift is not None:
        d = compute_luminosity_distance(redshift)
        return m - 5 * np.log10(np.asarray(d) * 1e6) + 5
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
        peaks, props = find_peaks(f, height=np.median(f), distance=len(f) // (n_components + 1))
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
        coeffs = np.polyfit(wave[mask], f[mask], deg=order)
        continuum = np.polyval(coeffs, wave)
        residuals = f - continuum
        std = np.std(residuals[mask])
        mask = np.abs(residuals) < sigma_clip * std

    continuum = np.polyval(np.polyfit(wave[mask], f[mask], deg=order), wave)
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
