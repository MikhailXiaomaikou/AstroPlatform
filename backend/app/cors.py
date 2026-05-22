"""CORS origin configuration.

Reads allowed origins from the CORS_ORIGINS environment variable (comma-separated).
Defaults include localhost dev servers and known production domains.
Never falls back to wildcard "*" in production — that would allow any origin.
"""

import os


def get_cors_origins() -> list[str]:
    """Return the list of allowed CORS origins."""
    raw = os.getenv("CORS_ORIGINS", "")
    env = os.getenv("ENV", "dev")

    origins = []
    if raw.strip():
        origins = [origin.strip() for origin in raw.split(",") if origin.strip()]

    # N'-1: when desktop admin HTML is opened directly the browser sends
    # `Origin: null`. Always allow "null" — admin endpoints are still gated
    # by the X-Admin-Secret header, CORS is not the auth layer.
    if "null" not in origins:
        origins.append("null")

    # In production, only use explicitly configured origins (plus null for desktop tools)
    if env == "production":
        return origins

    # Dev/test: include common dev and staging origins
    defaults = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://astro-frontend-tyfr.onrender.com",
        "https://astro-frontend.onrender.com",
        "https://astro-backend-h4x1.onrender.com",
        "https://standard-astro-frontend-tyfr.onrender.com",
        "https://standard-astro-frontend.onrender.com",
        "https://astroplatform.pages.dev",
    ]
    for d in defaults:
        if d not in origins:
            origins.append(d)

    return origins
