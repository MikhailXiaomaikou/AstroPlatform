"""Shared types, model tables, prior tables and S8 helpers.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np

from app.services.cosmology_mcmc import DEFAULT_PRIORS


# NOTE (2026-07-03 package split): keep the pre-split logger name so the whole
# package logs/filters through the exact same logger object as the old module.
logger = logging.getLogger("app.services.cosmology_likelihoods")

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
ExecutionMode = Literal["config_only", "compressed_gaussian", "external_cobaya", "external_cosmosis"]


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
    # SN Ia absolute-magnitude nuisance (Pantheon+SH0ES floats this).
    # Pantheon+SH0ES best-fit M_B = -19.253 (Riess+ 2022 ApJL 934 L7).
    # Narrow prior [-19.7, -18.8] keeps importance sampler ESS sane while
    # still allowing ±0.5 mag wandering, which is far wider than the data's
    # ~0.03 mag precision.  Wider priors blow up proposal efficiency.
    "M_B": (-19.7, -18.8),
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
class DataProductSpec:
    """Machine-readable public data product tied to a registry entry."""

    product_type: str
    role: str
    url: str
    format: str
    description: str
    columns: tuple[str, ...] = field(default_factory=tuple)
    rows: int | None = None
    sha256: str | None = None
    # Path (relative to the backend/ root) of a vendored copy whose sha256 is the
    # one pinned above.  Set when `url` points at a directory/landing page rather
    # than a single machine-readable file (e.g. CC/RSD), so loaders read the
    # local file and the digest check is meaningful instead of hashing an HTML
    # index.
    local_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompressedLikelihoodSpec:
    """Small published Gaussian summary likelihood.

    This is deliberately not a prose conclusion.  It is a data vector,
    covariance matrix, and precise source locator that can be combined by
    the phase-1 analytic runner while full external likelihood packages
    remain out of process.
    """

    parameters: tuple[str, ...]
    mean: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]
    source_locator: str
    approximation: str
    units: dict[str, str] = field(default_factory=dict)

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
    execution_mode: ExecutionMode = "config_only"
    data_products: tuple[DataProductSpec, ...] = field(default_factory=tuple)
    compressed_likelihood: CompressedLikelihoodSpec | None = None
    research_roles: tuple[str, ...] = field(default_factory=tuple)
    execution_level: str | None = None
    independence_group: str | None = None
    known_overlap: tuple[str, ...] = field(default_factory=tuple)
    claimable_parameters: tuple[str, ...] = field(default_factory=tuple)
    recommended_combinations: tuple[str, ...] = field(default_factory=tuple)
    do_not_combine_with: tuple[str, ...] = field(default_factory=tuple)
    # Redshift coverage (z_min, z_max) of this dataset's actual measurements.
    # None when the probe has no discrete-z coverage interval: H0 priors (z≈0),
    # CMB primary/compressed (z*≈1090), CMB-lensing kernels, cosmic-shear
    # tomographic kernels, compressed σ8 cluster priors. Surfaced to the LLM by
    # list_cosmology_datasets / load_cosmology_data_product so that asking to
    # "report X at z=N" beyond this range is recognisable as ΛCDM extrapolation
    # rather than a data constraint.
    z_coverage: tuple[float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["citations"] = [citation.to_dict() for citation in self.citations]
        result["covariance"] = self.covariance.to_dict()
        result["data_products"] = [product.to_dict() for product in self.data_products]
        if self.compressed_likelihood is not None:
            result["compressed_likelihood"] = self.compressed_likelihood.to_dict()
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
WL_MODELS: tuple[CosmologyModel, ...] = ALL_MODELS


RUNNER_PARAMETER_PRIORS: dict[str, tuple[float, float]] = {
    "H0": (50.0, 90.0),
    "omegam": (0.05, 0.6),
    "rd": (130.0, 170.0),
    "sigma8": (0.4, 1.2),
    "S8": (0.4, 1.2),
    # M6 (2026-05-18): Pantheon+SH0ES nuisance — SN Ia absolute magnitude.
    # Narrow prior [-19.7, -18.8] (vs the wider DEFAULT_BUILDER_PRIORS) keeps
    # the importance sampler proposal efficiency from collapsing; the data
    # constrains M_B to ~0.03 mag, so ±0.5 is already very generous.
    "M_B": (-19.7, -18.8),
    # Dark-energy equation of state. Bounds chosen to keep numerical
    # stability of (1+z)^(3(1+w0+wa)) over z ≤ 3 while admitting the
    # phantom-crossing region that DESI DR1 hinted at.
    "w": (-2.5, -0.2),
    "w0": (-2.5, -0.2),
    "wa": (-3.0, 2.0),
}

# Primary-CMB sampled parameters for the external-cobaya plik_lite path ONLY.
# Kept SEPARATE from RUNNER_PARAMETER_PRIORS so they never leak into the in-process
# compressed/geometric parameter order: _compressed_parameter_order reads only
# RUNNER_PARAMETER_PRIORS, and putting ombh2 there silently un-blocked the BBN-only
# compressed selection (bbn_ombh2_schoeneberg24, whose spec is just ('ombh2',)).
# _sanitize_runner_priors merges both dicts for the cobaya path. Flat OUTER bounds;
# the narrow Gaussian priors on tau / A_planck (COBAYA_GAUSSIAN_PRIORS) live inside
# these. H0 is shared and stays in RUNNER_PARAMETER_PRIORS.
CMB_PARAMETER_PRIORS: dict[str, tuple[float, float]] = {
    "ombh2": (0.019, 0.025),
    "omch2": (0.10, 0.14),
    "ns": (0.92, 1.00),
    "As": (1.8e-9, 2.4e-9),
    "tau": (0.02, 0.10),
    "A_planck": (0.98, 1.02),
    # Sum of neutrino masses [eV], flat — the standard wide prior of
    # Planck-style mnu extensions (oscillation floor ~0.06 eV is left to the
    # data; CAMB runs with num_massive_neutrinos=1 for *_mnu models). Lives
    # here (NOT in RUNNER_PARAMETER_PRIORS) so the in-process compressed
    # path cannot silently pick it up — its kernels do not respond to mnu.
    "mnu": (0.0, 5.0),
    # Curvature density Omega_k, flat prior — the standard curved-CMB range
    # (Planck-style omk extensions). Same placement rationale as mnu: the
    # in-process distance kernels are flat-only, so omegak must not leak
    # into the compressed path's sampled axes.
    "omegak": (-0.3, 0.3),
}


# ── S8 as a derived quantity (1B, 2026-05-29) ────────────────────────────────
# S8 ≡ σ8 · √(Ωm / 0.3) is *defined* by σ8 and Ωm; it is NOT an independent
# degree of freedom.  Whenever the sampled parameter set contains both σ8 and
# Ωm we drop S8 from the sampled axes and instead compute it per posterior
# sample, applying any dataset's S8 Gaussian on that derived value.  When σ8
# and Ωm are not both sampled (e.g. KiDS/DES/HSC/ACT alone, which only report
# S8) there is nothing to be inconsistent with, so S8 stays a directly sampled
# measurement exactly as before.
S8_PIVOT_OMEGAM = 0.3


def _s8_is_derived(parameter_order: list[str]) -> bool:
    return "sigma8" in parameter_order and "omegam" in parameter_order


def _derived_s8_from_samples(samples: np.ndarray, parameter_order: list[str]) -> np.ndarray:
    sigma8 = samples[:, parameter_order.index("sigma8")]
    omegam = samples[:, parameter_order.index("omegam")]
    return sigma8 * np.sqrt(omegam / S8_PIVOT_OMEGAM)


def _drop_derived_s8(order: list[str]) -> list[str]:
    """Remove S8 from a sampled parameter order when it is derivable from the
    co-sampled σ8 and Ωm (so the sampler never explores an S8 that violates the
    σ8/Ωm relation)."""
    if "S8" in order and "sigma8" in order and "omegam" in order:
        return [param for param in order if param != "S8"]
    return order


def _s8_gaussian_constraints(
    entries: list[CosmologyDatasetEntry],
) -> list[tuple[float, float]]:
    """(mean, sigma) for every compressed spec that carries an S8 row — applied
    on the derived S8 when σ8/Ωm are sampled."""
    out: list[tuple[float, float]] = []
    for entry in entries:
        spec = entry.compressed_likelihood
        if spec is None or "S8" not in spec.parameters:
            continue
        idx = list(spec.parameters).index("S8")
        var = float(np.asarray(spec.covariance, dtype=float)[idx][idx])
        if var > 0:
            out.append((float(np.asarray(spec.mean, dtype=float)[idx]), math.sqrt(var)))
    return out


C_LIGHT_KM_S = 299792.458
