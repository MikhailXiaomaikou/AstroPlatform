"""Cross-wavelength anomaly detection engine.

Runs a battery of multi-wavelength consistency checks on a dossier
(optical, NIR, MIR photometry, astrometry, SED shape) and flags
discrepancies that may indicate unusual astrophysical phenomena
such as circumstellar disks, AGN activity, high-velocity stars,
or non-stellar SEDs.
"""

import logging
import math
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(val: object) -> float | None:
    """Return a finite float or None."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _get_phot(dossier: dict, section: str, band: str) -> float | None:
    """Extract a photometric magnitude from the dossier safely."""
    phot = dossier.get("photometry")
    if not isinstance(phot, dict):
        return None
    sec = phot.get(section)
    if not isinstance(sec, dict):
        return None
    return _safe_float(sec.get(band))


def _get_astrometry(dossier: dict, key: str) -> float | None:
    astro = dossier.get("astrometry")
    if not isinstance(astro, dict):
        return None
    return _safe_float(astro.get(key))


def _object_class(dossier: dict) -> str:
    """Coarse classification: 'main_sequence', 'giant', 'agn', or 'unknown'."""
    otype = (dossier.get("object_type") or "").lower()
    if any(k in otype for k in ("agn", "qso", "quasar", "seyfert", "blazar", "liner")):
        return "agn"
    if any(k in otype for k in ("giant", "rgb", "agb", "rsg", "supergiant")):
        return "giant"
    if any(k in otype for k in ("star", "main sequence", "dwarf", "ms")):
        return "main_sequence"
    # Fallback: use spectral type luminosity class if present
    spt = (dossier.get("spectral_type") or "").strip()
    if spt:
        if "III" in spt or "II" in spt or "I" in spt:
            return "giant"
        if "V" in spt:
            return "main_sequence"
    return "unknown"


# ---------------------------------------------------------------------------
# Planck blackbody (simplified) for SED fitting
# ---------------------------------------------------------------------------

# Central wavelengths in microns for common bands
_BAND_WAVELENGTH_UM: dict[str, float] = {
    # Optical (SDSS)
    "u": 0.3551,
    "g": 0.4686,
    "r": 0.6166,
    "i": 0.7480,
    "z": 0.8932,
    # Gaia
    "g_mag": 0.6230,
    "bp_mag": 0.5100,
    "rp_mag": 0.7730,
    # NIR (2MASS)
    "J": 1.235,
    "H": 1.662,
    "Ks": 2.159,
    # MIR (WISE)
    "W1": 3.368,
    "W2": 4.618,
    "W3": 12.082,
    "W4": 22.194,
}

# Zero-point fluxes in Jy for mag-to-flux conversion.
# These intentionally mix systems: SDSS entries are AB (3631 Jy), while
# Gaia/2MASS/WISE entries are Vega-like catalog zero-points.  Treat these as
# quick-look SED values unless the downstream analysis tracks the photometric
# system explicitly.
_BAND_F0_JY: dict[str, float] = {
    "u": 3631.0,
    "g": 3631.0,
    "r": 3631.0,
    "i": 3631.0,
    "z": 3631.0,
    "g_mag": 3228.75,
    "bp_mag": 3552.01,
    "rp_mag": 2554.95,
    "J": 1594.0,
    "H": 1024.0,
    "Ks": 666.7,
    "W1": 309.54,
    "W2": 171.79,
    "W3": 31.674,
    "W4": 8.363,
}


def _mag_to_flux(mag: float, band: str) -> float | None:
    """Convert magnitude to flux (Jy)."""
    f0 = _BAND_F0_JY.get(band)
    if f0 is None:
        return None
    return f0 * 10.0 ** (-0.4 * mag)


def _planck_flux_ratio(wavelength_um: float, T: float) -> float:
    """Planck function B_nu in arbitrary units (proportional).

    Returns relative spectral radiance at the given wavelength for
    temperature T.  We only need the *shape*, not absolute calibration,
    so constants are dropped.
    """
    h = 6.626e-34
    c = 3.0e8
    k = 1.381e-23
    lam_m = wavelength_um * 1.0e-6
    nu = c / lam_m
    x = h * nu / (k * T)
    if x > 500:
        return 0.0
    return nu**3 / (np.exp(x) - 1.0)


def _fit_blackbody(
    wavelengths_um: list[float],
    fluxes: list[float],
    flux_errors: list[float] | None = None,
) -> tuple[float, float, int, float]:
    """Fit a single-temperature blackbody to observed SED points.

    Uses a two-stage grid search (coarse then fine) for improved
    temperature precision and reports a temperature uncertainty
    derived from the delta-chi2 = 1 interval.

    Returns (best_T, chi2, dof, T_err).
    """
    wavelengths_um_arr = np.array(wavelengths_um)
    fluxes_arr = np.array(fluxes)
    n = len(fluxes_arr)

    # --- Determine per-point uncertainties ---
    if flux_errors is not None and len(flux_errors) == len(fluxes_arr):
        sigma = np.asarray(flux_errors, dtype=float)
        sigma = np.where(sigma > 0, sigma, 0.1 * fluxes_arr)  # fallback for zero errors
    else:
        sigma = 0.1 * fluxes_arr  # 10% relative error fallback
    sigma = np.where(sigma > 0, sigma, 1.0e-30)

    def _chi2_at(T: float) -> float:
        model = np.array([_planck_flux_ratio(w, T) for w in wavelengths_um_arr])
        if model.max() == 0:
            return np.inf
        scale = np.dot(fluxes_arr, model) / np.dot(model, model)
        residuals = fluxes_arr - scale * model
        return float(np.sum((residuals / sigma) ** 2))

    # --- Stage 1: coarse grid search ---
    coarse_temps = np.arange(1000, 50001, 500)
    best_chi2 = np.inf
    best_T_coarse = 5000.0
    for T in coarse_temps:
        c2 = _chi2_at(T)
        if c2 < best_chi2:
            best_chi2 = c2
            best_T_coarse = T

    # --- Stage 2: fine grid search around coarse best ---
    fine_lo = max(500, best_T_coarse - 1500)
    fine_hi = best_T_coarse + 1500
    fine_temps = np.arange(fine_lo, fine_hi, 100)
    fine_chi2s = [_chi2_at(T) for T in fine_temps]

    best_T = best_T_coarse
    for T, c2 in zip(fine_temps, fine_chi2s):
        if c2 < best_chi2:
            best_chi2 = c2
            best_T = T

    # --- Temperature uncertainty (delta-chi2 < 1 interval) ---
    chi2_threshold = best_chi2 + 1.0
    valid_temps = [T for T, c in zip(fine_temps, fine_chi2s) if c <= chi2_threshold]
    T_err = (max(valid_temps) - min(valid_temps)) / 2 if len(valid_temps) > 1 else 500.0

    dof = max(n - 2, 1)  # 2 free params: T and scale
    return float(best_T), float(best_chi2), dof, float(T_err)


# ---------------------------------------------------------------------------
# Individual anomaly checks
# ---------------------------------------------------------------------------


def _check_ir_excess(dossier: dict) -> dict[str, Any]:
    """Check a: IR excess via W1-W2 color."""
    w1 = _get_phot(dossier, "mir", "W1")
    w2 = _get_phot(dossier, "mir", "W2")

    if w1 is None or w2 is None:
        return {
            "check": "ir_excess",
            "status": "SKIPPED",
            "observed": None,
            "expected": None,
            "significance": None,
            "possible_causes": None,
            "recommended_followup": None,
        }

    w1_w2 = w1 - w2
    obj_class = _object_class(dossier)

    if obj_class == "giant":
        threshold = 0.5
        expected_label = "W1-W2 < 0.5 for giants"
    elif obj_class == "agn":
        threshold = 0.8
        expected_label = "W1-W2 < 0.8 for AGN"
    else:
        threshold = 0.3
        expected_label = "W1-W2 < 0.3 for main sequence"

    excess = w1_w2 - threshold
    # Typical WISE photometric error ~0.03-0.05 mag; use 0.05 as 1-sigma
    sigma_w1w2 = 0.05
    significance = excess / sigma_w1w2 if excess > 0 else None

    if excess > 0.5:
        return {
            "check": "ir_excess",
            "status": "ANOMALY",
            "observed": f"W1-W2 = {w1_w2:.2f}",
            "expected": expected_label,
            "significance": f"{significance:.1f} sigma" if significance else None,
            "possible_causes": [
                "circumstellar disk",
                "dusty envelope",
                "young stellar object (YSO)",
                "AGN contamination",
            ],
            "recommended_followup": (
                "Mid-IR spectroscopy to distinguish thermal dust emission "
                "from non-thermal AGN contribution; check for variability."
            ),
        }
    return {
        "check": "ir_excess",
        "status": "NORMAL",
        "observed": f"W1-W2 = {w1_w2:.2f}",
        "expected": expected_label,
        "significance": None,
        "possible_causes": None,
        "recommended_followup": None,
    }


def _check_xray_optical(dossier: dict) -> dict[str, Any]:
    """Check b: X-ray to optical flux ratio."""
    raw = dossier.get("_raw", {})
    xray_data = raw.get("xray") or raw.get("chandra") or raw.get("xmm")

    # Also check top-level keys for x-ray flux
    fx = None
    if isinstance(xray_data, dict):
        fx = _safe_float(xray_data.get("flux")) or _safe_float(
            xray_data.get("flux_0.5_7")
        )
    if fx is None:
        fx = _safe_float(dossier.get("xray_flux"))

    if fx is None:
        return {
            "check": "xray_optical_ratio",
            "status": "SKIPPED",
            "observed": None,
            "expected": None,
            "significance": None,
            "possible_causes": None,
            "recommended_followup": None,
        }

    # Optical flux from r-band or g-band
    r_mag = _get_phot(dossier, "optical", "r")
    if r_mag is None:
        r_mag = _get_phot(dossier, "optical", "g")
    if r_mag is None:
        return {
            "check": "xray_optical_ratio",
            "status": "SKIPPED",
            "observed": "X-ray flux present but no optical magnitude",
            "expected": None,
            "significance": None,
            "possible_causes": None,
            "recommended_followup": None,
        }

    # Convert optical mag to flux (erg/s/cm^2 approx)
    f_opt = 3.631e-20 * 10 ** (-0.4 * r_mag) * 1e23  # rough erg/s/cm^2/Hz
    if f_opt <= 0:
        return {
            "check": "xray_optical_ratio",
            "status": "SKIPPED",
            "observed": None,
            "expected": None,
            "significance": None,
            "possible_causes": None,
            "recommended_followup": None,
        }

    log_ratio = math.log10(fx / f_opt) if f_opt > 0 else None
    if log_ratio is None:
        return {
            "check": "xray_optical_ratio",
            "status": "SKIPPED",
            "observed": None,
            "expected": None,
            "significance": None,
            "possible_causes": None,
            "recommended_followup": None,
        }

    obj_class = _object_class(dossier)

    if obj_class == "agn":
        lo, hi = -1.0, 1.0
        expected_label = "log(Fx/Fopt) in [-1, +1] for AGN"
    elif "active" in (dossier.get("object_type") or "").lower():
        lo, hi = -3.0, -1.0
        expected_label = "log(Fx/Fopt) in [-3, -1] for active stars"
    else:
        lo, hi = -4.0, -2.0
        expected_label = "log(Fx/Fopt) in [-4, -2] for normal stars"

    if log_ratio < lo or log_ratio > hi:
        causes = []
        if log_ratio > hi:
            causes = [
                "X-ray binary",
                "coronal activity",
                "AGN",
                "magnetic cataclysmic variable",
            ]
        else:
            causes = [
                "X-ray absorption",
                "heavily reddened source",
                "misidentified counterpart",
            ]
        return {
            "check": "xray_optical_ratio",
            "status": "ANOMALY",
            "observed": f"log(Fx/Fopt) = {log_ratio:.2f}",
            "expected": expected_label,
            "significance": None,
            "possible_causes": causes,
            "recommended_followup": (
                "Obtain X-ray spectrum to constrain emission mechanism; "
                "check for optical variability consistent with accretion."
            ),
        }
    return {
        "check": "xray_optical_ratio",
        "status": "NORMAL",
        "observed": f"log(Fx/Fopt) = {log_ratio:.2f}",
        "expected": expected_label,
        "significance": None,
        "possible_causes": None,
        "recommended_followup": None,
    }


def _check_astrometric_anomaly(dossier: dict) -> dict[str, Any]:
    """Check c: astrometric anomalies (parallax, proper motion)."""
    parallax = _get_astrometry(dossier, "parallax_mas")
    parallax_err = _get_astrometry(dossier, "parallax_error_mas")
    pm_ra = _get_astrometry(dossier, "pm_ra")
    pm_dec = _get_astrometry(dossier, "pm_dec")

    # Also try Gaia g_mag from _raw
    g_mag = None
    raw_gaia = (dossier.get("_raw") or {}).get("gaia", {})
    if isinstance(raw_gaia, dict):
        g_mag = _safe_float(raw_gaia.get("g_mag"))
    if g_mag is None:
        g_mag = _safe_float(dossier.get("g_mag"))

    if parallax is None and pm_ra is None:
        return {
            "check": "astrometric_anomaly",
            "status": "SKIPPED",
            "observed": None,
            "expected": None,
            "significance": None,
            "possible_causes": None,
            "recommended_followup": None,
        }

    anomalies: list[str] = []
    observed_parts: list[str] = []
    causes: list[str] = []

    # Negative parallax for bright object
    if parallax is not None and parallax < 0 and g_mag is not None and g_mag < 15:
        anomalies.append("negative parallax for bright object")
        observed_parts.append(
            f"parallax = {parallax:.3f} mas (negative, G = {g_mag:.1f})"
        )
        causes.extend(
            [
                "poor astrometric solution",
                "unresolved binary",
                "distant but bright giant",
            ]
        )

    # Large fractional parallax error for bright object
    if (
        parallax is not None
        and parallax_err is not None
        and parallax != 0
        and abs(parallax_err / parallax) > 0.5
        and g_mag is not None
        and g_mag < 15
    ):
        frac = abs(parallax_err / parallax)
        anomalies.append("large fractional parallax error")
        observed_parts.append(f"parallax_error/parallax = {frac:.2f} (G = {g_mag:.1f})")
        causes.extend(["astrometric binary", "partially resolved companion"])

    # High proper motion for brightness (proxy for high-velocity star)
    if pm_ra is not None and pm_dec is not None:
        total_pm = math.sqrt(pm_ra**2 + pm_dec**2)
        observed_parts.append(f"total PM = {total_pm:.1f} mas/yr")
        # Heuristic: bright + very high PM => nearby high-velocity star
        if total_pm > 200 and (g_mag is None or g_mag < 15):
            anomalies.append("unusually high proper motion")
            causes.extend(
                ["high-velocity star", "halo star", "hypervelocity star candidate"]
            )

    if anomalies:
        return {
            "check": "astrometric_anomaly",
            "status": "ANOMALY",
            "observed": "; ".join(observed_parts),
            "expected": "Well-determined parallax and moderate proper motion for brightness",
            "significance": None,
            "possible_causes": list(
                dict.fromkeys(causes)
            ),  # deduplicate, preserve order
            "recommended_followup": (
                "Multi-epoch astrometry to confirm proper motion; "
                "radial velocity measurement to compute 3-D kinematics."
            ),
        }

    return {
        "check": "astrometric_anomaly",
        "status": "NORMAL",
        "observed": "; ".join(observed_parts) if observed_parts else None,
        "expected": "Well-determined parallax and moderate proper motion for brightness",
        "significance": None,
        "possible_causes": None,
        "recommended_followup": None,
    }


def _check_color_color(dossier: dict) -> dict[str, Any]:
    """Check d: optical color-color consistency."""
    g = _get_phot(dossier, "optical", "g")
    r = _get_phot(dossier, "optical", "r")
    i = _get_phot(dossier, "optical", "i")

    if g is None or r is None:
        return {
            "check": "color_color",
            "status": "SKIPPED",
            "observed": None,
            "expected": None,
            "significance": None,
            "possible_causes": None,
            "recommended_followup": None,
        }

    g_r = g - r
    r_i = (r - i) if i is not None else None

    observed_str = f"g-r = {g_r:.2f}"
    if r_i is not None:
        observed_str += f", r-i = {r_i:.2f}"

    if g_r > 2.0 or g_r < -0.5:
        causes = []
        if g_r > 2.0:
            causes = [
                "very red object",
                "heavily reddened by dust",
                "cool M/L dwarf",
                "high-redshift galaxy",
            ]
        else:
            causes = [
                "very blue object",
                "hot white dwarf",
                "quasar with strong UV excess",
                "photometric error",
            ]
        return {
            "check": "color_color",
            "status": "ANOMALY",
            "observed": observed_str,
            "expected": "-0.5 < g-r < 2.0 for typical stellar/galactic sources",
            "significance": None,
            "possible_causes": causes,
            "recommended_followup": (
                "Verify photometry in independent survey; "
                "obtain spectrum to classify the object."
            ),
        }

    return {
        "check": "color_color",
        "status": "NORMAL",
        "observed": observed_str,
        "expected": "-0.5 < g-r < 2.0 for typical stellar/galactic sources",
        "significance": None,
        "possible_causes": None,
        "recommended_followup": None,
    }


def _check_sed_shape(dossier: dict) -> dict[str, Any]:
    """Check e: SED shape vs. single-temperature blackbody."""
    # Collect all available photometric bands
    bands_data: list[tuple[str, float, float]] = []  # (band, wavelength_um, flux)

    phot = dossier.get("photometry", {})
    for section in ("optical", "nir", "mir"):
        sec = phot.get(section, {})
        if not isinstance(sec, dict):
            continue
        for band, mag in sec.items():
            mag_f = _safe_float(mag)
            if mag_f is None:
                continue
            if band not in _BAND_WAVELENGTH_UM:
                continue
            flux = _mag_to_flux(mag_f, band)
            if flux is not None and flux > 0:
                bands_data.append((band, _BAND_WAVELENGTH_UM[band], flux))

    if len(bands_data) < 4:
        return {
            "check": "sed_shape",
            "status": "SKIPPED",
            "observed": f"Only {len(bands_data)} photometric band(s) available (need >= 4)",
            "expected": None,
            "significance": None,
            "possible_causes": None,
            "recommended_followup": None,
        }

    wavelengths = [b[1] for b in bands_data]
    fluxes = [b[2] for b in bands_data]
    band_names = [b[0] for b in bands_data]

    best_T, chi2, dof, T_err = _fit_blackbody(wavelengths, fluxes)
    chi2_dof = chi2 / dof

    observed_str = (
        f"Blackbody fit: T = {best_T:.0f} +/- {T_err:.0f} K, chi2/dof = {chi2_dof:.1f} "
        f"({len(bands_data)} bands: {', '.join(band_names)})"
    )

    if chi2_dof > 5.0:
        return {
            "check": "sed_shape",
            "status": "ANOMALY",
            "observed": observed_str,
            "expected": "chi2/dof < 5 for single-temperature blackbody",
            "significance": f"chi2/dof = {chi2_dof:.1f}",
            "T_err": T_err,
            "possible_causes": [
                "non-stellar SED (composite source)",
                "AGN power-law continuum",
                "significant dust reddening or circumstellar emission",
                "photometric contamination from nearby source",
            ],
            "recommended_followup": (
                "Multi-component SED fitting; spectroscopy to determine "
                "physical nature of the excess/deficit."
            ),
        }

    return {
        "check": "sed_shape",
        "status": "NORMAL",
        "observed": observed_str,
        "expected": "chi2/dof < 5 for single-temperature blackbody",
        "significance": f"chi2/dof = {chi2_dof:.1f}",
        "T_err": T_err,
        "possible_causes": None,
        "recommended_followup": None,
    }


def _check_variability(dossier: dict) -> dict[str, Any]:
    """Check f: multi-epoch magnitude variability."""
    multi_epoch = dossier.get("multi_epoch") or dossier.get("light_curve")
    if not isinstance(multi_epoch, (list, dict)):
        return {
            "check": "variability",
            "status": "SKIPPED",
            "observed": "No multi-epoch data in dossier",
            "expected": None,
            "significance": None,
            "possible_causes": None,
            "recommended_followup": None,
        }

    # Accept either a list of mag values or a dict with a "magnitudes" key
    mags: list[float] = []
    if isinstance(multi_epoch, list):
        for entry in multi_epoch:
            if isinstance(entry, dict):
                m = _safe_float(entry.get("mag") or entry.get("magnitude"))
                if m is not None:
                    mags.append(m)
            else:
                m = _safe_float(entry)
                if m is not None:
                    mags.append(m)
    elif isinstance(multi_epoch, dict):
        raw_mags = multi_epoch.get("magnitudes") or multi_epoch.get("mags") or []
        for val in raw_mags:
            m = _safe_float(val)
            if m is not None:
                mags.append(m)

    if len(mags) < 2:
        return {
            "check": "variability",
            "status": "SKIPPED",
            "observed": f"Only {len(mags)} epoch(s) available",
            "expected": None,
            "significance": None,
            "possible_causes": None,
            "recommended_followup": None,
        }

    mag_range = max(mags) - min(mags)
    mag_std = float(np.std(mags))

    observed_str = f"Magnitude range = {mag_range:.2f} mag over {len(mags)} epochs (std = {mag_std:.3f})"

    if mag_range > 0.5:
        return {
            "check": "variability",
            "status": "ANOMALY",
            "observed": observed_str,
            "expected": "Magnitude variation < 0.5 mag for non-variable sources",
            "significance": f"Range {mag_range:.2f} mag",
            "possible_causes": [
                "intrinsic variable star (Cepheid, RR Lyrae, Mira)",
                "eclipsing binary",
                "eruptive transient (nova, supernova)",
                "AGN variability",
            ],
            "recommended_followup": (
                "Time-series photometry to determine period and amplitude; "
                "spectroscopy to classify variability type."
            ),
        }

    return {
        "check": "variability",
        "status": "NORMAL",
        "observed": observed_str,
        "expected": "Magnitude variation < 0.5 mag for non-variable sources",
        "significance": None,
        "possible_causes": None,
        "recommended_followup": None,
    }


# ---------------------------------------------------------------------------
# Briefing template
# ---------------------------------------------------------------------------


def _generate_briefing(results: list[dict[str, Any]]) -> str:
    """Build a template-based plain-text briefing of all check results."""
    anomalies = [r for r in results if r["status"] == "ANOMALY"]
    skipped = [r for r in results if r["status"] == "SKIPPED"]
    normal = [r for r in results if r["status"] == "NORMAL"]

    if not anomalies:
        summary = "No multi-wavelength discrepancies detected."
        if skipped:
            skipped_names = ", ".join(r["check"] for r in skipped)
            summary += f" ({len(skipped)} check(s) skipped due to missing data: {skipped_names}.)"
        return summary

    parts = [
        f"{len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'} detected:\n"
    ]
    for i, a in enumerate(anomalies, 1):
        label = a["check"].replace("_", " ").title()
        parts.append(f"  {i}. {label}: {a.get('observed', 'N/A')}")
        if a.get("possible_causes"):
            parts.append(f"     Possible causes: {', '.join(a['possible_causes'])}")
        if a.get("recommended_followup"):
            parts.append(f"     Follow-up: {a['recommended_followup']}")

    if normal:
        parts.append(f"\n{len(normal)} check(s) passed normally.")
    if skipped:
        skipped_names = ", ".join(r["check"] for r in skipped)
        parts.append(f"{len(skipped)} check(s) skipped: {skipped_names}.")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def cross_wavelength_analysis(
    ra: float,
    dec: float,
    dossier: dict | None = None,
) -> dict:
    """Run cross-wavelength anomaly detection checks on a sky position.

    Parameters
    ----------
    ra : float
        Right ascension in degrees.
    dec : float
        Declination in degrees.
    dossier : dict, optional
        Pre-generated dossier from ``generate_dossier``.  If *None*, the
        dossier is fetched automatically.

    Returns
    -------
    dict
        Analysis results with ``anomalies_found``, ``checks_run``,
        ``results`` list, and ``briefing`` text.
    """
    if dossier is None:
        try:
            from app.services.dossier_generator import generate_dossier

            dossier = await generate_dossier(ra, dec)
        except Exception as exc:
            logger.error(
                "Failed to generate dossier for (%.4f, %.4f): %s", ra, dec, exc
            )
            return {
                "anomalies_found": 0,
                "checks_run": 0,
                "results": [],
                "briefing": f"Analysis aborted: could not generate dossier ({exc})",
            }

    # Run all checks
    results: list[dict[str, Any]] = [
        _check_ir_excess(dossier),
        _check_xray_optical(dossier),
        _check_astrometric_anomaly(dossier),
        _check_color_color(dossier),
        _check_sed_shape(dossier),
        _check_variability(dossier),
    ]

    checks_run = sum(1 for r in results if r["status"] != "SKIPPED")
    anomalies_found = sum(1 for r in results if r["status"] == "ANOMALY")

    briefing = _generate_briefing(results)

    logger.info(
        "Cross-wavelength analysis (%.4f, %.4f): %d checks run, %d anomalies",
        ra,
        dec,
        checks_run,
        anomalies_found,
    )

    return {
        "anomalies_found": anomalies_found,
        "checks_run": checks_run,
        "results": results,
        "briefing": briefing,
    }
