# Source Mapping Status

This page tracks the provenance-v2 mapping state for data sources.  It is
kept in sync with the machine-readable registry in
`backend/app/services/source_mapping.py`.

## Archive Connectors

Current provenance-v2 archive status (source of truth:
`backend/app/connectors/availability.py` `V2_AVAILABLE_CONNECTORS`):

| Status | Count | Sources |
|---|---:|---|
| Active | 9 | VizieR, Gaia DR3, SIMBAD, NED, 2MASS, ALMA, JPL Horizons, IAU MPC, NASA Exoplanet Archive |
| Maintenance-gated | 17 | SDSS, SDSS spectra, MAST, Chandra, AllWISE, ESO, IRSA, JWST, LAMOST, DESI, Pan-STARRS, XMM-Newton, NVSS, FIRST, ATNF PSRCAT, SPARC, FRBSTATS |

Active means the connector can run through the provenance-v2 dispatch path.
It does not mean every science quantity is available from that archive.

| Source | Mapping layer | Scope |
|---|---|---|
| VizieR | IVOA DataOrigin when available + registry fallback | Catalog rows and table-level citations |
| Gaia DR3 | PARAM scanner + registry supplement | Gaia rows and Gaia DR3 citation |
| SIMBAD | Registry table-level + field bibcode extraction | Object metadata and per-field bibcodes where emitted |
| NED | non-standard INFO scanner + registry supplement | Extragalactic object metadata and NED citation |
| 2MASS | VizieR II/246 path + registry fallback | Near-infrared catalog rows and 2MASS citation |
| ALMA | ObsCore metadata + registry fallback | Observation metadata only; no derived line luminosity/FWHM |
| JPL Horizons | Horizons API + registry fallback | Solar-system body ephemerides (geocentric/topocentric) backing `fetch_horizons_ephemeris` (Giorgini+1996 1996DPS....28.2504G) |
| IAU MPC | MPC orbit DB + registry fallback | Asteroid/comet orbital elements backing `query_mpc_orbit` |
| NASA Exoplanet Archive | NExScI/IPAC archive tables + registry fallback | Confirmed exoplanets, candidate planets, and TESS target metadata backing `query_exoplanet_archive` and `query_confirmed_planets` |

ALMA is deliberately metadata-only in this phase.  Claims about
`L[CII]`, FWHM, or line-ratio measurements must come from cited
literature measurement tables or a future dedicated line-measurement
connector.

JPL Horizons + IAU MPC were promoted to active with the Solar System M0
module (2026-05-18, Commit C2).  They are only useful when the runtime
focus is `ASTRO_RESEARCH_FOCUS=solar_system`, where they back the data-
query half of the solar-system tool group (plus JPL SBDB, CNEOS Sentry-II,
and DAMIT, which are direct HTTP endpoints rather than connector-registry
entries).

NASA Exoplanet Archive was promoted to active with Exoplanet M0
(2026-05-20). It is only useful when the runtime focus is
`ASTRO_RESEARCH_FOCUS=exoplanet`; it provides archive/candidate metadata
but not derived transit fits unless the dedicated exoplanet compute tools
run successfully.

## Literature Measurement Tables

The literature-table pathway is the legal route for paper-table quantities
such as `log L[CII]`, FWHM, lensing magnification, and line-measurement
uncertainties.

| Status | Entry | Notes |
|---|---|---|
| Verified fit-ready | arXiv:2002.00962, Béthermin et al. 2020 ALPINE [CII] master sample | End-to-end verified; expected 74 normalized line measurements |
| Pending verification | REBELS [CII] line tables | Candidate papers must be run through table extraction and produce `line_measurements > 0` |
| Pending verification | Capak et al. 2015 [CII] | Do not preload/cite row values until parser verification succeeds |
| Pending verification | Bothwell et al. 2013 SPT [CII] | Earlier remembered arXiv IDs were wrong; keep pending until verified |

Rules:

- `search_literature` supports paper/context claims only.
- `extract_literature_tables` supports measurement claims only when it returns normalized `line_measurements`.
- `prepare_spectral_measurements` validates whether those rows are complete enough for spectroscopy workbench use across `[CII]`, CO, Halpha, Lyalpha, [OIII], and future line samples.
- `fit_line_lfr` is the publication-path fit tool for line luminosity/FWHM relations.
- Raw-only tables are not fit-ready and must be described as needing column mapping.

## Observational Cosmology Registry

The cosmology likelihood registry is metadata/config-first with a phase-1
compressed Gaussian execution path. It currently tracks DESI DR1 BAO,
SDSS+6dF/SDSS-BOSS/eBOSS BAO, Pantheon+, DES-SN 5YR, Union3, Planck
compressed priors, ACT DR6 lensing, KiDS-1000, DES Y3, HSC weak-lensing
comparison branches, cosmic chronometers, and SH0ES H0 prior.

Config-only entries can build guarded Cobaya/CosmoSIS-style configs and
robustness matrices but cannot support posterior claims. Entries with
`execution_mode=compressed_gaussian` can run `run_cosmology_likelihood_chain`
for preliminary compressed posterior/tension summaries. Those numbers are
claimable only with `publication_ready=true` and must be described as
compressed-likelihood preliminary, not as full external likelihood results.

Priority executable-product coverage is now explicit in the registry:
DESI DR1 BAO points at the public CobayaSampler `bao_data` mean/covariance
files, Pantheon+ points at the public distance table, covariance matrices,
and CosmoSIS likelihood wrappers, and Planck 2018 points at the Planck
Legacy Archive likelihood-code page plus the compressed distance-prior table
source. DESI DR1 BAO is now consumed directly by the phase-1
`bao_gaussian_importance` runner for flat-LambdaCDM compressed preliminary
posterior summaries. Pantheon+ remains external-likelihood/config-only until
a runner consumes its distance table and covariance directly.

## Solar System Module Data Sources

The solar-system module (`ASTRO_RESEARCH_FOCUS=solar_system`, M0) wires
six external data services and four pure-function science kernels into
twelve LLM-callable tools.  All endpoints emit provenance-v2 dataset
entries on success and the standard `__tool_status__=FAILED` envelope on
HTTP / parse failure.

| Endpoint | Tool | Provenance reference |
|---|---|---|
| IAU MPC orbit database | `query_mpc_orbit` | IAU MPC |
| JPL Horizons API | `fetch_horizons_ephemeris` | Giorgini+1996 (1996DPS....28.2504G) |
| JPL SBDB orbit endpoint | `query_sbdb_orbit` | JPL SBDB |
| JPL CAD close-approach API | `query_sbdb_close_approaches` | JPL CAD |
| JPL CNEOS Sentry-II | `query_sentry_risk` | JPL CNEOS Sentry-II |
| DAMIT shape-model archive | `query_damit_shape_model` | Ďurech+2010 (2010A&A...513A..46D) |
| (pure-function) | `compute_hg_magnitude` | Bowell+1989, in Asteroids II, p. 524 |
| (pure-function) | `compute_afrho` | A'Hearn+1984 AJ 89, 579 |
| (pure-function) | `fit_neatm_diameter_albedo` | Harris 1998 Icarus 131, 291; Mainzer+2011 ApJ 731, 53 |
| (pure-function) | `compute_neo_collision_probability` | Öpik 1951; Wetherill 1967; Morbidelli+2002 |
| (pure-function) | `classify_asteroid_busdemeo` | DeMeo+2009 Icarus 202, 160 |
| (pure-function) | `classify_asteroid_sdss_colors` | Carvano+2010 A&A 510, A43 |

Pure-function tools tag results with `data_origin=cached_real` and never
fabricate observational rows; they consume parameter values supplied by
the caller (typically from the data-query tools above) and apply the
cited formula.

## Re-enable Checklist

To promote a gated archive connector:

1. Add registry metadata and acknowledgement text.
2. Populate `archive_version`, `source_urls`, `archive_ids`, and `source_authority`.
3. Add focused connector tests proving the provenance envelope.
4. Add the connector key to `V2_AVAILABLE_CONNECTORS`.
5. Update frontend source availability labels.

To promote a literature paper into the verified measurement seed list:

1. Run `extract_literature_tables` locally for the candidate paper.
2. Confirm `line_measurement_count > 0`.
3. Inspect row citations, line labels, luminosity units, FWHM, redshift, and lensing fields.
4. Add the arXiv ID to the verified seed list.
5. Add a regression test for the mapped column schema.
