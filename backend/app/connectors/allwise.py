"""AllWISE infrared survey connector via VizieR catalog II/328."""

import asyncio
import io
import logging
from functools import partial

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table
import astropy.units as u

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

logger = logging.getLogger(__name__)

ALLWISE_CATALOG = "II/328/allwise"


class AllWISEConnector(BaseConnector):
    """Connector for AllWISE mid-infrared survey via VizieR catalog II/328."""

    source_name = "allwise"

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        from astroquery.vizier import Vizier

        loop = asyncio.get_event_loop()

        if ra is None or dec is None:
            coord = await loop.run_in_executor(
                None, partial(SkyCoord.from_name, query)
            )
            ra, dec = coord.ra.deg, coord.dec.deg

        coord = SkyCoord(ra=ra, dec=dec, unit=(u.degree, u.degree), frame="icrs")
        radius_qty = radius * u.degree

        vizier = Vizier(
            columns=[
                "AllWISE", "RAJ2000", "DEJ2000",
                "W1mag", "W2mag", "W3mag", "W4mag",
                "Jmag", "Hmag", "Kmag",
                "cc_flags", "ext_flg",
            ],
            row_limit=50,
        )

        table_list = await loop.run_in_executor(
            None,
            partial(vizier.query_region, coord, radius=radius_qty, catalog=ALLWISE_CATALOG),
        )

        if table_list is None or len(table_list) == 0:
            return []

        return self._table_to_objects(table_list[0])

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch AllWISE photometric data for a source and return as FITS table.

        object_id should be an AllWISE designation (e.g. 'J004244.31+411608.5')
        or a name resolvable to coordinates.
        """
        loop = asyncio.get_event_loop()

        def _query():
            from astroquery.vizier import Vizier

            v = Vizier(columns=["**"], row_limit=1)
            result = v.query_constraints(catalog=ALLWISE_CATALOG, AllWISE=object_id)
            if result and len(result) > 0:
                return result[0]
            # Fallback: resolve name and do tight cone search
            try:
                coord = SkyCoord.from_name(object_id)
                result2 = v.query_region(coord, radius=3 * u.arcsec, catalog=ALLWISE_CATALOG)
                if result2 and len(result2) > 0:
                    return result2[0]
            except (ValueError, Exception) as e:
                logger.debug("SkyCoord.from_name fallback failed for %r: %s", object_id, e)
            return None

        table = await loop.run_in_executor(None, _query)

        if table is None or len(table) == 0:
            raise ValueError(f"No AllWISE data found for '{object_id}'")

        table = self._sanitize_columns(table)
        table = self._fill_masked(table)

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        safe_id = object_id.replace(" ", "_").replace("+", "p").replace("-", "m")
        return FITSFile(
            object_id=object_id,
            source="allwise",
            data=buf.read(),
            filename=f"allwise-{safe_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fill_masked(self, table: Table) -> Table:
        """Fill masked values so the table can be written to FITS."""
        for col in table.colnames:
            if hasattr(table[col], "mask") and np.any(table[col].mask):
                if table[col].dtype.kind in ("i", "u"):
                    table[col] = table[col].filled(-1)
                elif table[col].dtype.kind == "f":
                    table[col] = table[col].filled(np.nan)
                else:
                    table[col] = table[col].filled("")
        return table

    def _sanitize_columns(self, table: Table) -> Table:
        """Drop or convert object-dtype columns for FITS compatibility."""
        for c in list(table.colnames):
            if table[c].dtype == object:
                try:
                    table[c] = [str(v) for v in table[c]]
                except (ValueError, TypeError) as e:
                    logger.debug("Dropping column %r due to conversion error: %s", c, e)
                    table.remove_column(c)
        return table

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects: list[AstroObject] = []

        for row in table:
            ra = 0.0
            dec = 0.0
            for ra_col in ("RAJ2000", "_RAJ2000", "ra"):
                if ra_col in row.colnames:
                    try:
                        ra = float(row[ra_col])
                        break
                    except (ValueError, TypeError):
                        pass
            for dec_col in ("DEJ2000", "_DEJ2000", "dec"):
                if dec_col in row.colnames:
                    try:
                        dec = float(row[dec_col])
                        break
                    except (ValueError, TypeError):
                        pass

            # AllWISE designation
            name = ""
            if "AllWISE" in row.colnames:
                try:
                    name = str(row["AllWISE"]).strip()
                except (ValueError, TypeError, KeyError) as e:
                    logger.debug("Failed to extract AllWISE designation: %s", e)
            if not name:
                name = f"AllWISE-{ra:.5f}{dec:+.5f}"

            # W1 band as primary magnitude
            mag = None
            if "W1mag" in row.colnames:
                try:
                    mag = float(row["W1mag"])
                except (ValueError, TypeError):
                    pass

            # Collect WISE + 2MASS photometry in extra
            extra: dict = {}
            for band in ("W1mag", "W2mag", "W3mag", "W4mag", "Jmag", "Hmag", "Kmag"):
                if band in row.colnames:
                    try:
                        extra[band] = float(row[band])
                    except (ValueError, TypeError):
                        pass
            for flag in ("cc_flags", "ext_flg"):
                if flag in row.colnames:
                    try:
                        extra[flag] = str(row[flag]).strip()
                    except (ValueError, TypeError, KeyError) as e:
                        logger.debug("Failed to extract flag %r: %s", flag, e)

            objects.append(
                AstroObject(
                    source="allwise",
                    object_id=name,
                    name=f"AllWISE {name}" if not name.startswith("AllWISE") else name,
                    ra=ra,
                    dec=dec,
                    object_type="IR source",
                    magnitude=mag,
                    extra=extra,
                )
            )
        return objects
