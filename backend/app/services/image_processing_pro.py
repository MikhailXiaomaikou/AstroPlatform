"""Advanced image processing service using reproject and photutils.

Provides image reprojection, mosaicking, PSF matching, source deblending,
and cutout extraction for professional astronomical imaging.
"""

import io
import logging
import uuid

import numpy as np

logger = logging.getLogger(__name__)


def reproject_image(fits_path: str, target_wcs_fits: str | None = None,
                    target_ra: float | None = None, target_dec: float | None = None,
                    pixscale: float = 1.0, size_pix: int = 500,
                    method: str = "interp") -> dict:
    """Reproject a FITS image to a new WCS.

    Either provide target_wcs_fits (FITS file with target WCS) or
    target_ra/dec/pixscale/size_pix to define a new projection.
    """
    from astropy.io import fits as pyfits
    from astropy.wcs import WCS

    try:
        if method == "exact":
            from reproject import reproject_exact as _reproject
        else:
            from reproject import reproject_interp as _reproject
    except ImportError:
        return {"error": "reproject not installed"}

    with pyfits.open(fits_path) as hdul:
        input_data = hdul[0].data
        input_header = hdul[0].header
        input_wcs = WCS(input_header)

    if input_data is None:
        return {"error": "No image data in primary HDU"}

    # Build target WCS
    if target_wcs_fits:
        with pyfits.open(target_wcs_fits) as hdul2:
            target_header = hdul2[0].header
            target_wcs = WCS(target_header)
            shape_out = (target_header.get("NAXIS2", size_pix),
                        target_header.get("NAXIS1", size_pix))
    else:
        if target_ra is None:
            target_ra = input_wcs.wcs.crval[0]
        if target_dec is None:
            target_dec = input_wcs.wcs.crval[1]

        from astropy.wcs import WCS as MakeWCS
        target_wcs = MakeWCS(naxis=2)
        target_wcs.wcs.crpix = [size_pix / 2, size_pix / 2]
        target_wcs.wcs.crval = [target_ra, target_dec]
        target_wcs.wcs.cdelt = [-pixscale / 3600.0, pixscale / 3600.0]
        target_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        shape_out = (size_pix, size_pix)

    reprojected, footprint = _reproject((input_data, input_wcs),
                                         target_wcs, shape_out=shape_out)

    # Save result
    from app.storage import upload_fits
    output_header = target_wcs.to_header()
    hdu = pyfits.PrimaryHDU(reprojected, header=output_header)
    buf = io.BytesIO()
    hdu.writeto(buf, overwrite=True)
    buf.seek(0)

    out_path = f"reprojected/{uuid.uuid4()}.fits"
    upload_fits(out_path, buf.read())

    return {
        "output_path": out_path,
        "shape": list(reprojected.shape),
        "method": method,
        "coverage_fraction": float(np.nanmean(footprint)),
    }


def mosaic_images(fits_paths: list[str], method: str = "mean",
                  pixscale: float | None = None) -> dict:
    """Mosaic multiple FITS images into a single coadd."""
    from astropy.io import fits as pyfits
    from astropy.wcs import WCS

    try:
        from reproject.mosaicking import reproject_and_coadd
        from reproject import reproject_interp
    except ImportError:
        return {"error": "reproject not installed"}

    # Load all images
    hdus = []
    for path in fits_paths:
        with pyfits.open(path) as hdul:
            hdus.append((hdul[0].data.copy(), WCS(hdul[0].header)))

    if len(hdus) < 2:
        return {"error": "Need at least 2 images for mosaicking"}

    # Determine optimal WCS for output
    from reproject.mosaicking import find_optimal_celestial_wcs
    wcs_out, shape_out = find_optimal_celestial_wcs(
        [(d, w) for d, w in hdus],
        resolution=pixscale / 3600.0 if pixscale else None,
    )

    combine_func = np.nanmean if method == "mean" else np.nanmedian

    mosaic, footprint = reproject_and_coadd(
        hdus, wcs_out, shape_out=shape_out,
        reproject_function=reproject_interp,
        combine_function=combine_func,
    )

    # Save
    from app.storage import upload_fits
    header = wcs_out.to_header()
    hdu = pyfits.PrimaryHDU(mosaic, header=header)
    buf = io.BytesIO()
    hdu.writeto(buf, overwrite=True)
    buf.seek(0)

    out_path = f"mosaics/{uuid.uuid4()}.fits"
    upload_fits(out_path, buf.read())

    return {
        "output_path": out_path,
        "shape": list(mosaic.shape),
        "n_input_images": len(fits_paths),
        "method": method,
        "coverage_fraction": float(np.nanmean(footprint > 0)),
    }


def psf_match(fits_path: str, target_fwhm: float,
              current_fwhm: float | None = None) -> dict:
    """Match the PSF of an image to a target FWHM."""
    from astropy.io import fits as pyfits
    from astropy.convolution import convolve, Gaussian2DKernel

    with pyfits.open(fits_path) as hdul:
        data = hdul[0].data.copy()
        header = hdul[0].header.copy()

    if data is None:
        return {"error": "No image data"}

    # Estimate current FWHM if not provided
    if current_fwhm is None:
        try:
            from photutils.detection import DAOStarFinder
            from astropy.stats import sigma_clipped_stats
            _, median, std = sigma_clipped_stats(data, sigma=3.0)
            finder = DAOStarFinder(fwhm=3.0, threshold=5 * std)
            sources = finder(data - median)
            if sources is not None and len(sources) > 0:
                current_fwhm = float(np.median(sources['fwhm']))
            else:
                current_fwhm = 3.0
        except Exception as e:
            logger.debug("FWHM auto-detection failed, using default: %s", e)
            current_fwhm = 3.0

    if target_fwhm <= current_fwhm:
        return {"error": f"Target FWHM ({target_fwhm}) must be > current ({current_fwhm})"}

    # Compute matching kernel
    sigma_diff = np.sqrt(target_fwhm**2 - current_fwhm**2) / 2.3548
    kernel = Gaussian2DKernel(sigma_diff)

    matched = convolve(data, kernel, preserve_nan=True)

    # Save
    from app.storage import upload_fits
    hdu = pyfits.PrimaryHDU(matched, header=header)
    hdu.header['PSF_FWHM'] = target_fwhm
    hdu.header['PSF_ORIG'] = current_fwhm
    buf = io.BytesIO()
    hdu.writeto(buf, overwrite=True)
    buf.seek(0)

    out_path = f"psf_matched/{uuid.uuid4()}.fits"
    upload_fits(out_path, buf.read())

    return {
        "output_path": out_path,
        "original_fwhm": float(current_fwhm),
        "target_fwhm": float(target_fwhm),
        "kernel_sigma": float(sigma_diff),
    }


def deblend_sources(fits_path: str, threshold_sigma: float = 2.0,
                    npixels: int = 5, contrast: float = 0.001) -> dict:
    """Detect and deblend sources using photutils segmentation."""
    from astropy.io import fits as pyfits
    from astropy.stats import sigma_clipped_stats

    with pyfits.open(fits_path) as hdul:
        data = hdul[0].data

    if data is None:
        return {"error": "No image data"}

    try:
        from photutils.segmentation import detect_sources, deblend_sources as _deblend
        from photutils.segmentation import SourceCatalog
        from astropy.convolution import Gaussian2DKernel

        _, median, std = sigma_clipped_stats(data, sigma=3.0)
        # Threshold is applied to the background-subtracted image below,
        # so we pass the raw N-sigma cut (threshold_sigma * std) rather
        # than median + N*std.  Keep a reference so future tuning can
        # switch to the median-offset form without rewiring the call.
        _threshold_abs = median + threshold_sigma * std  # noqa: F841 — documentation

        # Small Gaussian kernel for smoothing before detection.
        _kernel = Gaussian2DKernel(2.0)  # noqa: F841 — reserved for filter_kernel arg

        segm = detect_sources(data - median, threshold=threshold_sigma * std,
                             npixels=npixels, connectivity=8)

        if segm is None:
            return {"sources": [], "n_sources": 0}

        # Deblend
        segm_deblend = _deblend(data - median, segm, npixels=npixels,
                               contrast=contrast, nlevels=32)

        cat = SourceCatalog(data - median, segm_deblend)

        sources = []
        for src in cat:
            sources.append({
                "id": int(src.label),
                "x": float(src.xcentroid),
                "y": float(src.ycentroid),
                "flux": float(src.segment_flux),
                "area_pix": int(src.area.value) if hasattr(src.area, 'value') else int(src.area),
                "semimajor": float(src.semimajor_sigma.value) if hasattr(src.semimajor_sigma, 'value') else float(src.semimajor_sigma),
                "semiminor": float(src.semiminor_sigma.value) if hasattr(src.semiminor_sigma, 'value') else float(src.semiminor_sigma),
                "orientation_deg": float(src.orientation.value) if hasattr(src.orientation, 'value') else float(src.orientation),
                "deblended": True,
            })

        return {
            "sources": sources,
            "n_sources": len(sources),
            "n_before_deblend": segm.nlabels,
            "n_after_deblend": segm_deblend.nlabels,
            "threshold_sigma": threshold_sigma,
        }
    except ImportError:
        return {"error": "photutils not installed"}


def cutout_service(fits_path: str, ra: float, dec: float,
                   size_arcsec: float = 60.0) -> dict:
    """Extract a postage stamp cutout from a FITS image."""
    from astropy.io import fits as pyfits
    from astropy.wcs import WCS
    from astropy.nddata import Cutout2D
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    with pyfits.open(fits_path) as hdul:
        data = hdul[0].data
        header = hdul[0].header
        wcs = WCS(header)

    if data is None:
        return {"error": "No image data"}

    position = SkyCoord(ra, dec, unit="deg")
    size = size_arcsec * u.arcsec

    try:
        cutout = Cutout2D(data, position, size, wcs=wcs)
    except Exception as e:
        return {"error": f"Cutout failed: {e}"}

    # Save cutout
    from app.storage import upload_fits
    hdu = pyfits.PrimaryHDU(cutout.data, header=cutout.wcs.to_header())
    buf = io.BytesIO()
    hdu.writeto(buf, overwrite=True)
    buf.seek(0)

    out_path = f"cutouts/{uuid.uuid4()}.fits"
    upload_fits(out_path, buf.read())

    return {
        "output_path": out_path,
        "shape": list(cutout.shape),
        "center_ra": ra,
        "center_dec": dec,
        "size_arcsec": size_arcsec,
    }
