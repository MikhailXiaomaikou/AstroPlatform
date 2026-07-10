"""Scientific benchmark accounting must distinguish pass, fail, and skip."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import numpy as np


_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "benchmarks"
    / "run_cosmology_benchmarks.py"
)
_SPEC = importlib.util.spec_from_file_location("cosmology_benchmark_runner", _SCRIPT)
assert _SPEC and _SPEC.loader
benchmark_runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark_runner)


def test_full_pantheon_opt_out_is_skip_not_pass(monkeypatch):
    import app.services.cosmology_likelihoods as cl

    monkeypatch.setattr(cl, "PANTHEON_PLUS_FULL_CHI2_ENABLED", False)
    result = benchmark_runner.bench_pantheon_full_cov_fidelity()

    assert result["status"] == "skipped"
    assert result["pass"] is None


def test_blocked_wcdm_chain_can_pass_numerically_but_fails_publication_gate(monkeypatch):
    import app.services.cosmology_likelihoods as cl

    monkeypatch.setattr(
        cl,
        "run_likelihood_chain",
        lambda **kwargs: {
            "parameters": {"w": {"median": -1.0}},
            "chain_tier": "blocked",
        },
    )
    result = benchmark_runner.bench_wcdm_w_near_minus_one()

    assert result["pass"] is False
    assert result["numerical_regression_pass"] is True
    assert result["publication_gate_correct"] is False
    assert result["scientific_publication_pass"] is False
    assert result["chain_tier"] == "blocked"


def test_blocked_wcdm_with_implausible_value_is_a_failure(monkeypatch):
    import app.services.cosmology_likelihoods as cl

    monkeypatch.setattr(
        cl,
        "run_likelihood_chain",
        lambda **kwargs: {
            "parameters": {"w": {"median": 0.0}},
            "chain_tier": "blocked",
        },
    )
    result = benchmark_runner.bench_wcdm_w_near_minus_one()

    assert result["pass"] is False
    assert result["numerical_regression_pass"] is False
    assert result["scientific_publication_pass"] is False


def test_preliminary_wcdm_regression_is_never_a_publication_pass(monkeypatch):
    import app.services.cosmology_likelihoods as cl

    monkeypatch.setattr(
        cl,
        "run_likelihood_chain",
        lambda **kwargs: {
            "parameters": {"w": {"median": -1.0}},
            "chain_tier": "exploratory",
            "publication_ready": False,
            "preliminary_ready": True,
            "preliminary_reasons": ["independent_chains_below_minimum"],
        },
    )
    result = benchmark_runner.bench_wcdm_w_near_minus_one()

    assert result["pass"] is True
    assert result["numerical_regression_pass"] is True
    assert result["publication_gate_correct"] is True
    assert result["scientific_publication_pass"] is False
    assert result["validation_scope"] == "preliminary_numerical_regression"


def test_cli_summary_does_not_count_skip_as_pass(monkeypatch, capsys):
    monkeypatch.setattr(
        benchmark_runner,
        "BENCHMARKS",
        [("skipped_case", lambda: {"pass": None, "status": "skipped"})],
    )
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT)])

    assert benchmark_runner.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["n_pass"] == 0
    assert payload["n_fail"] == 0
    assert payload["n_skipped"] == 1
    assert payload["n_numerical_regression_pass"] == 0
    assert payload["n_scientific_publication_pass"] == 0
    assert payload["suite_status"] == "passed_with_skips"


def test_cli_normalizes_numpy_boolean_and_accounts_for_every_case(monkeypatch, capsys):
    monkeypatch.setattr(
        benchmark_runner,
        "BENCHMARKS",
        [
            ("numpy_pass", lambda: {"pass": np.bool_(True)}),
            ("ordinary_fail", lambda: {"pass": False}),
            ("not_validated", lambda: {"pass": None, "status": "not_validated"}),
        ],
    )
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT)])

    assert benchmark_runner.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["n_benchmarks"] == 3
    assert payload["n_pass"] == 1
    assert payload["n_fail"] == 1
    assert payload["n_skipped"] == 1
    assert payload["n_numerical_regression_pass"] == 1
    assert payload["n_numerical_regression_fail"] == 1
    assert payload["n_scientific_publication_pass"] == 0
    assert payload["n_executed"] + payload["n_skipped"] == payload["n_benchmarks"]


def test_cli_counts_preliminary_numerical_pass_separately(monkeypatch, capsys):
    monkeypatch.setattr(
        benchmark_runner,
        "BENCHMARKS",
        [
            (
                "preliminary_case",
                lambda: {
                    "pass": True,
                    "numerical_regression_pass": True,
                    "publication_gate_correct": True,
                    "scientific_publication_pass": False,
                    "validation_scope": "preliminary_numerical_regression",
                },
            )
        ],
    )
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT)])

    assert benchmark_runner.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["n_pass"] == 1
    assert payload["n_numerical_regression_pass"] == 1
    assert payload["n_preliminary_numerical_pass"] == 1
    assert payload["n_publication_gate_pass"] == 1
    assert payload["n_scientific_publication_pass"] == 0
    assert payload["n_scientific_publication_withheld"] == 1
    assert payload["pass_semantics"] == "benchmark_contract_only_not_scientific_publication"
