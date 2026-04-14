"""ESO (European Southern Observatory) archive connector.

Provides access to optical/IR spectroscopy data from VLT, VLTI, and other
ESO telescopes via astroquery.eso.
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

if TYPE_CHECKING:
    from astropy.table import Table

logger = logging.getLogger(__name__)


class ESOConnector(BaseConnector):
    source_name = "eso"

    @with_retry(max_retries=2, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        import asyncio
        from functools import partial

        from astropy.coordinates import SkyCoord
        from astroquery.eso import Eso

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

        eso = Eso()
        eso.ROW_LIMIT = 50

        # astroquery is synchronous — run in thread pool
        loop = asyncio.get_event_loop()
        table = await loop.run_in_executor(
            None,
            partial(
                eso.query_instrument,
                "xshooter",
                coord,
                column_filters={"stime": ">0"},
            ),
        )

        if table is None or len(table) == 0:
            # Fall back to a general survey query
            table = await loop.run_in_executor(
                None,
                partial(
                    eso.query_surveys,
                    "VPHAS",
                    coord,
                ),
            )

        if table is None or len(table) == 0:
            return []

        results = []
        for row in table[:50]:
            # ESO tables vary by instrument; try common column names
            obs_id = ""
            for col in ("dp_id", "arcfile", "ARCFILE", "DP.ID"):
                if col in table.colnames:
                    obs_id = str(row[col])
                    break
            if not obs_id:
                obs_id = str(row[table.colnames[0]])

            target = ""
            for col in ("target", "object", "OBJECT", "Target"):
                if col in table.colnames:
                    target = str(row[col])
                    break
            if not target:
                target = obs_id

            obj_ra = 0.0
            for col in ("ra", "RA", "s_ra"):
                if col in table.colnames:
                    try:
                        obj_ra = float(row[col])
                    except (ValueError, TypeError):
                        pass
                    break

            obj_dec = 0.0
            for col in ("dec", "DEC", "s_dec"):
                if col in table.colnames:
                    try:
                        obj_dec = float(row[col])
                    except (ValueError, TypeError):
                        pass
                    break

            extra: dict = {}
            if "instrument" in table.colnames:
                extra["instrument"] = str(row["instrument"])
            if "exptime" in table.colnames:
                try:
                    extra["exposure_s"] = float(row["exptime"])
                except (ValueError, TypeError):
                    pass
            if "filter" in table.colnames:
                extra["filter"] = str(row["filter"])
            if "date_obs" in table.colnames:
                extra["date_obs"] = str(row["date_obs"])

            results.append(AstroObject(
                source="eso",
                object_id=obs_id,
                name=target,
                ra=obj_ra,
                dec=obj_dec,
                object_type="optical_ir_observation",
                extra=extra,
            ))

        return results

    @with_retry(max_retries=2, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def fetch(self, object_id: str) -> FITSFile:
        """Create metadata FITS for an ESO observation.

        Full ESO data products require authentication and are large.
        We provide a metadata FITS placeholder.
        """
        from astropy.io import fits

        hdu = fits.PrimaryHDU()
        hdu.header["OBJECT"] = object_id
        hdu.header["TELESCOP"] = "ESO/VLT"
        hdu.header["COMMENT"] = "Metadata-only. Download full data from archive.eso.org"
        buf = io.BytesIO()
        hdu.writeto(buf)
        return FITSFile(
            object_id=object_id,
            source="eso",
            data=buf.getvalue(),
            filename=f"eso_{object_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        from astropy.table import Table as AstropyTable
        if isinstance(raw_data, list):
            return AstropyTable(rows=raw_data)
        return AstropyTable(raw_data)
