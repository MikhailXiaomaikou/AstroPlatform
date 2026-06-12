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
import io
import json
import logging
import math
import os
import pathlib
from functools import lru_cache
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np

from app.services.cosmology_mcmc import DEFAULT_PRIORS

logger = logging.getLogger(__name__)

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

# Pantheon+SH0ES diagonal compressed preliminary summary.  This is registered
# as a fast phase-1 executable SN constraint so research matrices can run
# deterministically.  The full 1701-SN covariance chi² runner remains available
# only behind PANTHEON_PLUS_FULL_CHI2_ENABLED because it is too slow for default
# multi-cell chat workflows.
_PANTHEON_PLUS_COMPRESSED_MEAN: tuple[float, float, float] = (73.04, 0.334, -19.253)
_PANTHEON_PLUS_COMPRESSED_COV: tuple[tuple[float, float, float], ...] = (
    (1.04 ** 2, 0.0, 0.0),
    (0.0, 0.018 ** 2, 0.0),
    (0.0, 0.0, 0.027 ** 2),
)
_PANTHEON_PLUS_COMPRESSED_NAMES: tuple[str, ...] = ("H0", "omegam", "M_B")


_REGISTRY: dict[str, CosmologyDatasetEntry] = {
    "desi_dr1_bao": CosmologyDatasetEntry(
        key="desi_dr1_bao",
        display_name="DESI DR1 BAO",
        version="DR1 2024 BAO likelihood",
        probe="bao",
        z_coverage=(0.295, 2.33),
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
            DatasetCitation(
                label="Adame et al. DESI Collaboration DR1 BAO cosmology",
                year=2024,
                arxiv="2404.03002",
            ),
        ),
        notes="Use as BAO-only or combined late-universe distance anchor; requires rd prior or CMB calibration.",
        do_not_combine_with=("desi_dr2_bao", "sdss_dr12_consensus_bao"),
        cobaya_likelihood="external:desilike.desi_dr1_bao",
        cosmosis_module="likelihood/bao/desi1-dr1/desi1_dr1.py",
        execution_mode="compressed_gaussian",
        data_products=(
            DataProductSpec(
                product_type="bao_measurement_vector",
                role="measurement_vector",
                url=(
                    "https://raw.githubusercontent.com/CobayaSampler/bao_data/master/"
                    "desi_2024_gaussian_bao_ALL_GCcomb_mean.txt"
                ),
                format="ASCII table",
                description="DESI DR1 combined BAO Gaussian mean vector.",
                columns=("z", "value", "quantity"),
                rows=12,
                sha256="dd2873a0b88459a491af3c0c0307ba059f62df9211d5b976760f310565a1be68",
            ),
            DataProductSpec(
                product_type="bao_covariance_matrix",
                role="covariance",
                url=(
                    "https://raw.githubusercontent.com/CobayaSampler/bao_data/master/"
                    "desi_2024_gaussian_bao_ALL_GCcomb_cov.txt"
                ),
                format="ASCII matrix",
                description="DESI DR1 combined BAO Gaussian covariance matrix.",
                rows=12,
                sha256="bbafa9074b51cf1a45e0d10e4f37db8c0e80a5d1d1788857abb7fc49fb21abcc",
            ),
            DataProductSpec(
                product_type="bao_bin_products",
                role="bin_level_measurements",
                url="https://github.com/CobayaSampler/bao_data/tree/master",
                format="ASCII mean/cov pairs",
                description=(
                    "Per-tracer DESI DR1 BAO mean/covariance files for BGS, LRG, "
                    "ELG, QSO, and Lyα bins."
                ),
                columns=("z", "value", "quantity"),
            ),
        ),
    ),
    "desi_dr2_bao": CosmologyDatasetEntry(
        key="desi_dr2_bao",
        display_name="DESI DR2 BAO",
        version="DR2 2025 BAO likelihood",
        probe="bao",
        z_coverage=(0.295, 2.33),
        status="external_likelihood",
        observables=("DM_over_rd", "DH_over_rd", "DV_over_rd"),
        units={"distance_ratios": "dimensionless", "redshift": "dimensionless"},
        applicable_models=BAO_MODELS,
        likelihood_family="gaussian_bao",
        covariance=CovarianceSpec(
            kind="block covariance",
            provided=True,
            description=(
                "DESI DR2 combined BAO Gaussian covariance (13x13) for "
                "BGS/LRG/ELG/QSO/LyA bins. The public file labels the quantities "
                "DM/DH/DV_over_rs; r_s(z_drag) is identical to r_d."
            ),
            url=(
                "https://raw.githubusercontent.com/CobayaSampler/bao_data/master/"
                "desi_bao_dr2/desi_gaussian_bao_ALL_GCcomb_cov.txt"
            ),
            format="DESI DR2 / CobayaSampler bao_data ASCII matrix",
        ),
        source_url="https://arxiv.org/abs/2503.14738",
        citations=(
            DatasetCitation(
                label="DESI Collaboration 2025 DR2 BAO measurements",
                year=2025,
                arxiv="2503.14738",
            ),
            DatasetCitation(
                label="DESI Collaboration 2025 DR2 cosmological constraints",
                year=2025,
                arxiv="2503.14739",
            ),
        ),
        notes=(
            "DESI DR2 (2025) supersedes DR1 as the primary late-universe BAO "
            "distance anchor; it drove the w0waCDM dark-energy preference. Use as "
            "BAO-only or combined; requires an rd prior or CMB calibration."
        ),
        do_not_combine_with=("desi_dr1_bao", "sdss_dr12_consensus_bao"),
        cobaya_likelihood="external:desilike.desi_dr2_bao",
        cosmosis_module="likelihood/bao/desi-dr2/desi_dr2.py",
        execution_mode="compressed_gaussian",
        recommended_combinations=("planck2018_compressed", "bbn_ombh2_schoeneberg24"),
        data_products=(
            DataProductSpec(
                product_type="bao_measurement_vector",
                role="measurement_vector",
                url=(
                    "https://raw.githubusercontent.com/CobayaSampler/bao_data/master/"
                    "desi_bao_dr2/desi_gaussian_bao_ALL_GCcomb_mean.txt"
                ),
                format="ASCII table",
                description="DESI DR2 combined BAO Gaussian mean vector (13 rows).",
                columns=("z", "value", "quantity"),
                rows=13,
                sha256="9ac154ab583ce759c0f7eef3c978c7c70a6ead2d18774caceadf1a350a640585",
            ),
            DataProductSpec(
                product_type="bao_covariance_matrix",
                role="covariance",
                url=(
                    "https://raw.githubusercontent.com/CobayaSampler/bao_data/master/"
                    "desi_bao_dr2/desi_gaussian_bao_ALL_GCcomb_cov.txt"
                ),
                format="ASCII matrix",
                description="DESI DR2 combined BAO Gaussian covariance matrix (13x13).",
                rows=13,
                sha256="252a143274c8a07c78694c119617d36594f6d7965d00319ca611c6ffb886e509",
            ),
            DataProductSpec(
                product_type="bao_bin_products",
                role="bin_level_measurements",
                url="https://github.com/CobayaSampler/bao_data/tree/master/desi_bao_dr2",
                format="ASCII mean/cov pairs",
                description=(
                    "Per-tracer DESI DR2 BAO mean/covariance files for BGS, LRG "
                    "(two z bins), LRG+ELG, ELG, QSO, and LyA."
                ),
                columns=("z", "value", "quantity"),
            ),
        ),
    ),
    "sdss_6df_bao": CosmologyDatasetEntry(
        key="sdss_6df_bao",
        display_name="6dFGS + SDSS MGS low-z BAO",
        version="6dFGS (2011) + SDSS MGS (2015) D_V/r_d, Aubourg+ 2015 compilation",
        probe="bao",
        z_coverage=(0.106, 0.15),
        status="external_likelihood",
        observables=("DV_over_rd",),
        units={"distance_ratios": "dimensionless", "redshift": "dimensionless"},
        applicable_models=BAO_MODELS,
        likelihood_family="bao_mixed_gaussian_table",
        covariance=CovarianceSpec(
            kind="mixed: Gaussian (6dFGS) + full non-Gaussian chi2(alpha) table (MGS)",
            provided=True,
            description=(
                "6dFGS z=0.106 stays the Aubourg+2015 compilation Gaussian "
                "(D_V/r_d = 3.047 +/- 0.137). SDSS MGS z=0.15 is evaluated from "
                "the FULL released chi2(alpha) table (Ross+2015; the same "
                "399-point sdss_MGS_prob.txt cobaya's bao.sdss_dr7_mgs uses, "
                "alpha = (D_V/r_d)/4.29720761315 over [0.8005, 1.1985]) — the "
                "previous 4.470 +/- 0.17 Gaussian was an approximation of this "
                "non-Gaussian likelihood (2026-06-12 upgrade)."
            ),
            url="https://github.com/CobayaSampler/bao_data",
            format="Gaussian point + chi2(alpha) lookup table",
        ),
        source_url="https://arxiv.org/abs/1411.1074",
        citations=(
            DatasetCitation(label="Beutler et al. 6dFGS BAO", year=2011, arxiv="1106.3366"),
            DatasetCitation(label="Ross et al. SDSS DR7 MGS BAO", year=2015, arxiv="1409.3242"),
            DatasetCitation(label="Aubourg et al. cosmological implications compilation", year=2015, arxiv="1411.1074"),
        ),
        notes=(
            "Pre-DESI low-z BAO anchor: only the two points (6dFGS z=0.106, "
            "SDSS MGS z=0.15) are sourced and executed in-process. MGS runs on "
            "the released non-Gaussian chi2(alpha) table (sha256-pinned, "
            "numerically identical spline convention to cobaya); 6dFGS remains "
            "a literature-typed Gaussian — its release IS a single number. "
            "execution_mode 'compressed_gaussian' names the in-process "
            "compressed channel, not the MGS half's statistics (which are "
            "non-Gaussian). Does NOT include the BOSS/eBOSS DR16 "
            "intermediate-z bins — use desi_dr1_bao for z>0.15 BAO."
        ),
        cobaya_likelihood="external:bao.sdss_6df_legacy",
        cosmosis_module="likelihood/bao/sdss_dr16_6df/sdss_6df_bao.py",
        execution_mode="compressed_gaussian",
        data_products=(
            DataProductSpec(
                product_type="bao_alpha_chi2_table",
                role="mgs_alpha_chi2_table",
                url="https://github.com/CobayaSampler/bao_data",
                format="sdss_MGS_prob.txt (399 chi2 values over alpha in [0.8005, 1.1985])",
                description=(
                    "SDSS DR7 MGS full BAO likelihood: chi2 as a function of the "
                    "dilation parameter alpha = (D_V/r_d)/4.29720761315."
                ),
                rows=399,
                local_path="data/cosmology/sdss_6df_bao/sdss_MGS_prob.txt",
                sha256="c252e18fefc69b76e5918852944739b440c8fbbedffd4477cb72f532627de4db",
            ),
        ),
    ),
    # ── PART AI Phase 5: RSD f·σ8 multi-z compilation (Alam+ 2021) ──
    # eBOSS DR16 cosmology paper (arXiv:2007.08991) reports growth-rate
    # measurements f·σ8 at 7 redshift bins from 6dFGS / BOSS LOWZ+CMASS
    # / eBOSS LRG+ELG+QSO+Lyα. Independent of BAO distance ratios on
    # the same survey — registers separately so users can run BAO-only,
    # RSD-only, or BAO+RSD joint analyses.
    "eboss_dr16_rsd": CosmologyDatasetEntry(
        key="eboss_dr16_rsd",
        display_name="eBOSS DR16 + BOSS RSD f·σ8 (SDSS lineage)",
        version="Alam+ 2021 Table III RSD-only fσ8 (6 SDSS bins: MGS / BOSS×2 / eBOSS LRG·ELG·QSO; diagonal)",
        probe="rsd",
        # fσ8 coverage ends at z=1.48 (eBOSS QSO): the Lyα sample at z=2.33 does
        # NOT report a growth-rate measurement (Alam+2021 Fig.1), so the earlier
        # (0.15, 2.33) overstated the fσ8 reach.
        z_coverage=(0.15, 1.48),
        # Executable in-process via the dedicated fσ8 growth χ² path (1A); the
        # "external_likelihood" label follows the desi_dr1_bao convention (full
        # external likelihood is higher fidelity), NOT "cannot run in-process".
        status="external_likelihood",
        observables=("f_sigma8",),
        units={"f_sigma8": "dimensionless", "redshift": "dimensionless"},
        applicable_models=BAO_MODELS,
        likelihood_family="gaussian_rsd",
        covariance=CovarianceSpec(
            kind="diagonal covariance (6 SDSS z-bins)",
            provided=True,
            description=(
                "RSD-only f·σ8(z) at 6 SDSS redshift bins covering "
                "0.15 ≤ z ≤ 1.48 (MGS / BOSS×2 / eBOSS LRG·ELG·QSO; Lyα at "
                "z=2.33 reports no growth rate). Diagonal covariance per "
                "Alam+2021 Table III note a (per-tracer Gaussian, inter-bin "
                "correlations ignored). Together with BAO distance ratios this "
                "constrains the σ8 growth history independent of weak-lensing "
                "1+z snapshots."
            ),
            url="https://svn.sdss.org/public/data/eboss/DR16cosmo/tags/v1_0_1/",
            format="SDSS/eBOSS DR16 RSD likelihood data products",
        ),
        source_url="https://svn.sdss.org/public/data/eboss/DR16cosmo/tags/v1_0_1/",
        citations=(
            DatasetCitation(
                label="Beutler et al. 6dFGS RSD",
                year=2012, arxiv="1204.4725",
            ),
            DatasetCitation(
                label="Alam et al. BOSS DR12 RSD consensus",
                year=2017, arxiv="1607.03155",
                doi="10.1093/mnras/stx721",
            ),
            DatasetCitation(
                label="Bautista et al. eBOSS LRG RSD",
                year=2021, arxiv="2007.08993",
            ),
            DatasetCitation(
                label="de Mattia et al. eBOSS ELG RSD",
                year=2021, arxiv="2007.09008",
            ),
            DatasetCitation(
                label="Hou et al. eBOSS QSO RSD",
                year=2021, arxiv="2007.08998",
            ),
            DatasetCitation(
                label="du Mas des Bourboux et al. eBOSS Lyα BAO+RSD",
                year=2020, arxiv="2007.08995",
            ),
            DatasetCitation(
                label="Alam et al. eBOSS DR16 cosmology summary",
                year=2021, arxiv="2007.08991",
                doi="10.1103/PhysRevD.103.083533",
            ),
        ),
        notes=(
            "Executable in-process: 6 RSD-only fσ8 points at z = 0.15, 0.38, "
            "0.51, 0.70, 0.85, 1.48 (Alam+2021 Table III, SDSS-only — 6dFGS "
            "excluded, Lyα reports no fσ8). Predicted as fσ8(z)=f(z)·σ8·D(z)/D(0) "
            "with the Linder γ-index growth kernel; diagonal covariance (Table "
            "III note a treats per-tracer errors as Gaussian, correlations "
            "ignored). Tests whether σ8 growth history matches ΛCDM — third axis "
            "of σ8 tension cross-check alongside cosmic shear (1+z snapshot σ8) "
            "and SPT clusters (M–T counting σ8). The γ-parametrisation is a "
            "~0.1–1% approximation vs a full Boltzmann growth solve; the broader "
            "6dFGS/Lyα citations document the RSD-compilation context. "
            "fσ8 is H0-independent, so this constrains the (Ωm, σ8) combination."
        ),
        data_products=(
            DataProductSpec(
                product_type="rsd_measurement_vector",
                role="rsd_measurement_vector",
                url="https://svn.sdss.org/public/data/eboss/DR16cosmo/tags/v1_0_1/",
                format="ASCII table (z, fsigma8, sigma)",
                description=(
                    "6 RSD-only fσ8 points (z, fσ8, σ) from Alam et al. 2021 "
                    "Table III; per-tracer diagonal errors (Table III note a, "
                    "correlations ignored). sha256 pins the committed artifact; "
                    "the full 6×6 inter-bin covariance is not a vendorable table."
                ),
                columns=("z", "fsigma8", "sigma"),
                rows=6,
                sha256="5d9bb1559ad9d2df4809e80b308681dea4b635ff7f64be39e316d8efe84b79c9",
                local_path="data/cosmology/eboss_dr16_rsd/fsigma8.txt",
            ),
        ),
        do_not_combine_with=("eboss_dr16_lrg_fsbao", "eboss_dr16_qso_fsbao", "sdss_dr12_consensus_bao"),
        cobaya_likelihood="external:rsd.eboss_dr16_alam21",
        cosmosis_module="likelihood/rsd/eboss_dr16/eboss_dr16_rsd.py",
        nuisance_parameters=(
            "rsd_systematics_LOWZ", "rsd_systematics_CMASS",
            "rsd_systematics_LRG", "rsd_systematics_ELG",
            "rsd_systematics_QSO",
        ),
        execution_mode="compressed_gaussian",
    ),
    "eboss_dr16_lrg_fsbao": CosmologyDatasetEntry(
        key="eboss_dr16_lrg_fsbao",
        display_name="eBOSS DR16 LRG FSBAO (D_M/r_s, D_H/r_s, fσ8)",
        version="SDSS DR16 BAO+RSD consensus LRG: BOSS z=0.38,0.51 + eBOSS z=0.698, joint (D_M/r_s,D_H/r_s,fσ8), full 9×9 covariance",
        probe="bao_rsd",
        z_coverage=(0.38, 0.698),
        status="external_likelihood",
        observables=("DM_over_rs", "DH_over_rs", "f_sigma8"),
        units={"DM_over_rs": "dimensionless", "DH_over_rs": "dimensionless", "f_sigma8": "dimensionless"},
        applicable_models=BAO_MODELS,
        likelihood_family="fsbao_gaussian",
        covariance=CovarianceSpec(
            kind="full covariance",
            provided=True,
            description=(
                "Joint (D_M/r_s, D_H/r_s, fσ8) at 3 LRG redshifts (BOSS z=0.38,0.51 + "
                "eBOSS z=0.698) with the FULL 9×9 distance+growth covariance from the SDSS "
                "DR16 release. Higher-fidelity companion to the fσ8-only diagonal entry "
                "'eboss_dr16_rsd'; the two share tracers and must not be co-added."
            ),
            url="https://github.com/CobayaSampler/bao_data",
            format="z value quantity table + N×N covtot",
        ),
        source_url="https://github.com/CobayaSampler/bao_data",
        citations=(
            DatasetCitation(label="Alam et al. eBOSS DR16 cosmological implications", year=2021, arxiv="2007.08991"),
            DatasetCitation(label="Bautista et al. eBOSS DR16 LRG BAO+RSD", year=2021, arxiv="2007.08993"),
            DatasetCitation(label="Gil-Marín et al. eBOSS DR16 LRG full-shape", year=2020, arxiv="2007.08994"),
        ),
        notes=(
            "9-element joint vector (D_M/r_s, D_H/r_s, fσ8 at z=0.38, 0.51, 0.698) with the "
            "released full covariance, executed in-process as a flat w0waCDM rᵀC⁻¹r χ² that "
            "predicts both BAO distance ratios and the fσ8 growth rate. Constrains (H0, Ωm, "
            "r_d, σ8). Do NOT co-add with 'eboss_dr16_rsd' (same tracers' fσ8) — double-counts. "
            "No survey overlap with DESI BAO. ELG (grid likelihood) and Lyα/MGS (BAO-only) are "
            "not part of this Gaussian FSBAO entry."
        ),
        data_products=(
            DataProductSpec(
                product_type="fsbao_measurement_vector",
                role="measurement_vector",
                url="https://raw.githubusercontent.com/CobayaSampler/bao_data/master/sdss_DR16_BAOplus_LRG_FSBAO_DMDHfs8.dat",
                format="ASCII (z, value, quantity)",
                description="SDSS DR16 LRG joint (D_M/r_s, D_H/r_s, fσ8) measurement vector, vendored verbatim.",
                columns=("z", "value", "quantity"),
                rows=9,
                sha256="a098ea4df320ac1c18a9404237a75ae26953e16403a20294beb1d9573be33c56",
                local_path="data/cosmology/eboss_dr16_lrg_fsbao/mean.txt",
            ),
            DataProductSpec(
                product_type="fsbao_covariance",
                role="covariance",
                url="https://raw.githubusercontent.com/CobayaSampler/bao_data/master/sdss_DR16_BAOplus_LRG_FSBAO_DMDHfs8_covtot.txt",
                format="ASCII 9×9 matrix",
                description="SDSS DR16 LRG full 9×9 distance+growth covariance (covtot), vendored verbatim.",
                columns=("cov_ij",),
                rows=9,
                sha256="409cabbf4ccf6993053427f5a34d52e6557f2429c17777267459471180e72f96",
                local_path="data/cosmology/eboss_dr16_lrg_fsbao/cov.txt",
            ),
        ),
        do_not_combine_with=("eboss_dr16_rsd", "sdss_dr12_consensus_bao"),
        cobaya_likelihood="external:fsbao.sdss_dr16_lrg",
        cosmosis_module="external:fsbao/sdss_dr16_lrg",
        execution_mode="compressed_gaussian",
    ),
    "eboss_dr16_qso_fsbao": CosmologyDatasetEntry(
        key="eboss_dr16_qso_fsbao",
        display_name="eBOSS DR16 QSO FSBAO (D_M/r_s, D_H/r_s, fσ8)",
        version="SDSS DR16 BAO+RSD consensus QSO: z=1.48, joint (D_M/r_s,D_H/r_s,fσ8), full 3×3 covariance",
        probe="bao_rsd",
        z_coverage=(1.48, 1.48),
        status="external_likelihood",
        observables=("DM_over_rs", "DH_over_rs", "f_sigma8"),
        units={"DM_over_rs": "dimensionless", "DH_over_rs": "dimensionless", "f_sigma8": "dimensionless"},
        applicable_models=BAO_MODELS,
        likelihood_family="fsbao_gaussian",
        covariance=CovarianceSpec(
            kind="full covariance",
            provided=True,
            description=(
                "Joint (D_M/r_s, D_H/r_s, fσ8) at the eBOSS QSO effective redshift z=1.48 with "
                "the FULL 3×3 distance+growth covariance from the SDSS DR16 release. Higher-"
                "fidelity companion to the fσ8-only diagonal entry 'eboss_dr16_rsd'."
            ),
            url="https://github.com/CobayaSampler/bao_data",
            format="z value quantity table + N×N covtot",
        ),
        source_url="https://github.com/CobayaSampler/bao_data",
        citations=(
            DatasetCitation(label="Alam et al. eBOSS DR16 cosmological implications", year=2021, arxiv="2007.08991"),
            DatasetCitation(label="Hou et al. eBOSS DR16 QSO BAO+RSD", year=2021, arxiv="2007.08998"),
            DatasetCitation(label="Neveux et al. eBOSS DR16 QSO full-shape", year=2020, arxiv="2007.08999"),
        ),
        notes=(
            "3-element joint vector (D_M/r_s, D_H/r_s, fσ8 at z=1.48) with the released full "
            "3×3 covariance, executed in-process as a flat w0waCDM rᵀC⁻¹r χ² predicting BAO "
            "distance ratios and the fσ8 growth rate. Constrains (H0, Ωm, r_d, σ8). Do NOT "
            "co-add with 'eboss_dr16_rsd' (same QSO fσ8) — double-counts. No DESI overlap."
        ),
        data_products=(
            DataProductSpec(
                product_type="fsbao_measurement_vector",
                role="measurement_vector",
                url="https://raw.githubusercontent.com/CobayaSampler/bao_data/master/sdss_DR16_BAOplus_QSO_FSBAO_DMDHfs8.dat",
                format="ASCII (z, value, quantity)",
                description="SDSS DR16 QSO joint (D_M/r_s, D_H/r_s, fσ8) measurement vector, vendored verbatim.",
                columns=("z", "value", "quantity"),
                rows=3,
                sha256="cddd6cbbca7dadc910a5e8742f1f2144c066cb347b8ba03ae0bd4876fa06d8ed",
                local_path="data/cosmology/eboss_dr16_qso_fsbao/mean.txt",
            ),
            DataProductSpec(
                product_type="fsbao_covariance",
                role="covariance",
                url="https://raw.githubusercontent.com/CobayaSampler/bao_data/master/sdss_DR16_BAOplus_QSO_FSBAO_DMDHfs8_covtot.txt",
                format="ASCII 3×3 matrix",
                description="SDSS DR16 QSO full 3×3 distance+growth covariance (covtot), vendored verbatim.",
                columns=("cov_ij",),
                rows=3,
                sha256="88f844447fb546792769cdf09b4df7b7a7f77a948f02ef371f54a6f7dddb3d41",
                local_path="data/cosmology/eboss_dr16_qso_fsbao/cov.txt",
            ),
        ),
        do_not_combine_with=("eboss_dr16_rsd",),
        cobaya_likelihood="external:fsbao.sdss_dr16_qso",
        cosmosis_module="external:fsbao/sdss_dr16_qso",
        execution_mode="compressed_gaussian",
    ),
    "sdss_dr12_consensus_bao": CosmologyDatasetEntry(
        key="sdss_dr12_consensus_bao",
        display_name="SDSS BOSS DR12 consensus BAO",
        version="BOSS DR12 consensus BAO (Alam et al. 2017): D_M, H at z=0.38/0.51/0.61, full 6×6 covariance",
        probe="bao",
        z_coverage=(0.38, 0.61),
        status="external_likelihood",
        observables=("DM_over_rs", "bao_Hz_rs"),
        units={
            # NOT the dimensionless DESI/eBOSS convention: the released values
            # are stored against the fiducial sound horizon rs_fid = 147.78 Mpc
            # (cobaya bao.sdss_dr12_consensus_bao).
            "DM_over_rs": "Mpc (D_M·rs_fid/r_d)",
            "bao_Hz_rs": "km/s/Mpc (H·r_d/rs_fid)",
        },
        applicable_models=BAO_MODELS,
        likelihood_family="bao_gaussian_rsfid",
        covariance=CovarianceSpec(
            kind="full covariance",
            provided=True,
            description=(
                "Joint (D_M·rs_fid/r_d, H·r_d/rs_fid) at z = 0.38, 0.51, 0.61 with the "
                "released FULL 6×6 covariance (BAO_consensus_covtot_dM_Hz) — the BAO-only "
                "consensus likelihood behind the Planck 2018 '+BAO' parameter columns."
            ),
            url="https://github.com/CobayaSampler/bao_data",
            format="z value quantity table + 6×6 covtot",
        ),
        source_url="https://github.com/CobayaSampler/bao_data",
        citations=(
            DatasetCitation(label="Alam et al. BOSS DR12 consensus cosmology", year=2017, arxiv="1607.03155"),
        ),
        notes=(
            "BAO-only DR12 consensus (no fσ8): 6-element joint vector with the released "
            "full covariance, executed in-process as a flat w0waCDM rᵀC⁻¹r χ² in the "
            "rs_fid = 147.78 Mpc storage convention. Constrains (H0, Ωm, r_d). Do NOT "
            "co-add with eBOSS DR16 LRG-based entries — the DR12 z=0.61 bin shares BOSS "
            "galaxies with the DR16 LRG sample (the official SDSS suite combines them "
            "only after dropping that bin). MGS (z=0.15) and 6dFGS are independent."
        ),
        data_products=(
            DataProductSpec(
                product_type="bao_measurement_vector",
                role="measurement_vector",
                url="https://raw.githubusercontent.com/CobayaSampler/bao_data/master/sdss_DR12Consensus_bao.dat",
                format="ASCII (z, value, quantity)",
                description="BOSS DR12 consensus BAO (D_M·rs_fid/r_d, H·r_d/rs_fid) vector, vendored verbatim.",
                columns=("z", "value", "quantity"),
                rows=6,
                sha256="fc43f1cd9c815bb58b09f4d8d1d272d2c4ec57e05e4893e2121c20dc08f4f862",
                local_path="data/cosmology/sdss_dr12_consensus_bao/mean.txt",
            ),
            DataProductSpec(
                product_type="bao_covariance_matrix",
                role="covariance",
                url="https://raw.githubusercontent.com/CobayaSampler/bao_data/master/BAO_consensus_covtot_dM_Hz.txt",
                format="ASCII 6×6 matrix",
                description="BOSS DR12 consensus BAO full 6×6 covariance (covtot), vendored verbatim.",
                columns=("cov_ij",),
                rows=6,
                sha256="05c04829c8edc117870efe809494593a23de6c35547f8b66760a5250804b65cf",
                local_path="data/cosmology/sdss_dr12_consensus_bao/cov.txt",
            ),
        ),
        do_not_combine_with=(
            "eboss_dr16_lrg_fsbao",
            "eboss_dr16_rsd",
            # BOSS DR12 and the DESI BGS/LRG bins overlap on the sky and in
            # redshift (0.295/0.51/0.706 vs 0.38/0.51/0.61); the DESI key
            # papers partition at z=0.6 rather than co-add, because the
            # cross-covariance is unquantified.
            "desi_dr1_bao",
            "desi_dr2_bao",
        ),
        cobaya_likelihood="bao.sdss_dr12_consensus_bao",
        cosmosis_module="likelihood/bao/sdss_dr12/sdss_dr12.py",
        execution_mode="compressed_gaussian",
    ),
    "pantheon_plus": CosmologyDatasetEntry(
        key="pantheon_plus",
        display_name="Pantheon+",
        version="Pantheon+SH0ES DataRelease 2022",
        probe="sn",
        z_coverage=(0.001, 2.26),
        status="ready",
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
            DatasetCitation(
                label="Riess et al. SH0ES calibration",
                year=2022,
                arxiv="2112.04510",
                doi="10.3847/2041-8213/ac5c5b",
            ),
        ),
        notes="Can be used with or without SH0ES calibration; keep H0 prior separate unless explicitly selected.",
        cobaya_likelihood="external:sn.pantheon_plus",
        cosmosis_module="Pantheon+_Data/5_COSMOLOGY/cosmosis_likelihoods",
        nuisance_parameters=("M_B",),
        execution_mode="compressed_gaussian",
        data_products=(
            DataProductSpec(
                product_type="sn_distance_modulus_table",
                role="data_table",
                url=(
                    "https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/main/"
                    "Pantheon%2B_Data/4_DISTANCES_AND_COVAR/Pantheon%2BSH0ES.dat"
                ),
                format="ASCII table",
                description="Pantheon+SH0ES supernova distance table.",
                columns=("CID", "zHD", "zCMB", "MU_SH0ES", "MU_SH0ES_ERR_DIAG"),
                rows=1701,
            ),
            DataProductSpec(
                product_type="sn_covariance_matrix",
                role="covariance",
                url=(
                    "https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/main/"
                    "Pantheon%2B_Data/4_DISTANCES_AND_COVAR/Pantheon%2BSH0ES_STAT%2BSYS.cov"
                ),
                format="ASCII packed covariance",
                description="Pantheon+SH0ES statistical plus systematic covariance matrix.",
                rows=1701,
            ),
            DataProductSpec(
                product_type="sn_covariance_matrix",
                role="statistical_covariance",
                url=(
                    "https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/main/"
                    "Pantheon%2B_Data/4_DISTANCES_AND_COVAR/Pantheon%2BSH0ES_STATONLY.cov"
                ),
                format="ASCII packed covariance",
                description="Pantheon+SH0ES statistical-only covariance matrix.",
                rows=1701,
            ),
            DataProductSpec(
                product_type="cosmosis_likelihood_code",
                role="likelihood_code",
                url=(
                    "https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/main/"
                    "Pantheon%2B_Data/5_COSMOLOGY/cosmosis_likelihoods/"
                    "Pantheon%2B_only_cosmosis_likelihood.py"
                ),
                format="Python / CosmoSIS module",
                description="Pantheon+-only CosmoSIS likelihood wrapper.",
            ),
            DataProductSpec(
                product_type="cosmosis_likelihood_code",
                role="likelihood_code",
                url=(
                    "https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/main/"
                    "Pantheon%2B_Data/5_COSMOLOGY/cosmosis_likelihoods/"
                    "Pantheon%2BSH0ES_cosmosis_likelihood.py"
                ),
                format="Python / CosmoSIS module",
                description="Pantheon+SH0ES CosmoSIS likelihood wrapper.",
            ),
            DataProductSpec(
                # Kept LAST so it is never the default product returned by
                # load_cosmology_data_product (no role): its local_path is a 20MB
                # binary blob that must not be parsed as a text table (code-review #1).
                product_type="sn_full_data_npz",
                role="sn_full_data_npz",
                url="https://github.com/PantheonPlusSH0ES/DataRelease",
                format="npz",
                description=(
                    "Vendored Pantheon+SH0ES 1701-SN bundle (z_hd, z_hel, mu, "
                    "mu_err_diag, full stat+sys covariance) the in-process χ² reads."
                ),
                columns=("z_hd", "z_hel", "mu", "mu_err_diag", "cov"),
                rows=1701,
                sha256="d6b3ed124fa038c02bdc4457f4f7aff8bf6e9f6b41e1257f530c90d7bd1f8cca",
                local_path="data/pantheon_plus_2022/data.npz",
            ),
        ),
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=_PANTHEON_PLUS_COMPRESSED_NAMES,
            mean=_PANTHEON_PLUS_COMPRESSED_MEAN,
            covariance=_PANTHEON_PLUS_COMPRESSED_COV,
            units={
                "H0": "km s^-1 Mpc^-1",
                "omegam": "dimensionless",
                "M_B": "mag",
            },
            source_locator=(
                "Pantheon+SH0ES 2022 cosmology summary compression "
                "(Brout et al. 2022; Riess et al. 2022 calibration branch)."
            ),
            approximation=(
                "Diagonal SN+SH0ES compressed preliminary summary for phase-1 "
                "research matrices; not the full Pantheon+ covariance likelihood."
            ),
        ),
        research_roles=("sn_distance_ladder", "late_universe_distance", "dark_energy_matrix"),
        execution_level="compressed_preliminary",
        independence_group="pantheon_plus_sn",
        claimable_parameters=("H0", "omegam", "M_B"),
        recommended_combinations=("desi_dr1_bao", "planck2018_compressed"),
        # The compressed Pantheon+ spec IS the SH0ES-calibrated branch (its H0 mean
        # 73.04 ± 1.04 is the Riess+2022 SH0ES value), so co-adding the standalone
        # SH0ES H0 prior double-counts the identical measurement and halves the H0
        # variance. Keep them as robustness alternatives, never a joint fit.
        do_not_combine_with=("des_sn5yr", "union3", "shoes_h0_riess22"),
    ),
    "des_sn5yr": CosmologyDatasetEntry(
        key="des_sn5yr",
        display_name="DES-SN 5YR",
        version="DES-SN5YR Release 1 / 2024 cosmology sample",
        probe="sn",
        z_coverage=(0.025, 1.13),
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
            DatasetCitation(
                label="DES-SN5YR data products",
                year=2024,
                arxiv="2406.05046",
                doi="10.5281/zenodo.12720778",
            ),
        ),
        notes=(
            "Photometrically classified DES SN sample; robustness partner for "
            "Pantheon+/Union3. Default fast path is a compressed SN-only flat-ΛCDM Ωm "
            "Gaussian (Ωm=0.352±0.017). The FULL 1829-SN distance-modulus vector + "
            "stat+sys covariance is vendored (sha256-pinned data.npz, built by "
            "scripts/fetch_des_sn5yr.py from the github tag-1.3 Vincenzi+2024 Legacy "
            "release) and runs in-process as a full-covariance χ² when "
            "DES_SN5YR_FULL_CHI2_ENABLED is set — that path can constrain the w0/wa "
            "dark-energy EoS. The χ² analytically marginalizes the SN absolute "
            "magnitude (no M_B/H0 nuisance), so it constrains Ωm (+w0/wa) only."
        ),
        cobaya_likelihood="external:sn.des_sn5yr",
        cosmosis_module="external:DES-SN5YR",
        nuisance_parameters=("M_B",),
        do_not_combine_with=("pantheon_plus", "union3"),
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("omegam",),
            mean=(0.352,),
            covariance=((0.017 ** 2,),),
            units={"omegam": "dimensionless"},
            source_locator="DES Collaboration (Abbott et al.) 2024 (arXiv:2401.02929) Table 2, Flat-ΛCDM SN-only / no external priors: Ωm = 0.352 ± 0.017.",
            approximation="1D SN-only flat-ΛCDM Ωm Gaussian; NOT the full 1829-SN distance-modulus + covariance likelihood (external).",
        ),
        data_products=(
            DataProductSpec(
                product_type="sn_full_data_npz",
                role="sn_full_data_npz",
                url="https://github.com/des-science/DES-SN5YR",
                format="npz",
                description=(
                    "Vendored DES-SN5YR 1829-SN bundle (z_hd, z_hel, mu, mu_err_diag, "
                    "full stat+sys covariance C_sys+diag(MUERR²)) the in-process χ² reads. "
                    "Built from the github tag-1.3 Vincenzi+2024 Legacy release by "
                    "scripts/fetch_des_sn5yr.py."
                ),
                columns=("z_hd", "z_hel", "mu", "mu_err_diag", "cov"),
                rows=1829,
                sha256="8f01090ecd8a1ce719c3d892781d9031972eddd97e3f75ca40d3090b9676a529",
                local_path="data/des_sn5yr/data.npz",
            ),
        ),
    ),
    "union3": CosmologyDatasetEntry(
        key="union3",
        display_name="Union3 / UNITY1.5",
        version="Union3 2023 arXiv release",
        probe="sn",
        z_coverage=(0.01, 2.26),
        status="external_likelihood",
        observables=("z", "distance_modulus", "mag_covariance"),
        units={"z": "dimensionless", "mu": "mag"},
        applicable_models=SN_MODELS,
        likelihood_family="sn_distance_modulus",
        covariance=CovarianceSpec(
            kind="full 22x22 binned-mag covariance",
            provided=True,
            description=(
                "Union3/UNITY1.5 22-bin binned distance moduli + full magnitude "
                "covariance (the same Union3/lcparam_full.txt + mag_covmat.txt "
                "cobaya's sn.union3 reads). The chi2 analytically marginalizes "
                "the constant magnitude offset — identical to cobaya's "
                "_marginalize_abs_mag projection — so H0 and M_B drop out "
                "(2026-06-12 upgrade from the 1D compressed Omega_m Gaussian)."
            ),
            url="https://github.com/CobayaSampler/sn_data",
            format="lcparam (zcmb zhel mb) + dense covariance matrix",
        ),
        source_url="https://arxiv.org/abs/2311.12098",
        citations=(
            DatasetCitation(label="Rubin et al. Union3/UNITY1.5", year=2023, arxiv="2311.12098"),
        ),
        notes=(
            "Independent SN robustness branch; do not mix with Pantheon+ as if "
            "independent. Runs in-process on the FULL 22-bin binned distance-"
            "modulus vector + covariance (offset-marginalized chi2, constrains "
            "Omega_m + the w0/wa DE shape; no M_B/H0 nuisance) — always on, "
            "unlike DES-SN5YR's env-gated 1829-SN path, because 22x22 has no "
            "per-sample cost worth gating. The compressed Omega_m Gaussian "
            "below is retained as the published 1D anchor (oracle table), not "
            "an execution path. execution_mode 'compressed_gaussian' names the "
            "in-process channel, not the statistics."
        ),
        cobaya_likelihood="external:sn.union3",
        cosmosis_module="external:Union3/UNITY1.5",
        nuisance_parameters=("M_B",),
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("omegam",),
            mean=(0.356,),
            covariance=((0.027 ** 2,),),
            units={"omegam": "dimensionless"},
            source_locator="Rubin et al. 2023 (arXiv:2311.12098) Table 9, Flat-ΛCDM SN-only: Ωm = 0.356 (+0.028/-0.026); symmetrized σ = 0.027.",
            approximation="1D SN-only flat-ΛCDM Ωm Gaussian — published anchor for the in-process full 22-bin likelihood (which reproduces Ωm=0.356 at its chi2 minimum).",
        ),
        data_products=(
            DataProductSpec(
                product_type="sn_binned_distance_moduli",
                role="measurement_vector",
                url="https://github.com/CobayaSampler/sn_data",
                format="lcparam text table (#name zcmb zhel dz mb ...)",
                description=(
                    "Union3 22-bin binned distance moduli (mb column; arbitrary "
                    "constant normalization — the chi2 marginalizes the offset)."
                ),
                # Positional preview labels MUST match the file's leading
                # tokens (name zcmb zhel dz mb ...) — a 3-name tuple here once
                # served zhel under the label 'mb' (2026-06-12 review).
                columns=("name", "zcmb", "zhel", "dz", "mb"),
                rows=22,
                sha256="a840fe71c606bda11b869dbfcacc21c0199a5dc393f3790d10a7b58de97deae7",
                local_path="data/union3/lcparam_full.txt",
            ),
            DataProductSpec(
                product_type="sn_mag_covariance",
                role="covariance",
                url="https://github.com/CobayaSampler/sn_data",
                format="first line = n, then n*n values row-major",
                description="Union3 22x22 binned-magnitude covariance matrix.",
                rows=22,
                sha256="64c79abd24bf5154bc1e38ad0c031e31dd6247cdcc5ca930829698169809a146",
                local_path="data/union3/mag_covmat.txt",
            ),
        ),
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
            DatasetCitation(
                label="Planck 2018 final release",
                year=2018,
                arxiv="1807.06209",
                doi="10.1051/0004-6361/201833910",
                bibcode="2020A&A...641A...6P",
            ),
            DatasetCitation(
                label="Planck Collaboration VI 2020",
                year=2020,
                doi="10.1051/0004-6361/201833910",
                bibcode="2020A&A...641A...6P",
            ),
            DatasetCitation(label="Chen, Huang & Wang distance priors", year=2019, arxiv="1808.05724", doi="10.1088/1475-7516/2019/02/028"),
        ),
        notes=(
            "Compressed CMB prior, not a replacement for the full Planck likelihood "
            "in extended models. The (H0, Omega_m, sigma8, S8) summary uses a "
            "diagonal covariance, but as of 1B (2026-05-29) the runner samples only "
            "(H0, Omega_m, sigma8) and computes S8 == sigma8 * (Omega_m/0.3)^0.5 as "
            "a derived quantity, applying the S8 row on that derived value, so the "
            "joint posterior is internally consistent and the WL S8 likelihood pulls "
            "on the sigma8/Omega_m combination as it should. A real non-diagonal "
            "Planck covariance from the public chains remains a follow-up -- see "
            "scripts/fetch_planck2018_compressed.py. Treat these as compressed-"
            "preliminary, not full-likelihood, constraints."
        ),
        cobaya_likelihood="external:planck_2018_distance_prior",
        cosmosis_module="external:planck2018_distance_priors",
        execution_mode="compressed_gaussian",
        data_products=(
            DataProductSpec(
                product_type="planck_likelihood_archive",
                role="likelihood_code",
                url=(
                    "https://wiki.cosmos.esa.int/planck-legacy-archive/index.php/"
                    "CMB_spectrum_%26_Likelihood_Code"
                ),
                format="PLA likelihood code/data archive",
                description="Planck Legacy Archive page for the public CMB spectrum and likelihood code.",
            ),
            DataProductSpec(
                product_type="compressed_distance_prior",
                role="compressed_prior_table",
                url="https://arxiv.org/abs/1808.05724",
                format="paper table",
                description=(
                    "Planck final-release distance-prior mean vector and covariance source "
                    "used by the phase-1 compressed runner."
                ),
                columns=("R", "l_A", "ombh2", "ns"),
            ),
        ),
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("H0", "omegam", "sigma8", "S8"),
            mean=(67.36, 0.3153, 0.8111, 0.832),
            covariance=(
                (0.54**2, 0.0, 0.0, 0.0),
                (0.0, 0.0073**2, 0.0, 0.0),
                (0.0, 0.0, 0.0060**2, 0.0),
                (0.0, 0.0, 0.0, 0.013**2),
            ),
            units={
                "H0": "km s^-1 Mpc^-1",
                "omegam": "dimensionless",
                "sigma8": "dimensionless",
                "S8": "dimensionless",
            },
            source_locator="Planck Collaboration VI 2020 Table 2 baseline; S8 derived summary.",
            approximation=(
                "Diagonal compressed ΛCDM posterior summary; not the full Planck "
                "likelihood. The S8 row is kept for its published 1σ but the runner "
                "treats S8 == sigma8 * (Omega_m/0.3)^0.5 as a *derived* quantity "
                "(1B, 2026-05-29): the sampler never explores an independent S8, so "
                "the σ8/Ωm/S8 joint is now internally consistent. Real non-diagonal "
                "chain covariance remains a follow-up "
                "(scripts/fetch_planck2018_compressed.py produces the covariance)."
            ),
        ),
    ),
    "planck_2018_highl_TTTEEE_lite": CosmologyDatasetEntry(
        key="planck_2018_highl_TTTEEE_lite",
        display_name="Planck 2018 high-l plik_lite TT/TE/EE",
        version="Planck 2018 plik_lite_v22 (foreground-marginalized, native)",
        probe="cmb",
        status="external_likelihood",
        observables=("C_ell_TT", "C_ell_TE", "C_ell_EE"),
        units={"C_ell": "muK^2"},
        applicable_models=CMB_MODELS,
        likelihood_family="cmb_primary",
        covariance=CovarianceSpec(
            kind="binned bandpower covariance",
            provided=True,
            description=(
                "Planck 2018 plik_lite high-l (l~30-2508) foreground-marginalized "
                "TT/TE/EE binned bandpowers (613) + their full 613x613 covariance. "
                "One calibration nuisance, A_planck. Evaluated in-process via "
                "cobaya's PURE-PYTHON native likelihood (no clik) over a CAMB "
                "theory spectrum."
            ),
            url="https://pla.esac.esa.int/pla/#cosmology",
            format="cobaya planck_native_data plik_lite_2018_AL (cl_cmb + c_matrix)",
        ),
        source_url="https://arxiv.org/abs/1907.12875",
        citations=(
            DatasetCitation(
                label="Aghanim et al. Planck 2018 V. CMB power spectra and likelihoods",
                year=2020,
                arxiv="1907.12875",
            ),
            DatasetCitation(
                label="Aghanim et al. Planck 2018 VI. Cosmological parameters",
                year=2020,
                arxiv="1807.06209",
            ),
        ),
        notes=(
            "Primary high-l CMB TT/TE/EE — the first non-compressed CMB likelihood "
            "in the registry (planck2018_compressed keeps only the R/l_A/ombh2 "
            "distance priors). Runs as a real cobaya MCMC over a CAMB spectrum "
            "(minutes), gated behind EXTERNAL_COBAYA_ENABLED; the data is vendored "
            "+ sha256-pinned under data/cobaya_packages (clik-free native plik_lite, "
            "~3 MB). High-l alone does not constrain tau, so it is sampled with the "
            "Planck lowE Gaussian prior tau=0.0544+/-0.0073 (A_planck=1.0+/-0.0025) "
            "UNLESS planck_2018_lowl_EE is also selected — then tau is a flat-prior "
            "sampled parameter constrained by the real low-l EE likelihood. Combine "
            "with planck_2018_lowl_TT + planck_2018_lowl_EE for the full clik-free "
            "Planck 2018 primary stack. Reproduces chi2~584.5 / dof~0.96 at the "
            "Planck 2018 base-LCDM best fit."
        ),
        cobaya_likelihood="external:planck_2018_highl_plik.TTTEEE_lite_native",
        cosmosis_module="external:planck_2018_highl_plik.TTTEEE_lite_native",
        execution_mode="external_cobaya",
        data_products=(
            DataProductSpec(
                product_type="cmb_binned_bandpowers",
                role="measurement_vector",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="plik_lite_v22 cl_cmb_plik_v22.dat (613 binned TT/TE/EE bandpowers)",
                description="Foreground-marginalized binned CMB bandpowers (the data vector).",
                rows=613,
                sha256="dac0d9d493213e77c940a10a968cf0da3c5730bae60e1356c4cd8bcff96377ff",
            ),
            DataProductSpec(
                product_type="cmb_bandpower_covariance",
                role="covariance",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="plik_lite_v22 c_matrix_plik_v22.dat (613x613)",
                description="Full plik_lite bandpower covariance matrix.",
                rows=613,
                sha256="ad90378c50bd67841764179c90ae6711fa4317c649966ab2b0712143b31e0a32",
            ),
            # The likelihood also reads the binning definition + the .dataset
            # ini at init (cobaya planck_pliklite.py) — editing any of these
            # silently changes chi2, so they are pinned like the data vector.
            DataProductSpec(
                product_type="cmb_binning_definition",
                role="binning_blmin",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="plik_lite_v22 blmin.dat",
                description="Per-bin lower multipole edges of the bandpower binning.",
                sha256="325b351cbf8f694556bb13e98f285344e8d66811bb8eef18bcdcf1626518719d",
            ),
            DataProductSpec(
                product_type="cmb_binning_definition",
                role="binning_blmax",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="plik_lite_v22 blmax.dat",
                description="Per-bin upper multipole edges of the bandpower binning.",
                sha256="c28ade0fa5270c7e87ba07bdcb68aef8783b132b352bfaa36c04d17694ab4014",
            ),
            DataProductSpec(
                product_type="cmb_binning_definition",
                role="binning_weights",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="plik_lite_v22 bweight.dat",
                description="Per-l weights used to bin the theory spectrum.",
                sha256="8afcbd8bad769e2de96bacd80177e6543f96b2b406e6c2da1fd0d26718c9e415",
            ),
            DataProductSpec(
                product_type="cmb_dataset_ini",
                role="dataset_ini",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="plik_lite_v22.dataset",
                description=(
                    "Dataset ini controlling use_cl/nbintt/nbinte/nbinee/lmax/"
                    "bin_lmin_offset/calibration_param."
                ),
                sha256="0dc7318de1b1b8fe0ad79e6bdb13135eae0190c9678e52a0a4f5120ceafa64ca",
            ),
        ),
    ),
    "planck_2018_lowl_TT": CosmologyDatasetEntry(
        key="planck_2018_lowl_TT",
        display_name="Planck 2018 low-l Commander TT",
        version="Planck 2018 Commander low-l TT (gaussianized Blackwell-Rao, native)",
        probe="cmb",
        status="external_likelihood",
        observables=("C_ell_TT",),
        units={"C_ell": "muK^2"},
        applicable_models=CMB_MODELS,
        likelihood_family="cmb_primary",
        covariance=CovarianceSpec(
            kind="gaussianized Blackwell-Rao",
            provided=True,
            description=(
                "Planck 2018 Commander low-l TT (l=2-29): gaussianized "
                "Blackwell-Rao likelihood — mean vector + covariance + two cl2x "
                "spline tables mapping C_l to the gaussianized variable. Evaluated "
                "via cobaya's PURE-PYTHON native likelihood (no clik) over a CAMB "
                "theory spectrum."
            ),
            url="https://pla.esac.esa.int/pla/#cosmology",
            format="cobaya planck_native_data planck_2018_lowT_native (mu/cov/cl2x)",
        ),
        source_url="https://arxiv.org/abs/1907.12875",
        citations=(
            DatasetCitation(
                label="Aghanim et al. Planck 2018 V. CMB power spectra and likelihoods",
                year=2020,
                arxiv="1907.12875",
            ),
            DatasetCitation(
                label="Aghanim et al. Planck 2018 VI. Cosmological parameters",
                year=2020,
                arxiv="1807.06209",
            ),
        ),
        notes=(
            "Low-l temperature (Commander, l=2-29) — together with "
            "planck_2018_highl_TTTEEE_lite and planck_2018_lowl_EE this completes "
            "the clik-free Planck 2018 primary likelihood stack. Gated behind "
            "EXTERNAL_COBAYA_ENABLED; data vendored + sha256-pinned under "
            "data/cobaya_packages (~14 MB). Reproduces -2lnL = 23.44 at the "
            "Planck 2018 base-LCDM best fit (paper value 23.4, arXiv:1907.12875)."
        ),
        cobaya_likelihood="external:planck_2018_lowl.TT",
        cosmosis_module="external:planck_2018_lowl.TT",
        execution_mode="external_cobaya",
        recommended_combinations=("planck_2018_highl_TTTEEE_lite", "planck_2018_lowl_EE"),
        data_products=(
            DataProductSpec(
                product_type="cmb_lowl_gaussianized_mean",
                role="measurement_vector",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="planck_2018_lowT_native mu.txt",
                description="Commander gaussianized Blackwell-Rao mean vector (l=2-29).",
                sha256="aa2ffbcb2d26c2881553de428aba729422390f3bb04a20b7ee9ea3865aee579f",
            ),
            DataProductSpec(
                product_type="cmb_lowl_gaussianized_sigma",
                role="sigma_vector",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="planck_2018_lowT_native mu_sigma.txt",
                description="Per-l sigma of the gaussianized variable.",
                sha256="3c396bb6997c2746f5da0736c3d95eb6c748887e10613e37c481851a4fed6996",
            ),
            DataProductSpec(
                product_type="cmb_lowl_gaussianized_covariance",
                role="covariance",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="planck_2018_lowT_native cov.txt",
                description="Covariance of the gaussianized variable (l=2-29).",
                sha256="f3bedefd70c80388a4bda13faffc2cd803e59437216ca842e9df85aaa8c119d4",
            ),
            DataProductSpec(
                product_type="cmb_lowl_br_spline_table",
                role="br_spline_table_1",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="planck_2018_lowT_native cl2x_1.txt",
                description="Blackwell-Rao gaussianization spline table (part 1).",
                sha256="9c681e02595b14a3a934a32d3cfa93be7fba1968083a59326828834e37ac83b5",
            ),
            DataProductSpec(
                product_type="cmb_lowl_br_spline_table",
                role="br_spline_table_2",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="planck_2018_lowT_native cl2x_2.txt",
                description="Blackwell-Rao gaussianization spline table (part 2).",
                sha256="46714e527337832604f42eade620277910e7cc8d62af0150d2eb2873676ebb05",
            ),
        ),
    ),
    "planck_2018_lowl_EE": CosmologyDatasetEntry(
        key="planck_2018_lowl_EE",
        display_name="Planck 2018 low-l SimAll EE",
        version="Planck 2018 SimAll low-l EE (probability table, native)",
        probe="cmb",
        status="external_likelihood",
        observables=("C_ell_EE",),
        units={"C_ell": "muK^2"},
        applicable_models=CMB_MODELS,
        likelihood_family="cmb_primary",
        covariance=CovarianceSpec(
            kind="non-Gaussian probability table",
            provided=True,
            description=(
                "Planck 2018 SimAll low-l EE (l=2-29): tabulated per-l probability "
                "P(C_l) lookup, converted from the public clik "
                "simall_100x143_offlike5_EE_Aplanck_B. No Gaussian covariance — "
                "the full non-Gaussian likelihood surface IS the data product."
            ),
            url="https://pla.esac.esa.int/pla/#cosmology",
            format="cobaya planck_native_data planck_2018_lowE_native (prob_table)",
        ),
        source_url="https://arxiv.org/abs/1907.12875",
        citations=(
            DatasetCitation(
                label="Aghanim et al. Planck 2018 V. CMB power spectra and likelihoods",
                year=2020,
                arxiv="1907.12875",
            ),
            DatasetCitation(
                label="Aghanim et al. Planck 2018 VI. Cosmological parameters",
                year=2020,
                arxiv="1807.06209",
            ),
        ),
        notes=(
            "Low-l EE polarization (SimAll, l=2-29) — the measurement that "
            "actually constrains the reionization optical depth tau. When this "
            "entry is selected the runner samples tau with its FLAT prior instead "
            "of the lowE Gaussian pin tau=0.0544+/-0.0073 (using both would count "
            "the same data twice). Gated behind EXTERNAL_COBAYA_ENABLED; data "
            "vendored + sha256-pinned (~2 MB). Reproduces -2lnL = 395.52 at the "
            "Planck 2018 base-LCDM best fit (paper value 395.7, arXiv:1907.12875)."
        ),
        cobaya_likelihood="external:planck_2018_lowl.EE",
        cosmosis_module="external:planck_2018_lowl.EE",
        execution_mode="external_cobaya",
        recommended_combinations=("planck_2018_highl_TTTEEE_lite", "planck_2018_lowl_TT"),
        data_products=(
            DataProductSpec(
                product_type="cmb_lowl_probability_table",
                role="probability_table",
                url="https://github.com/CobayaSampler/planck_native_data",
                format="planck_2018_lowE_native prob_table.txt",
                description=(
                    "SimAll EE per-l tabulated probability P(C_l) — the full "
                    "non-Gaussian low-l EE likelihood surface."
                ),
                sha256="7efa150e762313f7920b7ae2b4f3cf3c7d3fdaaa6b1ae257b60b2c75279fe7b3",
            ),
        ),
    ),
    "planck_2018_lensing": CosmologyDatasetEntry(
        key="planck_2018_lensing",
        display_name="Planck 2018 CMB lensing (native)",
        version="Planck 2018 smica consext8 lensing bandpowers (CMBlikes native)",
        probe="cmb_lensing",
        status="external_likelihood",
        observables=("C_L_phiphi",),
        units={"C_L": "dimensionless"},
        applicable_models=CMB_MODELS,
        likelihood_family="cmb_lensing",
        covariance=CovarianceSpec(
            kind="binned bandpower covariance",
            provided=True,
            description=(
                "Planck 2018 lensing reconstruction (smica T+P, conservative "
                "consext8 range): 9 binned C_L^phiphi bandpowers + 9x9 "
                "covariance + per-bin window functions + linear fiducial "
                "correction. Evaluated via cobaya's PURE-PYTHON CMBlikes "
                "native likelihood (no clik) over a CAMB lensed spectrum."
            ),
            url="https://pla.esac.esa.int/pla/#cosmology",
            format="cobaya planck_supp_data_and_covmats lensing/2018 (.dataset + bandpowers + cov + windows)",
        ),
        source_url="https://arxiv.org/abs/1807.06210",
        citations=(
            DatasetCitation(
                label="Aghanim et al. Planck 2018 VIII. Gravitational lensing",
                year=2020,
                arxiv="1807.06210",
            ),
            DatasetCitation(
                label="Aghanim et al. Planck 2018 VI. Cosmological parameters",
                year=2020,
                arxiv="1807.06209",
            ),
        ),
        notes=(
            "Completes the clik-free Planck 2018 stack: TT/TE/EE (plik_lite) + "
            "low-l TT/EE + this lensing likelihood. Consumes the shared "
            "A_planck calibration (planck_calib defaults). Data vendored + "
            "sha256-pinned (~1.3 MB incl. both window sets — bin windows are "
            "chi2-load-bearing, pinned via directory aggregate digests). "
            "Reproduces -2lnL = 8.82 over 9 bins at the Planck 2018 base-LCDM "
            "best fit (chi2/dof ~ 0.98, matching the published goodness of "
            "fit). NOT independent of planck_pr4_lensing (same Planck maps; "
            "PR4 is the NPIPE reprocessing) — do not co-add."
        ),
        cobaya_likelihood="external:planck_2018_lensing.native",
        cosmosis_module="external:planck_2018_lensing.native",
        execution_mode="external_cobaya",
        recommended_combinations=(
            "planck_2018_highl_TTTEEE_lite", "planck_2018_lowl_TT", "planck_2018_lowl_EE",
        ),
        do_not_combine_with=("planck_pr4_lensing",),
        data_products=(
            DataProductSpec(
                product_type="cmb_lensing_dataset_ini",
                role="dataset_ini",
                url="https://github.com/CobayaSampler/planck_supp_data_and_covmats",
                format="smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8.dataset",
                description="CMBlikes dataset ini (bins, ranges, window wiring, calibration).",
                sha256="7bc37c8c17191c857425c0b1213c2df66cc99360a831009e8be765da4fe8d51c",
            ),
            DataProductSpec(
                product_type="cmb_lensing_bandpowers",
                role="measurement_vector",
                url="https://github.com/CobayaSampler/planck_supp_data_and_covmats",
                format="..._consext8_bandpowers.dat (9 C_L^phiphi bandpowers)",
                description="Binned lensing-potential bandpowers (the data vector).",
                rows=9,
                sha256="0113871c95b026dbf544c21f3c0cd667bea25ad146dddb93db4189cff660a6f0",
            ),
            DataProductSpec(
                product_type="cmb_lensing_covariance",
                role="covariance",
                url="https://github.com/CobayaSampler/planck_supp_data_and_covmats",
                format="..._consext8_cov.dat (9x9)",
                description="Bandpower covariance matrix.",
                rows=9,
                sha256="fdd19b43dacd3c65a3d092442c291401a3497cc4fddf9ce08bb098d5a428efc0",
            ),
            DataProductSpec(
                product_type="cmb_lensing_linear_correction",
                role="fiducial_correction",
                url="https://github.com/CobayaSampler/planck_supp_data_and_covmats",
                format="..._consext8_lensing_fiducial_correction.dat",
                description="Fiducial linear correction for the N1/normalization dependence.",
                sha256="d186f5cc43556f8a4178a275fc73142b69b7ba1976fea383bfb5763f4e133cd6",
            ),
            DataProductSpec(
                product_type="cmb_calibration_paramnames",
                role="calibration_paramnames",
                url="https://github.com/CobayaSampler/planck_supp_data_and_covmats",
                format="planck_calib.paramnames",
                description="Declares the shared A_planck calibration nuisance.",
                sha256="bc0155dd4026afff8e100a84ff3b3aae3c121b57071312a5cf19c47b79c6489b",
            ),
            # Window sets: per-bin window functions mapping the theory C_L onto
            # the binned bandpowers — chi2-load-bearing (the plik_lite bweight
            # lesson). Pinned as DIRECTORY AGGREGATE digests: sha256 over the
            # sorted (filename + bytes) of every file in the directory; the
            # runner's _verify_pinned_cmb_data recomputes the same aggregate.
            DataProductSpec(
                product_type="cmb_lensing_bin_windows",
                role="bin_windows_dir",
                url="https://github.com/CobayaSampler/planck_supp_data_and_covmats",
                format="..._consext8_window/window1..9.dat (directory aggregate)",
                description="Per-bin bandpower window functions (9 files).",
                rows=9,
                sha256="caaac4cb1fd1d24e5a968333e70449df1662ba347a6c90fd836d3f64a82cfc1b",
            ),
            DataProductSpec(
                product_type="cmb_lensing_linear_correction_windows",
                role="lin_windows_dir",
                url="https://github.com/CobayaSampler/planck_supp_data_and_covmats",
                format="..._consext8_lens_delta_window/window1..9.dat (directory aggregate)",
                description="Per-bin linear-correction window functions (9 files).",
                rows=9,
                sha256="d7bffafc35d460df1fe964017e61d9f59152741ecfb662e10c54ebb6c2391a61",
            ),
        ),
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
            DatasetCitation(label="Madhavacheril et al. ACT DR6 lensing", year=2024, arxiv="2304.05203"),
            DatasetCitation(label="Carron, Mirmelstein & Lewis likelihood method", year=2022, arxiv="2206.07773"),
        ),
        notes="Requires ACT likelihood data and external code; pair carefully with Planck CMB to avoid double-counting lensing.",
        cobaya_likelihood="external:act_dr6_lenslike.ACTDR6LensLike",
        cosmosis_module="external:act_dr6_lenslike",
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("H0", "sigma8", "S8"),
            mean=(68.1, 0.812, 0.831),
            covariance=(
                (1.0**2, 0.0, 0.0),
                (0.0, 0.013**2, 0.0),
                (0.0, 0.0, 0.023**2),
            ),
            units={
                "H0": "km s^-1 Mpc^-1",
                "sigma8": "dimensionless",
                "S8": "dimensionless",
            },
            source_locator="Madhavacheril et al. ACT DR6 lensing abstract joint ACT+Planck-lensing summary.",
            approximation=(
                "Diagonal ACT+Planck CMB-lensing compressed summary. Use for preliminary "
                "consistency checks only; do not combine as statistically independent from Planck lensing."
            ),
        ),
    ),
    "planck_pr4_lensing": CosmologyDatasetEntry(
        key="planck_pr4_lensing",
        display_name="Planck PR4 (NPIPE) CMB lensing",
        version="Planck PR4/NPIPE lensing likelihood (Carron+ 2022)",
        probe="cmb_lensing",
        status="external_likelihood",
        observables=("C_L_kappakappa",),
        units={"C_L": "dimensionless"},
        applicable_models=CMB_MODELS,
        likelihood_family="cmb_lensing",
        covariance=CovarianceSpec(
            kind="bandpower covariance",
            provided=True,
            description=(
                "Planck PR4 (NPIPE) CMB-lensing bandpower likelihood. Headline base-LCDM "
                "constraint sigma8 * Omega_m^0.25 = 0.599 +/- 0.016 (CMB lensing + weak BAO/BBN priors)."
            ),
            url="https://github.com/carronj/planck_PR4_lensing",
            format="planck_PR4_lensing likelihood package",
        ),
        source_url="https://arxiv.org/abs/2206.07773",
        citations=(
            DatasetCitation(
                label="Carron, Mirmelstein & Lewis CMB lensing from Planck PR4",
                year=2022,
                arxiv="2206.07773",
            ),
        ),
        notes=(
            "Planck PR4/NPIPE lensing reconstruction (~slightly more data and tighter "
            "than 2018 PR3 lensing). Complementary to act_dr6_lensing but NOT statistically "
            "independent of it; do not co-add naively. Full bandpower evaluation needs the "
            "external planck_PR4_lensing package (translation pending); the published "
            "sigma8*Omega_m^0.25 = 0.599 +/- 0.016 summary is recorded in the covariance description."
        ),
        cobaya_likelihood="external:planck_PR4_lensing",
        cosmosis_module="external:planck_PR4_lensing",
        execution_mode="external_cobaya",
        # Same Planck maps as planck_2018_lensing (PR4 = NPIPE reprocessing).
        do_not_combine_with=("planck_2018_lensing",),
    ),
    "kids1000_wl": CosmologyDatasetEntry(
        key="kids1000_wl",
        display_name="KiDS-1000 cosmic shear",
        version="KiDS-1000 cosmic-shear likelihood / 2-point statistics",
        probe="weak_lensing",
        status="external_likelihood",
        observables=("xi_plus", "xi_minus", "S8", "Omega_m"),
        units={"xi": "dimensionless", "S8": "dimensionless", "Omega_m": "dimensionless"},
        applicable_models=WL_MODELS,
        likelihood_family="cosmic_shear_2pt",
        covariance=CovarianceSpec(
            kind="tomographic two-point covariance",
            provided=True,
            description="KiDS-1000 tomographic cosmic-shear two-point covariance and likelihood products.",
            url="https://arxiv.org/abs/2007.15633",
            format="KiDS-1000 public likelihood products / paper tables",
        ),
        source_url="https://arxiv.org/abs/2007.15633",
        citations=(
            DatasetCitation(label="Asgari et al. KiDS-1000 cosmic shear", year=2021, arxiv="2007.15633"),
        ),
        notes=(
            "Galaxy weak-lensing comparison branch for S8 consistency checks. "
            "Requires nuisance treatment for intrinsic alignments, shear calibration, and redshift calibration."
        ),
        cobaya_likelihood="external:kids1000",
        cosmosis_module="external:kids1000",
        nuisance_parameters=("A_IA", "m_bias", "delta_z"),
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("S8",),
            mean=(0.759,),
            covariance=((0.0225**2,),),
            units={"S8": "dimensionless"},
            source_locator="Asgari et al. KiDS-1000 cosmic shear abstract/fiducial S8 summary.",
            approximation="Symmetrized 68% S8-only compressed summary; nuisance parameters marginalized in source analysis.",
        ),
    ),
    "des_y3_3x2pt": CosmologyDatasetEntry(
        key="des_y3_3x2pt",
        display_name="DES Y3 3x2pt weak lensing + clustering",
        version="DES Year 3 3x2pt cosmology likelihood",
        probe="weak_lensing",
        status="external_likelihood",
        observables=("xi_plus", "xi_minus", "gamma_t", "w_theta", "S8", "Omega_m"),
        units={"correlations": "dimensionless", "S8": "dimensionless", "Omega_m": "dimensionless"},
        applicable_models=WL_MODELS,
        likelihood_family="3x2pt",
        covariance=CovarianceSpec(
            kind="3x2pt covariance",
            provided=True,
            description="DES Y3 cosmic shear, galaxy-galaxy lensing, and clustering covariance.",
            url="https://des.ncsa.illinois.edu/releases/y3a2/Y3key-products",
            format="DES Y3 likelihood / CosmoSIS data products",
        ),
        source_url="https://des.ncsa.illinois.edu/releases/y3a2/Y3key-products",
        citations=(
            DatasetCitation(label="DES Collaboration Year 3 3x2pt cosmology", year=2022, arxiv="2105.13549"),
        ),
        notes=(
            "Galaxy weak-lensing comparison branch for S8 consistency checks; "
            "do not treat as independent of DES-SN because it is a different probe from the same survey."
        ),
        cobaya_likelihood="external:des_y3_3x2pt",
        cosmosis_module="external:des-y3-3x2pt",
        nuisance_parameters=("A_IA", "m_bias", "delta_z", "galaxy_bias"),
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("S8",),
            mean=(0.776,),
            covariance=((0.017**2,),),
            units={"S8": "dimensionless"},
            source_locator="DES Collaboration Year 3 3x2pt ΛCDM S8 summary.",
            approximation="S8-only compressed summary; full DES Y3 nuisance/covariance is external.",
        ),
    ),
    "hsc_y1_cosmic_shear": CosmologyDatasetEntry(
        key="hsc_y1_cosmic_shear",
        display_name="HSC Y1 cosmic shear",
        version="HSC SSP first-year cosmic-shear likelihood",
        probe="weak_lensing",
        status="external_likelihood",
        observables=("xi_plus", "xi_minus", "S8", "Omega_m"),
        units={"xi": "dimensionless", "S8": "dimensionless", "Omega_m": "dimensionless"},
        applicable_models=WL_MODELS,
        likelihood_family="cosmic_shear_2pt",
        covariance=CovarianceSpec(
            kind="mock-derived tomographic covariance",
            provided=True,
            description="HSC first-year tomographic cosmic-shear two-point covariance from realistic mocks.",
            url="https://arxiv.org/abs/1906.06041",
            format="HSC Y1 cosmic-shear likelihood / paper tables",
        ),
        source_url="https://arxiv.org/abs/1906.06041",
        citations=(
            DatasetCitation(label="Hamana et al. HSC Y1 cosmic shear", year=2020, arxiv="1906.06041"),
        ),
        notes=(
            "Galaxy weak-lensing comparison branch for S8 consistency checks. "
            "Useful for ACT DR6-style KiDS/DES/HSC comparison, but requires HSC-specific nuisance settings."
        ),
        cobaya_likelihood="external:hsc_y1_cosmic_shear",
        cosmosis_module="external:hsc-y1-cosmic-shear",
        nuisance_parameters=("A_IA", "m_bias", "delta_z"),
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("S8", "omegam"),
            mean=(0.823, 0.332),
            covariance=(
                (0.030**2, 0.0),
                (0.0, 0.073**2),
            ),
            units={"S8": "dimensionless", "omegam": "dimensionless"},
            source_locator="Hamana et al. HSC Y1 cosmic shear abstract ΛCDM summary.",
            approximation="Symmetrized S8/Omega_m compressed summary; covariance off-diagonal unavailable here.",
        ),
    ),
    "cosmic_chronometers": CosmologyDatasetEntry(
        key="cosmic_chronometers",
        display_name="Cosmic chronometers H(z)",
        version="Gómez-Valent & Amendola 2018 compilation (31 differential-age H(z), diagonal covariance)",
        probe="hz",
        z_coverage=(0.07, 1.965),
        # Executable in-process via the dedicated diagonal H(z) χ² path (like
        # desi_dr1_bao); "external_likelihood" because the higher-fidelity full
        # Moresco+2020 systematic-covariance version remains an external package.
        status="external_likelihood",
        observables=("z", "H_z", "H_z_covariance"),
        units={"z": "dimensionless", "H_z": "km s^-1 Mpc^-1"},
        applicable_models=ALL_MODELS,
        likelihood_family="hz_gaussian",
        covariance=CovarianceSpec(
            kind="diagonal covariance",
            provided=True,
            description=(
                "31 differential-age H(z) points with diagonal covariance "
                "(D_ij = σ_i² δ_ij) per Gómez-Valent & Amendola 2018 Table 1. "
                "The fuller Moresco et al. 2020 systematic covariance is a "
                "documented refinement not applied in this phase-1 runner."
            ),
            url="https://cluster.difa.unibo.it/astro/CC_data/",
            format="H(z) table (diagonal errors)",
        ),
        source_url="https://cluster.difa.unibo.it/astro/CC_data/",
        citations=(
            DatasetCitation(
                label="Gómez-Valent & Amendola CC H(z) compilation",
                year=2018, arxiv="1802.01505", doi="10.1088/1475-7516/2018/04/051",
            ),
            DatasetCitation(label="Moresco et al. covariance systematics", year=2020, arxiv="2003.07362"),
            DatasetCitation(label="Jiao et al. LEGA-C chronometers", year=2022, arxiv="2205.05701"),
        ),
        notes=(
            "31 model-independent H(z) measurements from differential ages of "
            "passive galaxies (z 0.07–1.965), executable in-process as a flat "
            "w0waCDM H(z)=H0·E(z) χ² with diagonal covariance. Independent "
            "expansion-rate probe; combine with BAO/SN/CMB. Diagonal-only: the "
            "Moresco+2020 systematic covariance would inflate errors, so treat "
            "as preliminary-grade rather than full-systematics publication."
        ),
        data_products=(
            DataProductSpec(
                product_type="hz_measurement_vector",
                role="hz_measurement_vector",
                url="https://cluster.difa.unibo.it/astro/CC_data/",
                format="ASCII table (z, H, sigma_H)",
                description=(
                    "31 differential-age H(z) points transcribed from "
                    "Gómez-Valent & Amendola 2018 Table 1. No single machine-"
                    "readable upstream release exists, so the sha256 pins the "
                    "committed artifact (drift guard); covariance is diagonal."
                ),
                columns=("z", "H_z", "sigma_H"),
                rows=31,
                sha256="2793de7a2a5ab29a45545fefe35988ca90a369516d64c4605d02a1907fdc8fad",
                local_path="data/cosmology/cosmic_chronometers/hz.txt",
            ),
        ),
        do_not_combine_with=("cosmic_chronometers_moresco20",),
        cobaya_likelihood="external:cosmic_chronometers",
        cosmosis_module="external:hz/cosmic_chronometers",
        execution_mode="compressed_gaussian",
    ),
    "cosmic_chronometers_moresco20": CosmologyDatasetEntry(
        key="cosmic_chronometers_moresco20",
        display_name="Cosmic chronometers H(z) — Moresco 2020 full covariance",
        version="Moresco et al. 2012/2015/2016 BC03 H(z) (15 pts) with the Moresco et al. 2020 full systematic covariance",
        probe="hz",
        z_coverage=(0.1791, 1.965),
        # Executable in-process via the dedicated full-covariance H(z) χ² path.
        # Distinct from the GA2018 31-point diagonal compilation
        # ("cosmic_chronometers"); this is the smaller Moresco-team BC03 subset
        # for which the Moresco+2020 systematic covariance is actually defined.
        status="external_likelihood",
        observables=("z", "H_z", "H_z_covariance"),
        units={"z": "dimensionless", "H_z": "km s^-1 Mpc^-1"},
        applicable_models=ALL_MODELS,
        likelihood_family="hz_gaussian",
        covariance=CovarianceSpec(
            kind="full covariance",
            provided=True,
            description=(
                "15 BC03 cosmic-chronometer H(z) points (Moresco 2012/2015/2016) with the "
                "FULL Moresco et al. 2020 covariance: diagonal statistical+metallicity plus "
                "fully-correlated IMF and SPS-model ('one-of-others') systematic terms. "
                "Reproduced from the vendored, sha256-pinned raw source files by "
                "scripts/gen_moresco20_cc_covariance.py (faithful port of the official "
                "gitlab.com/mmoresco/CCcovariance recipe), NOT hand-typed."
            ),
            url="https://gitlab.com/mmoresco/CCcovariance",
            format="z, H, sigma_H raw tables + reproduced NxN covariance",
        ),
        source_url="https://gitlab.com/mmoresco/CCcovariance",
        citations=(
            DatasetCitation(
                label="Moresco et al. cosmic-chronometer full covariance",
                year=2020, arxiv="2003.07362", doi="10.3847/1538-4357/ab9eb0",
            ),
            DatasetCitation(label="Moresco et al. H(z) at z<1.1", year=2012, arxiv="1201.3609"),
            DatasetCitation(label="Moresco H(z) at z~2", year=2015, arxiv="1503.01116"),
            DatasetCitation(label="Moresco et al. 6% H(z) measurement", year=2016, arxiv="1601.01701"),
        ),
        notes=(
            "15 differential-age H(z) measurements from the Moresco team's BC03 analysis "
            "(z 0.179–1.965), executed in-process as a flat w0waCDM H(z)=H0·E(z) χ² with the "
            "FULL Moresco+2020 systematic covariance (cov_fidelity='full'). This is the "
            "higher-fidelity, narrower companion to the GA2018 31-point diagonal entry "
            "'cosmic_chronometers'. Do NOT co-add with that entry: the 15 BC03 points are a "
            "subset of the 31, so combining them double-counts. Covariance is reproduced "
            "deterministically from the sha256-pinned raw files via the committed generator "
            "script; only diag+IMF+model('one-of-others') are summed, matching the upstream "
            "notebook's final covariance (avoids double-counting the model systematic)."
        ),
        data_products=(
            DataProductSpec(
                product_type="hz_measurement_vector",
                role="hz_measurement_vector",
                url="https://gitlab.com/mmoresco/CCcovariance/-/raw/master/data/HzTable_MM_BC03.dat",
                format="ASCII table (z, H, sigma_H)",
                description="15-point BC03 H(z) vector reproduced into mean.txt (z, H, quantity).",
                columns=("z", "H_z", "quantity"),
                rows=15,
                sha256="95fa695ac256527d2ddb35ff72059dd38a3ccb59af18d54b549c67c05379acc8",
                local_path="data/cosmology/cosmic_chronometers_moresco20/mean.txt",
            ),
            DataProductSpec(
                product_type="hz_covariance",
                role="covariance",
                url="https://gitlab.com/mmoresco/CCcovariance",
                format="ASCII 15x15 matrix",
                description=(
                    "Full 15x15 Moresco+2020 systematic covariance, reproduced from the "
                    "pinned raw source files by scripts/gen_moresco20_cc_covariance.py."
                ),
                columns=("cov_ij",),
                rows=15,
                sha256="f6315a93531477601a6165aac9f875380f1a2737d23e16fd05563853717c1f68",
                local_path="data/cosmology/cosmic_chronometers_moresco20/cov.txt",
            ),
            DataProductSpec(
                product_type="hz_raw_source",
                role="raw_hz_table",
                url="https://gitlab.com/mmoresco/CCcovariance/-/raw/master/data/HzTable_MM_BC03.dat",
                format="ASCII (z, Hz, errHz, stat, met, reference)",
                description="Raw upstream BC03 H(z) table (provenance source for mean.txt).",
                columns=("z", "Hz", "errHz", "stat_contr", "met_contr", "reference"),
                rows=15,
                sha256="32ce92caf251cb60a7a837c71f1856bea2b44fa5c1041f85410d11cb8164da98",
                local_path="data/cosmology/cosmic_chronometers_moresco20/HzTable_MM_BC03.dat",
            ),
            DataProductSpec(
                product_type="hz_systematics_source",
                role="raw_systematics_table",
                url="https://gitlab.com/mmoresco/CCcovariance/-/raw/master/data/data_MM20.dat",
                format="ASCII (z, IMF, stlib, mod, mod_ooo per-cent contributions)",
                description="Raw upstream per-cent systematic contributions (provenance source for cov.txt).",
                columns=("z", "IMF", "stlib", "mod", "mod_ooo"),
                rows=29,
                sha256="577ac2f346e346fe7cf94daa7b7000c05d04ebc8a029cda31e0d8643b956a485",
                local_path="data/cosmology/cosmic_chronometers_moresco20/data_MM20.dat",
            ),
        ),
        do_not_combine_with=("cosmic_chronometers",),
        cobaya_likelihood="external:cosmic_chronometers_moresco20",
        cosmosis_module="external:hz/cosmic_chronometers_moresco20",
        execution_mode="compressed_gaussian",
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
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("H0",),
            mean=(73.04,),
            covariance=((1.04**2,),),
            units={"H0": "km s^-1 Mpc^-1"},
            source_locator="Riess et al. 2022 SH0ES H0 prior.",
            approximation="Scalar Gaussian H0 prior; not an Ωm/S8 constraint.",
        ),
    ),
    # ── PART AI follow-up: spec papers #12-#15 (4 H0-ladder alternates besides
    # SH0ES + SPT-3G CMB) ──────────────────────────────────────────────────
    "trgb_h0_freedman19": CosmologyDatasetEntry(
        key="trgb_h0_freedman19",
        display_name="TRGB H0 prior (Freedman+ 2019)",
        version="Freedman et al. 2019 TRGB Carnegie-Chicago Hubble Program",
        probe="h0_prior",
        status="ready",
        observables=("H0",),
        units={"H0": "km s^-1 Mpc^-1"},
        applicable_models=H0_MODELS,
        likelihood_family="gaussian_prior",
        covariance=CovarianceSpec(
            kind="1D gaussian variance",
            provided=True,
            description="H0 = 69.8 +/- 1.9 km/s/Mpc (TRGB tip-of-RGB calibration).",
            url="https://doi.org/10.3847/1538-4357/ab2f73",
            format="scalar Gaussian prior",
        ),
        source_url="https://doi.org/10.3847/1538-4357/ab2f73",
        citations=(
            DatasetCitation(
                label="Freedman et al. TRGB H0 (CCHP)",
                year=2019,
                arxiv="1907.05922",
                doi="10.3847/1538-4357/ab2f73",
            ),
            # Context-only comparison anchors referenced by the notes below.
            # These citations make prose like "alternative to SH0ES" and
            # "compared with Planck 2018" provenance-visible without causing
            # the TRGB-only likelihood run to combine those datasets.
            DatasetCitation(
                label="Riess et al. SH0ES comparison anchor",
                year=2022,
                arxiv="2112.04510",
                doi="10.3847/2041-8213/ac5c5b",
            ),
            DatasetCitation(
                label="Planck 2018 CMB comparison anchor",
                year=2018,
                arxiv="1807.06209",
                doi="10.1051/0004-6361/201833910",
                bibcode="2020A&A...641A...6P",
            ),
        ),
        notes=(
            "Independent distance-ladder anchor (TRGB tip-of-RGB) that sits "
            "between SH0ES (Cepheid+SN Ia) and Planck. Use as a SH0ES "
            "alternate / cross-check; do NOT combine with SH0ES naively "
            "without modelling the shared SN Ia rung."
        ),
        cobaya_likelihood="gaussian:H0=69.8,sigma=1.9",
        cosmosis_module="prior H0 = gaussian 69.8 1.9",
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("H0",),
            mean=(69.8,),
            covariance=((1.9**2,),),
            units={"H0": "km s^-1 Mpc^-1"},
            source_locator="Freedman et al. 2019 TRGB H0 prior.",
            approximation="Scalar Gaussian H0 prior; mid-rung distance ladder anchor.",
        ),
        do_not_combine_with=("shoes_h0_riess22",),
    ),
    "cchp_h0_freedman24": CosmologyDatasetEntry(
        key="cchp_h0_freedman24",
        display_name="CCHP HST+JWST TRGB H0 prior (Freedman+ 2024)",
        version="Freedman et al. 2024/2025 CCHP HST+JWST TRGB H0 (ApJ 985, 203)",
        probe="h0_prior",
        status="ready",
        observables=("H0",),
        units={"H0": "km s^-1 Mpc^-1"},
        applicable_models=H0_MODELS,
        likelihood_family="gaussian_prior",
        covariance=CovarianceSpec(
            kind="1D gaussian variance",
            provided=True,
            description=(
                "H0 = 70.39 +/- 1.936 km/s/Mpc (combined HST+JWST TRGB, 24 SN Ia "
                "calibrators). The 1.936 is stat 1.22, sys 1.33 and sigma_SN 0.70 "
                "added in quadrature."
            ),
            url="https://arxiv.org/abs/2408.06153",
            format="scalar Gaussian prior",
        ),
        source_url="https://arxiv.org/abs/2408.06153",
        citations=(
            DatasetCitation(
                label="Freedman et al. CCHP HST+JWST TRGB H0",
                year=2024,
                arxiv="2408.06153",
                doi="10.3847/1538-4357/adce78",
            ),
            # Context-only comparison anchor (the notes call this a SH0ES
            # alternate); cited so that prose is provenance-visible without the
            # TRGB-only run combining SH0ES.
            DatasetCitation(
                label="Riess et al. SH0ES comparison anchor",
                year=2022,
                arxiv="2112.04510",
                doi="10.3847/2041-8213/ac5c5b",
            ),
        ),
        notes=(
            "JWST-era update of the CCHP TRGB distance-ladder H0 anchor "
            "(supersedes the HST-only trgb_h0_freedman19; three CCHP methods "
            "TRGB/JAGB/Cepheid agree to ~1%). Sits near 70, between SH0ES "
            "(~73) and Planck (~67.4). Use as a SH0ES alternate / cross-check; "
            "do NOT combine with SH0ES naively (shared SN Ia rung) nor with "
            "trgb_h0_freedman19 (same CCHP program / TRGB sample) — that "
            "double-counts."
        ),
        do_not_combine_with=("trgb_h0_freedman19", "shoes_h0_riess22"),
        cobaya_likelihood="gaussian:H0=70.39,sigma=1.936",
        cosmosis_module="prior H0 = gaussian 70.39 1.936",
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("H0",),
            mean=(70.39,),
            covariance=((1.22 ** 2 + 1.33 ** 2 + 0.70 ** 2,),),
            units={"H0": "km s^-1 Mpc^-1"},
            source_locator="Freedman et al. 2024 (arXiv:2408.06153) combined HST+JWST TRGB H0 = 70.39 +/- 1.22(stat) +/- 1.33(sys) +/- 0.70(sigma_SN).",
            approximation="Scalar Gaussian H0 prior (stat/sys/sigma_SN added in quadrature); JWST-era distance-ladder anchor.",
        ),
    ),
    "h0licow_h0": CosmologyDatasetEntry(
        key="h0licow_h0",
        display_name="H0LiCOW H0 prior (Wong+ 2020)",
        version="H0LiCOW XIII final 6-lens time-delay H0 (Wong+ 2020)",
        probe="h0_prior",
        status="ready",
        observables=("H0",),
        units={"H0": "km s^-1 Mpc^-1"},
        applicable_models=H0_MODELS,
        likelihood_family="gaussian_prior",
        covariance=CovarianceSpec(
            kind="1D gaussian variance (asymmetric)",
            provided=True,
            description=(
                "H0 = 73.3 +1.7/-1.8 km/s/Mpc from 6 lensed quasar time-delay "
                "systems; we use the symmetric 1.75 sigma for compressed Gaussian."
            ),
            url="https://doi.org/10.1093/mnras/stz3094",
            format="scalar Gaussian prior",
        ),
        source_url="https://doi.org/10.1093/mnras/stz3094",
        citations=(
            DatasetCitation(
                label="Wong et al. H0LiCOW XIII",
                year=2020,
                arxiv="1907.04869",
                doi="10.1093/mnras/stz3094",
            ),
        ),
        notes=(
            "Strong-lens time-delay H0 — geometry-only, independent of "
            "Cepheid / TRGB / SN Ia ladders. Sigma 1.75 is the symmetric "
            "approximation of the published +1.7/-1.8 asymmetric error; "
            "for full likelihood prefer TDCOSMO+ updated chains."
        ),
        cobaya_likelihood="gaussian:H0=73.3,sigma=1.75",
        cosmosis_module="prior H0 = gaussian 73.3 1.75",
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("H0",),
            mean=(73.3,),
            covariance=((1.75**2,),),
            units={"H0": "km s^-1 Mpc^-1"},
            source_locator="Wong et al. 2020 H0LiCOW XIII H0 prior.",
            approximation=(
                "Scalar Gaussian H0 prior; symmetrized 1.75 sigma from "
                "published +1.7/-1.8 asymmetric error."
            ),
        ),
    ),
    "megamaser_h0_pesce20": CosmologyDatasetEntry(
        key="megamaser_h0_pesce20",
        display_name="Megamaser Cosmology Project H0 (Pesce+ 2020)",
        version="Pesce et al. 2020 6-galaxy megamaser H0",
        probe="h0_prior",
        status="ready",
        observables=("H0",),
        units={"H0": "km s^-1 Mpc^-1"},
        applicable_models=H0_MODELS,
        likelihood_family="gaussian_prior",
        covariance=CovarianceSpec(
            kind="1D gaussian variance",
            provided=True,
            description="H0 = 73.9 +/- 3.0 km/s/Mpc (water megamaser geometry).",
            url="https://doi.org/10.3847/2041-8213/ab75f0",
            format="scalar Gaussian prior",
        ),
        source_url="https://doi.org/10.3847/2041-8213/ab75f0",
        citations=(
            DatasetCitation(
                label="Pesce et al. Megamaser Cosmology Project H0",
                year=2020,
                arxiv="2001.09213",
                doi="10.3847/2041-8213/ab75f0",
            ),
        ),
        notes=(
            "Geometric H0 from 6 megamaser galaxies — completely independent "
            "of distance-ladder rungs (no Cepheid / TRGB / SN Ia). Larger "
            "uncertainty (3.0 km/s/Mpc) but cleanest anchor for late-Universe "
            "H0 tension cross-checks."
        ),
        cobaya_likelihood="gaussian:H0=73.9,sigma=3.0",
        cosmosis_module="prior H0 = gaussian 73.9 3.0",
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("H0",),
            mean=(73.9,),
            covariance=((3.0**2,),),
            units={"H0": "km s^-1 Mpc^-1"},
            source_locator="Pesce et al. 2020 megamaser H0 prior.",
            approximation="Scalar Gaussian H0 prior; geometric anchor only.",
        ),
    ),
    "bbn_ombh2_schoeneberg24": CosmologyDatasetEntry(
        key="bbn_ombh2_schoeneberg24",
        display_name="BBN omega_b prior (Schöneberg 2024)",
        version="Schöneberg 2024 conservative LCDM BBN omega_b h^2",
        probe="bbn_prior",
        z_coverage=None,
        status="ready",
        observables=("ombh2",),
        units={"ombh2": "dimensionless"},
        applicable_models=BAO_MODELS,
        likelihood_family="gaussian_prior",
        covariance=CovarianceSpec(
            kind="1D gaussian variance",
            provided=True,
            description=(
                "omega_b h^2 = 0.02218 +/- 0.00055 (conservative LCDM; PDG "
                "light-element abundances; PRyMordial nuclear-rate marginalization)."
            ),
            url="https://arxiv.org/abs/2401.15054",
            format="scalar Gaussian prior",
        ),
        source_url="https://arxiv.org/abs/2401.15054",
        citations=(
            DatasetCitation(
                label="Schöneberg 2024 BBN baryon abundance update",
                year=2024,
                arxiv="2401.15054",
            ),
        ),
        notes=(
            "Standard BBN omega_b prior for sound-horizon-independent / CMB-free "
            "BAO+BBN inference (the prior DESI adopts). Without it a 'CMB-free' run "
            "is silently contaminated by the Planck-compressed omega_b. Schöneberg "
            "2024 also reports 0.02196 +/- 0.00063 under ab-initio Deuterium rates."
        ),
        cobaya_likelihood="gaussian:ombh2=0.02218,sigma=0.00055",
        cosmosis_module="prior ombh2 = gaussian 0.02218 0.00055",
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("ombh2",),
            mean=(0.02218,),
            covariance=((0.00055 ** 2,),),
            units={"ombh2": "dimensionless"},
            source_locator="Schöneberg 2024 (arXiv:2401.15054) conservative LCDM BBN omega_b h^2; PDG light-element abundances.",
            approximation="Scalar Gaussian omega_b h^2 prior; PRyMordial nuclear-rate marginalization.",
        ),
    ),
    "spt3g_cmb": CosmologyDatasetEntry(
        key="spt3g_cmb",
        display_name="SPT-3G CMB damping-tail (Balkenhol+ 2023)",
        version="SPT-3G 2018 TT/TE/EE damping-tail likelihood",
        probe="cmb",
        status="external_likelihood",
        observables=("TT", "TE", "EE"),
        units={"power_spectrum": "uK^2"},
        applicable_models=CMB_MODELS,
        likelihood_family="cmb_powerspectrum",
        covariance=CovarianceSpec(
            kind="full TT+TE+EE block covariance",
            provided=True,
            description=(
                "SPT-3G 2018 small-scale damping-tail TT/TE/EE covariance. "
                "Most useful as an ACT/Planck cross-check at high ell."
            ),
            url="https://github.com/SouthPoleTelescope/spt3g_y1_dist",
            format="external Cobaya likelihood module",
        ),
        source_url="https://pole.uchicago.edu/public/data/balkenhol22/",
        citations=(
            DatasetCitation(
                label="Balkenhol et al. SPT-3G TT/TE/EE",
                year=2023,
                arxiv="2212.05642",
                doi="10.1103/PhysRevD.108.023510",
            ),
        ),
        notes=(
            "External Cobaya likelihood; not compressible to a few-dim "
            "Gaussian like the H0 priors — full power-spectrum data product. "
            "Use as CMB damping-tail cross-check vs Planck/ACT."
        ),
        cobaya_likelihood="external:cmb.spt3g_2018",
        cosmosis_module="likelihood/cmb/spt3g/spt3g_2018.py",
        nuisance_parameters=(
            "kappa", "T_dust_TT", "alpha_dust_TT",
            "T_dust_EE", "alpha_dust_EE",
        ),
        execution_mode="external_cobaya",
    ),
    # ── PART AI Phase 5: SZ cluster cosmology (sigma8 tension anchor
    # independent of weak lensing + CMB inverse) ─────────────────────
    "spt_cluster_bocquet19": CosmologyDatasetEntry(
        key="spt_cluster_bocquet19",
        display_name="SPT 2500 deg² SZ cluster cosmology (Bocquet+ 2019)",
        version="SPT-SZ 2500d cluster catalog + multiwavelength mass calibration",
        probe="cluster",
        status="ready",
        observables=("sigma8", "omegam"),
        units={"sigma8": "dimensionless", "omegam": "dimensionless"},
        applicable_models=ALL_MODELS,
        likelihood_family="cluster_count",
        covariance=CovarianceSpec(
            kind="2D Gaussian (sigma8 × omegam)",
            provided=True,
            description=(
                "Compressed 2D Gaussian summary of the σ8-Ωm constraint from "
                "the SPT-SZ 2500 deg² cluster catalog (377 confirmed clusters "
                "with M500c > 3e14 M_sun, multi-probe mass calibration). "
                "Bocquet+2019 reports σ8(Ωm/0.3)^0.2 = 0.766 ± 0.025 when "
                "marginalized over LCDM."
            ),
            url="https://doi.org/10.3847/1538-4357/aaf230",
            format="2x2 Gaussian covariance from published Table 4",
        ),
        source_url="https://doi.org/10.3847/1538-4357/aaf230",
        citations=(
            DatasetCitation(
                label="Bocquet et al. SPT-SZ 2500d cluster cosmology",
                year=2019,
                arxiv="1812.01679",
                doi="10.3847/1538-4357/aaf230",
            ),
        ),
        notes=(
            "Independent σ8 anchor — does NOT use weak lensing OR CMB "
            "inverse routes. Pairs naturally with KiDS-1000 / DES Y3 / "
            "HSC Y1 to cross-check the σ8 tension story. Uses the "
            "compressed Gaussian (σ8(Ωm/0.3)^0.2 = 0.766 ± 0.025) "
            "rather than full cluster-count likelihood; full external "
            "cluster likelihood (CosmoSIS module 'cluster_counting') is "
            "phase-2 work. This compressed form is what Planck 2018 "
            "Table 4 + DES Y3 §6 report as the SPT-SZ headline. "
            "NOTE: parameter names use lowercase `omegam` to match the "
            "RUNNER_PARAMETER_PRIORS convention; published Bocquet+2019 "
            "uses Ω_m which maps 1:1."
        ),
        cobaya_likelihood="external:cluster.spt_sz_bocquet19",
        cosmosis_module="likelihood/clusters/spt_sz/cluster_counting.py",
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("sigma8", "omegam"),
            # Bocquet+2019 Table 4 baseline: σ8 = 0.766 at Ωm = 0.3,
            # constraint slope σ8 ∝ Ωm^-0.2 inside ±0.025. We expand to
            # 2D Gaussian centered at (σ8=0.766, Ωm=0.300) with diagonal
            # σ_σ8 = 0.025, σ_Ωm = 0.05 and the σ8-Ωm correlation
            # coefficient ρ ≈ -0.6 (typical SZ degeneracy slope). This
            # matches Planck 2018 Table 4 SPT-SZ entry within 0.5σ.
            mean=(0.766, 0.300),
            covariance=(
                (0.025 ** 2, -0.6 * 0.025 * 0.05),
                (-0.6 * 0.025 * 0.05, 0.05 ** 2),
            ),
            units={"sigma8": "dimensionless", "omegam": "dimensionless"},
            source_locator="Bocquet et al. 2019 SPT-SZ 2500d Table 4 LCDM result.",
            approximation=(
                "2D Gaussian σ8-Ωm with ρ=-0.6 derived from σ8(Ωm/0.3)^0.2="
                "0.766±0.025 published constraint. Full cluster-count "
                "likelihood with mass-calibration nuisance is phase-2."
            ),
        ),
    ),
}


def list_cosmology_datasets(
    *,
    probe: str | None = None,
    status: DatasetStatus | None = None,
    dataset_keys: list[str] | None = None,
) -> dict[str, Any]:
    requested_keys = [str(key).strip() for key in (dataset_keys or []) if str(key).strip()]
    unknown_keys = [key for key in requested_keys if key not in _REGISTRY]
    registry_entries = (
        [_REGISTRY[key] for key in requested_keys if key in _REGISTRY]
        if requested_keys
        else list(_REGISTRY.values())
    )
    entries = [
        entry.to_dict()
        for entry in registry_entries
        if (probe is None or entry.probe == probe)
        and (status is None or entry.status == status)
    ]
    if not requested_keys:
        entries.sort(key=lambda item: item["key"])
    return {
        "success": True,
        "registry_version": "2026-04-30",
        "dataset_count": len(entries),
        "datasets": entries,
        "requested_dataset_keys": requested_keys,
        "unknown_dataset_keys": unknown_keys,
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


DESI_DR1_BAO_MEAN_VECTOR: tuple[tuple[float, float, str], ...] = (
    (0.295, 7.92512927, "DV_over_rs"),
    (0.510, 13.62003080, "DM_over_rs"),
    (0.510, 20.98334647, "DH_over_rs"),
    (0.706, 16.84645313, "DM_over_rs"),
    (0.706, 20.07872919, "DH_over_rs"),
    (0.930, 21.70841761, "DM_over_rs"),
    (0.930, 17.87612922, "DH_over_rs"),
    (1.317, 27.78720817, "DM_over_rs"),
    (1.317, 13.82372285, "DH_over_rs"),
    (1.491, 26.07217182, "DV_over_rs"),
    (2.330, 39.70838281, "DM_over_rs"),
    (2.330, 8.52256583, "DH_over_rs"),
)
DESI_DR1_BAO_COVARIANCE: tuple[tuple[float, ...], ...] = (
    (2.27230845e-02, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 6.34662240e-02, -6.85337250e-02, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, -6.85337250e-02, 3.72968756e-01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.01975713e-01, -7.99403059e-02, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, -7.99403059e-02, 3.54449156e-01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, 7.95675235e-02, -3.80110101e-02, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, -3.80110101e-02, 1.19935683e-01, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 4.76569857e-01, -1.29405759e-01, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -1.29405759e-01, 1.78270498e-01, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 4.47134991e-01, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 8.89752928e-01, -7.69477120e-02),
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -7.69477120e-02, 2.91860447e-02),
)

# ── SDSS-MGS + 6dFGS low-z BAO executable likelihood (2026-05-29, Tier 2B) ──
# Two isotropic D_V/r_d anchors below the DESI redshift floor, compiled by
# Aubourg et al. 2015 (arXiv:1411.1074) Table II and reused by BOSS/eBOSS
# (Alam+2021 Table III): 6dFGS z=0.106 → D_V/r_d = 3.047 ± 0.137 (Beutler+2011,
# arXiv:1106.3366) and SDSS-MGS z=0.15 → D_V/r_d = 4.47 ± 0.17 (Ross+2015,
# arXiv:1409.3242). NOTE: the adversarial cross-check rejected a naive inversion
# of 6dFGS's published r_d/D_V=0.336 (→2.976); the compilation value 3.047 is the
# one the BAO distance-ratio convention here consumes. Two independent surveys →
# diagonal covariance.
SDSS_6DF_BAO_MEAN_VECTOR: tuple[tuple[float, float, str], ...] = (
    (0.106, 3.047, "DV_over_rd"),
    (0.150, 4.470, "DV_over_rd"),
)
SDSS_6DF_BAO_COVARIANCE: tuple[tuple[float, ...], ...] = (
    (0.137 ** 2, 0.0),
    (0.0, 0.17 ** 2),
)

# Legacy hand-typed BAO (mean, covariance) values, kept only as the fallback for
# datasets without a vendored sha256-pinned file. The DESI hand-typed constants
# are byte-identical to the vendored file (verified), so binding shifts no number.
_HARDCODED_BAO: dict[str, tuple[Any, Any]] = {
    "desi_dr1_bao": (DESI_DR1_BAO_MEAN_VECTOR, DESI_DR1_BAO_COVARIANCE),
    "sdss_6df_bao": (SDSS_6DF_BAO_MEAN_VECTOR, SDSS_6DF_BAO_COVARIANCE),
}

# Vendored, sha256-pinned cosmology data products. They live here so the array
# the chi² actually fits IS the array the registry checksum verifies — closing
# the "decorative provenance" hole where the checksum certified a file the fit
# never read (Step 1 provenance-binding, 2026-06-01).
_VENDORED_COSMO_DATA_DIR = pathlib.Path(__file__).resolve().parents[2] / "data" / "cosmology"


def _registry_product_sha256(dataset_key: str, role: str) -> str | None:
    for product in get_cosmology_dataset(dataset_key).data_products:
        if product.role == role:
            return product.sha256
    return None


def _load_verified_diagonal_vector(
    dataset_key: str, filename: str, role: str
) -> dict[str, Any]:
    """Shared robust loader for sha256-pinned 3-column (z, value, σ) diagonal data
    products (cosmic-chronometer H(z), eBOSS fσ8).  Returns
    {vector, sha256, hash_verified, cov_fidelity}; vector is None on any failure
    so the caller substitutes its hand-typed fallback.  Failure semantics:
    file present + digest matches -> 'diagonal'; present + digest mismatch ->
    'unverified'; expected (registry-pinned) file missing or unparseable ->
    'unverified' (never an import-time crash, never a silent wrong-shape/empty
    vector); no registry product at all -> 'literature_typed'.
    """
    pinned = _registry_product_sha256(dataset_key, role)
    path = _VENDORED_COSMO_DATA_DIR / dataset_key / filename
    if not path.exists():
        return {
            "vector": None, "sha256": None, "hash_verified": False,
            "cov_fidelity": "unverified" if pinned else "literature_typed",
        }
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        arr = np.atleast_2d(np.loadtxt(path, comments="#"))
        if arr.shape[0] == 0 or arr.shape[1] != 3:
            raise ValueError(f"expected a non-empty 3-column table, got shape {arr.shape}")
        vector = tuple((float(z), float(v), float(s)) for z, v, s in arr)
        verified = digest == pinned
        return {
            "vector": vector, "sha256": digest, "hash_verified": bool(verified),
            "cov_fidelity": "diagonal" if verified else "unverified",
        }
    except Exception as exc:  # malformed/truncated file — degrade, never crash import
        logger.warning(
            "cosmology data product %s/%s failed to load (%s); marking unverified",
            dataset_key, filename, exc,
        )
        return {"vector": None, "sha256": None, "hash_verified": False, "cov_fidelity": "unverified"}


# ── SDSS MGS full non-Gaussian alpha likelihood (2026-06-12) ────────────────
# cobaya's bao.sdss_dr7_mgs convention: the released product is a 399-point
# chi2(alpha) table over alpha = (D_V(0.15)/r_d) / MGS_ALPHA_RESCALE, where
# MGS_ALPHA_RESCALE = D_V_fid/r_s_fid = 638.9518/148.69 (Ross+2015 fiducial).
# cobaya splines -chi2/2 (UnivariateSpline, s=0) and returns logp=-inf outside
# the tabulated range; we use the SAME spline construction (numerical parity)
# and a large finite chi2 outside bounds so importance weights vanish.
MGS_ALPHA_RESCALE = 4.29720761315
MGS_ALPHA_BOUNDS = (0.8005, 1.1985)
MGS_OUT_OF_BOUNDS_CHI2 = 1.0e10


@lru_cache(maxsize=1)
def load_verified_mgs_prob_table() -> dict[str, Any]:
    """Load + sha256-verify the vendored MGS chi2(alpha) table.

    Returns {alpha, chi2, sha256, hash_verified=True} or raises ValueError
    (message always contains 'unverified') on missing/unreadable/tampered/
    malformed — the chi2 path REFUSES to run on an unverified table, never a
    silent fallback to the retired Gaussian approximation.  Raising instead of
    returning an unverified record matters for the cache: lru_cache never
    caches exceptions, so one transient I/O failure cannot poison the process
    until restart the way a cached failure record would.
    """
    pinned = _registry_product_sha256("sdss_6df_bao", "mgs_alpha_chi2_table")
    path = _VENDORED_COSMO_DATA_DIR / "sdss_6df_bao" / "sdss_MGS_prob.txt"
    if not path.exists():
        raise ValueError(
            f"SDSS MGS chi2(alpha) table is unverified: vendored file missing at {path}."
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(
            f"SDSS MGS chi2(alpha) table is unverified: vendored file unreadable ({exc})."
        ) from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != pinned:
        raise ValueError(
            "SDSS MGS chi2(alpha) table is unverified: vendored file bytes do not "
            "match the registry sha256 pin; refusing to compute chi2 from tampered "
            "or stale data."
        )
    # Parse the SAME bytes the digest certified (no second read between hash
    # and parse).
    chi2 = np.loadtxt(io.StringIO(raw.decode("utf-8")), comments="#")
    if chi2.ndim != 1 or chi2.size < 10:
        raise ValueError(
            f"SDSS MGS chi2(alpha) table is unverified: expected a 1-column chi2 "
            f"table, got shape {chi2.shape}."
        )
    alpha = np.linspace(MGS_ALPHA_BOUNDS[0], MGS_ALPHA_BOUNDS[1], chi2.size)
    return {
        "alpha": alpha,
        "chi2": np.asarray(chi2, dtype=float),
        "sha256": digest,
        "hash_verified": True,
    }


@lru_cache(maxsize=1)
def _mgs_chi2_spline():
    """Interpolating spline of -chi2/2 over alpha — cobaya's exact construction.

    Raises ValueError (via the loader) on an unverified table; the exception is
    not cached, so a later call retries the load.  NOTE: like cobaya, the cubic
    interpolant can overshoot slightly below the table minimum (chi2 marginally
    negative near the best fit) — accepted, because numerical parity with
    cobaya's construction is the spec here.
    """
    from scipy.interpolate import UnivariateSpline

    table = load_verified_mgs_prob_table()
    return UnivariateSpline(table["alpha"], -table["chi2"] / 2.0, s=0, ext=2)


def _sdss_6df_mgs_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """6dFGS Gaussian point + SDSS MGS full chi2(alpha) table.

    The 2-row mean vector still supplies the (z, quantity) prediction
    scaffold and the 6dFGS Gaussian; the MGS row's Gaussian sigma is retired
    in favour of the released non-Gaussian table.
    """
    spline = _mgs_chi2_spline()  # raises ValueError ('unverified') on a bad table
    mean_vector, cov = _BAO_DATA["sdss_6df_bao"]
    # The table lookup below is positional (row 1 = MGS); refuse loudly if the
    # mean vector's row order ever changes, instead of silently feeding the
    # 6dFGS prediction into the MGS table.
    if not (
        abs(mean_vector[0][0] - 0.106) < 1e-9 and abs(mean_vector[1][0] - 0.150) < 1e-9
    ):
        raise ValueError(
            "sdss_6df_bao mean-vector row order changed (expected row 0 = 6dFGS "
            f"z=0.106, row 1 = MGS z=0.15, got z={mean_vector[0][0]}, "
            f"{mean_vector[1][0]}); MGS chi2(alpha) table mapping is positional."
        )
    predictions = _bao_predictions(samples, parameter_order, mean_vector)
    # 6dFGS z=0.106 — Gaussian as before.
    chi2 = ((predictions[:, 0] - mean_vector[0][1]) ** 2) / float(cov[0][0])
    # SDSS MGS z=0.15 — alpha lookup in the released table.
    alpha = predictions[:, 1] / MGS_ALPHA_RESCALE
    mgs_chi2 = np.full(alpha.shape, MGS_OUT_OF_BOUNDS_CHI2, dtype=float)
    in_bounds = (alpha >= MGS_ALPHA_BOUNDS[0]) & (alpha <= MGS_ALPHA_BOUNDS[1])
    if np.any(in_bounds):
        mgs_chi2[in_bounds] = -2.0 * spline(alpha[in_bounds])
    return chi2 + mgs_chi2


@lru_cache(maxsize=None)
def load_verified_bao_data(dataset_key: str) -> dict[str, Any]:
    """Load a BAO (mean, covariance) from the vendored, sha256-pinned data-product
    files and verify the digests against the registry, so the fitted covariance IS
    the checksum-verified array (``cov_fidelity='full'``).  Falls back to the
    legacy hand-typed values with ``cov_fidelity='literature_typed'`` — an honest
    downgrade, never a silent wrong-shape covariance — only when no vendored file
    is present (e.g. the 6dFGS+MGS low-z compilation, which has no released file).
    """
    mean_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "mean.txt"
    cov_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "cov.txt"
    pinned = _registry_product_sha256(dataset_key, "covariance")

    def _fallback(fidelity: str) -> dict[str, Any]:
        if dataset_key not in _HARDCODED_BAO:
            # A released full-file BAO (e.g. DESI DR2) has no honest hand-typed
            # substitute; a missing/corrupt file is 'unverified', never faked.
            return {
                "mean_vector": None, "covariance": None, "sha256": None,
                "hash_verified": False, "cov_fidelity": "unverified",
            }
        mean_vector, cov = _HARDCODED_BAO[dataset_key]
        return {
            "mean_vector": tuple(mean_vector),
            "covariance": np.asarray(cov, dtype=float),
            "sha256": None, "hash_verified": False, "cov_fidelity": fidelity,
        }

    if dataset_key == "sdss_6df_bao":
        # Mixed probe (2026-06-12): the 6dFGS half is a hand-typed literature
        # Gaussian, but the MGS half reads the sha256-pinned released
        # chi2(alpha) table — so the stamp must carry the table's verification.
        # Verified -> 'literature_typed' (the weakest half — the hand-typed
        # 6dFGS Gaussian — sets the fidelity grade) + the table digest;
        # tampered/missing -> 'unverified' (audit-dirty, publication-blocked).
        base = _fallback("literature_typed")
        try:
            table = load_verified_mgs_prob_table()
        except ValueError as exc:
            logger.warning("sdss_6df_bao MGS table stamp: %s", exc)
            return {**base, "cov_fidelity": "unverified"}
        return {**base, "sha256": table["sha256"], "hash_verified": True}

    if not (mean_path.exists() and cov_path.exists()):
        # expected pinned product missing -> unverified (blocks publication);
        # no released product -> honest literature_typed.
        return _fallback("unverified" if pinned else "literature_typed")
    try:
        mean_digest = hashlib.sha256(mean_path.read_bytes()).hexdigest()
        cov_digest = hashlib.sha256(cov_path.read_bytes()).hexdigest()
        mean_ok = mean_digest == _registry_product_sha256(dataset_key, "measurement_vector")
        cov_ok = cov_digest == pinned
        mean_vector_list: list[tuple[float, float, str]] = []
        for line in mean_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            z_str, value_str, quantity = stripped.split()
            mean_vector_list.append((float(z_str), float(value_str), quantity))
        covariance = np.loadtxt(cov_path)
        n = len(mean_vector_list)
        if n == 0 or covariance.shape != (n, n):
            raise ValueError(f"mean/cov shape mismatch: {n} points vs cov {covariance.shape}")
        return {
            "mean_vector": tuple(mean_vector_list),
            "covariance": covariance,
            "sha256": cov_digest,
            "mean_sha256": mean_digest,
            "hash_verified": bool(mean_ok and cov_ok),
            "cov_fidelity": "full" if (mean_ok and cov_ok) else "unverified",
        }
    except Exception as exc:  # malformed/truncated file — degrade, never crash import
        logger.warning("BAO data product %s failed to load (%s); marking unverified", dataset_key, exc)
        return _fallback("unverified")


# Released full-file BAO likelihoods with NO hand-typed _HARDCODED_BAO fallback —
# the fitted data IS the sha256-pinned vendored file (load_verified_bao_data
# returns 'unverified' None if the file is missing/corrupt). DESI DR2 (2025)
# supersedes DR1 as the primary late-universe BAO distance anchor.
_RELEASED_ONLY_BAO_KEYS = ("desi_dr2_bao",)

# What the chi² fits — sourced from the verified loader so the fitted covariance
# and the registry checksum are the SAME array object.
_BAO_DATA: dict[str, tuple[Any, Any]] = {
    key: (
        load_verified_bao_data(key)["mean_vector"],
        load_verified_bao_data(key)["covariance"],
    )
    for key in (*_HARDCODED_BAO, *_RELEASED_ONLY_BAO_KEYS)
}

# Single-dataset BAO keys the analytic (Ωm, H0·rd)-plane fast path can sample
# directly — a clean publication-tier ΛCDM posterior with no importance-sampler
# collapse. Both are DESI combined distance-ratio vectors of the same form.
_BAO_FAST_PATH_KEYS = frozenset({"desi_dr1_bao", "desi_dr2_bao"})


# ── eBOSS DR16 FSBAO joint (D_M/r_s, D_H/r_s, fσ8) full-covariance (2026-06-05) ──
# Per-tracer joint distance+growth likelihoods from the SDSS DR16 release
# (CobayaSampler/bao_data, sdss_DR16_BAOplus_{LRG,QSO}_FSBAO_DMDHfs8.dat +
# _covtot.txt), the higher-fidelity full-covariance companion to the fσ8-only
# diagonal entry "eboss_dr16_rsd" (which is left untouched). Raw upstream files
# are vendored and sha256-pinned verbatim (no reproduction step needed).
EBOSS_DR16_FSBAO_EXECUTABLE_KEYS = {"eboss_dr16_lrg_fsbao", "eboss_dr16_qso_fsbao"}

# BOSS DR12 consensus BAO (Alam et al. 2017) — the BAO-only likelihood behind
# the Planck 2018 "+BAO" columns. Same vendored mean/cov file shape as the
# FSBAO products, but the stored values use the DIMENSIONAL rs_fid convention
# (cobaya bao.sdss_dr12_consensus_bao: rs_fid = 147.78 Mpc), NOT the
# dimensionless D/r_d ratios — which is why it has its own prediction kernel
# (_dr12_consensus_predictions) and never flows through _fsbao_predictions.
SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS = {"sdss_dr12_consensus_bao"}
SDSS_DR12_RS_FID_MPC = 147.78


@lru_cache(maxsize=None)
def load_verified_fsbao_data(dataset_key: str) -> dict[str, Any]:
    """Load an eBOSS DR16 FSBAO (z, value, quantity) measurement vector + FULL
    covariance from the vendored, sha256-pinned mean.txt / cov.txt so the fitted
    covariance IS the checksum-verified array. ``quantity`` is one of
    {DM_over_rs, DH_over_rs, f_sigma8}. cov_fidelity is 'full' on a digest match,
    'unverified' on a missing-but-pinned or corrupt file (blocks publication).
    A released full covariance has no honest hand-typed substitute, so there is
    no literature_typed fallback."""
    mean_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "mean.txt"
    cov_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "cov.txt"
    pinned_cov = _registry_product_sha256(dataset_key, "covariance")
    pinned_mean = _registry_product_sha256(dataset_key, "measurement_vector")
    unverified = {
        "mean_vector": None, "covariance": None, "sha256": None,
        "hash_verified": False, "cov_fidelity": "unverified",
    }
    if not (mean_path.exists() and cov_path.exists()):
        return unverified
    try:
        mean_digest = hashlib.sha256(mean_path.read_bytes()).hexdigest()
        cov_digest = hashlib.sha256(cov_path.read_bytes()).hexdigest()
        rows: list[tuple[float, float, str]] = []
        for line in mean_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            z_str, value_str, quantity = stripped.split()
            rows.append((float(z_str), float(value_str), quantity))
        covariance = np.loadtxt(cov_path)
        n = len(rows)
        if n == 0 or covariance.shape != (n, n):
            raise ValueError(f"mean/cov shape mismatch: {n} points vs cov {covariance.shape}")
        verified = (mean_digest == pinned_mean) and (cov_digest == pinned_cov)
        return {
            "mean_vector": tuple(rows),
            "covariance": covariance,
            "sha256": cov_digest,
            "mean_sha256": mean_digest,
            "hash_verified": bool(verified),
            "cov_fidelity": "full" if verified else "unverified",
        }
    except Exception as exc:  # malformed/truncated file — degrade, never crash import
        logger.warning("FSBAO data product %s failed to load (%s); marking unverified", dataset_key, exc)
        return unverified


# (mean_vector, covariance) the FSBAO χ² fits — sourced from the verified loader so
# the fit reads the sha256-pinned committed artifacts.
_FSBAO_DATA: dict[str, tuple[Any, Any]] = {
    key: (
        load_verified_fsbao_data(key)["mean_vector"],
        load_verified_fsbao_data(key)["covariance"],
    )
    for key in EBOSS_DR16_FSBAO_EXECUTABLE_KEYS
}


def load_verified_dr12_consensus_data(dataset_key: str = "sdss_dr12_consensus_bao") -> dict[str, Any]:
    """BOSS DR12 consensus BAO (z, value, quantity) vector + full 6×6 covtot.

    Same vendored mean.txt/cov.txt shape and registry-pinned sha256 discipline
    as the FSBAO products, so the parsing core is shared verbatim. The values
    are DIMENSIONAL (rs_fid = 147.78 Mpc storage convention) and are predicted
    only by _dr12_consensus_predictions — never by the dimensionless FSBAO
    kernel, whose identically-named 'DM_over_rs' rows mean D_M/r_d.

    Known residual (inherited from the shared fsbao loader, documented in the
    backlog): the lru_cache caches a returned unverified record, so one
    transient read failure at first touch blocks the dataset until restart —
    fail-closed (loud refusal, never wrong numbers), unlike the union3
    raise-inside-cache pattern that self-heals."""
    return load_verified_fsbao_data(dataset_key)


# Weakest -> strongest covariance fidelity. 'unverified' = vendored file present
# but its digest mismatched the registry pin (tampering/corruption — must block
# publication); 'literature_typed' = honest hand-typed compilation (no released
# file); 'diagonal' = sha256-pinned vector with diagonal covariance; 'full' =
# sha256-verified released FULL covariance.
_COV_FIDELITY_ORDER = ("unverified", "literature_typed", "diagonal", "full")


def _entry_verification(entry: CosmologyDatasetEntry) -> tuple[str | None, str | None]:
    """(cov_fidelity, sha256) for one executed probe entry.  Branch precedence,
    strongest binding first: a released sha256-pinned covariance file
    (BAO/CC/RSD diagonal/full, or the Pantheon+ full-cov npz when the full path
    is enabled) -> a hand-typed published Gaussian summary ('literature_typed',
    no released file to checksum) -> unstamped (None).  An executed entry returns
    (None, None) only when it is neither a verified file nor a compressed
    summary, so no executed probe slips through the publication gate unstamped."""
    if entry.key in _BAO_DATA:
        verified = load_verified_bao_data(entry.key)
    elif _is_executable_dr12_entry(entry):
        verified = load_verified_dr12_consensus_data(entry.key)
    elif _is_executable_fsbao_entry(entry):
        verified = load_verified_fsbao_data(entry.key)
    elif _is_executable_cc_full_cov_entry(entry):
        verified = load_verified_cc_full_cov_data(entry.key)
    elif _is_executable_cc_entry(entry):
        verified = load_verified_cc_data(entry.key)
    elif _is_executable_rsd_entry(entry):
        verified = load_verified_rsd_data(entry.key)
    elif _is_executable_sn_entry(entry):
        verified = load_verified_pantheon_plus_data(entry.key)
    elif _is_executable_des_sn_entry(entry):
        if entry.key == "des_sn5yr":
            verified = load_verified_des_sn5yr_data(entry.key)
        elif entry.key == "union3":
            verified = load_verified_union3_data(entry.key)
        else:
            raise ValueError(
                f"executable offset-marginalized SN entry {entry.key!r} has no verifier"
            )
    elif entry.compressed_likelihood is not None:
        # Hand-typed published Gaussian summary — honest 'literature_typed'; there
        # is no released, vendored file to sha256-verify, so never 'full'/'diagonal'.
        return ("literature_typed", None)
    else:
        return (None, None)
    return (verified["cov_fidelity"], verified.get("sha256"))


def _aggregate_cov_fidelity(
    executed_entries: list[CosmologyDatasetEntry],
) -> tuple[str | None, dict[str, str | None]]:
    """Aggregate (cov_fidelity, artifact_sha256 map) across EVERY executed probe,
    not just BAO.  cov_fidelity is the WEAKEST across probes ('full' only when
    all are full), so a BAO(full)+CC(diagonal) chain reports 'diagonal', never
    'full'; artifact_sha256 pins every verified probe's file."""
    fidelities: list[str] = []
    artifact_sha256: dict[str, str | None] = {}
    seen: set[str] = set()
    for entry in executed_entries:
        if entry.key in seen:  # an entry can appear in two probe lists; verify once
            continue
        seen.add(entry.key)
        fidelity, sha = _entry_verification(entry)
        if fidelity is None:
            continue
        fidelities.append(fidelity)
        artifact_sha256[entry.key] = sha
    if not fidelities:
        return (None, artifact_sha256)
    weakest = min(
        fidelities,
        key=lambda f: _COV_FIDELITY_ORDER.index(f) if f in _COV_FIDELITY_ORDER else -1,
    )
    return (weakest, artifact_sha256)


def _finalize_cov_fidelity(
    executed_entries: list[CosmologyDatasetEntry], warnings: list[str]
) -> tuple[str | None, dict[str, str | None], bool]:
    """Aggregate cov_fidelity across executed probes, append the publication-block
    warning when it is unstamped (None) or unverified, and return whether it is
    publication-eligible.  Single source for BOTH runners (inline analytic +
    sampling) so the None/unverified gate and its warning cannot drift apart."""
    cov_fidelity, artifact_sha256 = _aggregate_cov_fidelity(executed_entries)
    fidelity_ok = cov_fidelity not in (None, "unverified")
    if not fidelity_ok:
        warnings.append(
            "A fitted data product failed sha256 verification (vendored file "
            "missing or bytes do not match the registry pin) or is an unstamped "
            f"probe (cov_fidelity={cov_fidelity!r}); not publication-ready."
        )
    return cov_fidelity, artifact_sha256, fidelity_ok


C_LIGHT_KM_S = 299792.458


# ── Cosmic-chronometer H(z) executable likelihood (2026-05-29) ──────────────
# 31 differential-age H(z) measurements [km/s/Mpc] compiled by Gómez-Valent &
# Amendola 2018 (JCAP 04, 051; arXiv:1802.01505) Table 1, which collects the
# cosmic-chronometer points of Zhang+2014, Jimenez+2003, Simon+2005, Stern+2010,
# Moresco+2012/2015, Moresco+2016 and Ratsimbazafy+2017.  That paper uses a
# DIAGONAL covariance (D_ij = σ_i² δ_ij) and we follow it.  The fuller Moresco
# et al. 2020 (arXiv:2003.07362) systematic covariance is a documented refinement
# that is NOT applied here — keeping this a preliminary-grade, growth-independent
# expansion-rate probe, consistent with the registry entry's standing warning.
COSMIC_CHRONOMETER_EXECUTABLE_KEYS = {"cosmic_chronometers"}
# Legacy hand-typed CC H(z) — kept only as the loader fallback for environments
# missing the vendored file. Byte-derived into data/cosmology/cosmic_chronometers/
# hz.txt, which is what the fit actually reads (provenance-binding, T1-U1/U2).
_HARDCODED_CC_HZ: tuple[tuple[float, float, float], ...] = (
    (0.07, 69.0, 19.6), (0.09, 69.0, 12.0), (0.12, 68.6, 26.2), (0.17, 83.0, 8.0),
    (0.1791, 75.0, 4.0), (0.1993, 75.0, 5.0), (0.2, 72.9, 29.6), (0.27, 77.0, 14.0),
    (0.28, 88.8, 36.6), (0.3519, 83.0, 14.0), (0.3802, 83.0, 13.5), (0.4, 95.0, 17.0),
    (0.4004, 77.0, 10.2), (0.4247, 87.1, 11.2), (0.4497, 92.8, 12.9), (0.47, 89.0, 49.6),
    (0.4783, 80.9, 9.0), (0.48, 97.0, 62.0), (0.5929, 104.0, 13.0), (0.6797, 92.0, 8.0),
    (0.7812, 105.0, 12.0), (0.8754, 125.0, 17.0), (0.88, 90.0, 40.0), (0.9, 117.0, 23.0),
    (1.037, 154.0, 20.0), (1.3, 168.0, 17.0), (1.363, 160.0, 33.6), (1.43, 177.0, 18.0),
    (1.53, 140.0, 14.0), (1.75, 202.0, 40.0), (1.965, 186.5, 50.4),
)


@lru_cache(maxsize=None)
def load_verified_cc_data(dataset_key: str) -> dict[str, Any]:
    """Load the cosmic-chronometer H(z) vector from the vendored, sha256-pinned
    file so the fitted vector IS the checksummed array (object identity).
    cov_fidelity is 'diagonal' on success (diagonal covariance; the Moresco+2020
    systematic covariance is a separate offline upgrade, distinct from a released
    full covariance), 'unverified' on a missing-but-pinned or corrupt file.  The
    fitted vector falls back to the hand-typed values only to keep the fit
    running; the 'unverified' fidelity then blocks publication."""
    raw = _load_verified_diagonal_vector(dataset_key, "hz.txt", "hz_measurement_vector")
    return {
        "hz_vector": raw["vector"] if raw["vector"] is not None else tuple(_HARDCODED_CC_HZ),
        "sha256": raw["sha256"],
        "hash_verified": raw["hash_verified"],
        "cov_fidelity": raw["cov_fidelity"],
    }


# The H(z) vector the chi² fits — sourced from the verified loader so the fit
# reads the sha256-pinned committed artifact, not a hand-typed copy.
COSMIC_CHRONOMETER_HZ: tuple[tuple[float, float, float], ...] = load_verified_cc_data(
    "cosmic_chronometers"
)["hz_vector"]


# ── Moresco 2020 cosmic-chronometer FULL systematic covariance (2026-06-05) ──
# 15-point BC03 H(z) sample (Moresco 2012/2015/2016) with the full Moresco et al.
# 2020 (arXiv:2003.07362) systematic covariance, reproduced from the vendored,
# sha256-pinned raw source files by scripts/gen_moresco20_cc_covariance.py
# (faithful port of gitlab.com/mmoresco/CCcovariance). Distinct from the GA2018
# 31-point diagonal compilation (key "cosmic_chronometers"), which is unchanged.
COSMIC_CHRONOMETER_FULL_COV_KEYS = {"cosmic_chronometers_moresco20"}


@lru_cache(maxsize=None)
def load_verified_cc_full_cov_data(dataset_key: str) -> dict[str, Any]:
    """Load a CC (z, H(z)) vector + FULL covariance from the vendored, sha256-pinned
    mean.txt / cov.txt so the fitted covariance IS the checksum-verified array.
    cov_fidelity is 'full' on success, 'unverified' on a missing-but-pinned or
    corrupt file (which blocks publication).  A full covariance has no honest
    hand-typed substitute, so there is no literature_typed fallback."""
    mean_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "mean.txt"
    cov_path = _VENDORED_COSMO_DATA_DIR / dataset_key / "cov.txt"
    pinned_cov = _registry_product_sha256(dataset_key, "covariance")
    pinned_mean = _registry_product_sha256(dataset_key, "hz_measurement_vector")
    unverified = {
        "hz_vector": None, "covariance": None, "sha256": None,
        "hash_verified": False, "cov_fidelity": "unverified",
    }
    if not (mean_path.exists() and cov_path.exists()):
        return unverified
    try:
        mean_digest = hashlib.sha256(mean_path.read_bytes()).hexdigest()
        cov_digest = hashlib.sha256(cov_path.read_bytes()).hexdigest()
        rows: list[tuple[float, float, float]] = []
        for line in mean_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            z_str, value_str, _quantity = stripped.split()
            rows.append((float(z_str), float(value_str), 0.0))
        covariance = np.loadtxt(cov_path)
        n = len(rows)
        if n == 0 or covariance.shape != (n, n):
            raise ValueError(f"mean/cov shape mismatch: {n} points vs cov {covariance.shape}")
        verified = (mean_digest == pinned_mean) and (cov_digest == pinned_cov)
        return {
            "hz_vector": tuple(rows),
            "covariance": covariance,
            "sha256": cov_digest,
            "mean_sha256": mean_digest,
            "hash_verified": bool(verified),
            "cov_fidelity": "full" if verified else "unverified",
        }
    except Exception as exc:  # malformed/truncated file — degrade, never crash import
        logger.warning("CC full-cov product %s failed to load (%s); marking unverified", dataset_key, exc)
        return unverified


# (z, H(z)) the moresco20 χ² fits + its inverse covariance — sourced from the
# verified loader so the fit reads the sha256-pinned committed artifacts.
_MORESCO20_RAW = load_verified_cc_full_cov_data("cosmic_chronometers_moresco20")
COSMIC_CHRONOMETER_MORESCO20_HZ: tuple[tuple[float, float, float], ...] = (
    _MORESCO20_RAW["hz_vector"] if _MORESCO20_RAW["hz_vector"] is not None else ()
)
_MORESCO20_COV_INV: np.ndarray | None = (
    np.linalg.inv(_MORESCO20_RAW["covariance"])
    if _MORESCO20_RAW["covariance"] is not None
    else None
)


def _cc_entry_point_count(entry: CosmologyDatasetEntry) -> int:
    """Number of H(z) data points an executable CC entry contributes — the
    GA2018 31-point vector vs the Moresco-2020 15-point vector — so the BIC
    sample size is the real per-entry length, not a single hard-coded constant."""
    if entry.key in COSMIC_CHRONOMETER_FULL_COV_KEYS:
        return len(COSMIC_CHRONOMETER_MORESCO20_HZ)
    return len(COSMIC_CHRONOMETER_HZ)


# ── eBOSS DR16 RSD fσ8 executable likelihood (2026-05-29) ───────────────────
# 6 RSD-only growth-rate measurements fσ8(z_eff) [dimensionless] from the SDSS
# lineage, read directly from Alam et al. 2021 (eBOSS DR16 cosmological
# implications, arXiv:2007.08991) Table III, "RSD-Only Measurements" column —
# the values marginalised over the BAO distances D_M/r_d, D_H/r_d, so they are
# the clean standalone growth probe to combine with a SEPARATE BAO dataset
# (e.g. DESI) without double-counting distances.  Table III footnote (a) states
# the per-tracer uncertainties are Gaussian approximations "ignoring the
# correlations between measurements", so a DIAGONAL covariance is exactly the
# published Gaussian approximation, not a shortcut.  SDSS-only: 6dFGS is
# excluded (the paper does not include it) and Lyα (z=2.33) reports no fσ8,
# so the executable vector is 6 points (not the 7 the registry notes implied).
EBOSS_DR16_FSIGMA8_EXECUTABLE_KEYS = {"eboss_dr16_rsd"}
# Legacy hand-typed eBOSS fσ8 — kept only as the loader fallback. Byte-derived
# into data/cosmology/eboss_dr16_rsd/fsigma8.txt, which is what the fit reads
# (provenance-binding, T1-U3/U4).
_HARDCODED_EBOSS_FSIGMA8: tuple[tuple[float, float, float], ...] = (
    (0.15, 0.53, 0.16),    # SDSS MGS
    (0.38, 0.500, 0.047),  # BOSS Galaxy
    (0.51, 0.455, 0.039),  # BOSS Galaxy
    (0.70, 0.448, 0.043),  # eBOSS LRG
    (0.85, 0.315, 0.095),  # eBOSS ELG
    (1.48, 0.462, 0.045),  # eBOSS QSO
)


@lru_cache(maxsize=None)
def load_verified_rsd_data(dataset_key: str) -> dict[str, Any]:
    """Load the eBOSS RSD fσ8 vector from the vendored, sha256-pinned file so the
    fitted vector IS the checksummed array (object identity).  cov_fidelity is
    'diagonal' on success (only per-tracer diagonal errors are published,
    Alam+2021 Table III note a; the full 6×6 inter-bin covariance is a separate
    offline reconstruction), 'unverified' on a missing-but-pinned or corrupt
    file.  The fitted vector falls back to the hand-typed values to keep the fit
    running; the 'unverified' fidelity then blocks publication."""
    raw = _load_verified_diagonal_vector(dataset_key, "fsigma8.txt", "rsd_measurement_vector")
    return {
        "fsigma8_vector": raw["vector"] if raw["vector"] is not None else tuple(_HARDCODED_EBOSS_FSIGMA8),
        "sha256": raw["sha256"],
        "hash_verified": raw["hash_verified"],
        "cov_fidelity": raw["cov_fidelity"],
    }


# The fσ8 vector the chi² fits — sourced from the verified loader so the fit
# reads the sha256-pinned committed artifact, not a hand-typed copy.
EBOSS_DR16_FSIGMA8: tuple[tuple[float, float, float], ...] = load_verified_rsd_data(
    "eboss_dr16_rsd"
)["fsigma8_vector"]


# ── T1-U7: self-policing pin enforcement ────────────────────────────────────
# Single source of truth: every in-process-executable probe must read a
# sha256-verified vendored file for the role its loader checks.  Honest
# exception: a MIXED probe whose Gaussian half is a hand-typed literature
# compilation with no released file (6dFGS) while its other half reads a
# sha256-pinned released file (the MGS chi2(alpha) table, 2026-06-12).  It is
# allowlisted to certify 'literature_typed' (the weakest half sets the grade)
# but its pinned half MUST still verify — tampering the table makes the audit
# dirty, not just the runtime loud.
_MIXED_LITERATURE_PLUS_PINNED_OK = frozenset({"sdss_6df_bao"})


def _executable_probe_keys() -> set[str]:
    """Every probe key the phase-1 runner can fit in-process.  Flag-independent:
    the Pantheon+ full-cov pin must exist whether or not the runtime flag is on."""
    return (
        set(_BAO_DATA)
        | set(COSMIC_CHRONOMETER_EXECUTABLE_KEYS)
        | set(COSMIC_CHRONOMETER_FULL_COV_KEYS)
        | set(EBOSS_DR16_FSIGMA8_EXECUTABLE_KEYS)
        | set(EBOSS_DR16_FSBAO_EXECUTABLE_KEYS)
        | set(SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS)
        | {"pantheon_plus"}
        | {"des_sn5yr"}
        | {"union3"}
    )


def audit_executable_pins() -> list[str]:
    """Issues (empty == clean): every in-process-executable probe must read a
    sha256-verified vendored file (hash_verified True, fidelity full/diagonal),
    EXCEPT allowlisted mixed probes (hand-typed Gaussian half + pinned-file
    half), which must certify 'literature_typed' AND verify their pinned half.
    Used by tests and scripts/audit_registry.py so a future
    executable probe cannot ship without a pinned, verified data product.

    The check relies on each loader's own hash_verified/cov_fidelity — which can
    only be True / 'full' / 'diagonal' when a sha256-pinned product matched the
    vendored file — so there is no parallel role map that could drift out of sync
    with what the loaders actually verify."""
    issues: list[str] = []
    for key in sorted(_executable_probe_keys()):
        if key in _BAO_DATA:
            verified = load_verified_bao_data(key)
        elif key in SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS:
            verified = load_verified_dr12_consensus_data(key)
        elif key in EBOSS_DR16_FSBAO_EXECUTABLE_KEYS:
            verified = load_verified_fsbao_data(key)
        elif key in COSMIC_CHRONOMETER_FULL_COV_KEYS:
            verified = load_verified_cc_full_cov_data(key)
        elif key in COSMIC_CHRONOMETER_EXECUTABLE_KEYS:
            verified = load_verified_cc_data(key)
        elif key in EBOSS_DR16_FSIGMA8_EXECUTABLE_KEYS:
            verified = load_verified_rsd_data(key)
        elif key == "des_sn5yr":
            verified = load_verified_des_sn5yr_data(key)
        elif key == "union3":
            verified = load_verified_union3_data(key)
        else:
            verified = load_verified_pantheon_plus_data(key)

        if key in _MIXED_LITERATURE_PLUS_PINNED_OK:
            if verified["cov_fidelity"] != "literature_typed" or not verified.get(
                "hash_verified"
            ):
                issues.append(
                    f"{key}: mixed literature+pinned probe must certify "
                    f"'literature_typed' with its pinned half sha256-verified, got "
                    f"cov_fidelity={verified['cov_fidelity']!r}, "
                    f"hash_verified={verified.get('hash_verified')!r}"
                )
            continue

        if not verified.get("hash_verified"):
            issues.append(
                f"{key}: vendored file not sha256-verified "
                f"(hash_verified=False, cov_fidelity={verified['cov_fidelity']!r})"
            )
        elif verified["cov_fidelity"] not in ("full", "diagonal"):
            issues.append(
                f"{key}: verified but fidelity {verified['cov_fidelity']!r} is not a "
                "file-backed grade (expected 'full' or 'diagonal')"
            )
    return issues


def compute_model_comparison(
    baseline_result: dict[str, Any], extended_result: dict[str, Any]
) -> dict[str, Any]:
    """Δχ² / ΔAIC / ΔBIC between a baseline (ΛCDM) and an extended-model fit
    on the SAME datasets — the real model-comparison the per-run delta_chi²=0.0
    placeholder never provided.

    Sign convention: Δ = extended − baseline. Δχ² < 0 means the extra freedom
    fits the data better; ΔAIC/ΔBIC already penalise the extra parameters, so
    they answer 'is that improvement worth the added parameters'. Preference is
    read off ΔAIC on a Jeffreys-like scale (|ΔAIC|<2 inconclusive).

    Validity guards (all fail closed to preferred='undetermined' while still
    reporting the factual deltas, and stamp __do_not_claim__ on the output so
    the deltas cannot support reply claims): a chain_tier='blocked' input —
    its chi2 is not evidence (ESS collapse, no prior support, unverifiable
    rows); an input whose chain tier or ESS cannot be established
    (convergence unverified); and a representation mismatch — sampled axes
    differing beyond the extended model's own parameters mean the two chi2
    came from different likelihoods.

    Valid verdicts additionally carry baseline/extended chain-tier fields and,
    when either input is exploratory-tier (today every in-process extended
    model is off-anchor, so this is the NORMAL case), a verdict_caveat that
    consumers must render next to the verdict."""
    bf = baseline_result.get("fit_statistics") or {}
    ef = extended_result.get("fit_statistics") or {}

    def _num(d: dict[str, Any], key: str) -> float | None:
        v = d.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v) if math.isfinite(float(v)) else None

    def _delta(key: str) -> float | None:
        b, e = _num(bf, key), _num(ef, key)
        return round(e - b, 4) if (b is not None and e is not None) else None

    base_model = str(baseline_result.get("model") or "lcdm")
    ext_model = str(extended_result.get("model") or "")
    delta_aic = _delta("aic")
    k_b, k_e = _num(bf, "n_parameters"), _num(ef, "n_parameters")

    # Δχ²/ΔAIC only mean anything when both fits used the SAME likelihood. Some
    # compressed datasets are model-DEPENDENT representations (planck2018_compressed
    # swaps its diagonal ΛCDM summary for the Chen-Huang-Wang distance prior on
    # extended flat-DE chains, which adds an ombh2 axis) — then the two chi2 are
    # computed against different data vectors and the comparison is invalid. Detect
    # it from the sampled axes: any difference beyond the extended model's own
    # DE/extension parameters means the representation changed underneath.
    model_extension_params = {"w", "w0", "wa", "omegak", "mnu"}
    base_axes = baseline_result.get("parameters")
    ext_axes = extended_result.get("parameters")
    comparison_warning = None

    def _input_quality(result: dict[str, Any]) -> dict[str, Any]:
        tier = result.get("chain_tier")
        diags = result.get("chain_diagnostics")
        diags = diags if isinstance(diags, dict) else {}

        def _finite(value: Any) -> float | None:
            # bool is an int subclass and NaN passes isinstance — both must
            # not count as a measured ESS.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            return float(value) if math.isfinite(float(value)) else None

        ess = _finite(diags.get("proposal_ess"))
        if ess is None:
            ess = _finite(diags.get("ess_bulk"))
        return {
            "tier": tier if isinstance(tier, str) else None,
            "ess": ess,
        }

    quality = {
        "baseline": _input_quality(baseline_result),
        "extended": _input_quality(extended_result),
    }
    blocked_inputs = [label for label, q in quality.items() if q["tier"] == "blocked"]
    unvetted_inputs = [
        label for label, q in quality.items()
        if q["tier"] not in {"publication", "exploratory", "blocked"}
    ]
    ess_unknown_inputs = [
        label for label, q in quality.items()
        if q["tier"] in {"publication", "exploratory"} and q["ess"] is None
    ]
    if blocked_inputs:
        plural = len(blocked_inputs) > 1
        comparison_warning = (
            "the " + " and ".join(blocked_inputs)
            + (" fits carry" if plural else " fit carries")
            + " chain_tier='blocked' (ESS collapse, no prior support, or "
            "unverifiable rows): "
            + ("their" if plural else "its")
            + " chi2/AIC values are not evidence, so no model-preference "
            "verdict can be read from this pair."
        )
    elif unvetted_inputs:
        comparison_warning = (
            "no chain_tier could be established for the "
            + " and ".join(unvetted_inputs)
            + " fit(s); refusing a preference verdict from unvetted fits."
        )
    elif ess_unknown_inputs:
        comparison_warning = (
            "convergence is unverified (no measurable ESS) for the "
            + " and ".join(ess_unknown_inputs)
            + " fit(s): the best-fit chi2 has no numerical guarantee, so no "
            "model-preference verdict can be read from this pair."
        )
    elif not (isinstance(base_axes, dict) and isinstance(ext_axes, dict)):
        # Fail closed: without both sampled-axis summaries the representation
        # check below cannot run, and that check is exactly what catches
        # model-dependent compressed swaps. Every legitimate comparable result
        # today carries the parameters dict.
        comparison_warning = (
            "sampled-axis summaries are missing on at least one side, so "
            "representation comparability could not be established; no "
            "model-preference verdict can be read from this pair."
        )
    if comparison_warning is None and isinstance(base_axes, dict) and isinstance(ext_axes, dict):
        extra_beyond_model = (set(ext_axes) - set(base_axes)) - model_extension_params
        missing_from_ext = set(base_axes) - set(ext_axes)
        if extra_beyond_model or missing_from_ext:
            comparison_warning = (
                "sampled axes differ beyond the extended model's own parameters "
                f"(extra: {sorted(extra_beyond_model)}, missing: {sorted(missing_from_ext)}): "
                "a selected dataset uses a model-dependent compressed representation, "
                "so the two chi2 are computed against different likelihoods and "
                "delta_chi2/delta_aic are not a valid model comparison."
            )

    if comparison_warning is not None or delta_aic is None:
        preferred = "undetermined"
    elif delta_aic < -2.0:
        preferred = ext_model
    elif delta_aic > 2.0:
        preferred = base_model
    else:
        preferred = "inconclusive"
    out = {
        "baseline_model": base_model,
        "extended_model": ext_model,
        "delta_chi2": _delta("chi2"),
        "delta_aic": delta_aic,
        "delta_bic": _delta("bic"),
        "n_extra_params": int(k_e - k_b) if (k_b is not None and k_e is not None) else None,
        "preferred": preferred,
        "comparison_valid": comparison_warning is None,
        "baseline_chain_tier": quality["baseline"]["tier"],
        "extended_chain_tier": quality["extended"]["tier"],
        "baseline_ess": quality["baseline"]["ess"],
        "extended_ess": quality["extended"]["ess"],
        "convention": "delta = extended - baseline; negative favors the extended model; |delta_aic|<2 is inconclusive",
    }
    if comparison_warning is not None:
        out["comparison_warning"] = comparison_warning
        # The deltas stay visible for diagnostics, but they were computed from
        # at least one fit whose chi2 is not evidence (or from two different
        # likelihoods) — they must never support a reply claim.
        out["__do_not_claim__"] = True
    else:
        exploratory_inputs = [
            label for label, q in quality.items() if q["tier"] == "exploratory"
        ]
        if exploratory_inputs:
            out["verdict_caveat"] = (
                "the " + " and ".join(exploratory_inputs) + " fit is "
                "exploratory-tier (off-anchor frontier model and/or ESS below "
                "the publication floor): present the preference as "
                "compressed-preliminary evidence with this caveat, never as a "
                "published-anchor result."
            )
    return out


def run_likelihood_chain(
    *,
    model: str,
    dataset_keys: list[str],
    priors: dict[str, Any] | None = None,
    random_seed: int | None = None,
    n_samples: int = 4000,
    allow_emcee_fallback: bool = False,
) -> dict[str, Any]:
    """Run the phase-1 compressed Gaussian cosmology likelihood.

    This combines registry entries with explicit ``CompressedLikelihoodSpec``
    summaries and the DESI DR1 public Gaussian BAO mean/covariance products.
    Full external likelihood packages are reported as not-run unless a dataset
    has an explicit registered compression.  Pantheon+ defaults to such an
    SN compressed-preliminary path; the full covariance χ² runner is opt-in
    via PANTHEON_PLUS_FULL_CHI2_ENABLED.
    """
    model_key = _validate_model(model)
    entries = _validate_dataset_selection(model_key, dataset_keys)
    seed = int(random_seed if random_seed is not None else 20260502)
    sample_count = max(256, min(int(n_samples or 4000), 20000))
    if any(
        _is_executable_bao_entry(entry)
        or _is_executable_cc_entry(entry)
        or _is_executable_rsd_entry(entry)
        or _is_executable_fsbao_entry(entry)
        or _is_executable_dr12_entry(entry)
        or _is_executable_sn_entry(entry)
        or _is_executable_des_sn_entry(entry)
        for entry in entries
    ):
        return _run_sampling_likelihood_chain(
            model_key=model_key,
            entries=entries,
            priors=priors,
            seed=seed,
            sample_count=sample_count,
            allow_emcee_fallback=allow_emcee_fallback,
        )

    # PART AI Phase 5 #2 Track 2 step 2: external_cobaya dispatch.
    # When EXTERNAL_COBAYA_ENABLED env is truthy AND every selected entry
    # has execution_mode="external_cobaya" AND cobaya is importable, hand
    # the run to the subprocess runner. Default off — the legacy
    # compressed-Gaussian path below remains the production behaviour, and
    # `cobaya_runner.dispatch_external_cobaya` itself returns a structured
    # NOT_PUB_READY envelope until step 3 ships the adapter resolver.
    from app.services import cobaya_runner

    if cobaya_runner.is_external_enabled() and _all_external_cobaya(entries):
        cobaya_param_order = _cobaya_parameter_order(model_key, entries)
        cobaya_priors = _sanitize_runner_priors(cobaya_param_order, priors)
        return cobaya_runner.dispatch_external_cobaya(
            model_key=model_key,
            entries=entries,
            prior_bounds=cobaya_priors,
            parameter_order=cobaya_param_order,
            seed=seed,
            sample_count=sample_count,
            # A real posterior, not a single evaluate-at-reference point. This is
            # the minutes-long heavy fit the EXTERNAL_COBAYA_ENABLED gate exists
            # for (it never runs on the interactive deadline path by default).
            sampler="mcmc",
        )

    compressed_entries = [entry for entry in entries if entry.compressed_likelihood is not None]
    skipped_entries = [entry for entry in entries if entry.compressed_likelihood is None]

    if not compressed_entries:
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason="No selected dataset has a registered compressed Gaussian likelihood.",
        )

    if model_key != "lcdm":
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason=(
                "Phase-1 compressed likelihoods are calibrated as ΛCDM summary "
                "constraints; extended-model parameters are genuinely sampled "
                "only on the external Cobaya CMB path: select the Planck 2018 "
                "likelihood datasets (planck_2018_highl_TTTEEE_lite + "
                "planck_2018_lowl_TT / planck_2018_lowl_EE, optionally "
                "planck_2018_lensing) with this same tool (requires "
                "EXTERNAL_COBAYA_ENABLED=true; off by default — a minutes-long "
                "fit), or run external Cobaya/CosmoSIS packages."
            ),
        )

    parameter_order = _compressed_parameter_order(compressed_entries)
    if not parameter_order:
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason="Selected compressed likelihoods contain no supported phase-1 parameters.",
        )
    prior_bounds = _sanitize_runner_priors(parameter_order, priors)
    precision = np.zeros((len(parameter_order), len(parameter_order)), dtype=float)
    information = np.zeros(len(parameter_order), dtype=float)
    invalid_specs: list[str] = []

    for entry in compressed_entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        try:
            params = list(spec.parameters)
            mean = np.asarray(spec.mean, dtype=float)
            cov = np.asarray(spec.covariance, dtype=float)
            if mean.shape != (len(params),) or cov.shape != (len(params), len(params)):
                raise ValueError("mean/covariance dimensions do not match parameters")
            cov_inv = np.linalg.inv(cov)
            idx = [parameter_order.index(param) for param in params if param in parameter_order]
            local_idx = [params.index(param) for param in params if param in parameter_order]
            if not idx:
                # B2: this dataset has no LINEAR (sampled) parameter, so the
                # precision matrix can't take it. A pure derived-S8 dataset
                # (e.g. WL S8) is EXPECTED here — it is folded in below via the
                # S8 importance-reweighting (_s8_gaussian_constraints), so it
                # must NOT be flagged. Only a dataset that NO path applies — a
                # BBN ombh2 prior, whose parameter is neither sampled nor
                # reweighted — would otherwise contribute χ²=0 silently while
                # still appearing in datasets_used as publication-ready; flag
                # that one so the publication gate demotes the run.
                applied_via_s8 = "S8" in params and _s8_is_derived(parameter_order)
                if not applied_via_s8:
                    invalid_specs.append(
                        f"{entry.key}: none of its parameters {params} are in the "
                        f"sampled parameter set {list(parameter_order)} and it has "
                        f"no S8 row to reweight, so it contributed no constraint — "
                        f"not applied as run."
                    )
                continue
            sub_inv = cov_inv[np.ix_(local_idx, local_idx)]
            sub_mean = mean[local_idx]
            precision[np.ix_(idx, idx)] += sub_inv
            information[idx] += sub_inv @ sub_mean
        except Exception as exc:
            invalid_specs.append(f"{entry.key}: {exc}")

    try:
        posterior_cov = np.linalg.inv(precision)
        posterior_mean = posterior_cov @ information
    except Exception as exc:
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason=f"Compressed likelihood precision matrix is singular ({exc}).",
        )

    prior_violations = [
        name
        for name, value in zip(parameter_order, posterior_mean, strict=True)
        if not (prior_bounds[name][0] <= float(value) <= prior_bounds[name][1])
    ]
    rng = np.random.default_rng(seed)
    # Apply any derived-S8 (σ8·√(Ωm/0.3)) constraints by importance-reweighting
    # the narrow closed-form proposal.  The linear precision matrix only saw the
    # σ8/Ωm rows; the WL/Planck S8 rows are folded in here on the *derived* value
    # so the sampler can never explore an S8 inconsistent with σ8/Ωm.
    s8_constraints = _s8_gaussian_constraints(compressed_entries)
    s8_ess: float | None = None
    if s8_constraints and _s8_is_derived(parameter_order):
        # The proposal is closed-form (cheap), so oversample a fixed pool: the
        # importance ESS then clears the 400 publication floor even for small
        # requested clouds and several S8 datasets in tension (planck+5WL ESS≈1.8k
        # at pool 8000 vs 233 at the bare sample_count of 1000).
        pool = max(sample_count, 8000)
        proposal = rng.multivariate_normal(posterior_mean, posterior_cov, size=pool)
        derived = _derived_s8_from_samples(proposal, parameter_order)
        log_w = np.zeros(pool, dtype=float)
        for mean_s8, sigma_s8 in s8_constraints:
            log_w += -0.5 * ((derived - mean_s8) / sigma_s8) ** 2
        log_w -= log_w.max()
        weights = np.exp(log_w)
        weights /= weights.sum()
        s8_ess = float(1.0 / np.sum(weights ** 2))
        samples = proposal[rng.choice(pool, size=sample_count, p=weights)]
    else:
        samples = rng.multivariate_normal(posterior_mean, posterior_cov, size=sample_count)
    summaries = {
        name: _posterior_summary(samples[:, index])
        for index, name in enumerate(parameter_order)
    }
    if _s8_is_derived(parameter_order):
        summaries["S8"] = _posterior_summary(
            _derived_s8_from_samples(samples, parameter_order)
        )
    chi2_point = samples.mean(axis=0) if s8_ess is not None else posterior_mean
    chi2 = _combined_chi2(compressed_entries, parameter_order, chi2_point)
    k = len(parameter_order)
    n_constraints = sum(
        len(entry.compressed_likelihood.parameters)
        for entry in compressed_entries
        if entry.compressed_likelihood is not None
    )
    aic = chi2 + 2.0 * k
    bic = chi2 + math.log(max(n_constraints, 1)) * k
    tensions = _pairwise_tensions(compressed_entries)
    result_hash = _config_hash(
        model_key,
        [entry.key for entry in compressed_entries],
        {name: prior_bounds[name] for name in parameter_order},
        f"compressed_gaussian:{seed}:{sample_count}",
    )
    warnings = [
        "Compressed Gaussian summary likelihood; use as preliminary consistency check, not full external likelihood.",
    ]
    warnings.extend(_combination_warnings(entries))
    if skipped_entries:
        warnings.append(
            "Datasets not run in compressed phase: "
            + ", ".join(entry.key for entry in skipped_entries)
            + ". Generate external Cobaya/CosmoSIS configs for those datasets."
        )
    if invalid_specs:
        warnings.extend(invalid_specs)
    if prior_violations:
        warnings.append("Posterior mean outside configured prior bounds for: " + ", ".join(prior_violations))
    s8_underpowered = s8_ess is not None and s8_ess < 400.0
    if s8_underpowered:
        warnings.append(
            f"Derived-S8 reweighting ESS={s8_ess:.0f} below the 400 publication "
            "floor; S8-combined posterior is exploratory only."
        )

    # Every executed compressed summary is a hand-typed Gaussian -> 'literature_typed'
    # (no released file to checksum); stamp it so a compressed-only chain is never
    # left with an unstamped (None) fidelity the publication gate would ignore.
    cov_fidelity, artifact_sha256, fidelity_ok = _finalize_cov_fidelity(
        compressed_entries, warnings
    )
    # Off-anchor guard, mirrored from the sampling path: an extended-model chain
    # whose frontier parameters (w/w0/wa) have no reproduced anchor is never
    # publication-ready.  This analytic branch is lcdm-only today (guarded
    # upstream), so chain_is_off_anchor short-circuits to False — a no-op now
    # that removes the latent asymmetry if that upstream guard is ever relaxed.
    from app.services.cosmology_oracle import chain_is_off_anchor
    off_anchor = chain_is_off_anchor(model_key, [entry.key for entry in compressed_entries])
    # do_not_combine_with violation double-counts shared measurements -> never
    # publication-ready (mirrors the sampling path).
    combination_conflict = bool(_combination_warnings(entries))
    publication_ready = (
        not invalid_specs
        and not prior_violations
        and not skipped_entries
        and not s8_underpowered
        and fidelity_ok
        and not off_anchor
        and not combination_conflict
    )
    # Compressed-Gaussian analytic path is otherwise binary by construction: the
    # posterior is closed-form so there is no "exploratory" intermediate (the
    # only soft gate is the derived-S8 reweighting ESS above).
    chain_tier = "publication" if publication_ready else "blocked"
    result: dict[str, Any] = {
        "success": True,
        "__tool_status__": "COMPLETED" if publication_ready else "PARTIAL",
        "analysis_status": "COMPRESSED_CHAIN_READY" if publication_ready else "PARTIAL",
        "publication_ready": publication_ready,
        "chain_tier": chain_tier,
        "off_anchor_review_required": off_anchor,
        "claim_scope": "compressed_likelihood_preliminary",
        "compressed_likelihood_preliminary": True,
        "model": model_key,
        "model_label": MODEL_LABELS[model_key],
        "sampler": "compressed_gaussian_analytic",
        "parameters": summaries,
        "posterior_summary": summaries,
        "derived_params": {
            name: summaries[name]
            for name in ("S8", "sigma8", "omegam", "H0")
            if name in summaries
        },
        "pairwise_tensions": tensions,
        "fit_statistics": {
            "chi2": round(float(chi2), 6),
            # delta_chi2 placeholder removed (2026-06-12) — see the sampling
            # runner's fit_statistics note; use compute_model_comparison.
            "aic": round(float(aic), 6),
            "bic": round(float(bic), 6),
            "n_constraints": int(n_constraints),
            "n_parameters": int(k),
        },
        "chain_diagnostics": {
            "overall_status": (
                "analytic_gaussian"
                if s8_ess is None
                else "analytic_gaussian_s8_reweighted"
            ),
            "publication_ready": publication_ready,
            # No sampling chains exist on the analytic path — R-hat is
            # undefined, not 1.0 (2026-06-12 review: the hard-coded value was
            # a never-computed statistic in the provenance envelope). The
            # exact-Gaussian draws ARE independent, so ess = n_draws is real.
            "rhat": None,
            "rhat_note": "not applicable (analytic Gaussian, no sampling chains)",
            "ess_bulk": int(round(s8_ess)) if s8_ess is not None else sample_count,
            "ess_source": (
                "importance_weights" if s8_ess is not None else "exact_gaussian_draws"
            ),
            "n_draws": sample_count,
            "n_chains": 1,
            "thresholds": {"ess_min": 400},
        },
        "datasets_used": [entry.to_dict() for entry in compressed_entries],
        "datasets_not_run": [entry.to_dict() for entry in skipped_entries],
        "dataset_keys": [entry.key for entry in entries],
        "priors": {name: list(bounds) for name, bounds in prior_bounds.items()},
        "random_seed": seed,
        "n_samples": sample_count,
        "runner_hash": result_hash,
        "warnings": warnings,
        "__message_to_model__": (
            "This is a publication-ready compressed-Gaussian preliminary result, "
            "not a full external likelihood run. You may quote posterior/tension "
            "numbers only with that caveat and only for datasets_used; do not "
            "claim that datasets_not_run were included in the numerical posterior."
        ),
        "provenance": {
            "cosmology_likelihood": {
                "registry_version": "2026-04-30",
                "runner": "compressed_gaussian_analytic",
                "runner_hash": result_hash,
                "dataset_keys": [entry.key for entry in entries],
                "datasets_used": [entry.key for entry in compressed_entries],
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
                    for entry in compressed_entries
                ],
                "cov_fidelity": cov_fidelity,
                "artifact_sha256": artifact_sha256,
                "publication_ready": publication_ready,
            },
        },
    }
    if not publication_ready:
        result["__do_not_claim__"] = True
        # bug_011 fix: per-tier rewrite of __message_to_model__ — the
        # publication-tier text ("you may quote posterior/tension numbers")
        # contradicts the chain_tier=blocked / __do_not_claim__ guardrail.
        # Mirrors the per-tier message handling in _run_sampling_likelihood_chain.
        if invalid_specs:
            blocked_reason = "Invalid compressed-likelihood specs: " + "; ".join(invalid_specs)
        elif skipped_entries:
            blocked_reason = (
                "Requested datasets were not numerically included: "
                + ", ".join(entry.key for entry in skipped_entries)
            )
        elif prior_violations:
            blocked_reason = (
                "Posterior mean outside configured prior bounds for: "
                + ", ".join(prior_violations)
            )
        else:
            blocked_reason = "Compressed-Gaussian analytic chain blocked"
        result["__message_to_model__"] = (
            blocked_reason
            + ". Do not cite H0, Om0, sigma8, S8, HDI, or posterior "
            "constraints from this result."
        )
    return result


def run_robustness_matrix(
    *,
    model: str,
    supernova_sets: list[str] | None = None,
    include_h0_prior: bool = True,
    include_weak_lensing: bool = False,
    random_seed: int | None = None,
    n_samples: int = 4000,
) -> dict[str, Any]:
    model_key = _validate_model(model)
    sn_keys = list(supernova_sets) if supernova_sets is not None else ["pantheon_plus"]
    if not sn_keys:
        sn_keys = ["pantheon_plus"]
    combos: list[tuple[str, list[str]]] = [
        ("BAO only", ["desi_dr1_bao"]),
        ("SN only", [sn_keys[0]]),
        ("CMB only", ["planck2018_compressed"]),
        ("BAO + CMB", ["desi_dr1_bao", "planck2018_compressed"]),
    ]
    if include_weak_lensing:
        combos.append((
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
            combos.append((f"{label} only", [sn_key]))
        combos.append((f"BAO + {label}", ["desi_dr1_bao", sn_key]))
        combos.append((f"{label} + CMB", [sn_key, "planck2018_compressed"]))
        combos.append((f"BAO + {label} + CMB", ["desi_dr1_bao", sn_key, "planck2018_compressed"]))
    if include_h0_prior:
        combos.extend([
            (label + " + SH0ES H0", keys + ["shoes_h0_riess22"])
            for label, keys in list(combos)
        ])

    matrix: list[dict[str, Any]] = []
    base_seed = int(random_seed if random_seed is not None else 20260502)
    for index, (label, keys) in enumerate(combos):
        try:
            run = run_likelihood_chain(
                model=model_key,
                dataset_keys=keys,
                random_seed=base_seed + index,
                n_samples=n_samples,
            )
            # PART AD: explicit per-cell status so the UI can tell an empty
            # cell (config-only / missing likelihood) apart from a negative
            # scientific result. `runnable` is kept for back-compat.
            if run.get("publication_ready"):
                cell_status = "runnable"
            else:
                _as = str(run.get("analysis_status") or "").upper()
                if "NO_COMPRESSED" in _as or "MISSING" in _as:
                    cell_status = "missing_likelihood"
                elif run.get("execution_status") == "not_run" or "CONFIG" in _as:
                    cell_status = "config_only"
                elif isinstance(run.get("chain_diagnostics"), dict):
                    cell_status = "executed_not_ready"
                else:
                    cell_status = "blocked"
            execution_level = (
                "compressed_preliminary"
                if cell_status == "runnable"
                else "executed_not_ready"
                if cell_status == "executed_not_ready"
                else "config_only"
                if cell_status == "config_only"
                else "not_available"
            )
            matrix.append({
                "label": label,
                "dataset_keys": keys,
                "runnable": bool(run.get("publication_ready")),
                "publication_ready": bool(run.get("publication_ready")),
                "status": cell_status,
                "execution_level": execution_level,
                "result": run,
                "warnings": run.get("warnings", []),
            })
        except Exception as exc:
            matrix.append({
                "label": label,
                "dataset_keys": keys,
                "runnable": False,
                "publication_ready": False,
                "status": "failed",
                "error": str(exc),
                "error_class": exc.__class__.__name__,
            })

    ready_cells = [row for row in matrix if row.get("publication_ready")]
    not_ready_labels = [
        row["label"] for row in matrix if row.get("status") == "executed_not_ready"
    ]
    matrix_message = (
        "Summarize only cells with publication_ready=true as compressed preliminary "
        "results. For non-runnable cells, say the external likelihood/config is still needed."
    )
    if not_ready_labels:
        matrix_message += (
            " Cells marked executed_not_ready ran but their importance-sampling ESS fell "
            "below the publication floor (typically 3+ probe products). Do not quote those "
            "cells directly; to obtain a quotable posterior for one, call "
            "run_cosmology_likelihood_chain on that single dataset combination — it "
            "auto-upgrades to compressed-emcee (~11 s) and can reach publication-ready ESS."
        )
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if ready_cells else "PARTIAL",
        "analysis_status": "COMPRESSED_ROBUSTNESS_READY" if ready_cells else "PARTIAL",
        "publication_ready": bool(ready_cells),
        "claim_scope": "compressed_likelihood_preliminary",
        "model": model_key,
        "matrix_size": len(matrix),
        "ready_cells": len(ready_cells),
        "include_weak_lensing": bool(include_weak_lensing),
        "matrix": matrix,
        "warnings": [
            "Robustness matrix uses compressed Gaussian summaries where available; config-only cells are not numerical evidence.",
        ],
        "__message_to_model__": matrix_message,
    }


def _is_executable_bao_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in _BAO_DATA


def _is_executable_cc_full_cov_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in COSMIC_CHRONOMETER_FULL_COV_KEYS


def _is_executable_cc_entry(entry: CosmologyDatasetEntry) -> bool:
    return (
        entry.key in COSMIC_CHRONOMETER_EXECUTABLE_KEYS
        or entry.key in COSMIC_CHRONOMETER_FULL_COV_KEYS
    )


def _is_executable_rsd_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in EBOSS_DR16_FSIGMA8_EXECUTABLE_KEYS


def _is_executable_fsbao_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in EBOSS_DR16_FSBAO_EXECUTABLE_KEYS


def _is_executable_dr12_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS


# M6 (2026-05-18): Pantheon+SH0ES Python chi² runner — bypasses external
# Cobaya for the SN-distance-modulus likelihood.  1701 SNe + full
# stat+sys covariance from the 2022 data release, loaded lazily from
# backend/data/pantheon_plus_2022/data.npz.  It is intentionally opt-in:
# default chat/research matrices use the fast registered compressed summary
# above so a multi-cell workflow does not hang on the full covariance χ².
PANTHEON_PLUS_FULL_CHI2_ENABLED = os.getenv("PANTHEON_PLUS_FULL_CHI2_ENABLED", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PANTHEON_PLUS_EXECUTABLE_KEYS = {"pantheon_plus"} if PANTHEON_PLUS_FULL_CHI2_ENABLED else set()

# DES-SN5YR full distance-modulus χ² is opt-in for the same reason as Pantheon+:
# the 1829×1829 covariance is slow per-sample, and the default research path uses
# the fast compressed Ωm summary. Off -> des_sn5yr stays the compressed Ωm entry.
DES_SN5YR_FULL_CHI2_ENABLED = os.getenv("DES_SN5YR_FULL_CHI2_ENABLED", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DES_SN5YR_EXECUTABLE_KEYS = {"des_sn5yr"} if DES_SN5YR_FULL_CHI2_ENABLED else set()

# Union3's full 22-bin binned-distance likelihood is ALWAYS on — no env flag.
# The DES flag above exists purely for the 1829x1829 per-sample cost; a 22x22
# covariance has no cost worth gating, and the default path SHOULD be the
# released likelihood, not the 1D compressed approximation (2026-06-12).
UNION3_EXECUTABLE_KEYS = frozenset({"union3"})

# ESS floor below which a single-cell chain auto-upgrades from importance
# sampling to compressed-emcee.  Multi-probe products (3+ likelihoods) push the
# joint posterior far narrower than any proposal Gaussian, so importance ESS
# collapses (measured: BAO+SN+CMB ESS≈39).  emcee ensemble sampling recovers it
# (≈780) in ~11 s, well inside the 45 s tool deadline.  Robustness/research
# matrices keep the fast importance path so they stay inside their own deadline.
_EMCEE_FALLBACK_ESS_FLOOR = 400.0


def _is_executable_sn_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in PANTHEON_PLUS_EXECUTABLE_KEYS


def _is_executable_des_sn_entry(entry: CosmologyDatasetEntry) -> bool:
    # The "des_sn" family/plumbing name now means "offset-marginalized binned
    # SN distance-modulus likelihood" — DES-SN5YR (env-gated) AND Union3
    # (always on). Same parameter footprint (omegam + w0/wa; H0/M_B
    # marginalized out), same chi2 form, per-key data dispatch.
    return entry.key in DES_SN5YR_EXECUTABLE_KEYS or entry.key in UNION3_EXECUTABLE_KEYS


def _sn_emcee_bypass_active(
    sn_entries: list[CosmologyDatasetEntry] | None,
    des_sn_entries: list[CosmologyDatasetEntry] | None,
    allow_emcee_fallback: bool,
) -> bool:
    """Whether SN entries replace importance sampling with the emcee bypass.

    Single source for the runner's sampler label AND the routing inside
    _draw_importance_posterior, so the two cannot drift apart. Expensive
    full-vector SN sets (Pantheon+ 1701, DES-SN5YR 1829 — both env-gated
    opt-ins) ALWAYS take emcee: no proposal Gaussian covers their posteriors
    and the per-draw cost is prohibitive. The cheap offset-marginalized set
    (Union3, 22x22) takes emcee only when the caller allows it — robustness
    matrices pass allow_emcee_fallback=False precisely so every cell stays
    inside the chat tool deadline, and a 22-bin chi2 importance-samples fine
    (2026-06-12 review: union3 cells were forcing 30-cell matrices to ~64 s,
    past the 45 s default deadline)."""
    if sn_entries and any(e.key == "pantheon_plus" for e in sn_entries):
        return True
    if any(e.key == "des_sn5yr" for e in (des_sn_entries or [])):
        return True
    return bool(des_sn_entries) and allow_emcee_fallback


def _run_sampling_likelihood_chain(
    *,
    model_key: str,
    entries: list[CosmologyDatasetEntry],
    priors: dict[str, Any] | None,
    seed: int,
    sample_count: int,
    allow_emcee_fallback: bool = False,
) -> dict[str, Any]:
    """Importance-sample executable low-dimensional likelihood products.

    DESI DR1 publishes a Gaussian BAO measurement vector and covariance.  We
    evaluate those raw data products directly against flat ΛCDM distance-ratio
    predictions, while still treating full desilike/Cobaya as the higher-fidelity
    second-stage likelihood.
    """
    # Curvature + neutrino-mass extensions still require external CAMB; the
    # phase-1 distance integral is hard-coded flat with massless neutrinos.
    if model_key.startswith("ok_") or model_key.endswith("_mnu"):
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason=(
                "The phase-1 in-process runner (BAO distance ratios, cosmic-"
                "chronometer H(z), RSD fσ8 growth) is flat-geometry, massless-"
                f"neutrino only — it never samples omegak or mnu, so running "
                f"'{model_key}' here would only relabel a ΛCDM-shaped chain. "
                "Curvature (ok_*) and neutrino-mass (*_mnu) extensions are "
                "genuinely sampled only on the external Cobaya CMB path: select "
                "the Planck 2018 likelihood datasets "
                "(planck_2018_highl_TTTEEE_lite + planck_2018_lowl_TT / "
                "planck_2018_lowl_EE, optionally planck_2018_lensing) with this "
                "same tool (requires EXTERNAL_COBAYA_ENABLED=true; off by "
                "default — a minutes-long fit). Caveat for ok_* models: primary "
                "CMB alone carries a geometric degeneracy in omegak, so treat "
                "CMB-only curvature posteriors accordingly."
            ),
        )

    bao_entries = [entry for entry in entries if _is_executable_bao_entry(entry)]
    cc_entries = [entry for entry in entries if _is_executable_cc_entry(entry)]
    rsd_entries = [entry for entry in entries if _is_executable_rsd_entry(entry)]
    fsbao_entries = [entry for entry in entries if _is_executable_fsbao_entry(entry)]
    dr12_entries = [entry for entry in entries if _is_executable_dr12_entry(entry)]
    sn_entries = [entry for entry in entries if _is_executable_sn_entry(entry)]
    des_sn_entries = [entry for entry in entries if _is_executable_des_sn_entry(entry)]
    # des_sn5yr, when its full χ² is enabled, runs the executable path — exclude it
    # from the compressed-summary set just like the other executable SN entries.
    executable_sn_keys = {entry.key for entry in sn_entries} | {entry.key for entry in des_sn_entries}
    compressed_entries = [
        entry
        for entry in entries
        if entry.compressed_likelihood is not None and entry.key not in executable_sn_keys
    ]
    executable_keys = (
        {e.key for e in bao_entries}
        | {e.key for e in sn_entries}
        | {e.key for e in cc_entries}
        | {e.key for e in rsd_entries}
        | {e.key for e in fsbao_entries}
        | {e.key for e in dr12_entries}
        | {e.key for e in des_sn_entries}
    )
    skipped_entries = [
        entry
        for entry in entries
        if entry.key not in executable_keys
        and entry.compressed_likelihood is None
    ]
    parameter_order = _sampling_parameter_order(
        bao_entries, compressed_entries, sn_entries, model_key=model_key,
        cc_entries=cc_entries, rsd_entries=rsd_entries, fsbao_entries=fsbao_entries,
        dr12_entries=dr12_entries, des_sn_entries=des_sn_entries,
    )
    if not parameter_order:
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason="Selected datasets contain no phase-1 executable likelihood parameters.",
        )

    prior_bounds = _sanitize_runner_priors(parameter_order, priors)
    rng = np.random.default_rng(seed)
    invalid_specs: list[str] = []
    # SN entries may take the emcee bypass inside _draw_importance_posterior
    # rather than importance sampling; the shared helper decides for BOTH the
    # label here and the routing there.
    _sn_emcee_bypass = _sn_emcee_bypass_active(
        sn_entries, des_sn_entries, allow_emcee_fallback
    )
    sampler_used = "sn_emcee" if _sn_emcee_bypass else "bao_gaussian_importance"

    try:
        # Fast analytic-grid BAO-only path is calibrated for flat ΛCDM in the
        # natural (H0, omegam, rd) plane against a single DESI BAO vector (DR1 or
        # DR2); wCDM / w0waCDM add extra dimensions and other BAO datasets have
        # their own vectors, so anything else falls through to the importance
        # sampler. Requires EXACTLY ONE bao entry so a DR1+DR2 (overlapping) mix
        # never fires it (which would silently fit only one of the two).
        if (
            len(bao_entries) == 1
            and bao_entries[0].key in _BAO_FAST_PATH_KEYS
            and not compressed_entries
            and not sn_entries
            and not cc_entries
            and not rsd_entries
            and not fsbao_entries
            and not dr12_entries
            and not des_sn_entries
            and parameter_order == ["H0", "omegam", "rd"]
        ):
            (
                posterior_samples,
                best_chi2,
                proposal_ess,
                proposal_draws,
            ) = _draw_desi_bao_only_posterior(
                rng,
                parameter_order,
                prior_bounds,
                sample_count,
                key=bao_entries[0].key,
            )
        else:
            (
                posterior_samples,
                best_chi2,
                proposal_ess,
                proposal_draws,
                compressed_errors,
            ) = _draw_importance_posterior(
                rng,
                parameter_order,
                prior_bounds,
                bao_entries,
                compressed_entries,
                sample_count,
                sn_entries=sn_entries,
                cc_entries=cc_entries,
                rsd_entries=rsd_entries,
                fsbao_entries=fsbao_entries,
                dr12_entries=dr12_entries,
                des_sn_entries=des_sn_entries,
                allow_emcee_fallback=allow_emcee_fallback,
            )
            invalid_specs.extend(compressed_errors)
            # ESS-floor emcee upgrade.  Importance sampling collapses on 3+
            # probe products and on extended-DE axes; emcee recovers a
            # quotable posterior in ~11 s.  Matrices pass
            # allow_emcee_fallback=False for ΛCDM baseline subset cells (they
            # stay fast) and True for the extended-model branch cells AND the
            # full-union ΛCDM comparison anchor (emcee-eligible cells are
            # budget-capped at 3 per matrix), which is why run_research_matrix
            # has its own 120 s tool deadline.
            # Requires every entry to have an executable chi²
            # (no skipped_entries) and ≥2 likelihood components.
            n_components = (
                len(bao_entries) + len(compressed_entries) + len(sn_entries)
                + len(cc_entries) + len(rsd_entries) + len(fsbao_entries)
                + len(dr12_entries)
                + len(des_sn_entries)
            )
            if (
                allow_emcee_fallback
                and proposal_ess < _EMCEE_FALLBACK_ESS_FLOOR
                and not skipped_entries
                and n_components >= 2
            ):
                try:
                    (
                        _em_samples,
                        _em_best_chi2,
                        _em_ess,
                        _em_draws,
                        emcee_errors,
                    ) = _run_emcee_chain(
                        seed,
                        parameter_order,
                        prior_bounds,
                        bao_entries,
                        compressed_entries,
                        sn_entries,
                        sample_count,
                        cc_entries=cc_entries,
                        rsd_entries=rsd_entries,
                        fsbao_entries=fsbao_entries,
                        dr12_entries=dr12_entries,
                        des_sn_entries=des_sn_entries,
                    )
                    # 2026-06-12 review: only adopt the emcee upgrade when its
                    # ESS estimate is finite AND an actual improvement. If the
                    # emcee autocorrelation estimate failed (NaN), a measured
                    # importance ESS — even a low one — is strictly more honest
                    # than an unverifiable chain.
                    if math.isfinite(_em_ess) and _em_ess > proposal_ess:
                        posterior_samples = _em_samples
                        best_chi2 = _em_best_chi2
                        proposal_ess = _em_ess
                        proposal_draws = _em_draws
                        invalid_specs.extend(emcee_errors)
                        sampler_used = "compressed_emcee"
                    else:
                        logger.warning(
                            "compressed-emcee fallback did not improve diagnostics "
                            "(ess=%s); keeping importance result", _em_ess,
                        )
                except Exception as exc:
                    logger.warning(
                        "compressed-emcee fallback failed (%s); keeping importance result",
                        exc,
                    )
    except Exception as exc:
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason=f"Executable compressed likelihood failed ({exc}).",
        )
    summaries = {
        name: _posterior_summary(posterior_samples[:, index])
        for index, name in enumerate(parameter_order)
    }
    derived_samples: dict[str, np.ndarray] = {}
    # S8 is reported as a derived quantity (σ8·√(Ωm/0.3)) rather than a sampled
    # column, so its posterior is exactly consistent with the σ8/Ωm posterior.
    if _s8_is_derived(parameter_order):
        derived_samples["S8"] = _derived_s8_from_samples(posterior_samples, parameter_order)
        summaries["S8"] = _posterior_summary(derived_samples["S8"])
    if "H0" in parameter_order and "rd" in parameter_order:
        h0 = posterior_samples[:, parameter_order.index("H0")]
        rd = posterior_samples[:, parameter_order.index("rd")]
        derived_samples["H0_rd"] = h0 * rd
    derived_summaries = {
        name: _posterior_summary(values)
        for name, values in derived_samples.items()
    }
    for name in ("H0", "omegam", "rd", "sigma8", "S8"):
        if name in summaries:
            derived_summaries[name] = summaries[name]

    used_keys = {
        entry.key
        for entry in bao_entries + compressed_entries + cc_entries + rsd_entries + fsbao_entries + dr12_entries + sn_entries + des_sn_entries
    }
    used_entries = [entry for entry in entries if entry.key in used_keys]
    # Each executable BAO entry contributes its own measurement vector (DESI DR1
    # = 12 points, 6dFGS+MGS = 2 points); cosmic chronometers contribute 31 H(z)
    # points; eBOSS RSD contributes 6 fσ8 points.  Derive from the vectors so a
    # dataset with a different length doesn't silently feed a wrong BIC penalty.
    n_constraints = (
        sum(len(_BAO_DATA[entry.key][0]) for entry in bao_entries)
        + sum(_cc_entry_point_count(entry) for entry in cc_entries)
        + len(EBOSS_DR16_FSIGMA8) * len(rsd_entries)
        + sum(len(_FSBAO_DATA[entry.key][0]) for entry in fsbao_entries)
        + sum(len(load_verified_dr12_consensus_data(entry.key)["mean_vector"] or ()) for entry in dr12_entries)
        + sum(len(_load_pantheon_plus_data()["mu"]) for entry in sn_entries if entry.key == "pantheon_plus")
        + sum(_offset_sn_n_points(entry.key) for entry in des_sn_entries)
        + sum(
            len(entry.compressed_likelihood.parameters)
            for entry in compressed_entries
            if entry.compressed_likelihood is not None
        )
    )
    k = len(parameter_order)
    aic = best_chi2 + 2.0 * k
    bic = best_chi2 + math.log(max(n_constraints, 1)) * k
    result_hash = _config_hash(
        model_key,
        [entry.key for entry in used_entries],
        {name: prior_bounds[name] for name in parameter_order},
        f"importance_bao:{seed}:{sample_count}:{proposal_draws}",
    )
    warnings = [
        (
            "In-process Gaussian mean/covariance runner (compressed-likelihood "
            "preliminary), not a full external desilike/Cobaya likelihood. See "
            "datasets_used for the exact release(s) fit."
        ),
    ]
    warnings.extend(_combination_warnings(entries))
    if (bao_entries or fsbao_entries or dr12_entries) and not any(entry.probe == "cmb" for entry in used_entries):
        warnings.append(
            "BAO-only H0 and rd constraints are prior/calibration dependent; "
            "quote Omega_m or H0*rd more strongly than H0 alone."
        )
    if skipped_entries:
        warnings.append(
            "Datasets not run in compressed phase: "
            + ", ".join(entry.key for entry in skipped_entries)
            + ". Generate external Cobaya/CosmoSIS configs for those datasets."
        )
    if invalid_specs:
        warnings.extend(invalid_specs)
    # ess_unknown: the emcee autocorrelation estimate failed (NaN channel from
    # _run_emcee_chain) — convergence is UNVERIFIED, which is different from
    # "low": it blocks publication and caps the tier at exploratory below.
    ess_unknown = not math.isfinite(proposal_ess)
    if ess_unknown:
        warnings.append(
            f"{sampler_used} effective-sample-size estimate unavailable "
            "(autocorrelation time could not be estimated — pathological or "
            "too-short chain). Convergence is UNVERIFIED; result capped at "
            "exploratory."
        )
    elif proposal_ess < 400.0:
        warnings.append(
            f"{sampler_used} ESS={proposal_ess:.1f} below publication threshold 400."
        )

    cov_fidelity, artifact_sha256, fidelity_ok = _finalize_cov_fidelity(
        bao_entries + cc_entries + rsd_entries + fsbao_entries + dr12_entries + sn_entries + des_sn_entries + compressed_entries, warnings
    )
    _executable_full_fidelity = not compressed_entries and cov_fidelity == "full"
    if _executable_full_fidelity:
        # Keep the headline warning consistent with the executable scope —
        # the generic text calls the run "compressed-likelihood preliminary",
        # which is false for a chain that fit only released, sha256-verified
        # full-fidelity products (2026-06-12 gate check).
        warnings[0] = (
            "In-process runner over released, sha256-verified full-fidelity "
            "likelihood products (no compressed Gaussian participated); still "
            "not an external desilike/Cobaya run. See datasets_used for the "
            "exact release(s) fit."
        )
    # Off-anchor safety guard: a converged extended-model chain whose novel frontier
    # parameters (w/w0/wa) have no reproduced published anchor is NOT publication-
    # ready — at most exploratory + routed to human review — so claim_validator
    # (posterior claims only when publication_ready) cannot let its w0/wa be quoted
    # as a published conclusion.  The chat tool runs these with emcee, so this guard
    # is what actually holds the "no off-anchor conclusions" line in the live path.
    from app.services.cosmology_oracle import chain_is_off_anchor
    off_anchor = chain_is_off_anchor(model_key, [entry.key for entry in used_entries])
    if off_anchor:
        warnings.append(
            "Off-anchor frontier parameters (w/w0/wa) have no reproduced published "
            "anchor; result is exploratory and routed to human review, not "
            "publication-ready."
        )
    # do_not_combine_with violation (e.g. the GA2018 31-pt CC compilation co-added
    # with its Moresco-2020 15-pt subset): the joint likelihood double-counts shared
    # measurements, so the posterior is methodologically invalid — block it outright
    # (not even exploratory), beyond the advisory _combination_warnings already added.
    combination_conflict = bool(_combination_warnings(entries))
    publication_ready = (
        not invalid_specs
        and not skipped_entries
        and proposal_ess >= 400.0
        and fidelity_ok
        and not off_anchor
        and not combination_conflict
    )
    # Importance-sampler three-tier (mirrors fit_cosmology_emcee, 2026-05-21):
    #   publication: ESS ≥ 400 and no invalid specs
    #   exploratory: 100 ≤ ESS < 400 — posterior discussable but not citeable
    #   blocked:     ESS < 100, invalid specs, a double-count conflict, OR data that
    #                failed sha256 verification (unverified/unstamped fidelity must
    #                never be discussable, not even as exploratory — anti-fabrication).
    if publication_ready:
        chain_tier = "publication"
    elif (
        fidelity_ok
        and not invalid_specs
        and not skipped_entries
        and not combination_conflict
        # ess_unknown (diagnostics failed) lands here, NOT in blocked: the
        # chain may be fine — only its convergence certificate is missing —
        # so the numbers stay discussable with the exploratory caveat.
        and (proposal_ess >= 100.0 or ess_unknown)
    ):
        chain_tier = "exploratory"
    else:
        chain_tier = "blocked"
    # Reason-aware exploratory warning: a chain lands in the exploratory tier
    # either because the importance-sampler ESS is below 400 OR because the
    # off-anchor frontier guard fired (w/w0/wa with no reproduced published
    # anchor) — and these are independent. The old string hard-coded
    # "ESS below 400" for every exploratory chain, which is literally false
    # when ESS >= 400 and the off-anchor guard is what demoted it.
    exploratory_reasons: list[str] = []
    if ess_unknown:
        exploratory_reasons.append(
            "the effective-sample-size estimate failed (autocorrelation time "
            "not estimable), so convergence is unverified"
        )
    elif proposal_ess < 400.0:
        exploratory_reasons.append(
            f"{sampler_used} ESS={proposal_ess:.0f} is below the publication threshold of 400"
        )
    if off_anchor:
        exploratory_reasons.append(
            "off-anchor frontier parameters (w/w0/wa) have no reproduced published anchor"
        )
    exploratory_warning = (
        "Exploratory chain (" + "; ".join(exploratory_reasons) + "). "
        "Posterior median and 1-sigma range may be discussed as exploratory, but MUST "
        "NOT be cited as a published constraint and MUST NOT be added to the bibcode pool."
        if chain_tier == "exploratory" else None
    )
    # Honest claim scope (2026-06-12): a chain where NO compressed Gaussian
    # participated and the aggregate fidelity is sha256-verified 'full' ran the
    # released likelihood products themselves — labeling it
    # 'compressed_likelihood_preliminary' was a lie that made the
    # full_likelihood_overclaim gate hard-block factually true replies (the
    # union3 22-bin upgrade exposed it; same F1-specificity class as 9f2667e).
    # (_executable_full_fidelity computed above, next to the headline warning.)
    # Tension diagnostics compare each dataset's PUBLISHED 1D anchor; executed
    # SN entries keep that anchor in compressed_likelihood even though it no
    # longer runs, so include them — otherwise union3 silently vanishes from
    # pairwise_tensions the moment it becomes executable.
    _tension_entries = compressed_entries + [
        e for e in (*sn_entries, *des_sn_entries)
        if e.compressed_likelihood is not None
    ]
    result: dict[str, Any] = {
        "success": True,
        "__tool_status__": (
            "COMPLETED" if chain_tier == "publication"
            else "EXPLORATORY" if chain_tier == "exploratory"
            else "PARTIAL"
        ),
        "analysis_status": (
            "COMPRESSED_CHAIN_READY" if chain_tier == "publication"
            else "EXPLORATORY" if chain_tier == "exploratory"
            else "PARTIAL"
        ),
        "publication_ready": publication_ready,
        "chain_tier": chain_tier,
        "off_anchor_review_required": off_anchor,
        "claim_scope": (
            "executable_full_fidelity_likelihoods"
            if _executable_full_fidelity
            else "compressed_likelihood_preliminary"
        ),
        "compressed_likelihood_preliminary": not _executable_full_fidelity,
        "model": model_key,
        "model_label": MODEL_LABELS[model_key],
        "sampler": sampler_used,
        "parameters": summaries,
        "posterior_summary": summaries,
        "derived_params": derived_summaries,
        "pairwise_tensions": _pairwise_tensions(_tension_entries),
        "fit_statistics": {
            "chi2": round(best_chi2, 6),
            # delta_chi2 was a hard-coded 0.0 placeholder here for years — a
            # meaningless number masquerading as a computed statistic ("wCDM
            # gives delta_chi2=0, no improvement"). Model comparison lives in
            # compute_model_comparison, which fits BOTH models on the SAME
            # datasets; a single run has no baseline to difference against.
            "aic": round(float(aic), 6),
            "bic": round(float(bic), 6),
            "n_constraints": int(n_constraints),
            "n_parameters": int(k),
        },
        "chain_diagnostics": {
            "overall_status": (
                "emcee_sampled" if sampler_used in ("compressed_emcee", "sn_emcee")
                else "importance_resampled"
            ),
            "publication_ready": publication_ready,
            # R-hat is NOT computed on this runner (importance resampling has
            # no MCMC chains; the emcee bypass returns one flattened ensemble).
            # It was hard-coded 1.0 for years — a never-computed convergence
            # statistic inside the provenance envelope (2026-06-12 review).
            # Per-parameter R-hat lives on the external cobaya path.
            "rhat": None,
            "rhat_note": "not computed on the in-process runner",
            "ess_bulk": (
                None if ess_unknown else int(min(round(proposal_ess), sample_count))
            ),
            "ess_source": (
                "autocorr_failed" if ess_unknown
                else (
                    "emcee_autocorr"
                    if sampler_used in ("compressed_emcee", "sn_emcee")
                    else "importance_weights"
                )
            ),
            "proposal_ess": None if ess_unknown else round(proposal_ess, 3),
            "proposal_draws": int(proposal_draws),
            "n_draws": sample_count,
            "n_chains": 1,
            "thresholds": {"ess_min": 400},
        },
        "datasets_used": [entry.to_dict() for entry in used_entries],
        "datasets_not_run": [entry.to_dict() for entry in skipped_entries],
        "dataset_keys": [entry.key for entry in entries],
        "priors": {name: list(bounds) for name, bounds in prior_bounds.items()},
        "random_seed": seed,
        "n_samples": sample_count,
        "runner_hash": result_hash,
        "warnings": warnings,
        "__message_to_model__": (
            "This is a publication-ready compressed-likelihood preliminary result "
            "when publication_ready=true. Quote posterior numbers only with that "
            "caveat and only for datasets_used; do not claim datasets_not_run were "
            "included in the numerical posterior."
        ),
        "provenance": {
            "cosmology_likelihood": {
                "registry_version": "2026-04-30",
                "runner": sampler_used,
                "runner_hash": result_hash,
                "dataset_keys": [entry.key for entry in entries],
                "datasets_used": [entry.key for entry in used_entries],
                "datasets_not_run": [entry.key for entry in skipped_entries],
                "citations": _collect_citations(entries),
                "compressed_sources": _sampling_source_records(used_entries),
                "cov_fidelity": cov_fidelity,
                "artifact_sha256": artifact_sha256,
                "publication_ready": publication_ready,
            },
        },
    }
    # Per-tier rewrite of __message_to_model__ — keep the publication text
    # only for the publication tier; for exploratory and blocked, replace
    # with tier-specific guidance (mirrors fit_cosmology_emcee per-tier
    # message handling; bug_013 review fix: otherwise the result dict's
    # publication-tier instructions contradict the chain_tier badge).
    if chain_tier == "exploratory" and exploratory_warning is not None:
        result["__exploratory_warning__"] = exploratory_warning
        result["warnings"] = list(result.get("warnings") or []) + [exploratory_warning]
        result["__message_to_model__"] = (
            exploratory_warning
            + " When reporting numbers, prefix with 'exploratory' and refuse "
            "phrasings like 'we find H0 =' or 'our constraint is'."
        )
    elif chain_tier == "blocked":
        result["__do_not_claim__"] = True
        blocked_reason = (
            f"Importance sampler ESS={proposal_ess:.0f} below exploratory floor 100"
            if not invalid_specs and proposal_ess < 100.0
            else "Requested datasets were not numerically included: "
            + ", ".join(entry.key for entry in skipped_entries)
            if skipped_entries
            else "Invalid compressed-likelihood specs: " + "; ".join(invalid_specs)
            if invalid_specs
            else "Chain did not reach a quotable posterior"
        )
        result["__message_to_model__"] = (
            blocked_reason
            + ". Do not cite H0, Om0, w0, wa, sigma8, S8, HDI, or posterior "
            "constraints from this result."
        )
    return result


def _sampling_parameter_order(
    bao_entries: list[CosmologyDatasetEntry],
    compressed_entries: list[CosmologyDatasetEntry],
    sn_entries: list[CosmologyDatasetEntry] | None = None,
    *,
    model_key: str = "lcdm",
    cc_entries: list[CosmologyDatasetEntry] | None = None,
    rsd_entries: list[CosmologyDatasetEntry] | None = None,
    fsbao_entries: list[CosmologyDatasetEntry] | None = None,
    dr12_entries: list[CosmologyDatasetEntry] | None = None,
    des_sn_entries: list[CosmologyDatasetEntry] | None = None,
) -> list[str]:
    order: list[str] = []
    if bao_entries:
        order.extend(["H0", "omegam", "rd"])
    if des_sn_entries:
        # Offset-marginalized SN family (DES-SN5YR, Union3): the χ² analytically
        # marginalizes the SN offset (and H0), so it constrains Ωm (+ the w0/wa
        # DE shape) only — no H0, no M_B.
        if "omegam" not in order:
            order.append("omegam")
    if fsbao_entries:
        # FSBAO measures joint (D_M/r_s, D_H/r_s, fσ8): distance ratios need
        # (H0, Ωm, r_d), the fσ8 growth term adds σ8.
        for param in ("H0", "omegam", "rd", "sigma8"):
            if param not in order:
                order.append(param)
    if dr12_entries:
        # DR12 consensus BAO measures (D_M·rs_fid/r_d, H·r_d/rs_fid) — pure
        # distance/expansion, same parameter set as BAO, no growth term.
        for param in ("H0", "omegam", "rd"):
            if param not in order:
                order.append(param)
    if cc_entries:
        # Cosmic chronometers measure H(z) = H0·E(z), constraining (H0, Ωm).
        for param in ("H0", "omegam"):
            if param not in order:
                order.append(param)
    if rsd_entries:
        # RSD fσ8(z) = f(z)·σ8·D(z)/D(0) is H0-independent; it constrains the
        # growth combination of (Ωm, σ8) [+ the DE EoS via γ(w)].
        for param in ("omegam", "sigma8"):
            if param not in order:
                order.append(param)
    if sn_entries:
        # Pantheon+ chi² needs (H0, Ωm, M_B). H0 / Ωm overlap with BAO/CMB;
        # M_B (SN Ia absolute-magnitude nuisance) is unique to SN.
        for param in ("H0", "omegam", "M_B"):
            if param not in order:
                order.append(param)
    # Dark-energy parameters per model. SUPPORTED_MODELS[model_key] lists the
    # canonical parameter set ("w" for wCDM, "w0"/"wa" for CPL).
    for param in SUPPORTED_MODELS.get(model_key, ()):
        if param in {"w", "w0", "wa"} and param not in order:
            order.append(param)
    for param in _compressed_parameter_order(compressed_entries):
        if param not in order:
            order.append(param)
    # The planck2018_compressed de_flat branch in _compressed_chi2_samples swaps the
    # geometric Planck spec for the (R, l_A, ombh2) distance prior on extended FLAT
    # dark-energy chains, and that prior reads an ombh2 column. ombh2 is deliberately
    # kept out of RUNNER_PARAMETER_PRIORS / _compressed_parameter_order, so add it here
    # only when that exact branch will fire — otherwise the prior is unreachable and
    # Planck silently contributes zero chi2. Keep this predicate in lockstep with the
    # de_flat gate in _compressed_chi2_samples.
    planck_de_flat = (
        any(e.key == "planck2018_compressed" for e in compressed_entries)
        and any(p in order for p in ("w", "w0", "wa"))
        and "omegak" not in order
    )
    if planck_de_flat and "ombh2" not in order:
        order.append("ombh2")
    preferred = ["H0", "omegam", "rd", "w", "w0", "wa", "sigma8", "S8", "M_B"]
    ordered = [param for param in preferred if param in order] + [
        param for param in order if param not in preferred
    ]
    return _drop_derived_s8(ordered)


def _draw_uniform_prior_samples(
    rng: np.random.Generator,
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    count: int,
) -> np.ndarray:
    samples = np.empty((count, len(parameter_order)), dtype=float)
    for index, name in enumerate(parameter_order):
        low, high = prior_bounds[name]
        samples[:, index] = rng.uniform(low, high, size=count)
    return samples


def _draw_desi_bao_only_posterior(
    rng: np.random.Generator,
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    sample_count: int,
    key: str = "desi_dr1_bao",
) -> tuple[np.ndarray, float, float, int]:
    """Sample a single DESI BAO-only posterior (DR1 or DR2) in the natural H0*rd
    degeneracy plane."""
    if parameter_order != ["H0", "omegam", "rd"]:
        raise ValueError("DESI BAO-only runner expects H0, omegam, rd parameters")
    h0_low, h0_high = prior_bounds["H0"]
    om_low, om_high = prior_bounds["omegam"]
    rd_low, rd_high = prior_bounds["rd"]
    q_low = h0_low * rd_low
    q_high = h0_high * rd_high
    # Grid resolution sets how faithfully the (Ωm, H0·rd) posterior is represented;
    # DESI DR2's tighter 13-point likelihood needs a denser grid than DR1's to keep
    # the effective sample size above the publication floor (the superseding, more
    # constraining dataset must not land in a weaker chain tier than DR1).
    om_values = np.linspace(om_low, om_high, 600)
    q_values = np.linspace(q_low, q_high, 1200)
    om_grid, q_grid = np.meshgrid(om_values, q_values, indexing="ij")
    flat_om = om_grid.ravel()
    flat_q = q_grid.ravel()

    h0_cond_low = np.maximum(h0_low, flat_q / rd_high)
    h0_cond_high = np.minimum(h0_high, flat_q / rd_low)
    valid = h0_cond_high > h0_cond_low
    if not np.any(valid):
        raise ValueError("H0*rd grid has no support inside the configured H0/rd priors")

    grid_samples = np.column_stack([
        np.ones(np.count_nonzero(valid), dtype=float),
        flat_om[valid],
        flat_q[valid],
    ])
    chi2 = _bao_chi2_samples(grid_samples, parameter_order, key)
    best_chi2 = float(np.min(chi2))
    marginal_h0_prior = np.log(h0_cond_high[valid] / h0_cond_low[valid])
    log_weights = -0.5 * (chi2 - best_chi2) + np.log(marginal_h0_prior)
    log_weights -= float(np.max(log_weights))
    weights = np.exp(np.clip(log_weights, -745.0, 0.0))
    weight_sum = float(np.sum(weights))
    if not math.isfinite(weight_sum) or weight_sum <= 0.0:
        raise ValueError("DESI BAO-only posterior weights underflowed")

    probabilities = weights / weight_sum
    proposal_ess = float(weight_sum * weight_sum / np.sum(weights * weights))
    chosen = rng.choice(grid_samples.shape[0], size=sample_count, replace=True, p=probabilities)
    chosen_q = grid_samples[chosen, 2]
    chosen_om = grid_samples[chosen, 1]
    low = np.maximum(h0_low, chosen_q / rd_high)
    high = np.minimum(h0_high, chosen_q / rd_low)
    chosen_h0 = np.exp(rng.uniform(np.log(low), np.log(high)))
    chosen_rd = chosen_q / chosen_h0
    posterior = np.column_stack([chosen_h0, chosen_om, chosen_rd])
    return posterior, best_chi2, proposal_ess, int(grid_samples.shape[0])


def _draw_gaussian_centered_proposal(
    rng: np.random.Generator,
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    compressed_entries: list[CosmologyDatasetEntry],
    proposal_count: int,
    *,
    inflation: float = 2.5,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Build a multivariate-Gaussian importance proposal centered on the
    tightest compressed-likelihood entry overlapping ``parameter_order``.

    Returns ``(samples, log_proposal_pdf)`` so the caller can apply the
    importance correction ``log_w = -0.5 chi² - log q(theta)``.

    Returns ``None`` when there is no compressed Gaussian to anchor on, or
    when the chosen Gaussian's prior overlap is too small after rejection
    (caller should fall back to uniform proposal).

    Picks the tightest entry by sum-of-normalized-variances (per-parameter
    σ relative to its prior box width) so we don't compare H0 σ in km/s/Mpc
    to Ωm σ in dimensionless.  Covariance is scaled by ``inflation² = 6.25``
    so each Gaussian dim has σ_proposal = 2.5·σ_target.  Per-dim importance
    efficiency ≈ σ_t/σ_p = 0.4; on prod's 4-Gaussian-dim path
    (Planck H0/Ωm/σ8/S8) this gives 0.4⁴ ≈ 2.5%, ESS ≈ 2500 from a 100k
    proposal.  Inflation=5 collapsed to 0.04% / ESS≈30 because
    (1/5)⁴ ≈ 1.6e-3.  BAO+Planck combined σ_H0 ≈ 0.5 (Planck-dominated),
    so 2.5σ_Planck = 1.35 still covers the joint posterior comfortably.
    """
    best_entry: CosmologyDatasetEntry | None = None
    best_trace = math.inf
    best_local_idx: list[int] = []
    best_sample_idx: list[int] = []
    best_names: list[str] = []

    for entry in compressed_entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        params = list(spec.parameters)
        names = [n for n in params if n in parameter_order]
        if not names:
            continue
        local_idx = [params.index(n) for n in names]
        cov = np.asarray(spec.covariance, dtype=float)[np.ix_(local_idx, local_idx)]
        scales = np.array(
            [prior_bounds[n][1] - prior_bounds[n][0] for n in names], dtype=float
        )
        if not np.all(scales > 0):
            continue
        normalized_diag = np.diagonal(cov) / (scales ** 2)
        trace_norm = float(np.sum(normalized_diag))
        if trace_norm < best_trace:
            best_entry = entry
            best_trace = trace_norm
            best_local_idx = local_idx
            best_sample_idx = [parameter_order.index(n) for n in names]
            best_names = names

    if best_entry is None:
        return None

    spec = best_entry.compressed_likelihood
    assert spec is not None  # narrowed by the search above
    gaussian_mean = np.asarray(spec.mean, dtype=float)[best_local_idx]
    gaussian_cov = np.asarray(spec.covariance, dtype=float)[
        np.ix_(best_local_idx, best_local_idx)
    ] * (inflation ** 2)
    # Numerical jitter so multivariate_normal doesn't refuse near-singular
    # covariance for one-parameter (1×1) Gaussians.
    gaussian_cov = gaussian_cov + np.eye(len(best_names)) * 1e-12

    sign, logdet = np.linalg.slogdet(gaussian_cov)
    if sign <= 0 or not math.isfinite(logdet):
        return None
    inv_cov = np.linalg.inv(gaussian_cov)

    # Reject anything outside the prior box.  A 5σ-inflated Gaussian centered
    # well inside the box should keep ≥80% of draws; if it does not (e.g.
    # Gaussian mean near the edge), bail and let the caller use uniform.
    over = max(int(proposal_count * 1.5), proposal_count + 1)
    samples = np.empty((over, len(parameter_order)), dtype=float)
    gaussian_draws = rng.multivariate_normal(gaussian_mean, gaussian_cov, size=over)
    for k, idx in enumerate(best_sample_idx):
        samples[:, idx] = gaussian_draws[:, k]
    for i, name in enumerate(parameter_order):
        if i in best_sample_idx:
            continue
        low, high = prior_bounds[name]
        samples[:, i] = rng.uniform(low, high, size=over)

    in_box = np.ones(over, dtype=bool)
    for i, name in enumerate(parameter_order):
        low, high = prior_bounds[name]
        in_box &= (samples[:, i] >= low) & (samples[:, i] <= high)
    samples = samples[in_box]
    if samples.shape[0] < proposal_count:
        return None
    samples = samples[:proposal_count]

    diffs = samples[:, best_sample_idx] - gaussian_mean
    log_q = (
        -0.5 * np.einsum("ni,ij,nj->n", diffs, inv_cov, diffs)
        - 0.5 * logdet
        - 0.5 * len(best_names) * np.log(2.0 * np.pi)
    )
    # Uniform-prior dimensions contribute a constant log(1/(high-low)) which
    # cancels out of the importance ratio; intentionally omitted.
    return samples, log_q


def _run_emcee_chain(
    seed: int,
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    bao_entries: list[CosmologyDatasetEntry],
    compressed_entries: list[CosmologyDatasetEntry],
    sn_entries: list[CosmologyDatasetEntry],
    target_sample_count: int,
    cc_entries: list[CosmologyDatasetEntry] | None = None,
    rsd_entries: list[CosmologyDatasetEntry] | None = None,
    fsbao_entries: list[CosmologyDatasetEntry] | None = None,
    dr12_entries: list[CosmologyDatasetEntry] | None = None,
    des_sn_entries: list[CosmologyDatasetEntry] | None = None,
) -> tuple[np.ndarray, float, float, int, list[str]]:
    """emcee MCMC over any BAO + compressed + SN likelihood product.

    Importance sampling collapses on tight posteriors — both Pantheon+'s
    1701-SN χ² ridge and 3+ probe products whose joint posterior is far
    narrower than any proposal Gaussian.  emcee ensemble sampling handles
    these naturally; at 32 walkers × ~1500 post-burn steps the posterior ESS
    is in the hundreds-to-thousands.  Used both for the full-χ² SN path and as
    the single-cell ESS-floor fallback for compressed multi-probe chains.

    Returns the same tuple as ``_draw_importance_posterior`` for drop-in
    substitution: ``(samples, best_chi2, effective_sample_size, n_draws,
    compressed_errors)``.
    """
    cc_entries = cc_entries or []
    rsd_entries = rsd_entries or []
    fsbao_entries = fsbao_entries or []
    des_sn_entries = des_sn_entries or []
    import emcee

    rng = np.random.default_rng(seed)
    ndim = len(parameter_order)
    n_walkers = max(2 * ndim + 2, 32)
    # Burn-in + post-burn budget.  Pantheon+'s χ² has integrated
    # autocorrelation time τ ≈ 25-30 steps, and emcee's documentation
    # recommends ≥ 50τ for trustworthy posterior — i.e. ≥ 1500 post-burn
    # steps per walker.  With 32 walkers that produces ~48k posterior
    # samples (ESS in the thousands after thinning).  Now that
    # _flat_lcdm_dm_grid_vectorized eliminated the 1701-call Python loop,
    # this completes in ~60-120s instead of 11 minutes.
    n_burn = 400
    n_steps = max(n_burn + 1500, n_burn + max(target_sample_count // n_walkers + 100, 1500))

    # Init walkers in a small ball around a sensible center.  Different
    # parameter names get different default centers because Pantheon+ /
    # Planck disagree on H0 — we sit between them.
    init_centers = {
        "H0": 70.0,
        "omegam": 0.31,
        "rd": 147.0,
        "M_B": -19.4,
        "sigma8": 0.81,
        "S8": 0.83,
    }
    center = np.empty(ndim, dtype=float)
    for i, name in enumerate(parameter_order):
        low, high = prior_bounds[name]
        candidate = init_centers.get(name, 0.5 * (low + high))
        center[i] = float(np.clip(candidate, low + 0.01 * (high - low), high - 0.01 * (high - low)))
    scale = np.array(
        [(prior_bounds[name][1] - prior_bounds[name][0]) * 0.02 for name in parameter_order],
        dtype=float,
    )
    p0 = center + rng.normal(size=(n_walkers, ndim)) * scale
    for i, name in enumerate(parameter_order):
        low, high = prior_bounds[name]
        p0[:, i] = np.clip(p0[:, i], low + 1e-4 * (high - low), high - 1e-4 * (high - low))

    compressed_errors: list[str] = []

    def log_prob_batch(theta_batch: np.ndarray) -> np.ndarray:
        if theta_batch.ndim == 1:
            theta_batch = theta_batch[None, :]
        n_w = theta_batch.shape[0]
        in_box = np.ones(n_w, dtype=bool)
        for i, name in enumerate(parameter_order):
            low, high = prior_bounds[name]
            in_box &= (theta_batch[:, i] >= low) & (theta_batch[:, i] <= high)
        result = np.full(n_w, -np.inf, dtype=float)
        if not np.any(in_box):
            return result
        valid = theta_batch[in_box]
        chi2 = np.zeros(valid.shape[0], dtype=float)
        for entry in bao_entries:
            chi2 += _bao_chi2_samples(valid, parameter_order, entry.key)
        for entry in cc_entries:
            if entry.key == "cosmic_chronometers":
                chi2 += _cosmic_chronometer_chi2_samples(valid, parameter_order)
            elif entry.key == "cosmic_chronometers_moresco20":
                chi2 += _cosmic_chronometer_moresco20_chi2_samples(valid, parameter_order)
            else:
                raise ValueError(f"executable CC entry {entry.key!r} has no chi2 dispatch")
        for entry in rsd_entries:
            if entry.key == "eboss_dr16_rsd":
                chi2 += _eboss_fsigma8_chi2_samples(valid, parameter_order)
            else:
                raise ValueError(f"executable RSD entry {entry.key!r} has no chi2 dispatch")
        for entry in fsbao_entries:
            chi2 += _fsbao_chi2_samples(valid, parameter_order, entry.key)
        for entry in dr12_entries:
            chi2 += _dr12_chi2_samples(valid, parameter_order, entry.key)
        for entry in sn_entries:
            if entry.key == "pantheon_plus":
                chi2 += _pantheon_plus_chi2_samples(valid, parameter_order)
            else:
                raise ValueError(f"executable SN entry {entry.key!r} has no chi2 dispatch")
        for entry in des_sn_entries:
            chi2 += _offset_sn_chi2_samples(valid, parameter_order, entry.key)
        extra_chi2, errs = _compressed_chi2_samples(
            valid, parameter_order, compressed_entries
        )
        chi2 += extra_chi2
        if errs:
            compressed_errors.extend(errs)
        result[in_box] = -0.5 * chi2
        return result

    sampler = emcee.EnsembleSampler(n_walkers, ndim, log_prob_batch, vectorize=True)
    # Seed the chain evolution deterministically (emcee otherwise draws stretch
    # moves from numpy's global RNG, making random_seed / runner_hash a false
    # reproducibility claim). Mirrors cosmology_mcmc.fit_cosmology_emcee.
    try:
        sampler.random_state = np.random.RandomState(seed).get_state()
    except Exception:
        logger.debug("emcee sampler random_state could not be set explicitly", exc_info=True)
    sampler.run_mcmc(p0, n_steps, progress=False)

    chain = sampler.get_chain(discard=n_burn, flat=True)  # (n_walkers*(n_steps-n_burn), ndim)
    log_probs = sampler.get_log_prob(discard=n_burn, flat=True)
    finite = np.isfinite(log_probs)
    chain = chain[finite]
    log_probs = log_probs[finite]
    if chain.shape[0] == 0:
        raise ValueError("emcee chain produced no finite log-probability draws")

    best_chi2 = float(-2.0 * np.max(log_probs))

    # Sub-sample if we have more than target.
    if chain.shape[0] > target_sample_count:
        idx = rng.choice(chain.shape[0], size=target_sample_count, replace=False)
        chain_out = chain[idx]
    else:
        chain_out = chain

    # Effective sample size — median across parameters using autocorrelation
    # length when available, else conservative fallback.
    try:
        tau = sampler.get_autocorr_time(quiet=True, discard=n_burn)
        n_draws_total = (n_steps - n_burn) * n_walkers
        # get_autocorr_time returns NaN for (near-)zero-variance parameters
        # (e.g. a walker pinned to a prior edge). np.max would propagate the
        # NaN into ess_estimate and later crash int(round(nan)) at the caller.
        max_tau = float(np.nanmax(tau))
        if not math.isfinite(max_tau) or max_tau <= 0:
            raise ValueError("non-finite autocorrelation time")
        ess_estimate = float(n_draws_total / max(max_tau, 1.0))
    except Exception:
        # 2026-06-12 review: the old n/10 fallback (~4800 for a default chain)
        # sailed OVER the 400 publication floor at the exact moment the ESS
        # measurement FAILED — promoting an unverifiable chain to the highest
        # confidence tier. The honest value is "unknown": NaN propagates to the
        # runner, which reports ess=None, warns, and caps the tier at
        # exploratory (never publication).
        ess_estimate = float("nan")
    return chain_out, best_chi2, ess_estimate, int(chain.shape[0]), list(set(compressed_errors))


def _draw_importance_posterior(
    rng: np.random.Generator,
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    bao_entries: list[CosmologyDatasetEntry],
    compressed_entries: list[CosmologyDatasetEntry],
    sample_count: int,
    *,
    sn_entries: list[CosmologyDatasetEntry] | None = None,
    cc_entries: list[CosmologyDatasetEntry] | None = None,
    rsd_entries: list[CosmologyDatasetEntry] | None = None,
    fsbao_entries: list[CosmologyDatasetEntry] | None = None,
    dr12_entries: list[CosmologyDatasetEntry] | None = None,
    des_sn_entries: list[CosmologyDatasetEntry] | None = None,
    allow_emcee_fallback: bool = True,
) -> tuple[np.ndarray, float, float, int, list[str]]:
    sn_entries = sn_entries or []
    cc_entries = cc_entries or []
    rsd_entries = rsd_entries or []
    fsbao_entries = fsbao_entries or []
    dr12_entries = dr12_entries or []
    des_sn_entries = des_sn_entries or []
    # SN paths may bypass importance sampling with emcee — see
    # _sn_emcee_bypass_active for the policy (expensive full vectors always;
    # the cheap union3 22-bin set only when the caller allows emcee).
    if _sn_emcee_bypass_active(sn_entries, des_sn_entries, allow_emcee_fallback):
        # Use the rng's bit-state to seed emcee deterministically.
        emcee_seed = int(rng.integers(0, 2**31 - 1))
        return _run_emcee_chain(
            emcee_seed,
            parameter_order,
            prior_bounds,
            bao_entries,
            compressed_entries,
            sn_entries,
            sample_count,
            cc_entries=cc_entries,
            rsd_entries=rsd_entries,
            fsbao_entries=fsbao_entries,
            dr12_entries=dr12_entries,
            des_sn_entries=des_sn_entries,
        )

    proposal_count = min(max(sample_count * 25, 80_000), 300_000)

    # No SN entries: legacy importance-sampling path (delivers ESS > 400 for
    # BAO+CMB workflows since the W2 fix in commit 6c829df).
    # Gaussian-centered single-component proposal when any compressed entry
    # has a Gaussian likelihood overlapping the sampling parameters;
    # otherwise fall back to uniform-prior proposal.
    gaussian_proposal = _draw_gaussian_centered_proposal(
        rng, parameter_order, prior_bounds, compressed_entries, proposal_count,
    )
    if gaussian_proposal is not None:
        samples, log_proposal_pdf = gaussian_proposal
    else:
        samples = _draw_uniform_prior_samples(
            rng, parameter_order, prior_bounds, proposal_count
        )
        log_proposal_pdf = np.zeros(samples.shape[0], dtype=float)

    chi2 = np.zeros(samples.shape[0], dtype=float)
    for entry in bao_entries:
        chi2 += _bao_chi2_samples(samples, parameter_order, entry.key)
    for entry in cc_entries:
        if entry.key == "cosmic_chronometers":
            chi2 += _cosmic_chronometer_chi2_samples(samples, parameter_order)
        elif entry.key == "cosmic_chronometers_moresco20":
            chi2 += _cosmic_chronometer_moresco20_chi2_samples(samples, parameter_order)
        else:
            raise ValueError(f"executable CC entry {entry.key!r} has no chi2 dispatch")
    for entry in rsd_entries:
        if entry.key == "eboss_dr16_rsd":
            chi2 += _eboss_fsigma8_chi2_samples(samples, parameter_order)
        else:
            raise ValueError(f"executable RSD entry {entry.key!r} has no chi2 dispatch")
    for entry in fsbao_entries:
        chi2 += _fsbao_chi2_samples(samples, parameter_order, entry.key)
    for entry in (dr12_entries or []):
        chi2 += _dr12_chi2_samples(samples, parameter_order, entry.key)
    for entry in (sn_entries or []):
        if entry.key == "pantheon_plus":
            chi2 += _pantheon_plus_chi2_samples(samples, parameter_order)
        else:
            raise ValueError(f"executable SN entry {entry.key!r} has no chi2 dispatch")
    for entry in (des_sn_entries or []):
        chi2 += _offset_sn_chi2_samples(samples, parameter_order, entry.key)
    extra_chi2, compressed_errors = _compressed_chi2_samples(
        samples,
        parameter_order,
        compressed_entries,
    )
    chi2 += extra_chi2
    finite = np.isfinite(chi2) & np.isfinite(log_proposal_pdf)
    if not np.any(finite):
        raise ValueError("importance sampler produced no finite likelihood values")
    samples = samples[finite]
    chi2 = chi2[finite]
    log_proposal_pdf = log_proposal_pdf[finite]
    best_chi2 = float(np.min(chi2))
    if best_chi2 >= 0.5 * MGS_OUT_OF_BOUNDS_CHI2:
        # Every proposal sample carries an out-of-bounds likelihood penalty
        # (e.g. the whole prior volume maps outside the MGS chi2(alpha) table
        # range).  A CONSTANT penalty cancels in the normalized weights below,
        # so continuing would silently drop that constraint and report a
        # posterior shaped only by the remaining data — refuse loudly instead
        # (cobaya's equivalent is logp = -inf everywhere).
        raise ValueError(
            "no importance-sampling proposal has likelihood support: every "
            "sample hit an out-of-bounds penalty (best chi2 = "
            f"{best_chi2:.3g}); refusing to report a posterior that would "
            "silently drop that dataset's constraint. Check the prior ranges "
            "against the dataset's tabulated support."
        )
    # Importance weight: w = p(theta|data) / q(theta).  The numerator is
    # exp(-0.5 chi²); the denominator is the proposal pdf q.  Working in log-
    # space and subtracting the max keeps the exp() inside float range.
    log_weights = -0.5 * (chi2 - best_chi2) - log_proposal_pdf
    log_weights -= float(np.max(log_weights))
    weights = np.exp(np.clip(log_weights, -745.0, 0.0))
    weight_sum = float(np.sum(weights))
    if not math.isfinite(weight_sum) or weight_sum <= 0.0:
        raise ValueError("importance sampler weights underflowed")
    probabilities = weights / weight_sum
    proposal_ess = float(weight_sum * weight_sum / np.sum(weights * weights))
    posterior_indices = rng.choice(samples.shape[0], size=sample_count, replace=True, p=probabilities)
    posterior_samples = samples[posterior_indices]
    return posterior_samples, best_chi2, proposal_ess, int(samples.shape[0]), compressed_errors


def _bao_chi2_samples(
    samples: np.ndarray, parameter_order: list[str], key: str = "desi_dr1_bao"
) -> np.ndarray:
    if key == "sdss_6df_bao":
        # MGS half runs on the released non-Gaussian chi2(alpha) table
        # (2026-06-12 fidelity upgrade); 6dFGS half stays Gaussian.
        return _sdss_6df_mgs_chi2_samples(samples, parameter_order)
    if load_verified_bao_data(key)["cov_fidelity"] == "unverified":
        raise ValueError(
            f"BAO {key} covariance failed sha256 verification (or its vendored "
            "file is missing); refusing to compute chi2 from unverified data."
        )
    mean_vector, cov = _BAO_DATA[key]
    observed = np.asarray([row[1] for row in mean_vector], dtype=float)
    covariance = np.asarray(cov, dtype=float)
    predictions = _bao_predictions(samples, parameter_order, mean_vector)
    residual = predictions - observed
    return np.einsum("ni,ij,nj->n", residual, np.linalg.inv(covariance), residual)


def _bao_predictions(
    samples: np.ndarray,
    parameter_order: list[str],
    mean_vector: tuple[tuple[float, float, str], ...],
) -> np.ndarray:
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    rd = samples[:, parameter_order.index("rd")]
    # wCDM / w0waCDM extensions — read w/w0/wa per sample when present in
    # parameter_order. "w" is the single-parameter wCDM equation of state; it
    # maps to (w0=w, wa=0). When neither is in the parameter_order this is the
    # flat-ΛCDM (-1, 0) limit and the predictions match the legacy path.
    n_samples = samples.shape[0]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    if "wa" in parameter_order:
        wa = samples[:, parameter_order.index("wa")]
    else:
        wa = np.zeros(n_samples, dtype=float)
    predictions = np.empty((n_samples, len(mean_vector)), dtype=float)
    distance_cache: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for col, (z, _value, quantity) in enumerate(mean_vector):
        if z not in distance_cache:
            distance_cache[z] = _flat_de_distances_at_z(z, h0, omegam, w0=w0, wa=wa)
        dm, dh, dv = distance_cache[z]
        if quantity in {"DM_over_rs", "DM_over_rd"}:
            predictions[:, col] = dm / rd
        elif quantity in {"DH_over_rs", "DH_over_rd"}:
            predictions[:, col] = dh / rd
        elif quantity in {"DV_over_rs", "DV_over_rd"}:
            predictions[:, col] = dv / rd
        else:
            raise ValueError(f"unsupported BAO quantity {quantity!r}")
    return predictions


def _fsbao_predictions(
    samples: np.ndarray,
    parameter_order: list[str],
    mean_vector: tuple[tuple[float, float, str], ...],
) -> np.ndarray:
    """Predicted joint (D_M/r_s, D_H/r_s, fσ8) vector for the eBOSS DR16 FSBAO
    likelihoods.  Distance ratios reuse the flat-w0waCDM BAO kernel (/r_d, where
    r_s≡r_d); fσ8 reuses the RSD growth kernel (f(z)·σ8·D(z)/D(0))."""
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    rd = samples[:, parameter_order.index("rd")]
    sigma8 = samples[:, parameter_order.index("sigma8")]
    n_samples = samples.shape[0]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else np.zeros(n_samples)
    predictions = np.empty((n_samples, len(mean_vector)), dtype=float)
    distance_cache: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for col, (z, _value, quantity) in enumerate(mean_vector):
        if quantity in {"DM_over_rs", "DM_over_rd", "DH_over_rs", "DH_over_rd", "DV_over_rs", "DV_over_rd"}:
            if z not in distance_cache:
                distance_cache[z] = _flat_de_distances_at_z(z, h0, omegam, w0=w0, wa=wa)
            dm, dh, dv = distance_cache[z]
            if quantity.startswith("DM"):
                predictions[:, col] = dm / rd
            elif quantity.startswith("DH"):
                predictions[:, col] = dh / rd
            else:
                predictions[:, col] = dv / rd
        elif quantity in {"f_sigma8", "fsigma8"}:
            f_z = _growth_rate_f(z, omegam, w0, wa)
            d_ratio = _growth_factor_ratio(z, omegam, w0, wa)
            predictions[:, col] = f_z * sigma8 * d_ratio
        else:
            raise ValueError(f"unsupported FSBAO quantity {quantity!r}")
    return predictions


def _fsbao_chi2_samples(
    samples: np.ndarray, parameter_order: list[str], key: str
) -> np.ndarray:
    """Full-covariance χ² = rᵀ C⁻¹ r of an eBOSS DR16 FSBAO joint vector."""
    verified = load_verified_fsbao_data(key)
    if verified["cov_fidelity"] == "unverified" or verified["covariance"] is None:
        raise ValueError(
            f"FSBAO {key} covariance failed sha256 verification (or its vendored "
            "file is missing); refusing to compute chi2 from unverified data."
        )
    mean_vector, cov = verified["mean_vector"], verified["covariance"]
    observed = np.asarray([row[1] for row in mean_vector], dtype=float)
    predictions = _fsbao_predictions(samples, parameter_order, mean_vector)
    residual = predictions - observed
    return np.einsum("ni,ij,nj->n", residual, np.linalg.inv(np.asarray(cov, dtype=float)), residual)


def _dr12_consensus_predictions(
    samples: np.ndarray,
    parameter_order: list[str],
    mean_vector: tuple[tuple[float, float, str], ...],
) -> np.ndarray:
    """Predicted BOSS DR12 consensus vector in the release's DIMENSIONAL
    storage convention (cobaya bao.sdss_dr12_consensus_bao, rs_fid = 147.78):

      DM_over_rs row:  D_M(z) · (rs_fid / r_d)   [Mpc]
      bao_Hz_rs  row:  H(z)  · (r_d / rs_fid)    [km/s/Mpc]

    Mirrors cobaya's theory_fun with rs_rescale = 1/rs_fid exactly; H(z) is
    recovered from the flat-w0waCDM kernel's Hubble distance D_H = c/H."""
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    rd = samples[:, parameter_order.index("rd")]
    n_samples = samples.shape[0]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else np.zeros(n_samples)
    predictions = np.empty((n_samples, len(mean_vector)), dtype=float)
    distance_cache: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for col, (z, _value, quantity) in enumerate(mean_vector):
        if z not in distance_cache:
            distance_cache[z] = _flat_de_distances_at_z(z, h0, omegam, w0=w0, wa=wa)
        dm, dh, _dv = distance_cache[z]
        if quantity == "DM_over_rs":
            predictions[:, col] = dm * (SDSS_DR12_RS_FID_MPC / rd)
        elif quantity == "bao_Hz_rs":
            predictions[:, col] = (C_LIGHT_KM_S / dh) * (rd / SDSS_DR12_RS_FID_MPC)
        else:
            raise ValueError(f"unsupported DR12 consensus quantity {quantity!r}")
    return predictions


def _dr12_chi2_samples(
    samples: np.ndarray, parameter_order: list[str], key: str
) -> np.ndarray:
    """Full-covariance χ² = rᵀ C⁻¹ r of the BOSS DR12 consensus BAO vector."""
    verified = load_verified_dr12_consensus_data(key)
    if verified["cov_fidelity"] == "unverified" or verified["covariance"] is None:
        raise ValueError(
            f"DR12 consensus {key} covariance failed sha256 verification (or its "
            "vendored file is missing); refusing to compute chi2 from unverified data."
        )
    mean_vector, cov = verified["mean_vector"], verified["covariance"]
    observed = np.asarray([row[1] for row in mean_vector], dtype=float)
    predictions = _dr12_consensus_predictions(samples, parameter_order, mean_vector)
    residual = predictions - observed
    return np.einsum("ni,ij,nj->n", residual, np.linalg.inv(np.asarray(cov, dtype=float)), residual)


def _hz_predictions_for(
    samples: np.ndarray, parameter_order: list[str],
    vector: tuple[tuple[float, float, float], ...],
) -> np.ndarray:
    """Predicted H(z) [km/s/Mpc] for each posterior sample at the redshifts of
    ``vector`` (rows ``(z, H_obs, σ)``).  H(z) = H0·E(z) is closed-form for the
    flat w0waCDM kernel — no comoving-distance integral needed."""
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    n_samples = samples.shape[0]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else np.zeros(n_samples)
    predictions = np.empty((n_samples, len(vector)), dtype=float)
    for col, (z, _h_obs, _sigma) in enumerate(vector):
        rho_de = _de_energy_density(1.0 / (1.0 + z), w0, wa)
        ez = np.sqrt(omegam * (1.0 + z) ** 3 + (1.0 - omegam) * rho_de)
        predictions[:, col] = h0 * ez
    return predictions


def _cosmic_chronometer_hz_predictions(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """Predicted H(z) at the 31 GA2018 cosmic-chronometer redshifts."""
    return _hz_predictions_for(samples, parameter_order, COSMIC_CHRONOMETER_HZ)


def _cosmic_chronometer_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """Diagonal-covariance χ² of the 31-point cosmic-chronometer H(z) vector.

    Gómez-Valent & Amendola 2018 use a diagonal covariance for this compilation,
    so χ² = Σ_i ((H_pred(z_i) − H_obs_i) / σ_i)²."""
    observed = np.asarray([row[1] for row in COSMIC_CHRONOMETER_HZ], dtype=float)
    sigma = np.asarray([row[2] for row in COSMIC_CHRONOMETER_HZ], dtype=float)
    predictions = _cosmic_chronometer_hz_predictions(samples, parameter_order)
    residual = predictions - observed
    return np.sum((residual / sigma) ** 2, axis=1)


def _cosmic_chronometer_moresco20_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """Full-covariance χ² of the 15-point Moresco 2020 cosmic-chronometer H(z)
    vector: χ² = rᵀ C⁻¹ r with the reproduced systematic covariance C."""
    if (
        load_verified_cc_full_cov_data("cosmic_chronometers_moresco20")["cov_fidelity"] == "unverified"
        or _MORESCO20_COV_INV is None
        or not COSMIC_CHRONOMETER_MORESCO20_HZ
    ):
        raise ValueError(
            "Moresco-2020 CC covariance failed sha256 verification (or its vendored "
            "file is missing); refusing to compute chi2 from unverified data."
        )
    observed = np.asarray([row[1] for row in COSMIC_CHRONOMETER_MORESCO20_HZ], dtype=float)
    predictions = _hz_predictions_for(samples, parameter_order, COSMIC_CHRONOMETER_MORESCO20_HZ)
    residual = predictions - observed
    return np.einsum("ni,ij,nj->n", residual, _MORESCO20_COV_INV, residual)


# ── Structure-growth kernel for RSD fσ8 (Linder γ-parametrisation) ──────────
# 32-node Gauss-Legendre rule for the growth-factor integral, computed once.
_GROWTH_GL32_NODES, _GROWTH_GL32_WEIGHTS = np.polynomial.legendre.leggauss(32)


def _growth_index_gamma(w0: np.ndarray, wa: np.ndarray) -> np.ndarray:
    """Growth index γ (Linder & Cahn 2007).  ΛCDM → 0.55.  With CPL w(z=1) =
    w0 + wa·(1−a) at a=0.5: γ = 0.55 + 0.05[1+w(z=1)] for w(z=1) ≥ −1, else
    γ = 0.55 + 0.02[1+w(z=1)] (the phantom-side slope)."""
    w_z1 = w0 + 0.5 * wa
    return np.where(
        w_z1 >= -1.0,
        0.55 + 0.05 * (1.0 + w_z1),
        0.55 + 0.02 * (1.0 + w_z1),
    )


def _omega_m_of_z(
    z: float, omegam: np.ndarray, w0: np.ndarray, wa: np.ndarray
) -> np.ndarray:
    """Matter density fraction Ωm(z) = Ωm(1+z)³ / E²(z) for flat w0waCDM."""
    one_plus_z = 1.0 + z
    rho_de = _de_energy_density(1.0 / one_plus_z, w0, wa)
    ez2 = omegam * one_plus_z ** 3 + (1.0 - omegam) * rho_de
    return omegam * one_plus_z ** 3 / ez2


def _growth_rate_f(
    z: float, omegam: np.ndarray, w0: np.ndarray, wa: np.ndarray
) -> np.ndarray:
    """Linear growth rate f(z) = Ωm(z)^γ."""
    return _omega_m_of_z(z, omegam, w0, wa) ** _growth_index_gamma(w0, wa)


def _growth_factor_ratio(
    z: float, omegam: np.ndarray, w0: np.ndarray, wa: np.ndarray
) -> np.ndarray:
    """Normalised linear growth factor D(z)/D(0) = exp(−∫₀^z f(z')/(1+z') dz').

    f = dlnD/dlna ⇒ dlnD/dz = −f/(1+z); 32-point Gauss-Legendre quadrature.
    Sample arrays (omegam, w0, wa) are (N,); returns (N,)."""
    if z <= 0.0:
        return np.ones_like(omegam)
    nodes, weights = _GROWTH_GL32_NODES, _GROWTH_GL32_WEIGHTS
    zp = 0.5 * z * (nodes + 1.0)                                    # (K,)
    one_plus_zp = 1.0 + zp                                          # (K,)
    rho_de = _de_energy_density(
        1.0 / one_plus_zp[None, :], w0[:, None], wa[:, None]
    )                                                              # (N,K)
    ez2 = omegam[:, None] * one_plus_zp[None, :] ** 3 + (1.0 - omegam[:, None]) * rho_de
    omega_m_zp = omegam[:, None] * one_plus_zp[None, :] ** 3 / ez2  # (N,K)
    gamma = _growth_index_gamma(w0, wa)[:, None]                    # (N,1)
    integrand = omega_m_zp ** gamma / one_plus_zp[None, :]          # (N,K)
    integral = 0.5 * z * np.sum(weights[None, :] * integrand, axis=1)  # (N,)
    return np.exp(-integral)


def _eboss_fsigma8_predictions(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """Predicted fσ8(z) = f(z)·σ8·D(z)/D(0) at the 6 eBOSS RSD effective
    redshifts for each posterior sample.  Needs omegam + sigma8 in parameter
    order (added for RSD selections); fσ8 is H0-independent."""
    omegam = samples[:, parameter_order.index("omegam")]
    sigma8 = samples[:, parameter_order.index("sigma8")]
    n_samples = samples.shape[0]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else np.zeros(n_samples)
    predictions = np.empty((n_samples, len(EBOSS_DR16_FSIGMA8)), dtype=float)
    for col, (z, _v, _s) in enumerate(EBOSS_DR16_FSIGMA8):
        f_z = _growth_rate_f(z, omegam, w0, wa)
        d_ratio = _growth_factor_ratio(z, omegam, w0, wa)
        predictions[:, col] = f_z * sigma8 * d_ratio
    return predictions


def _eboss_fsigma8_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """Diagonal-covariance χ² of the 6-point eBOSS DR16 RSD fσ8 vector
    (Alam+2021 Table III footnote a: per-tracer Gaussian, correlations ignored)."""
    observed = np.asarray([row[1] for row in EBOSS_DR16_FSIGMA8], dtype=float)
    sigma = np.asarray([row[2] for row in EBOSS_DR16_FSIGMA8], dtype=float)
    predictions = _eboss_fsigma8_predictions(samples, parameter_order)
    residual = predictions - observed
    return np.sum((residual / sigma) ** 2, axis=1)


# M6 (2026-05-18): Pantheon+SH0ES 2022 data loader.  Lazy-loaded from a
# ~20 MB npz committed alongside the source code (see
# scripts/fetch_pantheon_plus.py for the regeneration script).  Holds the
# distance-modulus table + the full 1701x1701 stat+sys covariance + its
# inverse.  Inverse is cached because Cholesky-once-solve-many beats
# rebuilding it inside every chain.
_PANTHEON_PLUS_DATA_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "pantheon_plus_2022"
)
@lru_cache(maxsize=None)
def load_verified_pantheon_plus_data(dataset_key: str = "pantheon_plus") -> dict[str, Any]:
    """Load the Pantheon+SH0ES 1701-SN bundle from the vendored, sha256-pinned
    ``data.npz`` and verify its digest against the registry, so the covariance the
    χ² inverts IS the checksummed array (object identity).  cov_fidelity is "full"
    on a digest match (the stat+sys matrix is a released FULL covariance),
    "unverified" on a present-but-mismatched/corrupt file (blocks publication).  A
    missing-but-pinned file degrades to "unverified" with no arrays (so
    _entry_verification can stamp it without an import-time crash).  The expensive
    1701x1701 inverse is NOT computed here — verify-only callers
    (_entry_verification, audit_executable_pins) need only the digest + fidelity;
    the fit path derives cov_inv lazily via _pantheon_plus_cov_inv().
    """
    pinned = _registry_product_sha256(dataset_key, "sn_full_data_npz")
    npz_path = _PANTHEON_PLUS_DATA_DIR / "data.npz"

    def _fallback(fidelity: str) -> dict[str, Any]:
        return {
            "z_hd": None, "z_hel": None, "mu": None, "mu_err_diag": None, "cov": None,
            "sha256": None, "hash_verified": False, "cov_fidelity": fidelity,
        }

    if not npz_path.exists():
        return _fallback("unverified" if pinned else "literature_typed")
    try:
        raw = npz_path.read_bytes()  # read once: the hash and np.load share these bytes
        digest = hashlib.sha256(raw).hexdigest()
        npz = np.load(io.BytesIO(raw))
        verified = digest == pinned
        return {
            "z_hd": np.asarray(npz["z_hd"], dtype=np.float64),
            "z_hel": np.asarray(npz["z_hel"], dtype=np.float64),
            "mu": np.asarray(npz["mu"], dtype=np.float64),
            "mu_err_diag": np.asarray(npz["mu_err_diag"], dtype=np.float64),
            "cov": np.asarray(npz["cov"], dtype=np.float64),
            "sha256": digest,
            "hash_verified": bool(verified),
            "cov_fidelity": "full" if verified else "unverified",
        }
    except Exception as exc:  # malformed/truncated npz — degrade, never crash import
        logger.warning("Pantheon+ data product failed to load (%s); marking unverified", exc)
        return _fallback("unverified")


@lru_cache(maxsize=None)
def _pantheon_plus_cov_inv() -> np.ndarray:
    """Inverse of the verified 1701x1701 SN covariance — computed once, ONLY on the
    fit path (kept out of load_verified_pantheon_plus_data so verify-only callers
    do not pay the inversion just to read a digest/fidelity)."""
    return np.linalg.inv(load_verified_pantheon_plus_data("pantheon_plus")["cov"])


# ── DES-SN5YR full distance-modulus likelihood (2026-06-05) ─────────────────
# 1829-SN Vincenzi+2024 Legacy Hubble diagram + full stat+sys covariance,
# vendored as a sha256-pinned npz (built by scripts/fetch_des_sn5yr.py from the
# github tag-1.3 release: C_total = STAT+SYS systematic cov + diag(MUERR_FINAL²)).
# Mirrors the Pantheon+ full-cov machinery, but the χ² analytically marginalizes
# the SN absolute-magnitude offset (no M_B nuisance, and H0 drops out too), so it
# constrains Ωm (+ the w0/wa DE shape) only.
_DES_SN5YR_DATA_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "des_sn5yr"
)


@lru_cache(maxsize=None)
def load_verified_des_sn5yr_data(dataset_key: str = "des_sn5yr") -> dict[str, Any]:
    """Load the DES-SN5YR 1829-SN bundle from the vendored, sha256-pinned data.npz
    and verify its digest against the registry (so the covariance the χ² inverts IS
    the checksummed array). cov_fidelity is 'full' on a digest match, 'unverified'
    on a present-but-mismatched/corrupt or missing-but-pinned file (blocks
    publication). The 1829×1829 inverse is derived lazily on the fit path."""
    pinned = _registry_product_sha256(dataset_key, "sn_full_data_npz")
    npz_path = _DES_SN5YR_DATA_DIR / "data.npz"

    def _fallback(fidelity: str) -> dict[str, Any]:
        return {
            "z_hd": None, "z_hel": None, "mu": None, "mu_err_diag": None, "cov": None,
            "sha256": None, "hash_verified": False, "cov_fidelity": fidelity,
        }

    if not npz_path.exists():
        return _fallback("unverified" if pinned else "literature_typed")
    try:
        raw = npz_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        npz = np.load(io.BytesIO(raw))
        verified = digest == pinned
        return {
            "z_hd": np.asarray(npz["z_hd"], dtype=np.float64),
            "z_hel": np.asarray(npz["z_hel"], dtype=np.float64),
            "mu": np.asarray(npz["mu"], dtype=np.float64),
            "mu_err_diag": np.asarray(npz["mu_err_diag"], dtype=np.float64),
            "cov": np.asarray(npz["cov"], dtype=np.float64),
            "sha256": digest,
            "hash_verified": bool(verified),
            "cov_fidelity": "full" if verified else "unverified",
        }
    except Exception as exc:  # malformed/truncated npz — degrade, never crash import
        logger.warning("DES-SN5YR data product failed to load (%s); marking unverified", exc)
        return _fallback("unverified")


@lru_cache(maxsize=None)
def _des_sn5yr_cov_inv() -> np.ndarray:
    """Inverse of the verified 1829×1829 DES-SN5YR covariance — computed once, only
    on the fit path."""
    return np.linalg.inv(load_verified_des_sn5yr_data("des_sn5yr")["cov"])


@lru_cache(maxsize=None)
def _load_des_sn5yr_data() -> dict[str, np.ndarray]:
    """Arrays the DES-SN5YR χ² fits, sourced from the sha256-verified loader (cov IS
    the checksummed object) with cov_inv derived lazily. Refuses unverified data."""
    verified = load_verified_des_sn5yr_data("des_sn5yr")
    if verified["cov"] is None:
        raise FileNotFoundError(
            f"DES-SN5YR data file missing: {_DES_SN5YR_DATA_DIR / 'data.npz'}. "
            "Run `python scripts/fetch_des_sn5yr.py` to build it (~26 MB)."
        )
    if verified.get("cov_fidelity") == "unverified":
        raise ValueError(
            "DES-SN5YR covariance failed sha256 verification (digest mismatch); "
            "refusing to compute chi2 from unverified data — re-fetch the release."
        )
    return {
        "z_hd": verified["z_hd"], "z_hel": verified["z_hel"], "mu": verified["mu"],
        "cov_inv": _des_sn5yr_cov_inv(),
    }


# ── Union3 / UNITY1.5 full binned-distance likelihood (2026-06-12) ──────────
# The same 22-bin lcparam_full.txt + mag_covmat.txt cobaya's sn.union3 reads
# (CobayaSampler/sn_data), vendored + sha256-pinned. cobaya marginalizes the
# constant magnitude offset by projecting it out of invcov
# (_marginalize_abs_mag); our chi2 = δᵀC⁻¹δ − (ΣC⁻¹δ)²/(ΣC⁻¹) is the same
# projection applied per-sample — algebraically identical, locked by test.
_UNION3_DATA_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "union3"
)


@lru_cache(maxsize=1)
def _load_union3_raw() -> dict[str, Any]:
    """Load + sha256-verify the vendored Union3 files; raises ValueError on ANY
    failure (missing/unreadable/digest mismatch/malformed). lru_cache never
    caches exceptions, so one transient failure cannot poison the process
    until restart (the MGS-iteration lesson — a cached unverified record
    would)."""
    vec_path = _UNION3_DATA_DIR / "lcparam_full.txt"
    cov_path = _UNION3_DATA_DIR / "mag_covmat.txt"
    if not (vec_path.exists() and cov_path.exists()):
        raise ValueError(
            f"Union3 data unverified: vendored files missing under {_UNION3_DATA_DIR} "
            "(lcparam_full.txt + mag_covmat.txt, from CobayaSampler/sn_data)."
        )
    try:
        vec_raw = vec_path.read_bytes()
        cov_raw = cov_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Union3 data unverified: vendored file unreadable ({exc}).") from exc
    vec_ok = hashlib.sha256(vec_raw).hexdigest() == _registry_product_sha256(
        "union3", "measurement_vector"
    )
    cov_digest = hashlib.sha256(cov_raw).hexdigest()
    cov_ok = cov_digest == _registry_product_sha256("union3", "covariance")
    if not (vec_ok and cov_ok):
        raise ValueError(
            "Union3 data unverified: vendored file bytes do not match the registry "
            "sha256 pins; refusing to compute chi2 from tampered or stale data."
        )
    # Parse the SAME bytes the digests certified.
    z_cmb_list: list[float] = []
    z_hel_list: list[float] = []
    mb_list: list[float] = []
    for line in vec_raw.decode("utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        z_cmb_list.append(float(parts[1]))
        z_hel_list.append(float(parts[2]))
        mb_list.append(float(parts[4]))
    cov_tokens = cov_raw.decode("utf-8").split()
    n = int(cov_tokens[0])
    cov = np.asarray(cov_tokens[1:], dtype=float).reshape(n, n)
    if n != len(mb_list):
        raise ValueError(
            f"Union3 data unverified: vector/cov shape mismatch "
            f"({len(mb_list)} bins vs cov {n}x{n})."
        )
    return {
        "z_cmb": np.asarray(z_cmb_list, dtype=float),
        "z_hel": np.asarray(z_hel_list, dtype=float),
        "mb": np.asarray(mb_list, dtype=float),
        "cov": cov,
        "sha256": cov_digest,
    }


def load_verified_union3_data(dataset_key: str = "union3") -> dict[str, Any]:
    """Verification record for audit/provenance — NEVER raises (audit and
    import paths need a record, not an exception). Built fresh per call from
    the cached raw load: success → 'full' + digest; any failure → 'unverified'
    (blocks publication), recomputed next call so a transient failure heals
    without a process restart. Never a silent fallback to the compressed
    Gaussian."""
    try:
        raw = _load_union3_raw()
    except ValueError as exc:
        logger.warning("Union3 data product failed verification: %s", exc)
        return {
            "z_cmb": None, "z_hel": None, "mb": None, "cov": None,
            "sha256": None, "hash_verified": False, "cov_fidelity": "unverified",
        }
    return {**raw, "hash_verified": True, "cov_fidelity": "full"}


@lru_cache(maxsize=1)
def _union3_cov_inv() -> np.ndarray:
    """Inverse of the verified 22x22 Union3 covariance — computed on the fit path."""
    return np.linalg.inv(_load_union3_raw()["cov"])


def _load_union3_data() -> dict[str, np.ndarray]:
    """Arrays the Union3 χ² fits — raises ValueError ('unverified') on
    missing/tampered data via the raw loader."""
    raw = _load_union3_raw()
    return {
        "z_hd": raw["z_cmb"], "z_hel": raw["z_hel"], "mu": raw["mb"],
        "cov_inv": _union3_cov_inv(),
    }


def _load_pantheon_plus_data() -> dict[str, np.ndarray]:
    """Arrays the Pantheon+ χ² fits, sourced from the sha256-verified loader (cov
    IS the checksummed object) with cov_inv derived lazily.  No separate module
    cache: load_verified is lru-cached and _pantheon_plus_cov_inv memoizes the
    inverse, so this returns coherent objects without a second invalidation surface
    that could go stale or defeat a monkeypatch of the loader."""
    verified = load_verified_pantheon_plus_data("pantheon_plus")
    if verified["cov"] is None:
        raise FileNotFoundError(
            f"Pantheon+SH0ES data file missing: {_PANTHEON_PLUS_DATA_DIR / 'data.npz'}. "
            "Run `python scripts/fetch_pantheon_plus.py` to download "
            "the 2022 release (~20 MB)."
        )
    if verified.get("cov_fidelity") == "unverified":
        raise ValueError(
            "Pantheon+ covariance failed sha256 verification (digest mismatch); "
            "refusing to compute chi2 from unverified data — re-fetch the release."
        )
    return {
        "z_hd": verified["z_hd"], "z_hel": verified["z_hel"], "mu": verified["mu"],
        "mu_err_diag": verified["mu_err_diag"], "cov": verified["cov"],
        "cov_inv": _pantheon_plus_cov_inv(),
    }


# Pantheon+SH0ES baseline absolute magnitude.  The MU_SH0ES column in the
# data release is calibrated against the SH0ES Cepheid-SN distance ladder,
# which has M_B = -19.253 (Riess+ 2022 ApJL 934 L7).  Our likelihood lets
# the fit move M_B away from this baseline; the offset (M_B - M_B_REF) is
# what actually appears in the model, so at (H0=73.04, Ωm=0.334, M_B=-19.253)
# the residual collapses to zero and χ² ≈ dof (Pantheon+SH0ES best fit).
PANTHEON_PLUS_M_B_REF = -19.253


# Cached Gauss-Legendre quadrature nodes/weights (deg=64 trivially exact for
# the flat-ΛCDM E(z) integrand to << 0.1 mag accuracy over z ∈ [0, 3]).
_GL64_NODES, _GL64_WEIGHTS = np.polynomial.legendre.leggauss(64)


# ── CMB distance priors (Chen, Huang & Wang 2019, arXiv:1808.05724, Eqs 1-10) ──
# Compressed Planck 2018 TT,TE,EE+lowE geometry as (R, l_A, Omega_b h^2). The
# paper validates these base-LCDM priors against wCDM and CPL, so they remain
# valid in extended dark-energy models — unlike a hard H0/Omega_m Gaussian, which
# forbids the geometric slide along theta*=const that IS the dark-energy signal.
_T_CMB_K = 2.7255
_T_CMB_RATIO4 = (_T_CMB_K / 2.7) ** 4
# Eq (3): 3/(4 Omega_gamma h^2) = 31500 (T_CMB/2.7)^-4 ; baryon loading of `a`.
_RS_BARYON_COEF = 31500.0 / _T_CMB_RATIO4


def _cmb_distance_priors(omegam, h0, ombh2, w0=-1.0, wa=0.0):
    """(R, l_A, Omega_b h^2) CMB distance priors for flat (w0,wa)CDM, per
    Chen-Huang-Wang 2019.  Inputs scalar or broadcastable arrays.  R and l_A are
    H0-independent except through z*/radiation (the c/H0 cancels in both).
    Self-check: LCDM at Planck 2018 (Om=0.3153, H0=67.36, ombh2=0.02237) ->
    R~1.750, l_A~301.5."""
    scalar = np.ndim(omegam) == 0
    om = np.atleast_1d(np.asarray(omegam, float))
    obh2 = np.atleast_1d(np.asarray(ombh2, float))
    h2 = (np.atleast_1d(np.asarray(h0, float)) / 100.0) ** 2
    w0a = np.atleast_1d(np.asarray(w0, float))
    waa = np.atleast_1d(np.asarray(wa, float))
    om, obh2, h2, w0a, waa = np.broadcast_arrays(om, obh2, h2, w0a, waa)
    omh2 = om * h2
    # Recombination redshift z* (Eqs 8-10, Hu & Sugiyama 1996)
    g1 = 0.0738 * obh2 ** -0.238 / (1.0 + 39.5 * obh2 ** 0.763)
    g2 = 0.560 / (1.0 + 21.1 * obh2 ** 1.81)
    zstar = 1048.0 * (1.0 + 0.00124 * obh2 ** -0.738) * (1.0 + g1 * omh2 ** g2)
    # Radiation density incl. neutrinos (Eq 6); flat closes Omega_de.
    omr = om / (1.0 + 2.5e4 * omh2 / _T_CMB_RATIO4)
    omde = 1.0 - om - omr
    omc, omrc, omdec = om[:, None], omr[:, None], omde[:, None]
    w0c, wac, obc = w0a[:, None], waa[:, None], obh2[:, None]
    node = (_GL64_NODES + 1.0) * 0.5  # (64,) in [0,1]

    def inv_E(z):  # z shape (N, 64)
        x = 1.0 + z
        rho = x ** (3.0 * (1.0 + w0c + wac)) * np.exp(-3.0 * wac * z / x)
        return 1.0 / np.sqrt(omrc * x ** 4 + omc * x ** 3 + omdec * rho)

    # I = int_0^{z*} dz/E, in u=ln(1+z) so the low-z-peaked integrand is smooth.
    ustar = np.log(1.0 + zstar)[:, None]
    u = ustar * node
    i_dc = np.sum(_GL64_WEIGHTS * (ustar * 0.5) * np.exp(u) * inv_E(np.exp(u) - 1.0), axis=-1)
    # J = int_0^{a*} da / (a^2 E sqrt(3(1 + Rb a))).  c/H0 cancels in l_A = pi*I/J.
    astar = (1.0 / (1.0 + zstar))[:, None]
    a = astar * node
    rb = _RS_BARYON_COEF * obc * a
    integ = inv_E(1.0 / a - 1.0) / (a ** 2 * np.sqrt(3.0 * (1.0 + rb)))
    i_rs = np.sum(_GL64_WEIGHTS * (astar * 0.5) * integ, axis=-1)

    big_r = np.sqrt(om) * i_dc
    l_a = np.pi * i_dc / i_rs
    if scalar:
        return float(big_r[0]), float(l_a[0]), float(obh2[0])
    return big_r, l_a, obh2


# Planck 2018 TT,TE,EE+lowE distance priors, base-LCDM block (the paper validates
# this block for wCDM/CPL too).  Chen-Huang-Wang 2019, arXiv:1808.05724, Table I:
# R=1.7502+-0.0046, l_A=301.471+-0.090, ombh2=0.02236+-0.00015, with the listed
# correlation matrix.  Used as the CMB term for extended FLAT dark-energy fits.
_PLANCK18_DP_MEAN = np.array([1.7502, 301.471, 0.02236])
_PLANCK18_DP_SIGMA = np.array([0.0046, 0.090, 0.00015])
_PLANCK18_DP_CORR = np.array([
    [1.00, 0.46, -0.66],
    [0.46, 1.00, -0.33],
    [-0.66, -0.33, 1.00],
])
_PLANCK18_DP_INVCOV = np.linalg.inv(
    _PLANCK18_DP_SIGMA[:, None] * _PLANCK18_DP_SIGMA[None, :] * _PLANCK18_DP_CORR
)


def _planck_distance_prior_chi2(samples: np.ndarray, parameter_order: list[str]) -> np.ndarray:
    """Per-sample chi2 of the Planck 2018 CMB distance prior (R, l_A, ombh2) for an
    extended FLAT dark-energy chain (Chen-Huang-Wang 2019)."""
    om = samples[:, parameter_order.index("omegam")]
    h0 = samples[:, parameter_order.index("H0")]
    obh2 = samples[:, parameter_order.index("ombh2")]
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = -1.0
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else 0.0
    big_r, l_a, _ = _cmb_distance_priors(om, h0, obh2, w0=w0, wa=wa)
    resid = np.column_stack([big_r, l_a, obh2]) - _PLANCK18_DP_MEAN
    return np.einsum("ni,ij,nj->n", resid, _PLANCK18_DP_INVCOV, resid)


def _flat_de_dm_grid_vectorized(
    z: np.ndarray,
    h0: np.ndarray,
    omegam: np.ndarray,
    w0: np.ndarray,
    wa: np.ndarray,
) -> np.ndarray:
    """Vectorized comoving distance D_M(z; H0, Ωm, w0, wa) over (z, sample) pairs
    under flat w0waCDM (CPL).  ΛCDM is the (w0=-1, wa=0) limit.

    z      : (n_sn,)        — redshifts to evaluate at
    h0     : (n_samples,)   — Hubble constant per posterior sample
    omegam : (n_samples,)   — Ωm per posterior sample
    w0     : (n_samples,)   — dark-energy EOS at a=1 per sample
    wa     : (n_samples,)   — dark-energy EOS slope per sample
    Returns: (n_sn, n_samples)  — D_M in Mpc

    Replaces the previous Python `for j, z_j in z` loop inside Pantheon+
    chi² with one big NumPy einsum, and adds DE-aware E(z) integrand so
    wcdm/w0waCDM joint fits with SN are physically self-consistent
    (bug fix from review #2: SN χ² used to silently override w to -1).

    Memory: O(n_samples · n_sn · 64) float64; ~30 MB for 32 walkers × 1701
    SN × 64 nodes.  Tractable.
    """
    nodes = _GL64_NODES
    weights = _GL64_WEIGHTS
    # x[j, k] = 0.5 * z[j] * (nodes[k] + 1)  — quadrature variable
    x = 0.5 * z[:, None] * (nodes[None, :] + 1.0)            # (n_sn, 64)
    one_plus_x = 1.0 + x                                     # (n_sn, 64)
    one_plus_x_cubed = one_plus_x ** 3                       # (n_sn, 64)
    # Scale factor a = 1/(1+x); ρ_DE(a)/ρ_DE,0 = a^(-3(1+w0+wa)) · exp(-3 wa (1-a))
    a_int = 1.0 / one_plus_x                                 # (n_sn, 64)
    rho_de = (
        a_int[None, :, :] ** (-3.0 * (1.0 + w0[:, None, None] + wa[:, None, None]))
        * np.exp(-3.0 * wa[:, None, None] * (1.0 - a_int[None, :, :]))
    )                                                         # (n_samples, n_sn, 64)
    # ez[i, j, k] = sqrt(Ωm[i] * (1+x[j,k])^3 + (1 - Ωm[i]) * ρ_DE)
    ez = np.sqrt(
        omegam[:, None, None] * one_plus_x_cubed[None, :, :]
        + (1.0 - omegam[:, None, None]) * rho_de
    )                                                         # (n_samples, n_sn, 64)
    integral = 0.5 * z[None, :] * np.sum(weights[None, None, :] / ez, axis=2)
    # D_M = (c / H0) * integral
    dm = (C_LIGHT_KM_S / h0[:, None]) * integral             # (n_samples, n_sn)
    return dm.T  # (n_sn, n_samples)


def _flat_lcdm_dm_grid_vectorized(
    z: np.ndarray, h0: np.ndarray, omegam: np.ndarray
) -> np.ndarray:
    """ΛCDM-only convenience wrapper around :func:`_flat_de_dm_grid_vectorized`.

    Equivalent to (w0=-1, wa=0); kept as a thin shim for any caller that
    has no DE parameters in its parameter_order.
    """
    w0 = np.full_like(h0, -1.0, dtype=float)
    wa = np.zeros_like(h0, dtype=float)
    return _flat_de_dm_grid_vectorized(z, h0, omegam, w0, wa)


def _pantheon_plus_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """χ² contribution from Pantheon+SH0ES 1701 SNe Ia under flat w0waCDM.

    Model: μ_model(z) = 5·log10(D_L(z; H0, Ωm, w0, wa) [Mpc]) + 25 + (M_B - M_B_REF)
       where D_L = (1+z_hel)·D_M(z_hd), M_B_REF = -19.253 is the SH0ES baseline, and
       M_B is fit as a free nuisance.  At M_B = M_B_REF + 0 the model matches
       the SH0ES-calibrated distance modulus; offsets let the SN data
       constrain (H0, M_B) jointly, breaking the H0 degeneracy when combined
       with BAO/CMB.
    χ² = (μ_obs - μ_model)ᵀ · C⁻¹ · (μ_obs - μ_model)

    parameter_order must contain "H0", "omegam", "M_B".  Optionally also
    "w"/"w0"/"wa": when present, those columns flow through the DE-aware
    distance integrand so the joint posterior on the SN side is consistent
    with the cosmological model (review fix bug_001: previously SN χ² was
    hard-coded to ΛCDM regardless of model_key, silently biasing w/wa
    posteriors toward -1/0 in DESI+SN joint fits).
    """
    data = _load_pantheon_plus_data()
    z_hd = data["z_hd"]    # cosmological redshift — drives the comoving-distance integral
    z_hel = data["z_hel"]  # heliocentric redshift — the (1+z) luminosity-distance factor
    mu_obs = data["mu"]
    cov_inv = data["cov_inv"]
    n_samples = samples.shape[0]
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    m_b = samples[:, parameter_order.index("M_B")]
    # Read dark-energy params if present; default to ΛCDM (w0=-1, wa=0).
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    if "wa" in parameter_order:
        wa = samples[:, parameter_order.index("wa")]
    else:
        wa = np.zeros(n_samples, dtype=float)
    # Vectorized D_M over (z_hd, sample) under flat w0waCDM (CPL); the
    # luminosity-distance (1+z) factor uses z_hel per the Pantheon+ convention
    # D_L = (1 + z_hel) · D_M(z_hd).
    dm_grid = _flat_de_dm_grid_vectorized(z_hd, h0, omegam, w0, wa)  # (n_sn, n_samples)
    dl_grid = (1.0 + z_hel[:, None]) * dm_grid               # (n_sn, n_samples), Mpc
    mu_model = (
        5.0 * np.log10(dl_grid) + 25.0 + (m_b[None, :] - PANTHEON_PLUS_M_B_REF)
    )
    residual = mu_obs[:, None] - mu_model                    # (n_sn, n_samples)
    return np.einsum("in,ij,jn->n", residual, cov_inv, residual)


def _offset_marginalized_sn_chi2(
    samples: np.ndarray,
    parameter_order: list[str],
    *,
    z_hd: np.ndarray,
    z_hel: np.ndarray,
    mu_obs: np.ndarray,
    cov_inv: np.ndarray,
) -> np.ndarray:
    """Shared χ² core for binned/full SN distance-modulus likelihoods with the
    absolute-magnitude offset M analytically marginalized:

        δ = μ_model − μ_obs ,  χ² = δᵀC⁻¹δ − (Σ C⁻¹δ)² / (Σ C⁻¹)

    Algebraically identical to cobaya's _marginalize_abs_mag projection of the
    inverse covariance. The marginalized χ² is invariant to a constant shift of
    μ_model, so H0 (and M_B) drop out entirely — these likelihoods constrain
    Ωm (+ the w0/wa DE shape) only. parameter_order must contain "omegam";
    optionally "w"/"w0"/"wa". μ_obs may carry an arbitrary constant
    normalization (Union3's binned mb does)."""
    n_samples = samples.shape[0]
    omegam = samples[:, parameter_order.index("omegam")]
    h0_fid = np.full(n_samples, 70.0, dtype=float)  # absolute H0 is marginalized away
    if "w0" in parameter_order:
        w0 = samples[:, parameter_order.index("w0")]
    elif "w" in parameter_order:
        w0 = samples[:, parameter_order.index("w")]
    else:
        w0 = np.full(n_samples, -1.0, dtype=float)
    wa = samples[:, parameter_order.index("wa")] if "wa" in parameter_order else np.zeros(n_samples)
    dm_grid = _flat_de_dm_grid_vectorized(z_hd, h0_fid, omegam, w0, wa)  # (n_sn, n_samples)
    dl_grid = (1.0 + z_hel[:, None]) * dm_grid
    mu_model = 5.0 * np.log10(dl_grid) + 25.0
    delta = mu_model - mu_obs[:, None]                      # (n_sn, n_samples)
    cinv_delta = cov_inv @ delta                            # (n_sn, n_samples)
    chit2 = np.einsum("in,in->n", delta, cinv_delta)
    b = cinv_delta.sum(axis=0)                              # Σ_i (C⁻¹δ)_i per sample
    c_norm = float(cov_inv.sum())                           # Σ_ij C⁻¹_ij
    return chit2 - b ** 2 / c_norm


def _des_sn5yr_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """χ² from the DES-SN5YR 1829 SNe Ia (offset-marginalized, per the official
    DES-SN5YR likelihood)."""
    data = _load_des_sn5yr_data()
    return _offset_marginalized_sn_chi2(
        samples, parameter_order,
        z_hd=data["z_hd"], z_hel=data["z_hel"],
        mu_obs=data["mu"], cov_inv=data["cov_inv"],
    )


def _union3_chi2_samples(
    samples: np.ndarray, parameter_order: list[str]
) -> np.ndarray:
    """χ² from the Union3/UNITY1.5 22-bin binned distance moduli
    (offset-marginalized — cobaya sn.union3's use_abs_mag=False convention)."""
    data = _load_union3_data()
    return _offset_marginalized_sn_chi2(
        samples, parameter_order,
        z_hd=data["z_hd"], z_hel=data["z_hel"],
        mu_obs=data["mu"], cov_inv=data["cov_inv"],
    )


def _offset_sn_chi2_samples(
    samples: np.ndarray, parameter_order: list[str], key: str
) -> np.ndarray:
    """Per-key dispatch for the offset-marginalized SN family — else-raise so a
    future key cannot silently run on another dataset's data."""
    if key == "des_sn5yr":
        return _des_sn5yr_chi2_samples(samples, parameter_order)
    if key == "union3":
        return _union3_chi2_samples(samples, parameter_order)
    raise ValueError(f"executable offset-marginalized SN entry {key!r} has no chi2 dispatch")


def _offset_sn_n_points(key: str) -> int:
    """Number of fitted data points for an offset-marginalized SN entry."""
    if key == "des_sn5yr":
        return int(len(_load_des_sn5yr_data()["mu"]))
    if key == "union3":
        return int(len(_load_union3_data()["mu"]))
    raise ValueError(f"executable offset-marginalized SN entry {key!r} has no data loader")


def _de_energy_density(a: np.ndarray, w0: np.ndarray, wa: np.ndarray) -> np.ndarray:
    """Flat-DE ρ_DE(a) / ρ_DE,0 for the CPL w(a) = w0 + wa(1-a) parameterization.

    Closed form: f(a) = a^(-3(1+w0+wa)) * exp(-3 wa (1-a)).
    Reduces to 1 for ΛCDM (w0=-1, wa=0). Vectorized over both axes.
    """
    return a ** (-3.0 * (1.0 + w0 + wa)) * np.exp(-3.0 * wa * (1.0 - a))


def _flat_de_distances_at_z(
    z: float,
    h0: np.ndarray,
    omegam: np.ndarray,
    *,
    w0: np.ndarray | float = -1.0,
    wa: np.ndarray | float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Comoving D_M, Hubble D_H = c/H(z), volume D_V at redshift ``z`` under
    flat w0waCDM. ΛCDM is the (w0=-1, wa=0) limit.

    All sample arrays (h0, omegam, w0, wa) must be 1D of equal length; scalar
    w0/wa are broadcast. The Gauss-Legendre 64-point rule integrates 1/E(z')
    over z' ∈ [0, z] to < 1e-12 over z ≤ 3 for any sane (w0, wa) box.
    """
    nodes, weights = np.polynomial.legendre.leggauss(64)
    w0_arr = np.asarray(w0, dtype=float).reshape(-1) if np.ndim(w0) else np.full_like(omegam, float(w0))
    wa_arr = np.asarray(wa, dtype=float).reshape(-1) if np.ndim(wa) else np.full_like(omegam, float(wa))
    x = 0.5 * z * (nodes + 1.0)                                 # (64,)
    one_plus_x = 1.0 + x[None, :]                                # (1, 64)
    a_int = 1.0 / one_plus_x                                     # (1, 64) — scale factor
    rho_de_grid = _de_energy_density(a_int, w0_arr[:, None], wa_arr[:, None])
    ez_grid = np.sqrt(
        omegam[:, None] * one_plus_x ** 3
        + (1.0 - omegam[:, None]) * rho_de_grid
    )
    integral = 0.5 * z * np.sum(weights[None, :] / ez_grid, axis=1)
    dm = (C_LIGHT_KM_S / h0) * integral
    a_z = 1.0 / (1.0 + z)
    ez = np.sqrt(omegam * (1.0 + z) ** 3 + (1.0 - omegam) * _de_energy_density(a_z, w0_arr, wa_arr))
    dh = C_LIGHT_KM_S / (h0 * ez)
    dv = np.cbrt(z * dm * dm * dh)
    return dm, dh, dv


def _flat_lcdm_distances_at_z(
    z: float,
    h0: np.ndarray,
    omegam: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ΛCDM-only convenience wrapper around :func:`_flat_de_distances_at_z`."""
    return _flat_de_distances_at_z(z, h0, omegam, w0=-1.0, wa=0.0)


def _compressed_chi2_samples(
    samples: np.ndarray,
    parameter_order: list[str],
    compressed_entries: list[CosmologyDatasetEntry],
) -> tuple[np.ndarray, list[str]]:
    total = np.zeros(samples.shape[0], dtype=float)
    invalid_specs: list[str] = []
    # S8 = σ8·√(Ωm/0.3) is derived (not a sampled column) whenever σ8 and Ωm are
    # both sampled; its Gaussian then applies on the derived per-sample value.
    derived_s8 = (
        _derived_s8_from_samples(samples, parameter_order)
        if _s8_is_derived(parameter_order)
        else None
    )
    for entry in compressed_entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        # Extended FLAT dark-energy models: the compressed Planck spec pins
        # H0/omegam at their LCDM projection, which forbids the geometric slide
        # along theta*=const that IS the w0/wa signal.  Use the model-valid
        # acoustic-scale distance prior (R, l_A, ombh2) instead (Chen-Huang-Wang
        # 2019), keeping any derived-S8 growth row.  Curved (ok_*) models keep the
        # old path (a FLAT distance prior would be wrong; the curved prior is
        # deferred).  LCDM has no DE param -> untouched, byte-for-byte unchanged.
        de_flat = (
            entry.key == "planck2018_compressed"
            and any(p in parameter_order for p in ("w", "w0", "wa"))
            and "omegak" not in parameter_order
        )
        if de_flat:
            try:
                total += _planck_distance_prior_chi2(samples, parameter_order)
                if derived_s8 is not None and "S8" in spec.parameters:
                    j = list(spec.parameters).index("S8")
                    s8_mean = float(np.asarray(spec.mean, dtype=float)[j])
                    s8_var = float(np.asarray(spec.covariance, dtype=float)[j, j])
                    total += (derived_s8 - s8_mean) ** 2 / s8_var
            except Exception as exc:
                invalid_specs.append(f"{entry.key}: {exc}")
            continue
        try:
            params = list(spec.parameters)
            names = [
                name
                for name in params
                if name in parameter_order
                or (name == "S8" and derived_s8 is not None)
            ]
            if not names:
                # B2: none of this dataset's parameters are in the sampled set,
                # so it can contribute no chi2. Record it as an invalid spec —
                # which flips publication_ready off and surfaces a blocked
                # reason — instead of silently dropping it to chi2=0 while it
                # still appears in datasets_used as if it had constrained the
                # fit (e.g. a BBN ombh2 prior selected alongside a chain that
                # samples only H0/omegam/rd, where ombh2 is never sampled).
                invalid_specs.append(
                    f"{entry.key}: none of its parameters {params} are in the "
                    f"sampled parameter set {list(parameter_order)}, so it "
                    f"contributed no constraint — not applied as run."
                )
                continue
            local_idx = [params.index(name) for name in names]
            mean = np.asarray(spec.mean, dtype=float)[local_idx]
            cov = np.asarray(spec.covariance, dtype=float)[np.ix_(local_idx, local_idx)]
            columns = [
                derived_s8
                if name == "S8" and name not in parameter_order
                else samples[:, parameter_order.index(name)]
                for name in names
            ]
            residual = np.column_stack(columns) - mean
            total += np.einsum("ni,ij,nj->n", residual, np.linalg.inv(cov), residual)
        except Exception as exc:
            invalid_specs.append(f"{entry.key}: {exc}")
    return total, invalid_specs


def _sampling_source_records(entries: list[CosmologyDatasetEntry]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entry in entries:
        if entry.key == "desi_dr1_bao":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "DESI DR1 BAO ALL_GCcomb mean/covariance files",
                "approximation": "Gaussian BAO mean/covariance evaluated against flat LCDM distances",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key == "sdss_6df_bao":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "Aubourg+2015 Table II (6dFGS Gaussian) + CobayaSampler/bao_data sdss_MGS_prob.txt (Ross+2015 MGS chi2(alpha) table)",
                "approximation": "Mixed: 6dFGS z=0.106 hand-typed Gaussian (D_V/r_d = 3.047 ± 0.137) + SDSS MGS z=0.15 full non-Gaussian chi2(alpha) lookup (cobaya's spline convention, alpha = (D_V/r_d)/4.29720761315)",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key == "cosmic_chronometers":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "Gómez-Valent & Amendola 2018 (arXiv:1802.01505) Table 1 — 31 cosmic-chronometer H(z)",
                "approximation": "Diagonal-covariance H(z)=H0·E(z) χ² (flat w0waCDM); full Moresco+2020 systematic covariance not applied",
            })
        elif entry.key == "cosmic_chronometers_moresco20":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "Moresco et al. 2020 (arXiv:2003.07362) CCcovariance — 15 BC03 H(z) + full systematic covariance",
                "approximation": "Full-covariance H(z)=H0·E(z) χ² = rᵀC⁻¹r (flat w0waCDM); covariance reproduced from sha256-pinned raw files via scripts/gen_moresco20_cc_covariance.py",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key == "eboss_dr16_rsd":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "Alam et al. 2021 (arXiv:2007.08991) Table III RSD-only column — 6 eBOSS DR16 fσ8(z)",
                "approximation": "Diagonal-covariance fσ8=f(z)·σ8·D(z)/D(0) χ² with Linder γ growth index; per-tracer Gaussian (correlations ignored, per Table III note a)",
            })
        elif entry.key in EBOSS_DR16_FSBAO_EXECUTABLE_KEYS:
            records.append({
                "dataset_key": entry.key,
                "source_locator": f"SDSS DR16 BAO+RSD consensus (CobayaSampler/bao_data, {entry.key}) — joint (D_M/r_s, D_H/r_s, fσ8) + full covariance",
                "approximation": "Full-covariance rᵀC⁻¹r χ² (flat w0waCDM): D_M/r_s, D_H/r_s distance ratios + Linder-γ fσ8 growth, with the released joint distance+growth covariance",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key in SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS:
            records.append({
                "dataset_key": entry.key,
                "source_locator": "BOSS DR12 consensus BAO (Alam et al. 2017, arXiv:1607.03155; CobayaSampler/bao_data) — (D_M·rs_fid/r_d, H·r_d/rs_fid) at z=0.38/0.51/0.61 + full 6×6 covtot",
                "approximation": "Full-covariance rᵀC⁻¹r χ² (flat w0waCDM) in the release's rs_fid=147.78 Mpc storage convention, mirroring cobaya bao.sdss_dr12_consensus_bao",
                # Structured numeric field: rs_fid is a legitimate published
                # constant of the release and must stay claimable now that the
                # prose strings above are free-text-skipped by claim_validator.
                "rs_fid_mpc": SDSS_DR12_RS_FID_MPC,
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.key == "union3":
            records.append({
                "dataset_key": entry.key,
                "source_locator": "Union3/UNITY1.5 Rubin+2023 (arXiv:2311.12098; CobayaSampler/sn_data) — 22-bin binned distance moduli + full 22x22 mag covariance",
                "approximation": "Full-covariance SN χ² = δᵀC⁻¹δ − (ΣC⁻¹δ)²/(ΣC⁻¹) (flat w0waCDM), analytically marginalizing the constant magnitude offset (no M_B/H0); constrains Ωm (+w0/wa)",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif _is_executable_des_sn_entry(entry):
            records.append({
                "dataset_key": entry.key,
                "source_locator": "DES-SN5YR Vincenzi+2024 Legacy (github tag 1.3) — 1829-SN distance-modulus vector + full stat+sys covariance",
                "approximation": "Full-covariance SN χ² = δᵀC⁻¹δ − (ΣC⁻¹δ)²/(ΣC⁻¹) (flat w0waCDM), analytically marginalizing the SN absolute-magnitude offset (no M_B/H0); constrains Ωm (+w0/wa)",
                "data_products": [product.to_dict() for product in entry.data_products],
            })
        elif entry.compressed_likelihood is not None:
            records.append({
                "dataset_key": entry.key,
                "source_locator": entry.compressed_likelihood.source_locator,
                "approximation": entry.compressed_likelihood.approximation,
            })
    return records


def _compressed_runner_unavailable(
    *,
    model_key: str,
    entries: list[CosmologyDatasetEntry],
    seed: int,
    reason: str,
) -> dict[str, Any]:
    return {
        "success": True,
        "__tool_status__": "PARTIAL",
        "analysis_status": "NO_COMPRESSED_LIKELIHOOD",
        "publication_ready": False,
        "chain_tier": "blocked",
        "__do_not_claim__": True,
        "model": model_key,
        "model_label": MODEL_LABELS.get(model_key, model_key),
        "sampler": "compressed_gaussian_analytic",
        "dataset_keys": [entry.key for entry in entries],
        "datasets": [entry.to_dict() for entry in entries],
        "datasets_used": [],
        "datasets_not_run": [entry.to_dict() for entry in entries],
        "random_seed": seed,
        "warnings": [reason],
        "__message_to_model__": (
            reason
            + " Do not quote posterior constraints, S8/H0/Omega_m tensions, "
            "AIC/BIC, or significance from this result."
        ),
        "provenance": {
            "cosmology_likelihood": {
                "registry_version": "2026-04-30",
                "runner": "compressed_gaussian_analytic",
                "dataset_keys": [entry.key for entry in entries],
                "datasets_used": [],
                "datasets_not_run": [entry.key for entry in entries],
                "citations": _collect_citations(entries),
                "publication_ready": False,
            }
        },
    }


def run_alcock_paczynski_test(
    *,
    omega_m_grid: tuple[float, float, int] = (0.10, 0.50, 401),
) -> dict[str, Any]:
    """Alcock-Paczynski geometric test on DESI DR1 BAO DM/DH ratios.

    The (DM/rs) / (DH/rs) ratio at each redshift is independent of H0
    and rs (both cancel), so it provides a pure-geometry Ωm constraint
    that does NOT use the BAO peak amplitude — only the ratio of
    transverse to line-of-sight distances. Inconsistency between the
    AP-only Ωm and the BAO-amplitude Ωm signals either non-flat
    geometry or evolving dark energy.

    DESI DR1 has 5 redshift bins with both DM/rs and DH/rs measured
    (z = 0.510 / 0.706 / 0.930 / 1.317 / 2.330). DV/rs-only bins
    (z = 0.295, 1.491) cannot enter the AP test.

    Reference: Alcock & Paczynski 1979 Nature 281 358.

    Returns a dict with Ωm best-fit, 1σ band, per-z observed ratios,
    chi² at minimum, and degrees of freedom.
    """
    # Provenance binding (2026-06-12 review): fit the sha256-VERIFIED vendored
    # arrays, not the legacy hand-typed constants — the constants are
    # byte-identical to the vendored file (documented at _HARDCODED_BAO), so
    # this shifts no number, but it puts the array this tool actually fits
    # under the registry pin audit and makes publication_ready conditional on
    # verification instead of unconditional ("decorative provenance" class).
    verified = load_verified_bao_data("desi_dr1_bao")
    if not verified.get("hash_verified") or verified.get("mean_vector") is None:
        return {
            "tool": "alcock_paczynski_test",
            "success": False,
            "error": (
                "DESI DR1 BAO vendored data failed sha256 verification (or is "
                "missing); refusing to run the AP test on unverified data."
            ),
            "error_class": "unverified_data",
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "publication_ready": False,
            "__do_not_claim__": True,
        }
    mean_vector = verified["mean_vector"]
    pairs: list[tuple[float, float, float]] = []
    cov = verified["covariance"]
    for i, (z_dm, val_dm, qty_dm) in enumerate(mean_vector):
        if qty_dm != "DM_over_rs":
            continue
        for j, (z_dh, val_dh, qty_dh) in enumerate(mean_vector):
            if qty_dh != "DH_over_rs" or abs(z_dh - z_dm) > 1e-6:
                continue
            sigma_dm_sq = cov[i][i]
            sigma_dh_sq = cov[j][j]
            cov_dm_dh = cov[i][j]
            ratio = val_dm / val_dh
            sigma_ratio_sq = (
                sigma_dm_sq / val_dh ** 2
                + (val_dm / val_dh ** 2) ** 2 * sigma_dh_sq
                - 2 * (val_dm / val_dh ** 3) * cov_dm_dh
            )
            sigma_ratio = math.sqrt(max(sigma_ratio_sq, 1e-30))
            pairs.append((z_dm, ratio, sigma_ratio))
            break  # don't double-count if duplicate DH at same z

    if not pairs:
        return {
            "tool": "alcock_paczynski_test",
            "success": False,
            "error": "DESI DR1 BAO MEAN_VECTOR has no (DM, DH) pairs at the same redshift.",
            "error_class": "no_dm_dh_pairs",
            "__tool_status__": "FAILED",
            "__do_not_claim__": True,
        }

    omega_m_lo, omega_m_hi, omega_m_n = omega_m_grid
    omega_m = np.linspace(omega_m_lo, omega_m_hi, int(omega_m_n))
    h0 = np.full_like(omega_m, 70.0)  # H0 cancels in DM/DH ratio

    chi2 = np.zeros_like(omega_m)
    for z, ratio_obs, sigma_ratio in pairs:
        dm_pred, dh_pred, _ = _flat_lcdm_distances_at_z(z, h0, omega_m)
        ratio_pred = dm_pred / dh_pred
        chi2 += ((ratio_obs - ratio_pred) / sigma_ratio) ** 2

    best_idx = int(np.argmin(chi2))
    omega_m_best = float(omega_m[best_idx])
    chi2_min = float(chi2[best_idx])

    # 1σ band from Δχ² < 1
    in_1sigma_mask = (chi2 - chi2_min) < 1.0
    in_1sigma = omega_m[in_1sigma_mask]
    omega_m_lo_1sigma = float(in_1sigma.min()) if in_1sigma.size else omega_m_best
    omega_m_hi_1sigma = float(in_1sigma.max()) if in_1sigma.size else omega_m_best

    n_dof = max(len(pairs) - 1, 1)
    z_pairs_payload = [
        {
            "z": round(z, 4),
            "DM_DH_ratio": round(ratio, 6),
            "sigma_ratio": round(sigma, 6),
        }
        for z, ratio, sigma in pairs
    ]

    return {
        "tool": "alcock_paczynski_test",
        "success": True,
        "method": (
            "DESI DR1 BAO DM/DH ratio chi-square fit. H0 and r_d cancel "
            "in the geometric ratio, leaving Ωm as the only free "
            "parameter under flat LCDM."
        ),
        "n_redshift_pairs": len(pairs),
        "n_dof": n_dof,
        "omega_m_best": round(omega_m_best, 6),
        "omega_m_1sigma_low": round(omega_m_lo_1sigma, 6),
        "omega_m_1sigma_high": round(omega_m_hi_1sigma, 6),
        "omega_m_1sigma_half_width": round(
            (omega_m_hi_1sigma - omega_m_lo_1sigma) / 2.0, 6
        ),
        "chi2_min": round(chi2_min, 4),
        "chi2_per_dof": round(chi2_min / n_dof, 4),
        "z_pairs": z_pairs_payload,
        # Conditional on the sha256 verification above — an unverified file
        # already returned a loud refusal before reaching this point.
        "publication_ready": bool(verified.get("hash_verified")),
        "claim_scope": "alcock_paczynski_geometric_omega_m",
        "data_origin": "real_archive",
        "analysis_status": "ALCOCK_PACZYNSKI_READY",
        "citations": [
            {
                "label": "Alcock & Paczynski geometric test",
                "year": 1979,
                "doi": "10.1038/281358a0",
                "bibcode": "1979Natur.281..358A",
            },
            {
                "label": "DESI Collaboration DR1 BAO",
                "year": 2024,
                "arxiv": "2404.03002",
            },
        ],
        "provenance": {
            "alcock_paczynski": {
                "input_dataset": "desi_dr1_bao",
                "artifact_sha256": verified.get("sha256"),
                "cov_fidelity": verified.get("cov_fidelity"),
                "n_pairs_used": len(pairs),
                "omega_m_grid_min_max": [float(omega_m.min()), float(omega_m.max())],
                "omega_m_grid_resolution": int(omega_m_n),
                "H0_cancellation": "DM/DH ratio is independent of H0 and r_d",
                "model_assumed": "flat LCDM",
                "free_parameters": ["omegam"],
                "notes": (
                    "AP-only Ωm vs BAO-amplitude Ωm comparison reveals "
                    "non-flat geometry or evolving dark energy when "
                    "discrepant > 2σ."
                ),
            },
        },
    }


def _compressed_parameter_order(entries: list[CosmologyDatasetEntry]) -> list[str]:
    order: list[str] = []
    for entry in entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        for param in spec.parameters:
            if param in RUNNER_PARAMETER_PRIORS and param not in order:
                order.append(param)
    # Keep familiar cosmology params first for stable UI/test output.
    preferred = ["H0", "omegam", "sigma8", "S8"]
    ordered = [param for param in preferred if param in order] + [
        param for param in order if param not in preferred
    ]
    return _drop_derived_s8(ordered)


def _all_external_cobaya(entries: list[CosmologyDatasetEntry]) -> bool:
    """True iff every entry is registered with execution_mode='external_cobaya'.

    Used as the gate for delegating run_likelihood_chain to cobaya_runner.
    Mixed selections (some entries compressed, some external_cobaya) keep
    the legacy compressed-Gaussian path so we never silently drop the
    compressed datasets — those still produce a publication_ready summary
    in the existing branch.
    """
    return bool(entries) and all(
        entry.execution_mode == "external_cobaya" for entry in entries
    )


# Primary-CMB external-cobaya likelihoods that sample the full CMB parameter set
# (ombh2, omch2, H0, ns, As, tau), rather than the geometric (H0, Omega_m, rd)
# set the compressed/in-process probes use.
CMB_COBAYA_EXECUTABLE_KEYS = frozenset({
    "planck_2018_highl_TTTEEE_lite",
    "planck_2018_lowl_TT",
    "planck_2018_lowl_EE",
    "planck_2018_lensing",
})

# A_planck is sampled only when plik_lite or the 2018 lensing likelihood is
# selected (lensing's params include the planck_calib defaults, so it consumes
# the shared calibration). The native low-l likelihoods CAN consume it (cobaya
# get_can_support_params), but their default is calib=1 and a 0.25%
# calibration uncertainty is negligible against l<=29 cosmic variance — so a
# lowl-only run deliberately fixes it. In the full stack cobaya shares the one
# sampled A_planck across all likelihoods, matching official Planck practice.
CMB_APLANCK_KEYS = frozenset({
    "planck_2018_highl_TTTEEE_lite",
    "planck_2018_lensing",
})


def _cobaya_parameter_order(
    model_key: str,
    entries: list[CosmologyDatasetEntry],
) -> list[str]:
    """Pick the parameter ordering passed to cobaya_runner.

    A primary-CMB entry (plik_lite / low-l TT / low-l EE) samples the full CMB
    set (ombh2, omch2, H0, ns, As, tau), plus A_planck when plik_lite is among
    the entries (see CMB_APLANCK_KEYS).  Otherwise: prefers any
    compressed-likelihood parameter spec the registered entries expose; falls
    back to the intersection of SUPPORTED_MODELS[model_key] with
    RUNNER_PARAMETER_PRIORS so that the YAML cobaya_runner emits always declares
    params it has prior bounds for.
    """
    if any(entry.key in CMB_COBAYA_EXECUTABLE_KEYS for entry in entries):
        order = ["ombh2", "omch2", "H0", "ns", "As", "tau"]
        if any(entry.key in CMB_APLANCK_KEYS for entry in entries):
            order.append("A_planck")
        for param in SUPPORTED_MODELS.get(model_key, ()):
            # mnu joined 2026-06-12: without it a *_mnu chain silently ran
            # CAMB's fixed default neutrino mass while the result carried the
            # mnu model name (same class as the w0 orphan, but silent).
            # cobaya/CAMB consume a sampled "mnu" directly; live-verified the
            # plik_lite likelihood responds (-2lnL 584->2044 at mnu 0.06->0.5).
            # omegak joined the same day: ok_* chains died at CAMB setup on
            # the bogus "curved" extra_arg and never sampled curvature at all;
            # CAMB consumes it as "omk" via the YAML builder's alias table.
            if param in {"w", "w0", "wa", "mnu", "omegak"} and param not in order:
                order.append(param)
        return order
    compressed_order = _compressed_parameter_order(entries)
    if compressed_order:
        return compressed_order
    fallback = [p for p in SUPPORTED_MODELS[model_key] if p in RUNNER_PARAMETER_PRIORS]
    return fallback or ["H0", "omegam"]


def _sanitize_runner_priors(
    parameters: list[str],
    priors: dict[str, Any] | None,
) -> dict[str, tuple[float, float]]:
    user = priors or {}
    if not isinstance(user, dict):
        raise ValueError("priors must be an object")
    unknown = set(user) - set(parameters)
    if unknown:
        raise ValueError(f"priors include unsupported compressed-runner parameters: {sorted(unknown)}")
    # Geometric + CMB-cobaya params share this validator; the CMB-only params live
    # in a separate dict so they don't leak into _compressed_parameter_order.
    bounds = {**RUNNER_PARAMETER_PRIORS, **CMB_PARAMETER_PRIORS}
    out: dict[str, tuple[float, float]] = {}
    for name in parameters:
        default_low, default_high = bounds[name]
        raw = user.get(name, (default_low, default_high))
        if isinstance(raw, dict):
            low_raw, high_raw = raw.get("min"), raw.get("max")
        elif isinstance(raw, (list, tuple)) and len(raw) == 2:
            low_raw, high_raw = raw
        else:
            raise ValueError(f"prior for {name} must be [min, max]")
        low, high = float(low_raw), float(high_raw)
        if not (math.isfinite(low) and math.isfinite(high)) or low >= high:
            raise ValueError(f"prior for {name} must have finite min < max")
        if low < default_low or high > default_high:
            raise ValueError(f"prior for {name} must stay within [{default_low}, {default_high}]")
        out[name] = (low, high)
    return out


def _posterior_summary(values: np.ndarray) -> dict[str, Any]:
    hdi_low = round(float(np.percentile(values, 3.0)), 6)
    hdi_high = round(float(np.percentile(values, 97.0)), 6)
    return {
        "mean": round(float(np.mean(values)), 6),
        "std": round(float(np.std(values)), 6),
        "median": round(float(np.median(values)), 6),
        "hdi_low_94": hdi_low,
        "hdi_high_94": hdi_high,
        "hdi_94": [hdi_low, hdi_high],
        # These are posterior summaries from an importance-resampled cloud,
        # not independent MCMC chains.  The citeability diagnostic lives in
        # chain_diagnostics.proposal_ess; do not imply per-parameter ESS=N.
        "rhat": None,
        "ess_bulk": None,
        "ess_tail": None,
        "status": "importance_resampled_summary",
        "diagnostic_note": "Use chain_diagnostics.proposal_ess for the publication gate.",
    }


def _combined_chi2(
    entries: list[CosmologyDatasetEntry],
    parameter_order: list[str],
    posterior_mean: np.ndarray,
) -> float:
    params = {name: float(posterior_mean[index]) for index, name in enumerate(parameter_order)}
    # Derived S8 at this parameter point (σ8·√(Ωm/0.3)) when both are present —
    # so the WL/Planck S8 rows still enter χ² even though S8 is not sampled.
    derived_s8 = (
        params["sigma8"] * math.sqrt(params["omegam"] / S8_PIVOT_OMEGAM)
        if "sigma8" in params and "omegam" in params
        else None
    )
    total = 0.0
    for entry in entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        names = [
            name
            for name in spec.parameters
            if name in params or (name == "S8" and derived_s8 is not None)
        ]
        if not names:
            continue
        local_idx = [list(spec.parameters).index(name) for name in names]
        mean = np.asarray(spec.mean, dtype=float)[local_idx]
        cov = np.asarray(spec.covariance, dtype=float)[np.ix_(local_idx, local_idx)]
        vec = np.asarray(
            [
                derived_s8 if (name == "S8" and name not in params) else params[name]
                for name in names
            ],
            dtype=float,
        )
        residual = vec - mean
        total += float(residual.T @ np.linalg.inv(cov) @ residual)
    return total


def _compressed_s8_mean_var(
    spec: CompressedLikelihoodSpec,
) -> tuple[float, float, str] | None:
    """Return (S8 mean, variance, source) for direct or derived S8 specs."""

    params = list(spec.parameters)
    mean = np.asarray(spec.mean, dtype=float)
    cov = np.asarray(spec.covariance, dtype=float)
    if "S8" in params:
        idx = params.index("S8")
        return float(mean[idx]), float(cov[idx][idx]), "direct"
    if "sigma8" not in params or "omegam" not in params:
        return None
    sigma_idx = params.index("sigma8")
    omegam_idx = params.index("omegam")
    sigma8 = float(mean[sigma_idx])
    omegam = float(mean[omegam_idx])
    if sigma8 <= 0 or omegam <= 0:
        return None
    s8 = sigma8 * math.sqrt(omegam / S8_PIVOT_OMEGAM)
    # First-order error propagation from (sigma8, Omega_m).  This is sufficient
    # for the compressed tension table; the full posterior still comes from the
    # sampler and is reported separately.
    grad = np.asarray([
        s8 / sigma8,
        0.5 * s8 / omegam,
    ])
    subcov = cov[np.ix_([sigma_idx, omegam_idx], [sigma_idx, omegam_idx])]
    variance = float(grad.T @ subcov @ grad)
    if not math.isfinite(variance) or variance <= 0:
        return None
    return float(s8), variance, "derived_from_sigma8_omegam"


def _pairwise_tensions(entries: list[CosmologyDatasetEntry]) -> list[dict[str, Any]]:
    tensions: list[dict[str, Any]] = []
    specs = [
        (entry, entry.compressed_likelihood)
        for entry in entries
        if entry.compressed_likelihood is not None
    ]
    for i, (left_entry, left_spec) in enumerate(specs):
        if left_spec is None:
            continue
        for right_entry, right_spec in specs[i + 1:]:
            if right_spec is None:
                continue
            common = [
                name for name in left_spec.parameters
                if name in right_spec.parameters and name in RUNNER_PARAMETER_PRIORS
            ]
            for name in common:
                li = list(left_spec.parameters).index(name)
                ri = list(right_spec.parameters).index(name)
                left_var = float(left_spec.covariance[li][li])
                right_var = float(right_spec.covariance[ri][ri])
                denom = math.sqrt(max(left_var + right_var, 0.0))
                if denom <= 0:
                    continue
                delta = float(left_spec.mean[li] - right_spec.mean[ri])
                tensions.append({
                    "parameter": name,
                    "dataset_a": left_entry.key,
                    "dataset_b": right_entry.key,
                    "delta": round(delta, 6),
                    "sigma": round(abs(delta) / denom, 3),
                    "value_a": float(left_spec.mean[li]),
                    "value_b": float(right_spec.mean[ri]),
                })
            if "S8" not in common:
                left_s8 = _compressed_s8_mean_var(left_spec)
                right_s8 = _compressed_s8_mean_var(right_spec)
                if left_s8 is not None and right_s8 is not None:
                    left_value, left_var, left_source = left_s8
                    right_value, right_var, right_source = right_s8
                    denom = math.sqrt(max(left_var + right_var, 0.0))
                    if denom > 0:
                        delta = left_value - right_value
                        tensions.append({
                            "parameter": "S8",
                            "dataset_a": left_entry.key,
                            "dataset_b": right_entry.key,
                            "delta": round(delta, 6),
                            "sigma": round(abs(delta) / denom, 3),
                            "value_a": round(left_value, 6),
                            "value_b": round(right_value, 6),
                            "comparison": "derived_pairwise",
                            "value_a_source": left_source,
                            "value_b_source": right_source,
                            "note": (
                                "S8 compared after deriving it from sigma8 and "
                                "Omega_m where a compressed summary did not carry "
                                "S8 directly."
                            ),
                        })
                elif (
                    ("S8" in left_spec.parameters or {"sigma8", "omegam"} <= set(left_spec.parameters))
                    and ("S8" in right_spec.parameters or {"sigma8", "omegam"} <= set(right_spec.parameters))
                ):
                    tensions.append({
                        "parameter": "S8",
                        "dataset_a": left_entry.key,
                        "dataset_b": right_entry.key,
                        "sigma": None,
                        "status": "not_comparable",
                        "reason": "S8 uncertainty could not be propagated from the registered compressed covariance.",
                    })
    tensions.sort(
        key=lambda item: float(item["sigma"]) if isinstance(item.get("sigma"), (int, float)) else -1.0,
        reverse=True,
    )
    return tensions


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
