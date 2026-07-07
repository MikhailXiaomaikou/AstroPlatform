# Standard Astro

**A controlled AI workbench for auditable observational-cosmology research.**

Hosted alpha (no local setup): <https://astro-frontend-tyfr.onrender.com> —
registration is free; bring your own Anthropic/OpenAI key for those models
(see the account page).

Standard Astro is built around one practical research problem: letting an AI
assistant help plan, run, check, and write up observational-cosmology workflows
without letting model memory masquerade as data. You ask a research question in
plain language; the assistant proposes structured tool calls; the backend runs
registered datasets, likelihoods, fits, evidence checks, and exports; and every
strong numerical claim is expected to trace back to a current-turn tool result,
dataset, and citation.

The current project is best understood as a **research alpha workbench**, not a
general "reproduce any paper" machine. Its strongest area is registered,
provenance-tracked observational cosmology. Outside that executable envelope,
the correct behavior is a precise capability gap, not a guessed result.

## The one idea that matters

**The model proposes; the backend executes and verifies.** The LLM does not get
to create observational data by prose. It can request tools, but the backend is
the enforcement point for archive availability, provenance envelopes, synthetic
data detection, numeric validation, citation checking, chain diagnostics, and
fact verification.

That design is meant to make unsupported scientific claims hard to launder:
pasting a fabricated tool transcript should not ground a result, config-only
entries should not become posterior constraints, and paper abstracts should not
turn into measurement tables. The same guardrails are tested from the other
side with clean-turn specificity cases, so legitimate successful runs should be
able to reach the user without false blocks.

When tools come back empty or a requested likelihood is not executable, the
platform should return an explicit "what was tried / what is missing / what can
be run next" summary instead of filling the gap with memorized numbers.

## What you can do

- **Ask in chat** — archive queries, ADQL, literature search, table extraction
  from papers, analysis, fitting, and drafting, all from one conversation.
- **Run controlled cosmology workflows** — the registry contains released,
  pinned BAO, SN, CMB, H(z), fσ8, and related products. Executable entries run
  through in-process likelihoods or guarded external-Cobaya paths; config-only
  entries remain visible as gaps rather than being silently approximated.
  Chain diagnostics report only what was actually computed, model comparisons
  carry validity guards, and overlapping datasets refuse unsafe co-addition.
- **Mine papers into tools** — turn methods sections into reusable, cited
  capability specs.
- **Export** — paper drafts, BibTeX, acknowledgement text, notebooks, figures,
  and reproducibility bundles. Drafts are private until you publish them.

## Current alpha contract

Standard Astro's near-term target is **B-or-better partial-pass behavior** on
paper-derived observational-cosmology blind tests:

- recognize the scientific workflow and plausible registered datasets;
- run a controlled compressed/preliminary baseline when one exists;
- otherwise produce an auditable missing-capability matrix;
- avoid unsupported posterior, fit, significance, anomaly, or citation claims;
- explain what would be needed for paper-level agreement.

This is not a claim of 95% paper reproduction. Strict A-level agreement is an
offline hidden-answer evaluation that requires the correct data products,
method, model family, diagnostics, evidence graph, and numerical scale.

## Reproduction track record

A dedicated full-CMB Cobaya+CAMB run (DESI DR1 BAO + Pantheon+ + a clik-free
Planck 2018 stack, on free local compute) reproduced the **DESI 2024 VI
w0waCDM evolving-dark-energy result** with all four parameters within 0.3σ of
the published values and DESI-level error bars — see
[backend/scripts/cobaya/README_full_cmb_reproduction.md](./backend/scripts/cobaya/README_full_cmb_reproduction.md)
for the numbers, the reproduction commands, and the honest caveats (converged
at R-1(means) < 0.05, not the stricter 0.01 gold standard; 2.09σ joint
departure vs DESI's ~2.5σ, attributable to the close-proxy CMB stack). This is
an offline scripted run, **not** the autonomous chat path: the in-process
compressed-CMB path does not reproduce this result, so the platform correctly
refuses to claim it autonomously.

## What it does not claim

- It does not promise production-grade full-likelihood inference for every
  cosmology paper.
- It does not treat `CONFIG_READY`, paper abstracts, old chat context, or user
  assumptions as support for posterior numbers.
- It does not make ALMA metadata into line luminosity or FWHM measurements.
- It does not currently support every astronomy vertical in this repository;
  this checkout is intentionally focused on observational cosmology.

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

Cosmology likelihood data products are tracked separately from archive
connectors. See [docs/SOURCE_MAPPING.md](./docs/SOURCE_MAPPING.md) for the
current registry classes, execution modes, and claim scopes.

## Tech stack

| Layer | Stack |
|---|---|
| Frontend | React 19, TypeScript (strict), Vite, Plotly |
| Backend | FastAPI, SQLAlchemy async, Pydantic v2, SSE streaming |
| AI | Manual provider/model choice across Claude, OpenAI, DeepSeek, and local OpenAI-compatible backends |
| Science | astropy, astroquery, emcee, dynesty, cobaya, CAMB, ArviZ |
| Storage | PostgreSQL (prod) / SQLite (dev); local filesystem for FITS; Redis cache |

## Run it

No-install option: the hosted alpha at
<https://astro-frontend-tyfr.onrender.com> runs the same code as `main`.

Prerequisites: Python 3.11 (the version CI and the Docker image use) and Node 20+.

```bash
# Backend (from backend/) — create the venv on first run; it is gitignored
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend (from frontend/, separate terminal)
npm install
npm run dev        # http://localhost:5173, talks to the backend on :8000 by default
```

No `.env` file is required for local development: `ENV` defaults to `dev`, the
database defaults to a local SQLite file under `data/` at the repo root, and
dev-safe random `JWT_SECRET` / `FERNET_KEY` values are generated at startup
(with a logged warning — tokens and stored API keys do not survive restarts).
Production environment variables are documented in
[DEPLOYMENT.md](./DEPLOYMENT.md). To actually chat with the AI assistant you
need a model-provider API key: register, then add a key on the Account page
(BYOK), or set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` in
the backend environment.

**If startup fails with `Provenance registry freshness check failed`:** the
backend intentionally refuses to boot — in every environment, local dev
included — when any entry in
`backend/app/services/provenance_v2/fallback_registry.yaml` has a
`metadata.last_verified` date older than 180 days. Re-verify the stale entries
and update their dates; see the "Provenance-v2 Startup Guard" section of
[DEPLOYMENT.md](./DEPLOYMENT.md) for the procedure. Do not weaken the gate.

See [docs/QUICKSTART.md](./docs/QUICKSTART.md) for a product usage tour (what
to try once the app is running — it is not a development-setup guide).

## Documentation

- Architecture: [ARCHITECTURE.md](./ARCHITECTURE.md)
- API reference: [docs/API_REFERENCE.md](./docs/API_REFERENCE.md)
- Observational cosmology beta: [docs/OBSERVATIONAL_COSMOLOGY_BETA.md](./docs/OBSERVATIONAL_COSMOLOGY_BETA.md)
- Blind-test target: [docs/COSMOLOGY_PARTIAL_PASS_95_TARGET.md](./docs/COSMOLOGY_PARTIAL_PASS_95_TARGET.md)
- Blind-test protocol: [docs/BLIND_RESEARCH_TESTING_LOG.md](./docs/BLIND_RESEARCH_TESTING_LOG.md)
- Source mapping: [docs/SOURCE_MAPPING.md](./docs/SOURCE_MAPPING.md)
- Reference literature: [docs/REFERENCES.md](./docs/REFERENCES.md)
- Deployment: [DEPLOYMENT.md](./DEPLOYMENT.md)
- Agent / development notes: [CLAUDE.md](./CLAUDE.md)
- Recent changes: [CHANGELOG.md](./CHANGELOG.md)

## License

Licensing is not yet finalized; no license file is published in this repository at present.
