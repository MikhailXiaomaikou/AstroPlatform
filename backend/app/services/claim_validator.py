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
]


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

    def describe(self) -> str:
        """Human-readable summary used for the LLM regeneration prompt."""
        if not self.uncited:
            return "All numeric claims are supported by tool results."
        lines = [
            f"- {c.label} = {c.value} (phrase: \"{c.raw}\")"
            for c in self.uncited
        ]
        return (
            "The following numeric claims were NOT found in any tool_result "
            "this turn and must be removed or replaced with "
            "'not determined by my tools':\n" + "\n".join(lines)
        )


def extract_claims(text: str) -> list[Claim]:
    """Scan a reply for astronomical numeric claims."""
    claims: list[Claim] = []
    seen_spans: set[tuple[int, int]] = set()
    for label, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            span = match.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            try:
                value = float(match.group(1))
            except (ValueError, IndexError):
                continue
            if not math.isfinite(value):
                continue
            claims.append(Claim(
                label=label,
                raw=match.group(0).strip(),
                value=value,
                start=span[0],
                end=span[1],
            ))
    return claims


def _iter_numeric_values(payload: Any) -> Iterable[float]:
    """Yield every finite numeric scalar anywhere in a nested structure."""
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        v = float(payload)
        if math.isfinite(v):
            yield v
    elif isinstance(payload, dict):
        for val in payload.values():
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


def validate_claims(
    reply: str,
    tool_results: Any,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ValidationResult:
    """Check every numeric claim in `reply` against `tool_results`.

    `tool_results` can be a list of dicts (typical `_run_agent_loop`
    accumulator), a single dict, or any nested structure.  We harvest all
    numeric scalars into a set and test each claim for a match within
    `tolerance`.
    """
    claims = extract_claims(reply)
    if not claims:
        return ValidationResult(ok=True, claims=[], uncited=[])

    universe: set[float] = set(_iter_numeric_values(tool_results))
    uncited = [c for c in claims if not _matches_any(c.value, universe, tolerance)]
    return ValidationResult(ok=not uncited, claims=claims, uncited=uncited)


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
    return (
        "⚠ Reply withheld: the model attempted to cite values that were not "
        "produced by any tool this turn, and failed to correct itself after "
        "two attempts. The uncited values were:\n\n"
        + "\n".join(lines)
        + "\n\nPlease rephrase your question — for example, ask me to "
        "search literature for the value, or provide it explicitly in "
        "your prompt."
    )


def dump_tool_universe(tool_results: Any, limit: int = 50) -> str:
    """Debug helper: sample a few numeric values from the tool tree."""
    vals = list(_iter_numeric_values(tool_results))
    vals.sort()
    return json.dumps(vals[:limit])
