import pytest


def test_controlled_nested_sampler_recovers_toy_gaussian_but_does_not_cite_it():
    from app.services.nested_sampling import run_controlled_nested_sampler

    result = run_controlled_nested_sampler(
        parameters=[
            {"name": "S8", "prior": [0.6, 1.0]},
            {"name": "omegam", "prior": [0.1, 0.5]},
        ],
        gaussian_likelihood={
            "label": "toy weak-lensing compressed constraint",
            "parameters": ["S8", "omegam"],
            "mean": [0.80, 0.30],
            "covariance": [[0.01**2, 0.0], [0.0, 0.02**2]],
            "citation": "synthetic unit-test fixture",
        },
        sampler_config={"nlive": 70, "dlogz": 0.5},
        random_seed=123,
    )

    assert result["success"] is True
    assert result["analysis_status"] == "NESTED_SAMPLER_DIAGNOSTIC"
    assert result["publication_ready"] is False
    assert result["sampler"] == "dynesty_nested"
    assert result["parameters"]["S8"]["median"] == pytest.approx(0.80, abs=0.03)
    assert result["parameters"]["omegam"]["median"] == pytest.approx(0.30, abs=0.04)
    assert result["evidence"]["logzerr"] < 0.75
    assert result["chain_diagnostics"]["posterior_ess"] >= 80
    assert result["chain_diagnostics"]["numerical_quality_ready"] is True
    assert result["provenance"]["nested_sampler"]["source_machine_verified"] is False
    assert result["__do_not_claim__"]


def test_controlled_nested_sampler_rejects_invalid_covariance():
    from app.services.nested_sampling import run_controlled_nested_sampler

    result = run_controlled_nested_sampler(
        parameters=[{"name": "x", "prior": [-5, 5]}],
        gaussian_likelihood={
            "parameters": ["x"],
            "mean": [0.0],
            "covariance": [[0.0]],
        },
    )

    assert result["success"] is False
    assert result["analysis_status"] == "FAILED"
    assert result["publication_ready"] is False
    assert "positive definite" in result["error"]


def test_controlled_nested_sampler_low_nlive_is_not_publication_ready():
    from app.services.nested_sampling import run_controlled_nested_sampler

    result = run_controlled_nested_sampler(
        parameters=[{"name": "x", "prior": [-5, 5]}],
        gaussian_likelihood={
            "parameters": ["x"],
            "mean": [0.5],
            "covariance": [[0.5**2]],
        },
        sampler_config={"nlive": 25, "dlogz": 0.5},
        random_seed=12,
    )

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["__tool_status__"] == "PARTIAL"
    assert result["__do_not_claim__"]


def test_ai_tool_wrapper_runs_nested_sampler():
    from app.services.ai_tools_cosmology import _exec_run_nested_sampler

    result = _exec_run_nested_sampler(
        {
            "parameters": [{"name": "x", "prior": [-5, 5]}],
            "gaussian_likelihood": {
                "parameters": ["x"],
                "mean": [1.0],
                "covariance": [[0.25]],
            },
            "sampler_config": {"nlive": 60, "dlogz": 0.5},
            "random_seed": 99,
        }
    )

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["chain_diagnostics"]["numerical_quality_ready"] is True
    assert result["parameters"]["x"]["median"] == pytest.approx(1.0, abs=0.25)


def _registered_gaussian(dataset_key: str) -> dict:
    from app.services.cosmology_likelihoods.registry import get_cosmology_dataset

    spec = get_cosmology_dataset(dataset_key).compressed_likelihood
    assert spec is not None
    return {
        "dataset_key": dataset_key,
        "parameters": list(spec.parameters),
        "mean": list(spec.mean),
        "covariance": [list(row) for row in spec.covariance],
    }


def test_exact_registered_gaussian_can_be_publication_ready():
    from app.services.nested_sampling import run_controlled_nested_sampler

    result = run_controlled_nested_sampler(
        parameters=[{"name": "S8", "prior": [0.4, 1.2]}],
        gaussian_likelihood=_registered_gaussian("kids1000_wl"),
        sampler_config={"nlive": 70, "dlogz": 0.5},
        random_seed=321,
    )

    assert result["success"] is True
    assert result["publication_ready"] is True
    assert result["analysis_status"] == "NESTED_SAMPLER_READY"
    used = result["likelihoods_used"][0]
    assert used["source_machine_verified"] is True
    assert used["source_verification"] == "exact_registered_gaussian_match"
    assert used["citation"]


def test_dataset_key_cannot_launder_changed_gaussian_values():
    from app.services.nested_sampling import run_controlled_nested_sampler

    likelihood = _registered_gaussian("kids1000_wl")
    likelihood["mean"] = [0.95]
    likelihood["citation"] = "Invented et al. 2099"
    likelihood["source_url"] = "https://invalid.example/fake"
    result = run_controlled_nested_sampler(
        parameters=[{"name": "S8", "prior": [0.4, 1.2]}],
        gaussian_likelihood=likelihood,
        sampler_config={"nlive": 70, "dlogz": 0.5},
        random_seed=321,
    )

    assert result["chain_diagnostics"]["numerical_quality_ready"] is True
    assert result["publication_ready"] is False
    used = result["likelihoods_used"][0]
    assert used["source_verification"] == "registered_values_mismatch"
    # Untrusted citation metadata is not reflected into the provenance result.
    assert used["citation"] is None
    assert used["source_url"] is None


def test_maxiter_before_dlogz_is_never_publication_ready():
    from app.services.nested_sampling import run_controlled_nested_sampler

    result = run_controlled_nested_sampler(
        parameters=[{"name": "S8", "prior": [0.4, 1.2]}],
        gaussian_likelihood=_registered_gaussian("kids1000_wl"),
        sampler_config={"nlive": 70, "dlogz": 0.01, "maxiter": 0},
        random_seed=321,
    )

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["chain_diagnostics"]["stopping_criterion_met"] is False
    assert result["chain_diagnostics"]["maxiter_exhausted"] is True
    assert result["__do_not_claim__"]


def test_duplicate_gaussian_blocks_are_rejected_before_sampling():
    from app.services.nested_sampling import run_controlled_nested_sampler

    block = {
        "parameters": ["x"],
        "mean": [0.0],
        "covariance": [[1.0]],
    }
    result = run_controlled_nested_sampler(
        parameters=[{"name": "x", "prior": [-5.0, 5.0]}],
        likelihoods=[dict(block), dict(block)],
    )

    assert result["success"] is False
    assert result["publication_ready"] is False
    assert result["error_class"] == "invalid_nested_sampler_input"
    assert "duplicate Gaussian likelihood" in result["error"]


def test_same_declared_source_with_different_numbers_is_fail_closed():
    from app.services.nested_sampling import run_controlled_nested_sampler

    shared = {
        "parameters": ["x"],
        "covariance": [[1.0]],
        "citation": "Example Collaboration 2026",
        "source_url": "https://example.invalid/same-release",
    }
    result = run_controlled_nested_sampler(
        parameters=[{"name": "x", "prior": [-5.0, 5.0]}],
        likelihoods=[
            {**shared, "mean": [0.0]},
            {**shared, "mean": [1.0]},
        ],
    )

    assert result["success"] is False
    assert result["publication_ready"] is False
    assert result["error_class"] == "invalid_nested_sampler_input"
    assert "same citation/source" in result["error"]


def test_same_registered_dataset_cannot_be_multiplied_twice():
    from app.services.nested_sampling import run_controlled_nested_sampler

    registered = _registered_gaussian("kids1000_wl")
    result = run_controlled_nested_sampler(
        parameters=[{"name": "S8", "prior": [0.4, 1.2]}],
        likelihoods=[dict(registered), dict(registered)],
    )

    assert result["success"] is False
    assert result["publication_ready"] is False
    assert "duplicate Gaussian likelihood" in result["error"]


def test_paper_tool_gap_matrix_knows_nested_sampler_is_available():
    from app.services.paper_tool_mining import build_tool_gap_matrix

    matrix = build_tool_gap_matrix(
        tool_specs=[
            {
                "tool_category": "sampler",
                "canonical_capability": "nested_sampler",
                "implementation_status": "available",
                "source_spans": [{"section": "Methods"}],
            }
        ]
    )

    assert matrix["gap_matrix"][0]["capability"] == "nested_sampler"
    assert matrix["gap_matrix"][0]["current_status"] == "available"
    assert matrix["gap_matrix"][0]["available_platform_tools"] == ["run_nested_sampler"]
