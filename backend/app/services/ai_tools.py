"""Tool definitions and executors for the Claude AI research agent.

Each tool is a function Claude can call. The agent loop in chat.py
handles the tool_use → result → next message cycle automatically.
"""

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Tool Definitions (Anthropic tool_use format) ──

# In-memory cache for last search results per user session (keyed by a simple token)
_search_result_cache: dict[str, list[dict]] = {}


def store_search_results(key: str, results: list[dict]) -> None:
    """Cache search results so AI can access full data later."""
    _search_result_cache[key] = results
    # Keep only last 20 sessions
    if len(_search_result_cache) > 20:
        oldest = list(_search_result_cache.keys())[0]
        del _search_result_cache[oldest]


def get_cached_results(key: str) -> list[dict] | None:
    return _search_result_cache.get(key)


TOOLS = [
    {
        "name": "search_objects",
        "description": (
            "Search astronomical databases for objects by name, coordinates, or scientific criteria. "
            "Searches SIMBAD, Gaia, SDSS, NED, etc. Returns object names, positions, types, magnitudes, redshifts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Object name or search description (e.g. 'M31', 'NGC 1068', 'high redshift quasars')"},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Data sources to query (e.g. ['simbad', 'gaia', 'sdss']). Default: ['simbad']"},
                "radius": {"type": "number", "description": "Search radius in degrees. Default: 0.1"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "run_adql",
        "description": (
            "Execute an ADQL query on Gaia DR3, SIMBAD, VizieR, or CADC TAP services. "
            "Use this for precise database queries with column selection and filtering."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "ADQL query string"},
                "service": {"type": "string", "enum": ["gaia", "simbad", "vizier", "cadc"], "description": "TAP service to query"},
            },
            "required": ["query", "service"],
        },
    },
    {
        "name": "get_object_info",
        "description": (
            "Get comprehensive information about a specific astronomical object: "
            "type, redshift, spectral type, morphology, cross-identifications (all known names), "
            "which surveys have data, and literature references from ADS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Object name (e.g. 'M 77', 'NGC 1068', 'Sirius')"},
                "ra": {"type": "number", "description": "RA in degrees (optional, helps resolve ambiguous names)"},
                "dec": {"type": "number", "description": "Dec in degrees (optional)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "analyze_spectrum",
        "description": (
            "Analyze a FITS spectrum file: detect peaks, classify continuum shape, "
            "estimate redshift, identify spectral lines. Use this when the user has "
            "uploaded a FITS file or fetched one from a database."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {"type": "string", "description": "Path to the FITS file in storage"},
            },
            "required": ["fits_path"],
        },
    },
    {
        "name": "generate_pipeline",
        "description": (
            "Create a data processing pipeline DAG from a workflow description. "
            "Available nodes: LoadData, Denoise, SpectralFit, RedshiftEstimate, "
            "EquivalentWidth, SEDFit, CoordTransform, CrossMatch, PhotCalibrate, "
            "ImageStack, Plot, InteractivePlot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Pipeline name"},
                "description": {"type": "string", "description": "What the pipeline does"},
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "type": {"type": "string"},
                            "params": {"type": "object"},
                        },
                        "required": ["id", "type"],
                    },
                    "description": "Pipeline nodes in execution order",
                },
                "edges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "target": {"type": "string"},
                        },
                        "required": ["source", "target"],
                    },
                    "description": "Edges connecting nodes",
                },
            },
            "required": ["name", "nodes", "edges"],
        },
    },
    {
        "name": "search_literature",
        "description": "Search NASA ADS for academic papers about an astronomical object or topic. Returns titles, authors, years, and paper abstracts that you can cite in your response.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Object name or search query for ADS"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_last_search_results",
        "description": (
            "Retrieve the full list of the user's most recent search results "
            "(up to 200 objects with all fields). Use when the user asks about "
            "'my results', 'the objects I found', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Max results to return (default 50)", "default": 50},
            },
        },
    },
    {
        "name": "run_pipeline",
        "description": (
            "Execute a pipeline DAG synchronously and return the results. "
            "Use after generating a pipeline to actually run it on data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dag": {
                    "type": "object",
                    "description": "Pipeline DAG with nodes and edges arrays",
                },
                "input_data_id": {"type": "string", "description": "FITS file path to use as input"},
            },
            "required": ["dag", "input_data_id"],
        },
    },
    {
        "name": "run_python",
        "description": (
            "Execute Python code for data analysis, statistical modeling, or visualization. "
            "Available libraries: numpy (as np), scipy, astropy (Table, SkyCoord, units as u), "
            "matplotlib.pyplot (as plt), pandas. "
            "Helper functions: load_fits(path) returns astropy HDUList, "
            "get_search_results() returns the latest search results as a list of dicts. "
            "Use print() to output results. Matplotlib figures are automatically captured. "
            "Max execution time: 30 seconds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute. Use print() for output and plt for plots.",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what the code does",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "read_arxiv_paper",
        "description": (
            "Download and read the text content of an arXiv paper. "
            "Provide the arXiv ID (e.g. '2301.12345'). Returns the paper's "
            "title, abstract, and extracted text from the PDF."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "arxiv_id": {"type": "string", "description": "arXiv paper ID (e.g. '2301.12345')"},
            },
            "required": ["arxiv_id"],
        },
    },
]


# ── Tool Executors ──

async def execute_tool(tool_name: str, tool_input: dict, api_key: str = "") -> dict:
    """Execute a tool call and return the result as a dict."""
    try:
        if tool_name == "search_objects":
            return await _exec_search(tool_input)
        elif tool_name == "run_adql":
            return await _exec_adql(tool_input)
        elif tool_name == "get_object_info":
            return await _exec_object_info(tool_input)
        elif tool_name == "analyze_spectrum":
            return await _exec_analyze(tool_input, api_key)
        elif tool_name == "generate_pipeline":
            return _exec_pipeline(tool_input)
        elif tool_name == "search_literature":
            return await _exec_literature(tool_input)
        elif tool_name == "run_python":
            return await _exec_run_python(tool_input)
        elif tool_name == "get_last_search_results":
            return _exec_get_cached_results(tool_input)
        elif tool_name == "run_pipeline":
            return await _exec_run_pipeline(tool_input)
        elif tool_name == "read_arxiv_paper":
            return await _exec_read_paper(tool_input)
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        logger.warning("Tool %s failed: %s", tool_name, e)
        return {"error": str(e)}


async def _exec_search(inp: dict) -> dict:
    from app.connectors.registry import CONNECTORS_KEYS, get_connector
    from app.api.data import _astro_to_result
    from app.search.query_parser import parse_natural_query

    query = inp.get("query", "")
    sources = inp.get("sources", ["simbad"])
    radius = inp.get("radius", 0.1)
    sources = [s for s in sources if s in CONNECTORS_KEYS]
    if not sources:
        sources = ["simbad"]

    parsed = parse_natural_query(query)
    object_type = parsed.get("object_type")
    redshift_min = parsed.get("redshift_min")
    redshift_max = parsed.get("redshift_max")
    has_criteria = any([object_type, redshift_min, redshift_max])

    # Resolve coordinates
    search_ra, search_dec = None, None
    import re
    obj_match = re.search(
        r'\b(M\s*\d+|NGC\s*\d+|IC\s*\d+|Mrk\s*\d+)\b', query, re.IGNORECASE
    )
    if obj_match:
        try:
            from astropy.coordinates import SkyCoord
            coord = SkyCoord.from_name(obj_match.group(0))
            search_ra, search_dec = coord.ra.deg, coord.dec.deg
        except Exception:
            pass

    async def _search_one(source: str):
        connector = get_connector(source)
        if source == "simbad" and has_criteria and hasattr(connector, "search_by_criteria"):
            return await asyncio.wait_for(
                connector.search_by_criteria(
                    object_type=object_type,
                    redshift_min=redshift_min,
                    redshift_max=redshift_max,
                    ra=search_ra, dec=search_dec, radius=radius,
                ), timeout=30.0,
            )
        search_q = obj_match.group(0) if obj_match else query
        return await asyncio.wait_for(
            connector.search(search_q, ra=search_ra, dec=search_dec, radius=radius),
            timeout=30.0,
        )

    tasks = [_search_one(s) for s in sources]
    results_per = await asyncio.gather(*tasks, return_exceptions=True)

    all_results = []
    for src, res in zip(sources, results_per):
        if isinstance(res, Exception):
            all_results.append({"source": src, "error": str(res)})
        else:
            for obj in res[:25]:
                r = _astro_to_result(obj).model_dump()
                all_results.append(r)

    # Cache results for later retrieval by get_last_search_results
    store_search_results("latest", all_results)

    return {"results": all_results, "total": len(all_results)}


async def _exec_adql(inp: dict) -> dict:
    from app.api.integration import adql_query, ADQLRequest
    req = ADQLRequest(query=inp.get("query", ""), service=inp.get("service", "gaia"))
    result = await adql_query(req)
    # Truncate for context window
    data = result.get("data", {}) if isinstance(result, dict) else {}
    row_count = result.get("row_count", 0) if isinstance(result, dict) else 0
    truncated = {}
    for col, vals in data.items():
        truncated[col] = vals[:20] if isinstance(vals, list) else vals
    adql_result = {
        "columns": result.get("columns", []) if isinstance(result, dict) else [],
        "data": truncated,
        "row_count": row_count,
        "showing": min(20, row_count),
    }

    # Auto-inject full result into Python sandbox for immediate use
    store_search_results("latest_adql", [
        {col: data.get(col, [None] * row_count)[i] for col in adql_result["columns"]}
        for i in range(min(row_count, 200))
    ] if data else [])

    return adql_result


async def _exec_object_info(inp: dict) -> dict:
    from app.connectors.simbad import SIMBADConnector
    simbad = SIMBADConnector()

    detail = await simbad.get_object_detail(inp["name"])
    if not detail:
        return {"error": f"Object '{inp['name']}' not found in SIMBAD"}

    ids = await simbad.get_identifiers(detail.get("name", inp["name"]))

    # Get ADS refs
    refs = []
    try:
        from app.api.citations import _search_ads_sync
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, _search_ads_sync, detail.get("name", inp["name"]))
        refs = [{"title": r["title"], "year": r["year"], "bibcode": r["bibcode"]} for r in (raw or [])[:5]]
    except Exception:
        pass

    return {
        **detail,
        "cross_ids": [i["name"] for i in ids[:20]],
        "references": refs,
    }


async def _exec_analyze(inp: dict, api_key: str) -> dict:
    from app.services.spectrum_analyzer import extract_spectrum_from_fits, analyze_spectrum, build_claude_prompt
    from app.pipeline.nodes.redshift import redshift_estimate

    spec = extract_spectrum_from_fits(inp["fits_path"])
    summary = analyze_spectrum(spec["wavelength"], spec["flux"])

    rz_result = None
    if len(spec["wavelength"]) >= 50:
        try:
            rz = redshift_estimate(
                {"data": spec},
                {"method": "chi2_grid", "z_min": 0.0, "z_max": 2.0, "z_step": 0.001},
            )
            rz_result = rz.get("redshift_result")
        except Exception:
            pass

    return {
        "continuum_shape": summary.continuum_shape,
        "n_peaks": len(summary.peaks),
        "emission_peaks": [{"wavelength": p.wavelength, "snr": p.snr} for p in summary.peaks if p.is_emission][:10],
        "absorption_features": [{"wavelength": p.wavelength, "snr": p.snr} for p in summary.peaks if not p.is_emission][:10],
        "wavelength_range": [summary.wavelength_min, summary.wavelength_max],
        "redshift_estimate": rz_result,
        "prompt_summary": build_claude_prompt(summary, rz_result),
    }


def _exec_pipeline(inp: dict) -> dict:
    nodes = inp.get("nodes", [])
    edges = inp.get("edges", [])

    # Auto-position
    for i, node in enumerate(nodes):
        if "position" not in node:
            node["position"] = {"x": i * 300, "y": 150}
        if "data" not in node:
            node["data"] = {"label": node.get("type", ""), "params": node.get("params", {})}
        else:
            node["data"].setdefault("label", node.get("type", ""))
            node["data"].setdefault("params", node.get("params", {}))

    # Auto-generate edge IDs
    for i, edge in enumerate(edges):
        if "id" not in edge:
            edge["id"] = f"e{edge['source']}-{edge['target']}"

    dag = {"nodes": nodes, "edges": edges}
    return {
        "name": inp.get("name", "AI Pipeline"),
        "description": inp.get("description", ""),
        "dag": dag,
        "status": "created",
    }


async def _exec_literature(inp: dict) -> dict:
    try:
        from app.api.citations import _search_ads_sync
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, _search_ads_sync, inp["query"])
        if not raw:
            return {"results": [], "message": "No papers found. ADS API key may not be configured."}
        return {
            "results": [
                {
                    "title": r["title"],
                    "authors": r["authors"][:3],
                    "year": r["year"],
                    "bibcode": r["bibcode"],
                    "abstract": (r.get("abstract") or "")[:500],
                }
                for r in raw[:8]
            ]
        }
    except Exception as e:
        return {"error": str(e)}


async def _exec_run_python(inp: dict) -> dict:
    """Execute Python code in sandboxed environment."""
    from app.services.code_executor import execute_python

    code = inp.get("code", "")
    if not code.strip():
        return {"error": "No code provided"}

    # Run in executor with timeout to not block the event loop
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, execute_python, code, None),
            timeout=35.0,
        )
    except asyncio.TimeoutError:
        return {"success": False, "error": "Code execution timed out after 35 seconds", "stdout": ""}

    response: dict = {
        "success": result.success,
        "stdout": result.stdout[:5000] if result.stdout else "",
    }

    if result.error:
        response["error"] = result.error
    if result.stderr and not result.success:
        response["traceback"] = result.stderr[:2000]
    if result.figures:
        response["figures"] = result.figures[:5]  # max 5 figures
        response["figure_count"] = len(result.figures)
    if result.variables:
        response["variables"] = dict(list(result.variables.items())[:20])

    return response


def _exec_get_cached_results(inp: dict) -> dict:
    """Return full cached search results."""
    max_n = inp.get("max_results", 50)
    results = get_cached_results("latest")
    if results is None:
        return {"results": [], "message": "No recent search results cached. Run a search first."}
    return {"results": results[:max_n], "total": len(results)}


async def _exec_run_pipeline(inp: dict) -> dict:
    """Execute a pipeline DAG synchronously and return results."""
    from app.pipeline.engine import execute_dag, topological_sort
    from app.pipeline.nodes import registry

    dag = inp.get("dag", {})
    input_data_id = inp.get("input_data_id", "")

    if "nodes" not in dag or "edges" not in dag:
        return {"error": "DAG must have 'nodes' and 'edges'"}

    # Validate node types
    for node in dag.get("nodes", []):
        if node.get("type") not in registry:
            return {"error": f"Unknown node type: {node.get('type')}"}

    try:
        topological_sort(dag)
    except ValueError as e:
        return {"error": str(e)}

    import uuid
    run_id = str(uuid.uuid4())
    try:
        # Run in executor to avoid blocking the async event loop
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, execute_dag, dag, input_data_id, run_id)
    except Exception as e:
        return {"error": f"Pipeline execution failed: {e}"}

    # Summarize results (truncate large data arrays)
    summary = {}
    for nid, res in results.items():
        s = {}
        if "error" in res:
            s["error"] = res["error"]
        else:
            for k, v in res.items():
                if k == "data" and isinstance(v, dict):
                    s["data_keys"] = list(v.keys())
                    s["data_lengths"] = {dk: len(dv) if isinstance(dv, list) else "scalar" for dk, dv in v.items()}
                elif isinstance(v, list) and len(v) > 10:
                    s[k] = f"[{len(v)} items]"
                else:
                    s[k] = v
        summary[nid] = s

    return {"run_id": run_id, "status": "completed", "results": summary}


async def _exec_read_paper(inp: dict) -> dict:
    """Download an arXiv paper and extract text content."""
    import httpx
    import re

    arxiv_id = inp.get("arxiv_id", "").strip()
    if not arxiv_id:
        return {"error": "arxiv_id is required"}

    # Clean the ID (remove 'arXiv:' prefix if present)
    arxiv_id = re.sub(r'^arXiv:', '', arxiv_id, flags=re.IGNORECASE)

    # Fetch paper metadata from arXiv API
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"http://export.arxiv.org/api/query?id_list={arxiv_id}")
            resp.raise_for_status()
    except Exception as e:
        return {"error": f"Failed to fetch arXiv metadata: {e}"}

    xml_text = resp.text

    # Parse title and abstract from Atom XML
    title_match = re.search(r'<title[^>]*>(.*?)</title>', xml_text, re.DOTALL)
    abstract_match = re.search(r'<summary[^>]*>(.*?)</summary>', xml_text, re.DOTALL)
    authors = re.findall(r'<name>(.*?)</name>', xml_text)

    title = title_match.group(1).strip() if title_match else "Unknown"
    # Skip the first title match (it's the feed title "ArXiv Query")
    titles = re.findall(r'<title[^>]*>(.*?)</title>', xml_text, re.DOTALL)
    if len(titles) > 1:
        title = titles[1].strip()

    abstract = abstract_match.group(1).strip() if abstract_match else ""
    # Clean up whitespace
    abstract = re.sub(r'\s+', ' ', abstract)

    # Try to get the PDF and extract text (best-effort)
    pdf_text = ""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            pdf_resp = await client.get(f"https://arxiv.org/pdf/{arxiv_id}")
            if pdf_resp.status_code == 200 and len(pdf_resp.content) < 5_000_000:
                # Try to extract text from PDF
                try:
                    import io
                    from pdfminer.high_level import extract_text as _extract_pdf
                    pdf_text = _extract_pdf(io.BytesIO(pdf_resp.content))
                    # Truncate to first ~3000 chars (enough for intro + methods)
                    pdf_text = pdf_text[:3000]
                except ImportError:
                    pdf_text = "(pdfminer not installed — only abstract available)"
                except Exception:
                    pdf_text = "(PDF text extraction failed — only abstract available)"
    except Exception:
        pass

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": authors[:10],
        "abstract": abstract,
        "pdf_text_preview": pdf_text[:2000] if pdf_text else "",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
    }
