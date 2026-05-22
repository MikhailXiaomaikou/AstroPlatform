from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astropy.table import Table

logger = logging.getLogger(__name__)


@dataclass
class AstroObject:
    """Standardized astronomical object returned by search.

    Phase 4 / R15: ``__post_init__`` validates physical bounds and
    flags suspect values via ``extra['_validation_warnings']``.  We
    deliberately do NOT raise — a single malformed row should not kill
    an entire search batch — but the warnings propagate up to the UI
    via the sanity-warning pipeline.
    """
    source: str
    object_id: str
    name: str
    ra: float
    dec: float
    object_type: str = ""
    magnitude: float | None = None
    redshift: float | None = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        import math
        warnings: list[str] = []
        try:
            if not math.isfinite(self.ra) or not (0.0 <= self.ra < 360.0):
                warnings.append(f"ra={self.ra} outside [0, 360)")
            if not math.isfinite(self.dec) or not (-90.0 <= self.dec <= 90.0):
                warnings.append(f"dec={self.dec} outside [-90, 90]")
            if self.magnitude is not None:
                m = float(self.magnitude)
                if not math.isfinite(m) or m < -30 or m > 40:
                    warnings.append(f"magnitude={m} outside [-30, 40]")
            if self.redshift is not None:
                z = float(self.redshift)
                if not math.isfinite(z) or z < -0.01 or z > 20:
                    warnings.append(f"redshift={z} outside [-0.01, 20]")
        except (TypeError, ValueError) as exc:
            warnings.append(f"validation error: {exc}")
        if warnings:
            if not isinstance(self.extra, dict):
                self.extra = {}
            existing = self.extra.get("_validation_warnings") or []
            if isinstance(existing, list):
                self.extra["_validation_warnings"] = existing + warnings
            else:
                self.extra["_validation_warnings"] = warnings
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "astro_object_invalid_total", float(len(warnings)),
                    source=str(self.source)[:32],
                )
            except Exception as e:
                logger.debug("metric record_counter(astro_object_invalid_total) failed: %s", e)


@dataclass
class FITSFile:
    """Wrapper for fetched FITS data."""
    object_id: str
    source: str
    data: bytes
    filename: str


class BaseConnector(ABC):
    """Abstract base for all astronomical data source connectors."""

    source_name: str = ""

    @abstractmethod
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        """Search for astronomical objects by name or coordinates.

        Args:
            query: Object name or free-text query.
            ra: Right ascension in degrees.
            dec: Declination in degrees.
            radius: Search radius in degrees.

        Returns:
            List of matching AstroObject instances.
        """

    @abstractmethod
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch FITS data for a specific object.

        Args:
            object_id: Source-specific identifier.

        Returns:
            FITSFile with the binary data.
        """

    @abstractmethod
    def normalize(self, raw_data) -> Table:
        """Convert raw query results to an astropy Table."""
