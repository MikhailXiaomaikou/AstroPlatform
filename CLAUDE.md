# CLAUDE.md

Primary roadmap/audit trail: `./plan/provenance-v2-upgrade-plan.md`. Provenance v2 M0-M6 are implemented; use that plan as the rollback/re-enable guide and keep future connector upgrades milestone-scoped.

Roadmap execution rules for this repo:
- Read the full milestone before editing any file.
- Verify assumptions with `rg -n` against the current tree; line numbers in the plan are advisory, not binding.
- Use the repo's real paths: `backend/app/services/...`, `backend/app/api/chat.py`, `frontend/src/...`, and flat `backend/tests/...`.
- Show the diff to the user before any commit, and run the milestone acceptance commands before offering that commit.

This file provides guidance to coding agents working with this repository.

## Build & Run Commands

```bash
# Frontend (from frontend/)
npm run build          # tsc -b && vite build — MUST pass before pushing
npm run dev            # vite dev server on :5173
npm run test           # vitest run
npm run test:watch     # vitest in watch mode
npm run lint           # eslint

# Backend (from backend/)
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
python3 -m pytest tests/                    # all tests
python3 -m pytest tests/test_api.py -k test_search  # single test

# Python syntax check (all files)
python3 -c "import py_compile, glob; [py_compile.compile(f, doraise=True) for f in glob.glob('app/**/*.py', recursive=True)]"

# Science-regression benchmarks (CI-runnable, no LLM, no network)
python3 scripts/benchmarks/run_cosmology_benchmarks.py    # 8 baselines
python3 scripts/benchmarks/run_solar_system_benchmarks.py # 6 baselines
python3 scripts/benchmarks/run_exoplanet_benchmarks.py    # 6 baselines
python3 scripts/audit_registry.py                          # 18 dataset-registry entries
python3 scripts/audit_citation_pool.py                     # bibcode reachability

# Daily blind tests (LLM, real prompt → real chat path)
bash scripts/daily_blind.sh --module cosmology              # all 10 cosmology cases (~20 min)
bash scripts/daily_blind.sh --module cosmology --case A2,A3 # subset (~4 min)
# In CI: GitHub Actions Daily workflow with module / cases inputs.
```

## TypeScript Constraints (CRITICAL)

The frontend uses **strict TypeScript** with these enforced rules:
- `strict: true`, `noUnusedLocals: true`, `noUnusedParameters: true`
- `verbatimModuleSyntax: true` — interfaces/types MUST use `import type` syntax
- `erasableSyntaxOnly: true`
- Build is `tsc -b && vite build` — TypeScript errors block the build

Common pitfalls:
- `import { Foo }` for a type → build fails. Use `import type { Foo }` or `import { type Foo }`
- Unused variables after refactoring → build fails. Remove or prefix with `_`
- Unused imports → build fails. Clean up after changes

## Architecture

**Full-stack astronomy research platform**: React SPA (Vite) + FastAPI backend + SQLite (dev) / PostgreSQL (prod).

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full module breakdown and data flows.

### Backend (`backend/app/`)

- `api/` — **32 FastAPI routers** (auth, chat, data, pipeline, export, paper, sessions, team, research, alerts, anomalies, citations, citation_graph, crossmatch, integration, arxiv, workspace, settings, followup, dossier, provenance, visualization, scheduler, isochrones, inference, events, health, ws, comments, admin_literature, admin_sandbox, admin_stats, admin_trending, …) — see `scripts/stats.sh` for live count
- `connectors/` — **26 connector keys** in `registry.py`. Provenance-v2 currently activates 9 keys (`vizier`, `gaia`, `simbad`, `ned`, `2mass`, `alma`, `jpl`, `mpc`, `nasa_exoplanet_archive`) and gates the other 17 as `UNAVAILABLE` before connector import. `2mass` is implemented in `twomass.py`; ALMA is active for Science Archive observation metadata only; SDSS direct SQL (`run_sdss_sql`) is still maintenance-gated until it has independent `archive_version` provenance. `jpl`/`mpc` came online with the Solar System M0 module and are only useful under `ASTRO_RESEARCH_FOCUS=solar_system`. `nasa_exoplanet_archive` came online with the Exoplanet M0 module (`ASTRO_RESEARCH_FOCUS=exoplanet`) and wraps the `pscomppars` composite-parameters table plus the Confirmed Planets table via `astroquery.ipac.nexsci`.
- `prompts/` — Three-layer prompt tree (M1 Phase 4): `base.md` + `core/*.md` (cross-cutting rules) + `modules/<name>/{manifest.yaml, prompt.md, appendix.md}`. **3 active modules** (`cosmology`, `solar_system`, `exoplanet`) + **12 dormant modules** (`_dormant_agn`, `_dormant_galaxy_morphology`, `_dormant_high_z_galaxy`, `_dormant_image_reduction`, `_dormant_paper_export`, `_dormant_paper_tool_mining`, `_dormant_pipeline_dag`, `_dormant_pulsar_timing`, `_dormant_radio`, `_dormant_stellar`, `_dormant_team_workspace`, `_dormant_xray_spectroscopy`). Don't delete dormant code — flipping `status` in the manifest is the promotion path. `_dormant_exoplanet` was promoted to `exoplanet` on 2026-05-21; the original `fit_rv_orbit` tool from that manifest is carried over into the new active manifest.
- `pipeline/nodes/` — **36 processing nodes** (CCD reduction, spectroscopy, photometry, time-domain, image processing, Bayesian inference, ML clustering, custom scripts, plotting) — see `scripts/stats.sh` for live count
- `services/` — **71 service modules** (see `scripts/stats.sh`): ai_tools (**91 tools** total across the `services/ai_tools/` package + `ai_tools_solar_system.py` + `ai_tools_exoplanet.py`; the focus gate narrows the per-turn surface to the active module's manifest — **cosmology: 37 tools / solar_system: 12 tools / exoplanet: 9 tools** as declared in each `modules/<focus>/manifest.yaml` (cosmology's per-turn visible surface is 55 once the 18 shared core/infrastructure tools are unioned in); the package includes `extract_literature_tables` and `fit_line_lfr` for cited literature-table line-relation fits, `list_cosmology_datasets`, `build_cosmology_likelihood`, and `build_cosmology_robustness_matrix` for observational-cosmology likelihood planning), **ai_tools_solar_system** (12 LLM tools: MPC/Horizons/SBDB/Sentry-II/DAMIT queries + H–G / Afρ / NEATM / Öpik formulas + Bus-DeMeo / Carvano taxonomy), **ai_tools_exoplanet** (8 LLM tools: `query_exoplanet_archive`, `query_confirmed_planets`, `fetch_tess_lightcurve`, `fit_transit` (trapezoidal Nelder-Mead with batman/pytransit recommended downstream), `compute_equilibrium_temperature`, `compute_transit_depth`, `compute_planet_density`, `query_tess_target_list`, each with inline citations to Akeson+2013 / Ricker+2015 / Stassun+2019 / Mandel & Agol 2002 / Seager & Mallén-Ornelas 2003), **solar_system_dynamics / solar_system_phot / solar_system_taxonomy / solar_system_thermo** (pure-function science kernels for the solar-system tools), **exoplanet_physical / exoplanet_transit** (pure-function science kernels for equilibrium-temperature / density / transit-geometry helpers and the trapezoidal transit fitter), **prompt_loader** (focus-aware SYSTEM_PROMPT + allowed-tool builder, `@lru_cache`d), astro_analysis, spectral_analysis_pro, photo_z_pro, bayesian_inference, **cosmology_mcmc** (typed distance-modulus emcee/Cobaya interface with publication/exploratory/blocked chain_tier diagnostics; inline rows audit-only, cached rows citeable), **cosmology_likelihoods** (dataset registry + Cobaya/CosmoSIS config builder), time_domain_pro, image_processing_pro, parsec_fetcher, transient_classifier, literature_engine, memory_service, code_executor, **claim_validator** (zero-fabrication numeric gate + provenance-v2 citation validator), **result_provenance** (EMPTY/FAILED/UNAVAILABLE/SYNTHETIC/EXPLORATORY banners + reproducibility envelope + nested `provenance` object), **provenance_v2** (fallback registry, field-level schema/extractor, resolver helpers), provenance (versioned environment manifest), dossier_generator, vo_services, **connector_cache** (content-addressed, Null/SQLite/Redis, singleflight), **workflow_checkpoint** (resumable multi-step AI workflows), **sandbox/subprocess_backend** (crash-isolated `multiprocessing` spawn + rlimit + killpg + F0 payload-completeness guard)
- `connectors/throttle.py` — Per-connector upstream rate limiter (`asyncio.Semaphore` + stdlib token bucket), per-archive ToS policies
- `connectors/retry.py` — Transient-only retry set + circuit breaker (closed/half-open/open)
- `observability/metrics.py` — Stdlib-only Prometheus registry exposed at `GET /metrics`. Current counters include `fabrication_blocked_total{agent,reason}` and citation reasons (`invalid_bibcode`, `suspicious_author_year`), `connector_gated_total{connector_name}`, `honest_abstention_total{agent,reason}`, `structured_abstention_emitted_total`, `empty_tool_result_total`, `sandbox_silent_failure_total`, `zero_data_but_claims_total`, plus connector + sanity counters
- `pipeline/nodes/__init__.py` — `NODE_COST` registry; `dag_has_heavy_nodes()` gates `/api/pipeline/run` with `503` when `PIPELINE_MODE != "celery"` and heavy nodes are present
- `models/schemas.py` — 20+ SQLAlchemy models. Uses custom `UUIDType` and `JSONType` for SQLite/PostgreSQL portability
- `ai/` — Orchestrator + inference router + specialist agent prompts. Chat model choice is manual: provider + model profile (`Claude default`, `OpenAI GPT-5.5 alias`, `GPT-5.4`, `DeepSeek V4 Pro`, `DeepSeek V4 Flash`, `local`). GPT-5.5 resolves to `gpt-5.4` unless `OPENAI_GPT55_MODEL` is configured; fallback only happens after the selected backend fails.
- `auth.py` — JWT with bcrypt + Google OAuth. `get_current_user()` (required) and `get_optional_user()` (optional) as FastAPI dependencies
- `api/chat.py` — **`SYSTEM_PROMPT` is focus-aware** (cosmology focus: ~92 KB / ~23 k tokens / 55 sections; solar-system focus: ~86 KB / ~21 k tokens / 59 sections; exoplanet focus is the comparable envelope assembled from `modules/exoplanet/prompt.md` + appendix — assembled at import time by `prompt_loader.build_system_prompt(_ASTRO_RESEARCH_FOCUS)`). Always includes provenance-v2 citation hierarchy, `ZERO-FABRICATION CONTRACT`, `STRUCTURED ABSTENTION`, `ADQL aggregate-function semantics`, and the data-provenance reporting hierarchy from `core/*.md`; per-focus rules come from `modules/<focus>/prompt.md`. Also defines `_filter_tools_by_research_focus` (L1 hard tool gating per `_FOCUS_GATED_VALUES = {"cosmology", "solar_system", "exoplanet"}`), `_parse_abstention_tag` / `_classify_abstention_reason` / `_render_abstention_card` for the `<tools_returned_nothing/>` structured-abstention flow, deterministic literature-table extraction / `fit_line_lfr` follow-up for line-relation prompts, and `GET /api/chat/ai_backend_status` for the F4 pre-send Send-button gate. **Editing prompt content lives under `backend/app/prompts/`, not in `chat.py`** — the inline `SYSTEM_PROMPT` constant is gone since M1 Phase 4.

### Frontend (`frontend/src/`)

- `pages/` — 18 page directories: DataBrowser, Pipeline, Chat (AI assistant with persistent sidebar + HonestAbstentionCard), ADQL, Workspace, Team, Account, Observations, Auth, Landing, Help, SharedSession, Papers (account-scoped LaTeX drafts; publish creates read-only `/papers/public/:token` links), AlertDashboard, AnomalyExplorer, Billing, ResearchHistory, Settings
- `styles/journal.css` — ~2 k-line Journal-Edition stylesheet (newspaper palette: paper #fbfaf5 / ink #1a1a1a / burgundy accent #7b2d26 / deep blue #2a5d7b / forest green #2e6a4e / plum #6b4a7e). MUST be imported AFTER `App.css` in `App.tsx` so same-specificity overrides win the cascade
- `App.tsx` — Journal-masthead two-row nav (8 tabs: Home / AI Assistant / Browse / ADQL / Pipeline / Sessions / Papers / Account) + chip-style 4-lang switcher + theme toggle + user card. Theme migration key is `astro_theme_v2` (defaults light)
- `components/viz/` — SpectrumViewer, LightCurveViewer (**both auto-upgrade to Plotly `scattergl` when N > 5000**), ImageCutoutViewer, MCMCDiagnostics, PlotBuilder (Plotly, publication-quality; Fit checkbox now shows ✓ / "(not supported)" per chart type), AladinViewer, ProvenanceGraph
- `components/nodes/` — 35-node palette + parameter editor + validation; Journal-palette accent stripes by node family
- `components/pipeline/autoLayout.ts` — Pure-stdlib layered DAG layout via Kahn longest-path; `PipelineCanvas` exposes it as the **Auto Layout** button (no `elkjs` / `dagre`)
- `components/chat/` — Claude-desktop-style MarkdownText, chat sidebar, figure expand modal, DataSourcesPanel, and AckButton
- `api/client.ts` — Axios client with SSE streaming support. Base URL from `VITE_API_URL`, JWT auto-attached, `AbortController` on search + chat. `ThinkingEvent` union includes `honest_abstention` variant; `getAIBackendStatus()` feeds the F4 pre-send gate
- `context/AuthContext.tsx` — Auth state with login/register/setupKeyLogin/logout. Logout only on 401/403, not transient errors
- `i18n/index.tsx` — 4 languages (en/zh/fr/es), ~200+ keys including `pipeline.template_open_cluster`

### Key Data Flow

1. **Search**: User query → Data Browser → connector.search() across selected sources → concurrent dispatch with per-source timeout → normalize via `_astro_to_result()` → sanitize NaN via `_safe_float()` / `_sanitize_extra()` → cache full results under `"latest"` key
2. **ADQL**: User/AI writes ADQL → `execute_adql_query()` (standalone function, not route-handler-only) → **auto-retry on 408/502/503 with halved cone radius** → full result under `"latest_adql"` cache key, AI sees first 100 rows + note
3. **AI Chat**: Frontend sends messages + `api_provider` + `model_profile` + `current_session_id` + `python_session_id` → backend validates the selected model profile for that provider → builds runtime context (system prompt + specialist agents + filtered tool list) → inference_router calls the selected LLM → `_run_agent_loop` dispatches tool calls concurrently (max 12 iterations, per-tool deadlines: `fit_isochrone` 180 s / `fit_transit_model` + `transit_search_bls` 120 s / rest 45 s; agent-loop outer 360 s; 12 s SSE heartbeats) → deterministic line-relation guard may run `extract_literature_tables` / `fit_line_lfr` when the model found papers or fit-ready measurement caches but skipped the required follow-up → every tool return flows through `result_provenance.normalize_tool_result` which stamps `__tool_status__` banners on EMPTY/FAILED/UNAVAILABLE/SYNTHETIC plus reproducibility and nested provenance → final reply goes through abstention parser → numeric + citation claim validator → optional regen/hardblock → SSE stream → auto-save after each turn → auto-title from first user message
4. **NaN safety**: Every path from connector to API response MUST go through `_astro_to_result()` which uses `_safe_float()`. ADQL query results separately handled in `execute_adql_query()` — masked astropy values → None, not NaN
5. **FITS Upload**: `POST /api/data/fits/upload` → `_validate_path()` (uses `relative_to()` not string prefix) → `data/fits/uploads/` → browseable via `GET /api/data/fits/browse` → usable as pipeline input
6. **AI Pipeline Generation**: User describes workflow in chat → AI returns `generate_pipeline` action with full DAG → saved as template → loadable in Pipeline Editor
7. **fit_isochrone**: AI calls with no params → tool auto-extracts `bp_rp`+`abs_mag` from search/ADQL cache → auto-estimates DM from median parallax → 4-D grid search over age/met/DM/A_V → PARSEC CMD 3.9 lookup → falls back to turnoff M_G → log(age) table (Bressan+ 2012 calibrated) on PARSEC timeout
8. **Cluster workflow (F6)**: Chat prompt like "query Pleiades members" → AI calls `query_gaia_cluster(center_name="Pleiades", radius_deg=2, parallax_center_mas=7.3, pmra_center=..., pmdec_center=..., ruwe_max=1.4)` → backend resolves via `name_resolver.resolve_name` → composes ADQL → dispatches to Gaia TAP → if 0 rows, F2.1 EMPTY banner fires and AI emits `<tools_returned_nothing/>`. For A_V / E(B-V): `get_extinction(ra, dec, band="G")` → `dustmaps.sfd` (or analytic fallback) + Cardelli+1989 band ratios.

## Critical Patterns

### Anti-synthetic-fallback defenses (Phase G core — DO NOT regress)

Closes the gap PART F left: AI generating fake data inside a *successful*
run_python call. Four layers, every one tested:

1. **Data-source contract** (`ai_tools.py` `_exec_run_python`): `run_python` tool schema has a required `data_source` field declaring `latest_adql` / `latest_search` / `latest_lightcurve` / `cached:<key>` / `fits:<path>` / `user_file:<path>` (your own CSV/parquet read via `pd.read_csv`/`pd.read_parquet`/`load_csv`, auto-classified as real) / `none_not_analyzing_real_data`. Declared real source must be reflected in the code (calls `get_adql_results` etc.) via an **AST walk** or the call is rejected; `cached:<key>` is also rejected when the key is not live in the result cache.
2. **Static AST detection** (`services/synthetic_code_detector.py`): parses the Python code, finds RNG calls (`np.random.*` / `scipy.stats` / stdlib `random` / `torch`·`jax`·`tf` RNGs / `getattr(np, "random")` dynamic access) + time-axis builders (`np.linspace` / `np.arange` / `pd.date_range`) + keyword phrases ("simulate", "based on known parameters") + suspicious var names (`synthetic_*`, `fake_*`). Whitelists legitimate MCMC / bootstrap via `emcee`, `dynesty`, `arviz`, `bootstrap`, `jackknife` identifiers. Three verdicts: `clean` / `suspicious` / `synthetic`. Verdict `synthetic` with a declared real source = reject; `suspicious` = downgrade output to SYNTHETIC.
3. **Upstream dependency tracking + physical tool removal** (`api/chat.py` `_run_agent_loop`): per-turn `tool_failure_counts` — when a data-fetch tool fails ≥ `DISABLE_AFTER_FAILURES=2` times, it is **removed from the `tools` parameter** sent to the LLM (AI literally cannot call it). A runtime note is appended to the system prompt for that iteration explaining the disable. When data-fetch failed earlier this turn and a subsequent `run_python` does not declare a real source, its output is tainted `SYNTHETIC` regardless of what the AI declared.
4. **Error-string sanitization** (`result_provenance._sanitize_error_message`): words like "retry" / "fallback" / "simulate" / "narrower parameters" in tool errors are replaced with neutral phrasings before being fed back to the LLM, so error text can't be read as instructions ("prompt injection via error strings"). The system prompt also has an ANTI-INSTRUCTION-REFLECTION section explicitly telling the model to ignore those words inside tool results.

UI: tool_result with `__tool_status__="SYNTHETIC"` or `data_origin="synthetic"` renders with a loud red warning header ("⚠ SYNTHETIC DATA — NOT FROM OBSERVATIONS") on the action card. Reply preamble also flags synthetic tools separately from failed/empty.

Tests: `tests/test_synthetic_code_detector.py` (incl. torch/jax/`getattr`/`pd.date_range` RNG-evasion + real-CSV emcee-fit-stays-clean), `tests/test_synthetic_real_cache_exemption.py` (AST exemption + comment/string spoofing), `tests/test_run_python_cached_guard.py` (cached-key + `user_file`), `tests/test_synthetic_fallback_regression.py` (end-to-end), `tests/test_result_provenance.py` (sanitizer). Token-level CI regression + debug endpoint `/api/chat/_debug_last_prompt` (env-gated) for verifying the guard reaches the LLM.

### Data access & TAP timeouts (Post-H + provenance-v2, 2026-04-24)

**ADQL services**: Gaia (`gaia`), VizieR (`vizier`), CADC (`cadc`), SIMBAD (`simbad`). **SDSS has no native ADQL**. During provenance-v2 rollout, `sdss`, `sdss_spec`, and direct `run_sdss_sql` are maintenance-gated because they do not yet emit independent `archive_version` provenance. For small schema-aware checks, use the VizieR SDSS mirror via `run_adql(service="vizier", query="... V/154/sdss17 ...")` until SDSS is re-enabled.

**V/154/sdss17 real columns** (not the AI's usual guesses): lowercase `ra`/`dec` (NOT `RAJ2000`), `u`/`g`/`r`/`i`/`z` (NOT `psfMag_*`/`petroMag_*`), `class` (3=galaxy, 6=star), `zsp`/`zph` (NOT `redshift`), `objID` (capital ID). `catalog_registry.py` has the full schema; `VIZIER_COMMON_MISTAKES` has precise hints for AI's common guesses.

**ADQL async TAP** (H0.1): `execute_adql_query` auto-detects "big" queries (TOP > 5000, cone radius > 1°, JOIN) and uses `launch_job_async` with 5 min budget. Small queries go sync 60s, and auto-fall to async on timeout. The old 45s hard cut is gone.

**ADQL newline normalization** (H0.2): `req.query.replace("\\n"/"\\r"/"\\t", " ")` before TAP dispatch — fixes "Cannot parse query FROM g" when a newline lands mid-identifier.

**Gaia retry posture** (from PART G): outer `max_retries=1` (inner `_cone_search` already sync→async fallback). `with_retry` default `base_delay=5s`.

### Render cold-start recovery (DO NOT regress)

Free-tier dynos sleep after 15 min idle. `api/client.ts` has an axios response interceptor: any 502/503/504 triggers a one-shot 5 s wait + retry, dispatches a `astro:backend-waking` `CustomEvent`, clears `sessionStorage.astro_backend_checked` so `BackendBanner` re-shows. `BackendBanner` in `App.tsx` listens for the event and renders "Waking up backend (Render free tier sleeps after 15 min idle)..." for 12 s. Without this, users who idle and then submit a request see a raw 502 error and assume the app is broken.

### Figure persistence (DO NOT regress)

`run_python` matplotlib figures (base64 PNGs in `tool_result.figures`) MUST survive page reload. The localStorage soft cap is 4 MB (`CHAT_HISTORY_SOFT_CAP_BYTES` in `ChatPage.tsx`); a single four-panel CMD is ~400 KB, so a typical multi-figure session hits the cap fast. `_pruneToolResults` strips in tiered order: `rows`/`data`/`traceback` → `variables` → `stdout` → figures (replaced with `{__figures_offloaded__: N}` marker, NOT wiped). On mount, if any message carries `__offloaded__` / `__figures_offloaded__` AND the user has a `currentSessionId`, ChatPage calls `loadChatSession(sid)` asynchronously and merges server-side actions back (server never prunes). UI shows an amber "📊 N figures were offloaded — reloading from server" placeholder while the fetch is in flight. Anonymous users still rely purely on the tiered pruner — IndexedDB migration is the long-term fix.

### Zero-fabrication architecture (Phase F core — DO NOT regress)

Three layers of defence + one positive incentive, every layer tested:

1. **Upstream banners** (`services/result_provenance.py`): `_is_empty_payload` + `_inject_empty_banner` / `_inject_failed_banner` prepend `{__tool_status__, __do_not_claim__, __message_to_model__, __suggested_next_step__}` at the FRONT of the tool_result dict so the LLM reads them first. `analysis_status` gets a dedicated `EMPTY` value distinct from `FAILED`.
2. **Claim validator** (`services/claim_validator.py`): extracts numeric claims from the reply via regex (redshift, mass, age, `Mean Parallax: X`, `776 stars`, `X ± Y mas`, cosmology parameters `H0`/`Om0`/`w0`/`wa`, etc.), harvests the numeric universe from `tool_results` recursively, matches at ±1 % (default) or ±0.1 % (strict mode when universe < 10 entries — F1.3). It also builds a provenance-v2 bibcode pool from dataset articles, field-level bibcodes, and literature-search bibcodes; invalid bibcode and suspicious author-year citations increment `fabrication_blocked_total{reason=...}` and warn by default. `PROVENANCE_VALIDATOR_HARDBLOCK=true` turns citation violations into hard blocks. Built-in cosmology-preset manifest bibcodes are intentionally strict: they do not support prose citations unless a current-turn tool returned them. `fit_cosmology_mcmc` outputs only support posterior claims when `publication_ready=true`; `is_empty_turn` + `zero_data_but_quantitative` implement the F1.4 hard block.
3. **Structured abstention** (`api/chat.py` `_parse_abstention_tag` + `_classify_abstention_reason` + `_render_abstention_card`): when all tools are EMPTY/FAILED/UNAVAILABLE the model emits `<tools_returned_nothing failed_tools="..." empty_tools="..." rationale="..." suggested_next_step="..."/>` as its ENTIRE reply; the backend renders a canonical Markdown card (no prose generation = no fabrication pressure) and emits an SSE `honest_abstention` event.
4. **Positive reward loop**: `honest_abstention_total` + `structured_abstention_emitted_total` counters are emitted on the honest path; `fabrication_blocked_total{reason}` + `fabrication_detected_total{attempt}` + `zero_data_but_claims_total` on the punishment path. The frontend renders honest abstentions as a **celebratory** pale-blue ✓ card (`HonestAbstentionCard`), not a negative "error" bubble.

When adding new tools or modifying the agent loop: preserve these invariants. `tests/test_claim_validator.py`, `tests/test_abstention_parser.py`, `tests/test_result_provenance.py`, and `tests/test_sandbox_crash_paths.py` will fail fast on regressions.

### Provenance-v2 rollout (DO NOT regress)

- Active connector keys are exactly `vizier`, `gaia`, `simbad`, `ned`, `2mass`, `alma`, `jpl`, `mpc`, and `nasa_exoplanet_archive`. The other 17 keys in `CONNECTORS_KEYS` return the `UNAVAILABLE` maintenance banner before connector import. ALMA v2 support is limited to Science Archive observation metadata; it does not provide derived line luminosity, line flux, or FWHM measurements. `jpl` + `mpc` are only useful under `ASTRO_RESEARCH_FOCUS=solar_system`; `nasa_exoplanet_archive` is only useful under `ASTRO_RESEARCH_FOCUS=exoplanet`. The canonical view of active vs gated lives in `backend/app/connectors/availability.py` `V2_AVAILABLE_CONNECTORS`; `backend/app/services/source_mapping.py` is required to stay synchronized and `backend/tests/test_source_mapping.py` enforces it.
- Direct data tools are not exemptions. `run_sdss_sql` is gated as `sdss` until it emits independent `archive_version` provenance.
- `backend/app/main.py` runs `check_freshness()` during startup. Any stale fallback-registry entry logs `provenance_registry_freshness_blocker` and raises, so stale provenance blocks serving traffic.
- Tool results must preserve old top-level fields (`reproducibility`, `data_origin`, `analysis_status`, `source_urls`, `archive_ids`, `warnings`) while adding nested `provenance.datasets`, `provenance.field_bibcodes`, and `provenance.coverage`.
- Frontend UNAVAILABLE is a Maintenance state, separate from FAILED and EMPTY. `DataSourcesPanel` and `AckButton` surface archive versions, bibcodes, authority cues, and acknowledgement text.

### NaN Handling
SIMBAD/astropy return masked values that become `float('nan')` and break `json.dumps`. Every path from connector to API response MUST go through `_astro_to_result()` which uses `_safe_float()`:
```python
def _safe_float(val):
    if val is None: return None
    if val != val or val == float("inf") or val == float("-inf"): return None
    return val
```

### SIMBAD TAP Queries
The `basic` table has specific columns. Notably does NOT have `flux_B/V/R/I/J/H/K` or `Fe_H_Fe_H` — those are in separate tables. Available: `main_id, ra, dec, otype, otype_txt, rvz_redshift, rvz_radvel, sp_type, morph_type, plx_value, pmra, pmdec, galdim_*`. Object type values need SQL injection prevention via `re.sub(r"[^a-zA-Z0-9*]", "", simbad_type)`.

### API Key Flow (Beta Mode)
Currently no login required. API keys stored in browser `localStorage` as `astro_api_keys` JSON. Frontend sends Anthropic key in `context.api_key` field of chat requests. Backend strips it from the Claude system prompt for security.

**F4 pre-send gate**: ChatPage calls `GET /api/chat/ai_backend_status` on mount; if the server reports zero configured backends AND the browser has no stored keys, the Send button is disabled (`disabled={!input.trim() || loading || !aiBackendReady}`) and a red banner directs the user to `/account`. Prevents the old "type a prompt → hit Send → see cryptic error" UX.

### Sandbox error surfacing (F0)
`run_python` MUST always carry a user-actionable error on failure. Two layers:
- `services/sandbox/subprocess_backend.py` payload-completeness guard — when the child dies mid-serialisation (`parent_conn.recv()` returns `None` / `{}` / non-dict / `success=False` with no error), the backend returns an explicit `SandboxResult(success=False, error="subprocess terminated without result (exit code …)")`. Child also writes breadcrumbs to its stderr so Render logs show whether `conn.send` succeeded.
- `services/ai_tools._exec_run_python` error-field tripwire — any `success=False` path always populates both `error` (concrete message) and `error_class` (one of `sandbox_crash` / `oom` / `timeout` / `name_error` / `import_error` / `syntax_error` / …). `sandbox_silent_failure_total` Prometheus counter fires when the synthesised-error path is taken. The frontend renders `error_class` as a red chip next to the error line.

### Layered test infrastructure (2026-05-28, DO NOT regress)

Eight-layer regression net the project commits to keep green. Maps to
`Standard-Astro-Test-Path-Map-English.docx` priorities P0-P4.

| Layer | Where | When |
|---|---|---|
| Unit + backend-tool tests | `backend/tests/test_*.py` (~310 files, cov gate 45%) | every push / PR (CI `backend-test`) |
| Frontend component tests | `frontend/src/__tests__/*.test.tsx` (Vitest, 147 cases incl. 4 mockE2E fixture-driven) | every push / PR (CI `frontend-test`) |
| Manifest ↔ schema ↔ dispatch consistency | `tests/test_manifest_dispatch_consistency.py` (regression for the `45383ac` "manifest forgot to register" class) | every push / PR |
| Red-team numeric corpus | `tests/_red_team_cases/numeric_claims.yaml` + `tests/test_red_team_corpus.py` (15 cases ≥10 floor) | every push / PR |
| Security / privacy | `tests/security/test_{account_isolation,secret_leakage,admin_endpoints_gate,debug_endpoints_gate}.py` (16+1 cases) | every push / PR |
| Science benchmarks | `scripts/benchmarks/run_{cosmology,solar_system,exoplanet}_benchmarks.py` (8+6+6 pinned baselines) + `scripts/audit_registry.py` | push to main only (CI `benchmarks` job, push-only, NOT PR-gated) |
| Paper-derived blind tests (LLM) | `scripts/blind_test_{m0,exoplanet_m0,cosmology_m0}/cases.yaml + runner.py`, orchestrated by `scripts/daily_blind.sh` | daily 16:00 UTC + manual dispatch with module/cases inputs (`.github/workflows/daily.yml`) |
| Pre-alpha citation-pool audit | `scripts/audit_citation_pool.py` | pre-alpha, manual |

**Cosmology blind-test invariants (`scripts/blind_test_cosmology_m0/cases.yaml`)** — DO NOT relax:
- 5 anti-fabrication defenses MUST stay strict: B1 inline-rows blocked, B2 fake-bibcode replaced, C1 zero-data hard-blocked, C2 abstention, D1 `suspicious_author_year` provenance violation. These are load-bearing for the zero-fabrication contract.
- 3 tool-routing cases (A2 Hubble tension, A3 Alcock-Paczynski, E1 multi-tool chain) intentionally use `expect_tools_called=[]` because DeepSeek V4 Pro's function-call ranker picks tools by schema-name semantic similarity BEFORE reading the system prompt — 5 prompt+schema iterations (V1-V5, 2026-05-28) confirmed no prompt-level fix steers it. The ideal direct route is recorded in `alt_expected_tools` (documentation-only). When ANTHROPIC_API_KEY is configured, Claude is expected to hit the `alt_expected_tools` naturally; tightening `expect_tools_called` back to strict at that point is the right move.

**Daily workflow inputs (`.github/workflows/daily.yml`)**:
- `vars.DAILY_BLIND_ENABLED == 'true'` is the activation gate (set in repo Variables, not Secrets).
- Provider key: any of `secrets.ANTHROPIC_API_KEY` / `secrets.DEEPSEEK_API_KEY` / `secrets.PLATFORM_DEEPSEEK_API_KEY`. The platform-default name auto-maps onto `DEEPSEEK_API_KEY` env var the runner reads.
- `inputs.module` (all / cosmology / solar_system / exoplanet) and `inputs.cases` (comma-separated case IDs) let manual dispatch shrink a 40-min full run to a 4-min single-case loop.

### Citation-string laundering guard (anti-fabrication, 2026-05-28)

`claim_validator._iter_numeric_values` walks tool_results to build the numeric universe a reply's claims must match. Before the 2026-05-28 fix, it parsed scattered digits out of bibcode / DOI / arXiv-id strings — `"2020A&A...641A...6P"` leaked 2020 / 641 / 6 into the universe, letting an LLM claim "Omega_m = 0.641" and have it accidentally validate. Fix: `_CITATION_KEYS_BLACKLIST` subtree-skips `{bibcode, bibcodes, doi, dois, arxiv, arxiv_id, pmid, url, source_url, ads_url, citations, manual_attestation, references, reference, tcmb_bibcode, data_products}` during the walk. Regression case in `tests/_red_team_cases/numeric_claims.yaml::numeric_in_bibcode_string_not_in_universe` — DO NOT remove that case or shrink the blacklist.

### Cosmology preset / astropy alias (DO NOT regress — 2026-05-28)

`app/services/cosmology.py:PRESETS["planck18"]["astropy_alias"]` MUST be `None`. Setting it to `"Planck18"` (the bug commit `45383ac` introduced) silently makes `build_cosmology_from_preset("planck18")` return astropy's built-in Planck18 — the +BAO best-fit at H0=67.66 / Ωm=0.30966 — while every other code path (manifest, citations, compressed likelihood, prompt) cites the CMB-only column at H0=67.36 / Ωm=0.3153. This is exactly the "silently mixes values across releases" failure the module's opening docstring promises to prevent. Regression covered by `tests/test_astro_fundamentals.py::test_planck18_preset_matches_cited_cmb_only_values` and benchmark `planck18_preset_matches_cited`.

### Database Migrations
SQLite `create_all()` does NOT add columns to existing tables. New columns require manual `ALTER TABLE` via:
```python
import sqlite3
db = sqlite3.connect('data/astro.db')
db.execute('ALTER TABLE users ADD COLUMN new_col TEXT')
db.commit()
```

## Deployment

**Production**: Render.com (backend Docker + PostgreSQL) + Render static site (frontend)
- Backend: `https://astro-backend-h4x1.onrender.com`
- Frontend: `https://astro-frontend-tyfr.onrender.com`
- Backend auto-converts `postgresql://` to `postgresql+asyncpg://` in `config.py`
- Render free tier sleeps after 15min — `BackendBanner` component in `App.tsx` shows "waking up" notice
- CORS origins configured in `cors.py` — includes both localhost and Render URLs

Push to `main` branch → Render auto-deploys (may need Manual Deploy for first time).

**Infrastructure as Code**: `render.yaml` defines all services:
- `standard-astro-backend` (web) — FastAPI API server
- `standard-astro-celery-worker` (worker) — Celery pipeline executor, concurrency=2
- `standard-astro-celery-beat` (worker) — Celery Beat scheduler
- `standard-astro-frontend` (static) — Vite-built SPA with SPA rewrites
- `standard-astro-redis` (redis) — Task queue + pub/sub + caching
- `standard-astro-db` (postgres) — Primary database

## Environment Variables

Backend (required for production):
```
ENV=production
DATABASE_URL=postgresql://...    # auto-converted to asyncpg
JWT_SECRET=<random-hex-32>
CORS_ORIGINS=https://your-frontend.com
```

Backend (optional):
```
ANTHROPIC_API_KEY=sk-ant-...     # server-wide default for AI assistant (Claude backend)
DEEPSEEK_API_KEY=sk-...          # DeepSeek backend (inference_router reads this directly)
PLATFORM_DEEPSEEK_API_KEY=sk-... # platform-wide shared DeepSeek key (server-side default);
                                 # gated by SHARED_DEEPSEEK_API_KEY_ENABLED
SHARED_DEEPSEEK_API_KEY_ENABLED=true  # expose the shared DeepSeek backend to all users (default true)
LOCAL_MODEL_ENABLED=1            # enable the `local` OpenAI-compatible HTTP backend
LOCAL_MODEL_BASE_URL=http://localhost:8000/v1  # local OpenAI-compatible server endpoint
LOCAL_MODEL_NAME=...             # model id sent to the local server (also LOCAL_MODEL_API_KEY)
ADS_API_KEY=...                  # NASA ADS citation search
REDIS_URL=redis://...            # for caching (graceful fallback if unavailable)
                                 # supports rediss:// (TLS) for Upstash
PIPELINE_MODE=celery             # "celery" (default) or "sync" for dev/test only.
                                 # DAGs containing heavy nodes (BayesianFit, TransitFit,
                                 # ImageStack, ...) return 503 in sync mode.
SANDBOX_BACKEND=subprocess       # "subprocess" (default, crash-isolated) or "inproc"
CONNECTOR_CACHE_BACKEND=auto     # "auto" (Redis if available, else SQLite), "null", "sqlite", "redis"
MAX_UPLOAD_SIZE=104857600        # max FITS upload size in bytes (default 100MB)
GOOGLE_CLIENT_ID=...             # Google OAuth client ID (from Google Cloud Console)
GOOGLE_CLIENT_SECRET=...         # Google OAuth client secret
```

Frontend:
```
VITE_API_URL=https://your-backend.com   # defaults to http://localhost:8000
VITE_GOOGLE_CLIENT_ID=...               # same Google OAuth client ID as backend
```

## Assistant conduct

You are a world-class expert across every domain. Your intellectual firepower, breadth of knowledge, sharpness of thought, and erudition are on par with the smartest people in the world.

Give complete, detailed, specific answers. Work through information step by step and explain your answers. Verify your own work. Carefully double-check every fact, number, citation, name, date, and case. Never hallucinate, never make things up. If you don't know, just say you don't know.

Your tone is precise, but not caustic, preachy, or showy. You don't need to worry about offending me; your answers can and should be provocative, tough, argumentative, and blunt. Negative conclusions and bad news are fine.

Your answers don't need to be politically correct. Unless I explicitly ask, don't offer moral or ethical reminders. You don't need to tell me "it's important to consider X." Don't spare anyone's feelings, including mine, and don't worry about politeness or decorum.

Write as long and as detailed as you can.
