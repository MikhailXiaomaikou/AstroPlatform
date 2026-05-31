import pytest


def test_registry_contains_required_observational_cosmology_datasets():
    from app.services.cosmology_likelihoods import list_cosmology_datasets

    registry = list_cosmology_datasets()
    keys = {entry["key"] for entry in registry["datasets"]}

    assert {
        "desi_dr1_bao",
        "sdss_6df_bao",
        "pantheon_plus",
        "des_sn5yr",
        "union3",
        "planck2018_compressed",
        "act_dr6_lensing",
        "kids1000_wl",
        "des_y3_3x2pt",
        "hsc_y1_cosmic_shear",
        "cosmic_chronometers",
        "shoes_h0_riess22",
    } <= keys
    assert registry["dataset_count"] >= 12

    for entry in registry["datasets"]:
        assert entry["version"]
        assert entry["source_url"].startswith(("http://", "https://"))
        assert entry["citations"]
        assert entry["covariance"]["kind"]
        assert entry["units"]
        assert entry["applicable_models"]
        assert entry["execution_mode"] in {
            "config_only",
            "compressed_gaussian",
            "external_cobaya",
            "external_cosmosis",
        }


def test_priority_datasets_expose_machine_readable_data_products():
    from app.services.cosmology_likelihoods import list_cosmology_datasets

    registry = list_cosmology_datasets(
        dataset_keys=["desi_dr1_bao", "pantheon_plus", "planck2018_compressed"]
    )
    entries = {entry["key"]: entry for entry in registry["datasets"]}

    desi_products = entries["desi_dr1_bao"]["data_products"]
    assert any(
        product["role"] == "measurement_vector"
        and "desi_2024_gaussian_bao_ALL_GCcomb_mean.txt" in product["url"]
        and product["sha256"] == "dd2873a0b88459a491af3c0c0307ba059f62df9211d5b976760f310565a1be68"
        for product in desi_products
    )
    assert any(
        product["role"] == "covariance"
        and "desi_2024_gaussian_bao_ALL_GCcomb_cov.txt" in product["url"]
        and product["sha256"] == "bbafa9074b51cf1a45e0d10e4f37db8c0e80a5d1d1788857abb7fc49fb21abcc"
        for product in desi_products
    )

    pantheon_products = entries["pantheon_plus"]["data_products"]
    assert any(product["role"] == "data_table" and "Pantheon%2BSH0ES.dat" in product["url"] for product in pantheon_products)
    assert any(product["role"] == "covariance" and "STAT%2BSYS.cov" in product["url"] for product in pantheon_products)
    assert any(product["role"] == "likelihood_code" and "cosmosis_likelihood.py" in product["url"] for product in pantheon_products)

    planck_products = entries["planck2018_compressed"]["data_products"]
    assert any(product["role"] == "likelihood_code" and "Likelihood_Code" in product["url"] for product in planck_products)
    assert any(product["role"] == "compressed_prior_table" and product["url"] == "https://arxiv.org/abs/1808.05724" for product in planck_products)


def test_registry_can_list_only_requested_dataset_keys_in_order():
    from app.services.cosmology_likelihoods import list_cosmology_datasets

    registry = list_cosmology_datasets(
        dataset_keys=["pantheon_plus", "desi_dr1_bao", "not_a_dataset"]
    )

    assert [entry["key"] for entry in registry["datasets"]] == ["pantheon_plus", "desi_dr1_bao"]
    assert registry["requested_dataset_keys"] == [
        "pantheon_plus",
        "desi_dr1_bao",
        "not_a_dataset",
    ]
    assert registry["unknown_dataset_keys"] == ["not_a_dataset"]


@pytest.mark.asyncio
async def test_load_cosmology_data_product_parses_registered_table_and_covariance():
    from app.services.cosmology_data_products import load_cosmology_data_product

    table = await load_cosmology_data_product(
        dataset_key="desi_dr1_bao",
        role="measurement_vector",
        allow_network=False,
        content_override="# z value quantity\n0.30 7.9 DV_over_rd\n0.51 13.6 DM_over_rd\n",
    )
    assert table["analysis_status"] == "COSMOLOGY_DATA_PRODUCT_READY"
    assert table["parse"]["kind"] == "table"
    assert table["parse"]["row_count"] == 2
    assert table["parse"]["preview"][0]["z"] == pytest.approx(0.30)
    assert table["publication_ready"] is True

    cov = await load_cosmology_data_product(
        dataset_key="desi_dr1_bao",
        role="covariance",
        allow_network=False,
        content_override="1.0 0.2\n0.2 4.0\n",
    )
    assert cov["analysis_status"] == "COSMOLOGY_DATA_PRODUCT_READY"
    assert cov["parse"]["kind"] == "matrix"
    assert cov["parse"]["shape"] == [2, 2]
    assert cov["parse"]["symmetric"] is True
    assert cov["parse"]["positive_diagonal"] is True


@pytest.mark.asyncio
async def test_load_cosmology_data_product_parses_dimension_prefixed_sn_covariance():
    from app.services.cosmology_data_products import load_cosmology_data_product

    result = await load_cosmology_data_product(
        dataset_key="pantheon_plus",
        role="covariance",
        allow_network=False,
        content_override="2\n1.0\n0.1\n0.1\n4.0\n",
    )

    assert result["analysis_status"] == "COSMOLOGY_DATA_PRODUCT_READY"
    assert result["parse"]["kind"] == "matrix"
    assert result["parse"]["format_detected"] == "dimension_prefixed_flat_covariance"
    assert result["parse"]["shape"] == [2, 2]
    assert result["parse"]["symmetric"] is True


@pytest.mark.asyncio
async def test_load_cosmology_data_product_exposes_registered_compressed_likelihood():
    from app.services.cosmology_data_products import load_cosmology_data_product

    result = await load_cosmology_data_product(
        dataset_key="act_dr6_lensing",
        role="compressed_likelihood",
        allow_network=False,
    )

    assert result["analysis_status"] == "COSMOLOGY_COMPRESSED_DATA_PRODUCT_READY"
    assert result["publication_ready"] is True
    assert result["parse"]["kind"] == "compressed_gaussian_likelihood"
    assert "S8" in result["parse"]["parameters"]
    assert result["claim_scope"] == "registered_compressed_likelihood_data_product"


@pytest.mark.asyncio
async def test_load_cosmology_data_product_reports_unavailable_without_network_or_content():
    from app.services.cosmology_data_products import load_cosmology_data_product

    result = await load_cosmology_data_product(
        dataset_key="desi_dr1_bao",
        role="measurement_vector",
        allow_network=False,
    )

    assert result["analysis_status"] == "COSMOLOGY_DATA_PRODUCT_UNAVAILABLE"
    assert result["__tool_status__"] == "UNAVAILABLE"
    assert result["publication_ready"] is False


def test_compressed_likelihood_runner_combines_planck_act_and_wl_s8_constraints():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=[
            "planck2018_compressed",
            "act_dr6_lensing",
            "kids1000_wl",
            "des_y3_3x2pt",
            "hsc_y1_cosmic_shear",
        ],
        random_seed=123,
        n_samples=1000,
    )

    assert result["success"] is True
    assert result["publication_ready"] is True
    assert result["analysis_status"] == "COMPRESSED_CHAIN_READY"
    assert result["claim_scope"] == "compressed_likelihood_preliminary"
    assert set(result["parameters"]) >= {"H0", "omegam", "sigma8", "S8"}
    assert result["parameters"]["S8"]["median"] == pytest.approx(0.804, abs=0.02)
    assert result["chain_diagnostics"]["rhat"] == 1.0
    assert result["fit_statistics"]["aic"] > 0
    assert any(item["parameter"] == "S8" for item in result["pairwise_tensions"])
    assert len(result["datasets_used"]) == 5


def test_pairwise_tensions_compare_direct_s8_to_derived_sigma8_omegam():
    from app.services.cosmology_likelihoods import (
        CompressedLikelihoodSpec,
        CosmologyDatasetEntry,
        CovarianceSpec,
        DatasetCitation,
        _pairwise_tensions,
    )

    base_kwargs = {
        "version": "test",
        "status": "ready",
        "observables": ("S8",),
        "units": {"S8": "dimensionless"},
        "applicable_models": ("lcdm",),
        "likelihood_family": "compressed_gaussian",
        "covariance": CovarianceSpec(kind="gaussian", provided=True, description="test"),
        "source_url": "https://example.invalid",
        "citations": (DatasetCitation(label="Test", year=2026),),
        "notes": "test fixture",
        "execution_mode": "compressed_gaussian",
    }
    wl = CosmologyDatasetEntry(
        key="wl_s8",
        display_name="WL S8",
        probe="weak_lensing",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("S8",),
            mean=(0.760,),
            covariance=((0.02**2,),),
            source_locator="test",
            approximation="test",
        ),
        **base_kwargs,
    )
    cmb = CosmologyDatasetEntry(
        key="cmb_sigma8_omegam",
        display_name="CMB sigma8/Omega_m",
        probe="cmb",
        observables=("sigma8", "omegam"),
        units={"sigma8": "dimensionless", "omegam": "dimensionless"},
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("sigma8", "omegam"),
            mean=(0.810, 0.300),
            covariance=((0.01**2, 0.0), (0.0, 0.01**2)),
            source_locator="test",
            approximation="test",
        ),
        **{k: v for k, v in base_kwargs.items() if k not in {"observables", "units"}},
    )

    tensions = _pairwise_tensions([wl, cmb])
    s8 = next(item for item in tensions if item["parameter"] == "S8")

    assert s8["comparison"] == "derived_pairwise"
    assert s8["value_a_source"] == "direct"
    assert s8["value_b_source"] == "derived_from_sigma8_omegam"
    assert s8["value_b"] == pytest.approx(0.810)
    assert s8["sigma"] > 1.0


def test_desi_dr1_bao_data_product_runner_produces_publication_ready_preliminary_chain():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao"],
        random_seed=123,
        n_samples=512,
    )

    assert result["success"] is True
    assert result["publication_ready"] is True
    assert result["analysis_status"] == "COMPRESSED_CHAIN_READY"
    assert result["sampler"] == "bao_gaussian_importance"
    assert result["claim_scope"] == "compressed_likelihood_preliminary"
    assert [entry["key"] for entry in result["datasets_used"]] == ["desi_dr1_bao"]
    assert result["datasets_not_run"] == []
    assert set(result["parameters"]) == {"H0", "omegam", "rd"}
    assert result["parameters"]["omegam"]["median"] == pytest.approx(0.294, abs=0.03)
    assert result["fit_statistics"]["n_constraints"] == 12
    assert result["chain_diagnostics"]["proposal_ess"] >= 400
    assert "desilike/Cobaya" in result["warnings"][0]
    assert "prior/calibration dependent" in " ".join(result["warnings"])
    sources = result["provenance"]["cosmology_likelihood"]["compressed_sources"]
    assert sources[0]["dataset_key"] == "desi_dr1_bao"
    assert any(
        product["role"] == "measurement_vector"
        and product["sha256"] == "dd2873a0b88459a491af3c0c0307ba059f62df9211d5b976760f310565a1be68"
        for product in sources[0]["data_products"]
    )


def test_compressed_likelihood_runner_keeps_config_only_datasets_out_of_posterior():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["spt3g_cmb", "planck2018_compressed"],
        random_seed=123,
        n_samples=512,
    )

    assert result["publication_ready"] is False
    assert result["chain_tier"] == "blocked"
    assert result["__do_not_claim__"] is True
    assert [entry["key"] for entry in result["datasets_used"]] == ["planck2018_compressed"]
    assert [entry["key"] for entry in result["datasets_not_run"]] == ["spt3g_cmb"]
    assert "not run in compressed phase" in " ".join(result["warnings"])


def test_compressed_likelihood_runner_refuses_extended_model_publication_claims():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm_mnu",
        dataset_keys=["planck2018_compressed", "act_dr6_lensing"],
    )

    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True
    assert "extended-model parameters" in result["warnings"][0]


def test_likelihood_builder_emits_guarded_cobaya_and_cosmosis_config():
    from app.services.cosmology_likelihoods import build_likelihood_config

    result = build_likelihood_config(
        model="w0wa_cdm",
        dataset_keys=["desi_dr1_bao", "pantheon_plus", "planck2018_compressed"],
        priors={"w0": [-1.5, -0.4], "wa": [-2.0, 2.0]},
    )

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True
    assert result["analysis_status"] == "CONFIG_READY"
    assert result["model"] == "w0wa_cdm"
    assert result["config_hash"]
    assert "cobaya" in result
    assert "cosmosis" in result
    assert set(result["cobaya"]["likelihood"]) == {
        "desi_dr1_bao",
        "pantheon_plus",
        "planck2018_compressed",
    }
    assert result["priors"]["w0"] == [-1.5, -0.4]
    assert "DESI Collaboration" in str(result["provenance"]["cosmology_likelihood"]["citations"])


def test_likelihood_builder_can_plan_act_era_bao_and_weak_lensing_comparison():
    from app.services.cosmology_likelihoods import build_likelihood_config

    result = build_likelihood_config(
        model="lcdm",
        dataset_keys=[
            "sdss_6df_bao",
            "planck2018_compressed",
            "act_dr6_lensing",
            "kids1000_wl",
            "des_y3_3x2pt",
            "hsc_y1_cosmic_shear",
        ],
    )

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True
    assert set(result["cobaya"]["likelihood"]) == {
        "sdss_6df_bao",
        "planck2018_compressed",
        "act_dr6_lensing",
        "kids1000_wl",
        "des_y3_3x2pt",
        "hsc_y1_cosmic_shear",
    }
    citations = str(result["provenance"]["cosmology_likelihood"]["citations"])
    assert "Aubourg" in citations
    assert "KiDS-1000" in citations
    assert "DES Collaboration" in citations
    assert "HSC Y1" in citations


def test_likelihood_builder_rejects_unsupported_prior_and_duplicate_dataset():
    from app.services.cosmology_likelihoods import build_likelihood_config

    with pytest.raises(ValueError, match="unsupported parameters"):
        build_likelihood_config(
            model="lcdm",
            dataset_keys=["desi_dr1_bao"],
            priors={"w0": [-1.2, -0.8]},
        )

    with pytest.raises(ValueError, match="duplicates"):
        build_likelihood_config(
            model="lcdm",
            dataset_keys=["desi_dr1_bao", "desi_dr1_bao"],
        )


def test_robustness_matrix_generates_bao_sn_cmb_h0_variants():
    from app.services.cosmology_likelihoods import build_robustness_matrix

    matrix = build_robustness_matrix(
        model="w0wa_cdm",
        supernova_sets=["pantheon_plus", "union3"],
        include_h0_prior=True,
    )

    labels = {row["label"] for row in matrix["matrix"]}

    assert matrix["success"] is True
    assert matrix["publication_ready"] is False
    assert matrix["matrix_size"] == 22
    assert "BAO only" in labels
    assert "BAO only + SH0ES H0" in labels
    assert "SN only" in labels
    assert "CMB only" in labels
    assert "Pantheon+ + CMB" in labels
    assert "BAO + Pantheon+" in labels
    assert "BAO + Pantheon+ + CMB + SH0ES H0" in labels
    assert all(row["requires_chain_run"] for row in matrix["matrix"])


def test_executed_robustness_matrix_only_adds_weak_lensing_when_requested():
    from app.services.cosmology_likelihoods import run_robustness_matrix

    without_wl = run_robustness_matrix(
        model="lcdm",
        supernova_sets=["pantheon_plus"],
        include_h0_prior=False,
        include_weak_lensing=False,
    )
    with_wl = run_robustness_matrix(
        model="lcdm",
        supernova_sets=["pantheon_plus"],
        include_h0_prior=False,
        include_weak_lensing=True,
    )

    labels_without = {row["label"] for row in without_wl["matrix"]}
    labels_with = {row["label"] for row in with_wl["matrix"]}

    assert "BAO + CMB + weak lensing" not in labels_without
    assert "BAO + CMB + weak lensing" in labels_with


@pytest.mark.asyncio
async def test_bao_bin_anomaly_ai_tool_wraps_ap_diagnostic():
    from app.services.ai_tools import execute_tool

    result = await execute_tool(
        "assess_bao_bin_anomaly",
        {"omega_m_grid": [0.1, 0.5, 101]},
        python_session_id="test",
    )

    assert result["success"] is True
    assert result["publication_ready"] is True
    assert result["analysis_status"] == "ALCOCK_PACZYNSKI_READY"
    assert result["n_redshift_pairs"] >= 5
    assert result["provenance"]["alcock_paczynski"]["input_dataset"] == "desi_dr1_bao"


def test_compressed_runner_reports_no_executable_likelihood_reason():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    # des_sn5yr / union3 became executable (Tier 2A, compressed SN-only Ωm), so
    # spt3g_cmb (full TT/TE/EE, still external_cobaya) is now the config-only
    # exemplar for verifying the runner reports a not-run dataset.
    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["pantheon_plus", "spt3g_cmb"],
    )

    assert result["publication_ready"] is False
    assert result["analysis_status"] == "PARTIAL"
    assert result["chain_tier"] == "blocked"
    assert [entry["key"] for entry in result["datasets_used"]] == ["pantheon_plus"]
    assert [entry["key"] for entry in result["datasets_not_run"]] == ["spt3g_cmb"]
    assert "Datasets not run in compressed phase" in result["warnings"][1]


@pytest.mark.asyncio
async def test_ai_tool_wrappers_expose_registry_and_config_guardrails():
    from app.services.ai_tools import execute_tool

    listed = await execute_tool("list_cosmology_datasets", {"probe": "sn"}, python_session_id="test")
    assert listed["success"] is True
    assert {entry["key"] for entry in listed["datasets"]} >= {
        "pantheon_plus",
        "des_sn5yr",
        "union3",
    }

    selected = await execute_tool(
        "list_cosmology_datasets",
        {"dataset_keys": ["desi_dr1_bao", "planck2018_compressed"]},
        python_session_id="test",
    )
    assert [entry["key"] for entry in selected["datasets"]] == [
        "desi_dr1_bao",
        "planck2018_compressed",
    ]

    loaded = await execute_tool(
        "load_cosmology_data_product",
        {"dataset_key": "desi_dr1_bao", "role": "measurement_vector", "allow_network": False},
        python_session_id="test",
    )
    assert loaded["__tool_status__"] == "UNAVAILABLE"

    config = await execute_tool(
        "build_cosmology_likelihood",
        {"model": "lcdm", "dataset_keys": ["desi_dr1_bao", "shoes_h0_riess22"]},
        python_session_id="test",
    )
    assert config["success"] is True
    assert config["__tool_status__"] == "PARTIAL"
    assert config["publication_ready"] is False

    chain = await execute_tool(
        "run_cosmology_likelihood_chain",
        {"model": "lcdm", "dataset_keys": ["planck2018_compressed", "shoes_h0_riess22"]},
        python_session_id="test",
    )
    assert chain["success"] is True
    assert chain["publication_ready"] is True
    assert set(chain["parameters"]) >= {"H0", "omegam", "sigma8", "S8"}


# ── PART AI follow-up: spec papers #12-#15 H0 ladder + SPT-3G CMB ──────


def test_trgb_freedman19_h0_prior_registered() -> None:
    """spec paper #15: TRGB Freedman+ 2019 H0 = 69.8 ± 1.9 km/s/Mpc.
    Distance-ladder anchor between SH0ES (Cepheid) and Planck (CMB inverse)."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("trgb_h0_freedman19")
    assert entry.probe == "h0_prior"
    assert entry.execution_mode == "compressed_gaussian"
    cl = entry.compressed_likelihood
    assert cl is not None
    assert cl.parameters == ("H0",)
    assert cl.mean == (69.8,)
    assert cl.covariance == ((1.9 ** 2,),)
    arxivs = [c.arxiv for c in entry.citations if c.arxiv]
    assert "1907.05922" in arxivs


def test_h0licow_h0_prior_registered_with_symmetric_sigma() -> None:
    """spec paper #13: H0LiCOW XIII Wong+ 2020 H0 = 73.3 +1.7/-1.8.
    We use a symmetric Gaussian approximation with σ = 1.75."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("h0licow_h0")
    cl = entry.compressed_likelihood
    assert cl.mean == (73.3,)
    assert cl.covariance == ((1.75 ** 2,),)
    arxivs = [c.arxiv for c in entry.citations if c.arxiv]
    assert "1907.04869" in arxivs
    # The approximation field must explicitly state sigma is symmetrized (to avoid reviewers assuming a true 1D Gaussian)
    assert "symmetr" in cl.approximation.lower()


def test_megamaser_pesce20_h0_prior_registered() -> None:
    """spec paper #14: Pesce+ 2020 megamaser H0 = 73.9 ± 3.0 — geometric
    anchor, fully independent of the Cepheid/TRGB/SN Ia distance ladder."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("megamaser_h0_pesce20")
    cl = entry.compressed_likelihood
    assert cl.mean == (73.9,)
    assert cl.covariance == ((3.0 ** 2,),)
    arxivs = [c.arxiv for c in entry.citations if c.arxiv]
    assert "2001.09213" in arxivs
    # notes or approximation must explicitly state "geometric anchor / independent of distance ladder"
    note_blob = (entry.notes or "").lower() + (cl.approximation or "").lower()
    assert "geometric" in note_blob or "anchor" in note_blob


def test_spt3g_cmb_external_likelihood_registered() -> None:
    """spec paper #12: SPT-3G Balkenhol+ 2023 TT/TE/EE damping-tail.
    External Cobaya likelihood (cannot be compressed into a low-dimensional Gaussian; full power
    spectrum data product)."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("spt3g_cmb")
    assert entry.probe == "cmb"
    assert entry.execution_mode == "external_cobaya"
    assert entry.likelihood_family == "cmb_powerspectrum"
    # Must include the full TT/TE/EE triple observable
    assert set(entry.observables) >= {"TT", "TE", "EE"}
    arxivs = [c.arxiv for c in entry.citations if c.arxiv]
    assert "2212.05642" in arxivs
    # Must include at least a few standard nuisance parameters (kappa / dust)
    assert "kappa" in entry.nuisance_parameters


def test_all_4_h0_anchors_share_observable_and_models() -> None:
    """All 4 H0 anchors (TRGB / SH0ES / H0LiCOW / Megamaser) must
    expose H0 as the sole observable, and applicable_models must include
    lcdm/wcdm/w0wa_cdm (H0 prior is model-independent)."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    h0_anchors = [
        "shoes_h0_riess22",
        "trgb_h0_freedman19",
        "h0licow_h0",
        "megamaser_h0_pesce20",
    ]
    for key in h0_anchors:
        entry = get_cosmology_dataset(key)
        assert entry.observables == ("H0",), f"{key} observables wrong"
        assert "lcdm" in entry.applicable_models
        assert "wcdm" in entry.applicable_models
        assert "w0wa_cdm" in entry.applicable_models


def test_h0_anchor_means_span_known_tension_range() -> None:
    """The 4 H0 anchor mean values together must span the 'H0 tension interval' 69-74,
    so cosmology_mcmc can use them as cross-checks against each other."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    means = []
    for key in ("trgb_h0_freedman19", "shoes_h0_riess22",
                "h0licow_h0", "megamaser_h0_pesce20"):
        entry = get_cosmology_dataset(key)
        means.append(entry.compressed_likelihood.mean[0])
    assert min(means) <= 70.0   # TRGB end
    assert max(means) >= 73.0   # SH0ES / H0LiCOW / Megamaser end
    # Span of ~3-4 km/s/Mpc, i.e., the real tension interval
    assert max(means) - min(means) >= 3.0


# ── PART AI Phase 5: SPT-SZ cluster cosmology (Bocquet+ 2019) ────────


def test_spt_cluster_bocquet19_registered_with_sigma8_sz_constraint() -> None:
    """spec paper #19 — SPT-SZ Bocquet+ 2019 σ8(Ωm/0.3)^0.2 = 0.766 ± 0.025
    cluster-count cosmology. Independent σ8 anchor (no weak lensing,
    no CMB inverse). Compressed 2D Gaussian σ8 × Ωm with ρ=-0.6
    typical SZ degeneracy slope."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("spt_cluster_bocquet19")
    assert entry.probe == "cluster"
    assert entry.execution_mode == "compressed_gaussian"
    assert entry.likelihood_family == "cluster_count"
    cl = entry.compressed_likelihood
    assert cl is not None
    # Note: parameter names are lowercase (matches RUNNER_PARAMETER_PRIORS convention).
    # The published Bocquet+19 paper uses uppercase Ω_m; the platform internal schema uses omegam.
    assert cl.parameters == ("sigma8", "omegam")
    # sigma8 = 0.766 (Bocquet+19 baseline)
    assert abs(cl.mean[0] - 0.766) < 1e-6
    # Omegam = 0.300 (Bocquet+19 fiducial)
    assert abs(cl.mean[1] - 0.300) < 1e-6
    # Diagonal σ correctly recovered: σ_σ8 = 0.025, σ_Ωm = 0.05
    import math
    assert math.isclose(math.sqrt(cl.covariance[0][0]), 0.025, abs_tol=1e-6)
    assert math.isclose(math.sqrt(cl.covariance[1][1]), 0.050, abs_tol=1e-6)
    # rho = -0.6 SZ degeneracy slope direction
    rho = cl.covariance[0][1] / (
        math.sqrt(cl.covariance[0][0]) * math.sqrt(cl.covariance[1][1])
    )
    assert math.isclose(rho, -0.6, abs_tol=1e-6)

    arxivs = [c.arxiv for c in entry.citations if c.arxiv]
    assert "1812.01679" in arxivs


def test_spt_cluster_does_not_share_observables_with_weak_lensing() -> None:
    """SPT cluster must be **independent** of weak lensing — observables must not include
    xi_plus / xi_minus / S8 (those are cosmic shear fields). This is the key to its role
    as an independent sigma8 tension anchor."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("spt_cluster_bocquet19")
    assert "xi_plus" not in entry.observables
    assert "xi_minus" not in entry.observables
    # sigma8 sharing is OK (kappa-shear also outputs sigma8); omegam likewise (lowercase per
    # RUNNER_PARAMETER_PRIORS convention)
    assert "sigma8" in entry.observables
    assert "omegam" in entry.observables


def test_spt_cluster_chain_runner_combines_with_weak_lensing() -> None:
    """SPT cluster + KiDS-1000 + DES Y3 + HSC Y1 joint chain must be runnable
    (main sigma8 tension cross-check workflow). All 5 datasets are on the compressed_gaussian
    path; the runner should accept them."""
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=[
            "spt_cluster_bocquet19",
            "kids1000_wl",
            "des_y3_3x2pt",
            "hsc_y1_cosmic_shear",
            "planck2018_compressed",
        ],
        random_seed=20260503,
        n_samples=600,
    )
    assert result["success"] is True
    assert result["publication_ready"] is True
    # sigma8 must be in the fit parameters
    assert "sigma8" in result["parameters"]
    assert "S8" in result["parameters"]
    # All 5 datasets must participate in the fit
    assert len(result["datasets_used"]) == 5
    used_keys = {entry["key"] for entry in result["datasets_used"]}
    assert "spt_cluster_bocquet19" in used_keys


def test_spt_cluster_alone_chain_returns_2d_constraint() -> None:
    """Running SPT cluster alone must return a sigma8/Omegam two-parameter posterior, not empty."""
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["spt_cluster_bocquet19"],
        random_seed=20260503,
        n_samples=400,
    )
    assert result["success"] is True
    assert "sigma8" in result["parameters"]
    assert "omegam" in result["parameters"]
    # sigma8 median should be approximately 0.766 (compressed Gaussian center)
    sigma8_param = result["parameters"]["sigma8"]
    assert abs(sigma8_param["median"] - 0.766) < 0.05


# ── PART AI Phase 5: eBOSS DR16 RSD f·σ8 multi-z compilation ────────


def test_eboss_dr16_rsd_registered() -> None:
    """spec paper #6 RSD growth-rate side: eBOSS DR16 RSD compilation
    (Alam+ 2021) covering z=0.15..1.48 (RSD-only fσ8), complement to BAO
    distance ratios. Independent of weak-lensing σ8 (1+z snapshot) AND cluster
    σ8 (M-T counting) — third axis of σ8 tension cross-check."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("eboss_dr16_rsd")
    assert entry.probe == "rsd"
    assert entry.likelihood_family == "gaussian_rsd"
    # 2026-05-29 (1A): now executable in-process via the Linder-γ growth kernel
    # (fσ8 = f·σ8·D(z)/D(0)), so execution_mode flipped external_cobaya →
    # compressed_gaussian. status stays "external_likelihood" (the DESI-BAO
    # convention for an executable dedicated-path probe whose full external
    # likelihood is higher fidelity).
    assert entry.execution_mode == "compressed_gaussian"
    assert entry.observables == ("f_sigma8",)
    assert entry.status == "external_likelihood"


def test_eboss_dr16_rsd_citations_cover_all_7_z_bins() -> None:
    """7 z-bin compilation must cite each survey's published RSD paper:
    6dFGS / BOSS / 4 eBOSS sub-samples (LRG / ELG / QSO / Lyα) +
    summary cosmology paper."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("eboss_dr16_rsd")
    arxivs = {c.arxiv for c in entry.citations if c.arxiv}
    # 6dFGS RSD (Beutler+ 2012)
    assert "1204.4725" in arxivs
    # BOSS DR12 consensus (Alam+ 2017)
    assert "1607.03155" in arxivs
    # eBOSS LRG (Bautista+ 2021)
    assert "2007.08993" in arxivs
    # eBOSS ELG (de Mattia+ 2021)
    assert "2007.09008" in arxivs
    # eBOSS QSO (Hou+ 2021)
    assert "2007.08998" in arxivs
    # eBOSS Lyα (du Mas des Bourboux+ 2020)
    assert "2007.08995" in arxivs
    # eBOSS DR16 summary cosmology (Alam+ 2021)
    assert "2007.08991" in arxivs


def test_eboss_dr16_rsd_complements_sdss_6df_bao_independently() -> None:
    """sdss_6df_bao and eboss_dr16_rsd should be **independent dataset entries** —
    users can choose BAO-only / RSD-only / BAO+RSD joint, without forcing RSD and BAO
    to be bound together in a single entry."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    bao = get_cosmology_dataset("sdss_6df_bao")
    rsd = get_cosmology_dataset("eboss_dr16_rsd")

    # Different probes
    assert bao.probe == "bao"
    assert rsd.probe == "rsd"
    # Different likelihood families
    assert bao.likelihood_family == "gaussian_bao"
    assert rsd.likelihood_family == "gaussian_rsd"
    # Different observables (BAO uses distance ratios, RSD uses f·sigma8)
    assert "f_sigma8" not in bao.observables
    assert "DM_over_rd" not in rsd.observables


def test_eboss_dr16_rsd_nuisance_parameters_cover_per_subsample_systematics() -> None:
    """Each RSD analysis sub-sample has an independent systematic correction
    (modeling error in the non-linear matter power spectrum). All 5 eBOSS
    + BOSS sub-samples require nuisance parameters:"""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("eboss_dr16_rsd")
    nuisance = set(entry.nuisance_parameters)
    # Must include at least 5 sub-sample systematics: LOWZ / CMASS / LRG / ELG / QSO
    assert any("LOWZ" in n for n in nuisance)
    assert any("CMASS" in n for n in nuisance)
    assert any("LRG" in n for n in nuisance)
    assert any("ELG" in n for n in nuisance)
    assert any("QSO" in n for n in nuisance)


def test_single_cell_emcee_fallback_upgrades_collapsed_3probe() -> None:
    """3-probe importance ESS collapses (~38); allow_emcee_fallback upgrades the
    single-cell chain to compressed-emcee and recovers a quotable posterior."""
    from app.services.cosmology_likelihoods import run_likelihood_chain

    keys = ["desi_dr1_bao", "pantheon_plus", "planck2018_compressed"]

    # Matrix / default path: fast importance sampling, ESS collapses on the
    # 3-probe product, so the cell is not publication-ready.
    fast = run_likelihood_chain(
        model="lcdm", dataset_keys=keys, random_seed=42, n_samples=4000
    )
    assert fast["sampler"] == "bao_gaussian_importance"
    assert fast["publication_ready"] is False

    # Single-cell deep run: emcee upgrade clears the ESS floor.
    deep = run_likelihood_chain(
        model="lcdm",
        dataset_keys=keys,
        random_seed=42,
        n_samples=4000,
        allow_emcee_fallback=True,
    )
    assert deep["sampler"] == "compressed_emcee"
    assert deep["publication_ready"] is True
    assert deep["chain_tier"] == "publication"
    assert deep["chain_diagnostics"]["ess_bulk"] >= 400
    assert deep["chain_diagnostics"]["overall_status"] == "emcee_sampled"


def test_emcee_fallback_skips_when_importance_ess_sufficient() -> None:
    """2-probe importance already clears the ESS floor, so enabling the fallback
    must not waste an emcee run — the fast importance path is kept."""
    from app.services.cosmology_likelihoods import run_likelihood_chain

    deep = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
        random_seed=42,
        n_samples=4000,
        allow_emcee_fallback=True,
    )
    assert deep["sampler"] == "bao_gaussian_importance"
    assert deep["publication_ready"] is True
