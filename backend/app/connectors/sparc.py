"""SPARC rotation curve connector.

Reference: Lelli, McGaugh & Schombert 2016 AJ 152, 157
"SPARC: Mass Models for 175 Disk Galaxies with Spitzer Photometry
and Accurate Rotation Curves"

Accesses via VizieR catalog J/AJ/152/157.
"""

from __future__ import annotations

import asyncio
import logging

from app.connectors.base import AstroObject, BaseConnector, FITSFile

logger = logging.getLogger(__name__)

SPARC_VIZIER_ID = "J/AJ/152/157"


class SPARCConnector(BaseConnector):
    """Query SPARC disk galaxy catalogue via VizieR."""

    source_name = "sparc"

    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None,
        radius: float = 1.0,
    ) -> list[AstroObject]:
        """Look up a SPARC galaxy by name (e.g. "NGC 3198") or coordinates.

        Returns the galaxy-level metadata (distance, disk scale length,
        inclination, total HI mass, etc.). For the actual rotation curve
        data, use `fetch()` with the galaxy name.
        """
        def _query():
            try:
                from astroquery.vizier import Vizier
            except ImportError:
                logger.warning("astroquery not installed")
                return []

            Vizier.ROW_LIMIT = 500
            try:
                if query:
                    # Table "J/AJ/152/157/table1" has galaxy metadata
                    result = Vizier.query_object(query, catalog=SPARC_VIZIER_ID)
                elif ra is not None and dec is not None:
                    from astropy.coordinates import SkyCoord
                    from astropy import units as u
                    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
                    result = Vizier.query_region(
                        coord, radius=radius * u.deg, catalog=SPARC_VIZIER_ID,
                    )
                else:
                    return []
                if result is None or len(result) == 0:
                    return []
                # Pick the metadata table
                table = result[0]
                rows = []
                for row in table:
                    rows.append({k: row[k] for k in table.colnames if k in row.colnames})
                return rows
            except Exception as e:
                logger.warning("SPARC Vizier query failed: %s", e)
                return []

        loop = asyncio.get_running_loop()
        rows = await asyncio.wait_for(
            loop.run_in_executor(None, _query), timeout=30.0,
        )

        results = []
        for r in rows:
            try:
                name = str(r.get("Galaxy", r.get("Name", ""))).strip()
                if not name:
                    continue
                ra_val = float(r.get("RAJ2000", r.get("_RAJ2000", 0.0)))
                dec_val = float(r.get("DEJ2000", r.get("_DEJ2000", 0.0)))
                results.append(AstroObject(
                    source=self.source_name,
                    object_id=name,
                    name=name,
                    ra=ra_val,
                    dec=dec_val,
                    object_type="disk_galaxy",
                    extra={
                        "distance_Mpc": float(r["Dist"]) if r.get("Dist") is not None else None,
                        "inclination_deg": float(r["Inc"]) if r.get("Inc") is not None else None,
                        "L36_Lsun": float(r["L3.6"]) if r.get("L3.6") is not None else None,
                        "disk_scale_kpc": float(r["Rd"]) if r.get("Rd") is not None else None,
                        "HI_mass_Msun": float(r["MHI"]) if r.get("MHI") is not None else None,
                        "Vflat_km_s": float(r["Vf"]) if r.get("Vf") is not None else None,
                        "hubble_type": str(r.get("T", "")),
                        "source_reference": "SPARC (Lelli+ 2016 AJ 152, 157)",
                    },
                ))
            except (ValueError, TypeError, KeyError):
                continue
        return results

    async def fetch(self, object_id: str) -> FITSFile:
        raise NotImplementedError("SPARC rotation curves are tabular, not FITS")

    def normalize(self, raw_data):
        from astropy.table import Table
        return Table()
