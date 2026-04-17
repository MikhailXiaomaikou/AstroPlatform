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
            raw = match.group(2)
            # M3: translate glob-style '*' into SQL '%' and keep LIKE, rather
            # than collapsing to '=' which would never match a prefix (SIMBAD
            # stores compact tokens like 'G*', 'SB*', 'EB*' — dropping the
            # wildcard turned a valid prefix query into a zero-row query).
            if "*" in raw:
                translated = raw.replace("*", "%")
                warnings.append(
                    "SIMBAD otype wildcard '*' rewritten to SQL '%' for LIKE compatibility."
                )
                return f"otype LIKE '{translated}'"
            # No wildcard: a plain equality is what the caller meant.
            value = sanitize_simbad_otype(raw)
            return f"otype = '{value}'"

        rewritten = like_pattern.sub(_replace_like, rewritten)
        if re.search(r"\bflux_[BVRIJHK]\b|\bFe_H_Fe_H\b", rewritten, flags=re.IGNORECASE):
            warnings.append(
                "SIMBAD basic table does not expose flux_B/V/R/I/J/H/K or Fe_H_Fe_H; use joined tables or VizieR."
            )

    if service_l == "vizier":
        # M4: VizieR table names with slashes need double-quoting in ADQL.
        # Use re.sub with a callback instead of str.replace so we only
        # rewrite the matched FROM clause — str.replace would otherwise
        # touch the name wherever it appeared (comments, string literals).
        table_pattern = re.compile(
            r'\bFROM\s+(["\']?)(\w+/\w+/\w+)\1',
            flags=re.IGNORECASE,
        )
        rewrote_table = {"flag": False, "name": ""}

        def _quote_table(match: re.Match) -> str:
            if match.group(1):  # already quoted
                return match.group(0)
            rewrote_table["flag"] = True
            rewrote_table["name"] = match.group(2)
            return f'FROM "{match.group(2)}"'

        rewritten = table_pattern.sub(_quote_table, rewritten)
        if rewrote_table["flag"]:
            warnings.append(
                f"VizieR table name '{rewrote_table['name']}' auto-quoted for ADQL compatibility."
            )

    if not re.search(r"\bSELECT\s+TOP\s+\d+", rewritten, flags=re.IGNORECASE):
        warnings.append("Add SELECT TOP N for interactive AI queries to avoid large TAP jobs.")

    return ADQLDialectResult(service_l, query, rewritten, warnings, errors)
