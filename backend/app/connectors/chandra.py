"""Chandra/XMM X-ray connector via CSC2 cone search API."""

import asyncio
import io
import logging
from functools import partial
import re

import httpx
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.io.votable import parse_single_table
from astropy.table import Table

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

logger = logging.getLogger(__name__)

CSC2_CONE_URL = "https://cda.cfa.harvard.edu/csc21scs/coneSearch"


class ChandraConnector(BaseConnector):
    """Connector for Chandra X-ray Observatory via CSC2 cone search."""

    source_name = "chandra"

    @with_retry(max_retries=1, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        if ra is None or dec is None:
            ra, dec = await self._resolve_name(query)

        params = {
            "RA": str(ra),
            "DEC": str(dec),
            "SR": str(radius),
            "VERB": "1",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(CSC2_CONE_URL, params=params)
            resp.raise_for_status()

        return self._parse_results(resp.content)

    @with_retry(max_retries=1, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch Chandra source data and return as FITS table.

        object_id should be a CSC source name (e.g. '2CXO J004244.3+411609').
        """
        ra, dec = await self._resolve_object_id(object_id)
        params = {"RA": str(ra), "DEC": str(dec), "SR": "0.01", "VERB": "1"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(CSC2_CONE_URL, params=params)
            resp.raise_for_status()

        table = self._votable_to_table(resp.content)
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
        loop = asyncio.get_event_loop()
        coord = await loop.run_in_executor(None, partial(SkyCoord.from_name, name))
        return coord.ra.deg, coord.dec.deg

    async def _resolve_object_id(self, object_id: str) -> tuple[float, float]:
        match = re.search(
            r"J(?P<h>\d{2})(?P<m>\d{2})(?P<s>\d{2}(?:\.\d+)?)"
            r"(?P<sign>[+-])(?P<d>\d{2})(?P<dm>\d{2})(?P<ds>\d{2}(?:\.\d+)?)",
            object_id,
        )
        if match:
            sign = "-" if match.group("sign") == "-" else "+"
            coord = SkyCoord(
                f"{match.group('h')}h{match.group('m')}m{match.group('s')}s "
                f"{sign}{match.group('d')}d{match.group('dm')}m{match.group('ds')}s",
                unit=(u.hourangle, u.deg),
                frame="icrs",
            )
            return coord.ra.deg, coord.dec.deg
        return await self._resolve_name(object_id)

    def _votable_to_table(self, raw: bytes) -> Table:
        """Parse a CSC2 Simple Cone Search VOTable response."""
        if not raw.strip():
            return Table()
        table = parse_single_table(io.BytesIO(raw)).to_table(use_names_over_ids=True)
        if table is None:
            return Table()
        return table

    def _parse_results(self, raw: bytes) -> list[AstroObject]:
        table = self._votable_to_table(raw)
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
            for col in ("significance", "extent_flag", "hard_hm", "hard_ms", "err_ellipse_r0", "err_ellipse_r1"):
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
