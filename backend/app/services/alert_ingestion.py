"""ZTF transient alert ingestion via the Lasair broker API."""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import TransientAlert

logger = logging.getLogger(__name__)


class AlertIngestionService:
    """Ingest alerts from Lasair (ZTF) and cache in local database."""

    LASAIR_API = "https://lasair-ztf.lsst.ac.uk/api"

    # Rate-limit: max 10 requests per minute
    _MAX_REQUESTS_PER_MIN = 10
    _request_times: list[float] = []

    async def _rate_limit(self) -> None:
        """Block until a request slot is available (max 10 req/min)."""
        now = time.monotonic()
        # Purge timestamps older than 60 seconds
        self._request_times = [t for t in self._request_times if now - t < 60]
        if len(self._request_times) >= self._MAX_REQUESTS_PER_MIN:
            wait = 60 - (now - self._request_times[0])
            if wait > 0:
                logger.debug("Rate-limiting Lasair requests — waiting %.1fs", wait)
                await asyncio.sleep(wait)
        self._request_times.append(time.monotonic())

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict | None = None,
        retries: int = 3,
    ) -> dict | list | None:
        """HTTP GET with exponential backoff."""
        for attempt in range(retries):
            await self._rate_limit()
            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 429:
                    delay = 2 ** attempt * 5
                    logger.warning("Lasair 429 — backing off %ds", delay)
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if attempt < retries - 1:
                    delay = 2 ** attempt * 2
                    logger.warning(
                        "Lasair HTTP %s on attempt %d — retrying in %ds",
                        exc.response.status_code, attempt + 1, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning("Lasair request failed after %d attempts: %s", retries, exc)
            except Exception as exc:
                if attempt < retries - 1:
                    delay = 2 ** attempt * 2
                    logger.warning("Lasair error on attempt %d — retrying in %ds: %s", attempt + 1, delay, exc)
                    await asyncio.sleep(delay)
                else:
                    logger.warning("Lasair unreachable after %d attempts: %s", retries, exc)
        return None

    def _parse_alert(self, obj: dict) -> dict:
        """Normalise a Lasair object dict into a flat alert record."""
        return {
            "source": "ztf",
            "source_id": obj.get("objectId", obj.get("object_id", "")),
            "ra": _safe_float(obj.get("ramean", obj.get("ra"))) or 0.0,
            "dec": _safe_float(obj.get("decmean", obj.get("dec"))) or 0.0,
            "discovery_date": _parse_mjd(obj.get("mjdmin", obj.get("mjdfirst"))),
            "magnitude": _safe_float(obj.get("maglast", obj.get("mag"))),
            "mag_band": obj.get("filtlast", obj.get("filt", None)),
            "classification": obj.get("classification", None),
            "classification_confidence": _safe_float(obj.get("classificationConfidence")),
            "redshift": _safe_float(obj.get("redshift")),
            "host_galaxy": obj.get("hostname", obj.get("host_name", None)),
            "raw_data": obj,
        }

    # ── Public fetch methods ──

    async def fetch_recent(self, hours: int = 24, limit: int = 100) -> list[dict]:
        """Fetch recent alerts from Lasair."""
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                # Use the Lasair query endpoint to get recent objects
                # The /query/ endpoint allows SQL-like queries over the ZTF objects table
                mjd_cutoff = _datetime_to_mjd(
                    datetime.now(timezone.utc) - timedelta(hours=hours)
                )
                params = {
                    "selected": "objectId,ramean,decmean,maglast,mjdlast,mjdmin,classification,filtlast",
                    "tables": "objects",
                    "conditions": f"mjdlast>{mjd_cutoff:.4f}",
                    "limit": str(min(limit, 1000)),
                }
                data = await self._get(client, f"{self.LASAIR_API}/query/", params=params)
                if data is None:
                    return []
                objects = data if isinstance(data, list) else data.get("results", data.get("objects", []))
                return [self._parse_alert(obj) for obj in objects[:limit] if obj.get("objectId") or obj.get("object_id")]
        except Exception as exc:
            logger.warning("fetch_recent failed: %s", exc)
            return []

    async def fetch_by_region(self, ra: float, dec: float, radius_arcsec: float = 60) -> list[dict]:
        """Cone search around coordinates."""
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                params = {
                    "ra": ra,
                    "dec": dec,
                    "radius": radius_arcsec,
                    "pagesize": 100,
                }
                data = await self._get(client, f"{self.LASAIR_API}/cone/", params=params)
                if data is None:
                    return []
                objects = data if isinstance(data, list) else data.get("results", data.get("objects", []))
                return [self._parse_alert(obj) for obj in objects if obj.get("objectId") or obj.get("object_id")]
        except Exception as exc:
            logger.warning("fetch_by_region failed: %s", exc)
            return []

    async def fetch_by_type(self, object_type: str, days: int = 7) -> list[dict]:
        """Query by classification type."""
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                mjd_cutoff = _datetime_to_mjd(
                    datetime.now(timezone.utc) - timedelta(days=days)
                )
                params = {
                    "selected": "objectId,ramean,decmean,maglast,mjdlast,mjdmin,classification,filtlast",
                    "tables": "objects",
                    "conditions": f"classification='{object_type}' AND mjdlast>{mjd_cutoff:.4f}",
                    "limit": "200",
                }
                data = await self._get(client, f"{self.LASAIR_API}/query/", params=params)
                if data is None:
                    return []
                objects = data if isinstance(data, list) else data.get("results", data.get("objects", []))
                return [self._parse_alert(obj) for obj in objects if obj.get("objectId") or obj.get("object_id")]
        except Exception as exc:
            logger.warning("fetch_by_type failed: %s", exc)
            return []

    async def ingest(self, alerts: list[dict], db: AsyncSession) -> int:
        """Store alerts in database, deduplicating by source_id. Returns count of new alerts."""
        if not alerts:
            return 0

        source_ids = [a["source_id"] for a in alerts if a.get("source_id")]
        if not source_ids:
            return 0

        # Find already-stored source_ids
        stmt = select(TransientAlert.source_id).where(
            TransientAlert.source_id.in_(source_ids)
        )
        result = await db.execute(stmt)
        existing = {row[0] for row in result.all()}

        new_count = 0
        for alert in alerts:
            sid = alert.get("source_id")
            if not sid or sid in existing:
                continue
            row = TransientAlert(
                source=alert.get("source", "ztf"),
                source_id=sid,
                ra=alert["ra"],
                dec=alert["dec"],
                discovery_date=alert.get("discovery_date"),
                magnitude=alert.get("magnitude"),
                mag_band=alert.get("mag_band"),
                classification=alert.get("classification"),
                classification_confidence=alert.get("classification_confidence"),
                redshift=alert.get("redshift"),
                host_galaxy=alert.get("host_galaxy"),
                raw_data=alert.get("raw_data"),
            )
            db.add(row)
            existing.add(sid)
            new_count += 1

        if new_count:
            await db.commit()
            logger.info("Ingested %d new transient alerts", new_count)

        return new_count


# ── Helpers ──

def _safe_float(val: object) -> float | None:
    """Convert a value to float, returning None on failure."""
    if val is None or val == "" or val == "None":
        return None
    try:
        f = float(val)  # type: ignore[arg-type]
        if f != f:  # NaN check
            return None
        return f
    except (ValueError, TypeError):
        return None


def _parse_mjd(mjd: object) -> datetime | None:
    """Convert Modified Julian Date to datetime."""
    f = _safe_float(mjd)
    if f is None:
        return None
    try:
        # MJD epoch is 1858-11-17 00:00:00 UTC
        return datetime(1858, 11, 17, tzinfo=timezone.utc) + timedelta(days=f)
    except (OverflowError, ValueError):
        return None


def _datetime_to_mjd(dt: datetime) -> float:
    """Convert datetime to Modified Julian Date."""
    epoch = datetime(1858, 11, 17, tzinfo=timezone.utc)
    return (dt - epoch).total_seconds() / 86400.0
