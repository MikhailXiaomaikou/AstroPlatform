# Source Mapping Status

This page tracks the provenance-v2 mapping state for data sources.  It is
kept in sync with the machine-readable registry in
`backend/app/services/source_mapping.py`.

## Archive Connectors

Current first-stage provenance-v2 archive status:

| Status | Count | Sources |
|---|---:|---|
| Active | 6 | VizieR, Gaia DR3, SIMBAD, NED, 2MASS, ALMA |
| Maintenance-gated | 18 | SDSS, SDSS spectra, MAST, Chandra, AllWISE, ESO, IRSA, JWST, LAMOST, DESI, Pan-STARRS, XMM-Newton, NVSS, FIRST, JPL Horizons, ATNF PSRCAT, SPARC, FRBSTATS |

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

ALMA is deliberately metadata-only in this phase.  Claims about
`L[CII]`, FWHM, or line-ratio measurements must come from cited
literature measurement tables or a future dedicated line-measurement
connector.

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
