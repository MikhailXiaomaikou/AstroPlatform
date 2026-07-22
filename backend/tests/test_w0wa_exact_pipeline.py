"""Receipt and hidden-grade tests for the exact DESI w0wa offline pipeline."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import inspect
import json
import math
import subprocess
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import yaml
import numpy as np
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_DIR = _REPO_ROOT / "backend" / "scripts" / "cobaya"
_MODULE_PATH = _SCRIPT_DIR / "canonical_full_likelihood_evidence.py"
_SPEC = importlib.util.spec_from_file_location("w0wa_exact_pipeline", _MODULE_PATH)
assert _SPEC and _SPEC.loader
pipeline = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = pipeline
_SPEC.loader.exec_module(pipeline)

EXACT_CONFIG = _SCRIPT_DIR / "w0wa_desi_cmb_pantheonplus_exact.yaml"
DEPENDENCY_LOCK = _SCRIPT_DIR / "w0wa_exact_requirements.txt"
REFERENCE_CASES = _SCRIPT_DIR / "w0wa_exact_reference_cases.json"
WHEEL_MANIFEST = _SCRIPT_DIR / "w0wa_exact_wheel_manifest.json"


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _synthetic_inventory() -> dict:
    groups = {
        name: {
            "versions": ["test"],
            "files": [{"path": f"data/{index}.dat", "sha256": "sha256:" + "a" * 64}],
        }
        for index, name in enumerate(pipeline.REQUIRED_LIKELIHOODS)
    }
    return {
        "packages_path": "/synthetic/packages",
        "complete": True,
        "missing": [],
        "groups": groups,
        "fingerprint": pipeline._hash_object(groups),
    }


def _reference_payload(config_path: Path, inventory: dict) -> dict:
    del config_path, inventory
    return json.loads(REFERENCE_CASES.read_text(encoding="utf-8"))


def _environment_pass(lock_path: Path = DEPENDENCY_LOCK, wheels_path: Path | None = None) -> dict:
    return {
        "passed": True,
        "reasons": [],
        "required_versions": pipeline.REQUIRED_PACKAGE_VERSIONS,
        "fingerprint": "sha256:" + "b" * 64,
        "lock": {"path": str(Path(lock_path).resolve())},
        "wheels_path": str(Path(wheels_path or lock_path.parent).resolve()),
    }


def _trusted_runner_identity_provider(root: Path):
    """Build a host-neutral regular-file runner identity for launcher tests."""

    root.mkdir(parents=True, exist_ok=True)
    site_root = root / "site-packages"
    site_root.mkdir()
    python_path = root / "python"
    mpirun_path = root / "mpirun"
    module_path = site_root / "cobaya" / "run.py"
    module_path.parent.mkdir()
    python_path.write_bytes(b"fixture trusted Python launcher\n")
    mpirun_path.write_bytes(b"fixture trusted MPI launcher\n")
    module_path.write_bytes(b"# fixture cobaya.run module\n")
    python_path.chmod(0o555)
    mpirun_path.chmod(0o555)

    import_policy_payload = {
        "schema_version": 1,
        "isolated_interpreter": True,
        "python_flag": "-I",
        "ignore_environment": True,
        "no_user_site": True,
        "safe_path": True,
        "pythonpath_empty": True,
        "user_site_disabled_by_child": True,
        "venv_root": str(root.resolve()),
        "site_package_roots": [str(site_root.resolve())],
        "startup_hooks": [],
    }
    import_policy = {
        **import_policy_payload,
        "passed": True,
        "reasons": [],
        "fingerprint": pipeline._hash_object(import_policy_payload),
    }

    def identity() -> dict:
        executable = pipeline._regular_executable_record(
            python_path.resolve(), invoked_path=python_path.resolve()
        )
        executable["source_resolved_path"] = str(python_path.resolve())
        executable["source_sha256"] = pipeline._hash_file(python_path)
        return {
            "schema_version": 1,
            "invocation": "current_interpreter_module",
            "in_virtual_environment": True,
            "virtual_environment_prefix": str(root.resolve()),
            "import_policy": copy.deepcopy(import_policy),
            "executable": executable,
            "mpirun": pipeline._regular_executable_record(
                mpirun_path.resolve(), invoked_path=mpirun_path.resolve()
            ),
            "module": {
                "name": "cobaya.run",
                "path": str(module_path.resolve()),
                "size_bytes": module_path.stat().st_size,
                "sha256": pipeline._hash_file(module_path),
            },
            "distribution": {
                "name": "cobaya",
                "version": pipeline.REQUIRED_PACKAGE_VERSIONS["cobaya"],
                "root": str(site_root.resolve()),
                "fingerprint": "sha256:" + "9" * 64,
            },
        }

    return identity


def _install_trusted_child_identity_fixture(root: Path, monkeypatch):
    root.mkdir(parents=True, exist_ok=True)
    source_python = root / "source-python"
    launcher = root / "trusted-python"
    mpirun = root / "trusted-mpirun"
    for path, content in (
        (source_python, b"fixture source Python\n"),
        (launcher, b"fixture materialized Python\n"),
        (mpirun, b"fixture trusted mpirun\n"),
    ):
        path.write_bytes(content)
        path.chmod(0o555)
    distribution_root = root / "site-packages"
    module_path = distribution_root / "cobaya" / "run.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_bytes(b"# fixture cobaya.run\n")

    class FakeDistribution:
        metadata = {"Name": "cobaya"}

        def __init__(self):
            self.version = pipeline.REQUIRED_PACKAGE_VERSIONS["cobaya"]

        def locate_file(self, relative):
            return distribution_root / relative

    state = SimpleNamespace(
        source_python=source_python,
        launcher=launcher,
        mpirun=mpirun,
        distribution=FakeDistribution(),
        distribution_root=distribution_root,
        module_path=module_path,
    )
    monkeypatch.setattr(pipeline.sys, "executable", str(source_python))
    monkeypatch.setattr(pipeline.sys, "prefix", str(root / "venv"))
    monkeypatch.setattr(pipeline.sys, "base_prefix", str(root / "base"))
    monkeypatch.setattr(
        pipeline, "_materialize_trusted_python_launcher", lambda _: launcher
    )
    monkeypatch.setattr(
        pipeline.shutil,
        "which",
        lambda name: str(mpirun) if name == "mpirun" else None,
    )
    commitments = dict(pipeline.TRUSTED_NATIVE_RUNTIME_SHA256)
    commitments["mpirun"] = pipeline._hash_file(mpirun)
    monkeypatch.setattr(pipeline, "TRUSTED_NATIVE_RUNTIME_SHA256", commitments)
    monkeypatch.setattr(
        pipeline.importlib.metadata,
        "distribution",
        lambda _: state.distribution,
    )
    monkeypatch.setattr(
        pipeline.importlib.util,
        "find_spec",
        lambda _: SimpleNamespace(origin=str(state.module_path)),
    )
    monkeypatch.setattr(
        pipeline,
        "_distribution_inventory",
        lambda _: {"fingerprint": "sha256:" + "8" * 64},
    )
    monkeypatch.setattr(
        pipeline,
        "_exact_python_import_policy",
        lambda: {
            "isolated_interpreter": True,
            "pythonpath_empty": True,
            "passed": True,
            "reasons": [],
        },
    )
    return state


def test_exact_profile_matches_paper_stack_and_contains_no_answer_key():
    from cobaya.input import update_info

    config = yaml.safe_load(EXACT_CONFIG.read_text(encoding="utf-8"))
    assert pipeline.validate_canonical_config(config) == []
    assert set(config["likelihood"]) == set(pipeline.REQUIRED_LIKELIHOODS)
    assert config["likelihood"]["act_dr6_lenslike.ACTDR6LensLike"] == {
        "variant": "actplanck_baseline",
        "lens_only": False,
    }
    text = EXACT_CONFIG.read_text(encoding="utf-8").lower()
    for forbidden_answer_key in (
        "paper_target",
        "hidden_answer",
        "expected_center",
        "target_interval",
    ):
        assert forbidden_answer_key not in text
    expanded = update_info(config)
    for nuisance in (
        "A_planck",
        "calib_100T",
        "calib_217T",
        "A_cib_217",
        "xi_sz_cib",
        "ps_A_100_100",
        "galf_TE_A_100",
    ):
        assert nuisance in expanded["params"]
    assert pipeline.protocol_amendment_record()["valid"] is True
    assert pipeline.PAPER_FIDELITY_AMENDMENT["effective_bulk_ess_floor"] == 1000.0
    assert pipeline.EXACT_ENVIRONMENT_REVISION["status"] == (
        "WITHHELD_PENDING_FRESH_PREFLIGHT_AND_SCIENCE_REGRESSION"
    )

    reference = json.loads(REFERENCE_CASES.read_text(encoding="utf-8"))
    registered = {
        name
        for case in reference["cases"]
        for name in case["likelihoods"]
    }
    assert registered == set(pipeline.REFERENCE_LIKELIHOODS)
    camspec = next(
        case
        for case in reference["cases"]
        if case["case_id"] == "planck_pr4_npipe_camspec_ttteee"
    )
    assert camspec["source"]["commit"] == (
        "899f30a49f85de610dac321e91a1af50018e56aa"
    )
    assert camspec["theory_args"] == pipeline.PINNED_REFERENCE_THEORY_ARGS[
        camspec["case_id"]
    ]
    assert camspec["values"]["planck_NPIPE_highl_CamSpec.TTTEEE"] == {
        "absolute_tolerance": 0.1,
        "expected_chi2": 11341.17,
    }
    bao = next(
        case for case in reference["cases"] if case["case_id"] == "desi_dr1_bao_gaussian"
    )
    assert bao["parameterization"] == "upstream_planck_sampled"
    assert bao["point"]["H0"] == 67.25
    assert "theta_MC_100" not in bao["point"]
    assert "w" not in bao["point"] and "wa" not in bao["point"]


def test_amendment_003_parser_defaults_are_isolated_from_prior_state() -> None:
    defaults = pipeline._default_paths()
    local_root = _REPO_ROOT / ".local" / "w0wa-strict-a-readiness"
    primary_root = local_root / "primary-r2-a003"
    assert defaults["packages"] == local_root / "packages-r2-a003"
    assert defaults["wheels"] == local_root / "wheelhouse-r2-a003"
    assert defaults["preflight"] == primary_root / "preflight-r2-a003.json"
    assert defaults["generation"] == primary_root / "generation-r2-a003.json"
    assert defaults["analysis"] == primary_root / "analysis-r2-a003.json"
    assert defaults["adequacy_output_dir"] == primary_root / "adequacy-r2-a003"
    assert defaults["adequacy"] == primary_root / "model-adequacy-r2-a003.json"
    assert defaults["hidden_answer"] == (
        primary_root / "hidden-answer-r2-a003.json"
    )
    assert defaults["grade"] == primary_root / "grade-r2-a003.json"
    assert defaults["formal_chain_prefix"] == (
        _REPO_ROOT / "backend" / "cobaya_runs" / "w0wa_exact_formal_r2_a003"
    )

    parser = pipeline._build_parser()
    parsed = (
        parser.parse_args(["preflight"]),
        parser.parse_args(["generate"]),
        parser.parse_args(
            [
                "run", "--kind", "chain", "--config", str(EXACT_CONFIG),
                "--prefix", str(defaults["formal_chain_prefix"]),
                "--run-id", "parser-default-test",
            ]
        ),
        parser.parse_args(["analyze"]),
        parser.parse_args(["grade", "--target-hash", "sha256:" + "0" * 64]),
    )
    assert all(
        pipeline._historical_state_argument_violations(namespace) == []
        for namespace in parsed
    )


def test_revision_2_cli_rejects_revision_1_state_without_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend_root = tmp_path / "repo" / "backend"
    local_root = tmp_path / "repo" / ".local" / "w0wa-strict-a-readiness"
    legacy_packages = backend_root / "packages"
    legacy_chain = backend_root / "cobaya_runs" / "w0wa_exact_formal"
    monkeypatch.setattr(pipeline, "BACKEND_ROOT", backend_root)
    sentinels = {
        "preflight": local_root / "preflight.json",
        "analysis": local_root / "analysis.json",
        "chain": legacy_chain,
        "package": legacy_packages / "legacy-package.dat",
    }
    for name, path in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"revision-1-{name}\n".encode())
    original = {name: path.read_bytes() for name, path in sentinels.items()}

    def unexpected_runtime_check() -> dict:
        raise AssertionError("revision-1 guard did not run before exact CLI setup")

    monkeypatch.setattr(
        pipeline, "_require_isolated_exact_cli_runtime", unexpected_runtime_check
    )
    cases = (
        ["preflight", "--output", str(sentinels["preflight"])],
        ["generate", "--preflight-report", str(sentinels["preflight"])],
        [
            "run", "--kind", "chain", "--config", str(EXACT_CONFIG),
            "--prefix", str(sentinels["chain"]),
            "--run-id", "legacy-path-rejection-test",
        ],
        [
            "run", "--kind", "chain", "--config", str(EXACT_CONFIG),
            "--prefix", str(backend_root / "cobaya_runs" / "r2-test"),
            "--run-id", "legacy-runner-rejection-test",
            "--cobaya-run", str(local_root / "exact-venv" / "bin" / "cobaya-run"),
        ],
        ["analyze", "--packages-path", str(legacy_packages)],
        [
            "grade", "--manifest", str(sentinels["analysis"]),
            "--target-hash", "sha256:" + "0" * 64,
        ],
    )
    for argv in cases:
        assert pipeline.main(argv) == 2
    monkeypatch.setattr(
        pipeline.sys,
        "executable",
        str(local_root / "isolated-venv" / "bin" / "python"),
    )
    assert pipeline.main(["preflight"]) == 2
    assert {name: path.read_bytes() for name, path in sentinels.items()} == original
    assert not (local_root / "primary-r2-a003").exists()


def test_amendment_003_cli_rejects_amendment_002_state_without_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend_root = tmp_path / "repo" / "backend"
    local_root = tmp_path / "repo" / ".local" / "w0wa-strict-a-readiness"
    chain_root = backend_root / "cobaya_runs"
    monkeypatch.setattr(pipeline, "BACKEND_ROOT", backend_root)
    sentinels = {
        "package": local_root / "packages-r2" / "package.dat",
        "wheel": local_root / "wheelhouse-r2" / "wheel.whl",
        "preflight": local_root / "primary-r2" / "preflight-r2.json",
        "generation": local_root / "primary-r2" / "generation-r2.json",
        "analysis": local_root / "primary-r2" / "analysis-r2.json",
        "grade": local_root / "primary-r2" / "grade-r2.json",
        "runner": local_root / "exact-venv-r2" / "bin" / "cobaya-run",
        "chain": chain_root / "w0wa_exact_formal_r2.1.txt",
        "isolated_chain": chain_root / "w0wa_exact_isolated_r2.1.txt",
    }
    for name, path in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"amendment-002-{name}\n".encode())
    alias = tmp_path / "preflight-alias.json"
    alias.symlink_to(sentinels["preflight"])
    original = {name: path.read_bytes() for name, path in sentinels.items()}

    def unexpected_runtime_check() -> dict:
        raise AssertionError("historical guard did not run before exact CLI setup")

    monkeypatch.setattr(
        pipeline, "_require_isolated_exact_cli_runtime", unexpected_runtime_check
    )
    cases = (
        ["preflight", "--output", str(alias)],
        ["preflight", "--packages-path", str(sentinels["package"].parent)],
        ["preflight", "--wheels-path", str(sentinels["wheel"].parent)],
        ["generate", "--output", str(sentinels["generation"])],
        [
            "run", "--kind", "chain", "--config", str(EXACT_CONFIG),
            "--prefix", str(chain_root / "w0wa_exact_formal_r2"),
            "--run-id", "amendment-002-prefix-rejection-test",
        ],
        [
            "run", "--kind", "chain", "--config", str(EXACT_CONFIG),
            "--prefix", str(chain_root / "w0wa_exact_formal_r2_a003"),
            "--run-id", "amendment-002-runner-rejection-test",
            "--cobaya-run", str(sentinels["runner"]),
        ],
        [
            "analyze",
            "--chain-prefix", str(chain_root / "w0wa_exact_isolated_r2"),
        ],
        [
            "grade", "--manifest", str(sentinels["analysis"]),
            "--target-hash", "sha256:" + "0" * 64,
        ],
    )
    for argv in cases:
        assert pipeline.main(argv) == 2

    monkeypatch.setattr(
        pipeline.sys,
        "executable",
        str(local_root / "isolated-venv-r2" / "bin" / "python"),
    )
    assert pipeline.main(["preflight"]) == 2
    assert {name: path.read_bytes() for name, path in sentinels.items()} == original
    assert alias.is_symlink()
    assert not (local_root / "primary-r2-a003").exists()


def test_amendment_003_cli_rejects_historical_symlink_name_to_fresh_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    backend_root = repo_root / "backend"
    local_root = repo_root / ".local" / "w0wa-strict-a-readiness"
    fresh_target = local_root / "primary-r2-a003" / "preflight-r2-a003.json"
    fresh_target.parent.mkdir(parents=True)
    fresh_target.write_bytes(b"fresh-amendment-003-preflight\n")
    historical_alias = local_root / "primary-r2" / "preflight-r2.json"
    historical_alias.parent.mkdir()
    historical_alias.symlink_to(
        Path("../primary-r2-a003/preflight-r2-a003.json")
    )
    monkeypatch.setattr(pipeline, "BACKEND_ROOT", backend_root)
    monkeypatch.chdir(repo_root)

    def unexpected_runtime_check() -> dict:
        raise AssertionError("lexical historical guard did not run first")

    monkeypatch.setattr(
        pipeline, "_require_isolated_exact_cli_runtime", unexpected_runtime_check
    )
    raw_argument = Path(
        ".local/w0wa-strict-a-readiness/primary-r2/preflight-r2.json"
    )
    assert raw_argument.resolve() == fresh_target
    assert pipeline._path_is_historical_exact_state(raw_argument) is True
    assert pipeline.main(
        ["generate", "--preflight-report", str(raw_argument)]
    ) == 2
    assert fresh_target.read_bytes() == b"fresh-amendment-003-preflight\n"
    assert historical_alias.is_symlink()
    assert not (fresh_target.parent / "generation-r2-a003.json").exists()


def test_cli_rejects_arbitrary_preexisting_outputs_without_side_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_root = tmp_path / "custom-output-lane"
    output_root.mkdir()
    sentinels = {
        "preflight": output_root / "preflight.json",
        "free": output_root / "free.yaml",
        "fixed": output_root / "fixed.yaml",
        "generation": output_root / "generation.json",
        "analysis": output_root / "analysis.json",
        "grade": output_root / "grade.json",
        "chain_log": output_root / "custom-chain.runner.log",
        "map_attestation": output_root / "custom-map.attestation.json",
        "adequacy_entry": output_root / "adequacy" / "existing.yaml",
    }
    for name, path in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"existing-{name}\n".encode())
    output_alias = tmp_path / "output-alias.json"
    output_alias.symlink_to(sentinels["preflight"])
    broken_prefix_alias = output_root / "custom-broken.attestation.json"
    broken_prefix_alias.symlink_to(tmp_path / "missing-attestation.json")
    original = {name: path.read_bytes() for name, path in sentinels.items()}

    def unexpected_runtime_check() -> dict:
        raise AssertionError("freshness guard did not run before runtime setup")

    monkeypatch.setattr(
        pipeline, "_require_isolated_exact_cli_runtime", unexpected_runtime_check
    )
    cases = (
        ["preflight", "--output", str(sentinels["preflight"])],
        ["preflight", "--output", str(output_alias)],
        ["generate", "--free-output", str(sentinels["free"])],
        ["generate", "--fixed-output", str(sentinels["fixed"])],
        ["generate", "--output", str(sentinels["generation"])],
        [
            "generate",
            "--adequacy-output-dir", str(sentinels["adequacy_entry"].parent),
        ],
        [
            "run", "--kind", "chain", "--config", str(EXACT_CONFIG),
            "--prefix", str(output_root / "custom-chain"),
            "--run-id", "occupied-chain-prefix",
        ],
        [
            "run", "--kind", "map", "--config", str(EXACT_CONFIG),
            "--prefix", str(output_root / "custom-map"),
            "--run-id", "occupied-map-prefix",
        ],
        [
            "run", "--kind", "map", "--config", str(EXACT_CONFIG),
            "--prefix", str(output_root / "custom-broken"),
            "--run-id", "broken-prefix-symlink",
        ],
        ["analyze", "--output", str(sentinels["analysis"])],
        [
            "grade", "--output", str(sentinels["grade"]),
            "--target-hash", "sha256:" + "0" * 64,
        ],
    )
    for argv in cases:
        assert pipeline.main(argv) == 2

    with pytest.raises(ValueError, match="prefix must be new and fresh"):
        pipeline.run_cobaya_with_attestation(
            kind="map",
            config_path=EXACT_CONFIG,
            prefix=output_root / "custom-map",
            packages_path=tmp_path / "unused-packages",
            cobaya_run=None,
            mpi_processes=1,
            force=False,
            run_id="direct-map-prefix-guard",
        )

    assert {name: path.read_bytes() for name, path in sentinels.items()} == original
    assert output_alias.is_symlink()
    assert broken_prefix_alias.is_symlink()


def test_custom_output_names_are_allowed_only_when_fresh() -> None:
    parser = pipeline._build_parser()
    root = Path("custom-clean-output-lane")
    empty_adequacy = root / "empty-adequacy"
    parsed_generate = parser.parse_args(
        [
            "generate",
            "--free-output", str(root / "free.yaml"),
            "--fixed-output", str(root / "fixed.yaml"),
            "--adequacy-output-dir", str(empty_adequacy),
            "--output", str(root / "generation.json"),
        ]
    )
    parsed_run = parser.parse_args(
        [
            "run", "--kind", "map", "--config", str(EXACT_CONFIG),
            "--prefix", str(root / "map-prefix"),
            "--run-id", "fresh-custom-path-test",
        ]
    )
    assert pipeline._output_freshness_violations(parsed_generate) == []
    assert pipeline._output_freshness_violations(parsed_run) == []


def test_revision_2_path_guard_allows_shared_tracked_inputs() -> None:
    parsed = pipeline._build_parser().parse_args(
        [
            "preflight",
            "--canonical", str(EXACT_CONFIG),
            "--dependency-lock", str(DEPENDENCY_LOCK),
            "--reference-values", str(REFERENCE_CASES),
            "--data-manifest", str(_SCRIPT_DIR / "w0wa_exact_data_manifest.json"),
        ]
    )
    assert pipeline._revision_1_state_argument_violations(parsed) == []


def test_environment_fingerprint_ignores_alternate_launcher_path_only():
    runtime = {
        "python": "3.14.5",
        "executable": "/venv/bin/python",
        "platform": "macOS-test",
        "machine": "arm64",
        "packages": {"demo": "1.0"},
        "runtime_modules": {"demo": {"version": "1.0"}},
        "thread_environment": {"OMP_NUM_THREADS": "3"},
        "native_runtime": {
            "binaries": {
                "python": {
                    "path": "/venv/bin/python",
                    "size_bytes": 100,
                    "sha256": "sha256:" + "1" * 64,
                },
                "mpi4py_extension": {
                    "path": "/venv/site-packages/mpi4py/MPI.so",
                    "size_bytes": 200,
                    "sha256": "sha256:" + "2" * 64,
                },
            },
            "fingerprint": "sha256:" + "3" * 64,
        },
        "import_policy": {
            "venv_root": "/venv",
            "site_package_roots": ["/venv/site-packages"],
        },
        "runtime_closure": {
            "fingerprint": "sha256:" + "4" * 64,
            "distribution_fingerprints": {
                "demo": {
                    "version": "1.0",
                    "fingerprint": "sha256:" + "5" * 64,
                }
            },
        },
    }
    alternate_launcher = copy.deepcopy(runtime)
    alternate_launcher["executable"] = "/venv/bin/python-launch-copy"
    alternate_launcher["native_runtime"]["binaries"]["python"]["path"] = (
        "/venv/bin/python-launch-copy"
    )
    assert pipeline._environment_fingerprint(runtime) == (
        pipeline._environment_fingerprint(alternate_launcher)
    )

    different_bytes = copy.deepcopy(alternate_launcher)
    different_bytes["native_runtime"]["binaries"]["python"]["sha256"] = (
        "sha256:" + "6" * 64
    )
    assert pipeline._environment_fingerprint(runtime) != (
        pipeline._environment_fingerprint(different_bytes)
    )

    different_closure = copy.deepcopy(alternate_launcher)
    different_closure["runtime_closure"]["distribution_fingerprints"]["demo"][
        "fingerprint"
    ] = "sha256:" + "7" * 64
    assert pipeline._environment_fingerprint(runtime) != (
        pipeline._environment_fingerprint(different_closure)
    )


def test_environment_manifest_is_stable_when_planck_switches_clipy_origin(
    tmp_path, monkeypatch
):
    site_root = tmp_path / "exact-venv" / "site-packages"
    distributions = {}
    versions = {
        "camb": "1.6.6",
        "cobaya": "3.6.2",
        "clipy-like": "0.15",
        "act_dr6_lenslike": "1.2.1",
    }
    modules_by_distribution = {
        distribution: module
        for module, distribution in pipeline.RUNTIME_MODULE_DISTRIBUTIONS
    }

    class FakeDistribution:
        def __init__(self, name: str, module_name: str):
            self.metadata = {"Name": name}
            self.version = versions[name]
            self.files = [Path(module_name) / "__init__.py"]

        def locate_file(self, relative):
            return site_root / relative

    for distribution_name, module_name in modules_by_distribution.items():
        origin = site_root / module_name / "__init__.py"
        origin.parent.mkdir(parents=True, exist_ok=True)
        origin.write_text(
            f"__version__ = {versions[distribution_name]!r}\n",
            encoding="utf-8",
        )
        distributions[distribution_name] = FakeDistribution(
            distribution_name, module_name
        )

    monkeypatch.setattr(
        pipeline.importlib.metadata,
        "distribution",
        lambda name: distributions[name],
    )
    monkeypatch.setattr(
        pipeline.importlib.metadata,
        "version",
        lambda name: versions.get(name, "fixture-version"),
    )
    monkeypatch.setattr(
        pipeline,
        "_native_runtime_manifest",
        lambda: {"fingerprint": "sha256:" + "1" * 64},
    )
    monkeypatch.setattr(
        pipeline,
        "_exact_python_import_policy",
        lambda: {"fingerprint": "sha256:" + "2" * 64},
    )
    monkeypatch.setattr(
        pipeline,
        "_exact_runtime_closure_identity",
        lambda: {"fingerprint": "sha256:" + "3" * 64},
    )
    monkeypatch.delitem(sys.modules, "clipy", raising=False)

    before_planck_load = pipeline.environment_manifest()
    installed_origin = (
        site_root / "clipy" / "__init__.py"
    ).resolve()
    assert before_planck_load["runtime_modules"]["clipy"] == {
        "distribution": "clipy-like",
        "version": "0.15",
        "installed": True,
        "origin_scope": "installed_distribution",
        "origin": str(installed_origin),
        "relative_path": "clipy/__init__.py",
        "size_bytes": installed_origin.stat().st_size,
        "sha256": pipeline._hash_file(installed_origin),
    }

    loaded_origin = (
        tmp_path
        / "packages/code/planck/clipy/clipy/__init__.py"
    )
    loaded_origin.parent.mkdir(parents=True)
    loaded_origin.write_text("__version__ = '0.15'\n", encoding="utf-8")
    monkeypatch.setitem(
        sys.modules,
        "clipy",
        SimpleNamespace(
            __file__=str(loaded_origin),
            __version__="0.15",
            __spec__=SimpleNamespace(origin=str(loaded_origin)),
        ),
    )
    assert pipeline.importlib.util.find_spec("clipy").origin == str(loaded_origin)

    after_planck_load = pipeline.environment_manifest()
    assert after_planck_load == before_planck_load
    assert pipeline._environment_fingerprint(after_planck_load) == (
        pipeline._environment_fingerprint(before_planck_load)
    )


def test_loaded_clipy_must_still_come_from_committed_planck_tree(
    tmp_path, monkeypatch
):
    clipy_root = tmp_path / "packages/code/planck/clipy"
    loaded_origin = clipy_root / "clipy/__init__.py"
    loaded_origin.parent.mkdir(parents=True)
    loaded_origin.write_text("__version__ = '0.15'\n", encoding="utf-8")
    installed_origin = tmp_path / "exact-venv/site-packages/clipy/__init__.py"
    installed_origin.parent.mkdir(parents=True)
    installed_origin.write_text("__version__ = '0.15'\n", encoding="utf-8")
    monkeypatch.setattr(
        pipeline,
        "likelihood_runtime_inventory",
        lambda _packages_path: {"clipy": {"root": str(clipy_root)}},
    )
    monkeypatch.setitem(
        sys.modules,
        "clipy",
        SimpleNamespace(__file__=str(installed_origin), __version__="0.15"),
    )
    with pytest.raises(RuntimeError, match="outside packages code tree"):
        pipeline.assert_loaded_likelihood_runtime(tmp_path / "packages")

    monkeypatch.setitem(
        sys.modules,
        "clipy",
        SimpleNamespace(__file__=str(loaded_origin), __version__="0.15"),
    )
    monkeypatch.delitem(sys.modules, "act_dr6_lenslike", raising=False)
    with pytest.raises(RuntimeError, match="ACT reference case did not import"):
        pipeline.assert_loaded_likelihood_runtime(tmp_path / "packages")


def test_exact_runtime_closure_rejects_rogue_distribution(monkeypatch):
    class FakeDistribution:
        def __init__(self, name: str, version: str):
            self.metadata = {"Name": name}
            self.version = version

    expected_inventory = {
        "demo": {
            "installed": True,
            "version": "1.0",
            "fingerprint": "sha256:" + "8" * 64,
        },
        "pip": {
            "installed": True,
            **pipeline.FROZEN_BOOTSTRAP_DISTRIBUTIONS["pip"],
        },
    }
    monkeypatch.setattr(
        pipeline,
        "_distribution_inventory",
        lambda name: expected_inventory[name],
    )
    monkeypatch.setattr(
        pipeline,
        "_site_packages_ownership_inventory",
        lambda **_kwargs: {
            "passed": True,
            "reasons": [],
            "fingerprint": "sha256:" + "9" * 64,
        },
    )
    expected = [FakeDistribution("demo", "1.0"), FakeDistribution("pip", "26.1.1")]
    monkeypatch.setattr(
        pipeline.importlib.metadata,
        "distributions",
        lambda: list(expected),
    )
    accepted = pipeline._exact_runtime_closure_identity(
        {"demo": "1.0"}, {"demo"}
    )
    assert accepted["passed"] is True, accepted["reasons"]

    monkeypatch.setattr(
        pipeline.importlib.metadata,
        "distributions",
        lambda: [*expected, FakeDistribution("rogue-addon", "9.9")],
    )
    rejected = pipeline._exact_runtime_closure_identity(
        {"demo": "1.0"}, {"demo"}
    )
    assert rejected["passed"] is False
    assert "exact_installed_distributions_unregistered:rogue-addon" in (
        rejected["reasons"]
    )


def test_site_packages_ownership_rejects_loose_importable_module(
    tmp_path, monkeypatch
):
    site_root = tmp_path / "site-packages"
    site_root.mkdir()
    owned = site_root / "demo.py"
    owned.write_text("VALUE = 1\n", encoding="utf-8")
    rogue = site_root / "rogue_optional.py"
    rogue.write_text("VALUE = 'unregistered'\n", encoding="utf-8")

    class FakeDistribution:
        metadata = {"Name": "demo"}
        version = "1.0"
        files = [Path("demo.py")]

        @staticmethod
        def locate_file(relative):
            return site_root / relative

    monkeypatch.setattr(
        pipeline.importlib.metadata,
        "distribution",
        lambda _name: FakeDistribution(),
    )
    inventory = pipeline._site_packages_ownership_inventory(
        allowed_distributions=["demo"],
        site_roots=[site_root],
    )
    assert inventory["passed"] is False
    assert inventory["unowned_import_files"] == ["0:rogue_optional.py"]
    assert (
        "exact_site_packages_import_file_unowned:0:rogue_optional.py"
        in inventory["reasons"]
    )


def test_source_owned_pycache_is_stable_but_sourceless_pycache_is_fatal(
    tmp_path, monkeypatch
):
    site_root = tmp_path / "site-packages"
    site_root.mkdir()
    source = site_root / "demo.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    class FakeDistribution:
        metadata = {"Name": "demo"}
        version = "1.0"
        files = [Path("demo.py")]

        @staticmethod
        def locate_file(relative):
            return site_root / relative

    monkeypatch.setattr(
        pipeline.importlib.metadata,
        "distribution",
        lambda _name: FakeDistribution(),
    )
    baseline = pipeline._site_packages_ownership_inventory(
        allowed_distributions=["demo"],
        site_roots=[site_root],
    )
    assert baseline["passed"] is True
    assert baseline["generated_bytecode_policy"] == (
        pipeline.GENERATED_BYTECODE_CACHE_POLICY
    )

    owned_cache = Path(importlib.util.cache_from_source(str(source)))
    owned_cache.parent.mkdir()
    owned_cache.write_bytes(b"normal derived cache bytes")
    with_owned_cache = pipeline._site_packages_ownership_inventory(
        allowed_distributions=["demo"],
        site_roots=[site_root],
    )
    assert with_owned_cache == baseline

    owned_cache.write_bytes(b"refreshed normal derived cache bytes")
    after_refresh = pipeline._site_packages_ownership_inventory(
        allowed_distributions=["demo"],
        site_roots=[site_root],
    )
    assert after_refresh == baseline

    sourceless_cache = Path(
        importlib.util.cache_from_source(str(site_root / "orphan.py"))
    )
    sourceless_cache.write_bytes(b"unowned bytecode")
    rejected = pipeline._site_packages_ownership_inventory(
        allowed_distributions=["demo"],
        site_roots=[site_root],
    )
    assert rejected["passed"] is False
    assert rejected["unowned_generated_bytecode"] == [
        f"0:{sourceless_cache.relative_to(site_root).as_posix()}"
    ]
    assert any(
        reason.startswith("exact_site_packages_generated_bytecode_unowned:")
        for reason in rejected["reasons"]
    )


def test_paper_fidelity_ess_overlay_rejects_999_point_9():
    assert pipeline.chain_diagnostic_failures(1.001, 1_000.0) == []
    assert pipeline.chain_diagnostic_failures(1.001, 999.9) == [
        "bulk_ess_below_paper_fidelity_1000"
    ]


def test_exact_analyze_and_grade_do_not_call_legacy_significance_builder():
    assert "build_conclusion_attestations" not in inspect.getsource(
        pipeline.build_exact_analysis_manifest
    )
    assert "build_conclusion_attestations" not in inspect.getsource(
        pipeline.grade_exact_analysis
    )


def test_environment_selected_protocol_authority_cannot_bypass_empty_registry(
    tmp_path, monkeypatch
):
    private_key = Ed25519PrivateKey.generate()
    public_key_path = tmp_path / "protocol-reviewer-public.pem"
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv(
        pipeline.PROTOCOL_ADJUDICATION_PUBLIC_KEY_ENV, str(public_key_path)
    )
    monkeypatch.setenv(
        pipeline.PROTOCOL_ADJUDICATION_AUTHORITY_ENV, "external-protocol-board"
    )
    monkeypatch.setattr(
        pipeline, "TRUSTED_PROTOCOL_AUTHORITY_REGISTRY", {}, raising=False
    )
    report_path = tmp_path / "protocol-review.md"
    report_path.write_text("Independent protocol adjudication.\n", encoding="utf-8")
    unsigned = {
        "schema_version": 1,
        "algorithm": "ed25519",
        "authority_id": "external-protocol-board",
        "authority_key_sha256": pipeline._hash_file(public_key_path),
        "source": "independent_protocol_authority",
        "status": "authorized",
        "run_id": "externally-adjudicated-run",
        "target_hash": pipeline.PREREGISTERED_TARGET_COMMITMENT,
        "claim_scope": pipeline.EXACT_CLAIM_SCOPE,
        "known_target_reproduction_authorized": True,
        "protocol_status": dict(pipeline.RESEARCH_ALPHA_PROTOCOL_STATUS),
        "adjudicator": "independent-reviewer",
        "rationale_artifact": {
            "path": str(report_path.resolve()),
            "sha256": pipeline._hash_file(report_path),
            "size_bytes": report_path.stat().st_size,
        },
        "prohibited_conclusions": [
            "LambdaCDM_rejected",
            "dynamic_dark_energy_discovered",
        ],
    }
    payload = {
        **unsigned,
        "signature": base64.b64encode(
            private_key.sign(pipeline._canonical_json(unsigned))
        ).decode("ascii"),
    }
    adjudication_path = tmp_path / "adjudication.json"
    _write_json(adjudication_path, payload)
    rejected_unregistered = pipeline.verify_external_protocol_adjudication(
        adjudication_path,
        expected_run_id="externally-adjudicated-run",
    )
    assert rejected_unregistered["passed"] is False
    assert any(
        token in reason
        for reason in rejected_unregistered["reasons"]
        for token in ("registry", "preregistered", "untrusted")
    ), rejected_unregistered["reasons"]

    payload["signature"] = base64.b64encode(b"local-hmac").decode("ascii")
    _write_json(adjudication_path, payload)
    rejected = pipeline.verify_external_protocol_adjudication(adjudication_path)
    assert rejected["passed"] is False
    assert any("ed25519_unverified" in reason for reason in rejected["reasons"])


def test_exact_theta_mc_parameterization_is_accepted_by_camb():
    from cobaya.model import get_model

    info = yaml.safe_load(EXACT_CONFIG.read_text(encoding="utf-8"))
    info["likelihood"] = {"h0_probe": {"external": "lambda H0: 0.0"}}
    info.pop("sampler", None)
    info["packages_path"] = str(_REPO_ROOT / "backend" / "packages")
    model = get_model(info)
    result = model.logposterior(
        {
            "ombh2": 0.02237,
            "omch2": 0.12,
            "theta_MC_100": 1.04109,
            "tau": 0.055,
            "ns": 0.965,
            "logA": 3.05,
            "w": -0.9,
            "wa": -0.3,
        }
    )
    derived = dict(zip(model.parameterization.derived_params(), result.derived))
    assert math.isfinite(result.logpost)
    assert 40 < float(derived["H0"]) < 100


def test_adequacy_plan_freezes_predictive_rule_and_real_pantheon_variant(tmp_path):
    from app.services import research_alpha_manifest as research_alpha_consumer

    config = yaml.safe_load(EXACT_CONFIG.read_text(encoding="utf-8"))
    record = pipeline.write_model_adequacy_plan(
        config,
        tmp_path / "adequacy",
        _REPO_ROOT / "backend" / "packages",
    )
    plan = json.loads(Path(record["path"]).read_text(encoding="utf-8"))
    ppc = json.loads(
        Path(plan["predictive_checks"]["path"]).read_text(encoding="utf-8")
    )
    assert ppc["acceptance_rule"] == {
        "tail_probability": "(1 + count(T_rep >= T_observed)) / (replicates + 1)",
        "lower_inclusive": 0.01,
        "upper_inclusive": 0.99,
        "all_discrepancies_must_pass": True,
        "missing_or_nonfinite": "fail",
    }
    assert all(
        check["minimum_replicates"] == 400 and len(check["seed_entropy"]) == 4
        for check in ppc["checks"].values()
    )
    injections = json.loads(
        Path(plan["injection_recovery"]["path"]).read_text(encoding="utf-8")
    )
    assert injections["joint_parameters"] == ["w", "wa"]
    assert injections["joint_region"]["threshold_inclusive"] == pytest.approx(
        5.991464547107979
    )
    assert [item["simulation_seed"] for item in injections["fiducials"]] == [
        310001,
        310003,
        310019,
    ]
    assert injections["standardized_bias"]["maximum_aggregate_exclusive"] == 0.30

    variant = plan["pantheon_covariance_variant"]
    assert variant["variant"] == "official_statistical_only"
    assert variant["construction"] == (
        "official Pantheon+SH0ES_STATONLY.cov copied unmodified"
    )
    assert variant["source_data"]["rows_before_selection"] == 1701
    assert variant["source_data"]["rows_after_selection"] == 1590
    assert variant["source_covariance"]["sha256"] == (
        pipeline.PANTHEON_STATONLY_SHA256
    )
    covariance_path = Path(variant["generated_covariance"]["path"])
    with covariance_path.open(encoding="utf-8") as handle:
        assert int(handle.readline()) == 1701
        assert float(handle.readline()) > 0
    assert pipeline._hash_file(covariance_path) == pipeline.PANTHEON_STATONLY_SHA256
    pantheon_config = yaml.safe_load(
        Path(plan["configs"]["pantheonplus_covariance"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    assert pantheon_config["likelihood"]["sn.pantheonplus"][
        "dataset_file"
    ] == variant["generated_dataset"]["path"]

    # Exercise the production Research Alpha consumer against the artifacts
    # emitted by this producer, rather than a hand-built lookalike fixture.
    consumed_artifact, consumed_plan, consumed_ppc, consumed_injections = (
        research_alpha_consumer._exact_plan_artifact_payload(
            {"model_adequacy_plan": record}
        )
    )
    assert consumed_artifact["sha256"] == record["sha256"]
    assert consumed_plan["plan_sha256"] == plan["plan_sha256"]
    assert consumed_ppc == ppc
    assert consumed_injections == injections


def test_preflight_binds_config_data_environment_and_reference_values(
    tmp_path, monkeypatch
):
    canonical = tmp_path / "exact.yaml"
    canonical.write_bytes(EXACT_CONFIG.read_bytes())
    inventory = _synthetic_inventory()
    adequacy_inventory = {
        "packages_path": "/synthetic/packages",
        "complete": True,
        "missing": [],
        "groups": {
            "planck_NPIPE_highl_CamSpec.TTTEEE": {
                "versions": ["v1"],
                "files": [
                    {
                        "path": "data/planck_NPIPE_CamSpec/version.dat",
                        "size_bytes": 3,
                        "sha256": "sha256:" + "6" * 64,
                    }
                ],
            }
        },
        "fingerprint": "sha256:" + "5" * 64,
    }
    monkeypatch.setattr(pipeline, "build_data_inventory", lambda _: inventory)
    monkeypatch.setattr(
        pipeline,
        "build_adequacy_data_inventory",
        lambda _: dict(adequacy_inventory),
    )
    source_state = {
        "schema_version": 1,
        "repository_root": str(_REPO_ROOT),
        "head_commit": "1" * 40,
        "head_tree": "2" * 40,
        "branch": "codex/w0wa-strict-a-readiness",
        "files": [],
        "status_entries": [],
        "clean": True,
        "passed": True,
        "reasons": [],
        "fingerprint": "sha256:" + "3" * 64,
    }
    monkeypatch.setattr(
        pipeline,
        "build_source_state_inventory",
        lambda: dict(source_state),
    )
    monkeypatch.setattr(
        pipeline,
        "exact_environment_inventory",
        lambda lock, wheels: _environment_pass(Path(lock), Path(wheels)),
    )
    synthetic_trusted_data = {
        "passed": True,
        "reasons": [],
        "path": str((tmp_path / "trusted-data.json").resolve()),
        "sha256": "sha256:" + "7" * 64,
        "archive_root": str(tmp_path.resolve()),
        "source_archives": [],
        "overall_inventory_fingerprint": inventory["fingerprint"],
        "group_fingerprints": {},
    }
    monkeypatch.setattr(
        pipeline,
        "verify_trusted_data_manifest",
        lambda *_, **__: dict(synthetic_trusted_data),
    )
    live_calls = []

    def _live_reference(**kwargs):
        live_calls.append(kwargs)
        return {
            name: next(
                case["values"][name]["expected_chi2"]
                for case in _reference_payload(canonical, inventory)["cases"]
                if name in case["values"]
            )
            for name in kwargs["likelihood_names"]
        }

    monkeypatch.setattr(pipeline, "evaluate_reference_likelihoods", _live_reference)
    reference_path = tmp_path / "reference.json"
    _write_json(reference_path, _reference_payload(canonical, inventory))
    monkeypatch.setattr(
        pipeline, "TRUSTED_REFERENCE_SPEC_SHA256", pipeline._hash_file(reference_path)
    )
    monkeypatch.setattr(
        pipeline,
        "assert_loaded_likelihood_runtime",
        lambda _: {"fixture": True},
    )
    synthetic_likelihood_code = {
        "passed": True,
        "reasons": [],
        "path": str((tmp_path / "likelihood-code.json").resolve()),
        "sha256": "sha256:" + "8" * 64,
        "payload": {"fixture": True},
    }
    monkeypatch.setattr(
        pipeline,
        "verify_likelihood_code_manifest",
        lambda *_: dict(synthetic_likelihood_code),
    )

    report = pipeline.build_preflight_report(
        canonical_config_path=canonical,
        packages_path=tmp_path / "packages",
        dependency_lock_path=DEPENDENCY_LOCK,
        wheels_path=tmp_path / "wheels",
        reference_values_path=reference_path,
    )
    assert report["passed"] is True
    assert report["adequacy_data"] == adequacy_inventory
    assert report["source_state"] == source_state
    assert len(live_calls) == len(_reference_payload(canonical, inventory)["cases"])
    assert pipeline._verify_self_hash(report, "preflight_sha256") is True
    report_path = tmp_path / "preflight.json"
    _write_json(report_path, report)
    verified = pipeline.verify_preflight_receipt(
        report_path,
        canonical_config_path=canonical,
        packages_path=tmp_path / "packages",
    )
    assert verified["passed"] is True
    assert len(live_calls) == 2 * len(
        _reference_payload(canonical, inventory)["cases"]
    )

    # A receipt self-hash is integrity metadata, not an authority signature.
    # Re-hashing a forged copy of the recorded live values must not bypass a
    # fresh execution of the source-pinned reference cases.
    forged_report = json.loads(report_path.read_text(encoding="utf-8"))
    forged_observed = forged_report["reference_likelihood_values"][
        "live_observed_chi2_by_case"
    ]
    first_case = next(iter(forged_observed))
    first_component = next(iter(forged_observed[first_case]))
    forged_observed[first_case][first_component] += 0.5
    forged_report = pipeline._with_self_hash(forged_report, "preflight_sha256")
    _write_json(report_path, forged_report)
    forged_verification = pipeline.verify_preflight_receipt(
        report_path,
        canonical_config_path=canonical,
        packages_path=tmp_path / "packages",
    )
    assert forged_verification["passed"] is False
    assert any(
        "reference" in reason for reason in forged_verification["reasons"]
    ), forged_verification["reasons"]

    _write_json(report_path, report)

    missing_adequacy = {
        **adequacy_inventory,
        "complete": False,
        "missing": [
            "planck_NPIPE_highl_CamSpec.TTTEEE:"
            "data/planck_NPIPE_CamSpec/version.dat"
        ],
        "fingerprint": "sha256:" + "4" * 64,
    }
    monkeypatch.setattr(
        pipeline,
        "build_adequacy_data_inventory",
        lambda _: dict(missing_adequacy),
    )
    withheld = pipeline.build_preflight_report(
        canonical_config_path=canonical,
        packages_path=tmp_path / "packages",
        dependency_lock_path=DEPENDENCY_LOCK,
        wheels_path=tmp_path / "wheels",
        reference_values_path=reference_path,
    )
    assert withheld["status"] == "WITHHELD"
    assert (
        "adequacy_data:planck_npipe_camspec_inventory_incomplete"
        in withheld["failures"]
    )

    monkeypatch.setattr(
        pipeline,
        "build_adequacy_data_inventory",
        lambda _: dict(adequacy_inventory),
    )

    changed = yaml.safe_load(canonical.read_text(encoding="utf-8"))
    changed["sampler"]["mcmc"]["Rminus1_stop"] = 0.02
    canonical.write_text(yaml.safe_dump(changed), encoding="utf-8")
    drifted = pipeline.verify_preflight_receipt(
        report_path,
        canonical_config_path=canonical,
        packages_path=tmp_path / "packages",
    )
    assert "preflight_config_hash_drift" in drifted["reasons"]


def test_trusted_data_manifest_binds_official_archives_and_installed_bytes():
    inventory = pipeline.build_data_inventory(_REPO_ROOT / "backend" / "packages")
    adequacy_inventory = pipeline.build_adequacy_data_inventory(
        _REPO_ROOT / "backend" / "packages"
    )
    verified = pipeline.verify_trusted_data_manifest(
        pipeline.TRUSTED_DATA_MANIFEST_PATH,
        inventory=inventory,
        adequacy_inventory=adequacy_inventory,
    )
    payload = json.loads(
        pipeline.TRUSTED_DATA_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    npipe_name = "planck_NPIPE_highl_CamSpec.TTTEEE"
    committed_npipe = payload["adequacy_groups"][npipe_name]
    assert committed_npipe["file_count"] == 15
    assert committed_npipe["total_size_bytes"] == 510_767_254
    assert committed_npipe["fingerprint"] == (
        "sha256:d174212b9b0d88b1dc816b74ac117f00516e418c0fc456df4870968080c9a877"
    )
    assert len(committed_npipe["files"]) == 15
    assert {item["filename"] for item in payload["source_archives"]} == set(
        pipeline.EXPECTED_SOURCE_ARCHIVES
    )

    # Ordinary CI intentionally does not fetch the multi-GB exact stack. It
    # still exercises the same fail-closed verifier and must never silently
    # skip this test. On a provisioned formal-run host, require byte-for-byte
    # success and the complete physical NPIPE closure.
    physical_assets_complete = (
        inventory["complete"]
        and adequacy_inventory["complete"]
        and all(
            (pipeline.TRUSTED_SOURCE_ARCHIVE_ROOT / item["filename"]).is_file()
            for item in payload["source_archives"]
        )
    )
    if physical_assets_complete:
        assert verified["passed"] is True, verified["reasons"]
        assert len(verified["source_archives"]) == 3
        assert len(verified["source_vcs"]) == 3
        assert adequacy_inventory["groups"][npipe_name]["files"] == (
            committed_npipe["files"]
        )
        assert verified["data_quality_checks"]["sn.pantheonplus"] == {
            "data_rows": 1701,
            "data_columns": 47,
            "unique_CID": 1543,
            "redshift_column": "zHD",
            "redshift_cut": ">0.01",
            "rows_after_redshift_cut": 1590,
            "covariance_shape": [1701, 1701],
        }
    else:
        assert verified["passed"] is False
        assert verified["reasons"]

    forged = dict(inventory)
    forged["fingerprint"] = "sha256:" + "0" * 64
    rejected = pipeline.verify_trusted_data_manifest(
        pipeline.TRUSTED_DATA_MANIFEST_PATH,
        inventory=forged,
        adequacy_inventory=adequacy_inventory,
    )
    assert rejected["passed"] is False
    assert "trusted_data_overall_fingerprint_mismatch" in rejected["reasons"]

    forged_adequacy = dict(adequacy_inventory)
    forged_adequacy["fingerprint"] = "sha256:" + "0" * 64
    rejected_adequacy = pipeline.verify_trusted_data_manifest(
        pipeline.TRUSTED_DATA_MANIFEST_PATH,
        inventory=inventory,
        adequacy_inventory=forged_adequacy,
    )
    assert rejected_adequacy["passed"] is False
    assert (
        "trusted_adequacy_inventory_fingerprint_mismatch"
        in rejected_adequacy["reasons"]
    )


def test_adequacy_inventory_excludes_untrusted_generated_camspec_cache(tmp_path):
    patterns = pipeline.ADEQUACY_DATA_ASSETS[
        "planck_NPIPE_highl_CamSpec.TTTEEE"
    ]
    assert len(patterns) == 15
    assert all("*" not in pattern for pattern in patterns)
    for pattern in patterns:
        path = tmp_path / pattern
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"v1\n" if path.name == "version.dat" else b"official")

    before = pipeline.build_adequacy_data_inventory(tmp_path)
    cache = (
        tmp_path
        / "data/planck_NPIPE_CamSpec/CamSpec_NPIPE/"
        "CamSpec_NPIPE_12_6_cl_covinv_untrusted.npy"
    )
    cache.write_bytes(b"not-an-official-data-product")
    after = pipeline.build_adequacy_data_inventory(tmp_path)

    assert before["complete"] is True
    assert before == after
    files = before["groups"]["planck_NPIPE_highl_CamSpec.TTTEEE"]["files"]
    assert len(files) == 15
    assert all("_covinv_" not in item["path"] for item in files)


def test_security_roll_forward_pins_patched_setuptools_and_stays_withheld():
    lock_pins, lock_reasons = pipeline._parse_exact_version_lock(DEPENDENCY_LOCK)
    assert lock_reasons == []
    assert lock_pins["setuptools"] == "83.0.0"

    payload = json.loads(WHEEL_MANIFEST.read_text(encoding="utf-8"))
    setuptools_records = [
        record for record in payload["wheels"] if record["project"] == "setuptools"
    ]
    assert setuptools_records == [
        {
            "filename": "setuptools-83.0.0-py3-none-any.whl",
            "project": "setuptools",
            "sha256": (
                "sha256:29b23c360f22f414dc7336bb39178cc7"
                "bcbf6021ed2733cde173f09dba19abb3"
            ),
            "size_bytes": 1_008_090,
            "source_api": "https://pypi.org/pypi/setuptools/83.0.0/json",
            "upload_time_iso_8601": "2026-07-04T15:31:20.885481Z",
            "url": (
                "https://files.pythonhosted.org/packages/5d/40/"
                "e1e72872c6354b306daef1703549e8e83b4d43cfea356311bf722a043752/"
                "setuptools-83.0.0-py3-none-any.whl"
            ),
            "version": "83.0.0",
        }
    ]
    assert pipeline.EXACT_ENVIRONMENT_REVISION["status"] == (
        "WITHHELD_PENDING_FRESH_PREFLIGHT_AND_SCIENCE_REGRESSION"
    )


def test_trusted_producer_hashes_bind_canonical_and_independent_scripts():
    from app.services.w0wa_exact_contract import TRUSTED_CODE_SHA256

    for filename in (
        "canonical_full_likelihood_evidence.py",
        "independent_w0wa_postprocess.py",
    ):
        assert pipeline._hash_file(_SCRIPT_DIR / filename) == (
            TRUSTED_CODE_SHA256[filename]
        )


def test_wheel_manifest_freezes_complete_lock_and_missing_archives_fail_closed(
    tmp_path, monkeypatch
):
    lock_pins, lock_reasons = pipeline._parse_exact_version_lock(DEPENDENCY_LOCK)
    assert lock_reasons == []

    with monkeypatch.context() as incompatible_host:
        incompatible_host.setattr(pipeline.sys, "version", "0.0 fixture host")
        incompatible_host.setattr(
            pipeline, "sys_tags", lambda: iter(["py0-none-incompatible"])
        )
        _, incompatible_reasons = pipeline._trusted_wheel_manifest(lock_pins)
    assert incompatible_reasons == [
        "exact_wheel_manifest_python_version_mismatch",
        "exact_wheel_manifest_platform_tags_mismatch",
    ]

    frozen_payload = json.loads(WHEEL_MANIFEST.read_text(encoding="utf-8"))
    with monkeypatch.context() as frozen_host:
        frozen_host.setattr(
            pipeline.sys, "version", frozen_payload["python_version"]
        )
        frozen_host.setattr(
            pipeline,
            "sys_tags",
            lambda: iter(frozen_payload["platform_tags"]),
        )
        manifest, manifest_reasons = pipeline._trusted_wheel_manifest(lock_pins)
    assert manifest_reasons == []
    assert manifest is not None
    assert len(manifest["wheels"]) == len(lock_pins) == 52

    empty_wheelhouse = tmp_path / "empty-wheelhouse"
    empty_wheelhouse.mkdir()
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        monkeypatch.setenv(name, "3")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "untrusted-import-root"))
    environment = pipeline.exact_environment_inventory(
        DEPENDENCY_LOCK, empty_wheelhouse
    )
    missing = [
        reason
        for reason in environment["reasons"]
        if reason.startswith("exact_wheel_missing:")
    ]
    assert environment["passed"] is False
    assert any(
        "pythonpath" in reason.lower() or "import_policy" in reason
        for reason in environment["reasons"]
    ), environment["reasons"]
    assert len(missing) == 52
    assert environment["wheel_manifest"]["sha256"] == pipeline._hash_file(
        WHEEL_MANIFEST
    )

    forged_manifest = tmp_path / "forged-wheel-manifest.json"
    payload = json.loads(WHEEL_MANIFEST.read_text(encoding="utf-8"))
    payload["wheels"][0]["size_bytes"] += 1
    _write_json(forged_manifest, payload)
    monkeypatch.setattr(pipeline, "TRUSTED_WHEEL_MANIFEST_PATH", forged_manifest)
    _, rejected_reasons = pipeline._trusted_wheel_manifest(lock_pins)
    assert "exact_wheel_manifest_hash_not_preregistered" in rejected_reasons


def test_exact_environment_rejects_self_rehashed_installed_distribution(
    tmp_path, monkeypatch
):
    """Installed RECORD is not trusted when its bytes differ from the wheel."""

    def record_line(path: str, content: bytes) -> str:
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(content).digest()
        ).decode("ascii").rstrip("=")
        return f"{path},sha256={digest},{len(content)}"

    project = "demo"
    version = "1.0"
    dist_info = "demo-1.0.dist-info"
    trusted_files = {
        "demo/__init__.py": b'VALUE = "trusted"\n',
        f"{dist_info}/METADATA": (
            b"Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n\n"
        ),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: regression-test\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record_path = f"{dist_info}/RECORD"
    trusted_record = "\n".join(
        [record_line(path, content) for path, content in trusted_files.items()]
        + [f"{record_path},,"]
    ).encode("utf-8") + b"\n"
    trusted_files[record_path] = trusted_record

    wheel_root = tmp_path / "wheels"
    wheel_root.mkdir()
    wheel_path = wheel_root / "demo-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        for relative, content in trusted_files.items():
            archive.writestr(relative, content)

    install_root = tmp_path / "site-packages"
    for relative, content in trusted_files.items():
        destination = install_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    forged_payload = b'VALUE = "forged"\n'
    (install_root / "demo/__init__.py").write_bytes(forged_payload)
    # Make the installed RECORD internally self-consistent. A check that trusts
    # this mutable file instead of the frozen wheel would incorrectly pass.
    forged_record = "\n".join(
        [
            record_line(
                path,
                forged_payload if path == "demo/__init__.py" else content,
            )
            for path, content in trusted_files.items()
            if path != record_path
        ]
        + [f"{record_path},,"]
    ).encode("utf-8") + b"\n"
    (install_root / record_path).write_bytes(forged_record)

    class FakeDistribution:
        metadata = {"Name": project}
        files = [Path(path) for path in trusted_files]

        @property
        def version(self):
            return version

        @staticmethod
        def locate_file(relative):
            return install_root / relative

        @staticmethod
        def read_text(filename):
            path = install_root / dist_info / filename
            return path.read_text(encoding="utf-8") if path.is_file() else None

    lock_path = tmp_path / "requirements.txt"
    lock_path.write_text("demo==1.0\n", encoding="utf-8")
    wheel_manifest = {
        "wheels": [
            {
                "project": project,
                "version": version,
                "filename": wheel_path.name,
                "sha256": pipeline._hash_file(wheel_path),
                "size_bytes": wheel_path.stat().st_size,
            }
        ]
    }
    monkeypatch.setattr(
        pipeline, "_parse_exact_version_lock", lambda _: ({project: version}, [])
    )
    monkeypatch.setattr(
        pipeline, "_installed_runtime_closure", lambda _: ({project}, [])
    )
    monkeypatch.setattr(
        pipeline,
        "_trusted_wheel_manifest",
        lambda _: (wheel_manifest, []),
    )
    monkeypatch.setattr(
        pipeline.importlib.metadata,
        "distribution",
        lambda name: FakeDistribution(),
    )
    monkeypatch.setattr(
        pipeline, "TRUSTED_DEPENDENCY_LOCK_SHA256", pipeline._hash_file(lock_path)
    )
    monkeypatch.setattr(
        pipeline, "assert_locked_camb_runtime", lambda: {"fixture": True}
    )
    monkeypatch.setattr(
        pipeline,
        "environment_manifest",
        lambda: {
            "thread_environment": {
                "OMP_NUM_THREADS": "3",
                "MKL_NUM_THREADS": "3",
                "OPENBLAS_NUM_THREADS": "3",
            },
            "native_runtime": {"passed": True, "reasons": []},
        },
    )

    environment = pipeline.exact_environment_inventory(lock_path, wheel_root)
    assert environment["passed"] is False
    assert any(
        reason.startswith("installed_wheel_binding:demo:")
        for reason in environment["reasons"]
    ), environment["reasons"]


def test_source_state_inventory_binds_scoped_git_tree_and_fails_closed_when_dirty():
    state = pipeline.build_source_state_inventory()
    assert state["schema_version"] == 2
    assert state["head_commit"] and len(state["head_commit"]) == 40
    assert state["head_tree"] and len(state["head_tree"]) == 40
    assert state["base_commit"] == pipeline.TRUSTED_SOURCE_BASE_COMMIT
    assert isinstance(state["detached"], bool)
    assert {item["path"] for item in state["files"]} == set(
        pipeline.SOURCE_STATE_PATHS
    )
    assert all(
        item["sha256"].startswith("sha256:") and item["size_bytes"] > 0
        for item in state["files"]
    )
    if state["clean"] and not state["reasons"]:
        assert state["passed"] is True, state["reasons"]
    else:
        assert state["passed"] is False
        assert state["reasons"]


def test_amendment_003_source_base_is_current_ancestor_and_old_base_is_rejected():
    expected_base = "ebb2f8d8eef202dbe8a8a85b0cb753829f3899a2"
    unrelated_old_base = "f9efb4ac6f7850d4c7739ac038d08beb37ea785e"
    assert pipeline.TRUSTED_SOURCE_BASE_COMMIT == expected_base
    assert pipeline.TRUSTED_SOURCE_BASE_COMMIT != unrelated_old_base
    assert subprocess.run(
        [
            "git", "-C", str(_REPO_ROOT), "merge-base", "--is-ancestor",
            expected_base, "HEAD",
        ],
        check=False,
    ).returncode == 0


def test_source_state_accepts_clean_descendant_branch_and_detached_head(
    tmp_path, monkeypatch
):
    from app.services import research_alpha_manifest as research_consumer

    repository = tmp_path / "repo"
    repository.mkdir()
    for logical_path in pipeline.SOURCE_STATE_PATHS:
        path = repository / logical_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"frozen source fixture: {logical_path}\n", encoding="utf-8")

    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repository), *args],
            capture_output=True,
            check=True,
            text=True,
        )
        return completed.stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Scientific Test")
    git("config", "user.email", "science-test@example.invalid")
    git("add", ".")
    git("commit", "-m", "frozen base")
    base_commit = git("rev-parse", "HEAD")
    marker = repository / "descendant-marker.txt"
    marker.write_text("reviewed descendant\n", encoding="utf-8")
    git("add", marker.name)
    git("commit", "-m", "descendant")

    monkeypatch.setattr(pipeline, "BACKEND_ROOT", repository / "backend")
    monkeypatch.setattr(pipeline, "TRUSTED_SOURCE_BASE_COMMIT", base_commit)
    monkeypatch.setattr(
        research_consumer, "TRUSTED_SOURCE_BASE_COMMIT", base_commit
    )

    branch_state = pipeline.build_source_state_inventory()
    assert branch_state["branch"] == "main"
    assert branch_state["detached"] is False
    assert branch_state["base_commit"] == base_commit
    assert branch_state["passed"] is True, branch_state["reasons"]
    research_consumer._validate_exact_source_state(branch_state)

    git("checkout", "--detach", "HEAD")
    detached_state = pipeline.build_source_state_inventory()
    assert detached_state["detached"] is True
    assert detached_state["passed"] is True, detached_state["reasons"]
    research_consumer._validate_exact_source_state(detached_state)

    dirty_path = repository / pipeline.SOURCE_STATE_PATHS[0]
    dirty_path.write_text(
        dirty_path.read_text(encoding="utf-8") + "dirty\n", encoding="utf-8"
    )
    dirty_state = pipeline.build_source_state_inventory()
    assert dirty_state["passed"] is False
    assert "source_tree_has_changes" in dirty_state["reasons"]
    git("checkout", "--", pipeline.SOURCE_STATE_PATHS[0])

    git("checkout", "--orphan", "unrelated")
    git("commit", "--allow-empty", "-m", "unrelated root")
    unrelated_state = pipeline.build_source_state_inventory()
    assert unrelated_state["clean"] is True
    assert unrelated_state["passed"] is False
    assert any(
        "base" in reason and ("ancestor" in reason or "descendant" in reason)
        for reason in unrelated_state["reasons"]
    ), unrelated_state["reasons"]


def test_reference_values_fail_closed_on_partial_or_forged_likelihood(
    tmp_path, monkeypatch
):
    inventory = _synthetic_inventory()
    payload = _reference_payload(EXACT_CONFIG, inventory)
    payload["cases"].pop(0)
    shifted_name = pipeline.REQUIRED_LIKELIHOODS[1]
    shifted_case = next(
        case for case in payload["cases"] if shifted_name in case["values"]
    )
    shifted_case["values"][shifted_name]["observed"] = shifted_case["values"][
        shifted_name
    ]["expected_chi2"]
    reference_path = tmp_path / "reference.json"
    _write_json(reference_path, payload)
    monkeypatch.setattr(
        pipeline, "TRUSTED_REFERENCE_SPEC_SHA256", pipeline._hash_file(reference_path)
    )
    monkeypatch.setattr(
        pipeline,
        "assert_loaded_likelihood_runtime",
        lambda _: {"fixture": True},
    )

    def evaluator(**kwargs):
        result = {}
        for name in kwargs["likelihood_names"]:
            expected = next(
                case["values"][name]["expected_chi2"]
                for case in payload["cases"]
                if name in case["values"]
            )
            result[name] = expected + (1.0 if name == shifted_name else 0.0)
        return result

    result = pipeline.verify_reference_values(
        reference_path,
        canonical_config=yaml.safe_load(EXACT_CONFIG.read_text(encoding="utf-8")),
        packages_path=tmp_path / "packages",
        config_sha256=pipeline._hash_file(EXACT_CONFIG),
        data_fingerprint=inventory["fingerprint"],
        evaluator=evaluator,
    )
    assert result["passed"] is False
    assert any(reason.startswith("reference_likelihoods_missing:") for reason in result["reasons"])
    assert "reference_file_must_not_supply_observed_values" in result["reasons"]
    assert any(
        reason.endswith(f":{shifted_name}")
        and reason.startswith("reference_value_outside_tolerance:")
        for reason in result["reasons"]
    )


def test_formal_runner_rejects_wrong_resources_and_existing_prefix(
    tmp_path, monkeypatch
):
    kwargs = {
        "kind": "chain",
        "config_path": EXACT_CONFIG,
        "prefix": tmp_path / "formal",
        "packages_path": tmp_path / "packages",
        "cobaya_run": "does-not-run",
        "mpi_processes": 4,
        "force": True,
        "evidence_class": "formal_candidate",
        "run_id": "formal-test-run",
    }
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.delenv("MKL_NUM_THREADS", raising=False)
    monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)
    with pytest.raises(ValueError, match="OMP_NUM_THREADS=3"):
        pipeline.run_cobaya_with_attestation(**kwargs)

    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        monkeypatch.setenv(name, "3")
    wrong_mpi = {**kwargs, "mpi_processes": 3}
    with pytest.raises(ValueError, match="exactly four MPI"):
        pipeline.run_cobaya_with_attestation(**wrong_mpi)

    Path(f"{kwargs['prefix']}.1.txt").write_text("old chain", encoding="utf-8")
    with pytest.raises(ValueError, match="prefix must be new"):
        pipeline.run_cobaya_with_attestation(**kwargs)


@pytest.mark.parametrize("evidence_class", ["formal_candidate", "model_adequacy"])
def test_converged_runner_rejects_force_and_arbitrary_cobaya_entrypoint(
    tmp_path, monkeypatch, evidence_class
):
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        monkeypatch.setenv(name, "3")
    base = {
        "kind": "chain",
        "config_path": EXACT_CONFIG,
        "prefix": tmp_path / evidence_class,
        "packages_path": tmp_path / "packages",
        "cobaya_run": None,
        "mpi_processes": 4,
        "force": False,
        "evidence_class": evidence_class,
        "run_id": f"{evidence_class}-test-run",
    }
    with pytest.raises(ValueError, match="force|Force"):
        pipeline.run_cobaya_with_attestation(**{**base, "force": True})
    with pytest.raises(ValueError, match="Cobaya|cobaya|runner|override"):
        pipeline.run_cobaya_with_attestation(
            **{**base, "cobaya_run": "untrusted-cobaya-run"}
        )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "untrusted-import-root"))
    with pytest.raises(ValueError, match="PYTHONPATH|import"):
        pipeline.run_cobaya_with_attestation(**base)


def test_trusted_python_launcher_rejects_unfrozen_runtime(tmp_path, monkeypatch):
    source = tmp_path / "python"
    source.write_bytes(b"unfrozen Python launcher bytes\n")
    source.chmod(0o555)
    commitments = dict(pipeline.TRUSTED_NATIVE_RUNTIME_SHA256)
    commitments["python"] = "sha256:" + "0" * 64
    monkeypatch.setattr(pipeline, "TRUSTED_NATIVE_RUNTIME_SHA256", commitments)

    with pytest.raises(RuntimeError, match="bytes do not match the frozen runtime"):
        pipeline._materialize_trusted_python_launcher(source)


def test_trusted_child_identity_accepts_frozen_native_and_distribution_fixture(
    tmp_path, monkeypatch
):
    state = _install_trusted_child_identity_fixture(tmp_path, monkeypatch)
    identity = pipeline._trusted_cobaya_child_identity()

    assert identity["in_virtual_environment"] is True
    assert identity["executable"]["resolved_path"] == str(state.launcher.resolve())
    assert identity["executable"]["source_resolved_path"] == str(
        state.source_python.resolve()
    )
    assert identity["mpirun"]["sha256"] == pipeline._hash_file(state.mpirun)
    assert identity["module"]["path"] == str(state.module_path.resolve())
    assert identity["distribution"]["version"] == (
        pipeline.REQUIRED_PACKAGE_VERSIONS["cobaya"]
    )


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("mpirun_hash", "mpirun bytes do not match the frozen runtime"),
        ("module_origin", "cobaya.run resolves outside"),
        ("distribution_version", "distribution version is not the preregistered"),
    ],
)
def test_trusted_child_identity_rejects_native_or_distribution_drift(
    tmp_path, monkeypatch, drift, message
):
    state = _install_trusted_child_identity_fixture(tmp_path, monkeypatch)
    if drift == "mpirun_hash":
        commitments = dict(pipeline.TRUSTED_NATIVE_RUNTIME_SHA256)
        commitments["mpirun"] = "sha256:" + "0" * 64
        monkeypatch.setattr(
            pipeline, "TRUSTED_NATIVE_RUNTIME_SHA256", commitments
        )
    elif drift == "module_origin":
        state.module_path = tmp_path / "outside-cobaya-run.py"
        state.module_path.write_bytes(b"# unowned module\n")
    else:
        state.distribution.version = "0.0.0"

    with pytest.raises(RuntimeError, match=message):
        pipeline._trusted_cobaya_child_identity()


def test_converged_runner_uses_isolated_interpreter_and_attests_import_policy(
    tmp_path, monkeypatch
):
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        monkeypatch.setenv(name, "3")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    inventory = _synthetic_inventory()
    inventory["packages_path"] = str((tmp_path / "packages").resolve())
    monkeypatch.setattr(pipeline, "build_data_inventory", lambda _: inventory)
    identity_provider = _trusted_runner_identity_provider(tmp_path / "trusted-runner")
    trusted_identity = identity_provider()
    assert trusted_identity["in_virtual_environment"] is True
    assert isinstance(trusted_identity.get("import_policy"), dict)
    monkeypatch.setattr(
        pipeline, "_trusted_cobaya_child_identity", identity_provider
    )
    runtime_environment = {
        "python": "fixture",
        "thread_environment": {
            "OMP_NUM_THREADS": "3",
            "MKL_NUM_THREADS": "3",
            "OPENBLAS_NUM_THREADS": "3",
        },
        "native_runtime": {"passed": True, "reasons": []},
        "import_policy": copy.deepcopy(trusted_identity["import_policy"]),
    }
    monkeypatch.setattr(
        pipeline, "environment_manifest", lambda: runtime_environment
    )
    monkeypatch.setattr(pipeline, "likelihood_runtime_inventory", lambda _: {})
    captured: list[list[str]] = []

    def fake_run(command, **_kwargs):
        captured.append(list(command))
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    prefix = tmp_path / "isolated-formal"
    assert pipeline.run_cobaya_with_attestation(
        kind="chain",
        config_path=EXACT_CONFIG,
        prefix=prefix,
        packages_path=tmp_path / "packages",
        cobaya_run=None,
        mpi_processes=4,
        force=False,
        evidence_class="formal_candidate",
        run_id="isolated-formal-run",
    ) == 2
    expected_prefix = [
        trusted_identity["mpirun"]["resolved_path"],
        "-n",
        "4",
        "--bind-to",
        "none",
        trusted_identity["executable"]["resolved_path"],
        "-I",
        "-m",
        "cobaya.run",
    ]
    assert captured[0][: len(expected_prefix)] == expected_prefix
    assert Path(captured[0][0]).is_absolute()
    assert Path(captured[0][5]).is_absolute()
    assert Path(captured[0][0]).is_symlink() is False
    assert Path(captured[0][5]).is_symlink() is False

    attestation_path = Path(f"{prefix}.run.json")
    original = json.loads(attestation_path.read_text(encoding="utf-8"))
    missing_isolation = copy.deepcopy(original)
    missing_isolation["command"].remove("-I")
    _write_json(
        attestation_path,
        pipeline._with_self_hash(missing_isolation, "attestation_sha256"),
    )
    rejected_command = pipeline.verify_run_attestation(
        kind="chain",
        config_path=EXACT_CONFIG,
        prefix=prefix,
        expected_data_fingerprint=inventory["fingerprint"],
    )
    assert "formal_runner_command_not_canonical" in rejected_command["reasons"]

    drifted_policy = copy.deepcopy(original)
    drifted_policy["runner_identity"]["import_policy"] = {
        "attacker_controlled": True
    }
    _write_json(
        attestation_path,
        pipeline._with_self_hash(drifted_policy, "attestation_sha256"),
    )
    rejected_policy = pipeline.verify_run_attestation(
        kind="chain",
        config_path=EXACT_CONFIG,
        prefix=prefix,
        expected_data_fingerprint=inventory["fingerprint"],
    )
    assert any(
        reason in {
            "formal_runner_identity_drift",
            "formal_runner_import_policy_drift",
        }
        for reason in rejected_policy["reasons"]
    ), rejected_policy["reasons"]


def test_formal_launcher_completion_receipt_binds_real_run_and_resolved_binaries(
    tmp_path, monkeypatch
):
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        monkeypatch.setenv(name, "3")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    test_key = "k" * 64
    test_key_id = "formal-test-key"
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY", test_key)
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY_ID", test_key_id)
    monkeypatch.setattr(pipeline, "EXACT_EVIDENCE_SIGNING_KEY_ID", test_key_id)
    monkeypatch.setattr(
        pipeline,
        "EXACT_EVIDENCE_SIGNING_KEY_SHA256",
        "sha256:" + hashlib.sha256(test_key.encode("utf-8")).hexdigest(),
    )
    inventory = _synthetic_inventory()
    inventory["packages_path"] = str((tmp_path / "packages").resolve())
    monkeypatch.setattr(pipeline, "build_data_inventory", lambda _: inventory)
    identity_provider = _trusted_runner_identity_provider(tmp_path / "trusted-runner")
    trusted_identity = identity_provider()
    monkeypatch.setattr(
        pipeline, "_trusted_cobaya_child_identity", identity_provider
    )
    runtime_environment = {
        "python": "fixture",
        "thread_environment": {
            "OMP_NUM_THREADS": "3",
            "MKL_NUM_THREADS": "3",
            "OPENBLAS_NUM_THREADS": "3",
        },
        "native_runtime": {"passed": True, "reasons": []},
        "import_policy": copy.deepcopy(trusted_identity["import_policy"]),
    }
    monkeypatch.setattr(
        pipeline, "environment_manifest", lambda: runtime_environment
    )
    monkeypatch.setattr(pipeline, "likelihood_runtime_inventory", lambda _: {})
    prefix = tmp_path / "signed-formal"
    captured: list[list[str]] = []

    def fake_run(command, **kwargs):
        captured.append(list(command))
        assert "EVIDENCE_SIGNING_KEY" not in kwargs["env"]
        assert "EVIDENCE_SIGNING_KEY_ID" not in kwargs["env"]
        assert "EVIDENCE_VERIFICATION_KEYS" not in kwargs["env"]
        config_text = EXACT_CONFIG.read_text(encoding="utf-8")
        for suffix in (".input.yaml", ".updated.yaml"):
            Path(f"{prefix}{suffix}").write_text(config_text, encoding="utf-8")
        for index in range(1, 5):
            Path(f"{prefix}.{index}.txt").write_text(
                "# weight minuslogpost w wa omegam H0\n"
                "1 10 -0.8 -0.7 0.3 68\n",
                encoding="utf-8",
            )
        Path(f"{prefix}.checkpoint").write_text(
            yaml.safe_dump(
                {
                    "sampler": {
                        "mcmc": {
                            "converged": True,
                            "Rminus1_last": 0.001,
                            "mpi_size": 4,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        kwargs["stdout"].write(
            "The run has converged!\nSampling complete after 4000 accepted steps\n"
        )
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    assert pipeline.run_cobaya_with_attestation(
        kind="chain",
        config_path=EXACT_CONFIG,
        prefix=prefix,
        packages_path=tmp_path / "packages",
        cobaya_run=None,
        mpi_processes=4,
        force=False,
        evidence_class="formal_candidate",
        run_id="signed-formal-run",
    ) == 0
    assert captured[0][0] == trusted_identity["mpirun"]["resolved_path"]
    assert captured[0][5] == trusted_identity["executable"]["resolved_path"]
    assert Path(captured[0][0]).is_symlink() is False
    assert Path(captured[0][5]).is_symlink() is False

    attestation_path = Path(f"{prefix}.run.json")
    original = json.loads(attestation_path.read_text(encoding="utf-8"))
    assert original["completion_receipt_validation"]["passed"] is True
    assert original["launcher_completion_receipt"]["key_id"] == test_key_id
    assert (
        original["host_execution_trust_boundary"]
        == pipeline.EXACT_HOST_EXECUTION_TRUST_BOUNDARY
    )
    accepted = pipeline.verify_run_attestation(
        kind="chain",
        config_path=EXACT_CONFIG,
        prefix=prefix,
        expected_data_fingerprint=inventory["fingerprint"],
    )
    assert accepted["passed"] is True, accepted["reasons"]

    drifted_boundary = copy.deepcopy(original)
    drifted_boundary["host_execution_trust_boundary"] = {
        "schema_version": 1,
        "boundary_id": "hostile_host_claim",
    }
    _write_json(
        attestation_path,
        pipeline._with_self_hash(drifted_boundary, "attestation_sha256"),
    )
    rejected_boundary = pipeline.verify_run_attestation(
        kind="chain",
        config_path=EXACT_CONFIG,
        prefix=prefix,
        expected_data_fingerprint=inventory["fingerprint"],
    )
    assert "run_host_execution_trust_boundary_mismatch" in rejected_boundary[
        "reasons"
    ]
    assert "formal_launcher_completion_binding_mismatch" in rejected_boundary[
        "reasons"
    ]

    forged = copy.deepcopy(original)
    forged["command"][0] = "mpirun"
    _write_json(
        attestation_path,
        pipeline._with_self_hash(forged, "attestation_sha256"),
    )
    rejected = pipeline.verify_run_attestation(
        kind="chain",
        config_path=EXACT_CONFIG,
        prefix=prefix,
        expected_data_fingerprint=inventory["fingerprint"],
    )
    assert "formal_launcher_completion_binding_mismatch" in rejected["reasons"]
    assert "formal_runner_command_not_canonical" in rejected["reasons"]


def test_completed_attestation_helper_cannot_forge_formal_success(tmp_path, monkeypatch):
    inventory = _synthetic_inventory()
    inventory["packages_path"] = str((tmp_path / "packages").resolve())
    monkeypatch.setattr(pipeline, "environment_manifest", lambda: {"fixture": True})
    monkeypatch.setattr(pipeline, "likelihood_runtime_inventory", lambda _: {})
    prefix = tmp_path / "offline-forgery"
    pipeline._reserve_chain_prefix(prefix)
    config_text = EXACT_CONFIG.read_text(encoding="utf-8")
    for suffix in (".input.yaml", ".updated.yaml"):
        Path(f"{prefix}{suffix}").write_text(config_text, encoding="utf-8")
    for index in range(1, 5):
        Path(f"{prefix}.{index}.txt").write_text("synthetic\n", encoding="utf-8")
    Path(f"{prefix}.checkpoint").write_text(
        yaml.safe_dump(
            {
                "sampler": {
                    "mcmc": {
                        "converged": True,
                        "Rminus1_last": 0.001,
                        "mpi_size": 4,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    Path(f"{prefix}.runner.log").write_text(
        "The run has converged!\nSampling complete after 4000 accepted steps\n",
        encoding="utf-8",
    )
    forged = pipeline.write_completed_attestation(
        kind="chain",
        config_path=EXACT_CONFIG,
        prefix=prefix,
        data_inventory=inventory,
        returncode=0,
        command=["attacker-controlled"],
        require_chain_convergence=True,
        mpi_processes=4,
        threads_per_process=3,
        runner_identity={"attacker": True},
        evidence_class="formal_candidate",
        run_id="offline-forgery",
    )
    assert forged["success"] is False
    assert forged["status"] == "failed"
    assert "formal_launcher_completion_receipt_missing" in (
        forged["completion_receipt_validation"]["reasons"]
    )

    forged["success"] = True
    forged["status"] = "completed"
    forged["completion_receipt_validation"] = {"passed": True, "reasons": []}
    _write_json(
        Path(f"{prefix}.run.json"),
        pipeline._with_self_hash(forged, "attestation_sha256"),
    )
    rejected = pipeline.verify_run_attestation(
        kind="chain",
        config_path=EXACT_CONFIG,
        prefix=prefix,
        expected_data_fingerprint=inventory["fingerprint"],
    )
    assert "formal_launcher_completion_receipt_missing" in rejected["reasons"]


@pytest.mark.parametrize(
    ("key", "key_id", "expected_reason"),
    [
        (None, None, "formal_launcher_completion_ephemeral_key_forbidden"),
        ("short", "short-key", "formal_launcher_completion_verification_key_too_short"),
    ],
)
def test_launcher_completion_rejects_dev_ephemeral_and_short_keys(
    monkeypatch, key, key_id, expected_reason
):
    monkeypatch.delenv("EVIDENCE_SIGNING_KEY", raising=False)
    monkeypatch.delenv("EVIDENCE_SIGNING_KEY_ID", raising=False)
    if key is not None:
        monkeypatch.setenv("EVIDENCE_SIGNING_KEY", key)
        monkeypatch.setenv("EVIDENCE_SIGNING_KEY_ID", key_id)
    nonce = "a" * 64
    launch_context = pipeline._with_self_hash(
        {
            "schema_version": 1,
            "domain": pipeline.LAUNCH_NONCE_DOMAIN,
            "nonce_commitment": pipeline._launcher_nonce_commitment(nonce),
        },
        "launch_context_sha256",
    )
    expected_binding = {"completion_schema_version": 1, "fixture": True}
    receipt = pipeline.build_scientific_attestation(
        attestation_type=pipeline.LAUNCHER_COMPLETION_ATTESTATION_TYPE,
        payload={**expected_binding, "launcher_nonce": nonce},
        require_explicit=False,
    )
    validation = pipeline._validate_launcher_completion_receipt(
        receipt,
        expected_binding=expected_binding,
        launch_context=launch_context,
    )
    assert validation["passed"] is False
    assert expected_reason in validation["reasons"]


def test_launcher_completion_rejects_operator_selected_long_key_and_id(
    monkeypatch,
):
    attacker_key = "operator-selected-key-material-that-is-long-enough"
    attacker_key_id = "operator-selected-v1"
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY", attacker_key)
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY_ID", attacker_key_id)
    nonce = "b" * 64
    launch_context = pipeline._with_self_hash(
        {
            "schema_version": 1,
            "domain": pipeline.LAUNCH_NONCE_DOMAIN,
            "nonce_commitment": pipeline._launcher_nonce_commitment(nonce),
            "host_execution_trust_boundary": copy.deepcopy(
                pipeline.EXACT_HOST_EXECUTION_TRUST_BOUNDARY
            ),
        },
        "launch_context_sha256",
    )
    expected_binding = {
        "completion_schema_version": 1,
        "fixture": True,
        "host_execution_trust_boundary": copy.deepcopy(
            pipeline.EXACT_HOST_EXECUTION_TRUST_BOUNDARY
        ),
    }
    receipt = pipeline.build_scientific_attestation(
        attestation_type=pipeline.LAUNCHER_COMPLETION_ATTESTATION_TYPE,
        payload={
            **expected_binding,
            "launcher_nonce": nonce,
            "evidence_signing_key_binding": pipeline.signing_key_binding(
                require_explicit=True
            ),
        },
        require_explicit=True,
    )
    validation = pipeline._validate_launcher_completion_receipt(
        receipt,
        expected_binding=expected_binding,
        launch_context=launch_context,
    )

    assert validation["passed"] is False
    assert "formal_launcher_completion_key_id_mismatch" in validation["reasons"]
    assert "formal_launcher_completion_key_fingerprint_mismatch" in validation[
        "reasons"
    ]
    assert "formal_launcher_completion_verification_key_mismatch" in validation[
        "reasons"
    ]


def test_chain_prefix_reservation_is_atomic_and_exclusive(tmp_path):
    prefix = tmp_path / "runs" / "formal"
    prefix.parent.mkdir(parents=True)
    workers = 8
    barrier = threading.Barrier(workers)

    def reserve() -> Path | None:
        barrier.wait()
        try:
            return pipeline._reserve_chain_prefix(prefix)
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _: reserve(), range(workers)))

    winners = [result for result in results if result is not None]
    assert winners == [Path(f"{prefix}.reservation.lock")]
    assert winners[0].is_file()
    with pytest.raises(FileExistsError):
        pipeline._reserve_chain_prefix(prefix)


def test_grade_recomputes_chain_analysis_despite_forged_valid_self_hash(tmp_path):
    """A re-hashed manifest cannot replace statistics from physical chains."""

    prefix = tmp_path / "synthetic-formal"
    columns = ["weight", "w", "wa", "omegam", "H0"]
    for index in range(1, 5):
        rng = np.random.default_rng(7000 + index)
        rows = np.column_stack(
            [
                np.ones(1200),
                rng.normal(-0.4, 0.13, 1200),
                rng.normal(0.3, 0.45, 1200),
                rng.normal(0.22, 0.02, 1200),
                rng.normal(61.0, 2.5, 1200),
            ]
        )
        np.savetxt(
            Path(f"{prefix}.{index}.txt"),
            rows,
            header=" ".join(columns),
            comments="# ",
            fmt="%.12g",
        )
    updated_config = {
        "params": {
            "w": {"prior": {"min": -3.0, "max": 1.0}},
            "wa": {"prior": {"min": -3.0, "max": 2.0}},
            "omegam": {},
            "H0": {},
        }
    }
    Path(f"{prefix}.updated.yaml").write_text(
        yaml.safe_dump(updated_config), encoding="utf-8"
    )
    diagnostics, analysis_data = pipeline.diagnose_chains(
        prefix,
        updated_config=updated_config,
        burn_fraction=pipeline.DEFAULT_BURN_FRACTION,
    )
    intervals = pipeline.posterior_intervals(analysis_data)
    chain_sha256 = [
        item["sha256"] for item in diagnostics["chain_files"]
    ]
    manifest = pipeline._with_self_hash(
        {
            "artifact_type": "w0wa_exact_analysis",
            "posterior": {
                "diagnostics": diagnostics,
                "intervals_68": intervals,
            },
            "research_alpha_binding": {
                "chain_sha256": chain_sha256,
                "sampled_parameters": diagnostics["sampled_parameters"],
            },
            "run_identity": {
                "chain_ids": [
                    f"chain-{index}:{digest.split(':')[-1][:16]}"
                    for index, digest in enumerate(chain_sha256, start=1)
                ]
            },
        },
        "manifest_sha256",
    )
    authentic = pipeline._recompute_analysis_from_chain_artifacts(manifest)
    assert authentic["passed"] is True, authentic["reasons"]

    forged = copy.deepcopy(manifest)
    forged["posterior"]["intervals_68"]["w"]["mean"] = -0.01
    forged["posterior"]["diagnostics"]["parameters"]["w"][
        "rank_normalized_rhat"
    ] = 1.0
    forged = pipeline._with_self_hash(forged, "manifest_sha256")
    assert pipeline._verify_self_hash(forged, "manifest_sha256") is True

    rejected = pipeline._recompute_analysis_from_chain_artifacts(forged)
    assert rejected["passed"] is False
    assert {
        "analysis_chain_diagnostics_recompute_mismatch",
        "analysis_chain_intervals_recompute_mismatch",
    }.issubset(rejected["reasons"])


def test_grade_uses_hidden_commitment_and_never_grants_final_a(tmp_path):
    support_file = tmp_path / "support.json"
    support_file.write_text("{}", encoding="utf-8")
    support_record = {
        "path": str(support_file.resolve()),
        "size_bytes": support_file.stat().st_size,
        "sha256": pipeline._hash_file(support_file),
    }
    intervals = {
        "omegam": {
            "mean": 0.31,
            "std": 0.01,
            "minimal_lower_68": 0.30,
            "minimal_upper_68": 0.32,
            "mcse_mean": 0.0001,
        },
        "H0": {
            "mean": 68.0,
            "std": 1.0,
            "minimal_lower_68": 67.0,
            "minimal_upper_68": 69.0,
            "mcse_mean": 0.001,
        },
        "w": {
            "mean": -0.8,
            "std": 0.1,
            "minimal_lower_68": -0.9,
            "minimal_upper_68": -0.7,
            "mcse_mean": 0.001,
        },
        "wa": {
            "mean": -0.7,
            "std": 0.3,
            "minimal_lower_68": -1.0,
            "minimal_upper_68": -0.4,
            "mcse_mean": 0.001,
        },
    }
    diagnostic_parameters = {
        name: {
            "rank_normalized_rhat": 1.002,
            "bulk_ess": 800.0,
            "mcse_mean": intervals[name]["mcse_mean"],
            "posterior_std": max(
                intervals[name]["minimal_upper_68"]
                - intervals[name]["minimal_lower_68"],
                0.01,
            ),
        }
        for name in intervals
    }
    diagnostic_parameters["calibration"] = {
        "rank_normalized_rhat": 1.001,
        "bulk_ess": 900.0,
        "mcse_mean": 0.001,
        "posterior_std": 0.1,
    }
    manifest = pipeline._with_self_hash(
        {
            "schema_version": pipeline.SCHEMA_VERSION,
            "artifact_type": "w0wa_exact_analysis",
            "profile_id": pipeline.EXACT_PROFILE_ID,
                "claim_scope": pipeline.EXACT_CLAIM_SCOPE,
                "target_commitment": pipeline.PREREGISTERED_TARGET_COMMITMENT,
                "protocol_integrity": dict(pipeline.PROTOCOL_INTEGRITY),
                "paper_fidelity_amendment": dict(pipeline.PAPER_FIDELITY_AMENDMENT),
                "protocol_amendment_artifact": pipeline.protocol_amendment_record(),
            "status": "ANALYZED",
            "evidence_ready_for_offline_grading": True,
            "posterior": {
                "intervals_68": intervals,
                "diagnostics": {"parameters": diagnostic_parameters},
            },
            "claim_support_paths": [support_record],
            "run_identity": {
                "run_id": "formal-test-run",
                "chain_ids": ["chain-1", "chain-2", "chain-3", "chain-4"],
                "seeds": [11, 22, 33, 44],
            },
            "configuration": {"fingerprint": "sha256:" + "c" * 64},
            "data_fingerprints": {
                "desi": "sha256:" + "d" * 64,
                "pantheon": "sha256:" + "e" * 64,
            },
            "likelihood_fingerprints": {
                "planck": "sha256:" + "f" * 64,
                "act": "sha256:" + "1" * 64,
            },
        },
        "manifest_sha256",
    )
    manifest_path = tmp_path / "analysis.json"
    _write_json(manifest_path, manifest)

    checks = [
        "prior_predictive_check",
        "posterior_predictive_check",
        "prior_sensitivity",
        "systematics_robustness",
        "simulation_recovery",
        "independent_reproduction",
    ]
    hidden = {
        "schema_version": 1,
        "commitment_salt_hex": "0123456789abcdef",
        "targets": [
            {
                "name": "Omega_m",
                "paper_symbol": "Omega_m",
                "center": 0.31,
                "lower_68": 0.30,
                "upper_68": 0.32,
                "uncertainty_minus": 0.01,
                "uncertainty_plus": 0.01,
            },
            {
                "name": "H0",
                "paper_symbol": "H_0",
                "center": 68.0,
                "lower_68": 67.0,
                "upper_68": 69.0,
                "uncertainty_minus": 1.0,
                "uncertainty_plus": 1.0,
            },
            {
                "name": "w0",
                "paper_symbol": "w_0",
                "center": -0.8,
                "lower_68": -0.9,
                "upper_68": -0.7,
                "uncertainty_minus": 0.1,
                "uncertainty_plus": 0.1,
            },
            {
                "name": "wa",
                "paper_symbol": "w_a",
                "center": -0.7,
                "lower_68": -1.0,
                "upper_68": -0.4,
                "uncertainty_minus": 0.3,
                "uncertainty_plus": 0.3,
            },
        ],
        "directions": {"w0": "w0 > -1", "wa": "wa < 0"},
        "acceptance_thresholds": {
            "center_max_paper_sigma": 0.30,
            "interval_width_max_relative_error": 0.15,
            "prior_variant_max_shift_paper_sigma": 0.20,
            "systematics_variant_max_shift_paper_sigma": 0.50,
            "systematics_variant_max_interval_change_fraction": 0.20,
            "injection_count": 3,
            "injection_joint_coverage": 0.95,
            "injection_mean_max_standardized_bias": 0.30,
            "mcse_max_paper_sigma": 0.05,
        },
        "required_model_adequacy_checks": checks,
    }
    check_records = {}
    adequacy_paths = []
    for index, name in enumerate(checks):
        artifact = tmp_path / f"{name}.json"
        artifact.write_text(json.dumps({"name": name}), encoding="utf-8")
        record = {
            "status": "passed",
            "artifact_id": f"artifact:{name}",
            "artifact_path": str(artifact.resolve()),
            "artifact_hash": pipeline._hash_file(artifact),
        }
        check_records[name] = record
        adequacy_paths.append(
            {
                "path": str(artifact.resolve()),
                "size_bytes": artifact.stat().st_size,
                "sha256": pipeline._hash_file(artifact),
            }
        )
    check_records["prior_sensitivity"]["metrics"] = {
        "max_parameter_shift_paper_sigma": 0.10
    }
    check_records["systematics_robustness"]["metrics"] = {
        "variants": [
            {
                "name": "camspec",
                "max_parameter_shift_paper_sigma": 0.40,
                "max_interval_change_fraction": 0.25,
            }
        ]
    }
    check_records["simulation_recovery"]["metrics"] = {
        "injections": [
            {
                "name": f"fiducial-{index}",
                "truth_inside_joint_region": True,
                "joint_coverage": 0.95,
            }
            for index in range(3)
        ],
        "mean_standardized_bias": 0.10,
    }
    postprocessor = tmp_path / "independent-postprocess.json"
    postprocessor.write_text("{}", encoding="utf-8")
    check_records["independent_reproduction"]["metrics"] = {
        "independent_run_id": "isolated-test-run",
        "environment_fingerprint": "sha256:" + "2" * 64,
        "postprocessor_report_path": str(postprocessor.resolve()),
        "postprocessor_report_hash": pipeline._hash_file(postprocessor),
    }
    adequacy = {"checks": check_records, "evidence_paths": adequacy_paths}
    adequacy_path = tmp_path / "adequacy.json"
    _write_json(adequacy_path, adequacy)

    hidden_path = tmp_path / "hidden.json"
    _write_json(hidden_path, hidden)
    target_hash = pipeline._hash_object(hidden)
    grade = pipeline.grade_exact_analysis(
        manifest_path=manifest_path,
        hidden_answer_path=hidden_path,
        target_hash=target_hash,
        adequacy_manifest_path=adequacy_path,
    )
    assert grade["status"] == "WITHHELD"
    assert grade["A_ready_count"] == 0
    assert grade["strict_A_count"] == 0
    assert grade["publication_ready"] is False
    assert (
        "protocol_adjudication:external_protocol_adjudication_not_provided"
        in grade["failures"]
    )
    assert (
        "environment_revision_pending_fresh_preflight_and_science_regression"
        in grade["failures"]
    )
    assert grade["environment_revision"] == pipeline.EXACT_ENVIRONMENT_REVISION
    assert "research_alpha_manifest" not in grade

    tampered = pipeline.grade_exact_analysis(
        manifest_path=manifest_path,
        hidden_answer_path=hidden_path,
        target_hash="sha256:" + "0" * 64,
        adequacy_manifest_path=adequacy_path,
    )
    assert tampered["status"] == "WITHHELD"
    assert "hidden_answer_commitment_mismatch" in tampered["failures"]
