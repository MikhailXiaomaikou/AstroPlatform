"""Cross-match node — match two sets of sky coordinates within a separation radius."""

import numpy as np
from astropy.coordinates import SkyCoord
import astropy.units as u


def crossmatch(input_data: dict, params: dict) -> dict:
    """Cross-match two catalogs by sky coordinates.

    params:
        ra_key_1: str — RA key for first catalog (default "ra")
        dec_key_1: str — Dec key for first catalog (default "dec")
        ra_key_2: str — RA key for second catalog (default "ra_2")
        dec_key_2: str — Dec key for second catalog (default "dec_2")
        max_sep: float — maximum separation in arcseconds (default 3.0)
    """
    ra_key_1 = params.get("ra_key_1", "ra")
    dec_key_1 = params.get("dec_key_1", "dec")
    ra_key_2 = params.get("ra_key_2", "ra_2")
    dec_key_2 = params.get("dec_key_2", "dec_2")
    max_sep = float(params.get("max_sep", 3.0))

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

    return {
        **input_data,
        "data": output_data,
        "crossmatch_result": {
            "match_count": int(np.sum(good)),
            "total_catalog_1": len(ra1),
            "total_catalog_2": len(ra2),
            "max_sep_arcsec": max_sep,
            "mean_sep_arcsec": float(np.mean(matched_sep)) if len(matched_sep) > 0 else 0.0,
            "median_sep_arcsec": float(np.median(matched_sep)) if len(matched_sep) > 0 else 0.0,
        },
    }
