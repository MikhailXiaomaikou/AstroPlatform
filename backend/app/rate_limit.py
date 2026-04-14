import os

from slowapi import Limiter
from starlette.requests import Request

# Disable rate limiting in test/dev environments
_enabled = os.getenv("RATE_LIMIT_ENABLED", "true").lower() != "false"


def get_rate_limit_key(request: Request) -> str:
    """Use user ID from JWT if available, otherwise fall back to IP."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            from jose import jwt

            from app.config import settings

            payload = jwt.decode(
                token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
            )
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
        except Exception:
            pass
    # Fallback to IP
    return request.client.host if request.client else "unknown"


limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=["100/minute"],
    enabled=_enabled,
)


import logging
import time as _time

_quota_logger = logging.getLogger(__name__)


def _get_quota_redis():
    """Return a sync Redis connection for quota tracking, or None."""
    try:
        from app.config import settings

        if not settings.redis_url:
            return None
        import redis

        kwargs: dict = {"decode_responses": True, "socket_connect_timeout": 2, "socket_timeout": 2}
        if settings.redis_ssl:
            kwargs["ssl_cert_reqs"] = "none"
        return redis.Redis.from_url(settings.redis_url, **kwargs)
    except Exception:
        return None


class DailyQuota:
    """Per-user daily API quota tracking. Uses Redis when available, falls back to in-memory."""

    TIER_LIMITS = {
        "solo": {"api_calls": 1000, "pipeline_runs": 50, "adql_queries": 200},
        "lab": {"api_calls": 5000, "pipeline_runs": 200, "adql_queries": 1000},
        "institution": {"api_calls": 20000, "pipeline_runs": 1000, "adql_queries": 5000},
    }

    def __init__(self):
        # In-memory fallback
        self._usage: dict[str, dict[str, int]] = {}
        self._reset_day: dict[str, int] = {}

    def _today(self) -> int:
        return int(_time.time()) // 86400

    def _redis_key(self, user_id: str) -> str:
        return f"astro:quota:{user_id}:{self._today()}"

    def check_and_increment(self, user_id: str, tier: str, resource: str) -> dict:
        """Check quota and increment usage. Returns dict with allowed/remaining."""
        limits = self.TIER_LIMITS.get(tier, self.TIER_LIMITS["solo"])
        limit = limits.get(resource, 1000)

        # Try Redis first
        r = _get_quota_redis()
        if r is not None:
            try:
                key = self._redis_key(user_id)
                current = int(r.hincrby(key, resource, 1))
                r.expire(key, 86400)
                r.close()
                if current > limit:
                    return {"allowed": False, "current": current, "limit": limit, "resource": resource}
                return {"allowed": True, "current": current, "limit": limit, "remaining": limit - current}
            except Exception as e:
                _quota_logger.debug("Redis quota failed, using in-memory: %s", e)

        # In-memory fallback
        today = self._today()
        if self._reset_day.get(user_id) != today:
            self._usage[user_id] = {}
            self._reset_day[user_id] = today

        current = self._usage.get(user_id, {}).get(resource, 0)
        if current >= limit:
            return {"allowed": False, "current": current, "limit": limit, "resource": resource}

        if user_id not in self._usage:
            self._usage[user_id] = {}
        self._usage[user_id][resource] = current + 1

        return {"allowed": True, "current": current + 1, "limit": limit, "remaining": limit - current - 1}

    def get_usage(self, user_id: str) -> dict:
        # Try Redis first
        r = _get_quota_redis()
        if r is not None:
            try:
                key = self._redis_key(user_id)
                data = r.hgetall(key)
                r.close()
                return {k: int(v) for k, v in data.items()}
            except Exception:
                pass

        today = self._today()
        if self._reset_day.get(user_id) != today:
            return {}
        return dict(self._usage.get(user_id, {}))


daily_quota = DailyQuota()
