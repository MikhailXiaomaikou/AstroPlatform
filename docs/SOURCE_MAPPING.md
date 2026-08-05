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

Pantheon+ redshift coverage is also provenance-bound rather than being a
free-text prompt fact. The registered `z=0.001--2.26` interval points to the
immutable Pantheon+SH0ES DataRelease commit
`c447f0fea703fcd0fff57de5000947b5ca81286b` and the vendored 1701-row bundle
with SHA-256
`bf0daa4ba2c06347db286d35f9f43c6de7c4fb85634e9f3821008911c7728bad`.
An outside-range receipt therefore means **verified against the platform
registry**, not "the paper table was fetched and matched this turn."

### DESI DR2 official posterior-combination registry

The DR2 BAO mean/covariance likelihood files remain ordinary dataset entries.
Their CobayaSampler URLs are pinned to immutable commit
`b7b8a36e9bccb063081f811f323cada21ab5fbdd`; the vendored mean and covariance
SHA-256 digests are respectively
`9ac154ab583ce759c0f7eef3c978c7c70a6ead2d18774caceadf1a350a640585` and
`252a143274c8a07c78694c119617d36594f6d7965d00319ca611c6ffb886e509`.

The official DESI DR2 model + BAO + CMB + SN chains are a separate
**analysis-combination registry**, because a posterior chain is not a reusable
likelihood factor. The first frozen registry version contains the three
headline `base_w_wa` combinations with uncalibrated Pantheon+, Union3, and
DES-SN5YR. Every config, checkpoint and four-part chain is pinned to the DESI
v1.0 manifest at
`https://data.desi.lbl.gov/public/papers/y3/bao-cosmo-params/`; the manifest
itself is pinned as
`df78872aa8b2d3473a9e8de78f498180efd7cbcbeb18211ce4787fac52067ee5`.
The registry audit checks artifact roles, hashes, parameter mappings and the
Pantheon+ calibration warning.

`run_dark_energy_evidence_matrix` never downloads these roughly 245 MiB of
chains. An operator must provide a complete local mirror through
`DESI_DR2_OFFICIAL_CHAIN_ROOT`. Missing files, checksum changes, invalid
weights, mismatched headers, failed checkpoint convergence or insufficient
weight ESS produce `WITHHELD` cells with no intervals. The official
`pantheonplus` component is explicitly not the platform's SH0ES-calibrated
`pantheon_plus` entry, and the official Planck/NPIPE/ACT CMB stack is not the
platform's `planck2018_compressed` record.

The three headline combinations share DESI and CMB observations. Until a
byte-pinned cross-covariance or paired-resampling product is registered, the
Tension Lab reports centers, interval widths and empirical 2D display grids,
and returns
`correlated_tension_withheld` with `tension_sigma=null`. Optional DR1 reference
cells are config-only and remain separate; no cell ever mixes DR1 and DR2.

## Next-generation survey schema fixtures

Rubin, Euclid, and Roman have a separate fail-closed
`SurveyProductAdapter` registry. It records release/schema versions, logical
fields and units, coordinate/time/redshift conventions, covariance/masks/
selection, coverage/checksum policy, access/licence metadata, and a restricted
claim scope. As of 2026-07-17 all three entries are
`SCHEMA_FIXTURE_ONLY`: none can query an archive or support a scientific
measurement. Euclid Q1 being publicly released does not change that status,
because no exact Q1 catalogue export and product SHA-256 have been pinned.

See `docs/SURVEY_PRODUCT_SCHEMAS.md` for the undergraduate-level explanation,
official source links, and promotion checklist. The existing
`scripts/audit_registry.py` includes the fixture integrity audit.

### DESI `w0wa` exact offline profile

The preregistered DESI 2024 VI interval-reproduction workflow is separate from
the registry's historical clik-free/CamSpec proxy path. Its only A-readiness-
eligible profile uses the official DESI DR1 Gaussian BAO and Pantheon+ full
statistical/systematic covariance likelihoods, Planck PR3 Commander + simall +
plik TTTEEE, and ACT DR6 + Planck PR4 lensing `actplanck_baseline`. It pins
CAMB PPF, the neutrino model, priors, likelihood anchors, dependency closure,
data/code/config hashes, fresh MPI-chain identities, and independent analysis.

This is an offline evidence pipeline, not a new public HTTP capability. A
generated config, successful reference point, smoke chain, historical proxy
chain, or CI fixture is never counted as scientific completion. The workflow
remains `WITHHELD` until its formal chains and six model-adequacy requirements
have actually run. See `docs/DESI_W0WA_A_READINESS_PROTOCOL.md` for the frozen
scope and state definitions.

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
