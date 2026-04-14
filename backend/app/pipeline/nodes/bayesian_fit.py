"""Pipeline node: Bayesian model fitting with nested sampling or MCMC."""
import logging
import numpy as np
logger = logging.getLogger(__name__)

def bayesian_fit(input_data: dict, params: dict) -> dict:
    """Perform Bayesian model fitting on input data."""
    method = params.get("method", "mcmc")

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
            a = u[0] * np.max(y) * 2
            mu = x.min() + u[1] * (x.max() - x.min())
            sig = 0.1 + u[2] * (x.max() - x.min()) / 2
            return np.array([a, mu, sig])
    else:
        return {**input_data, "error": f"Unknown model: {model_type}", "node_type": "BayesianFit"}

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
                lp = 0.0  # flat prior
                if not np.isfinite(lp):
                    return -np.inf
                return lp + log_likelihood(theta)

            p0 = prior_transform(np.random.rand(n_walkers, ndim))
            sampler = emcee.EnsembleSampler(n_walkers, ndim, log_prob)
            sampler.run_mcmc(p0, n_steps, progress=False)

            flat_samples = sampler.get_chain(discard=n_burn, flat=True)

            result = {
                "samples": flat_samples.tolist(),
                "n_samples": len(flat_samples),
                "method": "mcmc",
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
    summary = {}
    for i, name in enumerate(param_names):
        col = samples[:, i]
        summary[name] = {
            "median": float(np.median(col)),
            "mean": float(np.mean(col)),
            "std": float(np.std(col)),
            "q16": float(np.percentile(col, 16)),
            "q84": float(np.percentile(col, 84)),
        }

    result["parameter_summary"] = summary
    result["model_type"] = model_type
    result["node_type"] = "BayesianFit"

    return {**input_data, **result}
