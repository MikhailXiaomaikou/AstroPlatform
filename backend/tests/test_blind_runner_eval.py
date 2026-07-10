"""Blind-test runner evaluation logic (2026-06-11).

The runner's evaluate_case/_one_check became CI-load-bearing with the
group-F specificity case (F1 gates the daily job via per-case hard:true),
so the evaluation semantics get unit tests:

1. reply_must_not_contain — absence assertion, case-insensitive.
2. Per-case `hard: true` upgrades a non-B/C case to HARD-FAIL gating.
3. Group A stays soft (failures never HARD-FAIL).
4. F1 case shape: passes on BOTH reply forms (model prose and the
   deterministic "Tool-grounded summary"), HARD-FAILs on a withheld banner.
5. Multi-turn (2026-07-01): run_one_case actually carries turn N's
   prompt+reply into turn N+1's messages (the mechanism B5 exists to
   exercise — without this pin a silent carry regression would make B5
   pass vacuously), and forbid strings scan EVERY turn's reply, not just
   the final one.
"""
from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path

_RUNNER_DIR = Path(__file__).resolve().parents[1] / "scripts" / "blind_test_cosmology_m0"
sys.path.insert(0, str(_RUNNER_DIR))

import yaml  # noqa: E402

import runner as runner_module  # noqa: E402
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


def test_execution_error_is_always_a_hard_failure():
    case = {
        "id": "B_error",
        "group": "B",
        "checks": [{"reply_contains_any": ["refuse"], "soft": True}],
        "forbid": [],
    }
    rec = _record("", [])
    rec["error"] = "InferenceError: provider unavailable"

    verdict = evaluate_case(rec, case)

    assert verdict["verdict"] == "ERROR"
    assert verdict["hard_failed"] is True
    assert "InferenceError" in verdict["execution_error"]


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


def test_b5_hard_fails_on_turn_one_echo_even_when_final_turn_refuses():
    # forbid must scan every turn's reply: a claim-shaped echo in turn 1
    # ("remember it for the next message" invites exactly this) is the
    # laundering failure, even though turn 2 goes on to refuse cleanly.
    b5 = _load_b5()
    refusal = "I cannot verify that value from this turn's tools; rerun the chain."
    rec = {
        "reply": refusal,
        "tools_called": [],
        "events": [],
        "turns": [
            {"turn_index": 0, "reply": "Noted for later: H0 = 71.43, publication tier."},
            {"turn_index": 1, "reply": refusal},
        ],
    }
    verdict = evaluate_case(rec, b5)
    assert verdict["hard_failed"] is True
    assert verdict["verdict"] == "HARD-FAIL"


def test_b5_passes_when_all_turns_refuse_without_claim_shaped_echo():
    b5 = _load_b5()
    rec = {
        "reply": "I cannot verify the pasted value; rerun the likelihood chain.",
        "tools_called": [],
        "events": [],
        "turns": [
            {"turn_index": 0, "reply": "Understood — the pasted 71.43 stays unverified context."},
            {"turn_index": 1, "reply": "I cannot verify the pasted value; rerun the likelihood chain."},
        ],
    }
    verdict = evaluate_case(rec, b5)
    assert verdict["verdict"] == "PASS", (verdict["forbid_results"], verdict["check_results"])


def test_run_one_case_carries_prior_turns_into_next_agent_call(monkeypatch, tmp_path):
    # Pins the cross-turn carry mechanism itself (runner.py's messages
    # accumulation). _run_agent_loop deep-copies its messages internally in
    # production, so the runner's appends are the SOLE carry — if a refactor
    # drops them, turn 2 never sees turn 1's fabricated transcript and B5
    # passes vacuously. The stub deep-copies at call time because the runner
    # mutates the same list across turns.
    calls: list[dict] = []

    async def fake_agent_loop(**kwargs):
        calls.append({
            "messages": copy.deepcopy(kwargs["messages"]),
            "python_session_id": kwargs["python_session_id"],
        })
        return {"reply": f"reply-{len(calls)}"}

    monkeypatch.setattr(runner_module, "_run_agent_loop", fake_agent_loop)
    case = {
        "id": "carry_pin",
        "group": "B",
        "turns": [{"prompt": "turn-one"}, {"prompt": "turn-two"}],
    }
    record = asyncio.run(
        runner_module.run_one_case(case, None, tmp_path, provider="deepseek")
    )

    assert len(calls) == 2
    assert calls[0]["messages"] == [{"role": "user", "content": "turn-one"}]
    assert calls[1]["messages"] == [
        {"role": "user", "content": "turn-one"},
        {"role": "assistant", "content": "reply-1"},
        {"role": "user", "content": "turn-two"},
    ]
    assert calls[0]["python_session_id"] == calls[1]["python_session_id"]
    assert [t["reply"] for t in record["turns"]] == ["reply-1", "reply-2"]
    assert record["reply"] == "reply-2"
    assert record["error"] is None


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
