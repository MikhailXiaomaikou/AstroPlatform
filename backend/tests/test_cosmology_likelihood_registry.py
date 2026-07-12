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
    # Caller-provided fixture bytes are parseable but never hash-bound evidence.
    assert table["publication_ready"] is False

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
async def test_pantheon_table_header_maps_declared_columns_by_name():
    from app.services.cosmology_data_products import load_cosmology_data_product

    content = (
        "CID IDSURVEY zHD zCMB m_b_corr IS_CALIBRATOR CEPH_DIST "
        "MU_SH0ES MU_SH0ES_ERR_DIAG\n"
        "2011fe 51 0.00122 0.00122 9.74571 1 28.9987 28.9987 1.51645\n"
    )
    result = await load_cosmology_data_product(
        dataset_key="pantheon_plus",
        role="data_table",
        allow_network=False,
        content_override=content,
    )

    preview = result["parse"]["preview"][0]
    assert result["parse"]["header_detected"] is True
    assert result["parse"]["row_count"] == 1
    assert preview["CID"] == "2011fe"
    assert preview["zHD"] == pytest.approx(0.00122)
    assert preview["zCMB"] == pytest.approx(0.00122)
    assert result["publication_ready"] is False  # override + row-count mismatch


@pytest.mark.asyncio
async def test_load_cosmology_data_product_labels_posterior_summary_as_context():
    from app.services.cosmology_data_products import load_cosmology_data_product

    result = await load_cosmology_data_product(
        dataset_key="act_dr6_lensing",
        role="compressed_likelihood",
        allow_network=False,
    )

    assert result["analysis_status"] == "COSMOLOGY_COMPRESSED_RECORD_READY"
    assert result["structure_valid"] is True
    assert result["data_product_valid"] is True
    assert result["validation_scope"] == "registry_structure_only"
    assert result["publication_ready"] is False
    assert result["scientific_publication_ready"] is False
    assert result["hash_verified"] is False
    assert result["parse"]["kind"] == "published_posterior_summary"
    assert result["product"]["role"] == "literature_context"
    assert "S8" in result["parse"]["parameters"]
    assert result["claim_scope"] == "literature_context_metadata"
    assert result["__do_not_claim__"] is True


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


def test_published_posterior_summaries_are_not_multiplied_as_likelihoods():
    """ACT/WL posterior summaries stay context-only; only Planck's separately
    encoded distance-prior approximation is executed."""
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
    assert result["publication_ready"] is False
    assert result["chain_tier"] != "publication"
    joined = " ".join(result["warnings"])
    assert "must not be co-added" in joined
    assert "planck2018_compressed" in joined and "act_dr6_lensing" in joined
    assert {item["key"] for item in result["datasets_used"]} == {
        "planck2018_compressed"
    }
    assert {item["key"] for item in result["datasets_not_run"]} >= {
        "act_dr6_lensing",
        "kids1000_wl",
        "des_y3_3x2pt",
        "hsc_y1_cosmic_shear",
    }
    assert result["chain_tier"] == "blocked"
    assert result["__do_not_claim__"] is True
    assert "parameters" not in result


@pytest.mark.parametrize(
    "dataset_key",
    [
        "pantheon_plus",
        "des_sn5yr",
        "pantheon18",
        "act_dr6_lensing",
        "kids1000_wl",
        "des_y3_3x2pt",
        "hsc_y1_cosmic_shear",
    ],
)
def test_published_posterior_summary_cannot_run_as_joint_likelihood(dataset_key):
    from app.services.cosmology_likelihoods import (
        get_cosmology_dataset,
        run_likelihood_chain,
    )

    entry = get_cosmology_dataset(dataset_key)
    assert entry.compressed_likelihood is not None
    assert entry.compressed_likelihood.statistical_role == "published_posterior_summary"
    result = run_likelihood_chain(model="lcdm", dataset_keys=[dataset_key], n_samples=256)
    assert result["analysis_status"] == "NO_COMPRESSED_LIKELIHOOD"
    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True
    assert result["datasets_used"] == []
    assert {item["key"] for item in result["datasets_not_run"]} == {dataset_key}


def test_registry_audit_rejects_invalid_role_and_missing_source_prior():
    from dataclasses import replace

    from app.services.cosmology_likelihoods import get_cosmology_dataset
    from scripts.audit_registry import _audit_entry

    entry = get_cosmology_dataset("kids1000_wl")
    assert entry.compressed_likelihood is not None
    invalid_role = replace(
        entry,
        compressed_likelihood=replace(
            entry.compressed_likelihood,
            statistical_role="posterior_but_trust_me",  # type: ignore[arg-type]
        ),
    )
    missing_prior = replace(
        entry,
        compressed_likelihood=replace(
            entry.compressed_likelihood,
            source_prior=None,
        ),
    )

    assert any("invalid statistical_role" in issue for issue in _audit_entry(invalid_role))
    assert any("must disclose source_prior" in issue for issue in _audit_entry(missing_prior))


def test_compressed_execution_policy_fails_closed_by_statistical_role():
    from app.services.cosmology_likelihoods import get_cosmology_dataset
    from app.services.cosmology_likelihoods.sampling import (
        _compressed_entry_is_executable,
    )

    for key in ("kids1000_wl", "pantheon_plus", "act_dr6_lensing"):
        assert _compressed_entry_is_executable(get_cosmology_dataset(key)) is False
    assert _compressed_entry_is_executable(
        get_cosmology_dataset("shoes_h0_riess22")
    ) is True
    # The proposal-only parameter block is not executed; this key is the one
    # explicit exception because its separately encoded CHW2019 distance-prior
    # likelihood is dispatched by the sampling runner.
    planck = get_cosmology_dataset("planck2018_compressed")
    assert planck.compressed_likelihood is not None
    assert planck.compressed_likelihood.statistical_role == "proposal_only"
    assert _compressed_entry_is_executable(planck) is True


def test_low_level_chi2_helpers_cannot_bypass_context_only_role():
    import numpy as np

    from app.services.cosmology_likelihoods import (
        _combined_chi2,
        _s8_gaussian_constraints,
        get_cosmology_dataset,
    )
    from app.services.cosmology_likelihoods.cmb import compressed_entry_row_count

    kids = get_cosmology_dataset("kids1000_wl")
    assert _combined_chi2([kids], ["S8"], np.asarray([0.9])) == 0.0
    assert _s8_gaussian_constraints([kids]) == []
    assert compressed_entry_row_count(kids, ["S8"]) == 0


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
        independence_group="independent_wl_fixture",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("S8",),
            mean=(0.760,),
            covariance=((0.02**2,),),
            source_locator="test",
            approximation="test",
            statistical_role="likelihood_approximation",
        ),
        **base_kwargs,
    )
    cmb = CosmologyDatasetEntry(
        key="cmb_sigma8_omegam",
        display_name="CMB sigma8/Omega_m",
        probe="cmb",
        independence_group="independent_cmb_fixture",
        observables=("sigma8", "omegam"),
        units={"sigma8": "dimensionless", "omegam": "dimensionless"},
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("sigma8", "omegam"),
            mean=(0.810, 0.300),
            covariance=((0.01**2, 0.0), (0.0, 0.01**2)),
            source_locator="test",
            approximation="test",
            statistical_role="likelihood_approximation",
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


def test_pairwise_tensions_do_not_quantify_declared_overlap() -> None:
    from app.services.cosmology_likelihoods import (
        _pairwise_tensions,
        get_cosmology_dataset,
    )

    tensions = _pairwise_tensions([
        get_cosmology_dataset("planck2018_compressed"),
        get_cosmology_dataset("act_dr6_lensing"),
    ])

    assert {item["parameter"] for item in tensions} == {"H0", "sigma8", "S8"}
    assert all(item["status"] == "not_comparable" for item in tensions)
    assert all("do_not_combine_with" in item["non_independence_reasons"] for item in tensions)
    assert all("sigma" not in item and "delta" not in item for item in tensions)


def test_pairwise_tensions_withhold_sigma_when_independence_is_not_verified() -> None:
    from app.services.cosmology_likelihoods import (
        _pairwise_tensions,
        get_cosmology_dataset,
    )

    tensions = _pairwise_tensions([
        get_cosmology_dataset("kids1000_wl"),
        get_cosmology_dataset("hsc_y1_cosmic_shear"),
    ])

    assert len(tensions) == 1
    assert tensions[0]["parameter"] == "S8"
    assert tensions[0]["status"] == "not_comparable"
    assert tensions[0]["sigma"] is None
    assert tensions[0]["non_independence_reasons"] == [
        "independence_not_verified"
    ]


def test_pairwise_tensions_respect_known_overlap_and_independence_group() -> None:
    from dataclasses import replace

    from app.services.cosmology_likelihoods import (
        _pairwise_tensions,
        get_cosmology_dataset,
    )

    base_left = get_cosmology_dataset("kids1000_wl")
    base_right = get_cosmology_dataset("des_y3_3x2pt")
    cases = (
        (
            replace(base_left, key="known_left", known_overlap=("known_right",)),
            replace(base_right, key="known_right"),
            "known_overlap",
        ),
        (
            replace(base_left, key="group_left", independence_group="shared_wl"),
            replace(base_right, key="group_right", independence_group="shared_wl"),
            "shared_independence_group",
        ),
    )
    for left, right, reason in cases:
        tensions = _pairwise_tensions([left, right])
        assert len(tensions) == 1
        assert tensions[0]["parameter"] == "S8"
        assert tensions[0]["status"] == "not_comparable"
        assert reason in tensions[0]["non_independence_reasons"]
        assert "sigma" not in tensions[0]


def test_desi_dr1_bao_data_product_runner_produces_preliminary_chain():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao"],
        random_seed=123,
        n_samples=512,
    )

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is True
    assert result["analysis_status"] == "EXPLORATORY"
    assert result["sampler"] == "bao_gaussian_importance"
    # 2026-06-12: a chain that executed ONLY released sha256-verified products
    # (no compressed Gaussian participated) carries the honest executable
    # scope — the old 'compressed_likelihood_preliminary' label made the
    # full_likelihood_overclaim gate hard-block factually true replies.
    assert result["claim_scope"] == "executable_full_fidelity_likelihoods"
    assert [entry["key"] for entry in result["datasets_used"]] == ["desi_dr1_bao"]
    assert result["datasets_not_run"] == []
    assert set(result["parameters"]) == {"H0", "omegam", "rd"}
    assert result["parameters"]["omegam"]["median"] == pytest.approx(0.294, abs=0.03)
    assert result["fit_statistics"]["n_constraints"] == 12
    assert result["chain_diagnostics"]["proposal_ess"] >= 400
    assert result["chain_diagnostics"]["n_independent_chains"] == 0
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
    assert "neutrino-mass" in result["warnings"][0]


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
    assert matrix["matrix_size"] == 18
    assert "BAO only" in labels
    assert "BAO only + SH0ES H0" in labels
    assert "SN only" in labels
    assert "CMB only" in labels
    assert "Pantheon+ + CMB" in labels
    assert "BAO + Pantheon+" in labels
    assert "BAO + Pantheon+ + CMB + SH0ES H0" not in labels
    assert not any(
        {"pantheon_plus", "shoes_h0_riess22"} <= set(row["dataset_keys"])
        for row in matrix["matrix"]
    )
    assert any(
        "union3" in row["dataset_keys"]
        and "shoes_h0_riess22" in row["dataset_keys"]
        for row in matrix["matrix"]
    )
    assert all(row["requires_chain_run"] for row in matrix["matrix"])


def test_executed_robustness_matrix_omits_overlapping_shoes_cells(
    monkeypatch,
) -> None:
    import app.services.cosmology_likelihoods.runners as runners_module

    monkeypatch.setattr(
        runners_module,
        "run_likelihood_chain",
        lambda **_kwargs: {
            "publication_ready": False,
            "analysis_status": "CONFIG_READY",
            "execution_status": "not_run",
            "warnings": [],
        },
    )
    matrix = runners_module.run_robustness_matrix(
        model="lcdm",
        supernova_sets=["pantheon_plus"],
        include_h0_prior=True,
        n_samples=256,
    )

    assert matrix["publication_ready"] is False
    assert matrix["__do_not_claim__"] is True
    assert matrix["analysis_status"] == "ROBUSTNESS_MATRIX_DIAGNOSTIC"
    assert matrix["matrix_size"] == 10
    assert not any(
        {"pantheon_plus", "shoes_h0_riess22"} <= set(row["dataset_keys"])
        for row in matrix["matrix"]
    )
    assert any(
        row["dataset_keys"] == ["desi_dr1_bao", "shoes_h0_riess22"]
        for row in matrix["matrix"]
    )


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
    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is True
    assert result["__do_not_claim__"] is True
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
    assert result["analysis_status"] == "NO_COMPRESSED_LIKELIHOOD"
    assert result["__do_not_claim__"] is True
    assert "Published posterior summaries" in result["warnings"][0]
    assert result["chain_tier"] == "blocked"
    assert result["datasets_used"] == []
    assert {entry["key"] for entry in result["datasets_not_run"]} == {
        "pantheon_plus",
        "spt3g_cmb",
    }
    assert "Published posterior summaries" in " ".join(result["warnings"])


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
    assert chain["publication_ready"] is False
    assert chain["preliminary_ready"] is True
    assert "literature_typed_input" in chain["preliminary_reasons"]
    assert set(chain["parameters"]) == {"H0", "omegam", "ombh2", "ns"}


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
    assert entry.applicable_models == ("lcdm",)
    assert cl.mean == (73.3,)
    assert cl.covariance == ((1.75 ** 2,),)
    arxivs = [c.arxiv for c in entry.citations if c.arxiv]
    assert "1907.04869" in arxivs
    # The approximation field must explicitly state sigma is symmetrized (to avoid reviewers assuming a true 1D Gaussian)
    assert "symmetr" in cl.approximation.lower()

    from app.services.cosmology_likelihoods import _validate_dataset_selection

    with pytest.raises(ValueError, match="not applicable to wcdm"):
        _validate_dataset_selection("wcdm", ["h0licow_h0"])


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


def test_h0_anchor_model_domains_are_explicit() -> None:
    """Direct low-redshift anchors are reusable across model families, while
    the registered H0LiCOW scalar is specifically its flat-LCDM posterior."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    model_independent_anchors = [
        "shoes_h0_riess22",
        "trgb_h0_freedman19",
        "megamaser_h0_pesce20",
    ]
    for key in model_independent_anchors:
        entry = get_cosmology_dataset(key)
        assert entry.observables == ("H0",), f"{key} observables wrong"
        assert "lcdm" in entry.applicable_models
        assert "wcdm" in entry.applicable_models
        assert "w0wa_cdm" in entry.applicable_models
    assert get_cosmology_dataset("h0licow_h0").applicable_models == ("lcdm",)


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


def test_spt_cluster_bocquet19_is_metadata_only_without_invented_covariance() -> None:
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("spt_cluster_bocquet19")
    assert entry.probe == "cluster"
    assert entry.status == "metadata_only"
    assert entry.execution_mode == "config_only"
    assert entry.likelihood_family == "cluster_count"
    assert entry.compressed_likelihood is None
    assert entry.covariance.provided is False
    assert "joint posterior covariance is not registered" in entry.covariance.description
    assert "sigma8_omegam_0p2" in entry.observables

    citation = next(c for c in entry.citations if c.arxiv == "1812.01679")
    assert citation.doi == "10.3847/1538-4357/ab1f10"
    assert entry.source_url == "https://doi.org/10.3847/1538-4357/ab1f10"


def test_spt_cluster_metadata_discloses_weak_lensing_mass_calibration() -> None:
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("spt_cluster_bocquet19")
    notes = entry.notes.lower()
    assert "weak gravitational-lensing" in notes
    assert "magellan/hst" in notes
    assert "32 clusters" in notes
    assert "not a weak-lensing-free anchor" in notes


def test_spt_cluster_is_not_numerically_combined_with_weak_lensing() -> None:
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
    assert result["publication_ready"] is False
    assert result["chain_tier"] == "blocked"
    used_keys = {entry["key"] for entry in result["datasets_used"]}
    not_run_keys = {entry["key"] for entry in result["datasets_not_run"]}
    assert "spt_cluster_bocquet19" not in used_keys
    assert "spt_cluster_bocquet19" in not_run_keys
    assert result["__do_not_claim__"] is True


def test_spt_cluster_alone_returns_no_numeric_likelihood() -> None:
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["spt_cluster_bocquet19"],
        random_seed=20260503,
        n_samples=400,
    )
    assert result["success"] is True
    assert result["analysis_status"] == "NO_COMPRESSED_LIKELIHOOD"
    assert result["chain_tier"] == "blocked"
    assert result["datasets_used"] == []
    assert [entry["key"] for entry in result["datasets_not_run"]] == [
        "spt_cluster_bocquet19"
    ]
    assert result["__do_not_claim__"] is True


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
    # Different likelihood families (sdss_6df_bao: mixed since the 2026-06-12
    # MGS chi2(alpha)-table upgrade — 6dFGS Gaussian + MGS non-Gaussian table)
    assert bao.likelihood_family == "bao_mixed_gaussian_table"
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
    """The emcee fallback improves ESS but one flattened ensemble stays preliminary."""
    from app.services.cosmology_likelihoods import run_likelihood_chain

    keys = ["desi_dr1_bao", "union3", "planck2018_compressed"]

    # Matrix / default path: fast importance sampling, ESS collapses on the
    # 3-probe product, so the cell is not publication-ready.
    fast = run_likelihood_chain(
        model="lcdm", dataset_keys=keys, random_seed=42, n_samples=4000
    )
    assert fast["sampler"] == "bao_gaussian_importance"
    assert fast["publication_ready"] is False

    # Single-cell deep run: emcee upgrade clears the scalar ESS floor, but it
    # still has no four independent chains or rank-Rhat certificate.
    deep = run_likelihood_chain(
        model="lcdm",
        dataset_keys=keys,
        random_seed=42,
        n_samples=4000,
        allow_emcee_fallback=True,
    )
    assert deep["sampler"] == "sn_emcee"
    assert deep["publication_ready"] is False
    assert deep["preliminary_ready"] is True
    assert deep["chain_tier"] == "exploratory"
    assert "flattened_coupled_emcee_ensemble" in deep["preliminary_reasons"]
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
    assert deep["publication_ready"] is False
    assert deep["preliminary_ready"] is True
