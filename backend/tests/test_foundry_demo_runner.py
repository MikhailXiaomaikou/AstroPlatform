from __future__ import annotations

import copy
import json
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
    assert report["validation_summary"]["official_mirror_verified"] is False
    assert len(report["candidate_bundle_sha256"]) == 64
    assert len(report["workflow_spec_sha256"]) == 64
    assert len(report["demo_report_sha256"]) == 64
    assert len(report["stdout_sha256"]) == 64
    assert len(report["stderr_sha256"]) == 64
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
