"""NASA/IPAC IRSA (Infrared Science Archive) connector.

Provides access to Spitzer, WISE, 2MASS, and other infrared survey catalogs
via astroquery.ipac.irsa.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

if TYPE_CHECKING:
    from astropy.table import Table

logger = logging.getLogger(__name__)


class IRSAConnector(BaseConnector):
    source_name = "irsa"

    # Default catalog for cone searches (AllWISE Source Catalog)
    DEFAULT_CATALOG = "allwise_p3as_psd"

    @with_retry(max_retries=2, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        import asyncio
        from functools import partial

        from astropy.coordinates import SkyCoord
        import astropy.units as u
        from astroquery.ipac.irsa import Irsa

        # Resolve coordinates
        if ra is None or dec is None:
            try:
                coord = SkyCoord.from_name(query)
            except (ValueError, Exception) as e:
                logger.debug("SkyCoord.from_name failed for %r: %s", query, e)
                parts = query.replace(",", " ").split()
                if len(parts) >= 2:
                    try:
                        coord = SkyCoord(float(parts[0]), float(parts[1]), unit="deg")
                    except ValueError:
                        return []
                else:
                    return []
        else:
            coord = SkyCoord(ra, dec, unit="deg")

        # astroquery is synchronous — run in thread pool
        loop = asyncio.get_event_loop()
        table = await loop.run_in_executor(
            None,
            partial(
                Irsa.query_region,
                coord,
                catalog=self.DEFAULT_CATALOG,
                radius=radius * u.deg,
            ),
        )

        if table is None or len(table) == 0:
            return []

        results = []
        for row in table[:50]:
            # AllWISE catalog columns
            obj_id = ""
            for col in ("designation", "source_id", "cntr"):
                if col in table.colnames:
                    obj_id = str(row[col])
                    break
            if not obj_id:
                obj_id = str(row[table.colnames[0]])

            name = ""
            for col in ("designation", "source_id"):
                if col in table.colnames:
                    name = str(row[col])
                    break
            if not name:
                name = obj_id

            obj_ra = 0.0
            for col in ("ra", "RA", "clon"):
                if col in table.colnames:
                    try:
                        obj_ra = float(row[col])
                    except (ValueError, TypeError):
                        pass
                    break

            obj_dec = 0.0
            for col in ("dec", "DEC", "clat"):
                if col in table.colnames:
                    try:
                        obj_dec = float(row[col])
                    except (ValueError, TypeError):
                        pass
                    break

            extra: dict = {}
            # WISE magnitudes
            for band in ("w1mpro", "w2mpro", "w3mpro", "w4mpro"):
                if band in table.colnames:
                    try:
                        extra[band] = float(row[band])
                    except (ValueError, TypeError):
                        pass
            # 2MASS magnitudes
            for band in ("j_m_2mass", "h_m_2mass", "k_m_2mass"):
                if band in table.colnames:
                    try:
                        extra[band] = float(row[band])
                    except (ValueError, TypeError):
                        pass

            magnitude = None
            if "w1mpro" in extra:
                magnitude = extra["w1mpro"]

            results.append(AstroObject(
                source="irsa",
                object_id=obj_id,
                name=name,
                ra=obj_ra,
                dec=obj_dec,
                object_type="ir_source",
                magnitude=magnitude,
                extra=extra,
            ))

        return results

    @with_retry(max_retries=2, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Reject metadata-only stand-ins for IRSA image products."""
        raise NotImplementedError(
            "IRSA catalog metadata is not a FITS image data product; this "
            "connector will not fabricate an empty FITS file. Request a real "
            "cutout from the appropriate IRSA image service and upload it."
        )

    def normalize(self, raw_data) -> Table:
        from astropy.table import Table as AstropyTable
        if isinstance(raw_data, list):
            return AstropyTable(rows=raw_data)
        return AstropyTable(raw_data)
