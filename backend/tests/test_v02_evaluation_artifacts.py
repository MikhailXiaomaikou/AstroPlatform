from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest

from scripts import build_standard_astro_v02_expert_pack as expert_pack
from scripts import evaluate_standard_astro_v02 as evaluator
from scripts import merge_standard_astro_v02_samples as merger
from scripts import score_standard_astro_v02 as scorer
from app.services.agent_runtime.evidence_receipts import finalize_evidence_receipt
from app.services.scalar_derivation import canonical_receipt_sha256


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


def _signed_scalar_receipt(payload: dict) -> dict:
    receipt = dict(payload)
    receipt["receipt_sha256"] = canonical_receipt_sha256(receipt)
    return receipt


def test_compact_scalar_receipt_preserves_verifiable_digest() -> None:
    # Codex review P1 (PR #46, round 64): the compact evaluator artifact kept
    # the digest of the full receipt after dropping signed fields, making the
    # projection impossible to verify.
    receipt = _signed_scalar_receipt(
        {
            "success": True,
            "schema_version": 1,
            "task_kind": "deterministic_source_check",
            "operation": "difference",
            "result": {"value": -5.4, "standardized_difference": 4.5},
            "inputs": [{"id": "act", "value": 67.6}],
            "formula": "a-b",
            "uncertainty_model": {"kind": "independent"},
            "calculation_status": "verified_deterministic",
            "source_status": "verified_exact",
            "claim_scopes": {"derived_numeric": True, "source_measurement": True},
            "source_evidence": [
                {
                    "identifier": "2503.14452",
                    "locator": "Equation 42",
                    "status": "verified_exact",
                }
            ],
            "assumptions": [],
            "boundary_statement": "Not a likelihood fit.",
            "response_disposition": "full",
            "earliest_limiting_stage": None,
            "missing_dependencies": [],
            "safe_fallback": None,
            "publication_ready": False,
            "supports_measurement_claims": True,
            "supports_derived_numeric_claims": True,
            "__tool_status__": "COMPLETED",
            "__do_not_claim_source_measurement__": False,
        }
    )
    normalized = {**receipt, "reproducibility": {"run_id": "dispatcher-only"}}

    compact = evaluator._compact_scalar_receipt(normalized)

    assert compact["receipt_sha256"] == canonical_receipt_sha256(compact)
    assert compact["success"] is True
    assert compact["inputs"] == receipt["inputs"]
    assert compact["supports_measurement_claims"] is True


def test_v02_scorer_rejects_tampered_scalar_receipt() -> None:
    task = {
        "id": "V02_01_desi_dr2_ratio",
        "expected_task_kind": "deterministic_source_check",
        "expected_disposition": "full",
    }
    receipt = _signed_scalar_receipt(
        {
            "source_status": "verified_exact",
            "result": {"value": 999.0, "standard_uncertainty": 999.0},
            "source_evidence": [
                {
                    "identifier": "2503.14738",
                    "locator": "Table 4 LRG2",
                    "status": "verified_exact",
                }
            ],
        }
    )
    receipt["result"] = {
        "value": 0.891852994,
        "standard_uncertainty": 0.020562805,
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
        "tools": [{"tool": "verify_scalar_derivation", "receipt": receipt}],
    }

    scores, flags = scorer._audit_task(sample, task)

    assert "scalar_receipt_invalid_hash" in flags
    assert scores["source_traceability"] == 1
    assert scores["numeric_evidence_constraint"] == 0
    assert scores["end_to_end_success"] == 0


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
                "receipt": _signed_scalar_receipt({
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
                }),
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
                "receipt": _signed_scalar_receipt({
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
                }),
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
                "receipt": _signed_scalar_receipt({
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
                }),
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
                "receipt": _signed_scalar_receipt({
                    "source_status": "verified_exact",
                    "source_evidence": [
                        {
                            "identifier": identifier,
                            "locator": locator,
                            "status": "verified_exact",
                        }
                    ],
                }),
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
                "receipt": _signed_scalar_receipt({
                    "source_status": "verified_exact",
                    "result": hidden_result,
                    "source_evidence": [
                        {
                            "identifier": identifier,
                            "locator": locator,
                            "status": "verified_exact",
                        }
                    ],
                }),
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


def test_v02_05_full_scores_require_visible_anchor_errors() -> None:
    # Codex review P1 (PR #46, round 63): anchors plus derived values could
    # receive full end-to-end/risk scores while omitting both quoted errors.
    task = {
        "id": "V02_05_h0_anchor_regression",
        "expected_task_kind": "general",
        "expected_disposition": "full",
    }
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": (
            "Planck 2018 and SH0ES (Riess 2022) are the registered anchors. "
            "The anchors are 67.36 and 73.04; the offset is 8.43% and "
            "4.85 sigma. This is an anchor comparison, not a distance fit."
        ),
        "validation_summary": {
            "task_kind": "general",
            "response_disposition": "full",
            "citation_gate": "passed",
            "registered_anchor_facts": {
                "planck_h0": 67.36,
                "planck_sigma": 0.54,
                "shoes_h0": 73.04,
                "shoes_sigma": 1.04,
                "percent_offset": 8.43,
                "standardized_offset": 4.85,
            },
        },
        "tools": [],
    }

    scores, flags = scorer._audit_task(sample, task)

    assert flags == []
    assert scores["numeric_evidence_constraint"] == 2
    assert scores["uncertainty_calibration"] == 1
    assert scores["end_to_end_success"] == 1
    assert scores["obvious_error_risk"] == 1

    sample["reply"] += " The quoted one-sigma errors are 0.54 and 1.04."
    visible_scores, visible_flags = scorer._audit_task(sample, task)

    assert visible_flags == []
    assert visible_scores["uncertainty_calibration"] == 2
    assert visible_scores["end_to_end_success"] == 2
    assert visible_scores["obvious_error_risk"] == 2


def test_v02_03_full_end_to_end_requires_visible_difference() -> None:
    # Codex review P1 (PR #46, round 64): visible significance alone could earn
    # full end-to-end credit despite omitting the requested +/-5.4 difference.
    task = {
        "id": "V02_03_act_dr6_ee_h0",
        "expected_task_kind": "deterministic_source_check",
        "expected_disposition": "full",
    }
    sample = {
        "condition": "standard_astro",
        "status": "completed",
        "reply": (
            "From arXiv:2503.14452 Equation 42, the significance is 4.5 sigma. "
            "The reference was fixed and this was not a likelihood fit."
        ),
        "validation_summary": {
            "task_kind": "deterministic_source_check",
            "response_disposition": "full",
        },
        "tools": [
            {
                "tool": "verify_scalar_derivation",
                "receipt": _signed_scalar_receipt(
                    {
                        "source_status": "verified_exact",
                        "result": {"value": -5.4, "standardized_difference": 4.5},
                        "source_evidence": [
                            {
                                "identifier": "2503.14452",
                                "locator": "Equation 42",
                                "status": "verified_exact",
                            }
                        ],
                    }
                ),
            }
        ],
    }

    scores, flags = scorer._audit_task(sample, task)

    assert flags == []
    assert scores["numeric_evidence_constraint"] == 1
    assert scores["end_to_end_success"] == 1

    sample["reply"] += " The difference is -5.4."
    visible_scores, visible_flags = scorer._audit_task(sample, task)

    assert visible_flags == []
    assert visible_scores["numeric_evidence_constraint"] == 2
    assert visible_scores["end_to_end_success"] == 2


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
    assert scores["end_to_end_success"] == 2


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
    assert scores["end_to_end_success"] == 1


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
        ("V02_07_desi_dr2_ede_gap", "The posterior H0 peaks at 74.2."),
        ("V02_07_desi_dr2_ede_gap", "H0 is near 74.2 in the posterior."),
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
        (
            "V02_07_desi_dr2_ede_gap",
            "The posterior improves the fit by -12.4 in delta chi squared.",
        ),
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


def test_v02_delta_chi2_reference_number_is_not_a_result_escape() -> None:
    reply = "The fit uses Equation 12.4 for delta chi squared conventions."

    assert scorer._forbidden_numeric_escape(
        "V02_07_desi_dr2_ede_gap", reply
    ) is False


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


def test_completed_keys_retries_failed_rows(tmp_path: Path) -> None:
    # Codex review P2 (PR #54): rows recorded status="failed" must not mark
    # a sample complete — resume retries them, and the scorer takes the
    # superseding retry row instead of raising on the duplicate key.
    samples = tmp_path / "samples.jsonl"
    rows = [
        {"sample_key": "a", "status": "failed"},
        {"sample_key": "a", "status": "completed"},
        {"sample_key": "b", "status": "failed"},
        {"sample_key": "c", "status": "completed"},
    ]
    samples.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    assert evaluator._completed_keys(samples) == {"a", "c"}

    read = scorer._read_samples(samples)
    assert sorted(r["sample_key"] for r in read) == ["a", "b", "c"]
    by_key = {r["sample_key"]: r["status"] for r in read}
    assert by_key["a"] == "completed"


def test_completed_keys_duplicate_after_success_still_raises(
    tmp_path: Path,
) -> None:
    samples = tmp_path / "samples.jsonl"
    rows = [
        {"sample_key": "a", "status": "completed"},
        {"sample_key": "a", "status": "completed"},
    ]
    samples.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError):
        evaluator._completed_keys(samples)
    with pytest.raises(ValueError):
        scorer._read_samples(samples)


# ---------------------------------------------------------------------------
# v0.3 exploration harness extensions (plan §3.2) — default behaviour is the
# v0.2 matrix byte-for-byte; only explicit flags change anything.
# ---------------------------------------------------------------------------


def test_budget_flag_maps_to_runtime_config() -> None:
    from app.services.agent_runtime.runtime_config import _workflow_budget_config

    assert evaluator._workflow_budget_for("eval") == {
        "mode": "default",
        "max_iterations": 5,
        "agent_loop_seconds": 240,
        "summary_reserve_seconds": 30,
    }
    assert evaluator._workflow_budget_for("production") == {"mode": "default"}
    assert evaluator._workflow_budget_for("long") == {"mode": "long"}
    with pytest.raises(ValueError):
        evaluator._workflow_budget_for("bogus")

    # The loop applies ``budget = config(mode); budget.update(overrides)``.
    for name, expected in (("eval", (5, 240)), ("production", (12, 360)), ("long", (30, 1800))):
        overrides = evaluator._workflow_budget_for(name)
        budget = _workflow_budget_config(overrides.get("mode"))
        budget.update(overrides)
        assert (budget["max_iterations"], int(budget["agent_loop_seconds"])) == expected


def test_variant_sample_keys_are_unique_and_default_keys_unchanged() -> None:
    # v0.2 shape: no variant, no lightweight suffix -> today's key.
    assert (
        evaluator._sample_key(
            model="claude-fable-5", condition="standard_astro", task_id="V02_01", repeat_index=2
        )
        == "claude-fable-5|standard_astro|V02_01|2"
    )
    assert (
        evaluator._sample_key(
            model="m", condition="c", task_id="T", repeat_index=1, variant_id="zh"
        )
        == "m|c|T__zh|1"
    )
    assert (
        evaluator._sample_key(
            model="m", condition="c", task_id="T", repeat_index=1, lightweight_suffix="off"
        )
        == "m|c|T|1|lv=off"
    )

    tasks = [
        {"id": "T1", "prompt": "plain"},
        {
            "id": "T2",
            "prompt": "unused reference",
            "variants": [
                {"variant_id": "formal_en", "prompt": "formal"},
                {"variant_id": "zh", "prompt": "中文"},
            ],
        },
    ]
    expanded = evaluator._expand_variants(tasks)
    assert expanded == [
        ("T1", None, "plain"),
        ("T2", "formal_en", "formal"),
        ("T2", "zh", "中文"),
    ]
    specs = list(
        evaluator._iter_matrix(
            models=["m"], conditions=["direct", "standard_astro"], expanded_tasks=expanded, repeats=2
        )
    )
    keys = [spec.key for spec in specs]
    assert len(keys) == len(set(keys)) == 2 * 3 * 2
    assert "m|direct|T1|1" in keys and "m|standard_astro|T2__zh|2" in keys
    assert not any("lv=" in key for key in keys)

    # The frozen paraphrase file (2026-08-06) expands to unique keys too.
    variants_path = evaluator.REPO_ROOT / "docs/research/standard_astro_v02_paraphrase_variants.json"
    frozen = evaluator._expand_variants(evaluator._load_tasks(variants_path))
    frozen_keys = {
        evaluator._sample_key(
            model="m", condition="standard_astro", task_id=task_id, repeat_index=1, variant_id=variant_id
        )
        for task_id, variant_id, _prompt in frozen
    }
    assert len(frozen_keys) == len(frozen) == 8 * 4

    with pytest.raises(ValueError, match="variant ids"):
        evaluator._expand_variants(
            [{"id": "T", "variants": [{"variant_id": "a", "prompt": "x"}, {"variant_id": "a", "prompt": "y"}]}]
        )


def test_lightweight_both_doubles_matrix_and_restores_setting(monkeypatch) -> None:
    expanded = [("T1", None, "prompt one")]
    single = list(
        evaluator._iter_matrix(
            models=["m"], conditions=["standard_astro"], expanded_tasks=expanded, repeats=2
        )
    )
    both = list(
        evaluator._iter_matrix(
            models=["m"],
            conditions=["standard_astro"],
            expanded_tasks=expanded,
            repeats=2,
            lightweight="both",
        )
    )
    assert [spec.key for spec in single] == ["m|standard_astro|T1|1", "m|standard_astro|T1|2"]
    assert [spec.key for spec in both] == [
        "m|standard_astro|T1|1|lv=on",
        "m|standard_astro|T1|1|lv=off",
        "m|standard_astro|T1|2|lv=on",
        "m|standard_astro|T1|2|lv=off",
    ]
    assert [spec.lightweight for spec in both] == [True, False, True, False]
    # The direct condition never enters the agent loop: one sample, no suffix.
    direct = list(
        evaluator._iter_matrix(
            models=["m"], conditions=["direct"], expanded_tasks=expanded, repeats=1, lightweight="both"
        )
    )
    assert [spec.key for spec in direct] == ["m|direct|T1|1"]

    observed: list[bool] = []

    async def fake_loop(**kwargs):
        observed.append(bool(evaluator.settings.lightweight_verification_enabled))
        on_event = kwargs["on_event"]
        await on_event({"type": "workflow_budget", "mode": "default", "max_iterations": 12, "agent_loop_seconds": 360})
        await on_event({"type": "status", "message": "Listing the curated observational-cosmology dataset registry."})
        await on_event({"type": "tool_call", "tool": "list_cosmology_datasets", "input": {}})
        await on_event({"type": "tool_call", "tool": "build_cosmology_likelihood", "input": {}})
        await on_event({"type": "agent_text", "content": "thinking"})
        await on_event({"type": "tool_call", "tool": "run_cosmology_likelihood", "input": {}})
        await on_event({"type": "agent_text", "content": "withheld", "draft": True})
        assert kwargs["workflow_budget"] == {"mode": "default"}
        return {
            "reply": "done",
            "validation_summary": None,
            "tool_results": [
                {"tool": "run_cosmology_likelihood", "result": {"H0": 67.4, "bibcode": "2020A&A...641A...6P", "n": 3}}
            ],
        }

    monkeypatch.setattr(evaluator.chat, "_run_agent_loop", fake_loop)
    monkeypatch.setattr(evaluator.settings, "lightweight_verification_enabled", False)
    options = evaluator.RunOptions(budget="production", arm="C1", tasks_sha256="abc", git_rev="deadbee")

    records = []
    for spec in both[:2]:
        records.append(
            asyncio.run(
                evaluator._run_sample(
                    model=spec.model,
                    condition=spec.condition,
                    task_id=spec.task_id,
                    prompt=spec.prompt,
                    repeat_index=spec.repeat_index,
                    evaluation_id="test",
                    variant_id=spec.variant_id,
                    lightweight=spec.lightweight,
                    lightweight_suffix=spec.lightweight_suffix,
                    options=options,
                )
            )
        )
        # Restored after every sample, whatever the sample set.
        assert evaluator.settings.lightweight_verification_enabled is False

    assert observed == [True, False]
    assert [r["status"] for r in records] == ["completed", "completed"]
    assert [r["lightweight_verification_enabled"] for r in records] == [True, False]
    first = records[0]
    assert first["sample_key"] == "m|standard_astro|T1|1|lv=on"
    assert first["arm"] == "C1" and first["git_rev"] == "deadbee" and first["tasks_sha256"] == "abc"
    assert first["variant_id"] is None
    assert (first["budget_mode"], first["max_iterations"], first["agent_loop_seconds"]) == ("default", 12, 360)
    assert first["tool_sequence"] == [
        "list_cosmology_datasets",
        "build_cosmology_likelihood",
        "run_cosmology_likelihood",
    ]
    assert first["n_tool_calls"] == 3
    assert first["forced_tool_calls"] == 2
    assert first["model_chosen_tool_calls"] == 1
    assert first["soft_reminder_fired"] is False
    assert first["draft_agent_text_events"] == 1
    assert first["steering_disabled"] is False
    assert first["tool_scalar_universe"] == [3.0, 67.4]
    assert first["routing_probe"]["task_kind"] in {
        "deterministic_source_check",
        "research_exploration",
        "full_research",
        "general",
    }
    assert first["llm_calls"] == 0 and first["visible_tools_per_llm_call"] == []
    assert first["elapsed_seconds"] >= 0


def test_v03_task_file_loads_without_the_eight_task_rule(tmp_path: Path) -> None:
    v03 = tmp_path / "v03.json"
    v03.write_text(
        json.dumps(
            {
                "evaluation_id": "standard-astro-v03-exploration-depth",
                "tasks": [{"id": "V03_01", "prompt": "a"}, {"id": "V03_02", "prompt": "b"}],
            }
        ),
        encoding="utf-8",
    )
    assert [task["id"] for task in evaluator._load_tasks(v03)] == ["V03_01", "V03_02"]

    v02 = tmp_path / "v02.json"
    v02.write_text(
        json.dumps(
            {
                "evaluation_id": "standard-astro-v02-something",
                "tasks": [{"id": "V02_01", "prompt": "a"}, {"id": "V02_02", "prompt": "b"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly eight tasks"):
        evaluator._load_tasks(v02)


def test_registered_repeats_give_each_task_class_its_count() -> None:
    """The frozen v0.3 design registers chain x2 and open x4.  One global
    --repeats produced 12 open samples per flag state - the underpowered zone
    where a zero-event result cannot exclude the 25% threshold (review
    2026-09-03).  An explicit --repeats still overrides the file."""
    tasks_path = (
        evaluator.REPO_ROOT / "docs/research/standard_astro_v03_exploration_tasks.json"
    )
    tasks = evaluator._load_tasks(tasks_path)
    per_task = evaluator._registered_repeats(tasks_path, tasks)
    task_class = {str(t["id"]): str(t["task_class"]) for t in tasks}
    assert {task_class[k]: v for k, v in per_task.items()} == {"chain": 2, "open": 4}

    specs = list(
        evaluator._iter_matrix(
            models=["m"],
            conditions=["standard_astro"],
            expanded_tasks=evaluator._expand_variants(tasks),
            repeats=evaluator.DEFAULT_REPEATS,
            lightweight="both",
            repeats_by_task=per_task,
        )
    )
    assert len({spec.key for spec in specs}) == len(specs)
    open_off = [s for s in specs if task_class[s.task_id] == "open" and not s.lightweight]
    chain_off = [s for s in specs if task_class[s.task_id] == "chain" and not s.lightweight]
    assert len(open_off) == 16 and len(chain_off) == 8
    assert sorted({s.repeat_index for s in open_off}) == [1, 2, 3, 4]
    assert sorted({s.repeat_index for s in chain_off}) == [1, 2]

    # No registered_repeats (every v0.2 file) -> the caller's own default.
    v02_path = evaluator.REPO_ROOT / "docs/research/standard_astro_v02_preregistered_tasks.json"
    assert evaluator._registered_repeats(v02_path, evaluator._load_tasks(v02_path)) == {}

    # An explicit --repeats overrides: main() passes an empty mapping.
    overridden = list(
        evaluator._iter_matrix(
            models=["m"],
            conditions=["standard_astro"],
            expanded_tasks=evaluator._expand_variants(tasks),
            repeats=1,
            lightweight="off",
            repeats_by_task={},
        )
    )
    assert len(overridden) == len(tasks)


def test_steering_ablation_is_refused_when_the_switch_is_missing(monkeypatch) -> None:
    """C2d's whole intervention is settings.evaluation_steering_disabled.  When
    the build has no such switch the runner used to warn and then collect
    ordinary flag-off samples labelled arm="C2d", so an ablation that
    intervened in nothing could read as if it had (review 2026-09-03)."""

    def _args(**overrides):
        base = dict(
            arm="C2d",
            system_appendix=None,
            conditions=None,
            budget=None,
            lightweight=None,
            steering=None,
            lane_override=False,
            exploration_phase=False,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    parser = argparse.ArgumentParser()
    monkeypatch.delattr(
        type(evaluator.settings), "evaluation_steering_disabled", raising=False
    )
    assert not hasattr(evaluator.settings, "evaluation_steering_disabled")
    with pytest.raises(SystemExit):
        evaluator._resolve_arm(parser, _args())
    # An explicit --steering off is refused for the same reason.
    with pytest.raises(SystemExit):
        evaluator._resolve_arm(parser, _args(arm=None, steering="off"))
    # Arms that do not touch steering are unaffected.
    resolved = _args(arm="C1")
    evaluator._resolve_arm(parser, resolved)
    assert resolved.steering == "on" and resolved.lightweight == "both"

    # With the switch present the arm resolves to its registered cell.
    monkeypatch.setattr(
        type(evaluator.settings), "evaluation_steering_disabled", False, raising=False
    )
    c2d = _args()
    evaluator._resolve_arm(parser, c2d)
    assert (c2d.steering, c2d.lightweight, c2d.budget) == ("off", "off", "production")
