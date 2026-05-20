"""JPL Horizons connector — authoritative solar system ephemeris.

References:
- Giorgini+ 1996 BAAS 28, 1158 (Horizons system, bibcode 1996DPS....28.2504G)
- Ginsburg+ 2019 AJ 157, 98 (astroquery, bibcode 2019AJ....157...98G)

Provides ephemerides + 基本物理量 for planets, asteroids, comets, spacecraft.
M0 Commit 2 (2026-05-18): polished from placeholder to provenance-v2 compliant
connector, mirroring TwoMASSConnector shape.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from functools import partial

from astropy.table import Table

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry
from app.services.provenance_v2.ivoa_dataorigin_resolver import resolve_ivoa_dataorigin

logger = logging.getLogger(__name__)

HORIZONS_ARCHIVE_VERSION = "horizons-2026"


class JPLHorizonsConnector(BaseConnector):
    """Query JPL Horizons for solar system body ephemerides + 物理量."""

    source_name = "jpl"

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None,
        radius: float = 0.1,
    ) -> list[AstroObject]:
        """按 designation/name 查询单点星历(now → now+1d),返回 AstroObject 列表."""
        if not query:
            return []
        loop = asyncio.get_running_loop()
        table = await asyncio.wait_for(
            loop.run_in_executor(None, partial(self._fetch_now_ephemeris, query)),
            timeout=30.0,
        )
        if table is None or len(table) == 0:
            return []
        return self._table_to_objects(table, designation=query)

    def _fetch_now_ephemeris(self, designation: str) -> Table | None:
        """单点 ephemeris (UTC now)。"""
        from astroquery.jplhorizons import Horizons

        now_dt = datetime.now(timezone.utc)
        start = now_dt.isoformat()[:10]
        stop = (now_dt + timedelta(days=1)).isoformat()[:10]
        obj = Horizons(
            id=designation,
            location="500@10",  # heliocentric, Sun barycenter
            epochs={"start": start, "stop": stop, "step": "1d"},
        )
        return obj.ephemerides()

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        raise NotImplementedError(
            "JPL Horizons 不提供 FITS。 时间序列星历请用 fetch_horizons_ephemeris ai_tool."
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    def _table_to_objects(
        self, table: Table, *, designation: str = "",
    ) -> list[AstroObject]:
        objects: list[AstroObject] = []
        provenance_dataset = resolve_ivoa_dataorigin(
            table,
            service_hint="jpl",
            archive_version=HORIZONS_ARCHIVE_VERSION,
        )
        for row in table:
            name = (
                str(row["targetname"]) if "targetname" in row.colnames else designation
            )
            ra = _safe_float(row, "RA") or 0.0
            dec = _safe_float(row, "DEC") or 0.0
            V = _safe_float(row, "V")
            r_au = _safe_float(row, "r")
            delta_au = _safe_float(row, "delta")
            alpha_deg = _safe_float(row, "alpha")
            light_time_min = _safe_float(row, "lighttime")

            extra: dict = {}
            if provenance_dataset:
                extra["_provenance_dataset"] = provenance_dataset
            if r_au is not None:
                extra["heliocentric_distance_au"] = r_au
            if delta_au is not None:
                extra["geocentric_distance_au"] = delta_au
            if alpha_deg is not None:
                extra["phase_angle_deg"] = alpha_deg
            if light_time_min is not None:
                extra["light_time_min"] = light_time_min
            extra["source_reference"] = (
                "JPL Horizons (Giorgini+ 1996, 1996DPS....28.2504G)"
            )

            objects.append(
                AstroObject(
                    source=self.source_name,
                    object_id=name,
                    name=name,
                    ra=ra,
                    dec=dec,
                    object_type="solar_system_body",
                    magnitude=V,
                    extra=extra,
                )
            )
        return objects


def _safe_float(row, col: str) -> float | None:
    if col not in row.colnames:
        return None
    try:
        val = float(row[col])
    except (ValueError, TypeError):
        return None
    import math
    if not math.isfinite(val):
        return None
    return val
