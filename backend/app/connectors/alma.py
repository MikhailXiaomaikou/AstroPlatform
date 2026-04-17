"""ALMA (Atacama Large Millimeter/submillimeter Array) archive connector."""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

if TYPE_CHECKING:
    from astropy.table import Table

logger = logging.getLogger(__name__)


class ALMAConnector(BaseConnector):
    source_name = "alma"

    @with_retry(max_retries=2, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        import asyncio
        from functools import partial

        from astropy.coordinates import SkyCoord
        import astropy.units as u
        from astroquery.alma import Alma

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
            partial(Alma.query_region, coord, radius=radius * u.deg),
        )

        if table is None or len(table) == 0:
            return []

        results = []
        for row in table[:50]:
            obs_id = str(row["obs_id"]) if "obs_id" in table.colnames else ""
            target = str(row["target_name"]) if "target_name" in table.colnames else obs_id
            obj_ra = float(row["s_ra"]) if "s_ra" in table.colnames else 0.0
            obj_dec = float(row["s_dec"]) if "s_dec" in table.colnames else 0.0

            extra: dict = {}
            if "frequency" in table.colnames:
                try:
                    extra["frequency_ghz"] = float(row["frequency"])
                except (ValueError, TypeError):
                    pass
            if "bandwidth" in table.colnames:
                try:
                    extra["bandwidth_ghz"] = float(row["bandwidth"])
                except (ValueError, TypeError):
                    pass
            if "t_exptime" in table.colnames:
                try:
                    extra["exposure_s"] = float(row["t_exptime"])
                except (ValueError, TypeError):
                    pass
            if "dataproduct_type" in table.colnames:
                extra["product_type"] = str(row["dataproduct_type"])
            if "band_list" in table.colnames:
                extra["band"] = str(row["band_list"])

            results.append(AstroObject(
                source="alma",
                object_id=obs_id,
                name=target,
                ra=obj_ra,
                dec=obj_dec,
                object_type="radio_observation",
                extra=extra,
            ))

        return results

    @with_retry(max_retries=2, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Create metadata FITS for an ALMA observation.

        Full ALMA data products are large (GB+) and require archive access.
        We provide a metadata FITS placeholder.
        """
        from astropy.io import fits

        hdu = fits.PrimaryHDU()
        hdu.header["OBJECT"] = object_id
        hdu.header["TELESCOP"] = "ALMA"
        hdu.header["COMMENT"] = "Metadata-only. Download full data from almascience.eso.org"
        buf = io.BytesIO()
        hdu.writeto(buf)
        return FITSFile(
            object_id=object_id,
            source="alma",
            data=buf.getvalue(),
            filename=f"alma_{object_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        from astropy.table import Table as AstropyTable
        if isinstance(raw_data, list):
            return AstropyTable(rows=raw_data)
        return AstropyTable(raw_data)
