"""Helpers for normalizing and deriving usernames."""

from __future__ import annotations

import re
import unicodedata

_USERNAME_CLEAN_RE = re.compile(r"[^a-z0-9._-]+")
_EDGE_SEPARATOR_RE = re.compile(r"^[._-]+|[._-]+$")
_MULTI_SEPARATOR_RE = re.compile(r"[._-]{2,}")

INTERNAL_EMAIL_DOMAIN = "users.standardastro.app"


def normalize_username(value: str) -> str:
    """Return a lowercase ASCII username candidate."""
    if not value:
        return ""

    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.strip().lower().replace("@", " ")
    ascii_value = re.sub(r"\s+", "_", ascii_value)
    ascii_value = _USERNAME_CLEAN_RE.sub("", ascii_value)
    ascii_value = _MULTI_SEPARATOR_RE.sub("_", ascii_value)
    ascii_value = _EDGE_SEPARATOR_RE.sub("", ascii_value)
    return ascii_value[:32]


def username_from_email(email: str) -> str:
    """Derive a username candidate from an email address."""
    local_part = email.split("@", 1)[0]
    return normalize_username(local_part)


def internal_email_for_username(username: str) -> str:
    """Generate an internal email address for username-based accounts."""
    return f"{username}@{INTERNAL_EMAIL_DOMAIN}"


def preferred_username(*candidates: str, fallback: str = "user") -> str:
    """Pick the first usable normalized username candidate."""
    for candidate in candidates:
        normalized = normalize_username(candidate or "")
        if normalized:
            return normalized
    return normalize_username(fallback) or "user"
