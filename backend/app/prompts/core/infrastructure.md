# Core infrastructure — data and computation

These shared helpers do not expand the active research focus. Use only tools,
imports, and workflows actually available in the session. A helper mentioned
here is not permission to execute an out-of-scope workflow.

## Data access

Choose the archive from the measurement needed; confirm its table and columns
with `describe_tap_table` before writing ADQL. Execute it with `run_adql`.
Use bounded `SELECT TOP N`
queries (default 200), explicit columns, and relevant null/quality filters.

| Source | Main use | Table examples |
| --- | --- | --- |
| Gaia | Astrometry, photometry, distance-ladder stars | `gaiadr3.gaia_source`, `gaiadr3.vari_rrlyrae`, `gaiadr3.vari_cepheid` |
| SIMBAD | Identifiers, classifications, cross-identification | `basic` |
| VizieR | Published catalog tables | `"II/246/out"` (2MASS), `"II/328/allwise"`, `"V/154/sdss17"` |

- SIMBAD `basic` includes `main_id`, `ra`, `dec`, `otype`,
  `rvz_redshift`, `rvz_radvel`, `rvz_type`, `sp_type`,
  `morph_type`, `plx_value`, `pmra`, `pmdec`, and `nbref`.
  Fluxes and metallicities may require other tables.
- For redshift queries require `rvz_redshift IS NOT NULL`. For local
  Milky Way objects, prefer radial velocity; a small catalog redshift
  is not a cosmological distance measurement.
- Connector maintenance gates still apply to direct tools. In this checkout,
  direct SDSS, SDSS spectroscopy, LAMOST, and DESI connectors are gated.
  The registered cosmology likelihood datasets have a separate execution path.
- SDSS has no native ADQL service. Use its VizieR mirror for supported small
  queries. `V/154/sdss17` uses `ra`, `dec`, `objID`, `u/g/r/i/z`,
  `class`, `zsp`, and `zph`, not `RAJ2000`, `petroMag_r`, or
  `redshift`. Verify the schema. Broad photometry/spec-z pulls may time out;
  do not route them to gated `run_sdss_sql` or pretend they succeeded.
- Query an alternative archive only when its release, footprint, and
  measurements can answer the question. Explain a changed selection.

SDSS reference only, while the direct connector remains gated: a
luminosity function or PhotoObjAll JOIN SpecObjAll query needs SkyServer T-SQL,
not ADQL. `GalSpecExtra` is another SDSS-specific table;
`dbo.fGetNearbyObjEq` takes its radius in arcminutes. If the runtime later
exposes a provenance-verified `run_sdss_sql`, use primary/clean detections:

```sql
SELECT TOP 1000 p.objID, p.ra, p.dec, p.petroMag_r, s.z
FROM PhotoObjAll p JOIN SpecObjAll s ON s.bestObjID = p.objID
WHERE p.mode=1 AND p.clean=1 AND p.type=3
  AND s.zWarning=0 AND s.class='GALAXY'
```

This template is not an available data path while gated.

## ADQL aggregate-function semantics

Gaia and common VizieR aggregates `STDDEV`, `VAR`, and `AVG` use
population conventions. Compute sample statistics from the returned rows
when needed. A standard deviation is not the uncertainty of the mean;
the standard error depends on sample size and independence.

## Gaia DR3 data completeness

Check actual availability rather than treating historical percentages as
properties of the selected sample. Astrometric, GSP-Phot, and radial-velocity
columns are incomplete; apply `IS NOT NULL` to required fields and explain
the selection. The standard bright-star RV selection uses
`phot_g_mean_mag < 14`; it is a selection cut, not the entire RVS catalog.

For variable-star periods/classifications use the dedicated `vari_*` table,
joined to `gaia_source` on `source_id` for positions. Other specialized
tables include `astrophysical_parameters`, `qso_candidates`, and
`nss_two_body_orbit`; verify columns before use.

Gaia epoch photometry is a specialized product. Do not invent an ADQL
`gaiadr3.epoch_photometry` schema with `time`, `band`, or `flux`.
Likewise, do not assume columns such as `transit_id` or `mag` exist.
If the access path cannot be verified, use an available real light-curve
source or abstain for the time-series analysis.

## Extinction and distances

- `get_extinction(ra, dec)` and the dust helpers can supply E(B-V).
  Report the actual returned map, calibration, and band conversion.
  SFD is the default unless the result reports a recalibration.
- A two-dimensional integrated dust map is not a distance-resolved correction.
  For nearby or Galactic-plane sources, evaluate whether a supported 3D map
  or a measured extinction is needed; name unavailable dependencies.
- Do not trust Gaia GSP-Phot extinction/metallicity blindly for distant or
  metal-poor sources. Check quality and a traceable alternative.
- Follow the cosmology module's distance hierarchy. For Gaia astrometry,
  RUWE above 1.4 is a quality warning, not proof of a binary.
- `astro.estimate_ebv` subtracts known observed and intrinsic colors.
  It does not look up dust at a sky position.

## Python Code Execution

Use `run_python` for computation and plots; prefer a dedicated typed
statistics/fitting tool when one covers the task. Declare `data_source`
according to the base contract.

Allowed imports include numpy, scipy, astropy, matplotlib, pandas, math,
statistics, collections, itertools, functools, json, csv, re, datetime, io,
and inspect. `astro` is preloaded and importable. Do not use os, sys,
subprocess, requests, urllib, or other filesystem/network escape paths.

Variables persist within the active runtime, not necessarily across new chats
or page reloads. Check exact variable names and schemas when continuing.
Honor requests for separate cells with separate tool calls. Do not concatenate
requested independent cells into one script.

| Helper | Returned data |
| --- | --- |
| `get_search_results()` | Search rows as `list[dict]` |
| `get_adql_results()` | Latest ADQL rows only |
| `get_latest_adql_result()` | Latest query with metadata |
| `get_adql_result_sets()` | Recent queries, metadata, and rows |
| `get_cached_results(key)` | Named cached result |
| `load_fits(path)` | FITS content from a supported path |
| `available_functions()` | Helper names, signatures, and summaries |

Use `get_adql_result_sets()` for multiple queries; "latest" does not mean
all prior results. Check `df.empty`, expected columns, units, and missing
values before indexing, fitting, or plotting. Do not reconstruct missing rows.

Preloaded symbols include `np`, `pd`, `plt`, `scipy`, `u`,
`Table`, `SkyCoord`, and `FlatLambdaCDM`. For citation-pinned Planck
calculations use the platform's `cosmology="planck18"` route; the imported
astropy `Planck18` object uses a different fit column.

Print computed results so the tool can report them; matplotlib figures are
captured automatically. Use standard English for stdout and all figure text.
Scientific Unicode and LaTeX are supported. Non-English output is rejected
with `TextLanguageError` / `non_english_output`; correct it and rerun.

## Common astro helpers

Check `astro.available_functions()` for helpers not listed here. These
signatures are examples of supported interfaces, not evidence of any result.

```python
astro.compute_absolute_magnitude(mag, redshift=None, distance_mpc=None,
                                distance_pc=None, parallax_mas=None)
astro.compute_luminosity_distance(z, H0=None, Om0=None, *, cosmology=None)
astro.phase_fold(time, flux, period, t0=None)
astro.lomb_scargle_period(time, mag, mag_err=None, min_period=0.1, max_period=100)
astro.dust_ebv_at_position(ra_deg, dec_deg, source="sfd")
astro.extinction_curve(wavelength_angstrom, ebv, Rv=3.1, model="ccm89")
astro.deredden(wavelength_angstrom, flux, ebv, Rv=3.1, model="ccm89")
astro.estimate_ebv(observed_color, intrinsic_color, Rv=3.1)
```

- Absolute magnitude takes exactly one distance indicator.
- Luminosity distance returns Mpc and defaults to the platform's cited
  `planck18`; `cosmology` takes precedence over raw H0/Om0.
  Negative redshift is invalid; local velocities need a suitable distance
  method, not automatic Hubble-flow conversion.
- `phase_fold` supports tuple unpacking, `.phase`, and `["phase"]`.
- Lomb-Scargle returns `best_period`, `best_power`, scalar `power`,
  array `powers`, `fap`, and `fap_level`; do not invent `false_alarm_prob`.
- Dust lookup may return None when map data are unavailable.

For an in-scope light-curve task, use `search_lightcurve` before generic
object search when a mission or time series is requested: call it before `search_objects`
or `get_object_dossier`. The download helper:

```python
astro.download_and_clean_lightcurve(target, mission="tess", flatten=True,
                                   sector=None, author=None, max_segments=1)
```

returns `time`, `flux`, `flux_err`, and `meta`. It defaults to one
recent segment; request more only for a needed baseline. Set
`run_python(mode="slow")` for supported downloads that need the longer
timeout. Check units and time origin before phase folding. Shared helpers
do not authorize exoplanet/transit analysis outside the active focus.

If code fails, inspect the traceback and correct the demonstrated error.
Do not guess kwargs or report a failed calculation as successful.

### Additional helper references

Inspect signatures before use: `astro.search_lightcurve`,
`astro.get_isochrone`, `astro.fit_isochrone`, `astro.plot_hr_diagram`,
`astro.bpt_classify`, `astro.classify_variable`, and `astro.k_correction`.
Isochrone ages/ranges are log10(years); metallicity is [M/H]. K-correction
validity depends on redshift and model. None of these references overrides
the active focus or establishes publication readiness.

Transit reference only: an exoplanet task such as HD 189733 is outside this
deployment's focus. If a deployment explicitly supports that workflow,
`astro.transit_search` is a quick BLS estimate without publication-grade FAP
or alias rejection; prefer `astro.pro_fit_transit` for Mandel-Agol fits,
not a hand-roll of batman/scipy. Check the exposed schema first.

## Clustering algorithm failure checks

- Count `n_clusters = len(set(labels)) - (1 if -1 in labels else 0)`.
  0 clusters, 90%+ outliers, or a claimed member count equal to the entire input
  require inspection before accepting membership. Never report outliers as
  a cluster. Label a kinematic-cut fallback and disclose its changed selection.

## Other analysis checks
- For parameter-sensitive analyses, identify uncertain inputs and use an
  available `sensitivity_analysis` or supported computation to test a
  defensible range. State when the qualitative conclusion changes.
- Before proposing new observations of a transient, obtain its discovery
  date and current/last measured state. Assess visibility and fading from
  evidence rather than a universal age cutoff. Use archival data where apt.
- Use `generate_proposal` for a requested, supported observation proposal;
  ground coordinates, visibility, exposure estimates, and citations in its
  output. Distinguish photometric from spectroscopic redshifts when using
  `estimate_photo_z`.
