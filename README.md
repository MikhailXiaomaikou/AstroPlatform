# Standard Astro

AI-native astronomy research platform for archive discovery, analysis,
statistical inference, provenance tracking, collaboration, and paper export.

Paper drafts generated from AI sessions are private to the owner account by
default. A draft becomes publicly readable only after the owner explicitly uses
**Publish Draft**, which creates a revocable `/papers/public/:token` link.

## What It Does

| Area | Summary |
|---|---|
| Data access | Query active provenance-v2 sources from one interface. |
| AI assistant | Multi-tool research agent for archive queries, ADQL, literature review, table extraction, analysis, fitting, and paper drafting. |
| Pipelines | Visual DAG editor for CCD reduction, spectroscopy, photometry, time-domain analysis, image processing, and Bayesian inference. |
| Provenance | Tool results carry citation, archive version, field bibcodes, query hashes, run IDs, and acknowledgement metadata. |
| Cosmology | Dataset registry, likelihood config builder, MCMC tools, robustness matrix scaffolding, and chain diagnostics. |
| Export | Paper drafts, BibTeX, acknowledgement text, notebooks, figures, and reproducibility packages. |

## Active Data Sources

The connector registry has 24 source keys. During the provenance-v2 rollout,
sources without upgraded citation and `archive_version` metadata are
maintenance-gated instead of silently returning weak-provenance data.

Active provenance-v2 sources:

- VizieR (`vizier`)
- Gaia DR3 (`gaia`)
- SIMBAD (`simbad`)
- NED (`ned`)
- 2MASS (`2mass`)
- ALMA Science Archive observation metadata (`alma`)

Maintenance-gated sources include SDSS / SDSS spectra, MAST, JWST, ESO, IRSA,
Chandra, XMM-Newton, AllWISE, LAMOST, DESI, Pan-STARRS, NVSS, FIRST, JPL
Horizons, ATNF Pulsar, SPARC, and FRBSTATS. Gated sources return an
`UNAVAILABLE` tool status and instruct the AI to suggest active alternatives.

ALMA currently provides observation metadata only. Derived line luminosities,
FWHM values, and line-relation fits require cited measurement tables from
literature extraction or a dedicated measurement source.

## Guardrails

- Numerical claims are checked against current-turn tool outputs.
- Citation claims are checked against tool-sourced bibcodes, arXiv IDs, DOIs,
  and row-level table provenance.
- Synthetic or demonstration outputs are marked non-citeable.
- Paper-level literature search supports context and citations, but not
  measurement claims unless tables are extracted and normalized.
- Gated archive calls are distinct from failed or empty calls in the UI.
- Data Sources panels expose `archive_version`, source authority, table/field
  citations, credits links, and acknowledgement templates.

## Scientific Coverage

Standard Astro includes workflows for:

- Gaia DR3 tables and variability products
- SIMBAD/NED/VizieR/2MASS object and catalog work
- ALMA high-redshift line-observation metadata
- Literature search and arXiv table extraction
- Spectral analysis, line fitting, and equivalent widths
- Photometry, source extraction, PSF work, and extinction handling
- Isochrone fitting and cluster analysis
- Time-domain period, transit, flare, and RV workflows
- Galaxy SFR, morphology, rotation-curve, and X-ray tools
- Observational cosmology likelihood and MCMC scaffolding
- Paper drafting, bibliography generation, and reproducibility export

## Tech Stack

| Layer | Stack |
|---|---|
| Frontend | React 19, TypeScript strict, Vite, React Router, React Flow, Plotly |
| Backend | FastAPI, SQLAlchemy async, Pydantic v2, SSE streaming |
| AI | Manual provider/model selection across Claude, OpenAI, DeepSeek, local OpenAI-compatible backends, and local-only CLI adapters |
| Astronomy | astropy, astroquery, specutils, photutils, reproject, pyvo, lightkurve |
| Statistics | emcee, dynesty, ArviZ, celerite2, batman, scipy, scikit-learn |
| Storage | PostgreSQL in production, SQLite for development, filesystem FITS storage |
| Reliability | Subprocess-isolated Python sandbox, connector cache, upstream throttling, Prometheus metrics |

## Repository Layout

```text
backend/
  app/
    ai/                 Inference router, model profiles, specialist agents
    api/                FastAPI routers for chat, data, auth, export, pipeline, admin
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
- Deployment notes: [DEPLOY_OPENCLAW.md](./DEPLOY_OPENCLAW.md)
- Agent/development notes: [CLAUDE.md](./CLAUDE.md)

## License

Released under the MIT License. See [LICENSE](./LICENSE) for details.
