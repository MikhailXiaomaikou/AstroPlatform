"""Basic CCD image reduction helpers."""

from __future__ import annotations

import io
import os
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from astropy.io import fits
from astropy.table import Table

from app.config import settings
from app.storage import download_fits, upload_fits


def _read_fits_array(path: str) -> tuple[np.ndarray, fits.Header]:
    if os.path.isabs(path) and os.path.exists(path):
        raw = Path(path).read_bytes()
    else:
        raw = download_fits(path)
    with fits.open(io.BytesIO(raw)) as hdul:
        for hdu in hdul:
            if hdu.data is not None:
                return np.asarray(hdu.data, dtype=float), hdu.header.copy()
    raise ValueError(f"No image data found in FITS file: {path}")


def _write_fits_array(data: np.ndarray, header: fits.Header, path: str) -> str:
    hdu = fits.PrimaryHDU(np.asarray(data, dtype=np.float32), header=header)
    buf = io.BytesIO()
    hdu.writeto(buf, overwrite=True)
    upload_fits(path, buf.getvalue())
    return path


def _median_combine(frames: list[np.ndarray]) -> np.ndarray:
    if not frames:
        raise ValueError("At least one calibration frame is required")
    stack = np.stack([np.asarray(frame, dtype=float) for frame in frames], axis=0)
    return np.nanmedian(stack, axis=0)


def bias_subtract(science_data: np.ndarray, bias_frames: list[np.ndarray]) -> np.ndarray:
    """Create a master bias by median-combining frames and subtract it."""
    try:
        import ccdproc  # noqa: F401
        from astropy import units as u
        from astropy.nddata import CCDData

        master_bias = ccdproc.combine([CCDData(frame, unit=u.adu) for frame in bias_frames], method="median")
        corrected = ccdproc.subtract_bias(CCDData(science_data, unit=u.adu), master_bias)
        return np.asarray(corrected.data, dtype=float)
    except Exception:
        return np.asarray(science_data, dtype=float) - _median_combine(bias_frames)


def dark_subtract(science_data: np.ndarray, dark_frames: list[np.ndarray], science_exptime: float, dark_exptime: float) -> np.ndarray:
    """Create a master dark, scale it by exposure time ratio, and subtract it."""
    master_dark = _median_combine(dark_frames)
    scale = 1.0 if dark_exptime in (0, None) else float(science_exptime) / float(dark_exptime)
    return np.asarray(science_data, dtype=float) - master_dark * scale


def flat_correct(science_data: np.ndarray, flat_frames: list[np.ndarray]) -> np.ndarray:
    """Create a normalized master flat and divide the science frame by it."""
    master_flat = _median_combine(flat_frames)
    mean_flat = float(np.nanmean(master_flat))
    if mean_flat == 0.0 or not np.isfinite(mean_flat):
        raise ValueError("Flat field normalization failed because the master flat mean is zero or invalid")
    normalized = master_flat / mean_flat
    safe = np.where(np.abs(normalized) < 1e-6, 1.0, normalized)
    return np.asarray(science_data, dtype=float) / safe


def cosmic_ray_reject(data: np.ndarray, sigclip: float = 5.0, sigfrac: float = 0.3, objlim: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Detect and clean cosmic rays, using astroscrappy when available."""
    array = np.asarray(data, dtype=float)
    try:
        import astroscrappy

        mask, cleaned = astroscrappy.detect_cosmics(array, sigclip=sigclip, sigfrac=sigfrac, objlim=objlim)
        return np.asarray(cleaned, dtype=float), np.asarray(mask, dtype=bool)
    except Exception:
        median = np.nanmedian(array)
        std = np.nanstd(array) or 1.0
        threshold = median + sigclip * std
        mask = array > threshold
        cleaned = array.copy()
        cleaned[mask] = median
        return cleaned, mask


def full_reduction(
    science_fits_path: str,
    bias_paths: list[str] | None = None,
    dark_paths: list[str] | None = None,
    flat_paths: list[str] | None = None,
    cosmic_ray: bool = True,
) -> dict:
    """Run a full CCD reduction pipeline and save a reduced FITS product."""
    science_data, header = _read_fits_array(science_fits_path)
    reduction_log: list[str] = []
    science_exptime = float(header.get("EXPTIME", 1.0) or 1.0)
    bias_frames: list[np.ndarray] = []

    if bias_paths:
        bias_frames = [_read_fits_array(path)[0] for path in bias_paths]
        science_data = bias_subtract(science_data, bias_frames)
        reduction_log.append(f"Bias subtraction using {len(bias_paths)} frame(s)")
        header.add_history(reduction_log[-1])

    if dark_paths:
        dark_arrays = []
        dark_exptime = science_exptime
        for path in dark_paths:
            dark_data, dark_header = _read_fits_array(path)
            if bias_frames:
                dark_data = bias_subtract(dark_data, bias_frames)
            dark_arrays.append(dark_data)
            dark_exptime = float(dark_header.get("EXPTIME", dark_exptime) or dark_exptime)
        science_data = dark_subtract(science_data, dark_arrays, science_exptime=science_exptime, dark_exptime=dark_exptime)
        reduction_log.append(f"Dark subtraction using {len(dark_paths)} frame(s)")
        header.add_history(reduction_log[-1])

    if flat_paths:
        flat_frames = []
        for path in flat_paths:
            flat_data, flat_header = _read_fits_array(path)
            if bias_frames:
                flat_data = bias_subtract(flat_data, bias_frames)
            if dark_paths:
                flat_exptime = float(flat_header.get("EXPTIME", science_exptime) or science_exptime)
                flat_data = dark_subtract(
                    flat_data,
                    dark_arrays,
                    science_exptime=flat_exptime,
                    dark_exptime=dark_exptime,
                )
            flat_frames.append(flat_data)
        science_data = flat_correct(science_data, flat_frames)
        reduction_log.append(f"Flat-field correction using {len(flat_paths)} frame(s)")
        header.add_history(reduction_log[-1])

    cosmic_mask = None
    if cosmic_ray:
        science_data, cosmic_mask = cosmic_ray_reject(science_data)
        reduction_log.append("Cosmic-ray cleaning applied")
        header.add_history(reduction_log[-1])

    output_path = f"processed/{uuid.uuid4().hex}_reduced.fits"
    _write_fits_array(science_data, header, output_path)

    return {
        "reduced_data": science_data,
        "header": {key: str(value) for key, value in header.items() if key},
        "reduction_log": reduction_log,
        "output_path": output_path,
        "cosmic_ray_mask_pixels": int(np.sum(cosmic_mask)) if cosmic_mask is not None else 0,
    }


async def solve_astrometry(fits_path: str) -> dict:
    """Submit a FITS image to astrometry.net and store the resulting WCS solution when available."""
    import httpx

    api_key = os.getenv("ASTROMETRY_API_KEY", "")
    if not api_key:
        return {"solved": False, "error": "ASTROMETRY_API_KEY is not configured"}

    raw = download_fits(fits_path)
    async with httpx.AsyncClient(timeout=30.0) as client:
        login_resp = await client.post("https://nova.astrometry.net/api/login", json={"apikey": api_key})
        login_resp.raise_for_status()
        login_data = login_resp.json()
        session_token = login_data.get("session")
        if not session_token:
            return {"solved": False, "error": "Astrometry login failed"}

        files = {"file": ("image.fits", raw, "application/fits")}
        upload_resp = await client.post(
            "https://nova.astrometry.net/api/upload",
            data={"request-json": __import__("json").dumps({"session": session_token})},
            files=files,
        )
        upload_resp.raise_for_status()
        subid = upload_resp.json().get("subid")
        if not subid:
            return {"solved": False, "error": "Astrometry upload failed"}

        job_id = None
        deadline = time.time() + 120
        while time.time() < deadline and job_id is None:
            await __import__("asyncio").sleep(4)
            jobs_resp = await client.get(f"https://nova.astrometry.net/api/submissions/{subid}")
            jobs_resp.raise_for_status()
            jobs = jobs_resp.json().get("jobs") or []
            job_id = next((job for job in jobs if job), None)
        if not job_id:
            return {"solved": False, "error": "Timed out waiting for astrometry job"}

        info_resp = await client.get(f"https://nova.astrometry.net/api/jobs/{job_id}/info")
        info_resp.raise_for_status()
        calibration = info_resp.json().get("calibration") or {}
        if not calibration:
            return {"solved": False, "error": "No astrometric calibration returned"}

    data, header = _read_fits_array(fits_path)
    height, width = data.shape[:2]
    pixscale_arcsec = calibration.get("pixscale")
    orientation_deg = calibration.get("orientation")
    parity = str(calibration.get("parity", "")).lower()
    if "ra" in calibration:
        header["CRVAL1"] = float(calibration["ra"])
    if "dec" in calibration:
        header["CRVAL2"] = float(calibration["dec"])
    header["CRPIX1"] = float(width) / 2.0
    header["CRPIX2"] = float(height) / 2.0
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CUNIT1"] = "deg"
    header["CUNIT2"] = "deg"

    if pixscale_arcsec:
        scale_deg = float(pixscale_arcsec) / 3600.0
        theta = np.deg2rad(float(orientation_deg or 0.0))
        mirror = -1.0 if parity == "pos" else 1.0
        header["CD1_1"] = -scale_deg * np.cos(theta)
        header["CD1_2"] = mirror * scale_deg * np.sin(theta)
        header["CD2_1"] = scale_deg * np.sin(theta)
        header["CD2_2"] = mirror * scale_deg * np.cos(theta)

    header.add_history("Astrometric solution added via astrometry.net")
    solved_path = f"processed/{uuid.uuid4().hex}_wcs.fits"
    _write_fits_array(data, header, solved_path)
    return {
        "solved": True,
        "ra_center": calibration.get("ra"),
        "dec_center": calibration.get("dec"),
        "pixel_scale": calibration.get("pixscale"),
        "rotation": calibration.get("orientation"),
        "output_path": solved_path,
        "wcs_header": {key: str(value) for key, value in header.items() if key.startswith(("CR", "CD", "CTYPE", "CDELT", "CUNIT"))},
    }


def extract_and_photometer(reduced_fits_path: str, aperture_radii: list[int] | None = None) -> pd.DataFrame:
    """Detect sources and perform basic aperture photometry."""
    aperture_radii = aperture_radii or [3, 5, 7]
    data, header = _read_fits_array(reduced_fits_path)
    image = np.asarray(data, dtype=float)

    objects = None
    background_rms = float(np.nanstd(image))
    threshold = np.nanmedian(image) + 3.0 * background_rms
    try:
        import sep

        image32 = image.astype(np.float32)
        bkg = sep.Background(image32)
        objects = sep.extract(image32 - bkg, 3.0, err=bkg.globalrms)
    except Exception:
        ys, xs = np.where(image > threshold)
        objects = np.zeros(len(xs), dtype=[("x", float), ("y", float), ("a", float), ("b", float), ("theta", float), ("npix", float)])
        objects["x"] = xs
        objects["y"] = ys
        objects["a"] = 2.5
        objects["b"] = 2.0
        objects["theta"] = 0.0
        objects["npix"] = 1

    rows = []
    y_grid, x_grid = np.indices(image.shape)
    for obj in objects[:500]:
        x = float(obj["x"])
        y = float(obj["y"])
        fluxes = {}
        for radius in aperture_radii:
            mask = (x_grid - x) ** 2 + (y_grid - y) ** 2 <= radius ** 2
            flux = float(np.nansum(image[mask]))
            fluxes[f"flux_{radius}px"] = flux
        mag_inst = float(-2.5 * np.log10(max(fluxes[f"flux_{aperture_radii[1] if len(aperture_radii) > 1 else aperture_radii[0]}px"], 1e-6)))
        row = {
            "x": x,
            "y": y,
            "ra": None,
            "dec": None,
            "mag_inst": mag_inst,
            "fwhm": float(2.355 * max(float(obj["a"]), float(obj["b"])) / 2.0),
            "ellipticity": float(1.0 - min(float(obj["a"]), float(obj["b"])) / max(float(obj["a"]), 1e-6)),
        }
        row.update(fluxes)
        rows.append(row)

    if "CRVAL1" in header and "CRVAL2" in header:
        try:
            from astropy.wcs import WCS

            wcs = WCS(header)
            for row in rows:
                ra, dec = wcs.pixel_to_world_values(row["x"], row["y"])
                row["ra"] = float(ra)
                row["dec"] = float(dec)
        except Exception:
            pass

    return pd.DataFrame(rows)
