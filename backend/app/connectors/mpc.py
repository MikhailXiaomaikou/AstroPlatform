"""Minor Planet Center connector via astroquery.mpc.

Reference: IAU Minor Planet Center — 官方 designation + osculating orbital
elements 数据库 (https://minorplanetcenter.net/). 通过 astroquery.mpc 访问
(Ginsburg+ 2019 AJ 157, 98, bibcode 2019AJ....157...98G).
M0 Commit 2 (2026-05-18): new connector, mirrors TwoMASSConnector shape.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from astropy.table import Table

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry
from app.services.provenance_v2.ivoa_dataorigin_resolver import resolve_ivoa_dataorigin

logger = logging.getLogger(__name__)

MPC_ARCHIVE_VERSION = "mpc-2026"

# 典型 MPC 轨道根数字段(astroquery.mpc 返回的 dict 字段名)
_ORBIT_FIELDS = (
    "absolute_magnitude",       # H
    "phase_slope",              # G
    "semimajor_axis",           # a (au)  — astroquery 字段名可能是 "a" 或 "semimajor_axis"
    "a",
    "eccentricity",
    "e",
    "inclination",
    "i",
    "argument_of_perihelion",
    "ascending_node",
    "mean_anomaly",
    "perihelion_distance",
    "aphelion_distance",
    "perihelion_date_jd",
    "epoch_jd",
    "epoch",
    "orbital_period",
)


class MPCConnector(BaseConnector):
    """Query MPC for asteroid / comet orbital elements + designation lookup."""

    source_name = "mpc"

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None,
        radius: float = 0.1,
    ) -> list[AstroObject]:
        if not query:
            return []
        loop = asyncio.get_running_loop()
        table = await asyncio.wait_for(
            loop.run_in_executor(None, partial(self._query_mpc, query)),
            timeout=30.0,
        )
        if table is None or len(table) == 0:
            return []
        return self._table_to_objects(table, designation=query)

    def _query_mpc(self, designation: str) -> Table | None:
        """先按小行星查,失败再按彗星查;返回 astropy Table 或 None。"""
        from astroquery.mpc import MPC

        results = None
        for target_type in ("asteroid", "comet"):
            try:
                raw = MPC.query_object(target_type=target_type, designation=designation)
            except Exception as exc:
                logger.debug("MPC %s query failed for %s: %s", target_type, designation, exc)
                raw = None
            if raw:
                results = raw
                break

        if not results:
            return None
        if isinstance(results, list):
            # astroquery.mpc 返回 list[dict];转成 Table
            return Table(rows=results) if results else None
        if isinstance(results, Table):
            return results
        # fallback
        try:
            return Table(results)
        except Exception as exc:
            logger.warning("MPC results 无法转 Table: %s", exc)
            return None

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        raise NotImplementedError(
            "MPC 不提供 FITS。 轨道根数 JSON 请用 query_mpc_orbit ai_tool."
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
            service_hint="mpc",
            archive_version=MPC_ARCHIVE_VERSION,
        )
        for row in table:
            # 名称: 优先 name,再 designation,再传入的 query
            name = designation
            for name_col in ("name", "designation", "number"):
                if name_col in row.colnames:
                    try:
                        candidate = str(row[name_col]).strip()
                        if candidate and candidate.lower() not in ("none", "nan", "--"):
                            name = candidate
                            break
                    except (ValueError, TypeError):
                        continue

            extra: dict = {}
            if provenance_dataset:
                extra["_provenance_dataset"] = provenance_dataset

            # 提取轨道根数
            for key in _ORBIT_FIELDS:
                if key in row.colnames:
                    try:
                        extra[key] = float(row[key])
                    except (ValueError, TypeError):
                        try:
                            extra[key] = str(row[key])
                        except Exception:
                            pass

            extra["source_reference"] = (
                "Minor Planet Center, IAU — official designation/orbit database"
            )

            # MPC results 是轨道根数,没有 RA/Dec — 用 0.0/0.0 占位(下游工具消费 extra)
            magnitude = extra.get("absolute_magnitude")
            if not isinstance(magnitude, (int, float)):
                magnitude = None

            objects.append(
                AstroObject(
                    source=self.source_name,
                    object_id=name,
                    name=name,
                    ra=0.0,
                    dec=0.0,
                    object_type="solar_system_body",
                    magnitude=magnitude,
                    extra=extra,
                )
            )
        return objects
