"""JPL Horizons connector — authoritative solar system ephemeris.

Reference: Giorgini+ 1996 BAAS 28, 1158 (JPL Horizons system).
Uses astroquery.jplhorizons as the standard Python interface
(Ginsburg+ 2019 AJ 157, 98).

Provides ephemerides for planets, asteroids, comets, spacecraft.
"""

from __future__ import annotations

import asyncio
import logging

from app.connectors.base import AstroObject, BaseConnector, FITSFile

logger = logging.getLogger(__name__)


class JPLHorizonsConnector(BaseConnector):
    """Query JPL Horizons for solar system body ephemerides."""

    source_name = "jpl"

    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None,
        radius: float = 0.1,
    ) -> list[AstroObject]:
        """Look up a solar system body by name/designation.

        Returns the current position (RA/Dec in ICRS at J2000.0) and
        basic orbital elements as an AstroObject.
        """
        if not query:
            return []

        def _query():
            try:
                from astroquery.jplhorizons import Horizons
                from datetime import datetime, timedelta, timezone
                # Horizons requires start < stop.  Use now -> now+1d so a single
                # ephemeris row is returned.  Previous code set stop to the 1st
                # of the current month, which was earlier than start on every
                # day except the 1st — producing an empty/error response for
                # ~30 days every month.
                now_dt = datetime.now(timezone.utc)
                now = now_dt.isoformat()[:10]
                stop = (now_dt + timedelta(days=1)).isoformat()[:10]
                obj = Horizons(
                    id=query,
                    location="500@10",  # heliocenter
                    epochs={"start": now, "stop": stop, "step": "1d"},
                )
                eph = obj.ephemerides()
                if eph is None or len(eph) == 0:
                    return []
                row = eph[0]
                return [{
                    "name": str(row.get("targetname", query)),
                    "ra": float(row.get("RA", 0.0)),
                    "dec": float(row.get("DEC", 0.0)),
                    "V": float(row.get("V", 0.0)) if "V" in row.colnames else None,
                    "r_au": float(row.get("r", 0.0)) if "r" in row.colnames else None,
                    "delta_au": float(row.get("delta", 0.0)) if "delta" in row.colnames else None,
                }]
            except Exception as e:
                logger.warning("JPL Horizons query failed for %s: %s", query, e)
                return []

        loop = asyncio.get_running_loop()
        rows = await asyncio.wait_for(
            loop.run_in_executor(None, _query), timeout=30.0,
        )

        return [
            AstroObject(
                source=self.source_name,
                object_id=str(r["name"]),
                name=str(r["name"]),
                ra=r["ra"],
                dec=r["dec"],
                object_type="solar_system_body",
                magnitude=r.get("V"),
                extra={
                    "heliocentric_distance_au": r.get("r_au"),
                    "geocentric_distance_au": r.get("delta_au"),
                    "source_reference": "JPL Horizons (Giorgini+ 1996 BAAS 28, 1158)",
                },
            )
            for r in rows
        ]

    async def fetch(self, object_id: str) -> FITSFile:
        raise NotImplementedError("JPL Horizons does not serve FITS files")

    def normalize(self, raw_data):
        from astropy.table import Table
        return Table()
