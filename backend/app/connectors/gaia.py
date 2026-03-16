import asyncio
import io
from functools import partial

from astropy.coordinates import SkyCoord
from astropy.table import Table
import astropy.units as u

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry


class GaiaConnector(BaseConnector):
    """Connector for Gaia DR3 via astroquery."""

    source_name = "gaia"

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        if ra is None or dec is None:
            loop = asyncio.get_event_loop()
            coord = await loop.run_in_executor(
                None, partial(SkyCoord.from_name, query)
            )
            ra, dec = coord.ra.deg, coord.dec.deg

        table = await self._cone_search(ra, dec, radius)
        return self._table_to_objects(table)

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch Gaia source data as a FITS-format table."""
        import numpy as np

        table = await self._query_by_source_id(object_id)

        # Drop object/variable-length columns that can't serialize to FITS
        drop_cols = [c for c in table.colnames if table[c].dtype == object]
        for c in drop_cols:
            table.remove_column(c)

        # Fill masked values to avoid FITS write issues
        for col in table.colnames:
            if hasattr(table[col], 'mask') and np.any(table[col].mask):
                if table[col].dtype.kind in ('i', 'u'):
                    table[col] = table[col].filled(-1)
                else:
                    table[col] = table[col].filled(np.nan)

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        return FITSFile(
            object_id=object_id,
            source="gaia",
            data=buf.read(),
            filename=f"gaia-{object_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    async def _cone_search(self, ra: float, dec: float, radius: float) -> Table:
        from astroquery.gaia import Gaia

        coord = SkyCoord(ra=ra, dec=dec, unit=(u.degree, u.degree), frame="icrs")
        radius_qty = radius * u.degree

        loop = asyncio.get_event_loop()
        job = await loop.run_in_executor(
            None,
            partial(
                Gaia.cone_search_async,
                coordinate=coord,
                radius=radius_qty,
            ),
        )
        return job.get_results()

    async def _query_by_source_id(self, source_id: str) -> Table:
        from astroquery.gaia import Gaia

        adql = f"SELECT * FROM gaiadr3.gaia_source WHERE source_id = {source_id}"
        loop = asyncio.get_event_loop()
        job = await loop.run_in_executor(
            None,
            partial(Gaia.launch_job, adql),
        )
        return job.get_results()

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects = []
        for row in table:
            ra = float(row["ra"]) if "ra" in row.colnames else 0.0
            dec = float(row["dec"]) if "dec" in row.colnames else 0.0
            source_id = str(row.get("source_id", row.get("SOURCE_ID", "")))

            mag = None
            for col in ("phot_g_mean_mag", "PHOT_G_MEAN_MAG"):
                if col in row.colnames:
                    mag = float(row[col])
                    break

            parallax = None
            for col in ("parallax", "PARALLAX"):
                if col in row.colnames:
                    try:
                        parallax = float(row[col])
                    except (ValueError, TypeError):
                        pass

            objects.append(
                AstroObject(
                    source="gaia",
                    object_id=source_id,
                    name=f"Gaia DR3 {source_id}",
                    ra=ra,
                    dec=dec,
                    object_type="star",
                    magnitude=mag,
                    extra={"parallax": parallax} if parallax is not None else {},
                )
            )
        return objects
