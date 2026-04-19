"""Unit tests for app.services.result_provenance.

Locks two contracts the chat agent depends on:

1. Every tool declared in app.services.ai_tools.TOOLS has a default
   classification (_DATA / _COMPUTE / _REFERENCE).  Unclassified tools
   silently fall through to UNAVAILABLE/PARTIAL, which tells the LLM the
   data is unreliable — a subtle regression that's easy to introduce
   when adding a new tool.

2. Compute tools default to REAL_ARCHIVE origin on success (not
   USER_UPLOADED).  Most compute tools analyze archive-sourced data;
   labelling their output "user_uploaded" subverts the anti-fabrication
   contract the whole module exists to enforce.
"""

from __future__ import annotations

import pytest

from app.services.ai_tools import TOOLS
from app.services.result_provenance import (
    ALL_KNOWN_TOOLS,
    COMPLETED,
    FAILED,
    REAL_ARCHIVE,
    UNAVAILABLE,
    _COMPUTE_TOOLS,
    _DATA_TOOLS,
    _REFERENCE_TOOLS,
    attach_provenance,
    normalize_tool_result,
    result_contract,
)


def test_all_registered_tools_are_classified():
    """Every name in TOOLS must appear in exactly one classification set."""
    tool_names = {t["name"] for t in TOOLS}
    missing = tool_names - ALL_KNOWN_TOOLS
    assert not missing, (
        f"{len(missing)} tool(s) in TOOLS are unclassified in "
        f"result_provenance.py — they will fall through to UNAVAILABLE/"
        f"PARTIAL. Add to _DATA_TOOLS, _COMPUTE_TOOLS, or _REFERENCE_TOOLS: "
        f"{sorted(missing)}"
    )


def test_classification_sets_disjoint():
    """No tool should appear in multiple classification sets."""
    assert not (_DATA_TOOLS & _COMPUTE_TOOLS), _DATA_TOOLS & _COMPUTE_TOOLS
    assert not (_DATA_TOOLS & _REFERENCE_TOOLS), _DATA_TOOLS & _REFERENCE_TOOLS
    assert not (_COMPUTE_TOOLS & _REFERENCE_TOOLS), _COMPUTE_TOOLS & _REFERENCE_TOOLS


def test_no_stale_classifications():
    """Classification sets should not contain names that no longer exist."""
    tool_names = {t["name"] for t in TOOLS}
    stale = ALL_KNOWN_TOOLS - tool_names
    assert not stale, (
        f"Classification set contains {len(stale)} stale tool(s) not in "
        f"TOOLS registry: {sorted(stale)}. Remove them from "
        f"result_provenance.py."
    )


def test_compute_tool_success_defaults_to_real_archive():
    """Compute-tool successes should be tagged REAL_ARCHIVE, not USER_UPLOADED.

    Most compute tools (run_python, fit_isochrone, analyze_spectrum, …)
    operate on archive data; labelling their output as user-uploaded
    misleads the LLM about provenance.
    """
    result = normalize_tool_result("run_python", {"value": 42})
    assert result["data_origin"] == REAL_ARCHIVE
    assert result["analysis_status"] == COMPLETED


def test_compute_tool_failure_is_unavailable_failed():
    result = normalize_tool_result("run_python", {"success": False, "error": "boom"})
    assert result["data_origin"] == UNAVAILABLE
    assert result["analysis_status"] == FAILED


def test_data_tool_success_is_real_archive_completed():
    result = normalize_tool_result("search_objects", {"results": [{"name": "M31"}]})
    assert result["data_origin"] == REAL_ARCHIVE
    assert result["analysis_status"] == COMPLETED


def test_reference_tool_success_is_real_archive_completed():
    result = normalize_tool_result("search_literature", {"papers": []})
    assert result["data_origin"] == REAL_ARCHIVE
    assert result["analysis_status"] == COMPLETED


def test_explicit_origin_on_payload_is_preserved():
    """Tools may override the inferred default by setting data_origin."""
    result = normalize_tool_result(
        "run_python",
        {"value": 1, "data_origin": "user_uploaded", "analysis_status": "completed"},
    )
    assert result["data_origin"] == "user_uploaded"
    assert result["analysis_status"] == "completed"


def test_error_payload_is_unavailable_failed():
    result = normalize_tool_result("search_objects", {"error": "timeout"})
    assert result["data_origin"] == UNAVAILABLE
    assert result["analysis_status"] == FAILED


def test_non_dict_result_is_wrapped():
    result = normalize_tool_result("search_objects", 42)
    assert result["value"] == 42
    assert "data_origin" in result


def test_attach_provenance_merges_warnings():
    payload = {"warnings": ["prior warning"]}
    out = attach_provenance(
        payload,
        data_origin=REAL_ARCHIVE,
        analysis_status=COMPLETED,
        warnings=["new warning"],
    )
    assert "prior warning" in out["warnings"]
    assert "new warning" in out["warnings"]


@pytest.mark.parametrize(
    "origin, status, expected_status",
    [
        ("synthetic", "completed", "simulated_demo"),
        ("unavailable", "completed", "failed"),
        ("real_archive", "completed", "completed"),
    ],
)
def test_result_contract_invariants(origin, status, expected_status):
    c = result_contract(data_origin=origin, analysis_status=status)
    assert c["analysis_status"] == expected_status


# --------------------------------------------------------------------
# R1 — reproducibility envelope
# --------------------------------------------------------------------


def test_reproducibility_envelope_is_attached_on_normalize():
    r = normalize_tool_result("search_objects", {"results": []}, tool_input={"q": "M31"})
    env = r["reproducibility"]
    assert "run_id" in env and len(env["run_id"]) >= 32  # uuid4
    assert "tool_version" in env
    assert "query_hash" in env and len(env["query_hash"]) == 16
    assert "timestamp_utc" in env


def test_query_hash_deterministic_same_input():
    from app.services.result_provenance import compute_query_hash
    h1 = compute_query_hash("run_adql", {"query": "SELECT * FROM gaia"})
    h2 = compute_query_hash("run_adql", {"query": "SELECT * FROM gaia"})
    h3 = compute_query_hash("run_adql", {"query": "SELECT 1"})
    assert h1 == h2
    assert h1 != h3


def test_reproducibility_envelope_is_idempotent():
    """normalize_tool_result must not re-stamp an existing envelope."""
    first = normalize_tool_result("run_python", {"value": 1}, tool_input={"code": "x=1"})
    original_run_id = first["reproducibility"]["run_id"]
    second = normalize_tool_result("run_python", first, tool_input={"code": "x=1"})
    assert second["reproducibility"]["run_id"] == original_run_id


def test_reproducibility_envelope_records_seed_when_supplied():
    r = normalize_tool_result(
        "fit_isochrone", {"age_gyr": 0.1}, tool_input={}, random_seed=42,
    )
    assert r["reproducibility"]["random_seed"] == 42


# ---------- F2.1: EMPTY status + upstream banner ----------


def test_adql_zero_rows_stamped_as_empty():
    """run_adql with row_count=0 should be analysis_status=EMPTY, not
    COMPLETED, and carry the __tool_status__ banner."""
    r = normalize_tool_result(
        "run_adql",
        {"row_count": 0, "rows": [], "columns": ["ra", "dec"]},
        tool_input={"query": "SELECT * FROM t"},
    )
    assert r["analysis_status"] == "empty"
    assert r["__tool_status__"] == "EMPTY"
    assert r["__do_not_claim__"] is True
    assert "MUST NOT claim" in r["__message_to_model__"]
    assert "next step" in r["__suggested_next_step__"].lower() or r["__suggested_next_step__"]


def test_search_objects_empty_results_stamped_as_empty():
    r = normalize_tool_result(
        "search_objects",
        {"results": []},
        tool_input={"query": "M31"},
    )
    assert r["analysis_status"] == "empty"
    assert r["__tool_status__"] == "EMPTY"


def test_run_python_no_output_stamped_as_empty():
    r = normalize_tool_result(
        "run_python",
        {"success": True, "stdout": "", "figures": [], "variables": {}},
        tool_input={"code": "pass"},
    )
    assert r["analysis_status"] == "empty"
    assert r["__tool_status__"] == "EMPTY"


def test_tool_failure_stamped_as_failed_with_banner():
    r = normalize_tool_result(
        "run_python",
        {"success": False, "error": "NameError: foo"},
        tool_input={"code": "print(foo)"},
    )
    assert r["analysis_status"] == "failed"
    assert r["__tool_status__"] == "FAILED"
    assert "NameError" in r["__message_to_model__"]
    assert r["__do_not_claim__"] is True


def test_successful_adql_not_stamped_empty():
    r = normalize_tool_result(
        "run_adql",
        {"row_count": 3, "rows": [[1], [2], [3]], "columns": ["x"]},
        tool_input={"query": "SELECT x FROM t"},
    )
    assert r["analysis_status"] == "completed"
    assert "__tool_status__" not in r or r.get("__tool_status__") != "EMPTY"


def test_banner_keys_appear_before_payload_in_dict_iter():
    """F2.1: banner keys must be at the FRONT of the dict so the LLM sees
    them first when streaming JSON left-to-right."""
    r = normalize_tool_result(
        "run_adql",
        {"row_count": 0, "rows": [], "columns": ["ra"]},
        tool_input={},
    )
    keys = list(r.keys())
    assert keys[0] == "__tool_status__", f"banner should be first key; got {keys[:5]}"


def test_suggested_next_step_is_tool_specific():
    from app.services.result_provenance import _suggest_next_step
    assert "cone radius" in _suggest_next_step("run_adql").lower()
    assert "print" in _suggest_next_step("run_python").lower()
    assert "keyword" in _suggest_next_step("search_literature").lower()


def test_is_empty_payload_detects_common_shapes():
    from app.services.result_provenance import _is_empty_payload

    assert _is_empty_payload("run_adql", {"row_count": 0})
    assert _is_empty_payload("search_objects", {"results": []})
    assert _is_empty_payload(
        "run_python",
        {"success": True, "stdout": "", "figures": [], "variables": {}},
    )
    # Non-empty payloads must NOT be flagged
    assert not _is_empty_payload("run_adql", {"row_count": 5})
    assert not _is_empty_payload("run_python", {"success": True, "stdout": "hi"})
