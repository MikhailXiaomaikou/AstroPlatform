"""Professional spectral analysis service using specutils.

Provides unit-aware spectral operations: line identification against
NIST catalogs, Gaussian/Voigt fitting, equivalent width measurement,
heliocentric/barycentric correction, flux calibration, and telluric correction.
"""

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Line catalog cache
_line_catalog: list[dict] | None = None
_CATALOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "line_catalogs"


def _load_line_catalog(catalog: str = "nist_optical") -> list[dict]:
    """Load spectral line catalog from JSON file."""
    global _line_catalog
    if _line_catalog is not None:
        return _line_catalog
    path = _CATALOG_DIR / f"{catalog}.json"
    if not path.exists():
        logger.warning("Line catalog %s not found, using empty catalog", catalog)
        return []
    with open(path) as f:
        data = json.load(f)
    _line_catalog = data.get("lines", [])
    return _line_catalog


def load_spectrum(fits_path: str) -> dict:
    """Load spectrum from FITS file, return dict with wavelength, flux, flux_err arrays.

    Attempts specutils Spectrum1D.read() first, falls back to manual WCS parsing.
    """
    try:
        from specutils import Spectrum1D
        import astropy.units as u
        spec = Spectrum1D.read(fits_path)
        result = {
            "wavelength": spec.spectral_axis.to(u.Angstrom).value.tolist(),
            "flux": spec.flux.value.tolist(),
            "flux_unit": str(spec.flux.unit),
            "wave_unit": "Angstrom",
        }
        if spec.uncertainty is not None:
            result["flux_err"] = spec.uncertainty.array.tolist()
        return result
    except Exception:
        pass

    # Fallback: manual FITS parsing
    from astropy.io import fits as pyfits
    with pyfits.open(fits_path) as hdul:
        # Try binary table first
        for hdu in hdul:
            if hasattr(hdu, 'columns') and hdu.columns is not None:
                wave_col = next((c for c in hdu.columns if c.name.upper() in
                    ("WAVE", "WAVELENGTH", "LAMBDA", "LOGLAM")), None)
                flux_col = next((c for c in hdu.columns if c.name.upper() in
                    ("FLUX", "COUNTS", "INTENSITY", "DATA")), None)
                if wave_col and flux_col:
                    wave = np.array(hdu.data[wave_col.name], dtype=float)
                    flux = np.array(hdu.data[flux_col.name], dtype=float)
                    if wave_col.name.upper() == "LOGLAM":
                        wave = 10**wave
                    result = {"wavelength": wave.tolist(), "flux": flux.tolist(),
                              "wave_unit": "Angstrom", "flux_unit": "counts"}
                    err_col = next((c for c in hdu.columns if c.name.upper() in
                        ("IVAR", "ERR", "FLUX_ERR", "SIGMA")), None)
                    if err_col:
                        err = np.array(hdu.data[err_col.name], dtype=float)
                        if err_col.name.upper() == "IVAR":
                            err = np.where(err > 0, 1.0 / np.sqrt(err), 0.0)
                        result["flux_err"] = err.tolist()
                    return result

        # Try 1D image with WCS
        header = hdul[0].header
        data = hdul[0].data
        if data is not None and data.ndim >= 1:
            if data.ndim > 1:
                data = data[0] if data.shape[0] < data.shape[-1] else data
                while data.ndim > 1:
                    data = data[0]
            naxis = len(data)
            crval = header.get("CRVAL1", 1.0)
            cdelt = header.get("CDELT1", header.get("CD1_1", 1.0))
            crpix = header.get("CRPIX1", 1.0)
            wave = crval + (np.arange(naxis) - (crpix - 1)) * cdelt
            return {"wavelength": wave.tolist(), "flux": data.tolist(),
                    "wave_unit": "Angstrom", "flux_unit": "counts"}

    raise ValueError(f"Could not extract spectrum from {fits_path}")


def identify_lines(wavelength: list, flux: list, flux_err: list | None = None,
                   threshold_snr: float = 3.0, catalog: str = "nist_optical",
                   redshift: float = 0.0) -> list[dict]:
    """Identify spectral lines by cross-matching detected peaks against a line catalog.

    Returns list of identified lines with observed/rest wavelengths and IDs.
    """
    wave = np.array(wavelength)
    fl = np.array(flux)

    try:
        from specutils import Spectrum1D
        from specutils.fitting import find_lines_threshold
        import astropy.units as u
        from astropy.nddata import StdDevUncertainty

        unc = StdDevUncertainty(np.array(flux_err)) if flux_err else None
        spec = Spectrum1D(flux=fl * u.Unit(""), spectral_axis=wave * u.Angstrom,
                         uncertainty=unc)

        lines_t = find_lines_threshold(spec, noise_factor=threshold_snr)
        detected = []
        if lines_t is not None and len(lines_t) > 0:
            for row in lines_t:
                detected.append({
                    "observed_wavelength": float(row["line_center"].value),
                    "type": str(row["line_type"]),
                })
    except Exception:
        # Fallback: simple peak detection
        from scipy.signal import find_peaks
        if flux_err:
            noise = np.median(np.abs(np.array(flux_err)))
        else:
            noise = np.median(np.abs(np.diff(fl))) * 1.4826

        prominence = threshold_snr * noise
        peaks_em, _ = find_peaks(fl, prominence=prominence)
        peaks_ab, _ = find_peaks(-fl, prominence=prominence)

        detected = []
        for p in peaks_em:
            detected.append({"observed_wavelength": float(wave[p]), "type": "emission"})
        for p in peaks_ab:
            detected.append({"observed_wavelength": float(wave[p]), "type": "absorption"})

    # Cross-match with catalog
    catalog_lines = _load_line_catalog(catalog)
    identified = []
    for det in detected:
        obs_wave = det["observed_wavelength"]
        rest_wave = obs_wave / (1.0 + redshift)

        best_match = None
        best_dist = float("inf")
        for cl in catalog_lines:
            dist = abs(cl["wavelength"] - rest_wave)
            if dist < best_dist and dist < 5.0:  # 5 Angstrom tolerance
                best_dist = dist
                best_match = cl

        entry = {
            "observed_wavelength": obs_wave,
            "rest_wavelength": rest_wave,
            "line_type": det["type"],
        }
        if best_match:
            entry["identification"] = best_match["name"]
            entry["element"] = best_match["element"]
            entry["catalog_wavelength"] = best_match["wavelength"]
            entry["offset_angstrom"] = round(rest_wave - best_match["wavelength"], 2)
        else:
            entry["identification"] = "unidentified"
        identified.append(entry)

    return identified


def fit_lines(wavelength: list, flux: list, flux_err: list | None = None,
              line_centers: list[float] | None = None,
              model: str = "gaussian") -> list[dict]:
    """Fit spectral lines with Gaussian, Lorentzian, or Voigt profiles.

    If line_centers not provided, auto-detects peaks first.
    """
    wave = np.array(wavelength)
    fl = np.array(flux)

    try:
        from specutils import Spectrum1D, SpectralRegion
        from specutils.fitting import fit_lines as specutils_fit_lines
        import astropy.units as u
        from astropy.modeling.models import Gaussian1D, Lorentz1D, Voigt1D

        spec = Spectrum1D(flux=fl * u.Unit(""), spectral_axis=wave * u.Angstrom)

        if line_centers is None:
            from specutils.fitting import find_lines_threshold
            lt = find_lines_threshold(spec, noise_factor=3.0)
            if lt is not None and len(lt) > 0:
                line_centers = [float(row["line_center"].value) for row in lt]
            else:
                return []

        results = []
        for center in line_centers:
            # Define region around line
            region = SpectralRegion((center - 20) * u.Angstrom, (center + 20) * u.Angstrom)

            if model == "gaussian":
                init_model = Gaussian1D(amplitude=float(np.max(fl)) * u.Unit(""),
                                       mean=center * u.Angstrom, stddev=2.0 * u.Angstrom)
            elif model == "lorentzian":
                init_model = Lorentz1D(amplitude=float(np.max(fl)), x_0=center, fwhm=4.0)
            elif model == "voigt":
                init_model = Voigt1D(x_0=center, amplitude_L=float(np.max(fl)),
                                    fwhm_L=2.0, fwhm_G=2.0)
            else:
                init_model = Gaussian1D(amplitude=float(np.max(fl)) * u.Unit(""),
                                       mean=center * u.Angstrom, stddev=2.0 * u.Angstrom)

            try:
                fitted = specutils_fit_lines(spec, init_model, window=region)
                if model == "gaussian":
                    results.append({
                        "center": float(fitted.mean.value),
                        "amplitude": float(fitted.amplitude.value),
                        "stddev": float(fitted.stddev.value),
                        "fwhm": float(fitted.stddev.value * 2.3548),
                        "model": model,
                    })
                else:
                    results.append({
                        "center": center,
                        "model": model,
                        "fitted": True,
                    })
            except Exception as e:
                results.append({"center": center, "model": model, "error": str(e)})

        return results

    except ImportError:
        # Fallback to scipy
        from scipy.optimize import curve_fit

        if line_centers is None:
            from scipy.signal import find_peaks
            peaks, _ = find_peaks(fl, prominence=np.std(fl))
            line_centers = wave[peaks].tolist()

        results = []
        for center in line_centers:
            mask = (wave > center - 20) & (wave < center + 20)
            if np.sum(mask) < 5:
                continue
            xdata, ydata = wave[mask], fl[mask]
            try:
                def gauss(x, a, mu, sig):
                    return a * np.exp(-(x - mu)**2 / (2 * sig**2))
                popt, pcov = curve_fit(gauss, xdata, ydata, p0=[np.max(ydata), center, 2.0])
                perr = np.sqrt(np.diag(pcov))
                results.append({
                    "center": float(popt[1]), "center_err": float(perr[1]),
                    "amplitude": float(popt[0]), "amplitude_err": float(perr[0]),
                    "stddev": float(abs(popt[2])), "stddev_err": float(perr[2]),
                    "fwhm": float(abs(popt[2]) * 2.3548),
                    "model": "gaussian",
                })
            except Exception as e:
                results.append({"center": center, "error": str(e)})

        return results


def measure_equivalent_width(wavelength: list, flux: list,
                             line_center: float, window: float = 20.0) -> dict:
    """Measure equivalent width of a spectral line."""
    wave = np.array(wavelength)
    fl = np.array(flux)

    try:
        from specutils import Spectrum1D, SpectralRegion
        from specutils.analysis import equivalent_width as spec_ew
        import astropy.units as u

        spec = Spectrum1D(flux=fl * u.Unit(""), spectral_axis=wave * u.Angstrom)
        region = SpectralRegion((line_center - window) * u.Angstrom,
                               (line_center + window) * u.Angstrom)
        ew = spec_ew(spec, regions=region)
        return {
            "equivalent_width": float(ew.value),
            "unit": str(ew.unit),
            "line_center": line_center,
            "window": window,
        }
    except Exception:
        # Fallback: trapezoidal integration
        mask = (wave > line_center - window) & (wave < line_center + window)
        w, f = wave[mask], fl[mask]
        if len(w) < 3:
            return {"error": "insufficient data points"}
        continuum = np.median(np.concatenate([f[:5], f[-5:]]))
        if continuum == 0:
            return {"error": "zero continuum"}
        ew = np.trapz(1.0 - f / continuum, w)
        return {
            "equivalent_width": float(ew),
            "unit": "Angstrom",
            "line_center": line_center,
            "window": window,
            "continuum_level": float(continuum),
        }


def heliocentric_correction(wavelength: list, flux: list,
                            ra: float, dec: float, obstime: str,
                            observatory: str = "greenwich") -> dict:
    """Apply heliocentric/barycentric velocity correction to a spectrum."""
    from astropy.coordinates import SkyCoord, EarthLocation
    from astropy.time import Time
    import astropy.units as u

    wave = np.array(wavelength)

    coord = SkyCoord(ra=ra, dec=dec, unit="deg")
    time = Time(obstime)

    try:
        location = EarthLocation.of_site(observatory)
    except Exception:
        location = EarthLocation.of_site("greenwich")

    v_corr = coord.radial_velocity_correction(obstime=time, location=location)
    beta = (v_corr / (299792.458 * u.km / u.s)).decompose().value
    wave_corrected = wave * (1.0 + beta)

    return {
        "wavelength": wave_corrected.tolist(),
        "flux": flux,
        "v_correction_km_s": float(v_corr.to(u.km / u.s).value),
        "applied": True,
    }


def flux_calibrate(wavelength: list, flux: list,
                   sensitivity_wavelength: list | None = None,
                   sensitivity_response: list | None = None) -> dict:
    """Apply flux calibration using a sensitivity function."""
    wave = np.array(wavelength)
    fl = np.array(flux)

    if sensitivity_wavelength is None or sensitivity_response is None:
        # Return uncalibrated with a note
        return {
            "wavelength": wavelength,
            "flux": flux,
            "calibrated": False,
            "note": "No sensitivity function provided. Provide standard star observation for calibration.",
        }

    sens_w = np.array(sensitivity_wavelength)
    sens_r = np.array(sensitivity_response)

    # Interpolate sensitivity to spectrum wavelength grid
    from scipy.interpolate import interp1d
    sens_interp = interp1d(sens_w, sens_r, bounds_error=False, fill_value=1.0)
    sensitivity = sens_interp(wave)

    flux_cal = fl / np.where(sensitivity > 0, sensitivity, 1.0)

    return {
        "wavelength": wavelength,
        "flux": flux_cal.tolist(),
        "flux_unit": "erg/s/cm2/Angstrom",
        "calibrated": True,
    }


def telluric_correct(wavelength: list, flux: list,
                     telluric_wavelength: list | None = None,
                     telluric_transmission: list | None = None) -> dict:
    """Correct for telluric absorption features."""
    wave = np.array(wavelength)
    fl = np.array(flux)

    if telluric_wavelength and telluric_transmission:
        tel_w = np.array(telluric_wavelength)
        tel_t = np.array(telluric_transmission)
        from scipy.interpolate import interp1d
        tel_interp = interp1d(tel_w, tel_t, bounds_error=False, fill_value=1.0)
        transmission = tel_interp(wave)
    else:
        # Apply simplified telluric model for major bands
        transmission = np.ones_like(wave)
        # O2 A-band (7594-7630 Angstrom)
        mask_o2a = (wave > 7580) & (wave < 7650)
        transmission[mask_o2a] *= 0.7
        # O2 B-band (6867-6884 Angstrom)
        mask_o2b = (wave > 6860) & (wave < 6890)
        transmission[mask_o2b] *= 0.85
        # Water bands
        for center, width, depth in [(7186, 30, 0.8), (8227, 50, 0.75), (9400, 100, 0.6)]:
            mask = (wave > center - width) & (wave < center + width)
            x = (wave[mask] - center) / width
            transmission[mask] *= depth + (1 - depth) * x**2

    flux_corrected = fl / np.where(transmission > 0.1, transmission, 1.0)

    return {
        "wavelength": wavelength,
        "flux": flux_corrected.tolist(),
        "corrected": True,
        "model": "custom" if telluric_wavelength else "simplified",
    }


def velocity_dispersion_xcorr(wavelength: list, flux: list,
                               template_wavelength: list, template_flux: list,
                               z: float = 0.0) -> dict:
    """Measure velocity dispersion via cross-correlation.

    Uses log-wavelength rebinning for uniform velocity bins,
    continuum normalization, and Gaussian fit to CCF peak.
    """
    wave = np.array(wavelength) / (1.0 + z)
    fl = np.array(flux)
    tw = np.array(template_wavelength)
    tf = np.array(template_flux)

    # Common wavelength range
    w_min = max(wave.min(), tw.min())
    w_max = min(wave.max(), tw.max())
    if w_max <= w_min:
        return {"error": "No overlapping wavelength range"}

    mask_s = (wave >= w_min) & (wave <= w_max)
    mask_t = (tw >= w_min) & (tw <= w_max)

    # Rebin to log-wavelength (uniform velocity bins)
    c_km_s = 299792.458
    ln_w_min = np.log(w_min)
    ln_w_max = np.log(w_max)
    n_pix = min(len(wave[mask_s]), len(tw[mask_t]), 2048)
    dln = (ln_w_max - ln_w_min) / n_pix
    vel_scale = c_km_s * dln  # km/s per pixel

    ln_grid = np.linspace(ln_w_min, ln_w_max, n_pix)
    w_grid = np.exp(ln_grid)

    from scipy.interpolate import interp1d
    spec_interp = interp1d(wave[mask_s], fl[mask_s], bounds_error=False, fill_value=0)
    temp_interp = interp1d(tw[mask_t], tf[mask_t], bounds_error=False, fill_value=0)

    spec_rebinned = spec_interp(w_grid)
    temp_rebinned = temp_interp(w_grid)

    # Continuum normalize
    from scipy.ndimage import median_filter
    for arr in [spec_rebinned, temp_rebinned]:
        cont = median_filter(arr, size=min(len(arr) // 5, 101))
        cont[cont <= 0] = 1.0
        arr /= cont

    # Subtract mean
    spec_rebinned -= np.mean(spec_rebinned)
    temp_rebinned -= np.mean(temp_rebinned)

    # Cross-correlate
    from scipy.signal import correlate
    ccf = correlate(spec_rebinned, temp_rebinned, mode='full')
    ccf /= np.max(np.abs(ccf)) if np.max(np.abs(ccf)) > 0 else 1

    n = len(spec_rebinned)
    lags = np.arange(-n + 1, n) * vel_scale  # velocity in km/s

    # Fit Gaussian to CCF peak
    peak_idx = np.argmax(ccf)
    half_width = min(50, n // 4)
    lo = max(0, peak_idx - half_width)
    hi = min(len(ccf), peak_idx + half_width)

    from scipy.optimize import curve_fit
    def gauss(x, a, mu, sig):
        return a * np.exp(-0.5 * ((x - mu) / sig) ** 2)

    try:
        popt, pcov = curve_fit(gauss, lags[lo:hi], ccf[lo:hi],
                               p0=[ccf[peak_idx], lags[peak_idx], 100.0])
        perr = np.sqrt(np.diag(pcov))

        sigma_v = abs(popt[2])
        sigma_v_err = perr[2]
        v_shift = popt[1]

        return {
            "sigma_v_km_s": float(sigma_v),
            "sigma_v_err_km_s": float(sigma_v_err),
            "velocity_shift_km_s": float(v_shift),
            "ccf_peak": float(popt[0]),
            "vel_scale_km_s_per_pixel": float(vel_scale),
            "method": "cross_correlation",
        }
    except Exception as e:
        return {"error": f"CCF fit failed: {e}", "ccf_peak_velocity": float(lags[peak_idx])}


def load_ifu_cube(fits_path: str) -> dict:
    """Load an IFU datacube from FITS (3D: wavelength x spatial x spatial)."""
    from astropy.io import fits as pyfits
    from astropy.wcs import WCS

    with pyfits.open(fits_path) as hdul:
        # Find the 3D data HDU
        data = None
        header = None
        for hdu in hdul:
            if hdu.data is not None and hdu.data.ndim == 3:
                data = hdu.data
                header = hdu.header
                break

        if data is None:
            return {"error": "No 3D datacube found in FITS file"}

        wcs = WCS(header)
        nwave, ny, nx = data.shape

        # Extract wavelength axis
        crval3 = header.get("CRVAL3", 1.0)
        cdelt3 = header.get("CDELT3", header.get("CD3_3", 1.0))
        crpix3 = header.get("CRPIX3", 1.0)
        wavelength = crval3 + (np.arange(nwave) - (crpix3 - 1)) * cdelt3

        # Spatial info
        pixscale = abs(header.get("CDELT1", header.get("CD1_1", 1.0))) * 3600  # arcsec

        return {
            "shape": [nwave, ny, nx],
            "wavelength_range": [float(wavelength[0]), float(wavelength[-1])],
            "n_wavelength": nwave,
            "spatial_size": [ny, nx],
            "pixel_scale_arcsec": float(pixscale),
            "wavelength_unit": header.get("CUNIT3", "Angstrom"),
            "has_wcs": wcs.has_celestial,
        }


def extract_spaxel_spectrum(fits_path: str, x: int, y: int) -> dict:
    """Extract 1D spectrum from a single spaxel in an IFU cube."""
    from astropy.io import fits as pyfits

    with pyfits.open(fits_path) as hdul:
        for hdu in hdul:
            if hdu.data is not None and hdu.data.ndim == 3:
                data = hdu.data
                header = hdu.header
                break
        else:
            return {"error": "No 3D datacube found"}

        nwave, ny, nx = data.shape
        if x < 0 or x >= nx or y < 0 or y >= ny:
            return {"error": f"Spaxel ({x},{y}) out of range ({nx},{ny})"}

        flux = data[:, y, x]
        crval3 = header.get("CRVAL3", 1.0)
        cdelt3 = header.get("CDELT3", header.get("CD3_3", 1.0))
        crpix3 = header.get("CRPIX3", 1.0)
        wavelength = crval3 + (np.arange(nwave) - (crpix3 - 1)) * cdelt3

        return {
            "wavelength": wavelength.tolist(),
            "flux": flux.tolist(),
            "spaxel_x": x,
            "spaxel_y": y,
        }


def extract_aperture_spectrum(fits_path: str, center_x: int, center_y: int,
                               radius_pix: int = 3) -> dict:
    """Extract coadded spectrum from a circular aperture in an IFU cube."""
    from astropy.io import fits as pyfits

    with pyfits.open(fits_path) as hdul:
        for hdu in hdul:
            if hdu.data is not None and hdu.data.ndim == 3:
                data = hdu.data
                header = hdu.header
                break
        else:
            return {"error": "No 3D datacube found"}

        nwave, ny, nx = data.shape
        yy, xx = np.ogrid[:ny, :nx]
        mask = ((xx - center_x)**2 + (yy - center_y)**2) <= radius_pix**2
        n_spaxels = int(np.sum(mask))

        if n_spaxels == 0:
            return {"error": "Aperture contains no spaxels"}

        flux = np.nanmean(data[:, mask], axis=1)
        flux_err = np.nanstd(data[:, mask], axis=1) / np.sqrt(n_spaxels)

        crval3 = header.get("CRVAL3", 1.0)
        cdelt3 = header.get("CDELT3", header.get("CD3_3", 1.0))
        crpix3 = header.get("CRPIX3", 1.0)
        wavelength = crval3 + (np.arange(nwave) - (crpix3 - 1)) * cdelt3

        return {
            "wavelength": wavelength.tolist(),
            "flux": flux.tolist(),
            "flux_err": flux_err.tolist(),
            "n_spaxels": n_spaxels,
            "center": [center_x, center_y],
            "radius_pix": radius_pix,
        }


# Standard star reference fluxes (simplified tabulations)
_STANDARD_STARS = {
    "feige34": {
        "wavelength": [3200, 3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000, 7500, 8000, 9000, 10000],
        "flux_erg": [4.84e-13, 5.53e-13, 5.81e-13, 5.55e-13, 5.03e-13, 4.53e-13, 4.01e-13, 3.55e-13, 3.13e-13, 2.75e-13, 2.42e-13, 1.87e-13, 1.47e-13],
    },
    "hz44": {
        "wavelength": [3200, 3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000, 7500, 8000, 9000, 10000],
        "flux_erg": [2.90e-13, 3.39e-13, 3.65e-13, 3.51e-13, 3.20e-13, 2.89e-13, 2.58e-13, 2.29e-13, 2.03e-13, 1.79e-13, 1.58e-13, 1.23e-13, 9.74e-14],
    },
    "gd71": {
        "wavelength": [3200, 3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000, 7500, 8000, 9000, 10000],
        "flux_erg": [1.82e-13, 2.06e-13, 2.15e-13, 2.03e-13, 1.83e-13, 1.63e-13, 1.44e-13, 1.27e-13, 1.12e-13, 9.84e-14, 8.65e-14, 6.65e-14, 5.21e-14],
    },
}

def auto_flux_calibrate(wavelength: list, flux: list,
                        standard_star_name: str = "feige34") -> dict:
    """Auto flux calibration using standard star reference spectra."""
    star_key = standard_star_name.lower().replace(" ", "").replace("-", "")
    if star_key not in _STANDARD_STARS:
        available = ", ".join(_STANDARD_STARS.keys())
        return {"error": f"Unknown standard star. Available: {available}"}

    ref = _STANDARD_STARS[star_key]
    wave = np.array(wavelength)
    fl = np.array(flux)

    from scipy.interpolate import interp1d
    ref_interp = interp1d(ref["wavelength"], ref["flux_erg"],
                          bounds_error=False, fill_value="extrapolate")
    ref_flux = ref_interp(wave)

    # Sensitivity = observed_counts / reference_flux
    sensitivity = np.where(ref_flux > 0, fl / ref_flux, 1.0)

    # Smooth sensitivity with Savitzky-Golay
    from scipy.signal import savgol_filter
    window = min(len(sensitivity) // 4 * 2 + 1, 51)
    if window >= 5:
        sensitivity_smooth = savgol_filter(sensitivity, window, 3)
    else:
        sensitivity_smooth = sensitivity

    sensitivity_smooth = np.where(sensitivity_smooth > 0, sensitivity_smooth, 1.0)

    return {
        "wavelength": wavelength,
        "sensitivity": sensitivity_smooth.tolist(),
        "standard_star": standard_star_name,
        "calibrated": True,
        "note": "Apply by dividing raw spectrum by this sensitivity function",
    }


def voronoi_bin_cube(fits_path: str, target_snr: float = 10.0) -> dict:
    """Voronoi binning of an IFU datacube to achieve target S/N per bin.

    Implementation of the Cappellari & Copin (2003) algorithm.
    """
    from astropy.io import fits as pyfits

    with pyfits.open(fits_path) as hdul:
        for hdu in hdul:
            if hdu.data is not None and hdu.data.ndim == 3:
                data = hdu.data
                header = hdu.header
                break
        else:
            return {"error": "No 3D datacube found"}

    nwave, ny, nx = data.shape

    # Compute signal and noise per spaxel (median flux and MAD)
    signal = np.nanmedian(data, axis=0)  # (ny, nx)
    noise = 1.4826 * np.nanmedian(np.abs(data - np.nanmedian(data, axis=0, keepdims=True)), axis=0)
    noise[noise <= 0] = np.nanmedian(noise[noise > 0]) if np.any(noise > 0) else 1.0

    snr = signal / noise

    # Simple Voronoi-like binning: iterative accretion
    bin_map = np.full((ny, nx), -1, dtype=int)
    bin_id = 0

    # Sort spaxels by S/N (lowest first)
    valid = np.isfinite(signal) & (signal > 0)
    coords = np.argwhere(valid)
    snr_vals = snr[valid]
    order = np.argsort(snr_vals)
    coords = coords[order]

    for cy, cx in coords:
        if bin_map[cy, cx] >= 0:
            continue

        # Start new bin
        current_bin = [(cy, cx)]
        bin_map[cy, cx] = bin_id
        current_signal = float(signal[cy, cx])
        current_noise_sq = float(noise[cy, cx] ** 2)

        # Accrete neighbors until target S/N reached
        while current_signal / np.sqrt(current_noise_sq) < target_snr:
            # Find unassigned neighbors
            neighbors = []
            for by, bx in current_bin:
                for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    ny2, nx2 = by + dy, bx + dx
                    if 0 <= ny2 < ny and 0 <= nx2 < nx and bin_map[ny2, nx2] < 0 and valid[ny2, nx2]:
                        neighbors.append((ny2, nx2))

            if not neighbors:
                break

            # Add the neighbor with highest S/N
            best = max(neighbors, key=lambda p: snr[p[0], p[1]])
            bin_map[best[0], best[1]] = bin_id
            current_bin.append(best)
            current_signal += signal[best[0], best[1]]
            current_noise_sq += noise[best[0], best[1]] ** 2

        bin_id += 1

    # Compute binned spectra
    n_bins = bin_id
    binned_spectra = {}
    for b in range(n_bins):
        mask = bin_map == b
        n_spax = int(np.sum(mask))
        if n_spax == 0:
            continue
        binned_flux = np.nanmean(data[:, mask], axis=1)
        binned_err = np.nanstd(data[:, mask], axis=1) / np.sqrt(n_spax)
        achieved_snr = float(np.nanmedian(binned_flux / (binned_err + 1e-30)))
        binned_spectra[b] = {
            "n_spaxels": n_spax,
            "achieved_snr": round(achieved_snr, 1),
        }

    # Extract wavelength
    crval3 = header.get("CRVAL3", 1.0)
    cdelt3 = header.get("CDELT3", header.get("CD3_3", 1.0))
    crpix3 = header.get("CRPIX3", 1.0)

    return {
        "n_bins": n_bins,
        "target_snr": target_snr,
        "bin_map_shape": [ny, nx],
        "bin_summary": binned_spectra,
        "wavelength_range": [float(crval3), float(crval3 + (nwave - 1) * cdelt3)],
        # FITS WCS reference pixel — exposed so downstream code can
        # reconstruct the exact wavelength axis: lambda = CRVAL3 +
        # (pixel - CRPIX3) * CDELT3.  Previously dropped on the floor.
        "wcs_crpix3": float(crpix3),
        "wcs_cdelt3": float(cdelt3),
    }


def ifu_velocity_map(fits_path: str, rest_wavelength: float,
                     window: float = 30.0) -> dict:
    """Compute 2D velocity and velocity dispersion maps from an IFU cube.

    Fits a Gaussian to a spectral line at each spaxel to measure
    velocity shift and line width.
    """
    from astropy.io import fits as pyfits
    from scipy.optimize import curve_fit

    c_km_s = 299792.458

    with pyfits.open(fits_path) as hdul:
        for hdu in hdul:
            if hdu.data is not None and hdu.data.ndim == 3:
                data = hdu.data
                header = hdu.header
                break
        else:
            return {"error": "No 3D datacube found"}

    nwave, ny, nx = data.shape
    crval3 = header.get("CRVAL3", 1.0)
    cdelt3 = header.get("CDELT3", header.get("CD3_3", 1.0))
    crpix3 = header.get("CRPIX3", 1.0)
    wavelength = crval3 + (np.arange(nwave) - (crpix3 - 1)) * cdelt3

    # Line window
    mask = (wavelength > rest_wavelength - window) & (wavelength < rest_wavelength + window)
    w_line = wavelength[mask]

    if len(w_line) < 5:
        return {"error": f"Insufficient wavelength coverage around {rest_wavelength} A"}

    velocity_map = np.full((ny, nx), np.nan)
    dispersion_map = np.full((ny, nx), np.nan)
    flux_map = np.full((ny, nx), np.nan)

    def gauss(x, a, mu, sig, c):
        return a * np.exp(-0.5 * ((x - mu) / sig) ** 2) + c

    for iy in range(ny):
        for ix in range(nx):
            spec = data[mask, iy, ix]
            if np.any(np.isnan(spec)) or np.all(spec == 0):
                continue
            try:
                p0 = [np.max(spec) - np.median(spec), rest_wavelength, 2.0, np.median(spec)]
                popt, _ = curve_fit(gauss, w_line, spec, p0=p0, maxfev=500)

                fitted_center = popt[1]
                fitted_sigma = abs(popt[2])

                # Velocity = c * (lambda_obs - lambda_rest) / lambda_rest
                velocity_map[iy, ix] = c_km_s * (fitted_center - rest_wavelength) / rest_wavelength
                dispersion_map[iy, ix] = c_km_s * fitted_sigma / rest_wavelength
                flux_map[iy, ix] = popt[0] * fitted_sigma * np.sqrt(2 * np.pi)
            except Exception:
                continue

    # Statistics
    valid = np.isfinite(velocity_map)

    return {
        "velocity_map": velocity_map.tolist(),
        "dispersion_map": dispersion_map.tolist(),
        "flux_map": flux_map.tolist(),
        "shape": [ny, nx],
        "rest_wavelength": rest_wavelength,
        "n_valid_spaxels": int(np.sum(valid)),
        "velocity_range_km_s": [float(np.nanmin(velocity_map)), float(np.nanmax(velocity_map))] if np.any(valid) else [0, 0],
        "mean_dispersion_km_s": float(np.nanmean(dispersion_map)) if np.any(valid) else 0,
    }


def ifu_line_ratio_map(fits_path: str,
                       line1_wavelength: float, line2_wavelength: float,
                       line1_name: str = "line1", line2_name: str = "line2",
                       window: float = 20.0) -> dict:
    """Compute a 2D emission line ratio map from an IFU cube.

    Useful for BPT diagrams: [O III]/Hbeta, [N II]/Halpha, [S II]/Halpha.
    """
    from astropy.io import fits as pyfits

    with pyfits.open(fits_path) as hdul:
        for hdu in hdul:
            if hdu.data is not None and hdu.data.ndim == 3:
                data = hdu.data
                header = hdu.header
                break
        else:
            return {"error": "No 3D datacube found"}

    nwave, ny, nx = data.shape
    crval3 = header.get("CRVAL3", 1.0)
    cdelt3 = header.get("CDELT3", header.get("CD3_3", 1.0))
    crpix3 = header.get("CRPIX3", 1.0)
    wavelength = crval3 + (np.arange(nwave) - (crpix3 - 1)) * cdelt3

    def measure_line_flux(w_center):
        mask = (wavelength > w_center - window) & (wavelength < w_center + window)
        if np.sum(mask) < 3:
            return np.full((ny, nx), np.nan)
        line_data = data[mask]
        # Continuum from edges
        cont_mask = ((wavelength > w_center - window * 2) & (wavelength < w_center - window)) | \
                    ((wavelength > w_center + window) & (wavelength < w_center + window * 2))
        if np.sum(cont_mask) > 0:
            continuum = np.nanmedian(data[cont_mask], axis=0)
        else:
            continuum = np.nanmedian(line_data[[0, -1]], axis=0)

        line_flux = np.nansum(line_data - continuum[np.newaxis, :, :], axis=0) * abs(cdelt3)
        return line_flux

    flux1 = measure_line_flux(line1_wavelength)
    flux2 = measure_line_flux(line2_wavelength)

    # Ratio map (log10)
    ratio = np.full((ny, nx), np.nan)
    valid = (flux2 > 0) & (flux1 > 0) & np.isfinite(flux1) & np.isfinite(flux2)
    ratio[valid] = np.log10(flux1[valid] / flux2[valid])

    return {
        "ratio_map_log10": ratio.tolist(),
        "flux1_map": flux1.tolist(),
        "flux2_map": flux2.tolist(),
        "shape": [ny, nx],
        "line1": {"name": line1_name, "wavelength": line1_wavelength},
        "line2": {"name": line2_name, "wavelength": line2_wavelength},
        "n_valid_spaxels": int(np.sum(valid)),
        "ratio_range": [float(np.nanmin(ratio)), float(np.nanmax(ratio))] if np.any(valid) else [0, 0],
    }
