"""J3: SDSS SkyServer direct SQL query connector.

This is the underlying implementation of the `run_sdss_sql` tool. When all
VizieR mirrors are down the SDSS pipeline should still work — we hit the
SDSS official SkyServer API directly (bypassing VizieR) so the AI can access
tables such as PhotoObjAll / SpecObjAll / Photoz / GalSpecInfo.

SDSS uses **T-SQL** dialect (Microsoft SQL Server), not ADQL:
- `TOP N` instead of `LIMIT`
- `dbo.fGetNearbyObjEq(ra, dec, radius_arcmin)` for cone search, not
  `CONTAINS(POINT, CIRCLE)`
- Required filters: `mode = 1 AND clean = 1` to exclude artefacts / secondary detections
- Column naming conventions: `objID` (uppercase ID), `ra`/`dec`, `u`/`g`/`r`/`i`/`z`
  (psfMag columns are `psfMag_u` etc., petroMag columns are `petroMag_u` etc.)

The response (format=json) has the structure `[{"Rows": [{col: val, ...}, ...]}]`;
we flatten it into the same `{columns, data, row_count}` shape as the ADQL path
so the existing `store_adql_result_set` cache layer can be reused.
"""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# DR18 is the latest release (2023 release of DR17 photometric), DR17 spectro.
# Two mirrors are listed as fallback, consistent with SDSSConnector._query_skyserver.
SDSS_SQL_URLS: dict[str, list[str]] = {
    "18": [
        "https://skyserver.sdss.org/dr18/SkyServerWS/SearchTools/SqlSearch",
    ],
    "17": [
        "https://skyserver.sdss.org/dr17/SkyServerWS/SearchTools/SqlSearch",
    ],
    "16": [
        "https://skyserver.sdss.org/dr16/SkyServerWS/SearchTools/SqlSearch",
    ],
}


async def execute_sdss_sql(
    query: str,
    dr: str = "18",
    timeout_s: float = 120.0,
    max_attempts: int = 1,
    backoff_s: float = 0.75,
) -> dict:
    """Execute a T-SQL query directly against the SkyServer SQL API.

    Return structure matches the ADQL path:
        {
          "columns": ["col1", "col2", ...],  # original SkyServer casing preserved
          "data": {"col1": [v1, v2, ...], ...},
          "column_aliases": {"col1_lower": "col1", ...},
          "row_count": int,
          "service": "sdss",
          "dr": "18",
        }

    Raises ConnectionError / ValueError / httpx.HTTPStatusError on failure;
    callers should catch these uniformly.
    """
    if not query or not query.strip():
        raise ValueError("SDSS SQL query is empty")

    dr_key = str(dr).strip()
    if dr_key not in SDSS_SQL_URLS:
        raise ValueError(f"Unsupported SDSS DR: {dr_key!r}, available: {list(SDSS_SQL_URLS.keys())}")

    # Simple dangerous-keyword guard — consistent with execute_adql_query behaviour.
    upper = query.upper()
    for bad in ("DROP ", "DELETE ", "INSERT ", "UPDATE ", "ALTER ", "CREATE "):
        if bad in upper:
            raise ValueError(f"Forbidden keyword in SDSS SQL: {bad.strip()}")

    mirrors = SDSS_SQL_URLS[dr_key]
    last_err: Exception | None = None
    attempts = 0
    max_attempts = max(1, int(max_attempts or 1))

    for attempt in range(1, max_attempts + 1):
        for url in mirrors:
            attempts += 1
            try:
                async with httpx.AsyncClient(timeout=timeout_s) as client:
                    # SkyServer GET interface: ?cmd=<sql>&format=json
                    # Using GET rather than POST because most SkyServer
                    # documentation uses GET, it is friendlier for debugging/
                    # logging (URL is reproducible), and query length stays
                    # within URL limits.
                    resp = await client.get(url, params={"cmd": query, "format": "json"})
                    resp.raise_for_status()
                    # Success -- parse JSON
                    try:
                        payload = resp.json()
                    except Exception as je:
                        raise ValueError(f"SDSS SkyServer did not return JSON (likely SQL syntax error). "
                                         f"Response head: {resp.text[:300]!r}") from je
                    return _parse_skyserver_json(payload, query=query, dr=dr_key)
            except (httpx.HTTPError, ConnectionError, TimeoutError, asyncio.TimeoutError) as e:
                logger.info(
                    "SDSS SkyServer mirror %s failed on attempt %s/%s (%s: %s)",
                    url, attempt, max_attempts, type(e).__name__, str(e)[:200],
                )
                last_err = e
                continue
            except ValueError:
                # SQL error -- retrying won't fix a syntax error, re-raise immediately.
                raise
        if attempt < max_attempts and backoff_s > 0:
            await asyncio.sleep(backoff_s * (2 ** (attempt - 1)))

    msg = (
        f"All SDSS DR{dr_key} SkyServer mirrors unavailable after {attempts} attempts. "
        f"Tried: {', '.join(mirrors)}. Last error: {last_err}. "
        f"SkyServer sometimes has transient outages; retry the same query, "
        f"or fall back to VizieR for small sanity checks."
    )
    raise ConnectionError(msg)


def _parse_skyserver_json(payload, query: str, dr: str) -> dict:
    """Parse a SkyServer JSON response into column-oriented storage.

    SkyServer returns a list of dicts with outer structure `[{"Rows": [...]}]`;
    in rare cases it is `{"Rows": [...]}` directly. Each entry in Rows is
    `{col1: v1, col2: v2, ...}`. This function flattens that into a dict of
    col -> list of values.
    """
    rows: list[dict] = []
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict) and isinstance(first.get("Rows"), list):
            rows = first["Rows"]
    elif isinstance(payload, dict) and isinstance(payload.get("Rows"), list):
        rows = payload["Rows"]

    if not rows:
        return {
            "columns": [],
            "data": {},
            "row_count": 0,
            "service": "sdss",
            "dr": dr,
            "query": query,
            # PART Y Batch 6 (audit): stamp archive_version on empty results
            # too so claim_validator / paper_generator can still attribute
            # the (empty) lookup to a specific SDSS DR.
            "archive_version": f"SDSS DR{dr}",
        }

    # Column names are taken from the first row's keys, preserving original order.
    columns_original = list(rows[0].keys())
    # Transpose: rows of dicts -> dict of lists.
    data: dict[str, list] = {col: [] for col in columns_original}
    for row in rows:
        for col in columns_original:
            val = row.get(col)
            # SkyServer values: numbers are int/float; the string "NULL" represents null.
            if val == "" or (isinstance(val, str) and val.strip().upper() == "NULL"):
                val = None
            data[col].append(val)

    # Compatibility layer for run_python: the AI has previously learned to write
    # df['petromag_r'], but official SkyServer column names are petroMag_r / objID /
    # zErr. Tool results display original column names; the cache layer supplements
    # them with lowercase aliases via this map.
    column_aliases = {col.lower(): col for col in columns_original}

    return {
        "columns": columns_original,
        "data": data,
        "column_aliases": column_aliases,
        "row_count": len(rows),
        "service": "sdss",
        "dr": dr,
        "query": query,
        # PART Y Batch 6 (audit): stamp archive_version so downstream
        # consumers know exactly which SDSS DR the row came from.
        "archive_version": f"SDSS DR{dr}",
    }
