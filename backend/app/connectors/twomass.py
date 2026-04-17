"""2MASS infrared survey connector via astroquery/VizieR."""

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

TWOMASS_CATALOG = "II/246"  # 2MASS All-Sky Point Source Catalog


class TwoMASSConnector(BaseConnector):
    """Connector for 2MASS infrared survey via VizieR catalog II/246."""

    source_name = "2mass"

    def _make_vizier(self, row_limit: int = 50):
        from astroquery.vizier import Vizier
        viz = Vizier(
            catalog=TWOMASS_CATALOG,
            row_limit=row_limit,
            columns=["*", "RAJ2000", "DEJ2000", "2MASS", "Jmag", "Hmag", "Kmag",
                      "e_Jmag", "e_Hmag", "e_Kmag", "Qflg"],
        )
        return viz

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        loop = asyncio.get_event_loop()

        if ra is None or dec is None:
            coord = await loop.run_in_executor(
                None, partial(SkyCoord.from_name, query)
            )
            ra, dec = coord.ra.deg, coord.dec.deg

        coord = SkyCoord(ra=ra, dec=dec, unit=(u.degree, u.degree), frame="icrs")
        radius_qty = radius * u.degree
        vizier = self._make_vizier()

        table_list = await loop.run_in_executor(
            None,
            partial(vizier.query_region, coord, radius=radius_qty),
        )

        if table_list is None or len(table_list) == 0:
            return []

        return self._table_to_objects(table_list[0])

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch 2MASS photometric data for a source and return as FITS table.

        object_id should be a 2MASS designation (e.g. '00424433+4116085')
        or coordinates that can be resolved to a single source.
        """
        from astroquery.vizier import Vizier

        loop = asyncio.get_event_loop()

        # Try to look up by 2MASS designation
        vizier = Vizier(
            catalog=TWOMASS_CATALOG,
            row_limit=10,
            columns=["**"],  # All columns
        )

        # Query by 2MASS ID using a constraints dict
        table_list = await loop.run_in_executor(
            None,
            partial(
                vizier.query_constraints,
                catalog=TWOMASS_CATALOG,
                **{"2MASS": object_id},
            ),
        )

        if table_list is None or len(table_list) == 0:
            # Fall back: try interpreting object_id as coordinates
            try:
                coord = await loop.run_in_executor(
                    None, partial(SkyCoord.from_name, object_id)
                )
                small_radius = 0.001 * u.degree  # ~3.6 arcsec
                vizier2 = Vizier(catalog=TWOMASS_CATALOG, row_limit=1, columns=["**"])
                table_list = await loop.run_in_executor(
                    None,
                    partial(vizier2.query_region, coord, radius=small_radius),
                )
            except (ValueError, TimeoutError, ConnectionError, OSError) as e:
                logger.debug("2MASS fetch fallback failed for '%s': %s", object_id, e)

        if table_list is None or len(table_list) == 0:
            raise ValueError(f"No 2MASS data found for '{object_id}'")

        table = table_list[0]
        table = self._fill_masked(table)
        table = self._sanitize_columns(table)

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        safe_id = object_id.replace(" ", "_").replace("+", "p").replace("-", "m")
        return FITSFile(
            object_id=object_id,
            source="2mass",
            data=buf.read(),
            filename=f"2mass-{safe_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

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
                    logger.debug("Dropping unconvertible column '%s': %s", c, e)
                    table.remove_column(c)
        return table

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects = []
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

            # 2MASS designation
            name = ""
            for id_col in ("_2MASS", "2MASS"):
                if id_col in row.colnames:
                    try:
                        name = str(row[id_col]).strip()
                        break
                    except (ValueError, TypeError, KeyError):
                        pass
            if not name:
                name = f"2MASS-{ra:.5f}{dec:+.5f}"

            # J-band magnitude as primary
            mag = None
            if "Jmag" in row.colnames:
                try:
                    mag = float(row["Jmag"])
                except (ValueError, TypeError):
                    pass

            # Collect J, H, K photometry in extra
            extra: dict = {}
            for band in ("Jmag", "Hmag", "Kmag", "e_Jmag", "e_Hmag", "e_Kmag"):
                if band in row.colnames:
                    try:
                        extra[band] = float(row[band])
                    except (ValueError, TypeError):
                        pass
            if "Qflg" in row.colnames:
                try:
                    extra["quality_flag"] = str(row["Qflg"]).strip()
                except (ValueError, TypeError, KeyError):
                    pass

            objects.append(
                AstroObject(
                    source="2mass",
                    object_id=name,
                    name=name,
                    ra=ra,
                    dec=dec,
                    object_type="star",
                    magnitude=mag,
                    extra=extra,
                )
            )
        return objects
