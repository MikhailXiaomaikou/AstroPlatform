"""T1-U10: the reproduce-anchor harness proves correctness, not just honesty.

For every published anchor in the oracle table, run the real in-process chain and
assert it lands on the published value within tolerance.  All currently-tabled
anchors are compressed/diagonal/BAO and therefore fast; a full-cov-only anchor
would be marked slow opt-in (none today).
"""
from __future__ import annotations

import pytest

from app.services import cosmology_oracle as co
from app.services.cosmology_oracle import PUBLISHED_ANCHORS, reproduce_anchor


def test_all_fast_oracle_anchors_reproduce_within_tol():
    failures = []
    for a in PUBLISHED_ANCHORS:
        out = reproduce_anchor(a)
        if not out["within_tol"]:
            failures.append((a.goal_key, out["reproduced_value"], a.value, a.tol))
    assert not failures, f"anchors not reproduced within tolerance: {failures}"


def test_reproduce_anchor_result_shape():
    out = reproduce_anchor(co.get_anchor("desi_dr1_bao_omegam"))
    for k in ("goal_key", "parameter", "published_value", "tol", "reproduced_value", "within_tol"):
        assert k in out
    assert out["goal_key"] == "desi_dr1_bao_omegam"
    assert isinstance(out["reproduced_value"], float)


def test_fit_quality_anchors_reproduce_chi2_dof():
    # The harness genuinely computes reduced χ² for fit-quality anchors and checks
    # the good-fit band.  Pin the ACTUAL values tightly too (in addition to the
    # wide oracle band) so a real regression — e.g. doubling CC χ²/dof — is caught,
    # which the [0.4,1.6]/[0.3,1.7] acceptance band alone would not catch.
    expected = {"cc_fit_quality": 0.50, "eboss_fit_quality": 1.32}
    for key, exp in expected.items():
        out = reproduce_anchor(co.get_anchor(key))
        assert out["within_tol"] is True, (key, out["reproduced_value"])
        assert out["reproduced_value"] == pytest.approx(exp, abs=0.1), key


def test_reproduce_anchor_flags_a_wrong_published_value():
    # Sanity: a deliberately-wrong value with a tight tolerance is NOT reproduced.
    a = co.get_anchor("desi_dr1_bao_omegam")
    bad = co.OracleAnchor(
        a.goal_key, a.parameter, 0.10, 0.001, a.datasets, a.model,
        a.independence, a.source_arxiv, a.source_label,
    )
    assert reproduce_anchor(bad)["within_tol"] is False
