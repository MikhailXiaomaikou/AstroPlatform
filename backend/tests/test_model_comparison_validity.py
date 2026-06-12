"""compute_model_comparison validity guards + research-matrix comparison flow.

History:
- 2026-06-11: cross-representation guard. planck2018_compressed is a
  model-DEPENDENT compressed representation: extended flat-DE chains swap its
  diagonal ΛCDM posterior summary for the Chen-Huang-Wang (R, l_A, ombh2)
  distance prior, which adds an ombh2 sampled axis. An lcdm-vs-wcdm pair on
  that selection compares chi2 against two DIFFERENT likelihoods — the deltas
  are not a model comparison.
- 2026-06-12 (user decision): the phase-1 matrix gate was OPENED for flat-DE
  extensions (wcdm/w0wa_cdm run numerically via the emcee upgrade) while
  curvature/neutrino-mass branches stay config_only and point to the CMB
  path. Opening the gate exposed a second validity hole: a chain_tier=
  'blocked' fit (ESS collapse) used to feed a confident preferred-model
  verdict. compute_model_comparison now fails closed on blocked inputs too.

All guards keep the factual deltas reported (they are real numbers from real
fits); only the preference verdict is withheld.
"""
from __future__ import annotations

from app.services.cosmology_likelihoods import (
    compute_model_comparison,
    run_likelihood_chain,
)


def test_cross_representation_pair_is_flagged_invalid():
    ds = ["desi_dr1_bao", "planck2018_compressed"]
    lcdm = run_likelihood_chain(model="lcdm", dataset_keys=ds, n_samples=400, random_seed=42)
    # The emcee upgrade keeps this fit out of blocked tier so the test
    # exercises the REPRESENTATION guard, not the chain-tier guard.
    wcdm = run_likelihood_chain(
        model="wcdm", dataset_keys=ds, n_samples=1500, random_seed=42,
        allow_emcee_fallback=True,
    )
    # Preconditions for the test to be meaningful: the wcdm chain really does
    # sample the extra ombh2 axis (distance-prior representation) and is not
    # blocked.
    assert "ombh2" in (wcdm.get("parameters") or {})
    assert "ombh2" not in (lcdm.get("parameters") or {})
    assert wcdm["chain_tier"] != "blocked"
    assert lcdm["chain_tier"] != "blocked"

    cmp = compute_model_comparison(lcdm, wcdm)
    assert cmp["comparison_valid"] is False
    assert cmp["preferred"] == "undetermined"
    assert "ombh2" in cmp["comparison_warning"]
    # The factual deltas stay reported (they are real numbers from real fits)
    # but the dict is tainted so they can never support a reply claim.
    assert cmp["delta_chi2"] is not None
    assert cmp["__do_not_claim__"] is True


def test_same_likelihood_pair_is_valid_with_one_extra_param():
    # DESI BAO + cosmic chronometers are model-INVARIANT representations (same
    # data vectors for lcdm and wcdm). Both chains take the emcee upgrade —
    # importance proposals collapse on this combo (lcdm too), and the validity
    # guard rightly refuses blocked inputs.
    ds = ["desi_dr1_bao", "cosmic_chronometers"]
    lcdm = run_likelihood_chain(
        model="lcdm", dataset_keys=ds, n_samples=1500, random_seed=42,
        allow_emcee_fallback=True,
    )
    wcdm = run_likelihood_chain(
        model="wcdm", dataset_keys=ds, n_samples=1500, random_seed=42,
        allow_emcee_fallback=True,
    )
    assert lcdm["chain_tier"] != "blocked"
    assert wcdm["chain_tier"] != "blocked"
    cmp = compute_model_comparison(lcdm, wcdm)
    assert cmp["comparison_valid"] is True
    assert "comparison_warning" not in cmp
    assert "__do_not_claim__" not in cmp
    assert cmp["n_extra_params"] == 1
    assert cmp["preferred"] in {"lcdm", "inconclusive"}
    # Tier transparency: the comparison carries both chain tiers, and an
    # exploratory input (the NORMAL case for in-process extended models, which
    # are all off-anchor today) forces a rendered caveat.
    assert cmp["baseline_chain_tier"] in {"publication", "exploratory"}
    assert cmp["extended_chain_tier"] in {"publication", "exploratory"}
    if "exploratory" in {cmp["baseline_chain_tier"], cmp["extended_chain_tier"]}:
        assert "exploratory-tier" in cmp["verdict_caveat"]


def test_blocked_tier_input_invalidates_comparison():
    """A wcdm importance fit on DESI-only collapses to ESS ~40 → blocked.

    Before 2026-06-12 this exact pair fed a confident comparison_valid=True
    verdict (the old version of the test above asserted it!) — chi2 from a
    one-effective-sample chain is noise, not evidence."""
    ds = ["desi_dr1_bao"]
    lcdm = run_likelihood_chain(model="lcdm", dataset_keys=ds, n_samples=400, random_seed=42)
    wcdm = run_likelihood_chain(model="wcdm", dataset_keys=ds, n_samples=400, random_seed=42)
    assert lcdm["chain_tier"] == "publication"
    assert wcdm["chain_tier"] == "blocked"  # precondition: ESS collapse is real

    cmp = compute_model_comparison(lcdm, wcdm)
    assert cmp["comparison_valid"] is False
    assert cmp["preferred"] == "undetermined"
    assert "blocked" in cmp["comparison_warning"]
    # Factual deltas still reported, but tainted: noise from a
    # one-effective-sample chain must never support a reply claim.
    assert cmp["delta_chi2"] is not None
    assert cmp["__do_not_claim__"] is True


def test_ess_unknown_input_invalidates_comparison():
    """A chain whose convergence cannot be verified (ess_source=
    autocorr_failed → ESS None) passes the blocked-tier check at exploratory
    tier but its best-fit chi2 has no numerical guarantee — the 2026-06-12
    review showed it could feed a confident preference verdict."""
    ds = ["desi_dr1_bao"]
    lcdm = run_likelihood_chain(model="lcdm", dataset_keys=ds, n_samples=400, random_seed=42)
    degraded = dict(lcdm)
    degraded["model"] = "wcdm"
    degraded["chain_tier"] = "exploratory"
    degraded["chain_diagnostics"] = {"proposal_ess": None, "ess_bulk": None,
                                     "ess_source": "autocorr_failed"}
    cmp = compute_model_comparison(lcdm, degraded)
    assert cmp["comparison_valid"] is False
    assert "convergence is unverified" in cmp["comparison_warning"]
    assert cmp["__do_not_claim__"] is True


def test_unknown_chain_tier_input_invalidates_comparison():
    """Fail closed on inputs that never went through the tier system at all
    (the guard used to fail OPEN on a missing chain_tier key)."""
    ds = ["desi_dr1_bao"]
    lcdm = run_likelihood_chain(model="lcdm", dataset_keys=ds, n_samples=400, random_seed=42)
    untiered = {k: v for k, v in lcdm.items() if k != "chain_tier"}
    untiered["model"] = "wcdm"
    cmp = compute_model_comparison(lcdm, untiered)
    assert cmp["comparison_valid"] is False
    assert "unvetted" in cmp["comparison_warning"]
    assert cmp["__do_not_claim__"] is True


def test_research_matrix_runs_flat_de_cells_with_comparison_discipline():
    """E2E contract (2026-06-12, replaces the phase-1 keeps-comparisons-empty
    pin after the user decision to open the gate): run_research_matrix now
    EXECUTES flat-DE extension cells (wcdm/w0wa_cdm) via the emcee upgrade,
    so model_comparisons reaches the LLM — and therefore the matrix
    __message_to_model__ MUST carry the invalid-comparison rendering
    discipline (never present comparison_valid=false deltas as
    model-preference evidence)."""
    from app.services.research_program import run_research_matrix

    m = run_research_matrix(
        dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
        models=["lcdm", "wcdm"],
        random_seed=42,
        n_samples=400,
    )
    wcdm_cells = [c for c in m["matrix"] if c.get("model") == "wcdm"]
    assert wcdm_cells, "matrix must still build the requested wcdm branch cell"
    for cell in wcdm_cells:
        # The branch cell is numerically RUN now: it carries a real result
        # with fit_statistics instead of a config_only stub.
        assert cell["execution_level"] != "config_only"
        assert isinstance(cell.get("result"), dict)
        assert isinstance(cell["result"].get("fit_statistics"), dict)
    assert m["model_comparisons"], "comparisons must fire for the matched-dataset pair"
    # desi+planck is the cross-representation (or blocked-tier, at low sample
    # counts) pair — either validity guard must withhold the verdict AND taint
    # the deltas out of the claimable universe.
    for comparison in m["model_comparisons"]:
        assert comparison["comparison_valid"] is False
        assert comparison["preferred"] == "undetermined"
        assert comparison["__do_not_claim__"] is True
    assert "comparison_valid=true" in m["__message_to_model__"]
    assert "verdict_caveat" in m["__message_to_model__"]
    assert any("not" in w and "model-preference" in w for w in m["warnings"])


def test_research_matrix_anchor_flag_lands_on_matching_combo():
    """2-probe selections: the full union coincides with a stock combo
    (BAO + SN), so the anchor flag must land on THAT cell and give it the
    emcee upgrade — without it the canonical baseline ran importance-only,
    landed blocked on a bad seed, and every comparison was invalidated."""
    from app.services.research_program import _proposed_experiment_matrix

    matrix = _proposed_experiment_matrix(
        ["desi_dr1_bao", "union3"], ["lcdm", "wcdm"], "compare lcdm and wcdm"
    )
    union = tuple(sorted(["desi_dr1_bao", "union3"]))
    anchors = [c for c in matrix if c.get("comparison_anchor")]
    assert len(anchors) == 1
    assert tuple(sorted(anchors[0]["dataset_keys"])) == union
    # No duplicate full-union lcdm cell was appended.
    lcdm_union_cells = [
        c for c in matrix
        if c.get("model") == "lcdm" and tuple(sorted(c["dataset_keys"])) == union
    ]
    assert len(lcdm_union_cells) == 1
    # Branch cells lead the matrix so chart truncation never hides them.
    assert matrix[0].get("requested_model_branch") or matrix[0].get("comparison_anchor")


def test_research_matrix_caps_duplicate_and_emcee_cells():
    """The proposed_experiment_matrix can come verbatim from an LLM-authored
    research_plan: duplicate (model, datasets) cells must be skipped and
    emcee-eligible cells capped, with both caps reported in warnings."""
    from app.services.research_program import run_research_matrix

    plan = {
        "research_question": "stress the matrix budgets",
        "candidate_dataset_keys": ["desi_dr1_bao", "cosmic_chronometers"],
        "model_families": ["lcdm", "wcdm"],
        "proposed_experiment_matrix": [
            {"label": "lcdm base", "dataset_keys": ["desi_dr1_bao", "cosmic_chronometers"], "model": "lcdm"},
            # 4 identical wcdm cells: only the first may run.
            *[
                {"label": f"wcdm dup {i}", "dataset_keys": ["desi_dr1_bao", "cosmic_chronometers"], "model": "wcdm"}
                for i in range(4)
            ],
            # Distinct wcdm cells to exhaust the emcee budget (3).
            {"label": "wcdm bao", "dataset_keys": ["desi_dr1_bao"], "model": "wcdm"},
            {"label": "wcdm cc", "dataset_keys": ["cosmic_chronometers"], "model": "wcdm"},
            {"label": "wcdm union3", "dataset_keys": ["union3"], "model": "wcdm"},
        ],
    }
    m = run_research_matrix(research_plan=plan, random_seed=42, n_samples=400)
    wcdm_cells = [c for c in m["matrix"] if c.get("model") == "wcdm"]
    # 4 duplicates collapsed into 1 + the 3 distinct cells.
    assert len(wcdm_cells) == 4
    assert any("duplicate matrix cell" in w for w in m["warnings"])
    # Emcee budget. The lcdm cell shares the wcdm-branch union, so the
    # executor DERIVES its anchor role and it consumes an emcee slot; of the
    # 4 surviving wcdm cells, 2 then run importance-only with the budget
    # warning, and the cap is also reported top-level.
    budget_warned = [
        c for c in wcdm_cells
        if any("emcee budget" in str(w) for w in c.get("warnings", []))
    ]
    assert len(budget_warned) == 2
    assert any("emcee budget was reached" in w for w in m["warnings"])


def test_research_matrix_derives_anchor_for_caller_supplied_plans():
    """Caller/LLM-supplied matrices carry no comparison_anchor flag; the
    executor must derive it (lcdm cell sharing an extended cell's dataset
    union) or the seed-lottery blocked-baseline failure the anchor was built
    to kill comes straight back through the research_plan door."""
    from unittest.mock import patch

    from app.services import research_program as rp

    calls: list[tuple[str, bool]] = []
    real_run = rp.run_likelihood_chain

    def spy(**kwargs):
        calls.append((kwargs["model"], kwargs.get("allow_emcee_fallback", False)))
        return real_run(**kwargs)

    plan = {
        "research_question": "caller-supplied comparison pair",
        "candidate_dataset_keys": ["desi_dr1_bao", "cosmic_chronometers"],
        "model_families": ["lcdm", "wcdm"],
        "proposed_experiment_matrix": [
            {"label": "lcdm union", "dataset_keys": ["desi_dr1_bao", "cosmic_chronometers"], "model": "lcdm"},
            {"label": "wcdm union", "dataset_keys": ["cosmic_chronometers", "desi_dr1_bao"], "model": "wcdm"},
        ],
    }
    with patch.object(rp, "run_likelihood_chain", side_effect=spy):
        rp.run_research_matrix(research_plan=plan, random_seed=42, n_samples=400)
    lcdm_calls = [fb for model, fb in calls if model == "lcdm"]
    assert lcdm_calls == [True]  # derived anchor → emcee upgrade allowed


def test_research_matrix_curvature_mnu_cells_stay_config_only():
    """The 2026-06-12 user decision opened ONLY flat-DE extensions. Curvature
    and neutrino-mass branches never sample their extension axis in-process
    (running them would relabel a ΛCDM-shaped chain), so their matrix cells
    stay config_only and point to the CMB likelihood path."""
    from app.services.research_program import run_research_matrix

    m = run_research_matrix(
        dataset_keys=["desi_dr1_bao"],
        models=["lcdm", "ok_lcdm", "lcdm_mnu"],
        random_seed=42,
        n_samples=400,
    )
    gated = [c for c in m["matrix"] if c.get("model") in {"ok_lcdm", "lcdm_mnu"}]
    assert len(gated) == 2
    for cell in gated:
        assert cell["execution_level"] == "config_only"
        assert cell["runnable"] is False
        assert not isinstance(cell.get("result"), dict)
        assert any(
            "run_cosmology_likelihood_chain" in str(w) and "EXTERNAL_COBAYA_ENABLED" in str(w)
            for w in cell.get("warnings", [])
        )
    # No extended fit ran → no comparisons from these branches.
    assert m["model_comparisons"] == []
