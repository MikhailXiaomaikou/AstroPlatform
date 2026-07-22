from __future__ import annotations

import copy
import hashlib
import json
import os
import runpy
import stat
import sys
import time
from pathlib import Path

import pytest

from app.services.foundry_demo_runner import (
    FoundryDemoContractError,
    load_candidate_bundle,
    run_candidate_demo,
    validate_candidate_bundle,
)


_CANDIDATE = (
    Path(__file__).resolve().parents[1]
    / "foundry_candidates"
    / "desi_dr2_official_chain_summary_v1.json"
)
_REPORT_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "foundry_candidates"
    / "demo-report-schema-v1.json"
)


def _use_child_scenario(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    from app.services import foundry_demo_runner

    monkeypatch.setattr(
        foundry_demo_runner,
        "_CANDIDATE_CHILD_MODULE",
        "tests.foundry_demo_child_fixture",
    )
    monkeypatch.setenv("FOUNDRY_TEST_SCENARIO", scenario)


def _rehash_demo_report(report: dict[str, object]) -> None:
    unsigned = dict(report)
    unsigned.pop("demo_report_sha256", None)
    report["demo_report_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def test_demo_artifacts_are_host_readable_and_never_overwritten(
    tmp_path: Path,
) -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_foundry_candidate_demo.py"
    )
    write_exclusive = runpy.run_path(str(script))["_write_exclusive"]
    output = tmp_path / "demo-report.json"

    write_exclusive(output, b"{}\n")

    assert output.read_bytes() == b"{}\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o644
    with pytest.raises(FileExistsError):
        write_exclusive(output, b"replacement")


def test_legacy_cli_publishes_failed_report_and_captured_streams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.services import foundry_demo_runner

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_foundry_candidate_demo.py"
    )
    failed_report = {
        "candidate_id": "failed_candidate",
        "demo_run_id": "00000000-0000-0000-0000-000000000001",
        "status": "FAILED",
        "demo_report_sha256": "a" * 64,
        "artifact_manifest": [],
    }
    monkeypatch.setattr(foundry_demo_runner, "load_candidate_bundle", lambda _path: {})
    def fake_run_candidate_demo(
        *_args: object,
        captured_streams: dict[str, bytes],
        **_kwargs: object,
    ) -> dict[str, object]:
        captured_streams.update({"stdout.log": b"", "stderr.log": b""})
        return failed_report

    monkeypatch.setattr(
        foundry_demo_runner,
        "run_candidate_demo",
        fake_run_candidate_demo,
    )
    output = tmp_path / "demo-report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--candidate",
            "failed_candidate",
            "--output",
            str(output),
        ],
    )

    exit_code = runpy.run_path(str(script))["main"]()

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "FAILED"
    assert (tmp_path / "stdout.log").read_bytes() == b""
    assert (tmp_path / "stderr.log").read_bytes() == b""


def test_checked_in_candidate_binds_current_demo_runner_definition() -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile.foundry-demo"

    assert bundle["runner_definition_sha256"] == hashlib.sha256(
        dockerfile.read_bytes()
    ).hexdigest()


def test_checked_in_candidate_is_non_formal_and_records_partial_without_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DESI_DR2_OFFICIAL_CHAIN_ROOT", raising=False)
    bundle = load_candidate_bundle(_CANDIDATE)
    report = run_candidate_demo(bundle)

    assert report["candidate_id"] == bundle["candidate_id"]
    assert report["candidate_version"] == 1
    assert report["status"] == "PARTIAL"
    assert report["evidence_class"] == "NON_FORMAL_DEMO"
    assert report["publication_ready"] is False
    assert report["claim_eligible"] is False
    assert report["evidence_pack_allowed"] is False
    assert report["failure_class"] == "official_chain_mirror_unavailable"
    assert report["validation_summary"]["registry_integrity"] is True
    assert report["validation_summary"]["official_mirror_configured"] is False
    assert report["validation_summary"]["official_mirror_verified"] is False
    assert report["validation_summary"]["withheld_reasons"]
    assert len(report["candidate_bundle_sha256"]) == 64
    assert len(report["workflow_spec_sha256"]) == 64
    assert len(report["demo_report_sha256"]) == 64
    assert len(report["stdout_sha256"]) == 64
    assert len(report["stderr_sha256"]) == 64
    assert report["artifact_manifest"] == [
        {
            "path": "stdout.log",
            "kind": "STDOUT",
            "sha256": report["stdout_sha256"],
            "bytes": report["stdout_bytes"],
        },
        {
            "path": "stderr.log",
            "kind": "STDERR",
            "sha256": report["stderr_sha256"],
            "bytes": report["stderr_bytes"],
        },
    ]
    assert len(report["environment_sha256"]) == 64
    assert report["environment"]["entrypoint_id"] == bundle["entrypoint_id"]
    user_cpu_seconds = report["resource_usage"]["user_cpu_seconds"]
    assert user_cpu_seconds is None or user_cpu_seconds >= 0
    assert report["result"]["official_ready_cells"] == 0
    assert all(not cell.get("parameter_intervals") for cell in report["result"]["matrix"])

    schema = json.loads(_REPORT_SCHEMA.read_text(encoding="utf-8"))
    assert set(report) == set(schema["required"])
    assert schema["properties"]["evidence_class"]["const"] == "NON_FORMAL_DEMO"
    assert schema["properties"]["publication_ready"]["const"] is False


def test_configured_but_invalid_mirror_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DESI_DR2_OFFICIAL_CHAIN_ROOT", raising=False)
    bundle = load_candidate_bundle(_CANDIDATE)

    report = run_candidate_demo(bundle, cache_root=tmp_path)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == "official_chain_mirror_integrity_failed"
    assert report["evidence_class"] == "NON_FORMAL_DEMO"
    assert report["publication_ready"] is False
    assert report["claim_eligible"] is False
    assert report["evidence_pack_allowed"] is False
    assert report["validation_summary"]["official_mirror_configured"] is True
    assert report["validation_summary"]["official_mirror_verified"] is False
    assert report["validation_summary"]["ready_cells"] == 0
    assert report["validation_summary"]["withheld_cells"] > 0
    assert report["validation_summary"]["withheld_reasons"]


def test_mixed_mirror_coverage_preserves_a_failed_public_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.services import foundry_demo_runner
    from app.services.cosmology_likelihoods import (
        analysis_registry,
        dark_energy_matrix,
    )

    monkeypatch.setattr(analysis_registry, "audit_cosmology_analysis_registry", lambda: [])
    monkeypatch.setattr(
        dark_energy_matrix,
        "run_dark_energy_evidence_matrix",
        lambda **_kwargs: {
            "analysis_status": "WITHHELD",
            "official_ready_cells": 1,
            "official_withheld_cells": 1,
            "matrix": [
                {
                    "dataset": "union3",
                    "status": "COMPLETED",
                    "withheld_reasons": [],
                },
                {
                    "dataset": "pantheon_plus",
                    "status": "WITHHELD",
                    "withheld_reasons": ["official_chain_checksum_mismatch"],
                },
            ],
            "provenance": {"source": "mixed-mirror-test"},
        },
    )
    bundle = copy.deepcopy(load_candidate_bundle(_CANDIDATE))
    bundle["workflow_spec"]["demo_inputs"]["supernova_sets"] = [
        "union3",
        "pantheon_plus",
    ]

    direct_outcome = foundry_demo_runner._run_desi_dr2_official_chain_summary(  # noqa: SLF001
        bundle,
        cache_root=tmp_path,
    )
    assert direct_outcome["status"] == "FAILED"
    assert direct_outcome["validation_summary"]["official_mirror_verified"] is True

    _use_child_scenario(monkeypatch, "mixed_mirror")
    report = run_candidate_demo(bundle, cache_root=tmp_path)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == "official_chain_mirror_integrity_failed"
    assert report["validation_summary"]["official_mirror_verified"] is True
    assert report["validation_summary"]["ready_cells"] == 1
    assert report["validation_summary"]["withheld_cells"] == 1

    replay_script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_public_foundry_candidate_replay.py"
    )
    validate_report = runpy.run_path(str(replay_script))["_validate_report"]
    identity = {
        "candidate_version_envelope": {
            "candidate_bundle_sha256": report["candidate_bundle_sha256"],
            "workflow_spec_sha256": report["workflow_spec_sha256"],
            "dependency_lock_sha256": report["dependency_lock_sha256"],
        },
        "candidate_version_sha256": report["candidate_version_sha256"],
        "runner_image_digest": report["runner_image_digest"],
        "runner_descriptor": {
            "git_commit": report["environment"]["tool_version"],
        },
    }

    validate_report(report, bundle=bundle, identity=identity)

    forged_extra = copy.deepcopy(report)
    forged_extra["extraReceiptData"] = {"publicationReady": True}
    _rehash_demo_report(forged_extra)
    with pytest.raises(ValueError, match="report_contract"):
        validate_report(forged_extra, bundle=bundle, identity=identity)

    forged_limitations = copy.deepcopy(report)
    forged_limitations["limitations"] = ["Original warnings removed."]
    _rehash_demo_report(forged_limitations)
    with pytest.raises(ValueError, match="limitations"):
        validate_report(forged_limitations, bundle=bundle, identity=identity)

    for field, value in (
        ("status", "passed"),
        ("result", [["publication_ready", True]]),
        ("validation_summary", [["numeric_claim_gate", "NON_FORMAL_DEMO"]]),
        ("duration_ms", True),
    ):
        forged_type = copy.deepcopy(report)
        forged_type[field] = value
        _rehash_demo_report(forged_type)
        with pytest.raises(ValueError, match="report_contract"):
            validate_report(forged_type, bundle=bundle, identity=identity)

    forged_status = copy.deepcopy(report)
    forged_status["result"]["matrix"][1]["status"] = "SUPPORTED"
    _rehash_demo_report(forged_status)
    with pytest.raises(ValueError, match="formal_claim_escape"):
        validate_report(forged_status, bundle=bundle, identity=identity)

    forged_reasons = copy.deepcopy(report)
    forged_reasons["result"]["matrix"][1]["withheld_reasons"] = (
        "official_chain_checksum_mismatch"
    )
    _rehash_demo_report(forged_reasons)
    with pytest.raises(ValueError, match="matrix_cell_withheld_reasons"):
        validate_report(forged_reasons, bundle=bundle, identity=identity)


def test_candidate_cannot_upgrade_its_output_policy() -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    forged = copy.deepcopy(bundle)
    forged["output_policy"]["publication_ready"] = True

    with pytest.raises(
        FoundryDemoContractError,
        match="candidate_output_policy_not_non_formal",
    ):
        validate_candidate_bundle(forged)


def test_candidate_cannot_supply_arbitrary_entrypoint() -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    forged = copy.deepcopy(bundle)
    forged["entrypoint_id"] = "os_system"

    with pytest.raises(
        FoundryDemoContractError,
        match="candidate_entrypoint_not_allowlisted",
    ):
        validate_candidate_bundle(forged)


def test_candidate_bundle_cannot_embed_formal_claim_signal() -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    forged = copy.deepcopy(bundle)
    forged["workflow_spec"]["forged_verdict"] = {
        "scientific_verdict": "SUPPORTED"
    }

    with pytest.raises(
        FoundryDemoContractError,
        match="candidate_bundle_formal_claim_escape",
    ):
        validate_candidate_bundle(forged)


def test_formal_claim_escape_is_erased(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, "formal_policy")
    report = run_candidate_demo(bundle)
    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_formal_claim_escape_blocked"
    assert report["validation_summary"] == {"formal_claim_escape_blocked": True}
    assert report["result"] == {}
    assert report["publication_ready"] is False
    assert report["claim_eligible"] is False


def test_nested_formal_claim_escape_is_erased(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, "formal_nested")
    report = run_candidate_demo(bundle)
    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_formal_claim_escape_blocked"
    assert report["result"] == {}


@pytest.mark.parametrize("escape_kind", ["value", "key", "failure_class"])
def test_formal_claim_hidden_in_candidate_output_text_is_erased(
    monkeypatch: pytest.MonkeyPatch,
    escape_kind: str,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(
        monkeypatch,
        {
            "value": "formal_hidden_value",
            "key": "formal_hidden_key",
            "failure_class": "formal_failure_class",
        }[escape_kind],
    )
    report = run_candidate_demo(bundle)
    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_formal_claim_escape_blocked"
    assert report["result"] == {}


def test_supported_matrix_status_is_erased(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, "supported_matrix")
    report = run_candidate_demo(bundle)
    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_formal_claim_escape_blocked"
    assert report["result"] == {}


def test_formal_claim_in_stream_is_quarantined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, "formal_print")
    streams: dict[str, bytes] = {}
    report = run_candidate_demo(bundle, captured_streams=streams)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_formal_claim_escape_blocked"
    assert report["result"] == {}
    assert streams["stdout.log"] == b""
    assert b"SUPPORTED" not in streams["stderr.log"]


@pytest.mark.parametrize(
    "scenario",
    [
        "evidence_pack_value",
        "evidence_pack_key",
        "evidence_pack_stdout",
        "evidence_pack_stderr",
    ],
)
def test_evidence_pack_identifier_is_erased_from_candidate_output(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, scenario)
    streams: dict[str, bytes] = {}
    report = run_candidate_demo(bundle, captured_streams=streams)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_formal_claim_escape_blocked"
    assert report["result"] == {}
    assert b"pack-123" not in streams["stdout.log"]
    assert b"pack-123" not in streams["stderr.log"]


@pytest.mark.parametrize("writer", ["os_write", "subprocess"])
def test_formal_claim_cannot_bypass_capture_through_native_fds(
    monkeypatch: pytest.MonkeyPatch,
    writer: str,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(
        monkeypatch,
        "native_os_write" if writer == "os_write" else "native_subprocess",
    )
    streams: dict[str, bytes] = {}
    report = run_candidate_demo(bundle, captured_streams=streams)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_formal_claim_escape_blocked"
    assert report["result"] == {}
    assert b"SUPPORTED" not in streams["stdout.log"]
    assert b"SUPPORTED" not in streams["stderr.log"]


def test_background_child_is_killed_without_blocking_or_losing_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, "background_child")
    started = time.monotonic()
    report = run_candidate_demo(bundle)

    assert time.monotonic() - started < 2
    assert report["status"] == "PARTIAL"
    assert report["failure_class"] == "fixture_only"


def test_delayed_background_thread_cannot_write_after_demo_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, "delayed_thread")
    streams: dict[str, bytes] = {}
    report = run_candidate_demo(bundle, captured_streams=streams)
    time.sleep(0.35)

    assert report["status"] == "PARTIAL"
    assert report["failure_class"] == "fixture_only"
    assert b"SUPPORTED" not in streams["stdout.log"]
    assert b"SUPPORTED" not in streams["stderr.log"]


def test_nested_demo_execution_is_rejected_before_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import foundry_demo_runner

    bundle = load_candidate_bundle(_CANDIDATE)
    monkeypatch.setattr(foundry_demo_runner, "_CANDIDATE_CHILD_ACTIVE", True)

    with pytest.raises(
        FoundryDemoContractError,
        match="candidate_nested_demo_execution_forbidden",
    ):
        run_candidate_demo(bundle)


def test_process_streams_are_restored_after_entrypoint_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    prior_stdout = sys.stdout
    prior_stderr = sys.stderr
    prior_stdout_stat = os.fstat(1)
    prior_stderr_stat = os.fstat(2)

    _use_child_scenario(monkeypatch, "exception")
    report = run_candidate_demo(bundle)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == "RuntimeError"
    assert sys.stdout is prior_stdout
    assert sys.stderr is prior_stderr
    assert os.path.samestat(prior_stdout_stat, os.fstat(1))
    assert os.path.samestat(prior_stderr_stat, os.fstat(2))


def test_candidate_stream_capture_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import foundry_demo_runner

    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, "bounded")
    streams: dict[str, bytes] = {}
    report = run_candidate_demo(bundle, captured_streams=streams)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_stream_limit_exceeded"
    assert report["stdout_bytes"] == 0
    assert streams["stdout.log"] == b""
    assert b"SUPPORTED" not in streams["stderr.log"]
    assert report["resource_usage"]["stdout_truncated"] is True
    assert (
        report["resource_usage"]["stdout_observed_bytes"]
        > foundry_demo_runner._STREAM_CAPTURE_LIMIT_BYTES
    )


@pytest.mark.parametrize(
    ("scenario", "failure_class"),
    [
        ("nonzero_exit", "candidate_child_exit_failed"),
        ("control_trailing_data", "candidate_control_result_trailing_data"),
        ("huge_integer_control", "candidate_control_result_invalid"),
    ],
)
def test_control_frame_and_child_exit_must_both_be_exact(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    failure_class: str,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, scenario)

    report = run_candidate_demo(bundle)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == failure_class
    assert report["result"] == {}


def test_control_decoder_maps_recursion_error_to_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import foundry_demo_runner

    encoded = b"{}"
    capture = foundry_demo_runner._BoundedFdCapture()  # noqa: SLF001
    capture._append(len(encoded).to_bytes(8, "big") + encoded)  # noqa: SLF001
    capture.eof_seen = True

    def recursion_error(*_args: object, **_kwargs: object) -> None:
        raise RecursionError("simulated adversarial JSON nesting")

    monkeypatch.setattr(foundry_demo_runner.json, "loads", recursion_error)

    outcome, failure = foundry_demo_runner._decode_candidate_control(  # noqa: SLF001
        capture
    )

    assert outcome is None
    assert failure == "candidate_control_result_invalid"


@pytest.mark.parametrize(
    ("scenario", "failure_class"),
    [
        ("invalid_status", "candidate_demo_status_invalid"),
        ("invalid_failure_class", "candidate_demo_failure_class_invalid"),
        (
            "invalid_validation_summary",
            "candidate_demo_validation_summary_invalid",
        ),
    ],
)
def test_malformed_candidate_outcome_is_recorded_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    failure_class: str,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, scenario)

    report = run_candidate_demo(bundle)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == failure_class
    assert report["result"] == {}


@pytest.mark.parametrize(
    ("scenario", "failure_class"),
    [
        ("control_early_eof", "candidate_control_result_incomplete"),
        ("control_oversized_header", "candidate_control_result_too_large"),
    ],
)
def test_incomplete_or_oversized_control_frame_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    failure_class: str,
) -> None:
    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, scenario)

    report = run_candidate_demo(bundle)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == failure_class


def test_hung_candidate_is_killed_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import foundry_demo_runner

    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, "hang")
    monkeypatch.setattr(
        foundry_demo_runner,
        "_CANDIDATE_EXECUTION_TIMEOUT_SECONDS",
        0.1,
    )
    started = time.monotonic()

    report = run_candidate_demo(bundle)

    assert time.monotonic() - started < 2
    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_execution_timeout"


def test_group_cleanup_permission_error_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import foundry_demo_runner

    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, "valid_partial")

    monkeypatch.setattr(
        foundry_demo_runner,
        "_terminate_candidate_group",
        lambda *_args, **_kwargs: False,
    )

    report = run_candidate_demo(bundle)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_process_group_cleanup_failed"


def test_subprocess_start_failure_returns_a_fail_closed_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import foundry_demo_runner

    bundle = load_candidate_bundle(_CANDIDATE)

    def fail_to_start(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated Popen failure")

    monkeypatch.setattr(foundry_demo_runner.subprocess, "Popen", fail_to_start)

    report = run_candidate_demo(bundle)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_runner_OSError"
    assert report["validation_summary"]["stream_capture_complete"] is False
    assert report["publication_ready"] is False


def test_second_stream_dup_failure_closes_the_first_descriptor_and_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import foundry_demo_runner

    bundle = load_candidate_bundle(_CANDIDATE)
    _use_child_scenario(monkeypatch, "valid_partial")
    before = len(os.listdir("/dev/fd"))
    real_dup = foundry_demo_runner.os.dup
    calls = 0

    def fail_second_dup(descriptor: int) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second dup failure")
        return real_dup(descriptor)

    monkeypatch.setattr(foundry_demo_runner.os, "dup", fail_second_dup)

    report = run_candidate_demo(bundle)

    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_runner_OSError"
    assert len(os.listdir("/dev/fd")) == before
