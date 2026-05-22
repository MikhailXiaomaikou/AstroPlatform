"""Minor Planet Center connector via astroquery.mpc.

Reference: IAU Minor Planet Center — official designation + osculating orbital
elements database (https://minorplanetcenter.net/). Accessed via astroquery.mpc
(Ginsburg+ 2019 AJ 157, 98, bibcode 2019AJ....157...98G).
M0 Commit 2 (2026-05-18): new connector, mirrors TwoMASSConnector shape.
"""

from __future__ import annotations

import asyncio
import logging
import re
from functools import partial

from astropy.table import Table

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry
from app.services.provenance_v2.ivoa_dataorigin_resolver import resolve_ivoa_dataorigin

logger = logging.getLogger(__name__)

MPC_ARCHIVE_VERSION = "mpc-2026"

# Two-letter group inside a provisional designation (the half-month sequence)
# does not count as an "asteroid name".
# See IAU Minor Planet Names: https://minorplanetcenter.net/iau/info/Astrometry.html
_PROVISIONAL_LETTERS = frozenset({
    "AB", "AC", "AD", "AE", "AF", "AG", "AH", "AJ", "AK", "AL", "AM", "AN",
    "AO", "AP", "AQ", "AR", "AS", "AT", "AU", "AV", "AW", "AX", "AY", "AZ",
    "BA", "BB", "BC", "BD", "BE", "BF", "BG", "BH", "BJ", "BK", "BL", "BM",
    "TB", "TC", "TD",  # common ones only; the fallback path preserves the original designation
})


def _normalize_mpc_designations(raw: str) -> list[str]:
    """Expand the input into multiple designation candidates to improve astroquery.mpc hit rate.

    Examples:
      "(3200) Phaethon"  -> ["3200", "Phaethon", "(3200) Phaethon"]
      "Apophis"          -> ["Apophis"]
      "1983 TB"          -> ["1983 TB"]  (provisional designation, kept intact)
      "99942"            -> ["99942"]
    """
    out: list[str] = []
    raw = (raw or "").strip()
    if not raw:
        return []

    # Provisional designations like "1983 TB" / "2024 YR4" must not be split.
    if re.match(r"^\d{4}\s+[A-Z]{1,2}\d*$", raw):
        return [raw]

    # Extract a numeric IAU number (1-7 digits).
    m_num = re.search(r"\b(\d{1,7})\b", raw)
    if m_num:
        out.append(m_num.group(1))

    # Extract a plain name (capitalized alphabetic word, length >= 3,
    # excluding two-letter provisional codes).
    for m_name in re.finditer(r"\b([A-Z][a-zA-Z]{2,})\b", raw):
        token = m_name.group(1)
        if token.upper() not in _PROVISIONAL_LETTERS and token not in out:
            out.append(token)

    # Verbatim fallback (e.g. "(3200) Phaethon" stays as the last candidate).
    if raw not in out:
        out.append(raw)

    return out

# Typical MPC orbital element fields (dict key names returned by astroquery.mpc)
_ORBIT_FIELDS = (
    "absolute_magnitude",       # H
    "phase_slope",              # G
    "semimajor_axis",           # a (au)  — astroquery field name may be "a" or "semimajor_axis"
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
        """Try MPC asteroid + comet queries; for each target_type also try
        multiple designation variants (number / name / verbatim) to improve
        hit rate on parenthesized forms like '(3200) Phaethon'. Returns an
        astropy Table or None.
        """
        from astroquery.mpc import MPC

        candidates = _normalize_mpc_designations(designation)
        results = None
        for desig in candidates:
            for target_type in ("asteroid", "comet"):
                try:
                    raw = MPC.query_object(target_type=target_type, designation=desig)
                except Exception as exc:
                    logger.debug(
                        "MPC %s query failed for %s (variant of %r): %s",
                        target_type, desig, designation, exc,
                    )
                    raw = None
                if raw:
                    results = raw
                    break
            if results:
                break

        if not results:
            return None
        if isinstance(results, list):
            # astroquery.mpc returns list[dict]; convert to Table
            return Table(rows=results) if results else None
        if isinstance(results, Table):
            return results
        # fallback
        try:
            return Table(results)
        except Exception as exc:
            logger.warning("MPC results could not be converted to Table: %s", exc)
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
            # Name priority: name column first, then designation, then the query argument
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

            # Extract orbital elements
            for key in _ORBIT_FIELDS:
                if key in row.colnames:
                    try:
                        extra[key] = float(row[key])
                    except (ValueError, TypeError):
                        try:
                            extra[key] = str(row[key])
                        except Exception as e:
                            logger.debug("MPC extra field %s str() fallback failed: %s", key, e)

            extra["source_reference"] = (
                "Minor Planet Center, IAU — official designation/orbit database"
            )

            # MPC results contain orbital elements, not RA/Dec — use 0.0/0.0 as placeholders (downstream tools consume extra)
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
