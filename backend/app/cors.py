"""CORS origin configuration.

Reads allowed origins from the CORS_ORIGINS environment variable (comma-separated).
Defaults to ["http://localhost:5173"] when ENV is not set or is "dev".
"""

import os


def get_cors_origins() -> list[str]:
    """Return the list of allowed CORS origins."""
    env = os.getenv("ENV", "dev")
    raw = os.getenv("CORS_ORIGINS", "")

    origins = []
    if raw.strip():
        origins = [origin.strip() for origin in raw.split(",") if origin.strip()]

    # Always include common origins
    defaults = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://astro-frontend-tyfr.onrender.com",
        "https://astroplatform.pages.dev",
    ]
    for d in defaults:
        if d not in origins:
            origins.append(d)

    return origins if origins else ["*"]
