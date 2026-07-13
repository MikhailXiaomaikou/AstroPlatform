import logging
import os
import time as _time

from slowapi import Limiter
from starlette.requests import Request

_quota_logger = logging.getLogger(__name__)

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
    # Fallback to client IP, derived through the explicit TRUSTED_PROXY_MODE
    # trust chain (see get_client_ip). The default trusts only the socket peer.
    return f"ip:{get_client_ip(request) or 'unknown'}"


def _parse_trusted_proxy_mode(raw: str) -> int | str:
    """Normalize proxy mode to "none", "render", "cloudflare", or hops.

    Anything unrecognized fails closed to "none" (trust no headers).
    """
    value = (raw or "").strip().lower()
    if value in ("", "none", "0"):
        return "none"
    if value in {"render", "cloudflare"}:
        return value
    try:
        hops = int(value)
    except ValueError:
        hops = 0
    if hops >= 1:
        return hops
    _quota_logger.warning(
        "Invalid TRUSTED_PROXY_MODE; trusting only the socket peer"
    )
    return "none"


def get_client_ip(request: Request) -> str | None:
    """Client IP for anonymous rate limits and audit fields.

    Forwarded headers are attacker-controlled on a direct connection, so
    which one (if any) to believe is decided by settings.trusted_proxy_mode
    (env TRUSTED_PROXY_MODE, documented in app/config.py):

    - "none": trust only the transport peer (request.client.host).
    - "render": trust the first non-empty X-Forwarded-For entry. Render's
      edge places the original client there; this mode must be an explicit
      deployment choice after validating that the service is edge-only.
    - N >= 1 trusted reverse proxies: each trusted proxy appends the peer it
      accepted to X-Forwarded-For, so the last N entries are trusted and the
      real client is the Nth from the right.
    - "cloudflare": trust CF-Connecting-IP, which Cloudflare sets on every
      proxied request.

    When the expected trusted header is missing (or the X-Forwarded-For chain
    is shorter than the trusted hop count), fall back to the socket peer —
    never to another header.
    """
    from app.config import settings

    mode = _parse_trusted_proxy_mode(settings.trusted_proxy_mode)

    if mode == "render":
        hops = [
            part.strip()
            for part in request.headers.get("X-Forwarded-For", "").split(",")
            if part.strip()
        ]
        if hops:
            return hops[0][:64]
    elif mode == "cloudflare":
        value = request.headers.get("CF-Connecting-IP", "").strip()
        if value:
            return value[:64]
    elif isinstance(mode, int):
        hops = [
            part.strip()
            for part in request.headers.get("X-Forwarded-For", "").split(",")
            if part.strip()
        ]
        if len(hops) >= mode:
            return hops[-mode][:64]

    if request.client:
        return request.client.host[:64]
    return None


limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=["100/minute"],
    enabled=_enabled,
)


def _get_quota_redis():
    """Return a sync Redis connection for quota tracking, or None."""
    try:
        from app.config import settings

        if not settings.redis_url:
            return None
        import redis

        kwargs: dict = {"decode_responses": True, "socket_connect_timeout": 2, "socket_timeout": 2}
        kwargs.update(settings.redis_tls_kwargs())
        return redis.Redis.from_url(settings.redis_url, **kwargs)
    except Exception:
        return None


class DailyQuota:
    """Daily API quota tracking for users or anonymous client buckets.

    Redis is the durable, multi-process authority when it is available. Local
    development may fall back to the in-process counters; hosted callers can
    set ``require_durable`` so a Redis outage cannot silently disable a
    platform-funded quota.
    """

    TIER_LIMITS = {
        # "starter" is the default tier for new self-service signups
        # (decision 2B, 2026-07): a tight daily cap on platform-funded chat
        # calls so a fresh anonymous registration cannot burn the shared
        # server DeepSeek key. Enforced at the chat endpoints via
        # app/api/chat.py:_enforce_starter_daily_quota, which exempts BYOK
        # calls. pipeline_runs / adql_queries spend no platform LLM money,
        # so they keep the solo allowances.
        "anonymous": {"api_calls": 50},
        "starter": {"api_calls": 50, "pipeline_runs": 50, "adql_queries": 200},
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

    def check_and_increment(
        self,
        user_id: str,
        tier: str,
        resource: str,
        *,
        require_durable: bool = False,
    ) -> dict:
        """Check quota and increment usage.

        ``user_id`` is a quota subject, not necessarily a database user ID;
        anonymous chat passes a namespaced client-IP bucket. When
        ``require_durable`` is true, lack of a working Redis backend returns a
        fail-closed verdict instead of using process-local memory.
        """
        limits = self.TIER_LIMITS.get(tier, self.TIER_LIMITS["solo"])
        limit = limits.get(resource, 1000)

        # Try Redis first
        r = _get_quota_redis()
        if r is not None:
            try:
                key = self._redis_key(user_id)
                current = int(r.hincrby(key, resource, 1))
                r.expire(key, 86400)
                if current > limit:
                    return {"allowed": False, "current": current, "limit": limit, "resource": resource}
                return {"allowed": True, "current": current, "limit": limit, "remaining": limit - current}
            except Exception as e:
                if require_durable:
                    _quota_logger.error(
                        "Durable daily-quota backend unavailable; rejecting request"
                    )
                    return {
                        "allowed": False,
                        "backend_unavailable": True,
                        "limit": limit,
                        "resource": resource,
                    }
                _quota_logger.debug("Redis quota failed, using in-memory: %s", e)
            finally:
                try:
                    r.close()
                except Exception:
                    pass
        elif require_durable:
            _quota_logger.error(
                "Durable daily-quota backend is not configured; rejecting request"
            )
            return {
                "allowed": False,
                "backend_unavailable": True,
                "limit": limit,
                "resource": resource,
            }

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
