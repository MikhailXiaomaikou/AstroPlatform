import asyncio
import io
import logging
from functools import partial

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table
import astropy.units as u

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry
from app.services.provenance_v2.param_scanner_resolver import resolve_param_provenance

logger = logging.getLogger(__name__)


class GaiaConnector(BaseConnector):
    """Connector for Gaia DR3 via astroquery."""

    source_name = "gaia"

    # Bug 11-adjacent: Gaia TAP is the most unstable of our 24 connectors.
    # Original outer retry was max_retries=3 with base_delay=0.5 — combined
    # with _cone_search's internal sync→async fallback (45s + 180s = 225s
    # per attempt), worst-case wait was 15 min before surfacing the error.
    # Reviewer hit "second query timed out after 3 retries" exactly because
    # of this.  New posture: outer retry = 1 (the inner sync→async already
    # IS the first fallback), base_delay bumped to 5s so we don't hammer
    # an already-overloaded endpoint.  Worst-case wait is now ~7.5 min
    # instead of ~15.
    @with_retry(
        max_retries=1, base_delay=5.0, max_delay=30.0,
        retryable_exceptions=(ConnectionError, TimeoutError, IOError),
    )
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        if ra is None or dec is None:
            loop = asyncio.get_event_loop()
            coord = await loop.run_in_executor(
                None, partial(SkyCoord.from_name, query)
            )
            ra, dec = coord.ra.deg, coord.dec.deg

        table = await self._cone_search(ra, dec, radius)
        return self._table_to_objects(table)

    @with_retry(
        max_retries=1, base_delay=5.0, max_delay=30.0,
        retryable_exceptions=(ConnectionError, TimeoutError, IOError),
    )
    async def fetch(self, object_id: str) -> FITSFile:
        """Fetch Gaia source data as a FITS-format table."""
        import numpy as np

        table = await self._query_by_source_id(object_id)

        # Drop object/variable-length columns that can't serialize to FITS
        drop_cols = [c for c in table.colnames if table[c].dtype == object]
        for c in drop_cols:
            table.remove_column(c)

        # Fill masked values to avoid FITS write issues
        for col in table.colnames:
            if hasattr(table[col], 'mask') and np.any(table[col].mask):
                if table[col].dtype.kind in ('i', 'u'):
                    table[col] = table[col].filled(-1)
                else:
                    table[col] = table[col].filled(np.nan)

        buf = io.BytesIO()
        table.write(buf, format="fits", overwrite=True)
        buf.seek(0)

        return FITSFile(
            object_id=object_id,
            source="gaia",
            data=buf.read(),
            filename=f"gaia-{object_id}.fits",
        )

    def normalize(self, raw_data) -> Table:
        if isinstance(raw_data, Table):
            return raw_data
        return Table(raw_data)

    async def _cone_search(self, ra: float, dec: float, radius: float) -> Table:
        from astroquery.gaia import Gaia

        coord = SkyCoord(ra=ra, dec=dec, unit=(u.degree, u.degree), frame="icrs")
        radius_qty = radius * u.degree

        # E1.2 / Bug 11-adjacent: the sync cone search times out often on
        # well-known targets (NGC / Messier / Gaia-rich clusters) or when
        # Gaia TAP is under load.  Cut the sync window from 45 s to 25 s
        # so we fall to async TAP faster when Gaia is slow — typical
        # healthy sync response is <5 s, so 25 s is generous.  The async
        # path has a 300 s budget (bumped from 180 s below).
        loop = asyncio.get_event_loop()
        try:
            job = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    partial(
                        Gaia.cone_search_async,
                        coordinate=coord,
                        radius=radius_qty,
                    ),
                ),
                timeout=25.0,
            )
            return job.get_results()
        except (asyncio.TimeoutError, Exception) as exc:
            logger.info(
                "Gaia cone_search sync path failed/timed out (%s); falling back to async TAP job",
                exc,
            )

        # Async fallback via an explicit ADQL job — tolerates 5-min
        # run times because the client keeps polling.
        adql = (
            f"SELECT TOP 500 source_id, ra, dec, "
            f"       phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag, bp_rp, "
            f"       parallax, parallax_error, pmra, pmdec, radial_velocity, ruwe "
            f"FROM gaiadr3.gaia_source "
            f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), "
            f"                 CIRCLE('ICRS', {ra}, {dec}, {radius}))"
        )
        try:
            job = await asyncio.wait_for(
                loop.run_in_executor(None, partial(Gaia.launch_job_async, adql)),
                timeout=300.0,  # Bug 11-adjacent: 180s → 300s for overloaded Gaia
            )
            return job.get_results()
        except Exception as exc2:
            logger.warning("Gaia async TAP fallback also failed: %s", exc2)
            raise

    async def _query_by_source_id(self, source_id: str) -> Table:
        from astroquery.gaia import Gaia
        import re

        if not re.match(r'^\d+$', str(source_id).strip()):
            raise ValueError(f"Invalid Gaia source_id: must be numeric, got {source_id!r}")
        adql = f"SELECT * FROM gaiadr3.gaia_source WHERE source_id = {int(source_id)}"
        loop = asyncio.get_event_loop()
        job = await loop.run_in_executor(
            None,
            partial(Gaia.launch_job, adql),
        )
        return job.get_results()

    def _table_to_objects(self, table: Table) -> list[AstroObject]:
        objects = []
        provenance_dataset = resolve_param_provenance(getattr(table, "meta", {}), service_hint="gaia")
        for row in table:
            ra = float(row["ra"]) if "ra" in row.colnames else 0.0
            dec = float(row["dec"]) if "dec" in row.colnames else 0.0
            source_id = str(row.get("source_id", row.get("SOURCE_ID", "")))

            # L2-d (audit 2026-04-20): 用 np.isfinite(v) 取代 `v == v`.
            # `v == v` 对真 float NaN 确实返回 False (IEEE754), 但对 astropy
            # masked scalar 会触发 DeprecationWarning (astropy >= 4.1) 且
            # 未来版本可能改语义.  np.isfinite 对 NaN / +/-Inf 都返回
            # False, 语义明确.
            mag = None
            for col in ("phot_g_mean_mag", "PHOT_G_MEAN_MAG"):
                if col in row.colnames:
                    try:
                        v = float(row[col])
                        if np.isfinite(v):
                            mag = v
                    except (ValueError, TypeError):
                        pass
                    break

            parallax = None
            for col in ("parallax", "PARALLAX"):
                if col in row.colnames:
                    try:
                        v = float(row[col])
                        if np.isfinite(v):
                            parallax = v
                    except (ValueError, TypeError):
                        pass

            # D2.2 — Gaia RV is STELLAR KINEMATIC radial velocity (km/s),
            # NOT cosmological redshift.  Previous code wrote z = v/c into
            # AstroObject.redshift which mislabelled kinematic drift as a
            # cosmological quantity.  We now expose it only as
            # radial_velocity_km_s and never as ``redshift``.
            rv = None
            for col in ("radial_velocity", "RADIAL_VELOCITY"):
                if col in row.colnames:
                    try:
                        v = float(row[col])
                        if np.isfinite(v):
                            rv = v
                    except (ValueError, TypeError):
                        pass
                    break
            # redshift remains None so AstroObject.redshift stays empty
            # rather than being filled with a stellar-kinematic misnomer.
            redshift = None

            extra: dict = {"_provenance_dataset": provenance_dataset} if provenance_dataset else {}
            if parallax is not None:
                extra["parallax"] = parallax
                # D7.14 — auto-compute distance when parallax is positive.
                # Users often miss that parallax (mas) needs 1000/plx to
                # become distance (pc); we do it for them and note the
                # Bailer-Jones caveat (negative or tiny plx → prior
                # needed, not simple inversion).
                #
                # L2-a (audit 2026-04-20): SNR 门控.  当 parallax/parallax_error
                # < 5 时, 1/plx 点估计高度偏斜 (mode ≠ 1/plx_mode), 不能当
                # 真距离.  标 partial + 引导用户用 Bayesian 后验.
                if parallax > 0:
                    # 尝试从 row 里读 parallax_error 算 SNR
                    plx_err = None
                    for err_col in ("parallax_error", "PARALLAX_ERROR"):
                        if err_col in row.colnames:
                            try:
                                plx_err = float(row[err_col])
                                if not (plx_err == plx_err and plx_err > 0):
                                    plx_err = None
                            except (ValueError, TypeError):
                                plx_err = None
                            break
                    extra["distance_pc"] = round(1000.0 / parallax, 2)
                    if plx_err is not None:
                        snr = parallax / plx_err
                        extra["parallax_snr"] = round(snr, 2)
                        if snr < 5:
                            extra["distance_requires_posterior"] = True
                            extra["distance_note"] = (
                                f"parallax_snr = {snr:.2f} < 5; 1/plx point "
                                f"estimate is highly biased (distance PDF is "
                                f"strongly skewed).  Use Bailer-Jones+ 2021 "
                                f"posterior instead of this value for any "
                                f"quantitative analysis."
                            )
                            extra["distance_analysis_status"] = "partial"
                else:
                    extra["distance_note"] = (
                        "Parallax ≤ 0; simple 1/plx inversion is meaningless. "
                        "Use a Bayesian distance prior (Bailer-Jones+ 2021)."
                    )
                    extra["distance_requires_posterior"] = True
            if rv is not None:
                extra["radial_velocity_km_s"] = round(rv, 2)
                extra["radial_velocity_note"] = (
                    "Stellar kinematic radial velocity (km/s); NOT cosmological redshift."
                )

            # Additional Gaia columns: uncertainties, proper motions, photometry, quality
            _extra_columns = [
                ("parallax_error", "parallax_error"),
                ("pmra", "pmra"),
                ("pmdec", "pmdec"),
                ("pmra_error", "pmra_error"),
                ("pmdec_error", "pmdec_error"),
                ("radial_velocity_error", "radial_velocity_error"),
                ("phot_bp_mean_mag", "phot_bp_mean_mag"),
                ("phot_rp_mean_mag", "phot_rp_mean_mag"),
                ("bp_rp", "bp_rp"),
                ("astrometric_excess_noise", "astrometric_excess_noise"),
                ("ruwe", "ruwe"),
                ("phot_g_mean_flux_over_error", "phot_g_mean_flux_over_error"),
            ]
            for gaia_col, extra_key in _extra_columns:
                for col in (gaia_col, gaia_col.upper()):
                    if col in row.colnames:
                        try:
                            v = float(row[col])
                            if np.isfinite(v):
                                extra[extra_key] = v
                        except (ValueError, TypeError):
                            pass
                        break

            objects.append(
                AstroObject(
                    source="gaia",
                    object_id=source_id,
                    name=f"Gaia DR3 {source_id}",
                    ra=ra,
                    dec=dec,
                    object_type="star",
                    magnitude=mag,
                    redshift=redshift,
                    extra=extra,
                )
            )
        return objects
