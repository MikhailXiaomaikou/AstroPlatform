"""VizieR catalog connector via astroquery."""

import asyncio
import io
from functools import partial

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table
import astropy.units as u

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

# Default catalogs to search when none specified
DEFAULT_CATALOGS = [
    "II/246",   # 2MASS All-Sky Point Source Catalog
    "I/355",    # Gaia DR3
]


class VizierConnector(BaseConnector):
    """Connector for VizieR catalog service via astroquery."""

    source_name = "vizier"

    def _make_vizier(self, catalogs: list[str] | None = None, row_limit: int = 50):
        from astroquery.vizier import Vizier
        viz = Vizier(row_limit=row_limit)
        if catalogs:
            viz.catalog = catalogs
        return viz

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
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
        vizier = self._make_vizier(catalogs=DEFAULT_CATALOGS)

        table_list = await loop.run_in_executor(
            None,
            partial(vizier.query_region, coord, radius=radius_qty, cache=False),
        )

        if table_list is None or len(table_list) == 0:
            return []

        objects: list[AstroObject] = []
        for table in table_list:
            objects.extend(self._table_to_objects(table))

        return objects

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch a VizieR catalog table as FITS.

        object_id should be a catalog identifier (e.g. 'II/246') optionally
        followed by a slash and table name (e.g. 'II/246/out').
        """
        from astroquery.vizier import Vizier

        loop = asyncio.get_event_loop()
        vizier = Vizier(row_limit=500)

        table_list = await loop.run_in_executor(
            None,
            partial(vizier.get_catalogs, object_id, cache=False),
        )

        if table_list is None or len(table_list) == 0:
            raise ValueError(f"No VizieR catalog data found for '{object_id}'")

        # Use the first table from the result
        table = table_list[0]
        table = self._fill_masked(table)
        table = self._sanitize_columns(table)

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        safe_id = object_id.replace("/", "_")
        return FITSFile(
            object_id=object_id,
            source="vizier",
            data=buf.read(),
            filename=f"vizier-{safe_id}.fits",
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
        """Drop object-dtype columns that cannot be serialized to FITS."""
        drop_cols = [c for c in table.colnames if table[c].dtype == object]
        for c in drop_cols:
            try:
                table[c] = [str(v) for v in table[c]]
            except Exception:
                table.remove_column(c)
        return table

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects = []
        # Detect RA/Dec column names (VizieR uses various conventions)
        ra_col = self._find_col(table, ("_RAJ2000", "RAJ2000", "RA_ICRS", "ra", "RA"))
        dec_col = self._find_col(table, ("_DEJ2000", "DEJ2000", "DE_ICRS", "dec", "DEC"))

        for row in table:
            ra = 0.0
            dec = 0.0
            if ra_col:
                try:
                    ra = float(row[ra_col])
                except (ValueError, TypeError):
                    pass
            if dec_col:
                try:
                    dec = float(row[dec_col])
                except (ValueError, TypeError):
                    pass

            # Try to find an identifier column
            name = ""
            for id_col in ("_2MASS", "2MASS", "Source", "Name", "ID"):
                if id_col in row.colnames:
                    try:
                        name = str(row[id_col]).strip()
                        break
                    except Exception:
                        pass
            if not name:
                name = f"VizieR-{ra:.5f}{dec:+.5f}"

            # Try to get a magnitude
            mag = None
            for mag_col in ("Vmag", "Gmag", "Jmag", "Rmag", "Bmag", "phot_g_mean_mag"):
                if mag_col in row.colnames:
                    try:
                        mag = float(row[mag_col])
                        break
                    except (ValueError, TypeError):
                        pass

            objects.append(
                AstroObject(
                    source="vizier",
                    object_id=name,
                    name=name,
                    ra=ra,
                    dec=dec,
                    magnitude=mag,
                )
            )
        return objects

    @staticmethod
    def _find_col(table: Table, candidates: tuple[str, ...]) -> str | None:
        for c in candidates:
            if c in table.colnames:
                return c
        return None
