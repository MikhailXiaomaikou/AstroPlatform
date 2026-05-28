# Reference Literature

The codebase keeps scientific constants, formula choices, registry entries,
and workflow priors anchored to explicit literature references. This is the
canonical map of references the named code paths use as their source of
truth.

Moved here from the project README on 2026-05-27 to keep the README focused
on the platform overview; the table itself is unchanged.

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
| JPL Horizons ephemerides | Giorgini et al. 1996, BAAS 28, 1158 (1996DPS....28.2504G) | Solar-system body ephemerides via JPL Horizons API |
| Asteroid H–G magnitude system | Bowell et al. 1989, in Asteroids II, p. 524 | Phase-function reduction for absolute magnitude H and slope G |
| Cometary Afρ | A'Hearn et al. 1984, AJ 89, 579 | Dust-production proxy for comets |
| NEATM thermal model | Harris 1998, Icarus 131, 291; Mainzer et al. 2011, ApJ 731, 53 | Asteroid diameter and albedo from thermal IR fluxes |
| NEO impact probability | Öpik 1951; Wetherill 1967; Morbidelli & Gladman 1998; Morbidelli et al. 2002 | Closed-form NEO collision-probability scaling |
| Asteroid taxonomy (Bus-DeMeo) | DeMeo et al. 2009, Icarus 202, 160 | Reflectance-spectrum asteroid classification |
| Asteroid taxonomy (SDSS colours) | Carvano et al. 2010, A&A 510, A43 | SDSS griz-colour asteroid classification |
| Asteroid shape models | Ďurech et al. 2010, A&A 513, A46 | DAMIT shape-model lookups |
| NASA Exoplanet Archive | Akeson et al. 2013, PASP 125, 989 | `pscomppars` composite-parameters table and Confirmed Planets registry citation |
| Transit light curves | Mandel & Agol 2002, ApJ 580, L171 | Analytic transit-light-curve formalism for limb-darkened fits |
| Transit geometry | Seager & Mallén-Ornelas 2003, ApJ 585, 1038 | Closed-form transit duration / depth / inclination relations |
| TESS mission | Ricker et al. 2015, JATIS 1, 014003 | Mission-level citation for TESS light-curve data products |
| TESS Input Catalog (TIC v8) | Stassun et al. 2019, AJ 158, 138 | Stellar parameters for TESS target selection |
