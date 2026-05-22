"""Cross-process JSON KV store for transient session/job state.

Used by ``workflow_checkpoint``, ``cosmology_mcmc._JOBS``, and the upcoming
``async_tool_runtime`` so they can survive process restart (SQLite) and be
visible across web + Celery workers (Redis).

Mirrors the auto-select pattern from ``connector_cache.py`` (Redis →
SQLite → Null) but stores **JSON-serialisable payloads** instead of
pickle blobs, so there is no class allowlist to maintain. Callers must
hand in dict / list / primitive payloads; anything else is dropped with
a warning.

Selection is controlled by the same ``connector_cache_backend`` setting
as the raw connector cache: one knob keeps configuration small. If we
ever need to disable just one of them, split the setting then.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class _NullBackend:
    name = "null"

    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str, ttl: int) -> None:
        return None

    def delete(self, key: str) -> None:
        return None

    def scan_keys(self, prefix: str) -> list[str]:
        return []


class _InMemoryBackend:
    """Process-local dict backend for tests. Not used in production paths."""

    name = "memory"

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}

    def get(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at < time.time():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str, ttl: int) -> None:
        self._data[key] = (value, time.time() + ttl)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def scan_keys(self, prefix: str) -> list[str]:
        now = time.time()
        return [
            k for k, (_v, expires_at) in self._data.items()
            if k.startswith(prefix) and expires_at >= now
        ]


class _SqliteBackend:
    name = "sqlite"

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.path)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS kv_store "
                "(key TEXT PRIMARY KEY, value TEXT, expires_at REAL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_kv_expires ON kv_store(expires_at)"
            )

    def get(self, key: str) -> str | None:
        try:
            with sqlite3.connect(str(self.path)) as db:
                row = db.execute(
                    "SELECT value, expires_at FROM kv_store WHERE key = ?",
                    (key,),
                ).fetchone()
            if not row:
                return None
            value, expires_at = row
            if expires_at < time.time():
                return None
            return value
        except sqlite3.Error as e:
            logger.debug("kv_store sqlite get failed: %s", e)
            return None

    def set(self, key: str, value: str, ttl: int) -> None:
        try:
            with sqlite3.connect(str(self.path)) as db:
                db.execute(
                    "INSERT OR REPLACE INTO kv_store (key, value, expires_at) "
                    "VALUES (?, ?, ?)",
                    (key, value, time.time() + ttl),
                )
                # Opportunistic eviction of a handful of expired rows
                db.execute(
                    "DELETE FROM kv_store WHERE expires_at < ? "
                    "AND rowid IN (SELECT rowid FROM kv_store "
                    "WHERE expires_at < ? LIMIT 100)",
                    (time.time(), time.time()),
                )
        except sqlite3.Error as e:
            logger.debug("kv_store sqlite set failed: %s", e)

    def delete(self, key: str) -> None:
        try:
            with sqlite3.connect(str(self.path)) as db:
                db.execute("DELETE FROM kv_store WHERE key = ?", (key,))
        except sqlite3.Error as e:
            logger.debug("kv_store sqlite delete failed: %s", e)

    def scan_keys(self, prefix: str) -> list[str]:
        try:
            with sqlite3.connect(str(self.path)) as db:
                rows = db.execute(
                    "SELECT key FROM kv_store WHERE key LIKE ? AND expires_at >= ?",
                    (prefix + "%", time.time()),
                ).fetchall()
            return [r[0] for r in rows]
        except sqlite3.Error:
            return []


class _RedisBackend:
    name = "redis"

    def __init__(self, url: str):
        import redis as _redis

        kwargs: dict = {"socket_connect_timeout": 2, "socket_timeout": 2}
        if url.startswith("rediss://"):
            kwargs.update(settings.redis_tls_kwargs())
        self._client = _redis.Redis.from_url(url, **kwargs)
        self._client.ping()

    def get(self, key: str) -> str | None:
        try:
            raw = self._client.get(key)
            if raw is None:
                return None
            return raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        except Exception as e:
            logger.debug("kv_store redis get failed: %s", e)
            return None

    def set(self, key: str, value: str, ttl: int) -> None:
        try:
            self._client.setex(key, ttl, value)
        except Exception as e:
            logger.debug("kv_store redis set failed: %s", e)

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except Exception as e:
            logger.debug("kv_store redis delete failed: %s", e)

    def scan_keys(self, prefix: str) -> list[str]:
        try:
            return [
                k.decode("utf-8") if isinstance(k, (bytes, bytearray)) else k
                for k in self._client.scan_iter(match=prefix + "*")
            ]
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Backend selection (lazy, shared with connector_cache config knob)
# ---------------------------------------------------------------------------
_backend: _NullBackend | _SqliteBackend | _RedisBackend | None = None


def _get_backend():
    global _backend
    if _backend is not None:
        return _backend

    backend_choice = getattr(settings, "connector_cache_backend", "auto")

    if backend_choice == "null":
        _backend = _NullBackend()
        return _backend

    if backend_choice in ("auto", "redis"):
        try:
            _backend = _RedisBackend(settings.redis_url)
            logger.info("kv_store: using Redis backend")
            return _backend
        except Exception as e:
            if backend_choice == "redis":
                logger.warning(
                    "kv_store: Redis requested but unavailable (%s); using null", e
                )
                _backend = _NullBackend()
                return _backend
            logger.debug(
                "kv_store: Redis unavailable (%s); falling back to SQLite", e
            )

    if backend_choice in ("auto", "sqlite"):
        try:
            db_path = Path(settings.local_storage_dir).parent / "kv_store.db"
            _backend = _SqliteBackend(db_path)
            logger.info("kv_store: using SQLite backend at %s", db_path)
            return _backend
        except Exception as e:
            logger.warning("kv_store: SQLite init failed (%s); disabling KV", e)

    _backend = _NullBackend()
    return _backend


def reset_backend() -> None:
    """Drop the cached backend instance (test helper)."""
    global _backend
    _backend = None


def use_memory_backend_for_testing() -> _InMemoryBackend:
    """Force the singleton to a process-local in-memory backend.

    Test-only helper. Lets unit tests exercise the real ``JsonKvStore``
    code paths (serialise / TTL / scan_keys) without hitting Redis or
    writing to the filesystem.
    """
    global _backend
    _backend = _InMemoryBackend()
    return _backend


# ---------------------------------------------------------------------------
# Public API: namespaced JSON store
# ---------------------------------------------------------------------------


def _jsonable_fallback(obj: Any) -> Any:
    """``json.dumps`` ``default`` hook that handles numpy scalars / arrays.

    Astronomy services return numpy types everywhere (sampler diagnostics,
    parameter summaries, etc.). Stringifying numpy floats would silently
    lose precision, so coerce to native Python types instead. Anything we
    still don't recognise falls back to ``str(obj)``.
    """
    try:
        import numpy as _np  # lazy: keep this module free of heavy imports at import time
    except ImportError:  # pragma: no cover — numpy is a hard backend dep
        return str(obj)
    if isinstance(obj, _np.integer):
        return int(obj)
    if isinstance(obj, _np.floating):
        return float(obj)
    if isinstance(obj, _np.bool_):
        return bool(obj)
    if isinstance(obj, _np.ndarray):
        return obj.tolist()
    return str(obj)


class JsonKvStore:
    """A namespaced KV store with JSON serialisation.

    Each instance binds to a fixed namespace; keys are stored as
    ``{namespace}:{key}`` so multiple services can share the same backend
    without colliding (workflow checkpoints, async jobs, etc.).
    """

    def __init__(self, namespace: str):
        if not namespace or ":" in namespace:
            raise ValueError(
                f"JsonKvStore namespace must be non-empty and contain no ':' "
                f"(got {namespace!r})"
            )
        self.namespace = namespace

    def _full_key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def get(self, key: str, default: Any = None) -> Any:
        raw = _get_backend().get(self._full_key(key))
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            logger.debug("kv_store: JSON decode failed for %s: %s", key, e)
            return default

    def set(self, key: str, value: Any, ttl: int) -> bool:
        try:
            payload = json.dumps(value, default=_jsonable_fallback)
        except (TypeError, ValueError) as e:
            logger.warning(
                "kv_store: value not JSON-serialisable for key %s (%s)", key, e
            )
            return False
        _get_backend().set(self._full_key(key), payload, ttl)
        return True

    def delete(self, key: str) -> None:
        _get_backend().delete(self._full_key(key))

    def scan_keys(self) -> list[str]:
        prefix = f"{self.namespace}:"
        keys = _get_backend().scan_keys(prefix)
        return [k[len(prefix):] for k in keys if k.startswith(prefix)]

    def clear_namespace(self) -> None:
        """Delete every key in this namespace. Test helper / admin tool."""
        for key in self.scan_keys():
            self.delete(key)
