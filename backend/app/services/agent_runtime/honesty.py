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
from typing import Any


_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?(?![A-Za-z0-9_.])"
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
    "posterior_summary",
    "derived_params",
    "pairwise_tensions",
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


def _reply_number_tokens(reply: str) -> list[float]:
    values: list[float] = []
    for match in _NUMBER_RE.finditer(str(reply or "")):
        try:
            value = float(match.group())
        except ValueError:
            continue
        if math.isfinite(value):
            values.append(value)
    return values


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


def nonpublication_posterior_values(reply: str, tool_results: Any) -> list[float]:
    """Return non-publication posterior numbers that escaped into prose."""

    def _withheld_values(node: Any, inherited_withhold: bool = False) -> Iterable[float]:
        if isinstance(node, Mapping):
            withhold = inherited_withhold or (
                node.get("publication_ready") is False
                or node.get("__do_not_claim__") is True
            )
            if withhold:
                for key in _POSTERIOR_KEYS:
                    if key in node:
                        yield from _finite_numbers(node[key])
            for nested in node.values():
                yield from _withheld_values(nested, withhold)
        elif isinstance(node, Sequence) and not isinstance(
            node, (str, bytes, bytearray)
        ):
            for nested in node:
                yield from _withheld_values(nested, inherited_withhold)

    entries = tool_results if isinstance(tool_results, list) else [tool_results]
    withheld: set[float] = set()
    for entry in entries or []:
        _tool, result = _entry_tool_and_result(entry)
        if result:
            withheld.update(_withheld_values(result))
    if not withheld:
        return []
    hits = {
        token
        for token in _reply_number_tokens(reply)
        if any(
            math.isclose(token, value, rel_tol=0.01, abs_tol=1e-12)
            for value in withheld
        )
    }
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
