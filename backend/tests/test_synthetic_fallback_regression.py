"""G7.1: end-to-end regression for the synthetic-data fallback trap.

Three scenarios the reviewer hit:

1. Paper 2 (HD 209458 TESS): MAST times out → AI writes
   np.random / np.linspace synthetic light curve → OLD gate missed it,
   NEW gate (G2 AST detector + G3 dependency tracking) must catch.

2. Paper 5 (Coma red sequence): VizieR V/147/sdss12 rejected
   'RAJ2000' → NEW catalog registry hint suggests the right column.

3. AI declares data_source='none_not_analyzing_real_data' → output
   is marked SYNTHETIC, no reply-level number will pass the
   zero-fabrication gate.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.services.ai_tools import _exec_run_python
from app.services.claim_validator import validate_claims, is_empty_turn, zero_data_but_quantitative
from app.services.synthetic_code_detector import analyze


# ----------------------------------------------------------- synthetic path


def test_paper2_hd209458_synthetic_code_is_rejected():
    """Declared data_source='latest_lightcurve' but the code is classic
    fake-TESS generation — must be rejected. Either layer (G1.2 code-vs-
    declaration mismatch OR G2 AST synthetic detection) is an acceptable
    rejection — what matters is the AI cannot get a happy success result."""
    code = """
# Since direct MAST access is timing out, let's simulate a realistic
# TESS light curve for HD 209458 based on known parameters
import numpy as np
time = np.linspace(0, 27, 20000)
flux = np.ones_like(time) + np.random.normal(0, 0.001, len(time))
print(f"Generated {len(time)} synthetic cadences")
"""
    # First detector verdict
    det = analyze(code)
    assert det.verdict == "synthetic"

    # Now via _exec_run_python, declared as real
    resp = asyncio.run(_exec_run_python({
        "code": code,
        "data_source": "latest_lightcurve",
    }))
    assert resp["success"] is False
    # Either layer rejects it — both are valid zero-fabrication outcomes.
    # G1.2 fires first (code doesn't use get_cached_results etc.), G2 would
    # fire if the code DID also reference a real-data helper.
    assert resp.get("error_class") in {
        "synthetic_declared_as_real",
        "data_source_mismatch",
    }


def test_declared_synthetic_marks_output():
    """Honest declaration of synthetic → SYNTHETIC banner attached."""
    code = """
import numpy as np
vals = np.random.randn(100)
print(vals.mean())
"""
    resp = asyncio.run(_exec_run_python({
        "code": code,
        "data_source": "none_not_analyzing_real_data",
    }))
    # It may succeed or fail at sandbox level; what matters is the banner
    # is attached when success.
    assert resp.get("data_origin") == "synthetic"
    assert resp.get("__tool_status__") == "SYNTHETIC" or resp.get("analysis_status") in ("simulated_demo", "failed")
    assert resp.get("__do_not_claim__") is True


def test_clean_real_data_code_passes():
    """Real-data reading code with correct data_source → passes."""
    code = """
rows = get_adql_results()
print(len(rows))
"""
    resp = asyncio.run(_exec_run_python({
        "code": code,
        "data_source": "latest_adql",
    }))
    # Sandbox may still fail (get_adql_results returns empty by default),
    # but the contract layer must accept this code — it should NOT be
    # error_class in {"synthetic_declared_as_real","data_source_mismatch"}.
    assert resp.get("error_class") not in (
        "synthetic_declared_as_real",
        "data_source_mismatch",
        "missing_data_source",
    )


def test_data_source_mismatch_rejected():
    """Declared latest_adql but code doesn't actually call get_adql_results."""
    code = """
import numpy as np
x = np.array([1, 2, 3])
print(x.mean())
"""
    resp = asyncio.run(_exec_run_python({
        "code": code,
        "data_source": "latest_adql",
    }))
    assert resp["success"] is False
    assert resp.get("error_class") == "data_source_mismatch"


def test_missing_data_source_defaults_to_synthetic():
    """Legacy callers without data_source must still work, but get
    marked SYNTHETIC (backwards-compat default, G1.1)."""
    code = "print('hello')"
    resp = asyncio.run(_exec_run_python({"code": code}))
    # Missing declaration → default to synthetic treatment
    assert resp.get("data_origin") == "synthetic"


# ------------------------------------------------------ catalog hint path


def test_catalog_suggestion_for_common_mistake():
    """RAJ2000 in SDSS V/147 → hint points to RA_ICRS or _RAJ2000."""
    from app.services.catalog_registry import suggest_for_missing_column
    hint = suggest_for_missing_column("RAJ2000")
    assert hint is not None
    assert "RA_ICRS" in hint or "_RAJ2000" in hint


def test_v147_sdss12_registered():
    from app.services.catalog_registry import get_catalog
    entry = get_catalog('"V/147/sdss12"')
    assert entry is not None
    col_names = [c.name for c in entry.columns]
    assert "RA_ICRS" in col_names
    assert "_RAJ2000" in col_names
    # And the COMMON MISTAKE 'RAJ2000' is NOT a real column for this catalog:
    assert "RAJ2000" not in col_names


# ---------------------------------------------------- empty-turn integration


def test_empty_turn_with_synthetic_bait_blocks_claim():
    """Reviewer-style failure: real data tool empty, AI's run_python was
    tainted SYNTHETIC.  Any number claimed from that synthetic output
    must fail the zero-fabrication gate."""
    # All turn results: empty ADQL + synthetic run_python
    tool_results = [
        {"tool": "run_adql", "result": {"row_count": 0, "__tool_status__": "EMPTY"}},
        {
            "tool": "run_python",
            "result": {
                "__tool_status__": "SYNTHETIC",
                "__do_not_claim__": True,
                "data_origin": "synthetic",
                "success": True,
                "stdout": "776 stars, parallax 7.353 mas",
                # The synthetic output CONTAINS 776 and 7.353 in its stdout
            },
        },
    ]
    # AI tries to cite these numbers
    reply = "Found 776 stars with mean parallax 7.353 mas."
    # is_empty_turn should detect ADQL failed; run_python success but
    # synthetic doesn't rescue the turn: it still counts as "no real data".
    assert is_empty_turn(tool_results) is True
    blocked_claims = zero_data_but_quantitative(reply, tool_results)
    assert {c.value for c in blocked_claims} >= {776.0, 7.353}

    result = validate_claims(reply, tool_results)
    assert result.ok is False
    assert {c.value for c in result.uncited} >= {776.0, 7.353}
    assert tool_results[1]["result"]["__do_not_claim__"] is True
    assert tool_results[1]["result"]["__tool_status__"] == "SYNTHETIC"


# -------------------------------------------------- anti-instruction reflex


def test_sanitize_error_strips_retry_and_simulate():
    """G1.5c: 'retry' / 'simulate' in tool error text must be neutralised
    so the LLM doesn't interpret them as instructions. What we assert:
    1. The imperative "Please retry" becomes a declarative "(you cannot retry it)".
    2. The word "simulate" gets [REDACTED].
    3. "narrower parameters" becomes "(the call was rejected)".
    """
    from app.services.result_provenance import _sanitize_error_message

    cleaned = _sanitize_error_message(
        "MAST timed out. Please retry with narrower parameters or "
        "simulate the light curve."
    )
    # The imperative "Please retry with" should be gone; only the
    # declarative / parenthesised replacement form may contain "retry".
    assert "please retry" not in cleaned.lower()
    assert "retry with" not in cleaned.lower()
    # narrower parameters → (the call was rejected)
    assert "narrower parameters" not in cleaned.lower()
    # simulate → [REDACTED]
    assert "simulate" not in cleaned.lower() or "[redacted" in cleaned.lower()
    # Sanity: redacted marker is present
    assert "redacted" in cleaned.lower()


def test_sanitize_error_preserves_normal_text():
    from app.services.result_provenance import _sanitize_error_message
    # A normal error with no injection-prone words should pass through
    original = "ConnectionError: host unreachable at 10.0.0.1"
    cleaned = _sanitize_error_message(original)
    assert "ConnectionError" in cleaned
    assert "host unreachable" in cleaned
