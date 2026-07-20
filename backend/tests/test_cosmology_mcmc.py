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


def test_distance_modulus_zero_point_is_exactly_degenerate_with_h0():
    """A constant magnitude zero point can be absorbed exactly by H0.

    This is why a generic ``(z, mu, sigma_mu)`` table without an explicit
    absolute calibration or M_B nuisance cannot yield a claimable H0.
    """
    import numpy as np

    from app.services.cosmology_mcmc import distance_modulus_model

    z = np.array([0.02, 0.1, 0.4, 0.9])
    h0 = 70.0
    delta_mu = 0.37
    shifted_h0 = h0 * 10.0 ** (-delta_mu / 5.0)
    baseline = distance_modulus_model(z, "flat_lcdm", {"H0": h0, "Om0": 0.3})
    shifted = distance_modulus_model(
        z,
        "flat_lcdm",
        {"H0": shifted_h0, "Om0": 0.3},
    )

    np.testing.assert_allclose(shifted, baseline + delta_mu, rtol=0.0, atol=1e-12)


def test_invalid_distance_modulus_columns_error():
    from app.services.cosmology_mcmc import CosmologyMCMCError, validate_distance_modulus_rows

    with pytest.raises(CosmologyMCMCError, match="z, mu, sigma_mu"):
        validate_distance_modulus_rows([{"z": 0.1, "mu": 38.0} for _ in range(3)])


def test_priors_may_tighten_but_not_widen():
    from app.services.cosmology_mcmc import CosmologyMCMCError, sanitize_priors

    priors = sanitize_priors("flat_w0wa_cdm", {"H0": [60, 80], "w0": [-1.5, -0.5]})
    assert priors["H0"] == (60.0, 80.0)
    assert priors["wa"] == (-3.0, 2.0)

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
    assert "absolute calibration" in inline["warnings"][0]
    assert inline["likelihood_fidelity"]["h0_identifiable"] is False
    assert inline["parameters"]["H0"]["claimable"] is False

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
    assert cached["publication_ready"] is False
    assert cached["chain_tier"] == "blocked"
    assert cached["__do_not_claim__"] is True
    assert cached["input_rows_verified"] is True
    assert cached["likelihood_fidelity"]["full_covariance_present"] is False
    assert cached["chain_diagnostics"]["sampler_diagnostics_passed"] is True
    assert cached["chain_diagnostics"]["publication_ready"] is False
    assert "full covariance" in cached["warnings"][0]


def test_user_uploaded_rows_do_not_bypass_sn_likelihood_science_gate(monkeypatch):
    """Trusted provenance cannot supply calibration/covariance absent in schema."""
    import app.services.cosmology_mcmc as cm

    def fake_good_diagnostics(_chain, names):
        return {
            "parameters": {
                name: {
                    "median": 70.0 if name == "H0" else 0.3,
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

    monkeypatch.setattr(cm, "_chain_diagnostics_from_emcee_chain", fake_good_diagnostics)

    result = cm.fit_cosmology_emcee(
        toy_distance_modulus_rows(),
        model="flat_lcdm",
        n_walkers=10,
        n_steps=10,
        n_burn=2,
        random_seed=3,
        input_data_origin="user_uploaded",
    )
    assert result["publication_ready"] is False
    assert result["chain_tier"] == "blocked"
    assert result["input_rows_verified"] is True
    assert result["__do_not_claim__"] is True
    assert result["parameters"]["H0"]["scientifically_identified"] is False


def test_medium_ess_cannot_override_sn_likelihood_science_gate(monkeypatch):
    """Sampler quality cannot rescue a scientifically incomplete likelihood."""
    import app.services.cosmology_mcmc as cm

    def fake_medium_diagnostics(_chain, names):
        return {
            "parameters": {
                name: {
                    "median": 70.0 if name == "H0" else 0.3,
                    "rhat": 1.07,
                    "ess_bulk": 200.0,
                    "ess_tail": 180.0,
                    "status": "marginal",
                }
                for name in names
            },
            "overall_status": "check_required",
            "publication_ready": False,
            "insufficient_params": list(names),
            "thresholds": {"ess_min": 400.0, "rhat_max": 1.05},
        }

    monkeypatch.setattr(cm, "_chain_diagnostics_from_emcee_chain", fake_medium_diagnostics)

    result = cm.fit_cosmology_emcee(
        toy_distance_modulus_rows(),
        model="flat_lcdm",
        n_walkers=10,
        n_steps=10,
        n_burn=2,
        random_seed=3,
        input_data_origin="cached_real",
        source_cache_key="latest_adql",
    )
    assert result["chain_tier"] == "blocked"
    assert result["publication_ready"] is False
    assert result["__tool_status__"] == "PARTIAL"
    assert result["analysis_status"] == "PARTIAL"
    assert result["__do_not_claim__"] is True
    assert "absolute calibration" in result["warnings"][0]


def test_blocked_tier_for_low_ess_even_with_claimable_origin(monkeypatch):
    """Plan B-1 (2026-05-20): ESS < ESS_EXPLORATORY_THRESHOLD (100) keeps the
    chain blocked (PARTIAL + __do_not_claim__) even when input_data_origin
    is claimable. Floor is firm — a chain that ran for ~50 effective draws
    is noise, not exploratory."""
    import app.services.cosmology_mcmc as cm

    def fake_low_diagnostics(_chain, names):
        return {
            "parameters": {
                name: {
                    "median": 70.0 if name == "H0" else 0.3,
                    "rhat": 1.02,
                    "ess_bulk": 50.0,
                    "ess_tail": 40.0,
                    "status": "not_converged",
                }
                for name in names
            },
            "overall_status": "check_required",
            "publication_ready": False,
            "insufficient_params": list(names),
            "thresholds": {"ess_min": 400.0, "rhat_max": 1.05},
        }

    monkeypatch.setattr(cm, "_chain_diagnostics_from_emcee_chain", fake_low_diagnostics)

    result = cm.fit_cosmology_emcee(
        toy_distance_modulus_rows(),
        model="flat_lcdm",
        n_walkers=10,
        n_steps=10,
        n_burn=2,
        random_seed=3,
        input_data_origin="cached_real",
        source_cache_key="latest_adql",
    )
    assert result["chain_tier"] == "blocked"
    assert result["publication_ready"] is False
    assert result["__tool_status__"] == "PARTIAL"
    assert result["__do_not_claim__"] is True
    assert "absolute calibration" in result["warnings"][0]


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
    # async_tool_runtime adds 'queued' (initial KV state, before Celery picks it up).
    assert status["status"] in {"queued", "running", "completed", "failed"}
    # The submit banner reports the real backend now (Celery via async_tool_runtime).
    assert queued["background_backend"] == "celery"


def test_background_submission_fails_honestly_in_https_worker_mode(monkeypatch):
    from app.config import settings
    from app.services.cosmology_mcmc import submit_emcee_job

    monkeypatch.setattr(settings, "science_execution_backend", "https_worker")

    rejected = submit_emcee_job(
        rows=toy_distance_modulus_rows(),
        model="flat_lcdm",
        n_walkers=10,
        n_steps=24,
        n_burn=6,
        random_seed=9,
    )

    assert rejected["__tool_status__"] == "FAILED"
    assert rejected["error_class"] == "science_workflow_not_registered"
    assert rejected["background_backend"] == "https_worker"
    assert "job_id" not in rejected
    assert "No cosmology chain was queued" in rejected["warning"]


@pytest.mark.asyncio
async def test_legacy_cosmology_poll_is_owner_scoped_end_to_end():
    from app.services.ai_tools_cosmology import dispatch_cosmology
    from app.services.cosmology_mcmc import submit_emcee_job

    owner = "owner-a"
    queued = submit_emcee_job(
        user_id=owner,
        rows=toy_distance_modulus_rows(),
        model="flat_lcdm",
        n_walkers=10,
        n_steps=24,
        n_burn=6,
        random_seed=9,
    )

    visible = await dispatch_cosmology(
        "get_cosmology_run_status",
        {"job_id": queued["job_id"]},
        user_id=owner,
    )
    hidden = await dispatch_cosmology(
        "get_cosmology_run_status",
        {"job_id": queued["job_id"]},
        user_id="owner-b",
    )

    assert visible is not None and visible["job_id"] == queued["job_id"]
    assert hidden is not None and hidden["error_class"] == "not_found"


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


def test_manual_attestation_requires_ads_confirmation(monkeypatch):
    """Inline rows + attestation upgrade to citeable (cached_real) only when
    ADS confirms the bibcode. An unconfirmable reference stays audit-only and
    the bibcode is dropped so it cannot reach the citation pool."""
    import asyncio

    from app.services import ai_tools_cosmology as atc

    inp = {
        "rows": toy_distance_modulus_rows(),
        "manual_attestation": {"source": "Riess+2022", "bibcode": "2022ApJ...934L...7R"},
    }

    async def _confirmed(**kwargs):
        return True

    async def _unconfirmed(**kwargs):
        return False

    monkeypatch.setattr(
        "app.services.literature_engine.resolve_bibcode_exists", _confirmed
    )
    _rows, origin, cache_key, attestation = asyncio.run(
        atc._cosmology_rows_from_input(inp, None)
    )
    assert origin == "cached_real"
    assert attestation is not None
    assert cache_key == "manual_attestation:2022ApJ...934L...7R"

    monkeypatch.setattr(
        "app.services.literature_engine.resolve_bibcode_exists", _unconfirmed
    )
    _rows, origin, cache_key, attestation = asyncio.run(
        atc._cosmology_rows_from_input(inp, None)
    )
    assert origin == "inline_unverified"
    assert attestation is None  # dropped — must not enter the citation pool
    assert cache_key is None
