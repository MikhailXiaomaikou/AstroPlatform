"""Shared regular expressions for provenance and citation handling."""

from __future__ import annotations

import re

BIBCODE_RE = re.compile(r"\b(\d{4}[A-Za-z][A-Za-z&.]+\.+\S+)")
ARXIV_ID_RE = re.compile(r"\barXiv:\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?|[a-z-]+/[0-9]{7}(?:v\d+)?)\b", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
AUTHOR_YEAR_RE = re.compile(
    r"\b([A-Z][a-zA-Z]+)(?:\s+et\s+al\.?)?\s*[\(\[]?((?:1[5-9]|20|21)\d{2})[\)\]]?(?=\W|$)"
)
IVOID_RE = re.compile(r"ivo://[\w./+-]+")
