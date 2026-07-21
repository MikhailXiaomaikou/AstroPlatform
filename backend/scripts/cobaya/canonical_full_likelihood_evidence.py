#!/usr/bin/env python3
"""Fail-closed evidence pipeline for the exact DESI 2024 VI w0wa run.

The A-readiness path is intentionally limited to reproducing the parameter
intervals in Table 3 for DESI+CMB+PantheonPlus. It does not test LambdaCDM,
derive a p-value or Bayes factor, or claim a dark-energy discovery. The workflow
is receipt-gated in the fixed order ``preflight -> generate -> run -> analyze ->
grade``. Every stage binds the exact configuration, data, installed code and
upstream receipt hashes. Smoke-run numbers are permanently non-citable.

The older paired-MAP helpers remain below for compatibility with archived proxy
runs, but they are not part of the exact-profile A-readiness path.

Run from ``backend/``. See ``README_full_cmb_reproduction.md`` for commands.
"""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import hashlib
import importlib.machinery
import importlib.metadata
import importlib.util
import io
import json
import math
import os
import platform
import secrets
import shlex
import shutil
import site
import stat
import subprocess
import sys
import sysconfig
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name


# Running this file directly sets ``sys.path[0]`` to ``scripts/cobaya``. Add the
# backend root so the production manifest signer is importable in the canonical
# CLI as well as under pytest.
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.research_alpha_attestation import (  # noqa: E402
    build_scientific_attestation,
    signing_key_binding,
    verification_key_for_id,
    verify_scientific_attestation,
)
from app.services.w0wa_exact_contract import (  # noqa: E402
    EXACT_CLAIM_SCOPE,
    EXACT_EVIDENCE_SIGNING_KEY_ID,
    EXACT_EVIDENCE_SIGNING_KEY_SHA256,
    EXACT_ENVIRONMENT_REVISION,
    EXACT_HOST_EXECUTION_TRUST_BOUNDARY,
    EXACT_PROFILE_ID,
    FROZEN_BOOTSTRAP_DISTRIBUTIONS,
    GENERATED_BYTECODE_CACHE_POLICY,
    PREREGISTERED_TARGET_COMMITMENT,
    PROTOCOL_STATUS as RESEARCH_ALPHA_PROTOCOL_STATUS,
    REQUIRED_LIKELIHOODS,
    REQUIRED_PACKAGE_VERSIONS,
    REQUIRED_SOURCE_STATE_PATHS as SOURCE_STATE_PATHS,
    REQUIRED_WHEEL_SHA256 as REQUIRED_WHEELS,
    TRUSTED_PROTOCOL_AUTHORITY_REGISTRY,
    TRUSTED_CANONICAL_CONFIG_SHA256,
    TRUSTED_DATA_MANIFEST_SHA256,
    TRUSTED_DEPENDENCY_LOCK_SHA256,
    TRUSTED_LIKELIHOOD_CODE_MANIFEST_SHA256,
    TRUSTED_NATIVE_RUNTIME_FINGERPRINT,
    TRUSTED_NATIVE_RUNTIME_SHA256,
    TRUSTED_PROTOCOL_AMENDMENT_SHA256,
    TRUSTED_REFERENCE_SPEC_SHA256,
    TRUSTED_SOURCE_BASE_COMMIT,
    TRUSTED_WHEEL_MANIFEST_SHA256,
)


SCHEMA_VERSION = 2
CONVERGED_EVIDENCE_CLASSES = frozenset({"formal_candidate", "model_adequacy"})
LAUNCHER_COMPLETION_ATTESTATION_TYPE = "w0wa_exact_launcher_completion"
LAUNCH_NONCE_DOMAIN = "standard-astro/w0wa-exact-launch/v1"
REQUIRED_CHAIN_COUNT = 4
RANK_RHAT_MAX_EXCLUSIVE = 1.01
# The user-preregistered floor was 400. DESI 2024 VI section 2.5 reports using
# chains with ESS approximately 10^3 for its quoted moment accuracy, so the
# public, pre-run amendment below applies the stricter (never weaker) value.
BULK_ESS_MIN = 1_000.0
DEFAULT_BURN_FRACTION = 0.30
MAX_EXPANDED_DRAWS_PER_CHAIN = 10_000_000
# ArviZ requires equal-length arrays. Permit at most 10% of any post-burn
# expanded chain to be discarded by the diagnostics-only alignment. This also
# fails a prematurely stopped or badly imbalanced chain instead of allowing the
# shortest chain to hide a long non-stationary prefix in its peers.
MIN_DIAGNOSTIC_ALIGNMENT_FRACTION = 0.90
REPORT_PARAMETERS = ("w", "wa", "omegam", "H0")
MODEL_PARAMETERS = {"w", "wa"}
REQUIRED_MODEL_ADEQUACY_CHECKS = (
    "prior_predictive_check",
    "posterior_predictive_check",
    "prior_sensitivity",
    "systematics_robustness",
    "simulation_recovery",
    "independent_reproduction",
)
ADEQUACY_REFERENCE_LIKELIHOODS = (
    "planck_NPIPE_highl_CamSpec.TTTEEE",
)
REFERENCE_LIKELIHOODS = (
    *REQUIRED_LIKELIHOODS,
    *ADEQUACY_REFERENCE_LIKELIHOODS,
)


def _require_exact_evidence_signing_key_binding() -> dict[str, Any]:
    """Return only the preregistered exact-profile signing-key commitment."""

    binding = signing_key_binding(require_explicit=True)
    if binding.get("key_id") != EXACT_EVIDENCE_SIGNING_KEY_ID:
        raise ValueError("exact evidence signing key id does not match the frozen contract")
    if binding.get("sha256") != EXACT_EVIDENCE_SIGNING_KEY_SHA256:
        raise ValueError(
            "exact evidence signing-key fingerprint does not match the frozen contract"
        )
    return binding

EXACT_PAPER = {
    "arxiv": "2404.03002v3",
    "table": "Table 3",
    "combination": "DESI+CMB+PantheonPlus",
}

# These are factual protocol-state records, not aspirational labels. The four
# target values were present in the implementation prompt, so analyst blinding
# was not achieved even though the compute-only stages never open the answer-key
# artifact. Grade must not auto-waive this deviation.
PROTOCOL_INTEGRITY = {
    "schema_version": 1,
    "target_preregistration": "FROZEN",
    "computation_answer_key_separation": "ENFORCED",
    "analyst_blinding": "NOT_ACHIEVED",
    "analyst_blinding_reason": "target_values_exposed_in_implementation_prompt",
    "permitted_description": "non_blinded_parameter_interval_reproduction",
    "a_ready_resolution_required": "independent_external_protocol_adjudication",
}
PAPER_FIDELITY_AMENDMENT = {
    "schema_version": 1,
    "amendment_id": "w0wa-ess-paper-fidelity-v1",
    "timing": "PRE_FORMAL_RUN",
    "change_type": "STRICTER_ONLY",
    "source": {"arxiv": "2404.03002v3", "section": "2.5"},
    "preregistered_bulk_ess_floor": 400.0,
    "paper_fidelity_bulk_ess_floor": 1_000.0,
    "effective_bulk_ess_floor": BULK_ESS_MIN,
    "hidden_target_changed": False,
}

PROTOCOL_AMENDMENT_PATH = (
    BACKEND_ROOT.parent / "docs" / "DESI_W0WA_A_READINESS_AMENDMENT_002.md"
)
TRUSTED_DATA_MANIFEST_PATH = (
    Path(__file__).resolve().parent / "w0wa_exact_data_manifest.json"
)
TRUSTED_WHEEL_MANIFEST_PATH = (
    Path(__file__).resolve().parent / "w0wa_exact_wheel_manifest.json"
)
TRUSTED_LIKELIHOOD_CODE_MANIFEST_PATH = (
    Path(__file__).resolve().parent / "w0wa_exact_likelihood_code_manifest.json"
)
EXACT_DEPENDENCY_LOCK_PATH = (
    Path(__file__).resolve().parent / "w0wa_exact_requirements.txt"
)
TRUSTED_SOURCE_ARCHIVE_ROOT = (
    BACKEND_ROOT.parent / ".local" / "w0wa-strict-a-readiness"
)
EXPECTED_SOURCE_ARCHIVES = {
    "COM_Likelihood_Data-baseline_R3.00.tar.gz": {
        "url": (
            "https://irsa.ipac.caltech.edu/data/Planck/release_3/software/"
            "COM_Likelihood_Data-baseline_R3.00.tar.gz"
        ),
        "size_bytes": 60_323_470,
    },
    "ACT_dr6_likelihood_v1.2.tgz": {
        "url": (
            "https://lambda.gsfc.nasa.gov/data/suborbital/ACT/ACT_dr6/"
            "likelihood/data/ACT_dr6_likelihood_v1.2.tgz"
        ),
        "size_bytes": 361_306_879,
    },
    "archives/CamSpec_NPIPE.v1.verified.zip": {
        "url": (
            "https://github.com/CobayaSampler/planck_native_data/releases/"
            "download/v1/CamSpec_NPIPE.zip"
        ),
        "size_bytes": 85_421_478,
        "source_asset_name": "CamSpec_NPIPE.zip",
        "release_asset_id": 84_169_207,
    },
}
PANTHEON_STATONLY_SOURCE_PATH = (
    TRUSTED_SOURCE_ARCHIVE_ROOT
    / "pantheonplus-data-release"
    / "Pantheon+_Data"
    / "4_DISTANCES_AND_COVAR"
    / "Pantheon+SH0ES_STATONLY.cov"
)
PANTHEON_STATONLY_SHA256 = (
    "sha256:9f177129a332735d3637affd20054080d5260815f3ca0809120c05b2c902297f"
)
PANTHEON_STATONLY_SIZE_BYTES = 31_827_416
PANTHEON_DATA_RELEASE_COMMIT = "c447f0fea703fcd0fff57de5000947b5ca81286b"
PANTHEON_STATONLY_GIT_BLOB = "bd0fef3f25150a3aee5100b3f35c895cbdf63235"
PROTOCOL_ADJUDICATION_PUBLIC_KEY_ENV = (
    "RESEARCH_ALPHA_PROTOCOL_AUTHORITY_PUBLIC_KEY_PATH"
)
PROTOCOL_ADJUDICATION_AUTHORITY_ENV = (
    "RESEARCH_ALPHA_PROTOCOL_AUTHORITY_ID"
)

RUNTIME_CLOSURE_ROOTS = (
    "cobaya",
    "camb",
    "clipy-like",
    "act_dr6_lenslike",
    "numpy",
    "scipy",
    "arviz",
    "getdist",
    "PyYAML",
    "mpi4py",
    "cryptography",
)
RUNTIME_MODULE_DISTRIBUTIONS = (
    ("camb", "camb"),
    ("cobaya", "cobaya"),
    ("clipy", "clipy-like"),
    ("act_dr6_lenslike", "act_dr6_lenslike"),
)
PINNED_REFERENCE_SOURCES: dict[str, dict[str, str]] = {
    "planck_pr3_commander_tt_clik": {
        "repository": "https://github.com/CobayaSampler/cobaya",
        "commit": "899f30a49f85de610dac321e91a1af50018e56aa",
        "path": "tests/test_cosmo_planck_2018.py",
        "test": "test_planck_2018_t_clik_camb",
        "point_symbol": "params_lowl_highTT_lensing",
    },
    "planck_pr3_simall_ee_clik_and_plik_ttteee": {
        "repository": "https://github.com/CobayaSampler/cobaya",
        "commit": "899f30a49f85de610dac321e91a1af50018e56aa",
        "path": "tests/test_cosmo_planck_2018.py",
        "test": "test_planck_2018_p_clik_camb",
        "point_symbol": "params_lowTE_highTTTEEE_lensingcmblikes",
    },
    "desi_dr1_bao_gaussian": {
        "repository": "https://github.com/CobayaSampler/cobaya",
        "commit": "899f30a49f85de610dac321e91a1af50018e56aa",
        "path": "tests/test_cosmo_bao.py",
        "test": "test_DESI_y1_camb",
        "point_symbol": "params_lowTEB_highTTTEEE",
    },
    "pantheonplus_full_covariance": {
        "repository": "https://github.com/CobayaSampler/cobaya",
        "commit": "899f30a49f85de610dac321e91a1af50018e56aa",
        "path": "tests/test_cosmo_sn.py",
        "test": "test_sn_pantheonplus_camb",
        "point_symbol": "params_lowTEB_highTTTEEE",
    },
    "act_dr6_planck_pr4_lensing_baseline": {
        "repository": "https://github.com/ACTCollaboration/act_dr6_lenslike",
        "commit": "b386ddbb5821c1216c709f051c9289292f174d30",
        "path": "act_dr6_lenslike/tests/test_cobaya.py",
        "test": "test_actplanck_baseline",
        "point_symbol": "info.params",
    },
    "planck_pr4_npipe_camspec_ttteee": {
        "repository": "https://github.com/CobayaSampler/cobaya",
        "commit": "899f30a49f85de610dac321e91a1af50018e56aa",
        "path": "tests/test_cosmo_planck_NPIPE.py",
        "test": "test_planck_NPIPE_p_CamSpec_camb",
        "point_symbol": "cosmo_params+nuisance_params",
    },
}

PINNED_REFERENCE_THEORY_ARGS: dict[str, dict[str, Any]] = {
    "planck_pr4_npipe_camspec_ttteee": {
        "lens_potential_accuracy": 1,
        "halofit_version": "mead2020",
        "bbn_predictor": "PArthENoPE_880.2_standard.dat",
    }
}

MINIMIZER_CONFIG = {
    "minimize": {
        "method": "bobyqa",
        "ignore_prior": False,
        "best_of": 4,
        "max_evals": "1e6d",
        "override_bobyqa": {
            # Tighter than Cobaya's noisy-likelihood default (0.05). The
            # resulting finite .minimum.txt is still checked independently.
            "rhoend": 0.01,
        },
    }
}

# Exact data products consumed by the paper-matched profile. Patterns are
# relative to Cobaya's packages directory. Whole likelihood trees are hashed
# when a product contains many internally referenced files: a package version
# or download URL is not a byte-level data certificate.
CANONICAL_DATA_ASSETS: dict[str, tuple[str, ...]] = {
    "planck_2018_lowl.TT_clik": (
        "data/planck_2018/baseline/plc_3.0/low_l/commander/commander_dx12_v3_2_29.clik/**/*",
    ),
    "planck_2018_lowl.EE_clik": (
        "data/planck_2018/baseline/plc_3.0/low_l/simall/simall_100x143_offlike5_EE_Aplanck_B.clik/**/*",
    ),
    "planck_2018_highl_plik.TTTEEE": (
        "data/planck_2018/baseline/plc_3.0/hi_l/plik/plik_rd12_HM_v22b_TTTEEE.clik/**/*",
        "data/planck_supp_data_and_covmats/**/*",
        "code/planck/clipy/**/*",
    ),
    "act_dr6_lenslike.ACTDR6LensLike": (
        "data/ACT_dr6_likelihood/v1.2/**/*",
    ),
    "bao.desi_2024_bao_all": (
        "data/bao_data/version.dat",
        "data/bao_data/desi_2024_gaussian_bao_ALL_GCcomb_mean.txt",
        "data/bao_data/desi_2024_gaussian_bao_ALL_GCcomb_cov.txt",
    ),
    "sn.pantheonplus": (
        "data/sn_data/version.dat",
        "data/sn_data/PantheonPlus/config.dataset",
        "data/sn_data/PantheonPlus/Pantheon+SH0ES.dat",
        "data/sn_data/PantheonPlus/Pantheon+SH0ES_STAT+SYS.cov",
    ),
}

ADEQUACY_DATA_ASSETS: dict[str, tuple[str, ...]] = {
    "planck_NPIPE_highl_CamSpec.TTTEEE": (
        "data/planck_NPIPE_CamSpec/version.dat",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/100x100_10.5_dust.dat",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/143x143_10.5_dust.dat",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/143x217_10.5_dust.dat",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/217x217_10.5_dust.dat",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/CamSpecHM.paramnames",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/CamSpec_NPIPE_12_6_cl.dataset",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/README.md",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/cib217.txt",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/cl_ksz_148_trac.dat",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/like_NPIPE_12.6_unified_cov.bin",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/like_NPIPE_12.6_unified_data_ranges.txt",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/like_NPIPE_12.6_unified_spectra.txt",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/sz_x_cib_template.dat",
        "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/tsz_143_eps0.50.dat",
    ),
}

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _hash_object(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value)).hexdigest()}"


def _hash_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _git_blob_sha1(path: str | Path) -> str:
    """Return Git's blob object id for source-provenance verification."""

    source = Path(path)
    size = source.stat().st_size
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"blob {size}\0".encode("ascii"))
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def protocol_amendment_record() -> dict[str, Any]:
    """Return the immutable public pre-run amendment or a fail-closed record."""

    path = PROTOCOL_AMENDMENT_PATH.resolve()
    if not path.is_file():
        return {
            "path": str(path),
            "sha256": None,
            "expected_sha256": TRUSTED_PROTOCOL_AMENDMENT_SHA256,
            "size_bytes": None,
            "valid": False,
        }
    observed = _hash_file(path)
    return {
        "path": str(path),
        "sha256": observed,
        "expected_sha256": TRUSTED_PROTOCOL_AMENDMENT_SHA256,
        "size_bytes": path.stat().st_size,
        "valid": observed == TRUSTED_PROTOCOL_AMENDMENT_SHA256,
    }


def verify_external_protocol_adjudication(
    path: str | Path | None,
    *,
    expected_run_id: str | None = None,
    expected_target_hash: str = PREREGISTERED_TARGET_COMMITMENT,
) -> dict[str, Any]:
    """Verify a detached waiver only against a preregistered authority."""

    reasons: list[str] = []
    if not TRUSTED_PROTOCOL_AUTHORITY_REGISTRY:
        reasons.append("external_protocol_adjudication_authority_registry_empty")
    adjudication_path = Path(path).expanduser().resolve() if path else None
    payload: dict[str, Any] = {}
    if adjudication_path is None:
        reasons.append("external_protocol_adjudication_not_provided")
    elif not adjudication_path.is_file():
        reasons.append("external_protocol_adjudication_missing")
    else:
        try:
            loaded = json.loads(adjudication_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reasons.append(
                f"external_protocol_adjudication_unreadable:{type(exc).__name__}"
            )
        else:
            if isinstance(loaded, dict):
                payload = loaded
            else:
                reasons.append("external_protocol_adjudication_not_mapping")

    if payload:
        authority = str(payload.get("authority_id") or "")
        expected_key_hash = TRUSTED_PROTOCOL_AUTHORITY_REGISTRY.get(authority)
        public_key_path_text = os.environ.get(PROTOCOL_ADJUDICATION_PUBLIC_KEY_ENV)
        if expected_key_hash is None:
            reasons.append("external_protocol_adjudication_authority_untrusted")
        if not public_key_path_text:
            reasons.append("external_protocol_adjudication_authority_unconfigured")
        public_key_path = (
            Path(public_key_path_text).expanduser().resolve()
            if public_key_path_text
            else None
        )
        public_key_hash = (
            _hash_file(public_key_path)
            if public_key_path is not None and public_key_path.is_file()
            else None
        )
        if (
            payload.get("schema_version") != 1
            or payload.get("algorithm") != "ed25519"
            or expected_key_hash is None
            or payload.get("authority_key_sha256") != expected_key_hash
            or public_key_hash != expected_key_hash
        ):
            reasons.append("external_protocol_adjudication_authority_invalid")
        if (
            payload.get("source") != "independent_protocol_authority"
            or payload.get("status") != "authorized"
            or (
                expected_run_id is not None
                and payload.get("run_id") != expected_run_id
            )
            or payload.get("target_hash") != expected_target_hash
            or payload.get("claim_scope") != EXACT_CLAIM_SCOPE
            or payload.get("known_target_reproduction_authorized") is not True
            or payload.get("protocol_status") != RESEARCH_ALPHA_PROTOCOL_STATUS
        ):
            reasons.append("external_protocol_adjudication_binding_mismatch")
        if not str(payload.get("adjudicator") or "").strip():
            reasons.append("external_protocol_adjudicator_missing")
        if set(payload.get("prohibited_conclusions") or []) != {
            "LambdaCDM_rejected",
            "dynamic_dark_energy_discovered",
        }:
            reasons.append("external_protocol_adjudication_scope_invalid")
        rationale = payload.get("rationale_artifact")
        if not isinstance(rationale, Mapping):
            reasons.append("external_protocol_adjudication_rationale_missing")
        else:
            rationale_path = Path(
                str(rationale.get("path") or "")
            ).expanduser().resolve()
            if not rationale_path.is_file():
                reasons.append("external_protocol_adjudication_rationale_file_missing")
            else:
                if rationale.get("sha256") != _hash_file(rationale_path):
                    reasons.append("external_protocol_adjudication_rationale_hash_mismatch")
                if rationale.get("size_bytes") != rationale_path.stat().st_size:
                    reasons.append("external_protocol_adjudication_rationale_size_mismatch")
        signature = payload.get("signature")
        if not isinstance(signature, str):
            reasons.append("external_protocol_adjudication_signature_invalid")
        elif public_key_path is not None:
            try:
                public_key = serialization.load_pem_public_key(
                    public_key_path.read_bytes()
                )
                if not isinstance(public_key, Ed25519PublicKey):
                    raise TypeError("configured key is not Ed25519")
                signature_bytes = base64.b64decode(
                    signature, validate=True
                )
                unsigned = dict(payload)
                unsigned.pop("signature", None)
                public_key.verify(signature_bytes, _canonical_json(unsigned))
            except (
                OSError,
                TypeError,
                ValueError,
                InvalidSignature,
            ) as exc:
                reasons.append(
                    "external_protocol_adjudication_ed25519_unverified:"
                    + type(exc).__name__
                )

    return {
        "passed": not reasons,
        "reasons": reasons,
        "path": str(adjudication_path) if adjudication_path else None,
        "sha256": (
            _hash_file(adjudication_path)
            if adjudication_path is not None and adjudication_path.is_file()
            else None
        ),
        "authority_id": payload.get("authority_id"),
        "status": payload.get("status"),
        "signature_algorithm": payload.get("algorithm"),
    }


def _load_yaml(path: str | Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return raw


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _prefix_file(prefix: str | Path, suffix: str) -> Path:
    return Path(f"{prefix}{suffix}")


def cobaya_mpi_seed_identities(
    seed_entropy: Sequence[int],
    *,
    mpi_size: int = REQUIRED_CHAIN_COUNT,
) -> list[dict[str, Any]]:
    """Reproduce Cobaya 3.6.2's SeedSequence(list).spawn(mpi_size) logic."""

    if (
        not isinstance(seed_entropy, (list, tuple))
        or not seed_entropy
        or not all(isinstance(value, int) for value in seed_entropy)
    ):
        raise ValueError("Cobaya seed entropy must be a non-empty integer sequence")
    children = np.random.SeedSequence(list(seed_entropy)).spawn(mpi_size)
    return [
        {
            "rank": rank,
            "entropy": list(seed_entropy),
            "spawn_key": list(child.spawn_key),
            # Compact integer for downstream schemas that require a per-chain
            # seed. The complete replay identity remains entropy+spawn_key.
            "derived_seed": int(child.generate_state(1, dtype=np.uint32)[0]),
        }
        for rank, child in enumerate(children)
    ]


def validate_canonical_config(config: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    likelihoods = config.get("likelihood")
    if not isinstance(likelihoods, Mapping):
        reasons.append("likelihood_mapping_missing")
    else:
        missing = sorted(set(REQUIRED_LIKELIHOODS) - set(likelihoods))
        extra = sorted(set(likelihoods) - set(REQUIRED_LIKELIHOODS))
        if missing:
            reasons.append("required_likelihoods_missing:" + ",".join(missing))
        if extra:
            reasons.append("unexpected_likelihoods_present:" + ",".join(extra))
        act = likelihoods.get("act_dr6_lenslike.ACTDR6LensLike")
        if not isinstance(act, Mapping) or act.get("variant") != "actplanck_baseline":
            reasons.append("act_lensing_variant_not_actplanck_baseline")
        if isinstance(act, Mapping) and act.get("lens_only") is True:
            reasons.append("act_lensing_cmb_corrections_disabled")

    mcmc = (config.get("sampler") or {}).get("mcmc")
    if not isinstance(mcmc, Mapping):
        reasons.append("mcmc_sampler_missing")
    else:
        if float(mcmc.get("Rminus1_stop", math.inf)) > 0.01:
            reasons.append("Rminus1_stop_not_strict")
        if float(mcmc.get("Rminus1_cl_stop", math.inf)) > 0.10:
            reasons.append("Rminus1_cl_stop_not_strict")
        if mcmc.get("drag") is not True:
            reasons.append("mcmc_drag_not_enabled")
        if float(mcmc.get("oversample_power", math.nan)) != 0.4:
            reasons.append("mcmc_oversample_power_not_paper_value")
        max_samples = mcmc.get("max_samples")
        if max_samples is not None and not (
            isinstance(max_samples, float) and math.isinf(max_samples)
        ):
            reasons.append("formal_mcmc_must_not_have_finite_max_samples")
        seeds = mcmc.get("seed")
        if (
            not isinstance(seeds, list)
            or len(seeds) != REQUIRED_CHAIN_COUNT
            or len(set(seeds)) != REQUIRED_CHAIN_COUNT
            or not all(isinstance(seed, int) for seed in seeds)
        ):
            reasons.append("four_distinct_registered_seeds_required")

    camb_args = ((config.get("theory") or {}).get("camb") or {}).get("extra_args") or {}
    camb_info = ((config.get("theory") or {}).get("camb") or {})
    if not isinstance(camb_info, Mapping) or camb_info.get("path") != "global":
        reasons.append("camb_global_locked_wheel_path_required")
    required_camb_args = {
        "lmax": 4000,
        "lens_margin": 1250,
        "lens_potential_accuracy": 4,
        "AccuracyBoost": 1,
        "lSampleBoost": 1,
        "lAccuracyBoost": 1,
        "halofit_version": "mead2016",
        "num_massive_neutrinos": 1,
        "dark_energy_model": "ppf",
        "nnu": 3.044,
        "standard_neutrino_neff": 3.044,
        "bbn_predictor": "PArthENoPE_880.2_standard.dat",
        "YHe": None,
        "theta_H0_range": [40, 100],
    }
    for name, expected in required_camb_args.items():
        if camb_args.get(name) != expected:
            reasons.append(f"camb_{name}_not_exact")

    params = config.get("params") or {}
    exact_priors = {
        "ombh2": (0.005, 0.1),
        "omch2": (0.001, 0.99),
        "theta_MC_100": (0.5, 10.0),
        "tau": (0.01, 0.8),
        "ns": (0.8, 1.2),
        "logA": (1.61, 3.91),
        "w": (-3.0, 1.0),
        "wa": (-3.0, 2.0),
    }
    for name, (expected_min, expected_max) in exact_priors.items():
        spec = params.get(name)
        prior = spec.get("prior") if isinstance(spec, Mapping) else None
        if not isinstance(prior, Mapping):
            reasons.append(f"free_{name}_prior_missing")
            continue
        if prior.get("min") != expected_min or prior.get("max") != expected_max:
            reasons.append(f"free_{name}_prior_not_table2")
    if params.get("mnu") != 0.06:
        reasons.append("single_massive_neutrino_mass_not_0.06ev")
    h0 = params.get("H0")
    if (
        not isinstance(h0, Mapping)
        or h0.get("prior") is not None
        or h0.get("min") != 40
        or h0.get("max") != 100
    ):
        reasons.append("H0_must_be_derived_not_sampled")
    theta = params.get("theta_MC_100")
    if not isinstance(theta, Mapping) or theta.get("drop") is not True:
        reasons.append("theta_MC_100_parameterization_missing")
    cosmomc_theta = params.get("cosmomc_theta")
    if not isinstance(cosmomc_theta, Mapping) or cosmomc_theta.get("value") != (
        "lambda theta_MC_100: 1.e-2*theta_MC_100"
    ) or cosmomc_theta.get("derived") is not False:
        reasons.append("cosmomc_theta_transform_not_exact")
    as_transform = params.get("As")
    if not isinstance(as_transform, Mapping) or as_transform.get("value") != (
        "lambda logA: 1e-10*np.exp(logA)"
    ):
        reasons.append("As_transform_not_exact")
    early_matter = (config.get("prior") or {}).get("early_matter_domination")
    if early_matter != "lambda w, wa: 0.0 if w + wa < 0.0 else -np.inf":
        reasons.append("w0_plus_wa_high_redshift_prior_missing")
    return reasons


def build_map_configs(
    canonical_config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive free-w0wa and fixed-LambdaCDM minimizers from one source."""

    reasons = validate_canonical_config(canonical_config)
    if reasons:
        raise ValueError("Canonical config is not strict: " + "; ".join(reasons))

    common = copy.deepcopy(dict(canonical_config))
    common.pop("output", None)
    common.pop("packages_path", None)
    common["sampler"] = copy.deepcopy(MINIMIZER_CONFIG)

    free = copy.deepcopy(common)
    fixed = copy.deepcopy(common)
    fixed_params = fixed.setdefault("params", {})
    fixed_params["w"] = -1.0
    fixed_params["wa"] = 0.0
    return free, fixed


def write_map_configs(
    canonical_path: str | Path,
    free_path: str | Path,
    fixed_path: str | Path,
) -> dict[str, Any]:
    canonical = _load_yaml(canonical_path)
    free, fixed = build_map_configs(canonical)
    for path, payload in ((Path(free_path), free), (Path(fixed_path), fixed)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    return {
        "canonical": str(canonical_path),
        "free_map": str(free_path),
        "fixed_map": str(fixed_path),
        "free_sha256": _hash_file(free_path),
        "fixed_sha256": _hash_file(fixed_path),
    }


def write_model_adequacy_plan(
    canonical_config: Mapping[str, Any],
    output_dir: str | Path,
    packages_path: str | Path,
) -> dict[str, Any]:
    """Freeze executable configs/specs for the six required adequacy checks.

    This creates inputs only. It never marks a check as passed; collectors must
    bind converged outputs from fresh runs before grade can sign A-readiness.
    """

    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    packages_root = Path(packages_path).resolve()
    configs: dict[str, dict[str, Any]] = {}

    def write_config(name: str, payload: Mapping[str, Any]) -> Path:
        path = root / f"{name}.yaml"
        path.write_text(
            yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        configs[name] = {
            "path": str(path),
            "sha256": _hash_file(path),
            "size_bytes": path.stat().st_size,
        }
        return path

    baseline = copy.deepcopy(dict(canonical_config))
    write_config("planck_pr3_plik", baseline)

    smoke = copy.deepcopy(baseline)
    smoke["sampler"]["mcmc"]["max_samples"] = 16
    smoke["sampler"]["mcmc"]["learn_every"] = 4
    write_config("non_citable_smoke", smoke)
    configs["non_citable_smoke"].update(
        {"non_citable": True, "evidence_class": "non_citable_smoke"}
    )

    widened = copy.deepcopy(baseline)
    widened["params"]["w"]["prior"] = {"min": -5.0, "max": 2.0}
    widened["params"]["wa"]["prior"] = {"min": -5.0, "max": 3.0}
    write_config("prior_w0wa_widened", widened)

    camspec = copy.deepcopy(baseline)
    camspec_likelihoods = camspec["likelihood"]
    camspec_likelihoods.pop("planck_2018_highl_plik.TTTEEE")
    camspec_likelihoods["planck_NPIPE_highl_CamSpec.TTTEEE"] = None
    write_config("planck_pr4_camspec", camspec)

    lensing = copy.deepcopy(baseline)
    lensing["likelihood"]["act_dr6_lenslike.ACTDR6LensLike"] = {
        "variant": "act_baseline",
        "lens_only": False,
    }
    write_config("lensing_combination", lensing)

    pantheon_source = (
        packages_root / "data" / "sn_data" / "PantheonPlus" / "Pantheon+SH0ES.dat"
    )
    if not pantheon_source.is_file():
        raise ValueError(f"Pantheon+ source table is missing: {pantheon_source}")
    source_lines = pantheon_source.read_text(encoding="utf-8").splitlines()
    if len(source_lines) < 2:
        raise ValueError("Pantheon+ source table is empty")
    source_columns = source_lines[0].lstrip("#").split()
    try:
        redshift_index = source_columns.index("zHD")
    except ValueError as exc:
        raise ValueError("Pantheon+ redshift source column is missing") from exc
    retained_after_redshift_cut = 0
    for line_number, line in enumerate(source_lines[1:], start=2):
        fields = line.split()
        if len(fields) != len(source_columns):
            raise ValueError(f"Pantheon+ malformed source row: {line_number}")
        redshift = float(fields[redshift_index])
        if not math.isfinite(redshift):
            raise ValueError(f"Pantheon+ invalid redshift row: {line_number}")
        if redshift > 0.01:
            retained_after_redshift_cut += 1

    statonly_source = PANTHEON_STATONLY_SOURCE_PATH.resolve()
    if (
        not statonly_source.is_file()
        or statonly_source.stat().st_size != PANTHEON_STATONLY_SIZE_BYTES
        or _hash_file(statonly_source) != PANTHEON_STATONLY_SHA256
    ):
        raise ValueError(
            "official Pantheon+ STATONLY covariance is missing or byte-drifted"
        )
    pantheon_covariance_path = root / "pantheonplus_stat_only.cov"
    shutil.copyfile(statonly_source, pantheon_covariance_path)
    pantheon_dataset = root / "pantheonplus_stat_only.dataset"
    pantheon_dataset.write_text(
        "\n".join(
            (
                "# Pre-registered Pantheon+ official statistical-only covariance variant",
                "name = PANTHEONPLUS_STAT_ONLY",
                f"data_file = {pantheon_source}",
                f"mag_covmat_file = {pantheon_covariance_path}",
                "",
            )
        ),
        encoding="utf-8",
    )
    pantheon_covariance = copy.deepcopy(baseline)
    pantheon_covariance["likelihood"]["sn.pantheonplus"] = {
        "dataset_file": str(pantheon_dataset)
    }
    write_config("pantheonplus_covariance", pantheon_covariance)

    independent = copy.deepcopy(baseline)
    independent_entropy = [71001931, 82350647, 94110763, 105320087]
    independent["sampler"]["mcmc"]["seed"] = independent_entropy
    write_config("independent_reproduction", independent)

    ppc_spec = {
        "schema_version": 1,
        "kind": "w0wa_predictive_check_plan",
        "profile_id": EXACT_PROFILE_ID,
        "producer_status": "WITHHELD",
        "producer_blocker": (
            "official exact Commander+SimAll+plik+ACT+BAO+PantheonPlus "
            "full-likelihood simulator is unavailable locally; an externally "
            "validated source-seeded simulator is required"
        ),
        "replicate_generation": {
            "prior": (
                "draw complete cosmological+nuisance vector from frozen Table 2 "
                "priors subject to w+wa<0, then simulate all four registered "
                "likelihood data blocks"
            ),
            "posterior": (
                "systematic equal-weight stratified draws from all four primary "
                "post-burn chains, then simulate all four registered likelihood "
                "data blocks"
            ),
            "minimum_replicates": 400,
            "seed_algorithm": "numpy.SeedSequence(entropy).spawn(discrepancies)",
        },
        "acceptance_rule": {
            "tail_probability": (
                "(1 + count(T_rep >= T_observed)) / (replicates + 1)"
            ),
            "lower_inclusive": 0.01,
            "upper_inclusive": 0.99,
            "all_discrepancies_must_pass": True,
            "missing_or_nonfinite": "fail",
        },
        "checks": {
            "prior_predictive_check": {
                # Replicate count, not an ESS threshold.
                "minimum_replicates": 400,
                "required_discrepancies": [
                    "desi_bao_residual_quadratic",
                    "pantheonplus_whitened_residual_quadratic",
                    "cmb_likelihood_component_chi2",
                    "lensing_bandpower_residual_quadratic",
                ],
                "seed_entropy": [133701, 133703, 133709, 133711],
            },
            "posterior_predictive_check": {
                # Replicate count, not an ESS threshold.
                "minimum_replicates": 400,
                "required_discrepancies": [
                    "desi_bao_residual_quadratic",
                    "pantheonplus_whitened_residual_quadratic",
                    "cmb_likelihood_component_chi2",
                    "lensing_bandpower_residual_quadratic",
                ],
                "seed_entropy": [233701, 233703, 233709, 233711],
            },
        },
        "required_output": {
            "run_id": "primary run_id",
            "replicates": "integer >= minimum_replicates",
            "discrepancy_artifacts": "distinct path+sha256 records",
            "status": "passed only after every registered discrepancy is evaluated",
        },
    }
    ppc_path = root / "predictive_checks.json"
    _write_json(ppc_path, ppc_spec)

    fiducial_base = {
        "ombh2": 0.02237,
        "omch2": 0.1200,
        "theta_MC_100": 1.04109,
        "tau": 0.055,
        "ns": 0.965,
        "logA": 3.05,
    }
    injections = {
        "schema_version": 1,
        "kind": "w0wa_injection_recovery_plan",
        "profile_id": EXACT_PROFILE_ID,
        "producer_status": "WITHHELD",
        "producer_blocker": (
            "source-seeded exact full-likelihood simulated-data producer is "
            "not yet externally validated"
        ),
        "joint_parameters": ["w", "wa"],
        "joint_region": {
            "coverage": 0.95,
            "statistic": (
                "d2=(recovered_center-truth)^T "
                "recovered_covariance^-1 (recovered_center-truth)"
            ),
            "distribution": "chi_square_df_2",
            "threshold_inclusive": 5.991464547107979,
            "center": "weighted posterior mean over all post-burn rows",
            "covariance": "weighted posterior covariance over all post-burn rows",
        },
        "standardized_bias": {
            "per_parameter": (
                "z_j=(recovered_center_j-truth_j)/sqrt(recovered_covariance_jj)"
            ),
            "per_fiducial": "mean(abs(z_w), abs(z_wa))",
            "aggregate": (
                "arithmetic mean of abs(z_j) across all 3 fiducials and both "
                "registered parameters"
            ),
            "maximum_aggregate_exclusive": 0.30,
        },
        "simulation": {
            "generator": (
                "full registered likelihood simulator at the fixed truth, "
                "including frozen nuisance prescriptions"
            ),
            "seed_algorithm": "numpy.random.Generator(PCG64(simulation_seed))",
            "simulated_data_must_be_distinct": True,
        },
        "fiducials": [
            {
                "name": "lambda_boundary",
                "simulation_seed": 310_001,
                "truth": {**fiducial_base, "w": -1.0, "wa": 0.0},
            },
            {
                "name": "evolving_quintessence",
                "simulation_seed": 310_003,
                "truth": {**fiducial_base, "w": -0.85, "wa": -0.60},
            },
            {
                "name": "crossing_model",
                "simulation_seed": 310_019,
                "truth": {**fiducial_base, "w": -1.10, "wa": 0.40},
            },
        ],
        "required_output": {
            "simulated_data_artifact": "one distinct source-seeded artifact per fiducial",
            "converged_chain_artifacts": "four fresh chains per fiducial",
            "recovered_center": "ordered [w, wa] finite vector",
            "recovered_covariance": "ordered 2x2 positive-definite matrix",
            "joint_mahalanobis_d2": "recomputed and <= frozen threshold",
            "per_parameter_standardized_bias": "recomputed ordered [z_w, z_wa]",
        },
    }
    injection_path = root / "injection_recovery.json"
    _write_json(injection_path, injections)

    plan = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "w0wa_model_adequacy_plan",
        "profile_id": EXACT_PROFILE_ID,
        "target_commitment": PREREGISTERED_TARGET_COMMITMENT,
        "protocol_integrity": dict(PROTOCOL_INTEGRITY),
        "paper_fidelity_amendment": dict(PAPER_FIDELITY_AMENDMENT),
        "protocol_amendment_artifact": protocol_amendment_record(),
        "configs": configs,
        "predictive_checks": {
            "path": str(ppc_path),
            "sha256": _hash_file(ppc_path),
            "size_bytes": ppc_path.stat().st_size,
        },
        "injection_recovery": {
            "path": str(injection_path),
            "sha256": _hash_file(injection_path),
            "size_bytes": injection_path.stat().st_size,
            "truth_commitment": _hash_object(injections["fiducials"]),
        },
        "pantheon_covariance_dataset_output": str(pantheon_dataset),
        "pantheon_covariance_variant": {
            "variant": "official_statistical_only",
            "rationale": (
                "use the collaboration-provided statistical covariance, including "
                "repeat-observation off-diagonal intrinsic-scatter terms, while "
                "removing the systematic covariance contribution"
            ),
            "construction": "official Pantheon+SH0ES_STATONLY.cov copied unmodified",
            "redshift_selection": "PantheonPlus likelihood zHD > 0.01 unchanged",
            "source_data": {
                "path": str(pantheon_source),
                "sha256": _hash_file(pantheon_source),
                "size_bytes": pantheon_source.stat().st_size,
                "rows_before_selection": len(source_lines) - 1,
                "rows_after_selection": retained_after_redshift_cut,
            },
            "source_covariance": {
                "path": str(statonly_source),
                "sha256": PANTHEON_STATONLY_SHA256,
                "size_bytes": PANTHEON_STATONLY_SIZE_BYTES,
                "repository": "https://github.com/PantheonPlusSH0ES/DataRelease",
                "commit": PANTHEON_DATA_RELEASE_COMMIT,
                "git_blob_sha1": PANTHEON_STATONLY_GIT_BLOB,
                "repository_path": (
                    "Pantheon+_Data/4_DISTANCES_AND_COVAR/"
                    "Pantheon+SH0ES_STATONLY.cov"
                ),
            },
            "generated_covariance": {
                "path": str(pantheon_covariance_path),
                "sha256": _hash_file(pantheon_covariance_path),
                "size_bytes": pantheon_covariance_path.stat().st_size,
            },
            "generated_dataset": {
                "path": str(pantheon_dataset),
                "sha256": _hash_file(pantheon_dataset),
                "size_bytes": pantheon_dataset.stat().st_size,
            },
        },
        "independent_seed_binding": cobaya_mpi_seed_identities(
            independent_entropy
        ),
        "status": "INPUTS_FROZEN_OUTPUTS_PENDING",
    }
    plan = _with_self_hash(plan, "plan_sha256")
    plan_path = root / "adequacy-plan.json"
    _write_json(plan_path, plan)
    return {
        "path": str(plan_path),
        "sha256": _hash_file(plan_path),
        "plan_sha256": plan["plan_sha256"],
        "configs": configs,
        "predictive_checks": plan["predictive_checks"],
        "injection_recovery": plan["injection_recovery"],
    }


def _config_without_runtime_fields(config: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(config))
    for key in ("output", "packages_path", "debug", "resume", "force"):
        result.pop(key, None)
    return result


def _config_fingerprint(config: Mapping[str, Any]) -> str:
    return _hash_object(_config_without_runtime_fields(config))


def _map_pair_fingerprints(config: Mapping[str, Any]) -> dict[str, str]:
    params = config.get("params") or {}
    shared_params = {
        str(name): spec
        for name, spec in params.items()
        if str(name) not in MODEL_PARAMETERS
    }
    return {
        "likelihood": _hash_object(config.get("likelihood") or {}),
        "theory": _hash_object(config.get("theory") or {}),
        "shared_parameters": _hash_object(shared_params),
    }


def validate_map_config_pair(
    free_config: Mapping[str, Any], fixed_config: Mapping[str, Any]
) -> dict[str, Any]:
    reasons: list[str] = []
    free_fp = _map_pair_fingerprints(free_config)
    fixed_fp = _map_pair_fingerprints(fixed_config)
    for key in ("likelihood", "theory", "shared_parameters"):
        if free_fp[key] != fixed_fp[key]:
            reasons.append(f"map_{key}_fingerprint_mismatch")
    if free_config.get("sampler") != fixed_config.get("sampler"):
        reasons.append("map_sampler_mismatch")

    free_params = free_config.get("params") or {}
    fixed_params = fixed_config.get("params") or {}
    for name in ("w", "wa"):
        free_spec = free_params.get(name)
        if not isinstance(free_spec, Mapping) or not isinstance(
            free_spec.get("prior"), Mapping
        ):
            reasons.append(f"free_{name}_not_sampled")
    if fixed_params.get("w") != -1.0:
        reasons.append("fixed_w_not_minus_one")
    if fixed_params.get("wa") != 0.0:
        reasons.append("fixed_wa_not_zero")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "free": free_fp,
        "fixed": fixed_fp,
    }


def build_data_inventory(
    packages_path: str | Path,
    *,
    asset_spec: Mapping[str, Sequence[str]] = CANONICAL_DATA_ASSETS,
) -> dict[str, Any]:
    root = Path(packages_path).resolve()
    groups: dict[str, Any] = {}
    missing: list[str] = []
    for likelihood, patterns in asset_spec.items():
        files: list[dict[str, Any]] = []
        seen: set[Path] = set()
        for pattern in patterns:
            matches = sorted(path for path in root.glob(pattern) if path.is_file())
            if not matches:
                missing.append(f"{likelihood}:{pattern}")
                continue
            for path in matches:
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                files.append(
                    {
                        "path": str(path.relative_to(root)),
                        "size_bytes": path.stat().st_size,
                        "sha256": _hash_file(path),
                    }
                )
        version_files = [item for item in files if item["path"].endswith("version.dat")]
        versions = []
        for item in version_files:
            version_path = root / item["path"]
            versions.append(
                version_path.read_text(encoding="utf-8", errors="replace").strip()
            )
        groups[str(likelihood)] = {
            "versions": versions,
            "files": files,
            "fingerprint": _hash_object(files),
        }

    fingerprint_payload = {
        "groups": groups,
        "missing": sorted(missing),
    }
    return {
        "packages_path": str(root),
        "complete": not missing and set(groups) == set(asset_spec),
        "missing": sorted(missing),
        "groups": groups,
        "fingerprint": _hash_object(fingerprint_payload),
    }


def build_adequacy_data_inventory(packages_path: str | Path) -> dict[str, Any]:
    """Inventory the pre-registered PR4 CamSpec systematics data separately."""

    return build_data_inventory(packages_path, asset_spec=ADEQUACY_DATA_ASSETS)


def build_source_state_inventory() -> dict[str, Any]:
    """Bind formal execution to one clean, reviewable Git source tree.

    The commit is recorded rather than hard-coded into a file inside that same
    commit, which would create a circular trust root. External review can
    independently checkout ``head_commit`` and recompute every listed byte.
    """

    repository = BACKEND_ROOT.parent.resolve()
    reasons: list[str] = []

    def git_output(*args: str, binary: bool = False) -> bytes | str:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        if binary:
            return result.stdout
        return result.stdout.decode("utf-8", errors="strict").strip()

    def git_returncode(*args: str) -> int:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            capture_output=True,
            check=False,
            timeout=30,
        )
        return int(result.returncode)

    files: list[dict[str, Any]] = []
    status_entries: list[str] = []
    head_commit: str | None = None
    head_tree: str | None = None
    branch: str | None = None
    detached: bool | None = None
    base_is_ancestor = False
    tracked_paths: set[str] = set()
    try:
        top_level = Path(str(git_output("rev-parse", "--show-toplevel"))).resolve()
        if top_level != repository:
            reasons.append("source_git_repository_root_mismatch")
        head_commit = str(git_output("rev-parse", "HEAD"))
        head_tree = str(git_output("rev-parse", "HEAD^{tree}"))
        branch = str(git_output("rev-parse", "--abbrev-ref", "HEAD"))
        detached = branch == "HEAD"
        base_is_ancestor = (
            git_returncode(
                "merge-base",
                "--is-ancestor",
                TRUSTED_SOURCE_BASE_COMMIT,
                "HEAD",
            )
            == 0
        )
        if not base_is_ancestor:
            reasons.append("source_git_head_not_descendant_of_frozen_base")
        raw_tracked = git_output(
            "ls-files",
            "-z",
            "--",
            *SOURCE_STATE_PATHS,
            binary=True,
        )
        tracked_paths = {
            item.decode("utf-8", errors="strict")
            for item in bytes(raw_tracked).split(b"\0")
            if item
        }
        raw_status = git_output(
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            binary=True,
        )
        status_entries = [
            item.decode("utf-8", errors="replace")
            for item in bytes(raw_status).split(b"\0")
            if item
        ]
    except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError) as exc:
        reasons.append(f"source_git_inventory_failed:{type(exc).__name__}:{exc}")

    for logical_path in SOURCE_STATE_PATHS:
        path = repository / logical_path
        if not path.is_file():
            reasons.append(f"source_file_missing:{logical_path}")
            continue
        if logical_path not in tracked_paths:
            reasons.append(f"source_file_not_git_tracked:{logical_path}")
        files.append(
            {
                "path": logical_path,
                "size_bytes": path.stat().st_size,
                "sha256": _hash_file(path),
            }
        )
    if status_entries:
        reasons.append("source_tree_has_changes")
    fingerprint_payload = {
        "base_commit": TRUSTED_SOURCE_BASE_COMMIT,
        "base_is_ancestor": base_is_ancestor,
        "head_commit": head_commit,
        "head_tree": head_tree,
        "branch": branch,
        "detached": detached,
        "files": files,
        "status_entries": status_entries,
    }
    return {
        "schema_version": 2,
        "repository_root": str(repository),
        **fingerprint_payload,
        "clean": not status_entries,
        "passed": not reasons,
        "reasons": reasons,
        "fingerprint": _hash_object(fingerprint_payload),
    }


def verify_trusted_data_manifest(
    manifest_path: str | Path,
    *,
    inventory: Mapping[str, Any],
    adequacy_inventory: Mapping[str, Any] | None = None,
    archive_root: str | Path = TRUSTED_SOURCE_ARCHIVE_ROOT,
) -> dict[str, Any]:
    """Verify installed likelihood bytes against a pre-run commitment."""

    path = Path(manifest_path).resolve()
    reasons: list[str] = []
    payload: dict[str, Any] = {}
    if not path.is_file():
        reasons.append("trusted_data_manifest_missing")
    else:
        if _hash_file(path) != TRUSTED_DATA_MANIFEST_SHA256:
            reasons.append("trusted_data_manifest_hash_mismatch")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reasons.append(f"trusted_data_manifest_unreadable:{type(exc).__name__}")
        else:
            if isinstance(loaded, dict):
                payload = loaded
            else:
                reasons.append("trusted_data_manifest_not_mapping")

    if payload.get("schema_version") != 1 or payload.get("kind") != (
        "w0wa_exact_data_byte_commitment"
    ):
        reasons.append("trusted_data_manifest_schema_invalid")
    if payload.get("profile_id") != EXACT_PROFILE_ID:
        reasons.append("trusted_data_manifest_profile_mismatch")
    if payload.get("frozen_before_formal_run") is not True:
        reasons.append("trusted_data_manifest_not_prerun_frozen")
    if payload.get("overall_inventory_fingerprint") != inventory.get("fingerprint"):
        reasons.append("trusted_data_overall_fingerprint_mismatch")

    expected_groups = payload.get("groups")
    observed_groups = inventory.get("groups")
    if not isinstance(expected_groups, Mapping) or not isinstance(
        observed_groups, Mapping
    ):
        reasons.append("trusted_data_groups_missing")
    elif set(expected_groups) != set(REQUIRED_LIKELIHOODS):
        reasons.append("trusted_data_group_set_invalid")
    else:
        for name in REQUIRED_LIKELIHOODS:
            expected = expected_groups.get(name)
            observed = observed_groups.get(name)
            if not isinstance(expected, Mapping) or not isinstance(observed, Mapping):
                reasons.append(f"trusted_data_group_missing:{name}")
                continue
            files = observed.get("files") or []
            summary = {
                "file_count": len(files),
                "total_size_bytes": sum(
                    int(item.get("size_bytes") or 0)
                    for item in files
                    if isinstance(item, Mapping)
                ),
                "fingerprint": observed.get("fingerprint"),
            }
            if any(expected.get(key) != value for key, value in summary.items()):
                reasons.append(f"trusted_data_group_fingerprint_mismatch:{name}")
            committed_files = expected.get("files")
            if committed_files is not None and committed_files != files:
                reasons.append(f"trusted_data_file_records_mismatch:{name}")

    expected_adequacy_groups = payload.get("adequacy_groups")
    observed_adequacy_groups = (
        adequacy_inventory.get("groups")
        if isinstance(adequacy_inventory, Mapping)
        else None
    )
    if payload.get("adequacy_inventory_fingerprint") != (
        adequacy_inventory.get("fingerprint")
        if isinstance(adequacy_inventory, Mapping)
        else None
    ):
        reasons.append("trusted_adequacy_inventory_fingerprint_mismatch")
    if not isinstance(expected_adequacy_groups, Mapping) or not isinstance(
        observed_adequacy_groups, Mapping
    ):
        reasons.append("trusted_adequacy_data_groups_missing")
    elif set(expected_adequacy_groups) != set(ADEQUACY_DATA_ASSETS):
        reasons.append("trusted_adequacy_data_group_set_invalid")
    else:
        for name in ADEQUACY_DATA_ASSETS:
            expected = expected_adequacy_groups.get(name)
            observed = observed_adequacy_groups.get(name)
            if not isinstance(expected, Mapping) or not isinstance(
                observed, Mapping
            ):
                reasons.append(f"trusted_adequacy_data_group_missing:{name}")
                continue
            files = observed.get("files") or []
            summary = {
                "file_count": len(files),
                "total_size_bytes": sum(
                    int(item.get("size_bytes") or 0)
                    for item in files
                    if isinstance(item, Mapping)
                ),
                "fingerprint": observed.get("fingerprint"),
            }
            if any(expected.get(key) != value for key, value in summary.items()):
                reasons.append(
                    f"trusted_adequacy_data_group_fingerprint_mismatch:{name}"
                )
            if expected.get("files") != files:
                reasons.append(
                    f"trusted_adequacy_data_file_records_mismatch:{name}"
                )

    quality_records: dict[str, Any] = {}
    expected_quality = payload.get("data_quality_checks")
    packages_root = Path(str(inventory.get("packages_path") or "")).resolve()
    if not isinstance(expected_quality, Mapping):
        reasons.append("trusted_data_quality_checks_missing")
    else:
        bao_mean = (
            packages_root
            / "data/bao_data/desi_2024_gaussian_bao_ALL_GCcomb_mean.txt"
        )
        bao_covariance = (
            packages_root
            / "data/bao_data/desi_2024_gaussian_bao_ALL_GCcomb_cov.txt"
        )
        pantheon_data = (
            packages_root / "data/sn_data/PantheonPlus/Pantheon+SH0ES.dat"
        )
        pantheon_covariance = (
            packages_root
            / "data/sn_data/PantheonPlus/Pantheon+SH0ES_STAT+SYS.cov"
        )
        try:
            bao_mean_rows = sum(
                1
                for line in bao_mean.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
            bao_covariance_table = np.loadtxt(bao_covariance, ndmin=2)
            pantheon_lines = pantheon_data.read_text(encoding="utf-8").splitlines()
            pantheon_columns = pantheon_lines[0].lstrip("#").split()
            pantheon_rows = [line.split() for line in pantheon_lines[1:]]
            cid_index = pantheon_columns.index("CID")
            redshift_index = pantheon_columns.index("zHD")
            if any(len(row) != len(pantheon_columns) for row in pantheon_rows):
                raise ValueError("Pantheon+ rows have inconsistent columns")
            redshifts = [float(row[redshift_index]) for row in pantheon_rows]
            with pantheon_covariance.open("r", encoding="utf-8") as handle:
                covariance_dimension = int(handle.readline().strip())
                covariance_values = sum(1 for line in handle if line.strip())
            if covariance_values != covariance_dimension * covariance_dimension:
                raise ValueError("Pantheon+ covariance length is not square")
        except (OSError, ValueError, IndexError) as exc:
            reasons.append(f"trusted_data_quality_unreadable:{type(exc).__name__}:{exc}")
        else:
            quality_records = {
                "bao.desi_2024_bao_all": {
                    "mean_data_rows": bao_mean_rows,
                    "covariance_shape": list(bao_covariance_table.shape),
                },
                "sn.pantheonplus": {
                    "data_rows": len(pantheon_rows),
                    "data_columns": len(pantheon_columns),
                    "unique_CID": len({row[cid_index] for row in pantheon_rows}),
                    "redshift_column": "zHD",
                    "redshift_cut": ">0.01",
                    "rows_after_redshift_cut": sum(value > 0.01 for value in redshifts),
                    "covariance_shape": [
                        covariance_dimension,
                        covariance_dimension,
                    ],
                },
            }
            if quality_records != expected_quality:
                reasons.append("trusted_data_quality_shape_mismatch")

    vcs_records: list[dict[str, Any]] = []
    vcs_sources = payload.get("source_vcs")
    if not isinstance(vcs_sources, list) or len(vcs_sources) != 3:
        reasons.append("trusted_source_vcs_incomplete")
    else:
        inventory_files = {
            str(item.get("path")): item
            for group in (inventory.get("groups") or {}).values()
            if isinstance(group, Mapping)
            for item in group.get("files") or []
            if isinstance(item, Mapping)
        }
        for source_index, source in enumerate(vcs_sources):
            if not isinstance(source, Mapping):
                reasons.append(f"trusted_source_vcs_invalid:{source_index}")
                continue
            repository = str(source.get("repository") or "")
            commit = str(source.get("commit") or "")
            files = source.get("files")
            if (
                not repository.startswith("https://github.com/")
                or len(commit) != 40
                or any(character not in "0123456789abcdef" for character in commit)
                or not isinstance(files, list)
                or not files
            ):
                reasons.append(f"trusted_source_vcs_metadata_invalid:{source_index}")
                continue
            source_root = source.get("source_root")
            root = (
                (BACKEND_ROOT.parent / str(source_root)).resolve()
                if isinstance(source_root, str)
                else packages_root
            )
            normalized_files: list[dict[str, Any]] = []
            for file_index, item in enumerate(files):
                if not isinstance(item, Mapping):
                    reasons.append(
                        f"trusted_source_vcs_file_invalid:{source_index}:{file_index}"
                    )
                    continue
                relative = str(
                    item.get("logical_path") or item.get("repository_path") or ""
                )
                relative_path = Path(relative)
                if (
                    not relative
                    or relative_path.is_absolute()
                    or ".." in relative_path.parts
                ):
                    reasons.append(
                        f"trusted_source_vcs_path_invalid:{source_index}:{file_index}"
                    )
                    continue
                physical = root / relative_path
                if not physical.is_file():
                    reasons.append(f"trusted_source_vcs_file_missing:{relative}")
                    continue
                observed = {
                    "path": str(physical.resolve()),
                    "size_bytes": physical.stat().st_size,
                    "sha256": _hash_file(physical),
                    "git_blob_sha1": _git_blob_sha1(physical),
                }
                if any(
                    observed[field] != item.get(field)
                    for field in ("size_bytes", "sha256", "git_blob_sha1")
                ):
                    reasons.append(f"trusted_source_vcs_file_mismatch:{relative}")
                logical_path = item.get("logical_path")
                if isinstance(logical_path, str):
                    inventory_item = inventory_files.get(logical_path)
                    if not isinstance(inventory_item, Mapping) or any(
                        inventory_item.get(field) != item.get(field)
                        for field in ("size_bytes", "sha256")
                    ):
                        reasons.append(
                            f"trusted_source_vcs_inventory_mismatch:{logical_path}"
                        )
                normalized_files.append({**dict(item), **observed})
            vcs_records.append(
                {
                    "repository": repository,
                    "tag": source.get("tag"),
                    "commit": commit,
                    "files": normalized_files,
                }
            )

    archive_records: list[dict[str, Any]] = []
    archives = payload.get("source_archives")
    if not isinstance(archives, list) or len(archives) != 3:
        reasons.append("trusted_source_archives_incomplete")
    else:
        archive_names = {
            str(item.get("filename") or "")
            for item in archives
            if isinstance(item, Mapping)
        }
        if archive_names != set(EXPECTED_SOURCE_ARCHIVES):
            reasons.append("trusted_source_archive_set_mismatch")
        root = Path(archive_root).resolve()
        for item in archives:
            if not isinstance(item, Mapping):
                reasons.append("trusted_source_archive_record_invalid")
                continue
            filename = str(item.get("filename") or "")
            relative_archive = Path(filename)
            if (
                not filename
                or relative_archive.is_absolute()
                or ".." in relative_archive.parts
            ):
                reasons.append(f"trusted_source_archive_path_invalid:{filename}")
                continue
            expected_identity = EXPECTED_SOURCE_ARCHIVES.get(filename)
            if not isinstance(expected_identity, Mapping) or any(
                item.get(field) != expected
                for field, expected in expected_identity.items()
            ):
                reasons.append(
                    f"trusted_source_archive_identity_mismatch:{filename}"
                )
            archive = root / relative_archive
            if not archive.is_file():
                reasons.append(f"trusted_source_archive_missing:{filename}")
                continue
            observed_hash = _hash_file(archive)
            observed_size = archive.stat().st_size
            if observed_hash != item.get("sha256"):
                reasons.append(f"trusted_source_archive_hash_mismatch:{filename}")
            if observed_size != item.get("size_bytes"):
                reasons.append(f"trusted_source_archive_size_mismatch:{filename}")
            archive_records.append(
                {
                    "filename": filename,
                    "path": str(archive),
                    "url": item.get("url"),
                    "size_bytes": observed_size,
                    "sha256": observed_hash,
                }
            )
    return {
        "passed": not reasons,
        "reasons": reasons,
        "path": str(path),
        "sha256": _hash_file(path) if path.is_file() else None,
        "archive_root": str(Path(archive_root).resolve()),
        "source_archives": archive_records,
        "source_vcs": vcs_records,
        "data_quality_checks": quality_records,
        "overall_inventory_fingerprint": payload.get(
            "overall_inventory_fingerprint"
        ),
        "adequacy_inventory_fingerprint": payload.get(
            "adequacy_inventory_fingerprint"
        ),
        "group_fingerprints": {
            name: record.get("fingerprint")
            for name, record in (expected_groups or {}).items()
            if isinstance(record, Mapping)
        },
        "adequacy_group_fingerprints": {
            name: record.get("fingerprint")
            for name, record in (expected_adequacy_groups or {}).items()
            if isinstance(record, Mapping)
        },
    }


def _native_runtime_manifest() -> dict[str, Any]:
    records: dict[str, Any] = {}
    reasons: list[str] = []
    try:
        import scipy
        from mpi4py import MPI

        mpirun = shutil.which("mpirun")
        mpi_extension = Path(str(MPI.__file__)).resolve()
        python_executable = Path(sys.executable).resolve()
        binaries = {
            "python": python_executable,
            "mpirun": Path(mpirun).resolve() if mpirun else None,
            "mpi4py_extension": mpi_extension,
        }
        binary_records: dict[str, Any] = {}
        for name, path in binaries.items():
            if path is None or not path.is_file():
                reasons.append(f"native_runtime_binary_missing:{name}")
                continue
            binary_records[name] = {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": _hash_file(path),
            }
            if _hash_file(path) != TRUSTED_NATIVE_RUNTIME_SHA256[name]:
                reasons.append(f"native_runtime_binary_hash_mismatch:{name}")
        mpirun_version = subprocess.run(
            [str(binaries["mpirun"]), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if mpirun_version.returncode != 0:
            reasons.append("native_runtime_mpirun_version_failed")
        vendor_name, vendor_version = MPI.get_vendor()
        if vendor_name != "Open MPI" or tuple(vendor_version) != (5, 0, 9):
            reasons.append("native_runtime_mpi_vendor_version_mismatch")
        numpy_config = json.loads(json.dumps(np.__config__.CONFIG, default=str))
        scipy_config = json.loads(
            json.dumps(scipy.show_config(mode="dicts"), default=str)
        )
        numpy_blas = (
            (numpy_config.get("Build Dependencies") or {}).get("blas") or {}
        ).get("name")
        scipy_blas = (
            (scipy_config.get("Build Dependencies") or {}).get("blas") or {}
        ).get("name")
        if str(numpy_blas).lower() != "accelerate" or str(scipy_blas).lower() != (
            "accelerate"
        ):
            reasons.append("native_runtime_blas_backend_mismatch")
        otool = subprocess.run(
            ["otool", "-L", str(mpi_extension)],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if otool.returncode != 0:
            reasons.append("native_runtime_mpi_linkage_unavailable")
        linked_library_lines = otool.stdout.strip().splitlines()
        if linked_library_lines and linked_library_lines[0].endswith(":"):
            linked_library_lines[0] = mpi_extension.name + ":"
        records = {
            "binaries": binary_records,
            "mpirun_version": mpirun_version.stdout.strip(),
            "mpi_vendor": {
                "name": vendor_name,
                "version": list(vendor_version),
                "library_version": MPI.Get_library_version().rstrip("\x00\n"),
                "linked_libraries": "\n".join(linked_library_lines),
            },
            "numpy_build": numpy_config,
            "scipy_build": scipy_config,
        }
    except (ImportError, OSError, subprocess.SubprocessError, ValueError) as exc:
        reasons.append(f"native_runtime_inventory_failed:{type(exc).__name__}:{exc}")
    # Absolute venv paths are audit metadata, not scientific identity. Exclude
    # them from the trust fingerprint so a separately created isolated venv
    # with byte-identical Python/mpi4py and the same native linkage can pass its
    # own preflight while retaining a distinct execution-environment fingerprint.
    fingerprint_records = copy.deepcopy(records)
    for binary in (fingerprint_records.get("binaries") or {}).values():
        if isinstance(binary, dict):
            binary.pop("path", None)
    fingerprint = _hash_object(fingerprint_records)
    if fingerprint != TRUSTED_NATIVE_RUNTIME_FINGERPRINT:
        reasons.append("native_runtime_fingerprint_mismatch")
    return {
        **records,
        "passed": not reasons,
        "reasons": reasons,
        "fingerprint": fingerprint,
        "fingerprint_scope": "byte_and_build_identity_excluding_absolute_paths",
    }


def _installed_distribution_module_identity(
    module_name: str,
    distribution_name: str,
) -> dict[str, Any]:
    """Return a load-order-independent identity for an installed module.

    Planck deliberately loads its committed clipy tree from ``packages/code``.
    Looking up an import spec after that point reports the live likelihood-code
    origin instead of the installed wheel origin.  Resolve the latter directly
    from frozen distribution metadata; live likelihood origins are validated
    separately by :func:`assert_loaded_likelihood_runtime`.
    """

    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return {
            "distribution": distribution_name,
            "version": None,
            "installed": False,
            "origin_scope": "installed_distribution",
            "origin": None,
            "relative_path": None,
            "size_bytes": None,
            "sha256": None,
        }
    listed_files = {
        Path(distribution.locate_file(relative)).resolve(): str(relative)
        for relative in distribution.files or []
    }
    module_path = Path(*module_name.split("."))
    candidates = (
        Path(distribution.locate_file(module_path / "__init__.py")).resolve(),
        Path(distribution.locate_file(module_path)).with_suffix(".py").resolve(),
    )
    origins = [
        path for path in dict.fromkeys(candidates) if path.is_file() and path in listed_files
    ]
    if len(origins) != 1:
        return {
            "distribution": distribution_name,
            "version": distribution.version,
            "installed": False,
            "origin_scope": "installed_distribution",
            "origin": None,
            "relative_path": None,
            "size_bytes": None,
            "sha256": None,
        }
    origin = origins[0]
    return {
        "distribution": distribution_name,
        "version": distribution.version,
        "installed": True,
        "origin_scope": "installed_distribution",
        "origin": str(origin),
        "relative_path": listed_files[origin],
        "size_bytes": origin.stat().st_size,
        "sha256": _hash_file(origin),
    }


def environment_manifest() -> dict[str, Any]:
    versions: dict[str, str | None] = {}
    for distribution in (
        *REQUIRED_PACKAGE_VERSIONS,
        "getdist",
        "arviz",
        "numpy",
        "scipy",
        "pyyaml",
    ):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    tracked_env = {
        key: os.environ.get(key)
        for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
    }
    runtime_modules = {
        module_name: _installed_distribution_module_identity(
            module_name, distribution_name
        )
        for module_name, distribution_name in RUNTIME_MODULE_DISTRIBUTIONS
    }
    return {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": versions,
        "runtime_modules": runtime_modules,
        "thread_environment": tracked_env,
        "native_runtime": _native_runtime_manifest(),
        "import_policy": _exact_python_import_policy(),
        "runtime_closure": _exact_runtime_closure_identity(),
    }


def _runtime_module_identity_reasons(
    value: Any,
    *,
    required_versions: Mapping[str, str],
) -> list[str]:
    reasons: list[str] = []
    expected_modules = dict(RUNTIME_MODULE_DISTRIBUTIONS)
    if not isinstance(value, Mapping) or set(value) != set(expected_modules):
        return ["exact_runtime_module_set_invalid"]
    expected_fields = {
        "distribution",
        "version",
        "installed",
        "origin_scope",
        "origin",
        "relative_path",
        "size_bytes",
        "sha256",
    }
    for module_name, distribution_name in expected_modules.items():
        record = value.get(module_name)
        expected_version = required_versions.get(
            canonicalize_name(distribution_name)
        )
        if not isinstance(record, Mapping) or set(record) != expected_fields:
            reasons.append(f"exact_runtime_module_record_invalid:{module_name}")
            continue
        origin = Path(str(record.get("origin") or "")).resolve()
        relative = Path(str(record.get("relative_path") or ""))
        if (
            record.get("distribution") != distribution_name
            or record.get("version") != expected_version
            or record.get("installed") is not True
            or record.get("origin_scope") != "installed_distribution"
            or not origin.is_file()
            or relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or record.get("size_bytes") != origin.stat().st_size
            or record.get("sha256") != _hash_file(origin)
        ):
            reasons.append(f"exact_runtime_module_identity_invalid:{module_name}")
            continue
        try:
            distribution = importlib.metadata.distribution(distribution_name)
            installed_origin = Path(
                distribution.locate_file(relative)
            ).resolve()
        except importlib.metadata.PackageNotFoundError:
            reasons.append(f"exact_runtime_module_distribution_missing:{module_name}")
            continue
        if installed_origin != origin:
            reasons.append(f"exact_runtime_module_origin_invalid:{module_name}")
    return reasons


def assert_locked_camb_runtime() -> dict[str, str]:
    """Prove that CAMB resolves to the locked global 1.6.6 distribution."""

    import camb

    version = str(getattr(camb, "__version__", ""))
    origin = Path(str(getattr(camb, "__file__", ""))).resolve()
    distribution = importlib.metadata.distribution("camb")
    distribution_root = Path(distribution.locate_file("")).resolve()
    if version != REQUIRED_PACKAGE_VERSIONS["camb"]:
        raise RuntimeError(
            f"loaded CAMB version {version!r} is not locked 1.6.6"
        )
    if not origin.is_relative_to(distribution_root):
        raise RuntimeError(
            f"loaded CAMB is outside the locked global distribution: {origin}"
        )
    return {"version": version, "origin": str(origin)}


def likelihood_runtime_inventory(packages_path: str | Path) -> dict[str, Any]:
    """Resolve and hash the implementations used by Planck and ACT likelihoods."""

    clipy_root = Path(packages_path).resolve() / "code" / "planck" / "clipy"
    clipy_files = sorted(
        path
        for path in clipy_root.rglob("*")
        if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts
    )
    clipy_records = [
        {
            "path": str(path.relative_to(clipy_root)),
            "size_bytes": path.stat().st_size,
            "sha256": _hash_file(path),
        }
        for path in clipy_files
    ]
    act_distribution = _distribution_inventory("act_dr6_lenslike")
    return {
        "camb": assert_locked_camb_runtime(),
        "clipy": {
            "expected_version": REQUIRED_PACKAGE_VERSIONS["clipy-like"],
            "root": str(clipy_root),
            "files": clipy_records,
            "tree_fingerprint": _hash_object(clipy_records),
        },
        "act_dr6_lenslike": {
            "version": act_distribution.get("version"),
            "fingerprint": act_distribution.get("fingerprint"),
        },
    }


def assert_loaded_likelihood_runtime(
    packages_path: str | Path,
) -> dict[str, Any]:
    """Check the modules actually imported by live reference evaluation."""

    runtime = likelihood_runtime_inventory(packages_path)
    clipy_module = sys.modules.get("clipy")
    if clipy_module is None:
        raise RuntimeError("Planck reference cases did not import clipy")
    clipy_origin = Path(str(getattr(clipy_module, "__file__", ""))).resolve()
    clipy_root = Path(runtime["clipy"]["root"]).resolve()
    clipy_version = str(getattr(clipy_module, "__version__", ""))
    if not clipy_origin.is_relative_to(clipy_root):
        raise RuntimeError(f"Planck loaded clipy outside packages code tree: {clipy_origin}")
    if clipy_version != REQUIRED_PACKAGE_VERSIONS["clipy-like"]:
        raise RuntimeError(f"Planck loaded clipy version {clipy_version!r}")
    act_module = sys.modules.get("act_dr6_lenslike")
    if act_module is None:
        raise RuntimeError("ACT reference case did not import act_dr6_lenslike")
    act_origin = Path(str(getattr(act_module, "__file__", ""))).resolve()
    act_distribution = importlib.metadata.distribution("act_dr6_lenslike")
    act_root = Path(act_distribution.locate_file("")).resolve()
    if not act_origin.is_relative_to(act_root):
        raise RuntimeError(f"ACT loaded outside locked distribution: {act_origin}")
    if act_distribution.version != REQUIRED_PACKAGE_VERSIONS["act_dr6_lenslike"]:
        raise RuntimeError(f"ACT loaded version {act_distribution.version!r}")
    camspec_module_name = (
        "cobaya.likelihoods.planck_NPIPE_highl_CamSpec.TTTEEE"
    )
    camspec_module = sys.modules.get(camspec_module_name)
    if camspec_module is None:
        raise RuntimeError("NPIPE CamSpec reference case did not load its likelihood")
    camspec_origin = Path(str(getattr(camspec_module, "__file__", ""))).resolve()
    cobaya_distribution = importlib.metadata.distribution("cobaya")
    cobaya_root = Path(cobaya_distribution.locate_file("")).resolve()
    if not camspec_origin.is_relative_to(cobaya_root):
        raise RuntimeError(
            f"NPIPE CamSpec loaded outside locked Cobaya distribution: {camspec_origin}"
        )
    runtime["clipy"]["loaded_origin"] = str(clipy_origin)
    runtime["clipy"]["loaded_version"] = clipy_version
    runtime["act_dr6_lenslike"]["loaded_origin"] = str(act_origin)
    runtime["planck_NPIPE_highl_CamSpec"] = {
        "loaded_origin": str(camspec_origin),
        "sha256": _hash_file(camspec_origin),
        "cobaya_version": cobaya_distribution.version,
    }
    return runtime


def _distribution_inventory(name: str) -> dict[str, Any]:
    """Hash an installed wheel's metadata and importable payload.

    Python environments do not retain the original wheel archive. Hashing every
    installed file listed by ``importlib.metadata`` plus its RECORD/WHEEL files
    provides the auditable installed-code equivalent and detects local edits.
    """

    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return {
            "distribution": name,
            "installed": False,
            "version": None,
            "files": [],
            "fingerprint": None,
        }
    records: list[dict[str, Any]] = []
    for relative in distribution.files or []:
        path = Path(distribution.locate_file(relative))
        if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        records.append(
            {
                "path": str(relative),
                "size_bytes": path.stat().st_size,
                "sha256": _hash_file(path),
            }
        )
    records.sort(key=lambda item: item["path"])
    return {
        "distribution": name,
        "installed": True,
        "version": distribution.version,
        "files": records,
        "fingerprint": _hash_object(records),
    }


def _site_packages_ownership_inventory(
    *,
    allowed_distributions: Sequence[str],
    site_roots: Sequence[str | Path] | None = None,
) -> dict[str, Any]:
    """Prove that every import-affecting site-packages file has an owner.

    Distribution RECORD inventories are closed over registered wheels, but a
    loose ``rogue_optional.py`` can still be imported without appearing in any
    distribution.  Walk the active venv's site-package roots and bind all
    Python sources, native extensions and path hooks to the frozen distribution
    set.  Generated ``__pycache__`` entries are derived, non-authoritative
    caches: they are excluded from every stable identity only when the
    corresponding hashed, distribution-owned source exists.  Unowned or
    sourceless bytecode remains a fatal closure violation.
    """

    reasons: list[str] = []
    venv_root = Path(sys.prefix).resolve()
    if site_roots is None:
        roots = sorted(
            {
                Path(raw).resolve()
                for raw in site.getsitepackages()
                if Path(raw).is_dir()
                and Path(raw).resolve().is_relative_to(venv_root)
            },
            key=str,
        )
    else:
        roots = sorted({Path(raw).resolve() for raw in site_roots}, key=str)
    if not roots or any(not root.is_dir() for root in roots):
        reasons.append("exact_site_packages_roots_missing")

    owners_by_path: dict[Path, set[str]] = {}
    owners_by_target: dict[Path, set[str]] = {}
    for raw_name in sorted(set(allowed_distributions)):
        name = canonicalize_name(raw_name)
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            reasons.append(f"exact_site_packages_owner_missing:{name}")
            continue
        for relative in distribution.files or []:
            candidate = Path(distribution.locate_file(relative)).absolute()
            owners_by_path.setdefault(candidate, set()).add(name)
            try:
                owners_by_target.setdefault(candidate.resolve(), set()).add(name)
            except OSError:
                reasons.append(f"exact_site_packages_owner_path_unreadable:{name}")

    extension_suffixes = tuple(
        sorted(
            set(importlib.machinery.EXTENSION_SUFFIXES)
            | {".so", ".pyd", ".dylib"},
            key=len,
            reverse=True,
        )
    )

    def logical_path(path: Path) -> str:
        absolute = path.absolute()
        for index, root in enumerate(roots):
            try:
                return f"{index}:{absolute.relative_to(root).as_posix()}"
            except ValueError:
                continue
        return f"outside:{absolute}"

    def path_owners(path: Path) -> list[str]:
        owners = set(owners_by_path.get(path.absolute(), set()))
        try:
            owners.update(owners_by_target.get(path.resolve(), set()))
        except OSError:
            pass
        return sorted(owners)

    def is_import_affecting(path: Path) -> bool:
        name = path.name
        return (
            name.endswith((".py", ".pyc", ".pth", ".egg-link"))
            or name in {"sitecustomize.py", "usercustomize.py"}
            or name.endswith(extension_suffixes)
        )

    owned_records: list[dict[str, Any]] = []
    unowned_import_files: list[str] = []
    unowned_generated_bytecode: list[str] = []
    symlinked_directories: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            logical = logical_path(path)
            if path.is_symlink() and path.is_dir():
                symlinked_directories.append(logical)
                reasons.append(
                    f"exact_site_packages_symlinked_directory:{logical}"
                )
                continue
            if not is_import_affecting(path):
                continue
            if not path.is_file():
                unowned_import_files.append(logical)
                reasons.append(f"exact_site_packages_import_file_unreadable:{logical}")
                continue
            try:
                target = path.resolve()
            except OSError:
                unowned_import_files.append(logical)
                reasons.append(f"exact_site_packages_import_file_unreadable:{logical}")
                continue
            if not any(target.is_relative_to(candidate) for candidate in roots):
                unowned_import_files.append(logical)
                reasons.append(f"exact_site_packages_import_file_outside_root:{logical}")
                continue
            if path.suffix == ".pyc" and "__pycache__" in path.parts:
                try:
                    source = Path(importlib.util.source_from_cache(str(path))).absolute()
                except (ValueError, NotImplementedError):
                    source = Path()
                owners = path_owners(source) if source != Path() else []
                if not source.is_file() or not owners:
                    unowned_generated_bytecode.append(logical)
                    reasons.append(
                        f"exact_site_packages_generated_bytecode_unowned:{logical}"
                    )
                    continue
                # A source-owned cache is derived from an already-hashed wheel
                # source.  Its path, count and bytes are intentionally absent
                # from the stable payload because normal imports may create or
                # refresh it between preflight and execution stages.
                continue
            owners = path_owners(path)
            if not owners:
                unowned_import_files.append(logical)
                reasons.append(f"exact_site_packages_import_file_unowned:{logical}")
                continue
            owned_records.append(
                {
                    "path": logical,
                    "owners": owners,
                    "size_bytes": path.stat().st_size,
                    "sha256": _hash_file(path),
                }
            )

    owned_records.sort(key=lambda item: item["path"])
    payload = {
        "schema_version": 1,
        "site_root_count": len(roots),
        "owned_import_files": {
            "count": len(owned_records),
            "fingerprint": _hash_object(owned_records),
        },
        "generated_bytecode_policy": dict(GENERATED_BYTECODE_CACHE_POLICY),
        "unowned_import_files": sorted(set(unowned_import_files)),
        "unowned_generated_bytecode": sorted(
            set(unowned_generated_bytecode)
        ),
        "symlinked_directories": sorted(set(symlinked_directories)),
    }
    return {
        **payload,
        "passed": not reasons,
        "reasons": reasons,
        "fingerprint": _hash_object(payload),
    }


def _verify_installed_distribution_against_wheel(
    distribution_name: str,
    wheel_path: str | Path,
) -> dict[str, Any]:
    """Compare installed payload bytes with the immutable wheel archive.

    ``importlib.metadata`` inventories are useful drift detectors, but their
    RECORD file lives in the mutable environment and therefore cannot establish
    provenance.  This verifier treats the frozen wheel's RECORD and member
    bytes as the authority, validates that RECORD internally, and then hashes
    the corresponding installed files.  Installer-rewritten console scripts
    are deliberately excluded; formal runs invoke ``python -I -m cobaya.run``
    and bind that module plus the isolated import policy directly.
    """

    wheel = Path(wheel_path).expanduser().resolve()
    reasons: list[str] = []
    expected_records: list[dict[str, Any]] = []
    installed_records: list[dict[str, Any]] = []
    skipped_members: list[str] = []
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return {
            "passed": False,
            "reasons": ["installed_distribution_missing"],
            "distribution": canonicalize_name(distribution_name),
            "wheel_path": str(wheel),
            "wheel_sha256": _hash_file(wheel) if wheel.is_file() else None,
            "expected_payload_fingerprint": None,
            "installed_payload_fingerprint": None,
            "checked_file_count": 0,
            "skipped_installer_rewritten_members": [],
        }
    if not wheel.is_file():
        return {
            "passed": False,
            "reasons": ["frozen_wheel_missing"],
            "distribution": canonicalize_name(distribution_name),
            "wheel_path": str(wheel),
            "wheel_sha256": None,
            "expected_payload_fingerprint": None,
            "installed_payload_fingerprint": None,
            "checked_file_count": 0,
            "skipped_installer_rewritten_members": [],
        }

    try:
        with zipfile.ZipFile(wheel) as archive:
            members = {
                info.filename: info
                for info in archive.infolist()
                if not info.is_dir()
            }
            record_paths = sorted(
                name
                for name in members
                if name.count("/") == 1 and name.endswith(".dist-info/RECORD")
            )
            if len(record_paths) != 1:
                reasons.append("wheel_record_file_count_invalid")
                record_rows: dict[str, tuple[str, str]] = {}
                record_path = None
            else:
                record_path = record_paths[0]
                try:
                    decoded_record = archive.read(record_path).decode("utf-8")
                    parsed_rows = list(csv.reader(io.StringIO(decoded_record)))
                except (UnicodeDecodeError, csv.Error, KeyError) as exc:
                    reasons.append(f"wheel_record_unreadable:{type(exc).__name__}")
                    parsed_rows = []
                record_rows = {}
                for row_number, row in enumerate(parsed_rows, start=1):
                    if len(row) != 3 or not row[0] or row[0] in record_rows:
                        reasons.append(f"wheel_record_row_invalid:{row_number}")
                        continue
                    record_rows[row[0]] = (row[1], row[2])

            data_prefixes = sorted(
                {
                    name.split("/", 1)[0]
                    for name in members
                    if ".data/" in name
                }
            )
            for member_name in sorted(members):
                if member_name == record_path:
                    continue
                row = record_rows.get(member_name)
                if row is None:
                    reasons.append(f"wheel_member_missing_from_record:{member_name}")
                    continue
                record_digest, record_size = row
                member_bytes = archive.read(member_name)
                expected_record_digest = (
                    "sha256="
                    + base64.urlsafe_b64encode(hashlib.sha256(member_bytes).digest())
                    .decode("ascii")
                    .rstrip("=")
                )
                if record_digest != expected_record_digest:
                    reasons.append(f"wheel_record_hash_mismatch:{member_name}")
                if record_size != str(len(member_bytes)):
                    reasons.append(f"wheel_record_size_mismatch:{member_name}")

                installed_relative = member_name
                installed_path: Path
                data_prefix = next(
                    (
                        prefix
                        for prefix in data_prefixes
                        if member_name.startswith(prefix + "/")
                    ),
                    None,
                )
                if data_prefix is not None:
                    remainder = member_name[len(data_prefix) + 1 :]
                    category, separator, relative = remainder.partition("/")
                    if not separator or not relative:
                        reasons.append(f"wheel_data_member_invalid:{member_name}")
                        continue
                    if category == "scripts":
                        skipped_members.append(member_name)
                        continue
                    if category in {"purelib", "platlib"}:
                        installed_relative = relative
                        installed_path = Path(distribution.locate_file(relative))
                    elif category == "data":
                        installed_relative = relative
                        installed_path = Path(sys.prefix) / relative
                    elif category == "headers":
                        installed_relative = relative
                        installed_path = Path(sysconfig.get_path("include")) / relative
                    else:
                        reasons.append(
                            f"wheel_data_install_category_unsupported:{category}"
                        )
                        continue
                else:
                    installed_path = Path(distribution.locate_file(member_name))

                expected = {
                    "wheel_member": member_name,
                    "installed_relative_path": installed_relative,
                    "size_bytes": len(member_bytes),
                    "sha256": f"sha256:{hashlib.sha256(member_bytes).hexdigest()}",
                }
                expected_records.append(expected)
                if not installed_path.is_file():
                    reasons.append(f"installed_wheel_member_missing:{member_name}")
                    installed_records.append(
                        {
                            "wheel_member": member_name,
                            "installed_relative_path": installed_relative,
                            "size_bytes": None,
                            "sha256": None,
                        }
                    )
                    continue
                installed = {
                    "wheel_member": member_name,
                    "installed_relative_path": installed_relative,
                    "size_bytes": installed_path.stat().st_size,
                    "sha256": _hash_file(installed_path),
                }
                installed_records.append(installed)
                if (
                    installed["size_bytes"] != expected["size_bytes"]
                    or installed["sha256"] != expected["sha256"]
                ):
                    reasons.append(f"installed_wheel_member_mismatch:{member_name}")
            archive_members = set(members)
            for recorded_member in sorted(set(record_rows) - archive_members):
                if recorded_member != record_path:
                    reasons.append(
                        f"wheel_record_references_missing_member:{recorded_member}"
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        reasons.append(f"frozen_wheel_unreadable:{type(exc).__name__}")

    expected_fingerprint = _hash_object(expected_records)
    installed_fingerprint = _hash_object(installed_records)
    if expected_fingerprint != installed_fingerprint:
        reasons.append("installed_payload_fingerprint_mismatch")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "distribution": canonicalize_name(distribution_name),
        "version": distribution.version,
        "wheel_path": str(wheel),
        "wheel_sha256": _hash_file(wheel),
        "expected_payload_fingerprint": expected_fingerprint,
        "installed_payload_fingerprint": installed_fingerprint,
        "checked_file_count": len(expected_records),
        "skipped_installer_rewritten_members": skipped_members,
    }


def _parse_exact_version_lock(path: Path) -> tuple[dict[str, str], list[str]]:
    pins: dict[str, str] = {}
    reasons: list[str] = []
    if not path.is_file():
        return {}, ["exact_dependency_lock_missing"]
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line or any(token in line for token in (";", "[", "]")):
            reasons.append(f"dependency_lock_not_exact_pin:{line_number}")
            continue
        name, version = (part.strip() for part in line.split("==", 1))
        normalized = canonicalize_name(name)
        if not name or not version or normalized in pins:
            reasons.append(f"dependency_lock_duplicate_or_invalid:{line_number}")
            continue
        pins[normalized] = version
    return pins, reasons


def _exact_python_import_policy(
    lock_path: str | Path = EXACT_DEPENDENCY_LOCK_PATH,
) -> dict[str, Any]:
    """Inventory every startup hook that ``python -I`` can still execute."""

    reasons: list[str] = []
    pythonpath = os.environ.get("PYTHONPATH")
    if pythonpath:
        reasons.append("exact_pythonpath_must_be_empty")
    isolated_interpreter = bool(sys.flags.isolated)
    ignore_environment = bool(sys.flags.ignore_environment)
    no_user_site = bool(sys.flags.no_user_site)
    safe_path = bool(sys.flags.safe_path)
    if not isolated_interpreter:
        reasons.append("exact_interpreter_requires_python_I")
    if not ignore_environment:
        reasons.append("exact_interpreter_must_ignore_python_environment")
    if not no_user_site:
        reasons.append("exact_interpreter_must_disable_user_site")
    if not safe_path:
        reasons.append("exact_interpreter_requires_safe_path")
    lock_pins, lock_reasons = _parse_exact_version_lock(Path(lock_path))
    reasons.extend(f"exact_import_policy:{reason}" for reason in lock_reasons)
    venv_root = Path(sys.prefix).resolve()
    site_roots = sorted(
        {
            Path(raw).resolve()
            for raw in site.getsitepackages()
            if Path(raw).is_dir() and Path(raw).resolve().is_relative_to(venv_root)
        },
        key=str,
    )
    if not site_roots:
        reasons.append("exact_venv_site_packages_missing")
    hook_paths = sorted(
        {
            path.resolve()
            for root in site_roots
            for pattern in ("*.pth", "sitecustomize.py", "usercustomize.py")
            for path in root.glob(pattern)
            if path.is_file()
        },
        key=str,
    )
    owners: dict[Path, set[str]] = {path: set() for path in hook_paths}
    for name, expected_version in sorted(lock_pins.items()):
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        if distribution.version != expected_version:
            continue
        for relative in distribution.files or []:
            candidate = Path(distribution.locate_file(relative)).resolve()
            if candidate in owners:
                owners[candidate].add(name)

    hook_records: list[dict[str, Any]] = []
    for path in hook_paths:
        path_owners = sorted(owners[path])
        if not path_owners:
            reasons.append(f"exact_import_hook_unowned:{path.name}")
        executable_lines = 0
        external_paths: list[str] = []
        if path.suffix == ".pth":
            for raw_line in path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith(("import ", "import\t")):
                    executable_lines += 1
                    continue
                resolved = (path.parent / line).resolve()
                if not resolved.is_relative_to(venv_root):
                    external_paths.append(str(resolved))
                    reasons.append(f"exact_pth_path_outside_venv:{path.name}")
        hook_records.append(
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": _hash_file(path),
                "owners": path_owners,
                "trusted_owner": bool(path_owners),
                "executable_lines": executable_lines,
                "external_paths": external_paths,
            }
        )
    payload = {
        "schema_version": 1,
        "isolated_interpreter": isolated_interpreter,
        "python_flag": "-I",
        "ignore_environment": ignore_environment,
        "no_user_site": no_user_site,
        "safe_path": safe_path,
        "pythonpath_empty": not bool(pythonpath),
        "user_site_disabled_by_child": no_user_site,
        "venv_root": str(venv_root),
        "site_package_roots": [str(path) for path in site_roots],
        "startup_hooks": hook_records,
    }
    return {
        **payload,
        "passed": not reasons,
        "reasons": reasons,
        "fingerprint": _hash_object(payload),
    }


def _installed_runtime_closure(roots: Sequence[str]) -> tuple[set[str], list[str]]:
    pending = list(roots)
    closure: set[str] = set()
    reasons: list[str] = []
    marker_environment = default_environment()
    while pending:
        requested = pending.pop()
        normalized = canonicalize_name(requested)
        if normalized in closure:
            continue
        try:
            distribution = importlib.metadata.distribution(requested)
        except importlib.metadata.PackageNotFoundError:
            reasons.append(f"runtime_dependency_missing:{requested}")
            continue
        installed_name = canonicalize_name(
            distribution.metadata.get("Name") or requested
        )
        closure.add(installed_name)
        for raw_requirement in distribution.requires or []:
            try:
                requirement = Requirement(raw_requirement)
            except ValueError:
                reasons.append(
                    f"runtime_dependency_requirement_invalid:{installed_name}"
                )
                continue
            if requirement.marker and not requirement.marker.evaluate(
                marker_environment
            ):
                continue
            pending.append(requirement.name)
    return closure, reasons


def _exact_runtime_closure_identity(
    lock_pins: Mapping[str, str] | None = None,
    dependency_closure: set[str] | None = None,
) -> dict[str, Any]:
    """Bind the complete installed environment, including frozen bootstrap pip."""

    reasons: list[str] = []
    if lock_pins is None:
        parsed, lock_reasons = _parse_exact_version_lock(EXACT_DEPENDENCY_LOCK_PATH)
        lock_pins = parsed
        reasons.extend(lock_reasons)
    normalized_pins = {
        canonicalize_name(str(name)): str(version)
        for name, version in lock_pins.items()
    }
    if dependency_closure is None:
        dependency_closure, closure_reasons = _installed_runtime_closure(
            RUNTIME_CLOSURE_ROOTS
        )
        reasons.extend(closure_reasons)
    normalized_closure = {canonicalize_name(name) for name in dependency_closure}
    if normalized_closure != set(normalized_pins):
        reasons.append("exact_dependency_runtime_closure_mismatch")

    installed_versions: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            reasons.append("installed_distribution_name_missing")
            continue
        name = canonicalize_name(raw_name)
        if name in installed_versions:
            reasons.append(f"installed_distribution_duplicate:{name}")
            continue
        installed_versions[name] = str(distribution.version)
    allowed_versions = {
        **normalized_pins,
        **{
            name: str(record["version"])
            for name, record in FROZEN_BOOTSTRAP_DISTRIBUTIONS.items()
        },
    }
    missing = sorted(set(allowed_versions) - set(installed_versions))
    unexpected = sorted(set(installed_versions) - set(allowed_versions))
    if missing:
        reasons.append("exact_installed_distributions_missing:" + ",".join(missing))
    if unexpected:
        reasons.append(
            "exact_installed_distributions_unregistered:" + ",".join(unexpected)
        )

    distribution_fingerprints: dict[str, dict[str, Any]] = {}
    for name, expected_version in sorted(allowed_versions.items()):
        inventory = _distribution_inventory(name)
        summary = {
            "version": inventory.get("version"),
            "fingerprint": inventory.get("fingerprint"),
        }
        distribution_fingerprints[name] = summary
        if (
            inventory.get("installed") is not True
            or summary["version"] != expected_version
        ):
            reasons.append(f"exact_installed_distribution_version_mismatch:{name}")
    bootstrap_records = {
        name: distribution_fingerprints.get(name)
        for name in sorted(FROZEN_BOOTSTRAP_DISTRIBUTIONS)
    }
    for name, expected in FROZEN_BOOTSTRAP_DISTRIBUTIONS.items():
        if bootstrap_records.get(name) != dict(expected):
            reasons.append(f"exact_bootstrap_distribution_fingerprint_mismatch:{name}")

    site_packages_ownership = _site_packages_ownership_inventory(
        allowed_distributions=sorted(allowed_versions)
    )
    reasons.extend(site_packages_ownership.get("reasons") or [])

    payload = {
        "required_versions": dict(sorted(normalized_pins.items())),
        "dependency_closure": sorted(normalized_closure),
        "installed_distributions": sorted(installed_versions),
        "bootstrap_distributions": bootstrap_records,
        "distribution_fingerprints": distribution_fingerprints,
        "site_packages_ownership": site_packages_ownership,
    }
    return {
        **payload,
        "passed": not reasons,
        "reasons": reasons,
        "fingerprint": _hash_object(payload),
    }


def _trusted_wheel_manifest(
    lock_pins: Mapping[str, str],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Load the frozen PyPI wheel closure without consulting the network.

    The manifest is a pre-run trust decision. Its own byte hash is frozen in
    this producer, and every record is cross-checked against the independently
    committed exact-version lock before local wheel archives are trusted.
    """

    path = TRUSTED_WHEEL_MANIFEST_PATH
    reasons: list[str] = []
    if not path.is_file():
        return None, ["exact_wheel_manifest_missing"]
    manifest_hash = _hash_file(path)
    if manifest_hash != TRUSTED_WHEEL_MANIFEST_SHA256:
        reasons.append("exact_wheel_manifest_hash_not_preregistered")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        reasons.append(f"exact_wheel_manifest_unreadable:{type(exc).__name__}")
        return None, reasons
    if not isinstance(payload, Mapping):
        reasons.append("exact_wheel_manifest_not_mapping")
        return None, reasons
    if payload.get("schema_version") != 1:
        reasons.append("exact_wheel_manifest_schema_mismatch")
    if payload.get("profile_id") != EXACT_PROFILE_ID:
        reasons.append("exact_wheel_manifest_profile_mismatch")
    if payload.get("created_before_smoke_or_formal_run") is not True:
        reasons.append("exact_wheel_manifest_not_pre_run")
    if payload.get("generated_at_utc") is not None:
        reasons.append("exact_wheel_manifest_contains_nondeterministic_timestamp")
    if payload.get("requirements_path") != "w0wa_exact_requirements.txt" or (
        payload.get("requirements_sha256") != TRUSTED_DEPENDENCY_LOCK_SHA256
    ):
        reasons.append("exact_wheel_manifest_lock_binding_mismatch")
    if payload.get("selection_rule") != (
        "highest_priority_non_yanked_compatible_pypi_wheel"
    ):
        reasons.append("exact_wheel_manifest_selection_rule_mismatch")
    if payload.get("python_version") != sys.version:
        reasons.append("exact_wheel_manifest_python_version_mismatch")
    if payload.get("platform_tags") != [str(tag) for tag in sys_tags()]:
        reasons.append("exact_wheel_manifest_platform_tags_mismatch")

    wheels = payload.get("wheels")
    if not isinstance(wheels, list) or not wheels:
        reasons.append("exact_wheel_manifest_records_missing")
        wheels = []
    observed_pins: dict[str, str] = {}
    filenames: set[str] = set()
    for index, record in enumerate(wheels):
        if not isinstance(record, Mapping):
            reasons.append(f"exact_wheel_manifest_record_invalid:{index}")
            continue
        project = canonicalize_name(str(record.get("project") or ""))
        version = str(record.get("version") or "")
        filename = str(record.get("filename") or "")
        sha256 = str(record.get("sha256") or "")
        size = record.get("size_bytes")
        url = str(record.get("url") or "")
        source_api = str(record.get("source_api") or "")
        if not project or project in observed_pins:
            reasons.append(f"exact_wheel_manifest_project_duplicate_or_invalid:{index}")
        else:
            observed_pins[project] = version
        if (
            not filename.endswith(".whl")
            or Path(filename).name != filename
            or filename in filenames
        ):
            reasons.append(f"exact_wheel_manifest_filename_invalid:{index}")
        filenames.add(filename)
        if not (
            sha256.startswith("sha256:")
            and len(sha256) == len("sha256:") + 64
            and all(character in "0123456789abcdef" for character in sha256[7:])
        ):
            reasons.append(f"exact_wheel_manifest_sha256_invalid:{index}")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            reasons.append(f"exact_wheel_manifest_size_invalid:{index}")
        if not url.startswith("https://files.pythonhosted.org/"):
            reasons.append(f"exact_wheel_manifest_url_invalid:{index}")
        expected_api = f"https://pypi.org/pypi/{project}/{version}/json"
        if source_api != expected_api:
            reasons.append(f"exact_wheel_manifest_source_api_invalid:{index}")
    if observed_pins != dict(lock_pins):
        reasons.append("exact_wheel_manifest_dependency_set_mismatch")
    direct_records = {
        str(record.get("filename")): str(record.get("sha256"))
        for record in wheels
        if isinstance(record, Mapping)
        and str(record.get("filename")) in REQUIRED_WHEELS
    }
    if direct_records != REQUIRED_WHEELS:
        reasons.append("exact_direct_wheel_commitments_mismatch")
    return {
        "path": str(path.resolve()),
        "sha256": manifest_hash,
        "size_bytes": path.stat().st_size,
        "profile_id": payload.get("profile_id"),
        "python_version": payload.get("python_version"),
        "platform_tags": payload.get("platform_tags"),
        "selection_rule": payload.get("selection_rule"),
        "wheels": [dict(record) for record in wheels if isinstance(record, Mapping)],
    }, reasons


def exact_environment_inventory(
    lock_path: str | Path,
    wheels_path: str | Path,
) -> dict[str, Any]:
    lock = Path(lock_path)
    wheel_root = Path(wheels_path).resolve()
    lock_pins, reasons = _parse_exact_version_lock(lock)
    if lock.is_file() and _hash_file(lock) != TRUSTED_DEPENDENCY_LOCK_SHA256:
        reasons.append("exact_dependency_lock_hash_not_preregistered")
    runtime_closure, closure_reasons = _installed_runtime_closure(
        RUNTIME_CLOSURE_ROOTS
    )
    reasons.extend(closure_reasons)
    missing_lock_pins = sorted(runtime_closure - set(lock_pins))
    extra_lock_pins = sorted(set(lock_pins) - runtime_closure)
    if missing_lock_pins:
        reasons.append("runtime_closure_unpinned:" + ",".join(missing_lock_pins))
    if extra_lock_pins:
        reasons.append("dependency_lock_outside_runtime_closure:" + ",".join(extra_lock_pins))
    distributions = {
        name: _distribution_inventory(name) for name in sorted(lock_pins)
    }
    for name, expected in lock_pins.items():
        installed = distributions[name]
        if installed["installed"] is not True:
            reasons.append(f"required_distribution_missing:{name}")
        elif installed["version"] != expected:
            reasons.append(
                f"required_distribution_version_mismatch:{name}:"
                f"{installed['version']}!={expected}"
            )
    try:
        camb_runtime = assert_locked_camb_runtime()
    except (ImportError, importlib.metadata.PackageNotFoundError, RuntimeError) as exc:
        camb_runtime = None
        reasons.append(f"locked_camb_runtime_invalid:{type(exc).__name__}:{exc}")
    runtime_environment = environment_manifest()
    reasons.extend(
        _runtime_module_identity_reasons(
            runtime_environment.get("runtime_modules"),
            required_versions=lock_pins,
        )
    )
    runtime_identity = runtime_environment.get("runtime_closure") or {}
    if runtime_identity.get("passed") is not True:
        reasons.extend(
            f"exact_runtime_identity:{reason}"
            for reason in runtime_identity.get("reasons") or []
        )
    if (
        runtime_identity.get("required_versions") != dict(sorted(lock_pins.items()))
        or runtime_identity.get("dependency_closure") != sorted(runtime_closure)
    ):
        reasons.append("exact_runtime_identity_lock_or_closure_mismatch")
    thread_environment = runtime_environment["thread_environment"]
    if any(value != "3" for value in thread_environment.values()):
        reasons.append("exact_thread_environment_must_be_omp_mkl_openblas_3")
    native_runtime = runtime_environment.get("native_runtime") or {}
    if native_runtime.get("passed") is not True:
        reasons.extend(
            str(reason) for reason in native_runtime.get("reasons") or []
        )
    import_policy = runtime_environment.get("import_policy") or {}
    if import_policy.get("passed") is not True:
        reasons.extend(str(reason) for reason in import_policy.get("reasons") or [])
    if not lock.is_file():
        lock_record = None
    else:
        lock_record = {
            "path": str(lock.resolve()),
            "sha256": _hash_file(lock),
        }
    wheel_manifest, wheel_manifest_reasons = _trusted_wheel_manifest(lock_pins)
    reasons.extend(wheel_manifest_reasons)
    wheel_records: list[dict[str, Any]] = []
    expected_wheel_records = {
        str(record["filename"]): record
        for record in ((wheel_manifest or {}).get("wheels") or [])
    }
    for filename, expected in sorted(expected_wheel_records.items()):
        wheel = wheel_root / filename
        if not wheel.is_file():
            reasons.append(f"exact_wheel_missing:{filename}")
            continue
        actual_hash = _hash_file(wheel)
        actual_size = wheel.stat().st_size
        if actual_hash != expected.get("sha256"):
            reasons.append(f"exact_wheel_hash_mismatch:{filename}")
        if actual_size != expected.get("size_bytes"):
            reasons.append(f"exact_wheel_size_mismatch:{filename}")
        wheel_records.append(
            {
                "project": expected.get("project"),
                "version": expected.get("version"),
                "filename": filename,
                "path": str(wheel),
                "size_bytes": actual_size,
                "sha256": actual_hash,
            }
        )
    expected_wheels_by_project = {
        canonicalize_name(str(record.get("project") or "")): record
        for record in expected_wheel_records.values()
    }
    installed_wheel_bindings: dict[str, dict[str, Any]] = {}
    for name in sorted(lock_pins):
        expected = expected_wheels_by_project.get(name)
        if expected is None:
            reasons.append(f"installed_wheel_binding:{name}:frozen_wheel_not_registered")
            continue
        binding = _verify_installed_distribution_against_wheel(
            name,
            wheel_root / str(expected.get("filename") or ""),
        )
        installed_wheel_bindings[name] = binding
        reasons.extend(
            f"installed_wheel_binding:{name}:{reason}"
            for reason in binding.get("reasons") or []
        )
    if wheel_root.is_dir():
        unexpected_wheels = sorted(
            path.name
            for path in wheel_root.iterdir()
            if path.is_file()
            and path.suffix == ".whl"
            and path.name not in expected_wheel_records
        )
        if unexpected_wheels:
            reasons.append("exact_wheels_unregistered:" + ",".join(unexpected_wheels))
    payload = {
        "required_versions": lock_pins,
        "runtime_closure": sorted(runtime_closure),
        "lock": lock_record,
        "wheels_path": str(wheel_root),
        "wheel_manifest": wheel_manifest,
        "wheels": wheel_records,
        "runtime": runtime_environment,
        "camb_runtime": camb_runtime,
        "distributions": distributions,
        "installed_wheel_bindings": installed_wheel_bindings,
    }
    return {
        **payload,
        "passed": not reasons,
        "reasons": reasons,
        "fingerprint": _hash_object(payload),
    }


def verify_likelihood_code_manifest(
    path: str | Path = TRUSTED_LIKELIHOOD_CODE_MANIFEST_PATH,
) -> dict[str, Any]:
    """Verify the pre-run executable-code commitment for the exact stack."""

    manifest_path = Path(path).expanduser().resolve()
    reasons: list[str] = []
    if not manifest_path.is_file():
        return {
            "passed": False,
            "reasons": ["likelihood_code_manifest_missing"],
            "path": str(manifest_path),
        }
    digest = _hash_file(manifest_path)
    if digest != TRUSTED_LIKELIHOOD_CODE_MANIFEST_SHA256:
        reasons.append("likelihood_code_manifest_hash_not_preregistered")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "passed": False,
            "reasons": [
                *reasons,
                f"likelihood_code_manifest_unreadable:{type(exc).__name__}",
            ],
            "path": str(manifest_path),
            "sha256": digest,
        }
    expected_trusted = {
        "canonical_config": TRUSTED_CANONICAL_CONFIG_SHA256,
        "data_manifest": TRUSTED_DATA_MANIFEST_SHA256,
        "dependency_lock": TRUSTED_DEPENDENCY_LOCK_SHA256,
        "reference_cases": TRUSTED_REFERENCE_SPEC_SHA256,
        "wheel_manifest": TRUSTED_WHEEL_MANIFEST_SHA256,
    }
    if payload.get("schema_version") != 1 or payload.get("kind") != (
        "w0wa_exact_likelihood_code_commitment"
    ):
        reasons.append("likelihood_code_manifest_schema_mismatch")
    if payload.get("profile_id") != EXACT_PROFILE_ID:
        reasons.append("likelihood_code_manifest_profile_mismatch")
    if payload.get("environment_revision") != EXACT_ENVIRONMENT_REVISION:
        reasons.append("likelihood_code_manifest_environment_revision_mismatch")
    if payload.get("frozen_before_formal_run") is not True:
        reasons.append("likelihood_code_manifest_not_pre_run")
    if payload.get("likelihoods") != list(REQUIRED_LIKELIHOODS):
        reasons.append("likelihood_code_manifest_likelihood_set_mismatch")
    if payload.get("adequacy_likelihoods") != list(
        ADEQUACY_REFERENCE_LIKELIHOODS
    ):
        reasons.append("likelihood_code_manifest_adequacy_set_mismatch")
    if payload.get("packages") != REQUIRED_PACKAGE_VERSIONS:
        reasons.append("likelihood_code_manifest_package_versions_mismatch")
    if payload.get("wheel_sha256") != REQUIRED_WHEELS:
        reasons.append("likelihood_code_manifest_direct_wheels_mismatch")
    if payload.get("full_wheel_closure") != {
        "manifest_sha256": TRUSTED_WHEEL_MANIFEST_SHA256,
        "wheel_count": 52,
    }:
        reasons.append("likelihood_code_manifest_wheel_closure_mismatch")
    if payload.get("runtime_code_verification") != (
        "preflight_distribution_files_plus_loaded_likelihood_trees"
    ):
        reasons.append("likelihood_code_manifest_runtime_method_mismatch")
    if payload.get("trusted_artifacts") != expected_trusted:
        reasons.append("likelihood_code_manifest_artifact_commitments_mismatch")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "path": str(manifest_path),
        "sha256": digest,
        "payload": payload,
    }


def _with_self_hash(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    result.pop(field, None)
    result[field] = _hash_object(result)
    return result


def _verify_self_hash(payload: Mapping[str, Any], field: str) -> bool:
    declared = payload.get(field)
    unsigned = copy.deepcopy(dict(payload))
    unsigned.pop(field, None)
    return declared == _hash_object(unsigned)


def evaluate_reference_likelihoods(
    *,
    canonical_config: Mapping[str, Any],
    packages_path: str | Path,
    point: Mapping[str, Any],
    likelihood_names: Sequence[str],
    parameterization: str,
    theory_args: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    """Evaluate one source-pinned reference case against exact components.

    Planck/BAO/SN cases use the canonical theta/logA parameterization. The ACT
    upstream regression fixes H0/As directly, so that one case constructs a
    fixed-parameter model while retaining the exact ACT likelihood options and
    the globally locked CAMB theory implementation.
    """

    from cobaya.cosmo_input import create_input, planck_base_model
    from cobaya.input import update_info
    from cobaya.model import get_model
    from cobaya.tools import recursive_update

    selected = tuple(str(name) for name in likelihood_names)
    if not selected or not set(selected).issubset(REFERENCE_LIKELIHOODS):
        raise ValueError("reference case likelihood subset is invalid")
    selected_likelihoods = {
        name: copy.deepcopy((canonical_config.get("likelihood") or {}).get(name))
        for name in selected
    }
    if parameterization == "upstream_planck_sampled":
        # Mirror Cobaya's own source-pinned regression harness. In particular,
        # its BAO and SN tests switch the base model to sampled H0 when the
        # source point contains H0, while the Planck tests remove H0 and retain
        # theta_MC_100. Mixing the source chi2 with the other parameterization
        # is not a valid reference test, even if the two cosmologies are close.
        upstream_base_model = copy.deepcopy(planck_base_model)
        if "H0" in point:
            upstream_base_model["hubble"] = "H"
        info = create_input(
            planck_names=True,
            theory="camb",
            **upstream_base_model,
        )
        camb_info: dict[str, Any] = {
            "path": "global",
            "ignore_obsolete": True,
        }
        if theory_args is not None:
            if not isinstance(theory_args, Mapping) or not theory_args:
                raise ValueError("upstream Planck reference theory arguments invalid")
            camb_info["extra_args"] = copy.deepcopy(dict(theory_args))
        info = recursive_update(
            info,
            {
                "theory": {"camb": camb_info},
                "likelihood": selected_likelihoods,
            },
        )
        info = update_info(info)
        for likelihood_info in info["likelihood"].values():
            likelihood_info.pop("params", None)
    else:
        info = {
            "theory": copy.deepcopy(canonical_config.get("theory") or {}),
            "likelihood": selected_likelihoods,
        }
        if theory_args is not None:
            raise ValueError("unexpected reference theory arguments")
        if parameterization == "canonical_sampled":
            info["params"] = copy.deepcopy(canonical_config.get("params") or {})
            info["prior"] = copy.deepcopy(canonical_config.get("prior") or {})
        elif parameterization == "upstream_fixed":
            info["params"] = copy.deepcopy(dict(point))
        else:
            raise ValueError("reference case parameterization is invalid")
    info["packages_path"] = str(packages_path)
    assert_locked_camb_runtime()
    if "planck_NPIPE_highl_CamSpec.TTTEEE" in selected:
        # Cobaya's native CamSpec base otherwise trusts a writable float32
        # inverse-covariance cache using a filename derived only from dataset
        # options. It is not part of the official release and could be stale or
        # poisoned. The source-pinned reference therefore always inverts the
        # frozen official covariance in memory and neither reads nor writes the
        # generated *_covinv_*.npy file.
        from cobaya.likelihoods.base_classes import planck_2018_CamSpec_python

        planck_2018_CamSpec_python.use_cache = False
    model = get_model(info)
    required_inputs = set(model.parameterization.sampled_params())
    if parameterization in {"canonical_sampled", "upstream_planck_sampled"}:
        supplied = set(point)
        missing = sorted(required_inputs - supplied)
        extra = sorted(supplied - required_inputs)
        if missing:
            raise ValueError("reference point parameters missing: " + ",".join(missing))
        if extra:
            raise ValueError("reference point parameters unexpected: " + ",".join(extra))
        loglikes = model.loglikes(dict(point), as_dict=True, return_derived=False)
    else:
        if required_inputs:
            raise ValueError("upstream fixed reference unexpectedly has sampled inputs")
        loglikes = model.loglikes(as_dict=True, return_derived=False)
    if not isinstance(loglikes, Mapping):
        raise ValueError("live likelihood evaluator did not return component mapping")
    observed = {str(name): -2.0 * float(value) for name, value in loglikes.items()}
    if set(observed) != set(selected):
        raise ValueError("live likelihood components do not match reference case")
    if any(not math.isfinite(value) for value in observed.values()):
        raise ValueError("live likelihood evaluation returned non-finite chi2")
    return observed


def _source_record_is_pinned(case_id: str, source: Any) -> bool:
    if not isinstance(source, Mapping):
        return False
    expected = PINNED_REFERENCE_SOURCES.get(case_id)
    if expected is None or any(source.get(key) != value for key, value in expected.items()):
        return False
    expected_url = (
        f"{expected['repository']}/blob/{expected['commit']}/{expected['path']}"
    )
    return source.get("url") == expected_url


def verify_reference_values(
    reference_path: str | Path,
    *,
    canonical_config: Mapping[str, Any],
    packages_path: str | Path,
    config_sha256: str,
    data_fingerprint: str,
    evaluator: Any = None,
) -> dict[str, Any]:
    """Run all immutable, independently sourced likelihood reference cases."""

    path = Path(reference_path)
    if not path.is_file():
        return {
            "passed": False,
            "reasons": ["reference_likelihood_values_missing"],
            "path": str(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "passed": False,
            "reasons": [f"reference_likelihood_values_unreadable:{type(exc).__name__}"],
            "path": str(path),
        }
    reasons: list[str] = []
    trusted_hash = _hash_file(path)
    if trusted_hash != TRUSTED_REFERENCE_SPEC_SHA256:
        reasons.append("reference_registry_hash_not_preregistered")
    if payload.get("schema_version") != 2:
        reasons.append("reference_schema_mismatch")
    if payload.get("profile_id") != EXACT_PROFILE_ID:
        reasons.append("reference_profile_mismatch")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        reasons.append("reference_cases_missing")
        cases = []
    registered_components: list[str] = []
    case_ids: list[str] = []
    observed_by_case: dict[str, dict[str, float]] = {}
    live_evaluator = evaluator or evaluate_reference_likelihoods
    # A modified registry is never executed: this prevents an attacker from
    # choosing a point, running it once and declaring that value authoritative.
    execute_cases = trusted_hash == TRUSTED_REFERENCE_SPEC_SHA256
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            reasons.append(f"reference_case_invalid:{index}")
            continue
        case_id = str(case.get("case_id") or "")
        case_ids.append(case_id)
        if case_id not in PINNED_REFERENCE_SOURCES:
            reasons.append(f"reference_case_id_not_pinned:{case_id or index}")
        if not _source_record_is_pinned(case_id, case.get("source")):
            reasons.append(f"reference_source_not_pinned:{case_id or index}")
        point = case.get("point")
        if not isinstance(point, Mapping) or not point:
            reasons.append(f"reference_point_mapping_missing:{case_id or index}")
            point = {}
        parameterization = case.get("parameterization")
        if parameterization not in {
            "canonical_sampled",
            "upstream_fixed",
            "upstream_planck_sampled",
        }:
            reasons.append(f"reference_parameterization_invalid:{case_id or index}")
        theory_args = case.get("theory_args")
        expected_theory_args = PINNED_REFERENCE_THEORY_ARGS.get(case_id)
        if expected_theory_args is None:
            if theory_args is not None:
                reasons.append(f"reference_theory_args_unexpected:{case_id or index}")
        elif theory_args != expected_theory_args:
            reasons.append(f"reference_theory_args_not_pinned:{case_id or index}")
        likelihood_names = case.get("likelihoods")
        if not isinstance(likelihood_names, list) or not likelihood_names:
            reasons.append(f"reference_likelihood_subset_missing:{case_id or index}")
            likelihood_names = []
        if len(set(likelihood_names)) != len(likelihood_names) or not set(
            likelihood_names
        ).issubset(REFERENCE_LIKELIHOODS):
            reasons.append(f"reference_likelihood_subset_invalid:{case_id or index}")
        registered_components.extend(str(name) for name in likelihood_names)
        values = case.get("values")
        if not isinstance(values, Mapping):
            reasons.append(f"reference_values_mapping_missing:{case_id or index}")
            values = {}
        if set(values) != set(likelihood_names):
            reasons.append(f"reference_case_value_set_mismatch:{case_id or index}")
        if any(
            isinstance(item, Mapping) and "observed" in item
            for item in values.values()
        ):
            reasons.append("reference_file_must_not_supply_observed_values")
        observed_values: dict[str, float] = {}
        if execute_cases and likelihood_names and point:
            try:
                observed_values = live_evaluator(
                    canonical_config=canonical_config,
                    packages_path=packages_path,
                    point=point,
                    likelihood_names=likelihood_names,
                    parameterization=parameterization,
                    theory_args=theory_args,
                )
            except Exception as exc:
                reasons.append(
                    f"live_reference_evaluation_failed:{case_id}:"
                    f"{type(exc).__name__}:{exc}"
                )
        observed_by_case[case_id] = observed_values
        for name in likelihood_names:
            item = values.get(name)
            if not isinstance(item, Mapping):
                continue
            expected = item.get("expected_chi2")
            observed = observed_values.get(name)
            tolerance = item.get("absolute_tolerance")
            if not all(
                isinstance(value, (int, float)) and math.isfinite(float(value))
                for value in (expected, observed, tolerance)
            ) or float(tolerance) < 0:
                reasons.append(f"reference_value_invalid:{case_id}:{name}")
                continue
            if abs(float(observed) - float(expected)) > float(tolerance):
                reasons.append(f"reference_value_outside_tolerance:{case_id}:{name}")
    if len(set(case_ids)) != len(case_ids):
        reasons.append("reference_case_ids_not_unique")
    missing = sorted(set(REFERENCE_LIKELIHOODS) - set(registered_components))
    extra = sorted(set(registered_components) - set(REFERENCE_LIKELIHOODS))
    duplicates = sorted(
        name for name in set(registered_components) if registered_components.count(name) > 1
    )
    if missing:
        reasons.append("reference_likelihoods_missing:" + ",".join(missing))
    if extra:
        reasons.append("reference_likelihoods_unexpected:" + ",".join(extra))
    if duplicates:
        reasons.append("reference_likelihoods_duplicated:" + ",".join(duplicates))
    likelihood_runtime: dict[str, Any] | None = None
    if execute_cases:
        try:
            likelihood_runtime = assert_loaded_likelihood_runtime(packages_path)
        except Exception as exc:
            reasons.append(
                f"loaded_likelihood_runtime_invalid:{type(exc).__name__}:{exc}"
            )
    return {
        "passed": not reasons,
        "reasons": reasons,
        "path": str(path.resolve()),
        "sha256": trusted_hash,
        "configuration_sha256": config_sha256,
        "data_fingerprint": data_fingerprint,
        "payload": payload,
        "live_observed_chi2_by_case": observed_by_case,
        "loaded_likelihood_runtime": likelihood_runtime,
    }


def build_preflight_report(
    *,
    canonical_config_path: str | Path,
    packages_path: str | Path,
    dependency_lock_path: str | Path,
    wheels_path: str | Path,
    reference_values_path: str | Path,
    data_manifest_path: str | Path = TRUSTED_DATA_MANIFEST_PATH,
) -> dict[str, Any]:
    canonical = _load_yaml(canonical_config_path)
    config_reasons = validate_canonical_config(canonical)
    canonical_config_sha256 = _hash_file(canonical_config_path)
    if canonical_config_sha256 != TRUSTED_CANONICAL_CONFIG_SHA256:
        config_reasons.append("canonical_config_bytes_do_not_match_frozen_profile")
    amendment = protocol_amendment_record()
    inventory = build_data_inventory(packages_path)
    adequacy_inventory = build_adequacy_data_inventory(packages_path)
    trusted_data = verify_trusted_data_manifest(
        data_manifest_path,
        inventory=inventory,
        adequacy_inventory=adequacy_inventory,
    )
    source_state = build_source_state_inventory()
    environment = exact_environment_inventory(dependency_lock_path, wheels_path)
    likelihood_code = verify_likelihood_code_manifest()
    reference = verify_reference_values(
        reference_values_path,
        canonical_config=canonical,
        packages_path=packages_path,
        config_sha256=canonical_config_sha256,
        data_fingerprint=str(inventory.get("fingerprint") or ""),
    )
    failures = [
        *[f"config:{reason}" for reason in config_reasons],
        *(
            []
            if inventory.get("complete") is True
            else ["data:exact_data_inventory_incomplete"]
        ),
        *(
            []
            if adequacy_inventory.get("complete") is True
            else ["adequacy_data:planck_npipe_camspec_inventory_incomplete"]
        ),
        *[f"source_state:{reason}" for reason in source_state["reasons"]],
        *[f"environment:{reason}" for reason in environment["reasons"]],
        *[f"likelihood_code:{reason}" for reason in likelihood_code["reasons"]],
        *[f"reference:{reason}" for reason in reference["reasons"]],
        *[f"trusted_data:{reason}" for reason in trusted_data["reasons"]],
        *([] if amendment["valid"] else ["protocol:amendment_hash_invalid"]),
    ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "w0wa_exact_preflight",
        "created_at": _utc_now(),
        "profile_id": EXACT_PROFILE_ID,
        "paper": EXACT_PAPER,
        "claim_scope": EXACT_CLAIM_SCOPE,
        "target_commitment": PREREGISTERED_TARGET_COMMITMENT,
        "protocol_integrity": dict(PROTOCOL_INTEGRITY),
        "paper_fidelity_amendment": dict(PAPER_FIDELITY_AMENDMENT),
        "protocol_amendment_artifact": amendment,
        "passed": not failures,
        "status": "PASS" if not failures else "WITHHELD",
        "failures": failures,
        "configuration": {
            "path": str(Path(canonical_config_path).resolve()),
            "sha256": canonical_config_sha256,
            "fingerprint": _config_fingerprint(canonical),
        },
        "data": inventory,
        "adequacy_data": adequacy_inventory,
        "trusted_data_manifest": trusted_data,
        "source_state": source_state,
        "environment": environment,
        "likelihood_code_manifest": likelihood_code,
        "reference_likelihood_values": reference,
    }
    return _with_self_hash(report, "preflight_sha256")


def verify_preflight_receipt(
    path: str | Path,
    *,
    canonical_config_path: str | Path,
    packages_path: str | Path,
) -> dict[str, Any]:
    receipt_path = Path(path)
    if not receipt_path.is_file():
        return {"passed": False, "reasons": ["preflight_receipt_missing"]}
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "passed": False,
            "reasons": [f"preflight_receipt_unreadable:{type(exc).__name__}"],
        }
    reasons: list[str] = []
    if payload.get("artifact_type") != "w0wa_exact_preflight":
        reasons.append("preflight_artifact_type_mismatch")
    if payload.get("profile_id") != EXACT_PROFILE_ID:
        reasons.append("preflight_profile_mismatch")
    if payload.get("target_commitment") != PREREGISTERED_TARGET_COMMITMENT:
        reasons.append("preflight_target_commitment_mismatch")
    if payload.get("protocol_integrity") != PROTOCOL_INTEGRITY:
        reasons.append("preflight_protocol_integrity_mismatch")
    if payload.get("paper_fidelity_amendment") != PAPER_FIDELITY_AMENDMENT:
        reasons.append("preflight_paper_fidelity_amendment_mismatch")
    current_amendment = protocol_amendment_record()
    if current_amendment.get("valid") is not True:
        reasons.append("preflight_protocol_amendment_invalid")
    if payload.get("protocol_amendment_artifact") != current_amendment:
        reasons.append("preflight_protocol_amendment_drift")
    if payload.get("passed") is not True or payload.get("status") != "PASS":
        reasons.append("preflight_not_passed")
    if not _verify_self_hash(payload, "preflight_sha256"):
        reasons.append("preflight_self_hash_invalid")
    if (payload.get("configuration") or {}).get("sha256") != _hash_file(
        canonical_config_path
    ):
        reasons.append("preflight_config_hash_drift")
    if _hash_file(canonical_config_path) != TRUSTED_CANONICAL_CONFIG_SHA256:
        reasons.append("preflight_frozen_config_hash_invalid")
    current_data = build_data_inventory(packages_path)
    if (payload.get("data") or {}).get("fingerprint") != current_data.get(
        "fingerprint"
    ):
        reasons.append("preflight_data_fingerprint_drift")
    current_adequacy_data = build_adequacy_data_inventory(packages_path)
    if current_adequacy_data.get("complete") is not True:
        reasons.append("preflight_planck_npipe_camspec_data_incomplete")
    if (payload.get("adequacy_data") or {}).get(
        "fingerprint"
    ) != current_adequacy_data.get("fingerprint"):
        reasons.append("preflight_adequacy_data_fingerprint_drift")
    current_source_state = build_source_state_inventory()
    if current_source_state.get("passed") is not True:
        reasons.extend(
            f"preflight_source_state_{reason}"
            for reason in current_source_state.get("reasons") or []
        )
    if (payload.get("source_state") or {}).get(
        "fingerprint"
    ) != current_source_state.get("fingerprint"):
        reasons.append("preflight_source_state_fingerprint_drift")
    trusted_data_record = payload.get("trusted_data_manifest") or {}
    trusted_data_path = trusted_data_record.get("path")
    if not isinstance(trusted_data_path, str):
        reasons.append("preflight_trusted_data_manifest_binding_missing")
    else:
        current_trusted_data = verify_trusted_data_manifest(
            trusted_data_path,
            inventory=current_data,
            adequacy_inventory=current_adequacy_data,
            archive_root=trusted_data_record.get("archive_root")
            or TRUSTED_SOURCE_ARCHIVE_ROOT,
        )
        if current_trusted_data.get("passed") is not True:
            reasons.extend(
                f"preflight_{reason}"
                for reason in current_trusted_data.get("reasons") or []
            )
        if trusted_data_record != current_trusted_data:
            reasons.append("preflight_trusted_data_manifest_drift")
    recorded_environment = payload.get("environment") or {}
    lock_path = ((recorded_environment.get("lock") or {}).get("path"))
    wheels_path = recorded_environment.get("wheels_path")
    if not isinstance(lock_path, str) or not isinstance(wheels_path, str):
        reasons.append("preflight_environment_binding_missing")
    else:
        current_environment = exact_environment_inventory(lock_path, wheels_path)
        if current_environment.get("passed") is not True:
            reasons.extend(
                f"preflight_environment_{reason}"
                for reason in current_environment.get("reasons") or []
            )
        if recorded_environment.get("fingerprint") != current_environment.get(
            "fingerprint"
        ):
            reasons.append("preflight_environment_fingerprint_drift")
    recorded_likelihood_code = payload.get("likelihood_code_manifest") or {}
    code_manifest_path = recorded_likelihood_code.get("path")
    if not isinstance(code_manifest_path, str):
        reasons.append("preflight_likelihood_code_manifest_binding_missing")
    else:
        current_likelihood_code = verify_likelihood_code_manifest(code_manifest_path)
        if current_likelihood_code.get("passed") is not True:
            reasons.extend(
                f"preflight_{reason}"
                for reason in current_likelihood_code.get("reasons") or []
            )
        if recorded_likelihood_code != current_likelihood_code:
            reasons.append("preflight_likelihood_code_manifest_drift")
    recorded_reference = payload.get("reference_likelihood_values") or {}
    reference_path = recorded_reference.get("path")
    if not isinstance(reference_path, str):
        reasons.append("preflight_reference_likelihood_binding_missing")
    else:
        try:
            current_reference = verify_reference_values(
                reference_path,
                canonical_config=_load_yaml(canonical_config_path),
                packages_path=packages_path,
                config_sha256=_hash_file(canonical_config_path),
                data_fingerprint=str(current_data.get("fingerprint") or ""),
            )
        except Exception as exc:
            reasons.append(
                "preflight_reference_likelihood_reverification_failed:"
                f"{type(exc).__name__}:{exc}"
            )
        else:
            if current_reference.get("passed") is not True:
                reasons.extend(
                    f"preflight_reference_{reason}"
                    for reason in current_reference.get("reasons") or []
                )
            if recorded_reference != current_reference:
                reasons.append("preflight_reference_likelihood_values_drift")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "path": str(receipt_path.resolve()),
        "sha256": _hash_file(receipt_path),
        "payload": payload,
    }


def build_generation_receipt(
    *,
    canonical_config_path: str | Path,
    free_output_path: str | Path,
    fixed_output_path: str | Path,
    preflight_report_path: str | Path,
    packages_path: str | Path,
    adequacy_output_dir: str | Path | None = None,
) -> dict[str, Any]:
    preflight = verify_preflight_receipt(
        preflight_report_path,
        canonical_config_path=canonical_config_path,
        packages_path=packages_path,
    )
    if not preflight["passed"]:
        raise ValueError("Preflight receipt is not valid: " + "; ".join(preflight["reasons"]))
    generated = write_map_configs(
        canonical_config_path,
        free_output_path,
        fixed_output_path,
    )
    adequacy_plan = write_model_adequacy_plan(
        _load_yaml(canonical_config_path),
        adequacy_output_dir
        or (Path(free_output_path).resolve().parent / "adequacy-configs"),
        packages_path,
    )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "w0wa_exact_generation",
        "created_at": _utc_now(),
        "profile_id": EXACT_PROFILE_ID,
        "claim_scope": EXACT_CLAIM_SCOPE,
        "target_commitment": PREREGISTERED_TARGET_COMMITMENT,
        "protocol_integrity": dict(PROTOCOL_INTEGRITY),
        "paper_fidelity_amendment": dict(PAPER_FIDELITY_AMENDMENT),
        "protocol_amendment_artifact": protocol_amendment_record(),
        "passed": True,
        "preflight": {
            "path": preflight["path"],
            "sha256": preflight["sha256"],
            "preflight_sha256": preflight["payload"]["preflight_sha256"],
        },
        "configuration": generated,
        "model_adequacy_plan": adequacy_plan,
    }
    return _with_self_hash(receipt, "generation_sha256")


def verify_generation_receipt(
    path: str | Path,
    *,
    canonical_config_path: str | Path,
    preflight_report_path: str | Path,
    packages_path: str | Path,
) -> dict[str, Any]:
    receipt_path = Path(path)
    if not receipt_path.is_file():
        return {"passed": False, "reasons": ["generation_receipt_missing"]}
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "passed": False,
            "reasons": [f"generation_receipt_unreadable:{type(exc).__name__}"],
        }
    reasons: list[str] = []
    if payload.get("artifact_type") != "w0wa_exact_generation":
        reasons.append("generation_artifact_type_mismatch")
    if payload.get("profile_id") != EXACT_PROFILE_ID:
        reasons.append("generation_profile_mismatch")
    if payload.get("target_commitment") != PREREGISTERED_TARGET_COMMITMENT:
        reasons.append("generation_target_commitment_mismatch")
    if payload.get("protocol_integrity") != PROTOCOL_INTEGRITY:
        reasons.append("generation_protocol_integrity_mismatch")
    if payload.get("paper_fidelity_amendment") != PAPER_FIDELITY_AMENDMENT:
        reasons.append("generation_paper_fidelity_amendment_mismatch")
    if payload.get("protocol_amendment_artifact") != protocol_amendment_record():
        reasons.append("generation_protocol_amendment_drift")
    if payload.get("passed") is not True:
        reasons.append("generation_not_passed")
    if not _verify_self_hash(payload, "generation_sha256"):
        reasons.append("generation_self_hash_invalid")
    preflight = verify_preflight_receipt(
        preflight_report_path,
        canonical_config_path=canonical_config_path,
        packages_path=packages_path,
    )
    if not preflight["passed"]:
        reasons.extend(f"generation_{reason}" for reason in preflight["reasons"])
    elif (payload.get("preflight") or {}).get("sha256") != preflight["sha256"]:
        reasons.append("generation_preflight_receipt_hash_mismatch")
    config = payload.get("configuration") or {}
    if config.get("canonical") != str(canonical_config_path):
        reasons.append("generation_canonical_path_mismatch")
    for key, field in (("free_map", "free_sha256"), ("fixed_map", "fixed_sha256")):
        generated_path = config.get(key)
        if not isinstance(generated_path, str) or not Path(generated_path).is_file():
            reasons.append(f"generation_{key}_missing")
        elif config.get(field) != _hash_file(generated_path):
            reasons.append(f"generation_{key}_hash_drift")
    adequacy_plan = payload.get("model_adequacy_plan") or {}
    plan_path = Path(str(adequacy_plan.get("path") or ""))
    if not plan_path.is_file():
        reasons.append("generation_adequacy_plan_missing")
    elif adequacy_plan.get("sha256") != _hash_file(plan_path):
        reasons.append("generation_adequacy_plan_hash_drift")
    else:
        try:
            plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reasons.append(f"generation_adequacy_plan_unreadable:{type(exc).__name__}")
        else:
            if not _verify_self_hash(plan_payload, "plan_sha256"):
                reasons.append("generation_adequacy_plan_self_hash_invalid")
            if plan_payload.get("target_commitment") != PREREGISTERED_TARGET_COMMITMENT:
                reasons.append("generation_adequacy_plan_target_mismatch")
            if plan_payload.get("protocol_integrity") != PROTOCOL_INTEGRITY:
                reasons.append("generation_adequacy_plan_protocol_mismatch")
            if plan_payload.get("paper_fidelity_amendment") != PAPER_FIDELITY_AMENDMENT:
                reasons.append("generation_adequacy_plan_ess_amendment_mismatch")
            if plan_payload.get("protocol_amendment_artifact") != protocol_amendment_record():
                reasons.append("generation_adequacy_plan_amendment_drift")
            for name, artifact in (plan_payload.get("configs") or {}).items():
                path = Path(str((artifact or {}).get("path") or ""))
                if not path.is_file() or (artifact or {}).get("sha256") != _hash_file(
                    path
                ):
                    reasons.append(f"generation_adequacy_config_drift:{name}")
            for field in ("predictive_checks", "injection_recovery"):
                artifact = plan_payload.get(field) or {}
                path = Path(str(artifact.get("path") or ""))
                if (
                    not path.is_file()
                    or artifact.get("sha256") != _hash_file(path)
                    or artifact.get("size_bytes") != path.stat().st_size
                ):
                    reasons.append(f"generation_adequacy_input_drift:{field}")
            covariance = plan_payload.get("pantheon_covariance_variant") or {}
            if covariance.get("variant") != "official_statistical_only" or (
                covariance.get("construction")
                != "official Pantheon+SH0ES_STATONLY.cov copied unmodified"
            ):
                reasons.append("generation_pantheon_covariance_variant_invalid")
            for field in (
                "source_data",
                "source_covariance",
                "generated_covariance",
                "generated_dataset",
            ):
                artifact = covariance.get(field) or {}
                path = Path(str(artifact.get("path") or ""))
                if (
                    not path.is_file()
                    or artifact.get("sha256") != _hash_file(path)
                    or artifact.get("size_bytes") != path.stat().st_size
                ):
                    reasons.append(f"generation_pantheon_variant_drift:{field}")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "path": str(receipt_path.resolve()),
        "sha256": _hash_file(receipt_path),
        "payload": payload,
    }


def _native_runtime_fingerprint_identity(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    normalized = copy.deepcopy(dict(value))
    binaries = normalized.get("binaries")
    if isinstance(binaries, Mapping):
        normalized["binaries"] = {
            str(name): {
                key: item for key, item in dict(record).items() if key != "path"
            }
            for name, record in binaries.items()
            if isinstance(record, Mapping)
        }
    return normalized


def _environment_fingerprint(environment: Mapping[str, Any]) -> str:
    return _hash_object(
        {
            "python": environment.get("python"),
            "platform": environment.get("platform"),
            "machine": environment.get("machine"),
            "packages": environment.get("packages"),
            "runtime_modules": environment.get("runtime_modules"),
            "thread_environment": environment.get("thread_environment"),
            "native_runtime": _native_runtime_fingerprint_identity(
                environment.get("native_runtime")
            ),
            "import_policy": environment.get("import_policy"),
            "runtime_closure": environment.get("runtime_closure"),
        }
    )


def _run_artifact_paths(kind: str, prefix: str | Path) -> list[Path]:
    if kind == "chain":
        return [
            *[_prefix_file(prefix, f".{index}.txt") for index in range(1, 5)],
            _prefix_file(prefix, ".input.yaml"),
            _prefix_file(prefix, ".updated.yaml"),
        ]
    if kind == "map":
        return [
            _prefix_file(prefix, ".minimum.txt"),
            # Cobaya adds the "minimize" infix to info files, while the
            # OnePoint collection itself remains <prefix>.minimum.txt.
            _prefix_file(prefix, ".minimize.input.yaml"),
            _prefix_file(prefix, ".minimize.updated.yaml"),
        ]
    raise ValueError(f"Unsupported run kind: {kind}")


def _chain_reservation_path(prefix: str | Path) -> Path:
    return _prefix_file(prefix, ".reservation.lock")


def _reserve_chain_prefix(prefix: str | Path) -> Path:
    """Atomically reserve a never-reused chain prefix.

    The lock is an immutable run artifact and is intentionally never removed,
    including after a failed process launch.  ``O_EXCL`` is the authority; the
    earlier glob check only provides a more informative error for old outputs.
    """

    reservation = _chain_reservation_path(prefix)
    reservation.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        reservation,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        os.write(descriptor, b"standard-astro-w0wa-exclusive-prefix-v1\n")
    finally:
        os.close(descriptor)
    return reservation


def _regular_executable_record(
    path: str | Path,
    *,
    invoked_path: str | Path,
) -> dict[str, Any]:
    """Return a byte/stat identity for an already resolved executable.

    The returned ``resolved_path`` is the path that must be passed to
    ``subprocess``.  A PATH token or symlink is deliberately not accepted here:
    resolving one identity and later executing another was a runner-identity
    TOCTOU gap in the original evidence launcher.
    """

    candidate = Path(path)
    if not candidate.is_absolute():
        raise RuntimeError(f"runner executable is not absolute: {candidate}")
    if candidate.is_symlink():
        raise RuntimeError(f"runner executable must not be a symlink: {candidate}")
    resolved = candidate.resolve(strict=True)
    if resolved != candidate:
        raise RuntimeError(f"runner executable path is not canonical: {candidate}")
    metadata = resolved.stat()
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        raise RuntimeError(f"runner executable is not a regular executable: {resolved}")
    return {
        "invoked_path": str(invoked_path),
        "resolved_path": str(resolved),
        "size_bytes": metadata.st_size,
        "sha256": _hash_file(resolved),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": stat.S_IMODE(metadata.st_mode),
        "mtime_ns": metadata.st_mtime_ns,
    }


def _materialize_trusted_python_launcher(source: Path) -> Path:
    """Create a content-addressed, non-symlink Python launcher in the venv.

    Typical virtual environments expose ``bin/python`` as a mutable symlink.
    Executing its resolved Homebrew target would lose ``pyvenv.cfg`` discovery,
    while executing the symlink would re-introduce a resolve/exec race.  A
    byte-identical regular file under the venv ``bin`` directory preserves venv
    discovery and gives the launch receipt one stable path and inode to bind.
    """

    source = source.resolve(strict=True)
    source_digest = _hash_file(source)
    trusted_digest = TRUSTED_NATIVE_RUNTIME_SHA256.get("python")
    if source_digest != trusted_digest:
        raise RuntimeError("Python launcher bytes do not match the frozen runtime")
    launcher_dir = Path(sys.prefix).resolve() / "bin"
    if not launcher_dir.is_dir():
        raise RuntimeError("virtual-environment bin directory is missing")
    destination = launcher_dir / (
        f".w0wa-python-launch-{source_digest.removeprefix('sha256:')}"
    )

    def validate_existing() -> Path:
        metadata = destination.lstat()
        if not stat.S_ISREG(metadata.st_mode) or destination.is_symlink():
            raise RuntimeError("content-addressed Python launcher is not regular")
        if stat.S_IMODE(metadata.st_mode) & 0o222:
            raise RuntimeError("content-addressed Python launcher is writable")
        if metadata.st_size != source.stat().st_size or _hash_file(destination) != (
            source_digest
        ):
            raise RuntimeError("content-addressed Python launcher drifted")
        return destination.resolve(strict=True)

    if destination.exists() or destination.is_symlink():
        return validate_existing()

    temporary = destination.with_name(
        f"{destination.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as target:
            shutil.copyfileobj(source_handle, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(0o555)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            # Another process materialized the same content-addressed launcher.
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return validate_existing()


def _trusted_cobaya_child_identity() -> dict[str, Any]:
    """Describe the exact absolute executables and Cobaya module to launch."""

    invoked_executable = Path(sys.executable).absolute()
    source_executable = invoked_executable.resolve(strict=True)
    executable = _materialize_trusted_python_launcher(source_executable)
    mpirun_invoked = shutil.which("mpirun")
    if not mpirun_invoked:
        raise RuntimeError("mpirun cannot be resolved")
    mpirun = Path(mpirun_invoked).resolve(strict=True)
    executable_record = _regular_executable_record(
        executable,
        invoked_path=invoked_executable,
    )
    executable_record["source_resolved_path"] = str(source_executable)
    executable_record["source_sha256"] = _hash_file(source_executable)
    mpirun_record = _regular_executable_record(
        mpirun,
        invoked_path=mpirun_invoked,
    )
    if mpirun_record["sha256"] != TRUSTED_NATIVE_RUNTIME_SHA256.get("mpirun"):
        raise RuntimeError("mpirun bytes do not match the frozen runtime")
    distribution = importlib.metadata.distribution("cobaya")
    distribution_root = Path(distribution.locate_file("")).resolve(strict=True)
    module_spec = importlib.util.find_spec("cobaya.run")
    if module_spec is None or not module_spec.origin:
        raise RuntimeError("cobaya.run module cannot be resolved")
    module_path = Path(module_spec.origin).resolve(strict=True)
    if not module_path.is_relative_to(distribution_root):
        raise RuntimeError("cobaya.run resolves outside the installed distribution")
    if distribution.version != REQUIRED_PACKAGE_VERSIONS["cobaya"]:
        raise RuntimeError(
            "cobaya.run distribution version is not the preregistered version"
        )
    return {
        "schema_version": 1,
        "invocation": "current_interpreter_module",
        "in_virtual_environment": sys.prefix != sys.base_prefix,
        "virtual_environment_prefix": str(Path(sys.prefix).resolve()),
        "import_policy": _exact_python_import_policy(),
        "executable": executable_record,
        "mpirun": mpirun_record,
        "module": {
            "name": "cobaya.run",
            "path": str(module_path),
            "size_bytes": module_path.stat().st_size,
            "sha256": _hash_file(module_path),
        },
        "distribution": {
            "name": canonicalize_name(
                distribution.metadata.get("Name") or "cobaya"
            ),
            "version": distribution.version,
            "root": str(distribution_root),
            "fingerprint": _distribution_inventory("cobaya").get("fingerprint"),
        },
    }


def _attestation_path(prefix: str | Path) -> Path:
    return _prefix_file(prefix, ".run.json")


def _artifact_records(paths: Sequence[Path]) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "sha256": _hash_file(path),
        }
        for path in paths
        if path.is_file()
    ]


def _launcher_nonce_commitment(nonce: str) -> str:
    return _hash_object({"domain": LAUNCH_NONCE_DOMAIN, "nonce": nonce})


def _build_launch_context(
    *,
    nonce_commitment: str,
    kind: str,
    evidence_class: str,
    run_id: str | None,
    prefix: str | Path,
    command: Sequence[str],
    config_path: str | Path,
    source_config_path: str | Path,
    data_fingerprint: str,
    runner_identity: Mapping[str, Any],
    reservation_path: Path | None,
    started_at: str,
) -> dict[str, Any]:
    context = {
        "schema_version": 1,
        "domain": LAUNCH_NONCE_DOMAIN,
        "nonce_commitment": nonce_commitment,
        "launcher_pid": os.getpid(),
        "profile_id": EXACT_PROFILE_ID,
        "kind": kind,
        "evidence_class": evidence_class,
        "run_id": run_id,
        "prefix": str(Path(prefix).resolve()),
        "command": list(command),
        "command_sha256": _hash_object(list(command)),
        "config_path": str(Path(config_path).resolve()),
        "config_sha256": _hash_file(config_path),
        "source_config_path": str(Path(source_config_path).resolve()),
        "source_config_sha256": _hash_file(source_config_path),
        "data_fingerprint": data_fingerprint,
        "runner_identity_sha256": _hash_object(dict(runner_identity)),
        "host_execution_trust_boundary": copy.deepcopy(
            EXACT_HOST_EXECUTION_TRUST_BOUNDARY
        ),
        "signing_material_withheld_from_child": True,
        "reservation": (
            _artifact_records([reservation_path])[0]
            if reservation_path is not None and reservation_path.is_file()
            else None
        ),
        "started_at": started_at,
    }
    return _with_self_hash(context, "launch_context_sha256")


def _completion_binding_from_payload(
    payload: Mapping[str, Any],
    *,
    prefix: str | Path,
) -> dict[str, Any]:
    """Select every completion fact protected by the launcher HMAC."""

    command = list(payload.get("command") or [])
    runner_identity = dict(payload.get("runner_identity") or {})
    likelihood_runtime = dict(payload.get("likelihood_runtime") or {})
    return {
        "completion_schema_version": 1,
        "profile_id": payload.get("profile_id"),
        "claim_scope": payload.get("claim_scope"),
        "kind": payload.get("kind"),
        "evidence_class": payload.get("evidence_class"),
        "run_id": payload.get("run_id"),
        "prefix": str(Path(prefix).resolve()),
        "launch_context_sha256": (
            (payload.get("launch_context") or {}).get("launch_context_sha256")
        ),
        "started_at": payload.get("started_at"),
        "completed_at": payload.get("completed_at"),
        "command": command,
        "command_sha256": _hash_object(command),
        "config_path": payload.get("config_path"),
        "config_sha256": payload.get("config_sha256"),
        "source_config_path": payload.get("source_config_path"),
        "source_config_sha256": payload.get("source_config_sha256"),
        "data_fingerprint": payload.get("data_fingerprint"),
        "environment_fingerprint": payload.get("environment_fingerprint"),
        "runner_identity_sha256": _hash_object(runner_identity),
        "likelihood_runtime_sha256": _hash_object(likelihood_runtime),
        "returncode": payload.get("returncode"),
        "resource_binding": payload.get("resource_binding"),
        "seed_binding": payload.get("seed_binding"),
        "termination": payload.get("termination"),
        "artifacts": payload.get("artifacts"),
        "missing_artifacts": payload.get("missing_artifacts"),
        "runner_log": payload.get("runner_log"),
        "host_execution_trust_boundary": payload.get(
            "host_execution_trust_boundary"
        ),
    }


def _validate_launcher_completion_receipt(
    receipt: Any,
    *,
    expected_binding: Mapping[str, Any],
    launch_context: Any,
) -> dict[str, Any]:
    reasons: list[str] = []
    if not isinstance(launch_context, Mapping) or not launch_context:
        reasons.append("formal_launch_context_missing")
    elif not _verify_self_hash(launch_context, "launch_context_sha256"):
        reasons.append("formal_launch_context_self_hash_invalid")
    elif (
        launch_context.get("host_execution_trust_boundary")
        != EXACT_HOST_EXECUTION_TRUST_BOUNDARY
    ):
        reasons.append("formal_launch_context_trust_boundary_mismatch")
    if not isinstance(receipt, Mapping) or not receipt:
        reasons.append("formal_launcher_completion_receipt_missing")
        return {"passed": False, "reasons": reasons}
    key_id = receipt.get("key_id")
    key_binding = receipt.get("evidence_signing_key_binding")
    if (
        not isinstance(key_binding, Mapping)
        or set(key_binding) != {"available", "key_id", "sha256"}
        or key_binding.get("available") is not True
        or key_binding.get("key_id") != key_id
    ):
        reasons.append("formal_launcher_completion_key_binding_malformed")
    if key_id != EXACT_EVIDENCE_SIGNING_KEY_ID:
        reasons.append("formal_launcher_completion_key_id_mismatch")
    if not isinstance(key_binding, Mapping) or key_binding.get(
        "sha256"
    ) != EXACT_EVIDENCE_SIGNING_KEY_SHA256:
        reasons.append("formal_launcher_completion_key_fingerprint_mismatch")
    try:
        verification_key = verification_key_for_id(key_id)
    except ValueError:
        verification_key = None
    if key_id == "dev-ephemeral":
        reasons.append("formal_launcher_completion_ephemeral_key_forbidden")
    if verification_key is None:
        reasons.append("formal_launcher_completion_verification_key_unavailable")
    elif len(verification_key.encode("utf-8")) < 32:
        reasons.append("formal_launcher_completion_verification_key_too_short")
    elif (
        "sha256:"
        + hashlib.sha256(verification_key.encode("utf-8")).hexdigest()
        != EXACT_EVIDENCE_SIGNING_KEY_SHA256
    ):
        reasons.append("formal_launcher_completion_verification_key_mismatch")
    try:
        signature_valid = verify_scientific_attestation(
            dict(receipt),
            expected_type=LAUNCHER_COMPLETION_ATTESTATION_TYPE,
        )
    except ValueError:
        signature_valid = False
    if not signature_valid:
        reasons.append("formal_launcher_completion_signature_invalid")
    nonce = receipt.get("launcher_nonce")
    if not isinstance(nonce, str) or len(nonce) < 64:
        reasons.append("formal_launcher_nonce_missing")
    elif isinstance(launch_context, Mapping) and launch_context.get(
        "nonce_commitment"
    ) != _launcher_nonce_commitment(nonce):
        reasons.append("formal_launcher_nonce_commitment_mismatch")
    metadata_fields = {
        "schema_version",
        "attestation_source",
        "attestation_type",
        "key_id",
        "manifest_hash",
        "signature",
        "signature_verified",
        "launcher_nonce",
        "evidence_signing_key_binding",
    }
    observed_binding = {
        key: value for key, value in receipt.items() if key not in metadata_fields
    }
    if observed_binding != dict(expected_binding):
        reasons.append("formal_launcher_completion_binding_mismatch")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "key_id": receipt.get("key_id"),
        "manifest_hash": receipt.get("manifest_hash"),
    }


def _chain_termination_record(prefix: str | Path) -> dict[str, Any]:
    checkpoint_path = _prefix_file(prefix, ".checkpoint")
    log_path = _prefix_file(prefix, ".runner.log")
    reasons: list[str] = []
    checkpoint: Mapping[str, Any] = {}
    if not checkpoint_path.is_file():
        reasons.append("mcmc_checkpoint_missing")
    else:
        try:
            checkpoint = _load_yaml(checkpoint_path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            reasons.append(f"mcmc_checkpoint_unreadable:{type(exc).__name__}")
    mcmc = ((checkpoint.get("sampler") or {}).get("mcmc") or {})
    if mcmc.get("converged") is not True:
        reasons.append("mcmc_checkpoint_not_converged")
    log_text = ""
    if not log_path.is_file():
        reasons.append("runner_log_missing")
    else:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    max_samples_reached = "Reached maximum number of accepted steps" in log_text
    convergence_message_seen = "The run has converged!" in log_text
    sampling_complete_seen = "Sampling complete after" in log_text
    if max_samples_reached:
        reasons.append("mcmc_stopped_at_max_samples")
    if not convergence_message_seen:
        reasons.append("mcmc_convergence_message_missing")
    if not sampling_complete_seen:
        reasons.append("mcmc_sampling_completion_message_missing")
    return {
        "status": "converged" if not reasons else "withheld",
        "passed": not reasons,
        "reasons": reasons,
        "max_samples_reached": max_samples_reached,
        "early_stop": bool(reasons),
        "convergence_message_seen": convergence_message_seen,
        "sampling_complete_seen": sampling_complete_seen,
        "checkpoint": (
            {
                "path": str(checkpoint_path.resolve()),
                "size_bytes": checkpoint_path.stat().st_size,
                "sha256": _hash_file(checkpoint_path),
                "converged": mcmc.get("converged"),
                "Rminus1_last": mcmc.get("Rminus1_last"),
                "mpi_size": mcmc.get("mpi_size"),
            }
            if checkpoint_path.is_file()
            else None
        ),
    }


def _chain_seed_binding(config_path: str | Path) -> dict[str, Any] | None:
    try:
        seed_entropy = (
            ((_load_yaml(config_path).get("sampler") or {}).get("mcmc") or {}).get(
                "seed"
            )
            or []
        )
        identities = cobaya_mpi_seed_identities(seed_entropy)
        return {
            "algorithm": "numpy.SeedSequence(entropy).spawn(mpi_size)",
            "entropy": list(seed_entropy),
            "identities": identities,
        }
    except (OSError, ValueError, yaml.YAMLError):
        return None


def write_completed_attestation(
    *,
    kind: str,
    config_path: str | Path,
    prefix: str | Path,
    data_inventory: Mapping[str, Any],
    returncode: int = 0,
    command: Sequence[str] | None = None,
    require_chain_convergence: bool = False,
    mpi_processes: int = REQUIRED_CHAIN_COUNT,
    threads_per_process: int = 3,
    runner_identity: Mapping[str, Any] | None = None,
    evidence_class: str = "synthetic_fixture",
    run_id: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    source_config_path: str | Path | None = None,
    workflow_receipts: Mapping[str, Any] | None = None,
    launch_context: Mapping[str, Any] | None = None,
    launcher_completion_receipt: Mapping[str, Any] | None = None,
    environment_record: Mapping[str, Any] | None = None,
    likelihood_runtime_record: Mapping[str, Any] | None = None,
    termination_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a completion certificate after a successful external run.

    Synthetic and historical fixtures may call this helper directly, but a
    formal/model-adequacy success additionally requires the HMAC-authenticated
    trusted-launcher receipt emitted by ``run_cobaya_with_attestation`` after
    its real subprocess returns. Public artifacts plus this public helper are
    therefore insufficient to synthesize a converged completion proof offline.
    """

    paths = _run_artifact_paths(kind, prefix)
    reservation_path = _chain_reservation_path(prefix)
    attested_paths = [
        *paths,
        *([reservation_path] if kind == "chain" and reservation_path.is_file() else []),
    ]
    missing = [str(path) for path in paths if not path.is_file()]
    termination = (
        dict(termination_record)
        if termination_record is not None
        else _chain_termination_record(prefix)
        if kind == "chain" and require_chain_convergence
        else None
    )
    candidate_success = (
        returncode == 0
        and not missing
        and data_inventory.get("complete") is True
        and (termination is None or termination.get("passed") is True)
    )
    environment = dict(environment_record or environment_manifest())
    likelihood_runtime = dict(
        likelihood_runtime_record
        or likelihood_runtime_inventory(
            str(data_inventory.get("packages_path") or "")
        )
    )
    seed_binding = _chain_seed_binding(config_path) if kind == "chain" else None
    source_path = Path(source_config_path or config_path)
    runner_log_path = _prefix_file(prefix, ".runner.log")
    runner_log = (
        _artifact_records([runner_log_path])[0]
        if runner_log_path.is_file()
        else None
    )
    completion_time = completed_at or _utc_now()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "status": "completed" if candidate_success else "failed",
        "success": candidate_success,
        "returncode": int(returncode),
        "started_at": started_at,
        "completed_at": completion_time,
        "command": list(command or []),
        "config_path": str(Path(config_path).resolve()),
        "config_sha256": _hash_file(config_path),
        "source_config_path": str(source_path.resolve()),
        "source_config_sha256": _hash_file(source_path),
        "profile_id": (
            EXACT_PROFILE_ID if evidence_class != "synthetic_fixture" else None
        ),
        "run_id": run_id,
        "claim_scope": (
            EXACT_CLAIM_SCOPE if evidence_class != "synthetic_fixture" else None
        ),
        "evidence_class": evidence_class,
        "citable": False,
        "workflow_receipts": dict(workflow_receipts or {}),
        "target_commitment": PREREGISTERED_TARGET_COMMITMENT,
        "protocol_integrity": dict(PROTOCOL_INTEGRITY),
        "paper_fidelity_amendment": dict(PAPER_FIDELITY_AMENDMENT),
        "protocol_amendment_artifact": protocol_amendment_record(),
        "protocol_status": dict(RESEARCH_ALPHA_PROTOCOL_STATUS),
        "protocol_amendment_sha256": TRUSTED_PROTOCOL_AMENDMENT_SHA256,
        "host_execution_trust_boundary": copy.deepcopy(
            EXACT_HOST_EXECUTION_TRUST_BOUNDARY
        ),
        "resource_binding": {
            "mpi_processes": mpi_processes,
            "threads_per_process": threads_per_process,
        },
        "data_fingerprint": data_inventory.get("fingerprint"),
        "environment": environment,
        "environment_fingerprint": _environment_fingerprint(environment),
        "runner_identity": dict(runner_identity or {}),
        "likelihood_runtime": likelihood_runtime,
        "seed_binding": seed_binding,
        "termination": termination,
        "artifacts": _artifact_records(attested_paths),
        "missing_artifacts": missing,
        "runner_log": runner_log,
        "launch_context": dict(launch_context or {}),
        "launcher_completion_receipt": dict(launcher_completion_receipt or {}),
    }
    completion_validation = {"passed": True, "reasons": []}
    if evidence_class in CONVERGED_EVIDENCE_CLASSES and candidate_success:
        completion_validation = _validate_launcher_completion_receipt(
            payload.get("launcher_completion_receipt"),
            expected_binding=_completion_binding_from_payload(
                payload,
                prefix=prefix,
            ),
            launch_context=payload.get("launch_context"),
        )
        if completion_validation.get("passed") is not True:
            payload["status"] = "failed"
            payload["success"] = False
    payload["completion_receipt_validation"] = completion_validation
    payload = _with_self_hash(payload, "attestation_sha256")
    _write_json(_attestation_path(prefix), payload)
    return payload


def verify_run_attestation(
    *,
    kind: str,
    config_path: str | Path,
    prefix: str | Path,
    expected_data_fingerprint: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    path = _attestation_path(prefix)
    if not path.is_file():
        return {
            "passed": False,
            "reasons": ["run_attestation_missing"],
            "path": str(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "passed": False,
            "reasons": [f"run_attestation_unreadable:{type(exc).__name__}"],
            "path": str(path),
        }
    if payload.get("schema_version") != SCHEMA_VERSION:
        reasons.append("run_attestation_schema_mismatch")
    if payload.get("target_commitment") != PREREGISTERED_TARGET_COMMITMENT:
        reasons.append("run_target_commitment_mismatch")
    if payload.get("protocol_integrity") != PROTOCOL_INTEGRITY:
        reasons.append("run_protocol_integrity_mismatch")
    if payload.get("paper_fidelity_amendment") != PAPER_FIDELITY_AMENDMENT:
        reasons.append("run_paper_fidelity_amendment_mismatch")
    if payload.get("protocol_amendment_artifact") != protocol_amendment_record():
        reasons.append("run_protocol_amendment_drift")
    if payload.get("protocol_status") != RESEARCH_ALPHA_PROTOCOL_STATUS:
        reasons.append("run_protocol_status_mismatch")
    if payload.get("protocol_amendment_sha256") != TRUSTED_PROTOCOL_AMENDMENT_SHA256:
        reasons.append("run_protocol_amendment_hash_mismatch")
    if (
        payload.get("profile_id") == EXACT_PROFILE_ID
        or payload.get("evidence_class") in CONVERGED_EVIDENCE_CLASSES
    ) and payload.get(
        "host_execution_trust_boundary"
    ) != EXACT_HOST_EXECUTION_TRUST_BOUNDARY:
        reasons.append("run_host_execution_trust_boundary_mismatch")
    if kind == "chain" and payload.get("resource_binding") != {
        "mpi_processes": REQUIRED_CHAIN_COUNT,
        "threads_per_process": 3,
    }:
        reasons.append("run_resource_binding_mismatch")
    if not _verify_self_hash(payload, "attestation_sha256"):
        reasons.append("run_attestation_self_hash_invalid")
    if payload.get("kind") != kind:
        reasons.append("run_attestation_kind_mismatch")
    if payload.get("status") != "completed" or payload.get("success") is not True:
        reasons.append("run_not_successfully_completed")
    if payload.get("returncode") != 0:
        reasons.append("run_returncode_nonzero")
    evidence_class = payload.get("evidence_class")
    if kind == "chain" and evidence_class in CONVERGED_EVIDENCE_CLASSES:
        if payload.get("profile_id") != EXACT_PROFILE_ID:
            reasons.append("formal_run_profile_mismatch")
        completion_validation = _validate_launcher_completion_receipt(
            payload.get("launcher_completion_receipt"),
            expected_binding=_completion_binding_from_payload(
                payload,
                prefix=prefix,
            ),
            launch_context=payload.get("launch_context"),
        )
        if completion_validation.get("passed") is not True:
            reasons.extend(completion_validation.get("reasons") or [])
        if payload.get("completion_receipt_validation") != completion_validation:
            reasons.append("formal_completion_validation_record_drift")
        termination = payload.get("termination")
        if (
            not isinstance(termination, Mapping)
            or termination.get("passed") is not True
            or termination.get("status") != "converged"
            or termination.get("early_stop") is not False
            or termination.get("max_samples_reached") is not False
        ):
            reasons.append("formal_chain_termination_not_converged")
        else:
            checkpoint_record = termination.get("checkpoint") or {}
            checkpoint_path = Path(str(checkpoint_record.get("path") or ""))
            if not checkpoint_path.is_file():
                reasons.append("formal_chain_checkpoint_missing")
            elif checkpoint_record.get("sha256") != _hash_file(checkpoint_path):
                reasons.append("formal_chain_checkpoint_hash_mismatch")
            if termination != _chain_termination_record(prefix):
                reasons.append("formal_chain_termination_record_drift")
        runner_log_record = payload.get("runner_log") or {}
        runner_log_path = _prefix_file(prefix, ".runner.log")
        if (
            runner_log_record.get("path") != str(runner_log_path.resolve())
            or not runner_log_path.is_file()
            or runner_log_record.get("sha256") != _hash_file(runner_log_path)
            or runner_log_record.get("size_bytes") != runner_log_path.stat().st_size
        ):
            reasons.append("formal_runner_log_drift")
        try:
            current_runner_identity = _trusted_cobaya_child_identity()
        except (
            ImportError,
            OSError,
            RuntimeError,
            importlib.metadata.PackageNotFoundError,
        ) as exc:
            reasons.append(
                f"formal_runner_identity_unverifiable:{type(exc).__name__}:{exc}"
            )
        else:
            if current_runner_identity.get("in_virtual_environment") is not True:
                reasons.append("formal_runner_not_in_virtual_environment")
            if payload.get("runner_identity") != current_runner_identity:
                reasons.append("formal_runner_identity_drift")
        recorded_import_policy = (
            (payload.get("runner_identity") or {}).get("import_policy") or {}
        )
        if (
            recorded_import_policy.get("isolated_interpreter") is not True
            or recorded_import_policy.get("pythonpath_empty") is not True
            or recorded_import_policy.get("passed") is not True
        ):
            reasons.append("formal_runner_import_policy_drift")
        command = payload.get("command")
        recorded_runner = payload.get("runner_identity") or {}
        recorded_mpirun = (recorded_runner.get("mpirun") or {}).get(
            "resolved_path"
        )
        recorded_python = (recorded_runner.get("executable") or {}).get(
            "resolved_path"
        )
        expected_command_prefix = [
            recorded_mpirun,
            "-n",
            str(REQUIRED_CHAIN_COUNT),
            "--bind-to",
            "none",
            recorded_python,
            "-I",
            "-m",
            "cobaya.run",
        ]
        if (
            not isinstance(recorded_mpirun, str)
            or not Path(recorded_mpirun).is_absolute()
            or not isinstance(recorded_python, str)
            or not Path(recorded_python).is_absolute()
            or not isinstance(command, list)
            or command[: len(expected_command_prefix)] != expected_command_prefix
            or "--force" in command
        ):
            reasons.append("formal_runner_command_not_canonical")
    if payload.get("config_sha256") != _hash_file(config_path):
        reasons.append("run_config_hash_mismatch")
    if payload.get("data_fingerprint") != expected_data_fingerprint:
        reasons.append("run_data_fingerprint_mismatch")
    if kind == "chain":
        try:
            config_seed = (
                ((_load_yaml(config_path).get("sampler") or {}).get("mcmc") or {}).get(
                    "seed"
                )
                or []
            )
            expected_seed_binding = {
                "algorithm": "numpy.SeedSequence(entropy).spawn(mpi_size)",
                "entropy": list(config_seed),
                "identities": cobaya_mpi_seed_identities(config_seed),
            }
        except (OSError, ValueError, yaml.YAMLError):
            expected_seed_binding = None
        if payload.get("seed_binding") != expected_seed_binding:
            reasons.append("run_seed_binding_invalid")
    environment = payload.get("environment")
    if not isinstance(environment, Mapping) or payload.get(
        "environment_fingerprint"
    ) != _environment_fingerprint(environment or {}):
        reasons.append("run_environment_fingerprint_invalid")
    runtime_binding = payload.get("likelihood_runtime")
    if evidence_class in CONVERGED_EVIDENCE_CLASSES:
        if not isinstance(runtime_binding, Mapping):
            reasons.append("formal_likelihood_runtime_binding_missing")
        else:
            try:
                clipy_root = Path(
                    str(((runtime_binding.get("clipy") or {}).get("root")) or "")
                ).resolve()
                current_likelihood_runtime = likelihood_runtime_inventory(
                    clipy_root.parents[2]
                )
            except (IndexError, OSError, RuntimeError, ValueError) as exc:
                reasons.append(
                    f"formal_likelihood_runtime_unverifiable:{type(exc).__name__}"
                )
            else:
                if runtime_binding != current_likelihood_runtime:
                    reasons.append("formal_likelihood_runtime_drift")

    expected_paths = {
        str(path.resolve()): path for path in _run_artifact_paths(kind, prefix)
    }
    if kind == "chain" and evidence_class in CONVERGED_EVIDENCE_CLASSES:
        reservation_path = _chain_reservation_path(prefix)
        expected_paths[str(reservation_path.resolve())] = reservation_path
    recorded = {
        str(item.get("path")): item
        for item in payload.get("artifacts") or []
        if isinstance(item, Mapping)
    }
    for path_text, artifact_path in expected_paths.items():
        item = recorded.get(path_text)
        if item is None:
            reasons.append(f"run_artifact_not_attested:{artifact_path.name}")
            continue
        if not artifact_path.is_file():
            reasons.append(f"run_artifact_missing:{artifact_path.name}")
            continue
        if item.get("sha256") != _hash_file(artifact_path):
            reasons.append(f"run_artifact_hash_mismatch:{artifact_path.name}")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "path": str(path),
        "sha256": _hash_file(path),
        "payload": payload,
    }


def run_cobaya_with_attestation(
    *,
    kind: str,
    config_path: str | Path,
    prefix: str | Path,
    packages_path: str | Path,
    cobaya_run: str | None,
    mpi_processes: int,
    force: bool,
    workflow_receipts: Mapping[str, Any] | None = None,
    evidence_class: str = "formal_candidate",
    run_id: str | None = None,
) -> int:
    if protocol_amendment_record().get("valid") is not True:
        raise ValueError("The immutable public protocol amendment is missing or drifted")
    if evidence_class in CONVERGED_EVIDENCE_CLASSES and not str(run_id or "").strip():
        raise ValueError("Converged evidence runs require a non-empty run_id")
    if evidence_class in CONVERGED_EVIDENCE_CLASSES:
        if mpi_processes != REQUIRED_CHAIN_COUNT:
            raise ValueError("Converged evidence runs require exactly four MPI processes")
        thread_values = {
            name: os.environ.get(name)
            for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
        }
        if any(value != "3" for value in thread_values.values()):
            raise ValueError(
                "Converged evidence runs require OMP_NUM_THREADS=3, "
                "MKL_NUM_THREADS=3 and OPENBLAS_NUM_THREADS=3"
            )
        if os.environ.get("PYTHONPATH"):
            raise ValueError("Converged exact evidence forbids non-empty PYTHONPATH")
    if kind == "chain":
        prefix_path = Path(prefix)
        existing = sorted(
            path
            for path in prefix_path.parent.glob(prefix_path.name + ".*")
            if path.exists()
        )
        if existing:
            raise ValueError(
                "Chain prefix must be new; existing artifacts cannot be resumed: "
                + ", ".join(path.name for path in existing)
            )
    if kind == "chain" and mpi_processes != REQUIRED_CHAIN_COUNT:
        raise ValueError("The canonical chain run requires exactly four MPI processes")
    if evidence_class in CONVERGED_EVIDENCE_CLASSES and force:
        raise ValueError("Converged evidence runs forbid --force")
    if evidence_class in CONVERGED_EVIDENCE_CLASSES and cobaya_run:
        raise ValueError(
            "Converged evidence runs do not permit Cobaya runner overrides"
        )
    config = _load_yaml(config_path)
    source_config_path = Path(config_path)
    effective_config_path = source_config_path
    config_reasons: list[str] = []
    if kind == "chain":
        if evidence_class == "non_citable_smoke":
            formal_source = copy.deepcopy(config)
            smoke_mcmc = formal_source.setdefault("sampler", {}).setdefault(
                "mcmc", {}
            )
            max_samples = smoke_mcmc.pop("max_samples", None)
            smoke_mcmc.pop("learn_every", None)
            config_reasons.extend(validate_canonical_config(formal_source))
            if max_samples is not None and (
                not isinstance(max_samples, int) or not 1 <= max_samples <= 32
            ):
                config_reasons.append("smoke_max_samples_must_be_between_1_and_32")
        elif evidence_class == "model_adequacy":
            theory = (config.get("theory") or {}).get("camb") or {}
            mcmc = (config.get("sampler") or {}).get("mcmc") or {}
            params = config.get("params") or {}
            if theory.get("path") != "global":
                config_reasons.append("adequacy_camb_global_path_required")
            if not isinstance(config.get("likelihood"), Mapping) or not config.get(
                "likelihood"
            ):
                config_reasons.append("adequacy_likelihoods_missing")
            if not isinstance(mcmc, Mapping) or mcmc.get("max_samples") is not None:
                config_reasons.append("adequacy_mcmc_must_run_to_convergence")
            seeds = mcmc.get("seed") if isinstance(mcmc, Mapping) else None
            if (
                not isinstance(seeds, list)
                or len(seeds) != REQUIRED_CHAIN_COUNT
                or len(set(seeds)) != REQUIRED_CHAIN_COUNT
            ):
                config_reasons.append("adequacy_seed_entropy_invalid")
            for name in ("w", "wa"):
                if not isinstance(params.get(name), Mapping) or not isinstance(
                    params[name].get("prior"), Mapping
                ):
                    config_reasons.append(f"adequacy_{name}_prior_missing")
        else:
            config_reasons.extend(validate_canonical_config(config))
    elif set(config.get("sampler") or {}) != {"minimize"}:
        config_reasons.append("map_config_does_not_use_minimize_sampler")
    if config_reasons:
        _write_json(
            _attestation_path(prefix),
            {
                "schema_version": SCHEMA_VERSION,
                "kind": kind,
                "status": "failed",
                "success": False,
                "returncode": None,
                "completed_at": _utc_now(),
                "reason": "invalid_run_configuration",
                "configuration_reasons": config_reasons,
                "config_path": str(Path(config_path).resolve()),
                "config_sha256": _hash_file(config_path),
            },
        )
        return 2
    if kind == "chain" and evidence_class == "non_citable_smoke":
        existing_limit = ((config.get("sampler") or {}).get("mcmc") or {}).get(
            "max_samples"
        )
        if existing_limit is None:
            smoke_config = copy.deepcopy(config)
            smoke_mcmc = smoke_config.setdefault("sampler", {}).setdefault(
                "mcmc", {}
            )
            smoke_mcmc["max_samples"] = 16
            smoke_mcmc["learn_every"] = 4
            effective_config_path = _prefix_file(prefix, ".non_citable_smoke.yaml")
            effective_config_path.parent.mkdir(parents=True, exist_ok=True)
            effective_config_path.write_text(
                yaml.safe_dump(smoke_config, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
    data_inventory = build_data_inventory(packages_path)
    if not data_inventory["complete"]:
        _write_json(
            _attestation_path(prefix),
            {
                "schema_version": SCHEMA_VERSION,
                "kind": kind,
                "status": "failed",
                "success": False,
                "returncode": None,
                "completed_at": _utc_now(),
                "reason": "canonical_data_inventory_incomplete",
                "data_inventory": data_inventory,
            },
        )
        return 2

    runner_identity = _trusted_cobaya_child_identity()
    if (
        evidence_class in CONVERGED_EVIDENCE_CLASSES
        and runner_identity.get("in_virtual_environment") is not True
    ):
        raise ValueError(
            "Converged evidence runs require the current trusted virtual environment"
        )
    runner_import_policy = runner_identity.get("import_policy") or {}
    if evidence_class in CONVERGED_EVIDENCE_CLASSES and (
        runner_import_policy.get("isolated_interpreter") is not True
        or runner_import_policy.get("pythonpath_empty") is not True
        or runner_import_policy.get("passed") is not True
    ):
        raise ValueError("Converged evidence import-search policy is not trusted")
    reservation_path: Path | None = None
    if kind == "chain":
        reservation_path = _reserve_chain_prefix(prefix)

    command: list[str] = []
    if mpi_processes > 1:
        command.extend(
            [
                str((runner_identity.get("mpirun") or {})["resolved_path"]),
                "-n",
                str(mpi_processes),
                "--bind-to",
                "none",
            ]
        )
    if cobaya_run and evidence_class not in CONVERGED_EVIDENCE_CLASSES:
        custom_invoked = shutil.which(str(cobaya_run)) or str(cobaya_run)
        custom_runner = Path(custom_invoked).resolve(strict=True)
        runner_command = [str(custom_runner)]
    else:
        runner_command = [
            str((runner_identity.get("executable") or {})["resolved_path"]),
            "-I",
            "-m",
            "cobaya.run",
        ]
    command.extend(
        [
            *runner_command,
            str(effective_config_path.resolve()),
            "-p",
            str(Path(packages_path).resolve()),
            "-o",
            str(Path(prefix).resolve()),
        ]
    )
    if force:
        command.append("--force")

    started_at = _utc_now()
    launcher_nonce = secrets.token_hex(32)
    launch_context = _build_launch_context(
        nonce_commitment=_launcher_nonce_commitment(launcher_nonce),
        kind=kind,
        evidence_class=evidence_class,
        run_id=run_id,
        prefix=prefix,
        command=command,
        config_path=effective_config_path,
        source_config_path=source_config_path,
        data_fingerprint=data_inventory["fingerprint"],
        runner_identity=runner_identity,
        reservation_path=reservation_path,
        started_at=started_at,
    )
    environment = environment_manifest()
    running = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "status": "running",
        "success": False,
        "returncode": None,
        "started_at": started_at,
        "completed_at": None,
        "command": command,
        "config_path": str(effective_config_path.resolve()),
        "config_sha256": _hash_file(effective_config_path),
        "source_config_path": str(source_config_path.resolve()),
        "source_config_sha256": _hash_file(source_config_path),
        "data_fingerprint": data_inventory["fingerprint"],
        "profile_id": EXACT_PROFILE_ID,
        "run_id": run_id,
        "claim_scope": EXACT_CLAIM_SCOPE,
        "target_commitment": PREREGISTERED_TARGET_COMMITMENT,
        "protocol_integrity": dict(PROTOCOL_INTEGRITY),
        "paper_fidelity_amendment": dict(PAPER_FIDELITY_AMENDMENT),
        "protocol_amendment_artifact": protocol_amendment_record(),
        "protocol_status": dict(RESEARCH_ALPHA_PROTOCOL_STATUS),
        "protocol_amendment_sha256": TRUSTED_PROTOCOL_AMENDMENT_SHA256,
        "host_execution_trust_boundary": copy.deepcopy(
            EXACT_HOST_EXECUTION_TRUST_BOUNDARY
        ),
        "resource_binding": {
            "mpi_processes": mpi_processes,
            "threads_per_process": 3,
        },
        "evidence_class": evidence_class,
        "citable": False,
        "workflow_receipts": dict(workflow_receipts or {}),
        "environment": environment,
        "environment_fingerprint": _environment_fingerprint(environment),
        "runner_identity": runner_identity,
        "launch_context": launch_context,
        "launcher_completion_receipt": {},
        "artifacts": _artifact_records(
            [reservation_path] if reservation_path is not None else []
        ),
    }
    _write_json(_attestation_path(prefix), running)

    log_path = _prefix_file(prefix, ".runner.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    child_environment = dict(os.environ)
    for sensitive_name in (
        "EVIDENCE_SIGNING_KEY",
        "EVIDENCE_SIGNING_KEY_ID",
        "EVIDENCE_VERIFICATION_KEYS",
    ):
        child_environment.pop(sensitive_name, None)
    with log_path.open("w", encoding="utf-8") as log_handle:
        log_handle.write("command: " + shlex.join(command) + "\n")
        log_handle.flush()
        try:
            if _trusted_cobaya_child_identity() != runner_identity:
                raise RuntimeError("runner identity drifted immediately before exec")
            completed = subprocess.run(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                check=False,
                text=True,
                env=child_environment,
            )
        except (OSError, RuntimeError) as exc:
            failed = {
                **running,
                "status": "failed",
                "completed_at": _utc_now(),
                "reason": f"runner_start_failed:{type(exc).__name__}:{exc}",
                "runner_log": {
                    "path": str(log_path.resolve()),
                    "sha256": _hash_file(log_path),
                },
            }
            _write_json(_attestation_path(prefix), failed)
            return 2

    completed_at = _utc_now()
    termination = (
        _chain_termination_record(prefix)
        if kind == "chain" and evidence_class in CONVERGED_EVIDENCE_CLASSES
        else None
    )
    completed_environment = environment_manifest()
    completed_likelihood_runtime = likelihood_runtime_inventory(
        str(data_inventory.get("packages_path") or "")
    )
    output_paths = _run_artifact_paths(kind, prefix)
    attested_paths = [
        *output_paths,
        *(
            [reservation_path]
            if kind == "chain"
            and reservation_path is not None
            and reservation_path.is_file()
            else []
        ),
    ]
    missing = [str(path) for path in output_paths if not path.is_file()]
    runner_log_record = _artifact_records([log_path])[0]
    provisional_payload = {
        "profile_id": EXACT_PROFILE_ID,
        "claim_scope": EXACT_CLAIM_SCOPE,
        "kind": kind,
        "evidence_class": evidence_class,
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "command": command,
        "config_path": str(effective_config_path.resolve()),
        "config_sha256": _hash_file(effective_config_path),
        "source_config_path": str(source_config_path.resolve()),
        "source_config_sha256": _hash_file(source_config_path),
        "data_fingerprint": data_inventory["fingerprint"],
        "environment_fingerprint": _environment_fingerprint(
            completed_environment
        ),
        "runner_identity": runner_identity,
        "likelihood_runtime": completed_likelihood_runtime,
        "returncode": int(completed.returncode),
        "resource_binding": {
            "mpi_processes": mpi_processes,
            "threads_per_process": 3,
        },
        "seed_binding": (
            _chain_seed_binding(effective_config_path)
            if kind == "chain"
            else None
        ),
        "termination": termination,
        "artifacts": _artifact_records(attested_paths),
        "missing_artifacts": missing,
        "runner_log": runner_log_record,
        "launch_context": launch_context,
        "host_execution_trust_boundary": copy.deepcopy(
            EXACT_HOST_EXECUTION_TRUST_BOUNDARY
        ),
    }
    candidate_success = (
        completed.returncode == 0
        and not missing
        and data_inventory.get("complete") is True
        and (termination is None or termination.get("passed") is True)
    )
    runner_identity_stable = False
    try:
        runner_identity_stable = _trusted_cobaya_child_identity() == runner_identity
    except (ImportError, OSError, RuntimeError):
        runner_identity_stable = False
    launcher_completion_receipt: dict[str, Any] = {}
    if (
        evidence_class in CONVERGED_EVIDENCE_CLASSES
        and candidate_success
        and runner_identity_stable
    ):
        try:
            exact_key_binding = _require_exact_evidence_signing_key_binding()
            launcher_completion_receipt = build_scientific_attestation(
                attestation_type=LAUNCHER_COMPLETION_ATTESTATION_TYPE,
                payload={
                    **_completion_binding_from_payload(
                        provisional_payload,
                        prefix=prefix,
                    ),
                    "launcher_nonce": launcher_nonce,
                    "evidence_signing_key_binding": exact_key_binding,
                },
                require_explicit=True,
            )
        except ValueError:
            # Missing/partial signing authority is a withheld run, never an
            # unsigned converged completion receipt.
            launcher_completion_receipt = {}

    attestation = write_completed_attestation(
        kind=kind,
        config_path=effective_config_path,
        prefix=prefix,
        data_inventory=data_inventory,
        returncode=completed.returncode,
        command=command,
        require_chain_convergence=(
            kind == "chain" and evidence_class in CONVERGED_EVIDENCE_CLASSES
        ),
        mpi_processes=mpi_processes,
        threads_per_process=3,
        runner_identity=runner_identity,
        evidence_class=evidence_class,
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        source_config_path=source_config_path,
        workflow_receipts=workflow_receipts,
        launch_context=launch_context,
        launcher_completion_receipt=launcher_completion_receipt,
        environment_record=completed_environment,
        likelihood_runtime_record=completed_likelihood_runtime,
        termination_record=termination,
    )
    return 0 if attestation["success"] else 2


def _read_chain_table(path: str | Path) -> tuple[list[str], np.ndarray]:
    chain_path = Path(path)
    header: list[str] | None = None
    with chain_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            if not line.lstrip().startswith("#"):
                raise ValueError(f"Missing Cobaya header in {chain_path}")
            header = line.lstrip()[1:].split()
            break
    if not header:
        raise ValueError(f"Empty Cobaya header in {chain_path}")
    data = np.loadtxt(chain_path, comments="#", ndmin=2)
    if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] != len(header):
        raise ValueError(f"Malformed Cobaya table in {chain_path}")
    return header, np.asarray(data, dtype=float)


def _sampled_parameters(updated_config: Mapping[str, Any]) -> list[str]:
    sampled: list[str] = []
    for name, spec in (updated_config.get("params") or {}).items():
        if isinstance(spec, Mapping) and spec.get("prior") is not None:
            sampled.append(str(name))
    return sampled


def chain_diagnostic_failures(rhat: float, ess_bulk: float) -> list[str]:
    """Apply the R-hat/ESS gate, including public amendment 001."""

    failures: list[str] = []
    if not math.isfinite(rhat):
        failures.append("rank_normalized_rhat_unavailable")
    elif rhat >= RANK_RHAT_MAX_EXCLUSIVE:
        failures.append("rank_normalized_rhat_at_or_above_1.01")
    if not math.isfinite(ess_bulk):
        failures.append("bulk_ess_unavailable")
    elif ess_bulk < BULK_ESS_MIN:
        failures.append("bulk_ess_below_paper_fidelity_1000")
    return failures


def diagnose_chains(
    chain_prefix: str | Path,
    *,
    updated_config: Mapping[str, Any],
    burn_fraction: float = DEFAULT_BURN_FRACTION,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not 0 <= burn_fraction < 1:
        raise ValueError("burn_fraction must be in [0, 1)")
    paths = [_prefix_file(chain_prefix, f".{index}.txt") for index in range(1, 5)]
    reasons: list[str] = []
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        return (
            {
                "passed": False,
                "reasons": [
                    "four_independent_chains_required",
                    *[f"missing:{p}" for p in missing],
                ],
                "n_chains": REQUIRED_CHAIN_COUNT - len(missing),
                "parameters": {},
            },
            {},
        )

    hashes = [_hash_file(path) for path in paths]
    if len(set(hashes)) != REQUIRED_CHAIN_COUNT:
        reasons.append("duplicate_chain_files_detected")

    sampled = _sampled_parameters(updated_config)
    if not sampled:
        reasons.append("sampled_parameter_metadata_missing")

    per_chain: list[dict[str, np.ndarray]] = []
    reporting_rows: list[dict[str, np.ndarray]] = []
    headers: list[list[str]] = []
    raw_rows: list[int] = []
    post_burn_rows: list[int] = []
    expanded_draws: list[int] = []
    try:
        for path in paths:
            header, data = _read_chain_table(path)
            headers.append(header)
            raw_rows.append(int(data.shape[0]))
            if "weight" not in header:
                raise ValueError(f"weight column missing from {path}")
            weights = data[:, header.index("weight")]
            rounded = np.rint(weights)
            if (
                np.any(~np.isfinite(weights))
                or np.any(weights <= 0)
                or not np.allclose(weights, rounded)
            ):
                raise ValueError(f"non-positive or non-integer MCMC weights in {path}")
            # Match GetDist: ignore_rows removes a fraction of raw chain rows,
            # before weights are used for posterior summaries.
            burn_rows = int(round(data.shape[0] * burn_fraction))
            retained = data[burn_rows:, :]
            retained_weights = rounded[burn_rows:]
            if retained.shape[0] < 4:
                raise ValueError(f"too few post-burn-in rows in {path}")
            post_burn_rows.append(int(retained.shape[0]))
            total_draws = int(np.sum(retained_weights))
            if total_draws > MAX_EXPANDED_DRAWS_PER_CHAIN:
                raise ValueError(f"expanded chain exceeds safety limit in {path}")
            row_index = np.repeat(
                np.arange(retained.shape[0]), retained_weights.astype(int)
            )
            if row_index.size < 4:
                raise ValueError(f"too few post-burn-in draws in {path}")
            expanded_draws.append(int(row_index.size))
            needed = list(dict.fromkeys([*sampled, *REPORT_PARAMETERS]))
            columns = {
                name: retained[row_index, header.index(name)]
                for name in needed
                if name in header
            }
            per_chain.append(columns)
            reporting_rows.append(
                {
                    "weights": retained_weights.astype(float),
                    **{
                        name: retained[:, header.index(name)]
                        for name in needed
                        if name in header
                    },
                }
            )
    except (OSError, ValueError) as exc:
        reasons.append(f"chain_read_error:{type(exc).__name__}:{exc}")

    if headers and any(header != headers[0] for header in headers[1:]):
        reasons.append("chain_headers_differ")
    missing_sampled = sorted(
        name for name in sampled if any(name not in chain for chain in per_chain)
    )
    if missing_sampled:
        reasons.append("sampled_parameters_missing:" + ",".join(missing_sampled))
    missing_report = sorted(
        name
        for name in REPORT_PARAMETERS
        if not headers or any(name not in header for header in headers)
    )
    if missing_report:
        reasons.append("report_parameters_missing:" + ",".join(missing_report))

    if reasons:
        return (
            {
                "passed": False,
                "reasons": reasons,
                "n_chains": len(per_chain),
                "chain_files": [
                    {"path": str(path), "sha256": digest}
                    for path, digest in zip(paths, hashes)
                ],
                "parameters": {},
            },
            {},
        )

    aligned_count = min(expanded_draws)
    alignment_fractions = [
        aligned_count / count if count > 0 else 0.0 for count in expanded_draws
    ]
    alignment_failures = []
    if min(alignment_fractions) < MIN_DIAGNOSTIC_ALIGNMENT_FRACTION:
        alignment_failures.append(
            "chain_lengths:diagnostic_alignment_fraction_below_0.90"
        )
    needed = list(dict.fromkeys([*sampled, *REPORT_PARAMETERS]))
    aligned: dict[str, np.ndarray] = {
        name: np.stack([chain[name][-aligned_count:] for chain in per_chain], axis=0)
        for name in needed
    }
    # Derived/report parameters are retained for intervals but are not used to
    # weaken the requirement that every sampled cosmological/nuisance parameter
    # pass the gate.
    if any(np.any(~np.isfinite(values)) for values in aligned.values()):
        return (
            {
                "passed": False,
                "reasons": ["non_finite_chain_values"],
                "n_chains": REQUIRED_CHAIN_COUNT,
                "parameters": {},
            },
            {},
        )

    import arviz as az

    diagnostic_names = list(dict.fromkeys([*sampled, *REPORT_PARAMETERS]))
    idata = az.from_dict(
        posterior={name: aligned[name] for name in diagnostic_names}
    )
    rhat_ds = az.rhat(idata, method="rank")
    ess_ds = az.ess(idata, method="bulk")
    mcse_ds = az.mcse(idata, method="mean")
    parameter_diagnostics: dict[str, Any] = {}
    gate_reasons: list[str] = list(alignment_failures)
    for name in diagnostic_names:
        rhat = float(np.asarray(rhat_ds[name]).reshape(-1)[0])
        ess_bulk = float(np.asarray(ess_ds[name]).reshape(-1)[0])
        mcse_mean = float(np.asarray(mcse_ds[name]).reshape(-1)[0])
        posterior_std = float(np.std(aligned[name].reshape(-1), ddof=1))
        failures = chain_diagnostic_failures(rhat, ess_bulk)
        if failures:
            gate_reasons.extend(f"{name}:{failure}" for failure in failures)
        parameter_diagnostics[name] = {
            "rank_normalized_rhat": rhat,
            "bulk_ess": ess_bulk,
            "mcse_mean": mcse_mean,
            "posterior_std": posterior_std,
            "mcse_over_posterior_std": (
                mcse_mean / posterior_std if posterior_std > 0 else math.inf
            ),
            "passed": not failures,
            "failures": failures,
        }
    diagnostics = {
        "passed": not gate_reasons,
        "reasons": gate_reasons,
        "method": {
            "rhat": "rank_normalized_split_rhat_arviz",
            "rhat_max_exclusive": RANK_RHAT_MAX_EXCLUSIVE,
            "ess": "bulk_ess_arviz",
            "ess_min_inclusive": BULK_ESS_MIN,
            "burn_fraction": burn_fraction,
            "burn_convention": "getdist_remove_fraction_of_raw_rows_per_chain",
            "alignment": "diagnostics_only_recent_draws_truncated_to_shortest_chain",
            "minimum_alignment_fraction_inclusive": (
                MIN_DIAGNOSTIC_ALIGNMENT_FRACTION
            ),
            "weights": "post_burn_integer_cobaya_weights_expanded_for_arviz_only",
            "reporting": "all_post_burn_weighted_rows_processed_by_getdist",
        },
        "n_chains": REQUIRED_CHAIN_COUNT,
        "raw_rows_per_chain": raw_rows,
        "post_burn_rows_per_chain": post_burn_rows,
        "post_burn_expanded_draws_per_chain": expanded_draws,
        "aligned_draws_per_chain": aligned_count,
        "diagnostic_alignment_fraction_per_chain": alignment_fractions,
        "maximum_diagnostic_discarded_fraction": 1.0 - min(alignment_fractions),
        "chain_length_balance_passed": not alignment_failures,
        "sampled_parameters": sampled,
        "diagnosed_parameters": diagnostic_names,
        "parameters": parameter_diagnostics,
        "chain_files": [
            {"path": str(path), "sha256": digest} for path, digest in zip(paths, hashes)
        ],
    }
    reporting_values = {
        name: np.concatenate([chain[name] for chain in reporting_rows])
        for name in needed
    }
    reporting_weights = np.concatenate(
        [chain["weights"] for chain in reporting_rows]
    )
    return diagnostics, {
        "diagnostic_chains": aligned,
        "reporting_values": reporting_values,
        "reporting_weights": reporting_weights,
    }


def posterior_intervals(
    analysis_data: Mapping[str, Any],
    parameters: Sequence[str] = REPORT_PARAMETERS,
) -> dict[str, Any]:
    from getdist.mcsamples import MCSamples

    reporting_values = analysis_data.get("reporting_values") or {}
    reporting_weights = np.asarray(
        analysis_data.get("reporting_weights"), dtype=float
    )
    diagnostic_chains = analysis_data.get("diagnostic_chains") or {}
    if not reporting_values or reporting_weights.ndim != 1:
        raise ValueError("GetDist reporting samples are missing")
    matrix = np.column_stack(
        [np.asarray(reporting_values[name], dtype=float) for name in parameters]
    )
    samples = MCSamples(
        samples=matrix,
        weights=reporting_weights,
        names=list(parameters),
        labels=list(parameters),
        settings={"contours": [0.68, 0.95]},
    )
    means = samples.getMeans()
    stds = np.sqrt(samples.getVars())
    marge_stats = samples.getMargeStats()
    intervals: dict[str, Any] = {}
    for index, name in enumerate(parameters):
        values = np.asarray(reporting_values[name], dtype=float)
        q16 = float(samples.confidence(values, 0.16, weights=reporting_weights))
        q50 = float(samples.confidence(values, 0.50, weights=reporting_weights))
        q84 = float(
            samples.confidence(values, 0.16, upper=True, weights=reporting_weights)
        )
        limit = marge_stats.parWithName(name).limits[0]
        minimum_lower = float(limit.lower)
        minimum_upper = float(limit.upper)
        import arviz as az

        chain_values = np.asarray(diagnostic_chains[name], dtype=float)
        mcse_mean = float(
            np.asarray(az.mcse(chain_values, method="mean")).reshape(-1)[0]
        )
        mean = float(means[index])
        intervals[name] = {
            "mean": mean,
            "std": float(stds[index]),
            "median": float(q50),
            "lower_68": float(q16),
            "upper_68": float(q84),
            "minus": float(q50 - q16),
            "plus": float(q84 - q50),
            "minimal_lower_68": minimum_lower,
            "minimal_upper_68": minimum_upper,
            "minimal_minus_from_mean": mean - minimum_lower,
            "minimal_plus_from_mean": minimum_upper - mean,
            "mcse_mean": mcse_mean,
            "reporting_engine": "GetDist 1.7.7 weighted post-burn rows",
        }
    return intervals


def table3_reported_interval(
    parameter: str,
    interval: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply DESI section 2.5's symmetric/asymmetric reporting rule."""

    center = float(interval["mean"])
    if parameter == "wa":
        lower = float(interval["minimal_lower_68"])
        upper = float(interval["minimal_upper_68"])
        statistic = "mean_and_minimal_68_percent_credible_interval"
    else:
        std = float(interval["std"])
        lower = center - std
        upper = center + std
        statistic = "posterior_mean_plus_or_minus_standard_deviation"
    return {
        "center": center,
        "lower_68": lower,
        "upper_68": upper,
        "uncertainty_minus": center - lower,
        "uncertainty_plus": upper - center,
        "mcse_mean": float(interval["mcse_mean"]),
        "reporting_statistic": statistic,
    }


def _read_cobaya_point(
    minimum_path: str | Path,
    likelihood_names: Sequence[str],
) -> dict[str, Any]:
    header, data = _read_chain_table(minimum_path)
    if data.shape[0] != 1:
        raise ValueError(f"Expected exactly one optimizer result in {minimum_path}")
    row = data[0]
    record = {name: float(row[index]) for index, name in enumerate(header)}
    if "minuslogpost" not in record or "chi2" not in record:
        raise ValueError("Optimizer result lacks minuslogpost or chi2")
    components: dict[str, float] = {}
    for name in likelihood_names:
        column = f"chi2__{name}"
        if column not in record:
            raise ValueError(f"Optimizer result lacks {column}")
        components[name] = record[column]
    values = [record["minuslogpost"], record["chi2"], *components.values()]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Optimizer result contains non-finite objective values")
    component_sum = float(sum(components.values()))
    tolerance = max(1e-4, abs(record["chi2"]) * 1e-7)
    if not math.isclose(component_sum, record["chi2"], rel_tol=0.0, abs_tol=tolerance):
        raise ValueError("Total chi2 does not match the declared likelihood components")
    return {
        "minuslogpost": record["minuslogpost"],
        "chi2_likelihood": record["chi2"],
        "chi2_components": components,
        "parameters": {
            name: value
            for name, value in record.items()
            if not name.startswith("chi2")
            and not name.startswith("minuslog")
            and name != "weight"
        },
        "result_sha256": _hash_file(minimum_path),
    }


def inspect_map_run(
    *,
    label: str,
    config_path: str | Path,
    prefix: str | Path,
    expected_data_fingerprint: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    try:
        declared = _load_yaml(config_path)
        input_config = _load_yaml(_prefix_file(prefix, ".minimize.input.yaml"))
        updated = _load_yaml(_prefix_file(prefix, ".minimize.updated.yaml"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return {
            "label": label,
            "passed": False,
            "reasons": [f"map_config_unreadable:{type(exc).__name__}"],
        }

    if _config_fingerprint(declared) != _config_fingerprint(input_config):
        reasons.append("map_input_config_does_not_match_declared_config")
    attestation = verify_run_attestation(
        kind="map",
        config_path=config_path,
        prefix=prefix,
        expected_data_fingerprint=expected_data_fingerprint,
    )
    if not attestation["passed"]:
        reasons.extend(attestation["reasons"])

    updated_fingerprints = _map_pair_fingerprints(updated)
    declared_ignore_prior = (
        ((declared.get("sampler") or {}).get("minimize") or {}).get(
            "ignore_prior"
        )
    )
    input_ignore_prior = (
        ((input_config.get("sampler") or {}).get("minimize") or {}).get(
            "ignore_prior"
        )
    )
    updated_ignore_prior = (
        ((updated.get("sampler") or {}).get("minimize") or {}).get(
            "ignore_prior"
        )
    )
    optimization_settings_consistent = (
        declared_ignore_prior == input_ignore_prior == updated_ignore_prior
        and isinstance(updated_ignore_prior, bool)
    )
    optimization_target = (
        "likelihood"
        if optimization_settings_consistent and updated_ignore_prior is True
        else "posterior"
        if optimization_settings_consistent and updated_ignore_prior is False
        else "unknown"
    )
    try:
        point = _read_cobaya_point(
            _prefix_file(prefix, ".minimum.txt"),
            tuple((updated.get("likelihood") or {}).keys()),
        )
    except (OSError, ValueError) as exc:
        reasons.append(f"map_result_invalid:{type(exc).__name__}:{exc}")
        point = None
    return {
        "label": label,
        "passed": not reasons,
        "reasons": reasons,
        "config": {
            "path": str(config_path),
            "sha256": _hash_file(config_path),
            "input_sha256": _hash_file(_prefix_file(prefix, ".minimize.input.yaml")),
            "updated_sha256": _hash_file(
                _prefix_file(prefix, ".minimize.updated.yaml")
            ),
        },
        "fingerprints": updated_fingerprints,
        "optimization": {
            "target": optimization_target,
            "declared_ignore_prior": declared_ignore_prior,
            "input_ignore_prior": input_ignore_prior,
            "updated_ignore_prior": updated_ignore_prior,
            "settings_consistent": optimization_settings_consistent,
            "likelihood_only_mle_proven": (
                optimization_settings_consistent
                and updated_ignore_prior is True
            ),
        },
        "attestation": attestation,
        "point": point,
    }


def compare_map_runs(
    free: Mapping[str, Any], fixed: Mapping[str, Any]
) -> dict[str, Any]:
    reasons: list[str] = []
    if free.get("passed") is not True:
        reasons.append("free_w0wa_optimization_not_verified")
    if fixed.get("passed") is not True:
        reasons.append("fixed_lcdm_optimization_not_verified")
    for key in ("likelihood", "shared_parameters"):
        if (free.get("fingerprints") or {}).get(key) != (
            fixed.get("fingerprints") or {}
        ).get(key):
            reasons.append(f"map_execution_{key}_fingerprint_mismatch")
    free_data = ((free.get("attestation") or {}).get("payload") or {}).get(
        "data_fingerprint"
    )
    fixed_data = ((fixed.get("attestation") or {}).get("payload") or {}).get(
        "data_fingerprint"
    )
    if free_data != fixed_data:
        reasons.append("map_execution_data_fingerprint_mismatch")
    free_environment = ((free.get("attestation") or {}).get("payload") or {}).get(
        "environment_fingerprint"
    )
    fixed_environment = ((fixed.get("attestation") or {}).get("payload") or {}).get(
        "environment_fingerprint"
    )
    if free_environment != fixed_environment:
        reasons.append("map_execution_environment_fingerprint_mismatch")

    free_point = free.get("point") or {}
    fixed_point = fixed.get("point") or {}
    free_target = (free.get("optimization") or {}).get("target")
    fixed_target = (fixed.get("optimization") or {}).get("target")
    likelihood_only_mle_proven = bool(
        (free.get("optimization") or {}).get("likelihood_only_mle_proven")
        is True
        and (fixed.get("optimization") or {}).get(
            "likelihood_only_mle_proven"
        )
        is True
    )
    if not reasons:
        delta_likelihood_chi2 = float(
            fixed_point["chi2_likelihood"] - free_point["chi2_likelihood"]
        )
        delta_objective = 2.0 * float(
            fixed_point["minuslogpost"] - free_point["minuslogpost"]
        )
        # The nesting inequality applies to likelihood maxima, not to posterior
        # modes evaluated under extra model priors. A negative likelihood delta
        # at two MAP points is therefore retained as descriptive evidence unless
        # both attested optimizers explicitly targeted the likelihood alone.
        if likelihood_only_mle_proven and delta_likelihood_chi2 < -0.01:
            reasons.append("free_model_has_worse_chi2_than_nested_fixed_model")
    else:
        delta_likelihood_chi2 = math.nan
        delta_objective = math.nan

    result: dict[str, Any] = {
        "passed": not reasons,
        "reasons": reasons,
        "significance_ready": False,
        "likelihood_only_mle_proven": likelihood_only_mle_proven,
        "free_w0wa": free,
        "fixed_lcdm": fixed,
        "method": {
            "optimization_targets": {
                "free_w0wa": free_target,
                "fixed_lcdm": fixed_target,
            },
            "delta_objective": (
                "2*(minuslogpost_fixed_lcdm-minuslogpost_free_w0wa)"
            ),
            "delta_likelihood_chi2": (
                "chi2_fixed_lcdm-minus-chi2_free_w0wa_at_optimized_points"
            ),
            "additional_parameters": 2,
            "wilks_calibration_verified": False,
            "statistical_interpretation": (
                "descriptive_paired_optimizer_difference"
                if not likelihood_only_mle_proven
                else "likelihood_ratio_test_candidate"
            ),
            "caveat": (
                "Posterior-mode differences are not likelihood-ratio test "
                "statistics. Do not derive a p-value, Gaussian-equivalent "
                "significance, or Bayesian evidence from them."
            ),
        },
    }
    if not reasons:
        result.update(
            {
                # delta_chi2 is retained as a compatibility alias, but its
                # paired-point semantics are explicit in method and the named
                # field below. It is not a calibrated test statistic here.
                "delta_chi2": delta_likelihood_chi2,
                "delta_likelihood_chi2_at_optimized_points": (
                    delta_likelihood_chi2
                ),
                "delta_objective_at_optimized_points": delta_objective,
            }
        )
        result["significance_withheld_reason"] = (
            "wilks_regularity_or_simulation_calibration_not_verified"
            if likelihood_only_mle_proven
            else (
                "paired_optimizers_target_posterior_not_likelihood_only_mle"
            )
        )
    return result


CONCLUSION_ATTESTATION_SCHEMA_VERSION = 1
CONCLUSION_ATTESTATION_ARTIFACT_TYPE = "scientific_conclusion_attestation"


def build_conclusion_attestations(
    *,
    map_comparison: Mapping[str, Any],
    data_fingerprint: str,
    likelihood_fingerprint: str,
    evidence_manifest_sha256: str,
) -> list[dict[str, Any]]:
    """Emit the exact schema consumed by the manuscript conclusion gate.

    Attestations are absent unless significance calibration is itself verified.
    Posterior convergence or a finite paired optimizer delta never enters this
    path on its own.
    """

    if map_comparison.get("significance_ready") is not True:
        return []
    method = map_comparison.get("method")
    if not isinstance(method, Mapping):
        return []
    calibration: dict[str, Any]
    comparison_type: str
    if (
        method.get("wilks_calibration_verified") is True
        and map_comparison.get("likelihood_only_mle_proven") is True
    ):
        calibration = {
            "method": "wilks",
            "verified": True,
            "assumptions_verified": True,
            "likelihood_only_mle_proven": True,
        }
        comparison_type = "likelihood_ratio"
    elif method.get("simulation_calibration_verified") is True:
        simulation_manifest_sha256 = str(
            method.get("simulation_manifest_sha256") or ""
        )
        if not (
            simulation_manifest_sha256.startswith("sha256:")
            and len(simulation_manifest_sha256) == 71
        ):
            return []
        calibration = {
            "method": "simulation",
            "verified": True,
            "simulation_calibration_verified": True,
            "simulation_manifest_sha256": simulation_manifest_sha256,
        }
        comparison_type = "simulation_calibrated_likelihood_ratio"
    else:
        return []
    if not all(
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        for value in (
            data_fingerprint,
            likelihood_fingerprint,
            evidence_manifest_sha256,
        )
    ):
        return []

    common = {
        "schema_version": CONCLUSION_ATTESTATION_SCHEMA_VERSION,
        "artifact_type": CONCLUSION_ATTESTATION_ARTIFACT_TYPE,
        "baseline_model": "lcdm",
        "alternative_model": "w0wa_cdm",
        "data_fingerprint": data_fingerprint,
        "likelihood_fingerprint": likelihood_fingerprint,
        "comparison_type": comparison_type,
        "calibration": calibration,
        "manifest_sha256": evidence_manifest_sha256,
        "publication_ready": True,
        "significance_ready": True,
    }
    return [
        {
            **common,
            "attestation_id": (
                f"{claim_kind}:{evidence_manifest_sha256.split(':', 1)[1][:24]}"
            ),
            "claim_kind": claim_kind,
        }
        for claim_kind in (
            "baseline_rejection",
            "extended_model_preference",
            "dark_energy_evolution",
        )
    ]


def build_evidence_manifest(
    *,
    canonical_config_path: str | Path,
    chain_prefix: str | Path,
    free_map_config_path: str | Path,
    fixed_map_config_path: str | Path,
    free_map_prefix: str | Path,
    fixed_map_prefix: str | Path,
    packages_path: str | Path,
    burn_fraction: float = DEFAULT_BURN_FRACTION,
    data_inventory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    canonical = _load_yaml(canonical_config_path)
    free_config = _load_yaml(free_map_config_path)
    fixed_config = _load_yaml(fixed_map_config_path)
    canonical_reasons = validate_canonical_config(canonical)
    pair_check = validate_map_config_pair(free_config, fixed_config)
    inventory = dict(data_inventory or build_data_inventory(packages_path))

    chain_input_path = _prefix_file(chain_prefix, ".input.yaml")
    chain_updated_path = _prefix_file(chain_prefix, ".updated.yaml")
    chain_binding_reasons: list[str] = []
    try:
        chain_input = _load_yaml(chain_input_path)
        chain_updated = _load_yaml(chain_updated_path)
        if _config_fingerprint(chain_input) != _config_fingerprint(canonical):
            chain_binding_reasons.append(
                "chain_input_config_does_not_match_canonical_config"
            )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        chain_binding_reasons.append(f"chain_config_unreadable:{type(exc).__name__}")
        chain_updated = {"params": {}}

    chain_attestation = verify_run_attestation(
        kind="chain",
        config_path=canonical_config_path,
        prefix=chain_prefix,
        expected_data_fingerprint=str(inventory.get("fingerprint")),
    )
    if not chain_attestation["passed"]:
        chain_binding_reasons.extend(chain_attestation["reasons"])

    diagnostics, aligned = diagnose_chains(
        chain_prefix,
        updated_config=chain_updated,
        burn_fraction=burn_fraction,
    )
    posterior_reasons = [
        *canonical_reasons,
        *(
            []
            if inventory.get("complete") is True
            else ["canonical_data_inventory_incomplete"]
        ),
        *chain_binding_reasons,
        *diagnostics.get("reasons", []),
    ]
    posterior_ready = not posterior_reasons and diagnostics.get("passed") is True
    posterior: dict[str, Any] = {
        "passed": posterior_ready,
        "reasons": posterior_reasons,
        "diagnostics": diagnostics,
        "attestation": chain_attestation,
    }
    if posterior_ready:
        posterior["intervals_68"] = posterior_intervals(aligned)

    free_run = inspect_map_run(
        label="free_w0wa",
        config_path=free_map_config_path,
        prefix=free_map_prefix,
        expected_data_fingerprint=str(inventory.get("fingerprint")),
    )
    fixed_run = inspect_map_run(
        label="fixed_lcdm",
        config_path=fixed_map_config_path,
        prefix=fixed_map_prefix,
        expected_data_fingerprint=str(inventory.get("fingerprint")),
    )
    map_comparison = compare_map_runs(free_run, fixed_run)
    if not pair_check["passed"]:
        map_comparison["passed"] = False
        map_comparison["reasons"] = [*pair_check["reasons"], *map_comparison["reasons"]]
        for key in (
            "delta_chi2",
            "delta_likelihood_chi2_at_optimized_points",
            "delta_objective_at_optimized_points",
            "p_value",
            "equivalent_sigma",
        ):
            map_comparison.pop(key, None)
    if inventory.get("complete") is not True:
        map_comparison["passed"] = False
        map_comparison["reasons"] = [
            "canonical_data_inventory_incomplete",
            *map_comparison["reasons"],
        ]
        for key in (
            "delta_chi2",
            "delta_likelihood_chi2_at_optimized_points",
            "delta_objective_at_optimized_points",
            "p_value",
            "equivalent_sigma",
        ):
            map_comparison.pop(key, None)

    publication_ready = posterior_ready and map_comparison.get("passed") is True
    failures = [
        *[f"posterior:{reason}" for reason in posterior["reasons"]],
        *[f"map:{reason}" for reason in map_comparison["reasons"]],
    ]
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "canonical_full_likelihood_w0wa_evidence",
        "created_at": _utc_now(),
        "status": "PASS" if publication_ready else "FAIL",
        "publication_ready": publication_ready,
        # Set below only after a versioned, calibrated, hash-bound conclusion
        # attestation has been constructed from this same manifest branch.
        "significance_ready": False,
        "claim_scope": (
            "posterior_intervals_and_descriptive_paired_optimizer_differences"
            if publication_ready and not map_comparison.get("significance_ready")
            else "posterior_intervals_and_likelihood_ratio_significance"
            if publication_ready
            else "none"
        ),
        "limitations": (
            [str(map_comparison["significance_withheld_reason"])]
            if map_comparison.get("significance_withheld_reason")
            else []
        ),
        "failures": failures,
        "configuration": {
            "canonical": {
                "path": str(canonical_config_path),
                "sha256": _hash_file(canonical_config_path),
                "fingerprint": _config_fingerprint(canonical),
                "validation_reasons": canonical_reasons,
            },
            "free_map": {
                "path": str(free_map_config_path),
                "sha256": _hash_file(free_map_config_path),
                "fingerprint": _config_fingerprint(free_config),
            },
            "fixed_map": {
                "path": str(fixed_map_config_path),
                "sha256": _hash_file(fixed_map_config_path),
                "fingerprint": _config_fingerprint(fixed_config),
            },
            "map_pair": pair_check,
        },
        "data": inventory,
        "environment": environment_manifest(),
        "posterior": posterior,
        "map_comparison": map_comparison,
    }
    evidence_manifest_sha256 = _hash_object(manifest)
    manifest["evidence_manifest_sha256"] = evidence_manifest_sha256
    likelihood_fingerprint = str(
        ((map_comparison.get("free_w0wa") or {}).get("fingerprints") or {}).get(
            "likelihood"
        )
        or ""
    )
    conclusion_attestations = build_conclusion_attestations(
        map_comparison=map_comparison,
        data_fingerprint=str(inventory.get("fingerprint") or ""),
        likelihood_fingerprint=likelihood_fingerprint,
        evidence_manifest_sha256=evidence_manifest_sha256,
    )
    manifest["conclusion_attestations"] = conclusion_attestations
    manifest["significance_ready"] = bool(conclusion_attestations)
    manifest["claim_scope"] = (
        "posterior_intervals_and_likelihood_ratio_significance"
        if publication_ready and conclusion_attestations
        else "posterior_intervals_and_descriptive_paired_optimizer_differences"
        if publication_ready
        else "none"
    )
    manifest["manifest_sha256"] = _hash_object(manifest)
    return manifest


def _support_path_records(paths: Sequence[str | Path]) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    reasons: list[str] = []
    seen: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path).resolve()
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            reasons.append(f"claim_support_path_missing:{path}")
            continue
        records.append(
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": _hash_file(path),
            }
        )
    if not records:
        reasons.append("claim_support_paths_required")
    return records, reasons


def build_exact_analysis_manifest(
    *,
    canonical_config_path: str | Path,
    chain_prefix: str | Path,
    packages_path: str | Path,
    preflight_report_path: str | Path,
    generation_report_path: str | Path,
    support_paths: Sequence[str | Path],
    burn_fraction: float = DEFAULT_BURN_FRACTION,
) -> dict[str, Any]:
    """Analyze only the exact formal chain; proxy and smoke runs are rejected."""

    canonical = _load_yaml(canonical_config_path)
    config_reasons = validate_canonical_config(canonical)
    inventory = build_data_inventory(packages_path)
    preflight = verify_preflight_receipt(
        preflight_report_path,
        canonical_config_path=canonical_config_path,
        packages_path=packages_path,
    )
    generation = verify_generation_receipt(
        generation_report_path,
        canonical_config_path=canonical_config_path,
        preflight_report_path=preflight_report_path,
        packages_path=packages_path,
    )
    chain_binding_reasons: list[str] = []
    try:
        chain_input = _load_yaml(_prefix_file(chain_prefix, ".input.yaml"))
        chain_updated = _load_yaml(_prefix_file(chain_prefix, ".updated.yaml"))
        if _config_fingerprint(chain_input) != _config_fingerprint(canonical):
            chain_binding_reasons.append(
                "chain_input_config_does_not_match_exact_config"
            )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        chain_binding_reasons.append(f"chain_config_unreadable:{type(exc).__name__}")
        chain_updated = {"params": {}}
    attestation = verify_run_attestation(
        kind="chain",
        config_path=canonical_config_path,
        prefix=chain_prefix,
        expected_data_fingerprint=str(inventory.get("fingerprint") or ""),
    )
    attestation_payload = attestation.get("payload") or {}
    if attestation_payload.get("profile_id") != EXACT_PROFILE_ID:
        chain_binding_reasons.append("chain_attestation_exact_profile_missing")
    if attestation_payload.get("evidence_class") != "formal_candidate":
        chain_binding_reasons.append("smoke_or_nonformal_run_is_not_citable")
    run_id = str(attestation_payload.get("run_id") or "").strip()
    if not run_id:
        chain_binding_reasons.append("formal_run_id_missing")
    receipt_binding = attestation_payload.get("workflow_receipts") or {}
    if preflight.get("passed") is True and receipt_binding.get(
        "preflight_sha256"
    ) != preflight["payload"].get("preflight_sha256"):
        chain_binding_reasons.append("chain_preflight_receipt_mismatch")
    if generation.get("passed") is True and receipt_binding.get(
        "generation_sha256"
    ) != generation["payload"].get("generation_sha256"):
        chain_binding_reasons.append("chain_generation_receipt_mismatch")
    if receipt_binding.get("protocol_amendment_sha256") != (
        protocol_amendment_record().get("sha256")
    ):
        chain_binding_reasons.append("chain_protocol_amendment_receipt_mismatch")
    preflight_runtime = ((preflight.get("payload") or {}).get("environment") or {}).get(
        "runtime"
    )
    if isinstance(preflight_runtime, Mapping) and attestation_payload.get(
        "environment_fingerprint"
    ) != _environment_fingerprint(preflight_runtime):
        chain_binding_reasons.append("chain_runtime_environment_drifted_from_preflight")
    diagnostics, aligned = diagnose_chains(
        chain_prefix,
        updated_config=chain_updated,
        burn_fraction=burn_fraction,
    )
    support_records, support_reasons = _support_path_records(support_paths)
    chain_ids = [
        f"chain-{index}:{str(item.get('sha256') or '').split(':')[-1][:16]}"
        for index, item in enumerate(diagnostics.get("chain_files") or [], start=1)
    ]
    seeds = ((canonical.get("sampler") or {}).get("mcmc") or {}).get("seed") or []
    seed_identities = cobaya_mpi_seed_identities(seeds)
    data_fingerprints = {
        str(name): str(group.get("fingerprint") or "")
        for name, group in (inventory.get("groups") or {}).items()
        if isinstance(group, Mapping)
    }
    environment_distributions = (
        ((preflight.get("payload") or {}).get("environment") or {}).get(
            "distributions"
        )
        or {}
    )
    loaded_runtime = (
        ((preflight.get("payload") or {}).get("reference_likelihood_values") or {}).get(
            "loaded_likelihood_runtime"
        )
        or {}
    )
    shared_implementation = {
        "cobaya": (environment_distributions.get("cobaya") or {}).get(
            "fingerprint"
        ),
        "camb": (environment_distributions.get("camb") or {}).get("fingerprint"),
    }
    implementation_fingerprints: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_LIKELIHOODS:
        record = dict(shared_implementation)
        if name.startswith("planck_"):
            record["clipy_tree"] = ((loaded_runtime.get("clipy") or {}).get(
                "tree_fingerprint"
            ))
        if name.startswith("act_dr6"):
            record["act_dr6_lenslike"] = (
                (environment_distributions.get("act_dr6_lenslike") or {}).get(
                    "fingerprint"
                )
            )
        implementation_fingerprints[name] = record
    likelihood_fingerprints = {
        name: _hash_object(
            {
                "configuration": (canonical.get("likelihood") or {}).get(name),
                "data_fingerprint": data_fingerprints.get(name),
                "implementation": implementation_fingerprints[name],
            }
        )
        for name in REQUIRED_LIKELIHOODS
    }
    failures = [
        *[f"configuration:{reason}" for reason in config_reasons],
        *(
            []
            if inventory.get("complete") is True
            else ["data:exact_data_inventory_incomplete"]
        ),
        *[f"preflight:{reason}" for reason in preflight.get("reasons", [])],
        *[f"generation:{reason}" for reason in generation.get("reasons", [])],
        *[f"chain:{reason}" for reason in chain_binding_reasons],
        *[f"attestation:{reason}" for reason in attestation.get("reasons", [])],
        *[f"diagnostics:{reason}" for reason in diagnostics.get("reasons", [])],
        *[f"support:{reason}" for reason in support_reasons],
    ]
    analyzed = not failures and diagnostics.get("passed") is True
    posterior: dict[str, Any] = {
        "passed": analyzed,
        "diagnostics": diagnostics,
        "attestation": attestation,
    }
    if analyzed:
        posterior["intervals_68"] = posterior_intervals(aligned)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "w0wa_exact_analysis",
        "created_at": _utc_now(),
        "profile_id": EXACT_PROFILE_ID,
        "paper": EXACT_PAPER,
        "claim_scope": EXACT_CLAIM_SCOPE,
        "target_commitment": PREREGISTERED_TARGET_COMMITMENT,
        "protocol_integrity": dict(PROTOCOL_INTEGRITY),
        "paper_fidelity_amendment": dict(PAPER_FIDELITY_AMENDMENT),
        "protocol_amendment_artifact": protocol_amendment_record(),
        "status": "ANALYZED" if analyzed else "WITHHELD",
        "evidence_ready_for_offline_grading": analyzed,
        "publication_ready": False,
        "external_review_complete": False,
        "failures": failures,
        "configuration": {
            "path": str(Path(canonical_config_path).resolve()),
            "sha256": _hash_file(canonical_config_path),
            "fingerprint": _config_fingerprint(canonical),
        },
        "data": inventory,
        "data_fingerprints": data_fingerprints,
        "implementation_fingerprints": implementation_fingerprints,
        "likelihood_fingerprints": likelihood_fingerprints,
        "workflow_receipts": {
            "preflight": preflight,
            "generation": generation,
        },
        "run_identity": {
            "run_id": run_id,
            "chain_ids": chain_ids,
            "seed_entropy": list(seeds),
            "seed_identities": seed_identities,
            "derived_chain_seeds": [
                item["derived_seed"] for item in seed_identities
            ],
        },
        "research_alpha_binding": {
            "chain_sha256": [
                str(item.get("sha256"))
                for item in diagnostics.get("chain_files") or []
            ],
            "sampled_parameters": list(
                diagnostics.get("sampled_parameters") or []
            ),
        },
        "posterior": posterior,
        "claim_support_paths": support_records,
        "prohibited_outputs": [
            "wilks_p_value",
            "gaussian_equivalent_sigma",
            "bayes_factor_preference",
            "dynamic_dark_energy_discovery_claim",
        ],
    }
    return _with_self_hash(manifest, "manifest_sha256")


def _normalise_target_name(name: Any) -> str | None:
    cleaned = (
        str(name)
        .strip()
        .lower()
        .replace("ω", "omega")
        .replace("_", "")
        .replace("{", "")
        .replace("}", "")
        .replace("0", "0")
    )
    aliases = {
        "omegam": "omegam",
        "omegamatter": "omegam",
        "h0": "H0",
        "w": "w",
        "w0": "w",
        "wa": "wa",
    }
    return aliases.get(cleaned)


def _numeric_threshold(
    thresholds: Mapping[str, Any], required_tokens: Sequence[str]
) -> float | None:
    for key, value in thresholds.items():
        key_text = str(key).lower()
        if all(token in key_text for token in required_tokens) and isinstance(
            value, (int, float)
        ):
            number = float(value)
            if math.isfinite(number) and number >= 0:
                return number
    return None


def _validate_support_records(records: Any) -> list[str]:
    reasons: list[str] = []
    if not isinstance(records, list) or not records:
        return ["claim_support_paths_required"]
    for item in records:
        if not isinstance(item, Mapping):
            reasons.append("claim_support_record_invalid")
            continue
        path = Path(str(item.get("path") or ""))
        if not path.is_file():
            reasons.append(f"claim_support_path_missing:{path}")
        elif item.get("sha256") != _hash_file(path):
            reasons.append(f"claim_support_path_hash_mismatch:{path}")
    return reasons


def _adequacy_check_names(payload: Mapping[str, Any]) -> set[str]:
    checks = payload.get("checks")
    if isinstance(checks, Mapping):
        return {
            str(name)
            for name, item in checks.items()
            if isinstance(item, Mapping) and item.get("passed") is True
        }
    if isinstance(checks, list):
        return {
            str(item.get("name"))
            for item in checks
            if isinstance(item, Mapping) and item.get("passed") is True
        }
    return set()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _validate_adequacy_evidence(
    adequacy: Mapping[str, Any],
    *,
    hidden: Mapping[str, Any],
    run_id: str,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    failures: list[str] = []
    checks = adequacy.get("checks")
    if not isinstance(checks, Mapping):
        return {}, ["adequacy_checks_mapping_missing"]
    required = hidden.get("required_model_adequacy_checks")
    if not isinstance(required, list) or not required:
        return {}, ["hidden_required_adequacy_checks_missing"]
    expected = {str(name) for name in required}
    if set(checks) != expected:
        failures.append("adequacy_check_set_mismatch")
    normalized: dict[str, dict[str, Any]] = {}
    for name in sorted(expected):
        raw = checks.get(name)
        if not isinstance(raw, Mapping):
            failures.append(f"adequacy_check_missing:{name}")
            continue
        status = str(raw.get("status") or "").lower()
        artifact_id = raw.get("artifact_id")
        artifact_hash = raw.get("artifact_hash")
        artifact_path = Path(str(raw.get("artifact_path") or ""))
        if status not in {"passed", "pass", "ok"}:
            failures.append(f"adequacy_status_not_passed:{name}")
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            failures.append(f"adequacy_artifact_id_missing:{name}")
        if not _is_sha256(artifact_hash):
            failures.append(f"adequacy_artifact_hash_invalid:{name}")
        if not artifact_path.is_file():
            failures.append(f"adequacy_artifact_path_missing:{name}")
        elif artifact_hash != _hash_file(artifact_path):
            failures.append(f"adequacy_artifact_hash_mismatch:{name}")
        normalized[name] = dict(raw)

    thresholds = hidden.get("acceptance_thresholds")
    if not isinstance(thresholds, Mapping):
        return normalized, [*failures, "hidden_acceptance_thresholds_missing"]
    prior_limit = thresholds.get("prior_variant_max_shift_paper_sigma")
    prior_metrics = (checks.get("prior_sensitivity") or {}).get("metrics")
    prior_shift = (
        prior_metrics.get("max_parameter_shift_paper_sigma")
        if isinstance(prior_metrics, Mapping)
        else None
    )
    if not isinstance(prior_limit, (int, float)) or not isinstance(
        prior_shift, (int, float)
    ) or float(prior_shift) > float(prior_limit):
        failures.append("prior_sensitivity_threshold_failed")

    shift_limit = thresholds.get("systematics_variant_max_shift_paper_sigma")
    interval_limit = thresholds.get(
        "systematics_variant_max_interval_change_fraction"
    )
    systematics_metrics = (checks.get("systematics_robustness") or {}).get("metrics")
    variants = (
        systematics_metrics.get("variants")
        if isinstance(systematics_metrics, Mapping)
        else None
    )
    if (
        not isinstance(variants, list)
        or not variants
        or not isinstance(shift_limit, (int, float))
        or not isinstance(interval_limit, (int, float))
    ):
        failures.append("systematics_variant_metrics_missing")
    else:
        for index, variant in enumerate(variants):
            shift = (
                variant.get("max_parameter_shift_paper_sigma")
                if isinstance(variant, Mapping)
                else None
            )
            interval_change = (
                variant.get("max_interval_change_fraction")
                if isinstance(variant, Mapping)
                else None
            )
            if not (
                isinstance(shift, (int, float))
                and isinstance(interval_change, (int, float))
                and (
                    float(shift) <= float(shift_limit)
                    or float(interval_change) <= float(interval_limit)
                )
            ):
                failures.append(f"systematics_variant_threshold_failed:{index}")

    expected_injections = thresholds.get("injection_count")
    expected_coverage = thresholds.get("injection_joint_coverage")
    bias_limit = thresholds.get("injection_mean_max_standardized_bias")
    recovery_metrics = (checks.get("simulation_recovery") or {}).get("metrics")
    injections = (
        recovery_metrics.get("injections")
        if isinstance(recovery_metrics, Mapping)
        else None
    )
    mean_bias = (
        recovery_metrics.get("mean_standardized_bias")
        if isinstance(recovery_metrics, Mapping)
        else None
    )
    if (
        not isinstance(expected_injections, int)
        or not isinstance(expected_coverage, (int, float))
        or not isinstance(bias_limit, (int, float))
        or not isinstance(injections, list)
        or len(injections) != expected_injections
        or not isinstance(mean_bias, (int, float))
        or float(mean_bias) >= float(bias_limit)
    ):
        failures.append("simulation_recovery_summary_failed")
    elif any(
        not isinstance(item, Mapping)
        or item.get("truth_inside_joint_region") is not True
        or not isinstance(item.get("joint_coverage"), (int, float))
        or float(item["joint_coverage"]) < float(expected_coverage)
        for item in injections
    ):
        failures.append("simulation_recovery_injection_failed")

    reproduction_metrics = (checks.get("independent_reproduction") or {}).get(
        "metrics"
    )
    if not isinstance(reproduction_metrics, Mapping):
        failures.append("independent_reproduction_metrics_missing")
    else:
        independent_run_id = str(
            reproduction_metrics.get("independent_run_id") or ""
        ).strip()
        environment_fingerprint = reproduction_metrics.get("environment_fingerprint")
        postprocessor_path = Path(
            str(reproduction_metrics.get("postprocessor_report_path") or "")
        )
        postprocessor_hash = reproduction_metrics.get("postprocessor_report_hash")
        if not independent_run_id or independent_run_id == run_id:
            failures.append("independent_reproduction_run_not_distinct")
        if not _is_sha256(environment_fingerprint):
            failures.append("independent_reproduction_environment_unbound")
        if not postprocessor_path.is_file():
            failures.append("independent_postprocessor_report_missing")
        elif postprocessor_hash != _hash_file(postprocessor_path):
            failures.append("independent_postprocessor_report_hash_mismatch")
    return normalized, failures


def _strict_adequacy_artifacts(
    payload: Mapping[str, Any],
    *,
    run_id: str,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Load the six signed ResearchAlpha adequacy aggregates fail-closed."""

    reasons: list[str] = []
    if payload.get("schema_version") != 1 or payload.get("artifact_type") != (
        "w0wa_research_alpha_adequacy_bundle"
    ):
        reasons.append("adequacy_bundle_schema_invalid")
    if payload.get("run_id") != run_id:
        reasons.append("adequacy_bundle_run_id_mismatch")
    if payload.get("protocol_amendment_sha256") != (
        TRUSTED_PROTOCOL_AMENDMENT_SHA256
    ):
        reasons.append("adequacy_bundle_protocol_amendment_mismatch")
    if not _verify_self_hash(payload, "bundle_sha256"):
        reasons.append("adequacy_bundle_self_hash_invalid")
    raw_checks = payload.get("research_alpha_adequacy_artifacts")
    if not isinstance(raw_checks, Mapping) or set(raw_checks) != set(
        REQUIRED_MODEL_ADEQUACY_CHECKS
    ):
        reasons.append("adequacy_bundle_check_set_invalid")
        return {}, reasons
    normalized: dict[str, dict[str, Any]] = {}
    paths: set[str] = set()
    hashes: set[str] = set()
    for name in REQUIRED_MODEL_ADEQUACY_CHECKS:
        raw = raw_checks.get(name)
        if not isinstance(raw, Mapping):
            reasons.append(f"adequacy_bundle_artifact_invalid:{name}")
            continue
        path = Path(str(raw.get("path") or "")).expanduser().resolve()
        if not path.is_file():
            reasons.append(f"adequacy_bundle_artifact_missing:{name}")
            continue
        record = {
            "path": str(path),
            "sha256": _hash_file(path),
            "size_bytes": path.stat().st_size,
        }
        if dict(raw) != record:
            reasons.append(f"adequacy_bundle_artifact_drift:{name}")
            continue
        if record["path"] in paths or record["sha256"] in hashes:
            reasons.append("adequacy_bundle_artifacts_not_distinct")
            continue
        paths.add(record["path"])
        hashes.add(record["sha256"])
        normalized[name] = record
    return normalized, reasons


def _canonical_artifact_record(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"required artifact missing: {resolved}")
    digest = _hash_file(resolved)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(f"artifact hash drifted: {resolved}")
    return {
        "path": str(resolved),
        "sha256": digest,
        "size_bytes": resolved.stat().st_size,
    }


def _write_named_hash_receipt(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    hash_field: str,
) -> dict[str, Any]:
    body = _with_self_hash(payload, hash_field)
    _write_json(path, body)
    return _canonical_artifact_record(path)


def _write_attestation_artifact(
    path: str | Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    _write_json(path, payload)
    return _canonical_artifact_record(path)


def _likelihood_code_artifacts(
    *,
    packages_path: str | Path,
    preflight_artifact: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Bind each likelihood to the common frozen code-closure commitment.

    The preflight artifact independently contains every installed distribution
    file hash and the loaded clipy/ACT runtime tree fingerprints. The compact
    committed manifest binds that large inventory to each likelihood without
    copying thousands of records into the final ResearchAlpha envelope.
    """

    del packages_path, preflight_artifact
    verification = verify_likelihood_code_manifest()
    if verification.get("passed") is not True:
        raise ValueError(
            "likelihood code manifest is not trusted: "
            + "; ".join(verification.get("reasons") or [])
        )
    artifact = _canonical_artifact_record(
        verification["path"],
        expected_sha256=TRUSTED_LIKELIHOOD_CODE_MANIFEST_SHA256,
    )
    return {name: [dict(artifact)] for name in REQUIRED_LIKELIHOODS}


def _research_alpha_inputs(
    *,
    manifest: Mapping[str, Any],
    manifest_path: str | Path,
    hidden: Mapping[str, Any],
    target_hash: str,
    adequacy_evidence: Mapping[str, Mapping[str, Any]],
    protocol_adjudication_path: str | Path,
) -> dict[str, Any]:
    """Build physical, receipt-derived inputs for the strict manifest signer."""

    from app.services.research_alpha_manifest import (
        build_research_alpha_analysis_authority_attestation,
        build_research_alpha_execution_binding,
        build_research_alpha_run_authority_attestation,
    )

    intervals = ((manifest.get("posterior") or {}).get("intervals_68") or {})
    target_records = hidden.get("targets") or []
    results: list[dict[str, Any]] = []
    target_sigma: dict[str, float] = {}
    report_to_public: dict[str, str] = {}
    for target in target_records:
        report_name = _normalise_target_name(target.get("name"))
        if report_name is None or report_name not in intervals:
            raise ValueError(f"signed result missing: {target.get('name')}")
        public_name = str(target.get("name"))
        report_to_public[report_name] = public_name
        reported = table3_reported_interval(report_name, intervals[report_name])
        results.append(
            {
                "name": public_name,
                "center": reported["center"],
                "lower_68": reported["lower_68"],
                "upper_68": reported["upper_68"],
                "uncertainty_minus": reported["uncertainty_minus"],
                "uncertainty_plus": reported["uncertainty_plus"],
            }
        )
        target_sigma[report_name] = min(
            float(target["uncertainty_minus"]),
            float(target["uncertainty_plus"]),
        )

    raw_diagnostics = (manifest.get("posterior") or {}).get("diagnostics") or {}
    raw_parameters = raw_diagnostics.get("parameters") or {}
    sampled_parameters = list(
        (manifest.get("research_alpha_binding") or {}).get("sampled_parameters")
        or raw_diagnostics.get("sampled_parameters")
        or []
    )
    if not sampled_parameters:
        raise ValueError("sampled parameter binding missing")
    per_parameter: dict[str, dict[str, float]] = {}
    for raw_name, record in raw_parameters.items():
        if not isinstance(record, Mapping):
            continue
        mcse = float(record.get("mcse_mean", math.inf))
        reference_sigma = target_sigma.get(str(raw_name))
        reference_kind = "paper_sigma"
        if reference_sigma is None:
            reference_sigma = float(record.get("posterior_std", math.nan))
            reference_kind = "posterior_sd"
        diagnostic_record = {
            "rhat": float(record.get("rank_normalized_rhat", math.inf)),
            "ess_bulk": float(record.get("bulk_ess", 0.0)),
            "mcse_mean": mcse,
            "posterior_std": float(record.get("posterior_std", math.nan)),
            "mcse_reference_kind": reference_kind,
            "mcse_reference_value": reference_sigma,
            "mcse_over_reference_sigma": (
                mcse / reference_sigma
                if math.isfinite(reference_sigma) and reference_sigma > 0
                else math.inf
            ),
        }
        per_parameter[str(raw_name)] = diagnostic_record
        public_name = report_to_public.get(str(raw_name))
        if public_name is not None:
            per_parameter[public_name] = dict(diagnostic_record)
    result_names = {item["name"] for item in results}
    if not (set(sampled_parameters) | result_names).issubset(per_parameter):
        raise ValueError("signed diagnostics omit reported parameters")
    run_identity = manifest.get("run_identity") or {}
    run_id = str(run_identity.get("run_id") or "")
    if not run_id:
        raise ValueError("run_id missing")
    chain_files = raw_diagnostics.get("chain_files") or []
    chain_ids = list(run_identity.get("chain_ids") or [])
    chain_seeds = list(run_identity.get("derived_chain_seeds") or [])
    if not (
        len(chain_files)
        == len(chain_ids)
        == len(chain_seeds)
        == REQUIRED_CHAIN_COUNT
    ):
        raise ValueError("four chain identities and seeds are required")

    output_dir = (
        Path(manifest_path).resolve().parent
        / (Path(manifest_path).stem + ".research-alpha-grade")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    chain_artifacts: list[dict[str, Any]] = []
    for index, (raw, chain_id, seed) in enumerate(
        zip(chain_files, chain_ids, chain_seeds), start=1
    ):
        chain = _canonical_artifact_record(
            str((raw or {}).get("path") or ""),
            expected_sha256=str((raw or {}).get("sha256") or ""),
        )
        columns, table = _read_chain_table(chain["path"])
        sidecar = _write_named_hash_receipt(
            output_dir / f"chain-{index}.attestation.json",
            {
                "schema_version": 1,
                "kind": "research_alpha_chain",
                "run_id": run_id,
                "chain_id": str(chain_id),
                "seed": int(seed),
                "chain_sha256": chain["sha256"],
                "columns": columns,
                "n_draws": int(table.shape[0]),
            },
            hash_field="self_hash",
        )
        chain_artifacts.append(
            {
                "chain_id": str(chain_id),
                "seed": int(seed),
                **chain,
                "attestation": sidecar,
            }
        )

    sampled_artifact = _write_named_hash_receipt(
        output_dir / "sampled-parameters.json",
        {
            "schema_version": 1,
            "kind": "sampled_parameters",
            "run_id": run_id,
            "sampled_parameters": sampled_parameters,
        },
        hash_field="self_hash",
    )
    config = manifest.get("configuration") or {}
    config_artifact = _canonical_artifact_record(
        str(config.get("path") or ""),
        expected_sha256=str(config.get("sha256") or ""),
    )
    data_inventory = manifest.get("data") or {}
    packages_path = Path(str(data_inventory.get("packages_path") or "")).resolve()
    data_artifacts: dict[str, list[dict[str, Any]]] = {}
    for name, group in (data_inventory.get("groups") or {}).items():
        records: list[dict[str, Any]] = []
        for raw in (group or {}).get("files") or []:
            logical_path = str(raw.get("path") or "")
            records.append(
                {
                    **_canonical_artifact_record(
                        packages_path / logical_path,
                        expected_sha256=str(raw.get("sha256") or ""),
                    ),
                    "logical_path": logical_path,
                }
            )
        if records:
            data_artifacts[str(name)] = records

    workflow = manifest.get("workflow_receipts") or {}
    preflight_binding = workflow.get("preflight") or {}
    generation_binding = workflow.get("generation") or {}
    preflight_artifact = _canonical_artifact_record(
        str(preflight_binding.get("path") or ""),
        expected_sha256=str(preflight_binding.get("sha256") or ""),
    )
    generation_artifact = _canonical_artifact_record(
        str(generation_binding.get("path") or ""),
        expected_sha256=str(generation_binding.get("sha256") or ""),
    )
    likelihood_artifacts = _likelihood_code_artifacts(
        packages_path=packages_path,
        preflight_artifact=preflight_artifact,
    )
    run_receipt_binding = ((manifest.get("posterior") or {}).get("attestation") or {})
    canonical_run_receipt = _canonical_artifact_record(
        str(run_receipt_binding.get("path") or ""),
        expected_sha256=str(run_receipt_binding.get("sha256") or ""),
    )
    amendment_artifact = _canonical_artifact_record(
        PROTOCOL_AMENDMENT_PATH,
        expected_sha256=TRUSTED_PROTOCOL_AMENDMENT_SHA256,
    )
    script_dir = Path(__file__).resolve().parent
    code_artifacts = [
        _canonical_artifact_record(Path(__file__).resolve()),
        _canonical_artifact_record(
            BACKEND_ROOT / "app/services/w0wa_exact_contract.py"
        ),
        _canonical_artifact_record(
            BACKEND_ROOT / "app/services/research_alpha_manifest.py"
        ),
        _canonical_artifact_record(
            BACKEND_ROOT / "app/services/research_alpha_attestation.py"
        ),
        _canonical_artifact_record(
            BACKEND_ROOT / "app/services/research_alpha_evaluator.py"
        ),
        _canonical_artifact_record(script_dir / "w0wa_exact_requirements.txt"),
        _canonical_artifact_record(script_dir / "w0wa_exact_reference_cases.json"),
        _canonical_artifact_record(script_dir / "w0wa_exact_data_manifest.json"),
        _canonical_artifact_record(
            TRUSTED_LIKELIHOOD_CODE_MANIFEST_PATH,
            expected_sha256=TRUSTED_LIKELIHOOD_CODE_MANIFEST_SHA256,
        ),
        _canonical_artifact_record(
            TRUSTED_WHEEL_MANIFEST_PATH,
            expected_sha256=TRUSTED_WHEEL_MANIFEST_SHA256,
        ),
    ]

    run_authority = build_research_alpha_run_authority_attestation(
        run_id=run_id,
        chain_artifacts=chain_artifacts,
        config_artifact=config_artifact,
        data_artifacts=data_artifacts,
        likelihood_artifacts=likelihood_artifacts,
        sampled_parameters_artifact=sampled_artifact,
        canonical_run_receipt_artifact=canonical_run_receipt,
        preflight_artifact=preflight_artifact,
        generation_artifact=generation_artifact,
        code_artifacts=code_artifacts,
        protocol_amendment_artifact=amendment_artifact,
    )
    run_attestation_artifact = _write_attestation_artifact(
        output_dir / "research-alpha-run.json", run_authority
    )

    diagnostics = {
        "status": "passed",
        "metrics": {
            "rhat_method": "rank_normalized",
            "ess_method": "bulk",
            "mcse_reference": "per_parameter",
            "mcse_reference_policy": {
                "reported_parameters": "preregistered_paper_sigma",
                "unreported_sampled_parameters": "same_closed_run_posterior_sd",
                "maximum_ratio_exclusive": 0.05,
            },
            "environment_fingerprint": (
                (run_receipt_binding.get("payload") or {}).get(
                    "environment_fingerprint"
                )
            ),
            "n_independent_chains": REQUIRED_CHAIN_COUNT,
            "chain_length_balance": {
                "alignment": raw_diagnostics.get("method", {}).get("alignment"),
                "minimum_alignment_fraction_inclusive": (
                    MIN_DIAGNOSTIC_ALIGNMENT_FRACTION
                ),
                "alignment_fraction_per_chain": raw_diagnostics.get(
                    "diagnostic_alignment_fraction_per_chain"
                ),
                "maximum_discarded_fraction": raw_diagnostics.get(
                    "maximum_diagnostic_discarded_fraction"
                ),
                "passed": raw_diagnostics.get("chain_length_balance_passed"),
            },
            "critical_parameters": sampled_parameters,
            "per_parameter": per_parameter,
        },
    }
    analysis_artifact = _canonical_artifact_record(manifest_path)
    offline_grade_artifact = _write_named_hash_receipt(
        output_dir / "primary-offline-grade.json",
        {
            "schema_version": 1,
            "artifact_type": "research_alpha_primary_offline_grade",
            "status": "passed",
            "profile_id": EXACT_PROFILE_ID,
            "run_id": run_id,
            "target_hash": target_hash,
            "analysis_receipt_sha256": analysis_artifact["sha256"],
            "chain_sha256": [item["sha256"] for item in chain_artifacts],
            "sampled_parameters": sampled_parameters,
            "numbers": results,
            "diagnostics": diagnostics,
        },
        hash_field="receipt_sha256",
    )
    analysis_authority = build_research_alpha_analysis_authority_attestation(
        run_id=run_id,
        run_attestation_artifact=run_attestation_artifact,
        analysis_receipt_artifact=analysis_artifact,
        offline_grade_receipt_artifact=offline_grade_artifact,
        analysis_code_artifact=_canonical_artifact_record(Path(__file__).resolve()),
        chain_artifacts=chain_artifacts,
    )
    analysis_attestation_artifact = _write_attestation_artifact(
        output_dir / "research-alpha-analysis.json", analysis_authority
    )
    protocol_adjudication_artifact = _canonical_artifact_record(
        protocol_adjudication_path
    )
    base = {
        "run_id": run_id,
        "target_hash": target_hash,
        "chain_artifacts": chain_artifacts,
        "config_artifact": config_artifact,
        "data_artifacts": data_artifacts,
        "likelihood_artifacts": likelihood_artifacts,
        "sampled_parameters_artifact": sampled_artifact,
        "run_attestation_artifact": run_attestation_artifact,
        "analysis_attestation_artifact": analysis_attestation_artifact,
        "protocol_adjudication_artifact": protocol_adjudication_artifact,
        "results": results,
        "diagnostics": diagnostics,
    }
    execution = build_research_alpha_execution_binding(**base)
    claim_support: list[dict[str, Any]] = []
    for index, item in enumerate(results, start=1):
        support_artifact = _write_named_hash_receipt(
            output_dir / f"claim-support-{index}.json",
            {
                "schema_version": 1,
                "kind": "research_alpha_claim_support",
                "run_id": run_id,
                "execution_fingerprint": execution["execution_fingerprint"],
                "results": {item["name"]: dict(item)},
                "source_artifacts": [analysis_artifact, offline_grade_artifact],
            },
            hash_field="self_hash",
        )
        claim_support.append(
            {
                "claim": f"{item['name']} 68% interval reproduction",
                "parameter": item["name"],
                "result_path": f"results.{item['name']}",
                **support_artifact,
            }
        )
    directions = hidden.get("directions") or {}
    return {
        **base,
        "adequacy_evidence_by_check": dict(adequacy_evidence),
        "claim_support_paths": claim_support,
        "datasets": [
            "DESI DR1 BAO",
            "PantheonPlus",
            "Planck PR3",
            "ACT DR6+Planck PR4 lensing",
        ],
        "methods": [
            "known-target preregistered exact full-likelihood interval reproduction"
        ],
        "models": ["w0waCDM"],
        "result_direction_terms": [str(value) for value in directions.values()],
    }


def _recompute_analysis_from_chain_artifacts(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Recalculate diagnostics and intervals from the four physical chains.

    The analysis self-hash is only corruption detection.  Offline grading must
    independently read the attested chain bytes and reproduce every
    chain-derived field before it may inspect numerical target agreement.
    """

    reasons: list[str] = []
    posterior = manifest.get("posterior")
    if not isinstance(posterior, Mapping):
        return {
            "passed": False,
            "reasons": ["analysis_chain_posterior_missing"],
            "diagnostics_fingerprint": None,
            "intervals_fingerprint": None,
        }
    recorded_diagnostics = posterior.get("diagnostics")
    recorded_intervals = posterior.get("intervals_68")
    if not isinstance(recorded_diagnostics, Mapping):
        reasons.append("analysis_chain_diagnostics_missing")
        recorded_diagnostics = {}
    if not isinstance(recorded_intervals, Mapping):
        reasons.append("analysis_chain_intervals_missing")
        recorded_intervals = {}
    chain_files = recorded_diagnostics.get("chain_files") or []
    if not isinstance(chain_files, list) or len(chain_files) != REQUIRED_CHAIN_COUNT:
        reasons.append("analysis_chain_file_set_invalid")
        return {
            "passed": False,
            "reasons": reasons,
            "diagnostics_fingerprint": None,
            "intervals_fingerprint": None,
        }

    prefix_text: str | None = None
    for index, item in enumerate(chain_files, start=1):
        if not isinstance(item, Mapping):
            reasons.append(f"analysis_chain_record_invalid:{index}")
            continue
        path_text = str(item.get("path") or "")
        suffix = f".{index}.txt"
        if not path_text.endswith(suffix):
            reasons.append(f"analysis_chain_path_sequence_invalid:{index}")
            continue
        candidate_prefix = path_text[: -len(suffix)]
        if prefix_text is None:
            prefix_text = candidate_prefix
        elif candidate_prefix != prefix_text:
            reasons.append("analysis_chain_prefixes_not_identical")
        chain_path = Path(path_text)
        if not chain_path.is_file():
            reasons.append(f"analysis_chain_file_missing:{index}")
        elif item.get("sha256") != _hash_file(chain_path):
            reasons.append(f"analysis_chain_file_hash_mismatch:{index}")
    if prefix_text is None or reasons:
        return {
            "passed": False,
            "reasons": reasons,
            "diagnostics_fingerprint": None,
            "intervals_fingerprint": None,
        }

    burn_fraction = (recorded_diagnostics.get("method") or {}).get(
        "burn_fraction"
    )
    if (
        isinstance(burn_fraction, bool)
        or not isinstance(burn_fraction, (int, float))
        or not math.isfinite(float(burn_fraction))
        or float(burn_fraction) != DEFAULT_BURN_FRACTION
    ):
        reasons.append("analysis_chain_burn_fraction_not_canonical")
        return {
            "passed": False,
            "reasons": reasons,
            "diagnostics_fingerprint": None,
            "intervals_fingerprint": None,
        }

    prefix = Path(prefix_text)
    updated_config_path = _prefix_file(prefix, ".updated.yaml")
    try:
        updated_config = _load_yaml(updated_config_path)
        recomputed_diagnostics, analysis_data = diagnose_chains(
            prefix,
            updated_config=updated_config,
            burn_fraction=float(burn_fraction),
        )
        recomputed_intervals = posterior_intervals(analysis_data)
    except Exception as exc:
        reasons.append(
            f"analysis_chain_recomputation_failed:{type(exc).__name__}:{exc}"
        )
        return {
            "passed": False,
            "reasons": reasons,
            "diagnostics_fingerprint": None,
            "intervals_fingerprint": None,
        }

    if _hash_object(recomputed_diagnostics) != _hash_object(recorded_diagnostics):
        reasons.append("analysis_chain_diagnostics_recompute_mismatch")
    if _hash_object(recomputed_intervals) != _hash_object(recorded_intervals):
        reasons.append("analysis_chain_intervals_recompute_mismatch")
    recomputed_chain_hashes = [
        str(item.get("sha256"))
        for item in recomputed_diagnostics.get("chain_files") or []
    ]
    binding = manifest.get("research_alpha_binding") or {}
    if binding.get("chain_sha256") != recomputed_chain_hashes:
        reasons.append("analysis_chain_research_alpha_hash_binding_mismatch")
    if binding.get("sampled_parameters") != recomputed_diagnostics.get(
        "sampled_parameters"
    ):
        reasons.append("analysis_chain_sampled_parameter_binding_mismatch")
    expected_chain_ids = [
        f"chain-{index}:{digest.split(':')[-1][:16]}"
        for index, digest in enumerate(recomputed_chain_hashes, start=1)
    ]
    if (manifest.get("run_identity") or {}).get("chain_ids") != expected_chain_ids:
        reasons.append("analysis_chain_identity_binding_mismatch")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "diagnostics_fingerprint": _hash_object(recomputed_diagnostics),
        "intervals_fingerprint": _hash_object(recomputed_intervals),
        "chain_sha256": recomputed_chain_hashes,
    }


def grade_exact_analysis(
    *,
    manifest_path: str | Path,
    hidden_answer_path: str | Path,
    target_hash: str,
    adequacy_manifest_path: str | Path,
    protocol_adjudication_path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply the preregistered target only at offline grading time."""

    failures: list[str] = []
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        manifest = {}
        failures.append(f"analysis_manifest_unreadable:{type(exc).__name__}")
    try:
        hidden = json.loads(Path(hidden_answer_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        hidden = {}
        failures.append(f"hidden_answer_unreadable:{type(exc).__name__}")
    canonical_target_hash = _hash_object(hidden) if hidden else None
    if canonical_target_hash != target_hash:
        failures.append("hidden_answer_commitment_mismatch")
    if target_hash != PREREGISTERED_TARGET_COMMITMENT:
        failures.append("target_commitment_not_preregistered")
    if manifest.get("artifact_type") != "w0wa_exact_analysis":
        failures.append("analysis_artifact_type_mismatch")
    if manifest.get("profile_id") != EXACT_PROFILE_ID:
        failures.append("analysis_profile_mismatch")
    if manifest.get("claim_scope") != EXACT_CLAIM_SCOPE:
        failures.append("analysis_claim_scope_mismatch")
    if manifest.get("target_commitment") != PREREGISTERED_TARGET_COMMITMENT:
        failures.append("analysis_target_commitment_mismatch")
    if manifest.get("protocol_integrity") != PROTOCOL_INTEGRITY:
        failures.append("analysis_protocol_integrity_mismatch")
    if manifest.get("paper_fidelity_amendment") != PAPER_FIDELITY_AMENDMENT:
        failures.append("analysis_paper_fidelity_amendment_mismatch")
    if EXACT_ENVIRONMENT_REVISION.get("status") != "VALIDATED_FOR_FORMAL_EXECUTION":
        failures.append(
            "environment_revision_pending_fresh_preflight_and_science_regression"
        )
    current_amendment = protocol_amendment_record()
    if current_amendment.get("valid") is not True:
        failures.append("protocol_amendment_hash_invalid")
    if manifest.get("protocol_amendment_artifact") != current_amendment:
        failures.append("analysis_protocol_amendment_drift")
    run_id = str(((manifest.get("run_identity") or {}).get("run_id") or ""))
    # The implementation prompt disclosed every target value. A local HMAC can
    # never waive this factual deviation; only a separately configured external
    # Ed25519 authority can explicitly adjudicate the known-target design.
    protocol_adjudication = verify_external_protocol_adjudication(
        protocol_adjudication_path,
        expected_run_id=run_id,
        expected_target_hash=target_hash,
    )
    if protocol_adjudication.get("passed") is not True:
        failures.extend(
            f"protocol_adjudication:{reason}"
            for reason in protocol_adjudication.get("reasons") or []
        )
    if manifest.get("status") != "ANALYZED" or manifest.get(
        "evidence_ready_for_offline_grading"
    ) is not True:
        failures.append("analysis_not_ready_for_grading")
    if not _verify_self_hash(manifest, "manifest_sha256"):
        failures.append("analysis_manifest_hash_invalid")
    chain_recomputation = _recompute_analysis_from_chain_artifacts(manifest)
    failures.extend(chain_recomputation.get("reasons") or [])
    failures.extend(_validate_support_records(manifest.get("claim_support_paths")))

    try:
        adequacy = json.loads(
            Path(adequacy_manifest_path).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        adequacy = {}
        failures.append(f"adequacy_manifest_unreadable:{type(exc).__name__}")
    adequacy_evidence, adequacy_failures = _strict_adequacy_artifacts(
        adequacy, run_id=run_id
    )
    failures.extend(adequacy_failures)

    thresholds = hidden.get("acceptance_thresholds")
    if not isinstance(thresholds, Mapping):
        thresholds = {}
        failures.append("hidden_acceptance_thresholds_missing")
    center_sigma_limit = _numeric_threshold(thresholds, ("center", "sigma"))
    width_error_limit = _numeric_threshold(thresholds, ("interval", "width"))
    mcse_sigma_limit = _numeric_threshold(thresholds, ("mcse", "sigma"))
    if center_sigma_limit is None:
        failures.append("center_sigma_threshold_missing")
    if width_error_limit is None:
        failures.append("interval_width_threshold_missing")
    if mcse_sigma_limit is None:
        failures.append("mcse_sigma_threshold_missing")

    targets = hidden.get("targets")
    if not isinstance(targets, list):
        targets = []
        failures.append("hidden_targets_list_missing")
    intervals = ((manifest.get("posterior") or {}).get("intervals_68") or {})
    parameter_checks: dict[str, Any] = {}
    for target in targets:
        if not isinstance(target, Mapping):
            failures.append("hidden_target_record_invalid")
            continue
        report_name = _normalise_target_name(target.get("name"))
        if report_name is None or report_name not in intervals:
            failures.append(f"target_parameter_missing:{target.get('name')}")
            continue
        required_fields = (
            "center",
            "lower_68",
            "upper_68",
            "uncertainty_minus",
            "uncertainty_plus",
        )
        if not all(
            isinstance(target.get(field), (int, float)) for field in required_fields
        ):
            failures.append(f"target_interval_incomplete:{target.get('name')}")
            continue
        actual = intervals[report_name]
        try:
            reported = table3_reported_interval(report_name, actual)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            failures.append(
                f"actual_interval_incomplete:{target.get('name')}:"
                f"{type(exc).__name__}"
            )
            continue
        actual_center = reported["center"]
        actual_lower = reported["lower_68"]
        actual_upper = reported["upper_68"]
        actual_uncertainty_minus = reported["uncertainty_minus"]
        actual_uncertainty_plus = reported["uncertainty_plus"]
        expected_center = float(target["center"])
        expected_lower = float(target["lower_68"])
        expected_upper = float(target["upper_68"])
        expected_uncertainty_minus = float(target["uncertainty_minus"])
        expected_uncertainty_plus = float(target["uncertainty_plus"])
        if (
            expected_uncertainty_minus <= 0
            or expected_uncertainty_plus <= 0
            or not math.isclose(
                expected_center - expected_lower,
                expected_uncertainty_minus,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                expected_upper - expected_center,
                expected_uncertainty_plus,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            failures.append(f"hidden_target_interval_misaligned:{target.get('name')}")
            continue
        sigma = float(
            target[
                "uncertainty_minus"
                if actual_center < expected_center
                else "uncertainty_plus"
            ]
        )
        expected_width = expected_upper - expected_lower
        actual_width = actual_upper - actual_lower
        center_error_sigma = abs(actual_center - expected_center) / sigma
        width_relative_error = abs(actual_width - expected_width) / expected_width
        uncertainty_minus_relative_error = (
            abs(actual_uncertainty_minus - expected_uncertainty_minus)
            / expected_uncertainty_minus
        )
        uncertainty_plus_relative_error = (
            abs(actual_uncertainty_plus - expected_uncertainty_plus)
            / expected_uncertainty_plus
        )
        lower_endpoint_error_side_sigma = (
            abs(actual_lower - expected_lower) / expected_uncertainty_minus
        )
        upper_endpoint_error_side_sigma = (
            abs(actual_upper - expected_upper) / expected_uncertainty_plus
        )
        mcse_paper_sigma = float(reported["mcse_mean"]) / sigma
        endpoint_limit = (
            center_sigma_limit + width_error_limit
            if center_sigma_limit is not None and width_error_limit is not None
            else None
        )
        field_checks = {
            "center": bool(
                center_sigma_limit is not None
                and center_error_sigma <= center_sigma_limit
            ),
            "lower_68": bool(
                endpoint_limit is not None
                and lower_endpoint_error_side_sigma <= endpoint_limit
            ),
            "upper_68": bool(
                endpoint_limit is not None
                and upper_endpoint_error_side_sigma <= endpoint_limit
            ),
            "uncertainty_minus": bool(
                width_error_limit is not None
                and uncertainty_minus_relative_error <= width_error_limit
            ),
            "uncertainty_plus": bool(
                width_error_limit is not None
                and uncertainty_plus_relative_error <= width_error_limit
            ),
            "total_width": bool(
                width_error_limit is not None
                and width_relative_error <= width_error_limit
            ),
            "mcse": bool(
                mcse_sigma_limit is not None
                and mcse_paper_sigma < mcse_sigma_limit
            ),
        }
        passed = bool(
            field_checks and all(field_checks.values())
        )
        if not passed:
            failures.append(f"numeric_acceptance_failed:{target.get('name')}")
        parameter_checks[str(target.get("name"))] = {
            "report_parameter": report_name,
            "actual_center": actual_center,
            "actual_lower_68": actual_lower,
            "actual_upper_68": actual_upper,
            "actual_uncertainty_minus": actual_uncertainty_minus,
            "actual_uncertainty_plus": actual_uncertainty_plus,
            "reporting_statistic": reported["reporting_statistic"],
            "center_error_paper_sigma": center_error_sigma,
            "interval_width_relative_error": width_relative_error,
            "lower_endpoint_error": actual_lower - expected_lower,
            "upper_endpoint_error": actual_upper - expected_upper,
            "lower_endpoint_error_side_sigma": lower_endpoint_error_side_sigma,
            "upper_endpoint_error_side_sigma": upper_endpoint_error_side_sigma,
            "uncertainty_minus_relative_error": uncertainty_minus_relative_error,
            "uncertainty_plus_relative_error": uncertainty_plus_relative_error,
            "mcse_paper_sigma": mcse_paper_sigma,
            "field_checks": field_checks,
            "passed": passed,
        }

    directions = hidden.get("directions")
    if not isinstance(directions, Mapping):
        failures.append("hidden_directions_missing")
        directions = {}
    direction_checks: dict[str, Any] = {}
    for target_name, semantic in directions.items():
        report_name = _normalise_target_name(target_name)
        if report_name not in intervals:
            failures.append(f"direction_parameter_missing:{target_name}")
            continue
        value = float(intervals[report_name]["mean"])
        compact = str(semantic).replace(" ", "")
        if ">-1" in compact:
            passed = value > -1.0
        elif "<0" in compact:
            passed = value < 0.0
        else:
            passed = False
            failures.append(f"direction_semantics_unrecognized:{target_name}")
        if not passed:
            failures.append(f"direction_acceptance_failed:{target_name}")
        direction_checks[str(target_name)] = {
            "semantic": semantic,
            "actual_center": value,
            "passed": passed,
        }

    research_alpha_manifest: dict[str, Any] | None = None
    if not failures:
        try:
            from app.services.research_alpha_manifest import (
                build_research_alpha_manifest,
                validate_research_alpha_manifest,
            )

            research_alpha_manifest = build_research_alpha_manifest(
                **_research_alpha_inputs(
                    manifest=manifest,
                    manifest_path=manifest_path,
                    hidden=hidden,
                    target_hash=target_hash,
                    adequacy_evidence=adequacy_evidence,
                    protocol_adjudication_path=str(protocol_adjudication_path),
                )
            )
            validation = validate_research_alpha_manifest(
                research_alpha_manifest,
                expected_run_id=run_id,
            )
            if validation.get("valid") is not True:
                failures.append(
                    "research_alpha_manifest_invalid:"
                    + ",".join(validation.get("reasons") or [])
                )
                research_alpha_manifest = None
        except (ImportError, TypeError, ValueError) as exc:
            failures.append(
                f"research_alpha_manifest_build_failed:{type(exc).__name__}:{exc}"
            )
            research_alpha_manifest = None

    status = "A_READY_PENDING_EXTERNAL_REVIEW" if not failures else "WITHHELD"
    result = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "w0wa_exact_grade",
        "created_at": _utc_now(),
        "profile_id": EXACT_PROFILE_ID,
        "paper": EXACT_PAPER,
        "claim_scope": EXACT_CLAIM_SCOPE,
        "protocol_integrity": dict(PROTOCOL_INTEGRITY),
        "paper_fidelity_amendment": dict(PAPER_FIDELITY_AMENDMENT),
        "environment_revision": copy.deepcopy(EXACT_ENVIRONMENT_REVISION),
        "protocol_amendment_artifact": current_amendment,
        "protocol_adjudication": {
            **protocol_adjudication,
            "status": (
                "EXTERNALLY_ACCEPTED"
                if protocol_adjudication.get("passed") is True
                else "REQUIRED_NOT_VERIFIED"
            ),
            "required_authority": "separately_configured_external_ed25519",
            "local_hmac_waiver_permitted": False,
        },
        "status": status,
        "A_ready_count": 1 if status == "A_READY_PENDING_EXTERNAL_REVIEW" else 0,
        "strict_A_count": 0,
        "external_review_complete": False,
        "publication_ready": False,
        "target_commitment": target_hash,
        "analysis_manifest_sha256": manifest.get("manifest_sha256"),
        "chain_recomputation": chain_recomputation,
        "adequacy_manifest_sha256": (
            _hash_file(adequacy_manifest_path)
            if Path(adequacy_manifest_path).is_file()
            else None
        ),
        "parameter_checks": parameter_checks,
        "direction_checks": direction_checks,
        "failures": failures,
        "prohibited_conclusions": [
            "LambdaCDM_rejected",
            "dynamic_dark_energy_discovered",
            "model_preference_sigma",
            "Bayes_factor_preference",
        ],
    }
    if research_alpha_manifest is not None:
        result["research_alpha_manifest"] = research_alpha_manifest
    return _with_self_hash(result, "grade_sha256")


def _default_paths() -> dict[str, Path]:
    script_dir = Path(__file__).resolve().parent
    local_dir = BACKEND_ROOT.parent / ".local" / "w0wa-strict-a-readiness"
    return {
        "canonical": script_dir / "w0wa_desi_cmb_pantheonplus_exact.yaml",
        "free_map_config": local_dir / "w0wa_exact_map.yaml",
        "fixed_map_config": local_dir / "lcdm_exact_map.yaml",
        "dependency_lock": script_dir / "w0wa_exact_requirements.txt",
        "reference_values": script_dir / "w0wa_exact_reference_cases.json",
        "data_manifest": script_dir / "w0wa_exact_data_manifest.json",
        "wheels": local_dir / "wheels",
        "preflight": local_dir / "preflight.json",
        "generation": local_dir / "generation.json",
        "analysis": local_dir / "analysis.json",
        "adequacy": local_dir / "model_adequacy.json",
        "grade": local_dir / "grade.json",
        "hidden_answer": local_dir / "hidden_answer.json",
    }


def _build_parser() -> argparse.ArgumentParser:
    defaults = _default_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser(
        "preflight", help="verify exact config, code, data and reference values"
    )
    preflight.add_argument("--canonical", type=Path, default=defaults["canonical"])
    preflight.add_argument("--packages-path", type=Path, default=Path("packages"))
    preflight.add_argument(
        "--dependency-lock", type=Path, default=defaults["dependency_lock"]
    )
    preflight.add_argument("--wheels-path", type=Path, default=defaults["wheels"])
    preflight.add_argument(
        "--reference-values", type=Path, default=defaults["reference_values"]
    )
    preflight.add_argument(
        "--data-manifest", type=Path, default=defaults["data_manifest"]
    )
    preflight.add_argument("--output", type=Path, default=defaults["preflight"])

    generate = subparsers.add_parser(
        "generate", help="derive configs only after a valid exact preflight"
    )
    generate.add_argument("--canonical", type=Path, default=defaults["canonical"])
    generate.add_argument(
        "--free-output", type=Path, default=defaults["free_map_config"]
    )
    generate.add_argument(
        "--fixed-output", type=Path, default=defaults["fixed_map_config"]
    )
    generate.add_argument("--packages-path", type=Path, default=Path("packages"))
    generate.add_argument(
        "--preflight-report", type=Path, default=defaults["preflight"]
    )
    generate.add_argument(
        "--adequacy-output-dir",
        type=Path,
        default=defaults["preflight"].parent / "adequacy-configs",
    )
    generate.add_argument("--output", type=Path, default=defaults["generation"])

    run = subparsers.add_parser(
        "run", help="run Cobaya and write a completion attestation"
    )
    run.add_argument("--kind", choices=("chain", "map"), required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--prefix", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--packages-path", type=Path, default=Path("packages"))
    run.add_argument(
        "--cobaya-run",
        default=None,
        help=(
            "optional smoke-only runner override; converged evidence is fixed "
            "to the current interpreter's cobaya.run module"
        ),
    )
    run.add_argument("--mpi", type=int, default=4)
    run.add_argument("--force", action="store_true")
    run.add_argument("--canonical", type=Path, default=defaults["canonical"])
    run.add_argument(
        "--preflight-report", type=Path, default=defaults["preflight"]
    )
    run.add_argument(
        "--generation-report", type=Path, default=defaults["generation"]
    )
    run.add_argument(
        "--evidence-class",
        choices=("formal_candidate", "model_adequacy", "non_citable_smoke"),
        default="formal_candidate",
        help="smoke outputs are permanently rejected by analyze",
    )

    analyze = subparsers.add_parser("analyze", help="analyze the exact formal chains")
    analyze.add_argument("--canonical", type=Path, default=defaults["canonical"])
    analyze.add_argument(
        "--chain-prefix", type=Path, default=Path("cobaya_runs/w0wa_exact_formal")
    )
    analyze.add_argument("--packages-path", type=Path, default=Path("packages"))
    analyze.add_argument(
        "--preflight-report", type=Path, default=defaults["preflight"]
    )
    analyze.add_argument(
        "--generation-report", type=Path, default=defaults["generation"]
    )
    analyze.add_argument(
        "--support-path",
        type=Path,
        action="append",
        default=[],
        help="repeat for every hash-bound claim-support artifact",
    )
    analyze.add_argument("--burn-fraction", type=float, default=DEFAULT_BURN_FRACTION)
    analyze.add_argument("--output", type=Path, default=defaults["analysis"])

    grade = subparsers.add_parser(
        "grade", help="apply the hidden target without granting final A status"
    )
    grade.add_argument("--manifest", type=Path, default=defaults["analysis"])
    grade.add_argument(
        "--hidden-answer", type=Path, default=defaults["hidden_answer"]
    )
    grade.add_argument("--target-hash", required=True)
    grade.add_argument(
        "--adequacy-manifest", type=Path, default=defaults["adequacy"]
    )
    grade.add_argument(
        "--protocol-adjudication",
        type=Path,
        help="independently Ed25519-signed known-target protocol adjudication",
    )
    grade.add_argument("--output", type=Path, default=defaults["grade"])
    return parser


def _require_isolated_exact_cli_runtime() -> dict[str, Any]:
    """Reject every exact stage unless this process itself is isolated.

    Checking only the Cobaya child is insufficient: preflight, generation,
    analysis and grading all import scientific code and therefore belong to the
    same trust boundary.  The policy is evaluated again inside preflight and
    bound into its environment fingerprint.
    """

    policy = _exact_python_import_policy()
    if policy.get("passed") is not True:
        raise ValueError(
            "exact CLI requires `python -I` with an empty PYTHONPATH and a "
            "trusted startup-hook closure: "
            + "; ".join(str(reason) for reason in policy.get("reasons") or [])
        )
    return policy


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        _require_isolated_exact_cli_runtime()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.command == "preflight":
        report = build_preflight_report(
            canonical_config_path=args.canonical,
            packages_path=args.packages_path,
            dependency_lock_path=args.dependency_lock,
            wheels_path=args.wheels_path,
            reference_values_path=args.reference_values,
            data_manifest_path=args.data_manifest,
        )
        _write_json(args.output, report)
        print(f"preflight status: {report['status']}")
        print(f"report: {args.output}")
        return 0 if report["passed"] else 2
    if args.command == "generate":
        try:
            result = build_generation_receipt(
                canonical_config_path=args.canonical,
                free_output_path=args.free_output,
                fixed_output_path=args.fixed_output,
                preflight_report_path=args.preflight_report,
                packages_path=args.packages_path,
                adequacy_output_dir=args.adequacy_output_dir,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        _write_json(args.output, result)
        print(f"generation status: {'PASS' if result['passed'] else 'WITHHELD'}")
        print(f"receipt: {args.output}")
        return 0
    if args.command == "run":
        preflight = verify_preflight_receipt(
            args.preflight_report,
            canonical_config_path=args.canonical,
            packages_path=args.packages_path,
        )
        generation = verify_generation_receipt(
            args.generation_report,
            canonical_config_path=args.canonical,
            preflight_report_path=args.preflight_report,
            packages_path=args.packages_path,
        )
        if not preflight["passed"] or not generation["passed"]:
            print(
                "run withheld: "
                + "; ".join([*preflight["reasons"], *generation["reasons"]]),
                file=sys.stderr,
            )
            return 2
        config_sha256 = _hash_file(args.config)
        generated_configs = (
            ((generation.get("payload") or {}).get("model_adequacy_plan") or {}).get(
                "configs"
            )
            or {}
        )
        generated_hashes = {
            name: (record or {}).get("sha256")
            for name, record in generated_configs.items()
        }
        if args.evidence_class == "formal_candidate" and config_sha256 != _hash_file(
            args.canonical
        ):
            print("run withheld: formal candidate must use canonical config", file=sys.stderr)
            return 2
        if args.evidence_class == "model_adequacy" and config_sha256 not in set(
            generated_hashes.values()
        ):
            print("run withheld: adequacy config is not generation-receipt bound", file=sys.stderr)
            return 2
        if args.evidence_class == "non_citable_smoke" and config_sha256 not in {
            generated_hashes.get("non_citable_smoke"),
            _hash_file(args.canonical),
        }:
            print("run withheld: smoke config is not generation-receipt bound", file=sys.stderr)
            return 2
        try:
            return run_cobaya_with_attestation(
                kind=args.kind,
                config_path=args.config,
                prefix=args.prefix,
                packages_path=args.packages_path,
                cobaya_run=args.cobaya_run,
                mpi_processes=args.mpi,
                force=args.force,
                workflow_receipts={
                    "preflight_sha256": preflight["payload"]["preflight_sha256"],
                    "generation_sha256": generation["payload"]["generation_sha256"],
                    "protocol_amendment_sha256": protocol_amendment_record()[
                        "sha256"
                    ],
                },
                evidence_class=args.evidence_class,
                run_id=args.run_id,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.command == "analyze":
        manifest = build_exact_analysis_manifest(
            canonical_config_path=args.canonical,
            chain_prefix=args.chain_prefix,
            packages_path=args.packages_path,
            preflight_report_path=args.preflight_report,
            generation_report_path=args.generation_report,
            support_paths=args.support_path,
            burn_fraction=args.burn_fraction,
        )
        _write_json(args.output, manifest)
        print(f"analysis status: {manifest['status']}")
        print(f"manifest: {args.output}")
        if manifest["posterior"]["passed"]:
            for name, interval in manifest["posterior"]["intervals_68"].items():
                print(
                    f"  {name}: mean={interval['mean']:.6g}, "
                    f"minimal68=[{interval['minimal_lower_68']:.6g}, "
                    f"{interval['minimal_upper_68']:.6g}], "
                    f"MCSE={interval['mcse_mean']:.3g}"
                )
        return 0 if manifest["evidence_ready_for_offline_grading"] else 2
    if args.command == "grade":
        grade = grade_exact_analysis(
            manifest_path=args.manifest,
            hidden_answer_path=args.hidden_answer,
            target_hash=args.target_hash,
            adequacy_manifest_path=args.adequacy_manifest,
            protocol_adjudication_path=args.protocol_adjudication,
        )
        _write_json(args.output, grade)
        print(f"grade status: {grade['status']}")
        print(f"strict A count: {grade['strict_A_count']}")
        print(f"report: {args.output}")
        return 0 if grade["status"] == "A_READY_PENDING_EXTERNAL_REVIEW" else 2
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
