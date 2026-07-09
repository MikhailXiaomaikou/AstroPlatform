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
    # Tier 2A made des_sn5yr / union3 executable (compressed SN-only Ωm), so all
    # five candidates now run in-process: no blocking gaps, and the plan upgrades
    # from "mixed" to fully compressed-preliminary executable.
    assert plan["executable_level"] == "compressed_preliminary"
    assert plan["blocking_gaps"] == []
    assert any(cell["label"] == "BAO + CMB" for cell in plan["proposed_experiment_matrix"])


def test_research_plan_explicit_desi_dr2_selects_dr2_bao() -> None:
    """Regression: research-matrix planning hard-coded the BAO probe member to
    desi_dr1_bao, so a question explicitly about DESI DR2 silently planned DR1
    (same bug class as the 2026-07-09 chat-routing DR2 reroute). Bare "DESI"
    keeps planning DR1; the registry marks the two releases mutually
    do_not_combine_with, so exactly one is ever selected."""
    from app.services.research_program import plan_research_program

    plan = plan_research_program(
        question=(
            "I want to research DESI DR2 BAO + Planck compressed dark-energy "
            "robustness and identify which combinations are executable."
        )
    )["research_plan"]
    assert plan["candidate_dataset_keys"] == ["desi_dr2_bao", "planck2018_compressed"]
    assert plan["executable_level"] == "compressed_preliminary"
    assert plan["blocking_gaps"] == []

    bare = plan_research_program(
        question="I want to research DESI BAO + Planck compressed dark-energy robustness."
    )["research_plan"]
    assert bare["candidate_dataset_keys"] == ["desi_dr1_bao", "planck2018_compressed"]


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
        cell["publication_ready"] is True
        and cell["dataset_keys"] == ["pantheon_plus"]
        for cell in result["matrix"]
    )
    assert any(
        cell["execution_level"] == "executed_not_ready"
        and "pantheon_plus" in cell["dataset_keys"]
        and "planck2018_compressed" in cell["dataset_keys"]
        for cell in result["matrix"]
    )
    charts = result["research_charts"]
    assert charts["chart_version"] == 1
    assert charts["matrix_status"]
    assert charts["posterior_forest"]
    assert charts["diagnostics"]
    assert any(row["parameter"] == "H0" for row in charts["posterior_forest"])
    assert any(row["status"] == "ready" for row in charts["matrix_status"])


def test_workflow2_bao_cmb_public_path_is_publication_ready() -> None:
    """Lock the full public Research Matrix path, not just the private sampler.

    Regression target: Workflow 2 BAO+CMB should no longer look like
    ESS≈1/40 in Chat UI.  The public plan→matrix path must mark the BAO+CMB
    cell publication-ready with H0 around the Planck-calibrated 67–68 range.
    """
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "我想测试一个观测宇宙学 Research Matrix：DESI DR1 BAO + Pantheon+ SN + "
        "Planck compressed CMB，flat ΛCDM。请先规划研究矩阵，再执行可运行的 "
        "compressed-likelihood cells。请特别报告 BAO+CMB 这一格的 "
        "publication_ready、ESS/chain diagnostics 和 H0 posterior median；"
        "如果 Pantheon+ 只能 config-only，也请明确说明。"
    )
    plan = plan_research_program(question=prompt)["research_plan"]
    result = run_research_matrix(research_plan=plan, random_seed=20260503, n_samples=4000)

    bao_cmb = next(cell for cell in result["matrix"] if cell["label"] == "BAO + CMB")
    chain = bao_cmb["result"]

    assert bao_cmb["publication_ready"] is True
    assert bao_cmb["execution_level"] == "compressed_preliminary"
    assert chain["chain_diagnostics"]["proposal_ess"] >= 400
    assert 67.0 <= chain["parameters"]["H0"]["median"] <= 68.0


def test_cmb_polarization_rotation_does_not_use_distance_priors() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "I want to test whether public CMB polarization data support a global "
        "polarization rotation angle using EB/TB parity-odd correlations. "
        "If the EB/TB bandpowers, covariance, instrument-angle prior, or "
        "rotation likelihood are missing, list the gap."
    )

    planned = plan_research_program(question=prompt)
    plan = planned["research_plan"]

    assert "CMB_POLARIZATION_ROTATION" in plan["required_probes"]
    assert "planck2018_compressed" not in plan["candidate_dataset_keys"]
    assert "planck_pr4_ebtb_rotation" in plan["candidate_dataset_keys"]
    assert plan["executable_level"] == "config_only"
    assert any("EB/TB" in gap for gap in plan["blocking_gaps"])

    matrix = run_research_matrix(research_plan=plan)

    assert matrix["publication_ready"] is False
    assert matrix["ready_cells"] == 0
    assert matrix["matrix"][0]["model"] == "isotropic_beta"
    assert matrix["matrix"][0]["publication_ready"] is False


def test_cmb_rotation_scope_gap_counts_as_partial_pass_readiness() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "I want to test CMB polarization rotation using EB/TB parity-odd "
        "correlations. Identify data vectors, covariance, calibration priors, "
        "and likelihood gaps without using distance priors."
    )

    plan = plan_research_program(question=prompt)["research_plan"]
    readiness = plan["partial_pass_readiness"]
    gap_rows = plan["capability_gap_matrix"]

    assert readiness["target"] == "B_OR_BETTER_PARTIAL_PASS_95"
    assert readiness["meets_partial_pass"] is True
    assert readiness["score_floor"] == "B"
    assert readiness["coverage_status"] == "domain_gap_mapped"
    assert any(row["component"].endswith(":covariance") for row in gap_rows)
    assert any(row["component"].endswith(":rotation_likelihood") for row in gap_rows)

    matrix = run_research_matrix(research_plan=plan)
    assert matrix["partial_pass_readiness"]["meets_partial_pass"] is True
    assert matrix["capability_gap_matrix"]


def test_cmb_polarization_rotation_hyphenated_prompt_does_not_use_distance_priors() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "I want to compare isotropic and anisotropic CMB polarization-rotation "
        "models using public Planck/ACT/SPT information. Do not use distance "
        "priors as polarization evidence."
    )

    plan = plan_research_program(question=prompt)["research_plan"]

    assert "CMB_POLARIZATION_ROTATION" in plan["required_probes"]
    assert "act_dr6_ebtb_rotation" in plan["candidate_dataset_keys"]
    assert any("rotation-angle likelihood" in gap for gap in plan["blocking_gaps"])

    matrix = run_research_matrix(research_plan=plan)

    assert matrix["publication_ready"] is False
    assert matrix["ready_cells"] == 0
    assert matrix["matrix"][0]["model"] == "isotropic_beta"


def test_cmb_rotation_prompt_with_late_time_dataset_names_does_not_run_bao_matrix() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "I want to test whether CMB polarization data support isotropic or "
        "anisotropic polarization rotation. Use Planck, ACT, DESI, Pantheon+, "
        "SH0ES only where registered, identify EB/TB spectra, angle-calibration "
        "priors and covariance, and report no rotation-angle numbers unless the "
        "likelihood is executable."
    )

    plan = plan_research_program(question=prompt)["research_plan"]

    assert plan["required_probes"] == ["CMB_POLARIZATION_ROTATION"]
    assert "planck_pr4_ebtb_rotation" in plan["candidate_dataset_keys"]
    assert any("rotation-angle likelihood" in gap for gap in plan["blocking_gaps"])

    matrix = run_research_matrix(research_plan=plan)

    assert matrix["publication_ready"] is False
    assert matrix["ready_cells"] == 0
    assert matrix["matrix"][0]["model"] == "isotropic_beta"


def test_mixed_cmb_rotation_and_late_time_cosmology_preserves_both_workflows() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "I want to test CMB polarization rotation and also compare H0 and dark "
        "energy constraints with BAO, SN, CMB, and SH0ES."
    )

    plan = plan_research_program(question=prompt)["research_plan"]

    assert "CMB_POLARIZATION_ROTATION" in plan["required_probes"]
    assert "BAO" in plan["required_probes"]
    assert "SN" in plan["required_probes"]
    assert "H0" in plan["required_probes"]
    assert "planck_pr4_ebtb_rotation" in plan["candidate_dataset_keys"]
    assert "planck2018_compressed" in plan["candidate_dataset_keys"]
    assert "lcdm" in plan["model_families"]
    assert "isotropic_beta" not in plan["model_families"]

    labels = [cell["label"] for cell in plan["proposed_experiment_matrix"]]
    assert any("BAO + SN + CMB" in label for label in labels)
    assert any("CMB rotation" in label for label in labels)

    matrix = run_research_matrix(research_plan=plan, n_samples=512)

    assert matrix["matrix_size"] > 1
    assert any(cell["model"] == "lcdm" for cell in matrix["matrix"])
    assert any(cell["model"] == "isotropic_beta" for cell in matrix["matrix"])


def test_cmb_rotation_matrix_execution_rejects_forced_late_time_dataset_keys() -> None:
    from app.services.research_program import run_research_matrix

    prompt = (
        "I want to test whether CMB polarization data support isotropic or "
        "anisotropic polarization rotation. Use Planck, ACT, DESI, Pantheon+, "
        "SH0ES only where registered, identify EB/TB spectra, angle-calibration "
        "priors and covariance, and report no rotation-angle numbers unless the "
        "likelihood is executable."
    )

    matrix = run_research_matrix(
        question=prompt,
        dataset_keys=["desi_dr1_bao", "pantheon_plus", "shoes_h0_riess22"],
        n_samples=512,
    )

    assert matrix["publication_ready"] is False
    assert matrix["ready_cells"] == 0
    assert matrix["matrix"][0]["dataset_keys"]
    assert "wrong routing" in matrix["failure_categories"]
    assert any("not valid substitutes" in warning for warning in matrix["warnings"])


def test_cmb_rotation_runner_supports_publication_ready_fixture(monkeypatch) -> None:
    from app.services import cmb_rotation_likelihoods as cr
    from app.services.research_program import run_research_matrix, verify_research_facts

    fixture = cr.CMBRotationDatasetEntry(
        key="unit_test_cmb_rotation",
        display_name="Unit-test EB/TB rotation likelihood",
        version="test fixture",
        observables=("EB", "TB", "beta_deg"),
        source_url="https://example.invalid/cmb-rotation-fixture",
        citations=(cr.CMBRotationCitation(label="Unit Test Rotation Fixture", year=2026),),
        covariance_provided=True,
        calibration_prior={"type": "gaussian", "sigma_deg": 0.05},
        execution_mode="compressed_gaussian",
        compressed_likelihood=cr.CMBRotationCompressedSpec(
            parameter="beta_deg",
            mean=0.35,
            sigma=0.10,
            source_locator="unit test compressed EB/TB beta likelihood",
            approximation="one-dimensional Gaussian beta posterior",
        ),
    )
    monkeypatch.setitem(cr.CMB_ROTATION_DATASETS, fixture.key, fixture)

    result = cr.run_cmb_rotation_likelihood(
        dataset_keys=[fixture.key],
        model="isotropic_beta",
        random_seed=7,
        n_samples=1024,
    )

    assert result["publication_ready"] is True
    assert result["analysis_status"] == "CMB_ROTATION_CHAIN_READY"
    assert abs(result["parameters"]["beta_deg"]["median"] - 0.35) < 0.02

    matrix = run_research_matrix(
        question=(
            "I want to test CMB polarization rotation with EB/TB correlations "
            "and an instrument-angle prior."
        ),
        dataset_keys=[fixture.key],
        n_samples=1024,
    )

    assert matrix["publication_ready"] is True
    assert matrix["ready_cells"] == 1
    assert matrix["matrix"][0]["result"]["parameters"]["beta_deg"]["median"]

    fact = verify_research_facts(
        tool_results=[{"tool": "run_cmb_rotation_likelihood", "result": result}],
        final_reply="Compressed-likelihood preliminary: beta_deg = 0.35 deg.",
    )

    assert fact["status"] == "passed"
    assert fact["unsupported_claim_count"] == 0


def test_cmb_rotation_fact_check_blocks_beta_without_runner() -> None:
    from app.services.research_program import verify_research_facts

    fact = verify_research_facts(
        tool_results=[],
        final_reply="The CMB polarization rotation angle beta_deg = 0.35 deg.",
    )

    assert fact["status"] == "blocked"
    assert any(
        claim["status"] == "unsupported" and claim["kind"] == "numeric"
        for claim in fact["claims"]
    )


def test_bmode_rotation_field_prompts_record_scope_gap() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompts = [
        "I want to examine whether B-mode polarization data can support a rotation-angle field on the sky. Identify required maps, bandpowers, covariance, and calibration priors.",
        "I want to study anisotropic cosmic birefringence using spherical-harmonic rotation-field estimators. Use registered data products only and report missing estimator/likelihood support.",
    ]

    for prompt in prompts:
        plan = plan_research_program(question=prompt)["research_plan"]

        assert "CMB_POLARIZATION_ROTATION" in plan["required_probes"]
        assert plan["candidate_dataset_keys"]
        assert any("rotation-angle likelihood" in gap for gap in plan["blocking_gaps"])

        matrix = run_research_matrix(research_plan=plan)

        assert matrix["publication_ready"] is False
        assert matrix["ready_cells"] == 0


def test_primordial_feature_request_does_not_use_compressed_distance_priors() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "I want to test whether oscillatory primordial-feature templates improve "
        "the fit to CMB temperature and polarization spectra. Identify the required "
        "Planck/ACT spectra, covariance, look-elsewhere treatment and sampler, then "
        "run only available controlled likelihoods and report missing pieces."
    )

    plan = plan_research_program(question=prompt)["research_plan"]

    assert "CMB_PRIMORDIAL_FEATURES" in plan["required_probes"]
    assert "planck2018_compressed" not in plan["candidate_dataset_keys"]
    assert plan["executable_level"] == "not_available"
    assert any("TT/TE/EE spectra" in gap for gap in plan["blocking_gaps"])

    matrix = run_research_matrix(research_plan=plan)

    assert matrix["publication_ready"] is False
    assert matrix["ready_cells"] == 0
    assert matrix["matrix"] == []
    assert plan["partial_pass_readiness"]["meets_partial_pass"] is True
    assert plan["partial_pass_readiness"]["coverage_status"] == "domain_gap_mapped"
    assert any(
        row["component"] == "primordial_feature:look_elsewhere"
        for row in plan["capability_gap_matrix"]
    )


def test_primordial_feature_spectra_variants_do_not_use_distance_priors() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompts = [
        "I want to compare sharp-feature and resonant-feature primordial power-spectrum models against CMB spectra.",
        "I want to test oscillatory residuals in CMB temperature spectra with a frequency scan.",
        "I want to run a feature-search workflow over CMB TT/TE/EE spectra and compare Δχ² with a null model.",
    ]

    for prompt in prompts:
        plan = plan_research_program(question=prompt)["research_plan"]

        assert "CMB_PRIMORDIAL_FEATURES" in plan["required_probes"]
        assert "planck2018_compressed" not in plan["candidate_dataset_keys"]
        assert any("look-elsewhere" in gap for gap in plan["blocking_gaps"])

        matrix = run_research_matrix(research_plan=plan)

        assert matrix["publication_ready"] is False
        assert matrix["ready_cells"] == 0


def test_ede_request_records_missing_model_but_runs_lcdm_baseline() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "I want to test whether an axion-like early-dark-energy model is favored "
        "after adding DESI BAO and recent CMB-lensing information. Use registered "
        "public BAO/CMB-lensing/CMB compressed data where available."
    )

    plan = plan_research_program(question=prompt)["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)

    assert any("Early-dark-energy" in gap for gap in plan["blocking_gaps"])
    assert any(cell.get("publication_ready") is True for cell in matrix["matrix"])
    assert any(
        cell.get("baseline_only") is True
        and any("baseline only" in warning.lower() for warning in cell.get("warnings", []))
        for cell in matrix["matrix"]
    )
    assert plan["partial_pass_readiness"]["meets_partial_pass"] is True
    assert plan["partial_pass_readiness"]["coverage_status"] == "runnable_baseline_available"


def test_unknown_research_question_does_not_meet_partial_pass_readiness() -> None:
    from app.services.research_program import plan_research_program

    plan = plan_research_program(question="Can you think about this vague project?")["research_plan"]

    assert plan["required_probes"] == []
    assert plan["partial_pass_readiness"]["meets_partial_pass"] is False
    assert plan["partial_pass_readiness"]["score_floor"] == "C"


def test_transient_early_energy_wording_stays_in_cosmology_research_mode() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "I want to test a transient early-energy component before recombination "
        "using public compressed cosmology data. Report only supported baseline constraints."
    )

    plan = plan_research_program(question=prompt)["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)

    assert any("Early-dark-energy" in gap for gap in plan["blocking_gaps"])
    assert "CMB" in plan["required_probes"]
    assert matrix["matrix"]
    assert all(cell.get("baseline_only") is True for cell in matrix["matrix"] if cell.get("publication_ready"))


def test_modified_gravity_request_records_dedicated_likelihood_gap() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "I want to test whether a modified-gravity expansion/growth model could "
        "reduce both H0 and S8 tensions using BAO, CMB, weak-lensing, growth-rate "
        "and chronometer data."
    )

    plan = plan_research_program(question=prompt)["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)

    assert any("Modified-gravity" in gap for gap in plan["blocking_gaps"])
    assert any(cell.get("publication_ready") is True for cell in matrix["matrix"])
    assert any(
        cell.get("publication_ready") is False
        for cell in matrix["matrix"]
        if "kids1000_wl" in cell.get("dataset_keys", [])
    )


def test_modified_gravity_interpretation_records_dedicated_gap() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "I want to check whether ACT lensing and galaxy shear prefer lower growth "
        "than Planck under a modified-gravity interpretation. Run available "
        "compressed summaries only."
    )

    plan = plan_research_program(question=prompt)["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)

    assert any("Modified-gravity" in gap for gap in plan["blocking_gaps"])
    assert any(cell.get("baseline_only") is True for cell in matrix["matrix"])


def test_growth_index_gamma_request_records_dedicated_gap() -> None:
    from app.services.research_program import plan_research_program, run_research_matrix

    prompt = (
        "I want to evaluate whether growth-index gamma differs from GR using "
        "registered weak-lensing and background data. Mark missing growth likelihoods."
    )

    plan = plan_research_program(question=prompt)["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)

    assert any("Modified-gravity" in gap for gap in plan["blocking_gaps"])
    assert matrix["matrix"]


def test_physical_dark_energy_histories_route_to_scope_gap_not_python() -> None:
    from app.services.research_program import plan_research_program

    prompt = (
        "I want to compare physically motivated dark-energy histories rather than "
        "only constant Lambda: thawing-like, emergent and mirage-like behavior, "
        "using BAO+CMB+SN distance data."
    )

    plan = plan_research_program(question=prompt)["research_plan"]

    assert any("Thawing, emergent, or mirage" in gap for gap in plan["blocking_gaps"])
    assert plan["proposed_experiment_matrix"]


def test_evidence_graph_links_claims_to_publication_ready_runs() -> None:
    from app.services.research_program import build_evidence_graph, plan_research_program, run_research_matrix

    plan = plan_research_program(question="Research DESI BAO LCDM constraints.")["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)
    graph = build_evidence_graph(tool_results=[{"tool": "run_research_matrix", "result": matrix}])

    assert graph["analysis_status"] == "EVIDENCE_GRAPH_READY"
    assert "omegam" in graph["claimable_parameters"]
    assert graph["unsupported_claim_count"] == 0
    assert graph["evidence_graph"]["supported_claims"]
    assert any(node["type"] == "result" for node in graph["evidence_graph"]["nodes"])
    first_claim = graph["evidence_graph"]["supported_claims"][0]
    assert first_claim["supporting_result"].startswith("result:")
    assert first_claim["evidence_path"][0].startswith("claim:")


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


def test_fact_verifier_does_not_block_full_likelihood_limitations() -> None:
    from app.services.research_program import (
        build_evidence_graph,
        plan_research_program,
        run_research_matrix,
        verify_research_facts,
    )

    plan = plan_research_program(
        question="Run DESI BAO and Planck compressed preliminary constraints."
    )["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)
    graph = build_evidence_graph(tool_results=[{"tool": "run_research_matrix", "result": matrix}])
    report = verify_research_facts(
        tool_results=[
            {"tool": "run_research_matrix", "result": matrix},
            {"tool": "build_evidence_graph", "result": graph},
        ],
        final_reply=(
            "These are compressed-likelihood preliminary numbers, not a full "
            "external Cobaya/CosmoSIS likelihood result. Full external "
            "Cobaya/CosmoSIS reproduction is still outside the compressed "
            "preliminary layer."
        ),
    )

    assert report["status"] in {"passed", "warning"}
    assert not any(claim["status"] == "contradicted" for claim in report["claims"])


def test_b9_fabricated_source_in_tool_input_is_not_verified() -> None:
    """B9: a fabricated arXiv id appearing only in a tool INPUT (on a failed
    call) must NOT be laundered into a Fact-Check 'verified' source — the
    payload text used for source cross-checking must exclude tool inputs and
    failed/do-not-claim results."""
    from app.services.research_program import verify_research_facts

    report = verify_research_facts(
        tool_results=[
            {
                "tool": "mine_paper_tools",
                "input": {"arxiv_id": "2512.34567"},
                "result": {
                    "success": False,
                    "__tool_status__": "FAILED",
                    "__do_not_claim__": True,
                },
            }
        ],
        final_reply="This analysis builds on arXiv:2512.34567.",
    )
    source_claims = [c for c in report["claims"] if c["kind"] == "source"]
    assert source_claims, "the arXiv id should be picked up as a source claim"
    assert all(c["status"] != "verified" for c in source_claims)


def test_fact_verifier_skips_weak_lensing_scope_caveat() -> None:
    from app.services.research_program import (
        build_evidence_graph,
        plan_research_program,
        run_research_matrix,
        verify_research_facts,
    )

    plan = plan_research_program(
        question=(
            "Check S8 consistency using KiDS DES HSC ACT Planck compressed "
            "datasets and report only compressed-summary caveats."
        )
    )["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)
    graph = build_evidence_graph(tool_results=[{"tool": "run_research_matrix", "result": matrix}])
    report = verify_research_facts(
        tool_results=[
            {"tool": "run_research_matrix", "result": matrix},
            {"tool": "build_evidence_graph", "result": graph},
        ],
        final_reply=(
            "This is a compressed-summary robustness screen, not a full "
            "weak-lensing likelihood analysis. I am not treating the matrix as "
            "a publication-grade tension result."
        ),
    )

    assert not any(
        claim["status"] == "unsupported" and claim["kind"] == "numeric"
        for claim in report["claims"]
    )
    assert not any(claim["status"] == "contradicted" for claim in report["claims"])


def test_fact_verifier_does_not_treat_parameter_mentions_as_numbers() -> None:
    from app.services.research_program import (
        build_evidence_graph,
        plan_research_program,
        run_research_matrix,
        verify_research_facts,
    )

    plan = plan_research_program(
        question=(
            "Check S8 consistency using weak-lensing and CMB compressed summaries, "
            "but do not quote unsupported n-sigma claims."
        )
    )["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)
    graph = build_evidence_graph(tool_results=[{"tool": "run_research_matrix", "result": matrix}])
    report = verify_research_facts(
        tool_results=[
            {"tool": "run_research_matrix", "result": matrix},
            {"tool": "build_evidence_graph", "result": graph},
        ],
        final_reply=(
            "Pairwise S8 tension diagnostics were extracted from compressed summaries. "
            "No survey-level n-sigma conclusion is claimed."
        ),
    )

    assert not any(
        claim["status"] == "unsupported" and claim["kind"] == "numeric"
        for claim in report["claims"]
    )


def test_fact_verifier_ignores_error_bars_and_markdown_structure() -> None:
    from app.services.research_program import verify_research_facts

    # P03R regression: markdown headings, table separator rows, and "+/- 1 sigma"
    # error-bar notation must NOT be extracted as unsupported scientific claims.
    report = verify_research_facts(
        tool_results=[],
        final_reply=(
            "## 1.2 Executed cells\n"
            "### Cell A — DESI DR1 BAO only\n"
            "| Parameter | Mean ± 1 sigma | 94% HDI | Notes |\n"
            "|---|---|---|---|\n"
        ),
    )
    real = [c for c in report["claims"] if "No strong scientific fact claim" not in c["text"]]
    assert real == [], real


def test_fact_verifier_distinguishes_error_bar_from_tension() -> None:
    from app.services.research_program import _has_tension_significance_claim

    # error bars / agreement statements are NOT tension claims
    assert _has_tension_significance_claim("Mean ± 1 sigma") is False
    assert _has_tension_significance_claim("H0 and H0_rd are < 1 sigma apart") is False
    assert _has_tension_significance_claim("consistent within 2 sigma") is False
    # genuine tension / discrepancy claims are still detected
    assert _has_tension_significance_claim("H0 shows 4.2 sigma tension with Planck") is True
    assert _has_tension_significance_claim("a 3 sigma discrepancy in S8") is True


def test_export_research_report_gates_results_when_fact_check_blocked() -> None:
    from app.services.research_program import export_research_report

    # P03R regression: a blocked fact check must NOT leave numeric values in the
    # Results section of either the report or the paper draft.
    tool_results = [
        {"tool": "run_research_matrix", "result": {
            "publication_ready": True,
            "parameters": {"H0": {"median": 67.3}},
            "analysis_status": "COMPRESSED_CHAIN_READY",
        }},
        {"tool": "verify_research_facts", "result": {
            "analysis_status": "FACT_CHECK_READY",
            "fact_check_report": {"status": "blocked", "verified_claim_count": 1, "unsupported_claim_count": 4, "claims": []},
        }},
    ]
    out = export_research_report(tool_results=tool_results, title="t")
    assert "## Needs Verification (fact check blocked)" in out["markdown"]
    assert "no numerical finding is cleared" in out["markdown"]
    assert "Fact check is BLOCKED" in out["paper_draft_markdown"]


def test_export_research_report_keeps_results_when_fact_check_passes() -> None:
    from app.services.research_program import export_research_report

    tool_results = [
        {"tool": "run_research_matrix", "result": {
            "publication_ready": True,
            "parameters": {"H0": {"median": 67.3}},
            "analysis_status": "COMPRESSED_CHAIN_READY",
        }},
        {"tool": "verify_research_facts", "result": {
            "analysis_status": "FACT_CHECK_READY",
            "fact_check_report": {"status": "passed", "verified_claim_count": 5, "unsupported_claim_count": 0, "claims": []},
        }},
    ]
    out = export_research_report(tool_results=tool_results, title="t")
    assert "Needs Verification (fact check blocked)" not in out["markdown"]


def test_fact_verifier_skips_negative_full_likelihood_scope_statement() -> None:
    from app.services.research_program import (
        build_evidence_graph,
        plan_research_program,
        run_research_matrix,
        verify_research_facts,
    )

    plan = plan_research_program(
        question="Check weak-lensing S8 consistency with CMB compressed summaries."
    )["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)
    graph = build_evidence_graph(tool_results=[{"tool": "run_research_matrix", "result": matrix}])
    report = verify_research_facts(
        tool_results=[
            {"tool": "run_research_matrix", "result": matrix},
            {"tool": "build_evidence_graph", "result": graph},
        ],
        final_reply=(
            "This turn supports only a compressed-likelihood preliminary check. "
            "Full ACT/Planck/KiDS/DES/HSC shear likelihoods were not run here, "
            "so this is not a publication-grade n-sigma claim."
        ),
    )

    assert not any(claim["status"] == "contradicted" for claim in report["claims"])
    assert not any(
        claim["status"] == "unsupported" and claim["kind"] == "numeric"
        for claim in report["claims"]
    )


def test_fact_verifier_does_not_block_spectra_likelihood_scope_gaps() -> None:
    from app.services.research_program import verify_research_facts

    report = verify_research_facts(
        tool_results=[],
        final_reply=(
            "No full Planck/ACT spectral covariance matrix was used in this run. "
            "This is not a primordial-feature constraint and requires a dedicated "
            "feature-template likelihood plus look-elsewhere calibration."
        ),
    )

    assert report["status"] in {"passed", "warning"}
    assert not any(claim["status"] == "contradicted" for claim in report["claims"])
    assert not any(
        claim["status"] == "unsupported" and claim["kind"] == "numeric"
        for claim in report["claims"]
    )


def test_fact_verifier_skips_future_external_chain_gap_statement() -> None:
    from app.services.research_program import verify_research_facts

    report = verify_research_facts(
        tool_results=[],
        final_reply=(
            "A full external Cobaya or CosmoSIS chain would be needed for "
            "publication-ready BAO+SN+CMB claims."
        ),
    )

    assert report["status"] in {"passed", "warning"}
    assert not any(claim["status"] == "contradicted" for claim in report["claims"])


def test_fact_verifier_blocks_unsupported_sigma_tension_claim() -> None:
    from app.services.research_program import (
        build_evidence_graph,
        plan_research_program,
        run_research_matrix,
        verify_research_facts,
    )

    plan = plan_research_program(
        question="Run a BAO and CMB robustness matrix without weak-lensing pairwise support."
    )["research_plan"]
    matrix = run_research_matrix(research_plan=plan, n_samples=512)
    graph = build_evidence_graph(tool_results=[{"tool": "run_research_matrix", "result": matrix}])
    report = verify_research_facts(
        tool_results=[
            {"tool": "run_research_matrix", "result": matrix},
            {"tool": "build_evidence_graph", "result": graph},
        ],
        final_reply="The current tools establish a 2.8σ S8 tension.",
    )

    assert any(
        claim["status"] == "unsupported" and claim["kind"] == "numeric"
        for claim in report["claims"]
    )


def test_fact_verifier_blocks_unsupported_p_value_claim() -> None:
    from app.services.research_program import verify_research_facts

    report = verify_research_facts(
        tool_results=[],
        final_reply="The correlation is significant with p < 0.01.",
    )

    assert any(
        claim["status"] == "unsupported" and claim["kind"] == "numeric"
        for claim in report["claims"]
    )


def test_fact_verifier_blocks_numeric_value_contradicting_ready_chain() -> None:
    from app.services.research_program import verify_research_facts

    tool_results = [
        {
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "success": True,
                "publication_ready": True,
                "claim_scope": "compressed_likelihood_preliminary",
                "parameters": {
                    "H0": {"median": 68.1, "hdi_94": [67.5, 68.7]},
                    "omegam": {"median": 0.31, "hdi_94": [0.29, 0.33]},
                },
                "datasets_used": [
                    {"key": "desi_dr1_bao", "display_name": "DESI DR1 BAO"},
                ],
            },
        }
    ]

    report = verify_research_facts(
        tool_results=tool_results,
        final_reply=(
            "The compressed-likelihood preliminary result gives "
            "H0 = 74.0 km/s/Mpc and Ωm = 0.31."
        ),
    )

    assert report["status"] == "blocked"
    assert any(
        claim["status"] == "contradicted"
        and "H0" in claim["text"]
        and "current-turn tool value" in claim["safe_rewrite"]
        for claim in report["claims"]
    )
    assert any(claim["status"] == "verified" and "Ωm" in claim["text"] for claim in report["claims"])


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
                "configuration files, chain files, and the full external likelihood "
                "package for reproducibility."
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


def test_export_research_report_includes_bibtex_and_manifest() -> None:
    from app.services.research_program import export_research_report

    result = export_research_report(
        research_plan={
            "research_question": "BAO + CMB consistency",
            "proposed_experiment_matrix": [
                {"label": "CMB", "dataset_keys": ["planck2018_compressed"], "model": "lcdm"}
            ],
            "blocking_gaps": ["Full Planck likelihood not run."],
        },
        evidence_graph={"claimable_parameters": ["H0", "omegam"]},
        tool_results=[
            {
                "tool": "run_cosmology_likelihood_chain",
                "result": {
                    "analysis_status": "COMPRESSED_CHAIN_READY",
                    "publication_ready": True,
                    "datasets_used": [
                        {
                            "key": "planck2018_compressed",
                            "display_name": "Planck 2018 compressed",
                            "version": "2018",
                            "source_url": "https://pla.esac.esa.int/",
                            "citations": [
                                {"label": "Planck Collaboration VI", "year": 2020, "arxiv": "1807.06209"}
                            ],
                        }
                    ],
                    "reproducibility": {
                        "run_id": "run-1",
                        "query_hash": "abc",
                        "tool_version": "test",
                    },
                },
            }
        ],
    )

    assert result["analysis_status"] == "RESEARCH_REPORT_READY"
    assert "Planck 2018 compressed" in result["markdown"]
    assert "1807.06209" in result["bibtex"]
    assert result["datasets"][0]["key"] == "planck2018_compressed"
    assert result["reproducibility_manifest"][0]["run_id"] == "run-1"
    assert result["report_package"]["files"][0]["path"] == "research_report.md"
    assert any(file["path"] == "paper_draft.md" for file in result["report_package"]["files"])
    assert any(file["path"] == "fact_check_report.json" for file in result["report_package"]["files"])
    assert "## Fact Verification" in result["markdown"]
    assert "## 4. Results" in result["paper_draft_markdown"]
    assert "Planck 2018 compressed" in result["paper_draft_markdown"]


def test_tool_gap_matrix_knows_research_export_package_is_available() -> None:
    from app.services.paper_tool_mining import build_tool_gap_matrix

    matrix = build_tool_gap_matrix(
        tool_specs=[
                {
                    "tool_category": "exporter",
                    "canonical_capability": "research_export",
                    "implementation_status": "available",
                    "source_spans": [{"section": "Data availability"}],
                }
        ]
    )

    row = matrix["gap_matrix"][0]
    assert row["current_status"] == "available"
    assert "export_research_report" in row["available_platform_tools"]


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
        sort_by="relevance",
    ))

    assert result["analysis_status"] == "PAPER_MINING_CANDIDATE_POOL_READY"
    assert result["candidate_count"] == 2
    assert result["live_search_enabled"] is False
    assert result["sort_by"] == "relevance"
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
