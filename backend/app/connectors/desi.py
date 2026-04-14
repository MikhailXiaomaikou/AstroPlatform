"""DESI (Dark Energy Spectroscopic Instrument) connector via NOIRLab TAP."""

import asyncio
import logging
from functools import partial

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

logger = logging.getLogger(__name__)


DESI_TAP_URL = "https://datalab.noirlab.edu/tap"


class DESIConnector(BaseConnector):
    """Connector for DESI public data via NOIRLab DataLab TAP."""

    source_name = "desi"

    @with_retry(max_retries=1, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        if ra is None or dec is None:
            try:
                from astropy.coordinates import SkyCoord
                coord = SkyCoord.from_name(query)
                ra, dec = coord.ra.deg, coord.dec.deg
            except (ValueError, Exception) as e:
                logger.debug("SkyCoord.from_name failed for '%s': %s", query, e)
                return []

        # Query DESI Early Data Release via DataLab
        adql = f"""SELECT TOP 50
            targetid, target_ra AS ra, target_dec AS dec,
            z AS redshift, zerr, spectype, deltachi2
        FROM desi_edr.zpix
        WHERE 1=CONTAINS(POINT('ICRS', target_ra, target_dec),
                         CIRCLE('ICRS', {ra}, {dec}, {radius}))
        AND z IS NOT NULL
        ORDER BY deltachi2 DESC"""

        try:
            from astroquery.utils.tap.core import TapPlus
            loop = asyncio.get_running_loop()
            tap = TapPlus(url=DESI_TAP_URL)
            job = await loop.run_in_executor(None, partial(tap.launch_job, adql))
            table = job.get_results()
        except (ValueError, TimeoutError, ConnectionError, OSError) as e:
            logger.debug("DESI TAP query failed: %s", e)
            return []

        if table is None or len(table) == 0:
            return []

        objects = []
        for row in table:
            try:
                ra_val = float(row["ra"])
                dec_val = float(row["dec"])
                z = float(row["redshift"]) if row["redshift"] == row["redshift"] else None
                spectype = str(row.get("spectype", "")).strip() if "spectype" in row.colnames else ""
                tid = str(row.get("targetid", ""))

                objects.append(AstroObject(
                    source="desi",
                    object_id=tid,
                    name=f"DESI {tid}",
                    ra=ra_val,
                    dec=dec_val,
                    object_type=spectype,
                    redshift=z,
                    extra={"zerr": float(row.get("zerr", 0))} if "zerr" in row.colnames else {},
                ))
            except (ValueError, TypeError):
                continue

        return objects

    async def fetch(self, object_id: str) -> FITSFile:
        raise ValueError("DESI FITS download not yet supported — use ADQL to query data directly")

    def normalize(self, raw_data):
        from astropy.table import Table
        return Table(raw_data) if not isinstance(raw_data, Table) else raw_data
