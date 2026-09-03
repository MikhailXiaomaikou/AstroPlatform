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
# identifiers (``H0``, ``DR1``, ``z0``).
# Only a RECOGNISED glued unit may follow the digits (Codex review
# 2026-09-03, PRRT_kwDORoeoE86eyrId): admitting any letter read "comet 67P"
# as 67 and replaced a whole honest reply when a withheld H0 sat near it.
# The same rule keeps out what earlier reviews rejected one by one -- a
# digit-led hex digest (``3a7e6e4``, ``68a9f3c2``), an English ordinal ("the
# 68th sample" is a draw index, review 2026-09-03) and a lone count letter
# (68k samples, 95M draws) -- because none of them starts a unit.  A glued
# ``K`` is deliberately absent: "68K samples" is a count, and a temperature
# is still read as ``2.7 K`` with the space.  The list is case-sensitive so
# ``95M`` cannot pass as metres.
_GLUED_UNIT = (
    r"(?:km|kpc|Mpc|Gpc|Gyr|Myr|yr|pc|keV|GeV|eV|sigma|σ|deg|arcmin|arcsec"
    r"|mag|Hz|nm|μm|Å|s|m|g)"
)
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d+\.\d+|\d+|\.\d+)"
    r"(?:[eE][-+]?\d+)?"
    r"(?![0-9_]|\.\d)"
    # What follows the number: the end of the text, a non-word character
    # (space, %, /, a dash, a bracket), or a recognised unit that is a whole
    # word of its own.  ``[^\W\d_]`` is "a letter" in Unicode terms.
    rf"(?=$|\W|{_GLUED_UNIT}(?![^\W\d_]))"
)
_PERCENT_AFTER_RE = re.compile(r"\s*(?:%|percent\b|per\s+cent\b)", re.IGNORECASE)
# H0 in little-h units, in the notations a user or model actually writes:
# ``h = 0.732``, ``h ≈ .683``, ``h0 = 0.677``, ``H0/100 = 0.677``,
# ``little-h value of 0.677``.  Compared only against withheld H0.
# The value accepts the same numeric grammar as the ordinary tokenizer, not
# just "0.677"/".677": an equivalent "h = 6.77e-1" produced a plain 0.677
# token with no x100 conversion, so the withheld H0 was never matched (Codex
# review 2026-09-03).  The sci-notation rewrite runs before this scan, so the
# superscript form arrives here as "6.77e-1".
_LITTLE_H_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:H0\s*/\s*100|little[-\s]h(?:\s+value)?|h0|h)"
    r"\s*(?:[=≈:~]|is|of|at)\s*"
    r"((?:\d+\.\d+|\d+|\.\d+)(?:[eE][-+]?\d+)?)",
    re.IGNORECASE,
)
# A parameter label bound to the token keeps it a value claim whatever
# follows: ``H0 = 68%`` and ``the H0 median is 68%`` are never interval
# idioms.  The binding is a symbol OR a copula, because a model states a
# value both ways and the symbol-only form let "the H0 median is 68%, with
# the credible interval withheld" pass as coverage (review 2026-09-03).
# The subject a value can be assigned TO: a named parameter, or an unlabeled
# posterior statistic.  "The posterior median is 68%, with the credible
# interval withheld" names no parameter, so the named-only pattern let the
# later interval word exempt a withheld value that the sentence had just
# stated (Codex review 2026-09-03).  ``H_0`` is the conventional spelling and
# was missing from the parameter list for the same reason.
_ASSIGNMENT_SUBJECT = (
    r"(?:H0|H_0|H₀|omegam|omega_m|Omega_m|sigma8|S8|w0|wa|hubble"
    r"|(?:posterior\s+|marginal(?:ised|ized)?\s+|exploratory\s+|fitted\s+)?"
    # "result" and a bare "value" are the plainest way to state a number and
    # were missing, so "The result is 68%, with the credible interval
    # withheld" was exempted by the later cue (Codex review 2026-09-03).
    r"(?:median|mean|best[-\s]?fit|central\s+value|point\s+estimate"
    r"|result|value|figure))"
)
# An approximation word between the copula and the number is still an
# assignment.  The copula had to end right before the number, so "The
# exploratory median is approximately sixty-eight" carried no claim context
# and the spelled value was skipped (Codex review 2026-09-03,
# PRRT_kwDORoeoE86etS0V).  A count ("approximately sixty-eight samples")
# still has no subject in front of it and stays unparsed.
_APPROXIMATION = r"(?:(?:about|approximately|around|roughly|near|close\s+to|some)\s+)?"
# A determiner or an opening bracket/quote after the copula or symbol does
# not break the assignment: ``H0 = the 68% credible interval``, ``H0 is (68%
# credible interval withheld)`` and ``H0 为（68% …）`` state the value with one
# word or mark in front of it, and the guard used to require the number to
# follow the copula directly (round 17, R2; origin/main catches all of them).
# ``为`` is the copula of the Chinese notation a bilingual reply writes and
# takes no surrounding whitespace.  Two deliberate limits: the colon takes no
# determiner -- ``H0: the 68% credible interval is withheld`` is a label
# introducing a description, which the runner's F5 specificity tests keep
# honest, while ``H0: 68%`` and ``H0: (68%`` still bind -- and the
# prepositions ``of``/``at`` take none either, so ``H0 is withheld at the
# 68% confidence level`` stays the coverage wording it is.  The copular
# determiner binds only inside the label's own sub-clause; see
# ``_parameter_assignment_before``.
_ASSIGNED_DETERMINER = r"(?:(?:the|a|an|our|its|this)\s+)?"
_ASSIGNED_OPENER = r"[(\"'“「【（]?\s*"
_ASSIGNMENT_SYMBOL_RE = re.compile(r"[=~≈]")
_PARAMETER_ASSIGNMENT_BEFORE_RE = re.compile(
    rf"\b{_ASSIGNMENT_SUBJECT}\b"
    r"(?:[^\n;]{0,28}?"
    rf"(?:[=~≈]\s*{_ASSIGNED_DETERMINER}|:\s*)"
    r"|(?P<copula_gap>[^\n;]{0,28}?)"
    r"(?:(?:\b(?:is|was|are|were|equals?|sits\s+at|comes\s+out\s+at)\s+"
    rf"{_APPROXIMATION}|为\s*)(?P<determiner>{_ASSIGNED_DETERMINER})"
    rf"|\b(?:of|at)\s+{_APPROXIMATION}))"
    rf"{_ASSIGNED_OPENER}$",
    re.IGNORECASE,
)
# The assignment can also FOLLOW the token: "68% is the H0 median, with its
# credible interval withheld" states the value first, and a backward-only
# check let the later interval cue exempt it (Codex review 2026-09-03,
# PRRT_kwDORoeoE86eyq3R, filed on #68).  A reverse copula followed by an
# assignment subject binds the token as a value.
_PARAMETER_ASSIGNMENT_AFTER_RE = re.compile(
    r"^\s*(?:%|percent\b|per\s+cent\b)?\s*(?:is|was|are|were)\s+"
    rf"(?:(?:the|our|its|this|that)\s+)?{_ASSIGNMENT_SUBJECT}\b",
    re.IGNORECASE,
)
# The label can also follow the token as a POSTFIX: ``68% for H0, with the
# credible interval withheld`` binds the number to the parameter through the
# preposition directly after the percent sign, and the interval cue later in
# the clause exempted it (round 17, R1).  ``the 68% credible interval for H0``
# is not this shape -- its preposition follows "interval", not the percent
# sign -- and stays the signed coverage wording.
_PARAMETER_POSTFIX_LABEL_RE = re.compile(
    r"^\s*(?:%|percent\b|per\s+cent\b)?\s+(?:for|of|on)\s+"
    rf"(?:(?:the|a|an|our|its)\s+)?{_ASSIGNMENT_SUBJECT}\b",
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
# Finer than a clause: what separates one predicate from the next inside a
# sentence.  Used only to decide which words sit in a parameter label's own
# sub-clause.
_SUBCLAUSE_BREAK_RE = re.compile(
    r"[;!?\n,]|\.(?=\s|$)"
    r"|\b(?:and|but|while|whereas|although|though|however|yet)\b",
    re.IGNORECASE,
)
_DIGIT_RE = re.compile(r"\d")
# "Another number" for the interval-cue trim: a digit, or a spelled number
# word.  Only the words that can carry a coverage level are listed, so an
# ordinary "one" or "two" in prose does not cut a cue short.
# A digit that is part of a LABEL is not another number: the "0" in H0, the
# "8" in sigma8 and the "2" in DR2 truncated the cue window right before the
# token, so "The credible interval for H0 is 68%" lost its own cue (Codex
# review 2026-09-03).
# The spelled phrase continues: another number word, or a decimal "point".
_SPELLED_CONTINUES_RE = re.compile(
    r"[-\s]+(?:point\b|zero|oh|one|two|three|four|five|six|seven|eight|nine|"
    r"ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\b",
    re.IGNORECASE,
)
_OTHER_NUMBER_RE = re.compile(
    r"(?<![A-Za-z_])\d|\b(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"sixty[-\s]eight|ninety[-\s]five|ninety[-\s]nine)\b",
    re.IGNORECASE,
)
# The interval words glued to the PREVIOUS number's own percent sign.  Cutting
# the cue window at that number left "% credible interval is withheld, but "
# in front of the token, so "The 95% credible interval is withheld, but 68%
# for H0 is the exploratory result" borrowed the 95's cue for the 68 (Codex
# review 2026-09-03, PRRT_kwDORoeoE86etS0Y).
_ATTACHED_INTERVAL_WORDS_RE = re.compile(
    r"(?:\s+(?:credible|confidence|intervals?|coverage|containment|percentiles?"
    r"|quantiles?|C\.?L\.?))*",
    re.IGNORECASE,
)
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
        # The pasted evidence gets the same power-of-ten rewrite the reply
        # gets: "h = 6.77 × 10^-1" captured only 6.77 and recorded 677, so a
        # reply saying "H0 = 67.7" produced no echo hit (Codex review
        # 2026-09-03).
        text = _normalized_reply_with_map(text)[0]
        for match in _DIRECT_PARAMETER_RE.finditer(text):
            values.add(float(match.group("value")))
        # The pasted evidence may itself be in little-h units.  Only the
        # reply side was converted, so a user who pasted "h = 0.677" and a
        # model that answered "H0 = 67.7" produced no echo hit at all -- B5
        # bypassed by switching units between the turns (Codex review
        # 2026-09-03).
        for match in _LITTLE_H_RE.finditer(text):
            try:
                values.add(float(match.group(1)) * 100.0)
            except ValueError:
                continue
        for parameter in _STRUCTURED_PARAMETER_RE.finditer(text):
            for stat in _STRUCTURED_STAT_RE.finditer(parameter.group("body")):
                values.add(float(stat.group("value")))
    return {value for value in values if math.isfinite(value)}


# Markdown emphasis and code marks that flank a run the way CommonMark
# emphasis does: the opener is not glued to a letter, digit, sign or decimal
# point on its left, the closer is not glued to a letter or digit on its
# right, and the run has no space next to either mark.  ``H0 is **68%**
# credible interval withheld`` put two asterisks between the copula and the
# number so no guard saw an assignment, and ``_68%_`` did not tokenize at all
# (round 17, R3).  Identifiers (``sigma_8``, ``fig_68_a``) and arithmetic
# (``2*68*3``) have letters or digits against the mark and are left alone.
_MARKUP_MARK_RE = re.compile(
    r"(?<![A-Za-z0-9.+\-−])(\*\*|__|\*|_|`)(?=\S)([^\n]+?)(?<=\S)\1(?![A-Za-z0-9])"
)


def _strip_markup_marks(text: str) -> str:
    """Remove paired emphasis/code marks; nested marks peel off in turn."""
    while True:
        stripped = _MARKUP_MARK_RE.sub(r"\2", text)
        if stripped == text:
            return stripped
        text = stripped


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
    # Three widenings over the original "<word> point <word>+" grammar, each
    # from a measured escape (Codex review 2026-09-03):
    #   * a leading "negative"/"minus" is part of the number.  Without it
    #     "w0 is negative one point zero" produced +1.0 and a withheld -1.0
    #     was never matched.
    #   * a whole-number word with no "point" is a number too.  "The
    #     exploratory median is sixty-eight" produced no token at all.  Only
    #     forms that cannot be an ordinary count are accepted: a tens word
    #     (twenty..ninety), optionally with a unit, or ANY unit word once it
    #     carries an explicit sign.  A bare "two"/"ten" stays unparsed, so
    #     "two tools" and "ten iterations" are still not posterior values.
    #   * the percent flag is read after a spelled token as well, so "the
    #     sixty-eight point zero percent credible interval" is an interval
    #     idiom rather than a bare value.
    sign = r"(?:(?P<sign>negative|minus)\s+)?"
    tens = r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    # A spelled whole number is a value only where the sentence treats it as
    # one.  "sixty-eight samples were retained" is a diagnostic count, and
    # reading every tens phrase as a posterior falsely replaced an honest
    # reply (Codex review 2026-09-03).  A decimal form ("sixty-eight point
    # two") stays unconditional: nobody counts samples that way.
    spelled = re.compile(
        rf"\b{sign}(?P<number>"
        rf"{_NUMBER_WORD_TOKEN}(?:[-\s]{_NUMBER_WORD_TOKEN})?"
        rf"\s+point(?:\s+{_NUMBER_WORD_TOKEN})+"
        rf")\b",
        re.IGNORECASE,
    )
    spelled_whole = re.compile(
        rf"\b{sign}(?P<number>{tens}(?:[-\s]{_NUMBER_WORD_TOKEN})?)\b"
        rf"(?P<unit>\s*(?:%|percent\b|per\s+cent\b|km\s*/\s*s|km/s/Mpc"
        rf"|kilometres?|kilometers?))?",
        re.IGNORECASE,
    )
    signed_unit = re.compile(
        rf"\b(?P<sign>negative|minus)\s+(?P<number>{_NUMBER_WORD_TOKEN})\b",
        re.IGNORECASE,
    )
    for pattern in (spelled, spelled_whole, signed_unit):
        for match in pattern.finditer(text):
            value = _spelled_number_to_float(match.group("number"))
            if value is None or not math.isfinite(value):
                continue
            if pattern is spelled_whole:
                # Not a fragment of a longer spelled number: "seventy-three
                # point two" is one number, and a lookahead in the pattern
                # only made it backtrack to "seventy" (a spurious 70).
                if _SPELLED_CONTINUES_RE.match(text[match.end():]):
                    continue
            if pattern is spelled_whole and not match.group("sign"):
                # Claim context required: a unit or percent sign of its own,
                # or an assignment subject in front of it.
                before = text[max(0, match.start() - 48):match.start()]
                if not match.group("unit") and not _parameter_assignment_before(before):
                    continue
            if match.group("sign"):
                value = -value
            # `spelled_whole` swallows a trailing "percent" as its unit
            # group, so the flag has to be read from the match as well as
            # from what follows it: "The sixty-eight percent credible
            # interval is withheld" was recorded with is_percent=False and
            # lost the interval exemption (Codex review 2026-09-03).
            unit = match.groupdict().get("unit") or ""
            tokens.append(_Token(
                value,
                bmap[match.start()],
                bmap[match.end()],
                bool(_PERCENT_AFTER_RE.match(text[match.end():]))
                or bool(_PERCENT_AFTER_RE.match(unit)),
                False,
            ))
    for match in _LITTLE_H_RE.finditer(text):
        try:
            reduced = float(match.group(1))
        except ValueError:
            continue
        # Little h is a reduced value below 1.  Widening the literal grammar
        # to the full numeric form let "H0 is 68" match the `h0` alternative
        # and invent a 6800 token (Codex review 2026-09-03); the magnitude is
        # what distinguishes the reduced notation from the value itself.
        if not 0.0 < reduced < 1.0:
            continue
        value = reduced * 100.0
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

    Little-h tokens are scanned here, unlike in the withheld-posterior gate:
    ``h = 0.677`` carries the value 67.7 and restates a rejected H0 in the
    standard equivalent representation, so excluding it let the number cross
    turns unchanged (Codex review 2026-09-03).  Because the comparison is
    exact, including the token widens nothing it should not: the user has to
    have supplied that very number.
    """

    unsupported = _unsupported_untrusted_values(messages, tool_results)
    if not unsupported:
        return []
    hits = {
        token.value
        for token in _reply_number_spans(reply)
        if _echo_token_flagged(token.value, unsupported)
    }
    return sorted(hits)


def _unsupported_untrusted_values(messages: list[dict], tool_results: Any) -> set[float]:
    """Rejected user-supplied evidence values: untrusted minus independently
    reproduced.  Shared by the echo gate and by ``redact_gated_values`` so the
    two cannot drift apart."""
    untrusted = _untrusted_user_values(messages)
    if not untrusted:
        return set()
    supported = _claimable_current_values(tool_results)
    return {
        value
        for value in untrusted
        if not any(
            math.isclose(value, current, rel_tol=1e-12, abs_tol=1e-12)
            for current in supported
        )
    }


def _echo_token_flagged(token_value: float, unsupported: set[float]) -> bool:
    """The echo gate's per-token rule: exact match against a rejected value."""
    return any(
        math.isclose(token_value, value, rel_tol=1e-12, abs_tol=1e-12)
        for value in unsupported
    )


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


def _parameter_assignment_before(before: str) -> bool:
    """True when the text ending at a token binds it as a parameter VALUE.

    A copula only assigns to the parameter while the parameter is still the
    subject of the clause.  In ``For H0, the credible interval is 68%`` the
    interval noun has taken the subject over, so ``is`` assigns the coverage
    level to the interval rather than a value to H0, and blocking that
    sentence was a false kill (Codex review 2026-09-03).  The window is not
    widened and no threshold moves: the copular branch simply stops binding
    across an intervening interval subject.  An explicit symbol
    (``H0 = 67.7%``) still binds regardless of what sits between, and a
    non-interval noun still binds (``the H0 median is 68%``).
    """
    match = _PARAMETER_ASSIGNMENT_BEFORE_RE.search(before)
    # R2's determiner binds only while the label is still the subject of the
    # sub-clause the copula sits in.  The copular branch reaches over commas
    # and sentence periods -- that reach is unchanged, it is what catches
    # ``H0 is withheld, and so is 68% credible interval withheld`` -- but
    # with a determiner it read ``H0 is withheld, and so is the 68% credible
    # interval`` as an H0 value and killed an honest coverage-level reply
    # (round 17 verifier).  A label further along is read on its own, so
    # ``..., and Omega_m is the 68% credible interval`` still binds.
    while (
        match is not None
        and match.group("determiner")
        and _SUBCLAUSE_BREAK_RE.search(match.group("copula_gap"))
    ):
        match = _PARAMETER_ASSIGNMENT_BEFORE_RE.search(before, match.start() + 1)
    if match is None:
        return False
    gap = match.group("copula_gap")
    if gap is None:
        return True
    # An explicit symbol in the label's own sub-clause binds through whatever
    # follows it: ``H0 = a credible interval of 68%`` is the value dressed as
    # an interval, and the interval noun after the symbol must not switch
    # the copular branch off (round 17, R2).  A comma ends the sub-clause, so
    # ``Omega_m = 0.31, credible interval at 68%`` is not bound by that "=".
    if _ASSIGNMENT_SYMBOL_RE.search(_SUBCLAUSE_BREAK_RE.split(gap)[-1]):
        return True
    if _INTERVAL_WORDING_RE.search(gap):
        return False
    # The interval subject can also PRECEDE the parameter label: "The
    # credible interval for H0 is 68%" puts it before "H0", where the gap
    # cannot see it, and the assignment then read the coverage level as an
    # H0 value (Codex review 2026-09-03).  Only the words between the start
    # of the clause and the label are inspected, so "H0's credible interval
    # is 68%" is still covered by the gap check above.
    # Only the words in the label's OWN sub-clause count.  Splitting on
    # sentence punctuation alone let "The credible interval is withheld, but
    # the result is 68%" disable a clear value assignment (Codex review
    # 2026-09-03), so commas and coordinators end the head as well.
    head = _SUBCLAUSE_BREAK_RE.split(before[: match.start()])[-1]
    return not _INTERVAL_WORDING_RE.search(head)


def _strip_previous_number_idiom(clause: str) -> str:
    """Drop what still belongs to the previous number from the cue window.

    ``clause`` starts right after the previous number's last digit or tens
    word.  The rest of a spelled number ("-five"), that number's own percent
    sign, and the interval words attached to the percent describe THAT
    number, not the token whose cue is being looked for.
    """
    rest = clause
    while True:
        continued = _SPELLED_CONTINUES_RE.match(rest)
        if continued is None:
            break
        rest = rest[continued.end():]
    percent = _PERCENT_AFTER_RE.match(rest)
    if percent is None:
        return rest
    rest = rest[percent.end():]
    return rest[_ATTACHED_INTERVAL_WORDS_RE.match(rest).end():]


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
    if _parameter_assignment_before(before):
        return False
    after = text[token.end:token.end + 48]
    if _PARAMETER_ASSIGNMENT_AFTER_RE.match(after) or _PARAMETER_POSTFIX_LABEL_RE.match(after):
        return False
    before_clause = _CLAUSE_BREAK_RE.split(before)[-1]
    after_clause = _CLAUSE_BREAK_RE.split(after)[0]
    # The cue has to describe THIS percentage.  Another number between the
    # token and the cue means the cue belongs to that one instead: in "68% of
    # the reference, with a 95% credible interval" the interval is the 95's
    # (review 2026-09-03).  Trim each window at the nearest other digit.
    # A spelled number is an intervening number as well: without this,
    # "68% and a ninety-five percent credible interval" let the 95's cue
    # exempt the withheld 68 (Codex review 2026-09-03).  Reachable because
    # the tokenizer now recognises spelled numbers.
    other = _OTHER_NUMBER_RE.search(after_clause)
    if other is not None:
        after_clause = after_clause[:other.start()]
    previous = None
    for match in _OTHER_NUMBER_RE.finditer(before_clause):
        previous = match
    if previous is not None:
        before_clause = _strip_previous_number_idiom(before_clause[previous.end():])
    return bool(
        _INTERVAL_WORDING_RE.search(before_clause)
        or _INTERVAL_WORDING_RE.search(after_clause)
    )


def nonpublication_posterior_values(reply: str, tool_results: Any) -> list[float]:
    """Return non-publication posterior numbers that escaped into prose.

    Matching stays at ``rel_tol=0.01`` for every withheld statistic.  Two
    token classes escape that comparison, and only these two.  A little-h
    token (``h = 0.732``) is compared against the withheld H0 values alone,
    so it cannot collide with an unrelated statistic that happens to sit near
    the same number.  A percent token is skipped only when it reads as
    interval wording in its own clause (``the 68% credible interval``); a
    percent token carrying a parameter assignment (``H0 = 67.7%``), or
    standing anywhere else, is checked against the full withheld universe
    like any other number.  There is no percentage-keyed sub-universe and no
    exemption by token class.
    """

    withheld_all, withheld_h0 = _withheld_universes(tool_results)
    if not withheld_all:
        return []
    # Emphasis and code marks are invisible to every guard below: the
    # stripped text is what gets tokenized and inspected, so ``**68%**`` reads
    # exactly like ``68%`` (round 17, R3).
    text = _strip_markup_marks(str(reply or ""))
    hits = {
        token.value
        for token in _reply_number_spans(text)
        if _withheld_token_flagged(text, token, withheld_all, withheld_h0)
    }
    return sorted(hits)


def _withheld_universes(tool_results: Any) -> tuple[set[float], set[float]]:
    """``(every withheld statistic, the withheld H0 values)`` for this turn."""
    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    named: set[tuple[str, str, float]] = set()
    for entry in entries or []:
        _tool, result = _entry_tool_and_result(entry)
        if result:
            named.update(_withheld_entries(result))
    return (
        {value for _parameter, _stat, value in named},
        {value for parameter, _stat, value in named if _is_h0_name(parameter)},
    )


def _near_withheld(token_value: float, universe: set[float]) -> bool:
    return any(
        math.isclose(token_value, value, rel_tol=0.01, abs_tol=1e-12)
        for value in universe
    )


def _withheld_token_flagged(
    text: str,
    token: _Token,
    withheld_all: set[float],
    withheld_h0: set[float],
) -> bool:
    """The withheld-posterior gate's decision for ONE token at ONE position.

    Factored out so ``redact_gated_values`` can reuse the identical per-token
    decision instead of re-matching by value.  Matching by value discarded the
    position-dependent exemptions: a token the interval idiom exempts here was
    blanked anyway whenever the same number was a hit somewhere else in the
    reply, and a little-h token exempted against a non-H0 withheld statistic
    went the same way (adversarial review 2026-09-03).
    """
    if token.little_h:
        return _near_withheld(token.value, withheld_h0)
    if token.is_percent and _is_interval_idiom(text, token):
        return False
    return _near_withheld(token.value, withheld_all)


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


# A number the model binds to a NAMED cosmology parameter is a result claim.
# The two honesty gates cannot see one the model invented outright -- it
# echoes nothing the user pasted and matches no withheld statistic -- so a
# draft that wrote "H0 = 73.24" while the turn had only run
# `list_cosmology_datasets` streamed it unredacted (Codex review 2026-09-03).
# Only named parameters count here: the unlabelled subjects the assignment
# guard also accepts ("the median is 3") say nothing about what the number
# measures.
_NAMED_PARAMETER_ASSIGNMENT_BEFORE_RE = re.compile(
    # Every parameter `claim_validator` recognises, not a subset: an
    # intermediate draft writing "n_s = 1.2" left the whole claim visible
    # because n_s was missing here while the final validator blocks it
    # (Codex review 2026-09-03).
    r"\b(?P<parameter>H0|H_0|H₀|hubble|omegam|omega_m|Omega_m|Omega_b|omega_b"
    r"|Omega_k|omega_k|Omega_L|omega_lambda|sigma8|sigma_8|S8|S_8"
    r"|w0|w_0|wa|w_a|mnu|m_nu|sum\s*m_?nu|n_s|ns|A_s|tau|r_d|rd)\b"
    r"(?:[^\n;]{0,28}?[=:~≈]\s*"
    r"|(?P<copula_gap>[^\n;]{0,28}?)"
    # An approximation word binds a value as surely as "=" does, and the final
    # validator recognises these: "H0 about 73.24" and "H0 near 73.24"
    # streamed unredacted (Codex review 2026-09-03).
    r"\b(?:is|was|are|were|of|at|equals?|sits\s+at|comes\s+out\s+at"
    r"|about|around|near|approximately|roughly|circa|close\s+to)\s+)$",
    re.IGNORECASE,
)


_PARAMETER_ALIASES = {
    "h_0": "h0", "h₀": "h0", "hubble": "h0",
    "omega_m": "omegam", "omegam": "omegam",
    "omega_b": "omegab", "omega_k": "omegak", "omega_l": "omegalambda",
    "sigma_8": "sigma8", "s_8": "s8", "w_0": "w0", "w_a": "wa",
    "m_nu": "mnu", "sum m_nu": "mnu", "sum mnu": "mnu",
    "ns": "n_s", "a_s": "a_s", "r_d": "rd",
}


def _canonical_parameter(name: str) -> str:
    key = " ".join(str(name or "").strip().lower().split())
    return _PARAMETER_ALIASES.get(key, key)


def _assigned_parameter(text: str, token: _Token) -> str | None:
    """The named parameter this token is bound to, if any."""
    match = _NAMED_PARAMETER_ASSIGNMENT_BEFORE_RE.search(
        text[max(0, token.start - 48):token.start]
    )
    return _canonical_parameter(match.group("parameter")) if match else None


def _uncited_value_spans(source: str, uncited: list) -> set[tuple[int, int]]:
    """The spans to blank for the validator's uncited claims.

    A ``Claim`` span covers the whole phrase ("H0 = 67.5 km/s/Mpc"); only the
    number inside it is withheld, so the reader still sees WHAT was withheld.
    The number is the token inside the claim span whose value the claim
    carries (a little-h token carries value*100, so its literal is compared
    too).  If no such token can be located the whole claim span is blanked --
    failing closed rather than streaming a value the final gate refuses.
    """
    tokens = _reply_number_spans(source)
    spans: set[tuple[int, int]] = set()
    for claim in uncited:
        start, end, value = int(claim.start), int(claim.end), float(claim.value)
        inside = [t for t in tokens if t.start >= start and t.end <= end]
        matched = [
            t for t in inside
            if math.isclose(t.value, value, rel_tol=1e-9, abs_tol=1e-12)
            or (t.little_h and math.isclose(t.value / 100.0, value, rel_tol=1e-9, abs_tol=1e-12))
        ]
        if matched:
            spans.update((t.start, t.end) for t in matched)
        else:
            spans.add((start, end))
    return spans


def redact_gated_values(
    reply: str,
    messages: list[dict],
    tool_results: Any,
) -> tuple[str, int]:
    """Blank out only the numbers the two honesty gates would withhold.

    Returns ``(redacted_text, replacement_count)``.  A span is replaced with
    the literal ``[withheld]`` only when the token AT THAT POSITION is one the
    gates would have flagged, decided by the gates' own per-token rules
    (``_withheld_token_flagged`` and ``_echo_token_flagged``) rather than by
    comparing values against the gates' output.  Value matching over-redacted,
    because a hit is reported as a bare number and the exemptions are
    positional: ``the 68% credible interval`` was blanked as soon as 68 was a
    hit elsewhere in the same draft (adversarial review 2026-09-03).

    Precise residual — the withheld-posterior rule matches at ``rel_tol=0.01``,
    so an unrelated number that happens to land within 1% of a withheld
    statistic (an ESS, a readiness count, a requested redshift, an iteration
    budget) IS replaced.  That is the gate's own decision, not an extra margin
    taken here: the same number in the final turn makes the gate refuse the
    whole reply.  Redacting less than the gate flags would stream values the
    gate withholds, so the draft deliberately mirrors it.  Numbers outside that
    band — publication years, arXiv identifiers, dataset counts, a redshift or
    an iteration budget that collides with nothing — survive byte-for-byte.

    The echo rule is applied to every token, little-h tokens included: the gate
    itself only scans non-little-h tokens, but ``h = 0.7143`` restates a
    rejected 71.43 and must not stream.

    A third rule covers what neither gate can see: a claim the FINAL
    validator would refuse.  The draft asks ``claim_validator.validate_claims``
    directly -- the same call ``loop.py`` makes on the final reply -- and
    blanks the value of every uncited claim.  That is the whole of rule 3: no
    private label list, bridge list, tolerance or bucket lives here any more,
    so the draft boundary cannot be weaker than the final one (the review
    rounds of 2026-09-03 found five such gaps in the private grammar).  A
    number the validator does not treat as a claim -- a year, an arXiv id, a
    count -- is untouched; a requested redshift no tool echoed is a claim the
    final gate refuses, and is withheld here for the same reason.

    Used on everything the loop sends out through ``on_event`` (2026-09-02
    review H5, corrected 2026-09-03): the intermediate ``agent_text`` drafts,
    and the ``draft_preview`` / ``final_preview`` / ``details`` of every
    gate event.  Both channels used to carry the raw pre-gate text.

    Where that text ends up, verified rather than assumed: ``chat.py``'s
    ``audit_trail`` is a request-local list whose only consumer is
    ``_tool_results_from_stream_audit`` in the workflow-timeout fallback —
    it is NOT written to ``ChatSession.audit_log`` (that column now holds
    server-owned, HMAC-signed evidence records; see
    ``app/services/server_evidence.py``, and ``SaveSessionRequest.audit_log``
    is deliberately ignored).  The browser side is transient too: the UI's
    ``_thinking`` steps are dropped by ``chatStorage.serializeStored``.  The
    durable sinks are the gate-events JSONL and the blind runner's
    ``case_<id>.json``, which dumps the whole recorded event list to disk.
    So the leak was live-visible on the wire and durable in the blind-test
    artifact — which is why gate events are split: the local JSONL keeps the
    full draft for triage, the emitted copy is redacted.
    """

    source = str(reply or "")
    if not source:
        return source, 0
    unsupported = _unsupported_untrusted_values(messages, tool_results)
    withheld_all, withheld_h0 = _withheld_universes(tool_results)
    # Rule 3 IS the final gate.  The draft used to carry its own grammar of
    # "what is a parameter claim" -- a label list, a bridge list, per-parameter
    # buckets, a +/- rule -- and every review round found a spelling the final
    # validator knew and the draft did not (TeX \pm, H_{0}, an age in Gyr, a
    # 0.1% strict tolerance, an echoed model input).  Two grammars for one
    # boundary is a standing invitation to that drift, so the draft now asks
    # `claim_validator.validate_claims` -- the same call, with the same
    # defaults, that `loop.py` makes on the final reply -- and blanks the value
    # of every claim it reports uncited.  Whatever the final gate would refuse
    # the draft withholds, and nothing else.
    from app.services.claim_validator import validate_claims

    uncited_spans = _uncited_value_spans(source, validate_claims(source, tool_results).uncited)
    flagged: set[tuple[int, int]] = set(uncited_spans)
    for token in _reply_number_spans(source):
        if (
            _echo_token_flagged(token.value, unsupported)
            or _withheld_token_flagged(source, token, withheld_all, withheld_h0)
        ):
            flagged.add((token.start, token.end))
    spans = sorted(flagged)
    if not spans:
        return source, 0
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            continue
        merged.append([start, end])
    redacted = source
    for start, end in reversed(merged):
        redacted = f"{redacted[:start]}[withheld]{redacted[end:]}"
    return redacted, len(merged)
