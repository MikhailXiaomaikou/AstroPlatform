"""Tests for audit_published_constraint (cosmology paper-vs-reproduction audit).

Pure functions (tension / classification / name normalisation) are tested
exactly. The audit comparison logic is tested with a monkeypatched
run_likelihood_chain so the tests are deterministic and need no real data or
network — reproduction itself is exercised by the existing likelihood tests.
"""
from __future__ import annotations

import math

import pytest

from app.services.cosmology_audit import (
    _canonical_param,
    audit_published_constraint,
    classify_tension,
    tension_sigma,
)


# ---- pure functions ----

def test_tension_sigma_basic():
    # SH0ES H0=73.04±1.04 vs reproduced 67.4±0.5 → ~4.9σ
    ns = tension_sigma(73.04, 1.04, 67.4, 0.5)
    assert ns == pytest.approx(abs(73.04 - 67.4) / math.sqrt(1.04**2 + 0.5**2), rel=1e-9)
    assert 4.5 < ns < 5.5


def test_tension_sigma_zero_denominator():
    assert tension_sigma(70.0, 0.0, 70.0, 0.0) == 0.0
    assert tension_sigma(73.0, 0.0, 67.0, 0.0) == float("inf")


def test_classify_tension_bands():
    assert classify_tension(0.5) == "CONSISTENT"
    assert classify_tension(2.0) == "MILD_TENSION"
    assert classify_tension(4.0) == "STRONG_TENSION"
    assert classify_tension(float("inf")) == "STRONG_TENSION"


def test_canonical_param_aliases():
    assert _canonical_param("H0") == "H0"
    assert _canonical_param("Om0") == "omegam"
    assert _canonical_param("Omega_m") == "omegam"
    assert _canonical_param("omegam") == "omegam"
    assert _canonical_param("w") == "w0"
    assert _canonical_param("w0") == "w0"
    assert _canonical_param("S8") == "S8"
    assert _canonical_param("sigma8") == "sigma8"


# ---- audit comparison logic ----

def _mock_chain(parameters, publication_ready=True):
    def _fn(*, model, dataset_keys, random_seed=None, allow_emcee_fallback=False):
        return {
            "success": True,
            "parameters": parameters,
            "publication_ready": publication_ready,
            "citations": [],
        }
    return _fn


def test_audit_blocked_when_no_runnable_dataset():
    r = audit_published_constraint(
        model="lcdm",
        dataset_keys=["totally_made_up_key"],
        claimed={"H0": [73.04, 1.04]},
        paper_ref={"source": "test", "arxiv": "0000.00000"},
    )
    assert r["analysis_status"] == "BLOCKED"
    assert r["__tool_status__"] == "BLOCKED"
    assert r["publication_ready"] is False
    assert r["audit_report"]["comparisons"] == []
    assert any("made_up" in u["key"] for u in r["audit_report"]["datasets_unavailable"])


def test_audit_strong_tension(monkeypatch):
    monkeypatch.setattr(
        "app.services.cosmology_likelihoods.run_likelihood_chain",
        _mock_chain({"H0": {"median": 67.4, "std": 0.5}}),
    )
    r = audit_published_constraint(
        model="lcdm",
        dataset_keys=["pantheon_plus"],
        claimed={"H0": [73.04, 1.04]},
        paper_ref={"source": "SH0ES", "arxiv": "2112.04510"},
    )
    assert r["analysis_status"] == "AUDIT_READY"
    comps = r["audit_report"]["comparisons"]
    assert len(comps) == 1
    assert comps[0]["verdict"] == "STRONG_TENSION"
    assert comps[0]["tension_sigma"] > 3
    assert any(c.get("role") == "audited_paper" for c in r["citations"])
    # anti-fabrication guard reaches the model
    assert "fabricat" in r["__message_to_model__"].lower()


def test_audit_consistent(monkeypatch):
    monkeypatch.setattr(
        "app.services.cosmology_likelihoods.run_likelihood_chain",
        _mock_chain({"H0": {"median": 67.4, "std": 0.5}}),
    )
    r = audit_published_constraint(
        model="lcdm",
        dataset_keys=["pantheon_plus"],
        claimed={"H0": [67.5, 0.6]},
    )
    assert r["audit_report"]["comparisons"][0]["verdict"] == "CONSISTENT"
    assert r["__tool_status__"] == "COMPLETED"


def test_audit_not_reproduced_param(monkeypatch):
    monkeypatch.setattr(
        "app.services.cosmology_likelihoods.run_likelihood_chain",
        _mock_chain({"H0": {"median": 67.4, "std": 0.5}}),
    )
    r = audit_published_constraint(
        model="lcdm",
        dataset_keys=["pantheon_plus"],
        claimed={"sigma8": [0.81, 0.01]},  # not in the reproduction
    )
    comps = r["audit_report"]["comparisons"]
    assert comps[0]["verdict"] == "NOT_REPRODUCED"
    assert r["__tool_status__"] == "BLOCKED"  # nothing reproduced


def test_audit_param_name_normalization(monkeypatch):
    # claimed uses "Om0", reproduction uses "omegam" → must still match
    monkeypatch.setattr(
        "app.services.cosmology_likelihoods.run_likelihood_chain",
        _mock_chain({"omegam": {"median": 0.315, "std": 0.007}}),
    )
    r = audit_published_constraint(
        model="lcdm",
        dataset_keys=["pantheon_plus"],
        claimed={"Om0": [0.298, 0.008]},
    )
    comps = r["audit_report"]["comparisons"]
    assert comps[0]["canonical"] == "omegam"
    assert comps[0]["verdict"] in {"CONSISTENT", "MILD_TENSION", "STRONG_TENSION"}


def test_audit_overlapping_datasets_flagged(monkeypatch):
    monkeypatch.setattr(
        "app.services.cosmology_likelihoods.run_likelihood_chain",
        _mock_chain({"H0": {"median": 73.0, "std": 1.0}}),
    )
    r = audit_published_constraint(
        model="lcdm",
        dataset_keys=["pantheon_plus"],
        claimed={"H0": [73.04, 1.04]},
        claimed_datasets=["pantheon_plus"],  # overlaps the reproduction
    )
    assert r["audit_report"]["independence"] == "overlapping_data"
    assert r["audit_report"]["comparisons"][0]["tension_sigma"] is None
    assert r["audit_report"]["comparisons"][0]["verdict"] == "OVERLAPPING_DATA_NOT_COMPARABLE"
    assert any("overlap" in w.lower() for w in r["warnings"])


def test_audit_detects_pantheon_shoes_shared_calibration(monkeypatch):
    monkeypatch.setattr(
        "app.services.cosmology_likelihoods.run_likelihood_chain",
        _mock_chain({"H0": {"median": 73.0, "std": 1.0}}),
    )
    r = audit_published_constraint(
        model="lcdm",
        dataset_keys=["pantheon_plus"],
        claimed={"H0": [73.04, 1.04]},
        claimed_datasets=["shoes_h0_riess22"],
    )

    assert r["audit_report"]["independence"] == "overlapping_data"
    comparison = r["audit_report"]["comparisons"][0]
    assert comparison["tension_sigma"] is None
    assert comparison["verdict"] == "OVERLAPPING_DATA_NOT_COMPARABLE"


def test_audit_runs_external_status_dataset_when_execution_mode_is_available(monkeypatch):
    monkeypatch.setattr(
        "app.services.cosmology_likelihoods.run_likelihood_chain",
        _mock_chain({"omegam": {"median": 0.3, "std": 0.02}}),
    )
    r = audit_published_constraint(
        model="lcdm",
        dataset_keys=["desi_dr1_bao"],
        claimed={"omegam": [0.3, 0.02]},
    )

    assert r["analysis_status"] == "AUDIT_READY"
    assert r["audit_report"]["datasets_run"] == ["desi_dr1_bao"]
