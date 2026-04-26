"""M6 验收: claim_validator.methodology_consistency_violations.

核心契约:
1. AI reply 含 'Bayesian' 类承诺但 fit_line_lfr 实际跑 OLS → method_mismatch
2. AI reply 含 'demagnified N sources' 但工具实际数 < N → demagnify_count_mismatch
3. AI 没有承诺时,不论 fit_method 如何,都不报 violation(避免误报)
4. AI 承诺 Bayesian + fit_method 实际是 'bayesian_xyerr_linmix' → 不报 violation
5. SYSTEM_PROMPT 已经被加段(line-relation methodology)— 简单存在性检查
"""

from app.services.claim_validator import methodology_consistency_violations


def _fit_lfr_result(fit_method: str, lensed_demag: int = 0) -> dict:
    """Build a minimal claimable fit_line_lfr tool_result envelope."""
    return {
        "tool": "fit_line_lfr",
        "result": {
            "tool": "fit_line_lfr",
            "success": True,
            "fit_method": fit_method,
            "lensed_sources_demagnified": lensed_demag,
            "publication_ready": True,
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
