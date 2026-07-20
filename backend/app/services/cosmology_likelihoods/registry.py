"""The curated dataset registry (_REGISTRY) and its read API.

Split verbatim out of the pre-2026-07-03 single-file
app/services/cosmology_likelihoods.py (7,757 lines). Import the package
``app.services.cosmology_likelihoods`` — it re-exports every pre-split name
and keeps the original one-namespace monkeypatch semantics.
"""

from __future__ import annotations

from typing import Any

from app.services.cosmology_likelihoods.core import (
    ALL_MODELS,
    BAO_MODELS,
    CMB_MODELS,
    CompressedLikelihoodSpec,
    CosmologyDatasetEntry,
    CovarianceSpec,
    DataProductSpec,
    DatasetCitation,
    DatasetStatus,
    H0_MODELS,
    MODEL_LABELS,
    SN_MODELS,
    SUPPORTED_MODELS,
    WL_MODELS,
)


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
        do_not_combine_with=(
            "desi_dr2_bao", "sdss_dr12_consensus_bao",
            "eboss_dr16_elg_bao", "eboss_dr16_lyauto_bao", "eboss_dr16_lyxqso_bao",
            # BOSS z=0.38/0.51 + eBOSS z=0.698 LRGs overlap DESI sky/z coverage
            # (same partition-at-z=0.6 rationale as sdss_dr12_consensus_bao).
            "eboss_dr16_lrg_fsbao",
            # eBOSS z=1.48 QSOs are re-observed by DESI (QSO bin z~1.49); the
            # DESI key papers replace eBOSS QSO rather than co-add.
            "eboss_dr16_qso_fsbao",
        ),
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
                "https://raw.githubusercontent.com/CobayaSampler/bao_data/"
                "b7b8a36e9bccb063081f811f323cada21ab5fbdd/"
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
                doi="10.1103/tr6y-kpc6",
            ),
            DatasetCitation(
                label="DESI Collaboration 2025 DR2 Ly-alpha BAO measurements",
                year=2025,
                arxiv="2503.14739",
            ),
        ),
        notes=(
            "DESI DR2 (2025) supersedes DR1 as the primary late-universe BAO "
            "distance anchor; it drove the w0waCDM dark-energy preference. Use as "
            "BAO-only or combined; requires an rd prior or CMB calibration."
        ),
        do_not_combine_with=(
            "desi_dr1_bao", "sdss_dr12_consensus_bao",
            "eboss_dr16_elg_bao", "eboss_dr16_lyauto_bao", "eboss_dr16_lyxqso_bao",
            # BOSS z=0.38/0.51 + eBOSS z=0.698 LRGs overlap DESI sky/z coverage
            # (same partition-at-z=0.6 rationale as sdss_dr12_consensus_bao).
            "eboss_dr16_lrg_fsbao",
            # eBOSS z=1.48 QSOs are re-observed by DESI (QSO bin z~1.49); the
            # DESI key papers replace eBOSS QSO rather than co-add.
            "eboss_dr16_qso_fsbao",
        ),
        cobaya_likelihood="external:desilike.desi_dr2_bao",
        cosmosis_module="likelihood/bao/desi-dr2/desi_dr2.py",
        execution_mode="compressed_gaussian",
        recommended_combinations=("planck2018_compressed", "bbn_ombh2_schoeneberg24"),
        data_products=(
            DataProductSpec(
                product_type="bao_measurement_vector",
                role="measurement_vector",
                url=(
                    "https://raw.githubusercontent.com/CobayaSampler/bao_data/"
                    "b7b8a36e9bccb063081f811f323cada21ab5fbdd/"
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
                    "https://raw.githubusercontent.com/CobayaSampler/bao_data/"
                    "b7b8a36e9bccb063081f811f323cada21ab5fbdd/"
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
                url=(
                    "https://github.com/CobayaSampler/bao_data/tree/"
                    "b7b8a36e9bccb063081f811f323cada21ab5fbdd/desi_bao_dr2"
                ),
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
        do_not_combine_with=(
            "eboss_dr16_lrg_fsbao", "eboss_dr16_qso_fsbao",
            "sdss_dr12_consensus_bao", "eboss_dr16_elg_bao",
        ),
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
            "Do NOT co-add with DESI BAO either: the BOSS z=0.38/0.51 bins and the eBOSS "
            "z=0.698 LRGs overlap DESI's sky/redshift coverage (DESI re-observes the same "
            "LRGs; the DESI key papers partition SDSS vs DESI at z=0.6 rather than co-add, "
            "and this 9-vector is indivisible). ELG (grid likelihood) and Lyα/MGS (BAO-only) "
            "are not part of this Gaussian FSBAO entry."
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
        do_not_combine_with=(
            "eboss_dr16_rsd",
            "sdss_dr12_consensus_bao",
            # Contains the BOSS z=0.38/0.51 galaxies plus eBOSS z=0.698 LRGs —
            # both overlap DESI's sky/z coverage (same rationale as the
            # sdss_dr12_consensus_bao ↔ DESI exclusion; the DESI key papers
            # partition at z=0.6 instead of co-adding).
            "desi_dr1_bao",
            "desi_dr2_bao",
        ),
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
            "co-add with 'eboss_dr16_rsd' (same QSO fσ8) — double-counts. Do NOT co-add "
            "with DESI BAO either: DESI re-observes the z~1.49 QSOs and the DESI key "
            "papers replace eBOSS QSO rather than co-add."
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
        do_not_combine_with=(
            "eboss_dr16_rsd",
            # DESI re-observes the z~1.49 QSOs; the DESI key papers replace
            # eBOSS QSO rather than co-add.
            "desi_dr1_bao",
            "desi_dr2_bao",
        ),
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
    "eboss_dr16_elg_bao": CosmologyDatasetEntry(
        key="eboss_dr16_elg_bao",
        display_name="eBOSS DR16 ELG BAO (non-Gaussian D_V/r_d table)",
        version="SDSS DR16 ELG BAO-only: released 399-point probability table for D_V/r_d at z=0.845",
        probe="bao",
        z_coverage=(0.845, 0.845),
        status="external_likelihood",
        observables=("DV_over_rs",),
        units={"DV_over_rs": "dimensionless"},
        applicable_models=BAO_MODELS,
        likelihood_family="bao_prob_grid",
        covariance=CovarianceSpec(
            kind="non-Gaussian probability table",
            provided=True,
            description=(
                "Released 1D probability density for D_V/r_d at the ELG effective "
                "redshift z=0.845 — the ELG posterior is visibly skewed, which is why "
                "the DR16 release ships a table instead of a Gaussian. Peak at "
                "D_V/r_d=18.33 (de Mattia et al. 2021)."
            ),
            url="https://github.com/CobayaSampler/bao_data",
            format="(D_V/r_d, probability) ASCII table",
        ),
        source_url="https://github.com/CobayaSampler/bao_data",
        citations=(
            DatasetCitation(label="Alam et al. eBOSS DR16 cosmological implications", year=2021, arxiv="2007.08991"),
            DatasetCitation(label="de Mattia et al. eBOSS DR16 ELG clustering", year=2021, arxiv="2007.09008"),
        ),
        notes=(
            "Executed in-process as chi2 = -2*ln P from the cubic log-probability "
            "spline over the released table (cobaya bao.sdss_dr16_bao_elg parity), "
            "predicting flat-w0waCDM D_V/r_d. Non-Gaussian table, not a Gaussian "
            "summary, despite the registry-wide execution_mode literal. Constrains "
            "(H0, Omega_m, r_d). Do NOT co-add with 'eboss_dr16_rsd' (same ELG "
            "galaxies feed its fsigma8 point) or DESI BAO (overlapping sky/structure)."
        ),
        data_products=(
            DataProductSpec(
                product_type="bao_probability_table",
                role="likelihood_grid",
                url="https://raw.githubusercontent.com/CobayaSampler/bao_data/master/sdss_DR16_ELG_BAO_DVtable.txt",
                format="ASCII 399x2 (D_V/r_d, probability)",
                description="eBOSS DR16 ELG BAO D_V/r_d probability table, vendored verbatim.",
                columns=("dv_over_rd", "probability"),
                rows=399,
                sha256="ebbd6b7a2946cf1903bac9e699702e6aa57a631799bb70421c8e7a55cb3d2c1f",
                local_path="data/cosmology/eboss_dr16_elg_bao/grid.txt",
            ),
        ),
        do_not_combine_with=("eboss_dr16_rsd", "desi_dr1_bao", "desi_dr2_bao"),
        cobaya_likelihood="bao.sdss_dr16_bao_elg",
        cosmosis_module="likelihood/bao/eboss_dr16/eboss_dr16_elg.py",
        execution_mode="compressed_gaussian",
    ),
    "eboss_dr16_lyauto_bao": CosmologyDatasetEntry(
        key="eboss_dr16_lyauto_bao",
        display_name="eBOSS DR16 Lyα auto BAO (2D likelihood grid)",
        version="SDSS DR16 Lyα forest auto-correlation BAO: 50×50 (D_M/r_d, D_H/r_d) likelihood grid at z=2.334",
        probe="bao",
        z_coverage=(2.334, 2.334),
        status="external_likelihood",
        observables=("DM_over_rs", "DH_over_rs"),
        units={"DM_over_rs": "dimensionless", "DH_over_rs": "dimensionless"},
        applicable_models=BAO_MODELS,
        likelihood_family="bao_prob_grid",
        covariance=CovarianceSpec(
            kind="non-Gaussian 2D likelihood grid",
            provided=True,
            description=(
                "Released 50×50 likelihood-ratio surface over (D_M/r_d, D_H/r_d) at "
                "z=2.334 from the Lyα forest auto-correlation — the only z>2 BAO "
                "anchor outside DESI. Peak at (37.76, 8.92) "
                "(du Mas des Bourboux et al. 2020)."
            ),
            url="https://github.com/CobayaSampler/bao_data",
            format="(D_M/r_d, D_H/r_d, likelihood) ASCII grid",
        ),
        source_url="https://github.com/CobayaSampler/bao_data",
        citations=(
            DatasetCitation(label="Alam et al. eBOSS DR16 cosmological implications", year=2021, arxiv="2007.08991"),
            DatasetCitation(label="du Mas des Bourboux et al. eBOSS DR16 Lyα BAO", year=2020, arxiv="2007.08995"),
        ),
        notes=(
            "Executed in-process as chi2 = -2*ln L from a bicubic spline of the "
            "released log-likelihood grid (cobaya bao.sdss_dr16_baoplus_lyauto "
            "parity; out-of-grid samples are REFUSED rather than extrapolated — a "
            "deliberate fail-safe deviation from cobaya). Non-Gaussian grid despite "
            "the registry-wide execution_mode literal. Constrains (H0, Omega_m, r_d). "
            "Combinable with the LYxQSO cross grid (the official suite multiplies "
            "them; their estimator cross-covariance is neglected by the release "
            "itself). Do NOT co-add with DESI BAO — DESI re-measures the same "
            "quasars/forest at z=2.33."
        ),
        data_products=(
            DataProductSpec(
                product_type="bao_likelihood_grid",
                role="likelihood_grid",
                url="https://raw.githubusercontent.com/CobayaSampler/bao_data/master/sdss_DR16_LYAUTO_BAO_DMDHgrid.txt",
                format="ASCII 2500x3 (D_M/r_d, D_H/r_d, likelihood)",
                description="eBOSS DR16 Lyα auto-correlation BAO likelihood grid, vendored verbatim.",
                columns=("dm_over_rd", "dh_over_rd", "likelihood"),
                rows=2500,
                sha256="40cee3a1c9dc58616ba7151ab9d020b0014238249409cd1ace71af14674e37e0",
                local_path="data/cosmology/eboss_dr16_lyauto_bao/grid.txt",
            ),
        ),
        do_not_combine_with=("desi_dr1_bao", "desi_dr2_bao"),
        cobaya_likelihood="bao.sdss_dr16_baoplus_lyauto",
        cosmosis_module="likelihood/bao/eboss_dr16/eboss_dr16_lyauto.py",
        execution_mode="compressed_gaussian",
    ),
    "eboss_dr16_lyxqso_bao": CosmologyDatasetEntry(
        key="eboss_dr16_lyxqso_bao",
        display_name="eBOSS DR16 Lyα×QSO cross BAO (2D likelihood grid)",
        version="SDSS DR16 Lyα×quasar cross-correlation BAO: 50×50 (D_M/r_d, D_H/r_d) likelihood grid at z=2.334",
        probe="bao",
        z_coverage=(2.334, 2.334),
        status="external_likelihood",
        observables=("DM_over_rs", "DH_over_rs"),
        units={"DM_over_rs": "dimensionless", "DH_over_rs": "dimensionless"},
        applicable_models=BAO_MODELS,
        likelihood_family="bao_prob_grid",
        covariance=CovarianceSpec(
            kind="non-Gaussian 2D likelihood grid",
            provided=True,
            description=(
                "Released 50×50 likelihood-ratio surface over (D_M/r_d, D_H/r_d) at "
                "z=2.334 from the Lyα×QSO cross-correlation. Peak at (37.44, 9.06) "
                "(du Mas des Bourboux et al. 2020)."
            ),
            url="https://github.com/CobayaSampler/bao_data",
            format="(D_M/r_d, D_H/r_d, likelihood) ASCII grid",
        ),
        source_url="https://github.com/CobayaSampler/bao_data",
        citations=(
            DatasetCitation(label="Alam et al. eBOSS DR16 cosmological implications", year=2021, arxiv="2007.08991"),
            DatasetCitation(label="du Mas des Bourboux et al. eBOSS DR16 Lyα BAO", year=2020, arxiv="2007.08995"),
        ),
        notes=(
            "Executed in-process as chi2 = -2*ln L from a bicubic spline of the "
            "released log-likelihood grid (cobaya bao.sdss_dr16_baoplus_lyxqso "
            "parity; out-of-grid samples are REFUSED rather than extrapolated). "
            "Non-Gaussian grid despite the registry-wide execution_mode literal. "
            "Constrains (H0, Omega_m, r_d). Combinable with the Lyα auto grid (the "
            "official suite multiplies them). Do NOT co-add with DESI BAO — DESI "
            "re-measures the same quasars/forest at z=2.33."
        ),
        data_products=(
            DataProductSpec(
                product_type="bao_likelihood_grid",
                role="likelihood_grid",
                url="https://raw.githubusercontent.com/CobayaSampler/bao_data/master/sdss_DR16_LYxQSO_BAO_DMDHgrid.txt",
                format="ASCII 2500x3 (D_M/r_d, D_H/r_d, likelihood)",
                description="eBOSS DR16 Lyα×QSO cross-correlation BAO likelihood grid, vendored verbatim.",
                columns=("dm_over_rd", "dh_over_rd", "likelihood"),
                rows=2500,
                sha256="653e2cea43a742d12090e9b7eacaf74dc7af7d7f6153a1a4c696d6303a7fb952",
                local_path="data/cosmology/eboss_dr16_lyxqso_bao/grid.txt",
            ),
        ),
        do_not_combine_with=("desi_dr1_bao", "desi_dr2_bao"),
        cobaya_likelihood="bao.sdss_dr16_baoplus_lyxqso",
        cosmosis_module="likelihood/bao/eboss_dr16/eboss_dr16_lyxqso.py",
        execution_mode="compressed_gaussian",
    ),
    "pantheon_plus": CosmologyDatasetEntry(
        key="pantheon_plus",
        display_name="Pantheon+",
        version="Pantheon+SH0ES DataRelease 2022",
        probe="sn",
        z_coverage=(0.001, 2.26),
        status="ready",
        observables=(
            "zHD", "zHEL", "m_b_corr", "IS_CALIBRATOR", "CEPH_DIST",
            "mu", "mu_covariance",
        ),
        units={"z": "dimensionless", "mu": "mag"},
        applicable_models=SN_MODELS,
        likelihood_family="sn_distance_modulus",
        covariance=CovarianceSpec(
            kind="stat+sys covariance",
            provided=True,
            description="Pantheon+ distance-modulus covariance matrix.",
            url="https://github.com/PantheonPlusSH0ES/DataRelease/tree/c447f0fea703fcd0fff57de5000947b5ca81286b",
            format="ASCII/FITS covariance in data release",
        ),
        source_url="https://github.com/PantheonPlusSH0ES/DataRelease/tree/c447f0fea703fcd0fff57de5000947b5ca81286b",
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
        notes=(
            "This key is the SH0ES-calibrated branch. The full runner applies "
            "the official calibrator selection and Cepheid distances; use a "
            "separate Pantheon+-only key for an uncalibrated SN-only analysis."
        ),
        cobaya_likelihood="external:sn.pantheon_plus",
        cosmosis_module="Pantheon+_Data/5_COSMOLOGY/cosmosis_likelihoods",
        nuisance_parameters=("M_B",),
        execution_mode="compressed_gaussian",
        data_products=(
            DataProductSpec(
                product_type="sn_distance_modulus_table",
                role="data_table",
                url=(
                    "https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/"
                    "c447f0fea703fcd0fff57de5000947b5ca81286b/"
                    "Pantheon%2B_Data/4_DISTANCES_AND_COVAR/Pantheon%2BSH0ES.dat"
                ),
                format="ASCII table",
                description="Pantheon+SH0ES supernova distance table.",
                columns=(
                    "CID", "zHD", "zCMB", "m_b_corr", "IS_CALIBRATOR",
                    "CEPH_DIST", "MU_SH0ES", "MU_SH0ES_ERR_DIAG",
                ),
                rows=1701,
            ),
            DataProductSpec(
                product_type="sn_covariance_matrix",
                role="covariance",
                url=(
                    "https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/"
                    "c447f0fea703fcd0fff57de5000947b5ca81286b/"
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
                    "https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/"
                    "c447f0fea703fcd0fff57de5000947b5ca81286b/"
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
                    "https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/"
                    "c447f0fea703fcd0fff57de5000947b5ca81286b/"
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
                    "https://raw.githubusercontent.com/PantheonPlusSH0ES/DataRelease/"
                    "c447f0fea703fcd0fff57de5000947b5ca81286b/"
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
                url="https://github.com/PantheonPlusSH0ES/DataRelease/tree/c447f0fea703fcd0fff57de5000947b5ca81286b",
                format="npz",
                description=(
                    "Vendored Pantheon+SH0ES 1701-row bundle including m_b_corr, "
                    "IS_CALIBRATOR, CEPH_DIST, and full stat+sys covariance; the "
                    "in-process likelihood applies the official 1657-row selection."
                ),
                columns=(
                    "z_hd", "z_hel", "mu", "mu_err_diag", "m_b_corr",
                    "is_calibrator", "cepheid_distance", "cov",
                ),
                rows=1701,
                sha256="bf0daa4ba2c06347db286d35f9f43c6de7c4fb85634e9f3821008911c7728bad",
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
                "Diagonal published SN+SH0ES posterior summary for literature "
                "context/proposal design only; not the full Pantheon+ likelihood."
            ),
            source_prior=(
                "Published Pantheon+SH0ES posterior summary after the source "
                "analysis calibration and cosmological priors; not deconvolved."
            ),
        ),
        research_roles=("sn_distance_ladder", "late_universe_distance", "dark_energy_matrix"),
        execution_level="context_only",
        independence_group="pantheon_plus_sn",
        claimable_parameters=(),
        recommended_combinations=("desi_dr1_bao", "planck2018_compressed"),
        # The context-only Pantheon+ posterior block IS the SH0ES-calibrated branch (its H0 mean
        # 73.04 ± 1.04 is the Riess+2022 SH0ES value), so co-adding the standalone
        # SH0ES H0 prior double-counts the identical measurement and halves the H0
        # variance. Keep them as robustness alternatives, never a joint fit.
        do_not_combine_with=("des_sn5yr", "union3", "shoes_h0_riess22", "pantheon18"),
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
            "Pantheon+/Union3. The registered SN-only flat-ΛCDM Ωm posterior "
            "summary (Ωm=0.352±0.017) is context-only and is not executed as a "
            "likelihood. The FULL 1829-SN distance-modulus vector + "
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
        do_not_combine_with=("pantheon_plus", "union3", "pantheon18"),
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("omegam",),
            mean=(0.352,),
            covariance=((0.017 ** 2,),),
            units={"omegam": "dimensionless"},
            source_locator="DES Collaboration (Abbott et al.) 2024 (arXiv:2401.02929) Table 2, Flat-ΛCDM SN-only / no external priors: Ωm = 0.352 ± 0.017.",
            approximation="Published 1D SN-only flat-ΛCDM Ωm posterior summary; context-only, NOT the full 1829-SN distance-modulus likelihood.",
            source_prior=(
                "Published flat-LambdaCDM SN-only posterior after source-analysis "
                "nuisance marginalisation; not deconvolved."
            ),
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
    "pantheon18": CosmologyDatasetEntry(
        key="pantheon18",
        display_name="Pantheon (2018)",
        version="Pantheon 2018 (Scolnic et al.) 1048-SN compilation",
        probe="sn",
        z_coverage=(0.0101, 2.26),
        status="external_likelihood",
        observables=("zcmb", "zhel", "mb", "mag_covariance"),
        units={"z": "dimensionless", "mb": "mag"},
        applicable_models=SN_MODELS,
        likelihood_family="sn_distance_modulus",
        covariance=CovarianceSpec(
            kind="stat+sys covariance",
            provided=True,
            description=(
                "Pantheon 2018 systematic covariance (sys_full_long, JLA format) plus "
                "the diagonal dmb² statistical terms — the cobaya sn.pantheon "
                "convention (pecz=0, intrinsicdisp=0)."
            ),
            url="https://github.com/CobayaSampler/sn_data",
            format="lcparam + JLA-format covariance",
        ),
        source_url="https://github.com/CobayaSampler/sn_data",
        citations=(
            DatasetCitation(label="Scolnic et al. Pantheon SN compilation", year=2018, arxiv="1710.00845"),
        ),
        notes=(
            "The SN anchor of the 2018-2022 literature era (quoted by Planck 2018 / "
            "DES-Y1 / eBOSS companion analyses). Its compressed SN-only flat-ΛCDM "
            "Ωm=0.298±0.022 record (Scolnic+18) is a published posterior summary for "
            "context/proposal use only and is never multiplied as a likelihood. The FULL "
            "1048-SN apparent-magnitude vector + stat+sys covariance is vendored "
            "(sha256-pinned text files, scripts/fetch_pantheon18.py from "
            "CobayaSampler/sn_data) and runs in-process as an offset-marginalized "
            "full-covariance χ² when PANTHEON18_FULL_CHI2_ENABLED is set (always via "
            "emcee — importance proposals cannot cover a 1048-SN ridge). The χ² "
            "analytically marginalizes M, so it constrains Ωm (+w0/wa), never H0. Do "
            "NOT co-add with Pantheon+/DES-SN5YR/Union3 — overlapping supernovae."
        ),
        cobaya_likelihood="sn.pantheon",
        cosmosis_module="likelihood/pantheon/pantheon.py",
        nuisance_parameters=("M_B",),
        do_not_combine_with=("pantheon_plus", "des_sn5yr", "union3"),
        execution_mode="compressed_gaussian",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("omegam",),
            mean=(0.298,),
            covariance=((0.022 ** 2,),),
            units={"omegam": "dimensionless"},
            source_locator="Scolnic et al. 2018 (arXiv:1710.00845) Table 8, SN-only flat-ΛCDM with systematics: Ωm = 0.298 ± 0.022.",
            approximation=(
                "1D SN-only flat-ΛCDM Ωm published posterior summary for "
                "context/proposal use only; NOT an executable Gaussian likelihood and "
                "NOT the full 1048-SN magnitude + covariance likelihood (env-gated)."
            ),
            source_prior=(
                "Published flat-LambdaCDM SN-only posterior after source-analysis "
                "systematics and nuisance marginalisation; not deconvolved."
            ),
        ),
        data_products=(
            DataProductSpec(
                product_type="sn_lcparam_vector",
                role="measurement_vector",
                url="https://raw.githubusercontent.com/CobayaSampler/sn_data/master/Pantheon/lcparam_full_long_zhel.txt",
                format="ASCII lcparam (name zcmb zhel dz mb dmb …)",
                description="Pantheon 2018 1048-SN light-curve parameter table, vendored verbatim.",
                columns=("name", "zcmb", "zhel", "dz", "mb", "dmb"),
                rows=1048,
                sha256="4e865e819eda499530b04da6965ab7aac0407878789b105732cb1f9b99a64323",
                local_path="data/cosmology/pantheon18/lcparam_full_long_zhel.txt",
            ),
            DataProductSpec(
                product_type="sn_mag_covariance",
                role="covariance",
                url="https://raw.githubusercontent.com/CobayaSampler/sn_data/master/Pantheon/sys_full_long.txt",
                format="JLA-format ASCII (N then N² values)",
                description="Pantheon 2018 systematic magnitude covariance, vendored verbatim.",
                columns=("cov_ij",),
                rows=1048,
                sha256="0ec3388b984a708f27bcedf7171c8a3e74621aca73dabb41a21246e9ae3fb53d",
                local_path="data/cosmology/pantheon18/sys_full_long.txt",
            ),
        ),
    ),
    "union3": CosmologyDatasetEntry(
        key="union3",
        display_name="Union3 / UNITY1.5",
        version="Union3 arXiv:2311.12098v4",
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
        source_url="https://arxiv.org/abs/2311.12098v4",
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
            source_locator="Rubin et al. (arXiv:2311.12098v4), PDF Table 9, Flat-ΛCDM SNe row: Ωm = 0.356 (+0.028/-0.026), with limits defined by Δχ²=1.",
            approximation="Symmetrized 1D Gaussian approximation to the published SN-only frequentist profile-χ² interval; literature anchor only, not the Table 9 statistical method or an executable Gaussian likelihood.",
            source_prior=(
                "Published flat-LambdaCDM SN-only frequentist profile-chi-square "
                "constraint with plus/minus limits at Delta chi-square = 1; retained "
                "only as a literature anchor."
            ),
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
            description=(
                "Correlated compressed CMB distance priors (R, l_A, Omega_b h^2, "
                "n_s) with the full 4x4 correlation matrix from Chen, Huang & "
                "Wang 2019 (arXiv:1808.05724) Table I, Planck 2018 TT,TE,EE+lowE "
                "base-LCDM."
            ),
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
            "Compressed CMB prior, not a replacement for the full Planck likelihood. "
            "EXECUTION (2026-07-07 upgrade): on the sampling path (any executable "
            "probe co-selected; every flat model, LCDM included) the executed CMB "
            "term is the CORRELATED Chen-Huang-Wang 2019 (arXiv:1808.05724, Table I) "
            "4-dim (R, l_A, ombh2, ns) distance-prior Gaussian — the only rows "
            "multiplied into chi-square. The (H0, Omega_m, sigma8, S8) block below "
            "is a Planck VI Table 2 base-LCDM posterior summary: it is proposal and "
            "literature context only, including for CMB-alone selections, and is "
            "never multiplied as a likelihood. Consequently the in-process distance-"
            "prior result does not constrain or report sigma8/S8; use the native "
            "Planck likelihood stack for growth-amplitude inference. Treat the "
            "distance-prior route as compressed-preliminary, not a full-likelihood "
            "constraint. Do NOT co-add with the "
            "native Planck 2018 stack entries (enforced via do_not_combine_with)."
        ),
        cobaya_likelihood="external:planck_2018_distance_prior",
        cosmosis_module="external:planck2018_distance_priors",
        execution_mode="compressed_gaussian",
        # This entry is a COMPRESSION of the same Planck 2018 data the clik-free
        # native stack fits directly (CHW2019 distance priors compress
        # TT,TE,EE+lowE; the parameter-summary/S8 rows quote the Planck VI
        # Table 2 TT,TE,EE+lowE+lensing column). Co-adding it with any part of
        # the full stack — or with the PR4/NPIPE lensing reprocessing of the
        # same maps — counts the same Planck data twice.
        do_not_combine_with=(
            "planck_2018_highl_TTTEEE_lite",
            "planck_2018_lowl_TT",
            "planck_2018_lowl_EE",
            "planck_2018_lensing",
            "planck_pr4_lensing",
            # act_dr6_lensing's executed numbers are the ACT+Planck JOINT
            # lensing summary, and this entry's S8 row quotes the
            # lensing-included Planck VI column — co-adding counts Planck
            # lensing twice.
            "act_dr6_lensing",
        ),
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
            source_locator=(
                "Executed distance priors: Chen, Huang & Wang 2019 (arXiv:1808.05724) "
                "Table I, Planck 2018 TT,TE,EE+lowE base-LCDM. Parameter summary rows "
                "(proposal/context only): Planck Collaboration VI 2020 Table 2 "
                "baseline. None of the H0/Omega_m/sigma8/S8 posterior rows is "
                "executed as a likelihood."
            ),
            approximation=(
                "Sampling path (flat models, any executable probe co-selected): the "
                "executed CMB chi2 is the correlated CHW2019 4-dim (R, l_A, ombh2, "
                "ns) distance-prior Gaussian. This diagonal parameter block feeds "
                "proposal anchoring and literature/tension context only; none of its "
                "four posterior-summary rows enters chi2. The distance prior carries "
                "no clustering-amplitude information, so sigma8/S8 are absent from "
                "this in-process result. Neither route is the full Planck likelihood."
            ),
            statistical_role="proposal_only",
            source_prior=(
                "The H0/Omega_m/sigma8/S8 rows are Planck base-LCDM posterior "
                "summaries. They inherit the source analysis priors and may not "
                "be multiplied as an independent likelihood."
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
        # planck2018_compressed is a compression of this same Planck 2018 data.
        do_not_combine_with=("planck2018_compressed",),
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
        # planck2018_compressed is a compression of this same Planck 2018 data.
        do_not_combine_with=("planck2018_compressed",),
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
        # planck2018_compressed is a compression of this same Planck 2018 data.
        do_not_combine_with=("planck2018_compressed",),
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
        do_not_combine_with=(
            "planck_pr4_lensing",
            # planck2018_compressed's S8 row quotes the Planck VI Table 2
            # TT,TE,EE+lowE+lensing column — it already contains this lensing
            # information (and the analytic no-probe path uses the full
            # parameter-level summary).
            "planck2018_compressed",
            # act_dr6_lensing's executed compressed numbers are the ACT+Planck
            # JOINT lensing summary — co-adding counts Planck lensing twice.
            "act_dr6_lensing",
        ),
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
            description=(
                "ACT DR6 lensing likelihood data tarball and likelihood code "
                "(the real standalone-ACT bandpower product, NASA LAMBDA). NOTE: "
                "the registered compressed spec below does NOT use this tarball — "
                "its numbers are hand-typed from the ACT+Planck joint summary "
                "(see notes)."
            ),
            url="https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_lh_get.html",
            format="ACT_dr6_likelihood_v1.2.tgz",
        ),
        source_url="https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_lh_info.html",
        citations=(
            DatasetCitation(label="Madhavacheril et al. ACT DR6 lensing", year=2024, arxiv="2304.05203"),
            DatasetCitation(label="Carron, Mirmelstein & Lewis likelihood method", year=2022, arxiv="2206.07773"),
        ),
        notes=(
            "COMPRESSED NUMBERS ARE NOT ACT-ONLY: the registered (H0, sigma8, S8) "
            "record is a context-only posterior summary hand-typed from the ACT DR6 "
            "lensing paper's ACT+Planck joint result. It is never executed as a "
            "Gaussian likelihood, is not a standalone ACT constraint, and must NOT "
            "be co-added with planck_2018_lensing / "
            "planck_pr4_lensing (double-counts Planck lensing; enforced via "
            "do_not_combine_with). The real standalone-ACT bandpower likelihood "
            "is STAGED (2026-07-07): act_dr6_lenslike pip-installed, adapter "
            "filled, act_baseline lens_only data vendored + sha256-pinned "
            "(data_products below) and reproducing the package's reference "
            "chi2 = 14.06 — but live cobaya execution still needs the "
            "cobaya_runner runtime-hash gate + YAML wiring, so execution_mode "
            "stays compressed_gaussian. Until that flips, treat the registered "
            "numbers as literature context only."
        ),
        cobaya_likelihood="external:act_dr6_lenslike.ACTDR6LensLike",
        cosmosis_module="external:act_dr6_lenslike",
        execution_mode="compressed_gaussian",
        # The registered context-only numbers are the ACT+Planck JOINT lensing
        # posterior summary — co-adding them with a Planck lensing likelihood (or with
        # planck2018_compressed, whose S8 row quotes the lensing-included
        # Planck VI column) counts Planck lensing twice.
        do_not_combine_with=(
            "planck_2018_lensing",
            "planck_pr4_lensing",
            "planck2018_compressed",
        ),
        # Real act_baseline lens_only likelihood inputs (2026-07-07): fetched
        # from the official NASA LAMBDA tarball by
        # scripts/fetch_act_dr6_lenslike.py, vendored under the cobaya
        # InstallableLikelihood get_path convention. Hash-verification against
        # these pins is provided by
        # cosmology_likelihoods.cmb.load_verified_act_dr6_lenslike_data —
        # enforced by the test suite today; live runner wiring must call it
        # before execution (no runtime path executes these files yet).
        # Reproduces the package's own reference chi2 = 14.06 (act_baseline
        # lens_only at the bundled fiducial spectra; act_dr6_lenslike
        # tests/test_act.py) — pinned by tests/test_act_dr6_lenslike.py.
        data_products=(
            DataProductSpec(
                product_type="lensing_likelihood_tarball",
                role="source_tarball",
                url=(
                    "https://lambda.gsfc.nasa.gov/data/suborbital/ACT/ACT_dr6/"
                    "likelihood/data/ACT_dr6_likelihood_v1.2.tgz"
                ),
                format="gzipped tar, 361,306,879 bytes (2024-02-01)",
                description=(
                    "Official NASA LAMBDA ACT DR6 lensing likelihood data tarball. "
                    "NOT vendored whole (345 MB); the fetch script verifies this "
                    "pin before extracting the subset below."
                ),
                sha256="bbcde3bcacd7c9a97138c4873c8a1217635a18504d15c4f86b1fba39d3601085",
            ),
            DataProductSpec(
                product_type="cmb_lensing_bandpowers",
                role="measurement_vector",
                url="https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_lh_get.html",
                format="v1.2/clkk_bandpowers_act.txt (18 binned C_L^kappakappa bandpowers)",
                description=(
                    "ACT DR6 binned lensing bandpowers (the data vector; the "
                    "act_baseline analysis range keeps bins [2:-6] of the 18)."
                ),
                rows=18,
                sha256="7660d216c48aa639dd374a6284b927a2821427fca8e98a3a7845da47c16806ae",
                local_path="data/cobaya_packages/data/ACT_dr6_likelihood/v1.2/clkk_bandpowers_act.txt",
            ),
            DataProductSpec(
                product_type="cmb_lensing_binning_matrix",
                role="binning_matrix",
                url="https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_lh_get.html",
                format="v1.2/binning_matrix_act.txt (18x3000)",
                description="Binning matrix applied to the theory C_L^kappakappa curve.",
                rows=18,
                sha256="a88fa3dc1ac5289e0580d8d0c1c3e9ee149f06cf62dad328cb5a248fd9985084",
                local_path="data/cobaya_packages/data/ACT_dr6_likelihood/v1.2/binning_matrix_act.txt",
            ),
            DataProductSpec(
                product_type="cmb_lensing_covariance",
                role="covariance_cmbmarg",
                url="https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_lh_get.html",
                format="v1.2/covmat_act_cmbmarg.txt (18x18)",
                description=(
                    "CMB-marginalized bandpower covariance — the one lens_only "
                    "runs use (Hartlap-corrected at load time, nsims_act=796)."
                ),
                rows=18,
                sha256="18ce4a7c542b7e23ecc17d492a8dbf748bf84a3bc30dc337a8999d9f4925c294",
                local_path="data/cobaya_packages/data/ACT_dr6_likelihood/v1.2/covmat_act_cmbmarg.txt",
            ),
            DataProductSpec(
                product_type="cmb_lensing_covariance",
                role="covariance",
                url="https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_lh_get.html",
                format="v1.2/covmat_act.txt (18x18)",
                description=(
                    "Non-marginalized bandpower covariance; loaded UNCONDITIONALLY "
                    "by act_dr6_lenslike.load_data as an internal consistency test, "
                    "so it is pinned alongside the executed covariance."
                ),
                rows=18,
                sha256="e710817fc88e0321e6d2a8dc3805489ada0df3f3d930e6e341e4aa929a36f361",
                local_path="data/cobaya_packages/data/ACT_dr6_likelihood/v1.2/covmat_act.txt",
            ),
            DataProductSpec(
                product_type="cmb_fiducial_spectra",
                role="fiducial_lensed_cls",
                url="https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_lh_get.html",
                format="v1.2/like_corrs/cosmo2017_10K_acc3_lensedCls.dat",
                description=(
                    "Fiducial lensed CMB spectra shipped with the release — the "
                    "theory input of the package's chi2=14.06 reference test "
                    "(also read by load_data when like_corrections=True)."
                ),
                sha256="8ca800b013145473f837a7e96d19a2c14972146c29e63fe7966a33f2bfeff47c",
                local_path="data/cobaya_packages/data/ACT_dr6_likelihood/v1.2/like_corrs/cosmo2017_10K_acc3_lensedCls.dat",
            ),
            DataProductSpec(
                product_type="cmb_fiducial_spectra",
                role="fiducial_lenspotential_cls",
                url="https://lambda.gsfc.nasa.gov/product/act/actadv_dr6_lensing_lh_get.html",
                format="v1.2/like_corrs/cosmo2017_10K_acc3_lenspotentialCls.dat",
                description=(
                    "Fiducial lensing-potential spectrum shipped with the release "
                    "— the C_L^kappakappa theory input of the chi2=14.06 reference."
                ),
                sha256="53d01931defba4cadda8781f9d6049cc56fea075295f58405812096abc2da9ae",
                local_path="data/cobaya_packages/data/ACT_dr6_likelihood/v1.2/like_corrs/cosmo2017_10K_acc3_lenspotentialCls.dat",
            ),
        ),
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
                "Diagonal compressed summary hand-typed from the ACT+Planck JOINT "
                "lensing results (abstract level) — NOT a standalone ACT-only "
                "constraint and NOT statistically independent of Planck lensing. "
                "Published posterior context only; never execute as a likelihood."
            ),
            source_prior=(
                "Published ACT+Planck-lensing posterior after the source analysis "
                "cosmology and nuisance priors; not deconvolved."
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
        # planck2018_compressed / act_dr6_lensing both carry Planck-lensing
        # information in their executed compressed numbers (Table 2 +lensing
        # column; ACT+Planck joint summary) — co-adding double-counts it.
        do_not_combine_with=(
            "planck_2018_lensing",
            "planck2018_compressed",
            "act_dr6_lensing",
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
            source_prior=(
                "Published KiDS-1000 posterior after intrinsic-alignment, shear, "
                "photo-z, and cosmological priors; not deconvolved."
            ),
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
            source_prior=(
                "Published DES Y3 posterior after source-analysis nuisance and "
                "cosmological priors; not deconvolved."
            ),
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
            source_prior=(
                "Published HSC Y1 posterior after intrinsic-alignment, shear, "
                "photo-z, and cosmological priors; not deconvolved."
            ),
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
            statistical_role="external_prior",
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
            statistical_role="external_prior",
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
            statistical_role="external_prior",
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
        applicable_models=("lcdm",),
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
            "approximation of the published +1.7/-1.8 asymmetric flat-LambdaCDM "
            "posterior and is therefore registered for lcdm only; "
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
                "Flat-LambdaCDM scalar Gaussian H0 approximation; symmetrized "
                "1.75 sigma from the published +1.7/-1.8 asymmetric error."
            ),
            statistical_role="external_prior",
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
            statistical_role="external_prior",
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
            "Standard BBN omega_b prior for an external sound-horizon forward "
            "model. The current in-process BAO runner samples r_d freely and has "
            "no BBN omega_b -> r_d mapping, so this entry is configuration/context "
            "only and must not be advertised as a CMB-free BAO+BBN H0 inference. "
            "Schöneberg 2024 also reports 0.02196 +/- 0.00063 under ab-initio "
            "Deuterium rates."
        ),
        cobaya_likelihood="gaussian:ombh2=0.02218,sigma=0.00055",
        cosmosis_module="prior ombh2 = gaussian 0.02218 0.00055",
        execution_mode="config_only",
        compressed_likelihood=CompressedLikelihoodSpec(
            parameters=("ombh2",),
            mean=(0.02218,),
            covariance=((0.00055 ** 2,),),
            units={"ombh2": "dimensionless"},
            source_locator="Schöneberg 2024 (arXiv:2401.15054) conservative LCDM BBN omega_b h^2; PDG light-element abundances.",
            approximation="Scalar Gaussian omega_b h^2 prior; PRyMordial nuclear-rate marginalization.",
            statistical_role="external_prior",
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
        version="SPT-SZ 2500d cluster catalog + weak-lensing/X-ray mass calibration",
        probe="cluster",
        status="metadata_only",
        observables=("sigma8", "omegam", "sigma8_omegam_0p2"),
        units={
            "sigma8": "dimensionless",
            "omegam": "dimensionless",
            "sigma8_omegam_0p2": "dimensionless",
        },
        applicable_models=ALL_MODELS,
        likelihood_family="cluster_count",
        covariance=CovarianceSpec(
            kind="published marginalized constraints; joint covariance not registered",
            provided=False,
            description=(
                "Bocquet et al. report marginalized constraints from the SPT-SZ "
                "cluster-count analysis, including Omega_m=0.276+/-0.047, "
                "sigma8=0.781+/-0.037, and "
                "sigma8(Omega_m/0.3)^0.2=0.766+/-0.025. A joint posterior "
                "covariance is not registered, so these numbers must not be "
                "expanded into an executable two-dimensional Gaussian."
            ),
            url="https://doi.org/10.3847/1538-4357/ab1f10",
            format="paper marginalized posterior summaries",
        ),
        source_url="https://doi.org/10.3847/1538-4357/ab1f10",
        citations=(
            DatasetCitation(
                label="Bocquet et al. SPT-SZ 2500d cluster cosmology",
                year=2019,
                arxiv="1812.01679",
                doi="10.3847/1538-4357/ab1f10",
            ),
        ),
        notes=(
            "Metadata-only until the public posterior chain or full cluster-count "
            "likelihood is ingested and hash-bound. The analysis supplements the "
            "343-cluster SZ sample with weak gravitational-lensing measurements "
            "of 32 clusters from Magellan/HST and X-ray measurements of 89 clusters "
            "from Chandra, and jointly fits mass-observable scaling relations and "
            "cosmology. It is therefore not a weak-lensing-free anchor. The published "
            "one-dimensional combination sigma8(Omega_m/0.3)^0.2 must not be treated "
            "as sigma8 at a fixed Omega_m or used to invent a two-dimensional "
            "covariance."
        ),
        execution_mode="config_only",
    ),
}


def list_cosmology_datasets(
    *,
    probe: str | None = None,
    status: DatasetStatus | None = None,
    dataset_keys: list[str] | None = None,
    requested_redshift: float | None = None,
) -> dict[str, Any]:
    requested_keys = [str(key).strip() for key in (dataset_keys or []) if str(key).strip()]
    unknown_keys = [key for key in requested_keys if key not in _REGISTRY]
    registry_entries = (
        [_REGISTRY[key] for key in requested_keys if key in _REGISTRY]
        if requested_keys
        else list(_REGISTRY.values())
    )
    selected_entries = [
        entry
        for entry in registry_entries
        if (probe is None or entry.probe == probe)
        and (status is None or entry.status == status)
    ]
    entries = [entry.to_dict() for entry in selected_entries]
    if not requested_keys:
        entries.sort(key=lambda item: item["key"])
    coverage_evaluations: list[dict[str, Any]] = []
    known_intervals: list[tuple[float, float]] = []
    if requested_redshift is not None:
        requested_z = float(requested_redshift)
        for entry in selected_entries:
            coverage = entry.z_coverage
            if coverage is None:
                coverage_evaluations.append({
                    "dataset_key": entry.key,
                    "coverage_status": "unknown",
                    "requested_redshift": requested_z,
                    "z_coverage_min": None,
                    "z_coverage_max": None,
                })
                continue
            z_min, z_max = float(coverage[0]), float(coverage[1])
            known_intervals.append((z_min, z_max))
            coverage_evaluations.append({
                "dataset_key": entry.key,
                "coverage_status": (
                    "within" if z_min <= requested_z <= z_max else "outside"
                ),
                "requested_redshift": requested_z,
                "z_coverage_min": z_min,
                "z_coverage_max": z_max,
            })
        evaluation_states = {
            item["coverage_status"] for item in coverage_evaluations
        }
        if "within" in evaluation_states:
            coverage_status = "within"
        elif evaluation_states == {"outside"}:
            coverage_status = "outside"
        else:
            coverage_status = "unknown"
    else:
        requested_z = None
        coverage_status = "not_requested"
        known_intervals = [
            (float(entry.z_coverage[0]), float(entry.z_coverage[1]))
            for entry in selected_entries
            if entry.z_coverage is not None
        ]
    z_coverage_min = (
        min(interval[0] for interval in known_intervals)
        if known_intervals
        else None
    )
    z_coverage_max = (
        max(interval[1] for interval in known_intervals)
        if known_intervals
        else None
    )
    return {
        "success": True,
        "registry_version": "2026-04-30",
        "dataset_count": len(entries),
        "datasets": entries,
        "requested_dataset_keys": requested_keys,
        "unknown_dataset_keys": unknown_keys,
        "coverage_status": coverage_status,
        "requested_redshift": requested_z,
        "z_coverage_min": z_coverage_min,
        "z_coverage_max": z_coverage_max,
        "coverage_evaluations": coverage_evaluations,
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
