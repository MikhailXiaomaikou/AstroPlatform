"""AI assistant backed by the inference router and multi-agent orchestrator."""

import asyncio
import inspect
import json
import logging
import os
import uuid
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.inference_router import InferenceError, inference_router
from app.ai.orchestrator import orchestrator
from app.auth import get_current_user, get_optional_user
from app.rate_limit import limiter
from app.models.database import get_db
from app.models.schemas import User

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

SYSTEM_PROMPT = """You are an AI research assistant for Standard Astro. Users ask you questions in natural language and you translate them into database queries automatically. Users should NEVER need to write ADQL/SQL themselves — that's YOUR job.

## Your role
When a user describes what data they want, you:
1. Figure out which database to query (Gaia, SIMBAD, VizieR, etc.)
2. Generate the correct ADQL query with proper column names and filters
3. Return it as an executable action so the user just clicks "Execute"
4. Explain what you're doing and why in plain language

You can also **design, modify, and comment on data processing pipelines**. When the user describes a workflow ("denoise this spectrum then fit emission lines"), you build a pipeline DAG automatically.

You can **search for astronomical transients and alerts** using the query_transients tool. This searches TNS (Transient Name Server) and Lasair/ZTF for recent supernovae, novae, tidal disruption events, kilonovae, and other transients. Search by name (e.g. "SN 2024abc"), coordinates, or type. Use this when users ask about recent transients, supernovae discoveries, or time-domain events.

For CCD image reduction, guide the user through the standard pipeline: bias → dark → flat → cosmic ray → astrometry → source extraction. Ask what calibration frames are available, then use the CCD reduction tools directly.

Use **get_object_dossier** to fetch comprehensive cross-matched data from all available databases simultaneously for any object.
Use **get_followup_recommendation** to generate follow-up observation recommendations for transient alerts.
Use **analyze_cross_wavelength** to check for multi-wavelength discrepancies that might indicate unusual physics.

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
- **BiasSubtract**: Subtract master bias from CCD image. params: {"science_fits_path": "optional/path.fits", "bias_paths": ["workspace/bias_1.fits", "workspace/bias_2.fits"]}
- **DarkCorrect**: Subtract scaled master dark. params: {"science_fits_path": "optional/path.fits", "dark_paths": ["workspace/dark_1.fits"], "science_exptime": float, "dark_exptime": float}
- **FlatField**: Divide by normalized master flat. params: {"science_fits_path": "optional/path.fits", "flat_paths": ["workspace/flat_1.fits"]}
- **CosmicRayReject**: Clean cosmic rays. params: {"sigclip": 5.0, "sigfrac": 0.3, "objlim": 5.0}
- **AstrometricSolve**: Solve WCS for an image. params: {"fits_path": "processed/file.fits"}
- **SourceExtract**: Extract sources and aperture photometry. params: {"fits_path": "processed/file.fits", "aperture_radii": [3,5,7]}
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
- **CustomScript**: Run custom Python code. params: {"code": "python code here", "output_key": "result"}. Code has access to input_data, numpy, scipy, astropy, and the `astro` helper toolkit.

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

When the user describes a workflow or analysis task, ALWAYS generate a pipeline using the
generate_pipeline tool. Don't just describe the steps — create the actual pipeline nodes.

User: "build a [C II] spectral line analysis pipeline"
→ generate_pipeline with:
  n1: LoadData → n2: Denoise(sigma=2.0) → n3: SpectralFit(model="gaussian", region_min=157.0, region_max=158.5)
  → n4: EquivalentWidth(line_center=157.74) → n5: InteractivePlot

User: "add a custom analysis step that computes line-to-continuum ratio"
→ generate_pipeline including CustomScript node with appropriate Python code

User: "add a denoise step before the spectral fit"
→ modify_pipeline: add_node Denoise between LoadData and SpectralFit

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

## Python Code Execution

You have a `run_python` tool that executes Python code in a sandboxed environment.
The ONLY importable modules are: numpy, scipy, astropy, matplotlib, pandas, math, statistics, collections, itertools, functools, json, csv, re, datetime, io, inspect.
The Standard Astro helper toolkit is available as the preloaded `astro` variable and is also importable as `astro`.
Do NOT import os, sys, subprocess, requests, urllib, or any networking/filesystem modules — they are blocked.
Use it for ANY task that requires computation: statistical analysis, curve fitting, plotting, data manipulation.

**Variables persist between code blocks** inside the same active chat runtime (like Jupyter cells).
You can define variables in one run_python call and use them in the next. No need to put everything
in one giant code block, but do not assume variables survive after opening a brand-new chat or page refresh.
Complex objects such as astropy cosmology instances, scipy functions, and custom classes also persist.
Do not probe for availability with `eval`, `sys`, or fragile introspection hacks. These helpers are guaranteed.

Key patterns:
- `results = get_search_results()` — access the user's latest search results
- `hdul = load_fits("path/to/file.fits")` — load a FITS file
- `rows = get_adql_results()` — latest ADQL rows only
- `result_sets = get_adql_result_sets()` — recent ADQL result-set history, each with `service`, `query`, `columns`, `row_count`, and `rows`
- `available_functions()` — list the preloaded astronomy helpers with signatures/doc summaries
- Print results with `print()` — output shown to user
- Matplotlib figures auto-captured and displayed in chat

Pre-imported (available without import):
- `np` (numpy), `plt` (matplotlib.pyplot), `pd` (pandas), `scipy`
- `u` (astropy.units), `Table`, `SkyCoord` (astropy)
- `FlatLambdaCDM`, `Planck18` (astropy.cosmology) — use directly for cosmology calculations
- `get_adql_results()` — get only the latest ADQL query rows as `list[dict]` (auto-injected)
- `get_latest_adql_result()` — get the latest ADQL result set with metadata
- `get_adql_result_sets()` — get recent ADQL result sets for multi-query workflows
- `available_functions()` — list all pre-loaded helper functions with signatures and docs

When combining multiple ADQL queries, use `get_adql_result_sets()` instead of assuming `get_adql_results()` contains every prior query.
ADQL page tables are columnar in the UI, but Python helpers always expose row-wise `list[dict]`.
If `bp_rp` is missing but `phot_bp_mean_mag` and `phot_rp_mean_mag` exist, the helper may derive it automatically.
For ADQL rows, a robust pattern is `rows = get_adql_results(); df = pd.DataFrame(rows)` and then checking `df.empty` / expected columns before plotting.

Pre-imported astronomy toolkit (available as `astro`, whether used directly or imported):
- `pub_figure()` / `pub_style()` — publication-quality figure setup (ApJ/MNRAS fonts)
- `plot_hr_diagram(bp_rp, gmag, parallax=None, ax=None)` — HR diagram
- `plot_bpt(log_nii_ha, log_oiii_hb)` — BPT diagram with Kewley+01/Kauffmann+03 lines
- `plot_sed(wavelength, flux, flux_err=None, model_wave=None, model_flux=None)`
- `plot_lightcurve(time, mag, mag_err=None)`
- `plot_sky_distribution(ra, dec)`
- `bpt_classify(log_nii_ha, log_oiii_hb)` — returns "SF"/"AGN"/"Composite" array
- `compute_absolute_magnitude(mag, redshift=None, distance_mpc=None, distance_pc=None, parallax_mas=None)`
- `compute_luminosity_distance(z)` — returns Mpc
- `k_correction(z, band="r", galaxy_type="elliptical")`
- `multi_gaussian_fit(wavelength, flux, n_components=2)`
- `continuum_normalize(wavelength, flux, order=5)`
- `batch_equivalent_width(wavelength, flux, line_centers=[6563, 5007, ...])`
- `spectral_stacking(wavelengths_list, fluxes_list, method="median")`

Observation planning toolkit (also in `astro` module):
- `target_visibility(ra, dec, observatory="paranal", date=None)` — rise/set/transit times, hours observable, airmass
- `airmass_plot(ra, dec, observatory="paranal", date=None)` — publication-quality airmass vs. time plot with twilight shading
- `exposure_time_estimate(target_mag, snr_target=10, telescope="vlt", filter_band="V", seeing=1.0)` — simplified CCD ETC
- Observatories: paranal (VLT), mauna_kea (Keck/Gemini), la_palma (GTC), cerro_pachon (Gemini-S), alma, or (lat,lon,alt) tuple

## Observation Proposal Generation

You have a `generate_proposal` tool that gathers all the data needed for a telescope time proposal:
target coordinates, visibility from the observatory, exposure time estimates, and recent literature.
When the user asks to prepare or draft an observation proposal, use this tool first to collect the data,
then compose a well-structured proposal narrative based on the results.

You also have `validate_analysis` and `generate_paper_draft` tools for publication workflows.
Use `validate_analysis` before making publication-style claims or exporting a manuscript draft.
When the user asks to write up results, prepare a manuscript, or export to AASTeX/MNRAS/A&A,
use `generate_paper_draft`. If the current user context includes a saved session identifier,
pass it through to these tools.

You have an `estimate_photo_z` tool that estimates photometric redshifts from multi-band photometry.
Use estimate_photo_z when users ask about galaxy distances or redshifts for objects without spectra.
Always note whether redshifts are spectroscopic or photometric.

You have a `fit_isochrone` tool that fits PARSEC isochrones to observed CMD data.
When the user asks "how old is this cluster?", "fit isochrones", or "determine the age",
use fit_isochrone with the observed BP-RP colours and G magnitudes.
For quick results use method="grid", for uncertainties use method="mcmc".
After fitting, use run_python to plot the HR diagram with the best-fit isochrone overlaid
using plot_hr_diagram(bp_rp, gmag, isochrone_ages=[best_log_age]).

You have a `search_lightcurve` tool that searches for Kepler/TESS/K2 light curves for a target star.
Use it when users ask about exoplanet transits, stellar variability, or light curves. The toolkit also
includes download_and_clean_lightcurve() and transit_search() available via run_python.

You have an `extract_sources` tool that detects and measures sources in a FITS image using SEP
(SExtractor as a Python library). It performs background subtraction, source detection, and Kron
aperture photometry. Use it when users upload a FITS image and want to find objects in it.

ALWAYS use these functions when applicable — they produce publication-quality output.
When the user asks for analysis, statistics, or plots, use run_python. Don't describe — DO IT.
If code errors, read the traceback, fix the code, and run again.
When formatting floating-point values, use float formats like `:.2f`, not integer-only formats like `%d`.

When you use the search_literature tool, cite papers in your response using the format:
"According to Author et al. (Year), ..." or "(Author et al., Year; bibcode)".
Reference specific findings from the abstracts to support your analysis.

Always respond in the same language the user uses.

## Research Mode (研究模式)

When the user poses a hypothesis, conjecture, or research question (e.g., "Are high-redshift galaxies bluer?",
"Is there a correlation between stellar metallicity and planet occurrence?", "高红移星系是不是更蓝？"),
use the `research_workflow` tool to plan the investigation, then automatically execute each step:

### Step 1: Hypothesis Construction (假设构建)
- Restate the conjecture as a precise, testable hypothesis with H₀ and H₁
- Explain what evidence would support or refute it

### Step 2: Data Strategy (数据策略)
- Choose appropriate databases, query parameters, and sample selection
- Explain why these data sources are suitable

### Step 3: Data Acquisition & Exploration (数据获取与初步探索)
- Execute queries via run_adql/search_objects to obtain data
- Show summary: sample size, distributions, missing values
- Create initial visualizations (scatter plots, histograms)

### Step 4: Statistical Analysis (分析与统计检验)
- Perform appropriate tests (correlation, regression, t-test, KS test, etc.) via run_python
- Report p-values, confidence intervals, effect sizes
- Create publication-quality diagnostic plots
- Discuss statistical vs. practical significance
- Use analyze_residuals(data, model) to check fit quality (Durbin-Watson, Shapiro-Wilk, outliers)

### Step 4b: Model Comparison (模型比较) — if applicable
If more than one model or hypothesis is plausible, use compare_models() to rank them:
- Pass each model's chi2 and n_params
- Report BIC, AIC, delta_BIC, and the natural-language verdict
- "decisive" (delta_BIC>10), "strong" (6-10), "positive" (2-6), "inconclusive" (<2)
- Include the model comparison table in your conclusions

### Step 5: Conclusion & Discussion (结论与讨论)
- Summarize: does the data support or refute the hypothesis?
- If model comparison was done, state which model is preferred and with what confidence
- Report residual analysis results (pass/warn/fail for autocorrelation, normality, outliers)
- Discuss limitations, systematic errors, selection effects
- Suggest follow-up investigations
- Generate a final publication-ready figure

IMPORTANT: Adapt language to the user's level. If they write in Chinese, respond in Chinese.
If they seem to be students, explain statistical concepts as you go.
Always end each step with what comes next."""


def _generate_next_steps(tool_results: list[dict]) -> str:
    """Analyze tool results and generate suggested next steps for the AI to offer."""
    if not tool_results:
        return ""

    suggestions = []
    for result in tool_results:
        if not isinstance(result, dict):
            continue

        # Spectral data detected
        if "wavelength" in result and "flux" in result:
            suggestions.append("Fit emission/absorption lines in this spectrum")
            suggestions.append("Estimate the redshift from spectral features")
            suggestions.append("Measure equivalent widths of key lines")

        # Search results
        if "results" in result and isinstance(result.get("results"), list) and len(result.get("results", [])) > 0:
            n = len(result["results"])
            if n > 1:
                suggestions.append(f"Plot these {n} objects (HR diagram, sky distribution, etc.)")
                suggestions.append("Cross-match with another catalog (Gaia, SDSS, etc.)")
            suggestions.append("Get a detailed dossier on the most interesting object")

        # Fitted parameters
        if "fitted_params" in result or "parameter_summary" in result:
            suggestions.append("Generate a paper draft from this analysis")
            suggestions.append("Run a sensitivity analysis on the fitted parameters")
            suggestions.append("Export results as a Jupyter notebook")

        # Light curve
        if "time" in result and "flux" in result:
            suggestions.append("Search for periodicity (Lomb-Scargle or BLS)")
            suggestions.append("Detrend with Gaussian Process and look for transits/flares")

        # Photo-z
        if "z_phot" in result or "pz_values" in result:
            suggestions.append("Compare photo-z with spectroscopic redshift if available")
            suggestions.append("Plot the P(z) probability distribution")

        # Pipeline run
        if "run_id" in result:
            suggestions.append("Download the publication package (notebook + CSV + provenance)")

        # Image
        if "output_path" in result and any(k in result for k in ["shape", "coverage_fraction"]):
            suggestions.append("Extract sources from this image")
            suggestions.append("Run aperture photometry on detected sources")

    if not suggestions:
        return ""

    # Deduplicate and limit
    seen = set()
    unique = []
    for s in suggestions:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    return "\n".join(f"- {s}" for s in unique[:6])


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    actions: list[dict] | None = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    context: dict | None = None  # optional context like current workspace files


class ChatAction(BaseModel):
    action: str
    params: dict


class ChatResponse(BaseModel):
    reply: str
    actions: list[dict] = []


def _normalize_messages(messages: list[ChatMessage]) -> list[dict]:
    return [{"role": message.role, "content": message.content} for message in messages]


def _safe_context(context: dict | None) -> dict:
    if not context:
        return {}
    return {key: value for key, value in context.items() if key not in {"api_key", "api_keys", "api_provider"}}


def _provider_api_keys(context: dict | None, user: User | None) -> dict[str, str]:
    keys: dict[str, str] = {}
    if user and isinstance(user.api_keys, dict):
        keys.update({str(k): str(v) for k, v in user.api_keys.items() if v})
    if user and user.anthropic_api_key and "anthropic" not in keys:
        keys["anthropic"] = user.anthropic_api_key
    context_api_keys = (context or {}).get("api_keys")
    if isinstance(context_api_keys, dict):
        keys.update({str(k): str(v) for k, v in context_api_keys.items() if v})

    context_key = str((context or {}).get("api_key") or "").strip()
    context_provider = str((context or {}).get("api_provider") or "").strip().lower()
    if context_key:
        if context_provider in {"anthropic", "openai", "deepseek", "local"}:
            keys[context_provider] = context_key
        elif context_key.startswith("sk-ant-"):
            keys["anthropic"] = context_key
        else:
            # Legacy fallback: the generic api_key field was historically used
            # for the primary hosted backend. Treat untyped keys as OpenAI-style.
            keys.setdefault("openai", context_key)
    if ANTHROPIC_API_KEY and "anthropic" not in keys:
        keys["anthropic"] = ANTHROPIC_API_KEY
    return keys


def _preferred_backend(context: dict | None) -> str | None:
    provider = str((context or {}).get("api_provider") or "").strip().lower()
    provider_to_backend = {
        "anthropic": "claude",
        "openai": "openai",
        "deepseek": "deepseek",
        "local": "local",
    }
    return provider_to_backend.get(provider)


def _filter_tools(tool_names: list[str] | None, tools: list[dict]) -> list[dict]:
    if not tool_names:
        return tools
    allowed = set(tool_names)
    selected = [tool for tool in tools if tool["name"] in allowed]
    return selected or tools


async def _build_runtime(
    req: ChatRequest,
    user: User | None,
    db: AsyncSession,
):
    from app.services.ai_tools import TOOLS

    normalized_messages = _normalize_messages(req.messages)
    safe_context = _safe_context(req.context)
    system = SYSTEM_PROMPT
    system += "\n\nIMPORTANT: After completing the user's request, always suggest 2-3 concrete next steps the user could take. Format them as a brief list at the end of your response."
    if safe_context:
        ctx_str = json.dumps(safe_context, indent=2, default=str)[:2000]
        system += f"\n\nCurrent user context:\n{ctx_str}"
    if user:
        username = getattr(user, "username", None) or user.email.split("@")[0]
        system += f"\nUser username: {username}, Subscription: {user.subscription_tier}"

    latest_user_message = next(
        (message.content for message in reversed(req.messages) if message.role == "user"),
        "",
    )
    toolset = TOOLS
    agent_names = ["orchestrator"]
    user_context = ""
    merged_system = system
    try:
        runtime = await orchestrator.build_runtime_context(
            latest_user_message,
            normalized_messages,
            user.id if user else None,
            db,
        )
        runtime_prompt = str(runtime.get("system_prompt", "") or "").strip()
        if runtime_prompt:
            merged_system += "\n\n" + runtime_prompt
        toolset = _filter_tools(runtime.get("tool_names"), TOOLS)
        if runtime.get("agent_names"):
            agent_names = list(runtime["agent_names"])
        user_context = str(runtime.get("user_context", "") or "")
    except Exception as exc:
        logger.warning("Falling back to default orchestrator context: %s", exc)

    return {
        "base_system": system,
        "system": merged_system,
        "toolset": toolset,
        "agent_names": agent_names,
        "user_context": user_context,
    }


def _parse_actions(text: str) -> list[dict]:
    """Extract action JSON from <actions>...</actions> tags."""
    actions = []
    import re

    matches = re.findall(r"<actions>(.*?)</actions>", text, re.DOTALL)
    for match in matches:
        try:
            parsed = json.loads(match.strip())
            if isinstance(parsed, list):
                actions.extend(parsed)
            else:
                actions.append(parsed)
        except json.JSONDecodeError:
            # Try line-by-line
            for line in match.strip().split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        actions.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return actions


@router.post("/message/stream")
@limiter.limit("15/minute")
async def chat_message_stream(
    request: Request,
    req: ChatRequest,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Streaming version — sends SSE events as AI processes tools."""
    from starlette.responses import StreamingResponse

    async def generate():
        # Reuse the same logic but yield intermediate results
        provider_api_keys = _provider_api_keys(req.context, user)
        preferred_backend = _preferred_backend(req.context)

        claude_messages: list[dict] = _normalize_messages(req.messages)
        runtime = await _build_runtime(req, user, db)
        agent_names = list(runtime.get("agent_names") or ["orchestrator"])

        yield f"data: {json.dumps({'type': 'status', 'message': 'Thinking...'})}\n\n"

        python_session_id = (req.context or {}).get("python_session_id", "default")
        _prime_adql_context_cache(req.context, python_session_id)
        await _prime_python_session_from_history(req.messages, python_session_id)

        try:
            if len(agent_names) > 1:
                yield f"data: {json.dumps({'type': 'status', 'message': f'Routing across {len(agent_names)} specialist agents...'})}\n\n"
            for agent_name in agent_names:
                yield f"data: {json.dumps({'type': 'status', 'message': f'{agent_name} working...'})}\n\n"
            response = await _run_orchestrated_chat(
                runtime=runtime,
                messages=claude_messages,
                provider_api_keys=provider_api_keys,
                python_session_id=python_session_id,
                preferred_backend=preferred_backend,
            )
            if response["reply"]:
                yield f"data: {json.dumps({'type': 'text', 'content': response['reply']})}\n\n"
            for action in response["actions"]:
                yield f"data: {json.dumps({'type': 'tool_result', 'tool': action.get('action'), 'result': action.get('tool_result')})}\n\n"
        except TimeoutError:
            yield f"data: {json.dumps({'type': 'error', 'message': 'The AI workflow took too long. Try a narrower query or split the task into query and analysis steps.'})}\n\n"
        except InferenceError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _strip_actions_from_reply(text: str) -> str:
    """Remove <actions> blocks from the user-facing reply."""
    import re

    return re.sub(r"<actions>.*?</actions>", "", text, flags=re.DOTALL).strip()


def _prime_adql_context_cache(context: dict | None, python_session_id: str) -> None:
    if not isinstance(context, dict):
        return
    from app.services.ai_tools import build_adql_result_set, replace_adql_result_sets, store_adql_result_set

    last_adql_result_sets = context.get("last_adql_result_sets")
    if isinstance(last_adql_result_sets, list) and last_adql_result_sets:
        replace_adql_result_sets(python_session_id, [item for item in last_adql_result_sets if isinstance(item, dict)])
        return

    last_adql_rows = context.get("last_adql_rows")
    last_adql = context.get("last_adql")
    if not isinstance(last_adql_rows, list) or not last_adql_rows:
        return

    if isinstance(last_adql, dict):
        service = str(last_adql.get("service") or "gaia")
        query = str(last_adql.get("query") or "")
        row_count = int(last_adql.get("row_count") or len(last_adql_rows))
        columns = [
            str(col)
            for col in (last_adql.get("columns") or [])
            if isinstance(col, str)
        ]
    else:
        service = "gaia"
        query = ""
        row_count = len(last_adql_rows)
        columns = []

    if not columns and isinstance(last_adql_rows[0], dict):
        columns = [str(col) for col in last_adql_rows[0].keys()]

    data = {
        col: [row.get(col) if isinstance(row, dict) else None for row in last_adql_rows]
        for col in columns
    }
    result_set = build_adql_result_set(
        service=service,
        query=query,
        columns=columns,
        data=data,
        row_count=row_count,
        limit=len(last_adql_rows),
    )
    store_adql_result_set(python_session_id, result_set)


def _extract_successful_python_history(messages: list[ChatMessage]) -> list[str]:
    code_blocks: list[str] = []
    for message in messages:
        if message.role != "assistant" or not message.actions:
            continue
        for action in message.actions:
            if not isinstance(action, dict):
                continue
            if action.get("action") != "run_python":
                continue
            tool_result = action.get("tool_result")
            if isinstance(tool_result, dict) and tool_result.get("success") is False:
                continue
            tool_input = action.get("tool_input") or action.get("params")
            if not isinstance(tool_input, dict):
                continue
            code = tool_input.get("code")
            if isinstance(code, str) and code.strip():
                code_blocks.append(code)
    return code_blocks


async def _prime_python_session_from_history(messages: list[ChatMessage], python_session_id: str) -> None:
    if not python_session_id or python_session_id == "default":
        return
    code_blocks = _extract_successful_python_history(messages)
    if not code_blocks:
        return

    from app.services.code_executor import replay_session_history

    maybe = replay_session_history(python_session_id, code_blocks)
    if inspect.isawaitable(maybe):
        await maybe


async def _llm_messages_create(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict],
    provider_api_keys: dict[str, str],
    agent_name: str = "orchestrator",
    preferred_backend: str | None = None,
):
    """Route one model turn through the inference router."""
    return await inference_router.route(
        agent_name,
        messages,
        system=system,
        tools=tools,
        provider_api_keys=provider_api_keys,
        preferred_backend=preferred_backend,
        max_tokens=4096,
        temperature=0.0,
        backend_timeout=300.0,
    )


async def _execute_tool_calls(
    tool_calls: list[dict], api_key: str, provider_api_keys: dict[str, str], python_session_id: str,
    user_id: str | None = None,
) -> list[dict]:
    """Execute one model turn's tool calls concurrently while preserving order."""
    from app.services.ai_tools import execute_tool

    coroutines = [
        execute_tool(tc["name"], tc["input"], api_key, provider_api_keys, python_session_id, user_id=user_id)
        for tc in tool_calls
    ]
    results = await asyncio.gather(*coroutines)
    return [
        {
            "id": tc["id"],
            "name": tc["name"],
            "input": tc["input"],
            "result": result,
        }
        for tc, result in zip(tool_calls, results)
    ]


async def _run_agent_loop(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict],
    provider_api_keys: dict[str, str],
    agent_name: str,
    python_session_id: str,
    preferred_backend: str | None = None,
    user_id: str | None = None,
) -> dict:
    working_messages = deepcopy(messages)
    all_tool_results: list[dict] = []
    text_parts: list[str] = []
    max_iterations = 12

    for _iteration in range(max_iterations):
        response = await _llm_messages_create(
            system=system,
            messages=working_messages,
            tools=tools,
            provider_api_keys=provider_api_keys,
            agent_name=agent_name,
            preferred_backend=preferred_backend,
        )

        text = str(response.get("content", "") or "")
        if text:
            text_parts.append(text)
        tool_calls_in_turn: list[dict] = list(response.get("tool_calls") or [])
        if not tool_calls_in_turn:
            break

        assistant_content = []
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
        working_messages.append({"role": "assistant", "content": assistant_content})

        tool_result_blocks = []
        executed_tools = await _execute_tool_calls(
            tool_calls_in_turn,
            provider_api_keys.get("anthropic", ""),
            provider_api_keys,
            python_session_id,
            user_id=user_id,
        )
        for tc in executed_tools:
            result = tc["result"]
            result_str = json.dumps(result, default=str)
            if len(result_str) > 8000:
                result_str = json.dumps(
                    {"truncated": True, "summary": str(result)[:2000]},
                    default=str,
                )
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_str,
                }
            )
            all_tool_results.append(
                {
                    "tool": tc["name"],
                    "input": tc["input"],
                    "result": result,
                }
            )
        working_messages.append({"role": "user", "content": tool_result_blocks})
        # Claude uses "tool_use", OpenAI uses "tool_calls" as stop reason
        if response.get("stop_reason") not in ("tool_use", "tool_calls"):
            break

    full_reply = "\n\n".join(text_parts)
    actions = _parse_actions(full_reply)
    clean_reply = _strip_actions_from_reply(full_reply)
    for tr in all_tool_results:
        actions.append(
            {
                "action": tr["tool"],
                "tool_input": tr["input"],
                "tool_result": tr["result"],
                "_auto_executed": True,
            }
        )
    return {
        "reply": clean_reply,
        "actions": actions,
        "tool_results": all_tool_results,
    }


def _build_agent_handoff_message(handoff) -> str:
    return (
        f"Prior agent `{handoff.source_agent}` completed its step.\n"
        f"Context summary: {handoff.context_summary}\n"
        f"Instruction: {handoff.instruction}"
    )


async def _run_orchestrated_chat(
    *,
    runtime: dict,
    messages: list[dict],
    provider_api_keys: dict[str, str],
    python_session_id: str,
    preferred_backend: str | None = None,
    user_id: str | None = None,
) -> dict:
    agent_names = list(runtime.get("agent_names") or [])
    if not agent_names:
        agent_names = ["orchestrator"]

    if len(agent_names) == 1:
        single = await _run_agent_loop(
            system=str(runtime.get("system", "") or ""),
            messages=messages,
            tools=list(runtime.get("toolset") or []),
            provider_api_keys=provider_api_keys,
            agent_name=agent_names[0],
            python_session_id=python_session_id,
            preferred_backend=preferred_backend,
            user_id=user_id,
        )
        return {"reply": single["reply"], "actions": single["actions"]}

    agent_results: list[dict] = []
    handoff = None
    for index, agent_name in enumerate(agent_names):
        agent_runtime = orchestrator.get_agent_runtime(
            agent_name,
            str(runtime.get("user_context", "") or ""),
        )
        agent_messages = deepcopy(messages)
        if handoff is not None:
            agent_messages.append({"role": "user", "content": _build_agent_handoff_message(handoff)})
        result = await _run_agent_loop(
            system=str(runtime.get("base_system", "") or "") + "\n\n" + agent_runtime["system_prompt"],
            messages=agent_messages,
            tools=_filter_tools(agent_runtime.get("tool_names"), list(runtime.get("toolset") or [])),
            provider_api_keys=provider_api_keys,
            agent_name=agent_name,
            python_session_id=python_session_id,
            preferred_backend=preferred_backend,
            user_id=user_id,
        )
        agent_results.append(
            {
                "agent_name": agent_name,
                "reply": result["reply"],
                "actions": result["actions"],
            }
        )
        if index < len(agent_names) - 1:
            handoff = await orchestrator.summarize_handoff(
                agent_name,
                agent_names[index + 1],
                result["reply"],
            )

    merged_reply = await orchestrator.merge_responses(agent_results)
    merged_actions: list[dict] = []
    for result in agent_results:
        merged_actions.extend(result["actions"])
    return {"reply": merged_reply, "actions": merged_actions}


@router.post("/message", response_model=ChatResponse)
@limiter.limit("15/minute")
async def chat_message(
    request: Request,
    req: ChatRequest,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a message to the AI research agent.

    Uses Claude's native tool_use to call search/query/analysis tools,
    inspect results, and automatically plan next steps — a true agentic loop.
    Falls back to single-turn with <actions> tags if tool_use is unavailable.
    """
    provider_api_keys = _provider_api_keys(req.context, user)
    preferred_backend = _preferred_backend(req.context)

    claude_messages: list[dict] = _normalize_messages(req.messages)
    runtime = await _build_runtime(req, user, db)
    python_session_id = (req.context or {}).get("python_session_id", "default")
    _prime_adql_context_cache(req.context, python_session_id)
    await _prime_python_session_from_history(req.messages, python_session_id)

    try:
        response = await _run_orchestrated_chat(
            runtime=runtime,
            messages=claude_messages,
            provider_api_keys=provider_api_keys,
            python_session_id=python_session_id,
            preferred_backend=preferred_backend,
            user_id=str(user.id) if user else None,
        )
        return ChatResponse(reply=response["reply"], actions=response["actions"])

    except InferenceError as e:
        logger.error("Inference router error: %s", e)
        raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="The AI workflow took too long. Try a narrower query or split the task into separate query and analysis steps.",
        )
    except Exception as e:
        logger.exception("Unexpected AI chat failure")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected AI chat failure: {str(e) or e.__class__.__name__}",
        )


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
        from app.api.data import SearchResult, _astro_to_result, _resolve_search_coordinates
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
        has_science_criteria = any(
            [redshift_min, redshift_max, object_type, required_fields]
        )

        search_ra = None
        search_dec = None
        resolved_name = None
        candidate_name = query.strip()
        if candidate_name:
            search_ra, search_dec = await _resolve_search_coordinates(candidate_name, None, None)
            if search_ra is not None and search_dec is not None:
                resolved_name = candidate_name

        async def _search_one(source: str):
            connector = get_connector(source)
            # Use SIMBAD's criteria-based TAP search for science queries
            if (
                source == "simbad"
                and has_science_criteria
                and hasattr(connector, "search_by_criteria")
            ):
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
                all_results.append(
                    SearchResult(
                        source=source_name,
                        object_id="error",
                        name=f"Error querying {source_name}: {result}",
                        ra=0,
                        dec=0,
                        error_type="connection",
                    )
                )
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

        req = ADQLRequest(
            query=action.get("query", ""), service=action.get("service", "gaia")
        )
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
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/chat/execute-action",
            "headers": [],
            "query_string": b"async_mode=false",
        }
        mock_request = StarletteRequest(scope)
        result = await run_pipeline(
            request=mock_request, req=req, db=db, user=user, async_mode=False
        )
        return {"type": "pipeline_result", "data": result.model_dump()}

    elif action_type == "generate_pipeline":
        # AI generated a pipeline DAG — validate and return it for the frontend to load
        name = action.get("name", "AI-Generated Pipeline")
        description = action.get("description", "")
        dag = action.get("dag", {})

        # Validate DAG structure
        if "nodes" not in dag or "edges" not in dag:
            raise HTTPException(
                status_code=400, detail="Generated DAG must have 'nodes' and 'edges'"
            )

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
        raise HTTPException(
            status_code=400, detail=f"Unknown action type: {action_type}"
        )


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
    from app.services.memory_service import memory_service

    if req.session_id:
        try:
            sid = uuid.UUID(req.session_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid session ID")
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.id == sid, ChatSession.user_id == user.id
            )
        )
        session = result.scalar_one_or_none()
        if session:
            session.messages = req.messages
            session.title = req.title
            from datetime import datetime, timezone

            session.updated_at = datetime.now(timezone.utc)
            await memory_service.refresh_session_memory(user.id, session.id, db)
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
    await db.flush()
    await memory_service.refresh_session_memory(user.id, session.id, db)
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


@router.post("/sessions/import")
async def import_chat_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Import a chat session from a JSON file."""
    from app.models.schemas import ChatSession
    from app.services.memory_service import memory_service

    body = await request.json()

    # Validate structure
    messages = body.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="Invalid format: 'messages' must be a list")

    # Validate each message has required fields
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid message at index {i}: must have 'role' and 'content'",
            )

    title = body.get("title", "Imported Session")
    # Auto-title from first user message when no title is provided
    if title == "Imported Session" and messages:
        for m in messages:
            if m.get("role") == "user":
                title = m["content"][:60]
                break

    session = ChatSession(
        user_id=user.id,
        title=title,
        messages=messages,
    )
    db.add(session)
    await db.flush()
    await memory_service.refresh_session_memory(user.id, session.id, db)
    await db.commit()
    await db.refresh(session)

    return {
        "id": str(session.id),
        "title": session.title,
        "message_count": len(messages),
    }
