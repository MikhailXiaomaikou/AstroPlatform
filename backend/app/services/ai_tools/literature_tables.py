"""Literature-table extraction: measurement cache schema v2 + arXiv table fetch.

Moved verbatim out of app/services/ai_tools/__init__.py (H2 split,
2026-07-03). Tool schemas here cover: extract_literature_tables.
Schemas are reassembled into TOOLS (exact pre-split order) and tool
calls are still dispatched by _execute_tool_inner in the package
__init__ — this module is an implementation detail, import from
app.services.ai_tools.
"""

import os
from typing import Any

from app.services.ai_tools import (
    _resolved_session_cache_key,
    _session_cache_key,
    get_session_cached_results,
    logger,
    store_session_results,
)
from app.services.ai_tools.literature import _arxiv_id_from_table_input

TOOL_SCHEMAS = [
    {
        "name": "extract_literature_tables",
        "description": (
            "Extract machine-readable tables from an arXiv/ar5iv paper and attach "
            "row-level citation provenance. Use this after search_literature when "
            "the user asks to compile literature samples, fit relations, or quote "
            "paper-table measurements such as log L[CII], FWHM, redshift, flux, "
            "or line widths. First phase supports arXiv IDs/URLs only; ADS-only "
            "bibcodes without an arXiv identifier are not auto-converted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "arxiv_id": {"type": "string", "description": "arXiv ID, arXiv:ID, or arxiv.org URL"},
                "arxiv_url": {"type": "string", "description": "Optional arXiv abs/pdf URL"},
                "url": {"type": "string", "description": "Optional arXiv URL alias (same effect as arxiv_url)."},
                "paper": {
                    "type": "object",
                    "description": "Optional paper object from search_literature containing bibcode/arxiv_url/title/authors/year.",
                },
                "column_mapping": {
                    "type": "object",
                    "description": (
                        "raw_only recovery: {field: column-header-or-0-based-index} "
                        "overriding the automatic column detection. Fields: source_name, "
                        "redshift, line, log_luminosity, luminosity_err, fwhm_km_s, "
                        "fwhm_err, mu_lens. Use ONLY after the user has confirmed which "
                        "column is which — never guess the mapping yourself."
                    ),
                },
                "table_id": {
                    "type": "string",
                    "description": "Optional table_id to apply column_mapping to one specific table.",
                },
            },
        },
    },
]


# Schema v2 adds per-row fields required by the Bayesian fit path
# (two-axis errors) plus lensing/cosmology bookkeeping.  Rows stored
# under v1 are migrated on read (see _normalize_measurement_to_v2).
_LITERATURE_SCHEMA_VERSION = 2

# Keys a v2 row must carry (value may be None when the source paper
# didn't report the quantity).  We do not require AI/regex to extract
# them; downstream tools (fit_line_lfr, demagnify_sample) decide how
# to handle nulls.
_V2_MEASUREMENT_KEYS: tuple[str, ...] = (
    "fwhm_err_km_s",
    "log_luminosity_err",
    "mu_lens",
    "is_lensed",
    "source_cosmology",
)


def _normalize_measurement_to_v2(row: dict[str, Any]) -> dict[str, Any]:
    """Ensure a single line_measurement dict carries the v2 schema keys.

    Called on both the write path (when caching fresh extraction
    results) and the read path (when resolving older v1 cache entries).
    Idempotent — v2 rows pass through unchanged.  None is the explicit
    sentinel for "paper did not report this quantity" so callers can
    distinguish it from "field missing from schema".
    """
    if not isinstance(row, dict):
        return row
    out = dict(row)
    for key in _V2_MEASUREMENT_KEYS:
        if key not in out:
            out[key] = None
    return out


def _literature_table_cache_payload(payload: dict[str, Any], cache_key: str) -> dict[str, Any]:
    """Cache literature-table data in a structured, downstream-readable shape."""
    raw_measurements = payload.get("line_measurements") or []
    line_measurements = [
        _normalize_measurement_to_v2(row) for row in raw_measurements if isinstance(row, dict)
    ]
    tables = payload.get("tables") or []
    return {
        "schema_version": _LITERATURE_SCHEMA_VERSION,
        "kind": "literature_tables",
        "cache_key": cache_key,
        "arxiv_id": payload.get("arxiv_id"),
        "title": payload.get("title"),
        "authors": payload.get("authors") or [],
        "year": payload.get("year"),
        "bibcode": payload.get("bibcode"),
        "doi": payload.get("doi"),
        "source_url": payload.get("source_url"),
        "line_measurements": line_measurements,
        "tables": tables,
        "source_summary": {
            "line_measurement_count": len(line_measurements),
            "raw_table_count": len(tables),
            "extraction_status": payload.get("extraction_status") or (
                "measurement_ready" if line_measurements else "raw_only"
            ),
            "normalization_status": payload.get("normalization_status") or (
                "line_measurements_detected" if line_measurements else "no_line_measurement_schema"
            ),
            "supports_measurement_claims": bool(line_measurements),
        },
        # Carried when the rows came from a user-confirmed column-mapping
        # rerun (raw_only recovery) — a later fit-from-cache must not present
        # user-asserted columns as auto-detected ones.
        **({
            "column_mapping_applied": payload.get("column_mapping_applied"),
            "column_mapping_source": payload.get("column_mapping_source"),
        } if payload.get("column_mapping_source") else {}),
    }


def _literature_tables_llm_summary(payload: dict[str, Any], cache_key: str) -> dict[str, Any]:
    line_measurements = payload.get("line_measurements") or []
    tables = payload.get("tables") or []
    preview_rows: list[dict[str, Any]] = []
    for row in line_measurements[:10]:
        if not isinstance(row, dict):
            continue
        citation = row.get("citation") if isinstance(row.get("citation"), dict) else {}
        preview_rows.append({
            "source_name": row.get("source_name"),
            "redshift": row.get("redshift"),
            "line_id": row.get("line_id"),
            "log_luminosity": row.get("log_luminosity"),
            "fwhm_km_s": row.get("fwhm_km_s"),
            "table_label": row.get("table_label") or citation.get("table_label"),
            "citation": row.get("bibcode") or row.get("arxiv_id") or citation.get("bibcode") or citation.get("arxiv_id"),
        })
    return {
        "cache_key": cache_key,
        "raw_table_count": len(tables),
        "line_measurement_count": len(line_measurements),
        "extraction_status": payload.get("extraction_status") or (
            "measurement_ready" if line_measurements else "raw_only"
        ),
        "normalization_status": payload.get("normalization_status") or (
            "line_measurements_detected" if line_measurements else "no_line_measurement_schema"
        ),
        "fit_ready": bool(line_measurements),
        "measurement_schema": [
            "source_name", "redshift", "line_id", "log_luminosity",
            "fwhm_km_s", "quality_flags", "citation",
        ],
        "preview_rows": preview_rows,
        "next_step": (
            f"Call fit_line_lfr(cache_key='{cache_key}') to fit the relation from these cited rows. "
            "Do not use synthetic run_python or hardcoded literature samples."
            if line_measurements else
            "No normalized line_measurements were detected; do not fit until columns are mapped."
        ),
    }


def _measurement_rows_from_cache_payload(payload: Any) -> list[dict[str, Any]]:
    # Every returned row goes through _normalize_measurement_to_v2 so
    # downstream code can rely on the v2 schema regardless of whether
    # the cache entry was written under schema_version=1 or =2.
    if isinstance(payload, dict):
        for key in ("line_measurements", "measurements", "rows", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [
                    _normalize_measurement_to_v2(dict(row))
                    for row in value if isinstance(row, dict)
                ]
        return []
    if isinstance(payload, list):
        return [
            _normalize_measurement_to_v2(dict(row))
            for row in payload if isinstance(row, dict)
        ]
    return []


def _resolve_literature_measurement_cache(cache_key: str, python_session_id: str | None) -> tuple[list[dict[str, Any]], str]:
    requested = (cache_key or "latest_literature_tables").strip() or "latest_literature_tables"
    resolved = _resolved_session_cache_key(requested, python_session_id)
    payload = get_session_cached_results(requested, python_session_id)
    rows = _measurement_rows_from_cache_payload(payload)
    return (rows, resolved) if rows else ([], resolved)


def _resolve_multiple_literature_caches(
    cache_keys: list[str],
    python_session_id: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """PART AF C2 — union multiple literature caches into one row list.

    M5 audit reproducer: AI extracted ALPINE (74 rows) + REBELS (13
    rows) into separate caches but fit_line_lfr only saw the most
    recent one. Now the tool can pass `cache_keys=[...]` and we
    merge across keys, deduping by source_name (or arxiv_id+row_index
    when name collides across surveys with different objects).

    Returns (merged_rows, list_of_resolved_keys_actually_loaded).
    """
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import _resolve_literature_measurement_cache
    merged: list[dict[str, Any]] = []
    resolved_keys: list[str] = []
    seen_source_keys: set[str] = set()
    for raw_key in cache_keys:
        key = (raw_key or "").strip()
        if not key:
            continue
        rows, resolved = _resolve_literature_measurement_cache(key, python_session_id)
        if not rows:
            continue
        resolved_keys.append(resolved)
        for row in rows:
            # Dedupe by (source_name, bibcode) — same source from same
            # paper twice would be redundant; same source name across
            # different bibcodes can legitimately co-exist (different
            # surveys' independent measurements of e.g. HZ7).
            sname = str(row.get("source_name") or "").strip()
            bib = str(row.get("bibcode") or row.get("arxiv_id") or "").strip()
            dedupe_key = f"{sname}::{bib}"
            if dedupe_key in seen_source_keys:
                continue
            seen_source_keys.add(dedupe_key)
            merged.append(row)
    return merged, resolved_keys


# PART Z C5: in-memory rolling-window rate limit for the chat-side
# extract_literature_tables call.
#
# Why not Redis? Render free-tier has 1 web worker so in-memory is
# already correct; if we go multi-worker the limit becomes per-worker
# (effectively N×cap, still well below the published ar5iv quotas).
# When that becomes a real problem we'll move this to connector_cache.
_arxiv_tool_calls: dict[str, list[float]] = {}
_ARXIV_RATE_WINDOW_S = 3600.0
_ARXIV_ANON_CAP_PER_HOUR = 5
_ARXIV_AUTH_CAP_PER_HOUR = 50


def _arxiv_rate_cap(*, user_id: str | None) -> int:
    env_name = (
        "ARXIV_TABLE_AUTH_CAP_PER_HOUR"
        if user_id
        else "ARXIV_TABLE_ANON_CAP_PER_HOUR"
    )
    default = _ARXIV_AUTH_CAP_PER_HOUR if user_id else _ARXIV_ANON_CAP_PER_HOUR
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using default cap=%d", env_name, raw, default)
        return default


def _check_arxiv_tool_rate_limit(
    *,
    user_id: str | None = None,
    chat_session_id: str | None = None,
) -> tuple[bool, int, int, str]:
    """Returns (allowed, used_in_window, cap, key_kind).

    The user_id path is per-user (50/hr), the anonymous path keys on the
    chat_session_id (5/hr) so each open chat tab is its own quota
    rather than every anonymous tab sharing one bucket.
    """
    import time as _time

    now = _time.monotonic()
    if user_id:
        key = f"user:{user_id}"
        cap = _arxiv_rate_cap(user_id=user_id)
        kind = "authenticated user"
    else:
        # Anonymous: identify by chat_session_id; fall back to a single
        # shared bucket if even that is missing (very early bootstrap).
        key = f"session:{chat_session_id or 'anonymous-fallback'}"
        cap = _arxiv_rate_cap(user_id=None)
        kind = "anonymous chat session"

    cutoff = now - _ARXIV_RATE_WINDOW_S
    timestamps = [t for t in _arxiv_tool_calls.get(key, []) if t > cutoff]
    if len(timestamps) >= cap:
        _arxiv_tool_calls[key] = timestamps
        return False, len(timestamps), cap, kind
    timestamps.append(now)
    _arxiv_tool_calls[key] = timestamps
    return True, len(timestamps), cap, kind


# PART Z: retry + cache wrapper around extract_arxiv_tables_payload.
# The HTTP endpoint was already gated and rate-limited (M19), but the chat
# tool path bypassed that and could repeatedly hit ar5iv on the AI's whim.
# Now every chat call goes through a 24h connector_cache lookup +
# circuit-breaker, so a paper fetched once stays cached and ar5iv outages
# trip the breaker instead of letting AI users amplify load.

async def _cached_extract_arxiv_tables_payload(arxiv_id_raw: str) -> dict:
    """24h-cached wrapper around extract_arxiv_tables_payload.

    Cache key includes a normalised arxiv_id so the various aliases
    (`2310.12345` / `arXiv:2310.12345` / arxiv URL) hit the same entry.
    Concurrent identical calls share a single upstream fetch via
    connector_cache.get_or_compute's singleflight.
    """
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import _extract_arxiv_tables_payload_with_retry
    from app.api.arxiv import _clean_arxiv_id
    from app.services.connector_cache import get_or_compute

    cleaned = _clean_arxiv_id(arxiv_id_raw) or arxiv_id_raw.strip()
    cache_key = f"arxiv_tables:v3:{cleaned}"

    async def _compute():
        return await _extract_arxiv_tables_payload_with_retry(arxiv_id_raw)

    return await get_or_compute(cache_key, _compute, ttl=24 * 3600)


def _arxiv_retryable_exceptions():
    """Build the retry-eligible exception tuple at import time without
    forcing httpx to be imported on cold paths.  Network timeouts and
    transient connection failures retry; HTTPException (e.g. 404 "no
    tables found") does NOT retry — that's a permanent semantic error.
    """
    try:
        import httpx
        return (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            ConnectionError,
            TimeoutError,
            OSError,
        )
    except ImportError:
        return (ConnectionError, TimeoutError, OSError)


async def _extract_arxiv_tables_payload_with_retry(arxiv_id_raw: str) -> dict:
    """Inner: retries via with_retry, fronted by the 24h cache above."""
    from app.api.arxiv import extract_arxiv_tables_payload
    from app.connectors.retry import with_retry

    @with_retry(
        max_retries=2,
        base_delay=1.0,
        retryable_exceptions=_arxiv_retryable_exceptions(),
    )
    async def _do_fetch(_id: str) -> dict:
        return await extract_arxiv_tables_payload(_id)

    return await _do_fetch(arxiv_id_raw)


async def _exec_extract_literature_tables(
    inp: dict,
    python_session_id: str = "default",
    *,
    user_id: str | None = None,
    chat_session_id: str | None = None,
) -> dict:
    """Extract arXiv tables and cache any normalized measurement rows.

    PART Z C1 originally hard-rejected `user_id is None` to keep
    anonymous chat from amplifying load on ar5iv. Real-world UX showed
    the gate was too sharp — even the platform owner kept hitting it.
    PART Z C5 (this update): drop the hard reject, add a per-key
    rate-limit (5/hr for anonymous, 50/hr for logged-in users). The
    primary DoS defences are still in place — 24h connector_cache for
    repeat fetches + circuit-breaker on httpx errors — so the rate
    limit is a thin extra layer rather than the only one.
    """
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import _cached_extract_arxiv_tables_payload
    from app.api.arxiv import extract_arxiv_tables_payload  # noqa: F401  (kept for callers)

    # Prefer the real chat session id, but fall back to the Python/session
    # runtime id.  Browser/UI fresh-chat flows can reach the tool executor
    # before a durable chat_session_id exists; using only
    # "anonymous-fallback" turned a 20-paper local UI benchmark into one
    # shared 5/hr bucket after the first few turns.
    limit_session_id = chat_session_id or python_session_id
    allowed, used, cap, key_kind = _check_arxiv_tool_rate_limit(
        user_id=user_id, chat_session_id=limit_session_id,
    )
    if not allowed:
        return {
            "success": False,
            "error": (
                f"extract_literature_tables rate limit reached "
                f"({used}/{cap} calls in the last hour for this "
                f"{key_kind}). Anonymous chat sessions are capped at 5/hr "
                f"to keep the public ar5iv / arxiv.org services healthy. "
                f"Sign in at /account for the larger 50/hr authenticated "
                f"quota, or wait for the rolling window to clear."
            ),
            "error_class": "rate_limit_exceeded",
            "rate_limit_used": used,
            "rate_limit_cap": cap,
            "rate_limit_kind": key_kind,
        }

    raw_id = _arxiv_id_from_table_input(inp)
    if not raw_id:
        return {
            "success": False,
            "error": "extract_literature_tables requires arxiv_id, arxiv_url, or paper.bibcode='arXiv:<id>'.",
            "error_class": "missing_arxiv_id",
        }
    try:
        payload = await _cached_extract_arxiv_tables_payload(raw_id)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Literature table extraction failed: {exc}",
            "error_class": "literature_table_extraction_failed",
        }

    # raw_only recovery (2026-06-11): a USER-CONFIRMED column mapping re-runs
    # the normalization over the already-extracted tables (no re-fetch). The
    # mapping only says which column is which — every value still comes
    # verbatim from the table cells.
    column_mapping = inp.get("column_mapping") if isinstance(inp.get("column_mapping"), dict) else None
    mapping_table_id = str(inp.get("table_id") or "").strip() or None
    if column_mapping:
        from app.api.arxiv import _normalize_line_measurements

        payload = dict(payload)  # never mutate the shared cached payload
        payload["line_measurements"] = _normalize_line_measurements(
            payload.get("tables") or [],
            column_mapping=column_mapping,
            table_id=mapping_table_id,
        )
        # The fetch-time payload carries its own status fields (arxiv.py) —
        # refresh them so a successful mapping rerun does not keep reporting
        # the stale raw_only verdict.
        payload["extraction_status"] = (
            "measurement_ready" if payload["line_measurements"] else "raw_only"
        )
        payload["normalization_status"] = (
            "line_measurements_detected" if payload["line_measurements"]
            else "no_line_measurement_schema"
        )
        # Stamp the mapping provenance on the payload too, so the cache copy
        # (read by a LATER fit_line_lfr) carries it — not just this result.
        payload["column_mapping_applied"] = dict(column_mapping)
        payload["column_mapping_source"] = "user_confirmed"

    line_measurements = payload.get("line_measurements") or []
    tables = payload.get("tables") or []
    latest_cache_key = _session_cache_key("latest_literature_tables", python_session_id) or "latest_literature_tables"
    cleaned_arxiv_id = str(payload.get("arxiv_id") or raw_id).replace("arXiv:", "").strip()
    raw_cache_key_base = f"literature_tables_raw:{cleaned_arxiv_id or 'unknown'}"
    raw_cache_key = _session_cache_key(raw_cache_key_base, python_session_id) or raw_cache_key_base
    cache_key = latest_cache_key if line_measurements else raw_cache_key
    cache_value = _literature_table_cache_payload(payload, cache_key)
    store_session_results(
        "latest_literature_tables" if line_measurements else raw_cache_key_base,
        python_session_id,
        cache_value,
    )
    if not line_measurements:
        # A raw-only extraction must not wipe an earlier fit-ready cache in this
        # session, and it must never inspect another session's global fallback.
        existing_latest = get_session_cached_results(
            "latest_literature_tables", python_session_id
        )
        if not existing_latest:
            # No fit-ready cache exists yet; keeping a latest raw cache preserves
            # old UX for "extract then inspect raw tables" while still preventing
            # zero-row overwrites after a successful extraction.
            store_session_results("latest_literature_tables", python_session_id, cache_value)

    bibcodes = [
        str(row.get("bibcode") or "").strip()
        for row in line_measurements
        if isinstance(row, dict) and str(row.get("bibcode") or "").strip()
    ]
    if not bibcodes and payload.get("bibcode"):
        bibcodes = [str(payload["bibcode"])]

    source_url = str(payload.get("source_url") or "").strip()
    dataset = {
        "service_key": "literature_table",
        "service_name": "Literature measurement table",
        "archive_version": "arXiv/ar5iv live source",
        "source_authority": "paper_table",
        "article": str(payload.get("bibcode") or ""),
        "publisher": "arXiv",
        "reference_url": source_url,
        "source_urls": [source_url] if source_url else [],
        "acknowledgement_template": (
            "This work used machine-readable tables extracted from the cited literature; "
            "verify the original paper table before publication."
        ),
    }
    field_bibcodes = {
        "columns": {"literature_table_bibcode": bibcodes},
        "mapping": {"literature_table_bibcode": "line_measurements"},
        "source_column_pattern": "literature_table_row_citation",
    }
    result = {
        "success": True,
        "arxiv_id": payload.get("arxiv_id"),
        "title": payload.get("title"),
        "authors": payload.get("authors") or [],
        "year": payload.get("year"),
        "bibcode": payload.get("bibcode"),
        "doi": payload.get("doi"),
        "source_url": source_url,
        "result_granularity": "paper_table",
        "supports_measurement_claims": bool(line_measurements),
        "tables": tables,
        "line_measurements": line_measurements,
        "line_measurement_count": len(line_measurements),
        "raw_table_count": len(tables),
        "extraction_status": payload.get("extraction_status") or (
            "measurement_ready" if line_measurements else "raw_only"
        ),
        "normalization_status": payload.get("normalization_status") or (
            "line_measurements_detected" if line_measurements else "no_line_measurement_schema"
        ),
        "cache_key": cache_key,
        "fit_ready": bool(line_measurements),
        "llm_summary": _literature_tables_llm_summary(payload, cache_key),
        "provenance": {
            "datasets": [dataset],
            "field_bibcodes": field_bibcodes if bibcodes else None,
            "coverage": {
                "field_level": {
                    "available": bool(bibcodes),
                    "bibcode_columns_found": 1 if bibcodes else 0,
                    "unique_bibcodes": len(set(bibcodes)),
                },
                "primary_citation_source": "field_level" if bibcodes else "table_level",
            },
        },
        "warnings": list(payload.get("warnings") or ([] if line_measurements else [
            "Tables were extracted, but no reliable line-measurement schema was detected. Do not fit L[CII]-FWHM until columns are mapped."
        ])),
    }
    if column_mapping:
        result["column_mapping_applied"] = dict(column_mapping)
        result["column_mapping_source"] = "user_confirmed"
        result["warnings"].append(
            "Column mapping was user-supplied for fields: "
            f"{sorted(column_mapping)} — values are verbatim table cells; "
            "mapping provenance: user_confirmed."
        )
    if not line_measurements and not tables:
        result["__message_to_model__"] = (
            f"No data tables were detected in arXiv:{payload.get('arxiv_id')}. "
            "This is a semantic no-data result, not permission to infer measurements "
            "from the abstract or from memory. Allowed next steps: search for a "
            "companion measurement-table paper, or tell the user that this paper does "
            "not expose a machine-readable table through the current extractor. "
            "Do NOT quote L[CII] / Hα / FWHM / line widths or fit a relation."
        )
    elif not line_measurements:
        result["__message_to_model__"] = (
            f"Raw literature tables were extracted from arXiv:{payload.get('arxiv_id')} "
            f"({len(tables)} table(s)), but no normalized line_measurements were detected. "
            "Treat this as a raw-only extraction, not as a fit-ready measurement table. "
            "It usually means the paper's tables are missing one of the required columns "
            "(source name, redshift, log L<line>, FWHM) — for example: REBELS often puts the "
            "size measurements in one paper and the line measurements in a companion paper. "
            "Allowed next steps: "
            "(a) SHOW the user the detected columns of each table (the `tables[i].columns` "
            "lists in this result) and ask THEM which column holds the source name / "
            "redshift / log luminosity / FWHM; once the user confirms, retry "
            "extract_literature_tables with column_mapping={field: header-or-index} "
            "(and table_id to target one table). NEVER guess the mapping yourself. OR "
            "(b) call `search_literature` to find the companion / measurement-table paper for "
            "this object class, OR "
            "(c) emit `<tools_returned_nothing failed_tools=\"extract_literature_tables\" "
            "rationale=\"this paper's tables do not contain line measurements\"/>` if the user "
            "asked specifically about THIS paper. "
            "Do NOT quote L[CII] / Hα / FWHM / line widths or fit a relation unless a "
            "measurement table is mapped, and do NOT hardcode remembered ALPINE / REBELS values."
        )
    else:
        result["__message_to_model__"] = (
            f"Extracted {len(line_measurements)} normalized line_measurements and cached them as "
            f"{cache_key}. For a luminosity/FWHM relation, call fit_line_lfr with this cache_key. "
            "Do NOT create a synthetic or hardcoded literature dataframe in run_python."
        )
    return result
