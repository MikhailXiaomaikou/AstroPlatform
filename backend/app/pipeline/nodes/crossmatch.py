"""Cross-match node — match two sets of sky coordinates within a separation radius.

Phase D2.1 — proper-motion epoch propagation:
When either catalog has `pmra / pmdec / parallax` columns and the two
catalogs report observations at different epochs (e.g. Gaia DR3 at 2016
vs SDSS at 2000), we propagate catalog 1's positions to the target
epoch before matching.  This closes the dominant systematic at high
proper motion (van Leeuwen 2007 A&A 474, Lindegren+ 2021 A&A 649).
"""

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.time import Time
import astropy.units as u


def _propagate_positions(ra, dec, pmra, pmdec, parallax, rv, epoch_from, epoch_to):
    """Return (ra, dec) at ``epoch_to`` given initial state at ``epoch_from``."""
    if epoch_from is None or epoch_to is None or epoch_from == epoch_to:
        return ra, dec
    try:
        t_from = Time(float(epoch_from), format="jyear")
        t_to = Time(float(epoch_to), format="jyear")
    except Exception:
        return ra, dec

    # Missing kinematic components default to zero so we at least run
    # the math on the ones the caller did provide.
    n = len(ra)
    pmra_arr = np.asarray(pmra, dtype=float) if pmra is not None and len(pmra) == n else np.zeros(n)
    pmdec_arr = np.asarray(pmdec, dtype=float) if pmdec is not None and len(pmdec) == n else np.zeros(n)
    plx_arr = np.asarray(parallax, dtype=float) if parallax is not None and len(parallax) == n else np.full(n, 1e-6)
    rv_arr = np.asarray(rv, dtype=float) if rv is not None and len(rv) == n else np.zeros(n)

    # Guard unphysical values that would blow up apply_space_motion.
    pmra_arr = np.nan_to_num(pmra_arr, nan=0.0, posinf=0.0, neginf=0.0)
    pmdec_arr = np.nan_to_num(pmdec_arr, nan=0.0, posinf=0.0, neginf=0.0)
    plx_arr = np.where(np.isfinite(plx_arr) & (plx_arr > 0), plx_arr, 1e-6)
    rv_arr = np.nan_to_num(rv_arr, nan=0.0, posinf=0.0, neginf=0.0)

    c = SkyCoord(
        ra=np.asarray(ra) * u.degree,
        dec=np.asarray(dec) * u.degree,
        pm_ra_cosdec=pmra_arr * (u.mas / u.yr),
        pm_dec=pmdec_arr * (u.mas / u.yr),
        distance=(1000.0 / plx_arr) * u.pc,
        radial_velocity=rv_arr * (u.km / u.s),
        obstime=t_from,
        frame="icrs",
    )
    c_new = c.apply_space_motion(new_obstime=t_to)
    return c_new.ra.degree, c_new.dec.degree


def crossmatch(input_data: dict, params: dict) -> dict:
    """Cross-match two catalogs by sky coordinates.

    params:
        ra_key_1: str — RA key for first catalog (default "ra")
        dec_key_1: str — Dec key for first catalog (default "dec")
        ra_key_2: str — RA key for second catalog (default "ra_2")
        dec_key_2: str — Dec key for second catalog (default "dec_2")
        max_sep: float — maximum separation in arcseconds (default 3.0)
        pm_key_ra_1, pm_key_dec_1, parallax_key_1, rv_key_1: optional
            keys naming the proper-motion / parallax / RV columns of
            catalog 1.  When present *and* ``epoch_1`` + ``epoch_2``
            differ, catalog 1 positions are propagated to ``epoch_2``
            before matching.
        epoch_1: float — Julian year of catalog 1 observations (e.g. 2016.0 for Gaia DR3)
        epoch_2: float — Julian year of catalog 2 observations
    """
    ra_key_1 = params.get("ra_key_1", "ra")
    dec_key_1 = params.get("dec_key_1", "dec")
    ra_key_2 = params.get("ra_key_2", "ra_2")
    dec_key_2 = params.get("dec_key_2", "dec_2")
    max_sep = float(params.get("max_sep", 3.0))
    epoch_1 = params.get("epoch_1")
    epoch_2 = params.get("epoch_2")

    data = input_data.get("data", {})

    ra1 = np.array(data.get(ra_key_1, []), dtype=float)
    dec1 = np.array(data.get(dec_key_1, []), dtype=float)
    ra2 = np.array(data.get(ra_key_2, []), dtype=float)
    dec2 = np.array(data.get(dec_key_2, []), dtype=float)

    if len(ra1) == 0 or len(dec1) == 0:
        raise ValueError(
            f"CrossMatch: no data found for catalog 1 keys '{ra_key_1}', '{dec_key_1}'"
        )
    if len(ra2) == 0 or len(dec2) == 0:
        raise ValueError(
            f"CrossMatch: no data found for catalog 2 keys '{ra_key_2}', '{dec_key_2}'"
        )

    # D2.1 — propagate catalog 1 to epoch 2 when epochs differ and PM is available.
    pm_epoch_note: str | None = None
    if epoch_1 is not None and epoch_2 is not None and float(epoch_1) != float(epoch_2):
        pmra = data.get(params.get("pm_key_ra_1", "pmra"))
        pmdec = data.get(params.get("pm_key_dec_1", "pmdec"))
        plx = data.get(params.get("parallax_key_1", "parallax"))
        rv = data.get(params.get("rv_key_1", "radial_velocity_km_s"))
        try:
            ra1, dec1 = _propagate_positions(ra1, dec1, pmra, pmdec, plx, rv,
                                              epoch_1, epoch_2)
            pm_epoch_note = f"Catalog 1 positions propagated from {epoch_1} to {epoch_2} via SkyCoord.apply_space_motion"
        except Exception as exc:
            pm_epoch_note = f"PM propagation failed, using static positions: {exc}"

    catalog1 = SkyCoord(ra=ra1, dec=dec1, unit=(u.degree, u.degree), frame="icrs")
    catalog2 = SkyCoord(ra=ra2, dec=dec2, unit=(u.degree, u.degree), frame="icrs")

    # Match catalog1 against catalog2
    idx, sep2d, _ = catalog1.match_to_catalog_sky(catalog2)

    # Filter by maximum separation
    sep_arcsec = sep2d.arcsec
    good = sep_arcsec <= max_sep

    matched_idx_1 = np.where(good)[0]
    matched_idx_2 = idx[good]
    matched_sep = sep_arcsec[good]

    output_data = dict(data)
    output_data["matched_idx_1"] = matched_idx_1.tolist()
    output_data["matched_idx_2"] = matched_idx_2.tolist()
    output_data["matched_sep_arcsec"] = matched_sep.tolist()

    crossmatch_result: dict = {
        "match_count": int(np.sum(good)),
        "total_catalog_1": len(ra1),
        "total_catalog_2": len(ra2),
        "max_sep_arcsec": max_sep,
        "mean_sep_arcsec": float(np.mean(matched_sep)) if len(matched_sep) > 0 else 0.0,
        "median_sep_arcsec": float(np.median(matched_sep)) if len(matched_sep) > 0 else 0.0,
    }
    if pm_epoch_note:
        crossmatch_result["epoch_propagation"] = pm_epoch_note
        crossmatch_result["epoch_from"] = epoch_1
        crossmatch_result["epoch_to"] = epoch_2

    return {
        **input_data,
        "data": output_data,
        "crossmatch_result": crossmatch_result,
    }
