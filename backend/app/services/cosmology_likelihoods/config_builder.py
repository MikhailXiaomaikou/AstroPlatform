"""Likelihood config builder, model/dataset validation, warnings, citations.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.services.cosmology_likelihoods.core import (
    CosmologyDatasetEntry,
    DEFAULT_BUILDER_PRIORS,
    MODEL_LABELS,
    SUPPORTED_MODELS,
)

from app.services.cosmology_likelihoods.registry import (
    get_cosmology_dataset,
)



def build_likelihood_config(
    *,
    model: str,
    dataset_keys: list[str],
    priors: dict[str, Any] | None = None,
    sampler: str = "mcmc",
    output_format: str = "both",
) -> dict[str, Any]:
    model_key = _validate_model(model)
    entries = _validate_dataset_selection(model_key, dataset_keys)
    prior_bounds = _sanitize_builder_priors(model_key, priors)
    config_hash = _config_hash(model_key, [entry.key for entry in entries], prior_bounds, sampler)
    cobaya = _build_cobaya_config(model_key, entries, prior_bounds, sampler)
    cosmosis = _build_cosmosis_config(model_key, entries, prior_bounds, sampler)
    warnings = _selection_warnings(entries)

    result: dict[str, Any] = {
        "success": True,
        "__tool_status__": "PARTIAL",
        "analysis_status": "CONFIG_READY",
        "publication_ready": False,
        "__do_not_claim__": True,
        "model": model_key,
        "model_label": MODEL_LABELS[model_key],
        "datasets": [entry.to_dict() for entry in entries],
        "priors": {name: list(bounds) for name, bounds in prior_bounds.items()},
        "sampler": sampler,
        "config_hash": config_hash,
        "config_status": "ready_to_review",
        "execution_status": "not_run",
        "warnings": warnings,
        "__message_to_model__": (
            "This tool generated a likelihood configuration only. Do not quote "
            "posterior constraints, tensions, AIC/BIC, or detection significance "
            "until a chain runner returns publication_ready=true."
        ),
    }
    if output_format in {"cobaya", "both"}:
        result["cobaya"] = cobaya
    if output_format in {"cosmosis", "both"}:
        result["cosmosis"] = cosmosis
    result["provenance"] = {
        "cosmology_likelihood": {
            "registry_version": "2026-04-30",
            "config_hash": config_hash,
            "dataset_keys": [entry.key for entry in entries],
            "citations": _collect_citations(entries),
        }
    }
    return result


def build_robustness_matrix(
    *,
    model: str,
    supernova_sets: list[str] | None = None,
    include_h0_prior: bool = True,
    include_weak_lensing: bool = False,
    sampler: str = "mcmc",
) -> dict[str, Any]:
    model_key = _validate_model(model)
    sn_keys = list(supernova_sets) if supernova_sets is not None else ["pantheon_plus"]
    if not sn_keys:
        sn_keys = ["pantheon_plus"]
    matrix: list[dict[str, Any]] = []

    base_combos: list[tuple[str, list[str]]] = [
        ("BAO only", ["desi_dr1_bao"]),
        ("SN only", [sn_keys[0]]),
        ("CMB only", ["planck2018_compressed"]),
        ("BAO + CMB", ["desi_dr1_bao", "planck2018_compressed"]),
    ]
    if include_weak_lensing:
        base_combos.append((
            "BAO + CMB + weak lensing",
            [
                "desi_dr1_bao",
                "planck2018_compressed",
                "kids1000_wl",
                "des_y3_3x2pt",
                "hsc_y1_cosmic_shear",
            ],
        ))
    for sn_key in sn_keys:
        label = get_cosmology_dataset(sn_key).display_name
        if sn_key != sn_keys[0]:
            base_combos.append((f"{label} only", [sn_key]))
        base_combos.append((f"BAO + {label}", ["desi_dr1_bao", sn_key]))
        base_combos.append((f"{label} + CMB", [sn_key, "planck2018_compressed"]))
        base_combos.append(
            (f"BAO + {label} + CMB", ["desi_dr1_bao", sn_key, "planck2018_compressed"])
        )

    for label, keys in base_combos:
        variants = [(label, keys)]
        if include_h0_prior and _can_add_shoes_h0_prior(keys):
            variants.append((label + " + SH0ES H0", keys + ["shoes_h0_riess22"]))
        for variant_label, variant_keys in variants:
            config = build_likelihood_config(
                model=model_key,
                dataset_keys=variant_keys,
                sampler=sampler,
                output_format="cobaya",
            )
            matrix.append(
                {
                    "label": variant_label,
                    "dataset_keys": variant_keys,
                    "config_hash": config["config_hash"],
                    "config": config["cobaya"],
                    "warnings": config["warnings"],
                    "requires_chain_run": True,
                    "interpretation_guardrail": (
                        "Compare posterior shifts only after all cells have "
                        "publication_ready=true chains. Do not infer robustness "
                        "from config availability alone."
                    ),
                }
            )

    return {
        "success": True,
        "__tool_status__": "PARTIAL",
        "analysis_status": "CONFIG_READY",
        "publication_ready": False,
        "__do_not_claim__": True,
        "model": model_key,
        "matrix_size": len(matrix),
        "matrix": matrix,
        "__message_to_model__": (
            "This robustness matrix is a set of chain configurations, not "
            "cosmological evidence. Explain what will be tested, then run or "
            "request publication-ready chains before making scientific claims."
        ),
    }


def _validate_model(model: str) -> str:
    key = str(model or "").strip().lower()
    aliases = {
        "lambda_cdm": "lcdm",
        "lambdacdm": "lcdm",
        "λcdm": "lcdm",
        "flat_lcdm": "lcdm",
        "flat_wcdm": "wcdm",
        "flat_w0wa_cdm": "w0wa_cdm",
        "ow0wa_cdm": "ok_w0wa_cdm",
        "curved_lcdm": "ok_lcdm",
        "curved_wcdm": "ok_wcdm",
        "curved_w0wa": "ok_w0wa_cdm",
    }
    key = aliases.get(key, key)
    if key not in SUPPORTED_MODELS:
        raise ValueError(f"unsupported cosmology model {model!r}; choose one of {sorted(SUPPORTED_MODELS)}")
    return key


def _validate_dataset_selection(model: str, keys: list[str]) -> list[CosmologyDatasetEntry]:
    if not keys:
        raise ValueError("at least one cosmology dataset must be selected")
    entries = [get_cosmology_dataset(key) for key in keys]
    unsupported = [
        entry.key
        for entry in entries
        if model not in entry.applicable_models
    ]
    if unsupported:
        raise ValueError(f"datasets not applicable to {model}: {unsupported}")
    if len({entry.key for entry in entries}) != len(entries):
        raise ValueError("dataset selection contains duplicates")
    return entries


def _sanitize_builder_priors(model: str, priors: dict[str, Any] | None) -> dict[str, tuple[float, float]]:
    params = SUPPORTED_MODELS[model]
    user = priors or {}
    if not isinstance(user, dict):
        raise ValueError("priors must be an object")
    unknown = set(user) - set(params)
    if unknown:
        raise ValueError(f"priors include unsupported parameters for {model}: {sorted(unknown)}")
    sanitized: dict[str, tuple[float, float]] = {}
    for name in params:
        default_low, default_high = DEFAULT_BUILDER_PRIORS[name]
        raw = user.get(name, (default_low, default_high))
        if isinstance(raw, dict):
            low_raw, high_raw = raw.get("min"), raw.get("max")
        elif isinstance(raw, (list, tuple)) and len(raw) == 2:
            low_raw, high_raw = raw
        else:
            raise ValueError(f"prior for {name} must be [min, max]")
        low, high = float(low_raw), float(high_raw)
        if low >= high:
            raise ValueError(f"prior for {name} must satisfy min < max")
        if low < default_low or high > default_high:
            raise ValueError(f"prior for {name} must stay within [{default_low}, {default_high}]")
        sanitized[name] = (low, high)
    return sanitized


def _build_cobaya_config(
    model: str,
    entries: list[CosmologyDatasetEntry],
    priors: dict[str, tuple[float, float]],
    sampler: str,
) -> dict[str, Any]:
    likelihood: dict[str, Any] = {}
    for entry in entries:
        likelihood[entry.key] = {
            "adapter": entry.cobaya_likelihood,
            "status": entry.status,
            "source_url": entry.source_url,
            "covariance": entry.covariance.to_dict(),
        }
    params = {
        name: {
            "prior": {"min": low, "max": high},
            "proposal": max((high - low) / 50.0, 1e-4),
        }
        for name, (low, high) in priors.items()
    }
    return {
        "theory": {"camb": {"extra_args": _model_theory_args(model)}},
        "likelihood": likelihood,
        "params": params,
        "sampler": {sampler: {"Rminus1_stop": 0.01, "max_samples": 200000}},
    }


def _build_cosmosis_config(
    model: str,
    entries: list[CosmologyDatasetEntry],
    priors: dict[str, tuple[float, float]],
    sampler: str,
) -> str:
    pipeline_modules = ["consistency", "camb"] + [entry.key for entry in entries]
    lines = [
        "[runtime]",
        f"sampler = {sampler}",
        "",
        "[pipeline]",
        "modules = " + " ".join(pipeline_modules),
        "values = values.ini",
        "likelihoods = " + " ".join(entry.key for entry in entries),
        "",
        "[priors]",
    ]
    for name, (low, high) in priors.items():
        lines.append(f"{name} = uniform {low:g} {high:g}")
    for entry in entries:
        lines.extend(
            [
                "",
                f"[{entry.key}]",
                f"module = {entry.cosmosis_module or 'external:' + entry.key}",
                f"source_url = {entry.source_url}",
                f"status = {entry.status}",
            ]
        )
    return "\n".join(lines) + "\n"


def _model_theory_args(model: str) -> dict[str, Any]:
    # lens_potential_accuracy=1 makes CAMB compute the lensed TT/TE/EE the plik_lite
    # high-l likelihood reads; CAMB ignores it when no lensed Cl is requested (the
    # geometric BAO/SN cobaya runs), so it is harmless there.
    # ΛCDM uses CAMB's DEFAULT dark energy (a cosmological constant) — DO NOT pass
    # dark_energy_model="lambda" (no such CAMB class; it raises CAMBValueError).
    # Only the dynamical-DE extensions name a real class (fluid / ppf).
    args: dict[str, Any] = {"lens_potential_accuracy": 1}
    if "w0wa" in model:
        # CPL w(a)=w0+wa(1-a): PPF lets w cross -1.  The substring "wcdm" is NOT
        # contained in "w0wa_cdm", so the CPL keys must be matched explicitly and
        # FIRST — otherwise every w0waCDM model silently fell through to the
        # cosmological-constant default while w0/wa were still being sampled.
        args["dark_energy_model"] = "ppf"
    elif "wcdm" in model:
        args["dark_energy_model"] = "fluid"
    if "mnu" in model:
        args["num_massive_neutrinos"] = 1
    # ok_* curvature: NO extra theory arg. "curved" is not a CAMB parameter
    # (live-verified: CAMBUnknownArgumentError) — it killed every ok_* CMB
    # chain at CAMB setup. CAMB infers curvature from the sampled omk input
    # (the omegak->omk alias in cobaya_runner's YAML builder), so nothing is
    # needed here. (2026-06-12)
    return args


def _combination_warnings(entries: list[CosmologyDatasetEntry]) -> list[str]:
    """Warn when two co-selected datasets declare each other in do_not_combine_with
    — overlapping/subset data products that would double-count if their likelihoods
    are co-added in one fit (e.g. the GA2018 31-point CC compilation and its
    Moresco-2020 15-point subset)."""
    keys = {entry.key for entry in entries}
    seen: set[frozenset[str]] = set()
    msgs: list[str] = []
    for entry in entries:
        for other in entry.do_not_combine_with:
            if other not in keys:
                continue
            pair = frozenset({entry.key, other})
            if pair in seen:
                continue
            seen.add(pair)
            msgs.append(
                f"Datasets '{entry.key}' and '{other}' overlap and must not be co-added "
                "in one likelihood (double-counts shared measurements); use them as "
                "robustness alternatives, not a joint fit."
            )
    return msgs


def _can_add_shoes_h0_prior(dataset_keys: list[str]) -> bool:
    """Whether SH0ES can be added without duplicating a calibrated branch."""
    if "shoes_h0_riess22" in dataset_keys:
        return False
    candidate_keys = [*dataset_keys, "shoes_h0_riess22"]
    entries = [get_cosmology_dataset(key) for key in candidate_keys]
    return not _combination_warnings(entries)


def _selection_warnings(entries: list[CosmologyDatasetEntry]) -> list[str]:
    warnings: list[str] = []
    if any(entry.status != "ready" for entry in entries):
        warnings.append(
            "One or more selected datasets require external likelihood/data files; "
            "this config is not a completed chain result."
        )
    probes = [entry.probe for entry in entries]
    if probes.count("sn") > 1:
        warnings.append(
            "Multiple SN compilations are selected. Treat them as robustness alternatives "
            "unless you have audited overlap and covariance between compilations."
        )
    if "h0_prior" in probes and not any(probe == "sn" for probe in probes):
        warnings.append("H0 prior selected without SN distances; check whether this is intended.")
    warnings.extend(_combination_warnings(entries))
    return warnings


def _collect_citations(entries: list[CosmologyDatasetEntry]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int]] = set()
    citations: list[dict[str, Any]] = []
    for entry in entries:
        for citation in entry.citations:
            key = (citation.label, citation.year)
            if key in seen:
                continue
            seen.add(key)
            citations.append(citation.to_dict())
    return citations


def _config_hash(
    model: str,
    dataset_keys: list[str],
    priors: dict[str, tuple[float, float]],
    sampler: str,
) -> str:
    canonical = json.dumps(
        {
            "model": model,
            "dataset_keys": dataset_keys,
            "priors": {name: list(bounds) for name, bounds in priors.items()},
            "sampler": sampler,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
