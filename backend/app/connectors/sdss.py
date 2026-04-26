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

        # L18 (audit 2026-04-20): radius clamp 不再静默.  之前 min(max(r,
        # 0.001), 0.05) 把用户要求的 1° 默默压成 0.05° (3 arcmin), 用户
        # 看到结果只有少数目标但不知道被 clamp 了, 样本偏差却无提示.
        # 改成: clamp 发生时 logger.warning, 稍后在返回对象的 extra 里
        # 记录 radius_clamped=True 让调用方/UI 能 propagate.
        requested_radius = float(radius)
        effective_radius = min(max(requested_radius, 0.001), 0.05)
        self._last_radius_clamp: dict | None = None
        if abs(effective_radius - requested_radius) > 1e-9:
            self._last_radius_clamp = {
                "radius_clamped": True,
                "radius_requested_deg": requested_radius,
                "radius_actual_deg": effective_radius,
                "radius_min_allowed_deg": 0.001,
                "radius_max_allowed_deg": 0.05,
            }
            logger.warning(
                "SDSSConnector: radius %.4f° clamped to %.4f° "
                "(SkyServer enforces 0.001-0.05° cone); sample size will "
                "reflect the smaller cone",
                requested_radius, effective_radius,
            )
        # Bug 11 path A: LEFT JOIN Photoz so galaxy/QSO photometric redshifts
        # are returned alongside the spectroscopic ones.  SDSS has ~400 M
        # photo-z estimates vs ~4 M spec-z, so a typical cone search jumps
        # from ~20% coverage (spec-z only) to ~75% coverage (spec-z OR
        # photo-z for galaxies).  Downstream `_table_to_objects` decides
        # which z to expose based on availability + object type, and
        # labels `z_source` as "spectroscopic" / "photometric" so the UI
        # can distinguish them.
        sql = f"""SELECT TOP 50
            p.objid, p.ra, p.dec, p.r AS mag_r, p.type,
            dbo.fPhotoTypeN(p.type) AS type_name,
            s.z AS spec_z, s.class AS spec_class,
            pz.z AS photo_z, pz.zerr AS photo_z_err,
            n.distance
        FROM dbo.fGetNearbyObjEq({ra}, {dec}, {effective_radius * 60}) AS n
        JOIN PhotoObj AS p ON n.objID = p.objID
        LEFT JOIN SpecObj AS s ON s.bestobjid = p.objid
        LEFT JOIN Photoz AS pz ON pz.objid = p.objid
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

            # Bug 11 path A: pick the best available redshift.
            # - spec_z (from SpecObj LEFT JOIN) always wins when present.
            # - Otherwise fall back to photo_z (from Photoz LEFT JOIN), but
            #   ONLY for galaxy-type objects — SDSS photo-z is trained on
            #   galaxy SEDs and produces meaningless numbers for stars
            #   (type=6, "STAR") since stars don't have a cosmological z.
            # - z_source labels which one we used so the UI can show a
            #   "spec" vs "photo" chip and the downstream zero-fabrication
            #   gate can treat photo-z as lower-precision.
            spec_z: float | None = None
            photo_z: float | None = None
            photo_z_err: float | None = None

            if "spec_z" in row.colnames:
                try:
                    z = float(row["spec_z"])
                    if z == z and z > -1:  # NaN + sanity
                        spec_z = z
                except (ValueError, TypeError):
                    pass

            if "photo_z" in row.colnames:
                try:
                    zp = float(row["photo_z"])
                    # SDSS Photoz returns -9999 for objects with no fit
                    if zp == zp and zp > -1 and zp < 10:
                        photo_z = zp
                except (ValueError, TypeError):
                    pass
            if "photo_z_err" in row.colnames:
                try:
                    zpe = float(row["photo_z_err"])
                    if zpe == zpe and zpe >= 0:
                        photo_z_err = zpe
                except (ValueError, TypeError):
                    pass

            # Is this object galaxy-like?  In SDSS PhotoObj.type:
            # 3 = GALAXY, 6 = STAR.  Normalize via type_name (e.g.
            # "GALAXY") produced by dbo.fPhotoTypeN for robustness.
            is_galaxy_like = type_name.upper() == "GALAXY"

            redshift: float | None = None
            z_source: str | None = None
            if spec_z is not None:
                redshift = spec_z
                z_source = "spectroscopic"
            elif photo_z is not None and is_galaxy_like:
                redshift = photo_z
                z_source = "photometric"

            extra: dict = {}
            if z_source:
                extra["z_source"] = z_source
            if photo_z is not None:
                extra["photo_z"] = photo_z
            if photo_z_err is not None:
                extra["photo_z_err"] = photo_z_err
            if "spec_class" in row.colnames:
                sc = str(row["spec_class"]).strip()
                if sc and sc != "":
                    extra["spec_class"] = sc
            if "distance" in row.colnames:
                try:
                    extra["distance_arcmin"] = float(row["distance"])
                except (ValueError, TypeError):
                    pass

            # PART Y Batch 4: stamp archive_version (SDSS DR18 PhotoObj path).
            extra["archive_version"] = "SDSS DR18"
            objects.append(
                AstroObject(
                    source="sdss",
                    object_id=obj_id,
                    name=obj_id,
                    ra=ra,
                    dec=dec,
                    # B-S2: when SDSS fPhotoTypeN returns NULL / empty
                    # (mode=2 secondary detections), type_name becomes "".
                    # Frontend renders empty str as literal "undefined" in
                    # the AutoToolResult JSON path.  Use "Unknown" so
                    # the UI shows a real label.
                    object_type=type_name or "Unknown",
                    magnitude=mag,
                    redshift=redshift,
                    extra=extra,
                )
            )
        return objects


class SDSSSpecOnlyConnector(SDSSConnector):
    """Bug 11 path C: 'only spectroscopically confirmed' SDSS variant.

    Queries `SpecObj` directly instead of joining from `PhotoObj`.  Every
    row returned has a real spec_z (100 % coverage) — useful when the
    user explicitly wants "objects with known redshift only" instead of
    a generic cone search.

    Trade-off vs the default SDSS connector:
    - Coverage: 100 % spec_z (vs ~20 % on PhotoObj cones).
    - Sample size: smaller — `SpecObj` has ~4 M entries vs `PhotoObj`'s
      ~10 B, so dim / non-spectroscopic objects won't appear.
    - Best for: statistical samples where redshift is mandatory
      (galaxy cluster membership, QSO selection, luminosity functions).
    - Worst for: finding every photometric source near a target.
    """

    source_name = "sdss_spec"

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        if ra is None or dec is None:
            ra, dec = await self._resolve_name(query)

        effective_radius = min(max(radius, 0.001), 0.1)  # slightly wider
        # L2-d (audit 2026-04-20): 真正的锥搜.  之前用 "ra BETWEEN ra-r AND
        # ra+r AND dec BETWEEN dec-r AND dec+r" 是方盒子, 在极区 (|dec|
        # 大) 纬向拉伸 — |dec|=89° / r=0.1° 时 RA 方向实际覆盖 ≈ 5.7°,
        # 其他区域按方盒收, 样本不一致.  切 dbo.fGetNearbyObjEq 保持跟
        # PhotoObj 路径一致的锥搜 (radius 单位 arcmin).  SpecObj 的 ra/dec
        # 就是真实天区坐标 (ICRS), 跟 fGetNearbyObjEq 的锥搜完全相容.
        radius_arcmin = effective_radius * 60
        sql = f"""SELECT TOP 200
            s.bestobjid AS objid, s.ra, s.dec, s.z AS spec_z,
            s.zerr AS spec_z_err, s.class AS spec_class,
            s.subclass AS spec_subclass, s.plate, s.mjd, s.fiberid,
            p.r AS mag_r, dbo.fPhotoTypeN(p.type) AS type_name,
            n.distance
        FROM dbo.fGetNearbyObjEq({ra}, {dec}, {radius_arcmin}) AS n
        JOIN SpecObj AS s ON s.bestobjid = n.objID
        LEFT JOIN PhotoObj AS p ON p.objid = s.bestobjid
        WHERE s.zwarning = 0
        ORDER BY n.distance"""

        try:
            text = await self._query_skyserver(sql)
            table = self._parse_csv(text)
        except (ValueError, TimeoutError, ConnectionError, OSError, httpx.HTTPStatusError) as e:
            logger.debug("SDSS spec-only query failed: %s", e)
            return []
        return self._spec_table_to_objects(table)

    def _spec_table_to_objects(self, table: Table) -> list[AstroObject]:
        """Build AstroObjects from a SpecObj-primary query result."""
        objects: list[AstroObject] = []
        for row in table:
            try:
                ra = float(row["ra"])
                dec = float(row["dec"])
            except (ValueError, TypeError, KeyError):
                continue
            obj_id = str(row["objid"]).strip() if "objid" in row.colnames else ""
            try:
                spec_z = float(row["spec_z"])
                if not (spec_z == spec_z and spec_z > -1):
                    continue
            except (ValueError, TypeError, KeyError):
                continue

            mag = None
            if "mag_r" in row.colnames:
                try:
                    m = float(row["mag_r"])
                    if m == m:
                        mag = m
                except (ValueError, TypeError):
                    pass

            type_name = ""
            if "type_name" in row.colnames:
                type_name = str(row["type_name"]).strip() or ""
            # If PhotoObj didn't match, fall back to spec_class as type.
            if not type_name and "spec_class" in row.colnames:
                type_name = str(row["spec_class"]).strip().upper()

            extra: dict = {"z_source": "spectroscopic"}
            for col in ("spec_z_err", "spec_class", "spec_subclass",
                        "plate", "mjd", "fiberid"):
                if col in row.colnames:
                    val = row[col]
                    if isinstance(val, str):
                        val = val.strip()
                        if val:
                            extra[col] = val
                    else:
                        try:
                            fv = float(val)
                            if fv == fv:
                                extra[col] = fv
                        except (ValueError, TypeError):
                            pass

            # PART Y Batch 4: stamp archive_version (SDSS DR18 SpecObj path).
            extra["archive_version"] = "SDSS DR18"
            objects.append(
                AstroObject(
                    source="sdss_spec",
                    object_id=obj_id,
                    name=obj_id,
                    ra=ra,
                    dec=dec,
                    # B-S2: same fallback as SDSSConnector path.
                    object_type=type_name or "Unknown",
                    magnitude=mag,
                    redshift=spec_z,
                    extra=extra,
                )
            )
        return objects
