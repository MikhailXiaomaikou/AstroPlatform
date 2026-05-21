"""Machine-readable source-mapping status for provenance-v2.

This registry is intentionally descriptive rather than executable.  It gives
the UI, docs, tests, and future maintenance jobs one place to answer:

* which archive connectors are provenance-v2 active,
* which connectors remain maintenance-gated,
* which literature table seeds are verified as measurement-producing, and
* how far the curated cosmology likelihood registry has progressed.

Do not put API keys, credentials, or live data payloads here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


ArchiveMappingStatus = Literal["active", "gated"]
MeasurementMappingStatus = Literal["verified_fit_ready", "pending_verification"]


@dataclass(frozen=True)
class ArchiveSourceMapping:
    key: str
    display_name: str
    status: ArchiveMappingStatus
    provenance_layer: str
    measurement_scope: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiteratureMeasurementSeed:
    key: str
    arxiv_id: str
    display_name: str
    line_id: str
    status: MeasurementMappingStatus
    expected_line_measurements: int | None
    usable_for_fit_line_lfr: bool
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ACTIVE_ARCHIVE_MAPPINGS: tuple[ArchiveSourceMapping, ...] = (
    ArchiveSourceMapping(
        key="vizier",
        display_name="VizieR",
        status="active",
        provenance_layer="IVOA DataOrigin when available + registry fallback",
        measurement_scope="catalog metadata and rows; table-level citations",
        notes="2MASS II/246 may route through VizieR but resolves to the 2mass service key.",
    ),
    ArchiveSourceMapping(
        key="gaia",
        display_name="Gaia DR3",
        status="active",
        provenance_layer="PARAM scanner + registry supplement",
        measurement_scope="Gaia archive rows and table-level Gaia DR3 citation",
        notes="gaia_dr3 aliases normalize to gaia.",
    ),
    ArchiveSourceMapping(
        key="simbad",
        display_name="SIMBAD",
        status="active",
        provenance_layer="project registry table-level + field bibcode extraction",
        measurement_scope="object metadata plus SIMBAD-family per-field bibcodes when present",
        notes="SIMBAD itself emits no DataOrigin metadata; registry is the table-level fallback.",
    ),
    ArchiveSourceMapping(
        key="ned",
        display_name="NED",
        status="active",
        provenance_layer="non-standard INFO scanner + registry supplement",
        measurement_scope="extragalactic object metadata and table-level NED citation",
        notes="NED is active for metadata; derived science claims still need tool-supported rows.",
    ),
    ArchiveSourceMapping(
        key="2mass",
        display_name="2MASS",
        status="active",
        provenance_layer="VizieR II/246 path + registry fallback",
        measurement_scope="near-infrared catalog rows and table-level 2MASS citation",
        notes="twomass, two_mass, and II/246 aliases normalize to 2mass.",
    ),
    ArchiveSourceMapping(
        key="alma",
        display_name="ALMA Science Archive",
        status="active",
        provenance_layer="ObsCore archive metadata + registry fallback",
        measurement_scope="observation metadata only; no derived L[line] or FWHM",
        notes="No derived line luminosity or FWHM; those claims require literature measurement tables or a dedicated line-measurement source.",
    ),
    ArchiveSourceMapping(
        key="jpl",
        display_name="JPL Horizons",
        status="active",
        provenance_layer="Horizons API + registry fallback",
        measurement_scope="solar-system body ephemerides (geocentric/topocentric vectors and orbital elements)",
        notes="Promoted to provenance-v2 active with the Solar System M0 module (2026-05-18). Backs the fetch_horizons_ephemeris tool; reference Giorgini et al. 1996 (1996DPS....28.2504G). Only surfaced when ASTRO_RESEARCH_FOCUS=solar_system.",
    ),
    ArchiveSourceMapping(
        key="mpc",
        display_name="IAU Minor Planet Center",
        status="active",
        provenance_layer="MPC orbit database + registry fallback",
        measurement_scope="asteroid and comet orbital elements (osculating + designation metadata)",
        notes="Promoted to provenance-v2 active with the Solar System M0 module (2026-05-18). Backs the query_mpc_orbit tool. Only surfaced when ASTRO_RESEARCH_FOCUS=solar_system.",
    ),
    ArchiveSourceMapping(
        key="nasa_exoplanet_archive",
        display_name="NASA Exoplanet Archive",
        status="active",
        provenance_layer="NExScI/IPAC archive tables + registry fallback",
        measurement_scope="confirmed exoplanet, candidate, and TESS target metadata plus table-level archive citation",
        notes="Promoted to provenance-v2 active with the Exoplanet M0 module (2026-05-20). Backs query_exoplanet_archive and query_confirmed_planets. Only surfaced when ASTRO_RESEARCH_FOCUS=exoplanet.",
    ),
)


GATED_ARCHIVE_SOURCES: tuple[str, ...] = (
    "sdss",
    "sdss_spec",
    "mast",
    "chandra",
    "allwise",
    "eso",
    "irsa",
    "jwst",
    "lamost",
    "desi",
    "panstarrs",
    "xmm",
    "nvss",
    "first",
    "atnf_pulsar",
    "sparc",
    "frbstats",
)


VERIFIED_LITERATURE_MEASUREMENT_SEEDS: tuple[LiteratureMeasurementSeed, ...] = (
    LiteratureMeasurementSeed(
        key="alpine_bethermin_2020_cii",
        arxiv_id="2002.00962",
        display_name="Béthermin et al. 2020 ALPINE [CII] master sample",
        line_id="[CII]",
        status="verified_fit_ready",
        expected_line_measurements=74,
        usable_for_fit_line_lfr=True,
        notes="End-to-end verified through extract_literature_tables -> line_measurements -> fit_line_lfr.",
    ),
)


PENDING_LITERATURE_MEASUREMENT_SEEDS: tuple[LiteratureMeasurementSeed, ...] = (
    LiteratureMeasurementSeed(
        key="rebels_cii_line_tables",
        arxiv_id="candidate:2202.04080-or-2202.10464",
        display_name="REBELS [CII] line-measurement tables",
        line_id="[CII]",
        status="pending_verification",
        expected_line_measurements=None,
        usable_for_fit_line_lfr=False,
        notes="Candidate papers must be run through table extraction and confirmed to produce line_measurements > 0.",
    ),
    LiteratureMeasurementSeed(
        key="capak_2015_cii",
        arxiv_id="candidate:1503.07596",
        display_name="Capak et al. 2015 [CII]",
        line_id="[CII]",
        status="pending_verification",
        expected_line_measurements=None,
        usable_for_fit_line_lfr=False,
        notes="Do not cite or preload until the parser produces row-level measurements.",
    ),
    LiteratureMeasurementSeed(
        key="bothwell_2013_spt_cii",
        arxiv_id="candidate:1304.4256",
        display_name="Bothwell et al. 2013 SPT [CII]",
        line_id="[CII]",
        status="pending_verification",
        expected_line_measurements=None,
        usable_for_fit_line_lfr=False,
        notes="Earlier remembered arXiv IDs were wrong; keep as pending until verified locally.",
    ),
)


def list_archive_source_mappings() -> dict[str, Any]:
    active = [entry.to_dict() for entry in ACTIVE_ARCHIVE_MAPPINGS]
    gated = [
        {
            "key": key,
            "display_name": _display_name_for_gated(key),
            "status": "gated",
            "provenance_layer": "not yet upgraded to provenance-v2",
            "measurement_scope": "unavailable until connector gets an M3-style provenance upgrade",
            "notes": "Dispatch returns the UNAVAILABLE maintenance banner before connector import.",
        }
        for key in GATED_ARCHIVE_SOURCES
    ]
    return {
        "active_count": len(active),
        "gated_count": len(gated),
        "active": active,
        "gated": gated,
    }


def list_literature_measurement_mappings() -> dict[str, Any]:
    verified = [entry.to_dict() for entry in VERIFIED_LITERATURE_MEASUREMENT_SEEDS]
    pending = [entry.to_dict() for entry in PENDING_LITERATURE_MEASUREMENT_SEEDS]
    return {
        "verified_count": len(verified),
        "pending_count": len(pending),
        "verified": verified,
        "pending": pending,
    }


def source_mapping_summary() -> dict[str, Any]:
    archive = list_archive_source_mappings()
    literature = list_literature_measurement_mappings()

    try:
        from app.services.cosmology_likelihoods import list_cosmology_datasets

        cosmology_count = int(list_cosmology_datasets().get("dataset_count", 0))
    except Exception:
        cosmology_count = 0

    return {
        "archive_active_count": archive["active_count"],
        "archive_gated_count": archive["gated_count"],
        "literature_verified_seed_count": literature["verified_count"],
        "literature_pending_seed_count": literature["pending_count"],
        "cosmology_dataset_count": cosmology_count,
        "status": {
            "archive_sources": "first-stage provenance-v2 mapping complete for active sources",
            "literature_measurements": "verified ALPINE seed available; multi-survey [CII] still pending",
            "cosmology_likelihoods": "metadata/config registry available; chains required before claims",
        },
    }


def _display_name_for_gated(key: str) -> str:
    return {
        "sdss": "SDSS",
        "sdss_spec": "SDSS spectra",
        "mast": "MAST",
        "chandra": "Chandra",
        "allwise": "AllWISE",
        "eso": "ESO",
        "irsa": "IRSA",
        "jwst": "JWST",
        "lamost": "LAMOST",
        "desi": "DESI",
        "panstarrs": "Pan-STARRS",
        "xmm": "XMM-Newton",
        "nvss": "NVSS",
        "first": "FIRST",
        "atnf_pulsar": "ATNF PSRCAT",
        "sparc": "SPARC",
        "frbstats": "FRBSTATS",
    }.get(key, key)
