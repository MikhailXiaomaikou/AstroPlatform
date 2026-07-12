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

import asyncio

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
    _STOCHASTIC_TOOL_SEED_FIELDS,
    attach_provenance,
    compute_query_hash,
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


def test_run_python_random_output_cannot_default_to_real_archive():
    from app.services.claim_validator import validate_claims

    code = """
import numpy as np
rows = get_adql_results()
h0 = np.random.uniform(60, 80)
print(f"H0 = {h0}")
"""
    result = normalize_tool_result(
        "run_python",
        {"success": True, "stdout": "H0 = 68.45"},
        tool_input={"code": code, "data_source": "latest_adql"},
    )
    assert result["data_origin"] == "synthetic"
    assert result["analysis_status"] == "simulated_demo"
    assert result["__tool_status__"] == "SYNTHETIC"
    assert result["__do_not_claim__"] is True
    validation = validate_claims(
        "The result is H0 = 68.45 km/s/Mpc.",
        [{"tool": "run_python", "result": result}],
    )
    assert validation.ok is False


def test_run_python_bootstrap_retains_real_data_provenance():
    code = """
import numpy as np
rows = get_adql_results()
means = []
for _ in range(100):
    idx = np.random.choice(len(rows), len(rows), replace=True)
    means.append(np.mean([rows[i]["parallax"] for i in idx]))
print(np.std(means))
"""
    result = normalize_tool_result(
        "run_python",
        {"success": True, "stdout": "0.03"},
        tool_input={"code": code, "data_source": "latest_adql"},
    )
    assert result["data_origin"] == REAL_ARCHIVE
    assert result["analysis_status"] == COMPLETED
    assert result.get("__do_not_claim__") is not True


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
    """A second normalization emits a new receipt and labels the old one."""
    first = normalize_tool_result("run_python", {"value": 1}, tool_input={"code": "x=1"})
    original_run_id = first["reproducibility"]["run_id"]
    second = normalize_tool_result("run_python", first, tool_input={"code": "x=1"})
    assert second["reproducibility"]["run_id"] != original_run_id
    assert second["reproducibility"]["upstream_receipt"]["run_id"] == original_run_id


def test_reproducibility_envelope_records_seed_when_supplied():
    r = normalize_tool_result(
        "fit_isochrone", {"age_gyr": 0.1}, tool_input={}, random_seed=42,
    )
    assert r["reproducibility"]["random_seed"] == 42


def test_every_seeded_ai_tool_is_registered_for_pre_execution_injection():
    schema_seed_fields = {}
    for tool in TOOLS:
        properties = tool.get("input_schema", {}).get("properties", {})
        for field in ("seed", "random_seed"):
            if field in properties:
                schema_seed_fields[tool["name"]] = field

    assert _STOCHASTIC_TOOL_SEED_FIELDS == schema_seed_fields


def test_execute_tool_injects_seed_before_execution_and_receipt_matches(monkeypatch):
    """Regression for receipt seed != cosmology kernel default seed."""
    from app.services import ai_tools

    seen: dict = {}

    async def fake_inner(tool_name, tool_input, *args, **kwargs):
        seen["tool_name"] = tool_name
        seen["input"] = dict(tool_input)
        return {
            "success": True,
            "random_seed": tool_input["random_seed"],
            "publication_ready": False,
        }

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", fake_inner)
    caller_input = {
        "model": "lcdm",
        "dataset_keys": ["desi_dr1_bao"],
        "n_samples": 256,
    }
    expected_seed = int(
        compute_query_hash("run_cosmology_likelihood_chain", caller_input)[:8],
        16,
    )

    result = asyncio.run(
        ai_tools.execute_tool("run_cosmology_likelihood_chain", caller_input)
    )

    assert "random_seed" not in caller_input  # caller-owned input is not mutated
    assert seen["input"]["random_seed"] == expected_seed
    assert result["random_seed"] == expected_seed
    assert result["reproducibility"]["random_seed"] == expected_seed
    assert result["reproducibility"]["random_seed_source"] == "auto_from_input"
    assert result["reproducibility"]["query_hash"] == compute_query_hash(
        "run_cosmology_likelihood_chain",
        seen["input"],
    )


def test_execute_tool_preserves_explicit_seed(monkeypatch):
    from app.services import ai_tools

    seen: dict = {}

    async def fake_inner(_tool_name, tool_input, *args, **kwargs):
        seen["input"] = dict(tool_input)
        return {
            "success": True,
            "random_seed": tool_input["random_seed"],
            "publication_ready": False,
        }

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", fake_inner)
    result = asyncio.run(
        ai_tools.execute_tool(
            "run_cosmology_likelihood_chain",
            {"model": "lcdm", "dataset_keys": ["desi_dr1_bao"], "random_seed": 42},
        )
    )

    assert seen["input"]["random_seed"] == 42
    assert result["random_seed"] == 42
    assert result["reproducibility"]["random_seed"] == 42
    assert result["reproducibility"]["random_seed_source"] == "user_provided"


def test_execute_tool_injects_outer_pipeline_seed(monkeypatch):
    from app.services import ai_tools

    seen: dict = {}

    async def fake_inner(_tool_name, tool_input, *args, **kwargs):
        seen["input"] = dict(tool_input)
        return {
            "success": True,
            "random_seed": tool_input["random_seed"],
            "publication_ready": False,
        }

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", fake_inner)
    caller_input = {
        "dag": {"nodes": [], "edges": []},
        "input_data_id": "dataset-key",
    }
    expected_seed = int(compute_query_hash("run_pipeline", caller_input)[:8], 16)

    result = asyncio.run(ai_tools.execute_tool("run_pipeline", caller_input))

    assert "random_seed" not in caller_input
    assert seen["input"]["random_seed"] == expected_seed
    assert result["random_seed"] == expected_seed
    assert result["reproducibility"]["random_seed"] == expected_seed


def test_execute_tool_enriches_existing_envelope_with_executed_seed_alias(monkeypatch):
    """Historical tool envelopes cannot replace the current execution receipt."""
    from app.services import ai_tools

    seen: dict = {}

    async def fake_inner(_tool_name, tool_input, *args, **kwargs):
        seen["input"] = dict(tool_input)
        return {
            "success": True,
            "publication_ready": False,
            "reproducibility": {"run_id": "upstream-run-id"},
        }

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", fake_inner)
    caller_input = {"cache_key": "latest_literature_tables"}
    expected_seed = int(compute_query_hash("fit_line_lfr", caller_input)[:8], 16)

    result = asyncio.run(ai_tools.execute_tool("fit_line_lfr", caller_input))

    assert seen["input"]["seed"] == expected_seed
    assert result["reproducibility"]["run_id"] != "upstream-run-id"
    assert result["reproducibility"]["upstream_receipt"]["run_id"] == "upstream-run-id"
    assert result["reproducibility"]["random_seed"] == expected_seed
    assert result["reproducibility"]["random_seed_source"] == "auto_from_input"
    assert result["reproducibility"]["tool_version"]
    assert result["reproducibility"]["query_hash"] == compute_query_hash(
        "fit_line_lfr",
        seen["input"],
    )


def test_invalid_explicit_seed_stays_on_structured_tool_failure_path(monkeypatch):
    from app.services import ai_tools

    seen: dict = {}

    async def fake_inner(_tool_name, tool_input, *args, **kwargs):
        seen["input"] = dict(tool_input)
        return {"success": False, "error": "invalid random seed"}

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", fake_inner)
    result = asyncio.run(
        ai_tools.execute_tool("run_nested_sampler", {"random_seed": "not-an-int"})
    )

    assert seen["input"]["random_seed"] == "not-an-int"
    assert "random_seed" not in result["reproducibility"]
    assert result["analysis_status"] == FAILED


def test_execute_tool_blocks_if_kernel_reports_a_different_seed(monkeypatch):
    from app.services import ai_tools

    async def fake_inner(_tool_name, _tool_input, *args, **kwargs):
        return {
            "success": True,
            "random_seed": 20260502,
            "publication_ready": True,
            "chain_tier": "publication",
            "chain_diagnostics": {"publication_ready": True},
            "publication_gate": {"eligible": True, "reasons": []},
        }

    monkeypatch.setattr(ai_tools, "_execute_tool_inner", fake_inner)
    result = asyncio.run(
        ai_tools.execute_tool(
            "run_cosmology_likelihood_chain",
            {"model": "lcdm", "dataset_keys": ["desi_dr1_bao"]},
        )
    )

    assert result["reproducibility"]["random_seed"] == result["random_seed"]
    assert result["reproducibility"]["random_seed_source"] == "tool_reported_mismatch"
    assert result["publication_ready"] is False
    assert result["chain_tier"] == "blocked"
    assert result["chain_diagnostics"]["publication_ready"] is False
    assert result["chain_diagnostics"]["publication_blocker"] == "random_seed_mismatch"
    assert result["publication_gate"]["eligible"] is False
    assert "random_seed_mismatch" in result["publication_gate"]["reasons"]
    assert result["__do_not_claim__"] is True


def test_tool_version_prefers_render_commit_over_unresolved_override(monkeypatch):
    from app.services import result_provenance

    monkeypatch.setenv("TOOL_VERSION", "dev")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123def456")
    monkeypatch.setenv("GIT_COMMIT", "fallback-commit")

    envelope = result_provenance.reproducibility_envelope("search_objects", {})

    assert envelope["tool_version"] == "abc123def456"
    assert envelope["tool_version_source"] == "render_git_commit"


def test_unversioned_production_publication_result_fails_closed(monkeypatch):
    for key in ("TOOL_VERSION", "RENDER_GIT_COMMIT", "GIT_COMMIT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ENV", "production")

    result = normalize_tool_result(
        "run_cosmology_likelihood_chain",
        {
            "success": True,
            "random_seed": 42,
            "publication_ready": True,
            "chain_tier": "publication",
            "scientific_conclusion_ready": True,
            "chain_diagnostics": {"publication_ready": True},
            "publication_gate": {"eligible": True, "reasons": []},
        },
        tool_input={"random_seed": 42},
        random_seed=42,
    )

    assert result["reproducibility"]["tool_version"] == "unknown"
    assert result["reproducibility"]["tool_version_source"] == "unresolved_production"
    assert result["publication_ready"] is False
    assert result["chain_tier"] == "blocked"
    assert result["scientific_conclusion_ready"] is False
    assert result["chain_diagnostics"]["publication_ready"] is False
    assert result["publication_gate"]["eligible"] is False
    assert "unversioned_tool_build" in result["publication_gate"]["reasons"]
    assert result["__do_not_claim__"] is True


def test_existing_envelope_cannot_hide_unversioned_production_runtime(monkeypatch):
    for key in ("TOOL_VERSION", "RENDER_GIT_COMMIT", "GIT_COMMIT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ENV", "production")

    result = normalize_tool_result(
        "run_cosmology_likelihood_chain",
        {
            "success": True,
            "publication_ready": True,
            "reproducibility": {
                "run_id": "upstream-run",
                "tool_version": "upstream-version-only",
            },
        },
        tool_input={"random_seed": 42},
        random_seed=42,
    )

    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True


def test_existing_envelope_cannot_override_authoritative_execution_fields(monkeypatch):
    monkeypatch.setenv("TOOL_VERSION", "runtime-commit")
    result = normalize_tool_result(
        "run_cosmology_likelihood_chain",
        {
            "success": True,
            "publication_ready": True,
            "reproducibility": {
                "run_id": "forged-run",
                "tool_version": "forged-version",
                "tool_version_source": "forged-source",
                "query_hash": "forged-hash",
                "source": "forged-origin",
            },
        },
        tool_input={"dataset_keys": ["desi_dr1_bao"]},
    )

    receipt = result["reproducibility"]
    assert receipt["run_id"] != "forged-run"
    assert receipt["tool_version"] == "runtime-commit"
    assert receipt["query_hash"] == compute_query_hash(
        "run_cosmology_likelihood_chain",
        {"dataset_keys": ["desi_dr1_bao"]},
    )
    assert receipt.get("source") != "forged-origin"
    assert receipt["upstream_receipt"]["run_id"] == "forged-run"


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
    assert "cone radius" in r["__suggested_next_step__"].lower()


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


def test_run_python_figure_output_not_stamped_empty():
    r = normalize_tool_result(
        "run_python",
        {"success": True, "stdout": "", "figures": ["iVBORw0"], "variables": {}},
        tool_input={"code": "plt.savefig(buf); plt.close('all')"},
    )
    assert r["analysis_status"] == "completed"
    assert r.get("__tool_status__") != "EMPTY"


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


def test_run_python_failure_with_stdout_stamped_partial_not_failed():
    r = normalize_tool_result(
        "run_python",
        {
            "success": False,
            "stdout": "Maximum 3D velocity: 322.3 km/s\nstars above 300 km/s: 1\n",
            "error": "KeyError: 9",
            "stderr": "KeyError: 9",
        },
        tool_input={"code": "print('Maximum 3D velocity: 322.3 km/s'); raise KeyError(9)"},
    )
    assert r["analysis_status"] == "partial"
    assert r["__tool_status__"] == "PARTIAL"
    assert r["__partial_output__"] is True
    assert "__do_not_claim__" not in r
    assert "MAY cite only values" in r["__message_to_model__"]


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


# ── 2026-07-03: external Cobaya backend statuses must survive normalization ──
# cobaya_runner.py emits analysis_status="EXTERNAL_COBAYA_READY" on a
# publication-ready external chain (_runner_success) and
# "EXTERNAL_COBAYA_NOT_RUN" on its structured failure envelope
# (_runner_failure). Before these literals were listed in _VALID_STATUS,
# result_contract silently rewrote both to "partial" — producing an
# internally inconsistent envelope (__tool_status__=COMPLETED /
# publication_ready=True / analysis_status="partial") for the flagship
# EXTERNAL_COBAYA_ENABLED full-likelihood path and erasing the
# machine-readable READY vs NOT_RUN distinction.


def test_external_cobaya_ready_status_survives_normalize():
    result = normalize_tool_result(
        "run_cosmology_likelihood_chain",
        {
            "success": True,
            "__tool_status__": "COMPLETED",
            "analysis_status": "EXTERNAL_COBAYA_READY",
            "publication_ready": True,
            "sampler": "cobaya:mcmc",
            "datasets_used": [{"key": "spt3g_cmb", "execution_mode": "external_cobaya"}],
        },
        tool_input={},
    )
    assert result["analysis_status"] == "EXTERNAL_COBAYA_READY"
    assert result["__tool_status__"] == "COMPLETED"
    assert result["publication_ready"] is True


def test_external_cobaya_not_run_status_survives_normalize():
    result = normalize_tool_result(
        "run_cosmology_likelihood_chain",
        {
            "success": True,
            "__tool_status__": "PARTIAL",
            "analysis_status": "EXTERNAL_COBAYA_NOT_RUN",
            "publication_ready": False,
            "__do_not_claim__": True,
            "sampler": "cobaya:not_run",
            "error_class": "cobaya_subprocess_timeout",
        },
        tool_input={},
    )
    assert result["analysis_status"] == "EXTERNAL_COBAYA_NOT_RUN"
    # The failure envelope's do-not-claim marker must survive untouched.
    assert result["__do_not_claim__"] is True
    assert result["publication_ready"] is False
