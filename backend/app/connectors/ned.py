"""NED (NASA/IPAC Extragalactic Database) connector via HTTP API."""

import io
import logging

import numpy as np
from astropy.table import Table

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry
from app.services.provenance_v2.non_standard_info_resolver import resolve_info_provenance

logger = logging.getLogger(__name__)

NED_LOOKUP_URL = "https://ned.ipac.caltech.edu/srs/ObjectLookup"
NED_SEARCH_URL = "https://ned.ipac.caltech.edu/cgi-bin/objsearch"
NED_REQUEST_TIMEOUT = 25.0


class NEDConnector(BaseConnector):
    """Connector for NASA/IPAC Extragalactic Database (NED)."""

    source_name = "ned"

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        import httpx

        if ra is not None and dec is not None:
            return await self._search_by_coords(ra, dec, radius)

        # Search by object name
        params = {
            "name": query,
        }

        async with httpx.AsyncClient(timeout=NED_REQUEST_TIMEOUT) as client:
            resp = await client.get(NED_LOOKUP_URL, params=params)
            resp.raise_for_status()

        data = resp.json()
        return self._parse_lookup_response(data)

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch NED summary data for an object and return as FITS table.

        object_id should be the NED object name (e.g. 'NGC 224', 'M 31').
        """
        import httpx

        params = {
            "name": object_id,
        }

        async with httpx.AsyncClient(timeout=NED_REQUEST_TIMEOUT) as client:
            resp = await client.get(NED_LOOKUP_URL, params=params)
            resp.raise_for_status()

        data = resp.json()
        objects = self._parse_lookup_response(data)

        if not objects:
            raise ValueError(f"No NED data found for '{object_id}'")

        # Build a table from the results
        table = Table()
        table["name"] = [obj.name for obj in objects]
        table["ra"] = [obj.ra for obj in objects]
        table["dec"] = [obj.dec for obj in objects]
        table["object_type"] = [obj.object_type for obj in objects]
        table["redshift"] = [obj.redshift if obj.redshift is not None else np.nan for obj in objects]
        table["magnitude"] = [obj.magnitude if obj.magnitude is not None else np.nan for obj in objects]

        # Add extra fields if available
        morphologies = []
        velocities = []
        for obj in objects:
            morphologies.append(obj.extra.get("morphology", ""))
            velocities.append(obj.extra.get("velocity", np.nan))
        table["morphology"] = morphologies
        table["velocity"] = velocities

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        safe_id = object_id.replace(" ", "_").replace("/", "_")
        return FITSFile(
            object_id=object_id,
            source="ned",
            data=buf.read(),
            filename=f"ned-{safe_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    async def _search_by_coords(self, ra: float, dec: float, radius: float) -> list[AstroObject]:
        """Search NED by coordinates using a cone search."""
        import httpx

        # NED cone search via IAU format
        radius_arcmin = radius * 60.0
        params = {
            "search_type": "Near Position Search",
            "in_csys": "Equatorial",
            "in_equinox": "J2000.0",
            "lon": f"{ra:.6f}d",
            "lat": f"{dec:.6f}d",
            "radius": str(radius_arcmin),
            "of": "json",
        }

        async with httpx.AsyncClient(timeout=NED_REQUEST_TIMEOUT) as client:
            resp = await client.get(NED_SEARCH_URL, params=params)
            resp.raise_for_status()

        try:
            data = resp.json()
            return self._parse_search_response(data)
        except (ValueError, TypeError, KeyError) as e:
            # NED may return non-JSON for some queries; return empty
            logger.debug("NED coord search response parsing failed: %s", e)
            return []

    def _parse_lookup_response(self, data: dict) -> list[AstroObject]:
        """Parse NED ObjectLookup JSON response."""
        objects = []

        # NED ObjectLookup returns various structures
        result_list = []
        if isinstance(data, dict):
            # Single result or wrapper
            if "Preferred" in data:
                result_list = [data]
            elif "ResultList" in data:
                result_list = data["ResultList"]
            elif isinstance(data.get("result"), list):
                result_list = data["result"]
            else:
                # Try treating the whole dict as a single result
                result_list = [data]
        elif isinstance(data, list):
            result_list = data

        for item in result_list:
            try:
                obj = self._parse_ned_object(item)
                if obj is not None:
                    objects.append(obj)
            except (KeyError, TypeError, ValueError):
                continue

        return objects

    def _parse_ned_object(self, item: dict) -> AstroObject | None:
        """Parse a single NED result item into an AstroObject."""
        # Try multiple key patterns NED uses
        preferred = item.get("Preferred", item)

        def _ned_str(val) -> str:
            if isinstance(val, dict):
                return str(val.get("Value", val.get("value", ""))).strip()
            return str(val).strip()

        name = ""
        for key in ("Name", "name", "prefname", "ObjName"):
            if key in preferred:
                name = _ned_str(preferred[key])
                break
        if not name and "Name" in item:
            name = _ned_str(item["Name"])
        if not name:
            return None

        ra = 0.0
        dec = 0.0
        # L19 (audit 2026-04-20): log 实际命中的列名方便未来 NED API 改
        # schema 时定位问题 (列名 fallback 顺序硬编码, 没 schema 版本 pin).
        ra_source_col = None
        for ra_key in ("RA", "ra", "RA(deg)"):
            if ra_key in preferred:
                try:
                    ra = float(preferred[ra_key])
                    ra_source_col = ra_key
                    break
                except (ValueError, TypeError):
                    pass
        dec_source_col = None
        for dec_key in ("DEC", "dec", "Dec(deg)"):
            if dec_key in preferred:
                try:
                    dec = float(preferred[dec_key])
                    dec_source_col = dec_key
                    break
                except (ValueError, TypeError):
                    pass
        if ra_source_col or dec_source_col:
            logger.debug(
                "NED column resolution: ra via %s, dec via %s",
                ra_source_col, dec_source_col,
            )

        # Fallback: try Position sub-dict
        pos = preferred.get("Position", {})
        if ra == 0.0 and "RA" in pos:
            try:
                ra = float(pos["RA"])
            except (ValueError, TypeError):
                pass
        if dec == 0.0 and "Dec" in pos:
            try:
                dec = float(pos["Dec"])
            except (ValueError, TypeError):
                pass

        obj_type = ""
        for key in ("Type", "type", "ObjType"):
            if key in preferred:
                val = preferred[key]
                if isinstance(val, dict):
                    obj_type = str(val.get("Value", val.get("value", ""))).strip()
                else:
                    obj_type = str(val).strip()
                break

        redshift = None
        for key in ("Redshift", "redshift", "z"):
            if key in preferred:
                try:
                    redshift = float(preferred[key])
                    break
                except (ValueError, TypeError):
                    pass

        magnitude = None
        for key in ("Magnitude", "magnitude", "Bmag"):
            if key in preferred:
                try:
                    magnitude = float(preferred[key])
                    break
                except (ValueError, TypeError):
                    pass

        provenance_dataset = resolve_info_provenance(item.get("INFO") or item.get("Info"), service_hint="ned")
        extra: dict = {"_provenance_dataset": provenance_dataset} if provenance_dataset else {}
        for key in ("Morphology", "morphology", "morph_type"):
            if key in preferred:
                extra["morphology"] = _ned_str(preferred[key])
                break
        for key in ("Velocity", "velocity"):
            if key in preferred:
                try:
                    extra["velocity"] = float(preferred[key])
                    break
                except (ValueError, TypeError):
                    pass

        # PART Y Batch 4: stamp archive_version. NED is a continuously-
        # updated extragalactic database; no DR version, use the service
        # name as the archive_version label.
        extra["archive_version"] = "NED"
        return AstroObject(
            source="ned",
            object_id=name,
            name=name,
            ra=ra,
            dec=dec,
            object_type=obj_type,
            magnitude=magnitude,
            redshift=redshift,
            extra=extra,
        )

    def _parse_search_response(self, data) -> list[AstroObject]:
        """Parse NED near-position search JSON response."""
        objects = []
        items = []
        if isinstance(data, dict):
            items = data.get("ResultList", data.get("result", []))
        elif isinstance(data, list):
            items = data

        for item in items[:50]:  # Limit results
            try:
                obj = self._parse_ned_object(item)
                if obj is not None:
                    objects.append(obj)
            except (KeyError, TypeError, ValueError):
                continue

        return objects
