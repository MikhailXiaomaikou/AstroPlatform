"""MAST (Mikulski Archive for Space Telescopes) connector via astroquery."""

import asyncio
import io
import tempfile
from functools import partial
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table
import astropy.units as u

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry


class MASTConnector(BaseConnector):
    """Connector for MAST archive (HST, JWST, Kepler, TESS, etc.)."""

    source_name = "mast"

    def _resolve_coordinates(self, query: str, ra: float | None, dec: float | None):
        if ra is not None and dec is not None:
            return SkyCoord(ra=ra, dec=dec, unit=(u.degree, u.degree), frame="icrs")

        try:
            return SkyCoord.from_name(query)
        except Exception:
            parts = query.replace(",", " ").split()
            if len(parts) >= 2:
                try:
                    return SkyCoord(
                        ra=float(parts[0]),
                        dec=float(parts[1]),
                        unit=(u.degree, u.degree),
                        frame="icrs",
                    )
                except ValueError:
                    return None
        return None

    @with_retry(max_retries=1, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        from astroquery.mast import Observations

        loop = asyncio.get_event_loop()
        if ra is not None and dec is not None:
            coord = self._resolve_coordinates(query, ra, dec)
        else:
            coord = await loop.run_in_executor(
                None, partial(self._resolve_coordinates, query, ra, dec)
            )

        if coord is not None:
            table = await loop.run_in_executor(
                None,
                partial(
                    Observations.query_region,
                    coord,
                    radius=radius * u.degree,
                ),
            )
        else:
            table = await loop.run_in_executor(
                None,
                partial(Observations.query_object, query, radius=radius * u.degree),
            )

        if table is None or len(table) == 0:
            return []

        # Query region returns a broader set of collections. Filter locally because
        # the remote name-resolution path in query_criteria/query_object has proven
        # much less reliable for common targets like M31.
        allowed_collections = {"HST", "JWST"}
        if "obs_collection" in table.colnames:
            table = table[[str(row["obs_collection"]).strip() in allowed_collections for row in table]]
        if "dataRights" in table.colnames:
            table = table[[str(row["dataRights"]).strip().upper() == "PUBLIC" for row in table]]

        if table is None or len(table) == 0:
            return []

        # Limit results to avoid overwhelming response
        if len(table) > 50:
            table = table[:50]

        return self._table_to_objects(table)

    @with_retry(max_retries=1, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch FITS product for a MAST observation.

        object_id should be a MAST obsid (observation ID).
        Downloads the minimum recommended products and returns the first
        FITS file found.
        """
        from astroquery.mast import Observations

        loop = asyncio.get_event_loop()

        # Get data products for this observation
        products = await loop.run_in_executor(
            None,
            partial(Observations.get_product_list, object_id),
        )

        if products is None or len(products) == 0:
            raise ValueError(f"No MAST products found for obsid '{object_id}'")

        # Filter to minimum recommended products (science data)
        filtered = await loop.run_in_executor(
            None,
            partial(
                Observations.filter_products,
                products,
                mrp_only=True,
                extension="fits",
            ),
        )

        if filtered is None or len(filtered) == 0:
            # Fall back to all FITS products
            filtered = await loop.run_in_executor(
                None,
                partial(
                    Observations.filter_products,
                    products,
                    extension="fits",
                ),
            )

        if filtered is None or len(filtered) == 0:
            # No downloadable FITS — return observation metadata as FITS table
            return await self._metadata_as_fits(object_id)

        # Download the first product to a temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = await loop.run_in_executor(
                None,
                partial(
                    Observations.download_products,
                    filtered[:1],
                    download_dir=tmpdir,
                ),
            )

            if manifest is None or len(manifest) == 0:
                raise ValueError(f"Failed to download MAST products for '{object_id}'")

            # Read the downloaded file
            local_path = str(manifest["Local Path"][0])
            fits_path = Path(local_path)
            if not fits_path.exists():
                raise ValueError(f"Downloaded file not found at '{local_path}'")

            data = fits_path.read_bytes()
            filename = fits_path.name

        return FITSFile(
            object_id=object_id,
            source="mast",
            data=data,
            filename=f"mast-{filename}",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    async def _metadata_as_fits(self, object_id: str) -> FITSFile:
        """Return observation metadata as a FITS table when no products are available."""
        from astroquery.mast import Observations

        loop = asyncio.get_event_loop()
        table = await loop.run_in_executor(
            None,
            partial(
                Observations.query_criteria,
                obs_id=object_id,
            ),
        )

        if table is None or len(table) == 0:
            raise ValueError(f"No MAST data found for '{object_id}'")

        table = self._fill_masked(table)
        table = self._sanitize_columns(table)

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        return FITSFile(
            object_id=object_id,
            source="mast",
            data=buf.read(),
            filename=f"mast-meta-{object_id}.fits",
        )

    def _fill_masked(self, table: Table) -> Table:
        """Fill masked values so the table can be written to FITS."""
        for col in table.colnames:
            if hasattr(table[col], "mask") and np.any(table[col].mask):
                if table[col].dtype.kind in ("i", "u"):
                    table[col] = table[col].filled(-1)
                elif table[col].dtype.kind == "f":
                    table[col] = table[col].filled(np.nan)
                else:
                    table[col] = table[col].filled("")
        return table

    def _sanitize_columns(self, table: Table) -> Table:
        """Drop or convert object-dtype columns for FITS compatibility."""
        for c in list(table.colnames):
            if table[c].dtype == object:
                try:
                    table[c] = [str(v) for v in table[c]]
                except Exception:
                    table.remove_column(c)
        return table

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects = []
        for row in table:
            ra = 0.0
            dec = 0.0
            for ra_col in ("s_ra", "ra"):
                if ra_col in row.colnames:
                    try:
                        ra = float(row[ra_col])
                        break
                    except (ValueError, TypeError):
                        pass
            for dec_col in ("s_dec", "dec"):
                if dec_col in row.colnames:
                    try:
                        dec = float(row[dec_col])
                        break
                    except (ValueError, TypeError):
                        pass

            # Observation ID
            obs_id = ""
            if "obsid" in row.colnames:
                obs_id = str(row["obsid"]).strip()

            # Target name
            name = ""
            if "target_name" in row.colnames:
                name = str(row["target_name"]).strip()
            if not name:
                name = obs_id

            # Observation metadata for extra dict
            extra: dict = {}
            for key in ("instrument_name", "filters", "t_exptime", "obs_collection",
                        "dataproduct_type", "calib_level"):
                if key in row.colnames:
                    try:
                        val = row[key]
                        # Convert masked values to None
                        if hasattr(val, "mask"):
                            continue
                        extra[key] = float(val) if isinstance(val, (int, float, np.floating, np.integer)) else str(val)
                    except (ValueError, TypeError):
                        pass

            objects.append(
                AstroObject(
                    source="mast",
                    object_id=obs_id,
                    name=name,
                    ra=ra,
                    dec=dec,
                    object_type=extra.get("dataproduct_type", ""),
                    extra=extra,
                )
            )
        return objects
