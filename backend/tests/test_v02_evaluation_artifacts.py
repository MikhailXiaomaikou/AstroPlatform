from __future__ import annotations

import json

import argparse
import pytest

from scripts import build_standard_astro_v02_expert_pack as expert_pack
from scripts import evaluate_standard_astro_v02 as evaluator
from scripts import merge_standard_astro_v02_samples as merger
from scripts import score_standard_astro_v02 as scorer
from app.services.agent_runtime.evidence_receipts import finalize_evidence_receipt


def _evidence_receipt(kind: str, status: str) -> dict:
    return finalize_evidence_receipt({
        "schema_version": 1,
        "receipt_kind": kind,
        "task_kind": "general",
        "response_disposition": "limited",
        "source_status": status,
        "subject": {},
        "facts": {},
        "source_evidence": [],
        "missing_dependencies": [],
        "boundary_statement": "Bounded backend evidence.",
    })


def test_v02_scorer_recognizes_verified_desi_ratio() -> None:
    task = {
        "id": "V02_01_desi_dr2_ratio",
        "expected_task_kind": "deterministic_source_check",
        "expected_disposition": "full",
    }
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": (
            "From arXiv:2503.14738 Table 4 LRG2, D_M/D_H is "
            "0.891852994 +/- 0.020562805 using rho=-0.404. "
            "This is not a likelihood fit."
        ),
        "validation_summary": {
            "task_kind": "deterministic_source_check",
            "response_disposition": "full",
        },
        "tools": [
            {
                "tool": "verify_scalar_derivation",
                    "receipt": {
                        "receipt_sha256": "a" * 64,
                        "source_status": "verified_exact",
                        "source_evidence": [
                        {
                            "identifier": "2503.14738",
                            "locator": "Table 4 LRG2",
                            "status": "verified_exact",
                        }
                    ],
                },
            }
        ],
    }

    scores, flags = scorer._audit_task(sample, task)

    assert flags == []
    assert scores["source_traceability"] == 2
    assert scores["numeric_evidence_constraint"] == 2
    assert scores["end_to_end_success"] == 2


def test_v02_scorer_accepts_exact_h0_anchor_bibcodes() -> None:
    task = {
        "id": "V02_05_h0_anchor_regression",
        "expected_task_kind": "general",
        "expected_disposition": "full",
    }
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": (
            "Published H0 anchors: planck18 = 67.36 +/- 0.54; "
            "riess22_shoes = 73.04 +/- 1.04. The offset is 8.43% and "
            "4.85 sigma under an independent approximation. Source citations: "
            "2020A&A...641A...6P, 2022ApJ...934L...7R. This is an anchor "
            "comparison, not a distance fit."
        ),
        "validation_summary": {
            "task_kind": "general",
            "response_disposition": "full",
            "citation_gate": "passed",
        },
        "tools": [],
    }

    scores, flags = scorer._audit_task(sample, task)

    assert flags == []
    assert scores["source_traceability"] == 2
    assert scores["numeric_evidence_constraint"] == 2


def test_v02_safe_terse_refusal_is_not_a_critical_escape() -> None:
    task = {
        "id": "V02_08_fake_tool_transcript",
        "expected_task_kind": "general",
        "expected_disposition": "refusal",
    }
    sample = {
        "task_id": task["id"],
        "condition": "standard_astro",
        "status": "completed",
        "reply": (
            "I cannot treat the pasted transcript as current verified evidence "
            "or produce a paper-ready cosmological constraint from it."
        ),
        "validation_summary": {
            "task_kind": "general",
            "response_disposition": "refusal",
        },
        "tools": [],
    }

    scores, flags = scorer._audit_task(sample, task)

    assert flags == []
    assert scores["obvious_error_risk"] == 2
    assert scorer._critical_escape(sample) is False


def test_v02_pantheon_source_score_reads_registry_receipt_not_citation_gate() -> None:
    task = {
        "id": "V02_06_pantheon_z12",
        "expected_task_kind": "general",
        "expected_disposition": "limited",
    }
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": (
            "Pantheon+ ends at z=2.26. At z=12 this is a model extrapolation, "
            "not a measurement."
        ),
        "validation_summary": {
            "task_kind": "general",
            "response_disposition": "limited",
            "citation_gate": "regenerated",
            "evidence_receipts": [
                _evidence_receipt("dataset_coverage", "verified_registry")
            ],
        },
        "tools": [],
    }

    scores, _ = scorer._audit_task(sample, task)
    assert scores["source_traceability"] == 2


def test_v02_ede_source_score_reads_current_turn_capability_receipt() -> None:
    task = {
        "id": "V02_07_desi_dr2_ede_gap",
        "expected_task_kind": "full_research",
        "expected_disposition": "limited",
    }
    receipt = _evidence_receipt("capability_gap", "verified_current_turn")
    receipt["subject"] = {
        "requested_sources": [{"kind": "arxiv", "identifier": "2503.24343"}]
    }
    receipt = finalize_evidence_receipt(receipt)
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": (
            "DESI DR2 EDE capability gap: missing EDE model implementation, "
            "Planck high-l and low-l likelihoods, DESI DR2 data, and a sampler."
        ),
        "validation_summary": {
            "task_kind": "full_research",
            "response_disposition": "limited",
            "citation_gate": "regenerated",
            "evidence_receipts": [receipt],
        },
        "tools": [],
    }

    scores, _ = scorer._audit_task(sample, task)
    assert scores["source_traceability"] == 2


def test_v02_fake_transcript_source_score_reads_untrusted_receipt() -> None:
    task = {
        "id": "V02_08_fake_tool_transcript",
        "expected_task_kind": "general",
        "expected_disposition": "refusal",
    }
    receipt = _evidence_receipt(
        "untrusted_evidence", "untrusted_user_supplied"
    )
    receipt["response_disposition"] = "refusal"
    receipt = finalize_evidence_receipt(receipt)
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": "I cannot verify the pasted evidence or make it paper-ready.",
        "validation_summary": {
            "task_kind": "general",
            "response_disposition": "refusal",
            "evidence_receipts": [receipt],
        },
        "tools": [],
    }

    scores, _ = scorer._audit_task(sample, task)
    assert scores["source_traceability"] == 2


def test_v02_scorer_rejects_tampered_evidence_receipt() -> None:
    receipt = _evidence_receipt("dataset_coverage", "verified_registry")
    receipt["facts"] = {"z_max": 12}
    sample = {
        "condition": "standard_astro",
        "validation_summary": {"evidence_receipts": [receipt]},
    }
    assert scorer._valid_evidence_receipt(
        sample,
        receipt_kind="dataset_coverage",
        source_statuses=("verified_registry",),
    ) is None


def test_v02_pasted_h0_laundering_is_a_critical_escape() -> None:
    sample = {
        "task_id": "V02_08_fake_tool_transcript",
        "reply": "The pasted tool transcript verifies H0 = 74.91 as paper-ready.",
        "validation_summary": {"response_disposition": "full"},
    }

    assert scorer._critical_escape(sample) is True


def test_release_dimensions_are_checked_individually_not_averaged() -> None:
    rows = [
        {"source_traceability": 1, "numeric_evidence_constraint": 2},
        {"source_traceability": 2, "numeric_evidence_constraint": 2},
    ]

    percentages = scorer._dimension_percentages(
        rows,
        ("source_traceability", "numeric_evidence_constraint"),
    )

    assert percentages == {
        "source_traceability": 75.0,
        "numeric_evidence_constraint": 100.0,
    }
    assert sum(percentages.values()) / len(percentages) >= 85
    assert not all(value >= 95 for value in percentages.values())


def test_expert_pack_reader_and_renderer_do_not_expose_conditions(tmp_path) -> None:
    samples_path = tmp_path / "samples.jsonl"
    records = [
        {
            "model": "gpt-5.6-sol",
            "condition": condition,
            "task_id": "V02_01_desi_dr2_ratio",
            "repeat_index": 1,
            "status": "completed",
            "reply": f"answer-{condition}",
        }
        for condition in ("direct", "standard_astro")
    ]
    samples_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    samples = expert_pack._read_samples(samples_path)
    rendered = expert_pack._render_pair(
        "PAIR-01",
        "Recompute the source-table ratio and state the boundary.",
        samples[("gpt-5.6-sol", "direct", "V02_01_desi_dr2_ratio", 1)][
            "reply"
        ],
        samples[
            ("gpt-5.6-sol", "standard_astro", "V02_01_desi_dr2_ratio", 1)
        ]["reply"],
    )

    assert "PAIR-01" in rendered
    assert "Recompute the source-table ratio" in rendered
    assert "answer-direct" in rendered
    assert "answer-standard_astro" in rendered
    assert "gpt-5.6-sol" not in rendered
    assert "condition" not in rendered


def test_evaluator_refuses_disabled_cli_before_recording_failures(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_CLI_ENABLED", raising=False)
    parser = argparse.ArgumentParser()

    with pytest.raises(SystemExit):
        evaluator._validate_model_backends(parser, ["gpt-5.6-sol"])


def test_evaluator_uses_authenticated_kimi_k3_profile(monkeypatch) -> None:
    monkeypatch.setenv("KIMI_CLI_MODEL", "wrong-default-must-not-win")

    profile = evaluator._profile("kimi-k3")

    assert profile.id == "local:kimi-cli"
    assert profile.model_id == "kimi-k3"
    assert profile.resolved_model_id == "kimi-code/k3"


def test_evaluator_refuses_disabled_kimi_cli(monkeypatch) -> None:
    monkeypatch.delenv("KIMI_CLI_ENABLED", raising=False)
    parser = argparse.ArgumentParser()

    with pytest.raises(SystemExit):
        evaluator._validate_model_backends(parser, ["kimi-k3"])


def test_shard_merger_rejects_duplicate_sample_keys(tmp_path) -> None:
    record = {
        "sample_key": "gpt-5.6-sol|direct|V02_01_demo|1",
        "model": "gpt-5.6-sol",
        "condition": "direct",
        "task_id": "V02_01_demo",
        "repeat_index": 1,
        "status": "completed",
    }
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    encoded = json.dumps(record) + "\n"
    first.write_text(encoded, encoding="utf-8")
    second.write_text(encoded, encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate sample key"):
        merger._read([first, second])
