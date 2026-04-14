"""Advanced Bayesian inference service using dynesty, ultranest, and ArviZ.

Provides nested sampling for Bayesian evidence, MCMC chain diagnostics,
posterior predictive checks, and model comparison tables.
"""

import logging
import numpy as np
from typing import Any, Callable

logger = logging.getLogger(__name__)


def nested_sampling(
    log_likelihood: Callable,
    prior_transform: Callable,
    ndim: int,
    nlive: int = 500,
    method: str = "dynesty",
    maxiter: int | None = None,
    dlogz: float = 0.5,
) -> dict[str, Any]:
    """Run nested sampling to compute Bayesian evidence and posterior.

    Args:
        log_likelihood: function(theta) -> log L
        prior_transform: function(u) -> theta, maps [0,1]^n to parameter space
        ndim: number of parameters
        nlive: number of live points
        method: 'dynesty' or 'ultranest'
        maxiter: maximum iterations
        dlogz: stopping criterion (evidence precision)

    Returns:
        dict with logZ, logZ_err, samples, weights, parameter_posteriors
    """
    if method == "dynesty":
        try:
            import dynesty

            sampler = dynesty.NestedSampler(
                log_likelihood, prior_transform, ndim,
                nlive=nlive,
            )
            sampler.run_nested(maxiter=maxiter, dlogz=dlogz)
            results = sampler.results

            # Extract weighted samples
            from dynesty.utils import resample_equal
            samples = resample_equal(results.samples, np.exp(results.logwt - results.logz[-1]))

            return {
                "logZ": float(results.logz[-1]),
                "logZ_err": float(results.logzerr[-1]),
                "samples": samples.tolist(),
                "n_samples": len(samples),
                "n_iterations": results.niter,
                "n_likelihood_calls": int(results.ncall),
                "method": "dynesty",
            }
        except ImportError:
            logger.warning("dynesty not available, falling back to ultranest")
            method = "ultranest"

    if method == "ultranest":
        try:
            import ultranest

            param_names = [f"p{i}" for i in range(ndim)]
            sampler = ultranest.ReactiveNestedSampler(
                param_names, log_likelihood, prior_transform,
            )
            result = sampler.run(min_num_live_points=nlive, dlogz=dlogz,
                                max_ncalls=maxiter or 100000)

            samples = result["posterior"]["points"]

            return {
                "logZ": float(result["logz"]),
                "logZ_err": float(result["logzerr"]),
                "samples": samples.tolist(),
                "n_samples": len(samples),
                "n_iterations": int(result.get("niter", 0)),
                "n_likelihood_calls": int(result.get("ncall", 0)),
                "method": "ultranest",
            }
        except ImportError:
            raise ImportError("Neither dynesty nor ultranest is installed")

    raise ValueError(f"Unknown method: {method}")


def compute_bayes_factor(logZ1: float, logZ2: float) -> dict:
    """Compute Bayes factor and interpret on Jeffreys scale.

    B = Z1/Z2, so ln(B) = logZ1 - logZ2
    Positive ln(B) favors model 1.
    """
    ln_B = logZ1 - logZ2
    B = np.exp(min(ln_B, 500))  # Prevent overflow

    abs_ln = abs(ln_B)
    if abs_ln < 1.0:
        strength = "inconclusive"
    elif abs_ln < 2.5:
        strength = "moderate"
    elif abs_ln < 5.0:
        strength = "strong"
    else:
        strength = "decisive"

    favored = "model_1" if ln_B > 0 else "model_2"

    return {
        "ln_bayes_factor": float(ln_B),
        "bayes_factor": float(B) if abs(ln_B) < 500 else "overflow",
        "strength": strength,
        "favored": favored,
        "jeffreys_scale": {
            "< 1.0": "inconclusive",
            "1.0 - 2.5": "moderate",
            "2.5 - 5.0": "strong",
            "> 5.0": "decisive",
        },
    }


def chain_diagnostics(
    samples: np.ndarray | list,
    parameter_names: list[str] | None = None,
    n_chains: int = 1,
) -> dict[str, Any]:
    """Compute MCMC chain diagnostics using ArviZ.

    Returns R-hat, ESS (effective sample size), MCSE for each parameter.
    """
    samples = np.array(samples)

    if parameter_names is None:
        parameter_names = [f"p{i}" for i in range(samples.shape[1] if samples.ndim > 1 else 1)]

    try:
        import arviz as az

        if samples.ndim == 2:
            # Reshape to (n_chains, n_samples, n_params)
            n_total = samples.shape[0]
            chain_len = n_total // n_chains
            samples_reshaped = samples[:chain_len * n_chains].reshape(n_chains, chain_len, -1)
        else:
            samples_reshaped = samples.reshape(1, -1, 1)

        data_dict = {name: samples_reshaped[:, :, i] for i, name in enumerate(parameter_names)}
        idata = az.from_dict({"posterior": data_dict})

        rhat = az.rhat(idata)
        ess = az.ess(idata)
        mcse = az.mcse(idata)

        diagnostics = {}
        for name in parameter_names:
            rhat_val = float(rhat[name].values) if hasattr(rhat[name], 'values') else float(rhat[name])
            ess_val = float(ess[name].values) if hasattr(ess[name], 'values') else float(ess[name])
            mcse_val = float(mcse[name].values) if hasattr(mcse[name], 'values') else float(mcse[name])

            if rhat_val < 1.01:
                status = "good"
            elif rhat_val < 1.1:
                status = "marginal"
            else:
                status = "not_converged"

            diagnostics[name] = {
                "rhat": round(rhat_val, 4),
                "ess": round(ess_val, 1),
                "mcse": round(mcse_val, 6),
                "status": status,
            }

        overall = "converged" if all(d["status"] == "good" for d in diagnostics.values()) else "check_required"

        return {"parameters": diagnostics, "overall_status": overall, "n_chains": n_chains}

    except ImportError:
        # Fallback without ArviZ
        diagnostics = {}
        for i, name in enumerate(parameter_names):
            col = samples[:, i] if samples.ndim > 1 else samples
            diagnostics[name] = {
                "mean": float(np.mean(col)),
                "std": float(np.std(col)),
                "median": float(np.median(col)),
                "q16": float(np.percentile(col, 16)),
                "q84": float(np.percentile(col, 84)),
                "status": "arviz_unavailable",
            }
        return {"parameters": diagnostics, "overall_status": "arviz_unavailable", "n_chains": n_chains}


def posterior_predictive_check(
    model_func: Callable,
    posterior_samples: np.ndarray | list,
    observed_data: np.ndarray | list,
    n_samples: int = 100,
) -> dict:
    """Generate posterior predictive samples and compute p-values."""
    samples = np.array(posterior_samples)
    obs = np.array(observed_data)

    # Draw n_samples from posterior
    indices = np.random.choice(len(samples), size=min(n_samples, len(samples)), replace=False)

    predictions = []
    for idx in indices:
        try:
            pred = model_func(samples[idx])
            predictions.append(np.array(pred))
        except Exception as e:
            logger.debug("Posterior prediction failed for sample %d: %s", idx, e)
            continue

    if not predictions:
        return {"error": "No valid predictions generated"}

    predictions = np.array(predictions)
    pred_mean = np.mean(predictions, axis=0)
    pred_std = np.std(predictions, axis=0)

    # Bayesian p-value: fraction of predictions more extreme than observed
    p_values = np.mean(predictions >= obs[None, :], axis=0)
    overall_p = float(np.mean(p_values))

    return {
        "predicted_mean": pred_mean.tolist(),
        "predicted_std": pred_std.tolist(),
        "p_values": p_values.tolist(),
        "overall_p_value": overall_p,
        "n_predictions": len(predictions),
        "calibration": "good" if 0.1 < overall_p < 0.9 else "poor",
    }


def model_comparison_table(
    models: dict[str, dict],
) -> dict:
    """Compare models using information criteria and/or Bayesian evidence.

    Args:
        models: {name: {"logZ": float, "chi2": float, "n_params": int, "n_data": int}}

    Returns sorted comparison table.
    """
    rows = []
    for name, info in models.items():
        n_params = info.get("n_params", 0)
        n_data = info.get("n_data", 1)
        chi2 = info.get("chi2", 0)
        logZ = info.get("logZ")

        row = {"model": name, "n_params": n_params}

        if chi2 > 0:
            row["chi2"] = float(chi2)
            row["chi2_reduced"] = float(chi2 / max(n_data - n_params, 1))
            row["AIC"] = float(chi2 + 2 * n_params)
            row["BIC"] = float(chi2 + n_params * np.log(n_data))

        if logZ is not None:
            row["logZ"] = float(logZ)

        rows.append(row)

    # Sort by BIC (or logZ if available)
    if all("logZ" in r for r in rows):
        rows.sort(key=lambda r: -r["logZ"])
        best = rows[0]
        for r in rows:
            r["delta_logZ"] = float(r["logZ"] - best["logZ"])
    elif all("BIC" in r for r in rows):
        rows.sort(key=lambda r: r["BIC"])
        best = rows[0]
        for r in rows:
            r["delta_BIC"] = float(r["BIC"] - best["BIC"])
            if r["delta_BIC"] < 2:
                r["verdict"] = "comparable"
            elif r["delta_BIC"] < 6:
                r["verdict"] = "disfavored"
            elif r["delta_BIC"] < 10:
                r["verdict"] = "strongly_disfavored"
            else:
                r["verdict"] = "ruled_out"

    return {"ranking": rows, "best_model": rows[0]["model"] if rows else None}
