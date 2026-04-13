# Standard Astro Architecture

## 1. System Shape

Standard Astro is a full-stack astronomy platform with four main runtime layers:

1. `Frontend SPA`
   React + TypeScript application that hosts Data Browser, Chat, Pipeline, Workspace, ADQL, Alerts, Anomaly Explorer, Research History, and shared-session pages.

2. `FastAPI application`
   Single backend process exposing domain routers for auth, data, AI chat, pipelines, exports, collaboration, research memory, alerts, and admin analytics.

3. `Execution + storage services`
   SQL database for metadata, object/file storage for FITS and exports, optional Redis + Celery for async pipelines and background execution.

4. `External astronomy and LLM services`
   Archive connectors, ADS/arXiv, astrometry.net, and routed LLM backends.

The platform is designed so users can move between search, chat, pipeline, workspace, and export without leaving the same data context.

## 2. Frontend Architecture

Main entrypoint: [App.tsx](/Users/chenkexuan/.openclaw/workspace/astro-platform/frontend/src/App.tsx)

### Top-level pages

- `Data Browser`
  Multi-source search UI, FITS preview, result actions, object detail flows.
- `Chat`
  AI assistant, export actions, session save/load, sharing, snapshots, collaboration controls.
- `Pipeline`
  React Flow canvas, node palette, parameter editor, template/version management, execution progress.
- `Workspace`
  User files and cross-module asset reuse.
- `ADQL`
  Raw query editor and result forwarding into chat.
- `Research History`
  Memory/profile/history management.
- `Shared Session`
  Read-only or comment/fork view for shared analysis sessions.
- `Alerts` and `Anomaly Explorer`
  Time-domain and anomaly workflows.

### Shared frontend infrastructure

- `src/api/client.ts`
  Central typed API client, auth header injection, page tracking headers, and helper wrappers.
- `src/context/AuthContext.tsx`
  JWT-based auth state and profile lifecycle.
- `src/hooks/useTracking.ts`
  Session-scoped event tracking for analytics.
- `src/components/nodes/*`
  Pipeline palette, node renderer, and node parameter editing.

## 3. Backend Architecture

Backend entrypoint: [main.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/main.py)

### API domains

- `auth`
  Username/password login, JWTs, optional Google login, profile/settings operations.
- `data`
  Multi-archive search, advanced search, FITS upload, preview, download, and fetch flows.
- `chat`
  AI chat endpoint, tool loop, session persistence, legacy action execution.
- `pipeline`
  DAG validation, sync/async execution, template/version APIs, batch run.
- `workspace`
  File metadata, tags, notes, export integration.
- `integration`
  ADQL/TAP and other interoperability endpoints.
- `export`
  Markdown/report/notebook/LaTeX/BibTeX and chat-based export flows.
- `paper`
  Paper draft generation and manuscript artifact download.
- `sessions`
  Share links, comments, session forking, snapshots, restore, diff.
- `research`
  Opt-in memory profile and history APIs.
- `alerts`, `anomalies`, `followup`, `dossier`
  Time-domain and anomaly features.
- `events`, `admin/events`, `admin/inference`
  Analytics and inference monitoring.

### Key backend subsystems

#### AI layer

- [app/ai/orchestrator.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/ai/orchestrator.py)
  Classifies request intent, assembles specialist-agent context, and chooses tool subsets.
- [app/ai/inference_router.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/ai/inference_router.py)
  Routes inference to Claude/OpenAI/DeepSeek/local backends and records cost/latency logs.
- `app/ai/agents/*`
  Specialist prompt definitions for data, analysis, literature, observation, and visualization work.
- [app/services/ai_tools.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/services/ai_tools.py)
  Tool catalog and execution layer used by chat.

#### Data access layer

- `app/connectors/*`
  Archive-specific adapters normalized behind a common search/fetch interface.
- [app/connectors/registry.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/connectors/registry.py)
  Lazy registry for 15 live connectors.
- `app/search/*`
  Natural-query parsing and filter extraction for advanced search.

#### Analysis layer

- [app/services/astro_analysis.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/services/astro_analysis.py)
  Astronomy helper functions exposed to the Python sandbox and AI tools.
- [app/services/code_executor.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/services/code_executor.py)
  Sandboxed Python execution with session-scoped variable persistence.
- [app/analysis/image_reduction.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/analysis/image_reduction.py)
  CCD reduction and source extraction helpers.

#### Pipeline layer

- [app/pipeline/engine.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/pipeline/engine.py)
  DAG validation, topological execution, sync fallback, Celery execution entrypoint.
- `app/pipeline/nodes/*`
  Query/import, spectroscopy, plotting, crossmatch, and CCD reduction nodes.

#### Collaboration and memory

- [app/api/sessions.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/api/sessions.py)
  Share tokens, comments, snapshots, fork flows.
- [app/services/memory_service.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/services/memory_service.py)
  Research profile generation, lightweight embedding store, history retrieval.

#### Analytics

- [app/services/event_collector.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/services/event_collector.py)
  Buffered event collection and bulk flush.
- [app/middleware/event_tracking.py](/Users/chenkexuan/.openclaw/workspace/astro-platform/backend/app/middleware/event_tracking.py)
  Automatic API-level tracking on core routes.

## 4. Persistence Model

Core persistent entities currently include:

- `User`
- `DataFile`
- `PipelineRun`, `RunResult`, `PipelineTemplateDB`, `PipelineVersion`
- `ChatSession`
- `PaperDraft`
- `UserEvent`
- `SharedSession`, `SessionFork`, `SessionComment`, `SessionSnapshot`
- `UserResearchProfile`, `SessionEmbedding`
- `InferenceLog`
- Additional collaboration, alerting, and scheduler tables already in the schema layer

The project keeps SQLite compatibility in development through custom `UUIDType` and `JSONType`, while still working with PostgreSQL in production.

## 5. Runtime Flows

### Search flow

1. User submits a query in the frontend.
2. Frontend calls `/api/data/search` or `/api/data/advanced-search`.
3. Backend resolves coordinates when needed.
4. Selected connectors run concurrently.
5. Results are normalized into a shared response model.
6. Files fetched from search results land in Workspace storage and become reusable elsewhere.

### AI chat flow

1. Frontend sends message history plus current context to `/api/chat/message`.
2. Backend builds runtime context:
   - user profile context if memory is enabled
   - specialist-agent prompt fragments
   - filtered tool list
3. Inference router calls the configured LLM backend.
4. Tool calls are executed concurrently.
5. Tool results are appended back into the model loop until the turn completes.
6. Saved sessions can later feed paper generation, collaboration, and research memory.

### Pipeline flow

1. User edits a DAG in the React Flow canvas.
2. Frontend posts the DAG to `/api/pipeline/run`.
3. Backend validates nodes, edges, and execution order.
4. Execution happens either:
   - asynchronously via Celery worker, or
   - synchronously in a thread executor
5. Node outputs are merged and trimmed for API return.
6. Run metadata is stored in `PipelineRun` and `RunResult`.

### Collaboration flow

1. A saved chat session can be shared with `view`, `fork`, or `comment` access.
2. Shared sessions are loaded via tokenized URLs.
3. Forking creates a new `ChatSession` under the collaborator’s account.
4. Snapshots serialize current chat-session state for point-in-time restore/diff.

### Memory flow

1. User opts into memory.
2. Saved chat sessions are summarized into profile features and hashed embeddings.
3. Future chat requests ask the memory service for relevant prior summaries.
4. Research History UI exposes searchable summaries and editable profile metadata.

### CCD reduction flow

1. User uploads or imports FITS data.
2. AI tools or pipeline nodes call CCD reduction helpers.
3. Bias, dark, flat, cosmic-ray, astrometry, and source extraction steps run on stored FITS assets.
4. Reduced products are written back to workspace-style storage and can re-enter Pipeline, Chat, or export flows.

## 6. External Integrations

### Astronomy archives

- SIMBAD
- Gaia
- SDSS
- VizieR
- MAST
- NED
- 2MASS
- Chandra
- AllWISE
- ALMA
- ESO
- IRSA
- JWST
- LAMOST
- DESI

### Other external services

- NASA ADS / arXiv
- astrometry.net
- Anthropic, OpenAI, DeepSeek, or local OpenAI-compatible model servers
- Redis/Celery for optional async execution

## 7. Deployment Profiles

### Minimal development

- FastAPI
- React/Vite
- SQLite
- local file storage

### Standard production

- FastAPI web service
- PostgreSQL
- object/file storage backend
- React static frontend
- Redis
- Celery worker + beat

### AI backend choices

- Claude-only deployment
- mixed Claude/OpenAI/DeepSeek routing
- local model fallback via OpenAI-compatible endpoint

## 8. Current Design Constraints

These are implementation realities worth keeping explicit:

- Research memory is opt-in and based on lightweight hashed embeddings, not heavyweight vector infrastructure.
- Celery is optional; the backend still supports synchronous pipeline execution.
- The orchestrator currently builds routed specialist context on top of a single chat turn loop; the backend is prepared for richer multi-agent execution, but the current production path is still centered on one coordinated tool loop per turn.
- Workspace files are the handoff boundary between search, chat, export, and pipeline modules.

This document is intended to stay aligned with the current repository, not an aspirational roadmap. Update it when modules, flows, or deployment assumptions materially change.
