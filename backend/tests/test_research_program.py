from __future__ import annotations


def test_research_plan_routes_multiprobe_cosmology_to_dag() -> None:
    from app.services.research_program import plan_research_program

    result = plan_research_program(
        question=(
            "I want to research DESI BAO + Pantheon+ + Planck dark-energy "
            "robustness and identify which combinations are executable."
        )
    )

    plan = result["research_plan"]
    assert result["analysis_status"] == "RESEARCH_PLAN_READY"
    assert plan["required_probes"] == ["BAO", "SN", "CMB"]
    assert plan["candidate_dataset_keys"] == [
        "desi_dr1_bao",
        "pantheon_plus",
        "des_sn5yr",
        "union3",
        "planck2018_compressed",
    ]
    assert plan["executable_level"] == "mixed"
    assert any("Pantheon+" in gap for gap in plan["blocking_gaps"])
    assert any(cell["label"] == "BAO + CMB" for cell in plan["proposed_experiment_matrix"])


def test_research_matrix_runs_executable_cells_and_marks_config_gaps() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    plan = plan_research_program(
        question="Research DESI BAO + Pantheon+ + Planck LCDM consistency."
    )["research_plan"]
    result = run_research_matrix(research_plan=plan, n_samples=512)

    assert result["analysis_status"] == "RESEARCH_MATRIX_READY"
    assert result["ready_cells"] >= 1
    assert any(cell["publication_ready"] is True for cell in result["matrix"])
    assert any(
        cell["publication_ready"] is False
        and "pantheon_plus" in cell["dataset_keys"]
        for cell in result["matrix"]
    )


def test_evidence_graph_links_claims_to_publication_ready_runs() -> None:
    from app.services.research_program import build_evidence_graph, plan_research_program, run_research_matrix

    plan = plan_research_program(question="Research DESI BAO LCDM constraints.")["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)
    graph = build_evidence_graph(tool_results=[{"tool": "run_research_matrix", "result": matrix}])

    assert graph["analysis_status"] == "EVIDENCE_GRAPH_READY"
    assert "omegam" in graph["claimable_parameters"]
    assert graph["unsupported_claim_count"] == 0
    assert graph["evidence_graph"]["supported_claims"]


def test_evidence_graph_flags_unsupported_final_reply_claims() -> None:
    from app.services.research_program import build_evidence_graph

    graph = build_evidence_graph(
        tool_results=[],
        final_reply="The result is H0 = 70 and S8 = 0.8 with a 3 sigma tension.",
    )

    assert graph["publication_ready"] is False
    assert graph["unsupported_claim_count"] >= 3


def test_research_registry_entries_expose_extended_research_fields() -> None:
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("desi_dr1_bao").to_dict()
    for key in (
        "research_roles",
        "execution_level",
        "independence_group",
        "known_overlap",
        "claimable_parameters",
        "recommended_combinations",
        "do_not_combine_with",
    ):
        assert key in entry
