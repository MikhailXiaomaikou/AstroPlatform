from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from scripts import score_standard_astro_v03_exploration as scorer


OPEN_TASK = "V03_03_h0_anchor_clustering"
CHAIN_TASK = "V03_01_bao_release_dependence"
OPEN_SEQUENCE_OFF = [
    "list_cosmology_datasets",
    "build_cosmology_likelihood",
    "run_cosmology_likelihood_chain",
]
CHAIN_SEQUENCE_OFF = [
    "list_cosmology_datasets",
    "build_cosmology_likelihood",
    "run_cosmology_likelihood_chain",
    "evaluate_chain_diagnostics",
]


def _task_payload() -> dict[str, Any]:
    return {
        "evaluation_id": "standard-astro-v03-exploration",
        "tasks": [
            {
                "id": OPEN_TASK,
                "task_class": "open",
                "prompt": "Which local anchors cluster together and which sit near BAO+BBN?",
                "registered_datasets": ["shoes_h0_riess22", "desi_dr2_bao", "bbn_ombh2_schoeneberg24"],
                "reachable_set": {
                    "flag_off": OPEN_SEQUENCE_OFF + ["load_cosmology_data_product"],
                    "flag_on": ["list_cosmology_datasets", "compare_luminosity_distances"],
                },
                "next_obvious_sequence": {
                    "flag_off": OPEN_SEQUENCE_OFF,
                    "flag_on": ["list_cosmology_datasets", "compare_luminosity_distances"],
                },
                "expected_disposition": "full",
            },
            {
                "id": CHAIN_TASK,
                "task_class": "chain",
                "prompt": "Run LCDM and w0wa with DESI DR1 versus DR2 BAO.",
                "registered_datasets": ["desi_dr1_bao", "desi_dr2_bao", "planck2018_compressed"],
                "reachable_set": {"flag_off": CHAIN_SEQUENCE_OFF, "flag_on": CHAIN_SEQUENCE_OFF},
                "next_obvious_sequence": CHAIN_SEQUENCE_OFF,
                "expected_disposition": "limited",
            },
        ],
    }


@pytest.fixture
def tasks_path(tmp_path: Path) -> Path:
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(_task_payload()), encoding="utf-8")
    return path


def _sample(**overrides: Any) -> dict[str, Any]:
    sequence = overrides.pop("tool_sequence", ["list_cosmology_datasets"])
    sample: dict[str, Any] = {
        "sample_key": "claude-fable-5|C1|" + OPEN_TASK + "__base|0",
        "model": "claude-fable-5",
        "condition": "standard_astro",
        "arm": "C1",
        "task_id": OPEN_TASK,
        "variant_id": "base",
        "repeat_index": 0,
        "lightweight_verification_enabled": False,
        "steering_disabled": False,
        "budget_mode": "production",
        "llm_calls": 3,
        "hit_iteration_cap": False,
        "hit_deadline": False,
        "elapsed_seconds": 42.0,
        "n_tool_calls": len(sequence),
        "tool_sequence": list(sequence),
        "distinct_tools": sorted(set(sequence)),
        "forced_tool_calls": [],
        "model_chosen_tool_calls": list(sequence),
        "soft_reminder_fired": False,
        "visible_tools_per_llm_call": [OPEN_SEQUENCE_OFF, OPEN_SEQUENCE_OFF],
        "routing_probe": {"task_kind": "general"},
        "tool_scalar_universe": [73.04, 67.4, 0.315],
        "draft_agent_text_events": 0,
        "reply": "The SH0ES anchor sits high; BAO+BBN sits low. No fit was run.",
        "tools": [{"tool": name, "status": "OK", "publication_ready": True} for name in sequence],
        "validation_summary": {"response_disposition": "full", "interventions": []},
        "transport_status": "completed",
    }
    sample.update(overrides)
    return sample


def _score(sample: dict[str, Any], tasks_path: Path) -> dict[str, Any]:
    tasks, sha = scorer._read_tasks(tasks_path)
    return scorer.score_samples([sample], tasks, sha)[0]


# --------------------------------------------------------------------------- #
# stop_reason_class fixtures, one per class
# --------------------------------------------------------------------------- #


def _chain_withheld_sample() -> dict[str, Any]:
    sequence = CHAIN_SEQUENCE_OFF[:3]
    return _sample(
        sample_key="claude-fable-5|C1|" + CHAIN_TASK + "__base|0",
        task_id=CHAIN_TASK,
        tool_sequence=sequence,
        forced_tool_calls=list(sequence),
        model_chosen_tool_calls=[],
        visible_tools_per_llm_call=[CHAIN_SEQUENCE_OFF],
        tools=[
            {"tool": "list_cosmology_datasets", "status": "OK"},
            {"tool": "build_cosmology_likelihood", "status": "OK"},
            {
                "tool": "run_cosmology_likelihood_chain",
                "status": "EXPLORATORY",
                "publication_ready": False,
            },
        ],
        validation_summary={"response_disposition": "limited", "interventions": []},
    )


STOP_FIXTURES: dict[str, dict[str, Any]] = {
    "completed_reachable": _sample(tool_sequence=OPEN_SEQUENCE_OFF),
    "premature_stop": _sample(),
    "blocked_by_lane": _sample(
        visible_tools_per_llm_call=[OPEN_SEQUENCE_OFF, ["list_cosmology_datasets"]]
    ),
    "blocked_by_cascade": _sample(
        tool_sequence=["list_cosmology_datasets", "load_cosmology_data_product"],
        tools=[
            {"tool": "list_cosmology_datasets", "status": "OK"},
            {"tool": "load_cosmology_data_product", "status": "FAILED"},
        ],
    ),
    "tier_withheld": _chain_withheld_sample(),
    "cap": _sample(hit_iteration_cap=True),
    "deadline": _sample(hit_deadline=True),
    "forced_chain_only": _sample(llm_calls=0, forced_tool_calls=["list_cosmology_datasets"]),
    "refusal": _sample(validation_summary={"response_disposition": "refusal", "interventions": []}),
    "transport_failed": _sample(transport_status="failed", error="bridge timeout", reply=None),
}


def test_every_stop_reason_class_has_a_fixture() -> None:
    assert set(STOP_FIXTURES) == set(scorer.STOP_REASON_CLASSES)


@pytest.mark.parametrize("expected_class", sorted(STOP_FIXTURES))
def test_stop_reason_class_fixtures(expected_class: str, tasks_path: Path) -> None:
    row = _score(STOP_FIXTURES[expected_class], tasks_path)
    assert row["stop_reason_class"] == expected_class, row["reason_premature_stop"]
    assert row["premature_stop"] is (expected_class == "premature_stop")


def test_premature_stop_false_when_next_tool_not_visible(tasks_path: Path) -> None:
    row = _score(STOP_FIXTURES["blocked_by_lane"], tasks_path)
    assert row["premature_stop"] is False
    assert "build_cosmology_likelihood not visible" in row["reason_premature_stop"]


def test_premature_stop_false_when_deadline_hit(tasks_path: Path) -> None:
    row = _score(_sample(hit_deadline=True), tasks_path)
    assert row["premature_stop"] is False
    assert row["stop_reason_class"] == "deadline"
    slow = _score(_sample(elapsed_seconds=200.0), tasks_path)
    assert slow["premature_stop"] is False
    assert slow["stop_reason_class"] == "deadline"


def test_premature_stop_uses_last_llm_call_visibility(tasks_path: Path) -> None:
    # Visible earlier but stripped in the last call: not premature.
    row = _score(
        _sample(visible_tools_per_llm_call=[OPEN_SEQUENCE_OFF, []]), tasks_path
    )
    assert row["stop_reason_class"] == "blocked_by_lane"
    assert "menu empty" in row["reason_premature_stop"]
    # Missing record is conservative: never counted as premature.
    unknown = _score(_sample(visible_tools_per_llm_call=[]), tasks_path)
    assert unknown["premature_stop"] is False
    assert "visibility" in unknown["reason_premature_stop"]


def test_tier_withheld_chain_sample_not_premature(tasks_path: Path) -> None:
    row = _score(_chain_withheld_sample(), tasks_path)
    assert row["premature_stop"] is False
    assert row["stop_reason_class"] == "tier_withheld"
    assert row["push_through"] == 1  # prose only after the withheld chain result


def test_flag_on_uses_flag_on_sequence(tasks_path: Path) -> None:
    row = _score(
        _sample(
            lightweight_verification_enabled=True,
            visible_tools_per_llm_call=[["list_cosmology_datasets", "compare_luminosity_distances"]],
        ),
        tasks_path,
    )
    assert row["flag"] == "flag_on"
    assert row["premature_stop"] is True
    assert "compare_luminosity_distances" in row["reason_premature_stop"]


# --------------------------------------------------------------------------- #
# hard_escape and the other dimensions
# --------------------------------------------------------------------------- #


def test_hard_escape_fires_outside_universe(tasks_path: Path) -> None:
    row = _score(_sample(reply="Combining anchors, H0 = 68.3 km/s/Mpc."), tasks_path)
    assert row["hard_escape"] is True
    assert "68.3" in row["hard_escape_values"]


def test_hard_escape_silent_on_universe_value(tasks_path: Path) -> None:
    row = _score(_sample(reply="Planck gives H0 = 67.4 km/s/Mpc (tool output)."), tasks_path)
    assert row["hard_escape"] is False
    assert row["claim_layering"] == 2


@pytest.mark.parametrize(
    "reply",
    [
        "The fit prefers w0 = -0.84.",
        "We find Omega_m ≈ 0.29 from the chain.",
        "S8 is 0.77 for the lensing subset.",
        "sigma8 = 0.81 here.",
        "Ω_k = -0.012 from the curvature run.",
        "Sum m_nu < 0.12 eV; mnu = 0.06 eV assumed.",
    ],
)
def test_hard_escape_covers_extended_parameter_labels(reply: str, tasks_path: Path) -> None:
    row = _score(_sample(reply=reply), tasks_path)
    assert row["hard_escape"] is True, reply


def test_hard_escape_ignores_values_quoted_from_the_prompt(tasks_path: Path) -> None:
    tasks, sha = scorer._read_tasks(tasks_path)
    tasks[OPEN_TASK]["prompt"] = "Is w0 = -1 recovered?"
    row = scorer.score_samples([_sample(reply="The prompt asks whether w0 = -1.")], tasks, sha)[0]
    assert row["hard_escape"] is False


def test_claim_layering_zero_on_unlabelled_number(tasks_path: Path) -> None:
    row = _score(_sample(reply="The tension is about 5.2 sigma."), tasks_path)
    assert row["claim_layering"] == 0
    assert "5.2" in row["reason_claim_layering"]


def test_claim_layering_requires_label_when_limited(tasks_path: Path) -> None:
    limited = {
        "response_disposition": "limited",
        "interventions": [{"gate": "numeric", "action": "withhold", "reason": "exploratory"}],
    }
    unlabelled = _score(_sample(validation_summary=limited), tasks_path)
    assert unlabelled["claim_layering"] == 1
    labelled = _score(
        _sample(validation_summary=limited, reply="Exploratory only: the chain is withheld."),
        tasks_path,
    )
    assert labelled["claim_layering"] == 2


def test_hypothesis_generation_counts_labelled_trace_alternatives(tasks_path: Path) -> None:
    reply = (
        "One possibility is that shoes_h0_riess22 carries a calibration offset. "
        "Alternatively the DESI DR2 BAO scale itself shifts. "
        "Another explanation could also be something unrelated to any dataset."
    )
    row = _score(_sample(reply=reply), tasks_path)
    assert row["hypothesis_candidates"] == 2
    assert row["hypothesis_generation"] == 2
    single = _score(_sample(reply="hypothesis: the desi_dr2_bao scale is off."), tasks_path)
    assert single["hypothesis_generation"] == 1
    none = _score(_sample(reply="Alternatively the moon is made of cheese."), tasks_path)
    assert none["hypothesis_generation"] == 0


def test_exploration_breadth_excludes_forced_calls(tasks_path: Path) -> None:
    forced_only = _score(
        _sample(
            tool_sequence=OPEN_SEQUENCE_OFF,
            forced_tool_calls=list(OPEN_SEQUENCE_OFF),
            model_chosen_tool_calls=[],
        ),
        tasks_path,
    )
    assert forced_only["exploration_breadth"] == 0
    partial = _score(_sample(), tasks_path)  # 1 of 4 reachable, model-chosen
    assert partial["exploration_breadth"] == 1
    broad = _score(_sample(tool_sequence=OPEN_SEQUENCE_OFF), tasks_path)  # 3 of 4
    assert broad["exploration_breadth"] == 2
    counted = _score(
        _sample(tool_sequence=OPEN_SEQUENCE_OFF, forced_tool_calls=2, model_chosen_tool_calls=1),
        tasks_path,
    )
    assert counted["exploration_breadth"] == 1
    assert "leading positions" in counted["reason_exploration_breadth"]


def test_push_through_scores(tasks_path: Path) -> None:
    failed = {"tool": "load_cosmology_data_product", "status": "FAILED"}
    pushed = _score(
        _sample(
            tool_sequence=["load_cosmology_data_product", "list_cosmology_datasets"],
            tools=[failed, {"tool": "list_cosmology_datasets", "status": "OK"}],
        ),
        tasks_path,
    )
    assert pushed["push_through"] == 2
    refused = _score(
        _sample(
            tool_sequence=["load_cosmology_data_product"],
            tools=[failed],
            reply="",
            validation_summary={"response_disposition": "refusal", "interventions": []},
        ),
        tasks_path,
    )
    assert refused["push_through"] == 0
    assert _score(_sample(), tasks_path)["push_through"] is None


def test_under_delivery_only_when_full_expected(tasks_path: Path) -> None:
    limited = {"response_disposition": "limited", "interventions": []}
    assert _score(_sample(validation_summary=limited), tasks_path)["under_delivery"] is True
    chain = _score(_chain_withheld_sample(), tasks_path)
    assert chain["under_delivery"] is False


def test_unknown_task_and_tasks_sha_mismatch_rejected(tasks_path: Path) -> None:
    tasks, sha = scorer._read_tasks(tasks_path)
    with pytest.raises(ValueError, match="Unknown task id"):
        scorer.score_samples([_sample(task_id="V03_99_nope")], tasks, sha)
    with pytest.raises(ValueError, match="sha256"):
        scorer.score_samples([_sample(tasks_sha256="deadbeef")], tasks, sha)
    scorer.score_samples([_sample(tasks_sha256=sha)], tasks, sha)


# --------------------------------------------------------------------------- #
# Statistics and strata
# --------------------------------------------------------------------------- #


def test_wilson_interval_sanity() -> None:
    zero = scorer.wilson_interval(0, 16)
    assert zero is not None and zero[0] == 0.0 and 0.19 < zero[1] < 0.21
    full = scorer.wilson_interval(16, 16)
    assert full is not None and full[1] == 1.0 and 0.79 < full[0] < 0.81
    half = scorer.wilson_interval(8, 16)
    assert half is not None and abs((half[0] + half[1]) / 2 - 0.5) < 1e-9
    # Wilson 95% for 8/16 is [0.280, 0.720]; it does not reach 0.25.
    assert 0.27 < half[0] < 0.29 and 0.71 < half[1] < 0.73
    assert half[0] < 0.5 < half[1]
    assert scorer.wilson_interval(0, 0) is None


def test_rule_of_three_for_zero_events() -> None:
    zero = scorer._rate_block(0, 8)
    assert zero["upper_bound_rule_of_three"] == pytest.approx(0.375)
    assert scorer._rate_block(0, 16)["upper_bound_rule_of_three"] == pytest.approx(0.1875)
    assert scorer._rate_block(1, 8)["upper_bound_rule_of_three"] is None
    assert scorer._rate_block(0, 0)["upper_bound_rule_of_three"] is None


def _matrix_rows(tasks_path: Path) -> list[dict[str, Any]]:
    tasks, sha = scorer._read_tasks(tasks_path)
    samples: list[dict[str, Any]] = []
    for flag_on in (False, True):
        for repeat in range(4):
            key = f"claude-fable-5|C1|{OPEN_TASK}__base|{repeat}|{flag_on}"
            visible = (
                [["list_cosmology_datasets", "compare_luminosity_distances"]]
                if flag_on
                else [OPEN_SEQUENCE_OFF]
            )
            samples.append(
                _sample(
                    sample_key=key,
                    repeat_index=repeat,
                    lightweight_verification_enabled=flag_on,
                    visible_tools_per_llm_call=visible,
                    tool_sequence=OPEN_SEQUENCE_OFF if repeat == 0 else ["list_cosmology_datasets"],
                )
            )
        samples.append(
            _sample(
                sample_key=f"claude-fable-5|C1|{CHAIN_TASK}__base|0|{flag_on}",
                task_id=CHAIN_TASK,
                lightweight_verification_enabled=flag_on,
                llm_calls=0,
                tool_sequence=CHAIN_SEQUENCE_OFF,
                forced_tool_calls=list(CHAIN_SEQUENCE_OFF),
                model_chosen_tool_calls=[],
            )
        )
    return scorer.score_samples(samples, tasks, sha)


def test_strata_emitted_separately_without_blended_headline(tasks_path: Path) -> None:
    rows = _matrix_rows(tasks_path)
    summary = scorer.build_summary(rows, tasks_sha256="abc", primary_arm=None)
    strata = summary["strata"]
    assert set(strata) == {
        f"{flag}|{loop}|{cls}"
        for flag in scorer.FLAGS
        for loop in scorer.LOOPS
        for cls in scorer.TASK_CLASSES
    }
    assert strata["flag_off|model_in_loop|open"]["n"] == 4
    assert strata["flag_on|model_in_loop|open"]["n"] == 4
    assert strata["flag_off|pipeline|chain"]["n"] == 1
    assert strata["flag_on|pipeline|chain"]["n"] == 1
    assert strata["flag_off|pipeline|open"]["n"] == 0
    assert sum(block["n"] for block in strata.values()) == len(rows)
    # No blended headline: every rate lives under a three-part stratum key.
    blended = [
        key
        for key in summary
        if any(token in key for token in ("premature", "rate", "overall", "headline", "all_"))
    ]
    assert blended == []
    assert all(key.count("|") == 2 for key in strata)
    off = strata["flag_off|model_in_loop|open"]["premature_stop"]
    assert off["count"] == 3 and off["n"] == 4
    assert off["wilson95"][0] < 0.75 < off["wilson95"][1]
    assert strata["flag_off|pipeline|chain"]["stop_reason_classes"]["forced_chain_only"] == 1


def test_decision_reads_only_the_primary_stratum(tasks_path: Path) -> None:
    rows = _matrix_rows(tasks_path)
    decision = scorer.build_summary(rows, tasks_sha256="abc", primary_arm=None)["decision"]
    assert decision["rule"] == "open-task, flag_off, model-in-loop premature_stop rate >= 0.25"
    assert decision["primary_n"] == 4
    assert decision["premise_reproduced"] is True
    # Only flag_on rows: the primary stratum is empty and the verdict is undetermined.
    flag_on_only = [r for r in rows if r["flag"] == "flag_on"]
    empty = scorer.build_summary(flag_on_only, tasks_sha256="abc", primary_arm=None)["decision"]
    assert empty["premise_reproduced"] is None and empty["primary_n"] == 0
    # Zero events with an underpowered rule-of-three bound stay undetermined.
    calm = [dict(r, premature_stop=False) for r in rows if r["stratum"] == "flag_off|model_in_loop|open"]
    zero = scorer.build_summary(calm, tasks_sha256="abc", primary_arm=None)["decision"]
    assert zero["premise_reproduced"] is None
    assert zero["upper_bound_rule_of_three"] == pytest.approx(0.75)
    sixteen = [dict(r, premature_stop=False, sample_key=f"k{i}") for i in range(16) for r in calm[:1]]
    powered = scorer.build_summary(sixteen, tasks_sha256="abc", primary_arm=None)["decision"]
    assert powered["premise_reproduced"] is False


def test_multiple_arms_are_kept_apart(tasks_path: Path) -> None:
    rows = _matrix_rows(tasks_path)
    other = [dict(r, arm="C2a", sample_key=r["sample_key"] + "|C2a") for r in rows]
    summary = scorer.build_summary(rows + other, tasks_sha256="abc", primary_arm=None)
    assert summary["arms"] == ["C1", "C2a"]
    assert summary["primary_arm"] == "C1"
    assert summary["strata"]["flag_off|model_in_loop|open"]["n"] == 4
    assert summary["by_arm"]["C2a"]["flag_off|model_in_loop|open"]["n"] == 4


# --------------------------------------------------------------------------- #
# CLI, adjudication round trip, markdown
# --------------------------------------------------------------------------- #


def _write_samples(path: Path, samples: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(s) for s in samples) + "\n", encoding="utf-8")


def test_cli_end_to_end_with_adjudication(tmp_path: Path, tasks_path: Path, monkeypatch: Any) -> None:
    samples_path = tmp_path / "C1_abc1234_samples.jsonl"
    escape = _sample(sample_key="escape|0", repeat_index=1, reply="H0 = 68.3 km/s/Mpc.")
    _write_samples(samples_path, [_sample(), escape, STOP_FIXTURES["transport_failed"] | {"sample_key": "fail|0"}])
    render = tmp_path / "result.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "score",
            "--tasks",
            str(tasks_path),
            "--samples",
            str(samples_path),
            "--render-md",
            str(render),
        ],
    )
    scorer.main()
    scores_path = tmp_path / "C1_abc1234_scores.csv"
    summary_path = tmp_path / "C1_abc1234_summary.json"
    adjudication_path = tmp_path / "C1_abc1234_adjudication.csv"
    assert scores_path.exists() and summary_path.exists() and adjudication_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["tasks_sha256"] == hashlib.sha256(tasks_path.read_bytes()).hexdigest()
    assert [e["sample_key"] for e in summary["hard_escapes"]] == ["escape|0"]
    assert [t["sample_key"] for t in summary["transport_failures"]] == ["fail|0"]
    assert summary["decision"]["primary_n"] == 2
    assert "premature_stop_rate" not in summary
    text = render.read_text(encoding="utf-8")
    assert text.startswith("# Standard Astro v0.3 exploration result")
    assert "Premise reproduced: undetermined" not in text or summary["decision"]["premise_reproduced"] is None
    assert "Hard escapes (release blocker): 1" in text
    assert "## Strata (never merged)" in text

    with adjudication_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {r["sample_key"] for r in rows} == {_sample()["sample_key"], "escape|0", "fail|0"}
    assert all(r["user_premature_stop"] == "" and r["user_hypotheses"] == "" for r in rows)
    for row in rows:
        row["user_premature_stop"] = "no"
        row["user_hypotheses"] = "1"
    with adjudication_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "score",
            "--tasks",
            str(tasks_path),
            "--samples",
            str(samples_path),
            "--adjudicated",
            str(adjudication_path),
            "--render-md",
            str(render),
        ],
    )
    scorer.main()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    primary = summary["strata"]["flag_off|model_in_loop|open"]
    assert primary["premature_stop"]["count"] == 2  # rule verdict stays primary
    assert primary["adjudicated_premature_stop"]["count"] == 0
    assert primary["adjudicated_premature_stop"]["n"] == 2
    assert summary["decision"]["adjudicated_rate"] == 0.0
    assert "Adjudicated (secondary)" in render.read_text(encoding="utf-8")
    with scores_path.open(encoding="utf-8", newline="") as handle:
        scored = {r["sample_key"]: r for r in csv.DictReader(handle)}
    assert scored["escape|0"]["user_premature_stop"] == "False"
    assert scored["escape|0"]["premature_stop"] == "True"
