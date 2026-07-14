"""Seed plumbing for stochastic pipeline nodes and Bayesian samplers."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import numpy as np
import pytest


def test_pipeline_derives_stable_per_node_seeds_without_mutating_input():
    from app.services.ai_tools.research_workflow import (
        _derive_pipeline_node_seed,
        _inject_pipeline_random_seeds,
    )

    dag = {
        "nodes": [
            {"id": "bayes-a", "type": "BayesianFit", "data": {"params": {}}},
            {"id": "period-b", "type": "TimeSeriesAnalysis", "data": {"params": {}}},
            {
                "id": "period-explicit",
                "type": "TimeSeriesAnalysis",
                "data": {"params": {"random_seed": 0}},
            },
            {"id": "plot", "type": "Plot", "data": {"params": {}}},
        ],
        "edges": [],
    }

    seeded, node_seeds = _inject_pipeline_random_seeds(dag, 17)
    seeded_again, node_seeds_again = _inject_pipeline_random_seeds(dag, 17)

    assert "random_seed" not in dag["nodes"][0]["data"]["params"]
    assert node_seeds == node_seeds_again
    assert seeded == seeded_again
    assert node_seeds == {
        "bayes-a": _derive_pipeline_node_seed(17, "bayes-a"),
        "period-b": _derive_pipeline_node_seed(17, "period-b"),
        "period-explicit": 0,
    }
    assert node_seeds["bayes-a"] != node_seeds["period-b"]
    assert "random_seed" not in seeded["nodes"][3]["data"]["params"]


def test_run_pipeline_passes_seeded_dag_to_engine(monkeypatch):
    from app.pipeline import engine
    from app.services.ai_tools.research_workflow import _exec_run_pipeline

    seen: dict = {}

    def fake_execute(dag, input_data_id, run_id, owner_scope):
        seen.update(
            dag=dag,
            input_data_id=input_data_id,
            run_id=run_id,
            owner_scope=owner_scope,
        )
        return {"node-a": {"random_seed": dag["nodes"][0]["data"]["params"]["random_seed"]}}

    monkeypatch.setattr(engine, "execute_dag", fake_execute)
    original_dag = {
        "nodes": [
            {"id": "node-a", "type": "BayesianFit", "data": {"params": {}}},
        ],
        "edges": [],
    }
    result = asyncio.run(
        _exec_run_pipeline(
            {"dag": original_dag, "input_data_id": "dataset-key", "random_seed": 41},
            owner_scope="owner-a",
        )
    )

    assert "random_seed" not in original_dag["nodes"][0]["data"]["params"]
    assert seen["dag"]["nodes"][0]["data"]["params"]["random_seed"] == result[
        "node_random_seeds"
    ]["node-a"]
    assert result["random_seed"] == 41
    assert seen["owner_scope"] == "owner-a"


def test_timeseries_node_replays_bootstrap_fap_with_seed():
    from app.pipeline.nodes.timeseries import timeseries_analysis

    data_rng = np.random.default_rng(7)
    time = np.sort(data_rng.uniform(0, 30, 35))
    mag = 0.15 * np.sin(2 * np.pi * time / 3.7) + data_rng.normal(0, 0.25, len(time))
    input_data = {"data": {"time": time.tolist(), "mag": mag.tolist()}}
    params = {
        "min_period": 0.5,
        "max_period": 10,
        "n_frequencies": 500,
        "n_bootstrap": 50,
        "random_seed": 31415,
    }

    first = timeseries_analysis(input_data, params)
    second = timeseries_analysis(input_data, params)

    assert first["period_result"]["fap_method"] == "bootstrap"
    assert first["period_result"]["fap"] == second["period_result"]["fap"]
    assert first["random_seed"] == second["random_seed"] == 31415


def test_bayesian_nested_sampling_replays_with_seed():
    pytest.importorskip("dynesty")
    from app.pipeline.nodes.bayesian_fit import bayesian_fit

    input_data = {
        "x": np.linspace(0, 1, 8).tolist(),
        "y": np.linspace(0, 1, 8).tolist(),
        "y_err": [0.1] * 8,
    }
    params = {
        "method": "nested",
        "model": "polynomial",
        "n_params": 1,
        "n_live": 30,
        "random_seed": 123,
    }

    first = bayesian_fit(input_data, params)
    second = bayesian_fit(input_data, params)

    assert first["random_seed"] == second["random_seed"] == 123
    assert first["logZ"] == second["logZ"]
    assert first["samples"] == second["samples"]


def test_bayesian_emcee_receives_explicit_random_state(monkeypatch):
    from app.pipeline.nodes.bayesian_fit import bayesian_fit
    from app.services import bayesian_inference

    class RecordingSampler:
        instances: list["RecordingSampler"] = []

        def __init__(self, n_walkers, ndim, _log_prob):
            self.n_walkers = n_walkers
            self.ndim = ndim
            self.random_state = None
            self.__class__.instances.append(self)

        def run_mcmc(self, p0, n_steps, progress=False):
            del progress
            self.chain = np.repeat(np.asarray(p0)[None, :, :], n_steps, axis=0)

        def get_chain(self, discard=0, flat=False):
            chain = self.chain[discard:]
            return chain.reshape((-1, self.ndim)) if flat else chain

    monkeypatch.setitem(
        sys.modules,
        "emcee",
        SimpleNamespace(EnsembleSampler=RecordingSampler),
    )
    monkeypatch.setattr(
        bayesian_inference,
        "chain_diagnostics",
        lambda *_args, **_kwargs: {"publication_ready": False},
    )
    input_data = {
        "x": np.linspace(0, 1, 8).tolist(),
        "y": np.linspace(0, 1, 8).tolist(),
        "y_err": [0.1] * 8,
    }
    params = {
        "method": "mcmc",
        "model": "polynomial",
        "n_params": 1,
        "n_walkers": 8,
        "n_steps": 4,
        "n_burn": 1,
        "random_seed": 2718,
    }

    first = bayesian_fit(input_data, params)
    second = bayesian_fit(input_data, params)

    assert first["random_seed"] == second["random_seed"] == 2718
    first_state = RecordingSampler.instances[0].random_state
    second_state = RecordingSampler.instances[1].random_state
    assert first_state[0] == second_state[0]
    np.testing.assert_array_equal(first_state[1], second_state[1])
    assert first_state[2:] == second_state[2:]


def test_seeded_ultranest_fails_closed():
    from app.pipeline.nodes.bayesian_fit import bayesian_fit
    from app.services.bayesian_inference import nested_sampling

    with pytest.raises(ValueError, match="deterministic replay is unsupported"):
        nested_sampling(
            lambda theta: -float(np.sum(np.square(theta))),
            lambda unit: unit,
            1,
            method="ultranest",
            random_seed=5,
        )

    result = bayesian_fit(
        {"x": [0.0, 1.0], "y": [0.0, 1.0], "y_err": [0.1, 0.1]},
        {
            "method": "ultranest",
            "model": "polynomial",
            "n_params": 1,
            "random_seed": 5,
        },
    )
    assert result["error_class"] == "deterministic_replay_unsupported"
    assert result["random_seed"] == 5
    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True
