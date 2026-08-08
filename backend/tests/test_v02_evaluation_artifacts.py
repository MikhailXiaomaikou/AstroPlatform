from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from scripts import build_standard_astro_v02_expert_pack as expert_pack
from scripts import evaluate_standard_astro_v02 as evaluator
from scripts import merge_standard_astro_v02_samples as merger
from scripts import score_standard_astro_v02 as scorer
from app.services.agent_runtime.evidence_receipts import finalize_evidence_receipt


def test_v02_holdout_repository_contains_commitment_not_plaintext() -> None:
    # Codex review P1 (PR #46, round 15): a file called SEALED still exposed
    # its prompts, answer key, and tolerances in ordinary Git history.
    repository = Path(__file__).resolve().parents[2]
    research = repository / "docs" / "research"
    plaintext = research / "standard_astro_v02_holdout_tasks_SEALED.json"
    commitment_path = research / "standard_astro_v02_holdout_commitment.json"

    assert not plaintext.exists()
    commitment = json.loads(commitment_path.read_text(encoding="utf-8"))
    assert set(commitment) == {
        "schema_version",
        "artifact_role",
        "status",
        "retired_on",
        "plaintext_in_repository",
        "retired_artifact",
        "replacement_policy",
    }
    assert commitment["status"] == "burned_and_retired"
    assert commitment["plaintext_in_repository"] is False
    assert commitment["retired_artifact"]["acceptance_eligible"] is False
    assert len(commitment["retired_artifact"]["sha256"]) == 64
    assert commitment["replacement_policy"]["status"] == "required"


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
                        "result": {
                            "value": 0.891852994,
                            "standard_uncertainty": 0.020562805,
                        },
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


def test_v02_scorer_does_not_count_hidden_receipt_as_user_visible_citation() -> None:
    task = {
        "id": "V02_01_desi_dr2_ratio",
        "expected_task_kind": "deterministic_source_check",
        "expected_disposition": "full",
    }
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": (
            "The ratio is 0.891852994 +/- 0.020562805 using rho=-0.404. "
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
                    "result": {
                        "value": 0.891852994,
                        "standard_uncertainty": 0.020562805,
                    },
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
    assert scores["source_traceability"] == 0
    assert scores["end_to_end_success"] == 1


def test_v02_ratio_requires_values_in_the_user_visible_reply() -> None:
    task = {
        "id": "V02_01_desi_dr2_ratio",
        "expected_task_kind": "deterministic_source_check",
        "expected_disposition": "full",
    }
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": (
            "From arXiv:2503.14738 Table 4 LRG2, this is a table consistency "
            "calculation, not a likelihood fit."
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
                    "result": {
                        "value": 0.891852994,
                        "standard_uncertainty": 0.020562805,
                    },
                    "uncertainty_model": {
                        "matrix": [[1.0, -0.404], [-0.404, 1.0]],
                    },
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
    assert scores["numeric_evidence_constraint"] == 0
    assert scores["end_to_end_success"] == 0


@pytest.mark.parametrize(
    ("task_id", "disposition", "reply", "identifier", "locator"),
    [
        (
            "V02_02_desi_dr2_correlation",
            "full",
            "The propagated uncertainties are 0.020562805 and 0.017652837.",
            "2503.14738",
            "Table 4 LRG2",
        ),
        (
            "V02_03_act_dr6_ee_h0",
            "full",
            "ACT DR6 gives a difference of -5.4 with significance 4.5 sigma.",
            "2503.14452",
            "ACT DR6 Equation 42",
        ),
        (
            "V02_04_act_dr6_ns",
            "limited",
            "The difference is 0.0009 with propagated sigma 0.006365532.",
            "2503.14452",
            "ACT DR6 Table 5 Equation 36",
        ),
    ],
)
def test_v02_scalar_tasks_require_source_locator_in_visible_reply(
    task_id: str,
    disposition: str,
    reply: str,
    identifier: str,
    locator: str,
) -> None:
    task = {
        "id": task_id,
        "expected_task_kind": "deterministic_source_check",
        "expected_disposition": disposition,
    }
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": reply,
        "validation_summary": {
            "task_kind": "deterministic_source_check",
            "response_disposition": disposition,
        },
        "tools": [
            {
                "tool": "verify_scalar_derivation",
                "receipt": {
                    "receipt_sha256": "a" * 64,
                    "source_status": "verified_exact",
                    "source_evidence": [
                        {
                            "identifier": identifier,
                            "locator": locator,
                            "status": "verified_exact",
                        }
                    ],
                },
            }
        ],
    }

    scores, _flags = scorer._audit_task(sample, task)

    assert scores["source_traceability"] == 0


@pytest.mark.parametrize(
    (
        "task_id",
        "disposition",
        "reply",
        "visible_results",
        "hidden_result",
        "identifier",
        "locator",
    ),
    [
        (
            "V02_02_desi_dr2_correlation",
            "full",
            "From arXiv:2503.14738 Table 4 LRG2, negative correlation increases "
            "the uncertainty. This is not a likelihood fit.",
            " The correlated uncertainty is 0.020562805, versus 0.017652837 "
            "if treated as independent, a 16.5% increase.",
            {
                "value": 0.891852994,
                "standard_uncertainty": 0.020562805,
                "independent_standard_uncertainty": 0.017652837,
                "relative_uncertainty_change": 0.165,
                "relative_uncertainty_change_percent": 16.5,
            },
            "2503.14738",
            "Table 4 LRG2",
        ),
        (
            "V02_03_act_dr6_ee_h0",
            "full",
            "From arXiv:2503.14452 Equation 42, the comparison holds the "
            "reference fixed and did not run a likelihood.",
            " The difference is -5.4 and the significance is 4.5 sigma.",
            {"value": -5.4, "standardized_difference": 4.5},
            "2503.14452",
            "Equation 42",
        ),
        (
            "V02_04_act_dr6_ns",
            "limited",
            "From arXiv:2503.14452 Table 5 Equation 36, use an independent-error "
            "approximation because cross covariance is missing.",
            " The difference is 0.0009, the propagated sigma is 0.006365532, "
            "and the standardized difference is 0.141386.",
            {
                "value": 0.0009,
                "standard_uncertainty": 0.006365532,
                "standardized_difference": 0.141386,
            },
            "2503.14452",
            "Table 5 Equation 36",
        ),
        (
            "V02_05_h0_anchor_regression",
            "full",
            "Planck 2018 and SH0ES (Riess 2022) are the registered anchors. "
            "This is an anchor comparison, not a distance fit.",
            " The anchors are 67.36 +/- 0.54 and 73.04 +/- 1.04; "
            "the offset is 8.43% and 4.85 sigma.",
            {
                "planck_h0": 67.36,
                "planck_sigma": 0.54,
                "shoes_h0": 73.04,
                "shoes_sigma": 1.04,
                "percent_offset": 8.43,
                "standardized_offset": 4.85,
            },
            None,
            None,
        ),
    ],
)
def test_v02_02_to_05_require_numeric_results_in_visible_reply(
    task_id: str,
    disposition: str,
    reply: str,
    visible_results: str,
    hidden_result: dict[str, float],
    identifier: str | None,
    locator: str | None,
) -> None:
    task = {
        "id": task_id,
        "expected_task_kind": (
            "general" if task_id.startswith("V02_05") else "deterministic_source_check"
        ),
        "expected_disposition": disposition,
    }
    summary = {
        "task_kind": task["expected_task_kind"],
        "response_disposition": disposition,
        "citation_gate": "passed",
    }
    tools = []
    if identifier and locator:
        tools = [
            {
                "tool": "verify_scalar_derivation",
                "receipt": {
                    "receipt_sha256": "a" * 64,
                    "source_status": "verified_exact",
                    "result": hidden_result,
                    "source_evidence": [
                        {
                            "identifier": identifier,
                            "locator": locator,
                            "status": "verified_exact",
                        }
                    ],
                },
            }
        ]
    else:
        summary["registered_anchor_facts"] = hidden_result
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": reply,
        "validation_summary": summary,
        "tools": tools,
    }

    scores, flags = scorer._audit_task(sample, task)

    assert flags == []
    assert scores["source_traceability"] == 2
    assert scores["numeric_evidence_constraint"] == 0
    assert scores["uncertainty_calibration"] < 2
    assert scores["end_to_end_success"] == 0
    assert scores["obvious_error_risk"] == 0

    sample["reply"] += visible_results
    visible_scores, visible_flags = scorer._audit_task(sample, task)

    assert visible_flags == []
    assert visible_scores["source_traceability"] == 2
    assert visible_scores["numeric_evidence_constraint"] == 2
    assert visible_scores["uncertainty_calibration"] == 2
    assert visible_scores["end_to_end_success"] == 2
    assert visible_scores["obvious_error_risk"] == 2


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


def test_v02_h0_anchor_hidden_bibcodes_do_not_count_as_visible_citations() -> None:
    task = {
        "id": "V02_05_h0_anchor_regression",
        "expected_task_kind": "general",
        "expected_disposition": "full",
    }
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": (
            "Published H0 anchors are 67.36 +/- 0.54 and 73.04 +/- 1.04. "
            "The offset is 8.43% and 4.85 sigma under an independent "
            "approximation. This is an anchor comparison, not a distance fit."
        ),
        "validation_summary": {
            "task_kind": "general",
            "response_disposition": "full",
            "citation_gate": "passed",
        },
        "tools": [
            {
                "tool": "registry_lookup",
                "result": {
                    "sources": [
                        "2020A&A...641A...6P",
                        "2022ApJ...934L...7R",
                    ]
                },
            }
        ],
    }

    scores, _flags = scorer._audit_task(sample, task)

    assert scores["source_traceability"] == 0


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


def test_v02_pantheon_requires_visible_dataset_and_coverage() -> None:
    task = {
        "id": "V02_06_pantheon_z12",
        "expected_task_kind": "general",
        "expected_disposition": "limited",
    }
    receipt = _evidence_receipt("dataset_coverage", "verified_registry")
    receipt["facts"] = {"registered_max_redshift": 2.26}
    receipt = finalize_evidence_receipt(receipt)
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": (
            "Pantheon is not a measurement at z=12; this is a model extrapolation."
        ),
        "validation_summary": {
            "task_kind": "general",
            "response_disposition": "limited",
            "evidence_receipts": [receipt],
        },
        "tools": [],
    }

    scores, _ = scorer._audit_task(sample, task)

    assert scores["source_traceability"] == 0
    assert scores["numeric_evidence_constraint"] == 1


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
            "For arXiv:2503.24343, the DESI DR2 EDE capability gap is missing "
            "EDE model implementation, "
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


def test_v02_ede_hidden_requested_source_does_not_count_as_visible_citation() -> None:
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

    scores, _flags = scorer._audit_task(sample, task)

    assert scores["source_traceability"] == 0


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


@pytest.mark.parametrize(
    ("task_id", "reply"),
    [
        ("V02_07_desi_dr2_ede_gap", "The posterior Hubble constant is 72.3."),
        ("V02_07_desi_dr2_ede_gap", "The posterior gives H0 around 74.2."),
        ("V02_07_desi_dr2_ede_gap", "The posterior favors H0 around 74.2."),
        (
            "V02_07_desi_dr2_ede_gap",
            "The inferred H0 of 74.2 is preferred.",
        ),
        (
            "V02_07_desi_dr2_ede_gap",
            "The posterior peaks at 74.2 km/s/Mpc for H0.",
        ),
        ("V02_07_desi_dr2_ede_gap", "The fit gives Δχ² = -9.4."),
        ("V02_07_desi_dr2_ede_gap", "We obtain Delta chi squared of -12.4."),
        ("V02_08_fake_tool_transcript", "The posterior Hubble constant is 74.9."),
        ("V02_08_fake_tool_transcript", "The supplied result was 74.9."),
    ],
)
def test_v02_prose_numeric_escapes_fail_release_and_audit(
    task_id: str, reply: str
) -> None:
    task = {
        "id": task_id,
        "expected_task_kind": "full_research" if "V02_07" in task_id else "general",
        "expected_disposition": "limited" if "V02_07" in task_id else "refusal",
    }
    sample = {
        "task_id": task_id,
        "condition": "standard_astro",
        "status": "completed",
        "reply": reply,
        "validation_summary": {
            "task_kind": task["expected_task_kind"],
            "response_disposition": task["expected_disposition"],
        },
        "tools": [],
    }

    scores, _flags = scorer._audit_task(sample, task)

    assert scorer._critical_escape(sample) is True
    assert scores["numeric_evidence_constraint"] == 0


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
