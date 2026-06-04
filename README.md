# Standard Astro

**An AI research copilot for observational cosmology that you can trust with a number.**

Standard Astro is built for working cosmologists. You ask a research question in
plain language; the assistant queries real archives, runs real likelihoods and
fits, and writes up the result — with every figure, parameter, and citation
traceable back to the tool run and dataset that produced it. If it can't back a
claim with a current-turn tool result, it tells you so instead of guessing.

## The one idea that matters

**The model never touches your data.** The LLM only proposes structured tool
calls; the backend runs them, wraps the output in provenance, and hands the
normalized result back. That single chokepoint is where archive availability,
synthetic-data detection, numeric validation (±1% against tool output), and
citation checking are enforced — so the assistant is *structurally* unable to
fabricate a value and pass it off as observed.

When the tools come back empty, you get an honest "here's what I tried and why
it didn't work" card, not invented prose.

## What you can do

- **Ask in chat** — archive queries, ADQL, literature search, table extraction
  from papers, analysis, fitting, and drafting, all from one conversation.
- **Run cosmology** — dataset registry, BAO / SN / CMB / lensing likelihood
  configs, a compressed-posterior runner and a controlled nested sampler, MCMC,
  chain diagnostics, and a robustness matrix. Blind-tested on 50+ cases derived
  from real papers.
- **Mine papers into tools** — turn methods sections into reusable, cited
  capability specs.
- **Export** — paper drafts, BibTeX, acknowledgement text, notebooks, figures,
  and reproducibility bundles. Drafts are private until you publish them.

## Scope

This repository is **cosmology-only**. The single active module is observational
cosmology; the solar-system and exoplanet verticals (and the dormant domains)
were extracted to the sibling **standard-astro-verticals** repo on 2026-06-03.
Runtime focus is set per-process via `ASTRO_RESEARCH_FOCUS` (defaults to
`cosmology`; anything other than `all` falls back to it).

## Data sources

Six provenance-v2 archives are live: **VizieR**, **Gaia DR3**, **SIMBAD**,
**NED**, **2MASS**, and the **ALMA Science Archive** (observation metadata).
Another 17 connector keys (SDSS, MAST, JWST, DESI, Chandra, …) return an
`UNAVAILABLE` maintenance banner until each ships its own `archive_version`
provenance — they are gated, not faked.

## Tech stack

| Layer | Stack |
|---|---|
| Frontend | React 19, TypeScript (strict), Vite, Plotly |
| Backend | FastAPI, SQLAlchemy async, Pydantic v2, SSE streaming |
| AI | Manual provider/model choice across Claude, OpenAI, DeepSeek, and local OpenAI-compatible backends |
| Science | astropy, astroquery, emcee, dynesty, cobaya, CAMB, ArviZ |
| Storage | PostgreSQL (prod) / SQLite (dev); filesystem or S3 for FITS; Redis cache |

## Run it

```bash
# Backend (from backend/)
source venv/bin/activate
uvicorn app.main:app --reload --port 8000

# Frontend (from frontend/)
npm run dev        # http://localhost:5173
```

See [docs/QUICKSTART.md](./docs/QUICKSTART.md) for first-run setup.

## Documentation

- Architecture: [ARCHITECTURE.md](./ARCHITECTURE.md)
- API reference: [docs/API_REFERENCE.md](./docs/API_REFERENCE.md)
- Reference literature: [docs/REFERENCES.md](./docs/REFERENCES.md)
- Deployment: [DEPLOYMENT.md](./DEPLOYMENT.md)
- Agent / development notes: [CLAUDE.md](./CLAUDE.md)
- Recent changes: [CHANGELOG.md](./CHANGELOG.md)

## License

MIT — see [LICENSE](./LICENSE).
