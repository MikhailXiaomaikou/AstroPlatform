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

# Scientific replies commonly carry TeX spacing commands around ``\pm``.
# Keep the grammar deliberately finite: whitespace, non-breaking ``~``, the
# short spacing commands, and ``\quad``/``\qquad``.  Both trusted typed
# extraction and untrusted-transcript detection use this exact separator so a
# formatting variant cannot be accepted by one path and missed by the other.
_SCIENTIFIC_SPACING = (
    r"(?:\s|~|\\[,;:!]|\\[ \t]|\\(?:quad|qquad)(?![A-Za-z]))*"
)
_UNCERTAINTY_OPERATOR = (
    rf"(?:±|\\pm(?![A-Za-z])|\+{_SCIENTIFIC_SPACING}/{_SCIENTIFIC_SPACING}[-−]|"
    rf"\+{_SCIENTIFIC_SPACING}[-−])"
)
_UNCERTAINTY_SEPARATOR = (
    rf"{_SCIENTIFIC_SPACING}{_UNCERTAINTY_OPERATOR}{_SCIENTIFIC_SPACING}"
)

# B3: a refusal or rebuttal can still launder a number by repeating a fake
# earlier-turn / pasted transcript value as a bare ``NUM ± NUM`` pair.  The
# ordinary value_with_error rule deliberately requires a physical unit, so it
# cannot see that shape.  Keep this rule context-bound: bare uncertainty pairs
# are treated as claims only when the *same sentence* identifies an untrusted
# text source.  The validator below makes these labels unconditionally
# uncited, even if an unrelated current tool happens to return the same floats.
_UNTRUSTED_CONTEXT_RE = re.compile(
    r"\b(?:earlier|previous(?:ly)?|pasted|quoted|transcript|"
    r"user(?:[\s\-_‑–—]+)(?:supplied|provided))\b",
    re.I,
)
_UNTRUSTED_CONTEXT_VALUE_WITH_ERROR_RE = re.compile(
    rf"{_NUM}{_UNCERTAINTY_SEPARATOR}"
    rf"([-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?)",
    re.I,
)
_ADJACENT_UNCERTAINTY_RE = re.compile(
    rf"^{_UNCERTAINTY_SEPARATOR}"
    r"([-+−]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?)",
    re.I,
)
_UNCERTAINTY_SEPARATOR_RE = re.compile(_UNCERTAINTY_SEPARATOR, re.I)
_SIGMA_DETECTION_CUE_RE = re.compile(
    r"\b(?:detect(?:ed|ion)?|significan(?:ce|t)|reject(?:ed|ion)?|"
    r"exclud(?:e|ed|es|ing|sion)|preference|prefer(?:s|red)?|evidence|"
    r"tension|conflict(?:s|ed|ing)?|clash(?:es|ed|ing)?|"
    r"disagree(?:s|d|ment|ments)?|at\s+odds|favou?r(?:s|ed|ing)?|"
    r"anomal(?:y|ies)|discrepanc(?:y|ies)|deviat(?:e|ed|es|ion)|"
    r"diverge(?:s|d|nce)?|incompatib(?:le|ility)|"
    r"excess(?:es)?|signal|offset|departure|difference|"
    r"inconsisten(?:cy|t))\b",
    re.I,
)
# Conventional interval-coverage labels are 1σ/2σ/3σ only. Any other sigma
# value can never be exempted as an interval marker, whatever the wording.
_INTERVAL_COVERAGE_SIGMA_LEVELS = frozenset({1.0, 2.0, 3.0})
_SIGMA_INTERVAL_CUE_RE = re.compile(
    r"\b(?:confidence|credible|interval|uncertaint(?:y|ies)|"
    r"error(?:\s*bars?)?|posterior|constraint|hdi|quantile|percentile|"
    r"lower|upper|bound|limit)\b",
    re.I,
)
_SENTENCE_BREAK_RE = re.compile(r'(?:\n+|[.!?](?:["\')\]]+)?\s+)')
_UNTRUSTED_CONTEXT_LABEL = "untrusted_context_value_with_error"

_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Cosmology results are often rendered as Markdown tables.  A cell
    # separator may sit between the parameter (and its unit) and the numeric
    # value, so the ordinary prose patterns below cannot see the claim.  Keep
    # these parameter-specific: a generic "any table number" rule would turn
    # row indices and sample counts into scientific measurements.
    ("cosmology_h0", re.compile(
        rf"^[ \t]*(?:\|[ \t]*)?[ \t*`$]*(?:H_?\{{?0\}}?|H₀)(?![A-Za-z0-9_])[^|\n]*\|[ \t*`$]*(?:[=≈~][ \t]*)?{_NUM}\b",
        re.I | re.M,
    )),
    ("cosmology_om0", re.compile(
        rf"^[ \t]*(?:\|[ \t]*)?[ \t*`$]*(?:Om0|(?:\\?Omega|Ω)_?\{{?m\}}?|OmegaM|Ωₘ|ΩM)(?![A-Za-z0-9_])[^|\n]*\|"
        rf"[ \t*`$]*(?:[=≈~][ \t]*)?{_NUM}\b",
        re.I | re.M,
    )),
    ("cosmology_ns", re.compile(
        rf"^[ \t]*(?:\|[ \t]*)?[ \t*`$]*(?:n_?\{{?s\}}?|nₛ)(?![A-Za-z0-9_])[^|\n]*\|[ \t*`$]*(?:[=≈~][ \t]*)?{_NUM}\b",
        re.I | re.M,
    )),
    ("cosmology_tau", re.compile(
        rf"^[ \t]*(?:\|[ \t]*)?[ \t*`$]*(?:\\?tau(?:_?reio)?|τ)(?![A-Za-z0-9_])[^|\n]*\|[ \t*`$]*(?:[=≈~][ \t]*)?{_NUM}\b",
        re.I | re.M,
    )),
    ("cosmology_ombh2", re.compile(
        rf"^[ \t]*(?:\|[ \t]*)?[ \t*`$]*(?:ombh2|(?:\\?omega|\\?Omega|Ω)_?\{{?b\}}?[ \t*]*h(?:\^?\{{?2\}}?|²)|ω_?b)(?![A-Za-z0-9_])"
        rf"[^|\n]*\|[ \t*`$]*(?:[=≈~][ \t]*)?{_NUM}\b",
        re.I | re.M,
    )),
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
        rf"\b(?:H_?\{{?0\}}?|H₀)[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}"
        rf"(?:[ \t]*km[ \t]*s(?:ec)?(?:ond)?[ \t]*[-/]?[ \t]*Mpc(?:\^-?1)?|[ \t]*km/?s/?Mpc)?\b",
        re.I,
    )),
    ("cosmology_om0", re.compile(
        rf"\b(?:Om0|(?:Omega|Ω)_?\{{?m\}}?|OmegaM|Ωₘ|ΩM)[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}\b",
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
    ("cosmology_ns", re.compile(
        rf"\b(?:n_?\{{?s\}}?|nₛ)[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}\b",
        re.I,
    )),
    ("cosmology_tau", re.compile(
        rf"\b(?:tau(?:_?reio)?|τ)[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}\b",
        re.I,
    )),
    ("cosmology_ombh2", re.compile(
        rf"\b(?:ombh2|(?:omega|Omega|Ω)_?\{{?b\}}?[ \t]*h(?:\^?\{{?2\}}?|²)|ω_?b)"
        rf"[ \t]*(?:is|was|=|≈|~|:|about|approximately)?[ \t]*{_NUM}\b",
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
    ("p_value", re.compile(
        rf"\bp\s*(?:=|<|>|≤|≥)\s*{_NUM}\b",
        re.I,
    )),
    # Model-comparison prose rarely uses the label-colon shape. Cover both
    # direct forms ("delta chi-squared is -4.6", "Δχ² = -4.6") and verb-first
    # forms ("improves chi-squared by -4.6"). Without this, an unattested
    # posterior-draw delta could evade the numeric gate simply by using "by".
    ("chi_squared", re.compile(
        rf"(?:\b(?:delta\s+|change\s+in\s+)?chi(?:[-\s]*(?:squared?|square)|\s*\^?\s*2|2)\b|"
        rf"(?:Δ\s*)?χ\s*(?:²|\^?\s*2))"
        rf"\s*(?:is|was|equals?|=|:|≈|~|of|by)?\s*{_NUM}\b",
        re.I,
    )),
    ("chi_squared", re.compile(
        rf"\b(?:improves?|improved|reduces?|reduced|changes?|changed)\s+"
        rf"(?:the\s+)?(?:chi(?:[-\s]*(?:squared?|square)|\s*\^?\s*2|2)\b|"
        rf"χ\s*(?:²|\^?\s*2))\s*(?:by|to|of|=|:)?\s*{_NUM}\b",
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
            "The following numeric claims are NOT supported by admissible "
            "current-turn tool evidence and must be removed or replaced with "
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

    2026-07-03: delegates to ``_strip_markdown_code_with_map`` so the two can
    never drift. Gates that report line numbers or slice sentence/line context
    must call the ``_with_map`` variant directly and translate match offsets
    back to the original reply — slicing the original text with a stripped
    offset was the code-block offset bug that disarmed the
    full_likelihood_overclaim gate.
    """
    stripped, _ = _strip_markdown_code_with_map(text)
    return stripped


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
_SCI_PRODUCT_SIGNS = frozenset("×✕⨯xX·∙⋅*")
# bare 10^exp with an implicit mantissa of 1 ("~10^8 M_sun")
_SCI_BARE_POWER = re.compile(r"(?<![\d.eE])10\s*(?:\^|\*\*)\s*([-+]?\d+)")


def _match_sci_mantissa_power(
    text: str,
    start: int,
) -> tuple[int, str, str] | None:
    """Parse ``mantissa × 10^exponent`` at ``start`` without backtracking.

    This deliberately mirrors the former regex grammar while scanning each
    component once.  The left boundary prevents a match from starting inside
    a longer decimal, just like the old negative lookbehind.
    """
    size = len(text)
    if start >= size or (
        start > 0
        and (text[start - 1].isdecimal() or text[start - 1] == ".")
    ):
        return None

    index = start
    if text[index] in "+-":
        index += 1
    integer_start = index
    while index < size and text[index].isdecimal():
        index += 1
    if index == integer_start:
        return None

    if index < size and text[index] == ".":
        fraction_start = index + 1
        fraction_end = fraction_start
        while fraction_end < size and text[fraction_end].isdecimal():
            fraction_end += 1
        if fraction_end > fraction_start:
            index = fraction_end
    mantissa_end = index

    while index < size and text[index].isspace():
        index += 1
    if index >= size or text[index] not in _SCI_PRODUCT_SIGNS:
        return None
    index += 1

    while index < size and text[index].isspace():
        index += 1
    if not text.startswith("10", index):
        return None
    index += 2

    while index < size and text[index].isspace():
        index += 1
    if index < size and text[index] == "^":
        index += 1
    elif text.startswith("**", index):
        index += 2
    else:
        return None

    while index < size and text[index].isspace():
        index += 1
    exponent_start = index
    if index < size and text[index] in "+-":
        index += 1
    exponent_digits = index
    while index < size and text[index].isdecimal():
        index += 1
    if index == exponent_digits:
        return None

    return index, text[start:mantissa_end], text[exponent_start:index]


def _replace_sci_mantissa_power_with_map(
    text: str,
    bmap: list[int] | None = None,
) -> tuple[str, list[int]]:
    """Normalize mantissa powers in one pass while preserving source offsets."""
    if bmap is not None and len(bmap) != len(text) + 1:
        raise ValueError("boundary map length must equal len(text) + 1")

    out_chars: list[str] = []
    out_map: list[int] = []
    index = 0
    while index < len(text):
        parsed = _match_sci_mantissa_power(text, index)
        if parsed is None:
            out_chars.append(text[index])
            if bmap is not None:
                out_map.append(bmap[index])
            index += 1
            continue

        end, mantissa, exponent = parsed
        replacement = f"{mantissa}e{exponent}"
        out_chars.append(replacement)
        if bmap is not None:
            out_map.extend([bmap[index]] * len(replacement))
        index = end

    if bmap is not None:
        out_map.append(bmap[len(text)])
    return "".join(out_chars), out_map


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
    # 2) mantissa × 10^exp → mantissa e exp.  A single-pass parser avoids
    # polynomial regex backtracking on long user-provided digit runs.
    text, _ = _replace_sci_mantissa_power_with_map(text)
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
    text, bmap = _replace_sci_mantissa_power_with_map(text, bmap)
    text, bmap = _apply_regex_with_map(
        text, bmap, _SCI_BARE_POWER, lambda m: f"1e{m.group(1)}"
    )
    return text, bmap


def _strip_markdown_code_with_map(text: str) -> tuple[str, list[int]]:
    """``_strip_markdown_code`` plus a boundary map back to the input text.

    Gates that regex over the code-stripped reply but report user-facing line
    numbers (or slice sentence/line context) must translate match offsets back
    to the original reply: a multi-line code block collapses to one space, so
    a raw stripped offset points EARLIER in the reply — context slices can
    land inside the code block (feeding code tokens like ``not``/``requires``
    to a non-claim qualifier check and disarming the gate) and "(line N)"
    pointers drift. Reuses the B15 boundary-map machinery; the two regexes and
    replacements mirror ``_strip_markdown_code`` exactly so matching behaviour
    is unchanged.
    """
    bmap = list(range(len(text) + 1))
    text, bmap = _apply_regex_with_map(
        text, bmap, re.compile(r"```.*?```", re.DOTALL), lambda m: " "
    )
    text, bmap = _apply_regex_with_map(
        text, bmap, re.compile(r"`[^`\n]*`"), lambda m: " "
    )
    return text, bmap


def _sentence_context_for_span(text: str, start: int, end: int) -> str:
    """Return the sentence-like region containing ``[start, end)``.

    Sentence breaks require terminal punctuation followed by whitespace (or a
    newline), so decimal points inside the uncertainty pair are not mistaken
    for boundaries.  This is intentionally local: an untrusted marker in a
    neighbouring sentence must not taint a current-tool result.
    """
    context_start = 0
    for boundary in _SENTENCE_BREAK_RE.finditer(text, 0, start):
        context_start = boundary.end()
    next_boundary = _SENTENCE_BREAK_RE.search(text, end)
    context_end = next_boundary.start() if next_boundary else len(text)
    return text[context_start:context_end]


def _sigma_is_interval_marker(text: str, claim: Claim) -> bool:
    """True when ``Nσ`` labels uncertainty coverage, not significance.

    A bare ``1σ`` beside a parameter error bar is conventional interval
    notation.  Treating it as an independent detection significance creates a
    false unsupported claim.  Explicit detection/significance/tension cues
    remain authoritative and keep the ordinary significance claim.

    Fail closed on the value itself: cue word lists are never complete
    ("posteriors conflict at 4.6σ" once slipped through on the strength of a
    nearby "posterior"), so only the conventional 1σ/2σ/3σ coverage labels
    are ever eligible for this exemption.
    """

    if claim.value not in _INTERVAL_COVERAGE_SIGMA_LEVELS:
        return False

    clause, claim_start, claim_end = _claim_clause(text, claim)

    def distance_to_claim(start: int, end: int) -> int:
        if end <= claim_start:
            return claim_start - end
        if start >= claim_end:
            return start - claim_end
        return 0

    def nearest(pattern: re.Pattern[str]) -> int | None:
        distances = [
            distance_to_claim(match.start(), match.end())
            for match in pattern.finditer(clause)
        ]
        return min(distances) if distances else None

    detection_distance = nearest(_SIGMA_DETECTION_CUE_RE)
    interval_distances = [
        distance
        for distance in (
            nearest(_UNCERTAINTY_SEPARATOR_RE),
            nearest(_SIGMA_INTERVAL_CUE_RE),
        )
        if distance is not None
    ]
    interval_distance = min(interval_distances, default=None)
    if interval_distance is None:
        return False
    # Fail closed on a tie: an equally near detection cue means the sigma
    # value still carries a significance interpretation and must be checked.
    return detection_distance is None or interval_distance < detection_distance


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
    original_text = text
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

    # ``1σ`` in a confidence/uncertainty clause annotates interval coverage;
    # it is not a second, detection-significance result.  Drop only that
    # generic significance match, leaving the typed central/error claims (and
    # explicit detection-significance wording) intact.
    claims = [
        claim
        for claim in claims
        if claim.label != "significance_sigma"
        or not _sigma_is_interval_marker(original_text, claim)
    ]

    # B3: identify both halves of a bare uncertainty pair whose sentence says
    # the value came from untrusted prose (earlier/previous/pasted/quoted/etc.).
    # Append these even when a generic rule already saw the same span; the
    # de-duplication priority below deliberately keeps the tainted label.
    for match in _UNTRUSTED_CONTEXT_VALUE_WITH_ERROR_RE.finditer(text):
        if not _UNTRUSTED_CONTEXT_RE.search(
            _sentence_context_for_span(text, match.start(), match.end())
        ):
            continue
        span = match.span()
        for grp_idx in (1, 2):
            value = float(match.group(grp_idx))
            if not math.isfinite(value):
                continue
            claims.append(Claim(
                label=f"{_UNTRUSTED_CONTEXT_LABEL}.g{grp_idx}",
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

    # Parameter-specific patterns stop at the central value before a ``±``.
    # Extract the adjacent error directly from that typed claim, independent
    # of units. This covers dimensionless S8/Ωm/w parameters and standard
    # Unicode units such as ``km s⁻¹ Mpc⁻¹`` that the generic unit catalogue
    # cannot enumerate safely.
    adjacent_uncertainties: list[Claim] = []
    for central_claim in list(claims):
        if not central_claim.label.startswith("cosmology_") or central_claim.label.endswith(
            "_uncertainty"
        ):
            continue
        tail = original_text[central_claim.end : central_claim.end + 96]
        adjacent = _ADJACENT_UNCERTAINTY_RE.match(tail)
        if adjacent is None:
            continue
        try:
            error_value = float(adjacent.group(1).replace("−", "-"))
        except ValueError:
            continue
        if not math.isfinite(error_value):
            continue
        error_end = central_claim.end + adjacent.end()
        adjacent_uncertainties.append(
            Claim(
                label=f"{central_claim.label}_uncertainty",
                raw=original_text[central_claim.start:error_end].strip(),
                value=error_value,
                start=central_claim.start,
                end=error_end,
            )
        )
    claims.extend(adjacent_uncertainties)

    # A generic ``value ± error`` match spans only the number pair, while a
    # parameter-specific cosmology match spans the label and central value.
    # Preserve that parameter identity on the uncertainty half before overlap
    # de-duplication so neither half can fall back to the flat numeric pool.
    for claim in claims:
        if claim.label != "value_with_error.g2":
            continue
        parameter_matches = [
            candidate
            for candidate in claims
            if candidate.label.startswith("cosmology_")
            and not candidate.label.endswith("_uncertainty")
            and not (
                claim.end <= candidate.start or claim.start >= candidate.end
            )
        ]
        if len({candidate.label for candidate in parameter_matches}) == 1:
            claim.label = f"{parameter_matches[0].label}_uncertainty"

    # L1: span-overlap dedup.  Two patterns may both match the same numeric
    # value at overlapping character ranges ("parallax is 9.00 mas" + "9.00
    # mas"); keep only the one with the wider span (= more context).
    if len(claims) <= 1:
        return claims
    # Tainted-context labels take priority over ordinary matches even when an
    # ordinary unit-bearing pattern spans more text.  Otherwise a pasted
    # ``71.43 ± 0.31 km/s`` could lose its fail-closed label during de-dup.
    claims_sorted = sorted(
        claims,
        key=lambda c: (
            not c.label.startswith(f"{_UNTRUSTED_CONTEXT_LABEL}."),
            0
            if c.label.startswith("cosmology_")
            else 2
            if c.label == "value_with_error.g1"
            else 1,
            -(c.end - c.start),
        ),
    )
    kept: list[Claim] = []
    for c in claims_sorted:
        redundant = False
        for k in kept:
            # Preserve both halves when central value and uncertainty happen
            # to be numerically equal (``1 ± 1``): g1 and g2 are independent
            # claims even though their value/span de-dup keys coincide.
            if (
                c.label.startswith(f"{_UNTRUSTED_CONTEXT_LABEL}.")
                and k.label.startswith(f"{_UNTRUSTED_CONTEXT_LABEL}.")
                and c.label != k.label
                and c.start == k.start
                and c.end == k.end
            ):
                continue
            # The centre and uncertainty are separate scientific statistics,
            # even for the edge case ``H0 = 1 ± 1`` where their values match.
            if (
                c.label.startswith("cosmology_")
                and k.label.startswith("cosmology_")
                and c.label.endswith("_uncertainty")
                != k.label.endswith("_uncertainty")
            ):
                continue
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
    is_model_comparison = bool(
        "comparison_valid" in payload
        and any(key in payload for key in ("delta_chi2", "delta_aic", "delta_bic"))
    )
    model_comparison_unattested = bool(
        is_model_comparison
        and not (
            payload.get("comparison_valid") is True
            and payload.get("baseline_chi2_kind") == "likelihood_only_mle"
            and payload.get("extended_chi2_kind") == "likelihood_only_mle"
            and isinstance(payload.get("likelihood_fingerprint"), str)
            and payload.get("likelihood_fingerprint", "").strip()
        )
    )
    return (
        payload.get("__do_not_claim__") is True
        or "SYNTHETIC" in status_values
        or "SIMULATED_DEMO" in status_values
        or model_comparison_unattested
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
    # Model-authored tool arguments (2026-06-12, the numeric twin of the B4
    # citation-pool rule): numbers under any nested "input" key are what the
    # model ASKED, not what a tool RETURNED — without this, echoing a
    # fabricated value into any tool argument launders it into the claim
    # universe (top-level accumulator inputs are also stripped structurally
    # by _result_only_nodes; this catches nested copies, e.g. a tool result
    # that embeds the turn's tool_calls).
    "input", "tool_input", "_turn_tool_results",
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
    # Hash/digest fields — hex strings whose digit runs and 'e'-separated
    # fragments would otherwise enter the numeric universe (2026-06-12: the
    # union3 chain's always-on artifact_sha256 digest validated a fabricated
    # H0=64.3 — the same laundering class as bibcodes). data_hash
    # (fit_cosmology_emcee) and files_sha256 (cobaya_runner) close the
    # remaining emitters of the class.
    "sha256", "mean_sha256", "artifact_sha256", "runner_hash", "result_hash",
    "config_hash", "digest", "data_hash", "files_sha256",
    "likelihood_fingerprint",
    # input_hash (build_evidence_graph node, _stable_hash of the model-supplied
    # input) is the same hex-digit-run class — and it is derived from
    # attacker-influenced input (2026-06-12 review #7/#11/#15).
    "input_hash",
})


# 2026-06-12 (review of the input-echo fix): a fabricated number can re-enter
# the claim universe even after the accumulator `input` field is stripped,
# because several tools COPY the model's own arguments into their RESULT body
# for UI/reproducibility (run_adql -> result["query"], query_high_velocity_stars
# -> result["params"], query_gaia_cluster -> result["center_ra"]/["radius_deg"],
# run_research_matrix -> result["research_plan"]). Those echoed values are what
# the model ASKED, not what a tool MEASURED, so their entire subtree is skipped.
# Real measured aggregates live under distinct keys (median_parallax_mas,
# mean_pmra, …) and are unaffected. Rendered research deliverables
# (markdown/paper draft/bibtex) are a RENDERING of evidence, not evidence —
# skipped as whole subtrees so a list/dict-valued variant cannot leak (the
# string-only _FREETEXT_KEYS skip was value-type fragile, review #3).
_NON_EVIDENCE_KEYS: frozenset[str] = frozenset({
    # Input-alias / echoed-argument keys
    "query", "params", "arguments", "kwargs", "research_plan",
    "center_ra", "center_dec", "radius_deg", "parallax_center_mas",
    "pmra_center", "pmdec_center", "ruwe_max",
    "original_radius_deg", "final_radius_deg",
    # Derived-from-unvalidated-input statistics (2026-06-12 review): a tension
    # significance computed from a model-supplied (unvalidated) `claimed`
    # published value is not a measurement — the model can solve the claim to
    # make the tension any target. The real `reproduced` value stays claimable
    # under its own key.
    "tension_sigma", "claimed", "claimed_value", "claimed_sigma",
    # Rendered deliverables (whole-subtree skip, not value-type dependent)
    "markdown", "paper_draft_markdown", "report_markdown", "bibtex",
    # Diagnostic warning lists (2026-07-07, backlog P3b): `warnings` is a
    # LIST of prose strings on nearly every runner result, so the string-only
    # _FREETEXT_KEYS skip never fired and numbers inside the prose ("w=-1
    # held fixed", "fit 1042 supernovae", years) leaked into the claimable
    # universe — the same value-type fragility review #3 fixed for markdown.
    # Diagnostic numbers a model may honestly quote (ESS / R-hat) keep
    # structured siblings (chain_diagnostics.*.ess_bulk, proposal_ess), so
    # the prose-skip does not orphan them — pinned by the red-team cases
    # numeric_in_warnings_list_not_in_universe /
    # warnings_prose_with_structured_diagnostics_sibling_stays_claimable.
    "warnings",
    # Dataset registry Gaussian records are provenance/configuration metadata.
    # Their hand-entered posterior/proposal means must not ground a fresh
    # constraint merely because a full registry entry appears in datasets_used.
    "compressed_likelihood",
    # Chain download references (2026-07-24): storage keys, uuids, sha256
    # digests, and byte sizes of the persisted getdist chain files are
    # reproducibility metadata, not measurements — their digits must never
    # enter the claimable numeric universe. Named `chain_downloads` (not
    # `chain_artifacts`) to avoid colliding with the research-alpha
    # manifest's attested chain_artifacts records, whose diagnostics must
    # stay in the universe if they ever surface in a chat result.
    "chain_downloads",
})


# A statistical record can be copied under several different envelope keys
# (``compressed_likelihood``, ``registered_parameter_block``, a data-product
# ``parse`` object, or a tension row).  Key-name blacklists alone therefore do
# not establish that its numbers are evidence.  Posterior summaries and
# proposal records retain their means/covariances for provenance and UI
# context, but those numbers did not come from the current computation and
# must never certify a fresh numerical claim.
_CONTEXT_ONLY_STATISTICAL_ROLES: frozenset[str] = frozenset({
    "published_posterior_summary",
    "proposal_only",
})
_CONTEXT_ONLY_STATISTICAL_SCOPES: frozenset[str] = frozenset({
    "context_only",
    "literature_context",
    "proposal_context",
    "literature_context_metadata",
    "proposal_context_metadata",
    "registered_gaussian_context_only",
})


def _is_context_only_statistical_payload(payload: Any) -> bool:
    """Whether a result subtree is statistical context rather than evidence."""

    if not isinstance(payload, dict):
        return False
    role = str(payload.get("statistical_role") or "").strip().lower()
    if role in _CONTEXT_ONLY_STATISTICAL_ROLES:
        return True
    for key in (
        "statistical_scope",
        "compressed_record_scope",
        "anchor_scope",
        "claim_scope",
    ):
        scope = str(payload.get(key) or "").strip().lower()
        if scope in _CONTEXT_ONLY_STATISTICAL_SCOPES:
            return True
    return False


# Cosmology-manifest result keys that may echo a model-authored legacy
# cosmology spec; their numbers are skipped UNLESS the manifest carries a
# real bibcode (a curated preset, citeable as provenance). See _iter_numeric_values.
_COSMOLOGY_MANIFEST_KEYS: frozenset[str] = frozenset({
    "target_cosmology", "current_cosmology", "cosmology_manifest",
    "source_cosmology", "assumed_cosmology",
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
    # Rendered research deliverables (2026-06-12): report/paper prose is a
    # RENDERING of evidence, not evidence — numbers inside it must ground via
    # the underlying tool results, never via the rendered text (an export of
    # tainted text would otherwise re-ground its own numbers).
    "paper_draft_markdown", "report_markdown", "bibtex",
    # Computation source strings (2026-06-12 review): run_python `code` (and
    # kin) carry initial guesses / grid sizes / fiducial constants that a fit
    # legitimately recovers. Tokenizing them would both pollute the universe
    # AND (via the input-subtraction) wrongly remove a genuine result that
    # numerically equals a code literal (e.g. bins=20 vs a real count of 20).
    "code", "script", "expression", "formula",
    # Provenance methodology prose (2026-06-12 DR12 review BLOCKER): the
    # compressed_sources records carry arXiv ids inside source_locator /
    # approximation strings. "arXiv:1607.03155" tokenizes to 1607.03155 —
    # IN-BAND for the DR12 dimensional convention (D_M·rs_fid/r_d ~ 1512-2307
    # Mpc), so a fabricated "D_M = 1607 Mpc" validated while the true released
    # value was blocked. These strings describe methodology; no measurement
    # may ground on them. Legitimate constants they mention (rs_fid) are
    # exposed as structured numeric fields instead.
    "source_locator", "approximation",
    # Registry naming prose (2026-06-12 eBOSS-grid review, same laundering
    # family): dataset version/display_name strings carry table dimensions and
    # grid shapes ("released 399-point probability table", "50×50") that
    # validated fabricated count claims ("the fit used 399 quasars"). The
    # honest structured siblings (z_coverage, rows fields outside blacklists)
    # remain harvested.
    "version", "display_name", "dataset_version", "dataset_display_name",
})


def _result_only_nodes(tool_results: Any) -> list[Any]:
    """Strip tool INPUTS from accumulator-shaped entries before any numeric
    harvest — the numeric twin of _citation_pool_nodes' B4 rule.

    The agent-loop accumulator shape is [{"id", "tool", "input", "result"}].
    The claim universe must be built from what tools RETURNED, never from
    what the model ASKED: otherwise a fabricated number echoed into any tool
    argument (a search query string, a parameter field) validates its own
    claim in the same turn (live-confirmed 2026-06-12). Bare result dicts
    (no envelope) pass through with any top-level "input" key dropped."""
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    nodes: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            nodes.append(entry)
            continue
        # An accumulator envelope ({id, tool, input, result}) carries evidence
        # ONLY in its result subtree — id/tool/input and any other top-level
        # sibling are not measurements. So whenever a "result" key exists,
        # take JUST that value (review #3: a stray sibling like {result:None,
        # H0_guess:73.8} must not leak). Only a bare, already-unwrapped result
        # dict (no "result" key) is walked directly, with "input" dropped.
        if "result" in entry:
            nodes.append(entry["result"])
        else:
            nodes.append({k: v for k, v in entry.items() if k != "input"})
    return nodes


def _model_input_numbers(tool_results: Any) -> set[float]:
    """Union of every model-authored input number across this turn's tool
    calls (accumulator entries shaped {id, tool, input, result}).

    The source set for the structural anti-echo subtraction: a value the model
    put in a DIRECT tool argument (audit_published_constraint `claimed`,
    sensitivity_analysis `base_value`, an unforeseen scalar echo) is removed
    from the claim universe wherever the tool echoes it, closing the per-key
    whack-a-mole. Crucially we harvest with the SAME blacklists as the result
    universe (_iter_numeric_values): computation source strings (`code`),
    config (`priors`/`proposal`/`n_samples`), echoed query/params, and
    citation identifiers are NOT harvested — so a genuine fit output that
    coincides with a code literal or prior bound is not wrongly subtracted
    (2026-06-12 review false-positive fix)."""
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    nums: set[float] = set()
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("input"), (dict, list)):
            nums |= set(_iter_numeric_values(entry["input"]))
    return nums


def _valid_bibcode_present(manifest: dict) -> bool:
    """True only when the manifest carries a REAL bibcode — a curated preset
    (planck18 -> 2020A&A...641A...6P). An ADS bibcode is exactly 19 characters,
    so a truthy-but-fabricated marker ('2099XXXX...FAKE', 15 chars) does not
    qualify even if it passes the loose extraction regex."""
    bib = manifest.get("bibcode")
    if not isinstance(bib, str):
        return False
    return any(len(b) == 19 for b in _bibcodes_from_text(bib))


def _manifest_subtree_is_skippable(val: Any) -> bool:
    """A cosmology-manifest value whose numbers must be skipped (model-authored
    legacy spec). Dicts without a valid bibcode and lists containing any such
    dict are skippable; a dict WITH a valid bibcode (curated preset) is kept."""
    if isinstance(val, dict):
        return not _valid_bibcode_present(val)
    if isinstance(val, list):
        return any(isinstance(v, dict) and not _valid_bibcode_present(v) for v in val)
    return False


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
        if (
            _is_tainted_synthetic_payload(payload)
            or _is_context_only_statistical_payload(payload)
        ):
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
            # 2026-06-12: skip echoed-input + rendered-deliverable subtrees
            # (the result-side twin of the accumulator-input strip).
            if key_str in _NON_EVIDENCE_KEYS:
                continue
            # 2026-06-12 (review #0/#2): a cosmology manifest echoed into a
            # tool result (compare_luminosity_distances -> target_cosmology,
            # fit_line_lfr -> cosmology_manifest) is the ASSUMED cosmology, not
            # a measurement. When it came from a model-authored legacy spec
            # ("FlatLambdaCDM_H73p8_Om0p295" -> bibcode None) its H0/Om0 are
            # the model's own input digits round-tripped — skip them. Curated
            # presets carry a real bibcode (planck18 -> 2020A&A...641A...6P)
            # and stay citeable as provenance.
            if key_str in _COSMOLOGY_MANIFEST_KEYS and _manifest_subtree_is_skippable(val):
                continue
            # P0-a: don't harvest prose digits from free-text field strings
            # (banner text, error messages, suggestions).  Nested structures
            # and numeric values under these keys are still walked.
            if key_str in _FREETEXT_KEYS and isinstance(val, str):
                continue
            yield from _iter_numeric_values(val)
    elif isinstance(payload, (list, tuple)):
        # tuple included (2026-06-12 eBOSS-grid review): raw (non-JSON-round-
        # tripped) tool results carry tuples like z_coverage=(2.334, 2.334);
        # without this branch the honest "at z=2.334" claim grounded ONLY via
        # launderable registry prose.
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
    "ns": "ns", "tau": "tau", "ombh2": "ombh2", "omegabh2": "ombh2",
    # Unit-bearing manifest key used by curated cosmology presets.
    "h0kmsmpc": "H0", "h0kms1mpc1": "H0",
}
# Roots tried longest-first; a key resolves if it equals a root or is the root
# followed by a known statistic suffix.
_COSMO_PARAM_ROOTS: tuple[tuple[str, str], ...] = (
    ("sigma8", "sigma8"), ("omegam", "omegam"), ("omegabh2", "ombh2"),
    ("ombh2", "ombh2"), ("h0rd", "H0_rd"),
    ("h0", "H0"), ("s8", "S8"), ("w0", "w0"), ("wa", "wa"), ("rd", "rd"),
    ("tau", "tau"), ("ns", "ns"),
)
_COSMO_STAT_SUFFIXES: frozenset[str] = frozenset({
    "", "median", "mean", "best", "low", "high", "value", "val",
    "bestfit", "map", "mle", "mode", "estimate", "centralvalue",
    "1sigmalow", "1sigmahigh", "q16", "q84", "hdilow94", "hdihigh94", "std",
    "sigma", "error", "err", "uncertainty", "stderr", "standarderror",
    "standarddeviation",
})

# A statement such as ``H0 = 68.8`` is a central-estimate claim.  It cannot be
# certified by an HDI boundary, standard deviation, error bar, prior bound, or
# another statistic merely because the number is close.  Uncertainty claims
# are still checked by their own generic numeric pattern against the flat
# universe; this set governs only parameter-labelled central values.
_COSMO_CENTRAL_STAT_SUFFIXES: frozenset[str] = frozenset({
    "",
    "median",
    "mean",
    "best",
    "bestfit",
    "map",
    "mle",
    "mode",
    "value",
    "val",
    "estimate",
    "centralvalue",
})
_COSMO_CENTRAL_SUMMARY_KEYS: frozenset[str] = frozenset({
    suffix for suffix in _COSMO_CENTRAL_STAT_SUFFIXES if suffix
})
_COSMO_ROW_CENTRAL_KEYS: frozenset[str] = frozenset({
    "reproduced",
    "reproducedvalue",
})

# Interval endpoints need their own typed universe.  A flat collection of all
# values for a parameter cannot distinguish its posterior centre from an HDI
# edge, and a flat collection of all tool numbers cannot distinguish S8's edge
# from an unrelated observable.  These normalized field names cover the
# result shapes emitted by the native likelihood and Cobaya runners; the
# classifier below also accepts coverage-suffixed HDI/CI and quantile keys.
_COSMO_INTERVAL_LOWER_KEYS: frozenset[str] = frozenset({
    "low",
    "lower",
    "lowerbound",
    "lowerlimit",
    "loweredge",
    "1sigmalow",
    "onesigmalow",
})
_COSMO_INTERVAL_UPPER_KEYS: frozenset[str] = frozenset({
    "high",
    "upper",
    "upperbound",
    "upperlimit",
    "upperedge",
    "1sigmahigh",
    "onesigmahigh",
})
_COSMO_INTERVAL_PAIR_KEYS: frozenset[str] = frozenset({
    "interval",
    "credibleinterval",
    "confidenceinterval",
    "posteriorinterval",
    "quantiles",
    "percentiles",
    "1sigma",
    "onesigma",
    "1sigmainterval",
    "onesigmainterval",
})


def _cosmology_interval_statistic_kind(statistic: Any) -> str | None:
    """Classify a normalized result field as lower/upper/pair interval data."""

    norm = re.sub(r"[^a-z0-9]", "", str(statistic).lower())
    if norm in _COSMO_INTERVAL_LOWER_KEYS:
        return "lower"
    if norm in _COSMO_INTERVAL_UPPER_KEYS:
        return "upper"
    if norm in _COSMO_INTERVAL_PAIR_KEYS:
        return "pair"

    # hdi_low_94 / hdi_94_low / ci_lower_68 and their upper equivalents.
    if re.fullmatch(r"(?:hdi|ci)(?:low|lower)\d*", norm) or re.fullmatch(
        r"(?:hdi|ci)\d*(?:low|lower)", norm
    ):
        return "lower"
    if re.fullmatch(r"(?:hdi|ci)(?:high|upper)\d*", norm) or re.fullmatch(
        r"(?:hdi|ci)\d*(?:high|upper)", norm
    ):
        return "upper"
    if re.fullmatch(r"(?:hdi|ci)\d*", norm):
        return "pair"

    # q16/q84, p025/p975, percentile03/percentile97, etc.  We only need the
    # side of the median here; exact coverage remains attached to provenance.
    quantile = re.fullmatch(r"(?:q|p|percentile)(\d{1,4})", norm)
    if quantile:
        percentile = int(quantile.group(1))
        if percentile < 50:
            return "lower"
        if percentile > 50:
            return "upper"
    return None


def _row_central_values(value: Any) -> Iterable[float]:
    """Yield only the central element from a row-oriented result value."""

    candidate = value[0] if isinstance(value, (list, tuple)) and value else value
    if isinstance(candidate, dict) and (
        _is_tainted_synthetic_payload(candidate)
        or _is_context_only_statistical_payload(candidate)
    ):
        return
    if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
        number = float(candidate)
        if math.isfinite(number):
            yield number
    elif isinstance(candidate, dict):
        for key, nested in candidate.items():
            key_norm = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if key_norm in _COSMO_CENTRAL_SUMMARY_KEYS:
                yield from _row_central_values(nested)


def _cosmology_param_and_statistic(name: Any) -> tuple[str, str] | None:
    """Return ``(canonical_parameter, statistic_suffix)`` for a result key."""

    norm = re.sub(r"[^a-z0-9]", "", str(name).lower())
    if not norm:
        return None
    if norm in _COSMO_PARAM_EXACT:
        return _COSMO_PARAM_EXACT[norm], ""
    for root, canon in _COSMO_PARAM_ROOTS:
        if norm.startswith(root):
            suffix = norm[len(root):]
            if (
                suffix in _COSMO_STAT_SUFFIXES
                or _cosmology_interval_statistic_kind(suffix) is not None
            ):
                return canon, suffix
    return None


def _canonicalize_cosmology_param(name: Any) -> str | None:
    parsed = _cosmology_param_and_statistic(name)
    return parsed[0] if parsed is not None else None


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
    "cosmology_ns": "ns",
    "cosmology_tau": "tau",
    "cosmology_ombh2": "ombh2",
}
_CLAIM_LABEL_TO_COSMO_UNCERTAINTY_PARAM: dict[str, str] = {
    f"{label}_uncertainty": parameter
    for label, parameter in _CLAIM_LABEL_TO_COSMO_PARAM.items()
}


def _claim_cosmology_param(claim: "Claim") -> str | None:
    """Canonical cosmology parameter a claim is about, or None for claims not
    captured by a parameter-specific cosmology pattern."""
    return _CLAIM_LABEL_TO_COSMO_PARAM.get(claim.label)


def _claim_cosmology_uncertainty_param(claim: "Claim") -> str | None:
    return _CLAIM_LABEL_TO_COSMO_UNCERTAINTY_PARAM.get(claim.label)


def _build_cosmology_labeled_universe(
    payload: Any,
    out: dict[str, set[float]] | None = None,
) -> dict[str, set[float]]:
    """Map each cosmology parameter to claimable *central estimates*.

    Parameter-labelled prose such as ``H0 = x`` asserts a central estimate.
    Consequently this universe admits medians, means, best fits/MAP/MLE values,
    and explicit scalar parameter values, but not HDI/quantile boundaries,
    standard deviations, proposal anchors, or pairwise-context values.  Error
    bars and interval claims are validated separately by their own numeric
    patterns against the flat result universe.
    """
    if out is None:
        out = {}
    if isinstance(payload, dict):
        if (
            _is_tainted_synthetic_payload(payload)
            or _is_context_only_statistical_payload(payload)
        ):
            return out
        # Parallel parameters/mean list form. Context/proposal records have
        # already returned above, so only an admissible statistical result can
        # contribute its declared mean here. Covariance-derived edges are not
        # central estimates and are deliberately excluded.
        params = payload.get("parameters")
        mean = payload.get("mean")
        if (
            isinstance(params, (list, tuple))
            and isinstance(mean, (list, tuple))
            and len(params) == len(mean)
            and params
            and all(isinstance(p, str) for p in params)
        ):
            for i, pname in enumerate(params):
                canon = _canonicalize_cosmology_param(pname)
                if (
                    canon is None
                    or isinstance(mean[i], bool)
                    or not isinstance(mean[i], (int, float))
                ):
                    continue
                value = float(mean[i])
                if math.isfinite(value):
                    out.setdefault(canon, set()).add(value)

        # A ``parameter`` field can name a row-oriented central result. Only
        # explicit central-statistic siblings qualify; value_a/value_b, delta,
        # sigma, intervals, dataset ids, and diagnostic prose do not.
        named = payload.get("parameter") or payload.get("param")
        ctx = (
            _canonicalize_cosmology_param(named)
            if isinstance(named, str)
            else None
        )
        for key, val in payload.items():
            key_str = str(key).lower()
            if (
                key_str in _METADATA_KEYS_BLACKLIST
                or key_str in _CITATION_KEYS_BLACKLIST
                or key_str in _NON_EVIDENCE_KEYS
            ):
                continue
            if (
                key_str in _COSMOLOGY_MANIFEST_KEYS
                and _manifest_subtree_is_skippable(val)
            ):
                continue
            parsed_key = _cosmology_param_and_statistic(key)
            if parsed_key is not None:
                canon_key, statistic = parsed_key
                bucket = out.setdefault(canon_key, set())
                if statistic in _COSMO_CENTRAL_STAT_SUFFIXES - {""}:
                    bucket.update(_row_central_values(val))
                elif statistic == "":
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        value = float(val)
                        if math.isfinite(value):
                            bucket.add(value)
                    elif isinstance(val, dict):
                        for stat_key, stat_value in val.items():
                            stat_norm = re.sub(
                                r"[^a-z0-9]", "", str(stat_key).lower()
                            )
                            if stat_norm in _COSMO_CENTRAL_SUMMARY_KEYS:
                                bucket.update(_row_central_values(stat_value))
                # Do not recurse into a parameter summary: its interval/error
                # fields belong to a different claim type.
                continue
            if ctx is not None and key not in {"parameter", "param"}:
                stat_norm = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if stat_norm in _COSMO_CENTRAL_SUMMARY_KEYS:
                    out.setdefault(ctx, set()).update(_row_central_values(val))
                elif stat_norm in _COSMO_ROW_CENTRAL_KEYS:
                    out.setdefault(ctx, set()).update(_row_central_values(val))
                elif isinstance(val, (dict, list, tuple)):
                    _build_cosmology_labeled_universe(val, out)
            else:
                _build_cosmology_labeled_universe(val, out)
    elif isinstance(payload, (list, tuple)):
        for val in payload:
            _build_cosmology_labeled_universe(val, out)
    return out


def _add_cosmology_interval_value(
    bucket: dict[str, set[float]],
    kind: str | None,
    value: Any,
) -> None:
    """Add one typed interval field without harvesting unrelated statistics."""

    if isinstance(value, dict) and (
        _is_tainted_synthetic_payload(value)
        or _is_context_only_statistical_payload(value)
    ):
        return
    if kind in {"lower", "upper"}:
        bucket[kind].update(_row_central_values(value))
        return
    if kind != "pair":
        return
    if isinstance(value, (list, tuple)):
        finite = [
            float(item)
            for item in value
            if isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
        ]
        if len(finite) >= 2:
            bucket["lower"].add(finite[0])
            bucket["upper"].add(finite[-1])
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            nested_kind = _cosmology_interval_statistic_kind(key)
            if nested_kind in {"lower", "upper"}:
                _add_cosmology_interval_value(bucket, nested_kind, nested)


def _build_cosmology_interval_universe(
    payload: Any,
    out: dict[str, dict[str, set[float]]] | None = None,
) -> dict[str, dict[str, set[float]]]:
    """Map each cosmology parameter to lower/upper interval endpoints."""

    if out is None:
        out = {}
    if isinstance(payload, dict):
        if (
            _is_tainted_synthetic_payload(payload)
            or _is_context_only_statistical_payload(payload)
        ):
            return out
        named = payload.get("parameter") or payload.get("param")
        context_param = (
            _canonicalize_cosmology_param(named)
            if isinstance(named, str)
            else None
        )
        for key, value in payload.items():
            key_lower = str(key).lower()
            if (
                key_lower in _METADATA_KEYS_BLACKLIST
                or key_lower in _CITATION_KEYS_BLACKLIST
                or key_lower in _NON_EVIDENCE_KEYS
            ):
                continue
            if (
                key_lower in _COSMOLOGY_MANIFEST_KEYS
                and _manifest_subtree_is_skippable(value)
            ):
                continue
            parsed = _cosmology_param_and_statistic(key)
            if parsed is not None:
                parameter, statistic = parsed
                bucket = out.setdefault(
                    parameter, {"lower": set(), "upper": set()}
                )
                if statistic:
                    _add_cosmology_interval_value(
                        bucket,
                        _cosmology_interval_statistic_kind(statistic),
                        value,
                    )
                elif isinstance(value, dict):
                    for summary_key, summary_value in value.items():
                        _add_cosmology_interval_value(
                            bucket,
                            _cosmology_interval_statistic_kind(summary_key),
                            summary_value,
                        )
                # Do not recurse inside a parameter summary. Its central,
                # error, and interval fields have now been explicitly typed.
                continue
            if context_param is not None and key not in {"parameter", "param"}:
                kind = _cosmology_interval_statistic_kind(key)
                if kind is not None:
                    bucket = out.setdefault(
                        context_param, {"lower": set(), "upper": set()}
                    )
                    _add_cosmology_interval_value(bucket, kind, value)
                    continue
            _build_cosmology_interval_universe(value, out)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            _build_cosmology_interval_universe(value, out)
    return out


_COSMO_UNCERTAINTY_KEYS: frozenset[str] = frozenset({
    "std",
    "sigma",
    "error",
    "err",
    "uncertainty",
    "stderr",
    "standarderror",
    "standarddeviation",
})


def _build_cosmology_uncertainty_universe(
    payload: Any,
    out: dict[str, set[float]] | None = None,
) -> dict[str, set[float]]:
    """Map parameters to symmetric uncertainty/error statistics only."""

    if out is None:
        out = {}
    if isinstance(payload, dict):
        if (
            _is_tainted_synthetic_payload(payload)
            or _is_context_only_statistical_payload(payload)
        ):
            return out
        named = payload.get("parameter") or payload.get("param")
        context_param = (
            _canonicalize_cosmology_param(named)
            if isinstance(named, str)
            else None
        )
        for key, value in payload.items():
            key_lower = str(key).lower()
            if (
                key_lower in _METADATA_KEYS_BLACKLIST
                or key_lower in _CITATION_KEYS_BLACKLIST
                or key_lower in _NON_EVIDENCE_KEYS
            ):
                continue
            if (
                key_lower in _COSMOLOGY_MANIFEST_KEYS
                and _manifest_subtree_is_skippable(value)
            ):
                continue
            parsed = _cosmology_param_and_statistic(key)
            if parsed is not None:
                parameter, statistic = parsed
                if statistic in _COSMO_UNCERTAINTY_KEYS:
                    out.setdefault(parameter, set()).update(
                        _row_central_values(value)
                    )
                elif statistic == "" and isinstance(value, dict):
                    for summary_key, summary_value in value.items():
                        summary_norm = re.sub(
                            r"[^a-z0-9]", "", str(summary_key).lower()
                        )
                        if summary_norm in _COSMO_UNCERTAINTY_KEYS:
                            out.setdefault(parameter, set()).update(
                                _row_central_values(summary_value)
                            )
                continue
            if context_param is not None and key not in {"parameter", "param"}:
                key_norm = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if key_norm in _COSMO_UNCERTAINTY_KEYS:
                    out.setdefault(context_param, set()).update(
                        _row_central_values(value)
                    )
                    continue
            _build_cosmology_uncertainty_universe(value, out)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            _build_cosmology_uncertainty_universe(value, out)
    return out


_COSMO_LOWER_BOUND_HINT_RE = re.compile(
    r"\b(?:as\s+low\s+as|lower\s+(?:(?:hdi|(?:1|one)[-\s]?sigma)\s+)?"
    r"(?:edge|bound|limit)|"
    r"(?:q|quantile)\s*16|16(?:th)?\s+percentile|hdi\s+lower)\b",
    re.IGNORECASE,
)
_COSMO_UPPER_BOUND_HINT_RE = re.compile(
    r"\b(?:as\s+high\s+as|upper\s+(?:(?:hdi|(?:1|one)[-\s]?sigma)\s+)?"
    r"(?:edge|bound|limit)|"
    r"(?:q|quantile)\s*84|84(?:th)?\s+percentile|hdi\s+upper)\b",
    re.IGNORECASE,
)
_COSMO_INTERVAL_HINT_RE = re.compile(
    r"\b(?:edge|bound|limit|hdi|quantile|percentile|credible\s+interval|"
    r"confidence\s+interval|(?:1|one)[-\s]?sigma)\b|1\s*σ",
    re.IGNORECASE,
)
_COSMO_CENTRAL_CUE_RE = re.compile(
    r"\b(?:central(?:\s+(?:result|estimate|value|constraint))?|median|mean|"
    r"best[-\s]?fit|map|mle)\b",
    re.IGNORECASE,
)
_CLAUSE_CONJUNCTION_RE = re.compile(r"\b(?:while|whereas|but)\b", re.IGNORECASE)
_COSMO_PARAMETER_MENTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("H0", re.compile(r"(?<![A-Za-z0-9_])(?:H_?0|H₀)(?![A-Za-z0-9_])", re.I)),
    ("omegam", re.compile(r"\b(?:omegam|omega[_\s-]?m|om0)\b|Ω_?m|Ωₘ", re.I)),
    ("sigma8", re.compile(r"\b(?:sigma_?8)\b|σ_?8", re.I)),
    ("S8", re.compile(r"(?<![A-Za-z0-9_])S_?8(?![A-Za-z0-9_])", re.I)),
    ("w0", re.compile(r"(?<![A-Za-z0-9_])(?:w_?0|w₀)(?![A-Za-z0-9_])", re.I)),
    ("wa", re.compile(r"(?<![A-Za-z0-9_])(?:w_?a|wₐ)(?![A-Za-z0-9_])", re.I)),
    ("ombh2", re.compile(r"\b(?:ombh2|omega[_\s-]?b\s*h\^?2)\b|ω_?b", re.I)),
    ("ns", re.compile(r"(?<![A-Za-z0-9_])(?:n_?s|nₛ)(?![A-Za-z0-9_])", re.I)),
    ("tau", re.compile(r"(?<![A-Za-z0-9_])(?:tau|τ)(?![A-Za-z0-9_])", re.I)),
)


def _claim_clause(reply: str, claim: Claim) -> tuple[str, int, int]:
    """Return the discourse clause containing a claim, preserving decimals."""

    boundaries: list[tuple[int, int]] = [
        match.span() for match in _SENTENCE_BREAK_RE.finditer(reply)
    ]
    boundaries.extend(
        (index, index + 1)
        for index, character in enumerate(reply)
        if character in ";；。！？"
    )
    clause_start = max(
        (end for start, end in boundaries if end <= claim.start),
        default=0,
    )
    clause_end = min(
        (start for start, end in boundaries if start >= claim.end),
        default=len(reply),
    )
    sentence = reply[clause_start:clause_end]
    claim_start = claim.start - clause_start
    claim_end = claim.end - clause_start

    conjunctions = list(_CLAUSE_CONJUNCTION_RE.finditer(sentence))
    left_conjunction = max(
        (match.end() for match in conjunctions if match.end() <= claim_start),
        default=0,
    )
    right_conjunction = min(
        (match.start() for match in conjunctions if match.start() >= claim_end),
        default=len(sentence),
    )
    clause_start += left_conjunction
    clause_end = clause_start + (right_conjunction - left_conjunction)
    sentence = reply[clause_start:clause_end]
    claim_start = claim.start - clause_start
    claim_end = claim.end - clause_start
    return sentence, claim_start, claim_end


def _cosmology_interval_claim_direction(
    reply: str,
    claim: Claim,
) -> str | None:
    """Classify an explicitly stated lower/upper/unspecified interval edge."""

    sentence, claim_start, claim_end = _claim_clause(reply, claim)
    parameter = _claim_cosmology_param(claim)
    mentions: list[tuple[str, float]] = []
    for name, pattern in _COSMO_PARAMETER_MENTION_PATTERNS:
        mentions.extend(
            (name, (match.start() + match.end()) / 2.0)
            for match in pattern.finditer(sentence)
        )

    def distance_to_claim(start: int, end: int) -> int:
        if end <= claim_start:
            return claim_start - end
        if start >= claim_end:
            return start - claim_end
        return 0

    def nearest(pattern: re.Pattern[str], *, bind_parameter: bool) -> int | None:
        distances: list[int] = []
        for match in pattern.finditer(sentence):
            if bind_parameter and parameter is not None and mentions:
                hint_center = (match.start() + match.end()) / 2.0
                current_distance = min(
                    (
                        abs(position - hint_center)
                        for name, position in mentions
                        if name == parameter
                    ),
                    default=abs(((claim_start + claim_end) / 2.0) - hint_center),
                )
                other_distance = min(
                    (
                        abs(position - hint_center)
                        for name, position in mentions
                        if name != parameter
                    ),
                    default=math.inf,
                )
                if other_distance < current_distance:
                    continue
            distances.append(distance_to_claim(match.start(), match.end()))
        return min(distances) if distances else None

    lower_distance = nearest(_COSMO_LOWER_BOUND_HINT_RE, bind_parameter=True)
    upper_distance = nearest(_COSMO_UPPER_BOUND_HINT_RE, bind_parameter=True)
    interval_distance = nearest(_COSMO_INTERVAL_HINT_RE, bind_parameter=True)
    central_distance = nearest(_COSMO_CENTRAL_CUE_RE, bind_parameter=False)

    # A parameter value immediately followed by ``± error`` is a central
    # estimate with an uncertainty, even when the surrounding prose calls it
    # a "one-sigma constraint" or "confidence interval".  Those generic
    # interval phrases describe the error bar; they must not reinterpret the
    # central value as an interval endpoint.  An explicit lower/upper
    # edge/bound/limit remains authoritative.
    if (
        _ADJACENT_UNCERTAINTY_RE.match(reply[claim.end : claim.end + 96])
        and lower_distance is None
        and upper_distance is None
    ):
        return None

    closest_interval = min(
        (
            distance
            for distance in (lower_distance, upper_distance, interval_distance)
            if distance is not None
        ),
        default=None,
    )
    if central_distance is not None and (
        closest_interval is None or central_distance <= closest_interval
    ):
        return None
    if lower_distance is not None and (
        upper_distance is None or lower_distance < upper_distance
    ):
        return "lower"
    if upper_distance is not None and (
        lower_distance is None or upper_distance < lower_distance
    ):
        return "upper"
    if interval_distance is not None:
        return "either"
    return None


_PUBLICATION_TYPED_RESULT_KEYS: dict[str, frozenset[str]] = {
    "significance_sigma": frozenset(
        {
            "equivalentsigma",
            "gaussianequivalentsigma",
            "significancesigma",
            "sigmaequivalent",
            "detectionsignificancesigma",
        }
    ),
    "p_value": frozenset(
        {
            "pvalue",
            "pvalues",
            "adjustedpvalue",
            "correctedpvalue",
            "pearsonp",
            "spearmanp",
            "kendallp",
        }
    ),
    "correlation_r": frozenset(
        {"correlationr", "pearsonr", "spearmanr", "kendalltau", "rvalue"}
    ),
    "parallax_mas": frozenset(
        {"parallax", "parallaxmas", "meanparallaxmas", "medianparallaxmas"}
    ),
    "distance_pc": frozenset({"distancepc", "distanceparsec"}),
    "distance_kpc": frozenset({"distancekpc", "distancekiloparsec"}),
    "distance_mpc": frozenset({"distancempc", "distancemegaparsec"}),
    "mass_solar": frozenset(
        {"masssolar", "massmsun", "stellarmasssolar", "stellarmassmsun"}
    ),
    "age_myr": frozenset(
        {"agemyr", "agemegayear", "clusteragemyr", "clustagemyr"}
    ),
    "age_gyr": frozenset({"agegyr", "agegigayear"}),
    "period_days": frozenset({"perioddays", "periodday"}),
    "redshift": frozenset({"redshift", "redshiftz", "zspec", "zphot"}),
    "radius_ratio": frozenset({"radiusratio", "rprs", "planettostarradiusratio"}),
    "transit_depth": frozenset({"transitdepth", "depthfraction"}),
    "line_fwhm": frozenset({"fwhm", "fwhmkms", "linewidth", "linewidthkms"}),
}
_PUBLICATION_TYPED_KEY_TO_LABELS: dict[str, set[str]] = {}
for _typed_label, _typed_keys in _PUBLICATION_TYPED_RESULT_KEYS.items():
    for _typed_key in _typed_keys:
        _PUBLICATION_TYPED_KEY_TO_LABELS.setdefault(_typed_key, set()).add(
            _typed_label
        )


def _build_publication_typed_universe(
    payload: Any, out: dict[str, set[float]] | None = None
) -> dict[str, set[float]]:
    if out is None:
        out = {}
    if isinstance(payload, dict):
        if (
            _is_tainted_synthetic_payload(payload)
            or _is_context_only_statistical_payload(payload)
        ):
            return out
        for key, value in payload.items():
            key_lower = str(key).lower()
            if (
                key_lower in _METADATA_KEYS_BLACKLIST
                or key_lower in _CITATION_KEYS_BLACKLIST
                or key_lower in _NON_EVIDENCE_KEYS
            ):
                continue
            if (
                key_lower in _COSMOLOGY_MANIFEST_KEYS
                and _manifest_subtree_is_skippable(value)
            ):
                continue
            key_norm = re.sub(r"[^a-z0-9]", "", key_lower)
            labels = _PUBLICATION_TYPED_KEY_TO_LABELS.get(key_norm, set())
            for label in labels:
                out.setdefault(label, set()).update(_iter_numeric_values(value))
            _build_publication_typed_universe(value, out)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            _build_publication_typed_universe(value, out)
    return out


_VERIFIED_SCALAR_DERIVED_NUMERIC_KEYS = frozenset(
    {
        "value",
        "standard_uncertainty",
        "standardized_difference_abs",
        "independent_standard_uncertainty",
        "relative_uncertainty_change_vs_independent",
        "relative_uncertainty_change_percent_vs_independent",
    }
)


def _verified_scalar_derived_numbers(tool_results: Any) -> set[float]:
    """Return only controlled derived fields from successful scalar receipts.

    The global anti-echo pass removes every model-authored input number.  A
    legitimate propagated uncertainty can numerically equal an input
    uncertainty (for example a difference against a fixed zero-error
    comparator).  Re-admit only named outputs produced by the deterministic
    backend, never echoed quantities, matrices, source text, or arbitrary tool
    dictionaries.
    """
    numbers: set[float] = set()
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    for entry in entries or []:
        tool_name, result = _entry_tool_and_result(entry)
        if tool_name != "verify_scalar_derivation" or not isinstance(result, dict):
            continue
        if not _payload_is_claimable_success(tool_name, result):
            continue
        if str(result.get("calculation_status") or "").lower() not in {
            "verified",
            "verified_deterministic",
            "linearized_approximation",
        }:
            continue
        scopes = result.get("claim_scopes")
        if not isinstance(scopes, dict) or scopes.get("derived_numeric") is not True:
            continue
        derived = result.get("result")
        if not isinstance(derived, dict):
            continue
        for key in _VERIFIED_SCALAR_DERIVED_NUMERIC_KEYS:
            value = derived.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric = float(value)
                if math.isfinite(numeric):
                    numbers.add(numeric)
    return numbers


def _publication_typed_claim_label(claim: Claim) -> str | None:
    base = claim.label.split(".g", 1)[0]
    if base in _PUBLICATION_TYPED_RESULT_KEYS:
        return base
    if base in {"redshift_z", "redshift_word"}:
        return "redshift"
    if base == "label_colon":
        raw = claim.raw.lower()
        for token, label in (
            ("parallax", "parallax_mas"),
            ("p-value", "p_value"),
            ("p value", "p_value"),
            ("pearson", "correlation_r"),
            ("spearman", "correlation_r"),
            ("period", "period_days"),
            ("age", "age_myr" if "myr" in raw else "age_gyr"),
            ("distance", "distance_mpc" if "mpc" in raw else "distance_kpc" if "kpc" in raw else "distance_pc"),
        ):
            if token in raw:
                return label
    return None


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
    require_typed_scientific_match: bool = False,
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

    ``require_typed_scientific_match`` is the fail-closed manuscript mode.  A
    recognisable cosmology parameter must then match that same parameter's
    labelled result bucket; it may not fall back to an unrelated number in the
    flat universe.  Gaussian-equivalent significance claims likewise match
    only explicitly labelled significance fields, never S/N or another sigma.

    NOTE (2026-06-12): an earlier round tried admitting USER-prompt numbers so
    honest restatements ("at z=1.5 ...") would validate. Adversarial review
    showed that channel reopened bibcode/year/identifier laundering via prompt
    text, let a user-typed number override a real tool-produced parameter, and
    relaxed strict mode by padding the prompt — so it was reverted. Restating
    an input value that no tool echoed into a result is a known specificity
    cost (backlog P3b), NOT worth weakening the gate.
    """
    claims = extract_claims(reply)
    # 2026-06-12: harvest from tool RESULTS only — model-authored inputs must
    # never ground a claim (see _result_only_nodes).
    claimable_nodes = _result_only_nodes(tool_results) if tool_results is not None else tool_results
    universe: set[float] = set(_iter_numeric_values(claimable_nodes))
    # STRUCTURAL anti-echo (2026-06-12): subtract every number the model
    # authored in this turn's tool INPUTS. Many tools copy their arguments into
    # their result (run_adql->query, audit_published_constraint->claimed,
    # sensitivity_analysis->base_value, …), so a fabricated value echoed under
    # ANY result key is removed here regardless of the key — closing the class
    # the per-key _NON_EVIDENCE_KEYS list can only chase one name at a time.
    input_numbers = _model_input_numbers(tool_results) if tool_results is not None else set()
    universe -= input_numbers
    universe |= _verified_scalar_derived_numbers(tool_results)
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
    labeled = _build_cosmology_labeled_universe(claimable_nodes) if tool_results else {}
    if input_numbers:  # same structural anti-echo for the per-parameter buckets
        labeled = {param: (vals - input_numbers) for param, vals in labeled.items()}
    interval_labeled = (
        _build_cosmology_interval_universe(claimable_nodes)
        if tool_results
        else {}
    )
    if input_numbers:
        interval_labeled = {
            param: {
                direction: values - input_numbers
                for direction, values in buckets.items()
            }
            for param, buckets in interval_labeled.items()
        }
    uncertainty_labeled = (
        _build_cosmology_uncertainty_universe(claimable_nodes)
        if tool_results
        else {}
    )
    if input_numbers:
        uncertainty_labeled = {
            param: values - input_numbers
            for param, values in uncertainty_labeled.items()
        }
    typed_universe: dict[str, set[float]] = {}
    if require_typed_scientific_match:
        typed_universe = _build_publication_typed_universe(claimable_nodes)
        if input_numbers:
            typed_universe = {
                label: values - input_numbers
                for label, values in typed_universe.items()
            }
        # A scalar verifier receipt provides central values, standard
        # uncertainties, and covariance, but no distributional model.
        # Its standardized_difference_abs remains claimable through the
        # controlled derived-number pool; it must not mint a Gaussian-equivalent
        # significance_sigma claim without publication-provided significance.

    uncited: list[Claim] = []
    for c in claims:
        # B3: provenance is contextual, not merely numeric.  A number quoted
        # from an earlier/previous/pasted/user-supplied transcript cannot be
        # grounded by a coincidentally equal float in this turn's global pool.
        # It must be omitted and, if needed, obtained from a fresh tool result.
        if c.label.startswith(f"{_UNTRUSTED_CONTEXT_LABEL}."):
            uncited.append(c)
            continue
        uncertainty_param = _claim_cosmology_uncertainty_param(c)
        param = _claim_cosmology_param(c)
        if uncertainty_param is not None:
            uncertainty_values = uncertainty_labeled.get(
                uncertainty_param, set()
            )
            if not uncertainty_values or not _matches_any(
                c.value, uncertainty_values, effective_tol
            ):
                uncited.append(c)
        elif param is not None:
            interval_direction = _cosmology_interval_claim_direction(reply, c)
            if interval_direction is None:
                param_values = labeled.get(param, set())
            else:
                buckets = interval_labeled.get(param, {})
                if interval_direction == "either":
                    param_values = set(buckets.get("lower", set())) | set(
                        buckets.get("upper", set())
                    )
                else:
                    param_values = set(buckets.get(interval_direction, set()))
            # Parameter claims must match both parameter and statistic type.
            # A centre cannot borrow an HDI edge; a lower/upper edge cannot
            # borrow the median or opposite edge. Neither route falls back to
            # the flat numeric universe.
            if not param_values:
                uncited.append(c)
            elif param_values and not _matches_any(c.value, param_values, effective_tol):
                uncited.append(c)
        elif require_typed_scientific_match and (
            typed_label := _publication_typed_claim_label(c)
        ) is not None:
            if not _matches_any(
                c.value, typed_universe.get(typed_label, set()), effective_tol
            ):
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
        # Lightweight receipts expose normalized source records rather than
        # the older literature-tool keys above.  Only an independently
        # matched record may seed citation support: model/user identifiers,
        # resolved-but-unmatched pages, and conflicts remain diagnostic only.
        if (
            str(node.get("kind") or "").lower() == "arxiv"
            and str(node.get("status") or "").lower() == "verified_exact"
        ):
            for key in ("identifier", "final_url"):
                value = node.get(key)
                if value:
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
        if (
            str(node.get("kind") or "").lower() == "doi"
            and str(node.get("status") or "").lower() == "verified_exact"
        ):
            for key in ("identifier", "final_url"):
                value = node.get(key)
                if value:
                    pool.update(_dois_from_text(str(value)))
    return pool


def _build_author_year_support(tool_results: Any) -> set[tuple[str, str]]:
    # B4 (2026-07-03): iterate _citation_pool_nodes, not _iter_dict_nodes, so
    # only non-tainted RESULT subtrees seed author-year support — never the
    # model-authored tool `input` payload and never FAILED/withheld results.
    # Otherwise "Riess et al. (2099)" becomes valid provenance merely by being
    # echoed into a tool argument (e.g. audit_published_constraint's paper_ref)
    # even when that call fails — the same laundering path already closed for
    # the bibcode/arXiv/DOI pools.
    support: set[tuple[str, str]] = set()
    for node in _citation_pool_nodes(tool_results):
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

    citation_text, citation_map = _strip_markdown_code_with_map(reply)
    for match in AUTHOR_YEAR_RE.finditer(citation_text):
        author, year = match.group(1), match.group(2)
        match_text = match.group(0).strip()
        line_text = _line_text(citation_text, match.start())
        original_offset = citation_map[match.start()]
        original_line_text = _line_text(reply, original_offset)
        if _line_is_nonclaim_context(line_text):
            continue
        # Inline-code stripping prevents code-like identifiers from being
        # mistaken for prose, but a validated DOI/arXiv ID/bibcode on the same
        # original line is still an exact source for that citation.
        if _line_has_valid_explicit_citation(
            original_line_text, valid_bibcodes, valid_arxiv_ids, valid_dois
        ):
            continue
        if _phrase_in_claimable_payload(match_text, claimable_payload_text):
            continue
        if not strict and _author_year_looks_like_noise(author, match_text):
            continue
        if _author_year_is_suspicious(author, year, valid_bibcodes, author_year_support):
            violations.append(CitationViolation(
                kind="suspicious_author_year",
                match_text=match_text,
                line_number=_line_number(reply, original_offset),
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

    stripped_reply, stripped_map = _strip_markdown_code_with_map(reply)
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
                line_number=_line_number(reply, stripped_map[match.start()]),
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
    # 2026-07-03: the arXiv fallback of search_literature identifies papers by
    # "arXiv:<id>" in the bibcode field instead of an ADS bibcode. Those never
    # match BIBCODE_RE, so citing them by arXiv id used to skip this barrier
    # entirely while the bibcode form was hard-blocked. Track the arXiv-shaped
    # identifiers through the same search-pool / classified pipeline.
    search_pool_arxiv: set[str] = set()
    classified_relevance: dict[str, str] = {}
    classified_arxiv: dict[str, str] = {}

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
                    raw_id = str(p.get("bibcode") or "")
                    bc = _normalize_bibcode(raw_id)
                    if bc:
                        search_pool.add(bc)
                    search_pool_arxiv.update(_arxiv_ids_from_text(raw_id))
            elif tool_name == "classify_literature_relevance":
                classes = payload.get("classifications") if isinstance(payload, dict) else None
                if not isinstance(classes, list):
                    continue
                for c in classes:
                    if not isinstance(c, dict):
                        continue
                    raw_id = str(c.get("bibcode") or "")
                    bc = _normalize_bibcode(raw_id)
                    rel = str(c.get("relevance") or "").strip()
                    if not rel:
                        continue
                    if bc:
                        classified_relevance[bc] = rel
                    for classified_id in _arxiv_ids_from_text(raw_id):
                        classified_arxiv[classified_id] = rel

    if not search_pool and not search_pool_arxiv:
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

    for match in ARXIV_ID_RE.finditer(reply):
        arxiv_id = _normalize_arxiv_id(match.group(1))
        if not arxiv_id or arxiv_id in seen:
            continue
        seen.add(arxiv_id)
        if arxiv_id in search_pool_arxiv and arxiv_id not in classified_arxiv:
            violations.append(CitationViolation(
                kind="unclassified_literature",
                match_text=f"arXiv:{arxiv_id}",
                line_number=_line_number(reply, match.start()),
            ))
        elif classified_arxiv.get(arxiv_id) == "Off-topic":
            violations.append(CitationViolation(
                kind="cited_off_topic_paper",
                match_text=f"arXiv:{arxiv_id}",
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
_EXTERNAL_RUN_WORDING_RE = re.compile(
    r"\b(?:external|cobaya|cosmosis|desilike)\b",
    re.IGNORECASE,
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
    r"\bL'?\s*(?:\[\s*)?CII\s*\]?"               # L'[CII], L'CII, L [CII]
    r"|\bLFR\b"
    r"|luminosity[\s-]+FWHM"
    r"|L[- ]FWHM\s+relation"
    r"|line[\s-]+luminosity[\s-]+(?:FWHM|width)"
    r"|line[\s-]+(?:width|FWHM)[\s-]+(?:relation|fit)"
    r"|\[CII\][^.\n]{0,80}(?:fit|relation|regression)"
    r"|(?:fit|regression)[^.\n]{0,80}\[CII\]"
    r"|\bL'\s*(?:-\s*)?FWHM"
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
    # Match against the code-stripped text, but translate every match offset
    # back to the original reply via the boundary map before slicing context
    # or computing line numbers — see _strip_markdown_code_with_map.
    stripped, stripped_map = _strip_markdown_code_with_map(reply)
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
                line_number=_line_number(reply, stripped_map[bayesian_match.start()]),
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
                line_number=_line_number(reply, stripped_map[ready_match.start()]),
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
                line_number=_line_number(reply, stripped_map[stat_match.start()]),
            ))

    # ── Cosmology compressed-vs-full likelihood scope ────────────────
    # A compressed Gaussian chain can support preliminary posterior/tension
    # numbers, but it does not make the selected probes "ready for full
    # likelihood analyses."  The evidence required depends on the WORDING of
    # the matched claim (2026-06-12): phrases asserting an external
    # Cobaya/CosmoSIS/desilike run need an actual external run; plain
    # "full likelihood ... ready" wording is also satisfied by an in-process
    # chain that executed only released full-fidelity products (e.g. the
    # union3 22-bin vector) — blocking that was a false positive of the same
    # class as the anchor-gate bug (9f2667e).
    for full_match in _FULL_EXTERNAL_LIKELIHOOD_READY_RE.finditer(stripped):
        # Context must come from the SAME text the regex matched (`stripped`):
        # slicing the original reply with a stripped offset lands earlier —
        # typically inside a preceding code block, whose tokens (not/requires/
        # config/...) then satisfy the non-claim qualifier and disarm the gate,
        # while honest "NOT ready" qualifiers become invisible and get blocked.
        sentence = _sentence_text(stripped, full_match.start())
        line = _line_text(stripped, full_match.start())
        context = f"{line} {sentence}"
        if _FULL_EXTERNAL_LIKELIHOOD_NONCLAIM_RE.search(context):
            continue
        if _full_external_likelihood_ready_available(tool_results):
            continue
        if not _EXTERNAL_RUN_WORDING_RE.search(
            context
        ) and _in_process_full_fidelity_chain_available(tool_results):
            continue
        violations.append(CitationViolation(
            kind="full_likelihood_overclaim",
            match_text=full_match.group(0),
            line_number=_line_number(reply, stripped_map[full_match.start()]),
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
                line_number=_line_number(reply, stripped_map[bypass_stat_match.start()]),
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
                line_number=_line_number(reply, stripped_map[m.start()]),
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


_CONCLUSION_ATTESTATION_SCHEMA_VERSION = 1
_CONCLUSION_ATTESTATION_ARTIFACT_TYPE = "scientific_conclusion_attestation"
_CONCLUSION_ATTESTATION_TOOLS: frozenset[str] = frozenset(
    {
        "get_cosmology_run_status",
        "run_cobaya_cosmology",
        "run_cosmology_likelihood_chain",
        "run_cosmology_robustness_matrix",
    }
)
_SHA256_VALUE_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.I)
_EVIDENCE_REF_RE = re.compile(
    r"\[evidence:(?P<id>[A-Za-z0-9][A-Za-z0-9_.:-]{2,127})\]",
    re.I,
)
_LCDM_RE = re.compile(
    r"\b(?:cosmological\s+constant|lambda\s*cdm|lcdm)\b|Λ\s*CDM",
    re.I,
)
_W0WA_RE = re.compile(
    r"\b(?:w\s*0\s*w\s*a\s*cdm|w0wa\s*cdm|cpl)\b|"
    r"w\s*[_\{]?0\}?\s*[-+/]\s*w\s*[_\{]?a\}?",
    re.I,
)
_WCDM_RE = re.compile(r"(?<![0a-z])w\s*cdm\b", re.I)
_BASELINE_REJECTION_RE = re.compile(
    r"(?:\b(?:rule[sd]?\s+out|exclude[sd]?|reject(?:ed|s)?|"
    r"disfavou?r(?:ed|s)?|inconsistent|incompatible|conflict(?:s|ed)?)\b"
    r"[^.\n;]{0,140}(?:cosmological\s+constant|(?:lambda|Λ)\s*CDM|LCDM)\b|"
    r"(?:cosmological\s+constant|(?:lambda|Λ)\s*CDM|LCDM)\b"
    r"[^.\n;]{0,140}\b(?:ruled\s+out|excluded|rejected|disfavou?red|"
    r"inconsistent|incompatible|fails?\s+to\s+fit)\b)",
    re.I,
)
_MODEL_PREFERENCE_RE = re.compile(
    r"\b(?:favou?r(?:s|ed)?|prefer(?:s|red)?|preference|evidence|support)\b"
    r"[^.\n;]{0,180}\b(?:w\s*0\s*w\s*a\s*cdm|w0wa\s*cdm|"
    r"w\s*cdm|cpl|evolving|dynamical|time[-\s]?varying)\b|"
    r"\b(?:w\s*0\s*w\s*a\s*cdm|w0wa\s*cdm|w\s*cdm|cpl)\b"
    r"[^.\n;]{0,180}\b(?:favou?red|preferred|supported)\b",
    re.I,
)
_ScientificToken = tuple[str, int, int]


def _scientific_word_tokens(text: str) -> list[_ScientificToken]:
    """Tokenize conclusion prose in one pass without backtracking regexes."""

    tokens: list[_ScientificToken] = []
    current: list[str] = []
    current_start: int | None = None
    current_end = 0
    subscript_map = {"₀": "0", "ₐ": "a"}

    def flush() -> None:
        nonlocal current_end, current_start
        if current:
            tokens.append(("".join(current), current_start or 0, current_end))
            current.clear()
            current_start = None
            current_end = 0

    for index, raw_character in enumerate(text):
        character = subscript_map.get(raw_character, raw_character.casefold())
        if character.isalnum():
            if current_start is None:
                current_start = index
            current.append(character)
            current_end = index + 1
        else:
            flush()
            if character == "≠":
                tokens.append((character, index, index + 1))
    flush()
    return tokens


def _spans_within(
    left: list[tuple[int, int]],
    right: list[tuple[int, int]],
    *,
    max_gap: int,
) -> bool:
    """Linear two-pointer proximity check for sorted character spans."""

    left_index = 0
    right_index = 0
    while left_index < len(left) and right_index < len(right):
        left_start, left_end = left[left_index]
        right_start, right_end = right[right_index]
        if left_end < right_start:
            gap = right_start - left_end
        elif right_end < left_start:
            gap = left_start - right_end
        else:
            gap = 0
        if gap <= max_gap:
            return True
        if left_end < right_start:
            left_index += 1
        else:
            right_index += 1
    return False


def _dark_energy_evolution_claim(sentence: str) -> bool:
    """Recognize strong dark-energy-evolution claims in linear time."""

    token_records = _scientific_word_tokens(sentence)
    tokens = [token for token, _, _ in token_records]
    evolution_words = {
        "evolve",
        "evolves",
        "evolved",
        "evolving",
        "dynamical",
        "vary",
        "varies",
        "varied",
        "variation",
        "timevarying",
        "timedependent",
    }
    subject_spans: list[tuple[int, int]] = []
    evolution_spans: list[tuple[int, int]] = []
    for index, token in enumerate(tokens):
        if tokens[index : index + 2] == ["dark", "energy"]:
            subject_spans.append(
                (token_records[index][1], token_records[index + 1][2])
            )
        elif tokens[index : index + 3] == ["equation", "of", "state"]:
            subject_spans.append(
                (token_records[index][1], token_records[index + 2][2])
            )
        if token in evolution_words:
            evolution_spans.append(
                (token_records[index][1], token_records[index][2])
            )
        elif tokens[index : index + 2] in (
            ["time", "varying"],
            ["time", "dependent"],
            ["time", "variation"],
        ):
            evolution_spans.append(
                (token_records[index][1], token_records[index + 1][2])
            )
    if _spans_within(subject_spans, evolution_spans, max_gap=120):
        return True

    wa_spans: list[tuple[int, int]] = []
    for index, token in enumerate(tokens):
        if token == "wa":
            wa_spans.append((index, 1))
        elif token == "w" and index + 1 < len(tokens) and tokens[index + 1] == "a":
            wa_spans.append((index, 2))

    for start, width in wa_spans:
        before = tokens[max(0, start - 2) : start]
        after = tokens[start + width : start + width + 4]
        if before[-2:] == ["non", "zero"] or before[-1:] == ["nonzero"]:
            return True
        if len(after) >= 3 and after[:3] in (
            ["differ", "from", "zero"],
            ["differs", "from", "zero"],
            ["deviate", "from", "zero"],
            ["deviates", "from", "zero"],
            ["is", "non", "zero"],
        ):
            return True
        if after[:2] == ["is", "nonzero"]:
            return True
        if len(after) >= 2 and after[:2] in (
            ["ne", "0"],
            ["neq", "0"],
            ["≠", "0"],
        ):
            return True
        if after[:1] in (["ne0"], ["neq0"]):
            return True
    return False
_HUBBLE_TENSION_RESOLUTION_RE = re.compile(
    r"(?:\b(?:hubble|h\s*0)\s+tension\b[^.\n;]{0,140}\b"
    r"(?:resolv(?:e|es|ed)|alleviat(?:e|es|ed)|eliminat(?:e|es|ed)|"
    r"remov(?:e|es|ed)|settle[sd]?)\b|"
    r"\b(?:resolv(?:e|es|ed)|alleviat(?:e|es|ed)|eliminat(?:e|es|ed)|"
    r"remov(?:e|es|ed)|settle[sd]?)\b[^.\n;]{0,140}"
    r"\b(?:hubble|h\s*0)\s+tension\b)",
    re.I,
)
_SPATIAL_CURVATURE_CONCLUSION_RE = re.compile(
    r"(?:\b(?:spatial|cosmic|cosmological)\s+curvature\b|"
    r"\b(?:omega|Ω)\s*[_\{]?k\}?\b|\bcurved\s+(?:cosmology|universe)\b)"
    r"[^.\n;]{0,160}\b(?:favou?r(?:s|ed)?|prefer(?:s|red)?|supported?|"
    r"detect(?:s|ed)?|non[-\s]?zero|deviat(?:e|es|ed)\s+from\s+zero|"
    r"inconsistent\s+with\s+(?:a\s+)?flat|exclude[sd]?\s+(?:a\s+)?flat)\b|"
    r"\b(?:favou?r(?:s|ed)?|prefer(?:s|red)?|evidence\s+for|support(?:s|ed)?)\b"
    r"[^.\n;]{0,160}\b(?:spatial|cosmic|cosmological)\s+curvature\b",
    re.I,
)
_NEUTRINO_MASS_DETECTION_RE = re.compile(
    r"(?:\b(?:non[-\s]?zero|positive)\s+(?:sum\s+of\s+)?neutrino\s+mass(?:es)?\b"
    r"[^.\n;]{0,140}\b(?:detect(?:s|ed)?|measur(?:e|es|ed)|favou?r(?:s|ed)?|"
    r"prefer(?:s|red)?|supported?)\b|"
    r"\b(?:detect(?:s|ed)?|evidence\s+for|support(?:s|ed)?|favou?r(?:s|ed)?)\b"
    r"[^.\n;]{0,140}\b(?:non[-\s]?zero\s+)?(?:sum\s+of\s+)?"
    r"neutrino\s+mass(?:es)?\b|"
    r"\b(?:sum\s+of\s+)?neutrino\s+mass(?:es)?\b[^.\n;]{0,140}"
    r"\b(?:is|are)\s+(?:non[-\s]?zero|detected|measured)\b)",
    re.I,
)
_GENERAL_RELATIVITY_REJECTION_RE = re.compile(
    r"(?:\b(?:general\s+relativity|GR)\b[^.\n;]{0,140}\b"
    r"(?:rule[sd]?\s+out|exclude[sd]?|reject(?:ed|s)?|disfavou?r(?:ed|s)?|"
    r"falsif(?:y|ies|ied)|fails?\s+(?:the\s+)?test)\b|"
    r"\b(?:modified\s+gravity|deviation(?:s)?\s+from\s+(?:general\s+relativity|GR))\b"
    r"[^.\n;]{0,140}\b(?:favou?r(?:s|ed)?|prefer(?:s|red)?|supported?|"
    r"detect(?:s|ed)?)\b)",
    re.I,
)
_NONASSERTIVE_COSMOLOGY_CONTEXT_RE = re.compile(
    r"\b(?:no\s+(?:statistically\s+significant\s+)?evidence|"
    r"insufficient\s+evidence|cannot\s+conclude|can't\s+conclude|"
    r"do(?:es)?\s+not\s+(?:show|support|establish|favour|favor)|"
    r"do(?:es)?\s+not\s+(?:resolve|alleviate|eliminate|remove)|"
    r"failed?\s+to\s+(?:show|establish|detect|resolve|alleviate)|"
    r"test(?:ed|ing)?\s+whether|investigat(?:e|ed|ing)\s+whether|"
    r"ask(?:ed|ing)?\s+whether|"
    # A modal hedges the conclusion only when it modifies a conclusion-like
    # verb ("may evolve", "could be preferred"). A bare modal elsewhere in
    # the sentence ("... evolves, and this result should appear in the
    # abstract") must not wash an assertive conclusion.
    r"(?:may|might|could|would|should)(?:\s+not)?"
    # An interposed parenthetical is part of the same hedge: "may, after
    # recalibration, be resolved" is "may be resolved" (Codex review
    # 2026-09-03; a pre-existing gap, present on main too).  Comma-anchored
    # and bounded, so nothing here can be split ambiguously with the
    # whitespace runs around it.
    r"(?:\s*,[^,;:\n]{0,40},)?"
    r"(?:\s+(?:be|been|have|still|also|yet|then|instead|plausibly|possibly|"
    r"eventually|partially|fully))*"
    r"\s+(?:evolv\w*|resolv\w*|alleviat\w*|eliminat\w*|indicat\w*|"
    r"suggest\w*|impl(?:y|ies)|support\w*|favou?r\w*|prefer\w*|detect\w*|"
    r"show\w*|reject\w*|exclud\w*|rule\s+out|reconcil\w*|explain\w*|"
    r"remain\w*|persist\w*|disappear\w*|weaken\w*|strengthen\w*|point\w*|"
    r"hint\w*|deviat\w*|differ\w*|change\w*|shift\w*|vary\w*|help\w*)|"
    # A hypothesis / forecast word hedges only in label or predicate form
    # ("a hypothesis worth testing", "the model forecasts that ...").  The
    # bare noun ("Our hypothesis is confirmed: the Hubble tension is
    # resolved ...") must not wash an assertive conclusion (2026-09-02 review
    # H9).  "we hypothesise that" is deliberately NOT exempt.  A
    # sentence-initial "Hypothesis:" label is handled by
    # _HYPOTHESIS_LABEL_RE in _strong_conclusion_from_sentence.
    r"a\s+hypothesis\s+worth\s+testing|forecast\s+that|"
    # "Our hypothesis is that X" is ordinary hedged prose that main
    # exempted; the narrowing above dropped the predicate form by
    # accident (Codex review 2026-09-03).  Restoring it cannot reopen
    # the washing hole: "Our hypothesis is confirmed: X" carries a
    # confirmed assertion, which cancels the hedge and keeps it a
    # violation.  The verb form "we hypothesise that" stays NOT exempt.
    # Singular nouns only, and only the two main already exempted as bare
    # words: main's `\bhypothesis\b|\bforecast\b` never matched
    # "hypotheses", "forecasts", "prediction" or "conjecture", so admitting
    # them here would have been a relaxation the PR body denied (audit
    # 2026-09-03).  The predicate form is a strict subset of the bare word.
    r"(?:hypothesis|forecast)\s+(?:is|was)\s+that|"
    # "confirmed / shown / found not to <verb>" is deliberately NOT a hedge.
    # Round seven (PRRT_kwDORoeoE86eypXG) read it as a negative result, and
    # every relaxation found afterwards -- "confirmed not to resolve the S8
    # tension yet resolves the Hubble tension" and thirty more -- came from
    # that one alternative, so it was withdrawn (2026-09-03) and the phrase
    # is read exactly as origin/main reads it.  A user-signed relaxation may
    # reinstate it; do not add it back as a fix.
    r"not\s+ruled\s+out|consistent\s+with\s+zero|does\s+not\s+evolve|"
    r"(?:is|are|remains?)\s+unresolved|(?:is|are)\s+not\s+(?:resolved|detected))\b",
    re.I,
)
_ZH_NONASSERTIVE_COSMOLOGY_CONTEXT_RE = re.compile(
    r"(?:没有|并无|无)(?:统计显著|显著)?证据|证据不足|无法(?:得出|断定|证明)|"
    r"不能(?:得出|断定|证明)|尚未|未能|是否|可能|或许|"
    r"^\s*假设[:：]|值得(?:检验|验证)的假设|预测[^。；;.!！？\n]{0,20}将|"
    # The Chinese predicate form, counterpart of "our hypothesis is that X".
    # Narrowing the bare 假设 alternative left ordinary hypothetical prose
    # with no hedge at all (Codex review 2026-09-03).
    # 猜想 was never exempt on main; 假设/预测 were exempt as bare words, so
    # the predicate form is a strict subset of what main allowed.
    r"(?:假设|预测)是|"
    r"仍未解决|没有解决|未(?:探测|检测)到|与零一致|不随时间演化|未被排除"
)
# Sentence-initial "Hypothesis:" (optionally bold or as a list item) marks the
# whole sentence as a labelled hypothesis, not a conclusion.
# The bold marker is part of each literal alternative rather than an optional
# group between two \s* runs: the latter shape backtracks polynomially on
# "hypothesis" followed by a long run of spaces (CodeQL py/polynomial-redos).
# All three Markdown shapes a model actually writes are accepted, including a
# bold span that swallows the colon inside a list item
# ("- **Hypothesis:** ...", review 2026-09-03).
# The label is closed by a colon or by a dash -- ASCII (also "--"), en dash or
# em dash (also the Chinese "——"), with optional spaces around it.  Requiring
# the colon turned "**Hypothesis** — a local void ..." into a blocked
# conclusion (Codex review 2026-09-04, thread fJuvl).  An ASCII hyphen joined
# to the next word is a compound ("Hypothesis-driven analysis ..."), not a
# label.  Only this anchored label learns the dash: a dash in the middle of a
# sentence ("The hypothesis — X — is confirmed") is ordinary punctuation.
_HYPOTHESIS_LABEL_SEPARATOR = r"(?::|-+(?![A-Za-z0-9])|[\u2013\u2014]+)"
_ZH_HYPOTHESIS_LABEL_SEPARATOR = r"(?:[:：]|\u2014+)"
_HYPOTHESIS_LABEL_RE = re.compile(
    r"^[ \t]*(?:(?:[-*>#]+|\d+[.)])[ \t]*)?"
    r"(?:\*\*[ \t]*hypothesis[ \t]*" + _HYPOTHESIS_LABEL_SEPARATOR + r"[ \t]*\*\*"
    r"|\*\*[ \t]*hypothesis[ \t]*\*\*[ \t]*" + _HYPOTHESIS_LABEL_SEPARATOR
    + r"|hypothesis[ \t]*" + _HYPOTHESIS_LABEL_SEPARATOR
    # The Chinese label is written in the same Markdown forms; recognising
    # only the bare form blocked "- **假设：** ..." as a strong conclusion
    # (review 2026-09-03).
    + r"|\*\*[ \t]*假设[ \t]*" + _ZH_HYPOTHESIS_LABEL_SEPARATOR + r"[ \t]*\*\*"
    r"|\*\*[ \t]*假设[ \t]*\*\*[ \t]*" + _ZH_HYPOTHESIS_LABEL_SEPARATOR
    + r"|假设[ \t]*" + _ZH_HYPOTHESIS_LABEL_SEPARATOR + r")",
    re.I,
)
# A confirmation only cancels a hedge when the sentence is not itself denying
# the conclusion.  "Although the calibration is confirmed, there is no
# evidence the Hubble tension is resolved" confirms an unrelated premise
# (review 2026-09-03).
_EXPLICIT_DENIAL_RE = re.compile(
    r"\bno\s+(?:statistically\s+significant\s+)?evidence\b"
    r"|\binsufficient\s+evidence\b"
    r"|\bcan(?:not|'t)\s+conclude\b"
    # The resolve-family verbs were missing here while
    # _NONASSERTIVE_COSMOLOGY_CONTEXT_RE already treated them as a hedge, so
    # reading the denial in the conclusion's clause turned "the data do not
    # resolve the Hubble tension" into a strong conclusion whenever anything
    # else in the sentence was confirmed (Codex review 2026-09-03).
    r"|\bdo(?:es)?\s+not\s+(?:show|support|establish|favou?r|indicate"
    r"|resolve|alleviate|eliminate|remove)\b"
    r"|\bfailed?\s+to\s+(?:show|establish|detect|resolve|alleviate)\b"
    # "confirmed / shown / found not to <verb>" is deliberately absent here
    # too: see the note in _NONASSERTIVE_COSMOLOGY_CONTEXT_RE (withdrawn
    # 2026-09-03).  "The void model is shown not to resolve the Hubble
    # tension" is caught, as on main; "Our hypothesis is confirmed not to
    # resolve the Hubble tension" is caught here through the narrowed
    # bare-noun hedge (origin/main still exempts it via the bare noun).
    r"|没有(?:统计显著|显著)?证据|证据不足|无法(?:得出|断定|证明)",
    re.I,
)
# A hedge word does not hedge a sentence that also announces a confirmation:
# "The forecast that X is resolved is now confirmed" is an assertion.  Checked
# after the hedge patterns so it can only REMOVE an exemption.
_CONFIRMED_ASSERTION_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|had|now)"
    r"(?:\s+(?:now|since|already|finally|independently|subsequently))?"
    r"(?:\s+been)?\s+(?:now\s+)?confirmed\b"
    r"|\bconfirms?\s+that\b|\bconfirmed\s*[:\u2014-]"
    r"|(?:得到|已被|已经?)证实|已证实",
    re.I,
)
# A confirmation in an EARLIER clause cancels the hedge only when what it
# confirms is the hypothesis itself ("Our forecast is confirmed: X",
# "假设：已被证实，X").  A confirmation of some other noun ("The calibration is
# confirmed, while our hypothesis is that X") confirms a premise and must
# leave the hedge standing (Codex review 2026-09-03).
_CONFIRMED_HYPOTHESIS_RE = re.compile(
    r"\b(?:hypothes[ei]s|forecasts?|predictions?|claims?|conjectures?)\b[^\n;]{0,24}?"
    r"\b(?:is|are|was|were|has|have|had|now)"
    r"(?:\s+(?:now|since|already|finally|independently|subsequently))?"
    r"(?:\s+been)?\s+(?:now\s+)?confirmed\b"
    r"|\bconfirms?\s+(?:the|our|this)\s+(?:hypothes[ei]s|forecast|prediction|claim)\b"
    r"|^[^\n]{0,24}?(?:假设|预测|猜想)[:：][^\n]{0,12}?(?:得到|已被|已经?)?证实",
    re.IGNORECASE,
)
_ZH_HUBBLE_TENSION_RESOLUTION_RE = re.compile(
    r"(?:哈勃|H\s*0)张力[^。；;.!！？\n]{0,80}(?:已)?(?:解决|缓解|消除|解除|终结)|"
    r"(?:解决|缓解|消除|解除|终结)[^。；;.!！？\n]{0,80}(?:哈勃|H\s*0)张力",
    re.I,
)
_ZH_SPATIAL_CURVATURE_CONCLUSION_RE = re.compile(
    r"(?:空间曲率|宇宙曲率|宇宙学曲率|Ω\s*[_\{]?k\}?|弯曲宇宙)"
    r"[^。；;.!！？\n]{0,100}(?:支持|偏好|探测|检测|非零|不为零|偏离零|排除平直|排斥平直)|"
    r"(?:支持|偏好|证据|探测|检测)[^。；;.!！？\n]{0,100}(?:空间曲率|宇宙曲率|宇宙学曲率)"
)
_ZH_NEUTRINO_MASS_DETECTION_RE = re.compile(
    r"(?:中微子质量|中微子质量之和)[^。；;.!！？\n]{0,100}"
    r"(?:探测|检测|测得|支持|偏好|非零|不为零)|"
    r"(?:探测|检测|测得|支持|证据)[^。；;.!！？\n]{0,100}(?:非零)?中微子质量"
)
_ZH_GENERAL_RELATIVITY_REJECTION_RE = re.compile(
    r"(?:广义相对论|广相)[^。；;.!！？\n]{0,100}"
    r"(?:排除|否定|拒绝|不支持|证伪|未通过|失败)|"
    r"(?:修正引力|修改引力)[^。；;.!！？\n]{0,100}(?:支持|偏好|证据|探测|检测)"
)
_ZH_DARK_ENERGY_EVOLUTION_RE = re.compile(
    r"暗能量[^。；;.!！？\n]{0,100}(?:演化|变化|随时间|时间依赖|动态)|"
    r"(?:演化|动态|随时间变化|时间依赖)暗能量"
)
_ZH_BASELINE_REJECTION_RE = re.compile(
    r"(?:宇宙学常数|Λ\s*CDM|LCDM)[^。；;.!！？\n]{0,100}"
    r"(?:被排除|排除|否定|拒绝|不支持|不相容|不一致)"
)
_ZH_MODEL_PREFERENCE_RE = re.compile(
    r"(?:数据|结果|证据)[^。；;.!！？\n]{0,100}(?:支持|偏好)"
    r"[^。；;.!！？\n]{0,60}(?:w0wa\s*CDM|w\s*CDM|CPL|动态暗能量|演化暗能量)",
    re.I,
)

_CONCLUSION_CLAIM_SUBJECTS: dict[str, str] = {
    "hubble_tension_resolution": "hubble_tension",
    "spatial_curvature_preference": "spatial_curvature",
    "neutrino_mass_detection": "neutrino_mass",
    "general_relativity_rejection": "general_relativity",
}


def _normalize_cosmology_model(value: Any) -> str | None:
    norm = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    aliases = {
        "lcdm": "lcdm",
        "lambdacdm": "lcdm",
        "flatlcdm": "lcdm",
        "wcdm": "wcdm",
        "flatwcdm": "wcdm",
        "w0wacdm": "w0wa_cdm",
        "w0wa": "w0wa_cdm",
        "cpl": "w0wa_cdm",
        "oklcdm": "ok_lcdm",
        "curvedlcdm": "ok_lcdm",
        "lcdmmnu": "lcdm_mnu",
        "massiveneutrinolcdm": "lcdm_mnu",
        "gr": "gr",
        "generalrelativity": "gr",
        "modifiedgravity": "modified_gravity",
        "mg": "modified_gravity",
    }
    return aliases.get(norm)


def _attestation_calibration_is_valid(attestation: dict[str, Any]) -> bool:
    calibration = attestation.get("calibration")
    if not isinstance(calibration, dict) or calibration.get("verified") is not True:
        return False
    method = str(calibration.get("method") or "").strip().lower()
    if method == "wilks":
        return (
            calibration.get("assumptions_verified") is True
            and calibration.get("likelihood_only_mle_proven") is True
        )
    if method in {"simulation", "parametric_bootstrap"}:
        return (
            calibration.get("simulation_calibration_verified") is True
            and bool(
                _SHA256_VALUE_RE.fullmatch(
                    str(calibration.get("simulation_manifest_sha256") or "")
                )
            )
        )
    return False


def _attestation_comparison_type_is_valid(
    attestation: dict[str, Any], claim_kind: str
) -> bool:
    comparison_type = str(attestation.get("comparison_type") or "")
    if claim_kind == "hubble_tension_resolution":
        return (
            comparison_type
            in {
                "tension_consistency_test",
                "simulation_calibrated_tension_consistency_test",
            }
            and attestation.get("independence_verified") is True
            and attestation.get("resolution_criterion_verified") is True
        )
    return comparison_type in {
        "likelihood_ratio",
        "simulation_calibrated_likelihood_ratio",
    }


def _validated_conclusion_attestations(tool_results: Any) -> dict[str, dict[str, Any]]:
    """Index exact, same-branch model-comparison attestations by id.

    No recursive boolean walk is permitted here.  Posterior readiness,
    significance readiness, calibration, model pair, data/likelihood hashes,
    and the manifest hash must coexist in one result object produced by an
    approved cosmology tool.  This prevents unrelated session evidence, or two
    different nested branches, from jointly unlocking a headline conclusion.
    """

    indexed: dict[str, dict[str, Any]] = {}
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    for entry in entries or []:
        tool_name, result = _entry_tool_and_result(entry)
        if tool_name not in _CONCLUSION_ATTESTATION_TOOLS or not isinstance(result, dict):
            continue
        candidates = [result]
        if tool_name == "get_cosmology_run_status" and isinstance(result.get("result"), dict):
            candidates = [result["result"]]
        for candidate in candidates:
            if (
                candidate.get("publication_ready") is not True
                or candidate.get("significance_ready") is not True
                or candidate.get("__do_not_claim__") is True
                or candidate.get("success") is False
                or bool(candidate.get("error"))
            ):
                continue
            raw_attestations = candidate.get("conclusion_attestations")
            if not isinstance(raw_attestations, list):
                single = candidate.get("conclusion_attestation")
                raw_attestations = [single] if isinstance(single, dict) else []
            result_manifest_hash = str(
                candidate.get("evidence_manifest_sha256")
                or candidate.get("manifest_sha256")
                or ""
            )
            result_data_fingerprint = str(
                candidate.get("data_fingerprint")
                or (
                    (candidate.get("data") or {}).get("fingerprint")
                    if isinstance(candidate.get("data"), dict)
                    else ""
                )
                or ""
            )
            map_comparison = candidate.get("map_comparison")
            free_w0wa = (
                map_comparison.get("free_w0wa")
                if isinstance(map_comparison, dict)
                else None
            )
            result_likelihood_fingerprint = str(
                candidate.get("likelihood_fingerprint")
                or (
                    (free_w0wa.get("fingerprints") or {}).get("likelihood")
                    if isinstance(free_w0wa, dict)
                    and isinstance(free_w0wa.get("fingerprints"), dict)
                    else ""
                )
                or ""
            )
            for attestation in raw_attestations:
                if not isinstance(attestation, dict):
                    continue
                attestation_id = str(attestation.get("attestation_id") or "")
                manifest_hash = str(attestation.get("manifest_sha256") or "")
                claim_kind = str(attestation.get("claim_kind") or "")
                required_subject = _CONCLUSION_CLAIM_SUBJECTS.get(claim_kind)
                normalized_baseline = _normalize_cosmology_model(
                    attestation.get("baseline_model")
                )
                normalized_alternative = _normalize_cosmology_model(
                    attestation.get("alternative_model")
                )
                if (
                    attestation.get("schema_version")
                    != _CONCLUSION_ATTESTATION_SCHEMA_VERSION
                    or attestation.get("artifact_type")
                    != _CONCLUSION_ATTESTATION_ARTIFACT_TYPE
                    or not attestation_id
                    or attestation.get("publication_ready") is not True
                    or attestation.get("significance_ready") is not True
                    or not _SHA256_VALUE_RE.fullmatch(manifest_hash)
                    or manifest_hash != result_manifest_hash
                    or not _SHA256_VALUE_RE.fullmatch(
                        str(attestation.get("data_fingerprint") or "")
                    )
                    or str(attestation.get("data_fingerprint") or "")
                    != result_data_fingerprint
                    or not _SHA256_VALUE_RE.fullmatch(
                        str(attestation.get("likelihood_fingerprint") or "")
                    )
                    or str(attestation.get("likelihood_fingerprint") or "")
                    != result_likelihood_fingerprint
                    or not _attestation_comparison_type_is_valid(
                        attestation, claim_kind
                    )
                    or not _attestation_calibration_is_valid(attestation)
                    or (
                        required_subject is not None
                        and str(attestation.get("claim_subject") or "")
                        != required_subject
                    )
                    or (
                        required_subject is None
                        and (
                            normalized_baseline is None
                            or normalized_alternative is None
                        )
                    )
                    or (
                        claim_kind
                        in {
                            "spatial_curvature_preference",
                            "neutrino_mass_detection",
                            "general_relativity_rejection",
                        }
                        and (
                            normalized_baseline is None
                            or normalized_alternative is None
                        )
                    )
                ):
                    continue
                indexed[attestation_id] = attestation
    return indexed


# A conclusion sits in one clause of its sentence.  Splitting on these marks
# is only used to decide WHICH clause an explicit denial governs; it never
# decides whether a conclusion exists.
# Clause boundaries for the hedge decision.  Punctuation, and the
# coordinating words that join two propositions without any: "The
# calibration is confirmed and our hypothesis is that X" is two
# propositions and was being read as one, so the confirmation of the first
# cancelled the hedge on the second (Codex review 2026-09-03).
_CONCLUSION_CLAUSE_BREAK_RE = re.compile(
    r"[,;:\u2014\uff0c\uff1b\uff1a]"
    r"|\b(?:but|while|whereas|although|though|however|so\s+that)\b"
    # "and"/"yet" split a sentence into propositions only when a new SUBJECT
    # follows.  Treating every "and" as a boundary detached a coordinated
    # predicate from its own modal -- "may weaken and ultimately be resolved"
    # lost the "may" (Codex review 2026-09-03).
    r"|\b(?:and|yet)\b(?=\s+(?:the|a|an|our|its|their|his|her|this|that|these|"
    r"those|it|we|they|he|she|there|no)\b)",
    re.IGNORECASE,
)
# A comma pair with no coordinating word between them is a parenthetical, not
# a clause boundary: "The Hubble tension may, after recalibration, be resolved
# by a local void" was reduced to " be resolved by a local void" and lost its
# own hedge (Codex review 2026-09-03).
# A conjunct whose verb is a bare infinitive -- be / get / become plus its
# complement, or a bare stem such as "persist" -- has no finite verb of its
# own, so its modal (if any) came from a conjunct before it
# (``_conjunct_inherits_a_modal``).  "been" is not a bare infinitive ("has
# been resolved" is finite), and a stem after "to" is a marked infinitive.
_BARE_INFINITIVE_RE = re.compile(
    r"(?<!\bto\s)\b(?:(?:be|get|become)\s+\w"
    r"|(?:remain|persist|weaken|strengthen|evolve|resolve)\b)",
    re.IGNORECASE,
)
# The Chinese counterpart: a conjunct that 而 / 并 / 且 continues.  Chinese
# marks the continuation on the connective, not on the verb.
_ZH_CONTINUED_CONJUNCT_RE = re.compile(r"^\s*(?:而|并|且)")
_MODAL_RE = re.compile(r"\b(?:may|might|could|would|should|can|will)\b", re.IGNORECASE)
# A comma pair whose content STARTS with a coordinating word is not a
# parenthetical either: ", and after repeated checks," in "The calibration is
# confirmed, and after repeated checks, the Hubble tension may be resolved"
# opens a new proposition.  Reading it as an aside reverted the clause to the
# sentence start, where the unrelated confirmation cancelled the hedge (Codex
# review 2026-09-03, PRRT_kwDORoeoE86etYLJ).  The same word list decides, in
# _prefix_confirmation_introduces_the_clause, whether a confirmation in an
# earlier clause introduces the conclusion or merely sits beside it.
_COORDINATING_WORDS = r"(?:and|but|while|whereas|although|though|yet|so)"
_COORDINATING_WORD_RE = re.compile(rf"\b{_COORDINATING_WORDS}\b", re.IGNORECASE)
_PARENTHETICAL_RE = re.compile(
    rf",(?!\s*{_COORDINATING_WORDS}\b)[^,;:\n]{{0,60}},$",
    re.IGNORECASE,
)
# What the clause split leaves behind that carries no proposition: an empty
# piece after a comma, or a coordinating word on its own.
_BARE_CONJUNCT_RE = re.compile(rf"\s*{_COORDINATING_WORDS}?\s*", re.IGNORECASE)


def _clause_bounds(sentence: str, start: int, end: int) -> tuple[int, int]:
    """Offsets of the clause of ``sentence`` containing the span ``start:end``."""
    left = 0
    previous_left = 0
    for mark in _CONCLUSION_CLAUSE_BREAK_RE.finditer(sentence):
        if mark.end() > start:
            break
        # A comma that CLOSES a parenthetical is not a clause start: the
        # clause reverts to where it began before the parenthetical opened.
        head = sentence[:mark.end()]
        parenthetical = _PARENTHETICAL_RE.search(head)
        if parenthetical is not None and parenthetical.start() + 1 >= left:
            left = previous_left
            continue
        previous_left = left
        left = mark.end()
    right = len(sentence)
    after = _CONCLUSION_CLAUSE_BREAK_RE.search(sentence, end)
    if after is not None:
        right = after.start()
    return left, right


def _clause_around(sentence: str, start: int, end: int) -> str:
    """The clause of ``sentence`` that contains the span ``start:end``."""
    left, right = _clause_bounds(sentence, start, end)
    return sentence[left:right]


def _conjunct_inherits_a_modal(sentence: str, clause: str, clause_start: int) -> bool:
    """True when ``clause`` has no finite verb and a conjunct before it has a modal.

    "The Hubble tension may weaken, and the remaining discrepancy be resolved
    by a local void" is one hedged prediction: "be resolved" is a bare
    infinitive and "may" scopes over both conjuncts.  The clause split left
    the second conjunct with no modal of its own, and reading only the piece
    just before it (" and") lost the hedge; origin/main exempts the sentence
    (Codex review 2026-09-03, round seven).

    A conjunct whose verb is a bare infinitive (``_BARE_INFINITIVE_RE`` with
    no finite modal) -- or, in Chinese, one that 而 / 并 / 且 continues --
    inherits the modal of the nearest earlier conjunct that has one, walking
    back across consecutive such conjuncts and the empty pieces the split
    leaves behind.  The walk stops at the first conjunct with a finite verb
    and reads it exactly as it would be read in its own clause: only a MODAL
    hedge is inherited (a negation does not reach across a clause break),
    and not one that a confirmation cancelled.  A conjunct with a finite
    verb of its own inherits nothing: "..., and a local void resolves it"
    stands alone.
    """

    def _has_no_finite_verb(text: str) -> bool:
        return bool(
            (_BARE_INFINITIVE_RE.search(text) and not _MODAL_RE.search(text))
            or _ZH_CONTINUED_CONJUNCT_RE.match(text)
        )

    if not _has_no_finite_verb(clause):
        return False
    prefix = sentence[:clause_start]
    pieces: list[tuple[int, str]] = []
    start = 0
    for mark in _CONCLUSION_CLAUSE_BREAK_RE.finditer(prefix):
        pieces.append((start, prefix[start : mark.start()]))
        start = mark.end()
    pieces.append((start, prefix[start:]))
    for piece_start, piece in reversed(pieces):
        if _BARE_CONJUNCT_RE.fullmatch(piece) or _has_no_finite_verb(piece):
            continue
        confirmed = _CONFIRMED_ASSERTION_RE.search(piece) is not None or (
            _prefix_confirmation_introduces_the_clause(sentence[:piece_start])
        )
        if confirmed:
            return False
        return any(
            _MODAL_RE.match(match.group(0)) is not None
            for match in _NONASSERTIVE_COSMOLOGY_CONTEXT_RE.finditer(piece)
        ) or any(
            match.group(0) in ("可能", "或许")
            for match in _ZH_NONASSERTIVE_COSMOLOGY_CONTEXT_RE.finditer(piece)
        )
    return False


def _dark_energy_evolution_anchor(sentence: str) -> int | None:
    """Character offset just past the evolution word the claim is built on.

    ``_dark_energy_evolution_claim`` is a token scan that returns a bool, so
    the conclusion has no span to take a clause from.  The anchor is the
    first evolution or variation word AFTER a "dark energy" / "equation of
    state" subject, within the gap the detector pairs across: that is the
    word the subject's own clause asserts.  Taking the LAST such word in the
    sentence anchored "Dark energy may evolve with time, while galaxy
    formation evolves nonlinearly" in the unrelated second clause, and the
    conclusion lost its own "may" (Codex review 2026-09-03,
    PRRT_kwDORoeoE86etYLM).  When no evolution word follows a subject (the
    "evolving dark energy" order, or the w_a path, which has no subject
    phrase) the last one in the sentence remains the anchor, as before.
    """
    records = _scientific_word_tokens(sentence)
    tokens = [token for token, _, _ in records]
    words = {
        "evolve", "evolves", "evolved", "evolving", "dynamical",
        "vary", "varies", "varied", "variation",
        "timevarying", "timedependent", "varying", "dependent",
        "wa", "nonzero",
    }
    subject_ends: list[int] = []
    for index in range(len(tokens)):
        if tokens[index : index + 2] == ["dark", "energy"]:
            subject_ends.append(records[index + 1][2])
        elif tokens[index : index + 3] == ["equation", "of", "state"]:
            subject_ends.append(records[index + 2][2])
    end: int | None = None
    for token, start, stop in records:
        if token not in words:
            continue
        # The same 120-character gap _dark_energy_evolution_claim pairs across.
        if any(0 <= start - subject_end <= 120 for subject_end in subject_ends):
            return stop
        end = stop
    return end


def _strong_conclusion_from_sentence(sentence: str) -> dict[str, str | None] | None:
    """The first strong conclusion in ``sentence`` that its own clause asserts.

    Every candidate is considered, not just the first that matches.  One
    sentence can carry a DENIED conclusion and an ASSERTED one -- "Although
    there is no evidence for spatial curvature, our forecast is confirmed:
    dark energy evolves with time" -- and stopping at the first match reported
    the denied curvature claim, whose clause is exempt, and never looked at
    the dark-energy claim the sentence actually asserts (Codex review
    2026-09-03).
    """
    if not sentence or "?" in sentence or "？" in sentence:
        return None
    for candidate in _conclusion_candidates(sentence):
        kind, subject, baseline, alternative, conclusion_end = candidate
        if _clause_hedges_the_conclusion(sentence, conclusion_end):
            continue
        if baseline is None and kind not in _CONCLUSION_CLAIM_SUBJECTS:
            baseline = "lcdm" if _LCDM_RE.search(sentence) else None
        if alternative is None and kind not in _CONCLUSION_CLAIM_SUBJECTS:
            alternative = (
                "w0wa_cdm"
                if _W0WA_RE.search(sentence)
                else "wcdm"
                if _WCDM_RE.search(sentence)
                else None
            )
        return {
            "kind": kind,
            "subject": subject,
            "baseline_model": baseline,
            "alternative_model": alternative,
        }
    return None


def _conclusion_candidates(
    sentence: str,
) -> list[tuple[str, str | None, str | None, str | None, int | None]]:
    """``(kind, subject, baseline, alternative, end offset)`` for every match.

    Order is the historical priority order; the caller takes the first
    candidate whose own clause asserts it.
    """
    found: list[tuple[str, str | None, str | None, str | None, int | None]] = []

    def _end(*matches: "re.Match[str] | None") -> int | None:
        for item in matches:
            if item is not None:
                return item.end()
        return None

    hubble = _end(
        _HUBBLE_TENSION_RESOLUTION_RE.search(sentence),
        _ZH_HUBBLE_TENSION_RESOLUTION_RE.search(sentence),
    )
    if hubble is not None:
        found.append(("hubble_tension_resolution", "hubble_tension", None, None, hubble))
    curvature = _end(
        _SPATIAL_CURVATURE_CONCLUSION_RE.search(sentence),
        _ZH_SPATIAL_CURVATURE_CONCLUSION_RE.search(sentence),
    )
    if curvature is not None:
        found.append((
            "spatial_curvature_preference", "spatial_curvature", "lcdm", "ok_lcdm", curvature,
        ))
    neutrino = _end(
        _NEUTRINO_MASS_DETECTION_RE.search(sentence),
        _ZH_NEUTRINO_MASS_DETECTION_RE.search(sentence),
    )
    if neutrino is not None:
        found.append((
            "neutrino_mass_detection", "neutrino_mass", "lcdm", "lcdm_mnu", neutrino,
        ))
    relativity = _end(
        _GENERAL_RELATIVITY_REJECTION_RE.search(sentence),
        _ZH_GENERAL_RELATIVITY_REJECTION_RE.search(sentence),
    )
    if relativity is not None:
        found.append((
            "general_relativity_rejection", "general_relativity", "gr",
            "modified_gravity", relativity,
        ))
    baseline_rejection = _end(
        _BASELINE_REJECTION_RE.search(sentence),
        _ZH_BASELINE_REJECTION_RE.search(sentence),
    )
    if baseline_rejection is not None:
        found.append(("baseline_rejection", None, None, None, baseline_rejection))
    preference = _end(
        _MODEL_PREFERENCE_RE.search(sentence),
        _ZH_MODEL_PREFERENCE_RE.search(sentence),
    )
    if preference is not None:
        found.append(("extended_model_preference", None, None, None, preference))
    if _dark_energy_evolution_claim(sentence) or _ZH_DARK_ENERGY_EVOLUTION_RE.search(sentence):
        zh = _ZH_DARK_ENERGY_EVOLUTION_RE.search(sentence)
        # The English detector is a token scan with no span, so the last
        # evolution word is the anchor its clause is built around.
        found.append((
            "dark_energy_evolution", None, None, None,
            zh.end() if zh is not None else _dark_energy_evolution_anchor(sentence),
        ))
    return found


def _clause_hedges_the_conclusion(sentence: str, conclusion_end: int | None) -> bool:
    """True when the conclusion's own clause hedges, denies or labels it.

    Every term describes a specific proposition, so all three are read in the
    conclusion's clause: reading them across the whole sentence attached a
    denial of another topic to a confirmed conclusion, and a confirmation of
    another topic to a real hedge (Codex review 2026-09-03).  The clause is
    taken from the conclusion's END, because the subject regexes may run
    across a comma.

    Two sentence-scoped exceptions, both of which label the WHOLE sentence:
    a sentence-initial "Hypothesis:" marker, and a hedge that introduces the
    conclusion through a colon ("This is a hypothesis worth testing: ...").
    The colon prefix counts only when it carries no confirmation of its own,
    which is what separates it from "our forecast is confirmed: ...".
    """
    if conclusion_end is None:
        clause, clause_start = sentence, 0
    else:
        clause_start, clause_end = _clause_bounds(
            sentence, max(0, conclusion_end - 1), conclusion_end
        )
        clause = sentence[clause_start:clause_end]
    prefix = sentence[:clause_start]
    confirmed = bool(
        _CONFIRMED_ASSERTION_RE.search(clause)
    ) or _prefix_confirmation_introduces_the_clause(prefix)
    if confirmed and not _EXPLICIT_DENIAL_RE.search(clause):
        return False
    if _HYPOTHESIS_LABEL_RE.search(sentence):
        return True
    if _NONASSERTIVE_COSMOLOGY_CONTEXT_RE.search(clause) or _ZH_NONASSERTIVE_COSMOLOGY_CONTEXT_RE.search(clause):
        return True
    # One modal can scope two coordinated conjuncts: "The Hubble tension may
    # weaken, and the remaining discrepancy be resolved by a local void"
    # leaves the second with a BARE infinitive and no modal of its own, so
    # its hedge lives in a conjunct before it (Codex review 2026-09-03).
    # Only a conjunct with no finite verb inherits, so "... and the Hubble
    # tension IS resolved" still stands on its own.
    if _conjunct_inherits_a_modal(sentence, clause, clause_start):
        return True
    if prefix.rstrip().endswith((":", "\uff1a")) and not _CONFIRMED_ASSERTION_RE.search(prefix):
        return bool(
            _NONASSERTIVE_COSMOLOGY_CONTEXT_RE.search(prefix)
            or _ZH_NONASSERTIVE_COSMOLOGY_CONTEXT_RE.search(prefix)
        )
    return False


def _prefix_confirmation_introduces_the_clause(prefix: str) -> bool:
    """True when a confirmed hypothesis in ``prefix`` introduces the clause after it.

    "Our hypothesis is confirmed: X" and "假设：已被证实，X" confirm X itself.
    "Our dark-energy forecast is confirmed, while our hypothesis is that X"
    confirms one proposition and hedges another, and the coordinating word is
    what says so; searching the whole prefix cancelled the hedge on X (Codex
    review 2026-09-03, PRRT_kwDORoeoE86eypXC).  A confirmation introduces the
    clause when the prefix ends with a colon or dash, or when no coordinating
    word stands between the confirmation and the clause.
    """
    match = _CONFIRMED_HYPOTHESIS_RE.search(prefix)
    if match is None:
        return False
    if prefix.rstrip().endswith((":", "\uff1a", "\u2014")):
        return True
    return _COORDINATING_WORD_RE.search(prefix, match.end()) is None


def _attestation_matches_claim(
    attestation: dict[str, Any], claim: dict[str, str | None]
) -> bool:
    if str(attestation.get("claim_kind") or "") != claim.get("kind"):
        return False
    subject = claim.get("subject")
    if subject is not None and str(attestation.get("claim_subject") or "") != subject:
        return False
    baseline = claim.get("baseline_model")
    alternative = claim.get("alternative_model")
    if baseline is None and alternative is None:
        return subject is not None
    return (
        baseline is not None
        and alternative is not None
        and _normalize_cosmology_model(attestation.get("baseline_model")) == baseline
        and _normalize_cosmology_model(attestation.get("alternative_model"))
        == alternative
    )


_SCIENTIFIC_SENTENCE_TERMINATORS = frozenset(".。\n;；!?！？")


def _iter_scientific_sentence_spans(text: str) -> Iterable[tuple[int, str]]:
    """Yield sentence-like spans in one linear pass over untrusted text."""

    start = 0
    for index, character in enumerate(text):
        if character not in _SCIENTIFIC_SENTENCE_TERMINATORS:
            continue
        if (
            character == "."
            and index > 0
            and index + 1 < len(text)
            and text[index - 1].isdigit()
            and text[index + 1].isdigit()
        ):
            continue
        # Match the prior catalogue's requirement that a segment contain at
        # least one non-terminator before its closing punctuation.
        if index > start:
            yield start, text[start : index + 1]
        start = index + 1
    if start < len(text):
        yield start, text[start:]


def scientific_conclusion_scope_violations(
    reply: str,
    tool_results: Any,
) -> list[CitationViolation]:
    """Require a same-sentence reference to an exactly matched attestation."""
    if not reply:
        return []

    stripped, stripped_map = _strip_markdown_code_with_map(reply)
    attestations = _validated_conclusion_attestations(tool_results)
    violations: list[CitationViolation] = []
    original_cursor = 0
    line_number = 1
    for start, sentence_span in _iter_scientific_sentence_spans(stripped):
        original_start = stripped_map[start]
        line_number += reply.count("\n", original_cursor, original_start)
        original_cursor = original_start
        sentence = sentence_span.strip()
        claim = _strong_conclusion_from_sentence(sentence)
        if claim is None:
            continue
        evidence_ids = [m.group("id") for m in _EVIDENCE_REF_RE.finditer(sentence)]
        if any(
            evidence_id in attestations
            and _attestation_matches_claim(attestations[evidence_id], claim)
            for evidence_id in evidence_ids
        ):
            continue
        violations.append(
            CitationViolation(
                kind="cosmology_conclusion_without_matched_attestation",
                match_text=sentence,
                line_number=line_number,
            )
        )
    return violations


def blocked_scientific_conclusion_reply_text(
    violations: list[CitationViolation],
) -> str:
    """Return a claim-free replacement for an unsupported headline result.

    Do not echo the rejected sentence here: the replacement itself is a public
    chat exit, and repeating the unsupported conclusion under a warning banner
    would still expose it as prose that users can quote out of context.
    """

    count = len(violations)
    return (
        "⚠ Scientific conclusion withheld: "
        f"{count} strong qualitative conclusion{'s' if count != 1 else ''} "
        "did not have an exact same-sentence reference to a matching, "
        "publication-ready scientific conclusion attestation from this turn. "
        "No headline conclusion is presented. Run the required calibrated "
        "likelihood or tension analysis, then cite its evidence attestation in "
        "the same sentence."
    )


def enforce_scientific_conclusion_gate(
    reply: str,
    tool_results: Any,
) -> tuple[str, list[CitationViolation]]:
    """Apply the reusable final-boundary gate for qualitative science claims."""

    violations = scientific_conclusion_scope_violations(reply, tool_results)
    if not violations:
        return reply, []
    return blocked_scientific_conclusion_reply_text(violations), violations


def _full_external_likelihood_ready_available(tool_results: Any) -> bool:
    """Whether a full EXTERNAL (Cobaya/CosmoSIS) likelihood run finished.
    In-process chains never satisfy this, whatever their fidelity — wording
    that asserts an external run must be backed by an external run."""
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
        # 2026-07-03: cobaya_runner._runner_success never emits a top-level
        # execution_mode or claim_scope, so the check above could never fire
        # for a genuine EXTERNAL_COBAYA_ENABLED chain — honest "ready for a
        # full external Cobaya run" wording stayed blocked (the 9f2667e
        # false-positive class). The external markers it DOES emit are the
        # analysis_status literal "EXTERNAL_COBAYA_READY" (only produced by
        # the external backend, only when publication_ready) and the
        # per-dataset registry attribute inside datasets_used — the dispatch
        # precondition (_all_external_cobaya) requires EVERY selected entry
        # to be external. Require both markers so no in-process chain can
        # ever inherit this unlock.
        if tool_name == "run_cosmology_likelihood_chain":
            status = str(result.get("analysis_status") or "").strip().upper()
            datasets_used = result.get("datasets_used")
            if (
                status == "EXTERNAL_COBAYA_READY"
                and isinstance(datasets_used, list)
                and datasets_used
                and all(
                    isinstance(dataset, dict)
                    and str(dataset.get("execution_mode") or "").lower()
                    in {"external_cobaya", "external_cosmosis"}
                    for dataset in datasets_used
                )
            ):
                return True
    return False


def _in_process_full_fidelity_chain_available(tool_results: Any) -> bool:
    """Whether an in-process chain that executed ONLY released, sha256-verified
    full-fidelity likelihood products finished (claim_scope
    'executable_full_fidelity_likelihoods', e.g. the union3 22-bin vector).
    Supports plain 'full likelihood ... ready' wording — NOT wording that
    asserts an external Cobaya/CosmoSIS run (2026-06-12 gate check: a broad
    scope-based unlock let a false 'full external Cobaya' claim through).

    Turn-level evidence cannot attribute a sentence to a specific chain, so
    the unlock is conservative: if ANY compressed-scope chain also ran this
    turn, return False — otherwise a 'ready for full likelihood' sentence
    about the compressed chain would inherit the full-fidelity chain's
    immunity (mixed-turn laundering, same gate check). The cost is that
    honest full-likelihood wording in a mixed turn stays blocked; the common
    clean single-chain turn is what the false-positive fix targets."""
    found_full_fidelity = False
    for entry in tool_results if isinstance(tool_results, list) else [tool_results]:
        tool_name, result = _entry_tool_and_result(entry)
        if tool_name not in {
            "run_cosmology_likelihood_chain",
            "run_cosmology_robustness_matrix",
        }:
            continue
        scope = str(result.get("claim_scope") or "").lower()
        if "compressed" in scope:
            return False
        if (
            tool_name == "run_cosmology_likelihood_chain"
            and scope == "executable_full_fidelity_likelihoods"
            and _payload_is_claimable_success(tool_name, result)
        ):
            found_full_fidelity = True
    return found_full_fidelity


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


def limited_citation_reply_text(violations: list[CitationViolation]) -> str:
    """Citation detail for an answer that remains visible with a caveat.

    The underlying violations and recovery instructions are identical to the
    hard-block banner. Only the user-facing disposition changes: the original
    answer is still shown, so saying the whole reply was withheld is false.
    """
    return blocked_citation_reply_text(violations).replace(
        "⚠ Reply withheld:",
        "⚠ Unsupported citation note:",
        1,
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


def limited_methodology_reply_text(violations: list[CitationViolation]) -> str:
    """Methodology detail for an answer that remains visible with a caveat."""
    return blocked_methodology_reply_text(violations).replace(
        "⚠ Reply withheld:",
        "⚠ Unsupported methodology note:",
        1,
    )


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
        if result.get("__do_not_claim_source_measurement__") is True:
            # A scalar receipt may retain a valid derived result when the
            # source fetch timed out or conflicted.  In that state its source
            # identifiers are diagnostic only and must not seed the valid
            # citation pool or turn user-supplied provenance into evidence.
            result = {
                key: value
                for key, value in result.items()
                if key != "source_evidence"
            }
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
        "run_nested_sampler",
        "run_research_matrix",
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
    """Lowercase text from dedicated paper-source payloads only.

    Exact author-year prose in a returned abstract can support the same phrase
    in a reply.  Generic compute stdout cannot: model-authored ``run_python``
    code could simply print an invented citation and otherwise launder it.
    """
    chunks: list[str] = []
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    for entry in entries or []:
        tool_name, result = _entry_tool_and_result(entry)
        if tool_name not in _PAPER_LEVEL_TOOLS:
            continue
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
    "verify_scalar_derivation",
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

    # A bibcode's final character is only the first-author initial.  It cannot
    # distinguish, for example, Parker (2020) from Planck (2020), so it must
    # never authenticate an author-year citation.  Bibcodes remain valid when
    # cited explicitly; author-year prose needs structured author/year support
    # (or an exact phrase/source identifier handled by the caller).
    _ = valid_bibcodes
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
        "2. Never repeat a number from earlier, previous, pasted, quoted, "
        "transcript, or user-supplied context, even while disclaiming it; "
        "refer to it only as 'the unverified pasted value'.\n"
        "3. Keep all correctly-cited current-turn numbers.\n"
        "4. Do NOT call any tools again; just rewrite the prose.\n"
        "5. Do NOT invent substitute numbers.\n"
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
        # Its structured diagnostics keep the turn non-empty, while the final
        # agent-loop boundary separately withholds posterior values from prose.
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


_DECLARED_COSMOLOGY_KEYS = _COSMOLOGY_MANIFEST_KEYS


def _iter_declared_cosmology_subtrees(node: Any) -> Iterable[dict]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _DECLARED_COSMOLOGY_KEYS and isinstance(value, dict):
                # Only a curated preset (valid bibcode) is provenance-citeable;
                # a model-authored legacy spec (bibcode None) is skipped so this
                # anchor gate AGREES with the validate_claims universe skip
                # (2026-06-12 review #1 — the two gates must not contradict).
                if not _manifest_subtree_is_skippable(value):
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
    this turn (curated cosmology-manifest subtrees only; signed
    ±tolerance band, same matching as validate_claims).

    Used by chat.py's cosmology-anchor comparison gate: fit_line_lfr's
    cosmology_manifest carries the Planck18 preset (H0=67.36, Om0=0.3153,
    sigma8=0.8111) the fit assumed, while compare_luminosity_distances returns
    current_cosmology / target_cosmology manifests. Citing values from those
    curated, bibcode-bearing subtrees is provenance-correct. Deliberately NOT
    matched against the full tool
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
    # Read declared cosmologies from RESULTS only (a manifest in a tool INPUT
    # is model-authored, 2026-06-12 review #1) and subtract model input numbers.
    result_nodes = _result_only_nodes(tool_results) if tool_results is not None else tool_results
    pool: set[float] = set()
    for subtree in _iter_declared_cosmology_subtrees(result_nodes):
        pool.update(_iter_numeric_values(subtree))
    pool -= _model_input_numbers(tool_results) if tool_results is not None else set()
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
