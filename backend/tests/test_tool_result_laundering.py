"""Same-turn tool-result laundering channels (2026-06-12, P1b items 1-2).

Two live-confirmed bypasses of the zero-fabrication gate, both closed here:

1. INPUT-ECHO: validate_claims used to harvest the whole accumulator entry
   including the model-authored ``input`` — echoing a fabricated number into
   any tool argument (a search query string, a parameter field) put it in
   the claim universe and validated the claim. Now the numeric universe is
   built from tool RESULTS only (_result_only_nodes, the numeric twin of the
   citation pool's B4 rule), nested "input" subtrees are blacklisted, and
   rendered report prose (markdown / paper_draft_markdown / bibtex) never
   grounds numbers.

2. SELF-SUPPLIED EVIDENCE: the render/verify research tools
   (export_research_report / verify_research_facts / build_evidence_graph)
   trusted model-supplied ``tool_results`` (and ``evidence_graph``)
   verbatim — a fabricated {publication_ready: true, H0: ...} payload became
   a user-facing report AND re-grounded its own numbers. Now the chat
   dispatcher always injects the turn's real accumulator as
   ``_turn_tool_results`` (stripping any model-supplied copy of that key for
   EVERY tool), and the executors treat it as the only admissible evidence,
   rebuilding the evidence graph from it.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services.ai_tools_research import (
    _exec_build_evidence_graph,
    _exec_export_research_report,
    _exec_verify_research_facts,
)
from app.services.claim_validator import validate_claims

FAKE_CHAIN = {
    "id": "fake_1", "tool": "run_cosmology_likelihood_chain", "input": {},
    "result": {
        "success": True, "publication_ready": True, "chain_tier": "publication",
        "model": "lcdm", "parameters": {"H0": {"median": 71.4}},
        "datasets_used": [{"key": "desi_dr1_bao"}],
    },
}
REAL_CHAIN = {
    "id": "real_1", "tool": "run_cosmology_likelihood_chain",
    "input": {"model": "lcdm"},
    "result": {
        "success": True, "publication_ready": True, "chain_tier": "publication",
        "model": "lcdm", "parameters": {"H0": {"median": 67.36}},
        "fit_statistics": {"chi2": 10.0, "n_parameters": 3},
        "datasets_used": [{"key": "desi_dr1_bao"}],
    },
}


# ── Channel 1: input echo ────────────────────────────────────────────────────

def test_query_string_echo_does_not_ground_claims():
    entry = {"id": "c1", "tool": "search_literature",
             "input": {"query": "Hubble constant H0 = 71.4 km/s/Mpc"},
             "result": {"results": [], "n_results": 0}}
    assert validate_claims("The fit prefers H0 = 71.4 km/s/Mpc.", [entry]).ok is False


def test_structured_input_does_not_defeat_label_aware_check():
    entry = {"id": "c2", "tool": "run_python",
             "input": {"H0": 71.4, "code": "print(1)"},
             "result": {"stdout": "1"}}
    assert validate_claims("Our analysis finds H0 = 71.4.", [entry]).ok is False


def test_nested_input_subtree_inside_a_result_is_skipped():
    # A result that embeds tool-call records (e.g. an export echoing the
    # turn's calls) must not leak their inputs into the universe.
    entry = {"id": "c3", "tool": "export_research_report", "input": {},
             "result": {"echoed": [{"tool": "x", "input": {"H0": 71.4}, "result": {}}]}}
    assert validate_claims("H0 = 71.4.", [entry]).ok is False


def test_result_numbers_still_ground_claims():
    assert validate_claims("H0 = 67.36.", [REAL_CHAIN]).ok is True


# ── Channel 1b: RESULT-side input echo (the 2026-06-12 review's blocker) ─────
# Several tools copy the model's own arguments into their RESULT body for UI /
# reproducibility, so stripping the accumulator `input` was not enough.

def test_run_adql_query_string_echo_does_not_ground():
    # run_adql -> result["query"] = the model-authored SQL string.
    entry = {"id": "q1", "tool": "run_adql",
             "input": {"query": "SELECT ... WHERE parallax > 71.4"},
             "result": {"query": "SELECT ... WHERE parallax > 71.4",
                        "row_count": 3, "data": {"x": [120.3, 135.7, 150.2]}}}
    assert validate_claims("The fit prefers H0 = 71.4.", [entry]).ok is False


def test_run_adql_sql_comment_marker_does_not_ground():
    entry = {"id": "q2", "tool": "run_adql", "input": {},
             "result": {"query": "SELECT 1 -- marker 64.37", "row_count": 1,
                        "data": {"x": [10.0]}}}
    assert validate_claims("H0 = 64.37.", [entry]).ok is False


def test_params_dict_echo_does_not_ground():
    entry = {"id": "p1", "tool": "query_high_velocity_stars", "input": {},
             "result": {"params": {"min_vtan_kms": 713, "min_parallax_mas": 0.33},
                        "data": {"vtan": [1590.18]}}}
    assert validate_claims("These stars move at v_tan = 713 km/s.", [entry]).ok is False


def test_scalar_center_echoes_do_not_ground_but_measured_aggregates_do():
    entry = {"id": "g1", "tool": "query_gaia_cluster", "input": {},
             "result": {"center_ra": 132.825, "radius_deg": 9.87,
                        "median_parallax_mas": 7.34, "row_count": 50}}
    # radius_deg / center_ra are echoed inputs — not citeable.
    assert validate_claims("velocity dispersion is 9.87 km/s.", [entry]).ok is False
    # median_parallax_mas is a MEASURED aggregate — still citeable.
    assert validate_claims("median parallax 7.34 mas.", [entry]).ok is True


def test_research_plan_reflection_does_not_ground():
    entry = {"id": "m1", "tool": "run_research_matrix", "input": {},
             "result": {"research_plan": {"H0_prior_center": 71.4, "target_H0": 71.4},
                        "matrix": [], "datasets_used": [{"key": "x"}]}}
    assert validate_claims("H0 = 71.4.", [entry]).ok is False


def test_deliverable_prose_as_list_or_dict_does_not_ground():
    # The freetext skip was value-type fragile (string only); the subtree skip
    # closes the list / nested-dict variants.
    entry = {"id": "d1", "tool": "export_research_report", "input": {},
             "result": {"markdown": ["## Findings", "H0 = 71.4 (publication-ready)"],
                        "paper_draft_markdown": {"abstract": "we find H0 = 71.4"}}}
    assert validate_claims("H0 = 71.4.", [entry]).ok is False


def test_input_hash_digits_do_not_enter_universe():
    entry = {"id": "h1", "tool": "build_evidence_graph", "input": {},
             "result": {"nodes": [{"input_hash": "44136fa355b3678a",
                                    "publication_ready": True}]}}
    universe = set(validate_claims("noop", [entry]).universe_sample)
    assert not ({355.0, 3678.0, 44136.0} & universe)


def test_labeled_universe_built_from_results_only():
    # The fabricated 71.4 sits in an input; the real 67.36 in a result. The
    # label-aware H0 bucket must contain only the result value.
    entries = [
        {"id": "a", "tool": "run_python", "input": {"H0": 71.4}, "result": {"stdout": ""}},
        REAL_CHAIN,
    ]
    assert validate_claims("H0 = 71.4.", entries).ok is False
    assert validate_claims("H0 = 67.36.", entries).ok is True


def test_report_prose_does_not_ground_numbers():
    # Rendered deliverables are a RENDERING of evidence, not evidence.
    entry = {"id": "e1", "tool": "export_research_report", "input": {},
             "result": {"markdown": "We find H0 = 71.4.",
                        "paper_draft_markdown": "Abstract: H0 = 71.4.",
                        "bibtex": "@article{x2026, note={71.4}}"}}
    assert validate_claims("H0 = 71.4.", [entry]).ok is False


# ── Channel 2: self-supplied evidence for render/verify tools ────────────────

def test_export_ignores_fabricated_tool_results_on_server_path():
    out = _exec_export_research_report({
        "tool_results": [FAKE_CHAIN],
        "_turn_tool_results": [REAL_CHAIN],
        "title": "T",
    })
    blob = (out.get("markdown") or "") + (out.get("paper_draft_markdown") or "")
    assert "71.4" not in blob
    assert "67.36" in blob
    assert out["tool_results_source"] == "server_turn_record"
    assert any("IGNORED" in w for w in out.get("warnings", []))


def test_export_ignores_fabricated_evidence_graph_on_server_path():
    out = _exec_export_research_report({
        "evidence_graph": {"nodes": [{"id": "n1", "value": 71.4}]},
        "_turn_tool_results": [REAL_CHAIN],
        "title": "T",
    })
    assert any("rebuilt" in w for w in out.get("warnings", []))


def test_export_library_path_stays_caller_supplied():
    out = _exec_export_research_report({"tool_results": [REAL_CHAIN], "title": "T"})
    assert out["tool_results_source"] == "caller_supplied"
    assert out.get("success") is True


def test_verify_and_graph_executors_use_server_record():
    v = _exec_verify_research_facts({
        "tool_results": [FAKE_CHAIN],
        "_turn_tool_results": [REAL_CHAIN],
        "final_reply": "H0 = 71.4.",
    })
    assert v["tool_results_source"] == "server_turn_record"
    g = _exec_build_evidence_graph({
        "tool_results": [FAKE_CHAIN],
        "_turn_tool_results": [REAL_CHAIN],
    })
    assert g["tool_results_source"] == "server_turn_record"
    import json
    assert "71.4" not in json.dumps(g.get("nodes", []))


def test_build_evidence_graph_is_in_the_dispatcher_injection_set():
    # Review #4/#5/#10/#14: build_evidence_graph was a model-callable tool left
    # OUT of the injection set, so a model-issued call ran on fabricated
    # tool_results. It must now receive the trusted accumulator like export/
    # verify. Assert the dispatcher source-injects it.
    import asyncio

    import app.services.ai_tools as ai_tools_mod
    from app.api import chat as chat_mod

    captured: dict[str, dict] = {}

    async def spy_execute_tool(tool_name, tool_input, *args, **kwargs):
        captured[tool_name] = dict(tool_input)
        return {"id": "x", "name": tool_name, "result": {"success": True}}

    monkeypatch_obj = pytest.MonkeyPatch()
    monkeypatch_obj.setattr(ai_tools_mod, "execute_tool", spy_execute_tool)
    try:
        calls = [{"id": "t1", "name": "build_evidence_graph",
                  "input": {"tool_results": [FAKE_CHAIN], "_turn_tool_results": [FAKE_CHAIN]}}]
        asyncio.run(chat_mod._execute_tool_calls(calls, "", {}, "s", turn_tool_results=[REAL_CHAIN]))
        injected = captured["build_evidence_graph"]["_turn_tool_results"]
        assert injected == [REAL_CHAIN]
    finally:
        monkeypatch_obj.undo()


def test_cross_turn_export_renders_but_marks_uncitable():
    # Review #13: empty server record (no tool ran THIS turn) + caller-supplied
    # prior-turn results must render the draft (not an empty report) BUT be
    # stamped __do_not_claim__ so its numbers cannot ground claims.
    out = _exec_export_research_report({
        "tool_results": [REAL_CHAIN], "_turn_tool_results": [], "title": "T",
    })
    assert out["tool_results_source"] == "caller_supplied_unverified"
    assert out["__do_not_claim__"] is True
    assert out["publication_ready"] is False
    assert len(out.get("markdown") or "") > 200  # a real draft, not "no results"
    # The unverified export's numbers do NOT ground a reply claim.
    v = validate_claims(
        "Our analysis finds H0 = 67.36.",
        [{"id": "ex", "tool": "export_research_report", "input": {}, "result": out}],
    )
    assert v.ok is False


def test_cross_turn_export_package_sizes_match_the_stamped_fields():
    # `_stamp_evidence_source` prepends the unverified-evidence banner to the
    # markdown fields AFTER export_research_report computed
    # report_package.files[].bytes, so on this path the package listed sizes
    # that were short by the banner's length — a manifest that disagrees with
    # the payload it describes. Every listed size must equal the byte length of
    # the field its source_key names, banner included.
    out = _exec_export_research_report({
        "tool_results": [REAL_CHAIN], "_turn_tool_results": [], "title": "T",
    })
    assert out["tool_results_source"] == "caller_supplied_unverified"
    assert out["markdown"].lstrip().startswith(">")  # the banner really is there
    files = out["report_package"]["files"]
    assert {f["source_key"] for f in files} >= {"markdown", "paper_draft_markdown"}
    for entry in files:
        value = out[entry["source_key"]]
        expected = (
            len(value.encode("utf-8"))
            if isinstance(value, str)
            else len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        )
        assert entry["bytes"] == expected, entry["path"]


def test_empty_turn_export_with_no_supplied_data_is_honest_empty():
    out = _exec_export_research_report({"_turn_tool_results": [], "title": "T"})
    assert out["tool_results_source"] == "server_turn_record"


# ── Channel 1c: cosmology-manifest echo (round-2 review #0/#2) ───────────────
# compare_luminosity_distances / fit_line_lfr echo the ASSUMED cosmology into
# their result. A model-authored legacy spec ("FlatLambdaCDM_H73p8_Om0p295")
# round-trips the model's own digits with bibcode=None; a curated preset
# carries a real bibcode and stays citeable as provenance.

def test_legacy_spec_manifest_does_not_ground():
    entry = {"id": "cmp", "tool": "compare_luminosity_distances", "input": {},
             "result": {"target_cosmology": {"H0_km_s_Mpc": 73.8, "Om0": 0.295,
                                              "bibcode": None},
                        "data": {"ratio": [1.02, 1.05]}}}
    assert validate_claims("We adopt H0 = 73.8 km/s/Mpc.", [entry]).ok is False
    assert validate_claims("Omega_m = 0.295.", [entry]).ok is False


def test_curated_preset_manifest_with_bibcode_still_grounds():
    entry = {"id": "cmp2", "tool": "compare_luminosity_distances", "input": {},
             "result": {"target_cosmology": {"H0_km_s_Mpc": 67.36, "Om0": 0.3153,
                                             "bibcode": "2020A&A...641A...6P"},
                        "data": {}}}
    assert validate_claims("Assuming Planck18 (H0 = 67.36).", [entry]).ok is True


def test_fit_line_lfr_manifest_echo_does_not_ground_but_fit_outputs_do():
    entry = {"id": "lfr", "tool": "fit_line_lfr", "input": {},
             "result": {"cosmology_manifest": {"H0_km_s_Mpc": 42.0, "Om0": 0.999,
                                               "bibcode": None},
                        "beta": 0.8, "alpha": 8.35}}
    assert validate_claims("H0 = 42.0.", [entry]).ok is False
    assert validate_claims("beta = 0.8.", [entry]).ok is True


# ── Bare-dict envelope hardening (round-2 review #3) ─────────────────────────

def test_result_none_sibling_echo_does_not_leak():
    # A malformed envelope with result=None and a top-level echoed sibling must
    # not leak that sibling's number (only the result subtree is evidence).
    entry = {"id": "c2", "tool": "fit_cosmology", "result": None, "H0_guess": 73.8}
    assert validate_claims("H0 = 73.8.", [entry]).ok is False


# ── verify_research_facts under the unverified path (round-2 review #5) ───────

def test_verify_unverified_path_does_not_certify():
    # Empty server record + caller-supplied fabricated data must NOT yield a
    # "verified" verdict (even though __do_not_claim__ already blocks grounding).
    out = _exec_verify_research_facts({
        "tool_results": [FAKE_CHAIN], "_turn_tool_results": [],
        "final_reply": "H0 = 71.4 verified.",
    })
    assert out["status"] == "not_verifiable_this_turn"
    assert out["__do_not_claim__"] is True
    assert out["verified_claim_count"] == 0


# ── Label-pin (round-1 guarantee must hold; the reverted user_prompt must not
#    reopen it) ─────────────────────────────────────────────────────────────

def test_produced_parameter_value_cannot_be_overridden():
    real = [{"id": "r", "tool": "run_cosmology_likelihood_chain", "input": {},
             "result": {"parameters": {"H0": {"median": 67.4}}}}]
    assert validate_claims("Our chain yields H0 = 73.04 km/s/Mpc.", real).ok is False


# ── Structural anti-echo: subtract model-authored input numbers (round-3) ────
# Instead of chasing echo KEYS one at a time, any number the model put in a
# tool INPUT is removed from the universe wherever a tool echoes it.

def test_audit_published_constraint_claimed_echo_blocked():
    # Review #5 (BLOCKER): the model fully controls `claimed`; the tool echoes
    # it into the result on the COMPLETED path.
    entry = {"id": "ap", "tool": "audit_published_constraint",
             "input": {"param": "H0", "claimed": [71.4, 1.0]},
             "result": {"param": "H0", "claimed_value": 71.4, "claimed_sigma": 1.0,
                        "status": "COMPLETED", "reproduced": [67.4]}}
    assert validate_claims("The published value is H0 = 71.4.", [entry]).ok is False
    # A genuinely reproduced value (NOT in the input) still grounds.
    assert validate_claims("We reproduce H0 = 67.4.", [entry]).ok is True


def test_sensitivity_analysis_base_value_echo_blocked():
    entry = {"id": "se", "tool": "sensitivity_analysis",
             "input": {"parameter": "H0", "base_value": 71.4},
             "result": {"parameter": "H0", "base_value": 71.4,
                        "results": [{"perturbation": 0.0, "value": 71.4}]}}
    assert validate_claims("Central value H0 = 71.4.", [entry]).ok is False


def test_assess_bao_bin_anomaly_grid_echo_blocked():
    entry = {"id": "ba", "tool": "assess_bao_bin_anomaly",
             "input": {"omega_m_grid": [0.295, 0.305, 10]},
             "result": {"provenance": {"alcock_paczynski": {
                 "omega_m_grid_min_max": [0.295, 0.305], "omega_m_grid_resolution": 10}},
                 "data": [1.0]}}
    assert validate_claims("Omega_m = 0.295.", [entry]).ok is False


def test_genuine_fit_output_coinciding_with_a_code_literal_still_grounds():
    # Round-4 false-positive fix: the input harvest skips the run_python `code`
    # string (and config keys), so a genuine result that numerically equals a
    # code literal (bins=20 -> a real peak count of 20; p0=[70.0] -> a fit that
    # recovers H0=70.0) is NOT wrongly subtracted.
    hist = {"id": "h", "tool": "run_python",
            "input": {"code": "counts,_=np.histogram(z, bins=20)\nprint(counts.max())",
                      "data_source": "latest_adql"},
            "result": {"stdout": "20", "variables": {"peak_count": 20}, "success": True}}
    assert validate_claims("The densest redshift bin contains 20 galaxies.", [hist]).ok is True
    fit = {"id": "f2", "tool": "run_python",
           "input": {"code": "p0=[70.0,0.30]\npopt,_=curve_fit(m,z,mu,p0=p0)\nprint(popt)",
                     "data_source": "latest_adql"},
           "result": {"stdout": "[70.0 0.315]", "variables": {"H0": 70.0}, "success": True}}
    assert validate_claims("The fit converges to H0 = 70.0 km/s/Mpc.", [fit]).ok is True


def test_structural_subtract_closes_arbitrary_echo_key():
    # Even an UNFORESEEN echo key is covered: the number is in the input, so it
    # is subtracted regardless of the result key name.
    entry = {"id": "x", "tool": "some_future_tool",
             "input": {"my_weird_param": 88.8},
             "result": {"an_unforeseen_echo_key": 88.8, "data": [1.0]}}
    # 88.8 is subtracted (it is a model input), so it is NOT in the universe.
    r = validate_claims("H0 = 88.8 km/s/Mpc.", [entry])
    assert r.ok is False
    assert 88.8 not in set(r.universe_sample)


# ── Anchor-gate consistency + fabricated bibcode (round-3 #1, #4/#8) ─────────

def test_anchor_gate_agrees_with_universe_on_legacy_manifest():
    from app.services.claim_validator import value_supported_by_cosmology_manifest

    legacy = [{"id": "d", "tool": "fit_line_lfr",
               "input": {"cosmology": "FlatLambdaCDM_H73p8_Om0p295"},
               "result": {"cosmology_manifest": {"H0_km_s_Mpc": 73.8, "Om0": 0.295,
                                                 "bibcode": None}, "beta": 0.8}}]
    assert validate_claims("H0 = 73.8.", legacy).ok is False
    assert value_supported_by_cosmology_manifest(73.8, legacy) is False  # gates agree


def test_anchor_gate_ignores_input_manifest():
    from app.services.claim_validator import value_supported_by_cosmology_manifest

    entry = [{"id": "e", "tool": "x",
              "input": {"cosmology_manifest": {"H0_km_s_Mpc": 71.4}},
              "result": {"beta": 0.8}}]
    assert value_supported_by_cosmology_manifest(71.4, entry) is False


def test_fabricated_bibcode_does_not_rescue_manifest():
    # A truthy but non-19-char bibcode marker must not make a legacy manifest
    # citeable.
    fab = [{"id": "g", "tool": "fit_line_lfr", "input": {},
            "result": {"cosmology_manifest": {"H0_km_s_Mpc": 73.8, "Om0": 0.295,
                                              "bibcode": "2099XXXX...FAKE"}, "beta": 0.8}}]
    assert validate_claims("H0 = 73.8.", fab).ok is False


def test_list_wrapped_legacy_manifest_blocked():
    lst = [{"id": "h", "tool": "compare_luminosity_distances", "input": {},
            "result": {"target_cosmology": [{"H0_km_s_Mpc": 73.8, "bibcode": None}],
                       "data": {}}}]
    assert validate_claims("H0 = 73.8.", lst).ok is False


def test_unverified_export_carries_loud_banner():
    out = _exec_export_research_report({
        "tool_results": [REAL_CHAIN], "_turn_tool_results": [], "title": "T",
    })
    assert "UNVERIFIED DRAFT" in (out.get("markdown") or "")


# ── Derived-number bypasses (round-4: tools that COMPUTE from model inputs) ──

def test_sensitivity_analysis_derived_value_does_not_ground():
    import asyncio

    from app.services.ai_tools import _exec_sensitivity_analysis

    out = asyncio.run(_exec_sensitivity_analysis(
        {"parameter": "H0", "base_value": 67.0, "perturbations": [0.1], "code": "result=1"}))
    assert out["__do_not_claim__"] is True
    entry = {"id": "s", "tool": "sensitivity_analysis",
             "input": {"parameter": "H0", "base_value": 67.0, "perturbations": [0.1]},
             "result": out}
    # 67.0 * 1.1 = 73.7 is a model-chosen what-if value, not a measurement.
    assert validate_claims("Our pipeline yields H0 = 73.7 km/s/Mpc.", [entry]).ok is False


def test_audit_tension_sigma_derived_from_unvalidated_claim_does_not_ground():
    entry = {"id": "a", "tool": "audit_published_constraint",
             "input": {"param": "H0", "claimed": [71.4, 1.0]},
             "result": {"audit_report": {"comparisons": [
                 {"param": "H0", "claimed": [71.4, 1.0],
                  "reproduced": [67.4, 0.5], "tension_sigma": 3.9}]},
                 "analysis_status": "AUDIT_READY"}}
    # The model-supplied published value and the tension derived from it are
    # not measurements; the platform's reproduced value is.
    assert validate_claims("This is a 3.9 sigma tension.", [entry]).ok is False
    assert validate_claims("The published value is H0 = 71.4.", [entry]).ok is False
    assert validate_claims("We reproduce H0 = 67.4.", [entry]).ok is True


def test_export_result_no_longer_regrounds_fabricated_numbers():
    # Even on the trusted library path, a deliverable rendered from tainted
    # inputs must not re-ground its numbers via the result text.
    out = _exec_export_research_report({"tool_results": [FAKE_CHAIN], "title": "T"})
    v = validate_claims(
        "Our analysis finds H0 = 71.4 at publication tier.",
        [{"id": "e1", "tool": "export_research_report", "input": {}, "result": out}],
    )
    assert v.ok is False


# ── Dispatcher injection layer ───────────────────────────────────────────────

def test_chat_dispatcher_injects_and_strips_turn_record(monkeypatch):
    import app.services.ai_tools as ai_tools_mod
    from app.api import chat as chat_mod

    captured: dict[str, dict] = {}

    async def spy_execute_tool(tool_name, tool_input, *args, **kwargs):
        captured[tool_name] = dict(tool_input)
        return {"id": "x", "name": tool_name, "result": {"success": True}}

    monkeypatch.setattr(ai_tools_mod, "execute_tool", spy_execute_tool)

    real_record = [REAL_CHAIN]
    calls = [
        # Model tries to smuggle the trusted key into an arbitrary tool.
        {"id": "t1", "name": "list_cosmology_datasets",
         "input": {"_turn_tool_results": [FAKE_CHAIN]}},
        # Render tool gets the REAL record regardless of what the model sent.
        {"id": "t2", "name": "export_research_report",
         "input": {"tool_results": [FAKE_CHAIN], "_turn_tool_results": [FAKE_CHAIN]}},
    ]
    results = asyncio.run(chat_mod._execute_tool_calls(
        calls, "", {}, "test-session", turn_tool_results=real_record,
    ))
    assert len(results) == 2
    assert "_turn_tool_results" not in captured["list_cosmology_datasets"]
    injected = captured["export_research_report"]["_turn_tool_results"]
    assert injected == real_record
    assert injected[0]["result"]["parameters"]["H0"]["median"] == pytest.approx(67.36)