"""Zero-fabrication gate (Phase 1 / R2).

The AI assistant is allowed to cite numbers ONLY if those numbers appear in
the tool_results returned during the current turn.  Every paragraph of the
final assistant reply passes through `validate_claims`, which extracts
astronomical numeric claims via a regex catalogue and searches the tool
output tree for a matching value within ±1 %.  Uncited claims are returned
to the agent loop, which either asks the LLM to regenerate or, after two
failed attempts, blocks the reply entirely.

Design choices:
- Tolerance 1 %: tight enough to catch fabrication, loose enough to
  accommodate the same value formatted with different precision.
- Scientific-notation friendly (`1.2e-3`, `1.2E-03`, `1.2 × 10^-3`).
- Categorical claims (e.g., object types) are compared by exact substring
  match against the tool-results tree serialised to text.
- Runs in O(claims × nodes) with no HTTP calls — deliberately cheap so we
  can afford the per-reply overhead.

Out of scope for R2:
- Multi-turn claims (reply cites a number from last turn's tools): the gate
  only walks THIS turn's tool_results.  If the user wants cross-turn
  citation the LLM must re-run the tool.
- Unit-aware equality (5 pc ≡ 5 pc).  Today we compare raw floats; R4 will
  add Measurement types and tighten this.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.common.regex import ARXIV_ID_RE, AUTHOR_YEAR_RE, BIBCODE_RE, DOI_RE

logger = logging.getLogger(__name__)

# Tolerance for numeric equality.  ±1 % catches nearly every real-world
# fabrication (the model tends to invent round numbers or swap digits) while
# still matching a tool value re-stated with one-decimal-place precision.
DEFAULT_TOLERANCE = 0.01
# PART Y Batch 1: PROVENANCE_VALIDATOR_HARDBLOCK is on by default — citation
# violations (suspicious_author_year / invalid_bibcode) are hard-blocked by
# default, aligned with the ZERO-FABRICATION CONTRACT numeric rules. Set
# PROVENANCE_VALIDATOR_HARDBLOCK=false explicitly to downgrade citation
# violations to warn-only (emergency production kill switch).
CITATION_VALIDATOR_HARDBLOCK = os.getenv("PROVENANCE_VALIDATOR_HARDBLOCK", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

# Regex catalogue of astronomical numeric claims.  Each entry extracts a
# float from a phrase the model commonly produces.  Order matters: the first
# match wins, so put specific patterns before general ones.
#
# A claim is (label, value_str) where `label` tags the kind (for telemetry
# + clearer messages back to the LLM).
_NUM = r"([-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?)"
_UNIT = r"(?:\s*(?:±|\+/-)\s*[-+]?(?:\d+(?:\.\d+)?|\.\d+))?"  # optional ± err

_PATTERNS: list[tuple[str, re.Pattern]] = [
    # R22: exoplanet transit fits often report dimensionless ratios without
    # units ("Rp/Rs ≈ 0.157").  The older unit-based catalogue missed these,
    # so multi-agent merged replies could launder an unsupported radius ratio.
    ("radius_ratio", re.compile(
        rf"\b(?:R_?p\s*/\s*R_?s|Rp\s*/\s*Rs|R_p\s*/\s*R_s|"
        rf"planet[-\s]*to[-\s]*star\s+radius\s+ratio|radius\s+ratio)"
        rf"\s*(?:is|was|=|≈|~|about|around|approximately|approx\.?|约为)?\s*{_NUM}\b",
        re.I,
    )),
    ("transit_depth", re.compile(
        rf"\b(?:transit\s+depth|depth)\s*(?:is|was|=|≈|~|about|around|approximately|approx\.?)?\s*{_NUM}\b",
        re.I,
    )),
    ("redshift_z", re.compile(rf"\bz\s*[=≈~]\s*{_NUM}\b", re.I)),
    ("redshift_word", re.compile(rf"\bredshift\s*(?:of|=|≈|~|is)?\s*{_NUM}\b", re.I)),
    ("line_log_lcii", re.compile(
        rf"\b(?:log\s*)?L\s*\[?\s*C\s*II\s*\]?\s*(?:is|was|=|≈|~|:|about|approximately)?\s*{_NUM}\b",
        re.I,
    )),
    ("line_fwhm", re.compile(
        rf"\b(?:FWHM|line\s+width)\s*(?:is|was|=|≈|~|:|about|approximately)?\s*{_NUM}\s*(?:km\s*/?\s*s|km\s*s-?1)?\b",
        re.I,
    )),
    ("cosmology_h0", re.compile(
        rf"\b(?:H_?0|H₀)[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}"
        rf"(?:[ \t]*km[ \t]*s(?:ec)?(?:ond)?[ \t]*[-/]?[ \t]*Mpc(?:\^-?1)?|[ \t]*km/?s/?Mpc)?\b",
        re.I,
    )),
    ("cosmology_om0", re.compile(
        rf"\b(?:Om0|Omega_?m|OmegaM|Ω_?m|Ωₘ|ΩM)[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}\b",
        re.I,
    )),
    ("cosmology_w0", re.compile(rf"\b(?:w_?0|w₀)[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}\b", re.I)),
    ("cosmology_wa", re.compile(rf"\b(?:w_?a|wₐ)[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}\b", re.I)),
    ("cosmology_sigma8", re.compile(
        rf"\b(?:sigma_?8|σ_?8)[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}\b",
        re.I,
    )),
    ("cosmology_s8", re.compile(
        rf"\b(?:S_?8)[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}\b",
        re.I,
    )),
    ("cmb_rotation_beta", re.compile(
        rf"\b(?:beta(?:_?deg)?|β|alpha(?:_?deg)?|α)[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}\s*(?:deg|degree|degrees|°)?\b",
        re.I,
    )),
    ("cmb_rotation_acb", re.compile(
        rf"\b(?:A[_\s-]?CB|A_\{{CB\}})[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}\b",
        re.I,
    )),
    ("significance_sigma", re.compile(
        rf"\b{_NUM}\s*(?:σ|sigma)\b",
        re.I,
    )),
    ("p_value", re.compile(
        rf"\bp[-\s]?value\s*(?:is|was|=|≈|~|:|about|approximately)?\s*{_NUM}\b",
        re.I,
    )),
    ("correlation_r", re.compile(
        rf"\b(?:Pearson\s+)?r\s*(?:is|was|=|≈|~|:|about|approximately)?\s*{_NUM}\b",
        re.I,
    )),
    ("log_g", re.compile(rf"\blog\s*g\s*[=≈~]\s*{_NUM}\b", re.I)),
    ("metallicity", re.compile(rf"\[Fe\s*/\s*H\]\s*[=≈~]\s*{_NUM}\b", re.I)),
    ("e_bv", re.compile(rf"E\s*\(\s*B\s*[-−]\s*V\s*\)\s*[=≈~]\s*{_NUM}\b", re.I)),
    ("a_v", re.compile(rf"\bA_?V\s*[=≈~]\s*{_NUM}\b", re.I)),
    ("mass_solar", re.compile(rf"{_NUM}\s*(?:M_sun|M☉|solar\s*mass(?:es)?)\b", re.I)),
    ("luminosity_solar", re.compile(rf"{_NUM}\s*(?:L_sun|L☉|solar\s*luminosit(?:y|ies))\b", re.I)),
    # W1: allow stacking markers so `age: ~100 Myr` / `age = ~100 Myr` /
    # `age is approximately 100 Myr` all match (previously only single-marker
    # forms like `age ~100 Myr` were captured).
    ("age_gyr", re.compile(rf"\bage(?:\s*(?:of|=|≈|~|is|:|：|about|approximately|roughly|around))*\s*{_NUM}\s*Gyr\b", re.I)),
    ("age_myr", re.compile(rf"\bage(?:\s*(?:of|=|≈|~|is|:|：|about|approximately|roughly|around))*\s*{_NUM}\s*Myr\b", re.I)),
    ("age_myr", re.compile(
        rf"\b(?:cluster\s+age|age\s+estimate|best[-\s]*fit\s+age|"
        rf"literature\s+age|typical\s+(?:age\s+)?(?:for|of)\s+[^.\n;:()]*?)"
        rf"[^.\n]*?{_NUM}\s*Myr\b|"
        rf"\b{_NUM}\s*Myr\b[^.\n]{{0,120}}\b(?:age|old|literature|typical)\b",
        re.I,
    )),
    ("teff_k", re.compile(rf"\bT(?:_?eff)?\s*[=≈~]\s*{_NUM}\s*K\b", re.I)),
    ("distance_pc", re.compile(rf"\bdistance\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*pc\b", re.I)),
    ("distance_kpc", re.compile(rf"\bdistance\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*kpc\b", re.I)),
    ("distance_mpc", re.compile(rf"\bdistance\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*Mpc\b", re.I)),
    ("period_days", re.compile(rf"\bperiod\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*days?\b", re.I)),
    ("period_change_rate", re.compile(
        rf"\b(?:dP\s*/\s*dt|period\s+(?:change|derivative)|"
        rf"rate\s+of\s+period\s+change)[^.\n;:()]*?{_NUM}"
        rf"(?:\s*(?:day\s*/\s*day|d\s*/\s*d|s\s*/\s*yr|"
        rf"sec(?:ond)?s?\s*/\s*yr))?",
        re.I,
    )),
    ("period_ppm", re.compile(
        rf"\b{_NUM}\s*ppm\b[^.\n;:()]*\b(?:period|change|stability|evolution)\b|"
        rf"\b(?:period|change|stability|evolution)[^.\n;:()]*?{_NUM}\s*ppm\b",
        re.I,
    )),
    # Pre-PART-W Chinese patterns (period / distance): kept for backward test compatibility.
    # X (PART X plan D): after replies are forced to English these two patterns almost never
    # trigger — CJK-containing replies are already hard-blocked upstream. Kept only as a last
    # fallback.
    ("period_days_zh", re.compile(rf"(?:周期|脉动周期|轨道周期)\s*(?:值)?\s*(?:为|是|=|≈|~|约为)?\s*{_NUM}\s*(?:天|日)\b", re.I)),
    ("distance_pc_zh", re.compile(rf"(?:距离|距离估算|距离约为)\s*(?:值)?\s*(?:为|是|=|≈|~|约为)?\s*{_NUM}\s*pc\b", re.I)),
    ("percent_claim", re.compile(
        rf"(?:误差|偏差|相对误差|差异|agreement|error|offset)\s*(?:为|是|=|≈|~|about|around|approximately|approx\.?)?\s*{_NUM}\s*%",
        re.I,
    )),
    ("parallax_mas", re.compile(rf"\bparallax\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*mas\b", re.I)),
    ("proper_motion", re.compile(rf"\bproper\s*motion\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*mas", re.I)),
    ("radial_velocity", re.compile(rf"\b(?:radial\s*velocity|RV)\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*km/?s", re.I)),
    ("magnitude", re.compile(rf"\b(?:V|G|B|R|J|H|K)\s*[=≈~]\s*{_NUM}\s*(?:mag)?\b", re.I)),

    # F1.1: labelled colon form — e.g. "Mean Parallax: 7.353 ± 0.001 mas",
    # "Distance: 136.0 ± 0.0 pc", "Member Star Count: 776 stars".  The
    # Pleiades reviewer saw every fabricated number rendered this way;
    # the original regex only handled equals / prose prefixes.
    ("label_colon", re.compile(
        rf"\b(?:mean\s+parallax|weighted\s+mean|parallax|distance|redshift|age|"
        rf"mass|luminosity|metallicity|log\s*g|temperature|T_?eff|period|"
        rf"proper\s+motion|pmra|pmdec|RV|radial\s+velocity|magnitude|mag|"
        rf"member\s+(?:count|star\s+count|number)|star\s+count|sample\s+size|"
        rf"source\s+count|row\s+count|N_(?:stars|members|sources)|"
        rf"fap|false\s+alarm\s+probability|chi[-\s]?squared?|reduced\s+chi2|"
        rf"r\^?2|ess|rhat|hdi)\s*[:=]\s*{_NUM}",
        re.I,
    )),

    # R14: synthetic diagnostic code often outputs summary statistics without
    # astronomical units (e.g. "mean=3.0", "std≈1.414"). These are still
    # numeric claims and must not be laundered from SYNTHETIC stdout.
    ("summary_stat", re.compile(
        rf"\b(?:mean|average|avg|std|standard\s+deviation|sigma)\s*"
        rf"(?:"
        rf"[:=≈~]\s*"
        rf"|(?:is|was|of|at)\s+(?:(?:about|around|approximately|approx\.?)\s+)?"
        rf"|(?:about|around|approximately|approx\.?)\s+"
        rf")"
        rf"{_NUM}\b",
        re.I,
    )),

    # F1.1: integer cardinal counts with a noun.  Captures "776 stars",
    # "1000 members", "250 sources" — the Pleiades fabrication included
    # "Member Star Count: 776 stars" which the old patterns missed
    # entirely (they only looked at physical quantities, not cardinalities).
    ("count_with_noun", re.compile(
        r"\b(\d{2,7})\s+(?:stars?|members?|sources?|objects?|galaxies?|"
        r"candidates?|rows?|targets?|samples?|points?|detections?|"
        r"quasars?|AGNs?|supernovae?|transients?|variables?|cepheids?|"
        r"eclipsing\s+binaries|clusters?|pulsars?|exoplanets?|planets?)\b",
        re.I,
    )),

    # F1.1 + L1: ± uncertainty pair — both the central value AND the error
    # are claims that must be backed by tool results.  The reviewer's
    # "7.353 ± 0.001 mas" exposed the gap: an error bar of 0.001 mas on
    # 776 stars is physically impossible, but the value 7.353 alone
    # could still match a tool output at ±1%.  Forcing both-match
    # tightens the gate by the second decimal.
    #
    # L1 (2026-04-20 audit): added missing spectral/X-ray/radio/high-z units:
    # Å / nm / μm / Gpc / keV / eV / MeV / erg·s⁻¹·cm⁻² / μJy / Jy / THz /
    # kHz.  Without these units, claims like "6563 Å ± 1 Å" and
    # "L_X = 1e44 erg/s ± 1e42" were not extracted and the zero-hallucination
    # gate was silently bypassed.
    ("value_with_error", re.compile(
        rf"{_NUM}\s*(?:±|\+/-|\+-)\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?)"
        rf"\s*(?:mas|pc|kpc|Mpc|Gpc|deg|arcmin|arcsec|km/?s|mag|dex|Gyr|Myr|yr|days?|"
        rf"K|eV|keV|MeV|GeV|"
        rf"M_sun|L_sun|AU|"
        rf"Hz|kHz|MHz|GHz|THz|"
        rf"Å|nm|μm|um|mm|"
        rf"erg(?:/s)?(?:/cm\^?2)?|Jy|mJy|μJy|uJy)\b",
        re.I,
    )),

    # L1 (audit 2026-04-20): bare-unit form (no ± error, just "value unit").
    # The prior label_colon pattern required a keyword prefix (period/distance/...),
    # and value_with_error required a ± symbol, leaving this common form uncovered:
    #   "Hα emission at 6563 Å", "L_X = 1.5e44 erg/s", "peak at 1.4 GHz",
    #   "flux 12.3 mJy", "at z=0.5 the luminosity is 3e10 L_sun".
    # Coverage matches value_with_error to ensure wavelength/frequency/flux/energy
    # claims all go through the same matching path.
    ("value_bare_unit", re.compile(
        rf"{_NUM}\s*"
        rf"(mas|pc|kpc|Mpc|Gpc|arcmin|arcsec|km/?s|mag|dex|Gyr|Myr|"
        rf"eV|keV|MeV|GeV|"
        rf"M_sun|L_sun|AU|"
        rf"kHz|MHz|GHz|THz|"
        rf"Å|nm|μm|um|mm|"
        rf"erg/s/cm\^?2|erg/s|Jy|mJy|μJy|uJy)\b",
        re.I,
    )),

    # F1.1: coordinate pairs "RA = X, Dec = Y".  Previously the
    # `distance`/`parallax` regexes covered many things but RA/Dec
    # could slip through if the model invented them.
    ("ra_dec_pair", re.compile(
        rf"\bRA\s*[=:]\s*{_NUM}[,\s]+Dec\s*[=:]\s*{_NUM}",
        re.I,
    )),
    # ── M0 Commit 5 (2026-05-18): solar_system numeric standards ──
    # Prevents the LLM from fabricating small-body orbital/physical quantities
    # without first querying MPC/Horizons/SBDB.
    ("semi_major_axis_au", re.compile(
        rf"\ba\s*[=≈~]\s*{_NUM}\s*(?:au|AU)\b", re.I,
    )),
    ("eccentricity", re.compile(
        # e ∈ [0, 1): restrict to 0 or 0.xxx (values ≥1 are excluded to avoid false matches with emcee/exoplanet ratios)
        r"\b(?:e|eccentricity)\s*[=≈~]\s*(0(?:\.\d+)?)(?!\d)", re.I,
    )),
    ("orbital_inclination_deg", re.compile(
        # `°` 是 non-word; 末尾 `\b` 只挂英文单位上, `°` 后不需 \b.
        rf"\b(?:i|inclination)\s*[=≈~]\s*{_NUM}\s*(?:°|deg(?:rees?)?\b)", re.I,
    )),
    ("diameter_km", re.compile(
        rf"\b(?:D|diameter)\s*[=≈~]\s*{_NUM}\s*km\b", re.I,
    )),
    ("albedo_pV", re.compile(
        r"\b(?:p_?V|geometric\s+albedo|albedo)\s*[=≈~]\s*0?\.\d+\b", re.I,
    )),
    ("Afrho_cm", re.compile(
        rf"\bAf\s*ρ?\s*[=≈~]\s*{_NUM}\s*cm\b", re.I,
    )),
    ("MOID_au", re.compile(
        rf"\bMOID\s*[=≈~]\s*{_NUM}\s*(?:au|AU)\b", re.I,
    )),
    ("phase_angle_deg", re.compile(
        rf"(?:α|phase\s+angle)\s*[=≈~]\s*{_NUM}\s*(?:°|deg(?:rees?)?\b)", re.I,
    )),
    # ── M0 2026-05-20: exoplanet numeric standards ──
    # Prevents the LLM from fabricating planet/host parameters without first
    # querying NASA Exoplanet Archive or TESS.
    ("planet_radius_re", re.compile(
        rf"\b(?:R(?:_p|p))\s*[=≈~]\s*{_NUM}\s*R(?:_?E(?:arth)?|⊕)", re.I,
    )),
    ("planet_mass_me", re.compile(
        rf"\b(?:M(?:_p|p))\s*[=≈~]\s*{_NUM}\s*M(?:_?E(?:arth)?|⊕)", re.I,
    )),
    ("orbital_period_days", re.compile(
        rf"\b(?:P|orbital\s+period)\s*[=≈~]\s*{_NUM}\s*(?:d|days?)\b", re.I,
    )),
    ("equilibrium_temperature_K", re.compile(
        rf"\bT(?:_eq|eq)\s*[=≈~]\s*{_NUM}\s*K\b", re.I,
    )),
    ("transit_depth_ppm", re.compile(
        rf"\b(?:transit\s+)?depth\s*[=≈~]\s*{_NUM}\s*ppm\b", re.I,
    )),
]

_NUMBER_WORD_DIGITS: dict[str, str] = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9",
}
_NUMBER_WORD_INTS: dict[str, int] = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_NUMBER_WORD_TOKEN = (
    r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety)"
)
_SPELLED_NUMBER_PATTERN = re.compile(
    rf"\b(?:mean|average|avg|std|standard\s+deviation|sigma|period|"
    rf"distance|redshift|age|mass|radius|ratio|depth|rms|chi[-\s]?squared?)"
    rf"\s*(?:is|was|of|at|=|≈|~|:)?\s+"
    rf"({_NUMBER_WORD_TOKEN}(?:[-\s]{_NUMBER_WORD_TOKEN})?"
    rf"(?:\s+point\s+{_NUMBER_WORD_TOKEN}(?:\s+{_NUMBER_WORD_TOKEN})*)?)\b",
    re.I,
)


def _spelled_number_to_float(raw: str) -> float | None:
    """Convert a simple English number phrase to float; used only as a claim-extraction fallback."""
    tokens = re.sub(r"[-_]", " ", raw.lower()).split()
    if not tokens:
        return None
    if "point" in tokens:
        idx = tokens.index("point")
        left_tokens = tokens[:idx]
        right_tokens = tokens[idx + 1:]
        if not right_tokens or any(tok not in _NUMBER_WORD_DIGITS for tok in right_tokens):
            return None
        left = _spelled_integer_to_int(left_tokens) if left_tokens else 0
        if left is None:
            return None
        return float(f"{left}.{''.join(_NUMBER_WORD_DIGITS[tok] for tok in right_tokens)}")
    integer = _spelled_integer_to_int(tokens)
    return float(integer) if integer is not None else None


def _spelled_integer_to_int(tokens: list[str]) -> int | None:
    if not tokens:
        return 0
    if any(tok not in _NUMBER_WORD_INTS for tok in tokens):
        return None
    total = 0
    for tok in tokens:
        total += _NUMBER_WORD_INTS[tok]
    return total


@dataclass
class Claim:
    label: str
    raw: str               # the literal span captured from the reply
    value: float           # parsed numeric value
    start: int             # character offset in the reply
    end: int


@dataclass
class ValidationResult:
    ok: bool
    claims: list[Claim] = field(default_factory=list)
    uncited: list[Claim] = field(default_factory=list)
    # F1.5: snapshot of the numeric universe harvested from tool_results
    # so the block message can show the user what tools actually produced.
    universe_sample: list[float] = field(default_factory=list)
    universe_size: int = 0
    strict_mode: bool = False

    def describe(self) -> str:
        """Human-readable summary used for the LLM regeneration prompt."""
        if not self.uncited:
            return "All numeric claims are supported by tool results."
        lines = [
            f"- {c.label} = {c.value} (phrase: \"{c.raw}\")"
            for c in self.uncited
        ]
        universe_note = (
            f"Tools returned {self.universe_size} distinct numeric values this turn"
            + (f" (sample: {self.universe_sample[:20]})" if self.universe_sample else " (empty)")
        )
        strict_note = (
            "\n\n[Strict mode is ON — tolerance tightened to 0.1% because the "
            "tool-result universe was thin (<10 entries).]"
            if self.strict_mode else ""
        )
        return (
            "The following numeric claims were NOT found in any tool_result "
            "this turn and must be removed or replaced with "
            "'not determined by my tools':\n" + "\n".join(lines)
            + "\n\n" + universe_note + strict_note
        )


@dataclass
class CitationViolation:
    kind: str
    match_text: str
    line_number: int


_UNSUPPORTED_NARRATIVE_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "literature_fallback",
        re.compile(
            r"\b(?:literature\s+values?|from\s+the\s+literature|"
            r"typical\s+(?:for|of|from)\s+[^.\n;:()]{0,80}literature|"
            r"(?:known|textbook)\s+values?)\b",
            re.I,
        ),
    ),
    (
        "unsupported_history",
        re.compile(
            r"\b(?:Goodricke|prototype\s+Classical\s+Cepheid|"
            r"monitored\s+for\s+[^.\n;:()]{0,40}(?:years?|centur(?:y|ies))|"
            r"(?:monitored|observed|known|studied|tracked|discovered|discovery)"
            r"[^.\n;:()]{0,80}\bsince\s+\d{4}|"
            r"\bsince\s+\d{4}[^.\n;:()]{0,80}\b(?:discovery|Goodricke|observations?|monitoring)|"
            r"stability\s+over\s+centur(?:y|ies)|remarkable\s+stability\s+over\s+centur(?:y|ies))\b",
            re.I,
        ),
    ),
    (
        "unsupported_period_change",
        re.compile(
            r"\b(?:dP\s*/\s*dt|period\s+(?:change|derivative)|"
            r"evolutionary\s+change|expected\s+evolutionary\s+change|"
            r"stability\s+over\s+centur(?:y|ies)|day\s*/\s*day|"
            r"sec(?:ond)?s?\s*/\s*yr)\b",
            re.I,
        ),
    ),
    (
        "unsupported_line_property_relation",
        re.compile(
            r"\b(?:(?:log\s*)?L\s*\[?\s*C\s*II\s*\]?|L_?CII|LCII|"
            r"line\s+luminosit(?:y|ies))[^.\n;:()]{0,120}"
            r"(?:FWHM|line\s+width|velocity\s+dispersion|relation|correlation)\b|"
            r"\b(?:FWHM|line\s+width|velocity\s+dispersion)[^.\n;:()]{0,120}"
            r"(?:(?:log\s*)?L\s*\[?\s*C\s*II\s*\]?|L_?CII|LCII|"
            r"line\s+luminosit(?:y|ies))\b",
            re.I,
        ),
    ),
    (
        "unsupported_line_property_relation",
        re.compile(
            r"\b(?:typically|generally|usually|commonly|often)[^.\n;:()]{0,80}"
            r"(?:range|ranges|span|spans|between)[^.\n;:()]{0,140}"
            r"(?:L\s*\[?\s*C\s*II\s*\]?|L_?CII|LCII|"
            r"line\s+luminosit(?:y|ies)|FWHM|line\s+width|velocity\s+width|"
            r"L☉|L_sun|km\s*/?\s*s|km\s*s-?1)|"
            r"\b(?:L\s*\[?\s*C\s*II\s*\]?|L_?CII|LCII|line\s+luminosit(?:y|ies)|"
            r"FWHM|line\s+width|velocity\s+width)[^.\n;:()]{0,100}"
            r"(?:typically|generally|usually|commonly|often)[^.\n;:()]{0,80}"
            r"(?:range|ranges|span|spans|between)\b",
            re.I,
        ),
    ),
    (
        "unsupported_bao_bin_anomaly",
        re.compile(
            r"\b(?:DESI|BAO|LRG)[^.\n;:()]{0,120}"
            r"(?:z_?eff|z\s*=|redshift)[^.\n;:()]{0,40}"
            r"(?:0\.51|0\.510|0\.5)[^.\n;:()]{0,160}"
            r"(?:tension|outlier|anomal(?:y|ous)|deviation|pull|high|low)\b|"
            r"\b(?:tension|outlier|anomal(?:y|ous)|deviation|pull)[^.\n;:()]{0,120}"
            r"(?:DESI|BAO|LRG)[^.\n;:()]{0,120}"
            r"(?:z_?eff|z\s*=|redshift)[^.\n;:()]{0,40}(?:0\.51|0\.510|0\.5)\b",
            re.I,
        ),
    ),
]


def _strip_markdown_code(text: str) -> str:
    """Strip markdown code blocks before extracting prose numeric claims.

    Tool schema / help replies often contain parameter defaults like ``limit: 24``
    or SQL examples. These are interface metadata, not astronomical conclusions,
    and must not enter the zero-fabrication gate.
    """
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", " ", text)
    return text


def _strip_thousands_separators(text: str) -> str:
    """Normalize thousands separators (1,234 -> 1234) so the numeric matcher
    captures the whole value instead of splitting on the comma. Only a comma
    between a digit and exactly three trailing digits is removed, so list
    separators like "ra, dec" or "1, 2, 3" stay untouched."""
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"(\d),(\d{3})(?=\D|$)", r"\1\2", text)
    return text


# P0-b (2026-05-26): the model and astronomers routinely write large / small
# magnitudes as "A × 10^B" / "A x 10^B" / "A·10**B" / "A × 10⁸" (superscript)
# rather than the "AeB" form `_NUM` understands.  Without this, a claim like
# "3.5 × 10^8 M_sun" is parsed by the mass_solar pattern as the bare exponent
# digit (8), so the real value 3.5e8 escapes the numeric gate entirely.  The
# module docstring already promised "1.2 × 10^-3" support; this implements it.
# We rewrite the power-of-ten form into equivalent e-notation so every
# existing pattern keeps working unchanged.  Pure e-notation (1.2e-3) is
# already handled by `_NUM` and is left untouched.
_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
_SCI_SUPERSCRIPT = re.compile(r"10\s*([⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+)")
# mantissa × 10^exp → mantissa e exp.  Product sign: × / x / X / · / ∙ / ⋅ / *.
# Exponent introduced by ^ or **.  The mantissa must be a number, so prose
# like "box 10^3" (x not preceded by a digit) never matches.
_SCI_MANTISSA_POWER = re.compile(
    r"([-+]?\d+(?:\.\d+)?)\s*[×✕⨯xX·∙⋅*]\s*10\s*(?:\^|\*\*)\s*([-+]?\d+)"
)
# bare 10^exp with an implicit mantissa of 1 ("~10^8 M_sun")
_SCI_BARE_POWER = re.compile(r"(?<![\d.eE])10\s*(?:\^|\*\*)\s*([-+]?\d+)")


def _normalize_sci_notation(text: str) -> str:
    """Rewrite power-of-ten notation into e-notation that ``_NUM`` understands.

    ``3.5 × 10^8`` / ``3.5 x 10^8`` / ``3.5·10**8`` / ``3.5 × 10⁸`` all become
    ``3.5e8``; a bare ``10^8`` / ``10⁸`` becomes ``1e8``.  Pure ``e``-notation
    (``1.2e-3``) is already understood by ``_NUM`` and is left untouched.
    """
    # 1) superscript exponent → caret form: "10⁻³" → "10^-3"
    text = _SCI_SUPERSCRIPT.sub(
        lambda m: "10^" + m.group(1).translate(_SUPERSCRIPT_DIGITS), text
    )
    # 2) mantissa × 10^exp → mantissa e exp
    text = _SCI_MANTISSA_POWER.sub(r"\1e\2", text)
    # 3) leftover bare 10^exp → 1e exp
    text = _SCI_BARE_POWER.sub(r"1e\1", text)
    return text


def _apply_regex_with_map(
    text: str, bmap: list[int], pattern: re.Pattern, repl_fn: Any
) -> tuple[str, list[int]]:
    """Apply one regex substitution while maintaining a boundary index map.

    `bmap` has length ``len(text) + 1``; ``bmap[i]`` is the offset in the
    ORIGINAL reply that boundary ``i`` of the current `text` corresponds to.
    We return the new text and an updated boundary map so a span found in the
    fully-transformed text can be translated back to the original reply's
    character offsets (which the redaction / line-number helpers slice).

    Each replaced span collapses to its replacement string: the replacement's
    left boundary keeps the match-start original offset and its right boundary
    keeps the match-end original offset, so redacting ``[orig_start, orig_end)``
    covers exactly the original characters the claim was extracted from.
    """
    out_chars: list[str] = []
    out_map: list[int] = []
    pos = 0
    for m in pattern.finditer(text):
        # Copy the untouched run before this match (identity boundaries).
        for i in range(pos, m.start()):
            out_map.append(bmap[i])
            out_chars.append(text[i])
        replacement = repl_fn(m)
        if replacement:
            # Left boundary → original match-start; any interior boundaries
            # also pin to match-start; the trailing char's right boundary is
            # supplied by the next appended boundary (match-end) below.
            for _ in replacement:
                out_map.append(bmap[m.start()])
            out_chars.append(replacement)
        pos = m.end()
    for i in range(pos, len(text)):
        out_map.append(bmap[i])
        out_chars.append(text[i])
    out_map.append(bmap[len(text)])  # final right boundary
    return "".join(out_chars), out_map


def _transform_for_claims(text: str) -> tuple[str, list[int]]:
    """Run the claim-extraction text transforms, tracking original offsets.

    Returns ``(transformed_text, bmap)`` where ``bmap[i]`` maps a boundary in
    the transformed text back to the corresponding offset in the input `text`.
    Mirrors the transform pipeline previously inlined in ``extract_claims``
    (``−``→``-``, strip code blocks/spans, strip thousands separators, then the
    three sci-notation rewrites) so matching behaviour is unchanged, but lets a
    matched span be mapped to the original reply for redaction / line numbers.
    """
    # B15: U+2212 → ASCII '-' is length-preserving (single code point each),
    # so it maps 1:1 and needs no map adjustment.
    text = text.replace("−", "-")
    bmap = list(range(len(text) + 1))
    # _strip_markdown_code: code blocks then inline code spans → single space.
    text, bmap = _apply_regex_with_map(
        text, bmap, re.compile(r"```.*?```", re.DOTALL), lambda m: " "
    )
    text, bmap = _apply_regex_with_map(
        text, bmap, re.compile(r"`[^`\n]*`"), lambda m: " "
    )
    # _strip_thousands_separators: "1,234" → "1234" (repeat to chain groups).
    prev = None
    while prev != text:
        prev = text
        text, bmap = _apply_regex_with_map(
            text, bmap, re.compile(r"(\d),(\d{3})(?=\D|$)"),
            lambda m: m.group(1) + m.group(2),
        )
    # _normalize_sci_notation steps 1-3.
    text, bmap = _apply_regex_with_map(
        text, bmap, _SCI_SUPERSCRIPT,
        lambda m: "10^" + m.group(1).translate(_SUPERSCRIPT_DIGITS),
    )
    text, bmap = _apply_regex_with_map(
        text, bmap, _SCI_MANTISSA_POWER, lambda m: f"{m.group(1)}e{m.group(2)}"
    )
    text, bmap = _apply_regex_with_map(
        text, bmap, _SCI_BARE_POWER, lambda m: f"1e{m.group(1)}"
    )
    return text, bmap


def extract_claims(text: str) -> list[Claim]:
    """Scan a reply for astronomical numeric claims.

    F1.1: multi-group patterns (value_with_error, ra_dec_pair) emit one
    Claim per captured group so both the central value AND the error bar
    (or both RA AND Dec) must match tool output.

    L1 (audit 2026-04-20): post-process deduplication — when two patterns capture
    the **same numeric value** with overlapping spans, keep the "more specific"
    match (typically the longer span that includes a label prefix). For example,
    "parallax is 9.00 mas" is matched by both parallax_mas (span 4-24) and
    value_bare_unit (span 16-24); only the former is kept. This avoids missing
    spectral-unit claims while preventing double-counting.
    """
    # B15: LLMs routinely emit the typographic Unicode minus U+2212 in
    # scientific prose ("w0 = −0.84"). `_NUM` only accepts ASCII -/+, so an
    # un-normalised minus made the whole numeric claim invisible to the gate.
    #
    # The code-strip / thousands-separator / sci-notation transforms are
    # length-CHANGING, so matched spans live in transformed coordinates. We
    # keep a boundary map (`bmap`) back to the ORIGINAL reply and store each
    # claim's start/end in original coordinates, because downstream redaction
    # (`_redact_uncited_phrases`) and line-number helpers slice the original
    # reply with these offsets.
    text, bmap = _transform_for_claims(text)
    claims: list[Claim] = []
    seen: set[tuple[int, int, float]] = set()
    for label, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            span = match.span()
            # Figure out how many capturing groups contain numeric values.
            # All patterns have group(1); value_with_error + ra_dec_pair
            # also have group(2).
            for grp_idx in range(1, (pattern.groups or 1) + 1):
                try:
                    raw_num = match.group(grp_idx)
                except IndexError:
                    continue
                if raw_num is None:
                    continue
                try:
                    value = float(raw_num)
                except ValueError:
                    continue
                if not math.isfinite(value):
                    continue
                key = (span[0], span[1], value)
                if key in seen:
                    continue
                seen.add(key)
                claim_label = label
                if pattern.groups and pattern.groups > 1 and label not in {"age_myr", "period_ppm"}:
                    claim_label = f"{label}.g{grp_idx}"
                claims.append(Claim(
                    label=claim_label,
                    raw=match.group(0).strip(),
                    value=value,
                    start=bmap[span[0]],
                    end=bmap[span[1]],
                ))
    for match in _SPELLED_NUMBER_PATTERN.finditer(text):
        value = _spelled_number_to_float(match.group(1))
        if value is None or not math.isfinite(value):
            continue
        claims.append(Claim(
            label="spelled_number",
            raw=match.group(0).strip(),
            value=value,
            start=bmap[match.start()],
            end=bmap[match.end()],
        ))

    # L1: span-overlap dedup.  Two patterns may both match the same numeric
    # value at overlapping character ranges ("parallax is 9.00 mas" + "9.00
    # mas"); keep only the one with the wider span (= more context).
    if len(claims) <= 1:
        return claims
    # Sort by span length descending so the widest-context claim wins.
    claims_sorted = sorted(claims, key=lambda c: -(c.end - c.start))
    kept: list[Claim] = []
    for c in claims_sorted:
        redundant = False
        for k in kept:
            # overlap + same value (<1e-9 absolute diff) → dedup
            if abs(c.value - k.value) < 1e-9 and not (c.end <= k.start or c.start >= k.end):
                redundant = True
                break
        if not redundant:
            kept.append(c)
    # Restore original left-to-right text order so downstream rendering /
    # user messages remain readable.
    kept.sort(key=lambda c: c.start)
    return kept


def _is_tainted_synthetic_payload(payload: Any) -> bool:
    """Return True when a tool payload must not support reply claims."""
    if not isinstance(payload, dict):
        return False
    status_values: list[str] = []
    for key in ("analysis_status", "__tool_status__", "status", "data_origin"):
        value = payload.get(key)
        if isinstance(value, str):
            status_values.append(value.strip().upper())
    return (
        payload.get("__do_not_claim__") is True
        or "SYNTHETIC" in status_values
        or "SIMULATED_DEMO" in status_values
    )


# L2 (audit 2026-04-20): metadata key blacklist.  Root cause of the Pleiades
# "776 stars" laundering — the tool returned `{"row_count": 776}`, the AI said
# "776 member stars", and the validator found 776 in the numeric pool (sourced
# from the system field row_count) and passed it through. After the audit,
# numbers attached to these **systemic metadata keys** are excluded from the
# pool. Note: only the top-level key is skipped; nested values are unaffected
# (if a data column happens to be named row_count, only that one layer is
# excluded — acceptable scope).
_METADATA_KEYS_BLACKLIST: frozenset[str] = frozenset({
    # Query metadata
    "row_count", "showing", "has_data", "truncated",
    "elapsed_seconds", "elapsed_ms", "timeout_s",
    "timestamp", "timestamp_utc", "created_at", "updated_at",
    # HTTP / retry metadata
    "status_code", "http_status", "attempts", "retry_count",
    # Identity / reproducibility envelope
    "run_id", "query_hash", "tool_version", "archive_version",
    "random_seed", "session_id", "user_id", "chat_session_id",
    "python_session_id",
    # Model configuration / diagnostics. These may contain numbers close to
    # scientific claims (e.g. H0 prior bounds [50, 90]) but are not posterior
    # measurements and must not support reply claims.
    "priors", "prior", "thresholds", "proposal", "ref",
    "package_versions", "cobaya_info",
    "n_walkers", "n_steps", "n_burn", "n_samples", "n_rows",
    "input_rows_verified",
    # Result status flags (mostly strings or bools, but occasionally a code)
    "success", "error_class", "argument", "error_code",
    "analysis_status", "__tool_status__", "data_origin",
    # Offset / pagination
    "offset", "limit", "per_page", "page", "total_pages",
    # Row-count metadata (same category as row_count)
    "num_rows", "num_cols", "n_rows", "n_cols", "total_count",
})


# 2026-05-28: citation-identifier keys (bibcode / doi / arxiv / URL / etc.)
# carry a lot of digits that are NOT measurements — bibcode volumes / page
# numbers, DOI fragments, arXiv IDs, publication years, ADS URLs.
# Harvesting those into the numeric universe lets a fabricated claim match
# accidentally (e.g. an LLM claim of "641" matches the volume parsed out of
# "2020A&A...641A...6P", and the claim_validator's anti-fabrication contract
# is silently bypassed via citation-string laundering).
# Skipping the entire subtree because nothing under these keys is a
# measurement — they're identifiers / references.
_CITATION_KEYS_BLACKLIST: frozenset[str] = frozenset({
    # Direct paper identifiers
    "bibcode", "bibcodes", "tcmb_bibcode",
    "doi", "dois", "doi_url",
    "arxiv", "arxiv_id", "arxiv_ids",
    "pmid", "ads_url", "url", "source_url",
    # Container fields whose values are lists/dicts of identifier objects.
    # Skipping the container drops the embedded labels too (e.g. "Riess+2022")
    # which would otherwise leak the year as a numeric token.
    "citations", "manual_attestation", "references", "reference",
    "data_products",
})


# P0-a (2026-05-26): free-text / diagnostic fields carry prose numbers (years,
# version strings, banner text, suggested next steps) that are NOT
# observational values.  Harvesting digits from them inflates the numeric
# universe, and a large universe lets a fabricated claim accidentally match
# some unrelated number within the ±1 % tolerance.  Root offenders are the
# result_provenance banner fields (`__message_to_model__`,
# `__suggested_next_step__`, `__partial_output__`) injected on every
# EMPTY/FAILED tool, plus generic prose keys.  We skip digit extraction from
# the STRING value of these keys only — numeric values and nested data
# structures under them (rare) are still walked, so real data rows that happen
# to live under, say, `details` are not lost.
_FREETEXT_KEYS: frozenset[str] = frozenset({
    # result_provenance banner fields
    "__message_to_model__", "__suggested_next_step__", "__partial_output__",
    "__do_not_claim__",
    # generic prose / diagnostic fields
    "message", "msg", "note", "notes", "error", "error_message",
    "detail", "details", "rationale", "explanation", "reason",
    "suggestion", "suggestions", "description", "summary", "text",
    "markdown", "banner", "warning", "warnings", "hint", "hints",
    "comment", "comments", "title", "label", "caption", "guidance",
})


def _iter_numeric_values(payload: Any, _in_blacklisted_key: bool = False) -> Iterable[float]:
    """Yield every finite numeric scalar from claimable tool payloads.

    L2: skip top-level fields in _METADATA_KEYS_BLACKLIST — the AI must not
    cite numbers from system fields like `row_count`, `timestamp`, or
    `status_code` as observational results. _in_blacklisted_key propagates
    downward: if the outer key is row_count, the entire subtree is excluded.
    """
    if _in_blacklisted_key:
        return
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        v = float(payload)
        if math.isfinite(v):
            yield v
    elif isinstance(payload, dict):
        if _is_tainted_synthetic_payload(payload):
            return
        for key, val in payload.items():
            # L2: skip the entire systemic metadata field
            key_str = str(key).lower() if not isinstance(key, str) else key.lower()
            if key_str in _METADATA_KEYS_BLACKLIST:
                continue
            # 2026-05-28: skip the entire subtree under citation-identifier
            # keys — bibcode / doi / arxiv / url / citations / references all
            # carry digits that are identifiers, not measurements, and a
            # fabricated claim must not legitimize itself by matching the
            # parsed-out volume / year / arxiv-id of a cited paper.
            if key_str in _CITATION_KEYS_BLACKLIST:
                continue
            # P0-a: don't harvest prose digits from free-text field strings
            # (banner text, error messages, suggestions).  Nested structures
            # and numeric values under these keys are still walked.
            if key_str in _FREETEXT_KEYS and isinstance(val, str):
                continue
            yield from _iter_numeric_values(val)
    elif isinstance(payload, list):
        for val in payload:
            yield from _iter_numeric_values(val)
    elif isinstance(payload, str):
        # A tool may serialise numbers inside a string (common for CSV /
        # preview rows).  Extract float-looking tokens cheaply.  P0-b: also
        # normalise "A × 10^B" power-of-ten forms so a cached "3.5 × 10^8"
        # data value lands in the universe in the same shape extract_claims sees.
        normalized = _normalize_sci_notation(payload)
        for token in re.findall(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?", normalized):
            try:
                v = float(token)
            except ValueError:
                continue
            if math.isfinite(v):
                yield v


def _matches_any(value: float, universe: set[float], tolerance: float) -> bool:
    if value in universe:
        return True
    if value == 0.0:
        # Tolerance-0 is impossible; accept any existing 0.
        return 0.0 in universe
    # B5: compare with SIGN preserved. The previous abs()-based match let a
    # sign-flipped claim (e.g. "w0 = 0.84") validate against the tool's
    # opposite-sign value (w0 = -0.84) — but for w0/wa and similar the sign IS
    # the physical conclusion (phantom vs quintessence). Build the tolerance
    # band around the signed value (sorted, since a negative value flips
    # lo/hi) and require the candidate to fall inside it with its own sign.
    lo, hi = sorted((value * (1 - tolerance), value * (1 + tolerance)))
    for candidate in universe:
        if candidate == 0.0:
            continue
        if lo <= candidate <= hi:
            return True
    return False


# ── 4.1 (2026-05-29): label-aware cosmology-parameter matching ───────────────
# The flat numeric universe is label-blind: a claim "σ8 = 0.315" was "supported"
# by ANY tool number within ±1% — e.g. an unrelated Ωm=0.3153 — so a cosmology
# parameter value could *launder* off a coincidentally-close number for a
# DIFFERENT quantity.  For claims we can pin to a specific cosmology parameter,
# we instead match ONLY against that parameter's own tool-produced values.
#
# Canonical parameter names + the stat suffixes a key may carry (median/best/…)
# so dict keys like "omega_m_best" or "H0_median" still resolve to the param.
_COSMO_PARAM_EXACT: dict[str, str] = {
    "h0": "H0", "omegam": "omegam", "sigma8": "sigma8", "s8": "S8",
    "w0": "w0", "wa": "wa", "w": "w", "rd": "rd", "mb": "M_B", "h0rd": "H0_rd",
}
# Roots tried longest-first; a key resolves if it equals a root or is the root
# followed by a known statistic suffix.
_COSMO_PARAM_ROOTS: tuple[tuple[str, str], ...] = (
    ("sigma8", "sigma8"), ("omegam", "omegam"), ("h0rd", "H0_rd"),
    ("h0", "H0"), ("s8", "S8"), ("w0", "w0"), ("wa", "wa"), ("rd", "rd"),
)
_COSMO_STAT_SUFFIXES: frozenset[str] = frozenset({
    "", "median", "mean", "best", "low", "high", "value", "val",
    "1sigmalow", "1sigmahigh", "q16", "q84", "hdilow94", "hdihigh94", "std",
})


def _canonicalize_cosmology_param(name: Any) -> str | None:
    norm = re.sub(r"[^a-z0-9]", "", str(name).lower())
    if not norm:
        return None
    if norm in _COSMO_PARAM_EXACT:
        return _COSMO_PARAM_EXACT[norm]
    for root, canon in _COSMO_PARAM_ROOTS:
        if norm.startswith(root) and norm[len(root):] in _COSMO_STAT_SUFFIXES:
            return canon
    return None


# extract_claims already tags canonical-symbol cosmology claims with a
# parameter-specific pattern label (cosmology_h0 / _om0 / _w0 / _wa / _sigma8 /
# _s8 — see _PATTERNS).  That tag IS the parameter identity, so we map straight
# off it rather than re-scanning text (a window scan misattributes because a
# cosmology claim's span starts at its label, so "before the number" catches the
# PREVIOUS clause's parameter).
_CLAIM_LABEL_TO_COSMO_PARAM: dict[str, str] = {
    "cosmology_h0": "H0",
    "cosmology_om0": "omegam",
    "cosmology_w0": "w0",
    "cosmology_wa": "wa",
    "cosmology_sigma8": "sigma8",
    "cosmology_s8": "S8",
}


def _claim_cosmology_param(claim: "Claim") -> str | None:
    """Canonical cosmology parameter a claim is about, or None for claims not
    captured by a parameter-specific cosmology pattern."""
    return _CLAIM_LABEL_TO_COSMO_PARAM.get(claim.label)


def _build_cosmology_labeled_universe(payload: Any, out: dict[str, set[float]] | None = None) -> dict[str, set[float]]:
    """Map each cosmology parameter to the set of numeric values the tools
    actually produced for it (parameter dicts, derived_params, pairwise tensions,
    and dataset compressed-spec means).  Harvested broadly so legitimate quotes
    of a parameter's median / 1σ edge / published value all match."""
    if out is None:
        out = {}
    if isinstance(payload, dict):
        if _is_tainted_synthetic_payload(payload):
            return out
        # Parallel parameters/mean(/covariance) list form (dataset specs).
        params = payload.get("parameters")
        mean = payload.get("mean")
        if (
            isinstance(params, (list, tuple))
            and isinstance(mean, (list, tuple))
            and len(params) == len(mean)
            and params
            and all(isinstance(p, str) for p in params)
        ):
            cov = payload.get("covariance")
            for i, pname in enumerate(params):
                canon = _canonicalize_cosmology_param(pname)
                if canon is None or isinstance(mean[i], bool) or not isinstance(mean[i], (int, float)):
                    continue
                m = float(mean[i])
                bucket = out.setdefault(canon, set())
                bucket.add(m)
                try:
                    var = float(cov[i][i])  # type: ignore[index]
                    if var > 0:
                        s = math.sqrt(var)
                        bucket.add(m - s)
                        bucket.add(m + s)
                except Exception:
                    pass
        # A "parameter" field naming the quantity (pairwise_tensions rows).
        named = payload.get("parameter") or payload.get("param")
        ctx = _canonicalize_cosmology_param(named) if isinstance(named, str) else None
        for key, val in payload.items():
            key_str = str(key).lower()
            if key_str in _METADATA_KEYS_BLACKLIST or key_str in _CITATION_KEYS_BLACKLIST:
                continue
            canon_key = _canonicalize_cosmology_param(key)
            if canon_key is not None:
                bucket = out.setdefault(canon_key, set())
                for v in _iter_numeric_values(val):
                    bucket.add(v)
            elif ctx is not None and key not in {"parameter", "param"}:
                bucket = out.setdefault(ctx, set())
                for v in _iter_numeric_values(val):
                    bucket.add(v)
            else:
                _build_cosmology_labeled_universe(val, out)
    elif isinstance(payload, (list, tuple)):
        for val in payload:
            _build_cosmology_labeled_universe(val, out)
    return out


# F1.3: when the tool-result universe is thin, switch to a tight tolerance
# so an invented number cannot accidentally match some stray column index
# or row count.  10 entries was chosen to be larger than the typical
# `row_count`/`shape`/`len` scalars a successful tool response contains
# beyond its actual data, and smaller than any real cone-search result.
STRICT_TOLERANCE = 0.001  # 0.1 %
STRICT_UNIVERSE_THRESHOLD = 10


def validate_claims(
    reply: str,
    tool_results: Any,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    strict_when_empty: bool = True,
) -> ValidationResult:
    """Check every numeric claim in `reply` against `tool_results`.

    `tool_results` can be a list of dicts (typical `_run_agent_loop`
    accumulator), a single dict, or any nested structure.  We harvest all
    numeric scalars into a set and test each claim for a match within
    `tolerance`.

    F1.3: if `strict_when_empty` and the harvested universe has fewer than
    `STRICT_UNIVERSE_THRESHOLD` entries, tighten tolerance to
    `STRICT_TOLERANCE` (0.1 %) to prevent accidental matches against
    indices / row counts / offsets.
    """
    claims = extract_claims(reply)
    universe: set[float] = set(_iter_numeric_values(tool_results))
    universe_size = len(universe)
    universe_sample = sorted(universe)[:50]

    strict_mode = False
    effective_tol = tolerance
    if strict_when_empty and universe_size < STRICT_UNIVERSE_THRESHOLD:
        effective_tol = STRICT_TOLERANCE
        strict_mode = True

    if not claims:
        return ValidationResult(
            ok=True,
            claims=[],
            uncited=[],
            universe_sample=universe_sample,
            universe_size=universe_size,
            strict_mode=strict_mode,
        )

    # 4.1: label-aware cosmology-parameter matching.  When a claim is recognisably
    # about a cosmology parameter that the tools actually produced, it must match
    # THAT parameter's own values — not any coincidentally-close number elsewhere
    # in the universe.  This closes the cross-label ±1% laundering surface (e.g.
    # an "σ8 = 0.315" claim being validated by an unrelated Ωm=0.3153).  Claims we
    # can't pin to a produced parameter keep the existing global-universe check.
    labeled = _build_cosmology_labeled_universe(tool_results) if tool_results else {}
    uncited: list[Claim] = []
    for c in claims:
        param = _claim_cosmology_param(c)
        if param is not None and labeled.get(param):
            if not _matches_any(c.value, labeled[param], effective_tol):
                uncited.append(c)
        elif not _matches_any(c.value, universe, effective_tol):
            uncited.append(c)
    return ValidationResult(
        ok=not uncited,
        claims=claims,
        uncited=uncited,
        universe_sample=universe_sample,
        universe_size=universe_size,
        strict_mode=strict_mode,
    )


def citation_validator_hardblock_enabled() -> bool:
    # PART Y Batch 1: defaults to True; only disabled when
    # PROVENANCE_VALIDATOR_HARDBLOCK=false is set explicitly. No longer falls
    # back to the module-level CITATION_VALIDATOR_HARDBLOCK constant, so that
    # tests using monkeypatch.setenv / delenv can switch the state at runtime.
    return os.getenv("PROVENANCE_VALIDATOR_HARDBLOCK", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def citation_violations_should_block(violations: list[CitationViolation]) -> bool:
    return bool(violations) and citation_validator_hardblock_enabled()


def _build_valid_bibcode_pool(tool_results: Any) -> set[str]:
    """Collect every tool-sourced bibcode that may support a reply citation."""
    pool: set[str] = set()
    for node in _citation_pool_nodes(tool_results):
        for key, value in node.items():
            if not value:
                continue
            if key in {"bibcode", "article", "reference", "source_reference"} or key.endswith("_bibcode"):
                pool.update(_bibcodes_from_text(str(value)))
            if key in {"citations", "references"} and isinstance(value, list):
                for item in value:
                    pool.update(_bibcodes_from_text(json.dumps(item, ensure_ascii=False, default=str)))

        provenance = node.get("provenance")
        if isinstance(provenance, dict):
            for dataset in provenance.get("datasets") or []:
                if isinstance(dataset, dict) and dataset.get("article"):
                    pool.update(_bibcodes_from_text(str(dataset["article"])))
            pool.update(_bibcodes_from_field_payload(provenance.get("field_bibcodes")))

        datasets = node.get("datasets")
        if isinstance(datasets, list):
            for dataset in datasets:
                if isinstance(dataset, dict) and dataset.get("article"):
                    pool.update(_bibcodes_from_text(str(dataset["article"])))

        pool.update(_bibcodes_from_field_payload(node.get("field_bibcodes")))
    return pool


def _build_valid_arxiv_pool(tool_results: Any) -> set[str]:
    pool: set[str] = set()
    for node in _citation_pool_nodes(tool_results):
        for key in ("arxiv", "arxiv_id", "bibcode", "article", "source_url", "reference_url"):
            value = node.get(key)
            if not value:
                continue
            pool.update(_arxiv_ids_from_text(str(value)))
    return pool


def _build_attempted_arxiv_pool(tool_results: Any) -> set[str]:
    """arXiv IDs attempted as tool inputs.

    These IDs do not support science claims, but they are safe to mention in
    honest failure/limitation lines such as "arXiv:1234.56789 could not be
    fetched".
    """
    pool: set[str] = set()
    for node in _iter_dict_nodes(tool_results):
        tool_input = node.get("input")
        if not isinstance(tool_input, dict):
            continue
        for key in ("arxiv_id", "arxiv_url", "paper"):
            value = tool_input.get(key)
            if isinstance(value, dict):
                pool.update(_arxiv_ids_from_text(json.dumps(value)))
            elif value:
                pool.update(_arxiv_ids_from_text(str(value)))
    return pool


def _build_valid_doi_pool(tool_results: Any) -> set[str]:
    pool: set[str] = set()
    for node in _citation_pool_nodes(tool_results):
        for key in ("doi", "source_url", "reference_url"):
            value = node.get(key)
            if not value:
                continue
            pool.update(_dois_from_text(str(value)))
    return pool


def _build_author_year_support(tool_results: Any) -> set[tuple[str, str]]:
    support: set[tuple[str, str]] = set()
    for node in _iter_dict_nodes(tool_results):
        year = str(node.get("year") or "").strip()[:4]
        authors = node.get("authors") or node.get("author")
        label = node.get("label")
        reference = node.get("reference") or node.get("source_reference")
        if not year:
            if isinstance(reference, str):
                # Tool payloads often carry compact strings such as
                # "Mainzer+ 2011 ... (2011ApJ...743..156M)" rather than a
                # full citation object. Treat them as support for the
                # corresponding author-year shorthand once the tool ran.
                for ref_match in re.finditer(r"\b([A-Z][A-Za-z'`-]+)(?:\+| et al\.?)?\s+(\d{4})\b", reference):
                    for key in _author_support_keys(ref_match.group(1)):
                        support.add((key, ref_match.group(2)))
            continue
        if isinstance(authors, list) and authors:
            for author in authors:
                for key in _author_support_keys(str(author)):
                    support.add((key, year))
        if isinstance(label, str) and label.strip():
            for key in _author_support_keys(label):
                support.add((key, year))
    return support


def provenance_citation_violations(
    reply: str,
    tool_results: Any,
    *,
    strict: bool = False,
) -> list[CitationViolation]:
    """Flag bibcode or author-year citations not present in tool provenance."""
    if not reply:
        return []
    valid_bibcodes = _build_valid_bibcode_pool(tool_results)
    valid_arxiv_ids = _build_valid_arxiv_pool(tool_results)
    attempted_arxiv_ids = _build_attempted_arxiv_pool(tool_results)
    valid_dois = _build_valid_doi_pool(tool_results)
    author_year_support = _build_author_year_support(tool_results)
    claimable_payload_text = _claimable_tool_text(tool_results)
    violations: list[CitationViolation] = []

    for match in BIBCODE_RE.finditer(reply):
        bibcode = _normalize_bibcode(match.group(1) if match.groups() else match.group(0))
        if not bibcode:
            continue
        if bibcode not in valid_bibcodes:
            violations.append(CitationViolation(
                kind="invalid_bibcode",
                match_text=bibcode,
                line_number=_line_number(reply, match.start()),
            ))

    for match in ARXIV_ID_RE.finditer(reply):
        arxiv_id = _normalize_arxiv_id(match.group(1))
        if arxiv_id and arxiv_id not in valid_arxiv_ids:
            line_text = _line_text(reply, match.start())
            if arxiv_id in attempted_arxiv_ids and _line_is_nonclaim_context(line_text):
                continue
            violations.append(CitationViolation(
                kind="invalid_arxiv_id",
                match_text=f"arXiv:{arxiv_id}",
                line_number=_line_number(reply, match.start()),
            ))

    for match in DOI_RE.finditer(reply):
        doi = _normalize_doi(match.group(0))
        if doi and doi not in valid_dois:
            violations.append(CitationViolation(
                kind="invalid_doi",
                match_text=doi,
                line_number=_line_number(reply, match.start()),
            ))

    citation_text = _strip_markdown_code(reply)
    for match in AUTHOR_YEAR_RE.finditer(citation_text):
        author, year = match.group(1), match.group(2)
        match_text = match.group(0).strip()
        line_text = _line_text(citation_text, match.start())
        if _line_is_nonclaim_context(line_text):
            continue
        if _line_has_valid_explicit_citation(line_text, valid_bibcodes, valid_arxiv_ids, valid_dois):
            continue
        if _phrase_in_claimable_payload(match_text, claimable_payload_text):
            continue
        if not strict and _author_year_looks_like_noise(author, match_text):
            continue
        if _author_year_is_suspicious(author, year, valid_bibcodes, author_year_support):
            violations.append(CitationViolation(
                kind="suspicious_author_year",
                match_text=match_text,
                line_number=_line_number(reply, match.start()),
            ))

    violations.extend(_paper_level_numeric_claim_violations(
        citation_text,
        tool_results,
        valid_bibcodes,
        valid_arxiv_ids,
        valid_dois,
        author_year_support,
    ))

    for violation in violations:
        _record_citation_violation_metric(violation.kind)
        logger.warning(
            "Citation provenance violation: kind=%s match=%r line=%d",
            violation.kind,
            violation.match_text,
            violation.line_number,
        )
    return violations


def unsupported_literature_narrative_violations(
    reply: str,
    tool_results: Any,
) -> list[CitationViolation]:
    """Hard-block unsupported literature/history prose, not only citations.

    B12/B13 exposed a gap: the assistant stopped inventing explicit
    author-year citations, but still laundered synthetic stdout or training
    priors as prose ("literature values", "monitored since 1784", dP/dt).
    These claims need a current-turn literature/tool source even when they
    contain no bibcode-shaped token.
    """
    if not reply:
        return []

    stripped_reply = _strip_markdown_code(reply)
    supported_payload_text = _claimable_tool_text(tool_results)
    violations: list[CitationViolation] = []
    seen: set[tuple[str, str, int]] = set()

    for kind, pattern in _UNSUPPORTED_NARRATIVE_PATTERNS:
        if _unsupported_narrative_kind_is_supported(kind, tool_results):
            continue
        for match in pattern.finditer(stripped_reply):
            match_text = match.group(0).strip()
            if not match_text:
                continue
            if match_text.lower() in supported_payload_text:
                continue
            key = (kind, match_text.lower(), match.start())
            if key in seen:
                continue
            seen.add(key)
            violations.append(CitationViolation(
                kind="unsupported_literature_narrative",
                match_text=match_text,
                line_number=_line_number(reply, match.start()),
            ))

    for violation in violations:
        _record_citation_violation_metric(violation.kind)
        logger.warning(
            "Unsupported narrative provenance violation: match=%r line=%d",
            violation.match_text,
            violation.line_number,
        )
    return violations


def unclassified_literature_violations(
    reply: str,
    tool_results: Any,
) -> list[CitationViolation]:
    """Stage 6 P0c-C (2026-05-19): hard-block citations to papers not classified
    by classify_literature_relevance.

    Workflow:
      1. Collect all paper bibcodes returned by search_literature this turn
         (search_pool).
      2. Collect classifications from classify_literature_relevance this turn
         (classified_relevance: bibcode -> Direct/Marginal/Off-topic).
      3. Extract all bibcode / arxiv_id citations from the reply.
      4. Any cited bibcode in search_pool but not in classified_relevance ->
         violation (kind=unclassified_literature).
      5. Any cited bibcode in classified_relevance with relevance=Off-topic ->
         violation (kind=cited_off_topic_paper).

    Bibcodes cited outside search_pool (e.g. direct dataset references) are not
    handled here — provenance_citation_violations already covers that path.

    Stages 5/6.2 used `__message_to_model__` as a soft rule (LLM voluntarily
    outputs Direct/Marginal/Off-topic), but prod testing showed the LLM skips it.
    This function + chat.py pipeline + banner upgrades it to a hard barrier.
    """
    if not reply:
        return []

    search_pool: set[str] = set()
    classified_relevance: dict[str, str] = {}

    if isinstance(tool_results, list):
        for tr in tool_results:
            if not isinstance(tr, dict):
                continue
            tool_name = str(tr.get("tool") or tr.get("name") or "").strip()
            # tool_result payload may be under "result" / "tool_result" / top-level
            payload = tr.get("result") if isinstance(tr.get("result"), dict) else tr
            if tool_name == "search_literature":
                papers = payload.get("results") if isinstance(payload, dict) else None
                if not isinstance(papers, list):
                    continue
                for p in papers:
                    if not isinstance(p, dict):
                        continue
                    bc = _normalize_bibcode(str(p.get("bibcode") or ""))
                    if bc:
                        search_pool.add(bc)
            elif tool_name == "classify_literature_relevance":
                classes = payload.get("classifications") if isinstance(payload, dict) else None
                if not isinstance(classes, list):
                    continue
                for c in classes:
                    if not isinstance(c, dict):
                        continue
                    bc = _normalize_bibcode(str(c.get("bibcode") or ""))
                    rel = str(c.get("relevance") or "").strip()
                    if bc and rel:
                        classified_relevance[bc] = rel

    if not search_pool:
        # search_literature was not called this turn; skip this check
        return []

    violations: list[CitationViolation] = []
    seen: set[str] = set()

    for match in BIBCODE_RE.finditer(reply):
        raw = match.group(1) if match.groups() else match.group(0)
        bibcode = _normalize_bibcode(raw)
        if not bibcode or bibcode in seen:
            continue
        seen.add(bibcode)
        if bibcode in search_pool and bibcode not in classified_relevance:
            violations.append(CitationViolation(
                kind="unclassified_literature",
                match_text=bibcode,
                line_number=_line_number(reply, match.start()),
            ))
        elif classified_relevance.get(bibcode) == "Off-topic":
            violations.append(CitationViolation(
                kind="cited_off_topic_paper",
                match_text=bibcode,
                line_number=_line_number(reply, match.start()),
            ))

    for violation in violations:
        _record_citation_violation_metric(violation.kind)
        logger.warning(
            "Unclassified literature violation: kind=%s bibcode=%r line=%d",
            violation.kind,
            violation.match_text,
            violation.line_number,
        )
    return violations


# ── M6: methodology-consistency check ─────────────────────────────
# When the assistant verbally promises a Bayesian fit / two-axis
# errors / a demagnification count, the actual fit_line_lfr or
# demagnify_sample tool_result must back the claim up.  Otherwise
# the methodology label is fiction even if the numbers happened to
# come from somewhere.

_BAYESIAN_PROMISE_RE = re.compile(
    r"\b(?:bayesian|linmix|kelly\s*0?7|kelly\s*2007|"
    r"two[\s-]?axis\s+errors?|"
    r"errors?\s+in\s+both\s+(?:axes|x\s+and\s+y))\b",
    re.IGNORECASE,
)
_DEMAGNIFY_COUNT_RE = re.compile(
    r"\b(?:demagnif(?:ied|y(?:ing)?))\s+(\d+)\s+sources?\b",
    re.IGNORECASE,
)
_LINE_RELATION_QUANT_RE = re.compile(
    r"\b(?:slope|intercept|intrinsic\s+scatter|sigma[_\s-]?int|"
    r"pearson|spearman|p[-\s]?value|alpha|beta|"
    r"r\s*=|p\s*=|[αβ]\s*=)\b",
    re.IGNORECASE,
)
_EXPLORATORY_FIT_QUALIFIER_RE = re.compile(
    r"\b(?:exploratory|not\s+(?:as\s+a\s+|a\s+)?publication[-\s]?ready|publication_ready\s*=\s*false|"
    r"partial\s+fit|partial\s+result|not\s+for\s+publication|"
    r"not\s+manuscript[-\s]?ready)\b",
    re.IGNORECASE,
)
_PUBLICATION_READY_TRUE_RE = re.compile(
    r"\bpublication[_\s-]?ready\s*=\s*true\b",
    re.IGNORECASE,
)
_FULL_EXTERNAL_LIKELIHOOD_READY_RE = re.compile(
    r"(?:\bready\b.{0,90}\bfull\s+(?:external\s+)?(?:likelihood|cobaya|cosmosis)\b|"
    r"\bfull\s+(?:external\s+)?(?:likelihood|cobaya|cosmosis)\b.{0,90}\bready\b)",
    re.IGNORECASE | re.DOTALL,
)
_FULL_EXTERNAL_LIKELIHOOD_NONCLAIM_RE = re.compile(
    r"\b(?:not|no|without|requires?|required|require|would|future|pending|not\s+run|not\s+included|still\s+need|"
    r"config(?:uration)?|workflow)\b",
    re.IGNORECASE,
)
# PART AI #3: LFR-context signature — the reply must clearly be doing line
# luminosity-FWHM relation work (not isochrone slope / photometry alpha etc.)
# before the bypass detector fires. Prevents false positives in other workflows.
_LFR_CONTEXT_RE = re.compile(
    r"(?:"
    r"\bL[' ]?\s*\[?\s*CII\s*\]?"               # L'[CII], L'CII, L [CII]
    r"|\bLFR\b"
    r"|luminosity[\s-]+FWHM"
    r"|L[- ]FWHM\s+relation"
    r"|line[\s-]+luminosity[\s-]+(?:FWHM|width)"
    r"|line[\s-]+(?:width|FWHM)[\s-]+(?:relation|fit)"
    r"|\[CII\][^.\n]{0,80}(?:fit|relation|regression)"
    r"|(?:fit|regression)[^.\n]{0,80}\[CII\]"
    r"|\bL'\s*-?\s*FWHM"
    r"|brightness[\s-]+temperature"
    r"|Solomon[\s-]?(?:1992|92)"
    r"|Carilli\s*(?:&|and)\s*Walter"
    r")",
    re.IGNORECASE,
)


def _collect_tool_results_for(tool_results: Any, tool_name: str) -> list[dict]:
    """Return every claimable success tool_result dict for the named tool.

    PART AB: filter out FAILED / EMPTY / SYNTHETIC results so the
    methodology-consistency check only fires when the named tool
    actually produced a real, claimable result. Otherwise a turn that
    didn't really run fit_line_lfr (e.g. tool was disabled, returned
    EMPTY, or wasn't called at all) would still trigger a
    method_mismatch on a prose mention of "Bayesian", as in the
    R2.4 M2 audit where 0 tool calls + a method-name mention in the
    user prompt blocked the reply pre-flight.
    """
    out: list[dict] = []
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    for entry in entries or []:
        name, result = _entry_tool_and_result(entry)
        if not result:
            continue
        candidate_name = (
            name
            or (str(result.get("tool")) if isinstance(result.get("tool"), str) else None)
        )
        if candidate_name != tool_name:
            continue
        # Drop unclaimable results — banner-stamped EMPTY / FAILED /
        # SYNTHETIC, plus explicit success=False payloads.
        if not _payload_is_claimable_success(tool_name, result):
            continue
        out.append(result)
    return out


def _collect_raw_tool_results_for(tool_results: Any, tool_name: str) -> list[dict]:
    """Return raw result dicts for a tool, including PARTIAL/__do_not_claim__.

    Claimability filtering is correct for "can this support a paper claim?",
    but methodology auditing also needs to see partial fits so it can require
    the prose to label their statistics as exploratory.
    """
    out: list[dict] = []
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    for entry in entries or []:
        name, result = _entry_tool_and_result(entry)
        if not result:
            continue
        candidate_name = (
            name
            or (str(result.get("tool")) if isinstance(result.get("tool"), str) else None)
        )
        if candidate_name == tool_name:
            out.append(result)
    return out


def methodology_consistency_violations(
    reply: str, tool_results: Any,
) -> list[CitationViolation]:
    """Cross-check methodology promises in the reply against fit_line_lfr
    / demagnify_sample tool results.  Returns violations with kind
    'method_mismatch' (Bayesian promised, OLS actually ran) or
    'demagnify_count_mismatch' (claimed N demagnified > what tools did).
    """
    if not reply:
        return []
    stripped = _strip_markdown_code(reply)
    fit_results = _collect_tool_results_for(tool_results, "fit_line_lfr")
    raw_fit_results = _collect_raw_tool_results_for(tool_results, "fit_line_lfr")
    demag_results = _collect_tool_results_for(tool_results, "demagnify_sample")

    violations: list[CitationViolation] = []

    # ── Bayesian promise ──────────────────────────────────────────
    bayesian_match = _BAYESIAN_PROMISE_RE.search(stripped)
    if bayesian_match and fit_results:
        any_bayesian = any(
            str(r.get("fit_method", "")).startswith("bayesian")
            for r in fit_results
        )
        if not any_bayesian:
            violations.append(CitationViolation(
                kind="method_mismatch",
                match_text=bayesian_match.group(0),
                line_number=_line_number(reply, bayesian_match.start()),
            ))

    # ── Publication-ready / exploratory status ───────────────────────
    # fit_line_lfr may legitimately return slope/intercept/scatter from a
    # real cache while marking the relation PARTIAL because units/citations
    # still need review. Those numbers may be discussed, but the prose must
    # label them as exploratory; sampler convergence alone does not make the
    # top-level relation publication-ready.
    if raw_fit_results:
        any_overall_publication_ready = any(
            r.get("publication_ready") is True
            and r.get("__do_not_claim__") is not True
            for r in raw_fit_results
        )
        has_partial_fit = any(
            r.get("publication_ready") is not True
            or r.get("__do_not_claim__") is True
            or str(r.get("__tool_status__") or "").strip().upper() == "PARTIAL"
            for r in raw_fit_results
        )
        ready_match = _PUBLICATION_READY_TRUE_RE.search(stripped)
        if ready_match and not any_overall_publication_ready:
            violations.append(CitationViolation(
                kind="publication_ready_mismatch",
                match_text=ready_match.group(0),
                line_number=_line_number(reply, ready_match.start()),
            ))
        stat_match = _LINE_RELATION_QUANT_RE.search(stripped)
        if (
            stat_match
            and has_partial_fit
            and not any_overall_publication_ready
            and not _EXPLORATORY_FIT_QUALIFIER_RE.search(stripped)
        ):
            violations.append(CitationViolation(
                kind="line_relation_exploratory_label_missing",
                match_text=stat_match.group(0),
                line_number=_line_number(reply, stat_match.start()),
            ))

    # ── Cosmology compressed-vs-full likelihood scope ────────────────
    # A compressed Gaussian chain can support preliminary posterior/tension
    # numbers, but it does not make the selected probes "ready for full
    # likelihood analyses."  Require an actual full external likelihood run
    # before that user-facing readiness claim is allowed.
    for full_match in _FULL_EXTERNAL_LIKELIHOOD_READY_RE.finditer(stripped):
        sentence = _sentence_text(reply, full_match.start())
        line = _line_text(reply, full_match.start())
        context = f"{line} {sentence}"
        if _FULL_EXTERNAL_LIKELIHOOD_NONCLAIM_RE.search(context):
            continue
        if not _full_external_likelihood_ready_available(tool_results):
            violations.append(CitationViolation(
                kind="full_likelihood_overclaim",
                match_text=full_match.group(0),
                line_number=_line_number(reply, full_match.start()),
            ))

    # ── PART AI #3: fit_line_lfr bypass detection ────────────────────
    # Bundle e8d9 reproducer: the reply reports LFR numbers (slope/intercept/scatter)
    # but fit_line_lfr was not called this turn — the AI ran OLS/linmix inside
    # run_python using cached rows and reported prose numbers, completely bypassing
    # fit_line_lfr's PARTIAL gate + cosmology recompute + lensing demagnify +
    # Bayesian diagnostics. This check is independent of raw_fit_results because
    # it only triggers when raw_fit_results is empty. Limited to cases where the
    # reply clearly follows the LFR workflow (LFR keyword + numbers) to avoid
    # false positives against isochrone slope / photometry alpha etc.
    if not raw_fit_results:
        lfr_ctx_match = _LFR_CONTEXT_RE.search(stripped)
        bypass_stat_match = _LINE_RELATION_QUANT_RE.search(stripped)
        if lfr_ctx_match and bypass_stat_match:
            violations.append(CitationViolation(
                kind="fit_line_lfr_bypass",
                match_text=bypass_stat_match.group(0),
                line_number=_line_number(reply, bypass_stat_match.start()),
            ))

    # ── Demagnify count ───────────────────────────────────────────
    max_demag = 0
    for r in fit_results:
        try:
            max_demag = max(max_demag, int(r.get("lensed_sources_demagnified") or 0))
        except (TypeError, ValueError):
            pass
    for r in demag_results:
        try:
            max_demag = max(max_demag, int(r.get("n_demagnified") or 0))
        except (TypeError, ValueError):
            pass
    for m in _DEMAGNIFY_COUNT_RE.finditer(stripped):
        try:
            claimed = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if claimed > max_demag:
            violations.append(CitationViolation(
                kind="demagnify_count_mismatch",
                match_text=m.group(0),
                line_number=_line_number(reply, m.start()),
            ))

    for v in violations:
        try:
            _record_citation_violation_metric(v.kind)
        except Exception:
            pass
        logger.warning(
            "Methodology consistency violation: kind=%s match=%r line=%d",
            v.kind, v.match_text, v.line_number,
        )
    return violations


def _unsupported_narrative_kind_is_supported(kind: str, tool_results: Any) -> bool:
    if kind == "unsupported_line_property_relation":
        return _line_measurement_rows_available(tool_results)
    if kind == "unsupported_bao_bin_anomaly":
        return _bao_bin_anomaly_assessment_available(tool_results)
    if kind == "literature_fallback" and _cosmology_publication_ready_available(tool_results):
        return True
    return (
        _tool_successfully_ran(tool_results, "search_literature")
        or _line_measurement_rows_available(tool_results)
        or _tool_successfully_ran(tool_results, "fit_line_lfr")
    )


def _line_measurement_rows_available(tool_results: Any) -> bool:
    for entry in tool_results if isinstance(tool_results, list) else [tool_results]:
        tool_name, result = _entry_tool_and_result(entry)
        if tool_name not in {"extract_literature_tables", "search_line_measurements", "fit_line_lfr"}:
            continue
        if not _payload_is_claimable_success(tool_name, result):
            continue
        if tool_name == "fit_line_lfr":
            return bool(result.get("publication_ready"))
        rows = result.get("line_measurements") if isinstance(result, dict) else None
        if isinstance(rows, list) and rows:
            return True
    return False


def _bao_bin_anomaly_assessment_available(tool_results: Any) -> bool:
    """True only when a tool returned bin-level BAO residual/pull evidence."""
    for entry in tool_results if isinstance(tool_results, list) else [tool_results]:
        tool_name, result = _entry_tool_and_result(entry)
        if not _payload_is_claimable_success(tool_name, result):
            continue
        if tool_name in {"assess_bao_bin_anomaly", "run_gp_reconstruction"}:
            return True
        if not isinstance(result, dict):
            continue
        for key in (
            "bin_level_assessment",
            "bao_bin_residuals",
            "bin_residuals",
            "outlier_assessment",
            "residual_pulls",
            "pull_sigma",
        ):
            value = result.get(key)
            if value not in (None, [], {}):
                return True
    return False


def _cosmology_publication_ready_available(tool_results: Any) -> bool:
    """Whether a cosmology posterior tool produced citeable current-turn results."""
    for entry in tool_results if isinstance(tool_results, list) else [tool_results]:
        tool_name, result = _entry_tool_and_result(entry)
        if tool_name not in {
            "run_cosmology_likelihood_chain",
            "run_cosmology_robustness_matrix",
            "run_cmb_rotation_likelihood",
        }:
            continue
        if _payload_is_claimable_success(tool_name, result):
            return True
    return False


def _full_external_likelihood_ready_available(tool_results: Any) -> bool:
    """Whether a full external likelihood, not compressed Gaussian, finished."""
    for entry in tool_results if isinstance(tool_results, list) else [tool_results]:
        tool_name, result = _entry_tool_and_result(entry)
        if tool_name not in {
            "run_cobaya_cosmology",
            "get_cosmology_run_status",
            "run_cosmology_likelihood_chain",
            "run_cosmology_robustness_matrix",
        }:
            continue
        if not _payload_is_claimable_success(tool_name, result):
            continue
        scope = str(result.get("claim_scope") or "").lower()
        sampler = str(result.get("sampler") or "").lower()
        execution_mode = str(result.get("execution_mode") or "").lower()
        if "compressed" in scope or "compressed" in sampler or execution_mode == "compressed_gaussian":
            continue
        if tool_name in {"run_cobaya_cosmology", "get_cosmology_run_status"}:
            return True
        if execution_mode in {"external_cobaya", "external_cosmosis"}:
            return True
    return False


def blocked_unsupported_narrative_reply_text(violations: list[CitationViolation]) -> str:
    lines = [
        f"- {violation.match_text} (line {violation.line_number})"
        for violation in violations
    ]
    return (
        "⚠ Reply withheld: the model attempted to use literature, historical, "
        "or physical-context claims that were not present in this turn's "
        "non-synthetic tool results.\n\n"
        + "\n".join(lines)
        + "\n\nRun a literature search or a dedicated measurement step first, "
        "or state that the value/context was not determined by the tools."
    )


def blocked_unclassified_literature_reply_text(violations: list[CitationViolation]) -> str:
    """Stage 6 P0c-C (2026-05-19): banner for unclassified / off-topic
    literature citations. Used with attach_draft_to_banner in chat.py."""
    unclassified_lines = []
    off_topic_lines = []
    for v in violations:
        if v.kind == "cited_off_topic_paper":
            off_topic_lines.append(f"- {v.match_text} (line {v.line_number})")
        else:
            unclassified_lines.append(f"- {v.match_text} (line {v.line_number})")
    body_parts = []
    if unclassified_lines:
        body_parts.append(
            "Cited but not classified via classify_literature_relevance:\n"
            + "\n".join(unclassified_lines)
        )
    if off_topic_lines:
        body_parts.append(
            "Cited but classified Off-topic by classify_literature_relevance:\n"
            + "\n".join(off_topic_lines)
        )
    return (
        "⚠ Reply withheld: the model cited literature paper(s) without first "
        "running `classify_literature_relevance` on them, or cited paper(s) "
        "that were classified Off-topic. Stage 6 P0c-C enforces strict "
        "abstract-screening as a hard gate, not a prompt-level suggestion.\n\n"
        + "\n\n".join(body_parts)
        + "\n\nCall `classify_literature_relevance` on the most recent "
        "search_literature output, mark each paper Direct / Marginal / Off-topic, "
        "then re-write the reply citing only Direct + Marginal papers."
    )


def blocked_citation_reply_text(violations: list[CitationViolation]) -> str:
    lines = [
        f"- {violation.kind}: {violation.match_text} (line {violation.line_number})"
        for violation in violations
    ]
    cosmology_note = _cosmology_manifest_block_note(violations)
    if any(v.kind == "paper_numeric_missing_citation" for v in violations):
        return (
            "⚠ Citation provenance check failed: the assistant reported numeric "
            "claims from paper-level literature without a supporting citation in "
            "the same sentence. The unsupported phrases were:\n\n"
            + "\n".join(lines)
            + cosmology_note
            + "\n\nFor literature-derived numbers, cite the source directly in "
            "the sentence that contains the number, for example "
            "`(DESI Collaboration 2024; arXiv:2404.03002)`. If multiple papers "
            "support different numbers, split them into separate cited sentences."
        )
    return (
        "⚠ Reply withheld: the model attempted to cite references that were "
        "not present in this turn's tool provenance. The unsupported citations were:\n\n"
        + "\n".join(lines)
        + cosmology_note
        + "\n\nPlease re-run the relevant archive or literature query so the citation "
        "appears in tool_results before using it."
    )


def _cosmology_manifest_block_note(violations: list[CitationViolation]) -> str:
    """Helpful strict-mode hint for built-in cosmology preset bibcodes.

    We deliberately do not add these manifest bibcodes to the valid citation
    pool implicitly.  The model must call a cosmology/compare tool this turn
    so the manifest appears in tool_results.
    """
    invalid = {
        str(v.match_text).strip()
        for v in violations
        if v.kind == "invalid_bibcode"
    }
    if not invalid:
        return ""
    try:
        from app.services.cosmology import PRESETS
    except Exception:
        return ""
    preset_hits = sorted({
        str(preset.get("name") or name)
        for name, preset in PRESETS.items()
        if preset.get("bibcode") in invalid or preset.get("tcmb_bibcode") in invalid
    })
    if not preset_hits:
        return ""
    return (
        "\n\nNote: one unsupported bibcode belongs to a platform cosmology "
        f"preset ({', '.join(preset_hits)}). Strict citation mode still "
        "requires that preset metadata to be returned by a tool in this turn. "
        "Call a cosmology provenance tool such as `compare_luminosity_distances` "
        "or run the relevant fit with an explicit `cosmology=` argument before "
        "quoting the preset bibcode."
    )


def blocked_methodology_reply_text(violations: list[CitationViolation]) -> str:
    """PART AB — separate gate text for `method_mismatch` /
    `demagnify_count_mismatch` so the user does not see the wrong fix
    instruction.

    The R2.4 M2 audit caught a regression where method_mismatch
    violations were rendered through `blocked_citation_reply_text`
    (which advises "re-run the archive query"), even though the right
    fix for a methodology mismatch is to actually run the fit tool
    with the requested method, OR to remove the methodology claim from
    the prose.
    """
    bayesian_lines: list[str] = []
    demag_lines: list[str] = []
    other_lines: list[str] = []
    for v in violations:
        bullet = f"- {v.match_text} (line {v.line_number})"
        if v.kind == "method_mismatch":
            bayesian_lines.append(bullet)
        elif v.kind == "demagnify_count_mismatch":
            demag_lines.append(bullet)
        else:
            other_lines.append(f"- {v.kind}: {v.match_text} (line {v.line_number})")

    parts: list[str] = [
        "⚠ Reply withheld: the model promised methodology / counts that "
        "the tools did not actually produce this turn."
    ]
    if bayesian_lines:
        parts.append(
            "Bayesian / linmix / two-axis-errors mentioned but no "
            "fit_line_lfr success with `fit_method=bayesian_xyerr_*`:\n"
            + "\n".join(bayesian_lines)
            + "\n\nFix: call `fit_line_lfr` with "
            "`fit_method_requested=\"bayesian_xyerr\"` first, then describe "
            "the result. Or remove the methodology claim from the prose "
            "and report what actually ran (OLS, slope, etc.)."
        )
    if demag_lines:
        parts.append(
            "Claimed demagnify count exceeds what `demagnify_sample` / "
            "`fit_line_lfr` actually did:\n"
            + "\n".join(demag_lines)
            + "\n\nFix: call `demagnify_sample(cache_key=..., mu_map=...)` "
            "for the missing sources, or report only the count the tool "
            "returned."
        )
    if other_lines:
        parts.append(
            "Other methodology mismatches:\n"
            + "\n".join(other_lines)
        )
    return "\n\n".join(parts)


def _iter_dict_nodes(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            yield from _iter_dict_nodes(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_dict_nodes(item)


def _entry_tool_and_result(entry: Any) -> tuple[str | None, dict[str, Any] | None]:
    if not isinstance(entry, dict):
        return None, None
    tool_name = str(entry.get("tool") or entry.get("name") or "").strip() or None
    result = entry.get("result") if isinstance(entry.get("result"), dict) else entry
    return tool_name, result if isinstance(result, dict) else None


def _result_may_support_citation(result: Any) -> bool:
    """B4: whether a tool RESULT can seed the valid-citation pool.

    Deliberately lighter than _payload_is_claimable_success (which gates
    NUMERIC claims and requires a positive data/rows payload): a successful
    literature lookup may legitimately carry citeable bibcodes without a
    `data` row. It excludes only results that failed, were withheld
    (__do_not_claim__), or are synthetic/empty/unavailable — exactly the
    states whose identifiers must never become "valid provenance".
    """
    if not isinstance(result, dict):
        return False
    if _is_tainted_synthetic_payload(result):  # __do_not_claim__ / SYNTHETIC / SIMULATED_DEMO
        return False
    if result.get("success") is False or bool(result.get("error")):
        return False
    status_values = [
        str(result.get(key) or "").strip().upper()
        for key in ("analysis_status", "__tool_status__", "status")
        if result.get(key) is not None
    ]
    if any(s in {"EMPTY", "FAILED", "UNAVAILABLE"} for s in status_values):
        return False
    return True


def _citation_pool_nodes(tool_results: Any) -> Iterable[dict[str, Any]]:
    """B4: dict nodes eligible to seed the valid-citation pools.

    Only the `result` subtree of each non-tainted tool entry is yielded —
    never the tool `input` payload. This closes the laundering path where a
    fabricated arXiv id / bibcode becomes "valid provenance" merely by being
    passed as a tool argument or returned by a FAILED fetch. The separate
    _build_attempted_arxiv_pool still reads inputs, but only to permit honest
    "could not be fetched" failure lines, never to support a science claim.
    """
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            # Already-unwrapped result dict (no {tool,input,result} envelope):
            # drop any nested `input` so an argument id never seeds the pool.
            result = {k: v for k, v in entry.items() if k != "input"}
        if not _result_may_support_citation(result):
            continue
        yield from _iter_dict_nodes(result)


def _payload_is_claimable_success(tool_name: str | None, result: dict[str, Any] | None) -> bool:
    if not result:
        return False
    status_values = [
        str(result.get(key) or "").strip().upper()
        for key in ("analysis_status", "__tool_status__", "status", "data_origin")
        if result.get(key) is not None
    ]
    if (
        result.get("__do_not_claim__") is True
        or result.get("success") is False
        or bool(result.get("error"))
        or any(s in {"EMPTY", "FAILED", "UNAVAILABLE", "SYNTHETIC", "SIMULATED_DEMO"} for s in status_values)
        or str(result.get("data_origin") or "").lower() in {"synthetic", "unavailable"}
        or result.get("row_count") == 0
        or result.get("found") == 0
    ):
        return False

    for key in ("data", "rows", "results"):
        if key in result:
            value = result.get(key)
            if value in (None, [], {}):
                return False

    if tool_name == "search_literature":
        return bool(result.get("bibcode") or result.get("results") or result.get("data"))
    if tool_name == "fit_isochrone":
        return bool(
            result.get("best_fit")
            or result.get("age_myr") is not None
            or result.get("best_log_age") is not None
        )
    if tool_name == "get_extinction":
        return bool(result.get("e_b_v") is not None or result.get("a_v") is not None)
    if tool_name in {
        "fit_cosmology_mcmc",
        "run_cobaya_cosmology",
        "run_cosmology_likelihood_chain",
        "run_cosmology_robustness_matrix",
        "run_cmb_rotation_likelihood",
    }:
        # publication_ready is True only for chain_tier="publication".
        # chain_tier="exploratory" (2026-05-20) returns publication_ready=False
        # by design, so EXPLORATORY MCMC results are excluded from the
        # claimable-success set: their numbers may flow into the numeric
        # universe (so chat-level ±1% checks pass) but they cannot be used
        # as a citation source or bibcode pool contributor.
        return result.get("publication_ready") is True
    if tool_name == "get_cosmology_run_status":
        nested = result.get("result")
        return isinstance(nested, dict) and nested.get("publication_ready") is True

    return True


def _successful_tool_names(tool_results: Any) -> set[str]:
    names: set[str] = set()
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    for entry in entries or []:
        tool_name, result = _entry_tool_and_result(entry)
        if tool_name and _payload_is_claimable_success(tool_name, result):
            names.add(tool_name)
    return names


def _tool_successfully_ran(tool_results: Any, tool_name: str) -> bool:
    return tool_name in _successful_tool_names(tool_results)


def _claimable_tool_text(tool_results: Any) -> str:
    """Lowercase JSON text for non-synthetic, claimable tool payloads only."""
    chunks: list[str] = []
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    for entry in entries or []:
        tool_name, result = _entry_tool_and_result(entry)
        if not _payload_is_claimable_success(tool_name, result):
            continue
        try:
            chunks.append(json.dumps(result, default=str).lower())
        except TypeError:
            chunks.append(str(result).lower())
    return "\n".join(chunks)


_PAPER_LEVEL_TOOLS: frozenset[str] = frozenset({"search_literature", "read_arxiv_paper"})
_CITABLE_ANALYSIS_TOOLS: frozenset[str] = frozenset({
    "compare_luminosity_distances",
    "demagnify_sample",
    "fit_cosmology_mcmc",
    "fit_isochrone",
    "fit_line_lfr",
    "get_cosmology_run_status",
    "get_extinction",
    "query_gaia_cluster",
    "run_cosmology_likelihood_chain",
    "run_cosmology_robustness_matrix",
    "run_cmb_rotation_likelihood",
    "run_adql",
    "run_cobaya_cosmology",
    "run_python",
    "search_line_measurements",
})


def _paper_level_numeric_claim_violations(
    reply: str,
    tool_results: Any,
    valid_bibcodes: set[str],
    valid_arxiv_ids: set[str],
    valid_dois: set[str],
    author_year_support: set[tuple[str, str]],
) -> list[CitationViolation]:
    """Require local citation for numbers drawn from paper-level tools.

    `search_literature` and `read_arxiv_paper` are citation/context tools.
    They can support paper-level discussion, but they do not make a global
    paragraph of mixed numerical claims traceable.  When they are the primary
    source of numbers in a turn, each numeric claim must carry a valid
    same-sentence citation (arXiv/DOI/bibcode or supported author-year).
    Dedicated measurement/fitting tools are exempt; their numeric provenance
    is checked by the zero-fabrication and methodology gates instead.
    """
    if not _paper_level_numeric_citations_required(tool_results):
        return []

    violations: list[CitationViolation] = []
    seen: set[tuple[str, int]] = set()
    for claim in extract_claims(reply):
        line_text = _line_text(reply, claim.start)
        if _line_is_nonclaim_context(line_text):
            continue
        sentence = _sentence_text(reply, claim.start)
        if _text_has_valid_paper_citation(
            sentence,
            valid_bibcodes,
            valid_arxiv_ids,
            valid_dois,
            author_year_support,
        ):
            continue
        key = (claim.raw, claim.start)
        if key in seen:
            continue
        seen.add(key)
        violations.append(CitationViolation(
            kind="paper_numeric_missing_citation",
            match_text=claim.raw,
            line_number=_line_number(reply, claim.start),
        ))
    return violations


def _paper_level_numeric_citations_required(tool_results: Any) -> bool:
    has_paper_tool = False
    has_analysis_tool = False
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    for entry in entries or []:
        tool_name, result = _entry_tool_and_result(entry)
        if not _payload_is_claimable_success(tool_name, result):
            continue
        if tool_name in _PAPER_LEVEL_TOOLS:
            has_paper_tool = True
        elif tool_name in _CITABLE_ANALYSIS_TOOLS:
            has_analysis_tool = True

    # Row-level extracted measurements are already table-cited and may be
    # consumed by downstream fitting; do not force an abstract-style citation
    # rule on those rows.
    if _line_measurement_rows_available(tool_results):
        has_analysis_tool = True
    return has_paper_tool and not has_analysis_tool


def _bibcodes_from_field_payload(payload: Any) -> set[str]:
    bibcodes: set[str] = set()
    if not isinstance(payload, dict):
        return bibcodes
    columns = payload.get("columns")
    if not isinstance(columns, dict):
        return bibcodes
    for values in columns.values():
        if not isinstance(values, list):
            continue
        for value in values:
            bibcodes.update(_bibcodes_from_text(str(value)))
    return bibcodes


def _bibcodes_from_text(text: str) -> set[str]:
    bibcodes: set[str] = set()
    for match in BIBCODE_RE.finditer(text or ""):
        normalized = _normalize_bibcode(match.group(1) if match.groups() else match.group(0))
        if normalized:
            bibcodes.add(normalized)
    return bibcodes


def _arxiv_ids_from_text(text: str) -> set[str]:
    ids: set[str] = set()
    text = str(text or "")
    for match in ARXIV_ID_RE.finditer(text):
        normalized = _normalize_arxiv_id(match.group(1))
        if normalized:
            ids.add(normalized)
    # Bare IDs are common inside structured `arxiv_id` fields.
    bare = re.fullmatch(r"\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?|[a-z-]+/[0-9]{7}(?:v\d+)?)\s*", text, re.I)
    if bare:
        normalized = _normalize_arxiv_id(bare.group(1))
        if normalized:
            ids.add(normalized)
    return ids


def _dois_from_text(text: str) -> set[str]:
    return {_normalize_doi(match.group(0)) for match in DOI_RE.finditer(str(text or ""))}


def _normalize_bibcode(raw: str) -> str:
    return str(raw or "").strip().strip("`.,;:)]}\"'")


def _normalize_arxiv_id(raw: str) -> str:
    value = str(raw or "").strip().strip("`.,;:)]}\"'")
    value = re.sub(r"^arxiv:\s*", "", value, flags=re.I)
    value = re.sub(r"v\d+$", "", value, flags=re.I)
    return value


def _normalize_doi(raw: str) -> str:
    return str(raw or "").strip().strip("`.,;:)]}\"'").lower()


def _author_key(raw: str) -> str:
    value = str(raw or "").strip()
    if "," in value:
        value = value.split(",", 1)[0]
    else:
        parts = value.split()
        value = parts[-1] if parts else ""
    return re.sub(r"[^a-z]", "", value.lower())


def _author_support_keys(raw: str) -> set[str]:
    """Return author keys that can support author-year shorthand.

    Collaboration papers are often cited as "Planck 2018" while metadata
    stores "Planck Collaboration".  Registry labels also often start with
    the first author and continue with a title fragment, e.g. "Chen, Huang
    & Wang distance priors".  Keep the normal last-name key, but also support
    the leading author/survey/collaboration token for these metadata labels.
    """
    text = str(raw or "").strip()
    keys: set[str] = set()
    normal = _author_key(text)
    if normal:
        keys.add(normal)
    parts = text.split()
    if parts:
        lead = re.sub(r"[^a-z]", "", parts[0].lower())
        if lead and len(lead) >= 3:
            keys.add(lead)
    seen_et_al = False
    for part in parts:
        cleaned = re.sub(r"[^a-z]", "", part.lower())
        if not cleaned:
            continue
        if cleaned in {"et", "al", "and"}:
            if cleaned in {"et", "al"}:
                seen_et_al = True
            continue
        if seen_et_al:
            break
        # Registry citation labels usually start with a short author list
        # followed by lower-case title words ("Chen, Huang & Wang distance
        # priors").  Capture those leading capitalized author tokens so
        # "Wang (2019)" is supported by the same label.
        if part[:1].isupper() or part[:1] in {"&"}:
            if len(cleaned) >= 3:
                keys.add(cleaned)
            continue
        break
    if len(parts) >= 2 and re.sub(r"[^a-z]", "", parts[-1].lower()) in {
        "collaboration",
        "team",
        "survey",
        "consortium",
    }:
        lead = re.sub(r"[^a-z]", "", parts[0].lower())
        if lead:
            keys.add(lead)
    if any(
        re.sub(r"[^a-z]", "", part.lower()) in {
            "collaboration",
            "team",
            "survey",
            "consortium",
        }
        for part in parts
    ):
        keys.add("collaboration")
    return keys


def _author_year_is_suspicious(
    author: str,
    year: str,
    valid_bibcodes: set[str],
    author_year_support: set[tuple[str, str]] | None = None,
) -> bool:
    year_prefix = str(year)
    author_key = _author_key(author)
    if author_key and (author_key, year_prefix) in (author_year_support or set()):
        return False

    matches = [bibcode for bibcode in valid_bibcodes if bibcode.startswith(year_prefix)]
    if not matches:
        return True

    author_initial = author[0].upper() if author else ""
    for bibcode in matches:
        if bibcode and bibcode[-1] == author_initial:
            return False

    # Same-year bibcodes exist in the pool but NONE matches this author's
    # initial and there is no author_year_support entry — the citation is not
    # vindicated by provenance. The count of unrelated same-year bibcodes is
    # irrelevant: a year that happens to be well-represented in the tool pool
    # must not whitelist an arbitrary invented author for that year.
    return True


# Tokens that legitimately precede a 4-digit number without forming an
# author-year citation: calendar/figure/section words, English articles and
# determiners, and generic solar-system prose nouns ("Ephemeris 2026").
_NON_AUTHOR_TOKENS = frozenset({
    "April", "August", "December", "February", "Figure", "January",
    "July", "June", "March", "May", "November", "October",
    "Section", "September", "Table",
    "The", "A", "An", "This", "That", "These", "Those", "There", "Their",
    "It", "Its", "In", "On", "At", "By", "As", "Of", "For", "From",
    "Ephemeris", "Epoch", "Perihelion", "Aphelion", "Apparition",
    "Approach", "Encounter", "Opposition", "Year",
})

_CURRENT_YEAR = datetime.date.today().year


def _author_year_looks_like_noise(author: str, match_text: str) -> bool:
    if author.upper() in {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}:
        return True
    # Known non-author tokens are never citations, even when a stray opening
    # parenthesis sits between the word and the year ("Ephemeris (2026").
    if author in _NON_AUTHOR_TOKENS:
        return True
    if "et al" in match_text.lower():
        return False
    # A current/future year with no "et al" and no bracketed citation shape
    # reads as a date/epoch ("Phaethon 2026", "the 2029 approach"), not a
    # published reference — those carry a past publication year.
    if "(" not in match_text and "[" not in match_text:
        year_match = re.search(r"\b((?:1[5-9]|20|21)\d{2})\b", match_text)
        if year_match and int(year_match.group(1)) >= _CURRENT_YEAR:
            return True
    if "(" in match_text or "[" in match_text:
        return False
    return False


def _line_text(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, max(0, offset)) + 1
    end = text.find("\n", offset)
    if end < 0:
        end = len(text)
    return text[start:end]


def _sentence_text(text: str, offset: int) -> str:
    line_start = text.rfind("\n", 0, max(0, offset)) + 1
    line_end = text.find("\n", offset)
    if line_end < 0:
        line_end = len(text)
    line = text[line_start:line_end]
    local_offset = max(0, offset - line_start)

    boundaries = [0]
    boundaries.extend(match.end() for match in re.finditer(r"(?<=[.!?])\s+", line))
    boundaries.append(len(line))

    start = 0
    end = len(line)
    for left, right in zip(boundaries, boundaries[1:]):
        if left <= local_offset < right or (right == len(line) and local_offset == right):
            start, end = left, right
            break
    return line[start:end].strip()


def _line_is_nonclaim_context(line: str) -> bool:
    lowered = str(line or "").lower()
    return any(
        token in lowered
        for token in (
            "not validated",
            "not verified",
            "not supported",
            "not tool-supported",
            "unsupported",
            "did not return",
            "does not return",
            "do not have",
            "no validated",
            "no authoritative",
            "could not",
            "cannot",
            "can't",
            "failed",
            "failure",
            "rate limit",
            "retry",
            "next step",
            "unable",
            "not obtained",
        )
    )


def _phrase_in_claimable_payload(phrase: str, payload_text: str) -> bool:
    import re

    needle = str(phrase or "").lower()
    haystack = str(payload_text or "").lower()
    if needle in haystack:
        return True
    normalized_needle = re.sub(r"[\s\-_]+", " ", needle).strip()
    normalized_haystack = re.sub(r"[\s\-_]+", " ", haystack)
    return bool(normalized_needle and normalized_needle in normalized_haystack)


def _line_has_valid_explicit_citation(
    line: str,
    valid_bibcodes: set[str],
    valid_arxiv_ids: set[str],
    valid_dois: set[str],
) -> bool:
    """Allow author-year shorthand when the same line has a verified citation."""
    if _bibcodes_from_text(line) & valid_bibcodes:
        return True
    if _arxiv_ids_from_text(line) & valid_arxiv_ids:
        return True
    if _dois_from_text(line) & valid_dois:
        return True
    return False


def _text_has_valid_paper_citation(
    text: str,
    valid_bibcodes: set[str],
    valid_arxiv_ids: set[str],
    valid_dois: set[str],
    author_year_support: set[tuple[str, str]],
) -> bool:
    if _line_has_valid_explicit_citation(text, valid_bibcodes, valid_arxiv_ids, valid_dois):
        return True
    for match in AUTHOR_YEAR_RE.finditer(text):
        author, year = match.group(1), match.group(2)
        if _author_year_looks_like_noise(author, match.group(0)):
            continue
        if (_author_key(author), str(year)) in author_year_support:
            return True
    return False


def _line_number(text: str, offset: int) -> int:
    return text[:offset].count("\n") + 1


def _record_citation_violation_metric(kind: str) -> None:
    try:
        from app.observability.metrics import record_counter

        # Use the violation's own kind as the per-reason label so each class
        # (invalid_doi, method_mismatch, fit_line_lfr_bypass, …) increments its
        # OWN fabrication_blocked_total{reason} series. The previous code
        # collapsed every kind outside a 4-entry allowlist into
        # "suspicious_author_year", which misattributed the counter and hid
        # per-class validator regressions.
        record_counter("fabrication_blocked_total", 1.0, reason=kind)
    except Exception:
        pass


def build_regeneration_prompt(result: ValidationResult) -> str:
    """User-role follow-up message instructing the LLM to remove fabrications."""
    return (
        "STOP. " + result.describe() + "\n\n"
        "Rewrite your previous reply to:\n"
        "1. Remove every un-cited number listed above, OR replace it with "
        "the literal phrase 'not determined by my tools'.\n"
        "2. Keep all correctly-cited numbers.\n"
        "3. Do NOT call any tools again; just rewrite the prose.\n"
        "4. Do NOT invent substitute numbers.\n"
        "Respond with only the corrected reply."
    )


def build_english_regeneration_prompt() -> str:
    """Ask the model to re-emit its previous reply in standard English.

    The platform requires English-only final replies (the zero-fabrication
    numeric-claim gate ships English regex only).  Instead of hard-blocking a
    non-English draft, the agent loop requests one English rewrite that keeps
    every number, unit, and citation verbatim.
    """
    return (
        "STOP. Your previous reply contained non-English (CJK / full-width) "
        "text. The platform requires the final reply to be in standard "
        "English.\n\n"
        "Rewrite your previous reply in standard English:\n"
        "1. Preserve every numeric value, unit, citation, bibcode, "
        "designation, and tool-backed fact EXACTLY as in the draft — do not "
        "add, drop, or alter any number or reference.\n"
        "2. Translate only the prose; keep equations and identifiers "
        "unchanged.\n"
        "3. Do not introduce any claim that was not already in the draft.\n"
        "4. Output the English version only, with no preamble or apology."
    )


def build_zero_data_qualitative_regeneration_prompt(result: ValidationResult) -> str:
    """Ask the model for a qualitative-only rewrite after a zero-data trip.

    This is intentionally narrower than the normal regeneration prompt.  When
    no citable numeric universe exists, a methodological question can still
    have a useful answer, but every number, threshold, sigma level, parameter
    value, redshift boundary, sample count, or named quantitative result must
    disappear.
    """
    return (
        "STOP. This turn produced no citable numeric tool results.\n\n"
        + result.describe()
        + "\n\nRewrite your previous reply as a qualitative-only answer:\n"
        "1. Remove every numeric value, numeric range, sigma/significance, "
        "redshift boundary, dataset count, parameter value, and equation "
        "coefficient.\n"
        "2. Do not replace removed numbers with new approximations.\n"
        "3. If the user asked about a method or expected scientific behaviour, "
        "you may describe qualitative expectations, caveats, and next steps.\n"
        "4. State that no quantitative result was determined in this turn.\n"
        "5. Do not add author-year citations, bibcodes, DOIs, or arXiv IDs "
        "unless they were already present in verified tool results.\n"
        "6. Do not call tools. Do not mention internal validator labels or "
        "platform function names.\n"
        "7. Reply in standard English only.\n\n"
        "Respond with only the corrected qualitative reply."
    )


def _claim_display_text(claim: Claim) -> str:
    raw = " ".join(str(claim.raw or "").split())
    if raw:
        return f"- {raw}"
    return f"- {claim.value:g}"


def blocked_reply_text(result: ValidationResult) -> str:
    """Fallback shown to the user when the LLM cannot stop fabricating."""
    lines = [_claim_display_text(c) for c in result.uncited]
    universe_snippet = (
        f"Tools returned {result.universe_size} distinct numeric values this turn"
        + (f" (sample: {result.universe_sample[:10]})" if result.universe_sample else " (empty).")
    )
    strict_note = (
        "\nStrict validation was on (tool-result universe was thin)."
        if result.strict_mode else ""
    )
    return (
        "⚠ Reply withheld: the model attempted to cite values that were not "
        "produced by this turn's tools. The unsupported numeric phrases were:\n\n"
        + "\n".join(lines)
        + f"\n\n{universe_snippet}{strict_note}"
        + "\n\nPlease ask for a cited data lookup / fit, or keep the answer "
        "qualitative until the relevant data are available."
    )


def _redact_uncited_phrases(reply: str, uncited: list[Claim]) -> str:
    """Stage 6 P0 (2026-05-19): replace each uncited claim's raw phrase with
    `[unverified: N]` using its precise (start, end) char offsets.

    Processes claims back-to-front so earlier offsets remain valid as we
    mutate. Overlapping spans are de-duplicated (keep the earliest).
    """
    if not uncited or not reply:
        return reply
    sorted_claims = sorted(uncited, key=lambda c: c.start)
    deduped: list[Claim] = []
    last_end = -1
    for c in sorted_claims:
        if c.start >= last_end and 0 <= c.start < c.end <= len(reply):
            deduped.append(c)
            last_end = c.end
    out = reply
    for c in reversed(deduped):
        replacement = f"[unverified: {c.value:g}]"
        out = out[:c.start] + replacement + out[c.end:]
    return out


def attach_draft_to_banner(
    banner: str,
    original_reply: str,
    title: str = "AI's draft response (provenance check failed — see above)",
) -> str:
    """Stage 6 P0 follow-up (2026-05-19): general helper that appends the AI's
    draft to any hard-block banner.

    Differs from `blocked_reply_with_narrative`: does not redact numbers, only
    attaches. Used for literature_narrative / citation / methodology detectors —
    they flag whole phrases rather than numbers, so redaction makes the narrative
    meaningless. The banner already lists line-number locations
    (e.g. `... (line 13)`) so the user can find the flagged segment.

    Falls back to banner-only if `original_reply` is empty/whitespace.
    """
    stripped = (original_reply or "").strip()
    if not stripped:
        return banner
    return (
        banner
        + "\n\n---\n\n"
        + f"## {title}\n\n"
        + original_reply
    )


def blocked_reply_with_narrative(
    result: ValidationResult,
    original_reply: str,
) -> str:
    """Stage 6 P0 (2026-05-19): preserve AI's narrative while flagging uncited
    numbers. Numeric-uncited path only — numbers are replaced with
    `[unverified: N]` inside the original narrative, then assembled into the
    final output via `attach_draft_to_banner`.

    Previous behavior (`blocked_reply_text` alone): wholesale replace AI reply
    with the banner — users lost methodology/caveats/qualitative reasoning.

    If `original_reply` is empty/whitespace, falls back to banner-only.
    """
    banner = blocked_reply_text(result)
    stripped_original = (original_reply or "").strip()
    if not stripped_original:
        return banner
    redacted = _redact_uncited_phrases(original_reply, result.uncited)
    return attach_draft_to_banner(
        banner,
        redacted,
        title="AI's draft response (uncited numbers redacted)",
    )


def is_empty_turn(tool_results: Any) -> bool:
    """F1.4: was this turn's tool output effectively empty?

    Used to hard-block any quantitative claim when the agent ran tools but
    none of them produced real data (e.g., ADQL returned 0 rows, Python
    sandbox crashed, search_objects returned []).  Any status string
    containing EMPTY / FAILED / UNAVAILABLE counts a tool as empty.

    The agent loop accumulator shape is:
        [{"tool": "run_adql", "input": {...}, "result": {...}}, ...]
    We look at each entry's ``result`` (if present) AND at the entry
    itself, so both shapes work.
    """
    if tool_results is None:
        return True
    if isinstance(tool_results, (list, tuple)):
        items = list(tool_results)
    elif isinstance(tool_results, dict):
        items = [tool_results]
    else:
        return False
    if not items:
        return True
    for entry in items:
        if not isinstance(entry, dict):
            return False
        # Prefer the inner result when the entry is an accumulator record.
        inner = entry.get("result") if isinstance(entry.get("result"), dict) else entry
        outer = entry  # outer may still hold status/error for orchestrator records

        status_tokens: list[str] = []
        for src in (inner, outer):
            for key in ("analysis_status", "__tool_status__", "status"):
                v = src.get(key)
                if isinstance(v, str):
                    status_tokens.append(v.upper())

        row_count = inner.get("row_count")
        if row_count is None:
            row_count = outer.get("row_count")

        has_payload = bool(inner.get("rows") or inner.get("data")
                           or inner.get("stdout") or inner.get("results")
                           or inner.get("figures") or inner.get("variables"))

        partial_with_payload = (
            any(tok == "PARTIAL" for tok in status_tokens)
            and has_payload
            and inner.get("__do_not_claim__") is not True
            and outer.get("__do_not_claim__") is not True
            and str(inner.get("data_origin") or "").lower() != "synthetic"
            and str(outer.get("data_origin") or "").lower() != "synthetic"
        )

        # EXPLORATORY (2026-05-20): cosmology MCMC chain with ESS/R-hat below
        # publication threshold but above exploratory floor + claimable input.
        # The posterior may be discussed in chat; this turn is therefore not
        # an empty turn even when status tokens look concerning.
        exploratory_unblocked = (
            any(tok == "EXPLORATORY" for tok in status_tokens)
            and inner.get("__do_not_claim__") is not True
            and outer.get("__do_not_claim__") is not True
        )

        synthetic_or_unciteable = (
            inner.get("__do_not_claim__") is True
            or outer.get("__do_not_claim__") is True
            or str(inner.get("data_origin") or "").lower() == "synthetic"
            or str(outer.get("data_origin") or "").lower() == "synthetic"
            or any(tok in {"SYNTHETIC", "SIMULATED_DEMO"} for tok in status_tokens)
        )

        explicit_fail = (
            not partial_with_payload
            and not exploratory_unblocked
            and (
                inner.get("success") is False
                or outer.get("success") is False
                or bool(inner.get("error"))
                or bool(outer.get("error"))
                or any(tok in {"EMPTY", "FAILED", "UNAVAILABLE"} for tok in status_tokens)
                or row_count == 0
                or synthetic_or_unciteable
            )
        )
        if explicit_fail:
            continue
        if inner is entry and not has_payload and not outer.get("result"):
            # Raw entry with no explicit failure but no data either.
            continue
        return False
    return True


def zero_data_but_quantitative(reply: str, tool_results: Any) -> list[Claim]:
    """F1.4: when every tool is empty/failed, return the numeric claims
    the reply still makes.  Callers short-circuit to the block path
    without letting the regen loop launder the claim.
    """
    if not is_empty_turn(tool_results):
        return []
    return extract_claims(reply)


def dump_tool_universe(tool_results: Any, limit: int = 50) -> str:
    """Debug helper: sample a few numeric values from the tool tree."""
    vals = list(_iter_numeric_values(tool_results))
    vals.sort()
    return json.dumps(vals[:limit])


_DECLARED_COSMOLOGY_KEYS = frozenset({"cosmology_manifest", "source_cosmology"})


def _iter_declared_cosmology_subtrees(node: Any) -> Iterable[dict]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _DECLARED_COSMOLOGY_KEYS and isinstance(value, dict):
                yield value
            else:
                yield from _iter_declared_cosmology_subtrees(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _iter_declared_cosmology_subtrees(item)


def value_supported_by_cosmology_manifest(
    value: float,
    tool_results: Any,
    tolerance: float = DEFAULT_TOLERANCE,
) -> bool:
    """True when ``value`` matches a number inside a cosmology a tool DECLARED
    this turn (cosmology_manifest / source_cosmology subtrees only; signed
    ±tolerance band, same matching as validate_claims).

    Used by chat.py's cosmology-anchor comparison gate: fit_line_lfr's
    cosmology_manifest carries the Planck18 preset (H0=67.36, Om0=0.3153,
    sigma8=0.8111) the fit assumed, and citing those values in prose is
    provenance-correct. Deliberately NOT matched against the full tool
    universe — with ~10^3 numeric leaves (FWHM errors, S/N, fluxes) a ±1%
    band around any O(10-100) fabricated anchor would almost always hit a
    coincidental match and launder it.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(v):
        return False
    pool: set[float] = set()
    for subtree in _iter_declared_cosmology_subtrees(tool_results):
        pool.update(_iter_numeric_values(subtree))
    if not pool:
        return False
    return _matches_any(v, pool, tolerance)


# W1 (PART W): literature-prior hard blacklist — age/mass/distance citations are
# only allowed in a reply if the corresponding measurement/lookup tool appeared
# in tool_results. Independent of the ±1% universe match: even if 100 happens
# to be in the universe, "age ~100 Myr" is hard-blocked unless the corresponding
# tool was called this turn. Prevents the LLM from laundering textbook priors by
# borrowing a number from a Gaia ADQL row.
_LITERATURE_PRIOR_LABELS_REQUIRE_TOOL: dict[str, tuple[str, ...]] = {
    # age: must be measured (fit_isochrone), cited (search_literature),
    # or fetched from a dossier (get_object_dossier).
    "age_myr": (
        "fit_isochrone", "search_literature", "get_object_dossier",
    ),
    "age_gyr": (
        "fit_isochrone", "search_literature", "get_object_dossier",
    ),
    # mass: in addition to fitting / literature / dossier, may also come
    # from a Gaia / SDSS mass column (run_adql).
    "mass_solar": (
        "fit_isochrone", "search_literature", "get_object_dossier", "run_adql",
    ),
    # distance: Gaia parallax (run_adql / get_object_info), extinction helper
    # (get_extinction returns a distance), dossier, or literature.
    "distance_pc": (
        "run_adql", "search_literature", "get_object_dossier",
        "get_object_info", "get_extinction",
    ),
    "distance_kpc": (
        "search_literature", "get_object_dossier", "get_object_info",
    ),
    "distance_mpc": (
        "search_literature", "get_object_dossier", "get_object_info",
    ),
    # Chinese labels were removed from _PATTERNS (PART X plan D — replies are
    # forced to English), so _zh keys are no longer needed. Chinese prose is
    # blocked upstream by the reply_contains_cjk hardblock in chat.py and
    # never reaches claim extraction.
}


def literature_prior_violations(reply: str, tool_results: Any) -> list[Claim]:
    """W1 (PART W): detect textbook-prior-style age/mass/distance citations.

    When a numeric claim with one of these labels appears in the reply but no
    corresponding successful, citable measurement/lookup tool result is present
    in tool_results (fit_isochrone / search_literature / get_object_dossier /
    run_adql / get_object_info / get_extinction), return the violating claims.
    Stricter than validate_claims' ±1% universe match: independent of whether
    the universe happens to contain a nearby number; judged purely by whether
    a successful, citable result was produced.

    Args:
        reply: the assistant's raw reply text
        tool_results: list of tool call results this turn (each dict has a "tool" key)

    Returns:
        List of violating claims. Empty list means every relevant claim is
        supported by a corresponding tool result.
    """
    successful_tools = _successful_tool_names(tool_results)
    claims = extract_claims(reply)
    violations: list[Claim] = []
    for c in claims:
        base_label = c.label.split(".g", 1)[0]
        allowed = _LITERATURE_PRIOR_LABELS_REQUIRE_TOOL.get(base_label)
        if allowed is None:
            continue
        if not any(t in successful_tools for t in allowed):
            violations.append(c)
    return violations


# X (PART X plan D): enforce English-only replies — any final reply containing
# CJK / Japanese / Korean / full-width punctuation is hard-blocked. Reuses the
# regex range from the PART W figure-language guard (CJK Unified / Hiragana /
# Katakana / Hangul / Full-width). Greek letters (U+0370-U+03FF) and scientific
# Unicode such as Å / ° / ± / ≤ / ≥ are excluded from matching; DejaVu Sans
# renders them correctly so they are permitted.
#
# Rationale for English-only replies:
# 1. Avoids patch-style expansion of label blacklists (infinite word-order
#    variants like "年龄: ~100 Myr" / "N Myr 的年龄" / "大约 N Myr 年纪").
# 2. Aligns with the run_python figure-text rule (PART W) — all numeric output
#    from the platform is consistently in English.
# 3. The research context (paper reproduction / peer review) is inherently
#    English-language work.
_CJK_REPLY_PATTERN = re.compile(
    "["
    "　-〿"  # CJK punctuation
    "぀-ゟ"  # Hiragana
    "゠-ヿ"  # Katakana
    "㐀-䶿"  # CJK Ext A
    "一-鿿"  # CJK Unified Ideographs
    "가-힯"  # Hangul Syllables
    "＀-￯"  # Full-width ASCII
    "]"
)

# Threshold 2: tolerates single-character false positives (e.g. a Chinese
# character like "昴" inside a quote), while prose lead-ins such as
# "根据..." / "符合..." / "与文献一致" (typically 2+ CJK chars) always trigger.
# A natural-language Chinese reply will always far exceed 2 CJK characters.
_CJK_REPLY_THRESHOLD = 2


def reply_contains_cjk(reply: str, threshold: int = _CJK_REPLY_THRESHOLD) -> bool:
    """X (PART X plan D): return True if the final assistant reply contains
    >= ``threshold`` CJK / Japanese / Korean / full-width characters.

    Used as a hard-block signal in ``_run_agent_loop``: English-only
    replies are a platform contract (documented in SYSTEM_PROMPT), and
    violations are caught here as defense in depth — regex patterns
    upstream cover English phrasings only, so Chinese prose would
    otherwise bypass the zero-fabrication gate.
    """
    if not reply:
        return False
    return len(_CJK_REPLY_PATTERN.findall(reply)) >= threshold
