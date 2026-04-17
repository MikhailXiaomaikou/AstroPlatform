"""VizieR catalog connector via astroquery."""

import asyncio
import io
import logging
import re
from functools import partial

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table
import astropy.units as u

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

logger = logging.getLogger(__name__)

# Default catalogs to search when none specified
DEFAULT_CATALOGS = [
    "II/246",   # 2MASS All-Sky Point Source Catalog
    "I/355",    # Gaia DR3
]

# Pattern matching a VizieR catalog identifier such as II/246, I/355,
# J/A+A/674/A1, V/50, B/eso, VI/135, etc.
_CATALOG_ID_RE = re.compile(
    r"^(?:[IVB]{1,3}|J)/[A-Za-z0-9+_.]+(?:/[A-Za-z0-9+_.]+)*$"
)

# Pattern to extract explicit catalog references embedded in a query string,
# e.g. "VizieR:II/246" or "catalog:J/A+A/674/A1".
_CATALOG_PREFIX_RE = re.compile(
    r"(?:vizier|catalog):([IVB]{1,3}|J)/([A-Za-z0-9+_.]+(?:/[A-Za-z0-9+_.]+)*)",
    re.IGNORECASE,
)


class VizierConnector(BaseConnector):
    """Connector for VizieR catalog service via astroquery."""

    source_name = "vizier"

    def _make_vizier(self, catalogs: list[str] | None = None, row_limit: int = 50):
        from astroquery.vizier import Vizier
        viz = Vizier(row_limit=row_limit)
        if catalogs:
            viz.catalog = catalogs
        return viz

    @staticmethod
    def _extract_catalogs(query: str) -> tuple[list[str] | None, str]:
        """Extract catalog IDs from the query string.

        Returns a tuple of (catalog_list, cleaned_query).  If no catalog
        identifiers are found in *query*, catalog_list is ``None`` and the
        query is returned unchanged.

        Supported forms:
        * ``"VizieR:II/246"`` or ``"catalog:J/A+A/674/A1"`` embedded in a
          longer query.
        * The query itself is a bare catalog ID such as ``"II/246"``.
        """
        # 1. Look for explicit "VizieR:<id>" / "catalog:<id>" patterns.
        matches = _CATALOG_PREFIX_RE.findall(query)
        if matches:
            catalogs = [f"{roman}/{rest}" for roman, rest in matches]
            # Strip the matched tokens so the remaining text can be used for
            # coordinate / name resolution.
            cleaned = _CATALOG_PREFIX_RE.sub("", query).strip()
            return catalogs, cleaned

        # 2. Check if the entire query is a catalog ID.
        stripped = query.strip()
        if _CATALOG_ID_RE.match(stripped):
            return [stripped], ""

        return None, query

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self,
        query: str,
        ra: float | None = None,
        dec: float | None = None,
        radius: float = 0.1,
        catalog: list[str] | str | None = None,
    ) -> list[AstroObject]:
        loop = asyncio.get_event_loop()

        # --- Determine which catalogs to query ---
        # Priority: explicit *catalog* parameter > IDs parsed from query > defaults.
        parsed_catalogs, cleaned_query = self._extract_catalogs(query)

        if catalog is not None:
            if isinstance(catalog, str):
                catalog = [catalog]
            use_catalogs = catalog
        elif parsed_catalogs is not None:
            use_catalogs = parsed_catalogs
        else:
            use_catalogs = DEFAULT_CATALOGS

        # Use cleaned_query for name resolution; fall back to original query
        # when the cleaned version is empty (i.e. the whole query was a catalog
        # ID) and no explicit coordinates were given.
        resolve_query = cleaned_query or query

        if ra is None or dec is None:
            coord = await loop.run_in_executor(
                None, partial(SkyCoord.from_name, resolve_query)
            )
            ra, dec = coord.ra.deg, coord.dec.deg

        coord = SkyCoord(ra=ra, dec=dec, unit=(u.degree, u.degree), frame="icrs")
        radius_qty = radius * u.degree
        vizier = self._make_vizier(catalogs=use_catalogs)

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

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
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
            except (ValueError, TypeError) as e:
                logger.debug("Dropping unconvertible column '%s': %s", c, e)
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
                    except (ValueError, TypeError, KeyError):
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
