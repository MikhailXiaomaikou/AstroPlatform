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
        for product in desi_products
    )
    assert any(
        product["role"] == "covariance"
        and "desi_2024_gaussian_bao_ALL_GCcomb_cov.txt" in product["url"]
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


def test_compressed_likelihood_runner_keeps_config_only_datasets_out_of_posterior():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["sdss_6df_bao", "planck2018_compressed"],
        random_seed=123,
        n_samples=512,
    )

    assert result["publication_ready"] is True
    assert [entry["key"] for entry in result["datasets_used"]] == ["planck2018_compressed"]
    assert [entry["key"] for entry in result["datasets_not_run"]] == ["sdss_6df_bao"]
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
    assert "eBOSS Collaboration" in citations
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
    assert matrix["matrix_size"] == 12
    assert "BAO only" in labels
    assert "BAO only + SH0ES H0" in labels
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


def test_compressed_runner_reports_no_executable_likelihood_reason():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["pantheon_plus", "des_sn5yr", "union3"],
    )

    assert result["publication_ready"] is False
    assert result["analysis_status"] == "NO_COMPRESSED_LIKELIHOOD"
    assert "No selected dataset has a registered compressed Gaussian likelihood" in result["warnings"][0]


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
    Distance-ladder anchor 之间 SH0ES (Cepheid) 和 Planck (CMB inverse)."""
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
    我们用 1.75 的对称 Gaussian 近似."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("h0licow_h0")
    cl = entry.compressed_likelihood
    assert cl.mean == (73.3,)
    assert cl.covariance == ((1.75 ** 2,),)
    arxivs = [c.arxiv for c in entry.citations if c.arxiv]
    assert "1907.04869" in arxivs
    # 必须明确说 sigma 是对称化近似 (避免审稿人误以为是真实 1D Gaussian)
    assert "symmetr" in cl.approximation.lower()


def test_megamaser_pesce20_h0_prior_registered() -> None:
    """spec paper #14: Pesce+ 2020 megamaser H0 = 73.9 ± 3.0 — 几何
    anchor, 完全独立于 Cepheid/TRGB/SN Ia 阶梯."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("megamaser_h0_pesce20")
    cl = entry.compressed_likelihood
    assert cl.mean == (73.9,)
    assert cl.covariance == ((3.0 ** 2,),)
    arxivs = [c.arxiv for c in entry.citations if c.arxiv]
    assert "2001.09213" in arxivs
    # notes 或 approximation 必须显式说"几何 anchor / 独立于阶梯"
    note_blob = (entry.notes or "").lower() + (cl.approximation or "").lower()
    assert "geometric" in note_blob or "anchor" in note_blob


def test_spt3g_cmb_external_likelihood_registered() -> None:
    """spec paper #12: SPT-3G Balkenhol+ 2023 TT/TE/EE damping-tail.
    External Cobaya likelihood (不能压缩成几维 Gaussian, 全 power
    spectrum data product)."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    entry = get_cosmology_dataset("spt3g_cmb")
    assert entry.probe == "cmb"
    assert entry.execution_mode == "external_cobaya"
    assert entry.likelihood_family == "cmb_powerspectrum"
    # 必须含完整 TT/TE/EE 三 observable
    assert set(entry.observables) >= {"TT", "TE", "EE"}
    arxivs = [c.arxiv for c in entry.citations if c.arxiv]
    assert "2212.05642" in arxivs
    # 必须含至少几个标准 nuisance (kappa / dust)
    assert "kappa" in entry.nuisance_parameters


def test_all_4_h0_anchors_share_observable_and_models() -> None:
    """全 4 个 H0 anchor (TRGB / SH0ES / H0LiCOW / Megamaser) 必须
    expose H0 作为唯一 observable, applicable_models 必须含
    lcdm/wcdm/w0wa_cdm (H0 prior 跟具体模型无关)."""
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
    """4 个 H0 anchor mean 值合起来必须横跨 'H0 tension 区间' 69-74,
    才能让 cosmology_mcmc 用作互相对照."""
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    means = []
    for key in ("trgb_h0_freedman19", "shoes_h0_riess22",
                "h0licow_h0", "megamaser_h0_pesce20"):
        entry = get_cosmology_dataset(key)
        means.append(entry.compressed_likelihood.mean[0])
    assert min(means) <= 70.0   # TRGB 端
    assert max(means) >= 73.0   # SH0ES / H0LiCOW / Megamaser 端
    # 横跨 ~3-4 km/s/Mpc, 即真实张力区间
    assert max(means) - min(means) >= 3.0
