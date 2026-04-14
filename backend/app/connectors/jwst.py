"""JWST (James Webb Space Telescope) connector via MAST.

Uses astroquery.mast with mission-specific filters to query JWST observations
including NIRCam, NIRSpec, MIRI, and NIRISS instruments.
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


class JWSTConnector(BaseConnector):
    source_name = "jwst"

    @with_retry(max_retries=1, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        import asyncio
        from functools import partial

        from astropy.coordinates import SkyCoord
        from astroquery.mast import Observations

        # Resolve coordinates
        if ra is None or dec is None:
            try:
                coord = SkyCoord.from_name(query)
                ra = coord.ra.deg
                dec = coord.dec.deg
            except (ValueError, Exception) as e:
                logger.debug("SkyCoord.from_name failed for '%s': %s", query, e)
                parts = query.replace(",", " ").split()
                if len(parts) >= 2:
                    try:
                        ra = float(parts[0])
                        dec = float(parts[1])
                    except ValueError:
                        return []
                else:
                    return []

        # astroquery is synchronous — run in thread pool
        loop = asyncio.get_event_loop()
        table = await loop.run_in_executor(
            None,
            partial(
                Observations.query_criteria,
                coordinates=f"{ra} {dec}",
                radius=radius,
                obs_collection="JWST",
            ),
        )

        if table is None or len(table) == 0:
            return []

        results = []
        for row in table[:50]:
            obs_id = str(row["obsid"]) if "obsid" in table.colnames else ""
            target = str(row["target_name"]) if "target_name" in table.colnames else obs_id
            obj_ra = float(row["s_ra"]) if "s_ra" in table.colnames else 0.0
            obj_dec = float(row["s_dec"]) if "s_dec" in table.colnames else 0.0

            extra: dict = {}
            if "instrument_name" in table.colnames:
                extra["instrument"] = str(row["instrument_name"])
            if "filters" in table.colnames:
                extra["filter"] = str(row["filters"])
            if "t_exptime" in table.colnames:
                try:
                    extra["exposure_s"] = float(row["t_exptime"])
                except (ValueError, TypeError):
                    pass
            if "dataproduct_type" in table.colnames:
                extra["product_type"] = str(row["dataproduct_type"])
            if "calib_level" in table.colnames:
                try:
                    extra["calib_level"] = int(row["calib_level"])
                except (ValueError, TypeError):
                    pass
            if "proposal_id" in table.colnames:
                extra["proposal_id"] = str(row["proposal_id"])
            if "t_obs_release" in table.colnames:
                extra["release_date"] = str(row["t_obs_release"])

            obj_type = "jwst_observation"
            instrument = extra.get("instrument", "")
            if "NIRSpec" in instrument:
                obj_type = "jwst_spectrum"
            elif "MIRI" in instrument:
                obj_type = "jwst_miri"
            elif "NIRCam" in instrument:
                obj_type = "jwst_imaging"

            results.append(AstroObject(
                source="jwst",
                object_id=obs_id,
                name=target,
                ra=obj_ra,
                dec=obj_dec,
                object_type=obj_type,
                extra=extra,
            ))

        return results

    @with_retry(max_retries=1, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Create metadata FITS for a JWST observation.

        Full JWST data products are large and hosted on MAST.
        We provide a metadata FITS placeholder.
        """
        from astropy.io import fits

        hdu = fits.PrimaryHDU()
        hdu.header["OBJECT"] = object_id
        hdu.header["TELESCOP"] = "JWST"
        hdu.header["COMMENT"] = "Metadata-only. Download full data from mast.stsci.edu"
        buf = io.BytesIO()
        hdu.writeto(buf)
        return FITSFile(
            object_id=object_id,
            source="jwst",
            data=buf.getvalue(),
            filename=f"jwst_{object_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        from astropy.table import Table as AstropyTable
        if isinstance(raw_data, list):
            return AstropyTable(rows=raw_data)
        return AstropyTable(raw_data)
