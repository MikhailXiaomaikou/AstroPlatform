"""ATNF Pulsar Catalogue connector.

Reference: Manchester, Hobbs, Teoh & Hobbs 2005 AJ 129, 1993
(ATNF Pulsar Catalogue, current version v1.70+).

Uses `psrqpy` (Pitkin 2018 JOSS 3, 538) as the Python interface —
queries the live ATNF catalogue and returns standard pulsar parameters
(period, period-derivative, dispersion measure, YMW16 distance, etc.).
"""

from __future__ import annotations

import asyncio
import logging

from app.connectors.base import AstroObject, BaseConnector, FITSFile

logger = logging.getLogger(__name__)


class ATNFPulsarConnector(BaseConnector):
    """Query the ATNF Pulsar Catalogue for known pulsars."""

    source_name = "atnf_pulsar"

    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None,
        radius: float = 1.0,
    ) -> list[AstroObject]:
        """Search for pulsars by name (e.g. "J0534+2200" / "B0531+21" / "Crab")
        or by sky position.
        """
        def _query():
            try:
                from psrqpy import QueryATNF
            except ImportError:
                logger.warning("psrqpy not installed")
                return []

            params = [
                "JNAME", "RAJD", "DECJD",
                "P0", "P1", "DM", "DIST", "DIST_DM", "DIST_DM1",
                "TYPE", "ASSOC", "SURVEY",
                "S400", "S1400",
            ]

            try:
                if query:
                    q = QueryATNF(params=params, psrs=[query])
                elif ra is not None and dec is not None:
                    # Use psrqpy's native cone search (coord1/coord2/radius),
                    # which filters via astropy SkyCoord.separation() — a true
                    # great-circle angular distance with correct cos(dec)
                    # weighting and RA wraparound at 0/360 built in. The old
                    # flat (RAJD-ra)^2+(DECJD-dec)^2 disc condition under-returned
                    # at high |dec| (the RA term was not scaled by cos(dec)) and
                    # missed targets straddling the RA=0/360 seam.
                    from astropy.coordinates import SkyCoord

                    center = SkyCoord(ra, dec, unit="deg")
                    coord1 = str(
                        center.ra.to_string(unit="hourangle", sep=":", pad=True),
                    )
                    coord2 = str(
                        center.dec.to_string(sep=":", pad=True, alwayssign=True),
                    )
                    q = QueryATNF(
                        params=params,
                        coord1=coord1, coord2=coord2, radius=float(radius),
                    )
                else:
                    return []
                table = q.table
                if table is None or len(table) == 0:
                    return []
                return [dict(row) for row in table]
            except Exception as e:
                logger.warning("ATNF query failed: %s", e)
                return []

        loop = asyncio.get_running_loop()
        rows = await asyncio.wait_for(
            loop.run_in_executor(None, _query), timeout=30.0,
        )

        results = []
        for r in rows:
            try:
                name = str(r.get("JNAME", "")).strip()
                if not name:
                    continue
                ra_val = float(r.get("RAJD", 0.0)) if r.get("RAJD") is not None else 0.0
                dec_val = float(r.get("DECJD", 0.0)) if r.get("DECJD") is not None else 0.0
                results.append(AstroObject(
                    source=self.source_name,
                    object_id=name,
                    name=name,
                    ra=ra_val,
                    dec=dec_val,
                    object_type="pulsar",
                    extra={
                        "period_s": float(r["P0"]) if r.get("P0") is not None else None,
                        "period_dot": float(r["P1"]) if r.get("P1") is not None else None,
                        "dm": float(r["DM"]) if r.get("DM") is not None else None,
                        "distance_kpc": float(r["DIST"]) if r.get("DIST") is not None else None,
                        "distance_dm_ymw16_kpc": float(r["DIST_DM"]) if r.get("DIST_DM") is not None else None,
                        "distance_dm_ne2001_kpc": float(r["DIST_DM1"]) if r.get("DIST_DM1") is not None else None,
                        "pulsar_type": str(r.get("TYPE", "")),
                        "assoc": str(r.get("ASSOC", "")),
                        "flux_400_mJy": float(r["S400"]) if r.get("S400") is not None else None,
                        "flux_1400_mJy": float(r["S1400"]) if r.get("S1400") is not None else None,
                        "source_reference": "ATNF Pulsar Catalogue (Manchester+ 2005 AJ 129, 1993)",
                    },
                ))
            except (ValueError, TypeError, KeyError):
                continue
        return results

    async def fetch(self, object_id: str) -> FITSFile:
        raise NotImplementedError("ATNF does not serve FITS files via this connector")

    def normalize(self, raw_data):
        from astropy.table import Table
        return Table()
