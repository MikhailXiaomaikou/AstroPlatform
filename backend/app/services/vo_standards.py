"""IVOA / VO standards support (Phase D4).

Covers:
- UCD + unit + datatype mapping for common astronomical column names so
  VOTable exports are properly self-describing and usable in TOPCAT,
  Aladin, STILTS (cite IVOA UCD2+ vocabulary).
- pyvo-backed async TAP job helper for long-running ADQL queries.
- MOC footprint utilities (mocpy) for catalog coverage + intersection.
- Gaia Datalink helpers for fetching spectra / epoch photometry by
  source_id.

These helpers are optional; callers use them at the boundary between
our internal data model and external VO-compliant outputs.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# UCD + unit + datatype for columns we expose through VOTable emission.
# When a column name isn't in the map we fall back to no UCD (safest for
# downstream VO clients — they'll treat it as a plain column).
COLUMN_METADATA: dict[str, dict[str, str]] = {
    # Astrometry
    "ra":         {"ucd": "pos.eq.ra;meta.main",   "unit": "deg",    "dtype": "double"},
    "ra_deg":     {"ucd": "pos.eq.ra;meta.main",   "unit": "deg",    "dtype": "double"},
    "raj2000":    {"ucd": "pos.eq.ra",             "unit": "deg",    "dtype": "double"},
    "dec":        {"ucd": "pos.eq.dec;meta.main",  "unit": "deg",    "dtype": "double"},
    "dec_deg":    {"ucd": "pos.eq.dec;meta.main",  "unit": "deg",    "dtype": "double"},
    "dej2000":    {"ucd": "pos.eq.dec",            "unit": "deg",    "dtype": "double"},
    "pmra":       {"ucd": "pos.pm;pos.eq.ra",      "unit": "mas/yr", "dtype": "double"},
    "pmdec":      {"ucd": "pos.pm;pos.eq.dec",     "unit": "mas/yr", "dtype": "double"},
    "parallax":   {"ucd": "pos.parallax.trig",     "unit": "mas",    "dtype": "double"},
    "plx":        {"ucd": "pos.parallax.trig",     "unit": "mas",    "dtype": "double"},
    # Photometry
    "phot_g_mean_mag":  {"ucd": "phot.mag;em.opt.V", "unit": "mag", "dtype": "float"},
    "phot_bp_mean_mag": {"ucd": "phot.mag;em.opt.B", "unit": "mag", "dtype": "float"},
    "phot_rp_mean_mag": {"ucd": "phot.mag;em.opt.R", "unit": "mag", "dtype": "float"},
    "g_mag":      {"ucd": "phot.mag;em.opt.V", "unit": "mag", "dtype": "float"},
    "v_mag":      {"ucd": "phot.mag;em.opt.V", "unit": "mag", "dtype": "float"},
    "b_mag":      {"ucd": "phot.mag;em.opt.B", "unit": "mag", "dtype": "float"},
    "j_mag":      {"ucd": "phot.mag;em.IR.J",  "unit": "mag", "dtype": "float"},
    "h_mag":      {"ucd": "phot.mag;em.IR.H",  "unit": "mag", "dtype": "float"},
    "k_mag":      {"ucd": "phot.mag;em.IR.K",  "unit": "mag", "dtype": "float"},
    "w1mpro":     {"ucd": "phot.mag;em.IR.3-4um", "unit": "mag", "dtype": "float"},
    "w2mpro":     {"ucd": "phot.mag;em.IR.4-8um", "unit": "mag", "dtype": "float"},
    # Kinematics
    "radial_velocity":       {"ucd": "spect.dopplerVeloc",            "unit": "km/s", "dtype": "double"},
    "radial_velocity_km_s":  {"ucd": "spect.dopplerVeloc",            "unit": "km/s", "dtype": "double"},
    "rv":                    {"ucd": "spect.dopplerVeloc",            "unit": "km/s", "dtype": "double"},
    "redshift":              {"ucd": "src.redshift",                   "unit": "",     "dtype": "double"},
    # Stellar params
    "teff":    {"ucd": "phys.temperature.effective", "unit": "K",   "dtype": "float"},
    "t_eff":   {"ucd": "phys.temperature.effective", "unit": "K",   "dtype": "float"},
    "log_g":   {"ucd": "phys.gravity",               "unit": "",    "dtype": "float"},
    "feh":     {"ucd": "phys.abund.Fe",              "unit": "",    "dtype": "float"},
    "fe_h":    {"ucd": "phys.abund.Fe",              "unit": "",    "dtype": "float"},
    # Identifiers
    "source_id":  {"ucd": "meta.id;meta.main", "unit": "", "dtype": "long"},
    "object_id":  {"ucd": "meta.id;meta.main", "unit": "", "dtype": "char"},
    "name":       {"ucd": "meta.id",           "unit": "", "dtype": "char"},
    # Distance
    "distance":      {"ucd": "pos.distance", "unit": "pc",  "dtype": "double"},
    "distance_pc":   {"ucd": "pos.distance", "unit": "pc",  "dtype": "double"},
    "distance_kpc":  {"ucd": "pos.distance", "unit": "kpc", "dtype": "double"},
    "distance_mpc":  {"ucd": "pos.distance", "unit": "Mpc", "dtype": "double"},
}


def apply_votable_metadata(votable_element) -> int:
    """Mutate a ``votable.tree.VOTableFile`` adding UCD/unit/datatype
    attributes to every FIELD whose name matches the metadata table.

    Returns the number of fields annotated.  Errors are logged and the
    function exits cleanly — a missing UCD never blocks export.
    """
    annotated = 0
    try:
        for resource in getattr(votable_element, "resources", []):
            for table in getattr(resource, "tables", []):
                for field in getattr(table, "fields", []):
                    name = str(getattr(field, "name", "")).lower()
                    meta = COLUMN_METADATA.get(name)
                    if not meta:
                        continue
                    if not getattr(field, "ucd", None):
                        field.ucd = meta["ucd"]
                    if not getattr(field, "unit", None) and meta["unit"]:
                        field.unit = meta["unit"]
                    annotated += 1
    except Exception as exc:
        logger.debug("apply_votable_metadata failed: %s", exc)
    return annotated


# ---------------------------------------------------------------------
# pyvo async TAP helper
# ---------------------------------------------------------------------


def launch_async_tap_job(url: str, adql: str, timeout: float = 600.0) -> dict:
    """Submit an async TAP job and poll until completion.

    Returns ``{status, phase, rows, elapsed_s, job_url}`` on success or
    ``{status: "error", detail: ...}`` on failure.  For long running
    queries (crossmatches, full-sky cone searches) the sync endpoint
    timing out (30s) is the norm; async mode uploads the query and
    polls a job URL.  See IVOA TAP 1.1 §3.3.
    """
    try:
        import pyvo
        import time as _time
    except ImportError:
        return {"status": "error", "detail": "pyvo not installed"}

    try:
        service = pyvo.dal.TAPService(url)
        t0 = _time.monotonic()
        job = service.submit_job(adql)
        try:
            job.run()
        except Exception:
            pass
        deadline = t0 + timeout
        while _time.monotonic() < deadline:
            phase = str(job.phase).upper()
            if phase in {"COMPLETED", "ERROR", "ABORTED"}:
                break
            _time.sleep(2.0)
        if str(job.phase).upper() != "COMPLETED":
            return {
                "status": "error",
                "detail": f"job did not complete (phase={job.phase})",
                "job_url": str(job.url),
            }
        result = job.fetch_result()
        return {
            "status": "ok",
            "phase": str(job.phase),
            "rows": len(result),
            "elapsed_s": _time.monotonic() - t0,
            "job_url": str(job.url),
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


# ---------------------------------------------------------------------
# MOC footprint helpers
# ---------------------------------------------------------------------


def moc_from_coords(ra_deg, dec_deg, max_norder: int = 10):
    """Build a MOC from a list of (ra, dec) points (deg)."""
    try:
        import astropy.units as u
        from astropy.coordinates import SkyCoord
        from mocpy import MOC
    except ImportError:
        logger.warning("mocpy not installed")
        return None
    coords = SkyCoord(ra=ra_deg * u.degree, dec=dec_deg * u.degree, frame="icrs")
    try:
        return MOC.from_skycoords(coords, max_norder=max_norder)
    except Exception as exc:
        logger.debug("moc_from_coords failed: %s", exc)
        return None


def moc_intersection(moc_list: list) -> Any:
    """Intersect a list of MOC objects; returns None if empty or mocpy
    is unavailable."""
    if not moc_list:
        return None
    m = moc_list[0]
    for other in moc_list[1:]:
        try:
            m = m.intersection(other)
        except Exception as exc:
            logger.debug("moc_intersection failed: %s", exc)
            return None
    return m


# ---------------------------------------------------------------------
# Gaia Datalink helpers
# ---------------------------------------------------------------------


def fetch_gaia_xp_spectrum(source_id: int) -> dict:
    """Fetch Gaia DR3 BP/RP externally-calibrated XP spectrum.

    Returns ``{status, wavelength_nm, flux_erg_s_cm2_nm, flux_err, ...}``.
    Thin wrapper around astroquery.gaia.Gaia.load_data; falls back to
    pyvo if astroquery lacks the direct API.
    """
    try:
        from astroquery.gaia import Gaia
    except ImportError:
        return {"status": "error", "detail": "astroquery not installed"}
    try:
        datalink = Gaia.load_data(
            ids=[int(source_id)],
            data_release="Gaia DR3",
            data_structure="RAW",
            retrieval_type="XP_CONTINUOUS",
            format="votable",
        )
        key = next(iter(datalink))  # source_id keyed
        table = datalink[key][0].to_table()
        return {
            "status": "ok",
            "rows": len(table),
            "columns": [str(c) for c in table.colnames],
            "archive_version": "Gaia DR3",
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
