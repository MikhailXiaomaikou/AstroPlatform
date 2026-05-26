"""Paper-tool-mining AI tools — extracted from ai_tools/__init__.py (H1 split, 2026-05-26).

The 7 paper-tool-mining tools (mine_paper_tools, run_paper_tool_mining_batch,
build_tool_ontology, build_tool_gap_matrix, rank_tool_implementation_queue,
build_paper_mining_candidate_pool, run_paper_tool_mining_loop). Implementations
are thin wrappers over paper_tool_mining / paper_candidate_pool /
paper_tool_mining_loop; no shared ai_tools helpers are needed.

Dispatch mirrors ai_tools_cosmology / solar_system / exoplanet: ai_tools/__init__
routes ``tool_name in PAPER_MINING_TOOL_NAMES`` to ``dispatch_paper_mining``.
"""
from __future__ import annotations

PAPER_MINING_TOOL_NAMES = frozenset(
    {
        "mine_paper_tools",
        "run_paper_tool_mining_batch",
        "build_tool_ontology",
        "build_tool_gap_matrix",
        "rank_tool_implementation_queue",
        "build_paper_mining_candidate_pool",
        "run_paper_tool_mining_loop",
    }
)


PAPER_MINING_TOOL_SCHEMAS = [
    {
        "name": "mine_paper_tools",
        "description": (
            "Mine a full paper's methods/data/tables/equations for ToolSpec records: "
            "data loaders, table extractors, likelihoods, samplers, fitters, diagnostics, "
            "plotters, exporters, validators. This maps infrastructure requirements only; "
            "it does not support scientific result claims. Abstract-only input is low "
            "confidence and blocked from high-confidence tool extraction."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "arxiv_id": {"type": "string"},
                "doi": {"type": "string"},
                "bibcode": {"type": "string"},
                "paper_url": {"type": "string"},
                "paper_metadata": {"type": "object"},
                "paper_text": {"type": "string", "description": "Full or substantial paper text, not just abstract."},
                "source_sections": {
                    "description": "Section mapping/list with text spans from methods/data/tables/appendix.",
                    "oneOf": [
                        {"type": "object"},
                        {"type": "array", "items": {"type": "object"}},
                    ],
                },
                "max_tools": {"type": "integer"},
            },
        },
    },
    {
        "name": "run_paper_tool_mining_batch",
        "description": (
            "Mine ToolSpecs from a batch of paper payloads and produce ontology, "
            "gap matrix, and implementation queue. Use for surveying 20-50 papers "
            "before deciding what Standard Astro should build next. Output is an "
            "infrastructure map, not a science result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "papers": {"type": "array", "items": {"type": "object"}},
                "domain_tag": {"type": "string"},
                "max_papers": {"type": "integer"},
            },
            "required": ["papers"],
        },
    },
    {
        "name": "build_tool_ontology",
        "description": (
            "Cluster mined ToolSpecs by tool category and canonical capability so "
            "the platform can see recurring research infrastructure needs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_specs": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["tool_specs"],
        },
    },
    {
        "name": "build_tool_gap_matrix",
        "description": (
            "Compare mined ToolSpecs against current Standard Astro capabilities and "
            "label capabilities as available, partial, missing, or blocked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_specs": {"type": "array", "items": {"type": "object"}},
                "current_tools": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["tool_specs"],
        },
    },
    {
        "name": "rank_tool_implementation_queue",
        "description": (
            "Rank missing/partial capability rows from a Tool Gap Matrix into an "
            "engineering implementation queue by paper frequency, evidence, and status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "gap_matrix": {"type": "array", "items": {"type": "object"}},
                "max_items": {"type": "integer"},
            },
            "required": ["gap_matrix"],
        },
    },
    {
        "name": "build_paper_mining_candidate_pool",
        "description": (
            "Build a deduplicated candidate-paper pool for the paper-to-tool mining "
            "loop. It can use supplied seed papers or, only when explicitly enabled, "
            "live arXiv search. Candidate pools are inputs to ToolSpec mining and do "
            "not support scientific result claims."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain_tag": {"type": "string"},
                "queries": {"type": "array", "items": {"type": "string"}},
                "seed_papers": {"type": "array", "items": {"type": "object"}},
                "state": {"type": "object"},
                "exclude_paper_ids": {"type": "array", "items": {"type": "string"}},
                "max_papers": {"type": "integer"},
                "allow_live_search": {
                    "type": "boolean",
                    "description": "When true, query arXiv for candidate papers. Default false.",
                },
                "fetch_text_preview": {
                    "type": "boolean",
                    "description": "Best-effort bounded PDF text preview for method/table spans. Default false.",
                },
                "text_preview_limit": {
                    "type": "integer",
                    "description": "Maximum candidate PDFs to preview when fetch_text_preview=true. Default 20.",
                },
                "max_pages_per_query": {
                    "type": "integer",
                    "description": "Maximum arXiv pages to request per query when live search is enabled. Default 2.",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["submittedDate", "lastUpdatedDate", "relevance"],
                    "description": "arXiv live-search sort mode. Use relevance after recent-paper pages are exhausted.",
                },
                "sort_order": {
                    "type": "string",
                    "enum": ["ascending", "descending"],
                    "description": "arXiv live-search sort order. Default descending.",
                },
            },
        },
    },
    {
        "name": "run_paper_tool_mining_loop",
        "description": (
            "Run bounded consecutive paper-to-tool mining rounds. Each round selects "
            "the next unread papers, normally 20, mines ToolSpecs, builds a gap matrix, "
            "and updates loop state. This is local/infrastructure planning only and "
            "does not produce scientific results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "papers": {"type": "array", "items": {"type": "object"}},
                "domain_tag": {"type": "string"},
                "state": {"type": "object"},
                "batch_size": {"type": "integer", "description": "Default 20."},
                "max_rounds": {"type": "integer", "description": "Bounded loop count; default 1."},
                "write_local_bundle": {
                    "type": "boolean",
                    "description": "Write sanitized local-only bundles under .local/paper_tool_mining.",
                },
            },
            "required": ["papers"],
        },
    },
]


def _exec_mine_paper_tools(inp: dict) -> dict:
    from app.services.paper_tool_mining import mine_paper_tools

    try:
        metadata = inp.get("paper_metadata")
        sections = inp.get("source_sections")
        return mine_paper_tools(
            arxiv_id=str(inp.get("arxiv_id") or "") or None,
            doi=str(inp.get("doi") or "") or None,
            bibcode=str(inp.get("bibcode") or "") or None,
            paper_url=str(inp.get("paper_url") or "") or None,
            paper_metadata=metadata if isinstance(metadata, dict) else None,
            paper_text=str(inp.get("paper_text") or "") or None,
            source_sections=sections if isinstance(sections, (dict, list)) else None,
            max_tools=int(inp.get("max_tools") or 80),
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


def _exec_run_paper_tool_mining_batch(inp: dict) -> dict:
    from app.services.paper_tool_mining import run_paper_tool_mining_batch

    try:
        papers = inp.get("papers")
        return run_paper_tool_mining_batch(
            papers=papers if isinstance(papers, list) else [],
            domain_tag=str(inp.get("domain_tag") or "observational_cosmology"),
            max_papers=int(inp.get("max_papers") or 50),
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


def _exec_build_tool_ontology(inp: dict) -> dict:
    from app.services.paper_tool_mining import build_tool_ontology

    try:
        specs = inp.get("tool_specs")
        return build_tool_ontology(tool_specs=specs if isinstance(specs, list) else [])
    except Exception as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": str(exc),
            "error_class": exc.__class__.__name__,
            "__do_not_claim__": True,
        }


def _exec_build_tool_gap_matrix(inp: dict) -> dict:
    from app.services.paper_tool_mining import build_tool_gap_matrix

    try:
        specs = inp.get("tool_specs")
        current_tools = inp.get("current_tools")
        return build_tool_gap_matrix(
            tool_specs=specs if isinstance(specs, list) else [],
            current_tools=([str(t) for t in current_tools] if isinstance(current_tools, list) else None),
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


def _exec_rank_tool_implementation_queue(inp: dict) -> dict:
    from app.services.paper_tool_mining import rank_tool_implementation_queue

    try:
        matrix = inp.get("gap_matrix")
        return rank_tool_implementation_queue(
            gap_matrix=matrix if isinstance(matrix, list) else [],
            max_items=int(inp.get("max_items") or 20),
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


async def _exec_build_paper_mining_candidate_pool(inp: dict) -> dict:
    from app.services.paper_candidate_pool import build_paper_mining_candidate_pool

    try:
        queries = inp.get("queries")
        seed_papers = inp.get("seed_papers")
        state = inp.get("state")
        exclude_paper_ids = inp.get("exclude_paper_ids")
        return await build_paper_mining_candidate_pool(
            domain_tag=str(inp.get("domain_tag") or "observational_cosmology"),
            queries=[str(q) for q in queries] if isinstance(queries, list) else None,
            seed_papers=seed_papers if isinstance(seed_papers, list) else None,
            state=state if isinstance(state, dict) else None,
            exclude_paper_ids=[str(x) for x in exclude_paper_ids] if isinstance(exclude_paper_ids, list) else None,
            max_papers=int(inp.get("max_papers") or 60),
            allow_live_search=bool(inp.get("allow_live_search")),
            fetch_text_preview=bool(inp.get("fetch_text_preview")),
            text_preview_limit=int(inp.get("text_preview_limit") or 20),
            max_pages_per_query=int(inp.get("max_pages_per_query") or 2),
            sort_by=str(inp.get("sort_by") or "submittedDate"),
            sort_order=str(inp.get("sort_order") or "descending"),
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


def _exec_run_paper_tool_mining_loop(inp: dict) -> dict:
    from app.services.paper_tool_mining_loop import run_paper_tool_mining_loop

    try:
        papers = inp.get("papers")
        state = inp.get("state")
        return run_paper_tool_mining_loop(
            papers=papers if isinstance(papers, list) else [],
            domain_tag=str(inp.get("domain_tag") or "observational_cosmology"),
            state=state if isinstance(state, dict) else None,
            batch_size=int(inp.get("batch_size") or 20),
            max_rounds=int(inp.get("max_rounds") or 1),
            write_local_bundle=bool(inp.get("write_local_bundle")),
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


async def dispatch_paper_mining(tool_name: str, tool_input: dict) -> dict | None:
    """Route the 7 paper-tool-mining tools; None for unknown (caller guards)."""
    if tool_name == "mine_paper_tools":
        return _exec_mine_paper_tools(tool_input)
    elif tool_name == "run_paper_tool_mining_batch":
        return _exec_run_paper_tool_mining_batch(tool_input)
    elif tool_name == "build_tool_ontology":
        return _exec_build_tool_ontology(tool_input)
    elif tool_name == "build_tool_gap_matrix":
        return _exec_build_tool_gap_matrix(tool_input)
    elif tool_name == "rank_tool_implementation_queue":
        return _exec_rank_tool_implementation_queue(tool_input)
    elif tool_name == "build_paper_mining_candidate_pool":
        return await _exec_build_paper_mining_candidate_pool(tool_input)
    elif tool_name == "run_paper_tool_mining_loop":
        return _exec_run_paper_tool_mining_loop(tool_input)
    return None
