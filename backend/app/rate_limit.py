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


import time as _time


class DailyQuota:
    """Per-user daily API quota tracking with in-memory storage."""

    TIER_LIMITS = {
        "solo": {"api_calls": 1000, "pipeline_runs": 50, "adql_queries": 200},
        "lab": {"api_calls": 5000, "pipeline_runs": 200, "adql_queries": 1000},
        "institution": {"api_calls": 20000, "pipeline_runs": 1000, "adql_queries": 5000},
    }

    def __init__(self):
        self._usage: dict[str, dict[str, int]] = {}  # user_id -> {resource: count}
        self._reset_day: dict[str, int] = {}  # user_id -> day number

    def _today(self) -> int:
        return int(_time.time()) // 86400

    def check_and_increment(self, user_id: str, tier: str, resource: str) -> dict:
        """Check quota and increment usage. Returns dict with allowed/remaining."""
        today = self._today()

        # Reset on new day
        if self._reset_day.get(user_id) != today:
            self._usage[user_id] = {}
            self._reset_day[user_id] = today

        limits = self.TIER_LIMITS.get(tier, self.TIER_LIMITS["solo"])
        limit = limits.get(resource, 1000)

        current = self._usage.get(user_id, {}).get(resource, 0)

        if current >= limit:
            return {"allowed": False, "current": current, "limit": limit, "resource": resource}

        if user_id not in self._usage:
            self._usage[user_id] = {}
        self._usage[user_id][resource] = current + 1

        return {"allowed": True, "current": current + 1, "limit": limit, "remaining": limit - current - 1}

    def get_usage(self, user_id: str) -> dict:
        today = self._today()
        if self._reset_day.get(user_id) != today:
            return {}
        return dict(self._usage.get(user_id, {}))


daily_quota = DailyQuota()
