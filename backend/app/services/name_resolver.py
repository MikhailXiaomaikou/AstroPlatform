"""Robust astronomical name resolution with archive fallbacks."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx


@dataclass
class ResolvedName:
    input_name: str
    canonical_name: str
    ra: float | None
    dec: float | None
    resolver: str
    aliases_tried: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.ra is not None and self.dec is not None


def candidate_names(name: str) -> list[str]:
    """Return likely archive spellings for non-standard astronomy names."""
    raw = " ".join(str(name).strip().split())
    if not raw:
        return []
    candidates = [raw]

    patterns = [
        (r"^(SDSS)J(\d{4}[+-]\d{4}.*)$", r"\1 J\2"),
        (r"^(RX)J(\d{4}[+-]\d{4}.*)$", r"\1 J\2"),
        (r"^(CXO)J(\d{4}[+-]\d{4}.*)$", r"\1 J\2"),
        (r"^(2MASS)J(\d{4}.*)$", r"\1 J\2"),
        (r"^(WISE)J(\d{4}.*)$", r"\1 J\2"),
        (r"^(NaSt)\s*(\d+)$", r"\1 \2"),
    ]
    for pattern, repl in patterns:
        normalized = re.sub(pattern, repl, raw, flags=re.IGNORECASE)
        if normalized != raw:
            candidates.append(normalized)

    compact = raw.replace(" ", "")
    if compact != raw:
        candidates.append(compact)
    if raw.lower().startswith("rxj"):
        candidates.append("RX " + raw[2:])
    if raw.lower().startswith("sdssj"):
        candidates.append("SDSS " + raw[4:])

    seen: set[str] = set()
    unique: list[str] = []
    for item in candidates:
        if item and item not in seen:
            unique.append(item)
            seen.add(item)
    return unique


def _hms_to_deg(hours: float, minutes: float = 0.0, seconds: float = 0.0) -> float:
    return (hours + minutes / 60.0 + seconds / 3600.0) * 15.0


def _parse_coordinate_name(name: str) -> tuple[float, float] | None:
    """Parse common JHHMM+DDMM or coarse BHHMM+DDD names."""
    cleaned = name.replace(" ", "")
    match = re.search(r"J(\d{2})(\d{2})(\d{0,4})([+-])(\d{2})(\d{2})(\d{0,4})", cleaned, re.I)
    if match:
        hh, mm, ss_raw, sign, dd, dm, ds_raw = match.groups()
        ss = float(ss_raw[:2] + "." + ss_raw[2:]) if ss_raw else 0.0
        ds = float(ds_raw[:2] + "." + ds_raw[2:]) if ds_raw else 0.0
        ra = _hms_to_deg(float(hh), float(mm), ss)
        dec = float(dd) + float(dm) / 60.0 + ds / 3600.0
        if sign == "-":
            dec = -dec
        return ra, dec

    bmatch = re.match(r"B(\d{2})(\d{2})([+-])(\d{2})(\d)", cleaned, re.I)
    if bmatch:
        hh, mm, sign, dd, tenth = bmatch.groups()
        ra = _hms_to_deg(float(hh), float(mm), 0.0)
        dec = float(dd) + float(tenth) / 10.0
        if sign == "-":
            dec = -dec
        return ra, dec
    return None


def _parse_sesame_xml(text: str) -> tuple[str | None, float | None, float | None]:
    root = ElementTree.fromstring(text)
    oname = root.findtext(".//oname")
    ra_text = root.findtext(".//jradeg")
    dec_text = root.findtext(".//jdedeg")
    if ra_text is None or dec_text is None:
        return oname, None, None
    return oname, float(ra_text), float(dec_text)


async def resolve_name(name: str, timeout: float = 8.0) -> ResolvedName:
    """Resolve an object name using Sesame, then NED, then coordinate syntax."""
    aliases = candidate_names(name)
    warnings: list[str] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for alias in aliases:
            try:
                url = f"https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?{quote_plus(alias)}"
                resp = await client.get(url)
                resp.raise_for_status()
                canonical, ra, dec = _parse_sesame_xml(resp.text)
                if ra is not None and dec is not None:
                    return ResolvedName(name, canonical or alias, ra, dec, "sesame", aliases, warnings)
            except (httpx.HTTPError, ElementTree.ParseError, ValueError) as exc:
                warnings.append(f"Sesame failed for {alias}: {exc}")

        for alias in aliases:
            try:
                resp = await client.get(
                    "https://ned.ipac.caltech.edu/byname",
                    params={"objname": alias, "of": "xml_main"},
                )
                resp.raise_for_status()
                text = resp.text
                ra_match = re.search(r"<RA[^>]*>([-+]?\d+(?:\.\d+)?)</RA>", text)
                dec_match = re.search(r"<DEC[^>]*>([-+]?\d+(?:\.\d+)?)</DEC>", text)
                if ra_match and dec_match:
                    return ResolvedName(
                        name, alias,
                        float(ra_match.group(1)), float(dec_match.group(1)),
                        "ned", aliases, warnings,
                    )
            except (httpx.HTTPError, ValueError) as exc:
                warnings.append(f"NED failed for {alias}: {exc}")

    for alias in aliases:
        parsed = _parse_coordinate_name(alias)
        if parsed is None:
            continue
        ra, dec = parsed
        if math.isfinite(ra) and math.isfinite(dec):
            warnings.append("Coordinates were inferred from the object name; verify before precision work.")
            return ResolvedName(name, alias, ra, dec, "coordinate_name", aliases, warnings)

    return ResolvedName(name, aliases[0] if aliases else name, None, None, "unresolved", aliases, warnings)
