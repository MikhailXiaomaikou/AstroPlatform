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
            "Multi-step research planner. USE ONLY for broad open-ended research "
            "requests that span >=3 distinct tool layers (e.g. 'design a study to "
            "test whether dark energy is dynamical', 'draft a paper section on the "
            "S8 tension'). DO NOT call this for single-tool calculations — for "
            "those, go DIRECT to the specific tool. Examples that should NOT use "
            "this planner: 'compute the Hubble tension' (use compare_luminosity_"
            "distances), 'run an Alcock-Paczynski test' (use assess_bao_bin_anomaly), "
            "'fit my distance modulus rows' (use fit_cosmology_mcmc), 'run a "
            "BAO+CMB+SN ΛCDM combined chain' (use run_cosmology_robustness_matrix). "
            "The plan returns hypotheses, probes, datasets, models, and gaps; it does "
            "NOT produce posterior numbers or fit results."
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
            "user explicitly asks for a generic plan-derived matrix. Cite only "
            "publication_ready=true cells as compressed-likelihood preliminary."
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
                "tool_results": {"type": "array", "items": {"type": "object"}},
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
                "tool_results": {"type": "array", "items": {"type": "object"}},
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
                "tool_results": {"type": "array", "items": {"type": "object"}},
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
        tool_results = inp.get("tool_results")
        return build_evidence_graph(
            tool_results=tool_results if isinstance(tool_results, list) else None,
            final_reply=str(inp.get("final_reply") or "") or None,
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


def _exec_verify_research_facts(inp: dict) -> dict:
    from app.services.research_program import verify_research_facts

    try:
        tool_results = inp.get("tool_results")
        evidence_graph = inp.get("evidence_graph")
        return verify_research_facts(
            tool_results=tool_results if isinstance(tool_results, list) else None,
            final_reply=str(inp.get("final_reply") or "") or None,
            evidence_graph=evidence_graph if isinstance(evidence_graph, dict) else None,
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


def _exec_export_research_report(inp: dict) -> dict:
    from app.services.research_program import export_research_report

    try:
        plan = inp.get("research_plan")
        graph = inp.get("evidence_graph")
        tool_results = inp.get("tool_results")
        return export_research_report(
            research_plan=plan if isinstance(plan, dict) else None,
            evidence_graph=graph if isinstance(graph, dict) else None,
            tool_results=tool_results if isinstance(tool_results, list) else None,
            title=str(inp.get("title") or "") or None,
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


async def dispatch_research(tool_name: str, tool_input: dict) -> dict | None:
    """Route the 5 research-program tools; None for unknown (caller guards).

    The executors are synchronous; this stays async for a uniform await-able
    interface with the other sibling dispatchers.
    """
    if tool_name == "plan_research_program":
        return _exec_plan_research_program(tool_input)
    elif tool_name == "run_research_matrix":
        return _exec_run_research_matrix(tool_input)
    elif tool_name == "build_evidence_graph":
        return _exec_build_evidence_graph(tool_input)
    elif tool_name == "verify_research_facts":
        return _exec_verify_research_facts(tool_input)
    elif tool_name == "export_research_report":
        return _exec_export_research_report(tool_input)
    return None
