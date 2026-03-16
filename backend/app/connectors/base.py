from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astropy.table import Table


@dataclass
class AstroObject:
    """Standardized astronomical object returned by search."""
    source: str
    object_id: str
    name: str
    ra: float
    dec: float
    object_type: str = ""
    magnitude: float | None = None
    redshift: float | None = None
    extra: dict = field(default_factory=dict)


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
