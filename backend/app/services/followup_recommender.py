"""Follow-up observation recommendation engine for transient alerts.

Generates prioritised follow-up observation plans based on transient
classification, confidence, and available ancillary data (dossier,
photometry, host-galaxy redshift, etc.).

The core function ``generate_followup`` consumes an alert dict together
with optional classification and dossier outputs from the transient
classifier and dossier generator, then returns a structured recommendation
dict containing urgency level, ordered observation recommendations, and
a scientific rationale.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Facility catalogues ──

SPECTROSCOPY_FACILITIES = {
    "4m": ["Gemini/GMOS", "VLT/FORS2", "Keck/LRIS"],
    "2m": ["NTT/EFOSC2", "NOT/ALFOSC"],
    "8m": ["VLT/X-shooter", "Keck/DEIMOS", "Gemini/GMOS"],
}

PHOTOMETRY_FACILITIES = [
    "Las Cumbres Observatory (LCO) 1m network",
    "Swift/UVOT",
    "ZTF forced photometry",
    "ATLAS forced photometry",
]

XRAY_FACILITIES = ["Swift/XRT", "NICER", "XMM-Newton"]
UV_FACILITIES = ["Swift/UVOT", "HST/COS"]

# ── Exposure-time estimation (inline fallback) ──

# Simplified zero-point + telescope parameters for quick estimates when
# the full ``exposure_time_estimate`` function is unavailable.
_SIMPLE_TELESCOPE_AREA_CM2: dict[str, float] = {
    "4m": math.pi * (200.0**2),
    "8m": math.pi * (400.0**2),
    "2m": math.pi * (100.0**2),
}

_VBAND_FLUX0 = 3.64e-9  # erg/cm2/s/A  (Vega)
_QE = 0.70


def _quick_exposure_seconds(
    magnitude: float,
    snr_target: float = 20.0,
    telescope_class: str = "4m",
) -> float:
    """Quick CCD-equation exposure estimate for spectroscopy.

    Falls back to simple scaling when ``astro_analysis.exposure_time_estimate``
    is not importable.  Returns exposure time in seconds.
    """
    area = _SIMPLE_TELESCOPE_AREA_CM2.get(
        telescope_class, _SIMPLE_TELESCOPE_AREA_CM2["4m"]
    )
    # Approximate photon rate (photons/s on detector)
    flux = _VBAND_FLUX0 * 10.0 ** (-0.4 * magnitude)
    dlambda = 890.0  # V-band effective width in Angstroms
    rate = flux * dlambda * area * _QE

    if rate <= 0:
        return 7200.0  # fallback 2 hours

    # Sky-dominated regime approximation: t ~ (SNR/rate)^2 * (rate + sky)
    sky_rate = flux * 10.0 ** (-0.4 * (21.8 - magnitude)) * dlambda * area * _QE * 3.0
    t = (snr_target**2) * (rate + sky_rate) / (rate**2)
    return max(float(t), 1.0)


def _estimate_exposure(
    magnitude: float,
    snr_target: float = 20.0,
    telescope: str = "gemini",
    filter_band: str = "V",
) -> dict[str, Any]:
    """Try the full ETC from astro_analysis, falling back to inline estimate."""
    try:
        from app.services.astro_analysis import exposure_time_estimate

        result = exposure_time_estimate(
            target_mag=magnitude,
            snr_target=snr_target,
            telescope=telescope,
            filter_band=filter_band,
        )
        return result
    except Exception:
        t = _quick_exposure_seconds(magnitude, snr_target)
        return {
            "exposure_time_seconds": round(t),
            "snr_achieved": snr_target,
            "notes": ["Estimated via simplified ETC."],
        }


def _compute_observability(ra: float, dec: float) -> dict[str, Any]:
    """Compute basic observability from standard observatories.

    Returns a summary dict with transit altitude and hours observable from
    the best-suited major observatory.
    """
    observatories = ["paranal", "mauna_kea", "la_palma"]
    best: dict[str, Any] = {
        "observatory": None,
        "transit_altitude_deg": 0.0,
        "hours_observable": 0.0,
        "is_observable": False,
    }

    try:
        from app.services.astro_analysis import target_visibility

        for obs in observatories:
            try:
                vis = target_visibility(ra, dec, observatory=obs)
                alt = vis.get("transit_altitude_deg", 0.0) or 0.0
                hours = vis.get("hours_observable", 0.0) or 0.0
                if hours > best["hours_observable"]:
                    best = {
                        "observatory": obs,
                        "transit_altitude_deg": round(alt, 1),
                        "hours_observable": round(hours, 1),
                        "is_observable": alt > 30.0,
                        "rise_utc": vis.get("rise_time_utc"),
                        "set_utc": vis.get("set_time_utc"),
                        "transit_utc": vis.get("transit_time_utc"),
                    }
            except Exception as exc:
                logger.debug("Visibility check for %s failed: %s", obs, exc)
    except ImportError:
        # Rough declination-based guess when astropy is unavailable
        if -70 <= dec <= 90:
            best["observatory"] = "mauna_kea" if dec > -30 else "paranal"
            best["transit_altitude_deg"] = round(
                90.0 - abs(dec - (19.82 if dec > -30 else -24.63)), 1
            )
            best["is_observable"] = best["transit_altitude_deg"] > 30.0

    return best


def _has_spectroscopic_redshift(dossier: dict | None) -> bool:
    """Check whether the dossier indicates a confirmed spectroscopic redshift."""
    if not dossier:
        return False
    redshift = dossier.get("redshift", {})
    if isinstance(redshift, dict):
        return (
            redshift.get("value") is not None
            and redshift.get("source") == "spectroscopic"
        )
    return False


def _host_has_redshift(dossier: dict | None) -> bool:
    """Check whether host galaxy already has a measured redshift."""
    if not dossier:
        return False
    host = dossier.get("host_galaxy", {})
    if isinstance(host, dict):
        return host.get("redshift") is not None
    return False


def _days_from_discovery(alert: dict) -> float | None:
    """Compute days since discovery from the alert's discovery_date field."""
    disc = alert.get("discovery_date")
    if disc is None:
        return None
    if isinstance(disc, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                disc = datetime.strptime(disc, fmt)
                if disc.tzinfo is None:
                    disc = disc.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        else:
            return None
    if isinstance(disc, datetime):
        if disc.tzinfo is None:
            disc = disc.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - disc).total_seconds() / 86400.0
    return None


# ── Main recommendation logic ──


async def generate_followup(
    alert: dict,
    dossier: dict | None = None,
    classification: dict | None = None,
) -> dict:
    """Generate follow-up observation recommendations for a transient alert.

    Parameters
    ----------
    alert : dict
        Alert packet with fields: ra, dec, magnitude, classification,
        classification_confidence, source_id, discovery_date, redshift,
        host_galaxy, raw_data.
    dossier : dict, optional
        Output from ``dossier_generator.generate_dossier``.
    classification : dict, optional
        Output from ``transient_classifier`` with keys: classification,
        confidence, probabilities.

    Returns
    -------
    dict
        Structured recommendation with urgency, ordered recommendations,
        and scientific rationale.
    """
    # Resolve classification + confidence from the best available source
    cls_type = (
        (classification or {}).get("classification")
        or alert.get("classification")
        or "Unknown"
    )
    confidence = (
        (classification or {}).get("confidence")
        or alert.get("classification_confidence")
        or 0.0
    )
    if confidence is None:
        confidence = 0.0

    ra = alert.get("ra", 0.0)
    dec = alert.get("dec", 0.0)
    magnitude = alert.get("magnitude")
    source_id = alert.get("source_id", "unknown")
    days_since = _days_from_discovery(alert)
    has_spec_z = _has_spectroscopic_redshift(dossier)
    host_z = _host_has_redshift(dossier)

    recommendations: list[dict[str, Any]] = []
    urgency = "MEDIUM"
    rationale_parts: list[str] = []

    rationale_parts.append(
        f"Object {source_id} classified as {cls_type} (confidence {confidence:.0%})."
    )

    # ── SN Ia logic ──
    if cls_type == "SN_Ia" and confidence > 0.5:
        if not has_spec_z:
            # No spectroscopic confirmation -- urgent spectroscopy
            urgency = "URGENT"
            exp = _estimate_exposure(
                magnitude or 18.0, snr_target=20, telescope="gemini"
            )
            recommendations.append(
                {
                    "type": "spectroscopy",
                    "priority": 1,
                    "instrument_class": "4m optical spectrograph",
                    "suggested_facilities": SPECTROSCOPY_FACILITIES["4m"],
                    "wavelength_range": "3500-9000 \u00c5",
                    "resolution": "R~1000",
                    "exposure_seconds": exp.get("exposure_time_seconds", 1800),
                    "snr_target": 20,
                    "science_goal": "Spectroscopic confirmation and subtype classification",
                    "time_critical": True,
                }
            )
            rationale_parts.append(
                "No spectroscopic redshift on record; spectroscopic confirmation "
                "is the highest priority to validate cosmological utility."
            )
        else:
            rationale_parts.append("Spectroscopic redshift confirmed.")

        # Early-phase photometric monitoring
        if days_since is not None and days_since < 10:
            urgency = max(urgency, "URGENT", key=_urgency_rank)
            recommendations.append(
                {
                    "type": "photometry",
                    "priority": 2 if not has_spec_z else 1,
                    "instrument_class": "1m-class optical imager",
                    "suggested_facilities": PHOTOMETRY_FACILITIES,
                    "bands": ["B", "V", "g", "r", "i"],
                    "cadence": "daily",
                    "duration_days": 60,
                    "science_goal": (
                        "Pre-maximum photometric monitoring for light-curve "
                        "fitting and Hubble-diagram inclusion"
                    ),
                    "time_critical": True,
                }
            )
            rationale_parts.append(
                f"Object is {days_since:.1f} days from discovery (<10 d), "
                "likely pre-maximum -- dense photometric sampling is critical."
            )
        elif days_since is not None:
            recommendations.append(
                {
                    "type": "photometry",
                    "priority": 3,
                    "instrument_class": "1m-class optical imager",
                    "suggested_facilities": PHOTOMETRY_FACILITIES,
                    "bands": ["B", "V", "g", "r", "i"],
                    "cadence": "every 3 days",
                    "duration_days": 60,
                    "science_goal": "Post-maximum light-curve coverage",
                    "time_critical": False,
                }
            )

        # Host galaxy spectroscopy if no host redshift
        if not host_z and alert.get("host_galaxy"):
            recommendations.append(
                {
                    "type": "spectroscopy",
                    "priority": 4,
                    "instrument_class": "4m optical spectrograph",
                    "suggested_facilities": SPECTROSCOPY_FACILITIES["4m"],
                    "wavelength_range": "3500-9000 \u00c5",
                    "resolution": "R~1000",
                    "exposure_seconds": 3600,
                    "snr_target": 10,
                    "science_goal": (
                        f"Host galaxy ({alert['host_galaxy']}) redshift measurement "
                        "for independent distance constraint"
                    ),
                    "time_critical": False,
                }
            )
            rationale_parts.append(
                f"Host galaxy {alert['host_galaxy']} lacks a measured redshift."
            )

    # ── TDE logic ──
    elif cls_type == "TDE":
        urgency = "URGENT"
        recommendations.append(
            {
                "type": "xray",
                "priority": 1,
                "instrument_class": "X-ray telescope",
                "suggested_facilities": XRAY_FACILITIES,
                "wavelength_range": "0.3-10 keV",
                "exposure_seconds": 5000,
                "snr_target": 10,
                "science_goal": (
                    "X-ray detection to confirm tidal disruption origin "
                    "and constrain black-hole accretion state"
                ),
                "time_critical": True,
            }
        )
        recommendations.append(
            {
                "type": "uv",
                "priority": 2,
                "instrument_class": "UV imager",
                "suggested_facilities": UV_FACILITIES,
                "wavelength_range": "1700-6000 \u00c5 (UV grism)",
                "cadence": "every 3 days",
                "duration_days": 90,
                "science_goal": (
                    "UV monitoring to track thermal evolution "
                    "and constrain debris circularization timescale"
                ),
                "time_critical": True,
            }
        )
        exp = _estimate_exposure(magnitude or 19.0, snr_target=15, telescope="keck")
        recommendations.append(
            {
                "type": "spectroscopy",
                "priority": 3,
                "instrument_class": "8m optical spectrograph",
                "suggested_facilities": SPECTROSCOPY_FACILITIES["8m"],
                "wavelength_range": "3500-10000 \u00c5",
                "resolution": "R~1000-3000",
                "exposure_seconds": exp.get("exposure_time_seconds", 3600),
                "snr_target": 15,
                "science_goal": (
                    "Optical spectroscopy to search for broad He II/H-alpha "
                    "emission (BLR signatures) and Bowen fluorescence lines"
                ),
                "time_critical": True,
            }
        )
        rationale_parts.append(
            "Tidal disruption events require rapid multi-wavelength follow-up: "
            "X-ray to confirm accretion, UV for thermal evolution, and optical "
            "spectroscopy for BLR and Bowen fluorescence diagnostics."
        )

    # ── Unknown / low-confidence logic ──
    elif cls_type == "Unknown" or confidence < 0.5:
        urgency = "HIGH"
        exp = _estimate_exposure(magnitude or 19.0, snr_target=15, telescope="gemini")
        recommendations.append(
            {
                "type": "spectroscopy",
                "priority": 1,
                "instrument_class": "4m optical spectrograph",
                "suggested_facilities": SPECTROSCOPY_FACILITIES["4m"],
                "wavelength_range": "3500-9000 \u00c5",
                "resolution": "R~1000",
                "exposure_seconds": exp.get("exposure_time_seconds", 1800),
                "snr_target": 15,
                "science_goal": "Classification spectroscopy for untyped/uncertain transient",
                "time_critical": True,
            }
        )
        recommendations.append(
            {
                "type": "photometry",
                "priority": 2,
                "instrument_class": "1m-class optical imager",
                "suggested_facilities": PHOTOMETRY_FACILITIES,
                "bands": ["g", "r", "i", "z"],
                "cadence": "daily",
                "duration_days": 14,
                "science_goal": "Multi-band photometry for colour evolution and light-curve morphology",
                "time_critical": True,
            }
        )
        rationale_parts.append(
            "Classification is uncertain; spectroscopic typing is the priority "
            "to determine the nature of this transient and guide further observations."
        )

    # ── AGN / CV logic ──
    elif cls_type in ("AGN", "CV"):
        is_unusually_bright = magnitude is not None and magnitude < 16.0
        if is_unusually_bright:
            urgency = "MEDIUM"
            exp = _estimate_exposure(magnitude, snr_target=10, telescope="gemini")
            recommendations.append(
                {
                    "type": "spectroscopy",
                    "priority": 2,
                    "instrument_class": "2m optical spectrograph",
                    "suggested_facilities": SPECTROSCOPY_FACILITIES["2m"],
                    "wavelength_range": "3500-9000 \u00c5",
                    "resolution": "R~1000",
                    "exposure_seconds": exp.get("exposure_time_seconds", 900),
                    "snr_target": 10,
                    "science_goal": (
                        f"Spectroscopy of unusually bright {cls_type} "
                        f"(mag {magnitude:.1f}) to check for state change or misclassification"
                    ),
                    "time_critical": False,
                }
            )
            rationale_parts.append(
                f"This {cls_type} is unusually bright (mag {magnitude:.1f}); "
                "spectroscopy recommended to rule out misclassification."
            )
        else:
            urgency = "LOW"
            rationale_parts.append(
                f"Classified as {cls_type} with no unusual brightness. "
                "Low follow-up priority."
            )

        recommendations.append(
            {
                "type": "archival",
                "priority": 5 if not is_unusually_bright else 3,
                "instrument_class": "archival databases",
                "suggested_facilities": [
                    "SIMBAD cross-match",
                    "NED archival search",
                    "SDSS spectroscopic archive",
                    "Gaia variable star catalogue",
                ],
                "science_goal": (
                    f"Archival search for prior detections and historical "
                    f"variability of this {cls_type}"
                ),
                "time_critical": False,
            }
        )

    # ── Other classifications -- generic follow-up ──
    else:
        urgency = "MEDIUM"
        exp = _estimate_exposure(magnitude or 19.0, snr_target=15, telescope="gemini")
        recommendations.append(
            {
                "type": "spectroscopy",
                "priority": 2,
                "instrument_class": "4m optical spectrograph",
                "suggested_facilities": SPECTROSCOPY_FACILITIES["4m"],
                "wavelength_range": "3500-9000 \u00c5",
                "resolution": "R~1000",
                "exposure_seconds": exp.get("exposure_time_seconds", 1800),
                "snr_target": 15,
                "science_goal": f"Spectroscopic characterisation of {cls_type} transient",
                "time_critical": False,
            }
        )
        recommendations.append(
            {
                "type": "photometry",
                "priority": 3,
                "instrument_class": "1m-class optical imager",
                "suggested_facilities": PHOTOMETRY_FACILITIES,
                "bands": ["g", "r", "i"],
                "cadence": "every 3 days",
                "duration_days": 30,
                "science_goal": "Multi-band light-curve monitoring",
                "time_critical": False,
            }
        )
        rationale_parts.append(f"Standard follow-up plan for {cls_type} transient.")

    # ── General: observability for all types ──
    observability = _compute_observability(ra, dec)
    if not observability.get("is_observable", True):
        rationale_parts.append(
            f"Warning: target transit altitude is only "
            f"{observability.get('transit_altitude_deg', 0)}\u00b0 from "
            f"{observability.get('observatory', 'best observatory')} -- "
            "scheduling may be challenging."
        )

    # ── General: exposure-time annotation for magnitude ──
    if magnitude is not None:
        for rec in recommendations:
            if rec.get("type") == "spectroscopy" and "exposure_seconds" not in rec:
                exp = _estimate_exposure(
                    magnitude, snr_target=rec.get("snr_target", 15)
                )
                rec["exposure_seconds"] = exp.get("exposure_time_seconds", 1800)

    # Sort recommendations by priority
    recommendations.sort(key=lambda r: r.get("priority", 99))

    return {
        "alert_id": alert.get("id") or source_id,
        "classification": cls_type,
        "confidence": confidence,
        "urgency": urgency,
        "recommendations": recommendations,
        "observability": observability,
        "rationale": " ".join(rationale_parts),
    }


def _urgency_rank(level: str) -> int:
    """Return numeric rank for urgency comparison (higher = more urgent)."""
    return {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "URGENT": 3}.get(level, 1)
