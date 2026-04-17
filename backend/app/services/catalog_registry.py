"""Static metadata registry for commonly used astronomical catalogs.

Provides fast local lookups for column names, types, and descriptions
without requiring a runtime TAP_SCHEMA query. Used by describe_tap_table
as a fast-path cache and by adql_dialect for column validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
}


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
