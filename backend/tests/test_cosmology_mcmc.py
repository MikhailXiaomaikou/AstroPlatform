import pytest


def toy_distance_modulus_rows():
    from app.services.cosmology_mcmc import distance_modulus_model

    z_values = [0.02, 0.05, 0.08, 0.12, 0.18, 0.25]
    mu_values = distance_modulus_model(
        z_values,
        "flat_lcdm",
        {"H0": 70.0, "Om0": 0.3},
    )
    return [
        {"z": float(z), "mu": float(mu), "sigma_mu": 0.18}
        for z, mu in zip(z_values, mu_values, strict=True)
    ]


def test_distance_modulus_validation_and_hash_are_stable():
    from app.services.cosmology_mcmc import validate_distance_modulus_rows

    rows = toy_distance_modulus_rows()
    first = validate_distance_modulus_rows(list(reversed(rows)))
    second = validate_distance_modulus_rows(rows)

    assert first.data_hash == second.data_hash
    assert first.z[0] < first.z[-1]


def test_invalid_distance_modulus_columns_error():
    from app.services.cosmology_mcmc import CosmologyMCMCError, validate_distance_modulus_rows

    with pytest.raises(CosmologyMCMCError, match="z, mu, sigma_mu"):
        validate_distance_modulus_rows([{"z": 0.1, "mu": 38.0} for _ in range(3)])


def test_priors_may_tighten_but_not_widen():
    from app.services.cosmology_mcmc import CosmologyMCMCError, sanitize_priors

    priors = sanitize_priors("flat_w0wa_cdm", {"H0": [60, 80], "w0": [-1.5, -0.5]})
    assert priors["H0"] == (60.0, 80.0)
    assert priors["wa"] == (-4.0, 4.0)

    with pytest.raises(CosmologyMCMCError, match="within"):
        sanitize_priors("flat_lcdm", {"H0": [40, 100]})


def test_emcee_fit_is_seed_reproducible_and_not_publication_ready_for_short_chain():
    from app.services.cosmology_mcmc import fit_cosmology_emcee

    rows = toy_distance_modulus_rows()
    first = fit_cosmology_emcee(
        rows,
        model="flat_lcdm",
        n_walkers=10,
        n_steps=28,
        n_burn=8,
        random_seed=1234,
    )
    second = fit_cosmology_emcee(
        rows,
        model="flat_lcdm",
        n_walkers=10,
        n_steps=28,
        n_burn=8,
        random_seed=1234,
    )

    assert first["parameters"]["H0"]["median"] == second["parameters"]["H0"]["median"]
    assert first["data_hash"] == second["data_hash"]
    assert first["publication_ready"] is False
    assert first["__do_not_claim__"] is True
    assert first["provenance"]["cosmology"]["random_seed"] == 1234


def test_inline_rows_remain_unciteable_even_with_good_diagnostics(monkeypatch):
    import app.services.cosmology_mcmc as cm

    def fake_diagnostics(_chain, names):
        return {
            "parameters": {
                name: {
                    "median": 70.0 if name == "H0" else 0.3,
                    "hdi_low_94": 69.0 if name == "H0" else 0.25,
                    "hdi_high_94": 71.0 if name == "H0" else 0.35,
                    "rhat": 1.0,
                    "ess_bulk": 1000.0,
                    "ess_tail": 900.0,
                    "status": "good",
                }
                for name in names
            },
            "overall_status": "converged",
            "publication_ready": True,
            "insufficient_params": [],
            "thresholds": {"ess_min": 400.0, "rhat_max": 1.05},
        }

    monkeypatch.setattr(cm, "_chain_diagnostics_from_emcee_chain", fake_diagnostics)

    inline = cm.fit_cosmology_emcee(
        toy_distance_modulus_rows(),
        model="flat_lcdm",
        n_walkers=10,
        n_steps=10,
        n_burn=2,
        random_seed=3,
        input_data_origin="inline_unverified",
    )
    assert inline["publication_ready"] is False
    assert inline["__do_not_claim__"] is True
    assert inline["data_origin"] == "unavailable"
    assert "inline/unverified rows" in inline["warnings"][0]

    cached = cm.fit_cosmology_emcee(
        toy_distance_modulus_rows(),
        model="flat_lcdm",
        n_walkers=10,
        n_steps=10,
        n_burn=2,
        random_seed=3,
        input_data_origin="cached_real",
        source_cache_key="latest_adql",
    )
    assert cached["publication_ready"] is True
    assert cached["input_rows_verified"] is True
    assert "__do_not_claim__" not in cached


def test_background_status_roundtrip():
    from app.services.cosmology_mcmc import get_cosmology_job_status, submit_emcee_job

    queued = submit_emcee_job(
        rows=toy_distance_modulus_rows(),
        model="flat_lcdm",
        n_walkers=10,
        n_steps=24,
        n_burn=6,
        random_seed=9,
    )
    status = get_cosmology_job_status(queued["job_id"])
    assert status["job_id"] == queued["job_id"]
    assert status["status"] in {"running", "completed", "failed"}
    assert status["background_backend"] == "in_process_ephemeral"


def test_cobaya_unavailable_is_structured_when_missing_or_disabled(monkeypatch):
    from app.services.cosmology_mcmc import run_cobaya_cosmology

    monkeypatch.delenv("COBAYA_COSMOLOGY_ENABLED", raising=False)
    result = run_cobaya_cosmology(
        toy_distance_modulus_rows(),
        model="flat_lcdm",
        random_seed=5,
        max_samples=20,
    )
    assert result["__tool_status__"] == "UNAVAILABLE"
    assert result["__do_not_claim__"] is True
    assert result["provenance"]["cosmology"]["sampler"] == "cobaya"
    assert "phase-1 disabled" in result["error"]
