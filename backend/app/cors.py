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

    # Production trusts only explicit origins. A downloaded file:// admin page
    # sends Origin: null; an operator who still needs that legacy workflow must
    # opt in with CORS_ORIGINS=...,null. Automatically allowing every opaque
    # origin would also expose anonymous/shared-key endpoints to sandboxed
    # documents, where CORS is part of the browser abuse boundary.
    if env.strip().lower() in {"prod", "production"}:
        return origins

    # Dev/test: include common dev and staging origins
    defaults = [
        "null",
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
