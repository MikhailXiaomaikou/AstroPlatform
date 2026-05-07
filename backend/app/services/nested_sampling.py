"""Controlled nested-sampling runner.

This module intentionally accepts only typed Gaussian likelihood summaries.
It does not execute user-provided Python, Cobaya YAML, or arbitrary
likelihood code.  The paper-to-tool mining loop repeatedly surfaced nested
sampling / evidence comparison as a missing research primitive, so this is
the small safe kernel the platform can expose before full external
likelihood adapters are ready.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from importlib import metadata
from typing import Any

import numpy as np


MAX_DIMENSIONS = 8
MIN_NLIVE_FOR_PUBLICATION_READY = 50
MIN_ESS_FOR_PUBLICATION_READY = 80.0
MAX_LOGZERR_FOR_PUBLICATION_READY = 0.75


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    prior: tuple[float, float]


@dataclass(frozen=True)
class GaussianLikelihoodSpec:
    label: str
    parameters: tuple[str, ...]
    mean: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]
    citation: str | None = None
    source_url: str | None = None


def run_controlled_nested_sampler(
    *,
    parameters: list[dict[str, Any]],
    gaussian_likelihood: dict[str, Any] | None = None,
    likelihoods: list[dict[str, Any]] | None = None,
    sampler_config: dict[str, Any] | None = None,
    random_seed: int | None = None,
) -> dict[str, Any]:
    """Run dynesty on one or more typed Gaussian likelihood summaries."""
    try:
        from dynesty import NestedSampler
        from dynesty import utils as dyfunc
    except Exception as exc:  # pragma: no cover - depends on environment
        return {
            "success": False,
            "__tool_status__": "UNAVAILABLE",
            "analysis_status": "UNAVAILABLE",
            "publication_ready": False,
            "error": f"dynesty is not available: {exc}",
            "error_class": "missing_dynesty",
            "__do_not_claim__": True,
            "__message_to_model__": (
                "Nested sampling is unavailable in this backend. Do not quote "
                "posterior or evidence values from this call."
            ),
        }

    try:
        parameter_specs = _parse_parameters(parameters)
        likelihood_specs = _parse_likelihoods(gaussian_likelihood, likelihoods)
        _validate_likelihoods(parameter_specs, likelihood_specs)
    except ValueError as exc:
        return _failed(str(exc), "invalid_nested_sampler_input")

    config = dict(sampler_config or {})
    seed = int(random_seed if random_seed is not None else config.get("random_seed", 20260507))
    nlive = max(20, min(int(config.get("nlive", 120)), 1000))
    dlogz = max(0.01, float(config.get("dlogz", 0.5)))
    maxiter_raw = config.get("maxiter")
    maxiter = int(maxiter_raw) if maxiter_raw is not None else None
    sample_method = str(config.get("sample") or "rwalk")
    if sample_method not in {"auto", "unif", "rwalk", "slice", "rslice", "hslice"}:
        return _failed(f"unsupported dynesty sample method: {sample_method}", "invalid_sampler_config")

    ndim = len(parameter_specs)
    names = [spec.name for spec in parameter_specs]
    lows = np.asarray([spec.prior[0] for spec in parameter_specs], dtype=float)
    widths = np.asarray([spec.prior[1] - spec.prior[0] for spec in parameter_specs], dtype=float)
    prepared_likelihoods = [_prepare_gaussian(spec, names) for spec in likelihood_specs]

    def prior_transform(unit_cube: np.ndarray) -> np.ndarray:
        return lows + widths * np.asarray(unit_cube, dtype=float)

    def loglikelihood(theta: np.ndarray) -> float:
        total = 0.0
        theta_map = {name: float(theta[index]) for index, name in enumerate(names)}
        for prepared in prepared_likelihoods:
            values = np.asarray([theta_map[name] for name in prepared["parameters"]], dtype=float)
            residual = values - prepared["mean"]
            total += float(prepared["log_norm"] - 0.5 * residual.T @ prepared["inv_cov"] @ residual)
        return total

    warnings: list[str] = [
        "Controlled Gaussian nested sampler; this is not a full external Cobaya/CosmoSIS likelihood.",
    ]
    if nlive < MIN_NLIVE_FOR_PUBLICATION_READY:
        warnings.append(f"nlive={nlive} is below publication-ready threshold {MIN_NLIVE_FOR_PUBLICATION_READY}.")

    rng = np.random.default_rng(seed)
    try:
        sampler = NestedSampler(
            loglikelihood,
            prior_transform,
            ndim,
            nlive=nlive,
            sample=sample_method,
            rstate=rng,
        )
        sampler.run_nested(dlogz=dlogz, maxiter=maxiter, print_progress=False)
        results = sampler.results
    except Exception as exc:
        return _failed(f"dynesty run failed: {exc}", exc.__class__.__name__)

    logz = float(results.logz[-1])
    logzerr = float(results.logzerr[-1])
    weights = np.exp(np.asarray(results.logwt, dtype=float) - logz)
    weight_sum = float(np.sum(weights))
    if not math.isfinite(weight_sum) or weight_sum <= 0:
        return _failed("nested sampler produced invalid posterior weights", "invalid_weights")
    weights = weights / weight_sum
    ess = float(1.0 / np.sum(np.square(weights)))
    equal_count = max(200, min(5000, int(np.asarray(results.samples).shape[0])))
    equal_samples = dyfunc.resample_equal(results.samples, weights, rstate=rng)
    if equal_samples.shape[0] > equal_count:
        take = rng.choice(equal_samples.shape[0], size=equal_count, replace=False)
        equal_samples = equal_samples[np.sort(take)]

    summaries = {
        name: _posterior_summary(equal_samples[:, index])
        for index, name in enumerate(names)
    }
    publication_ready = (
        nlive >= MIN_NLIVE_FOR_PUBLICATION_READY
        and ess >= MIN_ESS_FOR_PUBLICATION_READY
        and logzerr <= MAX_LOGZERR_FOR_PUBLICATION_READY
    )
    if ess < MIN_ESS_FOR_PUBLICATION_READY:
        warnings.append(f"posterior ESS={ess:.1f} is below threshold {MIN_ESS_FOR_PUBLICATION_READY}.")
    if logzerr > MAX_LOGZERR_FOR_PUBLICATION_READY:
        warnings.append(f"logZ error={logzerr:.3f} exceeds threshold {MAX_LOGZERR_FOR_PUBLICATION_READY}.")

    status = "NESTED_SAMPLER_READY" if publication_ready else "PARTIAL"
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if publication_ready else "PARTIAL",
        "analysis_status": status,
        "publication_ready": publication_ready,
        "claim_scope": "controlled_gaussian_nested_sampler",
        "sampler": "dynesty_nested",
        "parameters": summaries,
        "posterior_summary": summaries,
        "evidence": {
            "logz": round(logz, 6),
            "logzerr": round(logzerr, 6),
            "information": round(float(results.information[-1]), 6),
        },
        "chain_diagnostics": {
            "overall_status": "nested_sampling" if publication_ready else "not_publication_ready",
            "publication_ready": publication_ready,
            "nlive": int(nlive),
            "niter": int(results.niter),
            "ncall": int(np.sum(results.ncall)),
            "efficiency_percent": round(float(results.eff), 6),
            "posterior_ess": round(ess, 3),
            "n_equal_weight_samples": int(equal_samples.shape[0]),
            "thresholds": {
                "nlive_min": MIN_NLIVE_FOR_PUBLICATION_READY,
                "posterior_ess_min": MIN_ESS_FOR_PUBLICATION_READY,
                "logzerr_max": MAX_LOGZERR_FOR_PUBLICATION_READY,
            },
        },
        "priors": {spec.name: list(spec.prior) for spec in parameter_specs},
        "likelihoods_used": [_likelihood_to_dict(spec) for spec in likelihood_specs],
        "random_seed": seed,
        "package_versions": {"dynesty": _package_version("dynesty")},
        "warnings": warnings,
        "__message_to_model__": (
            "This is a controlled Gaussian nested-sampling result. Quote posterior "
            "or evidence values only when publication_ready=true, and state that "
            "it uses typed Gaussian summaries rather than full external likelihoods."
        ),
        "provenance": {
            "nested_sampler": {
                "runner": "dynesty_nested",
                "claim_scope": "controlled_gaussian_nested_sampler",
                "random_seed": seed,
                "likelihood_count": len(likelihood_specs),
                "publication_ready": publication_ready,
            }
        },
        "__do_not_claim__": (
            [] if publication_ready else [
                "Do not quote posterior or evidence values as research results; the nested sampler is not publication-ready."
            ]
        ),
    }


def _parse_parameters(items: list[dict[str, Any]]) -> list[ParameterSpec]:
    if not isinstance(items, list) or not items:
        raise ValueError("parameters must be a non-empty list")
    if len(items) > MAX_DIMENSIONS:
        raise ValueError(f"at most {MAX_DIMENSIONS} parameters are supported")
    seen: set[str] = set()
    parsed: list[ParameterSpec] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each parameter must be an object")
        name = str(item.get("name") or "").strip()
        prior = item.get("prior")
        if not name:
            raise ValueError("each parameter requires a name")
        if name in seen:
            raise ValueError(f"duplicate parameter name: {name}")
        if not isinstance(prior, (list, tuple)) or len(prior) != 2:
            raise ValueError(f"parameter {name} requires a [min, max] prior")
        low = float(prior[0])
        high = float(prior[1])
        if not (math.isfinite(low) and math.isfinite(high) and low < high):
            raise ValueError(f"parameter {name} prior must be finite with min < max")
        seen.add(name)
        parsed.append(ParameterSpec(name=name, prior=(low, high)))
    return parsed


def _parse_likelihoods(
    gaussian_likelihood: dict[str, Any] | None,
    likelihoods: list[dict[str, Any]] | None,
) -> list[GaussianLikelihoodSpec]:
    raw_items: list[dict[str, Any]] = []
    if isinstance(likelihoods, list) and likelihoods:
        raw_items.extend(item for item in likelihoods if isinstance(item, dict))
    elif isinstance(gaussian_likelihood, dict):
        raw_items.append(gaussian_likelihood)
    if not raw_items:
        raise ValueError("gaussian_likelihood or likelihoods is required")

    parsed: list[GaussianLikelihoodSpec] = []
    for index, item in enumerate(raw_items, start=1):
        params = item.get("parameters")
        mean = item.get("mean")
        cov = item.get("covariance")
        if not isinstance(params, list) or not params:
            raise ValueError("each likelihood requires parameters")
        if not isinstance(mean, list) or len(mean) != len(params):
            raise ValueError("likelihood mean length must match parameters")
        if not isinstance(cov, list) or len(cov) != len(params):
            raise ValueError("likelihood covariance shape must match parameters")
        cov_rows: list[tuple[float, ...]] = []
        for row in cov:
            if not isinstance(row, list) or len(row) != len(params):
                raise ValueError("likelihood covariance shape must match parameters")
            cov_rows.append(tuple(float(value) for value in row))
        parsed.append(
            GaussianLikelihoodSpec(
                label=str(item.get("label") or f"gaussian_likelihood_{index}"),
                parameters=tuple(str(param) for param in params),
                mean=tuple(float(value) for value in mean),
                covariance=tuple(cov_rows),
                citation=str(item.get("citation") or "").strip() or None,
                source_url=str(item.get("source_url") or "").strip() or None,
            )
        )
    return parsed


def _validate_likelihoods(
    parameters: list[ParameterSpec],
    likelihoods: list[GaussianLikelihoodSpec],
) -> None:
    names = {spec.name for spec in parameters}
    for likelihood in likelihoods:
        unknown = sorted(set(likelihood.parameters) - names)
        if unknown:
            raise ValueError(f"likelihood {likelihood.label} uses unknown parameters: {unknown}")
        cov = np.asarray(likelihood.covariance, dtype=float)
        if not np.allclose(cov, cov.T, rtol=1e-8, atol=1e-12):
            raise ValueError(f"likelihood {likelihood.label} covariance is not symmetric")
        try:
            np.linalg.cholesky(cov)
        except Exception as exc:
            raise ValueError(f"likelihood {likelihood.label} covariance is not positive definite") from exc


def _prepare_gaussian(spec: GaussianLikelihoodSpec, parameter_order: list[str]) -> dict[str, Any]:
    del parameter_order
    cov = np.asarray(spec.covariance, dtype=float)
    sign, logdet = np.linalg.slogdet(cov)
    if sign <= 0:
        raise ValueError(f"likelihood {spec.label} covariance determinant is not positive")
    return {
        "label": spec.label,
        "parameters": list(spec.parameters),
        "mean": np.asarray(spec.mean, dtype=float),
        "inv_cov": np.linalg.inv(cov),
        "log_norm": -0.5 * (len(spec.parameters) * math.log(2.0 * math.pi) + float(logdet)),
    }


def _posterior_summary(values: np.ndarray) -> dict[str, Any]:
    return {
        "mean": round(float(np.mean(values)), 6),
        "std": round(float(np.std(values)), 6),
        "median": round(float(np.median(values)), 6),
        "hdi_low_94": round(float(np.percentile(values, 3.0)), 6),
        "hdi_high_94": round(float(np.percentile(values, 97.0)), 6),
        "status": "nested_sampling",
    }


def _likelihood_to_dict(spec: GaussianLikelihoodSpec) -> dict[str, Any]:
    return {
        "label": spec.label,
        "parameters": list(spec.parameters),
        "mean": list(spec.mean),
        "covariance": [list(row) for row in spec.covariance],
        "citation": spec.citation,
        "source_url": spec.source_url,
    }


def _package_version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:  # pragma: no cover
        return "unknown"


def _failed(message: str, error_class: str) -> dict[str, Any]:
    return {
        "success": False,
        "__tool_status__": "FAILED",
        "analysis_status": "FAILED",
        "publication_ready": False,
        "error": message,
        "error_class": error_class,
        "__do_not_claim__": True,
        "__message_to_model__": (
            "Controlled nested sampling failed. Do not quote posterior, "
            "evidence, Bayes factor, or sampler diagnostics from this call."
        ),
    }
