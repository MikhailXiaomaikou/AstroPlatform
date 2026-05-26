import numpy as np


def test_evaluate_chain_diagnostics_reports_ready_for_iid_chains():
    from app.services.chain_diagnostics import evaluate_chain_diagnostics

    rng = np.random.default_rng(123)
    chains = {
        "S8": [rng.normal(0.80, 0.02, 600).tolist() for _ in range(4)],
        "omegam": [rng.normal(0.30, 0.03, 600).tolist() for _ in range(4)],
    }

    result = evaluate_chain_diagnostics(chains=chains)

    assert result["success"] is True
    assert result["analysis_status"] == "CHAIN_DIAGNOSTICS_READY"
    assert result["publication_ready"] is True
    assert result["parameters"]["S8"]["rhat"] < 1.05
    assert result["parameters"]["S8"]["ess_bulk"] >= 400
    assert result["chain_diagnostics"]["thresholds"]["rhat_max"] == 1.05


def test_evaluate_chain_diagnostics_marks_single_chain_partial():
    from app.services.chain_diagnostics import evaluate_chain_diagnostics

    result = evaluate_chain_diagnostics(
        chains={"H0": [70.0 + 0.01 * i for i in range(100)]},
    )

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["__tool_status__"] == "PARTIAL"
    assert result["parameters"]["H0"]["rhat"] is None
    assert result["__do_not_claim__"]


def test_ai_tool_wrapper_evaluates_chain_diagnostics():
    from app.services.ai_tools_cosmology import _exec_evaluate_chain_diagnostics

    rng = np.random.default_rng(5)
    result = _exec_evaluate_chain_diagnostics(
        {
            "chains": {
                "x": [rng.normal(0.0, 1.0, 500).tolist() for _ in range(3)],
            }
        }
    )

    assert result["success"] is True
    assert result["parameters"]["x"]["draws_per_chain"] == 500


def test_paper_tool_gap_matrix_knows_chain_diagnostics_is_available():
    from app.services.paper_tool_mining import build_tool_gap_matrix

    matrix = build_tool_gap_matrix(
        tool_specs=[
            {
                "tool_category": "diagnostic",
                "canonical_capability": "chain_diagnostics",
                "implementation_status": "available",
                "source_spans": [{"section": "Methods"}],
            }
        ]
    )

    assert matrix["gap_matrix"][0]["current_status"] == "available"
    assert "evaluate_chain_diagnostics" in matrix["gap_matrix"][0]["available_platform_tools"]
