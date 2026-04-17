import hashlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None

async def get_redis() -> aioredis.Redis | None:
    global _redis
    if _redis is None:
        try:
            from app.config import settings
            kwargs: dict = {"decode_responses": True}
            kwargs.update(settings.redis_tls_kwargs())
            _redis = aioredis.from_url(settings.redis_url, **kwargs)
            await _redis.ping()
        except (RedisError, ConnectionError, OSError) as e:
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
    except (RedisError, ConnectionError, json.JSONDecodeError) as e:
        logger.debug("cache_get failed for key %s: %s", key, e)
        return None

async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    r = await get_redis()
    if r is None:
        return
    try:
        await r.setex(key, ttl, json.dumps(value))
    except (RedisError, ConnectionError, TypeError) as e:
        logger.debug("cache_set failed for key %s: %s", key, e)
