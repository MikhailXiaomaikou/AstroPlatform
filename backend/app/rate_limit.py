import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# Disable rate limiting in test/dev environments
_enabled = os.getenv("RATE_LIMIT_ENABLED", "true").lower() != "false"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
    enabled=_enabled,
)
