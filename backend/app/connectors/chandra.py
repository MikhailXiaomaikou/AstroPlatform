"""Chandra/XMM X-ray connector via CSC2 cone search API."""

import asyncio
import csv
import io
from functools import partial

import httpx
from astropy.table import Table

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

CSC2_CONE_URL = "https://cda.cfa.harvard.edu/cscview/coneSearch"


class ChandraConnector(BaseConnector):
    """Connector for Chandra X-ray Observatory via CSC2 cone search."""

    source_name = "chandra"

    @with_retry(max_retries=1, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        if ra is None or dec is None:
            ra, dec = await self._resolve_name(query)

        # CSC2 cone search — radius in arcmin
        params = {
            "pos": f"{ra},{dec}",
            "sr": str(radius * 60),  # convert deg to arcmin
            "format": "csv",
            "columns": "name,ra,dec,flux_aper_b,significance,extent_flag,hard_hm,hard_ms",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(CSC2_CONE_URL, params=params)
            resp.raise_for_status()

        return self._parse_results(resp.text)

    @with_retry(max_retries=1, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch Chandra source data and return as FITS table.

        object_id should be a CSC source name (e.g. '2CXO J004244.3+411609').
        """
        params = {
            "pos": object_id,
            "sr": "0.01",
            "format": "csv",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(CSC2_CONE_URL, params=params)
            resp.raise_for_status()

        table = self._csv_to_table(resp.text)
        if len(table) == 0:
            raise ValueError(f"No Chandra data found for '{object_id}'")

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        safe_id = object_id.replace(" ", "_")
        return FITSFile(
            object_id=object_id,
            source="chandra",
            data=buf.read(),
            filename=f"chandra-{safe_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_name(self, name: str) -> tuple[float, float]:
        from astropy.coordinates import SkyCoord

        loop = asyncio.get_event_loop()
        coord = await loop.run_in_executor(None, partial(SkyCoord.from_name, name))
        return coord.ra.deg, coord.dec.deg

    def _csv_to_table(self, text: str) -> Table:
        """Parse CSC2 CSV response into an astropy Table."""
        lines = [ln for ln in text.strip().splitlines() if ln and not ln.startswith("#")]
        if len(lines) < 2:
            return Table()

        reader = csv.DictReader(lines)
        rows = list(reader)
        if not rows:
            return Table()

        columns: dict[str, list] = {key: [] for key in rows[0].keys()}
        for row in rows:
            for key in columns:
                val = row[key].strip() if row[key] else ""
                try:
                    columns[key].append(float(val))
                except (ValueError, TypeError):
                    columns[key].append(val)
        return Table(columns)

    def _parse_results(self, text: str) -> list[AstroObject]:
        table = self._csv_to_table(text)
        objects: list[AstroObject] = []

        for row in table:
            ra = 0.0
            dec = 0.0
            for ra_col in ("ra", "RA"):
                if ra_col in row.colnames:
                    try:
                        ra = float(row[ra_col])
                        break
                    except (ValueError, TypeError):
                        pass
            for dec_col in ("dec", "DEC"):
                if dec_col in row.colnames:
                    try:
                        dec = float(row[dec_col])
                        break
                    except (ValueError, TypeError):
                        pass

            name = ""
            for name_col in ("name", "Name"):
                if name_col in row.colnames:
                    name = str(row[name_col]).strip()
                    break

            extra: dict = {}
            for flux_col in ("flux_aper_b", "flux"):
                if flux_col in row.colnames:
                    try:
                        extra["flux"] = float(row[flux_col])
                        break
                    except (ValueError, TypeError):
                        pass
            for col in ("significance", "extent_flag", "hard_hm", "hard_ms"):
                if col in row.colnames:
                    try:
                        extra[col] = float(row[col])
                    except (ValueError, TypeError):
                        extra[col] = str(row[col])

            objects.append(
                AstroObject(
                    source="chandra",
                    object_id=name or f"cxo-{ra:.4f}-{dec:.4f}",
                    name=name or f"CXO {ra:.4f}{dec:+.4f}",
                    ra=ra,
                    dec=dec,
                    object_type="X-ray source",
                    extra=extra,
                )
            )
        return objects
