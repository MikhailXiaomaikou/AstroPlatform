"""J3: SDSS SkyServer 直连 SQL 查询 connector.

这是 `run_sdss_sql` 工具的底层实现.  VizieR 全部 mirror 宕机时 SDSS
管线不应该跟着废 — 直接打 SDSS 官方 SkyServer API (不经 VizieR),
让 AI 能拿到 PhotoObjAll / SpecObjAll / Photoz / GalSpecInfo 这些表
的数据.

SDSS 用 **T-SQL** 方言 (微软 SQL Server), 不是 ADQL:
- `TOP N` 不是 `LIMIT`
- `dbo.fGetNearbyObjEq(ra, dec, radius_arcmin)` 做锥搜, 不是
  `CONTAINS(POINT, CIRCLE)`
- 必备过滤: `mode = 1 AND clean = 1` 去掉伪影/次级检测
- 列名惯例: `objID` (大写 ID), `ra`/`dec`, `u`/`g`/`r`/`i`/`z`
  (psfMag 是 `psfMag_u` 等, petroMag 是 `petroMag_u` 等)

Response (format=json) 是 `[{"Rows": [{col: val, ...}, ...]}]` 结构,
我们把它展平成和 ADQL 路径一样的 `{columns, data, row_count}` 以便
复用已有的 `store_adql_result_set` 缓存层.
"""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# DR18 是最新 release (2023 release of DR17 photometric), DR17 spectro.
# 列出 2 个 mirror 作为 fallback, 用法跟 SDSSConnector._query_skyserver
# 保持一致.
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
    """直接打 SkyServer SQL API 跑一条 T-SQL 查询.

    返回结构跟 ADQL 路径一致:
        {
          "columns": ["col1", "col2", ...],  # 保留 SkyServer 原始大小写
          "data": {"col1": [v1, v2, ...], ...},
          "column_aliases": {"col1_lower": "col1", ...},
          "row_count": int,
          "service": "sdss",
          "dr": "18",
        }

    失败时抛 ConnectionError / ValueError / httpx.HTTPStatusError —
    调用方统一捕获.
    """
    if not query or not query.strip():
        raise ValueError("SDSS SQL query is empty")

    dr_key = str(dr).strip()
    if dr_key not in SDSS_SQL_URLS:
        raise ValueError(f"Unsupported SDSS DR: {dr_key!r}, available: {list(SDSS_SQL_URLS.keys())}")

    # 简单的危险关键词防御 — 跟 execute_adql_query 行为一致.
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
                    # SkyServer GET 接口: ?cmd=<sql>&format=json
                    # 不走 POST 是因为 SkyServer 多数历史文档用 GET, GET 对
                    # 调试/日志友好 (URL 可复现), 查询长度没超过 URL 上限.
                    resp = await client.get(url, params={"cmd": query, "format": "json"})
                    resp.raise_for_status()
                    # 成功 → parse json
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
                # SQL 错误 — 重试不会改变语法, 直接往上抛.
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
    """SkyServer 返回 list[dict], 外层是 `[{"Rows": [...]}]`, 少数情况
    直接是 `{"Rows": [...]}`.  Rows 里每行是 `{col1: v1, col2: v2, ...}`.
    展平成列存储 (dict of col → list of values).
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

    # 列名从第一行的 keys 取, 保持原始顺序.
    columns_original = list(rows[0].keys())
    # 转置: rows of dicts → dict of lists.
    data: dict[str, list] = {col: [] for col in columns_original}
    for row in rows:
        for col in columns_original:
            val = row.get(col)
            # SkyServer 返回值: numbers 是 int/float, "NULL" 字符串表示 null.
            if val == "" or (isinstance(val, str) and val.strip().upper() == "NULL"):
                val = None
            data[col].append(val)

    # 给 run_python 的兼容层使用: AI 以前已经学会写 df['petromag_r']，
    # 但 SkyServer 官方列名是 petroMag_r / objID / zErr。工具结果展示
    # 保持原始列名，cache 侧再按这个 map 补小写别名。
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
