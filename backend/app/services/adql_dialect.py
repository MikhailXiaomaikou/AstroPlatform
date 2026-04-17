"""Small ADQL dialect compatibility layer for archive-specific quirks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ADQLDialectResult:
    service: str
    query: str
    rewritten_query: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def sanitize_simbad_otype(value: str) -> str:
    """SIMBAD object types are compact tokens; strip SQL wildcards safely."""
    cleaned = re.sub(r"[^a-zA-Z0-9*]", "", value or "")
    return cleaned.rstrip("*")


def normalize_adql(query: str, service: str) -> ADQLDialectResult:
    """Rewrite common non-portable ADQL fragments and report risks."""
    service_l = (service or "").strip().lower()
    rewritten = query or ""
    warnings: list[str] = []
    errors: list[str] = []

    if service_l == "sdss":
        errors.append("SDSS SkyServer does not support ADQL TAP; use search_objects(sources=['sdss']).")
        return ADQLDialectResult(service_l, query, rewritten, warnings, errors)

    if service_l == "simbad":
        like_pattern = re.compile(
            r"\botype\s+LIKE\s+(['\"])([^'\"]+)\1",
            flags=re.IGNORECASE,
        )

        def _replace_like(match: re.Match) -> str:
            value = sanitize_simbad_otype(match.group(2))
            warnings.append(
                "SIMBAD TAP may reject LIKE on otype; rewritten to an exact otype predicate."
            )
            return f"otype = '{value}'"

        rewritten = like_pattern.sub(_replace_like, rewritten)
        if re.search(r"\bflux_[BVRIJHK]\b|\bFe_H_Fe_H\b", rewritten, flags=re.IGNORECASE):
            warnings.append(
                "SIMBAD basic table does not expose flux_B/V/R/I/J/H/K or Fe_H_Fe_H; use joined tables or VizieR."
            )

    if service_l == "vizier":
        # VizieR table names with slashes need double-quoting in ADQL
        table_pattern = re.compile(r'\bFROM\s+(["\']?)(\w+/\w+/\w+)\1', flags=re.IGNORECASE)
        match = table_pattern.search(rewritten)
        if match and not match.group(1):
            old = match.group(2)
            rewritten = rewritten.replace(f"FROM {old}", f'FROM "{old}"')
            warnings.append(f"VizieR table name '{old}' auto-quoted for ADQL compatibility.")

    if not re.search(r"\bSELECT\s+TOP\s+\d+", rewritten, flags=re.IGNORECASE):
        warnings.append("Add SELECT TOP N for interactive AI queries to avoid large TAP jobs.")

    return ADQLDialectResult(service_l, query, rewritten, warnings, errors)
