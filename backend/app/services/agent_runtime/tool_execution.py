"""LLM turn routing, tool-call execution, and workflow checkpoints.

Moved verbatim from app/api/chat.py (2026-07-03 god-file split).
"""

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from app.ai.inference_router import inference_router
from app.ai.model_profiles import ModelProfile

logger = logging.getLogger(__name__)


def _checkpoint_session_id(chat_session_id: str | None, python_session_id: str | None) -> str | None:
    chat_id = str(chat_session_id or "").strip()
    if chat_id:
        return chat_id
    py_id = str(python_session_id or "").strip()
    if py_id and py_id != "default":
        return py_id
    return None


def _hash_tool_input(tool_input: Any) -> str:
    import hashlib

    try:
        raw = json.dumps(tool_input, sort_keys=True, default=str)
    except (TypeError, ValueError) as e:
        logger.debug("hash_tool_input json fallback (%s): %s", type(e).__name__, e)
        raw = str(tool_input)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _checkpoint_cache_refs(tool_name: str, result: Any, python_session_id: str) -> list[str]:
    refs: list[str] = []
    if tool_name in {"run_adql", "query_high_velocity_stars"}:
        refs.extend(["latest_adql", "latest_adql_set", "latest_adql_sets"])
    elif tool_name == "run_sdss_sql":
        refs.extend(["latest_sdss_sql", "latest_adql", "latest_adql_set"])
    elif tool_name in {"search_objects", "get_object_info", "get_object_dossier"}:
        refs.append("latest")
    elif tool_name == "search_lightcurve":
        refs.append("latest_lightcurve")
    elif tool_name == "run_python":
        refs.append(f"python_session:{python_session_id}")
    if isinstance(result, dict) and result.get("figures"):
        refs.append("figures")
    return refs


def _checkpoint_status(result: Any) -> str:
    if not isinstance(result, dict):
        return "completed"
    status = str(result.get("__tool_status__") or result.get("analysis_status") or "").upper()
    if result.get("success") is False or result.get("error") or status in {"FAILED", "UNAVAILABLE"}:
        return "failed"
    return "completed"


def _checkpoint_result_summary(tool_name: str, result: Any) -> str:
    if not isinstance(result, dict):
        return f"{tool_name} returned {type(result).__name__}"
    bits: list[str] = []
    row_count = result.get("row_count")
    if isinstance(row_count, int):
        bits.append(f"{row_count} rows")
    columns = result.get("columns")
    if isinstance(columns, list) and columns:
        bits.append("columns=" + ",".join(str(c) for c in columns[:8]))
    if result.get("figures"):
        try:
            bits.append(f"{len(result['figures'])} figures")
        except TypeError:
            bits.append("figures")
    error = result.get("error")
    if error:
        bits.append("error=" + str(error)[:160])
    return "; ".join(bits)[:360] if bits else f"{tool_name} completed"


def _record_tool_checkpoint(
    *,
    chat_session_id: str | None,
    python_session_id: str,
    tool_call: dict,
    result: Any,
) -> dict[str, Any] | None:
    session_id = _checkpoint_session_id(chat_session_id, python_session_id)
    if not session_id:
        return None
    try:
        from app.services import workflow_checkpoint

        tool_name = str(tool_call.get("name") or "")
        step = workflow_checkpoint.record_step(
            session_id,
            tool_name,
            _hash_tool_input(tool_call.get("input")),
            _checkpoint_status(result),
            _checkpoint_cache_refs(tool_name, result, python_session_id),
            error=(str(result.get("error"))[:500] if isinstance(result, dict) and result.get("error") else None),
            tool_call_id=str(tool_call.get("id") or "") or None,
            summary=_checkpoint_result_summary(tool_name, result),
        )
        return {
            "session_id": session_id,
            "step_idx": step.step_idx,
            "tool_name": step.tool_name,
            "status": step.status,
            "cache_refs": step.cache_refs,
            "summary": step.summary,
        }
    except Exception:
        logger.debug("workflow checkpoint write failed", exc_info=True)
        return None


def _format_checkpoint_resume_note(session_id: str | None) -> str:
    if not session_id:
        return ""
    try:
        from app.services import workflow_checkpoint

        summary = workflow_checkpoint.summarize(session_id)
    except Exception:
        return ""
    if not summary.get("has_checkpoint"):
        return ""
    steps = summary.get("steps", [])[-8:]
    lines = [
        "[RUNTIME CHECKPOINT: previous tool steps exist for this chat/session.",
        "Use cached results before rerunning expensive archive queries. Relevant cache refs include latest_adql, latest_adql_set, latest_sdss_sql, latest_lightcurve, and python_session state.",
    ]
    for step in steps:
        bits = [
            f"#{step.get('step_idx')} {step.get('tool_name')} {step.get('status')}",
            f"refs={step.get('cache_refs') or []}",
        ]
        if step.get("summary"):
            bits.append(str(step.get("summary")))
        lines.append("- " + "; ".join(bits))
    lines.append("If the workflow budget is nearly exhausted, summarize these checkpoints and ask the user to continue from them.]")
    return "\n".join(lines)


# G7.3: debug store for the last LLM prompt.  Populated by the agent-loop
# `_llm_messages_create` when DEBUG_LAST_PROMPT env is set.  Admin-only
# endpoint returns it so the reviewer can verify in the browser that
# the anti-sim rule + anti-reflection rule + structured-abstention spec
# actually reach the model.
_LAST_PROMPT_DEBUG: dict[str, object] = {
    "enabled": False,
    "system": "",
    "message_count": 0,
    "first_messages_preview": [],
    "timestamp": "",
    "agent": "",
}


async def _llm_messages_create(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict],
    provider_api_keys: dict[str, str],
    agent_name: str = "orchestrator",
    preferred_backend: str | None = None,
    model_profile: ModelProfile | None = None,
):
    """Route one model turn through the inference router."""
    # G7.3: if debug-prompt capture is on, snapshot the system + first
    # messages for the /api/chat/_debug_last_prompt endpoint.
    if os.getenv("DEBUG_LAST_PROMPT", "").strip():
        from datetime import datetime as _dt
        try:
            _LAST_PROMPT_DEBUG.update({
                "enabled": True,
                "system": system,
                "message_count": len(messages),
                "first_messages_preview": [
                    {
                        "role": m.get("role"),
                        "content_preview": (
                            str(m.get("content"))[:500]
                            if not isinstance(m.get("content"), list)
                            else f"[{len(m.get('content', []))} content blocks]"
                        ),
                    }
                    for m in messages[:3]
                ],
                "timestamp": _dt.utcnow().isoformat() + "Z",
                "agent": agent_name,
                "tools_count": len(tools),
                "tool_names": [t.get("name") for t in tools],
            })
        except Exception:
            pass
    return await inference_router.route(
        agent_name,
        messages,
        system=system,
        tools=tools,
        provider_api_keys=provider_api_keys,
        preferred_backend=preferred_backend,
        model_profile=model_profile,
        # PART AF C4 — raised from 4096 to 8192. M5 audit caught a
        # complex chat round (5 search_literature + extract + fit + 4
        # run_python with multi-panel plots) hitting the truncation gate
        # twice, losing 5 of 6 figures including the Bayesian fit band
        # and Redshift Dependence Test. 8192 is supported by Claude
        # 4.6 / 4.7 / OpenAI / DeepSeek thinking-mode and matches the
        # `max_completion_tokens` ceiling for the responses API path.
        # Cost trade-off: per-turn output cost can up to 2× on long
        # turns, but a successfully-finished long turn is more valuable
        # than a half-truncated cheap one.
        max_tokens=8192,
        temperature=0.0,
        # Timeout budget (R0c tightens single-LLM cap after R0b added
        # per-tool deadlines):
        #   outer endpoint 420s -> agent loop 360s -> single LLM call 90s
        #   -> per-tool 45s.
        # Two back-to-back 90s LLM rounds + tools still fit in 360s, and a
        # 90s single-call cap means a hung LLM can't silently eat half the
        # loop budget the way the old 150s cap did.
        backend_timeout=90.0,
    )


async def _execute_tool_calls(
    tool_calls: list[dict], api_key: str, provider_api_keys: dict[str, str], python_session_id: str,
    user_id: str | None = None, chat_session_id: str | None = None,
    on_event: Callable[[dict], Awaitable[None]] | None = None,
    loop_deadline: float | None = None,
    summary_reserve_s: float = 60.0,
    workflow_budget_mode: str = "default",
    tool_deadline_scale: float = 1.0,
    turn_tool_results: list[dict] | None = None,
) -> list[dict]:
    """Execute one model turn's tool calls concurrently while preserving order.

    Uses return_exceptions=True (H2) so that a single raising tool does not
    abort the whole turn; raised exceptions are converted into error-shaped
    result dicts that flow through normalize_tool_result downstream.

    If `on_event` is provided, each tool also gets a per-tool progress
    heartbeat (`status: running <tool>... (Ns)`) every 6s while it executes.
    Without this heartbeat a slow single tool looks indistinguishable from
    "the whole agent is stuck" in the UI.
    """
    import time as _time
    from app.services.ai_tools import execute_tool

    # E0.1: per-tool hard deadline.  A flat 45 s cap is too tight for
    # compute-heavy analysis tools — the NGC 752 reviewer saw
    # fit_isochrone time out at 45 s and the AI fell back to a biased
    # estimator (age 3.65 Gyr vs literature 1.4-1.9 Gyr).  The table
    # below gives compute-heavy tools a realistic budget while keeping
    # the default 45 s for fast search/metadata calls so one slow
    # connector still can't burn the whole 360 s agent-loop.
    _TOOL_DEADLINE_TABLE: dict[str, float] = {
        "fit_isochrone": 180.0,
        "fit_transit_model": 120.0,
        "transit_search_bls": 120.0,
        "estimate_photo_z_pro": 90.0,
        "gp_detrend_lightcurve": 90.0,
        "x_ray_spectral_fit": 90.0,
        "fit_rv_orbit": 120.0,
        "fit_sersic_morphology": 90.0,
        "analyze_spectrum_pro": 90.0,
        "compute_galaxy_sfr": 60.0,
        # G5: run_python ceiling is the `slow` mode budget + a little
        # slack. The inner `_exec_run_python` picks the real per-call
        # timeout based on the AI's declared mode.
        "run_python": 310.0,
        # Audit-2026-04-20: the 8 tools below were falling to the 45 s
        # default but do legitimately slow work (multi-TAP fan-out, LLM
        # calls, deep cross-matches).  Reviewer-style queries were
        # hitting false timeouts.  Rough envelope per tool:
        "query_gaia_cluster": 90.0,   # Gaia TAP cone + agg stats
        "get_object_dossier": 120.0,  # 6-way parallel TAP (dominant tool for "tell me about X")
        "crossmatch_catalogs": 120.0, # dual TAP + join
        "sensitivity_analysis": 120.0,# parameter sweep via run_python
        "generate_paper_draft": 180.0,# LLM call for full paper sections
        "research_workflow": 240.0,   # multi-step hypothesis test
        "full_research_report": 300.0,# validation + paper + exports
        "solve_astrometry": 90.0,     # astrometry.net can be slow
        # Note: get_extinction stays at the 45 s default — SFD lookup is
        # <1 s typical, the 3-D fallback is local analytic.
        # J2 (2026-04-20 3rd regression): run_adql previously relied on the 45 s
        # default, but integration.py's execute_adql_query switches large queries
        # (TOP>5000 / cone>1 deg / JOIN) to launch_job_async with a 300 s async
        # budget. The 45 s tool-layer deadline cuts in first and the async path
        # never runs to completion. Give run_adql 300 s to align with integration.
        # The outer agent loop total is 360 s, so one run_adql taking 300 s leaves
        # 60 s for the subsequent LLM summary — sufficient.
        "run_adql": 300.0,
        # J3: run_sdss_sql hits SDSS SkyServer with an internal httpx timeout of 120 s.
        # A bit of extra slack handles large JOINs + JSON parsing; same tier as crossmatch_catalogs.
        "run_sdss_sql": 180.0,
        # MW v_esc / halo-star workflows need a focused Gaia DR3 helper
        # rather than repeated broad source-table scans.
        "query_high_velocity_stars": 240.0,
        # MAST / lightkurve cold starts are often >45s on Render.  Keep the
        # default mode bounded, then stretch it explicitly in long mode below.
        "search_lightcurve": 90.0,
        # ── 2026-06-12: research matrix runs many chains ──
        # ΛCDM baseline subset cells stay on the fast importance path (~26 s
        # for a 30-cell matrix); the extended-model branch cells (wcdm /
        # w0wa_cdm) AND the full-union ΛCDM comparison anchor take the emcee
        # upgrade at ~13 s each (importance proposals collapse on extended-DE
        # axes and multi-probe unions; ESS ~1). research_program caps
        # emcee-eligible cells at 3 and numerically-run cells at 24, so the
        # bound is ~26 s importance + 3 × ~13 s emcee ≈ 65 s; the observed
        # worst case is 51.7 s. 120 s adds headroom for cold imports. NOTE:
        # with the off-by-default PANTHEON_PLUS/DES_SN5YR_FULL_CHI2_ENABLED
        # env flags, full-vector SN cells always take emcee (~minutes each)
        # and were already over every deadline before this entry existed.
        "run_research_matrix": 120.0,
        # ── M1-A (2026-05-31): CAMB theory CMB spectrum ──
        # A bounded lmax<=2500 CAMB call is a few seconds, but calls serialize
        # behind a process-global lock (CAMB's Fortran kernel is non-reentrant)
        # and the first call pays a one-time camb import; 90 s gives headroom
        # for queueing under load + cold import without false timeouts.
        "compute_theory_cmb_spectrum": 90.0,
    }
    _TOOL_DEADLINE_DEFAULT = 45.0

    async def _run_one(tc: dict) -> dict:
        tool_name = tc.get("name") or ""
        base_deadline_s = _TOOL_DEADLINE_TABLE.get(tool_name, _TOOL_DEADLINE_DEFAULT)
        if workflow_budget_mode == "long":
            if tool_name == "run_adql":
                base_deadline_s = max(base_deadline_s, 780.0)
            elif tool_name == "run_sdss_sql":
                base_deadline_s = max(base_deadline_s, 300.0)
            elif tool_name == "query_high_velocity_stars":
                base_deadline_s = max(base_deadline_s, 420.0)
            elif tool_name == "search_lightcurve":
                base_deadline_s = max(base_deadline_s, 240.0)
            else:
                base_deadline_s = min(base_deadline_s * max(1.0, tool_deadline_scale), 360.0)
        deadline_s = base_deadline_s
        deadline_adjusted = False
        workflow_seconds_remaining: int | None = None
        if loop_deadline is not None:
            now = _time.monotonic()
            workflow_seconds_remaining = max(0, int(loop_deadline - now))
            tool_window_s = loop_deadline - now - summary_reserve_s
            # R5: do not start a new long tool call when the agent-loop deadline
            # is near. Otherwise the final 60 s summary budget gets consumed and
            # the outer 420 s hard wall kills the entire turn. Return a regular
            # tool-shaped failure so the next LLM iteration summarizes what was
            # already gathered.
            if tool_window_s < 8.0:
                return {
                    "error": (
                        f"Tool {tool_name} was not started because the workflow "
                        f"has only {workflow_seconds_remaining}s left; summarize "
                        "the successful tool results already gathered or ask the "
                        "user to split query + analysis steps."
                    ),
                    "success": False,
                    "error_class": "workflow_deadline_near",
                    "deadline_seconds": 0,
                    "base_deadline_seconds": int(base_deadline_s),
                    "workflow_seconds_remaining": workflow_seconds_remaining,
                    "summary_reserve_seconds": int(summary_reserve_s),
                }
            deadline_s = min(base_deadline_s, tool_window_s)
            deadline_adjusted = deadline_s < base_deadline_s

        async def _emit_tool_progress(progress: dict) -> None:
            if on_event is None:
                return
            try:
                await on_event({
                    "type": "tool_progress",
                    "tool": tool_name,
                    **progress,
                })
            except Exception:
                logger.debug("tool_progress event failed", exc_info=True)

        tool_input = dict(tc.get("input") or {})
        # 2026-06-12 anti-laundering: the trusted turn record is ALWAYS
        # server-controlled. Strip any model-supplied copy of the private key
        # for EVERY tool, then attach the real accumulator only for the tools
        # that render/verify tool results — without this, a model-authored
        # tool_results payload becomes a user-facing report and re-grounds
        # its own fabricated numbers (live-confirmed bypass).
        tool_input.pop("_turn_tool_results", None)
        if (
            tool_name in {
                "export_research_report",
                "verify_research_facts",
                "build_evidence_graph",
            }
            and turn_tool_results is not None
        ):
            tool_input["_turn_tool_results"] = list(turn_tool_results)
        if workflow_budget_mode == "long":
            tool_input.setdefault("_workflow_budget_mode", "long")
            if tool_name in {"run_adql", "run_sdss_sql", "query_high_velocity_stars"}:
                tool_input.setdefault("extended_timeout", True)

        task = asyncio.create_task(execute_tool(
            tool_name, tool_input, api_key, provider_api_keys, python_session_id,
            user_id=user_id, chat_session_id=chat_session_id,
            progress_callback=_emit_tool_progress,
        ))
        start = _time.monotonic()
        while not task.done():
            elapsed = _time.monotonic() - start
            if elapsed > deadline_s:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                return {
                    "error": (
                        f"Tool {tool_name} exceeded the {int(deadline_s)}s per-tool "
                        f"deadline and was cancelled. Retry with narrower parameters."
                    ),
                    "success": False,
                    "error_class": "tool_timeout",
                    "deadline_seconds": int(deadline_s),
                    "base_deadline_seconds": int(base_deadline_s),
                    "workflow_seconds_remaining": workflow_seconds_remaining,
                    "deadline_adjusted_for_workflow": deadline_adjusted,
                }
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=6.0)
            except asyncio.TimeoutError:
                if on_event is not None:
                    try:
                        await on_event({
                            "type": "status",
                            "tool": tool_name,
                            "message": f"running {tc['name']}… ({int(elapsed) + 6}s)",
                        })
                    except Exception:
                        pass
        return task.result()

    raw_results = await asyncio.gather(
        *(_run_one(tc) for tc in tool_calls),
        return_exceptions=True,
    )
    executed = []
    for tc, result in zip(tool_calls, raw_results):
        if isinstance(result, BaseException):
            logger.warning("Tool %s raised: %s", tc.get("name"), result)
            result = {
                "error": f"Tool {tc.get('name', '?')} raised {type(result).__name__}: {result}",
                "success": False,
            }
        executed.append({
            "id": tc["id"],
            "name": tc["name"],
            "input": tc["input"],
            "result": result,
        })
    return executed


def _tool_results_to_actions(all_tool_results: list[dict]) -> list[dict]:
    """Convert internal tool-result records into frontend action card structures."""
    actions: list[dict] = []
    for tr in all_tool_results:
        result = tr.get("result")
        if isinstance(result, dict) and result.get("__internal_suppressed__"):
            continue
        action = {
            "action": tr.get("tool"),
            "tool_input": tr.get("input"),
            "tool_result": result,
            "_auto_executed": True,
        }
        if tr.get("id"):
            action["_tool_call_id"] = tr.get("id")
        actions.append(action)
    return actions


def _tool_results_from_stream_audit(audit_trail: list[dict]) -> list[dict]:
    """Recover already-streamed tool results after an outer workflow timeout.

    The SSE endpoint wraps the full agent loop in ``asyncio.wait_for``.  When
    that outer timeout fires, the inner agent task is cancelled before it can
    return its normal consolidated ``actions`` list.  The thinking stream,
    however, has already emitted useful ``tool_result`` events to the browser
    and to ``audit_trail``.  Reconstruct a minimal tool-result list from that
    trail so timeout paths can still emit a deterministic, evidence-grounded
    summary instead of a blank/error-only assistant turn.

    This is intentionally generic: it does not special-case paper IDs,
    datasets, or prompts. It only reuses tool outputs that appeared in the
    current turn.
    """
    recovered: list[dict] = []
    seen_ids: set[str] = set()
    for evt in audit_trail or []:
        if not isinstance(evt, dict) or evt.get("type") != "tool_result":
            continue
        tool = str(evt.get("tool") or evt.get("name") or "").strip()
        if not tool:
            continue
        call_id = str(evt.get("tool_call_id") or evt.get("id") or "")
        dedupe_key = call_id or f"{tool}:{len(recovered)}"
        if dedupe_key in seen_ids:
            continue
        seen_ids.add(dedupe_key)
        recovered.append({
            "id": call_id or None,
            "tool": tool,
            "input": evt.get("input") if isinstance(evt.get("input"), dict) else {},
            "result": evt.get("result"),
        })
    return recovered
