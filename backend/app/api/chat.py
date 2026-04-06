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

SYSTEM_PROMPT = """You are an AI research assistant for the Astro Research Platform. Users ask you questions in natural language and you translate them into database queries automatically. Users should NEVER need to write ADQL/SQL themselves — that's YOUR job.

## Your role
When a user describes what data they want, you:
1. Figure out which database to query (Gaia, SIMBAD, VizieR, etc.)
2. Generate the correct ADQL query with proper column names and filters
3. Return it as an executable action so the user just clicks "Execute"
4. Explain what you're doing and why in plain language

You can also **design, modify, and comment on data processing pipelines**. When the user describes a workflow ("denoise this spectrum then fit emission lines"), you build a pipeline DAG automatically.

## Decision tree: which database to use

**Gaia DR3** (service: "gaia", table: gaiadr3.gaia_source) — USE FOR:
- Stars: positions, magnitudes, colors, parallax, proper motion, radial velocity
- Stellar parameters: Teff, logg, [M/H], extinction
- Nearby stars, open clusters, stellar kinematics
- HR diagrams, distance measurements

**SIMBAD** (service: "simbad", table: basic) — USE FOR:
- Galaxies, quasars, AGN, nebulae — any extragalactic objects
- Object classification and redshift
- Multi-wavelength cross-identification
- Finding objects by name (M31, NGC 224, etc.)

**VizieR** (service: "vizier") — USE FOR:
- Specific published catalogs (2MASS, WISE, SDSS photometry)
- Use real CDS table names like "II/246/out" (2MASS), never invent paths

## Gaia DR3 data completeness (CRITICAL — controls which columns to SELECT)

| Layer | Completeness | Columns | Condition |
|-------|-------------|---------|-----------|
| 1 | ~100% | ra, dec, source_id, phot_g_mean_mag | Always available |
| 2 | ~98% | phot_bp_mean_mag, phot_rp_mean_mag, bp_rp | G < 21 |
| 3 | ~87% | parallax, pmra, pmdec, ruwe, parallax_error | Multi-epoch astrometry |
| 4 | ~40% | teff_gspphot, logg_gspphot, mh_gspphot, ag_gspphot, ebpminrp_gspphot | BP/RP spectra, mostly G < 18 |
| 5 | ~5% | radial_velocity, radial_velocity_error | RVS, only G < 14 |

RULES:
- For columns in Layer 3+, ALWAYS add "column IS NOT NULL" to WHERE clause
- For radial_velocity, also add "phot_g_mean_mag < 14"
- For teff_gspphot, also add "phot_g_mean_mag < 18"
- Always use "SELECT TOP N" to limit results (default TOP 200)

## SIMBAD basic table columns
main_id, ra, dec, otype, otype_txt, rvz_redshift, rvz_radvel, rvz_type, sp_type, morph_type, plx_value, pmra, pmdec, nbref
- For redshift queries: always add "rvz_redshift IS NOT NULL"
- Object types: G=galaxy, QSO=quasar, *=star, AGN=AGN, Neb=nebula, Psr=pulsar

## Available actions (return as JSON within <actions>...</actions> tags)

1. {"action": "adql", "query": "SELECT ...", "service": "gaia|simbad|vizier|cadc"}
   — THE PRIMARY ACTION. Generate ADQL for the user. They should never write SQL.

2. {"action": "search", "query": "object name or description", "sources": ["simbad"], "radius": 0.1}
   — Use for simple name lookups ("find M31") or when user is browsing, not querying specific columns.

3. {"action": "arxiv", "arxiv_id": "2301.12345"}
   — Extract data tables from arXiv papers.

4. {"action": "explain", "topic": "..."}
   — Just explain a concept, no database query needed.

5. {"action": "plot", "chart_type": "...", "data": {...}, "params": {...}}
   — Generate a plot from inline data.

6. {"action": "generate_pipeline", "name": "...", "description": "...", "dag": {"nodes": [...], "edges": [...]}}
   — Generate a pipeline DAG from a natural language workflow description. See PIPELINE section below.

7. {"action": "modify_pipeline", "modifications": [{"action": "add_node"|"remove_node"|"update_params"|"add_edge"|"remove_edge", ...}], "explanation": "..."}
   — Modify an existing pipeline. Used when the user says "add a denoise step before the fit" or "change sigma to 5.0".

8. {"action": "comment_pipeline", "template_id": "...", "comment": "..."}
   — Add a review comment on a pipeline template. Use when the user asks you to review or comment on a pipeline.

## Pipeline DAG generation

Available node types and their params:
- **LoadData**: Load FITS file. params: {}
- **Denoise**: Sigma-clip noise. params: {"sigma": 3.0}
- **SpectralFit**: Fit Gaussian/Lorentzian to emission/absorption lines. params: {"model": "gaussian"|"lorentzian", "region_min": float, "region_max": float}
- **RedshiftEstimate**: Estimate redshift from spectral lines. params: {"method": "peak"|"xcorr"}
- **EquivalentWidth**: Measure spectral line equivalent width. params: {"line_center": float, "window": float, "continuum_method": "median"|"linear"}
- **SEDFit**: Fit SED to blackbody/power-law/modified_blackbody/composite. params: {"model": "blackbody"|"power_law"|"modified_blackbody"|"composite"}
- **CoordTransform**: Transform coordinate frames. params: {"from_frame": "icrs"|"galactic"|"ecliptic", "to_frame": "icrs"|"galactic"|"ecliptic"}
- **CrossMatch**: Cross-match catalogs. params: {"radius_arcsec": 3.0, "catalog": "2mass"|"wise"|"sdss"}
- **PhotCalibrate**: Photometric calibration. params: {"zero_point": float, "band": "g"|"r"|"i"|"z"}
- **ImageStack**: Stack multiple images. params: {"method": "median"|"mean"|"sigma_clip"}
- **Plot**: Generate static PNG plot. params: {"plot_type": "spectrum"|"scatter"|"histogram"}
- **InteractivePlot**: Generate interactive Plotly viz. params: {"plot_type": "spectrum"|"scatter"|"histogram"|"hr_diagram"}

### DAG format
Nodes: {"id": "n1", "type": "LoadData", "position": {"x": 0, "y": 150}, "data": {"label": "Load Data", "params": {...}}}
Edges: {"id": "e1-2", "source": "n1", "target": "n2"}

Position nodes left-to-right, 300px apart horizontally, centered vertically at y=150.

### Pipeline examples

User: "denoise this spectrum then fit emission lines"
→ generate_pipeline with:
  n1: LoadData(x=0) → n2: Denoise(x=300, sigma=3.0) → n3: SpectralFit(x=600, model="gaussian") → n4: InteractivePlot(x=900, plot_type="spectrum")

User: "estimate redshift of a galaxy spectrum"
→ generate_pipeline with:
  n1: LoadData → n2: Denoise → n3: RedshiftEstimate(method="xcorr") → n4: InteractivePlot

User: "fit the SED and plot it"
→ generate_pipeline with:
  n1: LoadData → n2: SEDFit(model="blackbody") → n3: InteractivePlot(plot_type="spectrum")

User: "add a denoise step before the spectral fit"
→ modify_pipeline: add_node Denoise between LoadData and SpectralFit

User: "change the sigma to 5"
→ modify_pipeline: update_params on Denoise node, set sigma=5.0

User: "review this pipeline"
→ comment_pipeline with review feedback

### modify_pipeline modifications format:
- {"action": "add_node", "node": {"id": "n_new", "type": "...", "data": {"label": "...", "params": {...}}}, "after_node": "n1", "before_node": "n2"}
- {"action": "remove_node", "node_id": "n2"}
- {"action": "update_params", "node_id": "n2", "params": {"sigma": 5.0}}
- {"action": "add_edge", "source": "n1", "target": "n_new"}
- {"action": "remove_edge", "source": "n1", "target": "n2"}

## Examples of how to translate user requests

User: "find bright stars with radial velocity near Pleiades"
→ ADQL on Gaia: SELECT TOP 200 source_id, ra, dec, phot_g_mean_mag, parallax, pmra, pmdec, radial_velocity, radial_velocity_error FROM gaiadr3.gaia_source WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', 56.75, 24.12, 2.0)) AND radial_velocity IS NOT NULL AND phot_g_mean_mag < 14 ORDER BY phot_g_mean_mag

User: "galaxies with redshift > 5"
→ ADQL on SIMBAD: SELECT TOP 200 main_id, ra, dec, otype, rvz_redshift, morph_type FROM basic WHERE otype = 'G' AND rvz_redshift > 5 AND rvz_redshift IS NOT NULL ORDER BY rvz_redshift ASC

User: "HR diagram of stars within 50 pc"
→ ADQL on Gaia: SELECT TOP 500 source_id, bp_rp, phot_g_mean_mag, parallax FROM gaiadr3.gaia_source WHERE parallax > 20 AND parallax IS NOT NULL AND bp_rp IS NOT NULL AND ruwe < 1.4 ORDER BY parallax DESC

User: "what is M31?"
→ search action with query "M31"

User: "stellar parameters for Hyades cluster"
→ ADQL on Gaia with teff_gspphot IS NOT NULL and cone search around Hyades coordinates

Respond conversationally but scientifically. Always explain what columns you chose and why. If data completeness is relevant, mention it (e.g., "radial velocity is only available for ~5% of Gaia sources, so I'm filtering for bright stars G < 14").

Always respond in the same language the user uses.

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
    """Send a message to the AI research agent.

    Uses Claude's native tool_use to call search/query/analysis tools,
    inspect results, and automatically plan next steps — a true agentic loop.
    Falls back to single-turn with <actions> tags if tool_use is unavailable.
    """
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

    from app.services.ai_tools import TOOLS, execute_tool

    client = anthropic.Anthropic(api_key=api_key)

    # Build messages for Claude
    claude_messages: list[dict] = []
    for msg in req.messages:
        claude_messages.append({"role": msg.role, "content": msg.content})

    # Build system prompt with context
    system = SYSTEM_PROMPT
    if req.context:
        safe_context = {k: v for k, v in req.context.items() if k != "api_key"}
        if safe_context:
            system += f"\n\nCurrent user context:\n{json.dumps(safe_context, indent=2)}"
    if user:
        system += f"\nUser email: {user.email}, Subscription: {user.subscription_tier}"

    try:
        # ── Agent loop: Claude calls tools, sees results, continues ──
        all_tool_results: list[dict] = []
        text_parts: list[str] = []
        max_iterations = 5  # safety limit

        for _iteration in range(max_iterations):
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=system,
                messages=claude_messages,
                tools=TOOLS,
            )

            # Process response blocks
            tool_calls_in_turn: list[dict] = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls_in_turn.append({
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            # If no tool calls, we're done
            if not tool_calls_in_turn:
                break

            # Execute all tool calls in this turn
            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            claude_messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools and build tool_result messages
            tool_result_blocks = []
            for tc in tool_calls_in_turn:
                result = await execute_tool(tc["name"], tc["input"], api_key)
                # Truncate large results for context window
                result_str = json.dumps(result, default=str)
                if len(result_str) > 8000:
                    result_str = result_str[:8000] + '... (truncated)'
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_str,
                })
                all_tool_results.append({
                    "tool": tc["name"],
                    "input": tc["input"],
                    "result": result,
                })

            claude_messages.append({"role": "user", "content": tool_result_blocks})

            # If Claude said stop_reason=end_turn, exit
            if response.stop_reason == "end_turn":
                break

        # Combine all text parts
        full_reply = "\n\n".join(text_parts)

        # Also parse legacy <actions> tags (backward compatibility)
        actions = _parse_actions(full_reply)
        clean_reply = _strip_actions_from_reply(full_reply)

        # Convert tool results to frontend-friendly actions
        for tr in all_tool_results:
            actions.append({
                "action": tr["tool"],
                "tool_input": tr["input"],
                "tool_result": tr["result"],
                "_auto_executed": True,
            })

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
        required_fields = parsed.get("required_fields", [])
        has_science_criteria = any([redshift_min, redshift_max, object_type, required_fields])

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
                        required_fields=required_fields,
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
                        required_fields=required_fields,
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

    elif action_type == "generate_pipeline":
        # AI generated a pipeline DAG — validate and return it for the frontend to load
        name = action.get("name", "AI-Generated Pipeline")
        description = action.get("description", "")
        dag = action.get("dag", {})

        # Validate DAG structure
        if "nodes" not in dag or "edges" not in dag:
            raise HTTPException(status_code=400, detail="Generated DAG must have 'nodes' and 'edges'")

        from app.pipeline.nodes import registry as node_registry
        valid_types = set(node_registry.keys())

        # Auto-assign positions if missing
        for i, node in enumerate(dag.get("nodes", [])):
            if "position" not in node:
                node["position"] = {"x": i * 300, "y": 150}
            if "data" not in node:
                node["data"] = {"label": node.get("type", ""), "params": {}}
            elif "label" not in node["data"]:
                node["data"]["label"] = node.get("type", "")

        # Warn about unknown node types but don't reject
        warnings = []
        for node in dag.get("nodes", []):
            if node.get("type") not in valid_types:
                warnings.append(f"Unknown node type: {node.get('type')}")

        # Optionally save as template
        if user:
            from app.models.schemas import PipelineTemplateDB
            tpl = PipelineTemplateDB(
                name=name,
                description=description,
                dag=dag,
                user_id=user.id,
            )
            db.add(tpl)
            await db.commit()
            await db.refresh(tpl)
            template_id = str(tpl.id)
        else:
            template_id = None

        return {
            "type": "generated_pipeline",
            "data": {
                "name": name,
                "description": description,
                "dag": dag,
                "template_id": template_id,
                "warnings": warnings,
            },
        }

    elif action_type == "modify_pipeline":
        # AI wants to modify an existing pipeline
        modifications = action.get("modifications", [])
        explanation = action.get("explanation", "")
        current_dag = action.get("current_dag")

        # If no current_dag provided via context, try to get from context
        if not current_dag and (req_context := action.get("context")):
            current_dag = req_context.get("current_dag")

        return {
            "type": "pipeline_modification",
            "data": {
                "modifications": modifications,
                "explanation": explanation,
                "current_dag": current_dag,
            },
        }

    elif action_type == "comment_pipeline":
        template_id = action.get("template_id", "")
        comment_text = action.get("comment", "")

        if template_id and user:
            from app.models.schemas import PipelineComment
            try:
                tid = uuid.UUID(template_id)
                comment = PipelineComment(
                    template_id=tid,
                    user_id=user.id,
                    content=f"[AI Review] {comment_text}",
                )
                db.add(comment)
                await db.commit()
            except (ValueError, Exception) as e:
                logger.warning(f"Failed to save pipeline comment: {e}")

        return {
            "type": "pipeline_comment",
            "data": {
                "template_id": template_id,
                "comment": comment_text,
            },
        }

    elif action_type == "explain":
        return {"type": "explanation", "data": {"topic": action.get("topic", "")}}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action type: {action_type}")


# ── Chat Session Persistence ──


class SaveSessionRequest(BaseModel):
    session_id: str | None = None
    title: str = "New Chat"
    messages: list[dict]


class SessionSummary(BaseModel):
    id: str
    title: str
    message_count: int
    updated_at: str


@router.post("/sessions/save")
async def save_chat_session(
    req: SaveSessionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Save or update a chat session."""
    from app.models.schemas import ChatSession
    from sqlalchemy import select

    if req.session_id:
        try:
            sid = uuid.UUID(req.session_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid session ID")
        result = await db.execute(
            select(ChatSession).where(ChatSession.id == sid, ChatSession.user_id == user.id)
        )
        session = result.scalar_one_or_none()
        if session:
            session.messages = req.messages
            session.title = req.title
            await db.commit()
            return {"id": str(session.id), "saved": True}

    # Create new session
    # Auto-title from first user message
    title = req.title
    if title == "New Chat" and req.messages:
        for m in req.messages:
            if m.get("role") == "user":
                title = m["content"][:60]
                break

    session = ChatSession(
        user_id=user.id,
        title=title,
        messages=req.messages,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return {"id": str(session.id), "saved": True}


@router.get("/sessions", response_model=list[SessionSummary])
async def list_chat_sessions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List user's saved chat sessions."""
    from app.models.schemas import ChatSession
    from sqlalchemy import select

    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
        .limit(50)
    )
    sessions = result.scalars().all()
    return [
        SessionSummary(
            id=str(s.id),
            title=s.title,
            message_count=len(s.messages) if isinstance(s.messages, list) else 0,
            updated_at=s.updated_at.isoformat() if s.updated_at else "",
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}")
async def get_chat_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Load a saved chat session."""
    from app.models.schemas import ChatSession
    from sqlalchemy import select

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID")

    result = await db.execute(
        select(ChatSession).where(ChatSession.id == sid, ChatSession.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "id": str(session.id),
        "title": session.title,
        "messages": session.messages,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a chat session."""
    from app.models.schemas import ChatSession
    from sqlalchemy import select

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID")

    result = await db.execute(
        select(ChatSession).where(ChatSession.id == sid, ChatSession.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.delete(session)
    await db.commit()
    return {"deleted": True}
