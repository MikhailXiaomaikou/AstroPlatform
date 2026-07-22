"""Immutable production contract for the DESI ``w0wa`` exact profile.

This module intentionally contains data only.  Both the canonical offline
producer and the Research Alpha verifier import the same commitments, so a
fixture cannot silently redefine what an "exact" run means.  Test fixtures use
``CI_FIXTURE_PROFILE_ID`` and are never eligible for A-ready status.
"""

from __future__ import annotations


EXACT_PROFILE_ID = "desi_2024_vi_table3_desi_cmb_pantheonplus_v1"
CI_FIXTURE_PROFILE_ID = "research_alpha_ci_fixture_v1"
EXACT_CLAIM_SCOPE = "parameter_interval_reproduction_only"
PREREGISTERED_TARGET_COMMITMENT = (
    "sha256:3ff17dead8bc529f19262d6db745f13cb6a284af161c6ce9b44f1fe0925a5029"
)
TRUSTED_SOURCE_BASE_COMMIT = "ebb2f8d8eef202dbe8a8a85b0cb753829f3899a2"

# Exact evidence is anchored to one pre-provisioned local signing-key identity.
# The secret key is deliberately not stored in the repository; producers and
# verifiers compare this public commitment before accepting an exact-profile
# HMAC.  Changing either value requires a protocol amendment and fresh runs.
EXACT_EVIDENCE_SIGNING_KEY_ID = "w0wa-strict-20260713-local-v1"
EXACT_EVIDENCE_SIGNING_KEY_SHA256 = (
    "sha256:31f141e8fa1ec15d2bea246b368d5fac14d879dafaccbd3d83c743b63084e9c1"
)

# This is a trusted-host execution boundary, not hostile-host isolation.  A
# same-UID process, root, or the machine operator can read/replace local state
# and is therefore inside the trusted computing base.  The local HMAC receipts
# detect drift or public-artifact tampering only when the host and key remain
# outside the attacker's control; they are not external scientific review.
EXACT_HOST_EXECUTION_TRUST_BOUNDARY = {
    "schema_version": 1,
    "boundary_id": "trusted_local_macos_host_v1",
    "trusted_principals": ["same_uid_processes", "root", "machine_operator"],
    "receipt_assurance": (
        "detect_bound_artifact_drift_or_tampering_without_local_host_or_key_control"
    ),
    "not_provided": [
        "hostile_host_isolation",
        "same_uid_root_or_operator_resistance",
        "signing_key_compromise_resistance",
        "external_scientific_review",
    ],
}

# No human authority was frozen before this known-target run.  Environment
# variables may locate a preregistered public key in a future protocol, but can
# never create trust.  Making either registry non-empty requires a new immutable
# amendment and a fresh formal run; this profile can never escalate to strict A.
TRUSTED_PROTOCOL_AUTHORITY_REGISTRY: dict[str, str] = {}
TRUSTED_EXTERNAL_REVIEW_AUTHORITY_REGISTRY: dict[str, str] = {}
EXACT_MAX_READINESS_STATUS = "A_READY_PENDING_EXTERNAL_REVIEW"

# Security revision 2 deliberately remains fail-closed until a fresh exact
# preflight and independent scientific regression are recorded.  A later
# immutable amendment must change this status; ordinary CI cannot activate it.
EXACT_ENVIRONMENT_REVISION = {
    "schema_version": 1,
    "revision_id": "w0wa-exact-environment-r2-security-20260722",
    "status": "WITHHELD_PENDING_FRESH_PREFLIGHT_AND_SCIENCE_REGRESSION",
    "scientific_contract_changed": False,
    "supersedes_dependency_lock_sha256": (
        "sha256:6d40a07a26b021b3cab6de36dbec3df446115718998a25b68abf08bde0a7f833"
    ),
    "validation_requirements": [
        "fresh_exact_preflight",
        "fixed_reference_likelihood_regression",
        "independent_scientific_regression",
    ],
}
EXACT_ENVIRONMENT_FORMAL_STATUS = "VALIDATED_FOR_FORMAL_EXECUTION"
EXACT_ENVIRONMENT_PENDING_REASON = (
    "environment_revision_pending_fresh_preflight_and_science_regression"
)


def exact_environment_validated_for_formal_execution() -> bool:
    """Keep every exact-result consumer fail-closed until revision 2 is approved."""

    return (
        EXACT_ENVIRONMENT_REVISION.get("status")
        == EXACT_ENVIRONMENT_FORMAL_STATUS
    )

# The answer key stays outside every model prompt, but its uncertainty scales
# are an immutable verifier input.  Otherwise an adequacy producer could make
# any shift pass by supplying an arbitrarily large ``reference_sigma``.
PREREGISTERED_PAPER_UNCERTAINTIES = {
    "Omega_m": {"minus": 0.0068, "plus": 0.0068},
    "H0": {"minus": 0.72, "plus": 0.72},
    "w0": {"minus": 0.063, "plus": 0.063},
    "wa": {"minus": 0.25, "plus": 0.29},
}

PANTHEONPLUS_SOURCE_DATA = {
    "logical_path": "data/sn_data/PantheonPlus/Pantheon+SH0ES.dat",
    "sha256": "sha256:1cb0fc379ef066afdc2ffd1857681cc478024570d8a3eba284fb645775198cf8",
    "rows": 1701,
}
PANTHEONPLUS_STATONLY_COVARIANCE = {
    "sha256": "sha256:9f177129a332735d3637affd20054080d5260815f3ca0809120c05b2c902297f",
    "size_bytes": 31_827_416,
    "repository": "https://github.com/PantheonPlusSH0ES/DataRelease",
    "commit": "c447f0fea703fcd0fff57de5000947b5ca81286b",
    "git_blob_sha1": "bd0fef3f25150a3aee5100b3f35c895cbdf63235",
    "repository_path": (
        "Pantheon+_Data/4_DISTANCES_AND_COVAR/Pantheon+SH0ES_STATONLY.cov"
    ),
}

REQUIRED_SOURCE_STATE_PATHS = (
    "backend/app/services/research_alpha_evaluator.py",
    "backend/app/services/research_alpha_attestation.py",
    "backend/app/services/research_alpha_manifest.py",
    "backend/app/services/w0wa_exact_contract.py",
    "backend/scripts/blind_test_cosmology_m0/cases.yaml",
    "backend/scripts/blind_test_cosmology_m0/runner.py",
    "backend/scripts/cobaya/README_full_cmb_reproduction.md",
    "backend/scripts/cobaya/build_w0wa_wheel_manifest.py",
    "backend/scripts/cobaya/canonical_full_likelihood_evidence.py",
    "backend/scripts/cobaya/independent_w0wa_postprocess.py",
    "backend/scripts/cobaya/w0wa_desi_cmb_pantheonplus_exact.yaml",
    "backend/scripts/cobaya/w0wa_exact_data_manifest.json",
    "backend/scripts/cobaya/w0wa_exact_install.yaml",
    "backend/scripts/cobaya/w0wa_exact_likelihood_code_manifest.json",
    "backend/scripts/cobaya/w0wa_exact_reference_cases.json",
    "backend/scripts/cobaya/w0wa_exact_requirements.txt",
    "backend/scripts/cobaya/w0wa_exact_wheel_manifest.json",
    "docs/DESI_W0WA_A_READINESS_AMENDMENT_001.md",
    "docs/DESI_W0WA_A_READINESS_AMENDMENT_002.md",
    "docs/DESI_W0WA_A_READINESS_AMENDMENT_003.md",
    "docs/DESI_W0WA_A_READINESS_PROTOCOL.md",
)

TRUSTED_CANONICAL_CONFIG_SHA256 = (
    "sha256:63d194d45238f7ecc4b18602cc1699c2b3059aa2628eeaf82f61a4e83398dd7a"
)
TRUSTED_DATA_MANIFEST_SHA256 = (
    "sha256:41b6ae2b1cc7c1a13cb59e5a12ed65233d5880e0bcc3c38ae0e270d9b8f853a8"
)
TRUSTED_DATA_INVENTORY_SHA256 = (
    "sha256:d88788f6945b795478bd9b79767441dbda6d90db97112257f9532909b3d30518"
)
TRUSTED_ADEQUACY_DATA_INVENTORY_SHA256 = (
    "sha256:c039846636769d8d04779180371806de5fd31c7e041fc2bc6e5991aa39cb55c9"
)
TRUSTED_DEPENDENCY_LOCK_SHA256 = (
    "sha256:cd1f2fef709506ca19a7eda578f392e72fe5d81fa8a3ea83729df3935b84f8a3"
)
TRUSTED_REFERENCE_SPEC_SHA256 = (
    "sha256:f2714ff4ff89bbf431638cab93da0222a0d4784a75f83da4a51bebdccbe166ac"
)
TRUSTED_LIKELIHOOD_CODE_MANIFEST_SHA256 = (
    "sha256:2ac837fce8fc5d7114f49af41c3541dc7e1085289141453b15c9feb81a584b31"
)
TRUSTED_WHEEL_MANIFEST_SHA256 = (
    "sha256:37c9926fae0ebb49e833f6ecfd51001a11a96470a5631dfae8d58fb09d3bcb36"
)
TRUSTED_PROTOCOL_AMENDMENT_SHA256 = (
    "sha256:4ce9bea6bd412b22047bbe605f8068d7691bb204754cd2902cfce23d3b139378"
)
TRUSTED_NATIVE_RUNTIME_FINGERPRINT = (
    "sha256:6412e4f362e566dc66ff53d9090b18d12cbe1cfdb03e6c05ab9f3c7adff518e1"
)
TRUSTED_NATIVE_RUNTIME_SHA256 = {
    "python": "sha256:695fc18cdb9a699c6b1ea88ce4027854782ed704eb34cc3200e6f8380155b6a8",
    "mpirun": "sha256:cec3dd36d17e91059efcd196411f4964777479a3ba8a6c6633c802fe76468a3a",
    "mpi4py_extension": (
        "sha256:3fc97304ad93277d2f1f4ebf799b4446484243a123324d86dcb92d949fec7b22"
    ),
}

# These source hashes are part of the reviewable production closure.  They are
# deliberately updated only when the corresponding implementation is reviewed.
# The canonical producer hash is refreshed after changes to that producer.
TRUSTED_CODE_SHA256 = {
    "canonical_full_likelihood_evidence.py": (
        "sha256:8bdcb81691f08e89162f7772dc70063194346af61f149b932713b6d6ccab3893"
    ),
    "independent_w0wa_postprocess.py": (
        "sha256:731a7840f8d92e7a3dfa210169a78a68ae6f043b50e12328306b82fbc11dd64e"
    ),
}

# Deliberately empty until dedicated adequacy executors are implemented,
# reviewed, and preregistered.  The canonical orchestration/grade script is not
# a full-likelihood simulator and must never be relabelled as one.  Exact
# adequacy authority remains fail-closed while these registries are empty.
TRUSTED_ADEQUACY_RUNNER_CODE_SHA256: dict[str, str] = {}
TRUSTED_ADEQUACY_ANALYZER_CODE_SHA256: dict[str, str] = {}

REQUIRED_LIKELIHOODS = (
    "planck_2018_lowl.TT_clik",
    "planck_2018_lowl.EE_clik",
    "planck_2018_highl_plik.TTTEEE",
    "act_dr6_lenslike.ACTDR6LensLike",
    "bao.desi_2024_bao_all",
    "sn.pantheonplus",
)

# Exact byte commitments made before the formal run.  The Research Alpha
# verifier recomputes each group fingerprint from the submitted file records.
REQUIRED_DATA_GROUPS = {
    "planck_2018_lowl.TT_clik": {
        "file_count": 7,
        "total_size_bytes": 6_509_031,
        "fingerprint": (
            "sha256:3c085956c13d1c5e8cf7f73d21929dd3bcbb53c2c33dd8b48338d1b602de810e"
        ),
    },
    "planck_2018_lowl.EE_clik": {
        "file_count": 7,
        "total_size_bytes": 694_285,
        "fingerprint": (
            "sha256:830037a017589ebdfe2c04d48a945049939dd68aec8de36cd23a2603b44d6b9f"
        ),
    },
    "planck_2018_highl_plik.TTTEEE": {
        "file_count": 1_106,
        "total_size_bytes": 70_882_176,
        "fingerprint": (
            "sha256:0e725006145e06d40775dbd7d16d5009dac171ae22d0ab441a7375ba11006d39"
        ),
    },
    "act_dr6_lenslike.ACTDR6LensLike": {
        "file_count": 31,
        "total_size_bytes": 2_927_689_271,
        "fingerprint": (
            "sha256:f15724474cf23a926c904c82d73485a08d8e0f145840ab456e9199cc6ef8693e"
        ),
    },
    "bao.desi_2024_bao_all": {
        "file_count": 3,
        "total_size_bytes": 2_550,
        "fingerprint": (
            "sha256:9adc44fb4eeddf990ca81ec1388787cc5ce8fd0973d93aa7250c97c1a240c047"
        ),
    },
    "sn.pantheonplus": {
        "file_count": 4,
        "total_size_bytes": 33_864_404,
        "fingerprint": (
            "sha256:f8538497e7d4223ed2174d4f5fcedc47018ddcc30c8eb7c9552e3681ac4d9fe1"
        ),
    },
}

# The PR4 NPIPE CamSpec stack is used only by the preregistered adequacy
# comparison, not by the exact primary posterior.  Its complete file list is
# recorded by preflight and physically rehashed by the verifier; the immutable
# manifest intentionally freezes the aggregate rather than duplicating a long
# machine-local inventory.
REQUIRED_ADEQUACY_DATA_GROUPS = {
    "planck_NPIPE_highl_CamSpec.TTTEEE": {
        "file_count": 15,
        "total_size_bytes": 510_767_254,
        "fingerprint": (
            "sha256:d174212b9b0d88b1dc816b74ac117f00516e418c0fc456df4870968080c9a877"
        ),
    }
}

# ``cobaya.input.update_info`` on the frozen exact configuration yields this
# complete sampled set.  It includes every cosmological and Planck nuisance
# parameter; derived reporting parameters (H0, omegam) are intentionally absent.
REQUIRED_SAMPLED_PARAMETERS = (
    "ombh2",
    "omch2",
    "theta_MC_100",
    "tau",
    "ns",
    "logA",
    "w",
    "wa",
    "A_planck",
    "calib_100T",
    "calib_217T",
    "A_cib_217",
    "xi_sz_cib",
    "A_sz",
    "ksz_norm",
    "gal545_A_100",
    "gal545_A_143",
    "gal545_A_143_217",
    "gal545_A_217",
    "ps_A_100_100",
    "ps_A_143_143",
    "ps_A_143_217",
    "ps_A_217_217",
    "galf_TE_A_100",
    "galf_TE_A_100_143",
    "galf_TE_A_100_217",
    "galf_TE_A_143",
    "galf_TE_A_143_217",
    "galf_TE_A_217",
)

REQUIRED_PACKAGE_VERSIONS = {
    "cobaya": "3.6.2",
    "camb": "1.6.6",
    "clipy-like": "0.15",
    "act_dr6_lenslike": "1.2.1",
}

# ``pip`` bootstraps the isolated environment but is not a scientific runtime
# dependency.  It is the sole allowed distribution outside the 52-wheel lock;
# both its version and complete installed-file fingerprint remain frozen.
FROZEN_BOOTSTRAP_DISTRIBUTIONS = {
    "pip": {
        "version": "26.1.1",
        "fingerprint": (
            "sha256:8a41bf4eefa2adcf1686e5747c2623b599e38d1d6216a0901cf83c85610f9b73"
        ),
    }
}

# Python may create or refresh ``__pycache__`` after preflight while importing
# the already-frozen source tree.  Such caches are derived, non-authoritative
# artifacts only when their corresponding distribution-owned source exists;
# they are excluded from stable identity so normal imports cannot create false
# environment drift.  A sourceless or unowned cache remains a fatal closure
# violation.
GENERATED_BYTECODE_CACHE_POLICY = {
    "classification": "derived_non_authoritative_cache",
    "stable_identity": (
        "excluded_only_when_hashed_distribution_owned_source_exists"
    ),
    "source_ownership_required": True,
    "sourceless_or_unowned_cache": "fatal",
}

REQUIRED_WHEEL_SHA256 = {
    "cobaya-3.6.2-py3-none-any.whl": (
        "sha256:5e81da5900442c1a20a0ffa9c1ff212983106a7473b29555cecc2af3e4214da0"
    ),
    "camb-1.6.6-py3-none-macosx_14_0_arm64.whl": (
        "sha256:5fded11be64fea84f62dd82be0d947fdc37b749cdbf14308fce5c127d9edcd54"
    ),
    "clipy_like-0.15-py3-none-any.whl": (
        "sha256:9bf56a1c648cab345e9fcb7f83c9a79cd82f92e4f6fe4392900eec23b96f49d0"
    ),
    "act_dr6_lenslike-1.2.1-py2.py3-none-any.whl": (
        "sha256:bae5850191f51adeb2f5256441abf2a38cdbe9fc1e1d46c3bbe8937c50a53884"
    ),
}

PROTOCOL_STATUS = {
    "target_preregistration": "frozen",
    "computation_answer_key_separation": "enforced",
    "analyst_blinding": "not_achieved",
}

PREFLIGHT_SCHEMA = (2, "w0wa_exact_preflight")
GENERATION_SCHEMA = (2, "w0wa_exact_generation")
RUN_RECEIPT_SCHEMA = (2, "chain")
ANALYSIS_SCHEMA = (2, "w0wa_exact_analysis")
PRIMARY_OFFLINE_GRADE_SCHEMA = (1, "research_alpha_primary_offline_grade")
INDEPENDENT_POSTPROCESS_SCHEMA = (1, "independent_w0wa_postprocess")
INDEPENDENT_OFFLINE_GRADE_SCHEMA = (
    1,
    "research_alpha_independent_offline_grade",
)
