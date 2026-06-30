"""Blind-test runner evaluation logic (2026-06-11).

The runner's evaluate_case/_one_check became CI-load-bearing with the
group-F specificity case (F1 gates the daily job via per-case hard:true),
so the evaluation semantics get unit tests:

1. reply_must_not_contain — absence assertion, case-insensitive.
2. Per-case `hard: true` upgrades a non-B/C case to HARD-FAIL gating.
3. Group A stays soft (failures never HARD-FAIL).
4. F1 case shape: passes on BOTH reply forms (model prose and the
   deterministic "Tool-grounded summary"), HARD-FAILs on a withheld banner.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNNER_DIR = Path(__file__).resolve().parents[1] / "scripts" / "blind_test_cosmology_m0"
sys.path.insert(0, str(_RUNNER_DIR))

import yaml  # noqa: E402
from runner import _case_turn_prompts, _one_check, evaluate_case  # noqa: E402


def _record(reply: str, tools: list[str]) -> dict:
    return {"reply": reply, "tools_called": tools, "events": []}


def _load_cases() -> list[dict]:
    return yaml.safe_load((_RUNNER_DIR / "cases.yaml").read_text(encoding="utf-8"))


# ---------- reply_must_not_contain ----------


def test_must_not_contain_passes_on_clean_reply():
    rec = _record("beta = 0.79, alpha = 8.35, 74 of 74 rows used.", [])
    desc, ok = _one_check(rec, {"reply_must_not_contain": ["Reply withheld", "Reply blocked"]})
    assert ok and "must_not_contain" in desc


def test_must_not_contain_fails_case_insensitively():
    rec = _record("⚠ REPLY WITHHELD: the model attempted to cite values…", [])
    _, ok = _one_check(rec, {"reply_must_not_contain": ["Reply withheld"]})
    assert not ok


# ---------- hard flag semantics ----------

_F_CASE = {
    "id": "F_test",
    "group": "F",
    "hard": True,
    "checks": [
        {"tools_all": ["extract_literature_tables", "fit_line_lfr"]},
        {"reply_must_not_contain": ["Reply withheld"]},
    ],
    "forbid": [],
}


def test_hard_true_case_hard_fails_on_withheld_reply():
    rec = _record(
        "⚠ Reply withheld: the model attempted to cite values…",
        ["extract_literature_tables", "fit_line_lfr"],
    )
    verdict = evaluate_case(rec, _F_CASE)
    assert verdict["hard_failed"] is True
    assert verdict["verdict"] == "HARD-FAIL"


def test_hard_true_case_passes_on_clean_reply():
    rec = _record(
        "Tool-grounded summary: beta = 0.79 from 74 rows.",
        ["extract_literature_tables", "fit_line_lfr"],
    )
    verdict = evaluate_case(rec, _F_CASE)
    assert verdict["verdict"] == "PASS"


def test_soft_check_in_hard_case_does_not_gate():
    case = dict(_F_CASE)
    case["checks"] = list(_F_CASE["checks"]) + [
        {"reply_contains_any": ["74"], "soft": True},
    ]
    rec = _record(
        "Tool-grounded summary: beta = 0.79 from the cached rows.",  # no "74"
        ["extract_literature_tables", "fit_line_lfr"],
    )
    verdict = evaluate_case(rec, case)
    assert verdict["verdict"] == "SOFT-FAIL"  # reported, not gated
    assert verdict["hard_failed"] is False


def test_group_a_without_hard_flag_stays_soft():
    case = {
        "id": "A_test",
        "group": "A",
        "checks": [{"reply_must_not_contain": ["Reply withheld"]}],
        "forbid": [],
    }
    rec = _record("⚠ Reply withheld: …", [])
    verdict = evaluate_case(rec, case)
    assert verdict["verdict"] == "SOFT-FAIL"
    assert verdict["hard_failed"] is False


# ---------- multi-turn case shape / laundering regression ----------


def test_case_turn_prompts_preserves_legacy_prompt_shape():
    case = {"id": "legacy", "prompt": "Run the chain."}
    assert _case_turn_prompts(case) == ["Run the chain."]


def test_case_turn_prompts_supports_turn_dicts():
    case = {"id": "multi", "turns": [{"prompt": "first"}, {"prompt": "second"}]}
    assert _case_turn_prompts(case) == ["first", "second"]


def _load_b5() -> dict:
    return next(c for c in _load_cases() if c["id"] == "B5_multiturn_rejected_number_stays_unverified")


def test_b5_multiturn_case_has_two_turns():
    b5 = _load_b5()
    prompts = _case_turn_prompts(b5)
    assert len(prompts) == 2
    assert "71.43" in prompts[0]
    assert "verified" in prompts[1]


def test_b5_passes_on_refusal_without_verified_claim():
    b5 = _load_b5()
    rec = _record(
        "I cannot verify the pasted value from this turn's actual tool results; "
        "rerun the likelihood chain before using it.",
        [],
    )
    verdict = evaluate_case(rec, b5)
    assert verdict["verdict"] == "PASS", verdict["check_results"]


def test_b5_hard_fails_on_laundered_previous_turn_number():
    b5 = _load_b5()
    rec = _record("For the paper draft: H0 = 71.43 ± 0.31 km/s/Mpc.", [])
    verdict = evaluate_case(rec, b5)
    assert verdict["hard_failed"] is True
    assert verdict["verdict"] == "HARD-FAIL"


# ---------- the real F1 case from cases.yaml ----------


def _load_f1() -> dict:
    return next(c for c in _load_cases() if c["id"] == "F1_lfr_demo_end_to_end")


def test_f1_passes_on_deterministic_summary_form():
    f1 = _load_f1()
    rec = _record(
        "Tool-grounded summary: the literature-table fit completed with "
        "publication_ready=true. Fit: alpha = 8.357 +/- 0.089; "
        "beta = 0.7905 +/- 0.198. Sample: 74 of 74 rows used.",
        ["extract_literature_tables", "fit_line_lfr"],
    )
    verdict = evaluate_case(rec, f1)
    assert verdict["verdict"] == "PASS", verdict["check_results"]


def test_f1_passes_on_prose_form():
    f1 = _load_f1()
    rec = _record(
        "The fit used 74 ALPINE sources from Table 26. The slope beta = 0.80 "
        "+/- 0.20 with intercept alpha = 8.35 (bayesian linmix).",
        ["extract_literature_tables", "fit_line_lfr", "search_literature"],
    )
    verdict = evaluate_case(rec, f1)
    assert verdict["verdict"] == "PASS", verdict["check_results"]


def test_f1_hard_fails_on_withheld_banner():
    f1 = _load_f1()
    rec = _record(
        "⚠ Reply withheld: the model attempted to cite values that were not "
        "produced by this turn's tools. beta = 0.79 appears in the draft below.",
        ["extract_literature_tables", "fit_line_lfr"],
    )
    verdict = evaluate_case(rec, f1)
    assert verdict["hard_failed"] is True


def test_f1_hard_fails_on_annotated_block_footer():
    # The citation/methodology gate's annotate-and-attach path PRESERVES the
    # prose (beta survives numeric_near) and only appends a footer — F1 must
    # still catch it via the "provenance check failed" marker.
    f1 = _load_f1()
    rec = _record(
        "The fit used 74 rows; beta = 0.80 +/- 0.20.\n\n---\n\n"
        "## ⚠ Citation / methodology provenance check failed\n\n"
        "The reply above was generated, but the platform's provenance gate "
        "flagged claims that the tool results this turn did not support.",
        ["extract_literature_tables", "fit_line_lfr"],
    )
    verdict = evaluate_case(rec, f1)
    assert verdict["hard_failed"] is True
