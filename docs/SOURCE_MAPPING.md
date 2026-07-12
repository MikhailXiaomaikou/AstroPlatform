# Source Mapping Status

This page tracks the provenance-v2 mapping state for data sources.  It is
kept in sync with the machine-readable registry in
`backend/app/services/source_mapping.py`.

## Archive Connectors

Current provenance-v2 archive status (source of truth:
`backend/app/connectors/availability.py` `V2_AVAILABLE_CONNECTORS`):

| Status | Count | Sources |
|---|---:|---|
| Active | 6 | VizieR, Gaia DR3, SIMBAD, NED, 2MASS, ALMA |
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

ALMA is deliberately metadata-only in this phase.  Claims about
`L[CII]`, FWHM, or line-ratio measurements must come from cited
literature measurement tables or a future dedicated line-measurement
connector.

The solar-system (JPL Horizons, IAU MPC) and exoplanet (NASA Exoplanet
Archive) connectors were extracted to the sibling standard-astro-verticals
repository on 2026-06-03 together with their modules; this repository is
cosmology-only. Their former registry keys remain in `CONNECTORS_KEYS`
history but are not active here.

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

The cosmology likelihood registry (34 entries as of 2026-06-13; live count
via `backend/scripts/audit_registry.py`) has grown far past the original
phase-1 compressed-Gaussian description. Three execution classes now exist:

1. **In-process executable likelihoods over released, sha256-pinned data
   products** (vendored byte-verbatim under `backend/data/`, every pin
   audited by `audit_registry.py` and `audit_executable_pins`): DESI DR1/DR2
   BAO mean+cov vectors; the 6dFGS Gaussian + SDSS MGS non-Gaussian
   chi2(alpha) table; the BOSS DR12 consensus BAO 6-point vector (the
   Planck 2018 "+BAO" likelihood, dimensional rs_fid=147.78 convention);
   eBOSS DR16 LRG/QSO joint FSBAO vectors; the eBOSS DR16 ELG probability
   table and Lya auto/cross 50x50 likelihood grids (non-Gaussian released
   surfaces, z=2.334); cosmic-chronometer H(z) (GA2018 diagonal +
   Moresco-2020 full covariance); eBOSS DR16 fsigma8; Union3's full 22-bin
   binned-distance likelihood (always on); and the offset-marginalized full
   SN vectors Pantheon+ 1701-SN, DES-SN5YR 1829-SN, and Pantheon 2018
   1048-SN behind their `*_FULL_CHI2_ENABLED` env flags. Cobaya-likelihood
   parity is locked by tests for the cobaya-sourced products.
2. **External-Cobaya CMB likelihoods** (vendored native data, dispatched to
   a cobaya subprocess behind `EXTERNAL_COBAYA_ENABLED`, off by default):
   the clik-free Planck 2018 suite — plik_lite TTTEEE, lowl TT, lowl EE,
   and lensing — where extended-model axes (mnu, omegak) are genuinely
   sampled. The in-process compressed path hard-refuses ok_*/*_mnu model
   names rather than relabeling LambdaCDM-shaped chains.
3. **Role-approved scalar external measurements** (SH0ES/TRGB/CCHP/
   megamaser H0, plus the flat-LCDM-only H0LiCOW scalar): these may enter the
   preliminary Gaussian runner only within their declared model/overlap scope.
4. **Literature-context / config-only entries** (published ACT/weak-lensing/SN
   posterior summaries, proposal-only Planck parameter rows, BBN until an
   omega_b-to-r_d forward model exists, and pending SPT/PR4 packages): these
   can build guarded configs or provide cited context but never enter chi2 as
   if they were independent likelihood factors.

Chain results are claimable only with `publication_ready=true` and the
matching `claim_scope`; chains over exclusively full-fidelity products carry
`executable_full_fidelity_likelihoods`, while priors/approximations remain
explicitly preliminary. Overlapping samples
declare reciprocal `do_not_combine_with` pairs (DESI vs SDSS/eBOSS BAO,
the SN compilations among themselves); violating combinations block.

## Solar System / Exoplanet Module Data Sources

Extracted to the sibling standard-astro-verticals repository on 2026-06-03
(together with their prompt modules, connectors, and tools). See that
repository's docs for the JPL/MPC/DAMIT/NExScI source mapping. This
repository is cosmology-only.

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
