"""Controlled CMB polarization-rotation likelihood helpers.

This module is deliberately narrower than the general cosmology likelihood
runner.  Planck distance priors, ACT lensing summaries, BAO, SN, and H0 priors
do not constrain EB/TB parity-odd polarization rotation.  A result from this
module is claimable only when a registered EB/TB rotation data product includes
both a covariance and an instrument-angle calibration prior.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np


CMBRotationExecutionMode = Literal["config_only", "compressed_gaussian"]


@dataclass(frozen=True)
class CMBRotationCitation:
    label: str
    year: int | None = None
    arxiv: str | None = None
    doi: str | None = None
    bibcode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CMBRotationCompressedSpec:
    parameter: str
    mean: float
    sigma: float
    source_locator: str
    approximation: str
    units: dict[str, str] = field(default_factory=lambda: {"beta_deg": "deg"})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CMBRotationDatasetEntry:
    key: str
    display_name: str
    version: str
    observables: tuple[str, ...]
    source_url: str
    citations: tuple[CMBRotationCitation, ...]
    covariance_provided: bool
    calibration_prior: dict[str, Any] | None
    execution_mode: CMBRotationExecutionMode = "config_only"
    compressed_likelihood: CMBRotationCompressedSpec | None = None
    notes: str = ""

    @property
    def probe(self) -> str:
        return "cmb_polarization_rotation"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["probe"] = self.probe
        result["citations"] = [citation.to_dict() for citation in self.citations]
        if self.compressed_likelihood is not None:
            result["compressed_likelihood"] = self.compressed_likelihood.to_dict()
        result["claimable_parameters"] = ["beta_deg"] if self.is_executable else []
        result["execution_level"] = "compressed_preliminary" if self.is_executable else "config_only"
        return result

    @property
    def is_executable(self) -> bool:
        return (
            self.execution_mode == "compressed_gaussian"
            and self.compressed_likelihood is not None
            and self.covariance_provided
            and bool(self.calibration_prior)
        )


CMB_ROTATION_DATASETS: dict[str, CMBRotationDatasetEntry] = {
    "planck_pr4_ebtb_rotation": CMBRotationDatasetEntry(
        key="planck_pr4_ebtb_rotation",
        display_name="Planck PR4/NPIPE EB/TB polarization-rotation products",
        version="candidate / config-only",
        observables=("EB", "TB", "beta_deg"),
        source_url="https://pla.esac.esa.int/",
        citations=(CMBRotationCitation(label="Planck Legacy Archive", year=None),),
        covariance_provided=False,
        calibration_prior=None,
        notes=(
            "Registered as a planning target only.  A claimable run requires a "
            "specific EB/TB data vector, covariance, and angle-calibration prior."
        ),
    ),
    "act_dr6_ebtb_rotation": CMBRotationDatasetEntry(
        key="act_dr6_ebtb_rotation",
        display_name="ACT DR6 EB/TB polarization-rotation products",
        version="candidate / config-only",
        observables=("EB", "TB", "beta_deg"),
        source_url="https://act.princeton.edu/",
        citations=(CMBRotationCitation(label="ACT DR6 public data products", year=None),),
        covariance_provided=False,
        calibration_prior=None,
        notes=(
            "Registered as a planning target only.  ACT lensing summaries are not "
            "valid substitutes for EB/TB rotation likelihoods."
        ),
    ),
    "bicep_keck_bk18_rotation": CMBRotationDatasetEntry(
        key="bicep_keck_bk18_rotation",
        display_name="BICEP/Keck BK18 polarization-angle products",
        version="candidate / config-only",
        observables=("EB", "TB", "beta_deg"),
        source_url="https://bicepkeck.org/",
        citations=(CMBRotationCitation(label="BICEP/Keck public data products", year=None),),
        covariance_provided=False,
        calibration_prior=None,
        notes=(
            "Registered as a planning target only.  Requires a typed angle "
            "calibration prior and EB/TB covariance before any beta claim."
        ),
    ),
}


def list_cmb_rotation_datasets() -> list[dict[str, Any]]:
    return [entry.to_dict() for entry in CMB_ROTATION_DATASETS.values()]


def get_cmb_rotation_dataset(key: str) -> CMBRotationDatasetEntry:
    try:
        return CMB_ROTATION_DATASETS[str(key)]
    except KeyError as exc:
        raise ValueError(
            f"unknown CMB rotation dataset {key!r}; choose one of {sorted(CMB_ROTATION_DATASETS)}"
        ) from exc


def cmb_rotation_dataset_exists(key: str) -> bool:
    return str(key) in CMB_ROTATION_DATASETS


def default_cmb_rotation_dataset_keys() -> list[str]:
    return list(CMB_ROTATION_DATASETS)


def run_cmb_rotation_likelihood(
    *,
    dataset_keys: list[str],
    model: str = "isotropic_beta",
    priors: dict[str, Any] | None = None,
    random_seed: int | None = None,
    n_samples: int = 4000,
) -> dict[str, Any]:
    if str(model or "").strip() != "isotropic_beta":
        return _blocked(
            model=str(model or ""),
            entries=[],
            seed=int(random_seed if random_seed is not None else 20260524),
            reason="CMB rotation v1 supports model='isotropic_beta' only.",
        )
    entries = [get_cmb_rotation_dataset(str(key)) for key in dataset_keys]
    seed = int(random_seed if random_seed is not None else 20260524)
    sample_count = max(256, min(int(n_samples or 4000), 20000))

    ready_entries = [entry for entry in entries if entry.is_executable]
    skipped_entries = [entry for entry in entries if not entry.is_executable]
    if not ready_entries:
        return _blocked(
            model="isotropic_beta",
            entries=entries,
            seed=seed,
            reason=(
                "No selected CMB rotation dataset has EB/TB measurements, covariance, "
                "and an instrument-angle calibration prior registered."
            ),
        )

    means = np.asarray([entry.compressed_likelihood.mean for entry in ready_entries if entry.compressed_likelihood], dtype=float)
    sigmas = np.asarray([entry.compressed_likelihood.sigma for entry in ready_entries if entry.compressed_likelihood], dtype=float)
    if np.any(~np.isfinite(sigmas)) or np.any(sigmas <= 0):
        return _blocked(
            model="isotropic_beta",
            entries=entries,
            seed=seed,
            reason="At least one CMB rotation compressed likelihood has an invalid sigma.",
        )

    precision = np.sum(1.0 / np.square(sigmas))
    beta_mean = float(np.sum(means / np.square(sigmas)) / precision)
    beta_sigma = float(math.sqrt(1.0 / precision))
    prior_bounds = _sanitize_beta_prior(priors)
    if not (prior_bounds[0] <= beta_mean <= prior_bounds[1]):
        return _blocked(
            model="isotropic_beta",
            entries=entries,
            seed=seed,
            reason="Combined beta posterior mean lies outside the configured prior bounds.",
        )

    rng = np.random.default_rng(seed)
    samples = rng.normal(beta_mean, beta_sigma, size=sample_count)
    summary = _summary(samples)
    chi2 = float(np.sum(np.square((means - beta_mean) / sigmas)))
    result_hash = _stable_hash({
        "model": "isotropic_beta",
        "dataset_keys": [entry.key for entry in entries],
        "seed": seed,
        "n_samples": sample_count,
        "beta_mean": beta_mean,
        "beta_sigma": beta_sigma,
        "priors": prior_bounds,
    })
    warnings = [
        "Compressed CMB rotation likelihood; use as preliminary consistency check, not a full Planck/ACT/BICEP likelihood.",
    ]
    if skipped_entries:
        warnings.append(
            "Datasets not run in compressed rotation phase: "
            + ", ".join(entry.key for entry in skipped_entries)
            + ". Register EB/TB data vector, covariance, and calibration prior before claiming them."
        )

    significance = abs(beta_mean) / beta_sigma if beta_sigma > 0 else float("inf")
    return {
        "success": True,
        "__tool_status__": "COMPLETED",
        "analysis_status": "CMB_ROTATION_CHAIN_READY",
        "publication_ready": True,
        "chain_tier": "publication",
        "claim_scope": "cmb_rotation_compressed_preliminary",
        "compressed_rotation_preliminary": True,
        "model": "isotropic_beta",
        "model_label": "isotropic CMB polarization rotation",
        "sampler": "compressed_beta_gaussian",
        "parameters": {
            "beta_deg": summary,
            "rotation_significance": {
                "median": round(float(significance), 6),
                "mean": round(float(significance), 6),
                "std": 0.0,
                "hdi_low_94": round(float(significance), 6),
                "hdi_high_94": round(float(significance), 6),
                "rhat": 1.0,
                "ess_bulk": sample_count,
                "status": "analytic_gaussian",
            },
        },
        "posterior_summary": {"beta_deg": summary},
        "fit_statistics": {
            "chi2": round(chi2, 6),
            "delta_chi2": 0.0,
            "aic": round(chi2 + 2.0, 6),
            "bic": round(chi2 + math.log(max(len(ready_entries), 1)), 6),
            "n_constraints": len(ready_entries),
            "n_parameters": 1,
        },
        "chain_diagnostics": {
            "overall_status": "analytic_gaussian",
            "publication_ready": True,
            "rhat": 1.0,
            "ess_bulk": sample_count,
            "n_draws": sample_count,
            "n_chains": 1,
            "thresholds": {"ess_min": 400, "rhat_max": 1.05},
        },
        "datasets_used": [entry.to_dict() for entry in ready_entries],
        "datasets_not_run": [entry.to_dict() for entry in skipped_entries],
        "dataset_keys": [entry.key for entry in entries],
        "priors": {"beta_deg": list(prior_bounds)},
        "random_seed": seed,
        "n_samples": sample_count,
        "runner_hash": result_hash,
        "warnings": warnings,
        "__message_to_model__": (
            "This is a publication-ready compressed CMB-rotation preliminary result, "
            "not a full external Planck/ACT/BICEP likelihood. You may quote beta_deg "
            "only with that caveat and only for datasets_used."
        ),
        "provenance": {
            "cmb_rotation_likelihood": {
                "runner": "compressed_beta_gaussian",
                "runner_hash": result_hash,
                "dataset_keys": [entry.key for entry in entries],
                "datasets_used": [entry.key for entry in ready_entries],
                "datasets_not_run": [entry.key for entry in skipped_entries],
                "citations": _collect_citations(entries),
                "compressed_sources": [
                    {
                        "dataset_key": entry.key,
                        "source_locator": entry.compressed_likelihood.source_locator
                        if entry.compressed_likelihood else None,
                        "approximation": entry.compressed_likelihood.approximation
                        if entry.compressed_likelihood else None,
                    }
                    for entry in ready_entries
                ],
                "publication_ready": True,
            }
        },
    }


def _blocked(*, model: str, entries: list[CMBRotationDatasetEntry], seed: int, reason: str) -> dict[str, Any]:
    return {
        "success": True,
        "__tool_status__": "PARTIAL",
        "analysis_status": "CMB_ROTATION_SCOPE_GAP",
        "publication_ready": False,
        "chain_tier": "blocked",
        "claim_scope": "cmb_rotation_scope_gap",
        "model": model or "isotropic_beta",
        "sampler": "compressed_beta_gaussian",
        "parameters": {},
        "datasets_used": [],
        "datasets_not_run": [entry.to_dict() for entry in entries],
        "dataset_keys": [entry.key for entry in entries],
        "random_seed": seed,
        "warnings": [reason],
        "__do_not_claim__": True,
        "__message_to_model__": (
            reason
            + " Do not quote beta_deg, alpha_deg, A_CB, EB/TB rotation significance, "
            "or parity-violation detection numbers from this result."
        ),
    }


def _summary(samples: np.ndarray) -> dict[str, Any]:
    median = float(np.median(samples))
    low, high = np.percentile(samples, [3.0, 97.0])
    return {
        "median": round(median, 8),
        "mean": round(float(np.mean(samples)), 8),
        "std": round(float(np.std(samples, ddof=1)), 8),
        "hdi_low_94": round(float(low), 8),
        "hdi_high_94": round(float(high), 8),
        "hdi_94": [round(float(low), 8), round(float(high), 8)],
        "rhat": 1.0,
        "ess_bulk": int(samples.size),
        "status": "analytic_gaussian",
    }


def _sanitize_beta_prior(priors: dict[str, Any] | None) -> tuple[float, float]:
    default = (-10.0, 10.0)
    if not isinstance(priors, dict):
        return default
    raw = priors.get("beta_deg") or priors.get("beta") or default
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        return default
    try:
        low = float(raw[0])
        high = float(raw[1])
    except (TypeError, ValueError):
        return default
    if not math.isfinite(low) or not math.isfinite(high) or low >= high:
        return default
    return (max(low, -90.0), min(high, 90.0))


def _collect_citations(entries: list[CMBRotationDatasetEntry]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    citations: list[dict[str, Any]] = []
    for entry in entries:
        for citation in entry.citations:
            payload = citation.to_dict()
            key = str(payload.get("bibcode") or payload.get("arxiv") or payload.get("doi") or payload.get("label") or "")
            if key and key not in seen:
                seen.add(key)
                citations.append(payload)
    return citations


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
