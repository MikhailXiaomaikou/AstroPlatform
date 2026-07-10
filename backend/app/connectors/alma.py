"""ALMA (Atacama Large Millimeter/submillimeter Array) archive connector."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry
from app.services.provenance_v2.registry_loader import dataset_from_registry

if TYPE_CHECKING:
    from astropy.table import Table

logger = logging.getLogger(__name__)


def _alma_provenance_dataset() -> dict:
    dataset = dataset_from_registry(
        "alma",
        source_authority="datacenter_ivoa_compliant",
        archive_version="ALMA Science Archive current",
        supplements={"standard": "ObsCore"},
    )
    return dataset or {
        "service_key": "alma",
        "service_name": "ALMA Science Archive",
        "archive_version": "ALMA Science Archive current",
        "source_authority": "datacenter_ivoa_compliant",
        "standard": "ObsCore",
        "reference_url": "https://almascience.eso.org/alma-data/archive",
        "credits_page_url": "https://almascience.nrao.edu/alma-data/publication-acknowledgement",
    }


class ALMAConnector(BaseConnector):
    source_name = "alma"

    @with_retry(max_retries=2, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        import asyncio
        from functools import partial

        from astropy.coordinates import SkyCoord
        import astropy.units as u
        from astroquery.alma import Alma

        # Resolve coordinates
        if ra is None or dec is None:
            try:
                coord = SkyCoord.from_name(query)
            except (ValueError, Exception) as e:
                logger.debug("SkyCoord.from_name failed for %r: %s", query, e)
                parts = query.replace(",", " ").split()
                if len(parts) >= 2:
                    try:
                        coord = SkyCoord(float(parts[0]), float(parts[1]), unit="deg")
                    except ValueError:
                        return []
                else:
                    return []
        else:
            coord = SkyCoord(ra, dec, unit="deg")

        # astroquery is synchronous — run in thread pool
        loop = asyncio.get_event_loop()
        table = await loop.run_in_executor(
            None,
            partial(Alma.query_region, coord, radius=radius * u.deg),
        )

        if table is None or len(table) == 0:
            return []

        return self._table_to_objects(table)

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        """Convert an ALMA ObsCore result table to normalized objects."""
        provenance_dataset = _alma_provenance_dataset()
        results = []
        for row in table[:50]:
            obs_id = str(row["obs_id"]) if "obs_id" in table.colnames else ""
            target = str(row["target_name"]) if "target_name" in table.colnames else obs_id
            obj_ra = float(row["s_ra"]) if "s_ra" in table.colnames else 0.0
            obj_dec = float(row["s_dec"]) if "s_dec" in table.colnames else 0.0

            source_urls = [
                str(value)
                for value in (
                    *(provenance_dataset.get("source_urls") or []),
                    provenance_dataset.get("reference_url"),
                    provenance_dataset.get("credits_page_url"),
                )
                if value
            ]
            archive_ids = [
                str(value)
                for value in (
                    *(provenance_dataset.get("archive_ids") or []),
                    provenance_dataset.get("ivoid"),
                    provenance_dataset.get("service_key"),
                )
                if value
            ]
            extra: dict = {
                "_provenance_dataset": provenance_dataset,
                "archive_version": provenance_dataset.get("archive_version"),
                "source_urls": source_urls,
                "archive_ids": archive_ids,
                "standard": "ObsCore",
                "measurement_scope": "observation_metadata_only",
                "line_measurements_available": False,
                "line_measurement_note": (
                    "ALMA archive rows describe observations. Derived line luminosity "
                    "or FWHM values require a cited line-measurement table."
                ),
            }
            if "proposal_id" in table.colnames:
                extra["proposal_id"] = str(row["proposal_id"])
            if "frequency" in table.colnames:
                try:
                    extra["frequency_ghz"] = float(row["frequency"])
                except (ValueError, TypeError):
                    pass
            if "frequency_support" in table.colnames:
                extra["frequency_support"] = str(row["frequency_support"])
            if "bandwidth" in table.colnames:
                try:
                    extra["bandwidth_ghz"] = float(row["bandwidth"])
                except (ValueError, TypeError):
                    pass
            if "t_exptime" in table.colnames:
                try:
                    extra["exposure_s"] = float(row["t_exptime"])
                except (ValueError, TypeError):
                    pass
            if "dataproduct_type" in table.colnames:
                extra["product_type"] = str(row["dataproduct_type"])
            if "band_list" in table.colnames:
                extra["band"] = str(row["band_list"])

            results.append(AstroObject(
                source="alma",
                object_id=obs_id,
                name=target,
                ra=obj_ra,
                dec=obj_dec,
                object_type="radio_observation",
                extra=extra,
            ))

        return results

    @with_retry(max_retries=2, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Reject metadata-only stand-ins for staged ALMA products."""
        raise NotImplementedError(
            "ALMA science products require archive staging; this connector only "
            "returns searchable observation metadata and will not fabricate an "
            "empty FITS data product. Download the staged product from the ALMA "
            "Science Archive and upload the resulting FITS file."
        )

    def normalize(self, raw_data) -> Table:
        from astropy.table import Table as AstropyTable
        if isinstance(raw_data, list):
            return AstropyTable(rows=raw_data)
        return AstropyTable(raw_data)
