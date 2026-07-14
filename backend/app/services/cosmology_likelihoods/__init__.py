"""Curated observational-cosmology dataset registry and config builder.

This module is intentionally metadata-first.  It records which public
cosmology datasets the platform knows about, what they measure, which
models they can be used with, where their covariance/likelihood lives,
and which citations must follow any downstream result.

The builder emits controlled Cobaya/CosmoSIS-style configuration objects;
it does not silently run external likelihoods that are not installed.
"""

# ── Package split (2026-07-03) ──────────────────────────────────────────────
# The pre-split 7,757-line cosmology_likelihoods.py moved verbatim into the
# probe-family modules of this package (same discipline as the ai_tools and
# chat.py surgeries). This __init__ re-exports EVERY pre-split top-level name,
# preserves the module-level init order (BAO -> CC -> RSD vendored-data loads),
# and restores two behaviors the .py -> package conversion would otherwise
# break:
#   1. __file__ is pinned to the old module path — five tests derive
#      backend/data/... paths from Path(cl.__file__).parents[2].
#   2. Monkeypatching an attribute on this package (monkeypatch.setattr(cl,
#      "_run_emcee_chain", ...) etc.) used to rebind THE one namespace every
#      function read from. _SharedNamespaceModule fans such writes out to every
#      submodule that holds the name, reproducing the one-namespace semantics.
import pathlib as _pathlib
import sys as _sys
import types as _types

from app.services.cosmology_likelihoods.core import (
    logger,  # noqa: F401
    CosmologyModel,  # noqa: F401
    DatasetStatus,  # noqa: F401
    ExecutionMode,  # noqa: F401
    SUPPORTED_MODELS,  # noqa: F401
    DEFAULT_BUILDER_PRIORS,  # noqa: F401
    DatasetCitation,  # noqa: F401
    CovarianceSpec,  # noqa: F401
    DataProductSpec,  # noqa: F401
    CompressedLikelihoodSpec,  # noqa: F401
    CosmologyDatasetEntry,  # noqa: F401
    EXECUTABLE_COMPRESSED_STATISTICAL_ROLES,  # noqa: F401
    compressed_rows_are_executable,  # noqa: F401
    MODEL_LABELS,  # noqa: F401
    ALL_MODELS,  # noqa: F401
    SN_MODELS,  # noqa: F401
    BAO_MODELS,  # noqa: F401
    CMB_MODELS,  # noqa: F401
    H0_MODELS,  # noqa: F401
    WL_MODELS,  # noqa: F401
    RUNNER_PARAMETER_PRIORS,  # noqa: F401
    CMB_PARAMETER_PRIORS,  # noqa: F401
    S8_PIVOT_OMEGAM,  # noqa: F401
    _s8_is_derived,  # noqa: F401
    _derived_s8_from_samples,  # noqa: F401
    _drop_derived_s8,  # noqa: F401
    _s8_gaussian_constraints,  # noqa: F401
    C_LIGHT_KM_S,  # noqa: F401
)

from app.services.cosmology_likelihoods.registry import (
    _PANTHEON_PLUS_COMPRESSED_MEAN,  # noqa: F401
    _PANTHEON_PLUS_COMPRESSED_COV,  # noqa: F401
    _PANTHEON_PLUS_COMPRESSED_NAMES,  # noqa: F401
    _REGISTRY,  # noqa: F401
    list_cosmology_datasets,  # noqa: F401
    get_cosmology_dataset,  # noqa: F401
)

from app.services.cosmology_likelihoods.config_builder import (
    build_likelihood_config,  # noqa: F401
    build_robustness_matrix,  # noqa: F401
    _validate_model,  # noqa: F401
    _validate_dataset_selection,  # noqa: F401
    _sanitize_builder_priors,  # noqa: F401
    _build_cobaya_config,  # noqa: F401
    _build_cosmosis_config,  # noqa: F401
    _model_theory_args,  # noqa: F401
    _combination_warnings,  # noqa: F401
    _selection_warnings,  # noqa: F401
    _collect_citations,  # noqa: F401
    _config_hash,  # noqa: F401
)

from app.services.cosmology_likelihoods.data_io import (
    _VENDORED_COSMO_DATA_DIR,  # noqa: F401
    _registry_product_sha256,  # noqa: F401
    _load_verified_diagonal_vector,  # noqa: F401
)

from app.services.cosmology_likelihoods.distances import (
    _de_energy_density,  # noqa: F401
    _flat_de_distances_at_z,  # noqa: F401
    _flat_lcdm_distances_at_z,  # noqa: F401
    _GL64_NODES,  # noqa: F401
    _GL64_WEIGHTS,  # noqa: F401
    _flat_de_dm_grid_vectorized,  # noqa: F401
    _flat_lcdm_dm_grid_vectorized,  # noqa: F401
)

from app.services.cosmology_likelihoods.growth import (
    _GROWTH_GL32_NODES,  # noqa: F401
    _GROWTH_GL32_WEIGHTS,  # noqa: F401
    _growth_index_gamma,  # noqa: F401
    _omega_m_of_z,  # noqa: F401
    _growth_rate_f,  # noqa: F401
    _growth_factor_ratio,  # noqa: F401
)

from app.services.cosmology_likelihoods.bao import (
    DESI_DR1_BAO_MEAN_VECTOR,  # noqa: F401
    DESI_DR1_BAO_COVARIANCE,  # noqa: F401
    SDSS_6DF_BAO_MEAN_VECTOR,  # noqa: F401
    SDSS_6DF_BAO_COVARIANCE,  # noqa: F401
    _HARDCODED_BAO,  # noqa: F401
    MGS_ALPHA_RESCALE,  # noqa: F401
    MGS_ALPHA_BOUNDS,  # noqa: F401
    MGS_OUT_OF_BOUNDS_CHI2,  # noqa: F401
    load_verified_mgs_prob_table,  # noqa: F401
    _mgs_chi2_spline,  # noqa: F401
    _sdss_6df_mgs_chi2_samples,  # noqa: F401
    load_verified_bao_data,  # noqa: F401
    _RELEASED_ONLY_BAO_KEYS,  # noqa: F401
    _BAO_DATA,  # noqa: F401
    _BAO_FAST_PATH_KEYS,  # noqa: F401
    EBOSS_DR16_FSBAO_EXECUTABLE_KEYS,  # noqa: F401
    SDSS_DR12_CONSENSUS_EXECUTABLE_KEYS,  # noqa: F401
    SDSS_DR12_RS_FID_MPC,  # noqa: F401
    EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS,  # noqa: F401
    load_verified_fsbao_data,  # noqa: F401
    _FSBAO_DATA,  # noqa: F401
    load_verified_dr12_consensus_data,  # noqa: F401
    load_verified_grid_bao_data,  # noqa: F401
    _elg_logprob_spline,  # noqa: F401
    _lya_loglike_spline,  # noqa: F401
    _bao_chi2_samples,  # noqa: F401
    _bao_predictions,  # noqa: F401
    _fsbao_predictions,  # noqa: F401
    _fsbao_chi2_samples,  # noqa: F401
    _dr12_consensus_predictions,  # noqa: F401
    _dr12_chi2_samples,  # noqa: F401
    _EBOSS_ELG_GRID_Z,  # noqa: F401
    _EBOSS_LYA_GRID_Z,  # noqa: F401
    _grid_bao_chi2_samples,  # noqa: F401
    _grid_support_warnings,  # noqa: F401
)

from app.services.cosmology_likelihoods.cc import (
    COSMIC_CHRONOMETER_EXECUTABLE_KEYS,  # noqa: F401
    _HARDCODED_CC_HZ,  # noqa: F401
    load_verified_cc_data,  # noqa: F401
    COSMIC_CHRONOMETER_HZ,  # noqa: F401
    COSMIC_CHRONOMETER_FULL_COV_KEYS,  # noqa: F401
    load_verified_cc_full_cov_data,  # noqa: F401
    _MORESCO20_RAW,  # noqa: F401
    COSMIC_CHRONOMETER_MORESCO20_HZ,  # noqa: F401
    _MORESCO20_COV_INV,  # noqa: F401
    _cc_entry_point_count,  # noqa: F401
    _hz_predictions_for,  # noqa: F401
    _cosmic_chronometer_hz_predictions,  # noqa: F401
    _cosmic_chronometer_chi2_samples,  # noqa: F401
    _cosmic_chronometer_moresco20_chi2_samples,  # noqa: F401
)

from app.services.cosmology_likelihoods.rsd import (
    EBOSS_DR16_FSIGMA8_EXECUTABLE_KEYS,  # noqa: F401
    _HARDCODED_EBOSS_FSIGMA8,  # noqa: F401
    load_verified_rsd_data,  # noqa: F401
    EBOSS_DR16_FSIGMA8,  # noqa: F401
    _eboss_fsigma8_predictions,  # noqa: F401
    _eboss_fsigma8_chi2_samples,  # noqa: F401
)

from app.services.cosmology_likelihoods.sn import (
    PANTHEON_PLUS_FULL_CHI2_ENABLED,  # noqa: F401
    PANTHEON_PLUS_EXECUTABLE_KEYS,  # noqa: F401
    DES_SN5YR_FULL_CHI2_ENABLED,  # noqa: F401
    DES_SN5YR_EXECUTABLE_KEYS,  # noqa: F401
    PANTHEON18_FULL_CHI2_ENABLED,  # noqa: F401
    PANTHEON18_EXECUTABLE_KEYS,  # noqa: F401
    UNION3_EXECUTABLE_KEYS,  # noqa: F401
    _PANTHEON_PLUS_DATA_DIR,  # noqa: F401
    load_verified_pantheon_plus_data,  # noqa: F401
    _pantheon_plus_cov_inv,  # noqa: F401
    _DES_SN5YR_DATA_DIR,  # noqa: F401
    load_verified_des_sn5yr_data,  # noqa: F401
    _des_sn5yr_cov_inv,  # noqa: F401
    _load_des_sn5yr_data,  # noqa: F401
    _UNION3_DATA_DIR,  # noqa: F401
    _load_union3_raw,  # noqa: F401
    load_verified_union3_data,  # noqa: F401
    _union3_cov_inv,  # noqa: F401
    _PANTHEON18_DATA_DIR,  # noqa: F401
    _load_pantheon18_raw,  # noqa: F401
    load_verified_pantheon18_data,  # noqa: F401
    _pantheon18_cov_inv,  # noqa: F401
    _load_pantheon18_data,  # noqa: F401
    _pantheon18_chi2_samples,  # noqa: F401
    _load_union3_data,  # noqa: F401
    _load_pantheon_plus_data,  # noqa: F401
    PANTHEON_PLUS_M_B_REF,  # noqa: F401
    _pantheon_plus_chi2_samples,  # noqa: F401
    _offset_marginalized_sn_chi2,  # noqa: F401
    _des_sn5yr_chi2_samples,  # noqa: F401
    _union3_chi2_samples,  # noqa: F401
    _offset_sn_chi2_samples,  # noqa: F401
    _offset_sn_n_points,  # noqa: F401
)

from app.services.cosmology_likelihoods.cmb import (
    _T_CMB_K,  # noqa: F401
    _T_CMB_RATIO4,  # noqa: F401
    _RS_BARYON_COEF,  # noqa: F401
    _cmb_distance_priors,  # noqa: F401
    _PLANCK18_DP_MEAN,  # noqa: F401
    _PLANCK18_DP_SIGMA,  # noqa: F401
    _PLANCK18_DP_CORR,  # noqa: F401
    _PLANCK18_DP_INVCOV,  # noqa: F401
    _planck_distance_prior_chi2,  # noqa: F401
    _planck_dp_lcdm_proposal_moments,  # noqa: F401
    _compressed_chi2_samples,  # noqa: F401
    CMB_COBAYA_EXECUTABLE_KEYS,  # noqa: F401
    CMB_APLANCK_KEYS,  # noqa: F401
    PLANCK18_DP_PROPOSAL_MOMENTS,  # noqa: F401
    ACT_DR6_LENSLIKE_DATA_DIR,  # noqa: F401
    ACT_DR6_LENSLIKE_FILES,  # noqa: F401
    ACT_DR6_LENSLIKE_VERSION,  # noqa: F401
    load_verified_act_dr6_lenslike_data,  # noqa: F401
)

from app.services.cosmology_likelihoods.verification import (
    _COV_FIDELITY_ORDER,  # noqa: F401
    _entry_verification,  # noqa: F401
    _aggregate_cov_fidelity,  # noqa: F401
    _finalize_cov_fidelity,  # noqa: F401
    _MIXED_LITERATURE_PLUS_PINNED_OK,  # noqa: F401
    _executable_probe_keys,  # noqa: F401
    audit_executable_pins,  # noqa: F401
    _is_executable_bao_entry,  # noqa: F401
    _is_executable_cc_full_cov_entry,  # noqa: F401
    _is_executable_cc_entry,  # noqa: F401
    _is_executable_rsd_entry,  # noqa: F401
    _is_executable_fsbao_entry,  # noqa: F401
    _is_executable_dr12_entry,  # noqa: F401
    _is_executable_grid_bao_entry,  # noqa: F401
    _is_executable_sn_entry,  # noqa: F401
    _is_executable_des_sn_entry,  # noqa: F401
)

from app.services.cosmology_likelihoods.sampling import (
    _EMCEE_FALLBACK_ESS_FLOOR,  # noqa: F401
    _sn_emcee_bypass_active,  # noqa: F401
    _run_sampling_likelihood_chain,  # noqa: F401
    _sampling_parameter_order,  # noqa: F401
    _draw_uniform_prior_samples,  # noqa: F401
    _draw_desi_bao_only_posterior,  # noqa: F401
    _draw_gaussian_centered_proposal,  # noqa: F401
    _run_emcee_chain,  # noqa: F401
    _draw_importance_posterior,  # noqa: F401
    _sampling_source_records,  # noqa: F401
    _compressed_runner_unavailable,  # noqa: F401
    _compressed_parameter_order,  # noqa: F401
    _all_external_cobaya,  # noqa: F401
    _cobaya_parameter_order,  # noqa: F401
    _sanitize_runner_priors,  # noqa: F401
    _posterior_summary,  # noqa: F401
    _combined_chi2,  # noqa: F401
    _compressed_s8_mean_var,  # noqa: F401
    _pairwise_tensions,  # noqa: F401
)

from app.services.cosmology_likelihoods.runners import (
    compute_model_comparison,  # noqa: F401
    run_likelihood_chain,  # noqa: F401
    run_robustness_matrix,  # noqa: F401
    run_alcock_paczynski_test,  # noqa: F401
)


# (1) Pre-split __file__ pin — see header note. Path(...).resolve() works for
# paths that do not exist, so pointing at the retired .py path is safe.
__file__ = str(_pathlib.Path(__file__).resolve().parent.parent / "cosmology_likelihoods.py")

# (2) One-namespace monkeypatch semantics — see header note.
_NAMESPACE_MODULES = tuple(
    _sys.modules[f"{__name__}.{_sub}"]
    for _sub in (
        "core",
        "registry",
        "config_builder",
        "data_io",
        "distances",
        "growth",
        "bao",
        "cc",
        "rsd",
        "sn",
        "cmb",
        "verification",
        "sampling",
        "runners",
    )
)


class _SharedNamespaceModule(_types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name.startswith("__") and name.endswith("__"):
            return  # dunders (__spec__, __doc__, ...) are per-module, never fanned out
        for _module in _NAMESPACE_MODULES:
            if name in _module.__dict__:
                _module.__dict__[name] = value

    def __delattr__(self, name):
        super().__delattr__(name)
        if name.startswith("__") and name.endswith("__"):
            return
        for _module in _NAMESPACE_MODULES:
            _module.__dict__.pop(name, None)


_sys.modules[__name__].__class__ = _SharedNamespaceModule
