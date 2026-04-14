"""Radio survey connectors for NVSS (1.4 GHz) and FIRST (1.4 GHz) via VizieR."""

from __future__ import annotations

import asyncio
import io
from functools import partial

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table
import astropy.units as u

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry

NVSS_CATALOG = "VIII/65"   # NVSS 1.4 GHz source catalog
FIRST_CATALOG = "VIII/92"  # FIRST 1.4 GHz source catalog


class NVSSConnector(BaseConnector):
    """Connector for NRAO VLA Sky Survey (NVSS) via VizieR catalog VIII/65."""

    source_name = "nvss"

    def _make_vizier(self, row_limit: int = 50):
        from astroquery.vizier import Vizier
        viz = Vizier(
            catalog=NVSS_CATALOG,
            row_limit=row_limit,
            columns=[
                "NVSS", "RAJ2000", "DEJ2000",
                "S1.4", "MajAxis", "MinAxis",
            ],
        )
        return viz

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        loop = asyncio.get_event_loop()

        if ra is None or dec is None:
            coord = await loop.run_in_executor(
                None, partial(SkyCoord.from_name, query)
            )
            ra, dec = coord.ra.deg, coord.dec.deg

        coord = SkyCoord(ra=ra, dec=dec, unit=(u.degree, u.degree), frame="icrs")
        radius_qty = radius * u.degree
        vizier = self._make_vizier()

        table_list = await loop.run_in_executor(
            None,
            partial(vizier.query_region, coord, radius=radius_qty),
        )

        if table_list is None or len(table_list) == 0:
            return []

        return self._table_to_objects(table_list[0])

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch NVSS source data and return as FITS table.

        object_id should be an NVSS source name (e.g. 'NVSS J132527+472731').
        """
        from astroquery.vizier import Vizier

        loop = asyncio.get_event_loop()

        vizier = Vizier(
            catalog=NVSS_CATALOG,
            row_limit=10,
            columns=["**"],
        )

        table_list = await loop.run_in_executor(
            None,
            partial(
                vizier.query_constraints,
                catalog=NVSS_CATALOG,
                NVSS=object_id,
            ),
        )

        if table_list is None or len(table_list) == 0:
            try:
                coord = await loop.run_in_executor(
                    None, partial(SkyCoord.from_name, object_id)
                )
                small_radius = 0.001 * u.degree
                vizier2 = Vizier(catalog=NVSS_CATALOG, row_limit=1, columns=["**"])
                table_list = await loop.run_in_executor(
                    None,
                    partial(vizier2.query_region, coord, radius=small_radius),
                )
            except Exception:
                pass

        if table_list is None or len(table_list) == 0:
            raise ValueError(f"No NVSS data found for '{object_id}'")

        table = table_list[0]
        table = self._fill_masked(table)
        table = self._sanitize_columns(table)

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        safe_id = object_id.replace(" ", "_").replace("+", "p").replace("-", "m")
        return FITSFile(
            object_id=object_id,
            source="nvss",
            data=buf.read(),
            filename=f"nvss-{safe_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

    def _safe_float(self, row, col: str) -> float | None:
        """Extract a float from a row column, returning None on failure."""
        if col not in row.colnames:
            return None
        try:
            val = float(row[col])
            if val != val or val == float("inf") or val == float("-inf"):
                return None
            return val
        except (ValueError, TypeError):
            return None

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects = []
        for row in table:
            ra = 0.0
            dec = 0.0
            for ra_col in ("RAJ2000", "_RAJ2000", "RA", "ra"):
                if ra_col in row.colnames:
                    try:
                        ra = float(row[ra_col])
                        break
                    except (ValueError, TypeError):
                        pass
            for dec_col in ("DEJ2000", "_DEJ2000", "DEC", "dec"):
                if dec_col in row.colnames:
                    try:
                        dec = float(row[dec_col])
                        break
                    except (ValueError, TypeError):
                        pass

            # NVSS source name as identifier
            name = ""
            if "NVSS" in row.colnames:
                try:
                    name = str(row["NVSS"]).strip()
                except Exception:
                    pass
            if not name:
                name = f"NVSS J{ra:.4f}{dec:+.4f}"

            # Collect radio properties in extra
            extra: dict = {}

            flux = self._safe_float(row, "S1.4")
            if flux is not None:
                extra["flux_1.4ghz"] = flux  # mJy

            maj = self._safe_float(row, "MajAxis")
            if maj is not None:
                extra["morphology_major_axis"] = maj

            minor = self._safe_float(row, "MinAxis")
            if minor is not None:
                extra["morphology_minor_axis"] = minor

            objects.append(
                AstroObject(
                    source="nvss",
                    object_id=name,
                    name=name,
                    ra=ra,
                    dec=dec,
                    object_type="radio source",
                    extra=extra,
                )
            )
        return objects


class FIRSTConnector(BaseConnector):
    """Connector for Faint Images of the Radio Sky at Twenty-cm (FIRST) via VizieR catalog VIII/92."""

    source_name = "first"

    def _make_vizier(self, row_limit: int = 50):
        from astroquery.vizier import Vizier
        viz = Vizier(
            catalog=FIRST_CATALOG,
            row_limit=row_limit,
            columns=[
                "FIRST", "RAJ2000", "DEJ2000",
                "Fint", "Maj", "Min",
            ],
        )
        return viz

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        loop = asyncio.get_event_loop()

        if ra is None or dec is None:
            coord = await loop.run_in_executor(
                None, partial(SkyCoord.from_name, query)
            )
            ra, dec = coord.ra.deg, coord.dec.deg

        coord = SkyCoord(ra=ra, dec=dec, unit=(u.degree, u.degree), frame="icrs")
        radius_qty = radius * u.degree
        vizier = self._make_vizier()

        table_list = await loop.run_in_executor(
            None,
            partial(vizier.query_region, coord, radius=radius_qty),
        )

        if table_list is None or len(table_list) == 0:
            return []

        return self._table_to_objects(table_list[0])

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError, Exception))
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch FIRST source data and return as FITS table.

        object_id should be a FIRST source name (e.g. 'FIRST J132527.5+472731').
        """
        from astroquery.vizier import Vizier

        loop = asyncio.get_event_loop()

        vizier = Vizier(
            catalog=FIRST_CATALOG,
            row_limit=10,
            columns=["**"],
        )

        table_list = await loop.run_in_executor(
            None,
            partial(
                vizier.query_constraints,
                catalog=FIRST_CATALOG,
                FIRST=object_id,
            ),
        )

        if table_list is None or len(table_list) == 0:
            try:
                coord = await loop.run_in_executor(
                    None, partial(SkyCoord.from_name, object_id)
                )
                small_radius = 0.001 * u.degree
                vizier2 = Vizier(catalog=FIRST_CATALOG, row_limit=1, columns=["**"])
                table_list = await loop.run_in_executor(
                    None,
                    partial(vizier2.query_region, coord, radius=small_radius),
                )
            except Exception:
                pass

        if table_list is None or len(table_list) == 0:
            raise ValueError(f"No FIRST data found for '{object_id}'")

        table = table_list[0]
        table = self._fill_masked(table)
        table = self._sanitize_columns(table)

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        safe_id = object_id.replace(" ", "_").replace("+", "p").replace("-", "m")
        return FITSFile(
            object_id=object_id,
            source="first",
            data=buf.read(),
            filename=f"first-{safe_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

    def _safe_float(self, row, col: str) -> float | None:
        """Extract a float from a row column, returning None on failure."""
        if col not in row.colnames:
            return None
        try:
            val = float(row[col])
            if val != val or val == float("inf") or val == float("-inf"):
                return None
            return val
        except (ValueError, TypeError):
            return None

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects = []
        for row in table:
            ra = 0.0
            dec = 0.0
            for ra_col in ("RAJ2000", "_RAJ2000", "RA", "ra"):
                if ra_col in row.colnames:
                    try:
                        ra = float(row[ra_col])
                        break
                    except (ValueError, TypeError):
                        pass
            for dec_col in ("DEJ2000", "_DEJ2000", "DEC", "dec"):
                if dec_col in row.colnames:
                    try:
                        dec = float(row[dec_col])
                        break
                    except (ValueError, TypeError):
                        pass

            # FIRST source name as identifier
            name = ""
            if "FIRST" in row.colnames:
                try:
                    name = str(row["FIRST"]).strip()
                except Exception:
                    pass
            if not name:
                name = f"FIRST J{ra:.4f}{dec:+.4f}"

            # Collect radio properties in extra
            extra: dict = {}

            flux = self._safe_float(row, "Fint")
            if flux is not None:
                extra["flux_1.4ghz"] = flux  # mJy

            maj = self._safe_float(row, "Maj")
            if maj is not None:
                extra["morphology_major_axis"] = maj

            minor = self._safe_float(row, "Min")
            if minor is not None:
                extra["morphology_minor_axis"] = minor

            objects.append(
                AstroObject(
                    source="first",
                    object_id=name,
                    name=name,
                    ra=ra,
                    dec=dec,
                    object_type="radio source",
                    extra=extra,
                )
            )
        return objects


class RadioAnalysis:
    """Radio astronomy analysis utilities."""

    @staticmethod
    def spectral_index(flux_1: float, freq_1: float, flux_2: float, freq_2: float) -> dict:
        """Compute radio spectral index alpha where S ~ nu^alpha.

        Args:
            flux_1, flux_2: Flux densities in mJy
            freq_1, freq_2: Frequencies in MHz
        """
        import math

        if flux_1 <= 0 or flux_2 <= 0 or freq_1 <= 0 or freq_2 <= 0:
            return {"error": "All values must be positive"}
        if freq_1 == freq_2:
            return {"error": "Frequencies must be different"}

        alpha = math.log(flux_1 / flux_2) / math.log(freq_1 / freq_2)

        # Classification
        if alpha > -0.1:
            classification = "flat-spectrum (compact/AGN core)"
        elif alpha > -0.5:
            classification = "moderately steep (young source)"
        elif alpha > -0.8:
            classification = "steep-spectrum (extended lobes)"
        else:
            classification = "ultra-steep (aged plasma/high-z)"

        return {
            "spectral_index": round(alpha, 3),
            "classification": classification,
            "flux_1_mJy": flux_1,
            "freq_1_MHz": freq_1,
            "flux_2_mJy": flux_2,
            "freq_2_MHz": freq_2,
        }

    @staticmethod
    def radio_luminosity(flux_mJy: float, redshift: float, freq_MHz: float = 1400,
                         spectral_index: float = -0.7) -> dict:
        """Compute radio luminosity from flux density and redshift.

        Uses standard cosmology (H0=70, Om=0.3, Ol=0.7).
        """
        from astropy.cosmology import FlatLambdaCDM
        import astropy.units as u

        cosmo = FlatLambdaCDM(H0=70, Om0=0.3)
        dl = cosmo.luminosity_distance(redshift).to(u.cm).value

        # Convert mJy to erg/s/cm^2/Hz
        flux_cgs = flux_mJy * 1e-26  # 1 mJy = 1e-26 erg/s/cm^2/Hz

        # K-correction
        k_corr = (1 + redshift) ** (1 + spectral_index)

        # Luminosity in W/Hz
        luminosity = 4 * 3.14159 * dl**2 * flux_cgs * k_corr / 1e7  # erg/s/Hz to W/Hz

        import math
        log_l = math.log10(luminosity) if luminosity > 0 else 0

        # Classification
        if log_l > 25:
            agn_class = "FR II / Radio-loud AGN"
        elif log_l > 23:
            agn_class = "FR I / Radio-intermediate AGN"
        elif log_l > 21:
            agn_class = "Star-forming galaxy"
        else:
            agn_class = "Radio-quiet"

        return {
            "luminosity_W_Hz": float(luminosity),
            "log_luminosity": round(log_l, 2),
            "classification": agn_class,
            "redshift": redshift,
            "freq_MHz": freq_MHz,
            "spectral_index_assumed": spectral_index,
        }

    @staticmethod
    def multi_frequency_crossmatch(ra: float, dec: float, radius: float = 0.01) -> dict:
        """Cross-match a source across radio surveys at different frequencies."""

        results = {}
        surveys = {
            "NVSS_1400MHz": ("nvss", 1400),
            "FIRST_1400MHz": ("first", 1400),
        }

        for survey_name, (connector_name, freq) in surveys.items():
            try:
                if connector_name == "nvss":
                    conn = NVSSConnector()
                elif connector_name == "first":
                    conn = FIRSTConnector()
                else:
                    continue

                loop = asyncio.new_event_loop()
                objs = loop.run_until_complete(conn.search("", ra=ra, dec=dec, radius=radius))
                loop.close()

                if objs:
                    best = objs[0]
                    flux = best.extra.get("flux_1.4ghz") or best.extra.get("Fint")
                    results[survey_name] = {
                        "detected": True,
                        "flux_mJy": float(flux) if flux else None,
                        "freq_MHz": freq,
                        "name": best.name,
                        "separation_arcsec": round(((ra - best.ra)**2 + (dec - best.dec)**2)**0.5 * 3600, 2),
                    }
                else:
                    results[survey_name] = {"detected": False, "freq_MHz": freq}
            except Exception as e:
                results[survey_name] = {"detected": False, "error": str(e)}

        # Compute spectral index if multiple detections
        detected = {k: v for k, v in results.items() if v.get("detected") and v.get("flux_mJy")}
        if len(detected) >= 2:
            freqs = list(detected.values())
            if freqs[0]["freq_MHz"] != freqs[1]["freq_MHz"]:
                si = RadioAnalysis.spectral_index(
                    freqs[0]["flux_mJy"], freqs[0]["freq_MHz"],
                    freqs[1]["flux_mJy"], freqs[1]["freq_MHz"],
                )
                results["spectral_index"] = si

        return {
            "ra": ra, "dec": dec,
            "surveys": results,
            "n_detections": sum(1 for v in results.values() if isinstance(v, dict) and v.get("detected")),
        }
