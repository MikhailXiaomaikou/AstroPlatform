"""CORS origin configuration.

Reads allowed origins from the CORS_ORIGINS environment variable (comma-separated).
Defaults to ["http://localhost:5173"] when ENV is not set or is "dev".
"""

import os


def get_cors_origins() -> list[str]:
    """Return the list of allowed CORS origins."""
    env = os.getenv("ENV", "dev")
    raw = os.getenv("CORS_ORIGINS", "")

    if raw.strip():
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    # Default for dev mode
    if env == "dev":
        return ["http://localhost:5173", "http://127.0.0.1:5173"]

    # In production with no CORS_ORIGINS set, return empty (no origins allowed)
    return []
