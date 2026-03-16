"""SIMBAD connector via astroquery."""

import asyncio
import io
from functools import partial

from astropy.coordinates import SkyCoord
from astropy.table import Table
import astropy.units as u

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry


class SIMBADConnector(BaseConnector):
    """Connector for SIMBAD astronomical database via astroquery."""

    source_name = "simbad"

    def _make_simbad(self):
        from astroquery.simbad import Simbad
        simbad = Simbad()
        simbad.add_votable_fields("otype", "V", "rvz_redshift")
        return simbad

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        simbad = self._make_simbad()
        loop = asyncio.get_event_loop()

        if ra is not None and dec is not None:
            coord = SkyCoord(ra=ra, dec=dec, unit=(u.degree, u.degree), frame="icrs")
            radius_qty = radius * u.degree
            table = await loop.run_in_executor(
                None,
                partial(simbad.query_region, coord, radius=radius_qty),
            )
        elif query in ("survey", "sky") or not query.strip():
            # No coordinates and no valid object name — skip
            return []
        else:
            table = await loop.run_in_executor(
                None,
                partial(simbad.query_object, query),
            )

        if table is None:
            return []

        return self._table_to_objects(table)

    async def search_by_criteria(
        self,
        object_type: str | None = None,
        redshift_min: float | None = None,
        redshift_max: float | None = None,
        ra: float | None = None,
        dec: float | None = None,
        radius: float = 1.0,
        limit: int = 100,
    ) -> list[AstroObject]:
        """Search SIMBAD using TAP/ADQL with science criteria (type, redshift)."""
        from astroquery.simbad import Simbad

        conditions = []

        if ra is not None and dec is not None:
            conditions.append(
                f"CONTAINS(POINT('ICRS', ra, dec), "
                f"CIRCLE('ICRS', {ra}, {dec}, {radius})) = 1"
            )
        if redshift_min is not None:
            conditions.append(f"rvz_redshift >= {redshift_min}")
        if redshift_max is not None:
            conditions.append(f"rvz_redshift <= {redshift_max}")
        if object_type:
            # Map common types to SIMBAD otype codes
            otype_map = {
                "AGN": "AGN", "quasar": "QSO", "galaxy": "G",
                "star": "*", "nebula": "Neb", "pulsar": "Psr",
                "supernova": "SN*", "Lyman-alpha emitter": "EmG",
                "submillimeter galaxy": "G", "Lyman-break galaxy": "G",
            }
            simbad_type = otype_map.get(object_type, object_type)
            conditions.append(f"otype = '{simbad_type}'")

        if not conditions:
            return []

        where = " AND ".join(conditions)
        adql = (
            f"SELECT TOP {limit} main_id, ra, dec, otype, rvz_redshift "
            f"FROM basic "
            f"WHERE {where} "
            f"ORDER BY rvz_redshift ASC"
        )

        loop = asyncio.get_event_loop()
        try:
            table = await loop.run_in_executor(
                None,
                partial(Simbad.query_tap, adql),
            )
        except Exception:
            return []

        if table is None or len(table) == 0:
            return []

        return self._table_to_objects(table)

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch SIMBAD data as a FITS table."""
        from astroquery.simbad import Simbad
        import numpy as np

        simbad = Simbad()
        simbad.add_votable_fields(
            "otype", "V", "B", "R",
            "rvz_redshift", "rvz_radvel",
            "sp",
        )

        loop = asyncio.get_event_loop()
        table = await loop.run_in_executor(
            None,
            partial(simbad.query_object, object_id),
        )

        if table is None or len(table) == 0:
            raise ValueError(f"No SIMBAD data found for '{object_id}'")

        # Convert object-type columns to strings for FITS compatibility
        for col in list(table.colnames):
            if table[col].dtype == object:
                try:
                    table[col] = [str(v) for v in table[col]]
                except Exception:
                    table.remove_column(col)

        # Fill masked values
        for col in table.colnames:
            if hasattr(table[col], "mask") and np.any(table[col].mask):
                if table[col].dtype.kind in ("i", "u"):
                    table[col] = table[col].filled(-1)
                elif table[col].dtype.kind == "f":
                    table[col] = table[col].filled(np.nan)
                else:
                    table[col] = table[col].filled("")

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        return FITSFile(
            object_id=object_id,
            source="simbad",
            data=buf.read(),
            filename=f"simbad-{object_id.replace(' ', '_')}.fits",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects = []
        for row in table:
            # RA/Dec — new astroquery returns decimal degrees directly
            ra = 0.0
            dec = 0.0
            for ra_col in ("ra", "RA"):
                if ra_col in row.colnames:
                    try:
                        val = float(row[ra_col])
                        if val == val:  # not NaN
                            ra = val
                        break
                    except (ValueError, TypeError):
                        pass
            for dec_col in ("dec", "DEC"):
                if dec_col in row.colnames:
                    try:
                        val = float(row[dec_col])
                        if val == val:  # not NaN
                            dec = val
                        break
                    except (ValueError, TypeError):
                        pass

            name = ""
            for name_col in ("main_id", "MAIN_ID"):
                if name_col in row.colnames:
                    name = str(row[name_col]).strip()
                    break

            obj_type = ""
            for otype_col in ("otype", "OTYPE"):
                if otype_col in row.colnames:
                    obj_type = str(row[otype_col]).strip()
                    break

            mag = None
            for mag_col in ("V", "FLUX_V", "flux_V"):
                if mag_col in row.colnames:
                    try:
                        val = float(row[mag_col])
                        if not (val != val):  # NaN check without import
                            mag = val
                        break
                    except (ValueError, TypeError):
                        pass

            redshift = None
            for z_col in ("rvz_redshift", "Z_VALUE"):
                if z_col in row.colnames:
                    try:
                        val = float(row[z_col])
                        if not (val != val):  # NaN check
                            redshift = val
                        break
                    except (ValueError, TypeError):
                        pass

            objects.append(
                AstroObject(
                    source="simbad",
                    object_id=name or f"simbad-{ra:.4f}-{dec:.4f}",
                    name=name,
                    ra=ra,
                    dec=dec,
                    object_type=obj_type,
                    magnitude=mag,
                    redshift=redshift,
                )
            )
        return objects
