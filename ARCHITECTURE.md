# Standard Astro Architecture

**Current as of 2026-08-07: cosmology-only scope; Provenance v2; research-mode and claim-gate hardening; dark-launched lightweight scalar verification; evidence receipts; and durable posterior-chain exports.** Reflects the actual checked-in code, not an aspirational roadmap. Update when modules, flows, or deployment assumptions materially change.

> **Scope.** This repository is **cosmology-only**. The sole active prompt module is `cosmology`; `ASTRO_RESEARCH_FOCUS` defaults to `cosmology` and any value other than `all` fails closed to it. The solar-system / exoplanet prompt modules and the 12 dormant prompt modules were extracted to `standard-astro-verticals` on 2026-06-03, so `backend/app/prompts/modules/` now contains only `cosmology/`. Their **tool implementations** still live in `backend/app/services/` but are invisible to the LLM because they are outside the cosmology manifest allowlist (81-tool catalog − 61 visible = 20 gated, live-imported 2026-08-07). Counts below come from `scripts/stats.sh` or the equivalent backend-venv live import — run it after any structural change.

## 1. System Shape

Standard Astro is a full-stack astronomy research platform with four runtime layers:

1. **Frontend SPA** — React 19 + TypeScript (strict) served by Vite. The current route table has 15 directly rendered screen families, including AI Chat, Claim Audit, Workflow Foundry, Research Workspace, Bot Console, Papers, Observations, Team, Account, and shared sessions; legacy Settings, Alert, and Anomaly URLs redirect to their surviving parents. The visual Data Browser, Pipeline Studio, and ADQL pages remain removed; the pipeline DAG engine still exists backend-side.

2. **FastAPI backend** — Single process, 44 domain-router modules (measured 2026-08-07; use `scripts/stats.sh` for the live count). SSE streaming on the chat path, long-poll + WebSocket for collaboration, background workers for pipeline execution.

3. **Execution + storage** — PostgreSQL (prod) / SQLite (dev) for metadata; local filesystem for FITS; Redis for content-addressed connector cache + Celery queue; Celery worker + beat for heavy pipelines. Arbitrary `run_python` execution is disabled by default and prohibited in hosted production until it runs in a separate OS-isolated environment with no application secrets or tenant mounts.

4. **External services** — 23 astronomy connector keys, with 6 provenance-v2 active sources (`vizier`, `gaia`, `simbad`, `ned`, `2mass`, `alma`) and 17 maintenance-gated sources; NASA ADS / arXiv, astrometry.net, IRSA dust maps, PARSEC isochrones, and routed LLM backends (Claude / OpenAI / DeepSeek / local). ALMA is active for Science Archive observation metadata, not derived line luminosity/FWHM measurements.

Users move between chat → analysis → export → paper without losing context. The chat assistant bridges the module through its **81-tool catalog** (§3; live-imported 2026-08-07). The cosmology manifest allowlists **61 tools** and the remaining 20 catalog entries — dormant-vertical implementations retained in code — are physically invisible to the LLM. With the dark-launched v0.2 flag off, `api/chat.py` also removes `verify_scalar_derivation`, leaving **60 tools wire-visible by default**.

### Runtime topology

```text
Browser SPA (React/Vite)
  ├─ REST: auth, workspace, data, papers, admin, settings
  ├─ SSE: /api/chat/message/stream streaming assistant turns
  └─ WebSocket: collaboration, presence, long-running progress

FastAPI web process
  ├─ Router layer: auth/data/chat/pipeline/export/paper/admin/...
  ├─ AI layer: orchestrator → inference_router → selected model backend
  ├─ Tool layer: ai_tools dispatcher → connectors / analysis services / sandbox
  ├─ Guardrail layer: task router → source/receipt verifier → claim/citation gates
  └─ Persistence layer: SQLAlchemy metadata + filesystem artifacts + cache

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
   figures, evidence receipts, and acknowledgement controls.

**Lightweight scalar source check (dark-launched v0.2)**

1. With `LIGHTWEIGHT_VERIFICATION_ENABLED=1`, `prompt_routing.py` classifies a
   bounded paper-table calculation as `deterministic_source_check`. Explicit
   requests to run a chain, likelihood, fit, sampler, posterior, or arbitrary
   expression stay on the heavy/general path; negated mentions do not force an
   upgrade.
2. A high-confidence parse can inject the single controlled
   `verify_scalar_derivation` call. If the model fills a partially specified
   call, the echo guard requires the operation, each quantity/value/error/unit,
   source identity, locator, and covariance statement to remain bound to the
   user's text before execution.
3. `scalar_derivation.py` validates arity, finite inputs, unit compatibility,
   covariance symmetry/diagonal/positive-semidefiniteness, and supported
   operation semantics. It uses analytic Jacobian propagation; it never runs
   generated code.
4. `source_packet_resolver.py` normalizes and retrieves bounded arXiv, DOI,
   Zenodo, or public-HTTPS material. Locator-scoped matching binds complete
   labels, values, uncertainties, correlations, table/row regions, and source
   identities. Missing, ambiguous, reordered, rounded/lossy, or conflicting
   evidence cannot become `verified_exact`.
5. The result separately records `calculation_status` and `source_status`, then
   chooses `full` or a useful `limited` response. Input errors abstain; pasted
   fake transcripts can produce a refusal receipt. Every scalar receipt gets a
   canonical SHA-256 hash and remains explicitly non-publication-ready.

**Archive/data query**

1. UI or AI tool selects a connector key.
2. `connectors.availability` blocks non-v2 keys before importing legacy code.
3. Active connectors query the upstream archive through retry/cache
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

> The solar-system and exoplanet module workflows that previously lived here were
> extracted to `standard-astro-verticals` on 2026-06-03 along with their tool
> implementations and prompt modules. See that repo for their documentation.

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

Entrypoint: [`src/App.tsx`](./frontend/src/App.tsx). Routes are declared here; the two-row **journal-masthead** holds the primary nav plus a chip-style 4-language switcher (EN / 中文 / FR / ES), theme toggle, and user menu. The M3 trim (2026-05-18) removed the Data Browser / Pipeline / ADQL pages and their nav tabs. `/settings`, `/alerts`, and `/anomalies` now redirect to surviving parents; `/research` is active again as the Research Workspace surface.

### Pages and route families

| Page | Purpose |
|---|---|
| `Landing` | Journal-style hero + stat strip + TOC grid + editorial rail |
| `Chat` | AI assistant with persistent sidebar (claude.ai style); thinking timeline, action cards, honest-abstention bubble — the primary surface |
| `ClaimAudit` | Dark-launched claim/evidence audit workflow |
| `Foundry` | Dark-launched candidate-workflow review and activation surface |
| `Research`, `ResearchWorkspace` | Research programs and account-scoped workspaces |
| `Bot` | Loopback-only local automation console; unavailable in hosted production |
| `Papers` | Account-scoped LaTeX manuscript drafts; drafts are private by default and can be explicitly published as read-only links |
| `Observations` | Transient feed, alerts, anomalies, follow-up recommendations |
| `Team` | Friends, shared datasets, activity feed, comments |
| `Account` | Profile, keys (Fernet-encrypted), opt-in memory; legacy `/settings` redirects here |
| `SharedSession` | Tokenized read / comment / fork view of any saved chat session |
| `Auth`, `Help`, `Privacy` | Login / register, onboarding docs, and privacy controls |

### Shared infrastructure

- [`src/api/client.ts`](./frontend/src/api/client.ts) — Axios + typed SSE streaming. `ThinkingEvent` union covers `agent_text` / `tool_call` / `tool_result` / `status` / **`honest_abstention`** / `error`. `getAIBackendStatus()` feeds the F4 pre-send gate.
- [`src/context/AuthContext.tsx`](./frontend/src/context/AuthContext.tsx) — JWT lifecycle; logout only on 401/403, not transient errors.
- [`src/components/viz/*`](./frontend/src/components/viz) — PlotBuilder (Plotly publication-grade; Fit checkbox now shows ✓ / "(not supported)" per chart type) and AladinViewer. (SpectrumViewer / LightCurveViewer / ImageCutoutViewer / MCMCDiagnostics were removed 2026-06-11 — dead code orphaned from every route by the M3 page trim.)
- [`src/components/chat/*`](./frontend/src/components/chat) — MarkdownText, chat sidebar, figure-expand modal, DataSourcesPanel, AckButton, CosmologyMCMCPanel, CosmologyLikelihoodPanel, `ScalarVerificationReceiptCard`, and the general `EvidenceReceiptCard`.
- [`src/i18n/index.tsx`](./frontend/src/i18n/index.tsx) — 4-language flat dictionary; ~200+ keys.
- [`src/styles/journal.css`](./frontend/src/styles/journal.css) — 2 k-line Journal-Edition stylesheet overriding chat / pipeline / browse / ADQL / sessions / account to the newspaper palette; loaded **after** `App.css` so same-specificity rules win the cascade.

### Chat UI specifics

- **`HonestAbstentionCard`** (`ChatPage.tsx`) renders the pale-blue ✓ bubble when the SSE `honest_abstention` event arrives. Shows failed/empty tool list, model's rationale, suggested next step, and a "Try it" button that prefills the chat input.
- **`AutoToolResult` status chips** — action card switches left border and badge based on `__tool_status__` / `analysis_status` / `success` / `error` from the provenance envelope. FAILED remains red, EMPTY remains amber, UNAVAILABLE renders as a separate Maintenance state, and SYNTHETIC keeps the loud synthetic warning.
- **`DataSourcesPanel` + `AckButton`** — tool results with nested provenance expose service name, `archive_version`, ivoid, bibcode/article, authority cues, field-bibcode counts, and a copyable acknowledgement template.
- **`ScalarVerificationReceiptCard`** — renders the controlled result, propagated uncertainty, formula, source status, response disposition, boundary statement, and receipt hash without merging source verification into arithmetic success.
- **`EvidenceReceiptCard`** — renders dataset-coverage, capability-gap, untrusted-transcript, and other validation receipts as first-class evidence instead of burying them in reply prose.
- **`.chat-reply-failure-preamble`** — collapsible ⚠ strip above a prose reply when any tool that turn failed/empty, preserving the validation signal for happy-path replies.
- **`ActionCard`** is memoized (`React.memo`) keyed on `reproducibility.run_id`, so streaming SSE events don't remount earlier cards and invalidate refs.
- **Pending marker** — an assistant bubble with `_pending: { started_at }` renders a spinner; after 60 s it offers Retry; reconciled against `getChatSession` on page reload.
- **F4 no-LLM gate** — Send button is disabled when neither browser-stored keys nor server-side backends are configured; red banner links to `/account`.

### TypeScript constraints

Strict build (`tsc -b && vite build`) is non-negotiable: `strict`, `noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax`, `erasableSyntaxOnly`. Types must be imported with `import type`. The frontend Vitest suite runs on every push — `npm run test` for the live count.

## 3. Backend Architecture

Entrypoint: [`backend/app/main.py`](./backend/app/main.py). FastAPI app factory + middleware stack (CORS, rate limit, event tracking, observability); migrates any missing columns at startup via `_migrate_add_columns` (SQLite/PG safe).

### Modular focus gating (M1/M2, 2026-05-18)

Standard Astro ships as a focus-gated prompt + tool catalog. After the 2026-06-03 cosmology-only refactor there is exactly one active module, but the gating machinery is unchanged so a vertical can be promoted back in by adding its module directory.

- **`backend/app/prompts/`** has a three-layer structure: `base.md` (always loaded) + `core/*.md` (cross-cutting rules — provenance, citation hierarchy, ZERO-FABRICATION CONTRACT, STRUCTURED ABSTENTION, ADQL semantics) + `modules/<name>/{manifest.yaml, prompt.md, appendix.md}`. Manifests declare `status: active|dormant` and the per-module tool allowlist.
- **`backend/app/services/prompt_loader.py`** assembles the runtime `SYSTEM_PROMPT` and the tool allowlist per the `ASTRO_RESEARCH_FOCUS` env var (default `cosmology`). `build_system_prompt(focus)` returns base + core + the active module's prompt; `build_allowed_tools(focus)` returns the manifest tool set. The loader still recognizes the `_dormant_<name>` directory convention, but none are checked in now.
- **Active modules** (1): `cosmology` — full BAO/SN/CMB/lensing workflow, blind-tested across 50+ paper-derived cases. The `solar_system` and `exoplanet` active modules and the 12 dormant prompt modules (`agn`, `galaxy_morphology`, `high_z_galaxy`, `image_reduction`, `paper_export`, `paper_tool_mining`, `pipeline_dag`, `pulsar_timing`, `radio`, `stellar`, `team_workspace`, `xray_spectroscopy`) were extracted to `standard-astro-verticals` on 2026-06-03; `modules/` now contains only `cosmology/`. Their tool *implementations* remain under `backend/app/services/` and are hidden by the focus gate.
- **L1 hard tool gating** lives in `api/chat.py` `_filter_tools_by_research_focus`: foci in `_FOCUS_GATED_VALUES = {"cosmology"}` filter the tool list before it reaches the LLM, and any focus other than `all` fails closed to cosmology. The cosmology manifest allowlists 61 of the 81 catalog tools (live import, 2026-08-07); the other 20 are dormant-vertical implementations the agent loop can never call under this build. When v0.2 is disabled, the same filter removes `verify_scalar_derivation` before the schema reaches the model, restoring a 60-tool wire surface.

### API domains (44 router modules — measured 2026-08-07)

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
| `health` | Liveness; `/health/deep` verifies Alembic head, durable storage, Redis and a live Celery worker |
| `events` | Analytics event ingestion |

### AI layer

- [`app/ai/orchestrator.py`](./backend/app/ai/orchestrator.py) — Intent classification, specialist-context assembly, tool-subset filtering.
- [`app/ai/model_profiles.py`](./backend/app/ai/model_profiles.py) — Manual provider/model registry. Current profiles: Claude default, OpenAI GPT-5.5 alias (falls back to `gpt-5.4` unless `OPENAI_GPT55_MODEL` is set), OpenAI GPT-5.4, DeepSeek V4 Pro, DeepSeek V4 Flash, local OpenAI-compatible HTTP, and local OpenAI/Codex CLI.
- [`app/ai/inference_router.py`](./backend/app/ai/inference_router.py) — Calls the user-selected model profile, logs cost/latency/model/fallback metadata, and falls back across backends only after the selected backend fails. Backends are `claude` / `openai` / `deepseek` / `local`; `_backend_is_available` checks the matching API key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY`) or strict local enablement. The `local` backend supports OpenAI-compatible HTTP (`LOCAL_MODEL_ENABLED=1`), an ephemeral Codex JSON bridge (`OPENAI_CLI_ENABLED=1`, `local:openai-cli`), and an ephemeral Claude Code JSON bridge (`CLAUDE_CLI_ENABLED=1`, `local:claude-cli`). Both CLI children receive a secret-minimized environment and are unavailable in production; Claude disables built-in tools/settings/session, while Codex ignores config/rules and uses its read-only sandbox. Standard Astro validates requested tool names before executing platform tools. Raises `InferenceError("No configured AI backends are available…")` when no backend is configured (surfaced pre-send by F4.2).
- `app/ai/agents/*` — Specialist prompt fragments (data, analysis, literature, observation, visualization, spectrum).
- [`app/services/ai_tools/`](./backend/app/services/ai_tools) — **81-tool catalog + executor dispatcher** (live import, 2026-08-07), organised as a package (`__init__.py` re-exports the public `TOOLS` list, `execute_tool`, and helper imports so call sites are unchanged). Each tool has a literature-cited description and JSON-schema input. The cosmology manifest contains **61 tools**; the default dark-launch filter exposes 60 until v0.2 is enabled. The other 20 entries are dormant-vertical implementations retained in code but never exposed under this build.
- [`app/services/prompt_loader.py`](./backend/app/services/prompt_loader.py) — Three-layer SYSTEM_PROMPT assembler (`base.md` + `core/*.md` + `modules/<focus>/prompt.md`) plus per-focus tool allowlist builder; cached via `lru_cache`.
- [`app/api/chat.py`](./backend/app/api/chat.py) — Agent loop (max 12 iterations), focus-aware `SYSTEM_PROMPT` built via `prompt_loader.build_system_prompt(_ASTRO_RESEARCH_FOCUS)` (cosmology focus: ~100 KB / ~26 k tokens as of 2026-07-03 — run `scripts/stats.sh` for the live numbers), SSE stream with heartbeats, empty-reply fallback synthesis, zero-fabrication gate, structured-abstention parser, and deterministic literature-table / `fit_line_lfr` follow-up for line-relation prompts when the model has found papers or fit-ready measurement caches but skipped the required tool.
- [`app/services/scalar_derivation.py`](./backend/app/services/scalar_derivation.py) — deterministic operation, unit, covariance, Jacobian, and canonical-receipt-hash kernel.
- [`app/services/source_packet_resolver.py`](./backend/app/services/source_packet_resolver.py) — bounded source adapters, locator-scoped exact matching, cache identity, SSRF/DNS-rebinding defenses, and explicit degraded source states.
- [`app/services/chain_export.py`](./backend/app/services/chain_export.py) — atomic getdist-format chain rendering and persistence with honest in-process/external-Cobaya metadata.

#### Tool catalogue (81 — live import 2026-08-07; 61 cosmology-allowlisted, 60 wire-visible by default)

Domain-specific additions include:
- **`verify_scalar_derivation`** — Controlled ratio, difference, product, or inverse-covariance weighted-mean derivation with explicit uncertainty and source verification. Singular covariance matrices are rejected. The tool never substitutes for a likelihood, fit, sampler, or posterior.
- **`query_gaia_cluster`** — Composes Gaia DR3 member-selection ADQL from structured params (center name → Sesame/SIMBAD resolve → parallax + PM + RUWE + G-mag cuts). Keeps SQL out of the LLM's hot path so F2.1 EMPTY banners fire cleanly on 0-row returns.
- **`get_extinction`** — A_V / E(B-V) at a sky position. Primary path SFD98 via `dustmaps.sfd`; exp-disk analytic fallback when `dustmaps` unavailable. Band-specific A_band via Cardelli+ 1989 ratios.

The catalogue is organized through `result_provenance.py` `_DATA_TOOLS` / `_COMPUTE_TOOLS` / `_REFERENCE_TOOLS`. Live names are sourced from `ai_tools.TOOLS`; what follows is a grouped snapshot rather than a hand-maintained checklist.

Highlights by group:

- **Archive / data query** — `search_objects`, `run_adql`, `get_object_info`, `get_object_dossier`, `query_transients`, `search_lightcurve`, `crossmatch_catalogs`, `batch_object_search`, `describe_tap_table`, `query_vo_service`, `get_last_search_results`, `read_fits_header`, `get_provenance`, `query_gaia_cluster`, `get_extinction`, `load_cosmology_data_product`. `run_sdss_sql` and `query_high_velocity_stars` remain registered but route through the maintenance-gated SDSS path.
- **Cosmology** — `list_cosmology_datasets`, `build_cosmology_likelihood`, `build_cosmology_robustness_matrix`, `run_cosmology_likelihood_chain`, `run_cosmology_robustness_matrix`, `fit_cosmology_mcmc`, `run_cobaya_cosmology`, `get_cosmology_run_status`, `run_nested_sampler`, `evaluate_chain_diagnostics`, `compare_luminosity_distances`, `sensitivity_analysis`.
- **Spectral measurement + line relations** — `prepare_spectral_measurements`, `extract_literature_tables`, `fit_line_lfr`, `export_sample_table`, `demagnify_sample`, `analyze_spectrum`, `analyze_spectrum_pro`.
- **Generic compute + sandbox** — `run_python`, `astro_statistics_toolbox`, `validate_analysis`, `fit_isochrone`, `estimate_photo_z` / `estimate_photo_z_pro`, time-domain tools (`fit_transit_model`, `gp_detrend_lightcurve`, `detect_stellar_flares`, `transit_search_bls`), image-reduction tools (`reduce_ccd_image`, `solve_astrometry`, `extract_photometry`, `extract_sources`, `process_image`), transient + galaxy + RV + morphology + X-ray + pulsar + radio tools.
- **Pipeline / workspace / export** — `generate_pipeline`, `run_pipeline`, `generate_paper_draft`, `share_with_team`, `invite_team_member`, `export_results`, `workspace_export`.
- **Research / mining / literature** — `search_literature`, `read_arxiv_paper`, `plan_research_program`, `build_evidence_graph`, `verify_research_facts`, `export_research_report`, `run_research_matrix`, `build_paper_mining_candidate_pool`, `mine_paper_tools`, `run_paper_tool_mining_batch`, `run_paper_tool_mining_loop`, `build_tool_ontology`, `build_tool_gap_matrix`, `rank_tool_implementation_queue`, `literature_review`, `research_workflow`, `generate_proposal`, `get_followup_recommendation`, `full_research_report`.

`result_provenance.ALL_KNOWN_TOOLS` is asserted to equal `{t["name"] for t in TOOLS}` in `tests/test_result_provenance.py`; adding a tool without classifying it breaks CI.

### Anti-synthetic-fallback defenses (Phase G core, closes F's gap)

PART F blocked the model from citing numbers not in `tool_results`. PART G
closes the loophole: the AI can still **generate** fake numbers inside a
`run_python` call (which itself succeeds), and then cite those. Four new
layers:

1. **G1 — Data-source contract.** `run_python` tool schema now has a required `data_source` field. Declared values: `latest_adql | latest_search | latest_lightcurve | cached:<key> | fits:<path> | user_file:<path> | none_not_analyzing_real_data`. Declared real source is validated (AST walk) against the code body (the code must call the matching helper like `get_adql_results()`); mismatch → reject. `user_file:<path>` auto-classifies a genuine `pd.read_csv`/`pd.read_parquet`/`load_csv`; `cached:<key>` is rejected when the key is not live. Declared `none_not_analyzing_real_data` → output is tagged SYNTHETIC.

2. **G2 — AST static analysis.** [`app/services/synthetic_code_detector.py`](./backend/app/services/synthetic_code_detector.py) parses the Python code, flags RNG calls (`np.random.*` / `scipy.stats` / stdlib `random` / `torch`·`jax`·`tf` / `getattr(np, "random")` dynamic access) + time-axis builders (`np.linspace` / `np.arange` / `pd.date_range`) + suspicious keyword phrases ("simulate", "based on known parameters", "mock data", "generate realistic X") + var names (`synthetic_*`, `fake_*`). Legitimate random-use contexts are whitelisted (`emcee`, `dynesty`, `arviz`, `bootstrap`, `jackknife`). Verdict `synthetic` + declared real source → reject; `suspicious` → downgrade output to SYNTHETIC.

3. **G3 / G3.4 — Upstream tool-failure tracking + physical tool removal.** [`api/chat.py` `_run_agent_loop`](./backend/app/api/chat.py) maintains a per-turn `tool_failure_counts`. When a data-fetch tool fails ≥ `DISABLE_AFTER_FAILURES=3` times (hard connector errors only; see the circuit-breaker note below), it is **removed from the `tools` parameter** on the next LLM call — the model literally cannot call it any more. A runtime note in the system prompt for that turn tells the model which tools were disabled and why. Also: when data-fetch failed earlier AND a subsequent `run_python` doesn't declare a real source, its output is tainted SYNTHETIC regardless of what was declared.

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

Historical context (written when the backend ran on Render's free tier,
whose dynos slept after 15 minutes idle; the backend now runs on a paid
Standard instance that does not sleep, and the mechanism is kept as defense
against any transient 502/503/504): a user who idled and then fired a request
would get a 502/503/504 back from the Render edge, and the existing
`BackendBanner` only checked backend health once at App mount — so mid-session
errors went unflagged. Fixed:

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
   - Provenance-v2 citation checks build a valid bibcode pool from `provenance.datasets[*].article`, `provenance.field_bibcodes`, and literature-search `bibcode` fields. Invalid bibcodes and suspicious author-year citations increment `fabrication_blocked_total{reason="invalid_bibcode"|"suspicious_author_year"}`. Hard-block mode is the default; only an explicit `PROVENANCE_VALIDATOR_HARDBLOCK=false` downgrades citation violations to warnings.
   - Literature-derived measurement claims are stricter than paper-context claims: `search_literature` abstracts can support background/citation context, but line-luminosity/FWHM slopes, intercepts, intrinsic scatter, correlations, and sample-fit claims require extracted measurement rows or a publication-ready `fit_line_lfr` result in the current turn.
   - Built-in cosmology-preset manifest bibcodes are not globally citeable. A reply may cite Planck/Riess/Suzuki/etc. preset papers only when a current-turn tool such as `compare_luminosity_distances` or a cosmology-aware fit returned that preset provenance.

3. **Structured abstention** — parser in [`app/api/chat.py`](./backend/app/api/chat.py).
   - System prompt (§ STRUCTURED ABSTENTION) instructs the LLM: when every tool's `__tool_status__` is EMPTY, FAILED, or UNAVAILABLE, the entire reply must be a single `<tools_returned_nothing failed_tools="..." empty_tools="..." rationale="..." suggested_next_step="..."/>` tag.
   - `_parse_abstention_tag` (permissive: self-closing / open-close, single/double quotes, surrounding whitespace; rejects prose before/after), `_classify_abstention_reason` (empty / failed / mixed / no_tools), `_render_abstention_card` emits canonical Markdown — model never generates prose here, so no fabrication pressure.
   - Backend emits an SSE `{"type": "honest_abstention", "payload": {...}}` event that the frontend routes into `DisplayMessage._abstention` → renders `HonestAbstentionCard`.
   - Claim validator is bypassed on this path (there's nothing to validate).

4. **Agent-loop integration**: F1.4 zero-data hard block fires first; otherwise the regeneration loop runs up to 2 attempts, then block with the full universe snapshot. Fallback synthesis for empty LLM replies is now also gated through `validate_claims` (F1.2). Numeric and citation counters under `fabrication_blocked_total{reason=...}` + `fabrication_detected_total{attempt}` + `reply_regeneration_total` track the punishment side; `honest_abstention_total{reason}` + `structured_abstention_emitted_total` track the reward side.

### Data access layer

- `app/connectors/*` — **23 connector keys**. The active provenance-v2 keys are `vizier`, `gaia`, `simbad`, `ned`, `2mass`, and `alma`. The other 17 keys (`sdss`, `sdss_spec`, `mast`, `chandra`, `allwise`, `eso`, `irsa`, `jwst`, `lamost`, `desi`, `panstarrs`, `xmm`, `nvss`, `first`, `atnf_pulsar`, `sparc`, `frbstats`) return an `UNAVAILABLE` maintenance banner before connector import. ALMA is active for Science Archive observation metadata only, not derived line luminosity or FWHM measurements. (The solar-system `jpl` / `mpc` and exoplanet `nasa_exoplanet_archive` connectors were extracted to `standard-astro-verticals` on 2026-06-03.)
- [`app/connectors/registry.py`](./backend/app/connectors/registry.py) — Lazy registry plus availability gate.
- [`app/connectors/availability.py`](./backend/app/connectors/availability.py) — `V2_AVAILABLE_CONNECTORS`, maintenance response builder, and `connector_gated_total{connector_name}` metric hook.
- [`app/services/source_mapping.py`](./backend/app/services/source_mapping.py) — machine-readable mapping status for active archive connectors, gated connectors, verified literature-table seeds, and cosmology registry progress. Human-facing status lives in [`docs/SOURCE_MAPPING.md`](./docs/SOURCE_MAPPING.md).
- [`app/services/provenance_v2/*`](./backend/app/services/provenance_v2) — Fallback registry, freshness checks, field-level schema/extractor, and DataOrigin/PARAM/INFO resolver helpers. Startup blocks on stale registry entries.
- [`app/api/arxiv.py`](./backend/app/api/arxiv.py) + `extract_literature_tables` — arXiv/ar5iv/LaTeX table extraction path. Raw tables carry table/caption/row provenance, and normalized line-measurement rows carry citation metadata so downstream `fit_line_lfr` can support relation statistics without relying on model memory.
- [`app/services/spectral_measurement_workbench.py`](./backend/app/services/spectral_measurement_workbench.py) — generic spectral-line measurement validator/inventory for `[CII]`, CO, Halpha, Lyalpha, [OIII], and future line tables. The `prepare_spectral_measurements` tool reports fit-ready rows, missing fields, line inventory, citation counts, and value ranges before relation fitting.
- [`app/services/cosmology_likelihoods/`](./backend/app/services/cosmology_likelihoods) — Observational-cosmology dataset registry (34 clean entries, audited 2026-08-07) plus controlled Cobaya/CosmoSIS-style config builders and in-process likelihood runners. Far past the original phase-1 compressed description: released, sha256-pinned data products now execute in-process for DESI DR1/DR2 BAO, 6dFGS+MGS (non-Gaussian table), BOSS DR12 consensus, eBOSS DR16 LRG/QSO FSBAO + ELG/Lya non-Gaussian grids, cosmic chronometers (incl. Moresco-2020 full covariance), eBOSS fsigma8, Union3's full 22-bin vector, and the env-gated full SN vectors (Pantheon+ 1701, DES-SN5YR 1829, Pantheon 2018 1048 — offset-marginalized); the clik-free Planck 2018 CMB suite (plik_lite/lowl TT/lowl EE/lensing) dispatches to external Cobaya behind `EXTERNAL_COBAYA_ENABLED`, where mnu/omegak are genuinely sampled (the in-process path hard-refuses ok_*/*_mnu names). Config outputs remain non-citeable; chain numbers are citeable only with `publication_ready=true` and the matching `claim_scope` (compressed participation → compressed-likelihood preliminary; exclusively full-fidelity products → `executable_full_fidelity_likelihoods`). Overlapping samples carry reciprocal `do_not_combine_with` pairs and violating combinations block.
- [`app/connectors/retry.py`](./backend/app/connectors/retry.py) — Transient-only retry set (`httpx.TimeoutException`, `httpx.ConnectError`, `ConnectionError`, `TimeoutError`) + circuit breaker with closed/half-open/open states; `circuit_breaker_open_total` + `connector_error_total` counters.
- [`app/services/connector_cache.py`](./backend/app/services/connector_cache.py) — Connector-payload cache under caller-supplied keys (wired on the arXiv table-extraction path). Backends: `RedisBackend` → `SQLiteBackend` → `NullBackend` (auto-select). Tiered TTLs: 24 h metadata, 1 h cones, 15 min ADQL. Singleflight dedup via a module-level `set[asyncio.Task]` with `task.add_done_callback(_tasks.discard)` so GC can't drop the shared future.

### Analysis layer

- [`app/services/astro_analysis.py`](./backend/app/services/astro_analysis.py) — 60+ exposed helpers: distance/DM conversions, `fit_isochrone` (PARSEC CMD 3.9 + turnoff fallback with Bressan+2012 calibration), `wd_cooling_age` (Bédard+2020 interpolation), `bss_select` (Rain+2021), CCM89 extinction, IRSA dust lookup, Lomb-Scargle + phase folding, Voigt / multi-Gauss fitting, `plot_hr_diagram` accepting parallax OR distance, `target_visibility`, `exposure_time_estimate`, bootstrap/MC error propagation.
- [`app/services/spectral_analysis_pro.py`](./backend/app/services/spectral_analysis_pro.py) — NIST line ID, heliocentric correction, IFU kinematics.
- [`app/services/photo_z_pro.py`](./backend/app/services/photo_z_pro.py) — 30 SED templates + Calzetti dust + Madau IGM + Bayesian priors.
- [`app/services/bayesian_inference.py`](./backend/app/services/bayesian_inference.py) — ArviZ-based ESS / R-hat / HDI / WAIC / LOO; `mcmc_insufficient_sampling_total` counter flags `ess_bulk<400` or `rhat>1.05`.
- [`app/services/astro_statistics.py`](./backend/app/services/astro_statistics.py) — deterministic statistics toolbox for robust summaries, OLS/weighted/ODR/Theil-Sen regression, bootstrap intervals, and descriptive upper-limit/censored-data summaries. This gives the agent a named statistics path before falling back to custom `run_python`.
- [`app/services/cosmology_mcmc.py`](./backend/app/services/cosmology_mcmc.py) — typed distance-modulus cosmology MCMC for `flat_lcdm`, `flat_wcdm`, and `flat_w0wa_cdm`; emcee runs synchronously for small cached-data jobs, inline rows are audit-only, the standalone `run_cobaya_cosmology` tool remains a controlled UNAVAILABLE placeholder (the real external-Cobaya path lives inside `run_cosmology_likelihood_chain` behind `EXTERNAL_COBAYA_ENABLED`, see cosmology_likelihoods), curvature uses the exact FLRW sinn distance and massive neutrinos the non-relativistic fold-in (ok_*/*_mnu models), and posterior numbers are citeable only when `publication_ready=true`.
- [`app/services/time_domain_pro.py`](./backend/app/services/time_domain_pro.py) — GP detrending, `BoxLeastSquares` + bootstrap FAP, flare detection, transit fitting with covariance matrix.
- [`app/services/image_processing_pro.py`](./backend/app/services/image_processing_pro.py) — Reproject, mosaic, PSF match, deblend, cutouts.
- [`app/services/transient_classifier.py`](./backend/app/services/transient_classifier.py) — Random-forest light-curve classifier + template spectral matching.

### Python sandbox

- [`app/services/sandbox/subprocess_backend.py`](./backend/app/services/sandbox/subprocess_backend.py) — trusted-local opt-in for crash containment only: `multiprocessing` spawn child, `resource.setrlimit` (RLIMIT_AS/CPU/NPROC), `os.setsid` + `os.killpg(SIGKILL)` on timeout. Filtering `exec`, `eval`, `compile`, and `open` does not create a security boundary; Python imports/object graphs can still reach the shared host. The backend is disabled by default and production configuration rejects it until execution moves to a separate no-secrets/no-tenant-mounts OS sandbox.
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
2. Runtime = focus-aware `SYSTEM_PROMPT` (size and section count: see §6) + specialist-agent fragments + filtered tool list (81 catalog tools → 61 in the cosmology manifest → 60 wire-visible with v0.2 off, measured 2026-08-07).
3. When v0.2 is enabled, `prompt_routing` assigns `deterministic_source_check`, `research_exploration`, `full_research`, or `general`. The orchestrator collapses deterministic checks to one agent loop so the outer specialist merge cannot duplicate work or discard a valid receipt.
4. `inference_router.route(...)` receives the validated manual `model_profile` from chat context, then enters the tool loop (max 12 iterations). Per-tool deadlines: `fit_isochrone` 180 s, `fit_transit_model`/`transit_search_bls` 120 s, `estimate_photo_z_pro` 90 s, rest 45 s. Agent-loop outer 360 s; connection heartbeats every 12 s to defeat proxy idle-kill.
5. Tool returns flow through `normalize_tool_result` → `__tool_status__` banner + reproducibility envelope + nested provenance + sanity warnings. Scalar and general evidence receipts stay attached to the validation summary.
6. Final reply goes through:
   1. `_parse_abstention_tag` → if `<tools_returned_nothing/>` → render card, emit SSE, return.
   2. `zero_data_but_quantitative(reply, tool_results)` → if empty turn + numeric claim → hard block.
   3. `validate_claims(...)` with `strict_when_empty`, plus provenance citation validation → up to 2 regeneration attempts for numeric failures; citation violations hard-block by default and become warn-only only when `PROVENANCE_VALIDATOR_HARDBLOCK=false` is set explicitly.
   4. Fallback synthesis (empty LLM reply) — also validated.
7. SSE events: `text` (final reply), `agent_text` (live thinking), `tool_call`, `tool_result` (`live: true` during loop + final consolidated), `status` (heartbeats), `honest_abstention`, `error`, `done`.
8. Auto-save after each turn; auto-title from first user message; F4 pre-send gate blocks `Send` when no AI backend is configured.

### Pipeline (backend / API only)

The visual Pipeline Studio frontend was removed in the M3 trim; the DAG engine and `pipeline` API routes remain for AI-generated (`generate_pipeline`) and programmatic DAGs.

1. A DAG is submitted to `POST /api/pipeline/run`. If heavy + `PIPELINE_MODE != celery` → 503.
2. Celery worker (default) or sync thread executor (dev/test) executes; Redis cache keyed on content hash; per-node provenance with environment manifest.

### Collaboration

Session share tokens → read / comment / fork URLs. Snapshots serialise current state for point-in-time restore + `difflib` diff. Presence via WebSocket (`/api/ws`, auth required).

## 6. AI knowledge base (`SYSTEM_PROMPT`)

Focus-aware. Cosmology focus assembles to **~100 KB / ~26 k tokens** (as of 2026-07-03; run `scripts/stats.sh` for the live size and section count — precise numbers here rot). The prompt is built per request from `backend/app/prompts/` by `services/prompt_loader.build_system_prompt(focus)`:

```
prompts/
  base.md                          # always loaded — voice, response format, top-of-prompt invariants
  core/*.md                        # cross-cutting rules: provenance hierarchy, ZERO-FABRICATION CONTRACT,
                                   # STRUCTURED ABSTENTION, ADQL aggregate semantics, claim validator interface, …
  modules/cosmology/prompt.md      # cosmology-focus workflow + literature-table + line-relation rules
  modules/cosmology/appendix.md    # the only module directory in this repo after the 2026-06-03 cosmology-only refactor
```

Cross-cutting invariants present under every focus:

1. **DATA RELEASE PINS** — Gaia DR3, SDSS DR18, 2MASS PSC, etc. Never mix releases silently.
2. **Data provenance reporting** — field-level bibcodes first, table-level dataset article second, registry fallback last; no invented bibcodes or memorized author-year substitutes.
3. **ZERO-FABRICATION CONTRACT** — ±1 % tool-cited rule, extended to cardinalities ("N stars" / "N close approaches").
4. **STRUCTURED ABSTENTION** — when all tools this turn are EMPTY/FAILED/UNAVAILABLE, the entire reply must be a single `<tools_returned_nothing.../>` tag; the system renders a card. Inventing prose is penalised.
5. **ADQL aggregate-function semantics** — STDDEV/VAR are population, not sample; σ/√N for SEM.

Focus-specific top-of-prompt sections:

- **Cosmology focus** (the only active focus) — literature-table / line-relation workflow (`search_literature` is context only; measurement statistics require `extract_literature_tables` / `fit_line_lfr`); cosmology workflow (distance/modulus claims must use a declared cosmology preset or typed tool output; Riess+2011/Suzuki+2012 wording maps to `FlatLambdaCDM_H73p8_Om0p295`); research mode (`plan_research_program` → `run_research_matrix` → `build_evidence_graph` → `verify_research_facts` → `export_research_report`); cluster / association idioms (prefer `query_gaia_cluster` + `get_extinction` over hand-written SQL); plus the retained domain workflows and specialised-domain references (FRB, GW, lensing, BAO, CMB, N-body, microlensing, chemical evolution, AO, VLBI), data-integrity rules, action JSON format, SIMBAD basic-table columns.

Claim-bearing formulas and constants are required to carry a source or a clearly labelled preliminary approximation. A prior audit removed LLM-hallucinated values (e.g. corrected Gaia `A_G/A_V=0.789` per Wang & Chen 2019; rewrote `wd_cooling_age` as 13-point Bédard+2020 interpolation), while the scientific remediation ledger and tests enforce the currently audited set rather than claiming exhaustive proof over all future code.

## 7. External integrations

### Astronomy archives

- **Active provenance-v2 sources**: VizieR, Gaia DR3, SIMBAD, NED, 2MASS, ALMA Science Archive observation metadata.
- **Maintenance-gated connector keys**: SDSS, SDSS spectra, MAST, Chandra, AllWISE, ESO, IRSA, JWST, LAMOST, DESI, Pan-STARRS, XMM-Newton, NVSS, FIRST, ATNF PSRCAT, SPARC, FRBSTATS.
- Gated sources return `UNAVAILABLE` / Maintenance rather than FAILED/EMPTY and do not execute legacy query code.
- ALMA is not a line-measurement source in v2. Derived `[CII]` luminosity, line flux, and FWHM values must come from cited literature tables or future dedicated measurement tools.
- The solar-system (JPL Horizons / IAU MPC / SBDB / Sentry-II / DAMIT) and exoplanet (NASA Exoplanet Archive, TESS / TIC v8) archive integrations were extracted to `standard-astro-verticals` on 2026-06-03.

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

Five deployed services plus one database:

1. `standard-astro-backend` — FastAPI, `/health/deep` healthcheck, paid
   **Standard** instance, S3-compatible research storage and `/app/data` disk.
2. `standard-astro-frontend` — Vite build, SPA rewrites.
3. `standard-astro-db` — PostgreSQL (basic-256mb).
4. `standard-astro-redis` — persistent Render Key Value Celery broker.
5. `standard-astro-celery-worker` — heavy task execution.
6. `standard-astro-celery-beat` — scheduled-task dispatch.

`backend/Dockerfile` is Python 3.11-slim; installs gfortran, libpq, fontconfig, fonts-noto-cjk (CJK matplotlib). Non-root `app:app` user. Live URLs: `astro-backend-h4x1.onrender.com`, `astro-frontend-tyfr.onrender.com`.

### Auto-deploy

Push to `main` → Render deploy after linked CI checks pass (2026-06-03 positioning:
the hosted deployment is NOT the current focus; local + GitHub Actions is the
primary environment, and deploy health is not the success criterion for a
change). The backend runs on a paid Standard instance that does not sleep;
the `BackendBanner` cold-start recovery path remains as defense against
transient 502/503s.

## 9. Current constraints

- Python sandbox is **stability-hardened**, not adversarial-grade. `seccomp` / `gVisor` / `Firecracker` are out of scope.
- Connector cache is opt-in at the call site; migration to every connector is incremental.
- Orchestrator still runs one tool-loop per turn; multi-agent execution is prepared but not yet the production path.
- Opt-in research memory uses hashed embeddings, not a vector DB.
- ADQL cache stores full result sets; the AI sees 100 rows; Python gets the rest via the cache key.
- System prompt is ~26 k tokens under cosmology focus (`scripts/stats.sh` for the live number). Further per-module growth will require a jump-to section index.
- Exactly 1 prompt module (`cosmology`) is active and it is the only module directory in the repo. Re-introducing a vertical means adding its module directory back (from `standard-astro-verticals`), populating its manifest tools list, and adding the focus literal to `_FOCUS_GATED_VALUES` in `api/chat.py`. The 20 retained dormant-tool implementations under `services/` stay hidden until a manifest allowlists them.
- Lightweight scalar verification is dark-launched and disabled by default. Its `verified_exact` state proves a locator-scoped label/value match, not scientific-method validity, and every receipt remains non-publication-ready. The 2026-08-06 evaluation artifacts predate the final ten review-hardening rounds, so the exact live demo prompts must be rerun on the current code before an expert demonstration or Alpha claim.
- `backend/app/services/source_mapping.py` is hand-maintained alongside `connectors/availability.py`. Both are synchronized on the same 6 active / 17 gated keys; the consistency is enforced by `backend/tests/test_source_mapping.py`, which asserts `set(ACTIVE_ARCHIVE_MAPPINGS keys) == V2_AVAILABLE_CONNECTORS` and that the gated set equals `CONNECTORS_KEYS - V2_AVAILABLE_CONNECTORS`. When promoting a new connector to v2 you must edit both files in the same change.
- API keys live in browser `localStorage` in beta mode; F4 gates the Send button but a full per-user session-storage migration is still backlog (PART C M9).
- **Final replies are English-only.** The zero-fabrication numeric/citation regex gate ships English patterns only, so a non-English (CJK) final reply would bypass claim extraction. A non-English draft now triggers one English regeneration (numbers/citations preserved) before the hard block, but the working language is still English — non-English prompts get English answers, not localized prose.
- **Citations must be backed by a current-turn tool result.** Every bibcode / arXiv ID / DOI / author-year in the final prose is checked against the bibcodes the turn's tools actually returned. A genuinely real classic reference is still withheld if no tool this turn surfaced its bibcode — the gate cannot distinguish a correct-from-memory citation from a fabricated one.

## 10. Testing

- **Backend**: pytest suite under `backend/tests/`. Major modules include `test_api`, `test_claim_validator`, `test_citation_validation`, `test_b7_regression`, `test_cosmology_mcmc`, `test_abstention_parser`, `test_sandbox_crash_paths`, `test_sandbox_isolation`, `test_result_provenance`, `test_connector_availability_gate`, `test_provenance_registry_loader`, `test_provenance_v2_connectors`, `test_connector_cache`, `test_router_golden`, `test_workflow_checkpoint`, `test_environment_manifest`, and `test_metrics`. The v0.2 contract is pinned by `test_scalar_derivation`, `test_scalar_verification_tool`, `test_source_packet_resolver`, `test_lightweight_task_routing`, `test_lightweight_agent_loop`, `test_evidence_receipts`, and `test_chain_export`. Golden-path fixtures live under `backend/tests/golden/`.
- **Frontend**: Vitest suite (`npm run test` for the live count). Coverage includes ChatPage, DataSourcesPanel, CosmologyMCMCPanel, AckButton, ActionCard, PlotBuilder, and common utilities. TypeScript strict `tsc -b` is a required pre-push gate.
- **CI**: GitHub Actions (`.github/workflows/ci.yml`) runs backend pytest + frontend `tsc` / `eslint` / `vitest` / `vite build` + backend ruff lint on every push and PR.
- **Physical-regression targets** (manual): NGC 1647 (open cluster, Frasca+2026), M53 (globular + RR Lyrae), Tom 2 blue stragglers (Rain+2021), Vel OB1, white dwarf LF, Pleiades IMF, NGC 752 isochrone age ∈ [1.2, 2.0] Gyr.

## 11. Physics formula provenance

The contract for claim-bearing formulas is that each one either:

1. Cites a specific published paper (author + year + journal + page) in comments, or
2. Wraps a widely-used package (sherpa, galpy, radvel, pysme, statmorph, arviz, dustmaps, …) and the package handles the physics.

Planning heuristics and pedagogical approximations must instead be labelled preliminary and non-publication; the remediation ledger records the enforced exceptions and tests. This is an engineering policy, not a claim that source review can prove every expression scientifically correct.

A full audit corrected Gaia `A_G/A_V=0.789` (Wang & Chen 2019), removed an incorrect `+0.2` bolometric correction, fixed the MS-vs-RGB ridge slope in `_estimate_age_from_turnoff`, annotated the empirical `+0.3 mag` binary bias as empirical, rewrote `wd_cooling_age` against Bédard+2020 tables (validated on Crab), and refreshed `chain_diagnostics` for the current ArviZ API. Subsequent F1 / F2 work layered claim-time validation + upstream banner enforcement on top. Those gates block the tested fabrication classes; they are not proof that every future scientific regression is detectable, so publication still requires the explicit readiness gates and independent domain review.
