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

## Reference Literature

The codebase keeps scientific constants, formula choices, and workflow priors
anchored to explicit literature references. This table is a compact map of the
main references currently used by tools, prompts, and validation fixtures.

| Area | Reference | Used for |
|---|---|---|
| Extinction law | Cardelli, Clayton & Mathis 1989, ApJ 345, 245 | CCM89 optical/IR extinction curve |
| Dust attenuation | Calzetti et al. 2000, ApJ 533, 682 | Starburst attenuation in photo-z / SED workflows |
| IGM absorption | Madau 1995, ApJ 441, 18 | High-redshift IGM absorption approximation |
| Gaia extinction coefficients | Wang & Chen 2019, ApJ 877, 116 | Gaia-band extinction ratios |
| PARSEC isochrones | Bressan et al. 2012, MNRAS 427, 127 | Isochrone fitting and turnoff fallback calibration |
| RR Lyrae PLZ | Muraveva et al. 2018, MNRAS 481, 1195 | RR Lyrae distance workflow guidance |
| Cepheid Leavitt law | Ripepi et al. 2019, A&A 625, A14 | Cepheid distance workflow guidance |
| Star-formation rates | Kennicutt & Evans 2012, ARA&A 50, 531 | Hα, UV, IR, and radio SFR calibrations |
| Pulsar derived quantities | Lorimer & Kramer 2004, Handbook of Pulsar Astronomy | Characteristic age, surface B, spin-down luminosity |
| Binary mass function | Hilditch 2001, An Introduction to Close Binary Stars | Spectroscopic binary mass-function relation |
| Variability index | Stetson 1996, PASP 108, 851 | Stetson K variability statistic |
| White dwarf cooling | Bédard et al. 2020, ApJ 901, 93 | Montreal cooling-age interpolation |
| NFW halo | Navarro, Frenk & White 1996, ApJ 462, 563 | Dark-matter halo profile guidance |
| SPARC rotation curves | Lelli, McGaugh & Schombert 2016, AJ 152, 157 | Galaxy rotation-curve catalog context |
| [CII] ALPINE tables | Béthermin et al. 2020, A&A 643, A2 | High-z [CII] table extraction and LFR tests |
| Gaia DR3 | Gaia Collaboration 2023, A&A 674, A1 | Gaia DR3 table-level citation |
| SIMBAD | Wenger et al. 2000, A&AS 143, 9 | SIMBAD registry citation |
| 2MASS | Skrutskie et al. 2006, AJ 131, 1163 | 2MASS registry citation |
| DESI DR1 BAO | DESI Collaboration 2024, arXiv:2404.03002 | Observational-cosmology registry entry |
| SH0ES prior | Riess et al. 2011 / 2022 | H0 prior provenance in cosmology workflows |
| Supernova cosmology | Suzuki et al. 2012, ApJ 746, 85 | Union-style Ωm / SN cosmology context |

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
- Deployment notes: [DEPLOYMENT.md](./DEPLOYMENT.md)
- Agent/development notes: [CLAUDE.md](./CLAUDE.md)

## License

Released under the MIT License. See [LICENSE](./LICENSE) for details.
