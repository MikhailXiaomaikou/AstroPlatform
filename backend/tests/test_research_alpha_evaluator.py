from __future__ import annotations


def test_complete_hidden_record_can_score_a_when_evidence_matches() -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class

    result = evaluate_alpha_class(
        platform_record={
            "visible_text": (
                "Research plan executed DESI DR1 BAO and Planck compressed. "
                "The flat LCDM run is publication-ready with ESS=735 and R-hat=1.00. "
                "Evidence graph and fact check passed. H0 = 67.36."
            ),
            "matrixVisible": True,
            "factCheckVisible": True,
            "publication_ready": True,
        },
        hidden_record={
            "full_paper_read_status": "complete",
            "expected_datasets": ["DESI DR1 BAO", "Planck compressed"],
            "expected_methods": ["compressed"],
            "expected_models": ["LCDM"],
            "expected_direction_terms": ["H0"],
            "expected_numbers": [{"name": "H0", "value": 67.4, "tolerance_abs": 0.2}],
        },
    )

    assert result["grade"] == "A"
    assert result["a_level_ready"] is True
    assert result["why_not_A"] == []


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


def test_numeric_mismatch_blocks_a_but_keeps_route_reviewable() -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class

    result = evaluate_alpha_class(
        platform_record={
            "visible_text": (
                "Research Plan: DESI DR1 BAO with Planck compressed. "
                "The compressed-likelihood preliminary run is publication-ready. "
                "Evidence graph and fact check passed. H0 = 70.50."
            ),
            "researchPlanVisible": True,
            "matrixVisible": True,
            "factCheckVisible": True,
            "publication_ready": True,
        },
        hidden_record={
            "full_paper_read_status": "complete",
            "expected_datasets": ["DESI DR1 BAO", "Planck compressed"],
            "expected_methods": ["compressed"],
            "expected_models": ["LCDM"],
            "expected_numbers": [{"name": "H0", "value": 67.4, "tolerance_abs": 0.2}],
        },
    )

    assert result["grade"] == "B"
    assert result["criteria"]["numeric_compatible"] == "contradicted"
    assert any("numeric_compatible=contradicted" in reason for reason in result["why_not_A"])


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


def test_a_requires_structured_result_expectations_not_only_a_number() -> None:
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


def test_alpha_summary_surfaces_counts_and_implementation_queue() -> None:
    from app.services.research_alpha_evaluator import summarize_alpha_evaluations

    summary = summarize_alpha_evaluations([
        {
            "grade": "A",
            "hidden_record_status": "complete",
            "why_not_A": [],
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

    assert summary["total"] == 3
    assert summary["strict_A_count"] == 1
    assert summary["B_or_better_count"] == 2
    assert summary["grade_counts"] == {"A": 1, "B": 1, "E": 1}
    assert summary["top_why_not_A"][0]["count"] == 1
    assert any("expected numerical constraints" in item["action"] for item in summary["implementation_queue"])
    assert any("unsupported_numeric_risk" in item["action"] for item in summary["implementation_queue"])
