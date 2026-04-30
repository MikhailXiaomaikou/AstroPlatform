"""Curated observational-cosmology dataset registry and config builder.

This module is intentionally metadata-first.  It records which public
cosmology datasets the platform knows about, what they measure, which
models they can be used with, where their covariance/likelihood lives,
and which citations must follow any downstream result.

The builder emits controlled Cobaya/CosmoSIS-style configuration objects;
it does not silently run external likelihoods that are not installed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app.services.cosmology_mcmc import DEFAULT_PRIORS

CosmologyModel = Literal[
    "lcdm",
    "wcdm",
    "w0wa_cdm",
    "ok_lcdm",
    "ok_wcdm",
    "ok_w0wa_cdm",
    "lcdm_mnu",
    "w0wa_cdm_mnu",
]

DatasetStatus = Literal["ready", "external_likelihood", "metadata_only"]


SUPPORTED_MODELS: dict[str, tuple[str, ...]] = {
    "lcdm": ("H0", "omegam", "ombh2", "rd"),
    "wcdm": ("H0", "omegam", "ombh2", "rd", "w"),
    "w0wa_cdm": ("H0", "omegam", "ombh2", "rd", "w0", "wa"),
    "ok_lcdm": ("H0", "omegam", "ombh2", "rd", "omegak"),
    "ok_wcdm": ("H0", "omegam", "ombh2", "rd", "omegak", "w"),
    "ok_w0wa_cdm": ("H0", "omegam", "ombh2", "rd", "omegak", "w0", "wa"),
    "lcdm_mnu": ("H0", "omegam", "ombh2", "rd", "mnu"),
    "w0wa_cdm_mnu": ("H0", "omegam", "ombh2", "rd", "w0", "wa", "mnu"),
}

DEFAULT_BUILDER_PRIORS: dict[str, tuple[float, float]] = {
    "H0": DEFAULT_PRIORS["H0"],
    "omegam": DEFAULT_PRIORS["Om0"],
    "ombh2": (0.018, 0.026),
    "rd": (130.0, 170.0),
    "w": (-2.5, -0.2),
    "w0": DEFAULT_PRIORS["w0"],
    "wa": DEFAULT_PRIORS["wa"],
    "omegak": (-0.3, 0.3),
    "mnu": (0.0, 1.0),
}


@dataclass(frozen=True)
class DatasetCitation:
    label: str
    year: int
    arxiv: str | None = None
    doi: str | None = None
    bibcode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CovarianceSpec:
    kind: str
    provided: bool
    description: str
    url: str | None = None
    format: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CosmologyDatasetEntry:
    key: str
    display_name: str
    version: str
    probe: str
    status: DatasetStatus
    observables: tuple[str, ...]
    units: dict[str, str]
    applicable_models: tuple[CosmologyModel, ...]
    likelihood_family: str
    covariance: CovarianceSpec
    source_url: str
    citations: tuple[DatasetCitation, ...]
    notes: str
    cobaya_likelihood: str | None = None
    cosmosis_module: str | None = None
    nuisance_parameters: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["citations"] = [citation.to_dict() for citation in self.citations]
        result["covariance"] = self.covariance.to_dict()
        return result


MODEL_LABELS: dict[str, str] = {
    "lcdm": "flat ΛCDM",
    "wcdm": "flat wCDM",
    "w0wa_cdm": "flat w0waCDM / CPL",
    "ok_lcdm": "curved ΛCDM",
    "ok_wcdm": "curved wCDM",
    "ok_w0wa_cdm": "curved w0waCDM / CPL",
    "lcdm_mnu": "flat ΛCDM + neutrino mass",
    "w0wa_cdm_mnu": "flat w0waCDM + neutrino mass",
}


ALL_MODELS: tuple[CosmologyModel, ...] = tuple(SUPPORTED_MODELS)  # type: ignore[assignment]
SN_MODELS: tuple[CosmologyModel, ...] = ALL_MODELS
BAO_MODELS: tuple[CosmologyModel, ...] = ALL_MODELS
CMB_MODELS: tuple[CosmologyModel, ...] = ALL_MODELS
H0_MODELS: tuple[CosmologyModel, ...] = ALL_MODELS


_REGISTRY: dict[str, CosmologyDatasetEntry] = {
    "desi_dr1_bao": CosmologyDatasetEntry(
        key="desi_dr1_bao",
        display_name="DESI DR1 BAO",
        version="DR1 2024 BAO likelihood",
        probe="bao",
        status="external_likelihood",
        observables=("DM_over_rd", "DH_over_rd", "DV_over_rd"),
        units={"distance_ratios": "dimensionless", "redshift": "dimensionless"},
        applicable_models=BAO_MODELS,
        likelihood_family="gaussian_bao",
        covariance=CovarianceSpec(
            kind="block covariance",
            provided=True,
            description="DESI DR1 BAO Gaussian covariance for BGS/LRG/ELG/QSO/LyA bins.",
            url="https://data.desi.lbl.gov/doc/releases/dr1/vac/bao-cosmo-params/",
            format="DESI VAC / desilike / CosmoSIS module data",
        ),
        source_url="https://data.desi.lbl.gov/doc/releases/dr1/vac/bao-cosmo-params/",
        citations=(
            DatasetCitation(
                label="DESI Collaboration 2024 DR1 BAO cosmology",
                year=2024,
                arxiv="2404.03002",
            ),
        ),
        notes="Use as BAO-only or combined late-universe distance anchor; requires rd prior or CMB calibration.",
        cobaya_likelihood="external:desilike.desi_dr1_bao",
        cosmosis_module="likelihood/bao/desi1-dr1/desi1_dr1.py",
    ),
    "pantheon_plus": CosmologyDatasetEntry(
        key="pantheon_plus",
        display_name="Pantheon+",
        version="Pantheon+SH0ES DataRelease 2022",
        probe="sn",
        status="external_likelihood",
        observables=("zHD", "zHEL", "mu", "mu_covariance"),
        units={"z": "dimensionless", "mu": "mag"},
        applicable_models=SN_MODELS,
        likelihood_family="sn_distance_modulus",
        covariance=CovarianceSpec(
            kind="stat+sys covariance",
            provided=True,
            description="Pantheon+ distance-modulus covariance matrix.",
            url="https://github.com/PantheonPlusSH0ES/DataRelease",
            format="ASCII/FITS covariance in data release",
        ),
        source_url="https://github.com/PantheonPlusSH0ES/DataRelease",
        citations=(
            DatasetCitation(label="Scolnic et al. Pantheon+ sample", year=2022, arxiv="2112.03863"),
            DatasetCitation(label="Brout et al. Pantheon+ cosmology", year=2022, arxiv="2202.04077"),
        ),
        notes="Can be used with or without SH0ES calibration; keep H0 prior separate unless explicitly selected.",
        cobaya_likelihood="external:sn.pantheon_plus",
        cosmosis_module="Pantheon+_Data/5_COSMOLOGY/cosmosis_likelihoods",
        nuisance_parameters=("M_B",),
    ),
    "des_sn5yr": CosmologyDatasetEntry(
        key="des_sn5yr",
        display_name="DES-SN 5YR",
        version="DES-SN5YR Release 1 / 2024 cosmology sample",
        probe="sn",
        status="external_likelihood",
        observables=("z", "mu", "mu_covariance"),
        units={"z": "dimensionless", "mu": "mag"},
        applicable_models=SN_MODELS,
        likelihood_family="sn_distance_modulus",
        covariance=CovarianceSpec(
            kind="stat+sys covariance",
            provided=True,
            description="DES 5-year SN distance and systematic covariance products.",
            url="https://zenodo.org/records/12720778",
            format="DES-SN5YR data release",
        ),
        source_url="https://zenodo.org/records/12720778",
        citations=(
            DatasetCitation(label="DES Collaboration 2024 SN cosmology", year=2024, arxiv="2401.02929"),
            DatasetCitation(label="DES-SN5YR data products", year=2024, arxiv="2406.05046"),
        ),
        notes="Photometrically classified DES SN sample; useful robustness partner for Pantheon+/Union3.",
        cobaya_likelihood="external:sn.des_sn5yr",
        cosmosis_module="external:DES-SN5YR",
        nuisance_parameters=("M_B",),
    ),
    "union3": CosmologyDatasetEntry(
        key="union3",
        display_name="Union3 / UNITY1.5",
        version="Union3 2023 arXiv release",
        probe="sn",
        status="external_likelihood",
        observables=("z", "distance_modulus", "unity_covariance_or_posterior"),
        units={"z": "dimensionless", "mu": "mag"},
        applicable_models=SN_MODELS,
        likelihood_family="sn_unity",
        covariance=CovarianceSpec(
            kind="UNITY covariance/posterior products",
            provided=True,
            description="Union3/UNITY1.5 released distances, light-curve fits, and framework products.",
            url="https://arxiv.org/abs/2311.12098",
            format="Union3 / UNITY1.5 release products",
        ),
        source_url="https://arxiv.org/abs/2311.12098",
        citations=(
            DatasetCitation(label="Rubin et al. Union3/UNITY1.5", year=2023, arxiv="2311.12098"),
        ),
        notes="Use as an independent SN robustness branch; do not mix with Pantheon+ as if independent.",
        cobaya_likelihood="external:sn.union3",
        cosmosis_module="external:Union3/UNITY1.5",
        nuisance_parameters=("M_B",),
    ),
    "planck2018_compressed": CosmologyDatasetEntry(
        key="planck2018_compressed",
        display_name="Planck 2018 compressed distance priors",
        version="Planck final release distance-prior compression",
        probe="cmb_compressed",
        status="metadata_only",
        observables=("R", "l_A", "ombh2", "ns"),
        units={"R": "dimensionless", "l_A": "dimensionless", "ombh2": "dimensionless"},
        applicable_models=CMB_MODELS,
        likelihood_family="cmb_distance_prior",
        covariance=CovarianceSpec(
            kind="compressed covariance",
            provided=True,
            description="Distance-prior mean vector and covariance from Planck final release literature.",
            url="https://arxiv.org/abs/1808.05724",
            format="paper table",
        ),
        source_url="https://wiki.cosmos.esa.int/planck-legacy-archive/",
        citations=(
            DatasetCitation(label="Planck Collaboration VI 2020", year=2020, doi="10.1051/0004-6361/201833910"),
            DatasetCitation(label="Chen, Huang & Wang distance priors", year=2019, arxiv="1808.05724", doi="10.1088/1475-7516/2019/02/028"),
        ),
        notes="Compressed CMB prior, not a replacement for the full Planck likelihood in extended models.",
        cobaya_likelihood="external:planck_2018_distance_prior",
        cosmosis_module="external:planck2018_distance_priors",
    ),
    "act_dr6_lensing": CosmologyDatasetEntry(
        key="act_dr6_lensing",
        display_name="ACT DR6 CMB lensing",
        version="ACT DR6 lensing likelihood v1.2",
        probe="cmb_lensing",
        status="external_likelihood",
        observables=("C_L_kappakappa",),
        units={"C_L": "dimensionless"},
        applicable_models=CMB_MODELS,
        likelihood_family="cmb_lensing",
        covariance=CovarianceSpec(
            kind="bandpower covariance",
            provided=True,
            description="ACT DR6 lensing likelihood data tarball and likelihood code.",
            url="https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_lh_get.html",
            format="ACT_dr6_likelihood_v1.2.tgz",
        ),
        source_url="https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_lh_info.html",
        citations=(
            DatasetCitation(label="ACT DR6 lensing likelihood", year=2024, arxiv="2304.05203"),
            DatasetCitation(label="Carron, Mirmelstein & Lewis likelihood method", year=2022, arxiv="2206.07773"),
        ),
        notes="Requires ACT likelihood data and external code; pair carefully with Planck CMB to avoid double-counting lensing.",
        cobaya_likelihood="external:act_dr6_lenslike.ACTDR6LensLike",
        cosmosis_module="external:act_dr6_lenslike",
    ),
    "cosmic_chronometers": CosmologyDatasetEntry(
        key="cosmic_chronometers",
        display_name="Cosmic chronometers H(z)",
        version="Moresco-style H(z) compilation with covariance recipe",
        probe="hz",
        status="metadata_only",
        observables=("z", "H_z", "H_z_covariance"),
        units={"z": "dimensionless", "H_z": "km s^-1 Mpc^-1"},
        applicable_models=ALL_MODELS,
        likelihood_family="hz_gaussian",
        covariance=CovarianceSpec(
            kind="recipe covariance",
            provided=False,
            description="Current public table requires adding systematic covariance following Moresco et al. recipes.",
            url="https://cluster.difa.unibo.it/astro/CC_data/",
            format="H(z) table + covariance recipe",
        ),
        source_url="https://cluster.difa.unibo.it/astro/CC_data/",
        citations=(
            DatasetCitation(label="Moresco et al. covariance systematics", year=2020, arxiv="2003.07362"),
            DatasetCitation(label="Jiao et al. LEGA-C chronometers", year=2022, arxiv="2205.05701"),
        ),
        notes="Do not treat diagonal-only CC errors as publication-grade if the systematic covariance was omitted.",
        cobaya_likelihood="external:cosmic_chronometers",
        cosmosis_module="external:hz/cosmic_chronometers",
    ),
    "shoes_h0_riess22": CosmologyDatasetEntry(
        key="shoes_h0_riess22",
        display_name="SH0ES H0 prior",
        version="Riess et al. 2022 SH0ES H0 prior",
        probe="h0_prior",
        status="ready",
        observables=("H0",),
        units={"H0": "km s^-1 Mpc^-1"},
        applicable_models=H0_MODELS,
        likelihood_family="gaussian_prior",
        covariance=CovarianceSpec(
            kind="1D gaussian variance",
            provided=True,
            description="H0 = 73.04 +/- 1.04 km/s/Mpc.",
            url="https://doi.org/10.3847/2041-8213/ac5c5b",
            format="scalar Gaussian prior",
        ),
        source_url="https://doi.org/10.3847/2041-8213/ac5c5b",
        citations=(
            DatasetCitation(label="Riess et al. SH0ES", year=2022, arxiv="2112.04510", doi="10.3847/2041-8213/ac5c5b"),
        ),
        notes="Use only when the analysis explicitly includes a local-distance-ladder H0 prior.",
        cobaya_likelihood="gaussian:H0=73.04,sigma=1.04",
        cosmosis_module="prior H0 = gaussian 73.04 1.04",
    ),
}


def list_cosmology_datasets(
    *,
    probe: str | None = None,
    status: DatasetStatus | None = None,
) -> dict[str, Any]:
    entries = [
        entry.to_dict()
        for entry in _REGISTRY.values()
        if (probe is None or entry.probe == probe)
        and (status is None or entry.status == status)
    ]
    entries.sort(key=lambda item: item["key"])
    return {
        "success": True,
        "registry_version": "2026-04-30",
        "dataset_count": len(entries),
        "datasets": entries,
        "supported_models": {
            key: {"label": MODEL_LABELS[key], "parameters": list(params)}
            for key, params in SUPPORTED_MODELS.items()
        },
    }


def get_cosmology_dataset(key: str) -> CosmologyDatasetEntry:
    try:
        return _REGISTRY[str(key)]
    except KeyError as exc:
        raise ValueError(f"unknown cosmology dataset {key!r}; choose one of {sorted(_REGISTRY)}") from exc


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
    sampler: str = "mcmc",
) -> dict[str, Any]:
    model_key = _validate_model(model)
    sn_keys = supernova_sets or ["pantheon_plus", "des_sn5yr", "union3"]
    matrix: list[dict[str, Any]] = []

    base_combos: list[tuple[str, list[str]]] = [
        ("BAO only", ["desi_dr1_bao"]),
        ("BAO + CMB", ["desi_dr1_bao", "planck2018_compressed"]),
    ]
    for sn_key in sn_keys:
        label = get_cosmology_dataset(sn_key).display_name
        base_combos.append((f"BAO + {label}", ["desi_dr1_bao", sn_key]))
        base_combos.append(
            (f"BAO + {label} + CMB", ["desi_dr1_bao", sn_key, "planck2018_compressed"])
        )

    for label, keys in base_combos:
        variants = [(label, keys)]
        if include_h0_prior:
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
        "Λcdm": "lcdm",
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
    args: dict[str, Any] = {"dark_energy_model": "lambda"}
    if "wcdm" in model:
        args["dark_energy_model"] = "fluid"
    if "mnu" in model:
        args["num_massive_neutrinos"] = 1
    if model.startswith("ok_"):
        args["curved"] = True
    return args


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
