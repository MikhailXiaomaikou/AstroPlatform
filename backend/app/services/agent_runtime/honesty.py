"""Deterministic final-boundary checks for Daily honesty contracts.

The normal claim validator understands scientific prose, but deliberately
allows some negated phrases (for example, ``H0 = X cannot be confirmed``) so
an honest explanation is not treated as a positive claim.  The Daily B4/B5
contract is stricter: a rejected user-supplied number must not be repeated at
all.  This module handles that echo channel and the separate rule that
non-publication-ready posterior values belong in tool cards, not final prose.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple


# 2026-09-02 (review H8): the trailing lookahead used to reject a number
# followed by a letter, so ``H0 = 73.2km/s/Mpc`` produced no token at all and
# a withheld posterior with a glued unit escaped the gate.  A unit may now
# follow the number.  The leading lookbehind still rejects letter-led
# identifiers (``H0``, ``DR1``, ``z0``); a digit-led hex digest
# (``3a7e6e4``, ``68a9f3c2``) is rejected by the trailing hex-run lookahead so
# a quoted provenance hash cannot become a number that collides with a
# withheld posterior.
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d+\.\d+|\d+|\.\d+)"
    r"(?:[eE][-+]?\d+)?"
    # A unit may follow the number; a longer hex run (a digit-led provenance
    # hash) and an English ordinal suffix may not.  "the 68th sample" is a
    # draw index, and reading it as a posterior replaced whole honest replies
    # when a withheld median sat near 68 (review 2026-09-03).
    r"(?![0-9_]|\.\d|[a-fA-F][0-9a-fA-F]{3}|(?:st|nd|rd|th)(?![A-Za-z]))"
)
_PERCENT_AFTER_RE = re.compile(r"\s*(?:%|percent\b|per\s+cent\b)", re.IGNORECASE)
# H0 in little-h units, in the notations a user or model actually writes:
# ``h = 0.732``, ``h ≈ .683``, ``h0 = 0.677``, ``H0/100 = 0.677``,
# ``little-h value of 0.677``.  Compared only against withheld H0.
_LITTLE_H_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:H0\s*/\s*100|little[-\s]h(?:\s+value)?|h0|h)"
    r"\s*(?:[=≈:~]|is|of|at)\s*(0?\.\d+)",
    re.IGNORECASE,
)
# A parameter label with an assignment operator right before a token keeps it
# a value claim whatever follows it: ``H0 = 68%`` is never an interval idiom.
_PARAMETER_ASSIGNMENT_BEFORE_RE = re.compile(
    r"\b(?:H0|H₀|omegam|omega_m|Omega_m|sigma8|S8|w0|wa)\b"
    r"[^\n;]{0,28}?[=:~≈]\s*$",
    re.IGNORECASE,
)
_H0_PARAMETER_NAMES = frozenset({"h0", "h_0", "hubble", "hubble_constant"})
# The only percent exemption is the credible/confidence-interval idiom itself:
# a token on a standard interval level, followed by ``%``/``percent``, with
# interval wording in the same clause (``the 68% credible interval``).  Every
# other percent token stays in the withheld universe, so ``67.7 percent for
# H0`` or ``H0 is 67.7% of 100 km/s/Mpc`` is still a withheld restatement
# (adversarial review 2026-09-02: a token-class exemption was a relaxation).
# Matched EXACTLY (see _is_interval_idiom): a reply that writes a level not on
# this list is writing a number, not naming an interval.  Rounded 1-sigma /
# 2-sigma spellings (68.3, 95.4) are deliberately absent: they collide with
# Planck-like H0 medians, and the asymmetric risk is resolved the way this
# project always resolves it — an honest reply can say "68%" or "1 sigma",
# a leak cannot be taken back.
_INTERVAL_LEVELS = (68.0, 68.27, 90.0, 95.0, 95.45, 99.0, 99.7)
_INTERVAL_WORDING_RE = re.compile(
    r"\b(?:interval|credible|confidence|C\.?L\.?|coverage|containment|"
    r"percentile|quantile)\b",
    re.IGNORECASE,
)
# A clause ends at ; ! ? newline, or at a period that ends a sentence — one
# followed by whitespace or the end of the text.  A period inside "C.L." (a
# letter follows immediately) or inside a decimal is not a boundary; the
# earlier form split every period and cut dotted abbreviations in half before
# the interval cue could be recognised (review 2026-09-03).
_CLAUSE_BREAK_RE = re.compile(r"[;!?\n]|\.(?=\s|$)")
_DIGIT_RE = re.compile(r"\d")
_UNTRUSTED_EVIDENCE_RE = re.compile(
    r"(?:tool_results?|tool\s+transcript|previous[- ]looking|"
    r"pasted?\s+(?:result|transcript|context)|user[- ]supplied|"
    r'\"tool\"\s*:|\"result\"\s*:|treat\s+it\s+as\s+verified|'
    r"appeared\s+earlier|same\s+(?:chat|session))",
    re.IGNORECASE,
)
_STRUCTURED_PARAMETER_RE = re.compile(
    r'[\"\'](?:H0|H₀|omegam|omega_m|Omega_m|sigma8|S8|w0|wa)[\"\']\s*:\s*\{'
    r"(?P<body>[^{}]{0,640})\}",
    re.IGNORECASE,
)
_STRUCTURED_STAT_RE = re.compile(
    r'[\"\'](?:median|mean|value|std|sigma|uncertainty(?:_minus|_plus)?|'
    r'lower(?:_68)?|upper(?:_68)?)[\"\']\s*:\s*'
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)",
    re.IGNORECASE,
)
_DIRECT_PARAMETER_RE = re.compile(
    r"\b(?:H0|H₀|omegam|omega_m|Omega_m|sigma8|S8|w0|wa)\b"
    r"[^\n;]{0,28}?[=:~≈]\s*"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)",
    re.IGNORECASE,
)

_POSTERIOR_KEYS = frozenset({
    "parameters",
    "parameter_intervals",
    "posterior_summary",
    "posterior_intervals",
    "credible_intervals",
    "marginalized_constraints",
    "parameter_constraints",
    "derived_params",
    "derived_parameters",
    "pairwise_tensions",
    "tension_lab",
    "two_dimensional_contours",
})


def _finite_numbers(value: Any) -> Iterable[float]:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isfinite(number):
            yield number
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            yield from _finite_numbers(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            yield from _finite_numbers(nested)


def _entry_tool_and_result(entry: Any) -> tuple[str | None, dict[str, Any] | None]:
    if not isinstance(entry, dict):
        return None, None
    tool = str(entry.get("tool") or entry.get("name") or "").strip() or None
    result = entry.get("result") if isinstance(entry.get("result"), dict) else entry
    return tool, result if isinstance(result, dict) else None


def _claimable_result(tool: str | None, result: dict[str, Any] | None) -> bool:
    if not result:
        return False
    if (
        result.get("__do_not_claim__") is True
        or result.get("publication_ready") is False
        or result.get("success") is False
        or bool(result.get("error"))
    ):
        return False
    status = {
        str(result.get(key) or "").strip().upper()
        for key in ("analysis_status", "__tool_status__", "status", "data_origin")
    }
    if status & {"EMPTY", "FAILED", "UNAVAILABLE", "SYNTHETIC", "SIMULATED_DEMO"}:
        return False
    if tool == "get_cosmology_run_status":
        nested = result.get("result")
        return bool(
            isinstance(nested, dict)
            and nested.get("publication_ready") is True
            and nested.get("__do_not_claim__") is not True
        )
    if tool in {
        "fit_cosmology_mcmc",
        "run_cobaya_cosmology",
        "run_cosmology_likelihood_chain",
        "run_cosmology_robustness_matrix",
        "run_cmb_rotation_likelihood",
        "run_nested_sampler",
    }:
        return result.get("publication_ready") is True
    return True


def _claimable_current_values(tool_results: Any) -> set[float]:
    # Reuse the production validator's evidence-only walker so registry
    # context, citations, hashes, warnings, and echoed inputs cannot make a
    # pasted number look independently reproduced.
    from app.services.claim_validator import _iter_numeric_values

    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    values: set[float] = set()
    for entry in entries or []:
        tool, result = _entry_tool_and_result(entry)
        if _claimable_result(tool, result):
            values.update(_iter_numeric_values(result))
    return values


def _untrusted_user_values(messages: list[dict]) -> set[float]:
    values: set[float] = set()
    for message in messages or []:
        if message.get("role") != "user":
            continue
        text = str(message.get("content") or "")
        if not _UNTRUSTED_EVIDENCE_RE.search(text):
            continue
        for match in _DIRECT_PARAMETER_RE.finditer(text):
            values.add(float(match.group("value")))
        for parameter in _STRUCTURED_PARAMETER_RE.finditer(text):
            for stat in _STRUCTURED_STAT_RE.finditer(parameter.group("body")):
                values.add(float(stat.group("value")))
    return {value for value in values if math.isfinite(value)}


class _Token(NamedTuple):
    value: float
    start: int
    end: int
    is_percent: bool
    little_h: bool


def _normalized_reply_with_map(reply: str) -> tuple[str, list[int]]:
    """Rewrite power-of-ten notation as ``claim_validator`` does, keeping offsets.

    Only the sci-notation rewrites are applied (``7.32×10^1`` -> ``7.32e1``);
    code spans are NOT stripped and thousands separators are NOT collapsed,
    so no token that the previous tokenizer produced can disappear.
    ``bmap[i]`` maps a boundary of the returned text back to the original
    reply.
    """
    from app.services.claim_validator import (
        _SCI_BARE_POWER,
        _SCI_SUPERSCRIPT,
        _SUPERSCRIPT_DIGITS,
        _apply_regex_with_map,
        _replace_sci_mantissa_power_with_map,
    )

    # No thousands-separator rewrite here: ``(\d),(\d{3})`` would glue two
    # comma-joined decimals (``144.9,149.3``) into one un-tokenizable run and
    # lose both numbers, which origin/main saw (adversarial review 2026-09-02).
    text = str(reply or "").replace("−", "-")
    bmap = list(range(len(text) + 1))
    text, bmap = _apply_regex_with_map(
        text, bmap, _SCI_SUPERSCRIPT,
        lambda m: "10^" + m.group(1).translate(_SUPERSCRIPT_DIGITS),
    )
    text, bmap = _replace_sci_mantissa_power_with_map(text, bmap)
    text, bmap = _apply_regex_with_map(
        text, bmap, _SCI_BARE_POWER, lambda m: f"1e{m.group(1)}"
    )
    return text, bmap


def _reply_number_spans(reply: str) -> list[_Token]:
    """Every number-like token in ``reply`` with its original-text span.

    The power-of-ten rewrite is additive, never substitutive: tokens are read
    from the rewritten text AND from the raw reply, then unioned.  Reading only
    the rewritten text would consume the raw mantissa — ``67.7 × 10^3 m/s/Mpc``
    becomes ``67.7e3`` and the withheld 67.7 disappears, which is an SI-prefix
    restatement of the same posterior (adversarial review 2026-09-03).
    """
    from app.services.claim_validator import _NUMBER_WORD_TOKEN, _spelled_number_to_float

    raw = str(reply or "").replace("−", "-")
    text, bmap = _normalized_reply_with_map(reply)
    tokens: list[_Token] = []
    for match in _NUMBER_RE.finditer(raw):
        try:
            value = float(match.group())
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        tokens.append(_Token(
            value,
            match.start(),
            match.end(),
            bool(_PERCENT_AFTER_RE.match(raw[match.end():])),
            False,
        ))
    for match in _NUMBER_RE.finditer(text):
        try:
            value = float(match.group())
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        tokens.append(_Token(
            value,
            bmap[match.start()],
            bmap[match.end()],
            bool(_PERCENT_AFTER_RE.match(text[match.end():])),
            False,
        ))
    spelled = re.compile(
        rf"\b({_NUMBER_WORD_TOKEN}(?:[-\s]{_NUMBER_WORD_TOKEN})?"
        rf"\s+point(?:\s+{_NUMBER_WORD_TOKEN})+)\b",
        re.IGNORECASE,
    )
    for match in spelled.finditer(text):
        value = _spelled_number_to_float(match.group(1))
        if value is not None and math.isfinite(value):
            tokens.append(_Token(value, bmap[match.start()], bmap[match.end()], False, False))
    for match in _LITTLE_H_RE.finditer(text):
        try:
            value = float(match.group(1)) * 100.0
        except ValueError:
            continue
        tokens.append(_Token(value, bmap[match.start(1)], bmap[match.end(1)], False, True))
    # The raw and rewritten passes yield the same token wherever no rewrite
    # applied; keep one per (value, span) so callers see a clean list.
    seen: set[tuple[float, int, int, bool]] = set()
    unique: list[_Token] = []
    for token in sorted(tokens, key=lambda t: (t.start, t.end, t.value)):
        key = (token.value, token.start, token.end, token.little_h)
        if key in seen:
            continue
        seen.add(key)
        unique.append(token)
    return unique


def _reply_number_tokens(reply: str) -> list[float]:
    return [token.value for token in _reply_number_spans(reply) if not token.little_h]


def untrusted_evidence_echo_values(
    reply: str,
    messages: list[dict],
    tool_results: Any,
) -> list[float]:
    """Return rejected user-supplied evidence values repeated in ``reply``.

    A value is exempt only when a claimable current-turn tool independently
    produced the same number.  Exact matching is intentional: this gate closes
    the echo channel without treating ordinary request parameters (such as a
    requested redshift) as evidence claims.
    """

    untrusted = _untrusted_user_values(messages)
    if not untrusted:
        return []
    supported = _claimable_current_values(tool_results)
    unsupported = {
        value
        for value in untrusted
        if not any(math.isclose(value, current, rel_tol=1e-12, abs_tol=1e-12) for current in supported)
    }
    hits = {
        token
        for token in _reply_number_tokens(reply)
        if any(math.isclose(token, value, rel_tol=1e-12, abs_tol=1e-12) for value in unsupported)
    }
    return sorted(hits)


def _named_numbers(value: Any, parameter: str = "", stat: str = "") -> Iterable[tuple[str, str, float]]:
    """Yield ``(parameter_name, stat_key, number)`` for every finite number
    under a posterior container.  ``parameter_name`` is the first key below
    the container (``parameters.H0.median`` -> ``("H0", "median", ...)``)."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isfinite(number):
            yield parameter, stat, number
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield from _named_numbers(nested, parameter or str(key), str(key))
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            yield from _named_numbers(nested, parameter, stat)


def _withheld_entries(node: Any, inherited_withhold: bool = False) -> Iterable[tuple[str, str, float]]:
    if isinstance(node, Mapping):
        withhold = inherited_withhold or (
            node.get("publication_ready") is False
            or node.get("__do_not_claim__") is True
        )
        if withhold:
            for key in _POSTERIOR_KEYS:
                if key in node:
                    yield from _named_numbers(node[key])
        for key, nested in node.items():
            if key == "prior_dominance_screen":
                # cosmology_likelihoods/sampling.py:_prior_dominance_screen —
                # prior bounds and edge-mass fractions (0.0 / 1.0 / 0.05),
                # never posterior statistics.  Its inner ``parameters`` map
                # collided with the research summary's own counts ("0 ready
                # out of 7", "1 removed") and replaced whole replies with a
                # refusal (blind case E1, 2026-08-06).  The real posterior of
                # the same run is still harvested from ``parameters`` /
                # ``posterior_summary`` at the result level.
                continue
            yield from _withheld_entries(nested, withhold)
    elif isinstance(node, Sequence) and not isinstance(
        node, (str, bytes, bytearray)
    ):
        for nested in node:
            yield from _withheld_entries(nested, inherited_withhold)


def _is_h0_name(parameter: str) -> bool:
    return parameter.replace("₀", "0").strip().lower() in _H0_PARAMETER_NAMES


def _is_interval_idiom(text: str, token: "_Token") -> bool:
    """``the 68% credible interval``: a standard interval level, written as a
    percentage, with interval wording in the same clause and no parameter
    assignment binding it as a value."""
    # Exact level match, not the withheld-value tolerance: at rel_tol=0.01 a
    # median anywhere in 67.3-68.7 counted as "68", so a Planck-like H0 could
    # be restated verbatim as "the 68.3% credible interval" and skip the gate
    # (adversarial review 2026-09-03).
    if not any(
        math.isclose(token.value, level, rel_tol=0.0, abs_tol=1e-9)
        for level in _INTERVAL_LEVELS
    ):
        return False
    before = text[max(0, token.start - 48):token.start]
    if _PARAMETER_ASSIGNMENT_BEFORE_RE.search(before):
        return False
    after = text[token.end:token.end + 48]
    before_clause = _CLAUSE_BREAK_RE.split(before)[-1]
    after_clause = _CLAUSE_BREAK_RE.split(after)[0]
    # The cue has to describe THIS percentage.  Another number between the
    # token and the cue means the cue belongs to that one instead: in "68% of
    # the reference, with a 95% credible interval" the interval is the 95's
    # (review 2026-09-03).  Trim each window at the nearest other digit.
    other = _DIGIT_RE.search(after_clause)
    if other is not None:
        after_clause = after_clause[:other.start()]
    previous = None
    for match in _DIGIT_RE.finditer(before_clause):
        previous = match
    if previous is not None:
        before_clause = before_clause[previous.end():]
    return bool(
        _INTERVAL_WORDING_RE.search(before_clause)
        or _INTERVAL_WORDING_RE.search(after_clause)
    )


def nonpublication_posterior_values(reply: str, tool_results: Any) -> list[float]:
    """Return non-publication posterior numbers that escaped into prose.

    Matching stays at ``rel_tol=0.01`` for every withheld statistic.  Two
    token classes are compared against narrower sets rather than the whole
    withheld universe: a little-h token (``h = 0.732``) is checked against
    withheld H0 values only, and a percent token (``the 68% interval``) is
    checked against withheld percentage-valued keys only — unless a parameter
    assignment precedes it (``H0 = 67.7%``), which keeps it a value claim.
    """

    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    named: set[tuple[str, str, float]] = set()
    for entry in entries or []:
        _tool, result = _entry_tool_and_result(entry)
        if result:
            named.update(_withheld_entries(result))
    if not named:
        return []
    withheld_all = {value for _parameter, _stat, value in named}
    withheld_h0 = {value for parameter, _stat, value in named if _is_h0_name(parameter)}
    text = str(reply or "")

    def _near(token_value: float, universe: set[float]) -> bool:
        return any(
            math.isclose(token_value, value, rel_tol=0.01, abs_tol=1e-12)
            for value in universe
        )

    hits: set[float] = set()
    for token in _reply_number_spans(text):
        if token.little_h:
            if _near(token.value, withheld_h0):
                hits.add(token.value)
            continue
        if token.is_percent and _is_interval_idiom(text, token):
            continue
        if _near(token.value, withheld_all):
            hits.add(token.value)
    return sorted(hits)


def untrusted_evidence_refusal() -> str:
    return (
        "I cannot verify or repeat the pasted numerical result because no "
        "claimable current-turn analysis produced it. Run the registered "
        "analysis again before using any value in a paper."
    )


def nonpublication_posterior_refusal() -> str:
    return (
        "The run is not publication-ready, so its posterior values are withheld "
        "from this reply. Review the tool card for diagnostics and run the "
        "registered full-likelihood path before making a numerical claim."
    )
