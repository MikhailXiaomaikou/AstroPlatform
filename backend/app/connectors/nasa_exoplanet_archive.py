"""NASA Exoplanet Archive connector via astroquery.ipac.nexsci.

Reference: NASA Exoplanet Archive (Akeson+ 2013 PASP 125, 989, bibcode
2013PASP..125..989A). Hosts confirmed planet parameters + Kepler/K2/TESS
candidate tables. Accessed via astroquery.ipac.nexsci.nasa_exoplanet_archive
(Ginsburg+ 2019 AJ 157, 98, bibcode 2019AJ....157...98G).

M0 (2026-05-20): new connector for the 3rd active module (exoplanet).
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from astropy.table import Table

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry
from app.services.provenance_v2.ivoa_dataorigin_resolver import resolve_ivoa_dataorigin

logger = logging.getLogger(__name__)

NEA_ARCHIVE_VERSION = "nasa-exoplanet-archive-2026"

# Confirmed planet parameter fields (pscomppars table — composite parameters).
# Schema: https://exoplanetarchive.ipac.caltech.edu/docs/API_PS_columns.html
_PLANET_FIELDS = (
    "pl_name",          # planet name
    "hostname",         # host star name
    "pl_orbper",        # orbital period (days)
    "pl_rade",          # planet radius (Earth radii)
    "pl_radj",          # planet radius (Jupiter radii)
    "pl_bmasse",        # planet mass (Earth masses)
    "pl_bmassj",        # planet mass (Jupiter masses)
    "pl_orbsmax",       # semi-major axis (au)
    "pl_orbeccen",      # eccentricity
    "pl_eqt",           # equilibrium temperature (K)
    "pl_insol",         # insolation flux (Earth = 1)
    "st_teff",          # stellar effective temperature (K)
    "st_rad",           # stellar radius (solar radii)
    "st_mass",          # stellar mass (solar masses)
    "st_logg",          # log surface gravity
    "st_met",           # metallicity [Fe/H]
    "sy_dist",          # system distance (pc)
    "ra",
    "dec",
    "discoverymethod",
)


class NASAExoplanetArchiveConnector(BaseConnector):
    """Query NASA Exoplanet Archive for confirmed planet parameters."""

    source_name = "nasa_exoplanet_archive"

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None,
        radius: float = 0.1,
    ) -> list[AstroObject]:
        if not query:
            return []
        loop = asyncio.get_running_loop()
        table = await asyncio.wait_for(
            loop.run_in_executor(None, partial(self._query_nea, query)),
            timeout=30.0,
        )
        if table is None or len(table) == 0:
            return []
        return self._table_to_objects(table, query=query)

    def _query_nea(self, query: str) -> Table | None:
        """Query NASA Exoplanet Archive pscomppars table by planet name or hostname.

        Tries planet name first ("HD 209458 b"), then hostname ("HD 209458") which
        returns all planets in that system.
        """
        from astroquery.ipac.nexsci.nasa_exoplanet_archive import NasaExoplanetArchive

        # try planet name first
        for column in ("pl_name", "hostname"):
            try:
                raw = NasaExoplanetArchive.query_criteria(
                    table="pscomppars",
                    where=f"{column}='{query}'",
                    select=",".join(_PLANET_FIELDS),
                )
            except Exception as exc:
                logger.debug("NEA %s query failed for %r: %s", column, query, exc)
                raw = None
            if raw is not None and len(raw) > 0:
                return raw
        return None

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        raise NotImplementedError(
            "NASA Exoplanet Archive does not serve FITS; use query_exoplanet_archive "
            "ai_tool for planet parameter JSON."
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    def _table_to_objects(
        self, table: Table, *, query: str = "",
    ) -> list[AstroObject]:
        objects: list[AstroObject] = []
        provenance_dataset = resolve_ivoa_dataorigin(
            table,
            service_hint="nasa_exoplanet_archive",
            archive_version=NEA_ARCHIVE_VERSION,
        )
        for row in table:
            name = query
            if "pl_name" in row.colnames:
                try:
                    candidate = str(row["pl_name"]).strip()
                    if candidate and candidate.lower() not in ("none", "nan", "--"):
                        name = candidate
                except (ValueError, TypeError):
                    pass

            extra: dict = {}
            if provenance_dataset:
                extra["_provenance_dataset"] = provenance_dataset

            for key in _PLANET_FIELDS:
                if key in row.colnames:
                    try:
                        extra[key] = float(row[key])
                    except (ValueError, TypeError):
                        try:
                            val = str(row[key]).strip()
                            if val and val.lower() not in ("none", "nan", "--", "masked"):
                                extra[key] = val
                        except Exception:
                            pass

            extra["source_reference"] = (
                "NASA Exoplanet Archive (Akeson+ 2013 PASP 125, 989) — "
                "confirmed planet composite-parameters table"
            )

            ra_val = extra.get("ra") if isinstance(extra.get("ra"), (int, float)) else 0.0
            dec_val = extra.get("dec") if isinstance(extra.get("dec"), (int, float)) else 0.0

            objects.append(
                AstroObject(
                    source=self.source_name,
                    object_id=name,
                    name=name,
                    ra=float(ra_val),
                    dec=float(dec_val),
                    object_type="exoplanet",
                    magnitude=None,
                    extra=extra,
                )
            )
        return objects
