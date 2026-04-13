"""Radio survey connectors for NVSS (1.4 GHz) and FIRST (1.4 GHz) via VizieR."""

from __future__ import annotations

import asyncio
import io
from functools import partial

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table
import astropy.units as u

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

NVSS_CATALOG = "VIII/65"   # NVSS 1.4 GHz source catalog
FIRST_CATALOG = "VIII/92"  # FIRST 1.4 GHz source catalog


class NVSSConnector(BaseConnector):
    """Connector for NRAO VLA Sky Survey (NVSS) via VizieR catalog VIII/65."""

    source_name = "nvss"

    def _make_vizier(self, row_limit: int = 50):
        from astroquery.vizier import Vizier
        viz = Vizier(
            catalog=NVSS_CATALOG,
            row_limit=row_limit,
            columns=[
                "NVSS", "RAJ2000", "DEJ2000",
                "S1.4", "MajAxis", "MinAxis",
            ],
        )
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
        vizier = self._make_vizier()

        table_list = await loop.run_in_executor(
            None,
            partial(vizier.query_region, coord, radius=radius_qty),
        )

        if table_list is None or len(table_list) == 0:
            return []

        return self._table_to_objects(table_list[0])

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch NVSS source data and return as FITS table.

        object_id should be an NVSS source name (e.g. 'NVSS J132527+472731').
        """
        from astroquery.vizier import Vizier

        loop = asyncio.get_event_loop()

        vizier = Vizier(
            catalog=NVSS_CATALOG,
            row_limit=10,
            columns=["**"],
        )

        table_list = await loop.run_in_executor(
            None,
            partial(
                vizier.query_constraints,
                catalog=NVSS_CATALOG,
                NVSS=object_id,
            ),
        )

        if table_list is None or len(table_list) == 0:
            try:
                coord = await loop.run_in_executor(
                    None, partial(SkyCoord.from_name, object_id)
                )
                small_radius = 0.001 * u.degree
                vizier2 = Vizier(catalog=NVSS_CATALOG, row_limit=1, columns=["**"])
                table_list = await loop.run_in_executor(
                    None,
                    partial(vizier2.query_region, coord, radius=small_radius),
                )
            except Exception:
                pass

        if table_list is None or len(table_list) == 0:
            raise ValueError(f"No NVSS data found for '{object_id}'")

        table = table_list[0]
        table = self._fill_masked(table)
        table = self._sanitize_columns(table)

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        safe_id = object_id.replace(" ", "_").replace("+", "p").replace("-", "m")
        return FITSFile(
            object_id=object_id,
            source="nvss",
            data=buf.read(),
            filename=f"nvss-{safe_id}.fits",
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
                except Exception:
                    table.remove_column(c)
        return table

    def _safe_float(self, row, col: str) -> float | None:
        """Extract a float from a row column, returning None on failure."""
        if col not in row.colnames:
            return None
        try:
            val = float(row[col])
            if val != val or val == float("inf") or val == float("-inf"):
                return None
            return val
        except (ValueError, TypeError):
            return None

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects = []
        for row in table:
            ra = 0.0
            dec = 0.0
            for ra_col in ("RAJ2000", "_RAJ2000", "RA", "ra"):
                if ra_col in row.colnames:
                    try:
                        ra = float(row[ra_col])
                        break
                    except (ValueError, TypeError):
                        pass
            for dec_col in ("DEJ2000", "_DEJ2000", "DEC", "dec"):
                if dec_col in row.colnames:
                    try:
                        dec = float(row[dec_col])
                        break
                    except (ValueError, TypeError):
                        pass

            # NVSS source name as identifier
            name = ""
            if "NVSS" in row.colnames:
                try:
                    name = str(row["NVSS"]).strip()
                except Exception:
                    pass
            if not name:
                name = f"NVSS J{ra:.4f}{dec:+.4f}"

            # Collect radio properties in extra
            extra: dict = {}

            flux = self._safe_float(row, "S1.4")
            if flux is not None:
                extra["flux_1.4ghz"] = flux  # mJy

            maj = self._safe_float(row, "MajAxis")
            if maj is not None:
                extra["morphology_major_axis"] = maj

            minor = self._safe_float(row, "MinAxis")
            if minor is not None:
                extra["morphology_minor_axis"] = minor

            objects.append(
                AstroObject(
                    source="nvss",
                    object_id=name,
                    name=name,
                    ra=ra,
                    dec=dec,
                    object_type="radio source",
                    extra=extra,
                )
            )
        return objects


class FIRSTConnector(BaseConnector):
    """Connector for Faint Images of the Radio Sky at Twenty-cm (FIRST) via VizieR catalog VIII/92."""

    source_name = "first"

    def _make_vizier(self, row_limit: int = 50):
        from astroquery.vizier import Vizier
        viz = Vizier(
            catalog=FIRST_CATALOG,
            row_limit=row_limit,
            columns=[
                "FIRST", "RAJ2000", "DEJ2000",
                "Fint", "Maj", "Min",
            ],
        )
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
        vizier = self._make_vizier()

        table_list = await loop.run_in_executor(
            None,
            partial(vizier.query_region, coord, radius=radius_qty),
        )

        if table_list is None or len(table_list) == 0:
            return []

        return self._table_to_objects(table_list[0])

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch FIRST source data and return as FITS table.

        object_id should be a FIRST source name (e.g. 'FIRST J132527.5+472731').
        """
        from astroquery.vizier import Vizier

        loop = asyncio.get_event_loop()

        vizier = Vizier(
            catalog=FIRST_CATALOG,
            row_limit=10,
            columns=["**"],
        )

        table_list = await loop.run_in_executor(
            None,
            partial(
                vizier.query_constraints,
                catalog=FIRST_CATALOG,
                FIRST=object_id,
            ),
        )

        if table_list is None or len(table_list) == 0:
            try:
                coord = await loop.run_in_executor(
                    None, partial(SkyCoord.from_name, object_id)
                )
                small_radius = 0.001 * u.degree
                vizier2 = Vizier(catalog=FIRST_CATALOG, row_limit=1, columns=["**"])
                table_list = await loop.run_in_executor(
                    None,
                    partial(vizier2.query_region, coord, radius=small_radius),
                )
            except Exception:
                pass

        if table_list is None or len(table_list) == 0:
            raise ValueError(f"No FIRST data found for '{object_id}'")

        table = table_list[0]
        table = self._fill_masked(table)
        table = self._sanitize_columns(table)

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        safe_id = object_id.replace(" ", "_").replace("+", "p").replace("-", "m")
        return FITSFile(
            object_id=object_id,
            source="first",
            data=buf.read(),
            filename=f"first-{safe_id}.fits",
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
                except Exception:
                    table.remove_column(c)
        return table

    def _safe_float(self, row, col: str) -> float | None:
        """Extract a float from a row column, returning None on failure."""
        if col not in row.colnames:
            return None
        try:
            val = float(row[col])
            if val != val or val == float("inf") or val == float("-inf"):
                return None
            return val
        except (ValueError, TypeError):
            return None

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects = []
        for row in table:
            ra = 0.0
            dec = 0.0
            for ra_col in ("RAJ2000", "_RAJ2000", "RA", "ra"):
                if ra_col in row.colnames:
                    try:
                        ra = float(row[ra_col])
                        break
                    except (ValueError, TypeError):
                        pass
            for dec_col in ("DEJ2000", "_DEJ2000", "DEC", "dec"):
                if dec_col in row.colnames:
                    try:
                        dec = float(row[dec_col])
                        break
                    except (ValueError, TypeError):
                        pass

            # FIRST source name as identifier
            name = ""
            if "FIRST" in row.colnames:
                try:
                    name = str(row["FIRST"]).strip()
                except Exception:
                    pass
            if not name:
                name = f"FIRST J{ra:.4f}{dec:+.4f}"

            # Collect radio properties in extra
            extra: dict = {}

            flux = self._safe_float(row, "Fint")
            if flux is not None:
                extra["flux_1.4ghz"] = flux  # mJy

            maj = self._safe_float(row, "Maj")
            if maj is not None:
                extra["morphology_major_axis"] = maj

            minor = self._safe_float(row, "Min")
            if minor is not None:
                extra["morphology_minor_axis"] = minor

            objects.append(
                AstroObject(
                    source="first",
                    object_id=name,
                    name=name,
                    ra=ra,
                    dec=dec,
                    object_type="radio source",
                    extra=extra,
                )
            )
        return objects
