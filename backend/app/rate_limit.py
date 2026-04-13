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
