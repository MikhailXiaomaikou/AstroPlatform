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
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Tolerance for numeric equality.  ±1 % catches nearly every real-world
# fabrication (the model tends to invent round numbers or swap digits) while
# still matching a tool value re-stated with one-decimal-place precision.
DEFAULT_TOLERANCE = 0.01

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
    ("log_g", re.compile(rf"\blog\s*g\s*[=≈~]\s*{_NUM}\b", re.I)),
    ("metallicity", re.compile(rf"\[Fe\s*/\s*H\]\s*[=≈~]\s*{_NUM}\b", re.I)),
    ("e_bv", re.compile(rf"E\s*\(\s*B\s*[-−]\s*V\s*\)\s*[=≈~]\s*{_NUM}\b", re.I)),
    ("a_v", re.compile(rf"\bA_?V\s*[=≈~]\s*{_NUM}\b", re.I)),
    ("mass_solar", re.compile(rf"{_NUM}\s*(?:M_sun|M☉|solar\s*mass(?:es)?)\b", re.I)),
    ("luminosity_solar", re.compile(rf"{_NUM}\s*(?:L_sun|L☉|solar\s*luminosit(?:y|ies))\b", re.I)),
    ("age_gyr", re.compile(rf"\bage\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*Gyr\b", re.I)),
    ("age_myr", re.compile(rf"\bage\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*Myr\b", re.I)),
    ("teff_k", re.compile(rf"\bT(?:_?eff)?\s*[=≈~]\s*{_NUM}\s*K\b", re.I)),
    ("distance_pc", re.compile(rf"\bdistance\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*pc\b", re.I)),
    ("distance_kpc", re.compile(rf"\bdistance\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*kpc\b", re.I)),
    ("distance_mpc", re.compile(rf"\bdistance\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*Mpc\b", re.I)),
    ("period_days", re.compile(rf"\bperiod\s*(?:of|=|≈|~|is)?\s*{_NUM}\s*days?\b", re.I)),
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
                if pattern.groups and pattern.groups > 1:
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


def blocked_reply_text(result: ValidationResult) -> str:
    """Fallback shown to the user when the LLM cannot stop fabricating."""
    lines = [f"- {c.label} = {c.value}" for c in result.uncited]
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
        "produced by any tool this turn, and failed to correct itself after "
        "two attempts. The uncited values were:\n\n"
        + "\n".join(lines)
        + f"\n\n{universe_snippet}{strict_note}"
        + "\n\nPlease rephrase your question — for example, ask me to "
        "search literature for the value, or provide it explicitly in "
        "your prompt."
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
