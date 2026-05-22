"""M6 acceptance: claim_validator.methodology_consistency_violations.

Core contracts:
1. AI reply contains 'Bayesian'-type promise but fit_line_lfr actually ran OLS → method_mismatch
2. AI reply contains 'demagnified N sources' but actual tool count < N → demagnify_count_mismatch
3. When AI makes no promise, no violation is reported regardless of fit_method (avoids false positives)
4. AI promises Bayesian + fit_method is actually 'bayesian_xyerr_linmix' → no violation
5. SYSTEM_PROMPT has the line-relation methodology segment added — simple existence check
"""

from app.services.claim_validator import methodology_consistency_violations


def _fit_lfr_result(
    fit_method: str,
    lensed_demag: int = 0,
    *,
    publication_ready: bool = True,
) -> dict:
    """Build a minimal claimable fit_line_lfr tool_result envelope."""
    return {
        "tool": "fit_line_lfr",
        "result": {
            "tool": "fit_line_lfr",
            "success": True,
            "fit_method": fit_method,
            "lensed_sources_demagnified": lensed_demag,
            "publication_ready": publication_ready,
            **({} if publication_ready else {
                "__tool_status__": "PARTIAL",
                "__do_not_claim__": True,
            }),
            "n_used": 50,
            "alpha": 8.0, "beta": 1.5,
        },
    }


def _demagnify_result(n: int) -> dict:
    return {
        "tool": "demagnify_sample",
        "result": {
            "tool": "demagnify_sample",
            "success": True,
            "n_demagnified": n,
            "n_input_rows": 60,
        },
    }


# ── Test 1: Bayesian promise + OLS reality → method_mismatch ──────────

def test_bayesian_promise_with_ols_result_triggers_mismatch():
    reply = "We performed Bayesian linear regression on the sample and recovered β=1.5."
    tool_results = [_fit_lfr_result("ols")]
    violations = methodology_consistency_violations(reply, tool_results)
    assert len(violations) == 1
    assert violations[0].kind == "method_mismatch"
    assert "bayesian" in violations[0].match_text.lower()


def test_linmix_promise_with_ols_result_triggers_mismatch():
    reply = "Using linmix we get β=1.5 ± 0.1."
    tool_results = [_fit_lfr_result("ols")]
    violations = methodology_consistency_violations(reply, tool_results)
    assert len(violations) == 1
    assert violations[0].kind == "method_mismatch"


def test_two_axis_errors_promise_with_ols_triggers_mismatch():
    reply = "The fit accounts for errors in both x and y."
    tool_results = [_fit_lfr_result("ols")]
    violations = methodology_consistency_violations(reply, tool_results)
    assert any(v.kind == "method_mismatch" for v in violations)


def test_kelly_2007_promise_with_ols_triggers_mismatch():
    reply = "The slope was estimated using the Kelly 2007 likelihood."
    tool_results = [_fit_lfr_result("ols")]
    violations = methodology_consistency_violations(reply, tool_results)
    assert any(v.kind == "method_mismatch" for v in violations)


# ── Test 2: Bayesian promise + Bayesian reality → no violation ────────

def test_bayesian_promise_with_bayesian_result_passes():
    reply = "We performed Bayesian regression with errors in both axes."
    tool_results = [_fit_lfr_result("bayesian_xyerr_linmix")]
    violations = methodology_consistency_violations(reply, tool_results)
    assert violations == []


def test_mixed_runs_one_bayesian_passes():
    """Even if some runs were OLS, having at least one Bayesian satisfies the promise."""
    reply = "We compared OLS and Bayesian linmix fits."
    tool_results = [
        _fit_lfr_result("ols"),
        _fit_lfr_result("bayesian_xyerr_linmix"),
    ]
    violations = methodology_consistency_violations(reply, tool_results)
    assert violations == []


# ── Test 3: no promise → no violation ─────────────────────────────────

def test_no_methodology_words_no_violation():
    reply = "The slope is β=1.5 ± 0.1 from a fit on 50 sources."
    tool_results = [_fit_lfr_result("ols")]
    violations = methodology_consistency_violations(reply, tool_results)
    assert violations == []


def test_only_ols_word_no_violation():
    reply = "We ran OLS and got β=1.5."
    tool_results = [_fit_lfr_result("ols")]
    violations = methodology_consistency_violations(reply, tool_results)
    assert violations == []


# ── Test 4: demagnify count mismatch ──────────────────────────────────

def test_overstated_demagnify_count_triggers_mismatch():
    reply = "We demagnified 30 sources before fitting."
    tool_results = [
        _fit_lfr_result("ols", lensed_demag=10),
        _demagnify_result(10),
    ]
    violations = methodology_consistency_violations(reply, tool_results)
    assert any(v.kind == "demagnify_count_mismatch" for v in violations)


def test_correct_demagnify_count_no_violation():
    reply = "We demagnified 10 sources before fitting."
    tool_results = [_demagnify_result(10)]
    violations = methodology_consistency_violations(reply, tool_results)
    assert all(v.kind != "demagnify_count_mismatch" for v in violations)


def test_understated_demagnify_count_no_violation():
    """Claiming fewer than actual is not a violation (still truthful)."""
    reply = "We demagnified 5 sources before fitting."
    tool_results = [_demagnify_result(10)]
    violations = methodology_consistency_violations(reply, tool_results)
    assert all(v.kind != "demagnify_count_mismatch" for v in violations)


def test_partial_line_relation_stats_must_be_labeled_exploratory():
    reply = "The slope is beta = 0.79 and intrinsic scatter is 0.32 dex."
    tool_results = [_fit_lfr_result("bayesian_xyerr_linmix", publication_ready=False)]
    violations = methodology_consistency_violations(reply, tool_results)
    assert any(v.kind == "line_relation_exploratory_label_missing" for v in violations)


def test_partial_line_relation_stats_pass_when_labeled_exploratory():
    reply = (
        "Exploratory only; not publication-ready: the slope is beta = 0.79 "
        "and intrinsic scatter is 0.32 dex."
    )
    tool_results = [_fit_lfr_result("bayesian_xyerr_linmix", publication_ready=False)]
    assert methodology_consistency_violations(reply, tool_results) == []


def test_partial_line_relation_stats_pass_with_publication_ready_scope_caveat():
    reply = (
        "The slope is beta = 0.79 and intrinsic scatter is 0.32 dex. "
        "This is not as a publication-ready statement about the global high-z [CII] LFR."
    )
    tool_results = [_fit_lfr_result("bayesian_xyerr_linmix", publication_ready=False)]
    assert methodology_consistency_violations(reply, tool_results) == []


def test_publication_ready_true_claim_requires_top_level_ready_fit():
    reply = "The Bayesian chain returned publication_ready=true, so the slope is final."
    tool_results = [_fit_lfr_result("bayesian_xyerr_linmix", publication_ready=False)]
    violations = methodology_consistency_violations(reply, tool_results)
    assert any(v.kind == "publication_ready_mismatch" for v in violations)


def test_compressed_chain_does_not_support_full_likelihood_ready_claim():
    reply = "All registered anchors are ready for both compressed and full likelihood analyses."
    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "success": True,
            "publication_ready": True,
            "claim_scope": "compressed_likelihood_preliminary",
            "sampler": "compressed_gaussian_analytic",
        },
    }]

    violations = methodology_consistency_violations(reply, tool_results)

    assert any(v.kind == "full_likelihood_overclaim" for v in violations)


def test_compressed_chain_scope_caveat_does_not_trigger_full_likelihood_claim():
    reply = (
        "This compressed-likelihood result is not a full external likelihood "
        "reproduction; full likelihood analyses still require external chains."
    )
    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "success": True,
            "publication_ready": True,
            "claim_scope": "compressed_likelihood_preliminary",
            "sampler": "compressed_gaussian_analytic",
        },
    }]

    assert methodology_consistency_violations(reply, tool_results) == []


def test_external_likelihood_config_ready_is_not_full_posterior_overclaim():
    reply = (
        "The DES-SN workflow is ready for external Cobaya/CosmoSIS execution, "
        "but posterior constraints require running the full external likelihood."
    )
    tool_results = [{
        "tool": "build_cosmology_likelihood",
        "result": {
            "success": True,
            "status": "CONFIG_READY",
            "datasets_used": [{"execution_mode": "external_cobaya"}],
        },
    }]

    assert methodology_consistency_violations(reply, tool_results) == []


def test_no_publication_ready_external_likelihood_heading_is_not_overclaim():
    reply = (
        "**No Publication-Ready External Likelihood**: the configuration was "
        "built, but the full external likelihood machinery was not run."
    )
    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "success": True,
            "publication_ready": False,
            "__tool_status__": "PARTIAL",
        },
    }]

    assert methodology_consistency_violations(reply, tool_results) == []


def test_external_likelihood_required_sentence_is_not_overclaim():
    reply = (
        "For neutrino mass constraints, the full external likelihood packages "
        "are required; no publication-ready posterior was produced."
    )
    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "success": True,
            "publication_ready": False,
            "__tool_status__": "PARTIAL",
        },
    }]

    assert methodology_consistency_violations(reply, tool_results) == []


# ── Test 5: empty/None inputs ─────────────────────────────────────────

def test_empty_reply_returns_empty():
    assert methodology_consistency_violations("", []) == []
    assert methodology_consistency_violations(None, []) == []


def test_no_fit_results_no_method_mismatch():
    """If no fit_line_lfr was called, methodology promise alone doesn't violate."""
    reply = "We will use Bayesian regression in the next step."
    violations = methodology_consistency_violations(reply, [])
    assert all(v.kind != "method_mismatch" for v in violations)


# ── Test 6: SYSTEM_PROMPT segment exists ──────────────────────────────

def test_system_prompt_has_methodology_section():
    """Smoke check: SYSTEM_PROMPT actually got the M6 segment.

    Easy to forget when refactoring chat.py; this anchors the contract.
    """
    from app.api.chat import SYSTEM_PROMPT
    assert "Line-relation fitting methodology" in SYSTEM_PROMPT
    # Key required-declaration phrases the segment contains
    assert "Declare the fit method" in SYSTEM_PROMPT
    assert "exploratory only; not" in SYSTEM_PROMPT
    assert "Decompose slope uncertainty" in SYSTEM_PROMPT
    assert "demagnify_sample" in SYSTEM_PROMPT
    assert "compare_luminosity_distances" in SYSTEM_PROMPT
    assert "subsample_significance_test" in SYSTEM_PROMPT
    assert "export_sample_table" in SYSTEM_PROMPT


# ── Test 7: chat.py imports the helper ────────────────────────────────

def test_chat_module_imports_methodology_helper():
    """The chat.py validation pipeline must actually invoke the new check."""
    import app.api.chat as chat_module
    src = open(chat_module.__file__).read()
    assert "methodology_consistency_violations" in src


# ── PART AI #3: fit_line_lfr bypass detection ───────────────────────────


def test_lfr_bypass_with_no_fit_tool_called_triggers_violation():
    """Bundle e8d9 reproducer: reply reports LFR slope/intercept/scatter but
    fit_line_lfr was not called this turn → triggers fit_line_lfr_bypass violation.
    Guards against LLM fitting in run_python and reporting numbers in prose to bypass PARTIAL gate."""
    from app.services.claim_validator import methodology_consistency_violations

    reply = (
        "Using the cached ALPINE measurement table, the Bayesian fit on the "
        "L'[CII]-FWHM relation gives slope beta = 0.766, intercept alpha = "
        "9.823, and intrinsic scatter = 0.315 dex."
    )
    tool_results = [
        {"tool": "extract_literature_tables", "result": {"success": True, "line_measurements": [{"row": 1}]}},
        {"tool": "run_python", "result": {"success": True, "stdout": "ok", "figures": []}},
    ]
    violations = methodology_consistency_violations(reply, tool_results)
    assert any(v.kind == "fit_line_lfr_bypass" for v in violations)


def test_lfr_bypass_with_fit_tool_called_does_NOT_trigger():
    """As long as fit_line_lfr was genuinely called this turn (even PARTIAL), bypass detector
    does not trigger — left to more precise checks like line_relation_exploratory_label_missing."""
    from app.services.claim_validator import methodology_consistency_violations

    reply = "L_prime[CII] LFR fit: slope = 0.79."
    tool_results = [_fit_lfr_result("ols", publication_ready=True)]
    violations = methodology_consistency_violations(reply, tool_results)
    assert all(v.kind != "fit_line_lfr_bypass" for v in violations)


def test_lfr_bypass_with_unrelated_slope_keyword_does_NOT_trigger():
    """Isochrone slope / photometry alpha and other workflows also use the word 'slope';
    bypass detector only triggers when LFR-context (L'[CII] / LFR / luminosity-FWHM etc.)
    is present. Prevents false positives."""
    from app.services.claim_validator import methodology_consistency_violations

    reply = (
        "The CMD isochrone fit gives a turnoff slope of 0.45 in the "
        "Gaia BP-RP plane. This does not relate to line luminosity."
    )
    tool_results = [
        {"tool": "fit_isochrone", "result": {"success": True, "best_age_gyr": 1.5}},
    ]
    violations = methodology_consistency_violations(reply, tool_results)
    assert all(v.kind != "fit_line_lfr_bypass" for v in violations)


def test_lfr_bypass_alternate_lfr_keywords_also_trigger():
    """_LFR_CONTEXT_RE should match multiple LFR phrasings: Solomon 1992 / brightness
    temperature / Carilli & Walter / luminosity-FWHM etc."""
    from app.services.claim_validator import methodology_consistency_violations

    for ctx_phrase in [
        "Following Solomon 1992 brightness temperature convention, the slope is 0.8.",
        "The Carilli & Walter 2013 reference relation predicts intercept = 9.5.",
        # Note: _LINE_RELATION_QUANT_RE requires "intrinsic scatter" not bare "scatter"
        "Our L-FWHM relation fit yields intrinsic scatter = 0.32 dex.",
    ]:
        violations = methodology_consistency_violations(ctx_phrase, [])
        assert any(v.kind == "fit_line_lfr_bypass" for v in violations), (
            f"bypass detector missed LFR context: {ctx_phrase!r}"
        )
