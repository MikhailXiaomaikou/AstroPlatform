"""Raw connector response cache.

Caches normalized connector search results (lists of AstroObject) keyed
by sha256(connector_name + sorted params). Saves upstream bandwidth and
hides transient failures behind a warm cache.

Backends (auto-select in this order):
1. Redis — when settings.redis_url is reachable
2. SQLite — persistent fallback at data/connector_cache.db
3. Null — disabled (test mode, unit tests don't touch upstream)

TTL is layered by query kind: metadata fetches live 24h, cone searches
1h, free-form ADQL 15 min. Callers override via `ttl=...`.

Also provides singleflight: concurrent identical queries share one
upstream call instead of stampeding the data source.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import pickle
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


# ── T1 (PART T): pickle RCE hardening ───────────────────────────────────
# Both connector_cache and subprocess_backend call pickle.loads on data from
# external sources (Redis / SQLite / parent-process cache). A malicious pickle's
# __reduce__ can achieve RCE; even if the source is trusted today, a single
# Redis credential leak or cache file write turns the entire service into an RCE
# entry point. RestrictedUnpickler whitelists only known archive / stdlib /
# scientific types; all others raise UnpicklingError.

_ALLOWED_PICKLE_CLASSES: set[tuple[str, str]] = {
    # stdlib primitives + containers
    ("builtins", "list"), ("builtins", "dict"), ("builtins", "set"),
    ("builtins", "tuple"), ("builtins", "frozenset"),
    ("builtins", "str"), ("builtins", "bytes"), ("builtins", "bytearray"),
    ("builtins", "int"), ("builtins", "float"), ("builtins", "bool"),
    ("builtins", "complex"), ("builtins", "NoneType"), ("builtins", "range"),
    ("builtins", "slice"),
    # datetime
    ("datetime", "date"), ("datetime", "datetime"), ("datetime", "time"),
    ("datetime", "timedelta"), ("datetime", "timezone"),
    # collections
    ("collections", "OrderedDict"), ("collections", "defaultdict"),
    ("collections", "Counter"), ("collections", "deque"),
    # uuid
    ("uuid", "UUID"),
    # project types
    ("app.connectors.base", "AstroObject"),
    ("app.api.data", "SearchResult"),
    # numpy
    ("numpy", "ndarray"), ("numpy", "dtype"),
    ("numpy.core.multiarray", "_reconstruct"),
    ("numpy.core.multiarray", "scalar"),
    ("numpy._core.multiarray", "_reconstruct"),
    ("numpy._core.multiarray", "scalar"),
    ("numpy._core.numeric", "_frombuffer"),
    ("numpy.core.numeric", "_frombuffer"),
    ("numpy", "int8"), ("numpy", "int16"), ("numpy", "int32"), ("numpy", "int64"),
    ("numpy", "uint8"), ("numpy", "uint16"), ("numpy", "uint32"), ("numpy", "uint64"),
    ("numpy", "float16"), ("numpy", "float32"), ("numpy", "float64"),
    ("numpy", "bool_"), ("numpy", "str_"), ("numpy", "bytes_"),
    # pandas
    ("pandas.core.frame", "DataFrame"),
    ("pandas.core.series", "Series"),
    ("pandas.core.indexes.base", "Index"),
    ("pandas.core.indexes.base", "_new_Index"),
    ("pandas.core.indexes.numeric", "Int64Index"),
    ("pandas.core.indexes.range", "RangeIndex"),
    ("pandas.core.internals.managers", "BlockManager"),
    ("pandas.core.internals.managers", "_unpickle_block"),
    ("pandas.core.internals.blocks", "new_block"),
    ("pandas._libs.internals", "_unpickle_block"),
    # astropy (types commonly returned by connectors)
    ("astropy.table.table", "Table"),
    ("astropy.table.table", "QTable"),
    ("astropy.table.column", "Column"),
    ("astropy.table.column", "MaskedColumn"),
    ("astropy.units.quantity", "Quantity"),
}


class RestrictedUnpickler(pickle.Unpickler):
    """Only permit classes in the allowlist; everything else raises.

    Blocks `__reduce__`-based RCE (os.system, subprocess.Popen, eval, etc.)
    since those classes/functions are not in _ALLOWED_PICKLE_CLASSES.
    """

    def find_class(self, module: str, name: str):
        if (module, name) in _ALLOWED_PICKLE_CLASSES:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(
            f"disallowed pickle class: {module}.{name}. "
            f"Add to _ALLOWED_PICKLE_CLASSES in connector_cache.py if safe."
        )


def loads_safe(data: bytes):
    """pickle.loads equivalent with class allowlist (T1)."""
    return RestrictedUnpickler(io.BytesIO(data)).load()

# ---------------------------------------------------------------------------
# TTL layer constants (seconds)
# ---------------------------------------------------------------------------
TTL_METADATA = 24 * 3600
TTL_CONE = 3600
TTL_ADQL = 900
TTL_DEFAULT = TTL_CONE

# Singleflight registry — one asyncio.Future per in-flight key
_inflight: dict[str, asyncio.Future] = {}
_inflight_lock: asyncio.Lock | None = None
# Strong refs to running _runner tasks so they cannot be garbage-collected mid-run
_runner_tasks: set[asyncio.Task] = set()


def _lock() -> asyncio.Lock:
    global _inflight_lock
    if _inflight_lock is None:
        _inflight_lock = asyncio.Lock()
    return _inflight_lock


def build_key(connector: str, endpoint: str, params: dict[str, Any]) -> str:
    """Content-addressed key for a connector call."""
    try:
        payload = json.dumps(params, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr(params)
    digest = hashlib.sha256(
        f"{connector}|{endpoint}|{payload}".encode("utf-8")
    ).hexdigest()
    return f"conncache:{connector}:{endpoint}:{digest}"


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class NullBackend:
    name = "null"

    def get(self, key: str):
        return None

    def set(self, key: str, value: Any, ttl: int) -> None:
        return None


class SQLiteBackend:
    name = "sqlite"

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS connector_cache "
                "(key TEXT PRIMARY KEY, value BLOB, expires_at REAL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_expires ON connector_cache(expires_at)"
            )

    def get(self, key: str):
        try:
            with sqlite3.connect(str(self.path)) as db:
                row = db.execute(
                    "SELECT value, expires_at FROM connector_cache WHERE key = ?",
                    (key,),
                ).fetchone()
            if not row:
                return None
            value, expires_at = row
            if expires_at < time.time():
                return None
            return loads_safe(value)
        except (sqlite3.Error, pickle.UnpicklingError, EOFError) as e:
            logger.debug("sqlite cache get failed: %s", e)
            return None

    def set(self, key: str, value: Any, ttl: int) -> None:
        try:
            payload = pickle.dumps(value)
            with sqlite3.connect(str(self.path)) as db:
                db.execute(
                    "INSERT OR REPLACE INTO connector_cache (key, value, expires_at) "
                    "VALUES (?, ?, ?)",
                    (key, payload, time.time() + ttl),
                )
                # Opportunistic eviction of a handful of expired rows
                db.execute(
                    "DELETE FROM connector_cache WHERE expires_at < ? "
                    "AND rowid IN (SELECT rowid FROM connector_cache "
                    "WHERE expires_at < ? LIMIT 100)",
                    (time.time(), time.time()),
                )
        except (sqlite3.Error, pickle.PicklingError) as e:
            logger.debug("sqlite cache set failed: %s", e)


class RedisBackend:
    name = "redis"

    def __init__(self, url: str):
        import redis as _redis
        kwargs: dict = {"socket_connect_timeout": 2, "socket_timeout": 2}
        if url.startswith("rediss://"):
            from app.config import settings as _s
            kwargs.update(_s.redis_tls_kwargs())
        self._client = _redis.Redis.from_url(url, **kwargs)
        # Probe
        self._client.ping()

    def get(self, key: str):
        try:
            raw = self._client.get(key)
            if raw is None:
                return None
            return loads_safe(raw)
        except Exception as e:
            logger.debug("redis cache get failed: %s", e)
            return None

    def set(self, key: str, value: Any, ttl: int) -> None:
        try:
            self._client.setex(key, ttl, pickle.dumps(value))
        except Exception as e:
            logger.debug("redis cache set failed: %s", e)


# ---------------------------------------------------------------------------
# Backend selection (lazy, shared)
# ---------------------------------------------------------------------------
_backend: NullBackend | SQLiteBackend | RedisBackend | None = None


def get_backend():
    global _backend
    if _backend is not None:
        return _backend

    backend_choice = getattr(settings, "connector_cache_backend", "auto")

    if backend_choice == "null":
        _backend = NullBackend()
        return _backend

    if backend_choice in ("auto", "redis"):
        try:
            _backend = RedisBackend(settings.redis_url)
            logger.info("connector_cache: using Redis backend")
            return _backend
        except Exception as e:
            if backend_choice == "redis":
                logger.warning("connector_cache: Redis requested but unavailable (%s); using null", e)
                _backend = NullBackend()
                return _backend
            logger.debug("connector_cache: Redis unavailable (%s); falling back to SQLite", e)

    if backend_choice in ("auto", "sqlite"):
        try:
            db_path = Path(settings.local_storage_dir).parent / "connector_cache.db"
            _backend = SQLiteBackend(db_path)
            logger.info("connector_cache: using SQLite backend at %s", db_path)
            return _backend
        except Exception as e:
            logger.warning("connector_cache: SQLite init failed (%s); disabling cache", e)

    _backend = NullBackend()
    return _backend


def reset_backend() -> None:
    """Drop the cached backend instance (test helper)."""
    global _backend
    _backend = None


# ---------------------------------------------------------------------------
# High-level get_or_compute with singleflight
# ---------------------------------------------------------------------------


async def get_or_compute(
    key: str,
    compute,
    *,
    ttl: int = TTL_DEFAULT,
    force_refresh: bool = False,
):
    """Look up `key`; on miss run the async `compute()` and cache the result.

    Concurrent calls with the same key share a single compute via asyncio
    Future, so N parallel identical queries produce 1 upstream call.
    """
    backend = get_backend()

    if not force_refresh:
        cached = backend.get(key)
        if cached is not None:
            logger.debug("connector_cache HIT %s", key)
            return cached

    lock = _lock()
    async with lock:
        # A parallel request may have filled the cache or started its own flight
        if key in _inflight:
            fut = _inflight[key]
            logger.debug("connector_cache SINGLEFLIGHT join %s", key)
        else:
            if not force_refresh:
                cached = backend.get(key)
                if cached is not None:
                    return cached
            fut = asyncio.get_running_loop().create_future()
            _inflight[key] = fut

            async def _runner():
                try:
                    result = await compute()
                    backend.set(key, result, ttl)
                    fut.set_result(result)
                except Exception as exc:
                    fut.set_exception(exc)
                finally:
                    _inflight.pop(key, None)

            task = asyncio.create_task(_runner())
            _runner_tasks.add(task)
            task.add_done_callback(_runner_tasks.discard)

    return await fut
