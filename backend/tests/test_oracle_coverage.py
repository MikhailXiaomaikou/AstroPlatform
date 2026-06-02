"""T1-U11: oracle-coverage measurement — the number that gates off-anchor autonomy.

Measurement only: it reports what fraction of the goal universe is backed by a
reproduced published anchor, and lists the uncovered (off-anchor) goals honestly.
It authorizes nothing.
"""
from __future__ import annotations

from app.services.cosmology_oracle import (
    PUBLISHED_ANCHORS,
    is_covered,
    oracle_coverage,
)


def test_coverage_reports_fraction_and_uncovered():
    cov = oracle_coverage()
    assert 0.0 <= cov["coverage_fraction"] <= 1.0
    assert cov["n_covered"] == len(PUBLISHED_ANCHORS)
    assert cov["n_goals"] == cov["n_covered"] + len(cov["uncovered_goals"])
    assert cov["uncovered_goals"], "uncovered goals must be listed, not hidden"
    # off-anchor extended-model goals must show up as uncovered
    assert "omega_k_curvature" in cov["uncovered_goals"]
    assert "w0_dark_energy_eos" in cov["uncovered_goals"]


def test_coverage_fraction_is_pinned():
    # 7 anchored / (7 + 4 off-anchor) — pin it so adding an anchor is a visible,
    # reviewed change to coverage rather than a silent drift.
    assert oracle_coverage()["coverage_fraction"] == round(7 / 11, 4)


def test_is_covered_distinguishes_anchored_from_off_anchor():
    assert is_covered("desi_dr1_bao_omegam") is True
    assert is_covered("omega_k_curvature") is False
    assert is_covered("totally_made_up_goal") is False


# ── T1-U13: coverage reports GENUINE (independent) reproductions separately ──

def test_coverage_reports_independent_reproductions():
    cov = oracle_coverage()
    for k in ("n_independent", "independent_fraction", "independent_goals"):
        assert k in cov, k
    n_indep = sum(1 for a in PUBLISHED_ANCHORS if a.independence == "independent")
    assert cov["n_independent"] == n_indep
    assert cov["independent_fraction"] == round(n_indep / cov["n_goals"], 4)
    # the genuine DESI reproduction is in the independent set; a compressed
    # consistency check is not.
    assert "desi_dr1_bao_omegam" in cov["independent_goals"]
    assert "pantheon_plus_omegam" not in cov["independent_goals"]
