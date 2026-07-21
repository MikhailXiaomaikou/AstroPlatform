"""File-backed, exact-run Research Alpha evidence manifests.

Only real artifacts can enter this manifest.  Every caller-supplied digest is
recomputed from an existing file before signing, independent chain files bind
their IDs and seeds, and model-adequacy/support evidence must carry the original
execution fingerprint.  A second, final run fingerprint then binds those
downstream artifacts so an external review cannot be replayed after they change.
"""

from __future__ import annotations

import base64
import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import zipfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.services.cosmology_likelihoods.verification import (
    PUBLICATION_MIN_INDEPENDENT_CHAINS,
    PUBLICATION_REQUIRED_ADEQUACY_CHECKS,
    PUBLICATION_RHAT_MAX,
    _assess_model_adequacy,
    build_model_adequacy_attestation,
)
from app.services.research_alpha_attestation import (
    build_scientific_attestation,
    scientific_content_hash,
    signing_key_binding,
    verification_key_for_id,
    verify_scientific_attestation,
)
from app.services.w0wa_exact_contract import (
    CI_FIXTURE_PROFILE_ID,
    EXACT_CLAIM_SCOPE,
    EXACT_EVIDENCE_SIGNING_KEY_ID,
    EXACT_EVIDENCE_SIGNING_KEY_SHA256,
    EXACT_ENVIRONMENT_PENDING_REASON,
    EXACT_ENVIRONMENT_REVISION,
    EXACT_HOST_EXECUTION_TRUST_BOUNDARY,
    EXACT_MAX_READINESS_STATUS,
    EXACT_PROFILE_ID,
    FROZEN_BOOTSTRAP_DISTRIBUTIONS,
    GENERATED_BYTECODE_CACHE_POLICY,
    GENERATION_SCHEMA,
    PANTHEONPLUS_SOURCE_DATA,
    PANTHEONPLUS_STATONLY_COVARIANCE,
    PREFLIGHT_SCHEMA,
    PREREGISTERED_PAPER_UNCERTAINTIES,
    PREREGISTERED_TARGET_COMMITMENT,
    REQUIRED_ADEQUACY_DATA_GROUPS,
    PROTOCOL_STATUS,
    REQUIRED_DATA_GROUPS,
    REQUIRED_LIKELIHOODS,
    REQUIRED_PACKAGE_VERSIONS,
    REQUIRED_SAMPLED_PARAMETERS,
    REQUIRED_SOURCE_STATE_PATHS,
    REQUIRED_WHEEL_SHA256,
    TRUSTED_CANONICAL_CONFIG_SHA256,
    TRUSTED_ADEQUACY_ANALYZER_CODE_SHA256,
    TRUSTED_ADEQUACY_RUNNER_CODE_SHA256,
    TRUSTED_CODE_SHA256,
    TRUSTED_ADEQUACY_DATA_INVENTORY_SHA256,
    TRUSTED_DATA_INVENTORY_SHA256,
    TRUSTED_DATA_MANIFEST_SHA256,
    TRUSTED_DEPENDENCY_LOCK_SHA256,
    TRUSTED_LIKELIHOOD_CODE_MANIFEST_SHA256,
    TRUSTED_NATIVE_RUNTIME_FINGERPRINT,
    TRUSTED_NATIVE_RUNTIME_SHA256,
    TRUSTED_PROTOCOL_AMENDMENT_SHA256,
    TRUSTED_PROTOCOL_AUTHORITY_REGISTRY,
    TRUSTED_REFERENCE_SPEC_SHA256,
    TRUSTED_SOURCE_BASE_COMMIT,
    TRUSTED_EXTERNAL_REVIEW_AUTHORITY_REGISTRY,
    TRUSTED_WHEEL_MANIFEST_SHA256,
    exact_environment_validated_for_formal_execution,
)


RESEARCH_ALPHA_MANIFEST_VERSION = 2
RESEARCH_ALPHA_MCSE_SIGMA_MAX = 0.05
RESEARCH_ALPHA_EXACT_BULK_ESS_MIN = 1000.0
RESEARCH_ALPHA_MIN_RAW_DRAWS_PER_CHAIN = 1000
EXTERNAL_REVIEW_PUBLIC_KEY_ENV = "RESEARCH_ALPHA_EXTERNAL_REVIEW_PUBLIC_KEY_PATH"
EXTERNAL_REVIEW_AUTHORITY_ENV = "RESEARCH_ALPHA_EXTERNAL_REVIEW_AUTHORITY_ID"
EXTERNAL_REVIEW_SCHEMA_VERSION = 1
PROTOCOL_AUTHORITY_PUBLIC_KEY_ENV = (
    "RESEARCH_ALPHA_PROTOCOL_AUTHORITY_PUBLIC_KEY_PATH"
)
PROTOCOL_AUTHORITY_ID_ENV = "RESEARCH_ALPHA_PROTOCOL_AUTHORITY_ID"
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_PASS_STATUSES = {"passed", "pass", "ok"}
_INTERVAL_FIELDS = (
    "center",
    "lower_68",
    "upper_68",
    "uncertainty_minus",
    "uncertainty_plus",
)
_REQUIRED_SYSTEMATICS_VARIANTS = {
    "planck_pr3_plik",
    "planck_pr4_camspec",
    "lensing_combination",
    "pantheonplus_covariance",
}
_REQUIRED_PREDICTIVE_DISCREPANCIES = {
    "desi_bao_residual_quadratic",
    "pantheonplus_whitened_residual_quadratic",
    "cmb_likelihood_component_chi2",
    "lensing_bandpower_residual_quadratic",
}
_REQUIRED_INJECTION_BASE = {
    "ombh2": 0.02237,
    "omch2": 0.1200,
    "theta_MC_100": 1.04109,
    "tau": 0.055,
    "ns": 0.965,
    "logA": 3.05,
}
_REQUIRED_INJECTION_TRUTHS = {
    "lambda_boundary": {**_REQUIRED_INJECTION_BASE, "w": -1.0, "wa": 0.0},
    "evolving_quintessence": {
        **_REQUIRED_INJECTION_BASE,
        "w": -0.85,
        "wa": -0.60,
    },
    "crossing_model": {**_REQUIRED_INJECTION_BASE, "w": -1.10, "wa": 0.40},
}


def _require_exact_evidence_signing_key() -> dict[str, Any]:
    """Require the preregistered key identity for exact-profile signing."""

    binding = signing_key_binding(require_explicit=True)
    if binding.get("key_id") != EXACT_EVIDENCE_SIGNING_KEY_ID:
        raise ValueError("exact evidence signing key id does not match the frozen contract")
    if binding.get("sha256") != EXACT_EVIDENCE_SIGNING_KEY_SHA256:
        raise ValueError(
            "exact evidence signing-key fingerprint does not match the frozen contract"
        )
    return binding


def _validate_exact_evidence_signing_key_binding(
    attestation: Mapping[str, Any],
) -> None:
    binding = attestation.get("evidence_signing_key_binding")
    key_id = attestation.get("key_id")
    if (
        not isinstance(binding, Mapping)
        or binding.get("available") is not True
        or binding.get("key_id") != key_id
        or set(binding) != {"available", "key_id", "sha256"}
    ):
        raise ValueError("exact evidence signing-key binding is missing or malformed")
    if key_id != EXACT_EVIDENCE_SIGNING_KEY_ID:
        raise ValueError("exact evidence signing key id does not match the frozen contract")
    if binding.get("sha256") != EXACT_EVIDENCE_SIGNING_KEY_SHA256:
        raise ValueError(
            "exact evidence signing-key fingerprint does not match the frozen contract"
        )
    candidate = verification_key_for_id(key_id)
    if not candidate:
        raise ValueError("exact evidence signing key is unavailable for verification")
    if len(candidate.encode("utf-8")) < 32:
        raise ValueError("exact evidence signing key must contain at least 32 bytes")
    expected = "sha256:" + hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    if expected != EXACT_EVIDENCE_SIGNING_KEY_SHA256:
        raise ValueError("exact evidence signing-key fingerprint mismatch")


def _build_research_alpha_scientific_attestation(
    *,
    attestation_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    body = dict(payload)
    if body.get("profile_id") == EXACT_PROFILE_ID:
        body["evidence_signing_key_binding"] = (
            _require_exact_evidence_signing_key()
        )
    attestation = build_scientific_attestation(
        attestation_type=attestation_type,
        payload=body,
        require_explicit=body.get("profile_id") == EXACT_PROFILE_ID,
    )
    if body.get("profile_id") == EXACT_PROFILE_ID:
        _validate_exact_evidence_signing_key_binding(attestation)
        if not verify_scientific_attestation(
            attestation, expected_type=attestation_type
        ):
            raise ValueError("exact evidence attestation signature is not verifiable")
    return attestation
_EXACT_PLAN_CONFIG_KEYS = {
    "planck_pr3_plik",
    "non_citable_smoke",
    "prior_w0wa_widened",
    "planck_pr4_camspec",
    "lensing_combination",
    "pantheonplus_covariance",
    "independent_reproduction",
}
_EXACT_PLAN_CONFIG_BY_CHECK_NAME = {
    "prior_predictive_check": {
        name: "planck_pr3_plik" for name in _REQUIRED_PREDICTIVE_DISCREPANCIES
    },
    "posterior_predictive_check": {
        name: "planck_pr3_plik" for name in _REQUIRED_PREDICTIVE_DISCREPANCIES
    },
    "prior_sensitivity": {
        "baseline_prior": "planck_pr3_plik",
        "widened_prior": "prior_w0wa_widened",
    },
    "systematics_robustness": {
        name: name for name in _REQUIRED_SYSTEMATICS_VARIANTS
    },
    "simulation_recovery": {
        name: "planck_pr3_plik" for name in _REQUIRED_INJECTION_TRUTHS
    },
}
_EXACT_PPC_TAIL_RULE = "(1 + count(T_rep >= T_observed)) / (replicates + 1)"
_EXACT_PPC_LOWER = 0.01
_EXACT_PPC_UPPER = 0.99
_EXACT_PPC_SEEDS = {
    "prior_predictive_check": [133701, 133703, 133709, 133711],
    "posterior_predictive_check": [233701, 233703, 233709, 233711],
}
_EXACT_INJECTION_SEEDS = {
    "lambda_boundary": 310001,
    "evolving_quintessence": 310003,
    "crossing_model": 310019,
}
_EXACT_INDEPENDENT_SEEDS = [71001931, 82350647, 94110763, 105320087]
_EXACT_INJECTION_PARAMETERS = ("w0", "wa")
_CHI2_2D_95 = 5.991464547107979
_EXACT_DISTRIBUTION_COUNT = 52
_EXACT_SIMULATED_DATA_BLOCKS = {"desi_bao", "pantheonplus", "cmb", "lensing"}
_EXACT_ADEQUACY_DATA_GROUP = "planck_NPIPE_highl_CamSpec.TTTEEE"
_REQUIRED_EXACT_AUDIT_CODE = {
    "canonical_full_likelihood_evidence.py",
    "w0wa_exact_contract.py",
    "research_alpha_attestation.py",
    "research_alpha_manifest.py",
    "research_alpha_evaluator.py",
}
_EXACT_PROFILE_ID = EXACT_PROFILE_ID
_RUN_ATTESTATION_TYPE = "research_alpha_run"
_ANALYSIS_ATTESTATION_TYPE = "research_alpha_analysis"
_ADEQUACY_ATTESTATION_TYPE = "research_alpha_adequacy"
_ADEQUACY_RUN_ATTESTATION_TYPE = "research_alpha_adequacy_run"
_ADEQUACY_ANALYSIS_ATTESTATION_TYPE = "research_alpha_adequacy_analysis"
_INDEPENDENT_ANALYSIS_ATTESTATION_TYPE = "research_alpha_independent_analysis"
_EXACT_ADEQUACY_RUN_RECEIPT_TYPE = "w0wa_exact_adequacy_run_receipt"
_EXACT_ADEQUACY_ANALYSIS_RECEIPT_TYPE = "w0wa_exact_adequacy_analysis_receipt"
_EXACT_ADEQUACY_AGGREGATE_RECEIPT_TYPE = "w0wa_exact_adequacy_aggregate_receipt"
_PROTOCOL_STATUS = PROTOCOL_STATUS
_PROTOCOL_AMENDMENT_SHA256 = TRUSTED_PROTOCOL_AMENDMENT_SHA256


def _canonical_distribution_name(value: Any) -> str:
    return re.sub(r"[-_.]+", "-", str(value or "")).lower()


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


def _runtime_environment_fingerprint(runtime: Mapping[str, Any]) -> str:
    """Reproduce the canonical runner's deliberately narrow run fingerprint."""

    return scientific_content_hash(
        {
            "python": runtime.get("python"),
            "platform": runtime.get("platform"),
            "machine": runtime.get("machine"),
            "packages": runtime.get("packages"),
            "runtime_modules": runtime.get("runtime_modules"),
            "thread_environment": runtime.get("thread_environment"),
            "native_runtime": _native_runtime_fingerprint_identity(
                runtime.get("native_runtime")
            ),
            "import_policy": runtime.get("import_policy"),
            "runtime_closure": runtime.get("runtime_closure"),
        }
    )


def _validate_recorded_exact_import_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("exact preflight import policy is missing")
    unsigned = {
        key: item
        for key, item in value.items()
        if key not in {"passed", "reasons", "fingerprint"}
    }
    if (
        value.get("schema_version") != 1
        or value.get("passed") is not True
        or value.get("reasons") != []
        or value.get("fingerprint") != scientific_content_hash(unsigned)
        or value.get("python_flag") != "-I"
        or any(
            value.get(field) is not True
            for field in (
                "isolated_interpreter",
                "ignore_environment",
                "no_user_site",
                "safe_path",
                "pythonpath_empty",
                "user_site_disabled_by_child",
            )
        )
        or not isinstance(value.get("site_package_roots"), Sequence)
        or isinstance(value.get("site_package_roots"), (str, bytes))
        or not isinstance(value.get("startup_hooks"), Sequence)
        or isinstance(value.get("startup_hooks"), (str, bytes))
    ):
        raise ValueError("exact preflight import policy is not a self-consistent PASS")
    return dict(value)


def _validate_verified_independent_import_policy(
    value: Any,
    *,
    recorded_policy: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("independent postprocessor import policy proof is missing")
    for field in (
        "preflight_import_policy_fingerprint",
        "startup_hook_fingerprint",
    ):
        _require_sha256(value.get(field), f"independent import policy {field}")
    if (
        value.get("schema_version") != 1
        or value.get("verified") is not True
        or value.get("passed") is not True
        or value.get("reasons") != []
        or value.get("preflight_import_policy_fingerprint")
        != recorded_policy.get("fingerprint")
        or any(
            value.get(field) is not True
            for field in (
                "isolated_interpreter",
                "ignore_environment",
                "no_user_site",
                "safe_path",
                "pythonpath_empty",
            )
        )
    ):
        raise ValueError("independent postprocessor import policy proof is invalid")
    return dict(value)


def _exact_environment_location(
    import_policy: Mapping[str, Any], *, field: str
) -> dict[str, Any]:
    venv_root = Path(
        _required_text(import_policy.get("venv_root"), f"{field}.venv_root")
    ).expanduser().resolve()
    raw_roots = import_policy.get("site_package_roots")
    if (
        not venv_root.is_dir()
        or not isinstance(raw_roots, Sequence)
        or isinstance(raw_roots, (str, bytes))
        or not raw_roots
    ):
        raise ValueError(f"{field} virtual-environment location is invalid")
    roots = sorted(
        {
            Path(_required_text(raw, f"{field}.site_package_roots"))
            .expanduser()
            .resolve()
            for raw in raw_roots
        },
        key=str,
    )
    if len(roots) != len(raw_roots) or any(
        not root.is_dir() or not root.is_relative_to(venv_root) for root in roots
    ):
        raise ValueError(f"{field} site-package roots are invalid")
    return {
        "venv_root": str(venv_root),
        # The producer records ``venv_root`` directly from resolved
        # ``sys.prefix`` and the independent postprocessor rechecks that live.
        # Retain both names here so the cross-run independence decision is
        # explicit rather than inferred from an executable path.
        "sys_prefix": str(venv_root),
        "site_package_roots": [str(root) for root in roots],
    }


def _environment_locations_overlap(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> bool:
    first_roots = [Path(path) for path in first.get("site_package_roots") or []]
    second_roots = [Path(path) for path in second.get("site_package_roots") or []]
    return any(
        left == right
        or left.is_relative_to(right)
        or right.is_relative_to(left)
        for left in first_roots
        for right in second_roots
    )


def _validate_independent_environment_locations(
    *,
    primary: Mapping[str, Any],
    independent: Mapping[str, Any],
) -> None:
    if (
        primary.get("venv_root") == independent.get("venv_root")
        or primary.get("sys_prefix") == independent.get("sys_prefix")
    ):
        raise ValueError(
            "independent reproduction reused the primary virtual environment"
        )
    if _environment_locations_overlap(primary, independent):
        raise ValueError(
            "independent reproduction site-package roots overlap the primary"
        )


def _validate_exact_distribution_inventory(
    required_versions: Any,
    distributions: Any,
) -> dict[str, str]:
    if (
        not isinstance(required_versions, Mapping)
        or not isinstance(distributions, Mapping)
        or len(required_versions) != _EXACT_DISTRIBUTION_COUNT
        or set(distributions) != set(required_versions)
    ):
        raise ValueError("exact installed distribution closure is incomplete")
    normalized_versions: dict[str, str] = {}
    for raw_name, raw_version in required_versions.items():
        name = _canonical_distribution_name(raw_name)
        version = _required_text(raw_version, f"exact distribution version {raw_name}")
        if not name or name != raw_name or name in normalized_versions:
            raise ValueError("exact distribution names are not canonical and unique")
        normalized_versions[name] = version
        record = distributions.get(name)
        files = record.get("files") if isinstance(record, Mapping) else None
        if (
            not isinstance(record, Mapping)
            or record.get("distribution") != name
            or record.get("installed") is not True
            or record.get("version") != version
            or not isinstance(files, Sequence)
            or isinstance(files, (str, bytes))
            or not files
        ):
            raise ValueError(f"exact installed distribution record is invalid: {name}")
        normalized_files: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for index, raw_file in enumerate(files):
            if not isinstance(raw_file, Mapping):
                raise ValueError(f"exact distribution file is malformed: {name}/{index}")
            path_text = _required_text(
                raw_file.get("path"), f"exact distribution file path {name}/{index}"
            )
            logical_path = Path(path_text)
            size = _integer(
                raw_file.get("size_bytes"),
                f"exact distribution file size {name}/{index}",
            )
            _require_sha256(
                raw_file.get("sha256"),
                f"exact distribution file SHA-256 {name}/{index}",
            )
            if (
                logical_path.is_absolute()
                or size < 0
                or path_text in seen_paths
            ):
                raise ValueError(f"exact distribution file inventory is unsafe: {name}")
            seen_paths.add(path_text)
            normalized_files.append(
                {
                    "path": path_text,
                    "size_bytes": size,
                    "sha256": raw_file["sha256"],
                }
            )
        if normalized_files != sorted(normalized_files, key=lambda item: item["path"]):
            raise ValueError(f"exact distribution files are not canonical: {name}")
        if record.get("fingerprint") != scientific_content_hash(normalized_files):
            raise ValueError(f"exact distribution fingerprint is inconsistent: {name}")
    return normalized_versions


def _validate_exact_runtime_closure_identity(
    value: Any,
    *,
    required_versions: Mapping[str, str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("exact runtime closure identity is missing")
    unsigned = {
        key: item
        for key, item in value.items()
        if key not in {"passed", "reasons", "fingerprint"}
    }
    expected_installed = set(required_versions) | set(
        FROZEN_BOOTSTRAP_DISTRIBUTIONS
    )
    fingerprints = value.get("distribution_fingerprints")
    if (
        value.get("passed") is not True
        or value.get("reasons") != []
        or value.get("fingerprint") != scientific_content_hash(unsigned)
        or value.get("required_versions") != dict(sorted(required_versions.items()))
        or value.get("dependency_closure") != sorted(required_versions)
        or value.get("installed_distributions") != sorted(expected_installed)
        or value.get("bootstrap_distributions")
        != FROZEN_BOOTSTRAP_DISTRIBUTIONS
        or not isinstance(fingerprints, Mapping)
        or set(fingerprints) != expected_installed
    ):
        raise ValueError("exact installed distribution set is not the frozen closure")
    for name, expected in FROZEN_BOOTSTRAP_DISTRIBUTIONS.items():
        if fingerprints.get(name) != expected:
            raise ValueError(f"exact bootstrap distribution drifted: {name}")
    for name, version in required_versions.items():
        record = fingerprints.get(name)
        if (
            not isinstance(record, Mapping)
            or record.get("version") != version
        ):
            raise ValueError(f"exact runtime distribution identity drifted: {name}")
        _require_sha256(
            record.get("fingerprint"), f"exact runtime distribution fingerprint {name}"
        )
    _validate_exact_site_packages_ownership(value.get("site_packages_ownership"))
    return dict(value)


def _validate_exact_site_packages_ownership(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("exact site-packages ownership inventory is missing")
    unsigned = {
        key: item
        for key, item in value.items()
        if key not in {"passed", "reasons", "fingerprint"}
    }
    owned = value.get("owned_import_files")
    expected_fields = {
        "schema_version",
        "site_root_count",
        "owned_import_files",
        "generated_bytecode_policy",
        "unowned_import_files",
        "unowned_generated_bytecode",
        "symlinked_directories",
        "passed",
        "reasons",
        "fingerprint",
    }
    if (
        set(value) != expected_fields
        or value.get("schema_version") != 1
        or value.get("passed") is not True
        or value.get("reasons") != []
        or value.get("fingerprint") != scientific_content_hash(unsigned)
        or not isinstance(owned, Mapping)
        or set(owned) != {"count", "fingerprint"}
        or value.get("generated_bytecode_policy")
        != GENERATED_BYTECODE_CACHE_POLICY
        or value.get("unowned_import_files") != []
        or value.get("unowned_generated_bytecode") != []
        or value.get("symlinked_directories") != []
    ):
        raise ValueError("exact site-packages ownership inventory did not pass")
    site_root_count = _integer(
        value.get("site_root_count"), "exact site-package root count"
    )
    owned_count = _integer(
        owned.get("count"), "exact owned import-file count"
    )
    if site_root_count < 1 or owned_count < 1:
        raise ValueError("exact site-packages ownership counts are invalid")
    _require_sha256(
        owned.get("fingerprint"), "exact owned import-file fingerprint"
    )
    return dict(value)


def _frozen_wheel_payload_commitment(path: Path) -> dict[str, Any]:
    """Re-derive the producer's installed-payload commitment from wheel bytes."""

    try:
        with zipfile.ZipFile(path) as archive:
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
                raise ValueError("exact frozen wheel RECORD count is invalid")
            record_path = record_paths[0]
            try:
                rows = list(
                    csv.reader(
                        io.StringIO(archive.read(record_path).decode("utf-8"))
                    )
                )
            except (UnicodeDecodeError, csv.Error, KeyError) as exc:
                raise ValueError("exact frozen wheel RECORD is unreadable") from exc
            record_rows: dict[str, tuple[str, str]] = {}
            for row in rows:
                if len(row) != 3 or not row[0] or row[0] in record_rows:
                    raise ValueError("exact frozen wheel RECORD row is invalid")
                record_rows[row[0]] = (row[1], row[2])
            data_prefixes = sorted(
                {
                    name.split("/", 1)[0]
                    for name in members
                    if ".data/" in name
                }
            )
            expected_records: list[dict[str, Any]] = []
            skipped_members: list[str] = []
            for member_name in sorted(members):
                if member_name == record_path:
                    continue
                row = record_rows.get(member_name)
                if row is None:
                    raise ValueError("exact frozen wheel member is absent from RECORD")
                member_bytes = archive.read(member_name)
                digest = (
                    "sha256="
                    + base64.urlsafe_b64encode(hashlib.sha256(member_bytes).digest())
                    .decode("ascii")
                    .rstrip("=")
                )
                if row != (digest, str(len(member_bytes))):
                    raise ValueError("exact frozen wheel RECORD payload mismatch")
                installed_relative = member_name
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
                        raise ValueError("exact frozen wheel .data member is invalid")
                    if category == "scripts":
                        skipped_members.append(member_name)
                        continue
                    if category not in {"purelib", "platlib", "data", "headers"}:
                        raise ValueError("exact frozen wheel install category is invalid")
                    installed_relative = relative
                expected_records.append(
                    {
                        "wheel_member": member_name,
                        "installed_relative_path": installed_relative,
                        "size_bytes": len(member_bytes),
                        "sha256": "sha256:"
                        + hashlib.sha256(member_bytes).hexdigest(),
                    }
                )
            if any(
                name not in members and name != record_path for name in record_rows
            ):
                raise ValueError("exact frozen wheel RECORD references a missing member")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("exact frozen wheel is unreadable") from exc
    return {
        "expected_payload_fingerprint": scientific_content_hash(expected_records),
        "checked_file_count": len(expected_records),
        "skipped_installer_rewritten_members": skipped_members,
    }


def _validate_exact_environment_closure(
    environment: Mapping[str, Any],
    *,
    manifest_wheels: Sequence[Mapping[str, Any]],
    observed_wheel_artifacts: Mapping[str, Mapping[str, Any]],
) -> None:
    """Revalidate the full lock, distribution, and installed-wheel closure."""

    required_versions = _validate_exact_distribution_inventory(
        environment.get("required_versions"), environment.get("distributions")
    )
    expected_by_project: dict[str, Mapping[str, Any]] = {}
    for raw in manifest_wheels:
        if not isinstance(raw, Mapping):
            raise ValueError("exact wheel manifest record is malformed")
        project = _canonical_distribution_name(raw.get("project"))
        if not project or project in expected_by_project:
            raise ValueError("exact wheel manifest projects are not unique")
        expected_by_project[project] = raw
    expected_versions = {
        name: _required_text(record.get("version"), f"exact wheel version {name}")
        for name, record in expected_by_project.items()
    }
    if expected_versions != required_versions:
        raise ValueError("exact required versions do not equal the frozen wheel closure")

    runtime_closure = environment.get("runtime_closure")
    if (
        not isinstance(runtime_closure, Sequence)
        or isinstance(runtime_closure, (str, bytes))
        or list(runtime_closure) != sorted(required_versions)
    ):
        raise ValueError("exact runtime closure does not equal the 52 locked distributions")

    runtime = environment.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError("exact preflight runtime is missing")
    _validate_recorded_exact_import_policy(runtime.get("import_policy"))
    _validate_exact_runtime_closure_identity(
        runtime.get("runtime_closure"), required_versions=required_versions
    )

    bindings = environment.get("installed_wheel_bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != set(required_versions):
        raise ValueError("exact installed wheel bindings are incomplete")
    for name, version in required_versions.items():
        binding = bindings.get(name)
        expected = expected_by_project[name]
        filename = _required_text(
            expected.get("filename"), f"exact frozen wheel filename {name}"
        )
        observed = observed_wheel_artifacts.get(filename)
        if not isinstance(binding, Mapping) or not isinstance(observed, Mapping):
            raise ValueError(f"exact installed wheel binding is missing: {name}")
        expected_payload = binding.get("expected_payload_fingerprint")
        installed_payload = binding.get("installed_payload_fingerprint")
        _require_sha256(expected_payload, f"exact expected wheel payload {name}")
        _require_sha256(installed_payload, f"exact installed wheel payload {name}")
        skipped = binding.get("skipped_installer_rewritten_members")
        checked_count = _integer(
            binding.get("checked_file_count"), f"exact installed wheel count {name}"
        )
        wheel_commitment = _frozen_wheel_payload_commitment(Path(observed["path"]))
        if (
            binding.get("passed") is not True
            or binding.get("reasons") != []
            or binding.get("distribution") != name
            or binding.get("version") != version
            or Path(str(binding.get("wheel_path") or "")).expanduser().resolve()
            != Path(str(observed["path"])).resolve()
            or binding.get("wheel_sha256") != observed.get("sha256")
            or binding.get("wheel_sha256") != expected.get("sha256")
            or expected_payload != installed_payload
            or expected_payload
            != wheel_commitment["expected_payload_fingerprint"]
            or checked_count != wheel_commitment["checked_file_count"]
            or checked_count <= 0
            or not isinstance(skipped, Sequence)
            or isinstance(skipped, (str, bytes))
            or list(skipped)
            != wheel_commitment["skipped_installer_rewritten_members"]
        ):
            raise ValueError(f"exact installed wheel binding is invalid: {name}")


def _normalize_research_profile(value: Any) -> str:
    profile_id = _required_text(value, "profile_id")
    if profile_id not in {EXACT_PROFILE_ID, CI_FIXTURE_PROFILE_ID}:
        raise ValueError("research alpha profile is not recognized")
    return profile_id


def _artifact_code_hashes(
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    return {Path(item["path"]).name: str(item["sha256"]) for item in artifacts}


def _exact_plan_config_key(check: str, name: str) -> str:
    by_name = _EXACT_PLAN_CONFIG_BY_CHECK_NAME.get(check)
    key = by_name.get(name) if isinstance(by_name, Mapping) else None
    if not isinstance(key, str):
        raise ValueError(f"exact adequacy check/name is not preregistered: {check}/{name}")
    return key


def _paper_sigma(parameter: str, delta: float) -> float:
    record = PREREGISTERED_PAPER_UNCERTAINTIES.get(parameter)
    if not isinstance(record, Mapping):
        raise ValueError(f"paper uncertainty is not frozen for {parameter}")
    side = "plus" if delta >= 0 else "minus"
    return float(record[side])


def _trusted_exact_adequacy_code(
    value: Any,
    *,
    registry: Mapping[str, str],
    field: str,
) -> dict[str, Any]:
    if not registry:
        raise ValueError(
            f"{field} registry is empty; exact adequacy remains WITHHELD"
        )
    artifact = _require_canonical_nested_artifact(value, field)
    expected = registry.get(Path(artifact["path"]).name)
    if expected is None or artifact["sha256"] != expected:
        raise ValueError(f"{field} is not preregistered and trusted")
    return artifact


def _validate_exact_source_state(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("exact preflight source_state is missing")
    files = value.get("files")
    status_entries = value.get("status_entries")
    head_commit = value.get("head_commit")
    head_tree = value.get("head_tree")
    branch = value.get("branch")
    detached = value.get("detached")
    if (
        value.get("schema_version") != 2
        or value.get("passed") is not True
        or value.get("clean") is not True
        or value.get("reasons") != []
        or status_entries != []
        or value.get("base_commit") != TRUSTED_SOURCE_BASE_COMMIT
        or value.get("base_is_ancestor") is not True
        or not isinstance(head_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", head_commit) is None
        or not isinstance(head_tree, str)
        or re.fullmatch(r"[0-9a-f]{40}", head_tree) is None
        or not isinstance(branch, str)
        or not branch
        or not isinstance(detached, bool)
        or detached != (branch == "HEAD")
        or not isinstance(files, Sequence)
        or isinstance(files, (str, bytes))
        or len(files) != len(REQUIRED_SOURCE_STATE_PATHS)
    ):
        raise ValueError("exact preflight source tree is not clean and reviewable")
    repository = Path(
        _required_text(value.get("repository_root"), "source repository_root")
    ).expanduser().resolve()
    observed_paths: list[str] = []
    for index, record in enumerate(files):
        if not isinstance(record, Mapping):
            raise ValueError("exact source file record is malformed")
        logical_path = _required_text(record.get("path"), f"source file {index} path")
        if logical_path != REQUIRED_SOURCE_STATE_PATHS[index]:
            raise ValueError("exact source inventory order/path drifted")
        physical = (repository / logical_path).resolve()
        try:
            physical.relative_to(repository)
        except ValueError as exc:
            raise ValueError("exact source inventory escapes repository") from exc
        if (
            not physical.is_file()
            or physical.stat().st_size
            != _integer(record.get("size_bytes"), f"source file {index} size")
            or _hash_file(physical) != record.get("sha256")
        ):
            raise ValueError(f"exact source file byte drift: {logical_path}")
        observed_paths.append(logical_path)
    if tuple(observed_paths) != REQUIRED_SOURCE_STATE_PATHS:
        raise ValueError("exact source inventory is incomplete")
    fingerprint_payload = {
        "base_commit": TRUSTED_SOURCE_BASE_COMMIT,
        "base_is_ancestor": True,
        "head_commit": head_commit,
        "head_tree": head_tree,
        "branch": branch,
        "detached": detached,
        "files": list(files),
        "status_entries": [],
    }
    if value.get("fingerprint") != scientific_content_hash(fingerprint_payload):
        raise ValueError("exact source-state fingerprint is inconsistent")

    def git_output(*args: str, binary: bool = False) -> str | bytes:
        try:
            result = subprocess.run(
                ["git", "-C", str(repository), *args],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("exact source-state Git verification failed") from exc
        if result.returncode != 0:
            raise ValueError("exact source-state Git verification failed")
        return result.stdout if binary else result.stdout.decode("utf-8").strip()

    if (
        git_output("rev-parse", "--show-toplevel") != str(repository)
        or git_output("rev-parse", "HEAD") != head_commit
        or git_output("rev-parse", "HEAD^{tree}") != head_tree
        or git_output("rev-parse", "--abbrev-ref", "HEAD") != branch
    ):
        raise ValueError("exact source-state Git identity drifted")
    tracked = {
        item.decode("utf-8")
        for item in bytes(
            git_output(
                "ls-files", "-z", "--", *REQUIRED_SOURCE_STATE_PATHS, binary=True
            )
        ).split(b"\0")
        if item
    }
    status = bytes(
        git_output(
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            binary=True,
        )
    )
    if tracked != set(REQUIRED_SOURCE_STATE_PATHS) or status:
        raise ValueError("exact source-state tracked/clean verification failed")
    try:
        ancestor = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "merge-base",
                "--is-ancestor",
                TRUSTED_SOURCE_BASE_COMMIT,
                "HEAD",
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("exact source-state base verification failed") from exc
    if ancestor.returncode != 0:
        raise ValueError("exact source-state HEAD is not descended from frozen base")


def _validate_exact_adequacy_data_inventory(
    value: Any,
    *,
    trusted_manifest: Mapping[str, Any],
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("exact adequacy-data inventory is missing")
    groups = value.get("groups")
    if (
        value.get("complete") is not True
        or value.get("missing") != []
        or not isinstance(groups, Mapping)
        or set(groups) != {_EXACT_ADEQUACY_DATA_GROUP}
    ):
        raise ValueError("exact CamSpec adequacy-data inventory is incomplete")
    packages_root = Path(
        _required_text(value.get("packages_path"), "adequacy packages_path")
    ).expanduser().resolve()
    group = groups[_EXACT_ADEQUACY_DATA_GROUP]
    files = group.get("files") if isinstance(group, Mapping) else None
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes)) or not files:
        raise ValueError("exact CamSpec adequacy-data files are missing")
    normalized_files: list[dict[str, Any]] = []
    for index, record in enumerate(files):
        if not isinstance(record, Mapping):
            raise ValueError("exact CamSpec file record is malformed")
        logical_path = _required_text(
            record.get("path"), f"CamSpec adequacy file {index} path"
        )
        relative = Path(logical_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("exact CamSpec logical path is unsafe")
        physical = (packages_root / relative).resolve()
        try:
            physical.relative_to(packages_root)
        except ValueError as exc:
            raise ValueError("exact CamSpec path escapes packages root") from exc
        size = _integer(record.get("size_bytes"), f"CamSpec file {index} size")
        digest = record.get("sha256")
        _require_sha256(digest, f"CamSpec file {index} sha256")
        if (
            not physical.is_file()
            or physical.stat().st_size != size
            or _hash_file(physical) != digest
        ):
            raise ValueError(f"exact CamSpec data byte drift: {logical_path}")
        normalized_files.append(
            {"path": logical_path, "size_bytes": size, "sha256": digest}
        )
    summary = {
        "file_count": len(normalized_files),
        "total_size_bytes": sum(item["size_bytes"] for item in normalized_files),
        "fingerprint": scientific_content_hash(normalized_files),
        "files": normalized_files,
    }
    committed_groups = trusted_manifest.get("adequacy_groups")
    committed = (
        committed_groups.get(_EXACT_ADEQUACY_DATA_GROUP)
        if isinstance(committed_groups, Mapping)
        else None
    )
    expected_summary = REQUIRED_ADEQUACY_DATA_GROUPS[_EXACT_ADEQUACY_DATA_GROUP]
    if (
        not isinstance(committed, Mapping)
        or any(committed.get(key) != expected for key, expected in summary.items())
        or any(summary.get(key) != expected for key, expected in expected_summary.items())
    ):
        raise ValueError("exact CamSpec data are not in the frozen data manifest")
    if (
        group.get("fingerprint") != summary["fingerprint"]
        or value.get("fingerprint")
        != TRUSTED_ADEQUACY_DATA_INVENTORY_SHA256
        or trusted_manifest.get("adequacy_inventory_fingerprint")
        != TRUSTED_ADEQUACY_DATA_INVENTORY_SHA256
    ):
        raise ValueError("exact CamSpec adequacy-data fingerprint mismatch")


def _validate_loaded_likelihood_runtime(value: Any) -> None:
    """Re-open the executable identities recorded by live reference checks."""

    if not isinstance(value, Mapping) or set(value) != {
        "camb",
        "clipy",
        "act_dr6_lenslike",
        "planck_NPIPE_highl_CamSpec",
    }:
        raise ValueError("exact loaded likelihood runtime set is invalid")

    camb = value["camb"]
    if (
        not isinstance(camb, Mapping)
        or camb.get("version") != REQUIRED_PACKAGE_VERSIONS["camb"]
        or not Path(
            _required_text(camb.get("origin"), "loaded CAMB origin")
        ).is_file()
    ):
        raise ValueError("exact loaded CAMB runtime is invalid")

    clipy = value["clipy"]
    clipy_files = clipy.get("files") if isinstance(clipy, Mapping) else None
    if (
        not isinstance(clipy, Mapping)
        or clipy.get("expected_version")
        != REQUIRED_PACKAGE_VERSIONS["clipy-like"]
        or clipy.get("loaded_version")
        != REQUIRED_PACKAGE_VERSIONS["clipy-like"]
        or not isinstance(clipy_files, Sequence)
        or isinstance(clipy_files, (str, bytes))
        or not clipy_files
    ):
        raise ValueError("exact loaded clipy runtime is invalid")
    clipy_root = Path(
        _required_text(clipy.get("root"), "loaded clipy root")
    ).expanduser().resolve()
    loaded_clipy = Path(
        _required_text(clipy.get("loaded_origin"), "loaded clipy origin")
    ).expanduser().resolve()
    try:
        loaded_clipy.relative_to(clipy_root)
    except ValueError as exc:
        raise ValueError("exact loaded clipy escaped its frozen tree") from exc
    if not loaded_clipy.is_file():
        raise ValueError("exact loaded clipy origin is missing")
    normalized_clipy_files: list[dict[str, Any]] = []
    for index, record in enumerate(clipy_files):
        if not isinstance(record, Mapping):
            raise ValueError("exact clipy file record is malformed")
        relative = Path(
            _required_text(record.get("path"), f"exact clipy file {index} path")
        )
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("exact clipy file path is unsafe")
        physical = (clipy_root / relative).resolve()
        try:
            physical.relative_to(clipy_root)
        except ValueError as exc:
            raise ValueError("exact clipy file escaped its frozen tree") from exc
        size = _integer(record.get("size_bytes"), f"exact clipy file {index} size")
        digest = record.get("sha256")
        _require_sha256(digest, f"exact clipy file {index} sha256")
        if (
            not physical.is_file()
            or physical.stat().st_size != size
            or _hash_file(physical) != digest
        ):
            raise ValueError(f"exact clipy file byte drift: {relative.as_posix()}")
        normalized_clipy_files.append(
            {
                "path": relative.as_posix(),
                "size_bytes": size,
                "sha256": digest,
            }
        )
    if clipy.get("tree_fingerprint") != scientific_content_hash(
        normalized_clipy_files
    ):
        raise ValueError("exact clipy runtime fingerprint is inconsistent")

    act = value["act_dr6_lenslike"]
    if (
        not isinstance(act, Mapping)
        or act.get("version") != REQUIRED_PACKAGE_VERSIONS["act_dr6_lenslike"]
        or not Path(
            _required_text(act.get("loaded_origin"), "loaded ACT origin")
        ).is_file()
    ):
        raise ValueError("exact loaded ACT runtime is invalid")
    _require_sha256(act.get("fingerprint"), "loaded ACT distribution fingerprint")

    camspec = value["planck_NPIPE_highl_CamSpec"]
    if not isinstance(camspec, Mapping) or camspec.get(
        "cobaya_version"
    ) != REQUIRED_PACKAGE_VERSIONS["cobaya"]:
        raise ValueError("exact loaded NPIPE CamSpec runtime is invalid")
    camspec_artifact = _normalize_artifact(
        {
            "path": camspec.get("loaded_origin"),
            "sha256": camspec.get("sha256"),
        },
        "loaded NPIPE CamSpec implementation",
    )
    if camspec_artifact["sha256"] != camspec.get("sha256"):
        raise ValueError("exact loaded NPIPE CamSpec implementation drifted")


def _validate_exact_likelihood_code_manifest(value: Any) -> None:
    if (
        not isinstance(value, Mapping)
        or value.get("passed") is not True
        or value.get("reasons") != []
    ):
        raise ValueError("exact likelihood-code manifest did not pass")
    artifact = _normalize_artifact(value, "exact likelihood-code manifest")
    payload = _load_json_artifact(artifact, "exact likelihood-code manifest")
    expected_trusted = {
        "canonical_config": TRUSTED_CANONICAL_CONFIG_SHA256,
        "data_manifest": TRUSTED_DATA_MANIFEST_SHA256,
        "dependency_lock": TRUSTED_DEPENDENCY_LOCK_SHA256,
        "reference_cases": TRUSTED_REFERENCE_SPEC_SHA256,
        "wheel_manifest": TRUSTED_WHEEL_MANIFEST_SHA256,
    }
    if (
        artifact["sha256"] != TRUSTED_LIKELIHOOD_CODE_MANIFEST_SHA256
        or value.get("payload") != payload
        or payload.get("schema_version") != 1
        or payload.get("kind") != "w0wa_exact_likelihood_code_commitment"
        or payload.get("profile_id") != EXACT_PROFILE_ID
        or payload.get("environment_revision") != EXACT_ENVIRONMENT_REVISION
        or payload.get("frozen_before_formal_run") is not True
        or payload.get("likelihoods") != list(REQUIRED_LIKELIHOODS)
        or payload.get("adequacy_likelihoods")
        != list(REQUIRED_ADEQUACY_DATA_GROUPS)
        or payload.get("packages") != REQUIRED_PACKAGE_VERSIONS
        or payload.get("wheel_sha256") != REQUIRED_WHEEL_SHA256
        or payload.get("full_wheel_closure")
        != {
            "manifest_sha256": TRUSTED_WHEEL_MANIFEST_SHA256,
            "wheel_count": 52,
        }
        or payload.get("runtime_code_verification")
        != "preflight_distribution_files_plus_loaded_likelihood_trees"
        or payload.get("trusted_artifacts") != expected_trusted
    ):
        raise ValueError("exact likelihood-code manifest bytes/schema drifted")


def _validate_exact_trusted_data_groups(
    trusted_manifest: Mapping[str, Any],
    inventory_groups: Mapping[str, Any],
) -> None:
    """Apply the producer's summary-or-full-list commitment policy exactly."""

    committed_groups = trusted_manifest.get("groups")
    if not isinstance(committed_groups, Mapping) or set(
        committed_groups
    ) != set(REQUIRED_DATA_GROUPS):
        raise ValueError("exact trusted data group set drifted")
    for name, expected_summary in REQUIRED_DATA_GROUPS.items():
        committed_group = committed_groups[name]
        observed_group = inventory_groups.get(name)
        if (
            not isinstance(committed_group, Mapping)
            or not isinstance(observed_group, Mapping)
            or any(
                committed_group.get(key) != expected
                for key, expected in expected_summary.items()
            )
        ):
            raise ValueError(f"exact trusted data group drifted: {name}")
        committed_files = committed_group.get("files")
        if committed_files is not None and committed_files != list(
            observed_group.get("files") or []
        ):
            raise ValueError(f"exact trusted data file list drifted: {name}")


def _validate_exact_data_provenance(
    verification: Mapping[str, Any], trusted_manifest: Mapping[str, Any]
) -> None:
    """Physically revalidate the frozen release archives and VCS source files."""

    archive_root = Path(
        _required_text(verification.get("archive_root"), "trusted archive_root")
    ).expanduser().resolve()
    committed_archives = trusted_manifest.get("source_archives")
    observed_archives = verification.get("source_archives")
    if (
        not isinstance(committed_archives, Sequence)
        or isinstance(committed_archives, (str, bytes))
        or not isinstance(observed_archives, Sequence)
        or isinstance(observed_archives, (str, bytes))
        or len(committed_archives) != len(observed_archives)
    ):
        raise ValueError("exact trusted source archive closure is incomplete")
    observed_by_name = {
        str(item.get("filename")): item
        for item in observed_archives
        if isinstance(item, Mapping)
    }
    if len(observed_by_name) != len(observed_archives):
        raise ValueError("exact trusted source archive names are invalid")
    for committed in committed_archives:
        if not isinstance(committed, Mapping):
            raise ValueError("exact trusted source archive record is malformed")
        filename = _required_text(
            committed.get("filename"), "trusted source archive filename"
        )
        relative = Path(filename)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("exact trusted source archive path is unsafe")
        observed = observed_by_name.get(filename)
        if not isinstance(observed, Mapping):
            raise ValueError(f"exact trusted source archive is missing: {filename}")
        artifact = _normalize_artifact(
            observed, f"exact trusted source archive {filename}"
        )
        expected_path = (archive_root / relative).resolve()
        try:
            expected_path.relative_to(archive_root)
        except ValueError as exc:
            raise ValueError("exact trusted source archive escaped its root") from exc
        if (
            Path(artifact["path"]) != expected_path
            or artifact["sha256"] != committed.get("sha256")
            or artifact["size_bytes"] != committed.get("size_bytes")
            or observed.get("url") != committed.get("url")
        ):
            raise ValueError(f"exact trusted source archive drifted: {filename}")

    committed_vcs = trusted_manifest.get("source_vcs")
    observed_vcs = verification.get("source_vcs")
    if (
        not isinstance(committed_vcs, Sequence)
        or isinstance(committed_vcs, (str, bytes))
        or not isinstance(observed_vcs, Sequence)
        or isinstance(observed_vcs, (str, bytes))
        or len(committed_vcs) != len(observed_vcs)
    ):
        raise ValueError("exact trusted VCS source closure is incomplete")
    observed_by_identity = {
        (str(item.get("repository")), str(item.get("commit"))): item
        for item in observed_vcs
        if isinstance(item, Mapping)
    }
    if len(observed_by_identity) != len(observed_vcs):
        raise ValueError("exact trusted VCS source identities are invalid")
    for committed in committed_vcs:
        if not isinstance(committed, Mapping):
            raise ValueError("exact trusted VCS source record is malformed")
        identity = (str(committed.get("repository")), str(committed.get("commit")))
        observed = observed_by_identity.get(identity)
        if (
            not isinstance(observed, Mapping)
            or observed.get("tag") != committed.get("tag")
        ):
            raise ValueError("exact trusted VCS source identity drifted")
        committed_files = committed.get("files")
        observed_files = observed.get("files")
        if (
            not isinstance(committed_files, Sequence)
            or isinstance(committed_files, (str, bytes))
            or not isinstance(observed_files, Sequence)
            or isinstance(observed_files, (str, bytes))
            or len(committed_files) != len(observed_files)
        ):
            raise ValueError("exact trusted VCS file closure is incomplete")
        observed_by_logical = {
            str(item.get("logical_path") or item.get("repository_path")): item
            for item in observed_files
            if isinstance(item, Mapping)
        }
        if len(observed_by_logical) != len(observed_files):
            raise ValueError("exact trusted VCS file identities are invalid")
        for expected_file in committed_files:
            if not isinstance(expected_file, Mapping):
                raise ValueError("exact trusted VCS file record is malformed")
            logical = _required_text(
                expected_file.get("logical_path")
                or expected_file.get("repository_path"),
                "trusted VCS logical path",
            )
            observed_file = observed_by_logical.get(logical)
            if not isinstance(observed_file, Mapping):
                raise ValueError(f"exact trusted VCS file is missing: {logical}")
            artifact = _normalize_artifact(
                observed_file, f"exact trusted VCS file {logical}"
            )
            if (
                artifact["sha256"] != expected_file.get("sha256")
                or artifact["size_bytes"] != expected_file.get("size_bytes")
                or observed_file.get("git_blob_sha1")
                != expected_file.get("git_blob_sha1")
            ):
                raise ValueError(f"exact trusted VCS file drifted: {logical}")

    if verification.get("data_quality_checks") != trusted_manifest.get(
        "data_quality_checks"
    ):
        raise ValueError("exact trusted data quality record drifted")


def _validate_exact_reference_values(value: Any) -> None:
    if (
        not isinstance(value, Mapping)
        or value.get("passed") is not True
        or value.get("reasons") != []
        or value.get("configuration_sha256") != TRUSTED_CANONICAL_CONFIG_SHA256
        or value.get("data_fingerprint") != TRUSTED_DATA_INVENTORY_SHA256
    ):
        raise ValueError("exact live likelihood reference verification is missing")
    artifact = _normalize_artifact(value, "exact likelihood reference registry")
    registry = _load_json_artifact(artifact, "exact likelihood reference registry")
    cases = registry.get("cases") if isinstance(registry, Mapping) else None
    observed = value.get("live_observed_chi2_by_case")
    if (
        artifact["sha256"] != TRUSTED_REFERENCE_SPEC_SHA256
        or value.get("payload") != registry
        or registry.get("schema_version") != 2
        or registry.get("profile_id") != EXACT_PROFILE_ID
        or not isinstance(cases, Sequence)
        or isinstance(cases, (str, bytes))
        or not cases
        or not isinstance(observed, Mapping)
    ):
        raise ValueError("exact likelihood reference registry drifted")
    case_ids = [
        _required_text(case.get("case_id"), "exact reference case id")
        if isinstance(case, Mapping)
        else ""
        for case in cases
    ]
    if (
        len(set(case_ids)) != len(case_ids)
        or set(observed) != set(case_ids)
        or any(not case_id for case_id in case_ids)
    ):
        raise ValueError("exact likelihood reference case set is invalid")
    for case in cases:
        assert isinstance(case, Mapping)
        case_id = str(case["case_id"])
        likelihoods = case.get("likelihoods")
        expected_values = case.get("values")
        observed_values = observed.get(case_id)
        if (
            not isinstance(likelihoods, Sequence)
            or isinstance(likelihoods, (str, bytes))
            or not isinstance(expected_values, Mapping)
            or not isinstance(observed_values, Mapping)
            or set(expected_values) != set(likelihoods)
            or set(observed_values) != set(likelihoods)
        ):
            raise ValueError(f"exact likelihood reference case is invalid: {case_id}")
        for likelihood in likelihoods:
            specification = expected_values[likelihood]
            if not isinstance(specification, Mapping):
                raise ValueError("exact likelihood reference value is malformed")
            expected = _finite_number(
                specification.get("expected_chi2"),
                f"exact reference expected chi2 {case_id}/{likelihood}",
            )
            tolerance = _finite_number(
                specification.get("absolute_tolerance"),
                f"exact reference tolerance {case_id}/{likelihood}",
            )
            measured = _finite_number(
                observed_values.get(likelihood),
                f"exact reference observed chi2 {case_id}/{likelihood}",
            )
            if tolerance < 0 or abs(measured - expected) > tolerance:
                raise ValueError(
                    f"exact likelihood reference value failed: {case_id}/{likelihood}"
                )
    _validate_loaded_likelihood_runtime(value.get("loaded_likelihood_runtime"))


def _validate_exact_preflight_receipt(
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    data: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    if (payload.get("schema_version"), payload.get("artifact_type")) != (
        PREFLIGHT_SCHEMA
    ):
        raise ValueError("exact preflight receipt schema is invalid")
    if (
        payload.get("profile_id") != EXACT_PROFILE_ID
        or payload.get("claim_scope") != EXACT_CLAIM_SCOPE
        or payload.get("target_commitment") != PREREGISTERED_TARGET_COMMITMENT
        or payload.get("passed") is not True
        or payload.get("status") != "PASS"
        or payload.get("failures") != []
    ):
        raise ValueError("exact preflight receipt is not a passed frozen profile")
    configuration = payload.get("configuration")
    if not isinstance(configuration, Mapping) or configuration.get(
        "sha256"
    ) != TRUSTED_CANONICAL_CONFIG_SHA256 or configuration.get("sha256") != config[
        "sha256"
    ]:
        raise ValueError("exact preflight configuration commitment mismatch")
    _validate_exact_source_state(payload.get("source_state"))

    inventory = payload.get("data")
    inventory_groups = inventory.get("groups") if isinstance(inventory, Mapping) else None
    if (
        not isinstance(inventory, Mapping)
        or inventory.get("complete") is not True
        or inventory.get("missing") != []
        or inventory.get("fingerprint") != TRUSTED_DATA_INVENTORY_SHA256
        or not isinstance(inventory_groups, Mapping)
        or set(inventory_groups) != set(REQUIRED_DATA_GROUPS)
    ):
        raise ValueError("exact preflight data inventory is not the frozen inventory")
    if set(data) != set(REQUIRED_DATA_GROUPS):
        raise ValueError("exact data artifact group set is incomplete or unexpected")
    for name, expected in REQUIRED_DATA_GROUPS.items():
        group = inventory_groups.get(name)
        if not isinstance(group, Mapping):
            raise ValueError(f"exact data inventory group is missing: {name}")
        inventory_files = group.get("files")
        if not isinstance(inventory_files, Sequence) or isinstance(
            inventory_files, (str, bytes)
        ):
            raise ValueError(f"exact data inventory files are missing: {name}")
        normalized_files = [
            {
                "path": item.get("logical_path"),
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
            }
            for item in data[name]
        ]
        if any(not item["path"] for item in normalized_files):
            raise ValueError(f"exact data logical paths are missing: {name}")
        if normalized_files != list(inventory_files):
            raise ValueError(f"exact data artifacts do not match preflight: {name}")
        summary = {
            "file_count": len(normalized_files),
            "total_size_bytes": sum(int(item["size_bytes"]) for item in normalized_files),
            "fingerprint": scientific_content_hash(normalized_files),
        }
        if summary != dict(expected) or group.get("fingerprint") != expected[
            "fingerprint"
        ]:
            raise ValueError(f"exact data group commitment mismatch: {name}")

    trusted_data = payload.get("trusted_data_manifest")
    if (
        not isinstance(trusted_data, Mapping)
        or trusted_data.get("passed") is not True
        or trusted_data.get("reasons") != []
        or trusted_data.get("sha256") != TRUSTED_DATA_MANIFEST_SHA256
        or trusted_data.get("overall_inventory_fingerprint")
        != TRUSTED_DATA_INVENTORY_SHA256
        or trusted_data.get("adequacy_inventory_fingerprint")
        != TRUSTED_ADEQUACY_DATA_INVENTORY_SHA256
        or trusted_data.get("group_fingerprints")
        != {
            name: record["fingerprint"]
            for name, record in REQUIRED_DATA_GROUPS.items()
        }
        or trusted_data.get("adequacy_group_fingerprints")
        != {
            name: record["fingerprint"]
            for name, record in REQUIRED_ADEQUACY_DATA_GROUPS.items()
        }
    ):
        raise ValueError("exact trusted data manifest verification is missing")
    trusted_data_artifact = _normalize_artifact(
        trusted_data, "exact trusted data manifest"
    )
    trusted_data_payload = _load_json_artifact(
        trusted_data_artifact, "exact trusted data manifest"
    )
    if (
        trusted_data_artifact["sha256"] != TRUSTED_DATA_MANIFEST_SHA256
        or trusted_data_payload.get("schema_version") != 1
        or trusted_data_payload.get("kind")
        != "w0wa_exact_data_byte_commitment"
        or trusted_data_payload.get("profile_id") != EXACT_PROFILE_ID
        or trusted_data_payload.get("frozen_before_formal_run") is not True
        or trusted_data_payload.get("overall_inventory_fingerprint")
        != TRUSTED_DATA_INVENTORY_SHA256
        or set(trusted_data_payload.get("groups") or {})
        != set(REQUIRED_DATA_GROUPS)
    ):
        raise ValueError("exact trusted data manifest bytes/schema drifted")
    # The immutable manifest stores the full byte list for compact groups and
    # an aggregate for very large likelihood trees.  In both cases the
    # preflight receipt supplies every file and the verifier has already
    # re-derived the frozen aggregate above.  If the manifest additionally
    # freezes files, require byte-for-byte identity instead of ignoring it.
    _validate_exact_trusted_data_groups(trusted_data_payload, inventory_groups)
    _validate_exact_data_provenance(trusted_data, trusted_data_payload)
    _validate_exact_adequacy_data_inventory(
        payload.get("adequacy_data"), trusted_manifest=trusted_data_payload
    )

    environment = payload.get("environment")
    if not isinstance(environment, Mapping) or environment.get("passed") is not True or (
        environment.get("reasons") != []
    ):
        raise ValueError("exact locked environment did not pass")
    environment_payload = {
        key: item
        for key, item in environment.items()
        if key not in {"passed", "reasons", "fingerprint"}
    }
    if environment.get("fingerprint") != scientific_content_hash(
        environment_payload
    ):
        raise ValueError("exact locked environment fingerprint is inconsistent")
    runtime_environment = environment.get("runtime")
    native_runtime = (
        runtime_environment.get("native_runtime")
        if isinstance(runtime_environment, Mapping)
        else None
    )
    native_binaries = (
        native_runtime.get("binaries")
        if isinstance(native_runtime, Mapping)
        else None
    )
    if (
        not isinstance(native_runtime, Mapping)
        or native_runtime.get("passed") is not True
        or native_runtime.get("reasons") != []
        or native_runtime.get("fingerprint") != TRUSTED_NATIVE_RUNTIME_FINGERPRINT
        or native_runtime.get("fingerprint_scope")
        != "byte_and_build_identity_excluding_absolute_paths"
        or not isinstance(native_binaries, Mapping)
        or set(native_binaries) != set(TRUSTED_NATIVE_RUNTIME_SHA256)
    ):
        raise ValueError("exact native MPI/numerical runtime is not frozen")
    thread_environment = (
        runtime_environment.get("thread_environment")
        if isinstance(runtime_environment, Mapping)
        else None
    )
    if thread_environment != {
        "OMP_NUM_THREADS": "3",
        "MKL_NUM_THREADS": "3",
        "OPENBLAS_NUM_THREADS": "3",
    }:
        raise ValueError("exact numerical thread environment is not 3/3/3")
    for name, expected_hash in TRUSTED_NATIVE_RUNTIME_SHA256.items():
        binary = _require_canonical_nested_artifact(
            native_binaries[name], f"exact native runtime binary {name}"
        )
        if binary["sha256"] != expected_hash:
            raise ValueError(f"exact native runtime binary drift: {name}")
    mpi_vendor = native_runtime.get("mpi_vendor")
    if (
        not isinstance(mpi_vendor, Mapping)
        or mpi_vendor.get("name") != "Open MPI"
        or mpi_vendor.get("version") != [5, 0, 9]
        or not _required_text(
            mpi_vendor.get("library_version"), "native MPI library version"
        )
        or not _required_text(
            mpi_vendor.get("linked_libraries"), "native MPI linked libraries"
        )
        or "Open MPI" not in _required_text(
            native_runtime.get("mpirun_version"), "native mpirun version"
        )
    ):
        raise ValueError("exact native MPI vendor/linkage mismatch")
    for package in ("numpy_build", "scipy_build"):
        build = native_runtime.get(package)
        blas = (
            ((build.get("Build Dependencies") or {}).get("blas") or {}).get("name")
            if isinstance(build, Mapping)
            else None
        )
        if str(blas).lower() != "accelerate":
            raise ValueError(f"exact {package} BLAS backend mismatch")
    fingerprint_binaries = {
        name: {
            key: item
            for key, item in dict(record).items()
            if key != "path"
        }
        for name, record in native_binaries.items()
        if isinstance(record, Mapping)
    }
    if scientific_content_hash(
        {
            "binaries": fingerprint_binaries,
            "mpirun_version": native_runtime.get("mpirun_version"),
            "mpi_vendor": native_runtime.get("mpi_vendor"),
            "numpy_build": native_runtime.get("numpy_build"),
            "scipy_build": native_runtime.get("scipy_build"),
        }
    ) != TRUSTED_NATIVE_RUNTIME_FINGERPRINT:
        raise ValueError("exact native runtime fingerprint is not self-consistent")
    lock = environment.get("lock")
    if not isinstance(lock, Mapping) or lock.get(
        "sha256"
    ) != TRUSTED_DEPENDENCY_LOCK_SHA256:
        raise ValueError("exact dependency lock commitment mismatch")
    lock_artifact = _normalize_artifact(lock, "exact dependency lock")
    if lock_artifact["sha256"] != TRUSTED_DEPENDENCY_LOCK_SHA256:
        raise ValueError("exact dependency lock bytes drifted")
    if any(
        (environment.get("required_versions") or {}).get(
            _canonical_distribution_name(name)
        )
        != version
        for name, version in REQUIRED_PACKAGE_VERSIONS.items()
    ):
        raise ValueError("exact required package versions mismatch")
    wheel_manifest = environment.get("wheel_manifest")
    if not isinstance(wheel_manifest, Mapping) or wheel_manifest.get(
        "sha256"
    ) != TRUSTED_WHEEL_MANIFEST_SHA256:
        raise ValueError("exact wheel manifest commitment mismatch")
    wheel_manifest_artifact = _normalize_artifact(
        wheel_manifest, "exact preflight wheel_manifest"
    )
    manifest_payload = _load_json_artifact(
        wheel_manifest_artifact, "exact preflight wheel_manifest"
    )
    manifest_wheels = manifest_payload.get("wheels")
    if (
        manifest_payload.get("schema_version") != 1
        or manifest_payload.get("profile_id") != EXACT_PROFILE_ID
        or manifest_payload.get("created_before_smoke_or_formal_run") is not True
        or manifest_payload.get("requirements_path")
        != "w0wa_exact_requirements.txt"
        or manifest_payload.get("requirements_sha256")
        != TRUSTED_DEPENDENCY_LOCK_SHA256
        or not isinstance(manifest_wheels, Sequence)
        or isinstance(manifest_wheels, (str, bytes))
        or len(manifest_wheels) != 52
    ):
        raise ValueError("exact wheel manifest schema is invalid")
    expected_wheels = {
        str(item.get("filename")): {
            "project": item.get("project"),
            "version": item.get("version"),
            "filename": item.get("filename"),
            "size_bytes": item.get("size_bytes"),
            "sha256": item.get("sha256"),
        }
        for item in manifest_wheels
        if isinstance(item, Mapping)
    }
    observed_wheels: dict[str, dict[str, Any]] = {}
    observed_wheel_artifacts: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(environment.get("wheels") or []):
        if not isinstance(item, Mapping):
            raise ValueError("exact wheel record is malformed")
        artifact = _normalize_artifact(item, f"exact wheel {index}")
        filename = str(item.get("filename") or "")
        if not filename or Path(artifact["path"]).name != filename:
            raise ValueError("exact wheel filename/path mismatch")
        observed_wheels[filename] = {
            "project": item.get("project"),
            "version": item.get("version"),
            "filename": filename,
            "size_bytes": artifact["size_bytes"],
            "sha256": artifact["sha256"],
        }
        observed_wheel_artifacts[filename] = artifact
    if (
        not expected_wheels
        or len(observed_wheels) != len(environment.get("wheels") or [])
        or observed_wheels != expected_wheels
    ):
        raise ValueError("exact full wheel closure byte commitments mismatch")
    if any(
        expected_wheels.get(filename, {}).get("sha256") != expected_hash
        for filename, expected_hash in REQUIRED_WHEEL_SHA256.items()
    ):
        raise ValueError("exact direct wheel commitments mismatch")
    _validate_exact_environment_closure(
        environment,
        manifest_wheels=[dict(item) for item in manifest_wheels],
        observed_wheel_artifacts=observed_wheel_artifacts,
    )

    _validate_exact_likelihood_code_manifest(
        payload.get("likelihood_code_manifest")
    )
    _validate_exact_reference_values(payload.get("reference_likelihood_values"))


def _exact_plan_artifact_payload(
    generation_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Open and revalidate the immutable exact model-adequacy plan.

    A self-hashed generation receipt is not authority by itself: the public
    builder is callable.  This routine therefore reopens every registered input
    and checks the scientific parts of the plan rather than trusting copied
    path/hash fields.
    """

    raw_record = generation_payload.get("model_adequacy_plan")
    if not isinstance(raw_record, Mapping):
        raise ValueError("exact generation model adequacy plan is missing")
    plan_artifact = _normalize_artifact(
        raw_record, "exact generation model adequacy plan"
    )
    plan = _load_json_artifact(plan_artifact, "exact generation model adequacy plan")
    _validate_named_self_hash(
        plan,
        field="exact generation model adequacy plan",
        candidates=("plan_sha256",),
    )
    if (
        plan.get("schema_version") != 2
        or plan.get("artifact_type") != "w0wa_model_adequacy_plan"
        or plan.get("profile_id") != EXACT_PROFILE_ID
        or plan.get("target_commitment") != PREREGISTERED_TARGET_COMMITMENT
        or plan.get("status") != "INPUTS_FROZEN_OUTPUTS_PENDING"
        or raw_record.get("plan_sha256") != plan.get("plan_sha256")
    ):
        raise ValueError("exact generation model adequacy plan contract is invalid")

    raw_configs = plan.get("configs")
    copied_configs = raw_record.get("configs")
    if (
        not isinstance(raw_configs, Mapping)
        or not isinstance(copied_configs, Mapping)
        or set(raw_configs) != _EXACT_PLAN_CONFIG_KEYS
        or set(copied_configs) != _EXACT_PLAN_CONFIG_KEYS
    ):
        raise ValueError("exact generation adequacy config set is incomplete")
    normalized_configs: dict[str, dict[str, Any]] = {}
    for key in sorted(_EXACT_PLAN_CONFIG_KEYS):
        registered = _normalize_artifact(
            raw_configs[key], f"exact adequacy config {key}"
        )
        copied = _normalize_artifact(
            copied_configs[key], f"copied exact adequacy config {key}"
        )
        if registered != copied:
            raise ValueError(f"exact adequacy copied config drift: {key}")
        normalized_configs[key] = registered

    ppc_artifact = _normalize_artifact(
        plan.get("predictive_checks"), "exact predictive-check plan"
    )
    copied_ppc = _normalize_artifact(
        raw_record.get("predictive_checks"), "copied exact predictive-check plan"
    )
    if ppc_artifact != copied_ppc:
        raise ValueError("exact predictive-check plan copy drift")
    ppc = _load_json_artifact(ppc_artifact, "exact predictive-check plan")
    if (
        ppc.get("schema_version") != 1
        or ppc.get("kind") != "w0wa_predictive_check_plan"
        or ppc.get("profile_id") != EXACT_PROFILE_ID
        or ppc.get("acceptance_rule")
        != {
            "tail_probability": _EXACT_PPC_TAIL_RULE,
            "lower_inclusive": _EXACT_PPC_LOWER,
            "upper_inclusive": _EXACT_PPC_UPPER,
            "all_discrepancies_must_pass": True,
            "missing_or_nonfinite": "fail",
        }
    ):
        raise ValueError("exact predictive-check rule is not frozen")
    ppc_checks = ppc.get("checks")
    if not isinstance(ppc_checks, Mapping) or set(ppc_checks) != {
        "prior_predictive_check",
        "posterior_predictive_check",
    }:
        raise ValueError("exact predictive-check types are incomplete")
    for check, spec in ppc_checks.items():
        if (
            not isinstance(spec, Mapping)
            or _integer(spec.get("minimum_replicates"), f"{check} replicates")
            != 400
            or set(spec.get("required_discrepancies") or ())
            != _REQUIRED_PREDICTIVE_DISCREPANCIES
        ):
            raise ValueError(f"exact predictive-check specification drift: {check}")
        seeds = spec.get("seed_entropy")
        if (
            not isinstance(seeds, Sequence)
            or isinstance(seeds, (str, bytes))
            or [_integer(seed, f"{check} seed") for seed in seeds]
            != _EXACT_PPC_SEEDS[check]
        ):
            raise ValueError(f"exact predictive-check seeds are invalid: {check}")

    injection_artifact = _normalize_artifact(
        plan.get("injection_recovery"), "exact injection-recovery plan"
    )
    copied_injection = _normalize_artifact(
        raw_record.get("injection_recovery"), "copied exact injection-recovery plan"
    )
    if injection_artifact != copied_injection:
        raise ValueError("exact injection-recovery plan copy drift")
    injection = _load_json_artifact(
        injection_artifact, "exact injection-recovery plan"
    )
    fiducials = injection.get("fiducials")
    joint_region = injection.get("joint_region")
    bias_definition = injection.get("standardized_bias")
    if (
        injection.get("schema_version") != 1
        or injection.get("kind") != "w0wa_injection_recovery_plan"
        or injection.get("profile_id") != EXACT_PROFILE_ID
        or not isinstance(joint_region, Mapping)
        or _float_or_none(joint_region.get("coverage")) != 0.95
        or joint_region.get("statistic")
        != (
            "d2=(recovered_center-truth)^T recovered_covariance^-1 "
            "(recovered_center-truth)"
        )
        or joint_region.get("distribution") != "chi_square_df_2"
        or _float_or_none(joint_region.get("threshold_inclusive")) != _CHI2_2D_95
        or not isinstance(bias_definition, Mapping)
        or _float_or_none(bias_definition.get("maximum_aggregate_exclusive"))
        != 0.30
        or not isinstance(fiducials, Sequence)
        or isinstance(fiducials, (str, bytes))
        or len(fiducials) != 3
    ):
        raise ValueError("exact injection-recovery plan is invalid")
    observed_truths: dict[str, dict[str, float]] = {}
    injection_seeds: set[int] = set()
    for record in fiducials:
        if not isinstance(record, Mapping):
            raise ValueError("exact injection fiducial is malformed")
        name = _required_text(record.get("name"), "exact injection name")
        truth = record.get("truth")
        if not isinstance(truth, Mapping):
            raise ValueError("exact injection truth is missing")
        expected_fields = set(_REQUIRED_INJECTION_TRUTHS.get(name, {}))
        if set(truth) != expected_fields:
            raise ValueError("exact injection full truth vector is incomplete")
        observed_truths[name] = {
            field: _finite_number(truth.get(field), f"{name}.{field}")
            for field in sorted(expected_fields)
        }
        seed = _integer(record.get("simulation_seed"), f"{name}.simulation_seed")
        if seed != _EXACT_INJECTION_SEEDS.get(name):
            raise ValueError("exact injection seed drifted")
        if seed in injection_seeds:
            raise ValueError("exact injection seeds are not unique")
        injection_seeds.add(seed)
    expected_truths = {
        name: {field: truth[field] for field in sorted(truth)}
        for name, truth in _REQUIRED_INJECTION_TRUTHS.items()
    }
    if observed_truths != expected_truths:
        raise ValueError("exact injection-recovery truths drifted")
    raw_injection_record = raw_record.get("injection_recovery")
    if not isinstance(raw_injection_record, Mapping) or raw_injection_record.get(
        "truth_commitment"
    ) != scientific_content_hash(list(fiducials)):
        raise ValueError("exact injection truth commitment is inconsistent")
    if injection.get("joint_parameters") != ["w", "wa"]:
        raise ValueError("exact injection joint-region definition is invalid")

    _validate_exact_pantheon_variant(plan, normalized_configs)
    _validate_exact_plan_config_semantics(
        generation_payload,
        plan=plan,
        configs=normalized_configs,
    )
    return plan_artifact, plan, ppc, injection


def _validate_exact_pantheon_variant(
    plan: Mapping[str, Any], configs: Mapping[str, Mapping[str, Any]]
) -> None:
    variant = plan.get("pantheon_covariance_variant")
    if (
        not isinstance(variant, Mapping)
        or variant.get("variant") != "official_statistical_only"
        or variant.get("construction")
        != "official Pantheon+SH0ES_STATONLY.cov copied unmodified"
        or variant.get("redshift_selection")
        != "PantheonPlus likelihood zHD > 0.01 unchanged"
    ):
        raise ValueError("exact Pantheon+ covariance variant is invalid")
    source = _normalize_artifact(
        variant.get("source_data"), "exact Pantheon+ source data"
    )
    expected_suffix = str(PANTHEONPLUS_SOURCE_DATA["logical_path"])
    if (
        source["sha256"] != PANTHEONPLUS_SOURCE_DATA["sha256"]
        or not source["path"].endswith(expected_suffix)
        or _integer(
            (variant.get("source_data") or {}).get("rows_before_selection"),
            "Pantheon+ rows_before_selection",
        )
        != PANTHEONPLUS_SOURCE_DATA["rows"]
    ):
        raise ValueError("exact Pantheon+ source is not the frozen release")
    source_metadata = variant.get("source_data")
    lines = Path(source["path"]).read_text(encoding="utf-8").splitlines()
    columns = lines[0].lstrip("#").split() if lines else []
    if "zHD" not in columns:
        raise ValueError("exact Pantheon+ source lacks zHD")
    redshift_index = columns.index("zHD")
    retained = 0
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != len(columns):
            raise ValueError("exact Pantheon+ source row is malformed")
        redshift = _finite_number(fields[redshift_index], "Pantheon+ zHD")
        retained += int(redshift > 0.01)
    if not isinstance(source_metadata, Mapping) or _integer(
        source_metadata.get("rows_after_selection"),
        "Pantheon+ rows_after_selection",
    ) != retained:
        raise ValueError("exact Pantheon+ redshift selection count drifted")
    covariance = _require_canonical_nested_artifact(
        variant.get("generated_covariance"), "exact Pantheon+ generated covariance"
    )
    source_covariance = _normalize_artifact(
        variant.get("source_covariance"), "exact Pantheon+ STATONLY covariance"
    )
    source_covariance_metadata = variant.get("source_covariance")
    if (
        source_covariance["sha256"]
        != PANTHEONPLUS_STATONLY_COVARIANCE["sha256"]
        or source_covariance["size_bytes"]
        != PANTHEONPLUS_STATONLY_COVARIANCE["size_bytes"]
        or not isinstance(source_covariance_metadata, Mapping)
        or any(
            source_covariance_metadata.get(field)
            != PANTHEONPLUS_STATONLY_COVARIANCE[field]
            for field in (
                "repository",
                "commit",
                "git_blob_sha1",
                "repository_path",
            )
        )
        or covariance["sha256"] != source_covariance["sha256"]
        or covariance["size_bytes"] != source_covariance["size_bytes"]
    ):
        raise ValueError("exact Pantheon+ STATONLY covariance provenance drifted")
    dataset = _require_canonical_nested_artifact(
        variant.get("generated_dataset"), "exact Pantheon+ generated dataset"
    )
    dataset_text = Path(dataset["path"]).read_text(encoding="utf-8")
    if (
        f"data_file = {source['path']}" not in dataset_text
        or f"mag_covmat_file = {covariance['path']}" not in dataset_text
    ):
        raise ValueError("exact Pantheon+ generated dataset is not source/covariance bound")
    variant_config = _load_yaml_artifact(
        configs["pantheonplus_covariance"], "exact Pantheon+ variant config"
    )
    likelihood = variant_config.get("likelihood")
    pantheon = likelihood.get("sn.pantheonplus") if isinstance(likelihood, Mapping) else None
    if not isinstance(pantheon, Mapping) or pantheon.get("dataset_file") != dataset[
        "path"
    ]:
        raise ValueError("exact Pantheon+ variant config does not use generated dataset")


def _validate_exact_plan_config_semantics(
    generation_payload: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    configs: Mapping[str, Mapping[str, Any]],
) -> None:
    """Re-derive every planned config from the trusted canonical YAML."""

    configuration = generation_payload.get("configuration")
    # The producer/consumer unit test intentionally passes only the returned
    # plan record. Formal generation receipts always include configuration and
    # are checked here; the reduced test path still exercises artifact closure.
    if not isinstance(configuration, Mapping):
        return
    canonical_path = Path(
        _required_text(configuration.get("canonical"), "generation canonical path")
    ).expanduser().resolve()
    if not canonical_path.is_file() or _hash_file(canonical_path) != (
        TRUSTED_CANONICAL_CONFIG_SHA256
    ):
        raise ValueError("exact adequacy plan canonical config is not trusted")
    try:
        canonical = yaml.safe_load(canonical_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("exact canonical config is unreadable") from exc
    if not isinstance(canonical, Mapping):
        raise ValueError("exact canonical config is not a mapping")
    expected: dict[str, dict[str, Any]] = {
        "planck_pr3_plik": copy.deepcopy(dict(canonical)),
    }

    smoke = copy.deepcopy(dict(canonical))
    smoke["sampler"]["mcmc"]["max_samples"] = 16
    smoke["sampler"]["mcmc"]["learn_every"] = 4
    expected["non_citable_smoke"] = smoke

    widened = copy.deepcopy(dict(canonical))
    widened["params"]["w"]["prior"] = {"min": -5.0, "max": 2.0}
    widened["params"]["wa"]["prior"] = {"min": -5.0, "max": 3.0}
    expected["prior_w0wa_widened"] = widened

    camspec = copy.deepcopy(dict(canonical))
    camspec["likelihood"].pop("planck_2018_highl_plik.TTTEEE")
    camspec["likelihood"]["planck_NPIPE_highl_CamSpec.TTTEEE"] = None
    expected["planck_pr4_camspec"] = camspec

    lensing = copy.deepcopy(dict(canonical))
    lensing["likelihood"]["act_dr6_lenslike.ACTDR6LensLike"] = {
        "variant": "act_baseline",
        "lens_only": False,
    }
    expected["lensing_combination"] = lensing

    variant = plan["pantheon_covariance_variant"]
    pantheon = copy.deepcopy(dict(canonical))
    pantheon["likelihood"]["sn.pantheonplus"] = {
        "dataset_file": variant["generated_dataset"]["path"]
    }
    expected["pantheonplus_covariance"] = pantheon

    independent = copy.deepcopy(dict(canonical))
    independent["sampler"]["mcmc"]["seed"] = list(_EXACT_INDEPENDENT_SEEDS)
    expected["independent_reproduction"] = independent

    for key, expected_payload in expected.items():
        observed = _load_yaml_artifact(configs[key], f"exact adequacy config {key}")
        if observed != expected_payload:
            raise ValueError(f"exact adequacy config semantics drift: {key}")
    raw_configs = plan.get("configs")
    smoke_record = (
        raw_configs.get("non_citable_smoke")
        if isinstance(raw_configs, Mapping)
        else None
    )
    if not isinstance(smoke_record, Mapping) or (
        smoke_record.get("non_citable") is not True
        or smoke_record.get("evidence_class") != "non_citable_smoke"
    ):
        raise ValueError("exact smoke config is not permanently non-citable")
    identities = plan.get("independent_seed_binding")
    if (
        not isinstance(identities, Sequence)
        or isinstance(identities, (str, bytes))
        or len(identities) != 4
        or any(
            not isinstance(identity, Mapping)
            or identity.get("rank") != rank
            or identity.get("entropy") != _EXACT_INDEPENDENT_SEEDS
            or identity.get("spawn_key") != [rank]
            for rank, identity in enumerate(identities)
        )
    ):
        raise ValueError("exact independent seed plan drifted")


def _validate_exact_run_role(
    *,
    config: Mapping[str, Any],
    generation_payload: Mapping[str, Any],
    evidence_class: Any,
    expected_role: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Bind an exact run to the only config permitted for its evidence role."""

    evidence = _required_text(evidence_class, "exact run evidence_class")
    if evidence == "formal_candidate" and config.get("sha256") != (
        TRUSTED_CANONICAL_CONFIG_SHA256
    ):
        # Preserve a fail-fast boundary for attempts to relabel toy/proxy runs.
        raise ValueError("exact canonical config SHA-256 is not frozen")
    _, plan, _, _ = _exact_plan_artifact_payload(generation_payload)
    raw_configs = plan.get("configs")
    if not isinstance(raw_configs, Mapping):
        raise ValueError("exact generation adequacy configs are missing")
    canonical = _normalize_artifact(
        raw_configs.get("planck_pr3_plik"), "exact registered primary config"
    )
    independent = _normalize_artifact(
        raw_configs.get("independent_reproduction"),
        "exact registered independent config",
    )
    if canonical["sha256"] != TRUSTED_CANONICAL_CONFIG_SHA256:
        raise ValueError("exact registered canonical config SHA-256 is not frozen")
    if evidence == "formal_candidate":
        role = "primary"
        expected_config = canonical
    elif evidence == "model_adequacy":
        role = "independent_reproduction"
        expected_config = independent
    else:
        raise ValueError("exact run evidence class is not allowed")
    if dict(config) != expected_config:
        raise ValueError(f"exact {role} run config is not its registered generation config")
    if expected_role is not None and role != expected_role:
        raise ValueError(
            f"exact run role mismatch: expected {expected_role}, observed {role}"
        )
    return role, canonical


def _validate_exact_generation_receipt(
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    preflight_artifact: Mapping[str, Any],
) -> None:
    if (payload.get("schema_version"), payload.get("artifact_type")) != (
        GENERATION_SCHEMA
    ):
        raise ValueError("exact generation receipt schema is invalid")
    if (
        payload.get("profile_id") != EXACT_PROFILE_ID
        or payload.get("claim_scope") != EXACT_CLAIM_SCOPE
        or payload.get("target_commitment") != PREREGISTERED_TARGET_COMMITMENT
        or payload.get("passed") is not True
    ):
        raise ValueError("exact generation receipt did not pass")
    upstream = payload.get("preflight")
    if not isinstance(upstream, Mapping) or upstream.get(
        "sha256"
    ) != preflight_artifact["sha256"]:
        raise ValueError("exact generation receipt preflight binding mismatch")
    configuration = payload.get("configuration")
    if not isinstance(configuration, Mapping) or configuration.get(
        "canonical"
    ) != config["path"]:
        raise ValueError("exact generation canonical config binding mismatch")
    _exact_plan_artifact_payload(payload)


def _validate_exact_artifact_contract(
    *,
    config: Mapping[str, Any],
    data: Mapping[str, Sequence[Mapping[str, Any]]],
    likelihoods: Mapping[str, Sequence[Mapping[str, Any]]],
    sampled_parameters: Sequence[str],
    preflight_payload: Mapping[str, Any],
    preflight_artifact: Mapping[str, Any],
    generation_payload: Mapping[str, Any],
    code_artifacts: Sequence[Mapping[str, Any]],
    evidence_class: Any,
    expected_role: str | None = None,
) -> str:
    role, canonical_config = _validate_exact_run_role(
        config=config,
        generation_payload=generation_payload,
        evidence_class=evidence_class,
        expected_role=expected_role,
    )
    if tuple(sampled_parameters) != REQUIRED_SAMPLED_PARAMETERS:
        raise ValueError(
            "exact sampled parameters do not include the complete cosmological and nuisance set"
        )
    if set(likelihoods) != set(REQUIRED_LIKELIHOODS):
        raise ValueError("exact likelihood artifact group set is incomplete or unexpected")
    for name, artifacts in likelihoods.items():
        if len(artifacts) != 1 or artifacts[0][
            "sha256"
        ] != TRUSTED_LIKELIHOOD_CODE_MANIFEST_SHA256:
            raise ValueError(f"exact likelihood/code manifest mismatch: {name}")
    code_hashes = _artifact_code_hashes(code_artifacts)
    if not _REQUIRED_EXACT_AUDIT_CODE.issubset(code_hashes):
        raise ValueError("exact acceptance/signer code audit closure is incomplete")
    for filename, expected_hash in TRUSTED_CODE_SHA256.items():
        if filename == "canonical_full_likelihood_evidence.py" and code_hashes.get(
            filename
        ) != expected_hash:
            raise ValueError("exact canonical producer code hash mismatch")
    _validate_exact_preflight_receipt(
        preflight_payload,
        config=canonical_config,
        data=data,
    )
    _validate_exact_generation_receipt(
        generation_payload,
        config=canonical_config,
        preflight_artifact=preflight_artifact,
    )
    return role


def build_research_alpha_run_authority_attestation(
    *,
    run_id: str,
    chain_artifacts: Sequence[Mapping[str, Any]],
    config_artifact: Mapping[str, Any],
    data_artifacts: Mapping[str, Any],
    likelihood_artifacts: Mapping[str, Any],
    sampled_parameters_artifact: Mapping[str, Any],
    canonical_run_receipt_artifact: Mapping[str, Any],
    preflight_artifact: Mapping[str, Any],
    generation_artifact: Mapping[str, Any],
    code_artifacts: Sequence[Mapping[str, Any]],
    protocol_amendment_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive an HMAC wrapper from a completed canonical runner receipt."""

    canonical_run_id = _required_text(run_id, "run_id")
    sampled_artifact = _normalize_artifact(
        sampled_parameters_artifact, "sampled_parameters_artifact"
    )
    sampled = _sampled_parameters_from_artifact(
        sampled_artifact, run_id=canonical_run_id
    )
    chains = _normalize_chain_artifacts(
        chain_artifacts,
        run_id=canonical_run_id,
        sampled_parameters=sampled,
    )
    config = _normalize_artifact(config_artifact, "config_artifact")
    data = _normalize_artifact_groups(data_artifacts, "data_artifacts")
    likelihoods = _normalize_artifact_groups(
        likelihood_artifacts, "likelihood_artifacts"
    )
    receipt_artifact = _require_canonical_nested_artifact(
        canonical_run_receipt_artifact, "canonical_run_receipt_artifact"
    )
    receipt = _load_json_artifact(receipt_artifact, "canonical_run_receipt_artifact")
    if receipt.get("schema_version") != 2 or receipt.get("kind") != "chain":
        raise ValueError("canonical run receipt schema is invalid")
    _validate_named_self_hash(
        receipt,
        field="canonical run receipt",
        candidates=("attestation_sha256",),
    )
    profile_id = _normalize_research_profile(receipt.get("profile_id"))
    evidence_class = receipt.get("evidence_class")
    evidence_class_allowed = (
        evidence_class in {"formal_candidate", "model_adequacy"}
        if profile_id == EXACT_PROFILE_ID
        else evidence_class == "ci_fixture"
    )
    if (
        receipt.get("run_id") != canonical_run_id
        or not evidence_class_allowed
        or receipt.get("status") != "completed"
        or receipt.get("success") is not True
        or receipt.get("returncode") != 0
    ):
        raise ValueError("canonical run receipt is not a successful certified run")
    receipt_protocol_status = _normalize_protocol_status(
        receipt.get("protocol_status")
    )
    if profile_id == EXACT_PROFILE_ID and receipt_protocol_status != _PROTOCOL_STATUS:
        raise ValueError("exact canonical run cannot claim analyst blinding was achieved")
    if (
        profile_id == EXACT_PROFILE_ID
        and receipt.get("host_execution_trust_boundary")
        != EXACT_HOST_EXECUTION_TRUST_BOUNDARY
    ):
        raise ValueError("exact canonical run host execution trust boundary mismatch")
    if receipt.get("config_path") != config["path"] or receipt.get(
        "config_sha256"
    ) != config["sha256"]:
        raise ValueError("canonical run receipt config mismatch")
    receipt_artifacts = receipt.get("artifacts")
    if not isinstance(receipt_artifacts, Sequence) or isinstance(
        receipt_artifacts, (str, bytes)
    ):
        raise ValueError("canonical run receipt artifacts are missing")
    receipt_file_bindings = {
        (str(item.get("path")), str(item.get("sha256")))
        for item in receipt_artifacts
        if isinstance(item, Mapping)
    }
    if not all(
        (chain["path"], chain["sha256"]) in receipt_file_bindings
        for chain in chains
    ):
        raise ValueError("canonical run receipt does not bind all chain files")
    preflight = _require_canonical_nested_artifact(
        preflight_artifact, "preflight_artifact"
    )
    generation = _require_canonical_nested_artifact(
        generation_artifact, "generation_artifact"
    )
    preflight_payload = _load_json_artifact(preflight, "preflight_artifact")
    generation_payload = _load_json_artifact(generation, "generation_artifact")
    _validate_named_self_hash(
        preflight_payload,
        field="preflight artifact",
        candidates=("preflight_sha256",),
    )
    _validate_named_self_hash(
        generation_payload,
        field="generation artifact",
        candidates=("generation_sha256",),
    )
    workflow_receipts = receipt.get("workflow_receipts")
    if not isinstance(workflow_receipts, Mapping) or workflow_receipts.get(
        "preflight_sha256"
    ) != preflight_payload["preflight_sha256"] or workflow_receipts.get(
        "generation_sha256"
    ) != generation_payload["generation_sha256"]:
        raise ValueError("canonical run receipt workflow binding mismatch")
    resources = receipt.get("resource_binding")
    if not isinstance(resources, Mapping):
        raise ValueError("canonical run receipt resource binding is missing")
    mpi_processes = _integer(resources.get("mpi_processes"), "mpi_processes")
    threads_per_process = _integer(
        resources.get("threads_per_process"), "threads_per_process"
    )
    environment_fingerprint = receipt.get("environment_fingerprint")
    _require_sha256(environment_fingerprint, "environment_fingerprint")
    seed_binding = receipt.get("seed_binding")
    if not isinstance(seed_binding, Mapping):
        raise ValueError("canonical run receipt seed binding is missing")
    raw_termination = receipt.get("termination")
    if not isinstance(raw_termination, Mapping):
        raise ValueError("canonical run receipt termination is missing")
    checkpoint_record = raw_termination.get("checkpoint")
    checkpoint = _normalize_artifact(
        checkpoint_record, "canonical run receipt checkpoint"
    )
    termination = {
        "passed": raw_termination.get("passed"),
        "status": raw_termination.get("status"),
        "max_samples_reached": raw_termination.get("max_samples_reached"),
        "early_stop": raw_termination.get("early_stop"),
        "mpi_size": (
            checkpoint_record.get("mpi_size")
            if isinstance(checkpoint_record, Mapping)
            else None
        ),
        "checkpoint_artifact": checkpoint,
    }
    amendment = _require_canonical_nested_artifact(
        protocol_amendment_artifact, "protocol_amendment_artifact"
    )
    if amendment["sha256"] != _PROTOCOL_AMENDMENT_SHA256 or receipt.get(
        "protocol_amendment_sha256"
    ) != _PROTOCOL_AMENDMENT_SHA256:
        raise ValueError("canonical run receipt protocol amendment mismatch")
    normalized_code_artifacts = _require_canonical_artifact_list(
        code_artifacts, "code_artifacts"
    )
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_artifact_contract(
            config=config,
            data=data,
            likelihoods=likelihoods,
            sampled_parameters=sampled,
            preflight_payload=preflight_payload,
            preflight_artifact=preflight,
            generation_payload=generation_payload,
            code_artifacts=normalized_code_artifacts,
            evidence_class=evidence_class,
        )
    payload = {
        "source": "server_attested",
        "profile_id": profile_id,
        "run_id": canonical_run_id,
        "evidence_class": receipt["evidence_class"],
        "status": receipt["status"],
        "success": receipt["success"],
        "returncode": receipt["returncode"],
        "runner": "cobaya",
        "mpi_processes": mpi_processes,
        "threads_per_process": threads_per_process,
        "environment_fingerprint": environment_fingerprint,
        "protocol_status": receipt_protocol_status,
        "protocol_amendment_artifact": amendment,
        "canonical_run_receipt_artifact": receipt_artifact,
        "preflight_artifact": preflight,
        "generation_artifact": generation,
        "code_artifacts": normalized_code_artifacts,
        "workflow_receipt_artifacts": [preflight, generation],
        "checkpoint_artifacts": [checkpoint],
        "config_artifact": config,
        "data_artifacts": data,
        "likelihood_artifacts": likelihoods,
        "sampled_parameters_artifact": sampled_artifact,
        "chain_artifacts": chains,
        "seed_binding": dict(seed_binding),
        "termination": termination,
    }
    if profile_id == EXACT_PROFILE_ID:
        payload["host_execution_trust_boundary"] = copy.deepcopy(
            EXACT_HOST_EXECUTION_TRUST_BOUNDARY
        )
    return _build_research_alpha_scientific_attestation(
        attestation_type=_RUN_ATTESTATION_TYPE,
        payload=payload,
    )


def build_research_alpha_analysis_authority_attestation(
    *,
    run_id: str,
    run_attestation_artifact: Mapping[str, Any],
    analysis_receipt_artifact: Mapping[str, Any],
    offline_grade_receipt_artifact: Mapping[str, Any],
    analysis_code_artifact: Mapping[str, Any],
    chain_artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Derive the analyzer HMAC from a self-hashed canonical analysis receipt."""

    canonical_run_id = _required_text(run_id, "run_id")
    receipt_artifact = _require_canonical_nested_artifact(
        analysis_receipt_artifact, "analysis_receipt_artifact"
    )
    receipt = _load_json_artifact(receipt_artifact, "analysis_receipt_artifact")
    profile_id = _normalize_research_profile(receipt.get("profile_id"))
    if (
        receipt.get("schema_version") != 2
        or receipt.get("artifact_type") != "w0wa_exact_analysis"
        or receipt.get("status") != "ANALYZED"
        or ((receipt.get("run_identity") or {}).get("run_id") != canonical_run_id)
    ):
        raise ValueError("canonical analysis receipt identity is invalid")
    _validate_named_self_hash(
        receipt,
        field="canonical analysis receipt",
        candidates=("manifest_sha256",),
    )
    binding = receipt.get("research_alpha_binding")
    if not isinstance(binding, Mapping):
        raise ValueError("canonical analysis receipt research_alpha_binding is missing")
    sampled = _required_unique_texts(
        binding.get("sampled_parameters"), "sampled_parameters"
    )
    chains = _normalize_chain_artifacts(
        chain_artifacts,
        run_id=canonical_run_id,
        sampled_parameters=sampled,
    )
    if binding.get("chain_sha256") != [item["sha256"] for item in chains]:
        raise ValueError("canonical analysis receipt chain binding mismatch")
    offline_artifact = _require_canonical_nested_artifact(
        offline_grade_receipt_artifact, "offline_grade_receipt_artifact"
    )
    offline = _load_json_artifact(offline_artifact, "offline_grade_receipt_artifact")
    if offline.get("schema_version") != 1 or offline.get("artifact_type") != (
        "research_alpha_primary_offline_grade"
    ):
        raise ValueError("primary offline grade receipt schema is invalid")
    _validate_named_self_hash(
        offline,
        field="primary offline grade receipt",
        candidates=("receipt_sha256",),
    )
    if (
        offline.get("status") != "passed"
        or offline.get("profile_id") != profile_id
        or offline.get("run_id") != canonical_run_id
        or offline.get("analysis_receipt_sha256") != receipt_artifact["sha256"]
        or offline.get("sampled_parameters") != sampled
        or offline.get("chain_sha256") != [item["sha256"] for item in chains]
    ):
        raise ValueError("primary offline grade receipt binding mismatch")
    numbers = _normalize_results(offline.get("numbers"))
    normalized_diagnostics = _normalize_diagnostics(
        offline.get("diagnostics") or {},
        sampled_parameters=sampled,
        result_names={item["name"] for item in numbers},
        expected_chain_count=len(chains),
        profile_id=profile_id,
    )
    run_attestation = _normalize_artifact(
        run_attestation_artifact, "run_attestation_artifact"
    )
    run_payload = _load_json_artifact(run_attestation, "run_attestation_artifact")
    if run_payload.get("profile_id") != profile_id:
        raise ValueError("analysis and run authority profiles do not match")
    normalized_analysis_code = _require_canonical_nested_artifact(
        analysis_code_artifact, "analysis_code_artifact"
    )
    if profile_id == EXACT_PROFILE_ID:
        if (
            receipt.get("claim_scope") != EXACT_CLAIM_SCOPE
            or receipt.get("target_commitment")
            != PREREGISTERED_TARGET_COMMITMENT
            or receipt.get("evidence_ready_for_offline_grading") is not True
            or receipt.get("publication_ready") is not False
            or receipt.get("external_review_complete") is not False
        ):
            raise ValueError("canonical exact analysis receipt contract is incomplete")
        if offline.get("target_hash") != PREREGISTERED_TARGET_COMMITMENT:
            raise ValueError("primary offline grade target commitment mismatch")
        if Path(normalized_analysis_code["path"]).name != (
            "canonical_full_likelihood_evidence.py"
        ) or normalized_analysis_code["sha256"] != TRUSTED_CODE_SHA256[
            "canonical_full_likelihood_evidence.py"
        ]:
            raise ValueError("primary exact analysis code hash mismatch")
    payload = {
        "source": "server_attested",
        "run_id": canonical_run_id,
        "profile_id": profile_id,
        "status": "completed",
        "run_attestation_sha256": run_attestation["sha256"],
        "analysis_receipt_artifact": receipt_artifact,
        "offline_grade_receipt_artifact": offline_artifact,
        "analysis_code_artifact": normalized_analysis_code,
        "chain_artifacts": chains,
        "sampled_parameters": sampled,
        "numbers": numbers,
        "diagnostics": normalized_diagnostics,
    }
    return _build_research_alpha_scientific_attestation(
        attestation_type=_ANALYSIS_ATTESTATION_TYPE,
        payload=payload,
    )


def _validate_exact_independent_postprocessor_policy(
    report: Mapping[str, Any],
) -> None:
    burn_fraction = _finite_number(
        report.get("burn_fraction"), "independent postprocessor burn_fraction"
    )
    policy = report.get("execution_policy")
    binding = report.get("research_alpha_binding")
    current_import_policy = (
        binding.get("current_import_policy") if isinstance(binding, Mapping) else None
    )
    if (
        burn_fraction != 0.30
        or report.get("burn_convention")
        != "getdist_remove_fraction_of_raw_rows_per_chain"
        or not isinstance(policy, Mapping)
        or policy.get("mode") != "research_alpha_bound"
        or _float_or_none(policy.get("formal_burn_fraction")) != 0.30
        or policy.get("formal_burn_fraction_enforced") is not True
        or policy.get("preflight_import_policy_verified") is not True
        or not isinstance(current_import_policy, Mapping)
        or policy.get("preflight_import_policy_fingerprint")
        != current_import_policy.get("preflight_import_policy_fingerprint")
        or policy.get("startup_hook_fingerprint")
        != current_import_policy.get("startup_hook_fingerprint")
    ):
        raise ValueError(
            "exact independent postprocessor did not enforce burn_fraction=0.30 "
            "and the bound import policy"
        )


def _exact_run_environment_location(
    run_payload: Mapping[str, Any], *, field: str
) -> dict[str, Any]:
    preflight_artifact = _require_canonical_nested_artifact(
        run_payload.get("preflight_artifact"), f"{field}.preflight_artifact"
    )
    preflight = _load_json_artifact(preflight_artifact, f"{field}.preflight_artifact")
    _validate_named_self_hash(
        preflight,
        field=f"{field} preflight",
        candidates=("preflight_sha256",),
    )
    environment = preflight.get("environment")
    runtime = environment.get("runtime") if isinstance(environment, Mapping) else None
    policy = runtime.get("import_policy") if isinstance(runtime, Mapping) else None
    recorded_policy = _validate_recorded_exact_import_policy(policy)
    return _exact_environment_location(recorded_policy, field=field)


def build_research_alpha_independent_analysis_authority_attestation(
    *,
    run_id: str,
    primary_execution_fingerprint: str,
    run_attestation_artifact: Mapping[str, Any],
    postprocessor_report_artifact: Mapping[str, Any],
    offline_grade_receipt_artifact: Mapping[str, Any],
    analysis_code_artifact: Mapping[str, Any],
    chain_artifacts: Sequence[Mapping[str, Any]],
    primary_run_attestation_artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive the independent authority wrapper from its postprocessor receipt."""

    canonical_run_id = _required_text(run_id, "independent run_id")
    _require_sha256(
        primary_execution_fingerprint, "primary_execution_fingerprint"
    )
    report = _require_canonical_nested_artifact(
        postprocessor_report_artifact,
        "independent postprocessor_report_artifact",
    )
    report_payload = _load_json_artifact(
        report, "independent postprocessor_report_artifact"
    )
    profile_id = _normalize_research_profile(report_payload.get("profile_id"))
    if (
        report_payload.get("schema_version") != 1
        or report_payload.get("artifact_type") != "independent_w0wa_postprocess"
        or report_payload.get("run_id") != canonical_run_id
        or report_payload.get("status") != "PASS"
        or report_payload.get("failures") != []
    ):
        raise ValueError("independent postprocessor receipt is not passed")
    if profile_id == EXACT_PROFILE_ID and (
        report_payload.get("claim_scope") != EXACT_CLAIM_SCOPE
        or report_payload.get("target_commitment")
        != PREREGISTERED_TARGET_COMMITMENT
        or _normalize_protocol_status(report_payload.get("protocol_status"))
        != _PROTOCOL_STATUS
    ):
        raise ValueError("independent postprocessor exact contract is incomplete")
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_independent_postprocessor_policy(report_payload)
    _validate_named_self_hash(
        report_payload,
        field="independent postprocessor receipt",
        candidates=("report_sha256",),
    )
    binding = report_payload.get("research_alpha_binding")
    if not isinstance(binding, Mapping):
        raise ValueError("independent postprocessor research_alpha_binding is missing")
    if binding.get("primary_execution_fingerprint") != primary_execution_fingerprint:
        raise ValueError("independent postprocessor primary fingerprint mismatch")
    environment_fingerprint = binding.get("environment_fingerprint")
    _require_sha256(environment_fingerprint, "environment_fingerprint")
    sampled = _required_unique_texts(
        binding.get("sampled_parameters"), "sampled_parameters"
    )
    chains = _normalize_chain_artifacts(
        chain_artifacts,
        run_id=canonical_run_id,
        sampled_parameters=sampled,
    )
    if binding.get("chain_sha256") != [item["sha256"] for item in chains]:
        raise ValueError("independent postprocessor chain binding mismatch")
    offline_artifact = _require_canonical_nested_artifact(
        offline_grade_receipt_artifact,
        "independent offline_grade_receipt_artifact",
    )
    offline = _load_json_artifact(
        offline_artifact, "independent offline_grade_receipt_artifact"
    )
    if offline.get("schema_version") != 1 or offline.get("artifact_type") != (
        "research_alpha_independent_offline_grade"
    ):
        raise ValueError("independent offline grade receipt schema is invalid")
    _validate_named_self_hash(
        offline,
        field="independent offline grade receipt",
        candidates=("receipt_sha256",),
    )
    if (
        offline.get("status") != "passed"
        or offline.get("profile_id") != profile_id
        or offline.get("run_id") != canonical_run_id
        or offline.get("primary_execution_fingerprint")
        != primary_execution_fingerprint
        or offline.get("postprocessor_report_sha256") != report["sha256"]
        or offline.get("sampled_parameters") != sampled
        or offline.get("chain_sha256") != [item["sha256"] for item in chains]
    ):
        raise ValueError("independent offline grade receipt binding mismatch")
    numbers = _normalize_results(offline.get("numbers"))
    normalized_diagnostics = _normalize_diagnostics(
        offline.get("diagnostics") or {},
        sampled_parameters=sampled,
        result_names={item["name"] for item in numbers},
        expected_chain_count=len(chains),
        profile_id=profile_id,
    )
    run_attestation = _normalize_artifact(
        run_attestation_artifact, "independent run_attestation_artifact"
    )
    run_payload = _load_json_artifact(
        run_attestation, "independent run_attestation_artifact"
    )
    if run_payload.get("profile_id") != profile_id:
        raise ValueError("independent run and analysis profiles do not match")
    environment_proof: dict[str, Any] | None = None
    primary_run_attestation: dict[str, Any] | None = None
    primary_environment_location: dict[str, Any] | None = None
    if profile_id == EXACT_PROFILE_ID:
        environment_proof = _validate_independent_environment_preflight_binding(
            binding,
            run_payload=run_payload,
            expected_environment_fingerprint=str(environment_fingerprint),
        )
        primary_run_attestation = _require_canonical_nested_artifact(
            primary_run_attestation_artifact,
            "primary run_attestation_artifact",
        )
        primary_run_payload = _load_json_artifact(
            primary_run_attestation, "primary run_attestation_artifact"
        )
        if (
            not verify_scientific_attestation(
                primary_run_payload, expected_type=_RUN_ATTESTATION_TYPE
            )
            or primary_run_payload.get("profile_id") != EXACT_PROFILE_ID
            or primary_run_payload.get("evidence_class") != "formal_candidate"
        ):
            raise ValueError("primary exact run authority is invalid")
        primary_environment_location = _exact_run_environment_location(
            primary_run_payload, field="primary exact run"
        )
        independent_location = environment_proof["environment_location"]
        _validate_independent_environment_locations(
            primary=primary_environment_location,
            independent=independent_location,
        )
    normalized_analysis_code = _require_canonical_nested_artifact(
        analysis_code_artifact, "independent analysis_code_artifact"
    )
    if profile_id == EXACT_PROFILE_ID:
        if offline.get("target_hash") != PREREGISTERED_TARGET_COMMITMENT:
            raise ValueError("independent offline grade target commitment mismatch")
        if Path(normalized_analysis_code["path"]).name != (
            "independent_w0wa_postprocess.py"
        ) or normalized_analysis_code["sha256"] != TRUSTED_CODE_SHA256[
            "independent_w0wa_postprocess.py"
        ]:
            raise ValueError("independent postprocessor code hash mismatch")
    reproduction_binding = {
        "run_id": canonical_run_id,
        "profile_id": profile_id,
        "primary_execution_fingerprint": primary_execution_fingerprint,
        "environment_fingerprint": environment_fingerprint,
        "chain_artifacts": chains,
        "sampled_parameters": sampled,
        "numbers": numbers,
        "diagnostics": normalized_diagnostics,
        "postprocessor_report_artifact": report,
        "run_attestation_artifact": run_attestation,
        "offline_grade_receipt_artifact": offline_artifact,
    }
    if environment_proof is not None:
        reproduction_binding["environment_preflight_artifact"] = (
            environment_proof["environment_preflight_artifact"]
        )
        reproduction_binding["preflight_environment_fingerprint"] = (
            environment_proof["preflight_environment_fingerprint"]
        )
        reproduction_binding["import_policy"] = environment_proof["import_policy"]
        reproduction_binding["environment_location"] = environment_proof[
            "environment_location"
        ]
        reproduction_binding["primary_environment_location"] = (
            primary_environment_location
        )
        reproduction_binding["primary_run_attestation_sha256"] = (
            primary_run_attestation["sha256"]
        )
    payload = {
        "source": "server_attested",
        "status": "completed",
        **reproduction_binding,
        "run_attestation_sha256": run_attestation["sha256"],
        "analysis_code_artifact": normalized_analysis_code,
        "independent_execution_fingerprint": scientific_content_hash(
            reproduction_binding
        ),
    }
    return _build_research_alpha_scientific_attestation(
        attestation_type=_INDEPENDENT_ANALYSIS_ATTESTATION_TYPE,
        payload=payload,
    )


def _validate_independent_environment_preflight_binding(
    report_binding: Mapping[str, Any],
    *,
    run_payload: Mapping[str, Any],
    expected_environment_fingerprint: str,
) -> dict[str, Any]:
    """Close runtime, full-preflight, and live import-policy identities."""

    _require_sha256(
        expected_environment_fingerprint,
        "independent environment fingerprint",
    )
    raw_report_record = report_binding.get("environment_preflight")
    if not isinstance(raw_report_record, Mapping):
        raise ValueError("independent postprocessor environment preflight is missing")
    report_artifact = _normalize_artifact(
        raw_report_record,
        "independent postprocessor environment preflight",
    )
    run_preflight = _require_canonical_nested_artifact(
        run_payload.get("preflight_artifact"),
        "independent run authority preflight_artifact",
    )
    if report_artifact != run_preflight:
        raise ValueError(
            "independent postprocessor environment preflight does not match run authority"
        )
    preflight = _load_json_artifact(
        run_preflight, "independent run authority preflight_artifact"
    )
    _validate_named_self_hash(
        preflight,
        field="independent environment preflight",
        candidates=("preflight_sha256",),
    )
    self_hash = preflight.get("preflight_sha256")
    environment = preflight.get("environment")
    runtime = environment.get("runtime") if isinstance(environment, Mapping) else None
    required_versions = (
        environment.get("required_versions")
        if isinstance(environment, Mapping)
        else None
    )
    distributions = (
        environment.get("distributions")
        if isinstance(environment, Mapping)
        else None
    )
    runtime_closure = (
        environment.get("runtime_closure")
        if isinstance(environment, Mapping)
        else None
    )
    interpreter = Path(
        _required_text(
            raw_report_record.get("interpreter"),
            "independent environment preflight interpreter",
        )
    ).expanduser().absolute()
    runtime_executable = Path(
        _required_text(
            runtime.get("executable") if isinstance(runtime, Mapping) else None,
            "independent preflight runtime executable",
        )
    ).expanduser().absolute()
    native_python = (
        (((runtime.get("native_runtime") or {}).get("binaries") or {}).get("python"))
        if isinstance(runtime, Mapping)
        else None
    )
    native_python_path = Path(
        _required_text(
            native_python.get("path") if isinstance(native_python, Mapping) else None,
            "independent preflight native Python path",
        )
    ).expanduser().absolute()
    if not isinstance(environment, Mapping):
        raise ValueError("independent environment preflight environment is missing")
    environment_unsigned = {
        key: item
        for key, item in environment.items()
        if key not in {"passed", "reasons", "fingerprint"}
    }
    preflight_environment_fingerprint = environment.get("fingerprint")
    _require_sha256(
        preflight_environment_fingerprint,
        "independent preflight environment fingerprint",
    )
    if not isinstance(runtime, Mapping):
        raise ValueError("independent preflight runtime is missing")
    runtime_environment_fingerprint = _runtime_environment_fingerprint(runtime)
    recorded_import_policy = _validate_recorded_exact_import_policy(
        runtime.get("import_policy")
    )
    environment_location = _exact_environment_location(
        recorded_import_policy, field="independent preflight"
    )
    verified_import_policy = _validate_verified_independent_import_policy(
        raw_report_record.get("import_policy"),
        recorded_policy=recorded_import_policy,
    )
    if report_binding.get("current_import_policy") != verified_import_policy:
        raise ValueError("independent postprocessor import policy copies disagree")
    normalized_versions = _validate_exact_distribution_inventory(
        required_versions, distributions
    )
    _validate_exact_runtime_closure_identity(
        runtime.get("runtime_closure"), required_versions=normalized_versions
    )
    if (
        not isinstance(runtime_closure, Sequence)
        or isinstance(runtime_closure, (str, bytes))
        or list(runtime_closure) != sorted(normalized_versions)
    ):
        raise ValueError("independent environment runtime closure is incomplete")

    if (
        raw_report_record.get("verified") is not True
        or raw_report_record.get("preflight_sha256") != self_hash
        or raw_report_record.get("environment_fingerprint")
        != expected_environment_fingerprint
        or raw_report_record.get("preflight_environment_fingerprint")
        != preflight_environment_fingerprint
        or report_binding.get("environment_fingerprint")
        != expected_environment_fingerprint
        or run_payload.get("environment_fingerprint")
        != expected_environment_fingerprint
        or runtime_environment_fingerprint != expected_environment_fingerprint
        or environment.get("passed") is not True
        or environment.get("reasons") != []
        or preflight_environment_fingerprint
        != scientific_content_hash(environment_unsigned)
        or interpreter != runtime_executable
        or interpreter.resolve() != native_python_path.resolve()
        or not interpreter.is_file()
        or _integer(
            raw_report_record.get("distribution_count"),
            "independent environment preflight distribution_count",
        )
        != len(distributions)
    ):
        raise ValueError(
            "independent environment preflight fingerprint/self-hash binding mismatch"
        )
    return {
        "environment_preflight_artifact": run_preflight,
        "runtime_environment_fingerprint": runtime_environment_fingerprint,
        "preflight_environment_fingerprint": preflight_environment_fingerprint,
        "import_policy": verified_import_policy,
        "environment_location": environment_location,
    }


def build_research_alpha_adequacy_run_authority_attestation(
    *,
    canonical_run_receipt_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive authority solely from a completed adequacy runner receipt."""

    receipt_artifact = _require_canonical_nested_artifact(
        canonical_run_receipt_artifact, "adequacy canonical_run_receipt_artifact"
    )
    receipt = _load_json_artifact(
        receipt_artifact, "adequacy canonical_run_receipt_artifact"
    )
    if receipt.get("schema_version") != 1 or receipt.get("artifact_type") != (
        "research_alpha_adequacy_run_receipt"
    ):
        raise ValueError("adequacy run receipt schema is invalid")
    profile_id = _normalize_research_profile(receipt.get("profile_id"))
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_evidence_signing_key_binding(receipt)
        if not verify_scientific_attestation(
            receipt, expected_type=_EXACT_ADEQUACY_RUN_RECEIPT_TYPE
        ):
            raise ValueError("exact adequacy run receipt producer HMAC is invalid")
    else:
        _validate_named_self_hash(
            receipt,
            field="adequacy run receipt",
            candidates=("receipt_sha256",),
        )
    run_id = _required_text(receipt.get("run_id"), "run_id")
    execution_fingerprint = receipt.get("execution_fingerprint")
    _require_sha256(execution_fingerprint, "execution_fingerprint")
    check = _required_text(receipt.get("check"), "check")
    name = _required_text(receipt.get("name"), "name")
    runner = _required_text(receipt.get("runner"), "adequacy runner")
    if (
        receipt.get("status") != "completed"
        or receipt.get("success") is not True
        or receipt.get("returncode") != 0
    ):
        raise ValueError("adequacy run receipt is not successful")
    config = _require_canonical_nested_artifact(
        receipt.get("config_artifact"), "adequacy config_artifact"
    )
    outputs = _require_canonical_artifact_list(
        receipt.get("output_artifacts"), "adequacy output_artifacts"
    )
    if not outputs:
        raise ValueError("adequacy run receipt has no outputs")
    termination = receipt.get("termination")
    if not isinstance(termination, Mapping) or termination.get("passed") is not True:
        raise ValueError("adequacy run receipt termination did not pass")
    checkpoint: dict[str, Any] | None = None
    phase = "sampling"
    if profile_id == EXACT_PROFILE_ID:
        phase = _required_text(receipt.get("phase"), "adequacy phase")
        expected_phase = (
            "simulation"
            if check in {"prior_predictive_check", "posterior_predictive_check"}
            else "sampling"
        )
        expected_runner = "pinned_simulator" if expected_phase == "simulation" else "cobaya"
        if phase != expected_phase or runner != expected_runner:
            raise ValueError("exact adequacy runner type does not match check phase")
    elif runner != "cobaya":
        raise ValueError("CI adequacy run receipt is not a Cobaya run")
    if phase == "sampling":
        checkpoint = _require_canonical_nested_artifact(
            termination.get("checkpoint_artifact"), "adequacy checkpoint_artifact"
        )
        if termination.get("status") not in {None, "converged"}:
            raise ValueError("adequacy sampling termination is not converged")
    elif termination.get("status") not in {None, "completed"}:
        raise ValueError("adequacy simulation termination is not completed")
    generation_artifact: dict[str, Any] | None = None
    plan_config_key: str | None = None
    runner_code_artifact: dict[str, Any] | None = None
    if profile_id == EXACT_PROFILE_ID:
        runner_code_artifact = _trusted_exact_adequacy_code(
            receipt.get("runner_code_artifact"),
            registry=TRUSTED_ADEQUACY_RUNNER_CODE_SHA256,
            field="exact adequacy runner code",
        )
        generation_artifact = _require_canonical_nested_artifact(
            receipt.get("generation_receipt_artifact"),
            "adequacy generation_receipt_artifact",
        )
        generation_payload = _load_json_artifact(
            generation_artifact, "adequacy generation_receipt_artifact"
        )
        _validate_named_self_hash(
            generation_payload,
            field="adequacy generation receipt",
            candidates=("generation_sha256",),
        )
        if (
            generation_payload.get("profile_id") != EXACT_PROFILE_ID
            or generation_payload.get("target_commitment")
            != PREREGISTERED_TARGET_COMMITMENT
            or generation_payload.get("passed") is not True
        ):
            raise ValueError("adequacy generation receipt is not exact and passed")
        _, plan_payload, ppc_plan, injection_plan = _exact_plan_artifact_payload(
            generation_payload
        )
        plan_config_key = _required_text(
            receipt.get("plan_config_key"), "adequacy plan_config_key"
        )
        if plan_config_key != _exact_plan_config_key(check, name):
            raise ValueError("adequacy plan_config_key does not match check/name")
        configs = plan_payload.get("configs")
        expected_config = configs.get(plan_config_key) if isinstance(configs, Mapping) else None
        if not isinstance(expected_config, Mapping) or _normalize_artifact(
            expected_config, "adequacy registered plan config"
        ) != config:
            raise ValueError("adequacy runner config is not generation-plan bound")
        simulation_code = _trusted_exact_adequacy_code(
            receipt.get("simulation_code_artifact"),
            registry=TRUSTED_ADEQUACY_RUNNER_CODE_SHA256,
            field="adequacy simulation code",
        ) if check in {
            "prior_predictive_check",
            "posterior_predictive_check",
            "simulation_recovery",
        } else None
        if simulation_code is not None and simulation_code != runner_code_artifact:
            raise ValueError("exact adequacy simulator is not the trusted runner")
        if check in {"prior_predictive_check", "posterior_predictive_check"}:
            spec = ppc_plan["checks"][check]
            discrepancy_order = list(spec["required_discrepancies"])
            expected_seed = int(spec["seed_entropy"][discrepancy_order.index(name)])
            simulation_field = "simulation_artifact"
        elif check == "simulation_recovery":
            fiducial = next(
                item for item in injection_plan["fiducials"] if item["name"] == name
            )
            expected_seed = int(fiducial["simulation_seed"])
            simulation_field = "simulated_data_artifact"
        else:
            expected_seed = None
            simulation_field = None
        if expected_seed is not None:
            observed_seed = _integer(
                receipt.get("simulation_seed"), "adequacy simulation_seed"
            )
            if observed_seed != expected_seed:
                raise ValueError("adequacy simulation seed is not plan-bound")
            simulation_artifact = _require_canonical_nested_artifact(
                receipt.get(simulation_field), f"adequacy {simulation_field}"
            )
            if check in {"prior_predictive_check", "posterior_predictive_check"} and (
                simulation_artifact not in outputs
            ):
                raise ValueError("predictive simulation output is not runner-bound")
        else:
            observed_seed = None
            simulation_artifact = None
    payload = {
        "source": "server_attested",
        "profile_id": profile_id,
        "kind": "research_alpha_adequacy_run",
        "run_id": run_id,
        "execution_fingerprint": execution_fingerprint,
        "check": check,
        "name": name,
        "variant_run_id": _required_text(
            receipt.get("variant_run_id"), "variant_run_id"
        ),
        "status": receipt["status"],
        "success": receipt["success"],
        "returncode": receipt["returncode"],
        "runner": runner,
        "phase": phase,
        "config_artifact": config,
        "output_artifacts": outputs,
        "canonical_run_receipt_artifact": receipt_artifact,
    }
    if checkpoint is not None:
        payload["checkpoint_artifact"] = checkpoint
    if generation_artifact is not None:
        payload["generation_receipt_artifact"] = generation_artifact
        payload["plan_config_key"] = plan_config_key
        payload["runner_code_artifact"] = runner_code_artifact
        if simulation_artifact is not None and simulation_field is not None:
            payload[simulation_field] = simulation_artifact
            payload["simulation_seed"] = observed_seed
            payload["simulation_code_artifact"] = simulation_code
    return _build_research_alpha_scientific_attestation(
        attestation_type=_ADEQUACY_RUN_ATTESTATION_TYPE,
        payload=payload,
    )


def build_research_alpha_adequacy_analysis_authority_attestation(
    *,
    runner_attestation_artifact: Mapping[str, Any],
    canonical_analysis_receipt_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive analyzer authority solely from its canonical receipt."""

    runner = _require_canonical_nested_artifact(
        runner_attestation_artifact, "adequacy runner_attestation_artifact"
    )
    receipt_artifact = _require_canonical_nested_artifact(
        canonical_analysis_receipt_artifact,
        "adequacy canonical_analysis_receipt_artifact",
    )
    receipt = _load_json_artifact(
        receipt_artifact, "adequacy canonical_analysis_receipt_artifact"
    )
    if receipt.get("schema_version") != 1 or receipt.get("artifact_type") != (
        "research_alpha_adequacy_analysis_receipt"
    ):
        raise ValueError("adequacy analysis receipt schema is invalid")
    profile_id = _normalize_research_profile(receipt.get("profile_id"))
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_evidence_signing_key_binding(receipt)
        if not verify_scientific_attestation(
            receipt, expected_type=_EXACT_ADEQUACY_ANALYSIS_RECEIPT_TYPE
        ):
            raise ValueError("exact adequacy analysis receipt producer HMAC is invalid")
    else:
        _validate_named_self_hash(
            receipt,
            field="adequacy analysis receipt",
            candidates=("report_sha256",),
        )
    runner_payload = _load_json_artifact(
        runner, "adequacy runner_attestation_artifact"
    )
    if (
        not verify_scientific_attestation(
            runner_payload, expected_type=_ADEQUACY_RUN_ATTESTATION_TYPE
        )
        or runner_payload.get("profile_id") != profile_id
    ):
        raise ValueError("adequacy runner and analysis profiles do not match")
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_evidence_signing_key_binding(runner_payload)
    run_id = _required_text(receipt.get("run_id"), "run_id")
    execution_fingerprint = receipt.get("execution_fingerprint")
    _require_sha256(execution_fingerprint, "execution_fingerprint")
    check = _required_text(receipt.get("check"), "check")
    name = _required_text(receipt.get("name"), "name")
    if receipt.get("status") != "passed" or receipt.get(
        "runner_attestation_sha256"
    ) != runner["sha256"]:
        raise ValueError("adequacy analysis receipt did not pass its certified run")
    analysis_code = _require_canonical_nested_artifact(
        receipt.get("analysis_code_artifact"), "adequacy analysis_code_artifact"
    )
    if profile_id == EXACT_PROFILE_ID:
        analysis_code = _trusted_exact_adequacy_code(
            analysis_code,
            registry=TRUSTED_ADEQUACY_ANALYZER_CODE_SHA256,
            field="exact adequacy analysis code",
        )
    metrics = receipt.get("metrics")
    if not isinstance(metrics, Mapping):
        raise ValueError("adequacy analysis receipt metrics are missing")
    payload = {
        "source": "server_attested",
        "profile_id": profile_id,
        "kind": "research_alpha_adequacy_subartifact",
        "run_id": run_id,
        "execution_fingerprint": execution_fingerprint,
        "check": check,
        "name": name,
        "status": receipt["status"],
        "runner_attestation_artifact": runner,
        "analysis_code_artifact": analysis_code,
        "metrics": dict(metrics),
        "canonical_analysis_receipt_artifact": receipt_artifact,
    }
    return _build_research_alpha_scientific_attestation(
        attestation_type=_ADEQUACY_ANALYSIS_ATTESTATION_TYPE,
        payload=payload,
    )


def build_research_alpha_adequacy_authority_attestation(
    *,
    canonical_aggregate_receipt_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive aggregate authority solely from its canonical analyzer receipt."""

    receipt_artifact = _require_canonical_nested_artifact(
        canonical_aggregate_receipt_artifact,
        "adequacy canonical_aggregate_receipt_artifact",
    )
    receipt = _load_json_artifact(
        receipt_artifact, "adequacy canonical_aggregate_receipt_artifact"
    )
    if receipt.get("schema_version") != 1 or receipt.get("artifact_type") != (
        "research_alpha_adequacy_aggregate_receipt"
    ):
        raise ValueError("adequacy aggregate receipt schema is invalid")
    profile_id = _normalize_research_profile(receipt.get("profile_id"))
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_evidence_signing_key_binding(receipt)
        if not verify_scientific_attestation(
            receipt, expected_type=_EXACT_ADEQUACY_AGGREGATE_RECEIPT_TYPE
        ):
            raise ValueError("exact adequacy aggregate receipt producer HMAC is invalid")
    else:
        _validate_named_self_hash(
            receipt,
            field="adequacy aggregate receipt",
            candidates=("aggregate_sha256",),
        )
    run_id = _required_text(receipt.get("run_id"), "run_id")
    execution_fingerprint = receipt.get("execution_fingerprint")
    _require_sha256(execution_fingerprint, "execution_fingerprint")
    check = _required_text(receipt.get("check"), "check")
    metrics = receipt.get("metrics")
    if receipt.get("status") != "passed" or not isinstance(metrics, Mapping):
        raise ValueError("adequacy aggregate receipt did not pass")
    aggregate_analysis_code: dict[str, Any] | None = None
    if profile_id == EXACT_PROFILE_ID:
        aggregate_analysis_code = _trusted_exact_adequacy_code(
            receipt.get("analysis_code_artifact"),
            registry=TRUSTED_ADEQUACY_ANALYZER_CODE_SHA256,
            field="adequacy aggregate analysis code",
        )
    payload = {
        "source": "server_attested",
        "profile_id": profile_id,
        "producer": "canonical_full_likelihood_analyzer",
        "kind": "research_alpha_adequacy",
        "check": check,
        "run_id": run_id,
        "execution_fingerprint": execution_fingerprint,
        "status": receipt["status"],
        "metrics": dict(metrics),
        "canonical_aggregate_receipt_artifact": receipt_artifact,
    }
    if aggregate_analysis_code is not None:
        payload["analysis_code_artifact"] = aggregate_analysis_code
    return _build_research_alpha_scientific_attestation(
        attestation_type=_ADEQUACY_ATTESTATION_TYPE,
        payload=payload,
    )


def build_research_alpha_execution_binding(
    *,
    run_id: str,
    target_hash: str,
    chain_artifacts: Sequence[Mapping[str, Any]],
    config_artifact: Mapping[str, Any],
    data_artifacts: Mapping[str, Any],
    likelihood_artifacts: Mapping[str, Any],
    sampled_parameters_artifact: Mapping[str, Any],
    run_attestation_artifact: Mapping[str, Any],
    analysis_attestation_artifact: Mapping[str, Any],
    protocol_adjudication_artifact: Mapping[str, Any] | None,
    results: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
    diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    """Inspect the base artifacts and return the fingerprint adequacy uses.

    Adequacy reports are produced after the chains are analyzed, so callers use
    this deterministic helper first and record its ``execution_fingerprint`` in
    every adequacy/support JSON artifact.  The final signed manifest recomputes
    the same value and rejects any mismatch.
    """

    prepared = _prepare_execution(
        run_id=run_id,
        target_hash=target_hash,
        chain_artifacts=chain_artifacts,
        config_artifact=config_artifact,
        data_artifacts=data_artifacts,
        likelihood_artifacts=likelihood_artifacts,
        sampled_parameters_artifact=sampled_parameters_artifact,
        run_attestation_artifact=run_attestation_artifact,
        analysis_attestation_artifact=analysis_attestation_artifact,
        protocol_adjudication_artifact=protocol_adjudication_artifact,
        results=results,
        diagnostics=diagnostics,
    )
    return {
        "run_id": prepared["run_id"],
        "profile_id": prepared["profile_id"],
        "target_hash": prepared["target_hash"],
        "execution_fingerprint": prepared["execution_fingerprint"],
        "artifacts": prepared["artifacts"],
        "fingerprints": prepared["fingerprints"],
        "sampled_parameters": prepared["sampled_parameters"],
        "numbers": prepared["numbers"],
        "diagnostics": prepared["diagnostics"],
    }


def _research_alpha_release_policy(profile_id: str) -> dict[str, Any]:
    """Return the signed readiness/publication policy for one manifest profile."""

    if profile_id != EXACT_PROFILE_ID:
        return {
            "readiness_status": "CI_FIXTURE_WITHHELD",
            "eligible": False,
            "numerical_eligible": False,
            "reasons": ["ci_fixture_non_publication_profile"],
        }
    if exact_environment_validated_for_formal_execution():
        return {
            "readiness_status": EXACT_MAX_READINESS_STATUS,
            "eligible": True,
            "numerical_eligible": True,
            "reasons": [],
        }
    return {
        "readiness_status": str(
            EXACT_ENVIRONMENT_REVISION.get("status")
            or "WITHHELD_ENVIRONMENT_REVISION_INVALID"
        ),
        "eligible": False,
        "numerical_eligible": False,
        "reasons": [EXACT_ENVIRONMENT_PENDING_REASON],
    }


def _validate_research_alpha_release_policy(
    manifest: Mapping[str, Any],
    *,
    profile_id: str,
    reasons: list[str],
) -> None:
    """Validate readiness independently of expensive artifact closure checks."""

    policy = _research_alpha_release_policy(profile_id)
    if profile_id == EXACT_PROFILE_ID and manifest.get(
        "environment_revision"
    ) != EXACT_ENVIRONMENT_REVISION:
        reasons.append("environment_revision_mismatch")
    if manifest.get("readiness_status") != policy["readiness_status"]:
        reasons.append("readiness_status_mismatch")

    gate = manifest.get("publication_gate")
    if not isinstance(gate, Mapping):
        reasons.append("publication_gate_missing")
        return
    policy_matches = all(
        gate.get(key) == policy[key]
        for key in ("eligible", "numerical_eligible", "reasons")
    )
    if policy_matches:
        return
    if profile_id == EXACT_PROFILE_ID:
        reasons.append(
            "publication_gate_not_eligible"
            if policy["eligible"] is True
            else "exact_environment_revision_publication_gate_not_withheld"
        )
    else:
        reasons.append("ci_fixture_publication_gate_not_withheld")


def build_research_alpha_manifest(
    *,
    run_id: str,
    target_hash: str,
    chain_artifacts: Sequence[Mapping[str, Any]],
    config_artifact: Mapping[str, Any],
    data_artifacts: Mapping[str, Any],
    likelihood_artifacts: Mapping[str, Any],
    sampled_parameters_artifact: Mapping[str, Any],
    run_attestation_artifact: Mapping[str, Any],
    analysis_attestation_artifact: Mapping[str, Any],
    protocol_adjudication_artifact: Mapping[str, Any] | None,
    results: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
    diagnostics: Mapping[str, Any],
    adequacy_evidence_by_check: Mapping[str, Mapping[str, Any]],
    claim_support_paths: Sequence[Mapping[str, Any]],
    datasets: Sequence[str],
    methods: Sequence[str],
    models: Sequence[str],
    result_direction_terms: Sequence[str],
    external_review_attestation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a signed Research Alpha manifest from verified local artifacts."""

    prepared = _prepare_execution(
        run_id=run_id,
        target_hash=target_hash,
        chain_artifacts=chain_artifacts,
        config_artifact=config_artifact,
        data_artifacts=data_artifacts,
        likelihood_artifacts=likelihood_artifacts,
        sampled_parameters_artifact=sampled_parameters_artifact,
        run_attestation_artifact=run_attestation_artifact,
        analysis_attestation_artifact=analysis_attestation_artifact,
        protocol_adjudication_artifact=protocol_adjudication_artifact,
        results=results,
        diagnostics=diagnostics,
    )
    normalized_datasets = _required_unique_texts(datasets, "datasets")
    normalized_methods = _required_unique_texts(methods, "methods")
    normalized_models = _required_unique_texts(models, "models")
    normalized_directions = _required_unique_texts(
        result_direction_terms, "result_direction_terms"
    )

    adequacy_subject = _adequacy_subject(
        run_id=prepared["run_id"],
        execution_fingerprint=prepared["execution_fingerprint"],
        target_hash=prepared["target_hash"],
        chain_artifacts=prepared["artifacts"]["chains"],
        datasets=normalized_datasets,
        models=normalized_models,
    )
    adequacy_evidence = _normalize_adequacy_evidence(
        adequacy_evidence_by_check,
        run_id=prepared["run_id"],
        execution_fingerprint=prepared["execution_fingerprint"],
        primary_chain_artifacts=prepared["artifacts"]["chains"],
        primary_run_attestation_artifact=prepared["artifacts"]["run_attestation"],
        primary_environment_fingerprint=prepared["diagnostics"]["metrics"][
            "environment_fingerprint"
        ],
        primary_numbers=prepared["numbers"],
        sampled_parameters=prepared["sampled_parameters"],
        profile_id=prepared["profile_id"],
    )
    adequacy_manifest = build_model_adequacy_attestation(
        subject=adequacy_subject,
        evidence_by_check=adequacy_evidence,
    )
    adequacy_assessment = _assess_model_adequacy(
        adequacy_manifest,
        expected_subject=adequacy_subject,
    )
    if adequacy_assessment["eligible"] is not True:
        raise ValueError(
            "model adequacy evidence is not eligible: "
            + ", ".join(adequacy_assessment["reasons"])
        )

    support_paths = _normalize_support_paths(
        claim_support_paths,
        run_id=prepared["run_id"],
        execution_fingerprint=prepared["execution_fingerprint"],
        numbers=prepared["numbers"],
    )
    final_binding = {
        "execution_fingerprint": prepared["execution_fingerprint"],
        "model_adequacy_manifest_hash": adequacy_manifest["manifest_hash"],
        "claim_support_paths": support_paths,
    }
    run_fingerprint = scientific_content_hash(final_binding)
    run_identity = {
        "run_id": prepared["run_id"],
        "chain_ids": [item["chain_id"] for item in prepared["artifacts"]["chains"]],
        "seeds": [item["seed"] for item in prepared["artifacts"]["chains"]],
        "execution_fingerprint": prepared["execution_fingerprint"],
        "run_fingerprint": run_fingerprint,
    }

    numbers: dict[str, dict[str, Any]] = {}
    result_evidence_by_name: dict[str, str] = {}
    support_by_parameter = {item["parameter"]: item for item in support_paths}
    for number in prepared["numbers"]:
        support = support_by_parameter[number["name"]]
        evidence = _result_evidence(
            run_id=prepared["run_id"],
            run_fingerprint=run_fingerprint,
            number=number,
            support=support,
        )
        evidence_id = scientific_content_hash(evidence)
        result_evidence_by_name[number["name"]] = evidence_id
        numbers[number["name"]] = {
            **{key: value for key, value in number.items() if key != "name"},
            "run_fingerprint": run_fingerprint,
            "evidence_id": evidence_id,
        }
        support["evidence_id"] = evidence_id

    diagnostics_evidence = {
        "kind": "research_alpha_chain_diagnostics",
        "run_id": prepared["run_id"],
        "run_fingerprint": run_fingerprint,
        "chain_artifacts": prepared["artifacts"]["chains"],
        "sampled_parameters_artifact": prepared["artifacts"]["sampled_parameters"],
        "status": "passed",
        "metrics": prepared["diagnostics"]["metrics"],
    }
    diagnostics_evidence_id = scientific_content_hash(diagnostics_evidence)
    signed_diagnostics = {
        **diagnostics_evidence,
        "evidence_id": diagnostics_evidence_id,
        "evidence_hash": diagnostics_evidence_id,
    }

    evidence_ids = [
        diagnostics_evidence_id,
        *result_evidence_by_name.values(),
        *(
            adequacy_manifest["checks"][name]["evidence_id"]
            for name in PUBLICATION_REQUIRED_ADEQUACY_CHECKS
        ),
    ]
    is_exact = prepared["profile_id"] == EXACT_PROFILE_ID
    release_policy = _research_alpha_release_policy(prepared["profile_id"])
    external_review = _normalize_external_review(
        external_review_attestation,
        run_id=prepared["run_id"],
        run_fingerprint=run_fingerprint,
        target_hash=prepared["target_hash"],
        profile_id=prepared["profile_id"],
    )
    if not is_exact and external_review["status"] == "approved":
        raise ValueError("CI fixture manifests cannot receive external A review")
    if external_review["status"] == "approved":
        evidence_ids.append(external_review["attestation"]["report_artifact"]["sha256"])

    payload = {
        "source": "server_attested",
        "profile_id": prepared["profile_id"],
        "research_alpha_manifest_version": RESEARCH_ALPHA_MANIFEST_VERSION,
        "readiness_status": release_policy["readiness_status"],
        "run_identity": run_identity,
        "target": {"hash": prepared["target_hash"]},
        "artifacts": prepared["artifacts"],
        "fingerprints": prepared["fingerprints"],
        "sampled_parameters": prepared["sampled_parameters"],
        "datasets": normalized_datasets,
        "methods": normalized_methods,
        "models": normalized_models,
        "result_direction_terms": normalized_directions,
        "protocol_status": prepared["protocol_status"],
        "numbers": numbers,
        "diagnostics": signed_diagnostics,
        "model_adequacy": adequacy_manifest,
        "publication_gate": {
            "eligible": release_policy["eligible"],
            "numerical_eligible": release_policy["numerical_eligible"],
            "reasons": release_policy["reasons"],
            "model_adequacy": adequacy_assessment,
        },
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "claim_support_paths": support_paths,
        "external_review": external_review,
    }
    if is_exact:
        payload["environment_revision"] = copy.deepcopy(EXACT_ENVIRONMENT_REVISION)
    return _build_research_alpha_scientific_attestation(
        attestation_type="research_alpha",
        payload=payload,
    )


def validate_research_alpha_manifest(
    manifest: Mapping[str, Any] | Any,
    *,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    """Re-open every artifact and validate the complete signed contract."""

    if not isinstance(manifest, dict):
        return {"valid": False, "reasons": ["manifest_not_mapping"]}
    reasons: list[str] = []
    if not verify_scientific_attestation(manifest, expected_type="research_alpha"):
        reasons.append("research_alpha_signature_unverified")
    if manifest.get("source") != "server_attested":
        reasons.append("research_alpha_source_untrusted")
    if manifest.get("research_alpha_manifest_version") != RESEARCH_ALPHA_MANIFEST_VERSION:
        reasons.append("research_alpha_schema_mismatch")
    try:
        profile_id = _normalize_research_profile(manifest.get("profile_id"))
    except ValueError as exc:
        profile_id = None
        reasons.append(_reason_from_error(exc))
    if profile_id == EXACT_PROFILE_ID:
        try:
            _validate_exact_evidence_signing_key_binding(manifest)
        except ValueError as exc:
            reasons.append(_reason_from_error(exc))
    if profile_id is not None:
        _validate_research_alpha_release_policy(
            manifest,
            profile_id=profile_id,
            reasons=reasons,
        )
    try:
        manifest_protocol_status = _normalize_protocol_status(
            manifest.get("protocol_status")
        )
    except ValueError as exc:
        manifest_protocol_status = None
        reasons.append(_reason_from_error(exc))

    run_identity = manifest.get("run_identity")
    artifacts = manifest.get("artifacts")
    target = manifest.get("target")
    if not isinstance(run_identity, Mapping):
        reasons.append("run_identity_missing")
        return {"valid": False, "reasons": list(dict.fromkeys(reasons))}
    run_id = str(run_identity.get("run_id") or "")
    if expected_run_id is not None and run_id != str(expected_run_id):
        reasons.append("run_id_mismatch")
    if not run_id:
        reasons.append("run_id_missing")
    target_hash = target.get("hash") if isinstance(target, Mapping) else None
    if not _is_sha256(target_hash):
        reasons.append("target_hash_invalid")
    if profile_id == EXACT_PROFILE_ID and target_hash != (
        PREREGISTERED_TARGET_COMMITMENT
    ):
        reasons.append("exact_target_commitment_mismatch")

    normalized_artifacts: dict[str, Any] | None = None
    sampled_parameters: list[str] = []
    try:
        normalized_artifacts = _revalidate_artifact_inventory(artifacts, run_id=run_id)
        sampled_parameters = _sampled_parameters_from_artifact(
            normalized_artifacts["sampled_parameters"], run_id=run_id
        )
    except ValueError as exc:
        reasons.append(_reason_from_error(exc))

    normalized_numbers: list[dict[str, Any]] = []
    try:
        normalized_numbers = _normalize_results(manifest.get("numbers"))
    except ValueError as exc:
        reasons.append(_reason_from_error(exc))
    diagnostics = manifest.get("diagnostics")
    normalized_diagnostics: dict[str, Any] | None = None
    try:
        normalized_diagnostics = _normalize_diagnostics(
            diagnostics if isinstance(diagnostics, Mapping) else {},
            sampled_parameters=sampled_parameters,
            result_names={item["name"] for item in normalized_numbers},
            expected_chain_count=(
                len(normalized_artifacts["chains"]) if normalized_artifacts else 0
            ),
            profile_id=profile_id,
        )
    except ValueError as exc:
        reasons.append(_reason_from_error(exc))

    authority_valid = False
    if (
        normalized_artifacts is not None
        and normalized_diagnostics is not None
        and normalized_numbers
    ):
        try:
            run_profile_id = _validate_run_authority_attestation(
                normalized_artifacts["run_attestation"],
                run_id=run_id,
                chains=normalized_artifacts["chains"],
                config=normalized_artifacts["config"],
                data=normalized_artifacts["data"],
                likelihoods=normalized_artifacts["likelihoods"],
                sampled_parameters_artifact=normalized_artifacts[
                    "sampled_parameters"
                ],
                expected_environment_fingerprint=normalized_diagnostics["metrics"][
                    "environment_fingerprint"
                ],
            )
            if run_profile_id != profile_id:
                raise ValueError("manifest profile does not match run authority")
            _validate_analysis_authority_attestation(
                normalized_artifacts["analysis_attestation"],
                run_id=run_id,
                run_attestation=normalized_artifacts["run_attestation"],
                chains=normalized_artifacts["chains"],
                sampled_parameters=sampled_parameters,
                numbers=normalized_numbers,
                diagnostics=normalized_diagnostics,
            )
            run_payload = _load_json_artifact(
                normalized_artifacts["run_attestation"],
                "artifacts.run_attestation",
            )
            run_protocol_status = _normalize_protocol_status(
                run_payload.get("protocol_status")
            )
            if manifest_protocol_status != run_protocol_status:
                raise ValueError("research alpha protocol status mismatch")
            _validate_protocol_eligibility(
                run_protocol_status,
                normalized_artifacts.get("protocol_adjudication"),
                run_id=run_id,
                target_hash=str(target_hash),
            )
            authority_valid = True
        except ValueError as exc:
            reasons.append(_reason_from_error(exc))

    execution_fingerprint: str | None = None
    if (
        normalized_artifacts is not None
        and normalized_diagnostics is not None
        and normalized_numbers
        and authority_valid
        and isinstance(target_hash, str)
    ):
        fingerprints = _fingerprints_from_artifacts(normalized_artifacts)
        if manifest.get("fingerprints") != fingerprints:
            reasons.append("artifact_fingerprints_mismatch")
        execution_binding = _execution_binding_payload(
            run_id=run_id,
            target_hash=target_hash,
            artifacts=normalized_artifacts,
            sampled_parameters=sampled_parameters,
            numbers=normalized_numbers,
            diagnostic_metrics=normalized_diagnostics["metrics"],
        )
        execution_fingerprint = scientific_content_hash(execution_binding)
        if run_identity.get("execution_fingerprint") != execution_fingerprint:
            reasons.append("execution_fingerprint_mismatch")

    adequacy_manifest = manifest.get("model_adequacy")
    support_paths: list[dict[str, Any]] = []
    if execution_fingerprint is not None:
        try:
            support_paths = _normalize_support_paths(
                manifest.get("claim_support_paths"),
                run_id=run_id,
                execution_fingerprint=execution_fingerprint,
                numbers=normalized_numbers,
                allow_evidence_ids=True,
            )
        except ValueError as exc:
            reasons.append(_reason_from_error(exc))
        _validate_adequacy_manifest(
            manifest,
            run_id=run_id,
            execution_fingerprint=execution_fingerprint,
            target_hash=str(target_hash),
            chain_artifacts=(normalized_artifacts or {}).get("chains") or [],
            primary_run_attestation_artifact=(normalized_artifacts or {}).get(
                "run_attestation"
            ),
            primary_numbers=normalized_numbers,
            sampled_parameters=sampled_parameters,
            reasons=reasons,
        )

    run_fingerprint: str | None = None
    if execution_fingerprint is not None and support_paths and isinstance(
        adequacy_manifest, Mapping
    ):
        final_binding = {
            "execution_fingerprint": execution_fingerprint,
            "model_adequacy_manifest_hash": adequacy_manifest.get("manifest_hash"),
            "claim_support_paths": [
                {key: value for key, value in item.items() if key != "evidence_id"}
                for item in support_paths
            ],
        }
        run_fingerprint = scientific_content_hash(final_binding)
        if run_identity.get("run_fingerprint") != run_fingerprint:
            reasons.append("run_fingerprint_mismatch")
        _validate_bound_result_evidence(
            manifest,
            run_id=run_id,
            run_fingerprint=run_fingerprint,
            numbers=normalized_numbers,
            support_paths=support_paths,
            reasons=reasons,
        )

    if run_fingerprint is not None and isinstance(target_hash, str):
        _validate_external_review(
            manifest.get("external_review"),
            run_id=run_id,
            run_fingerprint=run_fingerprint,
            target_hash=target_hash,
            profile_id=str(profile_id or ""),
            reasons=reasons,
        )
    review = manifest.get("external_review")
    approved = isinstance(review, Mapping) and review.get("status") == "approved"
    if approved:
        attestation = review.get("attestation")
        report = (
            attestation.get("report_artifact")
            if isinstance(attestation, Mapping)
            else None
        )
        report_hash = report.get("sha256") if isinstance(report, Mapping) else None
        if report_hash not in (manifest.get("evidence_ids") or []):
            reasons.append("external_review_report_evidence_unlisted")
    if profile_id == CI_FIXTURE_PROFILE_ID and approved:
        reasons.append("ci_fixture_external_review_forbidden")
    return {"valid": not reasons, "reasons": list(dict.fromkeys(reasons))}


def research_alpha_external_review_complete(manifest: Mapping[str, Any]) -> bool:
    """Return true only for an approved independent Ed25519 review."""

    validation = validate_research_alpha_manifest(manifest)
    if manifest.get("profile_id") == EXACT_PROFILE_ID:
        return False
    review = manifest.get("external_review")
    verification = review.get("verification") if isinstance(review, Mapping) else None
    return bool(
        validation["valid"]
        and isinstance(review, Mapping)
        and review.get("status") == "approved"
        and isinstance(verification, Mapping)
        and verification.get("independent_key_verified") is True
    )


def external_review_signing_bytes(attestation: Mapping[str, Any]) -> bytes:
    """Canonical bytes an independent reviewer signs with Ed25519."""

    payload = {key: value for key, value in attestation.items() if key != "signature"}
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _prepare_execution(**kwargs: Any) -> dict[str, Any]:
    run_id = _required_text(kwargs["run_id"], "run_id")
    target_hash = kwargs["target_hash"]
    _require_sha256(target_hash, "target_hash")
    sampled_artifact = _normalize_artifact(
        kwargs["sampled_parameters_artifact"], "sampled_parameters_artifact"
    )
    sampled_parameters = _sampled_parameters_from_artifact(
        sampled_artifact, run_id=run_id
    )
    chains = _normalize_chain_artifacts(
        kwargs["chain_artifacts"],
        run_id=run_id,
        sampled_parameters=sampled_parameters,
    )
    config = _normalize_artifact(kwargs["config_artifact"], "config_artifact")
    data = _normalize_artifact_groups(kwargs["data_artifacts"], "data_artifacts")
    likelihoods = _normalize_artifact_groups(
        kwargs["likelihood_artifacts"], "likelihood_artifacts"
    )
    numbers = _normalize_results(kwargs["results"])
    diagnostics = _normalize_diagnostics(
        kwargs["diagnostics"],
        sampled_parameters=sampled_parameters,
        result_names={item["name"] for item in numbers},
        expected_chain_count=len(chains),
    )
    run_attestation = _normalize_artifact(
        kwargs["run_attestation_artifact"], "run_attestation_artifact"
    )
    profile_id = _validate_run_authority_attestation(
        run_attestation,
        run_id=run_id,
        chains=chains,
        config=config,
        data=data,
        likelihoods=likelihoods,
        sampled_parameters_artifact=sampled_artifact,
        expected_environment_fingerprint=diagnostics["metrics"][
            "environment_fingerprint"
        ],
    )
    if profile_id == EXACT_PROFILE_ID:
        diagnostics = _normalize_diagnostics(
            kwargs["diagnostics"],
            sampled_parameters=sampled_parameters,
            result_names={item["name"] for item in numbers},
            expected_chain_count=len(chains),
            profile_id=profile_id,
        )
    if profile_id == EXACT_PROFILE_ID and target_hash != (
        PREREGISTERED_TARGET_COMMITMENT
    ):
        raise ValueError("exact Research Alpha target commitment mismatch")
    analysis_attestation = _normalize_artifact(
        kwargs["analysis_attestation_artifact"], "analysis_attestation_artifact"
    )
    _validate_analysis_authority_attestation(
        analysis_attestation,
        run_id=run_id,
        run_attestation=run_attestation,
        chains=chains,
        sampled_parameters=sampled_parameters,
        numbers=numbers,
        diagnostics=diagnostics,
    )
    run_payload = _load_json_artifact(run_attestation, "run_attestation_artifact")
    protocol_status = _normalize_protocol_status(run_payload.get("protocol_status"))
    protocol_amendment = _normalize_artifact(
        run_payload.get("protocol_amendment_artifact"),
        "run authority protocol_amendment_artifact",
    )
    adjudication = (
        _normalize_artifact(
            kwargs["protocol_adjudication_artifact"],
            "protocol_adjudication_artifact",
        )
        if kwargs.get("protocol_adjudication_artifact") is not None
        else None
    )
    _validate_protocol_eligibility(
        protocol_status,
        adjudication,
        run_id=run_id,
        target_hash=target_hash,
    )
    artifacts = {
        "chains": chains,
        "config": config,
        "data": data,
        "likelihoods": likelihoods,
        "sampled_parameters": sampled_artifact,
        "run_attestation": run_attestation,
        "analysis_attestation": analysis_attestation,
        "protocol_adjudication": adjudication,
        "protocol_amendment": protocol_amendment,
    }
    fingerprints = _fingerprints_from_artifacts(artifacts)
    execution_binding = _execution_binding_payload(
        run_id=run_id,
        target_hash=target_hash,
        artifacts=artifacts,
        sampled_parameters=sampled_parameters,
        numbers=numbers,
        diagnostic_metrics=diagnostics["metrics"],
    )
    return {
        "run_id": run_id,
        "profile_id": profile_id,
        "target_hash": target_hash,
        "artifacts": artifacts,
        "fingerprints": fingerprints,
        "sampled_parameters": sampled_parameters,
        "numbers": numbers,
        "diagnostics": diagnostics,
        "protocol_status": protocol_status,
        "execution_fingerprint": scientific_content_hash(execution_binding),
    }


def _execution_binding_payload(
    *,
    run_id: str,
    target_hash: str,
    artifacts: Mapping[str, Any],
    sampled_parameters: Sequence[str],
    numbers: Sequence[Mapping[str, Any]],
    diagnostic_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "target_hash": target_hash,
        "artifacts": dict(artifacts),
        "sampled_parameters": list(sampled_parameters),
        "numbers": [
            {field: item[field] for field in ("name", *_INTERVAL_FIELDS)}
            for item in sorted(numbers, key=lambda value: str(value["name"]).lower())
        ],
        "diagnostic_metrics": dict(diagnostic_metrics),
    }


def _normalize_chain_artifacts(
    value: Any, *, run_id: str, sampled_parameters: Sequence[str]
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("chain_artifacts must be a sequence")
    if len(value) < PUBLICATION_MIN_INDEPENDENT_CHAINS:
        raise ValueError("at least four chain artifacts are required")
    records: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError("chain artifact must be a mapping")
        artifact = _normalize_artifact(raw, f"chain_artifacts[{index}]")
        table = _parse_cobaya_chain(
            Path(artifact["path"]),
            sampled_parameters=sampled_parameters,
            field=f"chain_artifacts[{index}]",
        )
        chain_id = _required_text(raw.get("chain_id"), "chain_id")
        seed = _integer(raw.get("seed"), "seed")
        attestation = _normalize_artifact(
            raw.get("attestation"),
            f"chain_artifacts[{index}].attestation",
        )
        attested = _load_json_artifact(
            attestation, f"chain_artifacts[{index}].attestation"
        )
        if attested.get("schema_version") != 1 or attested.get("kind") != (
            "research_alpha_chain"
        ):
            raise ValueError("chain artifact attestation schema is invalid")
        _validate_self_hash(attested, "chain artifact attestation")
        if attested.get("run_id") != run_id:
            raise ValueError("chain artifact attestation run_id mismatch")
        if attested.get("chain_id") != chain_id or attested.get("seed") != seed:
            raise ValueError("chain artifact ID or seed does not match attestation")
        if attested.get("chain_sha256") != artifact["sha256"]:
            raise ValueError("chain artifact bytes do not match attestation")
        if attested.get("columns") != table["columns"]:
            raise ValueError("chain artifact columns do not match attestation")
        if attested.get("n_draws") != table["n_draws"]:
            raise ValueError("chain artifact draw count does not match attestation")
        records.append(
            {
                "chain_id": chain_id,
                "seed": seed,
                **artifact,
                "attestation": attestation,
                "columns": table["columns"],
                "n_draws": table["n_draws"],
            }
        )
    for field in ("chain_id", "seed", "path", "sha256"):
        values = [item[field] for item in records]
        if len(set(values)) != len(values):
            raise ValueError(f"chain artifact {field} values must be unique")
    return records


def _parse_cobaya_chain(
    path: Path,
    *,
    sampled_parameters: Sequence[str],
    field: str,
) -> dict[str, Any]:
    """Parse enough of a Cobaya table to reject prose or empty-chain fixtures."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{field} is not a readable Cobaya table") from exc
    first_nonempty = next((line for line in lines if line.strip()), None)
    if first_nonempty is None or not first_nonempty.lstrip().startswith("#"):
        raise ValueError(f"{field} is missing a Cobaya header")
    columns = first_nonempty.lstrip()[1:].split()
    if not columns or len(columns) != len(set(columns)):
        raise ValueError(f"{field} has an empty or duplicate Cobaya header")
    missing = sorted(
        {"weight", "minuslogpost", *sampled_parameters}.difference(columns)
    )
    if missing:
        raise ValueError(
            f"{field} is missing required Cobaya columns: {','.join(missing)}"
        )
    n_draws = 0
    weight_index = columns.index("weight")
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        values = stripped.split()
        if len(values) != len(columns):
            raise ValueError(
                f"{field} row {line_number} does not match the Cobaya header"
            )
        try:
            numeric = [float(value) for value in values]
        except ValueError as exc:
            raise ValueError(f"{field} row {line_number} is not numeric") from exc
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError(f"{field} row {line_number} contains non-finite values")
        if numeric[weight_index] <= 0:
            raise ValueError(f"{field} row {line_number} has a non-positive weight")
        n_draws += 1
    if n_draws < RESEARCH_ALPHA_MIN_RAW_DRAWS_PER_CHAIN:
        raise ValueError(
            f"{field} has fewer than {RESEARCH_ALPHA_MIN_RAW_DRAWS_PER_CHAIN} "
            "Cobaya draws"
        )
    return {"columns": columns, "n_draws": n_draws}


def _normalize_artifact_groups(value: Any, field: str) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{field} must be a non-empty mapping")
    groups: dict[str, list[dict[str, Any]]] = {}
    for name, raw in value.items():
        group_name = _required_text(name, f"{field} key")
        records = raw if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, Mapping)) else [raw]
        if not records:
            raise ValueError(f"{field}.{group_name} has no artifacts")
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(records):
            artifact = _normalize_artifact(
                item, f"{field}.{group_name}[{index}]"
            )
            if isinstance(item, Mapping) and item.get("logical_path") is not None:
                logical = Path(
                    _required_text(
                        item.get("logical_path"),
                        f"{field}.{group_name}[{index}].logical_path",
                    )
                )
                if logical.is_absolute() or ".." in logical.parts:
                    raise ValueError(
                        f"{field}.{group_name}[{index}].logical_path is unsafe"
                    )
                artifact["logical_path"] = logical.as_posix()
            normalized.append(artifact)
        groups[group_name] = normalized
    return dict(sorted(groups.items()))


def _validate_run_authority_attestation(
    artifact: Mapping[str, Any],
    *,
    run_id: str,
    chains: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    data: Mapping[str, Any],
    likelihoods: Mapping[str, Any],
    sampled_parameters_artifact: Mapping[str, Any],
    expected_environment_fingerprint: str,
    expected_exact_run_role: str = "primary",
) -> str:
    payload = _load_json_artifact(artifact, "run_attestation_artifact")
    if not verify_scientific_attestation(
        payload, expected_type=_RUN_ATTESTATION_TYPE
    ):
        raise ValueError("run authority attestation HMAC is invalid")
    if payload.get("source") != "server_attested":
        raise ValueError("run authority attestation source is untrusted")
    if payload.get("run_id") != run_id:
        raise ValueError("run authority attestation run_id mismatch")
    profile_id = _normalize_research_profile(payload.get("profile_id"))
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_evidence_signing_key_binding(payload)
    evidence_class = payload.get("evidence_class")
    evidence_class_allowed = (
        evidence_class in {"formal_candidate", "model_adequacy"}
        if profile_id == EXACT_PROFILE_ID
        else evidence_class == "ci_fixture"
    )
    if not evidence_class_allowed:
        raise ValueError("run authority attestation evidence class mismatch")
    if (
        payload.get("status") != "completed"
        or payload.get("success") is not True
        or payload.get("returncode") != 0
    ):
        raise ValueError("run authority attestation is not a successful completion")
    if payload.get("runner") != "cobaya":
        raise ValueError("run authority attestation runner is not Cobaya")
    canonical_receipt_artifact = _require_canonical_nested_artifact(
        payload.get("canonical_run_receipt_artifact"),
        "run authority canonical_run_receipt_artifact",
    )
    canonical_receipt = _load_json_artifact(
        canonical_receipt_artifact, "run authority canonical_run_receipt_artifact"
    )
    _validate_named_self_hash(
        canonical_receipt,
        field="run authority canonical run receipt",
        candidates=("attestation_sha256",),
    )
    for field in (
        "run_id",
        "profile_id",
        "evidence_class",
        "status",
        "success",
        "returncode",
        "environment_fingerprint",
        "seed_binding",
        "protocol_status",
    ):
        if canonical_receipt.get(field) != payload.get(field):
            raise ValueError(f"run authority canonical receipt {field} mismatch")
    if profile_id == EXACT_PROFILE_ID:
        if (
            payload.get("host_execution_trust_boundary")
            != EXACT_HOST_EXECUTION_TRUST_BOUNDARY
        ):
            raise ValueError("exact run authority host execution trust boundary mismatch")
        if (
            canonical_receipt.get("host_execution_trust_boundary")
            != EXACT_HOST_EXECUTION_TRUST_BOUNDARY
        ):
            raise ValueError(
                "exact run authority canonical host execution trust boundary mismatch"
            )
    resources = canonical_receipt.get("resource_binding")
    if not isinstance(resources, Mapping) or resources.get(
        "mpi_processes"
    ) != payload.get("mpi_processes") or resources.get(
        "threads_per_process"
    ) != payload.get("threads_per_process"):
        raise ValueError("run authority canonical receipt resources mismatch")
    if canonical_receipt.get("config_path") != config["path"] or canonical_receipt.get(
        "config_sha256"
    ) != config["sha256"]:
        raise ValueError("run authority canonical receipt config mismatch")
    canonical_files = canonical_receipt.get("artifacts") or []
    canonical_bindings = {
        (str(item.get("path")), str(item.get("sha256")))
        for item in canonical_files
        if isinstance(item, Mapping)
    }
    if not all(
        (chain["path"], chain["sha256"]) in canonical_bindings for chain in chains
    ):
        raise ValueError("run authority canonical receipt chain mismatch")
    payload_protocol_status = _normalize_protocol_status(
        payload.get("protocol_status")
    )
    if profile_id == EXACT_PROFILE_ID and payload_protocol_status != _PROTOCOL_STATUS:
        raise ValueError("exact run authority cannot claim analyst blinding was achieved")
    amendment = _require_canonical_nested_artifact(
        payload.get("protocol_amendment_artifact"),
        "run authority protocol_amendment_artifact",
    )
    if amendment["sha256"] != _PROTOCOL_AMENDMENT_SHA256:
        raise ValueError("run authority protocol amendment hash mismatch")
    _require_sha256(
        payload.get("environment_fingerprint"),
        "run authority environment_fingerprint",
    )
    if payload.get("environment_fingerprint") != expected_environment_fingerprint:
        raise ValueError("run authority environment fingerprint mismatch")
    if _integer(payload.get("mpi_processes"), "run mpi_processes") != 4:
        raise ValueError("run authority attestation did not use four MPI processes")
    if _integer(payload.get("threads_per_process"), "run threads_per_process") != 3:
        raise ValueError("run authority attestation did not use three threads per process")
    if len(chains) != 4:
        raise ValueError("formal Research Alpha run must contain exactly four chains")

    _require_canonical_nested_artifact(
        payload.get("preflight_artifact"), "run preflight_artifact"
    )
    _require_canonical_nested_artifact(
        payload.get("generation_artifact"), "run generation_artifact"
    )
    code_artifacts = _require_canonical_artifact_list(
        payload.get("code_artifacts"), "run code_artifacts"
    )
    workflow_artifacts = _require_canonical_artifact_list(
        payload.get("workflow_receipt_artifacts"),
        "run workflow_receipt_artifacts",
    )
    checkpoint_artifacts = _require_canonical_artifact_list(
        payload.get("checkpoint_artifacts"), "run checkpoint_artifacts"
    )
    if not code_artifacts or len(workflow_artifacts) < 2 or not checkpoint_artifacts:
        raise ValueError(
            "run authority attestation lacks code, workflow, or checkpoint artifacts"
        )
    if payload.get("config_artifact") != dict(config):
        raise ValueError("run authority attestation config artifact mismatch")
    if payload.get("data_artifacts") != dict(data):
        raise ValueError("run authority attestation data artifacts mismatch")
    if payload.get("likelihood_artifacts") != dict(likelihoods):
        raise ValueError("run authority attestation likelihood artifacts mismatch")
    if payload.get("sampled_parameters_artifact") != dict(
        sampled_parameters_artifact
    ):
        raise ValueError("run authority attestation sampled-parameter mismatch")
    if payload.get("chain_artifacts") != list(chains):
        raise ValueError("run authority attestation chain artifacts mismatch")

    seed_binding = payload.get("seed_binding")
    if not isinstance(seed_binding, Mapping):
        raise ValueError("run authority attestation seed binding is missing")
    _required_text(seed_binding.get("algorithm"), "run seed algorithm")
    identities = seed_binding.get("identities")
    if not isinstance(identities, Sequence) or isinstance(
        identities, (str, bytes)
    ) or len(identities) != 4:
        raise ValueError("run authority attestation must bind four seed identities")
    observed_by_rank: dict[int, tuple[int, tuple[int, ...]]] = {}
    for index, identity in enumerate(identities):
        if not isinstance(identity, Mapping):
            raise ValueError("run authority seed identity is malformed")
        rank = _integer(identity.get("rank"), f"seed identity {index} rank")
        derived_seed = _integer(
            identity.get("derived_seed"), f"seed identity {index} derived_seed"
        )
        spawn_key_raw = identity.get("spawn_key")
        if not isinstance(spawn_key_raw, Sequence) or isinstance(
            spawn_key_raw, (str, bytes)
        ) or not spawn_key_raw:
            raise ValueError("run authority seed identity spawn_key is missing")
        spawn_key = tuple(
            _integer(value, f"seed identity {index} spawn_key")
            for value in spawn_key_raw
        )
        if rank in observed_by_rank:
            raise ValueError("run authority seed ranks are not unique")
        observed_by_rank[rank] = (derived_seed, spawn_key)
    if set(observed_by_rank) != {0, 1, 2, 3}:
        raise ValueError("run authority seed ranks are not 0 through 3")
    if len({value[1] for value in observed_by_rank.values()}) != 4:
        raise ValueError("run authority seed spawn keys are not unique")
    if [observed_by_rank[index][0] for index in range(4)] != [
        int(chain["seed"]) for chain in chains
    ]:
        raise ValueError("run authority derived seeds do not match chain seeds")
    if profile_id == EXACT_PROFILE_ID:
        config_payload = _load_yaml_artifact(config, "exact run config")
        sampler = config_payload.get("sampler")
        mcmc = sampler.get("mcmc") if isinstance(sampler, Mapping) else None
        expected_entropy = mcmc.get("seed") if isinstance(mcmc, Mapping) else None
        if (
            not isinstance(expected_entropy, list)
            or len(expected_entropy) != 4
            or any(
                not isinstance(identity, Mapping)
                or identity.get("entropy") != expected_entropy
                or identity.get("spawn_key") != [rank]
                for rank, identity in enumerate(identities)
            )
        ):
            raise ValueError("exact run seed identities are not config-derived")

    termination = payload.get("termination")
    if not isinstance(termination, Mapping):
        raise ValueError("run authority termination record is missing")
    if (
        termination.get("passed") is not True
        or termination.get("status") != "converged"
        or termination.get("max_samples_reached") is not False
        or termination.get("early_stop") is not False
        or _integer(termination.get("mpi_size"), "termination mpi_size") != 4
    ):
        raise ValueError("run authority termination record is not converged")
    termination_checkpoint = _require_canonical_nested_artifact(
        termination.get("checkpoint_artifact"), "run termination checkpoint"
    )
    if termination_checkpoint not in checkpoint_artifacts:
        raise ValueError("run termination checkpoint is not in checkpoint artifacts")
    if profile_id == EXACT_PROFILE_ID:
        preflight_artifact = _require_canonical_nested_artifact(
            payload.get("preflight_artifact"), "run preflight_artifact"
        )
        generation_artifact = _require_canonical_nested_artifact(
            payload.get("generation_artifact"), "run generation_artifact"
        )
        _validate_exact_artifact_contract(
            config=config,
            data=data,
            likelihoods=likelihoods,
            sampled_parameters=_sampled_parameters_from_artifact(
                sampled_parameters_artifact, run_id=run_id
            ),
            preflight_payload=_load_json_artifact(
                preflight_artifact, "run preflight artifact"
            ),
            preflight_artifact=preflight_artifact,
            generation_payload=_load_json_artifact(
                generation_artifact, "run generation artifact"
            ),
            code_artifacts=code_artifacts,
            evidence_class=evidence_class,
            expected_role=expected_exact_run_role,
        )
    return profile_id


def _validate_analysis_authority_attestation(
    artifact: Mapping[str, Any],
    *,
    run_id: str,
    run_attestation: Mapping[str, Any],
    chains: Sequence[Mapping[str, Any]],
    sampled_parameters: Sequence[str],
    numbers: Sequence[Mapping[str, Any]],
    diagnostics: Mapping[str, Any],
) -> None:
    payload = _load_json_artifact(artifact, "analysis_attestation_artifact")
    if not verify_scientific_attestation(
        payload, expected_type=_ANALYSIS_ATTESTATION_TYPE
    ):
        raise ValueError("analysis authority attestation HMAC is invalid")
    if payload.get("source") != "server_attested":
        raise ValueError("analysis authority attestation source is untrusted")
    profile_id = _normalize_research_profile(payload.get("profile_id"))
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_evidence_signing_key_binding(payload)
    run_payload = _load_json_artifact(
        run_attestation, "analysis authority run attestation"
    )
    if run_payload.get("profile_id") != profile_id:
        raise ValueError("analysis authority profile does not match the run")
    if payload.get("run_id") != run_id or payload.get("status") != "completed":
        raise ValueError("analysis authority attestation run binding is invalid")
    if payload.get("run_attestation_sha256") != run_attestation["sha256"]:
        raise ValueError("analysis authority attestation run certificate mismatch")
    receipt_artifact = _require_canonical_nested_artifact(
        payload.get("analysis_receipt_artifact"), "analysis receipt artifact"
    )
    analysis_code = _require_canonical_nested_artifact(
        payload.get("analysis_code_artifact"), "analysis code artifact"
    )
    receipt = _load_json_artifact(receipt_artifact, "analysis receipt artifact")
    if receipt.get("schema_version") != 2 or receipt.get("artifact_type") != (
        "w0wa_exact_analysis"
    ):
        raise ValueError("analysis receipt is not a canonical exact analysis")
    _validate_named_self_hash(
        receipt,
        field="analysis receipt artifact",
        candidates=("manifest_sha256",),
    )
    if (
        receipt.get("profile_id") != profile_id
        or receipt.get("status") != "ANALYZED"
        or ((receipt.get("run_identity") or {}).get("run_id") != run_id)
    ):
        raise ValueError("analysis receipt exact run identity mismatch")
    binding = receipt.get("research_alpha_binding")
    if not isinstance(binding, Mapping):
        raise ValueError("analysis receipt research_alpha_binding is missing")
    if binding.get("sampled_parameters") != list(sampled_parameters) or binding.get(
        "chain_sha256"
    ) != [item["sha256"] for item in chains]:
        raise ValueError("analysis receipt research alpha chain binding mismatch")
    offline_artifact = _require_canonical_nested_artifact(
        payload.get("offline_grade_receipt_artifact"),
        "analysis offline_grade_receipt_artifact",
    )
    offline = _load_json_artifact(
        offline_artifact, "analysis offline_grade_receipt_artifact"
    )
    if offline.get("schema_version") != 1 or offline.get("artifact_type") != (
        "research_alpha_primary_offline_grade"
    ):
        raise ValueError("primary offline grade receipt schema is invalid")
    _validate_named_self_hash(
        offline,
        field="primary offline grade receipt",
        candidates=("receipt_sha256",),
    )
    if (
        offline.get("status") != "passed"
        or offline.get("run_id") != run_id
        or offline.get("analysis_receipt_sha256") != receipt_artifact["sha256"]
        or offline.get("sampled_parameters") != list(sampled_parameters)
        or offline.get("chain_sha256") != [item["sha256"] for item in chains]
        or _normalize_results(offline.get("numbers")) != list(numbers)
    ):
        raise ValueError("primary offline grade receipt binding mismatch")
    receipt_diagnostics = _normalize_diagnostics(
        offline.get("diagnostics") or {},
        sampled_parameters=sampled_parameters,
        result_names={str(item["name"]) for item in numbers},
        expected_chain_count=len(chains),
        profile_id=profile_id,
    )
    if receipt_diagnostics != diagnostics:
        raise ValueError("analysis receipt diagnostics mismatch")
    if payload.get("chain_artifacts") != list(chains):
        raise ValueError("analysis authority attestation chain artifacts mismatch")
    if payload.get("sampled_parameters") != list(sampled_parameters):
        raise ValueError("analysis authority attestation sampled parameters mismatch")
    if _normalize_results(payload.get("numbers")) != list(numbers):
        raise ValueError("analysis authority attestation result intervals mismatch")
    normalized_diagnostics = _normalize_diagnostics(
        payload.get("diagnostics") or {},
        sampled_parameters=sampled_parameters,
        result_names={str(item["name"]) for item in numbers},
        expected_chain_count=len(chains),
        profile_id=profile_id,
    )
    if normalized_diagnostics != diagnostics:
        raise ValueError("analysis authority attestation diagnostics mismatch")
    receipt_chain_files = (
        ((receipt.get("posterior") or {}).get("diagnostics") or {}).get(
            "chain_files"
        )
        or []
    )
    if receipt_chain_files:
        observed_hashes = {
            item.get("sha256") for item in receipt_chain_files if isinstance(item, Mapping)
        }
        if observed_hashes != {item["sha256"] for item in chains}:
            raise ValueError("analysis receipt chain hashes mismatch")
    if profile_id == EXACT_PROFILE_ID:
        if (
            receipt.get("claim_scope") != EXACT_CLAIM_SCOPE
            or receipt.get("target_commitment")
            != PREREGISTERED_TARGET_COMMITMENT
            or receipt.get("evidence_ready_for_offline_grading") is not True
            or offline.get("profile_id") != EXACT_PROFILE_ID
            or offline.get("target_hash") != PREREGISTERED_TARGET_COMMITMENT
        ):
            raise ValueError("analysis authority exact receipt contract is incomplete")
        if analysis_code["sha256"] != TRUSTED_CODE_SHA256[
            "canonical_full_likelihood_evidence.py"
        ]:
            raise ValueError("analysis authority code hash is not trusted")


def _normalize_protocol_status(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("research alpha protocol status is missing")
    status = {
        "target_preregistration": value.get("target_preregistration"),
        "computation_answer_key_separation": value.get(
            "computation_answer_key_separation"
        ),
        "analyst_blinding": value.get("analyst_blinding"),
    }
    if status["target_preregistration"] != "frozen" or status[
        "computation_answer_key_separation"
    ] != "enforced":
        raise ValueError("research alpha protocol prerequisites are not enforced")
    if status["analyst_blinding"] not in {"achieved", "not_achieved"}:
        raise ValueError("research alpha analyst blinding status is invalid")
    if dict(value) != status:
        raise ValueError("research alpha protocol status has unexpected fields")
    return {key: str(item) for key, item in status.items()}


def _validate_protocol_eligibility(
    protocol_status: Mapping[str, Any],
    adjudication_artifact: Mapping[str, Any] | None,
    *,
    run_id: str,
    target_hash: str,
) -> None:
    status = _normalize_protocol_status(protocol_status)
    if status["analyst_blinding"] == "achieved":
        if adjudication_artifact is not None:
            raise ValueError("blinded run must not carry an unblinded adjudication")
        return
    if adjudication_artifact is None:
        raise ValueError(
            "analyst blinding was not achieved and protocol adjudication is missing"
        )
    if not TRUSTED_PROTOCOL_AUTHORITY_REGISTRY:
        raise ValueError(
            "protocol authority registry is empty; known-target waiver is disabled"
        )
    payload = _load_json_artifact(
        adjudication_artifact, "protocol_adjudication_artifact"
    )
    key_path_text = os.environ.get(PROTOCOL_AUTHORITY_PUBLIC_KEY_ENV)
    authority_id = str(payload.get("authority_id") or "")
    frozen_key_hash = TRUSTED_PROTOCOL_AUTHORITY_REGISTRY.get(authority_id)
    if not key_path_text:
        raise ValueError("independent protocol-adjudication authority is not configured")
    if frozen_key_hash is None:
        raise ValueError("protocol adjudication authority is not preregistered")
    key_path = Path(key_path_text).expanduser().resolve()
    key_artifact = _normalize_artifact(
        {"path": str(key_path), "sha256": _hash_file(key_path)},
        "protocol adjudication public key",
    )
    if (
        payload.get("schema_version") != 1
        or payload.get("algorithm") != "ed25519"
        or payload.get("authority_id") != authority_id
        or payload.get("authority_key_sha256") != frozen_key_hash
        or key_artifact["sha256"] != frozen_key_hash
    ):
        raise ValueError("protocol adjudication authority is invalid")
    if (
        payload.get("source") != "independent_protocol_authority"
        or payload.get("status") != "authorized"
        or payload.get("run_id") != run_id
        or payload.get("target_hash") != target_hash
        or payload.get("claim_scope") != "parameter_interval_reproduction_only"
        or payload.get("known_target_reproduction_authorized") is not True
        or payload.get("protocol_status") != status
    ):
        raise ValueError("protocol adjudication binding mismatch")
    _required_text(payload.get("adjudicator"), "protocol adjudicator")
    _require_canonical_nested_artifact(
        payload.get("rationale_artifact"), "protocol adjudication rationale_artifact"
    )
    if set(payload.get("prohibited_conclusions") or []) != {
        "LambdaCDM_rejected",
        "dynamic_dark_energy_discovered",
    }:
        raise ValueError("protocol adjudication prohibited conclusions mismatch")
    signature = payload.get("signature")
    if not isinstance(signature, str):
        raise ValueError("protocol adjudication signature is missing")
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("protocol adjudication signature is not valid base64") from exc
    try:
        _load_ed25519_public_key(key_path).verify(
            signature_bytes,
            external_review_signing_bytes(payload),
        )
    except InvalidSignature as exc:
        raise ValueError("protocol adjudication Ed25519 signature is invalid") from exc


def _require_canonical_nested_artifact(value: Any, field: str) -> dict[str, Any]:
    normalized = _normalize_artifact(value, field)
    if not isinstance(value, Mapping) or dict(value) != normalized:
        raise ValueError(f"{field} is noncanonical")
    return normalized


def _require_canonical_artifact_list(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an artifact sequence")
    normalized = [
        _require_canonical_nested_artifact(item, f"{field}[{index}]")
        for index, item in enumerate(value)
    ]
    if len({item["path"] for item in normalized}) != len(normalized) or len(
        {item["sha256"] for item in normalized}
    ) != len(normalized):
        raise ValueError(f"{field} artifacts must be distinct")
    return normalized


def _normalize_artifact(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an artifact mapping")
    path_value = value.get("path") or value.get("artifact_path")
    path = Path(_required_text(path_value, f"{field}.path")).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"{field}.path is not an existing file")
    observed_hash = _hash_file(path)
    expected_hash = value.get("sha256") or value.get("artifact_hash")
    _require_sha256(expected_hash, f"{field}.sha256")
    if observed_hash != expected_hash:
        raise ValueError(f"{field}.sha256 does not match file bytes")
    size = path.stat().st_size
    if value.get("size_bytes") is not None and _integer(
        value.get("size_bytes"), f"{field}.size_bytes"
    ) != size:
        raise ValueError(f"{field}.size_bytes does not match file bytes")
    return {"path": str(path), "sha256": observed_hash, "size_bytes": size}


def _revalidate_artifact_inventory(value: Any, *, run_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("artifact inventory is missing")
    sampled = _normalize_artifact(
        value.get("sampled_parameters"), "artifacts.sampled_parameters"
    )
    sampled_parameters = _sampled_parameters_from_artifact(sampled, run_id=run_id)
    chains = _normalize_chain_artifacts(
        value.get("chains"),
        run_id=run_id,
        sampled_parameters=sampled_parameters,
    )
    config = _normalize_artifact(value.get("config"), "artifacts.config")
    data = _normalize_artifact_groups(value.get("data"), "artifacts.data")
    likelihoods = _normalize_artifact_groups(
        value.get("likelihoods"), "artifacts.likelihoods"
    )
    run_attestation = _normalize_artifact(
        value.get("run_attestation"), "artifacts.run_attestation"
    )
    analysis_attestation = _normalize_artifact(
        value.get("analysis_attestation"), "artifacts.analysis_attestation"
    )
    protocol_adjudication = (
        _normalize_artifact(
            value.get("protocol_adjudication"),
            "artifacts.protocol_adjudication",
        )
        if value.get("protocol_adjudication") is not None
        else None
    )
    protocol_amendment = _normalize_artifact(
        value.get("protocol_amendment"), "artifacts.protocol_amendment"
    )
    if protocol_amendment["sha256"] != _PROTOCOL_AMENDMENT_SHA256:
        raise ValueError("protocol amendment hash mismatch")
    return {
        "chains": chains,
        "config": config,
        "data": data,
        "likelihoods": likelihoods,
        "sampled_parameters": sampled,
        "run_attestation": run_attestation,
        "analysis_attestation": analysis_attestation,
        "protocol_adjudication": protocol_adjudication,
        "protocol_amendment": protocol_amendment,
    }


def _fingerprints_from_artifacts(artifacts: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "chains": scientific_content_hash(artifacts["chains"]),
        "config": artifacts["config"]["sha256"],
        "data": {
            name: scientific_content_hash(records)
            for name, records in artifacts["data"].items()
        },
        "likelihood": {
            name: scientific_content_hash(records)
            for name, records in artifacts["likelihoods"].items()
        },
        "sampled_parameters": artifacts["sampled_parameters"]["sha256"],
        "run_attestation": artifacts["run_attestation"]["sha256"],
        "analysis_attestation": artifacts["analysis_attestation"]["sha256"],
        "protocol_adjudication": (
            artifacts["protocol_adjudication"]["sha256"]
            if artifacts.get("protocol_adjudication") is not None
            else None
        ),
        "protocol_amendment": artifacts["protocol_amendment"]["sha256"],
    }


def _sampled_parameters_from_artifact(
    artifact: Mapping[str, Any], *, run_id: str
) -> list[str]:
    payload = _load_json_artifact(artifact, "sampled_parameters_artifact")
    if payload.get("schema_version") != 1:
        raise ValueError("sampled-parameter artifact schema is invalid")
    if payload.get("kind") != "sampled_parameters":
        raise ValueError("sampled-parameter artifact kind is invalid")
    _validate_self_hash(payload, "sampled-parameter artifact")
    if payload.get("run_id") != run_id:
        raise ValueError("sampled-parameter artifact run_id mismatch")
    return _required_unique_texts(
        payload.get("sampled_parameters"), "sampled_parameters"
    )


def _normalize_results(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        iterable: Any = [
            {"name": name, **dict(item)} if isinstance(item, Mapping) else {"name": name}
            for name, item in value.items()
        ]
    else:
        iterable = value
    if not isinstance(iterable, Sequence) or isinstance(iterable, (str, bytes)) or not iterable:
        raise ValueError("results must contain at least one interval")
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for raw in iterable:
        if not isinstance(raw, Mapping):
            raise ValueError("result interval must be a mapping")
        name = _required_text(raw.get("name") or raw.get("parameter"), "result name")
        if name in names:
            raise ValueError(f"duplicate result interval for {name}")
        names.add(name)
        record = {
            "name": name,
            "center": _finite_number(
                _first_present(raw, ("center", "value", "median", "mean")),
                f"{name}.center",
            ),
            "lower_68": _finite_number(
                _first_present(raw, ("lower_68", "lower", "q16")),
                f"{name}.lower_68",
            ),
            "upper_68": _finite_number(
                _first_present(raw, ("upper_68", "upper", "q84")),
                f"{name}.upper_68",
            ),
            "uncertainty_minus": _finite_number(
                _first_present(raw, ("uncertainty_minus", "error_minus", "minus")),
                f"{name}.uncertainty_minus",
            ),
            "uncertainty_plus": _finite_number(
                _first_present(raw, ("uncertainty_plus", "error_plus", "plus")),
                f"{name}.uncertainty_plus",
            ),
        }
        _validate_interval(record)
        normalized.append(record)
    return sorted(normalized, key=lambda item: item["name"].lower())


def _validate_interval(record: Mapping[str, Any]) -> None:
    name = str(record["name"])
    center = float(record["center"])
    lower = float(record["lower_68"])
    upper = float(record["upper_68"])
    minus = float(record["uncertainty_minus"])
    plus = float(record["uncertainty_plus"])
    if not lower < center < upper:
        raise ValueError(f"{name} interval bounds are not ordered")
    if minus <= 0 or plus <= 0:
        raise ValueError(f"{name} uncertainties must be positive")
    scale = max(abs(center), abs(lower), abs(upper), minus, plus, 1.0)
    tolerance = max(1e-12, scale * 1e-10)
    if not math.isclose(center - lower, minus, rel_tol=1e-10, abs_tol=tolerance):
        raise ValueError(f"{name} lower bound and uncertainty are misaligned")
    if not math.isclose(upper - center, plus, rel_tol=1e-10, abs_tol=tolerance):
        raise ValueError(f"{name} upper bound and uncertainty are misaligned")


def _normalize_diagnostics(
    diagnostics: Mapping[str, Any],
    *,
    sampled_parameters: Sequence[str],
    result_names: set[str],
    expected_chain_count: int,
    profile_id: str | None = None,
) -> dict[str, Any]:
    if str(diagnostics.get("status") or "").lower() not in _PASS_STATUSES:
        raise ValueError("diagnostics status is not passed")
    metrics = diagnostics.get("metrics")
    if not isinstance(metrics, Mapping) or not metrics:
        raise ValueError("diagnostics metrics are missing")
    if str(metrics.get("rhat_method") or "").lower() not in {"rank", "rank_normalized"}:
        raise ValueError("diagnostics R-hat method is not rank-normalized")
    if str(metrics.get("ess_method") or "").lower() != "bulk":
        raise ValueError("diagnostics ESS method is not bulk")
    if str(metrics.get("mcse_reference") or "").lower() != "per_parameter":
        raise ValueError("diagnostics MCSE reference is not per_parameter")
    _require_sha256(
        metrics.get("environment_fingerprint"),
        "diagnostics.environment_fingerprint",
    )
    n_chains = _integer(metrics.get("n_independent_chains"), "n_independent_chains")
    if n_chains != expected_chain_count or n_chains < PUBLICATION_MIN_INDEPENDENT_CHAINS:
        raise ValueError("diagnostics independent-chain count mismatch")
    if profile_id == EXACT_PROFILE_ID:
        balance = metrics.get("chain_length_balance")
        fractions = (
            balance.get("alignment_fraction_per_chain")
            if isinstance(balance, Mapping)
            else None
        )
        if (
            not isinstance(balance, Mapping)
            or balance.get("passed") is not True
            or balance.get("alignment")
            != "diagnostics_only_recent_draws_truncated_to_shortest_chain"
            or _float_or_none(
                balance.get("minimum_alignment_fraction_inclusive")
            )
            != 0.90
            or not isinstance(fractions, Sequence)
            or isinstance(fractions, (str, bytes))
            or len(fractions) != expected_chain_count
        ):
            raise ValueError("exact chain-length balance evidence is missing")
        normalized_fractions = [
            _finite_number(item, f"chain alignment fraction {index}")
            for index, item in enumerate(fractions)
        ]
        if any(item < 0.90 or item > 1.0 for item in normalized_fractions):
            raise ValueError("exact chain diagnostic alignment is below 0.90")
        maximum_discard = _finite_number(
            balance.get("maximum_discarded_fraction"),
            "maximum chain diagnostic discarded fraction",
        )
        expected_discard = 1.0 - min(normalized_fractions)
        if maximum_discard > 0.10 or not math.isclose(
            maximum_discard,
            expected_discard,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("exact chain diagnostic discard fraction is inconsistent")
    critical = _required_unique_texts(
        metrics.get("critical_parameters"), "critical_parameters"
    )
    if critical != list(sampled_parameters):
        raise ValueError("critical_parameters do not exactly match sampled-parameter artifact")
    per_parameter = metrics.get("per_parameter")
    if not isinstance(per_parameter, Mapping):
        raise ValueError("diagnostics per_parameter records are missing")
    required = set(sampled_parameters) | result_names
    if not required.issubset(set(per_parameter)):
        raise ValueError("diagnostics omit sampled or reported parameters")
    for name, raw in per_parameter.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"diagnostics missing for {name}")
        rhat = _finite_number(raw.get("rhat"), f"{name}.rhat")
        ess = _finite_number(raw.get("ess_bulk"), f"{name}.ess_bulk")
        mcse_ratio = _finite_number(
            _first_present(raw, ("mcse_over_reference_sigma", "mcse_sigma_ratio")),
            f"{name}.mcse_over_reference_sigma",
        )
        reference_kind = str(raw.get("mcse_reference_kind") or "").lower()
        if reference_kind not in {"paper_sigma", "posterior_sd"}:
            raise ValueError(f"{name} MCSE reference kind is invalid")
        paper_names = set(result_names)
        if "w0" in paper_names:
            paper_names.add("w")
        if "Omega_m" in paper_names or "Ωm" in paper_names:
            paper_names.add("omegam")
        expected_kind = "paper_sigma" if name in paper_names else "posterior_sd"
        if reference_kind != expected_kind:
            raise ValueError(f"{name} MCSE reference kind does not match its role")
        mcse_mean = _finite_number(raw.get("mcse_mean"), f"{name}.mcse_mean")
        posterior_std = _finite_number(
            raw.get("posterior_std"), f"{name}.posterior_std"
        )
        reference_value = _finite_number(
            raw.get("mcse_reference_value"), f"{name}.mcse_reference_value"
        )
        if mcse_mean < 0 or posterior_std <= 0 or reference_value <= 0:
            raise ValueError(f"{name} MCSE inputs are not positive finite scales")
        if reference_kind == "posterior_sd" and not math.isclose(
            reference_value,
            posterior_std,
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            raise ValueError(f"{name} posterior-SD MCSE reference is misbound")
        if profile_id == EXACT_PROFILE_ID and reference_kind == "paper_sigma":
            paper_parameter = {
                "w": "w0",
                "omegam": "Omega_m",
                "Ωm": "Omega_m",
            }.get(name, name)
            paper_record = PREREGISTERED_PAPER_UNCERTAINTIES.get(paper_parameter)
            expected_reference = (
                min(float(paper_record["minus"]), float(paper_record["plus"]))
                if isinstance(paper_record, Mapping)
                else None
            )
            if expected_reference is None or not math.isclose(
                reference_value,
                expected_reference,
                rel_tol=0.0,
                abs_tol=1e-15,
            ):
                raise ValueError(f"{name} MCSE paper reference is not preregistered")
        if not math.isclose(
            mcse_ratio,
            mcse_mean / reference_value,
            rel_tol=1e-10,
            abs_tol=1e-15,
        ):
            raise ValueError(f"{name} MCSE ratio is inconsistent with its reference")
        if rhat >= PUBLICATION_RHAT_MAX:
            raise ValueError(f"{name} rank-normalized R-hat is not below 1.01")
        if ess < RESEARCH_ALPHA_EXACT_BULK_ESS_MIN:
            raise ValueError(f"{name} bulk ESS is below 1000")
        if mcse_ratio >= RESEARCH_ALPHA_MCSE_SIGMA_MAX:
            raise ValueError(f"{name} MCSE is not below 0.05 reference sigma")
    return {"status": "passed", "metrics": dict(metrics)}


def _normalize_adequacy_evidence(
    value: Any,
    *,
    run_id: str,
    execution_fingerprint: str,
    primary_chain_artifacts: Sequence[Mapping[str, Any]],
    primary_run_attestation_artifact: Mapping[str, Any],
    primary_environment_fingerprint: str,
    primary_numbers: Sequence[Mapping[str, Any]],
    sampled_parameters: Sequence[str],
    profile_id: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise ValueError("adequacy evidence mapping is required")
    if set(value) != set(PUBLICATION_REQUIRED_ADEQUACY_CHECKS):
        raise ValueError("adequacy evidence check set is incomplete or unexpected")
    normalized: dict[str, dict[str, Any]] = {}
    paths: set[str] = set()
    hashes: set[str] = set()
    for name in PUBLICATION_REQUIRED_ADEQUACY_CHECKS:
        raw = value[name]
        artifact = _normalize_artifact(raw, f"adequacy.{name}")
        if artifact["path"] in paths or artifact["sha256"] in hashes:
            raise ValueError("adequacy artifacts must have distinct paths and hashes")
        paths.add(artifact["path"])
        hashes.add(artifact["sha256"])
        payload = _load_json_artifact(artifact, f"adequacy.{name}")
        if not verify_scientific_attestation(
            payload, expected_type=_ADEQUACY_ATTESTATION_TYPE
        ):
            raise ValueError(f"adequacy authority attestation HMAC invalid for {name}")
        if payload.get("source") != "server_attested" or payload.get(
            "producer"
        ) != "canonical_full_likelihood_analyzer":
            raise ValueError(f"adequacy authority source invalid for {name}")
        if payload.get("profile_id") != profile_id:
            raise ValueError(f"adequacy authority profile mismatch for {name}")
        if profile_id == EXACT_PROFILE_ID:
            _validate_exact_evidence_signing_key_binding(payload)
        aggregate_receipt_artifact = _require_canonical_nested_artifact(
            payload.get("canonical_aggregate_receipt_artifact"),
            f"adequacy.{name}.canonical_aggregate_receipt_artifact",
        )
        aggregate_receipt = _load_json_artifact(
            aggregate_receipt_artifact,
            f"adequacy.{name}.canonical_aggregate_receipt_artifact",
        )
        if aggregate_receipt.get("schema_version") != 1 or aggregate_receipt.get(
            "artifact_type"
        ) != "research_alpha_adequacy_aggregate_receipt":
            raise ValueError(f"adequacy aggregate receipt invalid for {name}")
        if profile_id == EXACT_PROFILE_ID:
            _validate_exact_evidence_signing_key_binding(aggregate_receipt)
            if not verify_scientific_attestation(
                aggregate_receipt,
                expected_type=_EXACT_ADEQUACY_AGGREGATE_RECEIPT_TYPE,
            ):
                raise ValueError(
                    f"adequacy aggregate producer HMAC invalid for {name}"
                )
        else:
            _validate_named_self_hash(
                aggregate_receipt,
                field=f"adequacy aggregate receipt {name}",
                candidates=("aggregate_sha256",),
            )
        if profile_id == EXACT_PROFILE_ID:
            aggregate_code = _trusted_exact_adequacy_code(
                payload.get("analysis_code_artifact"),
                registry=TRUSTED_ADEQUACY_ANALYZER_CODE_SHA256,
                field=f"adequacy.{name}.analysis_code_artifact",
            )
            if (
                aggregate_receipt.get("analysis_code_artifact") != aggregate_code
            ):
                raise ValueError(f"adequacy aggregate analysis code invalid for {name}")
        if any(
            aggregate_receipt.get(field) != payload.get(field)
            for field in (
                "profile_id",
                "run_id",
                "execution_fingerprint",
                "check",
                "status",
                "metrics",
            )
        ):
            raise ValueError(f"adequacy aggregate receipt binding mismatch for {name}")
        if payload.get("kind") != "research_alpha_adequacy":
            raise ValueError(f"adequacy artifact kind mismatch for {name}")
        if payload.get("check") != name:
            raise ValueError(f"adequacy artifact check mismatch for {name}")
        if payload.get("run_id") != run_id:
            raise ValueError(f"adequacy artifact run_id mismatch for {name}")
        if payload.get("execution_fingerprint") != execution_fingerprint:
            raise ValueError(f"adequacy artifact execution_fingerprint mismatch for {name}")
        if str(payload.get("status") or "").lower() not in _PASS_STATUSES:
            raise ValueError(f"adequacy evidence did not pass for {name}")
        _validate_adequacy_metrics(
            name,
            payload.get("metrics"),
            run_id=run_id,
            primary_chain_artifacts=primary_chain_artifacts,
            primary_run_attestation_artifact=primary_run_attestation_artifact,
            primary_environment_fingerprint=primary_environment_fingerprint,
            execution_fingerprint=execution_fingerprint,
            primary_numbers=primary_numbers,
            sampled_parameters=sampled_parameters,
            profile_id=profile_id,
        )
        normalized[name] = {
            **payload,
            "artifact_id": artifact["sha256"],
            "artifact_path": artifact["path"],
            "artifact_hash": artifact["sha256"],
            "artifact_size_bytes": artifact["size_bytes"],
        }
    return normalized


def _validate_adequacy_metrics(
    name: str,
    value: Any,
    *,
    run_id: str,
    primary_chain_artifacts: Sequence[Mapping[str, Any]],
    primary_run_attestation_artifact: Mapping[str, Any],
    primary_environment_fingerprint: str,
    execution_fingerprint: str,
    primary_numbers: Sequence[Mapping[str, Any]],
    sampled_parameters: Sequence[str],
    profile_id: str,
) -> None:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"adequacy structured metrics missing for {name}")
    if value.get("passed") is False or value.get("all_passed") is False:
        raise ValueError(f"adequacy metrics contradict passed status for {name}")
    failed = value.get("failed_checks")
    if failed is not None and _integer(failed, f"{name}.failed_checks") != 0:
        raise ValueError(f"adequacy metrics report failures for {name}")
    if name in {"prior_predictive_check", "posterior_predictive_check"}:
        checks = value.get("checks")
        if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
            raise ValueError(f"adequacy predictive subchecks are missing for {name}")
        if _integer(value.get("n_checks"), f"{name}.n_checks") != len(checks):
            raise ValueError(f"adequacy predictive n_checks mismatch for {name}")
        if not checks:
            raise ValueError(f"adequacy predictive checks are empty for {name}")
        if value.get("all_passed") is not True:
            raise ValueError(f"adequacy predictive checks did not all pass for {name}")
        normalized_checks = _validate_subartifact_records(
            checks,
            field=f"{name}.checks",
            require_passed=True,
            run_id=run_id,
            execution_fingerprint=execution_fingerprint,
            check=name,
            profile_id=profile_id,
        )
        if profile_id == EXACT_PROFILE_ID:
            if {item["name"] for item in normalized_checks} != (
                _REQUIRED_PREDICTIVE_DISCREPANCIES
            ):
                raise ValueError(
                    f"{name} does not contain the four frozen discrepancies"
                )
            seeds: set[int] = set()
            simulation_hashes: set[str] = set()
            for item in normalized_checks:
                metrics = item["metrics"]
                if _integer(metrics.get("replicates"), "predictive replicates") < 400:
                    raise ValueError(f"{name} has fewer than 400 replicates")
                if metrics.get("discrepancy") != item["name"]:
                    raise ValueError(f"{name} discrepancy identity mismatch")
                if metrics.get("statistic") != item["name"] or metrics.get(
                    "acceptance_rule"
                ) != _EXACT_PPC_TAIL_RULE:
                    raise ValueError("predictive statistic/rule is not plan-bound")
                observed_statistic = _finite_number(
                    metrics.get("observed_statistic"),
                    "predictive observed_statistic",
                )
                replicates = _integer(
                    metrics.get("replicates"), "predictive replicates"
                )
                exceedances = _integer(
                    metrics.get("count_at_or_above_observed"),
                    "predictive count_at_or_above_observed",
                )
                if exceedances < 0 or exceedances > replicates:
                    raise ValueError("predictive exceedance count is outside replicate range")
                expected_tail = (1.0 + exceedances) / (replicates + 1.0)
                tail = _finite_number(
                    metrics.get("tail_probability"), "predictive tail_probability"
                )
                if (
                    not math.isclose(tail, expected_tail, rel_tol=1e-12, abs_tol=1e-15)
                    or not _EXACT_PPC_LOWER <= tail <= _EXACT_PPC_UPPER
                    or metrics.get("passed") is not True
                ):
                    raise ValueError("predictive empirical tail rule did not pass")
                seed = _integer(metrics.get("simulation_seed"), "simulation_seed")
                simulation = _normalize_artifact(
                    metrics.get("simulation_artifact"),
                    "predictive simulation_artifact",
                )
                if (
                    seed != item.get("runner_simulation_seed")
                    or simulation != item.get("runner_simulation_artifact")
                ):
                    raise ValueError("predictive simulation is not runner/plan bound")
                simulation_payload = _load_json_artifact(
                    simulation, "predictive simulation artifact"
                )
                _validate_named_self_hash(
                    simulation_payload,
                    field="predictive simulation artifact",
                    candidates=("self_hash",),
                )
                replicate_statistics = simulation_payload.get(
                    "replicate_statistics"
                )
                if (
                    simulation_payload.get("schema_version") != 1
                    or simulation_payload.get("artifact_type")
                    != "w0wa_predictive_replicates"
                    or simulation_payload.get("profile_id") != EXACT_PROFILE_ID
                    or simulation_payload.get("run_id") != run_id
                    or simulation_payload.get("check") != name
                    or simulation_payload.get("discrepancy") != item["name"]
                    or simulation_payload.get("simulation_seed") != seed
                    or not math.isclose(
                        _finite_number(
                            simulation_payload.get("observed_statistic"),
                            "predictive artifact observed_statistic",
                        ),
                        observed_statistic,
                        rel_tol=1e-12,
                        abs_tol=1e-15,
                    )
                    or not isinstance(replicate_statistics, Sequence)
                    or isinstance(replicate_statistics, (str, bytes))
                    or len(replicate_statistics) != replicates
                ):
                    raise ValueError("predictive simulation artifact schema is invalid")
                simulated_values = [
                    _finite_number(value, "predictive replicate statistic")
                    for value in replicate_statistics
                ]
                if sum(value >= observed_statistic for value in simulated_values) != (
                    exceedances
                ):
                    raise ValueError("predictive exceedance count is not data-derived")
                if seed in seeds or simulation["sha256"] in simulation_hashes:
                    raise ValueError(f"{name} reuses predictive simulations or seeds")
                seeds.add(seed)
                simulation_hashes.add(simulation["sha256"])
    elif name == "prior_sensitivity":
        variant_runs = value.get("variant_runs")
        if not isinstance(variant_runs, Sequence) or isinstance(
            variant_runs, (str, bytes)
        ):
            raise ValueError("prior_sensitivity variant runner evidence is missing")
        normalized_variant_runs = _validate_subartifact_records(
            variant_runs,
            field="prior_sensitivity.variant_runs",
            require_passed=True,
            run_id=run_id,
            execution_fingerprint=execution_fingerprint,
            check=name,
            profile_id=profile_id,
        )
        if {item["name"] for item in normalized_variant_runs} != {
            "baseline_prior",
            "widened_prior",
        }:
            raise ValueError("prior_sensitivity runner variants are incomplete")
        if profile_id == EXACT_PROFILE_ID:
            variants_by_name = {
                item["name"]: item for item in normalized_variant_runs
            }
            baseline_numbers = {
                item["name"]: item
                for item in _normalize_results(
                    variants_by_name["baseline_prior"]["metrics"].get("intervals")
                )
            }
            widened_numbers = {
                item["name"]: item
                for item in _normalize_results(
                    variants_by_name["widened_prior"]["metrics"].get("intervals")
                )
            }
            required_reports = {"Omega_m", "H0", "w0", "wa"}
            if set(baseline_numbers) != required_reports or set(
                widened_numbers
            ) != required_reports:
                raise ValueError("prior_sensitivity interval evidence is incomplete")
            shift_records = value.get("parameter_shifts")
            if not isinstance(shift_records, Mapping) or set(
                shift_records
            ) != required_reports:
                raise ValueError("prior_sensitivity parameter shifts are incomplete")
            recomputed_shifts: list[float] = []
            for parameter in sorted(required_reports):
                record = shift_records[parameter]
                if not isinstance(record, Mapping):
                    raise ValueError("prior_sensitivity shift record is malformed")
                baseline_center = baseline_numbers[parameter]["center"]
                widened_center = widened_numbers[parameter]["center"]
                reference_sigma = _finite_number(
                    record.get("reference_sigma"),
                    f"prior_sensitivity {parameter} reference_sigma",
                )
                if reference_sigma <= 0:
                    raise ValueError("prior_sensitivity reference sigma is not positive")
                expected_reference_sigma = _paper_sigma(
                    parameter, widened_center - baseline_center
                )
                if not math.isclose(
                    reference_sigma,
                    expected_reference_sigma,
                    rel_tol=0.0,
                    abs_tol=1e-15,
                ):
                    raise ValueError(
                        "prior_sensitivity reference sigma is not the preregistered paper sigma"
                    )
                recomputed = abs(widened_center - baseline_center) / reference_sigma
                if (
                    _float_or_none(record.get("baseline_center")) != baseline_center
                    or _float_or_none(record.get("widened_center"))
                    != widened_center
                    or not math.isclose(
                        _finite_number(
                            record.get("standardized_shift"),
                            f"prior_sensitivity {parameter} standardized_shift",
                        ),
                        recomputed,
                        rel_tol=1e-10,
                        abs_tol=1e-12,
                    )
                ):
                    raise ValueError("prior_sensitivity shift is not derived from runs")
                recomputed_shifts.append(recomputed)
        widened = set(
            _required_unique_texts(
                value.get("widened_parameters"), f"{name}.widened_parameters"
            )
        )
        if not {"w0", "wa"}.issubset(widened):
            raise ValueError("prior_sensitivity did not widen both w0 and wa")
        baseline = _normalize_artifact(
            value.get("baseline_prior_artifact"), f"{name}.baseline_prior_artifact"
        )
        widened_artifact = _normalize_artifact(
            value.get("widened_prior_artifact"), f"{name}.widened_prior_artifact"
        )
        if baseline["sha256"] == widened_artifact["sha256"]:
            raise ValueError("prior_sensitivity reused the baseline prior configuration")
        if profile_id == EXACT_PROFILE_ID:
            variants_by_name = {item["name"]: item for item in normalized_variant_runs}
            if baseline != variants_by_name["baseline_prior"][
                "runner_config_artifact"
            ] or widened_artifact != variants_by_name["widened_prior"][
                "runner_config_artifact"
            ]:
                raise ValueError(
                    "prior_sensitivity prior artifacts are not the executed registered configs"
                )
        _validate_widened_prior_configs(baseline, widened_artifact)
        shift = _finite_number(value.get("max_standardized_shift"), f"{name}.shift")
        if profile_id == EXACT_PROFILE_ID and not math.isclose(
            shift,
            max(recomputed_shifts),
            rel_tol=1e-10,
            abs_tol=1e-12,
        ):
            raise ValueError("prior_sensitivity aggregate shift is contradictory")
        if shift > 0.20:
            raise ValueError("prior_sensitivity exceeds 0.20 sigma")
    elif name == "systematics_robustness":
        variants = value.get("variants")
        if not isinstance(variants, Sequence) or isinstance(variants, (str, bytes)):
            raise ValueError("systematics_robustness variants are missing")
        normalized_variants = _validate_subartifact_records(
            variants,
            field="systematics_robustness.variants",
            require_passed=True,
            require_numeric_effects=True,
            run_id=run_id,
            execution_fingerprint=execution_fingerprint,
            check=name,
            profile_id=profile_id,
        )
        names = {item["name"] for item in normalized_variants}
        if names != _REQUIRED_SYSTEMATICS_VARIANTS:
            raise ValueError("systematics_robustness required variant set is incomplete")
        if profile_id == EXACT_PROFILE_ID:
            config_hashes = {
                item["runner_config_sha256"] for item in normalized_variants
            }
            if len(config_hashes) != len(_REQUIRED_SYSTEMATICS_VARIANTS):
                raise ValueError(
                    "systematics_robustness variants reuse the same runner config"
                )
            if any(
                item["metrics"].get("variant") != item["name"]
                for item in normalized_variants
            ):
                raise ValueError("systematics_robustness variant config identity mismatch")
            for item in normalized_variants:
                effects = item["metrics"].get("parameter_effects")
                if not isinstance(effects, Mapping) or set(effects) != {
                    "Omega_m",
                    "H0",
                    "w0",
                    "wa",
                }:
                    raise ValueError(
                        f"systematics_robustness effects incomplete for {item['name']}"
                    )
                shifts: list[float] = []
                width_changes: list[float] = []
                for parameter, effect in effects.items():
                    if not isinstance(effect, Mapping):
                        raise ValueError("systematics effect record is malformed")
                    baseline_center = _finite_number(
                        effect.get("baseline_center"), f"{parameter}.baseline_center"
                    )
                    variant_center = _finite_number(
                        effect.get("variant_center"), f"{parameter}.variant_center"
                    )
                    baseline_width = _finite_number(
                        effect.get("baseline_width_68"),
                        f"{parameter}.baseline_width_68",
                    )
                    variant_width = _finite_number(
                        effect.get("variant_width_68"),
                        f"{parameter}.variant_width_68",
                    )
                    reference_sigma = _finite_number(
                        effect.get("reference_sigma"),
                        f"{parameter}.reference_sigma",
                    )
                    if min(baseline_width, variant_width, reference_sigma) <= 0:
                        raise ValueError("systematics effect scales are not positive")
                    expected_reference_sigma = _paper_sigma(
                        parameter, variant_center - baseline_center
                    )
                    if not math.isclose(
                        reference_sigma,
                        expected_reference_sigma,
                        rel_tol=0.0,
                        abs_tol=1e-15,
                    ):
                        raise ValueError(
                            "systematics reference sigma is not the preregistered paper sigma"
                        )
                    computed_shift = abs(variant_center - baseline_center) / reference_sigma
                    computed_width = abs(variant_width - baseline_width) / baseline_width
                    if not math.isclose(
                        _finite_number(
                            effect.get("standardized_shift"),
                            f"{parameter}.standardized_shift",
                        ),
                        computed_shift,
                        rel_tol=1e-10,
                        abs_tol=1e-12,
                    ) or not math.isclose(
                        _finite_number(
                            effect.get("interval_fractional_change"),
                            f"{parameter}.interval_fractional_change",
                        ),
                        computed_width,
                        rel_tol=1e-10,
                        abs_tol=1e-12,
                    ):
                        raise ValueError("systematics effects are not derived from intervals")
                    shifts.append(computed_shift)
                    width_changes.append(computed_width)
                if not math.isclose(
                    item["standardized_shift"],
                    max(shifts),
                    rel_tol=1e-10,
                    abs_tol=1e-12,
                ) or not math.isclose(
                    item["interval_fractional_change"],
                    max(width_changes),
                    rel_tol=1e-10,
                    abs_tol=1e-12,
                ):
                    raise ValueError("systematics variant aggregate is contradictory")
        shift = _finite_number(value.get("max_standardized_shift"), f"{name}.shift")
        interval = _finite_number(
            value.get("max_interval_fractional_change"), f"{name}.interval_change"
        )
        observed_shift = max(item["standardized_shift"] for item in normalized_variants)
        observed_interval = max(
            item["interval_fractional_change"] for item in normalized_variants
        )
        if not math.isclose(shift, observed_shift, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("systematics_robustness aggregate shift is contradictory")
        if not math.isclose(interval, observed_interval, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("systematics_robustness interval aggregate is contradictory")
    elif name == "simulation_recovery":
        fiducials = value.get("fiducials")
        if not isinstance(fiducials, Sequence) or isinstance(fiducials, (str, bytes)):
            raise ValueError("simulation_recovery fiducials are missing")
        normalized_fiducials = _validate_subartifact_records(
            fiducials,
            field="simulation_recovery.fiducials",
            require_passed=True,
            require_recovery=True,
            run_id=run_id,
            execution_fingerprint=execution_fingerprint,
            check=name,
            profile_id=profile_id,
        )
        if profile_id == EXACT_PROFILE_ID:
            if len(normalized_fiducials) != 3 or {
                item["name"] for item in normalized_fiducials
            } != set(_REQUIRED_INJECTION_TRUTHS):
                raise ValueError("simulation_recovery frozen fiducials mismatch")
            simulation_hashes: set[str] = set()
            simulation_seeds: set[int] = set()
            for item in normalized_fiducials:
                metrics = item["metrics"]
                truth = metrics.get("truth")
                expected_truth = _REQUIRED_INJECTION_TRUTHS[item["name"]]
                if (
                    not isinstance(truth, Mapping)
                    or set(truth) != set(expected_truth)
                    or {
                        key: _float_or_none(truth.get(key)) for key in expected_truth
                    }
                    != expected_truth
                ):
                    raise ValueError(
                        f"simulation_recovery truth mismatch for {item['name']}"
                    )
                simulation = item["simulated_data_artifact"]
                seed = item["simulation_seed"]
                if (
                    simulation["sha256"] in simulation_hashes
                    or seed in simulation_seeds
                ):
                    raise ValueError("simulation_recovery reused data or seed")
                simulation_hashes.add(simulation["sha256"])
                simulation_seeds.add(seed)
        if _integer(value.get("n_fiducials"), f"{name}.n_fiducials") != len(
            normalized_fiducials
        ) or len(normalized_fiducials) < 3:
            raise ValueError("simulation_recovery has fewer than three fiducials")
        if value.get("all_inside_joint_95") is not True or not all(
            item["inside_joint_95"] for item in normalized_fiducials
        ):
            raise ValueError("simulation_recovery misses a joint 95% region")
        bias = _finite_number(
            value.get("mean_standardized_bias"), f"{name}.mean_bias"
        )
        if profile_id == EXACT_PROFILE_ID:
            all_biases = [
                abs(value)
                for item in normalized_fiducials
                for value in item["standardized_bias_by_parameter"].values()
            ]
            observed_bias = sum(all_biases) / len(all_biases)
        else:
            observed_bias = sum(
                abs(item["standardized_bias"]) for item in normalized_fiducials
            ) / len(normalized_fiducials)
        if not math.isclose(bias, observed_bias, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("simulation_recovery aggregate bias is contradictory")
        if abs(bias) >= 0.30:
            raise ValueError("simulation_recovery mean bias is not below 0.30")
    elif name == "independent_reproduction":
        _validate_independent_reproduction(
            value,
            primary_run_id=run_id,
            primary_execution_fingerprint=execution_fingerprint,
            primary_environment_fingerprint=primary_environment_fingerprint,
            primary_chain_artifacts=primary_chain_artifacts,
            primary_run_attestation_artifact=primary_run_attestation_artifact,
            primary_numbers=primary_numbers,
            sampled_parameters=sampled_parameters,
            profile_id=profile_id,
        )


def _validate_independent_reproduction(
    value: Mapping[str, Any],
    *,
    primary_run_id: str,
    primary_execution_fingerprint: str,
    primary_environment_fingerprint: str,
    primary_chain_artifacts: Sequence[Mapping[str, Any]],
    primary_run_attestation_artifact: Mapping[str, Any],
    primary_numbers: Sequence[Mapping[str, Any]],
    sampled_parameters: Sequence[str],
    profile_id: str,
) -> None:
    for field in ("isolated_environment", "independent_seeds", "reproduced"):
        if value.get(field) is not True:
            raise ValueError(f"independent_reproduction {field} is not true")
    report_artifact = _require_canonical_nested_artifact(
        value.get("postprocessor_report_artifact"),
        "independent_reproduction.postprocessor_report_artifact",
    )
    report = _load_json_artifact(
        report_artifact, "independent_reproduction.postprocessor_report_artifact"
    )
    if report.get("schema_version") != 1 or report.get("artifact_type") != (
        "independent_w0wa_postprocess"
    ):
        raise ValueError("independent reproduction report schema is invalid")
    _validate_named_self_hash(
        report,
        field="independent reproduction report",
        candidates=("report_sha256",),
    )
    if report.get("status") != "PASS" or report.get("failures") != []:
        raise ValueError("independent reproduction postprocessor did not pass")
    if report.get("profile_id") != profile_id:
        raise ValueError("independent postprocessor profile mismatch")
    if profile_id == EXACT_PROFILE_ID and (
        report.get("claim_scope") != EXACT_CLAIM_SCOPE
        or report.get("target_commitment") != PREREGISTERED_TARGET_COMMITMENT
        or _normalize_protocol_status(report.get("protocol_status"))
        != _PROTOCOL_STATUS
    ):
        raise ValueError("independent postprocessor exact contract is incomplete")
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_independent_postprocessor_policy(report)
    report_binding = report.get("research_alpha_binding")
    if not isinstance(report_binding, Mapping):
        raise ValueError("independent postprocessor research_alpha_binding is missing")
    independent_run_id = _required_text(
        report.get("run_id"), "independent_reproduction.independent_run_id"
    )
    if independent_run_id == primary_run_id:
        raise ValueError("independent reproduction reused the primary run_id")

    run_artifact = _require_canonical_nested_artifact(
        value.get("independent_run_attestation_artifact"),
        "independent_reproduction.independent_run_attestation_artifact",
    )
    analysis_artifact = _require_canonical_nested_artifact(
        value.get("independent_analysis_attestation_artifact"),
        "independent_reproduction.independent_analysis_attestation_artifact",
    )
    analysis = _load_json_artifact(
        analysis_artifact,
        "independent_reproduction.independent_analysis_attestation_artifact",
    )
    if not verify_scientific_attestation(
        analysis, expected_type=_INDEPENDENT_ANALYSIS_ATTESTATION_TYPE
    ):
        raise ValueError("independent analysis authority HMAC is invalid")
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_evidence_signing_key_binding(analysis)
    if (
        analysis.get("source") != "server_attested"
        or analysis.get("profile_id") != profile_id
        or analysis.get("status") != "completed"
        or analysis.get("run_id") != independent_run_id
        or analysis.get("primary_execution_fingerprint")
        != primary_execution_fingerprint
        or analysis.get("run_attestation_sha256") != run_artifact["sha256"]
        or analysis.get("postprocessor_report_artifact") != report_artifact
    ):
        raise ValueError("independent analysis authority binding mismatch")
    independent_analysis_code = _require_canonical_nested_artifact(
        analysis.get("analysis_code_artifact"),
        "independent reproduction analysis_code_artifact",
    )
    if profile_id == EXACT_PROFILE_ID and (
        Path(independent_analysis_code["path"]).name
        != "independent_w0wa_postprocess.py"
        or independent_analysis_code["sha256"]
        != TRUSTED_CODE_SHA256["independent_w0wa_postprocess.py"]
    ):
        raise ValueError("independent reproduction analysis code is not trusted")
    environment_fingerprint = analysis.get("environment_fingerprint")
    _require_sha256(
        environment_fingerprint,
        "independent_reproduction.environment_fingerprint",
    )
    if environment_fingerprint == primary_environment_fingerprint:
        raise ValueError("independent reproduction reused the primary environment")
    if analysis.get("sampled_parameters") != list(sampled_parameters):
        raise ValueError("independent reproduction sampled parameters mismatch")
    offline_artifact = _require_canonical_nested_artifact(
        analysis.get("offline_grade_receipt_artifact"),
        "independent reproduction offline_grade_receipt_artifact",
    )
    offline = _load_json_artifact(
        offline_artifact, "independent reproduction offline_grade_receipt_artifact"
    )
    if offline.get("schema_version") != 1 or offline.get("artifact_type") != (
        "research_alpha_independent_offline_grade"
    ):
        raise ValueError("independent offline grade receipt schema is invalid")
    _validate_named_self_hash(
        offline,
        field="independent offline grade receipt",
        candidates=("receipt_sha256",),
    )

    independent_numbers = _normalize_results(analysis.get("numbers"))
    _validate_independent_result_agreement(
        primary_numbers=primary_numbers,
        independent_numbers=independent_numbers,
    )
    independent_diagnostics = _normalize_diagnostics(
        analysis.get("diagnostics") or {},
        sampled_parameters=sampled_parameters,
        result_names={item["name"] for item in independent_numbers},
        expected_chain_count=4,
        profile_id=profile_id,
    )
    if independent_diagnostics["metrics"]["environment_fingerprint"] != (
        environment_fingerprint
    ):
        raise ValueError("independent diagnostics environment mismatch")
    if (
        report_binding.get("primary_execution_fingerprint")
        != primary_execution_fingerprint
        or report_binding.get("environment_fingerprint") != environment_fingerprint
        or report_binding.get("sampled_parameters") != list(sampled_parameters)
    ):
        raise ValueError("independent postprocessor research alpha binding mismatch")
    if (
        offline.get("status") != "passed"
        or offline.get("profile_id") != profile_id
        or offline.get("run_id") != independent_run_id
        or offline.get("primary_execution_fingerprint")
        != primary_execution_fingerprint
        or offline.get("postprocessor_report_sha256") != report_artifact["sha256"]
        or offline.get("sampled_parameters") != list(sampled_parameters)
        or _normalize_results(offline.get("numbers")) != independent_numbers
        or _normalize_diagnostics(
            offline.get("diagnostics") or {},
            sampled_parameters=sampled_parameters,
            result_names={item["name"] for item in independent_numbers},
            expected_chain_count=4,
            profile_id=profile_id,
        )
        != independent_diagnostics
    ):
        raise ValueError("independent offline grade binding mismatch")

    run_payload = _load_json_artifact(
        run_artifact, "independent_reproduction.independent_run_attestation_artifact"
    )
    environment_proof: dict[str, Any] | None = None
    primary_run_artifact: dict[str, Any] | None = None
    primary_environment_location: dict[str, Any] | None = None
    if profile_id == EXACT_PROFILE_ID:
        environment_proof = _validate_independent_environment_preflight_binding(
            report_binding,
            run_payload=run_payload,
            expected_environment_fingerprint=str(environment_fingerprint),
        )
        primary_run_artifact = _require_canonical_nested_artifact(
            primary_run_attestation_artifact,
            "independent reproduction primary_run_attestation_artifact",
        )
        primary_run_payload = _load_json_artifact(
            primary_run_artifact,
            "independent reproduction primary_run_attestation_artifact",
        )
        if not verify_scientific_attestation(
            primary_run_payload, expected_type=_RUN_ATTESTATION_TYPE
        ):
            raise ValueError("primary exact run authority HMAC is invalid")
        _validate_exact_evidence_signing_key_binding(primary_run_payload)
        if (
            primary_run_payload.get("profile_id") != EXACT_PROFILE_ID
            or primary_run_payload.get("run_id") != primary_run_id
            or primary_run_payload.get("evidence_class") != "formal_candidate"
        ):
            raise ValueError("primary exact run authority identity is invalid")
        primary_environment_location = _exact_run_environment_location(
            primary_run_payload,
            field="independent reproduction primary exact run",
        )
        independent_environment_location = environment_proof[
            "environment_location"
        ]
        _validate_independent_environment_locations(
            primary=primary_environment_location,
            independent=independent_environment_location,
        )
        if (
            analysis.get("environment_preflight_artifact")
            != environment_proof["environment_preflight_artifact"]
            or analysis.get("preflight_environment_fingerprint")
            != environment_proof["preflight_environment_fingerprint"]
            or analysis.get("import_policy") != environment_proof["import_policy"]
            or analysis.get("environment_location")
            != independent_environment_location
            or analysis.get("primary_environment_location")
            != primary_environment_location
            or analysis.get("primary_run_attestation_sha256")
            != primary_run_artifact["sha256"]
        ):
            raise ValueError(
                "independent analysis environment preflight binding mismatch"
            )
    independent_sampled_artifact = _normalize_artifact(
        run_payload.get("sampled_parameters_artifact"),
        "independent run sampled_parameters_artifact",
    )
    independent_sampled = _sampled_parameters_from_artifact(
        independent_sampled_artifact, run_id=independent_run_id
    )
    if independent_sampled != list(sampled_parameters):
        raise ValueError("independent run sampled-parameter artifact mismatch")
    independent_chains = _normalize_chain_artifacts(
        run_payload.get("chain_artifacts"),
        run_id=independent_run_id,
        sampled_parameters=sampled_parameters,
    )
    if report_binding.get("chain_sha256") != [
        item["sha256"] for item in independent_chains
    ] or offline.get("chain_sha256") != [
        item["sha256"] for item in independent_chains
    ]:
        raise ValueError("independent chain hashes are not bound by both analyzers")
    independent_config = _normalize_artifact(
        run_payload.get("config_artifact"), "independent run config_artifact"
    )
    independent_data = _normalize_artifact_groups(
        run_payload.get("data_artifacts"), "independent run data_artifacts"
    )
    independent_likelihoods = _normalize_artifact_groups(
        run_payload.get("likelihood_artifacts"),
        "independent run likelihood_artifacts",
    )
    independent_profile = _validate_run_authority_attestation(
        run_artifact,
        run_id=independent_run_id,
        chains=independent_chains,
        config=independent_config,
        data=independent_data,
        likelihoods=independent_likelihoods,
        sampled_parameters_artifact=independent_sampled_artifact,
        expected_environment_fingerprint=environment_fingerprint,
        expected_exact_run_role="independent_reproduction",
    )
    if independent_profile != profile_id:
        raise ValueError("independent run profile mismatch")
    if analysis.get("chain_artifacts") != independent_chains:
        raise ValueError("independent analysis chain artifacts mismatch")
    primary_seeds = {item["seed"] for item in primary_chain_artifacts}
    primary_hashes = {item["sha256"] for item in primary_chain_artifacts}
    if primary_seeds & {item["seed"] for item in independent_chains}:
        raise ValueError("independent reproduction reused primary seeds")
    if primary_hashes & {item["sha256"] for item in independent_chains}:
        raise ValueError("independent reproduction reused primary chain files")

    _validate_postprocessor_report_binding(
        report,
        chains=independent_chains,
        sampled_parameters=sampled_parameters,
        numbers=independent_numbers,
        diagnostics=independent_diagnostics,
    )
    reproduction_binding = {
        "run_id": independent_run_id,
        "profile_id": profile_id,
        "primary_execution_fingerprint": primary_execution_fingerprint,
        "environment_fingerprint": environment_fingerprint,
        "chain_artifacts": independent_chains,
        "sampled_parameters": list(sampled_parameters),
        "numbers": independent_numbers,
        "diagnostics": independent_diagnostics,
        "postprocessor_report_artifact": report_artifact,
        "run_attestation_artifact": run_artifact,
        "offline_grade_receipt_artifact": offline_artifact,
    }
    if environment_proof is not None:
        reproduction_binding["environment_preflight_artifact"] = (
            environment_proof["environment_preflight_artifact"]
        )
        reproduction_binding["preflight_environment_fingerprint"] = (
            environment_proof["preflight_environment_fingerprint"]
        )
        reproduction_binding["import_policy"] = environment_proof["import_policy"]
        reproduction_binding["environment_location"] = environment_proof[
            "environment_location"
        ]
        reproduction_binding["primary_environment_location"] = (
            primary_environment_location
        )
        reproduction_binding["primary_run_attestation_sha256"] = (
            primary_run_artifact["sha256"]
        )
    expected_fingerprint = scientific_content_hash(reproduction_binding)
    if analysis.get("independent_execution_fingerprint") != expected_fingerprint:
        raise ValueError("independent reproduction fingerprint is not self-consistent")


def _validate_postprocessor_report_binding(
    report: Mapping[str, Any],
    *,
    chains: Sequence[Mapping[str, Any]],
    sampled_parameters: Sequence[str],
    numbers: Sequence[Mapping[str, Any]],
    diagnostics: Mapping[str, Any],
) -> None:
    if report.get("sampled_parameters") != list(sampled_parameters):
        raise ValueError("independent postprocessor sampled parameters mismatch")
    chain_files = report.get("chain_files")
    if not isinstance(chain_files, Sequence) or isinstance(
        chain_files, (str, bytes)
    ) or len(chain_files) != len(chains):
        raise ValueError("independent postprocessor chain files are incomplete")
    observed: set[tuple[str, str]] = set()
    for index, item in enumerate(chain_files):
        if not isinstance(item, Mapping):
            raise ValueError("independent postprocessor chain record is malformed")
        path = Path(
            _required_text(item.get("path"), f"independent chain_files[{index}].path")
        ).expanduser().resolve()
        digest = item.get("sha256")
        _require_sha256(digest, f"independent chain_files[{index}].sha256")
        if _hash_file(path) != digest:
            raise ValueError("independent postprocessor chain hash mismatch")
        observed.add((str(path), str(digest)))
    if observed != {(item["path"], item["sha256"]) for item in chains}:
        raise ValueError("independent postprocessor did not analyze certified chains")

    intervals = report.get("intervals_68")
    if not isinstance(intervals, Mapping):
        raise ValueError("independent postprocessor intervals are missing")
    for number in numbers:
        report_name = _independent_report_name(str(number["name"]), intervals)
        raw = intervals.get(report_name)
        if not isinstance(raw, Mapping):
            raise ValueError(
                f"independent postprocessor interval missing for {number['name']}"
            )
        normalized = _normalize_results([{"name": number["name"], **dict(raw)}])[0]
        if normalized != dict(number):
            raise ValueError(
                f"independent postprocessor interval mismatch for {number['name']}"
            )

    raw_diagnostics = report.get("diagnostics")
    if not isinstance(raw_diagnostics, Mapping):
        raise ValueError("independent postprocessor diagnostics are missing")
    normalized_per_parameter = diagnostics["metrics"]["per_parameter"]
    for name, expected in normalized_per_parameter.items():
        report_name = _independent_report_name(str(name), raw_diagnostics)
        raw = raw_diagnostics.get(report_name)
        if not isinstance(raw, Mapping) or raw.get("passed") is not True:
            raise ValueError(f"independent postprocessor diagnostics failed for {name}")
        if not math.isclose(
            _finite_number(raw.get("rank_normalized_rhat"), f"{name}.raw_rhat"),
            _finite_number(expected.get("rhat"), f"{name}.rhat"),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ) or not math.isclose(
            _finite_number(raw.get("bulk_ess"), f"{name}.raw_ess"),
            _finite_number(expected.get("ess_bulk"), f"{name}.ess_bulk"),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(f"independent postprocessor diagnostics mismatch for {name}")
        raw_mcse = _finite_number(raw.get("mcse_mean"), f"{name}.raw_mcse")
        posterior_std = _finite_number(
            raw.get("posterior_std"), f"{name}.posterior_std"
        )
        expected_mcse = _finite_number(
            expected.get("mcse_mean"), f"{name}.expected_mcse"
        )
        expected_posterior_std = _finite_number(
            expected.get("posterior_std"), f"{name}.expected_posterior_std"
        )
        if (
            raw_mcse < 0
            or posterior_std <= 0
            or not math.isclose(
                raw_mcse, expected_mcse, rel_tol=1e-12, abs_tol=1e-12
            )
            or not math.isclose(
                posterior_std,
                expected_posterior_std,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        ):
            raise ValueError(f"independent postprocessor MCSE mismatch for {name}")


def _independent_report_name(name: str, values: Mapping[str, Any]) -> str:
    aliases = {
        "w0": ("w0", "w"),
        "Omega_m": ("Omega_m", "omegam", "Ωm"),
        "Ωm": ("Ωm", "omegam", "Omega_m"),
    }
    for candidate in aliases.get(name, (name,)):
        if candidate in values:
            return candidate
    return name


def _derive_exact_injection_recovery(
    metrics: Mapping[str, Any],
    *,
    name: str,
    run_id: str,
    runner_simulation_seed: Any,
    runner_simulated_data_artifact: Any,
) -> dict[str, Any]:
    parameters = metrics.get("joint_parameters")
    if parameters != ["w", "wa"]:
        raise ValueError(f"simulation_recovery {name} joint parameters are not frozen")
    truth = metrics.get("truth")
    if not isinstance(truth, Mapping):
        raise ValueError(f"simulation_recovery {name} truth is missing")
    truth_vector = [
        _finite_number(truth.get(parameter), f"{name}.truth.{parameter}")
        for parameter in parameters
    ]
    centers_raw = metrics.get("recovered_center")
    covariance_raw = metrics.get("recovered_covariance")
    if (
        not isinstance(centers_raw, Sequence)
        or isinstance(centers_raw, (str, bytes))
        or len(centers_raw) != 2
        or not isinstance(covariance_raw, Sequence)
        or isinstance(covariance_raw, (str, bytes))
        or len(covariance_raw) != 2
    ):
        raise ValueError(f"simulation_recovery {name} center/covariance shape is invalid")
    centers = [
        _finite_number(value, f"{name}.recovered_center") for value in centers_raw
    ]
    covariance: list[list[float]] = []
    for row in covariance_raw:
        if (
            not isinstance(row, Sequence)
            or isinstance(row, (str, bytes))
            or len(row) != 2
        ):
            raise ValueError(f"simulation_recovery {name} covariance is not 2x2")
        covariance.append(
            [_finite_number(value, f"{name}.recovered_covariance") for value in row]
        )
    if not math.isclose(
        covariance[0][1], covariance[1][0], rel_tol=1e-12, abs_tol=1e-15
    ):
        raise ValueError(f"simulation_recovery {name} covariance is not symmetric")
    determinant = covariance[0][0] * covariance[1][1] - covariance[0][1] ** 2
    if covariance[0][0] <= 0 or covariance[1][1] <= 0 or determinant <= 0:
        raise ValueError(f"simulation_recovery {name} covariance is not positive definite")
    delta = [centers[index] - truth_vector[index] for index in range(2)]
    inverse = (
        (covariance[1][1] / determinant, -covariance[0][1] / determinant),
        (-covariance[1][0] / determinant, covariance[0][0] / determinant),
    )
    mahalanobis = sum(
        delta[row] * inverse[row][column] * delta[column]
        for row in range(2)
        for column in range(2)
    )
    observed_mahalanobis = _finite_number(
        metrics.get("joint_mahalanobis_d2"), f"{name}.joint_mahalanobis_d2"
    )
    if not math.isclose(
        observed_mahalanobis, mahalanobis, rel_tol=1e-10, abs_tol=1e-12
    ):
        raise ValueError(f"simulation_recovery {name} Mahalanobis value is not derived")
    inside = mahalanobis <= _CHI2_2D_95
    if metrics.get("inside_joint_95") is not inside or not inside:
        raise ValueError(f"simulation_recovery {name} is outside joint 95%")
    standardized = {
        parameter: delta[index] / math.sqrt(covariance[index][index])
        for index, parameter in enumerate(parameters)
    }
    observed_standardized = metrics.get("per_parameter_standardized_bias")
    if not isinstance(observed_standardized, Mapping) or set(
        observed_standardized
    ) != set(parameters) or any(
        not math.isclose(
            _finite_number(observed_standardized[parameter], f"{name}.{parameter}.bias"),
            standardized[parameter],
            rel_tol=1e-10,
            abs_tol=1e-12,
        )
        for parameter in parameters
    ):
        raise ValueError(f"simulation_recovery {name} standardized biases are not derived")
    mean_absolute_bias = sum(abs(value) for value in standardized.values()) / 2.0
    if not math.isclose(
        _finite_number(
            metrics.get("mean_absolute_standardized_bias"),
            f"{name}.mean_absolute_standardized_bias",
        ),
        mean_absolute_bias,
        rel_tol=1e-10,
        abs_tol=1e-12,
    ):
        raise ValueError(f"simulation_recovery {name} mean bias is not derived")
    seed = _integer(metrics.get("simulation_seed"), f"{name}.simulation_seed")
    simulated = _normalize_artifact(
        metrics.get("simulated_data_artifact"), f"{name}.simulated_data_artifact"
    )
    if seed != runner_simulation_seed or simulated != runner_simulated_data_artifact:
        raise ValueError(f"simulation_recovery {name} simulated data is not runner-bound")
    simulated_payload = _load_json_artifact(
        simulated, f"{name}.simulated_data_artifact"
    )
    _validate_named_self_hash(
        simulated_payload,
        field=f"{name}.simulated_data_artifact",
        candidates=("self_hash",),
    )
    data_blocks = simulated_payload.get("data_blocks")
    if (
        simulated_payload.get("schema_version") != 1
        or simulated_payload.get("artifact_type")
        != "w0wa_injection_simulated_data"
        or simulated_payload.get("profile_id") != EXACT_PROFILE_ID
        or simulated_payload.get("run_id") != run_id
        or simulated_payload.get("name") != name
        or simulated_payload.get("simulation_seed") != seed
        or simulated_payload.get("truth") != dict(truth)
        or not isinstance(data_blocks, Mapping)
        or set(data_blocks) != _EXACT_SIMULATED_DATA_BLOCKS
    ):
        raise ValueError(f"simulation_recovery {name} simulated-data schema is invalid")
    block_hashes: set[str] = set()
    for block_name, block in data_blocks.items():
        normalized_block = _normalize_artifact(
            block, f"{name}.simulated_data.{block_name}"
        )
        if normalized_block["sha256"] in block_hashes:
            raise ValueError(f"simulation_recovery {name} reuses simulated blocks")
        block_hashes.add(normalized_block["sha256"])
    joint_artifact = _normalize_artifact(
        metrics.get("joint_region_artifact"), f"{name}.joint_region_artifact"
    )
    joint_payload = _load_json_artifact(
        joint_artifact, f"{name}.joint_region_artifact"
    )
    _validate_named_self_hash(
        joint_payload,
        field=f"{name}.joint_region_artifact",
        candidates=("self_hash",),
    )
    expected_joint_fields = {
        "schema_version": 1,
        "artifact_type": "w0wa_injection_joint_region",
        "profile_id": EXACT_PROFILE_ID,
        "run_id": run_id,
        "name": name,
        "joint_parameters": list(parameters),
        "truth": {parameter: truth_vector[index] for index, parameter in enumerate(parameters)},
        "recovered_center": centers,
        "recovered_covariance": covariance,
        "joint_mahalanobis_d2": observed_mahalanobis,
        "threshold_inclusive": _CHI2_2D_95,
        "inside_joint_95": True,
    }
    if any(joint_payload.get(key) != value for key, value in expected_joint_fields.items()):
        raise ValueError(f"simulation_recovery {name} joint artifact is contradictory")
    return {
        "inside_joint_95": True,
        "standardized_bias": mean_absolute_bias,
        "standardized_bias_by_parameter": standardized,
        "simulated_data_artifact": simulated,
        "simulation_seed": seed,
    }


def _validate_subartifact_records(
    value: Sequence[Any],
    *,
    field: str,
    require_passed: bool,
    run_id: str,
    execution_fingerprint: str,
    check: str,
    profile_id: str,
    require_numeric_effects: bool = False,
    require_recovery: bool = False,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    paths: set[str] = set()
    hashes: set[str] = set()
    names: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError(f"{field}[{index}] must be a mapping")
        name = _required_text(raw.get("name"), f"{field}[{index}].name")
        if name in names:
            raise ValueError(f"{field} names must be unique")
        names.add(name)
        artifact = _normalize_artifact(raw, f"{field}[{index}]")
        if artifact["path"] in paths or artifact["sha256"] in hashes:
            raise ValueError(f"{field} artifacts must be distinct")
        paths.add(artifact["path"])
        hashes.add(artifact["sha256"])
        if require_passed and str(raw.get("status") or "").lower() not in _PASS_STATUSES:
            raise ValueError(f"{field}[{index}] did not pass")
        payload = _load_json_artifact(artifact, f"{field}[{index}]")
        if not verify_scientific_attestation(
            payload, expected_type=_ADEQUACY_ANALYSIS_ATTESTATION_TYPE
        ):
            raise ValueError(f"{field}[{index}] analyzer authority HMAC is invalid")
        if payload.get("source") != "server_attested" or payload.get("kind") != (
            "research_alpha_adequacy_subartifact"
        ):
            raise ValueError(f"{field}[{index}] schema is invalid")
        if payload.get("profile_id") != profile_id:
            raise ValueError(f"{field}[{index}] profile mismatch")
        if profile_id == EXACT_PROFILE_ID:
            _validate_exact_evidence_signing_key_binding(payload)
        if payload.get("check") != check or payload.get("name") != name:
            raise ValueError(f"{field}[{index}] identity mismatch")
        if payload.get("run_id") != run_id or payload.get(
            "execution_fingerprint"
        ) != execution_fingerprint:
            raise ValueError(f"{field}[{index}] run binding mismatch")
        if require_passed and str(payload.get("status") or "").lower() not in (
            _PASS_STATUSES
        ):
            raise ValueError(f"{field}[{index}] artifact did not pass")
        analysis_receipt_artifact = _require_canonical_nested_artifact(
            payload.get("canonical_analysis_receipt_artifact"),
            f"{field}[{index}].canonical_analysis_receipt_artifact",
        )
        analysis_receipt = _load_json_artifact(
            analysis_receipt_artifact,
            f"{field}[{index}].canonical_analysis_receipt_artifact",
        )
        if analysis_receipt.get("schema_version") != 1 or analysis_receipt.get(
            "artifact_type"
        ) != "research_alpha_adequacy_analysis_receipt":
            raise ValueError(f"{field}[{index}] analysis receipt schema is invalid")
        if profile_id == EXACT_PROFILE_ID:
            _validate_exact_evidence_signing_key_binding(analysis_receipt)
            if not verify_scientific_attestation(
                analysis_receipt,
                expected_type=_EXACT_ADEQUACY_ANALYSIS_RECEIPT_TYPE,
            ):
                raise ValueError(f"{field}[{index}] analysis producer HMAC is invalid")
        else:
            _validate_named_self_hash(
                analysis_receipt,
                field=f"{field}[{index}] analysis receipt",
                candidates=("report_sha256",),
            )
        if any(
            analysis_receipt.get(key) != payload.get(key)
            for key in (
                "run_id",
                "execution_fingerprint",
                "check",
                "name",
                "status",
                "metrics",
            )
        ):
            raise ValueError(f"{field}[{index}] analysis receipt binding mismatch")
        runner_artifact = _validate_adequacy_runner_attestation(
            payload.get("runner_attestation_artifact"),
            run_id=run_id,
            execution_fingerprint=execution_fingerprint,
            check=check,
            name=name,
            profile_id=profile_id,
        )
        analysis_code_artifact = _require_canonical_nested_artifact(
            payload.get("analysis_code_artifact"),
            f"{field}[{index}].analysis_code_artifact",
        )
        if profile_id == EXACT_PROFILE_ID:
            analysis_code_artifact = _trusted_exact_adequacy_code(
                analysis_code_artifact,
                registry=TRUSTED_ADEQUACY_ANALYZER_CODE_SHA256,
                field=f"{field}[{index}] analysis code",
            )
        if analysis_receipt.get("runner_attestation_sha256") != runner_artifact[
            "sha256"
        ] or analysis_receipt.get("analysis_code_artifact") != analysis_code_artifact:
            raise ValueError(f"{field}[{index}] analyzer provenance mismatch")
        payload_metrics = payload.get("metrics")
        if not isinstance(payload_metrics, Mapping):
            raise ValueError(f"{field}[{index}] artifact metrics are missing")
        runner_payload = _load_json_artifact(
            runner_artifact, f"{field}[{index}].runner_attestation_artifact"
        )
        runner_config = _require_canonical_nested_artifact(
            runner_payload.get("config_artifact"),
            f"{field}[{index}].runner_config_artifact",
        )
        record: dict[str, Any] = {
            "name": name,
            **artifact,
            "metrics": dict(payload_metrics),
            "runner_config_sha256": runner_config["sha256"],
            "runner_config_artifact": runner_config,
        }
        if profile_id == EXACT_PROFILE_ID:
            record["plan_config_key"] = runner_payload.get("plan_config_key")
            record["runner_simulation_seed"] = runner_payload.get(
                "simulation_seed"
            )
            for simulation_field in (
                "simulation_artifact",
                "simulated_data_artifact",
            ):
                if runner_payload.get(simulation_field) is not None:
                    record[f"runner_{simulation_field}"] = (
                        _require_canonical_nested_artifact(
                            runner_payload[simulation_field],
                            f"{field}[{index}].runner_{simulation_field}",
                        )
                    )
        if require_numeric_effects:
            record["standardized_shift"] = _finite_number(
                raw.get("standardized_shift"), f"{field}[{index}].standardized_shift"
            )
            record["interval_fractional_change"] = _finite_number(
                raw.get("interval_fractional_change"),
                f"{field}[{index}].interval_fractional_change",
            )
            if record["standardized_shift"] != _float_or_none(
                payload_metrics.get("standardized_shift")
            ) or record["interval_fractional_change"] != _float_or_none(
                payload_metrics.get("interval_fractional_change")
            ):
                raise ValueError(f"{field}[{index}] effect metrics mismatch")
            if (
                record["standardized_shift"] < 0
                or record["interval_fractional_change"] < 0
            ):
                raise ValueError(f"{field}[{index}] effect metrics cannot be negative")
            if (
                record["standardized_shift"] > 0.50
                and record["interval_fractional_change"] > 0.20
            ):
                raise ValueError(
                    f"{field}[{index}] exceeds both systematics thresholds"
                )
        if require_recovery:
            if profile_id == EXACT_PROFILE_ID:
                derived = _derive_exact_injection_recovery(
                    payload_metrics,
                    name=name,
                    run_id=run_id,
                    runner_simulation_seed=record.get("runner_simulation_seed"),
                    runner_simulated_data_artifact=record.get(
                        "runner_simulated_data_artifact"
                    ),
                )
                if (
                    raw.get("inside_joint_95") is not True
                    or not math.isclose(
                        _finite_number(
                            raw.get("standardized_bias"),
                            f"{field}[{index}].standardized_bias",
                        ),
                        derived["standardized_bias"],
                        rel_tol=1e-10,
                        abs_tol=1e-12,
                    )
                ):
                    raise ValueError(f"{field}[{index}] recovery summary mismatch")
                record.update(derived)
            else:
                if raw.get("inside_joint_95") is not True:
                    raise ValueError(f"{field}[{index}] is outside joint 95%")
                record["inside_joint_95"] = True
                record["standardized_bias"] = _finite_number(
                    raw.get("standardized_bias"),
                    f"{field}[{index}].standardized_bias",
                )
                if payload_metrics.get("inside_joint_95") is not True or record[
                    "standardized_bias"
                ] != _float_or_none(payload_metrics.get("standardized_bias")):
                    raise ValueError(f"{field}[{index}] recovery metrics mismatch")
        if not require_numeric_effects and not require_recovery and payload_metrics.get(
            "passed"
        ) is not True:
            raise ValueError(f"{field}[{index}] predictive metric did not pass")
        normalized.append(record)
    return normalized


def _validate_adequacy_runner_attestation(
    value: Any,
    *,
    run_id: str,
    execution_fingerprint: str,
    check: str,
    name: str,
    profile_id: str,
) -> dict[str, Any]:
    artifact = _require_canonical_nested_artifact(
        value, f"{check}.{name}.runner_attestation_artifact"
    )
    payload = _load_json_artifact(
        artifact, f"{check}.{name}.runner_attestation_artifact"
    )
    if not verify_scientific_attestation(
        payload, expected_type=_ADEQUACY_RUN_ATTESTATION_TYPE
    ):
        raise ValueError(f"{check}.{name} runner authority HMAC is invalid")
    if (
        payload.get("source") != "server_attested"
        or payload.get("profile_id") != profile_id
        or payload.get("kind") != "research_alpha_adequacy_run"
        or payload.get("run_id") != run_id
        or payload.get("execution_fingerprint") != execution_fingerprint
        or payload.get("check") != check
        or payload.get("name") != name
    ):
        raise ValueError(f"{check}.{name} runner authority binding mismatch")
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_evidence_signing_key_binding(payload)
    receipt_artifact = _require_canonical_nested_artifact(
        payload.get("canonical_run_receipt_artifact"),
        f"{check}.{name}.canonical_run_receipt_artifact",
    )
    receipt = _load_json_artifact(
        receipt_artifact, f"{check}.{name}.canonical_run_receipt_artifact"
    )
    if receipt.get("schema_version") != 1 or receipt.get("artifact_type") != (
        "research_alpha_adequacy_run_receipt"
    ):
        raise ValueError(f"{check}.{name} runner receipt schema is invalid")
    if profile_id == EXACT_PROFILE_ID:
        _validate_exact_evidence_signing_key_binding(receipt)
        if not verify_scientific_attestation(
            receipt, expected_type=_EXACT_ADEQUACY_RUN_RECEIPT_TYPE
        ):
            raise ValueError(f"{check}.{name} run producer HMAC is invalid")
    else:
        _validate_named_self_hash(
            receipt,
            field=f"{check}.{name} runner receipt",
            candidates=("receipt_sha256",),
        )
    if any(
        receipt.get(key) != payload.get(key)
        for key in (
            "profile_id",
            "run_id",
            "execution_fingerprint",
            "check",
            "name",
            "variant_run_id",
            "status",
            "success",
            "returncode",
            "runner",
        )
    ):
        raise ValueError(f"{check}.{name} runner receipt binding mismatch")
    if profile_id == EXACT_PROFILE_ID and receipt.get("phase") != payload.get(
        "phase"
    ):
        raise ValueError(f"{check}.{name} runner phase binding mismatch")
    expected_phase = (
        "simulation"
        if check in {"prior_predictive_check", "posterior_predictive_check"}
        else "sampling"
    )
    expected_runner = "pinned_simulator" if expected_phase == "simulation" else "cobaya"
    if (
        payload.get("status") != "completed"
        or payload.get("success") is not True
        or payload.get("returncode") != 0
        or (
            profile_id == EXACT_PROFILE_ID
            and (
                payload.get("phase") != expected_phase
                or payload.get("runner") != expected_runner
            )
        )
        or (
            profile_id != EXACT_PROFILE_ID and payload.get("runner") != "cobaya"
        )
    ):
        raise ValueError(f"{check}.{name} runner did not complete successfully")
    _required_text(payload.get("variant_run_id"), f"{check}.{name}.variant_run_id")
    outputs = _require_canonical_artifact_list(
        payload.get("output_artifacts"), f"{check}.{name}.output_artifacts"
    )
    if not outputs:
        raise ValueError(f"{check}.{name} runner has no output artifacts")
    termination = receipt.get("termination")
    if not isinstance(termination, Mapping) or termination.get("passed") is not True:
        raise ValueError(f"{check}.{name} runner termination mismatch")
    if expected_phase == "sampling" or profile_id != EXACT_PROFILE_ID:
        checkpoint = _require_canonical_nested_artifact(
            payload.get("checkpoint_artifact"), f"{check}.{name}.checkpoint_artifact"
        )
        if _normalize_artifact(
            termination.get("checkpoint_artifact"),
            f"{check}.{name}.receipt.checkpoint_artifact",
        ) != checkpoint:
            raise ValueError(f"{check}.{name} runner checkpoint mismatch")
    config = _require_canonical_nested_artifact(
        payload.get("config_artifact"), f"{check}.{name}.config_artifact"
    )
    if receipt.get("config_artifact") != config or receipt.get(
        "output_artifacts"
    ) != outputs:
        raise ValueError(f"{check}.{name} runner artifact binding mismatch")
    if profile_id == EXACT_PROFILE_ID:
        runner_code = _trusted_exact_adequacy_code(
            payload.get("runner_code_artifact"),
            registry=TRUSTED_ADEQUACY_RUNNER_CODE_SHA256,
            field=f"{check}.{name}.runner_code_artifact",
        )
        if receipt.get("runner_code_artifact") != runner_code:
            raise ValueError(f"{check}.{name} runner code binding mismatch")
        generation = _require_canonical_nested_artifact(
            payload.get("generation_receipt_artifact"),
            f"{check}.{name}.generation_receipt_artifact",
        )
        if receipt.get("generation_receipt_artifact") != generation:
            raise ValueError(f"{check}.{name} generation receipt binding mismatch")
        generation_payload = _load_json_artifact(
            generation, f"{check}.{name}.generation_receipt_artifact"
        )
        _, plan, ppc_plan, injection_plan = _exact_plan_artifact_payload(
            generation_payload
        )
        expected_key = _exact_plan_config_key(check, name)
        if (
            payload.get("plan_config_key") != expected_key
            or receipt.get("plan_config_key") != expected_key
            or _normalize_artifact(
                plan["configs"][expected_key],
                f"{check}.{name}.registered_config",
            )
            != config
        ):
            raise ValueError(f"{check}.{name} plan config binding mismatch")
        if check in {"prior_predictive_check", "posterior_predictive_check"}:
            spec = ppc_plan["checks"][check]
            order = list(spec["required_discrepancies"])
            expected_seed = int(spec["seed_entropy"][order.index(name)])
            simulation_field = "simulation_artifact"
        elif check == "simulation_recovery":
            fiducial = next(
                item for item in injection_plan["fiducials"] if item["name"] == name
            )
            expected_seed = int(fiducial["simulation_seed"])
            simulation_field = "simulated_data_artifact"
        else:
            expected_seed = None
            simulation_field = None
        if expected_seed is not None:
            simulation = _require_canonical_nested_artifact(
                payload.get(simulation_field), f"{check}.{name}.{simulation_field}"
            )
            code = _trusted_exact_adequacy_code(
                payload.get("simulation_code_artifact"),
                registry=TRUSTED_ADEQUACY_RUNNER_CODE_SHA256,
                field=f"{check}.{name}.simulation_code_artifact",
            )
            if (
                receipt.get(simulation_field) != simulation
                or receipt.get("simulation_code_artifact") != code
                or _integer(payload.get("simulation_seed"), "simulation_seed")
                != expected_seed
                or receipt.get("simulation_seed") != expected_seed
                or code != runner_code
            ):
                raise ValueError(f"{check}.{name} simulation provenance mismatch")
    return artifact


def _validate_independent_result_agreement(
    *,
    primary_numbers: Sequence[Mapping[str, Any]],
    independent_numbers: Sequence[Mapping[str, Any]],
) -> None:
    primary = {str(item["name"]): item for item in primary_numbers}
    independent = {str(item["name"]): item for item in independent_numbers}
    if set(primary) != set(independent):
        raise ValueError("independent reproduction result parameter set mismatch")
    for name, expected in primary.items():
        observed = independent[name]
        delta = float(observed["center"]) - float(expected["center"])
        sigma = (
            float(expected["uncertainty_plus"])
            if delta >= 0
            else float(expected["uncertainty_minus"])
        )
        if abs(delta) / sigma > 0.30:
            raise ValueError(f"independent reproduction center mismatch for {name}")
        primary_width = float(expected["upper_68"]) - float(expected["lower_68"])
        independent_width = float(observed["upper_68"]) - float(
            observed["lower_68"]
        )
        if abs(independent_width - primary_width) / primary_width > 0.15:
            raise ValueError(f"independent reproduction interval mismatch for {name}")


def _validate_widened_prior_configs(
    baseline_artifact: Mapping[str, Any], widened_artifact: Mapping[str, Any]
) -> None:
    baseline = _load_yaml_artifact(baseline_artifact, "baseline prior config")
    widened = _load_yaml_artifact(widened_artifact, "widened prior config")
    baseline_params = baseline.get("params")
    widened_params = widened.get("params")
    if not isinstance(baseline_params, Mapping) or not isinstance(
        widened_params, Mapping
    ):
        raise ValueError("prior_sensitivity configs are missing params")
    for requested in ("w0", "wa"):
        key = "w" if requested == "w0" and "w" in baseline_params else requested
        baseline_spec = baseline_params.get(key)
        widened_spec = widened_params.get(key)
        baseline_prior = (
            baseline_spec.get("prior") if isinstance(baseline_spec, Mapping) else None
        )
        widened_prior = (
            widened_spec.get("prior") if isinstance(widened_spec, Mapping) else None
        )
        if not isinstance(baseline_prior, Mapping) or not isinstance(
            widened_prior, Mapping
        ):
            raise ValueError(f"prior_sensitivity config is missing {requested} prior")
        baseline_min = _finite_number(
            baseline_prior.get("min"), f"baseline {requested} min"
        )
        baseline_max = _finite_number(
            baseline_prior.get("max"), f"baseline {requested} max"
        )
        widened_min = _finite_number(
            widened_prior.get("min"), f"widened {requested} min"
        )
        widened_max = _finite_number(
            widened_prior.get("max"), f"widened {requested} max"
        )
        if not (
            widened_min <= baseline_min
            and widened_max >= baseline_max
            and (widened_min < baseline_min or widened_max > baseline_max)
        ):
            raise ValueError(f"prior_sensitivity did not widen {requested} prior")


def _normalize_support_paths(
    value: Any,
    *,
    run_id: str,
    execution_fingerprint: str,
    numbers: Sequence[Mapping[str, Any]],
    allow_evidence_ids: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValueError("claim_support_paths are required")
    numbers_by_name = {str(item["name"]): item for item in numbers}
    covered: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError("claim support path must be a mapping")
        artifact = _normalize_artifact(raw, f"claim_support_paths[{index}]")
        payload = _load_json_artifact(artifact, f"claim_support_paths[{index}]")
        if payload.get("schema_version") != 1:
            raise ValueError("claim support artifact schema is invalid")
        if payload.get("kind") != "research_alpha_claim_support":
            raise ValueError("claim support artifact kind is invalid")
        _validate_self_hash(payload, "claim support artifact")
        if payload.get("run_id") != run_id:
            raise ValueError("claim support artifact run_id mismatch")
        if payload.get("execution_fingerprint") != execution_fingerprint:
            raise ValueError("claim support artifact execution_fingerprint mismatch")
        parameter = _required_text(raw.get("parameter"), "claim support parameter")
        if parameter not in numbers_by_name:
            raise ValueError(f"claim support path references unknown parameter {parameter}")
        result_path = _required_text(raw.get("result_path"), "claim support result_path")
        observed = _resolve_json_path(payload, result_path)
        expected = {
            field: numbers_by_name[parameter][field] for field in _INTERVAL_FIELDS
        }
        if not isinstance(observed, Mapping) or any(
            _float_or_none(observed.get(field)) != expected[field]
            for field in _INTERVAL_FIELDS
        ):
            raise ValueError(f"claim support result is misaligned for {parameter}")
        record = {
            "claim": _required_text(raw.get("claim"), "claim support claim"),
            "parameter": parameter,
            "result_path": result_path,
            "artifact_path": artifact["path"],
            "artifact_hash": artifact["sha256"],
            "artifact_size_bytes": artifact["size_bytes"],
        }
        if allow_evidence_ids:
            _require_sha256(raw.get("evidence_id"), "claim support evidence_id")
            record["evidence_id"] = raw["evidence_id"]
        normalized.append(record)
        covered.add(parameter)
    if covered != set(numbers_by_name):
        missing = sorted(set(numbers_by_name) - covered)
        raise ValueError("claim support paths missing for: " + ", ".join(missing))
    return normalized


def _adequacy_subject(
    *,
    run_id: str,
    execution_fingerprint: str,
    target_hash: str,
    chain_artifacts: Sequence[Mapping[str, Any]],
    datasets: Sequence[str],
    models: Sequence[str],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "execution_fingerprint": execution_fingerprint,
        "target_hash": target_hash,
        "chain_artifacts": list(chain_artifacts),
        "datasets": list(datasets),
        "models": list(models),
    }


def _validate_adequacy_manifest(
    manifest: Mapping[str, Any],
    *,
    run_id: str,
    execution_fingerprint: str,
    target_hash: str,
    chain_artifacts: Sequence[Mapping[str, Any]],
    primary_run_attestation_artifact: Mapping[str, Any],
    primary_numbers: Sequence[Mapping[str, Any]],
    sampled_parameters: Sequence[str],
    reasons: list[str],
) -> None:
    adequacy = manifest.get("model_adequacy")
    expected_subject = _adequacy_subject(
        run_id=run_id,
        execution_fingerprint=execution_fingerprint,
        target_hash=target_hash,
        chain_artifacts=chain_artifacts,
        datasets=manifest.get("datasets") or [],
        models=manifest.get("models") or [],
    )
    assessment = _assess_model_adequacy(
        dict(adequacy) if isinstance(adequacy, Mapping) else None,
        expected_subject=expected_subject,
    )
    if assessment["eligible"] is not True:
        reasons.extend(f"model_adequacy:{item}" for item in assessment["reasons"])
    checks = adequacy.get("checks") if isinstance(adequacy, Mapping) else None
    raw_records: dict[str, dict[str, Any]] = {}
    if isinstance(checks, Mapping):
        for name, record in checks.items():
            evidence = record.get("evidence") if isinstance(record, Mapping) else None
            if isinstance(evidence, Mapping):
                raw_records[str(name)] = {
                    "artifact_path": evidence.get("artifact_path"),
                    "artifact_hash": evidence.get("artifact_hash"),
                }
            if not isinstance(record, Mapping) or record.get("evidence_id") not in (
                manifest.get("evidence_ids") or []
            ):
                reasons.append(f"model_adequacy:{name}_evidence_unlisted")
    try:
        normalized = _normalize_adequacy_evidence(
            raw_records,
            run_id=run_id,
            execution_fingerprint=execution_fingerprint,
            primary_chain_artifacts=chain_artifacts,
            primary_run_attestation_artifact=primary_run_attestation_artifact,
            primary_environment_fingerprint=str(
                (manifest.get("diagnostics") or {}).get("metrics", {}).get(
                    "environment_fingerprint"
                )
            ),
            primary_numbers=primary_numbers,
            sampled_parameters=sampled_parameters,
            profile_id=_normalize_research_profile(manifest.get("profile_id")),
        )
        for name, evidence in normalized.items():
            stored = checks[name]["evidence"]
            for key in (
                "run_id",
                "execution_fingerprint",
                "metrics",
                "artifact_path",
                "artifact_hash",
            ):
                if stored.get(key) != evidence.get(key):
                    reasons.append(f"model_adequacy:{name}_{key}_mismatch")
    except ValueError as exc:
        reasons.append(_reason_from_error(exc))
    gate = manifest.get("publication_gate")
    if not isinstance(gate, Mapping):
        reasons.append("publication_gate_missing")
    elif not isinstance(gate.get("model_adequacy"), Mapping) or gate[
        "model_adequacy"
    ].get("manifest_hash") != assessment.get("manifest_hash"):
        reasons.append("publication_gate_adequacy_mismatch")


def _result_evidence(
    *,
    run_id: str,
    run_fingerprint: str,
    number: Mapping[str, Any],
    support: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "research_alpha_parameter_interval",
        "run_id": run_id,
        "run_fingerprint": run_fingerprint,
        "parameter": number["name"],
        "statistics": {field: number[field] for field in _INTERVAL_FIELDS},
        "support_artifact": {
            "path": support["artifact_path"],
            "sha256": support["artifact_hash"],
            "result_path": support["result_path"],
        },
    }


def _validate_bound_result_evidence(
    manifest: Mapping[str, Any],
    *,
    run_id: str,
    run_fingerprint: str,
    numbers: Sequence[Mapping[str, Any]],
    support_paths: Sequence[Mapping[str, Any]],
    reasons: list[str],
) -> None:
    evidence_ids = manifest.get("evidence_ids")
    evidence_ids = evidence_ids if isinstance(evidence_ids, list) else []
    observed_numbers = manifest.get("numbers")
    observed_numbers = observed_numbers if isinstance(observed_numbers, Mapping) else {}
    support_by_name = {item["parameter"]: item for item in support_paths}
    for number in numbers:
        name = str(number["name"])
        expected_id = scientific_content_hash(
            _result_evidence(
                run_id=run_id,
                run_fingerprint=run_fingerprint,
                number=number,
                support=support_by_name[name],
            )
        )
        observed = observed_numbers.get(name)
        if not isinstance(observed, Mapping) or observed.get("evidence_id") != expected_id:
            reasons.append(f"result_evidence_mismatch:{name}")
        if expected_id not in evidence_ids:
            reasons.append(f"result_evidence_unlisted:{name}")
        if support_by_name[name].get("evidence_id") != expected_id:
            reasons.append(f"claim_support_evidence_mismatch:{name}")
    diagnostics = manifest.get("diagnostics")
    if isinstance(diagnostics, Mapping):
        evidence = {
            "kind": "research_alpha_chain_diagnostics",
            "run_id": run_id,
            "run_fingerprint": run_fingerprint,
            "chain_artifacts": manifest["artifacts"]["chains"],
            "sampled_parameters_artifact": manifest["artifacts"]["sampled_parameters"],
            "status": "passed",
            "metrics": diagnostics.get("metrics"),
        }
        expected_id = scientific_content_hash(evidence)
        if diagnostics.get("evidence_id") != expected_id or diagnostics.get(
            "evidence_hash"
        ) != expected_id:
            reasons.append("diagnostics_evidence_hash_mismatch")
        if expected_id not in evidence_ids:
            reasons.append("diagnostics_evidence_unlisted")
    else:
        reasons.append("diagnostics_evidence_missing")


def _normalize_external_review(
    value: Mapping[str, Any] | None,
    *,
    run_id: str,
    run_fingerprint: str,
    target_hash: str,
    profile_id: str,
) -> dict[str, Any]:
    if value is None:
        return {"status": "pending_external_review"}
    if not isinstance(value, Mapping):
        raise ValueError("external review attestation must be a mapping")
    if profile_id == EXACT_PROFILE_ID and not (
        TRUSTED_EXTERNAL_REVIEW_AUTHORITY_REGISTRY
    ):
        raise ValueError(
            "exact external-review authority registry is empty; strict A is disabled"
        )
    key_path_text = os.environ.get(EXTERNAL_REVIEW_PUBLIC_KEY_ENV)
    claimed_authority_id = str(value.get("authority_id") or "")
    frozen_key_hash = (
        TRUSTED_EXTERNAL_REVIEW_AUTHORITY_REGISTRY.get(claimed_authority_id)
        if profile_id == EXACT_PROFILE_ID
        else None
    )
    authority_id = (
        claimed_authority_id
        if profile_id == EXACT_PROFILE_ID
        else os.environ.get(EXTERNAL_REVIEW_AUTHORITY_ENV)
    )
    if not key_path_text or not authority_id:
        raise ValueError("independent external-review authority is not configured")
    key_artifact = _normalize_artifact(
        {"path": key_path_text, "sha256": _hash_file(Path(key_path_text))},
        "external_review.public_key",
    )
    if value.get("schema_version") != EXTERNAL_REVIEW_SCHEMA_VERSION:
        raise ValueError("external review schema is invalid")
    if value.get("algorithm") != "ed25519":
        raise ValueError("external review algorithm is not ed25519")
    if value.get("authority_id") != authority_id:
        raise ValueError("external review authority_id mismatch")
    if profile_id == EXACT_PROFILE_ID and frozen_key_hash is None:
        raise ValueError("external review authority is not preregistered")
    if value.get("authority_key_sha256") != key_artifact["sha256"]:
        raise ValueError("external review authority key hash mismatch")
    if profile_id == EXACT_PROFILE_ID and key_artifact["sha256"] != frozen_key_hash:
        raise ValueError("external review authority key is not frozen")
    if value.get("status") != "approved":
        raise ValueError("external review status is not approved")
    if value.get("run_id") != run_id or value.get("run_fingerprint") != run_fingerprint:
        raise ValueError("external review run binding mismatch")
    if value.get("target_hash") != target_hash:
        raise ValueError("external review target_hash mismatch")
    _required_text(value.get("reviewer"), "external review reviewer")
    _validate_review_timestamp(value.get("reviewed_at"))
    report = _normalize_artifact(value.get("report_artifact"), "external_review.report")
    supplied_report = value.get("report_artifact")
    canonical_value = {
        **{key: item for key, item in value.items() if key != "report_artifact"},
        "report_artifact": report,
    }
    if not isinstance(supplied_report, Mapping) or any(
        supplied_report.get(key) != report[key] for key in ("path", "sha256", "size_bytes")
    ):
        raise ValueError("external review report artifact is noncanonical")
    signature = value.get("signature")
    if not isinstance(signature, str):
        raise ValueError("external review signature is missing")
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("external review signature is not valid base64") from exc
    key = _load_ed25519_public_key(Path(key_artifact["path"]))
    try:
        key.verify(signature_bytes, external_review_signing_bytes(canonical_value))
    except InvalidSignature as exc:
        raise ValueError("external review Ed25519 signature is invalid") from exc
    return {
        "status": "approved",
        "attestation": canonical_value,
        "verification": {
            "independent_key_verified": True,
            "authority_id": authority_id,
            "public_key_artifact": key_artifact,
        },
    }


def _validate_external_review(
    value: Any,
    *,
    run_id: str,
    run_fingerprint: str,
    target_hash: str,
    profile_id: str,
    reasons: list[str],
) -> None:
    if not isinstance(value, Mapping):
        reasons.append("external_review_status_missing")
        return
    if value.get("status") == "pending_external_review":
        if set(value) != {"status"}:
            reasons.append("pending_external_review_has_untrusted_fields")
        return
    try:
        normalized = _normalize_external_review(
            value.get("attestation"),
            run_id=run_id,
            run_fingerprint=run_fingerprint,
            target_hash=target_hash,
            profile_id=profile_id,
        )
        if dict(value) != normalized:
            reasons.append("external_review_noncanonical")
    except ValueError as exc:
        reasons.append(_reason_from_error(exc))


def _validate_review_timestamp(value: Any) -> None:
    text = _required_text(value, "external review reviewed_at")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("external review reviewed_at is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("external review reviewed_at must include a timezone")
    if parsed.astimezone(timezone.utc) > datetime.now(timezone.utc):
        raise ValueError("external review reviewed_at is in the future")


def _load_ed25519_public_key(path: Path) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(path.read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("external review public key is unreadable") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("external review public key is not Ed25519")
    return key


def _load_json_artifact(artifact: Mapping[str, Any], field: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(str(artifact["path"])).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} is not a readable JSON artifact") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{field} JSON must be a mapping")
    return payload


def _load_yaml_artifact(artifact: Mapping[str, Any], field: str) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(
            Path(str(artifact["path"])).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"{field} is not readable YAML") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{field} YAML must be a mapping")
    return payload


def _validate_self_hash(payload: Mapping[str, Any], field: str) -> None:
    supplied = payload.get("self_hash")
    _require_sha256(supplied, f"{field}.self_hash")
    expected = scientific_content_hash(
        {key: value for key, value in payload.items() if key != "self_hash"}
    )
    if supplied != expected:
        raise ValueError(f"{field} self_hash mismatch")


def _validate_named_self_hash(
    payload: Mapping[str, Any],
    *,
    field: str,
    candidates: Sequence[str],
) -> str:
    for name in candidates:
        supplied = payload.get(name)
        if supplied is None:
            continue
        _require_sha256(supplied, f"{field}.{name}")
        expected = scientific_content_hash(
            {key: value for key, value in payload.items() if key != name}
        )
        if supplied != expected:
            raise ValueError(f"{field} {name} mismatch")
        return name
    raise ValueError(f"{field} self hash is missing")


def _resolve_json_path(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not part or not isinstance(current, Mapping) or part not in current:
            raise ValueError(f"claim support result_path does not resolve: {path}")
        current = current[part]
    return current


def _hash_file(path: Path) -> str:
    if not path.expanduser().is_file():
        raise ValueError("artifact path is not an existing file")
    digest = hashlib.sha256()
    with path.expanduser().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _required_unique_texts(value: Any, field: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValueError(f"{field} must be a non-empty sequence")
    result = [_required_text(item, field) for item in value]
    if len(set(result)) != len(result):
        raise ValueError(f"{field} values must be unique")
    return result


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if isinstance(value, float) and value != number:
        raise ValueError(f"{field} must be an integer")
    return number


def _require_sha256(value: Any, field: str) -> None:
    if not _is_sha256(value):
        raise ValueError(f"{field} must be a full sha256 identifier")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _first_present(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _reason_from_error(exc: ValueError) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(exc).lower()).strip("_")
