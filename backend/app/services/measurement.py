"""Typed measurements for tool boundaries (Phase 1 / R4).

Every numeric quantity a tool returns should carry a unit and, where
appropriate, a coordinate frame + an uncertainty.  We cannot rewrite all
53 tools today, so the MVP is:

1. A `Measurement` dataclass (value, unit, err, frame) that tools can
   emit directly when producing key scalars.
2. A `tag_coords(result_dict)` helper that walks a result dict and
   attaches `ra_unit / dec_unit / frame` siblings to any `ra`/`dec` fields
   so downstream consumers (and the LLM) can never confuse ICRS degrees
   with FK5 hours.
3. `annotate_known_fields(result_dict)` that infers units for the
   highest-traffic column names (e.g., `parallax` → `mas`).  Applied by
   `normalize_tool_result` for the 8 high-traffic tools noted in the
   rigor plan.

Later phases can migrate individual tools to emit `Measurement` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class Measurement:
    value: float
    unit: str
    err: float | None = None
    frame: str | None = None  # only meaningful for coordinates

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Drop None fields so the JSON payload stays compact.
        return {k: v for k, v in d.items() if v is not None}


# Heuristic unit map for column names that nearly always mean the same
# thing in astronomy (best-effort — tools that know better can override).
_COLUMN_UNITS: dict[str, str] = {
    # Astrometry
    "ra": "deg", "ra_deg": "deg", "raj2000": "deg",
    "dec": "deg", "dec_deg": "deg", "dej2000": "deg",
    "pmra": "mas/yr", "pmdec": "mas/yr",
    "parallax": "mas", "plx": "mas", "plx_value": "mas",
    # Photometry
    "phot_g_mean_mag": "mag", "phot_bp_mean_mag": "mag", "phot_rp_mean_mag": "mag",
    "g_mag": "mag", "bp_mag": "mag", "rp_mag": "mag", "bp_rp": "mag",
    "v_mag": "mag", "b_mag": "mag", "r_mag": "mag", "i_mag": "mag",
    "j_mag": "mag", "h_mag": "mag", "k_mag": "mag",
    "jmag": "mag", "hmag": "mag", "kmag": "mag",
    "w1mag": "mag", "w2mag": "mag", "w3mag": "mag", "w4mag": "mag",
    "w1mpro": "mag", "w2mpro": "mag", "w3mpro": "mag", "w4mpro": "mag",
    # Kinematics / spectroscopy
    "radial_velocity": "km/s", "rv": "km/s", "rvz_radvel": "km/s",
    # NB: bare `z` is intentionally NOT mapped — on the documented SDSS path
    # (VizieR V/154/sdss17) `z` is the z-band magnitude, not a redshift; the
    # unambiguous redshift columns are `zsp`/`zph`.
    "redshift": "dimensionless", "rvz_redshift": "dimensionless",
    "zsp": "dimensionless", "zph": "dimensionless",
    # Stellar params
    "teff": "K", "t_eff": "K",
    "log_g": "dex", "logg": "dex",
    "metallicity": "dex", "feh": "dex", "fe_h": "dex",
    # Distance / size
    "distance": "pc", "distance_pc": "pc", "distance_kpc": "kpc", "distance_mpc": "Mpc",
    "radius": "R_sun", "radius_solar": "R_sun",
    "mass": "M_sun", "mass_solar": "M_sun",
    # Angular
    "separation_arcsec": "arcsec", "separation_arcmin": "arcmin",
    "radius_arcsec": "arcsec", "radius_arcmin": "arcmin", "radius_deg": "deg",
    # Time
    "period_days": "day", "period": "day",
    "epoch_mjd": "mjd",
}


def _apply_to_dict(d: dict, frame_default: str = "icrs") -> None:
    """Mutate `d` in place: attach unit siblings for known column names
    and tag coordinates with the ICRS frame unless one is already set.
    """
    keys = list(d.keys())
    has_ra = any(k.lower() in {"ra", "ra_deg", "raj2000"} for k in keys)
    has_dec = any(k.lower() in {"dec", "dec_deg", "dej2000"} for k in keys)
    if has_ra and has_dec and "frame" not in d:
        d["frame"] = frame_default

    for k in keys:
        lower = k.lower()
        unit = _COLUMN_UNITS.get(lower)
        if unit is None:
            continue
        unit_key = f"{k}_unit"
        if unit_key in d:
            continue
        v = d[k]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            d[unit_key] = unit


def annotate_known_fields(payload: Any, frame_default: str = "icrs") -> Any:
    """Walk a nested structure and annotate well-known astronomical
    columns with unit siblings + coordinate frame.

    The walker is defensive: unknown types pass through, and annotation
    is idempotent so the helper can be called multiple times without
    piling up `_unit_unit_unit` keys.
    """
    if isinstance(payload, dict):
        _apply_to_dict(payload, frame_default=frame_default)
        for v in payload.values():
            annotate_known_fields(v, frame_default=frame_default)
    elif isinstance(payload, list):
        # For list-of-dicts (row-oriented tables), annotate each row.
        for item in payload:
            annotate_known_fields(item, frame_default=frame_default)
    return payload
