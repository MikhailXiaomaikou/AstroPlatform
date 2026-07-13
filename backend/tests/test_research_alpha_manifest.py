from __future__ import annotations

import base64
import copy
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.research_alpha_test_support import (
    artifact,
    build_manifest,
    make_manifest_kwargs,
    sha,
    write_attestation,
    write_json_artifact,
    write_named_hash_artifact,
    write_plain,
)


def _hidden_record() -> dict:
    return {
        "full_paper_read_status": "complete",
        "target_hash": sha("1"),
        "expected_datasets": ["DESI DR1 BAO", "Planck high-l likelihood"],
        "expected_methods": ["full likelihood"],
        "expected_models": ["LCDM"],
        "expected_direction_terms": ["H0"],
        "expected_numbers": [{"name": "H0", "value": 67.36, "tolerance_abs": 0.01}],
    }


def _site_packages_ownership_identity() -> dict:
    from app.services.server_evidence import scientific_content_hash
    from app.services.w0wa_exact_contract import GENERATED_BYTECODE_CACHE_POLICY

    payload = {
        "schema_version": 1,
        "site_root_count": 1,
        "owned_import_files": {
            "count": 1,
            "fingerprint": sha("7"),
        },
        "generated_bytecode_policy": dict(GENERATED_BYTECODE_CACHE_POLICY),
        "unowned_import_files": [],
        "unowned_generated_bytecode": [],
        "symlinked_directories": [],
    }
    return {
        **payload,
        "passed": True,
        "reasons": [],
        "fingerprint": scientific_content_hash(payload),
    }


def _runtime_closure_identity(
    required_versions: dict[str, str],
    distributions: dict[str, dict],
) -> dict:
    from app.services.server_evidence import scientific_content_hash
    from app.services.w0wa_exact_contract import FROZEN_BOOTSTRAP_DISTRIBUTIONS

    fingerprints = {
        name: {
            "version": version,
            "fingerprint": distributions[name]["fingerprint"],
        }
        for name, version in required_versions.items()
    }
    fingerprints.update(FROZEN_BOOTSTRAP_DISTRIBUTIONS)
    payload = {
        "required_versions": dict(sorted(required_versions.items())),
        "dependency_closure": sorted(required_versions),
        "installed_distributions": sorted(fingerprints),
        "bootstrap_distributions": FROZEN_BOOTSTRAP_DISTRIBUTIONS,
        "distribution_fingerprints": fingerprints,
        "site_packages_ownership": _site_packages_ownership_identity(),
    }
    return {
        **payload,
        "passed": True,
        "reasons": [],
        "fingerprint": scientific_content_hash(payload),
    }


def _rewrite_self_hashed_json(path: Path, mutate) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    attestation_type = payload.get("attestation_type")
    if isinstance(attestation_type, str):
        from app.services.research_alpha_attestation import (
            build_scientific_attestation,
        )

        mutate(payload)
        if attestation_type == "research_alpha_adequacy":
            receipt_record = payload.get("canonical_aggregate_receipt_artifact")
            if isinstance(receipt_record, dict):
                receipt_path = Path(receipt_record["path"])
                receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
                receipt_payload.pop("aggregate_sha256", None)
                for key in (
                    "run_id",
                    "execution_fingerprint",
                    "check",
                    "status",
                    "metrics",
                ):
                    receipt_payload[key] = payload[key]
                payload["canonical_aggregate_receipt_artifact"] = (
                    write_named_hash_artifact(
                        receipt_path,
                        receipt_payload,
                        hash_field="aggregate_sha256",
                    )
                )
        signed = build_scientific_attestation(
            attestation_type=attestation_type,
            payload=payload,
        )
        return write_attestation(path, signed)
    if "report_sha256" in payload:
        payload.pop("report_sha256", None)
        mutate(payload)
        return write_named_hash_artifact(
            path, payload, hash_field="report_sha256"
        )
    payload.pop("self_hash", None)
    mutate(payload)
    return write_json_artifact(path, payload)


def test_actual_frozen_data_manifest_matches_consumer_commitment_policy() -> None:
    """Large producer groups freeze summaries; compact groups freeze all files."""

    from app.services.research_alpha_manifest import (
        _validate_exact_trusted_data_groups,
    )
    from app.services.w0wa_exact_contract import REQUIRED_DATA_GROUPS

    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "scripts/cobaya/w0wa_exact_data_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    groups = manifest["groups"]
    summary_only = {
        name for name, record in groups.items() if "files" not in record
    }
    assert summary_only == {
        "planck_2018_lowl.TT_clik",
        "planck_2018_lowl.EE_clik",
        "planck_2018_highl_plik.TTTEEE",
        "act_dr6_lenslike.ACTDR6LensLike",
    }
    observed = {
        name: {"files": record.get("files", [])}
        for name, record in groups.items()
    }
    _validate_exact_trusted_data_groups(manifest, observed)

    assert {
        name: {
            key: groups[name][key]
            for key in ("file_count", "total_size_bytes", "fingerprint")
        }
        for name in groups
    } == REQUIRED_DATA_GROUPS
    tampered = json.loads(json.dumps(manifest))
    tampered["groups"]["planck_2018_highl_plik.TTTEEE"]["file_count"] -= 1
    with pytest.raises(ValueError, match="trusted data group drifted"):
        _validate_exact_trusted_data_groups(tampered, observed)


def test_actual_likelihood_code_manifest_and_reference_registry_are_consumable(
    tmp_path: Path,
) -> None:
    from app.services.research_alpha_manifest import (
        _validate_exact_likelihood_code_manifest,
        _validate_exact_reference_values,
    )
    from app.services.server_evidence import scientific_content_hash
    from app.services.w0wa_exact_contract import (
        TRUSTED_CANONICAL_CONFIG_SHA256,
        TRUSTED_DATA_INVENTORY_SHA256,
    )

    scripts = Path(__file__).resolve().parents[1] / "scripts/cobaya"
    code_path = scripts / "w0wa_exact_likelihood_code_manifest.json"
    code_payload = json.loads(code_path.read_text(encoding="utf-8"))
    _validate_exact_likelihood_code_manifest(
        {
            "passed": True,
            "reasons": [],
            "payload": code_payload,
            **artifact(code_path),
        }
    )

    reference_path = scripts / "w0wa_exact_reference_cases.json"
    reference_payload = json.loads(reference_path.read_text(encoding="utf-8"))
    observed = {
        case["case_id"]: {
            name: specification["expected_chi2"]
            for name, specification in case["values"].items()
        }
        for case in reference_payload["cases"]
    }
    camb_origin = tmp_path / "camb.py"
    camb_origin.write_text("# locked CAMB fixture\n", encoding="utf-8")
    clipy_root = tmp_path / "clipy-root"
    clipy_root.mkdir()
    clipy_origin = clipy_root / "clipy.py"
    clipy_origin.write_text("__version__ = '0.15'\n", encoding="utf-8")
    clipy_file = {
        "path": "clipy.py",
        "size_bytes": clipy_origin.stat().st_size,
        "sha256": artifact(clipy_origin)["sha256"],
    }
    act_origin = tmp_path / "act_dr6_lenslike.py"
    act_origin.write_text("# locked ACT fixture\n", encoding="utf-8")
    camspec_origin = tmp_path / "TTTEEE.py"
    camspec_origin.write_text("# locked CamSpec fixture\n", encoding="utf-8")
    runtime = {
        "camb": {"version": "1.6.6", "origin": str(camb_origin)},
        "clipy": {
            "expected_version": "0.15",
            "root": str(clipy_root),
            "files": [clipy_file],
            "tree_fingerprint": scientific_content_hash([clipy_file]),
            "loaded_origin": str(clipy_origin),
            "loaded_version": "0.15",
        },
        "act_dr6_lenslike": {
            "version": "1.2.1",
            "fingerprint": sha("a"),
            "loaded_origin": str(act_origin),
        },
        "planck_NPIPE_highl_CamSpec": {
            "loaded_origin": str(camspec_origin),
            "sha256": artifact(camspec_origin)["sha256"],
            "cobaya_version": "3.6.2",
        },
    }
    reference = {
        "passed": True,
        "reasons": [],
        "configuration_sha256": TRUSTED_CANONICAL_CONFIG_SHA256,
        "data_fingerprint": TRUSTED_DATA_INVENTORY_SHA256,
        "payload": reference_payload,
        "live_observed_chi2_by_case": observed,
        "loaded_likelihood_runtime": runtime,
        **artifact(reference_path),
    }
    _validate_exact_reference_values(reference)

    shifted = json.loads(json.dumps(reference))
    first_case = reference_payload["cases"][0]
    first_name = first_case["likelihoods"][0]
    shifted["live_observed_chi2_by_case"][first_case["case_id"]][
        first_name
    ] += 1.0
    with pytest.raises(ValueError, match="reference value failed"):
        _validate_exact_reference_values(shifted)


def test_exact_diagnostics_require_unforgeable_chain_length_balance() -> None:
    from app.services.research_alpha_manifest import _normalize_diagnostics
    from app.services.w0wa_exact_contract import EXACT_PROFILE_ID

    def parameter(reference: float) -> dict:
        return {
            "rhat": 1.001,
            "ess_bulk": 1200.0,
            "mcse_mean": reference * 0.01,
            "posterior_std": reference * 2.0,
            "mcse_reference_kind": "paper_sigma",
            "mcse_reference_value": reference,
            "mcse_over_reference_sigma": 0.01,
        }

    diagnostics = {
        "status": "passed",
        "metrics": {
            "rhat_method": "rank_normalized",
            "ess_method": "bulk",
            "mcse_reference": "per_parameter",
            "environment_fingerprint": sha("e"),
            "n_independent_chains": 4,
            "chain_length_balance": {
                "alignment": (
                    "diagnostics_only_recent_draws_truncated_to_shortest_chain"
                ),
                "minimum_alignment_fraction_inclusive": 0.90,
                "alignment_fraction_per_chain": [1.0, 0.95, 0.90, 0.92],
                "maximum_discarded_fraction": 0.10,
                "passed": True,
            },
            "critical_parameters": ["w", "wa"],
            "per_parameter": {
                "w": parameter(0.063),
                "w0": parameter(0.063),
                "wa": parameter(0.25),
            },
        },
    }
    _normalize_diagnostics(
        diagnostics,
        sampled_parameters=["w", "wa"],
        result_names={"w0", "wa"},
        expected_chain_count=4,
        profile_id=EXACT_PROFILE_ID,
    )

    missing = json.loads(json.dumps(diagnostics))
    missing["metrics"].pop("chain_length_balance")
    with pytest.raises(ValueError, match="balance evidence is missing"):
        _normalize_diagnostics(
            missing,
            sampled_parameters=["w", "wa"],
            result_names={"w0", "wa"},
            expected_chain_count=4,
            profile_id=EXACT_PROFILE_ID,
        )

    forged = json.loads(json.dumps(diagnostics))
    forged["metrics"]["chain_length_balance"].update(
        {
            "alignment_fraction_per_chain": [1.0, 0.899, 0.95, 0.92],
            "maximum_discarded_fraction": 0.001,
        }
    )
    with pytest.raises(ValueError, match="alignment is below 0.90"):
        _normalize_diagnostics(
            forged,
            sampled_parameters=["w", "wa"],
            result_names={"w0", "wa"},
            expected_chain_count=4,
            profile_id=EXACT_PROFILE_ID,
        )

    inconsistent = json.loads(json.dumps(diagnostics))
    inconsistent["metrics"]["chain_length_balance"][
        "maximum_discarded_fraction"
    ] = 0.01
    with pytest.raises(ValueError, match="discard fraction is inconsistent"):
        _normalize_diagnostics(
            inconsistent,
            sampled_parameters=["w", "wa"],
            result_names={"w0", "wa"},
            expected_chain_count=4,
            profile_id=EXACT_PROFILE_ID,
        )


def test_independent_environment_preflight_binding_rejects_forgery_and_reuse(
    tmp_path: Path,
) -> None:
    from app.services.research_alpha_manifest import (
        _runtime_environment_fingerprint,
        _validate_independent_environment_preflight_binding,
    )
    from app.services.server_evidence import scientific_content_hash
    from app.services.w0wa_exact_contract import (
        EXACT_PROFILE_ID,
        PREREGISTERED_TARGET_COMMITMENT,
    )

    interpreter_link = tmp_path / "isolated-venv-python"
    interpreter_link.symlink_to(Path(sys.executable).resolve())
    interpreter = str(interpreter_link.absolute())
    native_python = str(Path(sys.executable).resolve())
    required_versions = {
        f"distribution-{index:02d}": "1.0" for index in range(52)
    }
    distributions = {
        name: {
            "distribution": name,
            "installed": True,
            "version": version,
            "files": [
                {
                    "path": f"{name}/module.py",
                    "size_bytes": 1,
                    "sha256": sha("a"),
                }
            ],
            "fingerprint": scientific_content_hash(
                [
                    {
                        "path": f"{name}/module.py",
                        "size_bytes": 1,
                        "sha256": sha("a"),
                    }
                ]
            ),
        }
        for name, version in required_versions.items()
    }
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    import_policy_unsigned = {
        "schema_version": 1,
        "isolated_interpreter": True,
        "python_flag": "-I",
        "ignore_environment": True,
        "no_user_site": True,
        "safe_path": True,
        "pythonpath_empty": True,
        "user_site_disabled_by_child": True,
        "venv_root": str(tmp_path),
        "site_package_roots": [str(site_packages)],
        "startup_hooks": [],
    }
    recorded_import_policy = {
        **import_policy_unsigned,
        "passed": True,
        "reasons": [],
        "fingerprint": scientific_content_hash(import_policy_unsigned),
    }
    verified_import_policy = {
        "schema_version": 1,
        "isolated_interpreter": True,
        "ignore_environment": True,
        "no_user_site": True,
        "safe_path": True,
        "pythonpath_empty": True,
        "passed": True,
        "reasons": [],
        "preflight_import_policy_fingerprint": recorded_import_policy["fingerprint"],
        "startup_hook_fingerprint": scientific_content_hash([]),
        "verified": True,
    }
    runtime = {
        "python": "3.13-test",
        "executable": interpreter,
        "platform": "test-platform",
        "machine": "arm64",
        "packages": required_versions,
        "runtime_modules": {},
        "thread_environment": {
            "OMP_NUM_THREADS": "3",
            "MKL_NUM_THREADS": "3",
            "OPENBLAS_NUM_THREADS": "3",
        },
        "native_runtime": {
            "binaries": {"python": {"path": native_python}}
        },
        "import_policy": recorded_import_policy,
        "runtime_closure": _runtime_closure_identity(
            required_versions, distributions
        ),
    }
    environment_fingerprint = _runtime_environment_fingerprint(runtime)
    environment_unsigned = {
        "required_versions": required_versions,
        "runtime_closure": sorted(required_versions),
        "distributions": distributions,
        "runtime": runtime,
    }
    preflight_environment_fingerprint = scientific_content_hash(environment_unsigned)
    preflight = write_named_hash_artifact(
        tmp_path / "isolated-preflight.json",
        {
            "schema_version": 2,
            "artifact_type": "w0wa_exact_preflight",
            "profile_id": EXACT_PROFILE_ID,
            "target_commitment": PREREGISTERED_TARGET_COMMITMENT,
            "passed": True,
            "status": "PASS",
            "environment": {
                **environment_unsigned,
                "passed": True,
                "reasons": [],
                "fingerprint": preflight_environment_fingerprint,
            },
        },
        hash_field="preflight_sha256",
    )
    preflight_payload = json.loads(
        Path(preflight["path"]).read_text(encoding="utf-8")
    )
    environment_record = {
        **preflight,
        "preflight_sha256": preflight_payload["preflight_sha256"],
        "environment_fingerprint": environment_fingerprint,
        "preflight_environment_fingerprint": preflight_environment_fingerprint,
        "interpreter": interpreter,
        "distribution_count": 52,
        "import_policy": verified_import_policy,
        "verified": True,
    }
    binding = {
        "environment_fingerprint": environment_fingerprint,
        "environment_preflight": environment_record,
        "current_import_policy": verified_import_policy,
    }
    run_payload = {
        "environment_fingerprint": environment_fingerprint,
        "preflight_artifact": preflight,
    }
    assert _validate_independent_environment_preflight_binding(
        binding,
        run_payload=run_payload,
        expected_environment_fingerprint=environment_fingerprint,
    ) == {
        "environment_preflight_artifact": preflight,
        "runtime_environment_fingerprint": environment_fingerprint,
        "preflight_environment_fingerprint": preflight_environment_fingerprint,
        "import_policy": verified_import_policy,
        "environment_location": {
            "venv_root": str(tmp_path.resolve()),
            "sys_prefix": str(tmp_path.resolve()),
            "site_package_roots": [str(site_packages.resolve())],
        },
    }

    conflated = {
        **environment_record,
        "preflight_environment_fingerprint": environment_fingerprint,
    }
    with pytest.raises(ValueError, match="fingerprint/self-hash binding mismatch"):
        _validate_independent_environment_preflight_binding(
            {**binding, "environment_preflight": conflated},
            run_payload=run_payload,
            expected_environment_fingerprint=environment_fingerprint,
        )

    invalid_import_policy = {
        **verified_import_policy,
        "verified": False,
    }
    with pytest.raises(ValueError, match="import policy proof is invalid"):
        _validate_independent_environment_preflight_binding(
            {
                **binding,
                "current_import_policy": invalid_import_policy,
                "environment_preflight": {
                    **environment_record,
                    "import_policy": invalid_import_policy,
                },
            },
            run_payload=run_payload,
            expected_environment_fingerprint=environment_fingerprint,
        )

    missing = dict(binding)
    missing.pop("environment_preflight")
    with pytest.raises(ValueError, match="environment preflight is missing"):
        _validate_independent_environment_preflight_binding(
            missing,
            run_payload=run_payload,
            expected_environment_fingerprint=environment_fingerprint,
        )

    # A byte-identical preflight copied to a foreign run path has the same
    # content hash and self-hash, but must still fail the path identity bind.
    foreign_path = tmp_path / "foreign-run-preflight.json"
    foreign_path.write_bytes(Path(preflight["path"]).read_bytes())
    foreign_record = {
        **environment_record,
        **artifact(foreign_path),
    }
    with pytest.raises(ValueError, match="does not match run authority"):
        _validate_independent_environment_preflight_binding(
            {**binding, "environment_preflight": foreign_record},
            run_payload=run_payload,
            expected_environment_fingerprint=environment_fingerprint,
        )

    # Repointing both sides after editing bytes does not bypass the preflight
    # self-hash: the receipt itself must still be canonical and intact.
    forged_payload = dict(preflight_payload)
    forged_payload["unregistered_mutation"] = True
    forged_path = tmp_path / "forged-preflight.json"
    forged_path.write_text(
        json.dumps(forged_payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    forged_artifact = artifact(forged_path)
    forged_record = {
        **environment_record,
        **forged_artifact,
    }
    with pytest.raises(ValueError, match="preflight_sha256 mismatch"):
        _validate_independent_environment_preflight_binding(
            {**binding, "environment_preflight": forged_record},
            run_payload={
                **run_payload,
                "preflight_artifact": forged_artifact,
            },
            expected_environment_fingerprint=environment_fingerprint,
        )

    missing_interpreter = dict(environment_record)
    missing_interpreter.pop("interpreter")
    with pytest.raises(ValueError, match="preflight interpreter"):
        _validate_independent_environment_preflight_binding(
            {**binding, "environment_preflight": missing_interpreter},
            run_payload=run_payload,
            expected_environment_fingerprint=environment_fingerprint,
        )

    wrong_count = {**environment_record, "distribution_count": 51}
    with pytest.raises(ValueError, match="fingerprint/self-hash binding mismatch"):
        _validate_independent_environment_preflight_binding(
            {**binding, "environment_preflight": wrong_count},
            run_payload=run_payload,
            expected_environment_fingerprint=environment_fingerprint,
        )

    failed_environment_payload = {
        key: value
        for key, value in preflight_payload.items()
        if key != "preflight_sha256"
    }
    failed_environment_payload["environment"] = {
        **failed_environment_payload["environment"],
        "passed": False,
    }
    failed_environment = write_named_hash_artifact(
        tmp_path / "failed-environment-preflight.json",
        failed_environment_payload,
        hash_field="preflight_sha256",
    )
    failed_payload = json.loads(
        Path(failed_environment["path"]).read_text(encoding="utf-8")
    )
    failed_record = {
        **environment_record,
        **failed_environment,
        "preflight_sha256": failed_payload["preflight_sha256"],
    }
    with pytest.raises(ValueError, match="fingerprint/self-hash binding mismatch"):
        _validate_independent_environment_preflight_binding(
            {**binding, "environment_preflight": failed_record},
            run_payload={
                **run_payload,
                "preflight_artifact": failed_environment,
            },
            expected_environment_fingerprint=environment_fingerprint,
        )


def test_exact_independent_environment_locations_must_be_disjoint(
    tmp_path: Path,
) -> None:
    from app.services.research_alpha_manifest import (
        _validate_independent_environment_locations,
    )

    primary_root = tmp_path / "primary"
    independent_root = tmp_path / "independent"
    primary_site = primary_root / "lib/python/site-packages"
    independent_site = independent_root / "lib/python/site-packages"
    primary_site.mkdir(parents=True)
    independent_site.mkdir(parents=True)
    primary = {
        "venv_root": str(primary_root.resolve()),
        "sys_prefix": str(primary_root.resolve()),
        "site_package_roots": [str(primary_site.resolve())],
    }
    independent = {
        "venv_root": str(independent_root.resolve()),
        "sys_prefix": str(independent_root.resolve()),
        "site_package_roots": [str(independent_site.resolve())],
    }
    _validate_independent_environment_locations(
        primary=primary,
        independent=independent,
    )

    with pytest.raises(ValueError, match="reused the primary virtual environment"):
        _validate_independent_environment_locations(
            primary=primary,
            independent={**independent, "sys_prefix": primary["sys_prefix"]},
        )

    with pytest.raises(ValueError, match="site-package roots overlap"):
        _validate_independent_environment_locations(
            primary=primary,
            independent={
                **independent,
                "site_package_roots": [str(primary_site / "nested")],
            },
        )


def test_exact_ownership_consumer_rejects_volatile_cache_identity_fields() -> None:
    from app.services.research_alpha_manifest import (
        _validate_exact_site_packages_ownership,
    )
    from app.services.server_evidence import scientific_content_hash

    identity = _site_packages_ownership_identity()
    _validate_exact_site_packages_ownership(identity)

    volatile = copy.deepcopy(identity)
    volatile["generated_bytecode"] = {
        "count": 1,
        "fingerprint": sha("8"),
    }
    volatile["fingerprint"] = scientific_content_hash(
        {
            key: value
            for key, value in volatile.items()
            if key not in {"passed", "reasons", "fingerprint"}
        }
    )
    with pytest.raises(ValueError, match="ownership inventory did not pass"):
        _validate_exact_site_packages_ownership(volatile)

    weakened = copy.deepcopy(identity)
    weakened["generated_bytecode_policy"]["sourceless_or_unowned_cache"] = (
        "ignored"
    )
    weakened["fingerprint"] = scientific_content_hash(
        {
            key: value
            for key, value in weakened.items()
            if key not in {"passed", "reasons", "fingerprint"}
        }
    )
    with pytest.raises(ValueError, match="ownership inventory did not pass"):
        _validate_exact_site_packages_ownership(weakened)


def test_exact_run_role_allows_only_registered_independent_model_adequacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import research_alpha_manifest as research

    canonical = artifact(
        Path(__file__).resolve().parents[1]
        / "scripts/cobaya/w0wa_desi_cmb_pantheonplus_exact.yaml"
    )
    assert canonical["sha256"] == research.TRUSTED_CANONICAL_CONFIG_SHA256
    independent = write_plain(
        tmp_path / "independent.yaml",
        "sampler:\n  mcmc:\n    seed: [71001931, 82350647, 94110763, 105320087]\n",
    )
    unregistered = write_plain(tmp_path / "unregistered.yaml", "sampler: {}\n")
    validated_plan = {
        "configs": {
            "planck_pr3_plik": canonical,
            "independent_reproduction": independent,
        }
    }
    monkeypatch.setattr(
        research,
        "_exact_plan_artifact_payload",
        lambda payload: ({}, validated_plan, {}, {}),
    )

    assert research._validate_exact_run_role(
        config=canonical,
        generation_payload={"validated": True},
        evidence_class="formal_candidate",
        expected_role="primary",
    )[0] == "primary"
    assert research._validate_exact_run_role(
        config=independent,
        generation_payload={"validated": True},
        evidence_class="model_adequacy",
        expected_role="independent_reproduction",
    )[0] == "independent_reproduction"

    with pytest.raises(ValueError, match="role mismatch"):
        research._validate_exact_run_role(
            config=independent,
            generation_payload={"validated": True},
            evidence_class="model_adequacy",
            expected_role="primary",
        )
    with pytest.raises(ValueError, match="canonical config SHA-256"):
        research._validate_exact_run_role(
            config=independent,
            generation_payload={"validated": True},
            evidence_class="formal_candidate",
        )
    with pytest.raises(ValueError, match="registered generation config"):
        research._validate_exact_run_role(
            config=unregistered,
            generation_payload={"validated": True},
            evidence_class="model_adequacy",
        )
    with pytest.raises(ValueError, match="registered generation config"):
        research._validate_exact_run_role(
            config=canonical,
            generation_payload={"validated": True},
            evidence_class="model_adequacy",
        )


def test_exact_preflight_consumer_revalidates_full_environment_closure(
    tmp_path: Path,
) -> None:
    from app.services.research_alpha_manifest import (
        _validate_exact_environment_closure,
    )
    from app.services.server_evidence import scientific_content_hash

    required_versions = {
        f"distribution-{index:02d}": "1.0" for index in range(52)
    }
    distributions: dict[str, dict] = {}
    manifest_wheels: list[dict] = []
    observed_wheels: dict[str, dict] = {}
    bindings: dict[str, dict] = {}
    for index, (name, version) in enumerate(required_versions.items()):
        files = [
            {
                "path": f"{name}/module.py",
                "size_bytes": index + 1,
                "sha256": sha(chr(ord("a") + index % 6)),
            }
        ]
        distributions[name] = {
            "distribution": name,
            "installed": True,
            "version": version,
            "files": files,
            "fingerprint": scientific_content_hash(files),
        }
        filename = f"{name}-1.0-py3-none-any.whl"
        wheel_path = tmp_path / filename
        module_name = f"{name}/module.py"
        module_bytes = b"x"
        record_path = f"{name.replace('-', '_')}-1.0.dist-info/RECORD"
        record_digest = base64.urlsafe_b64encode(
            hashlib.sha256(module_bytes).digest()
        ).decode("ascii").rstrip("=")
        with zipfile.ZipFile(wheel_path, "w") as archive:
            archive.writestr(module_name, module_bytes)
            archive.writestr(
                record_path,
                f"{module_name},sha256={record_digest},1\n{record_path},,\n",
            )
        wheel_artifact = artifact(wheel_path)
        wheel_sha = wheel_artifact["sha256"]
        manifest_wheels.append(
            {
                "project": name,
                "version": version,
                "filename": filename,
                "sha256": wheel_sha,
            }
        )
        observed_wheels[filename] = {
            "path": wheel_artifact["path"],
            "sha256": wheel_sha,
        }
        payload_fingerprint = scientific_content_hash(
            [
                {
                    "wheel_member": module_name,
                    "installed_relative_path": module_name,
                    "size_bytes": 1,
                    "sha256": "sha256:" + hashlib.sha256(module_bytes).hexdigest(),
                }
            ]
        )
        bindings[name] = {
            "passed": True,
            "reasons": [],
            "distribution": name,
            "version": version,
            "wheel_path": observed_wheels[filename]["path"],
            "wheel_sha256": wheel_sha,
            "expected_payload_fingerprint": payload_fingerprint,
            "installed_payload_fingerprint": payload_fingerprint,
            "checked_file_count": 1,
            "skipped_installer_rewritten_members": [],
        }
    import_policy_unsigned = {
        "schema_version": 1,
        "isolated_interpreter": True,
        "python_flag": "-I",
        "ignore_environment": True,
        "no_user_site": True,
        "safe_path": True,
        "pythonpath_empty": True,
        "user_site_disabled_by_child": True,
        "venv_root": str(tmp_path),
        "site_package_roots": [],
        "startup_hooks": [],
    }
    environment = {
        "required_versions": required_versions,
        "runtime_closure": sorted(required_versions),
        "distributions": distributions,
        "installed_wheel_bindings": bindings,
        "runtime": {
            "import_policy": {
                **import_policy_unsigned,
                "passed": True,
                "reasons": [],
                "fingerprint": scientific_content_hash(import_policy_unsigned),
            },
            "runtime_closure": _runtime_closure_identity(
                required_versions, distributions
            ),
        },
    }
    _validate_exact_environment_closure(
        environment,
        manifest_wheels=manifest_wheels,
        observed_wheel_artifacts=observed_wheels,
    )

    bad_closure = json.loads(json.dumps(environment))
    bad_closure["runtime_closure"].pop()
    with pytest.raises(ValueError, match="runtime closure"):
        _validate_exact_environment_closure(
            bad_closure,
            manifest_wheels=manifest_wheels,
            observed_wheel_artifacts=observed_wheels,
        )

    bad_distribution = json.loads(json.dumps(environment))
    bad_distribution["distributions"]["distribution-00"]["fingerprint"] = sha("f")
    with pytest.raises(ValueError, match="distribution fingerprint"):
        _validate_exact_environment_closure(
            bad_distribution,
            manifest_wheels=manifest_wheels,
            observed_wheel_artifacts=observed_wheels,
        )

    bad_binding = json.loads(json.dumps(environment))
    bad_binding["installed_wheel_bindings"]["distribution-00"]["passed"] = False
    with pytest.raises(ValueError, match="installed wheel binding is invalid"):
        _validate_exact_environment_closure(
            bad_binding,
            manifest_wheels=manifest_wheels,
            observed_wheel_artifacts=observed_wheels,
        )

    forged_payload = json.loads(json.dumps(environment))
    forged_payload["installed_wheel_bindings"]["distribution-00"].update(
        {
            "expected_payload_fingerprint": sha("f"),
            "installed_payload_fingerprint": sha("f"),
        }
    )
    with pytest.raises(ValueError, match="installed wheel binding is invalid"):
        _validate_exact_environment_closure(
            forged_payload,
            manifest_wheels=manifest_wheels,
            observed_wheel_artifacts=observed_wheels,
        )


def test_exact_independent_postprocessor_requires_frozen_burn_and_import_policy() -> None:
    from app.services.research_alpha_manifest import (
        _validate_exact_independent_postprocessor_policy,
    )

    verified_policy = {
        "preflight_import_policy_fingerprint": sha("a"),
        "startup_hook_fingerprint": sha("b"),
    }
    report = {
        "burn_fraction": 0.30,
        "burn_convention": "getdist_remove_fraction_of_raw_rows_per_chain",
        "execution_policy": {
            "mode": "research_alpha_bound",
            "formal_burn_fraction": 0.30,
            "formal_burn_fraction_enforced": True,
            "preflight_import_policy_verified": True,
            **verified_policy,
        },
        "research_alpha_binding": {"current_import_policy": verified_policy},
    }
    _validate_exact_independent_postprocessor_policy(report)

    wrong_burn = json.loads(json.dumps(report))
    wrong_burn["burn_fraction"] = 0.20
    with pytest.raises(ValueError, match="burn_fraction=0.30"):
        _validate_exact_independent_postprocessor_policy(wrong_burn)

    unbound_imports = json.loads(json.dumps(report))
    unbound_imports["execution_policy"]["startup_hook_fingerprint"] = sha("c")
    with pytest.raises(ValueError, match="bound import policy"):
        _validate_exact_independent_postprocessor_policy(unbound_imports)


def test_ci_builder_produces_file_backed_but_withheld_manifest(tmp_path: Path) -> None:
    from app.services.research_alpha_manifest import (
        build_research_alpha_manifest,
        validate_research_alpha_manifest,
    )

    manifest = build_research_alpha_manifest(**make_manifest_kwargs(tmp_path))

    assert manifest["profile_id"] == "research_alpha_ci_fixture_v1"
    assert manifest["readiness_status"] == "CI_FIXTURE_WITHHELD"
    assert manifest["publication_gate"]["eligible"] is False
    assert manifest["external_review"] == {"status": "pending_external_review"}
    assert len(manifest["artifacts"]["chains"]) == 4
    assert validate_research_alpha_manifest(
        manifest, expected_run_id="test-run-H0"
    ) == {"valid": True, "reasons": []}


def test_sha_shaped_strings_and_reused_chain_files_are_rejected(tmp_path: Path) -> None:
    from app.services.research_alpha_manifest import build_research_alpha_manifest

    kwargs = make_manifest_kwargs(tmp_path)
    kwargs["config_artifact"] = {
        "path": str(tmp_path / "missing.yaml"),
        "sha256": sha("a"),
    }
    with pytest.raises(ValueError, match="not an existing file"):
        build_research_alpha_manifest(**kwargs)

    kwargs = make_manifest_kwargs(tmp_path / "second")
    kwargs["chain_artifacts"][0].pop("attestation")
    with pytest.raises(ValueError, match="attestation must be an artifact mapping"):
        build_research_alpha_manifest(**kwargs)

    kwargs = make_manifest_kwargs(tmp_path / "third")
    kwargs["chain_artifacts"][3] = dict(kwargs["chain_artifacts"][0])
    with pytest.raises(ValueError, match="values must be unique"):
        build_research_alpha_manifest(**kwargs)


def test_public_exact_signer_rejects_toy_ci_artifacts(tmp_path: Path) -> None:
    """The former 1000-row fake-data P0 cannot be relabelled as exact."""

    from app.services.research_alpha_manifest import (
        build_research_alpha_run_authority_attestation,
    )
    from app.services.w0wa_exact_contract import (
        EXACT_HOST_EXECUTION_TRUST_BOUNDARY,
    )

    kwargs = make_manifest_kwargs(tmp_path)
    wrapper = json.loads(
        Path(kwargs["run_attestation_artifact"]["path"]).read_text(encoding="utf-8")
    )
    receipt_record = wrapper["canonical_run_receipt_artifact"]
    receipt_path = Path(receipt_record["path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt.pop("attestation_sha256", None)
    receipt.update(
        {
            "profile_id": "desi_2024_vi_table3_desi_cmb_pantheonplus_v1",
            "evidence_class": "formal_candidate",
            "protocol_status": {
                "target_preregistration": "frozen",
                "computation_answer_key_separation": "enforced",
                "analyst_blinding": "not_achieved",
            },
        }
    )
    relabelled = write_named_hash_artifact(
        receipt_path,
        receipt,
        hash_field="attestation_sha256",
    )

    with pytest.raises(ValueError, match="host execution trust boundary"):
        build_research_alpha_run_authority_attestation(
            run_id=kwargs["run_id"],
            chain_artifacts=kwargs["chain_artifacts"],
            config_artifact=kwargs["config_artifact"],
            data_artifacts=kwargs["data_artifacts"],
            likelihood_artifacts=kwargs["likelihood_artifacts"],
            sampled_parameters_artifact=kwargs["sampled_parameters_artifact"],
            canonical_run_receipt_artifact=relabelled,
            preflight_artifact=wrapper["preflight_artifact"],
            generation_artifact=wrapper["generation_artifact"],
            code_artifacts=wrapper["code_artifacts"],
            protocol_amendment_artifact=wrapper["protocol_amendment_artifact"],
        )

    receipt["host_execution_trust_boundary"] = copy.deepcopy(
        EXACT_HOST_EXECUTION_TRUST_BOUNDARY
    )
    relabelled = write_named_hash_artifact(
        receipt_path,
        receipt,
        hash_field="attestation_sha256",
    )

    with pytest.raises(ValueError, match="canonical config SHA-256 is not frozen"):
        build_research_alpha_run_authority_attestation(
            run_id=kwargs["run_id"],
            chain_artifacts=kwargs["chain_artifacts"],
            config_artifact=kwargs["config_artifact"],
            data_artifacts=kwargs["data_artifacts"],
            likelihood_artifacts=kwargs["likelihood_artifacts"],
            sampled_parameters_artifact=kwargs["sampled_parameters_artifact"],
            canonical_run_receipt_artifact=relabelled,
            preflight_artifact=wrapper["preflight_artifact"],
            generation_artifact=wrapper["generation_artifact"],
            code_artifacts=wrapper["code_artifacts"],
            protocol_amendment_artifact=wrapper["protocol_amendment_artifact"],
        )


def test_three_row_or_text_chain_exploit_is_rejected(tmp_path: Path) -> None:
    from app.services.research_alpha_manifest import build_research_alpha_manifest

    kwargs = make_manifest_kwargs(tmp_path)
    chain = kwargs["chain_artifacts"][0]
    chain_path = Path(chain["path"])
    chain_path.write_text(
        "# weight minuslogpost H0 calibration\n"
        "1 10 67.3 1.0\n1 10 67.4 1.0\n1 10 67.5 1.0\n",
        encoding="utf-8",
    )
    replacement = artifact(chain_path)
    sidecar_path = Path(chain["attestation"]["path"])
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar.pop("self_hash", None)
    sidecar.update(
        {
            "chain_sha256": replacement["sha256"],
            "columns": ["weight", "minuslogpost", "H0", "calibration"],
            "n_draws": 3,
        }
    )
    chain.update(replacement)
    chain["attestation"] = write_json_artifact(sidecar_path, sidecar)

    with pytest.raises(ValueError, match="fewer than 1000 Cobaya draws"):
        build_research_alpha_manifest(**kwargs)


def test_ess_999_point_9_and_missing_protocol_adjudication_are_withheld(
    tmp_path: Path,
) -> None:
    from app.services import research_alpha_manifest as research

    build_research_alpha_manifest = research.build_research_alpha_manifest

    kwargs = make_manifest_kwargs(tmp_path / "ess")
    for record in kwargs["diagnostics"]["metrics"]["per_parameter"].values():
        record["ess_bulk"] = 999.9
    with pytest.raises(ValueError, match="bulk ESS is below 1000"):
        build_research_alpha_manifest(**kwargs)

    with pytest.raises(ValueError, match="protocol adjudication is missing"):
        research._validate_protocol_eligibility(
            {
                "target_preregistration": "frozen",
                "computation_answer_key_separation": "enforced",
                "analyst_blinding": "not_achieved",
            },
            None,
            run_id="known-target-run",
            target_hash=sha("1"),
        )

def test_signed_manifest_fails_after_any_artifact_byte_changes(tmp_path: Path) -> None:
    from app.services.research_alpha_manifest import validate_research_alpha_manifest

    manifest = build_manifest(tmp_path)
    config_path = Path(manifest["artifacts"]["config"]["path"])
    config_path.write_text("model: tampered\n", encoding="utf-8")

    validation = validate_research_alpha_manifest(manifest)
    assert validation["valid"] is False
    assert any("sha256_does_not_match_file_bytes" in item for item in validation["reasons"])


def test_critical_parameters_must_equal_independent_sampled_parameter_artifact(
    tmp_path: Path,
) -> None:
    from app.services.research_alpha_manifest import build_research_alpha_manifest

    kwargs = make_manifest_kwargs(tmp_path)
    kwargs["diagnostics"]["metrics"]["critical_parameters"] = ["H0"]

    with pytest.raises(ValueError, match="do not exactly match sampled-parameter"):
        build_research_alpha_manifest(**kwargs)


def test_adequacy_binding_is_preserved_and_cross_run_reuse_is_rejected(
    tmp_path: Path,
) -> None:
    from app.services.research_alpha_manifest import build_research_alpha_manifest

    kwargs = make_manifest_kwargs(tmp_path)
    record = kwargs["adequacy_evidence_by_check"]["prior_predictive_check"]
    path = Path(record["path"])
    replacement = _rewrite_self_hashed_json(
        path,
        lambda payload: payload.update({"run_id": "different-run"}),
    )
    kwargs["adequacy_evidence_by_check"]["prior_predictive_check"] = replacement

    with pytest.raises(ValueError, match="run_id mismatch"):
        build_research_alpha_manifest(**kwargs)


def test_name_only_or_contradictory_adequacy_reports_cannot_pass(tmp_path: Path) -> None:
    from app.services.research_alpha_manifest import build_research_alpha_manifest

    kwargs = make_manifest_kwargs(tmp_path)
    record = kwargs["adequacy_evidence_by_check"]["posterior_predictive_check"]
    path = Path(record["path"])
    kwargs["adequacy_evidence_by_check"]["posterior_predictive_check"] = (
        _rewrite_self_hashed_json(
            path,
            lambda payload: payload.update({"metrics": {"all_passed": True}}),
        )
    )
    with pytest.raises(ValueError, match="predictive subchecks are missing"):
        build_research_alpha_manifest(**kwargs)

    kwargs = make_manifest_kwargs(tmp_path / "contradiction")
    record = kwargs["adequacy_evidence_by_check"]["simulation_recovery"]
    path = Path(record["path"])
    kwargs["adequacy_evidence_by_check"]["simulation_recovery"] = (
        _rewrite_self_hashed_json(
            path,
            lambda payload: payload["metrics"].update(
                {"passed": False, "all_inside_joint_95": False}
            ),
        )
    )
    with pytest.raises(ValueError, match="contradict passed status"):
        build_research_alpha_manifest(**kwargs)


def test_prior_and_systematics_evidence_require_real_preregistered_variants(
    tmp_path: Path,
) -> None:
    from app.services.research_alpha_manifest import build_research_alpha_manifest

    kwargs = make_manifest_kwargs(tmp_path)
    record = kwargs["adequacy_evidence_by_check"]["prior_sensitivity"]
    path = Path(record["path"])

    def make_prior_not_wider(payload: dict) -> None:
        widened = Path(payload["metrics"]["widened_prior_artifact"]["path"])
        widened.write_text(
            "params:\n  w:\n    prior: {min: -2, max: 0}\n  wa:\n    prior: {min: -3, max: 1}\n",
            encoding="utf-8",
        )
        payload["metrics"]["widened_prior_artifact"] = artifact(widened)

    kwargs["adequacy_evidence_by_check"]["prior_sensitivity"] = (
        _rewrite_self_hashed_json(path, make_prior_not_wider)
    )
    with pytest.raises(
        ValueError,
        match="does not match file bytes|did not widen w0 prior",
    ):
        build_research_alpha_manifest(**kwargs)

    kwargs = make_manifest_kwargs(tmp_path / "missing-variant")
    record = kwargs["adequacy_evidence_by_check"]["systematics_robustness"]
    path = Path(record["path"])

    def drop_variant(payload: dict) -> None:
        payload["metrics"]["variants"] = payload["metrics"]["variants"][:-1]

    kwargs["adequacy_evidence_by_check"]["systematics_robustness"] = (
        _rewrite_self_hashed_json(path, drop_variant)
    )
    with pytest.raises(ValueError, match="required variant set is incomplete"):
        build_research_alpha_manifest(**kwargs)


def test_independent_reproduction_cannot_reuse_primary_run_seed_or_chain(
    tmp_path: Path,
) -> None:
    from app.services.research_alpha_manifest import build_research_alpha_manifest

    kwargs = make_manifest_kwargs(tmp_path)
    record = kwargs["adequacy_evidence_by_check"]["independent_reproduction"]
    path = Path(record["path"])

    def reuse(payload: dict) -> None:
        report_record = payload["metrics"]["postprocessor_report_artifact"]
        report_path = Path(report_record["path"])
        replacement = _rewrite_self_hashed_json(
            report_path,
            lambda report: report.update({"run_id": "test-run-H0"}),
        )
        payload["metrics"]["postprocessor_report_artifact"] = replacement

    kwargs["adequacy_evidence_by_check"]["independent_reproduction"] = (
        _rewrite_self_hashed_json(path, reuse)
    )
    with pytest.raises(ValueError, match="reused the primary run_id"):
        build_research_alpha_manifest(**kwargs)


def test_claim_support_must_be_real_and_resolve_to_the_same_interval(tmp_path: Path) -> None:
    from app.services.research_alpha_manifest import build_research_alpha_manifest

    kwargs = make_manifest_kwargs(tmp_path)
    record = kwargs["claim_support_paths"][0]
    path = Path(record["path"])

    def alter(payload: dict) -> None:
        payload["numbers"]["H0"]["center"] = 999.0

    replacement = _rewrite_self_hashed_json(path, alter)
    kwargs["claim_support_paths"][0].update(replacement)
    with pytest.raises(ValueError, match="result is misaligned for H0"):
        build_research_alpha_manifest(**kwargs)


def test_final_fingerprint_changes_with_adequacy_or_support_artifacts(tmp_path: Path) -> None:
    from app.services.research_alpha_manifest import build_research_alpha_manifest

    kwargs = make_manifest_kwargs(tmp_path)
    first = build_research_alpha_manifest(**kwargs)
    adequacy_record = kwargs["adequacy_evidence_by_check"]["prior_sensitivity"]
    adequacy_path = Path(adequacy_record["path"])
    kwargs["adequacy_evidence_by_check"]["prior_sensitivity"] = (
        _rewrite_self_hashed_json(
            adequacy_path,
            lambda payload: payload["metrics"].update(
                {"max_standardized_shift": 0.11}
            ),
        )
    )
    second = build_research_alpha_manifest(**kwargs)

    assert first["run_identity"]["execution_fingerprint"] == second["run_identity"][
        "execution_fingerprint"
    ]
    assert first["run_identity"]["run_fingerprint"] != second["run_identity"][
        "run_fingerprint"
    ]


def test_evaluator_requires_platform_run_id_and_exact_non_negated_terms(
    tmp_path: Path,
) -> None:
    from app.services.research_alpha_evaluator import evaluate_alpha_class
    from app.services.research_alpha_manifest import build_research_alpha_manifest

    manifest = build_manifest(tmp_path)
    missing_run = evaluate_alpha_class(
        platform_record={"scientific_evidence_manifest": manifest},
        hidden_record=_hidden_record(),
    )
    assert missing_run["criteria"]["run_identity_match"] == "missing"
    assert missing_run["grade"] != "A_READY"

    kwargs = make_manifest_kwargs(tmp_path / "negated")
    kwargs["datasets"] = ["NOT DESI DR1 BAO", "Planck high-l likelihood"]
    kwargs["methods"] = ["not full likelihood"]
    kwargs["result_direction_terms"] = ["NOT H0"]
    negated = build_research_alpha_manifest(**kwargs)
    result = evaluate_alpha_class(
        platform_record={
            "run_id": "test-run-H0",
            "scientific_evidence_manifest": negated,
        },
        hidden_record=_hidden_record(),
    )
    assert result["criteria"]["data_match"] == "partial"
    assert result["criteria"]["method_match"] == "missing"
    assert result["criteria"]["direction_compatible"] == "missing"
    assert result["grade"] != "A_READY"


def test_asymmetric_center_tolerance_uses_shift_direction() -> None:
    from app.services.research_alpha_evaluator import _numeric_component_tolerance

    expected = {
        "center": -0.5,
        "uncertainty_minus": 0.2,
        "uncertainty_plus": 0.4,
    }
    negative = _numeric_component_tolerance(
        component="center",
        expected=expected,
        spec={},
        observed_value=-0.61,
    )
    positive = _numeric_component_tolerance(
        component="center",
        expected=expected,
        spec={},
        observed_value=-0.35,
    )
    assert negative == pytest.approx(0.06)
    assert positive == pytest.approx(0.12)


def test_internal_hmac_human_label_can_never_grant_strict_a(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.research_alpha_manifest import (
        EXTERNAL_REVIEW_AUTHORITY_ENV,
        EXTERNAL_REVIEW_PUBLIC_KEY_ENV,
        build_research_alpha_manifest,
    )
    from app.services.server_evidence import build_scientific_attestation

    kwargs = make_manifest_kwargs(tmp_path)
    pending = build_research_alpha_manifest(**kwargs)
    internal = build_scientific_attestation(
        attestation_type="research_alpha_external_review",
        payload={
            "source": "human_attested",
            "status": "approved",
            "run_id": pending["run_identity"]["run_id"],
            "run_fingerprint": pending["run_identity"]["run_fingerprint"],
        },
    )
    private_key = Ed25519PrivateKey.generate()
    public_path = tmp_path / "configured-external-authority.pem"
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv(EXTERNAL_REVIEW_PUBLIC_KEY_ENV, str(public_path))
    monkeypatch.setenv(EXTERNAL_REVIEW_AUTHORITY_ENV, "configured-board")
    with pytest.raises(ValueError, match="algorithm is not ed25519"):
        build_research_alpha_manifest(
            **kwargs,
            external_review_attestation=internal,
        )


def test_environment_selected_external_review_authority_needs_frozen_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import research_alpha_manifest as research

    private_key = Ed25519PrivateKey.generate()
    public_path = tmp_path / "environment-selected-review-key.pem"
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv(research.EXTERNAL_REVIEW_PUBLIC_KEY_ENV, str(public_path))
    monkeypatch.setenv(
        research.EXTERNAL_REVIEW_AUTHORITY_ENV, "environment-selected-board"
    )
    monkeypatch.setattr(
        research, "TRUSTED_EXTERNAL_REVIEW_AUTHORITY_REGISTRY", {}, raising=False
    )
    report_path = tmp_path / "review-report.txt"
    report_path.write_text("independent review\n", encoding="utf-8")
    attestation = {
        "schema_version": 1,
        "algorithm": "ed25519",
        "authority_id": "environment-selected-board",
        "authority_key_sha256": artifact(public_path)["sha256"],
        "status": "approved",
        "run_id": "exact-run",
        "run_fingerprint": sha("2"),
        "target_hash": sha("1"),
        "reviewer": "external-reviewer",
        "reviewed_at": (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat(),
        "report_artifact": artifact(report_path),
    }
    attestation["signature"] = base64.b64encode(
        private_key.sign(research.external_review_signing_bytes(attestation))
    ).decode("ascii")

    with pytest.raises(ValueError, match="registry|preregistered|trusted"):
        research._normalize_external_review(
            attestation,
            profile_id=research.EXACT_PROFILE_ID,
            run_id="exact-run",
            run_fingerprint=sha("2"),
            target_hash=sha("1"),
        )


def test_exact_research_alpha_requires_persistent_environment_signing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import research_alpha_manifest as research

    monkeypatch.delenv("EVIDENCE_SIGNING_KEY", raising=False)
    monkeypatch.delenv("EVIDENCE_SIGNING_KEY_ID", raising=False)
    with pytest.raises(ValueError, match="EVIDENCE_SIGNING_KEY|persistent|ephemeral"):
        research._build_research_alpha_scientific_attestation(
            attestation_type="research_alpha_test",
            payload={"profile_id": research.EXACT_PROFILE_ID},
        )

    operator_key = "persistent-exact-evidence-signing-key-2026"
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY", operator_key)
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY_ID", "dev-ephemeral")
    with pytest.raises(ValueError, match="key id|frozen contract"):
        research._require_exact_evidence_signing_key()

    monkeypatch.setenv("EVIDENCE_SIGNING_KEY_ID", "operator-selected-v1")
    with pytest.raises(ValueError, match="key id|frozen contract"):
        research._require_exact_evidence_signing_key()

    monkeypatch.setenv(
        "EVIDENCE_SIGNING_KEY_ID", research.EXACT_EVIDENCE_SIGNING_KEY_ID
    )
    with pytest.raises(ValueError, match="fingerprint|frozen contract"):
        research._require_exact_evidence_signing_key()

    with pytest.raises(ValueError, match="fingerprint|frozen contract"):
        research._build_research_alpha_scientific_attestation(
            attestation_type="research_alpha_test",
            payload={"profile_id": research.EXACT_PROFILE_ID},
        )


def test_exact_binding_rejects_valid_operator_selected_long_key_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import research_alpha_manifest as research
    from app.services.research_alpha_attestation import (
        build_scientific_attestation,
        signing_key_binding,
        verify_scientific_attestation,
    )

    operator_key = "operator-selected-key-material-that-is-long-enough"
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY", operator_key)
    monkeypatch.setenv("EVIDENCE_SIGNING_KEY_ID", "operator-selected-v1")
    binding = signing_key_binding(require_explicit=True)
    forged = build_scientific_attestation(
        attestation_type="research_alpha_manifest",
        payload={
            "profile_id": research.EXACT_PROFILE_ID,
            "evidence_signing_key_binding": binding,
        },
        require_explicit=True,
    )

    assert verify_scientific_attestation(
        forged,
        expected_type="research_alpha_manifest",
    )
    with pytest.raises(ValueError, match="key id|frozen contract"):
        research._validate_exact_evidence_signing_key_binding(forged)
    assert operator_key not in json.dumps(forged)


def test_valid_independent_ed25519_review_cannot_promote_ci_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.research_alpha_manifest import (
        EXTERNAL_REVIEW_AUTHORITY_ENV,
        EXTERNAL_REVIEW_PUBLIC_KEY_ENV,
        build_research_alpha_manifest,
        external_review_signing_bytes,
    )

    kwargs = make_manifest_kwargs(tmp_path)
    pending = build_research_alpha_manifest(**kwargs)
    private_key = Ed25519PrivateKey.generate()
    public_path = tmp_path / "external-review-authority.pem"
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv(EXTERNAL_REVIEW_PUBLIC_KEY_ENV, str(public_path))
    monkeypatch.setenv(EXTERNAL_REVIEW_AUTHORITY_ENV, "external-cosmology-board")
    report_path = tmp_path / "external-review-report.pdf"
    report_path.write_bytes(b"independent review report")
    key_record = artifact(public_path)
    attestation = {
        "schema_version": 1,
        "algorithm": "ed25519",
        "authority_id": "external-cosmology-board",
        "authority_key_sha256": key_record["sha256"],
        "status": "approved",
        "run_id": pending["run_identity"]["run_id"],
        "run_fingerprint": pending["run_identity"]["run_fingerprint"],
        "target_hash": pending["target"]["hash"],
        "reviewer": "external-reviewer-001",
        "reviewed_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "report_artifact": artifact(report_path),
    }
    attestation["signature"] = base64.b64encode(
        private_key.sign(external_review_signing_bytes(attestation))
    ).decode("ascii")
    with pytest.raises(
        ValueError, match="CI fixture manifests cannot receive external A review"
    ):
        build_research_alpha_manifest(
            **kwargs,
            external_review_attestation=attestation,
        )


def test_future_dated_external_review_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.research_alpha_manifest import (
        EXTERNAL_REVIEW_AUTHORITY_ENV,
        EXTERNAL_REVIEW_PUBLIC_KEY_ENV,
        build_research_alpha_manifest,
        external_review_signing_bytes,
    )

    kwargs = make_manifest_kwargs(tmp_path)
    pending = build_research_alpha_manifest(**kwargs)
    private_key = Ed25519PrivateKey.generate()
    public_path = tmp_path / "authority.pem"
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv(EXTERNAL_REVIEW_PUBLIC_KEY_ENV, str(public_path))
    monkeypatch.setenv(EXTERNAL_REVIEW_AUTHORITY_ENV, "board")
    report_path = tmp_path / "report.txt"
    report_path.write_text("review", encoding="utf-8")
    attestation = {
        "schema_version": 1,
        "algorithm": "ed25519",
        "authority_id": "board",
        "authority_key_sha256": artifact(public_path)["sha256"],
        "status": "approved",
        "run_id": "test-run-H0",
        "run_fingerprint": pending["run_identity"]["run_fingerprint"],
        "target_hash": sha("1"),
        "reviewer": "reviewer",
        "reviewed_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "report_artifact": artifact(report_path),
    }
    attestation["signature"] = base64.b64encode(
        private_key.sign(external_review_signing_bytes(attestation))
    ).decode("ascii")
    with pytest.raises(ValueError, match="reviewed_at is in the future"):
        build_research_alpha_manifest(
            **kwargs,
            external_review_attestation=attestation,
        )


def test_summary_keeps_pending_a_ready_separate_from_strict_a() -> None:
    from app.services.research_alpha_evaluator import summarize_alpha_evaluations

    summary = summarize_alpha_evaluations(
        [
            {"grade": "A_READY", "why_not_A": ["external_review=pending"]},
            {"grade": "A", "externally_reviewed": False, "why_not_A": []},
            {"grade": "B", "why_not_A": ["execution_ready=False"]},
        ]
    )
    assert summary["strict_A_count"] == 0
    assert summary["A_ready_count"] == 1
    assert summary["A_ready_rate"] == pytest.approx(1 / 3)


def test_exact_adequacy_code_registry_is_fail_closed_until_implemented() -> None:
    from app.services.research_alpha_manifest import (
        _trusted_exact_adequacy_code,
    )
    from app.services.w0wa_exact_contract import (
        TRUSTED_ADEQUACY_ANALYZER_CODE_SHA256,
        TRUSTED_ADEQUACY_RUNNER_CODE_SHA256,
    )

    assert TRUSTED_ADEQUACY_RUNNER_CODE_SHA256 == {}
    assert TRUSTED_ADEQUACY_ANALYZER_CODE_SHA256 == {}
    with pytest.raises(ValueError, match="registry is empty.*WITHHELD"):
        _trusted_exact_adequacy_code(
            None,
            registry=TRUSTED_ADEQUACY_RUNNER_CODE_SHA256,
            field="exact simulator",
        )
