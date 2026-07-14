import numpy as np


def test_iid_chains_are_convergence_ready_but_never_standalone_publication_ready():
    from app.services.chain_diagnostics import evaluate_chain_diagnostics

    rng = np.random.default_rng(123)
    chains = {
        "S8": [rng.normal(0.80, 0.02, 600).tolist() for _ in range(4)],
        "omegam": [rng.normal(0.30, 0.03, 600).tolist() for _ in range(4)],
    }

    result = evaluate_chain_diagnostics(chains=chains)

    assert result["success"] is True
    assert result["analysis_status"] == "CHAIN_DIAGNOSTICS_READY"
    assert result["convergence_ready"] is True
    assert result["diagnostics_ready"] is True
    assert result["publication_ready"] is False
    assert result["parameters"]["S8"]["rhat"] < 1.01
    assert result["parameters"]["S8"]["ess_bulk"] >= 400
    assert result["chain_diagnostics"]["thresholds"]["rhat_method"] == "rank"
    assert result["chain_diagnostics"]["thresholds"]["rhat_max"] == 1.01
    assert result["chain_diagnostics"]["thresholds"]["ess_method"] == "bulk"
    assert "missing_likelihood_provenance" in result["publication_reasons"]
    assert result["scientific_claim_scope"] == "diagnostics_only"
    assert result["__do_not_claim__"] is True


def test_evaluate_chain_diagnostics_marks_single_chain_partial():
    from app.services.chain_diagnostics import evaluate_chain_diagnostics

    result = evaluate_chain_diagnostics(
        chains={"H0": [70.0 + 0.01 * i for i in range(100)]},
    )

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["convergence_ready"] is False
    assert result["__tool_status__"] == "PARTIAL"
    assert result["parameters"]["H0"]["rhat"] is None
    assert result["__do_not_claim__"] is True


def test_nonconverged_chain_numbers_cannot_be_laundered_into_claims():
    from app.services.chain_diagnostics import evaluate_chain_diagnostics
    from app.services.claim_validator import validate_claims

    result = evaluate_chain_diagnostics(
        chains={"H0": [70.0 + 0.01 * i for i in range(100)]},
    )
    validation = validate_claims(
        "H0 = 70.495 and ESS = 0.",
        [{"tool": "evaluate_chain_diagnostics", "result": result}],
    )

    assert validation.ok is False


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
    assert result["publication_ready"] is False  # three chains are diagnostic-only
    assert result["convergence_ready"] is False


def test_convergence_gate_uses_strict_rhat_and_bulk_ess_thresholds(monkeypatch):
    import app.services.chain_diagnostics as diagnostics

    chains = {"x": [[float(i) for i in range(100)] for _ in range(4)]}
    monkeypatch.setattr(diagnostics, "_rhat", lambda _: 1.01)
    monkeypatch.setattr(diagnostics, "_ess", lambda _: 500.0)

    at_rhat_boundary = diagnostics.evaluate_chain_diagnostics(chains=chains)

    assert at_rhat_boundary["convergence_ready"] is False
    assert "rank_normalized_rhat_at_or_above_1.01" in at_rhat_boundary["publication_reasons"]

    monkeypatch.setattr(diagnostics, "_rhat", lambda _: 1.0)
    monkeypatch.setattr(diagnostics, "_ess", lambda _: 399.0)
    below_ess_boundary = diagnostics.evaluate_chain_diagnostics(chains=chains)

    assert below_ess_boundary["convergence_ready"] is False
    assert "bulk_ess_below_400" in below_ess_boundary["publication_reasons"]


def test_chains_stuck_at_different_constants_never_pass_publication_gate():
    """Zero within-chain variance must not erase obvious between-chain conflict."""
    from app.services.chain_diagnostics import evaluate_chain_diagnostics

    chains = {
        "x": [
            ([0.0] * 40),
            ([0.0] * 40),
            ([1.0] * 40),
            ([1.0] * 40),
        ]
    }
    result = evaluate_chain_diagnostics(chains=chains)

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["parameters"]["x"]["status"] != "ok"
    assert result["parameters"]["x"]["ess_bulk"] < 400.0


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
