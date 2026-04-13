"""XMM-Newton X-ray connector via VizieR 4XMM-DR14 catalog (IX/68)."""

import asyncio
import io
from functools import partial

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table
import astropy.units as u

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

XMM_CATALOG = "IX/68"  # 4XMM-DR14 serendipitous source catalog


class XMMConnector(BaseConnector):
    """Connector for XMM-Newton via VizieR catalog IX/68 (4XMM-DR14)."""

    source_name = "xmm"

    def _make_vizier(self, row_limit: int = 50):
        from astroquery.vizier import Vizier
        viz = Vizier(
            catalog=XMM_CATALOG,
            row_limit=row_limit,
            columns=[
                "IAUNAME", "RA", "DEC",
                "EP_8_FLUX",
                "EP_HR1", "EP_HR2", "EP_HR3",
                "EP_DET_ML", "EP_EXTENT", "SC_SUM_FLAG",
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
        """Fetch XMM-Newton source data and return as FITS table.

        object_id should be a 4XMM IAUNAME (e.g. '4XMM J004244.3+411608').
        """
        from astroquery.vizier import Vizier

        loop = asyncio.get_event_loop()

        vizier = Vizier(
            catalog=XMM_CATALOG,
            row_limit=10,
            columns=["**"],  # All columns
        )

        table_list = await loop.run_in_executor(
            None,
            partial(
                vizier.query_constraints,
                catalog=XMM_CATALOG,
                IAUNAME=object_id,
            ),
        )

        if table_list is None or len(table_list) == 0:
            # Fall back: try resolving object_id as coordinates
            try:
                coord = await loop.run_in_executor(
                    None, partial(SkyCoord.from_name, object_id)
                )
                small_radius = 0.001 * u.degree  # ~3.6 arcsec
                vizier2 = Vizier(catalog=XMM_CATALOG, row_limit=1, columns=["**"])
                table_list = await loop.run_in_executor(
                    None,
                    partial(vizier2.query_region, coord, radius=small_radius),
                )
            except Exception:
                pass

        if table_list is None or len(table_list) == 0:
            raise ValueError(f"No XMM-Newton data found for '{object_id}'")

        table = table_list[0]
        table = self._fill_masked(table)
        table = self._sanitize_columns(table)

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        safe_id = object_id.replace(" ", "_").replace("+", "p").replace("-", "m")
        return FITSFile(
            object_id=object_id,
            source="xmm",
            data=buf.read(),
            filename=f"xmm-{safe_id}.fits",
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
            for ra_col in ("RA", "RAJ2000", "_RAJ2000", "ra"):
                if ra_col in row.colnames:
                    try:
                        ra = float(row[ra_col])
                        break
                    except (ValueError, TypeError):
                        pass
            for dec_col in ("DEC", "DEJ2000", "_DEJ2000", "dec"):
                if dec_col in row.colnames:
                    try:
                        dec = float(row[dec_col])
                        break
                    except (ValueError, TypeError):
                        pass

            # IAUNAME as identifier
            name = ""
            if "IAUNAME" in row.colnames:
                try:
                    name = str(row["IAUNAME"]).strip()
                except Exception:
                    pass
            if not name:
                name = f"4XMM J{ra:.4f}{dec:+.4f}"

            # Collect X-ray properties in extra
            extra: dict = {}

            flux = self._safe_float(row, "EP_8_FLUX")
            if flux is not None:
                extra["EP_8_FLUX"] = flux

            for hr_col in ("EP_HR1", "EP_HR2", "EP_HR3"):
                val = self._safe_float(row, hr_col)
                if val is not None:
                    extra[hr_col] = val

            det_ml = self._safe_float(row, "EP_DET_ML")
            if det_ml is not None:
                extra["EP_DET_ML"] = det_ml

            extent = self._safe_float(row, "EP_EXTENT")
            if extent is not None:
                extra["EP_EXTENT"] = extent

            if "SC_SUM_FLAG" in row.colnames:
                try:
                    extra["SC_SUM_FLAG"] = int(row["SC_SUM_FLAG"])
                except (ValueError, TypeError):
                    try:
                        extra["SC_SUM_FLAG"] = str(row["SC_SUM_FLAG"]).strip()
                    except Exception:
                        pass

            objects.append(
                AstroObject(
                    source="xmm",
                    object_id=name,
                    name=name,
                    ra=ra,
                    dec=dec,
                    object_type="X-ray source",
                    extra=extra,
                )
            )
        return objects
