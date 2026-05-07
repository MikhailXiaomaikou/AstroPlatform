import pytest


def test_controlled_nested_sampler_recovers_toy_gaussian():
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
    assert result["analysis_status"] == "NESTED_SAMPLER_READY"
    assert result["publication_ready"] is True
    assert result["sampler"] == "dynesty_nested"
    assert result["parameters"]["S8"]["median"] == pytest.approx(0.80, abs=0.03)
    assert result["parameters"]["omegam"]["median"] == pytest.approx(0.30, abs=0.04)
    assert result["evidence"]["logzerr"] < 0.75
    assert result["chain_diagnostics"]["posterior_ess"] >= 80
    assert result["provenance"]["nested_sampler"]["publication_ready"] is True


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
    from app.services.ai_tools import _exec_run_nested_sampler

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
    assert result["publication_ready"] is True
    assert result["parameters"]["x"]["median"] == pytest.approx(1.0, abs=0.25)
