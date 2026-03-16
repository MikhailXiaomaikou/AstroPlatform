import hashlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None

async def get_redis() -> aioredis.Redis | None:
    global _redis
    if _redis is None:
        try:
            import os
            url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            _redis = aioredis.from_url(url, decode_responses=True)
            await _redis.ping()
        except Exception as e:
            logger.warning("Redis cache unavailable: %s", e)
            _redis = None
    return _redis

def cache_key(prefix: str, **kwargs) -> str:
    raw = json.dumps(kwargs, sort_keys=True)
    h = hashlib.md5(raw.encode()).hexdigest()
    return f"astro:{prefix}:{h}"

async def cache_get(key: str) -> Any | None:
    r = await get_redis()
    if r is None:
        return None
    try:
        val = await r.get(key)
        return json.loads(val) if val else None
    except Exception:
        return None

async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    r = await get_redis()
    if r is None:
        return
    try:
        await r.setex(key, ttl, json.dumps(value))
    except Exception:
        pass
