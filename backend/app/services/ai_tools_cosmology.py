"""Cosmology AI tools — extracted from ai_tools/__init__.py (H1 split, 2026-05-26).

The 13 likelihood / MCMC / robustness tools whose implementations are thin
wrappers over the cosmology_mcmc / cosmology_likelihoods /
cosmology_data_products / cmb_rotation_likelihoods / nested_sampling /
chain_diagnostics service modules.

Dispatch mirrors ai_tools_solar_system / ai_tools_exoplanet: ai_tools/__init__
routes ``tool_name in COSMOLOGY_TOOL_NAMES`` to ``dispatch_cosmology``. The
shared cache helpers (``get_cached_results``, ``_session_cache_key``,
``_validate_manual_attestation``) live in ai_tools/__init__ and are lazily
imported here to avoid an import cycle, consistent with the sibling modules.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

COSMOLOGY_TOOL_NAMES = frozenset(
    {
        "fit_cosmology_mcmc",
        "run_cobaya_cosmology",
        "get_cosmology_run_status",
        "list_cosmology_datasets",
        "load_cosmology_data_product",
        "build_cosmology_likelihood",
        "run_cosmology_likelihood_chain",
        "run_cmb_rotation_likelihood",
        "run_nested_sampler",
        "evaluate_chain_diagnostics",
        "build_cosmology_robustness_matrix",
        "run_cosmology_robustness_matrix",
        "assess_bao_bin_anomaly",
        "audit_published_constraint",
    }
)


COSMOLOGY_TOOL_SCHEMAS = [
    {
        "name": "fit_cosmology_mcmc",
        "description": (
            "Fit H0/Om0/w0/wa to a real distance-modulus table using a bounded emcee MCMC. "
            "Input rows must contain z, mu, and sigma_mu; no remembered or synthetic cosmology "
            "samples are allowed. Use this after obtaining a real table from the user, an archive, "
            "or a cited literature table. Direct inline rows are audit-only and not citeable; "
            "use cache_key from a real prior tool for publication-ready posterior claims. "
            "Results are citeable only when publication_ready=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Inline distance-modulus rows with z, mu, sigma_mu. Audit-only unless paired with manual_attestation or backed by cache_key.",
                },
                "cache_key": {
                    "type": "string",
                    "description": "Optional cache key such as latest_adql or latest_search containing rows.",
                },
                "manual_attestation": {
                    "type": "object",
                    "description": (
                        "Declare the published source of inline rows so the fit becomes citeable. "
                        "Require fields: source (free-text description, e.g. 'Riess+2022 Table 2') "
                        "and at least one of bibcode, arxiv, doi. Optional: note. "
                        "When provided, inline rows are upgraded from audit-only to citeable and the "
                        "attestation is recorded in result.citations + provenance.manual_attestation."
                    ),
                    "properties": {
                        "source": {"type": "string"},
                        "bibcode": {"type": "string"},
                        "arxiv": {"type": "string"},
                        "doi": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["source"],
                },
                "model": {
                    "type": "string",
                    "enum": ["flat_lcdm", "flat_wcdm", "flat_w0wa_cdm"],
                    "description": "Cosmology model to fit.",
                },
                "priors": {
                    "type": "object",
                    "description": "Optional tightened prior bounds, e.g. {\"H0\": [60, 80]}.",
                },
                "n_walkers": {"type": "integer", "description": "emcee walkers (default 32)."},
                "n_steps": {"type": "integer", "description": "emcee steps (default 800)."},
                "n_burn": {"type": "integer", "description": "burn-in steps (default 200)."},
                "random_seed": {"type": "integer", "description": "deterministic random seed."},
                "background": {
                    "type": "boolean",
                    "description": "Run as a background job; long chains are background automatically.",
                },
            },
        },
    },
    {
        "name": "run_cobaya_cosmology",
        "description": (
            "Run a controlled Cobaya distance-modulus cosmology fit. Accepts typed rows and bounded "
            "priors only; raw Cobaya YAML and arbitrary likelihood code are not accepted. Phase 1 "
            "keeps Cobaya UNAVAILABLE until posterior summarization lands; use fit_cosmology_mcmc "
            "for citeable short-chain emcee fits."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Inline distance-modulus rows with z, mu, sigma_mu.",
                },
                "cache_key": {"type": "string", "description": "Optional cache key containing rows."},
                "model": {
                    "type": "string",
                    "enum": ["flat_lcdm", "flat_wcdm", "flat_w0wa_cdm"],
                    "description": "Cosmology model to fit.",
                },
                "priors": {"type": "object", "description": "Optional tightened prior bounds."},
                "random_seed": {"type": "integer", "description": "deterministic random seed."},
                "max_samples": {"type": "integer", "description": "Cobaya sample budget."},
            },
        },
    },
    {
        "name": "get_cosmology_run_status",
        "description": "Poll the status/result of a background cosmology MCMC or Cobaya job.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID returned by fit_cosmology_mcmc."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "list_cosmology_datasets",
        "description": (
            "List the curated observational-cosmology dataset registry. Entries "
            "include version, citation, covariance, units, applicable models, "
            "source URL, and whether Standard Astro can run the likelihood "
            "directly or only generate an external config. Use this before "
            "planning DESI/SDSS+6dF BAO/Pantheon+/DES-SN/Union3/Planck/ACT/"
            "KiDS/DES/HSC weak-lensing/CC/SH0ES analyses."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "probe": {
                    "type": "string",
                    "description": "Optional probe filter: bao, sn, cmb_compressed, cmb_lensing, hz, h0_prior.",
                },
                "status": {
                    "type": "string",
                    "enum": ["ready", "external_likelihood", "metadata_only"],
                    "description": "Optional status filter.",
                },
                "dataset_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit dataset keys to list; preserves the requested order.",
                },
            },
        },
    },
    {
        "name": "load_cosmology_data_product",
        "description": (
            "Load and validate a machine-readable cosmology data product registered "
            "in the dataset registry, such as a BAO mean vector or covariance matrix. "
            "Reports shape, preview rows, sha256 verification, and covariance sanity. "
            "Does not produce posterior constraints by itself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_key": {"type": "string", "description": "Dataset key from list_cosmology_datasets."},
                "role": {"type": "string", "description": "Optional product role, e.g. measurement_vector or covariance."},
                "product_type": {"type": "string", "description": "Optional product type selector."},
                "allow_network": {"type": "boolean", "description": "Fetch the registry URL when true. Default true."},
                "max_preview_rows": {"type": "integer"},
            },
            "required": ["dataset_key"],
        },
    },
    {
        "name": "build_cosmology_likelihood",
        "description": (
            "Build a controlled Cobaya/CosmoSIS-style likelihood config from "
            "registered cosmology datasets and bounded priors. This prepares "
            "DESI or SDSS+6dF BAO, Pantheon+, DES-SN, Union3, Planck compressed, "
            "ACT DR6, KiDS/DES/HSC weak-lensing, cosmic chronometer, and "
            "SH0ES-prior combinations. It returns "
            "CONFIG_READY only; do not quote posterior constraints until a "
            "chain runner returns publication_ready=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "enum": [
                        "lcdm", "wcdm", "w0wa_cdm", "ok_lcdm",
                        "ok_wcdm", "ok_w0wa_cdm", "lcdm_mnu",
                        "w0wa_cdm_mnu",
                    ],
                    "description": "Cosmological model family.",
                },
                "dataset_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Dataset keys from list_cosmology_datasets.",
                },
                "priors": {
                    "type": "object",
                    "description": "Optional tightened prior bounds, e.g. {\"w0\": [-1.5, -0.5]}.",
                },
                "sampler": {
                    "type": "string",
                    "description": "Sampler label for generated config. Default: mcmc.",
                },
                "output_format": {
                    "type": "string",
                    "enum": ["cobaya", "cosmosis", "both"],
                    "description": "Which config style to return. Default: both.",
                },
            },
            "required": ["model", "dataset_keys"],
        },
    },
    {
        "name": "run_cosmology_likelihood_chain",
        "description": (
            "Run the phase-1 compressed Gaussian observational-cosmology likelihood "
            "for registered datasets that include published mean/covariance summaries. "
            "This can produce citeable preliminary H0/Omega_m/sigma8/S8/tension results "
            "only when publication_ready=true. It does not run full external ACT/Planck/"
            "BAO/SN weak-lensing likelihood packages; datasets without compressed "
            "summaries are reported as datasets_not_run."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "enum": [
                        "lcdm", "wcdm", "w0wa_cdm", "ok_lcdm",
                        "ok_wcdm", "ok_w0wa_cdm", "lcdm_mnu",
                        "w0wa_cdm_mnu",
                    ],
                    "description": "Cosmological model family. Phase-1 compressed execution is publication-ready for lcdm only.",
                },
                "dataset_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Dataset keys from list_cosmology_datasets.",
                },
                "priors": {
                    "type": "object",
                    "description": "Optional tightened prior bounds for compressed parameters.",
                },
                "random_seed": {"type": "integer", "description": "Deterministic sample seed."},
                "n_samples": {"type": "integer", "description": "Posterior sample count for display summaries."},
            },
            "required": ["model", "dataset_keys"],
        },
    },
    {
        "name": "run_cmb_rotation_likelihood",
        "description": (
            "Run a controlled CMB polarization-rotation likelihood for registered "
            "EB/TB data products. Version 1 supports isotropic beta only. Results "
            "are citeable only when publication_ready=true and must be described "
            "as compressed-rotation preliminary, not a full Planck/ACT/BICEP likelihood."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Registered CMB rotation dataset keys.",
                },
                "model": {
                    "type": "string",
                    "enum": ["isotropic_beta"],
                    "description": "CMB rotation model. Version 1 supports isotropic_beta only.",
                },
                "priors": {
                    "type": "object",
                    "description": "Optional bounded prior, e.g. {\"beta_deg\": [-5, 5]}.",
                },
                "random_seed": {"type": "integer", "description": "Deterministic sample seed."},
                "n_samples": {"type": "integer", "description": "Posterior sample count for display summaries."},
            },
            "required": ["dataset_keys", "model"],
        },
    },
    {
        "name": "run_nested_sampler",
        "description": (
            "Run a controlled dynesty nested sampler on typed Gaussian likelihood "
            "summaries. Use this for evidence/posterior diagnostics when a paper "
            "requires nested sampling but the likelihood can be represented as "
            "bounded parameters plus mean/covariance summaries. It never executes "
            "user Python or raw Cobaya YAML. Results are citeable only when "
            "publication_ready=true and must be described as controlled Gaussian "
            "nested-sampling, not a full external likelihood."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "parameters": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Parameter specs, e.g. "
                        "[{\"name\":\"S8\",\"prior\":[0.6,1.0]}]."
                    ),
                },
                "gaussian_likelihood": {
                    "type": "object",
                    "description": (
                        "Single Gaussian likelihood with parameters, mean, "
                        "covariance, and optional citation/source_url."
                    ),
                },
                "likelihoods": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Optional list of Gaussian likelihood blocks to multiply.",
                },
                "sampler_config": {
                    "type": "object",
                    "description": "Optional bounded dynesty settings: nlive, dlogz, maxiter, sample.",
                },
                "random_seed": {"type": "integer", "description": "Deterministic random seed."},
            },
            "required": ["parameters"],
        },
    },
    {
        "name": "evaluate_chain_diagnostics",
        "description": (
            "Compute convergence diagnostics for explicit posterior chains: R-hat, "
            "ESS, MCSE, HDI, draws per chain, and publication-readiness status. "
            "This diagnoses supplied chains only; it does not run a likelihood and "
            "cannot by itself support cosmological parameter claims."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chains": {
                    "type": ["object", "array"],
                    "description": (
                        "Either {parameter: [[chain1 draws], [chain2 draws], ...]} "
                        "or a list of numeric row objects."
                    ),
                },
                "parameters": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional parameter subset/order.",
                },
            },
            "required": ["chains"],
        },
    },
    {
        "name": "build_cosmology_robustness_matrix",
        "description": (
            "Generate the standard robustness matrix for observational "
            "cosmology: BAO only, BAO+SN, BAO+CMB, BAO+SN+CMB, and optional "
            "SH0ES H0-prior variants. The output is a set of chain configs "
            "with guardrails; it is not a posterior result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "enum": [
                        "lcdm", "wcdm", "w0wa_cdm", "ok_lcdm",
                        "ok_wcdm", "ok_w0wa_cdm", "lcdm_mnu",
                        "w0wa_cdm_mnu",
                    ],
                    "description": "Cosmological model family.",
                },
                "supernova_sets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "SN dataset alternatives. Default: Pantheon+; pass DES-SN5YR/Union3 explicitly for config-only comparison branches.",
                },
                "include_h0_prior": {
                    "type": "boolean",
                    "description": "Whether to include +SH0ES variants. Default: true.",
                },
                "include_weak_lensing": {
                    "type": "boolean",
                    "description": "Whether to add weak-lensing robustness cells. Default: false unless the user asks for WL/cosmic shear.",
                },
                "sampler": {
                    "type": "string",
                    "description": "Sampler label for generated configs. Default: mcmc.",
                },
            },
            "required": ["model"],
        },
    },
    {
        "name": "run_cosmology_robustness_matrix",
        "description": (
            "Execute the standard observational-cosmology robustness matrix using "
            "registered compressed Gaussian likelihoods where available. Config-only "
            "cells remain non-runnable and must be described as needing external chains."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "enum": [
                        "lcdm", "wcdm", "w0wa_cdm", "ok_lcdm",
                        "ok_wcdm", "ok_w0wa_cdm", "lcdm_mnu",
                        "w0wa_cdm_mnu",
                    ],
                    "description": "Cosmological model family.",
                },
                "supernova_sets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "SN dataset alternatives. Default: Pantheon+; pass DES-SN5YR/Union3 explicitly for config-only comparison branches.",
                },
                "include_h0_prior": {
                    "type": "boolean",
                    "description": "Whether to include +SH0ES variants. Default: true.",
                },
                "include_weak_lensing": {
                    "type": "boolean",
                    "description": "Whether to add weak-lensing robustness cells. Default: false unless the user asks for WL/cosmic shear.",
                },
                "random_seed": {"type": "integer", "description": "Deterministic sample seed."},
                "n_samples": {"type": "integer", "description": "Posterior sample count per runnable cell."},
            },
            "required": ["model"],
        },
    },
    {
        "name": "assess_bao_bin_anomaly",
        "description": (
            "Run a DESI DR1 BAO redshift-bin geometry diagnostic using the "
            "Alcock-Paczynski DM/DH ratios. This checks per-bin geometric "
            "consistency under flat LCDM without using H0 or r_d, and returns "
            "publication-ready preliminary Omega_m/AP residual diagnostics only "
            "when the registered DESI DR1 BAO mean vector and covariance are used."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "omega_m_grid": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Optional [low, high, n] Omega_m grid. Default [0.10, 0.50, 401].",
                },
            },
        },
    },
    {
        "name": "audit_published_constraint",
        "description": (
            "Audit a PUBLISHED cosmology constraint: reproduce it with the platform's real "
            "compressed-likelihood runner over registered datasets, then report the per-parameter "
            "n-sigma tension between the published value and the reproduced value. Use when the "
            "user gives a paper's quoted parameter value(s) (e.g. SH0ES H0=73.04+/-1.04) and wants "
            "to check them against in-platform data. Output is a TENSION / agreement report, NOT a "
            "fabrication verdict — a real tension means the measurements disagree, not that the "
            "paper is wrong. Parameters the platform cannot reproduce are marked NOT_REPRODUCED "
            "(a coverage gap), and unrunnable datasets are reported as BLOCKED rather than guessed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Cosmology model to reproduce under, e.g. lcdm / wcdm / w0wa_cdm.",
                },
                "dataset_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Registry dataset keys to reproduce with, e.g. [\"desi_dr1_bao\", \"pantheon_plus\"]. Non-runnable keys are reported as unavailable.",
                },
                "claimed": {
                    "type": "object",
                    "description": "Published values to audit, mapping parameter name to [value, sigma] (sigma optional), e.g. {\"H0\": [73.04, 1.04], \"omegam\": [0.298, 0.008]}.",
                },
                "paper_ref": {
                    "type": "object",
                    "description": "Source of the published value: {source, and at least one of bibcode/arxiv/doi}. Recorded for provenance and added to citations.",
                    "properties": {
                        "source": {"type": "string"},
                        "bibcode": {"type": "string"},
                        "arxiv": {"type": "string"},
                        "doi": {"type": "string"},
                    },
                },
                "claimed_datasets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: datasets the published value used, to detect overlap with the reproduction (overlap makes the n-sigma not a clean independent comparison).",
                },
                "random_seed": {"type": "integer", "description": "Deterministic seed for the reproduction."},
            },
            "required": ["model", "dataset_keys", "claimed"],
        },
    },
]


def _record_attestation_upgrade(verified: bool) -> None:
    """Audit counter for the manual_attestation citeable-upgrade path."""
    try:
        from app.observability.metrics import record_counter

        record_counter(
            "cosmology_attestation_upgrade_total",
            1.0,
            outcome="verified" if verified else "unverified",
        )
    except Exception:
        pass


async def _cosmology_rows_from_input(
    inp: dict, python_session_id: str | None
) -> tuple[list[dict[str, Any]], str, str | None, dict[str, Any] | None]:
    from app.services.ai_tools import (
        get_cached_results,
        _session_cache_key,
        _validate_manual_attestation,
    )

    rows = inp.get("rows")
    attestation_raw = inp.get("manual_attestation")
    if isinstance(rows, list) and rows:
        normalized = [dict(row) for row in rows if isinstance(row, dict)]
        if attestation_raw is not None:
            attestation = _validate_manual_attestation(attestation_raw)
            # Inline rows + attestation upgrade to a citeable origin ONLY when
            # ADS confirms the cited reference is real. Without this check a
            # fabricated-but-plausible bibcode could launder model-invented rows
            # into a citeable fit (and inject the bibcode into the citation pool).
            from app.services.literature_engine import resolve_bibcode_exists

            verified = await resolve_bibcode_exists(
                bibcode=attestation.get("bibcode"),
                arxiv=attestation.get("arxiv"),
                doi=attestation.get("doi"),
            )
            _record_attestation_upgrade(verified)
            if verified:
                # source_cache_key encodes the attestation reference so audit
                # logs tell "real cache hit" apart from "user-attested upload".
                source_id = attestation["bibcode"] or attestation["arxiv"] or attestation["doi"]
                return normalized, "cached_real", f"manual_attestation:{source_id}", attestation
            # ADS could not confirm the reference: keep audit-only and DROP the
            # attestation so the unverified bibcode never reaches the citation pool.
            return normalized, "inline_unverified", None, None
        return normalized, "inline_unverified", None, None

    cache_key = str(inp.get("cache_key") or "").strip()
    if not cache_key:
        raise ValueError("fit_cosmology_mcmc requires rows or cache_key")
    session_key = _session_cache_key(cache_key, python_session_id)
    payload = get_cached_results(session_key or cache_key)
    if payload is None and session_key:
        payload = get_cached_results(cache_key)
    if payload is None:
        raise ValueError(f"No cached rows found for cache_key={cache_key!r}")
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)], "cached_real", cache_key, None
    if isinstance(payload, dict):
        for key in ("rows", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(row) for row in value if isinstance(row, dict)], "cached_real", cache_key, None
            if isinstance(value, dict):
                columns = list(value.keys())
                lengths = [len(v) for v in value.values() if isinstance(v, list)]
                if lengths:
                    n = min(lengths)
                    return [
                        {column: value.get(column, [None] * n)[index] for column in columns}
                        for index in range(n)
                    ], "cached_real", cache_key, None
    raise ValueError(f"Cached payload {cache_key!r} does not contain row objects")


# ── Tool executors (moved verbatim from ai_tools/__init__.py, H1 split) ──


async def _exec_fit_cosmology_mcmc(inp: dict, python_session_id: str | None) -> dict:
    from app.services.async_tool_runtime import in_async_worker
    from app.services.cosmology_mcmc import (
        fit_cosmology_emcee,
        should_run_background,
        submit_emcee_job,
    )

    try:
        rows, input_data_origin, source_cache_key, attestation = await _cosmology_rows_from_input(
            inp, python_session_id
        )
        model = str(inp.get("model") or "flat_lcdm")
        n_walkers = int(inp.get("n_walkers") or 32)
        n_steps = int(inp.get("n_steps") or 800)
        n_burn = int(inp.get("n_burn") or 200)
        random_seed = inp.get("random_seed")
        random_seed_int = int(random_seed) if random_seed is not None else None
        kwargs = {
            "rows": rows,
            "model": model,
            "priors": inp.get("priors"),
            "n_walkers": n_walkers,
            "n_steps": n_steps,
            "n_burn": n_burn,
            "random_seed": random_seed_int,
            "input_data_origin": input_data_origin,
            "source_cache_key": source_cache_key,
            "manual_attestation": attestation,
        }
        # When the agent loop is running inside the Celery worker (because
        # a previous submit_async_job re-entered this code path), force the
        # synchronous emcee chain. Otherwise we'd submit a new Celery task
        # from inside a Celery task and deadlock on the worker queue.
        attestation_rejected = (
            inp.get("manual_attestation") is not None
            and input_data_origin == "inline_unverified"
        )
        if in_async_worker():
            result = await asyncio.to_thread(fit_cosmology_emcee, **kwargs)
        elif should_run_background(n_walkers, n_steps, bool(inp.get("background", False))):
            result = submit_emcee_job(**kwargs)
        else:
            result = await asyncio.to_thread(fit_cosmology_emcee, **kwargs)
        if attestation_rejected and isinstance(result, dict):
            note = (
                "manual_attestation reference could not be confirmed in ADS (or "
                "ADS is unavailable); rows kept audit-only and the cited bibcode "
                "was NOT added to the citation pool."
            )
            warns = result.get("warnings")
            if isinstance(warns, list):
                warns.append(note)
            else:
                result["warnings"] = [note]
        return result
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
            "__message_to_model__": (
                "Cosmology MCMC did not produce a valid posterior. Do not quote "
                "H0, Om0, w0, wa, sigma8, or posterior constraints from this call."
            ),
        }


async def _exec_run_cobaya_cosmology(inp: dict, python_session_id: str | None) -> dict:
    from app.services.cosmology_mcmc import run_cobaya_cosmology

    try:
        rows, _input_data_origin, _source_cache_key, _attestation = await _cosmology_rows_from_input(
            inp, python_session_id
        )
        return await asyncio.to_thread(
            run_cobaya_cosmology,
            rows,
            model=str(inp.get("model") or "flat_lcdm"),
            priors=inp.get("priors"),
            random_seed=int(inp["random_seed"]) if inp.get("random_seed") is not None else None,
            max_samples=int(inp.get("max_samples") or 4000),
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
            "__message_to_model__": (
                "Cobaya cosmology did not produce a valid posterior. Do not quote "
                "cosmology constraints from this call."
            ),
        }


def _exec_get_cosmology_run_status(inp: dict) -> dict:
    from app.services.cosmology_mcmc import get_cosmology_job_status

    job_id = str(inp.get("job_id") or "").strip()
    if not job_id:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": "job_id is required",
            "error_class": "missing_job_id",
        }
    return get_cosmology_job_status(job_id)


def _exec_list_cosmology_datasets(inp: dict) -> dict:
    from app.services.cosmology_likelihoods import list_cosmology_datasets

    return list_cosmology_datasets(
        probe=str(inp.get("probe") or "").strip() or None,
        status=str(inp.get("status") or "").strip() or None,
        dataset_keys=inp.get("dataset_keys") if isinstance(inp.get("dataset_keys"), list) else None,
    )


async def _exec_load_cosmology_data_product(inp: dict) -> dict:
    from app.services.cosmology_data_products import load_cosmology_data_product

    try:
        return await load_cosmology_data_product(
            dataset_key=str(inp.get("dataset_key") or ""),
            role=str(inp.get("role") or "").strip() or None,
            product_type=str(inp.get("product_type") or "").strip() or None,
            allow_network=bool(inp.get("allow_network", True)),
            max_preview_rows=int(inp.get("max_preview_rows") or 8),
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
        }


def _exec_build_cosmology_likelihood(inp: dict) -> dict:
    from app.services.cosmology_likelihoods import build_likelihood_config

    try:
        dataset_keys = inp.get("dataset_keys") or []
        if not isinstance(dataset_keys, list):
            raise ValueError("dataset_keys must be a list")
        return build_likelihood_config(
            model=str(inp.get("model") or ""),
            dataset_keys=[str(key) for key in dataset_keys],
            priors=inp.get("priors"),
            sampler=str(inp.get("sampler") or "mcmc"),
            output_format=str(inp.get("output_format") or "both"),
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
        }


def _exec_run_cosmology_likelihood_chain(inp: dict) -> dict:
    from app.services.cosmology_likelihoods import run_likelihood_chain

    try:
        dataset_keys = inp.get("dataset_keys") or []
        if not isinstance(dataset_keys, list):
            raise ValueError("dataset_keys must be a list")
        return run_likelihood_chain(
            model=str(inp.get("model") or ""),
            dataset_keys=[str(key) for key in dataset_keys],
            priors=inp.get("priors"),
            random_seed=int(inp["random_seed"]) if inp.get("random_seed") is not None else None,
            n_samples=int(inp.get("n_samples") or 4000),
            # Single-cell deep run: upgrade to compressed-emcee when importance
            # ESS collapses on multi-probe products (matrices keep the fast path).
            allow_emcee_fallback=True,
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
            "__message_to_model__": (
                "Compressed cosmology likelihood execution failed. Do not quote "
                "posterior constraints, S8/H0/Omega_m tensions, AIC/BIC, or significance."
            ),
        }


def _exec_run_cmb_rotation_likelihood(inp: dict) -> dict:
    from app.services.cmb_rotation_likelihoods import run_cmb_rotation_likelihood

    try:
        dataset_keys = inp.get("dataset_keys") or []
        if not isinstance(dataset_keys, list):
            raise ValueError("dataset_keys must be a list")
        return run_cmb_rotation_likelihood(
            dataset_keys=[str(key) for key in dataset_keys],
            model=str(inp.get("model") or "isotropic_beta"),
            priors=inp.get("priors") if isinstance(inp.get("priors"), dict) else None,
            random_seed=int(inp["random_seed"]) if inp.get("random_seed") is not None else None,
            n_samples=int(inp.get("n_samples") or 4000),
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "publication_ready": False,
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
            "__message_to_model__": (
                "CMB rotation likelihood execution failed. Do not quote beta_deg, "
                "alpha_deg, A_CB, EB/TB parity-odd significance, or detection claims."
            ),
        }


def _exec_run_nested_sampler(inp: dict) -> dict:
    from app.services.nested_sampling import run_controlled_nested_sampler

    try:
        parameters = inp.get("parameters")
        if not isinstance(parameters, list):
            raise ValueError("parameters must be a list")
        likelihoods = inp.get("likelihoods")
        return run_controlled_nested_sampler(
            parameters=parameters,
            gaussian_likelihood=(
                inp.get("gaussian_likelihood")
                if isinstance(inp.get("gaussian_likelihood"), dict)
                else None
            ),
            likelihoods=likelihoods if isinstance(likelihoods, list) else None,
            sampler_config=inp.get("sampler_config") if isinstance(inp.get("sampler_config"), dict) else None,
            random_seed=int(inp["random_seed"]) if inp.get("random_seed") is not None else None,
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "publication_ready": False,
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
            "__message_to_model__": (
                "Controlled nested sampling failed. Do not quote posterior, "
                "evidence, Bayes factor, or sampler diagnostics from this call."
            ),
        }


def _exec_evaluate_chain_diagnostics(inp: dict) -> dict:
    from app.services.chain_diagnostics import evaluate_chain_diagnostics

    try:
        chains = inp.get("chains")
        if chains is None:
            raise ValueError("chains is required")
        params = inp.get("parameters")
        return evaluate_chain_diagnostics(
            chains=chains if isinstance(chains, (dict, list)) else {},
            parameters=[str(p) for p in params] if isinstance(params, list) else None,
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "publication_ready": False,
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
        }


def _exec_build_cosmology_robustness_matrix(inp: dict) -> dict:
    from app.services.cosmology_likelihoods import build_robustness_matrix

    try:
        supernova_sets = inp.get("supernova_sets")
        if supernova_sets is not None and not isinstance(supernova_sets, list):
            raise ValueError("supernova_sets must be a list")
        return build_robustness_matrix(
            model=str(inp.get("model") or ""),
            supernova_sets=([str(key) for key in supernova_sets] if supernova_sets else None),
            include_h0_prior=bool(inp.get("include_h0_prior", True)),
            include_weak_lensing=bool(inp.get("include_weak_lensing", False)),
            sampler=str(inp.get("sampler") or "mcmc"),
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
        }


def _exec_run_cosmology_robustness_matrix(inp: dict) -> dict:
    from app.services.cosmology_likelihoods import run_robustness_matrix

    try:
        supernova_sets = inp.get("supernova_sets")
        if supernova_sets is not None and not isinstance(supernova_sets, list):
            raise ValueError("supernova_sets must be a list")
        return run_robustness_matrix(
            model=str(inp.get("model") or ""),
            supernova_sets=([str(key) for key in supernova_sets] if supernova_sets else None),
            include_h0_prior=bool(inp.get("include_h0_prior", True)),
            include_weak_lensing=bool(inp.get("include_weak_lensing", False)),
            random_seed=int(inp["random_seed"]) if inp.get("random_seed") is not None else None,
            n_samples=int(inp.get("n_samples") or 4000),
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
            "__message_to_model__": (
                "Compressed cosmology robustness execution failed. Do not quote "
                "robustness, posterior shifts, AIC/BIC, or tension claims."
            ),
        }


def _exec_assess_bao_bin_anomaly(inp: dict) -> dict:
    from app.services.cosmology_likelihoods import run_alcock_paczynski_test

    try:
        grid_raw = inp.get("omega_m_grid")
        grid: tuple[float, float, int] = (0.10, 0.50, 401)
        if isinstance(grid_raw, list) and len(grid_raw) == 3:
            grid = (float(grid_raw[0]), float(grid_raw[1]), int(grid_raw[2]))
        return run_alcock_paczynski_test(omega_m_grid=grid)
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "publication_ready": False,
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
            "__message_to_model__": (
                "BAO redshift-bin diagnostic failed. Do not quote AP Omega_m, "
                "per-bin residuals, outlier scores, or anomaly claims."
            ),
        }


def _exec_audit_published_constraint(inp: dict) -> dict:
    from app.services.cosmology_audit import audit_published_constraint

    try:
        dataset_keys = inp.get("dataset_keys") or []
        if not isinstance(dataset_keys, list):
            raise ValueError("dataset_keys must be a list")
        claimed = inp.get("claimed")
        if not isinstance(claimed, dict) or not claimed:
            raise ValueError("claimed must be a non-empty object mapping parameter -> [value, sigma]")
        return audit_published_constraint(
            model=str(inp.get("model") or ""),
            dataset_keys=[str(key) for key in dataset_keys],
            claimed=claimed,
            paper_ref=inp.get("paper_ref") if isinstance(inp.get("paper_ref"), dict) else None,
            claimed_datasets=inp.get("claimed_datasets") if isinstance(inp.get("claimed_datasets"), list) else None,
            random_seed=int(inp["random_seed"]) if inp.get("random_seed") is not None else None,
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
        }


async def dispatch_cosmology(
    tool_name: str, tool_input: dict, python_session_id: str | None = None
) -> dict | None:
    """Unified entry point for ai_tools/__init__: routes the 13 cosmology tools.

    Returns None for an unknown tool name so the caller can fall through; the
    caller already guards with ``tool_name in COSMOLOGY_TOOL_NAMES``.
    """
    if tool_name == "fit_cosmology_mcmc":
        return await _exec_fit_cosmology_mcmc(tool_input, python_session_id)
    elif tool_name == "run_cobaya_cosmology":
        return await _exec_run_cobaya_cosmology(tool_input, python_session_id)
    elif tool_name == "get_cosmology_run_status":
        return _exec_get_cosmology_run_status(tool_input)
    elif tool_name == "list_cosmology_datasets":
        return _exec_list_cosmology_datasets(tool_input)
    elif tool_name == "load_cosmology_data_product":
        return await _exec_load_cosmology_data_product(tool_input)
    elif tool_name == "build_cosmology_likelihood":
        return _exec_build_cosmology_likelihood(tool_input)
    elif tool_name == "run_cosmology_likelihood_chain":
        return _exec_run_cosmology_likelihood_chain(tool_input)
    elif tool_name == "run_cmb_rotation_likelihood":
        return _exec_run_cmb_rotation_likelihood(tool_input)
    elif tool_name == "run_nested_sampler":
        return _exec_run_nested_sampler(tool_input)
    elif tool_name == "evaluate_chain_diagnostics":
        return _exec_evaluate_chain_diagnostics(tool_input)
    elif tool_name == "build_cosmology_robustness_matrix":
        return _exec_build_cosmology_robustness_matrix(tool_input)
    elif tool_name == "run_cosmology_robustness_matrix":
        return _exec_run_cosmology_robustness_matrix(tool_input)
    elif tool_name == "assess_bao_bin_anomaly":
        return _exec_assess_bao_bin_anomaly(tool_input)
    elif tool_name == "audit_published_constraint":
        return _exec_audit_published_constraint(tool_input)
    return None
