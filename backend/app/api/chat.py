"""AI assistant powered by Claude — helps users interact with the platform."""
import json
import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_optional_user
from app.models.database import get_db
from app.models.schemas import User

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

SYSTEM_PROMPT = """You are an AI research assistant for the Astro Research Platform — a SaaS tool for professional astronomers.

You help users with:
1. **Data Discovery** — Search astronomical databases (SDSS, Gaia, SIMBAD, VizieR, MAST, NED, 2MASS, Chandra, AllWISE)
2. **Data Analysis** — Build and run processing pipelines (denoise, spectral fitting, coordinate transforms, redshift estimation, SED fitting, cross-matching, photometric calibration, image stacking)
3. **ADQL Queries** — Write and execute ADQL queries against Gaia, VizieR, CADC TAP services
4. **Workspace Management** — Organize saved data files, add tags/notes, export results
5. **Data Visualization** — Create interactive plots (HR diagrams, SEDs, spectra, sky maps, scatter plots, corner plots)

When a user asks a question, you should:
- Understand their astronomical research goal
- Determine which platform actions would help
- Return a structured response with both a human-readable explanation AND a list of actions to execute

Available actions (return as JSON in your response within <actions>...</actions> tags):

1. {"action": "search", "query": "natural language description or object name", "sources": ["sdss","gaia","simbad","vizier","mast","ned","2mass","chandra","allwise"], "radius": 0.1}
   - The search backend understands natural language: it parses redshift ranges (z>4), spectral lines (C II, Lyman-alpha), object types (galaxy, quasar), etc.
   - For science-criteria searches (redshift, object type), SIMBAD is automatically used via TAP/ADQL
   - For coordinate/name searches, any source works
2. {"action": "adql", "query": "SELECT ...", "service": "gaia|vizier|cadc"}
   - Gaia: query gaiadr3.gaia_source (columns: source_id, ra, dec, parallax, phot_g_mean_mag, bp_rp, radial_velocity, etc.)
   - VizieR: use real catalog names from CDS, e.g. "II/246/out" (2MASS), "I/355/gaiadr3" (Gaia in VizieR). Do NOT invent catalog paths — if unsure of the exact VizieR table name, use a search action instead
   - CADC: query CAOM2 tables (caom2.Observation, caom2.Plane)
   - SIMBAD TAP: query the "basic" table (columns: main_id, ra, dec, otype, rvz_redshift)
3. {"action": "arxiv", "arxiv_id": "2301.12345"} — extract data tables from an arXiv paper. Accepts arXiv ID or full URL.
4. {"action": "run_pipeline", "nodes": [{"type": "LoadData", ...}, {"type": "Denoise", ...}], "input_data_id": "..."}
5. {"action": "explain", "topic": "..."} — just provide explanation, no platform action needed
6. {"action": "plot", "chart_type": "hr_diagram|sed_fit|spectrum_overlay|redshift_histogram|sky_coverage|correlation_scatter|corner_plot", "data": {"x": [...], "y": [...], ...}, "params": {"title": "...", "x_label": "...", "y_label": "...", ...}}

When creating plots, always include appropriate axis labels and titles. For astronomical data:
- Use "RA (deg)" and "Dec (deg)" for coordinate axes
- Use wavelength units (Angstrom or nm) for spectra
- Use magnitude system labels (e.g., "G mag (Gaia)")
- Include color bars with physical units when using color dimensions

Respond conversationally but always be scientifically accurate. If the user's request is ambiguous, ask clarifying questions. When you suggest actions, explain WHY each step is needed for their research goal.

Always respond in the same language the user uses."""


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    context: dict | None = None  # optional context like current workspace files


class ChatAction(BaseModel):
    action: str
    params: dict


class ChatResponse(BaseModel):
    reply: str
    actions: list[dict] = []


def _parse_actions(text: str) -> list[dict]:
    """Extract action JSON from <actions>...</actions> tags."""
    actions = []
    import re
    matches = re.findall(r'<actions>(.*?)</actions>', text, re.DOTALL)
    for match in matches:
        try:
            parsed = json.loads(match.strip())
            if isinstance(parsed, list):
                actions.extend(parsed)
            else:
                actions.append(parsed)
        except json.JSONDecodeError:
            # Try line-by-line
            for line in match.strip().split('\n'):
                line = line.strip()
                if line.startswith('{'):
                    try:
                        actions.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return actions


def _strip_actions_from_reply(text: str) -> str:
    """Remove <actions> blocks from the user-facing reply."""
    import re
    return re.sub(r'<actions>.*?</actions>', '', text, flags=re.DOTALL).strip()


@router.post("/message", response_model=ChatResponse)
async def chat_message(
    req: ChatRequest,
    user: User | None = Depends(get_optional_user),
):
    """Send a message to the AI assistant."""
    # Priority: context api_key (from frontend localStorage) > user DB key > server env key
    context_key = (req.context or {}).get("api_key") if req.context else None
    user_keys = (user.api_keys or {}) if user else {}
    api_key = context_key or user_keys.get("anthropic") or (user.anthropic_api_key if user and user.anthropic_api_key else None) or ANTHROPIC_API_KEY
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="AI assistant not configured — set your API key in Settings or ask the admin to configure ANTHROPIC_API_KEY",
        )

    try:
        import anthropic
    except ImportError:
        raise HTTPException(status_code=503, detail="anthropic package not installed")

    client = anthropic.Anthropic(api_key=api_key)

    # Build messages for Claude
    claude_messages = []
    for msg in req.messages:
        claude_messages.append({
            "role": msg.role,
            "content": msg.content,
        })

    # Add context if available (strip sensitive fields)
    system = SYSTEM_PROMPT
    if req.context:
        safe_context = {k: v for k, v in req.context.items() if k != "api_key"}
        if safe_context:
            system += f"\n\nCurrent user context:\n{json.dumps(safe_context, indent=2)}"
    if user:
        system += f"\nUser email: {user.email}, Subscription: {user.subscription_tier}"

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=system,
            messages=claude_messages,
        )

        full_reply = response.content[0].text
        actions = _parse_actions(full_reply)
        clean_reply = _strip_actions_from_reply(full_reply)

        return ChatResponse(reply=clean_reply, actions=actions)

    except anthropic.AuthenticationError:
        raise HTTPException(status_code=401, detail="Invalid API key. Please check your Anthropic API key in Settings.")
    except anthropic.RateLimitError:
        raise HTTPException(status_code=429, detail="API rate limit exceeded. Please wait a moment and try again.")
    except anthropic.APIError as e:
        logger.error("Claude API error: %s", e)
        raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")


@router.post("/execute-action")
async def execute_action(
    action: dict,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Execute an action suggested by the AI assistant."""
    import asyncio

    action_type = action.get("action")

    if action_type == "search":
        from app.connectors.registry import CONNECTORS_KEYS, get_connector
        from app.api.data import SearchResult, _astro_to_result
        from app.search.query_parser import parse_natural_query

        query = action.get("query", "")
        source_list = action.get("sources", ["sdss", "gaia", "simbad"])
        radius = action.get("radius", 0.1)

        # Filter out unknown sources
        source_list = [s for s in source_list if s in CONNECTORS_KEYS]
        if not source_list:
            source_list = ["simbad"]

        # Parse the natural language query to extract science criteria
        parsed = parse_natural_query(query)
        redshift_min = parsed.get("redshift_min")
        redshift_max = parsed.get("redshift_max")
        object_type = parsed.get("object_type")
        has_science_criteria = any([redshift_min, redshift_max, object_type])

        # Try to resolve coordinates from an object name in the query
        import re
        search_ra = None
        search_dec = None
        obj_match = re.search(
            r'\b(M\s*\d+|NGC\s*\d+|IC\s*\d+|Mrk\s*\d+|3C\s*\d+|'
            r'UGC\s*\d+|SDSS\s*J[\d.+-]+)\b',
            query, re.IGNORECASE,
        )
        resolved_name = obj_match.group(0) if obj_match else None
        if resolved_name:
            try:
                from astropy.coordinates import SkyCoord
                coord = SkyCoord.from_name(resolved_name)
                search_ra, search_dec = coord.ra.deg, coord.dec.deg
            except Exception:
                pass

        async def _search_one(source: str):
            connector = get_connector(source)
            # Use SIMBAD's criteria-based TAP search for science queries
            if source == "simbad" and has_science_criteria and hasattr(connector, "search_by_criteria"):
                return await asyncio.wait_for(
                    connector.search_by_criteria(
                        object_type=object_type,
                        redshift_min=redshift_min,
                        redshift_max=redshift_max,
                        ra=search_ra,
                        dec=search_dec,
                        radius=radius,
                    ),
                    timeout=45.0,
                )
            # For coordinate-based connectors, skip if we have no coordinates
            # and the query is a science description (not a resolvable name)
            if search_ra is None and not resolved_name:
                return []
            search_q = resolved_name or query
            return await asyncio.wait_for(
                connector.search(search_q, ra=search_ra, dec=search_dec, radius=radius),
                timeout=45.0,
            )

        tasks = [_search_one(s) for s in source_list]
        results_per_source = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: list[SearchResult] = []
        for source_name, result in zip(source_list, results_per_source):
            if isinstance(result, Exception):
                logger.warning("Chat search failed for %s: %s", source_name, result)
                all_results.append(SearchResult(
                    source=source_name, object_id="error",
                    name=f"Error querying {source_name}: {result}",
                    ra=0, dec=0, error_type="connection",
                ))
                continue
            all_results.extend(_astro_to_result(obj) for obj in result)

        # If no science-based connectors were in the list, add SIMBAD automatically
        if has_science_criteria and "simbad" not in source_list:
            try:
                simbad = get_connector("simbad")
                extra = await asyncio.wait_for(
                    simbad.search_by_criteria(
                        object_type=object_type,
                        redshift_min=redshift_min,
                        redshift_max=redshift_max,
                        ra=search_ra,
                        dec=search_dec,
                        radius=radius,
                    ),
                    timeout=45.0,
                )
                all_results.extend(_astro_to_result(obj) for obj in extra)
            except Exception as e:
                logger.warning("Chat fallback SIMBAD search failed: %s", e)

        return {"type": "search_results", "data": [r.model_dump() for r in all_results]}

    elif action_type == "adql":
        # Call the integration endpoint directly (no rate limiter on this one)
        from app.api.integration import adql_query, ADQLRequest
        req = ADQLRequest(query=action.get("query", ""), service=action.get("service", "gaia"))
        result = await adql_query(req)
        return {"type": "adql_results", "data": result}

    elif action_type == "plot":
        from app.pipeline.nodes.plot_interactive import build_chart
        chart_type = action.get("chart_type", "correlation_scatter")
        data = action.get("data", {})
        params = action.get("params", {})
        plot_json = build_chart(chart_type, data, params)
        return {"type": "plot", "data": plot_json}

    elif action_type == "arxiv":
        from app.api.arxiv import extract_arxiv_tables, ArxivTableRequest
        arxiv_id = action.get("arxiv_id", "")
        result = await extract_arxiv_tables(ArxivTableRequest(arxiv_id=arxiv_id))
        return {"type": "arxiv_tables", "data": result.model_dump()}

    elif action_type == "run_pipeline":
        from app.api.pipeline import run_pipeline, RunRequest
        from starlette.requests import Request as StarletteRequest
        nodes = action.get("nodes", [])
        input_data_id = action.get("input_data_id", "")
        dag = {"nodes": nodes, "edges": action.get("edges", [])}
        req = RunRequest(dag=dag, input_data_id=input_data_id)
        scope = {"type": "http", "method": "POST", "path": "/api/chat/execute-action",
                 "headers": [], "query_string": b"async_mode=false"}
        mock_request = StarletteRequest(scope)
        result = await run_pipeline(request=mock_request, req=req, db=db, user=user, async_mode=False)
        return {"type": "pipeline_result", "data": result.model_dump()}

    elif action_type == "explain":
        return {"type": "explanation", "data": {"topic": action.get("topic", "")}}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action type: {action_type}")
