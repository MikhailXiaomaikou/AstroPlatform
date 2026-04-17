"""LAMOST (Large Sky Area Multi-Object Fiber Spectroscopic Telescope) connector.

Uses the LAMOST DR9 HTTP API at http://www.lamost.org/dr9/v2.0/api/ to query
the Chinese large sky survey with millions of stellar spectra.
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

LAMOST_API_BASE = "http://www.lamost.org/dr9/v2.0/api"


class LAMOSTConnector(BaseConnector):
    source_name = "lamost"

    @with_retry(max_retries=2, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        import asyncio
        from functools import partial

        from astropy.coordinates import SkyCoord

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

        loop = asyncio.get_event_loop()
        records = await loop.run_in_executor(
            None,
            partial(self._query_cone, ra, dec, radius),
        )

        if not records:
            return []

        results = []
        for rec in records[:50]:
            obs_id = str(rec.get("obsid", ""))
            designation = rec.get("designation", obs_id)
            obj_ra = float(rec.get("ra", 0.0))
            obj_dec = float(rec.get("dec", 0.0))

            extra: dict = {}
            if "class" in rec:
                extra["spectral_class"] = rec["class"]
            if "subclass" in rec:
                extra["subclass"] = rec["subclass"]
            if "snrg" in rec:
                try:
                    extra["snr_g"] = float(rec["snrg"])
                except (ValueError, TypeError):
                    pass
            if "snrr" in rec:
                try:
                    extra["snr_r"] = float(rec["snrr"])
                except (ValueError, TypeError):
                    pass
            if "snri" in rec:
                try:
                    extra["snr_i"] = float(rec["snri"])
                except (ValueError, TypeError):
                    pass
            if "rv" in rec:
                try:
                    extra["radial_velocity"] = float(rec["rv"])
                except (ValueError, TypeError):
                    pass
            if "fiberid" in rec:
                extra["fiber_id"] = rec["fiberid"]
            if "lmjd" in rec:
                extra["lmjd"] = rec["lmjd"]

            magnitude = None
            if "mag_g" in rec:
                try:
                    magnitude = float(rec["mag_g"])
                except (ValueError, TypeError):
                    pass

            redshift = None
            if "z" in rec:
                try:
                    redshift = float(rec["z"])
                except (ValueError, TypeError):
                    pass

            obj_type = extra.get("spectral_class", "spectrum")

            results.append(AstroObject(
                source="lamost",
                object_id=obs_id,
                name=str(designation),
                ra=obj_ra,
                dec=obj_dec,
                object_type=obj_type,
                magnitude=magnitude,
                redshift=redshift,
                extra=extra,
            ))

        return results

    @staticmethod
    def _query_cone(ra: float, dec: float, radius: float) -> list[dict]:
        """Synchronous cone search against the LAMOST DR9 API."""
        import requests

        # LAMOST API expects radius in arcminutes
        radius_arcmin = radius * 60.0
        url = f"{LAMOST_API_BASE}/cone"
        params = {
            "ra": ra,
            "dec": dec,
            "sr": radius_arcmin,
            "limit": 50,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # API returns a list of records or a dict with a "data" key
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        if isinstance(data, dict) and "rows" in data:
            return data["rows"]
        return []

    @with_retry(max_retries=2, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Create metadata FITS for a LAMOST spectrum.

        Full LAMOST spectra can be downloaded from the LAMOST archive.
        We provide a metadata FITS placeholder.
        """
        from astropy.io import fits

        hdu = fits.PrimaryHDU()
        hdu.header["OBJECT"] = object_id
        hdu.header["TELESCOP"] = "LAMOST"
        hdu.header["COMMENT"] = "Metadata-only. Download full spectra from www.lamost.org"
        buf = io.BytesIO()
        hdu.writeto(buf)
        return FITSFile(
            object_id=object_id,
            source="lamost",
            data=buf.getvalue(),
            filename=f"lamost_{object_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        from astropy.table import Table as AstropyTable
        if isinstance(raw_data, list):
            return AstropyTable(rows=raw_data)
        return AstropyTable(raw_data)
