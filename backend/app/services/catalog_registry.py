"""Static metadata registry for commonly used astronomical catalogs.

Provides fast local lookups for column names, types, and descriptions
without requiring a runtime TAP_SCHEMA query. Used by describe_tap_table
as a fast-path cache and by adql_dialect for column validation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogColumn:
    name: str
    datatype: str
    description: str
    unit: str = ""


@dataclass(frozen=True)
class CatalogEntry:
    table_name: str
    service: str
    description: str
    columns: tuple[CatalogColumn, ...]
    common_queries: tuple[str, ...] = ()


def _cols(*specs: tuple[str, str, str]) -> tuple[CatalogColumn, ...]:
    """Shorthand: each spec is (name, datatype, description)."""
    return tuple(CatalogColumn(name=s[0], datatype=s[1], description=s[2]) for s in specs)


CATALOG_REGISTRY: dict[str, CatalogEntry] = {
    # ── Gaia DR3 ──
    "gaiadr3.gaia_source": CatalogEntry(
        table_name="gaiadr3.gaia_source",
        service="gaia",
        description="Gaia DR3 main source table (~1.8 billion sources)",
        columns=_cols(
            ("source_id", "BIGINT", "Unique source identifier"),
            ("ra", "DOUBLE", "Right ascension (ICRS, epoch 2016.0)"),
            ("dec", "DOUBLE", "Declination (ICRS, epoch 2016.0)"),
            ("parallax", "DOUBLE", "Absolute stellar parallax [mas]"),
            ("parallax_error", "DOUBLE", "Standard error of parallax [mas]"),
            ("pmra", "DOUBLE", "Proper motion in RA direction [mas/yr]"),
            ("pmdec", "DOUBLE", "Proper motion in Dec direction [mas/yr]"),
            ("phot_g_mean_mag", "FLOAT", "G-band mean magnitude [mag]"),
            ("phot_bp_mean_mag", "FLOAT", "BP-band mean magnitude [mag]"),
            ("phot_rp_mean_mag", "FLOAT", "RP-band mean magnitude [mag]"),
            ("bp_rp", "FLOAT", "BP-RP colour [mag]"),
            ("radial_velocity", "DOUBLE", "Radial velocity [km/s]"),
            ("radial_velocity_error", "DOUBLE", "RV standard error [km/s]"),
            ("phot_g_mean_flux", "DOUBLE", "G-band mean flux [e-/s]"),
            ("phot_bp_mean_flux", "DOUBLE", "BP-band mean flux [e-/s]"),
            ("phot_rp_mean_flux", "DOUBLE", "RP-band mean flux [e-/s]"),
            ("ruwe", "FLOAT", "Renormalized unit weight error"),
            ("astrometric_excess_noise", "DOUBLE", "Excess astrometric noise [mas]"),
            ("teff_gspphot", "FLOAT", "Effective temperature from GSP-Phot [K]"),
            ("logg_gspphot", "FLOAT", "Surface gravity from GSP-Phot [log(cm/s^2)]"),
            ("mh_gspphot", "FLOAT", "Metallicity from GSP-Phot [dex]"),
            ("ag_gspphot", "FLOAT", "Extinction in G from GSP-Phot [mag]"),
            ("ebpminrp_gspphot", "FLOAT", "E(BP-RP) reddening from GSP-Phot [mag]"),
            ("distance_gspphot", "FLOAT", "Distance from GSP-Phot [pc]"),
            ("l", "DOUBLE", "Galactic longitude [deg]"),
            ("b", "DOUBLE", "Galactic latitude [deg]"),
            ("non_single_star", "SHORT", "Non-single star flag"),
            ("has_xp_continuous", "BOOLEAN", "Has XP continuous spectra"),
            ("has_xp_sampled", "BOOLEAN", "Has XP sampled spectra"),
            ("has_rvs", "BOOLEAN", "Has RVS spectra"),
        ),
        common_queries=("cone search", "HR diagram", "proper motion selection", "parallax filter"),
    ),
    "gaiadr3.vari_rrlyrae": CatalogEntry(
        table_name="gaiadr3.vari_rrlyrae",
        service="gaia",
        description="Gaia DR3 RR Lyrae variable stars",
        columns=_cols(
            ("source_id", "BIGINT", "Gaia source identifier"),
            ("pf", "DOUBLE", "Pulsation frequency [1/day]"),
            ("pf_error", "DOUBLE", "Frequency error [1/day]"),
            ("p1", "DOUBLE", "Period [day]"),
            ("epoch_g", "DOUBLE", "Reference epoch in G [JD-2455197.5]"),
            ("int_average_g", "FLOAT", "Intensity-averaged G magnitude"),
            ("int_average_bp", "FLOAT", "Intensity-averaged BP magnitude"),
            ("int_average_rp", "FLOAT", "Intensity-averaged RP magnitude"),
            ("peak_to_peak_g", "FLOAT", "Peak-to-peak amplitude in G"),
            ("metallicity", "FLOAT", "Photometric metallicity [dex]"),
            ("best_classification", "VARCHAR", "RRab / RRc / RRd"),
            ("num_clean_epochs_g", "INTEGER", "Number of clean G observations"),
        ),
    ),
    "gaiadr3.vari_cepheid": CatalogEntry(
        table_name="gaiadr3.vari_cepheid",
        service="gaia",
        description="Gaia DR3 Cepheid variable stars",
        columns=_cols(
            ("source_id", "BIGINT", "Gaia source identifier"),
            ("pf", "DOUBLE", "Pulsation frequency [1/day]"),
            ("p1", "DOUBLE", "Period [day]"),
            ("int_average_g", "FLOAT", "Intensity-averaged G magnitude"),
            ("int_average_bp", "FLOAT", "Intensity-averaged BP magnitude"),
            ("int_average_rp", "FLOAT", "Intensity-averaged RP magnitude"),
            ("peak_to_peak_g", "FLOAT", "Peak-to-peak amplitude in G"),
            ("type_best_classification", "VARCHAR", "DCEP / T2CEP / ACEP"),
            ("mode_best_classification", "VARCHAR", "FUNDAMENTAL / FIRST_OVERTONE / MULTI"),
        ),
    ),
    "gaiadr3.vari_eclipsing_binary": CatalogEntry(
        table_name="gaiadr3.vari_eclipsing_binary",
        service="gaia",
        description="Gaia DR3 eclipsing binary stars",
        columns=_cols(
            ("source_id", "BIGINT", "Gaia source identifier"),
            ("frequency", "DOUBLE", "Orbital frequency [1/day]"),
            ("global_ranking", "FLOAT", "Classification confidence"),
            ("model_type", "VARCHAR", "Eclipsing binary model type"),
            ("num_model_parameters", "INTEGER", "Number of model parameters"),
        ),
    ),
    "gaiadr3.nss_two_body_orbit": CatalogEntry(
        table_name="gaiadr3.nss_two_body_orbit",
        service="gaia",
        description="Gaia DR3 non-single star orbital solutions",
        columns=_cols(
            ("source_id", "BIGINT", "Gaia source identifier"),
            ("nss_solution_type", "VARCHAR", "Solution type (Orbital, AstroSpectroSB1, etc.)"),
            ("period", "DOUBLE", "Orbital period [day]"),
            ("eccentricity", "DOUBLE", "Orbital eccentricity"),
            ("semi_amplitude_primary", "DOUBLE", "RV semi-amplitude of primary [km/s]"),
            ("center_of_mass_velocity", "DOUBLE", "Systemic velocity [km/s]"),
        ),
    ),
    "gaiadr3.astrophysical_parameters": CatalogEntry(
        table_name="gaiadr3.astrophysical_parameters",
        service="gaia",
        description="Gaia DR3 astrophysical parameters from various modules",
        columns=_cols(
            ("source_id", "BIGINT", "Gaia source identifier"),
            ("teff_gspphot", "FLOAT", "Effective temperature [K]"),
            ("logg_gspphot", "FLOAT", "Surface gravity [log(cm/s^2)]"),
            ("mh_gspphot", "FLOAT", "Metallicity [dex]"),
            ("ag_gspphot", "FLOAT", "G-band extinction [mag]"),
            ("teff_gspspec", "FLOAT", "Teff from GSP-Spec [K]"),
            ("logg_gspspec", "FLOAT", "logg from GSP-Spec"),
            ("mh_gspspec", "FLOAT", "[M/H] from GSP-Spec"),
            ("alphafe_gspspec", "FLOAT", "[alpha/Fe] from GSP-Spec"),
            ("spectraltype_esphs", "VARCHAR", "Spectral type from ESP-HS"),
        ),
    ),
    "gaiadr3.qso_candidates": CatalogEntry(
        table_name="gaiadr3.qso_candidates",
        service="gaia",
        description="Gaia DR3 quasar candidates",
        columns=_cols(
            ("source_id", "BIGINT", "Gaia source identifier"),
            ("classprob_dsc_combmod_quasar", "FLOAT", "QSO probability from DSC"),
            ("redshift_qsoc", "FLOAT", "Photometric redshift from QSOC"),
            ("host_galaxy_flag", "SHORT", "Host galaxy flag"),
            ("astrometric_selection_flag", "BOOLEAN", "Selected via astrometry"),
        ),
    ),

    # ── SIMBAD ──
    "basic": CatalogEntry(
        table_name="basic",
        service="simbad",
        description="SIMBAD main table — object classification, coordinates, basic parameters",
        columns=_cols(
            ("main_id", "VARCHAR", "Primary SIMBAD identifier"),
            ("ra", "DOUBLE", "Right ascension ICRS [deg]"),
            ("dec", "DOUBLE", "Declination ICRS [deg]"),
            ("otype", "VARCHAR", "Object type code (G, QSO, *, AGN, Neb, Psr, ...)"),
            ("otype_txt", "VARCHAR", "Object type verbose text"),
            ("rvz_redshift", "DOUBLE", "Redshift"),
            ("rvz_radvel", "DOUBLE", "Radial velocity [km/s]"),
            ("rvz_type", "VARCHAR", "Velocity type (v, z, cz)"),
            ("sp_type", "VARCHAR", "Spectral type"),
            ("morph_type", "VARCHAR", "Morphological type"),
            ("plx_value", "DOUBLE", "Parallax [mas]"),
            ("pmra", "DOUBLE", "Proper motion in RA [mas/yr]"),
            ("pmdec", "DOUBLE", "Proper motion in Dec [mas/yr]"),
            ("nbref", "INTEGER", "Number of references"),
        ),
    ),

    # ── VizieR catalogs ──
    '"IV/39/tic82"': CatalogEntry(
        table_name='"IV/39/tic82"',
        service="vizier",
        description="TESS Input Catalog v8.2 — target star properties for TESS mission",
        columns=_cols(
            ("TIC", "BIGINT", "TESS Input Catalog identifier"),
            ("RAJ2000", "DOUBLE", "Right ascension J2000 [deg]"),
            ("DEJ2000", "DOUBLE", "Declination J2000 [deg]"),
            ("Tmag", "FLOAT", "TESS magnitude [mag]"),
            ("Vmag", "FLOAT", "V magnitude [mag]"),
            ("Bmag", "FLOAT", "B magnitude [mag]"),
            ("Jmag", "FLOAT", "2MASS J magnitude [mag]"),
            ("Hmag", "FLOAT", "2MASS H magnitude [mag]"),
            ("Kmag", "FLOAT", "2MASS Ks magnitude [mag]"),
            ("Teff", "FLOAT", "Effective temperature [K]"),
            ("logg", "FLOAT", "Surface gravity [log(cm/s^2)]"),
            ("MH", "FLOAT", "Metallicity [dex]"),
            ("rad", "FLOAT", "Stellar radius [solar radii]"),
            ("mass", "FLOAT", "Stellar mass [solar masses]"),
            ("plx", "FLOAT", "Parallax [mas]"),
            ("d", "FLOAT", "Distance [pc]"),
            ("e_Teff", "FLOAT", "Teff uncertainty [K]"),
            ("e_logg", "FLOAT", "logg uncertainty"),
            ("e_rad", "FLOAT", "Radius uncertainty [solar radii]"),
            ("e_mass", "FLOAT", "Mass uncertainty [solar masses]"),
            ("lumclass", "VARCHAR", "Luminosity class (DWARF, GIANT, SUBGIANT)"),
            ("objType", "VARCHAR", "Object type (STAR, EXTENDED)"),
        ),
        common_queries=("TESS targets by position", "stellar parameters", "Tmag range filter"),
    ),
    '"II/246/out"': CatalogEntry(
        table_name='"II/246/out"',
        service="vizier",
        description="2MASS All-Sky Catalog of Point Sources",
        columns=_cols(
            ("RAJ2000", "DOUBLE", "Right ascension J2000 [deg]"),
            ("DEJ2000", "DOUBLE", "Declination J2000 [deg]"),
            ("Jmag", "FLOAT", "J magnitude [mag]"),
            ("e_Jmag", "FLOAT", "J magnitude error [mag]"),
            ("Hmag", "FLOAT", "H magnitude [mag]"),
            ("e_Hmag", "FLOAT", "H magnitude error [mag]"),
            ("Kmag", "FLOAT", "Ks magnitude [mag]"),
            ("e_Kmag", "FLOAT", "Ks magnitude error [mag]"),
            ("Qflg", "VARCHAR", "JHK photometric quality flags"),
            ("Rflg", "VARCHAR", "Source of photometry"),
            ("Bflg", "VARCHAR", "Number of blend components"),
        ),
        common_queries=("NIR photometry", "2MASS cross-match", "JHK color-color diagram"),
    ),
    '"II/328/allwise"': CatalogEntry(
        table_name='"II/328/allwise"',
        service="vizier",
        description="AllWISE Source Catalog — mid-infrared photometry",
        columns=_cols(
            ("RAJ2000", "DOUBLE", "Right ascension J2000 [deg]"),
            ("DEJ2000", "DOUBLE", "Declination J2000 [deg]"),
            ("W1mag", "FLOAT", "W1 (3.4um) magnitude [mag]"),
            ("e_W1mag", "FLOAT", "W1 magnitude error [mag]"),
            ("W2mag", "FLOAT", "W2 (4.6um) magnitude [mag]"),
            ("e_W2mag", "FLOAT", "W2 magnitude error [mag]"),
            ("W3mag", "FLOAT", "W3 (12um) magnitude [mag]"),
            ("e_W3mag", "FLOAT", "W3 magnitude error [mag]"),
            ("W4mag", "FLOAT", "W4 (22um) magnitude [mag]"),
            ("e_W4mag", "FLOAT", "W4 magnitude error [mag]"),
            ("Jmag", "FLOAT", "2MASS J magnitude [mag]"),
            ("Hmag", "FLOAT", "2MASS H magnitude [mag]"),
            ("Kmag", "FLOAT", "2MASS Ks magnitude [mag]"),
            ("ccf", "VARCHAR", "Contamination and confusion flag"),
            ("ex", "VARCHAR", "Extended source flag"),
        ),
        common_queries=("MIR photometry", "WISE color selection", "YSO/AGN identification"),
    ),
    # G0.1: SDSS DR12 photometry — reviewer hit this catalog in Paper 5 (Coma
    # red sequence).  Real columns use RA_ICRS/DE_ICRS (NOT RAJ2000/DEJ2000,
    # which the AI kept guessing and getting rejected).  `_RAJ2000` / `_DEJ2000`
    # with leading underscore are VizieR-computed positional columns that
    # work for ANY table — these are a safe fallback the AI can always use.
    '"V/147/sdss12"': CatalogEntry(
        table_name='"V/147/sdss12"',
        service="vizier",
        description="SDSS DR12 Photometric Catalog (primary sources)",
        columns=_cols(
            ("RA_ICRS", "DOUBLE", "Right ascension (ICRS, epoch 2000) [deg]"),
            ("DE_ICRS", "DOUBLE", "Declination (ICRS, epoch 2000) [deg]"),
            ("_RAJ2000", "DOUBLE", "VizieR-computed RA J2000 [deg] — always available"),
            ("_DEJ2000", "DOUBLE", "VizieR-computed Dec J2000 [deg] — always available"),
            ("mode", "INTEGER", "1=primary, 2=secondary source"),
            ("q_mode", "CHAR", "Quality (+ for primary)"),
            ("class", "INTEGER", "Object class (3=galaxy, 6=star, ...)"),
            ("SDSS12", "VARCHAR", "SDSS-DR12 name (JHHMMSS.ss+DDMMSS.s)"),
            ("ObjID", "BIGINT", "Object ID"),
            ("umag", "FLOAT", "Model u magnitude [mag] — CMODEL preferred for galaxies"),
            ("e_umag", "FLOAT", "umag error [mag]"),
            ("gmag", "FLOAT", "Model g magnitude [mag]"),
            ("e_gmag", "FLOAT", "gmag error [mag]"),
            ("rmag", "FLOAT", "Model r magnitude [mag]"),
            ("e_rmag", "FLOAT", "rmag error [mag]"),
            ("imag", "FLOAT", "Model i magnitude [mag]"),
            ("e_imag", "FLOAT", "imag error [mag]"),
            ("zmag", "FLOAT", "Model z magnitude [mag]"),
            ("e_zmag", "FLOAT", "zmag error [mag]"),
            ("zsp", "FLOAT", "Spectroscopic redshift (if observed)"),
            ("e_zsp", "FLOAT", "zsp error"),
            ("zph", "FLOAT", "Photometric redshift (KD-tree method)"),
            ("e_zph", "FLOAT", "zph error"),
            ("photoFlags", "BIGINT", "Photometric flags"),
        ),
        common_queries=(
            "Red-sequence CMD (g-r vs r)",
            "SDSS galaxy selection with class=3",
            "Photometric redshift selection",
        ),
    ),
    # H1.1: SDSS DR17 spectroscopic + photometric (VizieR mirror).
    # Paper 3 reviewer hit this — AI generated RAJ2000 / DEJ2000 / petroMag_r
    # (VizieR convention from old catalogs), but V/154 uses lowercase
    # ra / dec plus the VizieR-computed _RAJ2000 / _DEJ2000.  Real schema
    # from CDS metadata.
    '"V/154/sdss17"': CatalogEntry(
        table_name='"V/154/sdss17"',
        service="vizier",
        description="SDSS DR17 sources (photometric primary, ~4.4B rows)",
        columns=_cols(
            ("_RAJ2000", "DOUBLE", "VizieR-computed RA at J2000 [deg] — always available"),
            ("_DEJ2000", "DOUBLE", "VizieR-computed Dec at J2000 [deg] — always available"),
            ("ra", "DOUBLE", "Right ascension (ICRS, epoch 2000) [deg] — lowercase"),
            ("dec", "DOUBLE", "Declination (ICRS, epoch 2000) [deg] — lowercase"),
            ("objID", "BIGINT", "SDSS object identifier (primary key)"),
            ("mode", "INTEGER", "1=primary, 2=secondary"),
            ("class", "INTEGER", "Object class (3=galaxy, 6=star)"),
            ("subClass", "VARCHAR", "Sub-classification (spectroscopic)"),
            # Photometry (u/g/r/i/z, lowercase — NOT petroMag_r or psfMag_r)
            ("u", "FLOAT", "Model u-band AB magnitude [mag]"),
            ("err_u", "FLOAT", "u magnitude error [mag]"),
            ("g", "FLOAT", "Model g-band AB magnitude [mag]"),
            ("err_g", "FLOAT", "g magnitude error [mag]"),
            ("r", "FLOAT", "Model r-band AB magnitude [mag]"),
            ("err_r", "FLOAT", "r magnitude error [mag]"),
            ("i", "FLOAT", "Model i-band AB magnitude [mag]"),
            ("err_i", "FLOAT", "i magnitude error [mag]"),
            ("z", "FLOAT", "Model z-band AB magnitude [mag] (photometry, NOT redshift)"),
            ("err_z", "FLOAT", "z magnitude error [mag]"),
            # Spectroscopic redshift (for the subset that has SpecObj)
            ("spCl", "VARCHAR", "Spectroscopic class (GALAXY / QSO / STAR)"),
            ("spType", "VARCHAR", "Spectroscopic sub-type"),
            ("zsp", "FLOAT", "Spectroscopic redshift (if observed)"),
            ("e_zsp", "FLOAT", "Spectroscopic redshift error"),
            ("f_zsp", "VARCHAR", "zsp quality flag"),
            ("zph", "FLOAT", "Photometric redshift (KD-tree)"),
            ("e_zph", "FLOAT", "Photometric redshift error"),
            ("plate", "INTEGER", "SDSS plate (spectroscopy)"),
            ("MJD", "INTEGER", "Observation MJD"),
            ("fiberID", "INTEGER", "Spectroscopic fiber ID"),
        ),
        common_queries=(
            "Galaxy luminosity function (class=3, zsp available)",
            "Photometric CMD (u-g vs g-r)",
            "QSO selection (class=3 spCl='QSO' or class=6 + colors)",
        ),
    ),
    # V/139/sdss9 — still widely referenced in legacy papers (earlier DR).
    '"V/139/sdss9"': CatalogEntry(
        table_name='"V/139/sdss9"',
        service="vizier",
        description="SDSS DR9 Photometric Catalog",
        columns=_cols(
            ("RA_ICRS", "DOUBLE", "Right ascension (ICRS, epoch 2000) [deg]"),
            ("DE_ICRS", "DOUBLE", "Declination (ICRS, epoch 2000) [deg]"),
            ("_RAJ2000", "DOUBLE", "VizieR-computed RA J2000 [deg]"),
            ("_DEJ2000", "DOUBLE", "VizieR-computed Dec J2000 [deg]"),
            ("mode", "INTEGER", "1=primary, 2=secondary"),
            ("class", "INTEGER", "Object class (3=galaxy, 6=star)"),
            ("umag", "FLOAT", "u magnitude [mag]"),
            ("gmag", "FLOAT", "g magnitude [mag]"),
            ("rmag", "FLOAT", "r magnitude [mag]"),
            ("imag", "FLOAT", "i magnitude [mag]"),
            ("zmag", "FLOAT", "z magnitude [mag]"),
            ("zph", "FLOAT", "Photometric redshift"),
            ("e_zph", "FLOAT", "Photometric redshift error"),
        ),
        common_queries=("SDSS DR9 color-magnitude", "Star/galaxy separation"),
    ),
    # I/355/gaiadr3 — VizieR mirror of Gaia DR3 (useful when Gaia TAP is down)
    '"I/355/gaiadr3"': CatalogEntry(
        table_name='"I/355/gaiadr3"',
        service="vizier",
        description="Gaia DR3 main source catalog (VizieR mirror)",
        columns=_cols(
            ("RA_ICRS", "DOUBLE", "RA (ICRS) at epoch 2016.0 [deg]"),
            ("DE_ICRS", "DOUBLE", "Dec (ICRS) at epoch 2016.0 [deg]"),
            ("_RAJ2000", "DOUBLE", "VizieR-computed RA at J2000 [deg]"),
            ("_DEJ2000", "DOUBLE", "VizieR-computed Dec at J2000 [deg]"),
            ("Source", "BIGINT", "Gaia source_id"),
            ("Plx", "DOUBLE", "Parallax [mas]"),
            ("pmRA", "DOUBLE", "Proper motion in RA [mas/yr]"),
            ("pmDE", "DOUBLE", "Proper motion in Dec [mas/yr]"),
            ("Gmag", "FLOAT", "G magnitude [mag]"),
            ("BPmag", "FLOAT", "BP magnitude [mag]"),
            ("RPmag", "FLOAT", "RP magnitude [mag]"),
            ("BP-RP", "FLOAT", "BP-RP colour [mag]"),
            ("RUWE", "FLOAT", "Renormalised unit-weight error"),
            ("Teff", "FLOAT", "GSP-Phot effective temperature [K]"),
        ),
        common_queries=("Gaia fallback when TAP is overloaded",),
    ),
    # II/335/galex_ais — UV photometry, commonly used for SED + YSO work
    '"II/335/galex_ais"': CatalogEntry(
        table_name='"II/335/galex_ais"',
        service="vizier",
        description="GALEX All-Sky Imaging Survey (AIS), GR6+GR7",
        columns=_cols(
            ("RAJ2000", "DOUBLE", "Right ascension J2000 [deg]"),
            ("DEJ2000", "DOUBLE", "Declination J2000 [deg]"),
            ("_RAJ2000", "DOUBLE", "VizieR-computed RA J2000 [deg]"),
            ("_DEJ2000", "DOUBLE", "VizieR-computed Dec J2000 [deg]"),
            ("FUV", "FLOAT", "FUV magnitude [AB mag]"),
            ("e_FUV", "FLOAT", "FUV error [mag]"),
            ("NUV", "FLOAT", "NUV magnitude [AB mag]"),
            ("e_NUV", "FLOAT", "NUV error [mag]"),
        ),
    ),
}

# G0.1: common column-name mistakes — helpful for the TAP error path.  When a
# VizieR query fails with "unresolved identifier", describe_tap_table /
# run_adql can grep this list and suggest the right column name.
VIZIER_COMMON_MISTAKES: dict[str, dict[str, str]] = {
    # Wrong → Right (or a note).  Keys are common AI/user guesses; values
    # point to the real canonical column in most VizieR tables or give a
    # safe fallback.
    "RAJ2000": (
        "Check the specific catalog — in SDSS V/147 and V/139 the column "
        "is `RA_ICRS`, not `RAJ2000`.  You can always use the VizieR-"
        "computed `_RAJ2000` (with leading underscore) for any table."
    ),
    "DEJ2000": (
        "Check the specific catalog — in SDSS V/147 and V/139 the column "
        "is `DE_ICRS`, not `DEJ2000`.  You can always use the VizieR-"
        "computed `_DEJ2000` (with leading underscore) for any table."
    ),
    "ra": "VizieR tables almost always use `RAJ2000` or `RA_ICRS` rather than lowercase `ra`. (Exception: V/154/sdss17 DOES use lowercase `ra`/`dec`.)",
    "dec": "VizieR tables almost always use `DEJ2000` or `DE_ICRS` rather than lowercase `dec`. (Exception: V/154/sdss17 DOES use lowercase `ra`/`dec`.)",
    # H1.1: SDSS-specific mistakes the AI keeps making.
    "petroMag_r": "V/154/sdss17 uses simply `r` for the model r-band magnitude. `petroMag_*` is the SDSS native column name but VizieR exposes only `u`/`g`/`r`/`i`/`z`.",
    "petroMag_g": "V/154/sdss17 uses simply `g` for the model g-band magnitude. (Same for u, r, i, z.)",
    "psfMag_r": "V/154/sdss17 uses simply `r` (no `psfMag_` prefix in the VizieR mirror).",
    "psfMag_g": "V/154/sdss17 uses simply `g` (no `psfMag_` prefix in the VizieR mirror).",
    "redshift": "V/154/sdss17 uses `zsp` for spectroscopic redshift and `zph` for photometric. Generic `redshift` is NOT a column.",
    "objid": "V/154/sdss17 uses `objID` (capital ID). Case matters in TAP.",
}


def suggest_for_missing_column(column: str) -> str | None:
    """If `column` is a well-known wrong name, return a hint string."""
    return VIZIER_COMMON_MISTAKES.get(column)


def get_catalog(table_name: str) -> CatalogEntry | None:
    """Look up a catalog by table name. Returns None if not in registry."""
    return CATALOG_REGISTRY.get(table_name)


def get_columns(table_name: str) -> list[CatalogColumn] | None:
    """Return column list for a registered table, or None."""
    entry = CATALOG_REGISTRY.get(table_name)
    return list(entry.columns) if entry else None


def validate_columns(table_name: str, column_names: list[str]) -> list[str]:
    """Return column names that do NOT exist in the registry for the given table.

    Returns an empty list if the table is not in the registry (no validation possible).
    """
    entry = CATALOG_REGISTRY.get(table_name)
    if entry is None:
        return []
    known = {c.name.lower() for c in entry.columns}
    return [c for c in column_names if c.lower() not in known]


# ══════════════════════════════════════════════════════════════════════
# J3 — SDSS SkyServer T-SQL schema cheatsheet
# ══════════════════════════════════════════════════════════════════════
# Shown to the AI in SYSTEM_PROMPT so it doesn't guess column names on
# the `run_sdss_sql` path.  SDSS uses T-SQL (Microsoft SQL Server), NOT
# ADQL, and column naming is different from VizieR's `V/154/sdss17` view.
# Source: https://skyserver.sdss.org/dr18/en/help/browser/browser.aspx
SDSS_DR18_SCHEMA: dict[str, dict] = {
    "PhotoObjAll": {
        "description": (
            "SDSS photometric catalog (~500 M detections across DR18). "
            "ALWAYS filter `mode = 1 AND clean = 1` to drop secondary + artefacts."
        ),
        "key_columns": [
            "objID (bigint, primary key)", "ra", "dec",
            "type (3=galaxy, 6=star, 0=unknown)", "mode (1=primary)", "clean (1=no artefacts)",
            "u, g, r, i, z (model magnitudes)",
            "psfMag_u..z (PSF magnitudes, point sources)",
            "petroMag_u..z (Petrosian magnitudes, extended sources)",
            "petroR50_r (Petrosian half-light radius in r)",
            "petroRad_r (Petrosian radius in r, arcsec)",
            "expRad_r, deVRad_r (exponential / de Vaucouleurs profile scale)",
            "fracDeV_r (0=pure exponential, 1=pure de Vaucouleurs)",
        ],
    },
    "SpecObjAll": {
        "description": (
            "SDSS spectroscopic catalog (~5 M galaxies/QSOs/stars with spec-z). "
            "Filter `zWarning = 0 AND class = 'GALAXY'` (or 'QSO' / 'STAR')."
        ),
        "key_columns": [
            "specObjID (bigint primary)", "bestObjID (link to PhotoObjAll.objID)",
            "plate, mjd, fiberID (plate coordinates)",
            "ra, dec",
            "z (spec redshift)", "zErr", "zWarning (0 = reliable)",
            "class ('GALAXY' / 'QSO' / 'STAR')",
            "subClass (morphology / stellar type)",
            "snMedian_r (spectrum S/N)",
        ],
    },
    "Photoz": {
        "description": (
            "SDSS photometric redshifts (kd-tree kNN, ~200 M objects). "
            "Join on `objID = PhotoObjAll.objID`."
        ),
        "key_columns": [
            "objID", "z (photo-z estimate)", "zErr",
            "nnCount (number of neighbours used)", "photoErrorClass (1=best)",
            "chisq (kNN chi-squared)",
        ],
    },
    "GalSpecInfo": {
        "description": (
            "MPA-JHU galaxy properties (Tremonti, Brinchmann et al.). "
            "Join on `specObjID = SpecObjAll.specObjID`. "
            "Contains stellar masses, metallicities, SFRs derived from spectra."
        ),
        "key_columns": [
            "specObjID", "plateid, mjd, fiberid",
            "ra, dec", "z (spec redshift from pipeline)",
            "reliable (1 = reliable classification)",
            "spectrotype (e.g. 'GALAXY' / 'STARFORMING' / 'AGN' / 'COMPOSITE')",
        ],
    },
    "GalSpecExtra": {
        "description": (
            "MPA-JHU derived quantities (stellar mass, SFR, metallicity)."
        ),
        "key_columns": [
            "specObjID",
            "lgm_tot_p50 (log10 total stellar mass, p50 percentile)",
            "lgm_tot_p16, lgm_tot_p84 (1-sigma range)",
            "sfr_tot_p50 (log10 SFR total, solar/yr)",
            "specsfr_tot_p50 (log10 specific SFR)",
            "oh_p50 (metallicity 12+log(O/H))",
            "bptclass (1=SF, 2=Low S/N SF, 3=Composite, 4=AGN, 5=Low S/N LINER)",
        ],
    },
    "Field": {
        "description": "SDSS field metadata (run, camcol, rerun, field coords + seeing).",
        "key_columns": [
            "fieldID", "run, rerun, camcol, field",
            "raMin, raMax, decMin, decMax (field footprint)",
            "psfWidth_u..z (PSF FWHM, arcsec)",
            "nStars_u..z, nGals_u..z",
        ],
    },
}


SDSS_TSQL_IDIOMS: list[str] = [
    # 常见正确写法, 加在 prompt 里供 AI 参考.
    "SELECT TOP 1000 objID, ra, dec, r FROM PhotoObjAll "
    "WHERE mode=1 AND clean=1 AND r BETWEEN 17 AND 21",
    "SELECT TOP 500 p.objID, p.ra, p.dec, p.r, s.z, s.class "
    "FROM PhotoObjAll p "
    "JOIN SpecObjAll s ON s.bestObjID = p.objID "
    "WHERE p.mode=1 AND s.zWarning=0 AND s.class='GALAXY'",
    "SELECT TOP 200 p.objID, p.ra, p.dec, p.petroMag_r, pz.z, pz.zErr "
    "FROM PhotoObjAll p JOIN Photoz pz ON pz.objID = p.objID "
    "JOIN dbo.fGetNearbyObjEq(194.95, 27.98, 60) AS n ON n.objID = p.objID "
    "WHERE p.mode=1 AND p.clean=1 AND p.type=3",
    "SELECT TOP 500 s.specObjID, s.z, g.lgm_tot_p50, g.sfr_tot_p50 "
    "FROM SpecObjAll s JOIN GalSpecExtra g ON g.specObjID = s.specObjID "
    "WHERE s.class='GALAXY' AND s.zWarning=0",
]
