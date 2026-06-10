"""Cross-match engine with astropy (primary) and optional STILTS backends.

Primary backend uses astropy SkyCoord for high-performance sky matching.
If STILTS_JAR_PATH is set and Java is available, the STILTS subprocess
backend can be used for tmatch2 / tmatchn operations.
"""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Valid join and find modes
_VALID_JOINS = {"1and2", "1or2", "all1", "all2", "1not2", "2not1"}
_VALID_FINDS = {"best", "all"}
_VALID_MATCH_TYPES = {"sky", "skyerr"}


class CrossMatchEngine:
    """Dual-backend cross-match engine for astronomical catalogs.

    Primary backend: astropy SkyCoord (search_around_sky / match_to_catalog_sky)
    Optional backend: STILTS tmatch2 subprocess (requires Java + stilts.jar)
    """

    def __init__(self) -> None:
        self._stilts_available = self._check_stilts()
        self._semaphore = asyncio.Semaphore(4)

    # ── Public API ──

    async def crossmatch(
        self,
        table1: pd.DataFrame,
        table2: pd.DataFrame,
        radius_arcsec: float = 3.0,
        match_type: str = "sky",
        join: str = "1and2",
        find: str = "best",
        use_stilts: bool = False,
    ) -> pd.DataFrame:
        """Cross-match two tables containing 'ra' and 'dec' columns (degrees).

        Parameters
        ----------
        table1, table2 : pd.DataFrame
            Must contain 'ra' and 'dec' columns in degrees.
        radius_arcsec : float
            Maximum match separation in arcseconds.
        match_type : str
            "sky" (positional) or "skyerr" (with errors, STILTS only).
        join : str
            Join type: "1and2", "1or2", "all1", "all2", "1not2", "2not1".
        find : str
            "best" (nearest match per source) or "all" (every pair within radius).
        use_stilts : bool
            Force STILTS backend if available.

        Returns
        -------
        pd.DataFrame
            Matched result with columns from both tables plus 'separation_arcsec'.
        """
        self._validate_inputs(table1, table2, match_type, join, find)

        if use_stilts and self._stilts_available:
            return await self._stilts_match(
                table1, table2, radius_arcsec, match_type, join, find
            )
        return self._astropy_match(table1, table2, radius_arcsec, join, find)

    async def multi_crossmatch(
        self,
        tables: list[pd.DataFrame],
        radius_arcsec: float = 3.0,
        use_stilts: bool = False,
    ) -> pd.DataFrame:
        """Match N tables sequentially by chaining pairwise matches.

        For astropy: chains pairwise "1and2" matches left-to-right.
        For STILTS:  uses tmatchn if available, else falls back to chaining.
        """
        if len(tables) < 2:
            raise ValueError("multi_crossmatch requires at least 2 tables")

        if use_stilts and self._stilts_available:
            return await self._stilts_multi_match(tables, radius_arcsec)

        # Chain pairwise matches through astropy
        result = tables[0].copy()
        for i, t in enumerate(tables[1:], start=2):
            # Rename columns to avoid collisions (except ra/dec which we need)
            result = self._astropy_match(
                result, t, radius_arcsec, join="1and2", find="best"
            )
            # After matching, the table2 columns have _2 suffix.
            # For the next iteration, we need ra/dec from table1 side.
            # Drop the _2 ra/dec so the next match uses the original coords.
            if "ra_2" in result.columns and "ra" in result.columns:
                result = result.rename(
                    columns={"ra_2": f"ra_{i}", "dec_2": f"dec_{i}"}
                )
            if "separation_arcsec" in result.columns:
                result = result.rename(
                    columns={"separation_arcsec": f"separation_arcsec_{i - 1}_{i}"}
                )

        return result

    # ── Astropy backend ──

    def _astropy_match(
        self,
        t1: pd.DataFrame,
        t2: pd.DataFrame,
        radius_arcsec: float,
        join: str,
        find: str,
    ) -> pd.DataFrame:
        """High-performance matching using astropy SkyCoord."""
        from astropy.coordinates import SkyCoord
        import astropy.units as u

        c1 = SkyCoord(ra=t1["ra"].values, dec=t1["dec"].values, unit="deg")
        c2 = SkyCoord(ra=t2["ra"].values, dec=t2["dec"].values, unit="deg")

        # Rename table2 columns to avoid collisions
        t2_renamed = t2.rename(columns={c: f"{c}_2" for c in t2.columns})

        if find == "best":
            return self._astropy_best_match(
                t1, t2_renamed, c1, c2, radius_arcsec, join
            )
        else:  # find == "all"
            return self._astropy_all_matches(
                t1, t2_renamed, c1, c2, radius_arcsec, join, u
            )

    def _astropy_best_match(
        self,
        t1: pd.DataFrame,
        t2_renamed: pd.DataFrame,
        c1,
        c2,
        radius_arcsec: float,
        join: str,
    ) -> pd.DataFrame:
        """For each source in t1, find the nearest match in t2."""
        idx, sep, _ = c1.match_to_catalog_sky(c2)
        mask = sep.arcsec <= radius_arcsec

        matched_t1_idx = np.where(mask)[0]
        matched_t2_idx = idx[mask]
        matched_sep = sep.arcsec[mask]

        unmatched_t1_idx = np.where(~mask)[0]

        # Build the set of t2 indices that got matched (for anti-joins)
        matched_t2_set = set(matched_t2_idx.tolist())
        all_t2_idx = set(range(len(t2_renamed)))
        unmatched_t2_idx = np.array(sorted(all_t2_idx - matched_t2_set))

        if join == "1and2":
            # Inner join: only matched rows
            result = t1.iloc[matched_t1_idx].reset_index(drop=True)
            result = pd.concat(
                [result, t2_renamed.iloc[matched_t2_idx].reset_index(drop=True)],
                axis=1,
            )
            result["separation_arcsec"] = matched_sep

        elif join == "all1":
            # Left join: all of t1, matched t2 or NaN
            # Build matched portion
            matched = t1.iloc[matched_t1_idx].reset_index(drop=True)
            matched = pd.concat(
                [matched, t2_renamed.iloc[matched_t2_idx].reset_index(drop=True)],
                axis=1,
            )
            matched["separation_arcsec"] = matched_sep

            # Unmatched t1 rows
            parts = [matched]
            if len(unmatched_t1_idx) > 0:
                um = t1.iloc[unmatched_t1_idx].reset_index(drop=True)
                for col in t2_renamed.columns:
                    um[col] = np.nan
                um["separation_arcsec"] = np.nan
                parts.append(um)

            result = pd.concat(parts, ignore_index=True)
            # Reorder to preserve original t1 order
            order = np.concatenate([matched_t1_idx, unmatched_t1_idx])
            sort_idx = np.argsort(order)
            result = result.iloc[sort_idx].reset_index(drop=True)

        elif join == "all2":
            # Right join: all of t2, matched t1 or NaN
            # Build matched portion
            matched = t1.iloc[matched_t1_idx].reset_index(drop=True)
            matched = pd.concat(
                [matched, t2_renamed.iloc[matched_t2_idx].reset_index(drop=True)],
                axis=1,
            )
            matched["separation_arcsec"] = matched_sep

            # Unmatched t2 rows
            parts = [matched]
            if len(unmatched_t2_idx) > 0:
                um = t2_renamed.iloc[unmatched_t2_idx].reset_index(drop=True)
                for col in t1.columns:
                    um[col] = np.nan
                um["separation_arcsec"] = np.nan
                parts.append(um)

            result = pd.concat(parts, ignore_index=True)
            # Reorder to preserve original t2 order
            order = np.concatenate([matched_t2_idx, unmatched_t2_idx])
            sort_idx = np.argsort(order)
            result = result.iloc[sort_idx].reset_index(drop=True)

        elif join == "1or2":
            # Full outer join: matched rows + unmatched from both
            # Matched rows
            matched_part = t1.iloc[matched_t1_idx].reset_index(drop=True)
            matched_part = pd.concat(
                [
                    matched_part,
                    t2_renamed.iloc[matched_t2_idx].reset_index(drop=True),
                ],
                axis=1,
            )
            matched_part["separation_arcsec"] = matched_sep

            parts = [matched_part]

            # Unmatched t1
            if len(unmatched_t1_idx) > 0:
                um_t1 = t1.iloc[unmatched_t1_idx].reset_index(drop=True)
                for col in t2_renamed.columns:
                    um_t1[col] = np.nan
                um_t1["separation_arcsec"] = np.nan
                parts.append(um_t1)

            # Unmatched t2
            if len(unmatched_t2_idx) > 0:
                um_t2 = t2_renamed.iloc[unmatched_t2_idx].reset_index(drop=True)
                for col in t1.columns:
                    um_t2[col] = np.nan
                um_t2["separation_arcsec"] = np.nan
                parts.append(um_t2)

            result = pd.concat(parts, ignore_index=True)

        elif join == "1not2":
            # Anti-join: rows in t1 with NO match in t2
            result = t1.iloc[unmatched_t1_idx].reset_index(drop=True)
            result["separation_arcsec"] = np.nan

        elif join == "2not1":
            # Anti-join: rows in t2 with NO match in t1
            result = t2_renamed.iloc[unmatched_t2_idx].reset_index(drop=True)
            result["separation_arcsec"] = np.nan

        else:
            raise ValueError(f"Unknown join type: {join}")

        return result

    def _astropy_all_matches(
        self,
        t1: pd.DataFrame,
        t2_renamed: pd.DataFrame,
        c1,
        c2,
        radius_arcsec: float,
        join: str,
        u_module,
    ) -> pd.DataFrame:
        """Find all pairs within radius using search_around_sky."""
        # c1.search_around_sky(c2) returns indices in argument-then-caller
        # order: (idx_into_c2, idx_into_c1, ...). Bind them so idx1 indexes
        # t1/c1 and idx2 indexes t2/c2, matching how they are used below.
        idx2, idx1, sep, _ = c1.search_around_sky(
            c2, radius_arcsec * u_module.arcsec
        )

        if join == "1and2":
            result = t1.iloc[idx1.tolist()].reset_index(drop=True)
            result = pd.concat(
                [result, t2_renamed.iloc[idx2.tolist()].reset_index(drop=True)],
                axis=1,
            )
            result["separation_arcsec"] = sep.arcsec

        elif join == "all1":
            # All t1 rows; for matched ones, include t2 data
            matched_t1_set = set(idx1.tolist())
            parts = []
            # Matched pairs
            matched = t1.iloc[idx1.tolist()].reset_index(drop=True)
            matched = pd.concat(
                [
                    matched,
                    t2_renamed.iloc[idx2.tolist()].reset_index(drop=True),
                ],
                axis=1,
            )
            matched["separation_arcsec"] = sep.arcsec
            parts.append(matched)
            # Unmatched t1 rows
            unmatched_idx = [
                i for i in range(len(t1)) if i not in matched_t1_set
            ]
            if unmatched_idx:
                um = t1.iloc[unmatched_idx].reset_index(drop=True)
                for col in t2_renamed.columns:
                    um[col] = np.nan
                um["separation_arcsec"] = np.nan
                parts.append(um)
            result = pd.concat(parts, ignore_index=True)

        elif join == "all2":
            matched_t2_set = set(idx2.tolist())
            parts = []
            matched = t1.iloc[idx1.tolist()].reset_index(drop=True)
            matched = pd.concat(
                [
                    matched,
                    t2_renamed.iloc[idx2.tolist()].reset_index(drop=True),
                ],
                axis=1,
            )
            matched["separation_arcsec"] = sep.arcsec
            parts.append(matched)
            unmatched_idx = [
                i for i in range(len(t2_renamed)) if i not in matched_t2_set
            ]
            if unmatched_idx:
                um = t2_renamed.iloc[unmatched_idx].reset_index(drop=True)
                for col in t1.columns:
                    um[col] = np.nan
                um["separation_arcsec"] = np.nan
                parts.append(um)
            result = pd.concat(parts, ignore_index=True)

        elif join == "1or2":
            matched_t1_set = set(idx1.tolist())
            matched_t2_set = set(idx2.tolist())
            parts = []
            # All matched pairs
            matched = t1.iloc[idx1.tolist()].reset_index(drop=True)
            matched = pd.concat(
                [
                    matched,
                    t2_renamed.iloc[idx2.tolist()].reset_index(drop=True),
                ],
                axis=1,
            )
            matched["separation_arcsec"] = sep.arcsec
            parts.append(matched)
            # Unmatched from t1
            um1_idx = [
                i for i in range(len(t1)) if i not in matched_t1_set
            ]
            if um1_idx:
                um1 = t1.iloc[um1_idx].reset_index(drop=True)
                for col in t2_renamed.columns:
                    um1[col] = np.nan
                um1["separation_arcsec"] = np.nan
                parts.append(um1)
            # Unmatched from t2
            um2_idx = [
                i for i in range(len(t2_renamed)) if i not in matched_t2_set
            ]
            if um2_idx:
                um2 = t2_renamed.iloc[um2_idx].reset_index(drop=True)
                for col in t1.columns:
                    um2[col] = np.nan
                um2["separation_arcsec"] = np.nan
                parts.append(um2)
            result = pd.concat(parts, ignore_index=True)

        elif join == "1not2":
            matched_t1_set = set(idx1.tolist())
            unmatched_idx = [
                i for i in range(len(t1)) if i not in matched_t1_set
            ]
            result = t1.iloc[unmatched_idx].reset_index(drop=True)
            result["separation_arcsec"] = np.nan

        elif join == "2not1":
            matched_t2_set = set(idx2.tolist())
            unmatched_idx = [
                i for i in range(len(t2_renamed)) if i not in matched_t2_set
            ]
            result = t2_renamed.iloc[unmatched_idx].reset_index(drop=True)
            result["separation_arcsec"] = np.nan

        else:
            raise ValueError(f"Unknown join type: {join}")

        return result

    # ── STILTS backend ──

    def _check_stilts(self) -> bool:
        """Check if STILTS jar and Java runtime are available."""
        jar_path = os.environ.get("STILTS_JAR_PATH", "")
        if not jar_path or not Path(jar_path).is_file():
            return False
        java = shutil.which("java")
        if java is None:
            return False
        logger.info("STILTS backend available: jar=%s java=%s", jar_path, java)
        return True

    @property
    def stilts_available(self) -> bool:
        return self._stilts_available

    async def _stilts_match(
        self,
        t1: pd.DataFrame,
        t2: pd.DataFrame,
        radius_arcsec: float,
        match_type: str,
        join: str,
        find: str,
    ) -> pd.DataFrame:
        """Match using STILTS tmatch2 subprocess."""
        jar_path = os.environ["STILTS_JAR_PATH"]
        tmpdir = tempfile.mkdtemp(prefix="stilts_xm_")

        try:
            in1 = os.path.join(tmpdir, "table1.csv")
            in2 = os.path.join(tmpdir, "table2.csv")
            out = os.path.join(tmpdir, "result.csv")

            t1.to_csv(in1, index=False)
            t2.to_csv(in2, index=False)

            # Map find parameter
            find_param = "best" if find == "best" else "all"

            cmd = [
                "java",
                "-jar",
                jar_path,
                "tmatch2",
                f"in1={in1}",
                "ifmt1=csv",
                f"in2={in2}",
                "ifmt2=csv",
                f"matcher={match_type}",
                "values1=ra dec",
                "values2=ra dec",
                f"params={radius_arcsec}",
                f"join={join}",
                f"find={find_param}",
                f"out={out}",
                "ofmt=csv",
            ]

            async with self._semaphore:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=60.0
                )

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(
                    f"STILTS tmatch2 failed (rc={proc.returncode}): {err_msg}"
                )

            if not Path(out).exists():
                raise RuntimeError("STILTS produced no output file")

            result = pd.read_csv(out)

            # STILTS adds a 'Separation' column in arcsec for sky matches;
            # normalize to our standard column name
            if "Separation" in result.columns:
                result = result.rename(columns={"Separation": "separation_arcsec"})

            return result

        finally:
            # Clean up temp files
            import shutil as _shutil

            _shutil.rmtree(tmpdir, ignore_errors=True)

    async def _stilts_multi_match(
        self,
        tables: list[pd.DataFrame],
        radius_arcsec: float,
    ) -> pd.DataFrame:
        """Match N tables using STILTS tmatchn."""
        jar_path = os.environ["STILTS_JAR_PATH"]
        n = len(tables)
        tmpdir = tempfile.mkdtemp(prefix="stilts_xmn_")

        try:
            out = os.path.join(tmpdir, "result.csv")
            cmd = [
                "java",
                "-jar",
                jar_path,
                "tmatchn",
                "multimode=pairs",
                "matcher=sky",
                f"params={radius_arcsec}",
                f"out={out}",
                "ofmt=csv",
            ]

            for i, tbl in enumerate(tables, start=1):
                csv_path = os.path.join(tmpdir, f"table{i}.csv")
                tbl.to_csv(csv_path, index=False)
                cmd.extend(
                    [
                        f"in{i}={csv_path}",
                        f"ifmt{i}=csv",
                        f"values{i}=ra dec",
                    ]
                )
            cmd.append(f"nin={n}")

            async with self._semaphore:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=60.0
                )

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(
                    f"STILTS tmatchn failed (rc={proc.returncode}): {err_msg}"
                )

            return pd.read_csv(out)

        finally:
            import shutil as _shutil

            _shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Validation ──

    @staticmethod
    def _validate_inputs(
        t1: pd.DataFrame,
        t2: pd.DataFrame,
        match_type: str,
        join: str,
        find: str,
    ) -> None:
        for label, tbl in [("table1", t1), ("table2", t2)]:
            if "ra" not in tbl.columns or "dec" not in tbl.columns:
                raise ValueError(
                    f"{label} must contain 'ra' and 'dec' columns; "
                    f"found: {list(tbl.columns)}"
                )
            if len(tbl) == 0:
                raise ValueError(f"{label} is empty")
        if match_type not in _VALID_MATCH_TYPES:
            raise ValueError(
                f"match_type must be one of {_VALID_MATCH_TYPES}, got '{match_type}'"
            )
        if join not in _VALID_JOINS:
            raise ValueError(
                f"join must be one of {_VALID_JOINS}, got '{join}'"
            )
        if find not in _VALID_FINDS:
            raise ValueError(
                f"find must be one of {_VALID_FINDS}, got '{find}'"
            )


# ── Module-level singleton ──

_engine: CrossMatchEngine | None = None


def get_crossmatch_engine() -> CrossMatchEngine:
    """Return (and lazily create) the module-level CrossMatchEngine singleton."""
    global _engine
    if _engine is None:
        _engine = CrossMatchEngine()
    return _engine
