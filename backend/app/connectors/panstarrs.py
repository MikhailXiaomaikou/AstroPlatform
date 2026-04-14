"""Pan-STARRS DR2 connector via MAST Catalogs API."""

import asyncio
import io
import logging
from functools import partial

import httpx
from astropy.coordinates import SkyCoord
from astropy.table import Table

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

logger = logging.getLogger(__name__)

PANSTARRS_API_URL = "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/stack"

# Magnitude columns to store in extra
MAG_COLUMNS = [
    "gMeanPSFMag", "rMeanPSFMag", "iMeanPSFMag", "zMeanPSFMag", "yMeanPSFMag",
]
MAG_ERR_COLUMNS = [
    "gMeanPSFMagErr", "rMeanPSFMagErr", "iMeanPSFMagErr", "zMeanPSFMagErr", "yMeanPSFMagErr",
]


class PanSTARRSConnector(BaseConnector):
    """Connector for Pan-STARRS DR2 via MAST Catalogs API."""

    source_name = "panstarrs"

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, httpx.HTTPStatusError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        if ra is None or dec is None:
            ra, dec = await self._resolve_name(query)

        params = {
            "ra": ra,
            "dec": dec,
            "radius": radius,
            "nDetections.gte": 1,
            "pagesize": 50,
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.get(PANSTARRS_API_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()

        rows = payload.get("data", [])
        return self._rows_to_objects(rows)

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, httpx.HTTPStatusError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch Pan-STARRS photometric data for an object as a FITS table."""
        clean_id = object_id.strip()

        params = {
            "objID": clean_id,
            "pagesize": 1,
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.get(PANSTARRS_API_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()

        rows = payload.get("data", [])
        if not rows:
            raise ValueError(f"No Pan-STARRS data found for objID {object_id}")

        table = Table(rows=[rows[0]])

        buf = io.BytesIO()
        table.write(buf, format="fits")
        buf.seek(0)

        return FITSFile(
            object_id=object_id,
            source="panstarrs",
            data=buf.read(),
            filename=f"panstarrs-{clean_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    async def _resolve_name(self, name: str) -> tuple[float, float]:
        loop = asyncio.get_running_loop()
        coord = await loop.run_in_executor(
            None, partial(SkyCoord.from_name, name)
        )
        return coord.ra.deg, coord.dec.deg

    def _rows_to_objects(self, rows: list[dict]) -> list[AstroObject]:
        objects = []
        for row in rows:
            obj_id = str(row.get("objName") or row.get("objID", ""))
            ra = _safe_float(row.get("raMean")) or 0.0
            dec = _safe_float(row.get("decMean")) or 0.0

            # Primary magnitude: g-band, fall back to r-band
            magnitude = _safe_float(row.get("gMeanPSFMag"))
            if magnitude is None:
                magnitude = _safe_float(row.get("rMeanPSFMag"))

            # Collect photometry into extra
            extra: dict = {}
            for col in MAG_COLUMNS + MAG_ERR_COLUMNS:
                val = _safe_float(row.get(col))
                if val is not None:
                    extra[col] = val

            n_det = row.get("nDetections")
            if n_det is not None:
                extra["nDetections"] = int(n_det)

            objects.append(
                AstroObject(
                    source="panstarrs",
                    object_id=obj_id,
                    name=obj_id,
                    ra=ra,
                    dec=dec,
                    object_type="",
                    magnitude=magnitude,
                    redshift=None,
                    extra=extra,
                )
            )
        return objects


def _safe_float(val) -> float | None:
    """Convert a value to float, returning None for missing/invalid/NaN."""
    if val is None:
        return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    if f != f or f == float("inf") or f == float("-inf"):
        return None
    # Pan-STARRS uses -999 as a sentinel for missing magnitudes
    if f == -999.0:
        return None
    return f
