"""Pipeline node: Bayesian model fitting with nested sampling or MCMC."""
import logging
import numpy as np
logger = logging.getLogger(__name__)

def bayesian_fit(input_data: dict, params: dict) -> dict:
    """Perform Bayesian model fitting on input data.

    Phase 1 / R5 — determinism: respects an optional ``random_seed`` param.
    When supplied, we seed numpy *and* derive an emcee-compatible RNG so
    running the same input twice yields identical samples (modulo library
    versions).  The effective seed is recorded in the returned result so
    audit logs / replays can reproduce it.
    """
    method = params.get("method", "mcmc")

    # R5: deterministic seed.  A caller can pass their own seed, or we
    # derive one from the hash of the input signature so repeated runs with
    # the same data+params replay bit-exact.
    user_seed = params.get("random_seed")
    if user_seed is None:
        import hashlib
        import json as _json
        sig_bytes = _json.dumps(
            {"method": method, "params": params, "x_len": len(input_data.get("x", []))},
            sort_keys=True, default=str,
        ).encode()
        user_seed = int.from_bytes(hashlib.sha256(sig_bytes).digest()[:4], "big")
    rng = np.random.default_rng(int(user_seed))
    np.random.seed(int(user_seed) & 0xFFFF_FFFF)  # legacy calls inside emcee

    # Get data
    x = np.array(input_data.get("x", input_data.get("wavelength", [])))
    y = np.array(input_data.get("y", input_data.get("flux", [])))
    y_err = np.array(input_data.get("y_err", input_data.get("flux_err", [])))

    if len(x) == 0 or len(y) == 0:
        return {**input_data, "error": "No data provided", "node_type": "BayesianFit"}

    if len(y_err) == 0:
        y_err = np.ones_like(y) * np.std(y) * 0.1

    # Model selection
    model_type = params.get("model", "polynomial")
    n_params_model = params.get("n_params", 3)

    # D1.2 — caller-supplied priors override the default flat priors.
    # Schema: priors = {param_name: {type: "normal"|"uniform"|"lognormal",
    #                                 mean|low|mu, std|high|sigma}}
    # Priors are applied in ADDITION to the default prior_transform during
    # MCMC (as extra log-prior terms) so the caller can keep one param
    # uniform and tighten another with a normal prior without rewriting
    # the transform.
    priors_spec = params.get("priors") or {}

    if model_type == "polynomial":
        def model_func(theta):
            return np.polyval(theta, x)
        ndim = n_params_model

        def log_likelihood(theta):
            model = model_func(theta)
            return -0.5 * np.sum(((y - model) / y_err) ** 2)

        def prior_transform(u):
            # Wide uniform priors
            return (u - 0.5) * 20.0

    elif model_type == "gaussian":
        ndim = 3  # amplitude, center, sigma

        def model_func(theta):
            a, mu, sig = theta
            return a * np.exp(-0.5 * ((x - mu) / sig) ** 2)

        def log_likelihood(theta):
            model = model_func(theta)
            return -0.5 * np.sum(((y - model) / y_err) ** 2)

        def prior_transform(u):
            # Index the LAST axis so this works for both a single 1-D unit-cube
            # point (nested sampling passes shape (3,)) and a (n_walkers, 3)
            # matrix (the MCMC path passes one row per walker). Indexing u[0]
            # directly would mistake the first WALKER for the amplitude axis and
            # return shape (3, 3) instead of (n_walkers, 3).
            u = np.asarray(u)
            a = u[..., 0] * np.max(y) * 2
            mu = x.min() + u[..., 1] * (x.max() - x.min())
            sig = 0.1 + u[..., 2] * (x.max() - x.min()) / 2
            return np.stack([a, mu, sig], axis=-1)
    else:
        return {**input_data, "error": f"Unknown model: {model_type}", "node_type": "BayesianFit"}

    # Build a closure that evaluates extra prior log-probability from
    # the caller's ``priors`` dict, keyed by positional parameter index.
    _param_names_for_priors = params.get("param_names", [f"p{i}" for i in range(ndim)])

    def _extra_log_prior(theta: np.ndarray) -> float:
        lp = 0.0
        for i, pname in enumerate(_param_names_for_priors):
            spec = priors_spec.get(pname)
            if not spec:
                continue
            ptype = str(spec.get("type", "uniform")).lower()
            val = float(theta[i])
            if ptype == "normal":
                mean = float(spec.get("mean", spec.get("mu", 0.0)))
                std = float(spec.get("std", spec.get("sigma", 1.0)))
                if std <= 0:
                    continue
                lp += -0.5 * ((val - mean) / std) ** 2 - 0.5 * np.log(2 * np.pi * std * std)
            elif ptype == "lognormal":
                mean = float(spec.get("mean", spec.get("mu", 0.0)))
                std = float(spec.get("std", spec.get("sigma", 1.0)))
                if val <= 0 or std <= 0:
                    return -np.inf
                lp += -0.5 * ((np.log(val) - mean) / std) ** 2 - np.log(val * std * np.sqrt(2 * np.pi))
            elif ptype == "uniform":
                low = float(spec.get("low", -np.inf))
                high = float(spec.get("high", np.inf))
                if val < low or val > high:
                    return -np.inf
        return float(lp)

    if method in ("nested", "dynesty", "ultranest"):
        from app.services.bayesian_inference import nested_sampling
        ns_method = "ultranest" if method == "ultranest" else "dynesty"
        result = nested_sampling(log_likelihood, prior_transform, ndim,
                                nlive=params.get("n_live", 500),
                                method=ns_method)
    elif method == "mcmc":
        try:
            import emcee
            n_walkers = params.get("n_walkers", max(2 * ndim + 2, 32))
            n_steps = params.get("n_steps", 2000)
            n_burn = params.get("n_burn", 500)

            def log_prob(theta):
                # D1.2 — proper prior term from caller-supplied dict.
                lp = _extra_log_prior(theta)
                if not np.isfinite(lp):
                    return -np.inf
                return lp + log_likelihood(theta)

            # M22 + L4 (audit 2026-04-20): hardened walker initialisation.
            # emcee can silently produce garbage chains -- if most walkers start
            # in the -inf region (prior violation) the chain never explores the
            # true posterior, yet ESS / R-hat diagnostics give a "false pass"
            # (Gelman & Shirley 2011). The old strategy retried only once with a
            # loose 50% threshold. Changed to:
            #   - 3 retry attempts
            #   - threshold raised to 80% finite (below this the run is unreliable)
            #   - on ultimate failure raise InsufficientPriorSupport; callers should
            #     tighten the prior or tell the user the data does not fit the model,
            #     rather than running a garbage MCMC.
            def _initial_finite_ratio(p):
                logs = np.asarray([log_prob(row) for row in p])
                finite = np.isfinite(logs)
                return float(finite.sum()) / max(1, len(logs))

            MIN_FINITE_RATIO = 0.80
            MAX_INIT_RETRIES = 3
            p0 = prior_transform(rng.random((n_walkers, ndim)))
            best_ratio = _initial_finite_ratio(p0)
            attempt_history = [best_ratio]
            for retry_i in range(MAX_INIT_RETRIES):
                if best_ratio >= MIN_FINITE_RATIO:
                    break
                p0_try = prior_transform(rng.random((n_walkers, ndim)))
                ratio_try = _initial_finite_ratio(p0_try)
                attempt_history.append(ratio_try)
                if ratio_try > best_ratio:
                    best_ratio = ratio_try
                    p0 = p0_try

            if best_ratio < MIN_FINITE_RATIO:
                raise ValueError(
                    f"BayesianFit (InsufficientPriorSupport): after "
                    f"{MAX_INIT_RETRIES + 1} init attempts, best finite-"
                    f"log-prob ratio was {best_ratio:.2%} (required "
                    f"≥{MIN_FINITE_RATIO:.0%}).  Attempt history: "
                    f"{[f'{r:.2%}' for r in attempt_history]}.  Running MCMC "
                    f"with walkers stuck in zero-probability region would "
                    f"produce garbage chains that pass ESS/Rhat diagnostics "
                    f"falsely (Gelman & Shirley 2011). Fix: tighten priors, "
                    f"check likelihood sign, or widen the data/model."
                )
            sampler = emcee.EnsembleSampler(n_walkers, ndim, log_prob)
            sampler.run_mcmc(p0, n_steps, progress=False)

            flat_samples = sampler.get_chain(discard=n_burn, flat=True)

            result = {
                "samples": flat_samples.tolist(),
                "n_samples": len(flat_samples),
                "method": "mcmc",
                "random_seed": int(user_seed),  # R5: replay key
            }

            # Add chain diagnostics
            from app.services.bayesian_inference import chain_diagnostics
            param_names = params.get("param_names", [f"p{i}" for i in range(ndim)])
            result["diagnostics"] = chain_diagnostics(flat_samples, param_names)

        except ImportError:
            return {**input_data, "error": "emcee not installed", "node_type": "BayesianFit"}
    else:
        return {**input_data, "error": f"Unknown method: {method}", "node_type": "BayesianFit"}

    # Compute parameter summaries
    samples = np.array(result["samples"])
    param_names = params.get("param_names", [f"p{i}" for i in range(ndim)])
    # D1.1 — report HDI (highest-density interval) alongside quantile
    # percentiles.  For symmetric posteriors the two agree; for
    # asymmetric ones HDI is the tighter, more honest credible interval.
    try:
        import arviz as _az
    except ImportError:
        _az = None

    summary = {}
    for i, name in enumerate(param_names):
        col = samples[:, i]
        row = {
            "median": float(np.median(col)),
            "mean": float(np.mean(col)),
            "std": float(np.std(col)),
            "q16": float(np.percentile(col, 16)),
            "q84": float(np.percentile(col, 84)),
        }
        if _az is not None:
            try:
                hdi68 = _az.hdi(col, hdi_prob=0.68).tolist()
                hdi94 = _az.hdi(col, hdi_prob=0.94).tolist()
                row["hdi_68"] = [float(hdi68[0]), float(hdi68[1])]
                row["hdi_94"] = [float(hdi94[0]), float(hdi94[1])]
            except Exception:
                pass
        summary[name] = row

    result["parameter_summary"] = summary
    result["model_type"] = model_type
    result["node_type"] = "BayesianFit"
    # D1.2 — record which priors were applied so downstream audit /
    # reproducibility-envelope consumers can see them.
    if priors_spec:
        result["priors_applied"] = priors_spec

    # D1.1 — expose the publication-readiness flag at the node level.
    diags = result.get("diagnostics") or {}
    if isinstance(diags, dict):
        result["publication_ready"] = bool(diags.get("publication_ready", False))

    return {**input_data, **result}
