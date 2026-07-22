from __future__ import annotations

import copy
import hashlib
import json
import runpy
import stat
import sys
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


def test_cli_records_contract_valid_failed_report_before_host_callback(
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
    monkeypatch.setattr(
        foundry_demo_runner,
        "run_candidate_demo",
        lambda *_args, **_kwargs: failed_report,
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


def test_formal_claim_escape_is_erased(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import foundry_demo_runner

    bundle = load_candidate_bundle(_CANDIDATE)

    def forged_entrypoint(*_args: object, **_kwargs: object) -> dict:
        return {
            "status": "PASSED",
            "result": {
                "publication_ready": True,
                "scientific_verdict": "SUPPORTED",
                "value": 123,
            },
            "validation_summary": {},
        }

    monkeypatch.setitem(
        foundry_demo_runner._ENTRYPOINTS,  # noqa: SLF001
        bundle["entrypoint_id"],
        forged_entrypoint,
    )
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
    from app.services import foundry_demo_runner

    bundle = load_candidate_bundle(_CANDIDATE)

    def forged_entrypoint(*_args: object, **_kwargs: object) -> dict:
        return {
            "status": "PASSED",
            "result": {"nested": [{"scientific_verdict": "SUPPORTED"}]},
            "validation_summary": {},
        }

    monkeypatch.setitem(
        foundry_demo_runner._ENTRYPOINTS,  # noqa: SLF001
        bundle["entrypoint_id"],
        forged_entrypoint,
    )
    report = run_candidate_demo(bundle)
    assert report["status"] == "FAILED"
    assert report["failure_class"] == "candidate_formal_claim_escape_blocked"
    assert report["result"] == {}


def test_candidate_stream_capture_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import foundry_demo_runner

    bundle = load_candidate_bundle(_CANDIDATE)

    def noisy_entrypoint(*_args: object, **_kwargs: object) -> dict:
        print("x" * (foundry_demo_runner._STREAM_CAPTURE_LIMIT_BYTES + 32))
        return {
            "status": "PARTIAL",
            "failure_class": "fixture_only",
            "result": {},
            "validation_summary": {"bounded": True},
        }

    monkeypatch.setitem(
        foundry_demo_runner._ENTRYPOINTS,  # noqa: SLF001
        bundle["entrypoint_id"],
        noisy_entrypoint,
    )
    streams: dict[str, bytes] = {}
    report = run_candidate_demo(bundle, captured_streams=streams)

    assert report["stdout_bytes"] == foundry_demo_runner._STREAM_CAPTURE_LIMIT_BYTES
    assert len(streams["stdout.log"]) == report["stdout_bytes"]
    assert streams["stderr.log"] == b""
    assert report["resource_usage"]["stdout_truncated"] is True
    assert (
        report["resource_usage"]["stdout_observed_bytes"]
        > report["stdout_bytes"]
    )
