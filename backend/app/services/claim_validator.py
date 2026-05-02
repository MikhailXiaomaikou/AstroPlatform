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
# PART Y Batch 1: PROVENANCE_VALIDATOR_HARDBLOCK 默认开启 — citation 违规
# (suspicious_author_year / invalid_bibcode) 默认硬拦, 跟 ZERO-FABRICATION
# CONTRACT 的数值规则对齐. 显式 PROVENANCE_VALIDATOR_HARDBLOCK=false 才能
# 把 citation 违规降级回 warn-only (生产紧急关闭用).
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
    # Pre-PART-W 中文 pattern (period / distance): 保留给旧测试兼容.
    # X (PART X 方案 D): reply 强制英文之后这两条几乎不会触发 — 因为含
    # CJK 的 reply 已在上游被硬拦. 保留仅作最后兜底.
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

    # R14: synthetic 诊断代码常输出无天文单位的摘要统计
    # ("mean=3.0", "std≈1.414")。这些仍然是数值 claim, 不能从
    # SYNTHETIC stdout 里被洗白引用。
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
    # L1 (2026-04-20 audit): 补齐光谱/X-ray/射电/高红移单位, 之前漏了
    # Å / nm / μm / Gpc / keV / eV / MeV / erg·s⁻¹·cm⁻² / μJy / Jy / THz /
    # kHz.  没有这些单位时整个 "6563 Å ± 1 Å", "L_X = 1e44 erg/s ± 1e42"
    # 这类光谱 / X 射线 claim 不被抽取, 零幻觉门直接失效.
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

    # L1 (audit 2026-04-20): 裸单位形式 (无 ± 误差部分, 直接 "数值 单位").
    # 之前的 label_colon 需要前缀词 (period/distance/...), value_with_error
    # 需要 ± 符号, 中间这种常见形式没覆盖:
    #   "Hα emission at 6563 Å", "L_X = 1.5e44 erg/s", "peak at 1.4 GHz",
    #   "flux 12.3 mJy", "at z=0.5 the luminosity is 3e10 L_sun".
    # 覆盖范围跟 value_with_error 一致, 确保波长/频率/通量/能量 claim
    # 统一走匹配.
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
    """把简单英文数字短语转成 float, 仅用于 claim 抽取兜底。"""
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
    """在抽取 prose 数值 claim 前移除 markdown 代码区。

    工具 schema / help 回复里常有 ``limit: 24`` 这种参数默认值, 或 SQL
    示例。它们是接口元数据, 不是天文结论, 不应进入 zero-fabrication gate。
    """
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", " ", text)
    return text


def extract_claims(text: str) -> list[Claim]:
    """Scan a reply for astronomical numeric claims.

    F1.1: multi-group patterns (value_with_error, ra_dec_pair) emit one
    Claim per captured group so both the central value AND the error bar
    (or both RA AND Dec) must match tool output.

    L1 (audit 2026-04-20): 后处理去重 — 当两个 pattern 捕获**同一个数值**
    且 span 有重叠时, 保留"语义更具体"的那一条(通常是 span 更长、包含
    前缀标签的形式).  例如 "parallax is 9.00 mas" 同时被 parallax_mas
    (span 4-24) 和 value_bare_unit (span 16-24) 匹到, 只保留前者.
    避免漏检光谱单位的同时不重复计数.
    """
    text = _strip_markdown_code(text)
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
                    start=span[0],
                    end=span[1],
                ))
    for match in _SPELLED_NUMBER_PATTERN.finditer(text):
        value = _spelled_number_to_float(match.group(1))
        if value is None or not math.isfinite(value):
            continue
        claims.append(Claim(
            label="spelled_number",
            raw=match.group(0).strip(),
            value=value,
            start=match.start(),
            end=match.end(),
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


# L2 (audit 2026-04-20): 元数据字段黑名单.  Pleiades "776 stars"
# laundering 的根因 — 工具返回 `{"row_count": 776}`, AI 说 "776 member
# stars", validator 扫完数字池发现 776 在里面 (来自 row_count 这种系统
# 字段), 放行.  审计后这些**系统性元数据 key** 上带的数字不进池子.
# 注: 只跳过 key 一层, 不影响嵌套 value (如果数据列碰巧叫 row_count,
# 只影响那一层, 影响范围可接受).
_METADATA_KEYS_BLACKLIST: frozenset[str] = frozenset({
    # 查询元信息
    "row_count", "showing", "has_data", "truncated",
    "elapsed_seconds", "elapsed_ms", "timeout_s",
    "timestamp", "timestamp_utc", "created_at", "updated_at",
    # HTTP / retry 元信息
    "status_code", "http_status", "attempts", "retry_count",
    # 身份 / 复现 envelope
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
    # 结果状态标志 (虽然多数是字符串或 bool, 偶尔是 code)
    "success", "error_class", "argument", "error_code",
    "analysis_status", "__tool_status__", "data_origin",
    # 偏移 / 分页
    "offset", "limit", "per_page", "page", "total_pages",
    # 记录数元信息 (跟 row_count 同类)
    "num_rows", "num_cols", "n_rows", "n_cols", "total_count",
})


def _iter_numeric_values(payload: Any, _in_blacklisted_key: bool = False) -> Iterable[float]:
    """Yield every finite numeric scalar from claimable tool payloads.

    L2: 跳过 _METADATA_KEYS_BLACKLIST 里的顶层字段 — AI 不应该能引用
    `row_count` / `timestamp` / `status_code` 这些系统字段里的数字当
    观测结果.  _in_blacklisted_key 用来 propagate: 如果外层 key 是
    row_count, 该子树整个不进池.
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
            # L2: 系统性元数据字段整体跳过
            key_str = str(key).lower() if not isinstance(key, str) else key.lower()
            if key_str in _METADATA_KEYS_BLACKLIST:
                continue
            yield from _iter_numeric_values(val)
    elif isinstance(payload, list):
        for val in payload:
            yield from _iter_numeric_values(val)
    elif isinstance(payload, str):
        # A tool may serialise numbers inside a string (common for CSV /
        # preview rows).  Extract float-looking tokens cheaply.
        for token in re.findall(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?", payload):
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
    target = abs(value)
    lo, hi = target * (1 - tolerance), target * (1 + tolerance)
    for candidate in universe:
        if candidate == 0.0:
            continue
        c = abs(candidate)
        if lo <= c <= hi:
            return True
    return False


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

    uncited = [c for c in claims if not _matches_any(c.value, universe, effective_tol)]
    return ValidationResult(
        ok=not uncited,
        claims=claims,
        uncited=uncited,
        universe_sample=universe_sample,
        universe_size=universe_size,
        strict_mode=strict_mode,
    )


def citation_validator_hardblock_enabled() -> bool:
    # PART Y Batch 1: 默认 True, 显式 PROVENANCE_VALIDATOR_HARDBLOCK=false
    # 才禁用. 不再 fall back 到模块级 CITATION_VALIDATOR_HARDBLOCK 常量,
    # 这样测试 monkeypatch.setenv / delenv 能在运行时切换状态.
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
    for node in _iter_dict_nodes(tool_results):
        for key, value in node.items():
            if not value:
                continue
            if key in {"bibcode", "article"} or key.endswith("_bibcode"):
                pool.update(_bibcodes_from_text(str(value)))

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
    for node in _iter_dict_nodes(tool_results):
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
    for node in _iter_dict_nodes(tool_results):
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
        if not year:
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
    r"\b(?:not|no|without|requires?|require|would|future|pending|not\s+run|not\s+included|still\s+need|"
    r"config(?:uration)?|workflow)\b",
    re.IGNORECASE,
)
# PART AI #3: LFR-context signature — reply 必须明显在做 line luminosity-FWHM
# relation (而不是 isochrone slope / photometry alpha 之类), bypass detector
# 才触发. 防误伤其它工作流.
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
        if _FULL_EXTERNAL_LIKELIHOOD_NONCLAIM_RE.search(sentence):
            continue
        if not _full_external_likelihood_ready_available(tool_results):
            violations.append(CitationViolation(
                kind="full_likelihood_overclaim",
                match_text=full_match.group(0),
                line_number=_line_number(reply, full_match.start()),
            ))

    # ── PART AI #3: fit_line_lfr bypass detection ────────────────────
    # Bundle e8d9 reproducer: reply 里报 LFR 数字 (slope/intercept/scatter)
    # 但本轮没调 fit_line_lfr — AI 在 run_python 里自己用 cached rows 跑
    # OLS/linmix 然后 prose 当数字报, 完全绕过 fit_line_lfr 的 PARTIAL gate
    # + cosmology recompute + lensing demagnify + Bayesian diagnostics.
    # 这条检查独立于 raw_fit_results, 因为 raw_fit_results 是空才触发.
    # 限定只在 reply 明显在做 LFR 工作流 (LFR keyword + 数字) 时触发,
    # 防误伤 isochrone slope / photometry alpha 等其它工作流的合法用法.
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
        if tool_name not in {"run_cosmology_likelihood_chain", "run_cosmology_robustness_matrix"}:
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
    }:
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

    return len(matches) < 2


def _author_year_looks_like_noise(author: str, match_text: str) -> bool:
    if author.upper() in {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}:
        return True
    if "et al" in match_text.lower():
        return False
    if "(" in match_text or "[" in match_text:
        return False
    return author in {
        "April",
        "August",
        "December",
        "February",
        "Figure",
        "January",
        "July",
        "June",
        "March",
        "May",
        "November",
        "October",
        "Section",
        "September",
        "Table",
    }


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

        if kind in {
            "invalid_bibcode",
            "suspicious_author_year",
            "unsupported_literature_narrative",
            "paper_numeric_missing_citation",
        }:
            reason = kind
        else:
            reason = "suspicious_author_year"
        record_counter("fabrication_blocked_total", 1.0, reason=reason)
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

        synthetic_or_unciteable = (
            inner.get("__do_not_claim__") is True
            or outer.get("__do_not_claim__") is True
            or str(inner.get("data_origin") or "").lower() == "synthetic"
            or str(outer.get("data_origin") or "").lower() == "synthetic"
            or any(tok in {"SYNTHETIC", "SIMULATED_DEMO"} for tok in status_tokens)
        )

        explicit_fail = (
            not partial_with_payload
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


# W1 (PART W): 文献先验硬黑名单 — age/mass/distance 必须有对应测量/引用工具
# 出现在 tool_results 才允许在 reply 里引用. 独立于 ±1% universe 匹配;
# 即使 universe 偶然包含数字 100, "age ~100 Myr" 也会被硬拦除非本轮跑了
# 对应工具. 防止 LLM 借 Gaia ADQL 返回表里某行的值洗白教科书先验.
_LITERATURE_PRIOR_LABELS_REQUIRE_TOOL: dict[str, tuple[str, ...]] = {
    # age: 要么测量 (fit_isochrone), 要么引用 (search_literature),
    # 要么从 dossier 拿现成的 (get_object_dossier).
    "age_myr": ("fit_isochrone", "search_literature", "get_object_dossier"),
    "age_gyr": ("fit_isochrone", "search_literature", "get_object_dossier"),
    # mass: 除了拟合 / 文献 / dossier, 也可从 Gaia / SDSS 质量列拿 (run_adql).
    "mass_solar": (
        "fit_isochrone", "search_literature", "get_object_dossier", "run_adql",
    ),
    # distance: Gaia parallax (run_adql / get_object_info), extinction helper
    # (get_extinction 返回含距离), dossier, 或文献引用.
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
    # 中文 label 已从 _PATTERNS 移除 (PART X 方案 D — reply 强制英文),
    # 所以 _zh 键不再需要. 中文 prose 在 chat.py 的 reply_contains_cjk
    # hardblock 上游就被拦下, 不会走到 claim 提取.
}


def literature_prior_violations(reply: str, tool_results: Any) -> list[Claim]:
    """W1 (PART W): 检测 textbook-prior 式 age/mass/distance 引用.

    当 reply 里出现这些 label 的数字 claim, 但本轮 tool_results 里没有任何
    对应成功且可引用的测量/引用工具结果 (fit_isochrone / search_literature /
    get_object_dossier / run_adql / get_object_info / get_extinction), 返回
    违规 claim 列表. 比 validate_claims 的 ±1% universe 匹配更严: 独立于
    universe 里是否偶然有相近数字, 直接按 "有没有成功产出可引用结果" 判断.

    Args:
        reply: assistant 回复原文
        tool_results: 本轮工具调用结果列表 (每条 dict 含 "tool" key)

    Returns:
        违规 claim 列表. 空 list 表示所有相关 claim 都有对应工具支撑.
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


# X (PART X 方案 D): reply 强制英文 — 含 CJK / 日文 / 韩文 / 全角
# punctuation 的最终回复直接硬拦. 复用 PART W figure-language-guard
# 的正则范围 (CJK Unified / Hiragana / Katakana / Hangul / Full-width).
# Greek 字母 (U+0370-U+03FF) + Å / ° / ± / ≤ / ≥ 等科学 Unicode 不在
# 匹配范围, DejaVu Sans 能正常显示所以允许.
#
# 为何 reply 强制英文:
# 1. 避免 label 黑名单补丁式扩展 ("年龄: ~100 Myr" / "N Myr 的年龄" /
#    "大约 N Myr 年纪" 无限语序变体)
# 2. 对齐 run_python 图表文字规则 (PART W), 整个平台数值输出一致英文
# 3. 科研场景 (论文复现 / 审稿) 本来就是英文工作语言
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

# 阈值 2: 单字符 false-positive 容忍 (e.g. 引号内引用的中文单字 "昴"),
# 但 "根据..." / "符合..." / "与文献一致" 等 prose 引导词 (通常 2+ CJK)
# 必然命中. 自然语言中文 reply 总字数远超 2.
_CJK_REPLY_THRESHOLD = 2


def reply_contains_cjk(reply: str, threshold: int = _CJK_REPLY_THRESHOLD) -> bool:
    """X (PART X 方案 D): return True if the final assistant reply contains
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
