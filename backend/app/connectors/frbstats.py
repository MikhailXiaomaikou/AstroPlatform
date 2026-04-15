"""FRBSTATS connector — FRB event database.

Reference: CHIME/FRB Collaboration 2021 ApJS 257, 59 (Catalog 1).
Queries the public FRB catalogue at https://www.herta-experiment.org/frbstats/

Returns FRB events with dispersion measure, redshift estimates,
and localization.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.connectors.base import AstroObject, BaseConnector, FITSFile

logger = logging.getLogger(__name__)

FRBSTATS_API = "https://www.herta-experiment.org/frbstats/api/events"


class FRBStatsConnector(BaseConnector):
    """Query the FRBSTATS public FRB catalogue."""

    source_name = "frbstats"

    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None,
        radius: float = 5.0,
    ) -> list[AstroObject]:
        """Search for FRBs by name (TNS ID like "FRB20121102A") or coordinates."""
        def _query():
            try:
                with httpx.Client(timeout=20.0) as client:
                    resp = client.get(FRBSTATS_API)
                    if resp.status_code != 200:
                        return []
                    data = resp.json()
                    events = data.get("events", []) if isinstance(data, dict) else data
                    return events or []
            except Exception as e:
                logger.warning("FRBSTATS query failed: %s", e)
                return []

        loop = asyncio.get_running_loop()
        rows = await asyncio.wait_for(
            loop.run_in_executor(None, _query), timeout=30.0,
        )

        results = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                name = str(r.get("frb_name") or r.get("name") or "").strip()
                if not name:
                    continue
                # Filter by name if specified
                if query and query.lower() not in name.lower():
                    continue
                ra_val = float(r.get("ra_j2000", r.get("ra", 0.0)) or 0.0)
                dec_val = float(r.get("dec_j2000", r.get("dec", 0.0)) or 0.0)
                # Filter by position cone if specified
                if ra is not None and dec is not None:
                    # Simple box filter (not great-circle)
                    if abs(ra_val - ra) > radius or abs(dec_val - dec) > radius:
                        continue
                results.append(AstroObject(
                    source=self.source_name,
                    object_id=name,
                    name=name,
                    ra=ra_val,
                    dec=dec_val,
                    object_type="fast_radio_burst",
                    extra={
                        "dm_pc_cm3": float(r["dm"]) if r.get("dm") is not None else None,
                        "dm_excess": float(r["dm_exc_ne2001"]) if r.get("dm_exc_ne2001") is not None else None,
                        "redshift": float(r["redshift"]) if r.get("redshift") is not None else None,
                        "repeater": bool(r.get("repeater", False)),
                        "telescope": str(r.get("telescope", "")),
                        "mjd": float(r["mjd"]) if r.get("mjd") is not None else None,
                        "source_reference": "FRBSTATS / CHIME/FRB Catalog 1 (Collab 2021 ApJS 257, 59)",
                    },
                ))
                if len(results) >= 500:
                    break
            except (ValueError, TypeError, KeyError):
                continue
        return results

    async def fetch(self, object_id: str) -> FITSFile:
        raise NotImplementedError("FRBSTATS does not serve FITS files")

    def normalize(self, raw_data):
        from astropy.table import Table
        return Table()
