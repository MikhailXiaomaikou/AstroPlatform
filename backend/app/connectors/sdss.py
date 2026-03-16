"""SDSS DR18 connector via SkyServer SQL Search API."""

import asyncio
import csv
import io
from functools import partial

import httpx
from astropy.table import Table

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

SKYSERVER_SQL_URL = "https://skyserver.sdss.org/dr18/SkyServerWS/SearchTools/SqlSearch"
SKYSERVER_SPECTRA_URL = "https://dr18.sdss.org/sas/dr18/spectro/sdss/redux/26/spectra"


class SDSSConnector(BaseConnector):
    """Connector for SDSS DR18 via SkyServer SQL Search."""

    source_name = "sdss"

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        if ra is None or dec is None:
            ra, dec = await self._resolve_name(query)

        sql = f"""SELECT TOP 50
            p.objid, p.ra, p.dec, p.r AS mag_r, p.type,
            dbo.fPhotoTypeN(p.type) AS type_name
        FROM PhotoObj AS p
        JOIN dbo.fGetNearbyObjEq({ra}, {dec}, {radius * 60}) AS n ON n.objID = p.objID
        ORDER BY p.r"""

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(SKYSERVER_SQL_URL, params={"cmd": sql, "format": "csv"})
            resp.raise_for_status()

        table = self._parse_csv(resp.text)
        return self._table_to_objects(table)

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch SDSS data for an object by objid.

        First tries to find a matching spectrum (plate-mjd-fiberid) and download the FITS.
        If no spectrum exists, returns the photometric data as a FITS table.
        """
        # Try to find spectrum for this objid
        sql = f"""SELECT TOP 1 s.plate, s.mjd, s.fiberid, s.z, s.zErr, s.class
        FROM SpecObj AS s
        WHERE s.bestobjid = {object_id}"""

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(SKYSERVER_SQL_URL, params={"cmd": sql, "format": "csv"})
            resp.raise_for_status()

        spec_table = self._parse_csv(resp.text)

        if len(spec_table) > 0:
            # Has spectrum — download FITS
            row = spec_table[0]
            plate = str(int(row["plate"])).zfill(4)
            mjd = str(int(row["mjd"]))
            fiberid = str(int(row["fiberid"])).zfill(4)
            url = f"{SKYSERVER_SPECTRA_URL}/{plate}/spec-{plate}-{mjd}-{fiberid}.fits"

            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()

            return FITSFile(
                object_id=object_id,
                source="sdss",
                data=resp.content,
                filename=f"sdss-spec-{plate}-{mjd}-{fiberid}.fits",
            )

        # No spectrum — return photometric data as FITS table
        sql_photo = f"""SELECT p.objid, p.ra, p.dec, p.u, p.g, p.r, p.i, p.z,
            p.Err_u, p.Err_g, p.Err_r, p.Err_i, p.Err_z,
            dbo.fPhotoTypeN(p.type) AS type_name
        FROM PhotoObj AS p
        WHERE p.objid = {object_id}"""

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(SKYSERVER_SQL_URL, params={"cmd": sql_photo, "format": "csv"})
            resp.raise_for_status()

        photo_table = self._parse_csv(resp.text)
        if len(photo_table) == 0:
            raise ValueError(f"No SDSS data found for objid {object_id}")

        buf = io.BytesIO()
        photo_table.write(buf, format="fits")
        buf.seek(0)

        return FITSFile(
            object_id=object_id,
            source="sdss",
            data=buf.read(),
            filename=f"sdss-photo-{object_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    async def _resolve_name(self, name: str) -> tuple[float, float]:
        from astropy.coordinates import SkyCoord

        loop = asyncio.get_event_loop()
        coord = await loop.run_in_executor(
            None, partial(SkyCoord.from_name, name)
        )
        return coord.ra.deg, coord.dec.deg

    def _parse_csv(self, text: str) -> Table:
        # Keep objid and similar large-int columns as strings to avoid float precision loss
        _string_columns = {"objid", "bestobjid", "specobjid", "fluxobjid"}

        lines = [l for l in text.strip().splitlines() if not l.startswith("#")]
        if len(lines) < 2:
            return Table()
        reader = csv.DictReader(lines)
        rows = list(reader)
        if not rows:
            return Table()
        columns: dict[str, list] = {key: [] for key in rows[0].keys()}
        for row in rows:
            for key in columns:
                val = row[key]
                if key.lower() in _string_columns:
                    columns[key].append(val.strip())
                else:
                    try:
                        columns[key].append(float(val))
                    except (ValueError, TypeError):
                        columns[key].append(val)
        return Table(columns)

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects = []
        for row in table:
            ra = float(row["ra"]) if "ra" in row.colnames else 0.0
            dec = float(row["dec"]) if "dec" in row.colnames else 0.0
            obj_id = str(row["objid"]).strip() if "objid" in row.colnames else ""

            mag = None
            if "mag_r" in row.colnames:
                try:
                    mag = float(row["mag_r"])
                except (ValueError, TypeError):
                    pass

            type_name = str(row["type_name"]) if "type_name" in row.colnames else ""

            objects.append(
                AstroObject(
                    source="sdss",
                    object_id=obj_id,
                    name=obj_id,
                    ra=ra,
                    dec=dec,
                    object_type=type_name,
                    magnitude=mag,
                )
            )
        return objects
