"""Research workflow tools: cached results, validation, drafts, proposals, papers.

Moved verbatim out of app/services/ai_tools/__init__.py (H2 split,
2026-07-03). Tool schemas here cover: get_last_search_results, validate_analysis, generate_paper_draft,
run_pipeline, generate_proposal, query_transients, read_arxiv_paper,
research_workflow.
Schemas are reassembled into TOOLS (exact pre-split order) and tool
calls are still dispatched by _execute_tool_inner in the package
__init__ — this module is an implementation detail, import from
app.services.ai_tools.
"""

import asyncio
import hashlib
from copy import deepcopy
from typing import Any

from app.services.ai_tools import _session_cache_key, get_cached_results
from app.services.ai_tools.catalog_queries import _exec_search
from app.services.ai_tools.literature import _exec_literature

TOOL_SCHEMAS = [
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
        "name": "validate_analysis",
        "description": (
            "Run a scientific rigor audit on the current saved chat session before paper export. "
            "Use this to check unit consistency, statistical assumptions, provenance, and completeness."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Saved chat session ID. If omitted, the current saved session ID is inferred from the Python session when possible.",
                },
            },
        },
    },
    {
        "name": "generate_paper_draft",
        "description": (
            "Generate a structured paper draft from a saved chat session. "
            "Use when the user asks to write up results, create a manuscript, or export analysis as a paper."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Saved chat session ID. If omitted, the current saved session ID is inferred from the Python session when possible.",
                },
                "journal_format": {
                    "type": "string",
                    "enum": ["aastex", "mnras", "aa"],
                    "description": "Target journal format. Ask the user if they have a preference; default is aastex.",
                },
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
                "random_seed": {
                    "type": "integer",
                    "description": (
                        "Outer reproducibility seed. BayesianFit and TimeSeriesAnalysis "
                        "nodes without their own seed receive stable per-node seeds "
                        "derived from this value and the node id."
                    ),
                },
            },
            "required": ["dag", "input_data_id"],
        },
    },
    {
        "name": "generate_proposal",
        "description": (
            "Gather data for an observation proposal. Resolves target coordinates, "
            "computes visibility from the telescope's observatory, estimates exposure "
            "time if target magnitude is known, and finds relevant literature. "
            "Returns structured information for drafting a telescope proposal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_name": {"type": "string", "description": "Target object name (e.g., 'NGC 1275')"},
                "telescope": {"type": "string", "description": "Telescope: vlt, keck, gemini, hst, jwst, alma, subaru"},
                "instrument": {"type": "string", "description": "Instrument name (optional, for reference)"},
                "science_goal": {"type": "string", "description": "Brief science justification"},
                "exposure_hours": {"type": "number", "description": "Requested exposure time in hours (optional)"},
            },
            "required": ["target_name", "telescope", "science_goal"],
        },
    },
    {
        "name": "query_transients",
        "description": (
            "Search for astronomical transients and alerts from TNS (Transient Name Server) "
            "and ZTF/Lasair. Find recent supernovae, novae, tidal disruption events, and other "
            "transients by name, coordinates, or type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Transient name (e.g., 'SN 2024abc', 'AT 2024xyz')"},
                "ra": {"type": "number", "description": "Right ascension in degrees"},
                "dec": {"type": "number", "description": "Declination in degrees"},
                "radius_arcsec": {"type": "number", "description": "Search radius in arcseconds (default 10)"},
                "days_back": {"type": "integer", "description": "Search window in days (default 30)"},
                "obj_type": {"type": "string", "description": "Filter by type: SN, SN Ia, SN II, TDE, nova, AGN, etc."},
            },
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
    {
        "name": "research_workflow",
        "description": (
            "Plan and execute a complete research workflow from hypothesis to conclusion. "
            "Use this when the user poses a research question or hypothesis that requires "
            "multi-step data analysis. The tool helps structure the investigation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hypothesis": {
                    "type": "string",
                    "description": "The research hypothesis or question to investigate",
                },
                "scope": {
                    "type": "string",
                    "enum": ["quick", "thorough"],
                    "description": "quick = rapid verification with basic statistics; thorough = comprehensive analysis with multiple tests",
                },
            },
            "required": ["hypothesis"],
        },
    },
]


_STOCHASTIC_PIPELINE_NODE_TYPES = frozenset({"BayesianFit", "TimeSeriesAnalysis"})


def _derive_pipeline_node_seed(outer_seed: int, node_id: str) -> int:
    """Derive a stable uint32 seed for one node in a seeded pipeline run."""
    payload = f"{int(outer_seed)}:{node_id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "big")


def _inject_pipeline_random_seeds(
    dag: dict,
    outer_seed: int | None,
) -> tuple[dict, dict[str, int]]:
    """Copy a DAG and seed every stochastic node without mutating caller input."""
    seeded_dag = deepcopy(dag)
    node_seeds: dict[str, int] = {}
    if outer_seed is None:
        return seeded_dag, node_seeds

    for node in seeded_dag.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") not in _STOCHASTIC_PIPELINE_NODE_TYPES:
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            data = {}
            node["data"] = data
        params = data.get("params")
        if not isinstance(params, dict):
            params = {}
        else:
            params = dict(params)
        raw_node_seed = params.get("random_seed")
        node_seed = (
            int(raw_node_seed)
            if raw_node_seed is not None
            else _derive_pipeline_node_seed(outer_seed, node_id)
        )
        params["random_seed"] = node_seed
        data["params"] = params
        node_seeds[node_id] = node_seed
    return seeded_dag, node_seeds


def _exec_get_cached_results(inp: dict, python_session_id: str = "default") -> dict:
    """Return full cached search results."""
    max_n = inp.get("max_results", 50)
    # Prefer the session-scoped cache so concurrent sessions don't read each
    # other's "latest" results; fall back to the global key for the default
    # (unscoped) session, mirroring code_executor.get_search_results_for_session.
    scoped_key = _session_cache_key("latest", python_session_id)
    results = (get_cached_results(scoped_key) if scoped_key else None)
    if results is None:
        results = get_cached_results("latest")
    if results is None:
        return {"results": [], "message": "No recent search results cached. Run a search first."}
    return {"results": results[:max_n], "total": len(results)}


def _resolve_session_id(tool_input: dict, python_session_id: str) -> str | None:
    session_id = str(tool_input.get("session_id") or "").strip()
    if session_id:
        return session_id
    fallback = str(python_session_id or "").strip()
    return fallback or None


async def _require_tool_session_owner(session_id: str, user_id: str | None, db: Any) -> dict[str, Any] | None:
    """Return a structured error unless the current user owns ``session_id``."""
    if not user_id:
        return {"error": "Sign in before validating or generating a paper from a saved chat session."}
    try:
        import uuid as _uuid
        from sqlalchemy import select as _select
        from app.models.schemas import ChatSession as _ChatSession

        sid = _uuid.UUID(str(session_id))
        uid = _uuid.UUID(str(user_id))
        session = (
            await db.execute(
                _select(_ChatSession.id).where(
                    _ChatSession.id == sid,
                    _ChatSession.user_id == uid,
                )
            )
        ).scalar_one_or_none()
    except ValueError:
        return {"error": "Invalid session ID. Save the current chat session first."}
    if session is None:
        return {"error": "Session not found for the current account."}
    return None


async def _exec_validate_analysis(
    inp: dict,
    python_session_id: str = "default",
    user_id: str | None = None,
) -> dict:
    from app.models.database import async_session
    from app.services.analysis_validator import validate_analysis

    session_id = _resolve_session_id(inp, python_session_id)
    if not session_id:
        return {"error": "session_id is required. Save the current chat session first."}

    async with async_session() as db:
        owner_error = await _require_tool_session_owner(session_id, user_id, db)
        if owner_error:
            return owner_error
        try:
            validation = await validate_analysis(
                session_id,
                db,
                owner_id=str(user_id),
            )
        except Exception as exc:
            return {"error": str(exc)}
    return validation


async def _exec_generate_paper_draft(
    inp: dict,
    python_session_id: str = "default",
    user_id: str | None = None,
) -> dict:
    from app.models.database import async_session
    from app.services.paper_generator import generate_paper_draft

    session_id = _resolve_session_id(inp, python_session_id)
    if not session_id:
        return {"error": "session_id is required. Save the current chat session first."}

    journal_format = str(inp.get("journal_format", "aastex") or "aastex").strip().lower()
    async with async_session() as db:
        owner_error = await _require_tool_session_owner(session_id, user_id, db)
        if owner_error:
            return owner_error
        try:
            generated = await generate_paper_draft(session_id, journal_format, db)
        except Exception as exc:
            # E0.3: classify common upstream failures (ADS DNS, timeout,
            # LLM backend outage) into a typed, user-friendly error so the
            # chat shows "AI backend temporarily unreachable" instead of
            # a raw "Errno -2 Name or service not known" traceback.
            import socket as _socket
            msg = str(exc)
            if isinstance(exc, _socket.gaierror) or "Name or service not known" in msg or "Temporary failure in name resolution" in msg:
                err_text = (
                    "External service (ADS / LLM backend) is currently unreachable "
                    "from this deploy. Your session and data are fine; retry once "
                    "network connectivity recovers, or export the raw chat/pipeline "
                    "via the Export menu."
                )
                return {
                    "error": err_text,
                    "error_class": "network_unreachable",
                    "detail": msg[:500],
                    "success": False,
                }
            if "timeout" in msg.lower() or "timed out" in msg.lower():
                return {
                    "error": "Paper draft generation timed out while fetching external metadata.",
                    "error_class": "timeout",
                    "detail": msg[:500],
                    "success": False,
                }
            return {
                "error": f"Paper draft generation failed: {msg[:300]}",
                "error_class": "unknown",
                "success": False,
            }

    paper_json = generated["paper_json"]
    return {
        "title": paper_json.get("title", ""),
        "abstract": paper_json.get("abstract", ""),
        "journal_format": paper_json.get("journal_format", journal_format),
        "sections": {
            "introduction": paper_json.get("introduction", {}).get("text", ""),
            "data_and_methods": paper_json.get("data_and_methods", {}).get("analysis_methods", ""),
            "results": paper_json.get("results", {}).get("text", ""),
            "discussion": paper_json.get("discussion", {}).get("text", ""),
            "conclusions": paper_json.get("conclusions", ""),
        },
        "figures": paper_json.get("results", {}).get("figures", []),
        "tables": paper_json.get("results", {}).get("tables", []),
        "bibtex_preview": generated["bibtex"][:4000],
        "latex_preview": generated["latex_source"][:6000],
    }


async def _exec_run_pipeline(
    inp: dict,
    *,
    owner_scope: str | None = None,
) -> dict:
    """Execute a pipeline DAG synchronously and return results."""
    from app.pipeline.engine import execute_dag, topological_sort
    from app.pipeline.nodes import registry

    dag = inp.get("dag", {})
    input_data_id = inp.get("input_data_id", "")
    raw_outer_seed = inp.get("random_seed")
    outer_seed = int(raw_outer_seed) if raw_outer_seed is not None else None

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

    dag, node_random_seeds = _inject_pipeline_random_seeds(dag, outer_seed)

    import uuid
    run_id = str(uuid.uuid4())
    try:
        # Run in executor to avoid blocking the async event loop
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            execute_dag,
            dag,
            input_data_id,
            run_id,
            owner_scope,
        )
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

    return {
        "run_id": run_id,
        "status": "completed",
        "results": summary,
        "random_seed": outer_seed,
        "node_random_seeds": node_random_seeds,
    }


async def _exec_generate_proposal(inp: dict) -> dict:
    """Gather observation proposal data: coordinates, visibility, ETC, literature."""
    target_name = inp.get("target_name", "")
    telescope = inp.get("telescope", "vlt").lower()
    instrument = inp.get("instrument", "")
    science_goal = inp.get("science_goal", "")
    exposure_hours = inp.get("exposure_hours")

    proposal: dict = {
        "analysis_status": "PLANNING_ESTIMATE",
        "publication_ready": False,
        "preliminary_ready": True,
        "claim_scope": "rough_observation_planning_only",
        "target_name": target_name,
        "telescope": telescope,
        "instrument": instrument,
        "science_goal": science_goal,
        "requested_exposure_hours": exposure_hours,
    }

    # 1. Resolve target coordinates
    ra, dec = None, None
    target_mag = None
    try:
        from app.services.name_resolver import resolve_name
        resolved = await resolve_name(target_name)
        if resolved.resolved:
            ra, dec = resolved.ra, resolved.dec
            from astropy.coordinates import SkyCoord
            coord = SkyCoord(ra, dec, unit="deg")
            proposal["coordinates"] = {"ra_deg": round(ra, 6), "dec_deg": round(dec, 6),
                                       "ra_hms": coord.ra.to_string(unit="hour", sep=":"),
                                       "dec_dms": coord.dec.to_string(sep=":")}
        else:
            proposal["coordinates"] = {"error": f"Could not resolve '{target_name}': tried {resolved.aliases_tried}"}
    except Exception as e:
        proposal["coordinates"] = {"error": f"Could not resolve '{target_name}': {e}"}

    # Try to get magnitude and extra info from SIMBAD
    try:
        from app.connectors.simbad import SIMBADConnector
        simbad = SIMBADConnector()
        detail = await simbad.get_object_detail(target_name)
        if detail:
            proposal["object_type"] = detail.get("object_type", "unknown")
            proposal["redshift"] = detail.get("redshift")
            # Try to extract a V magnitude
            extra = detail.get("extra", {})
            if isinstance(extra, dict):
                for key in ("flux_V", "flux_v", "mag_V", "mag_v", "V", "phot_g_mean_mag"):
                    val = extra.get(key)
                    if val is not None:
                        try:
                            target_mag = float(val)
                            break
                        except (ValueError, TypeError):
                            pass
    except Exception:
        pass

    # 2. Compute visibility. Earth-surface altitude/airmass is scientifically
    # inapplicable to space observatories; never substitute a convenient ground
    # site for HST/JWST schedulability.
    is_space_telescope = telescope in {"hst", "jwst"}
    if is_space_telescope:
        proposal["visibility"] = {
            "status": "not_applicable",
            "reason": (
                "Ground altitude, airmass, and night length do not describe "
                f"{telescope.upper()} visibility."
            ),
            "scheduling_reference": "STScI Astronomer's Proposal Tool (APT)",
        }
    elif ra is not None and dec is not None:
        try:
            from app.services.astro_analysis import target_visibility, _TELESCOPE_OBSERVATORY
            obs = _TELESCOPE_OBSERVATORY.get(telescope, "paranal")
            vis = target_visibility(ra, dec, observatory=obs)
            proposal["visibility"] = vis
        except Exception as e:
            proposal["visibility"] = {"error": str(e)}
    else:
        proposal["visibility"] = {"error": "No coordinates available"}

    # 3. Exposure time estimate (if magnitude available)
    if is_space_telescope:
        proposal["exposure_estimate"] = {
            "status": "not_available",
            "reason": (
                "The generic estimator assumes ground sky background, seeing, "
                "and a simplified CCD; use the official STScI instrument ETC."
            ),
        }
    elif target_mag is not None:
        try:
            from app.services.astro_analysis import exposure_time_estimate
            etc_result = exposure_time_estimate(
                target_mag, snr_target=10, telescope=telescope
            )
            proposal["exposure_estimate"] = etc_result
        except Exception as e:
            proposal["exposure_estimate"] = {"error": str(e)}
    else:
        proposal["exposure_estimate"] = {
            "note": "No magnitude found for automatic ETC. "
                    "Use exposure_time_estimate() in run_python with a known magnitude."
        }

    # 4. Search ADS for recent papers on the target
    try:
        lit_result = await _exec_literature({"query": target_name})
        proposal["recent_literature"] = lit_result.get("results", [])[:5]
    except Exception:
        proposal["recent_literature"] = []

    # 5. Summary notes
    notes = []
    vis = proposal.get("visibility", {})
    if (
        isinstance(vis, dict)
        and not vis.get("error")
        and vis.get("status") != "not_applicable"
    ):
        hrs = vis.get("hours_observable", 0)
        if hrs < 2:
            notes.append(f"Warning: target only observable {hrs:.1f} hours (alt > 30 deg).")
        if vis.get("never_rises"):
            notes.append("Target never rises from this observatory — choose a different site.")
    if telescope in ("hst", "jwst"):
        notes.append(
            "Space-telescope schedulability and exposure time require STScI "
            "APT and the official instrument ETC; no ground proxy was computed."
        )
    if exposure_hours and exposure_hours > 10:
        notes.append("Large time request — ensure strong scientific justification.")
    proposal["notes"] = notes

    return proposal


async def _exec_query_transients(inp: dict) -> dict:
    """Search TNS and Lasair for transients."""
    from app.services.transient_service import query_transients

    result = await query_transients(
        name=inp.get("name"),
        ra=inp.get("ra"),
        dec=inp.get("dec"),
        radius_arcsec=inp.get("radius_arcsec", 10),
        days_back=inp.get("days_back", 30),
        obj_type=inp.get("obj_type"),
    )
    return result


async def _exec_read_paper(inp: dict) -> dict:
    """Download an arXiv paper and extract text content."""
    import httpx
    import re

    arxiv_id = inp.get("arxiv_id", "").strip()
    if not arxiv_id:
        return {"error": "arxiv_id is required"}

    # Clean the ID (remove 'arXiv:' prefix if present)
    arxiv_id = re.sub(r'^arXiv:', '', arxiv_id, flags=re.IGNORECASE)

    # Fetch paper metadata from arXiv API.
    # R6-NEW-1: arXiv redirects http → https with a 301; httpx does not
    # follow redirects by default. Two-pronged fix: write the URL with https
    # directly + enable follow_redirects (some mirrors redirect again).
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(f"https://export.arxiv.org/api/query?id_list={arxiv_id}")
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


async def _exec_research_workflow(inp: dict) -> dict:
    """Plan a structured research workflow from hypothesis to conclusion.

    This is a PLANNING tool — it returns a research plan that the AI then
    executes step-by-step using other tools (run_adql, run_python, search_objects).
    """
    hypothesis = inp.get("hypothesis", "")
    scope = inp.get("scope", "thorough")

    if not hypothesis.strip():
        return {"error": "hypothesis is required"}

    # Determine likely data sources based on keywords
    h_lower = hypothesis.lower()
    data_sources = []
    suggested_queries = []

    if any(kw in h_lower for kw in ["star", "stellar", "hr diagram", "parallax", "proper motion", "teff", "luminosity"]):
        data_sources.append("gaia")
        suggested_queries.append("SELECT TOP 1000 ... FROM gaiadr3.gaia_source WHERE ...")
    if any(kw in h_lower for kw in ["galaxy", "galaxies", "redshift", "agn", "quasar", "morpholog"]):
        data_sources.append("simbad")
        suggested_queries.append("SELECT TOP 500 ... FROM basic WHERE otype = 'G' AND rvz_redshift IS NOT NULL")
    if any(kw in h_lower for kw in ["spectrum", "spectral", "emission", "absorption", "line"]):
        data_sources.append("sdss")
    if any(kw in h_lower for kw in ["transient", "supernova", "sn ", "nova", "tde"]):
        data_sources.append("tns")
    if not data_sources:
        data_sources = ["simbad", "gaia"]

    # Build analysis steps based on scope
    analysis_steps = [
        "Acquire sample from database with appropriate selection criteria",
        "Clean data: remove NaN/null values, apply quality cuts",
        "Compute derived quantities (absolute magnitudes, colors, distances, etc.)",
        "Visualize distributions and relationships (histograms, scatter plots)",
    ]
    statistical_tests = ["Pearson/Spearman correlation"]
    suggested_plots = ["histogram", "scatter"]

    if scope == "thorough":
        analysis_steps.extend([
            "Split sample into subgroups if applicable",
            "Perform regression analysis with uncertainties",
            "Check for selection effects and systematic biases",
            "Compare results with published literature",
        ])
        statistical_tests.extend([
            "Linear regression (with bootstrap uncertainties)",
            "KS test (for distribution comparison)",
            "Bayesian analysis if sample size permits",
        ])
        suggested_plots.extend([
            "residual plot",
            "corner/contour plot",
            "comparison with literature values",
            "publication-ready summary figure",
        ])
    else:
        statistical_tests.append("Simple linear fit")
        suggested_plots.append("summary figure")

    plan = {
        "hypothesis_formal": f"Research question: {hypothesis}",
        "null_hypothesis": "H₀: No significant relationship/difference exists as hypothesized.",
        "data_sources": data_sources,
        "suggested_queries": suggested_queries,
        "analysis_steps": analysis_steps,
        "suggested_plots": suggested_plots,
        "statistical_tests": statistical_tests,
        "scope": scope,
        "next_action": "Begin Step 1: refine the hypothesis, then proceed to data acquisition using run_adql or search_objects.",
    }

    # Auto-execute the first suggested query to bootstrap the workflow
    if plan.get("suggested_queries") and len(plan["suggested_queries"]) > 0:
        first_q = plan["suggested_queries"][0]
        try:
            query_str = first_q.get("query", first_q) if isinstance(first_q, dict) else str(first_q)
            step1 = await _exec_search({"query": query_str, "sources": ["simbad"]})
            plan["auto_executed_step1"] = True
            plan["step1_results_summary"] = f"Auto-searched: found {len(step1.get('results', []))} objects"
            plan["step1_results"] = step1
        except Exception as e:
            plan["auto_executed_step1"] = False
            plan["step1_error"] = str(e)

    return plan
