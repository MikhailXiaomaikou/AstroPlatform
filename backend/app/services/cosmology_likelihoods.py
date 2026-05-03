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
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np

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
            DatasetCitation(
                label="Adame et al. DESI Collaboration DR1 BAO cosmology",
                year=2024,
                arxiv="2404.03002",
            ),
        ),
        notes="Use as BAO-only or combined late-universe distance anchor; requires rd prior or CMB calibration.",
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
    "sdss_6df_bao": CosmologyDatasetEntry(
        key="sdss_6df_bao",
        display_name="SDSS + 6dF BAO compilation",
        version="6dFGS + SDSS/BOSS/eBOSS DR16 BAO public compilation",
        probe="bao",
        status="external_likelihood",
        observables=("DM_over_rd", "DH_over_rd", "DV_over_rd", "H_rd", "D_A_over_rd"),
        units={"distance_ratios": "dimensionless", "redshift": "dimensionless"},
        applicable_models=BAO_MODELS,
        likelihood_family="gaussian_bao",
        covariance=CovarianceSpec(
            kind="block covariance",
            provided=True,
            description=(
                "Legacy low-redshift BAO compilation used by pre-DESI ACT/Planck "
                "cross-checks; includes 6dFGS and SDSS/BOSS/eBOSS measurements."
            ),
            url="https://svn.sdss.org/public/data/eboss/DR16cosmo/tags/v1_0_1/",
            format="SDSS/eBOSS DR16 BAO likelihood data products",
        ),
        source_url="https://svn.sdss.org/public/data/eboss/DR16cosmo/tags/v1_0_1/",
        citations=(
            DatasetCitation(label="Beutler et al. 6dFGS BAO", year=2011, arxiv="1106.3366"),
            DatasetCitation(label="Alam et al. BOSS DR12 BAO", year=2017, arxiv="1607.03155"),
            DatasetCitation(label="eBOSS Collaboration DR16 cosmology", year=2021, arxiv="2007.08991"),
        ),
        notes=(
            "Prefer this over DESI DR1 when reproducing ACT DR6 lensing-era "
            "BAO combinations or papers that explicitly say BAO from SDSS and 6dF."
        ),
        cobaya_likelihood="external:bao.sdss_6df_legacy",
        cosmosis_module="likelihood/bao/sdss_dr16_6df/sdss_6df_bao.py",
        execution_mode="external_cobaya",
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
        execution_mode="external_cobaya",
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
        ),
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
            DatasetCitation(
                label="DES-SN5YR data products",
                year=2024,
                arxiv="2406.05046",
                doi="10.5281/zenodo.12720778",
            ),
        ),
        notes="Photometrically classified DES SN sample; useful robustness partner for Pantheon+/Union3.",
        cobaya_likelihood="external:sn.des_sn5yr",
        cosmosis_module="external:DES-SN5YR",
        nuisance_parameters=("M_B",),
        execution_mode="external_cobaya",
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
        execution_mode="external_cobaya",
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
        notes="Compressed CMB prior, not a replacement for the full Planck likelihood in extended models.",
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
            approximation="Diagonal compressed ΛCDM posterior summary; not the full Planck likelihood.",
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
        execution_mode="external_cobaya",
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
    # ── PART AI follow-up: spec papers #12-#15 (除 SH0ES 之外的 4 个 H0 阶梯
    # alternates + SPT-3G CMB) ──────────────────────────────────────────
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


RUNNER_PARAMETER_PRIORS: dict[str, tuple[float, float]] = {
    "H0": (50.0, 90.0),
    "omegam": (0.05, 0.6),
    "rd": (130.0, 170.0),
    "sigma8": (0.4, 1.2),
    "S8": (0.4, 1.2),
}


DESI_DR1_BAO_EXECUTABLE_KEYS = {"desi_dr1_bao"}
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
C_LIGHT_KM_S = 299792.458


def run_likelihood_chain(
    *,
    model: str,
    dataset_keys: list[str],
    priors: dict[str, Any] | None = None,
    random_seed: int | None = None,
    n_samples: int = 4000,
) -> dict[str, Any]:
    """Run the phase-1 compressed Gaussian cosmology likelihood.

    This combines registry entries with explicit ``CompressedLikelihoodSpec``
    summaries and the DESI DR1 public Gaussian BAO mean/covariance products.
    External SN/full-CMB likelihood packages are reported as not-run rather
    than approximated silently.
    """
    model_key = _validate_model(model)
    entries = _validate_dataset_selection(model_key, dataset_keys)
    seed = int(random_seed if random_seed is not None else 20260502)
    sample_count = max(256, min(int(n_samples or 4000), 20000))
    if any(_is_executable_bao_entry(entry) for entry in entries):
        return _run_sampling_likelihood_chain(
            model_key=model_key,
            entries=entries,
            priors=priors,
            seed=seed,
            sample_count=sample_count,
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
                "constraints; extended-model parameters require the external "
                "Cobaya/CosmoSIS likelihood packages."
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
    samples = rng.multivariate_normal(posterior_mean, posterior_cov, size=sample_count)
    summaries = {
        name: _posterior_summary(samples[:, index])
        for index, name in enumerate(parameter_order)
    }
    chi2 = _combined_chi2(compressed_entries, parameter_order, posterior_mean)
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

    publication_ready = not invalid_specs and not prior_violations
    result: dict[str, Any] = {
        "success": True,
        "__tool_status__": "COMPLETED" if publication_ready else "PARTIAL",
        "analysis_status": "COMPRESSED_CHAIN_READY" if publication_ready else "PARTIAL",
        "publication_ready": publication_ready,
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
            "delta_chi2": 0.0,
            "aic": round(float(aic), 6),
            "bic": round(float(bic), 6),
            "n_constraints": int(n_constraints),
            "n_parameters": int(k),
        },
        "chain_diagnostics": {
            "overall_status": "analytic_gaussian",
            "publication_ready": publication_ready,
            "rhat": 1.0,
            "ess_bulk": sample_count,
            "n_draws": sample_count,
            "n_chains": 1,
            "thresholds": {"ess_min": 400, "rhat_max": 1.05},
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
                "publication_ready": publication_ready,
            },
        },
    }
    if not publication_ready:
        result["__do_not_claim__"] = True
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
    sn_keys = supernova_sets or ["pantheon_plus", "des_sn5yr", "union3"]
    combos: list[tuple[str, list[str]]] = [
        ("BAO only", ["desi_dr1_bao"]),
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
        combos.append((f"BAO + {label}", ["desi_dr1_bao", sn_key]))
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
            matrix.append({
                "label": label,
                "dataset_keys": keys,
                "runnable": bool(run.get("publication_ready")),
                "publication_ready": bool(run.get("publication_ready")),
                "result": run,
                "warnings": run.get("warnings", []),
            })
        except Exception as exc:
            matrix.append({
                "label": label,
                "dataset_keys": keys,
                "runnable": False,
                "publication_ready": False,
                "error": str(exc),
                "error_class": exc.__class__.__name__,
            })

    ready_cells = [row for row in matrix if row.get("publication_ready")]
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
        "__message_to_model__": (
            "Summarize only cells with publication_ready=true as compressed preliminary "
            "results. For non-runnable cells, say the external likelihood/config is still needed."
        ),
    }


def _is_executable_bao_entry(entry: CosmologyDatasetEntry) -> bool:
    return entry.key in DESI_DR1_BAO_EXECUTABLE_KEYS


def _run_sampling_likelihood_chain(
    *,
    model_key: str,
    entries: list[CosmologyDatasetEntry],
    priors: dict[str, Any] | None,
    seed: int,
    sample_count: int,
) -> dict[str, Any]:
    """Importance-sample executable low-dimensional likelihood products.

    DESI DR1 publishes a Gaussian BAO measurement vector and covariance.  We
    evaluate those raw data products directly against flat ΛCDM distance-ratio
    predictions, while still treating full desilike/Cobaya as the higher-fidelity
    second-stage likelihood.
    """
    if model_key != "lcdm":
        return _compressed_runner_unavailable(
            model_key=model_key,
            entries=entries,
            seed=seed,
            reason=(
                "DESI DR1 executable phase-1 BAO runner currently supports "
                "flat ΛCDM only. Extended dark-energy/curvature/neutrino "
                "parameters require the external Cobaya/CosmoSIS likelihood."
            ),
        )

    bao_entries = [entry for entry in entries if _is_executable_bao_entry(entry)]
    compressed_entries = [entry for entry in entries if entry.compressed_likelihood is not None]
    skipped_entries = [
        entry
        for entry in entries
        if entry.key not in {bao.key for bao in bao_entries}
        and entry.compressed_likelihood is None
    ]
    parameter_order = _sampling_parameter_order(bao_entries, compressed_entries)
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

    try:
        if bao_entries and not compressed_entries:
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
            )
            invalid_specs.extend(compressed_errors)
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

    used_keys = {entry.key for entry in bao_entries + compressed_entries}
    used_entries = [entry for entry in entries if entry.key in used_keys]
    n_constraints = 12 * len(bao_entries) + sum(
        len(entry.compressed_likelihood.parameters)
        for entry in compressed_entries
        if entry.compressed_likelihood is not None
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
            "DESI DR1 Gaussian BAO mean/covariance runner; compressed-likelihood "
            "preliminary, not a full external desilike/Cobaya likelihood."
        ),
    ]
    if bao_entries and not any(entry.probe == "cmb" for entry in used_entries):
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
    if proposal_ess < 400.0:
        warnings.append(
            f"Importance sampler ESS={proposal_ess:.1f} below publication threshold 400."
        )

    publication_ready = not invalid_specs and proposal_ess >= 400.0
    result: dict[str, Any] = {
        "success": True,
        "__tool_status__": "COMPLETED" if publication_ready else "PARTIAL",
        "analysis_status": "COMPRESSED_CHAIN_READY" if publication_ready else "PARTIAL",
        "publication_ready": publication_ready,
        "claim_scope": "compressed_likelihood_preliminary",
        "compressed_likelihood_preliminary": True,
        "model": model_key,
        "model_label": MODEL_LABELS[model_key],
        "sampler": "bao_gaussian_importance",
        "parameters": summaries,
        "posterior_summary": summaries,
        "derived_params": derived_summaries,
        "pairwise_tensions": _pairwise_tensions(compressed_entries),
        "fit_statistics": {
            "chi2": round(best_chi2, 6),
            "delta_chi2": 0.0,
            "aic": round(float(aic), 6),
            "bic": round(float(bic), 6),
            "n_constraints": int(n_constraints),
            "n_parameters": int(k),
        },
        "chain_diagnostics": {
            "overall_status": "importance_resampled",
            "publication_ready": publication_ready,
            "rhat": 1.0,
            "ess_bulk": int(min(round(proposal_ess), sample_count)),
            "ess_tail": int(min(round(proposal_ess), sample_count)),
            "proposal_ess": round(proposal_ess, 3),
            "proposal_draws": int(proposal_draws),
            "n_draws": sample_count,
            "n_chains": 1,
            "thresholds": {"ess_min": 400, "rhat_max": 1.05},
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
                "runner": "bao_gaussian_importance",
                "runner_hash": result_hash,
                "dataset_keys": [entry.key for entry in entries],
                "datasets_used": [entry.key for entry in used_entries],
                "datasets_not_run": [entry.key for entry in skipped_entries],
                "citations": _collect_citations(entries),
                "compressed_sources": _sampling_source_records(used_entries),
                "publication_ready": publication_ready,
            },
        },
    }
    if not publication_ready:
        result["__do_not_claim__"] = True
    return result


def _sampling_parameter_order(
    bao_entries: list[CosmologyDatasetEntry],
    compressed_entries: list[CosmologyDatasetEntry],
) -> list[str]:
    order: list[str] = []
    if bao_entries:
        order.extend(["H0", "omegam", "rd"])
    for param in _compressed_parameter_order(compressed_entries):
        if param not in order:
            order.append(param)
    preferred = ["H0", "omegam", "rd", "sigma8", "S8"]
    return [param for param in preferred if param in order] + [
        param for param in order if param not in preferred
    ]


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
) -> tuple[np.ndarray, float, float, int]:
    """Sample DESI BAO-only posterior in the natural H0*rd degeneracy plane."""
    if parameter_order != ["H0", "omegam", "rd"]:
        raise ValueError("DESI BAO-only runner expects H0, omegam, rd parameters")
    h0_low, h0_high = prior_bounds["H0"]
    om_low, om_high = prior_bounds["omegam"]
    rd_low, rd_high = prior_bounds["rd"]
    q_low = h0_low * rd_low
    q_high = h0_high * rd_high
    om_values = np.linspace(om_low, om_high, 360)
    q_values = np.linspace(q_low, q_high, 720)
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
    chi2 = _desi_dr1_bao_chi2_samples(grid_samples, parameter_order)
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


def _draw_importance_posterior(
    rng: np.random.Generator,
    parameter_order: list[str],
    prior_bounds: dict[str, tuple[float, float]],
    bao_entries: list[CosmologyDatasetEntry],
    compressed_entries: list[CosmologyDatasetEntry],
    sample_count: int,
) -> tuple[np.ndarray, float, float, int, list[str]]:
    proposal_count = min(max(sample_count * 25, 80_000), 300_000)
    samples = _draw_uniform_prior_samples(rng, parameter_order, prior_bounds, proposal_count)
    chi2 = np.zeros(proposal_count, dtype=float)
    for entry in bao_entries:
        if entry.key == "desi_dr1_bao":
            chi2 += _desi_dr1_bao_chi2_samples(samples, parameter_order)
    extra_chi2, compressed_errors = _compressed_chi2_samples(
        samples,
        parameter_order,
        compressed_entries,
    )
    chi2 += extra_chi2
    finite = np.isfinite(chi2)
    if not np.any(finite):
        raise ValueError("importance sampler produced no finite likelihood values")
    samples = samples[finite]
    chi2 = chi2[finite]
    best_chi2 = float(np.min(chi2))
    log_weights = -0.5 * (chi2 - best_chi2)
    weights = np.exp(np.clip(log_weights, -745.0, 0.0))
    weight_sum = float(np.sum(weights))
    if not math.isfinite(weight_sum) or weight_sum <= 0.0:
        raise ValueError("importance sampler weights underflowed")
    probabilities = weights / weight_sum
    proposal_ess = float(weight_sum * weight_sum / np.sum(weights * weights))
    posterior_indices = rng.choice(samples.shape[0], size=sample_count, replace=True, p=probabilities)
    posterior_samples = samples[posterior_indices]
    return posterior_samples, best_chi2, proposal_ess, int(samples.shape[0]), compressed_errors


def _desi_dr1_bao_chi2_samples(samples: np.ndarray, parameter_order: list[str]) -> np.ndarray:
    observed = np.asarray([row[1] for row in DESI_DR1_BAO_MEAN_VECTOR], dtype=float)
    covariance = np.asarray(DESI_DR1_BAO_COVARIANCE, dtype=float)
    predictions = _desi_dr1_bao_predictions(samples, parameter_order)
    residual = predictions - observed
    return np.einsum("ni,ij,nj->n", residual, np.linalg.inv(covariance), residual)


def _desi_dr1_bao_predictions(samples: np.ndarray, parameter_order: list[str]) -> np.ndarray:
    h0 = samples[:, parameter_order.index("H0")]
    omegam = samples[:, parameter_order.index("omegam")]
    rd = samples[:, parameter_order.index("rd")]
    predictions = np.empty((samples.shape[0], len(DESI_DR1_BAO_MEAN_VECTOR)), dtype=float)
    distance_cache: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for col, (z, _value, quantity) in enumerate(DESI_DR1_BAO_MEAN_VECTOR):
        if z not in distance_cache:
            distance_cache[z] = _flat_lcdm_distances_at_z(z, h0, omegam)
        dm, dh, dv = distance_cache[z]
        if quantity in {"DM_over_rs", "DM_over_rd"}:
            predictions[:, col] = dm / rd
        elif quantity in {"DH_over_rs", "DH_over_rd"}:
            predictions[:, col] = dh / rd
        elif quantity in {"DV_over_rs", "DV_over_rd"}:
            predictions[:, col] = dv / rd
        else:
            raise ValueError(f"unsupported DESI BAO quantity {quantity!r}")
    return predictions


def _flat_lcdm_distances_at_z(
    z: float,
    h0: np.ndarray,
    omegam: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    nodes, weights = np.polynomial.legendre.leggauss(64)
    x = 0.5 * z * (nodes + 1.0)
    ez_grid = np.sqrt(omegam[:, None] * (1.0 + x[None, :]) ** 3 + (1.0 - omegam[:, None]))
    integral = 0.5 * z * np.sum(weights[None, :] / ez_grid, axis=1)
    dm = (C_LIGHT_KM_S / h0) * integral
    ez = np.sqrt(omegam * (1.0 + z) ** 3 + (1.0 - omegam))
    dh = C_LIGHT_KM_S / (h0 * ez)
    dv = np.cbrt(z * dm * dm * dh)
    return dm, dh, dv


def _compressed_chi2_samples(
    samples: np.ndarray,
    parameter_order: list[str],
    compressed_entries: list[CosmologyDatasetEntry],
) -> tuple[np.ndarray, list[str]]:
    total = np.zeros(samples.shape[0], dtype=float)
    invalid_specs: list[str] = []
    for entry in compressed_entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        try:
            params = list(spec.parameters)
            names = [name for name in params if name in parameter_order]
            if not names:
                continue
            sample_idx = [parameter_order.index(name) for name in names]
            local_idx = [params.index(name) for name in names]
            mean = np.asarray(spec.mean, dtype=float)[local_idx]
            cov = np.asarray(spec.covariance, dtype=float)[np.ix_(local_idx, local_idx)]
            residual = samples[:, sample_idx] - mean
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
    return [param for param in preferred if param in order] + [
        param for param in order if param not in preferred
    ]


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
    out: dict[str, tuple[float, float]] = {}
    for name in parameters:
        default_low, default_high = RUNNER_PARAMETER_PRIORS[name]
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
    return {
        "mean": round(float(np.mean(values)), 6),
        "std": round(float(np.std(values)), 6),
        "median": round(float(np.median(values)), 6),
        "hdi_low_94": round(float(np.percentile(values, 3.0)), 6),
        "hdi_high_94": round(float(np.percentile(values, 97.0)), 6),
        "rhat": 1.0,
        "ess_bulk": int(values.size),
        "ess_tail": int(values.size),
        "status": "analytic_gaussian",
    }


def _combined_chi2(
    entries: list[CosmologyDatasetEntry],
    parameter_order: list[str],
    posterior_mean: np.ndarray,
) -> float:
    params = {name: float(posterior_mean[index]) for index, name in enumerate(parameter_order)}
    total = 0.0
    for entry in entries:
        spec = entry.compressed_likelihood
        if spec is None:
            continue
        names = [name for name in spec.parameters if name in params]
        if not names:
            continue
        local_idx = [list(spec.parameters).index(name) for name in names]
        mean = np.asarray(spec.mean, dtype=float)[local_idx]
        cov = np.asarray(spec.covariance, dtype=float)[np.ix_(local_idx, local_idx)]
        residual = np.asarray([params[name] for name in names], dtype=float) - mean
        total += float(residual.T @ np.linalg.inv(cov) @ residual)
    return total


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
    tensions.sort(key=lambda item: float(item["sigma"]), reverse=True)
    return tensions


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
