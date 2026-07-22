"""Cross-check the genuinely separate w0wa chain postprocessor."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml


_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_DIR = _ROOT / "backend" / "scripts" / "cobaya"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


canonical = _load_module(
    "canonical_for_independent_test",
    _SCRIPT_DIR / "canonical_full_likelihood_evidence.py",
)
independent = _load_module(
    "independent_w0wa_postprocess",
    _SCRIPT_DIR / "independent_w0wa_postprocess.py",
)


def _site_packages_ownership_identity() -> dict:
    payload = {
        "schema_version": 1,
        "site_root_count": 1,
        "owned_import_files": {
            "count": 1,
            "fingerprint": "sha256:" + "7" * 64,
        },
        "generated_bytecode_policy": (
            dict(independent.GENERATED_BYTECODE_CACHE_POLICY)
        ),
        "unowned_import_files": [],
        "unowned_generated_bytecode": [],
        "symlinked_directories": [],
    }
    return {
        **payload,
        "passed": True,
        "reasons": [],
        "fingerprint": independent._hash_object(payload),
    }


def _patch_closed_runtime(monkeypatch, distributions: dict[str, dict]) -> None:
    pip_record = {
        "distribution": "pip",
        "installed": True,
        "files": [],
        **independent.FROZEN_BOOTSTRAP_DISTRIBUTIONS["pip"],
    }
    installed = {**distributions, "pip": pip_record}

    class FakeDistribution:
        def __init__(self, name: str, version: str):
            self.metadata = {"Name": name}
            self.version = version
            self._normalized_name = name
            self.entry_points = []

    monkeypatch.setattr(
        independent,
        "_distribution_inventory",
        lambda name: installed[name],
    )
    monkeypatch.setattr(
        independent.importlib.metadata,
        "distributions",
        lambda: [
            FakeDistribution(name, str(record["version"]))
            for name, record in installed.items()
        ],
    )
    monkeypatch.setattr(
        independent,
        "_site_packages_ownership_inventory",
        lambda **_kwargs: _site_packages_ownership_identity(),
    )


def _write_environment_preflight(
    tmp_path: Path,
) -> tuple[Path, dict[str, dict], str]:
    executable = Path(sys.executable)
    binary = executable.resolve()
    binary_record = {
        "path": str(binary),
        "size_bytes": binary.stat().st_size,
        "sha256": independent._hash_file(binary),
    }
    versions = {f"fixture-distribution-{index:02d}": "1.0" for index in range(52)}
    distributions = {
        name: {
            "distribution": name,
            "installed": True,
            "version": version,
            "files": [],
            "fingerprint": independent._hash_object([]),
        }
        for name, version in versions.items()
    }
    venv_root = Path(sys.prefix).resolve()
    site_roots = sorted(
        {
            Path(raw).resolve()
            for raw in independent.site.getsitepackages()
            if Path(raw).is_dir() and Path(raw).resolve().is_relative_to(venv_root)
        },
        key=str,
    )
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
    import_policy_unsigned = {
        "schema_version": 1,
        "isolated_interpreter": True,
        "python_flag": "-I",
        "ignore_environment": True,
        "no_user_site": True,
        "safe_path": True,
        "pythonpath_empty": True,
        "user_site_disabled_by_child": True,
        "venv_root": str(venv_root),
        "site_package_roots": [str(path) for path in site_roots],
        "startup_hooks": [
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": independent._hash_file(path),
                "owners": ["fixture-owner"],
                "trusted_owner": True,
                "executable_lines": 0,
                "external_paths": [],
            }
            for path in hook_paths
        ],
    }
    import_policy = {
        **import_policy_unsigned,
        "passed": True,
        "reasons": [],
        "fingerprint": independent._hash_object(import_policy_unsigned),
    }
    fingerprints = {
        name: {
            "version": version,
            "fingerprint": distributions[name]["fingerprint"],
        }
        for name, version in versions.items()
    }
    fingerprints.update(independent.FROZEN_BOOTSTRAP_DISTRIBUTIONS)
    runtime_closure_unsigned = {
        "required_versions": dict(sorted(versions.items())),
        "dependency_closure": sorted(versions),
        "installed_distributions": sorted(fingerprints),
        "bootstrap_distributions": independent.FROZEN_BOOTSTRAP_DISTRIBUTIONS,
        "distribution_fingerprints": fingerprints,
        "site_packages_ownership": _site_packages_ownership_identity(),
    }
    runtime_closure = {
        **runtime_closure_unsigned,
        "passed": True,
        "reasons": [],
        "fingerprint": independent._hash_object(runtime_closure_unsigned),
    }
    runtime = {
        "python": sys.version,
        "executable": str(executable.absolute()),
        "platform": "fixture-platform",
        "machine": "fixture-machine",
        "packages": {},
        "runtime_modules": {},
        "thread_environment": {
            name: independent.os.environ.get(name)
            for name in (
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
            )
        },
        "native_runtime": {
            "binaries": {
                "python": dict(binary_record),
                "mpirun": dict(binary_record),
                "mpi4py_extension": dict(binary_record),
            }
        },
        "import_policy": import_policy,
        "runtime_closure": runtime_closure,
    }
    runtime_fingerprint = independent._runtime_environment_fingerprint(runtime)
    environment = {
        "passed": True,
        "reasons": [],
        "lock": {"sha256": independent.TRUSTED_DEPENDENCY_LOCK_SHA256},
        "required_versions": versions,
        "runtime_closure": sorted(versions),
        "distributions": distributions,
        "runtime": runtime,
    }
    environment["fingerprint"] = independent._hash_object(
        {
            key: value
            for key, value in environment.items()
            if key not in {"passed", "reasons", "fingerprint"}
        }
    )
    payload = {
        "schema_version": 2,
        "artifact_type": "w0wa_exact_preflight",
        "profile_id": independent.EXACT_PROFILE_ID,
        "target_commitment": independent.PREREGISTERED_TARGET_COMMITMENT,
        "passed": True,
        "status": "PASS",
        "environment": environment,
    }
    payload["preflight_sha256"] = independent._hash_object(payload)
    path = tmp_path / "isolated-preflight.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path, distributions, runtime_fingerprint


def _patch_current_interpreter_as_isolated(monkeypatch) -> None:
    monkeypatch.setattr(
        independent,
        "_current_python_isolation",
        lambda: {
            "isolated_interpreter": True,
            "ignore_environment": True,
            "no_user_site": True,
            "safe_path": True,
            "pythonpath_empty": True,
            "passed": True,
            "reasons": [],
        },
    )


def _write_chains(
    tmp_path: Path,
    *,
    chain_lengths: tuple[int, int, int, int] = (1600, 1600, 1600, 1600),
) -> tuple[Path, Path, dict]:
    config = {
        "params": {
            "cosmo": {"prior": {"min": -1, "max": 1}},
            "w": {"prior": {"min": -3, "max": 1}},
            "wa": {"prior": {"min": -3, "max": 2}},
            "omegam": {"latex": "Omega_m"},
            "H0": {"latex": "H_0"},
        }
    }
    config_path = tmp_path / "updated.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    prefix = tmp_path / "chain"
    names = ["weight", "minuslogpost", "cosmo", "w", "wa", "omegam", "H0"]
    for index, n_rows in enumerate(chain_lengths, start=1):
        rng = np.random.default_rng(9000 + index)
        rows = np.column_stack(
            [
                np.ones(n_rows),
                rng.normal(100.0, 1.0, n_rows),
                rng.normal(0.1, 0.01, n_rows),
                rng.normal(-0.8, 0.06, n_rows),
                rng.normal(-0.7, 0.25, n_rows),
                rng.normal(0.31, 0.007, n_rows),
                rng.normal(68.0, 0.7, n_rows),
            ]
        )
        np.savetxt(
            Path(f"{prefix}.{index}.txt"),
            rows,
            header=" ".join(names),
            comments="# ",
        )
    return prefix, config_path, config


def _fixed_mcse(**overrides: float) -> dict[str, np.ndarray]:
    values = {name: 0.0 for name in ("cosmo", *independent.REPORT_PARAMETERS)}
    values.update(overrides)
    return {name: np.asarray([value]) for name, value in values.items()}


def test_independent_postprocessor_agrees_and_binds_chain_mutation(
    tmp_path, monkeypatch
):
    prefix, config_path, config = _write_chains(tmp_path)
    preflight_path, distributions, environment_fingerprint = (
        _write_environment_preflight(tmp_path)
    )
    _patch_closed_runtime(monkeypatch, distributions)
    _patch_current_interpreter_as_isolated(monkeypatch)
    primary_diagnostics, aligned = canonical.diagnose_chains(
        prefix,
        updated_config=config,
        burn_fraction=0.30,
    )
    primary_intervals = canonical.posterior_intervals(aligned)
    report = independent.independently_postprocess(
        chain_prefix=prefix,
        updated_config_path=config_path,
        run_id="isolated-rerun-1",
        burn_fraction=0.30,
        primary_execution_fingerprint="sha256:" + "a" * 64,
        environment_fingerprint=environment_fingerprint,
        environment_preflight_path=preflight_path,
    )
    assert report["status"] == "PASS", report["failures"]
    assert report["chain_length_balance_passed"] is True
    assert report["diagnostic_alignment_fraction_per_chain"] == [1.0] * 4
    assert report["research_alpha_binding"]["chain_sha256"] == [
        record["sha256"] for record in report["chain_files"]
    ]
    assert report["research_alpha_binding"]["environment_preflight"]["verified"] is True
    assert (
        report["research_alpha_binding"]["environment_preflight"]["distribution_count"]
        == 52
    )
    assert report["execution_policy"] == {
        "mode": "research_alpha_bound",
        "formal_burn_fraction": 0.30,
        "formal_burn_fraction_enforced": True,
        "current_python_isolation": {
            "isolated_interpreter": True,
            "ignore_environment": True,
            "no_user_site": True,
            "safe_path": True,
            "pythonpath_empty": True,
            "passed": True,
            "reasons": [],
        },
        "preflight_import_policy_fingerprint": report["research_alpha_binding"][
            "current_import_policy"
        ]["preflight_import_policy_fingerprint"],
        "startup_hook_fingerprint": report["research_alpha_binding"][
            "current_import_policy"
        ]["startup_hook_fingerprint"],
        "preflight_import_policy_verified": True,
    }
    assert report["research_alpha_binding"]["current_import_policy"]["verified"] is True
    for name, record in primary_diagnostics["parameters"].items():
        assert report["diagnostics"][name]["rank_normalized_rhat"] == pytest.approx(
            record["rank_normalized_rhat"], rel=1e-12
        )
        assert report["diagnostics"][name]["bulk_ess"] == pytest.approx(
            record["bulk_ess"], rel=1e-12
        )
        assert report["diagnostics"][name]["mcse_passed"] is True
    expected_paper_references = {
        "w": 0.063,
        "wa": 0.25,
        "omegam": 0.0068,
        "H0": 0.72,
    }
    for name, reference in expected_paper_references.items():
        diagnostic = report["diagnostics"][name]
        assert diagnostic["mcse_reference_kind"] == "paper_sigma"
        assert diagnostic["mcse_reference_value"] == pytest.approx(reference)
        assert diagnostic["mcse_over_reference_sigma"] == pytest.approx(
            diagnostic["mcse_mean"] / reference
        )
    nuisance_diagnostic = report["diagnostics"]["cosmo"]
    assert nuisance_diagnostic["mcse_reference_kind"] == "posterior_sd"
    assert nuisance_diagnostic["mcse_reference_value"] == pytest.approx(
        nuisance_diagnostic["posterior_std"]
    )
    assert report["diagnostic_thresholds"] == {
        "rank_normalized_rhat_maximum_exclusive": 1.01,
        "bulk_ess_minimum_inclusive": 1000.0,
        "mcse_maximum_reference_sigma_exclusive": 0.05,
        "mcse_reference_policy": {
            "reported_parameters": "preregistered_paper_sigma",
            "unreported_sampled_parameters": "same_closed_run_posterior_sd",
        },
    }
    for name in independent.REPORT_PARAMETERS:
        expected = canonical.table3_reported_interval(name, primary_intervals[name])
        for field in (
            "center",
            "lower_68",
            "upper_68",
            "uncertainty_minus",
            "uncertainty_plus",
        ):
            assert report["intervals_68"][name][field] == pytest.approx(
                expected[field], rel=1e-12
            )
        assert (
            report["intervals_68"][name]["reporting_statistic"]
            == expected["reporting_statistic"]
        )

    original_report_hash = report["report_sha256"]
    original_chain_hash = report["chain_files"][0]["sha256"]
    first_chain = Path(f"{prefix}.1.txt")
    lines = first_chain.read_text(encoding="utf-8").splitlines()
    fields = lines[1].split()
    fields[-1] = str(float(fields[-1]) + 0.01)
    lines[1] = " ".join(fields)
    first_chain.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mutated = independent.independently_postprocess(
        chain_prefix=prefix,
        updated_config_path=config_path,
        run_id="isolated-rerun-1",
        burn_fraction=0.30,
    )
    assert mutated["chain_files"][0]["sha256"] != original_chain_hash
    assert mutated["report_sha256"] != original_report_hash


def test_independent_postprocessor_withholds_all_reported_mcse_at_limit(
    tmp_path, monkeypatch
):
    prefix, config_path, _ = _write_chains(tmp_path)
    references = {
        "w": 0.063,
        "wa": 0.25,
        "omegam": 0.0068,
        "H0": 0.72,
    }
    fixed = _fixed_mcse(
        **{
            name: independent.MCSE_MAX_REFERENCE_SIGMA_EXCLUSIVE * reference
            for name, reference in references.items()
        }
    )
    monkeypatch.setattr(
        independent.az,
        "mcse",
        lambda _idata, *, method: fixed,
    )

    report = independent.independently_postprocess(
        chain_prefix=prefix,
        updated_config_path=config_path,
        run_id="reported-mcse-boundary-run",
        burn_fraction=0.30,
    )

    assert report["status"] == "WITHHELD"
    assert set(report["failures"]) == {
        f"diagnostic_threshold_failed:{name}" for name in references
    }
    assert "intervals_68" not in report
    for name, reference in references.items():
        diagnostic = report["diagnostics"][name]
        assert diagnostic["mcse_reference_kind"] == "paper_sigma"
        assert diagnostic["mcse_reference_value"] == pytest.approx(reference)
        assert diagnostic["mcse_mean"] == pytest.approx(0.05 * reference)
        assert diagnostic["mcse_absolute_limit_exclusive"] == pytest.approx(
            0.05 * reference
        )
        assert diagnostic["mcse_passed"] is False
        assert diagnostic["passed"] is False


def test_independent_postprocessor_withholds_nuisance_mcse_at_posterior_sd_limit(
    tmp_path, monkeypatch
):
    prefix, config_path, _ = _write_chains(tmp_path)
    burn_rows = int(round(1600 * 0.30))
    nuisance_draws = np.concatenate(
        [
            np.loadtxt(Path(f"{prefix}.{index}.txt"), comments="#", ndmin=2)[
                burn_rows:, 2
            ]
            for index in range(1, 5)
        ]
    )
    posterior_std = float(np.std(nuisance_draws, ddof=1))
    fixed = _fixed_mcse(
        cosmo=independent.MCSE_MAX_REFERENCE_SIGMA_EXCLUSIVE * posterior_std
    )
    monkeypatch.setattr(
        independent.az,
        "mcse",
        lambda _idata, *, method: fixed,
    )

    report = independent.independently_postprocess(
        chain_prefix=prefix,
        updated_config_path=config_path,
        run_id="nuisance-mcse-boundary-run",
        burn_fraction=0.30,
    )

    diagnostic = report["diagnostics"]["cosmo"]
    assert report["status"] == "WITHHELD"
    assert report["failures"] == ["diagnostic_threshold_failed:cosmo"]
    assert "intervals_68" not in report
    assert diagnostic["mcse_reference_kind"] == "posterior_sd"
    assert diagnostic["mcse_reference_value"] == pytest.approx(posterior_std)
    assert diagnostic["mcse_mean"] == pytest.approx(0.05 * posterior_std)
    assert diagnostic["mcse_passed"] is False
    assert diagnostic["passed"] is False


def test_environment_preflight_cannot_claim_a_foreign_interpreter(
    tmp_path, monkeypatch
):
    preflight_path, distributions, fingerprint = _write_environment_preflight(tmp_path)
    _patch_closed_runtime(monkeypatch, distributions)
    _patch_current_interpreter_as_isolated(monkeypatch)
    payload = json.loads(preflight_path.read_text(encoding="utf-8"))
    payload["environment"]["runtime"]["executable"] = (
        "/definitely/not/the/current/isolated/python"
    )
    payload["environment"]["fingerprint"] = independent._hash_object(
        {
            key: value
            for key, value in payload["environment"].items()
            if key not in {"passed", "reasons", "fingerprint"}
        }
    )
    payload.pop("preflight_sha256")
    payload["preflight_sha256"] = independent._hash_object(payload)
    preflight_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="not running under the preflight interpreter",
    ):
        independent._verify_environment_preflight(
            preflight_path,
            expected_fingerprint=fingerprint,
        )


def test_independent_runtime_closure_rejects_rogue_distribution(
    tmp_path, monkeypatch
):
    preflight_path, distributions, _ = _write_environment_preflight(tmp_path)
    _patch_closed_runtime(monkeypatch, distributions)
    payload = json.loads(preflight_path.read_text(encoding="utf-8"))
    runtime_identity = payload["environment"]["runtime"]["runtime_closure"]

    class RogueDistribution:
        metadata = {"Name": "rogue-addon"}
        version = "9.9"
        _normalized_name = "rogue-addon"
        entry_points = []

    expected_distributions = list(independent.importlib.metadata.distributions())
    monkeypatch.setattr(
        independent.importlib.metadata,
        "distributions",
        lambda: [*expected_distributions, RogueDistribution()],
    )
    with pytest.raises(
        ValueError,
        match="installed distribution set is not the frozen closure",
    ):
        independent._validate_runtime_closure_identity(
            runtime_identity,
            required_versions=payload["environment"]["required_versions"],
            site_roots=payload["environment"]["runtime"]["import_policy"][
                "site_package_roots"
            ],
        )


def test_independent_ownership_normalizes_only_source_owned_pycache(
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
        independent.importlib.metadata,
        "distribution",
        lambda _name: FakeDistribution(),
    )
    baseline = independent._site_packages_ownership_inventory(
        allowed_distributions=["demo"], site_roots=[site_root]
    )
    assert baseline["passed"] is True

    owned_cache = Path(importlib.util.cache_from_source(str(source)))
    owned_cache.parent.mkdir()
    owned_cache.write_bytes(b"derived cache")
    assert independent._site_packages_ownership_inventory(
        allowed_distributions=["demo"], site_roots=[site_root]
    ) == baseline

    orphan_cache = Path(
        importlib.util.cache_from_source(str(site_root / "orphan.py"))
    )
    orphan_cache.write_bytes(b"sourceless bytecode")
    rejected = independent._site_packages_ownership_inventory(
        allowed_distributions=["demo"], site_roots=[site_root]
    )
    assert rejected["passed"] is False
    assert rejected["unowned_generated_bytecode"] == [
        f"0:{orphan_cache.relative_to(site_root).as_posix()}"
    ]


def test_bound_postprocessor_rejects_even_adjacent_noncanonical_burn_fraction(
    tmp_path,
):
    with pytest.raises(
        ValueError,
        match="bound independent postprocessing requires burn_fraction=0.30",
    ):
        independent.independently_postprocess(
            chain_prefix=tmp_path / "must-not-be-read",
            updated_config_path=tmp_path / "must-not-be-read.yaml",
            run_id="noncanonical-burn-attack",
            burn_fraction=float(np.nextafter(0.30, 1.0)),
            primary_execution_fingerprint="sha256:" + "a" * 64,
            environment_fingerprint="sha256:" + "b" * 64,
            environment_preflight_path=tmp_path / "must-not-be-read.json",
        )


def test_preflight_pass_cannot_mask_current_nonisolated_execution(
    tmp_path, monkeypatch
):
    preflight_path, _, _ = _write_environment_preflight(tmp_path)
    payload = json.loads(preflight_path.read_text(encoding="utf-8"))
    recorded = payload["environment"]["runtime"]["import_policy"]
    monkeypatch.setattr(
        independent,
        "_current_python_isolation",
        lambda: {
            "isolated_interpreter": False,
            "ignore_environment": True,
            "no_user_site": True,
            "safe_path": True,
            "pythonpath_empty": True,
            "passed": False,
            "reasons": ["isolated_interpreter"],
        },
    )

    with pytest.raises(
        ValueError,
        match="requires python -I and an empty PYTHONPATH",
    ):
        independent._verify_current_import_policy(recorded)


def test_independent_cli_rejects_historical_state_before_analysis_or_writes(
    tmp_path, monkeypatch
):
    backend_root = tmp_path / "repo" / "backend"
    local_root = tmp_path / "repo" / ".local" / "w0wa-strict-a-readiness"
    chain_root = backend_root / "cobaya_runs"
    monkeypatch.setattr(independent, "BACKEND_ROOT", backend_root)
    sentinels = {
        "revision_1_output": local_root / "independent-postprocess.json",
        "r2_preflight": local_root / "isolated-r2" / "preflight-r2.json",
        "r2_output": (
            local_root / "isolated-r2" / "independent-postprocess-r2.json"
        ),
        "r2_config": chain_root / "w0wa_exact_isolated_r2.updated.yaml",
        "r2_chain": chain_root / "w0wa_exact_isolated_r2.1.txt",
        "custom_output": tmp_path / "custom-output" / "existing-report.json",
    }
    for name, path in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"historical-{name}\n".encode())
    alias = tmp_path / "preflight-alias.json"
    alias.symlink_to(sentinels["r2_preflight"])
    output_alias = tmp_path / "output-alias.json"
    output_alias.symlink_to(sentinels["custom_output"])
    original = {name: path.read_bytes() for name, path in sentinels.items()}
    safe_output = tmp_path / "safe" / "report-r2-a003.json"

    def unexpected_postprocess(**_kwargs):
        raise AssertionError("historical guard did not run before analysis")

    monkeypatch.setattr(
        independent, "independently_postprocess", unexpected_postprocess
    )

    def argv(**overrides):
        values = {
            "chain_prefix": tmp_path / "safe" / "w0wa_exact_isolated_r2_a003",
            "updated_config": tmp_path / "safe" / "isolated-r2-a003.updated.yaml",
            "environment_preflight": tmp_path / "safe" / "preflight-r2-a003.json",
            "output": safe_output,
        }
        values.update(overrides)
        return [
            "--chain-prefix", str(values["chain_prefix"]),
            "--updated-config", str(values["updated_config"]),
            "--run-id", "historical-state-guard-test",
            "--primary-execution-fingerprint", "sha256:" + "1" * 64,
            "--environment-fingerprint", "sha256:" + "2" * 64,
            "--environment-preflight", str(values["environment_preflight"]),
            "--output", str(values["output"]),
        ]

    cases = (
        argv(chain_prefix=chain_root / "w0wa_exact_isolated_r2"),
        argv(updated_config=sentinels["r2_config"]),
        argv(environment_preflight=alias),
        argv(output=sentinels["r2_output"]),
        argv(output=sentinels["revision_1_output"]),
        argv(output=sentinels["custom_output"]),
        argv(output=output_alias),
    )
    for case in cases:
        assert independent.main(case) == 2

    monkeypatch.setattr(
        independent.sys,
        "executable",
        str(local_root / "isolated-venv-r2" / "bin" / "python"),
    )
    assert independent.main(argv()) == 2
    assert {name: path.read_bytes() for name, path in sentinels.items()} == original
    assert alias.is_symlink()
    assert output_alias.is_symlink()
    assert not safe_output.exists()


def test_chain_parse_and_hash_share_one_immutable_single_fd_snapshot(
    tmp_path, monkeypatch
):
    prefix, config_path, _ = _write_chains(tmp_path)
    chain_paths = [Path(f"{prefix}.{index}.txt").resolve() for index in range(1, 5)]
    first_chain = chain_paths[0]
    original_bytes = first_chain.read_bytes()
    original_sha256 = independent._hash_bytes(original_bytes)
    mutation = b"\n# adversarial replacement after immutable snapshot\n"
    open_counts = {str(path): 0 for path in chain_paths}
    concrete_path_type = type(first_chain)
    real_path_open = concrete_path_type.open

    def counted_path_open(self, *args, **kwargs):
        key = str(self)
        if key in open_counts:
            open_counts[key] += 1
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(concrete_path_type, "open", counted_path_open)
    real_snapshot_reader = independent._read_chain_snapshot
    snapshot_calls = {str(path): 0 for path in chain_paths}

    def snapshot_then_replace(path):
        snapshot_calls[str(path)] += 1
        result = real_snapshot_reader(path)
        if path == first_chain:
            # This mutation happens after parsing and SHA-256 were derived from
            # the immutable bytes. Any later path-based hash would now diverge.
            with open(path, "ab") as handle:
                handle.write(mutation)
        return result

    monkeypatch.setattr(
        independent,
        "_read_chain_snapshot",
        snapshot_then_replace,
    )
    report = independent.independently_postprocess(
        chain_prefix=prefix,
        updated_config_path=config_path,
        run_id="single-snapshot-toctou-attack",
        burn_fraction=0.30,
    )

    assert report["status"] == "PASS", report["failures"]
    assert open_counts == {str(path): 1 for path in chain_paths}
    assert snapshot_calls == {str(path): 1 for path in chain_paths}
    assert report["chain_files"][0]["sha256"] == original_sha256
    assert report["chain_files"][0]["snapshot_size_bytes"] == len(original_bytes)
    assert first_chain.stat().st_size == len(original_bytes) + len(mutation)
    assert independent._hash_bytes(original_bytes + mutation) != original_sha256
    assert {record["snapshot_policy"] for record in report["chain_files"]} == {
        "single_fd_immutable_bytes_parse_and_sha256"
    }


def test_weighted_unequal_chains_keep_all_reporting_rows_after_raw_row_burn(tmp_path):
    config = {
        "params": {
            "cosmo": {"prior": {"min": -1, "max": 1}},
            "w": {"prior": {"min": -3, "max": 1}},
            "wa": {"prior": {"min": -3, "max": 2}},
            "omegam": {"latex": "Omega_m"},
            "H0": {"latex": "H_0"},
        }
    }
    config_path = tmp_path / "weighted.updated.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    prefix = tmp_path / "weighted"
    names = ["weight", "minuslogpost", "cosmo", "w", "wa", "omegam", "H0"]
    for index, n_rows in enumerate((1900, 1950, 2000, 2050), start=1):
        rng = np.random.default_rng(12000 + index)
        # Give the extra rows in longer chains a detectable but converged-scale
        # offset. Reporting must retain them; only diagnostics are aligned.
        location = -0.80 + 0.002 * index
        rows = np.column_stack(
            [
                rng.integers(1, 4, size=n_rows),
                rng.normal(100.0, 1.0, n_rows),
                rng.normal(0.1, 0.01, n_rows),
                rng.normal(location, 0.06, n_rows),
                rng.normal(-0.7, 0.25, n_rows),
                rng.normal(0.31, 0.007, n_rows),
                rng.normal(68.0, 0.7, n_rows),
            ]
        )
        np.savetxt(
            Path(f"{prefix}.{index}.txt"),
            rows,
            header=" ".join(names),
            comments="# ",
        )

    _, canonical_data = canonical.diagnose_chains(
        prefix, updated_config=config, burn_fraction=0.30
    )
    expected = canonical.posterior_intervals(canonical_data)
    report = independent.independently_postprocess(
        chain_prefix=prefix,
        updated_config_path=config_path,
        run_id="weighted-isolated-run",
        burn_fraction=0.30,
    )
    assert report["status"] == "PASS", report["failures"]
    assert report["chain_length_balance_passed"] is True
    assert min(report["diagnostic_alignment_fraction_per_chain"]) >= 0.90
    assert [record["post_burn_rows"] for record in report["chain_files"]] == [
        1330,
        1365,
        1400,
        1435,
    ]
    for name in independent.REPORT_PARAMETERS:
        expected_report = canonical.table3_reported_interval(name, expected[name])
        assert report["intervals_68"][name]["center"] == pytest.approx(
            expected_report["center"], rel=1e-12
        )
        assert report["intervals_68"][name]["lower_68"] == pytest.approx(
            expected_report["lower_68"], rel=1e-12
        )


def test_independent_postprocessor_withholds_imbalanced_chain_lengths(tmp_path):
    prefix, config_path, config = _write_chains(
        tmp_path,
        chain_lengths=(1600, 1600, 1600, 800),
    )
    primary_diagnostics, _ = canonical.diagnose_chains(
        prefix,
        updated_config=config,
        burn_fraction=0.30,
    )
    report = independent.independently_postprocess(
        chain_prefix=prefix,
        updated_config_path=config_path,
        run_id="imbalanced-isolated-run",
        burn_fraction=0.30,
    )

    expected_reason = "chain_lengths:diagnostic_alignment_fraction_below_0.90"
    assert expected_reason in primary_diagnostics["reasons"]
    assert report["status"] == "WITHHELD"
    assert report["failures"] == [expected_reason]
    assert report["chain_length_balance_passed"] is False
    assert report["diagnostic_alignment_fraction_per_chain"] == pytest.approx(
        [0.5, 0.5, 0.5, 1.0]
    )
    assert report["maximum_diagnostic_discarded_fraction"] == pytest.approx(0.5)
    assert "intervals_68" not in report
