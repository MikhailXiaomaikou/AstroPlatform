# Standard Astro Architecture

**Current as of the knowledge-base expansion (Phase A–G + post-bugfix loop).**

## 1. System Shape

Standard Astro is a full-stack astronomy platform with four runtime layers:

1. **Frontend SPA** — React + TypeScript application hosting Data Browser, AI Chat, Pipeline Studio, Workspace, ADQL editor, Observations, Account/Settings, Help, and shared-session pages.

2. **FastAPI application** — Single backend process exposing 28 domain routers for auth, data, AI chat (with SSE streaming), pipelines, exports, collaboration, research memory, alerts, provenance, and admin analytics.

3. **Execution + storage services** — SQL database for metadata (PostgreSQL in prod, SQLite in dev), local filesystem for FITS, optional Redis + Celery for async pipelines and background execution.

4. **External astronomy + LLM services** — 23 archive connectors, NASA ADS / arXiv, astrometry.net, and routed LLM backends (Claude, OpenAI, DeepSeek, local).

The platform is designed so users can move between search → chat → pipeline → workspace → export without leaving the same data context. The AI assistant bridges all modules by being able to call any backend capability via its 52-tool catalog.

## 2. Frontend Architecture

Main entrypoint: [App.tsx](./frontend/src/App.tsx)

### Top-level pages

- **Data Browser** — Multi-source search UI, FITS preview, result actions, object-detail flows, batch mode, saved searches.
- **Chat** — AI assistant with persistent sidebar (Claude-desktop style), session save/load/rename, export actions, sharing, snapshots, collaboration controls, Plotly chart expand/download.
- **Pipeline Studio** — React Flow canvas, node palette (35 types), parameter editor, template/version management, execution progress, quick-start templates.
- **ADQL** — Multi-service TAP editor with syntax highlighting, template library, and result forwarding into chat.
- **Workspace** — User files (FITS, VOTable, results) with tags, notes, batch upload, export.
- **Observations** — Transient alert feed (TNS/Lasair/ZTF), anomaly explorer, follow-up recommendations.
- **Team** — Collaboration hub: friends, shared datasets, shared pipelines, comments, activity feed.
- **Account** — Profile, API keys (Fernet-encrypted), subscription tier, usage stats.
- **Shared Session** — Read-only / comment / fork view for shared analysis sessions (tokenized URLs).
- **Landing / Help / Auth** — Public entry, onboarding documentation, login/register flows.

### Shared frontend infrastructure

- [`src/api/client.ts`](./frontend/src/api/client.ts) — Central typed API client, auth header injection, page-tracking headers, helper wrappers, SSE streaming support.
- [`src/context/AuthContext.tsx`](./frontend/src/context/AuthContext.tsx) — JWT-based auth state and profile lifecycle.
- [`src/hooks/useTracking.ts`](./frontend/src/hooks/useTracking.ts) — Session-scoped event tracking for analytics.
- [`src/components/nodes/*`](./frontend/src/components/nodes) — Pipeline palette, node renderer, node parameter editing.
- [`src/components/viz/*`](./frontend/src/components/viz) — SpectrumViewer, LightCurveViewer, ImageCutoutViewer, MCMCDiagnostics, PlotBuilder, AladinViewer, ProvenanceGraph.
- [`src/i18n/index.tsx`](./frontend/src/i18n/index.tsx) — 4-language translations (English, Chinese, French, Spanish).

## 3. Backend Architecture

Backend entrypoint: [`backend/app/main.py`](./backend/app/main.py)

### API domains (28 routers)

| Router | Responsibility |
|---|---|
| `auth` | Username/password, JWT, Google OAuth, profile/settings |
| `data` | Multi-archive search, advanced search, FITS upload/preview/download |
| `chat` | AI chat endpoint, tool loop, session persistence, rename, sharing |
| `pipeline` | DAG validation, sync/async execution, template/version APIs, batch |
| `workspace` | File metadata, tags, notes, export integration |
| `integration` | ADQL/TAP (with timeout retry), SAMP, VOTable, Jupyter export |
| `export` | Markdown/report/notebook/LaTeX/BibTeX + chat-based export |
| `paper` | Paper draft generation and manuscript download |
| `sessions` | Share links, comments, session forking, snapshots, restore, diff |
| `research` | Opt-in memory profile and history APIs |
| `alerts`, `anomalies`, `followup`, `dossier` | Time-domain and anomaly features |
| `citations`, `citation_graph` | NASA ADS + arXiv literature search |
| `arxiv` | arXiv full-text extraction |
| `crossmatch` | Position + probabilistic cross-matching |
| `visualization` | Interactive plot generation |
| `provenance` | IVOA ProvDM lineage export |
| `team` | Friends, team members, shared resources, activity feed |
| `scheduler` | Scheduled analysis jobs |
| `isochrones` | Cached PARSEC isochrone delivery |
| `inference` | Inference routing, model health, cost tracking |
| `settings` | Encrypted API key storage |
| `health` | Liveness, readiness, service stats |
| `events` | Analytics event ingestion |

### Key backend subsystems

#### AI layer

- [`app/ai/orchestrator.py`](./backend/app/ai/orchestrator.py) — Classifies request intent, assembles specialist-agent context, chooses tool subsets.
- [`app/ai/inference_router.py`](./backend/app/ai/inference_router.py) — Routes inference to Claude / OpenAI / DeepSeek / local backends, records cost/latency.
- `app/ai/agents/*` — Specialist prompt definitions (data, analysis, literature, observation, visualization).
- [`app/services/ai_tools.py`](./backend/app/services/ai_tools.py) — **52-tool catalog** and execution layer. Each tool has a literature-cited docstring and input schema.
- [`app/api/chat.py`](./backend/app/api/chat.py) — Chat loop, empty-reply fallback, `chat_session_id` threading, SSE streaming, ~51 KB literature-cited `SYSTEM_PROMPT` covering 16 astronomy domains.

#### Data access layer

- `app/connectors/*` — 23 archive-specific adapters normalized behind a common `BaseConnector.search()` / `fetch()` / `normalize()` interface.
- [`app/connectors/registry.py`](./backend/app/connectors/registry.py) — Lazy registry for all 23 connectors.
- `app/search/*` — Natural-query parsing and filter extraction.

**Connector list:**
- **Core optical/NIR:** `sdss`, `gaia`, `simbad`, `vizier`, `mast`, `ned`, `2mass`, `allwise`, `panstarrs`, `lamost`, `desi`
- **Space / multi-wavelength:** `chandra`, `xmm`, `alma`, `eso`, `irsa`, `jwst`
- **Radio:** `nvss`, `first`
- **Specialized (new):** `jpl` (Horizons), `atnf_pulsar` (ATNF PSRCAT), `sparc` (rotation curves), `frbstats` (FRB catalog)

#### Analysis layer

- [`app/services/astro_analysis.py`](./backend/app/services/astro_analysis.py) — Astronomy helper functions (60+ public functions) exposed to the Python sandbox and AI tools:
  - Distance/magnitude conversion
  - `fit_isochrone` (PARSEC CMD 3.9 + turnoff fallback)
  - `wd_cooling_age` (Bédard+ 2020 lookup table)
  - `bss_select` (blue straggler criteria from Rain+ 2021)
  - CCM89 extinction, IRSA dust lookup
  - Lomb-Scargle period, phase folding
  - Voigt / multi-Gaussian fitting
  - `plot_hr_diagram` (accepts both `parallax` and `distance_pc`)
  - `target_visibility`, `exposure_time_estimate`
  - Bootstrap / Monte Carlo error propagation
- [`app/services/spectral_analysis_pro.py`](./backend/app/services/spectral_analysis_pro.py) — NIST line identification, heliocentric correction, IFU kinematics.
- [`app/services/photo_z_pro.py`](./backend/app/services/photo_z_pro.py) — 30 SED templates + Calzetti dust + Madau IGM + Bayesian priors.
- [`app/services/bayesian_inference.py`](./backend/app/services/bayesian_inference.py) — MCMC chain diagnostics (R-hat, ESS, MCSE via ArviZ), Bayes factors, nested sampling.
- [`app/services/time_domain_pro.py`](./backend/app/services/time_domain_pro.py) — GP detrending, BLS transit search, flare detection, transit fitting.
- [`app/services/image_processing_pro.py`](./backend/app/services/image_processing_pro.py) — Reprojection, mosaicking, PSF matching, deblending, cutouts.
- [`app/services/transient_classifier.py`](./backend/app/services/transient_classifier.py) — Random Forest light-curve classifier + spectral template matching.
- [`app/services/code_executor.py`](./backend/app/services/code_executor.py) — **Sandboxed Python execution** with session-scoped variables. The `ALLOWED_MODULES` whitelist now includes:
  - **Original:** numpy, scipy, astropy, specutils, photutils, dynesty, arviz, celerite2, batman, matplotlib, pandas, dask, pyvo, sklearn
  - **New (Phase B/C):** sherpa, radvel, thejoker, galpy, pysme, statmorph, vorbin, ppxf, astroquery, dustmaps, healpy, lenstronomy, MulensModel, treecorr, yt, pint, psrqpy, skimage, warnings

#### Pipeline layer

- [`app/pipeline/engine.py`](./backend/app/pipeline/engine.py) — DAG validation, topological execution, Redis caching (shared connection pool), sync fallback, Celery execution entrypoint, provenance recording per node.
- `app/pipeline/nodes/*` — **35 node types**:
  - **Data input:** QueryData, ImportWorkspace, LoadData
  - **CCD reduction:** BiasSubtract, DarkCorrect, FlatField, CosmicRayReject
  - **Astrometry/photometry:** AstrometricSolve, SourceExtract, PSFPhotometry, PhotCalibrate, ImageStack
  - **Image processing:** Reproject, Mosaic, PSFMatch, Deblend
  - **Spectroscopy:** Denoise, SpectralFit, RedshiftEstimate, EquivalentWidth, FluxCalibrate, TelluricCorrect, SpectraStack
  - **Time-domain:** TimeSeriesAnalysis, GPDetrend, TransitFit
  - **Statistical inference:** BayesianFit, PhotoZPro, SEDFit, CrossMatch
  - **Transforms:** CoordTransform, Condition
  - **Custom:** CustomScript
  - **Output:** Plot, InteractivePlot

#### Collaboration and memory

- [`app/api/sessions.py`](./backend/app/api/sessions.py) — Share tokens, comments, snapshots, fork flows. Now with message-limit pagination.
- [`app/services/memory_service.py`](./backend/app/services/memory_service.py) — Research profile generation, lightweight embedding store, history retrieval.
- [`app/api/team.py`](./backend/app/api/team.py) — Friends, team members, shared results, activity feed.
- [`app/api/ws.py`](./backend/app/api/ws.py) — WebSocket relay for real-time presence, cursor tracking, live comments.

#### Analytics

- [`app/services/event_collector.py`](./backend/app/services/event_collector.py) — Buffered event collection and bulk flush.
- [`app/middleware/event_tracking.py`](./backend/app/middleware/event_tracking.py) — Automatic API-level tracking on core routes.

## 4. Persistence Model

Core persistent entities:

- `User` — Auth profile with JWT, username, Google OAuth binding, subscription tier, Fernet-encrypted API keys
- `DataFile` — FITS/VOTable/CSV metadata with user/source indexes
- `PipelineRun`, `RunResult`, `PipelineTemplateDB`, `PipelineVersion` — Pipeline execution history with content-addressable caching
- `ChatSession` — Chat history with indexes on `user_id` and `(user_id, created_at)` for fast session list queries
- `PaperDraft` — Generated paper drafts in structured JSON form
- `SharedSession`, `SessionFork`, `SessionComment`, `SessionSnapshot` — Collaboration primitives
- `UserResearchProfile`, `SessionEmbedding` — Opt-in research memory
- `UserEvent` — Analytics events with buffered flush
- `InferenceLog` — Cost / latency / model tracking
- Additional tables for alerts, anomalies, teams, setup keys, schedules

The project keeps SQLite compatibility in development via custom `UUIDType` and `JSONType`, while running PostgreSQL in production.

## 5. Runtime Flows

### Search flow

1. User submits a query in the Data Browser.
2. Frontend calls `/api/data/search` or `/api/data/advanced-search` with an `AbortController` to cancel stale requests.
3. Backend routes to selected connectors; some searches auto-select sources based on query keywords (e.g. "quasar" → SIMBAD + Gaia qso_candidates).
4. Connectors run concurrently with per-source timeouts.
5. Results are normalized into a shared `SearchResult` response model with a sanitized `extra` dict (NaN→None via `_safe_float`).
6. Full results stored under the `"latest"` cache key; AI can retrieve them via `get_last_search_results`.
7. Files fetched from search results land in Workspace storage and become reusable elsewhere.

### AI chat flow

1. Frontend sends message history plus context to `/api/chat/message/stream` (SSE).
2. Backend builds runtime context:
   - System prompt (~51 KB, 16 object-class workflows + data quality guardrails + Gaia DR3 tables reference)
   - Specialist-agent prompt fragments based on intent classification
   - Filtered tool list (52 tools available)
   - Chat session ID from `current_session_id` (for tools that need session state)
3. Inference router calls the configured LLM backend (Claude by default).
4. **Tool calls execute concurrently** in a Python `asyncio` loop; each tool is a wrapper around analysis/data functions.
5. Tool results are appended back into the model loop until the turn completes (max ~12 iterations).
6. **Empty-reply fallback**: if the model returns zero text but executed tools, chat.py synthesizes a minimal summary so users never see a blank bubble.
7. Reply + actions returned via SSE; auto-save after each turn; session auto-titled from first user message.
8. Saved sessions can later feed paper generation, collaboration, and research memory.

### ADQL flow

1. User writes ADQL or AI generates it via `run_adql` tool.
2. Backend calls `execute_adql_query` (standalone function, extracted from the FastAPI route handler so AI tools can use it directly).
3. On timeout (408/502/503), the system **automatically retries** with cone radius halved, then quartered.
4. Full result (up to 2000 rows from TAP) cached under `"latest_adql"` key; AI view shows first 100 rows + note.
5. AI can retrieve full data in `run_python` via `get_cached_results('latest_adql')`.

### Pipeline flow

1. User edits a DAG in the React Flow canvas.
2. Frontend posts the DAG to `/api/pipeline/run`.
3. Backend validates nodes, edges, topological order.
4. Execution happens either:
   - **Asynchronously** via Celery worker (if `PIPELINE_MODE=celery`), or
   - **Synchronously** in a thread executor
5. Each node's output is cached in Redis (shared connection pool) with a content hash key.
6. Provenance recorded per node (IVOA ProvDM format).
7. Node outputs merged and trimmed for API return.
8. Run metadata stored in `PipelineRun` + `RunResult`.

### Collaboration flow

1. A saved chat session can be shared with `view`, `fork`, or `comment` access.
2. Shared sessions loaded via tokenized URLs (`/shared/{token}`).
3. Forking creates a new `ChatSession` under the collaborator's account.
4. Snapshots serialize current chat-session state for point-in-time restore/diff.
5. Real-time presence via WebSocket (`/api/ws`).

### CCD reduction flow

1. User uploads or imports FITS data.
2. AI tools or pipeline nodes call CCD reduction helpers.
3. Bias → dark → flat → cosmic-ray → astrometry → source extraction steps run on stored FITS assets.
4. Reduced products written back to workspace storage and can re-enter Pipeline, Chat, or export flows.

## 6. AI Knowledge Base (system prompt)

The `SYSTEM_PROMPT` constant in [`app/api/chat.py`](./backend/app/api/chat.py) is ~51 KB of literature-cited workflow guidance organized as:

1. **Role and action types** — agent persona, JSON action schema, examples.
2. **Database decision tree** — which archive for which object class.
3. **Gaia DR3 data completeness** — 5-layer column availability by G magnitude.
4. **Gaia DR3 specialized tables** — 11 specialized tables (vari_rrlyrae, vari_cepheid, vari_eclipsing_binary, nss_two_body_orbit, galaxy_candidates, qso_candidates, astrophysical_parameters, ...).
5. **Gaia GSP-Phot data quality warnings** — explicit rules for when `ag_gspphot`, `mh_gspphot`, `teff_gspphot` are unreliable (distant, low-Z, crowded, faint, hot).
6. **Extinction routing for low-E(B-V) targets** — force `lookup_ebv_irsa` (SFD 1998) for |b|>20°, d<500pc, globular clusters. Hardcoded benchmark values (Pleiades A_V=0.12, M53 A_V=0.06).
7. **Blue straggler identification** — correct BSS criteria from Rain+ 2021 (not requiring BP-RP<0).
8. **Open cluster workflow** — Pleiades/NGC 1647/Hyades-class.
9. **Globular cluster workflow** — M53/M13/47 Tuc-class.
10. **Variable star workflow** — RR Lyrae (Muraveva+ 2018), Cepheids (Ripepi+ 2019), EB.
11. **Distance estimation hierarchy** — by distance range, with Lindegren+ 2021 ZP correction.
12. **Spectroscopic catalog selection** — LAMOST / APOGEE / GALAH / DESI / SDSS.
13. **Extinction / dust map options** — SFD/Bayestar/Green2019/Marshall.
14. **X-ray spectral analysis workflow** — Sherpa models, HI4PI NH, Wilms+ 2000 abundances.
15. **Galaxy SFR estimators** — K&E 2012 Table 1 (7 bands), Balmer decrement / UV slope dust correction.
16. **RV orbit fitting** — radvel/thejoker, Keplerian parameters, mass function.
17. **Galaxy rotation curves** — SPARC, NFW/Burkert/Einasto halos, galpy.
18. **Stellar atmospheres** — ATLAS9/MARCS/PHOENIX, pysme/ispec, VALD3.
19. **Galaxy morphology** — Sérsic profiles, galfit/statmorph.
20. **IMF** — Salpeter/Kroupa/Chabrier.
21. **Cluster virial / scaling** — Biviano 2006, Arnaud & Evrard 1999.
22. **Pulsars** — ATNF PSRCAT, YMW16 DM, Lorimer & Kramer 2004 formulas.
23. **White dwarf cooling** — Bédard+ 2020 Montreal tables.
24. **Brown dwarfs** — Kirkpatrick 2005 L/T/Y.
25. **IFU kinematics** — Voronoi binning, pPXF.
26. **AGN SED decomposition** — CIGALE, Shen+ 2011, Vestergaard & Peterson 2006 BH mass.
27. **Galactic streams** — GD-1, Sgr, Gaia-Enceladus.
28. **Solar system** — JPL Horizons, MPC, Bowell H-G system.
29. **P3 specialized domains (reference-only)** — FRB, GW counterparts, weak/strong lensing, BAO, CMB, N-body sims, microlensing, chemical evolution, adaptive optics, VLBI.
30. **Data integrity rules** — no simulated data, explicit failure reporting.
31. **Pipeline DAG generation** — node types with parameters, example workflows.
32. **Action JSON format** — SSE action delivery spec.

Every formula, constant, and table in the prompt is cited (author + year + journal + page). The earlier formula audit removed LLM-hallucinated values.

## 7. External Integrations

### Astronomy archives (23)

- **Core optical/NIR:** SDSS, Gaia DR3, SIMBAD, VizieR, MAST, NED, 2MASS, AllWISE, Pan-STARRS, LAMOST DR9, DESI EDR
- **Space observatories:** HST/Kepler/TESS (via MAST), JWST, ESO (VLT/VISTA), IRSA, Chandra, XMM-Newton
- **Sub-mm/radio:** ALMA, NVSS, FIRST
- **Specialized:** JPL Horizons (ephemerides), ATNF Pulsar Catalogue, SPARC rotation curves, FRBSTATS

### Other external services

- NASA ADS (citation search) and arXiv (full-text extraction)
- astrometry.net (WCS solving)
- IRSA dust map service (E(B-V) lookup)
- Anthropic / OpenAI / DeepSeek (LLM inference)
- Optional: Redis / Celery for async execution
- Optional: PARSEC CMD 3.9 (for live isochrones) — falls back to turnoff lookup on timeout

## 8. Deployment Profiles

### Minimal development

- FastAPI (uvicorn --reload)
- React / Vite dev server
- SQLite
- Local file storage
- No Redis / no Celery (sync pipeline mode)

### Standard production

- FastAPI web service
- PostgreSQL database
- Local filesystem or S3 for FITS
- React static frontend (built by Vite)
- Redis (cache + pub/sub)
- Celery worker + beat
- Uvicorn workers behind a load balancer
- Render.com blueprint available in `render.yaml`

### AI backend choices

- Claude-only deployment (recommended)
- Mixed Claude / OpenAI / DeepSeek routing via inference_router
- Local model fallback via OpenAI-compatible endpoint

## 9. Current Design Constraints

These are implementation realities worth keeping explicit:

- **Research memory** is opt-in and based on lightweight hashed embeddings, not heavyweight vector infrastructure.
- **Celery is optional**; the backend supports synchronous pipeline execution as a fallback.
- **Orchestrator** currently builds routed specialist context on top of a single chat-turn loop; the backend is prepared for richer multi-agent execution but the production path is still centered on one coordinated tool loop per turn.
- **Workspace files** are the handoff boundary between search, chat, export, and pipeline modules.
- **ADQL cache** stores full result sets but the AI model only sees the first 100 rows by default; downstream Python can retrieve the rest via `get_cached_results('latest_adql')`.
- **Python sandbox** has a whitelist + memory limit; packages requiring compiled C extensions are restricted to those that install cleanly on Python 3.14 (Corrfunc failed; treecorr works).
- **Some packages require external install** and can't be pip-installed alone (e.g. `sherpa.astro.xspec` needs HEASOFT; `CASA` needs separate distribution). The system prompt references these as "reference only" where necessary.
- **System prompt is ~51 KB / ~12.8 K tokens**. Further growth will require structural refactoring into jump-to sections.

## 10. Physics Formula Audit

A full audit of all astronomy formulas was performed, removing LLM-hallucinated values and citing each formula to its published source. Highlights:

- **Corrected** Gaia extinction coefficient: `A_G/A_V = 0.789` (Wang & Chen 2019) replacing the earlier `0.836` which was incorrectly attributed to Casagrande & VandenBerg 2018 (actual source: Jordi+ 2010).
- **Removed** an incorrect `+0.2` bolometric correction (wrong sign for A-type turnoff stars).
- **Fixed** the main-sequence ridge slope for MS-vs-RGB separation in `_estimate_age_from_turnoff`.
- **Annotated** the empirical binary bias correction (+0.3 mag) as empirical, not a standard literature value.
- **Rewrote** `wd_cooling_age` from a Mestel power-law to a 13-point log-log interpolation of Bédard+ 2020 Montreal cooling tables (validated: Crab pulsar τ_c matches literature).
- **Corrected** `chain_diagnostics` for the newer ArviZ API (explicit float cast + `from_dict(posterior=...)` keyword).

All formulas in the codebase now either:
1. Cite a specific published paper (author + year + journal + page) in code comments, or
2. Directly wrap a widely-used package (sherpa, galpy, radvel, pysme, statmorph, etc.) and the package handles the physics.

## 11. Testing Strategy

- **Unit + integration tests** — 697 pytest tests covering connectors, services, pipeline nodes, analysis, security, models.
- **End-to-end tests** — 29 integration tests in `tests/test_e2e_full.py` (marked `integration and not network`) covering AI tool dispatch, run_python sandbox, pipeline DAG execution, session state.
- **Smoke tests** — Post-bugfix loop verifies new tool/connector/sandbox imports + physical validation (Crab pulsar, K&E 2012 SFR, Bédard WD cooling).
- **CI** — GitHub Actions runs backend pytest + frontend vitest + TypeScript type check + ruff lint on every push.
- **Manual regression** — 7 rounds of real paper reproduction tests against:
  - NGC 1647 (open cluster, Frasca+ 2026) — age 199.5 vs 200 Myr (<1% error after fit_isochrone rewrite)
  - M53 (globular cluster + RR Lyrae)
  - Tom 2 blue stragglers (Rain+ 2021)
  - Vel OB1 OB runaway stars
  - White dwarf luminosity function
  - Pleiades IMF

This document tracks the **current repository state**, not an aspirational roadmap. Update it when modules, flows, or deployment assumptions materially change.
