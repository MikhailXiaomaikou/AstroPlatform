"""SDSS DR18 connector via SkyServer SQL Search API."""

import asyncio
import csv
import io
import logging
from functools import partial

import httpx
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

logger = logging.getLogger(__name__)

SKYSERVER_SQL_URLS = [
    "https://skyserver.sdss.org/dr18/SkyServerWS/SearchTools/SqlSearch",
    "https://skyserver.sdss.org/dr17/SkyServerWS/SearchTools/SqlSearch",
]
SKYSERVER_SPECTRA_URLS = [
    "https://dr18.sdss.org/sas/dr18/spectro/sdss/redux/26/spectra",
    "https://data.sdss.org/sas/dr17/sdss/spectro/redux/26/spectra",
]
# Keep backward compat references
SKYSERVER_SQL_URL = SKYSERVER_SQL_URLS[0]
SKYSERVER_SPECTRA_URL = SKYSERVER_SPECTRA_URLS[0]


class SDSSConnector(BaseConnector):
    """Connector for SDSS DR18 via SkyServer SQL Search."""

    source_name = "sdss"

    async def _query_skyserver(self, sql: str) -> str:
        """Try multiple SkyServer mirrors until one responds."""
        last_err = None
        for url in SKYSERVER_SQL_URLS:
            try:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    resp = await client.get(url, params={"cmd": sql, "format": "csv"})
                    resp.raise_for_status()
                    return resp.text
            except (ValueError, TimeoutError, ConnectionError, OSError, httpx.HTTPStatusError) as e:
                logger.debug("SkyServer mirror %s failed: %s", url, e)
                last_err = e
                continue
        raise last_err or ConnectionError("All SDSS SkyServer mirrors unavailable")

    async def _query_region_fallback(self, ra: float, dec: float, radius: float) -> Table:
        """Fallback to astroquery's region service when SkyServer SQL is slow."""
        from astroquery.sdss import SDSS

        effective_radius = min(max(radius, 0.001), 0.05) * u.deg
        coord = SkyCoord(ra=ra, dec=dec, unit="deg", frame="icrs")
        loop = asyncio.get_running_loop()

        def _run_query():
            return SDSS.query_region(
                coordinates=coord,
                radius=effective_radius,
                photoobj_fields=["objid", "ra", "dec", "r", "type"],
                timeout=60,
                data_release=17,
                cache=False,
            )

        table = await loop.run_in_executor(None, _run_query)
        if table is None:
            return Table()
        return table

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        if ra is None or dec is None:
            ra, dec = await self._resolve_name(query)

        effective_radius = min(max(radius, 0.001), 0.05)
        sql = f"""SELECT TOP 50
            p.objid, p.ra, p.dec, p.r AS mag_r, p.type,
            dbo.fPhotoTypeN(p.type) AS type_name,
            s.z AS spec_z, s.class AS spec_class,
            n.distance
        FROM dbo.fGetNearbyObjEq({ra}, {dec}, {effective_radius * 60}) AS n
        JOIN PhotoObj AS p ON n.objID = p.objID
        LEFT JOIN SpecObj AS s ON s.bestobjid = p.objid
        WHERE p.mode = 1
        ORDER BY n.distance"""

        try:
            text = await self._query_skyserver(sql)
            table = self._parse_csv(text)
        except (ValueError, TimeoutError, ConnectionError, OSError, httpx.HTTPStatusError) as e:
            logger.debug("SkyServer SQL query failed, falling back to astroquery: %s", e)
            table = await self._query_region_fallback(ra, dec, effective_radius)
        return self._table_to_objects(table)

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch SDSS data for an object by objid.

        First tries to find a matching spectrum (plate-mjd-fiberid) and download the FITS.
        If no spectrum exists, returns the photometric data as a FITS table.
        """
        # Validate object_id is numeric (prevent SQL injection on remote SkyServer)
        clean_id = object_id.strip()
        if not clean_id.isdigit():
            raise ValueError(f"Invalid SDSS objid (must be numeric): {object_id}")

        # Try to find spectrum for this objid
        sql = f"""SELECT TOP 1 s.plate, s.mjd, s.fiberid, s.z, s.zErr, s.class
        FROM SpecObj AS s
        WHERE s.bestobjid = {clean_id}"""

        text = await self._query_skyserver(sql)
        spec_table = self._parse_csv(text)

        if len(spec_table) > 0:
            # Has spectrum — download FITS, try mirrors
            row = spec_table[0]
            plate = str(int(row["plate"])).zfill(4)
            mjd = str(int(row["mjd"]))
            fiberid = str(int(row["fiberid"])).zfill(4)
            fits_name = f"spec-{plate}-{mjd}-{fiberid}.fits"

            last_err = None
            resp = None
            for base_url in SKYSERVER_SPECTRA_URLS:
                try:
                    url = f"{base_url}/{plate}/{fits_name}"
                    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        break
                except (ValueError, TimeoutError, ConnectionError, OSError, httpx.HTTPStatusError) as e:
                    logger.debug("SDSS spectrum mirror %s failed: %s", base_url, e)
                    last_err = e
                    resp = None
            if resp is None:
                raise last_err or ConnectionError("All SDSS spectrum mirrors unavailable")

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
        WHERE p.objid = {clean_id}"""

        photo_text = await self._query_skyserver(sql_photo)
        photo_table = self._parse_csv(photo_text)
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

        loop = asyncio.get_running_loop()
        coord = await loop.run_in_executor(
            None, partial(SkyCoord.from_name, name)
        )
        return coord.ra.deg, coord.dec.deg

    def _parse_csv(self, text: str) -> Table:
        # Keep objid and similar large-int columns as strings to avoid float precision loss
        _string_columns = {"objid", "bestobjid", "specobjid", "fluxobjid"}

        lines = [line for line in text.strip().splitlines() if not line.startswith("#")]
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
            for mag_col in ("mag_r", "r"):
                if mag_col in row.colnames:
                    try:
                        mag = float(row[mag_col])
                        break
                    except (ValueError, TypeError):
                        pass

            type_name = str(row["type_name"]) if "type_name" in row.colnames else ""

            # Spectroscopic redshift from SpecObj LEFT JOIN
            redshift = None
            if "spec_z" in row.colnames:
                try:
                    z = float(row["spec_z"])
                    if z == z and z > -1:  # NaN check and sanity
                        redshift = z
                except (ValueError, TypeError):
                    pass

            extra: dict = {}
            if "spec_class" in row.colnames:
                sc = str(row["spec_class"]).strip()
                if sc and sc != "":
                    extra["spec_class"] = sc
            if "distance" in row.colnames:
                try:
                    extra["distance_arcmin"] = float(row["distance"])
                except (ValueError, TypeError):
                    pass

            objects.append(
                AstroObject(
                    source="sdss",
                    object_id=obj_id,
                    name=obj_id,
                    ra=ra,
                    dec=dec,
                    object_type=type_name,
                    magnitude=mag,
                    redshift=redshift,
                    extra=extra,
                )
            )
        return objects
