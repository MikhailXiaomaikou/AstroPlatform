"""Research-program AI tools — extracted from ai_tools/__init__.py
(H1 split, 2026-05-26).

5 thin-wrapper tools over app.services.research_program: plan_research_program,
run_research_matrix, build_evidence_graph, verify_research_facts,
export_research_report. No shared ai_tools helpers are needed.

(research_workflow and full_research_report remain in ai_tools/__init__ for now:
the former is a large multi-step orchestrator, the latter has no standalone
executor.)

Dispatch mirrors the other sibling modules: ai_tools/__init__ routes
``tool_name in RESEARCH_TOOL_NAMES`` to ``dispatch_research``.
"""
from __future__ import annotations

import asyncio

RESEARCH_TOOL_NAMES = frozenset(
    {
        "plan_research_program",
        "run_research_matrix",
        "build_evidence_graph",
        "verify_research_facts",
        "export_research_report",
    }
)


RESEARCH_TOOL_SCHEMAS = [
    {
        "name": "plan_research_program",
        "description": (
            "DO NOT CALL FOR: 'Hubble tension' / 'compare Planck and SH0ES H0' / "
            "'preset vs preset distance' → use compare_luminosity_distances instead; "
            "'Alcock-Paczynski' / 'AP test' / 'BAO bin anomaly' / 'DM/DH ratio' → "
            "use assess_bao_bin_anomaly instead; 'fit distance modulus rows' / "
            "'ΛCDM/wCDM MCMC on this table' → use fit_cosmology_mcmc instead; "
            "'BAO+CMB+SN joint' / 'robustness matrix' / 'publication-ready ΛCDM "
            "combination' → use run_cosmology_robustness_matrix instead. "
            "ONLY USE for broad open-ended research turns that span >=3 distinct "
            "tool layers (e.g. 'design a study to test if dark energy is dynamical', "
            "'draft a paper section on the S8 tension'). Returns a research plan "
            "(hypotheses, probes, datasets, models, gaps); does NOT produce posterior "
            "numbers or fit results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The user's research question or blind-test prompt.",
                },
                "paper_metadata": {
                    "type": "object",
                    "description": "Optional paper metadata for reproduction-test mode.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "run_research_matrix",
        "description": (
            "Cross-module research-matrix orchestrator. PREFER the focus-specific "
            "version when the request is in one vertical: under "
            "ASTRO_RESEARCH_FOCUS=cosmology, use run_cosmology_robustness_matrix "
            "directly for BAO/SN/CMB combinations — it knows the cosmology dataset "
            "registry by name and produces tighter compressed-likelihood cells. "
            "Only use this generic run_research_matrix when (a) a previous "
            "plan_research_program call returned a multi-vertical plan, or (b) the "
            "user explicitly asks for a generic plan-derived matrix. The matrix "
            "aggregate is diagnostic-only; use a direct signed result for any "
            "numerical scientific claim."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "research_plan": {"type": "object", "description": "ResearchPlan from plan_research_program."},
                "question": {"type": "string", "description": "Fallback research question if no plan object is supplied."},
                "dataset_keys": {"type": "array", "items": {"type": "string"}},
                "models": {"type": "array", "items": {"type": "string"}},
                "random_seed": {"type": "integer"},
                "n_samples": {"type": "integer"},
            },
        },
    },
    {
        "name": "build_evidence_graph",
        "description": (
            "Build a claim-support graph from current-turn tool results. It links "
            "claimable parameters to publication-ready tool runs, datasets, citations, "
            "and flags unsupported numeric claims."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_results": {"type": "array", "items": {"type": "object"}, "description": "Optional hint only: in chat, the server-side record of tools that actually ran this turn is authoritative and cannot be overridden by this parameter."},
                "final_reply": {"type": "string"},
            },
        },
    },
    {
        "name": "verify_research_facts",
        "description": (
            "Verify final research claims against the current-turn evidence graph, "
            "tool runs, dataset registry metadata, extracted tables, and citations. "
            "This is a checking step only; it does not create new evidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_results": {"type": "array", "items": {"type": "object"}, "description": "Optional hint only: in chat, the server-side record of tools that actually ran this turn is authoritative and cannot be overridden by this parameter."},
                "final_reply": {"type": "string"},
                "evidence_graph": {"type": "object"},
            },
        },
    },
    {
        "name": "export_research_report",
        "description": (
            "Generate an auditable Markdown research report draft from a ResearchPlan, "
            "EvidenceGraph, and executed tool summaries. This is an export/drafting "
            "step and does not create new scientific evidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "research_plan": {"type": "object"},
                "evidence_graph": {"type": "object"},
                "tool_results": {"type": "array", "items": {"type": "object"}, "description": "Optional hint only: in chat, the server-side record of tools that actually ran this turn is authoritative and cannot be overridden by this parameter."},
                "title": {"type": "string"},
            },
        },
    },
]


def _exec_plan_research_program(inp: dict) -> dict:
    from app.services.research_program import plan_research_program

    try:
        metadata = inp.get("paper_metadata")
        return plan_research_program(
            question=str(inp.get("question") or ""),
            paper_metadata=metadata if isinstance(metadata, dict) else None,
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
        }


def _exec_run_research_matrix(inp: dict) -> dict:
    from app.services.research_program import run_research_matrix

    try:
        plan = inp.get("research_plan")
        dataset_keys = inp.get("dataset_keys")
        models = inp.get("models")
        return run_research_matrix(
            research_plan=plan if isinstance(plan, dict) else None,
            question=str(inp.get("question") or ""),
            dataset_keys=([str(key) for key in dataset_keys] if isinstance(dataset_keys, list) else None),
            models=([str(model) for model in models] if isinstance(models, list) else None),
            random_seed=int(inp["random_seed"]) if inp.get("random_seed") is not None else None,
            n_samples=int(inp.get("n_samples") or 4000),
        )
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
            "__message_to_model__": (
                "Research matrix execution failed. Do not quote posterior, "
                "robustness, tension, or fit statistics from this result."
            ),
        }


def _exec_build_evidence_graph(inp: dict) -> dict:
    from app.services.research_program import build_evidence_graph

    try:
        tool_results, source_warnings, source = _trusted_tool_results(inp)
        out = build_evidence_graph(
            tool_results=tool_results,
            final_reply=str(inp.get("final_reply") or "") or None,
        )
        return _stamp_evidence_source(out, source_warnings, source)
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
        }


def _trusted_tool_results(inp: dict) -> tuple[list | None, list[str], str]:
    """Resolve the tool_results evidence for the render/verify research tools.

    When the chat agent loop dispatched this call, it ALWAYS injects the
    turn's real accumulator as ``_turn_tool_results`` (the dispatcher strips
    any model-supplied copy of that key, so the model cannot forge it). The
    server record is then the ONLY admissible evidence — model-supplied
    ``tool_results`` were previously rendered verbatim into the report /
    fact-check, letting a fabricated ``{publication_ready: true, H0: ...}``
    payload become a user-facing deliverable AND re-ground its own numbers
    via the result text (live-confirmed bypass, 2026-06-12). Direct
    library/test callers (no injection) keep their payload — trusted code.

    Returns (tool_results, warnings, source_label). source_label is one of:
      - "server_turn_record": a NON-EMPTY trusted accumulator was injected;
        it is authoritative and any caller-supplied payload is ignored.
      - "caller_supplied_unverified": the chat path injected an EMPTY record
        (no tool produced results THIS turn) but the caller supplied a
        payload — ambiguous between an honest cross-turn echo of prior real
        results and a same-turn fabrication, which the server cannot tell
        apart. The draft is rendered from the caller payload so a legitimate
        "now export what we found" follow-up is not a blank report (review
        #13), but the output is stamped __do_not_claim__ so its numbers
        cannot ground claims (laundering stays closed).
      - "caller_supplied": no injection at all (direct library/test caller —
        trusted code, full passthrough)."""
    server = inp.get("_turn_tool_results")
    supplied = inp.get("tool_results")
    supplied = supplied if isinstance(supplied, list) else None
    warnings: list[str] = []
    if isinstance(server, list):
        if server:
            if supplied:
                server_ids = {e.get("id") for e in server if isinstance(e, dict)}
                unknown = sum(
                    1 for e in supplied
                    if not (isinstance(e, dict) and e.get("id") in server_ids)
                )
                if unknown:
                    warnings.append(
                        f"{unknown} caller-supplied tool_results entries did not match "
                        "this turn's server-side tool record and were IGNORED — the "
                        "output is built exclusively from tools that actually ran."
                    )
            return server, warnings, "server_turn_record"
        # Empty server record: no tool produced results this turn.
        if supplied:
            warnings.append(
                "No tool produced results in this turn; this draft was assembled "
                "from caller-supplied results that the server could NOT verify "
                "this turn. Its numbers are not claimable — re-run the analyses "
                "in this turn to cite them."
            )
            return supplied, warnings, "caller_supplied_unverified"
        return [], warnings, "server_turn_record"
    return supplied, warnings, "caller_supplied"


def _stamp_evidence_source(out: dict, warnings: list[str], source: str) -> dict:
    if not isinstance(out, dict):
        return out
    out["tool_results_source"] = source
    if source == "caller_supplied_unverified":
        # Numbers in an unverified draft must not ground reply claims: the
        # taint check (claim_validator._is_tainted_synthetic_payload) skips
        # any payload with __do_not_claim__, and publication_ready=False keeps
        # it out of the claimable-success paths.
        out["__do_not_claim__"] = True
        out["publication_ready"] = False
        # Review #5: the rendered draft may embed a model-supplied "verified"
        # fact-check. Prepend an unmistakable banner so the rendered prose
        # cannot be read as an authoritative result.
        _banner = (
            "> ⚠ UNVERIFIED DRAFT — no tool ran in this turn. Every number and "
            "any \"verified\" status below comes from caller-supplied data the "
            "server could not check this turn and must NOT be cited. Re-run the "
            "analyses in this turn to verify.\n\n"
        )
        for _key in ("markdown", "report_markdown", "paper_draft_markdown"):
            if isinstance(out.get(_key), str):
                out[_key] = _banner + out[_key]
    if warnings:
        existing = out.get("warnings")
        if isinstance(existing, list):
            existing.extend(warnings)
        else:
            out["warnings"] = list(warnings)
        note = " ".join(warnings)
        prior = str(out.get("__message_to_model__") or "")
        out["__message_to_model__"] = (prior + " " + note).strip()
    return out


def _trusted_evidence_graph(
    inp: dict,
    tool_results: list | None,
    source: str,
    warnings: list[str],
) -> dict | None:
    """Resolve the evidence_graph input under the same trust rule.

    A model-echoed graph is a second-order channel for the same laundering
    (fabricated node values render into the report and re-ground numbers).
    Whenever the chat path supplied a turn record (trusted OR unverified
    caller fallback), rebuild the graph from the resolved tool_results and
    ignore any supplied graph; only a direct library caller (no injection)
    may pass one through."""
    supplied = inp.get("evidence_graph")
    if source == "caller_supplied":
        return supplied if isinstance(supplied, dict) else None
    from app.services.research_program import build_evidence_graph

    if isinstance(supplied, dict) and supplied:
        warnings.append(
            "Caller-supplied evidence_graph was IGNORED and rebuilt from this "
            "turn's resolved tool record."
        )
    return build_evidence_graph(
        tool_results=tool_results,
        final_reply=str(inp.get("final_reply") or "") or None,
    )


def _exec_verify_research_facts(inp: dict) -> dict:
    from app.services.research_program import verify_research_facts

    try:
        tool_results, source_warnings, source = _trusted_tool_results(inp)
        if source == "caller_supplied_unverified":
            # Review #5: no tool produced results this turn, so a "verified"
            # verdict computed from caller-supplied data would mislead even
            # though __do_not_claim__ blocks its numbers from grounding. Refuse
            # to certify; route the model to re-run the analyses this turn.
            out = {
                "success": True,
                "__tool_status__": "PARTIAL",
                "analysis_status": "NOT_VERIFIABLE_THIS_TURN",
                "status": "not_verifiable_this_turn",
                "publication_ready": False,
                "__do_not_claim__": True,
                "claims": [],
                "verified_claim_count": 0,
                "note": (
                    "No tool ran in this turn, so the supplied claims could not "
                    "be verified against fresh tool evidence. Re-run the "
                    "analyses in this turn to obtain a verdict."
                ),
            }
            return _stamp_evidence_source(out, source_warnings, source)
        evidence_graph = _trusted_evidence_graph(inp, tool_results, source, source_warnings)
        out = verify_research_facts(
            tool_results=tool_results,
            final_reply=str(inp.get("final_reply") or "") or None,
            evidence_graph=evidence_graph,
        )
        return _stamp_evidence_source(out, source_warnings, source)
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
        }


def _exec_export_research_report(inp: dict) -> dict:
    from app.services.research_program import export_research_report

    try:
        plan = inp.get("research_plan")
        tool_results, source_warnings, source = _trusted_tool_results(inp)
        graph = _trusted_evidence_graph(inp, tool_results, source, source_warnings)
        out = export_research_report(
            research_plan=plan if isinstance(plan, dict) else None,
            evidence_graph=graph,
            tool_results=tool_results,
            title=str(inp.get("title") or "") or None,
        )
        return _stamp_evidence_source(out, source_warnings, source)
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
        }


async def dispatch_research(tool_name: str, tool_input: dict) -> dict | None:
    """Route the 5 research-program tools; None for unknown (caller guards).

    The executors are synchronous; this stays async for a uniform await-able
    interface with the other sibling dispatchers.
    """
    if tool_name == "plan_research_program":
        return _exec_plan_research_program(tool_input)
    elif tool_name == "run_research_matrix":
        # Run OFF the event loop: this calls run_likelihood_chain per
        # dataset×model cell (n_samples=4000), so a multi-cell matrix blocks
        # the worker for the full wall-clock duration — no SSE heartbeats for
        # any chat, and the per-tool deadline can only fire at an await point.
        # Mirror dispatch_cosmology's run_cosmology_likelihood_chain branch.
        return await asyncio.to_thread(_exec_run_research_matrix, tool_input)
    elif tool_name == "build_evidence_graph":
        return _exec_build_evidence_graph(tool_input)
    elif tool_name == "verify_research_facts":
        return _exec_verify_research_facts(tool_input)
    elif tool_name == "export_research_report":
        return _exec_export_research_report(tool_input)
    return None
