"""PSF photometry node using photutils.

Detects sources, builds an empirical PSF, fits it to all detections,
and also performs aperture photometry for comparison.
Falls back to aperture-only (via sep) when photutils is unavailable.
"""

from __future__ import annotations

import io
import warnings
from typing import Any

import numpy as np
from astropy.io import fits

from app.storage import download_fits


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_image(fits_path: str) -> tuple[np.ndarray, dict]:
    """Return (2D float64 image, header dict) from a FITS path."""
    raw = download_fits(fits_path)
    hdul = fits.open(io.BytesIO(raw))

    image = None
    header_dict: dict[str, str] = {}
    for hdu in hdul:
        header_dict.update({k: str(v) for k, v in hdu.header.items() if k})
        if hdu.data is not None and hdu.data.ndim >= 2:
            image = hdu.data.astype(np.float64)
            # Squeeze extra dimensions (e.g. (1,1,H,W) cubes)
            while image.ndim > 2:
                image = image[0]
            break

    hdul.close()
    if image is None:
        raise ValueError("PSFPhotometry: no 2-D image data found in FITS file")
    return image, header_dict


def _parse_aperture_radii(raw: Any) -> list[float]:
    """Accept '3,5,7' string or list of numbers and return list[float]."""
    if isinstance(raw, str):
        return [float(r.strip()) for r in raw.split(",") if r.strip()]
    if isinstance(raw, (list, tuple)):
        return [float(r) for r in raw]
    return [3.0, 5.0, 7.0]


def _safe(val: Any) -> Any:
    """Convert numpy scalars / NaN / Inf to JSON-safe Python types."""
    if val is None:
        return None
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating, float)):
        v = float(val)
        if v != v or v == float("inf") or v == float("-inf"):
            return None
        return v
    return val


# ---------------------------------------------------------------------------
# Photutils-based implementation
# ---------------------------------------------------------------------------

def _run_photutils(
    image: np.ndarray,
    fwhm: float,
    threshold_sigma: float,
    aperture_radii: list[float],
) -> dict:
    from photutils.background import Background2D, MedianBackground
    from photutils.detection import DAOStarFinder
    from photutils.aperture import CircularAperture, aperture_photometry

    # --- background estimation ---
    bkg_estimator = MedianBackground()
    try:
        bkg = Background2D(image, (50, 50), bkg_estimator=bkg_estimator)
    except Exception:
        # Fall back to a global estimate when the image is too small
        bkg_median = float(np.nanmedian(image))
        bkg_rms = float(np.nanstd(image))
        image_sub = image - bkg_median
        bkg = None
    else:
        bkg_median = float(np.nanmedian(bkg.background))
        bkg_rms = float(np.nanmedian(bkg.background_rms))
        image_sub = image - bkg.background

    # --- source detection ---
    finder = DAOStarFinder(fwhm=fwhm, threshold=threshold_sigma * bkg_rms)
    sources_tbl = finder(image_sub)

    if sources_tbl is None or len(sources_tbl) == 0:
        return _empty_result(bkg_median, bkg_rms, fwhm)

    x = np.array(sources_tbl["xcentroid"])
    y = np.array(sources_tbl["ycentroid"])
    sharpness = np.array(sources_tbl["sharpness"])
    roundness = np.array(sources_tbl["roundness1"])
    flux_peak = np.array(sources_tbl["peak"])

    # --- PSF photometry (best-effort) ---
    psf_flux: np.ndarray | None = None
    psf_flux_err: np.ndarray | None = None
    try:
        from photutils.psf import (
            EPSFBuilder,
            PSFPhotometry,
            IntegratedGaussianPRF,
        )
        from photutils.psf import extract_stars
        from astropy.nddata import NDData
        from astropy.table import Table as AstropyTable

        # Select brightest isolated stars to build EPSF
        n_epsf = min(25, max(5, len(sources_tbl) // 5))
        sorted_idx = np.argsort(-flux_peak)[:n_epsf]
        stars_tbl = AstropyTable()
        stars_tbl["x"] = x[sorted_idx]
        stars_tbl["y"] = y[sorted_idx]

        size = int(4 * fwhm) | 1  # ensure odd
        size = max(size, 11)

        nddata = NDData(data=image_sub)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            stars = extract_stars(nddata, stars_tbl, size=size)

        if len(stars) >= 3:
            epsf_builder = EPSFBuilder(
                oversampling=2,
                maxiters=5,
                progress_bar=False,
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                epsf, _fitted_stars = epsf_builder(stars)
            psf_model = epsf
        else:
            psf_model = IntegratedGaussianPRF(sigma=fwhm / 2.355)

        # Fit PSF to all detected positions
        init_params = AstropyTable()
        init_params["x_0"] = x
        init_params["y_0"] = y

        phot = PSFPhotometry(psf_model, fit_shape=(size, size))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = phot(image_sub, init_params=init_params)

        if "flux_fit" in result.colnames:
            psf_flux = np.array(result["flux_fit"])
        elif "flux_0" in result.colnames:
            psf_flux = np.array(result["flux_0"])
        if "flux_unc" in result.colnames:
            psf_flux_err = np.array(result["flux_unc"])

    except Exception:
        # PSF fitting failed -- will fall through to aperture only
        pass

    if psf_flux is None:
        psf_flux = np.full(len(x), np.nan)
    if psf_flux_err is None:
        psf_flux_err = np.full(len(x), np.nan)

    # --- aperture photometry ---
    positions = np.column_stack((x, y))
    ap_fluxes: dict[str, np.ndarray] = {}
    for r in aperture_radii:
        ap = CircularAperture(positions, r=r)
        phot_table = aperture_photometry(image_sub, ap)
        ap_fluxes[f"aperture_flux_{int(r)}"] = np.array(
            phot_table["aperture_sum"]
        )

    return _build_result(
        x, y, psf_flux, psf_flux_err, sharpness, roundness,
        ap_fluxes, aperture_radii, bkg_median, bkg_rms, fwhm,
    )


# ---------------------------------------------------------------------------
# Fallback: sep-based aperture photometry only
# ---------------------------------------------------------------------------

def _run_sep_fallback(
    image: np.ndarray,
    fwhm: float,
    threshold_sigma: float,
    aperture_radii: list[float],
) -> dict:
    try:
        import sep
    except ImportError:
        raise ImportError(
            "PSFPhotometry requires either 'photutils' or 'sep'. "
            "Install one of them:  pip install photutils  OR  pip install sep"
        )

    # sep requires C-contiguous float32/float64
    data = np.ascontiguousarray(image, dtype=np.float64)
    bkg = sep.Background(data)
    bkg_median = float(np.nanmedian(bkg.back()))
    bkg_rms = float(np.nanmedian(bkg.rms()))
    data_sub = data - bkg.back()

    objects = sep.extract(data_sub, threshold_sigma, err=bkg.rms())
    if len(objects) == 0:
        return _empty_result(bkg_median, bkg_rms, fwhm)

    x = objects["x"]
    y = objects["y"]

    # Aperture photometry at each radius
    ap_fluxes: dict[str, np.ndarray] = {}
    for r in aperture_radii:
        flux, _fluxerr, _flag = sep.sum_circle(data_sub, x, y, r)
        ap_fluxes[f"aperture_flux_{int(r)}"] = flux

    psf_flux = np.full(len(x), np.nan)
    psf_flux_err = np.full(len(x), np.nan)
    sharpness = np.full(len(x), np.nan)
    roundness = np.full(len(x), np.nan)

    return _build_result(
        x, y, psf_flux, psf_flux_err, sharpness, roundness,
        ap_fluxes, aperture_radii, bkg_median, bkg_rms, fwhm,
    )


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _empty_result(bkg_median: float, bkg_rms: float, fwhm: float) -> dict:
    radii_cols = ["aperture_flux_3", "aperture_flux_5", "aperture_flux_7"]
    columns = [
        "id", "x", "y", "psf_flux", "psf_flux_err",
        *radii_cols, "sharpness", "roundness",
    ]
    return {
        "type": "photometry",
        "fits_path": "",
        "n_sources": 0,
        "columns": columns,
        "data": {c: [] for c in columns},
        "rows": [],
        "background_median": _safe(bkg_median),
        "background_rms": _safe(bkg_rms),
        "fwhm_used": fwhm,
    }


def _build_result(
    x: np.ndarray,
    y: np.ndarray,
    psf_flux: np.ndarray,
    psf_flux_err: np.ndarray,
    sharpness: np.ndarray,
    roundness: np.ndarray,
    ap_fluxes: dict[str, np.ndarray],
    aperture_radii: list[float],
    bkg_median: float,
    bkg_rms: float,
    fwhm: float,
) -> dict:
    n = len(x)
    ids = list(range(1, n + 1))

    columns = [
        "id", "x", "y", "psf_flux", "psf_flux_err",
        *[f"aperture_flux_{int(r)}" for r in aperture_radii],
        "sharpness", "roundness",
    ]

    data: dict[str, list] = {
        "id": ids,
        "x": [_safe(v) for v in x],
        "y": [_safe(v) for v in y],
        "psf_flux": [_safe(v) for v in psf_flux],
        "psf_flux_err": [_safe(v) for v in psf_flux_err],
        "sharpness": [_safe(v) for v in sharpness],
        "roundness": [_safe(v) for v in roundness],
    }
    for col_name, arr in ap_fluxes.items():
        data[col_name] = [_safe(v) for v in arr]

    rows = []
    for i in range(n):
        row = {c: data[c][i] for c in columns}
        rows.append(row)

    return {
        "type": "photometry",
        "fits_path": "",
        "n_sources": n,
        "columns": columns,
        "data": data,
        "rows": rows,
        "background_median": _safe(bkg_median),
        "background_rms": _safe(bkg_rms),
        "fwhm_used": fwhm,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def psf_photometry(input_data: dict | None, params: dict) -> dict:
    """PSF photometry node using photutils.

    params:
        fits_path: Path to FITS image (optional, uses upstream if blank)
        fwhm: Expected FWHM in pixels (default 3.0)
        threshold_sigma: Detection threshold in sigma (default 5.0)
        aperture_radii: Comma-separated aperture radii for comparison (default "3,5,7")
    """
    fits_path = str(
        params.get("fits_path")
        or (input_data or {}).get("output_path")
        or (input_data or {}).get("fits_path")
        or ""
    )
    if not fits_path:
        raise ValueError("PSFPhotometry requires a FITS path in params or upstream data")

    fwhm = float(params.get("fwhm", 3.0))
    threshold_sigma = float(params.get("threshold_sigma", 5.0))
    aperture_radii = _parse_aperture_radii(params.get("aperture_radii", "3,5,7"))

    image, _header = _load_image(fits_path)

    # Try photutils first; fall back to sep
    try:
        result = _run_photutils(image, fwhm, threshold_sigma, aperture_radii)
    except ImportError:
        result = _run_sep_fallback(image, fwhm, threshold_sigma, aperture_radii)

    result["fits_path"] = fits_path
    return result
