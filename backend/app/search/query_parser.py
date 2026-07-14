"""Rule-based natural language query parser for astronomical searches.

Parses queries like "high redshift [C II] line data" or "z > 6 Lyman-alpha emitters"
into structured search filters. No external AI API calls — purely keyword matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Spectral line catalogue ──

SPECTRAL_LINES: dict[str, dict] = {
    # Carbon
    "cii": {"name": "[C II]", "rest_freq_ghz": 1900.5369, "rest_wavelength_um": 157.74},
    "c2": {"name": "[C II]", "rest_freq_ghz": 1900.5369, "rest_wavelength_um": 157.74},
    "[cii]": {"name": "[C II]", "rest_freq_ghz": 1900.5369, "rest_wavelength_um": 157.74},
    "[c ii]": {"name": "[C II]", "rest_freq_ghz": 1900.5369, "rest_wavelength_um": 157.74},
    "ci": {"name": "[C I]", "rest_freq_ghz": 492.1607, "rest_wavelength_um": 609.14},
    # Hydrogen Lyman series
    "lyman-alpha": {"name": "Lyman-\u03b1", "rest_wavelength_um": 0.12157},
    "lyman alpha": {"name": "Lyman-\u03b1", "rest_wavelength_um": 0.12157},
    "ly-alpha": {"name": "Lyman-\u03b1", "rest_wavelength_um": 0.12157},
    "ly-a": {"name": "Lyman-\u03b1", "rest_wavelength_um": 0.12157},
    "lya": {"name": "Lyman-\u03b1", "rest_wavelength_um": 0.12157},
    "ly\u03b1": {"name": "Lyman-\u03b1", "rest_wavelength_um": 0.12157},
    # Hydrogen Balmer series
    "h-alpha": {"name": "H-\u03b1", "rest_wavelength_um": 0.65628},
    "h alpha": {"name": "H-\u03b1", "rest_wavelength_um": 0.65628},
    "ha": {"name": "H-\u03b1", "rest_wavelength_um": 0.65628},
    "halpha": {"name": "H-\u03b1", "rest_wavelength_um": 0.65628},
    "h\u03b1": {"name": "H-\u03b1", "rest_wavelength_um": 0.65628},
    "h-beta": {"name": "H-\u03b2", "rest_wavelength_um": 0.48613},
    "h beta": {"name": "H-\u03b2", "rest_wavelength_um": 0.48613},
    "hb": {"name": "H-\u03b2", "rest_wavelength_um": 0.48613},
    "hbeta": {"name": "H-\u03b2", "rest_wavelength_um": 0.48613},
    "h-gamma": {"name": "H-\u03b3", "rest_wavelength_um": 0.43405},
    "hg": {"name": "H-\u03b3", "rest_wavelength_um": 0.43405},
    # Oxygen
    "oiii": {"name": "[O III]", "rest_wavelength_um": 0.50069},
    "o3": {"name": "[O III]", "rest_wavelength_um": 0.50069},
    "[oiii]": {"name": "[O III]", "rest_wavelength_um": 0.50069},
    "[o iii]": {"name": "[O III]", "rest_wavelength_um": 0.50069},
    "oii": {"name": "[O II]", "rest_wavelength_um": 0.37264},
    "[oii]": {"name": "[O II]", "rest_wavelength_um": 0.37264},
    "[o ii]": {"name": "[O II]", "rest_wavelength_um": 0.37264},
    "oi": {"name": "[O I]", "rest_wavelength_um": 63.18},
    # Nitrogen
    "nii": {"name": "[N II]", "rest_wavelength_um": 0.65835},
    "[nii]": {"name": "[N II]", "rest_wavelength_um": 0.65835},
    "[n ii]": {"name": "[N II]", "rest_wavelength_um": 0.65835},
    # Sulfur
    "sii": {"name": "[S II]", "rest_wavelength_um": 0.67164},
    "[sii]": {"name": "[S II]", "rest_wavelength_um": 0.67164},
    "[s ii]": {"name": "[S II]", "rest_wavelength_um": 0.67164},
    # CO rotational lines
    "co10": {"name": "CO(1-0)", "rest_freq_ghz": 115.271, "rest_wavelength_um": 2600.76},
    "co(1-0)": {"name": "CO(1-0)", "rest_freq_ghz": 115.271, "rest_wavelength_um": 2600.76},
    "co21": {"name": "CO(2-1)", "rest_freq_ghz": 230.538, "rest_wavelength_um": 1300.40},
    "co(2-1)": {"name": "CO(2-1)", "rest_freq_ghz": 230.538, "rest_wavelength_um": 1300.40},
    "co32": {"name": "CO(3-2)", "rest_freq_ghz": 345.796, "rest_wavelength_um": 867.00},
    "co(3-2)": {"name": "CO(3-2)", "rest_freq_ghz": 345.796, "rest_wavelength_um": 867.00},
    "co43": {"name": "CO(4-3)", "rest_freq_ghz": 461.041, "rest_wavelength_um": 650.25},
    "co(4-3)": {"name": "CO(4-3)", "rest_freq_ghz": 461.041, "rest_wavelength_um": 650.25},
    "co54": {"name": "CO(5-4)", "rest_freq_ghz": 576.268, "rest_wavelength_um": 520.23},
    "co(5-4)": {"name": "CO(5-4)", "rest_freq_ghz": 576.268, "rest_wavelength_um": 520.23},
    "co65": {"name": "CO(6-5)", "rest_freq_ghz": 691.473, "rest_wavelength_um": 433.56},
    "co(6-5)": {"name": "CO(6-5)", "rest_freq_ghz": 691.473, "rest_wavelength_um": 433.56},
    "co76": {"name": "CO(7-6)", "rest_freq_ghz": 806.652, "rest_wavelength_um": 371.65},
    "co(7-6)": {"name": "CO(7-6)", "rest_freq_ghz": 806.652, "rest_wavelength_um": 371.65},
    # Water
    "h2o": {"name": "H2O", "rest_freq_ghz": 556.936, "rest_wavelength_um": 538.29},
}

REDSHIFT_KEYWORDS: dict[str, tuple[float | None, float | None]] = {
    "high redshift": (4.0, None),
    "high-redshift": (4.0, None),
    "high-z": (4.0, None),
    "very high redshift": (6.0, None),
    "very high-redshift": (6.0, None),
    "ultra high redshift": (8.0, None),
    "low redshift": (0, 0.5),
    "low-redshift": (0, 0.5),
    "low-z": (0, 0.5),
    "nearby": (0, 0.1),
    "local": (0, 0.05),
    "local universe": (0, 0.05),
    "intermediate redshift": (0.5, 2.0),
    "intermediate-redshift": (0.5, 2.0),
    "cosmic noon": (1.0, 3.0),
    "epoch of reionization": (6.0, 15.0),
    "reionization": (6.0, 15.0),
    "eor": (6.0, 15.0),
    "cosmic dawn": (10.0, 20.0),
}

OBJECT_TYPE_KEYWORDS: dict[str, str] = {
    "agn": "AGN",
    "active galactic nucleus": "AGN",
    "active galactic nuclei": "AGN",
    "quasar": "quasar",
    "qso": "quasar",
    "galaxy": "galaxy",
    "galaxies": "galaxy",
    "star": "star",
    "stars": "star",
    "stellar": "star",
    "nebula": "nebula",
    "planetary nebula": "planetary nebula",
    "pulsar": "pulsar",
    "supernova": "supernova",
    "supernovae": "supernova",
    "sn": "supernova",
    "sne": "supernova",
    "lae": "Lyman-alpha emitter",
    "lyman-alpha emitter": "Lyman-alpha emitter",
    "lyman alpha emitter": "Lyman-alpha emitter",
    "lyman-alpha emitters": "Lyman-alpha emitter",
    "smg": "submillimeter galaxy",
    "submillimeter galaxy": "submillimeter galaxy",
    "sub-mm galaxy": "submillimeter galaxy",
    "lbg": "Lyman-break galaxy",
    "lyman-break galaxy": "Lyman-break galaxy",
    "lyman break galaxy": "Lyman-break galaxy",
    "grb": "gamma-ray burst",
    "gamma-ray burst": "gamma-ray burst",
    "gamma ray burst": "gamma-ray burst",
    "white dwarf": "white dwarf",
    "brown dwarf": "brown dwarf",
    "red dwarf": "red dwarf",
    "main sequence": "main sequence star",
    "giant star": "giant star",
    "red giant": "red giant",
    "blue giant": "blue giant",
    "supergiant": "supergiant",
    "cepheid": "cepheid",
    "rr lyrae": "RR Lyrae",
    "t tauri": "T Tauri",
    "protostar": "protostar",
    "protostellar": "protostar",
    "seyfert": "Seyfert galaxy",
    "blazar": "blazar",
    "liner": "LINER",
    "ulirg": "ULIRG",
    "lirg": "LIRG",
    "starburst": "starburst galaxy",
    "dwarf galaxy": "dwarf galaxy",
    "elliptical": "elliptical galaxy",
    "spiral": "spiral galaxy",
    "irregular": "irregular galaxy",
    "cluster": "galaxy cluster",
    "galaxy cluster": "galaxy cluster",
    "globular cluster": "globular cluster",
    "open cluster": "open cluster",
}

# ── Known astronomical regions (name → center coords + default search radius) ──

ASTRONOMICAL_REGIONS: dict[str, dict] = {
    # Star-forming regions
    "taurus": {"name": "Taurus SFR", "ra": 68.0, "dec": 26.0, "radius": 10.0},
    "taurus star-forming": {"name": "Taurus SFR", "ra": 68.0, "dec": 26.0, "radius": 10.0},
    "taurus molecular cloud": {"name": "Taurus SFR", "ra": 68.0, "dec": 26.0, "radius": 10.0},
    "orion": {"name": "Orion Nebula", "ra": 83.82, "dec": -5.39, "radius": 5.0},
    "orion nebula": {"name": "Orion Nebula", "ra": 83.82, "dec": -5.39, "radius": 2.0},
    "orion molecular cloud": {"name": "Orion Molecular Cloud", "ra": 83.82, "dec": -5.39, "radius": 8.0},
    "ophiuchus": {"name": "Ophiuchus SFR", "ra": 246.8, "dec": -24.5, "radius": 6.0},
    "rho ophiuchi": {"name": "Rho Ophiuchi", "ra": 246.4, "dec": -24.4, "radius": 3.0},
    "rho oph": {"name": "Rho Ophiuchi", "ra": 246.4, "dec": -24.4, "radius": 3.0},
    "serpens": {"name": "Serpens SFR", "ra": 277.5, "dec": 1.25, "radius": 3.0},
    "chamaeleon": {"name": "Chamaeleon SFR", "ra": 168.0, "dec": -77.0, "radius": 5.0},
    "lupus": {"name": "Lupus SFR", "ra": 240.0, "dec": -39.0, "radius": 5.0},
    "perseus": {"name": "Perseus SFR", "ra": 52.0, "dec": 31.0, "radius": 5.0},
    "perseus molecular cloud": {"name": "Perseus SFR", "ra": 52.0, "dec": 31.0, "radius": 5.0},
    "carina": {"name": "Carina Nebula", "ra": 161.0, "dec": -59.75, "radius": 2.0},
    "carina nebula": {"name": "Carina Nebula", "ra": 161.0, "dec": -59.75, "radius": 2.0},
    "eagle nebula": {"name": "Eagle Nebula (M16)", "ra": 274.7, "dec": -13.81, "radius": 1.0},
    "lagoon nebula": {"name": "Lagoon Nebula (M8)", "ra": 271.1, "dec": -24.38, "radius": 1.5},
    "rosette nebula": {"name": "Rosette Nebula", "ra": 98.0, "dec": 5.0, "radius": 2.0},
    # Nearby galaxies & groups
    "magellanic cloud": {"name": "LMC", "ra": 80.89, "dec": -69.76, "radius": 5.0},
    "large magellanic cloud": {"name": "LMC", "ra": 80.89, "dec": -69.76, "radius": 5.0},
    "lmc": {"name": "LMC", "ra": 80.89, "dec": -69.76, "radius": 5.0},
    "small magellanic cloud": {"name": "SMC", "ra": 13.19, "dec": -72.83, "radius": 3.0},
    "smc": {"name": "SMC", "ra": 13.19, "dec": -72.83, "radius": 3.0},
    # Constellations / galactic regions
    "scorpius": {"name": "Scorpius", "ra": 250.0, "dec": -25.0, "radius": 15.0},
    "sagittarius": {"name": "Sagittarius", "ra": 283.0, "dec": -30.0, "radius": 15.0},
    "cygnus": {"name": "Cygnus", "ra": 305.0, "dec": 40.0, "radius": 15.0},
    "centaurus": {"name": "Centaurus", "ra": 201.0, "dec": -43.0, "radius": 15.0},
    "virgo cluster": {"name": "Virgo Cluster", "ra": 187.7, "dec": 12.3, "radius": 8.0},
    "coma cluster": {"name": "Coma Cluster", "ra": 194.9, "dec": 27.98, "radius": 3.0},
    "fornax cluster": {"name": "Fornax Cluster", "ra": 54.6, "dec": -35.5, "radius": 4.0},
    # Galactic plane features
    "galactic center": {"name": "Galactic Center", "ra": 266.42, "dec": -29.01, "radius": 2.0},
    "galactic centre": {"name": "Galactic Center", "ra": 266.42, "dec": -29.01, "radius": 2.0},
    "galactic bulge": {"name": "Galactic Bulge", "ra": 266.42, "dec": -29.01, "radius": 10.0},
    # Well-known survey fields
    "hubble deep field": {"name": "Hubble Deep Field", "ra": 189.2, "dec": 62.22, "radius": 0.1},
    "hdf": {"name": "Hubble Deep Field", "ra": 189.2, "dec": 62.22, "radius": 0.1},
    "hubble ultra deep field": {"name": "Hubble Ultra Deep Field", "ra": 53.16, "dec": -27.79, "radius": 0.1},
    "hudf": {"name": "Hubble Ultra Deep Field", "ra": 53.16, "dec": -27.79, "radius": 0.1},
    "cosmos field": {"name": "COSMOS Field", "ra": 150.12, "dec": 2.21, "radius": 1.0},
    "cosmos": {"name": "COSMOS Field", "ra": 150.12, "dec": 2.21, "radius": 1.0},
    "goods-north": {"name": "GOODS-North", "ra": 189.23, "dec": 62.24, "radius": 0.15},
    "goods-south": {"name": "GOODS-South", "ra": 53.12, "dec": -27.81, "radius": 0.15},
    # Star clusters
    "pleiades": {"name": "Pleiades (M45)", "ra": 56.87, "dec": 24.12, "radius": 2.0},
    "hyades": {"name": "Hyades", "ra": 66.75, "dec": 15.87, "radius": 5.0},
    "praesepe": {"name": "Praesepe (M44)", "ra": 130.1, "dec": 19.67, "radius": 1.5},
    "omega centauri": {"name": "Omega Centauri", "ra": 201.7, "dec": -47.48, "radius": 0.5},
    "47 tucanae": {"name": "47 Tucanae", "ra": 6.02, "dec": -72.08, "radius": 0.5},
}

OBSERVATION_TYPE_KEYWORDS: dict[str, str] = {
    "spectroscopy": "spectroscopy",
    "spectroscopic": "spectroscopy",
    "spectra": "spectroscopy",
    "spectrum": "spectroscopy",
    "imaging": "imaging",
    "image": "imaging",
    "photometry": "photometry",
    "photometric": "photometry",
    "interferometry": "interferometry",
    "interferometric": "interferometry",
    "polarimetry": "polarimetry",
    "polarimetric": "polarimetry",
    "x-ray": "X-ray",
    "xray": "X-ray",
    "infrared": "infrared",
    "ir": "infrared",
    "near-ir": "near-infrared",
    "nir": "near-infrared",
    "mid-ir": "mid-infrared",
    "mir": "mid-infrared",
    "far-ir": "far-infrared",
    "fir": "far-infrared",
    "submillimeter": "submillimeter",
    "sub-mm": "submillimeter",
    "millimeter": "millimeter",
    "mm": "millimeter",
    "radio": "radio",
    "uv": "ultraviolet",
    "ultraviolet": "ultraviolet",
    "optical": "optical",
}


# ── Regex patterns for numeric redshift extraction ──

# Matches: z > 6, z>6, z >= 6.5, z ≥ 6
_RE_Z_GT = re.compile(
    r"\bz\s*(?:>|>=|≥|greater\s+than)\s*(\d+(?:\.\d+)?)", re.IGNORECASE
)
# Matches: z < 2, z <= 1.5, z ≤ 0.5
_RE_Z_LT = re.compile(
    r"\bz\s*(?:<|<=|≤|less\s+than)\s*(\d+(?:\.\d+)?)", re.IGNORECASE
)
# Matches: z = 2-3, z=2..3, z 2-3, redshift 2-3
# Separator is a dash/en-dash, the word "to", or two-or-more dots — never a
# single "." (which would split a decimal redshift like z=2.5 into a fake range).
_RE_Z_RANGE = re.compile(
    r"\b(?:z|redshift)\s*(?:[=:]\s*)?(\d+(?:\.\d+)?)\s*(?:[-–]+|\.{2,}|to)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
# Matches: z = 6, z=6.5, z ~ 7
_RE_Z_EQ = re.compile(
    r"\bz\s*[=~≈]\s*(\d+(?:\.\d+)?)", re.IGNORECASE
)

# Coordinate patterns: RA, Dec
_RE_RA = re.compile(
    r"\bra\s*[=:]\s*(\d+(?:\.\d+)?)", re.IGNORECASE
)
_RE_DEC = re.compile(
    r"\bdec\s*[=:]\s*([+-]?\d+(?:\.\d+)?)", re.IGNORECASE
)
_RE_RADIUS = re.compile(
    r"\bradius\s*[=:]\s*(\d+(?:\.\d+)?)", re.IGNORECASE
)


@dataclass
class ParsedQuery:
    """Structured result of parsing a natural language astronomical query."""
    redshift_min: float | None = None
    redshift_max: float | None = None
    spectral_line: str | None = None  # Key into SPECTRAL_LINES
    spectral_line_info: dict | None = None
    wavelength_min: float | None = None  # microns
    wavelength_max: float | None = None  # microns
    observation_type: str | None = None
    object_type: str | None = None
    instrument: str | None = None
    ra: float | None = None
    dec: float | None = None
    radius: float | None = None
    region: str | None = None  # Matched region display name
    # Computed fields
    observed_freq_min_ghz: float | None = None
    observed_freq_max_ghz: float | None = None
    observed_wavelength_min_um: float | None = None
    observed_wavelength_max_um: float | None = None
    matched_keywords: list[str] = field(default_factory=list)
    # Fields the user specifically wants in results (for NOT NULL filtering)
    required_fields: list[str] = field(default_factory=list)


def _match_longest_first(text_lower: str, mapping: dict[str, object]) -> tuple[str | None, object]:
    """Match the longest key in mapping against text. Returns (matched_key, value) or (None, None)."""
    # Sort by length descending so longer/more-specific keys match first
    for key in sorted(mapping.keys(), key=len, reverse=True):
        if key in text_lower:
            return key, mapping[key]
    return None, None


def parse_natural_query(query: str) -> dict:
    """Parse a natural language astronomical query into structured search filters.

    Returns a dict with keys matching AdvancedSearchRequest fields.
    """
    result = ParsedQuery()
    q = query.lower().strip()

    # ── 1. Parse explicit redshift values (z > 6, z=2-3, etc.) ──
    m_range = _RE_Z_RANGE.search(q)
    if m_range:
        result.redshift_min = float(m_range.group(1))
        result.redshift_max = float(m_range.group(2))
        result.matched_keywords.append(f"z={m_range.group(1)}-{m_range.group(2)}")
    else:
        m_gt = _RE_Z_GT.search(q)
        m_lt = _RE_Z_LT.search(q)
        m_eq = _RE_Z_EQ.search(q)

        if m_gt:
            result.redshift_min = float(m_gt.group(1))
            result.matched_keywords.append(f"z > {m_gt.group(1)}")
        if m_lt:
            result.redshift_max = float(m_lt.group(1))
            result.matched_keywords.append(f"z < {m_lt.group(1)}")
        if m_eq and result.redshift_min is None and result.redshift_max is None:
            z_val = float(m_eq.group(1))
            # For z=X, search within +/- 0.5
            result.redshift_min = max(0, z_val - 0.5)
            result.redshift_max = z_val + 0.5
            result.matched_keywords.append(f"z ~ {m_eq.group(1)}")

    # ── 2. Parse redshift keywords (only if no explicit z value found) ──
    if result.redshift_min is None and result.redshift_max is None:
        matched_kw, z_range = _match_longest_first(q, REDSHIFT_KEYWORDS)
        if matched_kw is not None and z_range is not None:
            z_min, z_max = z_range
            result.redshift_min = z_min
            result.redshift_max = z_max
            result.matched_keywords.append(matched_kw)

    # ── 3. Parse spectral line ──
    matched_line_key, line_info = _match_longest_first(q, SPECTRAL_LINES)
    if matched_line_key is not None and line_info is not None:
        result.spectral_line = matched_line_key
        result.spectral_line_info = line_info
        result.matched_keywords.append(line_info["name"])

    # ── 4. Parse object type ──
    matched_obj_key, obj_type = _match_longest_first(q, OBJECT_TYPE_KEYWORDS)
    if matched_obj_key is not None and obj_type is not None:
        result.object_type = obj_type
        result.matched_keywords.append(obj_type)

    # ── 5. Parse observation type ──
    # Wavelength-band keywords (x-ray, infrared, radio, etc.) take priority
    # over generic mode keywords (spectroscopy, imaging) when both are present.
    _BAND_KEYWORDS = {
        k: v for k, v in OBSERVATION_TYPE_KEYWORDS.items()
        if v in (
            "X-ray", "infrared", "near-infrared", "mid-infrared",
            "far-infrared", "submillimeter", "millimeter", "radio",
            "ultraviolet", "optical",
        )
    }
    matched_band_key, band_type = _match_longest_first(q, _BAND_KEYWORDS)
    matched_obs_key, obs_type = _match_longest_first(q, OBSERVATION_TYPE_KEYWORDS)

    if matched_band_key is not None and band_type is not None:
        result.observation_type = band_type
        result.matched_keywords.append(band_type)
        # If there's also a generic mode keyword, add it as extra context
        if (
            matched_obs_key is not None
            and obs_type is not None
            and obs_type != band_type
        ):
            result.matched_keywords.append(obs_type)
    elif matched_obs_key is not None and obs_type is not None:
        result.observation_type = obs_type
        result.matched_keywords.append(obs_type)

    # ── 6. Parse coordinates ──
    m_ra = _RE_RA.search(q)
    m_dec = _RE_DEC.search(q)
    m_rad = _RE_RADIUS.search(q)
    if m_ra:
        result.ra = float(m_ra.group(1))
    if m_dec:
        result.dec = float(m_dec.group(1))
    if m_rad:
        result.radius = float(m_rad.group(1))

    # ── 6b. Detect known astronomical regions ──
    # Only apply region coordinates when no explicit RA/Dec was given
    if result.ra is None and result.dec is None:
        matched_region_key, region_info = _match_longest_first(q, ASTRONOMICAL_REGIONS)
        if matched_region_key is not None and region_info is not None:
            result.ra = region_info["ra"]
            result.dec = region_info["dec"]
            if result.radius is None:
                result.radius = region_info["radius"]
            result.region = region_info["name"]
            result.matched_keywords.append(f"Region: {region_info['name']}")

    # ── 7. Compute observed wavelength/frequency for line + redshift ──
    if result.spectral_line_info is not None:
        _compute_observed_ranges(result)

    # ── 8. Detect what data fields the user specifically wants ──
    field_keywords = {
        "redshift": "rvz_redshift", "z data": "rvz_redshift", "z value": "rvz_redshift",
        "parallax": "plx_value", "proper motion": "pmra",
        "radial velocity": "rvz_radvel", "spectral type": "sp_type",
        "morphology": "morph_type", "morphological": "morph_type",
    }
    for kw, fld in sorted(field_keywords.items(), key=lambda x: len(x[0]), reverse=True):
        if kw in q:
            if fld not in result.required_fields:
                result.required_fields.append(fld)
    # If user searches by redshift, they want redshift data
    if result.redshift_min is not None or result.redshift_max is not None:
        if "rvz_redshift" not in result.required_fields:
            result.required_fields.append("rvz_redshift")

    return _to_dict(result)


def _compute_observed_ranges(result: ParsedQuery) -> None:
    """Given a spectral line and redshift range, compute observed freq/wavelength ranges."""
    line = result.spectral_line_info
    if line is None:
        return

    z_min = result.redshift_min if result.redshift_min is not None else 0.0
    # For open-ended redshift (e.g. z > 6), cap at z=15 for practical calculations
    z_max = result.redshift_max if result.redshift_max is not None else (
        min(z_min + 10, 15.0) if result.redshift_min is not None else z_min
    )

    # If no redshift info at all, just use rest values
    if result.redshift_min is None and result.redshift_max is None:
        if "rest_wavelength_um" in line:
            result.observed_wavelength_min_um = line["rest_wavelength_um"]
            result.observed_wavelength_max_um = line["rest_wavelength_um"]
        if "rest_freq_ghz" in line:
            result.observed_freq_min_ghz = line["rest_freq_ghz"]
            result.observed_freq_max_ghz = line["rest_freq_ghz"]
        return

    # Observed wavelength = rest_wavelength * (1 + z)
    if "rest_wavelength_um" in line:
        result.observed_wavelength_min_um = line["rest_wavelength_um"] * (1 + z_min)
        result.observed_wavelength_max_um = line["rest_wavelength_um"] * (1 + z_max)

    # Observed frequency = rest_freq / (1 + z)
    if "rest_freq_ghz" in line:
        # Higher z = lower observed freq, so ranges are swapped
        result.observed_freq_min_ghz = line["rest_freq_ghz"] / (1 + z_max)
        result.observed_freq_max_ghz = line["rest_freq_ghz"] / (1 + z_min) if z_min >= 0 else line["rest_freq_ghz"]


def _to_dict(result: ParsedQuery) -> dict:
    """Convert ParsedQuery to a dict, omitting None values."""
    d: dict = {}
    if result.redshift_min is not None:
        d["redshift_min"] = result.redshift_min
    if result.redshift_max is not None:
        d["redshift_max"] = result.redshift_max
    if result.spectral_line is not None:
        d["spectral_line"] = result.spectral_line
    if result.spectral_line_info is not None:
        d["spectral_line_info"] = result.spectral_line_info
    if result.wavelength_min is not None:
        d["wavelength_min"] = result.wavelength_min
    if result.wavelength_max is not None:
        d["wavelength_max"] = result.wavelength_max
    if result.observation_type is not None:
        d["observation_type"] = result.observation_type
    if result.object_type is not None:
        d["object_type"] = result.object_type
    if result.instrument is not None:
        d["instrument"] = result.instrument
    if result.ra is not None:
        d["ra"] = result.ra
    if result.dec is not None:
        d["dec"] = result.dec
    if result.radius is not None:
        d["radius"] = result.radius
    if result.region is not None:
        d["region"] = result.region
    if result.observed_freq_min_ghz is not None:
        d["observed_freq_min_ghz"] = result.observed_freq_min_ghz
    if result.observed_freq_max_ghz is not None:
        d["observed_freq_max_ghz"] = result.observed_freq_max_ghz
    if result.observed_wavelength_min_um is not None:
        d["observed_wavelength_min_um"] = result.observed_wavelength_min_um
    if result.observed_wavelength_max_um is not None:
        d["observed_wavelength_max_um"] = result.observed_wavelength_max_um
    if result.matched_keywords:
        d["matched_keywords"] = result.matched_keywords
    if result.required_fields:
        d["required_fields"] = result.required_fields
    return d


def compute_observed_wavelength(rest_wavelength_um: float, redshift: float) -> float:
    """Compute observed wavelength given rest wavelength and redshift."""
    return rest_wavelength_um * (1 + redshift)


def compute_observed_frequency(rest_freq_ghz: float, redshift: float) -> float:
    """Compute observed frequency given rest frequency and redshift."""
    return rest_freq_ghz / (1 + redshift)


# ── Smart source selection ──

# ALMA bands in GHz
ALMA_BANDS = {
    3: (84.0, 116.0),
    4: (125.0, 163.0),
    5: (163.0, 211.0),
    6: (211.0, 275.0),
    7: (275.0, 373.0),
    8: (385.0, 500.0),
    9: (602.0, 720.0),
    10: (787.0, 950.0),
}


def suggest_sources(parsed: dict) -> list[str]:
    """Given parsed query filters, suggest the best data sources to query.

    Returns a ranked list of source keys.
    """
    scores: dict[str, float] = {
        "sdss": 1.0,
        "gaia": 1.0,
        "simbad": 1.0,
        "mast": 1.0,
        "alma": 1.0,
        "ned": 1.0,
        "vizier": 0.5,
        "2mass": 0.5,
        "chandra": 0.5,
        "allwise": 0.5,
        "panstarrs": 0.5,
        "xmm": 0.5,
        "nvss": 0.5,
        "first": 0.5,
    }

    obs_type = parsed.get("observation_type", "")
    obj_type = parsed.get("object_type", "")

    # Boost ALMA if we have sub-mm/mm frequency data
    freq_min = parsed.get("observed_freq_min_ghz")
    freq_max = parsed.get("observed_freq_max_ghz")
    if freq_min is not None or freq_max is not None:
        # Check if falls in ALMA bands
        for _band, (lo, hi) in ALMA_BANDS.items():
            f_lo = freq_min or 0
            f_hi = freq_max or 1e6
            if f_lo <= hi and f_hi >= lo:
                scores["alma"] += 10.0
                break

    # Boost ALMA for submillimeter/millimeter observations
    if obs_type in ("submillimeter", "millimeter", "interferometry"):
        scores["alma"] += 5.0

    # Boost SDSS for optical spectroscopy of galaxies/quasars
    if obj_type in ("quasar", "AGN", "galaxy", "Seyfert galaxy", "LINER"):
        scores["sdss"] += 3.0
        scores["ned"] += 3.0
    if obs_type in ("spectroscopy", "optical"):
        scores["sdss"] += 2.0

    # Boost Pan-STARRS for optical imaging and stellar objects
    if obs_type in ("optical", "imaging"):
        scores["panstarrs"] += 3.0
    if obj_type in ("star", "main sequence star", "white dwarf", "cepheid", "RR Lyrae"):
        scores["panstarrs"] += 2.0

    # Boost Chandra and XMM-Newton for X-ray
    if obs_type == "X-ray":
        scores["chandra"] += 8.0
        scores["xmm"] += 6.0

    # Boost NVSS and FIRST for radio observations and related object types
    if obs_type == "radio":
        scores["nvss"] += 8.0
        scores["first"] += 8.0
    if obj_type in ("AGN", "pulsar", "SNR", "supernova remnant", "jet"):
        scores["nvss"] += 4.0
        scores["first"] += 4.0

    # Boost MAST for UV/optical/IR imaging and HST data
    if obs_type in ("ultraviolet", "imaging", "near-infrared"):
        scores["mast"] += 3.0

    # Boost Gaia for stellar objects
    if obj_type in ("star", "main sequence star", "white dwarf", "cepheid", "RR Lyrae"):
        scores["gaia"] += 5.0

    # Boost 2MASS/AllWISE for infrared
    if obs_type in ("infrared", "near-infrared"):
        scores["2mass"] += 3.0
        scores["allwise"] += 3.0
    if obs_type in ("mid-infrared", "far-infrared"):
        scores["allwise"] += 5.0

    # NED is good for extragalactic + redshift queries
    if parsed.get("redshift_min") is not None or parsed.get("redshift_max") is not None:
        scores["ned"] += 3.0
        scores["sdss"] += 1.0

    # SIMBAD is a general resolver, always useful
    scores["simbad"] += 1.0

    # Sort by score descending
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [name for name, _score in ranked]


def get_spectral_lines_list() -> list[dict]:
    """Return a deduplicated list of spectral lines for the frontend dropdown.

    Each entry includes a canonical `key` that uniquely identifies the line
    and maps back to SPECTRAL_LINES for backend filtering.
    """
    # Pick the shortest alias for each unique line name as the canonical key
    canonical: dict[str, str] = {}  # name -> shortest key
    for key, info in SPECTRAL_LINES.items():
        name = info["name"]
        if name not in canonical or len(key) < len(canonical[name]):
            canonical[name] = key

    seen: dict[str, dict] = {}
    for key, info in SPECTRAL_LINES.items():
        name = info["name"]
        if name not in seen:
            entry = {
                "key": canonical[name],
                "name": name,
                "rest_wavelength_um": info.get("rest_wavelength_um"),
            }
            if "rest_freq_ghz" in info:
                entry["rest_freq_ghz"] = info["rest_freq_ghz"]
            seen[name] = entry
    return sorted(seen.values(), key=lambda x: x.get("rest_wavelength_um", 0))
