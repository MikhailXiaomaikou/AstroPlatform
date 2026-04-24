"""SIMBAD connector via astroquery."""

import asyncio
import io
import logging
import re
from functools import partial

from astropy.coordinates import SkyCoord
from astropy.table import Table
import astropy.units as u

from app.connectors.base import AstroObject, BaseConnector, FITSFile
from app.connectors.retry import with_retry
from app.services.provenance_v2.registry_loader import dataset_from_registry

logger = logging.getLogger(__name__)


def _is_galactic_stellar_type(
    object_type: object | None,
    object_type_long: object | None = None,
    spectral_type: object | None = None,
) -> bool:
    """Return True for Milky Way stellar objects where z is not cosmological.

    Legacy substring-matching helper (kept for backwards compatibility).
    New code paths should use :func:`_otype_is_extragalactic` instead —
    the allow-list is more robust against otype_txt variants like
    ``Open (galactic) Cluster`` that sneak past substring matches.
    """
    text = " ".join(
        str(v or "").lower()
        for v in (object_type, object_type_long, spectral_type)
    )
    if any(token in text for token in ("galaxy", "qso", "quasar", "agn", "seyfert")):
        return False
    stellar_markers = (
        "*",
        "star",
        "cepheid",
        "variable",
        "open cluster",
        "cluster of stars",
        "pulsating variable",
        "binary",
        "supergiant",
    )
    spectral_markers = ("o", "b", "a", "f", "g", "k", "m", "wr", "wd")
    if any(token in text for token in stellar_markers):
        return True
    spectral = str(spectral_type or "").strip().lower()
    return bool(spectral) and spectral[:2] in spectral_markers or spectral[:1] in spectral_markers


# W2 (PART W): SIMBAD otype 中允许 rvz_redshift 按 cosmological z 解释
# 的 allow-list. 其它 otype (恒星/星团/脉冲星/星云/MW SNR/行星/...) 的
# rvz_redshift 实际是 radial_velocity/c 的内部编码, 不是宇宙学红移,
# 展示会误导用户. 反向设计 (allow-list 河外) 比 legacy 的 deny-list
# substring match 更可靠 — 后者对 otype_txt="Open (galactic) Cluster"
# 因为子串匹配 "open cluster" ≠ "open (galactic) cluster" 失败, 导致
# Pleiades 泄漏 z=2.01e-05.
# 参考 http://simbad.u-strasbg.fr/simbad/sim-display?data=otypes 的
# "Galaxy" / "AGN" / "Lensed" 三大类.
_EXTRAGALACTIC_OTYPES: frozenset[str] = frozenset({
    # 星系一般类型
    "G", "GiC", "GiG", "GiP",
    "GrG", "CGG", "ClG", "PaG", "IG",
    # 特殊星系
    "SBG", "H2G", "EmG", "BCG", "LSB", "LINER",
    # 历史遗留大写变体
    "GinCl", "GinGroup", "GinPair",
    # AGN
    "AGN", "AG?", "SyG", "Sy1", "Sy2", "rG",
    "LIN", "Bla", "BLL", "BLLac", "BlLac",
    "Q?", "QSO", "Q1", "Q2", "QuOQ",
    # 宇宙学距离尺度事件
    "grv", "Lev", "LeG", "LeQ", "GWE",
})


def _otype_is_extragalactic(otype: str | None) -> bool:
    """W2: Return True only for object types where rvz_redshift can be
    displayed as a cosmological redshift. All other otypes (stars, clusters,
    MW SNR, nebulae, ...) get z stripped so the UI shows radial_velocity
    instead.

    Allow-list approach (rather than deny-list) is chosen because SIMBAD
    has ~200+ galactic otype codes and any future addition defaults to
    safe (strip z) behaviour."""
    if not otype:
        return False
    return str(otype).strip() in _EXTRAGALACTIC_OTYPES


class SIMBADConnector(BaseConnector):
    """Connector for SIMBAD astronomical database via astroquery."""

    source_name = "simbad"
    COMMON_ALIASES = {
        "pleiades": "M45",
        "hyades": "Melotte 25",
        "orion nebula": "M42",
        "andromeda galaxy": "M31",
        "whirlpool galaxy": "M51",
        "eagle nebula": "M16",
        "crab nebula": "M1",
    }

    # Known coordinates + search radii (degrees) for extended objects whose
    # catalogue entry may not be returned by query_object.
    KNOWN_COORDS: dict[str, tuple[float, float, float]] = {
        "pleiades": (56.75, 24.12, 2.0),
        "crab nebula": (83.63, 22.01, 0.2),
        "orion nebula": (83.82, -5.39, 0.5),
        "hyades": (66.75, 15.87, 2.0),
    }

    def _make_simbad(self):
        from astroquery.simbad import Simbad
        simbad = Simbad()
        simbad.add_votable_fields("otype", "V", "rvz_redshift")
        return simbad

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
    async def search(
        self, query: str, ra: float | None = None, dec: float | None = None, radius: float = 0.1
    ) -> list[AstroObject]:
        simbad = self._make_simbad()
        loop = asyncio.get_running_loop()
        canonical_query = self.COMMON_ALIASES.get(query.strip().lower(), query)

        query_lower = query.strip().lower()

        # Always try name-based query first when a meaningful query string is
        # provided.  This handles clusters, nebulae, and other extended objects.
        if canonical_query and canonical_query.strip() and canonical_query not in ("survey", "sky"):
            # Step 1: try query_object with the canonical alias
            table = await loop.run_in_executor(
                None,
                partial(simbad.query_object, canonical_query),
            )
            if table is not None and len(table) > 0:
                return self._table_to_objects(table)

            # Step 2: try SkyCoord.from_name() (uses CDS Sesame name resolver)
            # which handles common English names like "Pleiades", "Crab Nebula"
            resolved_coord = None
            try:
                resolved_coord = await loop.run_in_executor(
                    None,
                    partial(SkyCoord.from_name, canonical_query),
                )
            except (ValueError, Exception) as e:
                # Sesame failed for canonical — try the original query too
                logger.debug("SkyCoord.from_name failed for %r: %s", canonical_query, e)
                if canonical_query != query:
                    try:
                        resolved_coord = await loop.run_in_executor(
                            None,
                            partial(SkyCoord.from_name, query),
                        )
                    except (ValueError, Exception) as e2:
                        logger.debug("SkyCoord.from_name failed for original query %r: %s", query, e2)

            # Step 3: check hardcoded coords for well-known extended objects
            if resolved_coord is None and query_lower in self.KNOWN_COORDS:
                kra, kdec, _kr = self.KNOWN_COORDS[query_lower]
                resolved_coord = SkyCoord(ra=kra, dec=kdec, unit=(u.degree, u.degree), frame="icrs")

            if resolved_coord is not None:
                # Use a wider radius for known extended objects, else a default
                cone_radius = 0.2
                if query_lower in self.KNOWN_COORDS:
                    cone_radius = self.KNOWN_COORDS[query_lower][2]
                table = await loop.run_in_executor(
                    None,
                    partial(simbad.query_region, resolved_coord, radius=cone_radius * u.degree),
                )
                if table is not None and len(table) > 0:
                    return self._table_to_objects(table)

            # Step 4: try query_objectids as a last resort
            try:
                from astroquery.simbad import Simbad as _Simbad
                ids_table = await loop.run_in_executor(
                    None,
                    partial(_Simbad.query_objectids, canonical_query),
                )
                if ids_table is not None and len(ids_table) > 0:
                    main_id = str(ids_table[0][0]).strip()
                    table = await loop.run_in_executor(
                        None,
                        partial(simbad.query_object, main_id),
                    )
                    if table is not None and len(table) > 0:
                        return self._table_to_objects(table)
            except (ValueError, TimeoutError, ConnectionError, OSError) as e:
                logger.debug("SIMBAD query_objectids failed for %r: %s", canonical_query, e)

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
            return []

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
        required_fields: list[str] | None = None,
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
            # Map user-friendly names to SIMBAD otype codes
            # Types with multiple codes use a tuple for IN clause
            otype_map: dict[str, str | tuple[str, ...]] = {
                "AGN": "AGN", "quasar": "QSO", "galaxy": "G",
                "star": "*", "nebula": "Neb", "pulsar": "Psr",
                "supernova": "SN*", "Lyman-alpha emitter": "EmG",
                "submillimeter galaxy": "G", "Lyman-break galaxy": "G",
                "Seyfert galaxy": ("Sy1", "Sy2"),
                "blazar": "Bla", "starburst galaxy": "SBG",
                "white dwarf": "WD*", "brown dwarf": "BD*",
                "cepheid": "Ce*", "RR Lyrae": "RR*",
                "galaxy cluster": "ClG", "globular cluster": "GlC",
                "gamma-ray burst": "grb", "planetary nebula": "PN",
            }
            mapped = otype_map.get(object_type, object_type)
            if isinstance(mapped, tuple):
                safe = [re.sub(r"[^a-zA-Z0-9*]", "", t) for t in mapped]
                in_list = ", ".join(f"'{t}'" for t in safe)
                conditions.append(f"otype IN ({in_list})")
            else:
                simbad_type = re.sub(r"[^a-zA-Z0-9*]", "", mapped)
                conditions.append(f"otype = '{simbad_type}'")

        if not conditions:
            return []

        # If user is searching by redshift, ensure we only return objects WITH redshift data
        has_redshift_filter = redshift_min is not None or redshift_max is not None
        if has_redshift_filter:
            conditions.append("rvz_redshift IS NOT NULL")

        # Add NOT NULL filters for any explicitly requested data fields
        valid_basic_cols = {"rvz_redshift", "plx_value", "pmra", "pmdec", "rvz_radvel", "sp_type", "morph_type"}
        for fld in (required_fields or []):
            if fld in valid_basic_cols:
                conditions.append(f"{fld} IS NOT NULL")

        where = " AND ".join(conditions)
        order_by = "rvz_redshift ASC" if has_redshift_filter else "nbref DESC"
        adql = (
            f"SELECT TOP {limit} main_id, ra, dec, otype, otype_txt, rvz_redshift, "
            f"rvz_radvel, rvz_type, sp_type, galdim_majaxis, galdim_minaxis, "
            f"galdim_angle, morph_type, plx_value, pmra, pmdec, "
            f"rvz_qual, plx_qual, sp_qual, coo_qual "
            f"FROM basic "
            f"WHERE {where} "
            f"ORDER BY {order_by}"
        )

        loop = asyncio.get_running_loop()
        try:
            table = await loop.run_in_executor(
                None,
                partial(Simbad.query_tap, adql),
            )
        except (ValueError, TimeoutError, ConnectionError, OSError) as e:
            logger.warning("SIMBAD TAP search_by_criteria failed: %s", e)
            return []

        if table is None or len(table) == 0:
            return []

        return self._table_to_objects(table)

    async def get_identifiers(self, object_name: str) -> list[dict]:
        """Get all known identifiers for an object via SIMBAD TAP (2-step: resolve OID then query ident)."""
        from astroquery.simbad import Simbad

        safe_name = object_name.replace("'", "''")
        loop = asyncio.get_running_loop()

        # Step 1: get the internal OID
        try:
            oid_table = await loop.run_in_executor(
                None, partial(Simbad.query_tap, f"SELECT oidref FROM ident WHERE id = '{safe_name}'")
            )
        except (ValueError, TimeoutError, ConnectionError, OSError) as e:
            logger.warning("SIMBAD TAP ident lookup failed for %r: %s", safe_name, e)
            return []
        if oid_table is None or len(oid_table) == 0:
            return []

        oid = int(oid_table["oidref"][0])

        # Step 2: get all identifiers for this OID
        try:
            id_table = await loop.run_in_executor(
                None, partial(Simbad.query_tap, f"SELECT id FROM ident WHERE oidref = {oid}")
            )
        except (ValueError, TimeoutError, ConnectionError, OSError) as e:
            logger.warning("SIMBAD TAP ident query failed for oid=%d: %s", oid, e)
            return []
        if id_table is None or len(id_table) == 0:
            return []

        return [{"name": str(row["id"]).strip()} for row in id_table]

    async def get_object_detail(self, object_name: str) -> dict | None:
        """Get detailed info for a single object via TAP (richer than basic search)."""
        from astroquery.simbad import Simbad

        safe_name = object_name.replace("'", "''")
        adql = (
            f"SELECT main_id, ra, dec, otype, otype_txt, "
            f"rvz_redshift, rvz_radvel, sp_type, morph_type, "
            f"plx_value, pmra, pmdec, nbref "
            f"FROM basic JOIN ident ON basic.oid = ident.oidref "
            f"WHERE ident.id = '{safe_name}'"
        )
        loop = asyncio.get_running_loop()
        try:
            table = await loop.run_in_executor(None, partial(Simbad.query_tap, adql))
        except (ValueError, TimeoutError, ConnectionError, OSError) as e:
            logger.warning("SIMBAD TAP object detail query failed for %r: %s", safe_name, e)
            return None

        if table is None or len(table) == 0:
            return None

        row = table[0]

        def safe(col: str):
            if col not in table.colnames:
                return None
            v = row[col]
            try:
                import numpy as _np
                if _np.ma.is_masked(v):
                    return None
            except (ImportError, TypeError):
                pass
            try:
                f = float(v)
                return f if f == f else None  # NaN check
            except (ValueError, TypeError):
                s = str(v).strip()
                if not s or s.lower() in {"undefined", "null", "none", "--"}:
                    return None
                return s

        object_type = safe("otype") or ""
        object_type_long = safe("otype_txt") or ""
        if not object_type_long and object_type == "OpC":
            object_type_long = "Open cluster"
        if not object_type and object_type_long:
            object_type = object_type_long
        if not object_type and not object_type_long and object_name.lower().strip() in {
            "pleiades",
            "m45",
            "melotte 22",
        }:
            object_type = "Open cluster"
            object_type_long = "Open cluster"
        spectral_type = safe("sp_type")
        redshift = safe("rvz_redshift")
        radial_velocity = safe("rvz_radvel")
        redshift_note = None
        # W2 (PART W): 用 otype allow-list 判是否保留 z. 非河外 (恒星/
        # 星团/脉冲星/星云/...) 一律剥离, 改展示 radial_velocity. 原
        # _is_galactic_stellar_type substring 匹配对 Pleiades otype_txt
        # "Open (galactic) Cluster" 漏判 → z=2.01e-05 泄漏给用户.
        if redshift is not None and not _otype_is_extragalactic(object_type):
            redshift_note = (
                f"SIMBAD rvz_redshift for otype='{object_type}' is "
                f"velocity-derived, not cosmological. Use radial_velocity "
                f"km/s instead."
            )
            redshift = None

        detail = {
            "name": str(row["main_id"]).strip() if "main_id" in table.colnames else object_name,
            "ra": safe("ra") or 0.0,
            "dec": safe("dec") or 0.0,
            "object_type": object_type,
            "object_type_long": object_type_long,
            "redshift": redshift,
            "radial_velocity": radial_velocity,
            "spectral_type": spectral_type,
            "morphology": safe("morph_type"),
            "parallax": safe("plx_value"),
            "proper_motion_ra": safe("pmra"),
            "proper_motion_dec": safe("pmdec"),
            "n_references": safe("nbref"),
        }
        if redshift_note:
            detail["redshift_note"] = redshift_note
            detail["redshift_source"] = "not_applicable_non_extragalactic"
        return detail

    @with_retry(max_retries=3, retryable_exceptions=(ConnectionError, TimeoutError, IOError))
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

        loop = asyncio.get_running_loop()
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
                except (ValueError, TypeError) as e:
                    logger.debug("Dropping column %r due to conversion error: %s", col, e)
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
        provenance_dataset = dataset_from_registry("simbad", source_authority="project_registry")
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

            radial_velocity = None
            for rv_col in ("rvz_radvel", "RVZ_RADVEL"):
                if rv_col in row.colnames:
                    try:
                        val = float(row[rv_col])
                        if not (val != val):
                            radial_velocity = val
                        break
                    except (ValueError, TypeError):
                        pass

            # Collect all extra columns into extra dict
            extra: dict = {"_provenance_dataset": provenance_dataset} if provenance_dataset else {}
            skip = {"main_id", "MAIN_ID", "ra", "RA", "dec", "DEC", "otype", "OTYPE",
                    "V", "FLUX_V", "flux_V", "rvz_redshift", "Z_VALUE",
                    "oid", "hpx", "nbref", "update_date",
                    "coo_err_maj", "coo_err_min", "coo_err_angle", "coo_wavelength",
                    "coo_bibcode", "coo_err_maj_prec", "coo_err_min_prec",
                    "ra_prec", "dec_prec", "rvz_redshift_prec", "rvz_radvel_prec",
                    "rvz_err_prec", "plx_prec", "plx_err_prec", "pmra_prec", "pmdec_prec",
                    "galdim_majaxis_prec", "galdim_minaxis_prec",
                    "pm_err_maj", "pm_err_min", "pm_err_angle", "pm_err_maj_prec", "pm_err_min_prec",
                    "plx_bibcode", "pm_bibcode", "rvz_bibcode", "sp_bibcode",
                    "morph_bibcode", "galdim_bibcode", "vlsr_bibcode",
                    "pm_qual", "morph_qual", "galdim_qual"}
            for col in row.colnames:
                if col in skip:
                    continue
                try:
                    val = row[col]
                    if hasattr(val, "item"):
                        val = val.item()  # numpy scalar to python
                    if val is None or (isinstance(val, float) and val != val):
                        continue
                    # Convert masked to None
                    if hasattr(val, "mask") or str(val) == "--":
                        continue
                    extra[col] = val
                except (ValueError, TypeError, KeyError) as e:
                    logger.debug("Skipping extra column %r: %s", col, e)

            spectral_type = extra.get("sp_type") or extra.get("SP_TYPE")
            object_type_long = extra.get("otype_txt") or extra.get("OTYPE_TXT")
            # W2 (PART W): 非河外 otype 剥离 z — 替代 legacy _is_galactic_stellar_type
            # substring 匹配 (后者对 "Open (galactic) Cluster" 失败, Pleiades 泄漏).
            # allow-list 更严格: 只明确的河外 otype 保留 z, 其它默认剥离.
            if not _otype_is_extragalactic(obj_type):
                if redshift is not None:
                    extra["redshift_note"] = (
                        f"SIMBAD rvz_redshift for otype={obj_type!r} is "
                        f"velocity-derived, not cosmological. Use "
                        f"radial_velocity km/s instead."
                    )
                    extra["redshift_source"] = "not_applicable_non_extragalactic"
                redshift = None
            if radial_velocity is not None:
                extra["radial_velocity_km_s"] = radial_velocity
            _ = spectral_type, object_type_long  # retained for future use

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
                    extra=extra,
                )
            )
        return objects
