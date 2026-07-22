#!/usr/bin/env python3
"""Independent chain postprocessor for the isolated DESI w0wa reproduction.

This implementation intentionally does not import the canonical evidence module.
It independently reads Cobaya weights, removes burn-in using GetDist's raw-row
convention, hashes every chain, computes rank-normalized R-hat, bulk ESS, MCSE
and the DESI reporting intervals, and writes a self-hashed report for the
independent-reproduction adequacy check.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.machinery
import importlib.metadata
import importlib.util
import io
import json
import math
import os
import re
import site
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import arviz as az
import numpy as np
import yaml
from getdist.mcsamples import MCSamples


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.w0wa_exact_contract import (  # noqa: E402
    EXACT_CLAIM_SCOPE,
    EXACT_PROFILE_ID,
    FROZEN_BOOTSTRAP_DISTRIBUTIONS,
    GENERATED_BYTECODE_CACHE_POLICY,
    PREREGISTERED_PAPER_UNCERTAINTIES,
    PREREGISTERED_TARGET_COMMITMENT,
    PROTOCOL_STATUS,
    TRUSTED_DEPENDENCY_LOCK_SHA256,
)


REPORT_PARAMETERS = ("w", "wa", "omegam", "H0")
RANK_RHAT_MAX_EXCLUSIVE = 1.01
BULK_ESS_MIN = 1_000.0
MAX_EXPANDED_DRAWS_PER_CHAIN = 10_000_000
MIN_DIAGNOSTIC_ALIGNMENT_FRACTION = 0.90
MCSE_MAX_REFERENCE_SIGMA_EXCLUSIVE = 0.05
FROZEN_FORMAL_BURN_FRACTION = 0.30
EXACT_DISTRIBUTION_COUNT = 52
REPORT_TO_PAPER_PARAMETER = {
    "w": "w0",
    "wa": "wa",
    "omegam": "Omega_m",
    "H0": "H0",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _hash_object(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _hash_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


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
    """Reproduce the canonical run-attestation runtime fingerprint."""

    return _hash_object(
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


def _current_python_isolation() -> dict[str, Any]:
    pythonpath = os.environ.get("PYTHONPATH")
    payload = {
        "isolated_interpreter": bool(sys.flags.isolated),
        "ignore_environment": bool(sys.flags.ignore_environment),
        "no_user_site": bool(sys.flags.no_user_site),
        "safe_path": bool(sys.flags.safe_path),
        "pythonpath_empty": not bool(pythonpath),
    }
    reasons = [name for name, passed in payload.items() if passed is not True]
    return {**payload, "passed": not reasons, "reasons": reasons}


def _verify_current_import_policy(recorded: Any) -> dict[str, Any]:
    """Recheck the current ``-I`` flags and every preflight startup hook."""

    if not isinstance(recorded, Mapping):
        raise ValueError("isolated preflight import policy is missing")
    unsigned = {
        key: value
        for key, value in recorded.items()
        if key not in {"passed", "reasons", "fingerprint"}
    }
    if (
        recorded.get("passed") is not True
        or recorded.get("reasons") != []
        or recorded.get("fingerprint") != _hash_object(unsigned)
        or recorded.get("python_flag") != "-I"
    ):
        raise ValueError("isolated preflight import policy is not a trusted PASS")

    isolation = _current_python_isolation()
    if isolation.get("passed") is not True:
        raise ValueError(
            "independent postprocessor requires python -I and an empty PYTHONPATH"
        )
    for field in (
        "isolated_interpreter",
        "ignore_environment",
        "no_user_site",
        "safe_path",
        "pythonpath_empty",
    ):
        if recorded.get(field) is not True or isolation.get(field) is not True:
            raise ValueError(f"independent postprocessor import flag mismatch: {field}")

    venv_root = Path(sys.prefix).resolve()
    current_roots = sorted(
        {
            Path(raw).resolve()
            for raw in site.getsitepackages()
            if Path(raw).is_dir() and Path(raw).resolve().is_relative_to(venv_root)
        },
        key=str,
    )
    if recorded.get("venv_root") != str(venv_root) or recorded.get(
        "site_package_roots"
    ) != [str(path) for path in current_roots]:
        raise ValueError("independent postprocessor site-package roots drifted")
    current_hooks = sorted(
        {
            path.resolve()
            for root in current_roots
            for pattern in ("*.pth", "sitecustomize.py", "usercustomize.py")
            for path in root.glob(pattern)
            if path.is_file()
        },
        key=str,
    )
    raw_hooks = recorded.get("startup_hooks")
    if not isinstance(raw_hooks, Sequence) or isinstance(raw_hooks, (str, bytes)):
        raise ValueError("isolated preflight startup-hook records are missing")
    by_path = {
        str(item.get("path")): item
        for item in raw_hooks
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    if set(by_path) != {str(path) for path in current_hooks}:
        raise ValueError("independent postprocessor startup-hook closure drifted")
    verified_hooks: list[dict[str, Any]] = []
    for path in current_hooks:
        record = by_path[str(path)]
        owners = record.get("owners")
        if (
            record.get("trusted_owner") is not True
            or not isinstance(owners, Sequence)
            or isinstance(owners, (str, bytes))
            or not owners
            or record.get("external_paths") != []
            or record.get("size_bytes") != path.stat().st_size
            or record.get("sha256") != _hash_file(path)
        ):
            raise ValueError(
                f"independent postprocessor startup hook is untrusted: {path.name}"
            )
        verified_hooks.append(
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": _hash_file(path),
            }
        )
    return {
        "schema_version": 1,
        **isolation,
        "preflight_import_policy_fingerprint": recorded["fingerprint"],
        "startup_hook_fingerprint": _hash_object(verified_hooks),
        "verified": True,
    }


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("updated config must be a mapping")
    return payload


def _distribution_inventory(name: str) -> dict[str, Any]:
    """Independently re-hash one installed distribution from RECORD metadata."""

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
    files: list[dict[str, Any]] = []
    for relative in distribution.files or []:
        path = Path(distribution.locate_file(relative))
        if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        files.append(
            {
                "path": str(relative),
                "size_bytes": path.stat().st_size,
                "sha256": _hash_file(path),
            }
        )
    files.sort(key=lambda item: item["path"])
    return {
        "distribution": name,
        "installed": True,
        "version": distribution.version,
        "files": files,
        "fingerprint": _hash_object(files),
    }


def _site_packages_ownership_inventory(
    *,
    allowed_distributions: Sequence[str],
    site_roots: Sequence[str | Path] | None = None,
) -> dict[str, Any]:
    """Close importable files while normalizing safe generated bytecode.

    Source-owned ``__pycache__`` is a derived, non-authoritative cache and is
    excluded from stable identity because normal imports may create it after
    preflight.  A cache without a present, distribution-owned hashed source is
    still fatal.
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
        name = _canonical_distribution_name(raw_name)
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


def _validate_runtime_closure_identity(
    value: Any,
    *,
    required_versions: Mapping[str, Any],
    site_roots: Sequence[str | Path] | None = None,
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("isolated runtime closure identity is missing")
    unsigned = {
        key: item
        for key, item in value.items()
        if key not in {"passed", "reasons", "fingerprint"}
    }
    normalized_required = {
        _canonical_distribution_name(name): str(version)
        for name, version in required_versions.items()
    }
    expected_installed = set(normalized_required) | set(FROZEN_BOOTSTRAP_DISTRIBUTIONS)
    installed_names: list[str] = []
    for distribution in importlib.metadata.distributions():
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            raise ValueError("isolated installed distribution lacks a name")
        installed_names.append(_canonical_distribution_name(raw_name))
    if len(set(installed_names)) != len(installed_names):
        raise ValueError("isolated installed distribution names are duplicated")
    fingerprints = value.get("distribution_fingerprints")
    if (
        value.get("passed") is not True
        or value.get("reasons") != []
        or value.get("fingerprint") != _hash_object(unsigned)
        or value.get("required_versions") != dict(sorted(normalized_required.items()))
        or value.get("dependency_closure") != sorted(normalized_required)
        or value.get("installed_distributions") != sorted(expected_installed)
        or set(installed_names) != expected_installed
        or value.get("bootstrap_distributions")
        != FROZEN_BOOTSTRAP_DISTRIBUTIONS
        or not isinstance(fingerprints, Mapping)
        or set(fingerprints) != expected_installed
    ):
        raise ValueError("isolated installed distribution set is not the frozen closure")
    for name in sorted(expected_installed):
        actual = _distribution_inventory(name)
        expected = {
            "version": actual.get("version"),
            "fingerprint": actual.get("fingerprint"),
        }
        if fingerprints.get(name) != expected:
            raise ValueError(f"isolated runtime distribution fingerprint drifted: {name}")
    for name, expected in FROZEN_BOOTSTRAP_DISTRIBUTIONS.items():
        if fingerprints.get(name) != expected:
            raise ValueError(f"isolated bootstrap distribution drifted: {name}")
    recorded_ownership = value.get("site_packages_ownership")
    live_ownership = _site_packages_ownership_inventory(
        allowed_distributions=sorted(expected_installed),
        site_roots=site_roots,
    )
    if (
        not isinstance(recorded_ownership, Mapping)
        or recorded_ownership.get("passed") is not True
        or recorded_ownership.get("reasons") != []
        or live_ownership != recorded_ownership
    ):
        raise ValueError("isolated site-packages ownership closure drifted")


def _verify_environment_preflight(
    path: str | Path,
    *,
    expected_fingerprint: str,
) -> dict[str, Any]:
    """Bind postprocessing to the actual isolated preflight environment.

    A caller-supplied fingerprint alone does not prove which interpreter ran
    this program. Revalidate the preflight self-hash, exact lock identity,
    interpreter/native binaries, current ``-I`` flags and startup hooks, thread
    settings, and every installed wheel payload before accepting the
    isolated-environment claim. ``expected_fingerprint`` is the runtime
    fingerprint stored by the independent run attestation; the larger wheel
    closure fingerprint is separately derived from this preflight receipt.
    """

    receipt_path = Path(path).resolve()
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"isolated environment preflight unreadable: {type(exc).__name__}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ValueError("isolated environment preflight must be a mapping")
    unsigned = dict(payload)
    declared_self_hash = unsigned.pop("preflight_sha256", None)
    if declared_self_hash != _hash_object(unsigned):
        raise ValueError("isolated environment preflight self-hash is invalid")
    if (
        payload.get("artifact_type") != "w0wa_exact_preflight"
        or payload.get("profile_id") != EXACT_PROFILE_ID
        or payload.get("target_commitment") != PREREGISTERED_TARGET_COMMITMENT
        or payload.get("passed") is not True
        or payload.get("status") != "PASS"
    ):
        raise ValueError("isolated environment preflight is not an exact PASS")
    environment = payload.get("environment")
    if not isinstance(environment, Mapping) or environment.get("passed") is not True:
        raise ValueError("isolated environment receipt is not passed")
    environment_unsigned = {
        key: value
        for key, value in environment.items()
        if key not in {"passed", "reasons", "fingerprint"}
    }
    preflight_environment_fingerprint = environment.get("fingerprint")
    if preflight_environment_fingerprint != _hash_object(environment_unsigned):
        raise ValueError("isolated environment closure fingerprint is invalid")
    if (environment.get("lock") or {}).get("sha256") != (
        TRUSTED_DEPENDENCY_LOCK_SHA256
    ):
        raise ValueError("isolated environment dependency lock is not trusted")

    runtime = environment.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError("isolated runtime record is missing")
    runtime_fingerprint = _runtime_environment_fingerprint(runtime)
    if runtime_fingerprint != expected_fingerprint:
        raise ValueError("isolated runtime fingerprint does not match the run")
    import_policy = _verify_current_import_policy(runtime.get("import_policy"))
    recorded_executable = Path(str(runtime.get("executable") or "")).absolute()
    current_executable = Path(sys.executable).absolute()
    if recorded_executable != current_executable:
        raise ValueError("postprocessor is not running under the preflight interpreter")
    thread_environment = runtime.get("thread_environment")
    expected_threads = {
        name: os.environ.get(name)
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
    }
    if thread_environment != expected_threads:
        raise ValueError("postprocessor thread environment drifted from preflight")

    native_binaries = (runtime.get("native_runtime") or {}).get("binaries") or {}
    if not isinstance(native_binaries, Mapping) or set(native_binaries) != {
        "python",
        "mpirun",
        "mpi4py_extension",
    }:
        raise ValueError("isolated native runtime binary closure is incomplete")
    for name, record in native_binaries.items():
        if not isinstance(record, Mapping):
            raise ValueError(f"isolated native runtime record invalid: {name}")
        binary = Path(str(record.get("path") or "")).resolve()
        if not binary.is_file() or binary.stat().st_size != record.get("size_bytes"):
            raise ValueError(
                f"isolated native runtime binary missing or resized: {name}"
            )
        if _hash_file(binary) != record.get("sha256"):
            raise ValueError(f"isolated native runtime binary hash drifted: {name}")
    if Path(str(native_binaries["python"]["path"])).resolve() != (
        current_executable.resolve()
    ):
        raise ValueError("postprocessor Python binary differs from preflight")

    required_versions = environment.get("required_versions")
    distributions = environment.get("distributions")
    if (
        not isinstance(required_versions, Mapping)
        or not isinstance(distributions, Mapping)
        or len(required_versions) != EXACT_DISTRIBUTION_COUNT
        or set(distributions) != set(required_versions)
        or environment.get("runtime_closure") != sorted(required_versions)
    ):
        raise ValueError("isolated installed distribution closure is incomplete")
    _validate_runtime_closure_identity(
        runtime.get("runtime_closure"),
        required_versions=required_versions,
        site_roots=(runtime.get("import_policy") or {}).get("site_package_roots"),
    )
    for name, expected_version in required_versions.items():
        expected_record = distributions.get(name)
        actual_record = _distribution_inventory(str(name))
        if not isinstance(expected_record, Mapping):
            raise ValueError(f"isolated distribution record missing: {name}")
        if expected_record.get("version") != expected_version:
            raise ValueError(f"isolated distribution version receipt invalid: {name}")
        if actual_record != expected_record:
            raise ValueError(f"isolated distribution bytes drifted: {name}")
    return {
        "path": str(receipt_path),
        "sha256": _hash_file(receipt_path),
        "preflight_sha256": declared_self_hash,
        "environment_fingerprint": expected_fingerprint,
        "preflight_environment_fingerprint": preflight_environment_fingerprint,
        "interpreter": str(current_executable),
        "distribution_count": len(distributions),
        "import_policy": import_policy,
        "verified": True,
    }


def _sampled_parameters(config: Mapping[str, Any]) -> list[str]:
    return [
        str(name)
        for name, spec in (config.get("params") or {}).items()
        if isinstance(spec, Mapping) and spec.get("prior") is not None
    ]


def _mcse_reference(name: str, posterior_std: float) -> tuple[str, float]:
    """Return the frozen scale used to judge one parameter's mean MCSE."""

    paper_parameter = REPORT_TO_PAPER_PARAMETER.get(name)
    if paper_parameter is None:
        return "posterior_sd", posterior_std
    uncertainty = PREREGISTERED_PAPER_UNCERTAINTIES.get(paper_parameter)
    if not isinstance(uncertainty, Mapping):
        raise ValueError(f"paper uncertainty is not frozen for {paper_parameter}")
    return "paper_sigma", min(
        float(uncertainty["minus"]),
        float(uncertainty["plus"]),
    )


def _read_chain_snapshot(
    path: Path,
) -> tuple[list[str], np.ndarray, str, int]:
    """Parse and hash one immutable in-memory snapshot of a chain file.

    Opening the path separately for parsing and hashing permits a replacement
    between those operations to bind one file while analyzing another. Read
    exactly once from one descriptor, then use the resulting immutable
    ``bytes`` object for both operations.
    """

    try:
        with path.open("rb") as handle:
            snapshot = handle.read()
    except OSError as exc:
        raise ValueError(f"chain snapshot unreadable: {path.name}") from exc
    if not snapshot:
        raise ValueError(f"empty Cobaya table in {path}")
    try:
        text = snapshot.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"non-UTF-8 Cobaya table in {path}") from exc
    header: list[str] | None = None
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            header = line.lstrip()[1:].split()
            break
        if line.strip():
            raise ValueError(f"missing Cobaya header in {path}")
    if not header:
        raise ValueError(f"empty Cobaya table in {path}")
    try:
        data = np.loadtxt(io.StringIO(text), comments="#", ndmin=2)
    except ValueError as exc:
        raise ValueError(f"malformed Cobaya table in {path}") from exc
    if data.shape[0] == 0 or data.shape[1] != len(header):
        raise ValueError(f"malformed Cobaya table in {path}")
    return (
        header,
        np.asarray(data, dtype=float),
        _hash_bytes(snapshot),
        len(snapshot),
    )


def independently_postprocess(
    *,
    chain_prefix: str | Path,
    updated_config_path: str | Path,
    run_id: str,
    burn_fraction: float = 0.30,
    primary_execution_fingerprint: str | None = None,
    environment_fingerprint: str | None = None,
    environment_preflight_path: str | Path | None = None,
) -> dict[str, Any]:
    if (
        isinstance(burn_fraction, bool)
        or not isinstance(burn_fraction, (int, float))
        or not math.isfinite(float(burn_fraction))
        or not 0 <= burn_fraction < 1
    ):
        raise ValueError("burn_fraction must be in [0, 1)")
    binding_requested = any(
        item is not None
        for item in (
            primary_execution_fingerprint,
            environment_fingerprint,
            environment_preflight_path,
        )
    )
    if binding_requested and burn_fraction != FROZEN_FORMAL_BURN_FRACTION:
        raise ValueError("bound independent postprocessing requires burn_fraction=0.30")
    if binding_requested and not (
        isinstance(primary_execution_fingerprint, str)
        and primary_execution_fingerprint.startswith("sha256:")
        and len(primary_execution_fingerprint) == 71
        and isinstance(environment_fingerprint, str)
        and environment_fingerprint.startswith("sha256:")
        and len(environment_fingerprint) == 71
        and environment_preflight_path is not None
    ):
        raise ValueError(
            "primary/isolated fingerprints and isolated preflight are required"
        )
    config_path = Path(updated_config_path)
    sampled = _sampled_parameters(_load_yaml(config_path))
    names = list(dict.fromkeys([*sampled, *REPORT_PARAMETERS]))
    chain_records: list[dict[str, Any]] = []
    expanded: list[dict[str, np.ndarray]] = []
    reporting_rows: list[dict[str, np.ndarray]] = []
    for index in range(1, 5):
        path = Path(f"{chain_prefix}.{index}.txt").resolve()
        header, rows, chain_sha256, snapshot_size = _read_chain_snapshot(path)
        missing = sorted(set(["weight", *names]) - set(header))
        if missing:
            raise ValueError(f"chain {index} columns missing: {','.join(missing)}")
        weights = rows[:, header.index("weight")]
        integer_weights = np.rint(weights)
        if (
            np.any(~np.isfinite(weights))
            or np.any(weights <= 0)
            or not np.allclose(weights, integer_weights)
        ):
            raise ValueError(f"chain {index} has invalid integer weights")
        # GetDist's ``ignore_rows`` removes a fraction of physical table rows,
        # not a fraction of multiplicity-expanded samples. Keep that exact
        # convention independent of the canonical implementation.
        burn_rows = int(round(rows.shape[0] * burn_fraction))
        retained = rows[burn_rows:, :]
        retained_weights = integer_weights[burn_rows:]
        if retained.shape[0] < 4:
            raise ValueError(f"chain {index} has too few post-burn rows")
        total = int(retained_weights.sum())
        if total > MAX_EXPANDED_DRAWS_PER_CHAIN:
            raise ValueError(f"chain {index} exceeds expansion safety limit")
        row_indices = np.repeat(
            np.arange(retained.shape[0]), retained_weights.astype(int)
        )
        if row_indices.size < 4:
            raise ValueError(f"chain {index} has too few post-burn draws")
        expanded.append(
            {name: retained[row_indices, header.index(name)] for name in names}
        )
        reporting_rows.append(
            {
                "weights": retained_weights.astype(float),
                **{name: retained[:, header.index(name)] for name in names},
            }
        )
        chain_records.append(
            {
                "chain_id": f"chain-{index}:{chain_sha256.split(':')[1][:16]}",
                "path": str(path),
                "sha256": chain_sha256,
                "snapshot_size_bytes": snapshot_size,
                "snapshot_policy": "single_fd_immutable_bytes_parse_and_sha256",
                "raw_rows": int(rows.shape[0]),
                "post_burn_rows": int(retained.shape[0]),
                "post_burn_draws": int(row_indices.size),
            }
        )
    if len({item["sha256"] for item in chain_records}) != 4:
        raise ValueError("chain files are not independent")
    expanded_counts = [len(chain[names[0]]) for chain in expanded]
    aligned_count = min(expanded_counts)
    alignment_fractions = [aligned_count / count for count in expanded_counts]
    aligned = {
        name: np.stack([chain[name][-aligned_count:] for chain in expanded])
        for name in names
    }
    idata = az.from_dict(posterior=aligned)
    rhat = az.rhat(idata, method="rank")
    ess = az.ess(idata, method="bulk")
    mcse = az.mcse(idata, method="mean")
    diagnostics: dict[str, Any] = {}
    failures: list[str] = []
    if min(alignment_fractions) < MIN_DIAGNOSTIC_ALIGNMENT_FRACTION:
        failures.append("chain_lengths:diagnostic_alignment_fraction_below_0.90")
    for name in names:
        parameter_rhat = float(np.asarray(rhat[name]).reshape(-1)[0])
        parameter_ess = float(np.asarray(ess[name]).reshape(-1)[0])
        parameter_mcse = float(np.asarray(mcse[name]).reshape(-1)[0])
        posterior_std = float(np.std(aligned[name].reshape(-1), ddof=1))
        mcse_reference_kind, mcse_reference_value = _mcse_reference(name, posterior_std)
        mcse_ratio = (
            parameter_mcse / mcse_reference_value
            if math.isfinite(mcse_reference_value) and mcse_reference_value > 0
            else math.inf
        )
        mcse_absolute_limit = MCSE_MAX_REFERENCE_SIGMA_EXCLUSIVE * mcse_reference_value
        rhat_passed = (
            math.isfinite(parameter_rhat) and parameter_rhat < RANK_RHAT_MAX_EXCLUSIVE
        )
        bulk_ess_passed = math.isfinite(parameter_ess) and parameter_ess >= BULK_ESS_MIN
        mcse_passed = (
            math.isfinite(parameter_mcse)
            and parameter_mcse >= 0
            and math.isfinite(mcse_ratio)
            and parameter_mcse < mcse_absolute_limit
        )
        passed = rhat_passed and bulk_ess_passed and mcse_passed
        if not passed:
            failures.append(f"diagnostic_threshold_failed:{name}")
        diagnostics[name] = {
            "rank_normalized_rhat": parameter_rhat,
            "bulk_ess": parameter_ess,
            "mcse_mean": parameter_mcse,
            "posterior_std": posterior_std,
            "mcse_reference_kind": mcse_reference_kind,
            "mcse_reference_value": mcse_reference_value,
            "mcse_absolute_limit_exclusive": mcse_absolute_limit,
            "mcse_over_reference_sigma": mcse_ratio,
            "rhat_passed": rhat_passed,
            "bulk_ess_passed": bulk_ess_passed,
            "mcse_passed": mcse_passed,
            "passed": passed,
        }
    report_values = {
        name: np.concatenate([chain[name] for chain in reporting_rows])
        for name in names
    }
    report_weights = np.concatenate([chain["weights"] for chain in reporting_rows])
    matrix = np.column_stack([report_values[name] for name in REPORT_PARAMETERS])
    samples = MCSamples(
        samples=matrix,
        weights=report_weights,
        names=list(REPORT_PARAMETERS),
        labels=list(REPORT_PARAMETERS),
        settings={"contours": [0.68, 0.95]},
    )
    means = samples.getMeans()
    stds = np.sqrt(samples.getVars())
    marge_stats = samples.getMargeStats()
    intervals: dict[str, Any] = {}
    for parameter_index, name in enumerate(REPORT_PARAMETERS):
        center = float(means[parameter_index])
        standard_deviation = float(stds[parameter_index])
        marginal_limit = marge_stats.parWithName(name).limits[0]
        if name == "wa":
            lower = float(marginal_limit.lower)
            upper = float(marginal_limit.upper)
            reporting_statistic = "mean_and_minimal_68_percent_credible_interval"
        else:
            lower = center - standard_deviation
            upper = center + standard_deviation
            reporting_statistic = "posterior_mean_plus_or_minus_standard_deviation"
        intervals[name] = {
            "center": center,
            "lower_68": lower,
            "upper_68": upper,
            "uncertainty_minus": center - lower,
            "uncertainty_plus": upper - center,
            "mcse_mean": diagnostics[name]["mcse_mean"],
            "reporting_statistic": reporting_statistic,
        }
    report = {
        "schema_version": 1,
        "artifact_type": "independent_w0wa_postprocess",
        "profile_id": EXACT_PROFILE_ID,
        "claim_scope": EXACT_CLAIM_SCOPE,
        "target_commitment": PREREGISTERED_TARGET_COMMITMENT,
        "protocol_status": dict(PROTOCOL_STATUS),
        "run_id": run_id,
        "status": "PASS" if not failures else "WITHHELD",
        "failures": failures,
        "execution_policy": {
            "mode": (
                "research_alpha_bound" if binding_requested else "exploratory_unbound"
            ),
            "formal_burn_fraction": FROZEN_FORMAL_BURN_FRACTION,
            "formal_burn_fraction_enforced": binding_requested,
            "current_python_isolation": _current_python_isolation(),
            "preflight_import_policy_verified": False,
        },
        "updated_config": {
            "path": str(config_path.resolve()),
            "sha256": _hash_file(config_path),
        },
        "burn_fraction": burn_fraction,
        "burn_convention": "getdist_remove_fraction_of_raw_rows_per_chain",
        "aligned_draws_per_chain": aligned_count,
        "diagnostic_alignment": "recent_draws_truncated_to_shortest_chain",
        "minimum_diagnostic_alignment_fraction_inclusive": (
            MIN_DIAGNOSTIC_ALIGNMENT_FRACTION
        ),
        "diagnostic_alignment_fraction_per_chain": alignment_fractions,
        "maximum_diagnostic_discarded_fraction": 1.0 - min(alignment_fractions),
        "chain_length_balance_passed": (
            min(alignment_fractions) >= MIN_DIAGNOSTIC_ALIGNMENT_FRACTION
        ),
        "reporting_engine": "GetDist 1.7.7 weighted all-post-burn rows",
        "chain_files": chain_records,
        "sampled_parameters": sampled,
        "diagnostic_thresholds": {
            "rank_normalized_rhat_maximum_exclusive": RANK_RHAT_MAX_EXCLUSIVE,
            "bulk_ess_minimum_inclusive": BULK_ESS_MIN,
            "mcse_maximum_reference_sigma_exclusive": (
                MCSE_MAX_REFERENCE_SIGMA_EXCLUSIVE
            ),
            "mcse_reference_policy": {
                "reported_parameters": "preregistered_paper_sigma",
                "unreported_sampled_parameters": "same_closed_run_posterior_sd",
            },
        },
        "diagnostics": diagnostics,
    }
    if not failures:
        report["intervals_68"] = intervals
    if binding_requested:
        environment_preflight = _verify_environment_preflight(
            environment_preflight_path,
            expected_fingerprint=environment_fingerprint,
        )
        current_import_policy = environment_preflight["import_policy"]
        report["execution_policy"] = {
            "mode": "research_alpha_bound",
            "formal_burn_fraction": FROZEN_FORMAL_BURN_FRACTION,
            "formal_burn_fraction_enforced": True,
            "current_python_isolation": {
                key: current_import_policy[key]
                for key in (
                    "isolated_interpreter",
                    "ignore_environment",
                    "no_user_site",
                    "safe_path",
                    "pythonpath_empty",
                    "passed",
                    "reasons",
                )
            },
            "preflight_import_policy_fingerprint": current_import_policy[
                "preflight_import_policy_fingerprint"
            ],
            "startup_hook_fingerprint": current_import_policy[
                "startup_hook_fingerprint"
            ],
            "preflight_import_policy_verified": True,
        }
        report["research_alpha_binding"] = {
            "primary_execution_fingerprint": primary_execution_fingerprint,
            "environment_fingerprint": environment_fingerprint,
            "environment_preflight": environment_preflight,
            "current_import_policy": current_import_policy,
            "chain_sha256": [item["sha256"] for item in chain_records],
            "sampled_parameters": sampled,
        }
    report["report_sha256"] = _hash_object(report)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain-prefix", type=Path, required=True)
    parser.add_argument("--updated-config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--burn-fraction", type=float, default=0.30)
    parser.add_argument("--primary-execution-fingerprint", required=True)
    parser.add_argument("--environment-fingerprint", required=True)
    parser.add_argument("--environment-preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _historical_state_locations() -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Return revision-1 and Amendment-002 state forbidden to new runs."""

    local_dir = BACKEND_ROOT.parent / ".local" / "w0wa-strict-a-readiness"
    protected_directories = (
        BACKEND_ROOT / "packages",
        local_dir / "exact-venv",
        local_dir / "wheels",
        local_dir / "isolated-venv",
        local_dir / "isolated",
        local_dir / "adequacy-configs",
        local_dir / "exact-venv-r2",
        local_dir / "wheelhouse-r2",
        local_dir / "packages-r2",
        local_dir / "primary-r2",
        local_dir / "isolated-venv-r2",
        local_dir / "isolated-r2",
    )
    protected_files = tuple(
        local_dir / name
        for name in (
            "w0wa_exact_map.yaml",
            "lcdm_exact_map.yaml",
            "preflight.json",
            "generation.json",
            "analysis.json",
            "model_adequacy.json",
            "hidden_answer.json",
            "grade.json",
            "protocol.json",
            "diagnostic-report.json",
            "independent-postprocess.json",
            "isolated-preflight.json",
            "isolated-generation.json",
        )
    )
    return protected_directories, protected_files


def _path_is_historical_exact_state(value: str | Path) -> bool:
    """Detect old state by both its lexical namespace and resolved target."""

    # Resolving first erases a caller-visible historical namespace.  A symlink
    # named under ``isolated-r2`` can point to fresh Amendment-003 state, but
    # Amendment 003 still forbids using that historical lexical name.  Check a
    # normalized absolute path without following symlinks first, then retain
    # the resolved check to reject aliases whose target is historical state.
    lexical_candidate = Path(os.path.abspath(os.fspath(Path(value).expanduser())))
    protected_directories, protected_files = _historical_state_locations()

    def is_protected(candidate, normalize) -> bool:
        if candidate in {normalize(path) for path in protected_files}:
            return True
        normalized_directories = tuple(
            normalize(directory) for directory in protected_directories
        )
        if any(
            candidate == directory or candidate.is_relative_to(directory)
            for directory in normalized_directories
        ):
            return True
        chain_root = BACKEND_ROOT / "cobaya_runs"
        for name in (
            "w0wa_exact_formal",
            "w0wa_exact_smoke",
            "w0wa_exact_isolated",
            "w0wa_exact_formal_r2",
            "w0wa_exact_smoke_r2",
            "w0wa_exact_isolated_r2",
        ):
            prefix = normalize(chain_root / name)
            if (
                candidate == prefix
                or candidate.is_relative_to(prefix)
                or (
                    candidate.parent == prefix.parent
                    and candidate.name.startswith(prefix.name + ".")
                )
            ):
                return True
        return False

    def lexical_normalize(path: Path) -> Path:
        return Path(os.path.abspath(os.fspath(path.expanduser())))

    if is_protected(lexical_candidate, lexical_normalize):
        return True
    return is_protected(lexical_candidate.resolve(), lambda path: path.resolve())


def _historical_state_argument_violations(
    args: argparse.Namespace,
) -> list[tuple[str, Path]]:
    """Validate every CLI path before analysis or output creation."""

    violations: list[tuple[str, Path]] = []
    if _path_is_historical_exact_state(sys.executable):
        violations.append(("python_executable", Path(sys.executable).resolve()))
    for field in (
        "chain_prefix",
        "updated_config",
        "environment_preflight",
        "output",
    ):
        value = getattr(args, field, None)
        if value is not None and _path_is_historical_exact_state(value):
            violations.append((field, Path(value).expanduser().resolve()))
    return violations


def _output_freshness_violation(args: argparse.Namespace) -> tuple[Path, str] | None:
    """Reject an existing report or symlink before any chain analysis."""

    output = Path(args.output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        return output, "output_file_already_exists"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    historical_violations = _historical_state_argument_violations(args)
    if historical_violations:
        for field, path in historical_violations:
            print(
                f"historical exact state path forbidden for {field}: {path}",
                file=sys.stderr,
            )
        return 2
    freshness_violation = _output_freshness_violation(args)
    if freshness_violation is not None:
        path, reason = freshness_violation
        print(
            f"output destination not fresh: {path} ({reason})",
            file=sys.stderr,
        )
        return 2
    report = independently_postprocess(
        chain_prefix=args.chain_prefix,
        updated_config_path=args.updated_config,
        run_id=args.run_id,
        burn_fraction=args.burn_fraction,
        primary_execution_fingerprint=args.primary_execution_fingerprint,
        environment_fingerprint=args.environment_fingerprint,
        environment_preflight_path=args.environment_preflight,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"independent postprocess status: {report['status']}")
    print(f"report: {args.output}")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
