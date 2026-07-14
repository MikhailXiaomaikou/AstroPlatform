"""Parametrized loader for the red-team numeric-claim corpus.

The corpus lives at ``backend/tests/_red_team_cases/numeric_claims.yaml``;
each case feeds (reply, tool_results) into ``validate_claims`` and asserts
``expect_ok``. Adding a case is editing one YAML entry — no Python
boilerplate per case.

Why a corpus rather than more inline test functions: the same
``validate_claims`` invariants matter across all active modules
(cosmology / solar_system / exoplanet) and across future modules. A
single corpus keeps each module's red-team cases discoverable, parses in
a single test, and prevents the "one-off case buried in a 1100-line test
file" problem that made the contract drift in earlier iterations.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

_CORPUS_PATH = pathlib.Path(__file__).parent / "_red_team_cases" / "numeric_claims.yaml"


def _load_cases() -> list[dict]:
    with _CORPUS_PATH.open() as fp:
        data = yaml.safe_load(fp)
    return list(data.get("cases") or [])


def _case_id(case: dict) -> str:
    return str(case.get("name") or "<unnamed>")


@pytest.mark.parametrize("case", _load_cases(), ids=_case_id)
def test_numeric_claim_corpus_case(case: dict) -> None:
    """For each case, ``validate_claims(reply, tool_results).ok`` must
    equal the case's declared ``expect_ok``. Failure messages include the
    case description + which claims (if any) fell outside the universe."""
    from app.services.claim_validator import validate_claims

    reply = str(case["reply"])
    tool_results = case.get("tool_results") or []
    expect_ok = bool(case["expect_ok"])
    description = str(case.get("description") or "")

    result = validate_claims(reply, tool_results)
    actual_ok = bool(result.ok)

    if actual_ok != expect_ok:
        uncited_repr = ", ".join(
            f"{c.label}={c.value!r}" for c in (result.uncited or [])
        )
        universe_repr = ", ".join(str(v) for v in (result.universe_sample or [])[:10])
        pytest.fail(
            f"red-team case {_case_id(case)!r} mismatch:\n"
            f"  description       : {description}\n"
            f"  expect_ok         : {expect_ok}\n"
            f"  actual ok         : {actual_ok}\n"
            f"  uncited claims    : [{uncited_repr}]\n"
            f"  universe (first 10): [{universe_repr}]\n"
            f"  strict_mode       : {result.strict_mode}"
        )


def test_corpus_is_not_empty() -> None:
    """A regression where the YAML file became empty / unparseable
    would silently 'pass' the parametrized test above (pytest reports
    zero collected). This explicit floor (>= 10 cases) prevents that."""
    cases = _load_cases()
    assert len(cases) >= 10, (
        f"red-team corpus has only {len(cases)} cases — expected >= 10. "
        "If you're removing cases, replace them with stronger ones."
    )
