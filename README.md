# Standard Astro

> An auditable AI research workbench for observational cosmology.

**Research alpha · Cosmology only**

Standard Astro turns cosmology questions into controlled retrieval,
likelihood, fitting, audit, and export workflows. Its differentiator is not
fitting power — it is provable non-fabrication: every numerical claim must
trace to current-run server evidence, versioned data with pinned checksums,
provenance records, and real citations. When the evidence is insufficient,
the system returns `WITHHELD` or `CAPABILITY_GAP` instead of guessing.

This is not a "reproduce any paper" machine, and it does not replace peer
review.

## Real demo

[![Claim Audit real-case demo](./docs/demo/poster.png)](./docs/demo/standard-astro-claim-audit-demo.mp4)

This 32-second storyboard is made from UI captures of a real local run. Given
a strong DESI DR2 evolving-dark-energy claim, the job completes while the
scientific verdict independently becomes `CAPABILITY_GAP`: no result is
guessed, and the signed Evidence Pack verifies.

[Case, limits, and rebuild instructions](./docs/demo/README.md)

## Quick start

Requires Python 3.11 and Node.js 20+. These commands are for a fresh clone.

```bash
# Terminal 1: backend
cd backend
python3.11 -m venv venv
source venv/bin/activate
pip install --require-hashes -r requirements.lock
./venv/bin/uvicorn app.main:app --reload --port 8000
```

```bash
# Terminal 2: frontend
cd frontend
npm ci
npm run dev
```

Open the [app](http://localhost:5173/chat),
[health check](http://localhost:8000/health), or
[API docs](http://localhost:8000/docs). Local development defaults to SQLite;
add your own model API key under Account. Full asynchronous and production
operation also requires PostgreSQL, Redis, a worker, and durable object
storage. See the [detailed quick start](./docs/QUICKSTART.md).

## What works today

- **Lightweight, source-checked scalar verification** for bounded paper-table
  calculations: ratio, difference, product, and generalized
  inverse-covariance weighted mean. The backend validates units and covariance,
  resolves the cited source separately from the arithmetic, and returns a
  hashed receipt with `full`, `limited`, `abstention`, or `refusal` semantics.
- **Executable cosmology likelihoods** over registered, checksum-pinned
  datasets (BAO, SNe Ia, CMB, H0 priors, chronometers, and more), with
  claim validation, provenance banners, and dataset-overlap guards.
- **Anti-fabrication gates under daily blind testing** — fake tool
  transcripts, invented bibcodes, and pasted numbers are hard-blocked in CI
  every day, and clean runs must not be falsely blocked either.
- **Claim Audit with signed Evidence Packs** — a real local run produces a
  verifiable evidence bundle (see the demo above).

Measured evidence, model-in-loop (2026-08-06 campaign; five models, natural
researcher phrasings, n=60 post-fix): in-platform score 90.4% versus 46.6%
for the same models bare; a pasted fake "tool result" number was repeated
0/15 times in-platform (rule-of-three upper bound < 20%) versus 15/15 bare;
false blocks of clean answers 1/60. An earlier 1440/1440 headline was
retracted because that pipeline self-check never had the model in the loop —
the honest framing above is the one this project reports. These are
pre-merge snapshot numbers; the standing gate is a fresh rerun on the
current revision before any Alpha v0.2 claim. Full record:
[campaign report](./docs/research/STANDARD_ASTRO_V02_CAMPAIGN_REPORT_2026-08-06.md)
([Chinese original](./docs/research/STANDARD_ASTRO_V02_CAMPAIGN_REPORT_2026-08-06.zh-CN.md)).

## What it does not do yet

- The strict DESI w0waCDM v1 reproduction remains `WITHHELD`: its chains and
  independent recomputation do not yet form a publishable evidence pack.
- Claim Audit, Workflow Foundry (AI-drafted candidate workflows), and the
  DESI DR2 matrix are engineering-complete but dark-launched: their feature
  flags default to off, candidates can never output `SUPPORTED` without
  human review plus a signed registry release, and none of it is a
  production feature yet.
- Lightweight scalar verification is also dark-launched
  (`LIGHTWEIGHT_VERIFICATION_ENABLED=0` by default). Its automated matrices
  and adversarial regression suite are green, but the current code still
  requires a fresh end-to-end demo rerun plus independent expert review before
  an Alpha v0.2 claim.
- Rubin / Euclid / Roman entries are schema fixtures only — not executable
  and not evidence for any measurement claim.
- Product validation is pending: the platform has not yet completed its
  planned continuous-operation record or real-user validation. Even a
  `SUPPORTED` verdict does not mean "peer reviewed".

The full, current honesty record — including known limits and daily
blind-test evidence — lives in
[Honesty evidence and known limits](./docs/HONESTY_EVIDENCE.md).

## Verify

```bash
cd backend
./venv/bin/ruff check app tests
./venv/bin/pytest tests -q
```

```bash
cd frontend
npm run lint
npm run test
npm run build
```

## Documentation

- [Honesty evidence and known limits](./docs/HONESTY_EVIDENCE.md)
- [Architecture](./ARCHITECTURE.md) · [Source mapping](./docs/SOURCE_MAPPING.md)
- [Detailed quick start](./docs/QUICKSTART.md)
- [v0.2 evaluation](./docs/research/STANDARD_ASTRO_V02_EVALUATION_2026-08-04.md) · [campaign and post-review record](./docs/research/STANDARD_ASTRO_V02_CAMPAIGN_REPORT_2026-08-06.md) (English; Chinese originals: [评测](./docs/research/STANDARD_ASTRO_V02_EVALUATION_2026-08-04.zh-CN.md) · [战役](./docs/research/STANDARD_ASTRO_V02_CAMPAIGN_REPORT_2026-08-06.zh-CN.md))
- [Deployment](./DEPLOYMENT.md) · [Production cutover checklist](./docs/PRODUCTION_CUTOVER_CHECKLIST.md)
- [Privacy](./PRIVACY.md) · [Security](./SECURITY.md) · [Data licences](./docs/DATA_LICENSES.md)
- [Complete P0 + P1 roadmap](./docs/roadmaps/P0_P1_COMPLETE_PLAN.zh-CN.md) (Chinese)
- [AI Workflow Foundry v1](./docs/roadmaps/AI_WORKFLOW_FOUNDRY_V1.zh-CN.md) (Chinese)
- [Foundry release and activation runbook](./docs/runbooks/FOUNDRY_RELEASE_AND_ACTIVATION.zh-CN.md) (Chinese)
- [Foundry candidate source materialization](./docs/runbooks/FOUNDRY_SOURCE_MATERIALIZATION.zh-CN.md) (Chinese)

## Licence

Project source code is licensed under the [Apache License 2.0](./LICENSE).
Data, papers, and third-party services retain their own licence, citation,
and acknowledgement requirements; Apache-2.0 does not override those terms.
Contributions also follow the [DCO](./docs/DCO.md).
