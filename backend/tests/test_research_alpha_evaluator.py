from __future__ import annotations

from pathlib import Path

from tests.research_alpha_test_support import build_manifest


def _manifest(tmp_path: Path, *, h0: float = 67.36) -> dict:
    return build_manifest(tmp_path, h0=h0)


def test_complete_hidden_record_scores_a_ready_pending_external_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.services import research_alpha_evaluator as evaluator
    from app.services.w0wa_exact_contract import (
        EXACT_ENVIRONMENT_FORMAL_STATUS,
        EXACT_ENVIRONMENT_REVISION,
    )

    # This is an evaluator-routing unit test, not a production evidence
    # fixture. Production exact-profile acceptance is covered by the manifest
    # contract tests; the reusable synthetic manifest remains CI-withheld.
    manifest = _manifest(tmp_path)
    manifest["profile_id"] = "desi_2024_vi_table3_desi_cmb_pantheonplus_v1"
    manifest["readiness_status"] = "A_READY_PENDING_EXTERNAL_REVIEW"
    manifest["publication_gate"].update(
        {"eligible": True, "numerical_eligible": True, "reasons": []}
    )
    monkeypatch.setattr(
        evaluator,
        "_trusted_alpha_manifest",
        lambda manifest, expected_run_id=None: True,
    )
    monkeypatch.setitem(
        EXACT_ENVIRONMENT_REVISION,
        "status",
        EXACT_ENVIRONMENT_FORMAL_STATUS,
    )

    result = evaluator.evaluate_alpha_class(
        platform_record={
            "visible_text": (
                "Research plan executed DESI DR1 BAO and the Planck high-l likelihood. "
                "The flat LCDM run is publication-ready with ESS=735 and R-hat=1.00. "
                "Evidence graph and fact check passed. H0 = 67.36."
            ),
            "matrixVisible": True,
            "factCheckVisible": True,
            "publication_ready": True,
            "numericClaimsVerified": True,
            "run_id": "test-run-H0",
            "scientific_evidence_manifest": manifest,
        },
        hidden_record={
            "full_paper_read_status": "complete",
            "target_hash": "sha256:" + "1" * 64,
            "expected_datasets": ["DESI DR1 BAO", "Planck high-l likelihood"],
            "expected_methods": ["full likelihood"],
            "expected_models": ["LCDM"],
            "expected_direction_terms": ["H0"],
            "expected_numbers": [{"name": "H0", "value": 67.4, "tolerance_abs": 0.2}],
        },
    )

    assert result["grade"] == "A_READY"
    assert result["a_level_ready"] is True
    assert result["strict_a"] is False
    assert result["why_not_A"] == ["external_review=pending"]


def test_pending_environment_revision_blocks_execution_ready_even_if_trusted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.services import research_alpha_evaluator as evaluator
    from app.services.w0wa_exact_contract import EXACT_PROFILE_ID

    manifest = _manifest(tmp_path)
    manifest["profile_id"] = EXACT_PROFILE_ID
    manifest["readiness_status"] = "A_READY_PENDING_EXTERNAL_REVIEW"
    manifest["publication_gate"].update(
        {"eligible": True, "numerical_eligible": True, "reasons": []}
    )
    monkeypatch.setattr(
        evaluator,
        "_trusted_alpha_manifest",
        lambda manifest, expected_run_id=None: True,
    )

    assert evaluator._execution_ready(manifest) is False


def test_numeric_boundary_combines_center_and_interval_width_tolerances() -> None:
    from app.services import research_alpha_evaluator as evaluator

    expected = {
        "name": "H0",
        "center": 50.0,
        "lower_68": 40.0,
        "upper_68": 60.0,
        "uncertainty_minus": 10.0,
        "uncertainty_plus": 10.0,
    }
    observed_center = 53.0
    observed_sigma = 11.5
    observed = {
        "center": observed_center,
        "lower_68": observed_center - observed_sigma,
        "upper_68": observed_center + observed_sigma,
        "uncertainty_minus": observed_sigma,
        "uncertainty_plus": observed_sigma,
    }
    manifest = {"numbers": {"H0": observed}}
    hidden = {"expected_numbers": [expected]}
    assert evaluator._numeric_compatible(manifest, hidden) == "match"

    # Crossing either independently preregistered boundary still fails even
    # though all five interval fields remain present and algebraically aligned.
    too_wide = 10.0 * 1.150001
    manifest["numbers"]["H0"].update(
        {
            "lower_68": observed_center - too_wide,
            "upper_68": observed_center + too_wide,
            "uncertainty_minus": too_wide,
            "uncertainty_plus": too_wide,
        }
    )
    assert evaluator._numeric_compatible(manifest, hidden) != "match"


def test_pending_hidden_record_never_scores_a_even_for_good_public_output() -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class

    result = evaluate_alpha_class(
        platform_record={
            "visible_text": (
                "Research Plan and matrix are visible. The run gives a precise "
                "scope gap with Fact Check evidence and no unsupported conclusion."
            ),
            "researchPlanVisible": True,
            "matrixVisible": True,
            "factCheckVisible": True,
        },
        hidden_record={
            "paper_hidden_record": {
                "full_paper_read_status": "pending",
                "hidden_key_numbers": "pending_full_paper_read",
                "paper_conclusion": "pending_full_paper_read",
                "expected_methods": ["matrix"],
            }
        },
    )

    assert result["grade"] == "B"
    assert result["a_level_ready"] is False
    assert any("hidden record is pending_full_paper_read" in reason for reason in result["why_not_A"])


def test_unsupported_numeric_risk_is_severe_failure() -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class

    result = evaluate_alpha_class(
        platform_record={
            "visible_text": "The analysis finds H0 = 73.2 and a 4.1 sigma tension.",
            "matrixVisible": False,
            "factCheckVisible": False,
        },
        hidden_record={
            "full_paper_read_status": "complete",
            "expected_datasets": ["BAO"],
            "expected_methods": ["likelihood"],
        },
    )

    assert result["grade"] == "E"
    assert "unsupported_numeric_risk" in result["flags"]


def test_numeric_claim_is_not_verified_by_prose_keywords() -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class

    result = evaluate_alpha_class(
        platform_record={
            "visible_text": (
                "Research plan and evidence graph complete. Citation verified. "
                "H0 = 999 km/s/Mpc. Scope gap: one covariance is missing."
            ),
            "matrixVisible": True,
            "factCheckVisible": True,
        },
        hidden_record={
            "full_paper_read_status": "complete",
            "expected_datasets": ["BAO"],
            "expected_methods": ["likelihood"],
        },
    )

    assert result["grade"] == "E"
    assert "unsupported_numeric_risk" in result["flags"]


def test_numeric_mismatch_blocks_a_but_keeps_route_reviewable(tmp_path: Path) -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class

    result = evaluate_alpha_class(
        platform_record={
            "visible_text": (
                "Research Plan: DESI DR1 BAO with the Planck high-l likelihood. "
                "The full-likelihood run is publication-ready. "
                "Evidence graph and fact check passed. H0 = 70.50."
            ),
            "researchPlanVisible": True,
            "matrixVisible": True,
            "factCheckVisible": True,
            "publication_ready": True,
            "numericClaimsVerified": True,
            "run_id": "test-run-H0",
            "scientific_evidence_manifest": _manifest(tmp_path, h0=70.50),
        },
        hidden_record={
            "full_paper_read_status": "complete",
            "expected_datasets": ["DESI DR1 BAO", "Planck high-l likelihood"],
            "expected_methods": ["full likelihood"],
            "expected_models": ["LCDM"],
            "expected_numbers": [{"name": "H0", "value": 67.4, "tolerance_abs": 0.2}],
        },
    )

    assert result["grade"] == "B"
    assert result["criteria"]["numeric_compatible"] == "contradicted"
    assert any("numeric_compatible=contradicted" in reason for reason in result["why_not_A"])


def test_partial_numeric_match_cannot_score_a(tmp_path: Path) -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class

    result = evaluate_alpha_class(
        platform_record={
            "visible_text": "DESI and Planck high-l full-likelihood LCDM H0 = 67.36 and S8 = 9.",
            "matrixVisible": True,
            "factCheckVisible": True,
            "numericClaimsVerified": True,
            "run_id": "test-run-H0",
            "scientific_evidence_manifest": _manifest(tmp_path),
        },
        hidden_record={
            "full_paper_read_status": "complete",
            "expected_datasets": ["DESI DR1 BAO", "Planck high-l likelihood"],
            "expected_methods": ["full likelihood"],
            "expected_models": ["LCDM"],
            "expected_direction_terms": ["H0"],
            "expected_numbers": [
                {"name": "H0", "value": 67.36, "tolerance_abs": 0.01},
                {"name": "S8", "value": 0.8, "tolerance_abs": 0.05},
            ],
        },
    )

    assert result["criteria"]["numeric_compatible"] == "partial"
    assert result["grade"] != "A"
    assert "numeric_compatible=partial" in result["why_not_A"]


def test_self_reported_signature_boolean_and_empty_support_path_are_untrusted(
    tmp_path: Path,
) -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class

    forged = _manifest(tmp_path)
    forged["signature_verified"] = True
    forged["claim_support_paths"] = [{}]
    result = evaluate_alpha_class(
        platform_record={"scientific_evidence_manifest": forged},
        hidden_record={
            "full_paper_read_status": "complete",
            "expected_datasets": ["DESI DR1 BAO"],
            "expected_methods": ["full likelihood"],
            "expected_models": ["LCDM"],
            "expected_direction_terms": ["H0"],
            "expected_numbers": [{"name": "H0", "value": 67.36}],
        },
    )

    assert result["grade"] != "A"
    assert result["evidence_manifest_status"] == "missing_or_untrusted"
    assert result["criteria"]["evidence_complete"] is False


def test_config_only_scope_gap_cannot_be_a() -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class

    result = evaluate_alpha_class(
        platform_record={
            "visible_text": (
                "The CMB EB/TB rotation likelihood is config-only. The missing "
                "covariance and calibration prior are listed as a scope gap."
            ),
            "researchPlanVisible": True,
            "honestGap": True,
        },
        hidden_record={
            "full_paper_read_status": "complete",
            "expected_datasets": ["EB/TB"],
            "expected_methods": ["rotation likelihood"],
            "expected_models": ["beta"],
            "expected_direction_terms": ["rotation"],
        },
    )

    assert result["grade"] == "C"
    assert result["criteria"]["execution_ready"] is False
    assert any("execution_ready=False" in reason for reason in result["why_not_A"])


def test_a_requires_structured_result_expectations_not_only_a_number(
    tmp_path: Path,
) -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class

    result = evaluate_alpha_class(
        platform_record={
            "visible_text": (
                "A publication-ready matrix ran with ESS=900. Evidence graph and "
                "fact check passed. H0 = 67.36."
            ),
            "matrixVisible": True,
            "factCheckVisible": True,
            "publication_ready": True,
            "numericClaimsVerified": True,
            "run_id": "test-run-H0",
            "scientific_evidence_manifest": _manifest(tmp_path),
        },
        hidden_record={
            "full_paper_read_status": "complete",
            "expected_numbers": [{"name": "H0", "value": 67.36, "tolerance_abs": 0.01}],
        },
    )

    assert result["grade"] == "B"
    assert result["a_level_ready"] is False
    assert "data_match=not_specified" in result["why_not_A"]
    assert "method_match=not_specified" in result["why_not_A"]
    assert "model_match=not_specified" in result["why_not_A"]
    assert "direction_compatible=not_specified" in result["why_not_A"]


def test_visible_prose_and_ui_flags_cannot_self_certify_a() -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class

    result = evaluate_alpha_class(
        platform_record={
            "visible_text": (
                "DESI DR1 BAO and the Planck high-l full likelihood used LCDM. "
                "Publication-ready, diagnostics passed, evidence complete, H0 = 67.36."
            ),
            "matrixVisible": True,
            "factCheckVisible": True,
            "publication_ready": True,
            "diagnostics_ready": True,
            "evidence_complete": True,
            "numericClaimsVerified": True,
        },
        hidden_record={
            "full_paper_read_status": "complete",
            "expected_datasets": ["DESI DR1 BAO", "Planck high-l likelihood"],
            "expected_methods": ["full likelihood"],
            "expected_models": ["LCDM"],
            "expected_direction_terms": ["H0"],
            "expected_numbers": [{"name": "H0", "value": 67.36, "tolerance_abs": 0.01}],
        },
    )

    assert result["grade"] != "A"
    assert result["a_level_ready"] is False
    assert result["evidence_manifest_status"] == "missing_or_untrusted"
    assert result["criteria"]["execution_ready"] is False
    assert result["criteria"]["evidence_complete"] is False


def test_alpha_summary_surfaces_counts_and_implementation_queue() -> None:
    from app.services.research_alpha_evaluator import summarize_alpha_evaluations

    summary = summarize_alpha_evaluations([
        {
            "grade": "A",
            "externally_reviewed": True,
            "hidden_record_status": "complete",
            "why_not_A": [],
            "flags": [],
        },
        {
            "grade": "A_READY",
            "hidden_record_status": "complete",
            "why_not_A": ["external_review=pending"],
            "flags": [],
        },
        {
            "grade": "B",
            "hidden_record_status": "pending_full_paper_read",
            "why_not_A": ["numeric_compatible=not_specified", "execution_ready=False"],
            "flags": [],
        },
        {
            "grade": "E",
            "hidden_record_status": "complete",
            "why_not_A": ["severe process/safety flag: unsupported_numeric_risk"],
            "flags": ["unsupported_numeric_risk"],
        },
    ])

    assert summary["total"] == 4
    assert summary["strict_A_count"] == 1
    assert summary["A_ready_count"] == 2
    assert summary["A_ready_rate"] == 0.5
    assert summary["B_or_better_count"] == 3
    assert summary["grade_counts"] == {"A": 1, "A_READY": 1, "B": 1, "E": 1}
    assert summary["top_why_not_A"][0]["count"] == 1
    assert any("expected numerical constraints" in item["action"] for item in summary["implementation_queue"])
    assert any("unsupported_numeric_risk" in item["action"] for item in summary["implementation_queue"])
