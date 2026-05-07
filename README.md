# Standard Astro

AI-native astronomy research platform for archive discovery, analysis,
statistical inference, provenance tracking, collaboration, and paper export.

Recent project changes are tracked in [CHANGELOG.md](./CHANGELOG.md).

Paper drafts generated from AI sessions are private to the owner account by
default. A draft becomes publicly readable only after the owner explicitly uses
**Publish Draft**, which creates a revocable `/papers/public/:token` link.

## What It Does

| Area | Summary |
|---|---|
| Data access | Query active provenance-v2 sources from one interface. |
| AI assistant | Multi-tool research agent for archive queries, ADQL, literature review, paper-to-tool mining, table extraction, research planning, evidence graphs, analysis, fitting, and paper drafting. |
| Pipelines | Visual DAG editor for CCD reduction, spectroscopy, photometry, time-domain analysis, image processing, and Bayesian inference. |
| Provenance | Tool results carry citation, archive version, field bibcodes, query hashes, run IDs, and acknowledgement metadata. |
| Cosmology | Dataset registry, registered data-product loader, research planner, likelihood config builder, compressed posterior runner, controlled nested sampler, explicit chain diagnostics, evidence graph, and robustness matrix. |
| Research infrastructure | Build paper candidate pools, mine papers for ToolSpecs, run 20-paper local mining rounds, build a tool ontology, identify platform gaps, and rank implementation queues before adding new runners. |
| Spectral measurements | Literature-table rows can be validated as fit-ready spectral measurements before line-relation fitting. |
| Statistics | Deterministic robust summaries, regression helpers, bootstrap intervals, and censored-data summaries reduce ad-hoc analysis code. |
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
- Paper candidate pools, paper-to-tool mining, 20-paper continuous loop state, ToolSpec ontology, gap matrix, and implementation queue planning
- Spectral analysis, line fitting, and equivalent widths
- Photometry, source extraction, PSF work, and extinction handling
- Isochrone fitting and cluster analysis
- Time-domain period, transit, flare, and RV workflows
- Galaxy SFR, morphology, rotation-curve, and X-ray tools
- Observational cosmology likelihood configs plus compressed Gaussian posterior runner and controlled Gaussian nested sampler
- Research Mode planning, evidence graphs, fact checks, and compressed-likelihood experiment matrices
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
| DESI DR1 BAO | DESI Collaboration 2024, arXiv:2404.03002 | Registry entry with public BAO mean/covariance data products |
| SDSS + 6dF BAO | Beutler et al. 2011; Alam et al. 2017; eBOSS Collaboration 2021 | ACT-era / pre-DESI BAO likelihood planning |
| Pantheon+ | Scolnic et al. 2022; Brout et al. 2022 | SN distance table, covariance, and CosmoSIS likelihood product links |
| ACT DR6 lensing | Madhavacheril et al. 2024, arXiv:2304.05203 | Compressed CMB-lensing S8/H0 consistency checks |
| Planck 2018 | Planck Collaboration VI 2020, A&A 641, A6 | Compressed CMB baseline, PLA likelihood-code link, and ΛCDM comparison |
| KiDS-1000 cosmic shear | Asgari et al. 2021, arXiv:2007.15633 | Weak-lensing S8 comparison branch |
| DES Y3 3x2pt | DES Collaboration 2022, arXiv:2105.13549 | Galaxy weak-lensing + clustering comparison branch |
| HSC Y1 cosmic shear | Hamana et al. 2020, arXiv:1906.06041 | HSC weak-lensing S8 comparison branch |
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
- Source mapping: [docs/SOURCE_MAPPING.md](./docs/SOURCE_MAPPING.md)
- Deployment notes: [DEPLOYMENT.md](./DEPLOYMENT.md)
- Agent/development notes: [CLAUDE.md](./CLAUDE.md)

## License

Released under the MIT License. See [LICENSE](./LICENSE) for details.
