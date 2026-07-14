"""T1-U10: the reproduce-anchor harness proves correctness, not just honesty.

For every genuinely reproducible anchor in the oracle table, run the real
in-process chain and assert it lands on the published value within tolerance.
Published posterior summaries remain literature context and must not be sent
through a sampler as if their posterior covariance were a likelihood.
"""
from __future__ import annotations

import pytest

from app.services import cosmology_oracle as co
from app.services.cosmology_oracle import PUBLISHED_ANCHORS, reproduce_anchor


def test_all_fast_oracle_anchors_reproduce_within_tol():
    failures = []
    for a in PUBLISHED_ANCHORS:
        if a.independence == "consistency":
            continue
        out = reproduce_anchor(a)
        if not out["within_tol"]:
            failures.append((a.goal_key, out["reproduced_value"], a.value, a.tol))
    assert not failures, f"anchors not reproduced within tolerance: {failures}"


def test_literature_posterior_anchor_is_not_run_as_a_likelihood():
    out = reproduce_anchor(co.get_anchor("pantheon_plus_omegam"))

    assert out["reproduction_attempted"] is False
    assert out["reproduction_status"] == "literature_context_not_executed"
    assert out["anchor_scope"] == "literature_context"
    assert out["reproduced_value"] is None
    assert out["within_tol"] is None
    assert out["publication_ready"] is False


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
