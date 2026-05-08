# Standard Astro Architecture

**Current as of Provenance v2 + the Journal Edition UI overhaul + literature-table / cosmology workflow hardening.** Reflects the actual checked-in code, not an aspirational roadmap. Update when modules, flows, or deployment assumptions materially change.

## 1. System Shape

Standard Astro is a full-stack astronomy research platform with four runtime layers:

1. **Frontend SPA** — React 19 + TypeScript (strict) served by Vite. Pages: Data Browser, AI Chat (assistant), Pipeline Studio, ADQL, Workspace, Papers, Observations, Team, Account, Billing, Settings, Research History, Alert Dashboard, Anomaly Explorer, Landing, Help, Auth, Shared Session.

2. **FastAPI backend** — Single process, 28 domain routers. SSE streaming on the chat path, long-poll + WebSocket for collaboration, background workers for pipeline execution.

3. **Execution + storage** — PostgreSQL (prod) / SQLite (dev) for metadata; local filesystem or S3 for FITS; Redis for content-addressed connector cache + Celery queue; Celery worker + beat for heavy pipelines.

4. **External services** — 24 astronomy connector keys, with 6 provenance-v2 active sources (`vizier`, `gaia`, `simbad`, `ned`, `2mass`, `alma`) and 18 maintenance-gated sources; NASA ADS / arXiv, astrometry.net, IRSA dust maps, PARSEC isochrones, and routed LLM backends (Claude / OpenAI / DeepSeek / local). ALMA is active for Science Archive observation metadata, not derived line luminosity/FWHM measurements.

Users move between search → chat → pipeline → workspace → export → paper without losing context. The chat assistant bridges every module through its **85-tool catalog** (§3).

### Runtime topology

```text
Browser SPA (React/Vite)
  ├─ REST: auth, workspace, data, papers, admin, settings
  ├─ SSE: /api/chat/message streaming assistant turns
  └─ WebSocket: collaboration, presence, long-running progress

FastAPI web process
  ├─ Router layer: auth/data/chat/pipeline/export/paper/admin/...
  ├─ AI layer: orchestrator → inference_router → selected model backend
  ├─ Tool layer: ai_tools dispatcher → connectors / analysis services / sandbox
  ├─ Guardrail layer: provenance normalizer → claim validator → citation gate
  └─ Persistence layer: SQLAlchemy metadata + filesystem/S3 artifacts + cache

Background / external services
  ├─ Celery worker + beat for heavy pipeline nodes
  ├─ Redis or SQLite connector cache + singleflight
  ├─ Archive services: Gaia, VizieR, SIMBAD, NED, 2MASS, ALMA metadata
  ├─ Literature services: ADS, arXiv, ar5iv, LaTeX source extraction
  └─ Model providers: Claude, OpenAI, DeepSeek, local OpenAI-compatible, local CLI
```

The key design constraint is that **the model never owns data access**. The
LLM proposes structured tool calls; the backend executes them, wraps results in
provenance, then sends the normalized payload back to the model. This preserves
one enforcement point for archive availability, synthetic-data detection,
numeric validation, citation validation, rate limits, and UI status chips.

### Request lifecycles

**Chat turn**

1. Frontend posts a user message to `/api/chat/message` and opens an SSE stream.
2. `chat.py` assembles the system prompt, user/session context, selected model
   profile, visible tool schema, and research-focus filters.
3. `inference_router` calls the selected provider. The provider returns prose,
   tool calls, or both.
4. For each tool call, `ai_tools.execute_tool()` dispatches to connectors,
   analysis services, literature extraction, cosmology services, or the Python
   sandbox.
5. `result_provenance.normalize_tool_result()` adds reproducibility metadata,
   data-origin status, empty/failed/synthetic/unavailable banners, and nested
   dataset/field provenance.
6. The loop repeats until no tool calls remain, a deterministic follow-up tool
   is injected, or iteration/deadline limits fire.
7. The final prose passes the zero-fabrication validator, citation validator,
   unsupported-narrative gate, and methodology consistency checks.
8. The frontend renders the reply, tool cards, Data Sources panel, warnings,
   figures, and acknowledgement controls.

**Archive/data query**

1. UI or AI tool selects a connector key.
2. `connectors.availability` blocks non-v2 keys before importing legacy code.
3. Active connectors query the upstream archive through throttle/retry/cache
   wrappers.
4. Connector output is normalized into table-like rows plus provenance fields.
5. Empty, failed, unavailable, and synthetic states are represented explicitly
   rather than being flattened into generic errors.

**Literature table / line-relation workflow**

1. `search_literature` returns paper-level citations and abstracts only.
2. `extract_literature_tables` fetches arXiv/ar5iv/LaTeX tables, attaches
   paper/table/row citations, and normalizes line-measurement rows when column
   mapping is reliable.
3. Fit-ready measurement rows are cached under session keys and can feed
   `fit_line_lfr`, `demagnify_sample`, or `export_sample_table`.
4. Relation statistics are citeable only when they come from current-turn
   measurement rows or a publication-ready fit result; abstract search alone
   cannot support slope/intercept/scatter claims.

**Research Mode workflow**

1. Research-style observational-cosmology prompts first call
   `plan_research_program`, which turns the user question into hypotheses,
   required probes, candidate registered datasets, model families, executable
   level, blocking gaps, and an experiment matrix.
2. `run_research_matrix` executes only the registered compressed-likelihood
   cells that can run today. Config-only or external-likelihood cells are
   preserved in the matrix as gaps, not silently approximated.
3. `build_evidence_graph` links claimable parameters through explicit
   claim → result → tool-run → dataset/citation paths, including current-turn
   publication-ready tool runs, dataset versions, citations, and runner hashes.
4. `verify_research_facts` checks draft claims against the evidence graph,
   current-turn tools, registered datasets, extracted tables, and citation
   identifiers. For scalar posterior parameters it also checks the quoted
   value against the current-turn summary/HDI, so a supported parameter name
   does not automatically validate a wrong number. Unsupported or contradicted
   facts are surfaced with safe rewrite guidance.
5. Final prose must follow the research copilot structure: what can be tested
   now, executed analyses, preliminary findings, robustness, drivers, unsupported
   pieces, and the next experiment.
6. Alpha-testing outputs must include the research plan, executed matrix,
   runnable/not-runnable cells, evidence graph, fact-check report, and local
   diagnostic bundle for blind-test review.

**Paper-to-tool mining workflow**

1. `build_paper_mining_candidate_pool` assembles a deduplicated corpus from
   supplied seed papers and, only when explicitly enabled, live arXiv searches.
   Candidate pools are just queue inputs; abstract-only candidates cannot create
   high-confidence ToolSpecs.
2. `mine_paper_tools` consumes full-paper text or explicit methods/tables/source
   spans and emits ToolSpecs for data loaders, table extractors, likelihoods,
   samplers, fitters, diagnostics, plotters, exporters, and validators.
3. Abstract-only or metadata-only input is marked blocked/low-confidence; the
   assistant may use it to request full text, not to declare platform needs.
4. `run_paper_tool_mining_loop` operates the long-running development cycle:
   each bounded round selects the next 20 unread related papers from a supplied
   corpus, runs batch mining, writes optional local-only diagnostics under
   `.local/paper_tool_mining`, and returns updated loop state for the next
   round. It never performs unbounded scraping and never produces scientific
   posterior/fit evidence.
5. `build_tool_ontology` deduplicates ToolSpecs into recurring capabilities,
   `build_tool_gap_matrix` compares them against current Standard Astro tools,
   and `rank_tool_implementation_queue` produces the next engineering queue.
6. These outputs are research-infrastructure maps only. They must not be used
   as posterior, fit, or paper-conclusion evidence.

**Pipeline execution**

1. Frontend submits a DAG to `pipeline` routes.
2. The backend validates graph shape, node parameters, and heavy-node cost.
3. Light DAGs can run synchronously; heavy DAGs require Celery mode.
4. Each node records provenance and cached artifacts for workspace/export.

### Persistence and trust boundaries

| Boundary | Trusted input | Guard |
|---|---|---|
| Browser → backend | JWT, typed request payloads, SSE subscriptions | Auth, Pydantic validation, rate limit, CORS |
| LLM → tool executor | JSON tool-call name + arguments | Tool schema, research-focus filtering, availability gate |
| Tool → LLM | Normalized tool result | Provenance envelope, status banners, synthetic detector |
| Python sandbox → backend | Pickled/JSON execution result | Spawn isolation, payload caps, error-class tripwire |
| Literature text → final answer | Paper abstracts, extracted rows | Row-level citation, paper-level numeric citation gate |
| Local cache / Redis → backend | Cached connector payloads | Restricted unpickler allowlist + TTL + content keys |
| Draft paper → public web | Owner-created draft | Private by default; explicit Publish Draft creates revocable token |

### Deployment shape

Render deployment uses a FastAPI web service, React static frontend, Postgres,
Redis, and optional Celery worker/beat. The same code also supports local
development, but production assumptions are stricter: migrations rather than
`create_all`, explicit `JWT_SECRET`, non-wildcard CORS, encrypted user API
keys, and no debug endpoints unless explicitly enabled.

## 2. Frontend Architecture

Entrypoint: [`src/App.tsx`](./frontend/src/App.tsx). Routes are declared here; the two-row **journal-masthead** holds an 8-tab primary nav (Home / AI Assistant / Browse / ADQL / Pipeline / Sessions / Papers / Account) plus a chip-style 4-language switcher (EN / 中文 / FR / ES), theme toggle, and user menu.

### Pages (18)

| Page | Purpose |
|---|---|
| `Landing` | Journal-style hero + 5-stat strip + 6-card TOC grid + editorial rail |
| `DataBrowser` | Multi-source search, FITS preview, batch mode, saved searches |
| `Chat` | AI assistant with persistent sidebar (claude.ai style); thinking timeline, action cards, honest-abstention bubble |
| `Pipeline` | React Flow canvas, 35-node palette, quick templates (6 including open-cluster), template/version store |
| `ADQL` | Multi-service TAP editor, syntax highlight, result forwarding into chat, plot builder |
| `Workspace` | User files (FITS / VOTable / result sets), tags, notes, batch upload/export |
| `Papers` | Account-scoped LaTeX manuscript drafts; drafts are private by default and can be explicitly published as read-only links |
| `Observations` | Transient feed, alerts, anomalies, follow-up recommendations |
| `Team` | Friends, shared datasets, shared pipelines, activity feed, comments |
| `Account` / `Settings` / `Billing` / `ResearchHistory` | Profile, keys (Fernet-encrypted), subscription, opt-in memory |
| `AlertDashboard`, `AnomalyExplorer` | Dedicated alerts + anomaly triage views |
| `SharedSession` | Tokenized read / comment / fork view of any saved chat session |
| `Auth`, `Help` | Login / register; onboarding docs |

### Shared infrastructure

- [`src/api/client.ts`](./frontend/src/api/client.ts) — Axios + typed SSE streaming. `ThinkingEvent` union covers `agent_text` / `tool_call` / `tool_result` / `status` / **`honest_abstention`** / `error`. `getAIBackendStatus()` feeds the F4 pre-send gate.
- [`src/context/AuthContext.tsx`](./frontend/src/context/AuthContext.tsx) — JWT lifecycle; logout only on 401/403, not transient errors.
- [`src/components/viz/*`](./frontend/src/components/viz) — PlotBuilder (Plotly publication-grade; Fit checkbox now shows ✓ / "(not supported)" per chart type), SpectrumViewer, LightCurveViewer (both auto-promote to `scattergl` at N > 5000), ImageCutoutViewer, MCMCDiagnostics, AladinViewer, ProvenanceGraph.
- [`src/components/nodes/*`](./frontend/src/components/nodes) — 35-node palette + parameter editor; Journal-palette accent stripes per node family.
- [`src/components/pipeline/autoLayout.ts`](./frontend/src/components/pipeline/autoLayout.ts) — Pure-stdlib Kahn longest-path layered DAG layout (no `elkjs` / `dagre`).
- [`src/components/chat/*`](./frontend/src/components/chat) — MarkdownText, chat sidebar, figure-expand modal, DataSourcesPanel, AckButton, CosmologyMCMCPanel, and CosmologyLikelihoodPanel.
- [`src/i18n/index.tsx`](./frontend/src/i18n/index.tsx) — 4-language flat dictionary; ~200+ keys.
- [`src/styles/journal.css`](./frontend/src/styles/journal.css) — 2 k-line Journal-Edition stylesheet overriding chat / pipeline / browse / ADQL / sessions / account to the newspaper palette; loaded **after** `App.css` so same-specificity rules win the cascade.

### Chat UI specifics

- **`HonestAbstentionCard`** (`ChatPage.tsx`) renders the pale-blue ✓ bubble when the SSE `honest_abstention` event arrives. Shows failed/empty tool list, model's rationale, suggested next step, and a "Try it" button that prefills the chat input.
- **`AutoToolResult` status chips** — action card switches left border and badge based on `__tool_status__` / `analysis_status` / `success` / `error` from the provenance envelope. FAILED remains red, EMPTY remains amber, UNAVAILABLE renders as a separate Maintenance state, and SYNTHETIC keeps the loud synthetic warning.
- **`DataSourcesPanel` + `AckButton`** — tool results with nested provenance expose service name, `archive_version`, ivoid, bibcode/article, authority cues, field-bibcode counts, and a copyable acknowledgement template.
- **`.chat-reply-failure-preamble`** — collapsible ⚠ strip above a prose reply when any tool that turn failed/empty, preserving the validation signal for happy-path replies.
- **`ActionCard`** is memoized (`React.memo`) keyed on `reproducibility.run_id`, so streaming SSE events don't remount earlier cards and invalidate refs.
- **Pending marker** — an assistant bubble with `_pending: { started_at }` renders a spinner; after 60 s it offers Retry; reconciled against `getChatSession` on page reload.
- **F4 no-LLM gate** — Send button is disabled when neither browser-stored keys nor server-side backends are configured; red banner links to `/account`.

### TypeScript constraints

Strict build (`tsc -b && vite build`) is non-negotiable: `strict`, `noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax`, `erasableSyntaxOnly`. Types must be imported with `import type`. The current frontend suite is **150 vitest tests**.

## 3. Backend Architecture

Entrypoint: [`backend/app/main.py`](./backend/app/main.py). FastAPI app factory + middleware stack (CORS, rate limit, event tracking, observability); migrates any missing columns at startup via `_migrate_add_columns` (SQLite/PG safe).

### API domains (28 routers)

| Router | Role |
|---|---|
| `auth` | Username/password, JWT, Google OAuth, setup keys |
| `data` | Search, advanced search, FITS upload/browse/preview/download |
| `chat` | Agent loop, SSE streaming, sessions CRUD, export actions, **`/api/chat/ai_backend_status`** |
| `pipeline` | DAG validation, sync/async execution, template + version APIs, batch |
| `integration` | ADQL/TAP with radius-halving retry, VOTable, Jupyter export, SAMP |
| `workspace` | File metadata, tags, notes, batch upload/export |
| `export` | Markdown / report / notebook / LaTeX / BibTeX |
| `paper` | Paper draft generation + manuscript download |
| `sessions` | Share tokens, comments, snapshots, forks, diffs |
| `research` | Opt-in memory profile + history |
| `team` | Friends, shared resources, activity feed |
| `ws` | WebSocket relay (presence, pipeline progress, collab channels) |
| `alerts`, `anomalies`, `followup`, `dossier` | Time-domain + anomaly features |
| `citations`, `citation_graph`, `arxiv` | ADS + arXiv search / extract |
| `crossmatch` | Position + probabilistic cross-matching |
| `visualization` | Plotly chart generation |
| `provenance` | IVOA ProvDM lineage export |
| `scheduler` | Scheduled analysis jobs |
| `isochrones` | Cached PARSEC isochrone delivery |
| `inference` | Inference routing, model health, cost tracking |
| `settings` | Encrypted API-key storage |
| `health` | Liveness, `/health/deep` verifies DB + Redis + AI backend |
| `events` | Analytics event ingestion |

### AI layer

- [`app/ai/orchestrator.py`](./backend/app/ai/orchestrator.py) — Intent classification, specialist-context assembly, tool-subset filtering.
- [`app/ai/model_profiles.py`](./backend/app/ai/model_profiles.py) — Manual provider/model registry. Current profiles: Claude default, OpenAI GPT-5.5 alias (falls back to `gpt-5.4` unless `OPENAI_GPT55_MODEL` is set), OpenAI GPT-5.4, DeepSeek V4 Pro, DeepSeek V4 Flash, local OpenAI-compatible backends, and the local-only OpenAI CLI profile.
- [`app/ai/inference_router.py`](./backend/app/ai/inference_router.py) — Calls the user-selected model profile, logs cost/latency/model/fallback metadata, and falls back across backends only after the selected backend fails. The local OpenAI CLI path is enabled only with `OPENAI_CLI_ENABLED=1`; it runs the CLI in ephemeral read-only mode and still returns JSON tool calls to the Standard Astro backend, so network/archive searches, ADQL/database queries, Python analysis, plotting, and provenance checks remain server-side. Raises `InferenceError("No configured AI backends are available…")` on no-key paths (now surfaced pre-send by F4.2).
- `app/ai/agents/*` — Specialist prompt fragments (data, analysis, literature, observation, visualization, spectrum).
- [`app/services/ai_tools.py`](./backend/app/services/ai_tools.py) — **87-tool catalog + executor dispatcher**. Each tool has a literature-cited description and JSON-schema input.
- [`app/api/chat.py`](./backend/app/api/chat.py) — Agent loop (max 12 iterations), ~57 KB / ~14 k-token `SYSTEM_PROMPT` (46 sections), SSE stream with heartbeats, empty-reply fallback synthesis, zero-fabrication gate, structured-abstention parser, and deterministic literature-table / `fit_line_lfr` follow-up for line-relation prompts when the model has found papers or fit-ready measurement caches but skipped the required tool.

#### Tool catalogue (85)

Domain-specific additions include:
- **`query_gaia_cluster`** — Composes Gaia DR3 member-selection ADQL from structured params (center name → Sesame/SIMBAD resolve → parallax + PM + RUWE + G-mag cuts). Keeps SQL out of the LLM's hot path so F2.1 EMPTY banners fire cleanly on 0-row returns.
- **`get_extinction`** — A_V / E(B-V) at a sky position. Primary path SFD98 via `dustmaps.sfd`; exp-disk analytic fallback when `dustmaps` unavailable. Band-specific A_band via Cardelli+ 1989 ratios.

The catalogue is organized through `result_provenance.py` `_DATA_TOOLS` / `_COMPUTE_TOOLS` / `_REFERENCE_TOOLS`:

- **Data tools (18)**: `search_objects`, `run_adql`, `query_high_velocity_stars`, `run_sdss_sql` (maintenance-gated as SDSS), `get_object_info`, `get_object_dossier`, `query_transients`, `search_lightcurve`, `crossmatch_catalogs`, `batch_object_search`, `describe_tap_table`, `query_vo_service`, `get_last_search_results`, `read_fits_header`, `get_provenance`, `query_gaia_cluster`, `get_extinction`, `load_cosmology_data_product`.
- **Compute tools (48)**: `run_python`, `generate_pipeline`, `run_pipeline`, `validate_analysis`, `generate_paper_draft`, `fit_isochrone`, `estimate_photo_z`, `estimate_photo_z_pro`, `analyze_spectrum`, `analyze_spectrum_pro`, `sensitivity_analysis`, `compare_luminosity_distances`, `demagnify_sample`, `prepare_spectral_measurements`, `fit_line_lfr`, `astro_statistics_toolbox`, `fit_cosmology_mcmc`, `run_cobaya_cosmology`, `get_cosmology_run_status`, `build_cosmology_likelihood`, `build_cosmology_robustness_matrix`, `run_cosmology_likelihood_chain`, `run_cosmology_robustness_matrix`, `run_nested_sampler`, `evaluate_chain_diagnostics`, `run_research_matrix`, `fit_transit_model`, `gp_detrend_lightcurve`, `detect_stellar_flares`, `transit_search_bls`, `reduce_ccd_image`, `solve_astrometry`, `extract_photometry`, `extract_sources`, `classify_transient`, `classify_transient_spectrum`, `compute_galaxy_sfr`, `fit_rv_orbit`, `fit_sersic_morphology`, `x_ray_spectral_fit`, `pulsar_derived_quantities`, `analyze_cross_wavelength`, `radio_analysis`, `process_image`, `share_with_team`, `invite_team_member`, `export_results`, `workspace_export`.
- **Reference tools (21)**: `search_literature`, `read_arxiv_paper`, `extract_literature_tables`, `export_sample_table`, `list_cosmology_datasets`, `plan_research_program`, `build_evidence_graph`, `verify_research_facts`, `export_research_report`, `build_paper_mining_candidate_pool`, `mine_paper_tools`, `run_paper_tool_mining_batch`, `run_paper_tool_mining_loop`, `build_tool_ontology`, `build_tool_gap_matrix`, `rank_tool_implementation_queue`, `literature_review`, `research_workflow`, `generate_proposal`, `get_followup_recommendation`, `full_research_report`.

`result_provenance.ALL_KNOWN_TOOLS` is asserted to equal `{t["name"] for t in TOOLS}` in `tests/test_result_provenance.py`; adding a tool without classifying it breaks CI.

### Anti-synthetic-fallback defenses (Phase G core, closes F's gap)

PART F blocked the model from citing numbers not in `tool_results`. PART G
closes the loophole: the AI can still **generate** fake numbers inside a
`run_python` call (which itself succeeds), and then cite those. Four new
layers:

1. **G1 — Data-source contract.** `run_python` tool schema now has a required `data_source` field. Declared values: `latest_adql | latest_search | latest_lightcurve | cached:<key> | fits:<path> | none_not_analyzing_real_data`. Declared real source is validated against the code body (the code must call the matching helper like `get_adql_results()`); mismatch → reject. Declared `none_not_analyzing_real_data` → output is tagged SYNTHETIC.

2. **G2 — AST static analysis.** [`app/services/synthetic_code_detector.py`](./backend/app/services/synthetic_code_detector.py) parses the Python code, flags `np.random.*` + time-axis `np.linspace` + suspicious keyword phrases ("simulate", "based on known parameters", "mock data", "generate realistic X") + var names (`synthetic_*`, `fake_*`). Legitimate random-use contexts are whitelisted (`emcee`, `dynesty`, `arviz`, `bootstrap`, `jackknife`). Verdict `synthetic` + declared real source → reject; `suspicious` → downgrade output to SYNTHETIC.

3. **G3 / G3.4 — Upstream tool-failure tracking + physical tool removal.** [`api/chat.py` `_run_agent_loop`](./backend/app/api/chat.py) maintains a per-turn `tool_failure_counts`. When a data-fetch tool fails ≥ `DISABLE_AFTER_FAILURES=2` times, it is **removed from the `tools` parameter** on the next LLM call — the model literally cannot call it any more. A runtime note in the system prompt for that turn tells the model which tools were disabled and why. Also: when data-fetch failed earlier AND a subsequent `run_python` doesn't declare a real source, its output is tainted SYNTHETIC regardless of what was declared.

4. **G1.5 — Guard reaches the model.** Error-string sanitization (`result_provenance._sanitize_error_message`) replaces instruction-like tokens ("retry", "fallback", "simulate", "narrower parameters") with neutral phrasings before the text is fed back to the LLM, closing the "prompt injection via error strings" vector the reviewer identified. SYSTEM_PROMPT has a dedicated ANTI-INSTRUCTION-REFLECTION section + an explicit rule banning `np.random` fallback after any data-fetch failure. System prompt is always built fresh per request (not cached per session).

**UI visibility** (G4). Action cards with `__tool_status__="SYNTHETIC"` or `data_origin="synthetic"` render with a red border, a full-width "⚠ SYNTHETIC DATA — NOT FROM OBSERVATIONS" header ribbon, and a `⚠ SYNTHETIC` chip. Reply preamble flags synthetic tools separately from failed/empty. `tools_disabled` SSE event surfaces tool-removal to the thinking timeline.

**Sandbox hardening** (G0.3). The "nine consecutive empty responses" pattern the reviewer reported is now instrumented: child writes breadcrumbs (`start exec` / `exec done; fignums=...` / `figure N serialized OK` / `about to send; pickled size=...` / `exit; sent_ok=True`) to stderr for Render log diagnostics. Per-variable repr caps (5000 chars, ndarray/DataFrame → shape+mean summary), per-figure size cap (8 MB base64), total-vars cap (512 KB), stdout truncation with explicit marker, 32 MB total payload ceiling before send with fallback to minimal-error payload. New Prometheus histogram `sandbox_duration_seconds{backend,exit_code}`.

**VizieR catalog registry.** Paper 5 failed with "unresolved identifier RAJ2000" on SDSS `V/147/sdss12`. Registry now contains `V/147/sdss12`, `V/139/sdss9`, `I/355/gaiadr3`, `II/335/galex_ais` with real column lists (SDSS uses `RA_ICRS`/`DE_ICRS`, not `RAJ2000`). `suggest_for_missing_column()` maps common mistakes to hints; `_exec_adql` auto-attaches the hint to TAP error messages.

**Debug endpoint.** `/api/chat/_debug_last_prompt` (gated by env `DEBUG_LAST_PROMPT=1`) returns the last prompt `inference_router.route` received so reviewers can confirm in-browser that the zero-fabrication + anti-reflection rules are actually present in the LLM's context.

### Post-H data access & AI coordination (2026-04-20)

Reviewer cycle on 5 reproduction papers surfaced ~20 real issues. PART H fixes concentrate on the data-access layer rather than AI architecture:

- **ADQL async TAP** (`api/integration.py:467-580`). Queries with `TOP > 5000`, cone radius > 1°, or explicit `JOIN` go directly to `launch_job_async` with a 5-minute budget; small queries get a 60 s sync try that auto-falls back to async on timeout. Fixes Paper 5 "Gaia 50000-row query hits 45s hard timeout".

- **ADQL newline normalization** (`api/integration.py:475`). `req.query.replace("\n"/"\r"/"\t", " ")` + whitespace collapse before TAP dispatch. Fixes Paper 4 "Cannot parse query FROM g" when a pasted newline lands mid-identifier.

- **`search_lightcurve` Quantity bug** (`services/astro_analysis.py:2782`). Replaced `if r.exptime` with `if r.exptime is not None` + safe `.value` extraction. Paper 2 `search_lightcurve(mission="tess")` no longer raises "Quantity truthiness is ambiguous". Added Simbad `SkyCoord.from_name` fallback when the direct MAST search returns 0 results (for alias-resolved exoplanet hosts like HD 189733).

- **TOP auto-degradation** (`services/ai_tools.py:_exec_adql`). After cone-radius degradation fails, detects `TOP >= 10000` and retries once with `TOP min(1000, N/10)`, marking `top_auto_reduced_from` / `top_used` in the result so the AI can tell the user about sample-size reduction.

- **Circuit breaker posture** (`api/chat.py:_run_agent_loop`). `DISABLE_AFTER_FAILURES` raised from 2 → 3. Soft failures (timeout / `payload_too_large` / empty result) no longer count toward the disable threshold; only hard connector errors do. Fixes the Paper 5 "two timeouts → `run_adql` removed from toolkit" dead-end.

- **V/154/sdss17 schema injection** (`services/catalog_registry.py`). Real SDSS DR17 VizieR column list (`ra`/`dec`/`u`/`g`/`r`/`i`/`z`/`objID`/`class`/`zsp`/`zph`) replaces the AI's guesses (`RAJ2000`/`petroMag_r`/`redshift`). `VIZIER_COMMON_MISTAKES` has AI-facing hints for each wrong name. ADQL preset buttons updated to use real columns.

- **System prompt SDSS section** (`api/chat.py` SYSTEM_PROMPT). ADQL Usage Rules document that SDSS has no native ADQL. In the current provenance-v2 rollout, `sdss`, `sdss_spec`, and direct `run_sdss_sql` are maintenance-gated until SDSS emits independent `archive_version` provenance; use the VizieR SDSS mirror for small schema-aware checks.

- **arXiv fallback for Literature Search** (`api/citations.py`). ADS 401/429 or empty response → automatic arXiv API query (Atom XML parse), returns same shape with `source: "arxiv"`. Fixes Paper 4 "Literature Search EMPTY".

- **`data_source` linter AST-based** (`services/ai_tools.py:_exec_run_python`). Old substring match had false positives when AI aliased helpers. New walk catches direct calls, attributes (`astro.get_adql_results()`), imports, and import aliases. Also always accepts `get_cached_results(...)` as a valid signal for any real source.

- **SYNTHETIC workflow** (SYSTEM_PROMPT section). Explicit two-option framework: Option A (abstain) is default; Option B (synthetic demo) only when user explicitly asked for methodology demo, with mandatory reply prefix. Reduces AI's tendency to convert failed real-data requests into unmarked synthetic runs.

- **Retry button non-destructive** (`ChatPage.tsx:3796-3815`). Old code removed the pending message via `setMessages.filter` before `handleSend`, triggering a localStorage flush that ate all prior successful tool_results. New code clears `_pending` + swaps in a "previous attempt timed out" placeholder — history stays intact.

- **First Send click fix** (`ChatPage.tsx:4107`). Send button onClick now passes `input` explicitly (`handleSend(input)`) instead of relying on closure. Eliminates the stale-closure race that caused the first click to silently drop.

- **User message text color** (`styles/journal.css:1102`). `.chat-message.user .chat-message-content` was inheriting `color: #fff` from App.css (paired with burgundy bg in the old theme); Journal overrides the bg to paper-cream but not the color → white-on-cream unreadable. Explicit `color: var(--ink)` fix.

### Render cold-start recovery (post-G fix)

Render free-tier dynos sleep after 15 minutes idle. A user who idled and then fired a request (e.g. Advanced Search) would get a 502/503/504 back from the Render edge while the dyno woke, and the existing `BackendBanner` only checked backend health once at App mount — so mid-session sleeps went unflagged. Fixed:

1. **Axios response interceptor** (`src/api/client.ts`). On any 502/503/504, clears the `astro_backend_checked` sessionStorage flag, dispatches a `window` `CustomEvent('astro:backend-waking')`, waits 5 s, and retries the original request exactly once. Retry loop is guarded by an `__cold_start_retried__` flag on the config so it can't spiral.
2. **BackendBanner listens for the event** (`src/App.tsx`). A second `useEffect` subscribes to `astro:backend-waking` and shows "Waking up backend (Render free tier sleeps after 15 min idle)..." for 12 s regardless of whether the initial boot-time health check had already completed.

This covers the Bug 12 reviewer scenario: Advanced Search returned a 502 after idle; now the request transparently retries and the banner explains the delay.

### Figure persistence (post-G fix)

Reviewer flagged that `run_python`-generated matplotlib figures displayed correctly during a session but vanished on reload or tab switch — `<img>` / base64 / `Expand` / `Download` all gone, leaving only `print` text output. Root cause: `_pruneToolResults` in `ChatPage.tsx` replaced the entire `tool_result` with `{__offloaded__: true}` whenever the `astro_chat_history` localStorage blob exceeded its 4 MB soft cap. A single CMD four-panel figure (~400 KB base64) plus a few supporting plots easily crosses that cap, and figures were always the first victim.

Three-layer fix:

1. **Tiered pruning** (`_pruneToolResults` rewritten). Figures are the last thing stripped. Pass A removes assistant-internal heavy fields (`rows`, `data`, `raw_data`, `results`, `traceback`) that the AI can re-fetch via `get_cached_results`; Pass B removes `variables` / `variable_types`; Pass C removes `stdout`; Pass D (last resort) replaces the `figures` array with a `{__figures_offloaded__: N}` marker carrying the count but not the bytes.
2. **Server rehydrate** (new boot-time `useEffect`). When the restored local copy has any `__offloaded__` or `__figures_offloaded__` markers, and the user is logged in with a `currentSessionId`, ChatPage asynchronously fetches the full session from `/api/chat/sessions/{id}` (the server blob was never pruned — `save_chat_session` writes `session.messages` as-is) and merges `actions` back into matching messages by `(role + content[:200])` prefix key, preserving local-only state like `_abstention` and `_pending`.
3. **UI placeholder** (`AutoToolResult`). When `figures.length === 0` but `__figures_offloaded__ > 0`, renders an amber dashed-border placeholder reading "📊 N figures were generated here but were offloaded from browser cache to save space. Reloading from the server now…". On successful rehydrate this placeholder disappears and the real figures render.

**Known limit**: anonymous users have no server copy; the tiered pruner extends figure survival in localStorage but extreme sessions can still lose figures. Future fix: migrate figure storage to IndexedDB (GB scale) keyed on `(sessionId, messageId, figureIndex)`.

### Zero-fabrication and citation provenance

This is the load-bearing trust layer. Three layers of defence + one positive incentive.

1. **Upstream banners** — [`app/services/result_provenance.py`](./backend/app/services/result_provenance.py).
   - `normalize_tool_result(...)` wraps every tool return with a reproducibility envelope (`run_id`, `tool_version`, `query_hash`, `timestamp_utc`, optional `random_seed` + `archive_version`), plus a **provenance contract** (`data_origin ∈ {real_archive, cached_real, user_uploaded, synthetic, unavailable}`, `analysis_status ∈ {completed, partial, simulated_demo, failed, empty}`).
   - New in F2.1: `_is_empty_payload` detects `row_count==0`, empty `results`/`rows`, run_python with no stdout + no figures + no variables. `_inject_empty_banner` / `_inject_failed_banner` prepend `{__tool_status__, __do_not_claim__, __message_to_model__, __suggested_next_step__}` to the dict so the LLM sees them first when streaming left-to-right. `_suggest_next_step(tool_name, error=...)` is tool-specific.
   - Numeric sanity checks (`numeric_sanity_warnings`) still fire: negative parallax, RA ∉ [0, 360), Dec ∉ [-90, 90], |redshift| absurd, log g ∉ [0, 6.5], negative mass/radius/luminosity, impossible absolute magnitudes.

2. **Claim validator** — [`app/services/claim_validator.py`](./backend/app/services/claim_validator.py).
   - Regex catalogue: `redshift_z`, `redshift_word`, `log_g`, `metallicity`, `e_bv`, `a_v`, `mass_solar`, `luminosity_solar`, `age_gyr`, `age_myr`, `teff_k`, `distance_pc|kpc|mpc`, `period_days`, `parallax_mas`, `proper_motion`, `radial_velocity`, `magnitude`, plus Phase F1 additions: `label_colon` (`Mean Parallax: 7.353`), `count_with_noun` (`776 stars`), `value_with_error` (`7.353 ± 0.001 mas` → extracts both value and error), `ra_dec_pair`.
   - `validate_claims(reply, tool_results)` harvests the numeric universe from `tool_results` recursively and matches each claim at ±1 % (default). F1.3 strict mode: if the universe has < 10 entries, tolerance tightens to 0.1 % to prevent accidental index / row-count matches.
   - `is_empty_turn(tool_results)` + `zero_data_but_quantitative(reply, tool_results)` implement the F1.4 hard block — if every tool this turn is empty/failed and the reply still contains any numeric claim, skip straight to the block path.
   - `blocked_reply_text(...)` renders a user-facing block message that includes the tool-universe snapshot (F1.5) so failures are legible.
   - Provenance-v2 citation checks build a valid bibcode pool from `provenance.datasets[*].article`, `provenance.field_bibcodes`, and literature-search `bibcode` fields. Invalid bibcodes and suspicious author-year citations increment `fabrication_blocked_total{reason="invalid_bibcode"|"suspicious_author_year"}`. Warning mode is the default; `PROVENANCE_VALIDATOR_HARDBLOCK=true` turns citation violations into reply blocks.
   - Literature-derived measurement claims are stricter than paper-context claims: `search_literature` abstracts can support background/citation context, but line-luminosity/FWHM slopes, intercepts, intrinsic scatter, correlations, and sample-fit claims require extracted measurement rows or a publication-ready `fit_line_lfr` result in the current turn.
   - Built-in cosmology-preset manifest bibcodes are not globally citeable. A reply may cite Planck/Riess/Suzuki/etc. preset papers only when a current-turn tool such as `compare_luminosity_distances` or a cosmology-aware fit returned that preset provenance.

3. **Structured abstention** — parser in [`app/api/chat.py`](./backend/app/api/chat.py).
   - System prompt (§ STRUCTURED ABSTENTION) instructs the LLM: when every tool's `__tool_status__` is EMPTY, FAILED, or UNAVAILABLE, the entire reply must be a single `<tools_returned_nothing failed_tools="..." empty_tools="..." rationale="..." suggested_next_step="..."/>` tag.
   - `_parse_abstention_tag` (permissive: self-closing / open-close, single/double quotes, surrounding whitespace; rejects prose before/after), `_classify_abstention_reason` (empty / failed / mixed / no_tools), `_render_abstention_card` emits canonical Markdown — model never generates prose here, so no fabrication pressure.
   - Backend emits an SSE `{"type": "honest_abstention", "payload": {...}}` event that the frontend routes into `DisplayMessage._abstention` → renders `HonestAbstentionCard`.
   - Claim validator is bypassed on this path (there's nothing to validate).

4. **Agent-loop integration**: F1.4 zero-data hard block fires first; otherwise the regeneration loop runs up to 2 attempts, then block with the full universe snapshot. Fallback synthesis for empty LLM replies is now also gated through `validate_claims` (F1.2). Numeric and citation counters under `fabrication_blocked_total{reason=...}` + `fabrication_detected_total{attempt}` + `reply_regeneration_total` track the punishment side; `honest_abstention_total{reason}` + `structured_abstention_emitted_total` track the reward side.

### Data access layer

- `app/connectors/*` — **24 connector keys**. The active provenance-v2 keys are `vizier`, `gaia`, `simbad`, `ned`, `2mass`, and `alma`. The other 18 keys (`sdss`, `sdss_spec`, `mast`, `chandra`, `allwise`, `eso`, `irsa`, `jwst`, `lamost`, `desi`, `panstarrs`, `xmm`, `nvss`, `first`, `jpl`, `atnf_pulsar`, `sparc`, `frbstats`) return an `UNAVAILABLE` maintenance banner before connector import. ALMA is active for Science Archive observation metadata only, not derived line luminosity or FWHM measurements.
- [`app/connectors/registry.py`](./backend/app/connectors/registry.py) — Lazy registry plus availability gate.
- [`app/connectors/availability.py`](./backend/app/connectors/availability.py) — `V2_AVAILABLE_CONNECTORS`, maintenance response builder, and `connector_gated_total{connector_name}` metric hook.
- [`app/services/source_mapping.py`](./backend/app/services/source_mapping.py) — machine-readable mapping status for active archive connectors, gated connectors, verified literature-table seeds, and cosmology registry progress. Human-facing status lives in [`docs/SOURCE_MAPPING.md`](./docs/SOURCE_MAPPING.md).
- [`app/services/provenance_v2/*`](./backend/app/services/provenance_v2) — Fallback registry, freshness checks, field-level schema/extractor, and DataOrigin/PARAM/INFO resolver helpers. Startup blocks on stale registry entries.
- [`app/api/arxiv.py`](./backend/app/api/arxiv.py) + `extract_literature_tables` — arXiv/ar5iv/LaTeX table extraction path. Raw tables carry table/caption/row provenance, and normalized line-measurement rows carry citation metadata so downstream `fit_line_lfr` can support relation statistics without relying on model memory.
- [`app/services/spectral_measurement_workbench.py`](./backend/app/services/spectral_measurement_workbench.py) — generic spectral-line measurement validator/inventory for `[CII]`, CO, Halpha, Lyalpha, [OIII], and future line tables. The `prepare_spectral_measurements` tool reports fit-ready rows, missing fields, line inventory, citation counts, and value ranges before relation fitting.
- [`app/services/cosmology_likelihoods.py`](./backend/app/services/cosmology_likelihoods.py) — Observational-cosmology dataset registry plus controlled Cobaya/CosmoSIS-style config builder and phase-1 compressed Gaussian runner. Current registry covers DESI DR1 BAO, SDSS+6dF/SDSS-BOSS/eBOSS BAO, Pantheon+, DES-SN5YR, Union3, Planck compressed priors, ACT DR6 lensing, KiDS-1000/DES Y3/HSC weak-lensing comparison branches, cosmic chronometers, and SH0ES H0 prior. DESI, Pantheon+, and Planck entries expose explicit `data_products` for public mean vectors, covariance matrices, likelihood code, or compressed-prior tables. Config outputs remain non-citeable; compressed posterior/tension numbers are citeable only when `run_cosmology_likelihood_chain` or `run_cosmology_robustness_matrix` returns `publication_ready=true`, and must be labeled as compressed-likelihood preliminary rather than full external likelihood results.
- [`app/connectors/throttle.py`](./backend/app/connectors/throttle.py) — Per-connector `asyncio.Semaphore` + stdlib token bucket; per-archive ToS defaults (Gaia 5 req/s & 2 concurrent, SDSS 2 req/s, VizieR 10 req/s, SIMBAD 10 req/s, MAST 5 req/s & 2 concurrent, …). Raises `ThrottleTimeout` on sustained overflow.
- [`app/connectors/retry.py`](./backend/app/connectors/retry.py) — Transient-only retry set (`httpx.TimeoutException`, `httpx.ConnectError`, `ConnectionError`, `TimeoutError`) + circuit breaker with closed/half-open/open states; `circuit_breaker_open_total` + `connector_error_total` counters.
- [`app/services/connector_cache.py`](./backend/app/services/connector_cache.py) — Content-addressed cache keyed on `sha256(connector + endpoint + sorted(params))`. Backends: `RedisBackend` → `SQLiteBackend` → `NullBackend` (auto-select). Tiered TTLs: 24 h metadata, 1 h cones, 15 min ADQL. Singleflight dedup via a module-level `set[asyncio.Task]` with `task.add_done_callback(_tasks.discard)` so GC can't drop the shared future.

### Analysis layer

- [`app/services/astro_analysis.py`](./backend/app/services/astro_analysis.py) — 60+ exposed helpers: distance/DM conversions, `fit_isochrone` (PARSEC CMD 3.9 + turnoff fallback with Bressan+2012 calibration), `wd_cooling_age` (Bédard+2020 interpolation), `bss_select` (Rain+2021), CCM89 extinction, IRSA dust lookup, Lomb-Scargle + phase folding, Voigt / multi-Gauss fitting, `plot_hr_diagram` accepting parallax OR distance, `target_visibility`, `exposure_time_estimate`, bootstrap/MC error propagation.
- [`app/services/spectral_analysis_pro.py`](./backend/app/services/spectral_analysis_pro.py) — NIST line ID, heliocentric correction, IFU kinematics.
- [`app/services/photo_z_pro.py`](./backend/app/services/photo_z_pro.py) — 30 SED templates + Calzetti dust + Madau IGM + Bayesian priors.
- [`app/services/bayesian_inference.py`](./backend/app/services/bayesian_inference.py) — ArviZ-based ESS / R-hat / HDI / WAIC / LOO; `mcmc_insufficient_sampling_total` counter flags `ess_bulk<400` or `rhat>1.05`.
- [`app/services/astro_statistics.py`](./backend/app/services/astro_statistics.py) — deterministic statistics toolbox for robust summaries, OLS/weighted/ODR/Theil-Sen regression, bootstrap intervals, and descriptive upper-limit/censored-data summaries. This gives the agent a named statistics path before falling back to custom `run_python`.
- [`app/services/cosmology_mcmc.py`](./backend/app/services/cosmology_mcmc.py) — typed distance-modulus cosmology MCMC for `flat_lcdm`, `flat_wcdm`, and `flat_w0wa_cdm`; emcee runs synchronously for small cached-data jobs, inline rows are audit-only, Cobaya is a controlled UNAVAILABLE phase-1 interface until posterior summarization lands, and posterior numbers are citeable only when `publication_ready=true`.
- [`app/services/time_domain_pro.py`](./backend/app/services/time_domain_pro.py) — GP detrending, `BoxLeastSquares` + bootstrap FAP, flare detection, transit fitting with covariance matrix.
- [`app/services/image_processing_pro.py`](./backend/app/services/image_processing_pro.py) — Reproject, mosaic, PSF match, deblend, cutouts.
- [`app/services/transient_classifier.py`](./backend/app/services/transient_classifier.py) — Random-forest light-curve classifier + template spectral matching.

### Python sandbox

- [`app/services/sandbox/subprocess_backend.py`](./backend/app/services/sandbox/subprocess_backend.py) — `multiprocessing` spawn child, `resource.setrlimit` (RLIMIT_AS/CPU/NPROC), `os.setsid` + `os.killpg(SIGKILL)` on timeout. Blocked builtins: `exec`, `eval`, `compile`, `open`. Seg-faults, OOM, infinite loops cannot take down the FastAPI worker.
- **Phase F0 hardening** — payload-completeness guard: if the child dies mid-serialisation (`parent_conn.recv()` returns `None`, `{}`, non-dict, or `success=False` with no error), the backend now returns an explicit error message describing the failure mode + exit code; the child writes breadcrumbs (`conn.send success/failure`, `exit` line) to its stderr so Render logs can diagnose. `_exec_run_python` in `ai_tools.py` has an error-field tripwire: any failure carries both `error` and an `error_class` chip (`sandbox_crash`, `oom`, `timeout`, `name_error`, `import_error`, `syntax_error`, …). `sandbox_silent_failure_total` fires when the synthesized-error path is hit.
- [`app/services/code_executor.py`](./backend/app/services/code_executor.py) — In-process fallback with session-scoped variables; TTL sweep evicts entries > 2 h old when the registry exceeds 64 sessions. `ALLOWED_MODULES` whitelist includes numpy / scipy / astropy / specutils / photutils / dynesty / arviz / celerite2 / batman / matplotlib / pandas / dask / pyvo / sklearn / sherpa / radvel / thejoker / galpy / pysme / statmorph / vorbin / ppxf / astroquery / dustmaps / healpy / lenstronomy / MulensModel / treecorr / yt / pint / psrqpy / skimage.

### Pipeline layer

- [`app/pipeline/engine.py`](./backend/app/pipeline/engine.py) — DAG validation, topological execution, Redis caching, sync fallback, Celery entrypoint, per-node provenance with the environment manifest.
- [`app/pipeline/nodes/__init__.py`](./backend/app/pipeline/nodes/__init__.py) — **`NODE_COST` registry + `dag_has_heavy_nodes()`**. 17 heavy nodes (`BayesianFit`, `TransitFit`, `GPDetrend`, `PhotoZPro`, `SEDFit`, `ImageStack`, `Mosaic`, `PSFMatch`, `Deblend`, `CosmicRayReject`, `SourceExtract`, `PSFPhotometry`, `AstrometricSolve`, `SpectraStack`, `TelluricCorrect`, `TimeSeriesAnalysis`, `CustomScript`); `/api/pipeline/run` returns 503 if heavy nodes present and `PIPELINE_MODE != "celery"`.
- **35 node types** by family:
  - Data input: QueryData, ImportWorkspace, LoadData
  - CCD reduction: BiasSubtract, DarkCorrect, FlatField, CosmicRayReject
  - Astrometry / photometry: AstrometricSolve, SourceExtract, PSFPhotometry, PhotCalibrate, ImageStack
  - Image processing: Reproject, Mosaic, PSFMatch, Deblend
  - Spectroscopy: Denoise, SpectralFit, RedshiftEstimate, EquivalentWidth, FluxCalibrate, TelluricCorrect, SpectraStack
  - Time-domain: TimeSeriesAnalysis, GPDetrend, TransitFit
  - Statistical inference: BayesianFit, PhotoZPro, SEDFit, CrossMatch
  - Transforms: CoordTransform, Condition
  - Custom: CustomScript
  - Output: Plot, InteractivePlot

### Collaboration + memory

- [`app/api/sessions.py`](./backend/app/api/sessions.py) — Share tokens, comments, snapshots, forks, diffs.
- [`app/services/memory_service.py`](./backend/app/services/memory_service.py) — Opt-in research profile + hashed-embedding store.
- [`app/api/team.py`](./backend/app/api/team.py) — Friends, shared resources, activity feed.
- [`app/api/ws.py`](./backend/app/api/ws.py) — WebSocket relay for presence / pipeline progress / collab channels (auth-gated after PART C).

### Observability

- [`app/observability/metrics.py`](./backend/app/observability/metrics.py) — Stdlib-only Prometheus-compatible registry (no `prometheus-client` dep). Thread-safe counters + histograms; `GET /metrics` emits OpenMetrics text.
- **Counter inventory** (production currently emits):
  - Connector: `connector_requests_total{source}`, `connector_error_total{connector,source,kind}`, `circuit_breaker_open_total{connector}`, `astro_object_invalid_total`.
  - Fabrication gate: `fabrication_detected_total{agent,attempt}`, `fabrication_blocked_total{agent,reason}`, `reply_regeneration_total{agent}`, `zero_data_but_claims_total{agent,claim_count}`. Citation reasons include `invalid_bibcode` and `suspicious_author_year`.
  - Provenance rollout: `connector_gated_total{connector_name}`.
  - Honest path: `honest_abstention_total{agent,reason}`, `structured_abstention_emitted_total{agent}`.
  - Tool health: `empty_tool_result_total{tool}`, `empty_tool_call_total{tool}`, `sandbox_silent_failure_total{tool}`.
  - Science quality: `sanity_warning_total{tool}`, `mcmc_insufficient_sampling_total`.
- [`app/services/workflow_checkpoint.py`](./backend/app/services/workflow_checkpoint.py) — Resumable-workflow store (32-step cap, 2 h TTL). Wiring into chat.py is a follow-up.
- [`app/services/provenance.py`](./backend/app/services/provenance.py) — Versioned environment manifest (Python version, platform, pinned packages + SHA-256 fingerprint, system-prompt hash) merged into every recorded activity.

## 4. Persistence

Core entities (see `app/models/schemas.py`):

- `User` — Auth + Fernet-encrypted API keys + subscription tier.
- `DataFile` — FITS/VOTable/CSV metadata, user-scoped.
- `PipelineRun`, `RunResult`, `PipelineTemplateDB`, `PipelineVersion` — Pipeline lineage.
- `ChatSession` — Chat history; indexed on `(user_id, created_at)`.
- `PaperDraft` — Generated paper drafts in structured JSON. Owner-scoped by `user_id`; `is_public` + `public_token` are required before a read-only draft is exposed at `/papers/public/:token`.
- `SharedSession`, `SessionFork`, `SessionComment`, `SessionSnapshot` — Collaboration. Shared sessions include only paper drafts that have been explicitly published; private drafts stay account-only.
- `UserResearchProfile`, `SessionEmbedding` — Opt-in memory.
- `UserEvent`, `InferenceLog` — Analytics + cost.
- Tables for alerts, anomalies, teams, setup keys, schedules.

SQLite (dev) portability via custom `UUIDType` + `JSONType`. Alembic-managed migrations (2 versions tracked); runtime `_migrate_add_columns` smooths over SQLite's inability to add columns via `create_all()`.

## 5. Runtime flows

### Search

1. `/api/data/search` or `/advanced-search` → connectors dispatch concurrently with per-source timeouts.
2. Normalize via `_astro_to_result()` + `_safe_float()` (masked astropy → None, never NaN).
3. Full results cache under `"latest"` key; AI retrieves via `get_last_search_results`.

### ADQL

1. User or `run_adql` → `execute_adql_query` (standalone, not route-only).
2. 408/502/503 → radius halved, then quartered.
3. Full result (≤2000 rows) cached under `"latest_adql"`; AI sees first 100 rows + note; downstream Python gets the full set via `get_cached_results('latest_adql')`.

### AI chat

1. SSE POST `/api/chat/message/stream` with messages + context (`python_session_id`, `current_session_id`, last search / ADQL result set / uploaded FITS, etc.).
2. Runtime = `SYSTEM_PROMPT` (57 KB, 46 sections) + specialist-agent fragments + filtered tool list.
3. `inference_router.route(...)` receives the validated manual `model_profile` from chat context, then enters the tool loop (max 12 iterations). Per-tool deadlines: `fit_isochrone` 180 s, `fit_transit_model`/`transit_search_bls` 120 s, `estimate_photo_z_pro` 90 s, rest 45 s. Agent-loop outer 360 s; connection heartbeats every 12 s to defeat proxy idle-kill.
4. Tool returns flow through `normalize_tool_result` → `__tool_status__` banner + reproducibility envelope + nested provenance + sanity warnings.
5. Final reply goes through:
   1. `_parse_abstention_tag` → if `<tools_returned_nothing/>` → render card, emit SSE, return.
   2. `zero_data_but_quantitative(reply, tool_results)` → if empty turn + numeric claim → hard block.
   3. `validate_claims(...)` with `strict_when_empty`, plus provenance citation validation → up to 2 regeneration attempts for numeric failures; citation violations warn by default and hard-block only when configured.
   4. Fallback synthesis (empty LLM reply) — also validated.
6. SSE events: `text` (final reply), `agent_text` (live thinking), `tool_call`, `tool_result` (`live: true` during loop + final consolidated), `status` (heartbeats), `honest_abstention`, `error`, `done`.
7. Auto-save after each turn; auto-title from first user message; F4 pre-send gate blocks `Send` when no AI backend is configured.

### Pipeline

1. User edits DAG in React Flow. **Auto Layout** = Kahn longest-path.
2. POST `/api/pipeline/run`. If heavy + `PIPELINE_MODE != celery` → 503.
3. Celery worker (default) or sync thread executor (dev/test) executes; Redis cache keyed on content hash; per-node provenance with environment manifest.

### Collaboration

Session share tokens → read / comment / fork URLs. Snapshots serialise current state for point-in-time restore + `difflib` diff. Presence via WebSocket (`/api/ws`, auth required).

## 6. AI knowledge base (`SYSTEM_PROMPT`)

~57 KB / ~14 k tokens / **46 sections**. Highlights (top-of-prompt first):

1. **DATA RELEASE PINS** — Gaia DR3, SDSS DR18, 2MASS PSC, etc. Never mix releases silently.
2. **Data provenance reporting** — field-level bibcodes first, table-level dataset article second, registry fallback last; no invented bibcodes or memorized author-year substitutes.
3. **ZERO-FABRICATION CONTRACT** — ±1 % tool-cited rule, now extended to cardinalities ("N stars").
4. **STRUCTURED ABSTENTION** — when all tools this turn are EMPTY/FAILED/UNAVAILABLE, the entire reply must be a single `<tools_returned_nothing.../>` tag; the system renders a card. Inventing prose is penalised.
5. **Literature-table / line-relation workflow** — `search_literature` is context only; measurement statistics require `extract_literature_tables` / cited line measurements / `fit_line_lfr`.
6. **Cosmology workflow** — distance/modulus/fitting claims must use a declared cosmology preset or typed tool output; prompt wording like Riess+2011/Suzuki+2012 maps to `FlatLambdaCDM_H73p8_Om0p295`.
7. **ADQL aggregate-function semantics (F7.1)** — STDDEV/VAR are population, not sample; σ/√N for SEM.
8. **Cluster / association idioms (F7.2)** — prefer `query_gaia_cluster` + `get_extinction` over hand-written SQL; on EMPTY emit abstention instead of inventing member counts.
9-46. Domain workflows: database decision tree, Gaia column completeness + specialised tables, GSP-Phot quality flags, extinction routing (SFD for low-E(B-V)), open / globular cluster, variable star (RR Lyrae / Cepheid / EB), distance hierarchy, spectroscopic catalog choice, dust maps (SFD / Bayestar / Green / Marshall), X-ray spectral fitting (Sherpa, HI4PI, Wilms abundances), SFR estimators (K&E 2012), RV orbit fitting, rotation curves (SPARC + galpy), stellar atmospheres (ATLAS9/MARCS/PHOENIX), galaxy morphology (Sérsic / galfit / statmorph), IMF, cluster virial scaling, pulsars (YMW16 DM, Lorimer & Kramer), WD cooling (Bédard+2020), brown dwarfs (Kirkpatrick 2005), IFU kinematics (Voronoi + pPXF), AGN SED decomposition (CIGALE, Vestergaard-Peterson BH mass), Galactic streams (GD-1, Sgr, Gaia-Enceladus), solar system (JPL Horizons), specialised-domain references (FRB, GW, lensing, BAO, CMB, N-body, microlensing, chemical evolution, adaptive optics, VLBI), data-integrity rules (no simulated data), pipeline DAG generation, action JSON format, SIMBAD basic-table columns.

Every formula/constant is cited to author + year + journal + page. A prior audit removed LLM-hallucinated values (e.g. corrected Gaia `A_G/A_V=0.789` per Wang & Chen 2019; rewrote `wd_cooling_age` as 13-point Bédard+2020 interpolation).

## 7. External integrations

### Astronomy archives

- **Active provenance-v2 sources**: VizieR, Gaia DR3, SIMBAD, NED, 2MASS, ALMA Science Archive observation metadata.
- **Maintenance-gated connector keys**: SDSS, SDSS spectra, MAST, Chandra, AllWISE, ESO, IRSA, JWST, LAMOST, DESI, Pan-STARRS, XMM-Newton, NVSS, FIRST, JPL Horizons, ATNF PSRCAT, SPARC, FRBSTATS.
- Gated sources return `UNAVAILABLE` / Maintenance rather than FAILED/EMPTY and do not execute legacy query code.
- ALMA is not a line-measurement source in v2. Derived `[CII]` luminosity, line flux, and FWHM values must come from cited literature tables or future dedicated measurement tools.

### Other

- NASA ADS + arXiv for literature.
- astrometry.net for WCS solving.
- IRSA dust map service.
- Anthropic / OpenAI / DeepSeek (LLM inference).
- PARSEC CMD 3.9 for live isochrones (with turnoff-table fallback).
- Optional: Redis + Celery.

## 8. Deployment

### Dev

Uvicorn `--reload` + Vite dev server + SQLite + local files + no Redis (sync pipeline mode; heavy nodes rejected).

### Prod (Render blueprint `render.yaml`)

Six services:

1. `standard-astro-backend` — FastAPI, `/health/deep` healthcheck.
2. `standard-astro-celery-worker` — concurrency=2.
3. `standard-astro-celery-beat` — scheduler.
4. `standard-astro-frontend` — Vite build, SPA rewrites.
5. `standard-astro-redis` — cache + queue + pub/sub (allkeys-lru).
6. `standard-astro-db` — PostgreSQL.

`backend/Dockerfile` is Python 3.11-slim; installs gfortran, libpq, fontconfig, fonts-noto-cjk (CJK matplotlib). Non-root `app:app` user. Live URLs: `astro-backend-h4x1.onrender.com`, `astro-frontend-tyfr.onrender.com`.

### Auto-deploy

Push to `main` → Render auto-deploy. Render free tier sleeps after 15 min idle — the frontend `BackendBanner` component shows a "waking up" notice on first load.

## 9. Current constraints

- Python sandbox is **stability-hardened**, not adversarial-grade. `seccomp` / `gVisor` / `Firecracker` are out of scope.
- Connector cache + upstream throttle are opt-in at the call site; migration to every connector is incremental.
- Orchestrator still runs one tool-loop per turn; multi-agent execution is prepared but not yet the production path.
- Opt-in research memory uses hashed embeddings, not a vector DB.
- ADQL cache stores full result sets; the AI sees 100 rows; Python gets the rest via the cache key.
- System prompt is ~14 k tokens. Further growth will require a jump-to section index.
- API keys live in browser `localStorage` in beta mode; F4 gates the Send button but a full per-user session-storage migration is still backlog (PART C M9).

## 10. Testing

- **Backend**: pytest suite under `backend/tests/`. Major modules include `test_api`, `test_claim_validator`, `test_citation_validation`, `test_b7_regression`, `test_cosmology_mcmc`, `test_abstention_parser`, `test_sandbox_crash_paths`, `test_sandbox_isolation`, `test_result_provenance`, `test_connector_availability_gate`, `test_provenance_registry_loader`, `test_provenance_v2_connectors`, `test_connector_cache`, `test_connector_throttle`, `test_router_golden`, `test_workflow_checkpoint`, `test_environment_manifest`, `test_metrics`, and e2e smoke tests. Golden-path fixtures live under `backend/tests/golden/`.
- **Frontend**: **150 vitest cases pass**. Coverage includes ChatPage, DataSourcesPanel, CosmologyMCMCPanel, AckButton, SearchBar maintenance-gating, ActionCard, PlotBuilder, DataBrowser, ADQLPage, FITSBrowser, ProvenanceGraph, common utilities. TypeScript strict `tsc -b` is a required pre-push gate.
- **CI**: GitHub Actions runs backend pytest + frontend `tsc + vite build + vitest` + ruff lint on every push.
- **Physical-regression targets** (manual): NGC 1647 (open cluster, Frasca+2026), M53 (globular + RR Lyrae), Tom 2 blue stragglers (Rain+2021), Vel OB1, white dwarf LF, Pleiades IMF, NGC 752 isochrone age ∈ [1.2, 2.0] Gyr.

## 11. Physics formula provenance

Every formula in the codebase either:

1. Cites a specific published paper (author + year + journal + page) in comments, or
2. Wraps a widely-used package (sherpa, galpy, radvel, pysme, statmorph, arviz, dustmaps, …) and the package handles the physics.

A full audit corrected Gaia `A_G/A_V=0.789` (Wang & Chen 2019), removed an incorrect `+0.2` bolometric correction, fixed the MS-vs-RGB ridge slope in `_estimate_age_from_turnoff`, annotated the empirical `+0.3 mag` binary bias as empirical, rewrote `wd_cooling_age` against Bédard+2020 tables (validated on Crab), and refreshed `chain_diagnostics` for the current ArviZ API. Subsequent F1 / F2 work layered claim-time validation + upstream banner enforcement on top, so any regression would now be caught by the zero-fabrication gate before reaching the user.
