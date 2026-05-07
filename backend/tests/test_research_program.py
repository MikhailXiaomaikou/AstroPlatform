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


def test_fact_verifier_flags_unsupported_posterior_claims() -> None:
    from app.services.research_program import verify_research_facts

    report = verify_research_facts(
        tool_results=[],
        final_reply="The full Cobaya likelihood gives H0 = 70 and S8 = 0.8.",
    )

    assert report["analysis_status"] == "FACT_CHECK_READY"
    assert report["status"] == "blocked"
    statuses = {claim["status"] for claim in report["claims"]}
    assert "unsupported" in statuses
    assert "contradicted" in statuses


def test_fact_verifier_accepts_publication_ready_matrix_claim_scope() -> None:
    from app.services.research_program import (
        build_evidence_graph,
        plan_research_program,
        run_research_matrix,
        verify_research_facts,
    )

    plan = plan_research_program(question="Research DESI BAO LCDM constraints.")["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)
    graph = build_evidence_graph(tool_results=[{"tool": "run_research_matrix", "result": matrix}])
    report = verify_research_facts(
        tool_results=[
            {"tool": "run_research_matrix", "result": matrix},
            {"tool": "build_evidence_graph", "result": graph},
        ],
        final_reply="This is a compressed-likelihood preliminary result for Omega_m.",
    )

    assert report["status"] in {"passed", "warning"}
    assert any(claim["status"] == "verified" for claim in report["claims"])


def test_fact_verifier_marks_source_not_in_current_turn_unsupported() -> None:
    from app.services.research_program import verify_research_facts

    report = verify_research_facts(
        tool_results=[],
        final_reply="The source 2099A&A...999Z...9X supports this value.",
    )

    assert report["status"] == "warning"
    assert any(claim["kind"] == "source" and claim["status"] == "unsupported" for claim in report["claims"])


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


def test_mine_paper_tools_extracts_method_backed_tools() -> None:
    from app.services.paper_tool_mining import mine_paper_tools

    result = mine_paper_tools(
        arxiv_id="2404.03002",
        paper_metadata={
            "title": "A mock DESI BAO and Pantheon+ dark energy analysis",
            "authors": ["Example Author"],
            "year": 2026,
        },
        source_sections={
            "Data and likelihood": (
                "We use the DESI BAO data vector together with Pantheon+ supernova "
                "distance moduli. The likelihood is evaluated with the published "
                "covariance matrix using a Gaussian chi-square."
            ),
            "Sampling and diagnostics": (
                "The posterior is sampled with MCMC using Cobaya. We inspect R-hat, "
                "ESS, trace plots, AIC/BIC, and split-sample robustness tests."
            ),
            "Appendix tables": (
                "Table 2 lists the machine-readable data vector and covariance matrix "
                "used by the likelihood."
            ),
        },
    )

    assert result["analysis_status"] == "PAPER_TOOL_MINING_READY"
    specs = result["tool_specs"]
    categories = {spec["tool_category"] for spec in specs}
    assert {"data_loader", "likelihood", "sampler", "diagnostic", "table_extractor"} <= categories
    assert all(spec["source_spans"] for spec in specs)
    assert max(spec["confidence"] for spec in specs) >= 0.78
    likelihood = next(spec for spec in specs if spec["tool_category"] == "likelihood")
    assert "DESI BAO" in likelihood["datasets"]
    assert likelihood["implementation_status"] in {"partial", "available"}


def test_mine_paper_tools_blocks_abstract_only_high_confidence() -> None:
    from app.services.paper_tool_mining import mine_paper_tools

    result = mine_paper_tools(
        paper_metadata={
            "title": "Abstract-only cosmology paper",
            "abstract": "We use DESI BAO and MCMC to constrain dark energy.",
            "arxiv_id": "2601.00001",
        },
    )

    assert result["analysis_status"] == "PAPER_TOOL_MINING_PARTIAL"
    assert result["blocked_reason"] == "full_text_required"
    assert all(spec["confidence"] < 0.5 for spec in result["tool_specs"])


def test_tool_ontology_gap_matrix_and_queue_rank_missing_capabilities() -> None:
    from app.services.paper_tool_mining import (
        build_tool_gap_matrix,
        build_tool_ontology,
        mine_paper_tools,
        rank_tool_implementation_queue,
    )

    mined = mine_paper_tools(
        arxiv_id="2601.00002",
        paper_metadata={"title": "Nested sampling cosmology workflow"},
        source_sections={
            "Methods": (
                "We evaluate the likelihood with a covariance matrix and run nested "
                "sampling with PolyChord to compute Bayesian evidence. We also release "
                "configuration files and chain files for reproducibility."
            ),
        },
    )
    specs = mined["tool_specs"]
    ontology = build_tool_ontology(tool_specs=specs)
    gap = build_tool_gap_matrix(tool_specs=specs)
    queue = rank_tool_implementation_queue(gap_matrix=gap["gap_matrix"])

    assert ontology["analysis_status"] == "TOOL_ONTOLOGY_READY"
    assert ontology["cluster_count"] >= 2
    assert any(row["current_status"] == "missing" for row in gap["gap_matrix"])
    assert queue["implementation_queue"]
    assert queue["implementation_queue"][0]["priority"] in {"P0", "P1", "P2"}


def test_run_paper_tool_mining_batch_produces_coverage_stats() -> None:
    from app.services.paper_tool_mining import run_paper_tool_mining_batch

    result = run_paper_tool_mining_batch(
        papers=[
            {
                "arxiv_id": "2601.00003",
                "title": "BAO likelihood paper",
                "sections": {
                    "Methods": "The BAO likelihood uses a data vector and covariance matrix.",
                },
            },
            {
                "arxiv_id": "2601.00004",
                "title": "Metadata only paper",
                "abstract": "We mention MCMC in the abstract.",
            },
        ],
        max_papers=10,
    )

    assert result["analysis_status"] == "PAPER_TOOL_MINING_BATCH_READY"
    assert result["paper_count"] == 2
    assert result["tool_spec_count"] >= 1
    assert "category_counts" in result["coverage_stats"]
    assert isinstance(result["implementation_queue"], list)


def test_paper_tool_mining_loop_round_reads_twenty_and_updates_state() -> None:
    from app.services.paper_tool_mining_loop import run_paper_tool_mining_loop_round

    papers = [
        {
            "arxiv_id": f"2601.{idx:05d}",
            "title": f"Mock cosmology paper {idx}",
            "sections": {
                "Methods": "The likelihood uses a data vector and covariance matrix with MCMC diagnostics.",
            },
        }
        for idx in range(25)
    ]

    result = run_paper_tool_mining_loop_round(papers=papers, batch_size=20)

    assert result["analysis_status"] == "PAPER_TOOL_MINING_LOOP_ROUND_READY"
    assert result["batch_size"] == 20
    assert len(result["selected_paper_ids"]) == 20
    assert result["remaining_unread"] == 5
    assert len(result["updated_state"]["read_paper_ids"]) == 20
    assert result["batch_result"]["paper_count"] == 20


def test_paper_tool_mining_loop_continues_from_state_and_stops_when_empty() -> None:
    from app.services.paper_tool_mining_loop import run_paper_tool_mining_loop_round

    papers = [
        {
            "arxiv_id": f"2602.{idx:05d}",
            "title": f"Mock paper {idx}",
            "sections": {"Methods": "We release tables and run MCMC with covariance diagnostics."},
        }
        for idx in range(3)
    ]
    first = run_paper_tool_mining_loop_round(papers=papers, batch_size=2)
    second = run_paper_tool_mining_loop_round(
        papers=papers,
        batch_size=2,
        state=first["updated_state"],
    )
    empty = run_paper_tool_mining_loop_round(
        papers=papers,
        batch_size=2,
        state=second["updated_state"],
    )

    assert first["selected_paper_ids"] == ["2602.00000", "2602.00001"]
    assert second["selected_paper_ids"] == ["2602.00002"]
    assert empty["analysis_status"] == "PAPER_TOOL_MINING_LOOP_EMPTY"
    assert empty["selected_paper_ids"] == []


def test_paper_tool_mining_loop_writes_local_bundle(tmp_path) -> None:
    import json

    from app.services.paper_tool_mining_loop import read_loop_state, run_paper_tool_mining_loop_round

    papers = [
        {
            "arxiv_id": "2603.00001",
            "title": "Bundle paper",
            "sections": {"Methods": "The data vector and covariance matrix feed a Gaussian likelihood."},
        }
    ]

    result = run_paper_tool_mining_loop_round(
        papers=papers,
        batch_size=20,
        output_dir=tmp_path,
        write_local_bundle=True,
    )

    bundle_path = tmp_path / "round_0001.json"
    state_path = tmp_path / "state.json"
    assert result["bundle_path"] == str(bundle_path)
    assert bundle_path.exists()
    assert state_path.exists()
    assert json.loads(bundle_path.read_text(encoding="utf-8"))["updated_state"]["local_only"] is True
    assert read_loop_state(state_path)["read_paper_ids"] == ["2603.00001"]


def test_paper_tool_mining_loop_recovers_read_ids_from_round_bundles(tmp_path) -> None:
    import json

    from app.services.paper_tool_mining_loop import read_loop_state

    (tmp_path / "state.json").write_text(
        json.dumps({"round_index": 1, "read_paper_ids": ["2605.00001"]}),
        encoding="utf-8",
    )
    (tmp_path / "round_0002.json").write_text(
        json.dumps(
            {
                "round_index": 2,
                "selected_paper_ids": ["2605.00002", "2605.00003"],
                "batch_result": {"tool_spec_count": 4, "mined_paper_count": 2},
            }
        ),
        encoding="utf-8",
    )

    state = read_loop_state(tmp_path / "state.json")

    assert state["round_index"] == 2
    assert state["read_paper_ids"] == ["2605.00001", "2605.00002", "2605.00003"]
    assert any(item["round_index"] == 2 for item in state["round_history"])


def test_paper_candidate_pool_normalizes_scores_and_dedupes_seed_papers() -> None:
    import asyncio

    from app.services.paper_candidate_pool import build_paper_mining_candidate_pool

    result = asyncio.run(build_paper_mining_candidate_pool(
        seed_papers=[
            {
                "arxiv_id": "2604.00001",
                "title": "DESI BAO covariance likelihood with Pantheon supernovae",
                "abstract": "We combine BAO, supernova, CMB, likelihood, covariance, and MCMC chains.",
            },
            {
                "arxiv_id": "2604.00001",
                "title": "Duplicate paper",
                "abstract": "Duplicate.",
            },
            {
                "title": "Weak lensing S8 tension robustness matrix",
                "sections": {"Methods": "We inspect covariance likelihoods and robustness tests."},
            },
        ],
        allow_live_search=False,
        max_papers=10,
    ))

    assert result["analysis_status"] == "PAPER_MINING_CANDIDATE_POOL_READY"
    assert result["candidate_count"] == 2
    assert result["live_search_enabled"] is False
    assert result["candidate_papers"][0]["relevance_score"] > 0
    assert any(p["mining_readiness"] == "source_sections_ready" for p in result["candidate_papers"])


def test_paper_candidate_pool_excludes_already_read_seed_papers() -> None:
    import asyncio

    from app.services.paper_candidate_pool import build_paper_mining_candidate_pool

    result = asyncio.run(build_paper_mining_candidate_pool(
        seed_papers=[
            {
                "arxiv_id": "2604.10001",
                "title": "Already read BAO paper",
                "abstract": "BAO likelihood covariance MCMC.",
            },
            {
                "arxiv_id": "2604.10002",
                "title": "Unread weak lensing paper",
                "abstract": "Weak lensing cosmic shear covariance likelihood.",
            },
        ],
        state={"read_paper_ids": ["2604.10001"]},
        allow_live_search=False,
    ))

    assert result["excluded_count"] == 1
    assert [p["arxiv_id"] for p in result["candidate_papers"]] == ["2604.10002"]
