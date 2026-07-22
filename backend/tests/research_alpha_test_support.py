from __future__ import annotations

import hashlib
import json
import os
import base64
from pathlib import Path
from typing import Any, Mapping


PROFILE_ID = "research_alpha_ci_fixture_v1"
CHAIN_COLUMNS = ["weight", "minuslogpost", "H0", "calibration"]


def sha(character: str) -> str:
    return "sha256:" + character * 64


def artifact(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "sha256": "sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "size_bytes": resolved.stat().st_size,
    }


def write_json_artifact(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.server_evidence import scientific_content_hash

    body = dict(payload)
    body["self_hash"] = scientific_content_hash(body)
    path.write_text(
        json.dumps(body, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return artifact(path)


def write_named_hash_artifact(
    path: Path, payload: dict[str, Any], *, hash_field: str
) -> dict[str, Any]:
    from app.services.server_evidence import scientific_content_hash

    body = dict(payload)
    body[hash_field] = scientific_content_hash(body)
    path.write_text(
        json.dumps(body, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return artifact(path)


def write_attestation(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    path.write_text(
        json.dumps(dict(payload), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return artifact(path)


def write_plain(path: Path, content: str) -> dict[str, Any]:
    path.write_text(content, encoding="utf-8")
    return artifact(path)


def chain_artifact(
    path: Path,
    *,
    run_id: str,
    chain_id: str,
    seed: int,
    offset: float,
) -> dict[str, Any]:
    rows = [
        (
            f"1 {10.0 + offset + index / 100000:.6f} "
            f"{67.25 + offset + (index % 17) / 10000:.6f} "
            f"{1.00 + offset + (index % 11) / 10000:.6f}"
        )
        for index in range(1000)
    ]
    path.write_text("# " + " ".join(CHAIN_COLUMNS) + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    chain = artifact(path)
    attestation = write_json_artifact(
        path.with_suffix(path.suffix + ".attestation.json"),
        {
            "schema_version": 1,
            "kind": "research_alpha_chain",
            "run_id": run_id,
            "chain_id": chain_id,
            "seed": seed,
            "chain_sha256": chain["sha256"],
            "columns": CHAIN_COLUMNS,
            "n_draws": 1000,
        },
    )
    return {
        "chain_id": chain_id,
        "seed": seed,
        **chain,
        "attestation": attestation,
        "columns": CHAIN_COLUMNS,
        "n_draws": 1000,
    }


def _auxiliary_artifacts(tmp_path: Path, prefix: str) -> dict[str, Any]:
    return {
        "preflight": write_named_hash_artifact(
            tmp_path / f"{prefix}-preflight.json",
            {"schema_version": 2, "artifact_type": "w0wa_exact_preflight", "profile_id": PROFILE_ID, "status": "PASS"},
            hash_field="preflight_sha256",
        ),
        "generation": write_named_hash_artifact(
            tmp_path / f"{prefix}-generation.json",
            {"schema_version": 2, "artifact_type": "w0wa_exact_generation", "profile_id": PROFILE_ID, "status": "PASS"},
            hash_field="generation_sha256",
        ),
        "code": write_plain(tmp_path / f"{prefix}-runner.py", f"# {prefix} canonical runner\n"),
        "workflow": [
            write_plain(tmp_path / f"{prefix}-workflow-preflight.json", f"{prefix} workflow preflight\n"),
            write_plain(tmp_path / f"{prefix}-workflow-generation.json", f"{prefix} workflow generation\n"),
        ],
        "checkpoint": write_plain(tmp_path / f"{prefix}.checkpoint", f"{prefix} converged checkpoint\n"),
        "analysis_code": write_plain(tmp_path / f"{prefix}-analysis.py", f"# {prefix} independent analyzer\n"),
    }


def _run_authority_artifact(
    tmp_path: Path,
    *,
    prefix: str,
    run_id: str,
    chains: list[dict[str, Any]],
    config: dict[str, Any],
    data: dict[str, Any],
    likelihoods: dict[str, Any],
    sampled: dict[str, Any],
    environment_fingerprint: str,
    auxiliaries: dict[str, Any],
) -> dict[str, Any]:
    from app.services.research_alpha_manifest import (
        build_research_alpha_run_authority_attestation,
    )

    seeds = [int(item["seed"]) for item in chains]
    checkpoint = auxiliaries["checkpoint"]
    amendment = artifact(
        Path(__file__).resolve().parents[2]
        / "docs"
        / "DESI_W0WA_A_READINESS_AMENDMENT_003.md"
    )
    preflight_payload = json.loads(Path(auxiliaries["preflight"]["path"]).read_text(encoding="utf-8"))
    generation_payload = json.loads(Path(auxiliaries["generation"]["path"]).read_text(encoding="utf-8"))
    protocol_status = _protocol_status()
    canonical_receipt = write_named_hash_artifact(
        tmp_path / f"{prefix}.run.json",
        {
            "schema_version": 2,
            "kind": "chain",
            "run_id": run_id,
            "profile_id": PROFILE_ID,
            "evidence_class": "ci_fixture",
            "status": "completed",
            "success": True,
            "returncode": 0,
            "config_path": config["path"],
            "config_sha256": config["sha256"],
            "environment_fingerprint": environment_fingerprint,
            "resource_binding": {"mpi_processes": 4, "threads_per_process": 3},
            "protocol_status": protocol_status,
            "protocol_amendment_sha256": amendment["sha256"],
            "workflow_receipts": {
                "preflight_sha256": preflight_payload["preflight_sha256"],
                "generation_sha256": generation_payload["generation_sha256"],
            },
            "seed_binding": {
                "algorithm": "numpy.SeedSequence(entropy).spawn(mpi_size)",
                "identities": [
                    {"rank": rank, "spawn_key": [rank], "derived_seed": seed}
                    for rank, seed in enumerate(seeds)
                ],
            },
            "termination": {
                "passed": True,
                "status": "converged",
                "max_samples_reached": False,
                "early_stop": False,
                "checkpoint": {**checkpoint, "mpi_size": 4},
            },
            "artifacts": [
                {key: item[key] for key in ("path", "sha256", "size_bytes")}
                for item in chains
            ],
        },
        hash_field="attestation_sha256",
    )
    signed = build_research_alpha_run_authority_attestation(
        run_id=run_id,
        chain_artifacts=chains,
        config_artifact=config,
        data_artifacts=data,
        likelihood_artifacts=likelihoods,
        sampled_parameters_artifact=sampled,
        canonical_run_receipt_artifact=canonical_receipt,
        preflight_artifact=auxiliaries["preflight"],
        generation_artifact=auxiliaries["generation"],
        code_artifacts=[auxiliaries["code"]],
        protocol_amendment_artifact=amendment,
    )
    return write_attestation(tmp_path / f"{prefix}.research-alpha-run.json", signed)


def _analysis_receipt(
    tmp_path: Path,
    *,
    prefix: str,
    run_id: str,
    chains: list[dict[str, Any]],
    results: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    return write_named_hash_artifact(
        tmp_path / f"{prefix}.analysis.json",
        {
            "schema_version": 2,
            "artifact_type": "w0wa_exact_analysis",
            "profile_id": PROFILE_ID,
            "status": "ANALYZED",
            "run_identity": {"run_id": run_id},
            "research_alpha_binding": {
                "chain_sha256": [item["sha256"] for item in chains],
                "sampled_parameters": ["H0", "calibration"],
            },
            "posterior": {
                "diagnostics": {
                    "chain_files": [
                        {"path": item["path"], "sha256": item["sha256"]}
                        for item in chains
                    ]
                }
            },
        },
        hash_field="manifest_sha256",
    )


def _analysis_authority_artifact(
    tmp_path: Path,
    *,
    prefix: str,
    run_id: str,
    run_attestation: dict[str, Any],
    receipt: dict[str, Any],
    analysis_code: dict[str, Any],
    chains: list[dict[str, Any]],
    results: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    from app.services.research_alpha_manifest import (
        build_research_alpha_analysis_authority_attestation,
    )

    offline_grade = write_named_hash_artifact(
        tmp_path / f"{prefix}.offline-grade.json",
        {
            "schema_version": 1,
            "artifact_type": "research_alpha_primary_offline_grade",
            "profile_id": PROFILE_ID,
            "status": "passed",
            "run_id": run_id,
            "analysis_receipt_sha256": receipt["sha256"],
            "chain_sha256": [item["sha256"] for item in chains],
            "sampled_parameters": ["H0", "calibration"],
            "numbers": results,
            "diagnostics": diagnostics,
        },
        hash_field="receipt_sha256",
    )
    signed = build_research_alpha_analysis_authority_attestation(
        run_id=run_id,
        run_attestation_artifact=run_attestation,
        analysis_receipt_artifact=receipt,
        offline_grade_receipt_artifact=offline_grade,
        analysis_code_artifact=analysis_code,
        chain_artifacts=chains,
    )
    return write_attestation(tmp_path / f"{prefix}.research-alpha-analysis.json", signed)


def _protocol_adjudication_artifact(
    tmp_path: Path,
    *,
    run_id: str,
    target_hash: str,
) -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app.services.research_alpha_manifest import (
        PROTOCOL_AUTHORITY_ID_ENV,
        PROTOCOL_AUTHORITY_PUBLIC_KEY_ENV,
        external_review_signing_bytes,
    )

    private_key = Ed25519PrivateKey.from_private_bytes(b"\x17" * 32)
    public_path = tmp_path / "protocol-authority.pem"
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    os.environ[PROTOCOL_AUTHORITY_PUBLIC_KEY_ENV] = str(public_path)
    os.environ[PROTOCOL_AUTHORITY_ID_ENV] = "independent-protocol-board"
    rationale = write_plain(
        tmp_path / "protocol-adjudication-rationale.txt",
        "Known-target parameter interval reproduction only; no discovery claim.\n",
    )
    payload = {
        "schema_version": 1,
        "algorithm": "ed25519",
        "authority_id": "independent-protocol-board",
        "authority_key_sha256": artifact(public_path)["sha256"],
        "source": "independent_protocol_authority",
        "status": "authorized",
        "run_id": run_id,
        "target_hash": target_hash,
        "claim_scope": "parameter_interval_reproduction_only",
        "known_target_reproduction_authorized": True,
        "protocol_status": dict(_protocol_status()),
        "adjudicator": "independent-protocol-reviewer",
        "rationale_artifact": rationale,
        "prohibited_conclusions": [
            "LambdaCDM_rejected",
            "dynamic_dark_energy_discovered",
        ],
    }
    payload["signature"] = base64.b64encode(
        private_key.sign(external_review_signing_bytes(payload))
    ).decode("ascii")
    return write_attestation(tmp_path / "protocol-adjudication.json", payload)


def _protocol_status() -> dict[str, str]:
    return {
        "target_preregistration": "frozen",
        "computation_answer_key_separation": "enforced",
        "analyst_blinding": "achieved",
    }


def _adequacy_subartifact(
    tmp_path: Path,
    *,
    run_id: str,
    execution_fingerprint: str,
    check: str,
    name: str,
    metrics: dict[str, Any],
    config: dict[str, Any],
    analysis_code: dict[str, Any],
) -> dict[str, Any]:
    from app.services.research_alpha_manifest import (
        build_research_alpha_adequacy_analysis_authority_attestation,
        build_research_alpha_adequacy_run_authority_attestation,
    )

    stem = f"{check}-{name}"
    output = write_plain(tmp_path / f"{stem}.output", f"{stem} full-likelihood output\n")
    checkpoint = write_plain(
        tmp_path / f"{stem}.checkpoint", f"{stem} converged checkpoint\n"
    )
    run_receipt = write_named_hash_artifact(
        tmp_path / f"{stem}.run-receipt.json",
        {
            "schema_version": 1,
            "artifact_type": "research_alpha_adequacy_run_receipt",
            "profile_id": PROFILE_ID,
            "run_id": run_id,
            "execution_fingerprint": execution_fingerprint,
            "check": check,
            "name": name,
            "variant_run_id": f"{run_id}:{stem}",
            "status": "completed",
            "success": True,
            "returncode": 0,
            "runner": "cobaya",
            "config_artifact": config,
            "output_artifacts": [output],
            "termination": {
                "passed": True,
                "checkpoint_artifact": checkpoint,
            },
        },
        hash_field="receipt_sha256",
    )
    runner = build_research_alpha_adequacy_run_authority_attestation(
        canonical_run_receipt_artifact=run_receipt,
    )
    runner_artifact = write_attestation(tmp_path / f"{stem}.runner.json", runner)
    analysis_receipt = write_named_hash_artifact(
        tmp_path / f"{stem}.analysis-receipt.json",
        {
            "schema_version": 1,
            "artifact_type": "research_alpha_adequacy_analysis_receipt",
            "profile_id": PROFILE_ID,
            "run_id": run_id,
            "execution_fingerprint": execution_fingerprint,
            "check": check,
            "name": name,
            "status": "passed",
            "runner_attestation_sha256": runner_artifact["sha256"],
            "analysis_code_artifact": analysis_code,
            "metrics": metrics,
        },
        hash_field="report_sha256",
    )
    analysis = build_research_alpha_adequacy_analysis_authority_attestation(
        runner_attestation_artifact=runner_artifact,
        canonical_analysis_receipt_artifact=analysis_receipt,
    )
    return write_attestation(tmp_path / f"{stem}.analysis.json", analysis)


def make_manifest_kwargs(tmp_path: Path, *, h0: float = 67.36) -> dict[str, Any]:
    from app.services.cosmology_likelihoods.verification import (
        PUBLICATION_REQUIRED_ADEQUACY_CHECKS,
    )
    from app.services.research_alpha_manifest import (
        build_research_alpha_adequacy_authority_attestation,
        build_research_alpha_execution_binding,
        build_research_alpha_independent_analysis_authority_attestation,
    )

    tmp_path.mkdir(parents=True, exist_ok=True)
    run_id = "test-run-H0"
    chains = [
        chain_artifact(
            tmp_path / f"chain-{index}.txt",
            run_id=run_id,
            chain_id=f"chain-{index}",
            seed=seed,
            offset=index / 1000,
        )
        for index, seed in enumerate((11, 22, 33, 44), start=1)
    ]
    config = write_plain(tmp_path / "exact.yaml", "model: w0wa\n")
    data = {"desi": [write_plain(tmp_path / "desi.dat", "DESI exact data\n")]}
    likelihoods = {"planck": [write_plain(tmp_path / "plik.py", "# exact likelihood fixture\n")]}
    sampled = write_json_artifact(
        tmp_path / "sampled.json",
        {
            "schema_version": 1,
            "kind": "sampled_parameters",
            "run_id": run_id,
            "sampled_parameters": ["H0", "calibration"],
        },
    )
    results = [
        {
            "name": "H0",
            "center": h0,
            "lower_68": h0 - 0.1,
            "upper_68": h0 + 0.1,
            "uncertainty_minus": 0.1,
            "uncertainty_plus": 0.1,
        }
    ]
    per_parameter = {
        name: {
            "rhat": 1.001,
            "ess_bulk": 1200.0,
            "mcse_over_reference_sigma": 0.01,
            "mcse_mean": 0.001,
            "posterior_std": 0.1,
            "mcse_reference_kind": (
                "paper_sigma" if name == "H0" else "posterior_sd"
            ),
            "mcse_reference_value": 0.1,
        }
        for name in ("H0", "calibration")
    }
    diagnostics = {
        "status": "passed",
        "metrics": {
            "rhat_method": "rank_normalized",
            "ess_method": "bulk",
            "mcse_reference": "per_parameter",
            "environment_fingerprint": sha("d"),
            "n_independent_chains": 4,
            "critical_parameters": ["H0", "calibration"],
            "per_parameter": per_parameter,
        },
    }
    auxiliaries = _auxiliary_artifacts(tmp_path, "primary")
    run_attestation = _run_authority_artifact(
        tmp_path,
        prefix="primary",
        run_id=run_id,
        chains=chains,
        config=config,
        data=data,
        likelihoods=likelihoods,
        sampled=sampled,
        environment_fingerprint=sha("d"),
        auxiliaries=auxiliaries,
    )
    receipt = _analysis_receipt(
        tmp_path,
        prefix="primary",
        run_id=run_id,
        chains=chains,
        results=results,
        diagnostics=diagnostics,
    )
    analysis_attestation = _analysis_authority_artifact(
        tmp_path,
        prefix="primary",
        run_id=run_id,
        run_attestation=run_attestation,
        receipt=receipt,
        analysis_code=auxiliaries["analysis_code"],
        chains=chains,
        results=results,
        diagnostics=diagnostics,
    )
    protocol_adjudication = None
    base = {
        "run_id": run_id,
        "target_hash": sha("1"),
        "chain_artifacts": chains,
        "config_artifact": config,
        "data_artifacts": data,
        "likelihood_artifacts": likelihoods,
        "sampled_parameters_artifact": sampled,
        "run_attestation_artifact": run_attestation,
        "analysis_attestation_artifact": analysis_attestation,
        "protocol_adjudication_artifact": protocol_adjudication,
        "results": results,
        "diagnostics": diagnostics,
    }
    execution_fingerprint = build_research_alpha_execution_binding(**base)[
        "execution_fingerprint"
    ]

    independent_run_id = "independent-test-run-H0"
    independent_chains = [
        chain_artifact(
            tmp_path / f"independent-chain-{index}.txt",
            run_id=independent_run_id,
            chain_id=f"independent-{index}",
            seed=seed,
            offset=0.1 + index / 1000,
        )
        for index, seed in enumerate((55, 66, 77, 88), start=1)
    ]
    independent_config = write_plain(tmp_path / "independent-exact.yaml", "model: w0wa\nrun: independent\n")
    independent_data = {"desi": [write_plain(tmp_path / "independent-desi.dat", "independent DESI exact data\n")]}
    independent_likelihoods = {"planck": [write_plain(tmp_path / "independent-plik.py", "# independent exact likelihood\n")]}
    independent_sampled = write_json_artifact(
        tmp_path / "independent-sampled.json",
        {
            "schema_version": 1,
            "kind": "sampled_parameters",
            "run_id": independent_run_id,
            "sampled_parameters": ["H0", "calibration"],
        },
    )
    independent_aux = _auxiliary_artifacts(tmp_path, "independent")
    independent_run_attestation = _run_authority_artifact(
        tmp_path,
        prefix="independent",
        run_id=independent_run_id,
        chains=independent_chains,
        config=independent_config,
        data=independent_data,
        likelihoods=independent_likelihoods,
        sampled=independent_sampled,
        environment_fingerprint=sha("e"),
        auxiliaries=independent_aux,
    )
    independent_diagnostics = {
        "status": "passed",
        "metrics": {**diagnostics["metrics"], "environment_fingerprint": sha("e")},
    }
    raw_independent_diagnostics = {
        name: {
            "rank_normalized_rhat": record["rhat"],
            "bulk_ess": record["ess_bulk"],
            "mcse_mean": 0.001,
            "posterior_std": 0.1,
            "passed": True,
        }
        for name, record in independent_diagnostics["metrics"]["per_parameter"].items()
    }
    independent_report = write_named_hash_artifact(
        tmp_path / "independent-postprocessor-report.json",
        {
            "schema_version": 1,
            "artifact_type": "independent_w0wa_postprocess",
            "profile_id": PROFILE_ID,
            "run_id": independent_run_id,
            "status": "PASS",
            "failures": [],
            "updated_config": {"path": independent_config["path"], "sha256": independent_config["sha256"]},
            "burn_fraction": 0.30,
            "aligned_draws_per_chain": 1000,
            "chain_files": [
                {
                    "chain_id": item["chain_id"],
                    "path": item["path"],
                    "sha256": item["sha256"],
                    "raw_rows": item["n_draws"],
                    "post_burn_draws": 1000,
                }
                for item in independent_chains
            ],
            "sampled_parameters": ["H0", "calibration"],
            "diagnostics": raw_independent_diagnostics,
            "intervals_68": {"H0": {key: value for key, value in results[0].items() if key != "name"}},
            "research_alpha_binding": {
                "primary_execution_fingerprint": execution_fingerprint,
                "environment_fingerprint": sha("e"),
                "chain_sha256": [item["sha256"] for item in independent_chains],
                "sampled_parameters": ["H0", "calibration"],
            },
        },
        hash_field="report_sha256",
    )
    independent_offline_grade = write_named_hash_artifact(
        tmp_path / "independent-offline-grade.json",
        {
            "schema_version": 1,
            "artifact_type": "research_alpha_independent_offline_grade",
            "profile_id": PROFILE_ID,
            "status": "passed",
            "run_id": independent_run_id,
            "primary_execution_fingerprint": execution_fingerprint,
            "postprocessor_report_sha256": independent_report["sha256"],
            "chain_sha256": [item["sha256"] for item in independent_chains],
            "sampled_parameters": ["H0", "calibration"],
            "numbers": results,
            "diagnostics": independent_diagnostics,
        },
        hash_field="receipt_sha256",
    )
    independent_analysis = build_research_alpha_independent_analysis_authority_attestation(
        run_id=independent_run_id,
        primary_execution_fingerprint=execution_fingerprint,
        run_attestation_artifact=independent_run_attestation,
        postprocessor_report_artifact=independent_report,
        offline_grade_receipt_artifact=independent_offline_grade,
        analysis_code_artifact=independent_aux["analysis_code"],
        chain_artifacts=independent_chains,
    )
    independent_analysis_artifact = write_attestation(
        tmp_path / "independent.research-alpha-analysis.json", independent_analysis
    )

    analysis_code = auxiliaries["analysis_code"]
    prior_checks = [
        {
            "name": f"prior-check-{index}",
            "status": "passed",
            **_adequacy_subartifact(
                tmp_path,
                run_id=run_id,
                execution_fingerprint=execution_fingerprint,
                check="prior_predictive_check",
                name=f"prior-check-{index}",
                metrics={"passed": True},
                config=config,
                analysis_code=analysis_code,
            ),
        }
        for index in range(1, 3)
    ]
    posterior_checks = [
        {
            "name": f"posterior-check-{index}",
            "status": "passed",
            **_adequacy_subartifact(
                tmp_path,
                run_id=run_id,
                execution_fingerprint=execution_fingerprint,
                check="posterior_predictive_check",
                name=f"posterior-check-{index}",
                metrics={"passed": True},
                config=config,
                analysis_code=analysis_code,
            ),
        }
        for index in range(1, 3)
    ]
    baseline_prior = write_plain(
        tmp_path / "baseline-prior-config.yaml",
        "params:\n  w:\n    prior: {min: -2, max: 0}\n  wa:\n    prior: {min: -3, max: 1}\n",
    )
    widened_prior = write_plain(
        tmp_path / "widened-prior-config.yaml",
        "params:\n  w:\n    prior: {min: -3, max: 1}\n  wa:\n    prior: {min: -5, max: 3}\n",
    )
    prior_variant_runs = [
        {
            "name": name,
            "status": "passed",
            **_adequacy_subartifact(
                tmp_path,
                run_id=run_id,
                execution_fingerprint=execution_fingerprint,
                check="prior_sensitivity",
                name=name,
                metrics={"passed": True},
                config=variant_config,
                analysis_code=analysis_code,
            ),
        }
        for name, variant_config in (
            ("baseline_prior", baseline_prior),
            ("widened_prior", widened_prior),
        )
    ]
    variant_effects = {
        "planck_pr3_plik": (0.05, 0.02),
        "planck_pr4_camspec": (0.30, 0.08),
        "lensing_combination": (0.20, 0.10),
        "pantheonplus_covariance": (0.25, 0.06),
    }
    variants = [
        {
            "name": name,
            "status": "passed",
            "standardized_shift": shift,
            "interval_fractional_change": interval,
            **_adequacy_subartifact(
                tmp_path,
                run_id=run_id,
                execution_fingerprint=execution_fingerprint,
                check="systematics_robustness",
                name=name,
                metrics={"standardized_shift": shift, "interval_fractional_change": interval},
                config=config,
                analysis_code=analysis_code,
            ),
        }
        for name, (shift, interval) in variant_effects.items()
    ]
    fiducial_biases = (0.05, 0.10, 0.15)
    fiducials = [
        {
            "name": f"fiducial-{index}",
            "status": "passed",
            "inside_joint_95": True,
            "standardized_bias": bias,
            **_adequacy_subartifact(
                tmp_path,
                run_id=run_id,
                execution_fingerprint=execution_fingerprint,
                check="simulation_recovery",
                name=f"fiducial-{index}",
                metrics={"inside_joint_95": True, "standardized_bias": bias},
                config=config,
                analysis_code=analysis_code,
            ),
        }
        for index, bias in enumerate(fiducial_biases, start=1)
    ]
    metrics_by_check: dict[str, dict[str, Any]] = {
        "prior_predictive_check": {"passed": True, "all_passed": True, "failed_checks": 0, "n_checks": 2, "checks": prior_checks},
        "posterior_predictive_check": {"passed": True, "all_passed": True, "failed_checks": 0, "n_checks": 2, "checks": posterior_checks},
        "prior_sensitivity": {
            "passed": True,
            "failed_checks": 0,
            "max_standardized_shift": 0.10,
            "widened_parameters": ["w0", "wa"],
            "baseline_prior_artifact": baseline_prior,
            "widened_prior_artifact": widened_prior,
            "variant_runs": prior_variant_runs,
        },
        "systematics_robustness": {"passed": True, "failed_checks": 0, "max_standardized_shift": 0.30, "max_interval_fractional_change": 0.10, "variants": variants},
        "simulation_recovery": {"passed": True, "failed_checks": 0, "n_fiducials": 3, "all_inside_joint_95": True, "mean_standardized_bias": 0.10, "fiducials": fiducials},
        "independent_reproduction": {
            "passed": True,
            "failed_checks": 0,
            "isolated_environment": True,
            "independent_seeds": True,
            "reproduced": True,
            "postprocessor_report_artifact": independent_report,
            "independent_run_attestation_artifact": independent_run_attestation,
            "independent_analysis_attestation_artifact": independent_analysis_artifact,
        },
    }
    adequacy: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(PUBLICATION_REQUIRED_ADEQUACY_CHECKS, start=1):
        aggregate_receipt = write_named_hash_artifact(
            tmp_path / f"adequacy-{index}-{name}.aggregate-receipt.json",
            {
                "schema_version": 1,
                "artifact_type": "research_alpha_adequacy_aggregate_receipt",
                "profile_id": PROFILE_ID,
                "run_id": run_id,
                "execution_fingerprint": execution_fingerprint,
                "check": name,
                "status": "passed",
                "metrics": metrics_by_check[name],
            },
            hash_field="aggregate_sha256",
        )
        signed = build_research_alpha_adequacy_authority_attestation(
            canonical_aggregate_receipt_artifact=aggregate_receipt,
        )
        adequacy[name] = write_attestation(
            tmp_path / f"adequacy-{index}-{name}.json", signed
        )

    support = write_json_artifact(
        tmp_path / "claim-support.json",
        {
            "schema_version": 1,
            "kind": "research_alpha_claim_support",
            "run_id": run_id,
            "execution_fingerprint": execution_fingerprint,
            "numbers": {
                item["name"]: {key: item[key] for key in ("center", "lower_68", "upper_68", "uncertainty_minus", "uncertainty_plus")}
                for item in results
            },
        },
    )
    return {
        **base,
        "adequacy_evidence_by_check": adequacy,
        "claim_support_paths": [{"claim": "H0", "parameter": "H0", "result_path": "numbers.H0", **support}],
        "datasets": ["DESI DR1 BAO", "Planck high-l likelihood"],
        "methods": ["full likelihood"],
        "models": ["LCDM"],
        "result_direction_terms": ["H0"],
    }


def build_manifest(tmp_path: Path, *, h0: float = 67.36) -> dict[str, Any]:
    from app.services.research_alpha_manifest import build_research_alpha_manifest

    return build_research_alpha_manifest(**make_manifest_kwargs(tmp_path, h0=h0))
