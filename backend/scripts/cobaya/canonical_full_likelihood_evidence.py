#!/usr/bin/env python3
"""Fail-closed evidence pipeline for the canonical full-likelihood w0wa run.

This module deliberately keeps configuration generation, execution attestation,
chain diagnostics, MAP comparison, and manifest construction in one auditable
place. It never emits posterior intervals before four independent chains pass
rank-normalized R-hat < 1.01 and bulk ESS >= 400 for every sampled parameter.
It never emits a model-comparison delta chi2 unless both Cobaya minimizations
completed successfully against identical likelihood, nuisance, and data
fingerprints. A paired posterior-mode delta remains descriptive: this workflow
withholds Wilks p-values and Gaussian-equivalent significances unless a future
revision proves likelihood-only MLE targets and the required calibration.

Run from ``backend/``. See ``README_full_cmb_reproduction.md`` for commands.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import yaml


SCHEMA_VERSION = 1
REQUIRED_CHAIN_COUNT = 4
RANK_RHAT_MAX_EXCLUSIVE = 1.01
BULK_ESS_MIN = 400.0
DEFAULT_BURN_FRACTION = 0.30
MAX_EXPANDED_DRAWS_PER_CHAIN = 10_000_000
REPORT_PARAMETERS = ("w", "wa", "omegam", "H0")
MODEL_PARAMETERS = {"w", "wa"}

REQUIRED_LIKELIHOODS = (
    "planck_2018_lowl.TT",
    "planck_2018_lowl.EE_sroll2",
    "planck_2018_highl_CamSpec2021.TTTEEE",
    "planck_2018_lensing.native",
    "bao.desi_2024_bao_all",
    "sn.pantheonplus",
)

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

# Exact data products consumed by the declared canonical stack. Patterns are
# relative to Cobaya's packages directory. Hashing the large CamSpec covariance
# is intentional: a version string alone is not a byte-level data certificate.
CANONICAL_DATA_ASSETS: dict[str, tuple[str, ...]] = {
    "planck_2018_lowl.TT": (
        "data/planck_2018_lowT_native/version.dat",
        "data/planck_2018_lowT_native/mu.txt",
        "data/planck_2018_lowT_native/mu_sigma.txt",
        "data/planck_2018_lowT_native/cov.txt",
        "data/planck_2018_lowT_native/cl2x_1.txt",
        "data/planck_2018_lowT_native/cl2x_2.txt",
    ),
    "planck_2018_lowl.EE_sroll2": (
        "data/planck_sroll2_lowE_native/version.dat",
        "data/planck_sroll2_lowE_native/sroll2_prob_table.txt",
    ),
    "planck_2018_highl_CamSpec2021.TTTEEE": (
        "data/planck_2018_CamSpec2021/version.dat",
        "data/planck_2018_CamSpec2021/CamSpec2021/CamSpecHM_12_6_cl.dataset",
        "data/planck_2018_CamSpec2021/CamSpec2021/CamSpecHM.paramnames",
        "data/planck_2018_CamSpec2021/CamSpec2021/like_12.6HMcleaned_unified_data_ranges.txt",
        "data/planck_2018_CamSpec2021/CamSpec2021/like_12.6HMcleaned_unified_spectra.txt",
        "data/planck_2018_CamSpec2021/CamSpec2021/like_12.6HMcleaned_unified_cov.bin",
        "data/planck_2018_CamSpec2021/CamSpec2021/100x100_10.5_dust.dat",
        "data/planck_2018_CamSpec2021/CamSpec2021/143x143_10.5_dust.dat",
        "data/planck_2018_CamSpec2021/CamSpec2021/217x217_10.5_dust.dat",
        "data/planck_2018_CamSpec2021/CamSpec2021/143x217_10.5_dust.dat",
        "data/planck_2018_CamSpec2021/CamSpec2021/cib217.txt",
        "data/planck_2018_CamSpec2021/CamSpec2021/tsz_143_eps0.50.dat",
        "data/planck_2018_CamSpec2021/CamSpec2021/sz_x_cib_template.dat",
        "data/planck_2018_CamSpec2021/CamSpec2021/cl_ksz_148_trac.dat",
    ),
    "planck_2018_lensing.native": (
        "data/planck_supp_data_and_covmats/version.dat",
        "data/planck_supp_data_and_covmats/lensing/2018/planck_calib.paramnames",
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8.dataset",
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8_bandpowers.dat",
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8_cov.dat",
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8_lensing_fiducial_correction.dat",
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8_window/window*.dat",
        "data/planck_supp_data_and_covmats/lensing/2018/smicadx12_Dec5_ftl_mv2_ndclpp_p_teb_consext8_lens_delta_window/window*.dat",
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

    mcmc = (config.get("sampler") or {}).get("mcmc")
    if not isinstance(mcmc, Mapping):
        reasons.append("mcmc_sampler_missing")
    else:
        if float(mcmc.get("Rminus1_stop", math.inf)) > 0.01:
            reasons.append("Rminus1_stop_not_strict")
        if float(mcmc.get("Rminus1_cl_stop", math.inf)) > 0.10:
            reasons.append("Rminus1_cl_stop_not_strict")

    camb_args = ((config.get("theory") or {}).get("camb") or {}).get("extra_args") or {}
    if camb_args.get("dark_energy_model") != "ppf":
        reasons.append("camb_dark_energy_model_not_ppf")

    params = config.get("params") or {}
    for name in ("w", "wa"):
        spec = params.get(name)
        if not isinstance(spec, Mapping) or not isinstance(spec.get("prior"), Mapping):
            reasons.append(f"free_{name}_prior_missing")
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


def environment_manifest() -> dict[str, Any]:
    versions: dict[str, str | None] = {}
    for distribution in (
        "cobaya",
        "camb",
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
    return {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": versions,
        "thread_environment": tracked_env,
    }


def _environment_fingerprint(environment: Mapping[str, Any]) -> str:
    return _hash_object(
        {
            "python": environment.get("python"),
            "platform": environment.get("platform"),
            "machine": environment.get("machine"),
            "packages": environment.get("packages"),
            "thread_environment": environment.get("thread_environment"),
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


def write_completed_attestation(
    *,
    kind: str,
    config_path: str | Path,
    prefix: str | Path,
    data_inventory: Mapping[str, Any],
    returncode: int = 0,
    command: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Write a completion certificate after a successful external run.

    This is also used by fast synthetic tests. Production users should normally
    let the ``run`` subcommand create it atomically around the Cobaya process.
    """

    paths = _run_artifact_paths(kind, prefix)
    missing = [str(path) for path in paths if not path.is_file()]
    success = returncode == 0 and not missing and data_inventory.get("complete") is True
    environment = environment_manifest()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "status": "completed" if success else "failed",
        "success": success,
        "returncode": int(returncode),
        "started_at": None,
        "completed_at": _utc_now(),
        "command": list(command or []),
        "config_path": str(Path(config_path).resolve()),
        "config_sha256": _hash_file(config_path),
        "data_fingerprint": data_inventory.get("fingerprint"),
        "environment": environment,
        "environment_fingerprint": _environment_fingerprint(environment),
        "artifacts": _artifact_records(paths),
        "missing_artifacts": missing,
    }
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
    if payload.get("kind") != kind:
        reasons.append("run_attestation_kind_mismatch")
    if payload.get("status") != "completed" or payload.get("success") is not True:
        reasons.append("run_not_successfully_completed")
    if payload.get("returncode") != 0:
        reasons.append("run_returncode_nonzero")
    if payload.get("config_sha256") != _hash_file(config_path):
        reasons.append("run_config_hash_mismatch")
    if payload.get("data_fingerprint") != expected_data_fingerprint:
        reasons.append("run_data_fingerprint_mismatch")
    environment = payload.get("environment")
    if not isinstance(environment, Mapping) or payload.get(
        "environment_fingerprint"
    ) != _environment_fingerprint(environment or {}):
        reasons.append("run_environment_fingerprint_invalid")

    expected_paths = {
        str(path.resolve()): path for path in _run_artifact_paths(kind, prefix)
    }
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
    cobaya_run: str,
    mpi_processes: int,
    force: bool,
) -> int:
    if kind == "chain" and mpi_processes != REQUIRED_CHAIN_COUNT:
        raise ValueError("The canonical chain run requires exactly four MPI processes")
    config = _load_yaml(config_path)
    config_reasons: list[str] = []
    if kind == "chain":
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

    command: list[str] = []
    if mpi_processes > 1:
        command.extend(["mpirun", "-n", str(mpi_processes), "--bind-to", "none"])
    command.extend(
        [
            cobaya_run,
            str(config_path),
            "-p",
            str(packages_path),
            "-o",
            str(prefix),
        ]
    )
    if force:
        command.append("--force")

    started_at = _utc_now()
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
        "config_path": str(Path(config_path).resolve()),
        "config_sha256": _hash_file(config_path),
        "data_fingerprint": data_inventory["fingerprint"],
        "environment": environment,
        "environment_fingerprint": _environment_fingerprint(environment),
        "artifacts": [],
    }
    _write_json(_attestation_path(prefix), running)

    log_path = _prefix_file(prefix, ".runner.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_handle:
        log_handle.write("command: " + shlex.join(command) + "\n")
        log_handle.flush()
        try:
            completed = subprocess.run(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                check=False,
                text=True,
            )
        except OSError as exc:
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

    attestation = write_completed_attestation(
        kind=kind,
        config_path=config_path,
        prefix=prefix,
        data_inventory=data_inventory,
        returncode=completed.returncode,
        command=command,
    )
    attestation["started_at"] = started_at
    attestation["runner_log"] = {
        "path": str(log_path.resolve()),
        "sha256": _hash_file(log_path),
    }
    _write_json(_attestation_path(prefix), attestation)
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


def diagnose_chains(
    chain_prefix: str | Path,
    *,
    updated_config: Mapping[str, Any],
    burn_fraction: float = DEFAULT_BURN_FRACTION,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
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
    headers: list[list[str]] = []
    raw_rows: list[int] = []
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
            total_draws = int(np.sum(rounded))
            if total_draws > MAX_EXPANDED_DRAWS_PER_CHAIN:
                raise ValueError(f"expanded chain exceeds safety limit in {path}")
            row_index = np.repeat(np.arange(data.shape[0]), rounded.astype(int))
            burn = int(math.floor(total_draws * burn_fraction))
            row_index = row_index[burn:]
            if row_index.size < 4:
                raise ValueError(f"too few post-burn-in draws in {path}")
            expanded_draws.append(int(row_index.size))
            needed = list(dict.fromkeys([*sampled, *REPORT_PARAMETERS]))
            columns = {
                name: data[row_index, header.index(name)]
                for name in needed
                if name in header
            }
            per_chain.append(columns)
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

    idata = az.from_dict(
        posterior={name: values for name, values in aligned.items() if name in sampled}
    )
    rhat_ds = az.rhat(idata, method="rank")
    ess_ds = az.ess(idata, method="bulk")
    parameter_diagnostics: dict[str, Any] = {}
    gate_reasons: list[str] = []
    for name in sampled:
        rhat = float(np.asarray(rhat_ds[name]).reshape(-1)[0])
        ess_bulk = float(np.asarray(ess_ds[name]).reshape(-1)[0])
        failures: list[str] = []
        if not math.isfinite(rhat):
            failures.append("rank_normalized_rhat_unavailable")
        elif rhat >= RANK_RHAT_MAX_EXCLUSIVE:
            failures.append("rank_normalized_rhat_at_or_above_1.01")
        if not math.isfinite(ess_bulk):
            failures.append("bulk_ess_unavailable")
        elif ess_bulk < BULK_ESS_MIN:
            failures.append("bulk_ess_below_400")
        if failures:
            gate_reasons.extend(f"{name}:{failure}" for failure in failures)
        parameter_diagnostics[name] = {
            "rank_normalized_rhat": rhat,
            "bulk_ess": ess_bulk,
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
            "alignment": "most_recent_draws_truncated_to_shortest_chain",
            "weights": "integer_cobaya_weights_expanded_before_diagnostics",
        },
        "n_chains": REQUIRED_CHAIN_COUNT,
        "raw_rows_per_chain": raw_rows,
        "post_burn_draws_per_chain": expanded_draws,
        "aligned_draws_per_chain": aligned_count,
        "sampled_parameters": sampled,
        "parameters": parameter_diagnostics,
        "chain_files": [
            {"path": str(path), "sha256": digest} for path, digest in zip(paths, hashes)
        ],
    }
    return diagnostics, aligned


def posterior_intervals(
    aligned: Mapping[str, np.ndarray],
    parameters: Sequence[str] = REPORT_PARAMETERS,
) -> dict[str, Any]:
    intervals: dict[str, Any] = {}
    for name in parameters:
        values = np.asarray(aligned[name], dtype=float).reshape(-1)
        q16, q50, q84 = np.quantile(values, [0.16, 0.50, 0.84])
        intervals[name] = {
            "mean": float(np.mean(values)),
            "median": float(q50),
            "lower_68": float(q16),
            "upper_68": float(q84),
            "minus": float(q50 - q16),
            "plus": float(q84 - q50),
        }
    return intervals


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
        "significance_ready": bool(map_comparison.get("significance_ready")),
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
    manifest["manifest_sha256"] = _hash_object(manifest)
    return manifest


def _default_paths() -> dict[str, Path]:
    script_dir = Path(__file__).resolve().parent
    return {
        "canonical": script_dir / "w0wa_desi_sn_planck.yaml",
        "free_map_config": script_dir / "w0wa_desi_sn_planck_map.yaml",
        "fixed_map_config": script_dir / "lcdm_desi_sn_planck_map.yaml",
    }


def _build_parser() -> argparse.ArgumentParser:
    defaults = _default_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="derive the paired MAP configs")
    generate.add_argument("--canonical", type=Path, default=defaults["canonical"])
    generate.add_argument(
        "--free-output", type=Path, default=defaults["free_map_config"]
    )
    generate.add_argument(
        "--fixed-output", type=Path, default=defaults["fixed_map_config"]
    )

    run = subparsers.add_parser(
        "run", help="run Cobaya and write a completion attestation"
    )
    run.add_argument("--kind", choices=("chain", "map"), required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--prefix", type=Path, required=True)
    run.add_argument("--packages-path", type=Path, default=Path("packages"))
    run.add_argument("--cobaya-run", default="venv/bin/cobaya-run")
    run.add_argument("--mpi", type=int, default=4)
    run.add_argument("--force", action="store_true")

    analyze = subparsers.add_parser(
        "analyze", help="build the fail-closed evidence manifest"
    )
    analyze.add_argument("--canonical", type=Path, default=defaults["canonical"])
    analyze.add_argument("--chain-prefix", type=Path, default=Path("cobaya_runs/w0wa"))
    analyze.add_argument(
        "--free-map-config", type=Path, default=defaults["free_map_config"]
    )
    analyze.add_argument(
        "--fixed-map-config", type=Path, default=defaults["fixed_map_config"]
    )
    analyze.add_argument(
        "--free-map-prefix", type=Path, default=Path("cobaya_runs/w0wa_free_map")
    )
    analyze.add_argument(
        "--fixed-map-prefix", type=Path, default=Path("cobaya_runs/lcdm_fixed_map")
    )
    analyze.add_argument("--packages-path", type=Path, default=Path("packages"))
    analyze.add_argument("--burn-fraction", type=float, default=DEFAULT_BURN_FRACTION)
    analyze.add_argument(
        "--output",
        type=Path,
        default=Path("cobaya_runs/w0wa_evidence_manifest.json"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "generate":
        result = write_map_configs(args.canonical, args.free_output, args.fixed_output)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "run":
        return run_cobaya_with_attestation(
            kind=args.kind,
            config_path=args.config,
            prefix=args.prefix,
            packages_path=args.packages_path,
            cobaya_run=args.cobaya_run,
            mpi_processes=args.mpi,
            force=args.force,
        )
    if args.command == "analyze":
        manifest = build_evidence_manifest(
            canonical_config_path=args.canonical,
            chain_prefix=args.chain_prefix,
            free_map_config_path=args.free_map_config,
            fixed_map_config_path=args.fixed_map_config,
            free_map_prefix=args.free_map_prefix,
            fixed_map_prefix=args.fixed_map_prefix,
            packages_path=args.packages_path,
            burn_fraction=args.burn_fraction,
        )
        _write_json(args.output, manifest)
        print(f"evidence status: {manifest['status']}")
        print(f"manifest: {args.output}")
        print(
            f"posterior gate: {'PASS' if manifest['posterior']['passed'] else 'FAIL'}"
        )
        if manifest["posterior"]["passed"]:
            for name, interval in manifest["posterior"]["intervals_68"].items():
                print(
                    f"  {name}: {interval['median']:.6g} "
                    f"(-{interval['minus']:.3g}/+{interval['plus']:.3g})"
                )
        print(f"MAP gate: {'PASS' if manifest['map_comparison']['passed'] else 'FAIL'}")
        if manifest["map_comparison"]["passed"]:
            print(
                "  descriptive likelihood delta chi2 at optimized points="
                f"{manifest['map_comparison']['delta_chi2']:.6g}"
            )
            print(
                "  p-value/significance withheld: "
                f"{manifest['map_comparison']['significance_withheld_reason']}"
            )
        return 0 if manifest["publication_ready"] else 2
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
