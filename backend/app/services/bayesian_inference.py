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

        # Ensure samples are float (newer arviz is strict about dtypes)
        samples = np.asarray(samples, dtype=float)

        if samples.ndim == 2:
            # Reshape to (n_chains, n_samples_per_chain, n_params)
            n_total = samples.shape[0]
            chain_len = n_total // n_chains
            samples_reshaped = samples[:chain_len * n_chains].reshape(n_chains, chain_len, -1)
        else:
            samples_reshaped = samples.reshape(1, -1, 1)

        # arviz.from_dict expects {name: array of shape (chains, draws)}
        # i.e. each parameter is a 2D array, not 3D
        data_dict = {
            name: np.ascontiguousarray(samples_reshaped[:, :, i], dtype=float)
            for i, name in enumerate(parameter_names)
        }
        # New arviz API: pass the dict as posterior= keyword, not wrapped
        idata = az.from_dict(posterior=data_dict)

        rhat = az.rhat(idata)
        ess_bulk = az.ess(idata, method="bulk")
        ess_tail = az.ess(idata, method="tail")
        mcse = az.mcse(idata)
        # D1.1 publication gate thresholds (Gelman BDA3; Vehtari+ 2021):
        #   ESS >= 400 per parameter, r-hat < 1.05.  Below these, the
        #   posterior cannot be trusted for quantitative inference and the
        #   zero-fabrication gate must refuse to cite the resulting values.
        ESS_PUBLICATION_THRESHOLD = 400.0
        RHAT_PUBLICATION_THRESHOLD = 1.05

        def _f(obj) -> float:
            return float(obj.values) if hasattr(obj, "values") else float(obj)

        diagnostics = {}
        insufficient = []
        for name in parameter_names:
            col = samples_reshaped[:, :, parameter_names.index(name)].ravel()
            try:
                hdi_vals = az.hdi(col, hdi_prob=0.94).tolist()
                hdi_low, hdi_high = float(hdi_vals[0]), float(hdi_vals[1])
            except Exception:
                hdi_low = float(np.percentile(col, 3.0))
                hdi_high = float(np.percentile(col, 97.0))

            rhat_val = _f(rhat[name])
            ess_bulk_val = _f(ess_bulk[name])
            ess_tail_val = _f(ess_tail[name])
            mcse_val = _f(mcse[name])

            if rhat_val < 1.01 and ess_bulk_val >= ESS_PUBLICATION_THRESHOLD:
                status = "good"
            elif rhat_val < RHAT_PUBLICATION_THRESHOLD and ess_bulk_val >= ESS_PUBLICATION_THRESHOLD / 2:
                status = "marginal"
            else:
                status = "not_converged"

            if (ess_bulk_val < ESS_PUBLICATION_THRESHOLD) or (rhat_val > RHAT_PUBLICATION_THRESHOLD):
                insufficient.append(name)

            diagnostics[name] = {
                "rhat": round(rhat_val, 4),
                "ess_bulk": round(ess_bulk_val, 1),
                "ess_tail": round(ess_tail_val, 1),
                "mcse": round(mcse_val, 6),
                "median": round(float(np.median(col)), 6),
                "hdi_low_94": round(hdi_low, 6),
                "hdi_high_94": round(hdi_high, 6),
                "status": status,
            }

        overall = "converged" if all(d["status"] == "good" for d in diagnostics.values()) else "check_required"
        # D1.1: explicit publication readiness flag.  If any parameter is
        # below the ESS or r-hat threshold, the result is NOT safe to cite
        # in a paper — the claim validator uses this to require
        # regeneration before emitting the reply.
        publication_ready = len(insufficient) == 0

        if not publication_ready:
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "mcmc_insufficient_sampling_total", 1.0,
                    params=",".join(sorted(insufficient))[:64],
                )
            except Exception:
                pass

        return {
            "parameters": diagnostics,
            "overall_status": overall,
            "publication_ready": publication_ready,
            "insufficient_params": insufficient,
            "thresholds": {
                "ess_min": ESS_PUBLICATION_THRESHOLD,
                "rhat_max": RHAT_PUBLICATION_THRESHOLD,
            },
            "n_chains": n_chains,
        }

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
    random_seed: int | None = None,
) -> dict:
    """Generate posterior predictive samples and compute Bayesian p-values.

    R5: ``random_seed`` makes the predictive draw reproducible.

    D1.2: computes multi-statistic Bayesian p-values per Gelman BDA3 §6.3.
    For each test statistic T (chi², max, min, range, median), we draw
    posterior predictive replicates y_rep and compute
        p_T = Pr(T(y_rep) >= T(y_obs) | y_obs)
    A well-calibrated model yields p_T in [0.05, 0.95] for every T;
    values outside that band flag model misspecification.  The
    per-datapoint fraction remains available (``p_values``) for backward
    compatibility, but the multi-statistic block (``stat_pvalues``) is
    the modern diagnostic.
    """
    samples = np.array(posterior_samples)
    obs = np.array(observed_data)

    rng = np.random.default_rng(random_seed)
    indices = rng.choice(len(samples), size=min(n_samples, len(samples)), replace=False)

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

    # Legacy per-datapoint p-values kept for backward compatibility.
    p_values = np.mean(predictions >= obs[None, :], axis=0)
    overall_p = float(np.mean(p_values))

    # D1.2 — multi-statistic PPC per Gelman BDA3 §6.3.
    def _stat_p(T_obs: float, T_rep: np.ndarray) -> float:
        return float(np.mean(T_rep >= T_obs))

    stat_pvalues: dict[str, float] = {}
    try:
        T_max_obs = float(np.max(obs))
        T_max_rep = np.array([np.max(p) for p in predictions])
        stat_pvalues["max"] = _stat_p(T_max_obs, T_max_rep)

        T_min_obs = float(np.min(obs))
        T_min_rep = np.array([np.min(p) for p in predictions])
        stat_pvalues["min"] = _stat_p(T_min_obs, T_min_rep)

        T_range_obs = float(np.ptp(obs))
        T_range_rep = np.array([np.ptp(p) for p in predictions])
        stat_pvalues["range"] = _stat_p(T_range_obs, T_range_rep)

        T_median_obs = float(np.median(obs))
        T_median_rep = np.array([np.median(p) for p in predictions])
        stat_pvalues["median"] = _stat_p(T_median_obs, T_median_rep)

        # Discrepancy chi-square (Gelman BDA3 eq 6.8).
        pred_var = np.var(predictions, axis=0) + 1e-30
        T_chi2_obs = float(np.sum((obs - pred_mean) ** 2 / pred_var))
        T_chi2_rep = np.array([
            np.sum((p - pred_mean) ** 2 / pred_var) for p in predictions
        ])
        stat_pvalues["chi2_discrepancy"] = _stat_p(T_chi2_obs, T_chi2_rep)
    except Exception as exc:
        logger.debug("PPC multi-statistic computation failed: %s", exc)

    # Overall calibration flag: any p outside [0.05, 0.95] flags miscalibration.
    miscalibrated = [k for k, v in stat_pvalues.items() if v < 0.05 or v > 0.95]

    return {
        "predicted_mean": pred_mean.tolist(),
        "predicted_std": pred_std.tolist(),
        "p_values": p_values.tolist(),
        "overall_p_value": overall_p,
        "stat_pvalues": stat_pvalues,
        "miscalibrated_statistics": miscalibrated,
        "n_predictions": len(predictions),
        "calibration": "good" if not miscalibrated and 0.05 < overall_p < 0.95 else "poor",
    }


def model_comparison_table(
    models: dict[str, dict],
) -> dict:
    """Compare models using information criteria and/or Bayesian evidence.

    Args:
        models: {name: {"logZ": float, "chi2": float, "n_params": int,
                       "n_data": int,
                       "log_likelihood": ndarray (optional, shape (n_samples, n_data))}}

    D1.1: when per-point log-likelihoods are supplied, we additionally
    compute WAIC (Watanabe 2010) and LOO-CV (Vehtari+ 2017 via arviz).
    LOO is preferred for small n_data where BIC's asymptotic assumption
    breaks down.  Pareto-k diagnostic is returned per model so reviewers
    can see whether LOO is itself reliable.

    Returns sorted comparison table.  When LOO available, rows are ranked
    by LOO (smaller = better predictive); else by logZ; else by BIC.
    """
    try:
        import arviz as az
    except ImportError:
        az = None

    rows = []
    for name, info in models.items():
        n_params = info.get("n_params", 0)
        n_data = info.get("n_data", 1)
        chi2 = info.get("chi2", 0)
        logZ = info.get("logZ")
        log_likelihood = info.get("log_likelihood")

        row = {"model": name, "n_params": n_params}

        if chi2 > 0:
            row["chi2"] = float(chi2)
            row["chi2_reduced"] = float(chi2 / max(n_data - n_params, 1))
            row["AIC"] = float(chi2 + 2 * n_params)
            row["BIC"] = float(chi2 + n_params * np.log(n_data))

        if logZ is not None:
            row["logZ"] = float(logZ)

        # D1.1 — WAIC/LOO via arviz when per-point log_likelihood provided.
        # arviz.loo requires a posterior group in InferenceData (a known
        # API quirk as of 0.17+).  We fabricate a trivial posterior with
        # the right (chains, draws) shape so LOO's machinery is happy;
        # the posterior values themselves are not used by PSIS-LOO.
        if log_likelihood is not None and az is not None:
            try:
                ll = np.asarray(log_likelihood, dtype=float)
                if ll.ndim == 2:
                    ll_shaped = ll[None, :, :]
                elif ll.ndim == 3:
                    ll_shaped = ll
                else:
                    raise ValueError(f"log_likelihood must be 2D or 3D, got shape {ll.shape}")
                n_chains, n_draws, _n_obs = ll_shaped.shape
                idata = az.from_dict(
                    posterior={"_dummy": np.zeros((n_chains, n_draws))},
                    log_likelihood={name: ll_shaped},
                )
                # Scale "deviance" gives -2 * elpd; "log" gives +elpd.
                # We want the larger-is-worse convention downstream, so
                # store deviance-scale values directly (no extra *-2).
                waic_res = az.waic(idata, var_name=name, scale="deviance")
                loo_res = az.loo(idata, var_name=name, scale="deviance")
                row["WAIC"] = float(waic_res.elpd_waic)
                row["WAIC_se"] = float(waic_res.se)
                row["LOO"] = float(loo_res.elpd_loo)
                row["LOO_se"] = float(loo_res.se)
                pk = np.asarray(loo_res.pareto_k)
                row["pareto_k_max"] = float(np.max(pk)) if pk.size else None
                row["pareto_k_fraction_gt_07"] = float(np.mean(pk > 0.7)) if pk.size else None
            except Exception as exc:
                logger.debug("WAIC/LOO computation failed for %s: %s", name, exc)

        rows.append(row)

    # Sort preference: LOO > logZ > BIC.
    if all("LOO" in r for r in rows):
        rows.sort(key=lambda r: r["LOO"])
        best = rows[0]
        for r in rows:
            r["delta_LOO"] = float(r["LOO"] - best["LOO"])
            if r["delta_LOO"] < 2:
                r["verdict"] = "comparable"
            elif r["delta_LOO"] < 6:
                r["verdict"] = "disfavored"
            elif r["delta_LOO"] < 10:
                r["verdict"] = "strongly_disfavored"
            else:
                r["verdict"] = "ruled_out"
    elif all("logZ" in r for r in rows):
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
