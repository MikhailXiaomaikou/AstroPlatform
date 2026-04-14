"""Professional spectral analysis service using specutils.

Provides unit-aware spectral operations: line identification against
NIST catalogs, Gaussian/Voigt fitting, equivalent width measurement,
heliocentric/barycentric correction, flux calibration, and telluric correction.
"""

import json
import logging
from pathlib import Path
from typing import Any

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
