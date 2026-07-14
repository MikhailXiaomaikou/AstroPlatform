"""Regression tests for deterministic emcee execution and seed plumbing."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd


class _RecordingSampler:
    instances: list["_RecordingSampler"] = []

    def __init__(self, n_walkers, n_dim, log_prob):
        self.n_walkers = n_walkers
        self.n_dim = n_dim
        self.log_prob = log_prob
        self.random_state = None
        self.acceptance_fraction = np.full(n_walkers, 0.5)
        self.__class__.instances.append(self)

    def run_mcmc(self, p0, n_steps, progress=False):
        del progress
        self.p0 = np.asarray(p0, dtype=float).copy()
        self._chain = np.repeat(self.p0[None, :, :], max(int(n_steps), 1), axis=0)

    def get_chain(self, discard=0, flat=False):
        chain = self._chain[int(discard):]
        if flat:
            return chain.reshape((-1, self.n_dim))
        return chain


def _assert_random_states_equal(left, right):
    assert left[0] == right[0]
    np.testing.assert_array_equal(left[1], right[1])
    assert left[2:] == right[2:]


def _install_recording_emcee(monkeypatch):
    _RecordingSampler.instances = []
    monkeypatch.setitem(
        sys.modules,
        "emcee",
        SimpleNamespace(EnsembleSampler=_RecordingSampler),
    )


def test_fit_isochrone_seeds_initial_walkers_and_emcee_state(monkeypatch):
    """The same seed must reproduce p0 and emcee's proposal RNG state."""
    import matplotlib.pyplot as plt
    import scipy.optimize

    from app.services import astro_analysis

    _install_recording_emcee(monkeypatch)
    monkeypatch.setitem(
        sys.modules,
        "corner",
        SimpleNamespace(corner=lambda *args, **kwargs: plt.figure()),
    )

    model_color = np.linspace(-0.5, 1.5, 20)
    isochrone = pd.DataFrame(
        {
            "G_BPmag": model_color + 0.5,
            "G_RPmag": np.full(model_color.size, 0.5),
            "Gmag": 2.0 + 3.0 * model_color,
        }
    )
    monkeypatch.setattr(astro_analysis, "get_isochrone", lambda *args: isochrone)

    def fake_minimize(fun, x0, *args, **kwargs):
        del args, kwargs
        x = np.asarray(x0, dtype=float)
        return SimpleNamespace(x=x, fun=float(fun(x)), success=True)

    monkeypatch.setattr(scipy.optimize, "minimize", fake_minimize)

    kwargs = {
        "method": "mcmc",
        "age_range": (8.0, 8.0),
        "met_range": (0.0, 0.0),
        "dm_range": (0.0, 0.0),
        "av_range": (0.0, 0.0),
        "n_grid_age": 1,
        "n_grid_met": 1,
        "n_walkers": 8,
        "n_steps": 3,
        "n_burn": 1,
        "seed": 31415,
    }
    colors = np.linspace(-0.3, 1.2, 10)
    magnitudes = 2.0 + 3.0 * colors

    first = astro_analysis.fit_isochrone(colors, magnitudes, **kwargs)
    second = astro_analysis.fit_isochrone(colors, magnitudes, **kwargs)

    assert first["random_seed"] == second["random_seed"] == 31415
    first_sampler, second_sampler = _RecordingSampler.instances
    np.testing.assert_array_equal(first_sampler.p0, second_sampler.p0)
    _assert_random_states_equal(first_sampler.random_state, second_sampler.random_state)

    expected_rng = np.random.RandomState(31415)
    expected_rng.normal(0, [0.1, 0.05, 0.2, 0.02], (8, 4))
    _assert_random_states_equal(first_sampler.random_state, expected_rng.get_state())


def test_fit_rv_orbit_seeds_initial_walkers_and_emcee_state(monkeypatch):
    """RV-orbit replay controls both the walker cloud and emcee proposals."""
    import astropy.timeseries
    import scipy.optimize

    from app.services.ai_tools.object_physics import _exec_fit_rv_orbit

    _install_recording_emcee(monkeypatch)

    class Parameter:
        def __init__(self, value, vary=True):
            self.value = value
            self.vary = vary

    class Parameters(dict):
        def __init__(self, *args, **kwargs):
            del args, kwargs
            super().__init__()

    class RVModel:
        def __init__(self, params):
            self.params = params

    class RVLikelihood:
        def __init__(self, model, *args):
            del args
            self.model = model

    class Posterior:
        def __init__(self, likelihood):
            self.params = likelihood.model.params

        def get_vary_params(self):
            return np.asarray(
                [
                    self.params[name].value
                    for name in ("per1", "tc1", "e1", "w1", "k1")
                ],
                dtype=float,
            )

        def logprob_array(self, theta):
            return -0.5 * float(np.sum(np.square(theta)))

    fake_radvel = SimpleNamespace(
        Parameters=Parameters,
        Parameter=Parameter,
        RVModel=RVModel,
        likelihood=SimpleNamespace(RVLikelihood=RVLikelihood),
        posterior=SimpleNamespace(Posterior=Posterior),
    )
    monkeypatch.setitem(sys.modules, "radvel", fake_radvel)
    monkeypatch.setitem(
        sys.modules,
        "arviz",
        SimpleNamespace(hdi=lambda values, hdi_prob: np.percentile(values, [16, 84])),
    )
    monkeypatch.setattr(
        scipy.optimize,
        "minimize",
        lambda *args, **kwargs: SimpleNamespace(success=True),
    )

    class FakeLombScargle:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def autopower(self, **kwargs):
            del kwargs
            return np.asarray([0.05, 0.1]), np.asarray([0.1, 1.0])

    monkeypatch.setattr(astropy.timeseries, "LombScargle", FakeLombScargle)

    tool_input = {
        "times": np.linspace(0.0, 20.0, 8).tolist(),
        "rvs": np.linspace(-5.0, 5.0, 8).tolist(),
        "rv_errs": [1.0] * 8,
        "use_mcmc": True,
        "n_walkers": 8,
        "n_steps": 3,
        "n_burn": 1,
        "random_seed": 2718,
    }

    first = asyncio.run(_exec_fit_rv_orbit(tool_input))
    second = asyncio.run(_exec_fit_rv_orbit(tool_input))

    assert first["random_seed"] == second["random_seed"] == 2718
    assert first["mcmc"]["random_seed"] == second["mcmc"]["random_seed"] == 2718
    first_sampler, second_sampler = _RecordingSampler.instances
    np.testing.assert_array_equal(first_sampler.p0, second_sampler.p0)
    _assert_random_states_equal(first_sampler.random_state, second_sampler.random_state)

    expected_rng = np.random.RandomState(2718)
    expected_rng.standard_normal((8, 6))
    _assert_random_states_equal(first_sampler.random_state, expected_rng.get_state())


def test_fit_isochrone_wrapper_forwards_random_seed(monkeypatch):
    from app.services import astro_analysis
    from app.services.ai_tools.stellar_tools import _exec_fit_isochrone

    seen_seeds = []

    def fake_fit(*args, **kwargs):
        del args
        seen_seeds.append(kwargs["seed"])
        return {
            "best_log_age": 8.0,
            "best_dm": 0.0,
            "best_av": 0.0,
            "chi2_reduced": 1.0,
        }

    monkeypatch.setattr(astro_analysis, "fit_isochrone", fake_fit)
    result = asyncio.run(
        _exec_fit_isochrone(
            {
                "bp_rp": [0.1, 0.2, 0.3, 0.4, 0.5],
                "abs_mag": [1.0, 1.2, 1.4, 1.6, 1.8],
                "method": "mcmc",
                "random_seed": 1234,
            }
        )
    )

    assert seen_seeds == [1234, 1234]
    assert result["random_seed"] == 1234


def test_dispatcher_injects_fit_isochrone_seed_before_execution(monkeypatch):
    from app.services import ai_tools
    from app.services.result_provenance import compute_query_hash

    seen = {}

    async def fake_inner(tool_name, tool_input, *args, **kwargs):
        del args, kwargs
        seen["tool_name"] = tool_name
        seen["input"] = dict(tool_input)
        return {"success": True, "random_seed": tool_input["random_seed"]}

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", fake_inner)
    caller_input = {"method": "mcmc"}
    expected_seed = int(
        compute_query_hash("fit_isochrone", caller_input)[:8],
        16,
    )

    result = asyncio.run(ai_tools.execute_tool("fit_isochrone", caller_input))

    assert caller_input == {"method": "mcmc"}
    assert seen["input"]["random_seed"] == expected_seed
    assert result["random_seed"] == expected_seed
    assert result["reproducibility"]["random_seed"] == expected_seed
    assert result["reproducibility"]["random_seed_source"] == "auto_from_input"
