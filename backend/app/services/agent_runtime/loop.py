"""The agent loop (_run_agent_loop) extracted from app/api/chat.py.

Moved verbatim from app/api/chat.py (2026-07-03 god-file split).

Dispatch shims: tests install doubles directly on the chat module
(``chat._llm_messages_create = fake``; ``importlib.reload(app.api.chat)``
re-reads ASTRO_RESEARCH_FOCUS), so the loop resolves those collaborators
through app.api.chat at call time via the shims below — the repo's lazy
import convention for api <-> services cycles.  Everything else is imported
normally from sibling modules.
"""

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

from app.ai.inference_router import InferenceError, inference_router
from app.ai.model_profiles import ModelProfile

from app.services.agent_runtime.abstention import (
    _abstention_attrs_without_numeric_claims,
    _classify_abstention_reason,
    _is_failed_or_empty_data_fetch,
    _parse_abstention_tag,
    _parse_actions,
    _render_abstention_card,
    _reply_looks_truncated,
    _run_python_code_reads_real_cache,
    _sanitize_tools_returned_nothing,
    _strip_actions_from_reply,
    _user_requested_synthetic_demo,
)
from app.services.agent_runtime.line_relation import (
    _is_line_relation_workflow,
    _line_fit_cosmology_from_prompt,
    _line_fit_method_from_prompt,
    _line_fit_subsample_splits_from_prompt,
    _ranked_literature_arxiv_candidates,
    _should_suppress_line_measurement_synthetic_python,
    _suppressed_line_measurement_python_result,
    _suppressed_line_relation_extract_result,
    _suppressed_line_relation_search_result,
    _table_extraction_arxiv_ids,
    _verified_line_relation_seed_candidates,
)
from app.services.agent_runtime.honesty import (
    nonpublication_posterior_refusal,
    nonpublication_posterior_values,
    untrusted_evidence_echo_values,
    untrusted_evidence_refusal,
)
from app.services.agent_runtime.prompt_routing import (
    _compact_tool_results_for_evidence,
    _cosmology_dataset_keys_from_prompt,
    _cosmology_direct_route_from_prompt,
    _cosmology_forbidden_probe_families,
    _cosmology_likelihood_build_calls_from_prompt,
    _cosmology_likelihood_run_calls_from_prompt,
    _cosmology_prompt_mentions_bao,
    _inline_statistics_tool_call_from_prompt,
    _is_cosmology_likelihood_workflow,
    _is_research_program_workflow,
    _research_evidence_graph_from_tool_results,
    _research_plan_from_tool_results,
    _unsupported_cosmology_anchor_numeric_comparison,
)
from app.services.agent_runtime.runtime_config import (
    _is_tool_inventory_request,
    _workflow_budget_config,
)
from app.services.agent_runtime.summaries import (
    _cosmology_outside_coverage_disclosure,
    _cosmology_requested_redshift,
    _cosmology_tool_grounded_summary,
    _enforce_cosmology_dataset_identity,
    _line_fit_partial_from_result,
    _line_fit_publication_ready_from_result,
    _line_lfr_tool_grounded_summary,
    _line_measurement_count_from_result,
    _research_tool_grounded_summary,
    _successful_research_report_export,
    _statistics_tool_grounded_summary,
    _tool_grounded_summary,
)
from app.services.agent_runtime.tool_execution import (
    _checkpoint_session_id,
    _format_checkpoint_resume_note,
    _record_tool_checkpoint,
    _tool_results_to_actions,
)

logger = logging.getLogger(__name__)


# ── Call-time dispatch shims through app.api.chat ──────────────────────
#
# These three collaborators are part of chat.py's monkeypatch surface:
# tests swap them directly on the chat module (test_chat_gate_fail_closed /
# test_gate_events / test_agent_loop_hard_reject_disable assign
# chat._llm_messages_create / chat._execute_tool_calls, and
# test_research_focus_gating reloads app.api.chat to re-read
# ASTRO_RESEARCH_FOCUS).  Resolving them through the chat module at call
# time keeps those seams intact after the move; production behavior is
# identical (chat.py re-exports the real implementations).


async def _llm_messages_create(**kwargs):
    from app.api import chat as _chat

    return await _chat._llm_messages_create(**kwargs)


async def _execute_tool_calls(*args, **kwargs):
    from app.api import chat as _chat

    return await _chat._execute_tool_calls(*args, **kwargs)


def _filter_tools_by_research_focus(tools: list[dict]) -> list[dict]:
    from app.api import chat as _chat

    return _chat._filter_tools_by_research_focus(tools)


def _active_research_focus() -> str:
    from app.api import chat as _chat

    return _chat._ASTRO_RESEARCH_FOCUS


# ── Per-reply validation summary (2026-07-03, honesty surfacing) ────────
#
# Derived ONLY from what the gate stack already computed this turn:
# fabrication_stats, the structured gate-intervention records collected by
# _gate_event, and whether the claim-gate block ran at all.  No new
# validation logic lives here — this is a read-only surfacing layer so the
# UI can distinguish "the gates ran and passed" from "the gates never ran".

# Gates whose interventions concern numeric / quantitative claims.
_NUMERIC_GATE_FAMILY = frozenset({
    "numeric_claims", "zero_data", "cjk_filter", "fact_verification",
    "empty_reply_fallback", "dataset_identity", "report_export",
    "untrusted_evidence_echo", "nonpublication_posterior", "dataset_coverage",
})
# Gates whose interventions concern citations / literature narrative.
_CITATION_GATE_FAMILY = frozenset({
    "citation_methodology", "literature_prior", "unclassified_literature",
    "unsupported_narrative", "cosmology_anchor", "fact_verification",
    "report_export",
})
_BLOCKING_GATE_ACTIONS = frozenset({"blocked", "annotated_blocked"})
_VALIDATION_SUMMARY_MAX_INTERVENTIONS = 10


def _derive_validation_summary(
    *,
    claim_gate_ran: bool,
    gate_skip_reason: str | None,
    fabrication_stats: dict,
    interventions: list[dict],
    tool_results: list[dict],
) -> dict:
    """Compact, honest per-reply summary of what the gate stack did.

    Per-gate vocabulary:
      passed           gate ran, no intervention
      regenerated      gate intervened; the shipped reply was rewritten or
                       downgraded to a tool-grounded form that then passed
      blocked          gate blocked the reply / annotated it as unverified
      skipped_no_data  gate ran but the turn produced no claimable tool
                       data — "passed" would overstate what was checked
      skipped          gate stack intentionally skipped (see ``reason``)

    HONESTY RULE: this must never claim more than the gates actually
    verified.  "passed" means "validated against this turn's tool data",
    NOT "guaranteed true".
    """

    def _family_state(family: frozenset) -> str:
        acts = [i for i in interventions if i.get("gate") in family]
        if any(i.get("action") in _BLOCKING_GATE_ACTIONS for i in acts):
            return "blocked"
        if acts:
            return "regenerated"
        if not claim_gate_ran:
            return "skipped"
        return "passed"

    numeric_state = _family_state(_NUMERIC_GATE_FAMILY)
    citation_state = _family_state(_CITATION_GATE_FAMILY)
    if numeric_state == "passed":
        try:
            from app.services.claim_validator import is_empty_turn

            if is_empty_turn(tool_results):
                numeric_state = "skipped_no_data"
        except Exception:
            pass
    summary: dict[str, Any] = {
        "schema_version": 1,
        "numeric_gate": numeric_state,
        "citation_gate": citation_state,
        "regen_count": int(fabrication_stats.get("regenerations", 0) or 0),
        "blocked": bool(fabrication_stats.get("blocked", False)),
        "interventions": [
            {
                "gate": str(i.get("gate", "")),
                "action": str(i.get("action", "")),
                "reason": str(i.get("reason", "")),
            }
            for i in interventions[:_VALIDATION_SUMMARY_MAX_INTERVENTIONS]
        ],
    }
    if not claim_gate_ran and gate_skip_reason:
        summary["reason"] = str(gate_skip_reason)
    return summary


def _not_run_validation_summary(reason: str) -> dict:
    """Summary for early-return paths where the gate stack never ran.

    Distinct from "passed" by construction — the UI must render these as
    "not validated (<reason>)", never as a pass.
    """
    return {
        "schema_version": 1,
        "numeric_gate": "not_run",
        "citation_gate": "not_run",
        "regen_count": 0,
        "blocked": False,
        "reason": str(reason),
        "interventions": [],
    }


async def _run_agent_loop(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict],
    provider_api_keys: dict[str, str],
    agent_name: str,
    python_session_id: str,
    preferred_backend: str | None = None,
    model_profile: ModelProfile | None = None,
    user_id: str | None = None,
    chat_session_id: str | None = None,
    on_event: Callable[[dict], Awaitable[None]] | None = None,
    workflow_budget: dict[str, Any] | None = None,
) -> dict:
    """Run the agent's multi-turn loop.

    When `on_event` is provided, intermediate thinking-process events are
    emitted so the SSE endpoint can stream them to the UI in real time:
    - {"type": "agent_text", "agent": <name>, "content": <str>}  — LLM text
      produced between tool calls (the model's "thinking out loud").
    - {"type": "tool_call", "agent": <name>, "tool": <name>, "input": <dict>}
      — fires before each tool starts executing.
    - {"type": "tool_result", "agent": <name>, "tool": <name>, "result": <dict>}
      — fires when each tool completes.
    """
    # 2026-05-28: normalise public provider names to canonical backend ids.
    # The blind-test runners pass --provider anthropic / deepseek / openai,
    # but inference_router registers backends under their internal names
    # (claude vs anthropic, etc.). Without this mapping, --provider anthropic
    # silently falls through to the first available fallback backend
    # (DeepSeek when DEEPSEEK_API_KEY is also set), which is what caused
    # the V7 Opus blind run to charge zero Anthropic credit.
    if preferred_backend == "anthropic":
        preferred_backend = "claude"

    import time as _time

    async def _emit(evt: dict) -> None:
        if on_event is not None:
            try:
                await on_event(evt)
            except Exception as exc:  # never let event-pump errors kill the loop
                logger.debug("on_event failed for %s: %s", evt.get("type"), exc)

    working_messages = deepcopy(messages)
    all_tool_results: list[dict] = []
    text_parts: list[str] = []
    latest_user_text = ""
    for _msg in reversed(messages):
        if _msg.get("role") == "user":
            latest_user_text = str(_msg.get("content") or "")
            break
    skip_claim_gate_for_meta = _is_tool_inventory_request(latest_user_text)
    budget = _workflow_budget_config((workflow_budget or {}).get("mode"))
    budget.update(workflow_budget or {})
    max_iterations = int(budget.get("max_iterations", 12))
    summary_reserve_s = float(budget.get("summary_reserve_seconds", 60.0))
    soft_reminder_s = float(budget.get("soft_reminder_seconds", 75.0))
    budget_mode = str(budget.get("mode") or "default")
    _loop_seconds = float(budget.get("agent_loop_seconds", 360.0))
    # H1 / long-task bump: default remains 360s; paper-scale workflows can
    # explicitly opt into a 900s loop with larger summary reserve.
    _loop_deadline = _time.monotonic() + _loop_seconds
    checkpoint_id = _checkpoint_session_id(chat_session_id, python_session_id)
    checkpoint_note = _format_checkpoint_resume_note(checkpoint_id)

    await _emit({
        "type": "workflow_budget",
        "agent": agent_name,
        "mode": budget_mode,
        "agent_loop_seconds": int(_loop_seconds),
        "summary_reserve_seconds": int(summary_reserve_s),
        "max_iterations": max_iterations,
    })
    if checkpoint_note:
        try:
            from app.services import workflow_checkpoint
            await _emit({
                "type": "workflow_checkpoint",
                "agent": agent_name,
                "summary": workflow_checkpoint.summarize(checkpoint_id or ""),
            })
        except Exception:
            pass

    # G3.1: track which data-fetch tools have failed this turn so we can
    # suppress subsequent synthetic run_python fallbacks + physically remove
    # the failed tools from future LLM turns (G3.4).
    _DATA_FETCH_TOOLS = {
        "search_objects", "run_adql", "search_lightcurve", "query_transients",
        "crossmatch_catalogs", "query_gaia_cluster", "get_object_info",
        "get_object_dossier", "get_extinction", "search_literature",
        "extract_literature_tables", "query_high_velocity_stars",
    }
    MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS = 2
    # G3.4 + H0.7: tool → failure count this turn.  When ≥
    # DISABLE_AFTER_FAILURES, the tool is removed from the `tools`
    # parameter sent to the LLM on the next iteration — the model
    # literally cannot call it any more.
    # H0.7: raised threshold from 2 to 3, and only count RETRYABLE
    # failures (connector errors, not timeouts/payload_too_large which
    # the AI might legitimately retry with smaller scope).
    # 2026-05-20: Explicit hard-reject error_class set — these are not retryable;
    # retrying with the same parameters will be rejected again. C2 case: the LLM
    # misread range_too_large as retryable and never triggered the circuit breaker
    # after 12 attempts.
    _HARD_REJECT_ERROR_CLASSES = frozenset({
        "range_too_large",    # rejected locally by the tool, e.g. 100-year daily ephemeris
        "missing_argument",   # required parameter missing; retrying is pointless until the LLM fills it in
        "invalid_argument",   # type/range error; same as above
    })
    tool_failure_counts: dict[str, int] = {}
    # R22: row_count=0 / EMPTY are soft for retry disabling, but still mean
    # later Python cells have no real cache to analyze.  Track them separately
    # so fallback/demo code is suppressed as ∅ Empty instead of AUTO/SYNTHETIC.
    empty_data_fetches: set[str] = set()
    DISABLE_AFTER_FAILURES = 3
    synthetic_run_python_count = 0  # G3.3 counter
    user_requested_synthetic_demo = _user_requested_synthetic_demo(messages)
    fit_ready_literature_cache_keys: list[str] = []
    inline_statistics_call = _inline_statistics_tool_call_from_prompt(latest_user_text)
    research_program_workflow = _is_research_program_workflow(latest_user_text)
    cosmology_likelihood_workflow = _is_cosmology_likelihood_workflow(latest_user_text)
    cosmology_likelihood_build_calls = _cosmology_likelihood_build_calls_from_prompt(latest_user_text)
    cosmology_likelihood_run_calls = _cosmology_likelihood_run_calls_from_prompt(latest_user_text)
    # 2026-05-28: deterministic single-tool route for "Hubble tension" and
    # "Alcock-Paczynski" prompts. Fires on iteration 0 only, bypasses the
    # first LLM call. None when no trigger phrase matches.
    cosmology_direct_route_calls = _cosmology_direct_route_from_prompt(latest_user_text)

    hit_iteration_cap = False
    hit_deadline = False
    soft_deadline_reminded = False
    # C-X1 (P0): record the LLM stop_reason of the iteration that breaks
    # the agent loop so the post-loop truncation gate can detect a
    # max_tokens / length cut-off and run a safe-summary regen instead
    # of shipping a dangling partial sentence to the UI.
    last_stop_reason: str | None = None
    for _iteration in range(max_iterations):
        if _time.monotonic() > _loop_deadline:
            hit_deadline = True
            # B1: do NOT ship accumulated LLM prose here. This early return
            # skips the entire post-loop validation block (claim validator,
            # zero-data hard block, citation gate, abstention parser), so a
            # fabricated number the model wrote in `text_parts` before the
            # deadline would reach the user unchecked. Emit only a deterministic
            # tool-grounded summary (numbers sourced from tool_results, not free
            # text) — matching the synthesis-failure fallback below and the
            # endpoint-timeout path in the SSE generator.
            grounded = _tool_grounded_summary(all_tool_results, latest_user_text) or ""
            summary = grounded.strip() or (
                "The agent loop timed out before any tool produced a citable result."
            )
            deadline_validation = _not_run_validation_summary("loop_deadline")
            try:
                from app.services.claim_validator import (
                    enforce_scientific_conclusion_gate,
                )

                summary, conclusion_violations = enforce_scientific_conclusion_gate(
                    summary, all_tool_results
                )
                if conclusion_violations:
                    deadline_validation = {
                        "schema_version": 1,
                        "numeric_gate": "not_run",
                        "citation_gate": "blocked",
                        "regen_count": 0,
                        "blocked": True,
                        "reason": "scientific_conclusion_scope",
                        "interventions": [{
                            "gate": "scientific_conclusion_scope",
                            "action": "blocked",
                            "reason": "unmatched_conclusion_attestation",
                        }],
                    }
            except Exception:
                summary = (
                    "The agent loop timed out and scientific-conclusion "
                    "validation could not complete. No scientific conclusion "
                    "is cleared for display; review the tool cards and rerun."
                )
                deadline_validation = {
                    "schema_version": 1,
                    "numeric_gate": "not_run",
                    "citation_gate": "blocked",
                    "regen_count": 0,
                    "blocked": True,
                    "reason": "scientific_conclusion_validation_error",
                    "interventions": [{
                        "gate": "scientific_conclusion_scope",
                        "action": "blocked",
                        "reason": "validation_error",
                    }],
                }
            return {
                "reply": (
                    summary
                    + f"\n\n(Agent loop timed out after {int(_loop_seconds)} seconds. "
                    "Results above are partial and checkpointed for continuation.)"
                ),
                "actions": _tool_results_to_actions(all_tool_results),
                "hit_deadline": True,
                "hit_iteration_cap": False,
                # This early return skips the entire gate stack — say so
                # instead of letting the reply look validated.
                "validation_summary": deadline_validation,
            }

        # G3.4: filter tools that have failed too many times this turn.
        visible_tools = [
            t for t in tools
            if tool_failure_counts.get(t.get("name", ""), 0) < DISABLE_AFTER_FAILURES
        ]
        disabled_this_turn = [
            t.get("name") for t in tools
            if tool_failure_counts.get(t.get("name", ""), 0) >= DISABLE_AFTER_FAILURES
        ]
        # L1: research-focus filter (outermost). Drop tools that are not
        # in the active focus's allowlist before any later domain-specific
        # gate runs. No-op only when ASTRO_RESEARCH_FOCUS=all; the default is
        # "cosmology" (chat.py:58), so this filter IS active by default.
        visible_tools = _filter_tools_by_research_focus(visible_tools)
        line_relation_workflow = _is_line_relation_workflow(latest_user_text)
        literature_searches_done = sum(
            1 for tr in all_tool_results if tr.get("tool") == "search_literature"
        )
        table_extraction_attempts_done = sum(
            1 for tr in all_tool_results if tr.get("tool") == "extract_literature_tables"
        )
        has_fit_ready_line_measurements = bool(fit_ready_literature_cache_keys) or any(
            _line_measurement_count_from_result(tr.get("result")) > 0
            for tr in all_tool_results
            if tr.get("tool") == "extract_literature_tables"
        )
        has_publication_ready_line_fit = any(
            _line_fit_publication_ready_from_result(tr.get("result"))
            for tr in all_tool_results
            if tr.get("tool") == "fit_line_lfr"
        )
        has_partial_line_fit = any(
            _line_fit_partial_from_result(tr.get("result"))
            for tr in all_tool_results
            if tr.get("tool") == "fit_line_lfr"
        )
        inline_statistics_done = any(
            tr.get("tool") == "astro_statistics_toolbox"
            for tr in all_tool_results
        )
        inline_statistics_pending = inline_statistics_call is not None and not inline_statistics_done
        research_plan_done = any(
            tr.get("tool") == "plan_research_program"
            for tr in all_tool_results
        )
        research_matrix_done = any(
            tr.get("tool") == "run_research_matrix"
            for tr in all_tool_results
        )
        research_evidence_done = any(
            tr.get("tool") == "build_evidence_graph"
            for tr in all_tool_results
        )
        # 2026-05-28: when a cosmology direct-route trigger matches the
        # user prompt (e.g. "Hubble tension", "Alcock-Paczynski"), suppress
        # the research_program_workflow detour so the direct route below
        # fires unopposed. Otherwise research_plan_pending at line 4570
        # would inject plan_research_program first and the direct gate's
        # "not tool_calls_in_turn" guard would skip.
        research_plan_pending = (
            research_program_workflow
            and not research_plan_done
            and not cosmology_direct_route_calls
        )
        research_matrix_pending = (
            research_program_workflow
            and research_plan_done
            and cosmology_likelihood_workflow
            and not research_matrix_done
            and not cosmology_direct_route_calls
        )
        research_evidence_pending = (
            research_program_workflow
            and research_matrix_done
            and not research_evidence_done
        )
        cosmology_registry_done = any(
            tr.get("tool") == "list_cosmology_datasets"
            for tr in all_tool_results
        )
        cosmology_likelihood_config_done = any(
            tr.get("tool") in {
                "build_cosmology_likelihood",
                "build_cosmology_robustness_matrix",
            }
            for tr in all_tool_results
        )
        cosmology_likelihood_run_done = any(
            tr.get("tool") in {
                "run_cosmology_likelihood_chain",
                "run_cosmology_robustness_matrix",
                "run_dark_energy_evidence_matrix",
            }
            for tr in all_tool_results
        )
        cosmology_registry_pending = (
            cosmology_likelihood_workflow
            and not research_program_workflow
            and not cosmology_registry_done
        )
        cosmology_likelihood_config_pending = (
            cosmology_likelihood_workflow
            and cosmology_registry_done
            and not cosmology_likelihood_config_done
            and bool(cosmology_likelihood_build_calls)
        )
        cosmology_likelihood_run_pending = (
            cosmology_likelihood_workflow
            and cosmology_likelihood_config_done
            and not cosmology_likelihood_run_done
            and bool(cosmology_likelihood_run_calls)
        )
        attempted_table_ids = _table_extraction_arxiv_ids(all_tool_results)
        ranked_arxiv_candidates = _ranked_literature_arxiv_candidates(all_tool_results)
        if line_relation_workflow:
            # Verified seeds are a resilience mechanism for ADS/arXiv drift and
            # rate limits.  Include them even when broad search returns other
            # candidates, because those candidates often contain only abstracts,
            # reviews, or non-measurement tables.  They still have to pass
            # extract_literature_tables in this turn before any value is used.
            by_arxiv_id: dict[str, dict[str, Any]] = {}
            for candidate in [
                *_verified_line_relation_seed_candidates(latest_user_text),
                *ranked_arxiv_candidates,
            ]:
                arxiv_id = str(candidate.get("arxiv_id") or "")
                if not arxiv_id:
                    continue
                prev = by_arxiv_id.get(arxiv_id)
                if prev is None or int(candidate.get("score") or 0) > int(prev.get("score") or 0):
                    by_arxiv_id[arxiv_id] = candidate
            ranked_arxiv_candidates = sorted(
                by_arxiv_id.values(),
                key=lambda item: int(item.get("score") or 0),
                reverse=True,
            )
        remaining_ranked_candidates = [
            c for c in ranked_arxiv_candidates
            if str(c.get("arxiv_id") or "") not in attempted_table_ids
        ]
        arxiv_candidates = [
            str(c["arxiv_id"])
            for c in remaining_ranked_candidates
            if c.get("arxiv_id")
        ]
        force_table_extraction = (
            line_relation_workflow
            and (
                literature_searches_done >= 2
                or table_extraction_attempts_done > 0
            )
            and table_extraction_attempts_done < MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS
            and not has_fit_ready_line_measurements
            and any(t.get("name") == "extract_literature_tables" for t in visible_tools)
            and bool(arxiv_candidates)
        )
        line_relation_extraction_exhausted = (
            line_relation_workflow
            and not has_fit_ready_line_measurements
            and table_extraction_attempts_done >= MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS
        )
        line_relation_search_exhausted = (
            line_relation_workflow
            and not has_fit_ready_line_measurements
            and literature_searches_done >= 3
            and table_extraction_attempts_done == 0
            and not arxiv_candidates
        )
        if force_table_extraction:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") not in {
                    "search_literature",
                    "fit_line_lfr",
                    "run_python",
                    "compare_luminosity_distances",
                    "demagnify_sample",
                    "export_sample_table",
                }
            ]
        elif line_relation_workflow and not has_fit_ready_line_measurements:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") not in {
                    "fit_line_lfr",
                    "run_python",
                    "compare_luminosity_distances",
                    "demagnify_sample",
                    "export_sample_table",
                }
            ]
        if line_relation_workflow and (has_publication_ready_line_fit or has_partial_line_fit):
            visible_tools = [
                t for t in visible_tools
                if t.get("name") not in {
                    "search_literature",
                    "extract_literature_tables",
                    "fit_line_lfr",
                    "run_python",
                    "compare_luminosity_distances",
                    "demagnify_sample",
                    "export_sample_table",
                }
            ]
        if line_relation_extraction_exhausted or line_relation_search_exhausted:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") not in {
                    "search_literature",
                    "extract_literature_tables",
                    "fit_line_lfr",
                    "run_python",
                    "compare_luminosity_distances",
                    "demagnify_sample",
                    "export_sample_table",
                }
            ]
        if inline_statistics_pending:
            # The user supplied the numerical arrays directly. Use the
            # deterministic statistics tool as the citable path; do not let
            # the model detour through run_python and trigger synthetic output.
            visible_tools = [
                t for t in visible_tools
                if t.get("name") == "astro_statistics_toolbox"
            ]
        elif inline_statistics_call is not None and inline_statistics_done:
            visible_tools = []
        elif research_plan_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") == "plan_research_program"
            ]
        elif research_matrix_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") == "run_research_matrix"
            ]
        elif research_evidence_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") == "build_evidence_graph"
            ]
        elif cosmology_registry_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") == "list_cosmology_datasets"
            ]
        elif cosmology_likelihood_config_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") in {
                    "build_cosmology_likelihood",
                    "build_cosmology_robustness_matrix",
                }
            ]
        elif cosmology_likelihood_run_pending:
            visible_tools = [
                t for t in visible_tools
                if t.get("name") in {
                    "run_cosmology_likelihood_chain",
                    "run_cosmology_robustness_matrix",
                    "run_dark_energy_evidence_matrix",
                }
            ]
        elif cosmology_likelihood_workflow and cosmology_likelihood_config_done:
            visible_tools = []

        # Append a runtime note to the system message when any tools are
        # disabled this iteration, so the model understands why its previous
        # calls "disappeared" from the schema.
        if disabled_this_turn:
            system_this_call = (
                system
                + "\n\n[RUNTIME: the following tools have been removed from "
                + "your toolkit this turn because they failed "
                + f"{DISABLE_AFTER_FAILURES}+ times already: "
                + f"{disabled_this_turn}. Do NOT attempt to call them by "
                + "name (they are not in your schema). Either use a DIFFERENT "
                + "tool with DIFFERENT parameters, or emit "
                + "<tools_returned_nothing failed_tools='"
                + ",".join(disabled_this_turn) + "' ...>.]"
            )
            # G3.5: tell the frontend, via SSE, that tools have been disabled
            await _emit({
                "type": "tools_disabled",
                "agent": agent_name,
                "disabled": disabled_this_turn,
                "iteration": _iteration,
            })
        else:
            system_this_call = system

        if force_table_extraction:
            candidate_text = ", ".join(arxiv_candidates[:MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS])
            candidate_details = "; ".join(
                f"{c.get('arxiv_id')} (score {c.get('score')}: {str(c.get('title') or '')[:90]})"
                for c in remaining_ranked_candidates[:MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS]
            )
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: this is a line-luminosity/FWHM relation "
                + "workflow. You have candidate papers from literature search, "
                + "verified seed metadata, or a previous failed table extraction. "
                + "Abstract-level paper results cannot support measurement or "
                + "fit claims. Do NOT call search_literature again this iteration. "
                + "Your next tool call must be extract_literature_tables for one of the "
                + f"top-ranked candidate arXiv IDs: {candidate_text}. "
                + f"Candidate details: {candidate_details}. "
                + f"Do not exceed {MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS} "
                + "total table-extraction attempts in this turn. If extraction "
                + "returns no usable line_measurements, say that clearly; "
                + "do not create synthetic or hardcoded sample rows.]"
            )

        if line_relation_extraction_exhausted:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: this line-luminosity/FWHM relation workflow "
                + f"has already used {table_extraction_attempts_done} "
                + "table-extraction attempt(s) without any normalized "
                + "line_measurements. The search/extraction/fitting/Python "
                + "tools have been removed for this iteration to avoid "
                + "burning quota or inventing data. Your response must be an "
                + "honest boundary summary: literature searches found papers, "
                + "but no fit-ready measurement table was extracted this turn; "
                + "do not report slope/intercept/scatter/r/p or create a "
                + "synthetic demonstration.]"
            )

        if line_relation_search_exhausted:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: this line-luminosity/FWHM relation workflow "
                + f"has already used {literature_searches_done} literature "
                + "searches without any high-confidence arXiv candidate for "
                + "machine-readable line-measurement tables. Search and "
                + "analysis tools have been removed for this iteration. "
                + "Respond with an honest boundary summary and ask for a "
                + "specific arXiv ID / table source; do not report fitted "
                + "relation statistics.]"
            )

        if inline_statistics_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: the user supplied explicit x/y arrays and "
                + "asked for a common statistical regression. The only "
                + "available tool this iteration is astro_statistics_toolbox. "
                + "Call it with the supplied arrays; do not use run_python, "
                + "do not hand-calculate, and do not make qualitative "
                + "significance claims until the statistics tool returns.]"
            )
        elif inline_statistics_call is not None and inline_statistics_done:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: astro_statistics_toolbox already returned "
                + "the regression result for the user's inline arrays. Stop "
                + "calling tools and summarize only the tool-provided slope, "
                + "intercept, method, and residual diagnostics.]"
            )
        elif research_plan_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: this is a research-mode request. The only "
                + "available tool this iteration is plan_research_program. "
                + "Call it before any posterior/fit or literature summary. "
                + "The plan itself is not evidence for numerical claims.]"
            )
        elif research_matrix_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: a ResearchPlan exists for this turn. The only "
                + "available tool this iteration is run_research_matrix. Execute "
                + "the numerically executable preliminary cells and mark config-only "
                + "cells as not runnable. Do not invent missing likelihood results.]"
            )
        elif research_evidence_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: a research matrix has returned. The only "
                + "available tool this iteration is build_evidence_graph. Build "
                + "claim provenance for this turn before giving the final summary.]"
            )
        elif cosmology_registry_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: the user is asking about observational "
                + "cosmology datasets/likelihoods/model comparison. The only "
                + "available tool this iteration is list_cosmology_datasets. "
                + "Call it before literature search or prose so the answer is "
                + "grounded in the curated registry with versions, covariance, "
                + "units, applicability, source URLs, and citations.]"
            )
        elif cosmology_likelihood_config_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: the curated cosmology dataset registry has "
                + "already returned. The only available tools this iteration "
                + "are build_cosmology_likelihood and "
                + "build_cosmology_robustness_matrix. Build guarded configs "
                + "for the requested model/dataset combinations. These "
                + "configs are not posterior chains; do not quote posterior "
                + "constraints unless a later chain returns publication_ready=true.]"
            )
        elif cosmology_likelihood_run_pending:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: guarded cosmology likelihood configs already "
                + "returned. The only available tools this iteration are "
                + "run_cosmology_likelihood_chain and "
                + "run_cosmology_robustness_matrix. Execute the phase-1 "
                + "verified likelihood paths and role-approved priors/approximations. "
                + "Published posterior/proposal summaries are context-only and must "
                + "not enter chi-square. If datasets_not_run is non-empty, say those datasets "
                + "still need external Cobaya/CosmoSIS chains; do not imply "
                + "they are included in the numerical posterior.]"
            )
        elif cosmology_likelihood_workflow and cosmology_likelihood_config_done:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: cosmology registry/config tools already "
                + "returned for this turn. Stop calling tools unless a "
                + "numerical likelihood result is already present. Summarize "
                + "registered datasets, executable results, context-only records, and which "
                + "posterior/chain claims remain unsupported.]"
            )

        if (
            cosmology_likelihood_workflow
            and _cosmology_prompt_mentions_bao(latest_user_text)
            and (
                "cmb" in _cosmology_forbidden_probe_families(latest_user_text)
                or "h0" in _cosmology_forbidden_probe_families(latest_user_text)
            )
        ):
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: this is a BAO-only request without CMB "
                + "calibration and/or H0 priors. Do not introduce Planck "
                + "sound-horizon numbers, H0 values, or author-year citations "
                + "for excluded probes. State that absolute H0/rd-calibrated "
                + "claims are not determined by this tool turn.]"
            )

        if line_relation_workflow and has_publication_ready_line_fit:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: a publication-ready line-relation fit has "
                + "already succeeded this turn. Stop calling tools now. "
                + "Summarize the fitted slope/intercept/scatter with the "
                + "tool-provided citation/provenance, and explicitly mark "
                + "scope limitations such as single-survey coverage, missing "
                + "z<1 subsample, or missing lensing demagnification. Do not "
                + "start another literature-search or extraction loop.]"
            )

        if line_relation_workflow and has_partial_line_fit:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: fit_line_lfr has returned a real but PARTIAL "
                + "line-relation fit this turn. Stop calling tools now and "
                + "summarize the fit. Every sentence containing slope, "
                + "intercept/alpha, beta, intrinsic scatter, r, or p-value "
                + "MUST be introduced with exactly: "
                + "\"Exploratory only; not publication-ready:\". "
                + "Do not claim `publication_ready=true` for the overall fit; "
                + "if a nested sampler reports readiness, describe it only as "
                + "sampler convergence and keep the top-level fit partial. "
                + "Also state the scope limitations: ALPINE-only if only that "
                + "cache is present, empty z<1 split if applicable, and "
                + "missing/unknown lensing demagnification metadata.]"
            )

        if checkpoint_note:
            system_this_call = system_this_call + "\n\n" + checkpoint_note

        # PART Y Batch 5 + C-X2 (audit follow-up): synthetic_run_python_count
        # was previously incremented but never read; PART Y Batch 5 wired it
        # to a >=3 forced-abstention nudge; C-X2 tightens the threshold to
        # >=1 because the most recent audit caught a turn where the AI
        # reached for synthetic data once, hit the upstream guard ("I see
        # the system is directing me to use the cached literature data"),
        # and then kept thrashing in the cached data instead of either
        # finding another tool path or emitting <tools_returned_nothing/>.
        # Pushing for abstention as soon as the first SYNTHETIC run_python
        # appears prevents the "thrash in cached data" loop while still
        # allowing the model to recover with a DIFFERENT real-data tool.
        # We deliberately do NOT physically disable run_python (the model
        # still needs it for legitimate post-cache analysis), just push
        # hard for abstention.
        if synthetic_run_python_count >= 1:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: you have emitted "
                + f"{synthetic_run_python_count} SYNTHETIC run_python "
                + "call(s) this turn (data_source='none_not_analyzing_real_data' "
                + "or auto-tainted because upstream fetches failed). STOP "
                + "writing demo / synthetic code now. Either (a) call a "
                + "DIFFERENT real-data tool with DIFFERENT parameters, or "
                + "(b) emit <tools_returned_nothing/> with the failed_tools "
                + "list as your ENTIRE reply. Do not produce any more "
                + "SYNTHETIC output this turn.]"
            )
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "synthetic_run_python_excess_total", 1.0,
                    count=str(min(synthetic_run_python_count, 10)),
                    agent=agent_name,
                )
            except Exception:
                pass

        if fit_ready_literature_cache_keys:
            latest_cache = fit_ready_literature_cache_keys[-1]
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: extract_literature_tables has already returned "
                + f"fit-ready line_measurements cached as {latest_cache}. For any "
                + "[CII]/line-luminosity/FWHM relation, call "
                + f"fit_line_lfr(cache_key='{latest_cache}') next, or use "
                + f"run_python with data_source='cached:{latest_cache}' and "
                + "get_cached_results(). Do NOT declare "
                + "data_source='none_not_analyzing_real_data' or hardcode "
                + "ALPINE/REBELS/literature sample rows.]"
            )

        seconds_left = _loop_deadline - _time.monotonic()
        if seconds_left <= soft_reminder_s:
            system_this_call = (
                system_this_call
                + "\n\n[RUNTIME: you are close to the agent-loop deadline "
                + f"({max(0, int(seconds_left))}s left). Stop broad retries now. "
                + "Summarize the successful tool results already gathered, or emit "
                + "<tools_returned_nothing/> if the required data is still missing. "
                + "Do not start another broad archive query unless it is essential "
                + "and narrowly scoped.]"
            )
            if not soft_deadline_reminded:
                await _emit({
                    "type": "status",
                    "message": (
                        "Agent is near the workflow deadline; asking it to summarize "
                        "partial results instead of starting broad retries."
                    ),
                })
                soft_deadline_reminded = True

        try:
            response = await _llm_messages_create(
                system=system_this_call,
                messages=working_messages,
                tools=visible_tools,
                provider_api_keys=provider_api_keys,
                agent_name=agent_name,
                preferred_backend=preferred_backend,
                model_profile=model_profile,
            )
        except InferenceError as exc:
            # P0 (2026-05-26): tools may have completed successfully but the
            # final LLM synthesis call failed (all configured backends down).
            # Do not return an empty answer — emit a deterministic
            # tool-grounded summary built from the results already gathered.
            _synthesis_fallback = _tool_grounded_summary(
                all_tool_results, latest_user_text
            ) or ""
            if not _synthesis_fallback.strip():
                raise
            logger.warning(
                "Agent loop: synthesis backend failed (%s); emitting "
                "tool-grounded fallback. tool_results=%d",
                exc, len(all_tool_results),
            )
            text_parts.append(
                "The research tools completed, but the model's final language "
                "synthesis failed (all configured AI backends were "
                "unavailable). Below is the tool-grounded summary of what ran; "
                "no unsupported conclusion is made.\n\n" + _synthesis_fallback
            )
            break

        text = str(response.get("content", "") or "")
        tool_calls_in_turn: list[dict] = list(response.get("tool_calls") or [])
        forced_tool_call_override = False
        if inline_statistics_pending and (
            not tool_calls_in_turn
            or any(tc.get("name") != "astro_statistics_toolbox" for tc in tool_calls_in_turn)
        ):
            text = ""
            tool_calls_in_turn = [deepcopy(inline_statistics_call)]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Running the deterministic statistics toolbox on the "
                    "inline x/y arrays before summarizing the regression."
                ),
            })
        if research_plan_pending:
            text = ""
            tool_calls_in_turn = [{
                "id": f"auto_research_plan_{uuid.uuid4().hex}",
                "name": "plan_research_program",
                "input": {"question": latest_user_text},
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": "Planning the research program before running analyses.",
            })
        if research_matrix_pending:
            text = ""
            tool_calls_in_turn = [{
                "id": f"auto_research_matrix_{uuid.uuid4().hex}",
                "name": "run_research_matrix",
                "input": {
                    "research_plan": _research_plan_from_tool_results(all_tool_results),
                    "question": latest_user_text,
                },
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Executing the runnable cells of the research matrix and "
                    "marking config-only gaps."
                ),
            })
        if research_evidence_pending:
            text = ""
            tool_calls_in_turn = [{
                "id": f"auto_evidence_graph_{uuid.uuid4().hex}",
                "name": "build_evidence_graph",
                "input": {
                    "tool_results": _compact_tool_results_for_evidence(all_tool_results),
                },
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": "Building the claim provenance graph for this research turn.",
            })
        if cosmology_registry_pending:
            # Keep cosmology routing deterministic. Some backends call
            # list_cosmology_datasets without the narrowed dataset_keys input,
            # which floods the model with the entire registry and can trigger
            # memory/Python detours. The auto-selected key list is the safe
            # contract for the rest of this turn.
            registry_dataset_keys = _cosmology_dataset_keys_from_prompt(latest_user_text)
            requested_redshift = _cosmology_requested_redshift(latest_user_text)
            registry_input: dict[str, Any] = {}
            if registry_dataset_keys:
                registry_input["dataset_keys"] = registry_dataset_keys
            if requested_redshift is not None:
                registry_input["requested_redshift"] = requested_redshift
            text = ""
            tool_calls_in_turn = [{
                "id": f"auto_cosmo_registry_{uuid.uuid4().hex}",
                "name": "list_cosmology_datasets",
                "input": registry_input,
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Listing the curated observational-cosmology dataset "
                    "registry before summarizing dataset availability."
                ),
            })
        if cosmology_likelihood_config_pending:
            text = ""
            tool_calls_in_turn = deepcopy(cosmology_likelihood_build_calls)
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Building guarded cosmology likelihood configs for the "
                    "requested model/dataset combinations."
                ),
            })
        if cosmology_likelihood_run_pending:
            text = ""
            tool_calls_in_turn = deepcopy(cosmology_likelihood_run_calls)
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Running registered cosmology likelihood paths and "
                    "role-approved priors/approximations."
                ),
            })
        # 2026-05-28: cosmology direct-route early gate. Fires only on the
        # first iteration with no tool history. Bypasses both DeepSeek's and
        # Anthropic's first-call planner bias for explicit "Hubble tension"
        # / "Alcock-Paczynski" prompts. Mirrors _inline_statistics_tool_call
        # injection pattern (line 3029).
        if (
            _iteration == 0
            and not tool_calls_in_turn
            and not all_tool_results
            and cosmology_direct_route_calls
            and _active_research_focus() == "cosmology"
        ):
            text = ""
            tool_calls_in_turn = deepcopy(cosmology_direct_route_calls)
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    f"Direct-route trigger matched: calling "
                    f"`{cosmology_direct_route_calls[0]['name']}` "
                    "directly to bypass the planner detour."
                ),
            })
        if force_table_extraction and not tool_calls_in_turn and arxiv_candidates:
            # Some backends still choose to summarize despite the runtime
            # "next call must be extract_literature_tables" instruction.  For
            # line-relation workflows this strands a verified seed/candidate
            # and produces an honest-but-premature boundary answer.  Make the
            # routing deterministic: execute the top untried candidate, and
            # let the next iteration summarize or fit based on real results.
            text = ""
            forced_id = f"auto_extract_{uuid.uuid4().hex}"
            forced_arxiv = arxiv_candidates[0]
            tool_calls_in_turn = [{
                "id": forced_id,
                "name": "extract_literature_tables",
                "input": {"arxiv_id": forced_arxiv},
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Continuing line-relation workflow with the next ranked "
                    f"literature table candidate: arXiv:{forced_arxiv}."
                ),
            })
        if (
            line_relation_workflow
            and fit_ready_literature_cache_keys
            and not has_publication_ready_line_fit
            and not has_partial_line_fit
            and not tool_calls_in_turn
            and any(t.get("name") == "fit_line_lfr" for t in visible_tools)
        ):
            # Same principle as auto-extract above: once the platform has a
            # cited, fit-ready measurement cache, do not rely on the model to
            # remember the exact next tool call.  Execute the deterministic
            # fit path so the turn ends with a real publication-ready or
            # explicit partial fit, not a silent fallback summary.
            text = ""
            latest_cache = fit_ready_literature_cache_keys[-1]
            fit_input: dict[str, Any] = {
                "cache_key": latest_cache,
                "line_id": "[CII]",
                "fit_method_requested": _line_fit_method_from_prompt(latest_user_text),
                "variant_label": "auto_fit_from_literature_tables",
            }
            target_cosmology = _line_fit_cosmology_from_prompt(latest_user_text)
            if target_cosmology:
                fit_input["cosmology"] = target_cosmology
            subsample_splits = _line_fit_subsample_splits_from_prompt(latest_user_text)
            if subsample_splits:
                fit_input["subsample_splits"] = subsample_splits
            tool_calls_in_turn = [{
                "id": f"auto_fit_{uuid.uuid4().hex}",
                "name": "fit_line_lfr",
                "input": fit_input,
            }]
            forced_tool_call_override = True
            await _emit({
                "type": "status",
                "message": (
                    "Fitting the cached line-measurement table before summarizing "
                    "the line-luminosity/FWHM relation."
                ),
            })
        abstention_text_in_prose = (
            "<tools_returned_nothing" in text or "<toolsreturnednothing" in text
        )
        structured_abstention_reply = (
            _parse_abstention_tag(text) if abstention_text_in_prose else None
        )
        # Preserve a whole-reply structured tag for the canonical honest-
        # abstention branch after the loop.  Only sanitize leaked tags that
        # are embedded in ordinary prose; eagerly sanitizing every tag made
        # the structured branch unreachable after a tool call.
        if abstention_text_in_prose and structured_abstention_reply is None:
            text = _sanitize_tools_returned_nothing(text)
        if text:
            # Research/cosmology turns can produce draft prose immediately
            # before a tool call.  That draft may contain literature priors or
            # preliminary numbers that have not passed citation/fact checks.
            # Keep the process visible, but do not expose or append unverified
            # prose when the same turn is still executing tools.
            if tool_calls_in_turn and (research_program_workflow or cosmology_likelihood_workflow):
                await _emit({
                    "type": "agent_text",
                    "agent": agent_name,
                    "content": (
                        "Draft intermediate prose withheld until the current "
                        "research-tool results pass evidence and fact checks."
                    ),
                    "draft": True,
                    "not_claimable": True,
                })
            else:
                text_parts.append(text)
                if structured_abstention_reply is None:
                    await _emit({
                        "type": "agent_text",
                        "agent": agent_name,
                        "content": text,
                    })
        if tool_calls_in_turn and abstention_text_in_prose:
            break
        if not tool_calls_in_turn:
            # C-X1 path 1: this is the common truncation case — the model's
            # final text turn was cut off by max_tokens / length with no
            # tool_use blocks.  Record the stop_reason here so the truncation
            # gate below can detect it; the assignment further down only runs
            # when tool calls were present.
            last_stop_reason = response.get("stop_reason")
            break

        assistant_content = []
        # PART Z C6 — DeepSeek thinking-mode contract: stash the
        # reasoning_content the model produced this turn so the next
        # OpenAI-compatible request can echo it back. Anthropic/OpenAI
        # paths don't return reasoning_content; the block is dropped
        # silently on those providers via _normalize_openai_messages.
        reasoning_content = response.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content.strip():
            assistant_content.append({
                "type": "reasoning_content",
                "text": reasoning_content,
            })
        if text:
            assistant_content.append({"type": "text", "text": text})
        for tool_call in tool_calls_in_turn:
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "input": tool_call["input"],
                }
            )
            # Emit the tool_call event *before* dispatch so the UI can show
            # "Calling <tool>..." while the tool is still executing.
            await _emit({
                "type": "tool_call",
                "agent": agent_name,
                "tool": tool_call["name"],
                "input": tool_call["input"],
                "iteration": _iteration + 1,
                "max_iterations": max_iterations,
            })
        working_messages.append({"role": "assistant", "content": assistant_content})

        suppressed_tool_results: dict[str, dict] = {}
        real_tool_calls: list[dict] = []
        # Line-relation searches often need one broad topic query, one methods
        # query, and one narrowed survey/table query.  The previous cap of two
        # real calls could suppress the narrowed ALPINE/REBELS/arXiv query and
        # incorrectly force an honest-but-unhelpful "no usable literature"
        # answer.  Keep the anti-loop guard, but align it with the exhaustion
        # threshold below (>=3 searches with no candidates).
        search_calls_allowed_this_turn = max(0, 3 - literature_searches_done)
        search_calls_seen_this_turn = 0
        extract_calls_allowed_this_turn = max(
            0,
            MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS - table_extraction_attempts_done,
        )
        extract_calls_seen_this_turn = 0
        for tool_call in tool_calls_in_turn:
            tool_name = str(tool_call.get("name") or "")
            if (
                line_relation_workflow
                and not has_fit_ready_line_measurements
                and tool_name == "search_literature"
                and search_calls_seen_this_turn >= search_calls_allowed_this_turn
            ):
                suppressed_tool_results[tool_call["id"]] = {
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "input": tool_call["input"],
                    "result": _suppressed_line_relation_search_result(),
                }
            elif (
                line_relation_workflow
                and not has_fit_ready_line_measurements
                and tool_name == "extract_literature_tables"
                and extract_calls_seen_this_turn >= extract_calls_allowed_this_turn
            ):
                suppressed_tool_results[tool_call["id"]] = {
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "input": tool_call["input"],
                    "result": _suppressed_line_relation_extract_result(
                        MAX_LINE_RELATION_TABLE_EXTRACT_ATTEMPTS
                    ),
                }
            elif _should_suppress_line_measurement_synthetic_python(
                tool_call,
                fit_ready_cache_keys=fit_ready_literature_cache_keys,
                latest_user_text=latest_user_text,
                user_requested_synthetic_demo=user_requested_synthetic_demo,
            ):
                suppressed_tool_results[tool_call["id"]] = {
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "input": tool_call["input"],
                    "result": _suppressed_line_measurement_python_result(fit_ready_literature_cache_keys),
                }
            else:
                real_tool_calls.append(tool_call)
                if line_relation_workflow and tool_name == "search_literature":
                    search_calls_seen_this_turn += 1
                if line_relation_workflow and tool_name == "extract_literature_tables":
                    extract_calls_seen_this_turn += 1

        tool_result_blocks = []
        real_executed_tools = await _execute_tool_calls(
            real_tool_calls,
            provider_api_keys.get("anthropic", ""),
            provider_api_keys,
            python_session_id,
            user_id=user_id,
            chat_session_id=chat_session_id,
            on_event=on_event,
            loop_deadline=_loop_deadline,
            summary_reserve_s=summary_reserve_s,
            workflow_budget_mode=budget_mode,
            tool_deadline_scale=float(budget.get("tool_deadline_scale", 1.0)),
            turn_tool_results=all_tool_results,
        ) if real_tool_calls else []
        real_by_id = {tc["id"]: tc for tc in real_executed_tools}
        executed_tools = [
            suppressed_tool_results.get(tc["id"]) or real_by_id[tc["id"]]
            for tc in tool_calls_in_turn
        ]
        for tc in executed_tools:
            result = tc["result"]
            tool_name = tc.get("name", "")

            if tool_name == "extract_literature_tables" and isinstance(result, dict):
                measurement_count = _line_measurement_count_from_result(result)
                if measurement_count > 0:
                    cache_key = str(result.get("cache_key") or "latest_literature_tables")
                    if cache_key not in fit_ready_literature_cache_keys:
                        fit_ready_literature_cache_keys.append(cache_key)

            # G3.1 + H0.7: mark data-fetch failures for the G3.4 disable gate.
            # H0.7: timeouts / payload_too_large / row_count=0 are "soft"
            # failures — the AI can legitimately retry with smaller TOP or
            # narrower cone.  Only count connector errors (real upstream
            # failure that's not the AI's fault) toward the disable counter.
            if tool_name in _DATA_FETCH_TOOLS and isinstance(result, dict):
                status_tokens: list[str] = []
                for key in ("analysis_status", "__tool_status__", "status"):
                    v = result.get(key)
                    if isinstance(v, str):
                        status_tokens.append(v.upper())
                err_str = str(result.get("error") or "").lower()
                err_class = str(result.get("error_class") or "").lower()
                # "Retryable" / soft failures — user/AI can adjust parameters.
                # 2026-05-20: explicit hard-reject error_class short-circuit — regardless
                # of what the error string looks like, these classes always count as hard,
                # preventing future error messages containing keywords like "too large" from
                # being incorrectly classified as soft.
                soft_failure = (
                    err_class not in _HARD_REJECT_ERROR_CLASSES
                    and (
                        "timeout" in err_str or "timed out" in err_str
                        or "retry budget" in err_str
                        or "payload_too_large" in err_class
                        or "too large" in err_str
                        or result.get("row_count") == 0  # empty result is not a connector failure
                        or result.get("found") == 0
                        or any(s == "EMPTY" for s in status_tokens)
                    )
                )
                empty_failure = _is_failed_or_empty_data_fetch(result)
                hard_failure = (
                    result.get("success") is False
                    or bool(result.get("error"))
                    or any(s in {"FAILED", "UNAVAILABLE"} for s in status_tokens)
                ) and not soft_failure

                if empty_failure:
                    empty_data_fetches.add(tool_name)
                if hard_failure:
                    tool_failure_counts[tool_name] = tool_failure_counts.get(tool_name, 0) + 1

            # G3.2: if any data-fetch failed this turn and the AI now runs
            # run_python without declaring a real source, treat that call as
            # EMPTY instead of showing a synthetic/demo replacement.  The
            # intended next step is an honest <tools_returned_nothing/>.
            if tool_name == "run_python" and isinstance(result, dict):
                failed_data_fetches = {
                    n for n, c in tool_failure_counts.items() if c > 0
                } | set(empty_data_fetches)
                declared = str(tc.get("input", {}).get("data_source", "")).strip()
                is_real_source_declared = declared in {
                    "latest_adql", "latest_search", "latest_lightcurve",
                    "latest_sdss_sql", "latest_high_velocity_stars",
                } or declared.startswith(("cached:", "fits:"))
                declared_empty_dependency = bool(empty_data_fetches & {
                    "latest_adql": {"run_adql"},
                    "latest_search": {"search_objects", "get_object_info", "get_object_dossier"},
                    "latest_lightcurve": {"search_lightcurve"},
                    "latest_sdss_sql": {"run_sdss_sql"},
                    "latest_high_velocity_stars": {"query_high_velocity_stars"},
                }.get(declared, set()))
                origin = str(result.get("data_origin") or "").lower()
                has_real_origin = origin in {"real_archive", "cached_real", "user_uploaded"}
                # PART AC C3 — code-content reverse exemption.
                # M3 audit caught the SYNTHETIC banner mis-tagging a
                # run_python that was processing real cached literature
                # tables (`get_cached_results("latest_literature_tables")`).
                # The AI hadn't declared a `data_source` on input and an
                # earlier search_literature returned 0 hits (entered
                # failed_data_fetches), so G3.2 tainted the result. But
                # the code body explicitly read a real cache helper —
                # the right answer is to inspect the code, not just the
                # input.data_source declaration.
                reads_real_cache_in_code = _run_python_code_reads_real_cache(
                    str(tc.get("input", {}).get("code") or "")
                )
                if (
                    failed_data_fetches
                    and not user_requested_synthetic_demo
                    and not has_real_origin
                    and not reads_real_cache_in_code
                    and (not is_real_source_declared or declared_empty_dependency)
                ):
                    # Replace the payload so stdout from fallback/demo code
                    # cannot be displayed as if it were a successful analysis.
                    empty_banner = {
                        "__tool_status__": "EMPTY",
                        "__do_not_claim__": True,
                        "__message_to_model__": (
                            f"Tool `run_python` produced no citeable data because "
                            f"data-fetch tools {sorted(failed_data_fetches)} failed "
                            f"earlier this turn and this call did not read a real "
                            f"data source. You MUST NOT use any facts, numbers, "
                            f"historical context, literature priors, physical "
                            f"interpretations, or conclusions from this call. "
                            f"Emit <tools_returned_nothing/> with the failed tool "
                            f"names instead of substituting synthetic data."
                        ),
                        "__suggested_next_step__": (
                            "Report that the requested real data could not be retrieved "
                            "and ask the user to narrow the query or try again later."
                        ),
                        "data_origin": "unavailable",
                        "analysis_status": "empty",
                        "row_count": 0,
                        "success": True,
                    }
                    suppressed_stdout = str(result.get("stdout") or "")
                    result = dict(empty_banner)
                    if suppressed_stdout.strip():
                        result["suppressed_stdout_preview"] = suppressed_stdout[:500]
                    tc = {**tc, "result": result}
                    synthetic_run_python_count += 1
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "empty_after_failed_fetch_total", 1.0,
                            failed_tool=",".join(sorted(failed_data_fetches))[:80],
                            agent=agent_name,
                        )
                    except Exception:
                        pass

            result_str = json.dumps(result, default=str)
            if len(result_str) > 16000:
                # Field-level truncation: recursively shrink long strings/lists
                # while preserving dict structure so the AI can keep analyzing.
                def _truncate_value(v, depth=0):
                    if depth > 4:
                        return "[depth-limit]"
                    if isinstance(v, str) and len(v) > 2000:
                        return v[:2000] + f"... [truncated {len(v) - 2000} chars]"
                    if isinstance(v, list):
                        if len(v) > 50:
                            return [_truncate_value(x, depth + 1) for x in v[:50]] + [
                                f"... [truncated {len(v) - 50} items]"
                            ]
                        return [_truncate_value(x, depth + 1) for x in v]
                    if isinstance(v, dict):
                        return {k: _truncate_value(val, depth + 1) for k, val in v.items()}
                    return v
                truncated_result = _truncate_value(result) if isinstance(result, (dict, list)) else result
                result_str = json.dumps(truncated_result, default=str)
                # If still too large, emit a valid JSON envelope with a text preview.
                # The previous hard cap spliced raw bytes onto a JSON string, which
                # breaks whenever the cut falls inside a string literal or escape
                # sequence — corrupting the LLM's tool-result input.
                if len(result_str) > 24000:
                    result_str = json.dumps({
                        "__truncated__": True,
                        "original_size": len(result_str),
                        "preview": result_str[:20000],
                        "note": "Result exceeded 24 KB after field-level truncation; see preview.",
                    })
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_str,
                }
            )
            all_tool_results.append(
                {
                    "id": tc["id"],
                    "tool": tc["name"],
                    "input": tc["input"],
                    "result": result,
                }
            )
            internal_suppressed = (
                isinstance(result, dict)
                and bool(result.get("__internal_suppressed__"))
            )
            checkpoint_event = None if internal_suppressed else _record_tool_checkpoint(
                chat_session_id=chat_session_id,
                python_session_id=python_session_id,
                tool_call=tc,
                result=result,
            )
            if checkpoint_event is not None:
                await _emit({
                    "type": "workflow_checkpoint",
                    "agent": agent_name,
                    **checkpoint_event,
                })
            # Stream the result immediately so the UI can update inline.
            # `live: true` distinguishes this from the final consolidated
            # tool_result events the SSE generator emits at the end — the
            # frontend uses the flag to deduplicate (live -> thinking UI,
            # final -> actions list).
            if not internal_suppressed:
                await _emit({
                    "type": "tool_result",
                    "agent": agent_name,
                    "tool": tc["name"],
                    "result": result,
                    "live": True,
                    "tool_call_id": tc["id"],
                })
        working_messages.append({"role": "user", "content": tool_result_blocks})
        # Claude uses "tool_use", OpenAI uses "tool_calls" as stop reason
        if (
            not forced_tool_call_override
            and response.get("stop_reason") not in ("tool_use", "tool_calls")
        ):
            last_stop_reason = response.get("stop_reason")
            break
    else:
        # The for-loop ran all `max_iterations` iterations without hitting any
        # break — i.e. the model still wanted to continue when the budget ran
        # out.  This is the only true "hit iteration cap" case.  Any clean
        # completion (no tool calls, abstention, synthesis failure, non-tool
        # stop_reason) leaves the loop via `break` and skips this `else`.
        hit_iteration_cap = True

    full_reply = "\n\n".join(text_parts)

    # C-X1 / PART AC C2 — truncation gate (max_tokens OR text-shape).
    # When the model's reply hits the max_tokens budget mid-sentence
    # (e.g. stops on a colon: "Let me extract the next table:") the old
    # code shipped the dangling text straight to the UI as if it were
    # a finished reply. This is the R2.6 / R2.10 / M3 silent-truncation
    # regression.
    #
    # Two detection paths:
    # 1. Provider stop_reason = max_tokens (Anthropic) / length (OpenAI / DeepSeek).
    # 2. PART AC C2: text-shape — the reply self-evidently ends mid-sentence
    #    (trailing colon / comma / em-dash / connective word) even when
    #    the provider claimed stop_reason="stop". M3 audit caught this
    #    exact case: stop_reason was clean but the prose ended on
    #    "Let me search for additional [CII] datasets:".
    truncated_by_stop_reason = last_stop_reason in {"max_tokens", "length"}
    truncated_by_shape = (
        not truncated_by_stop_reason
        and _reply_looks_truncated(full_reply)
    )
    if (truncated_by_stop_reason or truncated_by_shape) and full_reply.strip():
        truncation_reason = (
            str(last_stop_reason) if truncated_by_stop_reason else "text_shape"
        )
        try:
            from app.observability.metrics import record_counter
            record_counter(
                "max_tokens_truncation_total", 1.0,
                agent=agent_name, stop_reason=truncation_reason,
            )
        except Exception:
            pass
        await _emit({
            "type": "reply_truncated",
            "agent": agent_name,
            "stop_reason": truncation_reason,
            "partial_chars": len(full_reply),
        })
        try:
            safe_summary_user = (
                "Your previous reply was cut off mid-sentence by the "
                "model's max_tokens limit. In <= 3 sentences, write a "
                "summary that:\n"
                "1) Names what you actually completed using tool_results "
                "this turn (cite bibcodes / cache_keys when relevant).\n"
                "2) Names what you were ABOUT to do but did not complete "
                "(e.g. 'I had not yet called compare_luminosity_distances "
                "for Riess 2011').\n"
                "3) Suggests the user re-prompts with the next concrete "
                "step. Do NOT continue the original analysis — just "
                "summarise + hand off."
            )
            summary_messages = list(working_messages) + [
                {"role": "assistant", "content": full_reply},
                {"role": "user", "content": safe_summary_user},
            ]
            summary_resp = await inference_router.route(
                agent_name,
                summary_messages,
                system=system,
                tools=[],  # text-only — no tool calls allowed
                provider_api_keys=provider_api_keys,
                preferred_backend=preferred_backend,
                model_profile=model_profile,
                max_tokens=400,
                temperature=0.0,
                backend_timeout=30.0,
            )
            text_blocks = summary_resp.get("text") or summary_resp.get("content") or ""
            safe_summary = str(text_blocks).strip()
        except Exception as exc:
            logger.warning("safe-summary regen failed: %s", exc)
            safe_summary = ""

        truncation_banner = (
            "\n\n---\n\n"
            "*[The model's reply was cut off mid-sentence by its "
            "max_tokens limit. The platform regenerated a safe summary "
            "of what was actually completed and what remains to do "
            "next:]*\n\n"
        )
        if safe_summary:
            full_reply = full_reply.rstrip() + truncation_banner + safe_summary
        else:
            full_reply = full_reply.rstrip() + (
                "\n\n---\n\n*[Reply was truncated mid-sentence by the "
                "max_tokens limit; safe-summary regen also returned "
                "empty. Please re-ask with a narrower scope.]*"
            )

    actions = _parse_actions(full_reply)
    clean_reply = _strip_actions_from_reply(full_reply)

    # F2.3: structured abstention parser.  If the model emitted a single
    # <tools_returned_nothing/> tag as its reply, that IS the expected
    # response for empty/failed turns — skip the claim validator entirely
    # and render a canonical abstention card.
    abstention_payload = _parse_abstention_tag(clean_reply)
    if abstention_payload is not None:
        # R2 hard line (audit 2026-07-03): this branch skips the claim
        # validator, so scrub model-authored numeric claims out of the
        # attributes BEFORE they reach the card and the honest_abstention
        # SSE payload (the frontend renders both).
        abstention_payload, _abst_dropped_attrs = (
            _abstention_attrs_without_numeric_claims(abstention_payload)
        )
        if _abst_dropped_attrs:
            logger.warning(
                "Abstention tag from %s carried numeric claims in %s — "
                "attribute(s) withheld from the honest-abstention card",
                agent_name, _abst_dropped_attrs,
            )
        reason = _classify_abstention_reason(all_tool_results)
        try:
            from app.observability.metrics import record_counter
            record_counter(
                "honest_abstention_total", 1.0,
                agent=agent_name, reason=reason,
            )
            record_counter("structured_abstention_emitted_total", 1.0, agent=agent_name)
            if _abst_dropped_attrs:
                record_counter(
                    "abstention_numeric_attrs_withheld_total", 1.0,
                    agent=agent_name,
                )
        except Exception:
            pass
        logger.info(
            "Honest abstention emitted by %s (reason=%s): failed=%s empty=%s",
            agent_name, reason,
            abstention_payload.get("failed_tools", ""),
            abstention_payload.get("empty_tools", ""),
        )
        abstention_card = _render_abstention_card(abstention_payload, reason)
        identity_reply, abstention_identity_enforced = (
            _enforce_cosmology_dataset_identity(
                abstention_card, all_tool_results, latest_user_text
            )
        )
        clean_reply = (
            identity_reply + "\n\n---\n\n" + abstention_card
            if abstention_identity_enforced
            else abstention_card
        )
        visible_abstention_payload = dict(abstention_payload)
        if abstention_identity_enforced:
            # The frontend renders the structured abstention card instead of
            # ``reply``.  Put the platform-authored, tool-grounded disclosure
            # into the card's existing rationale field so it remains visible
            # without trusting model-supplied numeric prose.
            original_rationale = str(
                visible_abstention_payload.get("rationale") or ""
            ).strip()
            visible_abstention_payload["rationale"] = "\n\n".join(
                part for part in (original_rationale, identity_reply) if part
            )
        if on_event is not None:
            try:
                await on_event({
                    "type": "honest_abstention",
                    "payload": {
                        **visible_abstention_payload,
                        "reason": reason,
                        "agent": agent_name,
                        "reply_overridden_by_dataset_identity": (
                            abstention_identity_enforced
                        ),
                    },
                })
            except Exception:
                pass
        # Attach tool-result action cards as usual, then short-circuit out.
        actions.extend(_tool_results_to_actions(all_tool_results))
        abstention_validation_summary = _not_run_validation_summary(
            (
                "honest_abstention_dataset_identity_disclosure"
                if abstention_identity_enforced
                else "honest_abstention"
            )
        )
        if abstention_identity_enforced:
            abstention_validation_summary["interventions"] = [{
                "gate": "dataset_identity",
                "action": "disclosed_substitution",
                "reason": "requested_release_mismatch",
            }]
        return {
            "reply": clean_reply,
            "actions": actions,
            "tool_results": all_tool_results,
            "hit_iteration_cap": False,
            "hit_deadline": hit_deadline,
            "honest_abstention": True,
            "abstention_reason": reason,
            # The abstention card path skips the claim validator by design
            # (its prose is platform-written and claim-scrubbed above).
            "validation_summary": abstention_validation_summary,
        }
    if "<tools_returned_nothing" in clean_reply or "<toolsreturnednothing" in clean_reply:
        clean_reply = _sanitize_tools_returned_nothing(clean_reply)

    # R2: zero-fabrication gate.  Validate every numeric claim in the reply
    # against the tool_results collected this turn; if any claim can't be
    # cited, push the LLM to regenerate.  After two failures, block.
    fabrication_stats = {"pass": 0, "blocked": False, "regenerations": 0}
    # 2026-07-03 honesty surfacing: compact record of every gate
    # intervention this turn.  Feeds the per-reply validation_summary in
    # the final payload; read-only, derived from the same _gate_event
    # calls the observability layer already makes.
    gate_interventions: list[dict] = []

    async def _gate_event(
        gate: str,
        action: str,
        *,
        reason: str = "",
        details: dict | None = None,
        claims=None,
        violations=None,
        universe_size: int | None = None,
        draft: str = "",
        final: str = "",
    ) -> None:
        # Structured gate-intervention record — the false-positive triage
        # layer (app/observability/gate_events.py). One event per gate
        # intervention: JSONL append + gate_event_total counter + SSE emit.
        # Must NEVER affect the reply; everything is wrapped.
        try:
            gate_interventions.append({
                "gate": str(gate),
                "action": str(action),
                "reason": str(reason or ""),
            })
        except Exception:
            pass
        try:
            from app.observability.gate_events import (
                append_gate_event_jsonl,
                build_gate_event,
                claims_to_dicts,
                violations_to_dicts,
            )
            from app.observability.metrics import record_counter

            det = dict(details or {})
            if claims:
                det["claims"] = claims_to_dicts(claims)
            if violations:
                det["violations"] = violations_to_dicts(violations)
            evt = build_gate_event(
                gate=gate,
                action=action,
                reason=reason,
                agent=agent_name,
                details=det,
                tools_run=[
                    str(r.get("tool")) for r in all_tool_results
                    if isinstance(r, dict) and r.get("tool")
                ],
                universe_size=universe_size,
                regenerations=int(fabrication_stats.get("regenerations", 0)),
                draft=draft,
                final=final,
                chat_session_id=str(chat_session_id) if chat_session_id else None,
                python_session_id=str(python_session_id) if python_session_id else None,
            )
            append_gate_event_jsonl(evt)
            record_counter("gate_event_total", 1.0, gate=gate, action=action)
            await _emit(evt)
        except Exception as exc:
            logger.debug("gate_event emission failed: %s", exc)
    # The tool-inventory bypass exists so describing the real tool schema
    # (tool names, parameter defaults, counts) is not false-flagged by the
    # numeric/citation gate.  But it must NOT become a phrasing-conditioned
    # escape hatch: a prompt like "what tools are available, and what is the
    # Planck H0?" matches the inventory markers yet the reply can still ship
    # a fabricated scientific number / citation.  So only honor the skip when
    # the reply does not trip the load-bearing hard gates (zero-data
    # quantitative block + blocking citation violations); otherwise run the
    # full gate regardless of how the prompt was phrased.
    #
    # Audit 2026-07-03: the re-check above was ASYMMETRIC — on a data-bearing
    # turn zero_data_but_quantitative returns [] (the turn is not empty) and
    # the citation check ignores bare numbers, so "which tools ... also run
    # the chain and report the constraints" skipped validate_claims entirely
    # and a fabricated number could ship.  The skip is now only honored on
    # turns with NO claimable tool data (pure inventory answers); any turn
    # where a tool produced real payload runs the full gate stack.
    skip_gate = skip_claim_gate_for_meta
    if skip_gate and clean_reply.strip():
        from app.services.claim_validator import (
            is_empty_turn as _iet_meta,
            zero_data_but_quantitative as _zdq_meta,
            provenance_citation_violations as _pcv_meta,
            citation_violations_should_block as _cvsb_meta,
        )
        if (
            not _iet_meta(all_tool_results)
            or _zdq_meta(clean_reply, all_tool_results)
            or _cvsb_meta(_pcv_meta(clean_reply, all_tool_results))
        ):
            skip_gate = False
    if clean_reply.strip() and not skip_gate:
        from app.services.claim_validator import (
            validate_claims,
            build_regeneration_prompt,
            build_english_regeneration_prompt,
            build_zero_data_qualitative_regeneration_prompt,
            blocked_reply_with_narrative,
            attach_draft_to_banner,
            zero_data_but_quantitative,
            is_empty_turn,
            literature_prior_violations,
            reply_contains_cjk,
            provenance_citation_violations,
            citation_violations_should_block,
            blocked_citation_reply_text,
            unsupported_literature_narrative_violations,
            blocked_unsupported_narrative_reply_text,
            # Stage 6 P0c-C: second-pass hard barrier
            unclassified_literature_violations,
            blocked_unclassified_literature_reply_text,
            # M6: methodology cross-check (Bayesian / demagnify count)
            methodology_consistency_violations,
        )

        # F1.4: zero-data hard block.  If every tool call this turn was
        # failed / empty / errored but the reply still makes numeric
        # claims, short-circuit to the block path.  Pleiades case: AI
        # wrote "776 stars, 7.353 ± 0.001 mas" after run_adql returned
        # 0 rows and run_python crashed — the regen loop would have
        # laundered the claim by rewriting with different phrasing.
        # X (PART X plan D): force replies to English. Any final reply containing
        # CJK / Japanese / Korean / full-width punctuation (threshold: 3 characters)
        # is hard-blocked immediately. This is the highest-priority branch — because
        # downstream zero-hallucination gate regexes are almost entirely English,
        # Chinese prose bypasses all claim extraction. The prompt already instructs
        # the AI that "reply must be English"; this is the hard-constraint safety net.
        cjk_detected = reply_contains_cjk(clean_reply)
        if cjk_detected:
            _cjk_draft = clean_reply
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "fabrication_blocked_total",
                    1.0,
                    agent=agent_name,
                    reason="non_english_reply",
                )
            except Exception:
                pass
            # PART X plan D (revised): instead of hard-blocking a non-English
            # draft outright, attempt one English regeneration that preserves
            # every number and citation.  Only fall back to the hard block if
            # the rewrite fails or is still non-English.  Keeps non-English
            # prompts useful while preserving the English-only contract the
            # downstream zero-fabrication regex gate depends on.
            english_retry = ""
            try:
                english_messages = list(working_messages) + [
                    {"role": "assistant", "content": clean_reply},
                    {"role": "user", "content": build_english_regeneration_prompt()},
                ]
                english_regen = await _llm_messages_create(
                    system=system,
                    messages=english_messages,
                    tools=[],
                    provider_api_keys=provider_api_keys,
                    agent_name=agent_name,
                    preferred_backend=preferred_backend,
                    model_profile=model_profile,
                )
                english_retry = str(english_regen.get("content", "") or "").strip()
            except Exception as exc:
                logger.warning("English regeneration failed: %s", exc)
            if english_retry and not reply_contains_cjk(english_retry):
                clean_reply = english_retry
                cjk_detected = False
                fabrication_stats["regenerations"] += 1
                logger.info(
                    "Non-English reply from %s recovered via one English "
                    "regeneration",
                    agent_name,
                )
                await _gate_event(
                    "cjk_filter", "regenerated", reason="non_english_reply",
                    draft=_cjk_draft, final=clean_reply,
                )
            else:
                logger.error(
                    "Non-English reply from %s — hard-blocking after failed "
                    "English regeneration (CJK / full-width characters "
                    "detected; platform contract requires English-only)",
                    agent_name,
                )
                clean_reply = (
                    "⚠ Reply blocked by platform policy: the assistant's final "
                    "reply must be in standard English.  Non-English characters "
                    "(Chinese / Japanese / Korean / full-width) were detected "
                    "in the draft reply.\n\n"
                    "This is a one-time notice; the assistant will regenerate "
                    "in English on the next turn.  If you prefer a different "
                    "language, please use an external translator — this is a "
                    "research-tool platform and English is the working "
                    "language for citation integrity (the zero-fabrication "
                    "numeric-claim gate only ships English regex patterns)."
                )
                fabrication_stats["blocked"] = True
                await _gate_event(
                    "cjk_filter", "blocked", reason="non_english_reply",
                    draft=_cjk_draft, final=clean_reply,
                )

        zero_data_claims = [] if cjk_detected else zero_data_but_quantitative(clean_reply, all_tool_results)
        if zero_data_claims:
            _zd_draft = clean_reply
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "zero_data_but_claims_total",
                    1.0,
                    agent=agent_name,
                    claim_count=str(len(zero_data_claims)),
                )
            except Exception:
                pass
            logger.error(
                "Zero-data turn with %d quantitative claim(s) from %s — "
                "hard-blocking: %s",
                len(zero_data_claims), agent_name,
                [c.label for c in zero_data_claims],
            )
            validation = validate_claims(clean_reply, all_tool_results)
            qualitative_rewrite = ""
            if validation.uncited:
                # PART AH: zero-data turns can still answer methodological
                # questions qualitatively.  Try one text-only rewrite that
                # strips every unsupported number instead of immediately
                # replacing the whole answer with a withheld block.
                rewrite_messages = list(working_messages) + [
                    {"role": "assistant", "content": clean_reply},
                    {
                        "role": "user",
                        "content": build_zero_data_qualitative_regeneration_prompt(validation),
                    },
                ]
                try:
                    regen = await _llm_messages_create(
                        system=system,
                        messages=rewrite_messages,
                        tools=[],
                        provider_api_keys=provider_api_keys,
                        agent_name=agent_name,
                        preferred_backend=preferred_backend,
                        model_profile=model_profile,
                    )
                    qualitative_rewrite = str(regen.get("content", "") or "").strip()
                except Exception as exc:
                    logger.warning("Zero-data qualitative rewrite failed: %s", exc)

            if qualitative_rewrite:
                rewrite_validation = validate_claims(qualitative_rewrite, all_tool_results)
                rewrite_zero_data_claims = zero_data_but_quantitative(
                    qualitative_rewrite, all_tool_results,
                )
                rewrite_citation_violations = provenance_citation_violations(
                    qualitative_rewrite, all_tool_results,
                )
                rewrite_unsupported_narrative = unsupported_literature_narrative_violations(
                    qualitative_rewrite, all_tool_results,
                )
                if (
                    rewrite_validation.ok
                    and not rewrite_zero_data_claims
                    and not citation_violations_should_block(rewrite_citation_violations)
                    and not rewrite_unsupported_narrative
                ):
                    clean_reply = qualitative_rewrite
                    fabrication_stats["regenerations"] += 1
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "zero_data_qualitative_rewrite_total",
                            1.0,
                            agent=agent_name,
                        )
                    except Exception:
                        pass
                    await _gate_event(
                        "zero_data", "regenerated", reason="qualitative_rewrite",
                        claims=zero_data_claims, universe_size=validation.universe_size,
                        draft=_zd_draft, final=clean_reply,
                    )
                elif rewrite_citation_violations and citation_violations_should_block(rewrite_citation_violations):
                    # Stage 6 P0a follow-up: preserve AI's rewrite narrative
                    # (banner-only behavior dropped the draft entirely)
                    clean_reply = attach_draft_to_banner(
                        blocked_citation_reply_text(rewrite_citation_violations),
                        qualitative_rewrite,
                    )
                    fabrication_stats["blocked"] = True
                    await _gate_event(
                        "zero_data", "blocked", reason="rewrite_citation",
                        claims=zero_data_claims, violations=rewrite_citation_violations,
                        universe_size=validation.universe_size,
                        draft=_zd_draft, final=clean_reply,
                    )
                elif rewrite_unsupported_narrative:
                    # Stage 6 P0a follow-up: preserve AI's rewrite narrative
                    clean_reply = attach_draft_to_banner(
                        blocked_unsupported_narrative_reply_text(rewrite_unsupported_narrative),
                        qualitative_rewrite,
                    )
                    fabrication_stats["blocked"] = True
                    await _gate_event(
                        "zero_data", "blocked", reason="rewrite_narrative",
                        claims=zero_data_claims, violations=rewrite_unsupported_narrative,
                        universe_size=validation.universe_size,
                        draft=_zd_draft, final=clean_reply,
                    )
                else:
                    # Stage 6 P0: preserve AI's qualitative rewrite narrative
                    clean_reply = blocked_reply_with_narrative(
                        rewrite_validation, qualitative_rewrite,
                    )
                    fabrication_stats["blocked"] = True
                    await _gate_event(
                        "zero_data", "blocked", reason="rewrite_residual",
                        claims=(rewrite_validation.uncited or rewrite_zero_data_claims or zero_data_claims),
                        universe_size=rewrite_validation.universe_size,
                        draft=_zd_draft, final=clean_reply,
                    )
            else:
                # Stage 6 P0: preserve AI's original reply narrative
                clean_reply = blocked_reply_with_narrative(validation, clean_reply)
                fabrication_stats["blocked"] = True
                await _gate_event(
                    "zero_data", "blocked", reason="no_rewrite",
                    claims=(validation.uncited or zero_data_claims),
                    universe_size=validation.universe_size,
                    draft=_zd_draft, final=clean_reply,
                )
            try:
                from app.observability.metrics import record_counter
                if fabrication_stats["blocked"]:
                    record_counter(
                        "fabrication_blocked_total",
                        1.0,
                        agent=agent_name,
                        reason="zero_data",
                    )
            except Exception:
                pass

        elif (
            unsupported_narrative_claims := unsupported_literature_narrative_violations(
                clean_reply, all_tool_results
            )
        ):
            logger.error(
                "Unsupported narrative gate BLOCKED reply from %s (%d violations)",
                agent_name,
                len(unsupported_narrative_claims),
            )
            _un_draft = clean_reply
            tool_grounded_summary = (
                _research_tool_grounded_summary(all_tool_results)
                or _line_lfr_tool_grounded_summary(all_tool_results)
                or _statistics_tool_grounded_summary(all_tool_results)
                or _cosmology_tool_grounded_summary(all_tool_results, latest_user_text)
            )
            if tool_grounded_summary:
                summary_validation = validate_claims(tool_grounded_summary, all_tool_results)
                summary_citation_violations = provenance_citation_violations(
                    tool_grounded_summary, all_tool_results,
                )
                summary_unsupported = unsupported_literature_narrative_violations(
                    tool_grounded_summary, all_tool_results,
                )
                if (
                    summary_validation.ok
                    and not citation_violations_should_block(summary_citation_violations)
                    and not summary_unsupported
                ):
                    clean_reply = tool_grounded_summary
                    fabrication_stats["regenerations"] += 1
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "tool_grounded_regeneration_total",
                            1.0,
                            agent=agent_name,
                            reason="unsupported_narrative",
                        )
                    except Exception:
                        pass
                    await _gate_event(
                        "unsupported_narrative", "downgraded_summary",
                        violations=unsupported_narrative_claims,
                        draft=_un_draft, final=clean_reply,
                    )
                else:
                    # Stage 6 P0a follow-up: preserve AI's reply narrative
                    clean_reply = attach_draft_to_banner(
                        blocked_unsupported_narrative_reply_text(unsupported_narrative_claims),
                        clean_reply,
                    )
                    fabrication_stats["blocked"] = True
                    await _gate_event(
                        "unsupported_narrative", "blocked", reason="summary_failed",
                        violations=unsupported_narrative_claims,
                        draft=_un_draft, final=clean_reply,
                    )
            else:
                # Stage 6 P0a follow-up: preserve AI's reply narrative
                clean_reply = attach_draft_to_banner(
                    blocked_unsupported_narrative_reply_text(unsupported_narrative_claims),
                    clean_reply,
                )
                fabrication_stats["blocked"] = True
                await _gate_event(
                    "unsupported_narrative", "blocked", reason="no_summary",
                    violations=unsupported_narrative_claims,
                    draft=_un_draft, final=clean_reply,
                )

        elif (
            unclassified_claims := unclassified_literature_violations(
                clean_reply, all_tool_results
            )
        ):
            # Stage 6 P0c-C (2026-05-19): hard barrier. The LLM must first call
            # classify_literature_relevance before citing a search_literature paper
            # in the narrative. If it didn't call it / cited an Off-topic paper,
            # the whole section is blocked with a banner and the draft is preserved.
            logger.error(
                "Unclassified literature gate BLOCKED reply from %s (%d violations)",
                agent_name, len(unclassified_claims),
            )
            _uc_draft = clean_reply
            clean_reply = attach_draft_to_banner(
                blocked_unclassified_literature_reply_text(unclassified_claims),
                clean_reply,
            )
            fabrication_stats["blocked"] = True
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "fabrication_blocked_total",
                    1.0,
                    agent=agent_name,
                    reason="unclassified_literature",
                )
            except Exception:
                pass
            await _gate_event(
                "unclassified_literature", "blocked",
                violations=unclassified_claims,
                draft=_uc_draft, final=clean_reply,
            )

        elif literature_prior_violations(clean_reply, all_tool_results):
            # W1 (PART W): hard literature-prior block. Looser than
            # zero_data_but_quantitative — here tool_results contain data, but
            # the claim is a quantity like age/mass/distance that requires a
            # dedicated measurement tool. If that tool wasn't run this turn,
            # the block fires to prevent the regen loop from laundering the
            # number with a coincidentally matching universe value (Pleiades
            # "~100 Myr" scenario).
            lit_prior_claims = literature_prior_violations(
                clean_reply, all_tool_results
            )
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "fabrication_blocked_total",
                    1.0,
                    agent=agent_name,
                    reason="literature_prior",
                )
            except Exception:
                pass
            logger.error(
                "Literature-prior turn with %d claim(s) from %s — "
                "hard-blocking (labels: %s, tools_run this turn: %s)",
                len(lit_prior_claims), agent_name,
                [c.label for c in lit_prior_claims],
                sorted({
                    r["tool"] for r in all_tool_results
                    if isinstance(r, dict) and "tool" in r
                }),
            )
            validation = validate_claims(clean_reply, all_tool_results)
            # Stage 6 P0: keep AI's reply narrative; banner + extra note still
            # appear on top, narrative (with unverified numbers redacted) below.
            _original_reply_lit_anchor = clean_reply
            clean_reply = blocked_reply_with_narrative(
                validation, _original_reply_lit_anchor,
            )
            # Insert the literature-anchor guidance right after the banner,
            # before the "---" divider that precedes the narrative.
            _divider = "\n\n---\n\n"
            _additional_note = (
                "\n\nAdditional note: your claims matched the pattern of "
                "citing a textbook literature value (age / mass / distance) "
                "without running a corresponding measurement tool this turn. "
                "If you want to cite a literature value, run a literature "
                "search first so the citation is present in this turn. If "
                "you want to measure it, run the corresponding measurement "
                "workflow explicitly."
            )
            if _divider in clean_reply:
                _banner_part, _narrative_part = clean_reply.split(_divider, 1)
                clean_reply = _banner_part + _additional_note + _divider + _narrative_part
            else:
                clean_reply = clean_reply + _additional_note
            fabrication_stats["blocked"] = True
            await _gate_event(
                "literature_prior", "blocked",
                claims=lit_prior_claims, universe_size=validation.universe_size,
                draft=_original_reply_lit_anchor, final=clean_reply,
            )

        elif _unsupported_cosmology_anchor_numeric_comparison(clean_reply, all_tool_results):
            logger.error(
                "Unsupported cosmology anchor comparison from %s — replacing with grounded summary",
                agent_name,
            )
            _ca_draft = clean_reply
            tool_grounded_summary = (
                _research_tool_grounded_summary(all_tool_results)
                or _line_lfr_tool_grounded_summary(all_tool_results)
                or _cosmology_tool_grounded_summary(all_tool_results, latest_user_text)
            )
            if tool_grounded_summary:
                summary_validation = validate_claims(tool_grounded_summary, all_tool_results)
                summary_citation_violations = provenance_citation_violations(
                    tool_grounded_summary,
                    all_tool_results,
                )
                if (
                    summary_validation.ok
                    and not citation_violations_should_block(summary_citation_violations)
                    and not _unsupported_cosmology_anchor_numeric_comparison(
                        tool_grounded_summary,
                        all_tool_results,
                    )
                ):
                    clean_reply = tool_grounded_summary
                    fabrication_stats["regenerations"] += 1
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "tool_grounded_regeneration_total",
                            1.0,
                            agent=agent_name,
                            reason="unsupported_cosmology_anchor",
                        )
                    except Exception:
                        pass
                    await _gate_event(
                        "cosmology_anchor", "downgraded_summary",
                        draft=_ca_draft, final=clean_reply,
                    )
                else:
                    # Stage 6 P0: preserve tool_grounded_summary narrative
                    clean_reply = blocked_reply_with_narrative(
                        summary_validation, tool_grounded_summary,
                    )
                    fabrication_stats["blocked"] = True
                    await _gate_event(
                        "cosmology_anchor", "blocked", reason="summary_failed",
                        claims=summary_validation.uncited,
                        universe_size=summary_validation.universe_size,
                        draft=_ca_draft, final=clean_reply,
                    )
            else:
                validation = validate_claims(clean_reply, all_tool_results)
                # Stage 6 P0: preserve AI's original reply narrative
                clean_reply = blocked_reply_with_narrative(validation, clean_reply)
                fabrication_stats["blocked"] = True
                await _gate_event(
                    "cosmology_anchor", "blocked", reason="no_summary",
                    claims=validation.uncited, universe_size=validation.universe_size,
                    draft=_ca_draft, final=clean_reply,
                )

        elif True:
            citation_violations = provenance_citation_violations(clean_reply, all_tool_results)
            # M6 + PART AB: methodology mismatches (Bayesian promised but
            # OLS ran, demagnify count claimed > actual) gate the reply
            # the same way citation violations do, but render through a
            # DIFFERENT blocked-reply text (`blocked_methodology_reply_text`)
            # so the user gets the right fix instruction. Pre-PART-AB the
            # method violations were rendered through the citation text,
            # which told the user to "re-run the archive query" — that's
            # the wrong fix for a methodology mismatch (no archive query
            # makes the word "Bayesian" appear in tool_results).
            method_violations = methodology_consistency_violations(
                clean_reply, all_tool_results,
            )
            all_violations = list(citation_violations) + list(method_violations)
            if all_violations and citation_violations_should_block(all_violations):
                logger.error(
                    "Citation/methodology gate BLOCKED reply from %s "
                    "(citation=%d, method=%d)",
                    agent_name,
                    len(citation_violations),
                    len(method_violations),
                )
                _cm_draft = clean_reply
                tool_grounded_summary = (
                    _research_tool_grounded_summary(all_tool_results)
                    or _line_lfr_tool_grounded_summary(all_tool_results)
                    or _statistics_tool_grounded_summary(all_tool_results)
                    or _cosmology_tool_grounded_summary(all_tool_results, latest_user_text)
                )
                recovered_with_summary = False
                if tool_grounded_summary:
                    summary_validation = validate_claims(tool_grounded_summary, all_tool_results)
                    summary_citation_violations = provenance_citation_violations(
                        tool_grounded_summary,
                        all_tool_results,
                    )
                    summary_method_violations = methodology_consistency_violations(
                        tool_grounded_summary,
                        all_tool_results,
                    )
                    summary_violations = list(summary_citation_violations) + list(summary_method_violations)
                    if (
                        summary_validation.ok
                        and not citation_violations_should_block(summary_violations)
                    ):
                        clean_reply = tool_grounded_summary
                        recovered_with_summary = True
                        fabrication_stats["regenerations"] += 1
                        try:
                            from app.observability.metrics import record_counter
                            record_counter(
                                "tool_grounded_regeneration_total",
                                1.0,
                                agent=agent_name,
                                reason="citation_methodology",
                            )
                        except Exception:
                            pass
                        await _gate_event(
                            "citation_methodology", "downgraded_summary",
                            violations=all_violations,
                            draft=_cm_draft, final=clean_reply,
                        )

                if not recovered_with_summary:
                    annotations: list[str] = []
                    if citation_violations:
                        annotations.append(blocked_citation_reply_text(citation_violations))
                    if method_violations:
                        from app.services.claim_validator import (
                            blocked_methodology_reply_text,
                        )
                        annotations.append(blocked_methodology_reply_text(method_violations))

                    # PART AG C1 — annotate-and-attach mode (replaces the
                    # earlier withhold-all behaviour).
                    #
                    # R2.4 M6 audit caught the earlier path erasing 11 of 12
                    # visible tool cards: the model wrote a long correct
                    # prose with Python output, dataframe loads, fit_line_lfr
                    # numbers, then mentioned "Bothwell 2013" on a single line
                    # without a tool_result → guard tripped → entire reply
                    # replaced by "Reply withheld" → user lost the whole
                    # session's worth of work for one inline citation slip.
                    #
                    # Prefer a grounded deterministic summary when available.
                    # If there is no safe summary, keep the original prose and
                    # APPEND a footer with provenance violations so tool cards
                    # and real analysis remain visible.
                    if clean_reply.strip():
                        annotation_block = (
                            "\n\n---\n\n"
                            "## ⚠ Citation / methodology provenance check failed\n\n"
                            "The reply above was generated, but the platform's "
                            "provenance gate flagged claims that the tool results "
                            "this turn did not support. Treat the flagged items as "
                            "**NOT verified** and re-run the relevant tools before "
                            "quoting any of them in a paper.\n\n"
                            + "\n\n---\n\n".join(annotations)
                        )
                        clean_reply = clean_reply.rstrip() + annotation_block
                    else:
                        # Empty prose — rare but possible (e.g. the LLM
                        # returned only tool_use blocks). Fall back to the
                        # previous withhold-only message so the user has
                        # something to read.
                        clean_reply = "\n\n---\n\n".join(annotations)
                    fabrication_stats["blocked"] = True
                    await _gate_event(
                        "citation_methodology", "annotated_blocked",
                        violations=all_violations,
                        draft=_cm_draft, final=clean_reply,
                    )

            if not fabrication_stats["blocked"]:
                _nc_draft = clean_reply
                _nc_regen_before = fabrication_stats["regenerations"]
                _nc_summary_used = False
                _nc_regen_call_failed = False
                _nc_block_reason = "regen_exhausted"
                for attempt in range(2):
                    validation = validate_claims(clean_reply, all_tool_results)
                    if validation.ok:
                        break
                    fabrication_stats["pass"] = attempt + 1
                    fabrication_stats["regenerations"] += 1
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "fabrication_detected_total",
                            1.0,
                            agent=agent_name,
                            attempt=str(attempt + 1),
                        )
                    except Exception:
                        pass
                    logger.warning(
                        "Fabrication detected in %s reply (attempt %d): %d uncited claim(s): %s",
                        agent_name, attempt + 1, len(validation.uncited),
                        [c.label for c in validation.uncited],
                    )
                    # Push the correction as a follow-up user message; no tools.
                    working_messages.append({
                        "role": "assistant",
                        "content": clean_reply,
                    })
                    working_messages.append({
                        "role": "user",
                        "content": build_regeneration_prompt(validation),
                    })
                    try:
                        regen = await _llm_messages_create(
                            system=system,
                            messages=working_messages,
                            tools=[],  # no tools — prose rewrite only
                            provider_api_keys=provider_api_keys,
                            agent_name=agent_name,
                            preferred_backend=preferred_backend,
                            model_profile=model_profile,
                        )
                        regenerated = str(regen.get("content", "") or "").strip()
                    except Exception as exc:
                        logger.warning("Regeneration call failed: %s", exc)
                        _nc_regen_call_failed = True
                        break
                    if not regenerated:
                        _nc_regen_call_failed = True
                        break
                    clean_reply = regenerated
                    text_parts.append("\n[regenerated]\n" + regenerated)
                # Fail CLOSED (audit 2026-07-03).  Reaching here with a not-ok
                # last validation means either the two regen attempts did not
                # cure it (loop exhausted; the second rewrite was never
                # validated in-loop) or the regen call raised / returned empty
                # (`break` above).  The break paths previously shipped the
                # KNOWN-uncited draft unmarked, with only a telemetry event
                # (`regen_failed_shipped`) — a transient provider 429/timeout
                # turned the flagship gate off.  Now every exit re-validates
                # and blocks exactly like the regen-exhausted case.
                if not validation.ok:
                    if _nc_regen_call_failed:
                        _nc_block_reason = "regen_call_failed"
                    validation = validate_claims(clean_reply, all_tool_results)
                    if not validation.ok:
                        tool_grounded_summary = (
                            _research_tool_grounded_summary(all_tool_results)
                            or _line_lfr_tool_grounded_summary(all_tool_results)
                            or _statistics_tool_grounded_summary(all_tool_results)
                            or _cosmology_tool_grounded_summary(all_tool_results, latest_user_text)
                        )
                        if tool_grounded_summary:
                            summary_validation = validate_claims(
                                tool_grounded_summary, all_tool_results,
                            )
                            if summary_validation.ok:
                                clean_reply = tool_grounded_summary
                                validation = summary_validation
                                fabrication_stats["regenerations"] += 1
                                _nc_summary_used = True
                                try:
                                    from app.observability.metrics import record_counter
                                    record_counter(
                                        "tool_grounded_regeneration_total",
                                        1.0,
                                        agent=agent_name,
                                        reason=_nc_block_reason,
                                    )
                                except Exception:
                                    pass
                        if not validation.ok:
                            try:
                                from app.observability.metrics import record_counter
                                record_counter("fabrication_blocked_total", 1.0, agent=agent_name, reason=_nc_block_reason)
                            except Exception:
                                pass
                            logger.error(
                                "Fabrication gate BLOCKED reply from %s (%d uncited, %s)",
                                agent_name, len(validation.uncited), _nc_block_reason,
                            )
                            # Stage 6 P0: keep AI's regen-exhausted narrative
                            # so the user sees methodology / caveats, not just
                            # the banner. Uncited numbers are redacted in-place.
                            clean_reply = blocked_reply_with_narrative(
                                validation, clean_reply,
                            )
                            fabrication_stats["blocked"] = True
                if fabrication_stats["blocked"]:
                    await _gate_event(
                        "numeric_claims", "blocked", reason=_nc_block_reason,
                        claims=validation.uncited, universe_size=validation.universe_size,
                        draft=_nc_draft, final=clean_reply,
                    )
                elif _nc_summary_used:
                    await _gate_event(
                        "numeric_claims", "downgraded_summary", reason=_nc_block_reason,
                        draft=_nc_draft, final=clean_reply,
                    )
                elif fabrication_stats["regenerations"] > _nc_regen_before:
                    # `validation` is ok on every path that reaches here
                    # un-blocked (the fail-closed re-validation above blocks
                    # all not-ok exits, including regen call failures that
                    # previously shipped the draft as `regen_failed_shipped`).
                    await _gate_event(
                        "numeric_claims", "regenerated_clean",
                        universe_size=validation.universe_size,
                        draft=_nc_draft, final=clean_reply,
                    )
                if fabrication_stats["regenerations"]:
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "reply_regeneration_total", float(fabrication_stats["regenerations"]),
                            agent=agent_name,
                        )
                    except Exception:
                        pass
        # F1.2: track whether this turn ran the claim gate so the
        # fallback-synthesis branch below knows to apply it too.
        _claim_gate_ran = True
        _gate_skip_reason: str | None = None
    else:
        _claim_gate_ran = False
        # Honest label for the validation_summary: the gate stack was
        # skipped either because the reply was a pure tool-inventory meta
        # answer, or because the model returned no text at all (the
        # fallback-synthesis branch below still validates what it ships).
        _gate_skip_reason = (
            "tool_inventory_meta" if clean_reply.strip() else "empty_model_reply"
        )
        # Still need is_empty_turn for the fallback branch below.
        from app.services.claim_validator import is_empty_turn  # noqa: F401

    research_mode_result_present = research_program_workflow or any(
        isinstance(tr, dict)
        and tr.get("tool") in {
            "plan_research_program",
            "run_research_matrix",
            "build_evidence_graph",
        }
        for tr in all_tool_results
    )

    held_reply_produced = False
    fact_call_id: str | None = None
    automatic_fact_check_failed = False
    fact_check_no_safe_summary = False
    fact_check_failure_notice = (
        "Automatic fact verification failed, so no research claim or report "
        "was cleared for use. Review the failed Fact Check result and rerun "
        "verification before relying on this analysis."
    )
    if research_mode_result_present and clean_reply.strip():
        try:
            from app.services.research_program import verify_research_facts

            await _emit({
                "type": "status",
                "message": "Running fact check before finalizing the research summary.",
            })
            fact_input = {
                "tool_results": _compact_tool_results_for_evidence(all_tool_results),
                "final_reply": clean_reply,
            }
            fact_call_id = f"auto_fact_check_{uuid.uuid4().hex}"
            await _emit({
                "type": "tool_call",
                "agent": agent_name,
                "tool": "verify_research_facts",
                "input": {
                    "tool_result_count": len(all_tool_results),
                    "final_reply_chars": len(clean_reply),
                },
                "automatic": True,
            })
            fact_result = verify_research_facts(**fact_input)
            fact_tool_result = {
                "id": fact_call_id,
                "tool": "verify_research_facts",
                "input": fact_input,
                "result": fact_result,
            }
            all_tool_results.append(fact_tool_result)
            await _emit({
                "type": "tool_result",
                "agent": agent_name,
                "tool": "verify_research_facts",
                "result": fact_result,
                "live": True,
                "tool_call_id": fact_call_id,
                "automatic": True,
            })
            if fact_result.get("status") == "blocked":
                # HOLD instead of whole-reply nuke: keep the tool-grounded
                # deterministic core (now incl. the cosmology/AP summary) and
                # surface the un-grounded claims as a held footer — the verified
                # result is no longer lost just because the model embellished.
                _fv_draft = clean_reply
                safe_summary = (
                    _research_tool_grounded_summary(all_tool_results)
                    or _cosmology_tool_grounded_summary(all_tool_results, latest_user_text)
                )
                held_claims = [
                    c for c in (fact_result.get("claims") or [])
                    if c.get("status") in {"unsupported", "contradicted"}
                ]
                if safe_summary:
                    await _emit({
                        "type": "status",
                        "message": (
                            "Draft failed the fact-check guardrail; generating a "
                            "tool-grounded safe summary."
                        ),
                    })
                    clean_reply = safe_summary
                    if held_claims:
                        clean_reply += (
                            f"\n\n⚠ {len(held_claims)} claim(s) in the draft were held — not "
                            "grounded by any tool this turn — and excluded from the result "
                            "above. See the Fact Check panel for each held claim and how to "
                            "ground it (e.g. run the corresponding tool)."
                        )
                    held_reply_produced = True
                    await _gate_event(
                        "fact_verification", "downgraded_summary", reason="fact_check_held",
                        details={"held_claims": held_claims[:5], "held_count": len(held_claims)},
                        draft=_fv_draft, final=clean_reply,
                    )
                else:
                    clean_reply = (
                        "The research run completed, but fact verification found "
                        "a contradicted claim in the draft. Please review the Fact "
                        "Check card and rerun the missing evidence path before "
                        "using the result."
                    )
                    held_reply_produced = True
                    fact_check_no_safe_summary = True
                    fabrication_stats["blocked"] = True
                    await _gate_event(
                        "fact_verification", "blocked", reason="fact_check_no_summary",
                        details={"held_count": len(held_claims)},
                        draft=_fv_draft, final=clean_reply,
                    )
                try:
                    from app.observability.metrics import record_counter
                    record_counter(
                        "fabrication_blocked_total",
                        1.0,
                        agent=agent_name,
                        reason="fact_verification",
                    )
                except Exception:
                    pass
        except Exception as exc:
            automatic_fact_check_failed = True
            held_reply_produced = True
            fabrication_stats["blocked"] = True
            fact_failure_result = {
                "success": False,
                "__tool_status__": "FAILED",
                "analysis_status": "FACT_CHECK_FAILED",
                "status": "blocked",
                "publication_ready": False,
                "__do_not_claim__": True,
                "error_class": "automatic_fact_check_failed",
                "error": "Automatic fact verification failed.",
                "fact_check_report": {
                    "status": "blocked",
                    "verified_claim_count": 0,
                    "unsupported_claim_count": 0,
                    "claims": [],
                },
            }
            if fact_call_id is None:
                fact_call_id = f"auto_fact_check_{uuid.uuid4().hex}"
            existing_fact_result = next(
                (
                    tr
                    for tr in all_tool_results
                    if tr.get("id") == fact_call_id
                ),
                None,
            )
            compact_failure_input = {
                "tool_result_count": len(all_tool_results),
                "final_reply_chars": len(clean_reply),
            }
            if existing_fact_result is None:
                all_tool_results.append({
                    "id": fact_call_id,
                    "tool": "verify_research_facts",
                    "input": compact_failure_input,
                    "result": fact_failure_result,
                })
            else:
                existing_fact_result["input"] = compact_failure_input
                existing_fact_result["result"] = fact_failure_result
            await _emit({
                "type": "tool_result",
                "agent": agent_name,
                "tool": "verify_research_facts",
                "result": fact_failure_result,
                "live": True,
                "tool_call_id": fact_call_id,
                "automatic": True,
            })
            _fv_failure_draft = clean_reply
            clean_reply = fact_check_failure_notice
            await _gate_event(
                "fact_verification",
                "blocked",
                reason="automatic_fact_check_failed",
                draft=_fv_failure_draft,
                final=clean_reply,
            )
            logger.warning("Research fact verification failed closed: %s", exc)

    if research_mode_result_present and not held_reply_produced:
        # Research Mode answers must be the evidence graph's public surface, not
        # a fresh model-written literature review.  The model is still used to
        # choose and run tools, but the final prose is deterministic so it
        # cannot introduce unsupported paper facts, citations, or background
        # claims after the tools have completed. (Skipped when a held reply was
        # already produced above, so the held footer is not clobbered.)
        _rs_draft = clean_reply
        tool_grounded_research_summary = (
            _research_tool_grounded_summary(all_tool_results)
            or _cosmology_tool_grounded_summary(all_tool_results, latest_user_text)
        )
        if tool_grounded_research_summary:
            clean_reply = tool_grounded_research_summary
            await _gate_event(
                "research_deterministic_summary", "downgraded_summary",
                reason="research_mode_deterministic",
                draft=_rs_draft, final=clean_reply,
            )

    report_call_id: str | None = None
    automatic_report_export_failed = False
    report_export_failure_notice = (
        "Automatic research-report export failed. The tool-grounded analysis "
        "above remains visible, but the requested report artifact was not "
        "created or cleared for use. Rerun export before treating the workflow "
        "as complete."
    )
    if (
        research_mode_result_present
        and not automatic_fact_check_failed
        and not fact_check_no_safe_summary
        and not _successful_research_report_export(all_tool_results)
    ):
        try:
            from app.services.research_program import export_research_report

            report_input = {
                "research_plan": _research_plan_from_tool_results(all_tool_results),
                "evidence_graph": _research_evidence_graph_from_tool_results(all_tool_results),
                "tool_results": all_tool_results,
                "title": latest_user_text[:180] if latest_user_text else None,
            }
            report_call_id = f"auto_research_report_{uuid.uuid4().hex}"
            await _emit({
                "type": "tool_call",
                "agent": agent_name,
                "tool": "export_research_report",
                "input": {
                    "tool_result_count": len(all_tool_results),
                    "title": report_input["title"],
                },
                "automatic": True,
            })
            report_result = export_research_report(**report_input)
            all_tool_results.append({
                "id": report_call_id,
                "tool": "export_research_report",
                "input": {
                    "research_plan": report_input["research_plan"],
                    "evidence_graph": report_input["evidence_graph"],
                    "title": report_input["title"],
                },
                "result": report_result,
            })
            await _emit({
                "type": "tool_result",
                "agent": agent_name,
                "tool": "export_research_report",
                "result": report_result,
                "live": True,
                "tool_call_id": report_call_id,
                "automatic": True,
            })
        except Exception as exc:
            automatic_report_export_failed = True
            report_failure_result = {
                "success": False,
                "__tool_status__": "FAILED",
                "analysis_status": "REPORT_EXPORT_FAILED",
                "publication_ready": False,
                "__do_not_claim__": True,
                "error_class": "automatic_report_export_failed",
                "error": "Automatic research-report export failed.",
            }
            if report_call_id is None:
                report_call_id = f"auto_research_report_{uuid.uuid4().hex}"
            compact_report_input = {
                "tool_result_count": len(all_tool_results),
                "title": latest_user_text[:180] if latest_user_text else None,
            }
            existing_report_result = next(
                (
                    tr
                    for tr in all_tool_results
                    if tr.get("id") == report_call_id
                ),
                None,
            )
            if existing_report_result is None:
                all_tool_results.append({
                    "id": report_call_id,
                    "tool": "export_research_report",
                    "input": compact_report_input,
                    "result": report_failure_result,
                })
            else:
                existing_report_result["input"] = compact_report_input
                existing_report_result["result"] = report_failure_result
            await _emit({
                "type": "tool_result",
                "agent": agent_name,
                "tool": "export_research_report",
                "result": report_failure_result,
                "live": True,
                "tool_call_id": report_call_id,
                "automatic": True,
            })
            _report_failure_draft = clean_reply
            clean_reply = (
                clean_reply.rstrip() + "\n\n---\n\n" + report_export_failure_notice
            )
            fabrication_stats["blocked"] = True
            await _gate_event(
                "report_export",
                "blocked",
                reason="automatic_report_export_failed",
                draft=_report_failure_draft,
                final=clean_reply,
            )
            logger.warning("Research report export failed: %s", exc)

    # Dataset-release identity is a deterministic post-condition, not an LLM
    # wording preference.  Run this after every regeneration/fact-check path so
    # a non-empty draft cannot relabel the executed product as the release the
    # user requested (F4: KiDS-Legacy DR5 versus KiDS-1000).
    _identity_draft = clean_reply
    identity_reply, identity_enforced = _enforce_cosmology_dataset_identity(
        clean_reply, all_tool_results, latest_user_text
    )
    if identity_enforced:
        clean_reply = identity_reply
        if automatic_fact_check_failed:
            clean_reply += "\n\n---\n\n" + fact_check_failure_notice
        if automatic_report_export_failed:
            clean_reply += "\n\n---\n\n" + report_export_failure_notice
        identity_action = "downgraded_summary"
        identity_reason = "requested_release_mismatch"
        try:
            from app.services.claim_validator import (
                blocked_reply_with_narrative,
                validate_claims,
            )

            identity_validation = validate_claims(clean_reply, all_tool_results)
            if not identity_validation.ok:
                clean_reply = blocked_reply_with_narrative(
                    identity_validation, clean_reply
                )
                fabrication_stats["blocked"] = True
                identity_action = "blocked"
                identity_reason = "dataset_identity_claim_validation_failed"
        except Exception as exc:
            # Never fall back to the contradicted model draft, but do not mark
            # the deterministic replacement as validated when the secondary
            # claim validator itself is unavailable.
            fabrication_stats["blocked"] = True
            identity_action = "blocked"
            identity_reason = "dataset_identity_validation_error"
            clean_reply += (
                "\n\n---\n\nDataset identity was corrected from this turn's "
                "tool outputs, but secondary claim validation failed. Treat "
                "this reply as blocked until validation is rerun."
            )
            logger.exception("Dataset-identity summary validation failed: %s", exc)
        await _gate_event(
            "dataset_identity",
            identity_action,
            reason=identity_reason,
            draft=_identity_draft,
            final=clean_reply,
        )

    actions.extend(_tool_results_to_actions(all_tool_results))

    # Fallback: if the LLM returned zero text (empty text_parts) but did
    # execute tools, synthesise a minimal human-readable summary so the user
    # never sees a blank AI bubble. Root cause of the empty-response bug
    # observed in the WD LF test (first attempt).
    if not clean_reply.strip():
        _fb_kind = "none"
        research_summary = _research_tool_grounded_summary(all_tool_results)
        if research_summary:
            clean_reply = research_summary
            _fb_kind = "research"
        else:
            line_lfr_summary = _line_lfr_tool_grounded_summary(all_tool_results)
            if line_lfr_summary:
                clean_reply = line_lfr_summary
                _fb_kind = "line_lfr"
            else:
                stats_summary = _statistics_tool_grounded_summary(all_tool_results)
                if stats_summary:
                    clean_reply = stats_summary
                    _fb_kind = "statistics"
        if not clean_reply.strip():
            cosmology_summary = _cosmology_tool_grounded_summary(all_tool_results, latest_user_text)
            if cosmology_summary:
                clean_reply = cosmology_summary
                _fb_kind = "cosmology"
        if not clean_reply.strip():
            if all_tool_results:
                tool_names = ", ".join({tr["tool"] for tr in all_tool_results})
                clean_reply = (
                    f"I ran the following tools: {tool_names}. "
                    f"The results are shown below. "
                    f"(Note: the language model did not return a written summary — "
                    f"please review the tool outputs directly or ask me to explain them.)"
                )
                _fb_kind = "tool_names"
            else:
                clean_reply = (
                    "The language model returned an empty response. This may be a "
                    "transient API issue or a prompt length problem. Please try "
                    "again with a shorter prompt, or contact support if it persists."
                )
                _fb_kind = "no_tools"
        logger.warning(
            "Empty AI reply detected in %s agent loop; synthesised fallback. "
            "tool_results=%d iterations=%d",
            agent_name, len(all_tool_results), _iteration + 1,
        )
        await _gate_event(
            "empty_reply_fallback", "synthesized", reason=_fb_kind,
            final=clean_reply,
        )

        # F1.2: even the synthesised fallback must not smuggle numbers past
        # the validation gate.  The summary above is tool-name-only so it
        # should pass trivially, but if any downstream change adds
        # numerics to this branch, run validate_claims so it gets caught.
        try:
            from app.services.claim_validator import (
                validate_claims,
                blocked_reply_with_narrative,
            )
            fallback_validation = validate_claims(clean_reply, all_tool_results)
            if not fallback_validation.ok:
                logger.error(
                    "Fallback synthesis contained %d uncited claim(s); "
                    "replacing with block message",
                    len(fallback_validation.uncited),
                )
                try:
                    from app.observability.metrics import record_counter
                    record_counter(
                        "fabrication_blocked_total",
                        1.0,
                        agent=agent_name,
                        reason="fallback_synthesis",
                    )
                except Exception:
                    pass
                # Stage 6 P0: preserve fallback-synthesis narrative
                _fb_draft = clean_reply
                clean_reply = blocked_reply_with_narrative(
                    fallback_validation, clean_reply,
                )
                fabrication_stats["blocked"] = True
                await _gate_event(
                    "empty_reply_fallback", "blocked", reason="fallback_synthesis",
                    claims=fallback_validation.uncited,
                    universe_size=fallback_validation.universe_size,
                    draft=_fb_draft, final=clean_reply,
                )
        except Exception as e:
            logger.debug("Fallback synthesis validation skipped: %s", e)

    # Daily B4/B5 final post-condition.  Negated phrases such as
    # ``H0 = <value> cannot be confirmed`` are intentionally not positive
    # claims to the normal validator, but they still repeat the rejected user
    # number.  Replace the complete reply with a number-free refusal after all
    # model rewrites, banners, deterministic summaries, and fallbacks.
    _echo_draft = clean_reply
    echoed_untrusted_values = untrusted_evidence_echo_values(
        clean_reply,
        messages,
        all_tool_results,
    )
    if echoed_untrusted_values:
        clean_reply = untrusted_evidence_refusal()
        fabrication_stats["blocked"] = True
        await _gate_event(
            "untrusted_evidence_echo",
            "blocked",
            reason="user_supplied_number_repeated",
            details={"value_count": len(echoed_untrusted_values)},
            draft=_echo_draft,
            final=clean_reply,
        )

    # Daily F2 final post-condition.  A successful exploratory runner may keep
    # its posterior in the structured tool result for diagnostics, but when
    # publication_ready=false (or __do_not_claim__ is set), those values must
    # not flow into ordinary final prose even with an "exploratory" caveat.
    _posterior_draft = clean_reply
    escaped_posterior_values = nonpublication_posterior_values(
        clean_reply,
        all_tool_results,
    )
    if escaped_posterior_values:
        clean_reply = (
            _cosmology_tool_grounded_summary(all_tool_results, latest_user_text)
            or nonpublication_posterior_refusal()
        )
        fabrication_stats["regenerations"] += 1
        await _gate_event(
            "nonpublication_posterior",
            "downgraded_summary",
            reason="posterior_values_withheld",
            details={"value_count": len(escaped_posterior_values)},
            draft=_posterior_draft,
            final=clean_reply,
        )

    # Daily C2 final post-condition.  Structured coverage metadata is the
    # authority; append its plain-language consequence so a provider cannot
    # omit the distinction between a measurement and a model extrapolation.
    coverage_disclosure = _cosmology_outside_coverage_disclosure(
        all_tool_results,
        latest_user_text,
    )
    if coverage_disclosure and coverage_disclosure not in clean_reply:
        _coverage_draft = clean_reply
        clean_reply = clean_reply.rstrip() + "\n\n" + coverage_disclosure
        fabrication_stats["regenerations"] += 1
        await _gate_event(
            "dataset_coverage",
            "disclosed",
            reason="outside_registered_coverage",
            draft=_coverage_draft,
            final=clean_reply,
        )

    # Final qualitative-science post-condition.  This runs after every model,
    # regeneration, identity correction, deterministic research summary, and
    # empty-reply fallback path above.  Keeping one last boundary check prevents
    # a later fallback from reintroducing an unsupported headline conclusion.
    try:
        from app.services.claim_validator import (
            enforce_scientific_conclusion_gate,
        )

        _conclusion_draft = clean_reply
        clean_reply, conclusion_violations = enforce_scientific_conclusion_gate(
            clean_reply, all_tool_results
        )
        if conclusion_violations:
            fabrication_stats["blocked"] = True
            await _gate_event(
                "scientific_conclusion_scope",
                "blocked",
                reason="unmatched_conclusion_attestation",
                violations=conclusion_violations,
                draft=_conclusion_draft,
                final=clean_reply,
            )
    except Exception as exc:
        _conclusion_draft = clean_reply
        clean_reply = (
            "Scientific-conclusion validation failed, so no qualitative "
            "scientific conclusion is cleared for display. Review the current "
            "tool evidence and rerun the validation step."
        )
        fabrication_stats["blocked"] = True
        await _gate_event(
            "scientific_conclusion_scope",
            "blocked",
            reason="validation_error",
            details={"error_class": exc.__class__.__name__},
            draft=_conclusion_draft,
            final=clean_reply,
        )
        logger.exception("Scientific-conclusion final gate failed closed")

    # M7: telemetry so the UI can surface "hit iteration cap" to the user
    # (previously silent — a 13-step workflow just got truncated with no
    # indication why).  `hit_iteration_cap` is set in the agent loop's `else`
    # clause, which fires only when the loop exhausts its iteration budget
    # without breaking — a clean final-answer break is not flagged.

    return {
        "reply": clean_reply,
        "actions": actions,
        "tool_results": all_tool_results,
        "hit_iteration_cap": hit_iteration_cap,
        "hit_deadline": hit_deadline,
        # 2026-07-03 honesty surfacing: compact summary of what the gate
        # stack actually did this turn, derived from state the gates
        # already computed (fabrication_stats + gate_interventions).
        "validation_summary": _derive_validation_summary(
            claim_gate_ran=_claim_gate_ran,
            gate_skip_reason=_gate_skip_reason,
            fabrication_stats=fabrication_stats,
            interventions=gate_interventions,
            tool_results=all_tool_results,
        ),
    }
