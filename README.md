# Standard Astro

AI-native astronomy research platform for archive discovery, analysis,
statistical inference, provenance tracking, and paper export.

> **Module status.** This repository is **cosmology-only**: the sole active
> module is **observational cosmology** (blind-tested across 50+ paper-derived
> cases). The solar-system and exoplanet verticals were extracted to the sibling
> **standard-astro-verticals** repo (2026-06-03). Every other domain (stellar,
> AGN, X-ray, pulsars, galaxy morphology, image reduction, …) lives under
> `backend/app/prompts/modules/_dormant_*` with its tools hidden from the LLM
> until promoted. Runtime focus is per-process via `ASTRO_RESEARCH_FOCUS`
> (`cosmology` default; any value other than `all` fails closed to cosmology).

Recent changes: [CHANGELOG.md](./CHANGELOG.md).

## What It Does

| Area | Summary |
|---|---|
| Data access | Query 6 active provenance-v2 archive sources from one interface. |
| AI assistant | Multi-tool research agent — archive queries, ADQL, literature, table extraction, research planning, evidence graphs, analysis, fitting, paper drafting. |
| Pipelines | Visual DAG editor for CCD reduction, spectroscopy, photometry, time-domain, image processing, Bayesian inference. |
| Provenance | Every tool result carries citation, archive version, field bibcodes, query hash, run ID, and acknowledgement metadata. |
| Cosmology module | Dataset registry, likelihood configs (BAO / SN / CMB / lensing), compressed posterior runner, controlled nested sampler, chain diagnostics, robustness matrix. |
| Modular focus gate | 1 active prompt module (cosmology) + 12 dormant; `prompt_loader` builds a focus-aware `SYSTEM_PROMPT` and per-focus tool allowlist so non-focus tools are physically invisible to the LLM. |
| Export | Paper drafts, BibTeX, acknowledgement text, notebooks, figures, reproducibility packages. |

## Active Data Sources

6 provenance-v2 sources: **VizieR**, **Gaia DR3**, **SIMBAD**, **NED**,
**2MASS**, **ALMA Science Archive** (observation metadata only).

17 maintenance-gated keys (return `UNAVAILABLE` until each ships independent
`archive_version` provenance): SDSS, sdss_spec, MAST, JWST, ESO, IRSA,
Chandra, XMM-Newton, AllWISE, LAMOST, DESI, Pan-STARRS, NVSS, FIRST, ATNF
Pulsar, SPARC, FRBSTATS.

## Guardrails

- Numerical and citation claims are checked against current-turn tool outputs
  and tool-sourced bibcodes / DOIs / table-row provenance; non-cited values
  are regenerated or blocked.
- Synthetic or demonstration outputs are explicitly marked non-citeable.
- Failed, empty, and maintenance-gated calls render as distinct UI states.
- Final replies are English-only (the gate ships English patterns; non-English
  drafts get one English regeneration before being blocked).
- Paper drafts are private to the owner; **Publish Draft** is the only way to
  expose one, via a revocable `/papers/public/:token` link.

## Tech Stack

| Layer | Stack |
|---|---|
| Frontend | React 19, TypeScript strict, Vite, React Router, React Flow, Plotly |
| Backend | FastAPI, SQLAlchemy async, Pydantic v2, SSE streaming |
| AI | Manual provider / model selection across Claude, OpenAI, DeepSeek, and local OpenAI-compatible HTTP backends |
| Astronomy | astropy, astroquery, specutils, photutils, reproject, pyvo, lightkurve |
| Statistics | emcee, dynesty, ArviZ, celerite2, batman, scipy, scikit-learn |
| Storage | PostgreSQL in production, SQLite for development, filesystem FITS storage |
| Reliability | Subprocess-isolated Python sandbox, connector cache, upstream throttling, Prometheus metrics |

## Repository Layout

```text
backend/
  app/
    ai/                 Inference router, model profiles, specialist agents
    api/                FastAPI routers (chat, data, auth, export, pipeline, admin)
    connectors/         Archive connector implementations and availability gates
    pipeline/           Visual DAG engine and node implementations
    services/           AI tools, provenance, literature, sandbox, analysis services
  tests/                Backend pytest suite

frontend/
  src/
    api/                Typed API and SSE client
    components/         Chat cards, provenance panels, visualization components
    pages/              Chat, Data Browser, Pipeline Studio, Workspace, Account, Help
    __tests__/          Vitest suite
```

## Documentation

- Architecture: [ARCHITECTURE.md](./ARCHITECTURE.md)
- Quick start: [docs/QUICKSTART.md](./docs/QUICKSTART.md)
- API reference: [docs/API_REFERENCE.md](./docs/API_REFERENCE.md)
- Reference literature: [docs/REFERENCES.md](./docs/REFERENCES.md)
- Source mapping: [docs/SOURCE_MAPPING.md](./docs/SOURCE_MAPPING.md)
- Deployment notes: [DEPLOYMENT.md](./DEPLOYMENT.md)
- Agent / development notes: [CLAUDE.md](./CLAUDE.md)

## License

Released under the MIT License. See [LICENSE](./LICENSE) for details.
