"""Shared regular expressions for provenance and citation handling."""

from __future__ import annotations

import re

BIBCODE_RE = re.compile(r"\b(\d{4}[A-Za-z][A-Za-z&.]+\.+\S+)")
AUTHOR_YEAR_RE = re.compile(
    r"\b([A-Z][a-zA-Z]+)(?:\s+et\s+al\.?)?\s*[\(\[]?(\d{4})[\)\]]?(?=\W|$)"
)
IVOID_RE = re.compile(r"ivo://[\w./+-]+")
