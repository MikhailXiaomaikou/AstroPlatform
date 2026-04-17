"""Robust astronomical name resolution with archive fallbacks."""

from __future__ import annotations

import asyncio
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


async def _resolve_name_impl(name: str, per_request_timeout: float) -> "ResolvedName":
    """Actual resolver body, split out so the global timeout wrapper can cancel it."""
    aliases = candidate_names(name)
    warnings: list[str] = []
    async with httpx.AsyncClient(timeout=per_request_timeout, follow_redirects=True) as client:
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
                # M2: validate the response looks like a real NED record.  We
                # parse with ElementTree (same as Sesame) so a malformed HTML
                # error page cannot slip through the loose regex and yield a
                # spurious (0, 0) hit.
                try:
                    tree = ElementTree.fromstring(text)
                    ra_text = tree.findtext(".//RA")
                    dec_text = tree.findtext(".//DEC")
                    if ra_text is None or dec_text is None:
                        raise ValueError("NED response missing RA/DEC elements")
                    ra_val = float(ra_text)
                    dec_val = float(dec_text)
                    if ra_val == 0.0 and dec_val == 0.0:
                        raise ValueError("NED returned (0, 0); likely an error page")
                    return ResolvedName(
                        name, alias, ra_val, dec_val,
                        "ned", aliases, warnings,
                    )
                except (ElementTree.ParseError, ValueError) as parse_exc:
                    warnings.append(f"NED unparseable for {alias}: {parse_exc}")
            except (httpx.HTTPError, ValueError) as exc:
                warnings.append(f"NED failed for {alias}: {exc}")

    for alias in aliases:
        parsed = _parse_coordinate_name(alias)
        if parsed is None:
            continue
        ra, dec = parsed
        # M5: reject parses that produced nonsensical coordinates before
        # returning them as a user-visible result.
        if not (math.isfinite(ra) and math.isfinite(dec)):
            continue
        if not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0):
            warnings.append(f"Parsed coords for {alias} out of range; skipping.")
            continue
        warnings.append("Coordinates were inferred from the object name; verify before precision work.")
        return ResolvedName(name, alias, ra, dec, "coordinate_name", aliases, warnings)

    return ResolvedName(name, aliases[0] if aliases else name, None, None, "unresolved", aliases, warnings)


async def resolve_name(name: str, timeout: float = 8.0) -> ResolvedName:
    """Resolve an object name using Sesame, then NED, then coordinate syntax.

    M1: `timeout` is now a *global* wall-clock budget.  Previously it was
    applied per HTTP request, so the worst case (~8 aliases × 2 providers)
    was ~128s.  We wrap the whole coroutine in `asyncio.wait_for` and give
    each HTTP request a smaller slice so the overall latency stays bounded
    even when upstream services are slow or half-timed-out.
    """
    aliases_count = max(1, len(candidate_names(name)))
    per_request_budget = max(1.0, timeout / (aliases_count * 2))
    try:
        return await asyncio.wait_for(
            _resolve_name_impl(name, per_request_budget),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        aliases = candidate_names(name) or [name]
        return ResolvedName(
            name, aliases[0], None, None, "unresolved", aliases,
            [f"Name resolution timed out after {timeout:.1f}s."],
        )
