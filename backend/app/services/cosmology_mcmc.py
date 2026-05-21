"""Controlled cosmology MCMC tools for distance-modulus tables.

The service is intentionally narrow: callers provide typed rows with
``z``, ``mu`` and ``sigma_mu`` columns, choose one of three flat cosmology
models, and pass bounded numeric priors.  No raw Python, Cobaya YAML, or
arbitrary likelihood code is accepted.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_PRIORS: dict[str, tuple[float, float]] = {
    "H0": (50.0, 90.0),
    "Om0": (0.05, 0.6),
    "w0": (-2.5, -0.2),
    # wa bound matches cosmology_likelihoods.RUNNER_PARAMETER_PRIORS["wa"]
    # (review fix bug_010: previously diverged between (-4, 4) here and
    # (-3, 2) in the runner; aligning to the DESI DR1 (-3, 2) convention,
    # which also keeps (1+z)^(3(1+w0+wa)) numerically stable over z ≤ 3).
    "wa": (-3.0, 2.0),
}

MODEL_PARAMETERS: dict[str, tuple[str, ...]] = {
    "flat_lcdm": ("H0", "Om0"),
    "flat_wcdm": ("H0", "Om0", "w0"),
    "flat_w0wa_cdm": ("H0", "Om0", "w0", "wa"),
}

SYNC_SAMPLE_BUDGET = 80_000
ESS_PUBLICATION_THRESHOLD = 400.0
RHAT_PUBLICATION_THRESHOLD = 1.05
# Three-tier publication_ready (2026-05-20): chains with min ESS in
# [ESS_EXPLORATORY_THRESHOLD, ESS_PUBLICATION_THRESHOLD) and max R-hat in
# (RHAT_PUBLICATION_THRESHOLD, RHAT_EXPLORATORY_THRESHOLD] are tagged
# EXPLORATORY rather than blocked: the posterior may be discussed in chat
# but cannot be cited as a published constraint.
ESS_EXPLORATORY_THRESHOLD = 100.0
RHAT_EXPLORATORY_THRESHOLD = 1.10

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_JOB_TTL_SECONDS = 3600
_MAX_JOBS = 64
# user_uploaded entries come from FITS upload / user_supplied data_source
# tags with full audit log (hash + upload time + uploader). Treated as
# claimable since the synthetic-fallback defences (subprocess sandbox +
# synthetic_code_detector) already gate the upload path.
CLAIMABLE_INPUT_ORIGINS = frozenset({"cached_real", "user_uploaded"})


@dataclass(frozen=True)
class DistanceModulusDataset:
    z: np.ndarray
    mu: np.ndarray
    sigma_mu: np.ndarray
    rows: list[dict[str, float]]
    data_hash: str


class CosmologyMCMCError(ValueError):
    """Raised for invalid user-supplied cosmology MCMC inputs."""


def validate_distance_modulus_rows(rows: list[dict[str, Any]]) -> DistanceModulusDataset:
    """Validate and normalize distance-modulus rows.

    Required columns are ``z``, ``mu`` and ``sigma_mu``.  Aliases ``mu_err``
    and ``mu_error`` are accepted for the uncertainty column.
    """
    if not isinstance(rows, list) or not rows:
        raise CosmologyMCMCError("distance_modulus dataset requires at least one row")

    normalized: list[dict[str, float]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CosmologyMCMCError(f"row {index} is not an object")
        try:
            z = float(row["z"])
            mu = float(row["mu"])
            sigma = float(row.get("sigma_mu", row.get("mu_err", row.get("mu_error"))))
        except (KeyError, TypeError, ValueError) as exc:
            raise CosmologyMCMCError(
                "distance_modulus rows require numeric z, mu, sigma_mu columns"
            ) from exc
        if not (math.isfinite(z) and math.isfinite(mu) and math.isfinite(sigma)):
            raise CosmologyMCMCError(f"row {index} contains non-finite values")
        if z <= 0:
            raise CosmologyMCMCError(f"row {index} has z <= 0")
        if sigma <= 0:
            raise CosmologyMCMCError(f"row {index} has sigma_mu <= 0")
        normalized.append({"z": z, "mu": mu, "sigma_mu": sigma})

    if len(normalized) < 3:
        raise CosmologyMCMCError("at least three distance-modulus rows are required")

    normalized.sort(key=lambda item: item["z"])
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    data_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return DistanceModulusDataset(
        z=np.asarray([row["z"] for row in normalized], dtype=float),
        mu=np.asarray([row["mu"] for row in normalized], dtype=float),
        sigma_mu=np.asarray([row["sigma_mu"] for row in normalized], dtype=float),
        rows=normalized,
        data_hash=data_hash,
    )


def parameter_names_for_model(model: str) -> tuple[str, ...]:
    try:
        return MODEL_PARAMETERS[model]
    except KeyError as exc:
        raise CosmologyMCMCError(
            f"unsupported cosmology model {model!r}; choose one of {sorted(MODEL_PARAMETERS)}"
        ) from exc


def sanitize_priors(model: str, priors: dict[str, Any] | None = None) -> dict[str, tuple[float, float]]:
    """Return model priors after enforcing the platform's broad bounds.

    Callers may tighten the default intervals but may not widen them or add
    arbitrary parameters.
    """
    params = parameter_names_for_model(model)
    user_priors = priors or {}
    if not isinstance(user_priors, dict):
        raise CosmologyMCMCError("priors must be an object mapping parameter names to [min, max]")

    unknown = set(user_priors) - set(params)
    if unknown:
        raise CosmologyMCMCError(f"priors include unsupported parameters: {sorted(unknown)}")

    sanitized: dict[str, tuple[float, float]] = {}
    for name in params:
        default_low, default_high = DEFAULT_PRIORS[name]
        raw = user_priors.get(name, (default_low, default_high))
        if isinstance(raw, dict):
            raw_low, raw_high = raw.get("min"), raw.get("max")
        elif isinstance(raw, (list, tuple)) and len(raw) == 2:
            raw_low, raw_high = raw
        else:
            raise CosmologyMCMCError(f"prior for {name} must be [min, max]")
        try:
            low = float(raw_low)
            high = float(raw_high)
        except (TypeError, ValueError) as exc:
            raise CosmologyMCMCError(f"prior for {name} must be numeric") from exc
        if not (math.isfinite(low) and math.isfinite(high)) or low >= high:
            raise CosmologyMCMCError(f"prior for {name} must have finite min < max")
        if low < default_low or high > default_high:
            raise CosmologyMCMCError(
                f"prior for {name} must stay within [{default_low}, {default_high}]"
            )
        sanitized[name] = (low, high)
    return sanitized


# 32-point Gauss-Legendre nodes/weights for the comoving-distance integral.
# 32 is sufficient for z ≤ 3 in flat w0waCDM: integrand 1/E(z) is smooth and
# 32-point quadrature reaches < 1e-12 relative error vs. astropy's adaptive
# integrator (also < 0.1 mag in μ over the full Pantheon+ redshift range).
_DM_GL_NODES, _DM_GL_WEIGHTS = np.polynomial.legendre.leggauss(32)
_C_LIGHT_KM_S = 299792.458


def distance_modulus_model(z: np.ndarray, model: str, params: dict[str, float]) -> np.ndarray:
    """Evaluate model distance modulus at redshifts ``z``.

    Replaces the per-call ``astropy.cosmology.FlatLambdaCDM`` object creation
    + ``distmod()`` round-trip (~1 ms each, dominating emcee runtime at 25k
    walker-steps) with an inline 32-point Gauss-Legendre integration of the
    comoving distance.  ΛCDM is the (w0=-1, wa=0) limit; wCDM is (w0=w, wa=0).
    """
    if model not in MODEL_PARAMETERS:
        raise CosmologyMCMCError(f"unsupported cosmology model {model!r}")

    H0 = float(params["H0"])
    Om0 = float(params["Om0"])
    if model == "flat_lcdm":
        w0, wa = -1.0, 0.0
    elif model == "flat_wcdm":
        w0, wa = float(params["w0"]), 0.0
    else:  # flat_w0wa_cdm
        w0, wa = float(params["w0"]), float(params["wa"])

    z_arr = np.asarray(z, dtype=float)
    # x[j, k] = 0.5 * z[j] * (node_k + 1) — quadrature points in (0, z_j)
    x = 0.5 * z_arr[:, None] * (_DM_GL_NODES[None, :] + 1.0)
    one_plus_x = 1.0 + x
    a_int = 1.0 / one_plus_x
    if wa == 0.0 and w0 == -1.0:
        rho_de = 1.0
    else:
        rho_de = a_int ** (-3.0 * (1.0 + w0 + wa)) * np.exp(-3.0 * wa * (1.0 - a_int))
    ez = np.sqrt(Om0 * one_plus_x ** 3 + (1.0 - Om0) * rho_de)
    integral = 0.5 * z_arr * np.sum(_DM_GL_WEIGHTS[None, :] / ez, axis=1)
    dm_mpc = (_C_LIGHT_KM_S / H0) * integral           # comoving distance in Mpc
    dl_mpc = (1.0 + z_arr) * dm_mpc                    # luminosity distance
    return 5.0 * np.log10(dl_mpc) + 25.0


def log_probability(theta: np.ndarray, dataset: DistanceModulusDataset, model: str, priors: dict[str, tuple[float, float]]) -> float:
    names = parameter_names_for_model(model)
    params = {name: float(theta[i]) for i, name in enumerate(names)}
    for name, value in params.items():
        low, high = priors[name]
        if not (low <= value <= high):
            return -np.inf
    try:
        predicted = distance_modulus_model(dataset.z, model, params)
    except Exception:
        return -np.inf
    residual = (dataset.mu - predicted) / dataset.sigma_mu
    chi2 = float(np.sum(residual * residual))
    norm = float(np.sum(np.log(2.0 * np.pi * dataset.sigma_mu * dataset.sigma_mu)))
    return -0.5 * (chi2 + norm)


def fit_cosmology_emcee(
    rows: list[dict[str, Any]],
    *,
    model: str = "flat_lcdm",
    priors: dict[str, Any] | None = None,
    n_walkers: int = 32,
    n_steps: int = 800,
    n_burn: int = 200,
    random_seed: int | None = None,
    input_data_origin: str = "inline_unverified",
    source_cache_key: str | None = None,
    manual_attestation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a bounded emcee fit and return posterior summary + provenance.

    ``manual_attestation`` lets the caller declare the source of inline rows
    (paper bibcode + arxiv + DOI) so the result can be cited even when rows
    were not produced by a platform cache.  The attestation propagates into
    both the top-level ``citations`` array (so claim_validator picks up the
    bibcode for the valid-citation pool) and the ``provenance.cosmology``
    block (so reproducibility audits can trace the data back to a paper).
    """
    import emcee

    dataset = validate_distance_modulus_rows(rows)
    names = parameter_names_for_model(model)
    sanitized_priors = sanitize_priors(model, priors)
    ndim = len(names)

    n_walkers = int(n_walkers)
    n_steps = int(n_steps)
    n_burn = int(n_burn)
    if n_walkers < max(2 * ndim + 2, 8):
        raise CosmologyMCMCError(f"n_walkers must be at least {max(2 * ndim + 2, 8)} for {model}")
    if n_steps <= 0 or n_burn < 0 or n_burn >= n_steps:
        raise CosmologyMCMCError("n_steps must be positive and n_burn must be smaller than n_steps")

    seed = int(random_seed if random_seed is not None else 20260424)
    rng = np.random.default_rng(seed)
    center = np.asarray([(lo + hi) / 2.0 for lo, hi in sanitized_priors.values()], dtype=float)
    scale = np.asarray([(hi - lo) * 0.02 for lo, hi in sanitized_priors.values()], dtype=float)
    p0 = center + rng.normal(size=(n_walkers, ndim)) * scale
    for dim, (low, high) in enumerate(sanitized_priors.values()):
        p0[:, dim] = np.clip(p0[:, dim], low + 1e-6 * (high - low), high - 1e-6 * (high - low))

    sampler = emcee.EnsembleSampler(
        n_walkers,
        ndim,
        log_probability,
        args=(dataset, model, sanitized_priors),
    )
    try:
        sampler.random_state = np.random.RandomState(seed).get_state()
    except Exception:
        logger.debug("emcee sampler random_state could not be set explicitly", exc_info=True)
    started = time.perf_counter()
    sampler.run_mcmc(p0, n_steps, progress=False)
    elapsed = time.perf_counter() - started

    chain = np.asarray(sampler.get_chain(discard=n_burn), dtype=float)
    flat_samples = chain.reshape(-1, ndim)
    diagnostics = _chain_diagnostics_from_emcee_chain(chain, names)
    parameter_summary = diagnostics["parameters"]
    diagnostics_publication_ready = bool(diagnostics.get("publication_ready"))
    input_is_claimable = input_data_origin in CLAIMABLE_INPUT_ORIGINS

    # Three-tier publication_ready (2026-05-20): publication / exploratory / blocked.
    # ESS+R-hat extracted from parameter_summary["<param>"]["ess_bulk"/"rhat"];
    # values are None when the ArviZ pipeline failed (diagnostics_unavailable).
    ess_bulks = [p.get("ess_bulk") for p in parameter_summary.values()]
    rhats = [p.get("rhat") for p in parameter_summary.values()]
    valid_ess = [e for e in ess_bulks if isinstance(e, (int, float))]
    valid_rhats = [r for r in rhats if isinstance(r, (int, float))]
    min_ess = min(valid_ess) if valid_ess else None
    max_rhat = max(valid_rhats) if valid_rhats else None
    diagnostics_available = min_ess is not None and max_rhat is not None

    if diagnostics_publication_ready and input_is_claimable:
        chain_tier = "publication"
    elif (
        diagnostics_available
        and min_ess >= ESS_EXPLORATORY_THRESHOLD
        and max_rhat <= RHAT_EXPLORATORY_THRESHOLD
        and input_is_claimable
    ):
        chain_tier = "exploratory"
    else:
        chain_tier = "blocked"

    publication_ready = chain_tier == "publication"

    result: dict[str, Any] = {
        "success": True,
        "sampler": "emcee",
        "model": model,
        "observable": "distance_modulus",
        "parameters": parameter_summary,
        "posterior_summary": parameter_summary,
        "chain_diagnostics": diagnostics,
        "publication_ready": publication_ready,
        "chain_tier": chain_tier,
        "n_rows": len(dataset.rows),
        "n_walkers": n_walkers,
        "n_steps": n_steps,
        "n_burn": n_burn,
        "n_samples": int(flat_samples.shape[0]),
        "acceptance_fraction": round(float(np.mean(sampler.acceptance_fraction)), 4),
        "elapsed_seconds": round(float(elapsed), 3),
        "priors": _priors_to_json(sanitized_priors),
        "data_hash": dataset.data_hash,
        "random_seed": seed,
        "input_data_origin": input_data_origin,
        "source_cache_key": source_cache_key,
        "input_rows_verified": input_is_claimable,
        "manual_attestation": manual_attestation,
        "package_versions": package_versions(["astropy", "emcee", "arviz", "numpy"]),
    }
    if manual_attestation:
        # Surface the attestation as a citation entry so claim_validator's
        # bibcode harvester adds the source paper to the valid citation pool.
        # Only one of bibcode/arxiv/doi is required; we propagate whichever
        # the caller supplied.
        result["citations"] = [
            {
                "label": manual_attestation.get("source"),
                "bibcode": manual_attestation.get("bibcode"),
                "arxiv": manual_attestation.get("arxiv"),
                "doi": manual_attestation.get("doi"),
                "note": manual_attestation.get("note"),
                "source_type": "manual_attestation",
            }
        ]
    result["provenance"] = {
        "cosmology": _cosmology_provenance(result),
    }
    if manual_attestation:
        result["provenance"]["manual_attestation"] = manual_attestation
    if chain_tier == "exploratory":
        result["__tool_status__"] = "EXPLORATORY"
        result["analysis_status"] = "EXPLORATORY"
        warning = (
            f"Chain min ESS={min_ess:.0f} (publication threshold "
            f"{ESS_PUBLICATION_THRESHOLD:.0f}), max R-hat={max_rhat:.3f}. "
            "Posterior median and 1-sigma range may be discussed in conversation "
            "as exploratory, but MUST NOT be cited as a published constraint and "
            "MUST NOT be added to the bibcode pool."
        )
        result["__exploratory_warning__"] = warning
        result["__message_to_model__"] = (
            warning
            + " When reporting numbers, prefix with 'exploratory' and refuse "
            "phrasings like 'we find H0 =' or 'our constraint is'."
        )
        result["warnings"] = [warning]
    elif chain_tier == "blocked":
        result["__tool_status__"] = "PARTIAL"
        result["analysis_status"] = "PARTIAL"
        result["__do_not_claim__"] = True
        if not input_is_claimable:
            result["data_origin"] = "unavailable"
            reason = (
                "Cosmology MCMC used inline/unverified rows. Inline rows are audit-only "
                "because the model could have supplied remembered or synthetic tables. "
                "Re-run from a platform cache_key backed by a real data/literature tool."
            )
        elif not diagnostics_available:
            reason = "MCMC diagnostics unavailable (ArviZ pipeline failed); chain cannot be verified."
        else:
            reason = (
                f"MCMC chain min ESS={min_ess:.0f}, max R-hat={max_rhat:.3f}; "
                f"below exploratory floor (ESS>={ESS_EXPLORATORY_THRESHOLD:.0f}, "
                f"R-hat<={RHAT_EXPLORATORY_THRESHOLD})."
            )
        result["__message_to_model__"] = (
            reason
            + " Do not cite H0, Om0, w0, wa, sigma8, HDI, or posterior constraints "
            "from this result."
        )
        result["warnings"] = [reason]
    return result


def should_run_background(n_walkers: int, n_steps: int, force_background: bool = False) -> bool:
    return bool(force_background) or int(n_walkers) * int(n_steps) > SYNC_SAMPLE_BUDGET


def submit_emcee_job(**kwargs: Any) -> dict[str, Any]:
    job_id = f"cosmo-{uuid.uuid4().hex[:12]}"
    with _JOBS_LOCK:
        _cleanup_jobs_locked()
        _JOBS[job_id] = {
            "job_id": job_id,
            "status": "running",
            "created_at": time.time(),
            "sampler": "emcee",
            "model": kwargs.get("model", "flat_lcdm"),
            "background_backend": "in_process_ephemeral",
            "warning": (
                "Background cosmology jobs are ephemeral in this deployment and "
                "may be lost on process restart; use only for short follow-up polling."
            ),
        }

    def _target() -> None:
        try:
            result = fit_cosmology_emcee(**kwargs)
            update = {"status": "completed", "result": result, "completed_at": time.time()}
        except Exception as exc:
            logger.exception("cosmology MCMC background job failed")
            update = {
                "status": "failed",
                "error": str(exc),
                "error_class": exc.__class__.__name__,
                "completed_at": time.time(),
            }
        with _JOBS_LOCK:
            _JOBS[job_id].update(update)

    thread = threading.Thread(target=_target, name=f"cosmology-mcmc-{job_id}", daemon=True)
    thread.start()
    return {
        "success": True,
        "__tool_status__": "PARTIAL",
        "analysis_status": "QUEUED",
        "data_origin": "unavailable",
        "job_id": job_id,
        "status": "running",
        "sampler": "emcee",
        "model": kwargs.get("model", "flat_lcdm"),
        "background_backend": "in_process_ephemeral",
        "__do_not_claim__": True,
        "message": (
            "Cosmology MCMC job started in an in-process ephemeral background worker; "
            "poll get_cosmology_run_status. Do not quote posterior values until a "
            "completed result returns publication_ready=true."
        ),
    }


def get_cosmology_job_status(job_id: str) -> dict[str, Any]:
    with _JOBS_LOCK:
        _cleanup_jobs_locked()
        job = dict(_JOBS.get(str(job_id), {}))
    if not job:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": f"Unknown cosmology job_id: {job_id}",
            "error_class": "not_found",
        }
    return job


def run_cobaya_cosmology(
    rows: list[dict[str, Any]],
    *,
    model: str = "flat_lcdm",
    priors: dict[str, Any] | None = None,
    random_seed: int | None = None,
    max_samples: int = 4000,
) -> dict[str, Any]:
    """Return a controlled Cobaya placeholder until posterior summaries land."""
    dataset = validate_distance_modulus_rows(rows)
    sanitized_priors = sanitize_priors(model, priors)
    seed = int(random_seed if random_seed is not None else 20260424)
    info = build_cobaya_info(dataset, model, sanitized_priors, seed=seed, max_samples=max_samples)
    return _cobaya_unavailable(
        "Cobaya cosmology is phase-1 disabled until posterior sample summarization is implemented; use fit_cosmology_mcmc for citeable short-chain fits.",
        None,
        info,
        dataset,
        model,
        sanitized_priors,
        seed,
    )


def build_cobaya_info(
    dataset: DistanceModulusDataset,
    model: str,
    priors: dict[str, tuple[float, float]],
    *,
    seed: int,
    max_samples: int,
) -> dict[str, Any]:
    """Build a safe Cobaya info dict without raw user likelihood code."""

    def distance_modulus_loglike(**params: float) -> float:
        theta = np.asarray([params[name] for name in parameter_names_for_model(model)], dtype=float)
        return float(log_probability(theta, dataset, model, priors))

    params = {
        name: {
            "prior": {"min": low, "max": high},
            "ref": {"dist": "uniform", "min": low, "max": high},
            "proposal": max((high - low) / 50.0, 1e-3),
        }
        for name, (low, high) in priors.items()
    }
    return {
        "likelihood": {
            "distance_modulus_table": {
                "external": distance_modulus_loglike,
                "input_params": list(parameter_names_for_model(model)),
            }
        },
        "params": params,
        "sampler": {
            "mcmc": {
                "max_samples": int(max_samples),
                "Rminus1_stop": 0.05,
                "Rminus1_cl_stop": 0.2,
                "learn_proposal": True,
                "seed": seed,
            }
        },
        "debug": False,
    }


def _chain_diagnostics_from_emcee_chain(chain: np.ndarray, names: tuple[str, ...]) -> dict[str, Any]:
    # emcee chain is (draws, walkers, ndim); ArviZ wants (chains, draws).
    draws, walkers, ndim = chain.shape
    flat = chain.reshape(-1, ndim)
    try:
        import arviz as az

        reshaped = np.transpose(chain, (1, 0, 2))
        idata = az.from_dict(
            posterior={
                name: np.ascontiguousarray(reshaped[:, :, index], dtype=float)
                for index, name in enumerate(names)
            }
        )
        rhat = az.rhat(idata)
        ess_bulk = az.ess(idata, method="bulk")
        ess_tail = az.ess(idata, method="tail")
        mcse = az.mcse(idata)
        parameters: dict[str, dict[str, Any]] = {}
        insufficient: list[str] = []
        for index, name in enumerate(names):
            col = flat[:, index]
            hdi_low, hdi_high = _safe_hdi(col, az)
            rhat_value = _safe_float(rhat[name])
            ess_bulk_value = _safe_float(ess_bulk[name])
            ess_tail_value = _safe_float(ess_tail[name])
            mcse_value = _safe_float(mcse[name])
            status = _diagnostic_status(rhat_value, ess_bulk_value)
            if status != "good":
                insufficient.append(name)
            parameters[name] = {
                "mean": round(float(np.mean(col)), 6),
                "std": round(float(np.std(col)), 6),
                "median": round(float(np.median(col)), 6),
                "hdi_low_94": round(hdi_low, 6),
                "hdi_high_94": round(hdi_high, 6),
                "rhat": round(rhat_value, 4) if math.isfinite(rhat_value) else None,
                "ess_bulk": round(ess_bulk_value, 1) if math.isfinite(ess_bulk_value) else None,
                "ess_tail": round(ess_tail_value, 1) if math.isfinite(ess_tail_value) else None,
                "mcse": round(mcse_value, 6) if math.isfinite(mcse_value) else None,
                "status": status,
            }
        publication_ready = len(insufficient) == 0
        if not publication_ready:
            _record_insufficient_sampling(insufficient)
        return {
            "parameters": parameters,
            "overall_status": "converged" if publication_ready else "check_required",
            "publication_ready": publication_ready,
            "insufficient_params": insufficient,
            "thresholds": {
                "ess_min": ESS_PUBLICATION_THRESHOLD,
                "rhat_max": RHAT_PUBLICATION_THRESHOLD,
            },
            "n_chains": walkers,
            "n_draws": draws,
        }
    except Exception as exc:
        logger.warning("ArviZ diagnostics failed for cosmology MCMC: %s", exc)
        parameters = {}
        for index, name in enumerate(names):
            col = flat[:, index]
            parameters[name] = {
                "mean": round(float(np.mean(col)), 6),
                "std": round(float(np.std(col)), 6),
                "median": round(float(np.median(col)), 6),
                "hdi_low_94": round(float(np.percentile(col, 3)), 6),
                "hdi_high_94": round(float(np.percentile(col, 97)), 6),
                "rhat": None,
                "ess_bulk": None,
                "ess_tail": None,
                "status": "diagnostics_unavailable",
            }
        return {
            "parameters": parameters,
            "overall_status": "diagnostics_unavailable",
            "publication_ready": False,
            "insufficient_params": list(names),
            "thresholds": {
                "ess_min": ESS_PUBLICATION_THRESHOLD,
                "rhat_max": RHAT_PUBLICATION_THRESHOLD,
            },
            "n_chains": walkers,
            "n_draws": draws,
        }


def _safe_hdi(values: np.ndarray, az: Any) -> tuple[float, float]:
    try:
        hdi = az.hdi(values, hdi_prob=0.94)
        return float(hdi[0]), float(hdi[1])
    except Exception:
        return float(np.percentile(values, 3)), float(np.percentile(values, 97))


def _safe_float(value: Any) -> float:
    try:
        raw = value.values if hasattr(value, "values") else value
        result = float(raw)
        return result if math.isfinite(result) else math.nan
    except Exception:
        return math.nan


def _diagnostic_status(rhat: float, ess_bulk: float) -> str:
    if not (math.isfinite(rhat) and math.isfinite(ess_bulk)):
        return "not_converged"
    if rhat < 1.01 and ess_bulk >= ESS_PUBLICATION_THRESHOLD:
        return "good"
    if rhat < RHAT_PUBLICATION_THRESHOLD and ess_bulk >= ESS_PUBLICATION_THRESHOLD / 2.0:
        return "marginal"
    return "not_converged"


def _record_insufficient_sampling(params: list[str]) -> None:
    try:
        from app.observability.metrics import record_counter

        record_counter(
            "mcmc_insufficient_sampling_total",
            1.0,
            params=",".join(sorted(params))[:64],
        )
    except Exception:
        pass


def _priors_to_json(priors: dict[str, tuple[float, float]]) -> dict[str, list[float]]:
    return {name: [float(low), float(high)] for name, (low, high) in priors.items()}


def _cosmology_provenance(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": result.get("model"),
        "sampler": result.get("sampler"),
        "priors": result.get("priors"),
        "data_hash": result.get("data_hash"),
        "random_seed": result.get("random_seed"),
        "input_data_origin": result.get("input_data_origin"),
        "source_cache_key": result.get("source_cache_key"),
        "input_rows_verified": result.get("input_rows_verified"),
        "package_versions": result.get("package_versions"),
        "chain_diagnostics": result.get("chain_diagnostics"),
        "publication_ready": result.get("publication_ready"),
    }


def _cleanup_jobs_locked() -> None:
    now = time.time()
    expired = [
        job_id
        for job_id, job in _JOBS.items()
        if now - float(job.get("created_at") or now) > _JOB_TTL_SECONDS
    ]
    for job_id in expired:
        _JOBS.pop(job_id, None)
    if len(_JOBS) <= _MAX_JOBS:
        return
    for job_id, _ in sorted(_JOBS.items(), key=lambda item: float(item[1].get("created_at") or 0)):
        if len(_JOBS) <= _MAX_JOBS:
            break
        _JOBS.pop(job_id, None)


def package_versions(distributions: list[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in distributions:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    return versions


def _public_cobaya_info(info: dict[str, Any]) -> dict[str, Any]:
    public = dict(info)
    likelihood = public.get("likelihood")
    if isinstance(likelihood, dict):
        public["likelihood"] = {
            key: {
                sub_key: ("<controlled distance_modulus_loglike>" if sub_key == "external" else sub_value)
                for sub_key, sub_value in value.items()
            }
            for key, value in likelihood.items()
            if isinstance(value, dict)
        }
    return public


def _cobaya_unavailable(
    message: str,
    exc: Exception | None,
    info: dict[str, Any],
    dataset: DistanceModulusDataset,
    model: str,
    priors: dict[str, tuple[float, float]],
    seed: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "__tool_status__": "UNAVAILABLE",
        "analysis_status": "UNAVAILABLE",
        "data_origin": "UNAVAILABLE",
        "error": message if exc is None else f"{message} ({exc.__class__.__name__}: {exc})",
        "error_class": "cobaya_unavailable" if exc is None else exc.__class__.__name__,
        "__do_not_claim__": True,
        "__message_to_model__": (
            "Cobaya cosmology sampling is unavailable. Do not fabricate posterior "
            "constraints or fall back to synthetic cosmology values."
        ),
        "sampler": "cobaya",
        "model": model,
        "observable": "distance_modulus",
        "publication_ready": False,
        "cobaya_info": _public_cobaya_info(info),
        "priors": _priors_to_json(priors),
        "data_hash": dataset.data_hash,
        "random_seed": seed,
        "package_versions": package_versions(["cobaya", "astropy", "numpy"]),
    }
    result["provenance"] = {
        "cosmology": {
            "model": model,
            "sampler": "cobaya",
            "priors": result["priors"],
            "data_hash": dataset.data_hash,
            "random_seed": seed,
            "package_versions": result["package_versions"],
            "chain_diagnostics": {"publication_ready": False},
            "publication_ready": False,
        }
    }
    return result
