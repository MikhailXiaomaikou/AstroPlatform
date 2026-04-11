"""PARSEC isochrone fetcher — queries CMD 3.9 API for theoretical isochrones."""

import io
import logging
import re

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# CMD 3.9 API endpoint (must use HTTPS — HTTP redirects lose POST data)
CMD_URL = "https://stev.oapd.inaf.it/cgi-bin/cmd_3.9"

# Base URL for resolving relative output links
CMD_BASE_URL = "https://stev.oapd.inaf.it"

# Photometric system codes for CMD 3.7
PHOTSYS_MAP = {
    "gaia": "YBC_tab_mag_odfnew/tab_mag_gaiaEDR3.dat",
    "gaia_dr3": "YBC_tab_mag_odfnew/tab_mag_gaiaEDR3.dat",
    "sdss": "YBC_tab_mag_odfnew/tab_mag_sloan.dat",
    "sdss_ugriz": "YBC_tab_mag_odfnew/tab_mag_sloan.dat",
}


def _build_form_data(
    log_age_low: float,
    log_age_high: float,
    metallicity: float,
    photometric_system: str,
    d_log_age: float = 0.1,
) -> dict[str, str]:
    """Build the CMD 3.9 form POST data."""
    phot_key = photometric_system.lower()
    if phot_key not in PHOTSYS_MAP:
        raise ValueError(
            f"Unknown photometric system {photometric_system!r}. "
            f"Supported: {list(PHOTSYS_MAP.keys())}"
        )

    # Convert [M/H] to Z for the isoc_zlow/zupp fields
    # Z_sun = 0.0152, [M/H] = log10(Z/Z_sun)
    z_val = 0.0152 * 10 ** metallicity

    return {
        "cmd_version": "3.9",
        # Evolutionary tracks
        "track_parsec": "parsec_CAF09_v1.2S",
        "track_colibri": "parsec_CAF09_v1.2S_S_LMC_08_web",
        "track_postagb": "no",
        "n_Reimers": "0.2",
        "eta_Reimers": "0.2",
        # Photometric system
        "photsys_file": PHOTSYS_MAP[phot_key],
        "photsys_version": "YBCnewVega",
        # Dust
        "dust_sourceM": "dpmod60alox40",
        "dust_sourceC": "AMCSIC15",
        "kind_cspecmag": "aringer09",
        "kind_dust": "0",
        "kind_mag": "2",
        "kind_interp": "1",
        "kind_tpagb": "3",
        "kind_postagb": "-1",
        "kind_LPV": "4",
        # Extinction
        "extinction_coeff": "constant",
        "extinction_curve": "cardelli",
        "extinction_av": "0.0",
        # IMF
        "imf_file": "tab_imf/imf_kroupa_orig.dat",
        # Isochrone age — use log(age)
        "isoc_isagelog": "1",
        "isoc_agelow": str(10 ** log_age_low),
        "isoc_ageupp": str(10 ** log_age_high),
        "isoc_dage": "0.0",
        "isoc_lagelow": str(log_age_low),
        "isoc_lageupp": str(log_age_high),
        "isoc_dlage": str(d_log_age),
        # Isochrone metallicity — use Z (not [M/H])
        "isoc_ismetlog": "0",
        "isoc_zlow": str(z_val),
        "isoc_zupp": str(z_val),
        "isoc_dz": "0.0",
        "isoc_metlow": str(metallicity),
        "isoc_metupp": str(metallicity),
        "isoc_dmet": "0.0",
        # Output
        "output_kind": "0",
        "output_evstage": "1",
        "output_gzip": "0",
        # Luminosity function (not used but required)
        "lf_maginf": "-15",
        "lf_magsup": "20",
        "lf_deltamag": "0.5",
        "sim_mtot": "1.0e4",
        # CGI fields declarations
        ".cgifields": "output_gzip",
        # Submit
        "submit_form": "Submit",
    }


def _extract_output_url(html: str) -> str:
    """Parse the CMD HTML response to find the .dat output file URL.

    The API returns an HTML page containing a link to the generated
    data file (ending in ``.dat``).  We extract the first such link.
    """
    # Look for href links ending in .dat
    match = re.search(r'href=["\']?([^"\'>\s]*\.dat)["\']?', html)
    if match:
        url = match.group(1)
        if url.startswith("http"):
            return url
        # Resolve relative URLs like "../tmp/output123.dat"
        if url.startswith("../"):
            return CMD_BASE_URL + "/" + url.lstrip("../")
        if url.startswith("/"):
            return CMD_BASE_URL + url
        return CMD_BASE_URL + "/" + url

    # Fallback: look for plain URLs ending in .dat
    match = re.search(r'(https?://[^\s<>"\']+\.dat)', html)
    if match:
        return match.group(1)

    raise RuntimeError(
        "Could not find output .dat file URL in CMD response. "
        "The CMD API format may have changed."
    )


def _parse_isochrone_table(text: str) -> pd.DataFrame:
    """Parse the whitespace-separated CMD output into a DataFrame.

    The file has comment lines starting with ``#``.  The last comment
    line before data contains the column names.
    """
    lines = text.strip().splitlines()

    # Find the last header line (starts with #) that contains column names
    header_line = None
    data_start = 0
    for i, line in enumerate(lines):
        if line.startswith("#"):
            header_line = line
            data_start = i + 1
        else:
            break

    if header_line is None:
        raise RuntimeError("No header line found in CMD output file.")

    # Extract column names from the header line
    columns = header_line.lstrip("#").strip().split()

    # Collect data lines (skip any further comment lines in the body)
    data_lines = [
        line for line in lines[data_start:] if line.strip() and not line.startswith("#")
    ]

    if not data_lines:
        raise RuntimeError("CMD output file contains no data rows.")

    data_text = "\n".join(data_lines)
    df = pd.read_csv(
        io.StringIO(data_text),
        sep=r"\s+",
        names=columns,
        header=None,
        comment="#",
    )

    return df


def fetch_isochrone(
    log_age: float,
    metallicity: float,
    photometric_system: str = "gaia",
    timeout: float = 30.0,
) -> pd.DataFrame:
    """Fetch a PARSEC isochrone from CMD 3.7.

    Args:
        log_age: log10(age/yr), e.g. 8.0 for 100 Myr, 10.0 for 10 Gyr
        metallicity: [M/H], e.g. 0.0 for solar, -1.0 for metal-poor
        photometric_system: 'gaia' or 'sdss'
        timeout: request timeout in seconds

    Returns:
        pandas DataFrame with isochrone columns (mass, logTe, logg, magnitudes, etc.)

    Raises:
        ValueError: if photometric_system is unknown or parameters are out of range
        RuntimeError: if the CMD API response cannot be parsed
        httpx.HTTPError: on network / HTTP errors
    """
    if not (5.0 <= log_age <= 10.5):
        raise ValueError(
            f"log_age={log_age} is outside the typical PARSEC range [5.0, 10.5]."
        )
    if not (-4.0 <= metallicity <= 1.0):
        raise ValueError(
            f"metallicity={metallicity} is outside the typical PARSEC range [-4.0, 1.0]."
        )

    form_data = _build_form_data(log_age, log_age, metallicity, photometric_system)

    logger.info(
        "Fetching PARSEC isochrone: log_age=%.2f, [M/H]=%.2f, phot=%s",
        log_age,
        metallicity,
        photometric_system,
    )

    max_retries = 3
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.post(CMD_URL, data=form_data)
                resp.raise_for_status()
                output_url = _extract_output_url(resp.text)
                logger.debug("CMD output URL: %s", output_url)
                dat_resp = client.get(output_url)
                dat_resp.raise_for_status()

            df = _parse_isochrone_table(dat_resp.text)
            logger.info("Fetched isochrone with %d rows and %d columns.", len(df), len(df.columns))
            return df
        except Exception as exc:
            last_err = exc
            logger.warning("CMD API attempt %d/%d failed: %s", attempt, max_retries, exc)
            if attempt < max_retries:
                import time
                time.sleep(2 * attempt)

    raise RuntimeError(f"CMD API failed after {max_retries} retries: {last_err}")


def fetch_isochrone_grid(
    log_ages: list[float],
    metallicity: float,
    photometric_system: str = "gaia",
    timeout: float = 60.0,
) -> dict[float, pd.DataFrame]:
    """Fetch multiple isochrones at once (one request with age range).

    The CMD API supports returning multiple isochrones in a single request
    when given a range of ages.  Each isochrone is identified by the
    ``logAge`` (or equivalent) column in the output.

    Args:
        log_ages: list of log10(age/yr) values, e.g. [8.0, 9.0, 10.0]
        metallicity: [M/H], e.g. 0.0 for solar
        photometric_system: 'gaia' or 'sdss'
        timeout: request timeout in seconds

    Returns:
        dict mapping each requested log_age to its DataFrame
    """
    if not log_ages:
        return {}

    sorted_ages = sorted(log_ages)
    age_low = sorted_ages[0]
    age_high = sorted_ages[-1]

    # Determine the step size — use the minimum gap between requested ages,
    # or a small default if only one age is requested.
    if len(sorted_ages) > 1:
        gaps = [sorted_ages[i + 1] - sorted_ages[i] for i in range(len(sorted_ages) - 1)]
        d_log_age = min(gaps)
    else:
        d_log_age = 0.1

    form_data = _build_form_data(
        age_low, age_high, metallicity, photometric_system, d_log_age=d_log_age
    )

    logger.info(
        "Fetching PARSEC isochrone grid: log_age=[%.2f, %.2f], d=%.2f, [M/H]=%.2f",
        age_low,
        age_high,
        d_log_age,
        metallicity,
    )

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.post(CMD_URL, data=form_data)
        resp.raise_for_status()

        output_url = _extract_output_url(resp.text)
        logger.debug("CMD output URL: %s", output_url)

        dat_resp = client.get(output_url)
        dat_resp.raise_for_status()

    full_df = _parse_isochrone_table(dat_resp.text)

    # Split by age column — CMD output uses "logAge" or "logageyr" depending on version
    age_col = None
    for candidate in ("logAge", "logageyr", "log_age", "logage"):
        if candidate in full_df.columns:
            age_col = candidate
            break

    if age_col is None:
        logger.warning(
            "Could not find age column in CMD output (columns: %s). "
            "Returning entire table under the first requested age.",
            list(full_df.columns),
        )
        return {sorted_ages[0]: full_df}

    # Group by age and match to the nearest requested age
    result: dict[float, pd.DataFrame] = {}
    for age_val, group_df in full_df.groupby(age_col):
        # Find the closest requested age
        closest = min(sorted_ages, key=lambda a: abs(a - float(age_val)))
        if abs(float(age_val) - closest) < 0.05:  # tolerance
            result[closest] = group_df.reset_index(drop=True)

    missing = set(sorted_ages) - set(result.keys())
    if missing:
        logger.warning(
            "Some requested ages were not found in CMD output: %s",
            sorted(missing),
        )

    return result
